"""需求资产目录：资产盘点、跨任务状态聚合与资产树读侧。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


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
