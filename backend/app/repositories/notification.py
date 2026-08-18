"""通知仓储（04A §2.1 通知徽标：需人处理的未读事项，dedup_key 去重）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Notification


def _as_uuid(ref: Optional[str]) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(ref) if ref is not None else None
    except (ValueError, AttributeError, TypeError):
        return None


class SqlNotificationRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def notify(
        self,
        *,
        kind: str,
        dedup_key: str,
        title: str,
        summary: str = "",
        project_ref: Optional[str] = None,
        ref: Optional[str] = None,
    ) -> str:
        """insert-or-touch：同 dedup_key 复发 → occurrences+1 且清 read_at（需再次处理），
        不新增条目（徽标计数按事项去重，不按发生次数累加）。"""
        existing = self._s.scalars(
            select(Notification).where(Notification.dedup_key == dedup_key)
        ).first()
        if existing is not None:
            existing.occurrences += 1
            existing.read_at = None
            existing.title = title
            existing.summary = summary
            existing.updated_at = datetime.now(timezone.utc)
            self._s.flush()
            return str(existing.id)
        record = Notification(
            kind=kind,
            dedup_key=dedup_key,
            title=title,
            summary=summary,
            project_ref=_as_uuid(project_ref),
            ref=_as_uuid(ref),
        )
        self._s.add(record)
        self._s.flush()
        return str(record.id)

    def get(self, notification_id: str) -> Optional[Notification]:
        return self._s.get(Notification, _as_uuid(notification_id))

    def list(self, unread_only: bool = False, limit: int = 50) -> list[Notification]:
        stmt = select(Notification).order_by(Notification.updated_at.desc(), Notification.id).limit(limit)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        return list(self._s.scalars(stmt))

    def unread_count(self) -> int:
        return int(
            self._s.scalar(
                select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
            )
            or 0
        )

    def mark_read(self, notification_id: str) -> Optional[Notification]:
        record = self.get(notification_id)
        if record is None:
            return None
        if record.read_at is None:
            record.read_at = datetime.now(timezone.utc)
            self._s.flush()
        return record

    def mark_all_read(self) -> int:
        unread = self.list(unread_only=True, limit=1000)
        now = datetime.now(timezone.utc)
        for record in unread:
            record.read_at = now
        self._s.flush()
        return len(unread)

    def commit(self) -> None:
        self._s.commit()
