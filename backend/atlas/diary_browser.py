"""Concrete configurable read-only browser adapter; site-specific selectors supplied by owner."""
import json,os
from pathlib import Path
from datetime import datetime
from .diary import DiaryProvider,Homework
from .network import endpoint
from .config import settings
from urllib.parse import urlsplit

class PlaywrightDiaryProvider(DiaryProvider):
    def __init__(self,url,selectors,encrypted_session_path):
        self.url=url;self.selectors=selectors;self.path=Path(encrypted_session_path)
    def state(self):
        from cryptography.fernet import Fernet
        key=os.environ.get('ATLAS_DIARY_SESSION_KEY','')
        if not key:raise ValueError('Session encryption key not configured')
        return json.loads(Fernet(key.encode()).decrypt(self.path.read_bytes()))
    def homework(self,session):
        from playwright.sync_api import sync_playwright
        fields=self.selectors
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(storage_state=self.state())
                allowed={h.strip().lower() for h in settings().integration_hosts.split(',') if h.strip()}
                def route(request_route):
                    url=urlsplit(request_route.request.url)
                    if url.scheme in {'http','https'} and url.hostname and url.hostname.lower() in allowed:request_route.continue_()
                    else:request_route.abort()
                context.route('**/*',route)
                page=context.new_page();page.goto(endpoint(self.url),wait_until='domcontentloaded',timeout=20000)
                if not page.locator(fields['authorized']).count():raise ValueError('Повторная авторизация нужна; CAPTCHA/MFA не обходятся')
                result=[]
                for row in page.locator(fields['rows']).all()[:1000]:
                    due=row.locator(fields['due']).get_attribute('datetime')
                    result.append(Homework(external_id=row.get_attribute(fields.get('id_attribute','data-id')),subject=row.locator(fields['subject']).inner_text(),title=row.locator(fields['title']).inner_text(),due_at=datetime.fromisoformat(due),grade=row.locator(fields['grade']).inner_text() if fields.get('grade') else None))
                return result
            finally:browser.close()
    def schedule(self,session):
        # A site's schedule must be mapped explicitly; never invent unknown lesson data.
        raise NotImplementedError('Настройте отдельное отображение расписания конкретного сайта')
    def session_status(self):
        try:self.homework(None);return 'AUTHORIZED'
        except Exception:return 'REAUTH_REQUIRED_OR_UNAVAILABLE'
