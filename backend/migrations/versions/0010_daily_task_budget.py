"""Add UTC daily and per-task model spending limits without rewriting usage."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("budget", sa.Column("daily_token_limit", sa.Integer(), nullable=True))
    op.add_column("budget", sa.Column("daily_cost_limit", sa.Integer(), nullable=True))
    op.add_column("budget", sa.Column("task_cost_limit", sa.Integer(), nullable=True))
    op.add_column("model_usage", sa.Column("task_id", sa.String(length=36), nullable=True))
    op.create_index("ix_model_usage_task_id", "model_usage", ["task_id"])

def downgrade():
    op.drop_index("ix_model_usage_task_id", table_name="model_usage")
    op.drop_column("model_usage", "task_id")
    op.drop_column("budget", "task_cost_limit")
    op.drop_column("budget", "daily_cost_limit")
    op.drop_column("budget", "daily_token_limit")
