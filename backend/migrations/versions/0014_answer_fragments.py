"""Добавочный временный транспорт ответов; существующие задачи не меняются."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('answer_fragments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('task_id', sa.String(36), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('claim', sa.String(36), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'claim', 'sequence'))
    op.create_index('ix_answer_fragments_task_id', 'answer_fragments', ['task_id'])
    op.create_index('ix_answer_fragments_created_at', 'answer_fragments', ['created_at'])


def downgrade():
    op.drop_table('answer_fragments')
