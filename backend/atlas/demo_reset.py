"""Очистка только доказанного демо. Не зарегистрирована как инструмент модели."""
import hashlib
import json
from sqlalchemy import select, delete
from . import models as m

TABLES = [m.PushDelivery,m.Delivery,m.Message,m.Initiative,m.Event,m.StudyTask,m.Assignment,
          m.SpaceConversation,m.VoiceRequest,m.AnswerFragment,m.ToolEvent,m.Approval,m.Note,m.ToolCall,m.Task,
          m.Conversation,m.SpaceMemory,m.Embedding,m.MemoryConflict,m.MemoryFact,m.Memory,m.DemoHomework,m.Skill]
DIARY_TOOLS = {'diary.homework','diary.schedule','diary.sync','diary.study_plan'}


def plan(session):
    rows = {model.__tablename__:list(session.scalars(select(model))) for model in TABLES}
    chosen = {name:set() for name in rows}
    def add(model, row):
        keys=tuple(getattr(row,c.name) for c in model.__table__.primary_key.columns)
        chosen[model.__tablename__].add(keys)
    def ids(model):
        return {key[0] for key in chosen[model.__tablename__]}
    tasks=rows['tasks'];calls=rows['tool_calls'];messages=rows['messages']
    evidence={a.task_id for a in session.scalars(select(m.Audit).where(m.Audit.event=='reply_completed')) if a.details.get('provider')=='demo'}
    usages=list(session.scalars(select(m.ModelUsage)))
    real_usage={u.task_id for u in usages if not u.provider.startswith('demo')}
    device_calls={c.call_id for c in session.scalars(select(m.DeviceCommand))}
    protected_tasks=[]
    for task in tasks:
        task_calls=[c for c in calls if c.task_id==task.id]
        diary_plan=bool(task.plan) and all(isinstance(step,dict) and step.get('tool') in DIARY_TOOLS for step in task.plan)
        demo=(task.model_profile or {}).get('provider')=='demo' or task.id in evidence or diary_plan
        if not demo:continue
        # Даже demo-planner мог создать настоящую заметку или выполнить иной эффект.
        # Без отдельной метки происхождения такие результаты сохраняем целиком.
        if task.id in real_usage or task.status not in {'COMPLETED','FAILED','CANCELLED'} or any(c.id in device_calls or c.status in {'STARTED','UNKNOWN'} or c.tool not in DIARY_TOOLS|{'memory.search'} for c in task_calls):
            protected_tasks.append(task.id);continue
        add(m.Task,task)
    tids=ids(m.Task)
    for call in calls:
        if call.task_id in tids:add(m.ToolCall,call)
    callids=ids(m.ToolCall)
    for model in [m.Note,m.Approval]:
        for row in rows[model.__tablename__]:
            if row.call_id in callids:add(model,row)
    for model in [m.VoiceRequest,m.AnswerFragment,m.ToolEvent]:
        for row in rows[model.__tablename__]:
            if row.task_id in tids:add(model,row)
    active_diary=any(t.status not in {'COMPLETED','FAILED','CANCELLED'} and any(isinstance(step,dict) and step.get('tool') in DIARY_TOOLS for step in (t.plan or [])) for t in tasks)
    for row in rows['assignments']:
        if row.provider=='demo' and not active_diary:add(m.Assignment,row)
    aids=ids(m.Assignment)
    for row in rows['study_tasks']:
        if row.assignment_id in aids:add(m.StudyTask,row)
    for row in rows['events']:
        if row.details.get('assignment_id') in aids or (row.details.get('source')=='demo://atlas-diary' and not active_diary):add(m.Event,row)
    demo_tasks={t.id:t for t in tasks if t.id in tids}
    proven_messages=set()
    for row in messages:
        user_matches=[t for t in demo_tasks.values() if row.role=='user' and row.conversation_id==t.conversation_id and row.created_at==t.created_at and row.content==t.goal]
        if row.task_id in tids or len(user_matches)==1:proven_messages.add(row.id)
    for row in rows['initiatives']:
        related=row.related or {}
        # Реальный/неопределённый ответ не становится демо из-за исходного уведомления.
        if row.reply_id and row.reply_id not in proven_messages:continue
        if any(x.initiative_id==row.id and x.id not in proven_messages and (x.role!='assistant' or x.task_id) for x in messages):continue
        if related.get('task_id') in tids or related.get('event_id') in ids(m.Event) or related.get('study_task_id') in ids(m.StudyTask):add(m.Initiative,row)
    iids=ids(m.Initiative)
    for row in messages:
        if row.id in proven_messages or (row.initiative_id in iids and row.role=='assistant' and not row.task_id):add(m.Message,row)
    mids=ids(m.Message)
    bound={d.voice_conversation_id for d in session.scalars(select(m.Device)) if d.voice_conversation_id}
    bound.update(b.conversation_id for b in session.scalars(select(m.TelegramBinding)) if b.conversation_id)
    bound.update(link.conversation_id for link in rows['space_conversations'])
    for row in rows['conversations']:
        linked=[t for t in tasks if t.conversation_id==row.id]
        if row.id in bound or not linked or any(t.id not in tids for t in linked):continue
        if any(x.conversation_id==row.id and x.id not in mids for x in messages):continue
        if any(x.conversation_id==row.id and x.id not in iids for x in rows['initiatives']):continue
        add(m.Conversation,row)
    cids=ids(m.Conversation)
    for row in rows['deliveries']:
        if row.initiative_id in iids or row.conversation_id in cids or row.dedup_key in {'reply:'+mid for mid in mids}:add(m.Delivery,row)
    for row in rows['push_deliveries']:
        if row.initiative_id in iids:add(m.PushDelivery,row)
    for row in rows['memories']:
        if row.source in {'demo','atlas-demo','e2e'}:add(m.Memory,row)
    memoryids=ids(m.Memory)
    for row in rows['embeddings']:
        if row.assignment_id in aids:add(m.Embedding,row)
    for model in [m.SpaceMemory,m.MemoryFact,m.Embedding]:
        for row in rows[model.__tablename__]:
            if row.memory_id in memoryids:add(model,row)
    for row in rows['memory_conflicts']:
        if row.left_id in memoryids or row.right_id in memoryids:add(m.MemoryConflict,row)
    if not active_diary:
        for row in rows['demo_homework']:add(m.DemoHomework,row)
    for row in rows['skills']:
        if row.id=='diary-demo' and not active_diary:add(m.Skill,row)
    # Снимок включает содержимое и статусы удаляемых строк, но наружу идёт только digest.
    snapshot={}
    for model in TABLES:
        name=model.__tablename__
        snapshot[name]=[{c.name:getattr(row,c.name) for c in model.__table__.columns} for row in rows[name]
                        if tuple(getattr(row,c.name) for c in model.__table__.primary_key.columns) in chosen[name]]
        snapshot[name].sort(key=lambda row:json.dumps(row,default=str,sort_keys=True))
    digest=hashlib.sha256(json.dumps(snapshot,default=str,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'selected':chosen,'digest':digest,'counts':{k:len(v) for k,v in chosen.items() if v},
            'protected_demo_tasks':len(protected_tasks),'unknown_tasks':sum(not t.model_profile and t.id not in evidence and t.id not in tids for t in tasks),
            'owner_memories':sum(x.source=='owner' for x in rows['memories'])}


def public_plan(session):
    result=plan(session)
    return {k:v for k,v in result.items() if k!='selected'}


def retained_snapshot(session, selected):
    """Контроль всех сохраняемых строк, включая ledger и личные настройки. Только хэши."""
    result={}
    for table in m.Base.metadata.sorted_tables:
        if table.name in {'service_heartbeats','owner_sessions','voice_stations'}:continue
        records=[]
        for row in session.execute(select(table)).mappings():
            key=tuple(row[c.name] for c in table.primary_key.columns)
            if key in selected.get(table.name,set()):continue
            data=dict(row)
            if table.name=='tool_grants' and data.get('tool') in DIARY_TOOLS:
                data.pop('allowed',None)
            records.append(json.dumps(data,default=str,sort_keys=True,ensure_ascii=False))
        result[table.name]=hashlib.sha256('\n'.join(sorted(records)).encode()).hexdigest()
    return result


def reset(session, expected):
    from .desktop import lock
    lock(session)
    # Дожидаемся конкурирующих approvals/worker claims до построения окончательного плана.
    list(session.scalars(select(m.Task).with_for_update()))
    current=plan(session)
    if current['digest']!=expected:
        raise ValueError('Данные изменились. Пересмотрите план очистки.')
    list(session.scalars(select(m.ModelUsage).with_for_update()))
    before=retained_snapshot(session,current['selected'])
    for model in TABLES:
        columns=list(model.__table__.primary_key.columns)
        for keys in current['selected'][model.__tablename__]:
            session.execute(delete(model).where(*(column==key for column,key in zip(columns,keys))))
    if current['selected'][m.Skill.__tablename__]:
        for grant in session.scalars(select(m.ToolGrant).where(m.ToolGrant.tool.in_(DIARY_TOOLS))):grant.allowed=False
    session.flush()
    for model in TABLES:
        columns=list(model.__table__.primary_key.columns)
        for keys in current['selected'][model.__tablename__]:
            if session.scalar(select(model).where(*(column==key for column,key in zip(columns,keys)))) is not None:
                raise RuntimeError('Удаление не подтверждено')
    if retained_snapshot(session,current['selected'])!=before:
        raise ValueError('Сохраняемые данные изменились. Очистка отменена; пересмотрите план.')
    session.add(m.Audit(event='demo_reset_completed',details={'digest':expected,'counts':current['counts'],
                    'protected_demo_tasks':current['protected_demo_tasks'],'unknown_tasks':current['unknown_tasks']}))
    session.flush()
    return {'removed':current['counts'],'protected_demo_tasks':current['protected_demo_tasks'],'unknown_tasks':current['unknown_tasks'],'owner_memories':current['owner_memories'],'verified':True}
