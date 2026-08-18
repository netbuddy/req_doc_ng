"""诊断问题否决留痕表 + 裁决改稿列（T20260720：逐条否决防重提 + 采纳前逐条可编辑）

Revision ID: z8r9s0t1u2v3
Revises: y7q8r9s0t1u2
Create Date: 2026-07-20 12:00:00.000000

纯新增：一张新表 + 一个可空列。无回填、不改既有列形状、不删任何东西。
- ldm009_finding_veto：用户裁定「这条不是问题」的问题指纹（规则码 + 证据片段），跨轮生效；
  撤销写 revoked_at 而非删行。
- ldm009_diagnosis_round.adjudication_point_edits：采纳修订时用户对所选点替换文本的改稿
  （JSON：{point_ref: 用户终稿}）。AI 原案仍在 revision_points 列原样保留，两者并存。

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'z8r9s0t1u2v3'
down_revision: Union[str, Sequence[str], None] = 'y7q8r9s0t1u2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ldm009_finding_veto',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('item_ref', sa.Uuid(), nullable=False),
        sa.Column('rule_code', sa.String(length=64), nullable=True),
        sa.Column('evidence_span', sa.Text(), nullable=True),
        sa.Column('finding_type', sa.String(length=40), nullable=False),
        sa.Column('finding_summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('origin_finding_ref', sa.Uuid(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ldm009_finding_veto_project_id', 'ldm009_finding_veto', ['project_id'])
    op.create_index('ix_ldm009_finding_veto_item_ref', 'ldm009_finding_veto', ['item_ref'])
    op.create_index(
        'ix_ldm009_finding_veto_idempotency_key', 'ldm009_finding_veto',
        ['idempotency_key'], unique=True,
    )
    op.add_column(
        'ldm009_diagnosis_round',
        sa.Column('adjudication_point_edits', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ldm009_diagnosis_round', 'adjudication_point_edits')
    op.drop_index('ix_ldm009_finding_veto_idempotency_key', table_name='ldm009_finding_veto')
    op.drop_index('ix_ldm009_finding_veto_item_ref', table_name='ldm009_finding_veto')
    op.drop_index('ix_ldm009_finding_veto_project_id', table_name='ldm009_finding_veto')
    op.drop_table('ldm009_finding_veto')
