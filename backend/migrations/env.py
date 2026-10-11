from alembic import context
from atlas.db import engine
from atlas.models import Base
config = context.config
from pgvector.sqlalchemy import VECTOR

def compare_type(context,column,metadata_column,reflected,declared):
    # public.vector and vector resolve to the same extension type; dimensions still matter.
    if isinstance(reflected,VECTOR) and isinstance(declared,VECTOR):return reflected.dim!=declared.dim
    return None

def include_object(obj, name, type_, reflected, compare_to):
    # SQLite FTS5 virtual/shadow tables are managed explicitly by migration 0025.
    return not (type_ == 'table' and (name.startswith('memories_fts') or name.startswith('assignments_fts')))
if context.is_offline_mode():
    from atlas.config import settings
    context.configure(url=settings().database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=compare_type, include_object=include_object)
        with context.begin_transaction(): context.run_migrations()
