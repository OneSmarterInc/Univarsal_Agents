"""Where uploaded files wait for their job: the local disk or Amazon S3.

Only this module touches stored files. Files are named ``<job id>/<index><ending>``
and are deleted when their job ends. Reference files of agents (for example a
published list) are named ``ref_<agent>/<name>-<fingerprint><ending>``; the
cleanup of old job files never touches them.
"""

import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from agents_service.core.errors import ServiceError

NAME = re.compile(r'^job_[a-f0-9]{24}/\d{1,2}\.[a-z]{2,5}$')
JOB_ID = re.compile(r'^job_[a-f0-9]{24}$')
REFERENCE = re.compile(r'^ref_[a-z][a-z0-9_]{0,39}/[a-z][a-z0-9_]{0,39}-[a-f0-9]{16}\.[a-z]{2,5}$')


def file_name(job_id, index, suffix):
    name = f'{job_id}/{index}{suffix}'
    if not NAME.fullmatch(name):
        raise ServiceError('internal_error', 500)
    return name


def reference_name(agent, name, fingerprint, suffix):
    stored = f'ref_{agent}/{name}-{fingerprint}{suffix}'
    if not REFERENCE.fullmatch(stored):
        raise ValueError('Reference names: lower case letters, digits and _; endings like .csv')
    return stored


def _checked_reference(name):
    if not REFERENCE.fullmatch(name):
        raise ServiceError('internal_error', 500)
    return name


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name):
        if not NAME.fullmatch(name):
            raise ServiceError('internal_error', 500)
        return self.root / name

    def put(self, name, data):
        path = self._path(name)
        path.parent.mkdir(exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(data)

    @contextmanager
    def local_copy(self, name):
        yield self._path(name)

    def delete_job(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ServiceError('internal_error', 500)
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    def stored_jobs(self, older_than):
        """Job ids that have files older than the given age in seconds."""
        limit = time.time() - older_than
        return {
            p.name
            for p in self.root.iterdir()
            if JOB_ID.fullmatch(p.name) and p.is_dir() and p.stat().st_mtime < limit
        }

    def put_reference(self, name, data):
        path = self.root / _checked_reference(name)
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.part')
        temporary.write_bytes(data)
        temporary.replace(path)

    @contextmanager
    def reference_copy(self, name):
        yield self.root / _checked_reference(name)

    def delete_reference(self, name):
        (self.root / _checked_reference(name)).unlink(missing_ok=True)

    def check(self):
        probe = self.root / '.probe'
        probe.write_bytes(b'ok')
        probe.unlink()


class S3Storage:
    """Files in one bucket under a prefix, encrypted at rest (SSE-S3)."""

    def __init__(self, bucket, prefix='uploads/', region='', endpoint_url=''):
        import boto3

        self.bucket, self.prefix = bucket, prefix
        self.client = boto3.client('s3', region_name=region or None, endpoint_url=endpoint_url or None)

    def _key(self, name):
        if not NAME.fullmatch(name):
            raise ServiceError('internal_error', 500)
        return self.prefix + name

    def put(self, name, data):
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=self._key(name), Body=data, ServerSideEncryption='AES256'
            )
        except Exception as exc:  # noqa: BLE001 - any S3 failure is a retry-able storage error
            raise ServiceError('storage_unavailable', 503, True, 10) from exc

    @contextmanager
    def local_copy(self, name):
        with tempfile.TemporaryDirectory(prefix='agents-') as directory:
            path = Path(directory) / name.split('/')[-1]
            self.client.download_file(self.bucket, self._key(name), str(path))
            yield path

    def delete_job(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ServiceError('internal_error', 500)
        listing = self.client.list_objects_v2(Bucket=self.bucket, Prefix=f'{self.prefix}{job_id}/')
        keys = [{'Key': item['Key']} for item in listing.get('Contents', [])]
        if keys:
            self.client.delete_objects(Bucket=self.bucket, Delete={'Objects': keys})

    def stored_jobs(self, older_than):
        limit = time.time() - older_than
        found = set()
        paginator = self.client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get('Contents', []):
                job_id = item['Key'][len(self.prefix) :].split('/')[0]
                # Only this service's own job folders; never anything else in the bucket.
                if JOB_ID.fullmatch(job_id) and item['LastModified'].timestamp() < limit:
                    found.add(job_id)
        return found

    def put_reference(self, name, data):
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self.prefix + _checked_reference(name),
                Body=data,
                ServerSideEncryption='AES256',
            )
        except Exception as exc:  # noqa: BLE001
            raise ServiceError('storage_unavailable', 503, True, 10) from exc

    @contextmanager
    def reference_copy(self, name):
        with tempfile.TemporaryDirectory(prefix='agents-ref-') as directory:
            path = Path(directory) / name.split('/')[-1]
            self.client.download_file(self.bucket, self.prefix + _checked_reference(name), str(path))
            yield path

    def delete_reference(self, name):
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + _checked_reference(name))

    def check(self):
        self.client.head_bucket(Bucket=self.bucket)


def make_storage(settings):
    if settings.storage == 's3':
        return S3Storage(settings.s3_bucket, settings.s3_prefix, settings.s3_region, settings.s3_endpoint_url)
    return LocalStorage(Path(settings.data_dir) / 'files')
