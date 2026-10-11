"""Durable DB queue; generation is safe to repeat, committed replies are unique."""
import time
from datetime import timedelta
from sqlalchemy import select, update, or_, and_
from .db import sessions
from .models import Task, Message, Audit, VoiceRequest, now, uid
from .memory import retrieve,contains_secret
from .router import ModelRouter
from .config import settings
from .model_settings import ModelUnavailable

def tick(router=None):
    factory = sessions()
    from .ops import heartbeat
    heartbeat("worker")
    from .desktop import sweep
    with factory() as policy_session:
        from .answer_stream import prune
        prune(policy_session)
        from .voice_center import prune as voice_prune
        voice_prune(policy_session)
        sweep(policy_session);policy_session.commit()
    instant = now()
    with factory() as session:
        eligible = or_(Task.status == "PENDING", and_(Task.status.in_(["RUNNING", "PLANNING"]), Task.lease_until < instant))
        task = session.scalar(select(Task).where(eligible).order_by(Task.created_at).limit(1))
        if task is None:
            from .embeddings import index_one
            session.rollback()
            try:return index_one()
            except Exception:return False
        task_id, token = task.id, uid()
        kind = task.kind
        values = {"status": "PLANNING" if kind == "agent" and not task.plan else "RUNNING", "claim": token, "lease_until": instant + timedelta(seconds=120)}
        if kind == "chat":
            from .diary_queries import matches as diary_matches
            diary_candidate=not task.attachments and diary_matches(task.goal)
            values["plan"] = (["Прочитать сохранённый дневник", "Сохранить ответ"] if diary_candidate else
                              ["Извлечь релевантную память", "Получить ответ модели", "Сохранить ответ"])
        won = session.execute(update(Task).where(Task.id == task_id, eligible).execution_options(synchronize_session=False).values(**values))
        session.commit()
        if won.rowcount != 1: return False
    try:
        from .embeddings import warm
        with factory() as s:
            goal=s.get(Task,task_id).goal
            is_voice=s.get(VoiceRequest,task_id) is not None
        try:
            # Don't place a second provider call/cache of the transcript before voice's first answer.
            if not is_voice and not (kind=='chat' and diary_candidate): warm(goal)
        except Exception:pass
        if kind == "agent":
            from .agent import run
            run(task_id, token, router or ModelRouter())
            return True
        with factory() as session:
            task = session.get(Task, task_id)
            goal = task.goal
            profile = task.model_profile
            context = session.scalars(select(Message).where(Message.conversation_id == task.conversation_id,
                Message.created_at < task.created_at).order_by(Message.created_at.desc()).limit(12)).all()
            from .files import compose, MAX_CONTEXT
            history = []
            remaining = MAX_CONTEXT - sum(len(a['text'].encode()) for a in (task.attachments or []))
            # Newest attached files win the bounded context; do not silently cut a file in half.
            selected = {}
            images_left = 5 - sum(a['mime'].startswith('image/') for a in (task.attachments or []))
            def load_image(identifier):
                from .models import Attachment
                a = session.get(Attachment, identifier)
                if a is None or not a.image: raise ValueError('Изображение недоступно')
                return a.image
            for m in context:
                source = session.get(Task, m.attachment_task_id) if m.attachment_task_id else None
                files = source.attachments if source else []
                size = sum(len(a['text'].encode()) for a in (files or []))
                images = sum(a['mime'].startswith('image/') for a in (files or []))
                if size <= remaining and images <= images_left and (not images or (profile or {}).get('vision')):
                    selected[m.id] = files
                    remaining -= size
                    images_left -= images
            for m in reversed(context):
                content = compose(m.content, selected.get(m.id), load_image)
                if m.attachments and m.id not in selected:
                    content += '\n[Содержимое прежних вложений вне лимита контекста; попроси прикрепить файл повторно]'
                history.append({'role': m.role, 'content': content})
            voice_request = session.get(VoiceRequest, task_id)
            if voice_request is not None:
                history = voice_request.context[-5:]
                if profile:
                    profile = dict(profile, response_style='voice_brief')
            from .spaces import context as space_context
            pinned = space_context(session, task.conversation_id, (profile or {}).get('space_memory_ids', []))
            rows = pinned + [m for m in retrieve(session, goal) if m.id not in {p.id for p in pinned}]
            memories = [m.content for m in rows[:8]]
            goal = compose(goal, task.attachments, load_image)
            if diary_candidate and (router is None or isinstance(router,ModelRouter)):
                from .diary_queries import reply as diary_reply
                diary_answer=diary_reply(session,task.goal)
            else:diary_answer=None
        active_router=router or ModelRouter()
        sequence = 0
        def emit(content):
            nonlocal sequence
            from .answer_stream import publish
            sequence += 1
            publish(task_id, token, sequence, content)
        if diary_answer is not None:
            answer=diary_answer
            emit(answer)
            provider='atlas-diary'
            model='saved-data'
        else:
            answer=active_router.answer(goal,history,memories,profile=profile,task_id=task_id,on_fragment=emit) if isinstance(active_router,ModelRouter) else active_router.answer(goal,history,memories)
            provider=(profile or {}).get('provider',settings().model_provider)
            model=(profile or {}).get('model',settings().model_name)
        if contains_secret(answer):raise ValueError("Секреты нельзя сохранять в ответе")
        with factory() as session:
            # Lock finalization with conditional update; cancelled/stale claims cannot publish.
            won = session.execute(update(Task).where(Task.id == task_id, Task.claim == token,
                Task.status == "RUNNING", Task.lease_until > now()).values(status="COMPLETED", result=answer, lease_until=None))
            if won.rowcount == 1:
                task = session.get(Task, task_id)
                receipt = session.get(VoiceRequest, task_id)
                if receipt is None or receipt.keep_text:
                    session.add(Message(conversation_id=task.conversation_id, role="assistant", content=answer, task_id=task_id))
                session.add(Audit(task_id=task_id, event="reply_completed", details={"provider":provider,"model":model}))
            session.commit()
    except Exception as exc:
        with factory() as session:
            won = session.execute(update(Task).where(Task.id == task_id, Task.claim == token, Task.status.in_(["RUNNING", "PLANNING"])).values(
                status="FAILED", error=str(exc) if isinstance(exc,ModelUnavailable) else "Ошибка модели: " + type(exc).__name__, lease_until=None))
            if won.rowcount == 1:
                from sqlalchemy import delete
                from .models import AnswerFragment
                session.execute(delete(AnswerFragment).where(AnswerFragment.task_id == task_id, AnswerFragment.claim == token))
                session.add(Audit(task_id=task_id, event="model_failed", details={"type": type(exc).__name__}))
            session.commit()
    return True

if __name__ == "__main__":
    while True:
        try:
            if not tick(): time.sleep(0.1)
        except Exception as exc:
            print("Worker database error:", type(exc).__name__, flush=True)
            time.sleep(3)
