from datetime import timedelta,datetime,timezone
import uuid
from sqlalchemy import select
from atlas.db import sessions
from atlas.config import settings
from atlas.models import NotificationSettings,Initiative,Delivery,Message,TelegramBinding
from atlas.notifications import scheduler_tick,utc
from atlas.telegram import handle_update,dispatch_tick,queue_updates
from test_slice import conversation

def prefs(c,**changes):
    data={'enabled':True,'timezone':'UTC','quiet_start':0,'quiet_end':0,'daily_limit':3,'max_repeats':1,'repeat_hours':1,'channel':'web'};data.update(changes)
    assert c.put('/api/notifications/settings',json=data).status_code==200

def reminder(c,cid=None,priority=2):
    stamp=datetime.now(timezone.utc)-timedelta(seconds=2)
    return c.post('/api/initiatives',json={'message':'Вернуться к проекту Atlas','due_at':stamp.isoformat(),'priority':priority,'conversation_id':cid}).json()

def bind(c,monkeypatch):
    monkeypatch.setenv('ATLAS_TELEGRAM_BOT_TOKEN','fake-test-token')
    monkeypatch.setenv('ATLAS_TELEGRAM_OWNER_ID','123')
    settings.cache_clear()
    code=c.post('/api/telegram/bind-code').json()['code']
    update={'update_id':1,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'/start '+code}}
    assert handle_update(update)
    return c.get('/api/telegram').json()

class FakeTelegram:
    def __init__(self):self.sent=[]
    def send(self,chat,payload):self.sent.append((chat,payload));return {'message_id':99}

def test_initiative_web_reply_in_another_session(client):
    from fastapi.testclient import TestClient
    from atlas.api import app
    prefs(client);item=reminder(client,conversation(client))
    assert scheduler_tick()==1
    with TestClient(app) as other:
        other.post('/api/login',json={'password':'test-owner-password-123'});other.headers['X-Atlas-Client']='web'
        response=other.post('/api/conversations/'+item['conversation_id']+'/messages',json={'content':'Да, вернёмся','request_key':str(uuid.uuid4()),'initiative_id':item['id']})
        assert response.status_code==202
    assert client.get('/api/initiatives').json()[0]['status']=='ANSWERED'
    assert scheduler_tick(datetime.now(timezone.utc)+timedelta(hours=2))==0

def test_reminders_limited_and_never_assume_read(client):
    prefs(client);reminder(client)
    stamp=datetime.now(timezone.utc)
    assert scheduler_tick(stamp)==1
    assert scheduler_tick(stamp+timedelta(hours=2))==1
    assert scheduler_tick(stamp+timedelta(hours=4))==0
    item=client.get('/api/initiatives').json()[0]
    assert item['status']=='SENT' and item['repeats']==1 and item['reply_id'] is None
    assert len(client.get('/api/deliveries').json())==2

def test_quiet_daily_and_disabled(client):
    stamp=datetime.now(timezone.utc)
    prefs(client,quiet_start=stamp.hour,quiet_end=(stamp.hour+1)%24)
    reminder(client);assert scheduler_tick(stamp)==0
    prefs(client,daily_limit=1)
    reminder(client);assert scheduler_tick(stamp)==1
    assert scheduler_tick(stamp)==0
    prefs(client,enabled=False);assert scheduler_tick(stamp+timedelta(days=1))==0

def test_expiry_and_event_dedup(client):
    prefs(client);item=reminder(client)
    assert scheduler_tick()==1 and scheduler_tick()==0
    scheduler_tick(datetime.now(timezone.utc)+timedelta(days=4))
    assert client.get('/api/initiatives').json()[0]['status']=='EXPIRED'

def test_secure_binding_and_update_dedup(client,monkeypatch):
    b=bind(client,monkeypatch)
    assert b['bound']
    update={'update_id':2,'message':{'from':{'id':999},'chat':{'id':999,'type':'private'},'text':'steal data'}}
    assert not handle_update(update)
    update={'update_id':3,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'Atlas'}}
    assert handle_update(update) and not handle_update(update)
    messages=client.get('/api/conversations/'+b['conversation_id']+'/messages').json()
    assert len(messages)==1 and messages[0]['content']=='Atlas'

def test_cross_channel_reply_and_telegram_delivery(client,monkeypatch):
    b=bind(client,monkeypatch);prefs(client,channel='telegram')
    item=reminder(client,b['conversation_id']);scheduler_tick()
    api=FakeTelegram()
    dispatch_tick(api) # binding acknowledgement
    dispatch_tick(api) # initiative
    assert len(api.sent)==2
    assert handle_update({'update_id':2,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'Ответ','reply_to_message':{'message_id':99}}})
    assert client.get('/api/initiatives').json()[0]['status']=='ANSWERED'

def test_unknown_send_never_retried(client,monkeypatch):
    bind(client,monkeypatch)
    class Broken:
        def send(self,*a):raise RuntimeError('secret-token-in-URL')
    assert dispatch_tick(Broken())
    assert not dispatch_tick(FakeTelegram())
    delivery=client.get('/api/deliveries').json()[0]
    assert delivery['status']=='UNKNOWN' and 'secret-token' not in str(delivery)

def test_telegram_inline_approval_uses_same_policy(client,monkeypatch):
    b=bind(client,monkeypatch)
    from test_agent import create,note_step,detail
    from atlas.worker import tick
    t=create(client,[note_step('local')]);tick()
    a=client.get('/api/approvals').json()[0]
    update={'update_id':2,'callback_query':{'id':'callback','from':{'id':123},'data':'ap:'+a['id']+':y','message':{'chat':{'id':123,'type':'private'}}}}
    assert handle_update(update);tick()
    assert detail(client,t)['status']=='COMPLETED'


def test_telegram_rechecks_quiet_hours_before_send(client,monkeypatch):
    bind(client,monkeypatch);prefs(client,channel='telegram')
    reminder(client);scheduler_tick()
    api=FakeTelegram();dispatch_tick(api)
    stamp=datetime.now(timezone.utc)
    prefs(client,channel='telegram',quiet_start=stamp.hour,quiet_end=(stamp.hour+1)%24)
    assert not dispatch_tick(api,stamp)
    assert len(api.sent)==1
    prefs(client,channel='telegram')
    assert dispatch_tick(api,stamp)
    assert len(api.sent)==2


def test_channel_secrets_do_not_reach_messages_or_tasks(client,monkeypatch):
    b=bind(client,monkeypatch)
    update={'update_id':2,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'token=do-not-store-this'}}
    assert not handle_update(update)
    assert client.get('/api/conversations/'+b['conversation_id']+'/messages').json()==[]
    assert client.post('/api/conversations/'+b['conversation_id']+'/messages',json={'content':'test-owner-password-123','request_key':str(uuid.uuid4())}).status_code==422
    assert client.get('/api/tasks').json()==[]

def test_telegram_text_document_attached_to_shared_conversation(client,monkeypatch):
    b=bind(client,monkeypatch)
    class Files(FakeTelegram):
        def download_file(self,file_id,max_bytes):
            assert file_id=='fixture-file' and max_bytes==65536
            return b'hello from file\n'
    update={'update_id':2,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'caption':'Please read','document':{'file_id':'fixture-file','file_name':'note.TXT','file_size':16}}}
    assert handle_update(update,Files())
    assert not handle_update(update,Files())
    messages=client.get('/api/conversations/'+b['conversation_id']+'/messages').json()
    assert len(messages)==1 and 'hello from file' in messages[0]['content'] and 'Please read' in messages[0]['content']

def test_telegram_document_limits_and_secret_rejection(client,monkeypatch):
    b=bind(client,monkeypatch)
    class Files(FakeTelegram):
        def download_file(self,file_id,max_bytes):return b'api_key=secret-value'
    update={'update_id':2,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'document':{'file_id':'x','file_name':'bad.exe','file_size':3}}}
    assert not handle_update(update,Files())
    update={'update_id':3,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'document':{'file_id':'x','file_name':'note.txt','file_size':20}}}
    assert not handle_update(update,Files())
    assert client.get('/api/conversations/'+b['conversation_id']+'/messages').json()==[]
    assert client.get('/api/tasks').json()==[]

def test_telegram_voice_is_transcribed_then_queued(client,monkeypatch):
    b=bind(client,monkeypatch)
    from atlas import speech
    seen=[]
    monkeypatch.setattr(speech,'transcribe',lambda raw,duration: seen.append((raw,duration)) or 'Напомни завтра позвонить маме')
    class Voice(FakeTelegram):
        def download_file(self,file_id,max_bytes):
            assert file_id=='voice-id' and max_bytes==5*1024*1024
            return b'ogg fixture'
    update={'update_id':2,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'voice':{'file_id':'voice-id','file_size':11,'duration':42}}}
    assert handle_update(update,Voice())
    assert seen==[(b'ogg fixture',42)]
    messages=client.get('/api/conversations/'+b['conversation_id']+'/messages').json()
    assert len(messages)==1 and messages[0]['content']=='Напомни завтра позвонить маме'

def test_telegram_voice_limits_and_rub_budget(client,monkeypatch):
    bind(client,monkeypatch)
    from atlas import speech
    called=[]
    monkeypatch.setattr(speech,'transcribe',lambda raw,duration: called.append(duration) or 'текст')
    class Voice(FakeTelegram):
        def download_file(self,file_id,max_bytes):return b'ogg'
    invalid={'update_id':2,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'voice':{'file_id':'x','file_size':3,'duration':121}}}
    assert not handle_update(invalid,Voice()) and called==[]
    assert client.put('/api/budget',json={'token_limit':100000,'speech_rub_day_limit':40}).status_code==200
    from atlas.speech import reserve,estimated_kopecks
    assert estimated_kopecks(60,41)==41 and estimated_kopecks(61,41)==42
    import pytest
    with pytest.raises(ValueError,match='дневной лимит'):
        reserve(60)
    assert client.get('/api/budget').json()['speech']['day_kopecks']==0

def test_speech_transcriber_records_reserved_rub_cost(client,monkeypatch):
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','fake-router-key')
    settings.cache_clear()
    from atlas import speech
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'text':'распознано'}
    calls=[]
    monkeypatch.setattr('atlas.speech.httpx.post',lambda *args,**kwargs:(calls.append((args,kwargs)) or Response()))
    assert speech.transcribe(b'fixture-audio',60)=='распознано'
    assert calls[0][0][0]=='https://routerai.ru/api/v1/audio/transcriptions'
    assert calls[0][1]['json']['input_audio']['format']=='ogg'
    usage=client.get('/api/budget').json()['speech']
    assert usage['month_kopecks']==41 and usage['unknown']==0
