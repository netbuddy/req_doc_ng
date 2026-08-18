"""SCN-001 需求要素确认生命周期：LDM-005 生命周期/证据/修订稿/版本列 + 历史表 + 来源版本/补入表

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-07-02 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # LDM-005：确认生命周期承载 + 证据字段 + 修订稿 + 版本关系
    op.add_column('ldm005_requirement_element',
                  sa.Column('model_verdict', sa.String(length=48), nullable=True))
    op.add_column('ldm005_requirement_element',
                  sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('ldm005_requirement_element',
                  sa.Column('superseded', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('ldm005_requirement_element',
                  sa.Column('review_conclusion', sa.String(length=32), nullable=True))
    op.add_column('ldm005_requirement_element', sa.Column('review_basis', sa.Text(), nullable=True))
    op.add_column('ldm005_requirement_element', sa.Column('revision_draft', sa.Text(), nullable=True))
    op.add_column('ldm005_requirement_element',
                  sa.Column('updated_at', sa.DateTime(timezone=True),
                            server_default=sa.text('now()'), nullable=False))
    # 旧口径数据迁移：识别结论状态 → 确认生命周期（历史数据一律回待确认，证据入 model_verdict）
    op.execute(
        """
        UPDATE ldm005_requirement_element SET
          model_verdict = CASE process_status
            WHEN 'valid' THEN 'processable'
            WHEN 'pending' THEN 'processable'
            WHEN 'needs_supplement' THEN 'suspected_needs_supplement'
            WHEN 'excluded' THEN 'suspected_noise'
            ELSE model_verdict END,
          superseded = (process_status = 'closed'),
          process_status = CASE process_status
            WHEN 'closed' THEN 'revoked'
            ELSE 'pending_confirmation' END
        """
    )

    # LDM-002：来源版本号 + 版本快照 + 补入块
    op.add_column('ldm002_material',
                  sa.Column('source_version', sa.Integer(), nullable=False, server_default='1'))
    op.create_table('ldm002_material_revision',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('material_ref', sa.Uuid(), nullable=False),
        sa.Column('source_version', sa.Integer(), nullable=False),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ldm002_material_revision_material_ref'),
                    'ldm002_material_revision', ['material_ref'], unique=False)
    op.create_table('ldm002_material_supplement',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('material_ref', sa.Uuid(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('basis', sa.Text(), nullable=False),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ldm002_material_supplement_material_ref'),
                    'ldm002_material_supplement', ['material_ref'], unique=False)

    # LDM-005 变更历史
    op.create_table('ldm005_element_history',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('element_ref', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=48), nullable=False),
        sa.Column('from_status', sa.String(length=32), nullable=True),
        sa.Column('to_status', sa.String(length=32), nullable=True),
        sa.Column('operator_ref', sa.String(length=64), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('snapshot', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ldm005_element_history_element_ref'),
                    'ldm005_element_history', ['element_ref'], unique=False)
    op.create_index(op.f('ix_ldm005_element_history_project_id'),
                    'ldm005_element_history', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ldm005_element_history_project_id'), table_name='ldm005_element_history')
    op.drop_index(op.f('ix_ldm005_element_history_element_ref'), table_name='ldm005_element_history')
    op.drop_table('ldm005_element_history')
    op.drop_index(op.f('ix_ldm002_material_supplement_material_ref'),
                  table_name='ldm002_material_supplement')
    op.drop_table('ldm002_material_supplement')
    op.drop_index(op.f('ix_ldm002_material_revision_material_ref'),
                  table_name='ldm002_material_revision')
    op.drop_table('ldm002_material_revision')
    op.drop_column('ldm002_material', 'source_version')
    op.drop_column('ldm005_requirement_element', 'updated_at')
    op.drop_column('ldm005_requirement_element', 'revision_draft')
    op.drop_column('ldm005_requirement_element', 'review_basis')
    op.drop_column('ldm005_requirement_element', 'review_conclusion')
    op.drop_column('ldm005_requirement_element', 'superseded')
    op.drop_column('ldm005_requirement_element', 'version')
    op.drop_column('ldm005_requirement_element', 'model_verdict')
