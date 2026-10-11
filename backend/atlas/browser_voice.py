"""Ограниченный PCM-канал владельца; только явно настроенный локальный Whisper."""
import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .api import Secure, valid_session
from .config import settings
from .memory import contains_secret
from .voice_recognition import transcribe_pcm
from .voice_settings import _resolve_free_speech_model

router = APIRouter(prefix='/api/voice')
MAX_BYTES = 16000 * 2 * 30


def configured():
    path = settings().browser_voice_model_path
    return bool(path and _resolve_free_speech_model(path))


@router.get('/audio-status')
def audio_status(_: Secure):
    return {'ready': configured(), 'sample_rate': 16000, 'max_phrase_seconds': 30}


@router.websocket('/audio')
async def audio(ws: WebSocket):
    host = ws.headers.get('host', '')
    token = ws.cookies.get('atlas_session')
    if ws.headers.get('origin', '') not in {'http://' + host, 'https://' + host} or not await asyncio.to_thread(valid_session, token):
        await ws.close(code=1008);return
    await ws.accept()
    if not configured():
        await ws.send_json({'type':'error','message':'Локальный Whisper не настроен.'})
        await ws.close(code=1008);return
    data = bytearray()
    collecting = False
    deadline = time.monotonic() + 120
    try:
        await ws.send_json({'type':'ready'})
        while time.monotonic() < deadline:
            if not await asyncio.to_thread(valid_session, token):
                await ws.close(code=1008);return
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=min(5, max(0.01, deadline-time.monotonic())))
            except asyncio.TimeoutError:
                continue  # VAD silence is not a transport failure; check auth again.
            if message['type'] == 'websocket.disconnect':return
            raw = message.get('bytes')
            if raw is not None:
                if not collecting or not raw or len(raw) % 2 or len(raw) > 6400 or len(data) + len(raw) > MAX_BYTES:
                    raise ValueError('Некорректный или слишком длинный PCM-поток')
                data.extend(raw)
                continue
            control = message.get('text', '')
            if len(control) > 100:raise ValueError('Слишком большое управляющее событие')
            event = json.loads(control)
            if not isinstance(event, dict) or set(event) != {'type'}:raise ValueError('Некорректное событие')
            if event['type'] == 'begin' and not collecting:
                collecting = True;data.clear()
            elif event['type'] == 'end' and collecting and len(data) >= 3200:
                collecting = False
                config = {'free_speech_enabled':True, 'sample_rate':16000,
                          'free_speech_model_path':settings().browser_voice_model_path,
                          'free_speech_device':'cpu','free_speech_compute_type':'int8'}
                pcm = bytes(data);data.clear()
                result = await asyncio.to_thread(transcribe_pcm, pcm, config)
                del pcm
                if not await asyncio.to_thread(valid_session, token):
                    await ws.close(code=1008);return
                if result.status == 'unavailable':raise ValueError('Локальный Whisper недоступен')
                if contains_secret(result.text):raise ValueError('Приватный текст не передается')
                await ws.send_json({'type':'transcript','text':result.text})
            else:raise ValueError('Некорректная последовательность аудиособытий')
        await ws.close(code=1000)
    except (ValueError, TypeError):
        await ws.send_json({'type':'error','message':'Аудиопоток отклонен или распознавание недоступно.'})
        await ws.close(code=1008)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        data.clear()
