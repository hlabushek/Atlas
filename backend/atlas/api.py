import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Annotated,Literal
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import settings
from .db import db, sessions
from .models import Conversation, Message, Memory, Task, Audit, now
from .memory import CATEGORIES, contains_secret, retrieve

@asynccontextmanager
async def lifespan(app):
    settings()  # Fail closed on missing credentials before accepting requests.
    yield

app = FastAPI(title="Atlas", version="0.1.0", lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
@app.exception_handler(RequestValidationError)
async def invalid_input(request,exc):return JSONResponse(status_code=422,content={"detail":"Некорректные данные запроса"})
DB = Annotated[Session, Depends(db)]
failures: defaultdict[str, deque[float]] = defaultdict(deque)

def serializer(): return URLSafeTimedSerializer(settings().session_secret, salt="atlas-owner-v1")
def valid_session(token):
    try:
        from .models import OwnerSession
        from .notifications import utc
        identifier=serializer().loads(token or "",max_age=86400)
        with sessions()() as session:
            row=session.get(OwnerSession,identifier)
            return bool(row and not row.revoked and utc(row.expires_at)>now())
    except (BadSignature, SignatureExpired): return False

def owner(request: Request):
    if not valid_session(request.cookies.get("atlas_session")): raise HTTPException(401, "Войдите в Atlas")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("X-Atlas-Client") != "web":
        raise HTTPException(403, "Missing CSRF header")

Secure = Annotated[None, Depends(owner)]
class Login(BaseModel): password: str = Field(max_length=1000)
class NewConversation(BaseModel): title: str = Field(default="Новый разговор", min_length=1, max_length=100)
class Send(BaseModel):
    model_role: Literal["auto","main","planner"] = "main"
    attachment_ids: list[str] = Field(default_factory=list,max_length=5)
    initiative_id: str | None = None
    content: str = Field(default='', max_length=16000)
    request_key: str = Field(pattern=r"^[0-9a-f-]{36}$")
class MemoryInput(BaseModel):
    category: str = "PROFILE"
    content: str = Field(min_length=1, max_length=4000)

def dto(row):
    result = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    if isinstance(row, Task):
        result['attachments'] = [{k: v for k, v in a.items() if k != 'text'} for a in (row.attachments or [])]
    return result

def ensure_conversation(session, cid, *, writable=False):
    if session.get(Conversation, cid) is None: raise HTTPException(404, "Разговор не найден")
    if writable:
        from .conversations import writable as require_writable
        try:require_writable(session,cid)
        except LookupError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
    return session.get(Conversation,cid)

@app.get("/api/health")
def health(): return {"status": "ok"}

@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    host = request.client.host if request.client else "unknown"
    recent = failures[host]
    stamp = time.monotonic()
    while recent and recent[0] < stamp - 60: recent.popleft()
    if len(recent) >= 5: raise HTTPException(429, "Повторите через минуту")
    digest = lambda value: hashlib.sha256(value.encode()).digest()
    if not hmac.compare_digest(digest(body.password), digest(settings().owner_password)):
        recent.append(stamp)
        raise HTTPException(401, "Неверный пароль")
    recent.clear()
    from .models import OwnerSession
    from datetime import timedelta
    with sessions()() as session:
        row=OwnerSession(expires_at=now()+timedelta(days=1));session.add(row);session.commit();identifier=row.id
    response.set_cookie("atlas_session", serializer().dumps(identifier), httponly=True,
        secure=settings().cookie_secure, samesite="strict", max_age=86400)
    return {"authenticated": True}

@app.post("/api/logout")
def logout(request: Request, response: Response, session: DB, _: Secure):
    from .models import OwnerSession
    row=session.get(OwnerSession,serializer().loads(request.cookies['atlas_session']))
    row.revoked=True;session.commit()
    response.delete_cookie("atlas_session")
    return {"ok": True}

@app.get("/api/settings")
def config(_: Secure):
    from .model_settings import resolve
    from .embeddings import enabled
    r=resolve()
    return {"provider":r.provider,"model":r.model,"model_ready":r.ready,"max_output_tokens":r.max_output_tokens,
            "stage":"6/7","memory_search":"hybrid" if enabled() else "keyword","external_tools":True,"builtin_tools":True}

@app.get("/api/conversations")
def conversations(session: DB, _: Secure, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), scope: Literal['active','archive','trash','all']='active', q: str = Query('',max_length=200)):
    statement=select(Conversation)
    if scope=='active':statement=statement.where(Conversation.trashed_at.is_(None),Conversation.archived==False)
    elif scope=='archive':statement=statement.where(Conversation.trashed_at.is_(None),Conversation.archived==True)
    elif scope=='trash':statement=statement.where(Conversation.trashed_at.is_not(None))
    if q:
        pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        statement=statement.where(Conversation.title.ilike(pattern,escape='\\') | Conversation.description.ilike(pattern,escape='\\'))
    statement=statement.order_by(Conversation.pinned.desc(),Conversation.created_at.desc(),Conversation.id).limit(limit).offset(offset)
    return [dto(c) for c in session.scalars(statement)]

@app.post("/api/conversations")
def create_conversation(body: NewConversation, session: DB, _: Secure):
    if not body.title.strip() or contains_secret(body.title):raise HTTPException(422,'Некорректное название')
    row = Conversation(title=body.title)
    session.add(row); session.commit()
    return dto(row)

@app.get("/api/conversations/{cid}/messages")
def messages(cid: str, session: DB, _: Secure, limit: int = Query(100, ge=1, le=200), before: datetime | None = None):
    ensure_conversation(session, cid)
    statement=select(Message).where(Message.conversation_id == cid)
    if before is not None:statement=statement.where(Message.created_at < before)
    rows=session.scalars(statement.order_by(Message.created_at.desc()).limit(limit)).all()
    return [dto(m) for m in reversed(rows)]

@app.post("/api/conversations/{cid}/messages", status_code=202)
def send(cid: str, body: Send, session: DB, _: Secure):
    if contains_secret(body.content): raise HTTPException(422, 'Секреты нельзя отправлять модели')
    if not body.content.strip() and not body.attachment_ids: raise HTTPException(422, 'Добавьте текст или файл')
    ensure_conversation(session, cid, writable=True)
    from .files import fingerprint, snapshot, MAX_CONTEXT
    from .models import Attachment
    from .chat_routing import chat_profile, requested_role
    identity = fingerprint(body)
    existing = session.scalar(select(Task).where(Task.conversation_id == cid, Task.request_key == body.request_key))
    def matches(t):
        if t is None or t.kind != 'chat': return False
        if t.request_fingerprint: return t.request_fingerprint == identity
        return not body.attachment_ids and t.goal == body.content and requested_role(t.model_profile) == body.model_role
    if existing:
        if not matches(existing): raise HTTPException(409, 'Ключ отправки уже использован для другого сообщения')
        return dto(existing)
    attachments = []
    for identifier in dict.fromkeys(body.attachment_ids):
        a = session.get(Attachment, identifier)
        if a is None or a.deleted_at is not None: raise HTTPException(404, 'Файл не найден')
        if contains_secret(a.content): raise HTTPException(422, 'Секреты нельзя отправлять модели')
        attachments.append(snapshot(a))
    if len(body.content.encode()) + sum(len(a['text'].encode()) for a in attachments) > MAX_CONTEXT:
        raise HTTPException(413, 'Общий объём текста превышает 100 КБ. Разделите файлы на несколько сообщений')
    question = body.content.strip() or 'Проанализируй приложенные файлы'
    from .spaces import context_ids
    recent = session.scalars(select(Message).where(Message.conversation_id == cid).order_by(Message.created_at.desc()).limit(12)).all()
    needs_vision = any(a['mime'].startswith('image/') for a in attachments) or any(
        a.get('mime', '').startswith('image/') for m in recent for a in (m.attachments or []))
    role = body.model_role
    if needs_vision:
        from .file_models import choose
        role = choose(session, role)
    profile = dict(chat_profile(session, question, role), space_memory_ids=context_ids(session, cid), vision=needs_vision)
    if needs_vision:
        profile.update(requested_role=body.model_role, routing_reason='image_input' if attachments else 'image_context')
    task = Task(conversation_id=cid, request_key=body.request_key, goal=question, model_profile=profile,
                attachments=attachments, request_fingerprint=identity)
    session.add(task)
    try:
        session.flush()
        meta = [{k: v for k, v in a.items() if k != 'text'} for a in attachments]
        message = Message(conversation_id=cid, role='user', content=body.content.strip(), created_at=task.created_at,
                          attachments=meta, attachment_task_id=task.id if attachments else None)
        session.add(message); session.flush()
        from .notifications import link_reply
        if body.initiative_id:
            from .models import Initiative
            target = session.get(Initiative, body.initiative_id)
            if target is None or target.conversation_id != cid: raise HTTPException(422, 'Инициатива не относится к разговору')
        message.initiative_id = link_reply(session, cid, message.id, body.initiative_id)
        session.add(Audit(task_id=task.id, event='message_queued', details={'role': profile['role'],
                          'routing_reason': profile['routing_reason'], 'attachment_ids': [a['id'] for a in attachments]}))
        session.commit()
    except IntegrityError:
        session.rollback()
        task = session.scalar(select(Task).where(Task.conversation_id == cid, Task.request_key == body.request_key))
        if not matches(task): raise HTTPException(409, 'Конфликт отправки сообщения')
    return dto(task)

@app.get("/api/tasks")
def tasks(session: DB, _: Secure, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    statement=select(Task).order_by(Task.created_at.desc()).limit(limit).offset(offset)
    return [dto(t) for t in session.scalars(statement)]

@app.post("/api/tasks/{tid}/cancel")
def cancel(tid: str, session: DB, _: Secure):
    from .task_controls import cancel_task
    try: found = cancel_task(session, tid)
    except LookupError: raise HTTPException(404, 'Задача не найдена')
    session.commit()
    return dto(found)

@app.get("/api/memory")
def memories(session: DB, _: Secure, q: str = "", limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    if len(q)>4000:raise HTTPException(422,'Запрос слишком длинный')
    from .embeddings import warm
    try:warm(q)
    except Exception:pass # keyword retrieval remains available during provider failure
    rows = retrieve(session, q, limit, offset) if q else session.scalars(select(Memory).order_by(Memory.created_at.desc()).limit(limit).offset(offset))
    return [dto(m) for m in rows]

@app.post("/api/memory")
def add_memory(body: MemoryInput, session: DB, _: Secure):
    if body.category not in CATEGORIES: raise HTTPException(422, "Неизвестная категория")
    if contains_secret(body.content): raise HTTPException(422, "Секреты нельзя сохранять в памяти")
    row = Memory(category=body.category, content=body.content)
    session.add(row); session.add(Audit(event="memory_created")); session.commit()
    return dto(row)

@app.put("/api/memory/{mid}")
def edit_memory(mid: str, body: MemoryInput, session: DB, _: Secure):
    row = session.get(Memory, mid)
    if row is None: raise HTTPException(404, "Воспоминание не найдено")
    if body.category not in CATEGORIES or contains_secret(body.content): raise HTTPException(422, "Некорректная память")
    from .models import MemoryFact,MemoryConflict,Embedding
    session.execute(delete(Embedding).where(Embedding.memory_id==mid))
    session.execute(delete(MemoryConflict).where((MemoryConflict.left_id==mid)|(MemoryConflict.right_id==mid)))
    session.execute(delete(MemoryFact).where(MemoryFact.memory_id==mid))
    row.content, row.category, row.confirmed_at = body.content, body.category, now()
    session.add(Audit(event="memory_updated", details={"id": mid})); session.commit()
    return dto(row)

@app.delete("/api/memory/{mid}")
def remove_memory(mid: str, session: DB, _: Secure):
    row = session.get(Memory, mid)
    if row is None: raise HTTPException(404, "Воспоминание не найдено")
    from .models import Embedding,MemoryFact,MemoryConflict
    from .models import SpaceMemory
    session.execute(delete(SpaceMemory).where(SpaceMemory.memory_id == mid))
    session.execute(delete(Embedding).where(Embedding.memory_id==mid))
    session.execute(delete(MemoryConflict).where((MemoryConflict.left_id==mid)|(MemoryConflict.right_id==mid)))
    session.execute(delete(MemoryFact).where(MemoryFact.memory_id==mid))
    session.delete(row); session.add(Audit(event="memory_deleted", details={"id": mid})); session.commit()
    return {"ok": True}

@app.get("/api/export")
def export(session: DB, _: Secure):
    from .models import Space, SpaceConversation, SpaceMemory
    return {"version": 1, "memory": [dto(m) for m in session.scalars(select(Memory))],
        "spaces": [dto(s) for s in session.scalars(select(Space))],
        "space_conversations": [dto(s) for s in session.scalars(select(SpaceConversation))],
        "space_memories": [dto(s) for s in session.scalars(select(SpaceMemory))],
        "conversations": [dto(c) for c in session.scalars(select(Conversation))],
        "messages": [dto(m) for m in session.scalars(select(Message))]}

@app.get("/api/audit")
def audit(session: DB, _: Secure, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    statement=select(Audit).order_by(Audit.created_at.desc()).limit(limit).offset(offset)
    return [dto(a) for a in session.scalars(statement)]

@app.websocket("/api/events")
async def events(ws: WebSocket):
    # Exact origin match prevents cross-site WebSocket hijacking.
    origin = ws.headers.get("origin", "")
    host = ws.headers.get("host", "")
    if origin not in {"http://" + host, "https://" + host} or not valid_session(ws.cookies.get("atlas_session")):
        await ws.close(code=1008); return
    await ws.accept()
    previous_progress = []
    try:
        while True:
            if not await asyncio.to_thread(valid_session,ws.cookies.get('atlas_session')):
                await ws.close(code=1008);return
            def snapshot():
                with sessions()() as s:
                    from .answer_stream import tool_events
                    tasks = [{"id":t.id,"conversation_id":t.conversation_id,"status":t.status,
                              "error":t.error,"current_step":t.current_step} for t in
                             s.scalars(select(Task).order_by(Task.created_at.desc()).limit(100))]
                    return tasks, tool_events(s, [t['id'] for t in tasks])
            tasks, progress = await asyncio.to_thread(snapshot)
            # Full status snapshots reconcile late owner HTTP responses after
            # pause/resume; progress itself is sent only when its payload changes.
            await ws.send_json({"type":"tasks","tasks":tasks})
            if progress != previous_progress:
                await ws.send_json({"type":"tool_events","events":progress})
                previous_progress = progress
            await asyncio.sleep(0.25)
    except (WebSocketDisconnect, RuntimeError): pass

from .task_api import router as task_router
app.include_router(task_router)

from .channels_api import router as channels_router
app.include_router(channels_router)

from .skills_api import router as skills_router
app.include_router(skills_router)

from .desktop_api import router as desktop_router
app.include_router(desktop_router)

from .integrations_api import router as integrations_router
app.include_router(integrations_router)

from .model_settings_api import router as model_settings_router
app.include_router(model_settings_router)

from .connectors import router as connectors_router
app.include_router(connectors_router)

from .connector_actions import router as connector_actions_router
app.include_router(connector_actions_router)

from .voice_center_api import router as voice_center_router
app.include_router(voice_center_router)
from .browser_voice import router as browser_voice_router
app.include_router(browser_voice_router)

from .spaces_api import router as spaces_router
app.include_router(spaces_router)

from .conversation_api import router as conversation_router
app.include_router(conversation_router)

from .maintenance_api import router as maintenance_router
app.include_router(maintenance_router)

from .files_api import router as files_router
app.include_router(files_router)
