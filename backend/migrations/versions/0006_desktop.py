"""Desktop identities and durable commands; additive."""
from alembic import op
import sqlalchemy as sa
revision="0006"
down_revision="0005"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('devices',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('name',sa.String(length=100),primary_key=False,nullable=False),sa.Column('fingerprint',sa.String(length=64),primary_key=False,nullable=False,unique=True),sa.Column('enabled',sa.Boolean(),primary_key=False,nullable=False),sa.Column('roots',sa.JSON(),primary_key=False,nullable=False),sa.Column('operations',sa.JSON(),primary_key=False,nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),sa.Column('last_seen',sa.DateTime(timezone=True),primary_key=False,nullable=True))
    op.create_table('device_commands',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('device_id',sa.String(length=36),sa.ForeignKey('devices.id'),primary_key=False,nullable=False),sa.Column('call_id',sa.String(length=36),sa.ForeignKey('tool_calls.id'),primary_key=False,nullable=False,unique=True),sa.Column('arguments',sa.JSON(),primary_key=False,nullable=False),sa.Column('status',sa.String(length=30),primary_key=False,nullable=False),sa.Column('lease_token',sa.String(length=64),primary_key=False,nullable=True),sa.Column('lease_until',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('expires_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),sa.Column('result',sa.JSON(),primary_key=False,nullable=True),sa.Column('error',sa.String(length=150),primary_key=False,nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False))
    op.create_index('ix_device_commands_device_id','device_commands',['device_id'])
def downgrade():
    op.drop_table('device_commands')
    op.drop_table('devices')
