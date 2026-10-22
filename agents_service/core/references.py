"""Reference data an agent needs, loaded by an operator and shared by every
worker: for example a published list that changes once a month.

    agents tool oig load --file UPDATED.csv --as-of 2026-10-01

The file goes to storage (local disk or S3) and one row records which file is
current, its publication date and its fingerprint. Workers read the current
file and reload it only when the fingerprint changes.
"""

import hashlib
import time
from contextlib import contextmanager

from agents_service.core.models import ReferenceData
from agents_service.core.storage import reference_name


class References:
    """The reference data of one agent."""

    def __init__(self, db, storage, agent):
        self.db, self.storage, self.agent = db, storage, agent

    def info(self, name):
        """{'as_of', 'sha256', 'size', 'loaded_at', 'details'} of the current file, or None."""
        with self.db.session() as s:
            row = s.get(ReferenceData, (self.agent, name))
            if row is None:
                return None
            return {
                'as_of': row.as_of,
                'sha256': row.sha256,
                'size': row.size,
                'loaded_at': row.loaded_at,
                'details': row.details or {},
            }

    def put(self, name, raw, suffix, as_of, details=None):
        """Store a new current file; the previous one is deleted afterwards."""
        sha = hashlib.sha256(raw).hexdigest()
        stored = reference_name(self.agent, name, sha[:16], suffix)
        self.storage.put_reference(stored, raw)
        with self.db.session() as s:
            row = s.get(ReferenceData, (self.agent, name))
            old = row.stored if row else None
            if row is None:
                row = ReferenceData(agent=self.agent, name=name)
                s.add(row)
            row.stored, row.sha256, row.size, row.as_of = stored, sha, len(raw), as_of
            row.loaded_at, row.details = time.time(), details or {}
        if old and old != stored:
            self.storage.delete_reference(old)
        return self.info(name)

    @contextmanager
    def local_copy(self, name):
        """(path, info) of the current file. Raises LookupError when none was loaded."""
        with self.db.session() as s:
            row = s.get(ReferenceData, (self.agent, name))
            stored = row.stored if row else None
        if stored is None:
            raise LookupError(name)
        with self.storage.reference_copy(stored) as path:
            yield path, self.info(name)
