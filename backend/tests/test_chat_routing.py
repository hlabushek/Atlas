import json
import uuid
from datetime import timedelta
import httpx
import pytest
from atlas.config import settings
from atlas.db import sessions
from atlas.models import Device, ModelPreferences, Task, now
from atlas.chat_routing import chat_profile
from atlas.router import ModelRouter
from atlas.voice_gateway import submit_voice

def configure(client, enabled=True):
    state=client.get('/api/model-settings').json()
    config={**state['config'],'mode':'configured','auto_chat_routing':enabled,
        'main':{'provider':'routerai','model':'deepseek/deepseek-v4-flash','input_rate':500000,'output_rate':3000000},
        'planner':{'provider':'routerai','model':'deepseek/deepseek-v4.1-flash','input_rate':500000,'output_rate':2000000}}
    result=client.put('/api/model-settings',json={'revision':state['revision'],'config':config})
    assert result.status_code==200,result.text
    return result.json()

@pytest.mark.parametrize('text,role',[
    ('Привет, как дела?','main'),('Что такое нейросеть?','main'),
    ('Проанализируй мой проект','planner'),('Подумай над решением','planner'),
    ('Сравни два подхода','planner'),('Разбери подробно ошибку','planner'),
    ('Объясни подробно устройство нейросети','planner'),
    ('Кратко сравни два подхода','main'),('Быстро ответь, что такое память','main'),
    ('необычный'+ ' текст'*240,'planner'),('я задумался','main')])
def test_explicit_cheap_routing(client,text,role):
    configure(client)
    with sessions()() as s:
        profile=chat_profile(s,text)
        assert profile['role']==role
        assert profile['model']==('deepseek/deepseek-v4-flash' if role=='main' else 'deepseek/deepseek-v4.1-flash')
        assert profile['max_output_tokens']==(512 if role=='main' else 1024)
        assert 'key' not in profile

def test_disabled_and_manual_override(client):
    configure(client,False)
    with sessions()() as s:
        assert chat_profile(s,'Подумай')['role']=='main'
        assert 'chat_mode' not in chat_profile(s,'Подумай')
        assert chat_profile(s,'Привет','planner')['role']=='planner'
    configure(client)
    with sessions()() as s:
        assert chat_profile(s,'Подумай','main')['role']=='main'
        assert chat_profile(s,'Кратко','planner')['role']=='planner'

def test_auto_idempotency_snapshot_and_conflict(client):
    configure(client)
    cid=client.post('/api/conversations',json={}).json()['id']
    body={'content':'Подумай над проектом','request_key':str(uuid.uuid4()),'model_role':'auto'}
    task=client.post(f'/api/conversations/{cid}/messages',json=body).json()
    assert task['model_profile']['role']=='planner'
    configure(client,False)
    retry=client.post(f'/api/conversations/{cid}/messages',json=body)
    assert retry.status_code==202 and retry.json()['model_profile']==task['model_profile']
    assert client.post(f'/api/conversations/{cid}/messages',json={**body,'model_role':'main'}).status_code==409

def test_voice_uses_same_routing_without_new_permissions(client):
    configure(client)
    cid=client.post('/api/conversations',json={}).json()['id']
    with sessions()() as s:
        device=Device(name='Тест',fingerprint=uuid.uuid4().hex*2,enabled=True,roots=[],operations=[],expires_at=now()+timedelta(days=1),voice_enabled=True,voice_conversation_id=cid)
        s.add(device);s.commit()
        for text,role in [('Привет','main'),('Подумай над проектом','planner')]:
            body={'request_id':str(uuid.uuid4()),'text':text,'mode':'chat','store_transcripts':False}
            result=submit_voice(s,device.fingerprint,body)
            task=s.get(Task,result['task_id'])
            assert task.kind=='chat' and task.model_profile['role']==role
            s.get(ModelPreferences,1).config={**s.get(ModelPreferences,1).config,'auto_chat_routing':False};s.commit()
            assert submit_voice(s,device.fingerprint,body)['task_id']==task.id
            s.get(ModelPreferences,1).config={**s.get(ModelPreferences,1).config,'auto_chat_routing':True};s.commit()

def test_fast_payload_and_careful_voice_remain_safe(client,monkeypatch):
    configure(client)
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','fixture-router-key-123');settings.cache_clear()
    calls=[]
    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'Короткий ответ.'}}],'usage':{'prompt_tokens':10,'total_tokens':20}})
    original=httpx.Client
    monkeypatch.setattr('atlas.router.httpx.Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    for text in ('Привет','Подумай над проектом'):
        with sessions()() as s:profile=chat_profile(s,text)
        profile['response_style']='voice_brief'
        chunks=[]
        assert ModelRouter().answer(text,[],[],profile=profile,on_fragment=chunks.append)=='Короткий ответ.'
        assert ''.join(chunks)=='Короткий ответ.'
        assert 'не более 60 слов' in calls[-1]['messages'][0]['content']
    assert calls[0]['model']=='deepseek/deepseek-v4-flash' and calls[0]['reasoning']=={'enabled':False}
    assert calls[0]['max_tokens']==512
    assert calls[1]['model']=='deepseek/deepseek-v4.1-flash' and 'reasoning' not in calls[1]
    assert 'fixture-router-key' not in json.dumps(calls)
