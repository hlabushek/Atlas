import pytest
from datetime import datetime,timezone,timedelta
from sqlalchemy import select
from atlas.db import sessions
from atlas.models import Event
from atlas.skills import DIARY
from atlas.network import endpoint
from atlas.worker import tick
from test_agent import create,approve,detail


def installed(client):
    response=client.post('/api/skills',json=DIARY)
    assert response.status_code==200,response.text
    assert client.put('/api/skills/diary-demo',json={'enabled':True}).status_code==200
    for name in ['diary.sync','diary.homework','diary.schedule','diary.study_plan']:
        assert client.put('/api/tools/'+name+'/permission',json={'allowed':True}).status_code==200

def synchronize(client):
    task=create(client,[{'tool':'diary.sync','arguments':{}}])
    tick();approve(client);tick()
    assert detail(client,task)['status']=='COMPLETED'
    return detail(client,task)

def test_demo_diary_tracks_changes_and_deduplicates(client):
    installed(client);first=synchronize(client)
    assert first['calls'][0]['result']['new']==1
    assert len(client.get('/api/diary').json()['tasks'])==1
    synchronize(client)
    with sessions()() as s:assert len(s.scalars(select(Event)).all())==1
    item={'external_id':'math-1','subject':'Математика (демо)','title':'Новый вариант задания','due_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'grade':'5'}
    assert client.post('/api/diary/demo/homework',json=item).status_code==200
    changed=synchronize(client)
    assert changed['calls'][0]['result']['changed']==1
    state=client.get('/api/diary').json()
    assert len(state['assignments'])==1 and len(state['tasks'])==1
    assert state['assignments'][0]['source']=='demo://atlas-diary'
    with sessions()() as s:assert s.scalars(select(Event).where(Event.kind=='diary_changed')).first()
    assert client.get('/api/skills/diary-demo/history').json()

def test_disable_and_uninstall_revoke_tools(client):
    installed(client)
    assert client.put('/api/skills/diary-demo',json={'enabled':False}).status_code==200
    assert not any(t['name'].startswith('diary.') for t in client.get('/api/tools').json())
    assert client.delete('/api/skills/diary-demo').status_code==200
    assert client.get('/api/skills').json()==[]
    assert len(client.get('/api/diary').json()['demo_homework'])==1

def test_manifest_cannot_install_code_or_unlisted_network(client):
    bad={**DIARY,'script':'os.system(...)'}
    assert client.post('/api/skills',json=bad).status_code==422
    bad={'id':'remote','version':'1.0.0','description':'unsafe','kind':'rest','endpoint':'http://169.254.169.254/latest'}
    assert client.post('/api/skills',json=bad).status_code==422

def test_remote_http_endpoint_cannot_send_owner_credentials(monkeypatch):
    from atlas.config import settings
    monkeypatch.setenv('ATLAS_INTEGRATION_HOSTS','trusted.example,127.0.0.1')
    settings.cache_clear()
    with pytest.raises(ValueError,match='HTTPS'):
        endpoint('http://trusted.example/api')
    assert endpoint('http://127.0.0.1:8080/api')=='http://127.0.0.1:8080/api'

def test_diary_task_permission_required(client):
    client.post('/api/skills',json=DIARY);client.put('/api/skills/diary-demo',json={'enabled':True})
    task=create(client,[{'tool':'diary.sync','arguments':{}}]);tick()
    assert detail(client,task)['status']=='FAILED'
    assert client.get('/api/diary').json()['assignments']==[]

def test_diary_event_generates_single_initiative(client,monkeypatch):
    from test_notifications import prefs
    from atlas.notifications import scheduler_tick
    from atlas import initiative_decision
    monkeypatch.setattr(initiative_decision,'evaluate',lambda state,runtime:{
        'approved':True,'confidence':0.95,'model':'test-decision','context_hash':'a'*64})
    installed(client);prefs(client);synchronize(client)
    scheduler_tick();scheduler_tick()
    assert len([i for i in client.get('/api/initiatives').json() if i['reason']=='Изменение в дневнике'])==1


def test_rest_diary_reads_real_http_contract(client,monkeypatch):
    import json,threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from atlas.config import settings
    from atlas.diary import RestDiaryProvider,sync
    from atlas.models import Assignment
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Authorization')!='Bearer fixture-only':
                self.send_response(401);self.end_headers();return
            raw=json.dumps({'homework':[{'external_id':'api-1','subject':'Тестовый API','title':'HTTP задание','due_at':'2030-01-01T12:00:00Z','grade':'5'}],'schedule':[{'subject':'Тестовый API','time':'09:00'}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('ATLAS_INTEGRATION_HOSTS','127.0.0.1')
    monkeypatch.setenv('ATLAS_CONNECTOR_TEST','fixture-only');settings.cache_clear()
    url='http://127.0.0.1:'+str(server.server_port)+'/diary'
    try:
        provider=RestDiaryProvider(url,'ATLAS_CONNECTOR_TEST')
        assert provider.session_status()=='AUTHORIZED' and len(provider.schedule(None))==1
        assert RestDiaryProvider(url).session_status()=='REAUTH_REQUIRED_OR_UNAVAILABLE'
        with sessions()() as s:
            result=sync(s,provider,'fixture-api',url);s.commit()
            assert not result['demo'] and result['new']==1
            assert sync(s,provider,'fixture-api',url)['new']==0
            assert s.scalar(select(Assignment).where(Assignment.provider=='fixture-api')).source==url
    finally:server.shutdown();thread.join(timeout=5);server.server_close()
