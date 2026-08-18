"""config_entry + config_audit（04 §3.5 配置管理入口：支撑能力配置 + 保存审计留痕）

Revision ID: l4d5e6f7a8b9
Revises: k3c4d5e6f7a8
Create Date: 2026-07-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'l4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'k3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('config_entry',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('domain', sa.String(length=32), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('secrets', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_config_entry_domain'), 'config_entry', ['domain'], unique=True)
    op.create_table('config_audit',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('domain', sa.String(length=32), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('operator_ref', sa.String(length=64), nullable=False),
    sa.Column('changed_keys', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_config_audit_domain'), 'config_audit', ['domain'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_config_audit_domain'), table_name='config_audit')
    op.drop_table('config_audit')
    op.drop_index(op.f('ix_config_entry_domain'), table_name='config_entry')
    op.drop_table('config_entry')
