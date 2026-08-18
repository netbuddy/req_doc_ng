"""运行态面板 / 诊断中心(04A §2.1):六风险组现算判定 + 诊断缓冲 + HTTP 形状
+ 悬队自愈与最近作业明细(T20260724-agent-run-observability ②⑤)。"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.db.models  # noqa: F401  register tables
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import AgentRun, AgentRunEvent, Notification
from app.diagnostics import DiagnosticsBuffer, buffer as global_buffer
from app.log import log_event
from app.services.runtime_status import (
    FAILURE_SPIKE_THRESHOLD,
    GENERIC_FAILURE_REASON,
    ORPHANED_QUEUE_REASON,
    QUEUE_BACKLOG_THRESHOLD,
    RECENT_JOBS_LIMIT,
    build_runtime_status,
)

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session_factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _probe(mode="queued", redis_ok=True, workers=1, queued=0):
    return lambda: {"mode": mode, "redis_ok": redis_ok, "workers": workers, "queued": queued}


_NO_PROBE = object()  # 「本次调用不传队列探针」的哨兵（None 是有意义的取值＝队列状态查不到）


def _build(session_factory, probe=None, live=True, now=NOW, diagnostics=lambda: [],
           queued_run_ids=_NO_PROBE, queued_run_ids_probe=None):
    if queued_run_ids_probe is None and queued_run_ids is not _NO_PROBE:
        queued_run_ids_probe = lambda: queued_run_ids  # noqa: E731
    return build_runtime_status(
        session_factory=session_factory,
        async_probe=probe or _probe(),
        event_bus_live=live,
        diagnostics_snapshot=diagnostics,
        queued_run_ids_probe=queued_run_ids_probe,
        now=now,
    )


def _component(status, key):
    return next(c for c in status.components if c.key == key)


def _codes(status):
    return {a.code for a in status.alerts}


def _seed_run(factory, status, created_delta=timedelta(0), updated_delta=timedelta(0)):
    s = factory()
    try:
        s.add(
            AgentRun(
                kind="source_intake",
                status=status,
                created_at=NOW - created_delta,
                updated_at=NOW - updated_delta,
            )
        )
        s.commit()
    finally:
        s.close()


# ---- 六风险组判定 ----


def test_all_ok_queued_mode_is_normal(session_factory):
    status = _build(session_factory)
    assert status.status == "normal"
    assert status.alert_count == 0
    assert {c.key: c.status for c in status.components} == {
        "api": "ok", "db": "ok", "redis": "ok", "worker": "ok", "event_bus": "ok",
    }


def test_inline_mode_degrades_sse_and_marks_not_applicable(session_factory):
    status = _build(session_factory, probe=_probe(mode="inline", redis_ok=None, workers=None, queued=None), live=False)
    assert status.status == "degraded"
    assert _codes(status) == {"sse.degraded"}
    assert _component(status, "redis").status == "not_applicable"
    assert _component(status, "worker").status == "not_applicable"
    assert _component(status, "event_bus").status == "degraded"
    assert status.async_jobs.mode == "inline"
    assert status.async_jobs.queue_depth is None


def test_worker_absent_is_error_alert(session_factory):
    status = _build(session_factory, probe=_probe(workers=0))
    assert _codes(status) == {"async.worker.absent"}
    assert status.alerts[0].level == "ERROR"
    assert _component(status, "worker").status == "down"
    assert status.status == "degraded"


def test_redis_unreachable_is_error_alert(session_factory):
    status = _build(session_factory, probe=_probe(redis_ok=False, workers=None, queued=None))
    assert "async.redis.unreachable" in _codes(status)
    # redis 不可达时不再叠加 worker.absent(探测不到 worker 数,归因到 redis)
    assert "async.worker.absent" not in _codes(status)
    assert _component(status, "redis").status == "down"


def test_queue_backlog_over_threshold_warns(session_factory):
    status = _build(session_factory, probe=_probe(queued=QUEUE_BACKLOG_THRESHOLD + 1))
    assert "async.queue.backlog" in _codes(status)
    assert status.async_jobs.queue_depth == QUEUE_BACKLOG_THRESHOLD + 1


def test_db_down_is_down_and_aggregates_unavailable():
    def broken_factory():
        raise ConnectionError("db is gone")

    status = _build(broken_factory)
    assert status.status == "down"
    assert "db.unavailable" in _codes(status)
    assert _component(status, "db").status == "down"
    assert status.async_jobs.queued is None
    assert status.async_jobs.failed_recent is None


def test_failure_spike_in_window_warns(session_factory):
    for _ in range(FAILURE_SPIKE_THRESHOLD):
        _seed_run(session_factory, "failed", updated_delta=timedelta(minutes=1))
    status = _build(session_factory)
    assert "agent_run.failure_spike" in _codes(status)
    assert status.async_jobs.failed_recent == FAILURE_SPIKE_THRESHOLD


def test_old_failures_do_not_spike_but_count_recent(session_factory):
    # 窗口外(1 小时前)失败:计入近 24h 摘要,不触发突增告警
    for _ in range(FAILURE_SPIKE_THRESHOLD):
        _seed_run(session_factory, "failed", updated_delta=timedelta(hours=1))
    status = _build(session_factory)
    assert "agent_run.failure_spike" not in _codes(status)
    assert status.async_jobs.failed_recent == FAILURE_SPIKE_THRESHOLD


def test_async_jobs_summary_counts_and_oldest_waiting(session_factory):
    _seed_run(session_factory, "queued", created_delta=timedelta(minutes=30))
    _seed_run(session_factory, "queued", created_delta=timedelta(minutes=5))
    _seed_run(session_factory, "started")
    _seed_run(session_factory, "succeeded")
    status = _build(session_factory)
    assert status.async_jobs.queued == 2
    assert status.async_jobs.running == 1
    assert status.async_jobs.oldest_waiting_minutes == 30


# ---- 诊断事件缓冲 ----


def test_diagnostics_buffer_aggregates_warn_error_only():
    buf = DiagnosticsBuffer()
    buf.record("agent-queue", "async.worker.absent", "WARN")
    buf.record("agent-queue", "async.worker.absent", "ERROR")
    buf.record("backend-api", "some.info.event", "INFO")
    snap = buf.snapshot()
    assert len(snap) == 1
    entry = snap[0]
    assert entry["event"] == "async.worker.absent"
    assert entry["count"] == 2
    assert entry["level"] == "ERROR"  # 保留最近一次级别
    assert entry["first_seen"] <= entry["last_seen"]


def test_diagnostics_buffer_evicts_oldest_event_code():
    buf = DiagnosticsBuffer(max_entries=2)
    buf.record("c", "e.first", "WARN")
    buf.record("c", "e.second", "WARN")
    buf.record("c", "e.third", "WARN")
    events = {e["event"] for e in buf.snapshot()}
    assert events == {"e.second", "e.third"}


def test_log_event_feeds_global_buffer():
    global_buffer.reset()
    log_event("test-component", "test.warn.event", level="WARN", ok=False)
    log_event("test-component", "test.info.event", level="INFO")
    snap = global_buffer.snapshot()
    assert [e["event"] for e in snap] == ["test.warn.event"]
    # 白名单:摘要里没有 msg/body 字段
    assert set(snap[0]) == {"event", "component", "level", "first_seen", "last_seen", "count"}
    global_buffer.reset()


def test_diagnostics_projected_into_status(session_factory):
    diag = [
        {
            "event": "agent.run.failed", "component": "agent-worker", "level": "ERROR",
            "first_seen": "2026-07-04T11:00:00+00:00", "last_seen": "2026-07-04T11:58:00+00:00",
            "count": 7,
        }
    ]
    status = _build(session_factory, diagnostics=lambda: diag)
    assert len(status.diagnostics) == 1
    assert status.diagnostics[0].event == "agent.run.failed"
    assert status.diagnostics[0].count == 7


# ---- HTTP 形状(端点永不 500;DB 是否可达只影响响应体)----


def test_http_shape():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.get("/api/runtime-status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("normal", "degraded", "down")
    assert isinstance(body["alert_count"], int)
    assert {c["key"] for c in body["components"]} == {"api", "db", "redis", "worker", "event_bus", "llm"}
    assert "async_jobs" in body and "diagnostics" in body and "alerts" in body
    assert body["alert_count"] == len(body["alerts"])


# ---- 模型服务探针（04A §2.1 增补：AI 全链路共享瓶颈的全局事实）----


def _build_with_llm(session_factory, llm_probe):
    return build_runtime_status(
        session_factory=session_factory,
        async_probe=_probe(),
        event_bus_live=True,
        diagnostics_snapshot=lambda: [],
        llm_probe=llm_probe,
        now=NOW,
    )


def test_llm_component_absent_when_probe_not_wired(session_factory):
    status = _build(session_factory)
    assert all(c.key != "llm" for c in status.components)


def test_llm_not_configured_is_not_applicable_no_alert(session_factory):
    status = _build_with_llm(session_factory, lambda: {"configured": False, "ok": None, "latency_ms": None})
    comp = _component(status, "llm")
    assert comp.status == "not_applicable" and "stub" in comp.detail
    assert "llm.unreachable" not in _codes(status)


def test_llm_reachable_reports_latency(session_factory):
    status = _build_with_llm(session_factory, lambda: {"configured": True, "ok": True, "latency_ms": 42})
    comp = _component(status, "llm")
    assert comp.status == "ok" and "42" in comp.detail
    assert status.status == "normal"


def test_llm_unreachable_is_error_alert(session_factory):
    status = _build_with_llm(session_factory, lambda: {"configured": True, "ok": False, "latency_ms": None})
    assert _component(status, "llm").status == "down"
    assert "llm.unreachable" in _codes(status)
    assert status.status == "degraded"


def test_llm_probe_exception_degrades_to_down(session_factory):
    def broken():
        raise ConnectionError("probe boom")

    status = _build_with_llm(session_factory, broken)
    assert _component(status, "llm").status == "down"
    assert "llm.unreachable" in _codes(status)


# ---- 悬队自愈（T20260724-agent-run-observability ②）----
# 排队后无人执行的 AgentRun 会永久钉住等待数（库内曾有 22 天前的 docx_export 孤儿行）。
# 判死须同时满足「过判死期限」与「队列中无对应任务」，少一条都不动。


def _seed_kind_run(factory, kind, status, created_delta=timedelta(0)):
    """按 kind 造一行 AgentRun，返回 run_id。"""
    s = factory()
    try:
        run = AgentRun(kind=kind, status=status, created_at=NOW - created_delta,
                       updated_at=NOW - created_delta)
        s.add(run)
        s.commit()
        return str(run.id)
    finally:
        s.close()


def _stale_delta(kind):
    """超出该 kind 所属 lane 判死期限的入队龄。"""
    from app.services.run_liveness import lane_for_kind, run_liveness_deadline_seconds

    return timedelta(seconds=run_liveness_deadline_seconds(lane_for_kind(kind)) + 60)


def _status_of(factory, run_id):
    s = factory()
    try:
        return s.get(AgentRun, uuid.UUID(run_id)).status
    finally:
        s.close()


def _failed_event_count(factory, run_id):
    """该 run 落了几条 agent_run.failed 事件（判死被重复执行会多落一条）。"""
    s = factory()
    try:
        return (
            s.query(AgentRunEvent)
            .filter(AgentRunEvent.run_id == uuid.UUID(run_id),
                    AgentRunEvent.event == "agent_run.failed")
            .count()
        )
    finally:
        s.close()


def _notification_count(factory):
    """通知表里的行数（对账判死不得给用户推通知）。"""
    s = factory()
    try:
        return s.query(Notification).count()
    finally:
        s.close()


def test_orphan_queued_run_is_reclaimed_as_failed(session_factory):
    """超期 queued 且队列中无对应任务 → 判死 failed，等待数随之回落。"""
    run_id = _seed_kind_run(session_factory, "docx_export", "queued",
                            _stale_delta("docx_export"))

    status = _build(session_factory, queued_run_ids=set())

    assert _status_of(session_factory, run_id) == "failed"
    assert status.async_jobs.queued == 0  # 本次响应即已扣除
    assert status.async_jobs.oldest_waiting_minutes is None


def test_orphan_reclaim_writes_stable_reason_code(session_factory):
    """判死写稳定码（面板据此显示失败原因，不投影 error 原文）。"""
    run_id = _seed_kind_run(session_factory, "docx_export", "queued",
                            _stale_delta("docx_export"))

    status = _build(session_factory, queued_run_ids=set())

    row = next(job for job in status.recent_jobs if job.run_id == run_id)
    assert row.status == "failed"
    assert row.reason_code == ORPHANED_QUEUE_REASON


def test_reclaim_is_idempotent_across_reads(session_factory):
    """自愈可重入：再读一次不重复处理（行已非 queued）。"""
    run_id = _seed_kind_run(session_factory, "docx_export", "queued",
                            _stale_delta("docx_export"))

    _build(session_factory, queued_run_ids=set())
    before = _status_of(session_factory, run_id)
    _build(session_factory, queued_run_ids=set())

    assert before == "failed" and _status_of(session_factory, run_id) == "failed"
    # 第二次读取没有再处理这一行:判死只落了一条事件（重复处理会落两条）。
    assert _failed_event_count(session_factory, run_id) == 1


def test_fresh_queued_run_is_not_killed(session_factory):
    """未过判死期限的排队行不动（worker 忙碌时不误杀）。"""
    run_id = _seed_kind_run(session_factory, "item_formation", "queued", timedelta(seconds=30))

    status = _build(session_factory, queued_run_ids=set())

    assert _status_of(session_factory, run_id) == "queued"
    assert status.async_jobs.queued == 1


def test_stale_queued_run_with_live_queue_job_is_not_killed(session_factory):
    """已过期限但队列里还挂着对应任务 → 不动（等它执行）。"""
    run_id = _seed_kind_run(session_factory, "item_formation", "queued",
                            _stale_delta("item_formation"))

    status = _build(session_factory, queued_run_ids={run_id})

    assert _status_of(session_factory, run_id) == "queued"
    assert status.async_jobs.queued == 1


def test_unknown_queue_state_skips_reclaim(session_factory):
    """队列状态查不到（Redis 不可达）→ 一律不判死，宁可放过不误杀。"""
    run_id = _seed_kind_run(session_factory, "docx_export", "queued",
                            _stale_delta("docx_export"))

    _build(session_factory, queued_run_ids=None)

    assert _status_of(session_factory, run_id) == "queued"


def test_reclaim_failure_does_not_break_the_read(session_factory):
    """自愈炸了也要把运行态读出来（只记 WARN），且读数照常产出。"""
    def boom():
        raise RuntimeError("probe boom")

    run_id = _seed_kind_run(session_factory, "item_formation", "queued", timedelta(seconds=30))

    status = _build(session_factory, queued_run_ids_probe=boom)

    assert status.status in {"normal", "degraded"}
    assert _component(status, "db").status == "ok"
    # 有区分力的断言：容忍失效时聚合与明细都会停在初值（等待数 None、明细空），
    # 面板就成了"看上去健康但什么都读不出来"，且日志把原因误记为数据库不可达。
    assert status.async_jobs.queued == 1
    assert [job.run_id for job in status.recent_jobs] == [run_id]


# ---- 对账判死不冒充新近失败（冷审查裁定 C1）----
# 判死写入的 updated_at 是"记录时刻"，失败"发生"可能在几周前；聚合按 updated_at 判新鲜度，
# 于是打开面板这个动作本身会点亮一条失败突增告警、把整体状态打成降级、并逐条推通知。


def _seed_stale_orphans(factory, count):
    """造 count 行陈年孤儿：超期 queued 且队列中无对应任务，本次读取会被判死。"""
    return [
        _seed_kind_run(factory, "docx_export", "queued", _stale_delta("docx_export"))
        for _ in range(count)
    ]


def test_reclaimed_orphans_do_not_fake_a_failure_spike(session_factory):
    """一次回收达阈值行数：判死成功，但不算新近失败、不告警、不降级、不推通知。"""
    run_ids = _seed_stale_orphans(session_factory, FAILURE_SPIKE_THRESHOLD)

    status = _build(session_factory, queued_run_ids=set())

    assert all(_status_of(session_factory, rid) == "failed" for rid in run_ids)
    assert "agent_run.failure_spike" not in _codes(status)
    assert status.async_jobs.failed_recent == 0  # 也不抬高近 24h 计数
    assert status.status == "normal"
    assert _notification_count(session_factory) == 0
    # 不隐藏事实：明细表照常显示这些行，带判死稳定码
    reclaimed = [job for job in status.recent_jobs if job.run_id in set(run_ids)]
    assert len(reclaimed) == FAILURE_SPIKE_THRESHOLD
    assert {job.reason_code for job in reclaimed} == {ORPHANED_QUEUE_REASON}


def test_real_recent_failures_still_spike(session_factory):
    """对照组：真正新近发生的失败照旧触发突增告警（排除口径没有放宽过头）。"""
    for _ in range(FAILURE_SPIKE_THRESHOLD):
        _seed_run(session_factory, "failed", updated_delta=timedelta(minutes=1))

    status = _build(session_factory, queued_run_ids=set())

    assert "agent_run.failure_spike" in _codes(status)
    assert status.async_jobs.failed_recent == FAILURE_SPIKE_THRESHOLD
    assert status.status == "degraded"


def test_every_agent_run_kind_maps_to_a_registered_lane():
    """kind → lane 约定不得漂移：每个 kind 都能在 lane 分档表里找到自己。"""
    from app.services.notification import AGENT_RUN_KIND_LABELS
    from app.services.run_liveness import lane_for_kind
    from app.workers.queue import _LANE_TIER

    unregistered = [k for k in AGENT_RUN_KIND_LABELS if lane_for_kind(k) not in _LANE_TIER]
    assert unregistered == []


# ---- 最近作业明细（T20260724-agent-run-observability ⑤）----


def test_recent_jobs_are_newest_first_with_plain_language_labels(session_factory):
    """终态与非终态混排、按发起时间倒序、类型给白话名（稳定码不裸出）。"""
    _seed_kind_run(session_factory, "docx_export", "succeeded", timedelta(minutes=30))
    _seed_kind_run(session_factory, "element_recognition", "started", timedelta(minutes=5))
    _seed_kind_run(session_factory, "item_formation", "queued", timedelta(minutes=1))

    status = _build(session_factory)

    assert [job.kind for job in status.recent_jobs] == [
        "item_formation", "element_recognition", "docx_export",
    ]
    assert [job.kind_label for job in status.recent_jobs] == [
        "需求条目形成", "知识项识别", "docx 导出转换",
    ]


def test_recent_jobs_duration_only_for_terminal_rows(session_factory):
    """耗时只对终态给值；非终态给 None（前端呈现等待中/进行中）。"""
    s = session_factory()
    try:
        s.add(AgentRun(kind="docx_export", status="succeeded",
                       created_at=NOW - timedelta(seconds=90), updated_at=NOW - timedelta(seconds=18)))
        s.add(AgentRun(kind="item_formation", status="started",
                       created_at=NOW - timedelta(seconds=10), updated_at=NOW - timedelta(seconds=9)))
        s.commit()
    finally:
        s.close()

    status = _build(session_factory)
    by_kind = {job.kind: job for job in status.recent_jobs}

    assert by_kind["docx_export"].duration_seconds == 72
    assert by_kind["item_formation"].duration_seconds is None


def test_recent_jobs_never_project_raw_error_text(session_factory):
    """失败行只给通用稳定码，error 原文绝不出现在投影里（硬规则 8）。"""
    s = session_factory()
    try:
        s.add(AgentRun(kind="item_diagnosis", status="failed", error="Traceback: 秘密原文",
                       created_at=NOW - timedelta(minutes=2), updated_at=NOW - timedelta(minutes=1)))
        s.commit()
    finally:
        s.close()

    status = _build(session_factory)
    row = status.recent_jobs[0]

    assert row.reason_code == GENERIC_FAILURE_REASON
    assert "秘密原文" not in row.model_dump_json()


def test_recent_jobs_capped_at_limit(session_factory):
    """条数有上限（面板是摘要不是列表页）。"""
    for minute in range(RECENT_JOBS_LIMIT + 4):
        _seed_kind_run(session_factory, "source_intake", "succeeded", timedelta(minutes=minute))

    status = _build(session_factory)

    assert len(status.recent_jobs) == RECENT_JOBS_LIMIT
