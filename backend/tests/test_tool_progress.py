import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from atlas.answer_stream import publish_tool, tool_events
from atlas.db import sessions
from atlas.models import Task, ToolCall, ToolEvent, now
from atlas.tools import REGISTRY
from atlas.worker import tick
from test_agent import create, search_step, note_step


def test_progress_reaches_websocket_before_tool_finishes(client,monkeypatch):
    entered,finish=threading.Event(),threading.Event()
    original=REGISTRY['memory.search']
    def slow(session,call):
        entered.set()
        assert finish.wait(10)
        return original.execute(session,call)
    monkeypatch.setitem(REGISTRY,'memory.search',replace(original,execute=slow))
    task=create(client,[search_step()])
    worker=threading.Thread(target=tick)
    with client.websocket_connect('/api/events',headers={'origin':'http://testserver','host':'testserver'}) as socket:
        assert socket.receive_json()['type']=='tasks'
        worker.start()
        try:
            assert entered.wait(10)
            events=[]
            for _ in range(12):
                payload=socket.receive_json()
                if payload['type']=='tool_events':
                    events=payload['events']
                    if any(e['status']=='STARTED' for e in events):break
            assert any(e['type']=='tool_call' for e in events)
            assert events[-1]['status']=='STARTED' and events[-1]['tool']=='memory.search'
            assert set(events[-1])=={'id','task_id','call_id','type','tool','status','step','total'}
            assert not finish.is_set()
        finally:finish.set();worker.join(10)
    detail=client.get('/api/tasks/'+task['id']).json()
    assert detail['status']=='COMPLETED'
    assert detail['tool_events'][-1]['status']=='SUCCEEDED'


def test_approval_dry_run_and_denied_progress(client):
    waiting=create(client,[note_step('Текст')]);tick()
    assert client.get('/api/tasks/'+waiting['id']).json()['tool_events'][-1]['status']=='WAITING_APPROVAL'
    preview=create(client,[search_step()],True);tick()
    assert client.get('/api/tasks/'+preview['id']).json()['tool_events'][-1]['status']=='DRY_RUN'
    client.put('/api/tools/memory.search/permission',json={'allowed':False})
    denied=create(client,[search_step()]);tick()
    assert client.get('/api/tasks/'+denied['id']).json()['tool_events'][-1]['status']=='BLOCKED'


def test_stale_claim_expiry_and_cancel_never_publish_progress(client):
    task=create(client,[search_step()])
    with sessions()() as session:
        row=session.get(Task,task['id']);row.status='RUNNING';row.claim='current';row.lease_until=now()+timedelta(seconds=20)
        call=ToolCall(task_id=row.id,step=0,tool='memory.search',arguments={'query':'Atlas'})
        session.add(call);session.flush()
        with pytest.raises(ValueError):publish_tool(session,row,call,'obsolete','STARTED')
        publish_tool(session,row,call,'current','STARTED');session.commit()
        assert tool_events(session,[row.id])[-1]['status']=='STARTED'
        row.lease_until=now()-timedelta(seconds=1);session.commit()
        assert tool_events(session,[row.id])==[]
        row.lease_until=now()+timedelta(seconds=20)
        row.claim='replacement';session.commit()
        assert tool_events(session,[row.id])==[]
        row.lease_until=now()-timedelta(seconds=1)
        with pytest.raises(ValueError):publish_tool(session,row,call,'replacement','STARTED')
        row.status='CANCELLED';session.commit()
        assert tool_events(session,[row.id])==[]
        assert len(session.scalars(select(ToolEvent)).all())==1
