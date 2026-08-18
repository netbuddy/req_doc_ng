"""v2 需求管理工作台 · 质量诊断器：LDM-009 轮次表加可空 quality_meta 列

Revision ID: qm2a3b4c5d6e
Revises: u3l4m5n6o7p8
Create Date: 2026-07-08 00:00:00.000000

设计事实源：docs/proposals/requirement-management-redesign/02_质量诊断引擎与契约设计.md §5。
质量元数据为诊断轮次旁路产物（quality_profile / ears_rewrite / source_alignments / 各 finding 的
rule_code·evidence_span·severity·dimension）：可空 JSON，整列可丢弃不影响既有结论。存量行为 NULL。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'qm2a3b4c5d6e'
down_revision: Union[str, Sequence[str], None] = 'u3l4m5n6o7p8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'ldm009_diagnosis_round',
        sa.Column('quality_meta', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ldm009_diagnosis_round', 'quality_meta')
