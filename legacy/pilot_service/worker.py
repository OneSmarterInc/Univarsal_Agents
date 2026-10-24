import fcntl
import json
import time

from sqlalchemy import select

from .db import ApiKey, Connection, Job, Upload
from .errors import ServiceError
from .extractors import chunks, extract_blocks, metadata
from .github import GitHubConnections, analyze_repositories
from .inference import Ollama
from .parsers import parse_isolated
from .schemas import field_spec


def failure(kind, error, requested=None):
    scalar, lists, records = field_spec(kind)
    keys = (
        requested
        if requested is not None
        else (["profile", "repositories"] if kind == "github" else sorted(scalar | lists | records.keys()))
    )
    status = (
        "unreadable" if error.code in {"unreadable_document", "ocr_failed", "invalid_image"} else "failed"
    )
    return {
        "type": kind,
        "state": "failed",
        "data": {k: [] if k in lists | records.keys() else None for k in keys},
        "fields": {"/" + k: metadata(status, reason=error.code) for k in keys},
        "evidence": {},
        "inputs": [],
        "coverage": {"inputs_examined": 0},
        "warnings": [],
        "errors": [error.payload()],
    }


def document_source(service, source, tenant, model, check, requested=None):
    blocks, errors, notices, coverage = [], [], [], []
    read_inputs = 0
    for uid in source["upload_ids"]:
        check()
        with service.db.sessions() as db:
            upload = db.get(Upload, uid)
            if not upload or upload.tenant != tenant:
                raise ServiceError("resource_not_found", 404)
            path = service.path(upload)
        try:
            parsed = parse_isolated(path, source["type"])
            read_inputs += 1
            coverage.append({"input_id": uid, **parsed["coverage"]})
            notices.extend({"code": n, "input_id": uid} for n in parsed["warnings"])
            for block in parsed["blocks"]:
                for offset, part in chunks(block["text"]):
                    blocks.append(
                        {
                            "id": f"{uid}_{len(blocks)}",
                            "input_id": uid,
                            "text": part,
                            "origin": block["origin"],
                            "locator": {**block["locator"], "char_start": offset},
                        }
                    )
        except ServiceError as exc:
            errors.append({**exc.payload(), "input_id": uid})
    check()
    if blocks:
        result = extract_blocks(source["type"], blocks, model, check, requested, notices)
        if errors:
            if result["state"] == "succeeded":
                result["state"] = "partial"
            for field in result["fields"].values():
                if field["status"] == "not_found":
                    field.update(status="unavailable", reason_code="input_coverage_incomplete")
        result["errors"].extend(errors)
    else:
        result = failure(source["type"], ServiceError("unreadable_document"), requested)
        result["errors"] = errors or result["errors"]
    result["inputs"] = source["upload_ids"]
    result["coverage"].update(
        inputs_received=len(source["upload_ids"]),
        inputs_examined=read_inputs,
        inputs_failed=len(source["upload_ids"]) - read_inputs,
        documents=coverage,
    )
    return result


class UnavailableModel:
    def __init__(self, error):
        self.error = error

    def extract(self, *args, **kwargs):
        raise self.error


def run_once(service, model=None):
    """One-host pilot: shared filesystem flock serializes all model work across workers."""
    with (service.settings.data_dir / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        service.sweep()
        with service.db.transaction() as db:
            # Any processing row is abandoned: a live worker must own this same host lock.
            for old in db.scalars(select(Job).where(Job.state == "processing")):
                old.fence += 1
                old.state = "queued" if old.attempts < 2 else "failed"
                old.updated = time.time()
                if old.state == "failed":
                    old.result = {"sources": {}, "errors": [ServiceError("worker_interrupted").payload()]}
            row = db.scalar(select(Job).where(Job.state == "queued").order_by(Job.created).limit(1))
            if not row:
                return False
            row.state, row.updated = "processing", time.time()
            row.attempts += 1
            row.fence += 1
            job_id, fence, tenant, payload, key_id = row.id, row.fence, row.tenant, row.payload, row.key_id
            created = row.created
        deadline = time.monotonic() + service.settings.job_timeout

        def check():
            if time.monotonic() > deadline:
                raise ServiceError("job_deadline_exceeded")
            with service.db.sessions() as db:
                current = db.get(Job, job_id)
                key = db.get(ApiKey, key_id)
                if not current or current.state != "processing" or current.fence != fence:
                    raise ServiceError("job_cancelled")
                if not key or key.revoked:
                    raise ServiceError("invalid_credentials", 401)
                for source in payload["sources"]:
                    if source["type"] == "github":
                        connection = db.get(Connection, source["connection_id"])
                        if not connection or connection.tenant != tenant:
                            raise ServiceError("job_cancelled")

        results, errors, owned_model = {}, [], model is None
        owned_client = None
        if model is None:
            model = Ollama(service.settings)
            owned_client = model.client
            try:
                if (service.settings.data_dir / "inference.blocked").exists():
                    raise ServiceError("inference_requires_operator_reset", 503)
                model.ready()
            except ServiceError as exc:
                model.client.close()
                model = UnavailableModel(exc)
        try:
            if time.time() - created > 86400:
                raise ServiceError("queue_deadline_exceeded")
            for source in payload["sources"]:
                check()
                requested = payload.get("requested_fields", {}).get(source["id"])
                try:
                    if source["type"] == "github":
                        gh, expected_id = GitHubConnections(service).client(tenant, source["connection_id"])
                        try:
                            result = analyze_repositories(gh, expected_id, source, model, check)
                        finally:
                            gh.client.close()
                        if requested is not None:
                            result["data"] = {k: result["data"].get(k) for k in requested}
                            result["fields"] = {
                                k: v for k, v in result["fields"].items() if k.split("/")[1] in requested
                            }
                            for k in requested:
                                result["fields"].setdefault(
                                    "/" + k, metadata("unsupported", reason="field_not_supported")
                                )
                            used = {e for v in result["fields"].values() for e in v["evidence_ids"]}
                            result["evidence"] = {k: v for k, v in result["evidence"].items() if k in used}
                    else:
                        result = document_source(service, source, tenant, model, check, requested)
                    results[source["id"]] = result
                except ServiceError as exc:
                    if exc.code in {"job_cancelled", "job_deadline_exceeded"}:
                        raise
                    results[source["id"]] = failure(source["type"], exc, requested)
                if any(e["code"] == "model_timeout" for e in results[source["id"]]["errors"]):
                    (service.settings.data_dir / "inference.blocked").write_text("model_timeout\n")
                    model = UnavailableModel(ServiceError("inference_requires_operator_reset", 503))
        except ServiceError as exc:
            errors.append(exc.payload())
        except Exception:  # noqa: BLE001 - operational boundary emits no source data
            errors.append(ServiceError("internal_processing_error", 500).payload())
        finally:
            if owned_model and owned_client is not None:
                owned_client.close()
        states = [r["state"] for r in results.values()]
        state = (
            "succeeded"
            if states and all(x == "succeeded" for x in states) and not errors
            else ("partial" if any(x in {"succeeded", "partial"} for x in states) else "failed")
        )
        with service.db.transaction() as db:
            row = db.get(Job, job_id)
            if row and row.state == "processing" and row.fence == fence:
                row.state, row.updated = state, time.time()
                row.result = {
                    "sources": results,
                    "errors": errors,
                    "model": {
                        "alias": service.settings.ollama_model,
                        "manifest_digest": service.settings.ollama_digest,
                        "artifact_sha256": service.settings.model_artifact_sha256,
                        "schema_version": "1.0",
                    },
                }
                for upload in db.scalars(select(Upload).where(Upload.job_id == job_id)):
                    upload.expires = min(upload.expires, time.time() + 86400)
        return True


def main():
    from .config import Settings
    from .service import Service

    service = Service(Settings())
    while True:
        try:
            processed = run_once(service)
        except Exception:  # noqa: BLE001 - operational boundary emits no source data
            # Safe operational event only, no exception text or source payload.
            print(json.dumps({"event": "worker_iteration_failed"}), flush=True)
            processed = False
        if not processed:
            time.sleep(2)


if __name__ == "__main__":
    main()
