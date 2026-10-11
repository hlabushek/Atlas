import uuid
from dataclasses import replace
from datetime import timedelta
import pytest
from atlas.db import sessions
from atlas.models import Task, ToolCall, now
from atlas.worker import tick
from atlas.tools import REGISTRY
from test_slice import conversation


def create(client, plan=None, dry_run=False):
    body = {"conversation_id": conversation(client), "request_key": str(uuid.uuid4()),
            "goal": "Проект Atlas", "dry_run": dry_run}
    if plan is not None:
        body["plan"] = plan
    response = client.post("/api/tasks", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def search_step():
    return {"tool": "memory.search", "arguments": {"query": "Atlas"}}


def note_step(content="$previous"):
    return {"tool": "notes.create", "arguments": {"title": "Учебный план", "content": content}}


def detail(client, task):
    return client.get('/api/tasks/' + task['id']).json()


def approve(client):
    approval = client.get('/api/approvals').json()[0]
    assert client.post('/api/approvals/' + approval['id'], json={"approve": True}).status_code == 200
    return approval


def expire(task):
    with sessions()() as s:
        row = s.get(Task, task['id'])
        row.status = 'RUNNING'
        row.claim = 'dead'
        row.lease_until = now() - timedelta(seconds=1)
        s.commit()


def test_multistep_approval_and_actual_note(client):
    client.post('/api/memory', json={"category": "PROJECTS", "content": "Atlas помогает учиться"})
    task = create(client, [search_step(), note_step()])
    tick()
    assert detail(client, task)['current_step'] == 1
    tick()
    assert detail(client, task)['status'] == 'WAITING_APPROVAL'
    assert client.get('/api/notes').json() == []
    approval = approve(client)
    tick()
    assert detail(client, task)['status'] == 'COMPLETED'
    notes = client.get('/api/notes').json()
    assert len(notes) == 1 and 'помогает учиться' in notes[0]['content']
    assert client.post('/api/approvals/' + approval['id'], json={"approve": True}).status_code == 409
    assert not tick()


def test_demo_planner(client):
    task = create(client)
    tick()
    assert len(detail(client, task)['plan']) == 2
    tick()
    assert detail(client, task)['status'] == 'WAITING_APPROVAL'


def test_dry_run(client):
    task = create(client, [search_step(), note_step()], True)
    tick()
    tick()
    result = detail(client, task)
    assert result['status'] == 'COMPLETED'
    assert all(c['status'] == 'DRY_RUN' for c in result['calls'])
    assert client.get('/api/notes').json() == []
    assert client.get('/api/approvals').json() == []


def test_permission_denied_is_audited(client):
    client.put('/api/tools/memory.search/permission', json={"allowed": False})
    task = create(client, [search_step()])
    tick()
    assert detail(client, task)['status'] == 'FAILED'
    assert detail(client, task)['calls'][0]['status'] == 'BLOCKED'
    assert any(a['event'] == 'permission_denied' for a in client.get('/api/audit').json())


def test_revoke_after_approval(client):
    task = create(client, [note_step('data')])
    tick()
    approve(client)
    client.put('/api/tools/notes.create/permission', json={"allowed": False})
    tick()
    assert detail(client, task)['status'] == 'FAILED'
    assert client.get('/api/notes').json() == []


def test_pause_resume_and_denial(client):
    task = create(client, [search_step()])
    assert client.post('/api/tasks/' + task['id'] + '/pause').status_code == 200
    assert not tick()
    assert client.post('/api/tasks/' + task['id'] + '/resume').status_code == 200
    tick()
    assert detail(client, task)['status'] == 'COMPLETED'
    task = create(client, [note_step('data')])
    tick()
    approval = client.get('/api/approvals').json()[0]
    client.post('/api/approvals/' + approval['id'], json={"approve": False})
    assert detail(client, task)['status'] == 'FAILED' and not tick()


def test_recovery_skips_committed_steps(client, monkeypatch):
    calls = []
    original = REGISTRY['memory.search']
    def counted(s, call):
        calls.append(call.id)
        return original.execute(s, call)
    monkeypatch.setitem(REGISTRY, 'memory.search', replace(original, execute=counted))
    task = create(client, [search_step(), note_step()])
    tick()
    expire(task)
    tick()
    approve(client)
    tick()
    assert len(calls) == 1
    assert len(client.get('/api/notes').json()) == 1
    assert detail(client, task)['status'] == 'COMPLETED'


def test_atomic_write_crash_rolls_back(client, monkeypatch):
    class Crash(BaseException):
        pass
    original = REGISTRY['notes.create']
    def crashing(s, call):
        original.execute(s, call)
        raise Crash()
    task = create(client, [note_step('data')])
    tick()
    approve(client)
    monkeypatch.setitem(REGISTRY, 'notes.create', replace(original, execute=crashing))
    with pytest.raises(Crash):
        tick()
    assert client.get('/api/notes').json() == []
    expire(task)
    monkeypatch.setitem(REGISTRY, 'notes.create', original)
    tick()
    assert len(client.get('/api/notes').json()) == 1
    assert detail(client, task)['status'] == 'COMPLETED'


def test_ambiguous_action_never_replayed(client):
    task = create(client, [search_step()])
    expire(task)
    with sessions()() as s:
        s.add(ToolCall(task_id=task['id'], step=0, tool='memory.search',
                       arguments={'query': 'Atlas'}, status='STARTED'))
        s.commit()
    tick()
    assert detail(client, task)['status'] == 'PAUSED'
    assert detail(client, task)['calls'][0]['status'] == 'UNKNOWN'
    assert client.post('/api/tasks/' + task['id'] + '/resume').status_code == 409
    assert not tick()


def test_safe_retry_bounded(client, monkeypatch):
    def fail(*args):
        raise RuntimeError('token=secret-never-log')
    original = REGISTRY['memory.search']
    monkeypatch.setitem(REGISTRY, 'memory.search', replace(original, execute=fail))
    task = create(client, [search_step()])
    tick()
    tick()
    tick()
    result = detail(client, task)
    assert result['status'] == 'FAILED' and result['calls'][0]['attempts'] == 3
    assert 'secret-never-log' not in str(result)


def test_unknown_tool_and_budget_rejected(client):
    body = {"conversation_id": conversation(client), "request_key": str(uuid.uuid4()),
            "goal": "goal", "plan": [{"tool": "shell.exec", "arguments": {"cmd": "echo hi"}}]}
    assert client.post('/api/tasks', json=body).status_code == 422
    body['plan'] = [search_step()] * 9
    assert client.post('/api/tasks', json=body).status_code == 422


def test_cancel_waiting_approval(client):
    task = create(client, [note_step('data')])
    tick()
    approval = client.get('/api/approvals').json()[0]
    client.post('/api/tasks/' + task['id'] + '/cancel')
    assert client.post('/api/approvals/' + approval['id'], json={"approve": True}).status_code == 409
    assert detail(client, task)['status'] == 'CANCELLED' and not tick()


def test_request_fingerprint_covers_original_plan(client):
    body={"conversation_id":conversation(client),"request_key":str(uuid.uuid4()),"goal":"Atlas","plan":[search_step()]}
    task=client.post('/api/tasks',json=body).json()
    tick()
    assert client.post('/api/tasks',json=body).json()['id']==task['id']
    body['plan']=[]
    assert client.post('/api/tasks',json=body).status_code==409

def test_two_workers_never_duplicate_action(client):
    from concurrent.futures import ThreadPoolExecutor
    task=create(client,[note_step('data')])
    tick();approve(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:tick(),range(2)))
    assert len(client.get('/api/notes').json())==1
    assert detail(client,task)['status']=='COMPLETED'


def test_missing_policy_row_fails_closed(client):
    from sqlalchemy import delete
    from atlas.models import ToolGrant
    with sessions()() as session:
        session.execute(delete(ToolGrant).where(ToolGrant.tool=='memory.search'))
        session.commit()
    task=create(client,[search_step()])
    tick()
    assert detail(client,task)['status']=='FAILED'
    assert detail(client,task)['calls'][0]['status']=='BLOCKED'

def test_chat_key_cannot_alias_agent_task(client):
    body={"conversation_id":conversation(client),"request_key":str(uuid.uuid4()),"goal":"Atlas","plan":[search_step()]}
    assert client.post('/api/tasks',json=body).status_code==202
    response=client.post('/api/conversations/'+body['conversation_id']+'/messages',json={"request_key":body['request_key'],"content":body['goal']})
    assert response.status_code==409
