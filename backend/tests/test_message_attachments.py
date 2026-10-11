import io
import uuid
import zipfile
from sqlalchemy import select
from atlas.db import sessions
from atlas.models import Attachment, Message, Task, ModelUsage


def docx(text):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>')
    return out.getvalue()


def pdf(text='PDF readable text', blank=False, encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter(); page = writer.add_blank_page(width=200, height=200)
    if not blank:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(('BT /F1 12 Tf 10 100 Td ('+text+') Tj ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted: writer.encrypt('fixture-pass')
    out = io.BytesIO(); writer.write(out); return out.getvalue()


def upload(client, name, raw):
    result = client.post('/api/files', files={'file': (name, raw)})
    assert result.status_code == 200, result.text
    return result.json()


def test_local_documents_original_preview_and_no_model_usage(client):
    caps = client.get('/api/files/capabilities').json()
    assert set(caps['extensions']) == {'txt', 'md', 'csv', 'json', 'pdf', 'docx', 'png', 'jpg', 'jpeg', 'webp'}
    assert not caps['ocr'] and caps['vision']
    for name, raw, text in [('пример.docx', docx('Привет из документа'), 'Привет из документа'),
                            ('example.pdf', pdf(), 'PDF readable text')]:
        f = upload(client, name, raw)
        assert f['bytes'] == len(raw) and f['warnings']
        assert text in client.get('/api/files/'+f['id']+'/preview').json()['text']
        downloaded = client.get('/api/files/'+f['id'])
        assert downloaded.content == raw and downloaded.headers['x-content-type-options'] == 'nosniff'
        assert "filename*=UTF-8''" in downloaded.headers['content-disposition']
    with sessions()() as s:
        assert not list(s.scalars(select(ModelUsage))) and not list(s.scalars(select(Task)))


def test_unreadable_unsafe_and_oversize_documents(client):
    for name, raw in [('scan.pdf', pdf(blank=True)), ('locked.pdf', pdf(encrypted=True)),
                      ('broken.pdf', b'not pdf'), ('broken.docx', b'not zip'), ('image.png', b'image'),
                      ('secret.docx', docx('password=never-save')), ('empty.txt', b'')]:
        response = client.post('/api/files', files={'file': (name, raw)})
        assert response.status_code == 422, response.text
        assert 'never-save' not in response.text
    assert client.post('/api/files', files={'file': ('big.docx', b'x' * (5*1024*1024+1))}).status_code == 413
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('word/document.xml', b'x' * 1000000)
    assert client.post('/api/files', files={'file': ('bomb.docx', out.getvalue())}).status_code == 422
    with sessions()() as s: assert not list(s.scalars(select(Attachment)))


def test_snapshots_separate_message_context_hide_and_replay(client, monkeypatch):
    f = upload(client, 'контекст.txt', 'Уникальная информация во вложении'.encode())
    cid = client.post('/api/conversations', json={}).json()['id']
    body = {'content': 'Прочитай файл', 'request_key': str(uuid.uuid4()), 'attachment_ids': [f['id']], 'model_role': 'main'}
    task = client.post('/api/conversations/'+cid+'/messages', json=body).json()
    assert task['goal'] == body['content'] and 'text' not in task['attachments'][0]
    message = client.get('/api/conversations/'+cid+'/messages').json()[0]
    assert message['content'] == body['content'] and message['attachments'][0]['name'] == f['name']
    assert client.delete('/api/files/'+f['id']).status_code == 200
    assert client.get('/api/files/'+f['id']).status_code == 404
    assert client.get('/api/files/'+f['id']+'/preview?message_id='+message['id']).status_code == 200
    assert client.get('/api/files/'+f['id']+'?message_id=wrong').status_code == 404
    assert client.post('/api/conversations/'+cid+'/messages', json=body).json()['id'] == task['id']
    assert client.post('/api/conversations/'+cid+'/messages', json={**body, 'content':'Другое'}).status_code == 409
    assert client.post('/api/conversations/'+cid+'/messages', json={**body, 'request_key':str(uuid.uuid4())}).status_code == 404
    warmed=[]; monkeypatch.setattr('atlas.embeddings.warm', lambda text: warmed.append(text))
    captured=[]
    class Router:
        def answer(self, goal, history, memories):
            captured.append((goal, history)); return 'Ответ без содержимого файла'
    from atlas.worker import tick
    assert tick(Router())
    assert 'Уникальная информация' in captured[0][0]
    assert warmed == ['Прочитай файл']
    assert client.post('/api/conversations/'+cid+'/messages', json={'content':'Что в нём?', 'request_key':str(uuid.uuid4())}).status_code == 202
    assert tick(Router())
    assert any('Уникальная информация' in m['content'] for m in captured[1][1])
    with sessions()() as s:
        assert s.get(Task, task['id']).attachments[0]['text'].startswith('Уникальная')
        assert s.get(Message, message['id']).content == body['content']


def test_files_only_dedup_and_combined_limit(client):
    a=upload(client, 'a.txt', b'a'*60000); b=upload(client, 'b.txt', b'b'*60000)
    cid=client.post('/api/conversations', json={}).json()['id']
    path='/api/conversations/'+cid+'/messages'
    assert client.post(path,json={'request_key':str(uuid.uuid4())}).status_code==422
    result=client.post(path,json={'attachment_ids':[a['id'],a['id']], 'request_key':str(uuid.uuid4())})
    assert result.status_code==202 and len(result.json()['attachments'])==1
    assert result.json()['goal']=='Проанализируй приложенные файлы'
    assert client.post(path,json={'attachment_ids':[a['id'],b['id']], 'request_key':str(uuid.uuid4())}).status_code==413
    assert client.post(path,json={'attachment_ids':['x']*6, 'request_key':str(uuid.uuid4())}).status_code==422


def test_attachment_auth_csrf(client):
    a=upload(client, 'auth.txt', b'private')
    client.headers.pop('X-Atlas-Client')
    assert client.delete('/api/files/'+a['id']).status_code==403
    assert client.post('/api/files',files={'file':('a.txt',b'a')}).status_code==403
    client.headers['X-Atlas-Client']='web'; assert client.post('/api/logout').status_code==200
    for path in ['/api/files', '/api/files/capabilities', '/api/files/'+a['id'], '/api/files/'+a['id']+'/preview']:
        assert client.get(path).status_code==401


def image_bytes(fmt='PNG', size=(120, 80)):
    from PIL import Image
    out=io.BytesIO();Image.new('RGB',size,'green').save(out,format=fmt);return out.getvalue()


def test_image_upload_preview_reencoding_and_boundaries(client):
    from PIL import Image
    for name,fmt in [('рисунок.png','PNG'),('photo.jpg','JPEG'),('picture.webp','WEBP')]:
        raw=image_bytes(fmt)
        a=upload(client,name,raw)
        assert a['mime'].startswith('image/') and a['text_bytes']==0
        assert (a['width'],a['height'])==(120,80)
        preview=client.get('/api/files/'+a['id']+'/image')
        assert preview.status_code==200 and preview.headers['content-type']=='image/jpeg'
        with Image.open(io.BytesIO(preview.content)) as img:
            assert img.size==(120,80) and not img.getexif()
        assert client.get('/api/files/'+a['id']).content==raw
    assert client.post('/api/files',files={'file':('wrong.jpg',image_bytes())}).status_code==422
    assert client.post('/api/files',files={'file':('broken.png',b'invalid')}).status_code==422
    assert client.post('/api/files',files={'file':('bomb.png',image_bytes(size=(4100,4000)))}).status_code==422


def test_vision_routing_multimodal_payload_followup_and_budget(client,monkeypatch):
    import copy
    import base64
    import httpx
    from atlas.config import settings
    from atlas.models import ModelPreferences
    from atlas.file_models import roles
    monkeypatch.setenv('ATLAS_ROUTERAI_API_KEY','fixture-only-not-a-real-credential')
    settings.cache_clear()
    with sessions()() as s:
        prefs=s.get(ModelPreferences,1); config=copy.deepcopy(prefs.config)
        config.update(mode='configured',auto_chat_routing=True,embeddings_enabled=False)
        config['main'].update(provider='routerai',model='deepseek/deepseek-v4-flash')
        config['planner'].update(provider='routerai',model='deepseek/deepseek-v4.1-flash')
        prefs.config=config;s.commit()
    monkeypatch.setattr('atlas.file_models.catalog',lambda:{'deepseek/deepseek-v4-flash':False,'deepseek/deepseek-v4.1-flash':True})
    with sessions()() as s: assert not roles(s)['main']['vision'] and roles(s)['planner']['vision']
    a=upload(client,'green.png',image_bytes());cid=client.post('/api/conversations',json={}).json()['id']
    path='/api/conversations/'+cid+'/messages'
    body={'content':'Что на картинке?', 'attachment_ids':[a['id']], 'model_role':'main', 'request_key':str(uuid.uuid4())}
    assert client.post(path,json=body).status_code==422
    body.update(model_role='auto',request_key=str(uuid.uuid4()))
    first=client.post(path,json=body);assert first.status_code==202
    profile=first.json()['model_profile'];assert profile['role']=='planner' and profile['requested_role']=='auto' and profile['vision']
    payloads=[]
    def provider(request):
        import json
        payload=json.loads(request.content);payloads.append(payload)
        assert payload['model']=='deepseek/deepseek-v4.1-flash'
        parts=[p for m in payload['messages'] if isinstance(m['content'],list) for p in m['content'] if p['type']=='image_url']
        assert len(parts)==1
        image=parts[0]['image_url']['url'];assert image.startswith('data:image/jpeg;base64,')
        assert base64.b64decode(image.split(',',1)[1]).startswith(b'\xff\xd8')
        return httpx.Response(200,json={'choices':[{'message':{'content':'Тестовый ответ транспорта, не проверка качества vision'}}],'usage':{'prompt_tokens':500,'total_tokens':510}})
    transport=httpx.MockTransport(provider);real_client=httpx.Client
    monkeypatch.setattr('atlas.router.httpx.Client',lambda **kw:real_client(transport=transport,**kw))
    from atlas.worker import tick
    from atlas.router import ModelRouter
    assert tick(ModelRouter())
    assert client.get('/api/tasks').json()[0]['status']=='COMPLETED'
    assert client.delete('/api/files/'+a['id']).status_code==200
    assert client.post(path,json={'content':'А какой цвет?', 'model_role':'auto','request_key':str(uuid.uuid4())}).status_code==202
    assert tick(ModelRouter()) and len(payloads)==2
    from atlas.budget import estimate
    encoded,tokens=estimate(payloads[0]);assert 'data:image' not in encoded and tokens>=65536
    with sessions()() as s:
        assert all(r.status=='KNOWN' for r in s.scalars(select(ModelUsage)))
    settings.cache_clear()


def test_image_capability_fail_closed(client,monkeypatch):
    from atlas.file_models import choose
    from fastapi import HTTPException
    import pytest
    with sessions()() as s:
        with pytest.raises(HTTPException):choose(s,'auto')
    a=upload(client,'image.png',image_bytes());cid=client.post('/api/conversations',json={}).json()['id']
    result=client.post('/api/conversations/'+cid+'/messages',json={'attachment_ids':[a['id']], 'model_role':'auto','request_key':str(uuid.uuid4())})
    assert result.status_code==422
