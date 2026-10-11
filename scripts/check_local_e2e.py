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
    while web_port == api_port:
        web_port = free_port()
    env.update(ATLAS_DATABASE_URL='sqlite:///' + str(run_dir / 'e2e.db'),
               ATLAS_OWNER_PASSWORD=secrets.token_urlsafe(24), ATLAS_SESSION_SECRET=secrets.token_urlsafe(48),
               ATLAS_MODEL_PROVIDER='demo', ATLAS_COOKIE_SECURE='false',
               ATLAS_INTEGRATION_HOSTS='example.org', ATLAS_CONNECTOR_SESSION_KEY=Fernet.generate_key().decode(),
               PYTHONPATH=str(root / 'backend'), ATLAS_E2E_API_URL=f'http://127.0.0.1:{api_port}',
               ATLAS_E2E_PORT=str(web_port))
    env['ATLAS_E2E_PASSWORD'] = env['ATLAS_OWNER_PASSWORD']
    # Run Vite directly in its own process group; npm/Playwright worker shutdown
    # must not tear down the web service in the middle of a Windows suite.
    env['ATLAS_E2E_URL'] = f'http://127.0.0.1:{web_port}'
    migration = ('from alembic.config import Config;from alembic import command;'
                 f'c=Config({str(root / "backend" / "alembic.ini")!r});'
                 f'c.set_main_option("script_location",{str(root / "backend" / "migrations")!r});'
                 'command.upgrade(c,"head")')
    processes = []
    logs = []
    try:
        subprocess.run([sys.executable, '-c', migration], cwd=run_dir, env=env, check=True)
        npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
        if not npm:
            raise RuntimeError('npm не найден')
        subprocess.run([npm, 'run', 'build'], cwd=root / 'frontend', env=env, check=True)
        for args in (['-m', 'uvicorn', 'atlas.api:app', '--host', '127.0.0.1', '--port', str(api_port), '--no-access-log'],
                     ['-m', 'atlas.worker'], ['-m', 'atlas.scheduler']):
            log = (run_dir / f'process-{len(processes)}.log').open('wb')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, *args], cwd=run_dir, env=env,
                                              stdout=log, stderr=log))
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node.js не найден')
        web_log = (run_dir / 'process-vite.log').open('wb')
        logs.append(web_log)
        web = subprocess.Popen([node, str(root / 'frontend' / 'node_modules' / 'vite' / 'bin' / 'vite.js'),
                                'preview', '--host', '127.0.0.1', '--port', str(web_port), '--strictPort'],
                               cwd=root / 'frontend', env=env, stdout=web_log, stderr=web_log,
                               creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
                               if os.name == 'nt' else 0)
        processes.append(web)
        deadline = time.monotonic() + 30
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError('Тестовый API/worker завершился до E2E')
                try:
                    if (client.get(env['ATLAS_E2E_API_URL'] + '/api/health').status_code == 200
                            and client.get(env['ATLAS_E2E_URL']).status_code == 200):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError('Тестовый API не запустился')
        # The harness owns all service processes; Playwright only runs scenarios.
        result = subprocess.run([npm, 'run', 'test:e2e', '--', '--workers=1', *sys.argv[1:]],
                                cwd=root / 'frontend', env=env)
        if web.poll() is not None:
            raise RuntimeError(f'Тестовый Vite завершился с кодом {web.returncode}; см. process-vite.log')
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
