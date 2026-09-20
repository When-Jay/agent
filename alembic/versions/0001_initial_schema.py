"""initial schema: runtime + evaluation + evolution 平台全量表。

基线迁移策略：直接按三个 store 的 MetaData 建表（create_all 语义，
checkfirst 幂等）。这样：
- 全新库：一次性建全平台表；
- 存量库（旧版本靠 create_all 建表）：已有表自动跳过，仅登记版本号，
  完成向迁移链路的交接。

后续表结构演进使用 `alembic revision --autogenerate` 对比真实库生成增量迁移。

Revision ID: 0001
Revises:
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op

from agent_platform.infrastructure import (
    evaluation_sqlalchemy_store,
    evolution_sqlalchemy_store,
    sqlalchemy_store,
)

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 建表顺序：runtime → evaluation → evolution；回滚时反向 drop。
_METADATAS = (
    sqlalchemy_store.metadata,
    evaluation_sqlalchemy_store.metadata,
    evolution_sqlalchemy_store.metadata,
)


def upgrade() -> None:
    bind = op.get_bind()
    for schema_metadata in _METADATAS:
        schema_metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    for schema_metadata in reversed(_METADATAS):
        schema_metadata.drop_all(bind=bind)
