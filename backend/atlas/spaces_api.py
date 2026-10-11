"""Owner-only пространства: архивирование не удаляет разговоры и результаты."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, delete
from .api import DB, Secure, dto
from .models import Space, SpaceConversation, SpaceMemory, Conversation, Memory, Task, Note, ToolCall, Audit
from .memory import contains_secret

router = APIRouter(prefix='/api/spaces')


class SpaceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1000)
    archived: bool = False

    @field_validator('name', 'description')
    @classmethod
    def safe_text(cls, value):
        if contains_secret(value):
            raise ValueError('Секреты запрещены')
        value = value.strip()
        return value


def space(session, sid):
    row = session.scalar(select(Space).where(Space.id == sid).with_for_update())
    if row is None:
        raise HTTPException(404, 'Пространство не найдено')
    return row


def audit(session, event, sid, **details):
    session.add(Audit(event=event, details={'space_id': sid, **details}))
    session.commit()


@router.get('')
def listing(session: DB, _: Secure, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    return [dto(row) for row in session.scalars(select(Space).order_by(Space.created_at.desc()).limit(limit).offset(offset))]


@router.post('', status_code=201)
def create(body: SpaceInput, session: DB, _: Secure):
    if not body.name:
        raise HTTPException(422, 'Укажите название')
    row = Space(**body.model_dump())
    session.add(row)
    session.flush()
    audit(session, 'space_created', row.id)
    return dto(row)


@router.put('/{sid}')
def change(sid: str, body: SpaceInput, session: DB, _: Secure):
    row = space(session, sid)
    if not body.name:
        raise HTTPException(422, 'Укажите название')
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    audit(session, 'space_updated', sid, archived=row.archived)
    return dto(row)


@router.get('/{sid}')
def detail(sid: str, session: DB, _: Secure):
    row = space(session, sid)
    cids = select(SpaceConversation.conversation_id).where(SpaceConversation.space_id == sid)
    conversations = session.scalars(select(Conversation).where(Conversation.id.in_(cids)).order_by(Conversation.created_at.desc())).all()
    tasks = select(Task.id).where(Task.conversation_id.in_(cids))
    notes = session.scalars(select(Note).join(ToolCall, Note.call_id == ToolCall.id)
                           .where(ToolCall.task_id.in_(tasks)).order_by(Note.created_at.desc()).limit(100)).all()
    memories = session.scalars(select(Memory).join(SpaceMemory, SpaceMemory.memory_id == Memory.id)
                              .where(SpaceMemory.space_id == sid).order_by(Memory.id)).all()
    return {'space': dto(row), 'conversations': [dto(c) for c in conversations],
            'tasks': [dto(t) for t in session.scalars(select(Task).where(Task.conversation_id.in_(cids)).order_by(Task.created_at.desc()).limit(100))],
            'notes': [dto(n) for n in notes], 'memories': [dto(m) for m in memories]}


@router.put('/{sid}/conversations/{cid}')
def attach_conversation(sid: str, cid: str, session: DB, _: Secure):
    if space(session, sid).archived:
        raise HTTPException(409, 'Сначала восстановите пространство')
    if session.scalar(select(Conversation).where(Conversation.id == cid).with_for_update()) is None:
        raise HTTPException(404, 'Разговор не найден')
    link = session.get(SpaceConversation, cid)
    if link and link.space_id != sid:
        raise HTTPException(409, 'Разговор уже в другом пространстве. Сначала открепите его.')
    if not link:
        session.add(SpaceConversation(conversation_id=cid, space_id=sid))
        audit(session, 'space_conversation_attached', sid, conversation_id=cid)
    return {'ok': True}


@router.delete('/{sid}/conversations/{cid}')
def detach_conversation(sid: str, cid: str, session: DB, _: Secure):
    space(session, sid)
    session.execute(delete(SpaceConversation).where(SpaceConversation.space_id == sid, SpaceConversation.conversation_id == cid))
    audit(session, 'space_conversation_detached', sid, conversation_id=cid)
    return {'ok': True}


@router.put('/{sid}/memory/{mid}')
def attach_memory(sid: str, mid: str, session: DB, _: Secure):
    if space(session, sid).archived:
        raise HTTPException(409, 'Сначала восстановите пространство')
    if session.get(Memory, mid) is None:
        raise HTTPException(404, 'Воспоминание не найдено')
    if session.get(SpaceMemory, (sid, mid)) is None:
        count = len(session.scalars(select(SpaceMemory.memory_id).where(SpaceMemory.space_id == sid)).all())
        if count >= 5:
            raise HTTPException(409, 'Контекст ограничен пятью записями')
        session.add(SpaceMemory(space_id=sid, memory_id=mid))
        audit(session, 'space_memory_attached', sid, memory_id=mid)
    return {'ok': True}


@router.delete('/{sid}/memory/{mid}')
def detach_memory(sid: str, mid: str, session: DB, _: Secure):
    space(session, sid)
    session.execute(delete(SpaceMemory).where(SpaceMemory.space_id == sid, SpaceMemory.memory_id == mid))
    audit(session, 'space_memory_detached', sid, memory_id=mid)
    return {'ok': True}
