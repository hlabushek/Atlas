"""Narrow mTLS bridge from the local Windows voice companion to Atlas chat."""
import hashlib
import json
import re
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .memory import contains_secret
from .models import Audit, Conversation, Device, Message, Task, VoiceRequest, now


def _active_voice_device(session, fingerprint):
    device = session.scalar(select(Device).where(Device.fingerprint == fingerprint))
    if device is None or not device.enabled or not device.voice_enabled:
        raise PermissionError("Voice access is disabled")
    expiry = device.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now() or not device.voice_conversation_id:
        raise PermissionError("Voice access is expired or not configured")
    return device


def set_voice_policy(session, device, *, enabled, conversation_id):
    if enabled:
        if not device.enabled:
            raise ValueError("Сначала разрешите устройство")
        conversation = session.get(Conversation, conversation_id) if conversation_id else None
        if conversation is None or conversation.archived or conversation.trashed_at is not None:
            raise ValueError("Выберите разговор Atlas для голосовых сообщений")
        device.voice_conversation_id = conversation.id
        device.voice_enabled = True
    else:
        device.voice_enabled = False
    session.add(Audit(event="device_voice_policy_changed", details={"device_id": device.id, "enabled": bool(enabled)}))


def submit_voice(session, fingerprint, body):
    device = _active_voice_device(session, fingerprint)
    request_key = body.get("request_id")
    content = body.get("text")
    mode = body.get("mode", "chat")
    if not isinstance(request_key, str) or not re.fullmatch(r"[0-9a-f-]{36}", request_key):
        raise ValueError("Invalid request id")
    if not isinstance(content, str) or not content.strip() or len(content) > 16000:
        raise ValueError("Invalid voice text")
    if mode not in {"chat", "task"}:
        raise ValueError("Invalid voice mode")
    content = content.strip()
    if contains_secret(content):
        raise ValueError("Secret-like content is not accepted")
    from .voice_center import station
    policy = station(session, device.id).policy
    if policy.get('network_mode') == 'local_only' or policy.get('enabled') is False:
        raise PermissionError('Серверный голосовой режим выключен')
    keep_text = body.get('store_transcripts', True)
    if type(keep_text) is not bool: raise ValueError('Некорректная приватность')
    keep_text = keep_text and policy.get('store_transcripts', True)
    if mode == 'task' and not keep_text:
        raise ValueError('Режим задач требует журнала. Используйте разговор без сохранения текста.')
    context = body.get('context', [])
    if not isinstance(context, list) or len(context) > 5: raise ValueError('Слишком большой контекст')
    for item in context:
        if not isinstance(item, dict) or set(item) != {'role', 'content'} or item['role'] not in {'user', 'assistant'} or not isinstance(item['content'], str) or len(item['content']) > 2000 or contains_secret(item['content']):
            raise ValueError('Некорректный контекст')
    digest = hashlib.sha256(json.dumps({'text': content, 'mode': mode, 'keep': keep_text, 'context': context}, sort_keys=True).encode()).hexdigest()
    conversation_id = device.voice_conversation_id
    from .conversations import writable
    writable(session, conversation_id)
    existing = session.scalar(select(Task).where(Task.conversation_id == conversation_id, Task.request_key == request_key))
    if existing:
        receipt = session.get(VoiceRequest, existing.id)
        if receipt and (receipt.device_id != device.id or receipt.digest != digest):
            raise PermissionError('Request ownership conflict')
        if not receipt and (existing.goal != content or existing.kind != ("agent" if mode == "task" else "chat")):
            raise ValueError("Request id conflict")
        return {"task_id": existing.id, "conversation_id": conversation_id, "status": existing.status}
    from .model_settings import resolve
    if mode == "task":
        request = {"conversation_id": conversation_id, "request_key": request_key, "goal": content,
                   "plan": [], "dry_run": False, "max_steps": 8}
        task = Task(conversation_id=conversation_id, request_key=request_key, goal=content, kind="agent",
                    request_fingerprint=hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                    model_profile=resolve("planner", session).snapshot(), max_steps=8)
    else:
        from .chat_routing import chat_profile
        task = Task(conversation_id=conversation_id, request_key=request_key, goal=content,
                    model_profile=chat_profile(session,content))
    session.add(task)
    try:
        session.flush()
        session.add(VoiceRequest(task_id=task.id, device_id=device.id, keep_text=keep_text, digest=digest, context=context))
        if keep_text:
            session.add(Message(conversation_id=conversation_id, role="user", content=content, created_at=task.created_at))
        session.add(Audit(task_id=task.id, event="voice_message_queued", details={"device_id": device.id, "mode": mode}))
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(Task).where(Task.conversation_id == conversation_id, Task.request_key == request_key))
        if existing is None:
            raise ValueError("Request id conflict")
        receipt = session.get(VoiceRequest, existing.id)
        if receipt and (receipt.device_id != device.id or receipt.digest != digest):
            raise PermissionError('Request ownership conflict')
        if not receipt and (existing.goal != content or existing.kind != ("agent" if mode == "task" else "chat")):
            raise ValueError("Request id conflict")
        task = existing
    return {"task_id": task.id, "conversation_id": conversation_id, "status": task.status}


def voice_result(session, fingerprint, task_id):
    device = _active_voice_device(session, fingerprint)
    task = session.get(Task, task_id)
    if task is None or task.conversation_id != device.voice_conversation_id:
        raise PermissionError("Task is outside the configured voice conversation")
    answer = session.scalar(select(Message).where(Message.task_id == task.id, Message.role == "assistant"))
    return {"task_id": task.id, "status": task.status,
            "answer": answer.content if answer else task.result if session.get(VoiceRequest, task.id) else None,
            "error": task.error if task.status == "FAILED" else None}


def voice_stream_state(session, fingerprint, task_id, after=0):
    """Return the small, non-secret state exposed by the voice event stream.

    The stream deliberately reuses the same device/conversation gate as the
    ordinary result endpoint. It exposes checked fragments, progress and the final answer,
    but never exposes plans, tool arguments, claims, or credentials.
    """
    state = voice_result(session, fingerprint, task_id)
    from .answer_stream import fragments, stream_identity
    rows = fragments(session, task_id, after)
    status = state["status"]
    task = session.get(Task, task_id)
    from .models import ToolCall
    call = session.scalar(select(ToolCall).where(ToolCall.task_id == task_id, ToolCall.step == task.current_step))
    tool = call.tool if call else None
    progress = []
    for event in session.scalars(select(Audit).where(Audit.task_id == task_id, Audit.event.in_(
        ['tool_prepared', 'tool_verified', 'approval_requested', 'permission_denied']
    )).order_by(Audit.created_at.desc()).limit(30)):
        details = event.details or {}
        label = {'tool_prepared': 'Вызываю инструмент', 'tool_verified': 'Результат инструмента проверен',
                 'approval_requested': 'Жду подтверждение владельца', 'permission_denied': 'Инструмент запрещён'}[event.event]
        progress.append({'id': event.id, 'type': 'progress', 'message': label + (': ' + details['tool'] if details.get('tool') else '')})
    messages = {
        "PENDING": "Запрос принят, Atlas готовит ответ",
        "PLANNING": "Atlas составляет план",
        "RUNNING": "Atlas выполняет запрос",
        "WAITING_APPROVAL": "Нужно подтверждение в Atlas",
        "WAITING_DEVICE": "Atlas ожидает подключённое устройство",
        "PAUSED": "Задача приостановлена в Atlas",
        "COMPLETED": "Ответ готов",
        "FAILED": "Atlas не смог завершить запрос",
        "CANCELLED": "Запрос отменён",
    }
    return {
        "task_id": state["task_id"],
        "status": status,
        "message": ('Вызываю инструмент: ' + tool if tool and status == 'RUNNING' else messages.get(status, "Atlas обрабатывает запрос")),
        "step": task.current_step, "steps": len(task.plan or []), "tool": tool,
        "progress": list(reversed(progress)),
        "answer": state.get("answer") if status == "COMPLETED" else None,
        "error": state.get("error") if status == "FAILED" else None,
        "fragments": [{"id": row.id, "sequence": row.sequence, "text": row.content} for row in rows],
        "stream_id": stream_identity(session, task_id),
    }


def voice_cancel(session, fingerprint, task_id):
    device = _active_voice_device(session, fingerprint)
    request = session.get(VoiceRequest, task_id)
    if request is None or request.device_id != device.id:
        raise PermissionError('Можно отменить только собственную голосовую задачу')
    voice_result(session, fingerprint, task_id)
    from .task_controls import cancel_task
    task = cancel_task(session, task_id, source='voice')
    session.commit()
    return {'task_id': task.id, 'status': task.status, 'message': 'Уже выполненные действия не откатываются'}


def voice_consumed(session, fingerprint, task_id):
    device = _active_voice_device(session, fingerprint)
    receipt = session.get(VoiceRequest, task_id)
    if receipt is None or receipt.device_id != device.id: raise PermissionError('Другой запрос')
    from .voice_center import forget_request
    forget_request(session, receipt)
    session.commit()
    return {'consumed': receipt.consumed}
