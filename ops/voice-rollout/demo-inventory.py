"""Только статистика происхождения, без текста, названий, credentials и изменений."""
import json
from sqlalchemy import text
from atlas.db import engine

queries={
    'counts': "SELECT 'conversations' kind,count(*) n FROM conversations UNION ALL SELECT 'messages',count(*) FROM messages UNION ALL SELECT 'tasks',count(*) FROM tasks UNION ALL SELECT 'notes',count(*) FROM notes UNION ALL SELECT 'memories',count(*) FROM memories UNION ALL SELECT 'initiatives',count(*) FROM initiatives",
    'task_profiles': "SELECT coalesce(model_profile->>'provider','unknown') provider,kind,status,count(*) n FROM tasks GROUP BY 1,2,3 ORDER BY 1,2,3",
    'reply_providers': "SELECT details->>'provider' provider,count(*) n FROM audit WHERE event='reply_completed' GROUP BY 1 ORDER BY 1",
    'memory_sources': "SELECT source,count(*) n FROM memories GROUP BY 1 ORDER BY 1",
    'assignment_providers': "SELECT provider,count(*) n FROM assignments GROUP BY 1 ORDER BY 1",
    'demo_homework': "SELECT count(*) n FROM demo_homework",
    'demo_skill': "SELECT enabled,health FROM skills WHERE id='diary-demo'",
    'demo_replies': "SELECT count(*) n FROM messages WHERE role='assistant' AND content LIKE 'Демо-режим: языковая модель не подключена.%'",
    'task_protection': "SELECT count(*) n FROM tasks WHERE status IN ('PENDING','PLANNING','RUNNING','WAITING_APPROVAL','WAITING_DEVICE','PAUSED')",
}
with engine().connect() as connection:
    for label,query in queries.items():
        print(json.dumps({label:[dict(r) for r in connection.execute(text(query)).mappings()]},ensure_ascii=False))
