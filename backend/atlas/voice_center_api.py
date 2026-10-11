"""Все owner endpoints требуют cookie/CSRF; SSE повторно проверяет сессию."""
import asyncio
import json
from datetime import timedelta
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .api import DB, Secure, valid_session
from .db import sessions
from .models import Audit, Device, VoiceControl, VoiceEvent, VoiceStation, now
from .voice_center import CenterPolicy, snapshot, station

router = APIRouter(prefix='/api/voice')


class PolicyUpdate(BaseModel):
    revision: int
    policy: CenterPolicy


class ControlInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str
    scenario: str = Field(default='', max_length=80)


@router.get('/center')
def center(session: DB, _: Secure):
    return snapshot(session)


@router.get('/capabilities')
def capabilities(session: DB, _: Secure):
    from .capabilities import LOCAL
    from .skills import catalog
    return [c.public() for c in LOCAL.values()] + [t.capability.public() for t in catalog(session).values()]


@router.put('/devices/{device_id}/settings')
def settings(device_id: str, body: PolicyUpdate, session: DB, _: Secure):
    from .memory import contains_secret
    if contains_secret(json.dumps(body.policy.model_dump(), ensure_ascii=False)): raise HTTPException(422, 'Секреты запрещены')
    from .desktop import lock
    lock(session)
    if not session.get(Device, device_id): raise HTTPException(404, 'Устройство не найдено')
    row = station(session, device_id)
    if row.revision != body.revision: raise HTTPException(409, 'Настройки изменились в другой сессии. Перечитайте их.')
    row.policy = body.policy.model_dump()
    row.revision += 1
    if not row.policy['store_transcripts'] or row.policy['network_mode'] == 'local_only':
        row.state = {k: v for k, v in row.state.items() if k not in {'transcript', 'context'}}
        for event in session.scalars(select(VoiceEvent).where(VoiceEvent.device_id == device_id)):
            event.data = {k: v for k, v in event.data.items() if k not in {'transcript', 'context'}}
    session.add(Audit(event='voice_settings_changed', details={'device_id': device_id, 'revision': row.revision}))
    session.commit()
    return {'revision': row.revision, 'policy': row.policy}


@router.post('/devices/{device_id}/control', status_code=202)
def control(device_id: str, body: ControlInput, session: DB, _: Secure):
    device = session.get(Device, device_id)
    row = session.get(VoiceStation, device_id)
    if not device or not device.enabled or not device.voice_enabled: raise HTTPException(403, 'Голосовой доступ отключён')
    if not row or (now() - row.last_seen.replace(tzinfo=row.last_seen.tzinfo or now().tzinfo)).total_seconds() > 12:
        raise HTTPException(409, 'Локальный слушатель не подключён')
    if body.kind not in {'stop', 'cancel', 'repeat', 'reject', 'mute', 'unmute', 'test_scenario', 'hardware'}:
        raise HTTPException(422, 'Неизвестная команда')
    if body.kind == 'test_scenario' and not body.scenario: raise HTTPException(422, 'Выберите сценарий')
    payload = {'scenario': body.scenario}
    if body.kind == 'cancel':
        from .models import VoiceRequest
        task_id = row.state.get('task_id')
        if not isinstance(task_id, str): raise HTTPException(409, 'Нет голосовой задачи для отмены')
        receipt = session.get(VoiceRequest, task_id)
        if not receipt or receipt.device_id != device_id: raise HTTPException(409, 'Нет голосовой задачи для отмены')
        payload['task_id'] = task_id
    if body.kind in {'mute', 'unmute'}:
        row.policy = {**row.policy, 'enabled': body.kind == 'unmute'}
        row.revision += 1
    pending = session.scalar(select(VoiceControl).where(VoiceControl.device_id == device_id, VoiceControl.kind == body.kind,
                              VoiceControl.status.in_(['PENDING', 'DISPATCHED', 'RUNNING']), VoiceControl.expires_at > now()))
    if pending: raise HTTPException(409, 'Команда уже ожидает результата')
    command = VoiceControl(device_id=device_id, kind=body.kind, payload=payload, expires_at=now() + timedelta(seconds=30))
    session.add(command)
    session.flush()
    session.add(Audit(event='voice_control_requested', details={'device_id': device_id, 'kind': body.kind, 'id': command.id}))
    session.commit()
    return {'id': command.id, 'status': command.status}


@router.get('/events')
def events(request: Request, _: Secure):
    async def generate():
        previous = None
        for _ in range(1200):
            if await request.is_disconnected() or not await asyncio.to_thread(valid_session, request.cookies.get('atlas_session')):
                return
            def read():
                with sessions()() as session: return json.dumps(snapshot(session), ensure_ascii=False, default=str)
            value = await asyncio.to_thread(read)
            if value != previous:
                yield 'data: ' + value + '\n\n'
                previous = value
            else:
                yield ': heartbeat\n\n'
            await asyncio.sleep(0.25)
    return StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
