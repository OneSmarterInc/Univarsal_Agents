import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from .db import ApiKey, Connection, Database, Job, OAuthState, Rate, Upload
from .errors import ServiceError
from .parsers import verify_upload

SCOPES = {
    "capabilities:read",
    "uploads:write",
    "extractions:write",
    "extractions:read",
    "extractions:delete",
    "github:connect",
}


def identifier(prefix):
    return prefix + "_" + secrets.token_hex(16)


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


@dataclass(frozen=True)
class Principal:
    tenant: str
    key_id: str
    scopes: frozenset


class Service:
    def __init__(self, settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.upload_dir = settings.data_dir / "uploads"
        self.upload_dir.mkdir(mode=0o700, exist_ok=True)
        self.db = Database(settings.database_url)

    def digest(self, value):
        return hmac.new(self.settings.key_pepper.encode(), value.encode(), hashlib.sha256).hexdigest()

    def create_key(self, tenant, scopes=None):
        if not tenant or len(tenant) > 100:
            raise ValueError("Tenant is required and must be <=100 characters")
        selected = SCOPES if scopes is None else set(scopes)
        if not selected <= SCOPES:
            raise ValueError("Unknown scope")
        key_id, secret = identifier("key"), secrets.token_urlsafe(32)
        token = key_id + "." + secret
        with self.db.transaction() as s:
            s.add(ApiKey(id=key_id, tenant=tenant, digest=self.digest(token), scopes=sorted(selected)))
        return token

    def authenticate(self, token):
        if not token or len(token) > 200:
            raise ServiceError("invalid_credentials", 401)
        with self.db.sessions() as s:
            row = s.get(ApiKey, token.split(".", 1)[0])
            if row is None or row.revoked or not hmac.compare_digest(row.digest, self.digest(token)):
                raise ServiceError("invalid_credentials", 401)
            return Principal(row.tenant, row.id, frozenset(row.scopes))

    def rate(self, principal, bucket="control", limit=30):
        now = time.time()
        key = f"{principal.key_id}:{bucket}:{int(now // 60)}"
        with self.db.transaction() as s:
            row = s.get(Rate, key)
            if row and row.count >= limit:
                raise ServiceError("rate_limited", 429, True, 60 - int(now % 60))
            if row:
                row.count += 1
            else:
                s.add(Rate(id=key, count=1, expires=now + 120))

    def path(self, upload):
        # Opaque server-generated ID; never accepts a supplied filename or stored absolute path.
        import re

        if not re.fullmatch(r"upl_[a-f0-9]{32}", upload.id) or upload.suffix not in {
            ".pdf",
            ".docx",
            ".png",
            ".jpg",
            ".jpeg",
        }:
            raise ServiceError("invalid_storage_reference", 500)
        path = self.upload_dir / (upload.id + upload.suffix)
        if path.is_symlink():
            raise ServiceError("invalid_storage_reference", 500)
        return path

    def upload(self, principal, raw, filename, media_type, kind):
        suffix = verify_upload(raw, filename, media_type, kind)
        now = time.time()
        record = Upload(
            id=identifier("upl"),
            tenant=principal.tenant,
            kind=kind,
            media_type=media_type,
            suffix=suffix,
            sha256=hashlib.sha256(raw).hexdigest(),
            size=len(raw),
            expires=now + 86400,
        )
        target = self.path(record)
        try:
            with self.db.transaction() as s:
                # Bound orphan uploads as well as extraction jobs.
                count = s.scalar(
                    select(func.count()).select_from(Upload).where(Upload.tenant == principal.tenant)
                )
                if count >= 1000:
                    raise ServiceError("upload_quota_exceeded", 429, True, 3600)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as file:
                    file.write(raw)
                    file.flush()
                    os.fsync(file.fileno())
                s.add(record)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {
            "id": record.id,
            "source_type": kind,
            "media_type": media_type,
            "bytes": record.size,
            "sha256": record.sha256,
            "validation_state": "accepted",
            "expires_at": iso(record.expires),
        }

    def delete_upload(self, principal, upload_id):
        with self.db.transaction() as s:
            row = s.get(Upload, upload_id)
            if not row or row.tenant != principal.tenant:
                raise ServiceError("resource_not_found", 404)
            if row.job_id:
                raise ServiceError("upload_in_use", 409)
            self.path(row).unlink(missing_ok=True)
            s.delete(row)

    def submit(self, principal, request, idem):
        if not idem or not 1 <= len(idem) <= 100:
            raise ServiceError("invalid_idempotency_key", 400)
        now = time.time()
        payload = request.model_dump()
        req_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        idem_hash = self.digest(principal.tenant + ":" + principal.key_id + ":" + idem)
        with self.db.transaction() as s:
            old = s.scalar(select(Job).where(Job.idem_digest == idem_hash))
            if old and old.created + 86400 > now:
                if old.request_hash != req_hash:
                    raise ServiceError("idempotency_conflict", 409)
                return self.serialize(old)
            if old:
                old.idem_digest = self.digest(old.id + ":expired:" + secrets.token_hex(8))
                s.flush()
            active = select(func.count()).select_from(Job).where(Job.state.in_(["queued", "processing"]))
            if s.scalar(active) >= self.settings.global_pending:
                raise ServiceError("queue_full", 429, True, 30)
            if s.scalar(active.where(Job.tenant == principal.tenant)) >= self.settings.tenant_pending:
                raise ServiceError("tenant_queue_full", 429, True, 30)
            day = int(now // 86400) * 86400
            count = s.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.tenant == principal.tenant, Job.created >= day)
            )
            if count >= self.settings.daily_jobs:
                raise ServiceError("quota_exceeded", 429, True, int(day + 86400 - now))
            upload_rows = []
            for source in request.sources:
                if source.type == "github":
                    connection = s.get(Connection, source.connection_id)
                    if not connection or connection.tenant != principal.tenant:
                        raise ServiceError("resource_not_found", 404)
                for uid in source.upload_ids:
                    upload = s.get(Upload, uid)
                    if not upload or upload.tenant != principal.tenant or upload.expires <= now:
                        raise ServiceError("resource_not_found", 404)
                    if upload.kind != source.type:
                        raise ServiceError("invalid_source")
                    if upload.job_id:
                        raise ServiceError("upload_in_use", 409)
                    upload_rows.append(upload)
            job = Job(
                id=identifier("ext"),
                tenant=principal.tenant,
                key_id=principal.key_id,
                idem_digest=idem_hash,
                request_hash=req_hash,
                payload=payload,
                state="queued",
                created=now,
                updated=now,
                expires=now + self.settings.retention_seconds,
                fence=0,
                attempts=0,
            )
            s.add(job)
            for row in upload_rows:
                row.job_id, row.expires = job.id, job.expires
            return self.serialize(job)

    def serialize(self, row):
        result = {
            "id": row.id,
            "schema_version": "1.0",
            "state": row.state,
            "created_at": iso(row.created),
            "updated_at": iso(row.updated),
            "expires_at": iso(row.expires),
            "sources": {},
            "errors": [],
            "links": {"self": "/v1/extractions/" + row.id},
        }
        if row.state not in {"deleting", "deleted", "queued", "processing"}:
            result.update(row.result or {})
        if row.state in {"queued", "processing"}:
            result["poll_after_seconds"] = 5
        return result

    def get_job(self, principal, job_id):
        with self.db.transaction() as s:
            row = s.get(Job, job_id)
            if not row or row.tenant != principal.tenant:
                raise ServiceError("resource_not_found", 404)
            if row.expires <= time.time() and row.state not in {"deleting", "deleted"}:
                self.mark_deleting(row)
            return self.serialize(row)

    @staticmethod
    def mark_deleting(row):
        if row.state not in {"deleting", "deleted"}:
            row.state, row.result, row.updated = "deleting", None, time.time()
            row.fence += 1

    def delete_job(self, principal, job_id):
        with self.db.transaction() as s:
            row = s.get(Job, job_id)
            if not row or row.tenant != principal.tenant:
                raise ServiceError("resource_not_found", 404)
            self.mark_deleting(row)
            return self.serialize(row)

    def sweep(self):
        now = time.time()
        with self.db.transaction() as s:
            for row in s.scalars(
                select(Job).where(Job.expires <= now, Job.state.notin_(["deleting", "deleted"]))
            ):
                self.mark_deleting(row)
            for row in s.scalars(select(Job).where(Job.state == "deleting")):
                for upload in s.scalars(select(Upload).where(Upload.job_id == row.id)):
                    self.path(upload).unlink(missing_ok=True)
                    s.delete(upload)
                row.payload, row.result, row.state, row.updated = {}, None, "deleted", now
            for upload in s.scalars(select(Upload).where(Upload.expires <= now)):
                self.path(upload).unlink(missing_ok=True)
                s.delete(upload)
            for row in s.scalars(select(Job).where(Job.state == "deleted", Job.updated < now - 7 * 86400)):
                s.delete(row)
            for table in (OAuthState, Rate):
                for row in s.scalars(select(table).where(table.expires <= now)):
                    s.delete(row)
            known = {u.id + u.suffix for u in s.scalars(select(Upload))}
            for file in self.upload_dir.iterdir():
                if file.name not in known and file.is_file() and file.stat().st_mtime < now - 86400:
                    file.unlink()
