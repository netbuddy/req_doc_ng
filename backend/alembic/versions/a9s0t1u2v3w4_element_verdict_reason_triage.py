"""知识项裁定理由 + 建议剔除候选人工处置标记（T20260724-suspected-noise-triage）

Revision ID: a9s0t1u2v3w4
Revises: z8r9s0t1u2v3
Create Date: 2026-07-25 09:00:00.000000

纯新增：LDM-005 两个可空列。无回填、不改既有列形状、不删任何东西。
- verdict_reason：模型给该条裁定的具体理由（证据字段，与 model_verdict 同批落库后不可改写）。
  存量行为 NULL，读侧回落到该裁定的通用判据，UI 不留空。
- noise_triage：人工对「AI 建议剔除的候选」的处置标记（NoiseTriage）。NULL＝未处置，
  仍留在候选区；'restored'＝人工撤回到正常列表。撤回只写本列，绝不改写 model_verdict——
  模型意见与人工裁定并存，是 AI 效能统计「模型误杀率」的原料。

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9s0t1u2v3w4'
down_revision: Union[str, Sequence[str], None] = 'z8r9s0t1u2v3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'ldm005_requirement_element',
        sa.Column('verdict_reason', sa.Text(), nullable=True),
    )
    op.add_column(
        'ldm005_requirement_element',
        sa.Column('noise_triage', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ldm005_requirement_element', 'noise_triage')
    op.drop_column('ldm005_requirement_element', 'verdict_reason')
