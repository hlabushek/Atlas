"""Проверенные текстовые фрагменты, привязанные к актуальной аренде worker."""
import re
from datetime import timedelta
from sqlalchemy import select, update, delete
from .db import sessions
from .models import Task, AnswerFragment, now
from .memory import contains_secret, known_secrets


class SafeText:
    """Не выпускает незавершённое слово и хвост возможного известного секрета.

    Это фильтр существующей политики секретов, не универсальная DLP. Полный
    текст остаётся в памяти для проверки шаблонов через границы фрагментов.
    """
    def __init__(self):
        self.text = ''
        self.released = 0
        self.hold = max([80, *(len(value) for value in known_secrets())])

    def feed(self, delta, final=False):
        self.text += delta
        if len(self.text) > 50000 or contains_secret(self.text):
            raise ValueError('Небезопасный или слишком длинный ответ модели')
        limit = len(self.text) if final else max(0, len(self.text) - self.hold)
        if not final:
            # Only complete words; prefer sentence boundaries for immediate TTS.
            ends = [m.end() for m in re.finditer(r'(?<=[.!?\n])\s+', self.text[:limit]) if m.end() > self.released]
            if ends:
                limit = ends[-1]
            else:
                if limit - self.released < 180:
                    return ''
                limit = self.text.rfind(' ', self.released, min(limit, self.released + 260)) + 1
        if limit <= self.released:
            return ''
        if not final and limit - self.released < 32:
            return ''
        chunk = self.text[self.released:limit]
        self.released = limit
        return chunk


def publish(task_id, claim, sequence, content):
    """Условный UPDATE сериализует публикацию с отменой и сменой claim."""
    if not content or contains_secret(content):
        raise ValueError('Небезопасный фрагмент')
    with sessions()() as s:
        won = s.execute(update(Task).where(Task.id == task_id, Task.claim == claim,
            Task.status == 'RUNNING', Task.lease_until > now()).values(lease_until=now() + timedelta(seconds=120)))
        if won.rowcount != 1:
            raise ValueError('Аренда ответа завершена')
        s.add(AnswerFragment(task_id=task_id, claim=claim, sequence=sequence, content=content))
        # An obsolete attempt must never be replayed after worker recovery.
        s.execute(delete(AnswerFragment).where(AnswerFragment.task_id == task_id, AnswerFragment.claim != claim))
        s.commit()


def fragments(session, task_id, after=0):
    task = session.get(Task, task_id)
    if task is None or task.status not in {'RUNNING', 'COMPLETED'}:
        return []
    return session.scalars(select(AnswerFragment).where(AnswerFragment.task_id == task_id,
        AnswerFragment.claim == task.claim, AnswerFragment.sequence > after)
        .order_by(AnswerFragment.sequence).limit(256)).all()


def stream_identity(session, task_id):
    rows = fragments(session, task_id)
    return rows[0].id if rows else None


def prune(session):
    # Ephemeral transport; canonical completed answer is Task/Message, not this table.
    session.execute(delete(AnswerFragment).where(AnswerFragment.created_at < now() - timedelta(hours=24)))
