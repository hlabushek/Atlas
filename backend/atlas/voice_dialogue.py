"""Bounded local dialogue state; no server approvals or arbitrary actions."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass


CONFIRM = {'да', 'подтверждаю', 'подтверждаю действие'}
CANCEL = {'нет', 'отмена', 'отмени', 'не надо', 'отмени действие'}
END = {'закончи разговор', 'конец разговора', 'больше не слушай', 'до свидания'}


@dataclass
class PendingAction:
    command: str
    actions: list[dict]
    policy: dict
    expires_at: float


class VoiceDialogue:
    def __init__(self) -> None:
        self.active_until = 0.0
        self.pending: PendingAction | None = None
        self.clarification = None

    def reset(self) -> None:
        self.active_until = 0.0
        self.pending = None
        self.clarification = None

    def active(self, now: float) -> bool:
        self.expire(now)
        return now < self.active_until or self.pending is not None

    def accepts_unaddressed(self, phrase: str, config: dict, now: float, started_in_window: bool = False) -> bool:
        self.expire(now)
        if self.pending is not None and phrase in CONFIRM | CANCEL | END:
            return True
        return config.get('activation_mode') == 'wake_word_followup' and (
            now < self.active_until or started_in_window)

    def expire(self, now: float) -> bool:
        if self.pending is not None and now >= self.pending.expires_at:
            self.pending = None
            return True
        return False

    def extend(self, now: float, seconds: int) -> None:
        self.active_until = now + seconds if seconds else 0.0

    @staticmethod
    def policy(config: dict) -> dict:
        # Bind a confirmation to the exact local permissions and definitions.
        return deepcopy({key: config.get(key) for key in
                         ('sites', 'applications', 'scenarios', 'confirm_local_actions', 'test_mode', 'local_grants', 'local_grant_expires_at')})

    def propose(self, command: str, actions: list[dict], config: dict, now: float) -> str:
        self.pending = PendingAction(command, deepcopy(actions), self.policy(config),
                                     now + int(config.get('confirmation_timeout_seconds', 12)))
        return 'Скажите «подтверждаю» или «отмена»'

    def resolve(self, phrase: str, config: dict, now: float) -> tuple[str, PendingAction | None]:
        expired = self.expire(now)
        if phrase == 'это не то' and self.pending is not None:
            self.reset()
            return 'cancel', None
        if phrase in END:
            self.reset()
            return 'end', None
        if phrase in CANCEL:
            self.pending = None
            self.active_until = 0.0
            return 'cancel', None
        if phrase in CONFIRM:
            pending, self.pending = self.pending, None
            if pending is None or expired:
                return 'missing', None
            if pending.policy != self.policy(config):
                return 'changed', None
            return 'confirmed', pending
        # A new request replaces the pending one, never grants it authority.
        self.pending = None
        return 'request', None

    def ask(self, options, config, now):
        self.clarification = (deepcopy(options[:5]), self.policy(config), now + 12)
        return 'Что выбрать: ' + ' или '.join(option['name'] for option in options[:5]) + '? Назовите цель с обращением «Атлас».'

    def resolve_choice(self, phrase, config, now):
        if self.clarification is None: return None
        options, policy, expires = self.clarification
        self.clarification = None
        if now >= expires or policy != self.policy(config): return None
        from .local_voice import normalize
        matches = [option for option in options if phrase in {normalize(option['name']), normalize(option['name']).split(' ', 1)[-1]}]
        if len(matches) == 1: return matches[0]['actions']
        return None
