"""Проверка Alembic на восстановленной БД без раскрытия данных/credentials."""
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
    target = sys.argv[2] if len(sys.argv) > 2 else '0014'
    if target not in {'0014', '0015', '0016', '0017', '0018'}: raise ValueError('Неизвестная ревизия')
    if not re.fullmatch(r'atlas_voice_drill_[a-f0-9]{16}', database):
        raise ValueError('Только отдельная временная БД')
    url = make_url(settings().database_url).set(database=database)
    # env.py создаёт engine из Settings, а не config.attributes['connection'].
    # Меняем только окружение этого одноразового контейнера до запуска Alembic.
    os.environ['ATLAS_DATABASE_URL'] = url.render_as_string(hide_password=False)
    settings.cache_clear()
    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(engine)
    tables = [table for table in metadata.sorted_tables if table.name != 'alembic_version']

    def snapshot():
        result = {}
        with engine.connect() as connection:
            for table in tables:
                query = select(table).order_by(*table.primary_key.columns)
                digest = hashlib.sha256()
                count = 0
                for row in connection.execute(query):
                    digest.update(json.dumps(list(row), default=str, ensure_ascii=False).encode())
                    count += 1
                result[table.name] = (count, digest.hexdigest())
        return result

    before = snapshot()
    with engine.connect() as connection:
        original = connection.scalar(text('SELECT version_num FROM alembic_version'))
    config = Config('/app/alembic.ini')
    config.set_main_option('script_location', '/app/migrations')
    command.upgrade(config, 'head')
    if snapshot() != before:
        raise RuntimeError('Существующие данные изменились при миграции')
    with engine.connect() as connection:
        revision = connection.scalar(text('SELECT version_num FROM alembic_version'))
    if revision != target:
        raise RuntimeError('Неверная итоговая ревизия')
    print(f'PASS: {original}→{target}; прежние данные {len(tables)} таблиц совпадают по количеству и SHA-256')
    engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('FAIL: migration drill: ' + type(error).__name__)
        raise SystemExit(1)
