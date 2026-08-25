"""通知徽标（需人处理的未读事项）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel


# ---- 通知徽标（04A §2.1:需人处理的未读事项,按 dedup_key 去重）----


class NotificationRead(BaseModel):
    """单条通知读视图。occurrences=同一事项复发次数(不影响徽标计数)。"""

    id: str
    kind: str
    title: str
    summary: str
    project_ref: str | None = None
    ref: str | None = None
    occurrences: int
    read: bool
    created_at: str
    updated_at: str


class NotificationListRead(BaseModel):
    """GET /api/notifications。unread_count=徽标计数(未读事项数,按事项去重)。"""

    notifications: list[NotificationRead]
    unread_count: int


class NotificationActionResult(BaseModel):
    """标记已读结果。status:marked_read/already_read/all_read。"""

    status: str
    unread_count: int
