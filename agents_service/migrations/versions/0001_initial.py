"""Initial schema: keys, jobs, rate windows, usage and audit events.

Revision ID: 0001
Revises:
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'api_keys',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project', sa.String(100), nullable=False),
        sa.Column('agent', sa.String(40), nullable=False),
        sa.Column('digest', sa.String(64), nullable=False, unique=True),
        sa.Column('label', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', sa.Float, nullable=False),
        sa.Column('revoked_at', sa.Float),
        sa.Column('last_used_at', sa.Float),
    )
    op.create_index('ix_api_keys_project', 'api_keys', ['project'])
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(40), primary_key=True),
        sa.Column('project', sa.String(100), nullable=False),
        sa.Column('agent', sa.String(40), nullable=False),
        sa.Column('key_id', sa.String(40), nullable=False),
        sa.Column('idempotency_digest', sa.String(64)),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('subject_ref', sa.String(128)),
        sa.Column('options', sa.JSON, nullable=False),
        sa.Column('inputs', sa.JSON, nullable=False),
        sa.Column('result', sa.JSON),
        sa.Column('error_code', sa.String(60)),
        sa.Column('attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('lease_owner', sa.String(80)),
        sa.Column('lease_expires_at', sa.Float),
        sa.Column('created_at', sa.Float, nullable=False),
        sa.Column('started_at', sa.Float),
        sa.Column('finished_at', sa.Float),
        sa.Column('expires_at', sa.Float, nullable=False),
        sa.UniqueConstraint('key_id', 'idempotency_digest', name='uq_job_idempotency'),
    )
    op.create_index('ix_jobs_line', 'jobs', ['state', 'agent', 'created_at'])
    op.create_index('ix_jobs_project_state', 'jobs', ['project', 'state'])
    op.create_index('ix_jobs_expiry', 'jobs', ['expires_at'])
    op.create_table(
        'rate_windows',
        sa.Column('id', sa.String(160), primary_key=True),
        sa.Column('count', sa.Integer, nullable=False),
        sa.Column('expires_at', sa.Float, nullable=False),
    )
    op.create_table(
        'usage',
        sa.Column('day', sa.String(10), primary_key=True),
        sa.Column('project', sa.String(100), primary_key=True),
        sa.Column('agent', sa.String(40), primary_key=True),
        sa.Column('jobs', sa.Integer, nullable=False, server_default='0'),
        sa.Column('files', sa.Integer, nullable=False, server_default='0'),
        sa.Column('done', sa.Integer, nullable=False, server_default='0'),
        sa.Column('partial', sa.Integer, nullable=False, server_default='0'),
        sa.Column('failed', sa.Integer, nullable=False, server_default='0'),
        sa.Column('expired', sa.Integer, nullable=False, server_default='0'),
        sa.Column('run_seconds', sa.Float, nullable=False, server_default='0'),
        sa.Column('wait_seconds', sa.Float, nullable=False, server_default='0'),
    )
    op.create_table(
        'audit_events',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('at', sa.Float, nullable=False),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('project', sa.String(100)),
        sa.Column('agent', sa.String(40)),
        sa.Column('key_id', sa.String(40)),
        sa.Column('job_id', sa.String(40)),
    )
    op.create_index('ix_audit_events_at', 'audit_events', ['at'])


def downgrade():
    for table in ('audit_events', 'usage', 'rate_windows', 'jobs', 'api_keys'):
        op.drop_table(table)
