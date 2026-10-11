"""Manifest installation is data only; no arbitrary modules, shell or executable code."""
import json,re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from jsonschema import Draft202012Validator
from .models import Skill,ToolGrant,DemoHomework,now
from .db import sessions
from .tools import Tool,REGISTRY
from .diary import DemoDiaryProvider,sync,study_plan
from .network import endpoint

class Manifest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[a-z][a-z0-9_-]{1,40}$')
    version:str=Field(pattern=r'^\d+\.\d+\.\d+$')
    description:str=Field(min_length=1,max_length=500)
    kind:Literal['builtin','mcp','rest']
    compatibility:Literal['atlas-v1']='atlas-v1'
    endpoint:str|None=None
    credential_env:str|None=None
    instructions:str=Field(default='',max_length=8000)
    read_tools:list[str]=Field(default_factory=list,max_length=20)

DIARY={'id':'diary-demo','version':'1.0.0','description':'Демонстрационный дневник: задания, расписание, оценки и учебный план','kind':'builtin','compatibility':'atlas-v1','instructions':'Тестовые учебные данные. Не выдавать их за реальный дневник.','read_tools':[]}
class Empty(BaseModel):model_config=ConfigDict(extra='forbid')

def list_homework(s,c):
    from .models import Assignment
    return {'demo':True,'assignments':[{'external_id':x.external_id,'subject':x.subject,'title':x.title,'due_at':str(x.due_at),'grade':x.grade,'source':x.source} for x in s.scalars(select(Assignment).where(Assignment.provider=='demo'))]}

def verify_sync(session,call,result):
    from .models import Assignment
    for item in result.get('records',[]):
        row=session.get(Assignment,item['id'])
        if row is None or row.content_hash!=item['content_hash']:return False
    return 'records' in result

DIARY_TOOLS={
 'diary.homework':Tool('diary.homework','Демо-дневник: синхронизированные задания',Empty,'diary.read',False,True,'read',list_homework,lambda s,c,r:r.get('demo') is True),
 'diary.schedule':Tool('diary.schedule','Демо-дневник: расписание уроков',Empty,'diary.read',False,True,'read',lambda s,c:{'demo':True,'schedule':DemoDiaryProvider().schedule(s)},lambda s,c,r:r.get('demo') is True),
 'diary.sync':Tool('diary.sync','Синхронизировать демо-дневник и создать учебные задачи',Empty,'diary.write',True,False,'atomic',lambda s,c:sync(s),verify_sync),
 'diary.study_plan':Tool('diary.study_plan','Предложить порядок занятий по ближайшим дедлайнам',Empty,'diary.read',False,True,'read',lambda s,c:study_plan(s),lambda s,c,r:'plan' in r),
}

def safe_schema(schema):
    encoded=json.dumps(schema)
    if len(encoded)>20000 or any(x in encoded for x in ['"$ref"','"pattern"','"patternProperties"']):raise ValueError('Unsafe/oversized remote schema')
    Draft202012Validator.check_schema(schema)
    return schema

class JsonInput:
    def __init__(self,schema):self.schema=safe_schema(schema)
    def model_json_schema(self):return self.schema
    def model_validate(self,args):
        if len(json.dumps(args))>12000:raise ValueError('Arguments too large')
        if not isinstance(args,dict):raise ValueError('Object required')
        if not Draft202012Validator(self.schema).is_valid(args):raise ValueError('Invalid arguments')
        class Value:
            def model_dump(self):return dict(args)
        return Value()

def remote_tool(skill,entry):
    name=skill.id+'.'+entry['name'];manifest=skill.manifest
    def execute(s,c):
        if manifest['kind']=='mcp':
            from .mcp_client import call
            result=call(manifest['endpoint'],manifest.get('credential_env'),entry['name'],c.arguments)
            from .memory import contains_secret
            if contains_secret(json.dumps(result)):raise ValueError('Secret in remote result')
            return result
        from .network import headers
        import httpx
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            with client.stream('GET',endpoint(manifest['endpoint']),headers=headers(manifest.get('credential_env'))) as response:
                response.raise_for_status();raw=b''
                for chunk in response.iter_bytes():
                    raw+=chunk
                    if len(raw)>50000:raise ValueError('REST response too large')
        from .memory import contains_secret
        if contains_secret(raw.decode('utf-8')):raise ValueError('Secret in remote result')
        return {'data':json.loads(raw),'source':manifest['endpoint']}
    return Tool(name,entry.get('description','Удалённый источник')[:500],JsonInput(entry['inputSchema']),skill.id+'.read',True,False,'read',execute,lambda s,c,r:isinstance(r,dict))

def catalog(session):
    from .desktop import DESKTOP,DESKTOP_WRITE
    result={**REGISTRY,'desktop.read':DESKTOP,'desktop.write':DESKTOP_WRITE}
    for skill in session.scalars(select(Skill).where(Skill.enabled==True)):
        if skill.manifest['kind']=='builtin':result.update(DIARY_TOOLS)
        else:
            for entry in skill.manifest['tools']:result[skill.id+'.'+entry['name']]=remote_tool(skill,entry)
    from .models import ConnectorSource
    from .tools import connector_read_tool
    for source in session.scalars(select(ConnectorSource).where(ConnectorSource.enabled==True)):
        result['connector.'+source.id+'.read']=connector_read_tool(source)
    return result

def resolve(name):
    with sessions()() as s:
        result=catalog(s)
        if name not in result:raise ValueError('Unknown or disabled tool')
        return result[name]

def skill_for_tool(session,name):
    if name in REGISTRY or name in {'desktop.read','desktop.write'}:return None
    if name.startswith('connector.'):
        from .models import ConnectorSource
        connector_id=name[len('connector.'):].removesuffix('.read')
        source=session.get(ConnectorSource,connector_id)
        if source is None or not source.enabled or name!='connector.'+connector_id+'.read':raise ValueError('Connector is disabled or missing')
        return source
    sid='diary-demo' if name in DIARY_TOOLS else name.split('.',1)[0]
    skill=session.scalar(select(Skill).where(Skill.id==sid).with_for_update())
    if skill is None or not skill.enabled:raise ValueError('Skill is disabled or uninstalled')
    return skill

def install(session,data):
    manifest=Manifest.model_validate(data);values=manifest.model_dump()
    from .memory import contains_secret
    if contains_secret(json.dumps(values)):raise ValueError('Secrets in manifest prohibited')
    if manifest.credential_env and not re.fullmatch(r'ATLAS_CONNECTOR_[A-Z0-9_]{1,60}',manifest.credential_env):raise ValueError('Invalid credential reference')
    if session.get(Skill,manifest.id):raise ValueError('Skill already installed')
    entries=[]
    if manifest.kind=='builtin':
        if manifest.id!='diary-demo' or manifest.version!='1.0.0' or manifest.endpoint or manifest.credential_env:raise ValueError('Unknown builtin')
        values=Manifest.model_validate(DIARY).model_dump();names=list(DIARY_TOOLS)
        from datetime import timedelta
        if session.get(DemoHomework,'math-1') is None:session.add(DemoHomework(id='math-1',subject='Математика (демо)',title='Решить пять примеров',due_at=now()+timedelta(days=2)))
    else:
        endpoint(manifest.endpoint or '')
        if manifest.kind=='mcp':
            from .mcp_client import discover
            remote=discover(manifest.endpoint,manifest.credential_env)
            for item in remote:
                name=item['name'];annotations=item.get('annotations') or {}
                if name not in manifest.read_tools:continue
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',name) or annotations.get('readOnlyHint') is not True or annotations.get('destructiveHint') is True:raise ValueError('Only explicitly selected read-only MCP tools allowed')
                safe_schema(item['inputSchema']);entries.append({'name':name,'description':item.get('description',''),'inputSchema':item['inputSchema']})
            if len(entries)!=len(set(manifest.read_tools)) or not entries:raise ValueError('Requested read-only tools not discovered')
        else:entries=[{'name':'fetch','description':manifest.description,'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]
        values['tools']=entries;names=[manifest.id+'.'+e['name'] for e in entries]
    skill=Skill(id=manifest.id,version=manifest.version,manifest=values,enabled=False)
    session.add(skill)
    for name in names:
        grant=session.get(ToolGrant,name)
        if grant is None:session.add(ToolGrant(tool=name,allowed=False))
        else:grant.allowed=False
    session.flush();return skill
