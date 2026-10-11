"""Local, offline settings window for the Windows Atlas voice companion."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    'enabled': True,
    'wake_words': ['атлас'],
    'default_mode': 'chat',
    'sample_rate': 16000,
    'device_index': -1,
    'activation_mode': 'wake_word',
    'ptt_key': 'F8',
    'min_confidence': 0.55,
    'free_speech_enabled': True,
    'free_speech_model_path': '',
    'free_speech_device': 'auto',
    'free_speech_compute_type': 'int8',
    'follow_up_seconds': 0,
    'confirmation_timeout_seconds': 12,
    'vad_enabled': True,
    'silence_timeout_ms': 650,
    'confirm_local_actions': True,
    'test_mode': False,
    'speak_responses': True,
    'voice_interrupt_enabled': False,
    'tts_model_path': '',
    'tts_length_scale': 0.94,
    'reply_timeout_seconds': 90,
    'show_command_pill': True,
    'command_pill_seconds': 5,
    'voice_center_enabled': False,
    'voice_sse_enabled': False,
    'network_mode': 'atlas',
    'store_transcripts': True,
    'speak_sensitive': False,
    'session_context_seconds': 60,
    'local_grants': ['local.volume_delta', 'local.volume_set', 'local.mute_set', 'local.media',
                     'local.time_now', 'local.date_today', 'local.show_help', 'local.open_voice_settings',
                     'local.open_site', 'local.launch_app'],
    'local_grant_expires_at': 0,
}
SETTING_KEYS = set(DEFAULTS)
_HISTORY_LOCK = threading.Lock()


def config_path(path: str | None = None) -> Path:
    if path:
        return Path(path)
    base = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Atlas' / 'Voice'
    return base / 'voice.json'


def _normalize(text: str) -> str:
    return ' '.join(text.casefold().replace('ё', 'е').split())


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Локальный voice.json не найден или является ссылкой')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('voice.json должен содержать JSON-объект')
    return value


def _validate(values: dict[str, Any]) -> dict[str, Any]:
    from .capabilities import LOCAL
    for key in ('voice_center_enabled', 'voice_sse_enabled', 'store_transcripts', 'speak_sensitive'):
        if type(values.get(key)) is not bool: raise ValueError('Некорректный параметр приватности')
    if values.get('network_mode') not in {'local_only', 'atlas'}: raise ValueError('Некорректный сетевой режим')
    if type(values.get('session_context_seconds')) is not int or not 0 <= values['session_context_seconds'] <= 120:
        raise ValueError('Контекст должен храниться 0–120 секунд')
    grants = values.get('local_grants')
    if not isinstance(grants, list) or any(not isinstance(x, str) or x not in {c.id for c in LOCAL.values()} for x in grants):
        raise ValueError('Некорректные локальные разрешения')
    expiry = values.get('local_grant_expires_at')
    import math
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry) or expiry < 0: raise ValueError('Некорректный срок разрешения')
    wake_words = values.get('wake_words')
    if not isinstance(wake_words, list) or not 1 <= len(wake_words) <= 5:
        raise ValueError('Укажите от 1 до 5 слов активации')
    cleaned = [_normalize(word) for word in wake_words if isinstance(word, str)]
    if len(cleaned) != len(wake_words) or not all(cleaned) or any(len(word) > 40 for word in cleaned):
        raise ValueError('Слова активации должны быть непустыми строками до 40 символов')
    values['wake_words'] = cleaned

    if values.get('default_mode') not in {'chat', 'task'}:
        raise ValueError('Начальный режим должен быть «разговор» или «задачи»')
    if values.get('sample_rate') not in {8000, 16000, 32000}:
        raise ValueError('Частота микрофона должна быть 8000, 16000 или 32000 Гц')
    if not isinstance(values.get('device_index'), int) or values['device_index'] < -1:
        raise ValueError('Индекс микрофона задан некорректно')
    if values.get('activation_mode') not in {'wake_word', 'wake_word_followup', 'push_to_talk'}:
        raise ValueError('Режим активации задан некорректно')
    if not isinstance(values.get('ptt_key'), str) or not re.fullmatch(r'(?:F(?:[1-9]|1[0-2])|SPACE)', values['ptt_key']):
        raise ValueError('Клавиша push-to-talk должна быть F1–F12 или SPACE')
    if not isinstance(values.get('min_confidence'), (int, float)) or not 0.2 <= values['min_confidence'] <= 0.99:
        raise ValueError('Порог уверенности должен быть от 0,20 до 0,99')
    if not isinstance(values.get('free_speech_enabled'), bool):
        raise ValueError('Состояние улучшенного распознавания задано некорректно')
    if not isinstance(values.get('free_speech_model_path'), str):
        raise ValueError('Путь модели свободной речи задан некорректно')
    if values['free_speech_model_path'] and (
        not Path(values['free_speech_model_path']).is_absolute() or Path(values['free_speech_model_path']).is_symlink()
    ):
        raise ValueError('Путь модели свободной речи должен быть локальным абсолютным путём')
    if values.get('free_speech_device') not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('Устройство модели свободной речи задано некорректно')
    if values.get('free_speech_compute_type') not in {'auto', 'int8', 'int8_float16', 'float16', 'float32'}:
        raise ValueError('Тип вычислений модели свободной речи задан некорректно')
    if type(values.get('follow_up_seconds')) is not int or not 0 <= values['follow_up_seconds'] <= 20:
        raise ValueError('Окно продолжения разговора должно быть от 0 до 20 секунд')
    if type(values.get('confirmation_timeout_seconds')) is not int or not 5 <= values['confirmation_timeout_seconds'] <= 30:
        raise ValueError('Время подтверждения должно быть от 5 до 30 секунд')
    if not isinstance(values.get('vad_enabled'), bool):
        raise ValueError('Состояние VAD задано некорректно')
    if not isinstance(values.get('confirm_local_actions'), bool) or not isinstance(values.get('test_mode'), bool):
        raise ValueError('Защитные режимы заданы некорректно')
    if not isinstance(values.get('silence_timeout_ms'), int) or not 200 <= values['silence_timeout_ms'] <= 2000:
        raise ValueError('Тишина перед завершением фразы должна быть от 200 до 2000 мс')
    if not isinstance(values.get('enabled'), bool):
        raise ValueError('Состояние голосового режима задано некорректно')
    if type(values.get('voice_interrupt_enabled')) is not bool:
        raise ValueError('Некорректный режим голосового перебивания')
    if not isinstance(values.get('speak_responses'), bool):
        raise ValueError('Состояние озвучивания задано некорректно')
    if not isinstance(values.get('show_command_pill'), bool):
        raise ValueError('Состояние карточки задано некорректно')
    if not isinstance(values.get('reply_timeout_seconds'), int) or not 5 <= values['reply_timeout_seconds'] <= 300:
        raise ValueError('Таймаут ответа должен быть от 5 до 300 секунд')
    if not isinstance(values.get('command_pill_seconds'), int) or not 1 <= values['command_pill_seconds'] <= 30:
        raise ValueError('Время карточки должно быть от 1 до 30 секунд')
    if not isinstance(values.get('tts_length_scale'), (int, float)) or not 0.75 <= values['tts_length_scale'] <= 1.4:
        raise ValueError('Темп речи должен быть от 0,75 до 1,4')
    if not isinstance(values.get('tts_model_path'), str):
        raise ValueError('Путь голосовой модели задан некорректно')
    if values['tts_model_path'] and (not Path(values['tts_model_path']).is_absolute() or Path(values['tts_model_path']).is_symlink()):
        raise ValueError('Путь голосовой модели должен быть локальным абсолютным путём')
    values['tts_length_scale'] = float(values['tts_length_scale'])
    values['min_confidence'] = float(values['min_confidence'])
    return values


def _effective(raw: dict[str, Any]) -> dict[str, Any]:
    active = raw.get('active_profile')
    profiles = raw.get('profiles')
    merged = dict(DEFAULTS)
    merged.update(raw)
    if isinstance(active, str) and isinstance(profiles, dict) and isinstance(profiles.get(active), dict):
        merged.update(profiles[active])
    return merged


def effective_config(path: Path) -> dict[str, Any]:
    return _effective(_read(path))


def public_settings(path: Path) -> dict[str, Any]:
    raw = _read(path)
    effective = _effective(raw)
    tts_model = _resolve_tts_model(effective.get('tts_model_path'))
    settings = {
        key: effective.get(key, default) for key, default in DEFAULTS.items()
    }
    settings = _validate(settings)
    settings.update({
        'active_profile': raw.get('active_profile', 'По умолчанию'),
        'profiles': sorted(raw.get('profiles', {}).keys()) if isinstance(raw.get('profiles'), dict) else [],
        'model_available': Path(str(effective.get('model_path', ''))).is_dir(),
        'tts_model_available': tts_model is not None,
        'tts_model_path': str(tts_model or effective.get('tts_model_path', '')),
        'tts_models': list_tts_models(tts_model or effective.get('tts_model_path')),
        'free_speech_model_available': _resolve_free_speech_model(effective.get('free_speech_model_path')) is not None,
        'free_speech_dependency_available': _module_available('faster_whisper'),
        'free_speech_models': list_free_speech_models(effective.get('free_speech_model_path')),
        'companion_configured': bool(effective.get('companion_config')),
        'audio_devices': list_audio_devices(),
        'scenarios': validate_scenarios(effective.get('scenarios', []), effective),
    })
    return settings


def update_settings(path: Path, incoming: dict[str, Any]) -> dict[str, Any]:
    raw = _read(path)
    allowed = {
        *SETTING_KEYS,
    }
    current = _effective(raw)
    values = {key: current.get(key) for key in allowed}
    values.update({key: value for key, value in incoming.items() if key in allowed})
    for key, default in DEFAULTS.items():
        if values.get(key) is None:
            values[key] = default
    values = _validate(values)
    profile_name = incoming.get('profile_name') or raw.get('active_profile')
    if isinstance(profile_name, str) and profile_name and profile_name != 'По умолчанию':
        if not 1 <= len(profile_name) <= 40 or not re.fullmatch(r'[\wА-Яа-яЁё ._-]+', profile_name):
            raise ValueError('Название профиля содержит недопустимые символы')
        profiles = raw.setdefault('profiles', {})
        if not isinstance(profiles, dict):
            raise ValueError('Список профилей повреждён')
        if profile_name not in profiles and len(profiles) >= 10:
            raise ValueError('Можно создать не более 10 профилей')
        profiles[profile_name] = values
        raw['active_profile'] = profile_name
    else:
        raw.update(values)
        if incoming.get('active_profile') == 'По умолчанию':
            raw.pop('active_profile', None)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.voice-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(raw, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return public_settings(path)


def activate_profile(path: Path, name: str) -> dict[str, Any]:
    raw = _read(path)
    if name == 'По умолчанию':
        raw.pop('active_profile', None)
    else:
        profiles = raw.get('profiles')
        if not isinstance(profiles, dict) or not isinstance(profiles.get(name), dict):
            raise ValueError('Профиль не найден')
        raw['active_profile'] = name
    return _write(path, raw)


def delete_profile(path: Path, name: str) -> dict[str, Any]:
    if name == 'По умолчанию':
        raise ValueError('Профиль по умолчанию удалить нельзя')
    raw = _read(path)
    profiles = raw.get('profiles')
    if not isinstance(profiles, dict) or name not in profiles:
        raise ValueError('Профиль не найден')
    profiles.pop(name)
    if raw.get('active_profile') == name:
        raw.pop('active_profile', None)
    return _write(path, raw)


def _write(path: Path, raw: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.voice-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(raw, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return public_settings(path)


def list_audio_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_input = sd.default.device[0]
    except Exception:
        return []
    return [
        {'index': index, 'name': str(device['name']), 'channels': int(device['max_input_channels']),
         'default': index == default_input}
        for index, device in enumerate(devices)
        if int(device['max_input_channels']) > 0
    ]


def _resolve_tts_model(selected: Any) -> Path | None:
    candidates: list[Path] = []
    if isinstance(selected, str) and selected:
        candidates.append(Path(selected))
    default_dir = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Atlas' / 'Voice' / 'TTS'
    candidates.extend(sorted(default_dir.glob('*.onnx')) if default_dir.is_dir() and not default_dir.is_symlink() else [])
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _resolve_free_speech_model(selected: Any) -> Path | None:
    candidates: list[Path] = []
    if isinstance(selected, str) and selected:
        candidates.append(Path(selected))
    default_dir = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Atlas' / 'Voice' / 'Models'
    if default_dir.is_dir() and not default_dir.is_symlink():
        candidates.extend(sorted(path for path in default_dir.glob('whisper*') if path.is_dir()))
    if isinstance(selected, str) and selected:
        candidates = [Path(selected)]
    for candidate in candidates:
        required = ('config.json', 'model.bin', 'tokenizer.json')
        if (candidate.is_dir() and not candidate.is_symlink()
                and all((candidate / name).is_file() and not (candidate / name).is_symlink() for name in required)
                and any((candidate / name).is_file() and not (candidate / name).is_symlink()
                        for name in ('vocabulary.txt', 'vocabulary.json'))):
            return candidate
    return None


def list_free_speech_models(selected: Any) -> list[dict[str, Any]]:
    base = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Atlas' / 'Voice' / 'Models'
    candidates = list(base.glob('whisper*')) if base.is_dir() and not base.is_symlink() else []
    if isinstance(selected, str) and selected:
        candidates.append(Path(selected))
    found = {}
    for candidate in candidates:
        path = _resolve_free_speech_model(str(candidate))
        if path is not None:
            found[str(path)] = {'path': str(path), 'name': path.name,
                                'size_mb': round((path / 'model.bin').stat().st_size / (1024 * 1024))}
    return sorted(found.values(), key=lambda item: item['name'])


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def list_tts_models(selected: Any) -> list[dict[str, str]]:
    selected_path = _resolve_tts_model(selected)
    parent = selected_path.parent if selected_path is not None else Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Atlas' / 'Voice' / 'TTS'
    if not parent.is_dir() or parent.is_symlink():
        return []
    return [{'path': str(path), 'name': path.stem} for path in sorted(parent.glob('*.onnx')) if not path.is_symlink()]


def test_microphone(device_index: int = -1, sample_rate: int = 16000, duration: float = 0.7) -> dict[str, Any]:
    try:
        import numpy as np
        import sounddevice as sd
        recording = sd.rec(int(sample_rate * duration), samplerate=sample_rate, channels=1,
                           dtype='float32', device=None if device_index < 0 else device_index)
        sd.wait()
        peak = float(np.max(np.abs(recording))) if recording.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(recording)))) if recording.size else 0.0
        return {'ok': True, 'rms': round(rms, 4), 'peak': round(peak, 4), 'level_percent': min(100, round(peak * 100))}
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:180]}


def test_recognition(path: Path, device_index: int = -1, sample_rate: int = 16000) -> dict[str, Any]:
    """Recognize a short local sample without writing or uploading audio."""
    if sample_rate not in {8000, 16000, 32000} or device_index < -1:
        return {'ok': False, 'error': 'Некорректные параметры микрофона'}
    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model
        from .local_voice import _audio_level

        config = effective_config(path)
        model_path = Path(str(config.get('model_path', '')))
        if not model_path.is_dir() or model_path.is_symlink():
            raise ValueError('Модель распознавания не найдена')
        recognizer = KaldiRecognizer(Model(str(model_path)), sample_rate)
        recognized: list[str] = []
        highest_level = 0.0
        blocksize = sample_rate // 4
        with sd.RawInputStream(samplerate=sample_rate, blocksize=blocksize, dtype='int16', channels=1,
                               device=None if device_index < 0 else device_index) as stream:
            for _ in range(24):
                audio, _ = stream.read(blocksize)
                chunk = bytes(audio)
                highest_level = max(highest_level, _audio_level(chunk))
                if recognizer.AcceptWaveform(chunk):
                    text = json.loads(recognizer.Result()).get('text', '').strip()
                    if text:
                        recognized.append(text)
        final = json.loads(recognizer.FinalResult()).get('text', '').strip()
        if final:
            recognized.append(final)
        return {'ok': True, 'text': ' '.join(recognized)[:240], 'level_percent': min(100, round(highest_level * 100))}
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:180]}


def diagnostics(path: Path) -> dict[str, Any]:
    raw = _effective(_read(path))
    tts_model = _resolve_tts_model(raw.get('tts_model_path'))
    modules = {}
    for name in ('sounddevice', 'vosk', 'faster_whisper', 'piper', 'pystray', 'webview'):
        try:
            __import__(name)
            modules[name] = True
        except Exception:
            modules[name] = False
    return {
        'platform': os.name,
        'config': True,
        'enabled': raw.get('enabled') is True,
        'model_available': Path(str(raw.get('model_path', ''))).is_dir(),
        'tts_model_available': tts_model is not None,
        'free_speech_enabled': raw.get('free_speech_enabled') is True,
        'free_speech_model_available': _resolve_free_speech_model(raw.get('free_speech_model_path')) is not None,
        'companion_configured': bool(raw.get('companion_config')),
        'dependencies': modules,
        'audio_devices': list_audio_devices(),
    }


def validate_scenarios(scenarios: Any, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if scenarios is None:
        return []
    if not isinstance(scenarios, list) or len(scenarios) > 20:
        raise ValueError('Допустимо не более 20 сценариев')
    config = config or {}
    sites_value = config.get('sites')
    applications_value = config.get('applications')
    sites: dict[str, Any] = sites_value if isinstance(sites_value, dict) else {}
    applications: dict[str, Any] = applications_value if isinstance(applications_value, dict) else {}
    allowed_media = {'play_pause', 'next', 'previous'}
    result = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get('name'), str):
            raise ValueError('У каждого сценария должно быть имя')
        phrases = scenario.get('phrases')
        actions = scenario.get('actions')
        if not isinstance(phrases, list) or not 1 <= len(phrases) <= 10 or not all(isinstance(x, str) and _normalize(x) for x in phrases):
            raise ValueError('Сценарий должен содержать от 1 до 10 фраз')
        if not isinstance(actions, list) or not 1 <= len(actions) <= 12:
            raise ValueError('Сценарий должен содержать от 1 до 12 действий')
        safe_actions = []
        for action in actions:
            if not isinstance(action, dict) or action.get('type') not in {
                'volume_set', 'volume_delta', 'mute_set', 'media', 'open_site', 'launch_app',
                'show_help', 'time_now', 'date_today', 'open_voice_settings',
            }:
                raise ValueError('Сценарий содержит запрещённое действие')
            kind = action['type']
            if kind in {'volume_set', 'volume_delta'} and type(action.get('percent')) is not int:
                raise ValueError('Громкость в сценарии должна быть целым числом')
            if kind == 'volume_set' and not 0 <= action['percent'] <= 100:
                raise ValueError('Громкость должна быть от 0 до 100')
            if kind == 'volume_delta' and not -30 <= action['percent'] <= 30:
                raise ValueError('Изменение громкости должно быть от -30 до 30')
            if kind == 'mute_set' and type(action.get('muted')) is not bool:
                raise ValueError('mute_set требует логического значения')
            if kind == 'media' and action.get('command') not in allowed_media:
                raise ValueError('Медиакоманда не разрешена')
            if kind == 'open_site' and (not isinstance(action.get('site'), str) or action['site'] not in sites):
                raise ValueError('Сайт сценария отсутствует в allowlist')
            if kind == 'launch_app' and (not isinstance(action.get('app'), str) or action['app'] not in applications):
                raise ValueError('Приложение сценария отсутствует в allowlist')
            safe_actions.append(dict(action))
        if type(scenario.get('enabled', True)) is not bool:
            raise ValueError('Состояние сценария должно быть логическим значением')
        result.append({'name': scenario['name'][:80], 'phrases': [x[:100] for x in phrases],
                       'actions': safe_actions, 'enabled': scenario.get('enabled', True)})
    return result


def update_scenarios(path: Path, scenarios: Any) -> dict[str, Any]:
    raw = _read(path)
    effective = _effective(raw)
    raw['scenarios'] = validate_scenarios(scenarios, effective)
    return _write(path, raw)


def _history_path(path: Path) -> Path:
    return path.with_name('history.jsonl')


def record_history(path: Path, kind: str, status: str, command: str | None = None) -> None:
    from .voice_privacy import private_transcript
    entry = {'at': time.time(), 'kind': kind[:40], 'status': status[:180]}
    try: keep_text = effective_config(path).get('store_transcripts', True)
    except (OSError, ValueError): keep_text = False
    if command and keep_text and kind == 'local' and not private_transcript(command):
        entry['command'] = command[:180]
    target = _history_path(path)
    with _HISTORY_LOCK:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
            lines = target.read_text(encoding='utf-8').splitlines()[-100:]
            target.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
        except OSError:
            # История — диагностика, она не должна ломать рабочую команду.
            return


def read_history(path: Path) -> list[dict[str, Any]]:
    target = _history_path(path)
    if not target.is_file() or target.is_symlink():
        return []
    try: keep_text = effective_config(path).get('store_transcripts', True)
    except (OSError, ValueError): keep_text = False
    result = []
    for line in target.read_text(encoding='utf-8').splitlines()[-50:]:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                if not keep_text: value.pop('command', None)
                result.append(value)
        except json.JSONDecodeError:
            continue
    return result


def clear_history(path: Path) -> None:
    target = _history_path(path)
    if target.is_file() and not target.is_symlink():
        target.write_text('', encoding='utf-8')


SETTINGS_HTML = r'''<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Настройки голоса Atlas</title>
<style>
:root{color-scheme:dark;font:15px Segoe UI,system-ui,sans-serif;background:#101419;color:#edf2f7}
body{max-width:760px;margin:0 auto;padding:28px 32px;background:#101419}h1{font-size:24px;margin:0 0 8px}h2{font-size:17px;margin:26px 0 10px}p{color:#aab7c4;line-height:1.45}.hint{font-size:13px}
fieldset{border:1px solid #2b3742;border-radius:10px;padding:16px;margin:14px 0}label{display:block;margin:11px 0}input,select{box-sizing:border-box;width:100%;margin-top:5px;padding:9px;border:1px solid #43515f;border-radius:6px;background:#182029;color:#edf2f7}input[type=checkbox]{width:auto;margin:0 8px 0 0}label.check{display:flex;align-items:center}
button{border:0;border-radius:7px;padding:10px 16px;background:#4f9cff;color:white;font-weight:600;cursor:pointer;margin:8px 8px 0 0}button.secondary{background:#2b3742}button:disabled{opacity:.6;cursor:wait}.actions{display:flex;justify-content:flex-end;margin-top:20px}.status{min-height:22px;color:#86d993}.error{color:#ff9b9b}
textarea{box-sizing:border-box;width:100%;padding:12px;border:1px solid #43515f;border-radius:6px;background:#182029;color:#edf2f7;font:13px Consolas,monospace;resize:vertical}
</style></head>
<body>
<h1>Настройки голоса Atlas</h1>
<button id="voice_center" type="button">Открыть раздел «Голос» в Atlas</button><p id="voice_center_result" class="hint"></p>
<p id="bridge_status" class="hint" role="status">Подключаю локальные настройки…</p>
<p>Распознавание выполняется локально на этом ПК. Аудио не сохраняется и не отправляется в Atlas. Только активированный запрос, не являющийся локальной командой, передаётся в Atlas текстом.</p>
<fieldset><legend>Быстрая проверка</legend>
<p>Выберите микрофон ниже, затем нажмите «Проверить распознавание» и скажите «Атлас, сделай погромче». Проверка покажет услышанный текст, не меняя громкость.</p>
<button id="restart_voice" type="button">Сохранить и перезапустить голос</button>
<span id="restart_result" class="hint"></span>
</fieldset>
<fieldset id="software_profiles"><legend>Автономный профиль</legend>
<label>Активный профиль<select id="profile"></select></label>
<button id="new_profile" type="button" class="secondary">Создать профиль из текущих настроек</button>
<button id="delete_profile" type="button" class="secondary">Удалить выбранный профиль</button>
<p class="hint">Профиль хранит только параметры голосового режима. Сертификаты, приложения и секреты в профили не копируются.</p>
</fieldset>
<fieldset id="software_mode"><legend>Основной режим (без Voice Center)</legend>
<label class="check"><input id="enabled" type="checkbox">Включить локальное голосовое управление</label>
<label>Начальный режим<select id="default_mode"><option value="chat">Разговор</option><option value="task">Задачи Atlas</option></select></label>
<label>Слова активации<input id="wake_words" maxlength="200" placeholder="атлас, компьютер"></label>
<label>Активация<select id="activation_mode"><option value="wake_word">Строго: «Атлас» перед каждой фразой</option><option value="wake_word_followup">Разговор: «Атлас» и окно продолжения</option><option value="push_to_talk">Только при удержании клавиши</option></select></label>
<p id="activation_hint" class="hint" role="status"></p>
<label>Клавиша push-to-talk<select id="ptt_key"><option>F8</option><option>F9</option><option>F10</option><option>SPACE</option></select></label>
<label>Продолжение разговора без слова «Атлас», секунд<input id="follow_up_seconds" type="number" min="0" max="20"></label>
<label>Время подтверждения локального действия, секунд<input id="confirmation_timeout_seconds" type="number" min="5" max="30"></label>
<p class="hint">Окно продолжения работает только в режиме «Разговор»; 0 выключает его. Внутри окна фоновая речь тоже может стать запросом. В строгом режиме говорите в одной фразе: «Атлас, расскажи о…». Во время ожидания локального подтверждения без обращения принимаются только его подтверждение («подтверждаю»/«да»), отмена или завершение разговора. Серверные подтверждения остаются в Atlas.</p>
</fieldset>
<fieldset id="software_privacy"><legend>Автономная приватность</legend>
<label>Маршрут фраз<select id="network_mode"><option value="atlas">Локальное распознавание, серверный Atlas</option><option value="local_only">Только локальные команды</option></select></label>
<label class="check"><input id="store_transcripts" type="checkbox">Сохранять текст запросов</label>
<label class="check"><input id="speak_sensitive" type="checkbox">Озвучивать потенциально чувствительные ответы</label>
<label>Короткий контекст, секунд<input id="session_context_seconds" type="number" min="0" max="120"></label>
<p class="hint">Без сохранения chat-текст временно обрабатывается очередью и удаляется после доставки результата либо по сроку очистки. Задачи с инструментами требуют журнала. Полный mute — выключение голосового управления. Настройки центра меняются в основном разделе «Голос».</p>
</fieldset>
<fieldset><legend>Модель ответа (ИИ)</legend>
<p>Модель основного помощника используется и в голосовом разговоре. Выберите модель RouterAI из каталога или укажите её ID в общих настройках Atlas. Здесь ключи API не запрашиваются.</p>
<button id="model_settings" type="button" class="secondary">Выбрать модель ИИ в Atlas</button>
<p id="model_settings_result" class="hint" role="status"></p>
<p class="hint">Whisper ниже — распознавание речи, Piper — озвучка; это не модель, которая отвечает на ваши вопросы. Настройки ИИ требуют входа владельца.</p>
</fieldset>
<fieldset><legend>Микрофон и ответ</legend>
<label>Микрофон<select id="device_index"><option value="-1">Системный по умолчанию</option></select></label>
<p id="device_status" class="hint">Ожидаю список микрофонов…</p>
<label>Частота микрофона<select id="sample_rate"><option value="8000">8000 Гц</option><option value="16000">16000 Гц</option><option value="32000">32000 Гц</option></select></label>
<label>Минимальная уверенность распознавания<input id="min_confidence" type="number" min="0.2" max="0.99" step="0.01"></label>
<label class="check"><input id="free_speech_enabled" type="checkbox">Улучшать свободную речь вторым локальным распознавателем Whisper</label>
<label>Установленная модель Whisper<select id="free_speech_model_path"><option value="">Автоматический выбор</option></select></label>
<label>Вычисление Whisper<select id="free_speech_device"><option value="auto">Автоматически</option><option value="cpu">Процессор</option><option value="cuda">Видеокарта NVIDIA</option></select></label>
<label>Точность вычислений<select id="free_speech_compute_type"><option>int8</option><option>int8_float16</option><option>float16</option><option>float32</option><option>auto</option></select></label>
<p class="hint">Whisper используется только после того, как Vosk не нашёл локальную команду. Аудио не сохраняется; модель должна быть установлена локально в каталоге Atlas Voice.</p>
<label class="check"><input id="confirm_local_actions" type="checkbox">Подтверждать открытие сайтов и приложений</label>
<label class="check"><input id="test_mode" type="checkbox">Тестовый режим: только показывать команды</label>
<label class="check"><input id="speak_responses" type="checkbox">Озвучивать ответы локальным голосом Piper</label>
<label class="check"><input id="voice_interrupt_enabled" type="checkbox">Слушать «Атлас, стоп» во время ответа (лучше в наушниках)</label>
<p class="hint">Esc или клавиша PTT останавливают озвучку и ожидание всегда. Голосовое перебивание включается отдельно: эхоподавления нет, звук колонок может вызвать ложную остановку. Во время ответа другие фразы и подтверждения не выполняются. Серверная задача остаётся в Atlas.</p>
<label>Локальный голос<select id="tts_model_path"><option value="">Не выбран</option></select></label>
<label>Темп речи<input id="tts_length_scale" type="number" min="0.75" max="1.4" step="0.01"></label>
<label>Ожидание ответа Atlas, секунд<input id="reply_timeout_seconds" type="number" min="5" max="300"></label>
<button id="test_mic" type="button" class="secondary">Проверить микрофон</button>
<span id="mic_result" class="hint"></span>
<br><button id="test_recognition" type="button" class="secondary">Проверить распознавание (6 секунд)</button>
<span id="recognition_result" class="hint"></span>
<h2>Сравнение распознавания</h2>
<p class="hint">Оба движка услышат одну запись. Сначала загрузятся модели, затем по вашей кнопке начнётся отсчёт. Запросы в Atlas и команды компьютеру не отправляются. Результат отображается только в этом окне; аудио не сохраняется.</p>
<label>Фраза для сравнения (необязательно)<input id="expected_phrase" maxlength="500" placeholder="Атлас, расскажи, какая сегодня погода"></label>
<button id="prepare_probe" type="button" class="secondary">Подготовить сравнение</button>
<button id="begin_probe" type="button" disabled>Начать запись</button>
<button id="cancel_probe" type="button" class="secondary" disabled>Остановить проверку</button>
<p id="probe_status" role="status">Проверка не запущена</p>
<div id="probe_results"></div>
<h2>Voice Quality Lab — серия проб</h2>
<label>Условия<select id="quality_condition"><option value="quiet">Тишина</option><option value="music">Музыка</option><option value="headphones">Наушники</option><option value="distance">Расстояние</option><option value="fast">Быстрая / короткая команда</option><option value="names">Имена сайтов / приложений</option></select></label>
<label class="check"><input id="quality_wake" type="checkbox" checked>В пробе было обращение «Атлас»</label>
<label>Ожидаемое намерение<select id="quality_intent"><option value="">Не оценивать правильность команды</option><option value="atlas_request">Свободный запрос Atlas</option><option value="volume_delta">Изменить громкость</option><option value="volume_set">Задать громкость</option><option value="mute_set">Mute звука</option><option value="media">Медиа</option><option value="open_site">Открыть сайт</option><option value="launch_app">Открыть приложение</option></select></label>
<button id="quality_accept" type="button" class="secondary">Учесть завершённую пробу</button>
<button id="quality_refresh" type="button" class="secondary">Показать метрики</button>
<button id="quality_tts" type="button" class="secondary">Проверить Piper и первый звук</button>
<pre id="quality_result" class="hint"></pre>
<p class="hint">Сохраняются только числа, модель и условие; текст и аудио пробы не сохраняются. Нужна серия положительных и отрицательных проб, а не один удачный пример. ASR latency — время обработки записи; runtime оценивает конец фразы по RMS. TTS измеряется до передачи аудио звуковой карте, не акустическим датчиком. Недоступные метрики показываются как null, не как нулевые.</p>
</fieldset>
<fieldset><legend>Карточка команды</legend>
<label class="check"><input id="show_command_pill" type="checkbox">Показывать локальную карточку статуса</label>
<label>Скрывать карточку через, секунд<input id="command_pill_seconds" type="number" min="1" max="30"></label>
</fieldset>
<fieldset id="software_scenarios"><legend>Автономные безопасные сценарии</legend>
<p class="hint">Редактор принимает только фиксированные действия: громкость, мультимедиа, allowlist-сайты и allowlist-приложения. Shell, PowerShell и произвольный код запрещены.</p>
<textarea id="scenarios_json" rows="8" spellcheck="false"></textarea>
<button id="save_scenarios" type="button" class="secondary">Сохранить сценарии</button>
</fieldset>
<fieldset><legend>Последние локальные команды</legend>
<div id="history" class="hint">История загружается…</div>
<button id="clear_history" type="button" class="secondary">Очистить историю</button>
</fieldset>
<fieldset><legend>Диагностика</legend><button id="diagnose" type="button" class="secondary">Проверить Desktop Voice</button><pre id="diagnostics_output" class="hint"></pre></fieldset>
<p id="capabilities" class="hint"></p><p id="status" class="status" role="status"></p>
<div class="actions"><button id="cancel" class="secondary">Закрыть</button><button id="save">Сохранить</button></div>
<script>
const ids=['enabled','default_mode','wake_words','activation_mode','ptt_key','device_index','sample_rate','min_confidence','free_speech_enabled','free_speech_model_path','free_speech_device','free_speech_compute_type','follow_up_seconds','confirmation_timeout_seconds','confirm_local_actions','test_mode','speak_responses','voice_interrupt_enabled','tts_model_path','tts_length_scale','reply_timeout_seconds','show_command_pill','command_pill_seconds','network_mode','store_transcripts','speak_sensitive','session_context_seconds'];
let installedModels=[];
let centerManaged=false;
function fillWhisper(s){installedModels=s.free_speech_models||[];const node=el('free_speech_model_path');node.replaceChildren(new Option('Автоматический выбор',''),...installedModels.map(x=>new Option(`${x.name} · ${x.size_mb} МБ`,x.path)));if(s.free_speech_model_path&&!installedModels.some(x=>x.path===s.free_speech_model_path))node.add(new Option('Выбранная модель недоступна',s.free_speech_model_path));node.value=s.free_speech_model_path||''}
const el=id=>document.getElementById(id);
function activationControls(){const mode=el('activation_mode').value;el('follow_up_seconds').disabled=mode!=='wake_word_followup';el('ptt_key').disabled=mode!=='push_to_talk';el('wake_words').disabled=mode==='push_to_talk';el('activation_hint').textContent=mode==='wake_word'?'Каждая новая фраза должна начинаться с точного слова активации. После ответа фоновая речь игнорируется.':mode==='wake_word_followup'?'Обращение включает разговор. В заданном окне после ответа можно говорить без «Атлас». Похожие варианты слова активации допускаются.':'Слово «Атлас» не требуется. Речь принимается только при удержании выбранной клавиши.'}
function selectedDevice(){el('device_status').textContent=`Выбран: ${el('device_index').selectedOptions[0]?.textContent||'устройство недоступно'}. Проверка и сохранение относятся к этому микрофону.`}
function fill(s){
  centerManaged=Boolean(s.voice_center_enabled);
  for(const id of ['software_profiles','software_mode','software_privacy','software_scenarios'])el(id).hidden=Boolean(s.voice_center_enabled);
  fillWhisper(s);
  for(const id of ids){const node=el(id);if(node.type==='checkbox')node.checked=Boolean(s[id]);else if(id==='wake_words')node.value=s[id].join(', ');else if(id==='tts_model_path'){node.replaceChildren(new Option('Не выбран',''),...s.tts_models.map(x=>new Option(x.name,x.path)));node.value=s[id]}else node.value=s[id]}
  for(const id of ['confirm_local_actions','voice_interrupt_enabled'])el(id).closest('label').hidden=centerManaged;
  const profileEl=el('profile');profileEl.replaceChildren(new Option('По умолчанию','По умолчанию'),...s.profiles.map(x=>new Option(x,x)));profileEl.value=s.active_profile;
  el('scenarios_json').value=JSON.stringify(s.scenarios,null,2);
  el('capabilities').textContent=`Vosk: ${s.model_available?'готова':'не найдена'} · Whisper: ${s.free_speech_dependency_available&&s.free_speech_model_available?'готов':'пакет или модель не найдены'} · Piper: ${s.tts_model_available?'готов':'не найден'} · Companion: ${s.companion_configured?'настроен':'не настроен'}`;
  const deviceEl=el('device_index');const defaultDevice=s.audio_devices.find(d=>d.default);
  deviceEl.replaceChildren(new Option(`Системный по умолчанию${defaultDevice?' — '+defaultDevice.name:''}`,'-1'),...s.audio_devices.map(d=>new Option(`${d.index}: ${d.name}`,String(d.index))));deviceEl.value=s.device_index;
  selectedDevice();activationControls();loadHistory();
}
function collect(){
  const values={};const numeric=new Set(['device_index','sample_rate','min_confidence','tts_length_scale','reply_timeout_seconds','command_pill_seconds','follow_up_seconds','confirmation_timeout_seconds','session_context_seconds']);
  for(const id of ids){const node=el(id);values[id]=node.type==='checkbox'?node.checked:id==='wake_words'?node.value.split(',').map(x=>x.trim()).filter(Boolean):numeric.has(id)?Number(node.value):node.value}
  if(centerManaged)for(const key of ['enabled','default_mode','wake_words','activation_mode','ptt_key','follow_up_seconds','confirmation_timeout_seconds','confirm_local_actions','voice_interrupt_enabled','network_mode','store_transcripts','speak_sensitive','session_context_seconds'])delete values[key];
  return values;
}
async function loadHistory(){try{const rows=await pywebview.api.get_history();const box=el('history');box.replaceChildren();if(!rows.length){box.textContent='История пока пуста.';return}for(const x of rows.reverse()){const row=document.createElement('div');row.textContent=`${new Date(x.at*1000).toLocaleString('ru')} · ${x.command||x.kind} · ${x.status}`;box.append(row)}}catch(e){el('history').textContent='История недоступна'}}
let probeGeneration=0;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function probeBusy(busy){el('prepare_probe').disabled=busy;el('cancel_probe').disabled=!busy;for(const id of ['save','restart_voice','test_recognition','test_mic','device_index','sample_rate','free_speech_model_path','free_speech_device','free_speech_compute_type'])el(id).disabled=busy}
function showProbeResult(r){const box=el('probe_results');box.replaceChildren();if(r.redacted){box.textContent=r.message;return}const routes={local:'команда компьютеру',atlas:'запрос Atlas',mode:'смена режима',clarify:'нужно уточнение',empty:'речь не распознана'};for(const [label,key] of [['Vosk','vosk'],['Whisper','whisper']]){const line=document.createElement('p');const wer=r[key+'_wer'];line.textContent=`${label}: ${r[key]||'Речь не распознана'} · ${r[key+'_seconds']} с · ${routes[r[key+'_route']]||r.whisper_status}${wer?` · ошибок слов: ${wer.errors}/${wer.words} (${wer.percent}%)`:''}`;box.append(line)}const note=document.createElement('p');note.textContent=`Уровень RMS: ${r.rms_percent}%. Сравните текст с произнесённой фразой. Одной пробы недостаточно для выбора лучшей модели.`;box.append(note)}
async function pollProbe(generation){try{while(generation===probeGeneration){const s=await pywebview.api.recognition_probe_state();if(generation!==probeGeneration)return;el('probe_status').textContent=s.message;el('begin_probe').disabled=s.stage!=='ready';if(s.stage==='ready')return;if(['completed','cancelled','error'].includes(s.stage)){if(s.result)showProbeResult(s.result);probeBusy(false);el('begin_probe').disabled=true;return}await delay(250)}}catch(e){probeBusy(false);el('begin_probe').disabled=true;el('probe_status').textContent='Ошибка проверки: '+String(e)}}
el('prepare_probe').onclick=async()=>{const generation=++probeGeneration;probeBusy(true);el('probe_results').replaceChildren();el('probe_status').textContent='Подготавливаю модели…';try{await pywebview.api.prepare_recognition_probe(Number(el('device_index').value),Number(el('sample_rate').value),collect(),el('expected_phrase').value);pollProbe(generation)}catch(e){probeBusy(false);el('probe_status').textContent='Ошибка подготовки: '+String(e)}};
el('begin_probe').onclick=async()=>{const generation=probeGeneration;el('begin_probe').disabled=true;try{for(let n=3;n>0;n--){if(generation!==probeGeneration)return;el('probe_status').textContent=`Приготовьтесь: ${n}`;await delay(1000)}if(generation!==probeGeneration)return;await pywebview.api.begin_recognition_probe();pollProbe(generation)}catch(e){probeBusy(false);el('probe_status').textContent='Ошибка записи: '+String(e)}};
el('cancel_probe').onclick=async()=>{++probeGeneration;el('begin_probe').disabled=true;await pywebview.api.cancel_recognition_probe();pollProbe(probeGeneration)};
window.addEventListener('beforeunload',()=>{if(el('prepare_probe').disabled)pywebview.api.cancel_recognition_probe()});
async function load(){try{if(typeof window.pywebview?.api?.get_settings!=='function')throw new Error('Локальный мост настроек не подключился');const s=await pywebview.api.get_settings();fill(s);el('bridge_status').textContent='Настройки подключены — можно выбрать микрофон';el('bridge_status').className='status'}catch(e){el('bridge_status').textContent='Не удалось загрузить настройки: '+String(e);el('bridge_status').className='error'}}
el('device_index').onchange=selectedDevice;
el('voice_center').onclick=async()=>{try{el('voice_center_result').textContent=await pywebview.api.open_voice_center()}catch(e){el('voice_center_result').textContent=String(e)}};
el('quality_accept').onclick=async()=>{try{el('quality_result').textContent=JSON.stringify(await pywebview.api.accept_quality_trial(el('expected_phrase').value,el('quality_intent').value,el('quality_wake').checked,el('quality_condition').value),null,2)}catch(e){el('quality_result').textContent=String(e)}};
el('quality_refresh').onclick=async()=>{try{el('quality_result').textContent=JSON.stringify(await pywebview.api.quality_metrics(),null,2)}catch(e){el('quality_result').textContent=String(e)}};
el('quality_tts').onclick=async()=>{el('quality_tts').disabled=true;try{el('quality_result').textContent=JSON.stringify(await pywebview.api.test_tts_latency(),null,2)}catch(e){el('quality_result').textContent=String(e)}finally{el('quality_tts').disabled=false}};
el('activation_mode').onchange=()=>{if(el('activation_mode').value==='wake_word_followup'&&Number(el('follow_up_seconds').value)===0)el('follow_up_seconds').value=6;activationControls()};
el('model_settings').onclick=async()=>{el('model_settings').disabled=true;try{el('model_settings_result').textContent=await pywebview.api.open_model_settings()}catch(e){el('model_settings_result').textContent='Не удалось открыть настройки ИИ: '+String(e)}finally{el('model_settings').disabled=false}};
el('profile').onchange=async()=>{try{const s=await pywebview.api.activate_profile(el('profile').value);fill(s)}catch(e){el('status').textContent=e;el('status').className='error'}}
el('new_profile').onclick=async()=>{const name=prompt('Название нового профиля');if(!name)return;try{const s=await pywebview.api.save_settings({...collect(),profile_name:name.trim()});fill(s);el('status').textContent='Профиль сохранён'}catch(e){el('status').textContent=e;el('status').className='error'}}
el('delete_profile').onclick=async()=>{const name=el('profile').value;if(name==='По умолчанию')return;try{const s=await pywebview.api.delete_profile(name);fill(s);el('status').textContent='Профиль удалён'}catch(e){el('status').textContent=e;el('status').className='error'}}
el('test_mic').onclick=async()=>{el('mic_result').textContent='Проверяю…';const r=await pywebview.api.test_microphone(Number(el('device_index').value),Number(el('sample_rate').value));el('mic_result').textContent=r.ok?` Уровень: ${r.level_percent}% (RMS ${r.rms})`:` Ошибка: ${r.error}`}
el('test_recognition').onclick=async()=>{el('test_recognition').disabled=true;el('recognition_result').textContent='Говорите сейчас…';try{const r=await pywebview.api.test_recognition(Number(el('device_index').value),Number(el('sample_rate').value));el('recognition_result').textContent=r.ok?` Уровень ${r.level_percent}%. Услышано: ${r.text||'ничего — проверьте микрофон'}`:` Ошибка: ${r.error}`}catch(e){el('recognition_result').textContent='Ошибка проверки: '+String(e)}finally{el('test_recognition').disabled=false}}
el('restart_voice').onclick=async()=>{el('restart_voice').disabled=true;el('restart_result').textContent='Сохраняю…';try{const s=await pywebview.api.save_settings({...collect(),profile_name:el('profile').value});fill(s);el('restart_result').textContent=await pywebview.api.restart_voice()}catch(e){el('restart_result').textContent='Ошибка: '+String(e)}finally{el('restart_voice').disabled=false}}
el('save_scenarios').onclick=async()=>{try{const s=await pywebview.api.save_scenarios(JSON.parse(el('scenarios_json').value));fill(s);el('status').textContent='Сценарии сохранены'}catch(e){el('status').textContent=e;el('status').className='error'}}
el('clear_history').onclick=async()=>{await pywebview.api.clear_history();loadHistory()}
el('diagnose').onclick=async()=>{el('diagnostics_output').textContent='Проверяю…';try{el('diagnostics_output').textContent=JSON.stringify(await pywebview.api.diagnostics(),null,2)}catch(e){el('diagnostics_output').textContent=String(e)}}
el('save').onclick=async()=>{el('save').disabled=true;el('status').className='status';el('status').textContent='Сохраняю…';try{const s=await pywebview.api.save_settings({...collect(),profile_name:el('profile').value});fill(s);el('status').textContent='Сохранено. Для смены микрофона нажмите «Сохранить и перезапустить голос».'}catch(e){el('status').textContent=e;el('status').className='error'}finally{el('save').disabled=false}}
el('cancel').onclick=()=>pywebview.api.close_window();window.addEventListener('pywebviewready',load);
</script></body></html>'''


class VoiceSettingsApi:
    def __init__(self, path: Path) -> None:
        # pywebview recursively exposes every public attribute of js_api.
        # A public Window reference makes it crawl window.native/WebView2
        # indefinitely and prevents the JavaScript bridge from loading.
        self._path = path
        self._window: Any = None
        from .voice_probe import RecognitionProbe
        self._probe = RecognitionProbe()
        self._quality_accepted_probe: int | None = None

    def get_settings(self) -> dict[str, Any]:
        return public_settings(self._path)

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        return update_settings(self._path, values)

    def activate_profile(self, name: str) -> dict[str, Any]:
        return activate_profile(self._path, name)

    def delete_profile(self, name: str) -> dict[str, Any]:
        return delete_profile(self._path, name)

    def save_scenarios(self, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
        return update_scenarios(self._path, scenarios)

    def list_devices(self) -> list[dict[str, Any]]:
        return list_audio_devices()

    def test_microphone(self, device_index: int = -1, sample_rate: int = 16000) -> dict[str, Any]:
        return test_microphone(device_index, sample_rate)

    def test_recognition(self, device_index: int = -1, sample_rate: int = 16000) -> dict[str, Any]:
        return test_recognition(self._path, device_index, sample_rate)

    def prepare_recognition_probe(self, device_index: int = -1, sample_rate: int = 16000,
                                  values: dict[str, Any] | None = None, expected_phrase: str = '') -> dict[str, Any]:
        from .voice_privacy import private_transcript
        if not isinstance(expected_phrase, str) or len(expected_phrase) > 500 or private_transcript(expected_phrase):
            raise ValueError('Введите короткую фразу без приватных данных')
        current = effective_config(self._path)
        fields = {'free_speech_enabled', 'free_speech_model_path', 'free_speech_device',
                  'free_speech_compute_type', 'min_confidence'}
        current.update({key: value for key, value in (values or {}).items() if key in fields})
        current.update(device_index=device_index, sample_rate=sample_rate)
        _validate({key: current.get(key, default) for key, default in DEFAULTS.items()})
        current['_probe_expected'] = expected_phrase
        return self._probe.prepare(current)

    def recognition_probe_state(self) -> dict[str, Any]:
        return self._probe.state()

    def begin_recognition_probe(self) -> dict[str, Any]:
        return self._probe.begin()

    def cancel_recognition_probe(self) -> dict[str, Any]:
        return self._probe.cancel()

    def restart_voice(self) -> str:
        from .local_voice import request_voice_restart
        return request_voice_restart(str(self._path))

    def accept_quality_trial(self, expected: str, intent: str, expects_wake: bool, condition: str) -> dict[str, Any]:
        from .voice_quality import accept_trial
        state = self._probe.state()
        if state.get('stage') != 'completed' or not state.get('result'):
            raise ValueError('Сначала завершите явную пробу распознавания')
        marker = id(self._probe._thread)
        if marker == self._quality_accepted_probe: raise ValueError('Эта проба уже учтена. Запишите следующую.')
        result = accept_trial(self._path.with_name('quality-lab.json'), state['result'], expected, intent,
                              expects_wake, condition, effective_config(self._path))
        self._quality_accepted_probe = marker
        return result

    def quality_metrics(self) -> dict[str, Any]:
        from .voice_quality import all_metrics
        return all_metrics(self._path)

    def test_tts_latency(self) -> dict[str, Any]:
        from .local_voice import _make_speaker
        from .voice_quality import runtime_metric, all_metrics
        start = time.perf_counter()
        config = effective_config(self._path)
        config['speak_responses'] = True
        recorded: list[bool] = []
        def first():
            if not recorded:
                recorded.append(True)
                runtime_metric(self._path, 'tts_first_audio', time.perf_counter() - start)
        config['_tts_first_audio'] = first
        speaker = _make_speaker(config)
        if speaker is None: raise ValueError('Piper недоступен')
        speaker('Проверка голоса Atlas. Аудио остаётся на этом компьютере.')
        return all_metrics(self._path)

    def open_voice_center(self) -> str:
        import webbrowser
        if not webbrowser.open('https://atlas.prism-news.xyz/#voice'): raise RuntimeError('Не удалось открыть Atlas')
        return 'Открыт раздел «Голос». Требуется вход владельца.'

    def open_model_settings(self) -> str:
        from .local_voice import open_desktop_model_settings
        open_desktop_model_settings()
        return 'Открыты настройки моделей Atlas. При необходимости войдите как владелец.'

    def diagnostics(self) -> dict[str, Any]:
        return diagnostics(self._path)

    def get_history(self) -> list[dict[str, Any]]:
        return read_history(self._path)

    def clear_history(self) -> None:
        clear_history(self._path)

    def close_window(self) -> None:
        self._probe.cancel()
        if self._window is not None:
            self._window.destroy()


def open_settings_window(path: Path) -> None:
    if os.name != 'nt':
        raise RuntimeError('Окно настроек голоса доступно только в Windows')
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError('Установите зависимости voice из requirements-voice.lock') from exc
    api = VoiceSettingsApi(path)
    api._window = webview.create_window('Настройки голоса Atlas', html=SETTINGS_HTML, js_api=api,
                                       width=780, height=850, min_size=(620, 620),
                                       background_color='#101419', confirm_close=True)
    webview.start(gui='edgechromium', debug=False, private_mode=True)
