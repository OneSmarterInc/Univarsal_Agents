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
