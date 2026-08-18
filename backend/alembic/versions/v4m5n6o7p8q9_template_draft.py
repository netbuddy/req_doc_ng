"""模板定制草稿表：定制器工作态暂存（可变可删，与注册表不可变快照分离，2026-07-09 增补）

Revision ID: v4m5n6o7p8q9
Revises: xj7k8l9m0n1p
Create Date: 2026-07-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'v4m5n6o7p8q9'
down_revision: Union[str, Sequence[str], None] = 'xj7k8l9m0n1p'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'template_draft',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('origin', sa.String(length=16), nullable=False, server_default='blank'),
        sa.Column('source_registry_ref', sa.Uuid(), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('template_draft')
