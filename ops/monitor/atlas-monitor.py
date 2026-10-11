#!/usr/bin/env python3
"""Check Atlas from the VPS and notify its owner only on state changes."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE = Path('/var/lib/atlas-monitor/state.json')
ENV = Path('/opt/atlas/app/.env')
CONTAINERS = ('atlas-api-1', 'atlas-worker-1', 'atlas-scheduler-1',
              'atlas-telegram-1', 'atlas-web-1', 'atlas-postgres-1',
              'atlas-redis-1', 'atlas-desktop-gateway-1')


def settings():
    values = {}
    for line in ENV.read_text(encoding='utf-8').splitlines():
        match = re.match(r'^\s*(ATLAS_TELEGRAM_BOT_TOKEN|ATLAS_TELEGRAM_OWNER_ID)=(.*)$', line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"\'')
    return values


def notify(message):
    values = settings()
    token = values.get('ATLAS_TELEGRAM_BOT_TOKEN', '')
    owner = values.get('ATLAS_TELEGRAM_OWNER_ID', '')
    if not token or not re.fullmatch(r'\d{5,20}', owner):
        raise RuntimeError('Telegram alert destination is not configured')
    data = urllib.parse.urlencode({'chat_id': owner, 'text': message}).encode()
    request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=data)
    with urllib.request.urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError('Telegram alert was rejected')


def problems():
    found = []
    try:
        with urllib.request.urlopen('https://atlas.prism-news.xyz/api/health', timeout=8) as response:
            if response.status != 200:
                found.append('HTTPS health: HTTP ' + str(response.status))
    except Exception:
        found.append('HTTPS health недоступен')
    for name in CONTAINERS:
        result = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', name],
                                capture_output=True, text=True, timeout=5)
        if result.returncode or result.stdout.strip() != 'true':
            found.append('Контейнер остановлен: ' + name)
    for timer in ('atlas-backup.timer', 'atlas-restore-drill.timer'):
        result = subprocess.run(['systemctl', 'is-active', '--quiet', timer], timeout=5)
        if result.returncode:
            found.append('Таймер выключен: ' + timer)
    for service in ('atlas-backup.service', 'atlas-restore-drill.service'):
        result = subprocess.run(['systemctl', 'show', service, '--property=Result', '--value'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode or result.stdout.strip() not in {'success', ''}:
            found.append('Последний запуск завершился ошибкой: ' + service)
    archives = list(Path('/opt/atlas/backups/encrypted').glob('atlas-*.dump.gpg'))
    if not archives or time.time() - max(path.stat().st_mtime for path in archives) > 36 * 3600:
        found.append('Нет свежей зашифрованной копии базы')
    disk = shutil.disk_usage('/')
    if disk.free < 2 * 1024**3 or disk.free / disk.total < 0.1:
        found.append('Мало свободного места на VPS')
    return found


def main():
    if '--send-test' in sys.argv:
        notify('Atlas: проверка оповещений VPS работает.')
        print('Test alert sent')
        return
    current = problems()
    print('OK' if not current else 'ALERT: ' + '; '.join(current))
    if '--dry-run' in sys.argv:
        return
    previous = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else []
    if current != previous:
        notify('Atlas VPS: ' + ('обнаружены проблемы: ' + '; '.join(current) if current else 'работа восстановлена.'))
        STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = STATE.with_suffix('.tmp')
        temp.write_text(json.dumps(current, ensure_ascii=False), encoding='utf-8')
        os.chmod(temp, 0o600)
        temp.replace(STATE)


if __name__ == '__main__':
    main()
