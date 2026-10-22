"""Database tables.

Change a table only together with a new migration in agents_service/migrations.
"""

from sqlalchemy import JSON, Column, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class ApiKey(Base):
    """One key belongs to one project and one agent. Only a hash is stored."""

    __tablename__ = 'api_keys'
    id = Column(String(40), primary_key=True)
    project = Column(String(100), nullable=False, index=True)
    agent = Column(String(40), nullable=False)
    digest = Column(String(64), nullable=False, unique=True)
    label = Column(String(200), nullable=False, default='')
    created_at = Column(Float, nullable=False)
    revoked_at = Column(Float)
    last_used_at = Column(Float)


class Job(Base):
    """One extraction request and, once finished, its result."""

    __tablename__ = 'jobs'
    id = Column(String(40), primary_key=True)
    project = Column(String(100), nullable=False)
    agent = Column(String(40), nullable=False)
    key_id = Column(String(40), nullable=False)
    idempotency_digest = Column(String(64))
    request_digest = Column(String(64), nullable=False)
    # queued, running, done, partial, failed, expired, cancelled
    state = Column(String(16), nullable=False)
    subject_ref = Column(String(128))
    options = Column(JSON, nullable=False, default=dict)
    inputs = Column(JSON, nullable=False, default=list)
    result = Column(JSON)
    error_code = Column(String(60))
    attempts = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String(80))
    lease_expires_at = Column(Float)
    created_at = Column(Float, nullable=False)
    # When the job last joined the line: set on creation and on every retry.
    # Empty only for jobs queued by a 1.0 server during an upgrade (created_at counts then).
    queued_at = Column(Float)
    started_at = Column(Float)
    finished_at = Column(Float)
    expires_at = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint('key_id', 'idempotency_digest', name='uq_job_idempotency'),
        Index('ix_jobs_line', 'state', 'agent', 'created_at'),
        Index('ix_jobs_project_state', 'project', 'state'),
        Index('ix_jobs_expiry', 'expires_at'),
        Index('ix_jobs_queue_expiry', 'state', 'queued_at'),
    )


class RateWindow(Base):
    """Requests counted in one-minute windows."""

    __tablename__ = 'rate_windows'
    id = Column(String(160), primary_key=True)
    count = Column(Integer, nullable=False)
    expires_at = Column(Float, nullable=False)


class Usage(Base):
    """Daily totals per project and agent. Kept after jobs are deleted."""

    __tablename__ = 'usage'
    day = Column(String(10), primary_key=True)
    project = Column(String(100), primary_key=True)
    agent = Column(String(40), primary_key=True)
    jobs = Column(Integer, nullable=False, default=0)
    files = Column(Integer, nullable=False, default=0)
    done = Column(Integer, nullable=False, default=0)
    partial = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    expired = Column(Integer, nullable=False, default=0)
    run_seconds = Column(Float, nullable=False, default=0)
    wait_seconds = Column(Float, nullable=False, default=0)


class LookupCache(Base):
    """Answers from outside sources, kept for a short time. The id is a keyed hash of the question."""

    __tablename__ = 'lookup_cache'
    id = Column(String(64), primary_key=True)
    agent = Column(String(40), nullable=False)
    value = Column(JSON, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)


class ReferenceData(Base):
    """The current reference file of an agent (for example a monthly published list)."""

    __tablename__ = 'reference_data'
    agent = Column(String(40), primary_key=True)
    name = Column(String(40), primary_key=True)
    stored = Column(String(200), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    # The date the publisher gave the data (not when it was loaded), YYYY-MM-DD.
    as_of = Column(String(10), nullable=False)
    loaded_at = Column(Float, nullable=False)
    details = Column(JSON, nullable=False, default=dict)


class AuditEvent(Base):
    """Who did what with which key. Never holds file content."""

    __tablename__ = 'audit_events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    at = Column(Float, nullable=False, index=True)
    action = Column(String(40), nullable=False)
    project = Column(String(100))
    agent = Column(String(40))
    key_id = Column(String(40))
    job_id = Column(String(40))
