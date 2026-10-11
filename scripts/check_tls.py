"""Exercise HTTPS overlay with a locally trusted temporary certificate, then restore HTTP."""
import os,subprocess,time,ipaddress,ssl
from pathlib import Path
from datetime import datetime,timedelta,timezone
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
root=Path(__file__).resolve().parents[1];folder=root/'.sessions/tls-drill';folder.mkdir(parents=True,exist_ok=True)
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Atlas LOCAL TLS DRILL')]);stamp=datetime.now(timezone.utc)
cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(stamp-timedelta(minutes=5)).not_valid_after(stamp+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False).sign(key,hashes.SHA256())
certificate=folder/'server.crt';private=folder/'server.key';certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM));private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
env=dict(os.environ,ATLAS_TLS_CERT=str(certificate),ATLAS_TLS_KEY=str(private))
values=dict(l.split('=',1) for l in (root/'.env').read_text(encoding='utf-8-sig').splitlines() if '=' in l and not l.startswith('#'))
def compose(*args,tls=True):
    command=['docker','compose','-f','compose.yaml']+(['-f','compose.tls.yaml'] if tls else [])+list(args)
    subprocess.run(command,cwd=root,env=env,check=True,capture_output=True)
try:
    compose('up','-d','--no-deps','api','web');compose('exec','-T','web','nginx','-t')
    context=ssl.create_default_context(cafile=str(certificate))
    with httpx.Client(base_url='https://127.0.0.1:8443',verify=context,headers={'X-Atlas-Client':'web'},timeout=10) as c:
        for _ in range(30):
            try:
                if c.get('/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(1)
        else:raise AssertionError('HTTPS not ready')
        assert c.get('/api/ops').status_code==401
        r=c.post('/api/login',json={'password':values['ATLAS_OWNER_PASSWORD']});r.raise_for_status()
        assert 'secure' in r.headers['set-cookie'].lower() and 'httponly' in r.headers['set-cookie'].lower()
        assert c.get('/api/ops').status_code==200
        assert c.get('/').headers['strict-transport-security']=='max-age=31536000'
        c.post('/api/logout').raise_for_status()
    print('PASS: verified local TLS, Secure/HttpOnly cookie, authenticated API, HSTS, nginx config')
finally:compose('up','-d','--no-deps','api','web',tls=False)
