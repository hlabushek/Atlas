"""Рабочие пространства: только новые таблицы, история не меняется."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('spaces', sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('name', sa.String(100), nullable=False),
                    sa.Column('description', sa.String(1000), nullable=False),
                    sa.Column('archived', sa.Boolean(), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('space_conversations',
                    sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id'), primary_key=True),
                    sa.Column('space_id', sa.String(36), sa.ForeignKey('spaces.id'), nullable=False))
    op.create_index('ix_space_conversations_space_id', 'space_conversations', ['space_id'])
    op.create_table('space_memories',
                    sa.Column('space_id', sa.String(36), sa.ForeignKey('spaces.id'), primary_key=True),
                    sa.Column('memory_id', sa.String(36), sa.ForeignKey('memories.id', ondelete='CASCADE'), primary_key=True))


def downgrade():
    op.drop_table('space_memories')
    op.drop_table('space_conversations')
    op.drop_table('spaces')
