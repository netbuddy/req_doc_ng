"""AgentRun 进度端点：poll（GET /agent-runs/{id}）+ SSE 推送（.../events）。

SSE 由 Redis Streams 事件总线驱动（真推送、按 id 重放、无 DB）；终态帧内联最终结论
（服务端读一次 DB），前端无需第三次拉取。无 REDIS_URL 时退回定时 DB 轮询降级。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.adapters.event_bus import HEARTBEAT_EVENT, TERMINAL_EVENTS
from app.api.schemas import AgentRunEventRead, AgentRunRead
from app.db.models import AgentRun, AgentRunEvent
from app.deps import (
    agent_run_event_bus,
    get_agent_run_repo,
    new_session,
    _build_async_analysis_service,
    _build_async_service,
)
from app.domain.enums import AgentRunStatus
from app.domain.errors import NotFound
from app.log import log_event
from app.repositories.agent_run import SqlAgentRunRepository

router = APIRouter(tags=["agent-runs"])

_COMPONENT = "backend-api"
_TERMINAL_STATUS = {AgentRunStatus.SUCCEEDED.value, AgentRunStatus.FAILED.value}


def _to_read(run: AgentRun, events: list[AgentRunEvent]) -> AgentRunRead:
    return AgentRunRead(
        id=str(run.id),
        kind=run.kind,
        status=run.status,
        error=run.error,
        events=[
            AgentRunEventRead(event=e.event, at=e.created_at.isoformat() if e.created_at else "")
            for e in events
        ],
    )


@router.get("/agent-runs/{run_id}", response_model=AgentRunRead)
def get_agent_run(
    run_id: str, agent_runs: SqlAgentRunRepository = Depends(get_agent_run_repo)
) -> AgentRunRead:
    run = agent_runs.get(run_id)
    if run is None:
        raise NotFound("AgentRun 不存在")
    return _to_read(run, agent_runs.events(run_id))


# ---- 终态结论读取（服务端一次 DB 读，供 SSE 终态帧内联）----

def _read_terminal_result(run_id: str) -> Optional[dict]:
    """run_id → AgentRun.(kind, context_ref) → 按 kind 分派读结论；失败返回 None（前端可退回 poll）。"""
    session = new_session()
    try:
        run = SqlAgentRunRepository(session).get(run_id)
        if run is None or run.context_ref is None:
            return None
        if run.kind == "element_recognition":
            result = _build_async_analysis_service(session).read_element_workspace(str(run.context_ref))
        elif run.kind == "item_formation":
            # context_ref = 条目化批次上下文 → 终态帧内联条目形成工作区读视图
            from app.deps import _build_async_item_formation_service

            result = _build_async_item_formation_service(session).read_item_formation_workspace(
                str(run.context_ref)
            )
        elif run.kind in ("element_review", "element_execution"):
            # context_ref = 操作请求上下文 → 定位工作区（终态帧内联最新工作区读视图）
            from app.repositories.sqlalchemy import SqlProcessRecordRepository

            op = SqlProcessRecordRepository(session).read_element_operation(str(run.context_ref))
            if op is None:
                return None
            result = _build_async_analysis_service(session).read_element_workspace(op.parse_context_ref)
        else:  # source_intake
            result = _build_async_service(session).read_intake_result(str(run.context_ref))
        return result.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 结论装配失败不致命：客户端可经 poll 端点兜底
        log_event(
            _COMPONENT, "agent.run.result.read_failed", level="WARN",
            run_id=run_id, ok=False, error_code=type(exc).__name__,
        )
        return None
    finally:
        session.close()


def _sse(payload: dict, entry_id: Optional[str] = None) -> str:
    prefix = f"id: {entry_id}\n" if entry_id else ""
    return f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---- SSE：Redis Streams 推送路径 ----

async def _stream_via_bus(run_id: str, last_id: str) -> AsyncIterator[str]:
    async with aclosing(agent_run_event_bus.subscribe(run_id, last_id)) as stream:
        async for event, entry_id in stream:
            if event == HEARTBEAT_EVENT:
                yield ": keep-alive\n\n"
                continue
            if event in TERMINAL_EVENTS:
                result = await run_in_threadpool(_read_terminal_result, run_id)
                yield _sse({"event": event, "result": result}, entry_id)
                return
            yield _sse({"event": event}, entry_id)


# ---- SSE：无 Redis 时的定时 DB 轮询降级 ----

def _poll_snapshot(run_id: str) -> tuple[bool, list[str], Optional[str]]:
    session = new_session()
    try:
        repo = SqlAgentRunRepository(session)
        run = repo.get(run_id)
        if run is None:
            return False, [], None
        return True, [e.event for e in repo.events(run_id)], run.status
    finally:
        session.close()


async def _stream_via_db_poll(run_id: str) -> AsyncIterator[str]:
    seen = 0
    for _ in range(120):  # 最多 ~60s
        exists, events, status = await run_in_threadpool(_poll_snapshot, run_id)
        if not exists:
            yield _sse({"error": "not found"})
            return
        for event in events[seen:]:
            yield _sse({"event": event})
        seen = len(events)
        if status in _TERMINAL_STATUS:
            result = await run_in_threadpool(_read_terminal_result, run_id)
            yield _sse({"status": status, "result": result})
            return
        await asyncio.sleep(0.5)


@router.get("/agent-runs/{run_id}/events")
async def agent_run_events(run_id: str, request: Request) -> StreamingResponse:
    if getattr(agent_run_event_bus, "live", False):
        last_id = request.headers.get("Last-Event-ID") or "0"
        gen = _stream_via_bus(run_id, last_id)
    else:
        gen = _stream_via_db_poll(run_id)
    return StreamingResponse(gen, media_type="text/event-stream")
