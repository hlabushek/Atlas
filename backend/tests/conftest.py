import os
import uuid
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from atlas.config import settings
from atlas.db import engine, sessions
from atlas.api import app, failures

@pytest.fixture
def client(tmp_path, monkeypatch):
    postgres = os.environ.get("ATLAS_TEST_POSTGRES_URL")
    admin = None
    schema = "atlas_test_" + uuid.uuid4().hex
    if postgres:
        admin = create_engine(postgres)
        with admin.begin() as conn: conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(postgres).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    else:
        url = "sqlite:///" + str(tmp_path / "test.db")
    monkeypatch.setenv("ATLAS_DATABASE_URL", url)
    monkeypatch.setenv("ATLAS_OWNER_PASSWORD", "test-owner-password-123")
    monkeypatch.setenv("ATLAS_SESSION_SECRET", "test-session-secret-with-at-least-32-characters")
    monkeypatch.setenv("ATLAS_MODEL_PROVIDER", "demo")
    settings.cache_clear(); engine.cache_clear(); sessions.cache_clear(); failures.clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")
        if postgres:
            with engine().connect() as conn:
                assert conn.scalar(text("SELECT current_schema()")) == schema
                assert conn.scalar(text("SELECT COUNT(*) FROM conversations")) == 0
        with TestClient(app) as c:
            assert c.post("/api/login", json={"password":"test-owner-password-123"}).status_code == 200
            c.headers["X-Atlas-Client"] = "web"
            yield c
    finally:
        engine().dispose(); settings.cache_clear(); engine.cache_clear(); sessions.cache_clear()
        if admin:
            with admin.begin() as conn: conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
