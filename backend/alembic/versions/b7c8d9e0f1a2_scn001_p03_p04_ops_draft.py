"""SCN-001-P03/P04 操作请求上下文 + 变更草案 + 工作区版本 + LDM-005 留痕列

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-02 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('process_parse_request',
                  sa.Column('workspace_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('ldm005_requirement_element', sa.Column('correction_note', sa.Text(), nullable=True))
    op.add_column('ldm005_requirement_element', sa.Column('origin_refs', sa.Text(), nullable=True))

    op.create_table('process_element_operation',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('parse_context_ref', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_process_element_operation_idempotency_key'), 'process_element_operation', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_process_element_operation_parse_context_ref'), 'process_element_operation', ['parse_context_ref'], unique=False)
    op.create_index(op.f('ix_process_element_operation_project_id'), 'process_element_operation', ['project_id'], unique=False)

    op.create_table('process_element_change_draft',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('parse_context_ref', sa.Uuid(), nullable=False),
    sa.Column('workspace_version', sa.Integer(), nullable=False),
    sa.Column('operation_type', sa.String(length=32), nullable=False),
    sa.Column('origin', sa.String(length=24), nullable=False),
    sa.Column('items', sa.Text(), nullable=False),
    sa.Column('target_refs', sa.Text(), nullable=True),
    sa.Column('suggestion_refs', sa.Text(), nullable=True),
    sa.Column('source_ranges', sa.Text(), nullable=True),
    sa.Column('impact_summary', sa.Text(), nullable=True),
    sa.Column('create_gate', sa.String(length=40), nullable=False),
    sa.Column('next_action', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_process_element_change_draft_parse_context_ref'), 'process_element_change_draft', ['parse_context_ref'], unique=False)
    op.create_index(op.f('ix_process_element_change_draft_project_id'), 'process_element_change_draft', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_process_element_change_draft_project_id'), table_name='process_element_change_draft')
    op.drop_index(op.f('ix_process_element_change_draft_parse_context_ref'), table_name='process_element_change_draft')
    op.drop_table('process_element_change_draft')
    op.drop_index(op.f('ix_process_element_operation_project_id'), table_name='process_element_operation')
    op.drop_index(op.f('ix_process_element_operation_parse_context_ref'), table_name='process_element_operation')
    op.drop_index(op.f('ix_process_element_operation_idempotency_key'), table_name='process_element_operation')
    op.drop_table('process_element_operation')
    op.drop_column('ldm005_requirement_element', 'origin_refs')
    op.drop_column('ldm005_requirement_element', 'correction_note')
    op.drop_column('process_parse_request', 'workspace_version')
