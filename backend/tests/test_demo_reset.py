import uuid
from datetime import timedelta
from sqlalchemy import select
from atlas.db import sessions
from atlas import models as m


def seed(client):
    with sessions()() as s:
        demo=m.Conversation(title='Демо');mixed=m.Conversation(title='Смешанный');real=m.Conversation(title='Реальный')
        s.add_all([demo,mixed,real]);s.flush()
        for c,provider in [(demo,'demo'),(mixed,'demo'),(mixed,'routerai'),(real,'routerai')]:
            task=m.Task(conversation_id=c.id,request_key=str(uuid.uuid4()),goal='Тест',status='COMPLETED',model_profile={'provider':provider})
            s.add(task);s.flush()
            s.add_all([m.Message(conversation_id=c.id,role='user',content=task.goal,created_at=task.created_at),m.Message(conversation_id=c.id,role='assistant',content='Ответ',task_id=task.id)])
            if provider!='demo':
                call=m.ToolCall(task_id=task.id,step=0,tool='notes.create',arguments={},status='SUCCEEDED');s.add(call);s.flush()
                s.add(m.Note(call_id=call.id,title='Заметка',content=provider))
        s.add_all([m.Memory(content='Личное',category='PROFILE',source='owner'),m.Memory(content='Демо',category='KNOWLEDGE',source='demo')])
        s.add(m.ModelUsage(purpose='chat',provider='routerai:model',status='KNOWN',tokens=10,cost=1))
        s.add(m.Skill(id='diary-demo',version='1.0.0',manifest={'kind':'builtin'},enabled=True))
        s.add(m.DemoHomework(id='math-demo',subject='Демо',title='Демо',due_at=m.now()+timedelta(days=1)))
        for provider in ['demo','school.mos.ru']:
            assignment=m.Assignment(provider=provider,external_id='test',subject='Урок',title='Задание',due_at=m.now()+timedelta(days=1),source=provider,content_hash='a'*64)
            s.add(assignment);s.flush();s.add(m.StudyTask(assignment_id=assignment.id,goal='Учиться',due_at=assignment.due_at))
        s.add(m.Audit(event='old_audit',details={'keep':True}));s.commit()
        return demo.id,mixed.id,real.id


def test_proven_demo_only_and_verification(client):
    demo,mixed,real=seed(client)
    preview=client.get('/api/maintenance/demo').json()
    assert preview['counts']['tasks']==2 and preview['counts']['conversations']==1
    response=client.post('/api/maintenance/demo/reset',json={'digest':preview['digest'],'confirmation':'СБРОСИТЬ ДЕМО'})
    assert response.status_code==200,response.text
    assert response.json()['verified']
    with sessions()() as s:
        assert s.get(m.Conversation,demo) is None and s.get(m.Conversation,mixed) and s.get(m.Conversation,real)
        assert len(s.scalars(select(m.Task)).all())==2
        assert [n.content for n in s.scalars(select(m.Note))]==['routerai','routerai']
        assert [x.content for x in s.scalars(select(m.Memory))]==['Личное']
        assert [x.provider for x in s.scalars(select(m.Assignment))]==['school.mos.ru']
        assert len(s.scalars(select(m.ModelUsage)).all())==1
        assert s.scalar(select(m.Audit).where(m.Audit.event=='old_audit'))
        assert s.get(m.ModelPreferences,1)
    assert client.get('/api/maintenance/demo').json()['counts']=={}


def test_exact_plan_auth_and_confirmation(client):
    seed(client)
    digest=client.get('/api/maintenance/demo').json()['digest']
    assert client.post('/api/maintenance/demo/reset',json={'digest':digest,'confirmation':'да'}).status_code==422
    with sessions()() as s:
        s.add(m.DemoHomework(id='changed',subject='Тест',title='Новое',due_at=m.now()));s.commit()
    assert client.post('/api/maintenance/demo/reset',json={'digest':digest,'confirmation':'СБРОСИТЬ ДЕМО'}).status_code==409
    del client.headers['X-Atlas-Client']
    assert client.post('/api/maintenance/demo/reset',json={'digest':digest,'confirmation':'СБРОСИТЬ ДЕМО'}).status_code==403
    client.cookies.clear()
    assert client.get('/api/maintenance/demo').status_code==401


def test_unknown_real_usage_and_running_demo_preserved(client):
    with sessions()() as s:
        c=m.Conversation(title='Сохранить');s.add(c);s.flush()
        for profile,status in [(None,'COMPLETED'),({'provider':'demo'},'RUNNING'),({'provider':'demo'},'COMPLETED')]:
            task=m.Task(conversation_id=c.id,request_key=str(uuid.uuid4()),goal='Тест',status=status,model_profile=profile);s.add(task);s.flush()
            if status=='COMPLETED' and profile:s.add(m.ModelUsage(purpose='plan',provider='routerai:model',status='KNOWN',tokens=20,task_id=task.id))
        s.commit()
    result=client.get('/api/maintenance/demo').json()
    assert result['counts']=={} and result['unknown_tasks']==1 and result['protected_demo_tasks']==2


def test_demo_planner_does_not_make_real_note_disposable(client):
    with sessions()() as s:
        c=m.Conversation(title='Сохранить заметку');s.add(c);s.flush()
        t=m.Task(conversation_id=c.id,request_key=str(uuid.uuid4()),goal='Записать важное',status='COMPLETED',model_profile={'provider':'demo'});s.add(t);s.flush()
        call=m.ToolCall(task_id=t.id,step=0,tool='notes.create',arguments={},status='SUCCEEDED');s.add(call);s.flush()
        s.add(m.Note(call_id=call.id,title='Важное',content='Это личные данные'));s.commit()
    preview=client.get('/api/maintenance/demo').json()
    assert preview['counts']=={} and preview['protected_demo_tasks']==1


def test_audit_evidence_diary_children_and_channel_chat_retained(client):
    with sessions()() as s:
        c=m.Conversation(title='Голос');s.add(c);s.flush()
        t=m.Task(conversation_id=c.id,request_key=str(uuid.uuid4()),goal='Привет',status='COMPLETED');s.add(t);s.flush()
        s.add_all([m.Audit(task_id=t.id,event='reply_completed',details={'provider':'demo'}),m.Message(conversation_id=c.id,task_id=t.id,role='assistant',content='Демо')])
        s.add(m.TelegramBinding(id=2,conversation_id=c.id))
        assignment=m.Assignment(provider='demo',external_id='test',subject='Урок',title='Демо',due_at=m.now(),source='demo://atlas-diary',content_hash='a'*64);s.add(assignment);s.flush()
        study=m.StudyTask(assignment_id=assignment.id,goal='Демо',due_at=m.now());s.add(study);s.flush()
        event=m.Event(kind='diary.new',details={'assignment_id':assignment.id});s.add(event);s.flush()
        initiative=m.Initiative(conversation_id=c.id,reason='Учёба',message='Демо',event_key='test',next_allowed=m.now(),expires_at=m.now()+timedelta(days=1),related={'study_task_id':study.id});s.add(initiative);s.flush()
        s.add(m.Message(conversation_id=c.id,role='assistant',initiative_id=initiative.id,content='Напоминание'))
        s.add(m.Delivery(dedup_key='test-delivery',channel='web',payload={},conversation_id=c.id,initiative_id=initiative.id));s.commit();cid=c.id
    preview=client.get('/api/maintenance/demo').json()
    assert preview['counts']['tasks']==1 and 'conversations' not in preview['counts']
    response=client.post('/api/maintenance/demo/reset',json={'digest':preview['digest'],'confirmation':'СБРОСИТЬ ДЕМО'})
    assert response.status_code==200,response.text
    with sessions()() as s:
        assert s.get(m.Conversation,cid) and s.get(m.TelegramBinding,2)
        assert not s.scalars(select(m.Initiative)).all() and not s.scalars(select(m.Delivery)).all()
