"""Explicit owner action: apply recommended non-secret profiles via authenticated API."""
import argparse
from pathlib import Path
import httpx

def configure(base,env_file):
    values=dict(line.split('=',1) for line in Path(env_file).read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
    role=lambda model:{'provider':'routerai','model':model,'input_rate':0,'output_rate':0}
    config={'mode':'configured','main':role('deepseek/deepseek-v4.1-flash'),'planner':role('anthropic/claude-sonnet-5.5'),'embedding':role('qwen/qwen3-embedding-8b'),'embeddings_enabled':True,'max_output_tokens':1024}
    with httpx.Client(base_url=base,timeout=30,headers={'X-Atlas-Client':'web'},follow_redirects=False) as c:
        c.post('/api/login',json={'password':values['ATLAS_OWNER_PASSWORD']}).raise_for_status()
        old=c.get('/api/model-settings');old.raise_for_status()
        saved=c.put('/api/model-settings',json={'revision':old.json()['revision'],'config':config});saved.raise_for_status()
        result=saved.json()
        assert result['config']==config
        print('PASS: recommended profiles saved; readiness:',{role:state['ready'] for role,state in result['effective'].items()})
        c.post('/api/logout').raise_for_status()
if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Применить рекомендуемые модели Atlas (перезаписывает выбор моделей)')
    parser.add_argument('--base',default='http://127.0.0.1:8080');parser.add_argument('--env-file',default='.env')
    args=parser.parse_args();configure(args.base,args.env_file)
