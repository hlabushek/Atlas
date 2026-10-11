from datetime import datetime,timezone,timedelta
import json,ssl,threading,sys,hashlib,uuid
import pytest,httpx
from atlas.db import sessions
from atlas.models import DeviceCommand
from atlas.desktop import poll,command,complete,sweep
from atlas.companion_fs import execute
from atlas.worker import tick
from test_agent import create,approve,detail

def policy():return {'enabled':True,'roots':['docs'],'operations':['read_text','list'],'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
def device(c,fingerprint='a'*64):
    d=c.post('/api/devices',json={'name':'Тестовое устройство','fingerprint':fingerprint,'expires_at':policy()['expires_at']}).json()
    assert c.put('/api/devices/'+d['id'],json=policy()).status_code==200
    c.put('/api/tools/desktop.read/permission',json={'allowed':True});return d

def test_device_expiry_from_sqlite_api_remains_timezone_aware(client):
    d=device(client)
    fetched=next(row for row in client.get('/api/devices').json() if row['id']==d['id'])
    assert fetched['expires_at'].endswith('+00:00')
    revoked=client.put('/api/devices/'+d['id'],json={'enabled':False,'roots':[],'operations':[],'expires_at':fetched['expires_at']})
    assert revoked.status_code==200 and revoked.json()['enabled'] is False

def pending(c,d):
    t=create(c,[{'tool':'desktop.read','arguments':{'device_id':d['id'],'root':'docs','operation':'read_text','path':'hello.txt'}}])
    tick();assert detail(c,t)['status']=='WAITING_APPROVAL'
    approve(c);tick();assert detail(c,t)['status']=='WAITING_DEVICE';return t

def test_device_queue_approval_result_and_dedup(client):
    d=device(client);t=pending(client,d)
    with sessions()() as s:job=poll(s,d['fingerprint']);s.commit()
    with sessions()() as s:
        result={'text':'Привет','sha256':hashlib.sha256('Привет'.encode()).hexdigest()}
        assert complete(s,d['fingerprint'],job['id'],job['token'],result)['ok'];s.commit()
        assert complete(s,d['fingerprint'],job['id'],job['token'],result)['ok'];s.commit()
    tick();assert detail(client,t)['status']=='COMPLETED'
    assert len(client.get('/api/devices/'+d['id']+'/commands').json())==1
    assert 'lease_token' not in client.get('/api/devices/'+d['id']+'/commands').json()[0]

@pytest.mark.parametrize('revoke',['device','tool'])
def test_revoked_device_or_tool_blocks_execution(client,revoke):
    d=device(client);t=pending(client,d)
    with sessions()() as s:job=poll(s,d['fingerprint']);s.commit()
    if revoke=='tool':client.put('/api/tools/desktop.read/permission',json={'allowed':False})
    else:client.put('/api/devices/'+d['id'],json={**policy(),'enabled':False})
    with sessions()() as s:
        with pytest.raises(PermissionError):command(s,d['fingerprint'],job['id'],job['token'])
        sweep(s);s.commit()
    assert detail(client,t)['status']=='FAILED'

def test_lease_unknown_never_reissued(client):
    d=device(client);t=pending(client,d)
    with sessions()() as s:
        job=poll(s,d['fingerprint']);s.commit()
        row=s.get(DeviceCommand,job['id']);row.lease_until=datetime.now(timezone.utc)-timedelta(seconds=1);s.commit()
        assert poll(s,d['fingerprint']) is None;s.commit()
    assert detail(client,t)['status']=='PAUSED'
    assert client.post('/api/tasks/'+t['id']+'/resume').status_code==409

@pytest.mark.parametrize('path',['../secret','/absolute','C:/file','a/../b','a\\b','.env','key.pem','a:stream','a.'])
def test_path_validation_rejects_escapes(client,path):
    from atlas.desktop import DesktopInput
    with pytest.raises(ValueError):DesktopInput(device_id='x',root='docs',operation='read_text',path=path)

def test_local_filesystem_grants_limits_and_links(client,tmp_path):
    root=tmp_path/'root';root.mkdir();(root/'hello.txt').write_text('Привет',encoding='utf-8')
    cfg={**policy(),'roots':{'docs':str(root)}}
    args={'device_id':'x','root':'docs','operation':'read_text','path':'hello.txt'}
    assert execute(cfg,args)['text']=='Привет'
    assert execute(cfg,{**args,'path':'','operation':'list'})['entries'][0]['name']=='hello.txt'
    (root/'huge.txt').write_bytes(b'a'*65537)
    with pytest.raises(ValueError):execute(cfg,{**args,'path':'huge.txt'})
    import os
    os.link(root/'hello.txt',root/'hard.txt')
    with pytest.raises(ValueError):execute(cfg,{**args,'path':'hard.txt'})
    os.unlink(root/'hard.txt')
    (root/'secret.txt').write_text('password=never-export')
    with pytest.raises(ValueError):execute(cfg,{**args,'path':'secret.txt'})
    with pytest.raises(PermissionError):execute({**cfg,'enabled':False},args)
    with pytest.raises(PermissionError):execute({**cfg,'expires_at':'2000-01-01T00:00:00Z'},args)
    outside=tmp_path/'outside';outside.mkdir();(outside/'hello.txt').write_text('Escape')
    if sys.platform=='win32':
        import subprocess
        result=subprocess.run(['cmd','/c','mklink','/J',str(root/'linked'),str(outside)],capture_output=True)
        assert result.returncode==0
    else:(root/'linked').symlink_to(outside,target_is_directory=True)
    with pytest.raises((ValueError,OSError)):execute(cfg,{**args,'path':'linked/hello.txt'})

def test_desktop_create_text_is_bounded_and_never_overwrites(client,tmp_path):
    root=tmp_path/'root';root.mkdir()
    cfg={**policy(),'roots':{'docs':str(root)},'operations':['create_text']}
    args={'device_id':'x','root':'docs','operation':'create_text','path':'new/note.md','content':'Первый вариант\n'}
    (root/'new').mkdir()
    result=execute(cfg,args)
    assert result['created'] and result['path']=='new/note.md'
    assert (root/'new/note.md').read_bytes()=='Первый вариант\n'.encode('utf-8')
    with pytest.raises(ValueError,match='never overwritten'):
        execute(cfg,{**args,'content':'Второй вариант'})
    assert (root/'new/note.md').read_bytes()=='Первый вариант\n'.encode('utf-8')
    from atlas.desktop import DesktopInput
    with pytest.raises(ValueError):DesktopInput.model_validate({**args,'path':'new/secret.json','content':'api_key=secret-value'})
    with pytest.raises(ValueError):DesktopInput.model_validate({**args,'path':'new/code.py'})

def test_create_text_requires_separate_tool_permission_approval_and_real_mtls(client,tls,tmp_path):
    certdir,url,fps=tls;d=device(client,fps['device'])
    grants={**policy(),'operations':['read_text','list','create_text']}
    assert client.put('/api/devices/'+d['id'],json=grants).status_code==200
    assert client.put('/api/tools/desktop.write/permission',json={'allowed':True}).status_code==200
    content='Создано после отдельного подтверждения'
    t=create(client,[{'tool':'desktop.write','arguments':{'device_id':d['id'],'root':'docs','operation':'create_text','path':'new.txt','content':content}}])
    tick();assert detail(client,t)['status']=='WAITING_APPROVAL'
    approve(client);tick();assert detail(client,t)['status']=='WAITING_DEVICE'
    root=tmp_path/'docs';root.mkdir()
    config={**grants,'server':url,'ca':str(certdir/'ca.pem'),'cert':str(certdir/'device.pem'),'key':str(certdir/'device.key'),'roots':{'docs':str(root)}}
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps(config))
    from atlas.companion import tick as companion_tick
    assert companion_tick(cfg);tick()
    state=detail(client,t)
    assert state['status']=='COMPLETED'
    assert (root/'new.txt').read_text(encoding='utf-8')==content
    assert state['calls'][0]['result']['created'] is True

def test_launch_file_requires_exact_local_application_and_server_approval(client,tmp_path,monkeypatch):
    from atlas.desktop import DesktopInput
    for path in ('run.exe','script.py','shortcut.lnk'):
        with pytest.raises(ValueError):DesktopInput(device_id='x',root='docs',operation='launch_file',path=path,app='notepad')
    with pytest.raises(ValueError):DesktopInput(device_id='x',root='docs',operation='launch_file',path='note.txt')
    d=device(client)
    grants={**policy(),'operations':['read_text','list','launch_file']}
    assert client.put('/api/devices/'+d['id'],json=grants).status_code==200
    assert client.put('/api/tools/desktop.write/permission',json={'allowed':True}).status_code==200
    t=create(client,[{'tool':'desktop.write','arguments':{'device_id':d['id'],'root':'docs','operation':'launch_file','path':'note.txt','app':'notepad'}}])
    tick();assert detail(client,t)['status']=='WAITING_APPROVAL'
    approve(client);tick();assert detail(client,t)['status']=='WAITING_DEVICE'
    with sessions()() as s:job=poll(s,d['fingerprint']);s.commit()
    with sessions()() as s:
        assert complete(s,d['fingerprint'],job['id'],job['token'],{'launched':True,'path':'note.txt','app':'notepad','pid':123})['ok'];s.commit()
    tick();assert detail(client,t)['status']=='COMPLETED'

@pytest.mark.skipif(sys.platform!='win32',reason='Windows application launch')
def test_local_launch_uses_fixed_executable_without_shell(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();(root/'note.txt').write_text('hello')
    app=tmp_path/'notepad.exe';app.write_bytes(b'fixture')
    config={**policy(),'roots':{'docs':str(root)},'operations':['launch_file'],'applications':{'notepad':{'executable':str(app),'extensions':['txt']}}}
    seen=[]
    class Process:pid=123
    def fake_popen(argv,**kwargs):seen.append((argv,kwargs));return Process()
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',fake_popen)
    result=execute(config,{'device_id':'x','root':'docs','operation':'launch_file','path':'note.txt','app':'notepad'})
    assert result['launched'] and seen[0][0]==[str(app),str(root/'note.txt')] and seen[0][1]['shell'] is False
    with pytest.raises(PermissionError):execute({**config,'applications':{}},{'device_id':'x','root':'docs','operation':'launch_file','path':'note.txt','app':'notepad'})

@pytest.fixture
def tls(client,tmp_path):
    from atlas.desktop_certificates import create as certs
    fingerprints=certs(tmp_path/'tls')
    from atlas.desktop_gateway import make_server
    server=make_server(('127.0.0.1',0),str(tmp_path/'tls/server.pem'),str(tmp_path/'tls/server.key'),str(tmp_path/'tls/ca.pem'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield tmp_path/'tls','https://127.0.0.1:'+str(server.server_port),fingerprints
    server.shutdown();thread.join(timeout=5);server.server_close()

def test_real_mtls_companion_end_to_end(client,tls,tmp_path):
    certdir,url,fps=tls
    d=device(client,fps['device']);t=pending(client,d)
    root=tmp_path/'docs';root.mkdir();(root/'hello.txt').write_text('Настоящее mTLS чтение',encoding='utf-8')
    config={**policy(),'server':url,'ca':str(certdir/'ca.pem'),'cert':str(certdir/'device.pem'),'key':str(certdir/'device.key'),'roots':{'docs':str(root)}}
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps(config))
    from atlas.companion import tick as companion_tick
    assert companion_tick(cfg);tick()
    assert detail(client,t)['status']=='COMPLETED'
    assert detail(client,t)['calls'][0]['result']['text']=='Настоящее mTLS чтение'
    ctx=ssl.create_default_context(cafile=str(certdir/'ca.pem'))
    with pytest.raises(httpx.TransportError):
        with httpx.Client(verify=ctx,trust_env=False) as c:c.get(url+'/next')
    ctx.load_cert_chain(str(certdir/'server.pem'),str(certdir/'server.key'))
    with pytest.raises(httpx.TransportError):
        with httpx.Client(verify=ctx,trust_env=False) as c:c.get(url+'/next')


def test_voice_client_factory_uses_real_mtls_identity(client, tls, tmp_path):
    from atlas.local_voice import _atlas_client
    certdir, url, fps = tls
    registered = device(client, fps['device'])
    cid = client.post('/api/conversations', json={'title': 'Регрессия voice mTLS'}).json()['id']
    assert client.put('/api/devices/' + registered['id'] + '/voice',
                      json={'enabled': True, 'conversation_id': cid}).status_code == 200
    config = {'server': url, 'ca': str(certdir / 'ca.pem'),
              'cert': str(certdir / 'device.pem'), 'key': str(certdir / 'device.key')}
    path = tmp_path / 'voice-companion.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    with _atlas_client({'companion_config': str(path)}) as voice:
        accepted = voice.post('/voice', json={'request_id': str(uuid.uuid4()),
                                             'text': 'Проверка настоящего клиентского сертификата', 'mode': 'chat'})
        assert accepted.status_code == 200
        assert accepted.json()['conversation_id'] == cid
    # Не принимаем сертификат серверного назначения как клиентский.
    path.write_text(json.dumps({**config, 'cert': str(certdir / 'server.pem'),
                               'key': str(certdir / 'server.key')}), encoding='utf-8')
    with pytest.raises(httpx.TransportError):
        with _atlas_client({'companion_config': str(path)}) as voice:
            voice.get('/voice-result/' + accepted.json()['task_id'])


def test_voice_center_link_real_mtls_sse_and_single_use_control(client, tls, tmp_path):
    from atlas.local_voice import _atlas_client
    from atlas.voice_center_link import VoiceCenterLink
    from atlas.voice_settings import effective_config
    import time
    certdir, url, fps = tls
    registered = device(client, fps['device'])
    cid = client.post('/api/conversations', json={'title': 'Voice Center SSE'}).json()['id']
    assert client.put('/api/devices/' + registered['id'] + '/voice', json={'enabled': True, 'conversation_id': cid}).status_code == 200
    companion = tmp_path / 'mtls.json'
    companion.write_text(json.dumps({'server': url, 'ca': str(certdir / 'ca.pem'),
        'cert': str(certdir / 'device.pem'), 'key': str(certdir / 'device.key')}), encoding='utf-8')
    path = tmp_path / 'voice.json'
    path.write_text(json.dumps({'voice_center_enabled': True, 'companion_config': str(companion)}), encoding='utf-8')
    with VoiceCenterLink(path, effective_config(path)) as link:
        link.update(event=True, state='recognizing', route='local', transcript='Проверка SSE')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rows = client.get('/api/voice/center').json()
            if rows[0]['connected']: break
            time.sleep(.03)
        assert rows[0]['connected'] and rows[0]['state']['transcript'] == 'Проверка SSE'
        control = client.post('/api/voice/devices/' + registered['id'] + '/control', json={'kind': 'stop'})
        assert control.status_code == 202
        received = link.commands.get(timeout=5)
        assert received['id'] == control.json()['id']
        link.finish(received, True)
        with _atlas_client({'companion_config': str(companion)}) as voice:
            queued = voice.post('/voice', json={'request_id': str(uuid.uuid4()), 'text': 'SSE задача', 'mode': 'chat'})
            assert queued.status_code == 200
            tid = queued.json()['task_id']
            assert voice.post('/voice-cancel', json={'task_id': tid}).json()['status'] == 'CANCELLED'
            with voice.stream('GET', '/voice-sse/' + tid) as stream:
                assert stream.headers['content-type'].startswith('text/event-stream')
                events = [json.loads(line[6:]) for line in stream.iter_lines() if line.startswith('data: ')]
            assert events[-1]['status'] == 'CANCELLED'
        # An invalid local target must not block privacy/mute/grant revocation or controls.
        row = client.get('/api/voice/center').json()[0]
        policy = {**row['policy'], 'enabled': False, 'network_mode': 'local_only', 'local_grants': [],
                  'scenarios': [{'name': 'Не разрешён', 'phrases': ['запусти'],
                                 'actions': [{'type': 'open_site', 'site': 'нет в allowlist'}]}]}
        assert client.put('/api/voice/devices/' + registered['id'] + '/settings',
                          json={'revision': row['revision'], 'policy': policy}).status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and effective_config(path)['enabled']:
            time.sleep(.03)
        applied = effective_config(path)
        assert applied['enabled'] is False and applied['network_mode'] == 'local_only'
        assert applied['local_grants'] == [] and not applied.get('scenarios')
        rejected = client.post('/api/voice/devices/' + registered['id'] + '/control', json={'kind': 'reject'})
        assert rejected.status_code == 202
        assert link.commands.get(timeout=5)['id'] == rejected.json()['id']


def test_voice_bridge_uses_opt_in_shared_chat_and_is_idempotent(client,tls):
    certdir,url,fps=tls
    d=device(client,fps['device'])
    cid=client.post('/api/conversations',json={'title':'Голосовой Atlas'}).json()['id']
    endpoint='/api/devices/'+d['id']+'/voice'
    assert client.put(endpoint,json={'enabled':True,'conversation_id':cid}).status_code==200
    body={'request_id':str(uuid.uuid4()),'text':'Расскажи кратко о проекте','mode':'chat'}
    context=ssl.create_default_context(cafile=str(certdir/'ca.pem'));context.load_cert_chain(str(certdir/'device.pem'),str(certdir/'device.key'))
    with httpx.Client(base_url=url,verify=context,trust_env=False) as companion:
        accepted=companion.post('/voice',json=body);assert accepted.status_code==200,accepted.text
        duplicate=companion.post('/voice',json=body)
        assert duplicate.status_code==200 and duplicate.json()['task_id']==accepted.json()['task_id']
        assert client.put(endpoint,json={'enabled':False,'conversation_id':None}).status_code==200
        assert companion.post('/voice',json={**body,'request_id':str(uuid.uuid4())}).status_code==403
    task=client.get('/api/tasks/'+accepted.json()['task_id']).json()
    assert task['kind']=='chat' and task['conversation_id']==cid
    assert client.get('/api/conversations/'+cid+'/messages').json()[0]['content']==body['text']


def test_voice_event_stream_delivers_status_and_final_answer(client,tls):
    certdir,url,fps=tls
    d=device(client,fps['device'])
    cid=client.post('/api/conversations',json={'title':'Потоковый голос'}).json()['id']
    endpoint='/api/devices/'+d['id']+'/voice'
    assert client.put(endpoint,json={'enabled':True,'conversation_id':cid}).status_code==200
    body={'request_id':str(uuid.uuid4()),'text':'Проверь поток событий','mode':'chat'}
    context=ssl.create_default_context(cafile=str(certdir/'ca.pem'))
    context.load_cert_chain(str(certdir/'device.pem'),str(certdir/'device.key'))
    with httpx.Client(base_url=url,verify=context,trust_env=False,timeout=15) as companion:
        accepted=companion.post('/voice',json=body)
        assert accepted.status_code==200,accepted.text
        assert tick()
        with companion.stream('GET','/voice-events/'+accepted.json()['task_id']) as stream:
            assert stream.status_code==200
            events=[json.loads(line) for line in stream.iter_lines() if line]
    assert events[0]['type'] in {'status','answer'}
    assert any(event.get('type')=='answer' and event.get('answer') for event in events)


@pytest.mark.parametrize('revoke', [False, True])
def test_live_provider_sse_reaches_mtls_before_final_and_rechecks_policy(client, tls, monkeypatch, revoke):
    """Настоящие TCP HTTP/SSE и mTLS; модель заменена тестовым сервером."""
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    from atlas.model_settings import Runtime
    from atlas import router
    release, provider_started = threading.Event(), threading.Event()
    prefix = 'Первый проверенный ответ. ' * 12
    ending = 'Ответ завершён.'
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['stream'] is True
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(('data: ' + json.dumps({'choices': [{'delta': {'content': prefix}}]}, ensure_ascii=False) + '\n\n').encode())
            self.wfile.flush()
            provider_started.set()
            if release.wait(10):
                self.wfile.write(('data: ' + json.dumps({'choices': [{'delta': {'content': ending}}]}, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode())
                self.wfile.flush()
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    provider.daemon_threads = True
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    runtime = Runtime('openai-compatible', f'http://127.0.0.1:{provider.server_port}/v1', 'fixture', 1024, True)
    monkeypatch.setattr(router, 'resolve', lambda *_args, **_kwargs: runtime)
    certdir, url, fps = tls
    d = device(client, fps['device'])
    cid = client.post('/api/conversations', json={'title': 'Настоящий поток'}).json()['id']
    endpoint = '/api/devices/' + d['id'] + '/voice'
    client.put(endpoint, json={'enabled': True, 'conversation_id': cid})
    context = ssl.create_default_context(cafile=str(certdir/'ca.pem'))
    context.load_cert_chain(str(certdir/'device.pem'), str(certdir/'device.key'))
    worker = None
    try:
        with httpx.Client(base_url=url, verify=context, trust_env=False, timeout=15) as companion:
            tid = companion.post('/voice', json={'request_id': str(uuid.uuid4()), 'text': 'Объясни поток', 'mode': 'chat'}).json()['task_id']
            worker = threading.Thread(target=tick)
            worker.start()
            assert provider_started.wait(5)
            events = []
            with companion.stream('GET', '/voice-events/' + tid) as response:
                for line in response.iter_lines():
                    if not line: continue
                    event = json.loads(line)
                    events.append(event)
                    if event['type'] == 'fragment' and not release.is_set():
                        assert client.get('/api/tasks/' + tid).json()['status'] == 'RUNNING'
                        if revoke:
                            client.put(endpoint, json={'enabled': False, 'conversation_id': None})
                        else:
                            release.set()
                    if event['type'] == 'denied': break
            assert any(e['type'] == 'fragment' for e in events)
            assert 'claim' not in json.dumps(events) and 'Authorization' not in json.dumps(events)
            if revoke:
                assert events[-1]['type'] == 'denied'
                assert not any(e['type'] == 'answer' for e in events)
            else:
                assert ''.join(e['text'] for e in events if e['type'] == 'fragment') == prefix + ending
                assert events[-1]['answer'] == prefix + ending
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=5)
            assert not worker.is_alive()
        provider.shutdown()
        provider.server_close()


def test_voice_task_mode_still_obeys_existing_agent_confirmation_gate(client,tls):
    certdir,url,fps=tls
    d=device(client,fps['device'])
    cid=client.post('/api/conversations',json={'title':'Голосовой Atlas'}).json()['id']
    endpoint='/api/devices/'+d['id']+'/voice'
    assert client.put(endpoint,json={'enabled':True,'conversation_id':cid}).status_code==200
    body={'request_id':str(uuid.uuid4()),'text':'Составь план проекта','mode':'task'}
    context=ssl.create_default_context(cafile=str(certdir/'ca.pem'));context.load_cert_chain(str(certdir/'device.pem'),str(certdir/'device.key'))
    with httpx.Client(base_url=url,verify=context,trust_env=False) as companion:
        accepted=companion.post('/voice',json=body);assert accepted.status_code==200,accepted.text
        assert companion.get('/voice-result/'+accepted.json()['task_id']).json()['status']=='PENDING'
    task=client.get('/api/tasks/'+accepted.json()['task_id']).json()
    assert task['kind']=='agent' and task['status']=='PENDING' and task['plan']==[]
    assert tick()
    assert tick()
    waiting=client.get('/api/tasks/'+task['id']).json()
    assert waiting['status']=='WAITING_APPROVAL' and client.get('/api/notes').json()==[],(waiting['status'],waiting.get('plan'),waiting.get('error'),waiting.get('current_step'))
    approval=client.get('/api/approvals').json()[0]
    assert client.post('/api/approvals/'+approval['id'],json={'approve':True}).status_code==200
    assert tick() and client.get('/api/tasks/'+task['id']).json()['status']=='COMPLETED'
def test_waiting_device_can_cancel_without_execution(client):
    d=device(client);t=pending(client,d)
    with sessions()() as s:job=poll(s,d['fingerprint']);s.commit()
    assert client.post('/api/tasks/'+t['id']+'/cancel').json()['status']=='CANCELLED'
    with sessions()() as s:
        with pytest.raises(PermissionError):complete(s,d['fingerprint'],job['id'],job['token'],{'entries':[]})
    with sessions()() as s:assert poll(s,d['fingerprint']) is None
    assert client.get('/api/devices/'+d['id']+'/commands').json()[0]['status']=='CANCELLED'
