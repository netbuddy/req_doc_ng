"""领域档案（P6b）：ldm001_project 增记 domain_profile_key（领域上下文封闭集 key）

Revision ID: u3l4m5n6o7p8
Revises: t2k3l4m5n6o7
Create Date: 2026-07-08 00:00:00.000000

设计事实源：docs/proposals/knowledge-item-upgrade/08 §2.2；40 增补 §5.3。
- 本工作包唯一触碰 LDM-001 结构的点；nullable、默认 None=generic（等同未启用领域档案，P6a 行为）。
- 存量项目 domain_profile_key 为空 → 识别行为与 P6 前一致（AC-P6-05 迁移与默认行为）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'u3l4m5n6o7p8'
down_revision: Union[str, Sequence[str], None] = 't2k3l4m5n6o7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema：增可空列，存量行为空（=generic），零行为变化。"""
    op.add_column(
        'ldm001_project',
        sa.Column('domain_profile_key', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema：降级即移除列（领域档案功能可整体 revert）。"""
    op.drop_column('ldm001_project', 'domain_profile_key')
