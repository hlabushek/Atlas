"""Exact, owner-authorized HTTP actions with independent verification and no blind replay."""
import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .api import DB, Secure
from .connector_sessions import active as session_active, cookies as session_cookies, headers as session_headers, revoke as revoke_session
from .db import sessions
from .integration_permissions import scope_fingerprint, utc
from .memory import contains_secret
from .models import Audit, ConnectorAction, ConnectorActionRun, ConnectorSource, now
from .network import endpoint

router=APIRouter(prefix='/api/connector-actions')
FIELD=re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,39}(?:\.[a-zA-Z_][a-zA-Z0-9_]{0,39}){0,3}$')

class ActionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connector_id:str=Field(min_length=2,max_length=41)
    name:str=Field(min_length=1,max_length=100)
    method:str=Field(pattern=r'^(POST|PUT|PATCH)$')
    target_url:str=Field(min_length=1,max_length=500)
    body:dict=Field(default_factory=dict)
    verification_url:str=Field(min_length=1,max_length=500)
    verification_field:str=Field(pattern=FIELD.pattern)
    expected_value:str=Field(max_length=500)
    csrf_cookie_name:str|None=Field(default=None,pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
    csrf_header_name:str|None=Field(default=None,pattern=r'^X-(?:CSRFToken|CSRF-Token|XSRF-TOKEN)$')
    interval_minutes:int|None=Field(default=None,ge=60,le=10080)
    days:int=Field(default=30,ge=1,le=30)

class AutoGrant(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmation:str
    days:int=Field(default=7,ge=1,le=30)
    max_runs:int=Field(default=10,ge=1,le=100)

class RunDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approve:bool

def digest(action,source):
    payload={'source':scope_fingerprint(source),'session_generation':source.session_generation,
             'method':action.method,'target_url':action.target_url,
             'body':action.body,'verification_url':action.verification_url,
             'verification_field':action.verification_field,'expected_value':action.expected_value,
             'csrf_cookie_name':action.csrf_cookie_name,'csrf_header_name':action.csrf_header_name}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def same_origin(url,source):
    try:endpoint(url)
    except ValueError as exc:raise ValueError('Адрес действия должен быть в разрешённых доменах') from exc
    parsed=urlparse(url);origin=urlparse(source.origin)
    if (parsed.scheme,parsed.netloc)!=(origin.scheme,origin.netloc):raise ValueError('Действие должно оставаться на сайте источника')
    return url

def valid(action,source,stamp=None):
    stamp=utc(stamp or now())
    return bool(action and source and action.enabled and source.enabled and source.session_kind in (None,'cookies') and session_active(source)
                and utc(action.expires_at)>stamp and action.scope_fingerprint==digest(action,source))

def public(action):
    return {'id':action.id,'connector_id':action.connector_id,'name':action.name,'method':action.method,
            'target_url':action.target_url,'body':action.body,'verification_url':action.verification_url,
            'verification_field':action.verification_field,'expected_value':action.expected_value,
            'csrf_cookie_name':action.csrf_cookie_name,'csrf_header_name':action.csrf_header_name,
            'interval_minutes':action.interval_minutes,'next_run_at':action.next_run_at,
            'expires_at':action.expires_at,'enabled':action.enabled,'auto_approved':action.auto_approved,
            'approval_expires_at':action.approval_expires_at,'max_runs':action.max_runs,'run_count':action.run_count}

def run_public(run):
    return {'id':run.id,'action_id':run.action_id,'status':run.status,'scheduled_for':run.scheduled_for,
            'created_at':run.created_at,'completed_at':run.completed_at,'error_code':run.error_code,
            'auto_authorized':run.auto_authorized}

@router.get('')
def list_actions(session:DB,_:Secure):
    actions=session.scalars(select(ConnectorAction).order_by(ConnectorAction.created_at.desc()).limit(100)).all()
    result=[]
    for action in actions:
        runs=session.scalars(select(ConnectorActionRun).where(ConnectorActionRun.action_id==action.id).order_by(ConnectorActionRun.created_at.desc()).limit(5)).all()
        result.append({**public(action),'valid':valid(action,session.get(ConnectorSource,action.connector_id)),
                       'runs':[run_public(run) for run in runs]})
    return result

@router.post('')
def create_action(body:ActionInput,session:DB,_:Secure):
    source=session.get(ConnectorSource,body.connector_id)
    if source is None or not source.enabled:raise HTTPException(404,'Источник не найден или отключён')
    if not session_active(source):raise HTTPException(409,'Сначала передайте действующую сессию сайта')
    if bool(body.csrf_cookie_name)!=bool(body.csrf_header_name):raise HTTPException(422,'Укажите оба поля CSRF или оставьте оба пустыми')
    if body.csrf_cookie_name and body.csrf_cookie_name not in session_cookies(source,body.target_url):
        raise HTTPException(422,'Указанный CSRF cookie отсутствует в сессии')
    try:same_origin(body.target_url,source);same_origin(body.verification_url,source)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    encoded=json.dumps(body.body,ensure_ascii=False)
    review=json.dumps({'name':body.name,'target_url':body.target_url,'body':body.body,
        'verification_url':body.verification_url,'verification_field':body.verification_field,
        'expected_value':body.expected_value},ensure_ascii=False)
    if len(encoded.encode())>8000 or contains_secret(review) or any(x in encoded.lower() for x in ('"password"','"token"','"secret"','"cookie"','"authorization"')):
        raise HTTPException(422,'Тело запроса слишком велико или содержит секрет')
    action=ConnectorAction(connector_id=source.id,name=body.name,method=body.method,target_url=body.target_url,
        body=body.body,verification_url=body.verification_url,verification_field=body.verification_field,
        expected_value=body.expected_value,csrf_cookie_name=body.csrf_cookie_name,
        csrf_header_name=body.csrf_header_name,interval_minutes=body.interval_minutes,
        next_run_at=now()+timedelta(minutes=body.interval_minutes) if body.interval_minutes else None,
        expires_at=now()+timedelta(days=body.days),scope_fingerprint='',enabled=True,
        auto_approved=False,run_count=0)
    action.scope_fingerprint=digest(action,source)
    session.add(action);session.flush()
    session.add(Audit(event='connector_action_created',details={'action_id':action.id,'connector_id':source.id,'digest':action.scope_fingerprint}))
    session.commit()
    return public(action)

@router.put('/{action_id}/auto')
def allow_auto(action_id:str,body:AutoGrant,session:DB,_:Secure):
    action=session.get(ConnectorAction,action_id)
    if action is None:raise HTTPException(404,'Действие не найдено')
    source=session.get(ConnectorSource,action.connector_id)
    if not valid(action,source):raise HTTPException(409,'Источник или сессия недоступны')
    if action.method!='PUT' or action.interval_minutes is None:
        raise HTTPException(409,'Автоматическое разрешение доступно для точного регулярного PUT')
    if body.confirmation!=action.name:raise HTTPException(422,'Введите точное название действия')
    action.auto_approved=True
    action.approval_expires_at=min(utc(action.expires_at),now()+timedelta(days=body.days))
    action.max_runs=action.run_count+body.max_runs
    session.add(Audit(event='connector_action_auto_granted',details={'action_id':action.id,'digest':action.scope_fingerprint,
        'max_runs':action.max_runs,'expires_at':action.approval_expires_at.isoformat()}))
    session.commit();return public(action)

@router.delete('/{action_id}/auto')
def revoke_auto(action_id:str,session:DB,_:Secure):
    action=session.get(ConnectorAction,action_id)
    if action is None:raise HTTPException(404,'Действие не найдено')
    action.auto_approved=False;action.approval_expires_at=None;action.max_runs=None
    for run in session.scalars(select(ConnectorActionRun).where(ConnectorActionRun.action_id==action_id,
        ConnectorActionRun.status=='APPROVED',ConnectorActionRun.auto_authorized.is_(True))):
        run.status='CANCELLED';run.completed_at=now()
    session.add(Audit(event='connector_action_auto_revoked',details={'action_id':action.id}))
    session.commit();return public(action)

def pending(session,action):
    return session.scalar(select(ConnectorActionRun).where(ConnectorActionRun.action_id==action.id,
        ConnectorActionRun.status.in_(['WAITING_APPROVAL','APPROVED','STARTED'])))

def queue(session,action,stamp,force_manual=False):
    if pending(session,action):return None
    auto=bool(not force_manual and action.auto_approved and action.method=='PUT' and action.approval_expires_at
        and utc(action.approval_expires_at)>now() and action.max_runs is not None and action.run_count<action.max_runs)
    run=ConnectorActionRun(action_id=action.id,status='APPROVED' if auto else 'WAITING_APPROVAL',auto_authorized=auto,
        request_fingerprint=action.scope_fingerprint,scheduled_for=stamp,approved_at=now() if auto else None)
    session.add(run);session.flush()
    session.add(Audit(event='connector_action_auto_queued' if auto else 'connector_action_approval_requested',
                      details={'action_id':action.id,'run_id':run.id,'digest':run.request_fingerprint}))
    return run

@router.post('/{action_id}/run')
def request_run(action_id:str,session:DB,_:Secure):
    action=session.get(ConnectorAction,action_id)
    if action is None:raise HTTPException(404,'Действие не найдено')
    if not valid(action,session.get(ConnectorSource,action.connector_id)):raise HTTPException(409,'Действие или сессия недоступны')
    run=queue(session,action,now(),force_manual=True)
    if run is None:raise HTTPException(409,'Предыдущий запуск ещё не завершён')
    session.commit();return run_public(run)

@router.post('/runs/{run_id}/decision')
def decide(run_id:str,body:RunDecision,session:DB,_:Secure):
    run=session.scalar(select(ConnectorActionRun).where(ConnectorActionRun.id==run_id).with_for_update())
    if run is None:raise HTTPException(404,'Запуск не найден')
    if run.status!='WAITING_APPROVAL':raise HTTPException(409,'Запуск уже обработан')
    action=session.get(ConnectorAction,run.action_id)
    source=session.get(ConnectorSource,action.connector_id)
    if body.approve and (not valid(action,source) or run.request_fingerprint!=action.scope_fingerprint):
        raise HTTPException(409,'Настройки или сессия изменились')
    run.status='APPROVED' if body.approve else 'DENIED'
    run.approved_at=now() if body.approve else None
    run.completed_at=None if body.approve else now()
    session.add(Audit(event='connector_action_approved' if body.approve else 'connector_action_denied',
                      details={'action_id':action.id,'run_id':run.id,'digest':run.request_fingerprint}))
    session.commit();return run_public(run)

@router.delete('/{action_id}')
def disable_action(action_id:str,session:DB,_:Secure):
    action=session.get(ConnectorAction,action_id)
    if action is None:raise HTTPException(404,'Действие не найдено')
    action.enabled=False;action.auto_approved=False;action.next_run_at=None
    for run in session.scalars(select(ConnectorActionRun).where(ConnectorActionRun.action_id==action_id,
        ConnectorActionRun.status.in_(['WAITING_APPROVAL','APPROVED']))):
        run.status='CANCELLED';run.completed_at=now()
    session.add(Audit(event='connector_action_disabled',details={'action_id':action.id}))
    session.commit();return public(action)

def _field(document,path):
    value=document
    for part in path.split('.'):
        if not isinstance(value,dict) or part not in value:raise ValueError('verification_field_missing')
        value=value[part]
    return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,separators=(',',':'))

def _execute_http(action,source):
    same_origin(action.target_url,source);same_origin(action.verification_url,source)
    headers={'User-Agent':'Atlas-action/1.0','Accept':'application/json',
             'Origin':urlparse(source.origin).scheme+'://'+urlparse(source.origin).netloc,
             'Referer':source.origin,**session_headers(source,action.target_url)}
    if action.csrf_cookie_name:
        token=session_cookies(source,action.target_url).get(action.csrf_cookie_name)
        if not token:raise PermissionError('csrf_cookie_missing')
        headers[action.csrf_header_name]=token
    with httpx.Client(timeout=15,follow_redirects=False,trust_env=False) as client:
        with client.stream(action.method,action.target_url,json=action.body,headers=headers) as response:
            if response.status_code<200 or response.status_code>=300:raise ValueError('write_status')
        verify_headers={'User-Agent':'Atlas-action/1.0','Accept':'application/json',
                        **session_headers(source,action.verification_url)}
        with client.stream('GET',action.verification_url,headers=verify_headers) as verification:
            if verification.status_code!=200 or 'application/json' not in verification.headers.get('content-type','').lower():
                raise ValueError('verification_response')
            chunks=[];size=0
            for chunk in verification.iter_bytes():
                size+=len(chunk)
                if size>50000:raise ValueError('verification_too_large')
                chunks.append(chunk)
        actual=_field(json.loads(b''.join(chunks)),action.verification_field)
        if actual!=action.expected_value:raise ValueError('verification_mismatch')

def tick(stamp=None):
    stamp=stamp or now();completed=0
    with sessions()() as session:
        expired=session.scalars(select(ConnectorSource).where(ConnectorSource.session_ciphertext.is_not(None),
            ConnectorSource.session_expires_at<=now()).limit(10).with_for_update(skip_locked=True)).all()
        for source in expired:
            revoke_session(source)
            source.schedule_minutes=None;source.next_run_at=None
            source.schedule_fingerprint=None;source.schedule_expires_at=None
            for action in session.scalars(select(ConnectorAction).where(ConnectorAction.connector_id==source.id)):
                action.enabled=False;action.auto_approved=False;action.next_run_at=None
            session.add(Audit(event='connector_session_expired',details={'connector_id':source.id}))
        for stale in session.scalars(select(ConnectorActionRun).where(ConnectorActionRun.status=='STARTED',
            ConnectorActionRun.started_at<now()-timedelta(minutes=2)).limit(10).with_for_update(skip_locked=True)):
            stale.status='UNKNOWN';stale.error_code='interrupted';stale.completed_at=now()
            action=session.get(ConnectorAction,stale.action_id)
            action.enabled=False;action.auto_approved=False;action.next_run_at=None
            session.add(Audit(event='connector_action_unknown',details={'action_id':action.id,'run_id':stale.id,'error_code':'interrupted'}))
        due=session.scalars(select(ConnectorAction).where(ConnectorAction.enabled.is_(True),
            ConnectorAction.interval_minutes.is_not(None),ConnectorAction.next_run_at<=stamp)
            .order_by(ConnectorAction.next_run_at).limit(3).with_for_update(skip_locked=True)).all()
        for action in due:
            source=session.get(ConnectorSource,action.connector_id)
            if not valid(action,source,stamp):
                action.enabled=False;action.next_run_at=None
                session.add(Audit(event='connector_action_revoked',details={'action_id':action.id}))
                continue
            queue(session,action,stamp)
            action.next_run_at=stamp+timedelta(minutes=action.interval_minutes)
        session.commit()
    for _ in range(2):
        with sessions()() as session:
            run=session.scalar(select(ConnectorActionRun).where(ConnectorActionRun.status=='APPROVED')
                .order_by(ConnectorActionRun.created_at).with_for_update(skip_locked=True))
            if run is None:break
            action=session.get(ConnectorAction,run.action_id);source=session.get(ConnectorSource,action.connector_id)
            auto_invalid=bool(run.auto_authorized and (not action.auto_approved or not action.approval_expires_at
                or utc(action.approval_expires_at)<=now()))
            if not valid(action,source) or run.request_fingerprint!=action.scope_fingerprint or auto_invalid:
                run.status='CANCELLED';run.completed_at=now()
                session.add(Audit(event='connector_action_revoked',details={'action_id':action.id,'run_id':run.id}))
                session.commit();continue
            run.status='STARTED';run.started_at=now();action.run_count+=1
            identifier=run.id
            session.add(Audit(event='connector_action_started',details={'action_id':action.id,'run_id':run.id}))
            session.commit()
        try:
            with sessions()() as session:
                run=session.scalar(select(ConnectorActionRun).where(ConnectorActionRun.id==identifier).with_for_update())
                action=session.scalar(select(ConnectorAction).where(ConnectorAction.id==run.action_id).with_for_update())
                source=session.scalar(select(ConnectorSource).where(ConnectorSource.id==action.connector_id).with_for_update())
                auto_invalid=bool(run.auto_authorized and (not action.auto_approved or not action.approval_expires_at
                    or utc(action.approval_expires_at)<=now()))
                if not valid(action,source) or run.request_fingerprint!=action.scope_fingerprint or auto_invalid:
                    raise PermissionError('revoked')
                _execute_http(action,source)
                run.status='VERIFIED';run.completed_at=now();completed+=1
                session.add(Audit(event='connector_action_verified',details={'action_id':run.action_id,'run_id':run.id}))
                session.commit()
        except Exception as exc:
            with sessions()() as session:
                run=session.get(ConnectorActionRun,identifier);action=session.get(ConnectorAction,run.action_id)
                run.status='UNKNOWN';run.error_code=type(exc).__name__;run.completed_at=now()
                action.enabled=False;action.auto_approved=False;action.next_run_at=None
                session.add(Audit(event='connector_action_unknown',details={'action_id':action.id,'run_id':run.id,'error_code':run.error_code}))
                session.commit()
    return completed
