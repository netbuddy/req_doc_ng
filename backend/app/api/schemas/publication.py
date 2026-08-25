"""发布管理：文档编排服务与导出执行服务。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import (
    RequirementItemStatus,
    RequirementItemType,
)


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
