import re
from sqlalchemy import func,or_,select
from .models import Memory

CATEGORIES = {"PROFILE", "EPISODIC", "PROJECTS", "RELATIONSHIPS", "PREFERENCES", "TASKS", "KNOWLEDGE", "AGENT_HISTORY"}

def retrieve(session, query, limit=6, offset=0):
    # Literal token matching, never interpolate into SQL LIKE patterns.
    words = set(re.findall(r"\w{3,}", query.lower()))
    statement=select(Memory).order_by(Memory.confirmed_at.desc()).limit(1000)
    if words:
        statement=statement.where(or_(*(func.lower(Memory.content).contains(word) for word in words)))
    rows = session.scalars(statement).all()
    ranked = sorted(((sum(w in row.content.lower() for w in words), row) for row in rows), key=lambda pair: pair[0], reverse=True)
    from .embeddings import ranked as semantic
    from .models import MemoryFact
    lexical=[row.id for score,row in ranked if score>0]
    scores: dict[str, float] = {}
    for order in (lexical,semantic(session,query)):
        for rank,identifier in enumerate(order):scores[identifier]=scores.get(identifier,0)+1/(60+rank)
    result=[]
    for identifier in sorted(scores,key=lambda value: scores[value],reverse=True):
        fact=session.get(MemoryFact,identifier)
        if fact is None or fact.active:result.append(session.get(Memory,identifier))
    return result[offset:offset+limit]

def known_secrets():
    from .config import settings
    import os
    cfg=settings()
    known=[cfg.owner_password,cfg.session_secret,cfg.model_api_key,cfg.telegram_bot_token,cfg.embedding_api_key,cfg.vapid_private_key,cfg.routerai_api_key,cfg.plusvibe_api_key]
    known.extend(value for key,value in os.environ.items() if key.startswith('ATLAS_CONNECTOR_'))
    return [value for value in known if value]


def contains_secret(text):
    if any(value in text for value in known_secrets()):return True
    return bool(re.search(r"(?i)(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer\s+[a-z0-9_.-]{16,}|sk-[a-z0-9_-]{12,}|(?:password|пароль|api[_ -]?key|token|токен)\s*[:=]\s*\S+)", text))
