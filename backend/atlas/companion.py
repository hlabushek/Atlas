"""Outbound-only companion. Fixed operations, no shell and no inbound listener."""
import json,ssl,time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from .companion_fs import execute,WriteOutcomeUnknown

def tick(config_path):
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    url=config['server'];parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('HTTPS endpoint required')
    context=ssl.create_default_context(cafile=config['ca'])
    context.load_cert_chain(config['cert'],config['key'])
    with httpx.Client(base_url=url,verify=context,timeout=10,follow_redirects=False,trust_env=False) as client:
        response=client.get('/next');response.raise_for_status();job=response.json()
        if not job:return False
        identity={'id':job['id'],'token':job['token']}
        permit=client.post('/permit',json=identity);permit.raise_for_status()
        if not permit.json()['allowed']:return False
        try:result=execute(json.loads(Path(config_path).read_text(encoding='utf-8-sig')),job['arguments']);payload={**identity,'result':result}
        except WriteOutcomeUnknown:payload={**identity,'error':'Результат действия на устройстве требует ручной проверки','result':{'ambiguous':True}}
        except Exception:payload={**identity,'error':'Локальная проверка или чтение/запись не выполнены'}
        # Retry ACK of this result only; never execute the filesystem command twice.
        for attempt in range(2):
            try:
                reply=client.post('/result',json=payload);reply.raise_for_status();return True
            except httpx.TransportError:
                if attempt:raise
        return False

def main():
    import argparse
    import os
    parser=argparse.ArgumentParser(description='Atlas Companion: исходящее mTLS подключение, только разрешённые операции')
    parser.add_argument('config',help='Путь к локальному JSON config вне Git')
    args=parser.parse_args()
    if not Path(args.config).is_file():
        try:
            (Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Atlas'/'Companion'/'last-error.txt').write_text('Конфигурация не найдена: '+args.config+'\n',encoding='utf-8')
        except OSError:pass
        parser.error('Файл конфигурации Companion не найден. Укажите полный путь к companion.json')
    while True:
        try:
            if not tick(args.config):time.sleep(2)
        except FileNotFoundError as exc:
            # Local-only diagnostic: path of the missing file, never file contents or credentials.
            try:
                (Path(args.config).parent/'last-error.txt').write_text('Отсутствует файл: '+str(exc.filename or 'неизвестный')+'\n',encoding='utf-8')
            except OSError:pass
            print('Companion: отсутствует локальный файл конфигурации или сертификата; проверьте путь в companion.json',flush=True)
            raise SystemExit(2)
        except Exception as exc:print('Companion:',type(exc).__name__,flush=True);time.sleep(3)

if __name__=='__main__':main()
