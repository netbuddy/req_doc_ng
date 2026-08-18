"""模板注册表（登记快照，不可变）+ LDM-014/基线冻结注册行引用

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-03 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('template_registry',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('template_id', sa.String(length=128), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('doc_type', sa.String(length=32), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('registered_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_template_registry_template_id'), 'template_registry', ['template_id'], unique=False)
    op.create_index(op.f('ix_template_registry_content_hash'), 'template_registry', ['content_hash'], unique=True)
    op.create_unique_constraint('uq_template_registry_id_version', 'template_registry', ['template_id', 'version_no'])

    op.add_column('ldm014_requirement_document', sa.Column('template_registry_ref', sa.Uuid(), nullable=True))
    op.add_column('ldm014_release_baseline', sa.Column('template_registry_ref', sa.Uuid(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ldm014_release_baseline', 'template_registry_ref')
    op.drop_column('ldm014_requirement_document', 'template_registry_ref')
    op.drop_constraint('uq_template_registry_id_version', 'template_registry', type_='unique')
    op.drop_index(op.f('ix_template_registry_content_hash'), table_name='template_registry')
    op.drop_index(op.f('ix_template_registry_template_id'), table_name='template_registry')
    op.drop_table('template_registry')
