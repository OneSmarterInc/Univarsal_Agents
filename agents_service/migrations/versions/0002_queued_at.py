"""Jobs remember when they last joined the line, so a retried job is not expired
for the time it waited before its first try.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('jobs') as batch:
        batch.add_column(sa.Column('queued_at', sa.Float))
    op.execute('UPDATE jobs SET queued_at = created_at')
    with op.batch_alter_table('jobs') as batch:
        batch.alter_column('queued_at', existing_type=sa.Float, nullable=False)
    op.create_index('ix_jobs_queue_expiry', 'jobs', ['state', 'queued_at'])


def downgrade():
    op.drop_index('ix_jobs_queue_expiry', table_name='jobs')
    with op.batch_alter_table('jobs') as batch:
        batch.drop_column('queued_at')
