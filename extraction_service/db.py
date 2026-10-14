import time
from contextlib import contextmanager

from sqlalchemy import JSON, Boolean, Column, Float, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Gate(Base):
    __tablename__ = "gate"
    id = Column(Integer, primary_key=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id = Column(String(64), primary_key=True)
    tenant = Column(String(100), nullable=False, index=True)
    digest = Column(String(64), nullable=False, unique=True)
    scopes = Column(JSON, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)


class Upload(Base):
    __tablename__ = "uploads"
    id = Column(String(64), primary_key=True)
    tenant = Column(String(100), nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    media_type = Column(String(100), nullable=False)
    suffix = Column(String(10), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    expires = Column(Float, nullable=False)
    job_id = Column(String(64), index=True)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String(64), primary_key=True)
    tenant = Column(String(100), nullable=False, index=True)
    key_id = Column(String(64), nullable=False)
    idem_digest = Column(String(64), nullable=False, unique=True)
    request_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    result = Column(JSON)
    state = Column(String(20), nullable=False, default="queued", index=True)
    fence = Column(Integer, nullable=False, default=0)
    created = Column(Float, nullable=False, default=time.time)
    updated = Column(Float, nullable=False, default=time.time)
    expires = Column(Float, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)


class Rate(Base):
    __tablename__ = "rate_limits"
    id = Column(String(150), primary_key=True)
    count = Column(Integer, nullable=False)
    expires = Column(Float, nullable=False)


class Connection(Base):
    __tablename__ = "github_connections"
    id = Column(String(64), primary_key=True)
    tenant = Column(String(100), nullable=False, index=True)
    key_id = Column(String(64), nullable=False)
    github_id = Column(Integer, nullable=False)
    token_ciphertext = Column(Text, nullable=False)
    scopes = Column(String(1000), nullable=False)
    subject_ref = Column(String(128))


class OAuthState(Base):
    __tablename__ = "oauth_states"
    digest = Column(String(64), primary_key=True)
    tenant = Column(String(100), nullable=False)
    key_id = Column(String(64), nullable=False)
    subject_ref = Column(String(128))
    expires = Column(Float, nullable=False)


class Database:
    def __init__(self, url):
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            **(
                {"connect_args": {"check_same_thread": False, "timeout": 30}}
                if url.startswith("sqlite")
                else {}
            ),
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        # Bootstrap only; future schema changes must be explicit migrations.
        Base.metadata.create_all(self.engine)
        with self.sessions.begin() as s:
            if s.get(Gate, 1) is None:
                s.add(Gate(id=1))

    @contextmanager
    def transaction(self):
        with self.sessions() as s:
            try:
                if self.engine.dialect.name == "sqlite":
                    s.execute(text("BEGIN IMMEDIATE"))
                else:
                    s.execute(select(Gate).where(Gate.id == 1).with_for_update()).scalar_one()
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
