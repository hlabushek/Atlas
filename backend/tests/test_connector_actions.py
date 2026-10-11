"""Owner-scoped browser session and verified server actions."""
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from atlas import connector_actions
from atlas import public_connectors
from atlas.api import app
from atlas.config import settings
from atlas.connector_sessions import active, headers
from atlas.db import sessions
from atlas.models import Audit, ConnectorAction, ConnectorActionRun, ConnectorSource, now

def source(client,monkeypatch):
    monkeypatch.setenv('ATLAS_INTEGRATION_HOSTS','example.org')
    monkeypatch.setenv('ATLAS_CONNECTOR_SESSION_KEY',Fernet.generate_key().decode())
    settings.cache_clear()
    recipe={'rows':'article.card','key':'id','fields':{'id':'.id','title':'h2'}}
    result=client.post('/api/connectors',json={'id':'site-source','name':'Источник','origin':'https://example.org/tasks','recipe':recipe})
    assert result.status_code==200,result.text

def transfer(client):
    return client.put('/api/connectors/site-source/session',json={'cookies':[{'name':'sid','value':'example-session-value'}],'days':7})

def action(client,method='POST',interval=None):
    result=client.post('/api/connector-actions',json={'connector_id':'site-source','name':'Установить статус',
        'method':method,'target_url':'https://example.org/api/status','body':{'status':'done'},
        'verification_url':'https://example.org/api/status','verification_field':'status',
        'expected_value':'done','interval_minutes':interval})
    assert result.status_code==200,result.text
    return result.json()

def test_session_is_encrypted_scoped_and_revocable(client,monkeypatch):
    source(client,monkeypatch)
    assert transfer(client).status_code==200
    listing=client.get('/api/connectors').json()[0]
    assert listing['session_active'] is True
    assert 'example-session-value' not in str(listing)
    with sessions()() as session:
        row=session.get(ConnectorSource,'site-source')
        assert active(row)
        assert 'example-session-value' not in row.session_ciphertext
        assert headers(row)['Cookie']=='sid=example-session-value'
        assert all('example-session-value' not in str(a.details) for a in session.query(Audit).all())
    assert client.delete('/api/connectors/site-source/session').status_code==200
    assert client.get('/api/connectors').json()[0]['session_active'] is False

def test_session_cannot_cross_recipe_scope_and_requires_key(client,monkeypatch):
    source(client,monkeypatch)
    assert transfer(client).status_code==200
    expanded={'rows':'article.card','key':'id','fields':{'id':'.id','title':'h2','extra':'p.extra'}}
    assert client.put('/api/connectors/site-source/recipe',json={'recipe':expanded}).status_code==200
    assert client.get('/api/connectors').json()[0]['session_active'] is False
    monkeypatch.delenv('ATLAS_CONNECTOR_SESSION_KEY')
    response=transfer(client)
    assert response.status_code==422 and 'example-session-value' not in response.text

def test_vps_fetch_uses_only_transferred_session_for_exact_site(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    calls=[];original=httpx.Client
    def handler(request):
        calls.append((str(request.url),request.headers.get('cookie')))
        return httpx.Response(200,headers={'content-type':'text/html'},
            text='<article class="card"><span class="id">1</span><h2>Закрытая запись</h2></article>')
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(public_connectors.httpx,'Client',lambda **kwargs:original(transport=transport,**kwargs))
    with sessions()() as session:
        rows=public_connectors.fetch_public(session.get(ConnectorSource,'site-source'))
    assert rows[0]['title']=='Закрытая запись'
    assert calls==[('https://example.org/tasks','sid=example-session-value')]

def test_expired_session_never_falls_back_to_public_fetch(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    with sessions()() as session:
        row=session.get(ConnectorSource,'site-source')
        row.session_expires_at=now()-timedelta(seconds=1)
        session.commit()
    calls=[];monkeypatch.setattr(public_connectors.httpx,'Client',lambda **kwargs:calls.append(kwargs))
    with sessions()() as session:
        try:public_connectors.fetch_public(session.get(ConnectorSource,'site-source'))
        except PermissionError:pass
        else:assert False,'Просроченная сессия не должна использоваться'
    assert calls==[]
    assert connector_actions.tick()==0
    assert client.get('/api/connectors').json()[0]['session_present'] is False

def test_action_requires_session_and_stays_on_source_origin(client,monkeypatch):
    source(client,monkeypatch)
    payload={'connector_id':'site-source','name':'Запись','method':'POST','target_url':'https://example.org/api/status',
        'body':{'status':'done'},'verification_url':'https://example.org/api/status','verification_field':'status','expected_value':'done'}
    assert client.post('/api/connector-actions',json=payload).status_code==409
    assert transfer(client).status_code==200
    assert client.post('/api/connector-actions',json={**payload,'target_url':'https://other.example/api/status'}).status_code==422
    assert client.post('/api/connector-actions',json={**payload,'body':{'password':'abc'}}).status_code==422

def test_new_owner_routes_reject_anonymous_requests(client):
    with TestClient(app) as anonymous:
        assert anonymous.get('/api/connector-actions').status_code==401
        assert anonymous.put('/api/connectors/site-source/session',headers={'X-Atlas-Client':'web'},json={
            'cookies':[{'name':'sid','value':'abc'}]}).status_code==401

def test_manual_action_waits_for_approval_and_verifies_actual_result(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    config=action(client)
    calls=[];original=httpx.Client
    def handler(request):
        calls.append((request.method,str(request.url),request.headers.get('cookie')))
        if request.method=='POST':return httpx.Response(204)
        return httpx.Response(200,json={'status':'done'})
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(connector_actions.httpx,'Client',lambda **kwargs:original(transport=transport,**kwargs))
    run=client.post('/api/connector-actions/'+config['id']+'/run').json()
    assert run['status']=='WAITING_APPROVAL' and connector_actions.tick()==0 and calls==[]
    assert client.post('/api/connector-actions/runs/'+run['id']+'/decision',json={'approve':True}).status_code==200
    assert connector_actions.tick()==1
    assert [c[0] for c in calls]==['POST','GET']
    assert all(c[2]=='sid=example-session-value' for c in calls)
    rows=client.get('/api/connector-actions').json()
    assert rows[0]['runs'][0]['status']=='VERIFIED'

def test_unknown_write_is_not_retried(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    config=action(client)
    run=client.post('/api/connector-actions/'+config['id']+'/run').json()
    client.post('/api/connector-actions/runs/'+run['id']+'/decision',json={'approve':True})
    attempts=[]
    def fail(_action,_source):attempts.append(1);raise TimeoutError('ambiguous')
    monkeypatch.setattr(connector_actions,'_execute_http',fail)
    assert connector_actions.tick()==0
    assert connector_actions.tick()==0
    assert attempts==[1]
    row=client.get('/api/connector-actions').json()[0]
    assert row['enabled'] is False and row['runs'][0]['status']=='UNKNOWN'
    assert 'ambiguous' not in str(row)

def test_verification_mismatch_stops_action_without_replay(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    config=action(client)
    calls=[];original=httpx.Client
    def handler(request):
        calls.append(request.method)
        return httpx.Response(204) if request.method=='POST' else httpx.Response(200,json={'status':'pending'})
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(connector_actions.httpx,'Client',lambda **kwargs:original(transport=transport,**kwargs))
    run=client.post('/api/connector-actions/'+config['id']+'/run').json()
    client.post('/api/connector-actions/runs/'+run['id']+'/decision',json={'approve':True})
    assert connector_actions.tick()==0 and connector_actions.tick()==0
    assert calls==['POST','GET']
    assert client.get('/api/connector-actions').json()[0]['runs'][0]['status']=='UNKNOWN'

def test_csrf_header_is_derived_from_encrypted_cookie(client,monkeypatch):
    source(client,monkeypatch)
    assert client.put('/api/connectors/site-source/session',json={'cookies':[
        {'name':'sid','value':'example-session-value'},{'name':'csrftoken','value':'csrf-only-in-cookie'}]}).status_code==200
    result=client.post('/api/connector-actions',json={'connector_id':'site-source','name':'Запись с CSRF',
        'method':'POST','target_url':'https://example.org/api/status','body':{'status':'done'},
        'verification_url':'https://example.org/api/status','verification_field':'status',
        'expected_value':'done','csrf_cookie_name':'csrftoken','csrf_header_name':'X-CSRFToken'})
    assert result.status_code==200,result.text
    calls=[];original=httpx.Client
    def handler(request):
        calls.append(request.headers.get('x-csrftoken'))
        return httpx.Response(204) if request.method=='POST' else httpx.Response(200,json={'status':'done'})
    monkeypatch.setattr(connector_actions.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    run=client.post('/api/connector-actions/'+result.json()['id']+'/run').json()
    client.post('/api/connector-actions/runs/'+run['id']+'/decision',json={'approve':True})
    assert connector_actions.tick()==1 and calls==['csrf-only-in-cookie',None]
    assert 'csrf-only-in-cookie' not in str(client.get('/api/connector-actions').json())

def test_cookie_path_is_respected_for_write_and_verification(client,monkeypatch):
    source(client,monkeypatch)
    assert client.put('/api/connectors/site-source/session',json={'cookies':[
        {'name':'sid','value':'root-session','path':'/'},
        {'name':'task_secret','value':'tasks-only','path':'/tasks'}]}).status_code==200
    config=action(client)
    sent=[];original=httpx.Client
    def handler(request):
        sent.append(request.headers.get('cookie'))
        return httpx.Response(204) if request.method=='POST' else httpx.Response(200,json={'status':'done'})
    monkeypatch.setattr(connector_actions.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    run=client.post('/api/connector-actions/'+config['id']+'/run').json()
    client.post('/api/connector-actions/runs/'+run['id']+'/decision',json={'approve':True})
    assert connector_actions.tick()==1
    assert sent==['sid=root-session','sid=root-session']

def test_revoking_session_disables_scheduled_write(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    config=action(client,method='PUT',interval=60)
    assert client.put('/api/connector-actions/'+config['id']+'/auto',json={
        'confirmation':'Установить статус','days':7,'max_runs':2}).status_code==200
    assert client.delete('/api/connectors/site-source/session').status_code==200
    calls=[];monkeypatch.setattr(connector_actions,'_execute_http',lambda _a,_s:calls.append(1))
    assert connector_actions.tick(now()+timedelta(minutes=61))==0 and calls==[]
    assert client.get('/api/connector-actions').json()[0]['enabled'] is False

def test_replacing_session_revokes_actions_for_prior_account(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    config=action(client,method='PUT',interval=60)
    assert client.put('/api/connector-actions/'+config['id']+'/auto',json={
        'confirmation':'Установить статус','days':7,'max_runs':2}).status_code==200
    replaced=client.put('/api/connectors/site-source/session',json={
        'cookies':[{'name':'sid','value':'another-account-session'}]})
    assert replaced.status_code==200
    row=client.get('/api/connector-actions').json()[0]
    assert row['enabled'] is False and row['auto_approved'] is False
    assert client.post('/api/connector-actions/'+config['id']+'/run').status_code==409

def test_bounded_auto_put_stops_after_run_limit(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    config=action(client,method='PUT',interval=60)
    grant=client.put('/api/connector-actions/'+config['id']+'/auto',json={'confirmation':'Установить статус','days':7,'max_runs':2})
    assert grant.status_code==200
    executions=[]
    monkeypatch.setattr(connector_actions,'_execute_http',lambda _a,_s:executions.append(1))
    stamp=now()+timedelta(minutes=61)
    assert connector_actions.tick(stamp)==1
    assert connector_actions.tick(stamp+timedelta(minutes=61))==1
    assert connector_actions.tick(stamp+timedelta(minutes=122))==0
    assert executions==[1,1]
    with sessions()() as session:
        row=session.get(ConnectorAction,config['id'])
        assert row.run_count==2
        runs=session.query(ConnectorActionRun).filter_by(action_id=config['id']).all()
        assert sorted(r.status for r in runs)==['VERIFIED','VERIFIED','WAITING_APPROVAL']

def test_revoking_auto_grant_cancels_queued_auto_run(client,monkeypatch):
    source(client,monkeypatch);assert transfer(client).status_code==200
    config=action(client,method='PUT',interval=60)
    assert client.put('/api/connector-actions/'+config['id']+'/auto',json={
        'confirmation':'Установить статус','days':7,'max_runs':2}).status_code==200
    with sessions()() as session:
        row=session.get(ConnectorAction,config['id'])
        run=connector_actions.queue(session,row,now())
        assert run.status=='APPROVED' and run.auto_authorized
        session.commit()
    assert client.delete('/api/connector-actions/'+config['id']+'/auto').status_code==200
    calls=[];monkeypatch.setattr(connector_actions,'_execute_http',lambda _a,_s:calls.append(1))
    assert connector_actions.tick()==0 and calls==[]
    assert client.get('/api/connector-actions').json()[0]['runs'][0]['status']=='CANCELLED'
