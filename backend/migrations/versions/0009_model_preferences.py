"""Persistent non-secret model preferences. Existing environment remains active."""
from alembic import op
import sqlalchemy as sa
revision='0009'
down_revision='0008'
branch_labels=None
depends_on=None
def upgrade():
    table=op.create_table('model_preferences',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('config',sa.JSON(),nullable=False))
    role=lambda model:{'provider':'routerai','model':model,'input_rate':0,'output_rate':0}
    op.bulk_insert(table,[{'id':1,'revision':1,'config':{'mode':'environment','main':role('deepseek/deepseek-v4.1-flash'),'planner':role('anthropic/claude-sonnet-5.5'),'embedding':role('qwen/qwen3-embedding-8b'),'embeddings_enabled':True,'max_output_tokens':1024}}])
    op.add_column('tasks',sa.Column('model_profile',sa.JSON(),nullable=True))
def downgrade():
    with op.batch_alter_table('tasks') as batch:batch.drop_column('model_profile')
    op.drop_table('model_preferences')
