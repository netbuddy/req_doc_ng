"""SCN-002-P01 LDM-007 需求条目 + 字段修订记录 + 条目化批次过程记录

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ldm007_requirement_item',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('parse_result_ref', sa.Uuid(), nullable=False),
    sa.Column('formation_context_ref', sa.Uuid(), nullable=False),
    sa.Column('req_no', sa.String(length=32), nullable=False),
    sa.Column('expression', sa.Text(), nullable=False),
    sa.Column('req_type', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('source_element_refs', sa.Text(), nullable=False),
    sa.Column('formation_basis_ref', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm007_requirement_item_project_id'), 'ldm007_requirement_item', ['project_id'], unique=False)
    op.create_index(op.f('ix_ldm007_requirement_item_parse_result_ref'), 'ldm007_requirement_item', ['parse_result_ref'], unique=False)
    op.create_index(op.f('ix_ldm007_requirement_item_formation_context_ref'), 'ldm007_requirement_item', ['formation_context_ref'], unique=False)

    op.create_table('ldm007_item_revision',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('item_ref', sa.Uuid(), nullable=False),
    sa.Column('field_key', sa.String(length=32), nullable=False),
    sa.Column('before_value', sa.Text(), nullable=False),
    sa.Column('after_value', sa.Text(), nullable=False),
    sa.Column('revision_mode', sa.String(length=40), nullable=False),
    sa.Column('suggestion_ref', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm007_item_revision_item_ref'), 'ldm007_item_revision', ['item_ref'], unique=False)
    op.create_index(op.f('ix_ldm007_item_revision_idempotency_key'), 'ldm007_item_revision', ['idempotency_key'], unique=True)

    op.create_table('process_item_formation_request',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('parse_context_ref', sa.Uuid(), nullable=False),
    sa.Column('parse_result_ref', sa.Uuid(), nullable=False),
    sa.Column('scope_type', sa.String(length=32), nullable=False),
    sa.Column('target_refs', sa.Text(), nullable=True),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('stop_next_action', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_process_item_formation_request_project_id'), 'process_item_formation_request', ['project_id'], unique=False)
    op.create_index(op.f('ix_process_item_formation_request_parse_context_ref'), 'process_item_formation_request', ['parse_context_ref'], unique=False)
    op.create_index(op.f('ix_process_item_formation_request_parse_result_ref'), 'process_item_formation_request', ['parse_result_ref'], unique=False)
    op.create_index(op.f('ix_process_item_formation_request_idempotency_key'), 'process_item_formation_request', ['idempotency_key'], unique=True)

    op.create_table('process_itemization_outcome',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('formation_context_ref', sa.Uuid(), nullable=False),
    sa.Column('element_ref', sa.Uuid(), nullable=False),
    sa.Column('result_status', sa.String(length=16), nullable=False),
    sa.Column('item_ref', sa.Uuid(), nullable=True),
    sa.Column('formation_basis_ref', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('next_action', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_process_itemization_outcome_formation_context_ref'), 'process_itemization_outcome', ['formation_context_ref'], unique=False)
    op.create_index(op.f('ix_process_itemization_outcome_element_ref'), 'process_itemization_outcome', ['element_ref'], unique=False)

    op.create_table('process_item_revision_suggestion',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('item_ref', sa.Uuid(), nullable=False),
    sa.Column('field_key', sa.String(length=32), nullable=False),
    sa.Column('proposed_value', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('model_result_ref', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_process_item_revision_suggestion_item_ref'), 'process_item_revision_suggestion', ['item_ref'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_process_item_revision_suggestion_item_ref'), table_name='process_item_revision_suggestion')
    op.drop_table('process_item_revision_suggestion')
    op.drop_index(op.f('ix_process_itemization_outcome_element_ref'), table_name='process_itemization_outcome')
    op.drop_index(op.f('ix_process_itemization_outcome_formation_context_ref'), table_name='process_itemization_outcome')
    op.drop_table('process_itemization_outcome')
    op.drop_index(op.f('ix_process_item_formation_request_idempotency_key'), table_name='process_item_formation_request')
    op.drop_index(op.f('ix_process_item_formation_request_parse_result_ref'), table_name='process_item_formation_request')
    op.drop_index(op.f('ix_process_item_formation_request_parse_context_ref'), table_name='process_item_formation_request')
    op.drop_index(op.f('ix_process_item_formation_request_project_id'), table_name='process_item_formation_request')
    op.drop_table('process_item_formation_request')
    op.drop_index(op.f('ix_ldm007_item_revision_idempotency_key'), table_name='ldm007_item_revision')
    op.drop_index(op.f('ix_ldm007_item_revision_item_ref'), table_name='ldm007_item_revision')
    op.drop_table('ldm007_item_revision')
    op.drop_index(op.f('ix_ldm007_requirement_item_formation_context_ref'), table_name='ldm007_requirement_item')
    op.drop_index(op.f('ix_ldm007_requirement_item_parse_result_ref'), table_name='ldm007_requirement_item')
    op.drop_index(op.f('ix_ldm007_requirement_item_project_id'), table_name='ldm007_requirement_item')
    op.drop_table('ldm007_requirement_item')
