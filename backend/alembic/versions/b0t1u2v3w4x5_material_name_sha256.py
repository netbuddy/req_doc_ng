"""材料一态制两列：name（展示标签）+ content_sha256（不可改写的机器凭据）

Revision ID: b0t1u2v3w4x5
Revises: a9s0t1u2v3w4
Create Date: 2026-08-07 10:00:00.000000

纯新增：ldm002_material 加两个带默认值的列，无回填、不改既有列、不删任何东西。
依据＝docs/v2/drafts/材料接入字段级差异表-讨论稿.md「5-补 材料一态制」（2026-08-07 用户裁定）。
存量材料两列为空串；哈希只在新导入时计算，不追溯补算（补算属走近再裁）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b0t1u2v3w4x5"
down_revision: Union[str, None] = "a9s0t1u2v3w4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ldm002_material", sa.Column("name", sa.String(200), nullable=False, server_default=""))
    op.add_column("ldm002_material", sa.Column("content_sha256", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("ldm002_material", "content_sha256")
    op.drop_column("ldm002_material", "name")
