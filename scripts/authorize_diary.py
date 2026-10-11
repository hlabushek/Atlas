"""Run manually to authorize a chosen diary; never invoked by the model or scheduler."""
import os,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from atlas.network import endpoint
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

url=endpoint(os.environ['ATLAS_DIARY_URL'])
key=os.environ['ATLAS_DIARY_SESSION_KEY']
dest=Path(os.environ['ATLAS_DIARY_SESSION_PATH']).resolve()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=False)
    context=browser.new_context();page=context.new_page();page.goto(url)
    input('Войдите самостоятельно, включая MFA/CAPTCHA; затем нажмите Enter здесь: ')
    payload=json.dumps(context.storage_state()).encode()
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_bytes(Fernet(key.encode()).encrypt(payload))
    if os.name!='nt':os.chmod(dest,0o600)
    browser.close()
print('Авторизованная сессия сохранена в зашифрованном файле. Не добавляйте её в Git.')
