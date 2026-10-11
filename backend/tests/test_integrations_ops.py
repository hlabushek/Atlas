import uuid,threading,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from contextlib import contextmanager
import pytest,httpx
from sqlalchemy import select
from atlas.db import sessions
from atlas.models import Memory,Embedding,ModelUsage,Budget,PushSubscription,PushDelivery,NotificationSettings,Initiative,Conversation,now
from atlas.config import settings
from atlas.memory import retrieve
from atlas.embeddings import digest,key

@contextmanager
def provider():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            text=data['input'];v=[0.0]*768;v[0 if text in ('велосипед','двухколёсный транспорт') else 1]=1.0
            raw=json.dumps({'data':[{'embedding':v}],'usage':{'prompt_tokens':3,'total_tokens':3}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)
    finally:server.shutdown();thread.join();server.server_close()

def test_real_embedding_protocol_and_semantic_search(client,monkeypatch):
    from atlas.embeddings import index_one,warm
    with provider() as url:
        monkeypatch.setenv('ATLAS_EMBEDDING_URL',url);monkeypatch.setenv('ATLAS_EMBEDDING_MODEL','fixture-768');settings.cache_clear()
        a=client.post('/api/memory',json={'content':'велосипед'}).json()
        b=client.post('/api/memory',json={'content':'самолёт'}).json()
        assert index_one() and index_one() and not index_one()
        warm('двухколёсный транспорт')
        with sessions()() as s:
            assert retrieve(s,'двухколёсный транспорт')[0].id==a['id']
            assert all(r.status=='KNOWN' for r in s.scalars(select(ModelUsage)))
        assert client.put('/api/memory/'+a['id'],json={'content':'катер'}).status_code==200
        assert a['id'] not in [m['id'] for m in client.get('/api/memory?q=двухколёсный транспорт').json()]
        assert client.delete('/api/memory/'+b['id']).status_code==200

def test_conflicts_require_owner_choice_and_edit_invalidates(client):
    a=client.post('/api/memory',json={'content':'город Москва'}).json()['id'];b=client.post('/api/memory',json={'content':'город Казань'}).json()['id']
    for identifier,value in ((a,'Москва'),(b,'Казань')):
        assert client.put('/api/memory/'+identifier+'/fact',json={'key':'город','value':value}).status_code==200
    conflicts=client.get('/api/memory-conflicts').json();assert len(conflicts)==1
    assert client.post('/api/memory-conflicts/'+conflicts[0]['id']+'/resolve',json={'choice':'right'}).status_code==200
    assert [m['id'] for m in client.get('/api/memory?q=город').json()]==[b]
    assert client.delete('/api/memory/'+a).status_code==200
    assert client.put('/api/memory/'+b,json={'content':'город Омск'}).status_code==200
    assert client.get('/api/memory-conflicts').json()==[]

def test_files_real_upload_and_idempotent_chat(client):
    f=client.post('/api/files',files={'file':('план.txt','Проверить резервную копию'.encode(),'text/plain')});assert f.status_code==200
    identifier=f.json()['id'];assert 'резервную' in client.get('/api/files/'+identifier).text
    cid=client.post('/api/conversations',json={}).json()['id'];body={'content':'Прочитай','request_key':str(uuid.uuid4()),'attachment_ids':[identifier]}
    first=client.post('/api/conversations/'+cid+'/messages',json=body);assert first.status_code==202
    assert client.post('/api/conversations/'+cid+'/messages',json=body).json()['id']==first.json()['id']
    assert first.json()['goal']=='Прочитай'
    assert first.json()['attachments'][0]['id']==identifier
    assert 'text' not in first.json()['attachments'][0]
    assert client.post('/api/files',files={'file':('secret.txt',b'password=do-not-save')}).status_code==422
    assert client.post('/api/files',files={'file':('x.txt',b'x'*65537)}).status_code==413
    assert client.post('/api/files',files={'file':('x.exe',b'x')}).status_code==422
    assert client.delete('/api/files/'+identifier).status_code==200
    assert client.post('/api/conversations/'+cid+'/messages',json={**body,'request_key':str(uuid.uuid4())}).status_code==404

def test_budget_unknown_fail_closed_and_usage(client):
    from atlas.budget import reserve,settle
    assert client.put('/api/budget',json={'token_limit':10}).status_code==200
    identifier=reserve('chat','fixture',7,2);settle(identifier)
    with pytest.raises(ValueError):reserve('chat','fixture',2)
    assert client.get('/api/budget').json()['usage'][0]['status']=='UNKNOWN'
    assert client.put('/api/budget',json={'token_limit':100,'input_rate':1000000,'output_rate':2000000,'cost_limit':10}).status_code==200
    with pytest.raises(ValueError):reserve('chat','fixture',2)
    # Unknown cost before tariffs cannot be safely reinterpreted as free.

def test_daily_and_per_task_budget_caps_reserve_before_requests(client):
    import pytest
    from atlas.budget import reserve
    saved=client.put('/api/budget',json={'token_limit':1000,'cost_limit':1000,'input_rate':1000000,'output_rate':1000000,
        'daily_token_limit':15,'daily_cost_limit':10,'task_cost_limit':10})
    assert saved.status_code==200
    reserve('chat','fixture',4,2,(1000000,1000000),'task-a')
    with pytest.raises(ValueError,match='Лимит стоимости задачи'):
        reserve('plan','fixture',3,2,(1000000,1000000),'task-a')
    with pytest.raises(ValueError,match='Дневной лимит стоимости'):
        reserve('chat','fixture',3,2,(1000000,1000000),'task-b')
    with pytest.raises(ValueError,match='Дневной лимит токенов'):
        reserve('chat','fixture',10,0,(1000000,1000000),'task-b')
    totals=client.get('/api/budget').json()
    assert totals['today']=={'tokens':6,'cost':6}
    assert len(totals['usage'])==1 and totals['usage'][0]['task_id']=='task-a'
    assert client.put('/api/budget',json={'token_limit':900}).status_code==200
    assert client.get('/api/budget').json()['limits']['daily_token_limit']==15
def test_revocation_validation_and_auth_boundary(client):
    token=client.cookies.get('atlas_session')
    assert client.post('/api/logout').status_code==200
    client.cookies.set('atlas_session',token)
    for path in ('/api/ops','/api/files','/api/budget','/api/integrations','/api/memory-conflicts'):
        assert client.get(path).status_code==401
    assert client.post('/api/login',json={'password':['password=never-echo']}).status_code==422
    assert 'never-echo' not in client.post('/api/login',json={'password':['password=never-echo']}).text
    assert client.get('/openapi.json').status_code==404

def test_github_fixed_public_connector(client,monkeypatch):
    from atlas.github import read_repo,RepoInput
    calls=[]
    def handler(request):
        calls.append(str(request.url));return httpx.Response(200,json={'private':False,'full_name':'octocat/Hello-World','html_url':'https://github.com/octocat/Hello-World'})
    original=httpx.Client
    monkeypatch.setattr('atlas.github.httpx.Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    result=read_repo(None,SimpleNamespace(arguments={'owner':'octocat','repo':'Hello-World'}))
    assert result['full_name']=='octocat/Hello-World';assert calls==['https://api.github.com/repos/octocat/Hello-World']
    with pytest.raises(ValueError):RepoInput(owner='x/../../',repo='x')

def test_push_intent_dedup_and_ambiguous_not_replayed(client,monkeypatch):
    from atlas.push import tick,validate
    import base64
    from datetime import timedelta
    enc=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    point=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    keys={'auth':enc(b'x'*16),'p256dh':enc(point)}
    with pytest.raises(ValueError):validate('https://127.0.0.1/notify',keys)
    for name,value in [('ATLAS_VAPID_PRIVATE_KEY','fixture-vapid-private'),('ATLAS_VAPID_PUBLIC_KEY','fixture-public'),('ATLAS_VAPID_SUBJECT','mailto:owner@example.com')]:monkeypatch.setenv(name,value)
    settings.cache_clear()
    with sessions()() as s:
        p=s.get(NotificationSettings,1);p.enabled=True;p.enabled_at=now()-timedelta(hours=1);p.quiet_start=p.quiet_end=0
        c=Conversation(title='Push');s.add(c);s.flush()
        i=Initiative(event_key='push-test',reason='Тест',message='private owner content',conversation_id=c.id,status='SENT',sent_at=now(),next_allowed=now(),expires_at=now()+timedelta(days=1));s.add(i)
        s.add(PushSubscription(endpoint='https://fcm.googleapis.com/test',keys=keys));s.commit()
    calls=[]
    def crash(sub):calls.append(sub.id);raise TimeoutError()
    assert tick(crash)==1;assert tick(crash)==0;assert len(calls)==1
    with sessions()() as s:assert s.scalar(select(PushDelivery)).status=='UNKNOWN'


def test_real_push_crypto_and_redirects_disabled(client,monkeypatch):
    import base64,requests,http_ece
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from atlas.push import send
    encode=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
    receiver=ec.generate_private_key(ec.SECP256R1());vapid=ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv('ATLAS_VAPID_PRIVATE_KEY',encode(vapid.private_numbers().private_value.to_bytes(32,'big')))
    monkeypatch.setenv('ATLAS_VAPID_SUBJECT','mailto:owner@example.com');settings.cache_clear()
    keys={'auth':encode(b'x'*16),'p256dh':encode(receiver.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint))}
    calls=[]
    def transport(self,*args,**kw):
        calls.append(kw);r=requests.Response();r.status_code=201;r._content=b'';return r
    monkeypatch.setattr(requests.Session,'request',transport)
    send(SimpleNamespace(endpoint='https://fcm.googleapis.com/test',keys=keys))
    assert calls[0]['allow_redirects'] is False
    assert calls[0]['headers']['content-encoding']=='aes128gcm'
    raw=http_ece.decrypt(calls[0]['data'],private_key=receiver,auth_secret=b'x'*16,version='aes128gcm')
    assert json.loads(raw)['title']=='Atlas'
    assert json.loads(raw)['body']=='Новая инициатива. Откройте Atlas.'

def test_concurrent_budget_reservations_never_exceed_limit(client):
    from concurrent.futures import ThreadPoolExecutor
    from atlas.budget import reserve
    client.put('/api/budget',json={'token_limit':10})
    def attempt(_):
        try:return reserve('chat','fixture',6)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(attempt,range(4)))
    assert sum(bool(r) for r in results)==1
    assert client.get('/api/budget').json()['tokens']==6

def test_provider_failure_keeps_durable_reservation(client):
    from atlas.budget import request
    def fail(req):raise httpx.ReadTimeout('fixture')
    with httpx.Client(transport=httpx.MockTransport(fail)) as c:
        with pytest.raises(httpx.ReadTimeout):request(c,'https://fixture.example/embeddings',{}, {'input':'plain'},'embedding','fixture')
    usage=client.get('/api/budget').json()['usage'][0]
    assert usage['status']=='UNKNOWN' and usage['tokens']>0


def test_configured_secret_cannot_leave_in_provider_payload(client):
    from atlas.budget import request
    sent=[]
    with httpx.Client(transport=httpx.MockTransport(lambda req:sent.append(req))) as c:
        with pytest.raises(ValueError):request(c,'https://fixture.example/v1',{}, {'input':settings().owner_password},'chat','fixture')
    assert sent==[] and client.get('/api/budget').json()['tokens']==0


def test_revoked_session_closes_active_websocket(client):
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect('/api/events',headers={'origin':'http://testserver','host':'testserver'}) as ws:
        assert ws.receive_json()['type']=='tasks'
        assert client.post('/api/sessions/revoke').status_code==200
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
