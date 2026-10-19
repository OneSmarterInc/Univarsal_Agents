"""Alembic entry point. Run through `agents init-db` (Database.upgrade)."""

from alembic import context
from sqlalchemy import create_engine

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
        with context.begin_transaction():
            context.run_migrations()


run()
