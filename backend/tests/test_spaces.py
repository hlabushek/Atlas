import uuid
from atlas.db import sessions
from atlas.models import Task, ToolCall, Note, MemoryFact
from atlas.spaces import context
from atlas.worker import tick


def create(client, name='Учёба'):
    response = client.post('/api/spaces', json={'name': name, 'description': 'Подготовка к экзамену'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def conversation(client):
    return client.post('/api/conversations', json={'title': 'Разговор проекта'}).json()['id']


def test_spaces_owner_and_csrf(client):
    sid = create(client)
    del client.headers['X-Atlas-Client']
    assert client.put('/api/spaces/'+sid, json={'name':'Новое'}).status_code == 403
    client.cookies.clear()
    for method, path, body in [('GET','',None),('POST','',{'name':'Нет'}),('GET','/'+sid,None),
                              ('PUT','/'+sid,{'name':'Нет'}),('PUT',f'/{sid}/memory/m',None),
                              ('DELETE',f'/{sid}/memory/m',None),('PUT',f'/{sid}/conversations/c',None),
                              ('DELETE',f'/{sid}/conversations/c',None)]:
        assert client.request(method, '/api/spaces'+path, json=body).status_code == 401


def test_links_archive_and_existing_data_preserved(client):
    sid, other, cid = create(client), create(client,'Другой'), conversation(client)
    path = f'/api/spaces/{sid}/conversations/{cid}'
    assert client.put(path).status_code == 200
    assert client.put(path).status_code == 200
    assert client.put(f'/api/spaces/{other}/conversations/{cid}').status_code == 409
    row=client.get('/api/spaces/'+sid).json()
    assert [c['id'] for c in row['conversations']] == [cid]
    assert client.put('/api/spaces/'+sid,json={'name':'Учёба','archived':True}).status_code == 200
    assert client.get('/api/spaces/'+sid).json()['conversations'][0]['id'] == cid
    assert client.put(path).status_code == 409
    assert client.delete(path).status_code == 200
    assert client.get(f'/api/conversations/{cid}/messages').status_code == 200
    assert client.put(f'/api/spaces/{other}/conversations/{cid}').status_code == 200
    assert client.put('/api/spaces/'+sid,json={'name':'Учёба','archived':False}).status_code == 200
    events = [a['event'] for a in client.get('/api/audit').json()]
    assert 'space_created' in events and 'space_conversation_detached' in events


def test_memory_limit_delete_and_snapshot(client):
    sid, cid = create(client), conversation(client)
    client.put(f'/api/spaces/{sid}/conversations/{cid}')
    ids=[client.post('/api/memory',json={'content':f'Факт проекта номер {i}'}).json()['id'] for i in range(6)]
    for mid in ids[:5]:
        assert client.put(f'/api/spaces/{sid}/memory/{mid}').status_code == 200
    assert client.put(f'/api/spaces/{sid}/memory/{ids[5]}').status_code == 409
    assert client.put(f'/api/spaces/{sid}/memory/{ids[0]}').status_code == 200
    body={'content':'Привет','request_key':str(uuid.uuid4())}
    task=client.post(f'/api/conversations/{cid}/messages',json=body).json()
    assert set(task['model_profile']['space_memory_ids']) == set(ids[:5])
    client.delete(f'/api/spaces/{sid}/memory/{ids[0]}')
    assert client.post(f'/api/conversations/{cid}/messages',json=body).json()['model_profile']==task['model_profile']
    with sessions()() as s:
        assert ids[0] not in [m.id for m in context(s,cid,ids)]
        s.add(MemoryFact(memory_id=ids[1],key='test',value='test',active=False));s.commit()
        assert ids[1] not in [m.id for m in context(s,cid,ids)]
    assert client.delete('/api/memory/'+ids[2]).status_code == 200
    assert ids[2] not in [m['id'] for m in client.get('/api/spaces/'+sid).json()['memories']]
    client.put('/api/spaces/'+sid,json={'name':'Учёба','archived':True})
    with sessions()() as s:
        assert context(s,cid,ids)==[]


def test_project_context_reaches_real_worker_without_grants(client):
    sid,cid=create(client),conversation(client)
    mid=client.post('/api/memory',json={'content':'Любимый цвет — индиго'}).json()['id']
    client.put(f'/api/spaces/{sid}/conversations/{cid}')
    client.put(f'/api/spaces/{sid}/memory/{mid}')
    task=client.post(f'/api/conversations/{cid}/messages',json={'content':'Привет','request_key':str(uuid.uuid4())}).json()
    class Router:
        def answer(self,goal,history,memories):
            assert 'Любимый цвет — индиго' in memories
            return 'Ответ с контекстом'
    assert tick(Router())
    assert client.get('/api/tasks/'+task['id']).json()['status']=='COMPLETED'
    assert client.get('/api/spaces/'+sid).json()['tasks'][0]['id']==task['id']


def test_notes_and_tasks_are_scoped_to_conversations(client):
    sid,other,cid,foreign=create(client),create(client,'Личный'),conversation(client),conversation(client)
    client.put(f'/api/spaces/{sid}/conversations/{cid}')
    client.put(f'/api/spaces/{other}/conversations/{foreign}')
    with sessions()() as s:
        for c,title in [(cid,'Заметка проекта'),(foreign,'Другая заметка')]:
            task=Task(conversation_id=c,request_key=str(uuid.uuid4()),goal=title,status='COMPLETED')
            s.add(task);s.flush()
            call=ToolCall(task_id=task.id,step=0,tool='notes.create',arguments={},status='SUCCEEDED')
            s.add(call);s.flush();s.add(Note(call_id=call.id,title=title,content='Результат'))
        s.commit()
    row=client.get('/api/spaces/'+sid).json()
    assert [n['title'] for n in row['notes']]==['Заметка проекта']
    assert [t['goal'] for t in row['tasks']]==['Заметка проекта']
    exported=client.get('/api/export').json()
    assert len(exported['spaces'])==2 and len(exported['space_conversations'])==2


def test_validation_missing_targets_and_secrets(client):
    for name in [' ','token=abcdefghijklmnopqrstuvwxyz','x'*101]:
        assert client.post('/api/spaces',json={'name':name}).status_code == 422
    sid=create(client)
    assert client.get('/api/spaces/missing').status_code==404
    assert client.put(f'/api/spaces/{sid}/conversations/missing').status_code==404
    assert client.put(f'/api/spaces/{sid}/memory/missing').status_code==404
    assert client.get('/api/spaces?limit=201').status_code==422
