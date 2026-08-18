"""图表协同服务 P02（SCN-004：核对发起 / AI 核对 / 复核 / 确认准入 / 不通过分支）测试义务。

设计事实源：docs/30 …/SCN-004 §5.3 核对与复核规则、§5.4 节点清单、§5.5 分支结果矩阵。
覆盖：核对发起推进与阻断 / AI 失败不降级 / 复核幂等与理由必填 / 确认准入各阻断项 /
确认成功=图表与追溯同批成立 / 追溯确立失败回滚 / 退回→待补全→重回编辑→版本失锚 /
作废→失效 / 转问题项全链。
"""
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import StubChartVerifier
from app.api.schemas import (
    ChartConfirmationCommand,
    ChartCreateCommand,
    ChartFindingDecisionCommand,
    ChartIssueCommand,
    ChartLifecycleCommand,
    ChartSourceChangeCommand,
    ChartVerificationCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import Issue, ModelResult, Project, RequirementItem
from app.domain.enums import (
    ChartFindingDecision,
    ChartFormat,
    ChartStatus,
    ChartType,
    IssueStatus,
    TraceLinkStatus,
)
from app.domain.errors import InvalidInput, RejectedTransition
from app.repositories.sqlalchemy import build_sql_chart_service

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


def _seed(session):
    p = Project(name="demo")
    session.add(p)
    session.flush()
    items = []
    for idx, expr in enumerate(["系统应支持导出 docx", "导出耗时不超过五秒"], start=1):
        it = RequirementItem(
            project_id=p.id, parse_result_ref=uuid.uuid4(),
            formation_context_ref=uuid.uuid4(), req_no=f"REQ-00{idx}",
            expression=expr, req_type="functional", status="confirmed",
            source_element_refs="[]",
        )
        session.add(it)
        session.flush()
        items.append(str(it.id))
    session.commit()
    return {"project": str(p.id), "i1": items[0], "i2": items[1]}


_KEY_SEQ = iter(range(10000))


def _key(prefix="K"):
    return f"{prefix}-{next(_KEY_SEQ)}"


def _draft_chart(session, service, w, source_code=MERMAID_OK):
    result = service.create_chart(ChartCreateCommand(
        project_ref=w["project"], title="导出流程图", chart_type=ChartType.FLOWCHART,
        format=ChartFormat.MERMAID, source_refs=[w["i1"], w["i2"]],
        operator_ref="U1", idempotency_key=_key("C"),
    ))
    assert result.status == "created"
    chart_ref = result.chart_ref
    chart = service.read_chart_workspace(chart_ref)
    ws = service.apply_source_change(chart_ref, ChartSourceChangeCommand(
        project_ref=w["project"], source_code=source_code, format=ChartFormat.MERMAID,
        chart_type=ChartType.FLOWCHART, source_refs=[w["i1"], w["i2"]],
        expected_draft_version=chart.draft_version, operator_ref="U1",
        idempotency_key=_key("S"),
    ))
    assert ws.validation_errors == []
    return chart_ref


def _start_verification(service, w, chart_ref):
    return service.start_chart_verification(chart_ref, ChartVerificationCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=_key("V"),
    ))


def _decide_all(service, w, chart_ref, decision=ChartFindingDecision.ACCEPTED, reason=None):
    ws = service.read_chart_workspace(chart_ref)
    for f in ws.verification.findings:
        if f.decision is None:
            ws = service.submit_chart_finding_decision(
                chart_ref, f.finding_ref, ChartFindingDecisionCommand(
                    project_ref=w["project"], decision=decision, reason=reason,
                    operator_ref="U1", idempotency_key=_key("D"),
                ),
            )
    return ws


def _confirm(service, w, chart_ref, key=None):
    return service.confirm_chart(chart_ref, ChartConfirmationCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=key or _key("CF"),
    ))


# ============================================================================
# 核对发起（N01）：推进待确认 / 阻断入口
# ============================================================================

def test_start_verification_promotes_to_pending(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    result = _start_verification(service, w, chart_ref)
    assert result.status == "submitted"
    ws = service.read_chart_workspace(chart_ref)
    assert ws.status == ChartStatus.PENDING_CONFIRMATION
    assert ws.verification is not None
    assert ws.verification.processing_status == "completed"  # 同步 stub 已收束
    assert len(ws.verification.findings) == 1
    assert ws.verification.findings[0].finding_type.value == "no_obvious_issue"


def test_start_verification_rejected_without_source_code(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    result = service.create_chart(ChartCreateCommand(
        project_ref=w["project"], title="空图", chart_type=ChartType.FLOWCHART,
        format=ChartFormat.MERMAID, source_refs=[w["i1"]],
        operator_ref="U1", idempotency_key=_key("C"),
    ))
    reject = _start_verification(service, w, result.chart_ref)
    assert reject.status == "rejected_precheck"
    assert service.read_chart_workspace(result.chart_ref).status == ChartStatus.DRAFT


def test_verification_failed_does_not_degrade(session):
    """AI 核对失败：轮次 FAILED、确认阻断（不降级为纯人工确认）、可重新核对。"""
    w = _seed(session)
    service = build_sql_chart_service(session, chart_verifier=StubChartVerifier(failed=True))
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    ws = service.read_chart_workspace(chart_ref)
    assert ws.verification.processing_status == "failed"
    assert ws.verification.findings == []
    blocked = _confirm(service, w, chart_ref)
    assert blocked.status == "rejected_precheck"
    assert "不得降级" in blocked.next_action
    # 重新核对（换可用 verifier 模拟恢复）→ 新轮次收束
    service_ok = build_sql_chart_service(session)
    retry = _start_verification(service_ok, w, chart_ref)
    assert retry.status == "submitted"
    ws2 = service_ok.read_chart_workspace(chart_ref)
    assert ws2.verification.round_no == 2
    assert ws2.verification.processing_status == "completed"


# ============================================================================
# 复核（N05）：理由必填 / 不重复裁定 / 幂等 / 轮次版本锚
# ============================================================================

def test_reject_finding_requires_reason(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    finding = service.read_chart_workspace(chart_ref).verification.findings[0]
    with pytest.raises(InvalidInput):
        service.submit_chart_finding_decision(
            chart_ref, finding.finding_ref, ChartFindingDecisionCommand(
                project_ref=w["project"], decision=ChartFindingDecision.REJECTED,
                operator_ref="U1", idempotency_key=_key("D"),
            ),
        )


def test_finding_decision_not_repeatable_but_idempotent(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    finding = service.read_chart_workspace(chart_ref).verification.findings[0]
    same_key = _key("D")
    service.submit_chart_finding_decision(
        chart_ref, finding.finding_ref, ChartFindingDecisionCommand(
            project_ref=w["project"], decision=ChartFindingDecision.ACCEPTED,
            operator_ref="U1", idempotency_key=same_key,
        ),
    )
    # 同键重放：返回工作区不报错
    ws = service.submit_chart_finding_decision(
        chart_ref, finding.finding_ref, ChartFindingDecisionCommand(
            project_ref=w["project"], decision=ChartFindingDecision.ACCEPTED,
            operator_ref="U1", idempotency_key=same_key,
        ),
    )
    assert ws.verification.findings[0].decision == ChartFindingDecision.ACCEPTED
    # 新键重复裁定：默认拒绝
    with pytest.raises(RejectedTransition):
        service.submit_chart_finding_decision(
            chart_ref, finding.finding_ref, ChartFindingDecisionCommand(
                project_ref=w["project"], decision=ChartFindingDecision.REJECTED,
                reason="换个说法", operator_ref="U1", idempotency_key=_key("D"),
            ),
        )


# ============================================================================
# 确认准入（N06）+ 图表确认与追溯正式确立同批成立（N08/N09）
# ============================================================================

def test_confirm_blocked_before_review_settled(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    blocked = _confirm(service, w, chart_ref)
    assert blocked.status == "rejected_precheck"
    assert "未复核" in blocked.next_action


def test_confirm_success_establishes_chart_and_traces_together(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    _decide_all(service, w, chart_ref)  # no_obvious_issue 接受
    result = _confirm(service, w, chart_ref)
    assert result.status == "confirmed"
    assert result.trace_established_count == 2
    ws = service.read_chart_workspace(chart_ref)
    assert ws.status == ChartStatus.CONFIRMED
    assert all(l.status == TraceLinkStatus.EFFECTIVE for l in ws.trace_links)
    # 确认依据引用图文核对类 LDM-015
    mr = session.scalars(select(ModelResult).where(ModelResult.stage == "chart_verification")).one()
    assert str(mr.id) in ws.confirm_basis


def test_confirm_after_all_findings_rejected_with_reason(session):
    w = _seed(session)
    # 魔标 @conflict → 阻断类发现项
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w, source_code=MERMAID_OK + "\n  %% @conflict")
    _start_verification(service, w, chart_ref)
    _decide_all(service, w, chart_ref, decision=ChartFindingDecision.REJECTED, reason="经比对不存在冲突")
    result = _confirm(service, w, chart_ref)
    assert result.status == "confirmed"


def test_accepted_blocking_finding_blocks_confirmation(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w, source_code=MERMAID_OK + "\n  %% @hidden")
    _start_verification(service, w, chart_ref)
    _decide_all(service, w, chart_ref)  # 接受隐藏需求发现项
    blocked = _confirm(service, w, chart_ref)
    assert blocked.status == "rejected_precheck"
    assert "阻断发现项" in blocked.next_action
    ws = service.read_chart_workspace(chart_ref)
    assert ws.status == ChartStatus.PENDING_CONFIRMATION
    assert all(l.status == TraceLinkStatus.PRE_ESTABLISHED for l in ws.trace_links)


def test_confirm_idempotent_replay(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    _decide_all(service, w, chart_ref)
    key = _key("CF")
    r1 = _confirm(service, w, chart_ref, key=key)
    r2 = _confirm(service, w, chart_ref, key=key)
    assert r1.status == r2.status == "confirmed"


def test_trace_establish_failure_rolls_back_confirmation(session):
    """追溯正式确立失败 → 整体异常，同一事务回滚后图表不得对外呈现已确认（§5.5 行9）。"""
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    _decide_all(service, w, chart_ref)
    session.commit()  # 前序请求各自的事务边界（deps 层每请求 commit）

    original = service._trace_links.set_link_status

    def broken(link_ref, status, status_reason=None, established_basis=None):
        if status == TraceLinkStatus.EFFECTIVE.value:
            raise RuntimeError("trace store unavailable")
        return original(link_ref, status, status_reason, established_basis)

    service._trace_links.set_link_status = broken
    with pytest.raises(RuntimeError):
        _confirm(service, w, chart_ref)
    session.rollback()  # 会话事务边界 = deps 层 rollback
    ws = build_sql_chart_service(session).read_chart_workspace(chart_ref)
    assert ws.status == ChartStatus.PENDING_CONFIRMATION  # 不对外呈现已确认
    assert all(l.status == TraceLinkStatus.PRE_ESTABLISHED for l in ws.trace_links)


# ============================================================================
# 不通过分支（N07）：退回修订 / 作废 / 转问题项
# ============================================================================

def test_return_for_revision_marks_links_suspect_then_resume(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    ws = service.return_chart_for_revision(chart_ref, ChartLifecycleCommand(
        project_ref=w["project"], reason="表达偏差", operator_ref="U1", idempotency_key=_key("R"),
    ))
    assert ws.status == ChartStatus.RETURNED_FOR_REVISION
    assert all(l.status == TraceLinkStatus.SUSPECT_PENDING_REVIEW for l in ws.trace_links)
    assert ws.verification.invalidated

    # 重回编辑：关系恢复预建立；改源码后旧轮次版本失锚 → 确认阻断
    ws = service.resume_chart_editing(chart_ref, ChartLifecycleCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=_key("R"),
    ))
    assert ws.status == ChartStatus.DRAFT
    assert all(l.status == TraceLinkStatus.PRE_ESTABLISHED for l in ws.trace_links)
    service.apply_source_change(chart_ref, ChartSourceChangeCommand(
        project_ref=w["project"], source_code=MERMAID_OK + "\n  B --> C[归档]",
        format=ChartFormat.MERMAID, chart_type=ChartType.FLOWCHART,
        source_refs=[w["i1"], w["i2"]], expected_draft_version=ws.draft_version,
        operator_ref="U1", idempotency_key=_key("S"),
    ))
    # 直接确认（未重新核对）→ 阻断
    _start_verification(service, w, chart_ref)  # 需重新进入待确认
    blocked = _confirm(service, w, chart_ref)
    assert blocked.status == "rejected_precheck"  # 新轮次发现项未复核


def test_void_chart_invalidates_links(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    ws = service.void_chart(chart_ref, ChartLifecycleCommand(
        project_ref=w["project"], reason="不再需要", operator_ref="U1", idempotency_key=_key("VD"),
    ))
    assert ws.status == ChartStatus.VOIDED
    assert all(l.status == TraceLinkStatus.INVALID for l in ws.trace_links)
    # 作废是终态：不可重回编辑
    with pytest.raises(RejectedTransition):
        service.resume_chart_editing(chart_ref, ChartLifecycleCommand(
            project_ref=w["project"], operator_ref="U1", idempotency_key=_key("R"),
        ))


def test_transfer_finding_to_issue_blocks_confirmation(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w, source_code=MERMAID_OK + "\n  %% @hidden")
    _start_verification(service, w, chart_ref)
    _decide_all(service, w, chart_ref)  # 接受隐藏需求
    finding = service.read_chart_workspace(chart_ref).verification.findings[0]
    issue = service.create_issue_from_finding(chart_ref, finding.finding_ref, ChartIssueCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=_key("I"),
    ))
    assert issue.status == IssueStatus.PENDING
    assert issue.issue_type.value == "hidden_requirement"
    assert issue.chart_ref == chart_ref
    ws = service.read_chart_workspace(chart_ref)
    assert ws.verification.findings[0].issue_ref == issue.issue_ref
    # 关系保持未确认并关联问题项
    assert all(l.status == TraceLinkStatus.PRE_ESTABLISHED for l in ws.trace_links)
    assert all(l.issue_ref == issue.issue_ref for l in ws.trace_links)
    # LDM-015 标记转问题项
    mr = session.scalars(select(ModelResult).where(ModelResult.stage == "chart_verification")).one()
    assert mr.process_status == "transferred_to_issue"
    # 确认仍被阻断
    blocked = _confirm(service, w, chart_ref)
    assert blocked.status == "rejected_precheck"
    # 重复转入：发现项已关联问题项 → 返回既有问题项，不重复创建
    replay = service.create_issue_from_finding(chart_ref, finding.finding_ref, ChartIssueCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=_key("I"),
    ))
    assert replay.issue_ref == issue.issue_ref
    assert len(session.scalars(select(Issue)).all()) == 1
    # 问题项列表可查
    issues = service.list_issues(w["project"])
    assert len(issues.issues) == 1


def test_trace_links_view_distinguishes_statuses(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _draft_chart(session, service, w)
    _start_verification(service, w, chart_ref)
    _decide_all(service, w, chart_ref)
    _confirm(service, w, chart_ref)
    links = service.list_trace_links(w["project"])
    assert all(l.status == TraceLinkStatus.EFFECTIVE for l in links.links)
    assert all(l.upstream_label for l in links.links)
    assert all(l.downstream_label == "导出流程图" for l in links.links)
    effective_only = service.list_trace_links(w["project"], status="effective")
    assert len(effective_only.links) == len(links.links)


# ============================================================================
# LlmChartVerifier 输出承接（健康路径空数组缺陷：2026-07-07 修复）
# ============================================================================

class _FakeChatClient:
    def __init__(self, content: str) -> None:
        self._content = content

    def chat(self, system: str, user: str) -> str:
        return self._content


_VERIFY_ARGS = (
    "prj-1",
    {"title": "通知流程", "chart_type": "flowchart", "format": "mermaid",
     "source_code": "flowchart TD\n  A --> B"},
    [{"id": "item-1", "req_no": "REQ-001", "expression": "系统应发送通知", "req_type": "functional"}],
    [],
)


def test_llm_verifier_maps_empty_array_to_no_obvious_issue():
    """模型对无问题图表确定性返回 []：映射为无明显问题发现项，不判核对失败。"""
    from app.adapters.llm import LlmChartVerifier

    outcome = LlmChartVerifier(_FakeChatClient("[]")).verify(*_VERIFY_ARGS)
    assert outcome.failed is False
    assert [f.finding_type for f in outcome.findings] == ["no_obvious_issue"]
    assert outcome.findings[0].related_source_refs == ("item-1",)
    assert outcome.findings[0].summary  # 仍是可复核的发现项，不是空结论


def test_llm_verifier_rejects_malformed_findings():
    """非空但结构不合格（类型不在枚举/缺 summary）→ 仍失败停靠，可重试，不伪造。"""
    from app.adapters.llm import LlmChartVerifier

    malformed = '[{"finding_type": "无明显问题", "summary": "ok"}, {"finding_type": "no_obvious_issue", "summary": ""}]'
    outcome = LlmChartVerifier(_FakeChatClient(malformed)).verify(*_VERIFY_ARGS)
    assert outcome.failed is True
    assert outcome.findings == ()
