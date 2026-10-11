"""Одна транзакционная отмена для веба и голоса; выполненные эффекты не откатываются."""
from sqlalchemy import update
from .models import Approval, Audit, Task, now


def cancel_task(session, task_id, *, source='web'):
    from .desktop import lock, sweep
    lock(session)
    task = session.get(Task, task_id)
    if task is None:
        raise LookupError('Задача не найдена')
    session.execute(update(Task).where(Task.id == task_id, Task.status.in_(
        ['PENDING', 'PLANNING', 'RUNNING', 'WAITING_APPROVAL', 'WAITING_DEVICE', 'PAUSED']
    )).values(status='CANCELLED', claim=None, lease_until=None))
    session.execute(update(Approval).where(Approval.task_id == task_id, Approval.status == 'PENDING').values(
        status='CANCELLED', resolved_at=now()))
    sweep(session)
    session.add(Audit(task_id=task_id, event='cancel_requested', details={'source': source}))
    session.flush()
    session.refresh(task)
    return task
