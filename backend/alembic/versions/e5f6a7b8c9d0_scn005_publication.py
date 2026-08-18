"""SCN-005 LDM-014 需求文档 + 索引条目 + Markdown 稿 + 补丁 + docx 导出件 + 发布基线

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ldm014_requirement_document',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('doc_type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('template_ref', sa.String(length=128), nullable=False),
    sa.Column('template_schema_version', sa.String(length=16), nullable=False),
    sa.Column('coverage_scope', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('blocked_reason', sa.Text(), nullable=True),
    sa.Column('missing_list', sa.Text(), nullable=True),
    sa.Column('index_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm014_requirement_document_project_id'), 'ldm014_requirement_document', ['project_id'], unique=False)

    op.create_table('ldm014_doc_index_entry',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('document_ref', sa.Uuid(), nullable=False),
    sa.Column('index_version', sa.Integer(), nullable=False),
    sa.Column('section_key', sa.String(length=64), nullable=False),
    sa.Column('asset_type', sa.String(length=32), nullable=False),
    sa.Column('asset_ref', sa.Uuid(), nullable=True),
    sa.Column('asset_version', sa.String(length=16), nullable=False),
    sa.Column('order_no', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm014_doc_index_entry_document_ref'), 'ldm014_doc_index_entry', ['document_ref'], unique=False)
    op.create_index(op.f('ix_ldm014_doc_index_entry_index_version'), 'ldm014_doc_index_entry', ['index_version'], unique=False)

    op.create_table('ldm014_markdown_draft',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('document_ref', sa.Uuid(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('index_version', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('generated_content', sa.Text(), nullable=False),
    sa.Column('source_bindings', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('can_export', sa.Boolean(), nullable=False),
    sa.Column('block_reasons', sa.Text(), nullable=True),
    sa.Column('finalized_by', sa.String(length=64), nullable=True),
    sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm014_markdown_draft_document_ref'), 'ldm014_markdown_draft', ['document_ref'], unique=False)

    op.create_table('ldm014_markdown_patch',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('draft_ref', sa.Uuid(), nullable=False),
    sa.Column('impact', sa.String(length=32), nullable=False),
    sa.Column('before_text', sa.Text(), nullable=False),
    sa.Column('after_text', sa.Text(), nullable=False),
    sa.Column('bound_item_ref', sa.Uuid(), nullable=True),
    sa.Column('reflow_item_ref', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm014_markdown_patch_draft_ref'), 'ldm014_markdown_patch', ['draft_ref'], unique=False)

    op.create_table('ldm014_docx_export',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('document_ref', sa.Uuid(), nullable=False),
    sa.Column('draft_ref', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('file_path', sa.Text(), nullable=True),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('manual_fallback', sa.Boolean(), nullable=False),
    sa.Column('check_note', sa.Text(), nullable=True),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm014_docx_export_document_ref'), 'ldm014_docx_export', ['document_ref'], unique=False)
    op.create_index(op.f('ix_ldm014_docx_export_draft_ref'), 'ldm014_docx_export', ['draft_ref'], unique=False)
    op.create_index(op.f('ix_ldm014_docx_export_idempotency_key'), 'ldm014_docx_export', ['idempotency_key'], unique=True)

    op.create_table('ldm014_release_baseline',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('document_ref', sa.Uuid(), nullable=False),
    sa.Column('index_version', sa.Integer(), nullable=False),
    sa.Column('draft_ref', sa.Uuid(), nullable=False),
    sa.Column('template_ref', sa.String(length=128), nullable=False),
    sa.Column('template_schema_version', sa.String(length=16), nullable=False),
    sa.Column('export_ref', sa.Uuid(), nullable=False),
    sa.Column('manual_fallback', sa.Boolean(), nullable=False),
    sa.Column('asset_refs', sa.Text(), nullable=False),
    sa.Column('confirmed_by', sa.String(length=64), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ldm014_release_baseline_document_ref'), 'ldm014_release_baseline', ['document_ref'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ldm014_release_baseline_document_ref'), table_name='ldm014_release_baseline')
    op.drop_table('ldm014_release_baseline')
    op.drop_index(op.f('ix_ldm014_docx_export_idempotency_key'), table_name='ldm014_docx_export')
    op.drop_index(op.f('ix_ldm014_docx_export_draft_ref'), table_name='ldm014_docx_export')
    op.drop_index(op.f('ix_ldm014_docx_export_document_ref'), table_name='ldm014_docx_export')
    op.drop_table('ldm014_docx_export')
    op.drop_index(op.f('ix_ldm014_markdown_patch_draft_ref'), table_name='ldm014_markdown_patch')
    op.drop_table('ldm014_markdown_patch')
    op.drop_index(op.f('ix_ldm014_markdown_draft_document_ref'), table_name='ldm014_markdown_draft')
    op.drop_table('ldm014_markdown_draft')
    op.drop_index(op.f('ix_ldm014_doc_index_entry_index_version'), table_name='ldm014_doc_index_entry')
    op.drop_index(op.f('ix_ldm014_doc_index_entry_document_ref'), table_name='ldm014_doc_index_entry')
    op.drop_table('ldm014_doc_index_entry')
    op.drop_index(op.f('ix_ldm014_requirement_document_project_id'), table_name='ldm014_requirement_document')
    op.drop_table('ldm014_requirement_document')
