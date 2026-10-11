"""Owner-transferred browser cookies, encrypted at rest and scoped to one source."""
import json
import os
import re
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from .integration_permissions import scope_fingerprint, utc
from .models import ConnectorSource, now

COOKIE_NAME=re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")

def cipher():
    key=os.environ.get('ATLAS_CONNECTOR_SESSION_KEY','')
    if not key:raise ValueError('Ключ шифрования сессий не настроен')
    try:return Fernet(key.encode('ascii'))
    except (ValueError,UnicodeError) as exc:raise ValueError('Неверный ключ шифрования сессий') from exc

def checked_cookies(cookies):
    if not isinstance(cookies,list) or not 1<=len(cookies)<=30:raise ValueError('Нужно от 1 до 30 cookies')
    seen=set();pairs=[]
    for cookie in cookies:
        if not isinstance(cookie,dict) or set(cookie)-{'name','value','path'} or not {'name','value'}<=set(cookie):
            raise ValueError('Недопустимый формат cookie')
        name,value=cookie['name'],cookie['value']
        path=cookie.get('path','/')
        if not isinstance(path,str) or not path.startswith('/') or len(path)>200 or '\\' in path or any(ord(c)<32 for c in path):
            raise ValueError('Недопустимый путь cookie')
        if not isinstance(name,str) or not COOKIE_NAME.fullmatch(name) or (name,path) in seen:
            raise ValueError('Недопустимое или повторное имя cookie')
        if not isinstance(value,str) or not value or len(value)>4096 or any(ord(c)<33 or ord(c)>126 or c in ';\\' for c in value):
            raise ValueError('Недопустимое значение cookie')
        seen.add((name,path));pairs.append({'name':name,'value':value,'path':path})
    if len(json.dumps(pairs).encode())>16000:raise ValueError('Набор cookies слишком велик')
    return pairs

def store(source:ConnectorSource,cookies,days:int):
    pairs=checked_cookies(cookies)
    source.session_ciphertext=cipher().encrypt(json.dumps(pairs,separators=(',',':')).encode()).decode('ascii')
    source.session_fingerprint=scope_fingerprint(source)
    source.session_generation=str(uuid.uuid4())
    source.session_expires_at=now()+timedelta(days=days)
    source.session_kind='cookies'
    return [p['name'] for p in pairs]

def store_browser(source:ConnectorSource,state:dict,days:int=7):
    """Encrypt only state belonging to the exact source origin; never write plaintext to disk."""
    origin=urlparse(source.origin)
    site=f'{origin.scheme}://{origin.netloc}'
    if not isinstance(state,dict) or set(state)-{'cookies','origins'}:
        raise ValueError('Недопустимое состояние браузера')
    safe_cookies=[]
    for item in state.get('cookies',[]):
        if not isinstance(item,dict) or item.get('domain','').lstrip('.').lower()!=origin.hostname.lower():
            continue
        safe_cookies.append(item)
    if len(safe_cookies)>60:raise ValueError('Слишком много cookies')
    safe_origins=[item for item in state.get('origins',[]) if isinstance(item,dict) and item.get('origin')==site]
    if len(safe_origins)>1:raise ValueError('Недопустимое состояние браузера')
    payload={'cookies':safe_cookies,'origins':safe_origins}
    encoded=json.dumps(payload,separators=(',',':')).encode()
    if len(encoded)>128000:raise ValueError('Состояние браузера слишком велико')
    source.session_ciphertext=cipher().encrypt(encoded).decode('ascii')
    source.session_fingerprint=scope_fingerprint(source)
    source.session_generation=str(uuid.uuid4())
    source.session_expires_at=now()+timedelta(days=days)
    source.session_kind='browser'
    return [str(item.get('name','')) for item in safe_cookies]

def browser_state(source:ConnectorSource):
    if source.session_kind!='browser' or not active(source):raise PermissionError('Сессия браузера недоступна')
    return json.loads(cipher().decrypt(source.session_ciphertext.encode()))

def active(source:ConnectorSource):
    if not (source.enabled and source.session_ciphertext and source.session_generation
            and source.session_fingerprint==scope_fingerprint(source)
            and source.session_expires_at and utc(source.session_expires_at)>now()):return False
    try:
        data=json.loads(cipher().decrypt(source.session_ciphertext.encode()).decode())
        if source.session_kind=='browser':
            if not isinstance(data,dict) or not isinstance(data.get('cookies'),list) or not isinstance(data.get('origins'),list):return False
        else:checked_cookies(data)
    except (InvalidToken,ValueError,UnicodeError,TypeError):return False
    return True

def _pairs(source:ConnectorSource,url:str|None=None):
    if not source.session_ciphertext:return []
    if source.session_kind=='browser':raise PermissionError('Для HTTP-запроса требуется отдельная cookie-сессия')
    if not active(source):raise PermissionError('Сессия источника истекла или отозвана')
    try:pairs=json.loads(cipher().decrypt(source.session_ciphertext.encode()).decode())
    except (InvalidToken,UnicodeError,ValueError) as exc:raise PermissionError('Сессия источника недоступна') from exc
    pairs=checked_cookies(pairs)
    path=urlparse(url or source.origin).path or '/'
    pairs=[p for p in pairs if path==p['path'] or (path.startswith(p['path']) and
        (p['path'].endswith('/') or path[len(p['path']):].startswith('/')))]
    return sorted(pairs,key=lambda p:len(p['path']),reverse=True)

def cookies(source:ConnectorSource,url:str|None=None):
    result={}
    for pair in _pairs(source,url):result.setdefault(pair['name'],pair['value'])
    return result

def headers(source:ConnectorSource,url:str|None=None):
    pairs=_pairs(source,url)
    return {'Cookie':'; '.join(p['name']+'='+p['value'] for p in pairs)} if pairs else {}

def revoke(source:ConnectorSource):
    source.session_ciphertext=None
    source.session_fingerprint=None
    source.session_generation=None
    source.session_expires_at=None
    source.session_kind=None
