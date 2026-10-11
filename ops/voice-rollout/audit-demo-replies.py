"""Дополнительный read-only аудит ответов на демо-инициативы в копии до очистки."""
import json
import os
import re
import sys
from sqlalchemy import select
from sqlalchemy.engine import make_url
from atlas.config import settings
from atlas.db import sessions
from atlas import models as m
from atlas.demo_reset import plan


def main():
    database=sys.argv[1]
    if not re.fullmatch(r'atlas_voice_drill_[a-f0-9]{16}',database):raise ValueError('Только временная БД')
    os.environ['ATLAS_DATABASE_URL']=make_url(settings().database_url).set(database=database).render_as_string(hide_password=False)
    settings.cache_clear()
    with sessions()() as s:
        chosen=plan(s)['selected']
        tids={key[0] for key in chosen['tasks']};mids={key[0] for key in chosen['messages']}
        tasks=list(s.scalars(select(m.Task)))
        questionable=[]
        for row in s.scalars(select(m.Message)):
            if row.id not in mids:continue
            own_demo=row.task_id in tids or any(t.id in tids and row.role=='user' and t.conversation_id==row.conversation_id and t.created_at==row.created_at and t.goal==row.content for t in tasks)
            if not own_demo and row.initiative_id and (row.role!='assistant' or row.task_id):questionable.append(row.id)
        print(json.dumps({'selected_messages':len(mids),'unproven_initiative_replies':len(questionable)}))
        assert not questionable,'Нужна точечная проверка и восстановление связанных ответов'


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: reply provenance audit: '+type(error).__name__);raise SystemExit(1)
