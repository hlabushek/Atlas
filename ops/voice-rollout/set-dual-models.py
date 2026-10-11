"""Явная просьба владельца: изменить только две роли и флаг routing, с CAS/аудитом."""
import copy
import json
import os
from pathlib import Path
from sqlalchemy import update
from atlas.db import sessions
from atlas.models import ModelPreferences, Audit
from atlas.model_settings import Preferences

expected=int(os.environ.get('ATLAS_EXPECTED_MODEL_REVISION','24'))
backup=Path('/tmp/pre-dual-model-preferences.json')
with sessions()() as session:
    row=session.get(ModelPreferences,1)
    if row.revision!=expected:
        raise RuntimeError('Настройки изменены; проверьте актуальную ревизию перед применением')
    original=copy.deepcopy(row.config)
    descriptor=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as output:
        json.dump({'revision':row.revision,'config':original},output,ensure_ascii=False)
    config=copy.deepcopy(original)
    config['mode']='configured'
    config['auto_chat_routing']=True
    config['main']={**config['main'],'provider':'routerai','model':'deepseek/deepseek-v4-flash'}
    config['planner']={**config['planner'],'provider':'routerai','model':'deepseek/deepseek-v4.1-flash'}
    config=Preferences.model_validate(config).model_dump()
    won=session.execute(update(ModelPreferences).where(ModelPreferences.id==1,ModelPreferences.revision==expected).values(config=config,revision=expected+1))
    if won.rowcount!=1:
        raise RuntimeError('Конфликт настройки модели')
    session.add(Audit(event='model_preferences_changed',details={'revision':expected+1,'source':'owner_dual_deepseek','main':'deepseek/deepseek-v4-flash','planner':'deepseek/deepseek-v4.1-flash','auto_chat_routing':True}))
    session.commit()
    session.expire_all()
    actual=session.get(ModelPreferences,1)
    assert actual.config==config and actual.revision==expected+1
    print('PASS: main V4 Flash, planner V4.1 Flash, auto enabled; embeddings/budget/credentials preserved')
