"""knowledge schema: knowledge/RAG 四张表（knowledge-rag-spec.md S3）。

镜像 0001 基线策略：按 store 的 MetaData 建表（create_all 语义，
checkfirst 幂等）。全新库一次性建表；存量库已有表自动跳过。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op

from agent_platform.infrastructure import knowledge_sqlalchemy_store

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    knowledge_sqlalchemy_store.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    knowledge_sqlalchemy_store.metadata.drop_all(bind=op.get_bind())
