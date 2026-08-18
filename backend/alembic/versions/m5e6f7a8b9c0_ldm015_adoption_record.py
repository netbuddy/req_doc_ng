"""ldm015_adoption_record（AI效能统计口径设计 §7：LDM-015 采纳结论明细）

Revision ID: m5e6f7a8b9c0
Revises: l4d5e6f7a8b9
Create Date: 2026-07-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'm5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'l4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ldm015_adoption_record',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('model_result_ref', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('subject_type', sa.String(length=32), nullable=False),
    sa.Column('subject_ref', sa.Uuid(), nullable=False),
    sa.Column('outcome', sa.String(length=32), nullable=False),
    sa.Column('basis_ref', sa.Uuid(), nullable=True),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm015_adoption_record_model_result_ref'), 'ldm015_adoption_record', ['model_result_ref'], unique=False)
    op.create_index(op.f('ix_ldm015_adoption_record_project_id'), 'ldm015_adoption_record', ['project_id'], unique=False)
    op.create_index(op.f('ix_ldm015_adoption_record_stage'), 'ldm015_adoption_record', ['stage'], unique=False)
    op.create_index(op.f('ix_ldm015_adoption_record_subject_ref'), 'ldm015_adoption_record', ['subject_ref'], unique=False)
    op.create_index(op.f('ix_ldm015_adoption_record_idempotency_key'), 'ldm015_adoption_record', ['idempotency_key'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ldm015_adoption_record_idempotency_key'), table_name='ldm015_adoption_record')
    op.drop_index(op.f('ix_ldm015_adoption_record_subject_ref'), table_name='ldm015_adoption_record')
    op.drop_index(op.f('ix_ldm015_adoption_record_stage'), table_name='ldm015_adoption_record')
    op.drop_index(op.f('ix_ldm015_adoption_record_project_id'), table_name='ldm015_adoption_record')
    op.drop_index(op.f('ix_ldm015_adoption_record_model_result_ref'), table_name='ldm015_adoption_record')
    op.drop_table('ldm015_adoption_record')
