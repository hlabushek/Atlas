"""Операторская очистка только подтверждённого демо. Никаких секретов/текстов в выводе."""
import json
import os
import re
import sys
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from atlas.config import settings
from atlas.db import sessions
from atlas import models as m
from atlas.demo_reset import public_plan, reset


def main():
    mode=sys.argv[1]
    if mode=='drill':
        database=sys.argv[2]
        if not re.fullmatch(r'atlas_voice_drill_[a-f0-9]{16}',database):raise ValueError('Только временная БД')
        url=make_url(settings().database_url).set(database=database)
        os.environ['ATLAS_DATABASE_URL']=url.render_as_string(hide_password=False)
        settings.cache_clear()
    elif mode not in {'preview','--production-confirmed-demo-only'}:
        raise ValueError('Неизвестный режим')
    with sessions()() as s:
        assert s.scalar(text('SELECT version_num FROM alembic_version'))=='0017'
        if mode=='--production-confirmed-demo-only':
            assert make_url(settings().database_url).database=='atlas'
        preview=public_plan(s)
        print(json.dumps({'mode':mode,'preview':preview},ensure_ascii=False))
        if mode=='preview':return
        result=reset(s,preview['digest']);s.commit()
        print(json.dumps({'result':result},ensure_ascii=False))
    with sessions()() as s:
        remaining=public_plan(s)
        assert not remaining['counts'],'Остались выбранные демо-строки'
        print(json.dumps({'remaining':remaining,'retained':{
            'conversations':len(list(s.scalars(select(m.Conversation.id)))),
            'tasks':len(list(s.scalars(select(m.Task.id)))),
            'notes':len(list(s.scalars(select(m.Note.id)))),
            'model_usage':len(list(s.scalars(select(m.ModelUsage.id)))),
            'school_assignments':len(list(s.scalars(select(m.Assignment.id).where(m.Assignment.provider=='school.mos.ru')))),
            'model_revision':s.get(m.ModelPreferences,1).revision,
        }},ensure_ascii=False))
    print('PASS: targeted deletion verified; all retained data hashes matched before commit')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('FAIL: demo reset: '+type(error).__name__)
        raise SystemExit(1)
