from .db import sessions
from .models import ServiceHeartbeat,now
def heartbeat(name):
    if name not in {'worker','scheduler','telegram'}:raise ValueError('Unknown service')
    with sessions()() as s:
        if s.bind.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert
        else:from sqlalchemy.dialects.sqlite import insert
        statement=insert(ServiceHeartbeat).values(name=name,seen_at=now())
        s.execute(statement.on_conflict_do_update(index_elements=['name'],set_={'seen_at':statement.excluded.seen_at}));s.commit()
