"""Checks every request goes through before reaching a route."""

import logging
import secrets

from starlette.responses import JSONResponse

from agents_service.core.errors import ServiceError

log = logging.getLogger(__name__)
PUBLIC = ('/health/live', '/health/ready')


def error_response(exc, request_id, add_header=True):
    headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after else {}
    if add_header:
        headers['x-request-id'] = request_id
    return JSONResponse(
        {'error': exc.payload(), 'request_id': request_id}, status_code=exc.status, headers=headers
    )


class Boundary:
    """Request id, browser refusal, body size limit, safe headers and safe errors.

    The body is not read here: it is counted while the route reads it, so a
    route can check the API key before a large upload is read at all.
    """

    def __init__(self, app, max_body, body_limits=None):
        self.app, self.max_body = app, max_body
        self.body_limits = body_limits or {}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request_id = secrets.token_hex(12)
        scope.setdefault('state', {})['request_id'] = request_id
        headers = dict(scope['headers'])
        started = False

        async def safe_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message.setdefault('headers', [])
                message['headers'] += [
                    (b'x-request-id', request_id.encode()),
                    (b'cache-control', b'no-store'),
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'),
                ]
            await send(message)

        try:
            # Browsers always send Origin on cross-site calls. Keys belong on
            # backends only, so a browser call is refused before anything else.
            if b'origin' in headers and scope['path'] not in PUBLIC:
                raise ServiceError('browser_calls_not_allowed', 403)
            if scope['method'] in ('POST', 'PUT', 'PATCH'):
                limit = self.body_limits.get(scope['path'].rstrip('/'), self.max_body)
                receive = self._counted(headers, receive, limit)
            await self.app(scope, receive, safe_send)
        except ServiceError as exc:
            if not started:
                await error_response(exc, request_id)(scope, receive, send)
        except Exception:  # noqa: BLE001 - never let exception text reach the caller
            log.exception('Unhandled error: request_id=%s path=%s', request_id, scope['path'])
            if not started:
                await error_response(ServiceError('internal_error', 500), request_id)(scope, receive, send)

    def _counted(self, headers, receive, limit):
        if b'content-length' in headers:
            try:
                length = int(headers[b'content-length'])
            except ValueError:
                raise ServiceError('invalid_request', 400) from None
            if length < 0 or length > limit:
                raise ServiceError('request_too_large', 413)
        size = 0

        async def counted():
            nonlocal size
            message = await receive()
            size += len(message.get('body', b''))
            if size > limit:
                raise ServiceError('request_too_large', 413)
            return message

        return counted
