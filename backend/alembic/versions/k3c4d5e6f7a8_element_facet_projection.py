"""TC-08 要素完备度投影过程表（设计增补 §3；非事实源，可整层重算）

Revision ID: k3c4d5e6f7a8
Revises: j2b3c4d5e6f7
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'k3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'j2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'process_element_facet_projection',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('element_ref', sa.Uuid(), nullable=False),
        sa.Column('element_version', sa.Integer(), nullable=False),
        sa.Column('rubric_version', sa.Integer(), nullable=False),
        sa.Column('facet_key', sa.String(length=48), nullable=False),
        sa.Column('facet_status', sa.String(length=16), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('correctness', sa.String(length=32), nullable=True),
        sa.Column('completeness', sa.String(length=16), nullable=True),
        sa.Column('model_result_ref', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_process_element_facet_projection_element_ref'),
        'process_element_facet_projection', ['element_ref'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_process_element_facet_projection_element_ref'),
        table_name='process_element_facet_projection',
    )
    op.drop_table('process_element_facet_projection')
