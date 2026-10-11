"""Local Windows voice commands. Audio is processed on this PC and never uploaded."""
from __future__ import annotations

import json
import difflib
import os
import queue
import re
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
import httpx
from pathlib import Path
from urllib.parse import urlsplit
from .voice_settings import (_resolve_tts_model, config_path,
                             effective_config, open_settings_window, record_history)
from .voice_dialogue import VoiceDialogue
from .voice_recognition import PhraseAudio, transcribe_pcm, warm_whisper
from .voice_privacy import private_transcript


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFKC', text).lower().replace('ё', 'е')
    return ' '.join(re.findall(r'[a-zа-я0-9]+', text))


def _audio_level(data: bytes) -> float:
    if not data:
        return 0.0
    import array
    samples = array.array('h')
    samples.frombytes(data[:len(data) - (len(data) % 2)])
    if not samples:
        return 0.0
    return min(1.0, (sum(sample * sample for sample in samples) / len(samples)) ** 0.5 / 32768)


def _ptt_pressed(key: str) -> bool:
    if os.name != 'nt':
        return False
    import ctypes
    virtual_key = _ptt_virtual_key(key)
    return bool(virtual_key and ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)


def _ptt_virtual_key(key: str) -> int | None:
    if key in {'SPACE', 'ESC'}:
        return {'SPACE': 0x20, 'ESC': 0x1B}[key]
    if key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return 0x70 + int(key[1:]) - 1
    return None


def _recognized_text(payload: str, minimum_confidence: float) -> str:
    candidates = _recognized_candidates(payload, minimum_confidence)
    return candidates[0][0] if candidates else ''


def _recognized_candidates(payload: str, minimum_confidence: float) -> list[tuple[str, float]]:
    """Return accepted Vosk hypotheses ordered by recognizer confidence."""
    result = json.loads(payload)
    alternatives = result.get('alternatives')
    raw_candidates = alternatives if isinstance(alternatives, list) and alternatives else [result]
    accepted: list[tuple[str, float]] = []
    for candidate in raw_candidates[:3]:
        if not isinstance(candidate, dict):
            continue
        words = candidate.get('result', [])
        confidences = [float(word.get('conf', 0.0)) for word in words if isinstance(word, dict)] \
            if isinstance(words, list) else []
        fallback_confidence = sum(confidences) / len(confidences) if confidences else 1.0
        raw_confidence = candidate.get('confidence')
        try:
            confidence = float(raw_confidence) if raw_confidence is not None else fallback_confidence
        except (TypeError, ValueError):
            confidence = fallback_confidence
        if confidence < minimum_confidence:
            continue
        text = normalize(str(candidate.get('text', '')))
        if text:
            accepted.append((text, confidence))
    accepted.sort(key=lambda item: item[1], reverse=True)
    return accepted


def _raw_recognized_text(payload: str) -> str:
    """Read Vosk's text without applying the confidence gate."""
    result = json.loads(payload)
    if isinstance(result.get('alternatives'), list) and result['alternatives']:
        first = result['alternatives'][0]
        return _clean_free_speech(str(first.get('text', ''))) if isinstance(first, dict) else ''
    return _clean_free_speech(str(result.get('text', '')))


def _clean_free_speech(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace('\x00', ' ')).strip()[:16000]


def _whisper_transcribe(audio_bytes: bytes, config: dict) -> str | None:
    result = transcribe_pcm(audio_bytes, config)
    return result.text or None


def _warm_second_stage(config: dict) -> None:
    if config.get('free_speech_enabled') is not True or config.get('enabled') is not True:
        return
    snapshot = {key: config.get(key) for key in ('free_speech_enabled', 'free_speech_model_path',
                                               'free_speech_device', 'free_speech_compute_type')}
    def warm():
        try:
            warm_whisper(snapshot)
            print('Whisper подготовлен для свободной речи.', flush=True)
        except Exception:
            print('Whisper недоступен; быстрый распознаватель Vosk продолжает работать.', flush=True)
    threading.Thread(target=warm, daemon=True).start()


def _best_recognized_phrase(payload: str, minimum_confidence: float, config: dict,
                            wake_phrases: list[str], push_to_talk: bool) -> str:
    """Choose a usable hypothesis, preferring one that maps to a safe action."""
    candidates = _recognized_candidates(payload, minimum_confidence)
    if not candidates:
        return ''
    if not push_to_talk:
        for text, _confidence in candidates:
            if _wake_tail(text, wake_phrases, strict=config.get('activation_mode', 'wake_word') != 'wake_word_followup')[0]:
                return text
    for text, _confidence in candidates:
        if _voice_mode_command(text) or _select_actions(text, config) is not None:
            return text
    return candidates[0][0]


_COMMON_ASR_WORDS = {
    # Частые варианты малого русскоязычного Vosk-пакета. Они применяются
    # только к маршрутизации локальных команд, а не к тексту, который уходит
    # в Atlas.
    'громкасть': 'громкость',
    'громкост': 'громкость',
    'грамкость': 'громкость',
    'погромкий': 'погромче',
    'потише': 'тише',
    'потиш': 'тише',
    'атла': 'атлас',
    'атласа': 'атлас',
    'атласc': 'атлас',
}


def _match_token(actual: str, expected: str, *, threshold: float = 0.80) -> bool:
    actual = _COMMON_ASR_WORDS.get(actual, actual)
    expected = _COMMON_ASR_WORDS.get(expected, expected)
    if actual == expected:
        return True
    if len(expected) < 4 or len(actual) < 4:
        return False
    if actual[0] != expected[0] or abs(len(actual) - len(expected)) > 2:
        return False
    return difflib.SequenceMatcher(None, actual, expected).ratio() >= threshold


def _phrase_matches(text: str, choice: str) -> bool:
    phrase_words = normalize(text).split()
    choice_words = normalize(choice).split()
    if not phrase_words or not choice_words:
        return False
    if len(choice_words) > len(phrase_words):
        return False
    for start in range(len(phrase_words) - len(choice_words) + 1):
        window = phrase_words[start:start + len(choice_words)]
        if all(_match_token(actual, expected) for actual, expected in zip(window, choice_words)):
            return True
    return False


def _wake_tail(text: str, wake_phrases: list[str], *, strict: bool = False) -> tuple[bool, str]:
    """Recognize a slightly distorted wake word without broad activation.

    Only the first two words may contain the wake word, and the fuzzy match
    is restricted to one or more configured wake words. This tolerates the
    common Vosk results «атла»/«атласа», but does not turn arbitrary speech
    into a command.
    """
    words = normalize(text).split()
    if strict:
        for wake in wake_phrases:
            wake_words = normalize(wake).split()
            if wake_words and words[:len(wake_words)] == wake_words:
                return True, ' '.join(words[len(wake_words):])
        return False, normalize(text)
    for index in range(min(2, len(words))):
        for wake in wake_phrases:
            wake_words = normalize(wake).split()
            if index + len(wake_words) > len(words):
                continue
            if all(_match_token(actual, expected, threshold=0.84)
                   for actual, expected in zip(words[index:index + len(wake_words)], wake_words)):
                return True, ' '.join(words[index + len(wake_words):]).strip()
    return False, normalize(text)


def _activation_tail(phrase: str, raw_phrase: str, wake_phrases: list[str], push_to_talk: bool,
                     *, strict: bool = False) -> tuple[bool, str]:
    """Resolve activation while allowing Whisper to omit the wake word."""
    if push_to_talk:
        return bool(phrase or raw_phrase), phrase or raw_phrase
    for candidate in (phrase, raw_phrase):
        matched, tail = _wake_tail(candidate, wake_phrases, strict=strict)
        if matched:
            # If the low-confidence Vosk phrase supplied the wake word but the
            # second ASR supplied only the command, keep the second transcript.
            if candidate == raw_phrase and phrase and not _wake_tail(phrase, wake_phrases, strict=strict)[0]:
                return True, phrase
            return True, tail
    return False, ''


def _follow_up_seconds(config: dict) -> int:
    # Old configs may contain a duration; it never enables continuation itself.
    return int(config.get('follow_up_seconds', 0)) if config.get('activation_mode') == 'wake_word_followup' else 0


def _classify_intent(text: str, config: dict, confidence: float = 1.0) -> dict:
    """Return an explicit local-vs-Atlas routing decision."""
    mode = _voice_mode_command(text)
    if mode:
        return {'route': 'mode', 'intent': 'change_mode', 'target': mode, 'risk': 'low', 'needs_confirmation': False, 'confidence': confidence, 'mode': mode, 'actions': None}
    candidates = _action_candidates(text, config)
    if len(candidates) > 1:
        return {'route': 'clarify', 'intent': 'clarify', 'target': None, 'risk': 'medium', 'needs_confirmation': False, 'confidence': 0.0, 'mode': None, 'actions': None,
                'choices': [name for name, _ in candidates], 'options': [{'name': name, 'actions': actions} for name, actions in candidates]}
    if candidates:
        from .capabilities import local_policy
        actions = candidates[0][1]
        try: policy = local_policy(actions, config)
        except PermissionError:
            return {'route': 'denied', 'intent': 'denied', 'target': None, 'risk': 'high', 'needs_confirmation': False, 'confidence': confidence, 'mode': None, 'actions': None}
        first = actions[0]
        return {'route': 'local', 'intent': first['type'] if len(actions) == 1 else 'scenario',
                'target': first.get('site', first.get('app')), 'confidence': confidence, **policy, 'mode': None, 'actions': actions}
    return {'route': 'atlas', 'intent': 'atlas_request', 'target': None, 'risk': 'low', 'needs_confirmation': False, 'confidence': min(0.6, confidence), 'mode': None, 'actions': None}


def _conflicting_hypotheses(payload: str, config: dict, wake_phrases: list[str],
                            addressed: bool) -> bool:
    intents = set()
    for phrase, _ in _recognized_candidates(payload, float(config.get('min_confidence', 0.55))):
        matched, tail = _wake_tail(phrase, wake_phrases, strict=config.get('activation_mode', 'wake_word') != 'wake_word_followup')
        if not matched and not addressed:
            continue
        decision = _classify_intent(tail if matched else phrase, config)
        if decision['route'] == 'local':
            intents.add(json.dumps(decision['actions'], sort_keys=True, ensure_ascii=False))
        elif decision['route'] == 'mode':
            intents.add('mode:' + decision['mode'])
    return len(intents) > 1


_SMALL = {
    'ноль': 0, 'нуль': 0, 'один': 1, 'одна': 1, 'одно': 1,
    'два': 2, 'две': 2, 'три': 3, 'четыре': 4, 'пять': 5,
    'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9,
    'десять': 10, 'одиннадцать': 11, 'двенадцать': 12,
    'тринадцать': 13, 'четырнадцать': 14, 'пятнадцать': 15,
    'шестнадцать': 16, 'семнадцать': 17, 'восемнадцать': 18,
    'девятнадцать': 19,
}
_TENS = {'двадцать': 20, 'тридцать': 30, 'сорок': 40, 'пятьдесят': 50,
         'шестьдесят': 60, 'семьдесят': 70, 'восемьдесят': 80, 'девяносто': 90}


def _spoken_integer(text: str) -> int | None:
    words = text.split()
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    if words == ['сто']:
        return 100
    if len(words) == 1:
        return _SMALL.get(words[0], _TENS.get(words[0]))
    if len(words) == 2 and words[0] in _TENS and words[1] in _SMALL:
        return _TENS[words[0]] + _SMALL[words[1]]
    return None


def _volume_number(text: str) -> int | None:
    words = normalize(text).split()
    for end in range(len(words), 0, -1):
        for start in range(max(0, end - 2), end):
            value = _spoken_integer(' '.join(words[start:end]))
            if value is not None and 0 <= value <= 100:
                return value
    return None


def _volume_device():
    if os.name != 'nt':
        raise RuntimeError('Управление звуком доступно только в Windows')
    from pycaw.pycaw import AudioUtilities
    device = AudioUtilities.GetSpeakers()
    return device.EndpointVolume


def _media_key(key: int) -> None:
    import ctypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, 2, 0)


def _allowed_url(site: str) -> str:
    parsed = urlsplit(site)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Сайт должен быть задан точной HTTPS-ссылкой')
    return site


def _run_action(action: dict, config: dict) -> str:
    runtime_path = config.get('_runtime_config_path')
    if runtime_path:
        config = {**effective_config(Path(runtime_path)), '_runtime_config_path': runtime_path}
        if not config.get('enabled', True): raise PermissionError('Микрофон выключен')
    from .capabilities import local_policy
    local_policy([action], config)
    kind = action.get('type')
    if kind == 'show_help':
        return ('Команды: громче или тише; задай громкость от 0 до 100; выключи или включи звук; '
                'пауза или продолжи; следующий или предыдущий трек; открой разрешённый сайт или приложение; '
                'скажи который час или какая сегодня дата.')
    if kind == 'time_now':
        from datetime import datetime
        return 'Сейчас ' + datetime.now().astimezone().strftime('%H:%M')
    if kind == 'date_today':
        from datetime import datetime
        now = datetime.now().astimezone()
        weekdays = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
        months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
                  'сентября', 'октября', 'ноября', 'декабря')
        return f'Сегодня {weekdays[now.weekday()]}, {now.day} {months[now.month - 1]} {now.year} года'
    if kind == 'open_voice_settings':
        pythonw = Path(sys.prefix) / 'Scripts' / 'pythonw.exe'
        if not pythonw.is_file():
            raise RuntimeError('Не найден запускатель настроек Atlas Voice')
        import subprocess
        selected_config = str(config.get('_runtime_config_path') or config_path())
        subprocess.Popen([str(pythonw), '-m', 'atlas.local_voice', '--voice-settings', '--config', selected_config],
                         cwd=str(Path(__file__).resolve().parents[2]),
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000), close_fds=True)
        return 'Открываю настройки голоса'
    if kind == 'volume_set':
        value = action.get('percent')
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError('Громкость должна быть целым числом от 0 до 100')
        volume = _volume_device()
        volume.SetMasterVolumeLevelScalar(value / 100, None)
        if abs(volume.GetMasterVolumeLevelScalar() * 100 - value) > 1: raise RuntimeError('Громкость не подтверждена')
        return f'Громкость {value} процентов'
    if kind == 'volume_delta':
        delta = action.get('percent')
        if type(delta) is not int or not -30 <= delta <= 30:
            raise ValueError('Изменение громкости вне разрешённого диапазона')
        volume = _volume_device()
        value = max(0, min(100, round(volume.GetMasterVolumeLevelScalar() * 100) + delta))
        volume.SetMasterVolumeLevelScalar(value / 100, None)
        if abs(volume.GetMasterVolumeLevelScalar() * 100 - value) > 1: raise RuntimeError('Громкость не подтверждена')
        return f'Громкость {value} процентов'
    if kind == 'mute_set':
        muted = action.get('muted')
        if type(muted) is not bool:
            raise ValueError('Для mute_set требуется логическое значение')
        volume = _volume_device()
        volume.SetMute(muted, None)
        if bool(volume.GetMute()) != muted: raise RuntimeError('Mute не подтверждён')
        return 'Звук выключен' if muted else 'Звук включён'
    if kind == 'media':
        keys = {'play_pause': 0xB3, 'next': 0xB0, 'previous': 0xB1}
        command = action.get('command')
        if not isinstance(command, str):
            raise ValueError('Для media требуется команда')
        key = keys.get(command)
        if key is None:
            raise ValueError('Неизвестная медиакоманда')
        _media_key(key)
        return {'play_pause': 'Пауза или воспроизведение переключены', 'next': 'Переключил трек', 'previous': 'Вернулся к предыдущему треку'}[command]
    if kind == 'open_site':
        site_id = action.get('site')
        if not isinstance(site_id, str):
            raise ValueError('Для open_site требуется идентификатор сайта')
        site = (config.get('sites') or {}).get(site_id)
        if not isinstance(site, str):
            raise PermissionError('Сайт не добавлен в локальный список разрешённых')
        if not webbrowser.open(_allowed_url(site), new=2): raise RuntimeError('Браузер не принял запрос')
        return f'Браузер принял открытие {site_id}; загрузка страницы не проверена'
    if kind == 'launch_app':
        app_id = action.get('app')
        if not isinstance(app_id, str):
            raise ValueError('Для launch_app требуется идентификатор приложения')
        application = (config.get('applications') or {}).get(app_id)
        if not isinstance(application, dict):
            raise PermissionError('Приложение не добавлено в локальный список разрешённых')
        executable = Path(application.get('executable', ''))
        if os.name != 'nt' or not executable.is_absolute() or executable.suffix.lower() != '.exe' or not executable.is_file() or executable.is_symlink():
            raise PermissionError('Путь приложения не прошёл локальную проверку')
        if executable.name.lower() in {'cmd.exe', 'powershell.exe', 'pwsh.exe', 'wscript.exe', 'cscript.exe', 'mshta.exe', 'rundll32.exe', 'regsvr32.exe'}:
            raise PermissionError('Командные оболочки запускать нельзя')
        import subprocess
        subprocess.Popen([str(executable)], shell=False, close_fds=True)
        return f'Открываю {app_id}'
    raise ValueError('Тип действия не разрешён')


def _has_phrase(text: str, choices: tuple[str, ...]) -> bool:
    padded = f' {normalize(text)} '
    return any(f' {normalize(choice)} ' in padded or _phrase_matches(text, choice) for choice in choices)


def _relative_volume_step(phrase: str, default: int) -> int:
    words = phrase.split()
    if 'на' in words:
        start = len(words) - 1 - words[::-1].index('на') + 1
        amount = _volume_number(' '.join(words[start:]))
        if amount is not None and 1 <= amount <= 30:
            return amount
    return default


def _parse_builtin(text: str) -> list[dict] | None:
    phrase = normalize(text)
    if _has_phrase(phrase, ('помощь', 'что ты умеешь', 'какие команды', 'список команд')):
        return [{'type': 'show_help'}]
    if _has_phrase(phrase, ('который час', 'который сейчас час', 'сколько времени', 'назови время', 'время сейчас')):
        return [{'type': 'time_now'}]
    if _has_phrase(phrase, ('какая сегодня дата', 'какое сегодня число', 'какой сегодня день', 'назови дату', 'сегодняшняя дата')):
        return [{'type': 'date_today'}]
    if _has_phrase(phrase, ('открой настройки голоса', 'настройки голосового управления', 'настрой голос atlas')):
        return [{'type': 'open_voice_settings'}]

    if _has_phrase(phrase, (
        'поставь на паузу', 'на паузу', 'пауза', 'приостанови музыку', 'приостанови видео',
        'продолжи музыку', 'продолжи видео', 'возобнови музыку', 'возобнови видео',
        'продолжи воспроизведение', 'возобнови воспроизведение', 'включи воспроизведение',
        'начни воспроизведение', 'старт воспроизведения', 'запусти музыку', 'играй музыку',
        'воспроизводи музыку', 'play pause',
    )) or phrase in {'продолжи', 'возобнови', 'включи музыку', 'играй', 'пауза'}:
        return [{'type': 'media', 'command': 'play_pause'}]
    if _has_phrase(phrase, (
        'следующий трек', 'следующая песня', 'следующая композиция', 'следующее видео',
        'переключи трек', 'переключи песню', 'включи следующий трек', 'дальше трек',
    )) or phrase in {'дальше', 'следующий', 'следующая', 'следующее'}:
        return [{'type': 'media', 'command': 'next'}]
    if _has_phrase(phrase, (
        'предыдущий трек', 'предыдущая песня', 'предыдущая композиция', 'предыдущее видео',
        'верни предыдущий трек', 'переключи на предыдущий', 'назад трек',
    )) or phrase in {'назад', 'предыдущий', 'предыдущая', 'предыдущее'}:
        return [{'type': 'media', 'command': 'previous'}]

    increase_phrases = (
        'погромче', 'громче', 'прибавь громкость', 'прибавь звук', 'добавь громкости', 'добавь звук',
        'увеличь громкость', 'увеличь звук', 'подними громкость', 'сделай громче', 'звук выше',
        'громкость выше',
    )
    decrease_phrases = (
        'потише', 'тише', 'сделай потише', 'уменьши громкость', 'уменьши звук',
        'сбавь громкость', 'убавь звук', 'убавь громкость', 'понизь громкость', 'звук тише', 'громкость ниже',
    )
    if _has_phrase(phrase, increase_phrases):
        return [{'type': 'volume_delta', 'percent': _relative_volume_step(phrase, 10)}]
    if _has_phrase(phrase, decrease_phrases):
        return [{'type': 'volume_delta', 'percent': -_relative_volume_step(phrase, 10)}]

    if _has_phrase(phrase, (
        'выключи звук', 'отключи звук', 'убери звук', 'без звука', 'замьюти', 'замути звук',
        'выключи аудио', 'отключи аудио', 'заглуши звук', 'режим без звука',
    )):
        return [{'type': 'mute_set', 'muted': True}]
    if _has_phrase(phrase, (
        'включи звук', 'верни звук', 'восстанови звук', 'сними мут', 'размьюти', 'размути звук',
        'включи аудио', 'восстанови аудио', 'убери беззвучный режим', 'сними беззвучный режим',
    )):
        return [{'type': 'mute_set', 'muted': False}]

    if _has_phrase(phrase, ('громкость', 'уровень звука', 'звук')):
        number = _volume_number(phrase)
        if number is not None and 0 <= number <= 100:
            return [{'type': 'volume_set', 'percent': number}]
    return None


def _strip_politeness(phrase: str) -> str:
    words = normalize(phrase).split()
    while words and words[0] in {'пожалуйста', 'можешь', 'можно', 'будь', 'добр'}:
        words.pop(0)
    while words and words[-1] in {'пожалуйста', 'спасибо', 'если', 'можно'}:
        words.pop()
    return ' '.join(words)


def _target_matches(command: str, target: str, prefixes: tuple[str, ...], aliases: list[str] | None = None) -> bool:
    command = _strip_politeness(command)
    targets = {normalize(target)}
    for alias in aliases or []:
        if not isinstance(alias, str):
            continue
        normalized_alias = normalize(alias)
        if command == normalized_alias or _phrase_matches(command, normalized_alias):
            return True
        for prefix in sorted(prefixes, key=len, reverse=True):
            if normalized_alias.startswith(prefix):
                targets.add(normalized_alias[len(prefix):])
                break
    return any(command == prefix + candidate or _phrase_matches(command, prefix + candidate)
               for candidate in targets for prefix in prefixes)


def _select_actions(text: str, config: dict) -> list[dict] | None:
    candidates = _action_candidates(text, config)
    return candidates[0][1] if len(candidates) == 1 else None


def _action_candidates(text: str, config: dict) -> list[tuple[str, list[dict]]]:
    command = normalize(text)
    if any(command.startswith(prefix) for prefix in
           ('не ', 'не надо ', 'не нужно ', 'расскажи ', 'объясни ', 'как ', 'почему ', 'что будет ')):
        return []
    builtin = _parse_builtin(text)
    candidates = [('Команда', builtin)] if builtin is not None else []
    for scenario in config.get('scenarios', []):
        if not isinstance(scenario, dict) or scenario.get('enabled') is False:
            continue
        triggers = [normalize(x) for x in scenario.get('phrases', []) if isinstance(x, str)]
        scenario_prefixes = ('начни ', 'запусти ', 'включи ', 'активируй ', 'перейди в ', 'перейди к ',
                             'подготовь ', 'пора ', 'приступи к ')
        scenario_name = scenario.get('name') if isinstance(scenario, dict) else ''
        if (_strip_politeness(command) in triggers or
                (isinstance(scenario_name, str) and _target_matches(command, scenario_name, scenario_prefixes))):
            actions = scenario.get('actions')
            if isinstance(actions, list) and actions and len(actions) <= 12 and all(isinstance(x, dict) for x in actions):
                candidates.append(('Сценарий ' + str(scenario_name), actions))
    prefixes: tuple[str, ...]
    for app_id, app in (config.get('applications') or {}).items():
        prefixes = ('открой ', 'открой приложение ', 'открой мне ', 'запусти ', 'запусти приложение ',
                    'включи ', 'включить ', 'покажи ', 'показать ', 'открыть ', 'открыть приложение ',
                    'запустить ', 'запустить приложение ',
                    'хочу открыть ', 'хочу запустить ',
                    'мне нужен ', 'мне нужна ')
        aliases = app.get('phrases', []) if isinstance(app, dict) else []
        if _target_matches(command, app_id, prefixes, aliases):
            candidates.append(('Приложение ' + app_id, [{'type': 'launch_app', 'app': app_id}]))
    for site_id, site in (config.get('sites') or {}).items():
        prefixes = ('открой ', 'открой сайт ', 'открой страницу ', 'открой вкладку ', 'открой мне ',
                    'открыть ', 'открыть сайт ', 'запусти ', 'запусти сайт ', 'покажи ', 'показать ',
                    'перейди на ', 'перейди в ', 'перейди к ', 'перейти на ', 'перейти в ', 'перейти к ',
                    'зайди на ', 'зайди в ', 'зайти на ', 'зайти в ', 'посети сайт ', 'посети ', 'посетить ', 'хочу открыть ',
                    'открой браузер и сайт ')
        aliases = config.get('site_phrases', {}).get(site_id, [])
        if _target_matches(command, site_id, prefixes, aliases):
            candidates.append(('Сайт ' + site_id, [{'type': 'open_site', 'site': site_id}]))
    unique: list[tuple[str, list[dict]]] = []
    for candidate in candidates:
        if not any(candidate[1] == item[1] for item in unique): unique.append(candidate)
    return unique


def _atlas_client(config: dict):
    """Open the existing outbound mTLS Companion identity for the voice bridge."""
    companion_path = os.path.expandvars(str(config.get('companion_config', '')))
    if not companion_path:
        raise RuntimeError('В voice.json не указан companion_config')
    path = Path(companion_path)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RuntimeError('Локальная конфигурация Companion не найдена')
    companion = json.loads(path.read_text(encoding='utf-8-sig'))
    server = companion.get('server')
    parsed = urlsplit(server or '')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError('В companion.json не задан безопасный HTTPS адрес')
    ca, cert, key = (Path(companion.get(name, '')) for name in ('ca', 'cert', 'key'))
    if any(not p.is_absolute() or not p.is_file() or p.is_symlink() for p in (ca, cert, key)):
        raise RuntimeError('Файлы mTLS Companion не найдены')
    import httpx
    import ssl
    # HTTPX 0.28 возвращает context для verify=<str> до обработки cert=...
    # Явный SSLContext сохраняет проверку сервера и действительно отправляет identity.
    context = ssl.create_default_context(cafile=str(ca))
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return httpx.Client(base_url=server, verify=context,
                        timeout=10, follow_redirects=False, trust_env=False)


def _send_to_atlas(text: str, config: dict, mode='chat', speaker=None, on_status=None,
                   cancel_event=None, register_client=None) -> str:
    """Submit one transcript and consume the authenticated Atlas event stream.

    Older gateways are still supported through the legacy result polling
    endpoint. Checked fragments are queued for speech before the final answer;
    fallback reads the same task and never repeats its already queued prefix.
    The stream never carries audio or credentials.
    """
    if config.get('network_mode') == 'local_only': raise PermissionError('Только локальные команды')
    if on_status is not None:
        on_status('Отправляю запрос в Atlas', False)
    from .voice_reply import ReplyInterrupted
    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise ReplyInterrupted()
    deadline = time.monotonic() + max(5, min(300, int(config.get('reply_timeout_seconds', 90))))
    streamed = ''
    seen = set()
    with _atlas_client(config) as client:
        if register_client is not None:
            register_client(client)
        check_cancel()
        if config.get('network_mode') == 'local_only': raise PermissionError('Только локальные команды')
        response = client.post('/voice', json={'request_id': str(uuid.uuid4()), 'text': text, 'mode': mode,
            'store_transcripts': config.get('store_transcripts', True), 'context': config.get('_voice_context', [])})
        response.raise_for_status()
        task = response.json()
        task_id = task.get('task_id')
        if not isinstance(task_id, str):
            raise RuntimeError('Atlas не вернул идентификатор задачи')
        print(f'Передал в Atlas · задача {task_id}', flush=True)
        task_callback = config.get('_voice_task_callback')
        if callable(task_callback): task_callback(task_id)
        def finish(state):
            check_cancel()
            status = state.get('status')
            if status == 'COMPLETED' and isinstance(state.get('answer'), str):
                if private_transcript(state['answer']):
                    raise ValueError('Небезопасный ответ Atlas')
                answer = state['answer'].strip()
                print('Atlas: ответ получен.', flush=True)
                if speaker is not None:
                    if streamed and not state['answer'].startswith(streamed):
                        raise ReplyInterrupted()  # Another worker attempt; never replay mixed speech.
                    remaining = state['answer'][len(streamed):] if streamed else answer
                    if remaining.strip():
                        speaker(remaining)
                if on_status is not None:
                    on_status(answer or 'Atlas ответил', True, 6)
                if config.get('store_transcripts') is False:
                    try: client.post('/voice-consumed', json={'task_id': task_id}).raise_for_status()
                    except httpx.HTTPError: pass  # Bounded server cleanup, never resubmit.
                return answer
            if status == 'FAILED':
                if streamed:
                    raise RuntimeError('Потоковый ответ не завершён')
                message = 'Atlas не смог завершить ответ. Подробности доступны в истории задач.'
                print(message, flush=True)
                if on_status is not None:
                    on_status('Требуется действие в Atlas', True, 10)
                return message
            if status == 'CANCELLED':
                if on_status is not None:
                    on_status('Запрос отменён в Atlas', True, 6)
                return 'Запрос отменён в Atlas.'
            if status in {'WAITING_APPROVAL', 'WAITING_DEVICE', 'PAUSED'}:
                message = 'Задача ожидает продолжения в Atlas; проверьте раздел Подтверждения или Активность.'
                print(message, flush=True)
                if on_status is not None:
                    on_status('Задача ожидает подтверждения или продолжения в Atlas', True, 10)
                return None if config.get('voice_sse_enabled') else message
            return None

        stream_supported = True
        try:
            with client.stream('GET', ('/voice-sse/' if config.get('voice_sse_enabled') else '/voice-events/') + task_id, timeout=10) as response:
                if response.status_code in {404, 405}:
                    stream_supported = False
                else:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        check_cancel()
                        if time.monotonic() >= deadline:
                            raise httpx.ReadTimeout('Время ответа истекло')
                        if not line:
                            continue
                        if config.get('voice_sse_enabled'):
                            if not line.startswith('data: '): continue
                            line = line[6:]
                        if len(line) > 100000:
                            raise ValueError('Слишком большое событие Atlas')
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('Некорректное событие Atlas')
                        if event.get('type') in {'denied', 'reset', 'server_error'}:
                            raise RuntimeError('Поток Atlas остановлен; проверьте историю запроса')
                        if event.get('type') == 'fragment':
                            identifier, fragment = event.get('id'), event.get('text')
                            if not isinstance(identifier, str) or not isinstance(fragment, str):
                                raise ValueError('Некорректный фрагмент Atlas')
                            if identifier not in seen:
                                if not streamed and config.get('_runtime_config_path') and config.get('_phrase_end_at'):
                                    from .voice_quality import runtime_metric
                                    runtime_metric(Path(config['_runtime_config_path']), 'atlas_first_fragment', time.monotonic() - config['_phrase_end_at'])
                                seen.add(identifier)
                                streamed += fragment
                                if len(streamed) > 50000 or private_transcript(streamed):
                                    raise ValueError('Небезопасный ответ Atlas')
                                if speaker is not None:
                                    speaker(fragment)
                                if on_status is not None:
                                    control = 'Esc или «Атлас, стоп»' if config.get('voice_interrupt_enabled', False) else 'Esc или клавиша PTT'
                                    on_status('Atlas отвечает… · ' + control, False)
                            continue
                        if event.get('type') == 'heartbeat':
                            continue
                        if on_status is not None and isinstance(event.get('message'), str):
                            on_status(event['message'], False)
                        result = finish(event)
                        if result is not None:
                            return result
                    stream_supported = False  # EOF without a terminal answer also needs fallback.
        except (httpx.TransportError, json.JSONDecodeError) as exc:
            check_cancel()
            if not stream_supported:
                raise
            print('Поток Atlas прерван, возвращаюсь к проверке результата: ' + type(exc).__name__, flush=True)
            stream_supported = False

        if not stream_supported:
            if on_status is not None:
                on_status('Atlas выполняет задачу', False)
            while time.monotonic() < deadline:
                if cancel_event is not None:
                    if cancel_event.wait(0.3):
                        raise ReplyInterrupted()
                else:
                    time.sleep(1.5)
                check_cancel()
                result = client.get('/voice-result/' + task_id)
                result.raise_for_status()
                answer = finish(result.json())
                if answer is not None:
                    return answer

        if streamed:
            raise RuntimeError('Частичный ответ; полная версия доступна в Atlas')
        message = 'Задача осталась в Atlas; ответ можно посмотреть в выбранном диалоге.'
        print(message, flush=True)
        if on_status is not None:
            on_status('Задача продолжается в Atlas', True, 8)
        return message


def _speech_chunks(text: str, limit: int = 260) -> list[str]:
    """Split a response into pronounceable chunks for responsive playback."""
    chunks: list[str] = []
    for sentence in re.split(r'(?<=[.!?。！？])\s+', text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > limit:
            cut = sentence.rfind(' ', 0, limit)
            cut = cut if cut >= 80 else limit
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            chunks.append(sentence)
    return chunks or ([text.strip()] if text.strip() else [])


def _make_speaker(config: dict, speaking=None):
    if config.get('speak_responses') is not True:
        return None
    try:
        import sounddevice as sd
        import numpy as np
        from piper import PiperVoice, SynthesisConfig
        model_path = _resolve_tts_model(config.get('tts_model_path'))
        if model_path is None:
            raise RuntimeError('Файл локального мужского голоса Piper не найден')
        voice = PiperVoice.load(str(model_path))
        synthesis = SynthesisConfig(length_scale=max(0.75, min(1.4, float(config.get('tts_length_scale', 0.94)))))
        def speak(text, cancel=None):
            from .voice_privacy import sensitive_response
            if private_transcript(text) or (not config.get('speak_sensitive', False) and sensitive_response(text)):
                print('Чувствительный ответ не озвучен. Проверьте защищённый интерфейс Atlas.', flush=True)
                return
            from .voice_speech import SpeechText
            text = ' '.join(SpeechText().feed(text, final=True))
            if not text: return
            def interrupted():
                if _ptt_pressed('ESC'):
                    if cancel is not None:
                        cancel.set()  # A short keypress must also stop later queued chunks.
                    return True
                return cancel is not None and cancel.is_set()
            if speaking is not None: speaking.set()
            try:
                for sentence in _speech_chunks(text[:3500]):
                    if interrupted():
                        break
                    chunks = list(voice.synthesize(sentence, syn_config=synthesis))
                    if interrupted():
                        break
                    if chunks:
                        audio_data = np.concatenate([np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16) for chunk in chunks])
                        sd.play(audio_data, samplerate=chunks[0].sample_rate, blocking=False)
                        if callable(config.get('_tts_first_audio')): config['_tts_first_audio']()
                    else:
                        continue
                    while True:
                        try:
                            if interrupted():
                                sd.stop()
                                if cancel is not None and hasattr(cancel, 'requested_at') and config.get('_runtime_config_path'):
                                    from .voice_quality import runtime_metric
                                    runtime_metric(Path(config['_runtime_config_path']), 'interrupt', time.monotonic() - cancel.requested_at)
                                break
                            if not sd.get_stream().active:
                                break
                        except Exception:
                            break
                        time.sleep(0.05)
                    if interrupted():
                        break
            except Exception:
                print('Не удалось воспроизвести локальный мужской голос Piper.', flush=True)
            finally:
                if speaking is not None: speaking.clear()
        return speak
    except Exception:
        print('Локальный голос Piper недоступен; проверьте TTS-модель и зависимости.', flush=True)
    return None


def _cancel_voice_task(config, task_id):
    if not task_id: raise ValueError('Нет текущей голосовой задачи')
    with _atlas_client(config) as client:
        response = client.post('/voice-cancel', json={'task_id': task_id})
        response.raise_for_status()
        return response.json()['status']


def _voice_mode_command(text: str) -> str | None:
    phrase = normalize(text)
    if phrase in {'режим задач', 'перейди в режим задач', 'включи режим задач', 'режим агента'}:
        return 'task'
    if phrase in {'режим разговора', 'режим чата', 'перейди в режим разговора', 'включи режим разговора', 'обычный режим'}:
        return 'chat'
    return None


def _drain_audio(audio) -> None:
    """Discard buffered microphone audio after local TTS to reduce self-triggering."""
    while True:
        try:
            audio.get_nowait()
        except queue.Empty:
            return


def _config_path(path: str | None) -> Path:
    return config_path(path)


def _load_config(path: Path, allow_disabled: bool = False) -> dict:
    config = effective_config(path)
    if (not isinstance(config, dict) or type(config.get('enabled')) is not bool
            or (config.get('enabled') is not True and not allow_disabled)):
        raise ValueError('Голосовой режим выключен в локальной конфигурации')
    model = Path(config.get('model_path', ''))
    if not model.is_absolute() or not model.is_dir() or model.is_symlink():
        raise ValueError('Локальная модель Vosk не найдена или путь некорректен')
    wake_words = config.get('wake_words', ['атлас'])
    if not isinstance(wake_words, list) or not wake_words or not all(isinstance(x, str) and normalize(x) for x in wake_words):
        raise ValueError('Некорректный локальный список слов активации')
    if config.get('default_mode', 'chat') not in {'chat', 'task'}:
        raise ValueError('Некорректный начальный режим голосового помощника')
    if config.get('activation_mode', 'wake_word') not in {'wake_word', 'wake_word_followup', 'push_to_talk'}:
        raise ValueError('Некорректный режим активации')
    if config.get('free_speech_enabled', True) not in {True, False}:
        raise ValueError('Некорректный режим улучшенного распознавания')
    return config


def listen(config_path: str | None = None, stop_event=None, pause_event=None, status_callback=None) -> None:
    if os.name != 'nt':
        raise RuntimeError('Локальный голосовой режим пока рассчитан только на Windows')
    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model
    except ImportError as exc:
        raise RuntimeError('Установите необязательные зависимости: pip install -r requirements-voice.lock') from exc
    path = _config_path(config_path)
    config = _load_config(path, allow_disabled=True)
    config['_runtime_config_path'] = str(path)
    speech_end: list[float | None] = [None]
    first_audio_recorded = [False]
    def first_audio():
        if speech_end[0] is not None and not first_audio_recorded[0]:
            from .voice_quality import runtime_metric
            runtime_metric(path, 'tts_first_audio', time.monotonic() - speech_end[0])
            first_audio_recorded[0] = True
    config['_tts_first_audio'] = first_audio
    model = Model(str(Path(config['model_path'])))
    sample_rate = int(config.get('sample_rate', 16000))
    recognizer = KaldiRecognizer(model, sample_rate)
    from .voice_session import VoiceSession, command_grammar
    from .voice_center_link import VoiceCenterLink
    session_context = VoiceSession()
    center = VoiceCenterLink(path, config)
    last_task = [None]
    command_recognizer = None
    command_payload = ''
    def prepare_commands():
        if not callable(getattr(recognizer, 'FinalResult', None)): return None
        try:
            result = KaldiRecognizer(model, sample_rate, json.dumps(command_grammar(config), ensure_ascii=False))
            result.SetWords(True)
            return result
        except Exception:
            print('Малый словарь команд недоступен; используется общий Vosk и проверка намерений.', flush=True)
            return None
    command_recognizer = prepare_commands()
    set_words = getattr(recognizer, 'SetWords', None)
    if callable(set_words):
        set_words(True)
    set_alternatives = getattr(recognizer, 'SetMaxAlternatives', None)
    if callable(set_alternatives):
        set_alternatives(3)
    audio: queue.Queue = queue.Queue(maxsize=32)
    speaking = threading.Event()
    from .voice_reply import ReplyController, is_voice_interrupt, is_voice_cancel, interrupt_grammar
    replies = ReplyController()
    reply_status: queue.Queue = queue.Queue(maxsize=32)
    control_held = False
    speaker = _make_speaker(config, speaking)
    _warm_second_stage(config)
    pill = None
    if config.get('show_command_pill', True):
        from .command_pill import CommandPill
        pill = CommandPill(config.get('command_pill_seconds', 5))
    stream_ref = [None]
    stream_paused = False

    def stop_microphone():
        nonlocal stream_paused
        if stream_ref[0] is not None and not stream_paused:
            stream_ref[0].stop()
            stream_paused = True

    def resume_microphone():
        nonlocal stream_paused
        if stream_ref[0] is not None and stream_paused and not (pause_event is not None and pause_event.is_set()) and not (stop_event is not None and stop_event.is_set()):
            stream_ref[0].start()
            stream_paused = False

    dialogue = VoiceDialogue()
    ptt_held_during_phrase = False
    addressed_during_phrase = False
    phrase_audio = PhraseAudio(sample_rate)
    wake_phrases = [normalize(x) for x in config.get('wake_words', ['атлас'])]
    control_recognizer = None
    def prepare_control():
        if not config.get('voice_interrupt_enabled', False):
            return None
        try:
            control = KaldiRecognizer(model, sample_rate, json.dumps(interrupt_grammar(wake_phrases), ensure_ascii=False))
            if callable(getattr(control, 'SetWords', None)):
                control.SetWords(True)
            return control
        except Exception:
            print('Словарь голосовой остановки недоступен. Используйте Esc или клавишу PTT.', flush=True)
            return None
    control_recognizer = prepare_control()
    voice_mode = config.get('default_mode', 'chat')
    config_stamp = path.stat().st_mtime_ns
    if status_callback is not None:
        status_callback('listening')
    print('Atlas Voice запущен. Микрофон обрабатывается локально. Скажите «Атлас» и команду.', flush=True)

    def on_audio(indata, frames, timing, status):
        if status or (speaking.is_set() and (not config.get('voice_interrupt_enabled', False) or replies.current is None)) or (pause_event is not None and pause_event.is_set()):
            return
        try:
            audio.put_nowait((bytes(indata), time.monotonic()))
        except queue.Full:
            try:
                audio.get_nowait()
            except queue.Empty:
                pass

    def reset_recognition():
        nonlocal command_payload
        _drain_audio(audio)
        phrase_audio.clear()
        reset = getattr(recognizer, 'Reset', None)
        if callable(reset):
            reset()
        control_reset = getattr(control_recognizer, 'Reset', None)
        if callable(control_reset):
            control_reset()
        command_reset = getattr(command_recognizer, 'Reset', None)
        if callable(command_reset): command_reset()
        command_payload = ''

    def feedback(command, message, duration=6):
        if pill is not None:
            pill.finish(command, message, duration)
        print(message, flush=True)

    def cancel_server_task(control=None):
        replies.interrupt()
        reset_recognition()
        dialogue.reset()
        task_id = control.get('payload', {}).get('task_id') if control else last_task[0]
        cancel_config = dict(config)
        def cancel():
            try:
                status = _cancel_voice_task(cancel_config, task_id)
                feedback('', 'Статус задачи: ' + status + '. Выполненные действия не откатываются.')
                center.update(event=True, state='completed', route='server', task_id=task_id)
                if control: center.finish(control, True)
            except Exception:
                feedback('', 'Не удалось подтвердить отмену задачи. Проверьте её статус в Atlas.')
                center.update(event=True, state='error')
                if control: center.finish(control, False)
        threading.Thread(target=cancel, daemon=True).start()

    def task_received(task_id):
        last_task[0] = task_id
        center.update(event=True, task_id=task_id, state='processing', route='server')

    from .capabilities import LOCAL
    device_index = int(config.get('device_index', -1))
    microphone_name = str(device_index)
    try:
        microphone_name = str(sd.query_devices(None if device_index < 0 else device_index, 'input')['name'])
    except (AttributeError, KeyError, TypeError, ValueError): pass
    center.update(capabilities=[c.public() for c in LOCAL.values()], scenarios=config.get('scenarios', []),
                  model=Path(str(config.get('free_speech_model_path', ''))).name,
                  microphone=microphone_name)

    from .voice_probe import listener_presence, diagnostic_requested, acknowledge_diagnostic
    level_updated = 0.0
    with replies, center, listener_presence(), sd.RawInputStream(samplerate=sample_rate, blocksize=sample_rate // 20, dtype='int16', channels=1,
                           device=None if device_index < 0 else device_index, callback=on_audio) as stream:
        stream_ref[0] = stream
        while stop_event is None or not stop_event.is_set():
            try:
                current_stamp = path.stat().st_mtime_ns
                if current_stamp != config_stamp:
                    replies.interrupt()
                    refreshed = _load_config(path, allow_disabled=True)
                    refreshed['_runtime_config_path'] = str(path)
                    for field in ('sample_rate', 'device_index', 'model_path'):
                        if refreshed.get(field) != config.get(field):
                            refreshed[field] = config.get(field)
                            print('Для смены микрофона или модели перезапустите голосовой режим.', flush=True)
                    config = refreshed
                    config['_tts_first_audio'] = first_audio
                    voice_mode = config.get('default_mode', 'chat')
                    dialogue.reset()
                    session_context.reset()
                    reset_recognition()
                    wake_phrases = [normalize(x) for x in config.get('wake_words', ['атлас'])]
                    control_recognizer = prepare_control()
                    command_recognizer = prepare_commands()
                    speaker = _make_speaker(config, speaking)
                    _warm_second_stage(config)
                    config_stamp = current_stamp
                    print('Настройки голосового режима применены без перезапуска.', flush=True)
            except (OSError, ValueError) as exc:
                print('Не удалось применить новые настройки голоса: ' + type(exc).__name__, flush=True)
            try:
                control = center.commands.get_nowait()
            except queue.Empty:
                control = None
            if control:
                try:
                    kind = control['kind']
                    if kind == 'cancel': cancel_server_task(control)
                    elif kind in {'stop', 'reject', 'mute', 'unmute'}:
                        replies.interrupt(); reset_recognition(); dialogue.reset()
                        if kind in {'mute', 'unmute'}:
                            from .voice_settings import update_settings
                            update_settings(path, {'enabled': kind == 'unmute'})
                        if kind == 'reject': session_context.undo = None
                    elif kind == 'repeat':
                        session_context.expire(config)
                        answer = next((t['content'] for t in reversed(session_context.turns) if t['role'] == 'assistant'), None)
                        if not answer or speaker is None: raise ValueError('Нет ответа для повторения')
                        def repeat(reply, text=answer): reply.say(text); return text
                        replies.start(speaker, repeat)
                        reset_recognition()
                    elif kind == 'test_scenario':
                        from .voice_settings import validate_scenarios
                        candidate = next(x for x in config.get('scenarios', []) if x['name'] == control['payload']['scenario'])
                        validate_scenarios([candidate], config)
                        from .capabilities import local_policy
                        local_policy(candidate['actions'], config)
                        feedback('', 'Пробный сценарий: ' + ', '.join(a['type'] for a in candidate['actions']) + '. Действия не выполнялись.')
                        center.update(event=True, state='completed', route='local')
                    elif kind == 'hardware':
                        _run_action({'type': 'open_voice_settings'}, config)
                    else: raise ValueError('Неизвестный control')
                    record_history(path, 'control', 'Принят control ' + kind)
                    if kind != 'cancel': center.finish(control, True)
                except Exception:
                    center.finish(control, False)
            diagnostic = diagnostic_requested()
            if diagnostic or config.get('enabled') is not True or (pause_event is not None and pause_event.is_set()):
                replies.interrupt()
                if status_callback is not None:
                    status_callback('paused')
                center.update(state='paused', rms_percent=0, pending=False)
                session_context.reset()
                stop_microphone()
                reset_recognition()
                dialogue.reset()
                ptt_held_during_phrase = False
                addressed_during_phrase = False
                if diagnostic:
                    acknowledge_diagnostic()
                time.sleep(0.08)
                continue
            resume_microphone()
            try:
                while True:
                    source, message, finished, duration = reply_status.get_nowait()
                    if replies.current is source and not source.cancelled.is_set() and pill is not None:
                        if finished: pill.finish('', message, duration)
                        else: pill.update('', message)
            except queue.Empty:
                pass
            if replies.current is not None and replies.current.done.is_set():
                completed = replies.current
                if completed.error:
                    feedback('', 'Ответ прерван. Проверьте историю запроса в Atlas.', 8)
                    record_history(path, 'atlas', 'Ошибка ответа: ' + completed.error)
                elif not completed.cancelled.is_set():
                    record_history(path, 'atlas', 'Ответ получен')
                    if isinstance(completed.result, str): session_context.remember('assistant', completed.result)
                    center.update(event=True, state='completed', route='server', context=list(session_context.turns))
                replies.interrupt()
                reset_recognition()
                dialogue.extend(time.monotonic(), _follow_up_seconds(config))
            held = replies.current is not None and (_ptt_pressed('ESC') or _ptt_pressed(str(config.get('ptt_key', 'F8'))))
            if replies.current is not None and held and not control_held:
                replies.interrupt()
                reset_recognition()
                dialogue.reset()
                feedback('', 'Озвучка и ожидание остановлены. Задача остаётся в Atlas.')
            control_held = held
            if status_callback is not None:
                status_callback('speaking' if speaking.is_set() else 'processing' if replies.current else 'listening')
            center.update(state='speaking' if speaking.is_set() else 'processing' if replies.current else 'waiting_confirmation' if dialogue.pending else 'listening', pending=dialogue.pending is not None)
            try:
                data, captured_at = audio.get(timeout=0.05)
            except queue.Empty:
                continue
            phrase_audio.append(data)
            now = time.monotonic()
            if _audio_level(data) > 0.003: speech_end[0] = captured_at
            if now - level_updated >= 0.5:
                center.update(rms_percent=round(_audio_level(data) * 100, 2))
                level_updated = now
            if command_recognizer is not None and replies.current is None:
                if command_recognizer.AcceptWaveform(data): command_payload = command_recognizer.Result()
            if dialogue.expire(now):
                feedback('', 'Время подтверждения истекло. Повторите команду.')
            if config.get('activation_mode') == 'wake_word_followup' and now < dialogue.active_until and _audio_level(data) > 0.003:
                addressed_during_phrase = True
            if config.get('activation_mode', 'wake_word') == 'push_to_talk' and _ptt_pressed(str(config.get('ptt_key', 'F8'))):
                ptt_held_during_phrase = True
            active_recognizer = control_recognizer if replies.current is not None else recognizer
            if active_recognizer is None:
                phrase_audio.clear()
                continue
            accepted = active_recognizer.AcceptWaveform(data)
            if replies.current is not None and config.get('voice_interrupt_enabled', False):
                partial = getattr(active_recognizer, 'PartialResult', None)
                if not accepted and callable(partial):
                    partial_text = normalize(json.loads(partial()).get('partial', ''))
                    if is_voice_interrupt(partial_text, wake_phrases):
                        if is_voice_cancel(partial_text, wake_phrases):
                            cancel_server_task()
                            continue
                        replies.interrupt()
                        reset_recognition()
                        dialogue.reset()
                        feedback('', 'Озвучка и ожидание остановлены. Задача остаётся в Atlas.')
                        continue
            if not accepted:
                continue
            ptt_mode = config.get('activation_mode', 'wake_word') == 'push_to_talk'
            payload = active_recognizer.Result()
            raw_phrase = _raw_recognized_text(payload)
            phrase = _best_recognized_phrase(
                payload, float(config.get('min_confidence', 0.55)), config,
                wake_phrases, ptt_mode,
            )
            if replies.current is not None:
                if config.get('voice_interrupt_enabled', False) and is_voice_interrupt(phrase, wake_phrases):
                    if is_voice_cancel(phrase, wake_phrases):
                        cancel_server_task()
                        continue
                    replies.interrupt()
                    reset_recognition()
                    dialogue.reset()
                    feedback('', 'Озвучка и ожидание остановлены. Задача остаётся в Atlas.')
                # During an answer nothing from the microphone becomes a command,
                # approval or new request. No acoustic echo cancellation is claimed.
                phrase_audio.clear()
                ptt_held_during_phrase = addressed_during_phrase = False
                continue
            audio_bytes = phrase_audio.take()
            ptt_was_held = ptt_held_during_phrase
            ptt_held_during_phrase = False
            was_addressed = dialogue.accepts_unaddressed(normalize(phrase), config, now, addressed_during_phrase)
            addressed_during_phrase = False
            if ptt_mode and not ptt_was_held:
                dialogue.reset()
                continue
            # Vosk emits a final phrase only after silence. Rejecting it because
            # silence exceeded the VAD timeout made ordinary speech impossible.
            strict_wake = config.get('activation_mode', 'wake_word') != 'wake_word_followup'
            matched_wake, tail = _activation_tail(phrase, '', wake_phrases, ptt_mode, strict=strict_wake)
            if not matched_wake and not ptt_mode and phrase and was_addressed:
                matched_wake, tail = True, phrase
            if not matched_wake:
                if raw_phrase and not phrase and (ptt_mode or was_addressed or _wake_tail(raw_phrase, wake_phrases, strict=strict_wake)[0]):
                    feedback('', 'Не уверен в распознавании. Повторите фразу точнее.', 6)
                continue
            if not tail:
                seconds = _follow_up_seconds(config)
                dialogue.extend(now, seconds)
                feedback('', 'Слушаю команду…' if seconds else 'Скажите обращение и запрос в одной фразе: «Атлас, …».')
                continue
            center.update(event=True, state='recognizing', route='local', transcript=tail)
            if speech_end[0] is None: speech_end[0] = now
            first_audio_recorded[0] = False
            if private_transcript(tail) or private_transcript(raw_phrase):
                dialogue.reset()
                feedback('', 'Фраза содержит приватные данные. Используйте безопасный запрос.')
                continue
            if _conflicting_hypotheses(payload, config, wake_phrases, ptt_mode or was_addressed):
                feedback(tail, 'Распознал несколько разных команд. Повторите точнее.', 8)
                dialogue.extend(now, _follow_up_seconds(config))
                continue
            if status_callback is not None:
                status_callback('processing')
            resolution, confirmed = dialogue.resolve(normalize(tail), config, now)
            if resolution in {'cancel', 'end', 'missing', 'changed'}:
                feedback(tail, {'cancel': 'Ожидающее локальное действие отменено.',
                                'end': 'Разговор завершён. Для обращения скажите «Атлас».',
                                'missing': 'Нет действия для подтверждения. Повторите команду.',
                                'changed': 'Разрешения изменились. Повторите команду.'}[resolution])
                continue
            if normalize(tail) in {'отмени задачу', 'отмени запрос'}:
                cancel_server_task()
                continue
            actions: list[dict] | None
            if confirmed is not None:
                actions = confirmed.actions
                tail = confirmed.command
            else:
                follow = dialogue.resolve_choice(normalize(tail), config, now) or session_context.resolve(normalize(tail), config)
                if isinstance(follow, str):
                    feedback(tail, follow)
                    continue
                if isinstance(follow, list):
                    from .capabilities import local_policy
                    try: decision = {'route': 'local', 'actions': follow, **local_policy(follow, config)}
                    except PermissionError:
                        feedback('', 'Локальная возможность запрещена')
                        continue
                else:
                    confidence = next((score for candidate, score in _recognized_candidates(payload, float(config.get('min_confidence', 0.55))) if candidate == phrase), 0.0)
                    decision = _classify_intent(tail, config, confidence)
                if command_recognizer is not None:
                    restricted = command_payload or command_recognizer.FinalResult()
                    command_payload = ''
                    restricted_phrase = _recognized_text(restricted, float(config.get('min_confidence', 0.55)))
                    matched, restricted_tail = _activation_tail(restricted_phrase, '', wake_phrases, ptt_mode or was_addressed, strict=strict_wake)
                    if matched and decision['route'] == 'local':
                        restricted_decision = _classify_intent(restricted_tail, config)
                        if restricted_decision['route'] == 'local' and restricted_decision['actions'] != decision['actions']:
                            feedback(tail, 'Словарь команд и общая речь расходятся. Повторите точнее.')
                            continue
                actions = decision['actions']
                if decision['route'] == 'atlas' and audio_bytes:
                    whisper_text = _whisper_transcribe(audio_bytes, config)
                    if whisper_text:
                        whisper_matched, whisper_tail = _activation_tail(
                            whisper_text, phrase, wake_phrases, ptt_mode or was_addressed,
                            strict=strict_wake,
                        )
                        if whisper_matched and whisper_tail:
                            tail = whisper_tail
                            if private_transcript(tail):
                                dialogue.reset()
                                feedback('', 'Фраза содержит приватные данные. Используйте безопасный запрос.')
                                continue
                            decision = _classify_intent(tail, config, min(confidence, 0.9))
                            actions = decision['actions']
                if decision['route'] == 'clarify':
                    message = dialogue.ask(decision['options'], config, now)
                    feedback(tail, message, 10)
                    dialogue.extend(time.monotonic(), _follow_up_seconds(config))
                    continue
                if decision['route'] == 'mode':
                    voice_mode = decision['mode']
                    feedback(tail, 'Режим Atlas: ' + ('задачи с общими подтверждениями' if voice_mode == 'task' else 'разговор'))
                    dialogue.extend(time.monotonic(), _follow_up_seconds(config))
                    continue
                if decision['route'] == 'denied':
                    feedback('', 'Эта локальная возможность запрещена или разрешение истекло.')
                    continue
            if pill is not None:
                pill.show(tail)
            if actions is None:
                if config.get('network_mode') == 'local_only':
                    feedback('', 'Только локальные команды. Фраза не отправлена на сервер.')
                    center.update(event=True, state='completed', route='local')
                    continue
                if voice_mode == 'task' and not config.get('store_transcripts', True):
                    feedback('', 'Задачи требуют журнала. Включите сохранение текста или режим разговора.')
                    continue
                try:
                    request_text, request_config, request_mode = tail, dict(config), voice_mode
                    session_context.expire(config)
                    request_config['_voice_context'] = list(session_context.turns)
                    request_config['_voice_task_callback'] = task_received
                    request_config['_phrase_end_at'] = speech_end[0]
                    session_context.remember('user', tail)
                    def receive(reply, text=request_text, cfg=request_config, mode=request_mode):
                        def report_status(status, finished=False, duration=None):
                            if reply.cancelled.is_set():
                                return
                            try: reply_status.put_nowait((reply, status, finished, duration))
                            except queue.Full: pass
                        return _send_to_atlas(text, cfg, mode, reply.say, report_status,
                            reply.cancelled, reply.register_client)
                    replies.start(speaker, receive)
                    control_held = _ptt_pressed('ESC') or _ptt_pressed(str(config.get('ptt_key', 'F8')))
                    record_history(path, 'atlas', 'Передано в Atlas')
                    if status_callback is not None:
                        status_callback('listening')
                except Exception as exc:
                    print('Не удалось передать фразу в Atlas: ' + type(exc).__name__ + '. Проверьте голосовой доступ устройства и соединение.', flush=True)
                    if pill is not None:
                        pill.finish(tail, 'Не удалось передать запрос в Atlas', 7)
                    record_history(path, 'atlas', 'Ошибка передачи')
                finally:
                    reset_recognition()
                    dialogue.reset()
                continue
            try:
                from .capabilities import local_policy
                policy = local_policy(actions, config)
                if config.get('test_mode') is True:
                    message = 'Тестовый режим: команда не выполнена'
                    print(message, flush=True)
                    record_history(path, 'local', message, tail)
                    if pill is not None:
                        pill.finish(tail, message, 5)
                    dialogue.extend(time.monotonic(), _follow_up_seconds(config))
                    continue
                if policy['needs_confirmation'] and confirmed is None:
                    message = dialogue.propose(tail, actions, config, time.monotonic())
                    print(message, flush=True)
                    record_history(path, 'local', 'Ожидает подтверждения', tail)
                    center.update(event=True, state='waiting_confirmation', route='local', pending=True)
                    if pill is not None:
                        pill.finish(tail, message, int(config.get('confirmation_timeout_seconds', 12)))
                    continue
                if pill is not None:
                    pill.update(tail, 'Выполняю на компьютере…')
                messages = []
                undo = None
                if all(a['type'] in {'volume_delta', 'volume_set', 'mute_set'} for a in actions):
                    try:
                        device = _volume_device()
                        undo = [{'type': 'volume_set', 'percent': round(device.GetMasterVolumeLevelScalar() * 100)},
                                {'type': 'mute_set', 'muted': bool(device.GetMute())}]
                    except Exception: pass
                record_history(path, 'local', 'Начато выполнение', tail)
                for index, action in enumerate(actions):
                    messages.append(_run_action(action, config))
                    record_history(path, 'local', f'Шаг {index + 1}/{len(actions)} завершён')
                response = 'Готово: ' + '; '.join(messages)
                print(response, flush=True)
                if pill is not None:
                    seconds = _follow_up_seconds(config)
                    hint = f' · Можно продолжить без «Атлас» ({seconds} с)' if seconds else ''
                    pill.finish(tail, response + hint, seconds or 5)
                record_history(path, 'local', 'Выполнено', tail)
                if speech_end[0] is not None:
                    from .voice_quality import runtime_metric
                    runtime_metric(path, 'local_after_speech', time.monotonic() - speech_end[0])
                session_context.remember('user', tail, actions, undo)
                session_context.remember('assistant', response)
                center.update(event=True, state='completed', route='local', context=list(session_context.turns), pending=False)
                if status_callback is not None:
                    status_callback('speaking' if speaker is not None else 'listening')
                if speaker is not None:
                    def local_reply(reply, text=response):
                        reply.say(text)
                        return text
                    replies.start(speaker, local_reply)
                    reset_recognition()
                dialogue.extend(time.monotonic(), _follow_up_seconds(config))
            except Exception as exc:
                print('Команда не выполнена: ' + type(exc).__name__, flush=True)
                if pill is not None:
                    pill.finish(tail, 'Команда не выполнена. Ошибка: ' + type(exc).__name__, 7)
                record_history(path, 'local', 'Ошибка ' + type(exc).__name__, tail)
                if status_callback is not None:
                    status_callback('error')


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Локальные голосовые команды Atlas для Windows')
    parser.add_argument('--config', help='Путь к локальному voice.json; по умолчанию LocalAppData\\Atlas\\Voice\\voice.json')
    parser.add_argument('--tray', action='store_true', help='Работать фоновым приложением в системном трее Windows')
    parser.add_argument('--desktop-window', action='store_true', help='Открыть полноэкранный интерфейс Atlas в окне Windows')
    parser.add_argument('--model-settings', action='store_true', help='Открыть раздел моделей в окне Atlas')
    parser.add_argument('--voice-settings', action='store_true', help='Открыть локальные настройки голосового режима')
    args = parser.parse_args()
    if args.desktop_window:
        try:
            run_desktop_window(model_settings=args.model_settings)
        except Exception as exc:
            if os.name == 'nt':
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    'Не удалось открыть Atlas в окне Windows. Проверьте WebView2 и подключение к серверу. (' + type(exc).__name__ + ')',
                    'Atlas',
                    0x10,
                )
            raise SystemExit(2)
        return
    if args.voice_settings:
        try:
            open_settings_window(_config_path(args.config))
        except Exception as exc:
            if os.name == 'nt':
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    'Не удалось открыть настройки голоса. (' + type(exc).__name__ + ')',
                    'Atlas',
                    0x10,
                )
            raise SystemExit(2)
        return
    if args.tray:
        run_tray(args.config)
        return
    try:
        listen(args.config)
    except KeyboardInterrupt:
        print('\nAtlas Voice остановлен.', flush=True)
    except Exception as exc:
        print(f'Atlas Voice: {exc}', file=sys.stderr, flush=True)
        raise SystemExit(2)


_VOICE_MUTEX_NAME = 'Local\\AtlasVoiceTrayV2'
_VOICE_RESTART_NAME = 'Local\\AtlasVoiceRestartV2'


def _voice_kernel32():
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def request_voice_restart(config_path: str | None = None) -> str:
    """Restart the active listener, or launch it when no tray process exists."""
    if os.name != 'nt':
        raise RuntimeError('Голосовой режим доступен только в Windows')
    kernel32 = _voice_kernel32()
    event = kernel32.OpenEventW(0x0002, False, _VOICE_RESTART_NAME)
    if event:
        try:
            if not kernel32.SetEvent(event):
                raise OSError('Не удалось передать команду перезапуска')
        finally:
            kernel32.CloseHandle(event)
        return 'Голосовой режим перезапускается'
    import subprocess
    pythonw = Path(sys.prefix) / 'Scripts' / 'pythonw.exe'
    if not pythonw.is_file():
        raise RuntimeError('Не найден локальный запускатель Atlas Voice')
    command = [str(pythonw), '-m', 'atlas.local_voice', '--tray']
    if config_path:
        command.extend(['--config', str(_config_path(config_path))])
    subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[2]),
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000), close_fds=True)
    return 'Голосовой режим запускается'


def run_tray(config_path: str | None = None) -> None:
    """Native Windows notification-area shell around the always-on local listener."""
    if os.name != 'nt':
        raise RuntimeError('Системный трей доступен только в Windows')
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError('Для системного трея установите зависимости voice из lock-файла') from exc

    import ctypes
    kernel32 = _voice_kernel32()
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, _VOICE_MUTEX_NAME)
    if not mutex:
        raise RuntimeError('Не удалось создать блокировку Atlas Voice')
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(mutex)
        # The first process may still be creating its restart event. Never
        # spawn another tray from the duplicate-instance path.
        for _ in range(10):
            event = kernel32.OpenEventW(0x0002, False, _VOICE_RESTART_NAME)
            if event:
                try:
                    kernel32.SetEvent(event)
                finally:
                    kernel32.CloseHandle(event)
                break
            time.sleep(0.1)
        return
    restart_handle = kernel32.CreateEventW(None, False, False, _VOICE_RESTART_NAME)
    if not restart_handle:
        kernel32.CloseHandle(mutex)
        raise RuntimeError('Не удалось создать канал перезапуска Atlas Voice')

    stop_event = threading.Event()
    listener_stop = threading.Event()
    restart_requested = threading.Event()
    pause_event = threading.Event()
    listener_errors: queue.Queue[str] = queue.Queue(maxsize=1)
    listener_states: queue.Queue[str] = queue.Queue(maxsize=4)
    def make_icon(state: str):
        color = {'paused': (118, 118, 118), 'processing': (64, 150, 220),
                 'speaking': (160, 100, 220), 'error': (220, 70, 70)}.get(state, (30, 170, 95))
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((8, 8, 56, 56), fill=color, outline=(245, 245, 245), width=3)
        return image
    def worker():
        def report_state(state):
            try:
                listener_states.put_nowait(state)
            except queue.Full:
                try:
                    listener_states.get_nowait()
                except queue.Empty:
                    pass
                try:
                    listener_states.put_nowait(state)
                except queue.Full:
                    pass
        while not stop_event.is_set():
            listener_stop.clear()
            try:
                listen(config_path, listener_stop, pause_event, report_state)
            except Exception as exc:
                try: listener_errors.put_nowait(type(exc).__name__)
                except queue.Full: pass
                # A corrected microphone/configuration can be retried from
                # the GUI without creating a new tray or console window.
                while not stop_event.is_set() and not restart_requested.wait(0.2):
                    pass
            if not restart_requested.is_set():
                return
            restart_requested.clear()
    def toggle(icon, item):
        if pause_event.is_set(): pause_event.clear()
        else: pause_event.set()
        icon.icon = make_icon('paused' if pause_event.is_set() else 'listening')
        icon.title = 'Atlas — прослушивание приостановлено' if pause_event.is_set() else 'Atlas — слушает локально'

    def quit_app(icon, item):
        stop_event.set()
        listener_stop.set()
        icon.stop()

    def restart_app(icon, item):
        restart_requested.set()
        listener_stop.set()
        icon.title = 'Atlas — перезапускает голосовой режим'
    def disable_startup(icon, item):
        import winreg
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, 'AtlasVoice')
            icon.notify('Автозапуск Atlas отключён. Сейчас приложение продолжит работать.', 'Atlas Voice')
        except FileNotFoundError:
            icon.notify('Автозапуск уже отключён.', 'Atlas Voice')
    def open_atlas(icon, item):
        import subprocess
        pythonw = Path(sys.prefix) / 'Scripts' / 'pythonw.exe'
        if not pythonw.is_file():
            icon.notify('Не найден локальный запускатель Atlas.', 'Проверьте установку')
            return
        try:
            subprocess.Popen(
                [str(pythonw), '-m', 'atlas.local_voice', '--desktop-window'],
                cwd=str(Path(__file__).resolve().parents[2]),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000),
                close_fds=True,
            )
        except Exception:
            icon.notify('Не удалось открыть окно Atlas.', 'Ошибка запуска')
    def open_voice_settings(icon, item):
        import subprocess
        pythonw = Path(sys.prefix) / 'Scripts' / 'pythonw.exe'
        if not pythonw.is_file():
            icon.notify('Не найден локальный запускатель Atlas.', 'Проверьте установку')
            return
        try:
            command = [str(pythonw), '-m', 'atlas.local_voice', '--voice-settings']
            if config_path:
                command.extend(['--config', str(_config_path(config_path))])
            subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[2]),
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000), close_fds=True)
        except Exception:
            icon.notify('Не удалось открыть настройки голоса.', 'Ошибка запуска')
    menu = pystray.Menu(
        pystray.MenuItem('Открыть Atlas', open_atlas, default=True),
        pystray.MenuItem('Настройки голоса', open_voice_settings),
        pystray.MenuItem('Перезапустить голосовой режим', restart_app),
        pystray.MenuItem('Приостановить / возобновить микрофон', toggle,
                         checked=lambda item: pause_event.is_set()),
        pystray.MenuItem('Отключить автозапуск', disable_startup),
        pystray.MenuItem('Выход', quit_app),
    )
    icon = pystray.Icon('AtlasVoice', make_icon('listening'), 'Atlas — слушает локально', menu)
    def setup(tray_icon):
        threading.Thread(target=worker, name='atlas-voice-listener', daemon=True).start()
        def monitor():
            while not stop_event.wait(0.5):
                if kernel32.WaitForSingleObject(restart_handle, 0) == 0:
                    restart_app(tray_icon, None)
                try:
                    state = listener_states.get_nowait()
                    titles = {'listening': 'Atlas — слушает локально', 'processing': 'Atlas — обрабатывает команду',
                              'speaking': 'Atlas — отвечает голосом', 'paused': 'Atlas — микрофон приостановлен',
                              'error': 'Atlas — ошибка голосового режима'}
                    tray_icon.icon = make_icon(state)
                    tray_icon.title = titles.get(state, 'Atlas Voice')
                except queue.Empty:
                    pass
                try:
                    error = listener_errors.get_nowait()
                except queue.Empty:
                    continue
                tray_icon.notify('Atlas Voice остановился (' + error + ')', 'Проверьте конфигурацию и микрофон')
                tray_icon.title = 'Atlas — ошибка запуска голосового режима'
                break
        threading.Thread(target=monitor, name='atlas-voice-monitor', daemon=True).start()
    try:
        icon.run(setup=setup)
    finally:
        stop_event.set()
        listener_stop.set()
        kernel32.CloseHandle(restart_handle)
        kernel32.CloseHandle(mutex)


def open_desktop_model_settings() -> None:
    import subprocess
    pythonw = Path(sys.prefix) / 'Scripts' / 'pythonw.exe'
    if os.name != 'nt' or not pythonw.is_file():
        raise RuntimeError('Не найден локальный запускатель Atlas')
    subprocess.Popen(
        [str(pythonw), '-m', 'atlas.local_voice', '--desktop-window', '--model-settings'],
        cwd=str(Path(__file__).resolve().parents[2]),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000), close_fds=True,
    )


def run_desktop_window(*, model_settings: bool = False) -> None:
    """Open the complete hosted Atlas UI in a native Windows WebView2 window."""
    if os.name != 'nt':
        raise RuntimeError('Нативное окно Atlas пока рассчитано на Windows')

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    mutex_name = 'Local\\AtlasDesktopWindowV1'
    event_name = 'Local\\AtlasDesktopWindowShowV1'
    models_event_name = 'Local\\AtlasDesktopWindowModelsV1'
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    if not mutex:
        raise RuntimeError('Не удалось создать блокировку окна Atlas')
    if ctypes.get_last_error() == 183:
        event = kernel32.OpenEventW(0x0002, False, models_event_name if model_settings else event_name)
        if event:
            kernel32.SetEvent(event)
            kernel32.CloseHandle(event)
        kernel32.CloseHandle(mutex)
        if model_settings and not event:
            # An older already-open desktop process has no models event.
            # Its normal authenticated HTTPS UI remains available in a browser.
            if not webbrowser.open('https://atlas.prism-news.xyz/#models'):
                raise RuntimeError('Перезапустите окно Atlas для открытия настроек моделей')
        return
    show_event = kernel32.CreateEventW(None, False, False, event_name)
    if not show_event:
        kernel32.CloseHandle(mutex)
        raise RuntimeError('Не удалось создать канал активации окна Atlas')
    models_event = kernel32.CreateEventW(None, False, False, models_event_name)
    if not models_event:
        kernel32.CloseHandle(show_event)
        kernel32.CloseHandle(mutex)
        raise RuntimeError('Не удалось создать канал настроек моделей Atlas')

    profile = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Atlas' / 'Desktop' / 'WebView2'
    profile.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    try:
        import webview
        window = webview.create_window(
            'Atlas', 'https://atlas.prism-news.xyz' + ('/#models' if model_settings else ''),
            width=1360, height=900, min_size=(980, 640),
            background_color='#101419', confirm_close=False,
        )
        def activate_existing():
            while not stop_event.is_set():
                models_requested = kernel32.WaitForSingleObject(models_event, 100) == 0
                if models_requested or kernel32.WaitForSingleObject(show_event, 100) == 0:
                    try:
                        if models_requested:
                            window.evaluate_js('window.location.hash = "#models"; window.dispatchEvent(new Event("hashchange"))')
                        window.show()
                        window.restore()
                    except Exception:
                        pass
        threading.Thread(target=activate_existing, name='atlas-window-activation', daemon=True).start()
        # The remote page has no Python/desktop bridge. It gets only its normal
        # HTTPS origin, cookies, and the existing authenticated Atlas API.
        webview.start(gui='edgechromium', debug=False, private_mode=False, storage_path=str(profile))
    finally:
        stop_event.set()
        kernel32.CloseHandle(models_event)
        kernel32.CloseHandle(show_event)
        kernel32.CloseHandle(mutex)


if __name__ == '__main__':
    main()
