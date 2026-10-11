"""Управление чатами без удаления/переписывания прежней истории."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    for column in [sa.Column('description', sa.String(1000), nullable=False, server_default=''),
                   sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
                   sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
                   sa.Column('trashed_at', sa.DateTime(timezone=True), nullable=True),
                   sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
                   sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True)]:
        op.add_column('conversations', column)


def downgrade():
    for name in ['updated_at', 'revision', 'trashed_at', 'archived', 'pinned', 'description']:
        op.drop_column('conversations', name)
