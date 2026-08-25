"""图表协同服务、追溯图谱模块与问题项模块。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import (
    ChartFindingDecision,
    ChartFindingType,
    ChartFormat,
    ChartKind,
    ChartSourceKind,
    ChartStatus,
    ChartSuggestionHandling,
    ChartType,
    IssueStatus,
    IssueType,
    TraceLinkStatus,
)

from .materials import ActionFact

from .item_review import ConfirmationGateRead


# ---- SCN-004 图表协同服务 / 追溯图谱模块 / 问题项模块 ----


class ChartEligibleSourceRead(BaseModel):
    """图表创建候选来源（确认态 LDM-007 投影；来源准入的读侧呈现）。"""

    item_ref: str
    req_no: str
    expression: str
    req_type: str
    status: str
    # 完备性内容（供图表设计工作台「来源」页签逐条核对；档案增补 §4）
    curation_note: str | None = None
    boundary_note: str | None = None
    verification_method: str | None = None
    verification_note: str | None = None
    priority: str | None = None


class ChartBusinessSourceRead(BaseModel):
    """图表候选业务知识来源（P4 06 B.1；SUPPORTING_CONTENT 段：业务翼确认态要素投影）。"""

    element_ref: str
    element_type: str  # term/business_rule/role/external_system/assumption
    content: str
    knowledge_category: str = "business"


class ChartEligibleSourceListRead(BaseModel):
    project_ref: str
    sources: list[ChartEligibleSourceRead]  # 需求条目段（requirement_item）
    business_sources: list[ChartBusinessSourceRead] = Field(default_factory=list)  # 业务知识段
    next_action: str | None = None


class ChartCreateCommand(BaseModel):
    """图表创建入参（P01-N01/N03；只接受确认态来源）。

    title 可空：空时先落确定性临时标题，初稿生成结果以语义标题回填。
    generate_initial=True（创建向导默认路径）：创建后立即基于来源条目生成图表初稿
    （异步；结果经受控校验自动应用；失败停靠在设计页对话时间线可见）。
    """

    project_ref: str
    title: str = ""
    chart_type: ChartType
    format: ChartFormat
    source_kind: ChartSourceKind = ChartSourceKind.REQUIREMENT_ITEM
    source_refs: list[str]
    generate_initial: bool = False
    operator_ref: str
    idempotency_key: str


class ChartCreateResult(BaseModel):
    """图表创建返回（created=图表+预建立追溯；rejected_precheck=来源准入不通过）。"""

    status: str  # created / rejected_precheck
    chart_ref: str | None = None
    initial_suggestion_context_ref: str | None = None  # generate_initial 时的初稿生成请求
    next_action: str | None = None


class TraceLinkRead(BaseModel):
    """LDM-013 读视图（预建立不得作为正式追溯依据消费）。"""

    link_ref: str
    relation_type: str
    upstream_type: str
    upstream_ref: str
    upstream_label: str | None = None  # 上游对象展示标签（条目编号+表达摘要）
    downstream_type: str
    downstream_ref: str
    downstream_label: str | None = None
    status: TraceLinkStatus
    initial_basis: str
    status_reason: str | None = None
    established_basis: str | None = None
    established_at: str | None = None
    issue_ref: str | None = None


class TraceLinkListRead(BaseModel):
    project_ref: str
    links: list[TraceLinkRead]


class ChartSuggestionRead(BaseModel):
    """AI 图表源码建议读视图（LDM-015 投影；采纳前不改 LDM-012）。"""

    suggestion_ref: str  # 图表源码建议类 LDM-015 id
    source_code: str
    explanation: str
    process_status: str  # pending / adopted / revised_adopted / rejected
    created_for_version: int | None = None  # 建议基于的草稿版本


class ChartSuggestionThreadEntryRead(BaseModel):
    """AI 建议请求全生命周期读视图（区4 对话时间线；失败停靠必须可见，不得静默）。"""

    context_ref: str  # 建议请求上下文 id
    intent: str
    created_at: str
    kind: str = "revision"  # initial=创建初稿（结果自动应用）/ revision=修订建议（待人工采纳）
    status: str  # generating（送检中）/ suggested（已登记待处置）/ stopped（失败/拒绝停靠）
    stop_reason: str | None = None  # stopped 时的停靠原因（含模型 cannot_comply 理由）
    suggestion: ChartSuggestionRead | None = None  # suggested 时的候选建议


class ChartFindingRead(BaseModel):
    """图文核对发现项读视图（复核对象，不是正式问题项）。"""

    finding_ref: str
    finding_type: ChartFindingType
    summary: str
    basis_summary: str
    related_source_refs: list[str] = Field(default_factory=list)
    decision: ChartFindingDecision | None = None
    decision_reason: str | None = None
    decision_operator: str | None = None
    decided_at: str | None = None
    issue_ref: str | None = None
    is_blocking: bool = False  # 接受即阻断确认的发现项类型


class ChartVerificationRead(BaseModel):
    """图文核对轮次读视图（AI 失败不得降级为纯人工确认）。"""

    round_ref: str
    round_no: int
    chart_draft_version: int
    processing_status: str  # verifying / completed / failed
    reason: str | None = None
    invalidated: bool = False
    findings: list[ChartFindingRead] = Field(default_factory=list)


class ChartRevisionRead(BaseModel):
    """图表源码修订留痕读视图。"""

    revision_ref: str
    draft_version: int
    change_origin: str  # manual / ai_adopted / ai_revised_adopted
    note: str | None = None
    operator_ref: str
    created_at: str


class ChartRead(BaseModel):
    """LDM-012 列表行读视图。"""

    chart_ref: str
    title: str
    chart_kind: ChartKind
    chart_type: ChartType
    format: ChartFormat
    status: ChartStatus
    draft_version: int
    source_count: int
    updated_at: str


class ChartListRead(BaseModel):
    project_ref: str
    charts: list[ChartRead]
    next_action: str | None = None


class ChartWorkspaceRead(BaseModel):
    """图表工作区读视图（P01 编辑循环 + P02 核对确认的单次往返投影）。"""

    chart_ref: str
    project_ref: str
    title: str
    chart_kind: ChartKind
    chart_type: ChartType
    format: ChartFormat
    source_code: str
    draft_version: int
    status: ChartStatus
    status_reason: str | None = None
    preview_capability: str  # renderable / not_previewable（由 format 派生，不落库）
    creation_basis: str = ""
    verification_conclusion: str | None = None
    confirm_basis: str | None = None
    sources: list[ChartEligibleSourceRead] = Field(default_factory=list)
    trace_links: list[TraceLinkRead] = Field(default_factory=list)
    suggestions: list[ChartSuggestionRead] = Field(default_factory=list)
    suggestion_thread: list[ChartSuggestionThreadEntryRead] = Field(default_factory=list)
    verification: ChartVerificationRead | None = None
    revisions: list[ChartRevisionRead] = Field(default_factory=list)
    confirmation_gate: ConfirmationGateRead | None = None
    available_actions: list[ActionFact] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    next_action: str | None = None


class ChartSourceChangeCommand(BaseModel):
    """P01-N07/N10 源码变更应用入参（乐观锁 expected_draft_version）。"""

    project_ref: str
    source_code: str
    format: ChartFormat
    chart_type: ChartType
    source_refs: list[str]
    expected_draft_version: int
    operator_ref: str
    idempotency_key: str


class ChartSuggestionCommand(BaseModel):
    """P01-N08 AI 源码建议请求入参。"""

    project_ref: str
    intent: str = ""
    operator_ref: str
    idempotency_key: str


class ChartSuggestionRequestResult(BaseModel):
    """AI 建议请求返回（异步；建议经 LDM-015 登记后在工作区呈现）。"""

    status: str  # submitted / rejected_precheck
    suggestion_context_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class ChartSuggestionHandlingCommand(BaseModel):
    """P01-N09 AI 建议处理入参（拒绝必填理由；采纳仍需受控校验）。"""

    project_ref: str
    handling: ChartSuggestionHandling
    revised_source: str | None = None  # revise_and_adopt 时的用户修订稿
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class ChartVerificationCommand(BaseModel):
    """P02-N01 核对发起入参（草稿→待确认推进并冻结编辑）。"""

    project_ref: str
    operator_ref: str
    idempotency_key: str


class ChartVerificationRequestResult(BaseModel):
    """核对发起返回（异步；核对结果经轮次+发现项呈现）。"""

    status: str  # submitted / rejected_precheck
    request_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class ChartFindingDecisionCommand(BaseModel):
    """P02-N05 发现项复核入参（拒绝必填理由；不直接写 LDM-012/013）。"""

    project_ref: str
    decision: ChartFindingDecision
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class ChartConfirmationCommand(BaseModel):
    """P02-N08 图表确认提交入参（确认与追溯确立同批成立）。"""

    project_ref: str
    operator_ref: str
    idempotency_key: str


class ChartConfirmationResult(BaseModel):
    """图表确认返回（confirmed=图表已确认∧追溯已确立；任一失败不对外成立）。"""

    status: str  # confirmed / rejected_precheck
    chart_ref: str
    chart_status: ChartStatus
    trace_established_count: int = 0
    next_action: str | None = None


class ChartLifecycleCommand(BaseModel):
    """退回修订 / 作废 / 重回编辑入参。"""

    project_ref: str
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class ChartIssueCommand(BaseModel):
    """P02-N07 转问题项入参（LDM-011 最小实现；闭环归 SCN-006）。"""

    project_ref: str
    issue_type: IssueType | None = None  # 缺省按发现项类型映射
    title: str | None = None
    description: str | None = None
    operator_ref: str
    idempotency_key: str


class IssueRead(BaseModel):
    """LDM-011 读视图。"""

    issue_ref: str
    issue_type: IssueType
    status: IssueStatus
    title: str
    description: str
    origin_kind: str
    chart_ref: str | None = None
    finding_ref: str | None = None
    trace_link_refs: list[str] = Field(default_factory=list)
    created_by: str
    created_at: str


class IssueListRead(BaseModel):
    project_ref: str
    issues: list[IssueRead]
    next_action: str | None = None
