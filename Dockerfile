# AI Runtime Platform image: API + Celery worker share one build.
# Entry point differs per service via docker-compose `command`.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
# 数据库迁移脚本随镜像分发（api 容器启动时执行 alembic upgrade head）
COPY alembic.ini ./
COPY alembic ./alembic

# psycopg2-binary wheels bundle libpq; no system packages needed.
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "agent_platform.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
