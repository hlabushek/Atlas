"""Bounded authenticated local baseline, not a production capacity guarantee."""
import os,time,json,statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx
root=Path(__file__).resolve().parents[1]
values=dict(l.split('=',1) for l in (root/'.env').read_text(encoding='utf-8-sig').splitlines() if '=' in l and not l.startswith('#'))
base=os.environ.get('ATLAS_E2E_URL','http://127.0.0.1:8080')
with httpx.Client(base_url=base,timeout=20) as c:
    c.post('/api/login',json={'password':values['ATLAS_OWNER_PASSWORD']}).raise_for_status()
    cookie=c.cookies.get('atlas_session')
def one(index):
    start=time.perf_counter()
    with httpx.Client(base_url=base,timeout=20,cookies={'atlas_session':cookie}) as c:r=c.get(['/api/health','/api/tasks','/api/ops','/api/memory'][index%4])
    return r.status_code,time.perf_counter()-start
with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(one,range(100)))
latencies=sorted(t for status,t in results);failures=sum(status!=200 for status,t in results)
report={'requests':100,'concurrency':20,'failures':failures,'p50_seconds':statistics.median(latencies),'p95_seconds':latencies[94]}
(root/'.backups').mkdir(exist_ok=True);(root/'.backups/load-report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report));assert failures==0
