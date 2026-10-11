ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
COPY backend/atlas ./atlas
COPY backend/migrations ./migrations
COPY backend/alembic.ini backend/pyproject.toml backend/requirements.lock ./
RUN chmod -R a+rX /app/atlas /app/migrations && chmod a+r /app/alembic.ini /app/pyproject.toml /app/requirements.lock
USER atlas
