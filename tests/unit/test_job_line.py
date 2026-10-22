"""The job line: borrowing, fair turns, lost workers, expiry and cancellation."""

import time

import pytest
from sqlalchemy import update

from agents_service.agents.base import AgentResult
from agents_service.ai.providers import AIError
from agents_service.core.keys import Principal
from agents_service.core.models import Job
from agents_service.core.usage import usage_report
from agents_service.worker import runner

SOURCE = {'file_index': 0, 'sha256': 'x', 'evidence': 'A'}


def queue(services, project='a', agent='cv', n=1):
    principal = Principal('key_' + project.ljust(16, '0')[:16], project, agent)
    return [
        services.jobs.create(
            principal, agent, [{'name': 'cv.pdf', 'suffix': '.pdf', 'raw': b'%PDF-x', 'sha256': 'x'}]
        )['id']
        for _ in range(n)
    ]


def test_two_workers_never_take_the_same_job(services):
    queue(services, n=3)
    taken = [services.jobs.claim(f'w{i}', ['cv']) for i in range(4)]
    ids = [job.id for job in taken if job]
    assert len(ids) == 3 and len(set(ids)) == 3
    assert taken[3] is None


def test_projects_take_turns(services):
    busy = queue(services, 'busy', n=3)
    small = queue(services, 'small', n=1)
    first = services.jobs.claim('w', ['cv'])
    second = services.jobs.claim('w', ['cv'])
    # The busy project queued first, but the small project does not wait behind all of it.
    assert [first.id, second.id] == [busy[0], small[0]]


def test_worker_only_takes_its_agents(services):
    queue(services, agent='linkedin')
    assert services.jobs.claim('w', ['cv']) is None
    assert services.jobs.claim('w', ['linkedin']).agent == 'linkedin'


def test_lost_worker_job_returns_once_then_fails(services):
    (job_id,) = queue(services)
    assert services.jobs.claim('dead-1', ['cv']).id == job_id
    expire_lease(services, job_id)
    services.jobs.sweep()
    assert state(services, job_id) == 'queued'
    assert services.jobs.claim('dead-2', ['cv']).id == job_id
    expire_lease(services, job_id)
    services.jobs.sweep()
    assert state(services, job_id) == 'failed'
    assert error(services, job_id) == 'worker_lost'


def test_only_the_holder_can_finish_or_renew(services):
    (job_id,) = queue(services)
    services.jobs.claim('holder', ['cv'])
    assert not services.jobs.renew(job_id, 'other')
    assert not services.jobs.finish(job_id, 'other', 'done', {'records': []})
    assert services.jobs.renew(job_id, 'holder')
    assert services.jobs.finish(job_id, 'holder', 'done', {'records': []})
    assert state(services, job_id) == 'done'


def test_waiting_too_long_expires(services):
    (job_id,) = queue(services)
    with services.db.session() as s:
        s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(created_at=time.time() - 3600, queued_at=time.time() - 3600)
        )
    services.jobs.sweep()
    assert state(services, job_id) == 'expired'
    report = usage_report(services.db)['rows'][0]
    assert report['expired'] == 1


def test_old_results_are_deleted_but_never_waiting_jobs(services):
    finished, waiting = queue(services, n=2)
    with services.db.session() as s:
        s.execute(update(Job).where(Job.id == finished).values(state='done'))
        s.execute(update(Job).values(expires_at=time.time() - 1))
    services.jobs.sweep()
    with services.db.session() as s:
        assert s.get(Job, finished) is None
        assert s.get(Job, waiting) is not None


class SlowAgent:
    name, reader_version, uses_model = 'cv', 'test/1', False

    def __init__(self, action):
        self.action = action

    def run(self, job):
        return self.action(job)


def test_cancelling_a_running_job_stops_it(services, monkeypatch):
    (job_id,) = queue(services)
    principal = Principal('key_a000000000000000', 'a', 'cv')

    def cancel_then_ask_model(job):
        services.jobs.cancel(principal, job.job_id)
        from agents_service.ai.providers import _before_request

        _before_request()  # the next model request notices the cancellation
        return AgentResult()

    services.agents['cv'] = SlowAgent(cancel_then_ask_model)
    # Check the job line at once instead of every lease/3 seconds.
    monkeypatch.setattr(runner.Renewer, 'still_held', lambda self: self.jobs.renew(self.job_id, self.worker))
    runner.run(services, once=True)
    assert state(services, job_id) == 'cancelled'


def test_unreachable_model_retries_then_fails(services, monkeypatch):
    (job_id,) = queue(services)
    monkeypatch.setattr(runner.time, 'sleep', lambda s: None)

    def model_down(job):
        raise AIError('connection')

    services.agents['cv'] = SlowAgent(model_down)
    runner.run(services, once=True)
    assert state(services, job_id) == 'queued'  # one more try
    runner.run(services, once=True)
    assert state(services, job_id) == 'failed'
    assert error(services, job_id) == 'model_unavailable'


def test_crashing_agent_fails_only_that_job(services):
    first, second = queue(services, n=2)
    calls = []

    def crash_once(job):
        calls.append(job.job_id)
        if len(calls) == 1:
            raise RuntimeError('bug')
        return AgentResult(
            records=[{'id': 'r', 'section': 'name', 'value': 'A', 'details': {}, 'sources': [SOURCE]}]
        )

    services.agents['cv'] = SlowAgent(crash_once)
    runner.run(services, once=True)
    runner.run(services, once=True)
    assert (state(services, first), error(services, first)) == ('failed', 'internal_error')
    assert state(services, second) == 'done'


def test_usage_is_counted(services):
    queue(services, 'a', n=2)
    services.agents['cv'] = SlowAgent(lambda job: AgentResult())
    runner.run(services, once=True)
    row = usage_report(services.db)['rows'][0]
    assert (row['project'], row['agent'], row['jobs'], row['files'], row['failed']) == ('a', 'cv', 2, 2, 1)
    line = services.jobs.waiting_counts()
    assert line['agents']['cv']['queued'] == 1


def expire_lease(services, job_id):
    with services.db.session() as s:
        s.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=time.time() - 1))


def state(services, job_id):
    with services.db.session() as s:
        return s.get(Job, job_id).state


def error(services, job_id):
    with services.db.session() as s:
        return s.get(Job, job_id).error_code


@pytest.mark.parametrize('position', [1])
def test_queue_position_and_estimate(services, position):
    first, second = queue(services, n=2)
    principal = Principal('key_a000000000000000', 'a', 'cv')
    job = services.jobs.get(principal, second)
    assert job['queue'] == {'position': 2, 'estimated_wait_seconds': 120}
    assert services.jobs.get(principal, first)['queue']['position'] == position


def test_worker_that_lost_its_job_does_not_delete_the_new_holders_files(services):
    (job_id,) = queue(services)
    services.jobs.claim('stalled', ['cv'])
    expire_lease(services, job_id)
    services.jobs.sweep()
    services.jobs.claim('fresh', ['cv'])
    assert services.storage.stored_jobs(older_than=-1) == {job_id}
    assert not services.jobs.finish(job_id, 'stalled', 'done', {'records': []})
    assert services.storage.stored_jobs(older_than=-1) == {job_id}  # still there for 'fresh'


def test_worker_keeps_running_when_saving_fails(services, monkeypatch):
    first, second = queue(services, n=2)
    services.agents['cv'] = SlowAgent(lambda job: AgentResult())
    real_finish = services.jobs.finish
    calls = []

    def flaky_finish(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:  # the first save fails
            from agents_service.core.errors import ServiceError

            raise ServiceError('database_unavailable', 503, True, 5)
        return real_finish(*args, **kwargs)

    monkeypatch.setattr(services.jobs, 'finish', flaky_finish)
    import threading

    stop = threading.Event()
    stop.wait = lambda timeout=None: None
    runner.run(services, once=True, stop=stop)  # must not raise
    runner.run(services, once=True, stop=stop)
    assert state(services, first) == 'running'  # returns to the line when its borrow runs out
    assert state(services, second) == 'failed'  # no records: failed, but saved
    assert calls == [first, second]  # a failed save is never followed by a "failed" save


def test_a_failed_save_keeps_a_good_result_retryable(services, monkeypatch):
    (job_id,) = queue(services)
    record = {'id': 'r', 'section': 'name', 'value': 'A', 'details': {}, 'sources': [SOURCE]}
    services.agents['cv'] = SlowAgent(lambda job: AgentResult(records=[record]))
    real_finish, calls = services.jobs.finish, []

    def save_fails_once(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 1:
            from agents_service.core.errors import ServiceError

            raise ServiceError('database_unavailable', 503, True, 5)
        return real_finish(*args, **kwargs)

    monkeypatch.setattr(services.jobs, 'finish', save_fails_once)
    runner.run(services, once=True)
    assert calls == ['done']  # never re-saved as failed
    expire_lease(services, job_id)
    services.jobs.sweep()
    assert state(services, job_id) == 'queued'  # back in the line for its second try
    runner.run(services, once=True)
    assert state(services, job_id) == 'done'


def test_a_retried_job_gets_a_fresh_wait(services, monkeypatch):
    (job_id,) = queue(services)
    with services.db.session() as s:  # it waited 28 minutes before its first try
        s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(created_at=time.time() - 1680, queued_at=time.time() - 1680)
        )
    monkeypatch.setattr(runner.time, 'sleep', lambda s: None)
    services.agents['cv'] = SlowAgent(lambda job: (_ for _ in ()).throw(AIError('connection')))
    runner.run(services, once=True)
    with services.db.session() as s:  # and ran for 3 minutes before the model went down
        s.execute(update(Job).where(Job.id == job_id).values(created_at=time.time() - 1860))
    services.jobs.sweep()
    assert state(services, job_id) == 'queued'  # not expired: its wait started again


def test_a_lost_job_gets_a_fresh_wait(services):
    (job_id,) = queue(services)
    services.jobs.claim('dead', ['cv'])
    with services.db.session() as s:
        s.execute(update(Job).where(Job.id == job_id).values(created_at=time.time() - 3600))
    expire_lease(services, job_id)
    services.jobs.sweep()
    services.jobs.sweep()
    assert state(services, job_id) == 'queued'


def test_fair_turns_count_only_the_workers_agents(services):
    (a_linkedin,) = queue(services, 'a', agent='linkedin')
    (b_done,) = queue(services, 'b')
    now = time.time()
    with services.db.session() as s:
        # Project a just had a LinkedIn job start (on another worker); project b had a CV job 100 s ago.
        s.execute(update(Job).where(Job.id == a_linkedin).values(state='running', started_at=now))
        s.execute(update(Job).where(Job.id == b_done).values(state='done', started_at=now - 100))
    (b_job,) = queue(services, 'b')
    (a_job,) = queue(services, 'a')
    # For a CV worker, project a has never had a turn, so it goes before b.
    assert services.jobs.claim('w', ['cv']).id == a_job
    assert services.jobs.claim('w', ['cv']).id == b_job


def test_input_data_is_deleted_when_a_job_ends(services):
    principal = Principal('key_a000000000000000', 'a', 'cv')
    job = services.jobs.create(principal, 'cv', data={'full_name': 'Alex', 'date_of_birth': '1990-01-01'})
    services.jobs.claim('w', ['cv'])
    services.jobs.finish(job['id'], 'w', 'done', {'records': []})
    with services.db.session() as s:
        assert 'data' not in s.get(Job, job['id']).options


def test_data_agents_can_notice_a_cancel(services, monkeypatch):
    from agents_service.agents.base import JobCancelled

    (job_id,) = queue(services)
    principal = Principal('key_a000000000000000', 'a', 'cv')
    steps = []

    def lookup(job):
        services.jobs.cancel(principal, job.job_id)
        steps.append('first call')
        job.check_cancelled()
        steps.append('second call')
        return AgentResult()

    services.agents['cv'] = SlowAgent(lookup)
    monkeypatch.setattr(runner.Renewer, 'still_held', lambda self: self.jobs.renew(self.job_id, self.worker))
    runner.run(services, once=True)
    assert steps == ['first call']
    assert state(services, job_id) == 'cancelled'
    assert JobCancelled


def test_queue_position_follows_fair_turns(services):
    queue(services, 'busy', n=4)
    queue(services, 'other', agent='linkedin', n=3)
    (mine,) = queue(services, 'small', n=1)
    job = services.jobs.get(Principal('key_small00000000000', 'small', 'cv'), mine)
    # Only one busy-project job runs before the small project's turn; LinkedIn jobs do not count.
    assert job['queue']['position'] == 2


def test_a_job_queued_by_an_older_server_still_expires(services):
    (job_id,) = queue(services)
    with services.db.session() as s:  # a 1.0 server inserts jobs without queued_at
        s.execute(update(Job).where(Job.id == job_id).values(queued_at=None, created_at=time.time() - 3600))
    services.jobs.sweep()
    assert state(services, job_id) == 'expired'


def test_results_are_kept_for_the_retention_time_after_they_finish(services):
    (job_id,) = queue(services)
    with services.db.session() as s:  # created long ago, retried, finished now
        s.execute(update(Job).where(Job.id == job_id).values(created_at=time.time() - 30 * 86400))
    services.jobs.claim('w', ['cv'])
    services.jobs.finish(job_id, 'w', 'done', {'records': []})
    with services.db.session() as s:
        job = s.get(Job, job_id)
        assert job.expires_at >= job.finished_at + services.settings.result_retention_seconds - 1
    services.jobs.sweep()
    assert state(services, job_id) == 'done'


def test_the_stored_request_fingerprint_does_not_reveal_the_input(services):
    import hashlib
    import json

    principal = Principal('key_a000000000000000', 'a', 'cv')
    data = {'number': '1234567893'}
    job = services.jobs.create(principal, 'cv', data=data)
    plain = {'agent': 'cv', 'files': [], 'data': data, 'options': {}, 'subject_ref': None}
    guess = hashlib.sha256(json.dumps(plain, sort_keys=True).encode()).hexdigest()
    with services.db.session() as s:
        assert s.get(Job, job['id']).request_digest != guess
