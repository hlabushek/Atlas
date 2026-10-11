"""Owner preferences shared by every interface/process. No credentials in SQL."""
import copy,hashlib
from dataclasses import dataclass,field
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .config import settings
from .db import sessions
from .models import ModelPreferences
PROVIDERS={'routerai':('RouterAI','https://routerai.ru/api/v1'), 'plusvibe':('PlusVibeAPI','https://plusvibeapi.ru/v1')}
PRESETS=[{'name':'DeepSeek V4.1 Flash','model':'deepseek/deepseek-v4.1-flash','role':'main'},
         {'name':'Gemini 3.8 Flash','model':'google/gemini-3.8-flash','role':'main'},
         {'name':'Claude Sonnet 5.5','model':'anthropic/claude-sonnet-5.5','role':'planner'},
         {'name':'Qwen3-Embedding-8B','model':'qwen/qwen3-embedding-8b','role':'embedding'},
         {'name':'DeepSeek V4 Flash','model':'deepseek/deepseek-v4-flash','role':'main'},
         {'name':'Liquid D1 — модель решений','model':'liquid/d1','role':'decision'},
         {'name':'Tev1 4B Experimental','model':'togethercomputer/tev1-4b-experimental','role':'decision'}]
class Role(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider:Literal['routerai','plusvibe','environment']='routerai'
    model:str=Field(min_length=1,max_length=160,pattern=r'^[A-Za-z0-9][A-Za-z0-9_./:@=+&~-]*$')
    input_rate:int=Field(default=0,ge=0,le=1000000000)
    output_rate:int=Field(default=0,ge=0,le=1000000000)
class Preferences(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['environment','configured']='configured'
    main:Role=Field(default_factory=lambda:Role(model=PRESETS[0]['model']))
    planner:Role=Field(default_factory=lambda:Role(model=PRESETS[2]['model']))
    embedding:Role=Field(default_factory=lambda:Role(model=PRESETS[3]['model']))
    decision:Role=Field(default_factory=lambda:Role(model='liquid/d1'))
    embeddings_enabled:bool=True
    auto_chat_routing:bool=False
    max_output_tokens:int=Field(default=1024,ge=32,le=8192)
    @model_validator(mode='after')
    def decision_provider(self):
        if self.decision.provider!='routerai':
            raise ValueError('Для структурированных решений пока поддерживается RouterAI')
        return self
class ModelUnavailable(ValueError):pass
@dataclass(frozen=True)
class Runtime:
    provider:str
    url:str
    model:str
    max_output_tokens:int
    ready:bool
    key:str=field(default='',repr=False)
    rates:tuple|None=None
    revision:int=0
    @property
    def identity(self):return hashlib.sha256((self.provider+'\0'+self.url+'\0'+self.model).encode()).hexdigest()
    def require(self):
        if not self.ready:
            name=PROVIDERS.get(self.provider,(self.provider,''))[0]
            variable={'routerai':'ATLAS_ROUTERAI_API_KEY','plusvibe':'ATLAS_PLUSVIBE_API_KEY'}.get(self.provider,'настройки сервера')
            raise ModelUnavailable(name+' не подключён: настройте '+variable+' на сервере')
    def snapshot(self):
        # URL is public metadata only. Credentials/query fragments cannot enter task records.
        u=urlsplit(self.url)
        if u.username or u.password or u.query or u.fragment:raise ModelUnavailable('URL модели не должен содержать секреты')
        return {'provider':self.provider,'url':self.url,'model':self.model,'max_output_tokens':self.max_output_tokens,'rates':list(self.rates) if self.rates is not None else None,'revision':self.revision}

def credential(provider,url='',embedding=False):
    cfg=settings()
    if provider in PROVIDERS:
        dedicated=cfg.routerai_api_key if provider=='routerai' else cfg.plusvibe_api_key
        if dedicated:return dedicated
        if embedding and cfg.embedding_url.rstrip('/')==PROVIDERS[provider][1]:return cfg.embedding_api_key
        if cfg.model_url.rstrip('/')==PROVIDERS[provider][1]:return cfg.model_api_key
        return ''
    return cfg.embedding_api_key if embedding else cfg.model_api_key

def resolve(role='main',session=None,profile=None):
    if session is None:
        with sessions()() as s:return resolve(role,s,profile)
    cfg=settings()
    if profile is not None:
        provider=profile['provider'];url=profile['url'];model=profile['model']
        if provider in PROVIDERS and url!=PROVIDERS[provider][1]:raise ModelUnavailable('Некорректный профиль провайдера')
        if provider not in PROVIDERS and url!=(cfg.embedding_url if role=='embedding' else cfg.model_url):raise ModelUnavailable('Серверный endpoint изменён; создайте новую задачу')
        secret=credential(provider,url,role=='embedding')
        return Runtime(provider,url,model,profile['max_output_tokens'],provider in ('demo','ollama') or bool(secret),secret,tuple(profile['rates']) if profile.get('rates') is not None else None,profile.get('revision',0))
    row=session.get(ModelPreferences,1)
    prefs=Preferences.model_validate(row.config)
    entry=getattr(prefs,role)
    if role=='decision':
        secret=credential('routerai')
        return Runtime('routerai',PROVIDERS['routerai'][1],entry.model,prefs.max_output_tokens,bool(secret),secret,
            (entry.input_rate,entry.output_rate),row.revision)
    if prefs.mode=='environment' or entry.provider=='environment':
        if role=='embedding':
            return Runtime('environment',cfg.embedding_url,cfg.embedding_model,cfg.max_output_tokens,bool(cfg.embedding_url and cfg.embedding_model),cfg.embedding_api_key,None,row.revision)
        model=cfg.model_name if prefs.mode=='environment' else entry.model
        return Runtime(cfg.model_provider,cfg.model_url,model,cfg.max_output_tokens if prefs.mode=='environment' else prefs.max_output_tokens,True,cfg.model_api_key,None,row.revision)
    secret=credential(entry.provider,embedding=role=='embedding')
    ready=bool(secret) and (role!='embedding' or prefs.embeddings_enabled)
    return Runtime(entry.provider,PROVIDERS[entry.provider][1],entry.model,prefs.max_output_tokens,ready,secret,(entry.input_rate,entry.output_rate),row.revision)

def view(session):
    row=session.get(ModelPreferences,1)
    roles={}
    for name in ('main','planner','embedding','decision'):
        r=resolve(name,session)
        roles[name]={'provider':r.provider,'model':r.model,'ready':r.ready,'max_output_tokens':r.max_output_tokens}
    config=Preferences.model_validate(row.config).model_dump()
    return {'revision':row.revision,'config':config,'effective':roles,'presets':PRESETS,'providers':[{'id':p,'name':v[0],'key_configured':bool(credential(p))} for p,v in PROVIDERS.items()]}
