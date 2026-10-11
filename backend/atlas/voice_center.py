"""Voice Center: проверенные метаданные, одноразовые controls и ограниченное хранение."""
from datetime import timedelta, timezone
import json
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from .capabilities import LOCAL
from .memory import contains_secret
from .models import Audit, Device, Message, Task, VoiceControl, VoiceEvent, VoiceRequest, VoiceStation, now
from .voice_privacy import private_transcript


class CenterPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    activation_mode: str = 'wake_word'
    default_mode: str = 'chat'
    follow_up_seconds: int = Field(default=0, ge=0, le=20)
    wake_words: list[str] = Field(default_factory=lambda: ['атлас'], min_length=1, max_length=5)
    ptt_key: str = Field(default='F8', pattern=r'^(?:F(?:[1-9]|1[0-2])|SPACE)$')
    network_mode: str = 'atlas'
    store_transcripts: bool = True
    speak_sensitive: bool = False
    voice_interrupt_enabled: bool = False
    session_context_seconds: int = Field(default=60, ge=0, le=120)
    confirm_local_actions: bool = True
    local_grants: list[str] = Field(default_factory=lambda: [c.id for c in LOCAL.values()], max_length=20)
    local_grant_expires_at: float = Field(default=0, ge=0)
    scenarios: list[dict] = Field(default_factory=list, max_length=20)

    @field_validator('activation_mode', 'default_mode', 'network_mode')
    @classmethod
    def modes(cls, value, info):
        valid = {'activation_mode': {'wake_word', 'wake_word_followup', 'push_to_talk'},
                 'default_mode': {'chat', 'task'}, 'network_mode': {'atlas', 'local_only'}}
        if value not in valid[info.field_name]: raise ValueError('Неизвестный режим')
        return value

    @field_validator('local_grants')
    @classmethod
    def grants(cls, value):
        if set(value) - {c.id for c in LOCAL.values()}: raise ValueError('Неизвестная возможность')
        return value

    @field_validator('wake_words')
    @classmethod
    def wakes(cls, value):
        if any(not word.strip() or len(word) > 40 or private_transcript(word) for word in value): raise ValueError('Некорректное обращение')
        return value

    @field_validator('scenarios')
    @classmethod
    def scenario_data(cls, value):
        if len(json.dumps(value)) > 20000 or private_transcript(json.dumps(value)):
            raise ValueError('Некорректные сценарии')
        for entry in value:
            if set(entry) - {'name', 'phrases', 'actions', 'enabled'}: raise ValueError('Неизвестное поле сценария')
            if not isinstance(entry.get('actions'), list) or not 1 <= len(entry['actions']) <= 8:
                raise ValueError('Некорректные действия')
            for action in entry['actions']:
                if not isinstance(action, dict) or action.get('type') not in LOCAL:
                    raise ValueError('Запрещённое действие')
                if set(action) - {'type', 'percent', 'muted', 'command', 'site', 'app'}:
                    raise ValueError('Произвольные команды запрещены')
        return value


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def station(session, device_id):
    row = session.get(VoiceStation, device_id)
    if row is None:
        from .desktop import lock
        lock(session)
        row = session.get(VoiceStation, device_id)
    if row is None:
        row = VoiceStation(device_id=device_id, revision=0, policy={}, state={})
        session.add(row)
        session.flush()
    return row


def safe_state(body, policy):
    if not isinstance(body, dict) or len(json.dumps(body)) > 24000:
        raise ValueError('Слишком большое состояние')
    keys = {'state', 'route', 'rms_percent', 'microphone', 'model', 'task_id', 'activation_mode',
            'default_mode', 'network_mode', 'pending', 'context', 'transcript', 'capabilities', 'scenarios', 'metrics', 'policy_error'}
    value = {key: body[key] for key in keys if key in body}
    raw = json.dumps(value, ensure_ascii=False)
    if contains_secret(raw) or private_transcript(raw):
        raise ValueError('Приватные данные не принимаются')
    if not policy.get('store_transcripts', True) or value.get('network_mode') == 'local_only':
        value.pop('transcript', None)
        value.pop('context', None)
    if value.get('state') not in {'listening', 'recognizing', 'processing', 'speaking', 'waiting_confirmation',
                                  'paused', 'completed', 'error', 'stopped'}:
        raise ValueError('Некорректное состояние')
    return value


def report(session, fingerprint, body):
    from .voice_gateway import _active_voice_device
    device = _active_voice_device(session, fingerprint)
    row = station(session, device.id)
    incoming = body.get('state', {})
    value = safe_state(incoming, row.policy)
    if not row.policy and isinstance(body.get('settings'), dict):
        if contains_secret(json.dumps(body['settings'], ensure_ascii=False)): raise ValueError('Приватные настройки')
        row.policy = CenterPolicy.model_validate(body['settings']).model_dump()
        row.revision = 1
        value = safe_state(incoming, row.policy)
    row.state, row.last_seen = value, now()
    if body.get('event') is True:
        session.add(VoiceEvent(device_id=device.id, data=value))
        session.add(Audit(event='voice_local_event', details={'device_id': device.id,
            'state': value.get('state'), 'route': value.get('route')}))
    prune(session)
    session.commit()
    return {'revision': row.revision}


def control_snapshot(session, fingerprint):
    from .voice_gateway import _active_voice_device
    device = _active_voice_device(session, fingerprint)
    row = session.get(VoiceStation, device.id)
    # Claim before delivery. Lost delivery is visible, never silently retried.
    from .desktop import lock
    lock(session)
    command = session.scalar(select(VoiceControl).where(VoiceControl.device_id == device.id,
        VoiceControl.status == 'PENDING', VoiceControl.expires_at > now()).order_by(VoiceControl.created_at).limit(1).with_for_update())
    result = {'revision': row.revision if row else 0, 'policy': row.policy if row else {}}
    if command:
        command.status = 'DISPATCHED'
        result['control'] = {'id': command.id, 'kind': command.kind, 'payload': command.payload}
    session.commit()
    return result


def acknowledge(session, fingerprint, body):
    from .voice_gateway import _active_voice_device
    from .desktop import lock
    device = _active_voice_device(session, fingerprint)
    lock(session)
    row = session.get(VoiceControl, body.get('id'))
    if row is None or row.device_id != device.id: raise PermissionError('Другой control')
    if aware(row.expires_at) <= now(): raise PermissionError('Control истёк')
    status = body.get('status')
    if status not in {'RUNNING', 'COMPLETED', 'FAILED'}: raise ValueError('Некорректный результат')
    permitted = {'DISPATCHED': {'RUNNING'}, 'RUNNING': {'COMPLETED', 'FAILED'}}
    if status not in permitted.get(row.status, set()): raise PermissionError('Control уже использован')
    row.status = status
    session.add(Audit(event='voice_control_' + status.lower(), details={'device_id': device.id, 'id': row.id, 'kind': row.kind}))
    session.commit()
    return {'status': row.status}


def snapshot(session):
    from .voice_gateway import voice_stream_state
    result = []
    for device in session.scalars(select(Device).order_by(Device.name)):
        row = session.get(VoiceStation, device.id)
        data = dict(row.state) if row else {}
        task_id = data.get('task_id')
        if task_id and device.voice_enabled:
            try:
                task = voice_stream_state(session, device.fingerprint, task_id)
                data['task'] = {k: task[k] for k in ('status', 'message', 'step', 'steps', 'tool')}
            except (PermissionError, ValueError): pass
        result.append({'device_id': device.id, 'name': device.name,
            'connected': bool(row and data and (now() - aware(row.last_seen)).total_seconds() < 12),
            'enabled': device.enabled and device.voice_enabled, 'revision': row.revision if row else 0,
            'policy': row.policy if row else {}, 'state': data,
            'events': [{'id': event.id, 'created_at': event.created_at.isoformat(), **event.data} for event in
                session.scalars(select(VoiceEvent).where(VoiceEvent.device_id == device.id).order_by(VoiceEvent.created_at.desc()).limit(30))],
            'controls': [{'id': c.id, 'kind': c.kind, 'status': c.status} for c in session.scalars(
                select(VoiceControl).where(VoiceControl.device_id == device.id).order_by(VoiceControl.created_at.desc()).limit(10))]})
    return result


def forget_request(session, request):
    from .models import AnswerFragment
    task = session.get(Task, request.task_id)
    if task and not request.keep_text and task.status in {'COMPLETED', 'FAILED', 'CANCELLED'}:
        # Only this new voice request; historical conversations stay untouched.
        task.goal = '[Голосовой текст не сохранён]'
        task.result = None
        task.plan = []
        request.context = []
        session.execute(delete(Message).where(Message.task_id == task.id))
        session.execute(delete(AnswerFragment).where(AnswerFragment.task_id == task.id))
        request.consumed = True


def prune(session):
    session.execute(delete(VoiceEvent).where(VoiceEvent.created_at < now() - timedelta(days=1)))
    for row in session.scalars(select(VoiceControl).where(VoiceControl.expires_at <= now(), VoiceControl.status.in_(['PENDING', 'DISPATCHED', 'RUNNING']))):
        row.status = 'EXPIRED' if row.status == 'PENDING' else 'UNKNOWN'
    session.execute(delete(VoiceControl).where(VoiceControl.created_at < now() - timedelta(days=1), VoiceControl.status.in_(['COMPLETED', 'FAILED', 'EXPIRED'])))
    for request in session.scalars(select(VoiceRequest).where(VoiceRequest.keep_text.is_(False), VoiceRequest.consumed.is_(False),
                                                            VoiceRequest.created_at < now() - timedelta(minutes=15))):
        forget_request(session, request)
