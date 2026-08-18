"""项目管理组重构：删状态死列，加操作者与幂等键两列

Revision ID: c1u2v3w4x5y6
Revises: b0t1u2v3w4x5
Create Date: 2026-08-07 16:00:00.000000

依据＝docs/v2/drafts/项目管理字段级差异表-讨论稿.md（2026-08-07 用户裁定四件全做）。
三个动作：①删 status 列——全库恒为 active、无任何写入路径与归档操作，是死列；
②加 operator_ref（创建操作者，存量行空串）；③加 idempotency_key（创建幂等键，
存量行 NULL，唯一索引）。降级时 status 恢复为恒 active（原本也只有这一个值，无信息损失）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c1u2v3w4x5y6"
down_revision: Union[str, None] = "b0t1u2v3w4x5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("ldm001_project", "status")
    op.add_column("ldm001_project", sa.Column("operator_ref", sa.String(64), nullable=False, server_default=""))
    op.add_column("ldm001_project", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.create_index("ix_ldm001_project_idempotency_key", "ldm001_project", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_ldm001_project_idempotency_key", table_name="ldm001_project")
    op.drop_column("ldm001_project", "idempotency_key")
    op.drop_column("ldm001_project", "operator_ref")
    op.add_column("ldm001_project", sa.Column("status", sa.String(32), nullable=False, server_default="active"))
