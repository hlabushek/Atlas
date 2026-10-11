"""Один локальный ответ: сеть и ограниченная очередь TTS вне цикла микрофона."""
import queue
import threading
import time


class ReplyInterrupted(Exception):
    pass


class VoiceReply:
    def __init__(self, speaker=None, speech_lock=None):
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.queue = queue.Queue(maxsize=12)
        self.speaker = speaker
        self.speech_lock = speech_lock or threading.Lock()
        self.client = None
        self.result = None
        self.error = None
        self.generated = threading.Event()
        self.characters = 0
        from .voice_speech import SpeechText
        self.speech = SpeechText()
        self.threads = []

    def register_client(self, client):
        self.client = client
        if self.cancelled.is_set():
            client.close()
            raise ReplyInterrupted()

    def say(self, text):
        if self.cancelled.is_set():
            raise ReplyInterrupted()
        if self.speaker is None:
            return
        for piece in self.speech.feed(text):
            self._enqueue(piece)

    def _enqueue(self, text):
        # Existing safety ceiling, not the mechanism for shorter model generation.
        text = text[:max(0, 3500 - self.characters)]
        self.characters += len(text)
        if not text:
            return
        while not self.cancelled.is_set():
            try:
                self.queue.put(text, timeout=0.1)
                return
            except queue.Full:
                pass
        raise ReplyInterrupted()

    def start(self, receive):
        def generate():
            try:
                self.result = receive(self)
                if not self.cancelled.is_set() and self.speaker is not None:
                    for piece in self.speech.feed('', final=True):
                        self._enqueue(piece)
            except ReplyInterrupted:
                pass
            except Exception as exc:
                if not self.cancelled.is_set():
                    self.error = type(exc).__name__
                    self.cancel()
            finally:
                self.generated.set()
        def play():
            try:
                while not self.cancelled.is_set():
                    try:
                        text = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        if self.generated.is_set():
                            return
                        continue
                    with self.speech_lock:
                        if not self.cancelled.is_set() and self.speaker is not None:
                            self.speaker(text, self.cancelled)
            except Exception as exc:
                self.error = type(exc).__name__
                self.cancel()
            finally:
                self.done.set()
        self.threads = [threading.Thread(target=generate, daemon=True, name='atlas-voice-receive'),
                        threading.Thread(target=play, daemon=True, name='atlas-voice-play')]
        for thread in self.threads:
            thread.start()
        return self

    def cancel(self):
        if not self.cancelled.is_set(): self.cancelled.requested_at = time.monotonic()
        self.cancelled.set()
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    def close(self):
        self.cancel()
        for thread in self.threads:
            thread.join(timeout=0.5)


class ReplyController:
    """Сериализует устройство воспроизведения и ограничивает зависшие сети."""
    def __init__(self):
        self.current = None
        self.retired = []
        self.speech_lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.interrupt()
        for reply in self.retired:
            reply.close()

    def interrupt(self):
        if self.current is not None:
            self.current.cancel()
            self.retired.append(self.current)
            self.current = None

    def start(self, speaker, receive):
        self.interrupt()
        self.retired = [r for r in self.retired if any(t.is_alive() for t in r.threads)]
        if len(self.retired) >= 2:
            raise RuntimeError('Предыдущие соединения ещё закрываются; повторите через несколько секунд')
        self.current = VoiceReply(speaker, self.speech_lock).start(receive)
        return self.current


STOP_PHRASES = {'стоп', 'остановись', 'хватит', 'замолчи', 'останови ответ', 'отмени задачу', 'отмени запрос'}


def interrupt_grammar(wake_words):
    return sorted({wake + ' ' + stop for wake in wake_words for stop in STOP_PHRASES}) + ['[unk]']


def is_voice_interrupt(phrase, wake_words):
    # Exact explicit activation only; fuzzy matches/follow-up cannot stop speech.
    return any(phrase.startswith(wake + ' ') and phrase[len(wake) + 1:] in STOP_PHRASES for wake in wake_words)


def is_voice_cancel(phrase, wake_words):
    return any(phrase == wake + ' ' + command for wake in wake_words for command in ('отмени задачу', 'отмени запрос'))
