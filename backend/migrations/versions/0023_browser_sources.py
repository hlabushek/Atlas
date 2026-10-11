"""Optional browser rendering and encrypted browser state for existing sources."""
from alembic import op
import sqlalchemy as sa

revision='0023'
down_revision='0022'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('connector_sources',sa.Column('session_kind',sa.String(16),nullable=True))
    op.add_column('connector_sources',sa.Column('fetch_mode',sa.String(16),nullable=False,server_default='http'))
    op.execute("UPDATE connector_sources SET session_kind='cookies' WHERE session_ciphertext IS NOT NULL")

def downgrade():
    op.drop_column('connector_sources','fetch_mode')
    op.drop_column('connector_sources','session_kind')
