"""追溯分析服务（TRC-001）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .charts import TraceLinkRead


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
