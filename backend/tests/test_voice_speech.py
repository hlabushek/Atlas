import threading
from types import SimpleNamespace

import pytest
from atlas.voice_speech import SpeechText, plain_speech
from atlas.voice_reply import VoiceReply
from atlas.router import ModelRouter, VOICE_INSTRUCTIONS


@pytest.mark.parametrize('text,expected', [
    ('**Важное** и _обычное_.', 'Важное и обычное.'),
    ('## Заголовок', 'Заголовок'),
    ('- [Сайт](https://example.org/a/b) и / символы.', 'Сайт и символы.'),
    ('1. Первый пункт.', 'Первый пункт.'),
    ('Обычный ответ без разметки.', 'Обычный ответ без разметки.'),
    ('| Название | Число |', ''),
])
def test_plain_speech(text, expected):
    assert plain_speech(text) == expected


def test_markdown_split_at_every_character_is_clean_and_streams_before_end():
    stream = SpeechText()
    text = '**Короткий ответ.**\n[Подробнее](https://example.org/a/b).\n```python\nprint("не читать.")\n```\nГотово!'
    pieces = []
    for char in text:
        pieces.extend(stream.feed(char))
    pieces.extend(stream.feed('', final=True))
    assert ' '.join(pieces) == 'Короткий ответ. Подробнее. Готово!'
    stream = SpeechText()
    assert stream.feed('Первое предложение. ') == ['Первое предложение.']
    assert stream.feed('Второе ещё не завершено') == []
    assert stream.feed('', final=True) == ['Второе ещё не завершено']


def test_reply_preserves_full_result_but_speaker_receives_plain_text():
    spoken = []
    complete = '**Это ответ.**\nПодробнее на [сайте](https://example.org).'
    def receive(reply):
        for char in complete: reply.say(char)
        return complete
    reply = VoiceReply(lambda text, _: spoken.append(text)).start(receive)
    assert reply.done.wait(2)
    assert reply.result == complete and not reply.error
    assert ' '.join(spoken) == 'Это ответ. Подробнее на сайте.'


def test_reply_does_not_flush_pending_text_after_cancel():
    ready, done = threading.Event(), threading.Event()
    spoken = []
    def receive(reply):
        reply.say('Не завершённая фраза без точки')
        ready.set()
        done.wait(2)
        return 'Не завершённая фраза без точки'
    reply = VoiceReply(lambda text, _: spoken.append(text)).start(receive)
    assert ready.wait(1)
    reply.cancel(); done.set()
    assert reply.done.wait(2) and spoken == []


def test_cleaning_cannot_hide_secret_markup():
    with pytest.raises(ValueError): SpeechText().feed('pass**word**=private-value.', final=True)


@pytest.mark.parametrize('text,expected', [('1. Первый пункт.', 'Первый пункт.'),
                                         ('Число 3.5 подходит.', 'Число 3.5 подходит.')])
def test_streamed_numbers_and_list_markers_are_not_split(text, expected):
    stream, spoken = SpeechText(), []
    for char in text: spoken.extend(stream.feed(char))
    spoken.extend(stream.feed('', final=True))
    assert ' '.join(spoken) == expected


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('voice', [False, True])
def test_only_voice_model_requests_get_short_natural_response_instruction(monkeypatch, streaming, voice):
    from atlas import router
    cfg = SimpleNamespace(provider='fixture', model='model', url='https://fixture.invalid', key='',
                          max_output_tokens=1024, rates=None, require=lambda: None)
    monkeypatch.setattr(router, 'resolve', lambda *a, **kw: cfg)
    bodies = []
    def request(client, url, headers, body, *args, **kwargs):
        bodies.append(body)
        if 'on_delta' in kwargs:
            kwargs['on_delta']('Короткий ответ.')
            return 'Короткий ответ.'
        return {'choices': [{'message': {'content': 'Короткий ответ.'}}]}
    monkeypatch.setattr(router, 'request', request)
    monkeypatch.setattr(router, 'stream_request', request)
    profile = {'response_style': 'voice_brief'} if voice else {}
    answer = ModelRouter().answer('Объясни', [], [], profile=profile, on_fragment=(lambda _: None) if streaming else None)
    assert answer == 'Короткий ответ.'
    assert (VOICE_INSTRUCTIONS in bodies[0]['messages'][0]['content']) is voice
    assert bodies[0]['max_tokens'] == 1024  # Generation style, not a global token limit/cut.


def test_worker_marks_only_voice_chat_with_voice_style(client, monkeypatch):
    from atlas.db import sessions
    from atlas.models import Task
    from atlas.worker import tick
    from atlas.voice_gateway import submit_voice
    from test_voice_center import setup_device
    _, fp, cid = setup_device(client)
    seen = []
    monkeypatch.setattr(ModelRouter, 'answer', lambda self, goal, history, memories, **kwargs:
                        seen.append(kwargs['profile']) or 'Короткий ответ.')
    with sessions()() as session:
        submit_voice(session, fp, {'request_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'text': 'Голосовой вопрос'})
    assert tick(ModelRouter()) and seen[-1]['response_style'] == 'voice_brief'
    with sessions()() as session:
        # A normal web chat task shares the conversation but not the voice style.
        session.add(Task(conversation_id=cid, request_key='web', goal='Веб-вопрос', model_profile=seen[-1] | {'response_style': None}))
        session.commit()
    assert tick(ModelRouter()) and seen[-1].get('response_style') is None
