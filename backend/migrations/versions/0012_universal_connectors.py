"""Add owner-approved declarative browser connectors and imported records."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "connector_sources",
        sa.Column("id", sa.String(length=41), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("origin", sa.String(length=300), nullable=False),
        sa.Column("recipe", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "connector_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("connector_id", sa.String(length=41), sa.ForeignKey("connector_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_key", sa.String(length=200), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connector_id", "record_key", name="uq_connector_record_source_key"),
    )
    op.create_index("ix_connector_records_connector_id", "connector_records", ["connector_id"])

def downgrade():
    op.drop_index("ix_connector_records_connector_id", table_name="connector_records")
    op.drop_table("connector_records")
    op.drop_table("connector_sources")
