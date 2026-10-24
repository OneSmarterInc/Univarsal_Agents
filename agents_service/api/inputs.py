"""Turning a request into checked job inputs."""

import hashlib

from pydantic import ValidationError

from agents_service.core.errors import ServiceError


def parse_options(agent, fields, subject_ref):
    """`fields` (comma-separated sections) and `subject_ref` (the project's own reference)."""
    options = {}
    if isinstance(fields, list):
        fields = ','.join(map(str, fields))
    if fields:
        wanted = [f.strip() for f in str(fields).split(',') if f.strip()]
        if len(wanted) > 50 or any(f not in agent.sections for f in wanted):
            raise ServiceError('invalid_request')
        options['fields'] = wanted
    if subject_ref is not None and (not isinstance(subject_ref, str) or not 1 <= len(subject_ref) <= 128):
        raise ServiceError('invalid_request')
    return options


async def read_form(request, rule):
    """The uploaded files [(name, media type, bytes)] and text fields of a multipart request."""
    uploads, fields = [], {}
    form = await request.form(max_files=rule.max_files + 1, max_fields=20)
    try:
        for item in form.getlist('files') + form.getlist('file'):
            if hasattr(item, 'read'):
                raw = await item.read(rule.max_bytes + 1)
                uploads.append((item.filename or '', item.content_type or '', raw))
        for name in ('fields', 'subject_ref'):
            value = form.get(name)
            if isinstance(value, str):
                fields[name] = value
    finally:
        await form.close()
    return uploads, fields


def checked_files(agent, settings, uploads):
    """Safety-check every upload against the agent's file rule."""
    rule = agent.file_rule(settings)
    if not uploads or len(uploads) < rule.min_files:
        raise ServiceError('no_files')
    if len(uploads) > rule.max_files:
        raise ServiceError('too_many_files')
    files = []
    for name, media_type, raw in uploads:
        suffix = agent.check_file(raw, name, media_type, settings)
        files.append(
            {'name': name[-200:], 'suffix': suffix, 'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest()}
        )
    return files


def checked_data(agent, body):
    """The JSON `data` of a data agent, validated by its data_model."""
    if not isinstance(body, dict) or agent.data_model is None:
        raise ServiceError('invalid_request')
    try:
        return agent.data_model.model_validate(body.get('data', {})).model_dump(mode='json')
    except ValidationError:
        raise ServiceError('invalid_request') from None
