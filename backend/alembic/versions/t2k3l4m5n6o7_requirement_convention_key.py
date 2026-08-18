"""需求规约方案可配置化（C1/C2）：批次与投影增记 convention_key（口径锚）

Revision ID: t2k3l4m5n6o7
Revises: s1j2k3l4m5n6
Create Date: 2026-07-07 00:00:00.000000

设计事实源：docs/40 domains/DS-001/需求规约方案与档案选型.md §5。
- process_item_formation_request：批次发起时固定的生效规约方案（批次内一致）。
- process_item_structure_projection：判定所依据的规约方案（徽章口径锚；方案切换不追溯）。
两列均非空、server_default='ears-cn'，存量行由默认值回填 ears-cn（=切换前的隐式方案，零行为变化）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't2k3l4m5n6o7'
down_revision: Union[str, Sequence[str], None] = 's1j2k3l4m5n6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 批次固定方案：存量批次回填 ears-cn（历史批次即按现行 ears-cn 档案格式化）。
    op.add_column(
        'process_item_formation_request',
        sa.Column('convention_key', sa.String(length=32), nullable=False, server_default='ears-cn'),
    )
    # 投影口径锚：存量投影回填 ears-cn（投影可整层重算，回填为安全兜底）。
    op.add_column(
        'process_item_structure_projection',
        sa.Column('convention_key', sa.String(length=32), nullable=False, server_default='ears-cn'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('process_item_structure_projection', 'convention_key')
    op.drop_column('process_item_formation_request', 'convention_key')
