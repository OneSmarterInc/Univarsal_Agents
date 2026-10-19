"""Keys: one project + one agent; refused everywhere else."""

from agents_service.core.keys import revoke_key
from tests.conftest import auth


def start_cv(client, token, pdf_bytes, **extra):
    return client.post(
        '/v1/cv/jobs',
        headers=auth(token) | extra.pop('headers', {}),
        files={'files': ('cv.pdf', pdf_bytes, 'application/pdf')},
        **extra,
    )


def test_missing_or_malformed_key_is_refused(client, pdf_bytes):
    assert client.post('/v1/cv/jobs', files={'files': ('cv.pdf', pdf_bytes)}).status_code == 401
    assert start_cv(client, 'ua_cv_nothex.short', pdf_bytes).status_code == 401
    body = start_cv(client, 'ua_cv_0123456789abcdef.' + 'x' * 43, pdf_bytes).json()
    assert body['error']['code'] == 'invalid_credentials'
    assert 'request_id' in body


def test_key_for_another_agent_is_refused(client, make_key, pdf_bytes):
    linkedin_key = make_key('linkedin')
    response = start_cv(client, linkedin_key, pdf_bytes)
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'wrong_agent'


def test_revoked_key_is_refused(client, services, make_key, pdf_bytes):
    token = make_key('cv')
    assert start_cv(client, token, pdf_bytes).status_code == 202
    key_id = 'key_' + token.split('_')[2].split('.')[0]
    assert revoke_key(services.db, key_id)
    assert start_cv(client, token, pdf_bytes).status_code == 401


def test_projects_cannot_see_each_others_jobs(client, make_key, pdf_bytes):
    mine = make_key('cv', 'project-a')
    theirs = make_key('cv', 'project-b')
    job = start_cv(client, mine, pdf_bytes).json()
    assert client.get(job['links']['self'], headers=auth(mine)).status_code == 200
    assert client.get(job['links']['self'], headers=auth(theirs)).status_code == 404
    assert client.delete(job['links']['self'], headers=auth(theirs)).status_code == 404


def test_same_project_other_agent_cannot_read_the_job(client, make_key, pdf_bytes):
    cv_key = make_key('cv', 'project-a')
    linkedin_key = make_key('linkedin', 'project-a')
    job = start_cv(client, cv_key, pdf_bytes).json()
    # The LinkedIn key may not use the CV address at all.
    assert client.get(job['links']['self'], headers=auth(linkedin_key)).status_code == 403
    assert client.get(f'/v1/linkedin/jobs/{job["id"]}', headers=auth(linkedin_key)).status_code == 404


def test_two_keys_for_one_project_work_during_rotation(client, make_key, pdf_bytes):
    old, new = make_key('cv'), make_key('cv')
    job = start_cv(client, old, pdf_bytes).json()
    # Both keys belong to the same project and agent, so both can read it.
    assert client.get(job['links']['self'], headers=auth(new)).status_code == 200


def test_browser_calls_are_refused(client, make_key, pdf_bytes):
    response = start_cv(client, make_key('cv'), pdf_bytes, headers={'Origin': 'https://example.com'})
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'browser_calls_not_allowed'


def test_agents_list_needs_a_key_and_shows_limits(client, make_key):
    assert client.get('/v1/agents').status_code == 401
    body = client.get('/v1/agents', headers=auth(make_key('linkedin'))).json()
    names = {a['name']: a for a in body['agents']}
    assert set(names) == {'cv', 'linkedin'}
    assert names['linkedin']['files']['max_files'] == 5
    assert names['cv']['files']['types'] == ['.docx', '.pdf']
    assert names['cv']['address'] == '/v1/cv/jobs'


def test_health_is_public(client):
    assert client.get('/health/live').json() == {'status': 'alive'}
    ready = client.get('/health/ready').json()
    assert ready['checks'] == {'database': 'ok', 'storage': 'ok'}


def test_requests_per_minute_limit(tmp_path, pdf_bytes):
    from fastapi.testclient import TestClient

    from agents_service.api.app import create_app
    from agents_service.core.keys import create_key
    from agents_service.services import build
    from tests.conftest import make_settings

    services = build(make_settings(tmp_path, requests_per_minute=3, reads_per_minute=5))
    services.db.upgrade()
    token = create_key(services.db, services.settings.key_pepper, 'p', 'cv', 'cv')[1]
    client = TestClient(create_app(services))
    codes = [client.get('/v1/agents', headers=auth(token)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    response = client.get('/v1/agents', headers=auth(token))
    assert int(response.headers['Retry-After']) >= 1
    # Checking jobs has its own, higher limit, so polling never blocks new uploads.
    reads = [client.get('/v1/cv/jobs/job_' + '0' * 24, headers=auth(token)).status_code for _ in range(6)]
    assert reads == [404] * 5 + [429]


def test_error_body_and_header_carry_the_same_request_id(client):
    response = client.get('/v1/agents', headers={'Authorization': 'Bearer nope', 'x-request-id': 'spoofed'})
    assert response.status_code == 401
    assert response.json()['request_id'] == response.headers['x-request-id'] != 'spoofed'
    assert len(response.headers.get_list('x-request-id')) == 1
