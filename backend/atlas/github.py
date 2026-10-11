"""Public repositories only, fixed host and constrained paths."""
import httpx
from pydantic import BaseModel, ConfigDict, Field
class RepoInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    owner:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9-]{0,38}$')
    repo:str=Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')

def read_repo(session,call):
    args=RepoInput.model_validate(call.arguments)
    if args.repo in {'.','..'}:raise ValueError('Некорректное имя')
    with httpx.Client(base_url='https://api.github.com',timeout=15,follow_redirects=False,headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','User-Agent':'Atlas-public-connector'}) as client:
        r=client.get('/repos/'+args.owner+'/'+args.repo)
        r.raise_for_status()
        if len(r.content)>500000:raise ValueError('Ответ слишком большой')
        data=r.json()
        from .memory import contains_secret
        if contains_secret(str(data.get('description') or '')):raise ValueError('Secrets prohibited')
        if data.get('private') is not False:raise ValueError('Только публичные репозитории')
        return {k:data.get(k) for k in ('full_name','html_url','description','default_branch','stargazers_count','open_issues_count','updated_at','archived')}
