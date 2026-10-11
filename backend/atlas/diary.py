"""Provider boundary; demo data is explicit. A real API adapter accepts the same contract."""
import hashlib,json
from abc import ABC,abstractmethod
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel,Field,model_validator,ConfigDict
from sqlalchemy import select
from .models import DemoHomework,Assignment,StudyTask,SchoolScheduleDay,Event,now
from .notifications import utc

class Homework(BaseModel):
    model_config=ConfigDict(extra='forbid')
    external_id:str=Field(min_length=1,max_length=80)
    subject:str=Field(min_length=1,max_length=100)
    title:str=Field(min_length=1,max_length=300)
    due_at:datetime
    grade:str|None=Field(default=None,max_length=20)
    @model_validator(mode='after')
    def aware(self):
        if self.due_at.tzinfo is None:raise ValueError('Timezone required')
        from .memory import contains_secret
        if contains_secret(self.subject+' '+self.title+' '+(self.grade or '')):raise ValueError('Secrets in homework prohibited')
        self.due_at=utc(self.due_at);return self

SCHOOL_TZ=ZoneInfo('Europe/Moscow')
SCHOOL_SOURCE='https://school.mos.ru/diary'

class Lesson(BaseModel):
    model_config=ConfigDict(extra='forbid')
    subject:str=Field(min_length=1,max_length=100)
    starts_at:str|None=Field(default=None,pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    ends_at:str|None=Field(default=None,pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    room:str|None=Field(default=None,max_length=60)
    homework:str|None=Field(default=None,max_length=1000)
    @model_validator(mode='after')
    def safe(self):
        from .memory import contains_secret
        if self.starts_at and self.ends_at and self.ends_at<=self.starts_at:
            raise ValueError('Время окончания должно быть позже начала')
        if contains_secret(' '.join(x or '' for x in (self.subject,self.room,self.homework))):
            raise ValueError('Секреты в расписании запрещены')
        return self

class SchoolDayInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    day:date
    lessons:list[Lesson]=Field(max_length=20)
    @model_validator(mode='after')
    def reasonable_day(self):
        if not 2020<=self.day.year<=2100:raise ValueError('Недопустимая дата расписания')
        return self

def sync_school_days(session,days:list[SchoolDayInput]):
    changed=new=0
    for item in days:
        lessons=[lesson.model_dump(mode='json') for lesson in item.lessons]
        digest=hashlib.sha256(json.dumps(lessons,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        row=session.scalar(select(SchoolScheduleDay).where(SchoolScheduleDay.provider=='school.mos.ru',
            SchoolScheduleDay.day==item.day).with_for_update())
        if row is None:
            row=SchoolScheduleDay(provider='school.mos.ru',day=item.day,lessons=lessons,
                content_hash=digest,source=SCHOOL_SOURCE,synced_at=now(),revision=1)
            session.add(row);new+=1
        else:
            if row.content_hash!=digest:
                row.lessons=lessons;row.content_hash=digest;row.revision+=1;changed+=1
            row.synced_at=now()
    return {'new_days':new,'changed_days':changed}

def school_day(session,day:date):
    row=session.scalar(select(SchoolScheduleDay).where(SchoolScheduleDay.provider=='school.mos.ru',
        SchoolScheduleDay.day==day))
    assignments=[a for a in session.scalars(select(Assignment).where(Assignment.provider=='school.mos.ru'))
        if utc(a.due_at).astimezone(SCHOOL_TZ).date()==day]
    assignments.sort(key=lambda a:(utc(a.due_at),a.subject,a.title))
    return {'date':day.isoformat(),'schedule_known':row is not None,
        'lessons':row.lessons if row else [],'schedule_synced_at':row.synced_at.isoformat() if row else None,
        'homework':[{'subject':a.subject,'title':a.title,'due_at':utc(a.due_at).isoformat(),
                     'grade':a.grade,'synced_at':utc(a.synced_at).isoformat()} for a in assignments],
        'homework_latest_sync':max((utc(a.synced_at).isoformat() for a in session.scalars(
            select(Assignment).where(Assignment.provider=='school.mos.ru'))),default=None)}

def school_days(session,start:date,count:int):
    if not 1<=count<=7:raise ValueError('Период должен быть от 1 до 7 дней')
    return [school_day(session,start+timedelta(days=i)) for i in range(count)]

class DiaryProvider(ABC):
    @abstractmethod
    def homework(self,session)->list[Homework]:...
    @abstractmethod
    def schedule(self,session)->list[dict]:...
    @abstractmethod
    def session_status(self)->str:...

class DemoDiaryProvider(DiaryProvider):
    def homework(self,session):
        return [Homework(external_id=r.id,subject=r.subject,title=r.title,due_at=utc(r.due_at),grade=r.grade) for r in session.scalars(select(DemoHomework))]
    def schedule(self,session):
        return [{'subject':'Математика','weekday':1,'time':'09:00','demo':True},{'subject':'История','weekday':2,'time':'10:00','demo':True}]
    def session_status(self):return 'DEMO'

class RestDiaryProvider(DiaryProvider):
    def __init__(self,url,credential_env=None):self.url=url;self.credential_env=credential_env
    def data(self):
        from .network import endpoint,headers
        import httpx
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            with client.stream('GET',endpoint(self.url),headers=headers(self.credential_env)) as response:
                if response.status_code in {401,403}:raise ValueError('Повторно авторизуйтесь в сервисе')
                response.raise_for_status();raw=b''
                for chunk in response.iter_bytes():
                    raw+=chunk
                    if len(raw)>500000:raise ValueError('Response too large')
        return json.loads(raw)
    def homework(self,session):return [Homework.model_validate(x) for x in self.data()['homework'][:1000]]
    def schedule(self,session):return self.data().get('schedule',[])[:100]
    def session_status(self):
        try:self.data();return 'AUTHORIZED'
        except Exception:return 'REAUTH_REQUIRED_OR_UNAVAILABLE'

def sync(session,provider=None,provider_id='demo',source='demo://atlas-diary'):
    provider=provider or DemoDiaryProvider();new=changed=0;records=[]
    for item in provider.homework(session):
        values=item.model_dump(mode='json');digest=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
        row=session.scalar(select(Assignment).where(Assignment.provider==provider_id,Assignment.external_id==item.external_id))
        kind=None
        if row is None:
            row=Assignment(provider=provider_id,external_id=item.external_id,subject=item.subject,title=item.title,due_at=item.due_at,grade=item.grade,source=source,content_hash=digest)
            session.add(row);session.flush();new+=1;kind='diary_new'
        elif row.content_hash!=digest:
            row.subject=item.subject;row.title=item.title;row.due_at=item.due_at;row.grade=item.grade;row.content_hash=digest;row.revision+=1;changed+=1;kind='diary_changed'
        row.synced_at=now()
        records.append({"id":row.id,"content_hash":digest})
        if kind:session.add(Event(key='diary:'+row.id+':'+str(row.revision),kind=kind,details={'assignment_id':row.id,'title':row.subject+': '+row.title,'source':source}))
        task=session.scalar(select(StudyTask).where(StudyTask.assignment_id==row.id))
        if task is None:session.add(StudyTask(assignment_id=row.id,goal=row.subject+': '+row.title,due_at=row.due_at))
        else:task.goal=row.subject+': '+row.title;task.due_at=row.due_at
    session.flush();return {'provider':provider_id,'demo':provider_id=='demo','new':new,'changed':changed,'records':records}

def study_plan(session):
    tasks=session.scalars(select(StudyTask).where(StudyTask.status=='PENDING').order_by(StudyTask.due_at)).all()
    has_real=any(session.get(Assignment,t.assignment_id).provider!='demo' for t in tasks)
    return {'demo':not has_real,'plan':[{'goal':t.goal,'due_at':utc(t.due_at).isoformat(),'suggested_minutes':30} for t in tasks],'method':'Детерминированный порядок ближайших дедлайнов, не план от LLM'}


def retrieve(session, query, limit=20, offset=0, provider=None):
    """Поиск по сохранённым заданиям; демо и реальные источники можно ограничить."""
    from .embeddings import ranked
    from .hybrid_search import lexical, rrf
    from .memory import contains_secret
    if limit <= 0 or offset < 0 or not query.strip() or contains_secret(query):return []
    session.flush()
    filters = (Assignment.provider == provider,) if provider is not None else ()
    ids = rrf(lexical(session, query, Assignment, [Assignment.subject, Assignment.title],
                      'assignments_fts', filters=filters), ranked(session, query, domain='diary'))
    rows = [row for identifier in ids if (row := session.get(Assignment, identifier)) is not None
            and (provider is None or row.provider == provider)]
    return rows[offset:offset + limit]
