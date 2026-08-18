"""notification（04A §2.1 通知徽标：需人处理的未读事项，dedup_key 去重）

Revision ID: j2b3c4d5e6f7
Revises: i1a2b3c4d5e6
Create Date: 2026-07-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'j2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'i1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('notification',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('project_ref', sa.Uuid(), nullable=True),
    sa.Column('ref', sa.Uuid(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('dedup_key', sa.String(length=200), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notification_project_ref'), 'notification', ['project_ref'], unique=False)
    op.create_index(op.f('ix_notification_dedup_key'), 'notification', ['dedup_key'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_notification_dedup_key'), table_name='notification')
    op.drop_index(op.f('ix_notification_project_ref'), table_name='notification')
    op.drop_table('notification')
