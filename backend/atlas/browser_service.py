"""Isolated Chromium RPC; no Atlas database, model or session encryption key."""
import os
import secrets
from ipaddress import ip_address
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import browser_connectors as browser

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

async def bounded(request:Request):
    if len(await request.body())>256000:raise HTTPException(413,'Запрос слишком велик')

def authorized(x_atlas_browser_token:str|None=Header(default=None)):
    expected=os.environ.get('ATLAS_BROWSER_SERVICE_TOKEN','')
    if not expected or len(expected)<32 or not x_atlas_browser_token or not secrets.compare_digest(expected,x_atlas_browser_token):
        raise HTTPException(401,'Доступ запрещён')

def remote_origin(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422,'Недопустимый адрес сайта')
    try:
        address=ip_address(parsed.hostname)
        if not address.is_global:raise HTTPException(422,'Недопустимый адрес сайта')
    except ValueError:pass
    return url

class Start(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str=Field(pattern=r'^[a-z][a-z0-9_-]{1,40}$')
    origin:str=Field(max_length=500)
    state:dict|None=None

class Step(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str=Field(pattern=r'^[a-z][a-z0-9_-]{1,40}$')
    kind:str=Field(pattern=r'^(navigate|click|type|key|refresh)$')
    x:int|None=Field(default=None,ge=0,le=1200)
    y:int|None=Field(default=None,ge=0,le=800)
    value:str|None=Field(default=None,max_length=200)
    url:str|None=Field(default=None,max_length=500)

class SourceId(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str=Field(pattern=r'^[a-z][a-z0-9_-]{1,40}$')

class Render(BaseModel):
    model_config=ConfigDict(extra='forbid')
    origin:str=Field(max_length=500)
    recipe:dict
    state:dict|None=None

@app.get('/health')
def health():return {'status':'ok'}

@app.post('/start',dependencies=[Depends(authorized),Depends(bounded)])
def start(body:Start):
    try:return browser.invoke(browser._begin,body.source_id,remote_origin(body.origin),body.state)
    except Exception as exc:raise HTTPException(422,'Браузер не открыл страницу ('+type(exc).__name__+')') from exc

@app.post('/step',dependencies=[Depends(authorized),Depends(bounded)])
def step(body:Step):
    try:return browser.invoke(browser._step,body.source_id,body.kind,body.x,body.y,body.value,body.url)
    except Exception as exc:raise HTTPException(422,'Действие браузера не выполнено ('+type(exc).__name__+')') from exc

@app.post('/finish',dependencies=[Depends(authorized),Depends(bounded)])
def finish(body:SourceId):
    try:return browser.invoke(browser._finish,body.source_id)
    except Exception as exc:raise HTTPException(422,'Сеанс входа недоступен ('+type(exc).__name__+')') from exc

@app.post('/cancel',dependencies=[Depends(authorized),Depends(bounded)])
def cancel(body:SourceId):
    browser.invoke(browser._cancel,body.source_id)
    return {'closed':True}

@app.post('/render',dependencies=[Depends(authorized),Depends(bounded)])
def render(body:Render):
    try:return browser.invoke(browser._render,remote_origin(body.origin),body.recipe,body.state)
    except Exception as exc:raise HTTPException(422,'Страница не собрана ('+type(exc).__name__+')') from exc
