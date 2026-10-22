"""A small shared cache for answers from outside sources (for example a list
search), so the same question is not paid for twice within a day.

The key is a keyed hash of the question: the stored rows do not show what
was asked. Expired rows are deleted by the sweep.
"""

import hashlib
import hmac
import json
import time

from sqlalchemy import delete

from agents_service.core.models import LookupCache


class Cache:
    def __init__(self, db, pepper, agent):
        self.db, self.pepper, self.agent = db, pepper, agent

    def _key(self, question):
        text = json.dumps([self.agent, question], sort_keys=True, ensure_ascii=False)
        return hmac.new(self.pepper.encode(), text.encode(), hashlib.sha256).hexdigest()

    def get(self, question):
        """The stored answer, or None."""
        with self.db.session() as s:
            row = s.get(LookupCache, self._key(question))
            if row is None or row.expires_at < time.time():
                return None
            return row.value

    def put(self, question, value, seconds):
        now = time.time()
        with self.db.session() as s:
            row = s.get(LookupCache, self._key(question))
            if row is None:
                s.add(
                    LookupCache(
                        id=self._key(question), agent=self.agent, value=value, expires_at=now + seconds
                    )
                )
            else:
                row.value, row.expires_at = value, now + seconds


def delete_expired(s, now):
    s.execute(delete(LookupCache).where(LookupCache.expires_at < now))
