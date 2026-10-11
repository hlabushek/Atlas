from datetime import timedelta

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text, bindparam, JSON

from atlas.config import settings
from atlas.db import sessions, engine
from atlas.diary import retrieve as diary_retrieve
from atlas.embeddings import digest, index_one, warm
from atlas.hybrid_search import rrf
from atlas.memory import retrieve
from atlas.models import Assignment, Embedding, Memory, MemoryFact, now


def assignment(title, provider='school.mos.ru'):
    return Assignment(provider=provider, external_id=title, subject='Физика', title=title,
                      due_at=now()+timedelta(days=1), source='test://diary', content_hash=digest(title))


def test_rrf_combines_ranks_deduplicates_and_breaks_ties():
    assert rrf(['a','b','c'], ['c','b','d'])[:2] == ['c','b']
    assert rrf(['a','a','b'], ['b']) == ['b','a']
    assert rrf(['z'], ['a']) == ['a','z']
    assert rrf([],[]) == []


def test_fts_unicode_literal_queries_and_transactional_triggers(client):
    with sessions()() as session:
        row=Memory(category='KNOWLEDGE', content='Ёлка и МАРС: план полета')
        session.add(row);session.commit();mid=row.id
        assert retrieve(session,'марс')[0].id==mid
        assert retrieve(session,'план OR " ; --')[0].id==mid
        assert retrieve(session,'полет')==[]  # Full tokens, not arbitrary substrings.
        row.content='Венера';session.commit()
        assert retrieve(session,'марс')==[] and retrieve(session,'венера')[0].id==mid
        row.content='Юпитер';session.flush()
        assert retrieve(session,'юпитер')[0].id==mid
        session.rollback()
        assert retrieve(session,'юпитер')==[] and retrieve(session,'венера')[0].id==mid
        session.delete(row);session.commit()
        assert retrieve(session,'венера')==[]


def test_inactive_memory_and_pagination(client,monkeypatch):
    ids=[]
    for value in ('a','b','c'):
        ids.append(client.post('/api/memory',json={'content':'проект '+value}).json()['id'])
    monkeypatch.setattr('atlas.embeddings.ranked',lambda s,q:ids[::-1])
    with sessions()() as session:
        session.add(MemoryFact(memory_id=ids[1],key='test',value='old',active=False));session.commit()
        rows=retrieve(session,'проект',100)
        assert set(row.id for row in rows)=={ids[0],ids[2]}
        assert retrieve(session,'проект',1,1)[0].id==rows[1].id
        assert retrieve(session,'',10)==[]
        assert retrieve(session,'test-owner-password-123')==[]


def test_real_cosine_and_rrf_for_memory_and_diary(client,monkeypatch):
    monkeypatch.setenv('ATLAS_EMBEDDING_URL','https://fixture.invalid')
    monkeypatch.setenv('ATLAS_EMBEDDING_MODEL','test-768');settings.cache_clear()
    def handler(request):
        import json
        value=json.loads(request.content)['input']
        vector=[0.0]*768
        vector[0 if ('велосипед' in value or value=='транспорт') else 1]=1.0
        return httpx.Response(200,json={'data':[{'embedding':vector}],'usage':{'total_tokens':2}})
    original=httpx.Client
    monkeypatch.setattr('atlas.embeddings.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with sessions()() as session:
        memory=Memory(content='велосипед',category='KNOWLEDGE')
        matched=assignment('велосипед транспорт')
        other=assignment('ракета')
        session.add_all([memory,matched,other]);session.commit()
        mid,aid=memory.id,matched.id
    assert index_one() and index_one() and index_one() and not index_one()
    # A previously unseen owner search warms the query through the existing budget.
    api_result=client.get('/api/diary/search?q=транспорт').json()
    assert api_result['query_vector_cached'] is True
    assert api_result['assignments'][0]['id']==aid
    warm('транспорт')
    with sessions()() as session:
        assert retrieve(session,'транспорт')[0].id==mid
        assert diary_retrieve(session,'транспорт')[0].id==aid
        assert {r.id for r in diary_retrieve(session,'транспорт')}=={matched.id,other.id}
        assert len(session.scalars(select(Embedding).where(Embedding.assignment_id.is_not(None))).all())==2
        row=session.get(Assignment,aid);row.title='катер';session.commit()
        assert aid not in [r.id for r in diary_retrieve(session,'транспорт')]
    assert index_one() and not index_one()


def test_diary_api_search_respects_provider_edit_and_auth(client):
    with sessions()() as session:
        a,b=assignment('Закон инерции'),assignment('Закон инерции демо','demo')
        session.add_all([a,b]);session.commit();aid=a.id
    result=client.get('/api/diary/search?q=ИНЕРЦИИ&provider=school.mos.ru').json()
    assert [r['id'] for r in result['assignments']]==[aid]
    assert result['query_vector_cached'] is False
    assert len(client.get('/api/diary/search?q=инерции&limit=1&offset=1').json()['assignments'])==1
    with sessions()() as session:
        row=session.get(Assignment,aid);row.title='Оптика';session.commit()
    assert client.get('/api/diary/search?q=инерции&provider=school.mos.ru').json()['assignments']==[]
    assert client.get('/api/diary/search?q=оптика').json()['assignments'][0]['id']==aid
    client.cookies.clear()
    assert client.get('/api/diary/search?q=оптика').status_code==401


def test_upgrade_backfills_fts_and_preserves_existing_rows(client):
    config=Config('alembic.ini')
    command.downgrade(config,'0024')
    with sessions()() as session:
        row=Memory(content='Существующая память',category='KNOWLEDGE')
        school=assignment('Существующее задание')
        # ORM includes new column only on Embedding, not these legacy models.
        session.add_all([row,school]);session.commit();mid,aid=row.id,school.id
    with engine().connect() as connection:
        before=connection.execute(text('SELECT * FROM memories')).all()
    with engine().begin() as connection:
        statement=text('INSERT INTO embeddings(key,memory_id,model,digest,vector,created_at) '
                       'VALUES (:key,:mid,:model,:digest,:vector,CURRENT_TIMESTAMP)').bindparams(bindparam('vector',type_=JSON))
        connection.execute(statement,{'key':'e'*64,'mid':mid,'model':'legacy-768',
                                      'digest':digest('Существующая память'),'vector':[1.0]+[0.0]*767})
        embedding_before=connection.execute(text('SELECT key,memory_id,model,digest,vector,created_at FROM embeddings')).all()
    command.upgrade(config,'head')
    with sessions()() as session:
        assert retrieve(session,'память')[0].id==mid
        assert diary_retrieve(session,'задание')[0].id==aid
    with engine().connect() as connection:
        assert connection.execute(text('SELECT * FROM memories')).all()==before
        assert connection.execute(text('SELECT key,memory_id,model,digest,vector,created_at FROM embeddings')).all()==embedding_before
        assert connection.scalar(text('SELECT assignment_id FROM embeddings WHERE key=:key'),{'key':'e'*64}) is None
    command.check(config)
