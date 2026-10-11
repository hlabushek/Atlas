# Только для обновления кода при совпадающем SHA-256 requirements.lock.
# BASE_IMAGE должен указывать на сохранённый образ текущей установки.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /app
COPY backend/atlas ./atlas
COPY backend/migrations ./migrations
COPY backend/alembic.ini backend/pyproject.toml backend/requirements.lock ./
