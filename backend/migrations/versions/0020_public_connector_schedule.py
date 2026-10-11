"""Optional public connector polling; existing sources remain unscheduled."""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("connector_sources", sa.Column("schedule_minutes", sa.Integer(), nullable=True))
    op.add_column("connector_sources", sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("connector_sources", sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("connector_sources", sa.Column("last_run_status", sa.String(length=20), nullable=True))

def downgrade():
    op.drop_column("connector_sources", "last_run_status")
    op.drop_column("connector_sources", "last_run_at")
    op.drop_column("connector_sources", "next_run_at")
    op.drop_column("connector_sources", "schedule_minutes")
