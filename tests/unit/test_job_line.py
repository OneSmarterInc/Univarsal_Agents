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
        s.execute(update(Job).where(Job.id == job_id).values(created_at=time.time() - 3600))
    services.jobs.sweep()
    assert state(services, job_id) == 'expired'
    report = usage_report(services.db)['rows'][0]
    assert report['expired'] == 1


def test_old_results_are_deleted(services):
    (job_id,) = queue(services)
    with services.db.session() as s:
        s.execute(update(Job).where(Job.id == job_id).values(expires_at=time.time() - 1))
    services.jobs.sweep()
    with services.db.session() as s:
        assert s.get(Job, job_id) is None


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
            records=[{'id': 'r', 'section': 'name', 'value': 'A', 'details': {}, 'sources': []}]
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
        if len(calls) <= 2:  # the save and the fallback "failed" save both fail
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


def test_queue_position_follows_fair_turns(services):
    queue(services, 'busy', n=4)
    queue(services, 'other', agent='linkedin', n=3)
    (mine,) = queue(services, 'small', n=1)
    job = services.jobs.get(Principal('key_small00000000000', 'small', 'cv'), mine)
    # Only one busy-project job runs before the small project's turn; LinkedIn jobs do not count.
    assert job['queue']['position'] == 2
