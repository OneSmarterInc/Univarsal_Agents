"""Starting, reading, cancelling and limiting jobs through the API."""

import pytest

from agents_service.worker import runner
from tests.conftest import auth


@pytest.fixture
def fake_cv_model(monkeypatch):
    """The CV agent's model answer, without a model."""

    def ask(text, config):
        name = text.splitlines()[0]
        return {
            'name': name,
            'skills': ['Python', 'SQL'],
            'work_experience_entries': [
                {
                    'title': 'Data Analyst',
                    'organization': 'Example Health',
                    'start_date': '2021',
                    'end_date': '2024',
                    'evidence': 'Data Analyst, Example Health, 2021 - 2024',
                }
            ],
            'warnings': [],
        }

    monkeypatch.setattr('agents_service.agents.cv.agent.ask_model', ask)


def start(client, token, files, agent='cv', **kwargs):
    return client.post(
        f'/v1/{agent}/jobs', headers=auth(token) | kwargs.pop('headers', {}), files=files, **kwargs
    )


def test_cv_job_runs_end_to_end(client, services, make_key, pdf_bytes, fake_cv_model):
    token = make_key('cv')
    response = start(client, token, {'files': ('cv.pdf', pdf_bytes, 'application/pdf')})
    assert response.status_code == 202
    job = response.json()
    assert job['state'] == 'queued'
    assert job['queue']['position'] == 1
    assert response.headers['Location'] == job['links']['self']

    runner.run(services, once=True)

    done = client.get(job['links']['self'], headers=auth(token)).json()
    assert done['state'] == 'done'
    sections = {r['section'] for r in done['result']['records']}
    assert {'name', 'skills', 'work_experience_entries'} <= sections
    work = next(r for r in done['result']['records'] if r['section'] == 'work_experience_entries')
    assert work['sources'][0]['evidence'] == 'Data Analyst, Example Health, 2021 - 2024'
    assert done['result']['reader_version'] == 'cv/5'
    assert done['result']['model'] == {'provider': 'ollama', 'name': 'kormic-local'}
    assert done['result']['files'] == [{'index': 0, 'status': 'complete', 'records': 4}]
    # The uploaded file is deleted when the job ends.
    assert not list((services.settings.data_dir / 'files').glob('job_*/*'))


def test_fields_returns_only_the_sections_asked_for(client, services, make_key, pdf_bytes, fake_cv_model):
    token = make_key('cv')
    job = start(
        client, token, {'files': ('cv.pdf', pdf_bytes, 'application/pdf')}, data={'fields': 'skills'}
    ).json()
    runner.run(services, once=True)
    done = client.get(job['links']['self'], headers=auth(token)).json()
    assert {r['section'] for r in done['result']['records']} == {'skills'}


def test_unknown_field_is_refused(client, make_key, pdf_bytes):
    response = start(
        client, make_key('cv'), {'files': ('cv.pdf', pdf_bytes, 'application/pdf')}, data={'fields': 'salary'}
    )
    assert response.status_code == 422


def test_subject_ref_is_returned(client, make_key, pdf_bytes):
    job = start(
        client,
        make_key('cv'),
        {'files': ('cv.pdf', pdf_bytes, 'application/pdf')},
        data={'subject_ref': 'user-42'},
    ).json()
    assert job['subject_ref'] == 'user-42'


def test_idempotency_key_returns_the_same_job(client, make_key, pdf_bytes):
    token = make_key('cv')
    files = {'files': ('cv.pdf', pdf_bytes, 'application/pdf')}
    first = start(client, token, files, headers={'Idempotency-Key': 'upload-1'}).json()
    again = start(client, token, files, headers={'Idempotency-Key': 'upload-1'}).json()
    assert first['id'] == again['id']
    other = start(
        client,
        token,
        {'files': ('cv.pdf', pdf_bytes + b'%', 'application/pdf')},
        headers={'Idempotency-Key': 'upload-1'},
    )
    assert other.status_code == 409
    assert other.json()['error']['code'] == 'idempotency_conflict'


def test_wrong_file_types_are_refused(client, make_key, pdf_bytes, png_bytes):
    token = make_key('cv')
    assert start(client, token, {'files': ('cv.txt', b'plain text', 'text/plain')}).status_code == 415
    # A PNG renamed to .pdf is caught by its content.
    assert start(client, token, {'files': ('cv.pdf', png_bytes, 'application/pdf')}).status_code == 422
    assert start(client, token, {'files': ('cv.pdf', b'', 'application/pdf')}).status_code == 422
    assert start(client, token, {}).status_code in (400, 422)


def test_docx_with_macros_is_refused(client, make_key, docx_bytes):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as source, zipfile.ZipFile(buffer, 'w') as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr('word/vbaProject.bin', b'macro')
    response = start(
        client, make_key('cv'), {'files': ('cv.docx', buffer.getvalue(), 'application/octet-stream')}
    )
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'invalid_document'


def test_cv_takes_one_file_and_linkedin_up_to_five(client, make_key, pdf_bytes, png_bytes):
    two = [
        ('files', ('a.pdf', pdf_bytes, 'application/pdf')),
        ('files', ('b.pdf', pdf_bytes, 'application/pdf')),
    ]
    assert start(client, make_key('cv'), two).json()['error']['code'] == 'too_many_files'
    shots = [('files', (f'{i}.png', png_bytes, 'image/png')) for i in range(6)]
    linkedin = make_key('linkedin')
    assert start(client, linkedin, shots, agent='linkedin').json()['error']['code'] == 'too_many_files'
    assert start(client, linkedin, shots[:5], agent='linkedin').status_code == 202


def test_cancel_a_waiting_job(client, services, make_key, pdf_bytes):
    token = make_key('cv')
    job = start(client, token, {'files': ('cv.pdf', pdf_bytes, 'application/pdf')}).json()
    cancelled = client.delete(job['links']['self'], headers=auth(token))
    assert cancelled.status_code == 202
    assert cancelled.json()['state'] == 'cancelled'
    assert cancelled.json()['error']['code'] == 'cancelled'
    runner.run(services, once=True)  # nothing to do
    assert client.get(job['links']['self'], headers=auth(token)).json()['state'] == 'cancelled'


def test_delete_a_finished_result(client, services, make_key, pdf_bytes, fake_cv_model):
    token = make_key('cv')
    job = start(client, token, {'files': ('cv.pdf', pdf_bytes, 'application/pdf')}).json()
    runner.run(services, once=True)
    deleted = client.delete(job['links']['self'], headers=auth(token)).json()
    assert deleted['state'] == 'done'
    assert 'result' not in deleted


def test_project_waiting_limit_and_busy_answer(tmp_path, pdf_bytes):
    from fastapi.testclient import TestClient

    from agents_service.api.app import create_app
    from agents_service.core.keys import create_key
    from agents_service.services import build
    from tests.conftest import make_settings

    services = build(make_settings(tmp_path, max_waiting_per_project=2, max_waiting_total=3))
    services.db.upgrade()
    pepper = services.settings.key_pepper
    a = create_key(services.db, pepper, 'a', 'cv', 'cv')[1]
    b = create_key(services.db, pepper, 'b', 'cv', 'cv')[1]
    client = TestClient(create_app(services))
    files = {'files': ('cv.pdf', pdf_bytes, 'application/pdf')}
    assert [start(client, a, files).status_code for _ in range(2)] == [202, 202]
    full = start(client, a, files)
    assert full.status_code == 429
    assert full.json()['error']['code'] == 'project_queue_full'
    assert full.headers['Retry-After'] == '30'
    assert start(client, b, files).status_code == 202
    busy = start(client, b, files)
    assert busy.json()['error']['code'] == 'busy'


def test_request_size_limit(client, make_key):
    huge = b'%PDF-' + b'0' * (11 * 1024 * 1024)
    response = start(client, make_key('cv'), {'files': ('cv.pdf', huge, 'application/pdf')})
    assert response.status_code in (413, 422)
