"""诊断队列并发改造（issue #10 卡B1）：批次拆逐条目子 job + 增量重诊交错调度。

覆盖任务卡 A1/A3/A5 并发语义三形态（不依赖真 Redis，驱动可测核心 + FIFO 仿真队列）：
- 拆分调度：批次 run 逐条目推进，每子 job 处理一个条目、链式再入队，末条目收尾 succeeded。
- 增量交错：整批进行中插入独立单条目 run，其结论落库不晚于整批剩余条目完成（A1）。
- 失败传播：单条目诊断失败=该条目 diagnosis_failed 落库，批次 run 不整体夭折、仍 succeeded。
逐条目 commit 与进度契约（分母=发起捕获、逐条落库）沿用同步执行体，见 test_item_review。
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.adapters.event_bus import EVENT_COMPLETED, EVENT_FAILED, EVENT_STARTED
from app.adapters.llm import StubRequirementItemDiagnoser, _failed_verdict
from app.api.schemas import ItemizationBatchCommand, ItemReviewDiagnosisCommand
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    AgentRun,
    IntakeRecord,
    ItemDiagnosisRequest,
    ItemDiagnosisRound,
    Material,
    MaterialParseResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
)
from app.domain.enums import ItemizationScopeType
from app.repositories.agent_run import SqlAgentRunRepository
from app.repositories.sqlalchemy import (
    build_sql_item_formation_service,
    build_sql_item_review_service,
)
from app.workers.tasks import _process_diagnosis_item, _process_diagnosis_kickoff

RAW_TEXT = (
    "系统应支持导出 docx。导出耗时不超过五秒。系统应记录操作日志。"
    "界面应支持中文。数据应每日备份。"
)


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


class _RecordingBus:
    """记录 publish(run_id, event) 顺序，供收束次序断言。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def publish(self, run_id: str, event: str) -> None:
        self.events.append((run_id, event))


class _RecordingDiagnoser:
    """包裹 stub：记录逐条目调用；按调用序号强制个别条目失败（失败传播形态）。"""

    def __init__(self, fail_call_indices: tuple[int, ...] = ()) -> None:
        self._stub = StubRequirementItemDiagnoser()
        self._fail = set(fail_call_indices)
        self.calls: list[str] = []

    def diagnose(self, project_ref, diagnosis_mode, item, *args, **kwargs):
        idx = len(self.calls)
        self.calls.append(str(item.get("expression") or ""))
        if idx in self._fail:
            return _failed_verdict("stub 强制失败", "llm_error")
        return self._stub.diagnose(project_ref, diagnosis_mode, item, *args, **kwargs)


def _anchor(exact: str) -> str:
    start = RAW_TEXT.find(exact)
    return json.dumps({"ranges": [{"start": start, "end": start + len(exact), "exact": exact}]})


def _seed_pending_items(session, contents: list[str]) -> dict:
    """已解析结果 + len(contents) 条已确认要素 → 同数量待确认 LDM-007（同评审口径）。"""
    p = Project(name="demo")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text=RAW_TEXT, source_note="接入对象:访谈纪要")
    session.add(mat)
    session.flush()
    session.add(IntakeRecord(
        project_id=p.id, context_ref=uuid.uuid4(), intake_conclusion="accepted", material_ref=mat.id,
    ))
    ctx = ParseRequest(
        project_id=p.id, material_ref=mat.id, operator_ref="U1",
        idempotency_key=f"seed-{uuid.uuid4()}", workspace_version=1,
    )
    session.add(ctx)
    session.flush()
    parse = MaterialParseResult(
        project_id=p.id, material_ref=mat.id, context_ref=ctx.id, parse_status="parsed",
    )
    session.add(parse)
    session.flush()
    for content in contents:
        session.add(RequirementElement(
            project_id=p.id, parse_result_ref=parse.id, element_type="functional_requirement",
            content=content, source_anchor=_anchor(content), confidence=0.9,
            process_status="confirmed",
        ))
    session.commit()
    formation = build_sql_item_formation_service(session, auto_complete=True)
    formation.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=str(p.id), parse_result_ref=str(parse.id), workspace_version="1",
        scope_type=ItemizationScopeType.ALL_ELIGIBLE, target_element_refs=[],
        operator_ref="U1", idempotency_key=f"form-{uuid.uuid4()}",
    ))
    session.commit()
    items = [str(i.id) for i in session.scalars(
        select(RequirementItem).where(RequirementItem.project_id == p.id)
        .order_by(RequirementItem.req_no)
    ).all()]
    version = str(session.get(ParseRequest, ctx.id).workspace_version)
    return {"project": str(p.id), "parse_result": str(parse.id), "items": items, "version": version}


def _create_batch(session, w, item_refs: list[str]) -> str:
    """创建诊断批次 + 逐条目 diagnosing 轮次，但不就地执行（auto_complete=False）。"""
    svc = build_sql_item_review_service(session, auto_complete=False)
    result = svc.start_item_diagnosis(ItemReviewDiagnosisCommand(
        project_ref=w["project"], item_refs=item_refs, diagnosis_mode="standard",
        workspace_version=w["version"], operator_ref="U1", idempotency_key=f"diag-{uuid.uuid4()}",
    ))
    assert result.status == "submitted"
    session.commit()
    batch = session.scalars(
        select(ItemDiagnosisRequest).order_by(ItemDiagnosisRequest.created_at.desc())
    ).first()
    return str(batch.id)


def _new_run(session, batch_ref: str) -> str:
    run_id = SqlAgentRunRepository(session).create("item_diagnosis", batch_ref)
    session.commit()
    return run_id


def _rounds(session, batch_ref: str) -> list[ItemDiagnosisRound]:
    return list(session.scalars(
        select(ItemDiagnosisRound).where(ItemDiagnosisRound.batch_ref == uuid.UUID(batch_ref))
    ).all())


def _drive_one(session, fifo, diagnoser, bus) -> None:
    """仿单 worker 取一子 job 执行：处理一个条目，链式再入队追加至 FIFO 尾部。"""
    batch_ref, run_id = fifo.pop(0)
    _process_diagnosis_item(
        session, batch_ref, run_id, diagnoser, SqlAgentRunRepository(session), bus,
        lambda b, r: fifo.append((b, r)),
    )


# ---- A5 拆分调度：逐条目子 job 推进，末条目收尾 succeeded ----

def test_split_schedules_one_item_per_subjob(session):
    w = _seed_pending_items(session, ["系统应支持导出 docx", "系统应记录操作日志", "界面应支持中文"])
    batch = _create_batch(session, w, w["items"])
    run = _new_run(session, batch)
    diagnoser = _RecordingDiagnoser()
    bus = _RecordingBus()

    fifo = [(batch, run)]
    steps = 0
    while fifo:
        _drive_one(session, fifo, diagnoser, bus)
        steps += 1

    assert steps == 3  # 三条目=三次子 job（逐条目子 job，非单 job 大循环）
    assert len(diagnoser.calls) == 3  # 每条目各调一次 diagnose
    assert all(r.processing_status != "diagnosing" for r in _rounds(session, batch))  # 全收束
    completed = [rid for rid, ev in bus.events if ev == EVENT_COMPLETED]
    assert completed == [run]  # 末子 job 恰好收尾一次 succeeded


# ---- A1 增量交错：整批进行中插入单条目 run，增量结论不晚于整批剩余完成 ----

def test_incremental_interleaves_before_batch_remainder(session):
    w = _seed_pending_items(session, [
        "系统应支持导出 docx", "系统应记录操作日志", "界面应支持中文", "数据应每日备份",
    ])
    batch = _create_batch(session, w, w["items"][:3])       # 整批 A：3 条目
    incr = _create_batch(session, w, w["items"][3:])        # 增量 B：独立单条目 run
    run_a = _new_run(session, batch)
    run_b = _new_run(session, incr)
    diagnoser = _RecordingDiagnoser()
    bus = _RecordingBus()

    fifo = [(batch, run_a)]
    _drive_one(session, fifo, diagnoser, bus)               # A 条目1 → 再入队 A（尾部）
    fifo.append((incr, run_b))                              # 整批进行中：增量 B 到达同队列尾部
    while fifo:
        _drive_one(session, fifo, diagnoser, bus)

    order = [rid for rid, ev in bus.events if ev == EVENT_COMPLETED]
    assert run_b in order and run_a in order
    # A1：增量 B 结论落库（run 收束）不晚于整批 A 剩余全部条目完成
    assert order.index(run_b) < order.index(run_a)
    assert all(r.processing_status != "diagnosing" for r in _rounds(session, batch))
    assert all(r.processing_status != "diagnosing" for r in _rounds(session, incr))


# ---- A3/A5 失败传播：单条目失败不夭折批次 run ----

def test_single_item_failure_does_not_abort_batch_run(session):
    w = _seed_pending_items(session, ["系统应支持导出 docx", "系统应记录操作日志", "界面应支持中文"])
    batch = _create_batch(session, w, w["items"])
    run = _new_run(session, batch)
    diagnoser = _RecordingDiagnoser(fail_call_indices=(1,))  # 第二个被处理的条目强制失败
    bus = _RecordingBus()

    fifo = [(batch, run)]
    while fifo:
        _drive_one(session, fifo, diagnoser, bus)

    statuses = sorted(r.processing_status for r in _rounds(session, batch))
    assert statuses.count("failed") == 1               # 恰一个条目 diagnosis_failed 落库
    assert "diagnosing" not in statuses                # 其余条目正常收束、无残留在飞
    events = [ev for _, ev in bus.events]
    assert EVENT_COMPLETED in events and EVENT_FAILED not in events  # 批次 run 仍 succeeded


# ---- 批次协调 kickoff：标记 started + 派发首个逐条目子 job ----

def test_kickoff_marks_started_and_enqueues_first_item(session):
    w = _seed_pending_items(session, ["系统应支持导出 docx", "系统应记录操作日志"])
    batch = _create_batch(session, w, w["items"])
    run = _new_run(session, batch)
    bus = _RecordingBus()
    enqueued: list[tuple[str, str]] = []

    _process_diagnosis_kickoff(
        session, batch, run, SqlAgentRunRepository(session), bus,
        lambda b, r: enqueued.append((b, r)),
    )

    assert enqueued == [(batch, run)]                  # 派发首个逐条目子 job
    assert (run, EVENT_STARTED) in bus.events
    assert session.get(AgentRun, uuid.UUID(run)).status == "started"
