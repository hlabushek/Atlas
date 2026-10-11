"""Only owner-configured endpoints. No model-supplied URLs, shell or redirects."""
from urllib.parse import urlparse
from ipaddress import ip_address
import os
from .config import settings

def _is_loopback(host):
    if host.lower() == 'localhost':
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False

def endpoint(url):
    p=urlparse(url)
    allowed={h.strip().lower() for h in settings().integration_hosts.split(',') if h.strip()}
    if p.scheme not in {'https','http'} or not p.hostname or p.hostname.lower() not in allowed or p.username or p.password or p.fragment or p.query:
        raise ValueError('Endpoint is not owner-allowlisted')
    if p.scheme == 'http' and not _is_loopback(p.hostname):
        raise ValueError('Remote integrations require HTTPS')
    if len(url)>500:raise ValueError('Endpoint too long')
    return url

def headers(reference):
    if not reference:return {}
    import re
    if not re.fullmatch(r'ATLAS_CONNECTOR_[A-Z0-9_]{1,60}',reference):raise ValueError('Invalid credential reference')
    value=os.environ.get(reference,'')
    if not value:raise ValueError('Credential not configured')
    return {'Authorization':'Bearer '+value}
