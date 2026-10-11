"""Run additive Atlas migrations against one restored disposable database."""

import hashlib
import json
import os
import re
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.engine import make_url

from atlas.config import settings


def main():
    database = sys.argv[1]
    if not re.fullmatch(r"atlas_integration_drill_[a-f0-9]{16}", database):
        raise ValueError("Только временная БД")
    url = make_url(settings().database_url).set(database=database)
    os.environ["ATLAS_DATABASE_URL"] = url.render_as_string(hide_password=False)
    settings.cache_clear()
    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(engine)
    tables = [
        table for table in metadata.sorted_tables if table.name != "alembic_version"
    ]

    def snapshot():
        result = {}
        with engine.connect() as connection:
            for table in tables:
                digest = hashlib.sha256()
                count = 0
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ):
                    digest.update(
                        json.dumps(list(row), default=str, ensure_ascii=False).encode()
                    )
                    count += 1
                result[table.name] = (count, digest.hexdigest())
        return result

    before = snapshot()
    with engine.connect() as connection:
        original = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if original != "0018":
        raise ValueError("Ожидалась исходная ревизия 0018")
    config = Config("/app/alembic.ini")
    config.set_main_option("script_location", "/app/migrations")
    command.upgrade(config, "head")
    if snapshot() != before:
        raise RuntimeError("Прежние данные изменились")
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != "0023":
        raise RuntimeError("Итоговая ревизия не 0023")
    print(
        f"PASS: {original}→{revision}; {len(tables)} старых таблиц сохранены по количеству и SHA-256"
    )
    engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL: integration migration drill: " + type(exc).__name__)
        raise SystemExit(1)
