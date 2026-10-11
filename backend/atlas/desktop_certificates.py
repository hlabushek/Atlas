"""Local development CA only; never installs system trust or prints private keys."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import ipaddress
from cryptography import x509
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa

def create(directory):
    dest=Path(directory);dest.mkdir(parents=True,exist_ok=True)
    names=['ca.pem','ca.key','server.pem','server.key','device.pem','device.key']
    if any((dest/n).exists() for n in names):raise ValueError('Existing keys will not be overwritten')
    stamp=datetime.now(timezone.utc)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Atlas local development CA')])
    ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(stamp-timedelta(minutes=1)).not_valid_after(stamp+timedelta(days=30)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).add_extension(x509.KeyUsage(False,False,False,False,False,True,True,None,None),critical=True).add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),False).sign(ca_key,hashes.SHA256())
    def save(base,key,cert):
        (dest/(base+'.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        (dest/(base+'.pem')).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        import os
        if os.name!='nt':os.chmod(dest/(base+'.key'),0o600)
    save('ca',ca_key,ca)
    result={}
    for base,purpose in [('server',ExtendedKeyUsageOID.SERVER_AUTH),('device',ExtendedKeyUsageOID.CLIENT_AUTH)]:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        builder=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Atlas '+base)])).issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(stamp-timedelta(minutes=1)).not_valid_after(stamp+timedelta(days=30)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(x509.ExtendedKeyUsage([purpose]),False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),False)
        if base=='server':builder=builder.add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
        cert=builder.sign(ca_key,hashes.SHA256());save(base,key,cert);result[base]=cert.fingerprint(hashes.SHA256()).hex()
    return result
