from datetime import date
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import select
from .api import DB,Secure,dto
from .models import Skill,ToolGrant,ToolCall,DemoHomework,Assignment,StudyTask,SchoolScheduleDay,Audit
from .skills import Manifest,DIARY,install
from .diary import Homework,SchoolDayInput,DemoDiaryProvider,study_plan,sync,sync_school_days,school_day,school_days

router=APIRouter(prefix='/api')
class Toggle(BaseModel):enabled:bool
class Completion(BaseModel):completed:bool
class SchoolImport(BaseModel):
    homework:list[Homework]=Field(default_factory=list,max_length=300)
    days:list[SchoolDayInput]=Field(default_factory=list,max_length=7)
    @model_validator(mode='after')
    def validate_batch(self):
        if not self.homework and not self.days:raise ValueError('Нужны задания или дни расписания')
        if len({x.external_id for x in self.homework})!=len(self.homework):raise ValueError('Повторяются ID заданий')
        if len({x.day for x in self.days})!=len(self.days):raise ValueError('Повторяются даты расписания')
        return self

@router.get('/skills/catalog')
def available(_:Secure):return [DIARY]
@router.get('/skills')
def installed(session:DB,_:Secure):return [dto(s) for s in session.scalars(select(Skill))]
@router.post('/skills')
def add(body:Manifest,session:DB,_:Secure):
    try:skill=install(session,body.model_dump())
    except Exception as exc:
        session.rollback();raise HTTPException(422,'Навык не установлен: '+type(exc).__name__)
    session.add(Audit(event='skill_installed',details={'skill_id':skill.id}));session.commit();return dto(skill)
@router.put('/skills/{sid}')
def toggle(sid:str,body:Toggle,session:DB,_:Secure):
    skill=session.scalar(select(Skill).where(Skill.id==sid).with_for_update())
    if skill is None:raise HTTPException(404,'Навык не найден')
    skill.enabled=body.enabled
    if not body.enabled:
        names=list(__import__('atlas.skills',fromlist=['DIARY_TOOLS']).DIARY_TOOLS) if skill.manifest['kind']=='builtin' else [sid+'.'+e['name'] for e in skill.manifest['tools']]
        for name in names:
            grant=session.get(ToolGrant,name)
            if grant:grant.allowed=False
    session.add(Audit(event='skill_enabled' if body.enabled else 'skill_disabled',details={'skill_id':sid}));session.commit();return dto(skill)
@router.delete('/skills/{sid}')
def remove(sid:str,session:DB,_:Secure):
    skill=session.scalar(select(Skill).where(Skill.id==sid).with_for_update())
    if skill is None:raise HTTPException(404,'Навык не найден')
    skill.enabled=False
    from .skills import DIARY_TOOLS
    names=list(DIARY_TOOLS) if skill.manifest['kind']=='builtin' else [sid+'.'+e['name'] for e in skill.manifest['tools']]
    for name in names:
        grant=session.get(ToolGrant,name)
        if grant:grant.allowed=False
    session.delete(skill);session.add(Audit(event='skill_uninstalled',details={'skill_id':sid}));session.commit()
    return {'ok':True,'preserved':'История и учебные данные сохранены'}
@router.post('/skills/{sid}/health')
def health(sid:str,session:DB,_:Secure):
    skill=session.get(Skill,sid)
    if skill is None:raise HTTPException(404,'Навык не найден')
    try:
        if skill.manifest['kind']=='builtin':status=DemoDiaryProvider().session_status()
        elif skill.manifest['kind']=='mcp':
            from .mcp_client import discover
            discover(skill.manifest['endpoint'],skill.manifest.get('credential_env'));status='AVAILABLE'
        else:
            from .diary import RestDiaryProvider
            status=RestDiaryProvider(skill.manifest['endpoint'],skill.manifest.get('credential_env')).session_status()
    except Exception as exc:status='UNAVAILABLE: '+type(exc).__name__
    skill.health=status;session.commit();return {'status':status}
@router.get('/skills/{sid}/history')
def history(sid:str,session:DB,_:Secure):
    from .skills import DIARY_TOOLS
    names=list(DIARY_TOOLS) if sid=='diary-demo' else None
    query=select(ToolCall).where(ToolCall.tool.in_(names)) if names else select(ToolCall).where(ToolCall.tool.startswith(sid+'.'))
    return [dto(c) for c in session.scalars(query.order_by(ToolCall.created_at.desc()).limit(100))]
@router.get('/skills/{sid}/instructions')
def instructions(sid:str,session:DB,_:Secure):
    skill=session.get(Skill,sid)
    if skill is None:raise HTTPException(404,'Навык не найден')
    return {'text':skill.manifest.get('instructions',''),'trusted':False}

@router.get('/diary')
def diary(session:DB,_:Secure):
    has_school=(session.scalar(select(Assignment.id).where(Assignment.provider=='school.mos.ru').limit(1)) is not None
        or session.scalar(select(SchoolScheduleDay.id).where(SchoolScheduleDay.provider=='school.mos.ru').limit(1)) is not None)
    return {'demo':not has_school,'installed':session.get(Skill,'diary-demo') is not None,
        'assignments':[dto(x) for x in session.scalars(select(Assignment).order_by(Assignment.due_at))],
        'tasks':[dto(x) for x in session.scalars(select(StudyTask).order_by(StudyTask.due_at))],
        'demo_homework':[dto(x) for x in session.scalars(select(DemoHomework))],
        'schedule_available_days':[x.isoformat() for x in session.scalars(select(SchoolScheduleDay.day)
            .where(SchoolScheduleDay.provider=='school.mos.ru').order_by(SchoolScheduleDay.day))],
        'schedule':[] if has_school else DemoDiaryProvider().schedule(session),'plan':study_plan(session)}
@router.get('/diary/day')
def day_detail(day:date,session:DB,_:Secure):
    return school_day(session,day)
@router.get('/diary/days')
def days_detail(start:date,session:DB,_:Secure,count:int=Query(default=7,ge=1,le=7)):
    return {'days':school_days(session,start,count)}
@router.post('/diary/import/school-mos')
def import_school(body:SchoolImport,session:DB,_:Secure):
    from .diary import DiaryProvider
    class SubmittedSchoolProvider(DiaryProvider):
        def homework(self,_):return body.homework
        def schedule(self,_):return []
        def session_status(self):return 'AUTHORIZED'
    outcome=sync(session,SubmittedSchoolProvider(),provider_id='school.mos.ru',source='https://school.mos.ru/diary/homeworks/homeworks') if body.homework else {'new':0,'changed':0}
    days=sync_school_days(session,body.days)
    session.add(Audit(event='school_diary_imported',details={'homework_count':len(body.homework),
        'schedule_days':[x.day.isoformat() for x in body.days],
        'new_homework':outcome['new'],'changed_homework':outcome['changed'],**days}))
    session.commit()
    result={'count':len(body.homework),'new':outcome['new'],'changed':outcome['changed']}
    if body.days:result.update(days)
    return result
@router.post('/diary/demo/homework')
def update_demo(body:Homework,session:DB,_:Secure):
    if session.get(Skill,'diary-demo') is None:raise HTTPException(409,'Установите демо-дневник')
    row=session.get(DemoHomework,body.external_id)
    if row is None:row=DemoHomework(id=body.external_id);session.add(row)
    row.subject=body.subject;row.title=body.title;row.due_at=body.due_at;row.grade=body.grade
    session.add(Audit(event='demo_homework_changed',details={'external_id':row.id}));session.commit();return dto(row)
@router.put('/diary/tasks/{tid}')
def completed(tid:str,body:Completion,session:DB,_:Secure):
    task=session.get(StudyTask,tid)
    if task is None:raise HTTPException(404,'Учебная задача не найдена')
    task.status='COMPLETED' if body.completed else 'PENDING';session.commit();return dto(task)
