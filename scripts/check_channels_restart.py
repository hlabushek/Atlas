"""Compose drill for durable initiative delivery and cross-session response."""
import time,uuid,subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
import httpx
values=dict(line.split('=',1) for line in Path('.env').read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
with httpx.Client(base_url='http://127.0.0.1:8080',timeout=30,headers={'X-Atlas-Client':'web'}) as c:
    c.post('/api/login',json={'password':values['ATLAS_OWNER_PASSWORD']}).raise_for_status()
    original=c.get('/api/notifications/settings').json()
    try:
        c.put('/api/notifications/settings',json={**original,'enabled':True,'timezone':'UTC','quiet_start':0,'quiet_end':0,'daily_limit':30,'channel':'web'}).raise_for_status()
        item=c.post('/api/initiatives',json={'message':'RESTART INITIATIVE '+str(uuid.uuid4()),'due_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}).json()
        def wait(status):
            for _ in range(40):
                row=next(x for x in c.get('/api/initiatives').json() if x['id']==item['id'])
                if row['status']==status:return
                time.sleep(.5)
            raise AssertionError('Initiative did not reach '+status)
        wait('SENT')
        subprocess.run(['docker','compose','restart','scheduler'],check=True,capture_output=True)
        time.sleep(3)
        deliveries=[d for d in c.get('/api/deliveries').json() if d['initiative_id']==item['id']]
        assert len(deliveries)==1 and deliveries[0]['status']=='SENT'
        c.post('/api/conversations/'+item['conversation_id']+'/messages',json={'content':'Принято в другом клиенте','request_key':str(uuid.uuid4()),'initiative_id':item['id']}).raise_for_status()
        wait('ANSWERED')
        subprocess.run(['docker','compose','restart','scheduler'],check=True,capture_output=True)
        time.sleep(3)
        assert len([d for d in c.get('/api/deliveries').json() if d['initiative_id']==item['id']])==1
        print('PASS: scheduler restart preserved one delivery and response stopped reminders')
    finally:
        c.put('/api/notifications/settings',json={**original,'enabled':False}).raise_for_status()
