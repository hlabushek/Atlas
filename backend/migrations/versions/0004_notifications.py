"""Proactive notifications and Telegram; additive migration."""
from alembic import op
import sqlalchemy as sa
revision="0004"
down_revision="0003"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("messages",sa.Column("initiative_id",sa.String(36),nullable=True))
    op.create_index("ix_messages_initiative_id","messages",["initiative_id"])
    op.create_table('notification_settings',sa.Column('id',sa.Integer(),primary_key=True,nullable=False),sa.Column('enabled_at',sa.DateTime(timezone=True),nullable=True),sa.Column('enabled',sa.Boolean(),primary_key=False,nullable=True),sa.Column('timezone',sa.String(length=80),primary_key=False,nullable=True),sa.Column('quiet_start',sa.Integer(),primary_key=False,nullable=True),sa.Column('quiet_end',sa.Integer(),primary_key=False,nullable=True),sa.Column('daily_limit',sa.Integer(),primary_key=False,nullable=True),sa.Column('max_repeats',sa.Integer(),primary_key=False,nullable=True),sa.Column('repeat_hours',sa.Integer(),primary_key=False,nullable=True),sa.Column('channel',sa.String(length=20),primary_key=False,nullable=True))
    op.create_table('initiatives',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('event_key',sa.String(length=150),primary_key=False,nullable=True,unique=True),sa.Column('reason',sa.String(length=300),primary_key=False,nullable=True),sa.Column('message',sa.Text(),primary_key=False,nullable=True),sa.Column('priority',sa.Integer(),primary_key=False,nullable=True),sa.Column('related',sa.JSON(),primary_key=False,nullable=True),sa.Column('conversation_id',sa.String(length=36),sa.ForeignKey('conversations.id'),primary_key=False,nullable=True),sa.Column('channel',sa.String(length=20),primary_key=False,nullable=True),sa.Column('status',sa.String(length=30),primary_key=False,nullable=True),sa.Column('repeats',sa.Integer(),primary_key=False,nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('sent_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('next_allowed',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('expires_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('reply_id',sa.String(length=36),primary_key=False,nullable=True))
    op.create_table('deliveries',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('dedup_key',sa.String(length=180),primary_key=False,nullable=True,unique=True),sa.Column('initiative_id',sa.String(length=36),sa.ForeignKey('initiatives.id'),primary_key=False,nullable=True),sa.Column('conversation_id',sa.String(length=36),sa.ForeignKey('conversations.id'),primary_key=False,nullable=True),sa.Column('kind',sa.String(length=30),primary_key=False,nullable=True),sa.Column('channel',sa.String(length=20),primary_key=False,nullable=True),sa.Column('payload',sa.JSON(),primary_key=False,nullable=True),sa.Column('status',sa.String(length=30),primary_key=False,nullable=True),sa.Column('external_id',sa.String(length=60),primary_key=False,nullable=True),sa.Column('attempts',sa.Integer(),primary_key=False,nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('sent_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('lease_until',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('error',sa.String(length=150),primary_key=False,nullable=True))
    op.create_table('telegram_binding',sa.Column('id',sa.Integer(),primary_key=True,nullable=False),sa.Column('user_id',sa.String(length=30),primary_key=False,nullable=True),sa.Column('chat_id',sa.String(length=30),primary_key=False,nullable=True),sa.Column('conversation_id',sa.String(length=36),sa.ForeignKey('conversations.id'),primary_key=False,nullable=True),sa.Column('code_hash',sa.String(length=64),primary_key=False,nullable=True),sa.Column('code_expires',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('update_offset',sa.Integer(),primary_key=False,nullable=True))
    op.create_table('channel_updates',sa.Column('id',sa.String(length=60),primary_key=True,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=True))
    op.create_table('events',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('key',sa.String(length=150),primary_key=False,nullable=True,unique=True),sa.Column('kind',sa.String(length=50),primary_key=False,nullable=True),sa.Column('details',sa.JSON(),primary_key=False,nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=True))
    op.execute(sa.text("INSERT INTO notification_settings(id,enabled,timezone,quiet_start,quiet_end,daily_limit,max_repeats,repeat_hours,channel) VALUES (1,false,'Europe/Moscow',22,8,3,1,4,'web')"))
    op.execute(sa.text("INSERT INTO telegram_binding(id,update_offset) VALUES (1,0)"))

def downgrade():
    op.drop_table('events')
    op.drop_table('channel_updates')
    op.drop_table('telegram_binding')
    op.drop_table('deliveries')
    op.drop_table('initiatives')
    op.drop_table('notification_settings')
    op.drop_index("ix_messages_initiative_id",table_name="messages")
    op.drop_column("messages","initiative_id")
