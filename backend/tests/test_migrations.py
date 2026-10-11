import uuid
import json
from datetime import datetime, timezone
from alembic import command
from alembic.config import Config
from sqlalchemy import text, bindparam, JSON, MetaData, Table, Column, String, Integer, DateTime, Index
from atlas.db import engine, sessions
from atlas.models import Task, Message


def test_upgrade_preserves_existing_stage_one_data(client):
    # The client fixture uses a dedicated DB/schema; no application data is touched.
    command.downgrade(Config('alembic.ini'), '0001')
    cid, mid, tid = (str(uuid.uuid4()) for _ in range(3))
    with engine().begin() as conn:
        conn.execute(text("INSERT INTO conversations(id,title,created_at) VALUES (:id,'Legacy chat',CURRENT_TIMESTAMP)"), {'id': cid})
        conn.execute(text("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES (:id,:cid,'user','Legacy message',CURRENT_TIMESTAMP)"), {'id': mid, 'cid': cid})
        stmt = text("INSERT INTO tasks(id,conversation_id,request_key,goal,status,plan,created_at,updated_at) VALUES (:id,:cid,:key,'Legacy goal','PENDING',:plan,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)").bindparams(bindparam('plan', type_=JSON))
        conn.execute(stmt, {'id': tid, 'cid': cid, 'key': str(uuid.uuid4()), 'plan': []})
    command.upgrade(Config('alembic.ini'), 'head')
    with sessions()() as session:
        task = session.get(Task, tid)
        assert task.kind == 'chat' and task.current_step == 0 and not task.dry_run
        assert task.goal == 'Legacy goal'
        assert session.get(Message, mid).content == 'Legacy message'
    command.check(Config('alembic.ini'))


def test_speech_budget_migration_preserves_preexisting_usage_table(client):
    config = Config('alembic.ini')
    command.downgrade(config, '0010')
    metadata = MetaData()
    usage = Table(
        'speech_usage', metadata,
        Column('id', String(36), primary_key=True, nullable=False),
        Column('provider', String(100), nullable=False),
        Column('status', String(30), nullable=False),
        Column('duration_seconds', Integer, nullable=False),
        Column('cost_kopecks', Integer, nullable=False),
        Column('created_at', DateTime(timezone=True), nullable=False),
    )
    Index('ix_speech_usage_created_at', usage.c.created_at)
    metadata.create_all(engine())
    usage_id = str(uuid.uuid4())
    with engine().begin() as conn:
        conn.execute(usage.insert().values(
            id=usage_id,
            provider='test-provider',
            status='SUCCEEDED',
            duration_seconds=1,
            cost_kopecks=1,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))

    command.upgrade(config, 'head')

    with engine().begin() as conn:
        row = conn.execute(text('SELECT provider, status, cost_kopecks FROM speech_usage WHERE id=:id'), {'id': usage_id}).one()
        columns = {column['name'] for column in __import__('sqlalchemy').inspect(conn).get_columns('budget')}
    assert row == ('test-provider', 'SUCCEEDED', 1)
    assert {'speech_rub_month_limit', 'speech_rub_day_limit'} <= columns


def test_public_schedule_migration_preserves_existing_connector(client):
    config = Config('alembic.ini')
    command.downgrade(config, '0019')
    recipe = {'rows': 'article.card', 'key': 'id', 'fields': {'id': '.id', 'title': 'h2'}}
    with engine().begin() as conn:
        stmt = text("INSERT INTO connector_sources(id,name,origin,recipe,enabled,created_at) VALUES (:id,:name,:origin,:recipe,1,CURRENT_TIMESTAMP)").bindparams(bindparam('recipe', type_=JSON))
        conn.execute(stmt, {'id': 'old-source', 'name': 'Старый', 'origin': 'https://example.org/', 'recipe': recipe})
    command.upgrade(config, 'head')
    with engine().begin() as conn:
        row = conn.execute(text('SELECT name,origin,recipe,schedule_minutes,next_run_at FROM connector_sources WHERE id=:id'), {'id': 'old-source'}).one()
    assert row.name == 'Старый' and row.origin == 'https://example.org/'
    assert row.schedule_minutes is None and row.next_run_at is None


def test_permission_migration_revokes_legacy_connector_grant_and_schedule(client):
    config=Config('alembic.ini')
    command.downgrade(config,'0020')
    recipe={'rows':'article.card','key':'id','fields':{'id':'.id','title':'h2'}}
    with engine().begin() as conn:
        stmt=text("INSERT INTO connector_sources(id,name,origin,recipe,enabled,created_at,schedule_minutes,next_run_at) VALUES (:id,:name,:origin,:recipe,1,CURRENT_TIMESTAMP,60,CURRENT_TIMESTAMP)").bindparams(bindparam('recipe',type_=JSON))
        conn.execute(stmt,{'id':'legacy-source','name':'Старый','origin':'https://example.org/','recipe':recipe})
        conn.execute(text("INSERT INTO tool_grants(tool,allowed) VALUES ('connector.legacy-source.read',1)"))
    command.upgrade(config,'head')
    with engine().begin() as conn:
        source=conn.execute(text("SELECT name,schedule_minutes,schedule_fingerprint,schedule_expires_at FROM connector_sources WHERE id='legacy-source'")).one()
        grant=conn.execute(text("SELECT allowed,scope_fingerprint,expires_at FROM tool_grants WHERE tool='connector.legacy-source.read'")).one()
    assert source.name=='Старый' and source.schedule_minutes==60
    assert source.schedule_fingerprint is None and source.schedule_expires_at is None
    assert not grant.allowed and grant.scope_fingerprint is None and grant.expires_at is None


def test_authenticated_action_migration_keeps_old_connector_data(client):
    config=Config('alembic.ini')
    command.downgrade(config,'0021')
    recipe={'rows':'article.card','key':'id','fields':{'id':'.id','title':'h2'}}
    with engine().begin() as conn:
        stmt=text("INSERT INTO connector_sources(id,name,origin,recipe,enabled,created_at) VALUES (:id,:name,:origin,:recipe,1,CURRENT_TIMESTAMP)").bindparams(bindparam('recipe',type_=JSON))
        conn.execute(stmt,{'id':'before-session','name':'Существующий','origin':'https://example.org/','recipe':recipe})
    command.upgrade(config,'head')
    with engine().begin() as conn:
        row=conn.execute(text("SELECT name,recipe,session_ciphertext,session_expires_at FROM connector_sources WHERE id='before-session'")).one()
        action_count=conn.scalar(text('SELECT COUNT(*) FROM connector_actions'))
    assert row.name=='Существующий' and json.loads(row.recipe)==recipe
    assert row.session_ciphertext is None and row.session_expires_at is None and action_count==0


def test_browser_migration_keeps_cookie_session_and_disables_new_mode(client):
    config=Config('alembic.ini')
    command.downgrade(config,'0022')
    recipe={'rows':'article.card','key':'id','fields':{'id':'.id','title':'h2'}}
    with engine().begin() as conn:
        stmt=text("INSERT INTO connector_sources(id,name,origin,recipe,enabled,created_at,session_ciphertext) VALUES (:id,:name,:origin,:recipe,1,CURRENT_TIMESTAMP,:cipher)").bindparams(bindparam('recipe',type_=JSON))
        conn.execute(stmt,{'id':'old-cookie-source','name':'Существующий','origin':'https://example.org/',
                           'recipe':recipe,'cipher':'encrypted-existing-value'})
    command.upgrade(config,'head')
    with engine().begin() as conn:
        row=conn.execute(text("SELECT session_ciphertext,session_kind,fetch_mode FROM connector_sources WHERE id='old-cookie-source'")).one()
    assert row==('encrypted-existing-value','cookies','http')
