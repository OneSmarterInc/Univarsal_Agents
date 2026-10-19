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

from agents_service.agents.base import InputFile, JobContext
from agents_service.agents.records import job_state, notice_for
from agents_service.ai.providers import AIError, RequestStats, job_scope, model_config

log = logging.getLogger(__name__)
SWEEP_SECONDS = 60
MODEL_DOWN = ('connection', 'provider')


class ModelUnavailable(Exception):
    pass


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

    def stop(self):
        self.stop_event.set()


def all_files_lost_model(files):
    """True when every file failed because the model could not be reached."""
    return bool(files) and all(
        f['status'] == 'failed' and f.get('diagnostic', {}).get('reason') in MODEL_DOWN for f in files
    )


def run_job(services, job, worker):
    agent = services.agents[job.agent]
    settings = services.settings
    renewer = Renewer(services.jobs, job.id, worker, max(5, settings.lease_seconds / 3))
    renewer.start()
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
            )
            with job_scope(check=renewer.still_held, stats=stats):
                outcome = agent.run(context)
        if all_files_lost_model(outcome.files):
            raise ModelUnavailable()
        state = job_state(outcome.records, outcome.files)
        result = {
            'records': outcome.records,
            'files': outcome.files,
            'warnings': outcome.warnings,
            'notices': [notice_for(text) for text in outcome.warnings],
            'reader_version': agent.reader_version,
            'model': {'provider': model['provider'], 'name': model['model']} if model else None,
            'timing': {'seconds': round(time.monotonic() - started, 2), 'model': stats.summary()},
        }
        services.jobs.finish(job.id, worker, state, result, None if state != 'failed' else 'no_records')
        log.info(
            'job finished: id=%s agent=%s state=%s seconds=%.1f model_requests=%s',
            job.id, agent.name, state, time.monotonic() - started, stats.requests,
        )  # fmt: skip
    except AIError as exc:
        if exc.reason == 'cancelled':
            log.info('job cancelled while running: id=%s', job.id)
            services.storage.delete_job(job.id)
        elif exc.reason == 'configuration':
            log.error('Model configuration problem (%s); job %s failed', exc.field, job.id)
            services.jobs.finish(job.id, worker, 'failed', None, 'model_unavailable')
        else:
            _model_down(services, job, worker)
    except ModelUnavailable:
        _model_down(services, job, worker)
    except Exception:  # noqa: BLE001 - one job failing must not stop the worker
        log.exception('job crashed: id=%s agent=%s', job.id, job.agent)
        services.jobs.finish(job.id, worker, 'failed', None, 'internal_error')
    finally:
        renewer.stop()


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
        run_job(services, job, worker)
        if once:
            return
