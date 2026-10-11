from types import SimpleNamespace

from atlas import connectors
from atlas.db import sessions
from atlas.skills import catalog
from atlas.public_connectors import extract, extract_json, tick
from atlas.config import settings
from atlas.models import ConnectorSource, ToolGrant, now
from datetime import timedelta

def recipe():
    return {"rows":"article.card","key":"id","fields":{"id":".id","title":"h2","status":".status"}}

def create(client, name="Источники тестов", cid="demo-source"):
    return client.post("/api/connectors",json={"id":cid,"name":name,"origin":"https://example.org/tasks","recipe":recipe()})

def test_ai_draft_is_validated_and_not_executed_as_code(client,monkeypatch):
    def fake_draft(request):
        assert request.request=="Собрать фотографии"
        return recipe()
    monkeypatch.setattr(connectors,"draft_recipe",fake_draft)
    response=client.post("/api/connectors/draft",json={"page_url":"https://example.org/tasks?private=x","page_title":"Tasks","outline":"{\"headings\":[{\"label\":\"Tasks\"}]}","request":"Собрать фотографии"})
    assert response.status_code==200
    assert response.json()["origin"]=="https://example.org/tasks"
    assert response.json()["recipe"]==recipe()
    assert "javascript" not in str(response.json()).lower()

def test_connector_import_is_idempotent_listed_and_read_tool_is_denied_by_default(client):
    assert create(client).status_code==200
    batch={"records":[{"id":"task-1","title":"Прочитать главу","status":"до пятницы"}]}
    first=client.post("/api/connectors/demo-source/import",json=batch)
    second=client.post("/api/connectors/demo-source/import",json=batch)
    assert first.json()=={"count":1,"new":1,"changed":0}
    assert second.json()=={"count":1,"new":0,"changed":0}
    listing=client.get("/api/connectors").json()
    assert listing[0]["records"]==1 and listing[0]["origin"]=="https://example.org/tasks"
    tool_name="connector.demo-source.read"
    assert any(t["name"]==tool_name and t["blocked"] for t in client.get("/api/tools").json())
    assert client.put("/api/tools/connector.demo-source.read/permission",json={"allowed":True}).status_code==200
    assert not next(t for t in client.get("/api/tools").json() if t["name"]==tool_name)["blocked"]
    with sessions()() as session:
        tool=catalog(session)[tool_name]
        result=tool.execute(session,SimpleNamespace(arguments={"query":"прочитать","limit":10}))
    assert result["count"]==1 and result["records"][0]["title"]=="Прочитать главу"
    assert result["source"]=="https://example.org/tasks"

def test_connector_rejects_non_https_unsafe_css_and_secret_values(client):
    bad={"id":"unsafe-source","name":"Источник","origin":"http://example.org/tasks","recipe":recipe()}
    assert client.post("/api/connectors",json=bad).status_code==422
    bad={"id":"unsafe-source","name":"Источник","origin":"https://example.org/tasks","recipe":{**recipe(),"rows":"article:has(script)"}}
    assert client.post("/api/connectors",json=bad).status_code==422
    assert create(client).status_code==200
    payload={"records":[{"id":"task-1","title":"api_key=abcdefgh1234567890"}]}
    assert client.post("/api/connectors/demo-source/import",json=payload).status_code==422

def test_connector_rejects_duplicate_keys_within_one_import(client):
    assert create(client).status_code==200
    row={"id":"same","title":"Одна запись"}
    response=client.post("/api/connectors/demo-source/import",json={"records":[row,row]})
    assert response.status_code==422

def test_disabling_connector_revokes_agent_permission_and_reads(client):
    assert create(client).status_code==200
    assert client.post('/api/connectors/demo-source/import',json={'records':[{'id':'1','title':'Сохранено'}]}).status_code==200
    tool_name="connector.demo-source.read"
    assert client.put("/api/tools/"+tool_name+"/permission",json={"allowed":True}).status_code==200
    assert client.put("/api/connectors/demo-source",json={"enabled":False}).json()=={"id":"demo-source","enabled":False}
    assert all(t["name"]!=tool_name for t in client.get("/api/tools").json())
    assert client.post("/api/connectors/demo-source/import",json={"records":[{"id":"1","title":"x"}]}).status_code==404
    assert client.get('/api/connectors/demo-source/records').json()['records'][0]['data']['title']=='Сохранено'

def test_connector_recipe_can_be_extended_without_losing_records(client):
    assert create(client).status_code==200
    assert client.post("/api/connectors/demo-source/import",json={"records":[{"id":"1","title":"Первая"}]}).status_code==200
    expanded={**recipe(),"fields":{**recipe()["fields"],"image_url":"img.photo"}}
    response=client.put("/api/connectors/demo-source/recipe",json={"recipe":expanded})
    assert response.status_code==200 and response.json()["recipe"]==expanded
    assert client.get("/api/connectors/demo-source/records").json()["records"][0]["data"]["title"]=="Первая"
    changed={**expanded,"key":"title"}
    assert client.put("/api/connectors/demo-source/recipe",json={"recipe":changed}).status_code==409

def test_photo_address_must_not_carry_query_credentials(client):
    assert create(client).status_code==200
    expanded={**recipe(),"fields":{**recipe()["fields"],"image_url":"img.photo"}}
    assert client.put("/api/connectors/demo-source/recipe",json={"recipe":expanded}).status_code==200
    base={"id":"1","title":"Фото"}
    for url in ("https://example.org/photo.jpg?token=secret", "http://example.org/photo.jpg"):
        assert client.post("/api/connectors/demo-source/import",json={"records":[{**base,"image_url":url}]}).status_code==422
    assert client.post("/api/connectors/demo-source/import",json={"records":[{**base,"image_url":"https://example.org/photo.jpg"}]}).status_code==200

def test_public_html_extracts_visible_records_and_photo_url():
    html=b'<article class="card"><span class="id">one</span><h2>Photo</h2><img class="photo" src="/one.jpg"><span class="status" hidden>secret</span></article>'
    expanded={**recipe(),"fields":{**recipe()["fields"],"image_url":"img.photo"}}
    assert extract(html,"https://example.org/tasks",expanded)==[{"id":"one","title":"Photo","image_url":"https://example.org/one.jpg"}]

def test_public_json_flattens_albums_and_uses_stable_photo_urls(client):
    manifest={"id":"photo-source","name":"Фотографии","origin":"https://example.org/data.json","recipe":{
        "kind":"public_json","collection":"albums","items":"photos","key":"image_url",
        "fields":{"image_url":"full","title":"alt","album":"@parent.ru"}}}
    assert client.post("/api/connectors",json=manifest).status_code==200
    data='{"albums":[{"ru":"Город","photos":[{"full":"/one.webp","alt":"Улица"},{"full":"/two.webp"}]}]}'.encode()
    rows=extract_json(data,manifest["origin"],manifest["recipe"])
    assert rows==[
        {"image_url":"https://example.org/one.webp","title":"Улица","album":"Город"},
        {"image_url":"https://example.org/two.webp","title":"Город · two","album":"Город"},
    ]
    assert client.post("/api/connectors/photo-source/import",json={"records":rows}).json()["new"]==2
    unsafe={**manifest,"id":"unsafe-json","recipe":{**manifest["recipe"],"items":"../secrets"}}
    assert client.post("/api/connectors",json=unsafe).status_code==422

def test_public_schedule_is_opt_in_and_imports_idempotently(client,monkeypatch):
    from atlas import public_connectors
    assert create(client).status_code==200
    assert client.put("/api/connectors/demo-source/schedule",json={"minutes":60}).status_code==422
    monkeypatch.setenv("ATLAS_INTEGRATION_HOSTS","example.org")
    settings.cache_clear()
    monkeypatch.setattr(public_connectors,"fetch_public",lambda source:[{"id":"one","title":"С сайта"}])
    assert client.post("/api/connectors/demo-source/public-preview").json()["first_records"][0]["title"]=="С сайта"
    assert client.put("/api/connectors/demo-source/schedule",json={"minutes":60}).status_code==200
    assert tick(now()+timedelta(minutes=61))==1
    assert client.get("/api/connectors/demo-source/records").json()["records"][0]["data"]["title"]=="С сайта"
    assert client.get("/api/connectors").json()[0]["last_run_status"]=="OK"
    def unavailable(_source):raise RuntimeError("Недоступно")
    monkeypatch.setattr(public_connectors,"fetch_public",unavailable)
    assert tick(now()+timedelta(minutes=122))==0
    assert client.get("/api/connectors").json()[0]["last_run_status"]=="FAILED"
    assert client.get("/api/connectors/demo-source/records").json()["records"][0]["data"]["title"]=="С сайта"
    assert client.put("/api/connectors/demo-source/schedule",json={"minutes":None}).status_code==200
    assert tick(now()+timedelta(days=2))==0

def test_recipe_change_revokes_read_and_schedule(client,monkeypatch):
    from atlas import public_connectors
    assert create(client).status_code==200
    monkeypatch.setenv("ATLAS_INTEGRATION_HOSTS","example.org")
    settings.cache_clear()
    monkeypatch.setattr(public_connectors,"fetch_public",lambda source:[])
    assert client.put("/api/tools/connector.demo-source.read/permission",json={"allowed":True}).status_code==200
    assert client.put("/api/connectors/demo-source/schedule",json={"minutes":60}).status_code==200
    changed={**recipe(),"fields":{**recipe()["fields"],"extra":"p.extra"}}
    assert client.put("/api/connectors/demo-source/recipe",json={"recipe":changed}).status_code==200
    row=client.get("/api/connectors").json()[0]
    assert row["schedule_active"] is False and row["schedule_minutes"] is None
    tool=next(t for t in client.get("/api/tools").json() if t["name"]=="connector.demo-source.read")
    assert tool["blocked"] is True
    assert tick(now()+timedelta(days=1))==0

def test_expired_connector_permissions_fail_closed(client,monkeypatch):
    from atlas import public_connectors
    assert create(client).status_code==200
    monkeypatch.setenv("ATLAS_INTEGRATION_HOSTS","example.org")
    settings.cache_clear()
    monkeypatch.setattr(public_connectors,"fetch_public",lambda source:[])
    assert client.put("/api/tools/connector.demo-source.read/permission",json={"allowed":True,"days":1}).status_code==200
    assert client.put("/api/connectors/demo-source/schedule",json={"minutes":60,"days":1}).status_code==200
    with sessions()() as session:
        session.get(ToolGrant,"connector.demo-source.read").expires_at=now()-timedelta(minutes=1)
        session.get(ConnectorSource,"demo-source").schedule_expires_at=now()-timedelta(minutes=1)
        session.commit()
    tool=next(t for t in client.get("/api/tools").json() if t["name"]=="connector.demo-source.read")
    assert tool["blocked"] is True
    assert client.get("/api/connectors").json()[0]["schedule_active"] is False
    assert tick(now()+timedelta(hours=2))==0
    assert client.get("/api/connectors").json()[0]["schedule_minutes"] is None

def test_poll_discarded_when_permission_revoked_during_fetch(client,monkeypatch):
    from atlas import public_connectors
    assert create(client).status_code==200
    monkeypatch.setenv("ATLAS_INTEGRATION_HOSTS","example.org")
    settings.cache_clear()
    monkeypatch.setattr(public_connectors,"fetch_public",lambda source:[])
    assert client.put("/api/connectors/demo-source/schedule",json={"minutes":60}).status_code==200
    def revoke_during_fetch(source):
        with sessions()() as session:
            row=session.get(ConnectorSource,source.id)
            row.schedule_minutes=None
            row.schedule_fingerprint=None
            row.schedule_expires_at=None
            row.next_run_at=None
            session.commit()
        return [{"id":"late","title":"Не сохранять"}]
    monkeypatch.setattr(public_connectors,"fetch_public",revoke_during_fetch)
    assert tick(now()+timedelta(minutes=61))==0
    assert client.get("/api/connectors/demo-source/records").json()["records"]==[]
