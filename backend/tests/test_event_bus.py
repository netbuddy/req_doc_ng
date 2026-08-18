"""AgentRun 事件总线（Redis Streams）：发布→订阅重放、竞态修复、续传、心跳、保留/回收、Null。

用 fakeredis 驱动真实 XADD/XREAD 代码路径，免真 Redis。
"""
import asyncio
from contextlib import aclosing
from unittest.mock import patch

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio

from app.adapters import event_bus as eb
from app.adapters.event_bus import (
    EVENT_COMPLETED,
    EVENT_STARTED,
    HEARTBEAT_EVENT,
    TERMINAL_EVENTS,
    NullAgentRunEventBus,
    RedisStreamEventBus,
    _stream_key,
)


@pytest.fixture()
def bus_and_server():
    server = fakeredis.FakeServer()

    def sync_from_url(url, **kw):
        return fakeredis.FakeStrictRedis(server=server, **kw)

    def async_from_url(url, **kw):
        return fakeredis.aioredis.FakeRedis(server=server, **kw)

    with patch.object(redis.Redis, "from_url", staticmethod(sync_from_url)), patch.object(
        redis.asyncio.Redis, "from_url", staticmethod(async_from_url)
    ):
        yield RedisStreamEventBus("redis://fake"), server


async def _collect(bus, run_id, last_id="0", *, stop_terminal=True, limit=10):
    out = []
    async with aclosing(bus.subscribe(run_id, last_id)) as stream:
        async for event, entry_id in stream:
            out.append((event, entry_id))
            if event == HEARTBEAT_EVENT and stop_terminal:
                break  # 空闲：拿到一个心跳即可停
            if stop_terminal and event in TERMINAL_EVENTS:
                break
            if len(out) >= limit:
                break
    return out


def test_publish_then_subscribe_replays_all_incl_terminal(bus_and_server):
    """竞态修复：worker 早已发布完，晚到的订阅从 '0' 重放仍拿到全部含终态事件。"""
    bus, _ = bus_and_server
    bus.publish("R1", EVENT_STARTED)
    bus.publish("R1", EVENT_COMPLETED)

    got = asyncio.run(asyncio.wait_for(_collect(bus, "R1", "0"), timeout=5))

    assert [e for e, _ in got] == [EVENT_STARTED, EVENT_COMPLETED]


def test_last_event_id_resumes_after_given_id(bus_and_server):
    """断线续传：从上次 entry_id 起读，只收到其后的事件。"""
    bus, _ = bus_and_server
    bus.publish("R2", EVENT_STARTED)
    bus.publish("R2", EVENT_COMPLETED)
    first = asyncio.run(asyncio.wait_for(_collect(bus, "R2", "0"), timeout=5))
    started_id = first[0][1]

    resumed = asyncio.run(asyncio.wait_for(_collect(bus, "R2", started_id), timeout=5))

    assert [e for e, _ in resumed] == [EVENT_COMPLETED]


def test_heartbeat_when_idle(bus_and_server, monkeypatch):
    """空闲无事件：XREAD 阻塞到点产出心跳哨兵（cursor 不前移）。"""
    bus, _ = bus_and_server
    monkeypatch.setattr(eb, "_BLOCK_MS", 50)

    got = asyncio.run(asyncio.wait_for(_collect(bus, "IDLE", "0"), timeout=5))

    assert got and got[0][0] == HEARTBEAT_EVENT


def test_terminal_sets_maxlen_and_expire(bus_and_server, monkeypatch):
    """保留/回收：XADD 限长，终态后对 stream key 设 EXPIRE。"""
    bus, server = bus_and_server
    monkeypatch.setattr(eb, "_STREAM_MAXLEN", 3)
    for _ in range(5):
        bus.publish("R3", EVENT_STARTED)
    bus.publish("R3", EVENT_COMPLETED)

    client = fakeredis.FakeStrictRedis(server=server)
    key = _stream_key("R3")
    assert client.xlen(key) <= 3  # 限长生效
    assert client.ttl(key) > 0  # 终态后设了回收 TTL


def test_publish_failure_is_swallowed_and_logged(bus_and_server):
    """发布失败不拖垮 worker：DB 已是事实源。"""
    bus, _ = bus_and_server

    with patch.object(RedisStreamEventBus, "_sync", side_effect=RuntimeError("down")):
        bus.publish("R4", EVENT_STARTED)  # 不抛异常即通过


# ---- 事件稀疏容忍（T20260724-agent-run-observability ①：SSE 断流根因）----


def test_subscribe_socket_timeout_exceeds_block_window():
    """读超时须大于 XREAD 阻塞时长。

    redis-py 8 的默认 socket_timeout=5 秒小于 15 秒阻塞时长，空流阻塞读满 5 秒即抛
    TimeoutError——这就是事件间隔一长 SSE 就被掐断的根因。
    """
    assert eb._SUBSCRIBE_SOCKET_TIMEOUT_SECONDS > eb._BLOCK_MS / 1000


def test_subscribe_passes_socket_timeout_to_client(monkeypatch):
    """放宽的读超时确实传给了订阅客户端（不只是常量摆着）。"""
    captured: dict = {}
    server = fakeredis.FakeServer()

    def async_from_url(url, **kw):
        captured.update(kw)
        return fakeredis.aioredis.FakeRedis(server=server, **kw)

    monkeypatch.setattr(eb, "_BLOCK_MS", 50)  # 免测试真等 15 秒
    with patch.object(redis.asyncio.Redis, "from_url", staticmethod(async_from_url)):
        bus = RedisStreamEventBus("redis://fake")
        asyncio.run(asyncio.wait_for(_collect(bus, "TO1", "0"), timeout=5))

    assert captured["socket_timeout"] == eb._SUBSCRIBE_SOCKET_TIMEOUT_SECONDS


def test_read_timeout_keeps_stream_alive_and_delivers_terminal(bus_and_server, monkeypatch):
    """读超时不再断流：产出保活心跳继续读，其后的终态事件正常送达，且不记 read_failed。"""
    bus, _ = bus_and_server
    monkeypatch.setattr(eb, "_BLOCK_MS", 50)
    logged: list[str] = []
    monkeypatch.setattr(eb, "log_event", lambda component, event, **kw: logged.append(event))

    bus.publish("TO2", EVENT_STARTED)
    real_xread = fakeredis.aioredis.FakeRedis.xread
    calls = {"n": 0}

    async def flaky_xread(self, streams, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # 第一轮取到 started 后，第二轮模拟读超时
            raise redis.exceptions.TimeoutError("Timeout reading from socket")
        if calls["n"] == 3:  # 超时被容忍后，晚到的终态事件才发布
            bus.publish("TO2", EVENT_COMPLETED)
        return await real_xread(self, streams, **kw)

    with patch.object(fakeredis.aioredis.FakeRedis, "xread", flaky_xread):
        got = asyncio.run(
            asyncio.wait_for(_collect(bus, "TO2", "0", stop_terminal=False, limit=3), timeout=5)
        )

    assert [e for e, _ in got] == [EVENT_STARTED, HEARTBEAT_EVENT, EVENT_COMPLETED]
    assert "agent.run.stream.read_timeout" in logged
    assert "agent.run.stream.read_failed" not in logged  # 超时不再进 ERROR/诊断中心


def test_unrecoverable_read_error_still_ends_stream(bus_and_server, monkeypatch):
    """真不可恢复错误（连接被拒等）仍终止流并记 read_failed——容忍只针对超时。"""
    bus, _ = bus_and_server
    monkeypatch.setattr(eb, "_BLOCK_MS", 50)
    logged: list[str] = []
    monkeypatch.setattr(eb, "log_event", lambda component, event, **kw: logged.append(event))

    async def broken_xread(self, streams, **kw):
        raise redis.exceptions.ConnectionError("connection refused")

    with patch.object(fakeredis.aioredis.FakeRedis, "xread", broken_xread):
        got = asyncio.run(
            asyncio.wait_for(_collect(bus, "TO3", "0", stop_terminal=False, limit=3), timeout=5)
        )

    assert got == []
    assert "agent.run.stream.read_failed" in logged


def test_null_bus_is_noop_and_not_live():
    bus = NullAgentRunEventBus()
    assert bus.live is False
    bus.publish("R5", EVENT_STARTED)  # 不抛异常

    got = asyncio.run(asyncio.wait_for(_collect(bus, "R5", "0", limit=1), timeout=5))
    assert got == []  # Null 不推送
