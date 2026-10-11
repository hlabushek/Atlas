"""Read-only HTTPS verification; print no school data or credentials."""
import os
import httpx


def main():
    with httpx.Client(base_url='https://atlas.prism-news.xyz',timeout=20) as client:
        health=client.get('/api/health')
        anonymous=client.get('/api/diary/day',params={'day':'2026-10-05'})
        login=client.post('/api/login',json={'password':os.environ['ATLAS_OWNER_PASSWORD']})
        if health.status_code!=200 or anonymous.status_code!=401 or login.status_code!=200:
            raise RuntimeError('Health, access policy or owner login failed')
        client.headers['X-Atlas-Client']='web'
        diary=client.get('/api/diary')
        day=client.get('/api/diary/day',params={'day':'2026-10-05'})
        days=client.get('/api/diary/days',params={'start':'2026-10-05','count':2})
        if any(x.status_code!=200 for x in (diary,day,days)):
            raise RuntimeError('Diary read failed')
        summary=diary.json()
        detail=day.json()
        if detail.get('date')!='2026-10-05' or len(days.json().get('days',[]))!=2:
            raise RuntimeError('Diary response invalid')
        if not isinstance(summary.get('assignments'),list) or not isinstance(detail.get('lessons'),list):
            raise RuntimeError('Diary shape invalid')
        print('PASS: HTTPS health 200, anonymous diary 401, owner login 200, diary/day/days 200')
        print('Saved assignments:',len(summary['assignments']),'schedule days:',len(summary.get('schedule_available_days',[])))


if __name__=='__main__':main()
