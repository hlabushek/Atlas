"""Приёмка production только чтением: без модели и содержимого личных файлов."""
import httpx
from sqlalchemy import text
from atlas.config import settings
from atlas.db import sessions
from atlas.models import ModelPreferences


def main():
    with sessions()() as s:
        assert s.scalar(text('SELECT version_num FROM alembic_version')) == '0018'
        row=s.get(ModelPreferences,1)
        assert row.config['main']['model']=='deepseek/deepseek-v4-flash'
        assert row.config['planner']['model']=='deepseek/deepseek-v4.1-flash'
        print('PASS: schema 0018; existing dual-model settings retained; revision '+str(row.revision))
    with httpx.Client(base_url='https://atlas.prism-news.xyz',timeout=20,follow_redirects=False) as client:
        for path in ['/api/files', '/api/files/capabilities', '/api/files/missing/image', '/api/files/missing/preview']:
            assert client.get(path).status_code==401
        assert client.post('/api/login',json={'password':settings().owner_password}).status_code==200
        try:
            caps=client.get('/api/files/capabilities');assert caps.status_code==200
            data=caps.json();assert data['vision'] and not data['ocr'] and data['max_files']==5
            assert not data['roles']['main']['vision'] and data['roles']['planner']['vision']
            files=client.get('/api/files');assert files.status_code==200
            assert all('original' not in a and 'image' not in a and 'content' not in a for a in files.json())
            assert client.post('/api/files',files={'file':('do-not-create.txt',b'fixture')}).status_code==403
            print('PASS: real HTTPS, capabilities/catalog, private file metadata and CSRF; no uploads/provider calls')
        finally:
            assert client.post('/api/logout',headers={'X-Atlas-Client':'web'}).status_code==200
            assert client.get('/api/files').status_code==401
            print('PASS: diagnostic owner session revoked')


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('FAIL: '+type(error).__name__)
        raise SystemExit(1)
