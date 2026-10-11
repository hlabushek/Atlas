"""Serialize device dispatch, revoke and cancellation without replacing existing data."""
from alembic import op
import sqlalchemy as sa
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('device_mutex',sa.Column('id',sa.Integer(),primary_key=True,nullable=False))
    op.execute(sa.text('INSERT INTO device_mutex(id) VALUES (1)'))
def downgrade():
    op.drop_table('device_mutex')
