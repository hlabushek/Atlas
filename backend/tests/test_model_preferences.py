import json,uuid
import httpx,pytest
from sqlalchemy import select
from atlas.config import settings
from atlas.db import sessions
from atlas.models import Task,Embedding,ModelUsage
from atlas.model_settings import resolve,Preferences,ModelUnavailable
from atlas.router import ModelRouter
from atlas.worker import tick

def configure(client,**changes):
    old=client.get('/api/model-settings').json()
    config={**old['config'],'mode':'configured',**changes}
    r=client.put('/api/model-settings',json={'revision':old['revision'],'config':config})
    assert r.status_code==200,r.text
    return r.json()

def test_defaults_persistent_conflict_and_no_keys(client,monkeypatch):
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','fixture-key-for-router-123');settings.cache_clear()
    initial=client.get('/api/model-settings').json()
    assert initial['config']['main']['model']=='deepseek/deepseek-v4.1-flash'
    assert initial['config']['planner']['model']=='anthropic/claude-sonnet-5.5'
    assert initial['config']['embedding']['model']=='qwen/qwen3-embedding-8b'
    saved=configure(client)
    assert saved['effective']['main']['ready']
    assert 'fixture-key' not in json.dumps(saved)
    assert 'fixture-key' not in client.get('/api/export').text
    assert client.put('/api/model-settings',json={'revision':initial['revision'],'config':initial['config']}).status_code==409
    with sessions()() as s:assert resolve('planner',s).model=='anthropic/claude-sonnet-5.5'
    client.cookies.clear()
    assert client.get('/api/model-settings').status_code==401
    assert client.get('/api/model-catalog/routerai').status_code==401

def test_no_key_fails_honestly_without_http_or_budget(client,monkeypatch):
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','');settings.cache_clear()
    configure(client)
    with pytest.raises(ModelUnavailable):ModelRouter().answer('Привет',[],[])
    assert client.get('/api/budget').json()['tokens']==0
    cid=client.post('/api/conversations',json={}).json()['id']
    task=client.post('/api/conversations/'+cid+'/messages',json={'content':'Привет','request_key':str(uuid.uuid4())}).json()
    assert tick()
    result=client.get('/api/tasks/'+task['id']).json()
    assert result['status']=='FAILED' and 'ATLAS_ROUTERAI_API_KEY' in result['error']
    assert 'Демо' not in (result['result'] or '')

def test_role_selection_and_task_snapshot_survive_settings_change(client,monkeypatch):
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','fixture-key-for-router-123');settings.cache_clear()
    configure(client,embeddings_enabled=False)
    cid=client.post('/api/conversations',json={}).json()['id']
    body={'content':'Реши задачу','request_key':str(uuid.uuid4()),'model_role':'planner'}
    task=client.post('/api/conversations/'+cid+'/messages',json=body).json()
    configure(client,planner={'provider':'routerai','model':'replacement-model','input_rate':0,'output_rate':0})
    assert client.post('/api/conversations/'+cid+'/messages',json=body).json()['id']==task['id']
    assert client.post('/api/conversations/'+cid+'/messages',json={**body,'model_role':'main'}).status_code==409
    calls=[]
    def handler(req):
        calls.append(json.loads(req.content));assert req.headers['authorization']=='Bearer fixture-key-for-router-123'
        return httpx.Response(200,json={'choices':[{'message':{'content':'Проверенный ответ'}}],'usage':{'prompt_tokens':10,'total_tokens':20}})
    original=httpx.Client
    monkeypatch.setattr('atlas.router.httpx.Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    assert tick()
    assert calls[0]['model']=='anthropic/claude-sonnet-5.5'
    assert client.get('/api/tasks/'+task['id']).json()['status']=='COMPLETED'
    assert ModelRouter().answer('Новый запрос',[],[])=='Проверенный ответ'
    assert calls[1]['model']=='deepseek/deepseek-v4.1-flash'

def test_new_plans_use_planner_profile(client,monkeypatch):
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','fixture-key-for-router-123');settings.cache_clear()
    configure(client,embeddings_enabled=False)
    cid=client.post('/api/conversations',json={}).json()['id']
    task=client.post('/api/tasks',json={'conversation_id':cid,'request_key':str(uuid.uuid4()),'goal':'Поиск сведений'}).json()
    configure(client,planner={'provider':'routerai','model':'replacement-model'})
    calls=[]
    def handler(req):
        calls.append(json.loads(req.content)['model'])
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps([{'tool':'memory.search','arguments':{'query':'Atlas'}}])}}]})
    original=httpx.Client;monkeypatch.setattr('atlas.router.httpx.Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    assert tick();assert calls==['anthropic/claude-sonnet-5.5']
    assert client.get('/api/tasks/'+task['id']).json()['status']=='COMPLETED'

def test_embedding_switch_cannot_mix_namespaces(client,monkeypatch):
    from atlas.embeddings import index_one,warm,key
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','fixture-key-for-router-123');settings.cache_clear()
    configure(client)
    mid=client.post('/api/memory',json={'content':'тестовая память'}).json()['id']
    def handler(req):return httpx.Response(200,json={'data':[{'embedding':[1.0]+[0.0]*767}],'usage':{'prompt_tokens':2,'total_tokens':2}})
    original=httpx.Client;monkeypatch.setattr('atlas.embeddings.httpx.Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    assert index_one();warm('синоним')
    oldkey=key('синоним');oldidentity=resolve('embedding').identity
    configure(client,embedding={'provider':'routerai','model':'different-embedding'})
    assert resolve('embedding').identity!=oldidentity and key('синоним')!=oldkey
    with sessions()() as s:
        from atlas.embeddings import ranked
        assert ranked(s,'синоним')==[]
    assert index_one();warm('синоним')
    with sessions()() as s:
        assert s.scalar(select(Embedding).where(Embedding.memory_id==mid)).model==resolve('embedding').identity

def test_credentials_isolated_and_secret_validation(client,monkeypatch):
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','router-key-private-123');monkeypatch.setenv('ATLAS_PLUSVIBE_API_KEY','plus-key-private-123');settings.cache_clear()
    configure(client,main={'provider':'plusvibe','model':'custom-model'})
    r=resolve();assert r.url=='https://plusvibeapi.ru/v1' and r.key=='plus-key-private-123'
    assert 'plus-key-private-123' not in repr(r)
    old=client.get('/api/model-settings').json();old['config']['main']['model']='router-key-private-123'
    assert client.put('/api/model-settings',json={'revision':old['revision'],'config':old['config']}).status_code==422
    old['config']['main']['model']='good-model';old['config']['main']['api_key']='never-store-this'
    response=client.put('/api/model-settings',json={'revision':old['revision'],'config':old['config']})
    assert response.status_code==422 and 'never-store-this' not in response.text

def test_per_model_rates_preserved_during_provider_request(client,monkeypatch):
    from atlas.budget import request
    client.put('/api/budget',json={'token_limit':100000,'input_rate':99,'output_rate':99,'cost_limit':10000})
    def handler(req):return httpx.Response(200,json={'usage':{'prompt_tokens':2,'total_tokens':5}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:request(c,'https://fixture.example',{}, {'input':'text'},'chat','fixture',output=10,rates=(1000000,2000000))
    assert client.get('/api/budget').json()['cost']==8
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(ValueError):request(c,'https://fixture.example',{}, {'input':'text'},'chat','fixture',rates=(0,0))
