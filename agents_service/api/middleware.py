"""Checks every request goes through before reaching a route."""

import logging
import secrets

from starlette.responses import JSONResponse

from agents_service.core.errors import ServiceError

log = logging.getLogger(__name__)
PUBLIC = ('/health/live', '/health/ready')


def error_response(exc, request_id):
    headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after else {}
    headers['x-request-id'] = request_id
    return JSONResponse(
        {'error': exc.payload(), 'request_id': request_id}, status_code=exc.status, headers=headers
    )


class Boundary:
    """Request id, browser refusal, body size limit, safe headers and safe errors."""

    def __init__(self, app, max_body):
        self.app, self.max_body = app, max_body

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request_id = secrets.token_hex(12)
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
                receive = await self._bounded(headers, receive)
            await self.app(scope, receive, safe_send)
        except ServiceError as exc:
            if not started:
                await error_response(exc, request_id)(scope, receive, send)
        except Exception:  # noqa: BLE001 - never let exception text reach the caller
            log.exception('Unhandled error: request_id=%s path=%s', request_id, scope['path'])
            if not started:
                await error_response(ServiceError('internal_error', 500), request_id)(scope, receive, send)

    async def _bounded(self, headers, receive):
        if b'content-length' in headers:
            try:
                length = int(headers[b'content-length'])
            except ValueError:
                raise ServiceError('invalid_request', 400) from None
            if length < 0 or length > self.max_body:
                raise ServiceError('request_too_large', 413)
        messages, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                messages.append(message)
                break
            size += len(message.get('body', b''))
            if size > self.max_body:
                raise ServiceError('request_too_large', 413)
            messages.append(message)
            if not message.get('more_body'):
                break

        async def replay():
            if messages:
                return messages.pop(0)
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        return replay
