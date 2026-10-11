import hashlib,secrets
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import select
from .api import DB,Secure,dto,ensure_conversation
from .models import NotificationSettings,Initiative,TelegramBinding,Delivery,Audit,now
from .notifications import initiative,utc
from .config import settings

router=APIRouter(prefix="/api")
class Preferences(BaseModel):
    enabled: bool=False
    context_enabled: bool=False
    timezone:str="Europe/Moscow"
    quiet_start:int=Field(default=22,ge=0,le=23)
    quiet_end:int=Field(default=8,ge=0,le=23)
    daily_limit:int=Field(default=3,ge=0,le=30)
    max_repeats:int=Field(default=1,ge=0,le=3)
    repeat_hours:int=Field(default=4,ge=1,le=168)
    channel:str="web"
    @model_validator(mode="after")
    def validate(self):
        try:ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError,ValueError):raise ValueError("Неизвестный часовой пояс")
        if self.channel not in {"web","telegram"}:raise ValueError("Неизвестный канал")
        return self
class Reminder(BaseModel):
    message:str=Field(min_length=1,max_length=3000)
    due_at:datetime
    priority:int=Field(default=2,ge=1,le=3)
    conversation_id:str|None=None

@router.get('/notifications/settings')
def prefs(session:DB,_:Secure):return dto(session.get(NotificationSettings,1))
@router.put('/notifications/settings')
def set_prefs(body:Preferences,session:DB,_:Secure):
    row=session.scalar(select(NotificationSettings).where(NotificationSettings.id==1).with_for_update())
    if body.enabled and not row.enabled:row.enabled_at=now()
    for key,value in body.model_dump().items():setattr(row,key,value)
    # Disable cancels unsent notifications; never replays uncertain sends.
    if not row.enabled:
        for d in session.scalars(select(Delivery).where(Delivery.kind=='initiative',Delivery.status=='PENDING')):
            d.status='CANCELLED'
            item=session.get(Initiative,d.initiative_id)
            if item and item.status=='QUEUED':item.status='CANCELLED'
    session.add(Audit(event='notification_preferences_changed'));session.commit();return dto(row)
@router.get('/initiatives')
def items(session:DB,_:Secure):return [dto(i) for i in session.scalars(select(Initiative).order_by(Initiative.created_at.desc()).limit(100))]
@router.post('/initiatives')
def remind(body:Reminder,session:DB,_:Secure):
    if body.due_at.tzinfo is None:raise HTTPException(422,'Укажите часовой пояс')
    if body.conversation_id:ensure_conversation(session,body.conversation_id,writable=True)
    from .memory import contains_secret
    if contains_secret(body.message):raise HTTPException(422,'Секреты запрещены')
    item=initiative(session,'reminder:'+secrets.token_hex(16),'Владелец попросил напомнить',body.message,body.conversation_id,utc(body.due_at),utc(body.due_at)+timedelta(days=2),body.priority)
    session.commit();return dto(item)
@router.post('/initiatives/{iid}/cancel')
def cancel(iid:str,session:DB,_:Secure):
    item=session.scalar(select(Initiative).where(Initiative.id==iid).with_for_update())
    if item is None:raise HTTPException(404,'Инициатива не найдена')
    item.status='CANCELLED'
    for d in session.scalars(select(Delivery).where(Delivery.initiative_id==iid,Delivery.status=='PENDING')):d.status='CANCELLED'
    session.commit();return dto(item)
class Snooze(BaseModel):
    hours:int=Field(ge=1,le=24)
    @model_validator(mode='after')
    def allowed_interval(self):
        if self.hours not in {1,4,24}:raise ValueError('Выберите 1, 4 или 24 часа')
        return self

def active_initiative(session,iid):
    item=session.scalar(select(Initiative).where(Initiative.id==iid).with_for_update())
    if item is None:raise HTTPException(404,'Карточка не найдена')
    if item.status not in {'PENDING','SENT','QUEUED','SNOOZED'}:raise HTTPException(409,'Карточка уже закрыта')
    return item

@router.post('/initiatives/{iid}/complete')
def complete(iid:str,session:DB,_:Secure):
    item=active_initiative(session,iid);item.status='COMPLETED'
    for delivery in session.scalars(select(Delivery).where(Delivery.initiative_id==iid,Delivery.status=='PENDING')):
        delivery.status='CANCELLED'
    session.add(Audit(event='initiative_completed',details={'initiative_id':iid}));session.commit();return dto(item)

@router.post('/initiatives/{iid}/dismiss')
def dismiss(iid:str,session:DB,_:Secure):
    item=active_initiative(session,iid);item.status='CANCELLED'
    for delivery in session.scalars(select(Delivery).where(Delivery.initiative_id==iid,Delivery.status=='PENDING')):
        delivery.status='CANCELLED'
    session.add(Audit(event='initiative_dismissed',details={'initiative_id':iid}));session.commit();return dto(item)

@router.post('/initiatives/{iid}/snooze')
def snooze(iid:str,body:Snooze,session:DB,_:Secure):
    item=active_initiative(session,iid)
    if item.status=='QUEUED':raise HTTPException(409,'Уведомление уже отправляется через подключённый канал')
    item.status='SNOOZED';item.next_allowed=now()+timedelta(hours=body.hours)
    session.add(Audit(event='initiative_snoozed',details={'initiative_id':iid,'hours':body.hours}));session.commit();return dto(item)
@router.get('/telegram')
def telegram(session:DB,_:Secure):
    b=session.get(TelegramBinding,1)
    return {'configured':bool(settings().telegram_bot_token),'owner_id_configured':settings().telegram_owner_id is not None,'bound':bool(b.user_id),'user_id':b.user_id,'conversation_id':b.conversation_id}
@router.post('/telegram/bind-code')
def bind(session:DB,_:Secure):
    if not settings().telegram_bot_token or settings().telegram_owner_id is None:raise HTTPException(409,'Настройте token и owner ID в .env')
    b=session.scalar(select(TelegramBinding).where(TelegramBinding.id==1).with_for_update())
    code=secrets.token_urlsafe(24);b.code_hash=hashlib.sha256(code.encode()).hexdigest();b.code_expires=now()+timedelta(minutes=10)
    session.commit();return {'code':code,'expires_in':600}
@router.delete('/telegram')
def unbind(session:DB,_:Secure):
    b=session.scalar(select(TelegramBinding).where(TelegramBinding.id==1).with_for_update())
    b.user_id=b.chat_id=b.conversation_id=b.code_hash=None;b.code_expires=None
    for d in session.scalars(select(Delivery).where(Delivery.channel=='telegram',Delivery.status=='PENDING')):
        d.status='CANCELLED'
        item=session.get(Initiative,d.initiative_id) if d.initiative_id else None
        if item and item.status=='QUEUED':item.status='CANCELLED'
    session.add(Audit(event='telegram_unbound'));session.commit();return {'ok':True}
@router.get('/deliveries')
def deliveries(session:DB,_:Secure):return [dto(d) for d in session.scalars(select(Delivery).order_by(Delivery.created_at.desc()).limit(100))]
