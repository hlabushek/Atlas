"""Bind connector grants and schedules to reviewed scope and expiry."""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("tool_grants", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tool_grants", sa.Column("scope_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("connector_sources", sa.Column("schedule_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("connector_sources", sa.Column("schedule_expires_at", sa.DateTime(timezone=True), nullable=True))
    # Old connector grants cannot prove the reviewed field set. Fail closed on upgrade.
    op.execute(sa.text("UPDATE tool_grants SET allowed = false WHERE tool LIKE 'connector.%.read'"))

def downgrade():
    op.drop_column("connector_sources", "schedule_expires_at")
    op.drop_column("connector_sources", "schedule_fingerprint")
    op.drop_column("tool_grants", "scope_fingerprint")
    op.drop_column("tool_grants", "expires_at")
