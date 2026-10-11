"""Create a private CA and one Atlas Companion mTLS pair for a named host."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))


def write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def create(destination: Path, hostname: str, days: int) -> str:
    files = ("ca.pem", "ca.key", "server.pem", "server.key", "device.pem", "device.key")
    destination.mkdir(parents=True, exist_ok=True)
    if any((destination / name).exists() for name in files):
        raise FileExistsError("Refusing to overwrite existing Companion keys or certificates")

    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Atlas Companion private CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    write_key(destination / "ca.key", ca_key)
    write_cert(destination / "ca.pem", ca_cert)

    client_cert = None
    for name, usage in (("server", ExtendedKeyUsageOID.SERVER_AUTH), ("device", ExtendedKeyUsageOID.CLIENT_AUTH)):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Atlas Companion {name}")])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject).issuer_name(ca_name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        )
        if name == "server":
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        cert = builder.sign(ca_key, hashes.SHA256())
        write_key(destination / f"{name}.key", key)
        write_cert(destination / f"{name}.pem", cert)
        if name == "device":
            client_cert = cert

    assert client_cert is not None
    return client_cert.fingerprint(hashes.SHA256()).hex()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--hostname", default="atlas.prism-news.xyz")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    if not 30 <= args.days <= 730:
        parser.error("--days must be between 30 and 730")
    print("Fingerprint устройства:", create(args.destination, args.hostname, args.days))
