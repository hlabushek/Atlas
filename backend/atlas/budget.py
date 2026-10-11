"""Persistent reservations precede provider requests; ambiguous usage stays reserved."""
from datetime import datetime, timezone
from sqlalchemy import select, func, update
from .db import sessions
from .models import Budget, ModelUsage

def reserve(purpose, provider, input_tokens, output_tokens=0, rates=None, task_id=None):
    with sessions()() as s:
        # UPDATE serializes concurrent reservations on SQLite and PostgreSQL.
        s.execute(update(Budget).where(Budget.id==1).values(token_limit=Budget.token_limit))
        b=s.get(Budget,1)
        instant=datetime.now(timezone.utc)
        start=instant.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        day_start=instant.replace(hour=0,minute=0,second=0,microsecond=0)
        used=s.execute(select(func.coalesce(func.sum(ModelUsage.tokens),0),func.coalesce(func.sum(ModelUsage.cost),0)).where(ModelUsage.created_at>=start)).one()
        today=s.execute(select(func.coalesce(func.sum(ModelUsage.tokens),0),func.coalesce(func.sum(ModelUsage.cost),0)).where(ModelUsage.created_at>=day_start)).one()
        tokens=input_tokens+output_tokens
        input_rate,output_rate=rates if rates is not None else (b.input_rate,b.output_rate)
        cost=(input_tokens*input_rate+output_tokens*output_rate+999999)//1000000 if input_rate or output_rate else None
        if used[0]+tokens>b.token_limit: raise ValueError('Месячный лимит токенов исчерпан')
        if b.cost_limit is not None and (cost is None or used[1]+cost>b.cost_limit or s.scalar(select(ModelUsage.id).where(ModelUsage.created_at>=start,ModelUsage.cost.is_(None)).limit(1))): raise ValueError('Лимит стоимости или тариф не настроен')
        if b.daily_token_limit is not None and today[0]+tokens>b.daily_token_limit: raise ValueError('Дневной лимит токенов исчерпан (UTC)')
        if task_id and b.task_cost_limit is not None:
            task_cost=s.execute(select(func.coalesce(func.sum(ModelUsage.cost),0)).where(ModelUsage.task_id==task_id)).scalar_one()
            if cost is None or task_cost+cost>b.task_cost_limit or s.scalar(select(ModelUsage.id).where(ModelUsage.task_id==task_id,ModelUsage.cost.is_(None)).limit(1)):
                raise ValueError('Лимит стоимости задачи или тариф не настроен')
        if b.daily_cost_limit is not None and (cost is None or today[1]+cost>b.daily_cost_limit or s.scalar(select(ModelUsage.id).where(ModelUsage.created_at>=day_start,ModelUsage.cost.is_(None)).limit(1))): raise ValueError('Дневной лимит стоимости или тариф не настроен')
        row=ModelUsage(purpose=purpose,provider=provider,tokens=tokens,cost=cost,task_id=task_id)
        s.add(row);s.commit();return row.id

def settle(identifier, usage=None, rates=None, estimated_tokens=None):
    with sessions()() as s:
        row=s.get(ModelUsage,identifier)
        if row is None or row.status!='RESERVED':return
        b=s.get(Budget,1)
        input_rate,output_rate=rates if rates is not None else (b.input_rate,b.output_rate)
        if isinstance(usage,dict) and all(type(usage.get(k)) is int and 0<=usage[k]<=10000000 for k in ('prompt_tokens','total_tokens')) and usage['total_tokens']>=usage['prompt_tokens']:
            row.tokens=usage['total_tokens'];row.status='KNOWN'
            row.cost=(usage['prompt_tokens']*input_rate+(row.tokens-usage['prompt_tokens'])*output_rate+999999)//1000000 if input_rate or output_rate else None
        elif estimated_tokens is not None and type(estimated_tokens) is int and estimated_tokens>0:
            row.tokens=estimated_tokens;row.status='ESTIMATED'
            row.cost=(estimated_tokens*input_rate+999999)//1000000 if input_rate else None
        else:row.status='UNKNOWN'
        s.commit()

def estimate(payload):
    """Текст — консервативно по байтам; base64 не является текстовыми токенами."""
    import copy
    import json
    bounded = copy.deepcopy(payload)
    images = 0
    for message in bounded.get('messages', []):
        if isinstance(message.get('content'), list):
            for part in message['content']:
                if part.get('type') == 'image_url':
                    url = part.get('image_url', {}).get('url', '')
                    if not url.startswith('data:image/jpeg;base64,') or len(url) > 1500000:
                        raise ValueError('Допустимы только локально проверенные изображения')
                    images += 1
                    part['image_url']['url'] = '[локальное изображение]'
    if images > 5: raise ValueError('Максимум пять изображений в контексте')
    encoded = json.dumps(bounded, ensure_ascii=False)
    # 65536 на изображение — резерв, не заявленный расход/тариф или точный tokenizer.
    return encoded, len(encoded.encode()) + images * 65536


def request(client, url, headers, payload, purpose, provider, output=0, rates=None, task_id=None):
    from .memory import contains_secret
    encoded, tokens = estimate(payload)
    if contains_secret(encoded):raise ValueError('Секреты нельзя отправлять модели')
    # UTF-8 bytes are a deliberately conservative reservation, never a claimed tokenizer result.
    identifier=reserve(purpose,provider,tokens,output,rates,task_id)
    try:
        response=client.post(url,headers=headers,json=payload)
        response.raise_for_status()
        data=response.json()
        settle(identifier,data.get('usage'),rates)
        return data
    except Exception:
        settle(identifier);raise


def stream_request(client, url, headers, payload, purpose, provider, output=0, rates=None, task_id=None, on_delta=None):
    """SSE с одним резервом; обрыв не считается завершённым ответом/нулевым расходом."""
    import json
    import time
    from .memory import contains_secret
    encoded, tokens = estimate(payload)
    if contains_secret(encoded):
        raise ValueError('Секреты нельзя отправлять модели')
    identifier = reserve(purpose, provider, tokens, output, rates, task_id)
    usage = None
    text = ''
    started = time.monotonic()
    try:
        with client.stream('POST', url, headers=headers, json=dict(payload, stream=True)) as response:
            response.raise_for_status()
            if 'text/event-stream' not in response.headers.get('content-type', ''):
                # Some compatible providers return JSON despite stream=True. Never
                # resubmit a paid request just because streaming is unsupported.
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 2000000:
                        raise ValueError('Слишком большой ответ модели')
                data = json.loads(raw)
                text = data['choices'][0]['message']['content']
                if not isinstance(text, str) or len(text) > 50000:
                    raise ValueError('Некорректный ответ модели')
                if on_delta:
                    on_delta(text)
                usage = data.get('usage')
            else:
                event = []
                done = False
                event_bytes = 0
                events = 0
                for line in response.iter_lines():
                    if time.monotonic() - started > 90 or len(line) > 65536:
                        raise ValueError('Лимит потока модели')
                    if line.startswith('data:'):
                        event.append(line[5:].lstrip(' '))
                        event_bytes += len(line)
                        if event_bytes > 65536:
                            raise ValueError('Лимит события модели')
                    if line or not event:
                        continue
                    raw_event = '\n'.join(event)
                    event, event_bytes = [], 0
                    events += 1
                    if events > 100000:
                        raise ValueError('Лимит событий модели')
                    if raw_event == '[DONE]':
                        done = True
                        break
                    data = json.loads(raw_event)
                    if not isinstance(data, dict) or data.get('error'):
                        raise ValueError('Ошибка потока модели')
                    if isinstance(data.get('usage'), dict):
                        usage = data['usage']
                    choices = data.get('choices', [])
                    if choices:
                        choice = choices[0]
                        if choice.get('finish_reason') in {'error', 'tool_calls', 'function_call'}:
                            raise ValueError('Неподдерживаемый ответ модели')
                        delta = choice.get('delta', {}).get('content')
                        if delta is not None:
                            if not isinstance(delta, str) or len(text) + len(delta) > 50000:
                                raise ValueError('Некорректный фрагмент модели')
                            text += delta
                            if on_delta:
                                on_delta(delta)
                if not done:
                    raise ValueError('Незавершённый поток модели')
        if not text.strip():
            raise ValueError('Пустой ответ модели')
        settle(identifier, usage, rates)
        return text
    except Exception:
        settle(identifier)
        raise
