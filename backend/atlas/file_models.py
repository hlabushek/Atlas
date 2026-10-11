"""Совместимость vision берётся из публичного каталога, не угадывается по имени."""
import threading
import time
import httpx
from fastapi import HTTPException
from .model_settings import resolve

_cache = (0, {})
_lock = threading.Lock()


def catalog():
    global _cache
    with _lock:
        if time.monotonic() < _cache[0]:
            return _cache[1]
        try:
            response = httpx.get('https://routerai.ru/api/v1/models', timeout=5, follow_redirects=False)
            response.raise_for_status()
            data = response.json()['data']
            result = {r['id']: 'image' in r.get('architecture', {}).get('input_modalities', []) for r in data}
            _cache = (time.monotonic() + 600, result)
            return result
        except Exception:
            # No stale positive capability and no fallback to an unapproved paid model.
            _cache = (time.monotonic() + 15, {})
            return {}


def roles(session):
    effective = {name: resolve(name, session) for name in ['main', 'planner']}
    models = catalog() if any(r.provider == 'routerai' for r in effective.values()) else {}
    return {name: {'model': r.model, 'vision': r.ready and r.provider == 'routerai' and models.get(r.model, False)} for name, r in effective.items()}


def choose(session, requested):
    options = roles(session)
    if requested != 'auto':
        if options[requested]['vision']:
            return requested
        raise HTTPException(422, 'Выбранная модель не подтверждена как vision-совместимая. Выберите Авто или совместимую модель в настройках')
    for role in ['main', 'planner']:
        if options[role]['vision']:
            return role
    raise HTTPException(422, 'Нет подтверждённой модели для изображений. Настройте vision-модель RouterAI или повторите после восстановления каталога')
