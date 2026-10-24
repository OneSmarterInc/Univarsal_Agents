"""Database connection and schema upgrades."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from agents_service.core.errors import ServiceError

MIGRATIONS = Path(__file__).resolve().parent.parent / 'migrations'


class Database:
    def __init__(self, url):
        self.url = url
        sqlite = url.startswith('sqlite')
        if sqlite:
            path = url.split(':///', 1)[-1]
            if path and path != ':memory:':
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            **({'connect_args': {'check_same_thread': False, 'timeout': 30}} if sqlite else {}),
        )
        if sqlite:
            # Several processes (API, workers) share one file: WAL lets them
            # read while one writes, and the busy timeout waits for a lock.
            @event.listens_for(self.engine, 'connect')
            def _pragmas(connection, _record):
                cursor = connection.cursor()
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA busy_timeout=30000')
                cursor.execute('PRAGMA foreign_keys=ON')
                cursor.close()

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        """One transaction: committed when the block ends, rolled back on error."""
        try:
            with self.sessions() as s:
                try:
                    yield s
                    s.commit()
                except Exception:
                    s.rollback()
                    raise
        except OperationalError as exc:
            raise ServiceError('database_unavailable', 503, True, 5) from exc

    def upgrade(self, revision='head'):
        """Bring the schema to the latest migration (or to `revision`)."""
        from alembic import command
        from alembic.config import Config

        config = Config()
        config.set_main_option('script_location', str(MIGRATIONS))
        config.set_main_option('sqlalchemy.url', self.url.replace('%', '%%'))
        config.attributes['connection_engine'] = self.engine
        command.upgrade(config, revision)
