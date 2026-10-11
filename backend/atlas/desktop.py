"""Shared desktop policy and durable read/create commands. TLS owns device identity."""
from datetime import timedelta
from sqlalchemy import select
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from .models import Device,DeviceCommand,ToolCall,ToolGrant,Task,Approval,Audit,DeviceMutex,now,uid
from .notifications import utc
from .tools import Tool
from .memory import contains_secret

class DesktopInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id:str=Field(min_length=1,max_length=36)
    root:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    operation:str=Field(pattern=r'^(read_text|list|create_text|launch_file)$')
    path:str=Field(default='',max_length=500)
    content:str|None=Field(default=None,max_length=65536)
    app:str|None=Field(default=None,pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,39}$')
    @field_validator('path')
    @classmethod
    def relative(cls,value):
        if '\\' in value or ':' in value or value.startswith('/') or any(p in {'.','..'} or p.startswith('.') or p.endswith((' ','.')) for p in value.split('/') if p):raise ValueError('Unsafe relative path')
        if any(p.lower().endswith(('.pem','.key','.pfx','.session.enc')) for p in value.split('/')):raise ValueError('Credential file prohibited')
        return value
    @model_validator(mode='after')
    def safe_write(self):
        if self.operation=='create_text':
            if not self.path or self.content is None:raise ValueError('New file path and content are required')
            raw=self.content.encode('utf-8')
            if len(raw)>65536 or '\x00' in self.content or any(ord(c)<32 and c not in '\n\r\t' for c in self.content):raise ValueError('Invalid text content')
            if self.path.rsplit('.',1)[-1].lower() not in {'txt','md','csv','json'}:raise ValueError('Only txt/md/csv/json can be created')
            if contains_secret(self.content):raise ValueError('Secrets prohibited')
        elif self.content is not None:raise ValueError('Content is only allowed for create_text')
        if self.operation=='launch_file':
            if not self.path or not self.app:raise ValueError('File and application alias are required')
            if self.path.rsplit('.',1)[-1].lower() not in {'txt','md','csv','json','pdf','png','jpg','jpeg','webp','docx','xlsx','pptx'}:raise ValueError('Executable and script files cannot be launched')
        elif self.app is not None:raise ValueError('Application alias is only allowed for launch_file')
        return self

def lock(s):
    if s.scalar(select(DeviceMutex).where(DeviceMutex.id==1).with_for_update()) is None:raise PermissionError('Device policy unavailable')

def allowed(s,d,cmd=None):
    if not d or not d.enabled or utc(d.expires_at)<=now():return False
    if cmd:
        a=cmd.arguments;call=s.get(ToolCall,cmd.call_id);task=s.get(Task,call.task_id)
        grant=s.scalar(select(ToolGrant).where(ToolGrant.tool==call.tool).with_for_update());approval=s.scalar(select(Approval).where(Approval.call_id==call.id))
        expected_tool='desktop.write' if a['operation'] in {'create_text','launch_file'} else 'desktop.read'
        return call.tool==expected_tool and call.arguments==a and call.status=='STARTED' and task.current_step==call.step and a['root'] in d.roots and a['operation'] in d.operations and bool(grant and grant.allowed and approval and approval.status=='APPROVED' and task.status=='WAITING_DEVICE')
    return True

def enqueue(s,call):
    lock(s)
    args=DesktopInput.model_validate(call.arguments).model_dump()
    expected_tool='desktop.write' if args['operation'] in {'create_text','launch_file'} else 'desktop.read'
    if call.tool!=expected_tool:raise ValueError('Desktop tool does not match requested operation')
    d=s.scalar(select(Device).where(Device.id==args['device_id']).with_for_update())
    if not allowed(s,d) or args['root'] not in d.roots or args['operation'] not in d.operations:raise ValueError('Device grant denied')
    row=s.scalar(select(DeviceCommand).where(DeviceCommand.call_id==call.id))
    if row is None:
        row=DeviceCommand(device_id=d.id,call_id=call.id,arguments=args,expires_at=min(utc(d.expires_at),now()+timedelta(hours=1)));s.add(row);s.flush()
    return {'command_id':row.id}

DESKTOP=Tool('desktop.read','Прочитать разрешённый текст или список каталога на подключённом устройстве',DesktopInput,'device.read',True,False,'device',enqueue,lambda s,c,r:False)
DESKTOP_WRITE=Tool('desktop.write','Создать новый текстовый файл или открыть разрешённый файл выбранным приложением на устройстве',DesktopInput,'device.write',True,False,'device',enqueue,lambda s,c,r:False)

def fail(s,cmd,status='CANCELLED'):
    cmd.status=status;cmd.error='Доступ отозван, команда истекла или результат неизвестен'
    call=s.get(ToolCall,cmd.call_id);task=s.get(Task,call.task_id)
    approval=s.scalar(select(Approval).where(Approval.call_id==call.id))
    if approval and approval.status=='APPROVED':approval.status='CANCELLED'
    if call.status=='STARTED':call.status='UNKNOWN' if status=='UNKNOWN' else 'BLOCKED'
    if task.status=='WAITING_DEVICE':task.status='PAUSED' if status=='UNKNOWN' else 'FAILED';task.error=cmd.error
    s.add(Audit(task_id=task.id,event='device_command_'+status.lower(),details={'command_id':cmd.id}))

def sweep(s):
    lock(s)
    for cmd in s.scalars(select(DeviceCommand).where(DeviceCommand.status.in_(['PENDING','LEASED'])).with_for_update()):
        d=s.get(Device,cmd.device_id)
        if cmd.status=='LEASED' and utc(cmd.lease_until)<=now():fail(s,cmd,'UNKNOWN')
        elif utc(cmd.expires_at)<=now() or not allowed(s,d,cmd):fail(s,cmd)

def poll(s,fingerprint):
    lock(s)
    d=s.scalar(select(Device).where(Device.fingerprint==fingerprint).with_for_update())
    if not allowed(s,d):raise PermissionError('Device denied')
    d.last_seen=now();sweep(s)
    cmd=s.scalar(select(DeviceCommand).where(DeviceCommand.device_id==d.id,DeviceCommand.status=='PENDING').order_by(DeviceCommand.created_at).with_for_update(skip_locked=True).limit(1))
    if not cmd:return None
    cmd.status='LEASED';cmd.lease_token=uid();cmd.lease_until=now()+timedelta(seconds=30)
    s.add(Audit(event='device_command_claimed',details={'command_id':cmd.id}))
    return {'id':cmd.id,'token':cmd.lease_token,'arguments':cmd.arguments,'expires_at':cmd.lease_until.isoformat()}

def command(s,fingerprint,cid,token):
    lock(s)
    cmd=s.scalar(select(DeviceCommand).where(DeviceCommand.id==cid).with_for_update())
    if not cmd:raise PermissionError('Command denied')
    d=s.get(Device,cmd.device_id)
    if not allowed(s,d) or d.fingerprint!=fingerprint or cmd.lease_token!=token:raise PermissionError('Command denied')
    if cmd.status=='SUCCEEDED':return cmd
    if cmd.status!='LEASED' or utc(cmd.lease_until)<=now() or utc(cmd.expires_at)<=now() or not allowed(s,d,cmd):raise PermissionError('Command denied')
    return cmd

def complete(s,fingerprint,cid,token,result,error=None):
    cmd=command(s,fingerprint,cid,token)
    if cmd.status=='SUCCEEDED':return {'ok':True}
    if error:
        fail(s,cmd,'UNKNOWN' if result and result.get('ambiguous') is True else 'FAILED');return {'ok':False}
    import json
    if not isinstance(result,dict) or len(json.dumps(result))>75000 or contains_secret(json.dumps(result)):raise ValueError('Unsafe result')
    a=cmd.arguments
    if a['operation']=='read_text':
        import hashlib
        text=result.get('text')
        if not isinstance(text,str) or len(text.encode())>65536 or result.get('sha256')!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('Result verification failed')
    elif a['operation']=='list':
        if not isinstance(result.get('entries'),list) or len(result['entries'])>100:raise ValueError('Result verification failed')
    elif a['operation']=='create_text':
        import hashlib
        expected=a['content'].encode('utf-8')
        if result.get('created') is not True or result.get('path')!=a['path'] or result.get('bytes')!=len(expected) or result.get('sha256')!=hashlib.sha256(expected).hexdigest():raise ValueError('Result verification failed')
    elif a['operation']=='launch_file':
        if result.get('launched') is not True or result.get('path')!=a['path'] or result.get('app')!=a['app'] or not isinstance(result.get('pid'),int) or result['pid']<=0:raise ValueError('Launch result verification failed')
    else:raise ValueError('Unknown operation')
    call=s.get(ToolCall,cmd.call_id);task=s.get(Task,call.task_id)
    cmd.result=result;cmd.status='SUCCEEDED';call.result=result;call.status='SUCCEEDED'
    approval=s.scalar(select(Approval).where(Approval.call_id==call.id));approval.status='CONSUMED'
    task.status='PENDING'
    s.add(Audit(task_id=task.id,event='device_result_verified',details={'command_id':cmd.id}));return {'ok':True}
