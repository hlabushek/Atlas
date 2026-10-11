"""User-started local ASR comparison with explicit readiness and no audio files."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
import threading
import time

from .voice_privacy import private_transcript


_EVENTS = None
_EVENT_LOCK = threading.Lock()
_PROBE_LOCK = threading.Lock()


def _windows_events():
    global _EVENTS
    if os.name != 'nt':
        return None
    with _EVENT_LOCK:
        if _EVENTS is None:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            for name in ('CreateEventW', 'CreateMutexW'):
                fn = getattr(kernel, name)
                fn.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR] if name == 'CreateEventW' else [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
                fn.restype = wintypes.HANDLE
            kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.OpenMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            for name in ('SetEvent', 'ResetEvent', 'ReleaseMutex', 'CloseHandle'):
                getattr(kernel, name).argtypes = [wintypes.HANDLE]
                getattr(kernel, name).restype = wintypes.BOOL
            request = kernel.CreateEventW(None, True, False, 'Local\\AtlasVoiceProbeRequestV1')
            ack = kernel.CreateEventW(None, True, False, 'Local\\AtlasVoiceProbeAckV1')
            if not request or not ack:
                raise OSError('Не удалось создать канал диагностики микрофона')
            _EVENTS = kernel, request, ack
    return _EVENTS


def diagnostic_requested() -> bool:
    events = _windows_events()
    if not events or events[0].WaitForSingleObject(events[1], 0) != 0:
        return False
    kernel, request, ack = events
    mutex = kernel.OpenMutexW(0x00100001, False, 'Local\\AtlasVoiceProbeMutexV1')
    try:
        if not mutex:
            kernel.ResetEvent(request)
            kernel.ResetEvent(ack)
            return False
        result = kernel.WaitForSingleObject(mutex, 0)
        if result in (0, 0x80):
            # The probe died or finished; don't leave the microphone paused.
            kernel.ReleaseMutex(mutex)
            kernel.ResetEvent(request)
            kernel.ResetEvent(ack)
            return False
        return result == 0x102
    finally:
        if mutex:
            kernel.CloseHandle(mutex)


def acknowledge_diagnostic() -> None:
    events = _windows_events()
    if events:
        events[0].SetEvent(events[2])


@contextmanager
def _exclusive_probe():
    if not _PROBE_LOCK.acquire(blocking=False):
        raise RuntimeError('Проверка уже выполняется')
    events = None
    mutex = None
    try:
        events = _windows_events()
        if events:
            kernel = events[0]
            mutex = kernel.CreateMutexW(None, False, 'Local\\AtlasVoiceProbeMutexV1')
            if not mutex or kernel.WaitForSingleObject(mutex, 0) not in (0, 0x80):
                if mutex:
                    kernel.CloseHandle(mutex)
                    mutex = None
                raise RuntimeError('Проверка уже выполняется в другом окне')
        yield
    finally:
        if mutex:
            events[0].ReleaseMutex(mutex)
            events[0].CloseHandle(mutex)
        _PROBE_LOCK.release()


@contextmanager
def listener_presence():
    events = _windows_events()
    mutex = None
    try:
        if events:
            kernel = events[0]
            mutex = kernel.CreateMutexW(None, False, 'Local\\AtlasVoiceListenerV1')
            if not mutex or kernel.WaitForSingleObject(mutex, 0) not in (0, 0x80):
                if mutex:
                    kernel.CloseHandle(mutex)
                    mutex = None
                raise RuntimeError('Голосовой слушатель уже работает')
        yield
    finally:
        if mutex:
            events[0].ReleaseMutex(mutex)
            events[0].CloseHandle(mutex)


@contextmanager
def _pause_listener(cancel):
    events = _windows_events()
    if not events:
        yield
        return
    kernel, request, ack = events
    kernel.ResetEvent(ack)
    kernel.SetEvent(request)
    tray = kernel.OpenMutexW(0x00100000, False, 'Local\\AtlasVoiceListenerV1')
    if not tray:
        tray = kernel.OpenMutexW(0x00100000, False, 'Local\\AtlasVoiceTrayV2')
    try:
        if tray:
            deadline = time.monotonic() + 8
            while kernel.WaitForSingleObject(ack, 50) != 0:
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise RuntimeError('Голосовой модуль занят. Повторите проверку после его ответа')
        yield
    finally:
        kernel.ResetEvent(request)
        kernel.ResetEvent(ack)
        if tray:
            kernel.CloseHandle(tray)


def capture_pcm(config, seconds, cancel):
    import sounddevice as sd
    rate = int(config.get('sample_rate', 16000))
    device = int(config.get('device_index', -1))
    chunks = []
    with sd.RawInputStream(samplerate=rate, blocksize=rate // 4, dtype='int16', channels=1,
                           device=None if device < 0 else device) as stream:
        for _ in range(seconds * 4):
            if cancel.is_set():
                return b''
            data, overflow = stream.read(rate // 4)
            if overflow:
                raise RuntimeError('Микрофон теряет аудио. Повторите проверку')
            chunks.append(bytes(data))
    return b''.join(chunks)


def compare_pcm(data, config, recognizer):
    from .local_voice import _audio_level, _best_recognized_phrase, _classify_intent, _wake_tail
    from .voice_recognition import transcribe_pcm, word_error_rate
    started = time.perf_counter()
    cpu_started = time.process_time()
    recognizer.AcceptWaveform(data)
    payload = recognizer.FinalResult()
    raw = json.loads(payload)
    alternatives = raw.get('alternatives', [])
    if any(private_transcript(str(candidate.get('text', ''))) for candidate in [raw, *alternatives]
           if isinstance(candidate, dict)):
        return {'redacted': True, 'message': 'Текст скрыт: обнаружены приватные данные'}
    vosk = _best_recognized_phrase(payload, float(config.get('min_confidence', 0.55)),
                                    config, config.get('wake_words', ['атлас']), True)
    vosk_seconds = round(time.perf_counter() - started, 3)
    whisper = transcribe_pcm(data, config)
    result = {'rms_percent': round(_audio_level(data) * 100, 2),
              'vosk': vosk, 'vosk_seconds': vosk_seconds, 'whisper': whisper.text,
              'whisper_seconds': whisper.seconds, 'whisper_status': whisper.status}
    from .voice_quality import resources
    resource = resources()
    elapsed = time.perf_counter() - started
    resource['cpu_percent_one_core'] = round((resource.pop('cpu_seconds') - cpu_started) * 100 / elapsed, 1) if elapsed else None
    result['resources'] = resource
    if private_transcript(vosk) or private_transcript(whisper.text):
        return {'redacted': True, 'message': 'Текст скрыт: обнаружены приватные данные'}
    for name, text in (('vosk', vosk), ('whisper', whisper.text)):
        _, tail = _wake_tail(text, config.get('wake_words', ['атлас']))
        decision = _classify_intent(tail, config)
        result[name + '_route'] = decision['route'] if text else 'empty'
        result[name + '_wer'] = word_error_rate(config.get('_probe_expected', ''), text)
    return result


class RecognitionProbe:
    def __init__(self):
        self._lock = threading.Lock()
        self._start = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._state = {'stage': 'idle', 'message': 'Проверка не запущена'}

    def _set(self, stage, message, **extra):
        with self._lock:
            self._state = {'stage': stage, 'message': message, **extra}

    def state(self):
        with self._lock:
            if self._cancel.is_set() and self._state['stage'] in {'warming', 'ready', 'starting'}:
                return {'stage': 'cancelling', 'message': 'Останавливаю проверку…'}
            return deepcopy(self._state)

    def prepare(self, config, seconds=12):
        if type(seconds) is not int or not 6 <= seconds <= 20:
            raise ValueError('Запись должна длиться от 6 до 20 секунд')
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError('Проверка уже запущена')
            self._start.clear()
            self._cancel.clear()
            self._state = {'stage': 'warming', 'message': 'Подготавливаю модели…'}
            self._thread = threading.Thread(target=self._run, args=(deepcopy(config), seconds), daemon=True)
            self._thread.start()
        return self.state()

    def begin(self):
        with self._lock:
            if self._state['stage'] != 'ready':
                raise ValueError('Дождитесь готовности моделей')
            self._state = {'stage': 'starting', 'message': 'Подключаю микрофон…'}
            self._start.set()
        return self.state()

    def cancel(self):
        self._cancel.set()
        self._start.set()
        return {'stage': 'cancelling', 'message': 'Останавливаю проверку…'}

    def _run(self, config, seconds):
        try:
            with _exclusive_probe():
                from vosk import Model, KaldiRecognizer
                from .voice_recognition import warm_whisper
                recognizer = KaldiRecognizer(Model(str(config['model_path'])), int(config.get('sample_rate', 16000)))
                recognizer.SetWords(True)
                whisper_ready = config.get('free_speech_enabled') is True
                try:
                    warm_whisper(config)
                except Exception:
                    whisper_ready = False
                if self._cancel.is_set():
                    self._set('cancelled', 'Проверка отменена')
                    return
                self._set('ready', 'Модели готовы. Нажмите «Начать запись»' if whisper_ready else
                          'Vosk готов; Whisper недоступен или отключён. Нажмите «Начать запись»', whisper_ready=whisper_ready,
                          duration=seconds)
                if not self._start.wait(90) or self._cancel.is_set():
                    self._set('cancelled', 'Проверка отменена')
                    return
                with _pause_listener(self._cancel):
                    if self._cancel.is_set():
                        self._set('cancelled', 'Проверка отменена')
                        return
                    self._set('recording', 'Говорите — запись идёт', duration=seconds)
                    data = capture_pcm(config, seconds, self._cancel)
                    if self._cancel.is_set():
                        self._set('cancelled', 'Проверка отменена')
                        return
                    self._set('processing', 'Сравниваю Vosk и Whisper…')
                    result = compare_pcm(data, config, recognizer)
                    data = b''
                    if self._cancel.is_set():
                        self._set('cancelled', 'Проверка отменена')
                    else:
                        self._set('completed', 'Проверка завершена. Действия не выполнялись.', result=result)
        except Exception as exc:
            self._set('error', 'Проверка недоступна (' + type(exc).__name__ + '). Проверьте микрофон и модели.')
