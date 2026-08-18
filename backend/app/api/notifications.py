"""通知端点（04A §2.1 通知徽标）：列表 + 未读数 + 标记已读。

通知只承接"需要人处理或确认"的事项；生产点在失败/门禁代码位（notify_safely），
本层只读写通知表本身。v0.1 单用户，未做权限过滤（边界见 04A §11）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.schemas import NotificationActionResult, NotificationListRead, NotificationRead
from app.db.models import Notification
from app.deps import get_notification_repo
from app.domain.errors import NotFound
from app.log import log_event
from app.repositories.notification import SqlNotificationRepository

router = APIRouter(tags=["notifications"])

_COMPONENT = "backend-api"


def _to_read(record: Notification) -> NotificationRead:
    return NotificationRead(
        id=str(record.id),
        kind=record.kind,
        title=record.title,
        summary=record.summary,
        project_ref=str(record.project_ref) if record.project_ref else None,
        ref=str(record.ref) if record.ref else None,
        occurrences=record.occurrences,
        read=record.read_at is not None,
        created_at=record.created_at.isoformat() if record.created_at else "",
        updated_at=record.updated_at.isoformat() if record.updated_at else "",
    )


@router.get("/notifications", response_model=NotificationListRead)
def list_notifications(
    unread: bool = False,
    limit: int = 50,
    repo: SqlNotificationRepository = Depends(get_notification_repo),
) -> NotificationListRead:
    items = repo.list(unread_only=unread, limit=min(max(limit, 1), 200))
    return NotificationListRead(
        notifications=[_to_read(n) for n in items],
        unread_count=repo.unread_count(),
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationActionResult)
def mark_notification_read(
    notification_id: str,
    repo: SqlNotificationRepository = Depends(get_notification_repo),
) -> NotificationActionResult:
    record = repo.get(notification_id)
    if record is None:
        raise NotFound("通知不存在")
    already_read = record.read_at is not None
    repo.mark_read(notification_id)
    repo.commit()
    log_event(
        _COMPONENT, "notification.read", ok=True,
        notification_id=notification_id, already_read=already_read,
    )
    return NotificationActionResult(
        status="already_read" if already_read else "marked_read",
        unread_count=repo.unread_count(),
    )


@router.post("/notifications/read-all", response_model=NotificationActionResult)
def mark_all_notifications_read(
    repo: SqlNotificationRepository = Depends(get_notification_repo),
) -> NotificationActionResult:
    marked = repo.mark_all_read()
    repo.commit()
    log_event(_COMPONENT, "notification.read_all", ok=True, marked=marked)
    return NotificationActionResult(status="all_read", unread_count=repo.unread_count())
