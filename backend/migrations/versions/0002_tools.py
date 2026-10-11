"""Durable instrument steps and scoped approvals, no changes to existing chat data."""
from alembic import op
import sqlalchemy as sa
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade():
    for col in [sa.Column("kind",sa.String(20),nullable=False,server_default="chat"),
                sa.Column("current_step",sa.Integer(),nullable=False,server_default="0"),
                sa.Column("dry_run",sa.Boolean(),nullable=False,server_default=sa.false()),
                sa.Column("max_steps",sa.Integer(),nullable=False,server_default="8")]:op.add_column("tasks",col)
    op.add_column("tasks",sa.Column("request_fingerprint",sa.String(64)))
    op.create_table("tool_calls",sa.Column("id",sa.String(36),primary_key=True),sa.Column("task_id",sa.String(36),sa.ForeignKey("tasks.id"),nullable=False),sa.Column("step",sa.Integer(),nullable=False),sa.Column("tool",sa.String(100),nullable=False),sa.Column("arguments",sa.JSON(),nullable=False),sa.Column("status",sa.String(30),nullable=False),sa.Column("result",sa.JSON()),sa.Column("error",sa.String(200)),sa.Column("attempts",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("task_id","step"))
    op.create_index("ix_tool_calls_task_id","tool_calls",["task_id"])
    op.create_table("approvals",sa.Column("id",sa.String(36),primary_key=True),sa.Column("call_id",sa.String(36),sa.ForeignKey("tool_calls.id"),nullable=False,unique=True),sa.Column("task_id",sa.String(36),sa.ForeignKey("tasks.id"),nullable=False),sa.Column("status",sa.String(30),nullable=False),sa.Column("reason",sa.String(300),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("resolved_at",sa.DateTime(timezone=True)))
    op.create_index("ix_approvals_task_id","approvals",["task_id"])
    op.create_table("tool_grants",sa.Column("tool",sa.String(100),primary_key=True),sa.Column("allowed",sa.Boolean(),nullable=False))
    op.create_table("notes",sa.Column("id",sa.String(36),primary_key=True),sa.Column("call_id",sa.String(36),sa.ForeignKey("tool_calls.id"),nullable=False,unique=True),sa.Column("title",sa.String(100),nullable=False),sa.Column("content",sa.Text(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))

def downgrade():
    for name in ("notes","tool_grants","approvals","tool_calls"):op.drop_table(name)
    for name in ("request_fingerprint","max_steps","dry_run","current_step","kind"):op.drop_column("tasks",name)
