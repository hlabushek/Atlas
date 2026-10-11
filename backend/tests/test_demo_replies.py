import uuid
from datetime import timedelta
from sqlalchemy import select
from atlas import models as m
from atlas.db import sessions


def test_real_reply_to_demo_initiative_is_not_demo(client):
    with sessions()() as s:
        c=m.Conversation(title='Смешанная история');s.add(c);s.flush()
        task=m.Task(conversation_id=c.id,goal='Демо',request_key=str(uuid.uuid4()),status='COMPLETED',model_profile={'provider':'demo'});s.add(task);s.flush()
        item=m.Initiative(conversation_id=c.id,event_key='task:'+task.id,reason='Демо',message='Тест',next_allowed=m.now(),expires_at=m.now()+timedelta(days=1),related={'task_id':task.id},status='ANSWERED');s.add(item);s.flush()
        real=m.Task(conversation_id=c.id,goal='Настоящий ответ',request_key=str(uuid.uuid4()),status='COMPLETED',model_profile={'provider':'routerai'});s.add(real);s.flush()
        reply=m.Message(conversation_id=c.id,role='user',content=real.goal,created_at=real.created_at,initiative_id=item.id);s.add(reply);s.flush();item.reply_id=reply.id
        s.add(m.Message(conversation_id=c.id,role='assistant',content='Настоящая LLM',task_id=real.id,initiative_id=item.id))
        s.add(m.Message(conversation_id=c.id,role='assistant',content='Демо-ответ',task_id=task.id))
        s.commit();iid=item.id;rid=reply.id
    preview=client.get('/api/maintenance/demo').json()
    assert preview['counts']['tasks']==1 and preview['counts']['messages']==1
    assert 'initiatives' not in preview['counts'] and 'conversations' not in preview['counts']
    response=client.post('/api/maintenance/demo/reset',json={'digest':preview['digest'],'confirmation':'СБРОСИТЬ ДЕМО'})
    assert response.status_code==200,response.text
    with sessions()() as s:
        assert s.get(m.Initiative,iid).reply_id==rid
        assert s.get(m.Message,rid).content=='Настоящий ответ'
        assert len(list(s.scalars(select(m.Message))))==2
