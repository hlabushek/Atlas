import json
import threading
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, update
from atlas import budget, local_voice
from atlas.answer_stream import SafeText, publish, fragments
from atlas.db import sessions
from atlas.models import Task, ModelUsage, now
from atlas.voice_reply import VoiceReply, ReplyController, ReplyInterrupted, is_voice_interrupt


def sse(*deltas, usage=None, done=True):
    events = [': keep-alive\n\n']
    for delta in deltas:
        events.append('data: ' + json.dumps({'choices': [{'delta': {'content': delta}}]}, ensure_ascii=False) + '\n\n')
    if usage is not None:
        events.append('data: ' + json.dumps({'choices': [], 'usage': usage}) + '\n\n')
    if done:
        events.append('data: [DONE]\n\n')
    return ''.join(events).encode()


def request_stream(raw, on_delta=None, content_type='text/event-stream', status=200):
    calls = []
    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(status, content=raw, headers={'Content-Type': content_type})
    with httpx.Client(transport=httpx.MockTransport(handler)) as provider:
        result = budget.stream_request(provider, 'https://fixture.invalid/chat/completions', {},
            {'messages': [{'role': 'user', 'content': 'Безопасная цель'}]}, 'chat', 'fixture', 100, on_delta=on_delta)
    return result, calls


def test_sse_preserves_text_and_reserves_once(client):
    received = []
    text, calls = request_stream(sse('Первое. ', 'Второе.', usage={'prompt_tokens': 10, 'total_tokens': 15}), received.append)
    assert text == ''.join(received) == 'Первое. Второе.'
    assert calls[0]['stream'] is True and len(calls) == 1
    with sessions()() as session:
        rows = session.scalars(select(ModelUsage)).all()
        assert len(rows) == 1 and rows[0].status == 'KNOWN' and rows[0].tokens == 15


@pytest.mark.parametrize('raw', [sse('Часть', done=False), b'data: bad-json\n\n',
                                b'data: {"error":{"message":"password=secret"}}\n\n'])
def test_broken_stream_stays_unknown_and_never_becomes_completed(client, raw):
    with pytest.raises(ValueError):
        request_stream(raw)
    with sessions()() as session:
        row = session.scalar(select(ModelUsage))
        assert row.status == 'UNKNOWN' and row.tokens >= 100


def test_json_provider_fallback_does_not_repeat_paid_request(client):
    text, calls = request_stream(json.dumps({'choices': [{'message': {'content': 'Обычный ответ'}}]}).encode(),
                                content_type='application/json')
    assert text == 'Обычный ответ' and len(calls) == 1


def test_http_failure_does_not_retry_a_paid_request(client):
    with pytest.raises(httpx.HTTPStatusError):
        request_stream(b'{}', status=400)
    with sessions()() as session:
        assert len(session.scalars(select(ModelUsage)).all()) == 1


@pytest.mark.parametrize('secret', ['test-owner-password-123', 'test-session-secret-with-at-least-32-characters',
                                   'Bearer abcdefghijklmnopqrstuvwxyz', 'sk-abcdefghijklmnop',
                                   'пароль: значение', 'api_key = sensitive'])
def test_secret_split_at_every_boundary_never_reaches_published_chunks(client, secret):
    for boundary in range(1, len(secret)):
        guard = SafeText()
        safe = 'Безопасный контекст. ' * 10
        published = [guard.feed(safe)]
        with pytest.raises(ValueError):
            published.append(guard.feed(secret[:boundary]))
            guard.feed(secret[boundary:] + ' ' * 100)
        assert safe.startswith(''.join(published))


def test_safe_text_releases_before_final_and_preserves_exact_text(client):
    guard = SafeText()
    text = 'Первое предложение. ' * 15
    partial = guard.feed(text)
    assert partial and len(partial) < len(text)
    assert partial + guard.feed('', final=True) == text


def test_long_paragraph_keeps_streaming_after_an_earlier_sentence(client):
    guard = SafeText()
    first = guard.feed('Первое предложение. ' * 10)
    second = guard.feed('без длинных пауз ' * 30)
    assert first and second and len(second) <= 260
    assert first + second + guard.feed('', final=True) == 'Первое предложение. ' * 10 + 'без длинных пауз ' * 30


def task_row(client):
    cid = client.post('/api/conversations', json={'title': 'Поток'}).json()['id']
    with sessions()() as session:
        task = Task(conversation_id=cid, request_key='fixture', goal='Расскажи о проекте',
                    status='RUNNING', claim='claim-one', lease_until=now() + timedelta(seconds=120))
        session.add(task)
        session.commit()
        return task.id


def test_cancelled_and_stale_worker_cannot_publish(client):
    tid = task_row(client)
    publish(tid, 'claim-one', 1, 'Первый проверенный фрагмент. ')
    with sessions()() as session:
        session.execute(update(Task).where(Task.id == tid).values(claim='claim-two'))
        session.commit()
    with pytest.raises(ValueError):
        publish(tid, 'claim-one', 2, 'Устаревший фрагмент. ')
    publish(tid, 'claim-two', 1, 'Новая попытка. ')
    with sessions()() as session:
        assert [row.content for row in fragments(session, tid)] == ['Новая попытка. ']
        session.execute(update(Task).where(Task.id == tid).values(status='CANCELLED'))
        session.commit()
        assert fragments(session, tid) == []
    with pytest.raises(ValueError):
        publish(tid, 'claim-two', 2, 'После отмены. ')


def test_expired_lease_cannot_publish(client):
    tid = task_row(client)
    with sessions()() as session:
        session.execute(update(Task).where(Task.id == tid).values(lease_until=now() - timedelta(seconds=1)))
        session.commit()
    with pytest.raises(ValueError):
        publish(tid, 'claim-one', 1, 'Слишком поздно. ')


def test_additive_migration_preserves_existing_tasks_and_conversations(client):
    from alembic import command
    from alembic.config import Config
    tid = task_row(client)
    command.downgrade(Config('alembic.ini'), '0013')
    command.upgrade(Config('alembic.ini'), 'head')
    with sessions()() as session:
        assert session.get(Task, tid).goal == 'Расскажи о проекте'
    publish(tid, 'claim-one', 1, 'Ответ после обновления. ')


def test_private_input_never_starts_provider_or_reserves_budget(client):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('private request reached provider'))) as provider:
        with pytest.raises(ValueError):
            budget.stream_request(provider, 'https://fixture.invalid', {}, {'content': 'password=secret'}, 'chat', 'fixture')
    with sessions()() as session:
        assert session.scalar(select(ModelUsage)) is None


def test_tts_starts_before_generation_finishes_and_cancel_drops_queue():
    started, released = threading.Event(), threading.Event()
    spoken = []
    def speaker(text, cancel):
        spoken.append(text)
        started.set()
        cancel.wait(2)
    def receive(reply):
        reply.say('Первое предложение.')
        assert started.wait(1)
        reply.say('Не должно прозвучать.')
        released.wait(2)
    reply = VoiceReply(speaker).start(receive)
    try:
        assert started.wait(1) and not reply.generated.is_set()
        reply.cancel()
        released.set()
        assert reply.done.wait(1)
        assert spoken == ['Первое предложение.'] and reply.queue.empty()
        with pytest.raises(ReplyInterrupted):
            reply.say('Устаревший callback')
    finally:
        released.set()
        reply.close()


def test_reply_limit_and_network_cancellation_are_bounded():
    reply = VoiceReply(lambda *_: None)
    closed = []
    reply.register_client(SimpleNamespace(close=lambda: closed.append(True)))
    reply.say('x' * 4000 + '.')
    assert len(reply.queue.get_nowait()) == 3500
    reply.say('Больше не добавляется')
    assert reply.queue.empty()
    reply.cancel()
    assert closed == [True]


def test_controller_stops_previous_turn_without_speaking_its_stale_reply():
    waiting = threading.Event()
    spoken = []
    with ReplyController() as controller:
        old = controller.start(lambda text, _: spoken.append(text), lambda reply: waiting.wait(2))
        fresh = controller.start(lambda text, _: spoken.append(text), lambda reply: reply.say('Новый ответ'))
        assert old.cancelled.is_set() and fresh.done.wait(1)
        waiting.set()
    assert spoken == ['Новый ответ']


@pytest.mark.parametrize('phrase, expected', [('атлас стоп', True), ('атлас останови ответ', True),
    ('стоп', False), ('атлас открой сайт', False), ('расскажи атлас стоп', False), ('атлос стоп', False)])
def test_voice_interrupt_is_exact_and_never_an_action(phrase, expected):
    assert is_voice_interrupt(phrase, ['атлас']) is expected


class FakeVoiceClient:
    def __init__(self, events, final):
        self.events, self.final = events, final
        self.posts = 0
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def post(self, *args, **kwargs):
        self.posts += 1
        return httpx.Response(200, json={'task_id': 'fixture-task'}, request=httpx.Request('POST', 'https://fixture.invalid/voice'))
    def stream(self, *args, **kwargs):
        owner = self
        class Stream:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def raise_for_status(self): pass
            def iter_lines(self):
                for event in owner.events:
                    if isinstance(event, Exception): raise event
                    yield json.dumps(event, ensure_ascii=False)
        return Stream()
    def get(self, *_):
        return httpx.Response(200, json=self.final, request=httpx.Request('GET', 'https://fixture.invalid/result'))


@pytest.mark.parametrize('broken', [False, True])
def test_voice_final_or_poll_fallback_never_repeats_spoken_prefix(monkeypatch, broken):
    first = {'type': 'fragment', 'id': 'first', 'text': 'Первая часть. '}
    final = {'type': 'answer', 'status': 'COMPLETED', 'answer': 'Первая часть. Вторая часть.'}
    events = [first, first, httpx.RemoteProtocolError('broken') if broken else final]
    fake = FakeVoiceClient(events, final)
    monkeypatch.setattr(local_voice, '_atlas_client', lambda _: fake)
    monkeypatch.setattr(local_voice.time, 'sleep', lambda _: None)
    spoken = []
    assert local_voice._send_to_atlas('Запрос', {}, speaker=spoken.append) == final['answer']
    assert spoken == ['Первая часть. ', 'Вторая часть.'] and fake.posts == 1


def test_voice_cancelled_before_submit_never_sends(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    fake = FakeVoiceClient([], {})
    monkeypatch.setattr(local_voice, '_atlas_client', lambda _: fake)
    with pytest.raises(ReplyInterrupted):
        local_voice._send_to_atlas('Запрос', {}, cancel_event=cancel)
    assert fake.posts == 0


@pytest.mark.parametrize('event_type', ['denied', 'reset', 'server_error'])
def test_revocation_or_restart_stops_old_voice_instead_of_polling(monkeypatch, event_type):
    fake = FakeVoiceClient([{'type': event_type}], {'status': 'COMPLETED', 'answer': 'Не читать'})
    monkeypatch.setattr(local_voice, '_atlas_client', lambda _: fake)
    fake.get = lambda *_: pytest.fail('Revocation/reset must not fall back to polling')
    with pytest.raises(RuntimeError):
        local_voice._send_to_atlas('Запрос', {}, speaker=lambda _: pytest.fail('Unexpected speech'))


def test_legacy_final_response_is_private_checked_before_speech_and_console(monkeypatch, capsys):
    final = {'status': 'COMPLETED', 'answer': 'password=hidden-fixture-value'}
    fake = FakeVoiceClient([final], final)
    monkeypatch.setattr(local_voice, '_atlas_client', lambda _: fake)
    with pytest.raises(ValueError):
        local_voice._send_to_atlas('Запрос', {}, speaker=lambda _: pytest.fail('Private answer spoken'))
    assert 'hidden-fixture-value' not in capsys.readouterr().out


def test_short_escape_keypress_marks_whole_tts_turn_cancelled(monkeypatch):
    import sys
    from pathlib import Path
    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(play=lambda *_: pytest.fail('Cancelled speech played')))
    monkeypatch.setitem(sys.modules, 'piper', SimpleNamespace(
        PiperVoice=SimpleNamespace(load=lambda _: SimpleNamespace(synthesize=lambda *_args, **_kw: pytest.fail('Cancelled speech synthesized'))),
        SynthesisConfig=lambda **_: None))
    monkeypatch.setattr(local_voice, '_resolve_tts_model', lambda _: Path('fixture.onnx'))
    monkeypatch.setattr(local_voice, '_ptt_pressed', lambda _: True)
    cancel = threading.Event()
    speaker = local_voice._make_speaker({'speak_responses': True})
    assert speaker is not None
    speaker('Первый фрагмент.', cancel)
    assert cancel.is_set()


def test_cancelled_server_task_has_a_terminal_cancel_message(monkeypatch):
    fake = FakeVoiceClient([{'type': 'status', 'status': 'CANCELLED'}], {})
    monkeypatch.setattr(local_voice, '_atlas_client', lambda _: fake)
    assert local_voice._send_to_atlas('Запрос', {}) == 'Запрос отменён в Atlas.'
