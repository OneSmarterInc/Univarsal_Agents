import io
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from sqlalchemy import select

from extraction_service.db import ApiKey, Job, Upload
from extraction_service.errors import ServiceError
from extraction_service.schemas import Claim, ExtractionRequest, Observations
from extraction_service.worker import run_once


def request_for(uid):
    return ExtractionRequest(sources=[{"id": "cv1", "type": "cv", "upload_ids": [uid]}])


class SyntheticModel:
    """Test double only. No model quality is inferred from these tests."""

    def extract(self, kind, block, check):
        check()
        return Observations(
            scalars=[Claim(field="name", value="Alex Example", block_id=block["id"], quote="Alex Example")]
        )


def test_upload_job_worker_and_scoped_poll(client, service, token, pdf):
    h = {"Authorization": "Bearer " + token}
    response = client.post(
        "/v1/uploads",
        headers=h,
        data={"source_type": "cv"},
        files={"file": ("cv.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    uid = response.json()["id"]
    response = client.post(
        "/v1/extractions", headers={**h, "Idempotency-Key": "demo"}, json=request_for(uid).model_dump()
    )
    assert response.status_code == 202, response.text
    jid = response.json()["id"]
    assert run_once(service, SyntheticModel())
    result = client.get("/v1/extractions/" + jid, headers=h).json()
    assert result["state"] == "succeeded", result
    source = result["sources"]["cv1"]
    assert source["data"]["name"] == "Alex Example"
    assert source["fields"]["/name"]["confidence"] is None
    assert source["evidence"]
    foreign = service.create_key("tenant-b")
    assert (
        client.get("/v1/extractions/" + jid, headers={"Authorization": "Bearer " + foreign}).status_code
        == 404
    )


def test_unauthorized_and_scope_checks(client, service):
    assert client.get("/v1/capabilities").status_code == 401
    key = service.create_key("limited", ["capabilities:read"])
    h = {"Authorization": "Bearer " + key}
    assert client.get("/v1/capabilities", headers=h).status_code == 200
    assert client.delete("/v1/extractions/unknown", headers=h).status_code == 403


def test_foreign_upload_and_duplicate_selection_rejected(service, actor, pdf):
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    other = service.authenticate(service.create_key("tenant-b"))
    with pytest.raises(ServiceError, match="resource_not_found"):
        service.submit(other, request_for(uid), "x")
    with pytest.raises(ValueError):
        ExtractionRequest(
            sources=[{"id": "g", "type": "github", "connection_id": "c", "repository_ids": [1, 1]}]
        )


def test_concurrent_idempotency(service, actor, pdf):
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.submit(actor, request_for(uid), "same"), range(4)))
    assert len({r["id"] for r in results}) == 1
    request = request_for(uid)
    request.subject_ref = "different"
    with pytest.raises(ServiceError, match="idempotency_conflict"):
        service.submit(actor, request, "same")


def test_delete_fences_late_worker_and_erases_files(service, actor, pdf):
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    job = service.submit(actor, request_for(uid), "delete")

    class DeletingModel(SyntheticModel):
        def extract(self, kind, block, check):
            service.delete_job(actor, job["id"])
            check()

    run_once(service, DeletingModel())
    assert service.get_job(actor, job["id"])["state"] == "deleting"
    service.sweep()
    result = service.get_job(actor, job["id"])
    assert result["state"] == "deleted" and result["sources"] == {}
    assert not list(service.upload_dir.iterdir())
    assert service.submit(actor, request_for(uid), "delete")["state"] == "deleted"


def test_revoked_key_stops_processing(service, actor, pdf):
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    job = service.submit(actor, request_for(uid), "revoked")
    with service.db.transaction() as db:
        db.get(ApiKey, actor.key_id).revoked = True
    run_once(service, SyntheticModel())
    assert service.get_job(actor, job["id"])["state"] == "failed"


def test_model_failure_is_failed_not_not_found(service, actor, pdf):
    class Offline:
        def extract(self, *args):
            raise ServiceError("inference_unavailable", 503, True)

    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    job = service.submit(actor, request_for(uid), "offline")
    run_once(service, Offline())
    result = service.get_job(actor, job["id"])
    assert result["state"] == "failed"
    assert result["sources"]["cv1"]["fields"]["/name"]["status"] == "failed"


def test_retention_upload_expiry(service, actor, pdf):
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    with service.db.transaction() as db:
        db.get(Upload, uid).expires = time.time() - 1
    service.sweep()
    with service.db.sessions() as db:
        assert db.get(Upload, uid) is None
    assert not list(service.upload_dir.iterdir())


def test_ten_screenshots_limit():
    with pytest.raises(ValueError):
        ExtractionRequest(
            sources=[{"id": "li", "type": "linkedin", "upload_ids": [str(i) for i in range(11)]}]
        )


def test_body_limit_before_multipart(client, token):
    r = client.post(
        "/v1/uploads",
        headers={"Authorization": "Bearer " + token, "Content-Length": str(12 * 1024**2)},
        content=b"x",
    )
    assert r.status_code == 413


def test_invalid_body_does_not_echo_pii(client, token):
    r = client.post(
        "/v1/extractions",
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": "invalid"},
        json={"secret": "DO_NOT_ECHO_ME"},
    )
    assert r.status_code == 422 and "DO_NOT_ECHO_ME" not in r.text


def test_daily_quota_and_upload_reuse(service, actor, pdf):
    service.settings.daily_jobs = 1
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    service.submit(actor, request_for(uid), "first")
    with pytest.raises(ServiceError, match="quota_exceeded"):
        service.submit(actor, request_for(uid), "second")


def test_expired_job_hides_content_immediately(service, actor, pdf):
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    job = service.submit(actor, request_for(uid), "expires")
    with service.db.transaction() as db:
        row = db.get(Job, job["id"])
        row.state, row.result, row.expires = "succeeded", {"sources": {"secret": "value"}}, time.time() - 1
    result = service.get_job(actor, job["id"])
    assert result["state"] == "deleting" and result["sources"] == {}


def test_partial_screenshots_preserve_readable_results(service, actor, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    ids = [service.upload(actor, buffer.getvalue(), "s.png", "image/png", "linkedin")["id"] for _ in range(2)]
    job = service.submit(
        actor, ExtractionRequest(sources=[{"id": "li", "type": "linkedin", "upload_ids": ids}]), "partial"
    )

    def parse(path, kind):
        if ids[1] in str(path):
            raise ServiceError("unreadable_document")
        return {
            "blocks": [{"text": "Alex Example", "origin": "ocr", "locator": {}}],
            "warnings": [],
            "coverage": {},
        }

    monkeypatch.setattr("extraction_service.worker.parse_isolated", parse)
    run_once(service, SyntheticModel())
    result = service.get_job(actor, job["id"])
    assert result["state"] == "partial"
    assert result["sources"]["li"]["fields"]["/headline"]["status"] == "unavailable"


def test_worker_crash_retry_is_bounded(service, actor, pdf):
    uid = service.upload(actor, pdf, "cv.pdf", "application/pdf", "cv")["id"]
    job = service.submit(actor, request_for(uid), "retry")
    with service.db.transaction() as db:
        row = db.get(Job, job["id"])
        row.state, row.attempts = "processing", 2
    assert run_once(service, SyntheticModel()) is False
    with service.db.sessions() as db:
        row = db.scalar(select(Job).where(Job.id == job["id"]))
        assert row.state == "failed"


def test_unexpected_error_never_echoes_private_payload(client, monkeypatch):
    def broken(*args):
        raise RuntimeError("private-document-content")

    monkeypatch.setattr(client.app.state.service, "authenticate", broken)
    response = client.get("/v1/capabilities", headers={"Authorization": "Bearer synthetic"})
    assert response.status_code == 500
    assert "private-document-content" not in response.text


def test_github_boolean_id_rejected():
    import pytest
    from pydantic import ValidationError

    from extraction_service.schemas import Source

    with pytest.raises(ValidationError):
        Source(id="github", type="github", connection_id="connection", repository_ids=[True])
