"""条目形成服务与需求规约方案目录。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import (
    ItemRevisionMode,
    ItemizationResultStatus,
    ItemizationScopeType,
    RequirementItemStatus,
    RequirementItemType,
)

from .materials import ActionFact

from .elements import ElementFacetFindingRead, MaterialCanvasRead, RequirementElementRead


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
