"""Общие связи проекта; не дают новых полномочий модели."""
from sqlalchemy import select
from .models import Space, SpaceConversation, SpaceMemory, Memory, MemoryFact


def context_ids(session, conversation_id):
    link = session.get(SpaceConversation, conversation_id)
    if not link or session.get(Space, link.space_id).archived:
        return []
    return list(session.scalars(select(SpaceMemory.memory_id)
                               .where(SpaceMemory.space_id == link.space_id)
                               .order_by(SpaceMemory.memory_id).limit(5)))


def context(session, conversation_id, identifiers):
    # Проверка действующих связей повторяется при выполнении, в том числе после открепления.
    allowed = set(context_ids(session, conversation_id))
    rows = []
    for identifier in identifiers[:5]:
        row = session.get(Memory, identifier) if identifier in allowed else None
        fact = session.get(MemoryFact, identifier)
        if row and (fact is None or fact.active):
            rows.append(row)
    return rows
