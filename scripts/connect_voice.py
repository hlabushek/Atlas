"""Явное подключение существующего mTLS устройства к голосовому разговору Atlas."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes
import httpx

from atlas.config import settings
from atlas.local_voice import _load_config, _make_speaker, _send_to_atlas
from atlas.voice_reply import ReplyController
from atlas.voice_settings import config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Путь к локальному voice.json')
    parser.add_argument('--speak-test', action='store_true', help='Один платный запрос и настоящая озвучка Piper')
    args = parser.parse_args()
    config = _load_config(config_path(args.config))
    companion_path = Path(os.path.expandvars(config['companion_config']))
    companion = json.loads(companion_path.read_text(encoding='utf-8-sig'))
    certificate = x509.load_pem_x509_certificate(Path(companion['cert']).read_bytes())
    if certificate.not_valid_after_utc <= datetime.now(timezone.utc):
        raise RuntimeError('Сертификат устройства истёк')
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    # Owner credentials отправляются только существующему серверу Atlas, не модели.
    with httpx.Client(base_url='https://atlas.prism-news.xyz', trust_env=False,
                      follow_redirects=False, timeout=20, headers={'X-Atlas-Client': 'web'}) as owner:
        owner.post('/api/login', json={'password': settings().owner_password}).raise_for_status()
        try:
            preferences = owner.get('/api/model-settings')
            preferences.raise_for_status()
            main_role = preferences.json()['effective']['main']
            if main_role != {'provider': 'routerai', 'model': 'deepseek/deepseek-v4.1-flash',
                             'ready': True, 'max_output_tokens': main_role['max_output_tokens']}:
                raise RuntimeError('Выбранная DeepSeek не готова; настройки не изменены')
            devices = owner.get('/api/devices')
            devices.raise_for_status()
            matches = [device for device in devices.json() if device['fingerprint'] == fingerprint]
            if len(matches) != 1 or not matches[0]['enabled']:
                raise RuntimeError('Существующее устройство не найдено/не разрешено')
            device = matches[0]
            expiry = datetime.fromisoformat(device['expires_at'])
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                raise RuntimeError('Доступ устройства истёк')
            conversation = device.get('voice_conversation_id')
            if not conversation:
                created = owner.post('/api/conversations', json={'title': 'Голосовой разговор Atlas'})
                created.raise_for_status()
                conversation = created.json()['id']
            saved = owner.put(f"/api/devices/{device['id']}/voice",
                              json={'enabled': True, 'conversation_id': conversation})
            saved.raise_for_status()
            if not saved.json()['voice_enabled'] or saved.json()['voice_conversation_id'] != conversation:
                raise RuntimeError('Голосовой доступ не сохранился')
            print('PASS: существующее устройство привязано к голосовому разговору', flush=True)
            print('Conversation ID: ' + conversation, flush=True)
            if not args.speak_test:
                return
            speaker = _make_speaker(config, threading.Event())
            if speaker is None:
                raise RuntimeError('Локальная озвучка Piper не готова')
            fragments = []
            import sounddevice as sd
            play_audio = sd.play
            audio = {'frames': 0, 'errors': 0}
            def checked_play(samples, **kwargs):
                try:
                    result = play_audio(samples, **kwargs)
                    audio['frames'] += len(samples)
                    return result
                except Exception:
                    audio['errors'] += 1
                    raise
            started = time.monotonic()
            # Spy делегирует настоящему sounddevice.play; аудиовыход не подменён.
            with patch('sounddevice.play', side_effect=checked_play), ReplyController() as replies:
                def receive(reply):
                    def say(fragment):
                        fragments.append(fragment)
                        reply.say(fragment)
                    return _send_to_atlas(
                        'Это проверка голосового подключения. Ответь только двумя предложениями: '
                        '«Голосовой канал подключён. Я готов отвечать на вопросы.»', config, 'chat',
                        say, cancel_event=reply.cancelled, register_client=reply.register_client)
                reply = replies.start(speaker, receive)
                deadline = started + 110
                while not reply.done.wait(1):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Время проверки истекло; повторный запрос не отправлялся')
                elapsed = time.monotonic() - started
                expected = 'Голосовой канал подключён. Я готов отвечать на вопросы.'
                if reply.error:
                    print('Voice receive/play error type: ' + reply.error, flush=True)
                if (reply.error or reply.result != expected or ''.join(fragments).strip() != expected
                        or audio['errors'] or not audio['frames']):
                    raise RuntimeError('Проверка ответа/озвучки не завершилась ожидаемым результатом')
            print(f'PASS: mTLS → production worker → DeepSeek → voice client → Piper/audio; {elapsed:.2f} с', flush=True)
            print('Audio frames submitted: ' + str(audio['frames']), flush=True)
            budget = owner.get('/api/budget')
            budget.raise_for_status()
            print('Ledger total tokens: ' + str(budget.json()['tokens']), flush=True)
        finally:
            owner.post('/api/logout').raise_for_status()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Не печатаем HTTP response, request headers, config или тексты исключений.
        print('FAIL: voice connection: ' + type(error).__name__, flush=True)
        raise SystemExit(1)
