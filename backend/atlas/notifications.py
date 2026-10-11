"""Opt-in, model-gated initiatives delivered to one in-app activity inbox."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, time as daytime
from zoneinfo import ZoneInfo

from sqlalchemy import select, func, or_

from .db import sessions
from .model_settings import resolve
from .models import (NotificationSettings, Initiative, InitiativeDecision, Delivery, Message,
    Task, Event, StudyTask, Assignment, TelegramBinding, Audit, now)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def initiative(session, key, reason, message, cid=None, due=None, expires=None, priority=1, related=None):
    old = session.scalar(select(Initiative).where(Initiative.event_key == key))
    if old:
        return old
    stamp = now()
    item = Initiative(event_key=key, reason=reason, message=message, conversation_id=cid,
        next_allowed=due or stamp, expires_at=expires or stamp + timedelta(days=2),
        priority=priority, related=related or {})
    session.add(item)
    session.flush()
    return item


def link_reply(session, cid, message_id, initiative_id=None):
    query = select(Initiative).where(Initiative.conversation_id == cid,
        Initiative.status.in_(["SENT", "QUEUED"]))
    if initiative_id:
        query = query.where(Initiative.id == initiative_id)
    rows = session.scalars(query.with_for_update()).all()
    if len(rows) == 1:
        item = rows[0]
        item.status = "ANSWERED"
        item.reply_id = message_id
        for delivery in session.scalars(select(Delivery).where(
                Delivery.initiative_id == item.id, Delivery.status == "PENDING")):
            delivery.status = "CANCELLED"
        return item.id
    return None


def queue(session, key, channel, payload, cid=None, iid=None, kind="notification"):
    old = session.scalar(select(Delivery).where(Delivery.dedup_key == key))
    if old:
        return old
    delivery = Delivery(dedup_key=key, channel=channel, payload=payload,
        conversation_id=cid, initiative_id=iid, kind=kind)
    session.add(delivery)
    session.flush()
    return delivery


@dataclass(frozen=True)
class Candidate:
    key: str
    kind: str
    reason: str
    message: str
    priority: int
    conversation_id: str | None = None
    due_at: datetime | None = None
    expires_at: datetime | None = None
    related: dict | None = None


def _candidates(session, since, stamp):
    result = []
    tasks = session.scalars(select(Task).where(Task.kind == "agent", Task.status == "COMPLETED",
        Task.dry_run.is_(False), Task.updated_at >= since,
        ~select(Initiative.id).where(Initiative.event_key == ("task:" + Task.id)).exists()).limit(25)).all()
    for task in tasks:
        result.append(Candidate("task:" + task.id, "task_completed", "Задача завершена",
            "Atlas завершил задачу: " + task.goal[:700], 2, task.conversation_id,
            related={"task_id": task.id}))

    events = session.scalars(select(Event).where(Event.created_at >= since,
        Event.kind.in_(["diary_new", "diary_changed"]),
        ~select(Initiative.id).where(Initiative.event_key == ("event:" + Event.id)).exists()).limit(25)).all()
    for event in events:
        title = str((event.details or {}).get("title", "задание дневника"))[:300]
        result.append(Candidate("event:" + event.id, "diary_change", "Изменение в дневнике",
            "В дневнике изменилось задание: " + title, 2, related={"event_id": event.id}))

    studies = session.scalars(select(StudyTask).where(StudyTask.status == "PENDING",
        StudyTask.due_at >= stamp, StudyTask.due_at <= stamp + timedelta(hours=24)).limit(25)).all()
    for study in studies:
        due = utc(study.due_at)
        key = "deadline:" + study.id + ":" + due.isoformat()
        assignment = session.get(Assignment, study.assignment_id)
        subject = assignment.subject if assignment else "Учебная задача"
        title = assignment.title if assignment else study.goal
        result.append(Candidate(key, "deadline", "Приближается срок",
            f"Завтра срок по предмету «{subject}»: {title}"[:1000], 3,
            due_at=due, expires_at=due, related={"study_task_id": study.id}))
    return result


def _decision_state(session, candidate, include_messages):
    from .memory import contains_secret
    messages = []
    if include_messages and candidate.conversation_id:
        rows = session.scalars(select(Message).where(Message.conversation_id == candidate.conversation_id,
            Message.role.in_(["user", "assistant"]), Message.initiative_id.is_(None),
            or_(Message.attachments.is_(None), Message.attachments == []),
            Message.attachment_task_id.is_(None))
            .order_by(Message.created_at.desc()).limit(4)).all()
        for message in reversed(rows):
            content = message.content[:700].strip()
            if content and not contains_secret(content):
                messages.append({"role": message.role, "text": content})
    return {
        "purpose": "Определить, стоит ли показать владельцу одну короткую карточку в ленте активности.",
        "candidate": {"kind": candidate.kind, "reason": candidate.reason,
            "summary": candidate.message[:1000],
            "due_at": candidate.due_at.isoformat() if candidate.due_at else None},
        "recent_messages": messages,
    }


def _process_candidates(stamp):
    from .initiative_decision import evaluate
    completed = 0
    with sessions()() as session:
        prefs = session.scalar(select(NotificationSettings).where(NotificationSettings.id == 1))
        if not prefs or not prefs.enabled:
            return 0
        since = utc(prefs.enabled_at or stamp)
        include_messages = bool(prefs.context_enabled)
        candidates = _candidates(session, since, stamp)
        # Do not repeat completed decisions. Failed provider calls back off for six hours.
        eligible = []
        for candidate in candidates:
            previous = session.get(InitiativeDecision, candidate.key)
            if previous and (previous.status != "FAILED" or
                    (previous.retry_after and utc(previous.retry_after) > stamp)):
                continue
            eligible.append(candidate)
            if len(eligible) == 2:
                break
        prepared = [(candidate, _decision_state(session, candidate, include_messages),
            resolve('decision', session)) for candidate in eligible]
        session.commit()

    for candidate, state, runtime in prepared:
        try:
            result = evaluate(state, runtime)
            status = "APPROVED" if result['approved'] else "SUPPRESSED"
            confidence = result['confidence']
            model = result['model']
            context_hash = result['context_hash']
            retry_after = None
        except Exception:
            status, confidence, model = "FAILED", None, runtime.model
            import hashlib, json
            context_hash = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            retry_after = stamp + timedelta(hours=6)

        with sessions()() as session:
            with session.begin():
                prefs = session.scalar(select(NotificationSettings).where(
                    NotificationSettings.id == 1).with_for_update())
                previous = session.get(InitiativeDecision, candidate.key, with_for_update=True)
                if previous and previous.status != "FAILED":
                    continue
                if not prefs or not prefs.enabled:
                    continue
                record = previous or InitiativeDecision(source_key=candidate.key)
                record.status = status
                record.confidence = confidence
                record.model = model
                record.context_hash = context_hash
                record.retry_after = retry_after
                if previous is None:
                    session.add(record)
                if status == "APPROVED":
                    initiative(session, candidate.key, candidate.reason, candidate.message,
                        candidate.conversation_id, stamp, candidate.expires_at,
                        candidate.priority, candidate.related)
                    completed += 1
                session.add(Audit(event="initiative_decision", details={
                    "source_type": candidate.kind, "decision": status,
                    "confidence": confidence, "model": model,
                    "context_hash": context_hash}))
    return completed


def scheduler_tick(stamp=None):
    stamp = utc(stamp or now())
    created = _process_candidates(stamp)
    with sessions()() as session:
        prefs = session.scalar(select(NotificationSettings).where(
            NotificationSettings.id == 1).with_for_update())
        if not prefs or not prefs.enabled:
            return 0
        # Persist expiry and wake snoozed cards without generating messages or chats.
        for item in session.scalars(select(Initiative).where(
                Initiative.status.in_(["PENDING", "SENT", "QUEUED", "SNOOZED"]))).all():
            if utc(item.expires_at) <= stamp:
                item.status = "EXPIRED"
                for delivery in session.scalars(select(Delivery).where(
                        Delivery.initiative_id == item.id, Delivery.status == "PENDING")):
                    delivery.status = "CANCELLED"
            elif item.status == "SNOOZED" and utc(item.next_allowed) <= stamp:
                item.status = "SENT" if item.sent_at else "PENDING"

        local = stamp.astimezone(ZoneInfo(prefs.timezone))
        hour = local.hour
        quiet = (prefs.quiet_start <= hour < prefs.quiet_end if prefs.quiet_start < prefs.quiet_end
            else hour >= prefs.quiet_start or hour < prefs.quiet_end) if prefs.quiet_start != prefs.quiet_end else False
        if quiet:
            session.commit()
            return created
        midnight = datetime.combine(local.date(), daytime.min, tzinfo=ZoneInfo(prefs.timezone)).astimezone(timezone.utc)
        used = session.scalar(select(func.count()).select_from(Delivery).where(
            Delivery.kind == "initiative", Delivery.created_at >= midnight,
            Delivery.status.notin_(["CANCELLED", "FAILED"])))
        remaining = max(0, prefs.daily_limit - used)
        delivered = 0
        rows = session.scalars(select(Initiative).where(Initiative.status.in_(["PENDING", "SENT"]),
            Initiative.next_allowed <= stamp).order_by(Initiative.priority.desc(), Initiative.created_at)).all()
        for item in rows:
            if delivered >= remaining:
                break
            repeat = item.status == "SENT"
            if repeat and (item.repeats >= prefs.max_repeats or item.priority < 2):
                continue
            round_no = item.repeats + 1 if repeat else 0
            channel = prefs.channel
            binding = session.get(TelegramBinding, 1)
            if channel == "telegram" and (not binding or not binding.user_id):
                continue
            delivery_conversation = item.conversation_id
            if channel == "telegram" and delivery_conversation is None:
                # Reuse the already-bound Telegram conversation; never create one per initiative.
                delivery_conversation = binding.conversation_id
                if delivery_conversation is None:
                    continue
            text = ("Напоминание: " if repeat else "") + item.message
            delivery = queue(session, "initiative:" + item.id + ":" + str(round_no), channel,
                {"text": text, **({"owner": binding.user_id} if channel == "telegram" else {})},
                delivery_conversation, item.id, "initiative")
            if delivery.status != "PENDING":
                continue
            item.channel = channel
            item.repeats = round_no
            item.next_allowed = stamp + timedelta(hours=prefs.repeat_hours)
            if channel == "web":
                delivery.status = "SENT"
                delivery.sent_at = stamp
                item.status = "SENT"
                item.sent_at = stamp
            else:
                if item.conversation_id is None:
                    item.conversation_id = delivery_conversation
                item.status = "QUEUED"
            session.add(Audit(event="initiative_queued", details={
                "initiative_id": item.id, "round": round_no, "channel": channel}))
            delivered += 1
        session.commit()
        return created + delivered
