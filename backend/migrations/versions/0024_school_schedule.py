"""Keep real school-day schedules without changing existing homework or tasks."""
from alembic import op
import sqlalchemy as sa

revision='0024'
down_revision='0023'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('school_schedule_days',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('provider',sa.String(80),nullable=False),
        sa.Column('day',sa.Date(),nullable=False),
        sa.Column('lessons',sa.JSON(),nullable=False),
        sa.Column('content_hash',sa.String(64),nullable=False),
        sa.Column('source',sa.String(200),nullable=False),
        sa.Column('synced_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.UniqueConstraint('provider','day'))
    op.create_index('ix_school_schedule_days_day','school_schedule_days',['day'])

def downgrade():
    op.drop_index('ix_school_schedule_days_day',table_name='school_schedule_days')
    op.drop_table('school_schedule_days')
