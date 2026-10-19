import threading

import boto3
import pytest
from moto import mock_aws

from agents_service.core.config import Settings
from agents_service.core.errors import ServiceError
from agents_service.core.keys import Principal
from agents_service.core.storage import LocalStorage, S3Storage, file_name
from tests.conftest import make_settings

JOB = 'job_' + 'a' * 24


def test_local_storage_round_trip(tmp_path):
    storage = LocalStorage(tmp_path)
    name = file_name(JOB, 0, '.pdf')
    storage.put(name, b'%PDF-1')
    with storage.local_copy(name) as path:
        assert path.read_bytes() == b'%PDF-1'
    assert storage.stored_jobs(older_than=-1) == {JOB}
    storage.delete_job(JOB)
    assert storage.stored_jobs(older_than=-1) == set()


def test_storage_refuses_unexpected_names(tmp_path):
    storage = LocalStorage(tmp_path)
    for bad in ('../etc/passwd', 'job_x/0.pdf', JOB + '/0.pdf/../../x'):
        with pytest.raises(ServiceError):
            storage.put(bad, b'x')


@mock_aws
def test_s3_storage_round_trip_encrypted():
    boto3.client('s3', region_name='us-east-1').create_bucket(Bucket='agents-test')
    storage = S3Storage('agents-test', 'uploads/', 'us-east-1')
    name = file_name(JOB, 1, '.png')
    storage.put(name, b'png-bytes')
    head = storage.client.head_object(Bucket='agents-test', Key='uploads/' + name)
    assert head['ServerSideEncryption'] == 'AES256'
    with storage.local_copy(name) as path:
        assert path.read_bytes() == b'png-bytes'
    assert storage.stored_jobs(older_than=-1) == {JOB}
    storage.delete_job(JOB)
    assert storage.stored_jobs(older_than=-1) == set()
    storage.check()


@mock_aws
def test_jobs_work_with_s3(tmp_path):
    from agents_service.services import build

    boto3.client('s3', region_name='us-east-1').create_bucket(Bucket='agents-test')
    services = build(make_settings(tmp_path, storage='s3', s3_bucket='agents-test', s3_region='us-east-1'))
    services.db.upgrade()
    job = services.jobs.create(
        Principal('key_0000000000000000', 'p', 'cv'),
        'cv',
        [{'name': 'cv.pdf', 'suffix': '.pdf', 'raw': b'%PDF-x', 'sha256': 'x'}],
    )
    assert services.storage.stored_jobs(older_than=-1) == {job['id']}


def test_settings_need_a_real_pepper(tmp_path):
    with pytest.raises(ValueError):
        Settings(key_pepper='REPLACE_WITH_RANDOM_VALUE_PLEASE_32chars', _env_file=None)
    with pytest.raises(ValueError):
        Settings(key_pepper='short', _env_file=None)


def test_s3_needs_a_bucket(tmp_path):
    with pytest.raises(ValueError):
        make_settings(tmp_path, storage='s3')


def test_enabled_agents_from_text(tmp_path):
    assert make_settings(tmp_path, enabled_agents='cv, linkedin').enabled_agents == ['cv', 'linkedin']


def test_model_override_per_agent(tmp_path):
    settings = make_settings(
        tmp_path, model_overrides={'linkedin': {'provider': 'anthropic', 'name': 'claude-x', 'api_key': 'k'}}
    )
    assert settings.model_for('cv')['provider'] == 'ollama'
    linkedin = settings.model_for('linkedin')
    assert (linkedin['provider'], linkedin['name']) == ('anthropic', 'claude-x')
    with pytest.raises(ValueError):
        make_settings(tmp_path, model_overrides={'cv': {'colour': 'blue'}}).model_for('cv')


def test_parallel_claims_on_sqlite_never_share_a_job(services):
    from tests.unit.test_job_line import queue

    ids = set(queue(services, n=20))
    taken, lock = [], threading.Lock()

    def worker(n):
        while (job := services.jobs.claim(f'w{n}', ['cv'])) is not None:
            with lock:
                taken.append(job.id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(taken) == sorted(ids)


def test_env_example_loads(monkeypatch):
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / '.env.example'
    for line in example.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            monkeypatch.setenv(key, value)
    monkeypatch.setenv('AGENTS_KEY_PEPPER', 'x' * 40)
    settings = Settings(_env_file=None)
    assert settings.enabled_agents == ['cv', 'linkedin']
    assert settings.model_provider == 'ollama' and settings.model_overrides == {}
