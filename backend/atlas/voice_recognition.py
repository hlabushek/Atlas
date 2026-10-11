"""Local Whisper lifecycle and quality gate shared by listener and diagnostics."""
from __future__ import annotations

import re
import os
from pathlib import Path
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from .voice_settings import _resolve_free_speech_model


_LOCK = threading.Lock()
_MODEL: Any = None
_KEY: tuple[str, str, str] | None = None
_DLL_HANDLES: list[Any] = []


def _configure_gpu() -> None:
    if os.name != 'nt':
        return
    import ctypes
    base = Path(sys.prefix) / 'Lib' / 'site-packages' / 'nvidia'
    directories = [base / name / 'bin' for name in ('cublas', 'cudnn', 'cuda_runtime', 'cuda_nvrtc')]
    for directory in directories:
        if directory.is_dir() and not directory.is_symlink():
            resolved = str(directory)
            if resolved not in os.environ.get('PATH', '').split(os.pathsep):
                _DLL_HANDLES.append(os.add_dll_directory(resolved))
                os.environ['PATH'] = resolved + os.pathsep + os.environ.get('PATH', '')
    # Loading a model doesn't run the encoder. Validate its libraries up front.
    ctypes.WinDLL('cublas64_12.dll')
    ctypes.WinDLL('cudnn64_9.dll')


@dataclass(frozen=True)
class Transcript:
    text: str = ''
    status: str = 'unavailable'
    seconds: float = 0.0


class PhraseAudio:
    def __init__(self, sample_rate: int, seconds: int = 30):
        self._limit = sample_rate * 2 * seconds
        self._data = bytearray()
        self._truncated = False

    def append(self, data: bytes) -> None:
        if len(self._data) + len(data) > self._limit:
            self._truncated = True
            self._data.clear()
        elif not self._truncated:
            self._data.extend(data)

    def clear(self) -> None:
        self._data.clear()
        self._truncated = False

    def take(self) -> bytes:
        # A truncated recording must never rewrite a complete Vosk transcript.
        result = b'' if self._truncated else bytes(self._data)
        self.clear()
        return result


def word_error_rate(expected: str, actual: str) -> dict | None:
    def words(text):
        return re.findall(r'[a-zа-я0-9]+', text.casefold().replace('ё', 'е'))
    reference, hypothesis = words(expected), words(actual)
    if not reference:
        return None
    previous = list(range(len(hypothesis) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, candidate in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != candidate)))
        previous = current
    return {'errors': previous[-1], 'words': len(reference),
            'percent': round(previous[-1] * 100 / len(reference), 1)}


def prepare_whisper(config: dict) -> Any:
    global _MODEL, _KEY
    path = _resolve_free_speech_model(config.get('free_speech_model_path'))
    if path is None:
        raise ValueError('Локальная модель Whisper не найдена или загружена не полностью')
    key = (str(path), str(config.get('free_speech_device', 'auto')),
           str(config.get('free_speech_compute_type', 'int8')))
    if _KEY != key:
        from faster_whisper import WhisperModel
        device = key[1]
        if device == 'auto':
            import ctranslate2
            device = 'cuda' if ctranslate2.get_cuda_device_count() else 'cpu'
            if device == 'cuda':
                try:
                    _configure_gpu()
                except OSError:
                    device = 'cpu'
        elif device == 'cuda':
            _configure_gpu()
        # Evict the previous model before allocating GPU memory for another.
        _MODEL, _KEY = None, None
        model = WhisperModel(key[0], device=device, compute_type=key[2], local_files_only=True)
        _MODEL, _KEY = model, key
    return _MODEL


def warm_whisper(config: dict) -> None:
    if config.get('free_speech_enabled') is True:
        with _LOCK:
            prepare_whisper(config)


def pcm_audio(data: bytes, sample_rate: int):
    import numpy as np
    data = data[:len(data) - len(data) % 2]
    if sample_rate == 16000:
        return np.frombuffer(data, dtype='<i2').astype(np.float32) / 32768.0
    if sample_rate not in {8000, 32000}:
        raise ValueError('Частота микрофона задана некорректно')
    import av
    if not data:
        return np.empty(0, dtype=np.float32)
    frame = av.AudioFrame.from_ndarray(np.frombuffer(data, dtype='<i2').reshape(1, -1),
                                      format='s16', layout='mono')
    frame.sample_rate = sample_rate
    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    frames = resampler.resample(frame) + resampler.resample(None)
    if not frames:
        return np.empty(0, dtype=np.float32)
    return np.concatenate([frame.to_ndarray().reshape(-1) for frame in frames]).astype(np.float32) / 32768.0


def usable_segments(segments) -> str:
    accepted = []
    for segment in segments:
        if (getattr(segment, 'no_speech_prob', 0.0) > 0.65
                or getattr(segment, 'avg_logprob', 0.0) < -1.0
                or getattr(segment, 'compression_ratio', 0.0) > 2.4):
            # Don't build a partial command by silently dropping poor segments.
            return ''
        text = re.sub(r'\s+', ' ', segment.text.replace('\x00', ' ')).strip()
        if text:
            accepted.append(text)
    return ' '.join(accepted)[:16000]


def transcribe_pcm(data: bytes, config: dict) -> Transcript:
    global _MODEL, _KEY
    if config.get('free_speech_enabled') is not True or not data:
        return Transcript()
    started = time.perf_counter()
    try:
        audio = pcm_audio(data, int(config.get('sample_rate', 16000)))
        if not audio.size:
            return Transcript(status='empty')
        with _LOCK:
            model = prepare_whisper(config)
            segments, _ = model.transcribe(
                audio, language='ru', beam_size=5, best_of=5,
                condition_on_previous_text=False, vad_filter=True,
            )
            text = usable_segments(segments)
        return Transcript(text, 'ok' if text else 'empty', round(time.perf_counter() - started, 3))
    except Exception:
        # Provider paths and audio-derived exception strings are private.
        with _LOCK:
            _MODEL, _KEY = None, None
        return Transcript(status='unavailable', seconds=round(time.perf_counter() - started, 3))
