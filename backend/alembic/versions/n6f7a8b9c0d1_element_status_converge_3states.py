"""LDM-005 状态收敛为 3 态：存量 analyzing/revising → pending_confirmation。

绑定 docs/40 domains/DS-001/state-machines/需求要素.md（2026-07-05 收敛记录）：
「分析中」「修订中」降级为工作区会话事实（复核结论 / 未采纳修订稿字段保留，不动），
生命周期状态只剩 待确认 / 已确认 / 已撤销。

Revision ID: n6f7a8b9c0d1
Revises: m5e6f7a8b9c0
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'n6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'm5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE ldm005_requirement_element "
        "SET process_status = 'pending_confirmation' "
        "WHERE process_status IN ('analyzing', 'revising')"
    )


def downgrade() -> None:
    # 收敛不可逆：在途态语义已由会话事实承载，无法从数据反推原状态。
    pass
