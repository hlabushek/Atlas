"""Local transcript checks that do not require server settings or credentials."""
import os
import re


def private_transcript(text: str) -> bool:
    if re.search(r'(?i)(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
                 r'Bearer\s+[a-z0-9_.-]{16,}|sk-[a-z0-9_-]{12,}|'
                 r'(?:password|пароль|api[_ -]?key|token|токен)\s*(?:[:=]|это|равен)\s*\S+)', text):
        return True
    return any(value and value in text for key, value in os.environ.items()
               if key.startswith('ATLAS_') and any(x in key for x in ('PASSWORD', 'SECRET', 'TOKEN', 'KEY')))


def sensitive_response(text: str) -> bool:
    """Консервативный дополнительный TTS фильтр, не универсальная DLP."""
    return bool(re.search(r'(?i)(?:\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|'
                          r'\b(?:\d[ -]?){13,19}\b|\b(?:диагноз|паспорт|адрес проживания|номер счета|медицинск)\w*\b)', text))
