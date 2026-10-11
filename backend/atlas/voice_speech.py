"""Потоковая подготовка текста к произношению, не суммаризация и не ASR."""
import html
import re


def plain_speech(text):
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\[[^\]]*\]', r'\1', text)
    text = re.sub(r'\[\^?\d+\]', '', text)
    text = re.sub(r'https?://[^\s<>]+|www\.[^\s<>]+', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'^\s*(?:#{1,6}\s*|>\s*|[-+*•]\s+|\d+[.)]\s+)', '', text)
    if re.fullmatch(r'\s*[-_*|: ]+\s*', text): return ''
    if '|' in text: return ''  # Don't read Markdown tables as unrelated numbers.
    text = re.sub(r'_([^_\n]+)_', r'\1', text)
    text = text.replace('**', '').replace('__', '').replace('*', '').replace('_', ' ')
    text = text.replace('`', '').replace('~', '').replace('\\', ' ').replace('/', ' ')
    return re.sub(r'\s+', ' ', text).strip()


class SpeechText:
    def __init__(self):
        self.buffer = ''
        self.fence = None

    def feed(self, text, final=False):
        self.buffer += text
        if len(self.buffer) > 50000: raise ValueError('Слишком большой фрагмент речи')
        output = []
        while self.buffer:
            boundary = None
            # Fence content is line-oriented, not sentence-oriented.
            pattern = r'\n' if self.fence or self.buffer.lstrip().startswith(('```', '~~~')) else r'\n|[.!?](?=\s|$)'
            for match in re.finditer(pattern, self.buffer):
                candidate = self.buffer[:match.end()]
                if not final and match.end() == len(self.buffer) and re.search(r'\d\.$|(?:https?://|www\.)\S*[.!?]$', candidate):
                    continue
                if re.fullmatch(r'\s*\d+[.)]\s*', candidate): continue
                if not self.fence and (candidate.count('[') > candidate.count(']') or re.search(r'\]\([^)]*$', candidate)):
                    continue  # A link may straddle multiple provider fragments.
                boundary = match.end()
                break
            if boundary is None:
                if not final: break
                boundary = len(self.buffer)
            unit, self.buffer = self.buffer[:boundary], self.buffer[boundary:]
            marker = re.match(r'^\s*(`{3,}|~{3,})', unit)
            if marker:
                token = marker[1][0]
                if self.fence == token: self.fence = None
                elif self.fence is None: self.fence = token
                continue
            if self.fence: continue
            if re.match(r'^\s*\[[^]]+\]:\s*\S+', unit): continue
            clean = plain_speech(unit)
            from .voice_privacy import private_transcript
            if private_transcript(unit) or private_transcript(clean):
                raise ValueError('Приватные данные не озвучиваются')
            if clean: output.append(clean)
        return output
