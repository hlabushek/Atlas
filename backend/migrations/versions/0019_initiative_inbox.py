"""Initiatives live in the activity inbox, not generated conversations."""
from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('initiatives') as batch:
        batch.alter_column('conversation_id', existing_type=sa.String(length=36), nullable=True)
    op.add_column('notification_settings', sa.Column(
        'context_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'initiative_decisions',
        sa.Column('source_key', sa.String(length=180), primary_key=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('model', sa.String(length=160), nullable=False),
        sa.Column('context_hash', sa.String(length=64), nullable=False),
        sa.Column('retry_after', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_initiative_decisions_status', 'initiative_decisions', ['status'])


def downgrade():
    bind = op.get_bind()
    pending = bind.execute(sa.text(
        'SELECT count(*) FROM initiatives WHERE conversation_id IS NULL')).scalar_one()
    if pending:
        conversation_id = str(uuid.uuid4())
        stamp = datetime.now(timezone.utc)
        bind.execute(sa.text("""
            INSERT INTO conversations
              (id,title,description,pinned,archived,trashed_at,revision,updated_at,created_at)
            VALUES (:id,'Atlas — архив инициатив','',false,false,NULL,1,:stamp,:stamp)
        """), {'id': conversation_id, 'stamp': stamp})
        bind.execute(sa.text(
            'UPDATE initiatives SET conversation_id=:id WHERE conversation_id IS NULL'),
            {'id': conversation_id})
    op.drop_index('ix_initiative_decisions_status', table_name='initiative_decisions')
    op.drop_table('initiative_decisions')
    op.drop_column('notification_settings', 'context_enabled')
    with op.batch_alter_table('initiatives') as batch:
        batch.alter_column('conversation_id', existing_type=sa.String(length=36), nullable=False)
