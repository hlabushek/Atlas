import uuid
from atlas.db import sessions
from atlas.models import Message, Device, now
from datetime import timedelta


def create(client,title='Чат проекта'):
    return client.post('/api/conversations',json={'title':title}).json()


def change(client,row,**fields):
    return client.patch('/api/conversations/'+row['id'],json={'revision':row['revision'],**fields})


def test_rename_description_pin_search_and_conflict(client):
    old=create(client)
    row=change(client,old,title='Учёба',description='Подготовка',pinned=True).json()
    assert row['revision']==2 and row['description']=='Подготовка' and row['pinned']
    assert change(client,old,title='Затирание').status_code==409
    create(client,'Новый')
    assert client.get('/api/conversations').json()[0]['id']==old['id']
    assert [c['id'] for c in client.get('/api/conversations?q=Учёба').json()]==[old['id']]
    assert [c['id'] for c in client.get('/api/conversations?q=Подготовка').json()]==[old['id']]
    assert client.get('/api/conversations/'+old['id']).json()['revision']==2
    assert client.get('/api/conversations?q=%').json()==[]
    assert client.patch('/api/conversations/missing',json={'revision':1,'title':'Нет'}).status_code==404
    for title in [' ','token=abcdefghijklmnopqrstuvwxyz']:
        assert change(client,row,title=title).status_code==422


def test_archive_trash_restore_preserves_history(client):
    row=create(client)
    with sessions()() as s:
        s.add(Message(conversation_id=row['id'],role='user',content='Сохранить историю'));s.commit()
    row=change(client,row,action='archive').json()
    assert client.get('/api/conversations').json()==[]
    assert client.get('/api/conversations?scope=archive').json()[0]['id']==row['id']
    assert client.post('/api/conversations/'+row['id']+'/messages',json={'content':'Нельзя','request_key':str(uuid.uuid4())}).status_code==409
    row=change(client,row,action='trash').json()
    assert row['trashed_at']
    assert client.get('/api/conversations?scope=archive').json()==[]
    assert client.get('/api/conversations?scope=trash').json()[0]['id']==row['id']
    row=change(client,row,action='restore').json()
    assert not row['archived'] and not row['trashed_at']
    assert client.get('/api/conversations/'+row['id']+'/messages').json()[0]['content']=='Сохранить историю'


def test_active_task_and_channel_protected(client):
    row=create(client)
    task=client.post('/api/conversations/'+row['id']+'/messages',json={'content':'Тест','request_key':str(uuid.uuid4())}).json()
    assert change(client,row,action='trash').status_code==409
    client.post('/api/tasks/'+task['id']+'/cancel')
    with sessions()() as s:
        s.add(Device(name='ПК',fingerprint='a'*64,enabled=True,voice_enabled=True,voice_conversation_id=row['id'],roots=[],operations=[],expires_at=now()+timedelta(days=1)));s.commit()
    assert change(client,row,action='archive').status_code==409
    assert change(client,row,title='Голосовой чат').status_code==200


def test_auth_csrf_and_export(client):
    row=create(client)
    assert 'revision' in client.get('/api/export').json()['conversations'][0]
    del client.headers['X-Atlas-Client']
    assert change(client,row,pinned=True).status_code==403
    client.cookies.clear()
    assert change(client,row,action='trash').status_code==401
