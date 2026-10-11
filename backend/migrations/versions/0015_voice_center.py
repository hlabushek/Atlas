"""Добавочный Voice Center: не изменяет существующие столбцы/записи."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('voice_stations', sa.Column('device_id', sa.String(36), sa.ForeignKey('devices.id'), primary_key=True),
                    sa.Column('revision', sa.Integer(), nullable=False), sa.Column('policy', sa.JSON(), nullable=False),
                    sa.Column('state', sa.JSON(), nullable=False), sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False))
    op.create_table('voice_controls', sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('device_id', sa.String(36), sa.ForeignKey('devices.id'), nullable=False),
                    sa.Column('kind', sa.String(30), nullable=False), sa.Column('payload', sa.JSON(), nullable=False),
                    sa.Column('status', sa.String(20), nullable=False), sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_voice_controls_device_id', 'voice_controls', ['device_id'])
    op.create_table('voice_events', sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('device_id', sa.String(36), sa.ForeignKey('devices.id'), nullable=False),
                    sa.Column('data', sa.JSON(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_voice_events_device_id', 'voice_events', ['device_id'])
    op.create_index('ix_voice_events_created_at', 'voice_events', ['created_at'])
    op.create_table('voice_requests', sa.Column('task_id', sa.String(36), sa.ForeignKey('tasks.id'), primary_key=True),
                    sa.Column('device_id', sa.String(36), sa.ForeignKey('devices.id'), nullable=False),
                    sa.Column('keep_text', sa.Boolean(), nullable=False), sa.Column('digest', sa.String(64), nullable=False),
                    sa.Column('context', sa.JSON(), nullable=False), sa.Column('consumed', sa.Boolean(), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_voice_requests_device_id', 'voice_requests', ['device_id'])


def downgrade():
    for name in ('voice_requests', 'voice_events', 'voice_controls', 'voice_stations'):
        op.drop_table(name)
