"""LDM-007 属性补齐：验证方式（多选）+ 验收准则 + 条目优先级（29148 §5.2.8 对齐，提案 2026-07-06 拍板）

Revision ID: q9h0i1j2k3l4
Revises: p8g9h0i1j2k3
Create Date: 2026-07-06 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'q9h0i1j2k3l4'
down_revision: Union[str, Sequence[str], None] = 'p8g9h0i1j2k3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 三列均可空：存量确认态条目不回补（确认态不原地改写；演示数据经 seed --reset 自然补齐）
    op.add_column('ldm007_requirement_item',
                  sa.Column('verification_method', sa.String(length=64), nullable=True))
    op.add_column('ldm007_requirement_item',
                  sa.Column('verification_note', sa.Text(), nullable=True))
    op.add_column('ldm007_requirement_item',
                  sa.Column('priority', sa.String(length=8), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ldm007_requirement_item', 'priority')
    op.drop_column('ldm007_requirement_item', 'verification_note')
    op.drop_column('ldm007_requirement_item', 'verification_method')
