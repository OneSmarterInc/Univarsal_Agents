"""The job line.

* Projects take turns: the next job comes from the project served least recently.
* A worker "borrows" a job for ``lease_seconds`` and keeps renewing the borrow
  while it works. If the worker dies, the borrow runs out and the job goes back
  to the line (once; a second loss fails the job).
* Every change is a conditional update on one row, so any number of workers on
  any number of servers can share the line safely. No file locks.
* A job's input data (for example a name or date of birth for a lookup) is
  deleted when the job ends; only the result is kept.
"""

import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError

from agents_service.core.audit import record
from agents_service.core.errors import ServiceError
from agents_service.core.models import Job, RateWindow
from agents_service.core.storage import file_name
from agents_service.core.usage import add_usage

WAITING = ('queued', 'running')
FINISHED = ('done', 'partial', 'failed', 'expired', 'cancelled')
IDEMPOTENCY_SECONDS = 24 * 3600


def new_job_id():
    return 'job_' + secrets.token_hex(12)


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat() if value else None


def request_digest(pepper, agent, files, data, options, subject_ref):
    """Fingerprint of a request, to spot a reused Idempotency-Key with other content.

    Keyed with the pepper, so the stored value cannot be used to guess the input
    data (a number or a date of birth) after that data is deleted.
    """
    body = {
        'agent': agent,
        'files': [[f['name'], f['sha256']] for f in files],
        'data': data,
        'options': options,
        'subject_ref': subject_ref,
    }
    return hmac.new(pepper.encode(), json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()


class JobLine:
    def __init__(self, db, storage, settings):
        self.db, self.storage, self.settings = db, storage, settings

    # --- projects -------------------------------------------------------------

    def create(
        self, principal, agent, files=(), data=None, options=None, subject_ref=None, idempotency_key=None
    ):
        """Store the files and queue the job. Returns the job as JSON.

        files: [{'name', 'suffix', 'raw', 'sha256'}], already safety-checked.
        """
        data, options = data or {}, options or {}
        digest = request_digest(self.settings.key_pepper, agent, files, data, options, subject_ref)
        idem = None
        if idempotency_key is not None:
            if not 1 <= len(idempotency_key) <= 100:
                raise ServiceError('invalid_request', 400)
            idem = hashlib.sha256(idempotency_key.encode()).hexdigest()
            existing = self._existing(principal, idem, digest)
            if existing:
                return existing
        self._check_room(principal.project)
        now = time.time()
        job_id = new_job_id()
        stored = []
        try:
            for index, item in enumerate(files):
                name = file_name(job_id, index, item['suffix'])
                self.storage.put(name, item['raw'])
                stored.append(
                    {'index': index, 'name': item['name'], 'stored': name, 'sha256': item['sha256']}
                )
            with self.db.session() as s:
                job = Job(
                    id=job_id,
                    project=principal.project,
                    agent=agent,
                    key_id=principal.key_id,
                    idempotency_digest=idem,
                    request_digest=digest,
                    state='queued',
                    subject_ref=subject_ref,
                    options={**options, 'data': data},
                    inputs=stored,
                    attempts=0,
                    created_at=now,
                    queued_at=now,
                    expires_at=now + self.settings.result_retention_seconds,
                )
                s.add(job)
                s.flush()
                add_usage(s, now, principal.project, agent, jobs=1, files=len(stored))
                record(s, 'job_created', principal.project, agent, principal.key_id, job_id)
                return self.serialize(s, job)
        except IntegrityError:
            # The same Idempotency-Key arrived twice at the same moment.
            self.storage.delete_job(job_id)
            existing = self._existing(principal, idem, digest) if idem else None
            if existing:
                return existing
            raise
        except Exception:
            self.storage.delete_job(job_id)
            raise

    def _existing(self, principal, idem, digest):
        with self.db.session() as s:
            job = s.scalar(select(Job).where(Job.key_id == principal.key_id, Job.idempotency_digest == idem))
            if job is None:
                return None
            if job.created_at + IDEMPOTENCY_SECONDS < time.time():
                job.idempotency_digest = None  # expired: the key may be used again
                return None
            if job.request_digest != digest:
                raise ServiceError('idempotency_conflict', 409)
            return self.serialize(s, job)

    def _check_room(self, project):
        with self.db.session() as s:
            waiting = select(func.count()).select_from(Job).where(Job.state == 'queued')
            if s.scalar(waiting.where(Job.project == project)) >= self.settings.max_waiting_per_project:
                raise ServiceError('project_queue_full', 429, True, 30)
            if s.scalar(waiting) >= self.settings.max_waiting_total:
                raise ServiceError('busy', 429, True, 30)

    def get(self, principal, job_id):
        with self.db.session() as s:
            job = self._own(s, principal, job_id)
            return self.serialize(s, job)

    def cancel(self, principal, job_id):
        """Stop a waiting or running job, or delete a finished result."""
        with self.db.session() as s:
            job = self._own(s, principal, job_id)
            now = time.time()
            if job.state in WAITING:
                s.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.state.in_(WAITING))
                    .values(
                        state='cancelled',
                        finished_at=now,
                        expires_at=now + self.settings.result_retention_seconds,
                        lease_owner=None,
                        error_code='cancelled',
                    )
                )
            s.refresh(job)
            job.result = None
            job.options = without_data(job.options)
            record(s, 'job_cancelled', job.project, job.agent, principal.key_id, job.id)
            self.storage.delete_job(job.id)
            return self.serialize(s, job)

    def _own(self, s, principal, job_id):
        job = s.get(Job, job_id) if isinstance(job_id, str) and len(job_id) < 41 else None
        # Another project's job, or another agent's, looks exactly like a missing one.
        if job is None or job.project != principal.project or job.agent != principal.agent:
            raise ServiceError('resource_not_found', 404)
        return job

    # --- workers --------------------------------------------------------------

    def claim(self, worker_id, agents):
        """Borrow the next job for one of these agents, or None."""
        now = time.time()
        for _ in range(5):
            with self.db.session() as s:
                waiting = select(Job.project).where(Job.state == 'queued', Job.agent.in_(agents)).distinct()
                projects = list(s.scalars(waiting))
                if not projects:
                    return None
                # Projects take turns: the one whose last job for these agents started
                # longest ago goes first. Jobs for other agents run on other workers.
                last = dict(
                    s.execute(
                        select(Job.project, func.max(Job.started_at))
                        .where(Job.project.in_(projects), Job.agent.in_(agents))
                        .group_by(Job.project)
                    ).all()
                )
                project = min(projects, key=lambda p: (last.get(p) or 0, p))
                candidate = s.scalar(
                    select(Job.id)
                    .where(Job.state == 'queued', Job.agent.in_(agents), Job.project == project)
                    .order_by(Job.created_at)
                    .limit(1)
                )
                if candidate is None:
                    continue
                taken = s.execute(
                    update(Job)
                    .where(Job.id == candidate, Job.state == 'queued')
                    .values(
                        state='running',
                        lease_owner=worker_id,
                        lease_expires_at=now + self.settings.lease_seconds,
                        started_at=now,
                        attempts=Job.attempts + 1,
                    )
                ).rowcount
                if taken:
                    return s.get(Job, candidate)
            # Another worker took it first; look again.
        return None

    def renew(self, job_id, worker_id):
        """Extend the borrow. False when the job was cancelled or taken away."""
        with self.db.session() as s:
            return bool(
                s.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.lease_owner == worker_id, Job.state == 'running')
                    .values(lease_expires_at=time.time() + self.settings.lease_seconds)
                ).rowcount
            )

    def finish(self, job_id, worker_id, state, result=None, error_code=None):
        """Save the outcome, only if this worker still holds the job."""
        now = time.time()
        with self.db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return False
            changed = s.execute(
                update(Job)
                .where(Job.id == job_id, Job.lease_owner == worker_id, Job.state == 'running')
                .values(
                    state=state,
                    result=result,
                    error_code=error_code,
                    options=without_data(job.options),
                    finished_at=now,
                    expires_at=now + self.settings.result_retention_seconds,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            ).rowcount
            if changed:
                add_usage(
                    s,
                    now,
                    job.project,
                    job.agent,
                    **{state: 1},
                    run_seconds=now - (job.started_at or now),
                    wait_seconds=(job.started_at or now) - job.created_at,
                )
        if changed:
            # Only the worker that held the job may delete its files: a worker that lost
            # its borrow must not delete files another worker is now reading.
            self.storage.delete_job(job_id)
        return bool(changed)

    def retry_later(self, job_id, worker_id):
        """Put a job back in the line (the model was unreachable). False if out of attempts."""
        with self.db.session() as s:
            job = s.get(Job, job_id)
            if job is None or job.attempts >= self.settings.max_attempts:
                return False
            return bool(
                s.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.lease_owner == worker_id, Job.state == 'running')
                    .values(state='queued', lease_owner=None, lease_expires_at=None, queued_at=time.time())
                ).rowcount
            )

    # --- housekeeping ---------------------------------------------------------

    def sweep(self):
        """Expire old waiting jobs, return lost jobs, delete old results and files."""
        now = time.time()
        expired_ids, lost_ids = [], []
        with self.db.session() as s:
            for job in s.scalars(
                select(Job).where(
                    Job.state == 'queued',
                    # A job queued by a server still on version 1.0 has no queued_at yet.
                    func.coalesce(Job.queued_at, Job.created_at) < now - self.settings.queue_expiry_seconds,
                )
            ):
                if s.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.state == 'queued')
                    .values(
                        state='expired',
                        error_code='expired',
                        finished_at=now,
                        expires_at=now + self.settings.result_retention_seconds,
                        options=without_data(job.options),
                    )
                ).rowcount:
                    add_usage(s, now, job.project, job.agent, expired=1)
                    expired_ids.append(job.id)
            for job in s.scalars(select(Job).where(Job.state == 'running', Job.lease_expires_at < now)):
                out_of_attempts = job.attempts >= self.settings.max_attempts
                values = (
                    {
                        'state': 'failed',
                        'error_code': 'worker_lost',
                        'finished_at': now,
                        'expires_at': now + self.settings.result_retention_seconds,
                        'options': without_data(job.options),
                    }
                    if out_of_attempts
                    else {'state': 'queued', 'queued_at': now}
                )
                if (
                    s.execute(
                        update(Job)
                        .where(Job.id == job.id, Job.state == 'running', Job.lease_expires_at < now)
                        .values(lease_owner=None, lease_expires_at=None, **values)
                    ).rowcount
                    and out_of_attempts
                ):
                    add_usage(s, now, job.project, job.agent, failed=1)
                    lost_ids.append(job.id)
            # Only finished jobs are deleted: a waiting or running job is never removed under its caller.
            s.execute(delete(Job).where(Job.expires_at < now, Job.state.in_(FINISHED)))
            s.execute(delete(RateWindow).where(RateWindow.expires_at < now))
            active = set(s.scalars(select(Job.id).where(Job.state.in_(WAITING))))
        for job_id in expired_ids + lost_ids:
            self.storage.delete_job(job_id)
        # Files whose job finished or vanished (for example after a crash).
        for job_id in self.storage.stored_jobs(older_than=3600):
            if job_id not in active:
                self.storage.delete_job(job_id)
        return {'expired': len(expired_ids), 'failed_lost': len(lost_ids)}

    # --- reporting ------------------------------------------------------------

    def queue_info(self, s, job):
        """Position and wait estimate for one waiting job.

        Projects take turns, so before this job starts its own project's earlier
        jobs run, and each other project gets at most as many turns.
        """
        mine = s.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.state == 'queued',
                Job.agent == job.agent,
                Job.project == job.project,
                Job.created_at < job.created_at,
            )
        )
        others = s.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.state == 'queued', Job.agent == job.agent, Job.project != job.project)
            .group_by(Job.project)
        ).scalars()
        ahead = mine + sum(min(count, mine + 1) for count in others)
        workers = (
            s.scalar(select(func.count(func.distinct(Job.lease_owner))).where(Job.state == 'running')) or 1
        )
        average = self.average_run_seconds(s, job.agent)
        return {'position': ahead + 1, 'estimated_wait_seconds': int((ahead + 1) * average / workers)}

    def average_run_seconds(self, s, agent):
        """Mean run time of this agent's recent jobs; 60 seconds before there is any."""
        recent = s.execute(
            select(Job.started_at, Job.finished_at)
            .where(
                Job.agent == agent,
                Job.state.in_(('done', 'partial', 'failed')),
                and_(Job.started_at.is_not(None), Job.finished_at.is_not(None)),
            )
            .order_by(Job.finished_at.desc())
            .limit(20)
        ).all()
        if not recent:
            return 60.0
        return sum(f - st for st, f in recent) / len(recent)

    def serialize(self, s, job):
        body = {
            'id': job.id,
            'agent': job.agent,
            'state': job.state,
            'subject_ref': job.subject_ref,
            'created_at': iso(job.created_at),
            'started_at': iso(job.started_at),
            'finished_at': iso(job.finished_at),
            'expires_at': iso(job.expires_at),
            'links': {'self': f'/v1/{job.agent}/jobs/{job.id}'},
        }
        if job.state == 'queued':
            body['queue'] = self.queue_info(s, job)
        if job.state in WAITING:
            body['poll_after_seconds'] = 5
        if job.result is not None:
            body['result'] = filter_fields(job.result, (job.options or {}).get('fields'))
        if job.error_code:
            error = ServiceError(job.error_code, retryable=job.error_code in RETRYABLE)
            body['error'] = error.payload() | {
                'message': JOB_ERRORS.get(job.error_code, error.payload()['message'])
            }
        return body

    def waiting_counts(self):
        with self.db.session() as s:
            rows = s.execute(
                select(Job.agent, Job.state, func.count())
                .where(Job.state.in_(WAITING))
                .group_by(Job.agent, Job.state)
            ).all()
            oldest = s.scalar(select(func.min(Job.created_at)).where(Job.state == 'queued'))
        counts = {}
        for agent, state, count in rows:
            counts.setdefault(agent, {'queued': 0, 'running': 0})[state] = count
        return {'agents': counts, 'oldest_wait_seconds': int(time.time() - oldest) if oldest else 0}


RETRYABLE = {'model_unavailable', 'worker_lost', 'expired'}
JOB_ERRORS = {
    'expired': 'The job waited longer than the allowed time and was not started. Send it again.',
    'cancelled': 'The job was cancelled.',
    'worker_lost': 'The job was interrupted twice and was stopped. Send it again.',
    'model_unavailable': 'The model could not be reached. Send the job again later.',
    'no_records': 'No supported information was found in the files.',
    'internal_error': 'The job failed on the service side.',
}


def without_data(options):
    """The job's options without its input data."""
    return {k: v for k, v in (options or {}).items() if k != 'data'}


def filter_fields(result, fields):
    """Only the sections a project asked for (all of them when fields is empty)."""
    if not fields:
        return result
    wanted = set(fields)
    return {**result, 'records': [r for r in result.get('records', []) if r['section'] in wanted]}
