"""Adding an agent needs only its folder and one registry line (docs/adding-an-agent.md)."""

import pytest
from fastapi.testclient import TestClient

from agents_service.agents import registry
from agents_service.api.app import create_app
from agents_service.core.keys import create_key
from agents_service.services import build
from agents_service.worker import runner
from tests.conftest import auth, make_settings


@pytest.fixture
def template_services(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.AVAILABLE, 'template', 'agents_service.agents._template.agent')
    services = build(make_settings(tmp_path, enabled_agents='cv,linkedin,template'))
    services.db.upgrade()
    return services


def test_template_agent_gets_addresses_keys_and_jobs(template_services, docx_bytes):
    services = template_services
    client = TestClient(create_app(services))
    token = create_key(services.db, services.settings.key_pepper, 'demo', 'template', 'tpl')[1]
    assert token.startswith('ua_tpl_')
    agents = client.get('/v1/agents', headers=auth(token)).json()['agents']
    assert 'template' in [a['name'] for a in agents]

    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as source, zipfile.ZipFile(buffer, 'w') as target:
        for item in source.infolist():
            data = source.read(item)
            if item.filename == 'word/document.xml':
                data = data.replace(b'Alex Example', b'Name: Alex Example')
            target.writestr(item, data)

    job = client.post(
        '/v1/template/jobs',
        headers=auth(token),
        files={'files': ('a.docx', buffer.getvalue(), 'application/octet-stream')},
    ).json()
    runner.run(services, once=True)
    done = client.get(job['links']['self'], headers=auth(token)).json()
    assert done['state'] == 'done'
    record = done['result']['records'][0]
    assert record['details'] == {'label': 'Name', 'value': 'Alex Example'}
    assert done['result']['model'] is None
    # A CV key cannot use the new agent, and the new agent's key cannot use the CV agent.
    cv_token = create_key(services.db, services.settings.key_pepper, 'demo', 'cv', 'cv')[1]
    assert client.get(job['links']['self'], headers=auth(cv_token)).status_code == 403


@pytest.fixture
def lookup_services(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.AVAILABLE, 'template_lookup', 'agents_service.agents._template_data.agent')
    services = build(make_settings(tmp_path, enabled_agents='cv,template_lookup'))
    services.db.upgrade()
    return services


def test_lookup_agent_takes_json_and_returns_data_records(lookup_services):
    services = lookup_services
    client = TestClient(create_app(services))
    token = create_key(services.db, services.settings.key_pepper, 'demo', 'template_lookup', 'tpldata')[1]
    described = next(
        a
        for a in client.get('/v1/agents', headers=auth(token)).json()['agents']
        if a['name'] == 'template_lookup'
    )
    assert described['input'] == 'data' and described['empty_is_answer'] is True
    assert set(described['data_schema']['properties']) == {'number', 'full_name'}

    found = client.post(
        '/v1/template_lookup/jobs', headers=auth(token), json={'data': {'number': '1234567893'}}
    )
    missing = client.post('/v1/template_lookup/jobs', headers=auth(token), json={'data': {'number': '999'}})
    assert found.status_code == missing.status_code == 202
    runner.run(services, once=True)
    runner.run(services, once=True)
    found = client.get(found.json()['links']['self'], headers=auth(token)).json()
    missing = client.get(missing.json()['links']['self'], headers=auth(token)).json()
    assert found['state'] == 'done'
    record = found['result']['records'][0]
    assert record['section'] == 'register_entry' and record['details']['name'] == 'Alex Example'
    assert set(record['sources'][0]) == {'source', 'retrieved_at', 'evidence'}
    assert missing['state'] == 'done'  # nothing found is an answer, not a failure
    assert missing['result']['records'][0]['section'] == 'not_found'


def test_lookup_agent_refuses_bad_data(lookup_services):
    services = lookup_services
    client = TestClient(create_app(services))
    token = create_key(services.db, services.settings.key_pepper, 'demo', 'template_lookup', 'tpldata')[1]
    for body in ({'data': {}}, {'data': {'number': '1', 'extra': 'x'}}, {'data': {'number': 'x' * 41}}, []):
        assert client.post('/v1/template_lookup/jobs', headers=auth(token), json=body).status_code == 422
    big = {'data': {'number': '1', 'full_name': 'x' * 2_000_000}}
    assert client.post('/v1/template_lookup/jobs', headers=auth(token), json=big).status_code == 413


def test_a_wrongly_defined_agent_is_refused_at_start():
    from agents_service.agents._template_data.agent import TemplateLookupAgent

    class Broken(TemplateLookupAgent):
        key_prefix = 'has_underscore'
        data_model = None

    with pytest.raises(ValueError, match='key_prefix.*data_model'):
        registry.check_definition(Broken())


def test_every_available_agent_is_defined_correctly():
    for name in registry.AVAILABLE:
        registry.load(name)


def test_an_agent_with_settings_cannot_take_a_service_settings_name():
    from agents_service.agents.sam.agent import SamAgent

    for name in ('model', 's3', 'key'):

        class Clash(SamAgent):
            pass

        Clash.name, Clash.key_prefix = name, 'clash'
        with pytest.raises(ValueError, match='clashes with the service settings'):
            registry.check_definition(Clash())
