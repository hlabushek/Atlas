"""Owner-controlled, same-origin Chromium sessions and bounded JavaScript rendering.

No generated scripts, arbitrary selectors, downloads or raw browser storage are exposed.
"""
import base64
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx

from .connector_sessions import active as session_active, browser_state, cookies as source_cookies
from .network import endpoint
from .public_connectors import extract

_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='atlas-browser')
_live={}
_engine=None
_browser=None

def _same_site(url,origin):
    a,b=urlparse(url),urlparse(origin)
    return a.scheme==b.scheme and a.netloc==b.netloc and a.scheme=='https'

def _route(route,origin):
    request=route.request
    if not _same_site(request.url,origin) or request.resource_type in {'image','media','font'}:
        return route.abort()
    return route.continue_()

def _startup():
    global _engine,_browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _engine=sync_playwright().start()
        _browser=_engine.chromium.launch(headless=True)
    return _browser

def _cleanup():
    for key,value in list(_live.items()):
        if time.monotonic()-value['used']>600:
            value['context'].close()
            del _live[key]

def _view(page):
    return {'url':page.url,'title':page.title()[:200],
            'screenshot':'data:image/png;base64,'+base64.b64encode(page.screenshot(type='png',animations='disabled',timeout=10000)).decode()}

def _begin(source_id,origin,state):
    _cleanup()
    if source_id in _live:
        _live[source_id]['context'].close();del _live[source_id]
    if len(_live)>=2:raise ValueError('Одновременно доступны не более двух сеансов входа')
    browser=_startup()
    context=browser.new_context(storage_state=state,accept_downloads=False,service_workers='block',viewport={'width':1200,'height':800})
    context.route('**/*',lambda route:_route(route,origin))
    context.route_web_socket('**/*',lambda route:route.close())
    context.on('page',lambda popup:popup.close() if len(context.pages)>1 else None)
    page=context.new_page()
    page.set_default_timeout(10000)
    try:page.goto(origin,wait_until='domcontentloaded',timeout=20000)
    except Exception:
        context.close();raise
    _live[source_id]={'context':context,'page':page,'origin':origin,'used':time.monotonic()}
    return _view(page)

def _step(source_id,kind,x=None,y=None,value=None,url=None):
    _cleanup()
    item=_live.get(source_id)
    if item is None:raise ValueError('Сеанс входа истёк; откройте его снова')
    page=item['page'];origin=item['origin']
    if kind=='navigate':
        if not _same_site(url,origin):raise ValueError('Переход за пределы сайта запрещён')
        page.goto(url,wait_until='domcontentloaded',timeout=20000)
    elif kind=='click':page.mouse.click(x,y)
    elif kind=='type':page.keyboard.type(value,delay=15)
    elif kind=='key':page.keyboard.press(value)
    elif kind!='refresh':raise ValueError('Недопустимое действие')
    try:page.wait_for_load_state('domcontentloaded',timeout=3000)
    except Exception:pass
    if not _same_site(page.url,origin):raise ValueError('Сайт вышел за разрешённый домен')
    item['used']=time.monotonic()
    return _view(page)

def _finish(source_id):
    item=_live.pop(source_id,None)
    if item is None:raise ValueError('Сеанс входа истёк')
    try:return item['context'].storage_state(indexed_db=True)
    finally:item['context'].close()

def _cancel(source_id):
    item=_live.pop(source_id,None)
    if item:item['context'].close()

def invoke(operation,*args):
    """Keep sync Playwright objects on one dedicated thread."""
    return _executor.submit(operation,*args).result(timeout=40)

def _remote(path,payload=None,method='POST'):
    base=os.environ.get('ATLAS_BROWSER_SERVICE_URL','').rstrip('/')
    token=os.environ.get('ATLAS_BROWSER_SERVICE_TOKEN','')
    if not base or not token:raise ValueError('Изолированный браузер VPS не настроен')
    with httpx.Client(timeout=45,trust_env=False) as client:
        response=client.request(method,base+path,json=payload,headers={'X-Atlas-Browser-Token':token})
        response.raise_for_status()
        return response.json()

def _service_enabled():
    return bool(os.environ.get('ATLAS_BROWSER_SERVICE_URL'))

def begin(source):
    endpoint(source.origin)
    state=browser_state(source) if source.session_kind=='browser' and session_active(source) else None
    if source.session_kind=='cookies' and session_active(source):
        parsed=urlparse(source.origin)
        state={'cookies':[{'name':name,'value':value,'domain':parsed.hostname,'path':'/','secure':True}
                          for name,value in source_cookies(source).items()],'origins':[]}
    if _service_enabled():return _remote('/start',{'source_id':source.id,'origin':source.origin,'state':state})
    if os.environ.get('ATLAS_BROWSER_IN_PROCESS')!='1':raise ValueError('Изолированный браузер VPS не настроен')
    return invoke(_begin,source.id,source.origin,state)

def step(source_id,kind,x=None,y=None,value=None,url=None):
    if _service_enabled():return _remote('/step',{'source_id':source_id,'kind':kind,'x':x,'y':y,'value':value,'url':url})
    return invoke(_step,source_id,kind,x,y,value,url)

def finish(source_id):
    if _service_enabled():return _remote('/finish',{'source_id':source_id})
    return invoke(_finish,source_id)

def cancel(source_id):
    if _service_enabled():return _remote('/cancel',{'source_id':source_id})
    return invoke(_cancel,source_id)

def _render(origin,recipe,state):
    """Render a read-only page with the already approved exact CSS recipe."""
    browser=_startup()
    context=browser.new_context(storage_state=state,accept_downloads=False,service_workers='block')
    try:
        context.route('**/*',lambda route:_route(route,origin) if route.request.method=='GET' else route.abort())
        context.route_web_socket('**/*',lambda route:route.close())
        page=context.new_page()
        page.goto(origin,wait_until='domcontentloaded',timeout=20000)
        page.locator(recipe['rows']).first.wait_for(state='visible',timeout=10000)
        if not _same_site(page.url,origin):raise ValueError('Сайт перенаправил браузер')
        html=page.content().encode('utf-8')
        if len(html)>1_000_000:raise ValueError('Страница превышает 1 МБ')
        return extract(html,origin,recipe)
    finally:context.close()

def fetch(source):
    endpoint(source.origin)
    state=browser_state(source) if source.session_kind=='browser' else None
    if source.session_kind=='cookies':
        parsed=urlparse(source.origin)
        state={'cookies':[{'name':name,'value':value,'domain':parsed.hostname,'path':'/','secure':True}
                          for name,value in source_cookies(source).items()],'origins':[]}
    if _service_enabled():return _remote('/render',{'origin':source.origin,'recipe':source.recipe,'state':state})
    if os.environ.get('ATLAS_BROWSER_IN_PROCESS')!='1':raise ValueError('Изолированный браузер VPS не настроен')
    return invoke(_render,source.origin,source.recipe,state)
