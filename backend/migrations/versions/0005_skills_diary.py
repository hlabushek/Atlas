"""Data-only skill manifests and diary records."""
from alembic import op
import sqlalchemy as sa
revision="0005"
down_revision="0004"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('skills',sa.Column('id',sa.String(length=80),primary_key=True,nullable=False),sa.Column('version',sa.String(length=30),primary_key=False,nullable=True),sa.Column('manifest',sa.JSON(),primary_key=False,nullable=True),sa.Column('enabled',sa.Boolean(),primary_key=False,nullable=True),sa.Column('installed_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('health',sa.String(length=100),primary_key=False,nullable=True))
    op.create_table('demo_homework',sa.Column('id',sa.String(length=80),primary_key=True,nullable=False),sa.Column('subject',sa.String(length=100),primary_key=False,nullable=True),sa.Column('title',sa.String(length=300),primary_key=False,nullable=True),sa.Column('due_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('grade',sa.String(length=20),primary_key=False,nullable=True))
    op.create_table('assignments',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('provider',sa.String(length=80),primary_key=False,nullable=True),sa.Column('external_id',sa.String(length=80),primary_key=False,nullable=True),sa.Column('subject',sa.String(length=100),primary_key=False,nullable=True),sa.Column('title',sa.String(length=300),primary_key=False,nullable=True),sa.Column('due_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('grade',sa.String(length=20),primary_key=False,nullable=True),sa.Column('source',sa.String(length=200),primary_key=False,nullable=True),sa.Column('content_hash',sa.String(length=64),primary_key=False,nullable=True),sa.Column('synced_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('revision',sa.Integer(),primary_key=False,nullable=True),sa.UniqueConstraint('provider','external_id'))
    op.create_table('study_tasks',sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),sa.Column('assignment_id',sa.String(length=36),sa.ForeignKey('assignments.id'),primary_key=False,nullable=True,unique=True),sa.Column('goal',sa.String(length=400),primary_key=False,nullable=True),sa.Column('due_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),sa.Column('status',sa.String(length=30),primary_key=False,nullable=True))

def downgrade():
    op.drop_table('study_tasks')
    op.drop_table('assignments')
    op.drop_table('demo_homework')
    op.drop_table('skills')
