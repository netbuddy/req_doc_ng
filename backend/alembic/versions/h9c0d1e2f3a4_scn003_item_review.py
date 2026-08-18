"""SCN-003 条目评审：诊断批次过程记录 + LDM-009 诊断轮次 + 诊断发现项

Revision ID: h9c0d1e2f3a4
Revises: g7b8c9d0e1f2
Create Date: 2026-07-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'h9c0d1e2f3a4'
down_revision: Union[str, Sequence[str], None] = 'g7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'process_item_diagnosis_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('parse_context_ref', sa.Uuid(), nullable=False),
        sa.Column('parse_result_ref', sa.Uuid(), nullable=False),
        sa.Column('review_context_ref', sa.Uuid(), nullable=False),
        sa.Column('item_refs', sa.Text(), nullable=False),
        sa.Column('diagnosis_mode', sa.String(length=24), nullable=False),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('stop_next_action', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_process_item_diagnosis_request_project_id'), 'process_item_diagnosis_request', ['project_id'], unique=False)
    op.create_index(op.f('ix_process_item_diagnosis_request_parse_context_ref'), 'process_item_diagnosis_request', ['parse_context_ref'], unique=False)
    op.create_index(op.f('ix_process_item_diagnosis_request_parse_result_ref'), 'process_item_diagnosis_request', ['parse_result_ref'], unique=False)
    op.create_index(op.f('ix_process_item_diagnosis_request_review_context_ref'), 'process_item_diagnosis_request', ['review_context_ref'], unique=False)
    op.create_index(op.f('ix_process_item_diagnosis_request_idempotency_key'), 'process_item_diagnosis_request', ['idempotency_key'], unique=True)

    op.create_table(
        'ldm009_diagnosis_round',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('item_ref', sa.Uuid(), nullable=False),
        sa.Column('batch_ref', sa.Uuid(), nullable=False),
        sa.Column('round_no', sa.Integer(), nullable=False),
        sa.Column('diagnosis_mode', sa.String(length=24), nullable=False),
        sa.Column('processing_status', sa.String(length=24), nullable=False),
        sa.Column('context_coverage', sa.Text(), nullable=False),
        sa.Column('model_result_ref', sa.Uuid(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('invalidated', sa.Boolean(), nullable=False),
        sa.Column('invalidated_reason', sa.Text(), nullable=True),
        sa.Column('confirm_result', sa.String(length=24), nullable=True),
        sa.Column('confirm_basis', sa.Text(), nullable=True),
        sa.Column('confirmed_by', sa.String(length=64), nullable=True),
        sa.Column('confirm_idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('confirm_idempotency_key'),
    )
    op.create_index(op.f('ix_ldm009_diagnosis_round_project_id'), 'ldm009_diagnosis_round', ['project_id'], unique=False)
    op.create_index(op.f('ix_ldm009_diagnosis_round_item_ref'), 'ldm009_diagnosis_round', ['item_ref'], unique=False)
    op.create_index(op.f('ix_ldm009_diagnosis_round_batch_ref'), 'ldm009_diagnosis_round', ['batch_ref'], unique=False)

    op.create_table(
        'ldm009_review_finding',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('round_ref', sa.Uuid(), nullable=False),
        sa.Column('item_ref', sa.Uuid(), nullable=False),
        sa.Column('finding_type', sa.String(length=40), nullable=False),
        sa.Column('diagnosis_summary', sa.Text(), nullable=False),
        sa.Column('basis_summary', sa.Text(), nullable=False),
        sa.Column('suggested_disposition', sa.String(length=32), nullable=False),
        sa.Column('suggested_field', sa.String(length=32), nullable=True),
        sa.Column('suggested_value', sa.Text(), nullable=True),
        sa.Column('suggested_reason', sa.Text(), nullable=True),
        sa.Column('suggestion_ref', sa.Uuid(), nullable=True),
        sa.Column('model_result_ref', sa.Uuid(), nullable=True),
        sa.Column('decision', sa.String(length=32), nullable=True),
        sa.Column('decision_reason', sa.Text(), nullable=True),
        sa.Column('decision_operator', sa.String(length=64), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('decision_idempotency_key'),
    )
    op.create_index(op.f('ix_ldm009_review_finding_round_ref'), 'ldm009_review_finding', ['round_ref'], unique=False)
    op.create_index(op.f('ix_ldm009_review_finding_item_ref'), 'ldm009_review_finding', ['item_ref'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ldm009_review_finding_item_ref'), table_name='ldm009_review_finding')
    op.drop_index(op.f('ix_ldm009_review_finding_round_ref'), table_name='ldm009_review_finding')
    op.drop_table('ldm009_review_finding')
    op.drop_index(op.f('ix_ldm009_diagnosis_round_batch_ref'), table_name='ldm009_diagnosis_round')
    op.drop_index(op.f('ix_ldm009_diagnosis_round_item_ref'), table_name='ldm009_diagnosis_round')
    op.drop_index(op.f('ix_ldm009_diagnosis_round_project_id'), table_name='ldm009_diagnosis_round')
    op.drop_table('ldm009_diagnosis_round')
    op.drop_index(op.f('ix_process_item_diagnosis_request_idempotency_key'), table_name='process_item_diagnosis_request')
    op.drop_index(op.f('ix_process_item_diagnosis_request_review_context_ref'), table_name='process_item_diagnosis_request')
    op.drop_index(op.f('ix_process_item_diagnosis_request_parse_result_ref'), table_name='process_item_diagnosis_request')
    op.drop_index(op.f('ix_process_item_diagnosis_request_parse_context_ref'), table_name='process_item_diagnosis_request')
    op.drop_index(op.f('ix_process_item_diagnosis_request_project_id'), table_name='process_item_diagnosis_request')
    op.drop_table('process_item_diagnosis_request')
