"""Гибридный поиск и временные события инструментов; прежние строки сохранены."""
from alembic import op
import sqlalchemy as sa

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('embeddings') as batch:
        batch.add_column(sa.Column('assignment_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_embeddings_assignment', 'assignments', ['assignment_id'], ['id'], ondelete='CASCADE')
        batch.create_index('ix_embeddings_assignment_id', ['assignment_id'])
    op.create_table('tool_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('task_id', sa.String(36), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.String(36), nullable=False),
        sa.Column('claim', sa.String(36), nullable=False),
        sa.Column('type', sa.String(30), nullable=False),
        sa.Column('tool', sa.String(100), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('step', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_tool_events_task_id', 'tool_events', ['task_id'])
    op.create_index('ix_tool_events_created_at', 'tool_events', ['created_at'])
    if op.get_bind().dialect.name == 'sqlite':
        for table, columns in [('memories', ['content']), ('assignments', ['subject', 'title'])]:
            index = table + '_fts'
            names = ', '.join(['id', *columns])
            new = ', '.join('new.' + name for name in ['id', *columns])
            op.execute(f"CREATE VIRTUAL TABLE {index} USING fts5(id UNINDEXED, {', '.join(columns)}, tokenize='unicode61')")
            op.execute(f'INSERT INTO {index} ({names}) SELECT {names} FROM {table}')
            op.execute(f'CREATE TRIGGER {index}_insert AFTER INSERT ON {table} BEGIN INSERT INTO {index} ({names}) VALUES ({new}); END')
            op.execute(f'CREATE TRIGGER {index}_delete AFTER DELETE ON {table} BEGIN DELETE FROM {index} WHERE id=old.id; END')
            op.execute(f'CREATE TRIGGER {index}_update AFTER UPDATE ON {table} BEGIN DELETE FROM {index} WHERE id=old.id; INSERT INTO {index} ({names}) VALUES ({new}); END')


def downgrade():
    if op.get_bind().dialect.name == 'sqlite':
        for index in ['memories_fts', 'assignments_fts']:
            for kind in ['insert', 'delete', 'update']:
                op.execute(f'DROP TRIGGER {index}_{kind}')
            op.execute(f'DROP TABLE {index}')
    op.drop_table('tool_events')
    with op.batch_alter_table('embeddings') as batch:
        batch.drop_index('ix_embeddings_assignment_id')
        batch.drop_constraint('fk_embeddings_assignment', type_='foreignkey')
        batch.drop_column('assignment_id')
