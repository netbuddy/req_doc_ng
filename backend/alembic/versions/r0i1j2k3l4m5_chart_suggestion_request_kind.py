"""图表建议请求类别：initial=创建初稿自动应用 / revision=修订建议待采纳（创建即初稿改造 2026-07-07）

Revision ID: r0i1j2k3l4m5
Revises: q9h0i1j2k3l4
Create Date: 2026-07-07 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'r0i1j2k3l4m5'
down_revision: Union[str, Sequence[str], None] = 'q9h0i1j2k3l4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 存量请求都是修订建议语义，server_default 直接回填
    op.add_column('process_chart_suggestion_request',
                  sa.Column('request_kind', sa.String(length=16),
                            nullable=False, server_default='revision'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('process_chart_suggestion_request', 'request_kind')
