"""Alembic 环境：挂载平台三个 store 的 MetaData，URL 从 DATABASE_URL 读取。

- runtime（agent_platform.infrastructure.sqlalchemy_store.metadata）
- evaluation（agent_platform.infrastructure.evaluation_sqlalchemy_store.metadata）
- evolution（agent_platform.infrastructure.evolution_sqlalchemy_store.metadata）

生产 schema 唯一入口是 `alembic upgrade head`（compose 中由 api 服务执行）；
store 构造时对非 SQLite 方言不再 create_all，二者职责互斥。
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 使 agent_platform 可导入（alembic.ini 未安装包时，源码在 src/ 下）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from agent_platform.infrastructure import (  # noqa: E402
    evaluation_sqlalchemy_store,
    evolution_sqlalchemy_store,
    sqlalchemy_store,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = [
    sqlalchemy_store.metadata,
    evaluation_sqlalchemy_store.metadata,
    evolution_sqlalchemy_store.metadata,
]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set; alembic requires an explicit database url"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", _database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
