"""Public-page browser RPC smoke; no cookie or screenshot data is retained."""
from atlas.browser_connectors import _remote

source_id = 'public-vps-smoke'
view = _remote('/start', {'source_id': source_id, 'origin': 'https://example.org/', 'state': None})
try:
    assert view['url'] == 'https://example.org/'
    assert view['screenshot'].startswith('data:image/png;base64,')
finally:
    _remote('/finish', {'source_id': source_id})
print('PASS: Atlas API reached isolated browser, rendered page and closed session')
