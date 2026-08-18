"""HK-2 悬轮读侧自愈（幂等普查 G2）：诊断/图表核对悬轮对账测试义务。

覆盖（任务卡 A1–A4）：
- A1 诊断悬轮：started 超龄 run＋diagnosing 轮 → 读工作区后轮=FAILED、run=failed、
  通知落库、再发起新轮成功（`has_running_round` 守卫解锁）。
- A2 图表核对 VERIFYING 同构；run 缺失（历史脏数据）视同死。
- A3 run failed（非超龄）→ 轮次同步 FAILED；在飞未超龄轮不被误杀。
- A4 对账结构化日志字段断言；对账抛错不阻塞读主流程。
判死依据单测见 dead_run_verdict 节（run_liveness 复用 HK-1 判活，禁重写）。
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
import app.services.chart_collaboration as chart_mod
import app.services.item_review as review_mod
from app.api.schemas import (
    ChartCreateCommand,
    ChartSourceChangeCommand,
    ChartVerificationCommand,
    ItemReviewDiagnosisCommand,
    ItemizationBatchCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    AgentRun,
    ChartVerificationRound,
    IntakeRecord,
    ItemDiagnosisRequest,
    ItemDiagnosisRound,
    Material,
    MaterialParseResult,
    Notification,
    ParseRequest,
    Project,
    RequirementElement,
)
from app.domain.enums import ChartFormat, ChartType, ItemizationScopeType
from app.repositories.sqlalchemy import (
    build_sql_chart_service,
    build_sql_item_formation_service,
    build_sql_item_review_service,
)
from app.services.run_liveness import dead_run_verdict, run_liveness_deadline_seconds

RAW_TEXT = "系统应支持导出 docx。导出耗时不超过五秒。"
MERMAID_OK = "flowchart TD\n  A[导出] --> B[完成]"


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


@pytest.fixture()
def review_events(monkeypatch):
    events = []

    def _capture(component, event, msg="", level="INFO", **fields):
        events.append({"component": component, "event": event, "level": level, **fields})

    monkeypatch.setattr(review_mod, "log_event", _capture)
    return events


@pytest.fixture()
def chart_events(monkeypatch):
    events = []

    def _capture(component, event, msg="", level="INFO", **fields):
        events.append({"component": component, "event": event, "level": level, **fields})

    monkeypatch.setattr(chart_mod, "log_event", _capture)
    return events


def _seed_agent_run(session, kind, context_ref, status="started", age_seconds=0):
    """挂到承载对象的 AgentRun（SQLite CURRENT_TIMESTAMP 同口径：UTC 裸值）。"""
    run = AgentRun(
        kind=kind, status=status, context_ref=uuid.UUID(context_ref),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age_seconds),
    )
    session.add(run)
    session.commit()
    return str(run.id)


def _stale_age(lane):
    return run_liveness_deadline_seconds(lane) + 60


# ============================================================================
# 判死依据（dead_run_verdict；判活阈值复用 HK-1 run_liveness）
# ============================================================================

def test_dead_run_verdict_missing_failed_stale_alive():
    lane = "run_item_diagnosis"
    now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    deadline = run_liveness_deadline_seconds(lane)

    assert dead_run_verdict(lane, None, now=now) == "run_missing"  # run 缺失视同死
    failed = SimpleNamespace(status="failed", created_at=now)
    assert dead_run_verdict(lane, failed, now=now) == "run_failed"
    stale = SimpleNamespace(status="started", created_at=now - timedelta(seconds=deadline))
    assert dead_run_verdict(lane, stale, now=now) == "run_stale"  # 龄=阈值即判死
    alive = SimpleNamespace(status="started", created_at=now - timedelta(seconds=deadline - 1))
    assert dead_run_verdict(lane, alive, now=now) is None          # 界值内在飞：不收尸
    succeeded = SimpleNamespace(status="succeeded", created_at=now - timedelta(seconds=deadline * 2))
    assert dead_run_verdict(lane, succeeded, now=now) is None      # 正常收束不属失联对账


# ============================================================================
# 条目诊断（item_diagnosis）悬轮
# ============================================================================

def _anchor(exact: str) -> str:
    start = RAW_TEXT.find(exact)
    return json.dumps({"ranges": [{"start": start, "end": start + len(exact), "exact": exact}]})


def _seed_pending_items(session):
    """已解析结果 + 两条已确认要素 → 条目化批次形成两条待确认 LDM-007（同 test_item_review 口径）。"""
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
    for etype, content in [
        ("functional_requirement", "系统应支持导出 docx"),
        ("quality_attribute", "导出耗时不超过五秒"),
    ]:
        session.add(RequirementElement(
            project_id=p.id, parse_result_ref=parse.id, element_type=etype,
            content=content, source_anchor=_anchor(content), confidence=0.9,
            process_status="confirmed",
        ))
    session.commit()
    formation = build_sql_item_formation_service(session, auto_complete=True)
    result = formation.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=str(p.id), parse_result_ref=str(parse.id), workspace_version="1",
        scope_type=ItemizationScopeType.ALL_ELIGIBLE, target_element_refs=[],
        operator_ref="U1", idempotency_key=f"form-{uuid.uuid4()}",
    ))
    session.commit()
    from app.db.models import RequirementItem

    items = [str(i.id) for i in session.scalars(
        select(RequirementItem)
        .where(RequirementItem.project_id == p.id)
        .order_by(RequirementItem.req_no)
    ).all()]
    return {
        "project": str(p.id), "parse_result": str(parse.id),
        "formation_context": result.formation_context_ref, "items": items,
    }


def _diag_command(w, key=None):
    return ItemReviewDiagnosisCommand(
        project_ref=w["project"], item_refs=list(w["items"]), diagnosis_mode="standard",
        workspace_version="2",  # 形成批次收束后版本已推进到 2
        operator_ref="U1", idempotency_key=key or f"diag-{uuid.uuid4()}",
    )


def _stuck_diagnosis(session, w):
    """发起诊断但不执行（auto_complete=False）→ 轮次悬停 diagnosing，返回 batch_ref。"""
    svc = build_sql_item_review_service(session, auto_complete=False)
    result = svc.start_item_diagnosis(_diag_command(w))
    session.commit()
    assert result.status == "submitted"
    batch = session.scalars(select(ItemDiagnosisRequest).where(
        ItemDiagnosisRequest.project_id == uuid.UUID(w["project"])
    )).first()
    return svc, str(batch.id)


def _diagnosis_rounds(session, batch_ref):
    return session.scalars(select(ItemDiagnosisRound).where(
        ItemDiagnosisRound.batch_ref == uuid.UUID(batch_ref)
    )).all()


def test_diagnosis_stale_run_reconciled_and_guard_unlocked(session, review_events):
    """A1：started 超龄僵尸 run → 读工作区后轮=FAILED、run=failed、通知落库、守卫解锁。"""
    w = _seed_pending_items(session)
    svc, batch_ref = _stuck_diagnosis(session, w)
    run_id = _seed_agent_run(session, "item_diagnosis", batch_ref,
                             status="started", age_seconds=_stale_age("run_item_diagnosis"))

    blocked = svc.start_item_diagnosis(_diag_command(w))
    assert blocked.status == "rejected_precheck" and "诊断中" in blocked.next_action  # 对账前守卫锁死

    svc.read_item_review_workspace(w["formation_context"])
    session.commit()

    rounds = _diagnosis_rounds(session, batch_ref)
    assert rounds and all(r.processing_status == "failed" for r in rounds)  # 悬轮收尸
    assert all("已自动对账" in (r.reason or "") for r in rounds)            # 归因脱敏文案
    assert session.get(AgentRun, uuid.UUID(run_id)).status == "failed"      # 僵尸 run 判死落终态
    note = session.scalar(select(Notification).where(
        Notification.dedup_key == f"agent_run.failed:{run_id}"
    ))
    assert note is not None and "条目评审诊断" in note.title                 # 既有失败通知口径，防静默

    retry = svc.start_item_diagnosis(_diag_command(w))
    session.commit()
    assert retry.status == "submitted"  # has_running_round 守卫解锁，可发新轮


def test_diagnosis_missing_run_treated_as_dead(session):
    """A2（诊断侧）：run 缺失（历史脏数据）视同死 → 轮次收尸＋失联通知。"""
    w = _seed_pending_items(session)
    svc, batch_ref = _stuck_diagnosis(session, w)  # 同步装配不建 DB run：天然 run 缺失

    svc.read_item_review_workspace(w["formation_context"])
    session.commit()

    assert all(r.processing_status == "failed" for r in _diagnosis_rounds(session, batch_ref))
    note = session.scalar(select(Notification).where(
        Notification.dedup_key == f"agent_run.lost:{batch_ref}"
    ))
    assert note is not None and "失联" in note.title


def test_diagnosis_failed_run_syncs_rounds_without_new_notification(session):
    """A3：run failed（job 级强杀，非超龄）→ 轮次同步 FAILED；不再补失联通知（失败时已通知）。"""
    w = _seed_pending_items(session)
    svc, batch_ref = _stuck_diagnosis(session, w)
    _seed_agent_run(session, "item_diagnosis", batch_ref, status="failed", age_seconds=0)

    svc.read_item_review_workspace(w["formation_context"])
    session.commit()

    assert all(r.processing_status == "failed" for r in _diagnosis_rounds(session, batch_ref))
    lost = session.scalar(select(Notification).where(
        Notification.dedup_key == f"agent_run.lost:{batch_ref}"
    ))
    assert lost is None  # run_failed 分支不补发（mark_failed 时已通知）


def test_diagnosis_alive_round_not_killed(session):
    """A3：在飞未超龄（started 新鲜 run）→ 读工作区不误杀，守卫仍拦截。"""
    w = _seed_pending_items(session)
    svc, batch_ref = _stuck_diagnosis(session, w)
    run_id = _seed_agent_run(session, "item_diagnosis", batch_ref, status="started", age_seconds=30)

    svc.read_item_review_workspace(w["formation_context"])
    session.commit()

    assert all(r.processing_status == "diagnosing" for r in _diagnosis_rounds(session, batch_ref))
    assert session.get(AgentRun, uuid.UUID(run_id)).status == "started"  # run 原样
    blocked = svc.start_item_diagnosis(_diag_command(w))
    assert blocked.status == "rejected_precheck"  # 守卫仍在保护在飞批次


def test_diagnosis_reconcile_log_fields_and_error_isolation(session, review_events, monkeypatch):
    """A4：对账结构化日志（round_ref/batch_ref/run_id/verdict）；对账抛错不阻塞读主流程。"""
    w = _seed_pending_items(session)
    svc, batch_ref = _stuck_diagnosis(session, w)
    run_id = _seed_agent_run(session, "item_diagnosis", batch_ref,
                             status="started", age_seconds=_stale_age("run_item_diagnosis"))

    svc.read_item_review_workspace(w["formation_context"])
    logs = [e for e in review_events if e["event"] == "review.diagnosis.round_reconciled"]
    assert len(logs) == len(_diagnosis_rounds(session, batch_ref))
    for e in logs:
        assert e["level"] == "WARN" and e["component"] == "item-review"
        assert e["batch_ref"] == batch_ref and e["run_id"] == run_id
        assert e["verdict"] == "run_stale" and e["round_ref"]

    # 注入异常：对账崩溃只记 WARN，读主流程不受影响
    w2 = _seed_pending_items(session)
    svc2, batch2 = _stuck_diagnosis(session, w2)

    def _boom(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(review_mod, "dead_run_verdict", _boom)
    workspace = svc2.read_item_review_workspace(w2["formation_context"])
    assert workspace.review_items  # 读成功
    assert all(r.processing_status == "diagnosing" for r in _diagnosis_rounds(session, batch2))
    errors = [e for e in review_events if e["event"] == "review.diagnosis.reconcile_error"]
    assert errors and errors[0]["level"] == "WARN" and errors[0]["error_code"] == "RuntimeError"


# ============================================================================
# 图表核对（chart_verification）VERIFYING 悬轮（同构）
# ============================================================================

def _seed_chart_items(session):
    from app.db.models import RequirementItem

    p = Project(name="demo-chart")
    session.add(p)
    session.flush()
    refs = []
    for idx, expr in enumerate(["系统应支持导出 docx", "导出耗时不超过五秒"], start=1):
        it = RequirementItem(
            project_id=p.id, parse_result_ref=uuid.uuid4(),
            formation_context_ref=uuid.uuid4(), req_no=f"REQ-00{idx}",
            expression=expr, req_type="functional", status="confirmed",
            source_element_refs="[]",
        )
        session.add(it)
        session.flush()
        refs.append(str(it.id))
    session.commit()
    return {"project": str(p.id), "i1": refs[0], "i2": refs[1]}


def _stuck_verification(session, w):
    """建图→源码→发起核对但不执行（auto_complete=False）→ 轮次悬停 VERIFYING。"""
    svc = build_sql_chart_service(session, auto_complete=False)
    created = svc.create_chart(ChartCreateCommand(
        project_ref=w["project"], title="导出流程图", chart_type=ChartType.FLOWCHART,
        format=ChartFormat.MERMAID, source_refs=[w["i1"], w["i2"]],
        operator_ref="U1", idempotency_key=f"C-{uuid.uuid4()}",
    ))
    chart_ref = created.chart_ref
    ws = svc.read_chart_workspace(chart_ref)
    svc.apply_source_change(chart_ref, ChartSourceChangeCommand(
        project_ref=w["project"], source_code=MERMAID_OK, format=ChartFormat.MERMAID,
        chart_type=ChartType.FLOWCHART, source_refs=[w["i1"], w["i2"]],
        expected_draft_version=ws.draft_version, operator_ref="U1",
        idempotency_key=f"S-{uuid.uuid4()}",
    ))
    result = svc.start_chart_verification(chart_ref, ChartVerificationCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=f"V-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "submitted"
    round_ = session.scalars(select(ChartVerificationRound)).first()
    assert round_.processing_status == "verifying"
    return svc, chart_ref, str(result.request_ref), str(round_.id)


def _reverify(svc, w, chart_ref):
    return svc.start_chart_verification(chart_ref, ChartVerificationCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=f"V-{uuid.uuid4()}",
    ))


def test_chart_stale_run_reconciled_and_reverify_unlocked(session, chart_events):
    """A2：VERIFYING 悬轮＋超龄 run → 轮=FAILED、run=failed、通知落库、可重新核对。"""
    w = _seed_chart_items(session)
    svc, chart_ref, request_ref, round_ref = _stuck_verification(session, w)
    run_id = _seed_agent_run(session, "chart_verification", request_ref,
                             status="queued", age_seconds=_stale_age("run_chart_verification"))

    blocked = _reverify(svc, w, chart_ref)
    assert blocked.status == "rejected_precheck" and "进行中" in blocked.next_action  # 对账前锁死

    ws = svc.read_chart_workspace(chart_ref)
    session.commit()

    round_ = session.get(ChartVerificationRound, uuid.UUID(round_ref))
    assert round_.processing_status == "failed"
    assert "已自动对账" in round_.reason and "不得降级为纯人工确认" in round_.reason
    assert session.get(AgentRun, uuid.UUID(run_id)).status == "failed"
    note = session.scalar(select(Notification).where(
        Notification.dedup_key == f"agent_run.failed:{run_id}"
    ))
    assert note is not None and "图文一致性核对" in note.title
    start_fact = next(a for a in ws.available_actions if a.key == "start_verification")
    assert start_fact.enabled  # can_reverify 解锁（读视图即时可见）

    retry = _reverify(svc, w, chart_ref)
    session.commit()
    assert retry.status == "submitted"  # 单飞守卫解锁，可发新轮

    logs = [e for e in chart_events if e["event"] == "chart.verification.round_reconciled"]
    assert len(logs) == 1 and logs[0]["level"] == "WARN"
    assert logs[0]["round_ref"] == round_ref and logs[0]["chart_ref"] == chart_ref
    assert logs[0]["request_ref"] == request_ref and logs[0]["run_id"] == run_id
    assert logs[0]["verdict"] == "run_stale"


def test_chart_missing_run_treated_as_dead(session):
    """A2：run 缺失视同死（同步装配不建 DB run）→ 轮次收尸＋失联通知。"""
    w = _seed_chart_items(session)
    svc, chart_ref, request_ref, round_ref = _stuck_verification(session, w)

    svc.read_chart_workspace(chart_ref)
    session.commit()

    round_ = session.get(ChartVerificationRound, uuid.UUID(round_ref))
    assert round_.processing_status == "failed"
    note = session.scalar(select(Notification).where(
        Notification.dedup_key == f"agent_run.lost:{round_ref}"
    ))
    assert note is not None and "失联" in note.title


def test_chart_failed_run_syncs_round(session):
    """A3：run failed（非超龄）→ 轮次同步 FAILED（不误报失联）。"""
    w = _seed_chart_items(session)
    svc, chart_ref, request_ref, round_ref = _stuck_verification(session, w)
    _seed_agent_run(session, "chart_verification", request_ref, status="failed", age_seconds=0)

    svc.read_chart_workspace(chart_ref)
    session.commit()

    assert session.get(ChartVerificationRound, uuid.UUID(round_ref)).processing_status == "failed"
    lost = session.scalar(select(Notification).where(
        Notification.dedup_key == f"agent_run.lost:{round_ref}"
    ))
    assert lost is None


def test_chart_alive_round_not_killed_and_error_isolated(session, chart_events, monkeypatch):
    """A3/A4：在飞未超龄不误杀；对账抛错不阻塞读主流程。"""
    w = _seed_chart_items(session)
    svc, chart_ref, request_ref, round_ref = _stuck_verification(session, w)
    run_id = _seed_agent_run(session, "chart_verification", request_ref,
                             status="started", age_seconds=30)

    ws = svc.read_chart_workspace(chart_ref)
    assert session.get(ChartVerificationRound, uuid.UUID(round_ref)).processing_status == "verifying"
    assert session.get(AgentRun, uuid.UUID(run_id)).status == "started"  # run 原样
    assert _reverify(svc, w, chart_ref).status == "rejected_precheck"    # 守卫仍拦截

    def _boom(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(chart_mod, "dead_run_verdict", _boom)
    ws = svc.read_chart_workspace(chart_ref)  # 注入异常：读主流程不受影响
    assert ws.chart_ref == chart_ref
    errors = [e for e in chart_events if e["event"] == "chart.verification.reconcile_error"]
    assert errors and errors[0]["level"] == "WARN" and errors[0]["error_code"] == "RuntimeError"
