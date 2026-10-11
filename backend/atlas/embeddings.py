"""Real 768-dimensional provider; immutable namespace prevents model mixing."""
import hashlib,math,httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select,delete
from .db import sessions
from .models import Memory,Embedding
from .memory import contains_secret
from .budget import request
from .model_settings import resolve

def digest(text):return hashlib.sha256(text.encode()).hexdigest()
def enabled(session=None):return resolve('embedding',session).ready
def key(text,runtime=None):return digest((runtime or resolve('embedding')).identity+'\0'+text)
def vector(text,runtime=None):
    if contains_secret(text):raise ValueError('Секреты запрещены')
    r=runtime or resolve('embedding');r.require()
    headers={'Authorization':'Bearer '+r.key} if r.key else {}
    with httpx.Client(timeout=30,follow_redirects=False) as c:
        data=request(c,r.url.rstrip('/')+'/embeddings',headers,{'model':r.model,'input':text,'dimensions':768},'embedding',(r.provider+':'+r.model)[:100], rates=r.rates)
    v=data['data'][0]['embedding']
    if not isinstance(v,list) or len(v)!=768 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) or sum(x*x for x in v)==0:raise ValueError('Некорректный embedding: требуется 768 измерений')
    return v

def warm(query):
    r=resolve('embedding')
    if not r.ready or not query or contains_secret(query):return
    with sessions()() as s:
        if s.get(Embedding,key(query,r)):return
    v=vector(query,r)
    with sessions()() as s:
        if resolve('embedding',s).identity!=r.identity:return
        if not s.get(Embedding,key(query,r)):
            s.add(Embedding(key=key(query,r),model=r.identity,digest=digest(query),vector=v))
            try:s.commit()
            except IntegrityError:s.rollback()

def index_one():
    r=resolve('embedding')
    if not r.ready:return False
    with sessions()() as s:
        rows=s.scalars(select(Memory).order_by(Memory.created_at)).all()
        candidate=next((m for m in rows if not s.scalar(select(Embedding.key).where(Embedding.memory_id==m.id,Embedding.model==r.identity,Embedding.digest==digest(m.content)))),None)
        if candidate is None:return False
        identifier,text=candidate.id,candidate.content
    v=vector(text,r)
    with sessions()() as s:
        m=s.scalar(select(Memory).where(Memory.id==identifier).with_for_update())
        if m and m.content==text and resolve('embedding',s).identity==r.identity:
            s.execute(delete(Embedding).where(Embedding.memory_id==identifier))
            s.add(Embedding(key=digest(identifier+key(text,r)),memory_id=identifier,model=r.identity,digest=digest(text),vector=v))
            try:s.commit()
            except IntegrityError:s.rollback()
    return True

def ranked(session, query):
    r=resolve('embedding',session)
    if not r.ready:return []
    q=session.get(Embedding,key(query,r))
    if q is None:return []
    stmt=select(Embedding).where(Embedding.memory_id.is_not(None),Embedding.model==r.identity)
    if session.bind.dialect.name=='postgresql':stmt=stmt.order_by(Embedding.vector.op('OPERATOR(public.<=>)')(q.vector)).limit(30)
    rows=session.scalars(stmt).all()
    if session.bind.dialect.name!='postgresql':
        def cosine(e):return sum(float(a)*float(b) for a,b in zip(e.vector,q.vector))/(sum(float(a)**2 for a in e.vector)**.5*sum(float(a)**2 for a in q.vector)**.5)
        rows=sorted(rows,key=cosine,reverse=True)[:30]
    return [e.memory_id for e in rows if (m:=session.get(Memory,e.memory_id)) and e.digest==digest(m.content)]
