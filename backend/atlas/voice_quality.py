"""Числовые замеры явных проб и runtime. Не хранит аудио или текст фраз."""
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time

_LOCK = threading.Lock()


def error_rate(expected, actual, characters=False):
    import re
    clean = lambda text: ' '.join(re.findall(r'[a-zа-я0-9]+', text.casefold().replace('ё', 'е')))
    reference = list(clean(expected).replace(' ', '')) if characters else clean(expected).split()
    hypothesis = list(clean(actual).replace(' ', '')) if characters else clean(actual).split()
    if not reference: return None
    previous = list(range(len(hypothesis) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, candidate in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != candidate)))
        previous = current
    return {'errors': previous[-1], 'units': len(reference), 'percent': round(previous[-1] * 100 / len(reference), 1)}


def percentiles(values):
    values = sorted(v for v in values if type(v) in {int, float} and math.isfinite(v) and v >= 0)
    if not values: return {'samples': 0, 'p50': None, 'p95': None}
    return {'samples': len(values), 'p50': values[math.ceil(len(values) * .5) - 1], 'p95': values[math.ceil(len(values) * .95) - 1]}


def read_samples(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 300000: return []
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
        return rows[-256:] if isinstance(rows, list) else []
    except (OSError, ValueError): return []


def append_sample(path, sample):
    with _LOCK:
        rows = read_samples(path)[-255:] + [sample]
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix='.quality-', dir=path.parent)
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as stream: json.dump(rows, stream, ensure_ascii=False, allow_nan=False)
            os.replace(temp, path)
        except Exception:
            if os.path.exists(temp): os.unlink(temp)
            raise


def summary(rows, include_models=True):
    output = {}
    for engine in ('vosk', 'whisper'):
        samples = [r[engine] for r in rows if isinstance(r.get(engine), dict) and r[engine].get('usable')]
        commands = [s for s in samples if s.get('correct') is not None]
        background = [s for s in samples if s.get('false_wake') is not None]
        errors = [s for s in samples if s.get('wer')]
        output[engine] = {'latency_seconds': percentiles([s.get('seconds') for s in samples]),
            'command_accuracy_percent': round(100 * sum(s['correct'] for s in commands) / len(commands), 1) if commands else None,
            'false_wake_percent': round(100 * sum(s['false_wake'] for s in background) / len(background), 1) if background else None,
            'background_samples': len(background), 'missed_commands': sum(s.get('missed', False) for s in samples),
            'wer_percent': round(100 * sum(s['wer']['errors'] for s in errors) / sum(s['wer']['units'] for s in errors), 1) if errors else None,
            'cer_percent': round(100 * sum(s['cer']['errors'] for s in samples if s.get('cer')) / sum(s['cer']['units'] for s in samples if s.get('cer')), 1) if any(s.get('cer') for s in samples) else None}
    output['conditions'] = sorted({r['condition'] for r in rows if 'condition' in r})
    output['trials'] = len(rows)
    if include_models:
        output['by_model'] = {model: summary([r for r in rows if r.get('model') == model], False)
                              for model in sorted({str(r.get('model', 'unknown')) for r in rows})}
    output['note'] = 'ASR latency — обработка общей записи, не задержка от конца живой фразы. False wake — доля отрицательных проб, не событий в час. Малые выборки не подтверждают p95.'
    return output


def resources():
    result = {'cpu_seconds': time.process_time(), 'ram_mb': None, 'gpu': None}
    try:
        import psutil
        result['ram_mb'] = round(psutil.Process().memory_info().rss / 1024**2, 1)
    except ImportError: pass
    executable = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32' / 'nvidia-smi.exe'
    if executable.is_file() and not executable.is_symlink():
        import subprocess
        try:
            response = subprocess.run([str(executable), '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=2, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
            result['gpu'] = [{'utilization_percent': float(line.split(',')[0]), 'memory_mb': float(line.split(',')[1])}
                             for line in response.stdout.splitlines() if line.strip()]
        except (OSError, ValueError, subprocess.SubprocessError): pass
    return result


def accept_trial(path, result, expected, expected_intent, expects_wake, condition, config):
    from .local_voice import _wake_tail, _classify_intent
    from .capabilities import LOCAL
    from .voice_privacy import private_transcript
    if condition not in {'quiet', 'music', 'headphones', 'distance', 'fast', 'names'} or type(expects_wake) is not bool:
        raise ValueError('Некорректная проба')
    if expected_intent not in {'', 'atlas_request', *LOCAL} or private_transcript(expected) or result.get('redacted'):
        raise ValueError('Нельзя сохранить приватную пробу')
    row = {'condition': condition, 'resources': result.get('resources'), 'model': Path(str(config.get('free_speech_model_path', ''))).name}
    _, reference_tail = _wake_tail(expected, config.get('wake_words', ['атлас']), strict=True)
    reference = _classify_intent(reference_tail, config)
    for engine in ('vosk', 'whisper'):
        text = result.get(engine, '')
        wake, tail = _wake_tail(text, config.get('wake_words', ['атлас']), strict=True)
        decision = _classify_intent(tail, config)
        row[engine] = {'usable': engine == 'vosk' or result.get('whisper_status') in {'ok', 'empty'},
            'seconds': result.get(engine + '_seconds'), 'wer': error_rate(expected, text), 'cer': error_rate(expected, text, True),
            'correct': bool(wake and decision.get('intent') == expected_intent
                            and decision.get('target') == reference.get('target')
                            and decision.get('actions') == reference.get('actions')) if expected_intent else None,
            'missed': bool(expects_wake and not wake), 'false_wake': bool(wake) if not expects_wake else None}
    append_sample(path, row)
    return summary(read_samples(path))


def runtime_metric(config_path, kind, seconds):
    if type(seconds) in {int, float} and math.isfinite(seconds) and seconds >= 0:
        append_sample(config_path.with_name('runtime-metrics.json'), {'kind': kind, 'seconds': round(seconds, 4)})


def all_metrics(config_path):
    rows = read_samples(config_path.with_name('runtime-metrics.json'))
    return {'quality_lab': summary(read_samples(config_path.with_name('quality-lab.json'))),
            'runtime_seconds': {kind: percentiles([r.get('seconds') for r in rows if r.get('kind') == kind])
                for kind in ('local_after_speech', 'atlas_first_fragment', 'tts_first_audio', 'interrupt')},
            'resources': [r.get('resources') for r in read_samples(config_path.with_name('quality-lab.json'))[-5:]]}
