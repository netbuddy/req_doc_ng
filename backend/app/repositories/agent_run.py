"""AgentRun 仓储（异步任务状态 + 持久化进度事件）。"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentRun, AgentRunEvent
from app.domain.enums import AgentRunStatus


def _as_uuid(ref: Optional[str]) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(ref) if ref is not None else None
    except (ValueError, AttributeError, TypeError):
        return None


class SqlAgentRunRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(self, kind: str, context_ref: Optional[str] = None) -> str:
        run = AgentRun(
            kind=kind, status=AgentRunStatus.QUEUED.value, context_ref=_as_uuid(context_ref)
        )
        self._s.add(run)
        self._s.flush()
        self._add_event(run.id, "agent_run.queued")
        return str(run.id)

    def _add_event(self, run_id: uuid.UUID, event: str) -> None:
        self._s.add(AgentRunEvent(run_id=run_id, event=event))
        self._s.flush()

    def _get(self, run_id: str) -> Optional[AgentRun]:
        return self._s.get(AgentRun, _as_uuid(run_id))

    def mark_started(self, run_id: str) -> None:
        run = self._get(run_id)
        if run is not None:
            run.status = AgentRunStatus.STARTED.value
            self._add_event(run.id, "agent_run.started")

    def mark_succeeded(self, run_id: str) -> None:
        run = self._get(run_id)
        if run is not None:
            run.status = AgentRunStatus.SUCCEEDED.value
            self._add_event(run.id, "agent_run.completed")

    def mark_failed(self, run_id: str, error: str, *, notify: bool = True) -> None:
        """把 run 标记为失败。

        notify=False 供读侧对账回收使用：对账判死的是早已死掉的历史行（可能是几周前
        排队的孤儿），它不是需要用户现在去处理的待办，逐条推「可在对应工作台重试」的
        通知只会误导用户。判死本身另有 WARN 日志与明细表留痕，事实不丢。
        实时失败（worker 抛异常等）一律走默认的 notify=True，行为不变。
        """
        run = self._get(run_id)
        if run is not None:
            run.status = AgentRunStatus.FAILED.value
            run.error = (error or "")[:1000]
            self._add_event(run.id, "agent_run.failed")
            if notify:
                # 通知徽标（04A §2.1）：失败需人工重试/降级 → 同事务落通知（只放稳定码，不带 error 原文）
                from app.services.notification import notify_agent_run_failed

                notify_agent_run_failed(self._s, str(run.id), run.kind)

    def get(self, run_id: str) -> Optional[AgentRun]:
        return self._get(run_id)

    def find_by_context(self, context_ref: str, kind: Optional[str] = None) -> Optional[AgentRun]:
        """按承载对象（context_ref）反查其最新一次 AgentRun；用于读侧对账任务真实状态。"""
        stmt = select(AgentRun).where(AgentRun.context_ref == _as_uuid(context_ref))
        if kind is not None:
            stmt = stmt.where(AgentRun.kind == kind)
        return self._s.scalars(stmt.order_by(AgentRun.created_at.desc())).first()

    def events(self, run_id: str) -> list[AgentRunEvent]:
        return list(
            self._s.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == _as_uuid(run_id))
                .order_by(AgentRunEvent.created_at, AgentRunEvent.id)
            )
        )
