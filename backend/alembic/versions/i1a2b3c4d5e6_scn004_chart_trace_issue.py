"""SCN-004 受控图表确认与追溯关系成立：LDM-012 图表 + LDM-013 追溯 + LDM-011 问题项 + 核对过程记录

Revision ID: i1a2b3c4d5e6
Revises: h9c0d1e2f3a4
Create Date: 2026-07-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'i1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'h9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ldm012_requirement_chart',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('chart_kind', sa.String(length=24), nullable=False),
        sa.Column('chart_type', sa.String(length=32), nullable=False),
        sa.Column('format', sa.String(length=24), nullable=False),
        sa.Column('source_code', sa.Text(), nullable=False),
        sa.Column('draft_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('status_reason', sa.Text(), nullable=True),
        sa.Column('source_kind', sa.String(length=32), nullable=False),
        sa.Column('source_refs', sa.Text(), nullable=False),
        sa.Column('creation_basis', sa.Text(), nullable=False),
        sa.Column('verification_conclusion', sa.Text(), nullable=True),
        sa.Column('confirm_basis', sa.Text(), nullable=True),
        sa.Column('confirmed_by', sa.String(length=64), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ldm012_requirement_chart_project_id'), 'ldm012_requirement_chart', ['project_id'], unique=False)

    op.create_table(
        'ldm012_chart_revision',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('chart_ref', sa.Uuid(), nullable=False),
        sa.Column('draft_version', sa.Integer(), nullable=False),
        sa.Column('source_code', sa.Text(), nullable=False),
        sa.Column('format', sa.String(length=24), nullable=False),
        sa.Column('change_origin', sa.String(length=24), nullable=False),
        sa.Column('suggestion_ref', sa.Uuid(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index(op.f('ix_ldm012_chart_revision_chart_ref'), 'ldm012_chart_revision', ['chart_ref'], unique=False)

    op.create_table(
        'ldm013_trace_link',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('dimension', sa.String(length=16), nullable=False),
        sa.Column('relation_type', sa.String(length=32), nullable=False),
        sa.Column('upstream_type', sa.String(length=32), nullable=False),
        sa.Column('upstream_ref', sa.Uuid(), nullable=False),
        sa.Column('downstream_type', sa.String(length=32), nullable=False),
        sa.Column('downstream_ref', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('initial_basis', sa.Text(), nullable=False),
        sa.Column('status_reason', sa.Text(), nullable=True),
        sa.Column('established_basis', sa.Text(), nullable=True),
        sa.Column('established_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('issue_ref', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('upstream_type', 'upstream_ref', 'downstream_type', 'downstream_ref', 'relation_type', name='uq_ldm013_edge'),
    )
    op.create_index(op.f('ix_ldm013_trace_link_project_id'), 'ldm013_trace_link', ['project_id'], unique=False)
    op.create_index(op.f('ix_ldm013_trace_link_upstream_ref'), 'ldm013_trace_link', ['upstream_ref'], unique=False)
    op.create_index(op.f('ix_ldm013_trace_link_downstream_ref'), 'ldm013_trace_link', ['downstream_ref'], unique=False)

    op.create_table(
        'ldm011_issue',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('issue_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('origin_kind', sa.String(length=32), nullable=False),
        sa.Column('chart_ref', sa.Uuid(), nullable=True),
        sa.Column('finding_ref', sa.Uuid(), nullable=True),
        sa.Column('trace_link_refs', sa.Text(), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ldm011_issue_project_id'), 'ldm011_issue', ['project_id'], unique=False)
    op.create_index(op.f('ix_ldm011_issue_chart_ref'), 'ldm011_issue', ['chart_ref'], unique=False)
    op.create_index(op.f('ix_ldm011_issue_idempotency_key'), 'ldm011_issue', ['idempotency_key'], unique=True)

    op.create_table(
        'process_chart_suggestion_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('chart_ref', sa.Uuid(), nullable=False),
        sa.Column('base_draft_version', sa.Integer(), nullable=False),
        sa.Column('intent', sa.Text(), nullable=False),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('stop_next_action', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_process_chart_suggestion_request_project_id'), 'process_chart_suggestion_request', ['project_id'], unique=False)
    op.create_index(op.f('ix_process_chart_suggestion_request_chart_ref'), 'process_chart_suggestion_request', ['chart_ref'], unique=False)
    op.create_index(op.f('ix_process_chart_suggestion_request_idempotency_key'), 'process_chart_suggestion_request', ['idempotency_key'], unique=True)

    op.create_table(
        'process_chart_verification_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('chart_ref', sa.Uuid(), nullable=False),
        sa.Column('chart_draft_version', sa.Integer(), nullable=False),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('stop_next_action', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_process_chart_verification_request_project_id'), 'process_chart_verification_request', ['project_id'], unique=False)
    op.create_index(op.f('ix_process_chart_verification_request_chart_ref'), 'process_chart_verification_request', ['chart_ref'], unique=False)
    op.create_index(op.f('ix_process_chart_verification_request_idempotency_key'), 'process_chart_verification_request', ['idempotency_key'], unique=True)

    op.create_table(
        'process_chart_verification_round',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('chart_ref', sa.Uuid(), nullable=False),
        sa.Column('request_ref', sa.Uuid(), nullable=False),
        sa.Column('round_no', sa.Integer(), nullable=False),
        sa.Column('chart_draft_version', sa.Integer(), nullable=False),
        sa.Column('processing_status', sa.String(length=24), nullable=False),
        sa.Column('model_result_ref', sa.Uuid(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('invalidated', sa.Boolean(), nullable=False),
        sa.Column('invalidated_reason', sa.Text(), nullable=True),
        sa.Column('confirm_result', sa.String(length=24), nullable=True),
        sa.Column('confirm_idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('confirm_idempotency_key'),
    )
    op.create_index(op.f('ix_process_chart_verification_round_chart_ref'), 'process_chart_verification_round', ['chart_ref'], unique=False)
    op.create_index(op.f('ix_process_chart_verification_round_request_ref'), 'process_chart_verification_round', ['request_ref'], unique=False)

    op.create_table(
        'process_chart_verification_finding',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('round_ref', sa.Uuid(), nullable=False),
        sa.Column('chart_ref', sa.Uuid(), nullable=False),
        sa.Column('finding_type', sa.String(length=40), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('basis_summary', sa.Text(), nullable=False),
        sa.Column('related_source_refs', sa.Text(), nullable=False),
        sa.Column('model_result_ref', sa.Uuid(), nullable=True),
        sa.Column('decision', sa.String(length=16), nullable=True),
        sa.Column('decision_reason', sa.Text(), nullable=True),
        sa.Column('decision_operator', sa.String(length=64), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_idempotency_key', sa.String(length=128), nullable=True),
        sa.Column('issue_ref', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('decision_idempotency_key'),
    )
    op.create_index(op.f('ix_process_chart_verification_finding_round_ref'), 'process_chart_verification_finding', ['round_ref'], unique=False)
    op.create_index(op.f('ix_process_chart_verification_finding_chart_ref'), 'process_chart_verification_finding', ['chart_ref'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_process_chart_verification_finding_chart_ref'), table_name='process_chart_verification_finding')
    op.drop_index(op.f('ix_process_chart_verification_finding_round_ref'), table_name='process_chart_verification_finding')
    op.drop_table('process_chart_verification_finding')
    op.drop_index(op.f('ix_process_chart_verification_round_request_ref'), table_name='process_chart_verification_round')
    op.drop_index(op.f('ix_process_chart_verification_round_chart_ref'), table_name='process_chart_verification_round')
    op.drop_table('process_chart_verification_round')
    op.drop_index(op.f('ix_process_chart_verification_request_idempotency_key'), table_name='process_chart_verification_request')
    op.drop_index(op.f('ix_process_chart_verification_request_chart_ref'), table_name='process_chart_verification_request')
    op.drop_index(op.f('ix_process_chart_verification_request_project_id'), table_name='process_chart_verification_request')
    op.drop_table('process_chart_verification_request')
    op.drop_index(op.f('ix_process_chart_suggestion_request_idempotency_key'), table_name='process_chart_suggestion_request')
    op.drop_index(op.f('ix_process_chart_suggestion_request_chart_ref'), table_name='process_chart_suggestion_request')
    op.drop_index(op.f('ix_process_chart_suggestion_request_project_id'), table_name='process_chart_suggestion_request')
    op.drop_table('process_chart_suggestion_request')
    op.drop_index(op.f('ix_ldm011_issue_idempotency_key'), table_name='ldm011_issue')
    op.drop_index(op.f('ix_ldm011_issue_chart_ref'), table_name='ldm011_issue')
    op.drop_index(op.f('ix_ldm011_issue_project_id'), table_name='ldm011_issue')
    op.drop_table('ldm011_issue')
    op.drop_index(op.f('ix_ldm013_trace_link_downstream_ref'), table_name='ldm013_trace_link')
    op.drop_index(op.f('ix_ldm013_trace_link_upstream_ref'), table_name='ldm013_trace_link')
    op.drop_index(op.f('ix_ldm013_trace_link_project_id'), table_name='ldm013_trace_link')
    op.drop_table('ldm013_trace_link')
    op.drop_index(op.f('ix_ldm012_chart_revision_chart_ref'), table_name='ldm012_chart_revision')
    op.drop_table('ldm012_chart_revision')
    op.drop_index(op.f('ix_ldm012_requirement_chart_project_id'), table_name='ldm012_requirement_chart')
    op.drop_table('ldm012_requirement_chart')
