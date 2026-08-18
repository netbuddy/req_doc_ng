"""条目评审服务（AEP-032/033/034/095 + 确认/终止）—— SCN-003 v5 结论裁决测试义务。

设计事实源：docs/40 domains/DS-001（state-machines/需求条目.md / data.md LDM-009 /
interfaces/条目评审服务.md）、docs/40 slices/SCN-003-P01/页面详细设计.md（v5）。
覆盖：状态机迁移/默认拒绝 · P01 参数/版本/准入/幂等 · 结论承接与聚合守卫
· AEP-034 裁决（拒绝理由必填 / 采纳四种副作用链 / 幂等）· 修订链式自动增量
· AEP-095 对话三出口（草案迭代/解释/重评改判）· 覆盖确认 / 人工撤回 · 派生显示态。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.api.schemas import (
    ItemConfirmationCommand,
    ItemReviewDiagnosisCommand,
    ItemRevisionCommand,
    ItemWithdrawCommand,
    ItemizationBatchCommand,
    ReviewDialogueCommand,
    VerdictAdjudicationCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    IntakeRecord,
    ItemDiagnosisRound,
    ItemReviewFinding,
    Material,
    MaterialParseResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
)
from app.domain.enums import (
    DiagnosisTrigger,
    DialogueOutcomeType,
    ItemRevisionMode,
    ItemizationScopeType,
    RequirementItemStatus,
    ReviewDisplayCode as RDC,
    ReviewItemStatus as RIS,
    VerdictDecision,
    VerdictKind,
)
from app.domain.errors import InvalidInput, RejectedTransition
from app.domain.state_machine import ItemEvent, ItemState, item_transition
from app.repositories.sqlalchemy import (
    build_sql_item_formation_service,
    build_sql_item_review_service,
    build_sql_requirement_item_service,
)

RAW_TEXT = "系统应支持导出 docx。导出耗时不超过五秒。"


# ============================================================================
# 状态机单元测试（迁移表是事实源；未列出默认拒绝）
# ============================================================================

def test_item_state_machine_confirm_and_terminate_from_pending():
    assert item_transition(ItemState.PENDING_CONFIRMATION, ItemEvent.CONFIRM) is ItemState.CONFIRMED
    assert item_transition(ItemState.PENDING_CONFIRMATION, ItemEvent.TERMINATE) is ItemState.TERMINATED


@pytest.mark.parametrize("state", [ItemState.CONFIRMED, ItemState.SUPERSEDED, ItemState.TERMINATED])
def test_item_state_machine_default_rejects_confirm_on_non_pending(state):
    with pytest.raises(RejectedTransition):
        item_transition(state, ItemEvent.CONFIRM)


def test_item_state_machine_default_rejects_terminate_on_confirmed():
    with pytest.raises(RejectedTransition):
        item_transition(ItemState.CONFIRMED, ItemEvent.TERMINATE)


# ============================================================================
# 持久化集成测试（SQLite create_all；Stub 诊断器确定性规则）
# ============================================================================

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


def _anchor(exact: str) -> str:
    start = RAW_TEXT.find(exact)
    return json.dumps({"ranges": [{"start": start, "end": start + len(exact), "exact": exact}]})


def _seed_pending_items(session):
    """已接入材料 + 已解析结果 + 两条已确认要素 → 条目化批次形成两条待确认 LDM-007。"""
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
    items = session.scalars(select(RequirementItem).order_by(RequirementItem.req_no)).all()
    assert len(items) == 2
    return {
        "project": str(p.id), "parse_context": str(ctx.id), "parse_result": str(parse.id),
        "formation_context": result.formation_context_ref,
        "items": [str(i.id) for i in items],
    }


def _version(session, w) -> str:
    ctx = session.get(ParseRequest, uuid.UUID(w["parse_context"]))
    return str(ctx.workspace_version)


def _diag_command(session, w, item_refs=None, mode="standard", key=None, version=None):
    return ItemReviewDiagnosisCommand(
        project_ref=w["project"],
        item_refs=item_refs if item_refs is not None else list(w["items"]),
        diagnosis_mode=mode,
        workspace_version=version or _version(session, w),
        operator_ref="U1",
        idempotency_key=key or f"diag-{uuid.uuid4()}",
    )


def _run_diagnosis(session, w, diagnoser=None, **kwargs):
    svc = build_sql_item_review_service(session, auto_complete=True, item_diagnoser=diagnoser)
    result = svc.start_item_diagnosis(_diag_command(session, w, **kwargs))
    session.commit()
    return svc, result


def _item_view(workspace, item_ref):
    return next(i for i in workspace.review_items if i.item_ref == item_ref)


def _workspace(svc, w):
    return svc.read_item_review_workspace(w["formation_context"])


def _adjudicate(svc, session, w, item_ref, round_ref, decision, selected=None, reason=None, key=None):
    workspace = svc.adjudicate_verdict(VerdictAdjudicationCommand(
        project_ref=w["project"], item_ref=item_ref, round_ref=round_ref,
        decision=decision, selected_point_refs=selected, reason=reason,
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=key or f"adj-{uuid.uuid4()}",
    ))
    session.commit()
    return workspace


def _set_expression(session, item_ref: str, expression: str):
    item = session.get(RequirementItem, uuid.UUID(item_ref))
    item.expression = expression
    session.commit()


# ---- P01 参数校验 / 版本冲突 / 准入 / 幂等 ----

def test_diagnosis_invalid_mode_rejected_before_batch(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session)
    result = svc.start_item_diagnosis(_diag_command(session, w, mode="deep"))
    assert result.status == "rejected_precheck"
    assert session.scalars(select(ItemDiagnosisRound)).all() == []


def test_diagnosis_empty_selection_rejected(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session)
    result = svc.start_item_diagnosis(_diag_command(session, w, item_refs=[]))
    assert result.status == "rejected_precheck"


def test_diagnosis_version_conflict_rejected(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session)
    result = svc.start_item_diagnosis(_diag_command(session, w, version="99"))
    assert result.status == "rejected_precheck"
    assert "版本" in result.next_action


def test_diagnosis_rejects_non_pending_item(session):
    w = _seed_pending_items(session)
    item = session.get(RequirementItem, uuid.UUID(w["items"][0]))
    item.status = RequirementItemStatus.CONFIRMED.value
    session.commit()
    svc = build_sql_item_review_service(session)
    result = svc.start_item_diagnosis(_diag_command(session, w))
    assert result.status == "rejected_precheck"


def test_diagnosis_idempotent_replay_no_duplicate_batch(session):
    w = _seed_pending_items(session)
    key = f"diag-{uuid.uuid4()}"
    svc, first = _run_diagnosis(session, w, key=key)
    replay = svc.start_item_diagnosis(_diag_command(session, w, key=key))
    assert first.status == replay.status == "submitted"
    rounds = session.scalars(select(ItemDiagnosisRound)).all()
    assert len(rounds) == 2  # 两条目各一轮，无重复批次


# ---- 结论承接：结论铸造 + 聚合守卫 ----

def test_diagnosis_mints_verdict_with_points_and_evidence(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w)
    workspace = _workspace(svc, w)
    view = _item_view(workspace, w["items"][0])
    assert view.review_status is RIS.AWAITING_ADJUDICATION
    verdict = view.current_verdict
    assert verdict is not None and verdict.effective
    assert verdict.verdict_kind is VerdictKind.REVISE
    assert len(verdict.revision_points) == 1
    assert verdict.revision_points[0].point_ref == "P1"
    assert len(verdict.findings) == 2  # 证据行，无人工复核字段
    assert verdict.trigger is DiagnosisTrigger.USER_SUBMIT


def test_pass_verdict_when_expression_testable(session):
    w = _seed_pending_items(session)
    _set_expression(session, w["items"][0], "系统应支持导出 docx，并明确验收观察口径。")
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.current_verdict.verdict_kind is VerdictKind.PASS
    assert view.current_verdict.revision_points == []


def test_verdict_guard_rejects_inconsistent_output(session):
    """聚合守卫：模型给出 pass 但携带修订点 → 整轮失败，不伪造结论。"""
    class BadDiagnoser:
        def diagnose(self, project_ref, diagnosis_mode, item, sources, raw_text,
                     revisions, prior_findings, excluded_points=None, thread_context="", business_sources=None, attestation=None):
            from app.adapters.llm import DiagnosedFinding, ItemVerdictOutcome
            return ItemVerdictOutcome(
                verdict_kind="pass", verdict_summary="矛盾输出",
                findings=(DiagnosedFinding("no_blocker", "ok", "b"),),
                revision_points=({"point_ref": "P1", "label": "x", "finding_index": 0,
                                  "find": item["expression"], "replace": item["expression"] + "改"},),
                supplement_gaps=(), basis="bad",
            )

    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, diagnoser=BadDiagnoser(), item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.review_status is RIS.NO_VERDICT
    round_ = session.scalars(select(ItemDiagnosisRound)).one()
    assert round_.processing_status == "failed"
    assert "守卫" in round_.reason


def test_diagnosis_failure_stage_lands_in_ldm015_and_reason_is_plain(session):
    """分关落账（T20260712 A4）：失败体写 failure.stage+detail，讨论区文案白话化。"""
    from app.adapters.llm import StubRequirementItemDiagnoser
    from app.db.models import ModelResult

    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(
        session, w, diagnoser=StubRequirementItemDiagnoser(failed=True),
        item_refs=[w["items"][0]],
    )
    round_ = session.scalars(select(ItemDiagnosisRound)).one()
    assert round_.processing_status == "failed"
    assert "AI 诊断未完成（模型服务调用失败）" in round_.reason
    assert "不伪造结论" in round_.reason
    result = session.scalars(
        select(ModelResult).where(ModelResult.stage == "item_diagnosis")
    ).one()
    body = json.loads(result.result_content)
    assert body["failure"]["stage"] == "llm_error"
    assert body["failure"]["detail"]  # 白话原因，事后可定位摔在哪一关
    assert "verdict" not in body  # 不再落全空结论骨架


# ---- AEP-034 结论裁决 ----

def test_reject_verdict_requires_reason(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w)
    view = _item_view(_workspace(svc, w), w["items"][0])
    with pytest.raises(InvalidInput):
        _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                    VerdictDecision.REJECTED)


def test_reject_verdict_voids_it(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w)
    view = _item_view(_workspace(svc, w), w["items"][0])
    workspace = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                            VerdictDecision.REJECTED, reason="判定依据不足")
    after = _item_view(workspace, view.item_ref)
    assert after.review_status is RIS.NO_VERDICT
    assert after.current_verdict is None
    assert after.verdict_history[0].adjudication.decision is VerdictDecision.REJECTED
    assert after.verdict_history[0].adjudication.reason == "判定依据不足"


def test_duplicate_adjudication_rejected(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w)
    view = _item_view(_workspace(svc, w), w["items"][0])
    round_ref = view.current_verdict.round_ref
    _adjudicate(svc, session, w, view.item_ref, round_ref,
                VerdictDecision.REJECTED, reason="r1")
    with pytest.raises(RejectedTransition):
        _adjudicate(svc, session, w, view.item_ref, round_ref,
                    VerdictDecision.REJECTED, reason="r2")


def test_adjudication_idempotent_replay(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w)
    view = _item_view(_workspace(svc, w), w["items"][0])
    key = f"adj-{uuid.uuid4()}"
    _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                VerdictDecision.REJECTED, reason="r", key=key)
    replay = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                         VerdictDecision.REJECTED, reason="r", key=key)
    assert _item_view(replay, view.item_ref).review_status is RIS.NO_VERDICT


def test_adjudication_version_conflict_rejected(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w)
    view = _item_view(_workspace(svc, w), w["items"][0])
    with pytest.raises(RejectedTransition):
        svc.adjudicate_verdict(VerdictAdjudicationCommand(
            project_ref=w["project"], item_ref=view.item_ref,
            round_ref=view.current_verdict.round_ref,
            decision=VerdictDecision.ADOPTED, workspace_version="99",
            operator_ref="U1", idempotency_key=f"adj-{uuid.uuid4()}",
        ))


def test_adopt_pass_confirms_in_one_step(session):
    """采纳「建议通过」= 确认（无第二步；准入内联）。"""
    w = _seed_pending_items(session)
    _set_expression(session, w["items"][0], "系统应支持导出 docx，并明确验收观察口径。")
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.current_verdict.verdict_kind is VerdictKind.PASS
    workspace = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                            VerdictDecision.ADOPTED)
    after = _item_view(workspace, view.item_ref)
    assert after.review_status is RIS.CONFIRMED
    item = session.get(RequirementItem, uuid.UUID(view.item_ref))
    assert item.status == RequirementItemStatus.CONFIRMED.value
    round_ = session.get(ItemDiagnosisRound, uuid.UUID(view.current_verdict.round_ref))
    assert round_.confirm_result == "confirmed"
    assert round_.adjudication_decision == VerdictDecision.ADOPTED.value


def test_adopt_revise_applies_points_and_chains_incremental(session):
    """采纳「建议修订」：应用所选点 → 旧结论失效 → 自动增量诊断 → 新结论（stub=通过）。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    verdict = view.current_verdict
    assert verdict.verdict_kind is VerdictKind.REVISE
    workspace = _adjudicate(svc, session, w, view.item_ref, verdict.round_ref,
                            VerdictDecision.ADOPTED)  # selected=None → 全选
    after = _item_view(workspace, view.item_ref)
    item = session.get(RequirementItem, uuid.UUID(view.item_ref))
    assert "验收观察口径" in item.expression  # 修订点已应用
    # 修订记录带所选点出处
    assert after.revision_records[0].selected_point_refs == ["P1"]
    # 链式增量已收束并铸出新结论（stub：修订后表达含口径 → 通过）
    assert after.review_status is RIS.AWAITING_ADJUDICATION
    assert after.current_verdict.verdict_kind is VerdictKind.PASS
    assert after.current_verdict.trigger is DiagnosisTrigger.REVISION_CHAINED
    # 旧轮次失效留痕
    old = next(v for v in after.verdict_history if v.round_ref == verdict.round_ref)
    assert old.invalidated


def test_adopt_revise_atomic_when_adoption_stats_fails(session, monkeypatch):
    """A2（issue #12 K_dispatch）：_record_verdict_adoption 故障 → 裁决+修订+stats 整体回滚，
    条目可干净重试成功，不被「adjudication_decision 已 ADOPTED」弹回。

    修复前：链式派发 commit_each 先落库裁决+修订，其后 stats 行故障=永久缺行、retry 死锁；
    修复后：stats 行前移到派发前，与裁决+修订同一未提交事务，故障即整体回滚。
    """
    from app.db.models import AdoptionRecord
    from app.repositories.sqlalchemy import SqlModelResultRepository

    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    verdict = view.current_verdict
    assert verdict.verdict_kind is VerdictKind.REVISE
    before_expr = session.get(RequirementItem, uuid.UUID(view.item_ref)).expression

    real = SqlModelResultRepository.record_adoption
    armed = {"on": True}

    def _maybe_boom(self, **kwargs):
        # 采纳结局 stats 行（review_round）故障注入——命中 _record_verdict_adoption
        if armed["on"] and kwargs.get("subject_type") == "review_round":
            raise RuntimeError("adoption stats 写入故障（注入）")
        return real(self, **kwargs)

    monkeypatch.setattr(SqlModelResultRepository, "record_adoption", _maybe_boom)

    key = f"adj-{uuid.uuid4()}"
    with pytest.raises(RuntimeError):
        svc.adjudicate_verdict(VerdictAdjudicationCommand(
            project_ref=w["project"], item_ref=view.item_ref, round_ref=verdict.round_ref,
            decision=VerdictDecision.ADOPTED, selected_point_refs=None, reason=None,
            workspace_version=_version(session, w), operator_ref="U1", idempotency_key=key,
        ))
    session.rollback()  # 请求层回滚（get_item_review_service 的 except 分支）

    # 整体回滚：裁决未持久（decision 仍 None）、表达未改、无采纳明细行
    round_row = session.get(ItemDiagnosisRound, uuid.UUID(verdict.round_ref))
    assert round_row.adjudication_decision is None
    assert session.get(RequirementItem, uuid.UUID(view.item_ref)).expression == before_expr
    assert session.scalars(
        select(AdoptionRecord).where(AdoptionRecord.subject_ref == uuid.UUID(verdict.round_ref))
    ).all() == []

    # 解除故障，同 key 重试成功：不被 ADOPTED 弹回，修订点应用 + stats 行齐备
    armed["on"] = False
    workspace = svc.adjudicate_verdict(VerdictAdjudicationCommand(
        project_ref=w["project"], item_ref=view.item_ref, round_ref=verdict.round_ref,
        decision=VerdictDecision.ADOPTED, selected_point_refs=None, reason=None,
        workspace_version=_version(session, w), operator_ref="U1", idempotency_key=key,
    ))
    session.commit()
    item = session.get(RequirementItem, uuid.UUID(view.item_ref))
    assert "验收观察口径" in item.expression  # 修订点已应用
    stats = session.scalars(
        select(AdoptionRecord).where(AdoptionRecord.subject_ref == uuid.UUID(verdict.round_ref))
    ).all()
    assert any(r.outcome.startswith("adopted") for r in stats)  # 采纳明细齐备
    _ = workspace


def test_adopt_revise_with_excluded_points_recorded(session):
    """分点选择：排除点留痕（重评上下文），采纳结局=修订采纳。"""
    class TwoPointDiagnoser:
        def diagnose(self, project_ref, diagnosis_mode, item, sources, raw_text,
                     revisions, prior_findings, excluded_points=None, thread_context="", business_sources=None, attestation=None):
            from app.adapters.llm import DiagnosedFinding, ItemVerdictOutcome
            expr = str(item["expression"])
            if excluded_points or "验收观察口径" in expr:
                return ItemVerdictOutcome(
                    verdict_kind="pass", verdict_summary="尊重排除，通过。",
                    findings=(DiagnosedFinding("no_blocker", "ok", "b"),),
                    revision_points=(), supplement_gaps=(), basis="stub",
                )
            head, tail = expr[:6], expr[6:]
            return ItemVerdictOutcome(
                verdict_kind="revise", verdict_summary="两点修订。",
                findings=(DiagnosedFinding("untestable", "缺口径", "b"),),
                revision_points=(
                    {"point_ref": "P1", "label": "点一", "finding_index": 0,
                     "find": head, "replace": head + "（一）", "basis": "", "group": None},
                    {"point_ref": "P2", "label": "点二", "finding_index": 0,
                     "find": tail, "replace": tail + "（二）", "basis": "", "group": None},
                ),
                supplement_gaps=(), basis="stub",
            )

    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, diagnoser=TwoPointDiagnoser(), item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert len(view.current_verdict.revision_points) == 2
    workspace = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                            VerdictDecision.ADOPTED, selected=["P1"])
    after = _item_view(workspace, view.item_ref)
    item = session.get(RequirementItem, uuid.UUID(view.item_ref))
    assert "（一）" in item.expression and "（二）" not in item.expression
    old = next(v for v in after.verdict_history if v.round_ref == view.current_verdict.round_ref)
    assert old.adjudication.selected_point_refs == ["P1"]
    assert old.adjudication.excluded_point_refs == ["P2"]


def test_adopt_withdraw_terminates_item(session):
    w = _seed_pending_items(session)
    _set_expression(session, w["items"][0], "重复条目：系统应支持导出 docx（应撤回）")
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.current_verdict.verdict_kind is VerdictKind.WITHDRAW
    workspace = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                            VerdictDecision.ADOPTED)
    assert _item_view(workspace, view.item_ref).review_status is RIS.TERMINATED
    item = session.get(RequirementItem, uuid.UUID(view.item_ref))
    assert item.status == RequirementItemStatus.TERMINATED.value


def test_adopt_supplement_registers_gap_and_blocks_diagnosis(session):
    w = _seed_pending_items(session)
    _set_expression(session, w["items"][0], "系统应支持导出 docx（缺来源：口径出处不明）")
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.current_verdict.verdict_kind is VerdictKind.SUPPLEMENT
    workspace = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                            VerdictDecision.ADOPTED)
    after = _item_view(workspace, view.item_ref)
    assert after.review_status is RIS.NO_VERDICT
    assert after.supplement_gaps_open  # 缺口未闭合
    # 缺口未闭合阻断用户再诊断
    result = svc.start_item_diagnosis(_diag_command(session, w, item_refs=[view.item_ref]))
    assert result.status == "rejected_precheck"
    assert "缺口" in result.next_action


def _batch_element_refs(session, w) -> list[str]:
    """本解析批次全部要素 id（升序），供来源登记用例构造合法来源集。"""
    rows = session.scalars(select(RequirementElement).where(
        RequirementElement.parse_result_ref == uuid.UUID(w["parse_result"]))).all()
    return sorted(str(r.id) for r in rows)


def test_source_registration_closes_supplement_gap(session):
    """A3（本卡核心）：对已采纳「建议补充来源」的条目登记来源后，四点串联——
    旧诊断轮失效、来源缺口判定为空、派生显示态离开「待补充来源」、start_item_diagnosis 前置放行。

    issue #30：「登记来源」＝一次内容修订，复用既有失效链闭合缺口，不改 item_review.py。
    """
    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    _set_expression(session, item_ref, "系统应支持导出 docx（缺来源：口径出处不明）")
    svc, _ = _run_diagnosis(session, w, item_refs=[item_ref])
    view = _item_view(_workspace(svc, w), item_ref)
    assert view.current_verdict.verdict_kind is VerdictKind.SUPPLEMENT
    round_ref = view.current_verdict.round_ref
    _adjudicate(svc, session, w, item_ref, round_ref, VerdictDecision.ADOPTED)
    blocked = _item_view(_workspace(svc, w), item_ref)
    assert blocked.supplement_gaps_open and blocked.display_code is RDC.SUPPLEMENT_PENDING  # 前置：待补充来源

    # 登记来源＝一次内容修订（本批次已确认要素集，与原来源不同 → 内容变更）
    item_service = build_sql_requirement_item_service(session)
    result = item_service.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=_version(session, w),
        revision_mode=ItemRevisionMode.MANUAL, field_key="source_element_refs",
        revised_value=json.dumps(_batch_element_refs(session, w)),
        operator_ref="U1", idempotency_key=f"src-{uuid.uuid4()}"))
    session.commit()
    assert result.status == "applied"

    # ① 旧诊断轮失效
    assert session.get(ItemDiagnosisRound, uuid.UUID(round_ref)).invalidated is True
    review = build_sql_item_review_service(session)
    after = _item_view(_workspace(review, w), item_ref)
    # ② 来源缺口判定为空　③ 派生显示态离开「待补充来源」
    assert not after.supplement_gaps_open
    assert after.display_code is not RDC.SUPPLEMENT_PENDING
    # ④ start_item_diagnosis 前置放行（同 USER_SUBMIT 触发，不再 rejected_precheck）
    result2 = review.start_item_diagnosis(_diag_command(session, w, item_refs=[item_ref]))
    assert result2.status == "submitted"


def test_source_registration_matches_expression_revision_behavior(session):
    """A4：登记来源触发的失效行为与修订表达一致——同为内容变更路径，
    旧结论随版本失效、直发不链式复诊（与表达修订对照）。"""
    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    _run_diagnosis(session, w, item_refs=[item_ref])  # 先产生一轮结论
    item_service = build_sql_requirement_item_service(session)
    result = item_service.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=_version(session, w),
        revision_mode=ItemRevisionMode.MANUAL, field_key="source_element_refs",
        revised_value=json.dumps(_batch_element_refs(session, w)),
        operator_ref="U1", idempotency_key=f"src-{uuid.uuid4()}"))
    session.commit()
    assert result.status == "applied"
    assert "旧结论随版本失效" in result.next_action  # 与表达修订同口径回执
    assert result.agent_run_ref is None  # 直发不链式复诊（同 expression 直发）
    review = build_sql_item_review_service(session)
    view = _item_view(_workspace(review, w), item_ref)
    assert view.review_status is RIS.NO_VERDICT  # 旧结论失效、无新轮 → 无结论


# ---- 修订链式自动增量（人工修订入口）----

def test_manual_revision_invalidates_but_does_not_chain(session):
    """阶段策略解耦 P1：AEP-036 直发内容修订失效旧结论，但**不**自动链式复诊。

    链式复诊迁回评审裁决采纳动作（_adopt_revise）；直发路径只写事实、发布 ItemRevised 事件。
    修订后旧结论随版本失效、无新轮次 → 条目回到无结论态，待用户显式发起诊断。
    """
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    item_service = build_sql_requirement_item_service(session)
    result = item_service.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        workspace_version=_version(session, w),
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value="系统应支持导出 docx，并明确验收观察口径。",
        operator_ref="U1", idempotency_key=f"rev-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "applied"
    assert result.agent_run_ref is None  # 直发不再链式复诊
    review = build_sql_item_review_service(session)
    view = _item_view(_workspace(review, w), w["items"][0])
    assert view.review_status is RIS.NO_VERDICT  # 旧结论失效、无新轮 → 无结论
    assert view.current_verdict is None


# ---- AEP-095 对话三出口 ----

def test_dialogue_draft_and_iterate_and_adopt(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    r1 = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="把当前条目的表达修订为：导出结果归档后 72 小时内可恢复",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    assert r1.outcome_type is DialogueOutcomeType.DRAFT
    assert r1.draft.in_flight and r1.draft.draft_seq == 1
    assert "72 小时" in r1.draft.draft_value
    # 迭代：原位第 2 稿，旧稿候选过期
    r2 = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="再改成：归档后 48 小时内可恢复",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    assert r2.draft.draft_seq == 2
    view = _item_view(_workspace(svc, w), w["items"][0])
    drafts = [m for m in view.dialogue_messages if m.kind is DialogueOutcomeType.DRAFT]
    assert len(drafts) == 2
    assert [d.in_flight for d in drafts] == [False, True]
    # 采纳草案（AEP-036 accept_suggestion）：阶段策略解耦 P1 后直发采纳草案不再链式增量
    # （链式复诊只属评审裁决采纳动作），只写修订、失效旧结论。
    item_service = build_sql_requirement_item_service(session)
    result = item_service.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        workspace_version=_version(session, w),
        revision_mode=ItemRevisionMode.ACCEPT_SUGGESTION, field_key="expression",
        suggestion_ref=r2.draft.suggestion_ref,
        operator_ref="U1", idempotency_key=f"rev-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "applied" and result.agent_run_ref is None
    item = session.get(RequirementItem, uuid.UUID(w["items"][0]))
    assert "48 小时" in item.expression


def test_dialogue_draft_cannot_comply_returns_reason_not_empty_draft(session):
    """拒绝通道：composer 返回 cannot_comply（空稿+reason）→ 解释出口回显原因，不落草案。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])

    class _RefusingComposer:
        def compose(self, item, sources, intent, current_draft, structure_context=None):
            from app.adapters.llm import DraftOutcome
            return DraftOutcome(proposed_value="", note="", reason="该意图与本条目表达无关，无法起草")

    svc._draft_composer = _RefusingComposer()
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="修订为：讲一个与本条目无关的故事",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    assert r.outcome_type is DialogueOutcomeType.EXPLANATION
    assert "无法起草" in r.explanation  # 模型给的中文原因直接展示
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert not [m for m in view.dialogue_messages if m.kind is DialogueOutcomeType.DRAFT]


def test_dialogue_question_yields_explanation_without_new_verdict(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    before = _item_view(_workspace(svc, w), w["items"][0]).current_verdict.round_ref
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="这条结论的依据是什么？",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    assert r.outcome_type is DialogueOutcomeType.EXPLANATION and r.explanation
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.current_verdict.round_ref == before  # 解释不改结论
    assert any(m.kind is DialogueOutcomeType.EXPLANATION for m in view.dialogue_messages)


def test_dialogue_challenge_maintain_keeps_verdict(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    before = _item_view(_workspace(svc, w), w["items"][0]).current_verdict.round_ref
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="我认为这条判得太严了",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    assert r.outcome_type is DialogueOutcomeType.EXPLANATION
    assert _item_view(_workspace(svc, w), w["items"][0]).current_verdict.round_ref == before


def test_dialogue_reeval_supersedes_verdict(session):
    """改判必经轮次：新结论替代旧结论（旧卡收折为已替代）。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    old_round = _item_view(_workspace(svc, w), w["items"][0]).current_verdict.round_ref
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="表达在来源里有明确口径，请改判",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    assert r.outcome_type is DialogueOutcomeType.REEVAL
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.current_verdict.verdict_kind is VerdictKind.PASS
    assert view.current_verdict.trigger is DiagnosisTrigger.DIALOGUE_REEVAL
    old = next(v for v in view.verdict_history if v.round_ref == old_round)
    assert old.superseded_by == view.current_verdict.round_ref


# ---- 覆盖确认 / 人工撤回 ----

def test_override_confirm_requires_reason_and_marks_overridden(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    with pytest.raises(InvalidInput):
        svc.confirm_item(ItemConfirmationCommand(
            project_ref=w["project"], item_ref=view.item_ref,
            workspace_version=_version(session, w), override=True,
            operator_ref="U1", idempotency_key=f"cf-{uuid.uuid4()}",
        ))
    result = svc.confirm_item(ItemConfirmationCommand(
        project_ref=w["project"], item_ref=view.item_ref,
        workspace_version=_version(session, w), override=True,
        reason="业务侧已线下确认口径",
        operator_ref="U1", idempotency_key=f"cf-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "confirmed"
    round_ = session.get(ItemDiagnosisRound, uuid.UUID(view.current_verdict.round_ref))
    assert round_.overridden is True
    assert round_.adjudication_decision == VerdictDecision.REJECTED.value


def test_non_override_confirm_redirected_to_adjudication(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    result = svc.confirm_item(ItemConfirmationCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"cf-{uuid.uuid4()}",
    ))
    assert result.status == "rejected_precheck"
    assert "AEP-034" in result.next_action


def test_withdraw_item_requires_reason_and_terminates(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session)
    with pytest.raises(InvalidInput):
        svc.withdraw_item(ItemWithdrawCommand(
            project_ref=w["project"], item_ref=w["items"][0],
            workspace_version=_version(session, w), reason="  ",
            operator_ref="U1", idempotency_key=f"wd-{uuid.uuid4()}",
        ))
    result = svc.withdraw_item(ItemWithdrawCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        workspace_version=_version(session, w), reason="与既有条目重复",
        operator_ref="U1", idempotency_key=f"wd-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "terminated"
    item = session.get(RequirementItem, uuid.UUID(w["items"][0]))
    assert item.status == RequirementItemStatus.TERMINATED.value


# ---- 确认时放弃在途草案 ----

def test_adopt_pass_abandons_inflight_draft(session):
    w = _seed_pending_items(session)
    _set_expression(session, w["items"][0], "系统应支持导出 docx，并明确验收观察口径。")
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="把当前条目的表达修订为：再加一句",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    assert r.draft.in_flight
    view = _item_view(_workspace(svc, w), w["items"][0])
    _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                VerdictDecision.ADOPTED)
    after = _item_view(_workspace(svc, w), w["items"][0])
    drafts = [m for m in after.dialogue_messages if m.kind is DialogueOutcomeType.DRAFT]
    assert drafts and not any(d.in_flight for d in drafts)  # 草案已放弃留痕


# ---- 工作区聚合 ----

def test_workspace_counts_and_next_action(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w)
    workspace = _workspace(svc, w)
    assert workspace.total_count == 2
    assert workspace.confirmed_count == 0
    assert "待你裁决" in workspace.next_action

    # 发现项证据行不再携带人工复核字段（冻结历史口径不复活）
    findings = session.scalars(select(ItemReviewFinding)).all()
    assert findings and all(f.decision is None for f in findings)


def test_attribute_revision_keeps_standing_verdict(session):
    """29148 属性补齐：验证方式/验收准则/优先级修订留痕但不失效诊断轮次、不链式增量。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    before = _item_view(_workspace(svc, w), w["items"][0])
    assert before.review_status is RIS.AWAITING_ADJUDICATION
    standing_round = before.current_verdict.round_no

    item_service = build_sql_requirement_item_service(session)
    result = item_service.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        workspace_version=_version(session, w),
        revision_mode=ItemRevisionMode.MANUAL, field_key="priority",
        revised_value="high", reason="干系人裁量",
        operator_ref="U1", idempotency_key=f"rev-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "applied"
    assert result.agent_run_ref is None  # 属性修订不触发链式增量诊断

    after = _item_view(_workspace(svc, w), w["items"][0])
    assert after.review_status is RIS.AWAITING_ADJUDICATION  # 站立结论未失效
    assert after.current_verdict is not None
    assert after.current_verdict.round_no == standing_round
    assert after.priority == "high"


# ---- AEP-095 斜杠命令解释（2026-07-06 扩展：命令词确定性解析 + Stub 解释 + 派发）----

def _dialogue_cmd(svc, session, w, item_ref, message, selected=None):
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=item_ref, message=message,
        selected_item_refs=selected or [],
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    return r


def test_slash_unknown_command_deterministic_reply(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/不存在 随便写")
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert r.command_word == "不存在" and "可用命令" in r.message


def test_slash_diagnosis_current_item(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/诊断 标准")
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert r.operation == "start_diagnosis" and r.agent_run_ref
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.current_verdict is not None  # auto_complete：结论已铸


def test_slash_diagnosis_selected_scope(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _dialogue_cmd(
        svc, session, w, w["items"][0],
        "/诊断 对已勾选条目发起全面诊断", selected=list(w["items"]),
    )
    assert r.operation == "start_diagnosis"
    assert r.params_echo["diagnosis_mode"] == "comprehensive"
    ws = _workspace(svc, w)
    assert all(_item_view(ws, ref).current_verdict is not None for ref in w["items"])


def test_slash_reject_verdict_with_reason_voids_it(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/拒绝结论 第1轮 依据不足，判定过严")
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert r.operation == "adjudicate_reject" and "已拒绝" in r.message
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.review_status is RIS.NO_VERDICT  # 结论作废回无结论


def test_slash_reject_verdict_without_reason_clarifies(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/拒绝结论")
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert "理由" in r.message
    view = _item_view(_workspace(svc, w), w["items"][0])
    assert view.review_status is RIS.AWAITING_ADJUDICATION  # 未派发，结论仍在


def test_slash_adopt_verdict_executes_side_effect_chain(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/采纳结论")
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert r.operation == "adjudicate_adopt" and "已采纳" in r.message


def test_slash_manual_revision_applies_and_does_not_chain(session):
    """阶段策略解耦 P1：评审页 /修订 人工修订走对象层直发（非采纳路径），不再链式复诊。

    回执只陈述修订已应用，不写「链式增量」；修订内容照常落库。
    """
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    r = _dialogue_cmd(
        svc, session, w, w["items"][0],
        "/修订 把当前条目的表达修订为：登录响应时间在正常负载下不超过 2 秒",
    )
    assert r.operation == "manual_revision"
    assert "链式增量" not in (r.message or "") and "修订已应用" in (r.message or "")
    item = session.get(RequirementItem, uuid.UUID(w["items"][0]))
    assert "2 秒" in item.expression


def test_slash_revision_direction_only_drafts(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/修订 把验收口径写得更可测一些")
    assert r.outcome_type is DialogueOutcomeType.DRAFT
    assert r.command_word == "修订" and r.operation == "draft"
    assert r.draft is not None and r.draft.in_flight


def test_slash_adopt_draft_without_inflight_draft(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/采纳草案")
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert "没有在途修订草案" in r.message


def test_slash_override_confirm_with_reason(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/覆盖确认 理由：线下评审已确认口径")
    assert r.operation == "override_confirm"
    item = session.get(RequirementItem, uuid.UUID(w["items"][0]))
    assert item.status == "confirmed"


def test_slash_override_confirm_without_reason_clarifies(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/覆盖确认")
    assert "理由" in r.message
    item = session.get(RequirementItem, uuid.UUID(w["items"][0]))
    assert item.status == "pending_confirmation"  # 未派发


def test_slash_withdraw_terminates(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _dialogue_cmd(svc, session, w, w["items"][0], "/撤回 理由：需求已合并到其它条目")
    assert r.operation == "withdraw"
    item = session.get(RequirementItem, uuid.UUID(w["items"][0]))
    assert item.status == "terminated"


# ---- 链路回执条：review_dialogue on_stage 阶段事实（04A §2.1 增补）----

def test_dialogue_on_stage_slash_command_sequence(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    stages: list[str] = []
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0], message="/诊断 标准",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ), on_stage=stages.append)
    session.commit()
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert stages == ["accepted", "interpreting", "dispatching"]


def test_dialogue_on_stage_free_text_runs_generation_lane(session):
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    stages: list[str] = []
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0],
        message="这条结论的依据是什么？",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ), on_stage=stages.append)
    session.commit()
    assert r.outcome_type is DialogueOutcomeType.EXPLANATION
    assert stages == ["accepted", "running"]  # 生成型 lane 统一映射「执行中」


def test_dialogue_on_stage_unknown_command_emits_nothing(session):
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    stages: list[str] = []
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=w["items"][0], message="/不存在 x",
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ), on_stage=stages.append)
    session.commit()
    assert "未知命令" in r.message
    assert stages == []


# ============================================================================
# issue #10 B2a：run 级失败事实契约 · 显示态封闭集下沉 · 说明句单点 · 契约守卫
# ============================================================================


def _run_ref_of(workspace, item_ref: str) -> str:
    """取包含该条目的诊断批次（run）id。"""
    return next(r.run_ref for r in workspace.diagnosis_runs if item_ref in r.item_refs)


def _fe_heuristic_failed_count(workspace, run) -> int:
    """前端旧启发式（collectRunCompletionToasts）：以条目全局最新态猜测本 run 失败。
    复刻其归因逻辑，用于与后端按 run 直接归因对比，钉死其漏报/误计缺陷。"""
    by_ref = {i.item_ref: i for i in workspace.review_items}
    count = 0
    for ref in run.item_refs:
        item = by_ref.get(ref)
        if item is None:
            continue
        if item.review_status is RIS.NO_VERDICT and item.verdict_history \
                and item.verdict_history[0].status == "failed":
            count += 1
    return count


class _MarkerFailDiagnoser:
    """表达含 FAILME → 交付失败；否则委派 stub 确定性规则（用于同批混合成败）。"""

    def __init__(self):
        from app.adapters.llm import StubRequirementItemDiagnoser
        self._ok = StubRequirementItemDiagnoser()

    def diagnose(self, project_ref, diagnosis_mode, item, sources, raw_text,
                 revisions, prior_findings, excluded_points=None, thread_context="", business_sources=None, attestation=None):
        from app.adapters.llm import _failed_verdict
        if "FAILME" in str(item.get("expression") or ""):
            return _failed_verdict("诊断模型不可用", "llm_error")
        # S7：转发点要透传全部参数——签名加了 attestation 却不往下传，是一个会静默吞掉
        # 新参数的委托桩，被它包住的诊断器永远收不到背书事实。
        return self._ok.diagnose(project_ref, diagnosis_mode, item, sources, raw_text,
                                 revisions, prior_findings, excluded_points, thread_context,
                                 business_sources, attestation)


def test_run_failed_count_attributes_failures_within_the_run(session):
    """A1：本 run 失败按 run 直接归因（分子=本批失败轮次数），与条目全局最新态无关。"""
    from app.adapters.llm import StubRequirementItemDiagnoser
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(failed=True))
    ws = _workspace(svc, w)
    run = ws.diagnosis_runs[0]
    assert run.status == "completed"
    assert run.total_count == 2 and run.failed_count == 2  # 两条目全失败
    # VerdictRead 携带 run/batch 关联字段（只增不改）
    view = _item_view(ws, w["items"][0])
    assert view.verdict_history[0].batch_ref == run.run_ref
    assert view.verdict_history[0].item_ref == view.item_ref


def test_run_failed_count_mixed_success_and_failure_in_one_run(session):
    """A1：同批一失一成 → failed_count 恰为失败条目数，不受成功条目干扰。"""
    w = _seed_pending_items(session)
    _set_expression(session, w["items"][0], "系统应支持导出 docx，FAILME。")
    _set_expression(session, w["items"][1], "系统应支持导出 docx，并明确验收观察口径。")
    svc, _ = _run_diagnosis(session, w, diagnoser=_MarkerFailDiagnoser())
    run = _workspace(svc, w).diagnosis_runs[0]
    assert run.total_count == 2 and run.failed_count == 1


def test_run_failed_count_not_underreported_after_resettlement_rediagnosis(session):
    """A1：结算窗口内被新批重诊，旧 run 失败不漏报——直击前端以条目全局最新态猜测的归因错位。"""
    from app.adapters.llm import StubRequirementItemDiagnoser
    w = _seed_pending_items(session)
    item0 = w["items"][0]
    # run A：item0 失败（item0 落 no_verdict，最新轮次 failed）
    svc, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(failed=True),
                            item_refs=[item0])
    ws = _workspace(svc, w)
    run_a = _run_ref_of(ws, item0)
    view0 = _item_view(ws, item0)
    assert view0.display_code is RDC.DIAGNOSIS_FAILED
    assert _fe_heuristic_failed_count(ws, next(r for r in ws.diagnosis_runs if r.run_ref == run_a)) == 1

    # run B：同 item0 重诊成功 → item0 转 awaiting（全局最新态不再是 failed）
    _set_expression(session, item0, "系统应支持导出 docx，并明确验收观察口径。")
    svc2, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(), item_refs=[item0])
    ws2 = _workspace(svc2, w)
    view0b = _item_view(ws2, item0)
    assert view0b.review_status is RIS.AWAITING_ADJUDICATION

    run_a_after = next(r for r in ws2.diagnosis_runs if r.run_ref == run_a)
    run_b_after = next(r for r in ws2.diagnosis_runs if r.run_ref != run_a)
    # 后端按 run 归因：run A 失败事实稳定保留（不漏报），run B 无失败
    assert run_a_after.failed_count == 1
    assert run_b_after.failed_count == 0
    # 前端旧启发式在 run A 上会漏报（item0 已非 no_verdict → 计 0），后端修正之
    assert _fe_heuristic_failed_count(ws2, run_a_after) == 0


def test_run_failed_count_does_not_bleed_across_batches(session):
    """A1：跨批遗留失败不误计——run B 只统计其成员轮次，不吸入 item0 的遗留失败。"""
    from app.adapters.llm import StubRequirementItemDiagnoser
    w = _seed_pending_items(session)
    item0, item1 = w["items"][0], w["items"][1]
    # run A：item0 失败并遗留 no_verdict
    svc, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(failed=True),
                            item_refs=[item0])
    run_a = _run_ref_of(_workspace(svc, w), item0)
    # run B：只诊 item1（成功）
    _set_expression(session, item1, "系统应支持导出 docx，并明确验收观察口径。")
    svc2, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(), item_refs=[item1])
    ws2 = _workspace(svc2, w)
    run_b = next(r for r in ws2.diagnosis_runs if r.run_ref != run_a)
    assert run_b.item_refs == [item1]
    assert run_b.failed_count == 0  # item0 的遗留失败不落入 run B


def test_display_code_closed_set_covers_pending_and_confirmed_and_terminated(session):
    """A2：显示态封闭集——未诊断=待诊断；确认/终止终态码。"""
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    ws = _workspace(svc, w)
    v = _item_view(ws, w["items"][0])
    assert v.review_status is RIS.NO_VERDICT
    assert v.display_code is RDC.PENDING_DIAGNOSIS
    assert v.display_note == "尚未发起过诊断。"

    # 确认
    _set_expression(session, w["items"][0], "系统应支持导出 docx，并明确验收观察口径。")
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    ws = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                     VerdictDecision.ADOPTED)
    assert _item_view(ws, w["items"][0]).display_code is RDC.CONFIRMED


def test_display_code_diagnosis_failed_and_streak_note(session):
    """A2：诊断失败码 + 连击≥2 说明句（对齐 deriveReviewDisplay 副语）。"""
    from app.adapters.llm import StubRequirementItemDiagnoser
    w = _seed_pending_items(session)
    item0 = w["items"][0]
    svc, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(failed=True),
                            item_refs=[item0])
    v1 = _item_view(_workspace(svc, w), item0)
    assert v1.display_code is RDC.DIAGNOSIS_FAILED
    assert v1.display_note == "最近一次诊断未完成（原因见对话线程），可重试或改人工处理。"
    # 二次失败 → 连击说明句
    svc2, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(failed=True),
                             item_refs=[item0])
    v2 = _item_view(_workspace(svc2, w), item0)
    assert v2.display_code is RDC.DIAGNOSIS_FAILED
    assert v2.display_note == "诊断已连续失败 2 次（原因见对话线程），可重试或改人工处理。"


def test_display_code_verdict_rejected(session):
    """A2：结论已拒绝码 + 说明句。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    ws = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                     VerdictDecision.REJECTED, reason="判定依据不足")
    v = _item_view(ws, w["items"][0])
    assert v.review_status is RIS.NO_VERDICT
    assert v.display_code is RDC.VERDICT_REJECTED
    assert v.display_note == "上一轮结论已被拒绝，可重新诊断、人工修订、覆盖确认或撤回。"


def test_display_code_supplement_pending(session):
    """A2：待补充来源码 + 说明句（不含计数，对齐蓝本）。"""
    w = _seed_pending_items(session)
    _set_expression(session, w["items"][0], "系统应支持导出 docx（缺来源：口径出处不明）")
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    ws = _adjudicate(svc, session, w, view.item_ref, view.current_verdict.round_ref,
                     VerdictDecision.ADOPTED)
    v = _item_view(ws, w["items"][0])
    assert v.display_code is RDC.SUPPLEMENT_PENDING
    assert v.display_note == "来源缺口未闭合，补充来源或修订表达后可再诊断。"


def test_display_code_pending_diagnosis_invalidated_carries_revised_subnote(session):
    """A2：修订失效瞬态归待诊断 + 已修订副语（进行过诊断的条目不回退到「尚未发起过诊断」）。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    view = _item_view(_workspace(svc, w), w["items"][0])
    round_ref = view.current_verdict.round_ref
    # 直接令唯一轮次失效（无更新轮次）——复刻修订失效后未重诊的瞬态
    round_ = session.get(ItemDiagnosisRound, uuid.UUID(round_ref))
    round_.invalidated = True
    round_.invalidated_reason = "条目已修订"
    session.commit()
    v = _item_view(_workspace(svc, w), w["items"][0])
    assert v.review_status is RIS.NO_VERDICT
    assert v.display_code is RDC.PENDING_DIAGNOSIS
    assert v.display_note == "条目已修订，旧结论已失效；可重新诊断。"


def test_awaiting_display_note_is_single_source(session):
    """A3：待裁决说明句由后端单点输出（区1/区5 未来同源），随结论状态字。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    v = _item_view(_workspace(svc, w), w["items"][0])
    assert v.review_status is RIS.AWAITING_ADJUDICATION
    assert v.display_code is RDC.AWAITING_ADJUDICATION
    assert v.display_note == v.status_note  # 非 no_verdict 沿用 _derive_display 单点
    assert "待你裁决" in v.display_note


def test_no_verdict_wording_cleared_from_backend_output(session):
    """A3：后端读视图输出清零「无结论」字样（裁定 4：采用「可诊断/待诊断」白话）。"""
    w = _seed_pending_items(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    ws = _workspace(svc, w)
    assert "无结论" not in (ws.next_action or "")
    for op in ws.available_operations:
        assert "无结论" not in (op.disabled_reason or "")
    for item in ws.review_items:
        assert "无结论" not in item.status_note
        assert "无结论" not in item.display_note


def test_completed_run_retained_and_failed_count_stable(session):
    """A4 契约守卫：批次收束后仍作为已完成 run 保留可查，failed_count 稳定。"""
    from app.adapters.llm import StubRequirementItemDiagnoser
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, diagnoser=StubRequirementItemDiagnoser(failed=True),
                            item_refs=[w["items"][0]])
    ws1 = _workspace(svc, w)
    run1 = ws1.diagnosis_runs[0]
    assert run1.status == "completed" and run1.failed_count == 1
    # 再次读取（无新批次）：已完成 run 仍在，计数不漂移
    svc2 = build_sql_item_review_service(session, auto_complete=True)
    ws2 = _workspace(svc2, w)
    assert len(ws2.diagnosis_runs) == 1
    assert ws2.diagnosis_runs[0].failed_count == 1


# ============================================================================
# 为条目找候选来源（issue #30 出口三部曲之二 / ADR-0002 P3「说缺必说补」）
# ============================================================================

def _seed_item_for_find_sources(session):
    """一条条目 + 精心构造的候选差集：验证四类排除（已链接/待确认/已撤销/异批次）+ superseded。"""
    p = Project(name="fs-demo")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text=RAW_TEXT, source_note="接入对象:访谈纪要")
    session.add(mat)
    session.flush()
    ctx_a = ParseRequest(
        project_id=p.id, material_ref=mat.id, operator_ref="U1",
        idempotency_key=f"fs-a-{uuid.uuid4()}", workspace_version=1,
    )
    ctx_b = ParseRequest(
        project_id=p.id, material_ref=mat.id, operator_ref="U1",
        idempotency_key=f"fs-b-{uuid.uuid4()}", workspace_version=1,
    )
    session.add_all([ctx_a, ctx_b])
    session.flush()
    parse_a = MaterialParseResult(
        project_id=p.id, material_ref=mat.id, context_ref=ctx_a.id, parse_status="parsed",
    )
    parse_b = MaterialParseResult(  # 异批次：独立 context（context_ref 唯一约束）
        project_id=p.id, material_ref=mat.id, context_ref=ctx_b.id, parse_status="parsed",
    )
    session.add_all([parse_a, parse_b])
    session.flush()

    def _el(parse, content, status="confirmed", superseded=False):
        e = RequirementElement(
            project_id=p.id, parse_result_ref=parse.id, element_type="business_rule",
            content=content,
            source_anchor=json.dumps({"ranges": [{"start": 0, "end": len(content), "exact": content}]}),
            confidence=0.9, process_status=status, superseded=superseded,
        )
        session.add(e)
        session.flush()
        return str(e.id)

    ids = {
        "linked": _el(parse_a, "已链接来源"),
        "cand1": _el(parse_a, "大额订单需人工审核"),
        "cand2": _el(parse_a, "单笔超一万元须部门经理审批"),
        "pending": _el(parse_a, "待确认的要素", status="pending_confirmation"),
        "revoked": _el(parse_a, "已撤销的要素", status="revoked"),
        "superseded": _el(parse_a, "旧版本被替代", superseded=True),
        "other_batch": _el(parse_b, "异批次已确认要素"),
    }
    item = RequirementItem(
        project_id=p.id, parse_result_ref=parse_a.id, formation_context_ref=ctx_a.id,
        req_no="REQ-006", expression="大额订单需人工审核后方可提交",
        req_type="functional", status="pending_confirmation",
        source_element_refs=json.dumps([ids["linked"]]),
    )
    session.add(item)
    session.commit()
    return {"project": str(p.id), "item": str(item.id), **ids}


def _find_sources(svc, session, fs):
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=fs["project"], item_ref=fs["item"], message="/找来源",
        workspace_version="1", operator_ref="U1", idempotency_key=f"fs-{uuid.uuid4()}",
    ))
    session.commit()
    return r


def test_find_sources_pool_excludes_four_classes_and_superseded(session):
    """A3：候选差集=同批次已确认且未链接的要素，逐类排除已链接/待确认/已撤销/异批次+旧版本。"""
    fs = _seed_item_for_find_sources(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    pool = svc._source_candidate_pool(svc._items.get_item(fs["item"]))
    ids = {c["id"] for c in pool}
    assert ids == {fs["cand1"], fs["cand2"]}
    assert fs["linked"] not in ids        # 已链接
    assert fs["pending"] not in ids       # 待确认
    assert fs["revoked"] not in ids       # 已撤销
    assert fs["other_batch"] not in ids   # 异批次
    assert fs["superseded"] not in ids    # 旧版本被替代
    assert all(c["source_quote"] for c in pool)  # 每候选带原文引文


def test_find_sources_dialogue_returns_candidate_payload(session):
    """A2/A4：/找来源 经命令表路由到 find_sources，桩 lane 返回结构合法的候选载荷（COMMAND 出口）。"""
    fs = _seed_item_for_find_sources(session)
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _find_sources(svc, session, fs)
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert r.operation == "find_sources"
    assert r.source_candidates is not None
    assert {c.element_ref for c in r.source_candidates} == {fs["cand1"], fs["cand2"]}
    for c in r.source_candidates:
        assert c.reason and c.rank >= 1 and c.source_quote and c.content


def test_find_sources_empty_pool_gives_exit_not_dead_end(session):
    """P1 无死胡同：候选差集为空时不静默，给出撤回/补材料的可行出口，且无候选载荷。"""
    fs = _seed_item_for_find_sources(session)
    for eid in (fs["cand1"], fs["cand2"]):  # 撤销两条候选 → 差集空
        session.get(RequirementElement, uuid.UUID(eid)).process_status = "revoked"
    session.commit()
    svc = build_sql_item_review_service(session, auto_complete=True)
    r = _find_sources(svc, session, fs)
    assert r.outcome_type is DialogueOutcomeType.COMMAND
    assert r.source_candidates is None
    assert r.next_action and ("撤回" in r.next_action or "补入" in r.next_action)


# ============================================================================
# 评审页对话来源隔离（2026-07-20 走查反馈第⑧组）
#
# 条目形成页与本页共用同一个阶段键 item_revision_draft（建议卡机制同源，是刻意决定），
# 载荷此前不标来源，本页读投影遂把形成页的起草交换也拉了进来，读着像自己说过的话。
# ============================================================================

def _write_draft_payload(svc, item_ref: str, *, origin, seq=None, in_flight=False, value="形成页起草的表达。"):
    """按某一页的写法落一条起草交换；in_flight=True 时再挂一条候选建议行。"""
    body = {
        "item_ref": item_ref, "proposed_value": value, "note": "",
        "user_message": "补写可观测结果", "at": "2026-07-20T10:00:00+00:00",
    }
    if seq is not None:
        body["draft_seq"] = seq
    if origin is not None:
        body["origin"] = origin
    message_ref = svc._model_results.record_stage_payload(
        "item_revision_draft", item_ref, "drafted", json.dumps(body, ensure_ascii=False), "测试写入",
    )
    if in_flight:
        svc._formation_process.save_suggestion(item_ref, "expression", value, "候选", message_ref)
    return message_ref


def _dialogue_of(svc, w, item_ref):
    return _item_view(_workspace(svc, w), item_ref).dialogue_messages


def test_review_dialogue_hides_formation_history_exchanges(session):
    """形成页留下的已收束交换不进本页对话历史。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    item_ref = w["items"][0]
    _write_draft_payload(svc, item_ref, origin="formation")
    session.commit()

    assert _dialogue_of(svc, w, item_ref) == []


def test_review_dialogue_keeps_formation_inflight_draft_with_its_origin(session):
    """在途候选是唯一例外：跨页续稿保留，用户仍可在本页采纳，但要标明来源。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    item_ref = w["items"][0]
    _write_draft_payload(svc, item_ref, origin="formation", in_flight=True)
    session.commit()

    messages = _dialogue_of(svc, w, item_ref)
    assert len(messages) == 1
    assert messages[0].origin == "formation" and messages[0].in_flight


def test_review_own_exchanges_unaffected_and_marked(session):
    """本页自己的交换照常显示，并标上本页来源。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    item_ref = w["items"][0]
    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=item_ref, message="修订为：导出耗时不超过三秒",
        workspace_version=_version(session, w), operator_ref="U1",
        idempotency_key=f"D-{uuid.uuid4()}",
    ))
    session.commit()
    assert r.outcome_type == DialogueOutcomeType.DRAFT

    messages = _dialogue_of(svc, w, item_ref)
    assert [m.origin for m in messages] == ["review"]


def test_legacy_payload_without_origin_keeps_showing(session):
    """存量载荷没有来源字段，无从判断来源，一律维持原有显示，不猜。"""
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    item_ref = w["items"][0]
    _write_draft_payload(svc, item_ref, origin=None, seq=1)
    session.commit()

    messages = _dialogue_of(svc, w, item_ref)
    assert len(messages) == 1 and messages[0].origin is None


def test_review_draft_after_formation_inflight_draft_does_not_crash(session):
    """稿次断链（本卡顺手修）：形成页写的在途建议没有稿次字段，本页起草取稿次不得抛错。

    触发路径＝用户在形成页起草但不采纳，再到本页说一句修订意图。缺稿次按 0 算，
    故本页新稿即第 1 稿，序号语义不变。
    """
    w = _seed_pending_items(session)
    svc, _ = _run_diagnosis(session, w, item_refs=[w["items"][0]])
    item_ref = w["items"][0]
    _write_draft_payload(svc, item_ref, origin="formation", in_flight=True)
    session.commit()

    r = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=item_ref, message="修订为：导出耗时不超过三秒",
        workspace_version=_version(session, w), operator_ref="U1",
        idempotency_key=f"D-{uuid.uuid4()}",
    ))
    session.commit()
    assert r.outcome_type == DialogueOutcomeType.DRAFT
    assert r.draft is not None and r.draft.draft_seq == 1
