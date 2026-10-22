"""Request limits counted per key in one-minute windows."""

import time

from agents_service.core.errors import ServiceError
from agents_service.core.models import RateWindow
from agents_service.core.usage import upsert


def check_rate(db, key_id, bucket, limit):
    """Count one request; raise 429 (with seconds to wait) when over the limit."""
    now = time.time()
    row_id = f'{key_id}:{bucket}:{int(now // 60)}'
    with db.session() as s:
        upsert(s, RateWindow, {'id': row_id}, {'count': 1}, fixed={'expires_at': now + 120})
        count = s.get(RateWindow, row_id).count
    if count > limit:
        raise ServiceError('rate_limited', 429, True, max(1, 60 - int(now % 60)))


def use_daily(db, name, limit):
    """Count one call against a daily limit shared by every worker (UTC days).

    True when the call is allowed. Used for outside sources with their own
    daily limit, so all servers together stay under it.
    """
    now = time.time()
    day = time.strftime('%Y-%m-%d', time.gmtime(now))
    row_id = f'day:{name}:{day}'
    with db.session() as s:
        upsert(s, RateWindow, {'id': row_id}, {'count': 1}, fixed={'expires_at': now + 2 * 86400})
        return s.get(RateWindow, row_id).count <= limit
