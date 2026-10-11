import json
import threading
from contextlib import nullcontext
from types import SimpleNamespace
import sys
import time
import os

import pytest

from atlas.voice_dialogue import VoiceDialogue
from atlas import local_voice, voice_probe, voice_recognition, voice_settings


@pytest.fixture(autouse=True)
def isolated_voice_ipc(monkeypatch):
    # Real Windows synchronization, separate names: never pause the owner's listener.
    if os.name != 'nt':
        yield
        return
    import uuid
    kernel = voice_probe._windows_events()[0]
    prefix = 'Local\\AtlasTest' + uuid.uuid4().hex
    class IsolatedKernel:
        def __getattr__(self, name):
            function = getattr(kernel, name)
            if name in {'CreateEventW', 'CreateMutexW', 'OpenMutexW'}:
                def scoped(*args):
                    return function(*args[:-1], args[-1].replace('Local\\Atlas', prefix))
                return scoped
            return function
    scoped = IsolatedKernel()
    request = scoped.CreateEventW(None, True, False, 'Local\\AtlasVoiceProbeRequestV1')
    ack = scoped.CreateEventW(None, True, False, 'Local\\AtlasVoiceProbeAckV1')
    monkeypatch.setattr(voice_probe, '_EVENTS', (scoped, request, ack))
    try:
        yield
    finally:
        kernel.CloseHandle(request)
        kernel.CloseHandle(ack)


def test_confirmation_is_single_use_and_bound_to_current_permissions():
    d = VoiceDialogue()
    config = {'sites': {'дневник': 'https://school.example'}}
    actions = [{'type': 'open_site', 'site': 'дневник'}]
    d.propose('открой дневник', actions, config, 10)
    kind, pending = d.resolve('подтверждаю', config, 11)
    assert kind == 'confirmed' and pending.actions == actions
    assert d.resolve('да', config, 12) == ('missing', None)
    d.propose('открой дневник', actions, config, 13)
    config['sites']['дневник'] = 'https://different.example'
    assert d.resolve('подтверждаю', config, 14) == ('changed', None)


@pytest.mark.parametrize('phrase', ['нет', 'отмена', 'не надо', 'отмени действие', 'закончи разговор'])
def test_cancel_and_end_never_execute_pending_action(phrase):
    d = VoiceDialogue()
    d.propose('открой дневник', [{'type': 'open_site', 'site': 'дневник'}], {}, 1)
    kind, action = d.resolve(phrase, {}, 2)
    assert kind in {'cancel', 'end'} and action is None
    assert not d.active(3)


def test_confirmation_expires_and_a_new_request_replaces_it():
    d = VoiceDialogue()
    d.propose('команда', [{'type': 'media', 'command': 'next'}], {}, 1)
    assert d.resolve('подтверждаю', {}, 13) == ('missing', None)
    d.propose('команда', [{'type': 'media', 'command': 'next'}], {}, 20)
    assert d.resolve('который час', {}, 21) == ('request', None)
    assert d.resolve('да', {}, 22) == ('missing', None)
    d.extend(30, 6)
    assert d.active(35) and not d.active(36)
    d.extend(40, 0)
    assert not d.active(40)


def test_ambiguous_targets_and_disabled_scenarios_do_not_pick_first_match():
    config = {'sites': {'дневник': 'https://a.example'},
              'applications': {'дневник': {'executable': 'C:/app.exe'}},
              'scenarios': [{'name': 'Учёба', 'phrases': ['начни учёбу'],
                             'enabled': False, 'actions': [{'type': 'media', 'command': 'next'}]}]}
    decision = local_voice._classify_intent('открой дневник', config)
    assert decision['route'] == 'clarify' and len(decision['choices']) == 2
    assert local_voice._select_actions('открой дневник', config) is None
    assert local_voice._select_actions('начни учёбу', config) is None


@pytest.mark.parametrize('phrase', ['не сделай громче', 'расскажи как сделать громче',
                                  'объясни как поставить на паузу', 'как выключить звук'])
def test_explanations_and_negations_do_not_run_builtins(phrase):
    assert local_voice._classify_intent(phrase, {})['route'] == 'atlas'


@pytest.mark.skipif(os.name != 'nt', reason='Windows microphone listener')
@pytest.mark.parametrize('mode, texts, expected', [
    ('wake_word_followup', ['атлас', 'сделай громче'], [('volume_delta', 2)]),
    ('wake_word', ['сделай громче', 'атлас который час'], [('time_now', 2)]),
    ('wake_word', ['атлас открой дневник', 'подтверждаю'], [('open_site', 2)]),
    ('wake_word', ['атлас открой дневник', 'отмена', 'подтверждаю', 'атлас который час'], [('time_now', 4)]),
    ('wake_word', ['атлас открой дневник', 'который час', 'атлас который час'], [('time_now', 3)]),
    ('wake_word', ['атлас открой дневник', 'расскажи о погоде', 'подтверждаю'], [('open_site', 3)]),
    ('wake_word', ['атлас', 'сделай громче', 'атлас который час'], [('time_now', 3)]),
    ('wake_word', ['атласа сделай громче', 'ну атлас сделай громче', 'расскажи о погоде', 'атлас который час'], [('time_now', 4)]),
    ('wake_word', ['атлас сделай громче', 'сделай громче', 'атлас который час'], [('volume_delta', 1), ('time_now', 3)]),
    ('wake_word_followup', ['атлас сделай громче', 'сделай громче', 'атлас который час'], [('volume_delta', 1), ('volume_delta', 2), ('time_now', 3)]),
    ('push_to_talk', ['сделай громче', 'который час'], [('time_now', 2)]),
])
def test_listener_routes_separate_wake_confirmation_and_cancel(tmp_path, monkeypatch, mode, texts, expected):
    model = tmp_path / 'model'
    model.mkdir()
    path = tmp_path / 'voice.json'
    path.write_text(json.dumps({'enabled': True, 'model_path': str(model), 'speak_responses': False,
                                'activation_mode': mode, 'follow_up_seconds': 6,
                                'show_command_pill': False, 'sites': {'дневник': 'https://school.example'}}), encoding='utf-8')
    stop = threading.Event()
    actions = []
    position = [0]

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
        def __enter__(self):
            for _ in texts:
                self.callback(b'\0\0' * 4000, 4000, None, None)
            return self
        def __exit__(self, *_args):
            pass
        def stop(self):
            pass
        def start(self):
            pass

    class Recognizer:
        def __init__(self, *_args):
            pass
        def AcceptWaveform(self, _data):
            return True
        def Result(self):
            index = position[0]
            position[0] += 1
            return json.dumps({'text': texts[index], 'result': [{'conf': 0.95}]})

    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=Stream))
    monkeypatch.setitem(sys.modules, 'vosk', SimpleNamespace(Model=lambda _: object(), KaldiRecognizer=Recognizer))
    monkeypatch.setattr(voice_probe, 'diagnostic_requested', lambda: False)
    monkeypatch.setattr(voice_probe, 'listener_presence', lambda: nullcontext())
    monkeypatch.setattr(local_voice, '_make_speaker', lambda *_: None)
    monkeypatch.setattr(local_voice, '_warm_second_stage', lambda *_: None)
    monkeypatch.setattr(local_voice, '_whisper_transcribe', lambda *_: None)
    monkeypatch.setattr(local_voice, '_send_to_atlas', lambda *_: pytest.fail('Фоновая речь не должна уходить в Atlas'))
    monkeypatch.setattr(local_voice, '_ptt_pressed', lambda _: mode == 'push_to_talk' and position[0] == len(texts) - 1)
    monkeypatch.setattr(local_voice, 'record_history', lambda *_: None)

    def run(action, config):
        actions.append((action['type'], position[0]))
        if position[0] == len(texts):
            stop.set()
        return 'Готово'
    monkeypatch.setattr(local_voice, '_run_action', run)
    timer = threading.Timer(2, stop.set)
    timer.start()
    try:
        local_voice.listen(str(path), stop_event=stop)
    finally:
        timer.cancel()
    assert actions == expected


def test_strict_mode_ignores_legacy_duration_and_pending_is_not_a_conversation():
    config = {'activation_mode': 'wake_word', 'follow_up_seconds': 20}
    d = VoiceDialogue()
    d.extend(1, 20)
    assert local_voice._follow_up_seconds(config) == 0
    assert not d.accepts_unaddressed('расскажи о погоде', config, 2, True)
    d.propose('команда', [{'type': 'media', 'command': 'next'}], config, 2)
    assert d.accepts_unaddressed('подтверждаю', config, 3)
    assert not d.accepts_unaddressed('расскажи о погоде', config, 3)
    assert not d.accepts_unaddressed('подтверждаю', config, 14)
    config['activation_mode'] = 'wake_word_followup'
    assert local_voice._follow_up_seconds(config) == 20
    assert d.accepts_unaddressed('расскажи о погоде', config, 15)


@pytest.mark.parametrize('phrase, matched', [
    ('Атлас, расскажи о погоде', True), ('атласа расскажи о погоде', False),
    ('ну атлас расскажи о погоде', False), ('расскажи про атлас', False),
    ('атла расскажи о погоде', False),
])
def test_strict_wake_word_is_exact_and_leading(phrase, matched):
    assert local_voice._wake_tail(phrase, ['атлас'], strict=True)[0] is matched


def test_strict_activation_supports_custom_phrase_and_refinement_only_after_valid_wake():
    assert local_voice._activation_tail('Эй компьютер, расскажи о погоде', '', ['эй компьютер'], False, strict=True) == (True, 'расскажи о погоде')
    assert local_voice._activation_tail('Расскажи о погоде', 'атлас расскажи о погоде', ['атлас'], False, strict=True) == (True, 'Расскажи о погоде')
    assert local_voice._activation_tail('Расскажи о погоде', 'атласа расскажи о погоде', ['атлас'], False, strict=True) == (False, '')
    payload = json.dumps({'alternatives': [{'text': 'атласа сделай громче', 'confidence': 0.95}, {'text': 'атлас который час', 'confidence': 0.9}]})
    assert local_voice._best_recognized_phrase(payload, 0.55, {}, ['атлас'], False) == 'атлас который час'


def test_activation_modes_round_trip_in_profiles_and_unknown_mode_is_rejected(tmp_path):
    path = tmp_path / 'voice.json'
    path.write_text('{"enabled":true}', encoding='utf-8')
    for mode in ('wake_word', 'wake_word_followup', 'push_to_talk'):
        s = voice_settings.update_settings(path, {'activation_mode': mode, 'profile_name': mode})
        assert s['activation_mode'] == mode
    assert voice_settings.activate_profile(path, 'wake_word')['follow_up_seconds'] == 0
    with pytest.raises(ValueError, match='активации'):
        voice_settings.update_settings(path, {'activation_mode': 'always_listen'})


@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher')
def test_model_settings_bridge_only_launches_fixed_authenticated_ui(monkeypatch, tmp_path):
    import subprocess
    executable = tmp_path / 'Scripts' / 'pythonw.exe'
    executable.parent.mkdir()
    executable.touch()
    calls = []
    monkeypatch.setattr(sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    voice_settings.VoiceSettingsApi(tmp_path / 'unused.json').open_model_settings()
    assert calls[0][0] == [str(executable), '-m', 'atlas.local_voice', '--desktop-window', '--model-settings']
    assert 'shell' not in calls[0][1]


@pytest.mark.skipif(os.name != 'nt', reason='Windows listener')
def test_strict_listener_does_not_reactivate_after_a_completed_atlas_reply(tmp_path, monkeypatch):
    model = tmp_path / 'model'
    model.mkdir()
    path = tmp_path / 'voice.json'
    path.write_text(json.dumps({'enabled': True, 'model_path': str(model), 'follow_up_seconds': 20,
                               'speak_responses': False, 'show_command_pill': False}), encoding='utf-8')
    stop, completed = threading.Event(), threading.Event()
    requests, actions, refinements = [], [], []
    streams = []
    texts = ['атлас расскажи о погоде', 'расскажи о котах', 'сделай громче', 'атлас который час']

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
            streams.append(self)
        def emit(self, index):
            self.callback(index.to_bytes(2, 'little') * 4000, 4000, None, None)
        def __enter__(self):
            self.emit(0)
            return self
        def __exit__(self, *_): pass
        def stop(self): pass
        def start(self): pass

    class Recognizer:
        def __init__(self, *_): self.index = 0
        def AcceptWaveform(self, data):
            self.index = int.from_bytes(data[:2], 'little')
            return True
        def Result(self):
            return json.dumps({'text': texts[self.index], 'result': [{'conf': 0.95}]})
        def Reset(self): pass

    def send(text, *_):
        requests.append(text)
        return 'Ответ тестовой сети, без обращения к провайдеру.'
    def history(_path, _kind, message, *_):
        if message == 'Ответ получен': completed.set()
    def status(state):
        if state == 'listening' and completed.is_set():
            completed.clear()
            for index in range(1, len(texts)): streams[0].emit(index)
    def run(action, _):
        actions.append(action['type'])
        stop.set()
        return 'Готово'
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=Stream))
    monkeypatch.setitem(sys.modules, 'vosk', SimpleNamespace(Model=lambda _: object(), KaldiRecognizer=Recognizer))
    monkeypatch.setattr(voice_probe, 'listener_presence', lambda: nullcontext())
    monkeypatch.setattr(voice_probe, 'diagnostic_requested', lambda: False)
    monkeypatch.setattr(local_voice, '_make_speaker', lambda *_: None)
    monkeypatch.setattr(local_voice, '_warm_second_stage', lambda *_: None)
    monkeypatch.setattr(local_voice, '_whisper_transcribe', lambda audio, _: refinements.append(audio) or None)
    monkeypatch.setattr(local_voice, '_ptt_pressed', lambda _: False)
    monkeypatch.setattr(local_voice, '_send_to_atlas', send)
    monkeypatch.setattr(local_voice, '_run_action', run)
    monkeypatch.setattr(local_voice, 'record_history', history)
    timer = threading.Timer(3, stop.set)
    timer.start()
    try:
        local_voice.listen(str(path), stop_event=stop, status_callback=status)
    finally:
        timer.cancel()
    assert requests == ['расскажи о погоде']
    assert len(refinements) == 1  # Only the activated request uses Whisper.
    assert actions == ['time_now']


@pytest.mark.parametrize('field, value', [('no_speech_prob', 0.9), ('avg_logprob', -2.0), ('compression_ratio', 3.0)])
def test_whisper_quality_gate_rejects_entire_uncertain_command(field, value):
    good = SimpleNamespace(text='открой', no_speech_prob=0.1, avg_logprob=-0.2, compression_ratio=1.0)
    bad = SimpleNamespace(text='дневник', no_speech_prob=0.1, avg_logprob=-0.2, compression_ratio=1.0)
    setattr(bad, field, value)
    assert voice_recognition.usable_segments([good, bad]) == ''


def test_whisper_uses_actual_input_rate_and_falls_back_on_model_failure(monkeypatch):
    rates = []
    monkeypatch.setattr(voice_recognition, 'pcm_audio', lambda data, rate: rates.append(rate) or SimpleNamespace(size=1))
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([SimpleNamespace(text='Расскажи о погоде.')], None))
    monkeypatch.setattr(voice_recognition, 'prepare_whisper', lambda _: model)
    result = voice_recognition.transcribe_pcm(b'audio', {'free_speech_enabled': True, 'sample_rate': 32000})
    assert result.status == 'ok' and result.text == 'Расскажи о погоде.' and rates == [32000]
    monkeypatch.setattr(voice_recognition, 'prepare_whisper', lambda _: (_ for _ in ()).throw(RuntimeError('private model path')))
    result = voice_recognition.transcribe_pcm(b'audio', {'free_speech_enabled': True})
    assert result.status == 'unavailable' and result.text == ''
    assert voice_recognition._MODEL is None and voice_recognition._KEY is None


def test_complete_model_detection_supports_json_vocabulary_and_no_silent_replacement(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    model = tmp_path / 'Atlas' / 'Voice' / 'Models' / 'whisper-large'
    model.mkdir(parents=True)
    for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.json'):
        (model / name).write_text('{}', encoding='utf-8')
    assert voice_settings._resolve_free_speech_model(str(model)) == model
    assert voice_settings._resolve_free_speech_model(str(tmp_path / 'missing')) is None
    assert voice_settings.list_free_speech_models('')[0]['name'] == 'whisper-large'


def wait_stage(probe, stages):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = probe.state()
        if state['stage'] in stages:
            return state
        time.sleep(0.005)
    raise AssertionError(probe.state())


def setup_probe(monkeypatch):
    from atlas.voice_recognition import Transcript
    calls = []
    recognizer = SimpleNamespace(SetWords=lambda _: None, AcceptWaveform=lambda data: calls.append(('vosk', data)),
                                 FinalResult=lambda: '{"text":"расскажи о погоде","result":[{"conf":0.95}]}')
    monkeypatch.setitem(sys.modules, 'vosk', SimpleNamespace(Model=lambda _: object(), KaldiRecognizer=lambda *_: recognizer))
    monkeypatch.setattr(voice_recognition, 'warm_whisper', lambda _: None)
    monkeypatch.setattr(voice_recognition, 'transcribe_pcm', lambda data, _: calls.append(('whisper', data)) or
                        Transcript('Расскажи о погоде.', 'ok', 0.1))
    monkeypatch.setattr(voice_probe, '_exclusive_probe', lambda: nullcontext())
    monkeypatch.setattr(voice_probe, '_pause_listener', lambda _: nullcontext())
    monkeypatch.setattr(voice_probe, 'capture_pcm', lambda *_: calls.append(('capture', None)) or b'\x01\x00' * 100)
    return calls


def test_probe_waits_for_user_and_compares_identical_recording(monkeypatch):
    calls = setup_probe(monkeypatch)
    probe = voice_probe.RecognitionProbe()
    probe.prepare({'model_path': 'local-model', 'free_speech_enabled': True}, seconds=6)
    wait_stage(probe, {'ready'})
    assert calls == []
    with pytest.raises(ValueError, match='уже'):
        probe.prepare({'model_path': 'local-model'})
    probe.begin()
    state = wait_stage(probe, {'completed'})
    assert state['result']['whisper'] == 'Расскажи о погоде.'
    assert calls[1][1] == calls[2][1] and calls[0][0] == 'capture'


def test_probe_cancel_before_begin_never_opens_microphone(monkeypatch):
    calls = setup_probe(monkeypatch)
    probe = voice_probe.RecognitionProbe()
    probe.prepare({'model_path': 'local-model'})
    wait_stage(probe, {'ready'})
    probe.cancel()
    wait_stage(probe, {'cancelled'})
    assert calls == []


def test_probe_exception_does_not_publish_paths_or_transcripts(monkeypatch):
    setup_probe(monkeypatch)
    monkeypatch.setattr(voice_probe, 'capture_pcm', lambda *_: (_ for _ in ()).throw(ValueError('password=secret /private/model')))
    probe = voice_probe.RecognitionProbe()
    probe.prepare({'model_path': 'local-model'})
    wait_stage(probe, {'ready'})
    probe.begin()
    state = wait_stage(probe, {'error'})
    assert 'password' not in json.dumps(state) and '/private' not in json.dumps(state)


def test_probe_private_result_and_history_are_redacted(tmp_path, monkeypatch):
    from atlas.voice_recognition import Transcript
    setup_probe(monkeypatch)
    monkeypatch.setattr(voice_recognition, 'transcribe_pcm', lambda *_: Transcript('пароль: secret', 'ok'))
    result = voice_probe.compare_pcm(b'\0\0', {}, SimpleNamespace(AcceptWaveform=lambda _: None,
                                    FinalResult=lambda: '{"text":"привет"}'))
    assert result['redacted'] is True and 'secret' not in json.dumps(result)
    path = tmp_path / 'voice.json'
    voice_settings.record_history(path, 'local', 'Отклонено', 'пароль: secret')
    assert 'secret' not in (tmp_path / 'history.jsonl').read_text(encoding='utf-8')


@pytest.mark.parametrize('values', [{'follow_up_seconds': 21}, {'follow_up_seconds': True},
                                  {'confirmation_timeout_seconds': 2}])
def test_dialogue_settings_reject_unbounded_windows(tmp_path, values):
    path = tmp_path / 'voice.json'
    path.write_text('{"enabled":true}', encoding='utf-8')
    with pytest.raises(ValueError):
        voice_settings.update_settings(path, values)


def test_conflicting_vosk_alternatives_require_clarification():
    payload = json.dumps({'alternatives': [{'text': 'атлас сделай громче', 'confidence': 0.92},
                                          {'text': 'атлас сделай тише', 'confidence': 0.9}]})
    assert local_voice._conflicting_hypotheses(payload, {}, ['атлас'], False)
    payload = json.dumps({'alternatives': [{'text': 'атлас сделай громче', 'confidence': 0.92},
                                          {'text': 'атлас прибавь громкость', 'confidence': 0.9}]})
    assert not local_voice._conflicting_hypotheses(payload, {}, ['атлас'], False)


@pytest.mark.parametrize('actual, errors', [('Расскажи, какая сегодня погода?', 0),
                                         ('расплежь какая сегодня погода', 1),
                                         ('расскажи погода', 2), ('расскажи какая сегодня погода завтра', 1)])
def test_word_error_measurement_counts_substitutions_deletions_and_insertions(actual, errors):
    result = voice_recognition.word_error_rate('расскажи какая сегодня погода', actual)
    assert result['errors'] == errors and result['words'] == 4


def test_long_recording_keeps_complete_vosk_instead_of_using_only_audio_tail():
    audio = voice_recognition.PhraseAudio(16000, seconds=1)
    audio.append(b'\0\0' * 16000)
    audio.append(b'\1\0' * 100)
    assert audio.take() == b''
    audio.append(b'\1\0' * 100)
    assert audio.take() == b'\1\0' * 100


def test_probe_redacts_raw_vosk_before_punctuation_is_normalized(monkeypatch):
    monkeypatch.setattr(voice_recognition, 'transcribe_pcm', lambda *_: pytest.fail('private audio must not be refined'))
    recognizer = SimpleNamespace(AcceptWaveform=lambda _: None,
                                 FinalResult=lambda: '{"text":"пароль: private-value"}')
    result = voice_probe.compare_pcm(b'\0\0', {}, recognizer)
    assert result['redacted'] is True and 'private-value' not in json.dumps(result)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named events')
def test_probe_waits_for_listener_acknowledgment():
    ready, stop = threading.Event(), threading.Event()
    acknowledgments = []
    def listener():
        with voice_probe.listener_presence():
            ready.set()
            while not stop.wait(0.005):
                if voice_probe.diagnostic_requested():
                    acknowledgments.append(True)
                    voice_probe.acknowledge_diagnostic()
    worker = threading.Thread(target=listener)
    worker.start()
    try:
        assert ready.wait(1)
        with voice_probe._exclusive_probe(), voice_probe._pause_listener(threading.Event()):
            assert acknowledgments
    finally:
        stop.set()
        worker.join(timeout=1)
    assert not worker.is_alive() and not voice_probe.diagnostic_requested()


@pytest.mark.skipif(os.name != 'nt', reason='Windows microphone listener')
def test_runtime_disable_stops_capture_and_drops_buffered_commands(tmp_path, monkeypatch):
    model = tmp_path / 'model'
    model.mkdir()
    path = tmp_path / 'voice.json'
    path.write_text(json.dumps({'enabled': True, 'model_path': str(model),
                               'speak_responses': False, 'show_command_pill': False}), encoding='utf-8')
    stop = threading.Event()
    actions, stopped = [], []
    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
        def __enter__(self):
            for _ in range(2):
                self.callback(b'\0\0' * 4000, 4000, None, None)
            return self
        def __exit__(self, *_):
            pass
        def stop(self):
            stopped.append(True)
        def start(self):
            pass
    recognizer = SimpleNamespace(AcceptWaveform=lambda _: True,
        Result=lambda: '{"text":"атлас сделай громче","result":[{"conf":0.95}]}')
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=Stream))
    monkeypatch.setitem(sys.modules, 'vosk', SimpleNamespace(Model=lambda _: object(), KaldiRecognizer=lambda *_: recognizer))
    monkeypatch.setattr(voice_probe, 'listener_presence', lambda: nullcontext())
    monkeypatch.setattr(voice_probe, 'diagnostic_requested', lambda: False)
    monkeypatch.setattr(local_voice, '_make_speaker', lambda *_: None)
    monkeypatch.setattr(local_voice, '_warm_second_stage', lambda *_: None)
    monkeypatch.setattr(local_voice, 'record_history', lambda *_: None)
    def run(action, _config):
        actions.append(action)
        voice_settings.update_settings(path, {'enabled': False})
        return 'Готово'
    monkeypatch.setattr(local_voice, '_run_action', run)
    def state(status):
        if status == 'paused':
            stop.set()
    timer = threading.Timer(2, stop.set)
    timer.start()
    try:
        local_voice.listen(str(path), stop_event=stop, status_callback=state)
    finally:
        timer.cancel()
    assert len(actions) == 1 and stopped


@pytest.mark.skipif(os.name != 'nt', reason='Windows named events')
def test_diagnostic_request_is_visible_to_listener_and_recovers_after_probe_exit():
    kernel, request, ack = voice_probe._windows_events()
    result = []
    try:
        with voice_probe._exclusive_probe():
            kernel.SetEvent(request)
            worker = threading.Thread(target=lambda: result.append(voice_probe.diagnostic_requested()))
            worker.start()
            worker.join(timeout=1)
            assert result == [True]
        # A crashed/finished probe must not keep an already running tray paused.
        assert voice_probe.diagnostic_requested() is False
        assert kernel.WaitForSingleObject(request, 0) == 0x102
    finally:
        kernel.ResetEvent(request)
        kernel.ResetEvent(ack)
