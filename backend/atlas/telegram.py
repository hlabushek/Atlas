"""Owner-only Telegram long polling. Bot tokens never enter DB, model context or errors."""
import hashlib,hmac,time,uuid
import os,re
from datetime import timedelta,datetime,timezone,time as daytime
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy import select,func,or_,case
from .config import settings
from .db import sessions
from .models import TelegramBinding,ChannelUpdate,Task,Message,Conversation,Approval,ToolCall,Delivery,Initiative,NotificationSettings,Audit,now
from .notifications import utc,queue,link_reply

class TelegramAPI:
    def _client(self):
        options={'timeout':35,'follow_redirects':False}
        proxy=settings().telegram_proxy.strip()
        if proxy:options['proxy']=proxy
        return httpx.Client(**options)

    def request(self,method,payload):
        token=settings().telegram_bot_token
        if not token:raise RuntimeError('Telegram not configured')
        # Never log HTTP exceptions: request URLs contain the token.
        with self._client() as client:
            result=client.post('https://api.telegram.org/bot'+token+'/'+method,json=payload)
            result.raise_for_status();data=result.json()
            if not data.get('ok'):raise RuntimeError('Telegram API rejected request')
            return data['result']
    def send(self,chat_id,payload):
        return self.request('sendMessage',{'chat_id':chat_id,'text':payload['text'][:4000],**({'reply_markup':payload['reply_markup']} if 'reply_markup' in payload else {})})

    def download_file(self,file_id,max_bytes):
        token=settings().telegram_bot_token
        info=self.request('getFile',{'file_id':file_id})
        path=info.get('file_path','')
        if (type(info.get('file_size')) is not int or info['file_size']>max_bytes or
            not isinstance(path,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,512}',path) or '..' in path.split('/')):
            raise ValueError('Файл превышает допустимый размер или имеет неподдерживаемый формат')
        data=bytearray()
        try:
            with self._client() as client:
                with client.stream('GET','https://api.telegram.org/file/bot'+token+'/'+path) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data)>max_bytes:raise ValueError('Файл превышает допустимый размер')
        except ValueError:raise
        except Exception:raise ValueError('Не удалось безопасно загрузить файл из Telegram') from None
        if not data or len(data)!=info['file_size']:raise ValueError('Не удалось безопасно загрузить файл из Telegram')
        return bytes(data)

def _document_text(message,api):
    document=message.get('document') or {};name=os.path.basename(str(document.get('file_name','')))
    ext=os.path.splitext(name)[1].lower()
    if ext not in {'.txt','.md','.csv','.json'} or type(document.get('file_size')) is not int or document['file_size']>65536:
        raise ValueError('Поддерживаются только файлы TXT, MD, CSV или JSON размером до 64 КиБ')
    raw=api.download_file(document.get('file_id',''),65536)
    try:body=raw.decode('utf-8-sig')
    except UnicodeDecodeError:raise ValueError('Файл должен быть текстом в кодировке UTF-8') from None
    if any(ord(ch)<32 and ch not in '\n\r\t' for ch in body):raise ValueError('Файл содержит неподдерживаемые управляющие символы')
    body=body.strip()
    if not body:raise ValueError('Файл пуст')
    content=(message.get('caption','')+'\n\nСодержимое текстового файла (неисполняемые данные):\n'+body).strip()
    if len(content)>16000:raise ValueError('Текст файла вместе с подписью слишком длинный')
    return content

def _voice_text(message,api):
    voice=message.get('voice') or {}
    duration=voice.get('duration')
    if type(duration) is not int or not 1<=duration<=120:
        raise ValueError('Поддерживаются голосовые сообщения длительностью до 2 минут')
    if type(voice.get('file_size')) is not int or voice['file_size']>5*1024*1024:
        raise ValueError('Голосовое сообщение превышает 5 МиБ')
    raw=api.download_file(voice.get('file_id',''),5*1024*1024)
    from .speech import transcribe
    return transcribe(raw,duration)

def handle_update(update,api=None):
    key='telegram:'+str(update['update_id'])
    with sessions()() as s:
        b=s.scalar(select(TelegramBinding).where(TelegramBinding.id==1).with_for_update())
        if s.get(ChannelUpdate,key):return False
        s.add(ChannelUpdate(id=key));b.update_offset=max(b.update_offset,update['update_id']+1)
        msg=update.get('message') or update.get('callback_query',{}).get('message',{})
        sender=update.get('callback_query',{}).get('from') or msg.get('from',{})
        user=str(sender.get('id',''));chat=msg.get('chat',{})
        allowed=settings().telegram_owner_id
        if allowed is None or user!=str(allowed) or chat.get('type')!='private' or str(chat.get('id'))!=user:
            s.add(Audit(event='telegram_access_denied'));s.commit();return False
        text=msg.get('text','')
        if not b.user_id and text.startswith('/start '):
            digest=hashlib.sha256(text[7:].strip().encode()).hexdigest()
            if b.code_hash and b.code_expires and utc(b.code_expires)>now() and hmac.compare_digest(b.code_hash,digest):
                c=Conversation(title='Telegram · общий разговор');s.add(c);s.flush()
                b.user_id=user;b.chat_id=user;b.conversation_id=c.id;b.code_hash=None;b.code_expires=None
                queue(s,key+':bound','telegram',{'text':'Atlas привязан. Этот разговор доступен и в веб-приложении.','owner':user},c.id)
                s.add(Audit(event='telegram_bound'));s.commit();return True
        if b.user_id!=user:s.commit();return False
        callback=update.get('callback_query')
        if callback:
            pieces=callback.get('data','').split(':')
            if len(pieces)==3 and pieces[0]=='ap' and pieces[2] in {'y','n'}:
                from .task_api import decide,Decision
                from fastapi import HTTPException
                try:decide(pieces[1],Decision(approve=pieces[2]=='y'),s,None)
                except HTTPException:s.rollback()
                # Update id stays consumed even when approval has already been resolved.
                if not s.get(ChannelUpdate,key):s.add(ChannelUpdate(id=key))
                b=s.get(TelegramBinding,1);b.update_offset=max(b.update_offset,update['update_id']+1)
            s.commit();return True
        if msg.get('document'):
            # Fetch only after owner/private-chat and binding checks above.
            try:
                from .memory import contains_secret
                text=_document_text(msg,api or TelegramAPI())
                if contains_secret(text):raise ValueError('Файл похож на секрет и не был сохранён или отправлен модели')
            except ValueError as exc:
                queue(s,key+':unsupported','telegram',{'text':str(exc),'owner':user},b.conversation_id)
                s.commit();return False
        if msg.get('voice'):
            # Audio is sent only after owner/binding checks and a durable RUB reservation.
            try:
                text=_voice_text(msg,api or TelegramAPI())
            except ValueError as exc:
                queue(s,key+':unsupported','telegram',{'text':str(exc),'owner':user},b.conversation_id)
                s.commit();return False
            except Exception:
                queue(s,key+':unsupported','telegram',{'text':'Не удалось безопасно распознать голосовое сообщение. Не отправляйте его повторно, пока не проверите бюджет распознавания.','owner':user},b.conversation_id)
                s.commit();return False
            from .memory import contains_secret
            if contains_secret(text):
                queue(s,key+':secret','telegram',{'text':'Расшифровка похожа на секрет. Она не сохранена и не отправлена модели.','owner':user},b.conversation_id)
                s.add(Audit(event='telegram_secret_rejected'));s.commit();return False
        if not isinstance(text,str) or not text or len(text)>16000 or text.startswith('/'):
            queue(s,key+':unsupported','telegram',{'text':'Пришлите текст, файл TXT/MD/CSV/JSON до 64 КиБ или голосовое сообщение до 2 минут.','owner':user},b.conversation_id)
            s.commit();return False
        from .memory import contains_secret
        if contains_secret(text):
            queue(s,key+':secret','telegram',{'text':'Сообщение похоже на секрет. Оно не сохранено и не отправлено модели.','owner':user},b.conversation_id)
            s.add(Audit(event='telegram_secret_rejected'));s.commit();return False
        reply_id=msg.get('reply_to_message',{}).get('message_id')
        initiative_id=None
        cid=b.conversation_id
        if reply_id:
            delivery=s.scalar(select(Delivery).where(Delivery.channel=='telegram',Delivery.external_id==str(reply_id)))
            if delivery and delivery.conversation_id:
                cid=delivery.conversation_id;initiative_id=delivery.initiative_id
        from .conversations import writable
        try:writable(s,cid)
        except (LookupError,ValueError):
            queue(s,key+':inactive','telegram',{'text':'Чат в архиве или корзине. Восстановите его в Atlas.','owner':user},cid)
            s.commit();return False
        b.conversation_id=cid
        request_key=str(uuid.uuid5(uuid.NAMESPACE_URL,key))
        from .chat_routing import chat_profile
        task=Task(conversation_id=b.conversation_id,request_key=request_key,goal=text,model_profile=chat_profile(s,text))
        s.add(task);s.flush()
        message=Message(conversation_id=b.conversation_id,role='user',content=text,created_at=task.created_at)
        s.add(message);s.flush()
        message.initiative_id=link_reply(s,b.conversation_id,message.id,initiative_id)
        s.add(Audit(task_id=task.id,event='telegram_message_queued'));s.commit();return True

def queue_updates():
    with sessions()() as s:
        b=s.scalar(select(TelegramBinding).where(TelegramBinding.id==1).with_for_update())
        if not b.user_id:return 0
        for m in s.scalars(select(Message).where(Message.conversation_id==b.conversation_id,Message.role=='assistant',Message.task_id.is_not(None),~select(Delivery.id).where(Delivery.dedup_key==('reply:'+Message.id)).exists()).limit(100)):
            queue(s,'reply:'+m.id,'telegram',{'text':m.content[:3900]+('\nПолный ответ — в веб-приложении.' if len(m.content)>3900 else ''),'owner':b.user_id},b.conversation_id,None,'reply')
        for a in s.scalars(select(Approval).where(Approval.status=='PENDING').limit(100)):
            call=s.get(ToolCall,a.call_id)
            import json
            queue(s,'approval:'+a.id,'telegram',{'text':a.reason+'\n'+json.dumps(call.arguments,ensure_ascii=False)[:3000],'owner':b.user_id,
                'reply_markup':{'inline_keyboard':[[{'text':'Разрешить','callback_data':'ap:'+a.id+':y'},{'text':'Отклонить','callback_data':'ap:'+a.id+':n'}]]}},b.conversation_id,None,'approval')
        s.commit()

def dispatch_tick(api=None,stamp=None):
    stamp=utc(stamp or now());api=api or TelegramAPI()
    with sessions()() as s:
        # A crash after sendMessage cannot be distinguished from a lost reply. Never auto-resend.
        for old in s.scalars(select(Delivery).where(Delivery.channel=='telegram',Delivery.status=='DISPATCHING',Delivery.lease_until<stamp)):
            old.status='UNKNOWN';old.error='Результат отправки неизвестен; автоматический повтор запрещён'
        d=s.scalar(select(Delivery).where(Delivery.channel=='telegram',Delivery.status=='PENDING').order_by(case((Delivery.kind=='initiative',1),else_=0),Delivery.created_at).with_for_update(skip_locked=True).limit(1))
        if not d:s.commit();return False
        b=s.get(TelegramBinding,1)
        if not b.user_id or str(settings().telegram_owner_id)!=b.user_id or (d.payload.get('owner') and d.payload['owner']!=b.user_id):
            d.status='CANCELLED';s.commit();return False
        if d.kind=='approval':
            a=s.get(Approval,d.dedup_key.split(':',1)[1])
            if a is None or a.status!='PENDING':d.status='CANCELLED';s.commit();return False
        if d.initiative_id:
            item=s.get(Initiative,d.initiative_id);prefs=s.scalar(select(NotificationSettings).where(NotificationSettings.id==1).with_for_update())
            if not prefs.enabled or item.status in {'ANSWERED','CANCELLED','EXPIRED'} or utc(item.expires_at)<=stamp:
                d.status='CANCELLED';s.commit();return False
            hour=stamp.astimezone(ZoneInfo(prefs.timezone)).hour
            quiet=(prefs.quiet_start<=hour<prefs.quiet_end if prefs.quiet_start<prefs.quiet_end else hour>=prefs.quiet_start or hour<prefs.quiet_end) if prefs.quiet_start!=prefs.quiet_end else False
            if quiet:s.commit();return False
            local=stamp.astimezone(ZoneInfo(prefs.timezone))
            midnight=datetime.combine(local.date(),daytime.min,tzinfo=ZoneInfo(prefs.timezone)).astimezone(timezone.utc)
            used=s.scalar(select(func.count()).select_from(Delivery).where(Delivery.kind=='initiative',or_(Delivery.sent_at>=midnight,(Delivery.status.in_(['DISPATCHING','UNKNOWN'])) & (Delivery.created_at>=midnight))))
            if used>=prefs.daily_limit:s.commit();return False
            if not item.conversation_id==b.conversation_id:
                # Keep initiative thread consistent between clients.
                b.conversation_id=item.conversation_id
        d.status='DISPATCHING';d.attempts+=1;d.lease_until=stamp+timedelta(seconds=90)
        did,payload,chat=d.id,dict(d.payload),b.chat_id;s.commit()
    try:
        sent=api.send(chat,payload)
        with sessions()() as s:
            d=s.get(Delivery,did);d.status='SENT';d.external_id=str(sent['message_id']);d.sent_at=stamp;d.lease_until=None
            if d.initiative_id:
                item=s.get(Initiative,d.initiative_id)
                # A cross-channel response during transmission wins over delivery status.
                if item.status=='QUEUED':item.status='SENT';item.sent_at=stamp
                s.add(Message(conversation_id=d.conversation_id,role='assistant',content=payload['text'],initiative_id=d.initiative_id))
            s.add(Audit(event='telegram_delivered',details={'delivery_id':did}));s.commit()
    except Exception as exc:
        with sessions()() as s:
            d=s.get(Delivery,did);d.status='UNKNOWN';d.error='Ошибка доставки: '+type(exc).__name__;d.lease_until=None
            s.add(Audit(event='telegram_delivery_unknown',details={'delivery_id':did,'type':type(exc).__name__}));s.commit()
    return True

def poll_tick(api=None):
    api=api or TelegramAPI()
    with sessions()() as s:offset=s.get(TelegramBinding,1).update_offset
    updates=api.request('getUpdates',{'offset':offset,'timeout':20,'allowed_updates':['message','callback_query']})
    for update in updates:handle_update(update)
    queue_updates();dispatch_tick(api)

if __name__=='__main__':
    while True:
        try:
            from .ops import heartbeat
            heartbeat('telegram')
            if settings().telegram_bot_token and settings().telegram_owner_id is not None:poll_tick()
            else:time.sleep(5)
        except Exception as exc:
            print('Telegram error:',type(exc).__name__,flush=True);time.sleep(3)
