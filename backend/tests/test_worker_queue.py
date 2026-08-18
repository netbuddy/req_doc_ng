"""按 lane 的 rq job_timeout（T20260711-rq-job-timeout）。

mock 断言，不依赖真 Redis：rq 分支入队携带表定 job_timeout（A2）；
未登记 lane 回落单调用档＋WARN；inline 分支行为不变；
入队结构化日志含 job_timeout 字段（A3）。
"""
from __future__ import annotations

import dataclasses

import pytest

import app.workers.queue as queue_mod

# deps.py 9 个 make_enqueue 调用点 + run_search_reindex（防御性登记）
BATCH_LANES = {"run_item_formation", "run_item_diagnosis", "run_item_structure_recheck"}
SINGLE_LANES = {
    # 诊断逐条目子 job（issue #10 卡B1 拆分调度单元；批次协调 run 仍走 batch 判活封套）
    "run_item_diagnosis_item",
    "run_source_intake",
    "run_element_recognition",
    "run_element_review",
    "run_element_execution",
    "run_chart_suggestion",
    "run_chart_verification",
    "run_docx_export",
    "run_search_reindex",
}


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def enqueue(self, task_path: str, *args, **kwargs) -> None:
        self.calls.append((task_path, args, kwargs))


@pytest.fixture()
def captured_events(monkeypatch):
    events: list[dict] = []

    def _capture(component, event, msg="", level="INFO", **fields):
        events.append({"component": component, "event": event, "level": level, **fields})

    monkeypatch.setattr(queue_mod, "log_event", _capture)
    return events


def _set_settings(monkeypatch, **overrides) -> None:
    """Settings 为 frozen dataclass：以 replace 副本替换 queue 模块级引用。"""
    monkeypatch.setattr(queue_mod, "settings", dataclasses.replace(queue_mod.settings, **overrides))


def _rq_enqueue(monkeypatch, task_name: str) -> FakeQueue:
    """构造 rq 分支闭包：假 Redis URL＋假 Queue，探测 WARN 短路（不触网）。"""
    fake = FakeQueue()
    _set_settings(monkeypatch, redis_url="redis://localhost:6399/9")
    monkeypatch.setattr(queue_mod, "_intake_queue", lambda conn: fake)
    monkeypatch.setattr(queue_mod, "warn_if_no_worker", lambda conn, run_id=None: False)
    fake.enqueue_fn = queue_mod.make_enqueue(task_name)
    return fake


# ---- A1：全部 lane 登记入表，分档与 tasks.py 实现一致，无 lane 落 rq 180s 默认 ----


def test_every_task_entry_registered_in_lane_tier():
    from app.workers import tasks as tasks_mod

    entries = {
        n for n in dir(tasks_mod)
        if n.startswith("run_")
        and callable(getattr(tasks_mod, n))
        and getattr(tasks_mod, n).__module__ == tasks_mod.__name__  # 排除 re-export 的 judgement
    }
    assert entries == set(queue_mod._LANE_TIER)  # 新 lane 未登记时此处先红


def test_tier_assignment_matches_verified_classification():
    assert {n for n, t in queue_mod._LANE_TIER.items() if t == "batch"} == BATCH_LANES
    assert {n for n, t in queue_mod._LANE_TIER.items() if t == "single"} == SINGLE_LANES


def test_no_lane_falls_back_to_rq_default_180():
    for lane in queue_mod._LANE_TIER:
        assert queue_mod.job_timeout_for(lane) >= 360


def test_single_tier_scales_with_llm_timeout(monkeypatch):
    _set_settings(monkeypatch, llm_timeout=300.0)
    assert queue_mod.job_timeout_for("run_source_intake") == 600
    _set_settings(monkeypatch, llm_timeout=60.0)
    assert queue_mod.job_timeout_for("run_source_intake") == 360  # 下限兜底


# ---- A2：rq 分支 enqueue 携带表定 job_timeout ----


@pytest.mark.parametrize("lane", sorted(BATCH_LANES | SINGLE_LANES))
def test_rq_enqueue_carries_table_timeout(monkeypatch, captured_events, lane):
    fake = _rq_enqueue(monkeypatch, lane)
    fake.enqueue_fn("ctx-1", "run-1")

    (task_path, args, kwargs) = fake.calls[0]
    assert task_path == f"app.workers.tasks.{lane}"
    assert args == ("ctx-1", "run-1")
    # 诊断 lane 自动重试上限 1 → 最坏每条目 2 次调用，批上限随动翻倍（合并裁定修复）
    if lane == "run_item_diagnosis":
        expected = 3600
    elif lane in BATCH_LANES:
        expected = 1800
    else:
        expected = int(max(2 * queue_mod.settings.llm_timeout, 360))
    assert kwargs == {"job_timeout": expected}


def test_unregistered_lane_falls_back_with_warn(monkeypatch, captured_events):
    fake = _rq_enqueue(monkeypatch, "run_future_lane")
    fake.enqueue_fn("ctx-2", "run-2")

    assert fake.calls[0][2] == {"job_timeout": int(max(2 * queue_mod.settings.llm_timeout, 360))}
    warns = [e for e in captured_events if e["event"] == "async.job_timeout.unregistered"]
    assert warns and warns[0]["level"] == "WARN" and warns[0]["lane"] == "run_future_lane"


# ---- A3：入队结构化日志含 job_timeout（现有 log_event 口径，无原文） ----


def test_rq_enqueue_logs_structured_event(monkeypatch, captured_events):
    fake = _rq_enqueue(monkeypatch, "run_item_formation")
    fake.enqueue_fn("ctx-3", "run-3")

    enq = [e for e in captured_events if e["event"] == "async.enqueue"]
    assert enq == [{
        "component": "agent-queue",
        "event": "async.enqueue",
        "level": "INFO",
        "lane": "run_item_formation",
        "queue": "intake",
        "run_id": "run-3",
        "job_timeout": 1800,
    }]
    assert "ctx-3" not in str(enq)  # 不落上下文引用之外的原文（body 只有稳定码/计数）


# ---- inline 分支零改动：同步就地执行，不产生入队日志 ----


def test_inline_mode_executes_directly_without_enqueue_log(monkeypatch, captured_events):
    from app.workers import tasks as tasks_mod

    called: list[tuple[str, str]] = []
    _set_settings(monkeypatch, redis_url=None)
    monkeypatch.setattr(tasks_mod, "run_source_intake", lambda ctx, run: called.append((ctx, run)))

    enqueue = queue_mod.make_enqueue("run_source_intake")
    enqueue("ctx-4", "run-4")

    assert called == [("ctx-4", "run-4")]
    assert captured_events == []  # inline 不打 async.enqueue，行为不变


# ---- issue #7：inline background_inline 受理即回（提交后台执行，立即返回） ----


def test_inline_background_returns_before_task_finishes(monkeypatch, captured_events):
    """background_inline=True：入队立即返回、任务在后台线程执行（受理即回）。"""
    import threading

    from app.workers import tasks as tasks_mod

    _set_settings(monkeypatch, redis_url=None)
    release = threading.Event()
    done = threading.Event()
    captured: list[tuple[str, str]] = []

    def _slow(ctx, run):
        release.wait(2)  # 模拟慢活（真实为链式重诊 LLM）
        captured.append((ctx, run))
        done.set()

    monkeypatch.setattr(tasks_mod, "run_item_diagnosis", _slow)

    enqueue = queue_mod.make_enqueue("run_item_diagnosis", background_inline=True)
    enqueue("batch-1", "run-1")

    # 任务尚在后台阻塞（release 未置位），入队已返回 → 受理即回成立
    assert captured == []
    logs = [e for e in captured_events if e["event"] == "async.inline_background"]
    assert logs and logs[0]["lane"] == "run_item_diagnosis" and logs[0]["run_id"] == "run-1"
    release.set()
    assert done.wait(2)  # 后台确实执行
    assert captured == [("batch-1", "run-1")]


def test_inline_background_off_by_default_stays_synchronous(monkeypatch):
    """默认（无 background_inline）inline 仍同步就地执行——避免波及其它 lane。"""
    from app.workers import tasks as tasks_mod

    _set_settings(monkeypatch, redis_url=None)
    order: list[str] = []
    monkeypatch.setattr(tasks_mod, "run_source_intake",
                        lambda ctx, run: order.append("task"))
    enqueue = queue_mod.make_enqueue("run_source_intake")
    enqueue("c", "r")
    order.append("after")
    assert order == ["task", "after"]  # 同步：任务先于返回
