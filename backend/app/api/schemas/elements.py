"""分析转化服务：要素识别、复核与校正修订。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

from app.domain.enums import (
    ElementProcessStatus,
    ElementType,
    MaterialParseStatus,
    ModelVerdict,
    NoiseTriage,
    RecognitionOutcome,
    RecognitionRequestStatus,
    ReviewConclusion,
    knowledge_category_of,
)

from .materials import ActionFact


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
