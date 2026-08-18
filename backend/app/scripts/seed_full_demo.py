"""全量演示数据集一键导入（docs/demo-dataset/02_数据集设计.md 故事线）。

覆盖七个工作面全部流程与主要状态分支：材料接入（已接入/需补充/已排除）→
知识抽取（已确认要素 + 分析中工作区）→ 条目形成（含字段修订）→ 条目评审
（确认态/待复核/修订闭环）→ 图表（受控/草稿未决/退回可疑）→ 追溯
（有效/预建立/可疑/缺口/转问题项）→ 发布（索引/定稿/失败导出/成功导出/基线）。

数据全部经服务层写入（与门禁一致）；AI 环节用 Stub（结局确定、不调模型）。
幂等：按项目名去重，已存在则整体跳过。
用法：cd backend && uv run python -m app.scripts.seed_full_demo [--reset]
  --reset：先删除既有演示项目全部数据再重建（仅限本脚本创建的两个演示项目）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import RecognizedElement, StubSourceIntakeJudge
from app.api.schemas import (
    ChartConfirmationCommand,
    ChartCreateCommand,
    ChartFindingDecisionCommand,
    ChartLifecycleCommand,
    ChartSourceChangeCommand,
    ChartVerificationCommand,
    ConfirmBaselineCommand,
    DocIndexEntryRead,
    ElementDecisionCommand,
    ElementRecognitionCommand,
    FinalizeMarkdownCommand,
    GenerateMarkdownCommand,
    ItemReviewDiagnosisCommand,
    VerdictAdjudicationCommand,
    ItemRevisionCommand,
    ItemizationBatchCommand,
    MarkdownEditCommand,
    ReopenIndexCommand,
    SaveIndexCommand,
    StartDocxExportCommand,
    SupportingBasisCommand,
    TextIntakeCommand,
    TraceIssueCommand,
)
from app.config import settings
from app.db.base import make_engine, make_session_factory
from app.db.models import ParseRequest, Project, RequirementElement, RequirementItem
from app.domain.enums import (
    ChartFormat,
    ChartSourceKind,
    ChartType,
    ElementType,
    ItemRevisionMode,
    ItemizationScopeType,
    ModelJudgement,
    ModelVerdict,
)
from app.repositories.publication import SqlPublicationRepository
from app.repositories.sqlalchemy import (
    SqlIssueRepository,
    SqlTraceLinkRepository,
    build_sql_analysis_service,
    build_sql_chart_service,
    build_sql_item_formation_service,
    build_sql_item_review_service,
    build_sql_requirement_item_service,
    build_sql_service,
)
from app.repositories.trace_read import TraceReadRepository
from app.scripts.import_packaged_templates import import_packaged_templates
from app.services.publication import DocumentOrchestrationService, ExportExecutionService
from app.services.trace_analysis import TraceAnalysisService

DEMO_NAME = "电商订单中心（演示）"
EMPTY_NAME = "空白项目（演示）"
OP = "demo-seed"

# ---- 材料语料（要素锚点 = 原文逐字引文，必须出现在 raw_text 中）----

M1_TEXT = (
    "订单模块需求评审纪要。"
    "用户下单后，系统应通过短信和邮件向用户发送通知。"
    "订单金额超过 500 元时，系统应要求人工审核通过后方可提交。"
    "系统应支持将订单导出为 docx 格式归档。"
    "库存不足时，系统应拦截下单并提示用户。"
    "订单查询页面响应时间不超过两秒。"
    "系统必须部署在企业内网环境。"
    "历史订单数据至少保留三年。"
    "系统应提供 OpenAPI 兼容的订单查询接口。"
    "履约单是指从下单到出库的一次完整订单处理流程。"
    "订单管理员负责审核大额订单并处理异常订单。"
    "系统需与外部支付网关对接以完成扣款。"
    "假设订单基础数据由上游 ERP 系统保证准确。"
    "单笔订单金额超过一万元的须经部门经理审批，依据订单管理办法。"
)

# (类型, 原文引文, 识别置信度)——多档置信度让校准图有真实分布（口径设计 §8-S4）
M1_ELEMENTS = (
    ("functional_requirement", "用户下单后，系统应通过短信和邮件向用户发送通知", 0.95),
    ("functional_requirement", "订单金额超过 500 元时，系统应要求人工审核通过后方可提交", 0.88),
    ("functional_requirement", "系统应支持将订单导出为 docx 格式归档", 0.92),
    ("functional_requirement", "库存不足时，系统应拦截下单并提示用户", 0.68),
    ("quality_attribute", "订单查询页面响应时间不超过两秒", 0.83),
    ("constraint", "系统必须部署在企业内网环境", 0.74),
    ("data_requirement", "历史订单数据至少保留三年", 0.57),
    ("interface_requirement", "系统应提供 OpenAPI 兼容的订单查询接口", 0.91),
    # 业务领域知识翼（两翼框架 P2 演示：术语/角色/外部系统/假设）
    ("term", "履约单是指从下单到出库的一次完整订单处理流程", 0.90),
    ("role", "订单管理员", 0.86),
    ("external_system", "外部支付网关", 0.88),
    ("assumption", "假设订单基础数据由上游 ERP 系统保证准确", 0.70),
    ("business_rule", "单笔订单金额超过一万元的须经部门经理审批，依据订单管理办法", 0.85),
)

# M2 显式识别（分析中工作区）：含低置信度误识别样本，供校准图低置信桶
M2_ELEMENTS = (
    ("functional_requirement", "系统应支持每日自动生成支付对账单", 0.90),
    ("functional_requirement", "对账差异要能导出明细供财务复核", 0.79),
    ("functional_requirement", "对账任务失败时应自动重试三次", 0.86),
    ("quality_attribute", "月度结算报表生成耗时不超过一分钟", 0.62),
    ("functional_requirement", "结算与对账访谈纪要", 0.34),
)

M2_TEXT = (
    "结算与对账访谈纪要。"
    "系统应支持每日自动生成支付对账单。"
    "对账差异要能导出明细供财务复核。"
    "对账任务失败时应自动重试三次。"
    "月度结算报表生成耗时不超过一分钟。"
)

MERMAID_ORDER_FLOW = (
    "flowchart TD\n"
    "  A[用户下单] --> B{金额超过500元?}\n"
    "  B -- 是 --> C[人工审核]\n"
    "  B -- 否 --> D[提交订单]\n"
    "  C --> D\n"
    "  D --> E[短信与邮件通知]"
)

MERMAID_NOTIFY_SEQ = (
    "sequenceDiagram\n"
    "  participant U as 用户\n"
    "  participant S as 订单系统\n"
    "  participant N as 通知服务\n"
    "  U->>S: 提交订单\n"
    "  S->>N: 触发通知\n"
    "  N-->>U: 短信+邮件"
)

MERMAID_EXPORT_FLOW = (
    "flowchart TD\n"
    "  A[选择订单] --> B[生成docx]\n"
    "  B --> C[归档]"
)

# 业务知识来源图表（P4 06 B）：以外部系统「外部支付网关」为来源的领域事实图，
# 只刻画业务领域交互（不替代需求条目支撑需求语义）。
MERMAID_PAYMENT_FLOW = (
    "flowchart LR\n"
    "  A[订单系统] --> B[外部支付网关]\n"
    "  B --> C[银行/支付渠道]\n"
    "  C --> B\n"
    "  B --> A"
)


def _k(name: str) -> str:
    """幂等键：固定字符串（重放不产生副本）。"""
    return f"demo-full-{name}"


# ---- P1 材料接入 ----

def _intake(session: Session, pid: str, note: str, text: str, key: str, judgement=None) -> str:
    judge = StubSourceIntakeJudge(judgement) if judgement else None
    svc = build_sql_service(session, auto_complete=True, judge=judge)
    result = svc.submit_text_intake(TextIntakeCommand(
        project_ref=pid, raw_text=text, source_note=note,
        operator_ref=OP, idempotency_key=_k(key),
    ))
    session.commit()
    return result.context_ref


# ---- P2 知识抽取 ----

def _recognize(session: Session, pid: str, ctx: str, key: str, recognizer=None):
    svc = build_sql_analysis_service(session, auto_complete=True, recognizer=recognizer)
    intake_svc = build_sql_service(session, auto_complete=True)
    material_ref = intake_svc.read_intake_result(ctx).material_ref
    assert material_ref, f"接入 {ctx} 无材料引用"
    submitted = svc.submit_element_recognition(ElementRecognitionCommand(
        project_ref=pid, material_ref=material_ref, operator_ref=OP, idempotency_key=_k(key),
    ))
    session.commit()
    return svc, svc.read_element_workspace(submitted.parse_context_ref)


def _confirm_all_elements(session: Session, svc, workspace):
    targets = [e.id for e in (workspace.elements or [])]
    assert targets, "识别未产出要素"
    updated = svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=workspace.parse_context_ref,
        workspace_version=workspace.workspace_version,
        element_refs=targets, decision="confirm",
        operator_ref=OP, idempotency_key=_k(f"eldec-{workspace.parse_context_ref}"),
    ))
    session.commit()
    return updated


class _ExplicitRecognizer:
    """按预设清单识别（锚点=原文逐字引文；置信度逐条给定，供校准统计）。"""

    def __init__(self, spec: tuple[tuple[str, str, float], ...]) -> None:
        self._spec = spec

    def recognize(self, project_ref: str, raw_text: str, source_note: str,
                  project_scope=None, project_background=None, domain_profile=None):
        from app.adapters.llm import RecognitionResult

        elements = tuple(
            RecognizedElement(
                element_type=ElementType(etype), content=content, source_anchor=content,
                confidence=confidence, verdict=ModelVerdict.PROCESSABLE,
            )
            for etype, content, confidence in self._spec
        )
        return RecognitionResult(elements=elements, basis="演示种子：预设识别清单", failed=False)


# ---- P4 条目评审 ----

def _ws_version(session: Session, parse_context: str) -> str:
    ctx = session.get(ParseRequest, uuid.UUID(parse_context))
    return str(ctx.workspace_version)


def _review_svc(session: Session):
    return build_sql_item_review_service(session, auto_complete=True)


def _diagnose(session: Session, pid: str, parse_context: str, item_refs: list[str],
              key: str, mode: str = "standard"):
    svc = _review_svc(session)
    result = svc.start_item_diagnosis(ItemReviewDiagnosisCommand(
        project_ref=pid, item_refs=item_refs, diagnosis_mode=mode,
        workspace_version=_ws_version(session, parse_context),
        operator_ref=OP, idempotency_key=_k(key),
    ))
    session.commit()
    assert result.status not in ("rejected_precheck",), f"诊断被拒：{result.next_action}"
    return svc, result


def _standing_verdict(svc, formation_context: str, item_ref: str):
    workspace = svc.read_item_review_workspace(formation_context)
    view = next(i for i in workspace.review_items if i.item_ref == item_ref)
    return view.current_verdict


def _adjudicate(session: Session, svc, pid: str, parse_context: str,
                formation_context: str, item_ref: str, decision: str,
                reason: str | None = None, key: str | None = None):
    """结论裁决（v5）：采纳按状态字执行副作用链；拒绝理由必填。"""
    verdict = _standing_verdict(svc, formation_context, item_ref)
    assert verdict is not None, f"条目 {item_ref} 没有站立结论可裁决"
    svc.adjudicate_verdict(VerdictAdjudicationCommand(
        project_ref=pid, item_ref=item_ref, round_ref=verdict.round_ref,
        decision=decision, selected_point_refs=None, reason=reason,
        workspace_version=_ws_version(session, parse_context),
        operator_ref=OP, idempotency_key=_k(key or f"adj-{verdict.round_ref}"),
    ))
    session.commit()
    return verdict


# ---- P5 图表 ----

def _make_chart(session: Session, pid: str, title: str, chart_type: ChartType,
                source_code: str, source_refs: list[str], key: str,
                source_kind: ChartSourceKind = ChartSourceKind.REQUIREMENT_ITEM) -> str:
    svc = build_sql_chart_service(session)
    created = svc.create_chart(ChartCreateCommand(
        project_ref=pid, title=title, chart_type=chart_type,
        format=ChartFormat.MERMAID, source_kind=source_kind, source_refs=source_refs,
        operator_ref=OP, idempotency_key=_k(f"{key}-create"),
    ))
    assert created.status == "created", f"图表创建被拒：{created.next_action}"
    chart_ref = created.chart_ref
    ws = svc.read_chart_workspace(chart_ref)
    ws = svc.apply_source_change(chart_ref, ChartSourceChangeCommand(
        project_ref=pid, source_code=source_code, format=ChartFormat.MERMAID,
        chart_type=chart_type, source_refs=source_refs,
        expected_draft_version=ws.draft_version,
        operator_ref=OP, idempotency_key=_k(f"{key}-src"),
    ))
    assert ws.validation_errors == [], f"图表源码校验失败：{ws.validation_errors}"
    session.commit()
    return chart_ref


def _verify_chart(session: Session, pid: str, chart_ref: str, key: str,
                  decide: bool, confirm: bool) -> None:
    svc = build_sql_chart_service(session)
    svc.start_chart_verification(chart_ref, ChartVerificationCommand(
        project_ref=pid, operator_ref=OP, idempotency_key=_k(f"{key}-verify"),
    ))
    session.commit()
    if not decide:
        return  # 留未裁定发现项（教学接手点）
    ws = svc.read_chart_workspace(chart_ref)
    for f in ws.verification.findings:
        if f.decision is None:
            svc.submit_chart_finding_decision(chart_ref, f.finding_ref, ChartFindingDecisionCommand(
                project_ref=pid, decision="accepted",
                operator_ref=OP, idempotency_key=_k(f"{key}-d-{f.finding_ref}"),
            ))
    session.commit()
    if confirm:
        result = svc.confirm_chart(chart_ref, ChartConfirmationCommand(
            project_ref=pid, operator_ref=OP, idempotency_key=_k(f"{key}-confirm"),
        ))
        assert result.status == "confirmed", f"图表确认被拒：{result.next_action}"
        session.commit()


# ---- P7 发布 ----

def _doc_svc(session: Session) -> DocumentOrchestrationService:
    return DocumentOrchestrationService(SqlPublicationRepository(session))


def _export_svc(session: Session) -> ExportExecutionService:
    return ExportExecutionService(SqlPublicationRepository(session))  # inline 转换


def _index_entries(items_by_type: dict[str, list[str]], material_ref: str,
                   chart_refs: list[str] | None = None) -> list[DocIndexEntryRead]:
    entries: list[DocIndexEntryRead] = []
    for i, ref in enumerate(items_by_type.get("functional", [])):
        entries.append(DocIndexEntryRead(section_key="requirements.functional",
                                         asset_type="requirement_item", asset_ref=ref, order_no=i))
    for i, ref in enumerate(items_by_type.get("quality", [])):
        entries.append(DocIndexEntryRead(section_key="requirements.quality",
                                         asset_type="requirement_item", asset_ref=ref, order_no=i))
    for i, ref in enumerate(items_by_type.get("interface", [])):
        entries.append(DocIndexEntryRead(section_key="requirements.interface",
                                         asset_type="requirement_item", asset_ref=ref, order_no=i))
    for i, ref in enumerate(items_by_type.get("constraint", [])):
        entries.append(DocIndexEntryRead(section_key="overview.constraints",
                                         asset_type="requirement_item", asset_ref=ref, order_no=i))
    for i, ref in enumerate(chart_refs or []):
        entries.append(DocIndexEntryRead(section_key="requirements.charts",
                                         asset_type="chart", asset_ref=ref, order_no=i))
    entries.append(DocIndexEntryRead(section_key="appendix.materials",
                                     asset_type="material", asset_ref=material_ref, order_no=0))
    return entries


def _save_index(session: Session, pid: str, entries: list[DocIndexEntryRead], key: str):
    svc = _doc_svc(session)
    result = svc.save_content_index(SaveIndexCommand(
        project_ref=pid, template_ref="srs-iso29148-v2",  # P5：v2 补业务知识四章节整表投影
        coverage_scope="release-v0.1 发布范围", entries=entries,
        operator_ref=OP, idempotency_key=_k(key),
    ))
    session.commit()
    assert result.status == "index_ready", f"索引未就绪：{getattr(result, 'missing', None)}"
    return svc


def _publish_flow(session: Session, pid: str, items_by_type: dict[str, list[str]],
                  material_ref: str, chart_refs: list[str] | None = None) -> None:
    """失败导出（教学接手点）→ 调整索引回路 → 成功导出 → 发布基线。"""
    entries = _index_entries(items_by_type, material_ref, chart_refs)
    svc = _save_index(session, pid, entries, "idx-1")

    # 第一稿：注入确定性转换失败标记 → 定稿 → 导出失败停靠 + export.failed 通知
    draft = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=pid, operator_ref=OP, idempotency_key=_k("gen-1"),
    ))
    session.commit()
    svc.record_edit(MarkdownEditCommand(
        project_ref=pid, draft_ref=draft.draft_ref,
        content=draft.content + "\n<!--convert-fail-->\n", operator_ref=OP,
    ))
    session.commit()
    fin = svc.finalize_markdown(FinalizeMarkdownCommand(
        project_ref=pid, draft_ref=draft.draft_ref,
        operator_ref=OP, idempotency_key=_k("fin-1"),
    ))
    assert fin.status == "finalized", f"定稿被拒：{fin.next_action}"
    session.commit()
    failed = _export_svc(session).start_export(StartDocxExportCommand(
        project_ref=pid, draft_ref=draft.draft_ref,
        operator_ref=OP, idempotency_key=_k("exp-fail"),
    ))
    session.commit()
    repo = SqlPublicationRepository(session)
    assert repo.get_export(failed.export_ref).status == "failed", "预期失败导出未失败"

    # 调整索引编排回路 → 第二稿干净生成 → 定稿 → 成功导出 → 基线确认
    svc.reopen_index(ReopenIndexCommand(project_ref=pid, operator_ref=OP))
    session.commit()
    svc = _save_index(session, pid, entries, "idx-2")
    draft2 = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=pid, operator_ref=OP, idempotency_key=_k("gen-2"),
    ))
    session.commit()
    if any(e.asset_type == "chart" for e in entries):
        assert "```mermaid" in draft2.content, "受控图表未随索引渲染进 Markdown"
    fin2 = svc.finalize_markdown(FinalizeMarkdownCommand(
        project_ref=pid, draft_ref=draft2.draft_ref,
        operator_ref=OP, idempotency_key=_k("fin-2"),
    ))
    assert fin2.status == "finalized", f"二稿定稿被拒：{fin2.next_action}"
    session.commit()
    ok = _export_svc(session).start_export(StartDocxExportCommand(
        project_ref=pid, draft_ref=draft2.draft_ref,
        operator_ref=OP, idempotency_key=_k("exp-ok"),
    ))
    session.commit()
    export = repo.get_export(ok.export_ref)
    assert export.status == "succeeded", f"导出失败：{export.failure_reason}"
    baseline = _export_svc(session).confirm_baseline(ConfirmBaselineCommand(
        project_ref=pid, export_ref=ok.export_ref, note="演示发布基线 v0.1",
        operator_ref=OP, idempotency_key=_k("baseline"),
    ))
    session.commit()
    assert baseline.baseline_ref


# ---- 主流程 ----

def _reset(session: Session) -> None:
    """删除两个演示项目的全部数据（仅限本脚本创建的项目；重建=完整采纳明细）。"""
    from app.db.models import (
        AdoptionRecord, AgentRun, AgentRunEvent, ChartSourceRevision,
        ChartSuggestionRequest, ChartVerificationFinding, ChartVerificationRequest,
        ChartVerificationRound, DemoChatTranscript, DocumentIndexEntry, DocxExport,
        ElementChangeDraft,
        ElementFacetProjection, ElementHistory, IntakeRecord, IntakeRequest, Issue,
        ItemDiagnosisRequest, ItemDiagnosisRound, ItemFormationRequest,
        ItemRevisionSuggestion, ItemizationOutcome, MarkdownDraft, MarkdownPatch,
        Material, MaterialParseResult, MaterialRevision, MaterialSupplement,
        ModelResult, Notification, ParseRequest, ElementOperation, ReleaseBaseline,
        RequirementChart, RequirementDocument, RequirementElement, RequirementItem,
        RequirementItemRevision, ItemReviewFinding, TraceLink,
    )

    pids = [p.id for p in session.scalars(
        select(Project).where(Project.name.in_([DEMO_NAME, EMPTY_NAME]))
    ).all()]
    if not pids:
        print("reset：无既有演示项目")
        return

    def ids(model, col="project_id"):
        return [r for r in session.scalars(
            select(model.id).where(getattr(model, col).in_(pids))
        ).all()]

    def wipe(model, col, values):
        if values:
            for row in session.scalars(select(model).where(getattr(model, col).in_(values))).all():
                session.delete(row)

    material_ids = ids(Material)
    element_ids = ids(RequirementElement)
    item_ids = ids(RequirementItem)
    chart_ids = ids(RequirementChart)
    document_ids = ids(RequirementDocument)
    draft_ids = [r for r in session.scalars(
        select(MarkdownDraft.id).where(MarkdownDraft.document_ref.in_(document_ids))
    ).all()] if document_ids else []
    round_ids = [r for r in session.scalars(
        select(ChartVerificationRound.id).where(ChartVerificationRound.chart_ref.in_(chart_ids))
    ).all()] if chart_ids else []
    formation_ids = ids(ItemFormationRequest)
    context_ids = (ids(IntakeRequest) + ids(ParseRequest) + formation_ids
                   + ids(ItemDiagnosisRequest) + ids(ChartSuggestionRequest)
                   + ids(ChartVerificationRequest))
    run_ids = [r for r in session.scalars(
        select(AgentRun.id).where(AgentRun.context_ref.in_(context_ids))
    ).all()] if context_ids else []

    wipe(AgentRunEvent, "run_id", run_ids)
    wipe(AgentRun, "id", run_ids)
    # 演示留痕表：append-only 的唯一删除例外＝演示项目重置（防孤儿留痕行）
    wipe(DemoChatTranscript, "project_ref", pids)
    wipe(Notification, "project_ref", pids)
    wipe(AdoptionRecord, "project_id", pids)
    wipe(ModelResult, "applies_to_ref", context_ids)
    wipe(ChartVerificationFinding, "round_ref", round_ids)
    wipe(ChartVerificationRound, "chart_ref", chart_ids)
    wipe(ChartSourceRevision, "chart_ref", chart_ids)
    wipe(MarkdownPatch, "draft_ref", draft_ids)
    wipe(DocxExport, "document_ref", document_ids)
    wipe(ReleaseBaseline, "document_ref", document_ids)
    wipe(MarkdownDraft, "document_ref", document_ids)
    wipe(DocumentIndexEntry, "document_ref", document_ids)
    wipe(ItemReviewFinding, "item_ref", item_ids)
    wipe(ItemDiagnosisRound, "project_id", pids)
    wipe(RequirementItemRevision, "item_ref", item_ids)
    wipe(ItemRevisionSuggestion, "item_ref", item_ids)
    wipe(ItemizationOutcome, "formation_context_ref", formation_ids)
    wipe(ElementFacetProjection, "element_ref", element_ids)
    wipe(ElementHistory, "project_id", pids)
    wipe(ElementChangeDraft, "project_id", pids)
    wipe(ElementOperation, "project_id", pids)
    wipe(TraceLink, "project_id", pids)
    wipe(Issue, "project_id", pids)
    for model in (RequirementChart, RequirementItem, RequirementElement,
                  MaterialParseResult, IntakeRecord):
        wipe(model, "project_id", pids)
    wipe(MaterialRevision, "material_ref", material_ids)
    wipe(MaterialSupplement, "material_ref", material_ids)
    wipe(Material, "project_id", pids)
    for model in (ItemDiagnosisRequest, ItemFormationRequest, ParseRequest,
                  IntakeRequest, ChartSuggestionRequest, ChartVerificationRequest):
        wipe(model, "project_id", pids)
    wipe(RequirementDocument, "project_id", pids)
    for pid in pids:
        project = session.get(Project, pid)
        if project is not None:
            session.delete(project)
    session.commit()
    print(f"reset 完成：已删除演示项目 {len(pids)} 个及其全部数据")


def main() -> None:
    import sys

    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        if "--reset" in sys.argv:
            _reset(session)
        if session.scalars(select(Project).where(Project.name == DEMO_NAME)).first() is not None:
            print(f"seed 跳过：项目「{DEMO_NAME}」已存在")
            return

        import_packaged_templates(session)
        session.commit()

        demo = Project(name=DEMO_NAME, scope="release-v0.1",
                       background="电商订单中心需求工程演示（全流程数据集）",
                       domain_profile_key="ecommerce-fulfillment")  # P6b 领域档案演示
        session.add(demo)
        if session.scalars(select(Project).where(Project.name == EMPTY_NAME)).first() is None:
            session.add(Project(name=EMPTY_NAME, scope="release-v0.1",
                                background="空态与材料先行演示"))
        session.flush()
        pid = str(demo.id)
        session.commit()

        # P1 材料接入：已接入×2 + 需补充停靠 + 已排除
        ctx1 = _intake(session, pid, "订单需求评审纪要（业务部-王经理，2026-06-18）", M1_TEXT, "m1")
        ctx2 = _intake(session, pid, "结算与对账访谈纪要（财务部-李会计，2026-06-25）", M2_TEXT, "m2")
        _intake(session, pid, "口头补充说明（内容待补齐）",
                "这块功能大概就按上次说的来，细节回头再定。", "m3",
                judgement=ModelJudgement.INSUFFICIENT_CONTENT)
        _intake(session, pid, "群聊闲聊记录",
                "中午吃什么？楼下新开了家面馆，据说不错。", "m4",
                judgement=ModelJudgement.NO_ASSET_VALUE)

        # P2 知识抽取：m1 全要素确认；m2 部分裁定（含拒绝低置信度误识别）后保持分析中（教学接手点）
        asvc, ws1 = _recognize(session, pid, ctx1, "m1-rec", recognizer=_ExplicitRecognizer(M1_ELEMENTS))
        ws1 = _confirm_all_elements(session, asvc, ws1)
        asvc2, ws2 = _recognize(session, pid, ctx2, "m2-rec", recognizer=_ExplicitRecognizer(M2_ELEMENTS))
        m2_by_content = {e.content: e.id for e in (ws2.elements or [])}
        confirm_ref = m2_by_content.get(M2_ELEMENTS[0][1])
        reject_ref = m2_by_content.get(M2_ELEMENTS[-1][1])
        if confirm_ref:
            asvc2.decide_elements(ElementDecisionCommand(
                parse_context_ref=ws2.parse_context_ref, workspace_version=ws2.workspace_version,
                element_refs=[confirm_ref], decision="confirm",
                operator_ref=OP, idempotency_key=_k("m2-confirm"),
            ))
            session.commit()
        if reject_ref:
            ws2 = asvc2.read_element_workspace(ws2.parse_context_ref)
            asvc2.decide_elements(ElementDecisionCommand(
                parse_context_ref=ws2.parse_context_ref, workspace_version=ws2.workspace_version,
                element_refs=[reject_ref], decision="reject",
                operator_ref=OP, idempotency_key=_k("m2-reject"),
            ))
            session.commit()

        # P3 条目形成：批次条目化 → 全部待确认
        formation = build_sql_item_formation_service(session, auto_complete=True)
        formed = formation.start_element_itemization_batch(ItemizationBatchCommand(
            project_ref=pid, parse_result_ref=ws1.parse_result_ref,
            workspace_version=ws1.workspace_version,
            scope_type=ItemizationScopeType.ALL_ELIGIBLE,
            operator_ref=OP, idempotency_key=_k("form-1"),
        ))
        session.commit()
        formation_ctx = formed.formation_context_ref
        parse_ctx = ws1.parse_context_ref

        items = session.scalars(
            select(RequirementItem)
            .where(RequirementItem.project_id == uuid.UUID(pid))
            .order_by(RequirementItem.req_no)
        ).all()
        assert len(items) >= 6, f"条目化产出不足：{len(items)}"
        item_refs = [str(i.id) for i in items]

        # 留 1 条不复核（教学接手点：待复核发现项）；1 条走修订闭环；其余逐项复核后确认。
        # 接手点取功能类最后一条（功能类有多条）：条目创建顺序按 uuid 破平局跨运行非确定，
        # 若随机留下唯一的质量条目会饿死发布必填槽 requirements.quality（曾在 --reset 后复现）。
        functional_refs = [str(i.id) for i in items if i.req_type == "functional"]
        assert len(functional_refs) >= 3, "功能条目不足以同时留 hero/教学接手点并满足发布必填槽"
        pending_ref = functional_refs[-1]
        # hero = 追溯页默认焦点：最后确认 → updated_at 最大 → 稳定命中 AEP-058 _default_focus；
        # 其邻域刻意连到受控图表(有效边)+草稿图表(预建立边)+退回图表(可疑边)+已发布文档，
        # 让「追溯关系总览」默认态贴合 04A §6 v4 原型（上游材料/要素、下游图表/文档、含可疑边）。
        hero_ref = functional_refs[0]

        # 条目优先级：仅人工设定（AEP-036 留痕；29148 属性补齐）。
        # 教学接手点条目不设优先级 → 维护列表"缺优先级"警示筛选有演示对象；
        # data 类条目由 stub 不产出验收准则 → "缺验收准则"警示同理。
        item_svc = build_sql_requirement_item_service(session, chain_incremental=False)
        priority_targets = [r for r in item_refs if r != pending_ref]
        priority_plan = ["high", "high"] + ["medium"] * (len(priority_targets) - 3) + ["low"]
        for ref, level in zip(priority_targets, priority_plan):
            item_svc.apply_item_revision(ItemRevisionCommand(
                project_ref=pid, item_ref=ref,
                workspace_version=_ws_version(session, parse_ctx),
                revision_mode=ItemRevisionMode.MANUAL,
                field_key="priority", revised_value=level,
                reason="干系人裁量：相对本发布范围的业务重要性",
                operator_ref=OP, idempotency_key=_k(f"prio-{ref}"),
            ))
        # 教学接手点条目（保持待确认）：改写为含多处模糊量词/未定义阈值的陈述，
        # 让质量诊断器演示多标注（stub 逐词产 evidence_span：较大/尽快/超时）。
        item_svc.apply_item_revision(ItemRevisionCommand(
            project_ref=pid, item_ref=pending_ref,
            workspace_version=_ws_version(session, parse_ctx),
            revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
            revised_value="当订单实付金额较大时，系统应尽快将其转入人工审核队列并暂缓发货；超时后自动释放。",
            reason="演示：构造含模糊量词与未定义阈值的陈述以展示质量诊断多标注",
            operator_ref=OP, idempotency_key=_k(f"demo-expr-{pending_ref}"),
        ))
        session.commit()

        # P4 条目评审：一轮标准诊断覆盖全部条目（hero 留到最后确认 → 默认焦点）
        rsvc, _ = _diagnose(session, pid, parse_ctx, item_refs, "diag-1")

        def _confirm_via_review(ref: str) -> None:
            verdict = _standing_verdict(rsvc, formation_ctx, ref)
            if verdict is None:
                return
            if verdict.verdict_kind and verdict.verdict_kind.value == "revise":
                # 采纳「建议修订」：应用修订点 → 链式自动增量诊断 → 新结论（预期通过）
                _adjudicate(session, rsvc, pid, parse_ctx, formation_ctx, ref,
                            "adopted", key=f"adopt-rev-{ref}")
                verdict = _standing_verdict(rsvc, formation_ctx, ref)
            if verdict is not None and verdict.verdict_kind and verdict.verdict_kind.value == "pass":
                # 采纳「建议通过」= 确认（一步到位）
                _adjudicate(session, rsvc, pid, parse_ctx, formation_ctx, ref,
                            "adopted", key=f"adopt-pass-{ref}")

        for ref in item_refs:
            if ref in (pending_ref, hero_ref):
                continue
            _confirm_via_review(ref)
        # hero 末位确认：其 updated_at 最大 → 追溯页默认焦点稳定落在 hero 邻域
        _confirm_via_review(hero_ref)

        confirmed = [str(i.id) for i in items if str(i.id) not in (pending_ref,)
                     and session.get(RequirementItem, i.id).status == "confirmed"]
        by_type: dict[str, list[str]] = {}
        for i in items:
            session.refresh(i)
            if i.status == "confirmed":
                by_type.setdefault(i.req_type, []).append(str(i.id))

        # P4 两翼演示：业务知识（术语「履约单」，确认态）→ hero 条目（确认态）建
        # 支撑依据正式边（AC-P4-01：SUPPORTING_BASIS effective，走人工补全写通道）。
        # 其余业务知识（角色/外部系统/假设）刻意留空 → 追溯缺口「业务知识未被引用」
        # 有演示对象（AC-P4-04）。create_supporting_basis 以 find_link 幂等，可重跑。
        biz_term = session.scalar(
            select(RequirementElement)
            .where(
                RequirementElement.project_id == uuid.UUID(pid),
                RequirementElement.element_type == "term",
                RequirementElement.process_status == "confirmed",
                RequirementElement.superseded.is_(False),
            )
            .order_by(RequirementElement.created_at)
        )
        if biz_term is not None:
            tsvc = TraceAnalysisService(
                TraceReadRepository(session),
                SqlTraceLinkRepository(session),
                SqlIssueRepository(session),
            )
            tsvc.create_supporting_basis(pid, SupportingBasisCommand(
                element_ref=str(biz_term.id), item_ref=hero_ref, operator_ref=OP,
            ))
            session.commit()

        # P5 图表：受控 / 草稿未决（教学接手点）/ 退回→可疑边。
        # hero 邻域刻意覆盖三种边态，使追溯页默认焦点一屏同时见到 有效/预建立/可疑：
        fr = by_type.get("functional", [])
        assert len(fr) >= 3 and hero_ref in fr, "确认态功能条目不足以支撑图表演示"
        other = next(r for r in fr if r != hero_ref)  # 另一确认功能条目，与 hero 同挂受控图表
        chart1 = _make_chart(session, pid, "订单处理流程图", ChartType.FLOWCHART,
                             MERMAID_ORDER_FLOW, [hero_ref, other], "c1")
        _verify_chart(session, pid, chart1, "c1", decide=True, confirm=True)  # hero→图表 有效边

        chart2 = _make_chart(session, pid, "下单通知时序图", ChartType.SEQUENCE_DIAGRAM,
                             MERMAID_NOTIFY_SEQ, [hero_ref], "c2")
        _verify_chart(session, pid, chart2, "c2", decide=False, confirm=False)  # hero→图表 预建立边

        # chart2 教学接手点保留未决发现项，但取一条转问题项（演示转问题项入口 + 统计分布）
        c2svc = build_sql_chart_service(session)
        c2ws = c2svc.read_chart_workspace(chart2)
        c2_findings = [f for f in (c2ws.verification.findings if c2ws.verification else []) if f.decision is None]
        if c2_findings:
            from app.api.schemas import ChartIssueCommand

            c2svc.create_issue_from_finding(chart2, c2_findings[0].finding_ref, ChartIssueCommand(
                project_ref=pid, title=None, description=None,
                operator_ref=OP, idempotency_key=_k("c2-issue"),
            ))
            session.commit()

        chart3 = _make_chart(session, pid, "订单导出链路图", ChartType.FLOWCHART,
                             MERMAID_EXPORT_FLOW, [hero_ref], "c3")  # 退回→hero→图表 可疑边
        csvc = build_sql_chart_service(session)
        csvc.start_chart_verification(chart3, ChartVerificationCommand(
            project_ref=pid, operator_ref=OP, idempotency_key=_k("c3-verify"),
        ))
        ws3 = csvc.return_chart_for_revision(chart3, ChartLifecycleCommand(
            project_ref=pid, reason="导出链路与最新评审结论存在表达偏差，退回修订",
            operator_ref=OP, idempotency_key=_k("c3-return"),
        ))
        session.commit()

        # P4 业务知识来源图表（06 B）：以外部系统「外部支付网关」（业务翼确认态）为来源，
        # source_kind=SUPPORTING_CONTENT → 图表来源两翼分组（AC-P4-05）+ 业务知识→图表边。
        biz_sys = session.scalar(
            select(RequirementElement).where(
                RequirementElement.project_id == uuid.UUID(pid),
                RequirementElement.element_type == "external_system",
                RequirementElement.process_status == "confirmed",
                RequirementElement.superseded.is_(False),
            )
        )
        if biz_sys is not None:
            chart4 = _make_chart(session, pid, "支付网关交互图", ChartType.FLOWCHART,
                                 MERMAID_PAYMENT_FLOW, [str(biz_sys.id)], "c4",
                                 source_kind=ChartSourceKind.SUPPORTING_CONTENT)
            _verify_chart(session, pid, chart4, "c4", decide=True, confirm=True)

        # P6 追溯：对可疑边转问题项（origin=trace_diagnosis）
        suspect_links = [l for l in ws3.trace_links if l.status == "suspect_pending_review"]
        trace_svc = TraceAnalysisService(
            TraceReadRepository(session),
            trace_links=SqlTraceLinkRepository(session),
            issues=SqlIssueRepository(session),
        )
        if suspect_links:
            trace_svc.create_diagnosis_issue(pid, TraceIssueCommand(
                title="追溯诊断：订单导出链路图关系可疑，待复核补全",
                description="图表退回修订导致覆盖关系转可疑；需复核关系是否仍成立。",
                trace_link_ref=suspect_links[0].link_ref, chart_ref=chart3,
                operator_ref=OP, idempotency_key=_k("issue-1"),
            ))
            session.commit()

        # P7 发布：失败导出 → 调整索引 → 成功导出 → 基线
        material_ref = build_sql_service(session, auto_complete=True) \
            .read_intake_result(ctx1).material_ref
        _publish_flow(session, pid, by_type, material_ref, chart_refs=[chart1])

        # 演示自检：追溯页默认焦点=hero，且其下游邻域同时含 有效边+可疑边（贴合 04A v4 原型总览）
        entry = trace_svc.read_entry(pid)
        focus = entry.default_focus
        assert focus and focus.ref == hero_ref, f"追溯默认焦点非 hero：{focus}"
        down = trace_svc.read_chain(pid, "requirement_item", hero_ref, "downstream", depth=2, limit=8)
        edge_status = {e.status for lv in down.levels for e in lv.edges}
        assert {"effective", "suspect_pending_review"} <= edge_status, \
            f"hero 下游未覆盖 有效+可疑 边：{edge_status}"

        # 全局检索：演示集导入完成后全量回填 search_index，一键出检索（02 §5.1）。
        from app.services.search_index import build_search_indexer
        idx_session = make_session_factory(engine)()
        try:
            idx_stats = build_search_indexer(idx_session).reindex_all()
        finally:
            idx_session.close()

        print(f"seed 完成：项目「{DEMO_NAME}」（{pid}）+「{EMPTY_NAME}」")
        print(f"  材料 4（接入2/需补充1/排除1）；条目 {len(items)}（确认 {len(confirmed)}）")
        print(f"  检索索引：回填 {sum(s.projected for s in idx_stats)} 节点（{len(idx_stats)} 项目，"
              f"prune {sum(s.pruned for s in idx_stats)}）")
        print("  图表 4（受控/草稿未决/退回可疑/业务知识来源）；可疑边+问题项+缺口；")
        print("  两翼：业务知识→条目 支撑依据正式边 + 业务知识→图表来源边 + 未引用业务知识缺口；")
        print(f"  追溯默认焦点 hero={focus.label}；下游边态={sorted(edge_status)}；缺口 {entry.counts.gaps}")
        print("  发布：失败导出停靠 + 成功导出 + 发布基线；通知：export.failed")
    finally:
        session.close()


if __name__ == "__main__":
    main()
