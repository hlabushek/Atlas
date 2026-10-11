"""Integration data, budget and ops; existing data preserved."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
revision="0008"
down_revision="0007"
branch_labels=None
depends_on=None
class QualifiedVector(VECTOR):
    def get_col_spec(self,**kw):return "public.vector(768)"
def upgrade():
    op.create_table('memory_facts',sa.Column('memory_id',sa.String(length=36),sa.ForeignKey('memories.id'),primary_key=True,nullable=False),sa.Column('key',sa.String(length=100),primary_key=False,nullable=False),sa.Column('value',sa.String(length=1000),primary_key=False,nullable=False),sa.Column('active',sa.Boolean(),primary_key=False,nullable=False))
    op.create_index('ix_memory_facts_key','memory_facts',['key'])
    op.create_table('memory_conflicts',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('left_id',sa.String(length=36),sa.ForeignKey('memories.id'),primary_key=False,nullable=False),sa.Column('right_id',sa.String(length=36),sa.ForeignKey('memories.id'),primary_key=False,nullable=False),sa.Column('status',sa.String(length=30),primary_key=False,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False))
    op.create_table('embeddings',sa.Column('key',sa.String(length=64),primary_key=True,nullable=False),sa.Column('memory_id',sa.String(length=36),sa.ForeignKey('memories.id'),primary_key=False,nullable=True),sa.Column('model',sa.String(length=200),primary_key=False,nullable=False),sa.Column('digest',sa.String(length=64),primary_key=False,nullable=False),sa.Column('vector',QualifiedVector(768).with_variant(sa.JSON(),"sqlite"),primary_key=False,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False))
    op.create_index('ix_embeddings_memory_id','embeddings',['memory_id'])
    op.create_table('attachments',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('name',sa.String(length=100),primary_key=False,nullable=False),sa.Column('content',sa.Text(),primary_key=False,nullable=False),sa.Column('sha256',sa.String(length=64),primary_key=False,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False))
    op.create_table('budget',sa.Column('id',sa.Integer(),primary_key=True,nullable=False),sa.Column('token_limit',sa.Integer(),primary_key=False,nullable=False),sa.Column('cost_limit',sa.Integer(),primary_key=False,nullable=True),sa.Column('input_rate',sa.Integer(),primary_key=False,nullable=False),sa.Column('output_rate',sa.Integer(),primary_key=False,nullable=False))
    op.create_table('model_usage',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('purpose',sa.String(length=30),primary_key=False,nullable=False),sa.Column('provider',sa.String(length=100),primary_key=False,nullable=False),sa.Column('status',sa.String(length=30),primary_key=False,nullable=False),sa.Column('tokens',sa.Integer(),primary_key=False,nullable=False),sa.Column('cost',sa.Integer(),primary_key=False,nullable=True),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False))
    op.create_table('push_subscriptions',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('endpoint',sa.Text(),primary_key=False,nullable=False,unique=True),sa.Column('keys',sa.JSON(),primary_key=False,nullable=False),sa.Column('enabled',sa.Boolean(),primary_key=False,nullable=False))
    op.create_table('push_deliveries',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('initiative_id',sa.String(length=36),sa.ForeignKey('initiatives.id'),primary_key=False,nullable=False),sa.Column('subscription_id',sa.String(length=36),sa.ForeignKey('push_subscriptions.id'),primary_key=False,nullable=False),sa.Column('status',sa.String(length=30),primary_key=False,nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),sa.UniqueConstraint('initiative_id','subscription_id'))
    op.create_table('owner_sessions',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),sa.Column('revoked',sa.Boolean(),primary_key=False,nullable=False))
    op.create_table('service_heartbeats',sa.Column('name',sa.String(length=30),primary_key=True,nullable=False),sa.Column('seen_at',sa.DateTime(timezone=True),primary_key=False,nullable=False))
    op.execute(sa.text('INSERT INTO budget(id,token_limit,input_rate,output_rate) VALUES (1,1000000,0,0)'))
def downgrade():
    op.drop_table('service_heartbeats')
    op.drop_table('owner_sessions')
    op.drop_table('push_deliveries')
    op.drop_table('push_subscriptions')
    op.drop_table('model_usage')
    op.drop_table('budget')
    op.drop_table('attachments')
    op.drop_table('embeddings')
    op.drop_table('memory_conflicts')
    op.drop_table('memory_facts')
