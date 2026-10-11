"""Вложения отдельно от текста сообщения; прежние записи не изменяются."""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('attachments', sa.JSON(), nullable=True))
    op.add_column('messages', sa.Column('attachment_task_id', sa.String(36), nullable=True))
    op.add_column('tasks', sa.Column('attachments', sa.JSON(), nullable=True))
    op.add_column('attachments', sa.Column('original', sa.LargeBinary(), nullable=True))
    op.add_column('attachments', sa.Column('mime', sa.String(100), nullable=True))
    op.add_column('attachments', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('attachments', sa.Column('warnings', sa.JSON(), nullable=True))
    op.add_column('attachments', sa.Column('image', sa.LargeBinary(), nullable=True))
    op.add_column('attachments', sa.Column('width', sa.Integer(), nullable=True))
    op.add_column('attachments', sa.Column('height', sa.Integer(), nullable=True))


def downgrade():
    for table, columns in [('attachments', ['height', 'width', 'image', 'warnings', 'deleted_at', 'mime', 'original']),
                           ('tasks', ['attachments']), ('messages', ['attachment_task_id', 'attachments'])]:
        for name in columns:
            op.drop_column(table, name)
