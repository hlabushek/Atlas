"""Generic encrypted push; ambiguous dispatch is never automatically repeated."""
import base64,json
from urllib.parse import urlsplit
from sqlalchemy import select,update
from .config import settings
from .db import sessions
from .models import PushSubscription,PushDelivery,Initiative,NotificationSettings,now
from .notifications import utc

def validate(endpoint,keys):
    u=urlsplit(endpoint)
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.hostname not in settings().push_hosts.split(',') or u.fragment:raise ValueError('Endpoint prohibited')
    if set(keys)!={'auth','p256dh'}:raise ValueError('Keys required')
    try:
        decode=lambda v:base64.urlsafe_b64decode(v+'='*(-len(v)%4))
        if len(decode(keys['auth']))!=16 or len(decode(keys['p256dh']))!=65 or decode(keys['p256dh'])[0]!=4:raise ValueError('Invalid keys')
        from cryptography.hazmat.primitives.asymmetric import ec
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),decode(keys['p256dh']))
    except Exception:raise ValueError('Invalid keys')

def send(subscription):
    import requests
    from pywebpush import webpush
    class NoRedirect(requests.Session):
        def request(self,*args,**kwargs):
            kwargs['allow_redirects']=False
            response=super().request(*args,**kwargs)
            if 300<=response.status_code<400:raise ValueError('Push redirect prohibited')
            return response
    validate(subscription.endpoint,subscription.keys)
    cfg=settings()
    with NoRedirect() as transport:
        webpush(subscription_info={'endpoint':subscription.endpoint,'keys':subscription.keys},data=json.dumps({'title':'Atlas','body':'Новая инициатива. Откройте Atlas.'},ensure_ascii=False),vapid_private_key=cfg.vapid_private_key,vapid_claims={'sub':cfg.vapid_subject},requests_session=transport,timeout=10)

def tick(sender=None):
    cfg=settings()
    if not(cfg.vapid_private_key and cfg.vapid_public_key and cfg.vapid_subject):return 0
    from zoneinfo import ZoneInfo
    with sessions()() as s:
        s.execute(update(NotificationSettings).where(NotificationSettings.id==1).values(enabled=NotificationSettings.enabled))
        prefs=s.scalar(select(NotificationSettings).where(NotificationSettings.id==1).with_for_update())
        if not prefs.enabled:return 0
        h=now().astimezone(ZoneInfo(prefs.timezone)).hour
        quiet=(prefs.quiet_start<=h<prefs.quiet_end if prefs.quiet_start<prefs.quiet_end else h>=prefs.quiet_start or h<prefs.quiet_end) if prefs.quiet_start!=prefs.quiet_end else False
        if quiet:return 0
        items=s.scalars(select(Initiative).where(Initiative.status=='SENT',Initiative.sent_at>=prefs.enabled_at)).all()
        for i in items:
            if utc(i.expires_at)<=now():continue
            for sub in s.scalars(select(PushSubscription).where(PushSubscription.enabled==True)):
                if not s.scalar(select(PushDelivery.id).where(PushDelivery.initiative_id==i.id,PushDelivery.subscription_id==sub.id)):
                    s.add(PushDelivery(initiative_id=i.id,subscription_id=sub.id))
        s.commit()
    with sessions()() as s:
        d=s.scalar(select(PushDelivery).where(PushDelivery.status=='PENDING').with_for_update(skip_locked=True).limit(1))
        if d is None:return 0
        identifier=d.id
        won=s.execute(update(PushDelivery).where(PushDelivery.id==identifier,PushDelivery.status=='PENDING').values(status='UNKNOWN'))
        s.commit()
        if won.rowcount!=1:return 0 # crash after intent remains ambiguous
    with sessions()() as s:
        d=s.get(PushDelivery,identifier)
        sub=s.scalar(select(PushSubscription).where(PushSubscription.id==d.subscription_id).with_for_update())
        i=s.get(Initiative,d.initiative_id)
        prefs=s.scalar(select(NotificationSettings).where(NotificationSettings.id==1).with_for_update())
        h=now().astimezone(ZoneInfo(prefs.timezone)).hour
        quiet=(prefs.quiet_start<=h<prefs.quiet_end if prefs.quiet_start<prefs.quiet_end else h>=prefs.quiet_start or h<prefs.quiet_end) if prefs.quiet_start!=prefs.quiet_end else False
        if not sub.enabled or not prefs.enabled or quiet or i.status!='SENT' or utc(i.expires_at)<=now():d.status='CANCELLED'
        else:
            try:(sender or send)(sub);d.status='SENT'
            except Exception as exc:
                if getattr(getattr(exc,'response',None),'status_code',None) in (404,410):sub.enabled=False;d.status='EXPIRED'
        s.commit()
    return 1
