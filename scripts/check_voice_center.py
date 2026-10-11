"""Read-only production smoke: owner auth/SSE + connected local voice metadata. No LLM or audio."""
import argparse
import json
import time
import httpx
from atlas.config import settings
from atlas.local_voice import _atlas_client
from atlas.voice_settings import config_path, effective_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mute-test', action='store_true', help='Кратко выключить и включить микрофон через центр')
    args = parser.parse_args()
    config = effective_config(config_path(None))
    with httpx.Client(base_url='https://atlas.prism-news.xyz', trust_env=False, timeout=15,
                      headers={'X-Atlas-Client': 'web'}, follow_redirects=False) as owner:
        owner.post('/api/login', json={'password': settings().owner_password}).raise_for_status()
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                response = owner.get('/api/voice/center')
                response.raise_for_status()
                connected = [d for d in response.json() if d['connected']]
                if connected:
                    break
                time.sleep(.5)
            assert len(connected) == 1, 'Локальный слушатель не подключён'
            device = connected[0]
            assert device['state']['state'] in {'listening', 'paused'}
            assert device['state']['microphone']
            assert device['policy']['activation_mode'] == 'wake_word'
            assert device['policy']['follow_up_seconds'] == 0
            with _atlas_client(config) as companion:
                with companion.stream('GET', '/voice-control-events', timeout=10) as response:
                    response.raise_for_status()
                    policy = next(json.loads(line[6:]) for line in response.iter_lines() if line.startswith('data: '))
                assert policy['policy']['activation_mode'] == 'wake_word'
                assert policy['policy']['follow_up_seconds'] == 0
            print('PASS: реальный слушатель, микрофон, mTLS SSE и строгая активация')
            with owner.stream('GET', '/api/voice/events') as stream:
                stream.raise_for_status()
                data = next(json.loads(line[6:]) for line in stream.iter_lines() if line.startswith('data: '))
                assert any(d['device_id'] == device['device_id'] for d in data)
            print('PASS: защищённый owner SSE; текст фраз и аудио не использовались')
            roles = owner.get('/api/model-settings')
            roles.raise_for_status()
            assert roles.json()['effective']['main']['model'] == 'deepseek/deepseek-v4.1-flash'
            print('PASS: выбранная отвечающая DeepSeek сохранена; запросы к модели не выполнялись')
            if args.mute_test:
                assert device['policy']['enabled'] is True, 'Не включаем ранее выключенный владельцем микрофон'
                endpoint = '/api/voice/devices/' + device['device_id']
                try:
                    for kind, state in [('mute', 'paused'), ('unmute', 'listening')]:
                        sent = owner.post(endpoint + '/control', json={'kind': kind})
                        sent.raise_for_status()
                        control_id = sent.json()['id']
                        deadline = time.monotonic() + 12
                        while time.monotonic() < deadline:
                            rows = owner.get('/api/voice/center')
                            rows.raise_for_status()
                            current = next(d for d in rows.json() if d['device_id'] == device['device_id'])
                            result = next((c['status'] for c in current['controls'] if c['id'] == control_id), None)
                            if result == 'COMPLETED' and current['state']['state'] == state:
                                break
                            time.sleep(.2)
                        else:
                            raise RuntimeError('Нет подтверждённого результата ' + kind)
                    print('PASS: реальные mute/unmute выполнены и подтверждены слушателем; исходное состояние восстановлено')
                finally:
                    rows = owner.get('/api/voice/center')
                    rows.raise_for_status()
                    current = next(d for d in rows.json() if d['device_id'] == device['device_id'])
                    if not current['policy']['enabled']:
                        restored = owner.put(endpoint + '/settings', json={
                            'revision': current['revision'], 'policy': {**current['policy'], 'enabled': True}})
                        restored.raise_for_status()
        finally:
            owner.post('/api/logout').raise_for_status()
    assert httpx.get('https://atlas.prism-news.xyz/api/voice/center', trust_env=False).status_code == 401
    print('PASS: anonymous Voice Center закрыт')


if __name__ == '__main__':
    main()
