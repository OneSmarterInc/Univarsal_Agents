"""Errors a caller may see. Messages are fixed text: never file content."""

MESSAGES = {
    'invalid_credentials': 'The API key is missing, unknown or revoked.',
    'wrong_agent': 'This API key belongs to a different agent.',
    'browser_calls_not_allowed': 'Call the agents from your backend; keys must never reach a browser.',
    'resource_not_found': 'No such job for this key.',
    'agent_not_found': 'No such agent, or it is switched off.',
    'invalid_request': 'The request is not valid.',
    'idempotency_conflict': 'This Idempotency-Key was already used with a different request.',
    'request_too_large': 'The request is too large.',
    'file_too_large': 'A file is larger than this agent accepts.',
    'too_many_files': 'Too many files for this agent.',
    'no_files': 'Send at least one file.',
    'unsupported_media_type': 'This file type is not accepted by this agent.',
    'empty_file': 'A file is empty.',
    'invalid_document': 'The document could not be read. It may be broken or protected.',
    'invalid_image': 'The image could not be read.',
    'image_pixel_limit': 'The image is too large.',
    'decompression_limit': 'The document expands beyond the safety limit.',
    'external_document_relationship': 'The document links to outside content, which is not allowed.',
    'rate_limited': 'Too many requests. Retry after the given number of seconds.',
    'busy': 'The agents are busy. Retry after the given number of seconds.',
    'project_queue_full': 'Your project has too many waiting jobs. Retry after the given number of seconds.',
    'storage_unavailable': 'File storage is unavailable. Retry shortly.',
    'database_unavailable': 'The service database is unavailable. Retry shortly.',
    'internal_error': 'Something went wrong on the service side.',
}


class ServiceError(Exception):
    def __init__(self, code, status=422, retryable=False, retry_after=None):
        self.code, self.status = code, status
        self.retryable, self.retry_after = retryable, retry_after
        super().__init__(code)

    def payload(self):
        body = {
            'code': self.code,
            'message': MESSAGES.get(self.code, self.code.replace('_', ' ')),
            'retryable': self.retryable,
        }
        if self.retry_after is not None:
            body['retry_after_seconds'] = self.retry_after
        return body
