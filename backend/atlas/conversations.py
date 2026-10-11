"""Метаданные и обратимое удаление. Запросы и эффекты не переписываются."""
from sqlalchemy import select
from .models import Conversation, Task, Device, TelegramBinding, Initiative, Audit, now

ACTIVE = ['PENDING', 'PLANNING', 'RUNNING', 'WAITING_APPROVAL', 'WAITING_DEVICE', 'PAUSED']


def writable(session, cid):
    row = session.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
    if row is None:
        raise LookupError('Разговор не найден')
    if row.trashed_at is not None or row.archived:
        raise ValueError('Сначала восстановите разговор из корзины или архива')
    return row


def protected_reason(session, cid):
    if session.scalar(select(Device.id).where(Device.voice_conversation_id == cid, Device.voice_enabled == True)):
        return 'Чат используется голосовым Atlas. Сначала выберите другой чат в доступе устройства.'
    if session.scalar(select(TelegramBinding.id).where(TelegramBinding.conversation_id == cid, TelegramBinding.user_id.is_not(None))):
        return 'Чат используется Telegram. Сначала отвяжите канал или выберите другой разговор.'
    if session.scalar(select(Initiative.id).where(Initiative.conversation_id == cid, Initiative.status.in_(['PENDING','SENT','QUEUED']))):
        return 'У чата есть действующее напоминание. Сначала завершите или отмените его.'
    return None


def change(session, cid, *, revision, values):
    # Тот же mutex, что у policy/отмены/настроек устройств: привязка не гоняется с корзиной.
    from .desktop import lock
    lock(session)
    row = session.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
    if row is None:
        raise LookupError('Разговор не найден')
    if row.revision != revision:
        raise ValueError('Чат изменён в другой сессии. Обновите список.')
    action = values.pop('action', 'update')
    if action in {'archive', 'trash'}:
        reason = protected_reason(session, cid)
        if reason:
            raise ValueError(reason)
        if session.scalar(select(Task.id).where(Task.conversation_id == cid, Task.status.in_(ACTIVE))):
            raise ValueError('Сначала завершите или отмените активные задачи чата. Выполненные эффекты не откатываются.')
    if action == 'archive':
        row.archived = True
    elif action == 'trash':
        row.trashed_at = now()
    elif action == 'restore':
        row.trashed_at = None
        row.archived = False
    elif action == 'update':
        for key, value in values.items():
            setattr(row, key, value)
    else:
        raise ValueError('Неизвестное действие')
    row.revision += 1
    row.updated_at = now()
    session.add(Audit(event='conversation_'+action, details={'conversation_id':cid,'revision':row.revision,'fields':sorted(values)}))
    session.flush()
    return row
