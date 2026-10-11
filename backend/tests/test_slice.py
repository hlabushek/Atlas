from datetime import timedelta
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from atlas.api import app
from atlas.worker import tick
from atlas.db import sessions
from atlas.models import Task, Message, now
from atlas.memory import retrieve

def conversation(c): return c.post('/api/conversations',json={}).json()['id']
def send(c,cid,text='Проект Atlas',key=None):
    return c.post('/api/conversations/'+cid+'/messages',json={'content':text,'request_key':key or str(uuid.uuid4())})

def test_auth_and_csrf(client):
    with TestClient(app) as stranger:
        assert stranger.get('/api/memory').status_code==401
    client.headers.pop('X-Atlas-Client')
    assert client.post('/api/conversations',json={}).status_code==403

def test_chat_persisted_and_shared_memory(client):
    cid=conversation(client)
    client.post('/api/memory',json={'category':'PROJECTS','content':'Проект Atlas использует Python'})
    queued=send(client,cid)
    assert queued.status_code==202
    assert tick()
    history=client.get('/api/conversations/'+cid+'/messages').json()
    assert len(history)==2 and 'использует Python' in history[1]['content']
    with TestClient(app) as other:
        other.post('/api/login',json={'password':'test-owner-password-123'})
        assert other.get('/api/conversations/'+cid+'/messages').json()==history
    second=conversation(client)
    send(client,second)
    tick()
    assert 'использует Python' in client.get('/api/conversations/'+second+'/messages').json()[-1]['content']

def test_deleted_memory_not_retrieved(client):
    m=client.post('/api/memory',json={'content':'Уникальный факт про орбиты'}).json()
    with sessions()() as s: assert len(retrieve(s,'орбиты'))==1
    assert client.delete('/api/memory/'+m['id']).status_code==200
    with sessions()() as s: assert retrieve(s,'орбиты')==[]

def test_edit_and_secret_block(client):
    assert client.post('/api/memory',json={'content':'password=supersecret'}).status_code==422
    m=client.post('/api/memory',json={'content':'Проект версия один'}).json()
    assert client.put('/api/memory/'+m['id'],json={'category':'PROJECTS','content':'Проект версия два'}).status_code==200
    assert 'два' in client.get('/api/memory').json()[0]['content']

def test_request_dedup_and_reply_exactly_once(client):
    cid=conversation(client);key=str(uuid.uuid4())
    a=send(client,cid,key=key);b=send(client,cid,key=key)
    assert a.json()['id']==b.json()['id']
    assert send(client,cid,'Другое',key).status_code==409
    assert tick();assert not tick()
    assert len(client.get('/api/conversations/'+cid+'/messages').json())==2

def test_expired_worker_recovered(client):
    cid=conversation(client);t=send(client,cid).json()
    with sessions()() as s:
        row=s.get(Task,t['id']);row.status='RUNNING';row.claim='old';row.lease_until=now()-timedelta(seconds=1);s.commit()
    assert tick()
    assert client.get('/api/tasks').json()[0]['status']=='COMPLETED'
    assert len(client.get('/api/conversations/'+cid+'/messages').json())==2

def test_cancelled_task_never_published(client):
    cid=conversation(client);t=send(client,cid).json()
    class CancellingRouter:
        def answer(self,*args):
            assert client.post('/api/tasks/'+t['id']+'/cancel').status_code==200
            return 'Should not be published'
    tick(CancellingRouter())
    assert len(client.get('/api/conversations/'+cid+'/messages').json())==1
    assert client.get('/api/tasks').json()[0]['status']=='CANCELLED'

def test_model_failure_does_not_expose_secrets(client):
    cid=conversation(client);send(client,cid)
    class Broken:
        def answer(self,*args): raise RuntimeError('secret-api-key')
    tick(Broken())
    task=client.get('/api/tasks').json()[0]
    assert task['status']=='FAILED'
    assert 'secret-api-key' not in str(task)

def test_export(client):
    conversation(client)
    assert len(client.get('/api/export').json()['conversations'])==1

def test_collection_pagination_and_recent_messages(client):
    first=conversation(client)
    second=conversation(client)
    assert len(client.get('/api/conversations?limit=1').json())==1
    assert len(client.get('/api/conversations?limit=1&offset=1').json())==1
    for index in range(3):
        assert send(client,first,'Сообщение '+str(index)).status_code==202
    recent=client.get('/api/conversations/'+first+'/messages?limit=2').json()
    assert len(recent)==2 and recent[-1]['content']=='Сообщение 2'
    assert len(client.get('/api/memory?limit=1').json())<=1
    assert len(client.get('/api/tasks?limit=1').json())<=1
    assert second

def test_websocket_requires_session_and_same_origin(client):
    from starlette.websockets import WebSocketDisconnect
    import pytest
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/api/events',headers={'origin':'https://evil.example'}):pass
    with client.websocket_connect('/api/events',headers={'origin':'http://testserver'}) as socket:
        assert socket.receive_json()['type']=='tasks'

def test_login_rate_limit(client):
    with TestClient(app) as c:
        for _ in range(5): assert c.post('/api/login',json={'password':'bad'}).status_code==401
        assert c.post('/api/login',json={'password':'bad'}).status_code==429
