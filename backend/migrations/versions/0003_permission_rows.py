"""Seed lockable policy rows; preserve any explicit owner grants/revocations."""
from alembic import op
import sqlalchemy as sa
revision="0003"
down_revision="0002"
branch_labels=None
depends_on=None

def upgrade():
    grants=sa.table("tool_grants",sa.column("tool",sa.String(100)),sa.column("allowed",sa.Boolean()))
    conn=op.get_bind()
    for name in ("memory.search","notes.create"):
        if conn.scalar(sa.select(grants.c.tool).where(grants.c.tool==name)) is None:
            conn.execute(grants.insert().values(tool=name,allowed=True))

def downgrade():
    # Policy rows may have been changed by the owner; preserve them on downgrade.
    pass
