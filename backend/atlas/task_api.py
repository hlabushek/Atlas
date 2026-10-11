"""Owner controls for durable agent tasks and approvals."""
import hashlib
import json
from datetime import timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .api import DB, Secure, dto, ensure_conversation
from .models import Task, ToolCall, Approval, ToolGrant, ConnectorSource, Note, Audit, now
from .integration_permissions import grant_valid, scope_fingerprint
from .tools import validate_plan
from .memory import contains_secret

router=APIRouter(prefix="/api")
class AgentRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    conversation_id: str
    request_key: str=Field(pattern=r"^[0-9a-f-]{36}$")
    goal: str=Field(min_length=1,max_length=4000)
    plan: list[dict]=Field(default_factory=list,max_length=8)
    dry_run: bool=False
    max_steps: int=Field(default=8,ge=1,le=8)
class Decision(BaseModel):
    approve: bool
class GrantInput(BaseModel):
    allowed: bool
    days: int=Field(default=30,ge=1,le=90)

def locked_task(session,tid):
    task=session.scalar(select(Task).where(Task.id==tid).with_for_update())
    if task is None:raise HTTPException(404,"Задача не найдена")
    return task

def fingerprint(body):
    return hashlib.sha256(json.dumps(body.model_dump(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def same_request(task,body,plan):
    return task.kind=="agent" and task.request_fingerprint==fingerprint(body)

@router.post("/tasks",status_code=202)
def create_agent(body:AgentRequest,session:DB,_:Secure):
    ensure_conversation(session,body.conversation_id,writable=True)
    if contains_secret(body.goal):raise HTTPException(422,"Секреты нельзя отправлять планировщику")
    try:plan=validate_plan(body.plan,body.max_steps) if body.plan else []
    except (ValueError,TypeError):raise HTTPException(422,"Некорректный план или неизвестный инструмент")
    existing=session.scalar(select(Task).where(Task.conversation_id==body.conversation_id,Task.request_key==body.request_key))
    if existing:
        if not same_request(existing,body,plan):raise HTTPException(409,"Request key already used")
        return dto(existing)
    from .model_settings import resolve
    task=Task(model_profile=resolve('planner',session).snapshot(),request_fingerprint=fingerprint(body),conversation_id=body.conversation_id,request_key=body.request_key,goal=body.goal,kind="agent",plan=plan,dry_run=body.dry_run,max_steps=body.max_steps)
    session.add(task)
    try:
        session.flush();session.add(Audit(task_id=task.id,event="agent_queued",details={"dry_run":body.dry_run}));session.commit()
    except IntegrityError:
        session.rollback()
        task=session.scalar(select(Task).where(Task.conversation_id==body.conversation_id,Task.request_key==body.request_key))
        if task is None or not same_request(task,body,plan):raise HTTPException(409,"Request conflict")
    return dto(task)

@router.get("/tasks/{tid}")
def detail(tid:str,session:DB,_:Secure):
    task=session.get(Task,tid)
    if task is None:raise HTTPException(404,"Задача не найдена")
    return {**dto(task),"calls":[dto(c) for c in session.scalars(select(ToolCall).where(ToolCall.task_id==tid).order_by(ToolCall.step))]}

@router.post("/tasks/{tid}/pause")
def pause(tid:str,session:DB,_:Secure):
    task=locked_task(session,tid)
    if task.status not in {"PENDING","PLANNING","RUNNING","WAITING_APPROVAL"}:raise HTTPException(409,"Эту задачу нельзя приостановить")
    task.status="PAUSED";task.claim=None;task.lease_until=None
    session.add(Audit(task_id=tid,event="task_paused"));session.commit()
    return dto(task)

@router.post("/tasks/{tid}/resume")
def resume(tid:str,session:DB,_:Secure):
    task=locked_task(session,tid)
    if task.status!="PAUSED":raise HTTPException(409,"Задача не приостановлена")
    unknown=session.scalar(select(ToolCall).where(ToolCall.task_id==tid,ToolCall.status.in_(["UNKNOWN","STARTED"])))
    if unknown:raise HTTPException(409,"Неизвестный результат действия: автоматический повтор запрещён")
    task.status="PENDING";task.error=None;task.claim=None;task.lease_until=None
    session.add(Audit(task_id=tid,event="task_resumed"));session.commit()
    return dto(task)

@router.get("/approvals")
def approvals(session:DB,_:Secure):
    return [{**dto(a),"call":dto(session.get(ToolCall,a.call_id))} for a in session.scalars(select(Approval).order_by(Approval.created_at.desc()).limit(100))]

@router.post("/approvals/{aid}")
def decide(aid:str,body:Decision,session:DB,_:Secure):
    approval=session.get(Approval,aid)
    if approval is None:raise HTTPException(404,"Подтверждение не найдено")
    task=locked_task(session,approval.task_id)
    session.refresh(approval)
    if approval.status!="PENDING":raise HTTPException(409,"Подтверждение уже обработано")
    call=session.get(ToolCall,approval.call_id)
    if task.status not in {"WAITING_APPROVAL","PAUSED"} or call.status!="WAITING_APPROVAL" or task.current_step!=call.step:
        raise HTTPException(409,"Действие больше не ожидает разрешения")
    grant=session.get(ToolGrant,call.tool)
    source=session.get(ConnectorSource,call.tool[len("connector."):-len(".read")]) if call.tool.startswith("connector.") and call.tool.endswith(".read") else None
    if body.approve and (source is None or not grant_valid(grant,source) if call.tool.startswith("connector.") else grant is not None and not grant_valid(grant)):
        raise HTTPException(403,"Разрешение инструмента отсутствует или истекло")
    approval.status="APPROVED" if body.approve else "DENIED";approval.resolved_at=now()
    if not body.approve:
        task.status="FAILED";task.error="Владелец отклонил действие";call.status="BLOCKED"
    elif task.status=="WAITING_APPROVAL":task.status="PENDING"
    session.add(Audit(task_id=task.id,event="approval_approved" if body.approve else "approval_denied",details={"call_id":call.id}));session.commit()
    return dto(approval)

@router.get("/tools")
def tools(session:DB,_:Secure):
    output=[]
    from .skills import catalog
    for t in catalog(session).values():
        grant=session.get(ToolGrant,t.name)
        source=session.get(ConnectorSource,t.name[len("connector."):-len(".read")]) if t.name.startswith("connector.") and t.name.endswith(".read") else None
        output.append({"name":t.name,"description":t.description,"permission":t.permission,"requires_approval":t.requires_approval,
            "blocked":not grant_valid(grant,source),"expires_at":grant.expires_at if source is not None and grant else None,
            "schema":t.schema.model_json_schema(),"execution":t.execution,"capability":t.capability.public()})
    return output

@router.put("/tools/{name}/permission")
def permission(name:str,body:GrantInput,session:DB,_:Secure):
    from .skills import catalog
    if name not in catalog(session):raise HTTPException(404,"Инструмент не найден")
    source=session.get(ConnectorSource,name[len("connector."):-len(".read")]) if name.startswith("connector.") and name.endswith(".read") else None
    if source is not None and body.allowed and not source.enabled:raise HTTPException(409,"Источник отключён")
    grant=session.get(ToolGrant,name)
    if grant is None:grant=ToolGrant(tool=name,allowed=body.allowed);session.add(grant)
    else:grant.allowed=body.allowed
    if source is not None:
        grant.scope_fingerprint=scope_fingerprint(source) if body.allowed else None
        grant.expires_at=now()+timedelta(days=body.days) if body.allowed else None
    session.add(Audit(event="tool_permission_changed",details={"tool":name,"allowed":body.allowed,"expires_at":grant.expires_at.isoformat() if source is not None and grant.expires_at else None}));session.commit()
    return {"tool":name,"allowed":body.allowed,"expires_at":grant.expires_at if source is not None else None}

@router.get("/notes")
def notes(session:DB,_:Secure):
    return [dto(n) for n in session.scalars(select(Note).order_by(Note.created_at.desc()).limit(100))]
