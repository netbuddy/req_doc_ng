"""对话端点 SSE 流式回执（链路回执条数据源，AEP-095/096 流式变体）。

契约（SCN-001-P02 前端交互与接口 §5.1）：
- `event: stage` 帧：{"stage": <AiRequestStage 稳定码>, "at": <ISO8601>}，按处理进度逐帧推送；
- `event: result` 终帧：与非流式响应同构的结果 DTO；
- `event: error` 帧：流内已发 200，域错误/意外错误降级为可展示 message（不再抛 409/500）。
服务在独立线程执行并自管 session（run 回调负责 commit/rollback/close），
生成器只消费队列——避免跨线程共享请求级 session。
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import datetime, timezone
from typing import Callable

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.log import log_event

_COMPONENT = "dialogue-sse"


def wants_event_stream(request: Request) -> bool:
    return "text/event-stream" in (request.headers.get("accept") or "")


def _frame(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def stream_dialogue(run: Callable[[Callable[[str], None]], BaseModel]) -> StreamingResponse:
    q: "queue.SimpleQueue" = queue.SimpleQueue()

    def emit_stage(stage: str) -> None:
        q.put(("stage", json.dumps(
            {"stage": stage, "at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False,
        )))

    def worker() -> None:
        try:
            result = run(emit_stage)
            q.put(("result", result.model_dump_json()))
        except (InvalidInput, NotFound, RejectedTransition) as exc:
            q.put(("error", json.dumps({"message": str(exc)}, ensure_ascii=False)))
        except Exception as exc:  # noqa: BLE001 流内已 200，错误只能降级为帧
            log_event(_COMPONENT, "dialogue.stream.failed", level="ERROR",
                      error_code=type(exc).__name__, ok=False)
            q.put(("error", json.dumps({"message": "服务内部错误，请稍后重试"}, ensure_ascii=False)))
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            event, data = item
            yield _frame(event, data)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
