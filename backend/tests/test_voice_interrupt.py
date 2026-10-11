"""Цикл слушателя: фоновые сеть/TTS не разрешают команды из собственного голоса."""
import json
import os
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from atlas import local_voice, voice_probe
from atlas.voice_reply import ReplyInterrupted


@pytest.mark.skipif(os.name != 'nt', reason='Windows listener')
@pytest.mark.parametrize('partial', [False, True])
def test_listener_keeps_control_alive_and_never_executes_commands_during_reply(tmp_path, monkeypatch, partial):
    model = tmp_path / 'model'
    model.mkdir()
    path = tmp_path / 'voice.json'
    path.write_text(json.dumps({'enabled': True, 'model_path': str(model), 'speak_responses': True,
                               'show_command_pill': False, 'free_speech_enabled': False,
                               'voice_interrupt_enabled': True}), encoding='utf-8')
    stop, sent, playing, recognized, cancelled = [threading.Event() for _ in range(5)]
    actions, requests = [], []
    ready_for_control, ready_after_stop = threading.Event(), threading.Event()
    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
            self.sequence = 0
        def send(self):
            self.sequence += 1
            self.callback(self.sequence.to_bytes(2, 'little') * 4000, 4000, None, None)
        def __enter__(self):
            self.send()
            def produce():
                if not sent.wait(2) or not playing.wait(2): return
                if not ready_for_control.wait(2): return
                self.send()  # Must be ignored, even though it includes wake word.
                if not recognized.wait(2): return
                self.send()  # Explicit voice interruption.
                if not cancelled.wait(2): return
                if not finished_speech.wait(2): return
                if not ready_after_stop.wait(2): return
                self.send()  # New allowlisted action after interruption.
            self.thread = threading.Thread(target=produce, daemon=True)
            self.thread.start()
            return self
        def __exit__(self, *_): self.thread.join(timeout=1)
        def stop(self): pass
        def start(self): pass
    finished_speech = threading.Event()
    texts = ['атлас расскажи о проекте', 'атлас открой сайт', 'атлас стоп', 'атлас сделай громче']
    class Recognizer:
        def __init__(self, *_): self.index = -1
        def AcceptWaveform(self, _):
            self.index = int.from_bytes(_[:2], 'little') - 1
            return not (partial and self.index == 2)
        def Result(self):
            if self.index == 1: recognized.set()
            return json.dumps({'text': texts[self.index], 'result': [{'conf': 0.99}]})
        def PartialResult(self): return '{"partial":"атлас стоп"}'
        def Reset(self): pass
    def speaker(text, cancel):
        playing.set()
        assert cancel.wait(3)
        finished_speech.set()
    def send(text, cfg, mode, say, on_status, cancel_event, register_client):
        requests.append(text)
        say('Первое предложение.')
        sent.set()
        assert cancel_event.wait(3)
        cancelled.set()
        raise ReplyInterrupted()
    def action(value, cfg):
        actions.append(value)
        stop.set()
        return 'Готово'
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=Stream))
    monkeypatch.setitem(sys.modules, 'vosk', SimpleNamespace(Model=lambda _: object(), KaldiRecognizer=Recognizer))
    monkeypatch.setattr(voice_probe, 'listener_presence', lambda: nullcontext())
    monkeypatch.setattr(voice_probe, 'diagnostic_requested', lambda: False)
    monkeypatch.setattr(local_voice, '_ptt_pressed', lambda _: False)
    monkeypatch.setattr(local_voice, '_make_speaker', lambda *_: speaker)
    monkeypatch.setattr(local_voice, '_warm_second_stage', lambda *_: None)
    monkeypatch.setattr(local_voice, '_send_to_atlas', send)
    monkeypatch.setattr(local_voice, 'record_history', lambda *_: None)
    monkeypatch.setattr(local_voice, '_run_action', action)
    timer = threading.Timer(5, stop.set)
    timer.start()
    def status(state):
        if state == 'processing' and sent.is_set(): ready_for_control.set()
        if state == 'listening' and cancelled.is_set(): ready_after_stop.set()
    try:
        local_voice.listen(str(path), stop_event=stop, status_callback=status)
    finally:
        timer.cancel()
    assert requests == ['расскажи о проекте']
    assert actions == [{'type': 'volume_delta', 'percent': 10}]
    assert cancelled.is_set() and finished_speech.is_set()
