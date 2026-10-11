"""Full local E2E on an isolated migrated database, without live credentials."""
from pathlib import Path
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from cryptography.fernet import Fernet


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    root = Path(__file__).resolve().parents[1]
    run_root = root / '.sessions'
    run_root.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='voice-e2e-', dir=run_root)).resolve()
    env = {key: value for key, value in os.environ.items() if not key.startswith('ATLAS_')}
    api_port, web_port = free_port(), free_port()
    env.update(ATLAS_DATABASE_URL='sqlite:///' + str(run_dir / 'e2e.db'),
               ATLAS_OWNER_PASSWORD=secrets.token_urlsafe(24), ATLAS_SESSION_SECRET=secrets.token_urlsafe(48),
               ATLAS_MODEL_PROVIDER='demo', ATLAS_COOKIE_SECURE='false',
               ATLAS_INTEGRATION_HOSTS='example.org', ATLAS_CONNECTOR_SESSION_KEY=Fernet.generate_key().decode(),
               PYTHONPATH=str(root / 'backend'), ATLAS_E2E_API_URL=f'http://127.0.0.1:{api_port}',
               ATLAS_E2E_PORT=str(web_port))
    env['ATLAS_E2E_PASSWORD'] = env['ATLAS_OWNER_PASSWORD']
    # Playwright owns Vite so a recycled test worker cannot terminate a separate helper process.
    env.pop('ATLAS_E2E_URL', None)
    migration = ('from alembic.config import Config;from alembic import command;'
                 f'c=Config({str(root / "backend" / "alembic.ini")!r});'
                 f'c.set_main_option("script_location",{str(root / "backend" / "migrations")!r});'
                 'command.upgrade(c,"head")')
    processes = []
    logs = []
    try:
        subprocess.run([sys.executable, '-c', migration], cwd=run_dir, env=env, check=True)
        for args in (['-m', 'uvicorn', 'atlas.api:app', '--host', '127.0.0.1', '--port', str(api_port), '--no-access-log'],
                     ['-m', 'atlas.worker'], ['-m', 'atlas.scheduler']):
            log = (run_dir / f'process-{len(processes)}.log').open('wb')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, *args], cwd=run_dir, env=env,
                                              stdout=log, stderr=log))
        deadline = time.monotonic() + 30
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError('Тестовый API/worker завершился до E2E')
                try:
                    if client.get(env['ATLAS_E2E_API_URL'] + '/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError('Тестовый API не запустился')
        npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
        if not npm:
            raise RuntimeError('npm не найден')
        # npm scripts own their Vite process; Playwright shuts it down after testing.
        result = subprocess.run([npm, 'run', 'test:e2e', '--', '--workers=1', *sys.argv[1:]],
                                cwd=root / 'frontend', env=env)
        return result.returncode
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        print('Тестовая база сохранена в ' + str(run_dir) + '; рабочие данные не использовались.')


if __name__ == '__main__':
    raise SystemExit(main())
