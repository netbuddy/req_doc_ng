"""条目档案 P2：LDM-007 补撰写字段 + 条目陈述达标投影过程表（增补 §3/§4；非事实源，可整层重算）

Revision ID: p8g9h0i1j2k3
Revises: o7f8a9b0c1d2
Create Date: 2026-07-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'p8g9h0i1j2k3'
down_revision: Union[str, Sequence[str], None] = 'o7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # LDM-007 撰写字段（20 基线 §5.7 关键业务数据项；类型无关，权威仍是文本）
    op.add_column('ldm007_requirement_item', sa.Column('curation_note', sa.Text(), nullable=True))
    op.add_column('ldm007_requirement_item', sa.Column('boundary_note', sa.Text(), nullable=True))

    op.create_table(
        'process_item_structure_projection',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('item_ref', sa.Uuid(), nullable=False),
        sa.Column('item_content_rev', sa.Integer(), nullable=False),
        sa.Column('profile_version', sa.Integer(), nullable=False),
        sa.Column('row_kind', sa.String(length=8), nullable=False),
        sa.Column('key', sa.String(length=48), nullable=False),
        sa.Column('facet_status', sa.String(length=16), nullable=True),
        sa.Column('value_text', sa.Text(), nullable=True),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('statement_conformance', sa.String(length=16), nullable=True),
        sa.Column('completeness', sa.String(length=16), nullable=True),
        sa.Column('model_result_ref', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_process_item_structure_projection_item_ref'),
        'process_item_structure_projection', ['item_ref'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_process_item_structure_projection_item_ref'),
        table_name='process_item_structure_projection',
    )
    op.drop_table('process_item_structure_projection')
    op.drop_column('ldm007_requirement_item', 'boundary_note')
    op.drop_column('ldm007_requirement_item', 'curation_note')
