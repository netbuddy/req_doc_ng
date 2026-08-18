"""SCN-001-P02 材料解析结果 LDM-004 + 需求要素 LDM-005 + 识别请求上下文

Revision ID: a1b2c3d4e5f6
Revises: 06642f700c48
Create Date: 2026-07-01 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# created_at 默认值用 sa.func.now()（随方言编译），与 baseline 保持一致。


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '06642f700c48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # LDM-015 增结构化结果列（识别要素集 JSON；接入判断类不使用）。
    op.add_column('ldm015_model_result', sa.Column('result_content', sa.Text(), nullable=True))

    op.create_table('process_parse_request',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('material_ref', sa.Uuid(), nullable=False),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('stop_next_action', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_process_parse_request_idempotency_key'), 'process_parse_request', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_process_parse_request_material_ref'), 'process_parse_request', ['material_ref'], unique=False)
    op.create_index(op.f('ix_process_parse_request_project_id'), 'process_parse_request', ['project_id'], unique=False)

    op.create_table('ldm004_material_parse_result',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('material_ref', sa.Uuid(), nullable=False),
    sa.Column('context_ref', sa.Uuid(), nullable=False),
    sa.Column('model_result_ref', sa.Uuid(), nullable=True),
    sa.Column('parse_status', sa.String(length=32), nullable=False),
    sa.Column('parse_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm004_material_parse_result_context_ref'), 'ldm004_material_parse_result', ['context_ref'], unique=True)
    op.create_index(op.f('ix_ldm004_material_parse_result_material_ref'), 'ldm004_material_parse_result', ['material_ref'], unique=False)
    op.create_index(op.f('ix_ldm004_material_parse_result_project_id'), 'ldm004_material_parse_result', ['project_id'], unique=False)

    op.create_table('ldm005_requirement_element',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('parse_result_ref', sa.Uuid(), nullable=False),
    sa.Column('element_type', sa.String(length=48), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source_anchor', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('process_status', sa.String(length=32), nullable=False),
    sa.Column('correction_state', sa.String(length=32), nullable=True),
    sa.Column('model_result_ref', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm005_requirement_element_parse_result_ref'), 'ldm005_requirement_element', ['parse_result_ref'], unique=False)
    op.create_index(op.f('ix_ldm005_requirement_element_project_id'), 'ldm005_requirement_element', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ldm005_requirement_element_project_id'), table_name='ldm005_requirement_element')
    op.drop_index(op.f('ix_ldm005_requirement_element_parse_result_ref'), table_name='ldm005_requirement_element')
    op.drop_table('ldm005_requirement_element')
    op.drop_index(op.f('ix_ldm004_material_parse_result_project_id'), table_name='ldm004_material_parse_result')
    op.drop_index(op.f('ix_ldm004_material_parse_result_material_ref'), table_name='ldm004_material_parse_result')
    op.drop_index(op.f('ix_ldm004_material_parse_result_context_ref'), table_name='ldm004_material_parse_result')
    op.drop_table('ldm004_material_parse_result')
    op.drop_index(op.f('ix_process_parse_request_project_id'), table_name='process_parse_request')
    op.drop_index(op.f('ix_process_parse_request_material_ref'), table_name='process_parse_request')
    op.drop_index(op.f('ix_process_parse_request_idempotency_key'), table_name='process_parse_request')
    op.drop_table('process_parse_request')
    op.drop_column('ldm015_model_result', 'result_content')
