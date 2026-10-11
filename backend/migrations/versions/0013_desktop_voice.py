"""Bind an explicitly enabled desktop voice client to an Atlas conversation."""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("voice_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("devices", sa.Column("voice_conversation_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("devices") as batch:
        batch.create_foreign_key(
            "fk_devices_voice_conversation_id_conversations",
            "conversations", ["voice_conversation_id"], ["id"], ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("devices") as batch:
        batch.drop_constraint("fk_devices_voice_conversation_id_conversations", type_="foreignkey")
        batch.drop_column("voice_conversation_id")
        batch.drop_column("voice_enabled")
