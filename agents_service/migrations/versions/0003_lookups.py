"""Tables for lookup agents: a short-lived cache of outside answers and the
current reference file of each agent.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'lookup_cache',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('agent', sa.String(40), nullable=False),
        sa.Column('value', sa.JSON, nullable=False),
        sa.Column('expires_at', sa.Float, nullable=False),
    )
    op.create_index('ix_lookup_cache_expires_at', 'lookup_cache', ['expires_at'])
    op.create_table(
        'reference_data',
        sa.Column('agent', sa.String(40), primary_key=True),
        sa.Column('name', sa.String(40), primary_key=True),
        sa.Column('stored', sa.String(200), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size', sa.Integer, nullable=False),
        sa.Column('as_of', sa.String(10), nullable=False),
        sa.Column('loaded_at', sa.Float, nullable=False),
        sa.Column('details', sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table('reference_data')
    op.drop_index('ix_lookup_cache_expires_at', table_name='lookup_cache')
    op.drop_table('lookup_cache')
