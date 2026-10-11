"""Initial persisted chat, memory and durable reply queue."""
from alembic import op
import sqlalchemy as sa
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    if op.get_bind().dialect.name == "postgresql": op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("conversations", sa.Column("id", sa.String(36), primary_key=True), sa.Column("title", sa.String(100), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("messages", sa.Column("id", sa.String(36), primary_key=True), sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False), sa.Column("role", sa.String(20), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("task_id", sa.String(36), unique=True))
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table("memories", sa.Column("id", sa.String(36), primary_key=True), sa.Column("category", sa.String(30), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("source", sa.String(100), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("access", sa.String(30), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("tasks", sa.Column("id", sa.String(36), primary_key=True), sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False), sa.Column("request_key", sa.String(36), nullable=False), sa.Column("goal", sa.Text(), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("plan", sa.JSON(), nullable=False), sa.Column("result", sa.Text()), sa.Column("error", sa.String(200)), sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("claim", sa.String(36)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("conversation_id", "request_key"))
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_conversation_id", "tasks", ["conversation_id"])
    op.create_table("audit", sa.Column("id", sa.String(36), primary_key=True), sa.Column("task_id", sa.String(36)), sa.Column("event", sa.String(60), nullable=False), sa.Column("details", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    for name in ("audit", "tasks", "memories", "messages", "conversations"): op.drop_table(name)
