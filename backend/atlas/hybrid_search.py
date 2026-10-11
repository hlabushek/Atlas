"""Общий лексический поиск и Reciprocal Rank Fusion без сетевых запросов."""
import re
from collections.abc import Iterable

from sqlalchemy import func, or_, select, text


def tokens(query: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'[^\W_]+', query.casefold(), re.UNICODE)))[:32]


def rrf(*rankings: Iterable[str], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(dict.fromkeys(ranking), start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda identifier: (-scores[identifier], identifier))


def lexical(session, query, model, columns, index, limit=1000, filters=()):
    words = tokens(query)
    if not words:
        return []
    if session.get_bind().dialect.name == 'sqlite':
        # Index/table names are internal constants; MATCH is always a bound value.
        if index not in {'memories_fts', 'assignments_fts'}:
            raise ValueError('Неизвестный поисковый индекс')
        match = ' OR '.join('"' + word + '"' for word in words)
        ids = text(f'SELECT id FROM {index} WHERE {index} MATCH :query ORDER BY rank, id LIMIT :limit')
        candidates = session.execute(ids, {'query': match, 'limit': limit}).scalars().all()
        if not filters:
            return candidates
        allowed = set(session.scalars(select(model.id).where(model.id.in_(candidates), *filters)))
        return [identifier for identifier in candidates if identifier in allowed]
    # PostgreSQL keeps its existing lexical fallback; SQLite FTS5 is not portable.
    rows = session.scalars(select(model).where(*filters, or_(*(
        func.lower(column).contains(word, autoescape=True) for column in columns for word in words
    ))).order_by(model.id).limit(limit)).all()
    return [row.id for row in sorted(rows, key=lambda row: (-sum(
        word in ' '.join(getattr(row, column.key) for column in columns).casefold()
        for word in words), row.id))]
