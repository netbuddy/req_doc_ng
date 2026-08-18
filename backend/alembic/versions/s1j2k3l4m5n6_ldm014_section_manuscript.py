"""LDM-014 章节撰稿表（AEP-098）：人工撰写内容成为文档正文第一类来源（2026-07-06 增补）

Revision ID: s1j2k3l4m5n6
Revises: r0i1j2k3l4m5
Create Date: 2026-07-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 's1j2k3l4m5n6'
down_revision: Union[str, Sequence[str], None] = 'r0i1j2k3l4m5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ldm014_section_manuscript',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('document_ref', sa.Uuid(), nullable=False),
        sa.Column('section_key', sa.String(length=64), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('revision_no', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_by', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ldm014_section_manuscript_document_ref'),
                    'ldm014_section_manuscript', ['document_ref'], unique=False)
    op.create_index('ux_ldm014_manuscript_doc_section',
                    'ldm014_section_manuscript', ['document_ref', 'section_key'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ux_ldm014_manuscript_doc_section', table_name='ldm014_section_manuscript')
    op.drop_index(op.f('ix_ldm014_section_manuscript_document_ref'),
                  table_name='ldm014_section_manuscript')
    op.drop_table('ldm014_section_manuscript')
