"""Structured, fail-closed relevance decisions for proactive initiative candidates."""
import hashlib
import json

import httpx

from .budget import reserve, settle
from .memory import contains_secret
from .model_settings import Runtime

ENDPOINT = 'https://routerai.ru/api/v1/decisions'
MAX_CONTEXT_BYTES = 10_000
SEND_THRESHOLD = 0.85


def evaluate(state, runtime: Runtime):
    runtime.require()
    if runtime.provider != 'routerai' or runtime.url != 'https://routerai.ru/api/v1':
        raise ValueError('Модель решений должна использовать RouterAI Decisions API')
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    raw = encoded.encode('utf-8')
    if len(raw) > MAX_CONTEXT_BYTES:
        raise ValueError('Контекст решения превышает допустимый размер')
    if contains_secret(encoded):
        raise ValueError('Контекст содержит секретоподобные данные')
    # Reserve conservatively by UTF-8 bytes. RouterAI Decisions does not bill output tokens.
    reserved_tokens = max(1, len(raw))
    identity = reserve('initiative_decision', 'routerai:' + runtime.model,
        reserved_tokens, 0, runtime.rates)
    payload = {
        'model': runtime.model,
        'state': state,
        'questions': {
            'notify': {
                'type': 'noul',
                'instructions': 'Нужно ли сейчас показать владельцу Atlas короткую карточку по этому событию?',
                'criteria': {
                    'true': 'Это новое, конкретное и полезное действие или изменение, требующее внимания владельца. Оно связано с его недавней целью, сроком или результатом работы.',
                    'false': 'Это рутинное, дублирующееся, уже решённое, несущественное или не требующее внимания событие. При сомнении выбирай false.'
                }
            }
        }
    }
    try:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            response = client.post(ENDPOINT,
                headers={'Authorization': 'Bearer ' + runtime.key, 'Content-Type': 'application/json'},
                json=payload)
            response.raise_for_status()
            data = response.json()
        confidence = data['answers']['notify']['noul']
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError('Некорректный структурированный ответ модели')
        settle(identity, data.get('usage'), runtime.rates, estimated_tokens=reserved_tokens)
        return {'confidence': float(confidence), 'approved': float(confidence) >= SEND_THRESHOLD,
            'model': runtime.model, 'context_hash': hashlib.sha256(raw).hexdigest()}
    except Exception:
        settle(identity)
        raise
