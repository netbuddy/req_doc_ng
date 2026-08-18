"""FE 面 Pydantic DTO —— 类名 = OpenAPI schema 名 = 前端生成 TS 类型名。

命名：读 *Read、写命令 *Command、写结果 *Result（docs/40 shared/前端契约适配 §2）。
枚举跨线为稳定码；*Ref 为 str(uuid)。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

from app.domain.enums import (
    ChartFindingDecision,
    ChartFindingType,
    ChartFormat,
    ChartKind,
    ChartSourceKind,
    ChartStatus,
    ChartSuggestionHandling,
    ChartType,
    DiagnosisMode,
    ElementProcessStatus,
    ElementType,
    knowledge_category_of,
    DiagnosisTrigger,
    DialogueOutcomeType,
    IntakeConclusion,
    IntakeOutcome,
    IntakeRequestStatus,
    IssueStatus,
    IssueType,
    ItemizationResultStatus,
    ItemizationScopeType,
    ItemRevisionMode,
    MaterialParseStatus,
    ModelVerdict,
    NoiseTriage,
    RecognitionOutcome,
    RecognitionRequestStatus,
    RequirementItemStatus,
    RequirementItemType,
    ReviewConclusion,
    ReviewDisplayCode,
    ReviewFindingType,
    ReviewItemStatus,
    VerdictDecision,
    VerdictKind,
    TraceLinkStatus,
)


class HealthPayload(BaseModel):
    """GET /api/health（基础设施健康，对齐 frontend/src/api/health.ts）。"""

    status: str
    checks: dict[str, str] = Field(default_factory=dict)
    service: str | None = None
    version: str | None = None
    environment: str | None = None
    ready: bool | None = None


# ---- 项目上下文服务（业务项目 LDM-001；2026-08-07 项目管理组重构：V2 应答信封）----

class CreateProjectCommand(BaseModel):
    """createProject 入参（含操作者与幂等键，对齐 V1 写接口纪律与 V2 留痕要求）。"""

    name: str = Field(description="项目名称（必填，去首尾空白后不得为空）。")
    scope: str | None = Field(default=None, description="项目范围说明（选填）。")
    background: str | None = Field(default=None, description="项目背景说明（选填）。")
    domain_profile_key: str | None = Field(default=None, description="领域档案键（封闭集，选填；缺省不注入领域先验）。")
    operator_ref: str = Field(description="操作者标识——创建者，随行存储（V2 操作留痕的接口准备）。")
    idempotency_key: str = Field(description="幂等键——同键重放返回同一项目，不重复建行。")


class ProjectSummary(BaseModel):
    """项目摘要——列表一行所需字段（详情走「读单个项目」）。"""

    project_id: str = Field(description="项目标识（UUID）。")
    name: str = Field(description="项目名称。")
    created_at: str = Field(description="创建时刻（ISO 8601）。")


class ProjectDetail(BaseModel):
    """项目详情——单读与创建回执的载荷。"""

    project_id: str = Field(description="项目标识（UUID）。")
    name: str = Field(description="项目名称。")
    scope: str | None = Field(default=None, description="项目范围说明。")
    background: str | None = Field(default=None, description="项目背景说明。")
    domain_profile_key: str | None = Field(default=None, description="领域档案键（封闭集；空=不注入领域先验）。")
    domain_profile_label: str = Field(description="领域档案显示名——按键派生，不落表。")
    created_at: str = Field(description="创建时刻（ISO 8601）。")


class ProjectDeletionReport(BaseModel):
    """删除清点回执（级联删净摘要；逐表明细走结构化日志）。"""

    project_id: str = Field(description="被删项目标识。")
    project_name: str = Field(description="被删项目名称。")
    deleted_rows: int = Field(description="全部表删除行数合计（含项目行自身）。")
    table_counts: dict[str, int] = Field(description="表名 → 删除行数（删净证据摘要）。")
    files_deleted: int = Field(description="落盘导出文件实删个数。")
    files_failed: int = Field(description="落盘文件删除失败个数（已记结构化日志，不回滚）。")


class BusinessRejection(BaseModel):
    """业务拒绝形状（对齐 V2 契约 common.yaml BusinessRejection）。"""

    category: Literal["业务拒绝"] = Field(description="类别，固定值。")
    reason_code: str = Field(description="原因码（中文短语，机器码，正本见 docs/v2/design/业务拒绝原因码表.md）。")
    message: str = Field(description="文案（人可读，可改；码不可改）。")
    details: dict[str, object] | None = Field(default=None, description="详情——随码而异的结构化参数。")


class BusinessRejectionEnvelope(BaseModel):
    """业务拒绝信封：与成功同走 200，以 result 字段区分。"""

    result: Literal["业务拒绝"] = Field(description="应答信封结果字段。")
    rejection: BusinessRejection = Field(description="拒绝的结构化说明。")


class SuccessOfProjectList(BaseModel):
    """成功信封：项目摘要列表。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，本操作恒为「成功」。")
    data: list[ProjectSummary] = Field(description="全部项目摘要，按创建时刻升序。")


class SuccessOfProjectDetail(BaseModel):
    """成功信封：项目详情（单读与创建回执共用）。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，本操作恒为「成功」。")
    data: ProjectDetail = Field(description="项目详情。")


class SuccessOfProjectDeletion(BaseModel):
    """成功信封：删除清点回执。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，删除成功时为「成功」。")
    data: ProjectDeletionReport = Field(description="删除清点回执。")


class DomainProfileRead(BaseModel):
    """AEP-103 领域档案只读目录项（建项目下拉 + 设置页展示）。"""

    key: str
    label: str
    description: str = ""
    version: int = 1


# ---- 材料接收服务 接口专用数据模型 ----

class TextIntakeCommand(BaseModel):
    """AEP-001 入参。"""

    project_ref: str = Field(description="项目标识——须与路径参数 project_id 一致，不一致返回 400。")
    raw_text: str = Field(description="材料正文（纯文本）。提交后由受理判断决定是否形成正式材料。")
    source_note: str = Field(default="", description="来源备注——材料从哪里来的自由说明，选填。")
    operator_ref: str = Field(description="操作者标识——记入操作留痕，回答「这份材料是谁提交的」。")
    idempotency_key: str = Field(description="幂等键——同一键重复提交不重复受理，用于断网重试等场景防重。")


class IntakeRequestResult(BaseModel):
    """AEP-001 返回（受理立即返回；异步判断经 agent_run_ref 追踪）。"""

    status: IntakeRequestStatus = Field(description="受理状态——提交是否被接收进入异步判断。")
    context_ref: str | None = Field(default=None, description="接入上下文标识——后续用它查询受理结论。")
    agent_run_ref: str | None = Field(default=None, description="异步任务标识——经任务通道（轮询或 SSE）追踪受理进度。")
    next_action: str | None = Field(default=None, description="建议的下一步动作，供界面导航。")


class IntakeJudgementResultCommand(BaseModel):
    """AEP-002 入参（模型编排内部回交，不暴露 HTTP）。"""

    model_result_ref: str
    intake_context_ref: str
    operator_ref: str
    idempotency_key: str
    service_accepts: bool = True


class IntakeDecisionResult(BaseModel):
    """AEP-002 返回。material_ref 仅在 accepted 分支非空。"""

    outcome: IntakeOutcome
    intake_conclusion: IntakeConclusion | None = None
    material_ref: str | None = None
    next_action: str | None = None


class ActionFact(BaseModel):
    """可执行动作事实（领域裁定）；ViewModel 映射为 ActionVM，前端不自算门禁。"""

    key: str = Field(description="动作标识，例如「重新提交」。")
    enabled: bool = Field(description="该动作当前是否可执行——由后端领域规则裁定，前端不自行推断。")
    disabled_reason: str | None = Field(default=None, description="不可执行时的原因说明，供界面展示。")


class IntakeResultRead(BaseModel):
    """结果查询读视图（intakeApi.getResult）。"""

    context_ref: str = Field(description="接入上下文标识——与提交时返回的 context_ref 对应。")
    intake_conclusion: IntakeConclusion | None = Field(default=None, description="受理结论三值：accepted＝接收成为正式材料；returned_for_supplement＝退回补充；excluded＝排除。判断未完成时为空。")
    material_ref: str | None = Field(default=None, description="材料标识——仅结论为「接收」时非空，此后以它引用该材料。")
    basis: str | None = Field(default=None, description="结论依据——受理判断给出的理由说明。")
    next_action: str | None = Field(default=None, description="建议的下一步动作，供界面导航。")
    available_actions: list[ActionFact] = Field(default_factory=list, description="当前可用动作清单——门禁裁定结果，前端照单呈现。")


# ---- 材料接入 V2 形态（2026-08-08 用户裁定路线 A：三拍制保留，应答改 V2 信封）----

class IntakeSubmitCommand(BaseModel):
    """提交材料接入的入参（V2 形态：项目标识走路径，不再入请求体）。"""

    text: str = Field(description="材料正文（纯文本）。提交后由受理判断决定是否形成正式材料。")
    source_note: str = Field(default="", description="来源备注——材料从哪里来的自由说明，选填。")
    operator_ref: str = Field(description="操作者标识——记入操作留痕，回答「这份材料是谁提交的」。")
    idempotency_key: str = Field(description="幂等键——同一键重复提交不重复受理，重放返回同一接入上下文。")


class IntakeReceipt(BaseModel):
    """提交回执——受理已登记、进入异步判断。"""

    context_ref: str = Field(description="接入上下文标识——后续用它查询受理结论。")
    agent_run_ref: str | None = Field(default=None, description="异步任务标识——经任务通道追踪受理进度；幂等重放命中时为空。")


class SuccessOfIntakeReceipt(BaseModel):
    """成功信封：提交回执。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，受理登记成功为「成功」。")
    data: IntakeReceipt = Field(description="提交回执。")


class SuccessOfIntakeConclusion(BaseModel):
    """成功信封：受理结论读视图（判断中／失败停靠／三值结论都是合法的读取结果）。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，本操作恒为「成功」。")
    data: IntakeResultRead = Field(description="受理结论读视图。")


# ---- 分析转化服务 接口专用数据模型（SCN-001-P02）----

class ElementRecognitionCommand(BaseModel):
    """AEP-021 入参（对已接入材料发起知识项识别）。"""

    project_ref: str
    material_ref: str
    operator_ref: str
    idempotency_key: str


class RecognitionRequestResult(BaseModel):
    """AEP-021 返回（受理立即返回；异步识别经 agent_run_ref 追踪）。"""

    status: RecognitionRequestStatus
    parse_context_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class RecognitionResultCommand(BaseModel):
    """AEP-022 入参（模型编排内部回交，不暴露 HTTP）。"""

    model_result_ref: str
    parse_context_ref: str
    operator_ref: str
    idempotency_key: str


class RecognitionDecisionResult(BaseModel):
    """AEP-022 返回。parse_result_ref 仅在 registered/no_processable_elements 分支非空。"""

    outcome: RecognitionOutcome
    parse_result_ref: str | None = None
    element_count: int = 0
    next_action: str | None = None


class ElementFacetFindingRead(BaseModel):
    """完备性判据单面向判定读视图（label/revision_hint 由服务端判据补齐，模型只给判定）。"""

    facet_key: str
    label: str
    required: bool = False
    status: str  # present / missing / ambiguous / not_applicable（判据驱动 N/A，不计缺口）
    evidence: str | None = None  # 原文/要素内容逐字片段（present/ambiguous 必有）
    note: str | None = None
    revision_hint: str | None = None  # 缺失时的修订提示（来自判据，非模型生成）


class ElementFacetReviewRead(BaseModel):
    """要素完备度投影（最近一轮复核的派生结果；非权威、可再生，不参与门禁）。"""

    rubric_version: int
    correctness: str | None = None  # consistent_with_source / deviates / unverifiable
    completeness: str | None = None  # complete / incomplete（必备面向未全判定时为空）
    facets: list[ElementFacetFindingRead] = Field(default_factory=list)
    stale: bool = False  # 要素已修订出新版本，投影过期（待重诊；TC-08 版本锚）


class RequirementElementRead(BaseModel):
    """知识项读视图（LDM-005 投影）。source_anchor 为结构化锚点 JSON 字符串。

    process_status 为人工确认生命周期；model_verdict/confidence 为证据预标记。
    facet_review 为最近一轮复核的完备度投影（LDM-015 派生，不落 LDM-005）。
    source_drift_tokens 为偏离原文投影（派生不落库）：表达中的数字/拉丁术语
    token 在原文与补入块语料中均不存在时列出（非空=已偏离原文，勘误/补入可消解）。
    """

    id: str
    element_type: ElementType
    content: str
    source_anchor: str | None = None
    source_drift_tokens: list[str] = Field(default_factory=list)
    confidence: float | None = None
    process_status: ElementProcessStatus
    model_verdict: ModelVerdict | None = None
    # 模型给该条裁定的具体理由（证据字段）；None＝模型漏给或存量数据，读侧回落该裁定的通用判据
    verdict_reason: str | None = None
    # 人工对「AI 建议剔除的候选」的处置标记；None＝未处置（suspected_noise 的条目仍在候选区）
    noise_triage: NoiseTriage | None = None
    version: int = 1
    superseded: bool = False
    review_conclusion: ReviewConclusion | None = None
    review_basis: str | None = None
    revision_draft: str | None = None
    correction_note: str | None = None
    origin_refs: list[str] = Field(default_factory=list)
    facet_review: ElementFacetReviewRead | None = None
    # 该知识项最近一次写入时刻（ISO）：区5 把复核·修订稿卡按此时刻插进时间线，缺则视为最新
    updated_at: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def knowledge_category(self) -> str:
        """派生字段：两翼归属（"requirement"|"business"），由 element_type 派生、不落库。"""
        return knowledge_category_of(self.element_type)


class SourceAnchorRange(BaseModel):
    """来源锚点范围（offset + 引文选择器；页面详细设计 §4.2）。"""

    start: int = -1
    end: int = -1
    exact: str = ""
    prefix: str = ""
    suffix: str = ""


class MaterialTextBlockRead(BaseModel):
    """区3 来源画布段落块（offset 以 raw_text 全局字符偏移为准）。"""

    block_id: str
    index: int
    start_offset: int
    end_offset: int
    text: str


class MaterialSupplementRead(BaseModel):
    """补入来源块读视图（带「补」标记：内容/依据/补入人，原快照不动）。"""

    supplement_ref: str
    content: str
    basis: str
    operator_ref: str
    at: str


class MaterialCanvasRead(BaseModel):
    """区3 来源画布唯一数据源（LDM-002 当前来源版本，不读前端本地草稿）。"""

    material_ref: str
    title: str
    source_note: str | None = None
    raw_text: str
    source_version: int = 1
    blocks: list[MaterialTextBlockRead] = Field(default_factory=list)
    supplements: list[MaterialSupplementRead] = Field(default_factory=list)


class MaterialParseContextRead(BaseModel):
    """材料最近一次识别请求上下文（进页只读回放；从未识别过则 parse_context_ref 为空）。"""

    material_ref: str
    parse_context_ref: str | None = None


class ElementChangeDraftRead(BaseModel):
    """P04 变更草案读视图（确认创建前不是正式 LDM-005）。"""

    draft_ref: str
    workspace_version: str
    operation_type: str
    target_element_refs: list[str] = Field(default_factory=list)
    before_items: list[RequirementElementRead] = Field(default_factory=list)
    after_items: list[RequirementElementRead] = Field(default_factory=list)
    source_ranges: list[SourceAnchorRange] = Field(default_factory=list)
    impact_summary: list[str] = Field(default_factory=list)
    create_gate: str = "creatable"  # creatable/needs_material_supplement/needs_item_revision/needs_manual/stopped
    next_action: str | None = None
    # 草案最近一次写入时刻（ISO）：区5 把草案卡按此时刻插进时间线，缺则视为最新
    updated_at: str | None = None


class ElementWorkspaceRead(BaseModel):
    """知识项工作区读视图（N07 ViewModel；五区唯一刷新边界，available_* 为后端事实）。"""

    parse_context_ref: str
    parse_result_ref: str | None = None  # LDM-004 引用（条目化批次命令 AEP-038 携带）
    workspace_version: str = "1"
    parse_status: MaterialParseStatus | None = None
    material_canvas: MaterialCanvasRead | None = None
    elements: list[RequirementElementRead] = Field(default_factory=list)
    # 既有知识项（登记归并命中的既往要素，锚点换算到本材料）：只读可见，不参与裁决/门禁/批量
    merged_existing_elements: list[RequirementElementRead] = Field(default_factory=list)
    selected_element_ref: str | None = None
    basis: str | None = None
    review_note: str | None = None  # 最近一次复核结局摘要（成功/补漏N条/失败）
    change_draft: ElementChangeDraftRead | None = None
    next_action: str | None = None
    available_actions: list[ActionFact] = Field(default_factory=list)
    available_operations: list[ActionFact] = Field(default_factory=list)


# ---- P03 复核 / P04 校正修订 命令（AEP-023..029）----

class ElementOperationRequestResult(BaseModel):
    """AEP-023/025 受理返回（异步经 agent_run_ref 追踪）。"""

    status: str  # accepted / rejected_precheck
    operation_context_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class ElementReviewCommand(BaseModel):
    """AEP-023 入参：AI 复核只请求建议，不请求变更。"""

    parse_context_ref: str
    workspace_version: str
    target_element_refs: list[str] = Field(default_factory=list)
    selected_text_ranges: list[SourceAnchorRange] = Field(default_factory=list)
    review_intent: str = ""
    operator_ref: str
    idempotency_key: str


class ElementReviewResultCommand(BaseModel):
    """AEP-024 入参（模型编排内部回交，不暴露 HTTP）。"""

    model_result_ref: str
    operation_context_ref: str
    operator_ref: str
    idempotency_key: str


class ElementAiExecutionCommand(BaseModel):
    """AEP-025 入参：AI 只执行用户指定操作。"""

    parse_context_ref: str
    workspace_version: str
    operation_type: str
    target_element_refs: list[str] = Field(default_factory=list)
    selected_text_ranges: list[SourceAnchorRange] = Field(default_factory=list)
    execution_instruction: str
    operator_ref: str
    idempotency_key: str


class ElementAiExecutionResultCommand(BaseModel):
    """AEP-028 入参（模型编排内部回交，不暴露 HTTP）。"""

    model_result_ref: str
    operation_context_ref: str
    operator_ref: str
    idempotency_key: str


class ElementDecisionCommand(BaseModel):
    """直接裁定入参：确认→已确认 / 拒绝→已撤销（单条或批量；含分析中越过复核）。"""

    parse_context_ref: str
    workspace_version: str
    element_refs: list[str]
    decision: str  # confirm / reject
    reason: str | None = None
    operator_ref: str
    idempotency_key: str
    # 用户已看过在途修订守卫的二次确认弹层并选择坚持确认。守卫是软拦截：这个字段
    # 只决定确认留痕里加不加「确认时有 AI 修订在途」的注记，不决定放不放行——
    # 不带它的请求（默认 false）行为与守卫上线前逐字节一致。
    inflight_revision_ack: bool = False


class ElementDecisionPrecheckCommand(BaseModel):
    """确认前的在途修订预检入参（只读，不迁移状态、不升版本）。"""

    parse_context_ref: str
    element_refs: list[str]


class GuardedElementRead(BaseModel):
    """被在途修订守卫拦下的一条知识项（供二次确认弹层逐条列名）。"""

    element_ref: str
    content_brief: str          # 知识项表达摘要，供弹层认人（不含任何模型原文/提示词）
    agent_run_ref: str
    run_status: str             # queued / started


class ElementDecisionPrecheckRead(BaseModel):
    """确认前预检读视图：guarded 非空＝这些条目正被 AI 起草修订，确认会搁置修订稿。"""

    guarded: list[GuardedElementRead] = Field(default_factory=list)


class ElementTriageCommand(BaseModel):
    """「AI 建议剔除的候选」的人工处置入参（restore=撤回到正常列表 / return=移回候选区）。

    只写人工裁定标记 noise_triage；model_verdict 与 verdict_reason 是模型证据，本命令
    不改写。处置不迁移确认生命周期、不升版本——撤回后的知识项仍是「待确认」。

    idempotency_key 可选，且**本命令不做幂等重放保护**（与 ElementDecisionCommand、
    ElementReviewCommand 的同名字段语义不同：那两处分别用它拼采纳明细键、查重放操作）。
    重复提交由工作区版本校验拦下——首次调用即递增 workspace_version，重放时携带的旧版本会被拒。
    """

    parse_context_ref: str
    workspace_version: str
    element_refs: list[str]
    action: str  # restore / return
    reason: str | None = None
    operator_ref: str
    idempotency_key: str | None = None


class ElementRevisionCommand(BaseModel):
    """修订迭代入参（对话轮次，不迁移状态；AI 辅助或人工直改修订稿）。"""

    parse_context_ref: str
    workspace_version: str
    element_ref: str
    mode: str = "ai"  # ai（送检 AI 迭代修订稿）/ manual（人工直接给修订稿）
    instruction: str | None = None   # ai 模式：修订指令
    draft_content: str | None = None  # manual 模式：修订稿全文
    operator_ref: str
    idempotency_key: str


class RevisionFinalizeCommand(BaseModel):
    """修订稿定夺入参：adopt=采纳即确认（超出原文须先补入）/ abandon=清除草稿（状态不变）。"""

    parse_context_ref: str
    workspace_version: str
    element_ref: str
    action: str  # adopt / abandon
    operator_ref: str
    idempotency_key: str


class ElementEditCommand(BaseModel):
    """就地修订入参：改类型/改范围/改表达（动 LDM-005，版本+1，不迁状态）。"""

    parse_context_ref: str
    workspace_version: str
    element_ref: str
    edit_type: str  # adjust_type / adjust_anchor / revise_expression
    new_element_type: ElementType | None = None
    new_content: str | None = None
    selected_text_ranges: list[SourceAnchorRange] = Field(default_factory=list)
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class ElementReopenCommand(BaseModel):
    """重开/回流入参：已撤销→待确认（重开）/ 已确认→待确认（回流），产生新版本。"""

    parse_context_ref: str
    workspace_version: str
    element_ref: str
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class MaterialErratumCommand(BaseModel):
    """勘误入参：修正原文笔误（不引入新事实）；原文出新来源版本，受影响要素回待确认。"""

    parse_context_ref: str
    workspace_version: str
    old_text: str
    new_text: str
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class MaterialSupplementCommand(BaseModel):
    """补入入参：追加原文没有的新事实（带「补」标记留痕）；相关要素回待确认。"""

    parse_context_ref: str
    workspace_version: str
    content: str
    basis: str
    target_element_refs: list[str] = Field(default_factory=list)
    operator_ref: str
    idempotency_key: str


class ElementDialogueCommand(BaseModel):
    """AEP-096 入参：区5 整段对话原文（可含 /命令词）+ 上下文引用（非命令参数）。"""

    parse_context_ref: str
    workspace_version: str
    message: str
    target_element_refs: list[str] = Field(default_factory=list)  # 上下文：当前选中/勾选
    selected_text_ranges: list[SourceAnchorRange] = Field(default_factory=list)  # 上下文：区3 选区
    operator_ref: str
    idempotency_key: str  # 派发子操作幂等键为 {key}:dispatch


class ElementDialogueResult(BaseModel):
    """AEP-096 返回：命令解释回执（executed 内联工作区；queued 走 AgentRun）。"""

    outcome: str  # executed / queued / clarify / cannot_comply / unknown_command / rejected_precheck
    command_word: str | None = None
    operation: str | None = None
    operation_label: str | None = None
    params_echo: dict | None = None
    message: str | None = None
    agent_run_ref: str | None = None
    workspace: ElementWorkspaceRead | None = None
    next_action: str | None = None


class ElementHistoryRecordRead(BaseModel):
    """要素变更历史一条（谁/何时/改了什么，US-E4-01）。"""

    version: int
    action: str
    from_status: ElementProcessStatus | None = None
    to_status: ElementProcessStatus | None = None
    operator_ref: str
    note: str | None = None
    snapshot: str | None = None  # JSON：变更前 内容/类型/锚点/修订稿
    at: str


class ElementHistoryRead(BaseModel):
    """要素变更历史读视图。"""

    element_ref: str
    records: list[ElementHistoryRecordRead] = Field(default_factory=list)


class ManualElementCorrectionCommand(BaseModel):
    """AEP-027 入参：版本关系层人工校正进入变更草案（拆分/合并/新增）。

    operation_type：add_missing/split/merge。split 时 new_content 以换行分隔多个拆分结果。
    """

    parse_context_ref: str
    workspace_version: str
    operation_type: str
    target_element_refs: list[str] = Field(default_factory=list)
    selected_text_ranges: list[SourceAnchorRange] = Field(default_factory=list)
    new_content: str | None = None
    new_element_type: ElementType | None = None
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class ElementChangeConfirmCommand(BaseModel):
    """AEP-029 入参：确认创建（P04 唯一改变有效 LDM-005 集合的工作台入口）。"""

    parse_context_ref: str
    workspace_version: str
    draft_ref: str
    operator_ref: str
    idempotency_key: str


# ---- SCN-002-P01 条目形成服务（AEP-038）+ 需求条目服务（AEP-036 最小面）----

class ItemizationBatchCommand(BaseModel):
    """AEP-038 startElementItemizationBatch 入参（详设 条目形成服务.md）。"""

    project_ref: str
    parse_result_ref: str
    workspace_version: str
    scope_type: ItemizationScopeType
    target_element_refs: list[str] = Field(default_factory=list)
    operator_ref: str
    idempotency_key: str


class ItemizationBatchRequestResult(BaseModel):
    """AEP-038 受理返回（受理立即返回；批次经 agent_run_ref 追踪）。"""

    status: str  # submitted / in_flight（复用在途批次，refs 为原批次）/ rejected_precheck
    formation_context_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class StructureRecheckCommand(BaseModel):
    """AEP-114 startStructureRecheck 入参（结构复核批次受理；只判不改）。

    item_refs 空=默认目标集：待确认 ∩（修订后未复核 ∪ 无体检结果），排除已终止与
    现行判定条目（内容未变重跑只产生判定方差；任务卡裁定 2）。单条复核=携带单元素 item_refs。
    """

    project_ref: str
    parse_result_ref: str
    workspace_version: str
    item_refs: list[str] = Field(default_factory=list)
    operator_ref: str
    idempotency_key: str


class StructureRecheckRequestResult(BaseModel):
    """AEP-114 受理返回（受理立即返回；批次经 agent_run_ref 追踪，完成后工作区刷新）。

    弹层计数由前端按工作区徽标事实派生（单一口径）；受理侧不再回传 stale/missing
    细分计数（issue #8 清理债：线上无消费者且注释失实，撤除）。
    """

    # submitted / in_flight（复用在途复核，refs 为原批次）/ noop_current（现行判定零调用直发回执）
    # / rejected_precheck
    status: str
    recheck_context_ref: str | None = None
    agent_run_ref: str | None = None
    target_item_refs: list[str] = Field(default_factory=list)
    next_action: str | None = None


class StructureRecheckOutcomeRead(BaseModel):
    """AEP-114 读侧：复核批次逐条目结局（回执两集合口径的事实源；issue #8 缺陷 1/3/5）。

    事实来自受理信封的执行过程账目：已重判 / 修订在飞已过期跳过（CAS 丢弃，可重跑）/
    复核失败（旧判保留原样）/ 离开流程跳过；pending=尚未执行（批次在途）。
    """

    recheck_context_ref: str
    target_item_refs: list[str] = Field(default_factory=list)
    refreshed_refs: list[str] = Field(default_factory=list)
    expired_skipped_refs: list[str] = Field(default_factory=list)
    failed_refs: list[str] = Field(default_factory=list)
    skipped_refs: list[str] = Field(default_factory=list)
    pending_refs: list[str] = Field(default_factory=list)


class ItemizationResultRead(BaseModel):
    """逐要素归因结果（created/blocked/failed/skipped + 原因 + next_action）。"""

    element_ref: str
    result_status: ItemizationResultStatus
    item_ref: str | None = None
    formation_basis_ref: str | None = None
    reason: str | None = None
    next_action: str | None = None


class ItemRevisionRecordRead(BaseModel):
    """待确认条目字段修订记录读视图（改前/改后/操作者留痕）。"""

    record_ref: str
    field_key: str
    before_value: str
    after_value: str
    revision_mode: ItemRevisionMode
    selected_point_refs: list[str] = Field(default_factory=list)
    operator_ref: str
    reason: str | None = None
    created_at: str


class ItemRevisionSuggestionRead(BaseModel):
    """字段修订候选建议（来源=条目格式化类 LDM-015；建议不是确认事实）。"""

    suggestion_ref: str
    item_ref: str
    field_key: str  # expression / req_type
    proposed_value: str
    reason: str
    status: str  # candidate / accepted / rejected / expired
    # 建议生成时刻（ISO）：区5 把建议卡按此时刻插进时间线，缺则视为最新
    created_at: str | None = None


class ItemStructureReviewRead(BaseModel):
    """条目陈述达标投影（条目档案判定；格式化 LDM-015 派生，非权威、可再生，不参与门禁）。

    facet 行复用 ElementFacetFindingRead 形状（label/revision_hint 由服务端档案补齐）。
    """

    profile_version: int
    convention_key: str | None = None  # 判定所依据的规约方案（徽章按投影记录的方案口径渲染）
    statement_conformance: str | None = None  # conforms / deviates / not_applicable
    completeness: str | None = None  # complete / incomplete（必备面向未全判定时为空）
    facets: list[ElementFacetFindingRead] = Field(default_factory=list)
    stale: bool = False  # 条目表达/类型已偏离格式化时点，投影过期（待重诊；方案切换不触发）


class PendingRequirementItemRead(BaseModel):
    """待确认需求条目读视图（LDM-007 投影）。

    structure_review 为条目档案陈述达标投影（格式化 LDM-015 派生，不落 LDM-007）。
    """

    item_ref: str
    req_no: str
    expression: str
    req_type: RequirementItemType
    status: RequirementItemStatus
    version_no: int = 1
    source_element_refs: list[str] = Field(default_factory=list)
    formation_basis_ref: str | None = None
    curation_note: str | None = None  # 内容整理说明（20 基线 §5.7；AEP-036 可修订）
    boundary_note: str | None = None  # 条目边界说明（同上）
    verification_method: list[str] = Field(default_factory=list)  # 验证方式（多选；模型可提建议初稿）
    verification_note: str | None = None  # 验收准则（模型初稿只准归纳来源；缺失仅警示）
    priority: str | None = None  # 条目优先级 high/medium/low（仅人工设定）
    revision_records: list[ItemRevisionRecordRead] = Field(default_factory=list)
    available_actions: list[ActionFact] = Field(default_factory=list)
    structure_review: ItemStructureReviewRead | None = None


class BlockedElementRead(RequirementElementRead):
    """不可形成条目的要素（支撑性/未确认/锚点不足）+ 停靠原因。"""

    formation_role: str = "blocked"  # supporting / blocked
    blocked_reason: str | None = None


class ItemFormationWorkspaceRead(BaseModel):
    """条目形成页面唯一工作区读视图（五区同一 workspace_version）。

    parse_result_ref：AEP-097 对话/AEP-038 批次的 body 锚点（恢复路径只有本读视图时前端由此取锚）。
    """

    formation_context_ref: str
    parse_result_ref: str | None = None
    workspace_version: str
    # 区2 只读徽标：本批次固定的生效规约方案（切换唯一入口=设置页；选型文档 §5，SCN-002 §5）
    convention_key: str | None = None
    convention_display_name: str | None = None
    material_canvas: MaterialCanvasRead | None = None
    eligible_elements: list[RequirementElementRead] = Field(default_factory=list)
    blocked_elements: list[BlockedElementRead] = Field(default_factory=list)
    # P7 §1.1 意图背景：确认态 goal/scenario 只读投影（不可勾选、不入批次、不建边）
    intent_context: list[RequirementElementRead] = Field(default_factory=list)
    pending_items: list[PendingRequirementItemRead] = Field(default_factory=list)
    selected_item_ref: str | None = None
    batch_results: list[ItemizationResultRead] = Field(default_factory=list)
    revision_suggestions: list[ItemRevisionSuggestionRead] = Field(default_factory=list)
    available_actions: list[ActionFact] = Field(default_factory=list)
    available_operations: list[ActionFact] = Field(default_factory=list)
    next_action: str | None = None


# ---- AEP-102 需求规约方案目录（只读；选型文档 §3 classDiagram）----


class ConventionPatternRead(BaseModel):
    """句式模板速览行（设置页模板表数据源）。"""

    label: str
    pattern: str


class ConventionExampleRead(BaseModel):
    """逐 req_type 完整示例（设置页示例表数据源；五类全覆盖）。"""

    req_type: str
    statement: str


class RequirementConventionRead(BaseModel):
    """单个规约方案的元数据 + 句式模板 + 完整示例（文案单一来源，前端禁硬编码）。"""

    convention_key: str
    display_name: str
    blueprint: str
    positioning: str
    pattern_overview: list[ConventionPatternRead] = Field(default_factory=list)
    examples: list[ConventionExampleRead] = Field(default_factory=list)


class RequirementConventionCatalogRead(BaseModel):
    """AEP-102 listRequirementConventions 响应：全部方案 + 当前生效方案 key。"""

    active_convention: str
    conventions: list[RequirementConventionRead] = Field(default_factory=list)


class ItemRevisionCommand(BaseModel):
    """AEP-036 applyItemRevision 入参（本切片只落待确认字段修订最小分支）。"""

    project_ref: str
    item_ref: str
    workspace_version: str
    revision_mode: ItemRevisionMode
    field_key: str = "expression"  # expression / req_type / curation_note / boundary_note / source_element_refs / verification_method / verification_note / priority
    # revised_value：标量字符串；field_key=source_element_refs 时承载要素 id 的 JSON 数组字符串（如 ["<id>"]）
    revised_value: str | None = None
    suggestion_ref: str | None = None
    accept_mode: str | None = None
    selected_point_refs: list[str] | None = None  # v5：采纳结论修订点子集的出处留痕
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class ItemRevisionResult(BaseModel):
    """AEP-036 返回（agent_run_ref=链式增量诊断；structure_recheck_run_ref=链式结构体检）。"""

    status: str  # applied / rejected_precheck
    item_ref: str
    workspace_version: str
    revision_record_ref: str | None = None
    agent_run_ref: str | None = None
    # 内容修订链式结构体检的运行引用（前端静默跟踪，完成后刷新徽标；走查第三轮裁定）
    structure_recheck_run_ref: str | None = None
    # 链式体检批次信封引用（AEP-114 读侧：终态后取逐条目结局回执）
    structure_recheck_context_ref: str | None = None
    next_action: str | None = None


class FormationDialogueCommand(BaseModel):
    """AEP-097 入参：条目形成页区5 整段对话原文（可含 /命令词）+ 上下文引用。

    body 锚定 parse_result_ref（/生成条目 须在 formation_context_ref 存在前可用）；
    formation_context_ref 仅批次建立后随上下文携带（executed 出口回读工作区用）。
    """

    project_ref: str
    parse_result_ref: str
    formation_context_ref: str | None = None
    workspace_version: str
    message: str
    item_ref: str | None = None  # 上下文：区5 当前选中条目
    selected_element_refs: list[str] = Field(default_factory=list)  # 上下文：区1 勾选集
    operator_ref: str
    idempotency_key: str  # 派发子操作幂等键为 {key}:dispatch


class FormationDialogueResult(BaseModel):
    """AEP-097 返回：命令解释回执（executed 内联工作区；queued 走 AgentRun；
    draft 出候选建议卡；explanation 纯说明零副作用）。"""

    outcome: str  # executed / queued / draft / explanation / clarify / cannot_comply / unknown_command / rejected_precheck
    command_word: str | None = None
    operation: str | None = None
    operation_label: str | None = None
    params_echo: dict | None = None
    message: str | None = None
    explanation: str | None = None
    suggestion: ItemRevisionSuggestionRead | None = None
    agent_run_ref: str | None = None
    formation_context_ref: str | None = None  # queued（/生成条目）时新批次引用
    created_item_refs: list[str] = Field(default_factory=list)  # 拆分/归并新建条目
    workspace: ItemFormationWorkspaceRead | None = None
    # 内容变更链式结构体检的运行引用（executed 出口；前端静默跟踪，完成后刷新徽标）
    structure_recheck_run_ref: str | None = None
    # 链式体检批次信封引用（AEP-114 读侧：终态后取逐条目结局回执）
    structure_recheck_context_ref: str | None = None
    next_action: str | None = None


# ---- SCN-003 条目评审（条目评审服务 AEP-032/033/034 + 需求条目服务 AEP-037）----


class ConfirmationGateRead(BaseModel):
    """确认准入读视图（通用门禁投影，SCN-004 图表确认等复用；can_submit=false 时给出阻断原因）。"""

    can_submit: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    review_summary_ref: str | None = None


class ReviewFindingRead(BaseModel):
    """诊断发现项读视图（v5：结论的只读证据行，无人工复核字段）。

    v2 质量诊断器 additive 旁路字段（rule_code/dimension/severity/evidence_span）：可空，
    旧消费者忽略即可；来自诊断轮次 quality_meta，缺失时降级为无 chip/无高亮。
    """

    finding_ref: str
    finding_type: ReviewFindingType
    diagnosis_summary: str
    basis_summary: str
    rule_code: str | None = None       # RequirementQualityRule 稳定码
    dimension: str | None = None       # QualityDimension 稳定码
    severity: str = "medium"           # QualitySeverity 稳定码
    evidence_span: str | None = None   # 基准表达中恰好出现一次的逐字片段（供高亮）
    # ---- 问题否决（AEP-116）：读投影时按指纹现算，不落库 ----
    #: 用户已裁定这条不是问题（本轮无论模型是否重提，都不计入阻断）。
    vetoed: bool = False
    veto_ref: str | None = None        # 命中的那条否决留痕（撤销时用）
    veto_reason: str | None = None     # 用户当时给的理由（可空）
    #: 这条能否被否决。指纹＝规则码+证据片段，两者皆缺则无法跨轮匹配，界面不给否决入口
    #: ——绝不拿模型自由撰写的问题摘要当匹配键（措辞漂移会同时造成误命中与漏命中）。
    can_veto: bool = False
    #: 条目已有人工确认来源，这条来源对齐类发现因此降为非阻断提示（读时现算，同 vetoed）。
    #: 只降来源对齐一类：与业务规则矛盾（BIZ-RULE-CONFLICT）及歧义/可测试性等判据照常阻断。
    source_attested: bool = False


class RevisionPointRead(BaseModel):
    """修订点读视图（分点选择、一次采纳；find 在基准表达中唯一定位）。"""

    point_ref: str
    label: str
    #: 模型输出的发现项序号。它与读视图 findings 的数组序不是一回事（发现项读出序由数据库
    #: 排序决定），故消费方应优先用下面的 finding_ref；此字段保留供存量数据与排障对照。
    finding_index: int = 0
    #: 本修订点所针对的发现项引用。存量轮次的元数据没有引用，此处为空，消费方回退按下标配对。
    finding_ref: str | None = None
    find: str
    replace: str
    basis: str = ""
    group: str | None = None
    #: 本点所针对的发现项已被用户裁定为「不是问题」，故本点不应再被采纳（界面标灰不可勾选）。
    vetoed: bool = False


class FindingVetoRead(BaseModel):
    """问题否决留痕读视图（AEP-116）：用户裁定「这条不是问题」的记录，跨轮生效。"""

    veto_ref: str
    item_ref: str
    finding_type: ReviewFindingType
    rule_code: str | None = None
    evidence_span: str | None = None
    finding_summary: str = ""          # 否决当时那条问题的摘要（展示用，不参与匹配）
    reason: str | None = None          # 用户理由（可选）
    operator_ref: str = ""
    at: str = ""                       # 登记时间
    revoked: bool = False
    revoked_at: str | None = None


class SourceAttestationRead(BaseModel):
    """人工确认背书读视图：材料里没写这条，由人确认它是真实需求并负责登记。

    **独立证据类别**，与「来源要素」并列而不混同：背书不产生任何材料锚点、不生成引文，
    条目的 source_element_refs 保持原样（背书条目通常为空）。界面据此显示背书标记与理由，
    绝不据此显示原文——因为确实没有对应的材料位置。
    """

    record_ref: str
    reason: str                        # 用户填的理由（必填，原文照录）
    operator_ref: str = ""
    at: str = ""                       # 登记时间


class VerdictAdjudicationRead(BaseModel):
    """结论裁决读视图（AEP-034 留痕投影）。"""

    decision: VerdictDecision
    selected_point_refs: list[str] = Field(default_factory=list)
    excluded_point_refs: list[str] = Field(default_factory=list)
    #: 采纳时用户对所选点替换文本的改稿（{point_ref: 用户终稿}）；空＝未改稿，采纳的就是 AI 原案。
    #: AI 原案始终在同轮 revision_points 里原样保留，两者并存供留痕对照。
    point_edits: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    operator_ref: str = ""
    at: str = ""


class VerdictRead(BaseModel):
    """LDM-009 轮次读视图（v5：结论=判断；线程结论卡与收折回执的素材）。"""

    round_ref: str
    round_no: int = 1
    # run/batch 关联（issue #10 B2a，只增不改）：每轮次归属的诊断批次与条目，供按 run 归因失败
    # 事实（弃用前端以条目全局最新态猜测本 run 失败的归因错位）。旧消费者忽略即可。
    batch_ref: str = ""
    item_ref: str = ""
    diagnosis_mode: DiagnosisMode
    trigger: DiagnosisTrigger = DiagnosisTrigger.USER_SUBMIT
    status: str  # running / completed / failed（诊断处理状态投影）
    verdict_kind: VerdictKind | None = None
    verdict_summary: str | None = None
    findings: list[ReviewFindingRead] = Field(default_factory=list)
    revision_points: list[RevisionPointRead] = Field(default_factory=list)
    supplement_gaps: list[str] = Field(default_factory=list)
    context_coverage: str = ""
    model_result_refs: list[str] = Field(default_factory=list)
    invalidated: bool = False
    invalidated_reason: str | None = None
    superseded_by: str | None = None
    adjudication: VerdictAdjudicationRead | None = None
    overridden: bool = False
    confirm_result: str | None = None
    effective: bool = False  # 当前版本有效且未被裁决/替代（=待裁决的站立结论）
    reason: str | None = None  # 失败/未能诊断原因
    created_at: str = ""
    # v2 质量诊断器旁路（可空）：质量画像 6 维评分 / EARS 改写脚手架
    quality_profile: dict | None = None   # {overall, dimensions:[{key,score,note}]}
    ears_rewrite: dict | None = None      # {pattern_type, lines:[...], note}
    # ---- 问题否决派生（AEP-116）：读投影时现算，随撤销自动恢复 ----
    #: 本轮仍然成立的阻断性问题条数（发现项类型非 no_blocker、且未被否决者计数）。
    blocking_finding_count: int = 0
    #: 本轮曾报出阻断性问题，但它们已被用户逐条裁定为「不是问题」，一条不剩。
    #: 只认用户的逐条裁定，不认人工确认降格——「已被逐条裁定（N 条）」这句留痕由它驱动。
    all_blocking_findings_vetoed: bool = False
    #: 本轮曾报出阻断性问题，且此刻一条待用户处理的都不剩（被裁定或因人工确认降格都算）。
    #: 这是「直接确认」通道的开门条件；服务端确认时会重新核算一遍，不采信前端传来的判断。
    blocking_findings_cleared: bool = False


class SourceAlignmentRead(BaseModel):
    """来源语义对齐分读视图（AEP-105）：LLM 逐源 alignment + source_drift 派生 drift 合并。"""

    element_ref: str
    wing: str | None = None            # knowledge_category：requirement / business
    anchor: str | None = None          # 来源锚点引文
    alignment: float | None = None     # 0.0–1.0（LLM 产出；无诊断/未产出为 None）
    drift: bool = False                # 是否偏离（source_drift_tokens 非空）
    drift_tokens: list[str] = Field(default_factory=list)
    note: str | None = None


class ItemQualityRead(BaseModel):
    """需求条目质量投影（AEP-105）：最新一轮诊断的质量元数据，详情卡「质量诊断」页签数据源。"""

    item_ref: str
    req_no: str
    base_expression: str               # 基准表达 = span/对齐分定位锚
    has_diagnosis: bool = False
    round_ref: str | None = None
    round_no: int = 0
    status: str = ""                   # running / completed / failed
    verdict_kind: VerdictKind | None = None
    verdict_summary: str | None = None
    quality_profile: dict | None = None
    findings: list[ReviewFindingRead] = Field(default_factory=list)
    revision_points: list[RevisionPointRead] = Field(default_factory=list)
    ears_rewrite: dict | None = None
    source_alignments: list[SourceAlignmentRead] = Field(default_factory=list)


class DialogueMessageRead(BaseModel):
    """评审对话消息读视图（解释 / 草案；LDM-015 投影，领域零副作用）。"""

    message_ref: str
    kind: DialogueOutcomeType
    user_message: str = ""
    text: str = ""                      # 解释正文
    draft_value: str | None = None      # 草案完整表达
    draft_note: str | None = None       # 缺来源预警
    draft_seq: int | None = None        # 稿次
    suggestion_ref: str | None = None   # 采纳草案时的 AEP-036 候选建议引用
    in_flight: bool = False             # 在途草案（未采纳未放弃）
    #: 这条交换发生在哪一页（review/formation）。条目形成页与本页共用同一个阶段键，
    #: 本页只显示自己的交换，例外是形成页留下的在途候选建议——它要显示，但须标明来源。
    #: 存量载荷没有这一项，为空即来源不明，按原样显示不猜。
    origin: str | None = None
    created_at: str = ""


class ReviewRequirementItemRead(BaseModel):
    """条目评审读视图（v5：LDM-007 + 结论/轮次历史 + 对话消息 + 派生显示态）。"""

    item_ref: str
    req_no: str
    expression: str
    req_type: RequirementItemType
    status: RequirementItemStatus
    version_no: str = "1"
    source_element_refs: list[str] = Field(default_factory=list)
    formation_basis_ref: str | None = None
    verification_method: list[str] = Field(default_factory=list)  # 验证方式（29148 属性补齐）
    verification_note: str | None = None  # 验收准则（缺失=评审"建议补充来源"前置信号）
    priority: str | None = None  # 条目优先级（仅人工设定）
    revision_records: list[ItemRevisionRecordRead] = Field(default_factory=list)
    review_status: ReviewItemStatus  # 派生显示态：无结论/诊断中/待裁决/已确认/已终止
    status_note: str = ""            # 显示态说明（如：结论被拒绝待人驱动、来源缺口未闭合）
    # 用户可见显示态封闭集（issue #10 B2a，只增不改）：把 review_status 的 no_verdict 细分为
    # 待诊断/诊断失败/结论已拒绝/待补充来源。display_note=对应说明句单点（含待裁决说明句、
    # 诊断失败连击次数、到达路径副语），B2b 前端两处（区1/区5）消费同一字段，退役 deriveReviewDisplay。
    display_code: ReviewDisplayCode = ReviewDisplayCode.PENDING_DIAGNOSIS
    display_note: str = ""
    current_verdict: VerdictRead | None = None      # 站立结论（待裁决）
    verdict_history: list[VerdictRead] = Field(default_factory=list)  # 已裁决/已替代/失效轮次（新→旧）
    dialogue_messages: list[DialogueMessageRead] = Field(default_factory=list)  # 旧→新
    supplement_gaps_open: list[str] = Field(default_factory=list)  # 未闭合来源缺口（阻断再诊断）
    #: 已按 AI 建议采纳过多少次「建议修订」仍未通过。**纯事实，不是门禁**——
    #: 评审往复的终点只有「AI 判通过」与「人工撤回」两个，什么时候不值得再改由用户判断
    #: （2026-07-20 用户拍板废除原采纳链空转熔断）。界面据此给非阻断提示，不禁用任何入口。
    adopted_revise_rounds: int = 0
    #: 本条目的问题否决留痕（含已撤销者，新→旧）：用户裁定过「不是问题」的完整账目，界面可查。
    finding_vetoes: list[FindingVetoRead] = Field(default_factory=list)
    #: 人工确认背书（材料未记载该需求，由人确认它成立）。空＝没有背书过。
    #: 它是与来源要素并列的独立证据类别，不是来源要素的一种——界面必须分开显示。
    source_attestation: SourceAttestationRead | None = None
    #: 当前这次「旧结论失效」正是人工确认造成的——即来源缺口刚刚被确认闭合。
    #: 与 source_attestation 的区别：后者是粘性事实（背过书就一直在），本字段只在那一刻为真，
    #: 背书之后的任何一次普通修订都会让它转假。界面据此把说明句升格为醒目横幅（说明句仍取
    #: display_note，不另造文案），判据与说明句选句同一单点（见服务层同名派生）。
    attestation_closed_gap: bool = False
    available_actions: list[ActionFact] = Field(default_factory=list)


class DiagnosisRunProgressRead(BaseModel):
    """诊断批次轻量进度（批次只汇总过程状态，不新增业务事实）。"""

    run_ref: str
    item_refs: list[str] = Field(default_factory=list)
    diagnosis_mode: DiagnosisMode
    status: str  # running / completed
    completed_count: int = 0
    total_count: int = 0
    # 本批次内失败轮次数（issue #10 B2a，只增不改）：按 run 直接归因，分子=该批 processing_status
    # ∈{failed,not_diagnosable} 的轮次数。前端弃用以条目全局最新态猜测本 run 失败（结算窗口内被新批
    # 重诊则漏报、跨批遗留失败可误计）。批次收束后仍保留可查（契约守卫），failed_count 随之稳定。
    failed_count: int = 0
    next_action: str | None = None


class ItemReviewWorkspaceRead(BaseModel):
    """条目评审页面唯一工作区读视图（AEP-033；线程/会话条/动态流三投影的素材）。"""

    review_context_ref: str
    formation_context_ref: str
    workspace_version: str
    material_canvas: MaterialCanvasRead | None = None
    source_elements: list[RequirementElementRead] = Field(default_factory=list)
    review_items: list[ReviewRequirementItemRead] = Field(default_factory=list)
    diagnosis_options: list[DiagnosisMode] = Field(default_factory=list)
    diagnosis_runs: list[DiagnosisRunProgressRead] = Field(default_factory=list)
    available_operations: list[ActionFact] = Field(default_factory=list)
    confirmed_count: int = 0
    total_count: int = 0
    next_action: str | None = None


class ItemReviewDiagnosisCommand(BaseModel):
    """AEP-032 startDiagnosis 入参（批次级提交、条目级处理）。"""

    project_ref: str
    item_refs: list[str] = Field(default_factory=list)
    diagnosis_mode: str  # DiagnosisMode 稳定码（不可识别时业务拒绝，不走 422）
    workspace_version: str
    operator_ref: str
    idempotency_key: str


class ItemReviewDiagnosisRequestResult(BaseModel):
    """AEP-032 受理返回（受理立即返回；批次经 agent_run_ref 追踪，逐条目实时刷新）。"""

    status: str  # submitted / rejected_precheck
    review_context_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class VerdictAdjudicationCommand(BaseModel):
    """AEP-034 adjudicateVerdict 入参（v5 重定义：裁决对象=结论；副作用链后端原子执行）。"""

    project_ref: str
    item_ref: str
    round_ref: str
    decision: VerdictDecision
    selected_point_refs: list[str] | None = None  # 仅采纳 revise：所选修订点（None=全选）
    #: 仅采纳 revise：用户对所选点替换文本的改稿（{point_ref: 用户终稿}）。不传＝按 AI 原案采纳，
    #: 行为与本字段引入前完全一致。定位片段 find 不变，只换替换文本；空字符串会被拒（等同取消勾选）。
    point_edits: dict[str, str] | None = None
    reason: str | None = None                     # 拒绝必填（回复正文即理由）
    workspace_version: str
    operator_ref: str
    idempotency_key: str


class FindingVetoCommand(BaseModel):
    """AEP-116 否决/恢复一条诊断问题（用户裁定「这条不是问题」，或撤销该裁定）。

    action=veto 时给 finding_ref（用户正看着的那条发现项，服务端由它取指纹）；
    action=restore 时给 veto_ref（要撤销的那条留痕）。
    """

    project_ref: str
    item_ref: str
    action: str                    # veto / restore
    finding_ref: str | None = None
    veto_ref: str | None = None
    reason: str | None = None      # 否决理由（可选）
    operator_ref: str
    idempotency_key: str


class SourceAttestationCommand(BaseModel):
    """人工确认背书入参：材料里漏写了这条，人工确认它是真实需求。

    这是对「条目的依据必须能在材料里指出来」的**授权例外**，所以理由必填、操作者与时间
    全程留痕；服务端只登记背书事实，绝不代写任何材料锚点或引文。不设撤销动作——
    见服务层 attest_source 的说明。
    """

    project_ref: str
    item_ref: str
    reason: str                        # 必填：为什么它是真实需求
    operator_ref: str = Field(min_length=1)  # 必填：授权例外要能追到人，不许留空
    idempotency_key: str


class ReviewDialogueCommand(BaseModel):
    """AEP-095 reviewDialogue 入参（对话面：无斜杠自由文本领域零写入；
    2026-07-06 扩展：message 可含 /命令词，斜杠命令解释后派发既有端点逻辑）。"""

    project_ref: str
    item_ref: str
    message: str
    draft_ref: str | None = None  # 在途草案引用（迭代时带上）
    selected_item_refs: list[str] = Field(default_factory=list)  # 上下文：区1 勾选的诊断范围
    workspace_version: str
    operator_ref: str
    idempotency_key: str
    #: 这条命令是不是用户亲手输入的。页面自行发起的命令（如条目进入「待补充来源」态时
    #: 自动查一次候选来源）传 False：它不是用户说的话，不写演示留痕，否则每次进页面都会
    #: 多出一对用户从未输入过的气泡（冷审查 T20260718-demo-chat-transcript F2）。
    #: 缺省 True——不传即按用户输入处理，既有调用方与手敲命令的行为都不变。
    user_initiated: bool = True


class SourceCandidateRead(BaseModel):
    """为条目找候选来源读视图（issue #30；候选=同批次已确认、未链接到本条的要素投影）。

    element_ref 逐条取自服务算出的差集，content/source_quote 为要素事实（原文引文为登记依据），
    reason/rank 为 AI lane 的推荐理由与相关度排序。前端候选卡与一键登记接线属后续卡。
    """

    element_ref: str
    element_type: str = ""
    content: str = ""
    source_quote: str | None = None  # 首个原文引文（登记为来源时的依据；无锚点时为 None）
    reason: str = ""                 # AI 推荐理由
    rank: int = 0                    # 相关度排序（1 最相关）


class ReviewDialogueResult(BaseModel):
    """AEP-095 返回（四出口：解释 / 草案 / 轻量重评运行引用 / 斜杠命令回执）。"""

    outcome_type: DialogueOutcomeType
    explanation: str | None = None
    draft: DialogueMessageRead | None = None
    agent_run_ref: str | None = None  # reeval / 命令派发出队列型运行时返回
    next_action: str | None = None
    # ---- outcome_type=command 的解释回执（时间线审计）----
    command_word: str | None = None
    operation: str | None = None
    operation_label: str | None = None
    params_echo: dict | None = None
    message: str | None = None  # clarify / cannot_comply / 未知命令 / 校验失败的用户可见文案
    # ---- /找来源 命令的候选来源载荷（issue #30；非空即候选卡，前端凭此渲染，只加不改）----
    source_candidates: list[SourceCandidateRead] | None = None


class ItemConfirmationCommand(BaseModel):
    """AEP-037 确认写入入参（v5：仅覆盖确认直写路径保留用户面；理由必填）。"""

    project_ref: str
    item_ref: str
    workspace_version: str
    override: bool = False       # 覆盖确认（无视站立结论；理由必填）
    reason: str | None = None
    review_summary_ref: str | None = None  # 兼容字段（历史客户端）
    operator_ref: str
    idempotency_key: str


class ItemConfirmationResult(BaseModel):
    """确认写入结果或准入失败原因。"""

    status: str  # confirmed / rejected_precheck
    item_ref: str
    item_status: RequirementItemStatus
    next_action: str | None = None


class ItemWithdrawCommand(BaseModel):
    """人工撤回入参（待确认 → 已终止；理由必填）。"""

    project_ref: str
    item_ref: str
    workspace_version: str
    reason: str
    operator_ref: str
    idempotency_key: str


class ItemWithdrawResult(BaseModel):
    status: str  # terminated / rejected_precheck
    item_ref: str
    item_status: RequirementItemStatus
    next_action: str | None = None


class AgentRunEventRead(BaseModel):
    event: str
    at: str


class AgentRunRead(BaseModel):
    """异步任务进度（agentRunApi.get）。"""

    id: str
    kind: str
    status: str  # AgentRunStatus 稳定码
    error: str | None = None
    events: list[AgentRunEventRead] = Field(default_factory=list)


# ---- SCN-005 发布管理（文档编排服务 / 导出执行服务）----


class TemplateSectionRead(BaseModel):
    """模板章节元数据（模板文件适配器抽取；含槽位与必填规则）。"""

    key: str
    number: str
    title: str
    level: int
    purpose: str
    content_types: list[str] = Field(default_factory=list)
    required: bool
    repeatable: bool
    missing_policy: str
    boilerplate: str | None = None
    examples: list[str] = Field(default_factory=list)  # 章节样例（AI 起草少样本；复制起草反填用）
    # 撰稿时是否提供「从目录选取」引用标准的入口（T20260721）：
    # ＝章节标题看起来是参考资料类（domain/reference_standards.py 单点判定） ∧ 支持人工撰稿。
    # 判定结果由后端算好下发，前端不得散落章节 key 字符串。默认 False：模板登记等不涉及撰稿
    # 的读路径不必关心它。
    standards_pickable: bool = False


class TemplateDescriptorRead(BaseModel):
    """模板描述读取结果（schema 校验通过时 sections 非空；失败时 error 说明原因）。"""

    template_ref: str
    schema_version: str | None = None
    title: str | None = None
    description: str | None = None
    export_binding: dict | None = None  # docx 渲染绑定（模板定制器复制起草回填用）
    sections: list[TemplateSectionRead] = Field(default_factory=list)
    error: str | None = None


class CandidateItemRead(BaseModel):
    """候选资产池·确认态需求条目（候选视图不等于入文档许可）。"""

    item_ref: str
    req_no: str
    expression: str
    req_type: RequirementItemType
    status: RequirementItemStatus
    version_no: str


class CandidateMaterialRead(BaseModel):
    """候选资产池·支撑材料。"""

    material_ref: str
    source_note: str
    excerpt: str
    source_version: int


class CandidateChartRead(BaseModel):
    """候选资产池·受控图表（status=confirmed 才进入候选；候选≠许可）。"""

    chart_ref: str
    title: str
    chart_type: str
    format: str
    status: str
    draft_version: int
    source_count: int
    confirmed_at: str | None = None


class TraceBindingSummaryRead(BaseModel):
    """LDM-013 追溯绑定只读摘要（追溯依据不入文档内容，仅供候选池追溯 tab 展示）。"""

    effective: int = 0
    pre_established: int = 0
    suspect: int = 0


class CandidateAssetsRead(BaseModel):
    """P01-N04 候选资产视图（追溯依据只读摘要，不作为可编排内容）。"""

    items: list[CandidateItemRead] = Field(default_factory=list)
    materials: list[CandidateMaterialRead] = Field(default_factory=list)
    charts: list[CandidateChartRead] = Field(default_factory=list)
    traces: list[str] = Field(default_factory=list)
    trace_summary: TraceBindingSummaryRead | None = None
    pending_item_count: int = 0


class SectionManuscriptRead(BaseModel):
    """LDM-014.章节撰稿读视图（AEP-098：人工撰写内容为第一类正文来源）。"""

    section_key: str
    content: str
    revision_no: int
    updated_by: str
    updated_at: str


class SectionDraftResultRead(BaseModel):
    """AEP-110 起草结果信封（T20260721 改造）：起草成功与模型拒绝都是 HTTP 200。

    模型拒绝起草（如章节零依据，照它的判断编内容就是编造）是**正常业务结果**而非请求错误，
    所以走 status='declined' 带上理由原文，让界面把理由当一等回执呈现；早先把它当 400 抛，
    理由会被接口层拼的 URL 与状态码前缀淹没。

    仍走 400 的两种情况不变：模型服务不可用（真故障）、非人工撰稿章节的预检拒绝（用错入口）。
    """

    status: str  # drafted / declined
    manuscript: SectionManuscriptRead | None = None  # status=drafted 时必有
    reason: str | None = None  # status=declined 时必有：模型给出的拒绝理由原文


class SectionDraftBasisRead(BaseModel):
    """某个可撰稿章节的「AI 起草依据」计数（T20260721）。

    口径与起草服务实际喂给模型的一致：asset_count＝挂在本章节且已确认的需求条目数，
    example_count＝模板给本章节写的样例数。两者都为 0 时模型通常会拒绝起草，界面据此在
    点击前就提示，把拒绝提前到点击之前。
    """

    section_key: str
    asset_count: int = 0
    example_count: int = 0


class SaveManuscriptCommand(BaseModel):
    """保存章节撰稿：仅可撰稿章节（boilerplate/authored_text）；content 空白 = 回落默认文本。"""

    project_ref: str
    template_ref: str | None = None  # 文档未创建时按此建档（缺省内置模板）
    section_key: str
    content: str
    operator_ref: str


class DraftManuscriptCommand(BaseModel):
    """AEP-110：章节撰稿 AI 起草初稿（仅 authored_text 章节；写撰稿阶段，人工可改可清空）。"""

    project_ref: str
    template_ref: str | None = None
    operator_ref: str


class CandidatePreviewRead(BaseModel):
    """候选资产渲染预览（AEP-099）：与生成稿同一确定性渲染器，预览即最终渲染。"""

    asset_type: str  # requirement_item / chart / material
    asset_ref: str
    title: str
    markdown: str


class DocIndexEntryRead(BaseModel):
    """文档内容索引条目（只有引用与位置，无正文）。"""

    section_key: str
    asset_type: str
    asset_ref: str | None = None
    asset_version: str = "1"
    order_no: int = 0


class SlotStatusRead(BaseModel):
    """槽位满足状态（索引编排页左栏）。"""

    section_key: str
    required: bool
    satisfied: bool
    filled_count: int
    missing_reason: str | None = None
    rebuild_entry: str | None = None


class MissingItemRead(BaseModel):
    """缺失清单条目（补建依据，不是文档正文内容）。"""

    section_key: str
    section_title: str
    reason: str
    rebuild_entry: str


class MarkdownPatchRead(BaseModel):
    """预览编辑补丁（未定稿前不是正式资产）。"""

    patch_ref: str
    impact: str  # EditImpact 稳定码
    before_text: str
    after_text: str
    bound_item_ref: str | None = None
    reflow_item_ref: str | None = None
    status: str
    note: str | None = None


class SourceBindingRead(BaseModel):
    """Markdown 行区间 → 源资产绑定（编辑影响识别依据）。"""

    start_line: int
    end_line: int
    kind: str  # heading / boilerplate / item / material / chart
    section_key: str
    asset_ref: str | None = None


class MarkdownDraftRead(BaseModel):
    """Markdown 中间稿/定稿读视图。"""

    draft_ref: str
    version_no: int
    index_version: int
    status: str  # MarkdownDraftStatus 稳定码
    can_export: bool
    content: str
    source_bindings: list[SourceBindingRead] = Field(default_factory=list)
    block_reasons: list[str] = Field(default_factory=list)
    patches: list[MarkdownPatchRead] = Field(default_factory=list)
    finalized_by: str | None = None
    finalized_at: str | None = None


class DocxExportRead(BaseModel):
    """候选 docx 导出件读视图（候选≠发布）。"""

    export_ref: str
    draft_ref: str
    status: str  # DocxExportStatus 稳定码
    failure_reason: str | None = None
    manual_fallback: bool = False
    check_note: str | None = None
    file_available: bool = False
    created_at: str


class ReleaseBaselineRead(BaseModel):
    """发布基线快照（只读复核视图）。"""

    baseline_ref: str
    document_ref: str
    index_version: int
    draft_ref: str
    template_ref: str
    template_schema_version: str
    export_ref: str
    manual_fallback: bool
    asset_refs: list[str] = Field(default_factory=list)
    confirmed_by: str
    confirmed_at: str
    note: str | None = None


class RequirementDocumentRead(BaseModel):
    """LDM-014 需求文档读视图。"""

    document_ref: str
    doc_type: str
    title: str
    template_ref: str
    template_schema_version: str
    coverage_scope: str | None = None
    status: str  # DocumentStatus 稳定码
    blocked_reason: str | None = None
    index_version: int


class DocumentFragmentRead(BaseModel):
    """资产在 Markdown 稿中的绑定片段（行区间切片；追溯预览用，只读）。"""

    section_key: str
    section_number: str
    section_title: str
    start_line: int
    end_line: int
    markdown: str


class AssetFragmentRead(BaseModel):
    """资产 → 文档片段追溯读视图（追溯依据不入 docx 正文；绑定由生成时落库）。"""

    project_ref: str
    asset_type: str  # requirement_item / chart
    asset_ref: str
    document_ref: str | None = None
    document_title: str | None = None
    document_status: str | None = None
    draft_ref: str | None = None
    draft_version: int | None = None
    draft_status: str | None = None  # MarkdownDraftStatus 稳定码
    index_version: int | None = None
    in_current_index: bool = False
    baseline_ref: str | None = None
    fragments: list[DocumentFragmentRead] = Field(default_factory=list)
    next_action: str | None = None


class PublicationWorkspaceRead(BaseModel):
    """发布管理工作台唯一工作区读视图（索引编排页 + 发布主工作台共用）。"""

    project_ref: str
    document: RequirementDocumentRead | None = None
    template: TemplateDescriptorRead
    candidates: CandidateAssetsRead
    manuscripts: list[SectionManuscriptRead] = Field(default_factory=list)
    # 每个可 AI 起草章节的起草依据计数（零依据章节据此在点击前提示；口径同起草服务）
    draft_basis: list[SectionDraftBasisRead] = Field(default_factory=list)
    index_entries: list[DocIndexEntryRead] = Field(default_factory=list)
    slot_status: list[SlotStatusRead] = Field(default_factory=list)
    missing_list: list[MissingItemRead] = Field(default_factory=list)
    markdown: MarkdownDraftRead | None = None
    exports: list[DocxExportRead] = Field(default_factory=list)
    baseline: ReleaseBaselineRead | None = None
    next_action: str | None = None


class SaveIndexCommand(BaseModel):
    """P01 保存文档内容索引（含准入校验；缺必填→受阻+缺失清单）。"""

    project_ref: str
    template_ref: str | None = None  # 缺省用内置 SRS 模板
    coverage_scope: str | None = None
    entries: list[DocIndexEntryRead] = Field(default_factory=list)
    operator_ref: str
    idempotency_key: str


class SaveIndexResult(BaseModel):
    """P01 索引保存结果。"""

    status: str  # index_ready / index_blocked / rejected_precheck
    document_ref: str | None = None
    index_version: int | None = None
    missing_list: list[MissingItemRead] = Field(default_factory=list)
    blocked_reason: str | None = None
    next_action: str | None = None


class GenerateMarkdownCommand(BaseModel):
    """P02 生成/重新生成 Markdown 中间稿。"""

    project_ref: str
    operator_ref: str
    idempotency_key: str


class MarkdownEditCommand(BaseModel):
    """P02 窗口微调：提交编辑后全文，系统 diff 识别编辑影响并记录补丁。"""

    project_ref: str
    draft_ref: str
    content: str
    operator_ref: str


class MarkdownEditResult(BaseModel):
    """P02 编辑影响识别结果（即时预览反馈）。"""

    status: str  # recorded
    draft_ref: str
    patches: list[MarkdownPatchRead] = Field(default_factory=list)
    can_finalize: bool = True
    block_reasons: list[str] = Field(default_factory=list)
    pending_item_refs: list[str] = Field(default_factory=list)  # 待修订确认态条目
    next_action: str | None = None


class FinalizeMarkdownCommand(BaseModel):
    """P02 定稿确认。confirm_reflow=true 表示用户已确认待修订确认态条目清单。"""

    project_ref: str
    draft_ref: str
    confirm_reflow: bool = False
    operator_ref: str
    idempotency_key: str


class FinalizeMarkdownResult(BaseModel):
    """P02 定稿结果。"""

    status: str  # finalized / pending_item_confirmation / item_revision_reflowed / blocked
    draft_ref: str
    pending_items: list[MarkdownPatchRead] = Field(default_factory=list)
    reflowed_item_refs: list[str] = Field(default_factory=list)
    block_reasons: list[str] = Field(default_factory=list)
    next_action: str | None = None


class ReopenIndexCommand(BaseModel):
    """P02→P01 调整索引编排（当前稿标记需重新生成）。"""

    project_ref: str
    operator_ref: str


class StartDocxExportCommand(BaseModel):
    """P03 发起 docx 导出（只能从可导出的 Markdown 定稿版本进入）。"""

    project_ref: str
    draft_ref: str
    operator_ref: str
    idempotency_key: str


class StartDocxExportResult(BaseModel):
    """P03 导出受理结果（转换经 agent_run_ref 追踪；inline 模式立即完成）。"""

    status: str  # submitted / rejected_precheck
    export_ref: str | None = None
    agent_run_ref: str | None = None
    next_action: str | None = None


class ExportCheckCommand(BaseModel):
    """P03 候选 docx 检查结论承接。"""

    project_ref: str
    export_ref: str
    passed: bool
    note: str | None = None
    operator_ref: str


class ManualFallbackCommand(BaseModel):
    """P03 人工降级导出件登记（必须标记，不算系统转换成功）。"""

    project_ref: str
    draft_ref: str
    reason: str
    operator_ref: str
    idempotency_key: str


class ConfirmBaselineCommand(BaseModel):
    """P03 发布基线确认（未经用户确认不得形成基线）。"""

    project_ref: str
    export_ref: str
    note: str | None = None
    operator_ref: str
    idempotency_key: str


class ConfirmBaselineResult(BaseModel):
    """P03 基线确认结果。"""

    status: str  # confirmed / rejected_precheck
    baseline_ref: str | None = None
    next_action: str | None = None


class ItemConfirmCommand(BaseModel):
    """需求条目最小确认门禁（SCN-003 完整评审链另行承接）。"""

    project_ref: str
    item_ref: str
    operator_ref: str
    idempotency_key: str


class ItemConfirmResult(BaseModel):
    """条目确认结果。"""

    status: str  # confirmed / rejected_precheck
    item_ref: str
    item_status: RequirementItemStatus
    next_action: str | None = None


# ---- 需求资产目录服务（AEP-052 资产盘点 / AEP-072 跨任务状态聚合，只读投影）----

class FlowStageStatusRead(BaseModel):
    """新增需求流程单阶段状态（派生，非存储）。"""

    stage: str  # intake / analysis / itemFormation / itemReview
    status: str  # done / in_progress / not_started / stopped
    detail: str | None = None  # 停靠原因/边界说明摘要


class RequirementFlowRead(BaseModel):
    """AEP-072 单条「新增需求」流程投影（以接入请求上下文为根，实时派生）。"""

    flow_id: str  # = intake_context_ref
    title: str
    summary: str | None = None  # 服务端短语，如「知识抽取 · 进行中」
    current_stage: str  # intake / analysis / itemFormation / itemReview
    resume_stage: str  # 恢复落点（当前恒 = current_stage）
    resumable: bool
    # 终结态（需补充/已排除）可处置：继续编辑（AEP-112 预填重提）/放弃本次接入（AEP-111 软删）。
    # 死路（无可处理要素）不可处置——与 resumable=False 是两个口径（OVW-001 修订 2026-07-10）。
    dismissable: bool = False
    stages: list[FlowStageStatusRead]  # 固定 4 项，顺序 = 四阶段
    intake_context_ref: str
    material_ref: str | None = None
    parse_context_ref: str | None = None
    formation_context_ref: str | None = None
    updated_at: str  # ISO8601（到达最深行时间戳）


class IntakePrefillRead(BaseModel):
    """AEP-112 继续编辑预填读视图：旧上下文提交内容；编辑后仍走 AEP-001 重提为新流程。"""

    context_ref: str
    raw_text: str
    source_note: str


class FlowDismissCommand(BaseModel):
    """AEP-111 放弃本次接入（软删）入参。"""

    operator_ref: str


class FlowDismissRead(BaseModel):
    """AEP-111 结果：dismissed_at 非空即总览投影不再显示（记录保留可审计）。"""

    context_ref: str
    dismissed_at: str  # ISO8601


class OverviewStatMetricRead(BaseModel):
    """总览计数指标（只放事实；tone/label/目标工作面归前端展示层）。"""

    key: str
    value: int


class OverviewCoverageRead(BaseModel):
    """覆盖度方向（口径复用追溯分析服务 AEP-062；总览台只读转投影，不持第二事实源）。"""

    key: str  # item_source / item_chart / item_document
    covered: int
    total: int
    ratio: float


class OverviewTraceRiskRead(BaseModel):
    """追溯与风险小计（缺口/可疑来自追溯分析服务；问题项=LDM-011 计数）。"""

    gaps: int
    suspects: int
    issues: int


class OverviewConversionChainRead(BaseModel):
    """需求转化链四节点（识别 → 人工确认 → 条目形成 → 需求条目）。

    与同一响应内其余计数出自同一次事实载入，故下列恒等式必然成立（服务层单测逐条断言）：
    elements_total = elements_requirement + elements_other；
    elements_requirement = elements_confirmed + elements_pending；
    materials_with_requirement = materials_formed + materials_unformed；
    items_total = items_pending + items_confirmed + items_closed = items_sourced + items_direct。
    """

    # 阶段一 识别产出
    elements_total: int              # 已有知识项（存量：排除被替代与已撤销）
    elements_requirement: int        # 需求类（可形成条目的五类；恒等于 requirement_type_metrics 之和）
    elements_other: int              # 非需求类（作分析上下文，不形成条目）
    # 阶段二 人工确认
    elements_confirmed: int
    elements_pending: int
    # 阶段三 条目形成（材料口径）
    materials_with_requirement: int  # 识别出需求类知识项的材料份数
    materials_formed: int            # 其中已有条目产出的份数
    materials_unformed: int
    # 产出 需求条目
    items_total: int
    items_pending: int
    items_confirmed: int
    items_closed: int                # 已了结＝被替代 + 已终止
    items_sourced: int               # 可回溯到知识项来源
    items_direct: int                # 直建（无知识项来源）


class OverviewTypeBridgeRead(BaseModel):
    """数字桥：某需求类型从知识项到条目的逐步去向账（五类各一份，一次下发）。

    行内闭合：elements_total = elements_confirmed + elements_pending；
    elements_confirmed = entered_formation + not_formed；
    not_formed = not_formed_material_pending + not_formed_not_adopted；
    items_total = items_sourced + items_direct。
    「进入形成 → 条目」跨对象（左侧数知识项、右侧数条目），故不构成等式。
    """

    key: str                            # functional/quality/constraint/data/interface
    elements_total: int                 # 该类已有知识项
    elements_confirmed: int
    elements_pending: int
    entered_formation: int              # 已被至少一条条目引用为来源
    not_formed: int
    not_formed_material_pending: int    # 其中：所在材料尚未执行条目形成
    not_formed_not_adopted: int         # 其中：材料已执行形成但该知识项未被采用
    items_from_elements_same_type: int  # 由该类知识项形成、且自身为该类的条目数
    items_from_elements_other_type: int # 由该类知识项形成、但被定为其它类型的条目数
    items_total: int                    # 该类条目总数
    items_sourced: int                  # 来自知识项
    items_direct: int                   # 直建


class OverviewRead(BaseModel):
    """GET /projects/{id}/overview —— AEP-052 计数 + AEP-072 流程投影（单次往返）。"""

    project_ref: str
    asset_metrics: list[OverviewStatMetricRead]  # materials/elements/items/charts/documents/issues
    requirement_type_metrics: list[OverviewStatMetricRead]  # functional/quality/constraint/data/interface
    requirement_status_metrics: list[OverviewStatMetricRead]  # pending / confirmed / closed
    coverage: list[OverviewCoverageRead] = Field(default_factory=list)
    trace_risk: OverviewTraceRiskRead | None = None
    flows: list[RequirementFlowRead]
    conversion_chain: OverviewConversionChainRead | None = None
    type_bridge: list[OverviewTypeBridgeRead] = Field(default_factory=list)


# ---- 模板注册表（配置域：登记快照 / 停用 / 预览）----


class TemplateRegistryRead(BaseModel):
    """模板注册行读视图（登记快照；内容不外发，经 descriptor/预览消费）。"""

    registry_ref: str
    template_key: str
    version_no: int
    name: str
    schema_version: str
    doc_type: str
    content_hash: str
    source: str  # builtin / registered
    status: str  # active / disabled
    registered_by: str
    registered_at: str


class TemplateRegisterCommand(BaseModel):
    """模板登记入参：内容送检（内置 schema），失败整体拒绝不落库。"""

    content: str  # 模板文件 JSON 原文
    name: str | None = None  # 缺省取模板 title
    operator_ref: str
    idempotency_key: str


class TemplateRegistryDetailRead(TemplateRegistryRead):
    """模板注册行详情（含结构预览 descriptor）。"""

    descriptor: TemplateDescriptorRead


class TemplateStatusCommand(BaseModel):
    """模板停用/启用（唯一可变字段；无删除：基线引用内容须永久可解析）。"""

    status: str  # active / disabled
    operator_ref: str


class TemplateValidateCommand(BaseModel):
    """模板干跑送检入参（AEP-100：只校验，不落库不占版本号）。"""

    content: str  # 模板文件 JSON 原文


class TemplateValidationRead(BaseModel):
    """模板干跑送检结果（模板定制器实时校验消费）。"""

    ok: bool
    error: str | None = None  # 问题清单全文（一次性列出）
    descriptor: TemplateDescriptorRead | None = None  # ok 时的结构预览


class TemplateDraftRead(BaseModel):
    """模板定制草稿读视图（工作态：未送检、不占版本号、不可被发布消费）。"""

    draft_ref: str
    name: str
    payload: str  # 定制器状态 JSON 信封（designer_state_version + info/binding/tree）
    origin: str  # blank / copy / edit
    source_registry_ref: str | None = None  # copy/edit 起点登记行
    created_by: str
    created_at: str
    updated_at: str


class TemplateDraftSaveCommand(BaseModel):
    """草稿暂存入参（POST 新建 / PUT 覆盖；payload 后端不解析只存取）。"""

    name: str = ""  # 展示名（取定制器模板名称，可为空）
    payload: str
    origin: str = "blank"  # blank / copy / edit（仅新建时生效）
    source_registry_ref: str | None = None  # 仅新建时生效
    operator_ref: str


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


# ---- 运行态面板 / 诊断中心（04A §2.1,基础设施只读投影,非业务事实源）----


class RuntimeComponentRead(BaseModel):
    """单个平台组件的探测结果。status:ok/degraded/down/not_applicable。"""

    key: str  # api | db | redis | worker | event_bus
    label: str
    status: str
    detail: str | None = None


class RuntimeAlertRead(BaseModel):
    """活跃风险组(按组去重,现算不累加)。level:WARN/ERROR。"""

    code: str
    level: str
    summary: str
    hint: str | None = None


class AsyncJobsSummaryRead(BaseModel):
    """异步作业摘要(agent_run 表聚合 + Redis 队列深度)。"""

    mode: str  # inline | queued
    queued: int | None = None
    running: int | None = None
    failed_recent: int | None = None  # 近 24h 失败数
    oldest_waiting_minutes: int | None = None
    queue_depth: int | None = None  # Redis 队列积压(inline 模式为 None)


class RecentAgentRunRead(BaseModel):
    """最近一条异步作业(运行态面板明细表一行)。

    只投影稳定码与派生读数:kind_label 是给人看的白话名,reason_code 是登记过的失败原因码,
    AgentRun.error 的原文一律不出现在本结构里(硬规则 8)。
    """

    run_id: str
    kind: str  # AgentRun.kind 稳定码
    kind_label: str  # 白话名(单一来源=services/notification.AGENT_RUN_KIND_LABELS)
    status: str  # queued | started | succeeded | failed
    created_at: str  # 发起时刻 ISO
    duration_seconds: int | None = None  # 终态才有;非终态=None(等待中/进行中)
    reason_code: str | None = None  # 失败行的原因稳定码


class DiagnosticEventRead(BaseModel):
    """诊断事件白名单摘要(进程内环形缓冲,只读展示)。"""

    event: str
    component: str
    level: str
    first_seen: str
    last_seen: str
    count: int


class RuntimeStatusRead(BaseModel):
    """GET /api/runtime-status。status:normal/degraded/down;alert_count=活跃风险组数。"""

    status: str
    alert_count: int
    generated_at: str
    components: list[RuntimeComponentRead]
    alerts: list[RuntimeAlertRead]
    async_jobs: AsyncJobsSummaryRead
    recent_jobs: list[RecentAgentRunRead] = []  # 最近异步作业明细(加性扩展)
    diagnostics: list[DiagnosticEventRead]


# ---- 通知徽标（04A §2.1:需人处理的未读事项,按 dedup_key 去重）----


class NotificationRead(BaseModel):
    """单条通知读视图。occurrences=同一事项复发次数(不影响徽标计数)。"""

    id: str
    kind: str
    title: str
    summary: str
    project_ref: str | None = None
    ref: str | None = None
    occurrences: int
    read: bool
    created_at: str
    updated_at: str


class NotificationListRead(BaseModel):
    """GET /api/notifications。unread_count=徽标计数(未读事项数,按事项去重)。"""

    notifications: list[NotificationRead]
    unread_count: int


class NotificationActionResult(BaseModel):
    """标记已读结果。status:marked_read/already_read/all_read。"""

    status: str
    unread_count: int


# ---- 追溯分析服务（TRC-001，AEP-058…AEP-066；只读投影 + 复核路由/转问题项）----


class TraceNodeRead(BaseModel):
    """关系网节点读视图（五类资产的最小展示投影，非事实源）。"""

    node_type: str  # material / element / requirement_item / chart / document
    ref: str
    label: str  # 材料节点=原文头优先（source_note 降为详情面板字段，2026-07-12 演进）
    sub_label: str | None = None  # 类型/编号稳定码（前端映射展示）
    status: str | None = None  # 节点自身生命周期状态稳定码
    updated_at: str | None = None
    source_note: str | None = None  # 仅材料节点：接入登记的来源说明（详情面板「来源说明」）


class TraceEdgeRead(BaseModel):
    """关系网边读视图。origin=ldm013 时 link_ref 可查 AEP-061 详情；derived 为结构派生投影。"""

    edge_key: str
    relation_kind: str  # material_element / element_item / chart_source / document_reference
    origin: str  # ldm013 / derived
    upstream_type: str
    upstream_ref: str
    downstream_type: str
    downstream_ref: str
    status: str  # LDM-013 四态照录；derived 边恒 derived
    link_ref: str | None = None
    status_reason: str | None = None
    # 仅 material_element 边（2026-07-12 演进）：下游知识项来源锚点引文（LDM-005.source_anchor.
    # ranges[].exact 逐字原文）。anchor_quote=首条（卡片用），anchor_quotes=全部（详情面板列全）；
    # 锚点缺失/解析失败 = null/空列表。只读可再生投影，不入权威、不参与门禁。
    anchor_quote: str | None = None
    anchor_quotes: list[str] = Field(default_factory=list)


class TraceLevelRead(BaseModel):
    """邻域窗口单层（distance 从 1 起；折叠摘要=窗口外对象的摘要节点表达）。"""

    distance: int
    nodes: list[TraceNodeRead] = Field(default_factory=list)
    edges: list[TraceEdgeRead] = Field(default_factory=list)  # 连接上一层（或焦点）的边
    folded_count: int = 0
    folded_by_type: dict[str, int] = Field(default_factory=dict)


class TraceChainRead(BaseModel):
    """AEP-059/060 单方向链路读（漫游重定心=以新焦点重取）。"""

    project_ref: str
    direction: str  # upstream / downstream
    focus: TraceNodeRead
    depth: int
    limit: int
    include_invalid: bool = False
    levels: list[TraceLevelRead] = Field(default_factory=list)


class TraceAnchorGroupRead(BaseModel):
    """AEP-058 锚点分组（各类型最近更新前 N 条，供左区对象导航）。"""

    node_type: str
    nodes: list[TraceNodeRead] = Field(default_factory=list)


class TraceCountsRead(BaseModel):
    """AEP-058 项目级小计数（关系四态 + 诊断角标；冲突无判定规则事实源恒 0/待接入）。"""

    links_total: int
    effective: int
    pre_established: int
    suspect: int
    invalid: int
    gaps: int
    conflicts: int = 0
    conflicts_available: bool = False


class TraceEntryRead(BaseModel):
    """AEP-058 入口锚点 + 小计数（只回入口与计数，不含明细）。"""

    project_ref: str
    anchors: list[TraceAnchorGroupRead] = Field(default_factory=list)
    default_focus: TraceNodeRead | None = None
    counts: TraceCountsRead
    next_action: str | None = None


class TraceCoverageDirectionRead(BaseModel):
    """AEP-062 单方向覆盖度（预建立不计入条目→图表覆盖）。"""

    key: str  # item_source / item_chart / item_document
    covered: int
    total: int
    ratio: float  # 0..1；total=0 时为 1.0（无应覆盖对象视为满覆盖）


class TraceCoverageRead(BaseModel):
    project_ref: str
    directions: list[TraceCoverageDirectionRead] = Field(default_factory=list)


class TraceGapItemRead(BaseModel):
    """AEP-063 缺口/孤儿项（补全=带上下文导航，nav_target 为目标工作台稳定码）。"""

    kind: str  # item_no_source / item_no_chart / item_no_document / chart_orphan / element_orphan
    node_type: str
    node_ref: str
    label: str
    detail: str
    nav_target: str  # requirement_workbench / diagram_workbench / publication_workbench


class TraceGapListRead(BaseModel):
    project_ref: str
    items: list[TraceGapItemRead] = Field(default_factory=list)
    total: int = 0


class TraceSuspectListRead(BaseModel):
    """AEP-064 可疑失效链路清单（LDM-013 照录；include_invalid 时并列失效项）。"""

    project_ref: str
    items: list[TraceLinkRead] = Field(default_factory=list)
    total: int = 0


class TraceReviewCommand(BaseModel):
    """AEP-066 可疑链路复核（结论交追溯图谱模块按迁移表重判，本服务只路由）。"""

    conclusion: str  # restore / maintain
    reason: str | None = None
    operator_ref: str
    idempotency_key: str


class TraceReviewResult(BaseModel):
    """restored=可疑→预建立（sync-trace；后续须重走图表核对确认）；maintained=状态不变留痕。"""

    status: str  # restored / maintained
    link: TraceLinkRead
    next_action: str | None = None


class TraceIssueCommand(BaseModel):
    """AEP-066 转问题项（origin_kind=trace_diagnosis；闭环归 SCN-006）。"""

    title: str
    description: str | None = None
    issue_type: str | None = None  # 缺省 gap
    trace_link_ref: str | None = None
    chart_ref: str | None = None
    operator_ref: str
    idempotency_key: str


class SupportingBasisCommand(BaseModel):
    """人工补全支撑依据边入参（P4 06 A.1）：业务翼确认态要素 → 需求条目。

    条目确认态 → 边直接有效；条目待确认 → 预建立（P7 引用依据；随条目确认转有效）。
    """

    element_ref: str
    item_ref: str
    operator_ref: str


class SupportingBasisResult(BaseModel):
    link_ref: str
    status: str  # pre_established | effective
    next_action: str | None = None


# ---------------------------------------------------------------------------
# 需求资产目录·资产读侧（04A §5 资产树/详情 + §3.1 维护列表；只读投影）
# ---------------------------------------------------------------------------


class AssetNodeRead(BaseModel):
    """资产树节点（只读目录视图；树节点不是新的事实对象，UINV-09）。"""

    ref: str
    label: str
    sub_label: str | None = None  # 稳定码（element_type/req_type/chart_type…），展示映射归前端
    status: str | None = None  # 稳定码
    updated_at: str | None = None


class AssetGroupRead(BaseModel):
    asset_type: str  # material / element / requirement_item / chart / trace_link / document / issue
    count: int
    nodes: list[AssetNodeRead] = Field(default_factory=list)


class AssetTraceSummaryRead(BaseModel):
    effective: int
    pre_established: int
    suspect: int
    invalid: int


class QualityAlertSummaryRead(BaseModel):
    """质量告警聚合（v2 KPI）：项目内已诊断条目最新一轮发现项按严重度计数（未诊断不计）。"""

    high: int = 0
    medium: int = 0
    low: int = 0
    diagnosed_items: int = 0


class WorkbenchReservedRead(BaseModel):
    """v2 工作台预留接口占位（AEP-106/107/108）：类型就位、返回 deferred，后端后续 drop-in。

    追溯覆盖矩阵 / AI 副驾聚合 / 变更影响·风险预测三模块本轮仅预留；前端 DeferredBadge 呈现，
    不造假数据（仿 overview deferredNote，见 v2 方案 04 篇 §2）。
    """

    deferred: bool = True
    note: str
    items: list = Field(default_factory=list)


class AssetCatalogRead(BaseModel):
    project_ref: str
    groups: list[AssetGroupRead] = Field(default_factory=list)
    trace_summary: AssetTraceSummaryRead
    quality_alert_summary: QualityAlertSummaryRead = Field(default_factory=QualityAlertSummaryRead)


class AssetAttributeRead(BaseModel):
    """资产详情键值行：key 为稳定码，标签文案归前端。"""

    key: str
    value: str


class AssetRelationRead(BaseModel):
    kind: str  # source_material / derived_element / referenced_by_item / covered_by_chart / covers_item / upstream / downstream
    asset_type: str
    ref: str
    label: str


class AssetDetailRead(BaseModel):
    asset_type: str
    ref: str
    label: str
    sub_label: str | None = None
    status: str | None = None
    summary: str = ""
    attributes: list[AssetAttributeRead] = Field(default_factory=list)
    relations: list[AssetRelationRead] = Field(default_factory=list)


class ItemMaintenanceItemRead(BaseModel):
    """维护列表行（04A §3.1：只显示需求条目及其维护状态）。"""

    ref: str
    req_no: str
    expression: str
    req_type: str
    status: str
    updated_at: str | None = None
    source_count: int = 0
    revision_count: int = 0
    priority: str | None = None  # 条目优先级（可选列）
    verification_missing: bool = False  # 缺验收准则警示（29148 属性补齐；仅警示不硬卡）
    priority_missing: bool = False      # 缺优先级警示（评审/确认前应人工补齐）
    quality_score: int | None = None    # 最新诊断轮质量分（无诊断/无画像为 None，不伪造）
    quality_alert: str | None = None    # 最新诊断轮最重严重度 high/medium/low（无发现项为 None）


class ItemMaintenanceListRead(BaseModel):
    project_ref: str
    items: list[ItemMaintenanceItemRead] = Field(default_factory=list)
    total: int = 0


class BusinessKnowledgeRowRead(BaseModel):
    """业务知识维护列表行（AEP-104；05 §2）。业务领域知识翼要素的只读治理面。"""

    ref: str
    element_type: str
    knowledge_category: str  # 派生，恒为 "business"（端点只列业务翼），显式回契约
    content: str
    process_status: str
    source_count: int = 1       # 来源锚点/材料数（P3 归并后为多锚点计数；当前单锚点）
    referenced_count: int = 0   # 被引用计数（P4 支撑依据投影后填；P4 前恒 0）
    updated_at: str | None = None


class BusinessKnowledgeListRead(BaseModel):
    project_ref: str
    items: list[BusinessKnowledgeRowRead] = Field(default_factory=list)
    total: int = 0


class ItemSourceEvidenceRead(BaseModel):
    element_ref: str
    element_type: str
    content: str
    material_label: str | None = None


class ItemRevisionRead(BaseModel):
    """LDM-007 字段修订留痕（AEP-036 改前/改后/操作者）。"""

    field_key: str
    before_value: str
    after_value: str
    revision_mode: str
    reason: str | None = None
    operator_ref: str
    created_at: str


class ItemRelatedCountsRead(BaseModel):
    charts: int = 0
    documents: int = 0
    trace_effective: int = 0
    trace_suspect: int = 0


class ItemMaintenanceCardRead(BaseModel):
    """需求卡片（选中条目详情：内容/来源依据/修订留痕/关联计数）。"""

    ref: str
    req_no: str
    expression: str
    req_type: str
    status: str
    updated_at: str | None = None
    verification_method: list[str] = Field(default_factory=list)  # 29148 属性补齐
    verification_note: str | None = None
    priority: str | None = None
    source_evidence: list[ItemSourceEvidenceRead] = Field(default_factory=list)
    revisions: list[ItemRevisionRead] = Field(default_factory=list)
    related: ItemRelatedCountsRead


# ==== 配置管理入口（04 §3.5 / CONN-006 / 04A §9）====
# 密钥只写不回显：ConfigSecretRead 只带 set 标志与脱敏占位，任何 Read 都不含明文。


class ConfigDomainStatusRead(BaseModel):
    """配置域状态（设置工作台左区菜单：已配置/默认值签）。"""

    domain: str
    label: str
    group: str  # 身份与权限 / 外部能力（外观为本地偏好，不经后端）
    downstream: str  # 下游单元（04 §3.5 配置域模块表）
    configured: bool  # 是否已保存过配置（false = 生效值来自 env 默认）
    source: str  # saved / env
    updated_at: str | None = None
    updated_by: str | None = None


class ConfigFieldRead(BaseModel):
    key: str
    value: str | int | float | None = None
    source: str  # saved / env


class ConfigSecretRead(BaseModel):
    """密钥字段读投影：只报告是否已设置，绝不回显明文。"""

    key: str
    set: bool
    placeholder: str  # 已设置 → 脱敏占位；未设置 → 空串


class ConfigDomainRead(BaseModel):
    domain: str
    label: str
    group: str
    downstream: str
    source: str  # saved / env
    updated_at: str | None = None
    updated_by: str | None = None
    fields: list[ConfigFieldRead] = Field(default_factory=list)
    secrets: list[ConfigSecretRead] = Field(default_factory=list)


class ConfigSaveCommand(BaseModel):
    """保存配置：values 为非敏字段；secrets 空串=保留原值（脱敏占位未重输）。"""

    values: dict[str, str | int | float | None] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    operator_ref: str


class ConfigSaveResult(BaseModel):
    domain: str
    saved: bool
    changed_keys: list[str] = Field(default_factory=list)
    audit_ref: str


class ModelConnectionTestCommand(BaseModel):
    """模型服务测试连接：api_key 现输现用；未输且 use_saved_key → 用已保存密钥。

    未保存的草稿也能测：地址/模型/类型全部随请求体来，服务端只在需要已存密钥时读库；
    整个动作不写库、不改启用状态。
    """

    base_url: str
    model: str | None = None
    timeout_seconds: float = 5.0
    api_key: str | None = None
    # 默认 False：不显式选用已存密钥就不带它。缺省为 True 会让「只给 base_url」的裸请求替调用方
    # 取出已存明文密钥发往请求体给定的任意地址（无鉴权端点，密钥外泄面）。前端只在这条已存过
    # 密钥、且草稿地址仍等于已存地址时才显式置 True。
    use_saved_key: bool = False
    # 两级测试：reachability=带鉴权探模型列表；generation=发一次最小生成请求验证能真的回话。
    level: str = "reachability"
    provider_type: str = "llama_cpp"
    # 取已保存密钥时用哪个 provider 的密钥（草稿未保存则留空，走 default）。
    provider_id: str | None = None


class ModelConnectionTestResult(BaseModel):
    """测试连接结果：仅状态/延迟/稳定结果码，不含密钥与原始响应体。

    `outcome` 是封闭集里的稳定结果码，白话文案由前端映射（走查改措辞不必动后端）：
    ok / unreachable（服务不可达）/ timeout（响应超时）/ auth_failed（鉴权失败）/
    model_missing（模型不存在）/ bad_response（响应形状异常）。
    `error_code` 保留原有的原始错误标识（HTTP 状态或异常类名），供排查用。
    """

    ok: bool
    latency_ms: int | None = None
    model_count: int | None = None
    error_code: str | None = None
    level: str = "reachability"
    outcome: str = "ok"
    # 第一级：配置的模型标识是否出现在端点返回的模型列表里（未配置模型标识时为 None）。
    model_listed: bool | None = None
    # 第二级：回复内容的字符数（只报长度不报正文——响应体不外带，硬规则 8）。
    reply_length: int | None = None
    # 端点返回的模型标识清单（前端做「模型不存在」提示时给候选；至多前 20 个）。
    models: list[str] = Field(default_factory=list)


# ---- 导出能力就绪清单（T20260724：docx 导出依赖的本地工具链逐项探测；只读，无写接口）----


class ExportReadinessItemRead(BaseModel):
    """单条导出能力的就绪结果：只给稳定结果码与探到的事实，白话文案由前端映射。

    `key` 是能力（不是二进制名）的封闭集：
    pdf_preview（文档转 PDF 预览）/ mermaid_diagram（流程图渲染）/ plantuml_diagram（结构图渲染）。
    `outcome` 是封闭集里的稳定结果码，缺失时指出缺的是哪一个依赖：
    ready / soffice_missing / mmdc_missing / java_missing / plantuml_jar_missing。
    `path` 是定位到的可执行文件或 jar 路径（缺失时为 None）；`version` 取不到时为 None，
    且**不影响 `ready`**——就绪与否只由定位结果决定，与渲染时的判据同源。
    """

    key: str
    ready: bool
    outcome: str
    path: str | None = None
    version: str | None = None


class ExportReadinessRead(BaseModel):
    """导出能力就绪清单：逐项探测本地工具链，纯定位＋版本，不做任何转换。"""

    checked_at: str
    all_ready: bool
    items: list[ExportReadinessItemRead] = Field(default_factory=list)

class CapabilityItemRead(BaseModel):
    """能力清单里的一条。只回稳定代码与实测数值，**白话文案由前端映射**。

    这样走查阶段改措辞不必动后端，且文案本身可单测——与既有两级连通测试的 outcome 同一套口径。
    `key` 取值：reachable / generate / thinking_off / structured / context / unknown_fields；
    `state` 取值：supported（可用）/ degraded（有条件）/ unsupported（不可用）/ unknown（没探明）。
    键与取值的封闭集定义在 app/adapters/llm.py，前端不得另写一份。
    """

    key: str
    state: str
    # C3：探明的关思考方式（reasoning_effort / enable_thinking / none）。
    mode: str | None = None
    # C3：这个端点/模型会不会思考（null=没探明）。「思考模式」开关的可用性说明取自这里——
    # 它与 state 回答的是两个问题：available 说有没有思考这回事，state 说能不能把它关掉。
    available: bool | None = None
    # C4：实测强制生效的最高档（json_schema / json_object / prompt_only）。
    tier: str | None = None
    # C5：有效上下文（token）与它的出处（models.max_model_len / props.n_ctx / api_show.context_length）。
    tokens: int | None = None
    source: str | None = None
    # 结论之外还要告诉用户的那一句话的代码（如 vllm_needs_reasoning_parser）。
    note_code: str | None = None
    # C1/C2 沿用既有两级连通测试的稳定结果码；其余项在探测出错时放异常类名。
    outcome: str | None = None
    latency_ms: int | None = None
    # 判定依据的数值事实（基线/候选各自的延迟与输出 token 数、试过哪些字段）。绝不含响应正文。
    detail: dict = Field(default_factory=dict)


class ModelCapabilityProbeResult(BaseModel):
    """逐能力探测的结果：清单 + 一份可「应用」的能力档案。

    探测本身不写库、不改启用状态——档案要等用户点「应用」、随 provider 配置保存才生效。
    """

    items: list[CapabilityItemRead] = Field(default_factory=list)
    # 可直接写回 provider 的 capability_profile（形状见 app/adapters/llm.py）。
    profile: dict = Field(default_factory=dict)
    probed_at: str
    # 基线两项（可达＋能生成）是否都过。没过时后四项一律 unknown：连回话都不行就谈不上验产物。
    ok: bool = False


# ---- 模型服务多 provider（T20260720：列表管理 + 启用指针；存储零迁移，落既有配置行）----


class LlmProviderTypeRead(BaseModel):
    """provider 类型封闭集目录（前端下拉的唯一来源，禁前端另写一份清单）。"""

    key: str
    label: str
    description: str


class LlmProviderRead(BaseModel):
    """单个 provider 读投影：密钥只报是否已设置，绝不回显明文。"""

    id: str
    name: str
    provider_type: str
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int
    concurrency_limit: int
    api_key_set: bool
    active: bool
    # 思考模式：是否让这个模型服务带思考跑。默认关——思考模型开着思考时重流程慢 20–50 倍
    # 直至超时，且思考段可能吃光输出预算导致正文为空（116 实测，见能力探测与参数适配提案）。
    thinking_enabled: bool = False
    # 能力探测档案：对这个端点探到的事实（能否关思考、结构化输出真生效到哪一档、有效上下文
    # 多大、探测时间）。空字典=从未探测，适配层按 provider 类型的先验默认走。
    # 形状定义在 app/adapters/llm.py（CapabilityProfile.to_payload），此处只做透明投影。
    capability_profile: dict = Field(default_factory=dict)


class LlmProviderListRead(BaseModel):
    active_provider_id: str
    providers: list[LlmProviderRead] = Field(default_factory=list)
    provider_types: list[LlmProviderTypeRead] = Field(default_factory=list)
    # saved = 库里已存 providers 数组；env = 尚未保存过，列表由存量平铺配置或 env 投影而来。
    source: str = "env"
    updated_at: str | None = None
    updated_by: str | None = None


class LlmProviderWrite(BaseModel):
    """单个 provider 写入项：id 留空=新增（服务端派号）；api_key 留空=保留原值。"""

    id: str | None = None
    name: str
    provider_type: str
    base_url: str
    model: str
    timeout_seconds: float = 180.0
    max_retries: int = 3
    concurrency_limit: int = 5
    api_key: str | None = None
    # 显式清除已保存密钥（与「留空=保留原值」区分开）。
    clear_api_key: bool = False
    # 能力探测档案：缺席（null）=保留库里已存的那份，与密钥「留空=保留原值」同一套语义；
    # 显式给出才覆盖（设置页点「应用探测结果」时带上），给空字典即清空。
    capability_profile: dict | None = None
    # 思考模式开关：同样是缺席=保留原值，显式给出才覆盖。
    thinking_enabled: bool | None = None


class LlmProviderSaveCommand(BaseModel):
    """整表替换：providers 即保存后的完整列表，缺席者视为删除（其密钥一并清除）。"""

    providers: list[LlmProviderWrite] = Field(default_factory=list)
    active_provider_id: str | None = None
    operator_ref: str


# ---------------------------------------------------------------------------
# AEP-118 引用标准目录（配置域 reference_standards）
# ---------------------------------------------------------------------------
# 清单定义的单一来源是 app/domain/reference_standards.py：本处只做读写投影，不复制任何
# 条目内容与类别标签。只登记引用元数据，不承接标准全文（全文走材料接入）。


class ReferenceStandardCategoryRead(BaseModel):
    """类别封闭集的一项；中文标签由后端给，前端不硬编码。"""

    key: str
    label: str


class ReferenceStandardRead(BaseModel):
    """目录中的一条引用标准条目。

    builtin=True 的条目随代码版本化，只可停用（enabled=False）不可编辑；
    builtin=False 的自有条目可增可改可删，其 enabled 恒为 True。
    """

    key: str
    code: str
    title: str
    year: str = ""
    issuer: str = ""
    note: str = ""
    category: str
    category_label: str
    url: str = ""
    builtin: bool
    enabled: bool


class ReferenceStandardCatalogRead(BaseModel):
    """目录全集（内置＋自有），含被停用的内置条目。

    返回全集而非只返回启用项：设置页要展示被停用的内置条目才能让用户恢复它们。只消费启用
    项的一方（撰稿选取器）按 enabled 自行过滤。
    """

    entries: list[ReferenceStandardRead] = Field(default_factory=list)
    categories: list[ReferenceStandardCategoryRead] = Field(default_factory=list)
    builtin_count: int = 0
    custom_count: int = 0
    disabled_count: int = 0
    # saved = 库里存过用户层数据；builtin = 从未保存过，目录全部来自内置清单。
    source: str = "builtin"
    updated_at: str | None = None
    updated_by: str | None = None


class ReferenceStandardWrite(BaseModel):
    """单条自有条目写入项：key 留空＝按标准号自动生成标识。"""

    key: str | None = None
    code: str
    title: str
    year: str = ""
    issuer: str = ""
    note: str = ""
    category: str
    url: str = ""


class ReferenceStandardSaveCommand(BaseModel):
    """整表替换用户层：custom_entries 即保存后的完整自有条目列表，缺席者视为删除。

    内置条目不出现在这里——它们改不了，只能出现在 disabled_builtin_keys 里被停用。
    """

    custom_entries: list[ReferenceStandardWrite] = Field(default_factory=list)
    disabled_builtin_keys: list[str] = Field(default_factory=list)
    operator_ref: str


# ---------------------------------------------------------------------------
# AEP-094 AI 效能按环节统计（模型推理结果仓储·统计读面；AI效能统计口径设计 §6）
# ---------------------------------------------------------------------------


class AiStageEffectRead(BaseModel):
    """按环节采纳明细计数（比率与中文标签归前端；分母口径=口径设计 D3）。"""

    stage: str
    total: int = 0                    # 窗口内已收口明细（adopted+adopted_with_revision+rejected+transferred_to_issue）
    pending_records: int = 0          # 窗口内记录级 pending 提示
    adopted: int = 0
    adopted_with_revision: int = 0
    rejected: int = 0
    transferred_to_issue: int = 0


class AiCalibrationBucketRead(BaseModel):
    range: str                        # 例 "0.6-0.7"
    avg_confidence: float
    accuracy: float
    count: int


class AiCalibrationRead(BaseModel):
    """置信度校准（样本=识别明细×要素置信度；口径设计 §5.2）。"""

    ece: float | None = None
    rating: str = "insufficient"      # excellent/good/fair/poor/insufficient
    sample_size: int = 0
    buckets: list[AiCalibrationBucketRead] = Field(default_factory=list)


class AiCoverageRead(BaseModel):
    touched: int = 0
    untouched: int = 0
    not_applicable: int = 0           # 非管线产生（直写导入）
    total_items: int = 0


class AiRiskSignalRead(BaseModel):
    key: str                          # low_confidence / rejection_rising / issue_conversion / source_conflict
    level: str                        # high / medium / low / deferred
    value: int = 0


class AiFailureStageCountRead(BaseModel):
    """失败关卡分桶计数（口径设计 §5.5）。"""

    failure_stage: str                # parse/llm_error/structure/aggregation/synthesis/unclassified
    count: int = 0


class AiDeliveryFailureRead(BaseModel):
    """交付失败＝AI 未能交出合法结论（LDM-015 judgement=*_failed），按 lane 聚合。

    与「拒绝率」（人工不采纳，采纳明细 rejected）是正交维度，禁混算（口径设计 §5.5）。
    失败率=failed/total 归前端；total=该 lane 窗口内总判定行数（分母）。
    """

    stage: str                        # lane 稳定码（LDM-015.stage）
    total: int = 0                    # 窗口内该 lane 总判定行数（分母）
    failed: int = 0                   # *_failed 行数（分子）
    by_failure_stage: list[AiFailureStageCountRead] = Field(default_factory=list)


class AiEffectivenessRead(BaseModel):
    """GET /projects/{id}/ai-effectiveness —— 只读统计，不改变 LDM-015 处理状态（UINV-23）。"""

    project_ref: str
    window_days: int
    stages: list[AiStageEffectRead] = Field(default_factory=list)
    calibration: AiCalibrationRead
    coverage: AiCoverageRead
    risk_signals: list[AiRiskSignalRead] = Field(default_factory=list)
    delivery_failures: list[AiDeliveryFailureRead] = Field(default_factory=list)


class AiDeliveryFailureInstanceRead(BaseModel):
    """交付失败个案（钻取；口径 §5.5）。白话字段，不含 Prompt/模型原文（AGENTS 硬规 8）。

    run_status = best-effort 关联 AgentRun（kind==stage ∧ context_ref==applies_to_ref）状态，
    用于从统计接入运行态·诊断中心的重试/降级跟进；无法关联时为 null。
    """

    occurred_at: str                  # LDM-015.created_at（ISO）
    failure_stage: str                # parse/llm_error/structure/aggregation/synthesis/unclassified
    detail: str = ""                  # 白话失败详情（failure.detail 或 basis）
    subject_req_no: str | None = None # 受影响条目编号（条目类 lane 可解析时）
    run_status: str | None = None     # queued/started/succeeded/failed（best-effort）


class AiDeliveryFailureInstancesRead(BaseModel):
    """GET /projects/{id}/ai-effectiveness/delivery-failures —— 某 lane[×失败关卡] 的失败个案钻取。"""

    stage: str
    failure_stage: str | None = None
    window_days: int
    total_failed: int = 0             # 过滤下失败总数（可能 > 返回条数）
    instances: list[AiDeliveryFailureInstanceRead] = Field(default_factory=list)


# ---- 全局检索（GET /api/search；04 篇 §2）----
class SearchHitRead(BaseModel):
    """单条命中。ref = 稳定语义引用（(asset_type, ref) 口径）→ 深链锚；
    workbench/label 由服务端派生（04 §3），前端不自行判定落点。"""

    project_id: str
    project_name: str          # 跨项目：面板按项目标注、导航切项目所需
    entity_type: str           # material|element|requirement_item|chart|document
    ref: str
    title: str
    snippet: str               # 匹配片段（服务端生成，03 §5）
    workbench: str             # 目标工作台 WorkbenchKey 码（management|diagram|release…）
    score: float               # RRF 融合分
    status: str | None = None


class SearchGroupRead(BaseModel):
    entity_type: str
    label: str                 # 中文组头，取 labels.SEARCH_ENTITY_GROUP_LABELS 单一来源
    hits: list[SearchHitRead] = Field(default_factory=list)
    total: int                 # 该类命中总数（可 > len(hits)）


class SearchResultsRead(BaseModel):
    query: str
    groups: list[SearchGroupRead] = Field(default_factory=list)
    total: int


class ChatTranscriptRowRead(BaseModel):
    """演示留痕一行（AI 对话演示简化方案 2026-07-18 §2.3 读点）。

    content 为已解析的 JSON 载荷：`{text}` 或找来源的 `{text, candidates:[...]}`。
    role/kind 供前端水合时映射气泡语气（role+kind→ChatMsg.kind / ChatMessage 部件）。
    """

    id: str
    channel: str
    context_ref: str
    role: str
    kind: str
    content: dict = Field(default_factory=dict)
    created_at: str


class ChatTranscriptRead(BaseModel):
    """按 (channel, context_ref) 拉取的留痕行（created_at 升序，(created_at, id) 消歧保序）。"""

    rows: list[ChatTranscriptRowRead] = Field(default_factory=list)
