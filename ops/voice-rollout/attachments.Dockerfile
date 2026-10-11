# Добавляются ровно две проверенные библиотеки; остальные зависимости сохранены.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
COPY .sessions/attachments-wheel/pypdf-6.19.0-py3-none-any.whl /tmp/pypdf.whl
RUN echo '7e5d6e730e7dae87d560a2cee218b852f6498c8be61966f3cd02ead971e48d14  /tmp/pypdf.whl' | sha256sum -c - && mv /tmp/pypdf.whl /tmp/pypdf-6.19.0-py3-none-any.whl && python -m pip install --no-index --no-deps /tmp/pypdf-6.19.0-py3-none-any.whl && rm /tmp/pypdf-6.19.0-py3-none-any.whl
COPY .sessions/attachments-wheel/pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl /tmp/pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
RUN echo '78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91  /tmp/pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl' | sha256sum -c - && python -m pip install --no-index --no-deps /tmp/pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl && rm /tmp/pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
COPY backend/atlas ./atlas
COPY backend/migrations ./migrations
COPY backend/alembic.ini backend/pyproject.toml backend/requirements.lock ./
USER atlas
