"""图表协同服务 P01（SCN-004：创建准入 / 预建立追溯 / 源码编辑循环 / AI 建议采纳）测试义务。

设计事实源：docs/30 …/SCN-004 §4.4 节点清单、§4.5 分支结果矩阵。
覆盖：来源准入 / 草稿壳+预建立 / 受控校验正反例 / 追溯同步 / AI 建议登记与三种处置 /
失败停靠不伪造 / 版本冲突 / 幂等 / 待确认冻结编辑（默认拒绝）。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import StubChartSourceSuggester
from app.api.schemas import (
    ChartCreateCommand,
    ChartSourceChangeCommand,
    ChartSuggestionCommand,
    ChartSuggestionHandlingCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import ModelResult, Project, RequirementChart, RequirementItem, TraceLink
from app.domain.enums import (
    ChartFormat,
    ChartStatus,
    ChartSuggestionHandling,
    ChartType,
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
    """项目 + 确认态条目×2 + 待确认条目×1。"""
    p = Project(name="demo")
    session.add(p)
    session.flush()

    def item(req_no, expression, status):
        it = RequirementItem(
            project_id=p.id, parse_result_ref=uuid.uuid4(),
            formation_context_ref=uuid.uuid4(), req_no=req_no,
            expression=expression, req_type="functional", status=status,
            source_element_refs="[]",
        )
        session.add(it)
        session.flush()
        return str(it.id)

    i1 = item("REQ-001", "系统应支持导出 docx", "confirmed")
    i2 = item("REQ-002", "导出耗时不超过五秒", "confirmed")
    i3 = item("REQ-003", "系统应支持批量导入", "pending_confirmation")
    session.commit()
    return {"project": str(p.id), "i1": i1, "i2": i2, "i3": i3}


def _create_cmd(w, sources, key="C1", title="导出流程图",
                chart_type=ChartType.FLOWCHART, format_=ChartFormat.MERMAID):
    return ChartCreateCommand(
        project_ref=w["project"], title=title, chart_type=chart_type,
        format=format_, source_refs=list(sources), operator_ref="U1",
        idempotency_key=key,
    )


def _source_cmd(w, source_code, sources, version, key="S1",
                chart_type=ChartType.FLOWCHART, format_=ChartFormat.MERMAID):
    return ChartSourceChangeCommand(
        project_ref=w["project"], source_code=source_code, format=format_,
        chart_type=chart_type, source_refs=list(sources),
        expected_draft_version=version, operator_ref="U1", idempotency_key=key,
    )


def _created_chart(session, service, w, sources=None, key="C1"):
    result = service.create_chart(_create_cmd(w, sources or [w["i1"], w["i2"]], key=key))
    assert result.status == "created"
    return result.chart_ref


# ============================================================================
# 创建准入（N01/N02）+ 草稿壳 + 预建立追溯（N04/N05）
# ============================================================================

def test_create_with_initial_generation_applies_draft(session):
    """创建即初稿：generate_initial=True → 初稿经受控校验自动应用 + 语义标题回填。"""
    w = _seed(session)
    service = build_sql_chart_service(session)
    cmd = _create_cmd(w, [w["i1"], w["i2"]], title="")
    cmd = cmd.model_copy(update={"generate_initial": True})
    result = service.create_chart(cmd)
    assert result.status == "created"
    assert result.initial_suggestion_context_ref is not None

    ws = service.read_chart_workspace(result.chart_ref)
    assert ws.source_code != ""  # 初稿已就位（stub 按来源条目生成骨架）
    assert ws.draft_version == 2  # 壳 v1 → 初稿 v2
    assert "REQ-001" in ws.title  # stub 语义标题回填（临时标题被覆盖）
    # 留痕：初稿版本 change_origin=ai_initial
    assert any(r.change_origin == "ai_initial" for r in ws.revisions)
    # 时间线：initial 轮次收束为 suggested 且已承接（process_status=adopted）
    entry = next(e for e in ws.suggestion_thread if e.kind == "initial")
    assert entry.status == "suggested"
    assert entry.suggestion is not None
    assert entry.suggestion.process_status == "adopted"


def test_create_with_initial_generation_failed_keeps_editable_shell(session):
    """初稿生成失败：停靠可见，图表保留为可手工编辑的空稿。"""
    w = _seed(session)
    service = build_sql_chart_service(session, chart_suggester=StubChartSourceSuggester(failed=True))
    cmd = _create_cmd(w, [w["i1"]]).model_copy(update={"generate_initial": True, "title": ""})
    result = service.create_chart(cmd)
    assert result.status == "created"
    ws = service.read_chart_workspace(result.chart_ref)
    assert ws.source_code == ""
    assert ws.draft_version == 1
    assert ws.title == "REQ-001 流程图"  # 确定性临时标题保留
    entry = next(e for e in ws.suggestion_thread if e.kind == "initial")
    assert entry.status == "stopped" and entry.stop_reason
    # 仍可手工编辑
    ws2 = service.apply_source_change(
        result.chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"]], version=1),
    )
    assert ws2.validation_errors == []


def test_initial_result_degrades_to_pending_when_draft_diverged(session):
    """初稿结果晚于人工编辑到达：不覆盖人工稿，降级为待采纳建议卡。"""
    w = _seed(session)
    # auto_complete=False 模拟异步：创建时只登记请求不执行
    service = build_sql_chart_service(session, auto_complete=False)
    cmd = _create_cmd(w, [w["i1"]]).model_copy(update={"generate_initial": True})
    result = service.create_chart(cmd)
    # 用户抢先手工编辑（v1 → v2）
    service.apply_source_change(
        result.chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"]], version=1),
    )
    # 初稿判定此时才完成
    from app.repositories.sqlalchemy import run_chart_suggestion_judgement
    run_chart_suggestion_judgement(
        session, result.initial_suggestion_context_ref, StubChartSourceSuggester(),
    )
    ws = service.read_chart_workspace(result.chart_ref)
    assert ws.source_code == MERMAID_OK  # 人工稿未被覆盖
    entry = next(e for e in ws.suggestion_thread if e.kind == "initial")
    assert entry.status == "suggested"
    assert entry.suggestion is not None
    assert entry.suggestion.process_status == "pending"  # 降级为待人工采纳


def test_create_rejected_without_sources(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    result = service.create_chart(_create_cmd(w, []))
    assert result.status == "rejected_precheck"
    # 不建 LDM-012、不建 LDM-013
    assert session.scalars(select(RequirementChart)).all() == []
    assert session.scalars(select(TraceLink)).all() == []


def test_create_rejected_with_unconfirmed_source(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    result = service.create_chart(_create_cmd(w, [w["i1"], w["i3"]]))
    assert result.status == "rejected_precheck"
    assert "确认态" in result.next_action
    assert session.scalars(select(RequirementChart)).all() == []


def test_create_rejected_format_type_mismatch(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    result = service.create_chart(_create_cmd(
        w, [w["i1"]], chart_type=ChartType.DECISION_TABLE, format_=ChartFormat.MERMAID,
    ))
    assert result.status == "rejected_precheck"


def test_create_draft_shell_and_pre_established_links(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    ws = service.read_chart_workspace(chart_ref)
    assert ws.status == ChartStatus.DRAFT
    assert ws.draft_version == 1
    assert ws.source_code == ""
    assert len(ws.trace_links) == 2
    assert all(l.status == TraceLinkStatus.PRE_ESTABLISHED for l in ws.trace_links)
    assert ws.preview_capability == "renderable"


def test_create_idempotent_replay(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    r1 = service.create_chart(_create_cmd(w, [w["i1"]], key="SAME"))
    r2 = service.create_chart(_create_cmd(w, [w["i1"]], key="SAME"))
    assert r1.chart_ref == r2.chart_ref
    assert len(session.scalars(select(RequirementChart)).all()) == 1


def test_eligible_sources_only_confirmed(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    read = service.list_eligible_sources(w["project"])
    refs = {s.item_ref for s in read.sources}
    assert refs == {w["i1"], w["i2"]}


# ============================================================================
# 源码变更应用与受控校验（N07/N10）
# ============================================================================

def test_manual_source_change_applies_and_records_revision(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    ws = service.apply_source_change(
        chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"], w["i2"]], version=1),
    )
    assert ws.validation_errors == []
    assert ws.draft_version == 2
    assert ws.source_code == MERMAID_OK
    assert any(r.change_origin == "manual" and r.draft_version == 2 for r in ws.revisions)


def test_uncontrolled_source_rejected_draft_kept(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    ws = service.apply_source_change(
        chart_ref, _source_cmd(w, "sequenceDiagram\n  A->>B: hi", [w["i1"]], version=1),
    )
    # 格式与类型不匹配：validation_errors 呈现，草稿壳保留、有效源码不更新
    assert ws.validation_errors
    assert ws.source_code == ""
    assert ws.draft_version == 1
    assert ws.status == ChartStatus.DRAFT


def test_source_change_version_conflict_rejected(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    with pytest.raises(RejectedTransition):
        service.apply_source_change(
            chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"]], version=99),
        )


def test_source_change_idempotent_replay(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    service.apply_source_change(chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"]], version=1, key="K1"))
    ws = service.apply_source_change(chart_ref, _source_cmd(w, "别的", [w["i1"]], version=1, key="K1"))
    assert ws.draft_version == 2  # 重放不重复应用
    assert ws.source_code == MERMAID_OK


def test_source_change_with_unconfirmed_source_rejected(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    ws = service.apply_source_change(
        chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"], w["i3"]], version=1),
    )
    assert any("确认态" in e for e in ws.validation_errors)
    assert ws.source_code == ""


# ============================================================================
# 覆盖对象变化与预建立追溯同步（N11）
# ============================================================================

def test_source_refs_change_syncs_trace_links(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w, sources=[w["i1"], w["i2"]])
    ws = service.apply_source_change(
        chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"]], version=1),
    )
    by_upstream = {l.upstream_ref: l for l in ws.trace_links}
    assert by_upstream[w["i1"]].status == TraceLinkStatus.PRE_ESTABLISHED
    assert by_upstream[w["i2"]].status == TraceLinkStatus.INVALID  # 移除→失效


def test_re_adding_source_re_pre_establishes_link(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w, sources=[w["i1"], w["i2"]])
    service.apply_source_change(chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"]], version=1, key="A"))
    ws = service.apply_source_change(
        chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"], w["i2"]], version=2, key="B"),
    )
    by_upstream = {l.upstream_ref: l for l in ws.trace_links}
    assert by_upstream[w["i2"]].status == TraceLinkStatus.PRE_ESTABLISHED
    assert len(ws.trace_links) == 2  # 唯一约束下复用同一条边


# ============================================================================
# AI 源码建议（N08/N09）：登记 LDM-015 → 采纳 / 修订采纳 / 拒绝 / 失败停靠
# ============================================================================

def _request_suggestion(session, service, w, chart_ref, key="SG1", intent=""):
    result = service.request_chart_suggestion(
        chart_ref, ChartSuggestionCommand(
            project_ref=w["project"], intent=intent, operator_ref="U1", idempotency_key=key,
        ),
    )
    assert result.status == "submitted"
    return result.suggestion_context_ref


def test_suggestion_registered_as_ldm015_pending(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    _request_suggestion(session, service, w, chart_ref, intent="补充导出分支")
    ws = service.read_chart_workspace(chart_ref)
    assert len(ws.suggestions) == 1
    assert ws.suggestions[0].process_status == "pending"
    # 对话时间线同步呈现：请求意图 + 已登记建议
    assert len(ws.suggestion_thread) == 1
    assert ws.suggestion_thread[0].status == "suggested"
    assert ws.suggestion_thread[0].intent == "补充导出分支"
    assert ws.suggestion_thread[0].suggestion is not None
    assert ws.suggestion_thread[0].suggestion.suggestion_ref == ws.suggestions[0].suggestion_ref
    # 建议隔离在 LDM-015，图表未被改动
    assert ws.source_code == ""
    mr = session.scalars(select(ModelResult).where(ModelResult.stage == "chart_source_suggestion")).one()
    assert mr.judgement == "suggested"


def test_adopt_suggestion_applies_source(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    _request_suggestion(session, service, w, chart_ref)
    suggestion = service.read_chart_workspace(chart_ref).suggestions[0]
    ws = service.handle_chart_suggestion(
        chart_ref, suggestion.suggestion_ref, ChartSuggestionHandlingCommand(
            project_ref=w["project"], handling=ChartSuggestionHandling.ADOPT,
            operator_ref="U1", idempotency_key="H1",
        ),
    )
    assert ws.validation_errors == []
    assert ws.draft_version == 2
    assert ws.source_code == suggestion.source_code
    assert ws.suggestions[0].process_status == "adopted"
    assert any(r.change_origin == "ai_adopted" for r in ws.revisions)


def test_revise_and_adopt_uses_revised_source(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    _request_suggestion(session, service, w, chart_ref)
    suggestion = service.read_chart_workspace(chart_ref).suggestions[0]
    revised = MERMAID_OK + "\n  B --> C[归档]"
    ws = service.handle_chart_suggestion(
        chart_ref, suggestion.suggestion_ref, ChartSuggestionHandlingCommand(
            project_ref=w["project"], handling=ChartSuggestionHandling.REVISE_AND_ADOPT,
            revised_source=revised, operator_ref="U1", idempotency_key="H2",
        ),
    )
    assert ws.source_code == revised
    assert ws.suggestions[0].process_status == "revised_adopted"


def test_reject_suggestion_requires_reason_and_keeps_chart(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    _request_suggestion(session, service, w, chart_ref)
    suggestion = service.read_chart_workspace(chart_ref).suggestions[0]
    with pytest.raises(InvalidInput):
        service.handle_chart_suggestion(
            chart_ref, suggestion.suggestion_ref, ChartSuggestionHandlingCommand(
                project_ref=w["project"], handling=ChartSuggestionHandling.REJECT,
                operator_ref="U1", idempotency_key="H3",
            ),
        )
    ws = service.handle_chart_suggestion(
        chart_ref, suggestion.suggestion_ref, ChartSuggestionHandlingCommand(
            project_ref=w["project"], handling=ChartSuggestionHandling.REJECT,
            reason="与来源不符", operator_ref="U1", idempotency_key="H3",
        ),
    )
    assert ws.source_code == ""  # 拒绝不改 LDM-012
    assert ws.draft_version == 1
    assert ws.suggestions[0].process_status == "rejected"
    # LDM-015 不删除
    assert session.scalars(select(ModelResult).where(ModelResult.stage == "chart_source_suggestion")).one()


def test_suggestion_failed_stops_without_fabrication(session):
    w = _seed(session)
    service = build_sql_chart_service(session, chart_suggester=StubChartSourceSuggester(failed=True))
    chart_ref = _created_chart(session, service, w)
    context_ref = _request_suggestion(session, service, w, chart_ref)
    ws = service.read_chart_workspace(chart_ref)
    assert ws.suggestions == []  # 失败不产生候选建议
    from app.repositories.sqlalchemy import SqlChartProcessRepository
    req = SqlChartProcessRepository(session).get_suggestion_request(context_ref)
    assert req.stop_next_action is not None  # 停靠原因保留
    # 停靠结局必须进入工作区读视图（区4 对话时间线），不得静默
    assert len(ws.suggestion_thread) == 1
    entry = ws.suggestion_thread[0]
    assert entry.context_ref == context_ref
    assert entry.status == "stopped"
    assert entry.stop_reason == req.stop_next_action
    assert entry.suggestion is None


def test_suggestion_request_idempotent_replay(session):
    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    c1 = _request_suggestion(session, service, w, chart_ref, key="SAME")
    c2 = _request_suggestion(session, service, w, chart_ref, key="SAME")
    assert c1 == c2
    assert len(service.read_chart_workspace(chart_ref).suggestions) == 1


# ============================================================================
# 待确认冻结编辑（默认拒绝）
# ============================================================================

def test_pending_chart_freezes_source_editing(session):
    from app.api.schemas import ChartVerificationCommand

    w = _seed(session)
    service = build_sql_chart_service(session)
    chart_ref = _created_chart(session, service, w)
    service.apply_source_change(chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"], w["i2"]], version=1))
    result = service.start_chart_verification(
        chart_ref, ChartVerificationCommand(
            project_ref=w["project"], operator_ref="U1", idempotency_key="V1",
        ),
    )
    assert result.status == "submitted"
    with pytest.raises(RejectedTransition):
        service.apply_source_change(
            chart_ref, _source_cmd(w, MERMAID_OK, [w["i1"]], version=2, key="X"),
        )
    reject = service.request_chart_suggestion(
        chart_ref, ChartSuggestionCommand(
            project_ref=w["project"], operator_ref="U1", idempotency_key="SGX",
        ),
    )
    assert reject.status == "rejected_precheck"
