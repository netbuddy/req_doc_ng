"""P03 确认工作台 + P04 版本关系层：直接裁定、复核结论×裁定矩阵、修订迭代、
就地修订、改源联动（勘误/补入）、拆分/合并/新增、重开/回流、历史留痕、门禁。

设计事实源：domains/DS-001/state-machines/需求要素.md（迁移表是事实源）、
docs/30 05A/SCN-001 §4.3/§4.4；验收口径：docs/iterations/SCN-001/验收场景清单.md。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.adapters.llm import (
    ReviewFinding,
    StubElementOperationExecutor,
    StubElementReviewer,
)
from app.api.schemas import (
    ElementAiExecutionCommand,
    ElementChangeConfirmCommand,
    ElementDecisionCommand,
    ElementEditCommand,
    ElementRecognitionCommand,
    ElementReopenCommand,
    ElementReviewCommand,
    ElementRevisionCommand,
    ManualElementCorrectionCommand,
    MaterialErratumCommand,
    MaterialSupplementCommand,
    RevisionFinalizeCommand,
    SourceAnchorRange,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import IntakeRecord, Material, Project, RequirementElement
from app.domain.enums import ElementProcessStatus as ES
from app.domain.enums import ReviewConclusion as RC
from app.domain.errors import InvalidInput, RejectedTransition
from app.repositories.in_memory import build_analysis_wiring
from app.repositories.sqlalchemy import build_sql_analysis_service

# 5 句：stub 识别取前 4 句为要素，第 5 句留给「扫原文补漏」
RAW = "系统应支持一键导出所需数据。导出任务需在30秒内完成。系统要给用户发送通知。希望提供统一的数据工作台。库存不足时下单要被拦截并提示用户。"


def _wiring(**kwargs):
    w = build_analysis_wiring(auto_complete=True, **kwargs)
    w.source_assets.seed_material("M-1", raw_text=RAW, accepted=True)
    return w


def _workspace(w, key="K1"):
    r = w.service.submit_element_recognition(ElementRecognitionCommand(
        project_ref="P-1", material_ref="M-1", operator_ref="U1", idempotency_key=key,
    ))
    return r.parse_context_ref, w.service.read_element_workspace(r.parse_context_ref)


def _refresh(w, ctx):
    return w.service.read_element_workspace(ctx)


def _decide(w, ctx, refs, decision, key="KD"):
    ws = _refresh(w, ctx)
    return w.service.decide_elements(ElementDecisionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_refs=refs, decision=decision, operator_ref="U1", idempotency_key=key,
    ))


def _review(w, ctx, refs, key="KR"):
    ws = _refresh(w, ctx)
    return w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        target_element_refs=refs, review_intent="复核",
        operator_ref="U1", idempotency_key=key,
    ))


def _finalize(w, ctx, ref, action, key="KJ"):
    ws = _refresh(w, ctx)
    return w.service.finalize_revision(RevisionFinalizeCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, action=action, operator_ref="U1", idempotency_key=key,
    ))


def _element(ws, ref):
    return next(e for e in ws.elements if e.id == ref)


# ============================================================================
# E2：直接确认 / 拒绝（US-E2-01 / US-E2-02，含批量）
# ============================================================================

def test_batch_confirm_and_reject():
    w = _wiring()
    ctx, ws = _workspace(w)
    # stub 识别把第 2 句判为「建议剔除」，它待在候选区里、不能被确认（冷审查裁定 C1 的守卫），
    # 故批量确认的取样要避开候选项；拒绝不受此限，撤销本就是候选区的正当出口。
    ids = [e.id for e in ws.elements if e.model_verdict.value != "suspected_noise"]
    assert all(e.process_status is ES.PENDING_CONFIRMATION for e in ws.elements)
    assert len(ids) >= 3

    ws2 = _decide(w, ctx, ids[:2], "confirm", key="KD1")
    assert all(_element(ws2, i).process_status is ES.CONFIRMED for i in ids[:2])

    ws3 = _decide(w, ctx, ids[2:3], "reject", key="KD2")
    rejected = _element(ws3, ids[2])
    assert rejected.process_status is ES.REVOKED
    assert not rejected.superseded  # 已撤销仍可查，不删除


def test_decide_on_terminal_state_default_rejected():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _decide(w, ctx, [ref], "confirm", key="KD3")
    with pytest.raises(RejectedTransition):  # 已确认不再直接确认（默认拒绝）
        _decide(w, ctx, [ref], "confirm", key="KD4")


# ============================================================================
# E2：复核 = 对话轮次（不迁移状态）；修订稿经「采纳修订稿」即确认（US-E2-03 … 07 收敛）
# ============================================================================

def test_review_keeps_pending_and_fills_conclusion():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _review(w, ctx, [ref])
    ws2 = _refresh(w, ctx)
    el = _element(ws2, ref)
    assert el.process_status is ES.PENDING_CONFIRMATION  # 复核不迁移状态（会话事实）
    assert el.review_conclusion is not None              # AI 结论已回填（stub：pass）
    assert el.review_basis


def test_review_needs_revision_then_adopt_confirms():
    """复核给出修订稿（会话数据）→ 采纳修订稿 = 采纳即确认，content 生效、版本+1。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    w.model_orchestration._reviewer = StubElementReviewer(findings=(
        ReviewFinding(ref, RC.NEEDS_REVISION, "复核意见", "修订稿内容"),
    ))
    _review(w, ctx, [ref])
    ws2 = _refresh(w, ctx)
    el = _element(ws2, ref)
    assert el.process_status is ES.PENDING_CONFIRMATION  # 结论返回也不迁移
    assert el.revision_draft == "修订稿内容"

    ws3 = _finalize(w, ctx, ref, "adopt")
    assert ws3.status == "accepted"
    el3 = _element(_refresh(w, ctx), ref)
    assert el3.process_status is ES.CONFIRMED  # 采纳即确认
    assert el3.content == "修订稿内容"          # 修订稿生效
    assert el3.version >= 2                    # 旧版本保留（历史）


def test_review_not_accepted_then_direct_reject():
    """不认可复核后的要素 → 直接拒绝（无「否决」事件）。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _review(w, ctx, [ref])
    ws2 = _decide(w, ctx, [ref], "reject", key="KD6")
    assert _element(ws2, ref).process_status is ES.REVOKED


def test_decide_confirm_after_review_conversation():
    """复核只是对话轮次，随时可直接确认。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _review(w, ctx, [ref])
    ws2 = _decide(w, ctx, [ref], "confirm", key="KD5")
    assert _element(ws2, ref).process_status is ES.CONFIRMED


def test_review_failed_keeps_pending_retryable():
    """US-E2-08：复核失败 → 状态不变、留失败原因、可重试。"""
    w = _wiring(reviewer=StubElementReviewer(failed=True))
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _review(w, ctx, [ref])
    ws2 = _refresh(w, ctx)
    el = _element(ws2, ref)
    assert el.process_status is ES.PENDING_CONFIRMATION
    assert el.review_conclusion is None and el.review_basis  # 失败原因保留
    # 可重试（对话自然继续）
    r = _review(w, ctx, [ref], key="KR-retry")
    assert r.status == "accepted"


def test_adopt_without_draft_rejected():
    w = _wiring(reviewer=StubElementReviewer(failed=True))
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _review(w, ctx, [ref])
    with pytest.raises(InvalidInput):
        _finalize(w, ctx, ref, "adopt")


def test_review_on_terminal_element_rejected_precheck():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _decide(w, ctx, [ref], "confirm", key="KD7")
    r = _review(w, ctx, [ref], key="KR-terminal")
    assert r.status == "rejected_precheck"
    assert "重开" in (r.next_action or "")


# ============================================================================
# E2：修订迭代（对话轮次，不迁状态）/ 采纳即确认 / 放弃草稿（US-E2-09 / US-E2-10 收敛）
# ============================================================================

def test_revision_loop_adopt():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id

    # AI 迭代修订稿：对话轮次，状态保持待确认
    ws0 = _refresh(w, ctx)
    r = w.service.revise_element(ElementRevisionCommand(
        parse_context_ref=ctx, workspace_version=ws0.workspace_version,
        element_ref=ref, mode="ai", instruction="按短信+邮件补充通知方式",
        operator_ref="U1", idempotency_key="KV1",
    ))
    assert r.status == "accepted"
    ws2 = _refresh(w, ctx)
    el = _element(ws2, ref)
    assert el.process_status is ES.PENDING_CONFIRMATION  # 迭代不迁移状态
    assert el.revision_draft and "修订" in el.revision_draft  # stub 修订稿

    # 继续迭代（人工直改）：仍是会话数据
    w.service.revise_element(ElementRevisionCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        element_ref=ref, mode="manual", draft_content="通知方式：短信+邮件",
        operator_ref="U1", idempotency_key="KV2",
    ))
    ws3 = _refresh(w, ctx)
    el3 = _element(ws3, ref)
    assert el3.process_status is ES.PENDING_CONFIRMATION
    assert el3.revision_draft == "通知方式：短信+邮件"

    # 采纳修订稿 → 采纳即确认，修订稿生效
    w.service.finalize_revision(RevisionFinalizeCommand(
        parse_context_ref=ctx, workspace_version=ws3.workspace_version,
        element_ref=ref, action="adopt", operator_ref="U1", idempotency_key="KV3",
    ))
    ws4 = _refresh(w, ctx)
    el4 = _element(ws4, ref)
    assert el4.process_status is ES.CONFIRMED
    assert el4.content == "通知方式：短信+邮件"


def test_revision_abandon_clears_draft_keeps_pending():
    """不采纳 = 清除草稿；状态不变，随时可继续对话或直接裁决。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    ws0 = _refresh(w, ctx)
    w.service.revise_element(ElementRevisionCommand(
        parse_context_ref=ctx, workspace_version=ws0.workspace_version,
        element_ref=ref, mode="manual", draft_content="草稿",
        operator_ref="U1", idempotency_key="KV4",
    ))
    ws2 = _refresh(w, ctx)
    w.service.finalize_revision(RevisionFinalizeCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        element_ref=ref, action="abandon", operator_ref="U1", idempotency_key="KV5",
    ))
    ws3 = _refresh(w, ctx)
    el3 = _element(ws3, ref)
    assert el3.process_status is ES.PENDING_CONFIRMATION  # 状态不变
    assert not el3.revision_draft                          # 草稿已清除
    # 修订过程留痕可查
    history = w.service.read_element_history(ctx, ref)
    actions = [rec.action for rec in history.records]
    assert "revision_iterated" in actions and "discard_revision_draft" in actions


def test_finalize_to_review_action_removed():
    """「转 AI 复核」定夺动作已删除：复核就是对话，不是定夺出口。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    with pytest.raises(InvalidInput):
        w.service.finalize_revision(RevisionFinalizeCommand(
            parse_context_ref=ctx, workspace_version=ws.workspace_version,
            element_ref=ref, action="to_review", operator_ref="U1", idempotency_key="KV7",
        ))


def test_adopt_blocked_when_draft_exceeds_source_then_supplement_unblocks():
    """超出原文守卫：修订稿引入原文没有的事实 → 阻断采纳；补入依据后放行。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    w.service.revise_element(ElementRevisionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, mode="manual", draft_content="高峰期 1000 并发下 P95 导出不超过30秒",
        operator_ref="U1", idempotency_key="KV8",
    ))
    r = _finalize(w, ctx, ref, "adopt", key="KV9")
    assert r.status == "rejected_precheck"
    assert "补入" in (r.next_action or "") and "P95" in (r.next_action or "")

    # 补入依据（补块进入来源语料）后重新采纳
    ws2 = _refresh(w, ctx)
    w.service.material_supplement(MaterialSupplementCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        content="性能口径：高峰期 1000 并发，P95 统计", basis="7/3 性能评审会共识",
        target_element_refs=[], operator_ref="U1", idempotency_key="KV10s",
    ))
    r2 = _finalize(w, ctx, ref, "adopt", key="KV11")
    assert r2.status == "accepted"
    el = _element(_refresh(w, ctx), ref)
    assert el.process_status is ES.CONFIRMED
    assert "P95" in el.content


# ============================================================================
# E2：扫原文补漏（US-E2-11）→ 新「待确认」要素
# ============================================================================

def test_scan_missing_registers_new_pending_elements():
    w = _wiring()
    ctx, ws = _workspace(w)
    n_before = len(ws.elements)
    r = w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        target_element_refs=[],
        selected_text_ranges=[SourceAnchorRange(start=0, end=len(RAW), exact=RAW)],
        review_intent="补漏", operator_ref="U1", idempotency_key="KS1",
    ))
    assert r.status == "accepted"
    ws2 = _refresh(w, ctx)
    new_ones = [e for e in ws2.elements[n_before:]]
    assert new_ones, "补漏应产生新要素"
    assert all(e.process_status is ES.PENDING_CONFIRMATION for e in new_ones)
    assert all(e.source_anchor for e in new_ones)  # 带来源高亮


# ============================================================================
# E3：就地修订（改类型/改范围/改表达 —— 版本+1，不迁状态）
# ============================================================================

def test_edit_adjust_type_keeps_status_bumps_version():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    ws2 = w.service.edit_element(ElementEditCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, edit_type="adjust_type", new_element_type="constraint",
        operator_ref="U1", idempotency_key="KE1",
    ))
    el = _element(ws2, ref)
    assert el.element_type.value == "constraint"
    assert el.process_status is ES.PENDING_CONFIRMATION  # 不迁状态
    assert el.version == 2
    history = w.service.read_element_history(ctx, ref)
    snap = next(rec for rec in history.records if rec.action == "adjust_type")
    assert json.loads(snap.snapshot)["element_type"] != "constraint"  # 改前版本保留


def test_edit_adjust_anchor_from_selection():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    ws2 = w.service.edit_element(ElementEditCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, edit_type="adjust_anchor",
        selected_text_ranges=[SourceAnchorRange(start=0, end=13)],
        operator_ref="U1", idempotency_key="KE2",
    ))
    anchor = json.loads(_element(ws2, ref).source_anchor)
    assert anchor["ranges"][0]["start"] == 0 and anchor["ranges"][0]["exact"] == RAW[0:13]


def test_edit_revise_expression_direct():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    old_content = ws.elements[0].content
    ws2 = w.service.edit_element(ElementEditCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, edit_type="revise_expression", new_content="改写后的表达",
        operator_ref="U1", idempotency_key="KE3",
    ))
    el = _element(ws2, ref)
    assert el.content == "改写后的表达" and el.version == 2
    history = w.service.read_element_history(ctx, ref)
    snap = next(rec for rec in history.records if rec.action == "revise_expression")
    assert json.loads(snap.snapshot)["content"] == old_content  # 改前版本保留


def test_source_drift_projected_on_revise_and_resolved_by_supplement():
    """偏离原文投影（派生不落库）：表达超出来源证据 → source_drift_tokens 非空；
    补入登记依据（扩充语料）后偏离消解。原文快照始终不随修订改写。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[1].id  # 「导出任务需在30秒内完成。」
    assert _element(ws, ref).source_drift_tokens == []  # 识别产物取自原文，无偏离

    ws2 = w.service.edit_element(ElementEditCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, edit_type="revise_expression",
        new_content="导出任务需在300秒内完成。",
        operator_ref="U1", idempotency_key="KDRIFT1",
    ))
    assert _element(ws2, ref).source_drift_tokens == ["300"]  # 偏离被标记
    assert ws2.material_canvas.raw_text == RAW  # 原文不随修订改写

    ws3 = w.service.material_supplement(MaterialSupplementCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        content="评审会确认导出上限放宽到300秒", basis="0707 评审会决议",
        target_element_refs=[ref], operator_ref="U1", idempotency_key="KDRIFT2",
    ))
    assert _element(ws3, ref).source_drift_tokens == []  # 补入依据后偏离消解


def test_source_drift_resolved_by_erratum():
    """勘误出新来源版本后，按新原文重算偏离（材料记错了的消解通道）。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[1].id  # 「导出任务需在30秒内完成。」
    w.service.edit_element(ElementEditCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, edit_type="revise_expression",
        new_content="导出任务需在300秒内完成。",
        operator_ref="U1", idempotency_key="KDRIFT3",
    ))
    ws2 = _refresh(w, ctx)
    assert _element(ws2, ref).source_drift_tokens == ["300"]
    ws3 = w.service.material_erratum(MaterialErratumCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        old_text="30秒", new_text="300秒",
        operator_ref="U1", idempotency_key="KDRIFT4",
    ))
    assert ws3.material_canvas.source_version == 2
    assert _element(ws3, ref).source_drift_tokens == []


# ============================================================================
# E3：改源联动（勘误 US-E3-04 / 补入 US-E3-05）
# ============================================================================

def test_erratum_new_source_version_and_affected_back_to_pending():
    w = _wiring()
    w.source_assets.seed_material("M-2", raw_text="系统应支持把订单导出为 PDG 格式。历史订单保留三年。", accepted=True)
    r = w.service.submit_element_recognition(ElementRecognitionCommand(
        project_ref="P-1", material_ref="M-2", operator_ref="U1", idempotency_key="K-err",
    ))
    ctx = r.parse_context_ref
    ws = _refresh(w, ctx)
    # 找到覆盖 PDG 的要素并先确认它（勘误后应回待确认）
    target = next(e for e in ws.elements if "PDG" in e.content)
    _decide(w, ctx, [target.id], "confirm", key="KD-err")

    ws2 = _refresh(w, ctx)
    ws3 = w.service.material_erratum(MaterialErratumCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        old_text="PDG", new_text="PDF", operator_ref="U1", idempotency_key="KERR1",
    ))
    assert ws3.material_canvas.source_version == 2       # 原文出新来源版本
    assert "PDF" in ws3.material_canvas.raw_text and "PDG" not in ws3.material_canvas.raw_text
    el = _element(ws3, target.id)
    assert el.process_status is ES.PENDING_CONFIRMATION  # 受影响要素回待确认
    assert "PDF" in el.content                           # 内容联动修正
    # 未受影响要素不动
    other = next(e for e in ws3.elements if e.id != target.id)
    assert other.process_status is ES.PENDING_CONFIRMATION


def test_erratum_requires_unique_fragment():
    w = _wiring()
    ctx, ws = _workspace(w)
    with pytest.raises(InvalidInput):
        w.service.material_erratum(MaterialErratumCommand(
            parse_context_ref=ctx, workspace_version=ws.workspace_version,
            old_text="不存在的片段", new_text="X", operator_ref="U1", idempotency_key="KERR2",
        ))


def test_supplement_marks_and_returns_targets_to_pending():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _decide(w, ctx, [ref], "confirm", key="KD-sup")

    ws2 = _refresh(w, ctx)
    ws3 = w.service.material_supplement(MaterialSupplementCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        content="通知方式：短信、邮件", basis="评审会口头确认",
        target_element_refs=[ref], operator_ref="U1", idempotency_key="KSUP1",
    ))
    sups = ws3.material_canvas.supplements
    assert len(sups) == 1 and sups[0].basis == "评审会口头确认" and sups[0].operator_ref == "U1"
    assert ws3.material_canvas.raw_text == _refresh(w, ctx).material_canvas.raw_text  # 原快照不改写
    assert _element(ws3, ref).process_status is ES.PENDING_CONFIRMATION


def test_supplement_requires_basis():
    w = _wiring()
    ctx, ws = _workspace(w)
    with pytest.raises(InvalidInput):
        w.service.material_supplement(MaterialSupplementCommand(
            parse_context_ref=ctx, workspace_version=ws.workspace_version,
            content="X", basis="  ", operator_ref="U1", idempotency_key="KSUP2",
        ))


# ============================================================================
# E4：拆分 / 合并 / 新增（版本关系层，产物落「待确认」）
# ============================================================================

def test_split_produces_pending_children_with_relation():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    draft = w.service.submit_manual_element_correction(ManualElementCorrectionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        operation_type="split", target_element_refs=[ref],
        new_content="拆分要素甲\n拆分要素乙", operator_ref="U1", idempotency_key="KSP1",
    ))
    assert len(draft.after_items) == 2
    ws2 = w.service.confirm_element_change_draft(ElementChangeConfirmCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        draft_ref=draft.draft_ref, operator_ref="U1", idempotency_key="KSP2",
    ))
    old = _element(ws2, ref)
    assert old.superseded  # 旧要素被替代但保留
    children = [e for e in ws2.elements if ref in e.origin_refs]
    assert len(children) == 2
    assert all(e.process_status is ES.PENDING_CONFIRMATION for e in children)


def test_merge_produces_single_pending_with_relations():
    w = _wiring()
    ctx, ws = _workspace(w)
    ids = [e.id for e in ws.elements[:2]]
    draft = w.service.submit_manual_element_correction(ManualElementCorrectionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        operation_type="merge", target_element_refs=ids,
        new_content="合并后的要素表达", operator_ref="U1", idempotency_key="KMG1",
    ))
    assert len(draft.after_items) == 1 and len(draft.before_items) == 2
    ws2 = w.service.confirm_element_change_draft(ElementChangeConfirmCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        draft_ref=draft.draft_ref, operator_ref="U1", idempotency_key="KMG2",
    ))
    merged = next(e for e in ws2.elements if set(ids) <= set(e.origin_refs))
    assert merged.process_status is ES.PENDING_CONFIRMATION
    assert all(_element(ws2, i).superseded for i in ids)


def test_add_missing_with_source_lands_pending():
    w = _wiring()
    ctx, ws = _workspace(w)
    draft = w.service.submit_manual_element_correction(ManualElementCorrectionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        operation_type="add_missing", new_content="希望提供统一的数据工作台",
        reason="人工补登", operator_ref="U1", idempotency_key="KAD1",
    ))
    assert draft.create_gate == "creatable"
    ws2 = w.service.confirm_element_change_draft(ElementChangeConfirmCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        draft_ref=draft.draft_ref, operator_ref="U1", idempotency_key="KAD2",
    ))
    added = next(e for e in ws2.elements
                 if e.content == "希望提供统一的数据工作台" and e.correction_note)
    assert added.process_status is ES.PENDING_CONFIRMATION
    assert added.correction_note == "人工补登"  # 新增依据留痕


def test_add_without_source_fact_blocked():
    """无来源事实拦截：拟新增内容不在原文/补来源 → 回材料补充，确认创建被拒。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    draft = w.service.submit_manual_element_correction(ManualElementCorrectionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        operation_type="add_missing", new_content="系统必须支持区块链存证",
        operator_ref="U1", idempotency_key="KAD3",
    ))
    assert draft.create_gate == "needs_material_supplement"
    with pytest.raises(RejectedTransition):
        w.service.confirm_element_change_draft(ElementChangeConfirmCommand(
            parse_context_ref=ctx, workspace_version=ws.workspace_version,
            draft_ref=draft.draft_ref, operator_ref="U1", idempotency_key="KAD4",
        ))


def test_add_backed_by_supplement_is_creatable():
    """补入后再新增：内容能回到「补」来源 → 可创建。"""
    w = _wiring()
    ctx, ws = _workspace(w)
    w.service.material_supplement(MaterialSupplementCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        content="系统必须支持区块链存证", basis="客户邮件 2026-06-30",
        operator_ref="U1", idempotency_key="KSUP3",
    ))
    ws2 = _refresh(w, ctx)
    draft = w.service.submit_manual_element_correction(ManualElementCorrectionCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        operation_type="add_missing", new_content="系统必须支持区块链存证",
        operator_ref="U1", idempotency_key="KAD5",
    ))
    assert draft.create_gate == "creatable"


# ============================================================================
# E4：重开（已撤销→待确认）/ 回流（已确认→待确认），新版本
# ============================================================================

def test_reopen_revoked_back_to_pending_new_version():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _decide(w, ctx, [ref], "reject", key="KRJ")
    ws2 = _refresh(w, ctx)
    ws3 = w.service.reopen_element(ElementReopenCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        element_ref=ref, operator_ref="U1", idempotency_key="KRO1",
    ))
    el = _element(ws3, ref)
    assert el.process_status is ES.PENDING_CONFIRMATION
    assert el.version == 2  # 新版本
    history = w.service.read_element_history(ctx, ref)
    assert any(rec.action == "reopen" for rec in history.records)
    assert any(rec.action == "reject" for rec in history.records)  # 原撤销结论保留


def test_reflow_confirmed_back_to_pending_new_version():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    _decide(w, ctx, [ref], "confirm", key="KCF")
    ws2 = _refresh(w, ctx)
    ws3 = w.service.reopen_element(ElementReopenCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        element_ref=ref, operator_ref="U1", idempotency_key="KRO2",
    ))
    el = _element(ws3, ref)
    assert el.process_status is ES.PENDING_CONFIRMATION and el.version == 2
    history = w.service.read_element_history(ctx, ref)
    assert any(rec.action == "reflow" for rec in history.records)


def test_reopen_pending_rejected():
    w = _wiring()
    ctx, ws = _workspace(w)
    with pytest.raises(RejectedTransition):
        w.service.reopen_element(ElementReopenCommand(
            parse_context_ref=ctx, workspace_version=ws.workspace_version,
            element_ref=ws.elements[0].id, operator_ref="U1", idempotency_key="KRO3",
        ))


# ============================================================================
# E5：有效集合与下游门禁
# ============================================================================

def test_gate_only_confirmed_expression_elements():
    w = _wiring()
    ctx, ws = _workspace(w)
    assert not any(a.key == "start_item_formation" and a.enabled for a in ws.available_actions)
    expr = next(e for e in ws.elements
                if e.element_type.value in ("functional_requirement", "quality_attribute", "constraint"))
    ws2 = _decide(w, ctx, [expr.id], "confirm", key="KG1")
    assert any(a.key == "start_item_formation" and a.enabled for a in ws2.available_actions)


# ============================================================================
# 版本冲突与默认拒绝
# ============================================================================

def test_stale_version_rejected_across_commands():
    w = _wiring()
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    w.process_records.bump_workspace_version(ctx)
    with pytest.raises(RejectedTransition):
        w.service.decide_elements(ElementDecisionCommand(
            parse_context_ref=ctx, workspace_version=ws.workspace_version,
            element_refs=[ref], decision="confirm", operator_ref="U1", idempotency_key="KX1",
        ))
    with pytest.raises(RejectedTransition):
        w.service.edit_element(ElementEditCommand(
            parse_context_ref=ctx, workspace_version=ws.workspace_version,
            element_ref=ref, edit_type="revise_expression", new_content="X",
            operator_ref="U1", idempotency_key="KX2",
        ))


# ============================================================================
# P04：AI 执行（草案先行 + 失败停靠）
# ============================================================================

def test_ai_execution_forms_draft():
    w = _wiring()
    ctx, ws = _workspace(w)
    r = w.service.submit_element_ai_execution(ElementAiExecutionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        operation_type="revise_expression", target_element_refs=[ws.elements[0].id],
        execution_instruction="补充验收口径", operator_ref="U1", idempotency_key="KE10",
    ))
    assert r.status == "accepted"
    ws2 = _refresh(w, ctx)
    assert ws2.change_draft is not None and ws2.change_draft.create_gate == "creatable"
    assert all(not e.superseded for e in ws2.elements)  # 确认前不生效


def test_ai_execution_failed_stops_draft():
    w = _wiring(executor=StubElementOperationExecutor(failed=True))
    ctx, ws = _workspace(w)
    w.service.submit_element_ai_execution(ElementAiExecutionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        operation_type="revise_expression", target_element_refs=[ws.elements[0].id],
        execution_instruction="补充验收口径", operator_ref="U1", idempotency_key="KE11",
    ))
    ws2 = _refresh(w, ctx)
    assert ws2.change_draft is not None and ws2.change_draft.create_gate == "stopped"
    assert not any(o.key == "confirm_change" and o.enabled for o in ws2.available_operations)


def test_ai_revision_failed_keeps_pending_with_trace():
    """修订 AI 失败：状态不变（对话轮次失败），留失败记录可重试。"""
    w = _wiring(executor=StubElementOperationExecutor(failed=True))
    ctx, ws = _workspace(w)
    ref = ws.elements[0].id
    w.service.revise_element(ElementRevisionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=ref, mode="ai", instruction="改写",
        operator_ref="U1", idempotency_key="KV10",
    ))
    ws2 = _refresh(w, ctx)
    assert _element(ws2, ref).process_status is ES.PENDING_CONFIRMATION
    history = w.service.read_element_history(ctx, ref)
    assert any(rec.action == "revision_failed" for rec in history.records)


# ============================================================================
# 持久化（SQLite）：确认生命周期全链路
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


def test_sql_lifecycle_roundtrip(session):
    p = Project(name="demo")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text=RAW, source_note="访谈")
    session.add(mat)
    session.flush()
    session.add(IntakeRecord(
        project_id=p.id, context_ref=uuid.uuid4(),
        intake_conclusion="accepted", material_ref=mat.id,
    ))
    session.flush()

    svc = build_sql_analysis_service(session, auto_complete=True)
    r = svc.submit_element_recognition(ElementRecognitionCommand(
        project_ref=str(p.id), material_ref=str(mat.id),
        operator_ref="U1", idempotency_key="SK1",
    ))
    session.commit()
    ctx = r.parse_context_ref
    ws = svc.read_element_workspace(ctx)
    assert all(e.process_status is ES.PENDING_CONFIRMATION for e in ws.elements)

    # 复核 = 对话轮次：结论回填、状态不变
    # 取样排除建议剔除候选：后续要对这一条 confirm，候选会被 C1 守卫拒绝；
    # elements 同时刻落库读回顺序由 UUID 兜底，按下标取到哪一条是随机的
    ref = next(e.id for e in ws.elements if e.model_verdict.value != "suspected_noise")
    svc.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        target_element_refs=[ref], review_intent="复核",
        operator_ref="U1", idempotency_key="SK2",
    ))
    session.commit()
    ws2 = svc.read_element_workspace(ctx)
    el = next(e for e in ws2.elements if e.id == ref)
    assert el.process_status is ES.PENDING_CONFIRMATION and el.review_conclusion is not None

    # 直接确认（复核不阻塞裁决）
    ws3 = svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        element_refs=[ref], decision="confirm", operator_ref="U1", idempotency_key="SK3",
    ))
    session.commit()
    el3 = next(e for e in ws3.elements if e.id == ref)
    assert el3.process_status is ES.CONFIRMED

    # 勘误 → 新来源版本 + 受影响回待确认（覆盖 SQL 路径）
    ws4 = svc.read_element_workspace(ctx)
    ws5 = svc.material_erratum(MaterialErratumCommand(
        parse_context_ref=ctx, workspace_version=ws4.workspace_version,
        old_text="30秒", new_text="10秒", operator_ref="U1", idempotency_key="SK4",
    ))
    session.commit()
    assert ws5.material_canvas.source_version == 2
    affected = [e for e in ws5.elements if "10秒" in e.content]
    assert affected and all(e.process_status is ES.PENDING_CONFIRMATION for e in affected)

    # 历史留痕落库
    history = svc.read_element_history(ctx, ref)
    assert history.records
    rows = session.scalars(select(RequirementElement)).all()
    assert rows  # LDM-005 持久化
