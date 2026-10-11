"""Короткий видимый контекст. Не выдаёт grants и не закрывает чужие окна."""
from collections import deque
import time
from .voice_privacy import private_transcript


class VoiceSession:
    def __init__(self):
        self.turns = deque(maxlen=5)
        self.last_actions = []
        self.undo = None
        self.updated = 0.0

    def reset(self):
        self.turns.clear()
        self.last_actions = []
        self.undo = None
        self.updated = 0.0

    def expire(self, config):
        seconds = int(config.get('session_context_seconds', 60))
        if not seconds or time.monotonic() - self.updated >= seconds: self.reset()

    def remember(self, role, text, actions=None, undo=None):
        if private_transcript(text): return
        self.turns.append({'role': role, 'content': text[:2000]})
        self.updated = time.monotonic()
        if actions is not None:
            self.last_actions, self.undo = list(actions), undo

    def resolve(self, text, config):
        self.expire(config)
        from .local_voice import _spoken_integer
        if text in {'это не то', 'верни как было', 'отмени последнее действие'}:
            return self.undo if self.undo else 'Нельзя автоматически откатить последнее действие. Окна, сайты и выполненные серверные действия не закрываются.'
        if text.startswith('еще на ') and self.last_actions:
            number = _spoken_integer(text[7:])
            last = self.last_actions[-1]
            if number is not None and 1 <= number <= 30 and last.get('type') == 'volume_delta':
                return [{'type': 'volume_delta', 'percent': number if last['percent'] > 0 else -number}]
        return None


def command_grammar(config):
    """Малый словарь — вспомогательная гипотеза, не источник обращения/полномочий."""
    phrases = {'сделай громче', 'сделай тише', 'поставь на паузу', 'продолжи музыку', 'следующий трек',
               'предыдущий трек', 'выключи звук', 'верни звук', 'который час', 'какая сегодня дата',
               'подтверждаю', 'отмена', 'режим задач', 'режим разговора', 'отмени задачу'}
    for site in config.get('sites', {}): phrases.add('открой ' + site)
    for app in config.get('applications', {}): phrases.add('открой ' + app)
    for scenario in config.get('scenarios', []):
        if scenario.get('enabled', True): phrases.update(scenario.get('phrases', []))
    wakes = config.get('wake_words', ['атлас'])
    return sorted(phrases | {wake + ' ' + phrase for wake in wakes for phrase in phrases})[:500] + ['[unk]']
