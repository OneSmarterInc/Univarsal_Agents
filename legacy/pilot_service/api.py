import secrets
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from .config import Settings
from .errors import ServiceError
from .github import GitHubConnections
from .inference import Ollama
from .schemas import ExtractionRequest, Strict, field_spec
from .service import Service


class ConnectionRequest(Strict):
    subject_ref: str | None = Field(default=None, max_length=128)


class Boundary:
    """Authenticate before reading bodies and enforce bounds even for chunked transfer."""

    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = secrets.token_hex(16)
        headers = dict(scope["headers"])
        public = scope["path"] in {"/health/live", "/v1/github/oauth/callback"}
        started = False
        try:
            if not public:
                token = headers.get(b"authorization", b"").decode("latin1")
                if not token.startswith("Bearer "):
                    raise ServiceError("invalid_credentials", 401)
                # DB operations are small but run off the event loop.
                from starlette.concurrency import run_in_threadpool

                principal = await run_in_threadpool(self.service.authenticate, token[7:])
                await run_in_threadpool(self.service.rate, principal)
                scope.setdefault("state", {})["principal"] = principal
            if scope["method"] in {"POST", "PUT", "PATCH"}:
                ceiling = 11 * 1024 * 1024 if scope["path"] == "/v1/uploads" else 64 * 1024
                if b"content-length" in headers:
                    try:
                        length = int(headers[b"content-length"])
                    except ValueError:
                        raise ServiceError("invalid_request", 400) from None
                    if length < 0 or length > ceiling:
                        raise ServiceError("request_too_large", 413)
                messages, size = [], 0
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    size += len(message.get("body", b""))
                    if size > ceiling:
                        raise ServiceError("request_too_large", 413)
                    messages.append(message)
                    if not message.get("more_body"):
                        break

                async def replay():
                    if messages:
                        return messages.pop(0)
                    return {"type": "http.request", "body": b"", "more_body": False}

                receive = replay

            async def safe_send(message):
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                    message["headers"] += [
                        (b"x-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                await send(message)

            await self.app(scope, receive, safe_send)
        except ServiceError as exc:
            await JSONResponse(
                {"error": exc.payload(), "request_id": request_id},
                status_code=exc.status,
                headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else {},
            )(scope, receive, send)

        except Exception:  # noqa: BLE001 - prevent exception payloads reaching server logs
            if not started:
                await JSONResponse({"error": ServiceError("internal_error", 500).payload()}, status_code=500)(
                    scope, receive, send
                )


def principal(request, scope):
    actor = request.state.principal
    if scope not in actor.scopes:
        raise ServiceError("scope_denied", 403)
    return actor


def create_app(settings=None, service=None):
    service = service or Service(settings or Settings())
    connections = GitHubConnections(service)
    app = FastAPI(title="Univarsal Agents", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.add_middleware(Boundary, service=service)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse(
            {"error": exc.payload()},
            status_code=exc.status,
            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else {},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic's default details can echo sensitive request bodies.
        return JSONResponse({"error": ServiceError("invalid_request").payload()}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse({"error": ServiceError("internal_error", 500).payload()}, status_code=500)

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/v1/capabilities")
    def capabilities(request: Request):
        principal(request, "capabilities:read")
        supported = {}
        for kind in ("cv", "linkedin"):
            scalars, lists, records = field_spec(kind)
            supported[kind] = sorted(scalars | lists | records.keys())
        supported["github"] = ["profile", "repositories"]
        return {
            "schema_version": "1.0",
            "supported_fields": supported,
            "limits": {
                "file_bytes": 10485760,
                "cv_files": 1,
                "cv_pdf_pages": 30,
                "cv_text_characters": 36000,
                "linkedin_images": 10,
                "github_repositories": 5,
                "image_pixels": 25000000,
                "jobs_per_day": service.settings.daily_jobs,
            },
            "formats": {"cv": ["pdf", "docx"], "linkedin": ["png", "jpg", "jpeg"]},
            "github_public_only": True,
            "inference": "private_ollama",
            "model_quality_validated": False,
            "projection": "top_level_fields_only",
        }

    @app.get("/v1/readiness")
    def readiness(request: Request):
        principal(request, "capabilities:read")
        if (service.settings.data_dir / "inference.blocked").exists():
            raise ServiceError("inference_requires_operator_reset", 503)
        model = Ollama(service.settings)
        try:
            return {"status": "artifact_available", **model.ready(), "synthetic_inference_checked": False}
        finally:
            model.client.close()

    @app.get("/v1/openapi.json")
    def schema(request: Request):
        principal(request, "capabilities:read")
        return app.openapi()

    @app.post("/v1/uploads", status_code=201)
    async def upload(
        request: Request,
        source_type: Annotated[Literal["cv", "linkedin"], Form()],
        file: Annotated[UploadFile, File()],
    ):
        actor = principal(request, "uploads:write")
        from starlette.concurrency import run_in_threadpool

        await run_in_threadpool(service.rate, actor, "uploads", 6)
        try:
            raw = await file.read(10485761)
            return await run_in_threadpool(
                service.upload, actor, raw, file.filename, file.content_type, source_type
            )
        finally:
            await file.close()

    @app.delete("/v1/uploads/{upload_id}", status_code=204)
    def delete_upload(request: Request, upload_id: str):
        service.delete_upload(principal(request, "uploads:write"), upload_id)
        return Response(status_code=204)

    @app.post("/v1/extractions", status_code=202)
    def submit(
        request: Request, payload: ExtractionRequest, idempotency_key: str = Header(alias="Idempotency-Key")
    ):
        actor = principal(request, "extractions:write")
        service.rate(actor, "submissions", 2)
        result = service.submit(actor, payload, idempotency_key)
        return JSONResponse(result, status_code=202, headers={"Location": result["links"]["self"]})

    @app.get("/v1/extractions/{job_id}")
    def get_job(request: Request, job_id: str):
        return service.get_job(principal(request, "extractions:read"), job_id)

    @app.delete("/v1/extractions/{job_id}", status_code=202)
    def delete_job(request: Request, job_id: str):
        return service.delete_job(principal(request, "extractions:delete"), job_id)

    @app.post("/v1/github/connections", status_code=201)
    def connect(request: Request, payload: ConnectionRequest):
        return connections.authorize(principal(request, "github:connect"), payload.subject_ref)

    @app.get("/v1/github/oauth/callback")
    def callback(code: str = Query(max_length=512), state: str = Query(max_length=256)):
        return connections.callback(code, state)

    @app.get("/v1/github/connections/{connection_id}/repositories")
    def inventory(request: Request, connection_id: str, page: int = Query(default=1, ge=1, le=100)):
        actor = principal(request, "github:connect")
        gh, expected = connections.client(actor.tenant, connection_id)
        try:
            if gh.get("/user").get("id") != expected:
                raise ServiceError("github_identity_changed", 403)
            repos = gh.inventory(page)
            return {
                "repositories": [
                    {"id": r["id"], "full_name": r["full_name"]} for r in repos if not r.get("private", True)
                ],
                "next_page": page + 1 if len(repos) == 100 and page < 100 else None,
            }
        finally:
            gh.client.close()

    @app.delete("/v1/github/connections/{connection_id}", status_code=202)
    def disconnect(request: Request, connection_id: str):
        return connections.disconnect(principal(request, "github:connect"), connection_id)

    return app
