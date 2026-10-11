"""Track Telegram speech costs separately in RUB kopecks."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    budget_columns = {column["name"] for column in sa.inspect(bind).get_columns("budget")}
    for name in ("speech_rub_month_limit", "speech_rub_day_limit"):
        if name not in budget_columns:
            op.add_column("budget", sa.Column(name, sa.Integer(), nullable=True))

    inspector = sa.inspect(bind)
    if "speech_usage" not in inspector.get_table_names():
        op.create_table(
            "speech_usage",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("duration_seconds", sa.Integer(), nullable=False),
            sa.Column("cost_kopecks", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    else:
        columns = {column["name"]: column for column in inspector.get_columns("speech_usage")}
        expected = {"id", "provider", "status", "duration_seconds", "cost_kopecks", "created_at"}
        if set(columns) != expected or any(columns[name]["nullable"] for name in expected):
            raise RuntimeError("Existing speech_usage table does not match migration 0011")
        if set(inspector.get_pk_constraint("speech_usage").get("constrained_columns") or []) != {"id"}:
            raise RuntimeError("Existing speech_usage primary key does not match migration 0011")

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("speech_usage")}
    if "ix_speech_usage_created_at" not in indexes:
        op.create_index("ix_speech_usage_created_at", "speech_usage", ["created_at"])

def downgrade():
    op.drop_index("ix_speech_usage_created_at", table_name="speech_usage")
    op.drop_table("speech_usage")
    op.drop_column("budget", "speech_rub_day_limit")
    op.drop_column("budget", "speech_rub_month_limit")
