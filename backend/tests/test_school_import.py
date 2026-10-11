from datetime import datetime,timezone
from datetime import date
import uuid
from sqlalchemy import select
from alembic import command
from alembic.config import Config
from atlas.db import sessions
from atlas.models import SchoolScheduleDay,Audit
from atlas.diary_queries import matches,reply
from atlas.worker import tick
from atlas.router import ModelRouter


def test_school_import_is_authenticated_idempotent_and_real(client):
    payload={'homework':[{'external_id':'616574267','subject':'История','title':'Прочитать параграф','due_at':'2026-10-01T23:59:00+03:00','grade':None}]}
    first=client.post('/api/diary/import/school-mos',json=payload)
    assert first.status_code==200 and first.json()=={'count':1,'new':1,'changed':0}
    second=client.post('/api/diary/import/school-mos',json=payload)
    assert second.status_code==200 and second.json()=={'count':1,'new':0,'changed':0}
    diary=client.get('/api/diary').json()
    assert diary['demo'] is False and diary['schedule']==[]
    assert any(a['provider']=='school.mos.ru' and a['external_id']=='616574267' for a in diary['assignments'])


def test_school_import_rejects_secret_and_oversized_batch(client):
    row={'external_id':'1','subject':'История','title':'password=private-value','due_at':'2026-10-01T23:59:00+03:00'}
    assert client.post('/api/diary/import/school-mos',json={'homework':[row]}).status_code==422
    row['title']='Читать'
    assert client.post('/api/diary/import/school-mos',json={'homework':[row]*301}).status_code==422


def test_schedule_days_are_kept_separately_and_reimport_is_idempotent(client):
    day={'day':'2026-10-05','lessons':[{'subject':'Математика','starts_at':'09:00','ends_at':'09:45','room':'12'}]}
    other={'day':'2026-10-06','lessons':[]}
    first=client.post('/api/diary/import/school-mos',json={'days':[day,other]})
    assert first.status_code==200 and first.json()=={'count':0,'new':0,'changed':0,'new_days':2,'changed_days':0}
    assert client.post('/api/diary/import/school-mos',json={'days':[day]}).json()['changed_days']==0
    changed={**day,'lessons':[{'subject':'Алгебра','starts_at':'09:00','ends_at':'09:45'}]}
    assert client.post('/api/diary/import/school-mos',json={'days':[changed]}).json()['changed_days']==1
    days=client.get('/api/diary/days',params={'start':'2026-10-05','count':3}).json()['days']
    assert [d['schedule_known'] for d in days]==[True,True,False]
    assert days[0]['lessons'][0]['subject']=='Алгебра' and days[1]['lessons']==[]
    diary=client.get('/api/diary').json()
    assert diary['schedule_available_days']==['2026-10-05','2026-10-06']
    assert diary['demo'] is False and diary['schedule']==[]
    with sessions()() as session:
        assert session.scalar(select(SchoolScheduleDay).where(SchoolScheduleDay.day==date(2026,10,5))).revision==2


def test_school_day_homework_uses_moscow_date_and_chat_reports_saved_data(client):
    body={'homework':[{'external_id':'one','subject':'История','title':'Прочитать параграф',
                       'due_at':'2026-10-05T23:30:00+03:00'}],
          'days':[{'day':'2026-10-05','lessons':[{'subject':'История','starts_at':'10:00'}]}]}
    assert client.post('/api/diary/import/school-mos',json=body).status_code==200
    day=client.get('/api/diary/day',params={'day':'2026-10-05'}).json()
    assert day['schedule_known'] and day['homework'][0]['title']=='Прочитать параграф'
    assert client.get('/api/diary/day',params={'day':'2026-10-06'}).json()['homework']==[]
    with sessions()() as session:
        answer=reply(session,'Покажи расписание и ДЗ на 05.10.2026',today=date(2026,10,4))
        assert 'История' in answer and 'Прочитать параграф' in answer
        assert 'Расписание обновлено' in answer and 'Последнее обновление' in answer
        assert 'Сохранённое ДЗ по датам' in reply(session,'ДЗ списком',today=date(2026,10,4))
        assert 'Расписание на этот день не загружено' in reply(session,'Расписание на 06.10.2026',today=date(2026,10,4))


def test_schedule_input_and_read_permissions(client):
    assert client.post('/api/diary/import/school-mos',json={'days':[{'day':'2026-10-05','lessons':[
        {'subject':'Математика','starts_at':'10:00','ends_at':'09:00'}]}]}).status_code==422
    assert client.post('/api/diary/import/school-mos',json={'days':[{'day':'2026-10-05','lessons':[]},
        {'day':'2026-10-05','lessons':[]}]}).status_code==422
    assert client.get('/api/diary/days',params={'start':'2026-10-05','count':8}).status_code==422
    client.cookies.clear()
    assert client.get('/api/diary/day',params={'day':'2026-10-05'}).status_code==401
    assert client.post('/api/diary/import/school-mos',json={'days':[{'day':'2026-10-05','lessons':[]}]}).status_code==401


def test_diary_query_does_not_capture_unrelated_schedule(client):
    assert matches('Расписание уроков на завтра')
    assert matches('ДЗ списком')
    assert not matches('Расписание работы над проектом')
    assert not matches('Как выполнить это задание по Python?')


def test_chat_reads_saved_diary_without_calling_model(client,monkeypatch):
    assert client.post('/api/diary/import/school-mos',json={'days':[{'day':'2026-10-05',
        'lessons':[{'subject':'Физика','starts_at':'11:00'}]}]}).status_code==200
    cid=client.post('/api/conversations',json={}).json()['id']
    task=client.post(f'/api/conversations/{cid}/messages',json={'content':'Расписание на 05.10.2026',
        'request_key':str(uuid.uuid4())}).json()
    monkeypatch.setattr(ModelRouter,'answer',lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('model called')))
    assert tick()
    result=client.get('/api/tasks/'+task['id']).json()
    assert result['status']=='COMPLETED' and 'Физика' in result['result']
    with sessions()() as session:
        audit=session.scalar(select(Audit).where(Audit.task_id==task['id'],Audit.event=='reply_completed'))
        assert audit.details=={'provider':'atlas-diary','model':'saved-data'}


def test_schedule_migration_preserves_existing_assignments(client):
    body={'homework':[{'external_id':'preserved','subject':'Биология','title':'Старое ДЗ',
                       'due_at':'2026-10-05T12:00:00+03:00'}]}
    assert client.post('/api/diary/import/school-mos',json=body).status_code==200
    command.downgrade(Config('alembic.ini'),'0023')
    command.upgrade(Config('alembic.ini'),'0024')
    day=client.get('/api/diary/day',params={'day':'2026-10-05'}).json()
    assert day['homework'][0]['title']=='Старое ДЗ' and not day['schedule_known']
