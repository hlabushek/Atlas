import httpx
import pytest
from atlas.config import settings
from atlas.router import ModelRouter

def test_compatible_provider_has_bounded_context(client,monkeypatch):
    monkeypatch.setenv('ATLAS_MODEL_PROVIDER','openai-compatible')
    monkeypatch.setenv('ATLAS_MODEL_URL','https://model.example/v1')
    monkeypatch.setenv('ATLAS_MODEL_NAME','test-model')
    monkeypatch.setenv('ATLAS_MODEL_API_KEY','test-secret')
    settings.cache_clear()
    captured={}
    class FakeClient:
        def __init__(self,**kw): captured['options']=kw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,url,**kw):
            captured.update(url=url,**kw)
            return httpx.Response(200,json={'choices':[{'message':{'content':'Реальный формат ответа'}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'Client',FakeClient)
    result=ModelRouter().answer('цель',[{'role':'user','content':str(i)} for i in range(30)],['контекст'])
    assert result=='Реальный формат ответа'
    assert captured['url']=='https://model.example/v1/chat/completions'
    assert captured['options']['follow_redirects'] is False
    assert len(captured['json']['messages'])==15
    assert 'test-secret' not in str(captured['json'])

def test_missing_secrets_fail_closed(client,monkeypatch):
    from atlas.config import Settings
    monkeypatch.setenv('ATLAS_OWNER_PASSWORD','')
    with pytest.raises(ValueError):Settings(_env_file=None)
