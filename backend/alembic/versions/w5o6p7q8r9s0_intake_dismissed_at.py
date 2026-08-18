"""接入请求上下文软删列：放弃本次接入（OVW-001 修订 2026-07-10，总览投影过滤、记录保留可审计）

Revision ID: w5o6p7q8r9s0
Revises: v4m5n6o7p8q9
Create Date: 2026-07-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'w5o6p7q8r9s0'
down_revision: Union[str, Sequence[str], None] = 'v4m5n6o7p8q9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'process_intake_request',
        sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('process_intake_request', 'dismissed_at')
