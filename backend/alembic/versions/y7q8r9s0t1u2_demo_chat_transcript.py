"""演示留痕表 demo_chat_transcript（AI 对话演示简化方案 2026-07-18）

Revision ID: y7q8r9s0t1u2
Revises: x6p7q8r9s0t1
Create Date: 2026-07-18 00:00:00.000000

设计事实源：docs/proposals/unified-chat-widget/AI对话演示简化方案-2026-07-18.md §2.1。
- 新建 append-only 留痕表：三个对话页区5 消息的服务端留痕，供刷新后水合（现状刷新即失）。
- project_ref / context_ref 两索引，名与 ORM create_all（index=True）产出同名，避免 schema 漂移
  （沿用 x6p7q8r9s0t1 的同名索引纪律）。
- 纯新增：不触碰任何既有表；downgrade 整表删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'y7q8r9s0t1u2'
down_revision: Union[str, Sequence[str], None] = 'x6p7q8r9s0t1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建表 + 两索引（project_ref / context_ref）。"""
    op.create_table(
        'demo_chat_transcript',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_ref', sa.Uuid(), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('context_ref', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_demo_chat_transcript_project_ref', 'demo_chat_transcript', ['project_ref'],
    )
    op.create_index(
        'ix_demo_chat_transcript_context_ref', 'demo_chat_transcript', ['context_ref'],
    )


def downgrade() -> None:
    """删索引 + 整表（纯新增，可逆）。"""
    op.drop_index('ix_demo_chat_transcript_context_ref', table_name='demo_chat_transcript')
    op.drop_index('ix_demo_chat_transcript_project_ref', table_name='demo_chat_transcript')
    op.drop_table('demo_chat_transcript')
