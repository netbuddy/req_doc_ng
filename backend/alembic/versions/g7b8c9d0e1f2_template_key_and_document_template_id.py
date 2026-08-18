"""Rename template key and collapse document template references

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('uq_template_registry_id_version', 'template_registry', type_='unique')
    op.drop_index(op.f('ix_template_registry_template_id'), table_name='template_registry')
    op.alter_column('template_registry', 'template_id', new_column_name='template_key')
    op.create_index(op.f('ix_template_registry_template_key'), 'template_registry', ['template_key'], unique=False)
    op.create_unique_constraint('uq_template_registry_key_version', 'template_registry', ['template_key', 'version_no'])

    op.add_column('ldm014_requirement_document', sa.Column('template_id', sa.Uuid(), nullable=True))
    op.add_column('ldm014_release_baseline', sa.Column('template_id', sa.Uuid(), nullable=True))

    op.execute("""
        UPDATE ldm014_requirement_document
        SET template_id = COALESCE(
            template_registry_ref,
            (
                SELECT tr.id
                FROM template_registry tr
                WHERE tr.template_key = ldm014_requirement_document.template_ref
                  AND tr.status = 'active'
                ORDER BY tr.version_no DESC
                LIMIT 1
            )
        )
    """)
    op.execute("""
        UPDATE ldm014_release_baseline
        SET template_id = COALESCE(
            template_registry_ref,
            (
                SELECT tr.id
                FROM template_registry tr
                WHERE tr.template_key = ldm014_release_baseline.template_ref
                  AND tr.status = 'active'
                ORDER BY tr.version_no DESC
                LIMIT 1
            )
        )
    """)

    op.drop_column('ldm014_requirement_document', 'template_registry_ref')
    op.drop_column('ldm014_requirement_document', 'template_schema_version')
    op.drop_column('ldm014_requirement_document', 'template_ref')
    op.drop_column('ldm014_release_baseline', 'template_registry_ref')
    op.drop_column('ldm014_release_baseline', 'template_schema_version')
    op.drop_column('ldm014_release_baseline', 'template_ref')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('ldm014_release_baseline', sa.Column('template_ref', sa.String(length=128), nullable=True))
    op.add_column('ldm014_release_baseline', sa.Column('template_schema_version', sa.String(length=16), nullable=True))
    op.add_column('ldm014_release_baseline', sa.Column('template_registry_ref', sa.Uuid(), nullable=True))
    op.add_column('ldm014_requirement_document', sa.Column('template_ref', sa.String(length=128), nullable=True))
    op.add_column('ldm014_requirement_document', sa.Column('template_schema_version', sa.String(length=16), nullable=True))
    op.add_column('ldm014_requirement_document', sa.Column('template_registry_ref', sa.Uuid(), nullable=True))

    op.execute("""
        UPDATE ldm014_requirement_document
        SET template_ref = tr.template_key,
            template_schema_version = tr.schema_version,
            template_registry_ref = ldm014_requirement_document.template_id
        FROM template_registry tr
        WHERE tr.id = ldm014_requirement_document.template_id
    """)
    op.execute("""
        UPDATE ldm014_release_baseline
        SET template_ref = tr.template_key,
            template_schema_version = tr.schema_version,
            template_registry_ref = ldm014_release_baseline.template_id
        FROM template_registry tr
        WHERE tr.id = ldm014_release_baseline.template_id
    """)

    op.drop_column('ldm014_release_baseline', 'template_id')
    op.drop_column('ldm014_requirement_document', 'template_id')
    op.drop_constraint('uq_template_registry_key_version', 'template_registry', type_='unique')
    op.drop_index(op.f('ix_template_registry_template_key'), table_name='template_registry')
    op.alter_column('template_registry', 'template_key', new_column_name='template_id')
    op.create_index(op.f('ix_template_registry_template_id'), 'template_registry', ['template_id'], unique=False)
    op.create_unique_constraint('uq_template_registry_id_version', 'template_registry', ['template_id', 'version_no'])
