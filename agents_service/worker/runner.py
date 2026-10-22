"""The worker: borrow a job, run its agent, save the result, repeat.

Start as many workers as you like, on any number of machines, all pointing at
the same database and storage. ``--agents cv`` limits a worker to some agents.
"""

import logging
import os
import socket
import threading
import time
from contextlib import ExitStack

from agents_service.agents.base import InputFile, JobCancelled, JobContext
from agents_service.agents.records import check_records, job_state, notice_for
from agents_service.ai.providers import AIError, RequestStats, job_scope, model_config

log = logging.getLogger(__name__)
SWEEP_SECONDS = 60
# How often a running job checks the line: renews its borrow and notices a cancel.
RENEW_SECONDS = 10
MODEL_DOWN = ('connection', 'provider')


def worker_id():
    return f'{socket.gethostname()[:40]}:{os.getpid()}:{int(time.time())}'


class Renewer(threading.Thread):
    """Keeps the borrow alive while the agent works; notices cancellation."""

    def __init__(self, jobs, job_id, worker, every):
        super().__init__(daemon=True)
        self.jobs, self.job_id, self.worker, self.every = jobs, job_id, worker, every
        self.stop_event = threading.Event()
        self.held = True

    def run(self):
        while not self.stop_event.wait(self.every):
            try:
                self.held = self.jobs.renew(self.job_id, self.worker)
            except Exception:  # noqa: BLE001 - a database blip is retried next time
                log.warning('Could not renew job %s; will retry', self.job_id)
                continue
            if not self.held:
                return

    def still_held(self):
        """Checked before every model request: False once the job was cancelled."""
        return self.held

    def cancelled(self):
        return not self.still_held()

    def stop(self):
        self.stop_event.set()


def all_files_lost_model(files):
    """True when every file failed because the model could not be reached."""
    return bool(files) and all(
        f['status'] == 'failed' and f.get('diagnostic', {}).get('reason') in MODEL_DOWN for f in files
    )


def run_job(services, job, worker):
    """Run one job, then save its outcome.

    Running and saving are kept apart: an agent problem saves a failed job,
    while a problem saving (the database is down) raises to the loop, and the
    job returns to the line when its borrow runs out.
    """
    renewer = Renewer(services.jobs, job.id, worker, RENEW_SECONDS)
    renewer.start()
    try:
        outcome = _run_agent(services, job, renewer)
    finally:
        renewer.stop()
    if outcome is None:
        return
    kind, value = outcome
    if kind == 'model_down':
        _model_down(services, job, worker)
    elif kind == 'failed':
        services.jobs.finish(job.id, worker, 'failed', None, value)
    else:
        state, result = value
        services.jobs.finish(job.id, worker, state, result, 'no_records' if state == 'failed' else None)
        log.info(
            'job finished: id=%s agent=%s state=%s seconds=%.1f model_requests=%s',
            job.id, job.agent, state, result['timing']['seconds'], result['timing']['model']['requests'],
        )  # fmt: skip


def _run_agent(services, job, renewer):
    """('done', (state, result)), ('failed', error code), ('model_down', None), or None when cancelled."""
    agent = services.agents[job.agent]
    settings = services.settings
    stats = RequestStats()
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            inputs = [
                InputFile(
                    index=item['index'],
                    name=item['name'],
                    path=stack.enter_context(services.storage.local_copy(item['stored'])),
                    sha256=item['sha256'],
                )
                for item in job.inputs
            ]
            model = model_config(settings, agent.name) if agent.uses_model else None
            context = JobContext(
                job_id=job.id,
                project=job.project,
                files=inputs,
                data=(job.options or {}).get('data', {}),
                options=job.options or {},
                settings=settings,
                model=model,
                cancelled=renewer.cancelled,
            )
            with job_scope(check=renewer.still_held, stats=stats):
                outcome = agent.run(context)
                context.check_cancelled()
    except JobCancelled:
        log.info('job stopped: id=%s (cancelled or taken over)', job.id)
        return None
    except AIError as exc:
        if exc.reason == 'cancelled':
            # Cancelled by the project (cancel() deletes the files), or the borrow was
            # lost to another worker, which must keep its files.
            log.info('job stopped: id=%s (cancelled or taken over)', job.id)
            return None
        if exc.reason == 'configuration':
            log.error('Model configuration problem (%s); job %s failed', exc.field, job.id)
            return ('failed', 'model_unavailable')
        return ('model_down', None)
    except Exception:  # noqa: BLE001 - one job failing must not stop the worker
        log.exception('job crashed: id=%s agent=%s', job.id, job.agent)
        return ('failed', 'internal_error')
    if all_files_lost_model(outcome.files):
        return ('model_down', None)
    try:
        check_records(
            outcome.records, getattr(agent, 'sections', None) or {r['section'] for r in outcome.records}
        )
    except (ValueError, KeyError, TypeError):
        log.error('Agent %s returned records in the wrong shape; job %s failed', job.agent, job.id)
        return ('failed', 'internal_error')
    state = job_state(
        outcome.records, outcome.files, empty_is_answer=getattr(agent, 'empty_is_answer', False)
    )
    result = {
        'records': outcome.records,
        'files': outcome.files,
        'warnings': outcome.warnings,
        'notices': [notice_for(text) for text in outcome.warnings],
        'reader_version': agent.reader_version,
        'model': {'provider': model['provider'], 'name': model['model']} if model else None,
        'timing': {'seconds': round(time.monotonic() - started, 2), 'model': stats.summary()},
    }
    return ('done', (state, result))


def _model_down(services, job, worker):
    if services.jobs.retry_later(job.id, worker):
        log.warning('Model unreachable; job %s goes back to the line', job.id)
        time.sleep(10)
    else:
        services.jobs.finish(job.id, worker, 'failed', None, 'model_unavailable')


def run(services, agents=None, once=False, stop=None):
    """The worker loop. `stop` is an Event that ends the loop after the current job."""
    names = list(agents or services.agents)
    unknown = [n for n in names if n not in services.agents]
    if unknown:
        raise SystemExit(f'Not switched on: {", ".join(unknown)} (see AGENTS_ENABLED_AGENTS)')
    worker = worker_id()
    stop = stop or threading.Event()
    last_sweep = 0.0
    log.info('worker started: id=%s agents=%s', worker, ','.join(names))
    while not stop.is_set():
        if time.monotonic() - last_sweep > SWEEP_SECONDS:
            try:
                services.jobs.sweep()
            except Exception:  # noqa: BLE001 - housekeeping retries next minute
                log.warning('Sweep failed; will retry')
            last_sweep = time.monotonic()
        try:
            job = services.jobs.claim(worker, names)
        except Exception:  # noqa: BLE001 - database blip: wait and retry
            log.warning('Could not reach the job line; retrying')
            job = None
            time.sleep(5)
        if job is None:
            if once:
                return
            stop.wait(services.settings.poll_seconds)
            continue
        try:
            run_job(services, job, worker)
        except Exception:  # noqa: BLE001 - e.g. the database was down while saving
            # The job keeps its borrow until it runs out, then returns to the line.
            log.exception('Could not save job %s; it will return to the line', job.id)
            stop.wait(5)
        if once:
            return
