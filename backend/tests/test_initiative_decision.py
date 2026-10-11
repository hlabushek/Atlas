import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import select, func

from atlas.db import sessions
from atlas.models import (Audit, Conversation, Delivery, Event, Initiative, InitiativeDecision,
    Message, NotificationSettings, Task, now)
from atlas.notifications import scheduler_tick
from test_slice import conversation
from test_notifications import prefs


def test_decision_endpoint_is_typed_bounded_and_budgeted(monkeypatch):
    from atlas import initiative_decision as module
    from atlas.model_settings import Runtime
    calls=[];settled=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'answers':{'notify':{'noul':0.91}}}
    class Client:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):calls.append((url,headers,json));return Response()
    monkeypatch.setattr(module.httpx,'Client',Client)
    monkeypatch.setattr(module,'reserve',lambda *args:(settled.append(('reserve',args)) or 'usage-id'))
    monkeypatch.setattr(module,'settle',lambda *args,**kwargs:settled.append(('settle',args,kwargs)))
    state={'candidate':{'kind':'task_completed','summary':'Готова конкретная задача'},'recent_messages':[{'role':'user','text':'Хочу закончить проект'}]}
    result=module.evaluate(state,Runtime('routerai','https://routerai.ru/api/v1','liquid/d1',1024,True,'fixture-key',(0,0),7))
    assert calls[0][0]=='https://routerai.ru/api/v1/decisions'
    assert calls[0][1]['Authorization']=='Bearer fixture-key'
    payload=calls[0][2]
    assert payload['model']=='liquid/d1' and payload['state']==state
    assert payload['questions']['notify']['type']=='noul'
    assert result['approved'] and result['confidence']==0.91
    assert len(result['context_hash'])==64
    assert settled[-1][2]['estimated_tokens']>0


def test_approved_task_candidate_uses_existing_chat_without_creating_messages(client,monkeypatch):
    from atlas.model_settings import Runtime
    prefs(client,context_enabled=True)
    monkeypatch.setattr('atlas.notifications.resolve',lambda role,session:Runtime('routerai','https://routerai.ru/api/v1','liquid/d1',1024,True,'fixture-key',(0,0),7))
    cid=conversation(client)
    client.post(f'/api/conversations/{cid}/messages',json={'content':'Вернёмся к Atlas завтра','request_key':str(uuid.uuid4())})
    with sessions()() as s:
        task=Task(conversation_id=cid,kind='agent',dry_run=False,goal='Подготовить план',request_key=str(uuid.uuid4()),status='COMPLETED')
        s.add(task);s.flush();task_id=task.id
        s.commit()
    seen=[]
    def approve(state,runtime):
        seen.append(state)
        return {'approved':True,'confidence':0.94,'model':'liquid/d1','context_hash':hashlib.sha256(json.dumps(state,ensure_ascii=False).encode()).hexdigest()}
    monkeypatch.setattr('atlas.initiative_decision.evaluate',approve)
    scheduler_tick()
    with sessions()() as s:
        cards=s.scalars(select(Initiative)).all()
        assert len(cards)==1 and cards[0].conversation_id==cid
        assert len(s.scalars(select(Conversation)).all())==1
        assert len(s.scalars(select(Message)).all())==1
        assert len(s.scalars(select(Delivery).where(Delivery.kind=='initiative')).all())==1
        decision=s.get(InitiativeDecision,'task:'+task_id)
        assert decision.status=='APPROVED' and decision.confidence==0.94
    assert seen[0]['candidate']['kind']=='task_completed'
    assert seen[0]['recent_messages']==[{'role':'user','text':'Вернёмся к Atlas завтра'}]


def test_model_suppression_is_saved_and_not_requeried(client,monkeypatch):
    from atlas.model_settings import Runtime
    prefs(client)
    monkeypatch.setattr('atlas.notifications.resolve',lambda role,session:Runtime('routerai','https://routerai.ru/api/v1','liquid/d1',1024,True,'fixture-key',(0,0),7))
    with sessions()() as s:
        s.add(Event(key='diary-event-1',kind='diary_changed',details={'title':'обычное изменение'},created_at=now()))
        s.commit()
    calls=[]
    def suppress(state,runtime):
        calls.append(state)
        return {'approved':False,'confidence':0.51,'model':'liquid/d1','context_hash':'a'*64}
    monkeypatch.setattr('atlas.initiative_decision.evaluate',suppress)
    scheduler_tick();scheduler_tick()
    with sessions()() as s:
        assert s.scalar(select(func.count()).select_from(Initiative))==0
        decision=s.get(InitiativeDecision,'event:'+s.scalar(select(Event.id).where(Event.key=='diary-event-1')))
        assert decision.status=='SUPPRESSED' and decision.confidence==0.51
    assert s.scalar(select(func.count()).select_from(Audit).where(Audit.event=='initiative_decision'))==1
    assert len(calls)==1
    assert calls[0]['recent_messages']==[]


def test_initiative_cards_can_complete_snooze_and_dismiss_without_deleting_history(client):
    prefs(client)
    due=(now()+timedelta(minutes=5)).isoformat()
    item=client.post('/api/initiatives',json={'message':'Пересмотреть проект','due_at':due}).json()
    assert item['conversation_id'] is None
    assert client.post('/api/initiatives/'+item['id']+'/snooze',json={'hours':2}).status_code==422
    assert client.post('/api/initiatives/'+item['id']+'/snooze',json={'hours':4}).json()['status']=='SNOOZED'
    completed=client.post('/api/initiatives/'+item['id']+'/complete')
    assert completed.status_code==200 and completed.json()['status']=='COMPLETED'
    assert client.post('/api/initiatives/'+item['id']+'/dismiss').status_code==409
    assert client.get('/api/initiatives').json()[0]['id']==item['id']
