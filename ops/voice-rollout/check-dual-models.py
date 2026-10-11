"""Проверка метаданных: не печатать ключи, транскрипции или ответы владельца."""
import json
import os
from sqlalchemy import select
from atlas.db import sessions
from atlas.models import ModelPreferences, Task, ModelUsage, VoiceRequest
from atlas.model_settings import Preferences

with sessions()() as session:
    row=session.get(ModelPreferences,1)
    config=Preferences.model_validate(row.config)
    assert config.main.model=='deepseek/deepseek-v4-flash'
    assert config.planner.model=='deepseek/deepseek-v4.1-flash'
    assert config.auto_chat_routing
    print('PASS: persisted main V4 / planner V4.1 / auto; revision '+str(row.revision))
    ids=[x for x in os.environ.get('ATLAS_PROBE_IDS','').split(',') if x]
    for identifier in ids:
        task=session.get(Task,identifier)
        assert task is not None
        usage=session.scalars(select(ModelUsage).where(ModelUsage.task_id==identifier)).all()
        receipt=session.get(VoiceRequest,identifier)
        if receipt and not receipt.keep_text and task.status=='COMPLETED':
            assert receipt.consumed and not task.result and not receipt.context
            print('PASS: probe delivered, temporary voice reply/context cleared')
        print(json.dumps({'id':task.id,'status':task.status,'model':task.model_profile['model'],'role':task.model_profile['role'],'routing_reason':task.model_profile['routing_reason'],'provider_calls':len(usage),'usage_status':[x.status for x in usage],'error':task.error},ensure_ascii=False))
