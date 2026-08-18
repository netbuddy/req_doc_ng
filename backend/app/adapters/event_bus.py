"""AgentRun 进度事件总线适配器(实现 interfaces.AgentRunEventBus)。

Redis Streams 版:worker 提交后 XADD 事件(仅事件名),SSE 侧 redis.asyncio 阻塞 XREAD
从上次 id 起消费——跨进程真推送、按 id 重放、无 DB。Null 版无 REDIS_URL 时退化,
SSE 端点检测到 `live=False` 改走 DB 轮询降级。

事件稀疏容忍:两次事件相隔多久都不断流——订阅客户端的 socket 读超时显式放宽到大于 XREAD
阻塞时长,且读超时被当作「本段无新事件」产出保活心跳继续读,不再终止流。

铁律:事件 payload 只放事件名,绝不写 error 原文 / prompt / 密钥 / 用户原文(AGENTS.md 规则 8)。
"""
from __future__ import annotations

from typing import AsyncIterator

from app.config import Settings
from app.log import log_event

_COMPONENT = "event-bus"

# 事件名(与 repositories/agent_run.py 的写入保持一致)。
EVENT_STARTED = "agent_run.started"
EVENT_COMPLETED = "agent_run.completed"
EVENT_FAILED = "agent_run.failed"
TERMINAL_EVENTS = frozenset({EVENT_COMPLETED, EVENT_FAILED})

# 心跳哨兵:XREAD block 超时无新事件时产出,供 SSE 发注释帧保活(非真实事件)。
HEARTBEAT_EVENT = ":heartbeat"

_STREAM_MAXLEN = 1000  # 每 run 事件流限长(approximate)
_STREAM_TTL_SECONDS = 3600  # 终态后回收已完成流
_BLOCK_MS = 15000  # XREAD 阻塞时长(到点产出心跳)

# 订阅端 socket 读超时(秒)。redis-py 8 的 Redis.__init__ 默认 socket_timeout=5,小于上面的
# 阻塞时长:空流阻塞读满 5 秒即抛 TimeoutError,SSE 在事件间隔超过 5 秒时被掐断(2026-07-25
# 实测)。故显式放宽到「阻塞时长 + 余量」,令 XREAD 总能自然到点返回空结果而非撞上读超时。
_SUBSCRIBE_SOCKET_TIMEOUT_SECONDS = _BLOCK_MS / 1000 + 5
_SUBSCRIBE_CONNECT_TIMEOUT_SECONDS = 5  # 建连超时无需放宽(连不上要快速失败)


def _stream_key(run_id: str) -> str:
    return f"agent_run:events:{run_id}"


class RedisStreamEventBus:
    """Redis Streams 实现。发布用同步客户端,订阅用 redis.asyncio。"""

    live = True

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._sync_client = None  # 惰性:worker 进程内复用

    def _sync(self):
        if self._sync_client is None:
            from redis import Redis

            self._sync_client = Redis.from_url(self._url)
        return self._sync_client

    def publish(self, run_id: str, event: str) -> None:
        key = _stream_key(run_id)
        try:
            client = self._sync()
            client.xadd(key, {"event": event}, maxlen=_STREAM_MAXLEN, approximate=True)
            if event in TERMINAL_EVENTS:
                client.expire(key, _STREAM_TTL_SECONDS)
            log_event(_COMPONENT, "agent.run.event.published", run_id=run_id, agent_event=event, ok=True)
        except Exception as exc:  # 发布失败不拖垮 worker:DB 已是事实源,SSE 可经 poll 降级
            log_event(
                _COMPONENT,
                "agent.run.event.publish_failed",
                level="ERROR",
                run_id=run_id,
                agent_event=event,
                ok=False,
                error_code=type(exc).__name__,
            )

    async def subscribe(self, run_id: str, last_id: str = "0") -> AsyncIterator[tuple[str, str]]:
        from redis.asyncio import Redis
        from redis.exceptions import TimeoutError as RedisTimeoutError

        key = _stream_key(run_id)
        client = Redis.from_url(
            self._url,
            decode_responses=True,
            socket_timeout=_SUBSCRIBE_SOCKET_TIMEOUT_SECONDS,
            socket_connect_timeout=_SUBSCRIBE_CONNECT_TIMEOUT_SECONDS,
        )
        cursor = last_id
        log_event(_COMPONENT, "agent.run.stream.subscribed", run_id=run_id, last_id=last_id)
        try:
            while True:
                try:
                    resp = await client.xread({key: cursor}, block=_BLOCK_MS)
                except (RedisTimeoutError, TimeoutError) as exc:
                    # 读超时 = 这段时间没有新事件,不是故障:产出保活心跳、下一轮继续阻塞读。
                    # (显式放宽 socket_timeout 后本分支应罕见,留作第二道保险——两处都改互为
                    # 保险,任一失效仍不断流。)redis.exceptions.TimeoutError 不继承内建
                    # TimeoutError,asyncio 侧超时才是内建的,故两个都要接。
                    log_event(
                        _COMPONENT,
                        "agent.run.stream.read_timeout",
                        run_id=run_id,
                        ok=True,
                        error_code=type(exc).__name__,
                        block_ms=_BLOCK_MS,
                    )
                    yield (HEARTBEAT_EVENT, cursor)  # 保活心跳,cursor 不前移
                    continue
                except Exception as exc:
                    # 真不可恢复错误(连接被拒、协议错等)才终止流并记 ERROR 进诊断中心。
                    log_event(
                        _COMPONENT,
                        "agent.run.stream.read_failed",
                        level="ERROR",
                        run_id=run_id,
                        ok=False,
                        error_code=type(exc).__name__,
                    )
                    return
                if not resp:
                    yield (HEARTBEAT_EVENT, cursor)  # 保活心跳,cursor 不前移
                    continue
                for _stream, entries in resp:
                    for entry_id, fields in entries:
                        cursor = entry_id
                        yield (fields.get("event", ""), entry_id)
        finally:
            log_event(_COMPONENT, "agent.run.stream.unsubscribed", run_id=run_id)
            await client.aclose()


class NullAgentRunEventBus:
    """无 REDIS_URL / 测试默认。publish 空操作;SSE 端点见 live=False 走 DB 轮询降级。"""

    live = False

    def publish(self, run_id: str, event: str) -> None:
        log_event(_COMPONENT, "agent.run.event.published", run_id=run_id, agent_event=event, ok=True, sink="null")

    async def subscribe(self, run_id: str, last_id: str = "0") -> AsyncIterator[tuple[str, str]]:
        # Null 不推送;端点不应调用(应走降级)。留空异步生成器以满足协议。
        return
        yield  # pragma: no cover  (标记为异步生成器)


def build_agent_run_event_bus(settings: Settings):
    """有 REDIS_URL → Redis Streams;无 → Null(对齐 build_source_intake_judge/make_enqueue)。"""
    if settings.redis_url:
        return RedisStreamEventBus(settings.redis_url)
    return NullAgentRunEventBus()
