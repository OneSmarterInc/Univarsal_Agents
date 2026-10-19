"""Request limits counted per key in one-minute windows."""

import time

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from agents_service.core.errors import ServiceError
from agents_service.core.models import RateWindow


def check_rate(db, key_id, bucket, limit):
    """Count one request; raise 429 (with seconds to wait) when over the limit."""
    now = time.time()
    window = int(now // 60)
    row_id = f'{key_id}:{bucket}:{window}'
    for _ in range(3):
        try:
            with db.session() as s:
                changed = s.execute(
                    update(RateWindow).where(RateWindow.id == row_id).values(count=RateWindow.count + 1)
                ).rowcount
                if not changed:
                    s.add(RateWindow(id=row_id, count=1, expires_at=now + 120))
                    s.flush()
                count = s.get(RateWindow, row_id).count
            break
        except IntegrityError:
            continue  # another request created the window first; count again
    else:
        count = limit + 1
    if count > limit:
        raise ServiceError('rate_limited', 429, True, max(1, 60 - int(now % 60)))
