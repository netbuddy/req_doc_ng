"""持久实体 ORM（LDM-001/002/003/015 + 过程记录 接入请求上下文）。

设计事实源：docs/40 domains/DS-001/data.md、DS-004/data.md。
枚举以稳定 ASCII 码存 String 列；id 为 UUID（跨库 sqlalchemy.Uuid）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.functions import GenericFunction

from app.config import settings
from app.db.base import Base
from app.domain.errors import RejectedTransition

# 知识层快照内容列：正式库用 JSONB（可查询可索引），SQLite 测试库退化为普通 JSON。
KNOWLEDGE_CONTENT_JSON = JSON().with_variant(JSONB(), "postgresql")


class Project(Base):
    """LDM-001 业务项目。"""

    __tablename__ = "ldm001_project"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    scope: Mapped[str | None] = mapped_column(Text, default=None)
    background: Mapped[str | None] = mapped_column(Text, default=None)
    # P6b 领域档案（封闭集 key；None=generic=不注入领域先验，零迁移安全）
    domain_profile_key: Mapped[str | None] = mapped_column(String(64), default=None)
    # 2026-08-07 项目管理组重构：记创建操作者（V2「每笔操作留痕记用户标识」的接口准备；存量行空串）。
    operator_ref: Mapped[str] = mapped_column(String(64), default="")
    # 创建请求的幂等键：同键重放返回同一项目，不重复建行（存量行为 NULL）。
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntakeRequest(Base):
    """接入请求上下文（过程记录仓储；记录前瞬态，承载提交内容供后续形成 LDM-002）。"""

    __tablename__ = "process_intake_request"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    source_note: Mapped[str] = mapped_column(Text, default="")
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    stop_next_action: Mapped[str | None] = mapped_column(Text, default=None)
    # 放弃本次接入（软删，OVW-001 修订 2026-07-10）：非空即总览投影不再显示；行不物理删除，过程记录可审计。
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# 结构复核受理信封 payload 内的幂等键字段名（写侧与迁移回填单一来源，禁裸字符串双份）。
RECHECK_IDEMPOTENCY_PAYLOAD_KEY = "idempotency_key"


class ModelResult(Base):
    """LDM-015 模型推理结果记录（来源接入判断类；不持久化 Prompt/敏感原文）。"""

    __tablename__ = "ldm015_model_result"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    applies_to_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    stage: Mapped[str] = mapped_column(String(32), default="source_intake")
    judgement: Mapped[str] = mapped_column(String(32))  # 结果分类稳定码（接入=ModelJudgement；识别=recognized/no_elements/failed）
    basis: Mapped[str | None] = mapped_column(Text, default=None)  # 判定依据（不含 Prompt/原始响应）
    result_content: Mapped[str | None] = mapped_column(Text, default=None)  # 结构化结果 JSON（识别要素集；不含 Prompt/敏感原文）
    process_status: Mapped[str] = mapped_column(String(32), default="pending")
    # T20260713-recheck-idem-migration（issue #12 卡B）：结构复核受理信封幂等键升为索引列，
    # 取代 result_content LIKE 片段匹配（%/_/反斜杠三病＋跨项目泄漏）。仅 recheck 受理信封行写值，
    # 其余 stage 恒 NULL；nullable+unique（多 NULL 行不冲突），与其余六处 idempotency_key 范式对齐。
    recheck_idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Material(Base):
    """LDM-002 需求材料（仅确认接收后形成）。raw_text 为当前来源版本正文。"""

    __tablename__ = "ldm002_material"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    source_note: Mapped[str] = mapped_column(Text, default="")
    content_form: Mapped[str] = mapped_column(String(32), default="plain_text")
    source_version: Mapped[int] = mapped_column(Integer, default=1)  # 勘误出新版本时 +1
    # 材料一态制两列（2026-08-07 裁定，docs/v2/drafts/材料接入字段级差异表-讨论稿.md 5-补）：
    # name＝展示用标签（非身份，允许同名），默认取正文首行；content_sha256＝导入时一次计算，
    # 「导入即不可改写」的机器凭据，兼作同内容重复导入的查重依据。
    name: Mapped[str] = mapped_column(String(200), default="")
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaterialRevision(Base):
    """LDM-002 来源版本快照（勘误前的旧正文留档；原快照不改写）。"""

    __tablename__ = "ldm002_material_revision"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    material_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    source_version: Mapped[int] = mapped_column(Integer)  # 该快照对应的版本号
    raw_text: Mapped[str] = mapped_column(Text)  # 该版本正文快照
    note: Mapped[str | None] = mapped_column(Text, default=None)  # 勘误说明（改了什么）
    operator_ref: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaterialSupplement(Base):
    """LDM-002 补入来源块（追加原文没有的新事实；留痕补入人与依据，原快照不动）。"""

    __tablename__ = "ldm002_material_supplement"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    material_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    content: Mapped[str] = mapped_column(Text)
    basis: Mapped[str] = mapped_column(Text, default="")  # 补入依据
    operator_ref: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntakeRecord(Base):
    """LDM-003 材料接入记录（状态承载 intake_conclusion）。"""

    __tablename__ = "ldm003_intake_record"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True)
    intake_conclusion: Mapped[str] = mapped_column(String(32))  # IntakeConclusion 稳定码
    material_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParseRequest(Base):
    """识别请求上下文（过程记录仓储；记录前瞬态，承载 material_ref 供后续形成 LDM-004/005）。

    镜像 IntakeRequest：承载幂等键与失败停靠，LDM-004 未创建期间承载『解析中』阶段。
    """

    __tablename__ = "process_parse_request"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    material_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    stop_next_action: Mapped[str | None] = mapped_column(Text, default=None)
    workspace_version: Mapped[int] = mapped_column(Integer, default=1)  # 工作区快照版本（要素集变更时递增）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaterialParseResult(Base):
    """LDM-004 材料解析结果（本次识别整体状态；仅承接成功时形成）。"""

    __tablename__ = "ldm004_material_parse_result"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    material_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True)
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    parse_status: Mapped[str] = mapped_column(String(32))  # MaterialParseStatus 稳定码
    parse_note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementElement(Base):
    """LDM-005 需求要素（SCN-001-P02 全集登记；process_status=人工确认生命周期）。"""

    __tablename__ = "ldm005_requirement_element"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    parse_result_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    element_type: Mapped[str] = mapped_column(String(48))  # ElementType 稳定码
    content: Mapped[str] = mapped_column(Text)
    source_anchor: Mapped[str | None] = mapped_column(Text, default=None)  # 指向 LDM-002 原文范围
    confidence: Mapped[float | None] = mapped_column(Float, default=None)  # 证据字段
    process_status: Mapped[str] = mapped_column(String(32))  # ElementProcessStatus（确认生命周期）
    model_verdict: Mapped[str | None] = mapped_column(String(48), default=None)  # ModelVerdict 证据字段
    # 模型给该条裁定的具体理由（证据字段，随 model_verdict 一同落库后不可改写）；
    # 旧数据与模型漏给时为 None，读侧回落到该裁定的通用判据
    verdict_reason: Mapped[str | None] = mapped_column(Text, default=None)
    # 人工对「建议剔除候选」的处置标记（NoiseTriage）；与 model_verdict 并存，不改写模型证据
    noise_triage: Mapped[str | None] = mapped_column(String(32), default=None)
    version: Mapped[int] = mapped_column(Integer, default=1)  # 修订/重开时 +1（旧版本入历史）
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)  # 被拆分/合并替代（版本关系层）
    review_conclusion: Mapped[str | None] = mapped_column(String(32), default=None)  # ReviewConclusion
    review_basis: Mapped[str | None] = mapped_column(Text, default=None)  # 最近一次复核意见/失败原因
    revision_draft: Mapped[str | None] = mapped_column(Text, default=None)  # 当前修订稿（未采纳前不生效）
    correction_state: Mapped[str | None] = mapped_column(String(32), default=None)  # manual/review_adopted/ai_execution
    correction_note: Mapped[str | None] = mapped_column(Text, default=None)  # 校正/新增/补入依据
    origin_refs: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：前身要素 id（拆分/合并留痕）
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ElementHistory(Base):
    """LDM-005 变更历史（谁/何时/改了什么；旧版本快照留档，US-E4-01）。"""

    __tablename__ = "ldm005_element_history"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    version: Mapped[int] = mapped_column(Integer)  # 变更后的版本号
    action: Mapped[str] = mapped_column(String(48))  # register/confirm/reject/review/adjudicate/…
    from_status: Mapped[str | None] = mapped_column(String(32), default=None)
    to_status: Mapped[str | None] = mapped_column(String(32), default=None)
    operator_ref: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    snapshot: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：变更前 内容/类型/锚点/修订稿
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ElementOperation(Base):
    """P03/P04 操作请求上下文（过程记录；承载复核/AI执行命令参数，供 worker 读取）。"""

    __tablename__ = "process_element_operation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    parse_context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # review / execution
    payload: Mapped[str] = mapped_column(Text)  # JSON：targets/ranges/intent/instruction/workspace_version
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ElementChangeDraft(Base):
    """P04 变更草案（过程记录；确认创建前不是正式 LDM-005，不得被后续消费）。"""

    __tablename__ = "process_element_change_draft"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    parse_context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    workspace_version: Mapped[int] = mapped_column(Integer)  # 草案基于的版本
    operation_type: Mapped[str] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(24))  # manual / review_adopted / ai_execution
    items: Mapped[str] = mapped_column(Text)  # JSON：[{action:create|close, origin_refs, element{...}}]
    target_refs: Mapped[str | None] = mapped_column(Text, default=None)  # JSON
    suggestion_refs: Mapped[str | None] = mapped_column(Text, default=None)  # JSON（采纳复核建议时）
    source_ranges: Mapped[str | None] = mapped_column(Text, default=None)  # JSON
    impact_summary: Mapped[str | None] = mapped_column(Text, default=None)  # JSON
    create_gate: Mapped[str] = mapped_column(String(40), default="creatable")
    next_action: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/confirmed/cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ElementFacetProjection(Base):
    """要素完备度投影（过程记录；非事实源，可整层重算，不作下游门禁。设计增补 §3）。

    仅由 AEP-024 从已登记 LDM-015 写入；element_version 为版本锚，
    与 LDM-005.version 不一致即过期（工作区显示待重诊）。
    """

    __tablename__ = "process_element_facet_projection"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    element_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    element_version: Mapped[int] = mapped_column(Integer)  # 诊断针对的要素版本（版本锚）
    rubric_version: Mapped[int] = mapped_column(Integer)  # 判定所依据的判据版本
    facet_key: Mapped[str] = mapped_column(String(48))
    facet_status: Mapped[str] = mapped_column(String(16))  # present / missing / ambiguous
    evidence: Mapped[str | None] = mapped_column(Text, default=None)  # 原文引用片段
    note: Mapped[str | None] = mapped_column(Text, default=None)
    correctness: Mapped[str | None] = mapped_column(String(32), default=None)  # 每行冗余，读取聚合
    completeness: Mapped[str | None] = mapped_column(String(16), default=None)
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 来源 LDM-015
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementItem(Base):
    """LDM-007 需求条目（SCN-002-P01：仅 AEP-038 创建待确认；确认态演进归 SCN-003）。"""

    __tablename__ = "ldm007_requirement_item"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    parse_result_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 限定来源要素集合（LDM-004）
    formation_context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 形成批次上下文
    req_no: Mapped[str] = mapped_column(String(32))  # 待确认阶段为临时编号
    expression: Mapped[str] = mapped_column(Text)
    req_type: Mapped[str] = mapped_column(String(24))  # RequirementItemType 稳定码
    status: Mapped[str] = mapped_column(String(32))  # RequirementItemStatus 稳定码
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    source_element_refs: Mapped[str] = mapped_column(Text)  # JSON：来源 LDM-005 id 列表
    formation_basis_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 条目格式化类 LDM-015
    # 20 基线 §5.7 撰写字段（类型无关；条目档案增补 §4）：形成时模型初稿、AEP-036 可修订
    curation_note: Mapped[str | None] = mapped_column(Text, default=None)  # 内容整理说明
    boundary_note: Mapped[str | None] = mapped_column(Text, default=None)  # 条目边界说明
    # 29148 §5.2.8 属性补齐（LDM-007 属性补齐提案，2026-07-06 拍板；类型无关，缺失仅警示）
    verification_method: Mapped[str | None] = mapped_column(String(64), default=None)  # 多选，逗号连接 VerificationMethod 码
    verification_note: Mapped[str | None] = mapped_column(Text, default=None)  # 验收准则（模型初稿只准归纳来源）
    priority: Mapped[str | None] = mapped_column(String(8), default=None)  # ItemPriority 码（仅人工设定）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ItemStructureProjection(Base):
    """条目陈述达标投影（过程记录；非事实源，可整层重算，不作下游门禁。条目档案增补 §3）。

    仅由条目形成/修订链路从已登记 LDM-015 写入；item_content_rev 为版本锚
    （= 条目内容修订序号，形成时 1；LDM-007.version_no 归 req_no 替代族谱，不用作此锚），
    与当前内容修订序号不一致即过期（工作区显示达标待重诊）。
    """

    __tablename__ = "process_item_structure_projection"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    item_content_rev: Mapped[int] = mapped_column(Integer)  # 判定针对的内容修订序号（版本锚）
    profile_version: Mapped[int] = mapped_column(Integer)  # 判定所依据的档案版本
    # 判定所依据的规约方案（口径锚；存量回填 ears-cn）；徽章按本列渲染，方案切换不判过期。
    convention_key: Mapped[str] = mapped_column(String(32), default="ears-cn", server_default="ears-cn")
    row_kind: Mapped[str] = mapped_column(String(8))  # facet / field
    key: Mapped[str] = mapped_column(String(48))  # facet key 或 payload 字段 key
    facet_status: Mapped[str | None] = mapped_column(String(16), default=None)  # 仅 facet 行
    value_text: Mapped[str | None] = mapped_column(Text, default=None)  # 仅 field 行（结构化取值）
    evidence: Mapped[str | None] = mapped_column(Text, default=None)  # 来源要素/原文引用片段
    note: Mapped[str | None] = mapped_column(Text, default=None)
    statement_conformance: Mapped[str | None] = mapped_column(String(16), default=None)  # 每行冗余
    completeness: Mapped[str | None] = mapped_column(String(16), default=None)  # 每行冗余
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 来源 LDM-015
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementItemRevision(Base):
    """LDM-007 待确认字段修订记录（AEP-036；改前/改后/操作者留痕 + 幂等）。"""

    __tablename__ = "ldm007_item_revision"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    field_key: Mapped[str] = mapped_column(String(32))  # expression / req_type
    before_value: Mapped[str] = mapped_column(Text)
    after_value: Mapped[str] = mapped_column(Text)
    revision_mode: Mapped[str] = mapped_column(String(40))  # ItemRevisionMode 稳定码
    suggestion_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    selected_point_refs: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：所选修订点出处（v5）
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemFormationRequest(Base):
    """条目化批次上下文（过程记录仓储；运行过程边界，不形成新的需求事实源）。"""

    __tablename__ = "process_item_formation_request"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    parse_context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 定位要素工作区
    parse_result_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    scope_type: Mapped[str] = mapped_column(String(32))  # ItemizationScopeType 稳定码
    target_refs: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：勾选要素 id
    # 批次发起时读取一次并固定的生效规约方案（批次内一致，避免执行中途切换混排；存量回填 ears-cn）。
    convention_key: Mapped[str] = mapped_column(String(32), default="ears-cn", server_default="ears-cn")
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    stop_next_action: Mapped[str | None] = mapped_column(Text, default=None)  # 批次级失败停靠
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemizationOutcome(Base):
    """条目化批次逐要素归因结果（过程记录；created/blocked/failed/skipped + 原因 + next_action）。"""

    __tablename__ = "process_itemization_outcome"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    formation_context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    element_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    result_status: Mapped[str] = mapped_column(String(16))  # ItemizationResultStatus 稳定码
    item_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    formation_basis_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    next_action: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemRevisionSuggestion(Base):
    """字段修订候选建议投影（来源=条目格式化类 LDM-015；只记录处置状态，不作事实源）。"""

    __tablename__ = "process_item_revision_suggestion"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    field_key: Mapped[str] = mapped_column(String(32))  # expression / req_type
    proposed_value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate/accepted/rejected/expired
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 来源 LDM-015
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemDiagnosisRequest(Base):
    """诊断批次过程记录（SCN-003-P01-N05；运行过程边界，不是业务事实源，不替代 LDM-009）。"""

    __tablename__ = "process_item_diagnosis_request"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    parse_context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 工作区版本锚点
    parse_result_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    review_context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 评审工作区键（=条目形成批次上下文）
    item_refs: Mapped[str] = mapped_column(Text)  # JSON：本批次条目 id（批次只是执行组织方式）
    diagnosis_mode: Mapped[str] = mapped_column(String(24))  # DiagnosisMode 稳定码
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    stop_next_action: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemDiagnosisRound(Base):
    """LDM-009 需求条目评审记录·诊断轮次（逐条目；承载诊断处理状态、结论、复核收束与确认依据）。"""

    __tablename__ = "ldm009_diagnosis_round"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    item_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    batch_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=1)  # 条目内轮次序号（新轮次+1，最近轮次判定用）
    diagnosis_mode: Mapped[str] = mapped_column(String(24))  # 请求诊断参数（DiagnosisMode）
    trigger: Mapped[str] = mapped_column(String(24), default="user_submit")  # DiagnosisTrigger 稳定码
    processing_status: Mapped[str] = mapped_column(String(24))  # DiagnosisProcessingStatus 稳定码
    context_coverage: Mapped[str] = mapped_column(Text, default="")  # 诊断覆盖说明（模式决定范围）
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 诊断类 LDM-015
    reason: Mapped[str | None] = mapped_column(Text, default=None)  # 未能诊断/失败原因（不伪造结论）
    invalidated: Mapped[bool] = mapped_column(Boolean, default=False)  # 条目修订后旧轮次失效（N07）
    invalidated_reason: Mapped[str | None] = mapped_column(Text, default=None)
    # ---- v5 结论字段组（结论=判断，仅轮次铸造；聚合一致性由服务端守卫）----
    verdict_kind: Mapped[str | None] = mapped_column(String(16), default=None)  # VerdictKind 稳定码
    verdict_summary: Mapped[str | None] = mapped_column(Text, default=None)
    revision_points: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：修订点列表（仅 revise）
    supplement_gaps: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：来源缺口清单（仅 supplement）
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 被改判替代时指向新轮次
    excluded_point_refs: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：采纳时被排除的点（重评上下文）
    # ---- v2 需求质量诊断器旁路元数据（可空 JSON；降级不拒收，整列可丢弃不影响结论）----
    # {quality_profile, ears_rewrite, source_alignments, findings:[{finding_type,rule_code,evidence_span,severity,dimension}]}
    quality_meta: Mapped[str | None] = mapped_column(Text, default=None)
    # ---- v5 结论裁决字段组（AEP-034；裁决对象=结论）----
    adjudication_decision: Mapped[str | None] = mapped_column(String(16), default=None)  # VerdictDecision
    adjudication_selected_points: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：所选修订点
    adjudication_reason: Mapped[str | None] = mapped_column(Text, default=None)  # 拒绝理由（回复正文）
    # 采纳修订时用户对所选点替换文本的改稿（JSON：{point_ref: 用户终稿}；可空=未改稿，行为同改前）。
    # AI 原案不落这里——它在上面 revision_points 里原样不动，两者并存才能在留痕里对照展示。
    adjudication_point_edits: Mapped[str | None] = mapped_column(Text, default=None)
    adjudication_operator: Mapped[str | None] = mapped_column(String(64), default=None)
    adjudicated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    adjudication_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)  # 覆盖确认时标记（效能统计覆盖率）
    confirm_result: Mapped[str | None] = mapped_column(String(24), default=None)  # confirmed（确认写入）
    confirm_basis: Mapped[str | None] = mapped_column(Text, default=None)  # 确认依据（复核收束摘要）
    confirmed_by: Mapped[str | None] = mapped_column(String(64), default=None)
    confirm_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ItemReviewFinding(Base):
    """LDM-009 诊断发现项（P02 最小复核对象：结论+依据+建议处置+可选建议修订内容+逐项复核判断）。"""

    __tablename__ = "ldm009_review_finding"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    round_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    item_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    finding_type: Mapped[str] = mapped_column(String(40))  # ReviewFindingType 稳定码
    diagnosis_summary: Mapped[str] = mapped_column(Text)
    basis_summary: Mapped[str] = mapped_column(Text, default="")
    suggested_disposition: Mapped[str] = mapped_column(String(32))  # SuggestedDisposition 稳定码
    suggested_field: Mapped[str | None] = mapped_column(String(32), default=None)  # expression / req_type
    suggested_value: Mapped[str | None] = mapped_column(Text, default=None)  # 建议修订内容（绑定本发现项）
    suggested_reason: Mapped[str | None] = mapped_column(Text, default=None)
    suggestion_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # AEP-036 候选建议投影
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 来源 LDM-015
    decision: Mapped[str | None] = mapped_column(String(32), default=None)  # FindingReviewDecision
    decision_reason: Mapped[str | None] = mapped_column(Text, default=None)  # 拒绝理由/需完善说明
    decision_operator: Mapped[str | None] = mapped_column(String(64), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ItemFindingVeto(Base):
    """LDM-009 诊断问题否决留痕：用户裁定「这条不是问题」，此后不再重提、不再阻塞确认。

    为什么另立一张表，而不是往上面 ItemReviewFinding 的 decision 列里写（2026-07-20 方案门拍板）：
    - **粒度不同**。发现项每诊断一轮都是新写的行；用户否决的却是那个「问题」本身，一次否决要
      对未来所有轮次生效。所以这里存的是问题指纹（规则码 + 证据片段），不是某一行的引用。
    - **撤销要留痕**。用户否决过又撤销，这个事实本身有价值；写在发现项行上只能把 decision 置空，
      事实随之消失。这里改为写 revoked_at，行永不删。
    - **语义不混**。ItemReviewFinding.decision 是 v5 前冻结的复核判断三值（accepted/rejected/
      needs_improvement），与「是不是问题」不是同一个判断轴；两套语义同列并存，存量数据将无法区分。

    匹配是纯字符串比较（见 services/item_review.py 的 veto_key/veto_matches），可离线复算——
    否决拦截绝不引入模型判定。
    """

    __tablename__ = "ldm009_finding_veto"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    item_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 否决作用域=该条目
    # ---- 问题指纹（跨轮匹配键；规则码缺失时退化为 finding_type + evidence_span）----
    rule_code: Mapped[str | None] = mapped_column(String(64), default=None)
    evidence_span: Mapped[str | None] = mapped_column(Text, default=None)
    finding_type: Mapped[str] = mapped_column(String(40))  # ReviewFindingType 稳定码
    # ---- 留痕（展示用；不参与匹配——模型自由撰写的摘要当匹配键会随措辞漂移误判）----
    finding_summary: Mapped[str] = mapped_column(Text, default="")
    origin_finding_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 首次否决时的那一行
    reason: Mapped[str | None] = mapped_column(Text, default=None)  # 用户理由（可选）
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # ---- 撤销（置时间而非删行；撤销后该指纹恢复计入阻断）----
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_by: Mapped[str | None] = mapped_column(String(64), default=None)


class TemplateRegistry(Base):
    """模板注册表（配置域：登记快照，不可变）。

    UINV-20 边界：只登记、停用，不编辑内容——改内容 = 登记新版本行；
    行永不改写（status 除外），发布基线引用因此永远可解析。
    内置模板启动时按 content_hash 幂等同步（source=builtin）。
    """

    __tablename__ = "template_registry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    template_key: Mapped[str] = mapped_column(String(128), index=True)  # 业务标识（跨版本稳定）
    version_no: Mapped[int] = mapped_column(Integer, default=1)  # (template_key, version_no) 唯一
    name: Mapped[str] = mapped_column(String(200))
    schema_version: Mapped[str] = mapped_column(String(16))
    doc_type: Mapped[str] = mapped_column(String(32), default="srs")
    content_type: Mapped[str] = mapped_column(String(64), default="application/json")  # 扩展点：未来 docx 二进制模板
    content: Mapped[str] = mapped_column(Text)  # 模板文件内容快照（登记后不可变）
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256
    source: Mapped[str] = mapped_column(String(16), default="registered")  # builtin / registered
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / disabled
    registered_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TemplateDraft(Base):
    """模板定制草稿（配置域：定制器工作态，可变可删）。

    与 template_registry 的不可变快照分离：草稿只是编辑器状态的暂存，
    未经送检、不占版本号、不可被发布消费；登记成功后由调用方删除。
    payload 为定制器状态 JSON（info/binding/tree 信封），后端不解析。
    """

    __tablename__ = "template_draft"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), default="")  # 展示名（取定制器模板名称，可为空）
    payload: Mapped[str] = mapped_column(Text)  # 定制器状态 JSON（不透明信封，含 designer_state_version）
    origin: Mapped[str] = mapped_column(String(16), default="blank")  # blank / copy / edit
    source_registry_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 源登记行（copy/edit 起点）
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RequirementDocument(Base):
    """LDM-014 需求文档（SCN-005；索引/Markdown/导出件/基线为其内四个状态对象）。"""

    __tablename__ = "ldm014_requirement_document"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    doc_type: Mapped[str] = mapped_column(String(32), default="srs")
    title: Mapped[str] = mapped_column(String(200), default="需求规格说明")
    template_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 指向 template_registry.id（冻结登记行）
    coverage_scope: Mapped[str | None] = mapped_column(Text, default=None)  # 发布范围说明
    status: Mapped[str] = mapped_column(String(32))  # DocumentStatus 稳定码
    blocked_reason: Mapped[str | None] = mapped_column(Text, default=None)  # 受阻原因（模板/必填/准入）
    missing_list: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：缺失清单+补建入口
    index_version: Mapped[int] = mapped_column(Integer, default=0)  # 索引每次保存 +1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentIndexEntry(Base):
    """LDM-014.文档内容索引条目（只存资产引用+章节位置+顺序，不复制正文事实）。"""

    __tablename__ = "ldm014_doc_index_entry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    index_version: Mapped[int] = mapped_column(Integer, index=True)  # 所属索引版本（历史保留）
    section_key: Mapped[str] = mapped_column(String(64))  # 模板章节槽位 key
    asset_type: Mapped[str] = mapped_column(String(32))  # SlotAssetType 稳定码
    asset_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # boilerplate 无引用
    asset_version: Mapped[str] = mapped_column(String(16), default="1")  # 纳入时资产版本快照
    order_no: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SectionManuscript(Base):
    """LDM-014.章节撰稿（AEP-098）：文档正文的第一类人工内容。

    只承载可撰稿章节（模板 content_types 含 boilerplate/authored_text）的人工文字；
    生成 Markdown 时覆盖模板默认文本（boilerplate 降级为默认预填稿）。
    (document_ref, section_key) 唯一；删除行 = 回落模板默认文本。
    不改任何治理资产事实（条目/图表/材料门禁不经此表）。
    """

    __tablename__ = "ldm014_section_manuscript"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    section_key: Mapped[str] = mapped_column(String(64))  # 模板章节 key
    content: Mapped[str] = mapped_column(Text)  # 撰稿全文（可含 {project_name}/{coverage_scope} 占位符）
    revision_no: Mapped[int] = mapped_column(Integer, default=1)  # 每次保存 +1
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarkdownDraft(Base):
    """LDM-014.Markdown 中间稿/定稿版本（派生发布制品；源资产绑定用于编辑影响识别）。"""

    __tablename__ = "ldm014_markdown_draft"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    index_version: Mapped[int] = mapped_column(Integer)  # 生成依据的索引版本
    content: Mapped[str] = mapped_column(Text)  # 当前预览内容（含未定稿补丁）
    generated_content: Mapped[str] = mapped_column(Text)  # 生成时原始内容（补丁 diff 基准）
    source_bindings: Mapped[str] = mapped_column(Text, default="[]")  # JSON：行区间→资产绑定
    status: Mapped[str] = mapped_column(String(32), default="draft")  # MarkdownDraftStatus
    can_export: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reasons: Mapped[str | None] = mapped_column(Text, default=None)  # JSON：不可定稿项
    finalized_by: Mapped[str | None] = mapped_column(String(64), default=None)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarkdownPatch(Base):
    """LDM-014.预览编辑补丁（未定稿前不是正式资产，不得被条目/图表/追溯消费）。"""

    __tablename__ = "ldm014_markdown_patch"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    draft_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    impact: Mapped[str] = mapped_column(String(32))  # EditImpact 稳定码
    before_text: Mapped[str] = mapped_column(Text, default="")
    after_text: Mapped[str] = mapped_column(Text, default="")
    bound_item_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 触及的确认态 LDM-007
    reflow_item_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 回流生成的新待确认条目
    status: Mapped[str] = mapped_column(String(16), default="pending")  # PatchStatus
    note: Mapped[str | None] = mapped_column(Text, default=None)
    operator_ref: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocxExport(Base):
    """LDM-014.候选 docx 导出件（候选≠发布；人工降级须明确标记）。"""

    __tablename__ = "ldm014_docx_export"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    draft_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 来源 Markdown 定稿版本
    status: Mapped[str] = mapped_column(String(32))  # DocxExportStatus 稳定码
    file_path: Mapped[str | None] = mapped_column(Text, default=None)
    failure_reason: Mapped[str | None] = mapped_column(Text, default=None)
    manual_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    check_note: Mapped[str | None] = mapped_column(Text, default=None)  # 检查结论说明
    operator_ref: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReleaseBaseline(Base):
    """LDM-014.发布基线快照（冻结本次交付事实；形成后只读）。"""

    __tablename__ = "ldm014_release_baseline"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    index_version: Mapped[int] = mapped_column(Integer)
    draft_ref: Mapped[uuid.UUID] = mapped_column(Uuid)  # Markdown 定稿版本
    template_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 指向 template_registry.id（冻结登记行）
    export_ref: Mapped[uuid.UUID] = mapped_column(Uuid)  # 候选/人工降级导出件
    manual_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON：源资产版本引用清单
    confirmed_by: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementChart(Base):
    """LDM-012 需求图表（SCN-004；status=草稿中/待确认/已确认/退回修订/作废）。"""

    __tablename__ = "ldm012_requirement_chart"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    title: Mapped[str] = mapped_column(String(200))
    chart_kind: Mapped[str] = mapped_column(String(24))  # ChartKind 稳定码（由 chart_type 派生）
    chart_type: Mapped[str] = mapped_column(String(32))  # ChartType 稳定码
    format: Mapped[str] = mapped_column(String(24))  # ChartFormat 稳定码
    source_code: Mapped[str] = mapped_column(Text, default="")  # 受控源码（仅校验通过后写入）
    draft_version: Mapped[int] = mapped_column(Integer, default=1)  # 源码变更应用 +1（乐观锁+轮次锚）
    status: Mapped[str] = mapped_column(String(32))  # ChartStatus 稳定码
    status_reason: Mapped[str | None] = mapped_column(Text, default=None)  # 退回修订/作废原因
    source_kind: Mapped[str] = mapped_column(String(32), default="requirement_item")  # ChartSourceKind
    source_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON：来源确认态 LDM-007 id 列表
    creation_basis: Mapped[str] = mapped_column(Text, default="")  # 创建准入结论摘要
    verification_conclusion: Mapped[str | None] = mapped_column(Text, default=None)  # 核对结论（确认时写入）
    confirm_basis: Mapped[str | None] = mapped_column(Text, default=None)  # 确认依据（复核收束+LDM-015 ref）
    confirmed_by: Mapped[str | None] = mapped_column(String(64), default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChartSourceRevision(Base):
    """LDM-012 图表源码修订留痕（每次变更应用后的版本快照与编辑依据）。"""

    __tablename__ = "ldm012_chart_revision"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chart_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    draft_version: Mapped[int] = mapped_column(Integer)  # 变更后的版本号
    source_code: Mapped[str] = mapped_column(Text)  # 该版本源码快照
    format: Mapped[str] = mapped_column(String(24))
    change_origin: Mapped[str] = mapped_column(String(24))  # ChartSourceChangeOrigin 稳定码
    suggestion_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 采纳的源码类 LDM-015
    note: Mapped[str | None] = mapped_column(Text, default=None)
    operator_ref: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TraceLink(Base):
    """LDM-013 追溯关系（SCN-004 图表来源；预建立不得作为正式追溯依据消费）。"""

    __tablename__ = "ldm013_trace_link"
    __table_args__ = (
        UniqueConstraint(
            "upstream_type", "upstream_ref", "downstream_type", "downstream_ref", "relation_type",
            name="uq_ldm013_edge",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    dimension: Mapped[str] = mapped_column(String(16), default="spatial")  # 空间/时间（ADR-0001）
    relation_type: Mapped[str] = mapped_column(String(32))  # TraceRelationType 稳定码
    upstream_type: Mapped[str] = mapped_column(String(32))  # requirement_item / …
    upstream_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    downstream_type: Mapped[str] = mapped_column(String(32))  # chart / …
    downstream_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(32))  # TraceLinkStatus 稳定码
    initial_basis: Mapped[str] = mapped_column(Text, default="")  # 预建立初始依据
    status_reason: Mapped[str | None] = mapped_column(Text, default=None)  # 待补全/失效原因
    established_basis: Mapped[str | None] = mapped_column(Text, default=None)  # 正式确立依据
    established_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    issue_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 关联 LDM-011
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Issue(Base):
    """LDM-011 问题项（本迭代最小实现：图表核对转入；处置闭环归 SCN-006）。"""

    __tablename__ = "ldm011_issue"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    issue_type: Mapped[str] = mapped_column(String(32))  # IssueType 稳定码
    status: Mapped[str] = mapped_column(String(24), default="pending")  # IssueStatus 稳定码
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    origin_kind: Mapped[str] = mapped_column(String(32), default="chart_verification")
    chart_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, default=None)
    finding_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 来源核对发现项
    trace_link_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON：关联 LDM-013 id
    created_by: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChartSuggestionRequest(Base):
    """AI 图表源码建议请求上下文（过程记录；SCN-004-P01-N08 送检参数与失败停靠）。"""

    __tablename__ = "process_chart_suggestion_request"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    chart_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    base_draft_version: Mapped[int] = mapped_column(Integer)  # 建议基于的草稿版本
    intent: Mapped[str] = mapped_column(Text, default="")  # AI 生成/修订意图
    request_kind: Mapped[str] = mapped_column(
        String(16), default="revision", server_default="revision",
    )  # initial=创建初稿（结果自动应用）/ revision=修订建议（待人工采纳）
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    stop_next_action: Mapped[str | None] = mapped_column(Text, default=None)  # AI 失败停靠
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChartVerificationRequest(Base):
    """图文核对请求上下文（过程记录；SCN-004-P02-N01 核对发起）。"""

    __tablename__ = "process_chart_verification_request"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    chart_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    chart_draft_version: Mapped[int] = mapped_column(Integer)  # 核对针对的草稿版本（轮次锚）
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    stop_next_action: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChartVerificationRound(Base):
    """图文核对轮次（过程记录；承载核对处理状态、发现项归属与确认幂等）。"""

    __tablename__ = "process_chart_verification_round"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chart_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    request_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=1)  # 图表内轮次序号
    chart_draft_version: Mapped[int] = mapped_column(Integer)  # 版本锚（与图表当前版本不符则失效）
    processing_status: Mapped[str] = mapped_column(String(24))  # ChartVerificationProcessingStatus
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 图文核对类 LDM-015
    reason: Mapped[str | None] = mapped_column(Text, default=None)  # 核对失败原因（不伪造结论）
    invalidated: Mapped[bool] = mapped_column(Boolean, default=False)  # 退回修订后旧轮次显式失效
    invalidated_reason: Mapped[str | None] = mapped_column(Text, default=None)
    confirm_result: Mapped[str | None] = mapped_column(String(24), default=None)  # confirmed（N08 写入）
    confirm_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChartVerificationFinding(Base):
    """图文核对发现项（复核对象：类型+摘要+依据+逐项复核判断；不是正式问题项）。"""

    __tablename__ = "process_chart_verification_finding"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    round_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    chart_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    finding_type: Mapped[str] = mapped_column(String(40))  # ChartFindingType 稳定码
    summary: Mapped[str] = mapped_column(Text)
    basis_summary: Mapped[str] = mapped_column(Text, default="")
    related_source_refs: Mapped[str] = mapped_column(Text, default="[]")  # JSON：涉及来源条目 id
    model_result_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 来源 LDM-015
    decision: Mapped[str | None] = mapped_column(String(16), default=None)  # ChartFindingDecision
    decision_reason: Mapped[str | None] = mapped_column(Text, default=None)  # 拒绝理由（必填）
    decision_operator: Mapped[str | None] = mapped_column(String(64), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    issue_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 转问题项后回填
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdoptionRecord(Base):
    """LDM-015 采纳结论明细（AI效能统计口径设计 §7）：各环节人工裁定逐对象留痕。

    批级 LDM-015 记录装不下批内多结局；明细是 06A §3.3"保存采纳结论和去向"的落地，
    由各环节业务服务在裁定命令中回写，统计（AEP-094）只读明细聚合。
    """

    __tablename__ = "ldm015_adoption_record"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_result_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 读模型冗余（口径 D4）
    stage: Mapped[str] = mapped_column(String(32), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))  # element/requirement_item/finding/chart_draft/material_intake
    subject_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    outcome: Mapped[str] = mapped_column(String(32))  # adopted/adopted_with_revision/rejected/transferred_to_issue/superseded
    basis_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 业务留痕引用
    operator_ref: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class clock_timestamp(GenericFunction):  # noqa: N801 SQL 函数名即类名（SQLAlchemy 约定）
    """语句执行时刻（区别于 now()＝事务开始时刻）。

    PostgreSQL 下 `now()` 返回**事务开始**时刻，整段事务内恒定。worker 的终态迁移写在
    横跨 LLM 调用的长事务里，用 now() 回填 updated_at 会把完成时刻记成事务开始时刻——
    实测出现过状态已 succeeded 而 updated_at 停在 started 时刻的行，令耗时/等待时长口径失真。
    PostgreSQL 原生提供 clock_timestamp()；SQLite 无此函数，其 CURRENT_TIMESTAMP 本就按
    语句求值，语义等价，故按方言编译过去（口径一致，无需迁移）。
    """

    type = DateTime(timezone=True)
    inherit_cache = True


@compiles(clock_timestamp, "sqlite")
def _sqlite_clock_timestamp(element, compiler, **kw) -> str:  # noqa: ANN001
    return "CURRENT_TIMESTAMP"


class AgentRun(Base):
    """异步任务（AgentRun）：状态/进度/审计摘要（ADR-007）。"""

    __tablename__ = "agent_run"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(48))  # source_intake / ...
    status: Mapped[str] = mapped_column(String(16), default="queued")  # AgentRunStatus 稳定码
    context_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # onupdate 取语句时刻而非事务开始时刻：长事务内的终态迁移也要记下真实的完成时刻。
    # 只是 UPDATE 语句里的表达式，不是 DDL 默认值，故改动无需 alembic 迁移。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=clock_timestamp()
    )


class AgentRunEvent(Base):
    """AgentRun 进度事件（持久化，供 SSE 不可用时轮询，25-05）。"""

    __tablename__ = "agent_run_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    event: Mapped[str] = mapped_column(String(48))  # agent_run.started/progress/completed/failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    """通知（04A §2.1 通知徽标）：需要人处理或确认的未读事项，按 dedup_key 去重。

    运行日志/结构化事件不逐条转通知，只有需人工介入的事件落此表；
    title/summary 只放稳定码与提示文案，绝不放 error 原文/prompt/模型响应。
    复发（同 dedup_key 再次 notify）→ occurrences+1 且清 read_at（需要再次处理）。
    """

    __tablename__ = "notification"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(48))  # agent_run.failed / export.failed / ...
    project_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, default=None)
    ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 关联对象（run/export/...）
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConfigEntry(Base):
    """配置管理入口（04 §3.5 / CONN-006）：支撑能力配置，按域一行。

    只写配置、不写治理事实：不形成确认结论、追溯关系或发布基线。
    payload 为非敏字段 JSON；secrets 只写不回显（读侧仅返回“已设置”+脱敏占位）。
    外观偏好是浏览器本地偏好（04A §9.1），永不落此表。
    """

    __tablename__ = "config_entry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # model_service / export / chart_rendering
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON：非敏配置字段
    secrets: Mapped[str] = mapped_column(Text, default="{}")  # JSON：密钥类字段（只写不回显，不入日志）
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConfigAudit(Base):
    """配置保存审计留痕（CONN-006：保存经 `审计留痕` 记录）。

    只记 谁/何时/哪个域/动了哪些字段名；绝不记字段值（含密钥明文，硬规则 8）。
    """

    __tablename__ = "config_audit"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(32), default="save")  # save / test_connection
    operator_ref: Mapped[str] = mapped_column(String(64))
    changed_keys: Mapped[str] = mapped_column(Text, default="[]")  # JSON：仅字段名列表
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchIndex(Base):
    """派生检索索引（全局检索工作包 · 非逻辑数据实体，不占 LDM 编号）。

    从五类事实源（材料/知识项/需求条目/图表/文档）投影出的去规范化索引，**可整层重算、可整表重建**；
    五类源表的权威模型不动（README 不变式 2）。检索层只经 SearchSourceProvider.iter_nodes() 产出的
    IndexableNode 写入本表，不直接耦合源表结构（不变式 3）。

    身份约束 = (project_id, entity_type, ref)：ref 为稳定语义引用（(asset_type, ref) 寻址口径，
    非本表行 PK，不变式 4），换源/重建不漂移，天然映射未来图节点 id。

    embedding：Postgres 为 Vector(dim) 语义向量（无 embedding 端点时留 NULL → 检索走词法）；
    SQLite（测试）经 with_variant 降级为 Text 恒 NULL，检索走 Python 子串（不变式 7）。
    HNSW / GIN(pg_trgm) 索引为 Postgres 专有，在迁移里建（模型无法可移植表达），见迁移 xj7k8l9m0n1p。
    """

    __tablename__ = "search_index"
    __table_args__ = (
        UniqueConstraint("project_id", "entity_type", "ref", name="uq_search_index_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    entity_type: Mapped[str] = mapped_column(String(32))  # == IndexableNode.node_type == 未来图节点 label
    ref: Mapped[str] = mapped_column(String(128))  # 稳定语义引用（(asset_type, ref) 口径）
    title: Mapped[str] = mapped_column(Text, default="")  # 展示标题（短）
    body: Mapped[str] = mapped_column(Text, default="")  # 可搜正文（全文拼接，绕开 _head 截断）
    # 语义向量：Postgres=Vector(dim)，SQLite=Text（恒 NULL）。无 embedding 端点时全 NULL → 词法降级。
    embedding = mapped_column(
        Vector(settings.embedding_dim).with_variant(Text(), "sqlite"), nullable=True, default=None
    )
    content_hash: Mapped[str] = mapped_column(String(64), default="")  # sha256(node_type + body)，跳过未变行重嵌
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )  # 源更新时间，检索 tie-break


class DemoChatTranscript(Base):
    """演示留痕表（AI 对话演示简化方案 2026-07-18 · 非逻辑数据实体，不占 LDM 编号）。

    三个对话页（知识抽取/条目形成/条目评审）区5 消息的服务端留痕，供刷新后水合——现状是刷新即失。
    **append-only**：仓库代码对本表不得出现 UPDATE/DELETE，唯一例外＝seed_full_demo --reset 的演示项目
    重置删除范围。写入全在 API 层用独立短 session 即写即提交，服务层零改动（爆炸半径最小）。

    context_ref 按渠道取不同上下文键：analysis=parse_context_ref / formation=parse_result_ref /
    review=item_ref（三者在 DB 中均为 uuid）。content 为 JSON 串（Text 承载，与本仓其余 JSON 字段同范式）：
    `{text}` 或找来源的 `{text, candidates: [...]}`。created_at 为排序键，由写侧显式赋微秒精度 UTC 值
    （SQLite CURRENT_TIMESTAMP 仅秒级，独立事务同秒会撞键；读侧再以 (created_at, id) 消歧保序）。
    """

    __tablename__ = "demo_chat_transcript"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    channel: Mapped[str] = mapped_column(String(16))  # analysis | formation | review
    context_ref: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    kind: Mapped[str] = mapped_column(String(32))  # free_text | command | command_result | source_candidates | failure_note
    content: Mapped[str] = mapped_column(Text)  # JSON：{text} 或 {text, candidates:[...]}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ════════════════════════════════════════════════════════════════════
# V2 知识层（2026-08-08 起，逐步落库；设计正本＝docs/v2/design/数据模型.md，
# 实施记录＝docs/v2/drafts/知识层落库对齐稿-讨论稿.md）。
# 命名走 V2 英文名不带 ldm 编号（用户裁定）；主键用 UUID v7（时间有序，
# 对只追加的表索引友好——共享结构裁定决定点一）。
# ════════════════════════════════════════════════════════════════════

def uuid7() -> uuid.UUID:
    """生成 UUID v7（RFC 9562）：前 48 位为毫秒时间戳，其余为版本位与随机位。"""
    import secrets
    import time as _time

    ts = int(_time.time() * 1000) & ((1 << 48) - 1)
    value = (
        (ts << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return uuid.UUID(int=value)


class KnowledgeAsset(Base):
    """资产——一条知识的户口：终身不变的身份＋生命周期状态。内容不在本表（在快照表）。

    类别（kind）以本表为唯一来源：快照内容里的判别字段须与之一致（写入函数校验）。
    合并去向不设列：合并是事件，将来做合并功能时建合并事件表（2026-08-08 用户裁定）。
    """

    __tablename__ = "asset"
    __table_args__ = (
        CheckConstraint("kind IN ('需求知识', '领域概念')", name="ck_asset_kind"),
        CheckConstraint(
            "status IN ('待确认', '已确认', '已拒绝', '已废止', '已合并')",
            name="ck_asset_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="待确认")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeSnapshot(Base):
    """快照——某一次提交存下的那一版知识内容，写入后永不修改（改内容＝追加新快照）。

    不可变两道闸：ORM 层 before_update 事件拒绝任何修改（测试可验）；正式库另有
    迁移里建的数据库触发器拒绝 UPDATE（SQLite 测试库不含触发器）。
    序号唯一约束（资产内）兜底重复提交：同时挤进来的两笔只有一笔成功——修改本身
    是独占的（2026-08-08 用户裁定，同一时刻只允许一个人修订），此约束只是防重放的保险。
    """

    __tablename__ = "snapshot"
    __table_args__ = (
        UniqueConstraint("asset_id", "seq_no", name="uq_snapshot_asset_seq"),
        CheckConstraint("author_kind IN ('智能体', '治理者')", name="ck_snapshot_author"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.id"), index=True)
    seq_no: Mapped[int] = mapped_column(Integer)  # 资产内从 1 递增
    content: Mapped[dict] = mapped_column(KNOWLEDGE_CONTENT_JSON)  # 结构正本 api/schemas/knowledge.yaml
    content_sha256: Mapped[str] = mapped_column(String(64))  # 对规范化序列化字节算出的指纹
    content_hash_alg: Mapped[str] = mapped_column(String(32))  # 指纹的算法与规范化规则，如 sha256/jcs
    author_kind: Mapped[str] = mapped_column(String(16))  # 智能体（须带 task_ref）／治理者（须带 audit_ref）
    task_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 产生任务（任务台账表后续步骤建）
    audit_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)  # 人工修订留痕（留痕表后续步骤建）
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@event.listens_for(KnowledgeSnapshot, "before_update")
def _forbid_snapshot_update(_mapper, _connection, _target):  # SQLAlchemy 事件签名固定
    raise RejectedTransition("快照不可修改：改内容请追加新快照")
