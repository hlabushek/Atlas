"""Production owner API and encryption-key smoke without printing credentials."""
import os

import httpx

from atlas.connector_sessions import cipher

cipher()
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=10,trust_env=False,
                  headers={'X-Atlas-Client':'web'}) as client:
    login=client.post('/api/login',json={'password':os.environ['ATLAS_OWNER_PASSWORD']})
    assert login.status_code==200
    # The real HTTPS proxy carries Secure cookies; this loopback diagnostic adds it explicitly.
    client.headers['Cookie']='atlas_session='+client.cookies['atlas_session']
    try:
        assert client.get('/api/connectors').status_code==200
        assert client.get('/api/connector-actions').status_code==200
    finally:
        client.post('/api/logout')
print('PASS: owner API, connector routes and session encryption key available')
