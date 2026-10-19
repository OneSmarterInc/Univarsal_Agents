"""The HTTP API. Every agent's addresses are built from the agent registry:

POST   /v1/<agent>/jobs          start a job (files, or JSON data)
GET    /v1/<agent>/jobs/{id}     the job and, when finished, its result
DELETE /v1/<agent>/jobs/{id}     cancel it, or delete its result
GET    /v1/agents                the agents and their limits
GET    /health/live, /health/ready
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from agents_service.api.inputs import checked_data, checked_files, parse_options, read_form
from agents_service.api.middleware import Boundary, error_response
from agents_service.core.errors import ServiceError
from agents_service.core.keys import authenticate
from agents_service.core.limits import check_rate
from agents_service.services import build

log = logging.getLogger(__name__)
API_VERSION = '1.0'


def bearer(request):
    value = request.headers.get('authorization', '')
    if not value.startswith('Bearer '):
        raise ServiceError('invalid_credentials', 401)
    return value[7:].strip()


def create_app(services=None):
    services = services or build()
    settings = services.settings
    rules = [a.file_rule(settings) for a in services.agents.values()]
    max_body = max([r.max_files * r.max_bytes for r in rules if r] + [64 * 1024]) + 1024 * 1024

    app = FastAPI(
        title='Univarsal Agents',
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.services = services
    app.add_middleware(Boundary, max_body=max_body)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        # The middleware adds the x-request-id header; the body carries the same id.
        return error_response(exc, request.scope.get('state', {}).get('request_id', ''), add_header=False)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI's default message can repeat the request body; never send it back.
        return JSONResponse({'error': ServiceError('invalid_request').payload()}, status_code=422)

    def principal_for(request, agent_name=None, bucket='requests'):
        principal = authenticate(services.db, settings.key_pepper, bearer(request))
        if agent_name is not None and principal.agent != agent_name:
            raise ServiceError('wrong_agent', 403)
        limit = settings.reads_per_minute if bucket == 'reads' else settings.requests_per_minute
        check_rate(services.db, principal.key_id, bucket, limit)
        return principal

    @app.get('/health/live')
    def live():
        return {'status': 'alive'}

    @app.get('/health/ready')
    def ready():
        checks = {}
        try:
            with services.db.session() as s:
                s.execute(text('SELECT 1'))
            checks['database'] = 'ok'
        except Exception:  # noqa: BLE001 - readiness reports, never raises
            checks['database'] = 'unavailable'
        try:
            services.storage.check()
            checks['storage'] = 'ok'
        except Exception:  # noqa: BLE001
            checks['storage'] = 'unavailable'
        ok = all(v == 'ok' for v in checks.values())
        return JSONResponse(
            {'status': 'ready' if ok else 'not_ready', 'checks': checks}, status_code=200 if ok else 503
        )

    @app.get('/v1/agents')
    def agents(request: Request):
        principal_for(request)
        return {
            'api_version': API_VERSION,
            'agents': [agent.describe(settings) for agent in services.agents.values()],
        }

    @app.get('/v1/openapi.json')
    def openapi(request: Request):
        principal_for(request)
        return app.openapi()

    for agent in services.agents.values():
        add_agent_routes(app, services, agent, principal_for)

    return app


def add_agent_routes(app, services, agent, principal_for):
    settings = services.settings
    base = f'/v1/{agent.name}/jobs'

    def accept(principal, idempotency_key, uploads, form_fields, body):
        """Everything after reading the request; runs in a worker thread."""
        check_rate(services.db, principal.key_id, 'jobs', settings.jobs_per_minute)
        if agent.input_kind == 'files':
            files, data, given = checked_files(agent, settings, uploads), {}, form_fields
        else:
            files, data, given = [], checked_data(agent, body), body
        subject_ref = given.get('subject_ref')
        options = parse_options(agent, given.get('fields'), subject_ref)
        return services.jobs.create(principal, agent.name, files, data, options, subject_ref, idempotency_key)

    async def start_job(request: Request):
        principal = await run_in_threadpool(principal_for, request, agent.name)
        uploads, form_fields, body = [], {}, None
        if agent.input_kind == 'files':
            uploads, form_fields = await read_form(request, agent.file_rule(settings))
        else:
            try:
                body = await request.json()
            except ValueError:
                raise ServiceError('invalid_request') from None
        job = await run_in_threadpool(
            accept, principal, request.headers.get('idempotency-key'), uploads, form_fields, body
        )
        log.info('job accepted: id=%s agent=%s project=%s', job['id'], agent.name, principal.project)
        return JSONResponse(job, status_code=202, headers={'Location': job['links']['self']})

    def get_job(request: Request, job_id: str):
        return services.jobs.get(principal_for(request, agent.name, bucket='reads'), job_id)

    def cancel_job(request: Request, job_id: str):
        return JSONResponse(services.jobs.cancel(principal_for(request, agent.name), job_id), status_code=202)

    app.add_api_route(base, start_job, methods=['POST'], name=f'{agent.name}_start', status_code=202)
    app.add_api_route(base + '/{job_id}', get_job, methods=['GET'], name=f'{agent.name}_get')
    app.add_api_route(
        base + '/{job_id}', cancel_job, methods=['DELETE'], name=f'{agent.name}_cancel', status_code=202
    )
