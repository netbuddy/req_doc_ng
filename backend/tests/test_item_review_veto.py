"""问题否决与采纳前逐条改稿（T20260720 / AEP-116 + AEP-034 扩展）测试义务。

覆盖：
- A1 否决留痕：登记后持久可读、可查账目；撤销后恢复计入阻断。
- A2 确定性拦截：新一轮报同一个问题（含换措辞引用同一处证据）照样被标记、不计入阻断；
  一轮阻断问题被逐条否决且一条不剩时，条目可不经覆盖确认直接确认；判据是纯函数、无模型参与。
- A3 提示词注入：诊断上下文与渲染出的提示词含被否决问题与用户理由。
- A4 可编辑采纳：改稿被应用、AI 原案不丢；空稿/未勾选/不存在的点一律拒绝并给白话理由。

设计事实源：docs/40 domains/DS-001（data.md LDM-009）；任务卡
harness-engineering/worktree-pool/tasks/T20260720-review-point-veto-and-edit.card.md。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import DiagnosedFinding, ItemVerdictOutcome
from app.adapters.prompts.environment import render_pair
from app.api.schemas import FindingVetoCommand, ItemConfirmationCommand
from app.db.models import ItemDiagnosisRound
from app.domain.enums import VerdictDecision, VerdictKind
from app.domain.errors import InvalidInput, NotFound
from app.services.item_review import (
    _VETO_MATCH_EXACT,
    _VETO_MATCH_NARROWED,
    _veto_hit,
    _veto_key,
    _veto_match,
    _veto_norm,
)

from tests.test_item_review import (  # 复用既有夹具与助手（同一被测服务，口径单一来源）
    _adjudicate,
    _item_view,
    _run_diagnosis,
    _seed_pending_items,
    _set_expression,
    _version,
    _workspace,
    session,  # noqa: F401  pytest fixture
)

# 两个 stub 演示规则词：「尽快」→ INCOSE-R7（span=尽快），「超时」→ SMELL-UNDEF（span=超时）。
# 用它们造出带规则码与证据片段的发现项——只有可指纹化的发现项才谈得上否决。
VAGUE_EXPRESSION = "系统应尽快完成导出，且超时不得发生"


class _FixedDiagnoser:
    """按给定结论逐轮返回（测试要模拟「模型换个措辞把同一个问题再报一遍」）。"""

    def __init__(self, *outcomes: ItemVerdictOutcome) -> None:
        self._outcomes = list(outcomes)
        self.seen_excluded: list[list[dict]] = []

    def diagnose(
        self, project_ref, diagnosis_mode, item, sources, raw_text, revisions,
        prior_findings, excluded_points=None, thread_context="", business_sources=None, attestation=None,
    ) -> ItemVerdictOutcome:
        self.seen_excluded.append(list(excluded_points or []))
        return self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]


def _revise_outcome(*spans: tuple[str, str, str]) -> ItemVerdictOutcome:
    """构造一个「建议修订」结论：每个 (rule_code, span, summary) 一条阻断发现项。

    修订点只挂第一条发现项——这样「有的点被否决、有的点没有」两种情形都能造出来。
    """
    findings = tuple(
        DiagnosedFinding(
            "untestable", summary, "stub 依据",
            rule_code=rule, evidence_span=span, severity="medium", dimension="verifiable",
        )
        for rule, span, summary in spans
    )
    return ItemVerdictOutcome(
        verdict_kind="revise",
        verdict_summary="表达存在待改问题，建议修订。",
        findings=findings,
        revision_points=({
            "point_ref": "P1", "label": "补充可验证口径", "finding_index": 0,
            "find": VAGUE_EXPRESSION, "replace": VAGUE_EXPRESSION + "（三秒内）",
            "basis": "stub 依据", "group": None,
        },),
        supplement_gaps=(), basis="stub 条目诊断完成",
    )


def _two_point_outcome() -> ItemVerdictOutcome:
    """两个修订点各挂一条发现项（用来造「勾一个、不勾另一个」的情形）。"""
    return ItemVerdictOutcome(
        verdict_kind="revise", verdict_summary="两处待改。",
        findings=(
            DiagnosedFinding("untestable", "「尽快」不可测。", "stub 依据",
                             rule_code="INCOSE-R7", evidence_span="尽快",
                             severity="medium", dimension="verifiable"),
            DiagnosedFinding("missing_field", "超时阈值未定义。", "stub 依据",
                             rule_code="SMELL-UNDEF", evidence_span="超时",
                             severity="medium", dimension="complete"),
        ),
        revision_points=(
            {"point_ref": "P1", "label": "量化时限", "finding_index": 0,
             "find": "尽快", "replace": "在三秒内", "basis": "stub", "group": None},
            {"point_ref": "P2", "label": "补超时阈值", "finding_index": 1,
             "find": "超时", "replace": "超过十秒", "basis": "stub", "group": None},
        ),
        supplement_gaps=(), basis="stub 条目诊断完成",
    )


def _grouped_two_point_outcome() -> ItemVerdictOutcome:
    """两个修订点同属一个联动组（group=g1），各挂一条发现项（造 C4：否决其一，整组联动）。"""
    return ItemVerdictOutcome(
        verdict_kind="revise", verdict_summary="两处语法必须联动。",
        findings=(
            DiagnosedFinding("untestable", "「尽快」不可测。", "stub 依据",
                             rule_code="INCOSE-R7", evidence_span="尽快",
                             severity="medium", dimension="verifiable"),
            DiagnosedFinding("missing_field", "超时阈值未定义。", "stub 依据",
                             rule_code="SMELL-UNDEF", evidence_span="超时",
                             severity="medium", dimension="complete"),
        ),
        revision_points=(
            {"point_ref": "P1", "label": "量化时限", "finding_index": 0,
             "find": "尽快", "replace": "在三秒内", "basis": "stub", "group": "g1"},
            {"point_ref": "P2", "label": "补超时阈值", "finding_index": 1,
             "find": "超时", "replace": "超过十秒", "basis": "stub", "group": "g1"},
        ),
        supplement_gaps=(), basis="stub 条目诊断完成",
    )


def _grouped_plus_free_outcome() -> ItemVerdictOutcome:
    """P1/P2 同联动组，P3 独立。用来验证「整组剔除只剔那一组、组外的点照常采纳」。"""
    return ItemVerdictOutcome(
        verdict_kind="revise", verdict_summary="两处联动加一处独立。",
        findings=(
            DiagnosedFinding("untestable", "「尽快」不可测。", "stub 依据",
                             rule_code="INCOSE-R7", evidence_span="尽快",
                             severity="medium", dimension="verifiable"),
            DiagnosedFinding("missing_field", "超时阈值未定义。", "stub 依据",
                             rule_code="SMELL-UNDEF", evidence_span="超时",
                             severity="medium", dimension="complete"),
            DiagnosedFinding("ambiguous_expression", "「发生」语义偏弱。", "stub 依据",
                             rule_code="MODAL-WEAK", evidence_span="发生",
                             severity="low", dimension="unambiguous"),
        ),
        revision_points=(
            {"point_ref": "P1", "label": "量化时限", "finding_index": 0,
             "find": "尽快", "replace": "在三秒内", "basis": "stub", "group": "g1"},
            {"point_ref": "P2", "label": "补超时阈值", "finding_index": 1,
             "find": "超时", "replace": "超过十秒", "basis": "stub", "group": "g1"},
            {"point_ref": "P3", "label": "改强制语气", "finding_index": 2,
             "find": "发生", "replace": "出现", "basis": "stub", "group": None},
        ),
        supplement_gaps=(), basis="stub 条目诊断完成",
    )


def _nested_span_outcome() -> ItemVerdictOutcome:
    """同一轮两条**不同**的问题，规则码相同、证据片段一条嵌套在另一条里（C1 的数据形状）。

    真实模型下常见：一条问题引整句、另一条只引其中的从句。两个片段在基准表达里各自恰好
    出现一次（适配器 _anchor_once 的契约），所以两条都可指纹化。
    """
    return ItemVerdictOutcome(
        verdict_kind="revise", verdict_summary="整句与其中一处各有问题。",
        findings=(
            DiagnosedFinding("untestable", "整句缺可观测结果。", "stub 依据",
                             rule_code="INCOSE-R7", evidence_span="系统应尽快完成导出",
                             severity="medium", dimension="verifiable"),
            DiagnosedFinding("untestable", "「尽快」不可测。", "stub 依据",
                             rule_code="INCOSE-R7", evidence_span="尽快完成导出",
                             severity="high", dimension="verifiable"),
        ),
        # 只挂一个修订点：两条发现项的证据片段嵌套，若各挂一个点会撞上「修订点跨度不得
        # 重叠」这条既有硬约束（_verdict_guard），整轮会被拒收。
        revision_points=(
            {"point_ref": "P1", "label": "补可观测结果", "finding_index": 0,
             "find": "系统应尽快完成导出", "replace": "系统应在三秒内完成导出", "basis": "stub",
             "group": None},
        ),
        supplement_gaps=(), basis="stub 条目诊断完成",
    )


def _mixed_no_blocker_outcome() -> ItemVerdictOutcome:
    """一条 no_blocker（非阻断）＋一条真阻断问题，都可指纹化（验后端阻断口径跳过 no_blocker）。"""
    return ItemVerdictOutcome(
        verdict_kind="revise", verdict_summary="含一条非阻断项。",
        findings=(
            DiagnosedFinding("no_blocker", "来源依据可定位。", "stub 依据",
                             rule_code="INCOSE-R7", evidence_span="尽快",
                             severity="low", dimension="verifiable"),
            DiagnosedFinding("missing_field", "超时阈值未定义。", "stub 依据",
                             rule_code="SMELL-UNDEF", evidence_span="超时",
                             severity="medium", dimension="complete"),
        ),
        revision_points=(
            {"point_ref": "P1", "label": "补超时阈值", "finding_index": 1,
             "find": "超时", "replace": "超过十秒", "basis": "stub", "group": None},
        ),
        supplement_gaps=(), basis="stub 条目诊断完成",
    )


def _veto(svc, session, w, item_ref, finding_ref, reason=None, key=None):
    workspace = svc.record_finding_veto(FindingVetoCommand(
        project_ref=w["project"], item_ref=item_ref, action="veto",
        finding_ref=finding_ref, reason=reason,
        operator_ref="U1", idempotency_key=key or f"veto-{uuid.uuid4()}",
    ))
    session.commit()
    return workspace


def _restore(svc, session, w, item_ref, veto_ref):
    workspace = svc.record_finding_veto(FindingVetoCommand(
        project_ref=w["project"], item_ref=item_ref, action="restore",
        veto_ref=veto_ref, operator_ref="U1", idempotency_key=f"undo-{uuid.uuid4()}",
    ))
    session.commit()
    return workspace


def _diagnosed(session, diagnoser=None):
    """已诊断一轮的条目：返回 (svc, w, item_ref, 站立结论)。"""
    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    _set_expression(session, item_ref, VAGUE_EXPRESSION)
    svc, _ = _run_diagnosis(session, w, diagnoser=diagnoser, item_refs=[item_ref])
    verdict = _item_view(_workspace(svc, w), item_ref).current_verdict
    assert verdict is not None and verdict.verdict_kind is VerdictKind.REVISE
    return svc, w, item_ref, verdict


def _blocking(verdict):
    return [f for f in verdict.findings if f.finding_type.value != "no_blocker"]


def _finding_with_span(verdict, span: str):
    """按证据片段取发现项——同事务写入的发现项 created_at 相同，读出序不可依赖。"""
    return next(f for f in verdict.findings if f.evidence_span == span)


# ============================================================================
# 匹配判据（纯函数：无数据库、无模型）
# ============================================================================

def test_veto_norm_folds_whitespace_only():
    assert _veto_norm("  尽快   完成 ") == "尽快 完成"
    assert _veto_norm(None) == ""
    # 刻意不折叠大小写与全半角：折了用户就无法预期两个看着不同的片段为何算同一个问题
    assert _veto_norm("ABC") != _veto_norm("abc")


def test_veto_key_falls_back_to_finding_type_without_rule_code():
    assert _veto_key("INCOSE-R7", "尽快", "untestable") == ("INCOSE-R7", "尽快")
    assert _veto_key(None, "尽快", "untestable") == ("type:untestable", "尽快")
    # 证据片段缺失 = 无可复算的定位依据 → 不可指纹化
    assert _veto_key("INCOSE-R7", "", "untestable") is None
    assert _veto_key("INCOSE-R7", None, "untestable") is None


def test_veto_match_hits_exact_quote_and_narrowed_quote():
    a = _veto_key("INCOSE-R7", "应尽快完成审批流程", "untestable")
    # 完全相等
    assert _veto_match(a, _veto_key("INCOSE-R7", "应尽快完成审批流程", "untestable")) == _VETO_MATCH_EXACT
    # 新片段落在已否决片段之内（模型下一轮少截了几个字）
    assert _veto_match(a, _veto_key("INCOSE-R7", "尽快完成审批", "untestable")) == _VETO_MATCH_NARROWED
    assert not _veto_hit(a, _veto_key("SMELL-UNDEF", "应尽快完成审批流程", "untestable"))  # 规则码不同
    assert not _veto_hit(a, _veto_key("INCOSE-R7", "另一处完全不同的表述", "untestable"))


def test_veto_match_containment_requires_minimum_length():
    """过短的片段只认完全相等——两字碎片的包含关系证明不了是同一处证据。"""
    long_veto = _veto_key("INCOSE-R7", "应尽快完成导出", "untestable")
    assert _veto_hit(long_veto, _veto_key("INCOSE-R7", "应尽快完成导出", "untestable"))
    assert not _veto_hit(long_veto, _veto_key("INCOSE-R7", "尽快", "untestable"))


def test_veto_match_rejects_a_wider_span_as_a_new_problem():
    """C1/P1 根治的方向不对称（2026-07-21 方案门拍板）：新片段真包含已否决片段时不命中。

    多出来的那截是用户当初没看过的内容，可能是一个真的新问题（P1：用户否决了针对「响应
    时间」的问题，下一轮报「响应时间与吞吐量均未定义阈值」），压掉它等于替用户判定。
    代价不对称：漏命中只是让用户再标一次，误命中会让没看过的问题被判成不成立并放行确认。
    """
    narrow = _veto_key("INCOSE-R7", "响应时间", "untestable")
    wider = _veto_key("INCOSE-R7", "响应时间与吞吐量均未定义阈值", "untestable")
    assert not _veto_hit(narrow, wider)
    # 反方向仍然命中（同一处证据被截窄）——单靠方向判不出「同一轮的两条不同问题」，
    # 那由 _mark_vetoes 的同轮唯一命中兜住，见 test_veto_of_outer_span_leaves_nested_sibling_blocking。
    assert _veto_match(wider, narrow) == _VETO_MATCH_NARROWED


def test_veto_key_missing_rule_code_next_round_currently_misses():
    """C6 覆盖形状（钉现行为，未实施 C6 修复）：同一处证据，上一轮带规则码、下一轮缺规则码时，
    真码键 (INCOSE-R7, span) 与退化键 (type:untestable, span) 在第一道规则键比较就判不等，
    已登记的否决静默失效、同一个问题重新阻塞。修复（一条否决同持两把键）另行处置。
    """
    with_code = _veto_key("INCOSE-R7", "尽快完成导出", "untestable")
    without_code = _veto_key(None, "尽快完成导出", "untestable")
    assert without_code == ("type:untestable", "尽快完成导出")
    assert not _veto_hit(with_code, without_code)


# ============================================================================
# A1 否决留痕：登记持久、账目可查、撤销恢复
# ============================================================================

def test_veto_marks_finding_and_persists(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    blocking = _blocking(verdict)
    assert len(blocking) == 2 and all(f.can_veto for f in blocking)

    workspace = _veto(svc, session, w, item_ref, blocking[0].finding_ref, reason="这处业务上就是这么说的")
    view = _item_view(workspace, item_ref)
    marked = next(f for f in view.current_verdict.findings if f.finding_ref == blocking[0].finding_ref)
    assert marked.vetoed and marked.veto_reason == "这处业务上就是这么说的" and marked.veto_ref

    # 换一个服务实例重读（模拟刷新/重进）：留痕从库里读出来，不是内存态
    fresh = _item_view(_workspace(svc, w), item_ref)
    assert next(f for f in fresh.current_verdict.findings
                if f.finding_ref == blocking[0].finding_ref).vetoed
    assert len(fresh.finding_vetoes) == 1
    assert fresh.finding_vetoes[0].reason == "这处业务上就是这么说的"
    assert fresh.finding_vetoes[0].revoked is False
    # 还剩一条问题成立 → 不满足直接确认条件
    assert fresh.current_verdict.blocking_finding_count == 1
    assert fresh.current_verdict.all_blocking_findings_vetoed is False


def test_veto_restore_puts_the_problem_back(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    blocking = _blocking(verdict)
    workspace = _veto(svc, session, w, item_ref, blocking[0].finding_ref, reason="不是问题")
    veto_ref = _item_view(workspace, item_ref).finding_vetoes[0].veto_ref

    workspace = _restore(svc, session, w, item_ref, veto_ref)
    view = _item_view(workspace, item_ref)
    assert all(not f.vetoed for f in view.current_verdict.findings)
    assert view.current_verdict.blocking_finding_count == 2
    # 撤销写时间而不删行：否决过又改主意，这个事实要留住
    assert len(view.finding_vetoes) == 1 and view.finding_vetoes[0].revoked is True


def test_veto_is_idempotent_and_not_double_counted(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    finding_ref = _blocking(verdict)[0].finding_ref
    key = f"veto-{uuid.uuid4()}"
    _veto(svc, session, w, item_ref, finding_ref, key=key)
    _veto(svc, session, w, item_ref, finding_ref, key=key)          # 同一幂等键重放
    _veto(svc, session, w, item_ref, finding_ref)                    # 同一个问题再标一次
    assert len(_item_view(_workspace(svc, w), item_ref).finding_vetoes) == 1


def test_veto_rejected_when_problem_has_no_locatable_evidence(session):
    """没有证据片段就无法跨轮认出同一个问题——宁可拒绝，也不拿问题摘要当匹配键。"""
    diagnoser = _FixedDiagnoser(ItemVerdictOutcome(
        verdict_kind="revise", verdict_summary="有问题。",
        findings=(DiagnosedFinding("untestable", "缺可验证口径。", "stub 依据"),),
        revision_points=({
            "point_ref": "P1", "label": "补口径", "finding_index": 0,
            "find": VAGUE_EXPRESSION, "replace": VAGUE_EXPRESSION + "（三秒内）",
            "basis": "stub", "group": None,
        },),
        supplement_gaps=(), basis="stub",
    ))
    svc, w, item_ref, verdict = _diagnosed(session, diagnoser)
    finding = _blocking(verdict)[0]
    assert finding.can_veto is False
    with pytest.raises(InvalidInput):
        _veto(svc, session, w, item_ref, finding.finding_ref)


# ============================================================================
# A2 确定性拦截：新一轮重提同一个问题照样被标记
# ============================================================================

def _reject_then_rediagnose(svc, session, w, item_ref, verdict, diagnoser):
    """拒绝当前结论（条目回到可诊断）后再跑一轮，模拟「模型又报了一遍」。"""
    _adjudicate(svc, session, w, item_ref, verdict.round_ref,
                VerdictDecision.REJECTED, reason="先看看下一轮")
    svc2, _ = _run_diagnosis(session, w, diagnoser=diagnoser, item_refs=[item_ref])
    return svc2, _item_view(_workspace(svc2, w), item_ref)


def test_repeat_of_vetoed_problem_is_marked_in_the_next_round(session):
    repeat = _revise_outcome(
        ("INCOSE-R7", "尽快完成导出", "「尽快」不可测。"),
        ("SMELL-UNDEF", "超时不得发生", "超时阈值未定义。"),
    )
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(repeat))
    for f in _blocking(verdict):
        _veto(svc, session, w, item_ref, f.finding_ref, reason="业务已确认无需量化")

    svc2, view = _reject_then_rediagnose(svc, session, w, item_ref, verdict, _FixedDiagnoser(repeat))
    fresh = view.current_verdict
    assert fresh.round_ref != verdict.round_ref                  # 确实是新的一轮
    assert all(f.vetoed for f in _blocking(fresh))               # 同一个问题照样被标记
    assert fresh.blocking_finding_count == 0
    assert fresh.all_blocking_findings_vetoed is True
    assert all(p.vetoed for p in fresh.revision_points)          # 派生的修订点一并标灰
    assert "可以直接确认" in view.status_note


def test_narrowed_quote_of_the_same_evidence_still_hits(session):
    """模型下一轮少截几个字引用同一处证据（规则码不变）→ 包含规则命中，不算新问题。"""
    first = _revise_outcome(("INCOSE-R7", "系统应尽快完成导出", "「尽快」不可测。"))
    narrowed = _revise_outcome(("INCOSE-R7", "尽快完成导出", "该表述仍不可测。"))
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(first))
    _veto(svc, session, w, item_ref, _blocking(verdict)[0].finding_ref, reason="不是问题")

    _svc2, view = _reject_then_rediagnose(svc, session, w, item_ref, verdict, _FixedDiagnoser(narrowed))
    assert view.current_verdict.all_blocking_findings_vetoed is True


def test_widened_quote_is_reported_again_instead_of_being_swallowed(session):
    """P1 回归护栏：下一轮的片段比已否决的更宽 → 当作新问题重新提出，不被静默压掉。

    多出来的「，且超时不得发生」是用户当初没裁定过的内容。判据宁可让用户再标一次，
    也不替他判定一个他没看过的问题不成立（那会连带打开「直接确认」的门）。
    """
    first = _revise_outcome(("INCOSE-R7", "尽快完成导出", "「尽快」不可测。"))
    widened = _revise_outcome(("INCOSE-R7", "尽快完成导出，且超时不得发生", "范围更大的新问题。"))
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(first))
    _veto(svc, session, w, item_ref, _blocking(verdict)[0].finding_ref, reason="不是问题")

    _svc2, view = _reject_then_rediagnose(svc, session, w, item_ref, verdict, _FixedDiagnoser(widened))
    assert view.current_verdict.all_blocking_findings_vetoed is False
    assert view.current_verdict.blocking_finding_count == 1


def test_different_problem_in_the_next_round_still_blocks(session):
    """否决只对被否决的那个问题生效，不是「以后都别提了」。"""
    first = _revise_outcome(("INCOSE-R7", "尽快完成导出", "「尽快」不可测。"))
    other = _revise_outcome(("SMELL-UNDEF", "超时不得发生", "超时阈值未定义。"))
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(first))
    _veto(svc, session, w, item_ref, _blocking(verdict)[0].finding_ref, reason="不是问题")

    _svc2, view = _reject_then_rediagnose(svc, session, w, item_ref, verdict, _FixedDiagnoser(other))
    assert view.current_verdict.all_blocking_findings_vetoed is False
    assert view.current_verdict.blocking_finding_count == 1


# ============================================================================
# A2/A5 门禁衔接：全部否决后可直接确认（且不是覆盖确认）
# ============================================================================

def _confirm(svc, session, w, item_ref, override=False, reason=None, key=None):
    result = svc.confirm_item(ItemConfirmationCommand(
        project_ref=w["project"], item_ref=item_ref, override=override, reason=reason,
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=key or f"cfm-{uuid.uuid4()}",
    ))
    session.commit()
    return result


def test_confirm_without_override_after_all_problems_vetoed(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    for f in _blocking(verdict):
        _veto(svc, session, w, item_ref, f.finding_ref, reason="业务已确认")
    view = _item_view(_workspace(svc, w), item_ref)
    assert next(a for a in view.available_actions
                if a.key == "confirm_without_override").enabled is True

    result = _confirm(svc, session, w, item_ref)
    assert result.status == "confirmed"
    round_ = session.get(ItemDiagnosisRound, uuid.UUID(verdict.round_ref))
    # 结论按拒绝收口并写明理由，但不是覆盖：不打覆盖标记（效能统计的覆盖率不受污染）
    assert round_.adjudication_decision == VerdictDecision.REJECTED.value
    assert round_.overridden is False
    assert "逐条裁定为不是问题（2 条）" in round_.adjudication_reason
    # 拍板第 3 条核心承诺：库里的 verdict_kind 一个字不改（直接确认不改写结论类型）
    assert round_.verdict_kind == VerdictKind.REVISE.value


def test_confirm_without_override_refused_while_a_problem_stands(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    _veto(svc, session, w, item_ref, _blocking(verdict)[0].finding_ref)  # 只否决一条
    view = _item_view(_workspace(svc, w), item_ref)
    assert next(a for a in view.available_actions
                if a.key == "confirm_without_override").enabled is False
    assert _confirm(svc, session, w, item_ref).status == "rejected_precheck"


def test_veto_of_outer_span_leaves_nested_sibling_blocking(session):
    """C1 回归护栏：同一轮两条不同的问题、规则码相同、证据片段嵌套，否决其一不得连带消解另一条。

    同一轮里的两条发现项按定义就是模型分别报出的两个问题，一次否决只认领其中一条
    （_mark_vetoes 的同轮唯一命中）。「直接确认」的门因此仍然关着。
    """
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_nested_span_outcome()))
    assert len(_blocking(verdict)) == 2
    outer = _finding_with_span(verdict, "系统应尽快完成导出")  # 整句那条（P1 挂在它上面）
    _veto(svc, session, w, item_ref, outer.finding_ref, reason="整句表述业务上没问题")

    view = _item_view(_workspace(svc, w), item_ref)
    marked = [f.finding_ref for f in view.current_verdict.findings if f.vetoed]
    assert marked == [outer.finding_ref]  # 只标记用户点的那一条
    assert view.current_verdict.blocking_finding_count == 1
    assert view.current_verdict.all_blocking_findings_vetoed is False
    # 被否决问题的修订点照常标灰，从句那条问题没有修订点，不受影响
    assert [p.point_ref for p in view.current_verdict.revision_points if p.vetoed] == ["P1"]
    assert next(a for a in view.available_actions
                if a.key == "confirm_without_override").enabled is False
    assert _confirm(svc, session, w, item_ref).status == "rejected_precheck"


def test_veto_of_inner_span_leaves_the_wider_sibling_blocking(session):
    """C1 的反方向：否决从句那条，整句那条（范围更宽）同样不被连带消解。"""
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_nested_span_outcome()))
    inner = _finding_with_span(verdict, "尽快完成导出")
    _veto(svc, session, w, item_ref, inner.finding_ref, reason="「尽快」在本业务里有共识")

    view = _item_view(_workspace(svc, w), item_ref)
    assert [f.finding_ref for f in view.current_verdict.findings if f.vetoed] == [inner.finding_ref]
    assert view.current_verdict.blocking_finding_count == 1
    # 整句那条没被否决，它的修订点照常可采纳
    assert [p.point_ref for p in view.current_verdict.revision_points if p.vetoed] == []
    assert _confirm(svc, session, w, item_ref).status == "rejected_precheck"


def test_confirm_reason_counts_user_decisions_not_matched_findings(session):
    """C1 次生：留痕的条数＝用户实际裁定过的否决行数，与界面上他点过的次数自洽。"""
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_nested_span_outcome()))
    for f in _blocking(verdict):
        _veto(svc, session, w, item_ref, f.finding_ref, reason="业务已确认")

    assert _confirm(svc, session, w, item_ref).status == "confirmed"
    round_ = session.get(ItemDiagnosisRound, uuid.UUID(verdict.round_ref))
    assert "逐条裁定为不是问题（2 条）" in round_.adjudication_reason


def test_confirm_gate_keeps_its_other_checks(session):
    """否决只消解「问题是否成立」这一条；版本一致性等判据一条也没放宽。"""
    svc, w, item_ref, verdict = _diagnosed(session)
    for f in _blocking(verdict):
        _veto(svc, session, w, item_ref, f.finding_ref)
    result = svc.confirm_item(ItemConfirmationCommand(
        project_ref=w["project"], item_ref=item_ref, override=False, reason=None,
        workspace_version="999", operator_ref="U1", idempotency_key=f"cfm-{uuid.uuid4()}",
    ))
    assert result.status == "rejected_precheck" and "版本" in result.next_action


def test_override_confirm_still_marks_overridden(session):
    """覆盖确认这条既有通道原样不变（含覆盖标记与理由必填）。"""
    svc, w, item_ref, verdict = _diagnosed(session)
    with pytest.raises(InvalidInput):
        _confirm(svc, session, w, item_ref, override=True, reason="  ")
    assert _confirm(svc, session, w, item_ref, override=True, reason="人工判定可通过").status == "confirmed"
    assert session.get(ItemDiagnosisRound, uuid.UUID(verdict.round_ref)).overridden is True


# ============================================================================
# A3 提示词注入
# ============================================================================

def test_vetoed_problem_enters_diagnosis_context_and_prompt(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    finding = _blocking(verdict)[0]
    _veto(svc, session, w, item_ref, finding.finding_ref, reason="行业惯例如此")

    entries = svc._excluded_points_of(item_ref)
    vetoed = [e for e in entries if e.get("kind") == "vetoed_finding"]
    assert len(vetoed) == 1
    assert vetoed[0]["rule_code"] == finding.rule_code
    assert vetoed[0]["evidence_span"] == finding.evidence_span
    assert vetoed[0]["user_reason"] == "行业惯例如此"

    _system, user = render_pair(
        "item_diagnosis", project_ref="P", diagnosis_mode="standard",
        item="{}", sources="[]", business_sources="[]", raw_text="", revisions="[]",
        attestation="", prior_findings="[]", thread_context="（无）", output_schema="{}",
        excluded_points=json.dumps(entries, ensure_ascii=False),
    )
    assert "vetoed_finding" in user and "行业惯例如此" in user
    assert "不许再报这个问题" in user


# ============================================================================
# A4 采纳前逐条可编辑
# ============================================================================

def _adopt_with_edits(svc, session, w, item_ref, verdict, edits, selected=None):
    from app.api.schemas import VerdictAdjudicationCommand
    workspace = svc.adjudicate_verdict(VerdictAdjudicationCommand(
        project_ref=w["project"], item_ref=item_ref, round_ref=verdict.round_ref,
        decision=VerdictDecision.ADOPTED,
        selected_point_refs=selected if selected is not None else [p.point_ref for p in verdict.revision_points],
        point_edits=edits, workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"adj-{uuid.uuid4()}",
    ))
    session.commit()
    return workspace


def test_adopt_applies_user_final_text_and_keeps_ai_original(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    point = verdict.revision_points[0]
    final_text = "系统应在三秒内完成导出，且超时不得发生"

    workspace = _adopt_with_edits(svc, session, w, item_ref, verdict, {point.point_ref: final_text})
    view = _item_view(workspace, item_ref)
    assert view.expression == final_text                       # 条目落的是用户终稿

    historic = next(v for v in view.verdict_history if v.round_ref == verdict.round_ref)
    assert historic.adjudication.point_edits == {point.point_ref: final_text}
    # AI 原案原样保留在同一轮的修订点里，两者并存可对照
    assert next(p for p in historic.revision_points if p.point_ref == point.point_ref).replace == point.replace
    assert point.replace != final_text


def test_adopt_without_edits_behaves_exactly_as_before(session):
    svc, w, item_ref, verdict = _diagnosed(session)
    point = verdict.revision_points[0]
    workspace = _adopt_with_edits(svc, session, w, item_ref, verdict, None)
    view = _item_view(workspace, item_ref)
    assert view.expression == point.replace
    historic = next(v for v in view.verdict_history if v.round_ref == verdict.round_ref)
    assert historic.adjudication.point_edits == {}


@pytest.mark.parametrize("edits, fragment", [
    ({"P-not-exist": "随便改改"}, "对应不上"),
    ({"__EMPTY__": "   "}, "内容是空的"),
    ({"__FIND__": None}, "改回了原文"),
])
def test_adopt_rejects_bad_edits_with_plain_language(session, edits, fragment):
    svc, w, item_ref, verdict = _diagnosed(session)
    point = verdict.revision_points[0]
    payload = {}
    for key, value in edits.items():
        if key == "__EMPTY__":
            payload[point.point_ref] = value
        elif key == "__FIND__":
            payload[point.point_ref] = point.find
        else:
            payload[key] = value
    with pytest.raises(InvalidInput) as exc:
        _adopt_with_edits(svc, session, w, item_ref, verdict, payload)
    assert fragment in str(exc.value)


def test_adopt_drops_a_point_whose_problem_was_vetoed(session):
    """标为不是问题之后，它的改法不能再被应用——卡面写着「不会应用」就得真不应用（C4）。

    改法：不再报错要求用户去操作一个界面上已不存在的「恢复计入」复选框，而是把被否决的点剔除。
    这里被选的只有 P1（其问题已被否决）、又无同组的其它点，剔除后无点可采纳，给出如实的空集说明。
    （槽内浏览器走查发现：标记后结论卡不重挂载，初选里还留着那个点，直采会把它应用掉。）
    """
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_two_point_outcome()))
    # 按 P1 自己指向的发现项取，不按读出序取第一条——同事务写入的发现项 created_at 相同，
    # 读出序退化为随机 UUID 序，按序号取会让本例时灵时不灵（本例最初就是这么写的，在全量
    # 跑里随机红过一次）。
    p1 = next(p for p in verdict.revision_points if p.point_ref == "P1")
    _veto(svc, session, w, item_ref, p1.finding_ref, reason="业务已确认")
    with pytest.raises(InvalidInput) as exc:
        _adopt_with_edits(svc, session, w, item_ref, verdict, None, selected=["P1"])
    assert "没有可采纳的改法" in str(exc.value) and "恢复计入" in str(exc.value)
    # 没被标记的那个点照常可采纳
    workspace = _adopt_with_edits(svc, session, w, item_ref, verdict, None, selected=["P2"])
    assert _item_view(workspace, item_ref).expression == "系统应尽快完成导出，且超过十秒不得发生"


def test_adopt_rejects_edit_on_an_unselected_point(session):
    """这次不采纳的点携带改稿会被拒——否则用户以为改了，实际根本不会被应用。

    C12：拒绝理由不再提「勾选」，界面上已经没有复选框了。
    """
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_two_point_outcome()))
    assert len(verdict.revision_points) == 2
    with pytest.raises(InvalidInput) as exc:
        _adopt_with_edits(svc, session, w, item_ref, verdict,
                          {"P2": "超过五秒"}, selected=["P1"])
    assert "没有被采纳" in str(exc.value)
    assert "勾选" not in str(exc.value)


def test_adopt_applies_edits_only_to_the_selected_points(session):
    """勾一个改一个：只有被勾选的那个点参与合成，未勾选的点原文保持不变。"""
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_two_point_outcome()))
    workspace = _adopt_with_edits(svc, session, w, item_ref, verdict,
                                  {"P1": "在两秒内"}, selected=["P1"])
    view = _item_view(workspace, item_ref)
    assert view.expression == "系统应在两秒内完成导出，且超时不得发生"
    historic = next(v for v in view.verdict_history if v.round_ref == verdict.round_ref)
    assert historic.adjudication.point_edits == {"P1": "在两秒内"}
    assert historic.adjudication.excluded_point_refs == ["P2"]


# ============================================================================
# 回归护栏（冷审查 T20260720-review-point-veto-and-edit 覆盖形状）：
# C4 非空 group / C5 默认口径 / C2 真实 stub / no_blocker 阻断口径 / 撤销幂等 / verdict_kind 不变
# ============================================================================

def _adopt_all(svc, session, w, item_ref, round_ref):
    """不点名修订点采纳（selected_point_refs=None，模拟区5 斜杠命令采纳）。

    注意：不能借道 _adopt_with_edits——那个助手在 selected 为 None 时会把全部点显式列出，
    恰好绕过本处要验的「服务端默认口径」。这里直接传 None。
    """
    from app.api.schemas import VerdictAdjudicationCommand
    workspace = svc.adjudicate_verdict(VerdictAdjudicationCommand(
        project_ref=w["project"], item_ref=item_ref, round_ref=round_ref,
        decision=VerdictDecision.ADOPTED, selected_point_refs=None, point_edits=None,
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"adj-{uuid.uuid4()}",
    ))
    session.commit()
    return workspace


def test_adopt_drops_the_whole_linkage_group_when_one_member_is_vetoed(session):
    """C4（非空 group）：被否决的点与另一点同联动组时，前端只提交未被否决的点，联动组把被否决
    点拉回。改法把整组一并剔除、不再报错要求操作界面上不存在的入口；本组两点都关联到裁定，剔完
    为空，给出如实的联动说明。若把守卫改回「报错」，报错文案会变，本例的联动说明断言随之翻红。
    """
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_grouped_two_point_outcome()))
    p2 = next(p for p in verdict.revision_points if p.point_ref == "P2")
    _veto(svc, session, w, item_ref, p2.finding_ref, reason="业务已确认")
    with pytest.raises(InvalidInput) as exc:
        # 前端过滤掉被否决点后提交的就是 P1；expand_selection 按组把 P2 拉回
        _adopt_with_edits(svc, session, w, item_ref, verdict, None, selected=["P1"])
    assert "联动组" in str(exc.value) and "恢复计入" in str(exc.value)


def test_adopt_keeps_points_outside_the_dropped_group(session):
    """C4（整组剔除只剔那一组）：P1/P2 同组、P3 独立，否决 P2 后提交 P1+P3——
    整组 g1 被剔除，组外的 P3 照常应用；被剔点的改稿一并忽略，不当作「未勾选」错误。"""
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_grouped_plus_free_outcome()))
    p2 = next(p for p in verdict.revision_points if p.point_ref == "P2")
    _veto(svc, session, w, item_ref, p2.finding_ref, reason="业务已确认")
    # P1 携带改稿也应被静默忽略（它随整组被剔），P3 正常应用
    workspace = _adopt_with_edits(
        svc, session, w, item_ref, verdict, {"P1": "在三秒内"}, selected=["P1", "P3"],
    )
    view = _item_view(workspace, item_ref)
    # 只有 P3（发生→出现）被应用；P1/P2 整组未动
    assert view.expression == "系统应尽快完成导出，且超时不得出现"
    historic = next(v for v in view.verdict_history if v.round_ref == verdict.round_ref)
    assert set(historic.adjudication.excluded_point_refs or []) == {"P1", "P2"}


def test_slash_adopt_default_omits_vetoed_points(session):
    """C5（默认口径）：不点名修订点采纳时，默认取「未被标为不是问题」的点，而不是全部点。

    唯一的点被否决 → 默认集为空 → 走「没有选中任何改法……请用拒绝」这条空集出口。
    若把默认改回 all_refs（还原 C5），被否决点会进默认集、随后被联动剔除逻辑剔成空集，报的是
    另一句「没有可采纳的改法：你选的改法……」——本例断言前一句正是用来钉住 C5 那一处默认口径。
    """
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_revise_outcome(
        ("INCOSE-R7", "尽快完成导出", "「尽快」不可测。"),
    )))
    finding = _blocking(verdict)[0]
    _veto(svc, session, w, item_ref, finding.finding_ref, reason="业务已确认")
    with pytest.raises(InvalidInput) as exc:
        _adopt_all(svc, session, w, item_ref, verdict.round_ref)
    assert "没有选中任何改法" in str(exc.value)


def test_slash_adopt_still_applies_the_live_point_after_a_veto(session):
    """C5（用户价值）：否决两个问题里的一个后，不点名采纳仍然成功——只应用未被否决的那个点，
    而不是像修复前那样必然失败。"""
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_two_point_outcome()))
    p1 = next(p for p in verdict.revision_points if p.point_ref == "P1")
    _veto(svc, session, w, item_ref, p1.finding_ref, reason="业务已确认")
    workspace = _adopt_all(svc, session, w, item_ref, verdict.round_ref)
    # 默认集 = 未被否决的 P2；只改「超时」这一处
    assert _item_view(workspace, item_ref).expression == "系统应尽快完成导出，且超过十秒不得发生"


def test_stub_rediagnosis_after_veto_still_blocks_the_remaining_problem(session):
    """C2（真实 StubRequirementItemDiagnoser，非自造替身）：否决两个问题里的一个、再诊断一轮时，
    未被否决的那个问题必须照常阻断——不能因为「排除点非空」被 stub 凭空判「建议通过」把它吞掉。

    这是本裁定里 C2 落网的直接原因：既有测试全程用 _FixedDiagnoser 顶替 stub，默认诊断器这条
    真实路径一次都没跑过。把 stub 判据改回 `or excluded_points`（还原 C2），本例转红。
    """
    svc, w, item_ref, verdict = _diagnosed(session)          # diagnoser=None → 真实 stub
    blocking = _blocking(verdict)
    assert len(blocking) == 2                                 # stub 对 VAGUE 表达报两条阻断问题
    _veto(svc, session, w, item_ref, blocking[0].finding_ref, reason="业务已确认")

    _svc2, view = _reject_then_rediagnose(svc, session, w, item_ref, verdict, None)  # 仍用真实 stub
    fresh = view.current_verdict
    assert fresh.verdict_kind is VerdictKind.REVISE           # 不是被凭空放行的「建议通过」
    assert fresh.blocking_finding_count >= 1                  # 没被否决的问题还在阻断
    assert fresh.all_blocking_findings_vetoed is False


def test_no_blocker_finding_is_excluded_from_the_blocking_count(session):
    """后端阻断口径：no_blocker 类发现项不计入 blocking_finding_count／all_blocking_findings_vetoed。
    唯一的真阻断问题被否决后即可直接确认，non-blocker 不参与这条谓词。"""
    svc, w, item_ref, verdict = _diagnosed(session, _FixedDiagnoser(_mixed_no_blocker_outcome()))
    assert verdict.blocking_finding_count == 1               # no_blocker 不计入
    blocking = _blocking(verdict)
    assert len(blocking) == 1 and blocking[0].finding_type.value == "missing_field"
    _veto(svc, session, w, item_ref, blocking[0].finding_ref, reason="业务已确认")
    fresh = _item_view(_workspace(svc, w), item_ref).current_verdict
    assert fresh.all_blocking_findings_vetoed is True


def test_veto_restore_is_idempotent(session):
    """撤销幂等：连续两次 restore 同一条否决，问题恢复计入且账目只留一行（撤销写时间不删行）。"""
    svc, w, item_ref, verdict = _diagnosed(session)
    finding_ref = _blocking(verdict)[0].finding_ref
    workspace = _veto(svc, session, w, item_ref, finding_ref, reason="不是问题")
    veto_ref = _item_view(workspace, item_ref).finding_vetoes[0].veto_ref
    _restore(svc, session, w, item_ref, veto_ref)
    _restore(svc, session, w, item_ref, veto_ref)           # 二次撤销：仓储层 revoked_at 已非空即返回
    view = _item_view(_workspace(svc, w), item_ref)
    assert view.current_verdict.blocking_finding_count == 2
    assert len(view.finding_vetoes) == 1 and view.finding_vetoes[0].revoked is True


# ============================================================================
# C46 日志三缺口：改稿采纳可区分、否决的拒绝与去重落痕、否决与撤销记操作人
# ============================================================================

def _captured_events(monkeypatch) -> list[dict]:
    """截获 log_event 发出的结构化日志（断言字段，不断言文本）。"""
    import app.services.item_review as svc_mod
    events: list[dict] = []
    original = svc_mod.log_event

    def spy(component, event, msg="", level="INFO", **fields):
        events.append({"event": event, "level": level, **fields})
        return original(component, event, msg, level, **fields)

    monkeypatch.setattr(svc_mod, "log_event", spy)
    return events


def test_adopt_log_distinguishes_user_edit_from_ai_original(session, monkeypatch):
    """改稿采纳与原案采纳在日志里可区分：edited 记条数（不记文本）。"""
    events = _captured_events(monkeypatch)
    svc, w, item_ref, verdict = _diagnosed(session)
    point = verdict.revision_points[0]
    _adopt_with_edits(svc, session, w, item_ref, verdict, {point.point_ref: "系统应在三秒内完成导出"})

    adopted = next(e for e in events if e["event"] == "review.verdict.revise_adopted")
    assert adopted["edited"] == 1
    assert all("三秒内" not in str(v) for v in adopted.values())  # 改稿文本不进日志


def test_veto_rejection_and_deduplication_leave_a_trace(session, monkeypatch):
    """用户点了却什么都没发生的两种情形都落痕：被拒绝、被去重。"""
    events = _captured_events(monkeypatch)
    svc, w, item_ref, verdict = _diagnosed(session)
    finding = _blocking(verdict)[0]

    _veto(svc, session, w, item_ref, finding.finding_ref, reason="业务已确认")
    _veto(svc, session, w, item_ref, finding.finding_ref, reason="又点了一次")  # 同一个问题
    deduped = next(e for e in events if e["event"] == "review.finding.veto_deduplicated")
    assert deduped["operator_ref"] == "U1"

    with pytest.raises(NotFound):
        _veto(svc, session, w, item_ref, str(uuid.uuid4()))  # 不存在的问题
    rejected = next(e for e in events if e["event"] == "review.finding.veto_rejected")
    assert rejected["reject_reason"] == "finding_not_found" and rejected["level"] == "WARN"
    # 用户理由不进日志（硬规矩第 8 条）
    assert all("业务已确认" not in str(v) for e in events for v in e.values())


def test_veto_and_revoke_record_the_operator(session, monkeypatch):
    """标记与撤销都是可追责的状态迁移，谁做的要在场。"""
    events = _captured_events(monkeypatch)
    svc, w, item_ref, verdict = _diagnosed(session)
    workspace = _veto(svc, session, w, item_ref, _blocking(verdict)[0].finding_ref)
    veto_ref = _item_view(workspace, item_ref).finding_vetoes[0].veto_ref
    _restore(svc, session, w, item_ref, veto_ref)

    assert next(e for e in events if e["event"] == "review.finding.vetoed")["operator_ref"] == "U1"
    assert next(e for e in events if e["event"] == "review.finding.veto_revoked")["operator_ref"] == "U1"


# ============================================================================
# C9/C38 确认端点的拒绝分支：说得出的理由、认得清的项目
# ============================================================================

def test_confirm_while_diagnosing_says_wait_not_endpoint_lecture(session, monkeypatch):
    """C9：诊断进行中点确认，回的是「等本轮结束」，不是一段解释端点分工的开发者语言。

    此前这条检查排在否决消解通道的早退之后：正在跑的那一轮不满足「已收束」，
    _veto_cleared_round 返回 None，用户先撞上早退那句话，永远看不到「诊断进行中」。
    """
    svc, w, item_ref, verdict = _diagnosed(session)
    for f in _blocking(verdict):
        _veto(svc, session, w, item_ref, f.finding_ref, reason="业务已确认")
    monkeypatch.setattr(svc._reviews, "has_running_round", lambda _item_ref: True)

    result = _confirm(svc, session, w, item_ref)
    assert result.status == "rejected_precheck"
    assert "诊断进行中" in result.next_action and "等待本轮结束" in result.next_action


def test_confirm_rejects_an_item_from_another_project(session):
    """C38：条目必须属于命令里的项目（对照 record_finding_veto 早有的同款校验）。"""
    svc, w, item_ref, verdict = _diagnosed(session)
    for f in _blocking(verdict):
        _veto(svc, session, w, item_ref, f.finding_ref)
    with pytest.raises(NotFound):
        svc.confirm_item(ItemConfirmationCommand(
            project_ref=str(uuid.uuid4()), item_ref=item_ref, override=False, reason=None,
            workspace_version=_version(session, w),
            operator_ref="U1", idempotency_key=f"cfm-{uuid.uuid4()}",
        ))
