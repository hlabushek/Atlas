"""Encrypted connector sessions and bounded write action runs."""
from alembic import op
import sqlalchemy as sa

revision='0022'
down_revision='0021'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('connector_sources',sa.Column('session_ciphertext',sa.Text(),nullable=True))
    op.add_column('connector_sources',sa.Column('session_fingerprint',sa.String(64),nullable=True))
    op.add_column('connector_sources',sa.Column('session_generation',sa.String(36),nullable=True))
    op.add_column('connector_sources',sa.Column('session_expires_at',sa.DateTime(timezone=True),nullable=True))
    op.create_table('connector_actions',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('connector_id',sa.String(41),sa.ForeignKey('connector_sources.id',ondelete='CASCADE'),nullable=False),
        sa.Column('name',sa.String(100),nullable=False),
        sa.Column('method',sa.String(8),nullable=False),
        sa.Column('target_url',sa.String(500),nullable=False),
        sa.Column('body',sa.JSON(),nullable=False),
        sa.Column('verification_url',sa.String(500),nullable=False),
        sa.Column('verification_field',sa.String(80),nullable=False),
        sa.Column('expected_value',sa.String(500),nullable=False),
        sa.Column('csrf_cookie_name',sa.String(128),nullable=True),
        sa.Column('csrf_header_name',sa.String(30),nullable=True),
        sa.Column('interval_minutes',sa.Integer(),nullable=True),
        sa.Column('next_run_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('scope_fingerprint',sa.String(64),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.Column('auto_approved',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('approval_expires_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('max_runs',sa.Integer(),nullable=True),
        sa.Column('run_count',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_connector_actions_connector_id','connector_actions',['connector_id'])
    op.create_table('connector_action_runs',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('action_id',sa.String(36),sa.ForeignKey('connector_actions.id',ondelete='CASCADE'),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),
        sa.Column('request_fingerprint',sa.String(64),nullable=False),
        sa.Column('auto_authorized',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('scheduled_for',sa.DateTime(timezone=True),nullable=False),
        sa.Column('approved_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('started_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('completed_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('error_code',sa.String(60),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_connector_action_runs_action_id','connector_action_runs',['action_id'])

def downgrade():
    op.drop_index('ix_connector_action_runs_action_id',table_name='connector_action_runs')
    op.drop_table('connector_action_runs')
    op.drop_index('ix_connector_actions_connector_id',table_name='connector_actions')
    op.drop_table('connector_actions')
    op.drop_column('connector_sources','session_expires_at')
    op.drop_column('connector_sources','session_fingerprint')
    op.drop_column('connector_sources','session_generation')
    op.drop_column('connector_sources','session_ciphertext')
