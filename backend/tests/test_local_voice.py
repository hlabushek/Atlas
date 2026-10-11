import pytest
import queue
import json
import os
import sys
import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace

from atlas import local_voice
from atlas.local_voice import (_activation_tail, _best_recognized_phrase, _classify_intent,
                               _parse_builtin, _ptt_virtual_key, _recognized_candidates,
                               _recognized_text, _run_action, _select_actions, _speech_chunks,
                               _voice_mode_command, _wake_tail)
from atlas.command_pill import CommandPill
from atlas.voice_settings import VoiceSettingsApi, read_history, record_history, update_settings, validate_scenarios


@pytest.mark.parametrize(("phrase", "expected"), [
    ("поставь на паузу", [{"type": "media", "command": "play_pause"}]),
    ("сделай погромче", [{"type": "volume_delta", "percent": 10}]),
    ("громкость 35 процентов", [{"type": "volume_set", "percent": 35}]),
])
def test_local_voice_maps_common_pc_commands_to_fixed_actions(phrase, expected):
    assert _select_actions(phrase, {}) == expected


@pytest.mark.parametrize(('phrase', 'expected'), [
    ('Атлас, прибавь звук', [{'type': 'volume_delta', 'percent': 10}]),
    ('можешь сделать погромче на двадцать процентов', [{'type': 'volume_delta', 'percent': 20}]),
    ('убавь громкость на 5', [{'type': 'volume_delta', 'percent': -5}]),
    ('установи громкость на 35 процентов', [{'type': 'volume_set', 'percent': 35}]),
    ('заглуши звук', [{'type': 'mute_set', 'muted': True}]),
    ('сними беззвучный режим', [{'type': 'mute_set', 'muted': False}]),
    ('возобнови музыку', [{'type': 'media', 'command': 'play_pause'}]),
    ('следующий', [{'type': 'media', 'command': 'next'}]),
    ('верни предыдущий трек', [{'type': 'media', 'command': 'previous'}]),
    ('какие команды ты понимаешь', [{'type': 'show_help'}]),
    ('Атлас, который сейчас час', [{'type': 'time_now'}]),
    ('какое сегодня число', [{'type': 'date_today'}]),
    ('открой настройки голоса', [{'type': 'open_voice_settings'}]),
])
def test_local_voice_supports_natural_command_variants(phrase, expected):
    assert _parse_builtin(phrase) == expected


@pytest.mark.parametrize(('phrase', 'expected'), [
    ('прибавь громкасть на двадцать', [{'type': 'volume_delta', 'percent': 20}]),
    ('поставь на паузы', [{'type': 'media', 'command': 'play_pause'}]),
    ('убавь громкост на 5', [{'type': 'volume_delta', 'percent': -5}]),
])
def test_local_voice_tolerates_common_russian_asr_distortions(phrase, expected):
    assert _parse_builtin(phrase) == expected


def test_local_voice_wake_word_tolerates_only_small_leading_distortion():
    assert _wake_tail('ну атла сделай погромче', ['атлас']) == (True, 'сделай погромче')
    assert _wake_tail('атласа поставь на паузу', ['атлас']) == (True, 'поставь на паузу')
    assert _wake_tail('расскажи про атлас', ['атлас']) == (False, 'расскажи про атлас')
    assert _activation_tail('расскажи о погоде', 'атлас расскажи о погоде', ['атлас'], False) == (True, 'расскажи о погоде')


def test_local_voice_classifier_separates_local_allowlist_from_free_speech():
    local = _classify_intent('сделай погромче', {})
    free = _classify_intent('расскажи, что у меня запланировано на завтра', {})
    assert local['route'] == 'local' and local['confidence'] == 1.0
    assert free['route'] == 'atlas' and free['actions'] is None


def test_local_voice_fuzzy_target_matching_stays_inside_allowlist():
    config = {
        'sites': {'дневник': 'https://school.example'},
        'site_phrases': {'дневник': ['школьный дневник']},
    }
    assert _select_actions('открой школьный дневник', config) == [{'type': 'open_site', 'site': 'дневник'}]
    assert _select_actions('открой школьный дневники', config) == [{'type': 'open_site', 'site': 'дневник'}]
    assert _select_actions('открой неизвестный дневники', config) is None


def test_local_voice_splits_long_speech_into_interruptible_sentences():
    assert _speech_chunks('Первый ответ. Второй ответ!') == ['Первый ответ.', 'Второй ответ!']
    chunks = _speech_chunks('слово ' * 120, limit=80)
    assert len(chunks) > 1 and all(len(chunk) <= 80 for chunk in chunks)


def test_local_voice_returns_local_help_time_and_date_responses():
    assert 'Команды:' in _run_action({'type': 'show_help'}, {})
    assert _run_action({'type': 'time_now'}, {}).startswith('Сейчас ')
    assert _run_action({'type': 'date_today'}, {}).startswith('Сегодня ')


def test_local_voice_matches_polite_site_and_app_variants_only_for_allowlisted_targets():
    config = {
        'sites': {'дневник': 'https://school.example'},
        'site_phrases': {'дневник': ['покажи школьный дневник']},
        'applications': {'блокнот': {'executable': 'C:/Windows/notepad.exe'}},
    }
    assert _select_actions('пожалуйста, перейди на дневник', config) == [{'type': 'open_site', 'site': 'дневник'}]
    assert _select_actions('можешь открыть школьный дневник пожалуйста', config) == [{'type': 'open_site', 'site': 'дневник'}]
    assert _select_actions('можешь открыть приложение блокнот пожалуйста', config) == [{'type': 'launch_app', 'app': 'блокнот'}]
    assert _select_actions('открой неизвестное приложение', config) is None


def test_local_voice_scenario_name_accepts_natural_activation_verbs():
    config = {'scenarios': [{
        'name': 'Рабочий режим',
        'phrases': ['начни рабочий режим', 'пора работать'],
        'actions': [{'type': 'volume_set', 'percent': 35}],
    }]}
    expected = [{'type': 'volume_set', 'percent': 35}]
    assert _select_actions('включи рабочий режим', config) == expected
    assert _select_actions('можешь подготовь рабочий режим пожалуйста', config) == expected


def test_local_voice_modes_are_explicit_and_unrecognized_actions_stay_out_of_os():
    assert _voice_mode_command("перейди в режим задач") == "task"
    assert _voice_mode_command("обычный режим") == "chat"
    assert _voice_mode_command("открой PowerShell") is None
    assert _select_actions("открой PowerShell", {}) is None


def test_command_pill_bounds_queue_and_truncates_transient_text():
    pill = CommandPill.__new__(CommandPill)
    pill._events = queue.Queue(maxsize=2)
    pill._close_after = 5
    pill.show('x' * 300)
    assert len(pill._events.get_nowait()[0]) == 180
    pill.show('команда 1')
    pill.update('команда 2', 'обновление')
    pill.finish('последняя', 'готово')

    assert pill._events.get_nowait() == ('команда 2', 'обновление', None)
    command, status, timeout = pill._events.get_nowait()
    assert (command, status, timeout) == ('последняя', 'готово', 5)
    assert pill._events.empty()
    assert len('x' * 300) > 180


def test_voice_settings_update_is_validated_and_preserves_local_paths(tmp_path):
    model = tmp_path / 'vosk-model'
    model.mkdir()
    tts = tmp_path / 'voice.onnx'
    tts.write_bytes(b'model')
    config_path = tmp_path / 'voice.json'
    config_path.write_text('''{
      "enabled": true,
      "model_path": "''' + str(model).replace('\\', '/') + '''",
      "tts_model_path": "''' + str(tts).replace('\\', '/') + '''",
      "companion_config": "C:/private/companion.json",
      "wake_words": ["атлас"], "default_mode": "chat", "sample_rate": 16000,
      "speak_responses": true, "tts_length_scale": 0.94,
      "reply_timeout_seconds": 90, "show_command_pill": true, "command_pill_seconds": 5,
      "sites": {"атлас": "https://atlas.example"},
      "scenarios": [{"name": "Работа", "phrases": ["начни работу"], "actions": [{"type": "open_site", "site": "атлас"}]}]
    }''', encoding='utf-8')

    result = update_settings(config_path, {
        'enabled': True, 'wake_words': ['Атлас', 'компьютер'], 'default_mode': 'task',
        'sample_rate': 16000, 'speak_responses': False, 'tts_length_scale': 1.05,
        'reply_timeout_seconds': 45, 'show_command_pill': True, 'command_pill_seconds': 8,
    })

    assert result['wake_words'] == ['атлас', 'компьютер']
    assert result['default_mode'] == 'task'
    assert result['free_speech_enabled'] is True
    saved = config_path.read_text(encoding='utf-8')
    assert 'companion.json' in saved
    assert 'начни работу' in saved


def test_voice_settings_reject_unsafe_values(tmp_path):
    config_path = tmp_path / 'voice.json'
    config_path.write_text('{"enabled": true, "wake_words": ["атлас"], "default_mode": "chat", "sample_rate": 16000, "speak_responses": true, "tts_length_scale": 0.94, "reply_timeout_seconds": 90, "show_command_pill": true, "command_pill_seconds": 5}', encoding='utf-8')
    with pytest.raises(ValueError, match='от 1 до 5'):
        update_settings(config_path, {'wake_words': []})
    with pytest.raises(ValueError, match='режим'):
        update_settings(config_path, {'default_mode': 'shell'})
    with pytest.raises(ValueError, match='абсолютным'):
        update_settings(config_path, {'free_speech_model_path': 'models/whisper-small'})


def test_voice_settings_bridge_does_not_expose_window_and_can_select_microphone(tmp_path, monkeypatch):
    from atlas import voice_settings

    path = tmp_path / 'voice.json'
    path.write_text('{"enabled": true}', encoding='utf-8')
    monkeypatch.setattr(voice_settings, 'list_audio_devices', lambda: [
        {'index': 7, 'name': 'Тестовый микрофон', 'channels': 1, 'default': False},
    ])
    api = VoiceSettingsApi(path)
    window = SimpleNamespace(destroy=lambda: None)
    window.native = window  # pywebview would recurse if this were a public API field
    api._window = window

    assert all(callable(getattr(api, name)) for name in dir(api) if not name.startswith('_'))
    assert api.get_settings()['audio_devices'][0]['index'] == 7
    assert api.save_settings({'device_index': 7})['device_index'] == 7
    assert voice_settings.effective_config(path)['device_index'] == 7
    api.close_window()


def test_voice_confidence_filter_and_local_history(tmp_path):
    assert _recognized_text('{"text":"атлас привет","result":[{"conf":0.92}]}', 0.55) == 'атлас привет'
    assert _recognized_text('{"text":"шум","result":[{"conf":0.2}]}', 0.55) == ''
    config_path = tmp_path / 'voice.json'
    config_path.write_text('{"enabled": true}', encoding='utf-8')
    record_history(config_path, 'local', 'Выполнено', 'поставь на паузу')
    record_history(config_path, 'atlas', 'Передано в Atlas', 'секрет не должен сохраниться')
    rows = read_history(config_path)
    assert rows[0]['command'] == 'поставь на паузу'
    assert 'command' not in rows[1]


def test_local_voice_prefers_an_alternative_that_matches_a_safe_intent():
    payload = json.dumps({'alternatives': [
        {'confidence': 0.91, 'text': 'сделай громкасть'},
        {'confidence': 0.86, 'text': 'сделай громче'},
    ]})
    candidates = _recognized_candidates(payload, 0.55)
    assert candidates[0][0] == 'сделай громкасть'
    assert _best_recognized_phrase(payload, 0.55, {}, ['атлас'], True) == 'сделай громче'


def test_voice_scenarios_allow_only_allowlisted_actions():
    config = {'sites': {'атлас': 'https://atlas.example'}, 'applications': {'блокнот': {'executable': 'C:/Windows/notepad.exe'}}}
    valid = [{'name': 'Работа', 'phrases': ['начни работу'], 'actions': [{'type': 'open_site', 'site': 'атлас'}]}]
    assert validate_scenarios(valid, config)[0]['name'] == 'Работа'
    with pytest.raises(ValueError, match='запрещённое'):
        validate_scenarios([{'name': 'Опасно', 'phrases': ['запусти'], 'actions': [{'type': 'shell'}]}], config)


def test_push_to_talk_uses_correct_windows_function_keys():
    assert _ptt_virtual_key('F1') == 0x70
    assert _ptt_virtual_key('F8') == 0x77
    assert _ptt_virtual_key('F12') == 0x7B
    assert _ptt_virtual_key('ESC') == 0x1B
    assert _ptt_virtual_key('F13') is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows microphone listener')
@pytest.mark.parametrize('activation_mode', ['wake_word', 'push_to_talk'])
def test_final_phrase_after_silence_runs_local_action(tmp_path, monkeypatch, activation_mode):
    model = tmp_path / 'model'
    model.mkdir()
    config = tmp_path / 'voice.json'
    config.write_text(json.dumps({
        'enabled': True, 'model_path': str(model), 'speak_responses': False,
        'show_command_pill': False, 'wake_words': ['атлас'],
        'activation_mode': activation_mode, 'ptt_key': 'F8',
        'vad_enabled': True, 'silence_timeout_ms': 200,
    }), encoding='utf-8')
    stop = threading.Event()
    actions = []

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']

        def __enter__(self):
            self.callback(b'\x01\x00' * 4000, 4000, None, None)
            self.callback(b'\x00\x00' * 4000, 4000, None, None)
            return self

        def __exit__(self, *args):
            return None

        def stop(self):
            return None

        def start(self):
            return None

    class FakeRecognizer:
        def __init__(self, *_args):
            self.calls = 0

        def AcceptWaveform(self, _data):
            self.calls += 1
            if self.calls == 2:
                time.sleep(0.25)
                return True
            return False

        def Result(self):
            phrase = 'атлас сделай погромче' if activation_mode == 'wake_word' else 'сделай погромче'
            return json.dumps({'text': phrase, 'result': [{'conf': 0.95}]})

    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=FakeStream))
    monkeypatch.setitem(sys.modules, 'vosk', SimpleNamespace(Model=lambda _path: object(), KaldiRecognizer=FakeRecognizer))
    monkeypatch.setattr(local_voice, '_make_speaker', lambda *_args: None)
    monkeypatch.setattr(local_voice, '_warm_second_stage', lambda *_args: None)
    from atlas import voice_probe
    monkeypatch.setattr(voice_probe, 'listener_presence', lambda: nullcontext())
    monkeypatch.setattr(voice_probe, 'diagnostic_requested', lambda: False)
    monkeypatch.setattr(local_voice, 'record_history', lambda *_args: None)
    presses = iter([activation_mode == 'push_to_talk', False])
    monkeypatch.setattr(local_voice, '_ptt_pressed', lambda _key: next(presses, False))

    def run_action(action, _config):
        actions.append(action)
        stop.set()
        return 'Готово'

    monkeypatch.setattr(local_voice, '_run_action', run_action)
    deadline = threading.Timer(2, stop.set)
    deadline.start()
    try:
        local_voice.listen(str(config), stop_event=stop)
    finally:
        deadline.cancel()
    assert actions == [{'type': 'volume_delta', 'percent': 10}]
