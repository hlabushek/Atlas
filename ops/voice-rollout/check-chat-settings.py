"""Приёмка HTTPS с временной owner-сессией. Без LLM, микрофона и содержимого истории."""
import httpx
from sqlalchemy import text
from atlas.config import settings
from atlas.db import sessions
from atlas.models import ModelPreferences


def main():
    base='https://atlas.prism-news.xyz'
    with sessions()() as session:
        assert session.scalar(text('SELECT version_num FROM alembic_version'))=='0017'
        preferences=session.get(ModelPreferences,1)
        assert preferences.config['main']['model']=='deepseek/deepseek-v4-flash'
        assert preferences.config['planner']['model']=='deepseek/deepseek-v4.1-flash'
        print('PASS: schema 0017; dual models retained; revision '+str(preferences.revision))
    with httpx.Client(base_url=base,timeout=15,follow_redirects=True) as client:
        for path in ['/api/spaces','/api/spaces/missing','/api/voice/center','/api/model-settings','/api/maintenance/demo','/api/conversations/missing']:
            assert client.get(path).status_code==401
        assert client.get('/api/health').json()['status']=='ok'
        assert client.get('/about').status_code==200
        assert client.post('/api/login',json={'password':settings().owner_password}).status_code==200
        try:
            assert client.get('/api/spaces').status_code==200
            assert client.get('/api/conversations').status_code==200
            assert client.get('/api/voice/center').status_code==200
            assert client.get('/api/model-settings').status_code==200
            assert client.get('/api/maintenance/demo').json()['counts']=={}
            chats=client.get('/api/conversations').json()
            if chats:
                metadata=client.get('/api/conversations/'+chats[0]['id']).json()
                assert {'description','pinned','archived','trashed_at','revision'}<=metadata.keys()
            for scope in ['archive','trash']:
                assert client.get('/api/conversations?scope='+scope).status_code==200
            assert client.post('/api/maintenance/demo/reset',json={'digest':'a'*64,'confirmation':'СБРОСИТЬ ДЕМО'}).status_code==403
            assert client.post('/api/spaces',json={'name':'Не создавать'}).status_code==403
            print('PASS: actual HTTPS public routes, owner reads and CSRF; no provider calls or created spaces')
        finally:
            assert client.post('/api/logout',headers={'X-Atlas-Client':'web'}).status_code==200
            assert client.get('/api/spaces').status_code==401
            print('PASS: diagnostic owner session revoked')


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('FAIL: '+type(error).__name__)
        raise SystemExit(1)
