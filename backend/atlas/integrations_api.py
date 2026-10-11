from fastapi import APIRouter, HTTPException
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select,func,delete,update,Integer
from .api import DB,Secure,dto
from .models import Memory,MemoryFact,MemoryConflict,Embedding,Budget,ModelUsage,SpeechUsage,PushSubscription,Task,ServiceHeartbeat,OwnerSession,Audit
from .memory import contains_secret
from .config import settings
router=APIRouter(prefix='/api')

@router.get('/integrations')
def status(s:DB,_:Secure):
    cfg=settings()
    from .model_settings import resolve
    embedding=resolve('embedding',s)
    return {'embedding_enabled':embedding.ready,'embedding_model':embedding.model,'indexed':s.scalar(select(func.count()).select_from(Embedding).where(Embedding.memory_id.is_not(None),Embedding.model==embedding.identity)),'push_enabled':bool(cfg.vapid_private_key and cfg.vapid_public_key and cfg.vapid_subject),'vapid_public_key':cfg.vapid_public_key,'github':'public'}

@router.post('/embeddings/index')
def index(_:Secure):
    from .embeddings import enabled,index_one
    if not enabled():raise HTTPException(409,'Embedding провайдер не настроен')
    try:return {'indexed':int(index_one())}
    except Exception:raise HTTPException(502,'Embedding провайдер недоступен или вернул некорректный ответ')

class FactInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(min_length=1,max_length=100,pattern=r'^[\w .-]+$')
    value:str=Field(min_length=1,max_length=1000)
@router.put('/memory/{mid}/fact')
def fact(mid:str,body:FactInput,s:DB,_:Secure):
    if not s.get(Memory,mid):raise HTTPException(404,'Память не найдена')
    if contains_secret(body.key) or contains_secret(body.value):raise HTTPException(422,'Секреты запрещены')
    s.execute(delete(MemoryConflict).where((MemoryConflict.left_id==mid)|(MemoryConflict.right_id==mid)))
    row=s.get(MemoryFact,mid)
    if row is None:row=MemoryFact(memory_id=mid);s.add(row)
    row.key=body.key.strip().casefold();row.value=body.value.strip();row.active=True;s.flush()
    for other in s.scalars(select(MemoryFact).where(MemoryFact.key==row.key,MemoryFact.memory_id!=mid,MemoryFact.active==True,MemoryFact.value!=row.value)):
        s.add(MemoryConflict(left_id=other.memory_id,right_id=mid))
    s.add(Audit(event='memory_fact_confirmed',details={'id':mid}));s.commit();return dto(row)
@router.get('/memory-conflicts')
def conflicts(s:DB,_:Secure):
    return [dict(dto(c),left=dto(s.get(Memory,c.left_id)),right=dto(s.get(Memory,c.right_id))) for c in s.scalars(select(MemoryConflict).where(MemoryConflict.status=='PENDING'))]
class Resolution(BaseModel):choice:str=Field(pattern='^(left|right|both)$')
@router.post('/memory-conflicts/{identifier}/resolve')
def resolve(identifier:str,body:Resolution,s:DB,_:Secure):
    c=s.get(MemoryConflict,identifier)
    if c is None or c.status!='PENDING':raise HTTPException(409,'Конфликт отсутствует или уже разрешён')
    if body.choice!='both':
        removed=c.right_id if body.choice=='left' else c.left_id
        s.get(MemoryFact,removed).active=False
        for related in s.scalars(select(MemoryConflict).where(MemoryConflict.status=='PENDING',((MemoryConflict.left_id==removed)|(MemoryConflict.right_id==removed)))):related.status='RESOLVED'
    c.status='RESOLVED';s.add(Audit(event='memory_conflict_resolved',details={'id':identifier,'choice':body.choice}));s.commit();return {'ok':True}


class BudgetInput(BaseModel):
    model_config=ConfigDict(extra='ignore')
    token_limit:int=Field(ge=1,le=100000000)
    cost_limit:int|None=Field(default=None,ge=1,le=1000000000)
    input_rate:int=Field(default=0,ge=0,le=1000000000)
    output_rate:int=Field(default=0,ge=0,le=1000000000)
    daily_token_limit:int|None=Field(default=None,ge=1,le=100000000)
    daily_cost_limit:int|None=Field(default=None,ge=1,le=1000000000)
    task_cost_limit:int|None=Field(default=None,ge=1,le=1000000000)
    speech_rub_month_limit:int|None=Field(default=None,ge=1,le=1000000000)
    speech_rub_day_limit:int|None=Field(default=None,ge=1,le=1000000000)
@router.get('/budget')
def budget(s:DB,_:Secure):
    from datetime import datetime,timezone
    instant=datetime.now(timezone.utc)
    start=instant.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    day_start=instant.replace(hour=0,minute=0,second=0,microsecond=0)
    usage=s.scalars(select(ModelUsage).where(ModelUsage.created_at>=start).order_by(ModelUsage.created_at.desc()).limit(100)).all()
    totals=s.execute(select(func.coalesce(func.sum(ModelUsage.tokens),0),func.coalesce(func.sum(ModelUsage.cost),0),func.count(ModelUsage.id),func.count(ModelUsage.cost)).where(ModelUsage.created_at>=start)).one()
    daily=s.execute(select(func.coalesce(func.sum(ModelUsage.tokens),0),func.coalesce(func.sum(ModelUsage.cost),0),func.count(ModelUsage.id),func.count(ModelUsage.cost)).where(ModelUsage.created_at>=day_start)).one()
    speech_month=s.execute(select(func.coalesce(func.sum(SpeechUsage.cost_kopecks),0),func.count(SpeechUsage.id),func.sum((SpeechUsage.status=='UNKNOWN').cast(Integer))).where(SpeechUsage.created_at>=start)).one()
    speech_day=s.execute(select(func.coalesce(func.sum(SpeechUsage.cost_kopecks),0),func.count(SpeechUsage.id),func.sum((SpeechUsage.status=='UNKNOWN').cast(Integer))).where(SpeechUsage.created_at>=day_start)).one()
    return {'limits':dto(s.get(Budget,1)),'usage':[dto(r) for r in usage], 'tokens':totals[0], 'cost':totals[1] if totals[2]==totals[3] else None,
            'today':{'tokens':daily[0],'cost':daily[1] if daily[2]==daily[3] else None},
            'speech':{'month_kopecks':speech_month[0],'day_kopecks':speech_day[0],
                      'unknown':speech_month[2] or 0,'rate_kopecks_per_minute':settings().telegram_stt_kopecks_per_minute}}
@router.put('/budget')
def limits(body:BudgetInput,s:DB,_:Secure):
    values=body.model_dump(exclude_unset=True)
    if values:s.execute(update(Budget).where(Budget.id==1).values(**values))
    s.add(Audit(event='budget_updated'));s.commit();return {'ok':True}

class PushInput(BaseModel):
    endpoint:str=Field(max_length=2048)
    keys:dict[str,str]
@router.post('/push-subscriptions')
def subscribe(body:PushInput,s:DB,_:Secure):
    from .push import validate
    try:validate(body.endpoint,body.keys)
    except ValueError:raise HTTPException(422,'Некорректная push подписка')
    if not settings().vapid_private_key:raise HTTPException(409,'Push не настроен')
    row=s.scalar(select(PushSubscription).where(PushSubscription.endpoint==body.endpoint))
    if row is None:row=PushSubscription(endpoint=body.endpoint,keys=body.keys);s.add(row)
    else:row.enabled=True;row.keys=body.keys
    s.commit();return {'id':row.id}
@router.delete('/push-subscriptions/{identifier}')
def unsubscribe(identifier:str,s:DB,_:Secure):
    row=s.get(PushSubscription,identifier)
    if row is None:raise HTTPException(404,'Подписка не найдена')
    row.enabled=False;s.commit();return {'ok':True}
@router.post('/sessions/revoke')
def revoke(s:DB,_:Secure):
    s.execute(update(OwnerSession).values(revoked=True));s.add(Audit(event='sessions_revoked'));s.commit();return {'ok':True}
@router.get('/ops')
def ops(s:DB,_:Secure):
    return {'tasks':dict(s.execute(select(Task.status,func.count()).group_by(Task.status)).all()),'heartbeats':[dto(r) for r in s.scalars(select(ServiceHeartbeat))],'model_requests':dict(s.execute(select(ModelUsage.status,func.count()).group_by(ModelUsage.status)).all())}
