"""全局检索工作包 P0 · 派生检索索引 search_index（pgvector + pg_trgm）

Revision ID: xj7k8l9m0n1p
Revises: qm2a3b4c5d6e
Create Date: 2026-07-09 00:00:00.000000

设计事实源：docs/proposals/global-search-command-palette/06_基础设施与图谱前向兼容设计.md §1-2、
01_检索架构总体设计.md §5。search_index 是从五类事实源投影的**去规范化派生索引**（可整层重算、
可整表重建）；本迁移是本工作包唯一的 DB 结构新增，不改任何既有表（README 不变式 1/2）。

分支：
- Postgres：建 vector / pg_trgm 扩展 + search_index（含 Vector(1024) 列）+ HNSW 向量索引
  （vector_cosine_ops）+ GIN 三元组索引（title/body）+ project_id B-tree + 唯一 (project_id,entity_type,ref)。
- SQLite（测试库经 Base.metadata.create_all 建表，通常不跑本迁移；此分支保证 `alembic upgrade` 在
  SQLite 亦不报错）：建等价无向量列表（embedding 为 Text 恒 NULL），跳过扩展与向量/三元组索引，
  检索走 Python 子串降级（03 §4）。

维度：Vector(1024) 与 config.embedding_dim 默认一致（dashscope text-embedding-v3 等）；换维需新迁移
+ 全量重嵌（06 §6 风险）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'xj7k8l9m0n1p'
down_revision: Union[str, Sequence[str], None] = 'qm2a3b4c5d6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 config.embedding_dim 默认一致；迁移是不可变快照，换维请新增迁移而非改此常量。
EMBEDDING_DIM = 1024


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        # pgvector（Vector 类型 / HNSW）与 pg_trgm（GIN 三元组）：plain postgres:16 不含 pgvector，
        # 镜像已切 pgvector/pgvector:pg16（docker-compose）。IF NOT EXISTS：卷沿用、可幂等重跑。
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        embedding_col = sa.Column('embedding', Vector(EMBEDDING_DIM), nullable=True)
    else:
        # SQLite：无向量类型，embedding 存 Text（恒 NULL），检索走子串降级。
        embedding_col = sa.Column('embedding', sa.Text(), nullable=True)

    op.create_table(
        'search_index',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        # entity_type == IndexableNode.node_type == 未来图节点 label
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        # ref = 稳定语义引用（(asset_type, ref) 口径），非行 PK（不变式 4）
        sa.Column('ref', sa.String(length=128), nullable=False),
        sa.Column('title', sa.Text(), nullable=False, server_default=''),
        sa.Column('body', sa.Text(), nullable=False, server_default=''),
        sa.Column('content_hash', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        embedding_col,
        sa.UniqueConstraint(
            'project_id', 'entity_type', 'ref', name='uq_search_index_identity'
        ),
    )
    op.create_index('ix_search_index_project_id', 'search_index', ['project_id'])

    if is_pg:
        # HNSW 余弦向量索引（与查询 embedding <=> qvec / vector_cosine_ops 一致，03 §1.1）。
        op.execute(
            "CREATE INDEX ix_search_index_embedding_hnsw ON search_index "
            "USING hnsw (embedding vector_cosine_ops)"
        )
        # GIN 三元组索引：title/body 上的 pg_trgm 模糊/相似查询走索引（词法 lane，03 §1.2）。
        op.execute(
            "CREATE INDEX ix_search_index_body_trgm ON search_index "
            "USING gin (body gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_search_index_title_trgm ON search_index "
            "USING gin (title gin_trgm_ops)"
        )


def downgrade() -> None:
    """Downgrade schema。整表可丢弃（派生索引），不误伤任何既有表。"""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # drop_table 连带删除其上 HNSW/GIN/B-tree 索引与唯一约束。
    op.drop_table('search_index')

    if is_pg:
        # 扩展仅本工作包使用（迁移前全库无 vector/pg_trgm 依赖）；无 CASCADE，若他处已依赖则显式报错。
        op.execute("DROP EXTENSION IF EXISTS vector")
        op.execute("DROP EXTENSION IF EXISTS pg_trgm")
