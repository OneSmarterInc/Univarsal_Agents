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
