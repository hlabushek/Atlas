from datetime import datetime,timezone
import re
from pydantic import BaseModel,Field,field_validator
from fastapi import APIRouter,HTTPException
from sqlalchemy import select
from .api import DB,Secure,dto
from .models import Device,DeviceCommand,Audit
from .desktop import sweep,lock
router=APIRouter(prefix='/api')
def device_dto(device):
    data=dto(device)
    for key in ('expires_at','last_seen'):
        value=data.get(key)
        if value is not None and value.tzinfo is None:data[key]=value.replace(tzinfo=timezone.utc)
    return data
class Registration(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')
    expires_at:datetime
class Policy(BaseModel):
    enabled:bool
    roots:list[str]=Field(max_length=10)
    operations:list[str]=Field(max_length=4)
    expires_at:datetime
    @field_validator('roots')
    @classmethod
    def roots_ok(cls,value):
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',x) for x in value):raise ValueError('Root alias required')
        return value
    @field_validator('operations')
    @classmethod
    def ops_ok(cls,value):
        if set(value)-{'read_text','list','create_text','launch_file'}:raise ValueError('Operation denied')
        return value
    @field_validator('expires_at')
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None:raise ValueError('Timezone required')
        return value
class VoicePolicy(BaseModel):
    enabled:bool
    conversation_id:str|None=None
    @field_validator('conversation_id')
    @classmethod
    def conversation_id_ok(cls,value):
        if value is not None and not re.fullmatch(r'[0-9a-f-]{36}',value):raise ValueError('Некорректный ID разговора')
        return value
@router.get('/devices')
def devices(s:DB,_:Secure):
    sweep(s);s.commit();return [device_dto(x) for x in s.scalars(select(Device))]
@router.post('/devices')
def add(body:Registration,s:DB,_:Secure):
    if body.expires_at.tzinfo is None:raise HTTPException(422,'Укажите часовой пояс')
    if s.scalar(select(Device).where(Device.fingerprint==body.fingerprint)):raise HTTPException(409,'Сертификат уже зарегистрирован')
    from .memory import contains_secret
    if contains_secret(body.name):raise HTTPException(422,'Секреты запрещены')
    d=Device(**body.model_dump());s.add(d);s.add(Audit(event='device_registered'));s.commit();return device_dto(d)
@router.put('/devices/{did}')
def update(did:str,body:Policy,s:DB,_:Secure):
    lock(s)
    d=s.scalar(select(Device).where(Device.id==did).with_for_update())
    if not d:raise HTTPException(404,'Устройство не найдено')
    for k,v in body.model_dump().items():setattr(d,k,v)
    if not d.enabled:d.voice_enabled=False
    sweep(s);s.add(Audit(event='device_policy_changed',details={'device_id':did}));s.commit();return device_dto(d)
@router.put('/devices/{did}/voice')
def update_voice(did:str,body:VoicePolicy,s:DB,_:Secure):
    lock(s)
    d=s.scalar(select(Device).where(Device.id==did).with_for_update())
    if not d:raise HTTPException(404,'Устройство не найдено')
    from .voice_gateway import set_voice_policy
    try:set_voice_policy(s,d,enabled=body.enabled,conversation_id=body.conversation_id)
    except ValueError as exc:raise HTTPException(422,str(exc))
    s.commit();return device_dto(d)
@router.get('/devices/{did}/commands')
def history(did:str,s:DB,_:Secure):return [{k:v for k,v in dto(x).items() if k!='lease_token'} for x in s.scalars(select(DeviceCommand).where(DeviceCommand.device_id==did).order_by(DeviceCommand.created_at.desc()).limit(100))]
