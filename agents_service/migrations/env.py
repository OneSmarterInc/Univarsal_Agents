"""Alembic entry point. Run through `agents init-db` (Database.upgrade)."""

from alembic import context
from sqlalchemy import create_engine, text

from agents_service.core.models import Base

config = context.config
target_metadata = Base.metadata


def run():
    engine = config.attributes.get('connection_engine') or create_engine(
        config.get_main_option('sqlalchemy.url')
    )
    with engine.begin() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == 'sqlite',
        )
        if connection.dialect.name == 'postgresql':
            # Two services starting at once (API and workers) must not migrate together:
            # the second waits here, then finds nothing left to do.
            connection.execute(text('SELECT pg_advisory_xact_lock(7151000301)'))
        with context.begin_transaction():
            context.run_migrations()


run()
