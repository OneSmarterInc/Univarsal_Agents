"""API keys: one key = one project + one agent.

A key looks like ``ua_cv_3f9c0a1b2c3d4e5f.<secret>``. The server stores only an
HMAC hash of it, so a key is shown once when created and can never be read back.
"""

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import select

from agents_service.core.audit import record
from agents_service.core.errors import ServiceError
from agents_service.core.models import ApiKey

TOKEN = re.compile(r'^ua_([a-z0-9]{1,10})_([a-f0-9]{16})\.([A-Za-z0-9_-]{32,64})$')
PROJECT = re.compile(r'^[a-z0-9][a-z0-9_-]{0,99}$')


@dataclass(frozen=True)
class Principal:
    key_id: str
    project: str
    agent: str


def digest(pepper, token):
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def create_key(db, pepper, project, agent, key_prefix, label=''):
    """Make a key and return (key_id, token). The token is shown only now."""
    if not PROJECT.fullmatch(project or ''):
        raise ValueError('Project name: lower-case letters, digits, - and _, up to 100 characters.')
    kid = secrets.token_hex(8)
    token = f'ua_{key_prefix}_{kid}.{secrets.token_urlsafe(32)}'
    key_id = 'key_' + kid
    now = time.time()
    with db.session() as s:
        s.add(
            ApiKey(
                id=key_id,
                project=project,
                agent=agent,
                digest=digest(pepper, token),
                label=label[:200],
                created_at=now,
            )
        )
        record(s, 'key_created', project=project, agent=agent, key_id=key_id)
    return key_id, token


def authenticate(db, pepper, token):
    """The key's project and agent, or 401."""
    match = TOKEN.fullmatch(token or '')
    if not match:
        raise ServiceError('invalid_credentials', 401)
    with db.session() as s:
        row = s.get(ApiKey, 'key_' + match.group(2))
        if (
            row is None
            or row.revoked_at is not None
            or not hmac.compare_digest(row.digest, digest(pepper, token))
        ):
            raise ServiceError('invalid_credentials', 401)
        now = time.time()
        if not row.last_used_at or now - row.last_used_at > 60:
            row.last_used_at = now
        return Principal(row.id, row.project, row.agent)


def revoke_key(db, key_id):
    with db.session() as s:
        row = s.get(ApiKey, key_id)
        if row is None:
            return False
        if row.revoked_at is None:
            row.revoked_at = time.time()
            record(s, 'key_revoked', project=row.project, agent=row.agent, key_id=row.id)
        return True


def list_keys(db, project=None):
    with db.session() as s:
        query = select(ApiKey).order_by(ApiKey.project, ApiKey.agent, ApiKey.created_at)
        if project:
            query = query.where(ApiKey.project == project)
        return [
            {
                'id': row.id,
                'project': row.project,
                'agent': row.agent,
                'label': row.label,
                'created_at': row.created_at,
                'last_used_at': row.last_used_at,
                'revoked': row.revoked_at is not None,
            }
            for row in s.scalars(query)
        ]
