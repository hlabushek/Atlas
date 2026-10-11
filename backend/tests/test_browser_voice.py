import pytest
from starlette.websockets import WebSocketDisconnect

from atlas import browser_voice
from atlas.voice_recognition import Transcript

HEADERS={'origin':'http://testserver','host':'testserver'}


def test_browser_voice_auth_origin_and_disabled_provider(client):
    assert client.get('/api/voice/audio-status').json()['ready'] is False
    with client.websocket_connect('/api/voice/audio',headers=HEADERS) as socket:
        assert socket.receive_json()['type']=='error'
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/api/voice/audio',headers={**HEADERS,'origin':'https://evil.invalid'}):pass
    client.cookies.clear()
    assert client.get('/api/voice/audio-status').status_code==401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/api/voice/audio',headers=HEADERS):pass


def test_pcm_transcript_and_revocation(client,monkeypatch):
    monkeypatch.setattr(browser_voice,'configured',lambda:True)
    received=[]
    def recognize(data,config):
        received.append(data)
        assert config['sample_rate']==16000
        return Transcript('Проверочная фраза','ok')
    monkeypatch.setattr(browser_voice,'transcribe_pcm',recognize)
    with client.websocket_connect('/api/voice/audio',headers=HEADERS) as socket:
        assert socket.receive_json()['type']=='ready'
        socket.send_json({'type':'begin'})
        pcm=b'\0\1'*1600
        socket.send_bytes(pcm)
        socket.send_json({'type':'end'})
        assert socket.receive_json()=={'type':'transcript','text':'Проверочная фраза'}
        assert received==[pcm]
        client.post('/api/sessions/revoke')
        socket.send_json({'type':'begin'})
        with pytest.raises(WebSocketDisconnect):socket.receive_json()


@pytest.mark.parametrize('invalid',[b'odd',b'\0'*6402,{'type':'end'},{'type':'begin','extra':True},[]])
def test_invalid_audio_fails_closed(client,monkeypatch,invalid):
    monkeypatch.setattr(browser_voice,'configured',lambda:True)
    with client.websocket_connect('/api/voice/audio',headers=HEADERS) as socket:
        assert socket.receive_json()['type']=='ready'
        if isinstance(invalid,bytes):socket.send_json({'type':'begin'});socket.send_bytes(invalid)
        else:socket.send_json(invalid)
        assert socket.receive_json()['type']=='error'
        with pytest.raises(WebSocketDisconnect):socket.receive_json()


def test_secret_transcript_never_leaves_audio_channel(client,monkeypatch):
    monkeypatch.setattr(browser_voice,'configured',lambda:True)
    monkeypatch.setattr(browser_voice,'transcribe_pcm',lambda data,config:Transcript('test-owner-password-123','ok'))
    with client.websocket_connect('/api/voice/audio',headers=HEADERS) as socket:
        socket.receive_json();socket.send_json({'type':'begin'});socket.send_bytes(b'\0'*3200);socket.send_json({'type':'end'})
        event=socket.receive_json()
        assert event['type']=='error' and 'test-owner-password' not in str(event)


def test_phrase_limit_and_silence_do_not_reach_recognizer(client,monkeypatch):
    monkeypatch.setattr(browser_voice,'configured',lambda:True)
    monkeypatch.setattr(browser_voice,'MAX_BYTES',3200)
    received=[]
    monkeypatch.setattr(browser_voice,'transcribe_pcm',lambda data,config:received.append(data))
    with client.websocket_connect('/api/voice/audio',headers=HEADERS) as socket:
        socket.receive_json();socket.send_json({'type':'begin'})
        socket.send_bytes(b'\0'*3200);socket.send_bytes(b'\0'*2)
        assert socket.receive_json()['type']=='error'
        assert received==[]
