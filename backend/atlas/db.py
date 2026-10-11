from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .config import settings

@lru_cache
def engine():
    url = settings().database_url
    return create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}, pool_pre_ping=True)

@lru_cache
def sessions():
    return sessionmaker(engine(), expire_on_commit=False)

def db():
    with sessions()() as session:
        yield session
