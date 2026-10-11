import re
from .models import Memory, MemoryFact
from .hybrid_search import lexical, rrf

CATEGORIES = {"PROFILE", "EPISODIC", "PROJECTS", "RELATIONSHIPS", "PREFERENCES", "TASKS", "KNOWLEDGE", "AGENT_HISTORY"}

def retrieve(session, query, limit=6, offset=0):
    from .embeddings import ranked as semantic
    if limit <= 0 or offset < 0 or not query.strip() or contains_secret(query):return []
    session.flush()
    order = rrf(lexical(session, query, Memory, [Memory.content], 'memories_fts'), semantic(session, query))
    result=[]
    for identifier in order:
        fact=session.get(MemoryFact,identifier)
        row=session.get(Memory,identifier)
        if row is not None and (fact is None or fact.active):result.append(row)
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
