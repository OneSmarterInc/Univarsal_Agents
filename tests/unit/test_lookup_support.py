"""Shared parts for lookup agents: outside calls, own settings, daily limits,
the cache, reference data, failure codes, readiness and agent commands."""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agents_service.agents import registry
from agents_service.agents.base import NotConfigured
from agents_service.api.app import create_app
from agents_service.core.cache import Cache
from agents_service.core.keys import create_key
from agents_service.core.limits import use_daily
from agents_service.core.models import Job, LookupCache
from agents_service.core.references import References
from agents_service.lookups.http import SourceError, get_json
from agents_service.services import build
from agents_service.worker import runner
from tests.conftest import auth, make_settings
from tests.unit import lookup_agent_for_tests as probe

SECRET = 'very-secret-key-value'


def server(handler):
    return httpx.MockTransport(handler)


# --- outside calls -----------------------------------------------------------------


def test_get_json_returns_the_answer_and_sends_params():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'ok': True})

    assert get_json('https://x.example/a', params={'q': 'Alex'}, transport=server(handler)) == {'ok': True}
    assert seen[0].url.params['q'] == 'Alex'


@pytest.mark.parametrize(
    'status, kind',
    [(500, 'unavailable'), (503, 'unavailable'), (401, 'unauthorized'), (403, 'unauthorized'),
     (429, 'rate_limited'), (404, 'rejected'), (301, 'rejected')],
)  # fmt: skip
def test_get_json_sorts_failures(status, kind):
    with pytest.raises(SourceError) as caught:
        get_json('https://x.example/a', transport=server(lambda r: httpx.Response(status, text='x')))
    assert caught.value.kind == kind


def test_get_json_refuses_bad_and_huge_answers():
    with pytest.raises(SourceError, match='bad_response'):
        get_json('https://x.example/a', transport=server(lambda r: httpx.Response(200, text='<html>')))
    huge = b'[' + b'1,' * 3_000_000 + b'1]'
    with pytest.raises(SourceError, match='bad_response'):
        get_json('https://x.example/a', transport=server(lambda r: httpx.Response(200, content=huge)))


def test_get_json_never_shows_the_key_when_the_source_is_down(caplog):
    def down(request):
        raise httpx.ConnectError(f'cannot reach {request.url}')  # httpx puts the address in its text

    caplog.set_level(logging.DEBUG)
    with pytest.raises(SourceError) as caught:
        get_json('https://x.example/a', params={'api_key': SECRET}, transport=server(down))
    assert caught.value.kind == 'unavailable'
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert SECRET not in str(caught.value) and SECRET not in caplog.text
    for status in (401, 500):
        with pytest.raises(SourceError) as caught:
            get_json(
                'https://x.example/a',
                params={'api_key': SECRET},
                transport=server(lambda r, s=status: httpx.Response(s)),
            )
        assert SECRET not in str(caught.value)
    assert SECRET not in caplog.text


def test_get_json_needs_https_and_params_for_query_values():
    with pytest.raises(ValueError):
        get_json('http://x.example/a')
    with pytest.raises(ValueError):
        get_json('https://x.example/a?api_key=x')


# --- the agent's own settings ------------------------------------------------------


def test_agent_settings_come_from_prefixed_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AGENTS_PROBE_API_KEY', SECRET)
    monkeypatch.setenv('AGENTS_PROBE_DAILY_LIMIT', '7')
    loaded = probe.AGENT.load_settings(make_settings(tmp_path))
    assert loaded.api_key.get_secret_value() == SECRET and loaded.daily_limit == 7


def test_agent_settings_from_a_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('AGENTS_PROBE_API_KEY', raising=False)
    (tmp_path / '.env').write_text('AGENTS_PROBE_API_KEY=from-file\n')
    assert probe.AGENT.load_settings(make_settings(tmp_path)).api_key.get_secret_value() == 'from-file'


def test_missing_agent_settings_name_the_setting_not_the_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('AGENTS_PROBE_API_KEY', raising=False)
    monkeypatch.setenv('AGENTS_PROBE_DAILY_LIMIT', 'not-a-number-secretish')
    with pytest.raises(NotConfigured) as caught:
        probe.AGENT.load_settings(make_settings(tmp_path))
    assert 'AGENTS_PROBE_API_KEY' in str(caught.value) and 'AGENTS_PROBE_DAILY_LIMIT' in str(caught.value)
    assert 'secretish' not in str(caught.value)


# --- daily limit and cache ----------------------------------------------------------


def test_daily_limit_is_shared(services):
    assert [use_daily(services.db, 'probe:search', 2) for _ in range(3)] == [True, True, False]
    assert use_daily(services.db, 'other:search', 2)


def test_cache_keeps_answers_without_showing_the_question(services):
    cache = Cache(services.db, services.settings.key_pepper, 'probe')
    cache.put({'name': 'Alex Example'}, {'hits': [1]}, 60)
    assert cache.get({'name': 'Alex Example'}) == {'hits': [1]}
    assert cache.get({'name': 'Someone Else'}) is None
    assert Cache(services.db, services.settings.key_pepper, 'other').get({'name': 'Alex Example'}) is None
    with services.db.session() as s:
        row = s.scalars(select(LookupCache)).one()
        assert 'Alex' not in row.id
        row.expires_at = 0
    assert cache.get({'name': 'Alex Example'}) is None
    services.jobs.sweep()
    with services.db.session() as s:
        assert s.scalars(select(LookupCache)).first() is None


# --- reference data -----------------------------------------------------------------


def test_reference_data_is_shared_and_replaced(services):
    refs = References(services.db, services.storage, 'probe')
    assert refs.info('list') is None
    with pytest.raises(LookupError):
        with refs.local_copy('list'):
            pass
    first = refs.put('list', b'Alex', '.txt', '2026-09-01', {'rows': 1})
    assert (first['as_of'], first['size'], first['details']) == ('2026-09-01', 4, {'rows': 1})
    second = refs.put('list', b'Alex Sam', '.txt', '2026-10-01')
    with refs.local_copy('list') as (path, info):
        assert path.read_bytes() == b'Alex Sam' and info['as_of'] == '2026-10-01'
    files = list((services.settings.data_dir / 'files' / 'ref_probe').iterdir())
    assert [f.name for f in files] == [f'list-{second["sha256"][:16]}.txt']  # the old file is gone
    services.jobs.sweep()  # the job-file cleanup never touches reference files
    assert (services.settings.data_dir / 'files' / 'ref_probe').exists()


def test_reference_names_are_checked(services):
    refs = References(services.db, services.storage, 'probe')
    for bad in ('../x', 'List', 'a/b'):
        with pytest.raises(ValueError):
            refs.put(bad, b'x', '.txt', '2026-10-01')


def test_reference_data_on_s3():
    import boto3
    from moto import mock_aws

    from agents_service.core.storage import S3Storage, reference_name

    with mock_aws():
        boto3.client('s3', region_name='us-east-1').create_bucket(Bucket='refs')
        storage = S3Storage('refs', 'uploads/', 'us-east-1')
        name = reference_name('probe', 'list', 'a' * 16, '.csv')
        storage.put_reference(name, b'rows')
        with storage.reference_copy(name) as path:
            assert path.read_bytes() == b'rows'
        assert storage.stored_jobs(older_than=-1) == set()
        storage.delete_reference(name)


# --- running a lookup job ------------------------------------------------------------


@pytest.fixture
def probe_services(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.AVAILABLE, 'probe', 'tests.unit.lookup_agent_for_tests')
    monkeypatch.setitem(probe.CALLS, 'behaviour', 'ok')
    monkeypatch.setitem(
        probe.CALLS, 'transport', server(lambda r: httpx.Response(200, json={'hits': [r.url.params['q']]}))
    )
    services = build(
        make_settings(
            tmp_path,
            enabled_agents='cv,probe',
            agent_settings={'probe': {'api_key': SECRET, 'daily_limit': 2}},
        )
    )
    services.db.upgrade()
    monkeypatch.setattr(runner.time, 'sleep', lambda s: None)
    return services


def run_one(services, name='Alex'):
    from agents_service.core.keys import Principal

    job = services.jobs.create(Principal('key_p000000000000000', 'p', 'probe'), 'probe', data={'name': name})
    runner.run(services, once=True)
    with services.db.session() as s:
        row = s.get(Job, job['id'])
        return row.state, row.error_code, row.result, row.attempts


def test_lookup_job_uses_settings_cache_quota_and_references(probe_services):
    References(probe_services.db, probe_services.storage, 'probe').put('list', b'Alex', '.txt', '2026-10-01')
    state, error, result, _ = run_one(probe_services)
    assert (state, error) == ('done', None)
    record = result['records'][0]
    assert record['details'] == {'hits': ['Alex'], 'listed': True}
    assert record['sources'][0]['retrieved_at'] == '2026-10-01'
    assert run_one(probe_services)[0] == 'done'  # from the cache: no quota used
    assert run_one(probe_services, 'Sam')[0] == 'done'  # second outside call of the day
    assert run_one(probe_services, 'Kim')[:2] == ('failed', 'daily_limit_reached')


def test_a_source_that_is_down_is_retried_once_then_fails_with_its_own_code(probe_services, monkeypatch):
    def down(request):
        raise httpx.ConnectError('down')

    monkeypatch.setitem(probe.CALLS, 'transport', server(down))
    References(probe_services.db, probe_services.storage, 'probe').put('list', b'x', '.txt', '2026-10-01')
    state, error, _, attempts = run_one(probe_services)
    assert (state, attempts) == ('queued', 1)  # back in the line
    runner.run(probe_services, once=True)
    with probe_services.db.session() as s:
        job = s.scalars(select(Job)).one()
        assert (job.state, job.error_code, job.attempts) == ('failed', 'source_unavailable', 2)


def test_missing_settings_fail_the_job_as_not_configured(probe_services):
    probe_services.settings.agent_settings['probe'] = {}
    assert run_one(probe_services)[:2] == ('failed', 'not_configured')


def test_an_unknown_failure_code_is_an_internal_error(probe_services, monkeypatch):
    monkeypatch.setitem(probe.CALLS, 'behaviour', 'bad_code')
    assert run_one(probe_services)[:2] == ('failed', 'internal_error')


def test_job_errors_tell_the_project_what_happened(probe_services):
    client = TestClient(create_app(probe_services))
    token = create_key(probe_services.db, probe_services.settings.key_pepper, 'p', 'probe', 'probe')[1]
    probe_services.settings.agent_settings['probe'] = {}
    job = client.post('/v1/probe/jobs', headers=auth(token), json={'data': {'name': 'Alex'}}).json()
    runner.run(probe_services, once=True)
    body = client.get(job['links']['self'], headers=auth(token)).json()
    assert body['error']['code'] == 'not_configured'
    assert 'not set up' in body['error']['message']


# --- readiness and commands ------------------------------------------------------------


def test_readiness_shows_each_agent_without_failing_the_service(probe_services):
    client = TestClient(create_app(probe_services))
    token = create_key(probe_services.db, probe_services.settings.key_pepper, 'p', 'probe', 'probe')[1]

    def state():
        return next(
            a['state']
            for a in client.get('/v1/agents', headers=auth(token)).json()['agents']
            if a['name'] == 'probe'
        )

    body = client.get('/health/ready').json()
    assert body['status'] == 'ready' and body['agents'] == {'cv': 'ok', 'probe': 'needs setup'}
    assert state() == 'no list loaded (agents tool probe load)'  # the reason needs a key
    probe_services.settings.agent_settings['probe'] = {}
    response = client.get('/health/ready')
    assert response.status_code == 200 and 'AGENTS_' not in response.text
    assert state() == 'not configured: AGENTS_PROBE_API_KEY'


def test_agent_command_loads_reference_data(tmp_path, monkeypatch, capsys):
    from agents_service.cli import main as cli

    monkeypatch.setitem(registry.AVAILABLE, 'probe', 'tests.unit.lookup_agent_for_tests')
    settings = make_settings(tmp_path, enabled_agents='cv,probe', agent_settings={'probe': {'api_key': 'k'}})
    services = build(settings)
    services.db.upgrade()
    monkeypatch.setattr('agents_service.services.build', lambda *a: services)
    listed = tmp_path / 'list.txt'
    listed.write_text('Alex Sam')
    assert cli.main(['tool', 'probe', 'load', '--file', str(listed), '--as-of', '2026-10-01']) == 0
    assert 'loaded 8 bytes as of 2026-10-01' in capsys.readouterr().out
    assert References(services.db, services.storage, 'probe').info('list')['as_of'] == '2026-10-01'
    assert cli.main(['tool', 'cv', 'load']) == 2  # the CV agent has no commands
    assert json.dumps({}) == '{}'


def test_two_workers_storing_the_same_answer_at_once_is_not_an_error(services, monkeypatch):
    from sqlalchemy.orm import Session

    cache = Cache(services.db, services.settings.key_pepper, 'probe')
    cache.put({'name': 'Alex'}, {'hits': [1]}, 60)
    real_get = Session.get
    monkeypatch.setattr(
        Session,
        'get',
        lambda self, model, key, **kw: None if model is LookupCache else real_get(self, model, key, **kw),
    )
    cache.put({'name': 'Alex'}, {'hits': [2]}, 60)  # the other worker's insert wins; no error
    monkeypatch.undo()
    assert cache.get({'name': 'Alex'}) == {'hits': [1]}
