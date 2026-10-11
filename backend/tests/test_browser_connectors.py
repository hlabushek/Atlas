"""Browser mode never exposes stored state and revokes old permissions."""
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from atlas.browser_service import app as browser_app

from atlas.api import app
from atlas.config import settings
from atlas.connector_sessions import active, browser_state
from atlas.db import sessions
from atlas.models import Audit, ConnectorSource
from atlas import browser_connectors, public_connectors

RECIPE={'rows':'article.card','key':'id','fields':{'id':'.id','title':'h2'}}

def make_source(client,monkeypatch):
    monkeypatch.setenv('ATLAS_INTEGRATION_HOSTS','example.org')
    monkeypatch.setenv('ATLAS_CONNECTOR_SESSION_KEY',Fernet.generate_key().decode())
    settings.cache_clear()
    result=client.post('/api/connectors',json={'id':'browser-source','name':'Источник',
        'origin':'https://example.org/private','recipe':RECIPE})
    assert result.status_code==200,result.text

def test_browser_login_encrypted_and_old_schedule_revoked(client,monkeypatch):
    make_source(client,monkeypatch)
    assert client.put('/api/connectors/browser-source/fetch-mode',json={'mode':'browser'}).status_code==200
    state={'cookies':[{'name':'sid','value':'private-token','domain':'example.org','path':'/','secure':True},
                      {'name':'other','value':'do-not-save','domain':'other.org','path':'/'}],
           'origins':[{'origin':'https://example.org','localStorage':[{'name':'auth','value':'local-secret'}]},
                      {'origin':'https://other.org','localStorage':[{'name':'x','value':'do-not-save'}]}]}
    monkeypatch.setattr(browser_connectors,'begin',lambda _source:{'url':'https://example.org/private','title':'Вход','screenshot':'data:image/png;base64,AAAA'})
    monkeypatch.setattr(browser_connectors,'step',lambda *args:{'url':'https://example.org/private','title':'Вход','screenshot':'data:image/png;base64,AAAA'})
    monkeypatch.setattr(browser_connectors,'finish',lambda _source_id:state)
    assert client.post('/api/connectors/browser-source/browser/start').status_code==200
    step=client.post('/api/connectors/browser-source/browser/step',json={'kind':'type','value':'typed-password'})
    assert step.status_code==200
    saved=client.post('/api/connectors/browser-source/browser/save')
    assert saved.status_code==200,saved.text
    with sessions()() as session:
        source=session.get(ConnectorSource,'browser-source')
        assert active(source) and source.session_kind=='browser'
        assert 'private-token' not in source.session_ciphertext
        restored=browser_state(source)
        assert restored['cookies'][0]['value']=='private-token'
        assert len(restored['cookies'])==1 and len(restored['origins'])==1
        assert 'typed-password' not in str([row.details for row in session.query(Audit).all()])
        assert 'local-secret' not in str([row.details for row in session.query(Audit).all()])
    listed=client.get('/api/connectors').json()[0]
    assert listed['fetch_mode']=='browser' and listed['session_kind']=='browser'
    assert 'private-token' not in str(listed) and 'local-secret' not in str(listed)
    assert client.delete('/api/connectors/browser-source/session').status_code==200
    assert client.get('/api/connectors').json()[0]['session_active'] is False

def test_browser_fetch_uses_same_recipe_and_requires_owner(client,monkeypatch):
    make_source(client,monkeypatch)
    assert client.put('/api/connectors/browser-source/fetch-mode',json={'mode':'browser'}).status_code==200
    monkeypatch.setattr(browser_connectors,'fetch',lambda source:public_connectors.extract(
        b'<article class="card"><b class="id">1</b><h2>JS result</h2></article>',source.origin,source.recipe))
    with sessions()() as session:
        records=public_connectors.fetch_public(session.get(ConnectorSource,'browser-source'))
    assert records[0]['title']=='JS result'
    with TestClient(app) as anonymous:
        assert anonymous.post('/api/connectors/browser-source/browser/start').status_code==401
        assert anonymous.put('/api/connectors/browser-source/fetch-mode',json={'mode':'http'}).status_code==401

def test_browser_route_rejects_cross_origin_and_non_get_read():
    class Request:
        def __init__(self,url,method='GET'):
            self.url=url;self.method=method;self.resource_type='document'
    class Route:
        def __init__(self,url):self.request=Request(url);self.result=None
        def abort(self):self.result='blocked'
        def continue_(self):self.result='allowed'
    allowed=Route('https://example.org/private')
    blocked=Route('https://other.org/private')
    browser_connectors._route(allowed,'https://example.org/private')
    browser_connectors._route(blocked,'https://example.org/private')
    assert allowed.result=='allowed' and blocked.result=='blocked'

def test_render_reuses_existing_browser_runtime(monkeypatch):
    events=[]

    class Locator:
        first=None
        def wait_for(self,**_kwargs):events.append('wait')

    class Page:
        url='https://example.org/private'
        def goto(self,*_args,**_kwargs):events.append('goto')
        def locator(self,_selector):
            value=Locator()
            value.first=value
            return value
        def content(self):return '<article class="card"><b class="id">1</b><h2>JS result</h2></article>'

    class Context:
        def route(self,*_args):pass
        def route_web_socket(self,*_args):pass
        def new_page(self):return Page()
        def close(self):events.append('close')

    class Browser:
        def new_context(self,**_kwargs):
            events.append('context')
            return Context()

    monkeypatch.setattr(browser_connectors,'_startup',lambda:Browser())
    records=browser_connectors._render('https://example.org/private',RECIPE,None)
    assert records[0]['title']=='JS result'
    assert events==['context','goto','wait','close']

def test_isolated_browser_service_requires_token_and_hides_step_values(monkeypatch):
    monkeypatch.setenv('ATLAS_BROWSER_SERVICE_TOKEN','separate-test-token-with-32-characters')
    monkeypatch.setattr(browser_connectors,'invoke',lambda operation,*args:{'received':operation.__name__})
    with TestClient(browser_app) as service:
        assert service.post('/start',json={'source_id':'test-source','origin':'https://example.org/'}).status_code==401
        headers={'X-Atlas-Browser-Token':'separate-test-token-with-32-characters'}
        response=service.post('/start',headers=headers,json={'source_id':'test-source','origin':'https://example.org/'})
        assert response.status_code==200 and response.json()=={'received':'_begin'}
        assert service.post('/start',headers=headers,json={'source_id':'test-source','origin':'https://127.0.0.1/'}).status_code==422
        assert service.post('/render',headers=headers,json={'origin':'https://example.org/',
            'recipe':RECIPE,'state':{'x':'a'*260000}}).status_code==413
