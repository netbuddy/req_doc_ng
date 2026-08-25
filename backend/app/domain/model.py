"""V2 领域对象定义（骨架期核心流程）——《领域模型》的代码承载。

语义正本＝设计文档《领域模型》（DR-008：概念与字段命名的正本，中文语义名与出处
均在该文；本文件不新增业务语义，docstring 只作导航引用不复制字段表）。本文件是
领域层的手写定义：17 个概念＝13 实体（含 2 个关系）＋4 个值对象，实体标识自带
（id 字段），关系不带自有标识。一致性由 tests/test_domain_model.py 机器核对
（概念齐全、枚举列值与文档一致、内容值对象字段与 api/schemas/knowledge.yaml 一致）。

两条纪律（DR-008/《领域模型》§5）：①新业务事实先入领域模型再入本文件；
②存储形态（DDL/ORM）与传输形态（契约 DTO）都是本定义的投影，两者不合并、
不互相生成，改动次序＝先领域模型文档、再本文件与契约、再 DDL。

语义类型到 Python 的映射（《领域模型》§1 封闭八类）：
标识→UUID；文本/长文本→str；枚举→StrEnum（中文成员，与契约生成物同风格）；
时刻→datetime；计数→int；指纹→str；键值集合→Mapping[str, Any]。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID

from app.domain.errors import InvalidInput


# ---- 枚举（列值正本＝《领域模型》§3 各字段表的「列值：」声明）----


class SourceKind(StrEnum):
    """原始材料·来源形态（§3.4）。"""

    文件 = "文件"
    粘贴 = "粘贴"


class TaskKind(StrEnum):
    """认知任务·类别（§3.6；骨架期唯一列值，成文等随后续环加入）。"""

    提取 = "提取"


class TaskStatus(StrEnum):
    """认知任务·状态（§3.6；裁定甲-30 封闭五值）。"""

    已登记 = "已登记"
    进行中 = "进行中"
    待裁决 = "待裁决"
    已完成 = "已完成"
    失败 = "失败"


class AssetKind(StrEnum):
    """知识单元·类别（§3.7）；知识单元内容的类别判别（§3.14）与之同一列值集合。"""

    需求知识 = "需求知识"
    领域概念 = "领域概念"


class AssetStatus(StrEnum):
    """知识单元·状态（§3.7；迁移语义正本＝《知识单元状态迁移表》）。"""

    待确认 = "待确认"
    已确认 = "已确认"
    已拒绝 = "已拒绝"
    已废止 = "已废止"
    已合并 = "已合并"


class AuthorKind(StrEnum):
    """候选稿·作者身份（§3.8；人工修订产生「治理者」候选稿）。"""

    智能体 = "智能体"
    治理者 = "治理者"


class DecisionKind(StrEnum):
    """裁决·类别（§3.10；人独占确认出口）。"""

    确认 = "确认"
    退回 = "退回"
    拒绝 = "拒绝"


class RequirementCategory(StrEnum):
    """需求知识内容·类别（§3.14；骨架期三档粗分）。"""

    功能需求 = "功能需求"
    约束 = "约束"
    其他 = "其他"


# ---- 实体（§3.1—§3.13；「关系」建模身份的两个不带自有标识）----


@dataclass(frozen=True, kw_only=True)
class Project:
    """项目——一切资产与操作的归属边界（§3.1）。"""

    id: UUID
    name: str
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class UserAccount:
    """治理者（用户）——平台唯一的确认权持有者；骨架期用户表只承载留痕身份（§3.2）。"""

    id: UUID
    display_name: str


@dataclass(frozen=True, kw_only=True)
class Membership:
    """成员（关系）——用户与项目的从属；非成员访问一律拒绝（§3.3）。"""

    project_id: UUID
    user_id: UUID


@dataclass(frozen=True, kw_only=True)
class Material:
    """原始材料（不可变）——导入即不可改写，指纹是机器核验凭据（§3.4）。"""

    id: UUID
    project_id: UUID
    name: str
    source_kind: SourceKind
    content: str
    content_sha256: str
    imported_by: UUID
    imported_at: datetime


@dataclass(frozen=True, kw_only=True)
class ParseResult:
    """解析结果（不可变）——规范化全文＝来源锚点偏移的计算基准（§3.5）。"""

    id: UUID
    material_id: UUID
    parser_version: str
    text: str
    text_sha256: str
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class TaskFailureItem:
    """任务失败项（值对象）——部分输入未产出候选的一条原因说明（§3.17）。"""

    reason: str


@dataclass(frozen=True, kw_only=True)
class Task:
    """认知任务——一件工作在任务台账里的记录，真实回执的唯一事实源（§3.6）。"""

    id: UUID
    project_id: UUID
    kind: TaskKind
    status: TaskStatus
    initiated_by: UUID
    material_ids: tuple[UUID, ...]
    redo_of_decision: UUID | None = None
    retry_of_task: UUID | None = None
    failure_items: tuple[TaskFailureItem, ...] = ()
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.material_ids:
            raise InvalidInput("认知任务的输入材料至少一份（《领域模型》§3.6）")


@dataclass(frozen=True, kw_only=True)
class Asset:
    """知识单元——受治理知识的户口；骨架期「资产」总称的唯一实例（§3.7）。"""

    id: UUID
    project_id: UUID
    kind: AssetKind
    status: AssetStatus
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class AttributeItem:
    """属性项（值对象）——歧义已消解（属性值）与未消解（候选解释列表）两形态二选一（§3.15，R-007）。"""

    name: str
    value: str | None = None
    candidates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.value is None) == (not self.candidates):
            raise InvalidInput("属性项须在属性值与候选解释列表中二选一（R-007）")
        if self.candidates and len(self.candidates) < 2:
            raise InvalidInput("歧义未消解形态的候选解释至少两条（R-007）")


@dataclass(frozen=True, kw_only=True)
class RequirementContent:
    """知识单元内容·需求知识变体（值对象，§3.14）。

    结构正本＝api/schemas/knowledge.yaml 的 RequirementContent（两形态共用同一份
    定义）；本类是它的领域形态承载，字段一致性由测试核对。
    """

    kind: AssetKind = AssetKind.需求知识
    title: str
    description: str
    category: RequirementCategory
    attributes: tuple[AttributeItem, ...] = ()
    concept_refs: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is not AssetKind.需求知识:
            raise InvalidInput("需求知识变体的类别判别必须是「需求知识」（§3.14）")


@dataclass(frozen=True, kw_only=True)
class ConceptContent:
    """知识单元内容·领域概念变体（值对象，§3.14）。结构正本同 RequirementContent 注。"""

    kind: AssetKind = AssetKind.领域概念
    name: str
    definition: str
    aliases: tuple[str, ...] = ()
    attributes: tuple[AttributeItem, ...] = ()
    concept_refs: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is not AssetKind.领域概念:
            raise InvalidInput("领域概念变体的类别判别必须是「领域概念」（§3.14）")


KnowledgeContent = RequirementContent | ConceptContent
"""知识单元内容（§3.14）：判别字段 kind 区分两个变体。"""


@dataclass(frozen=True, kw_only=True)
class SourceAnchor:
    """来源锚点（值对象）——知识与材料原文之间可机器校验的连接（§3.16，R-006）。

    偏移为 Unicode 码点计数，由内核查找计算；引文是解析文本的精确子串。
    """

    material_id: UUID
    parse_result_id: UUID
    start_offset: int
    end_offset: int
    quote: str
    hit_count: int


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    """候选稿（不可变）——内容每次提交固化成的快照（§3.8，DR-005）。"""

    id: UUID
    asset_id: UUID
    seq_no: int
    content: KnowledgeContent
    content_sha256: str
    author_kind: AuthorKind
    task_id: UUID | None = None
    audit_id: UUID | None = None
    anchors: tuple[SourceAnchor, ...]
    submitted_at: datetime

    def __post_init__(self) -> None:
        if self.author_kind is AuthorKind.智能体 and self.task_id is None:
            raise InvalidInput("智能体候选稿必须携带产生任务（《领域模型》§3.8）")
        if self.author_kind is AuthorKind.治理者 and self.audit_id is None:
            raise InvalidInput("治理者候选稿必须携带修订留痕（《领域模型》§3.8）")
        if not self.anchors:
            raise InvalidInput("候选稿的来源锚定至少一条（R-006）")


@dataclass(frozen=True, kw_only=True)
class Version:
    """版本（不可变）——经确认裁决固化的候选稿；版本序列即资产的权威历史（§3.9，DR-005）。"""

    id: UUID
    asset_id: UUID
    version_no: int
    snapshot_id: UUID
    decision_id: UUID
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class Decision:
    """裁决（不可变）——治理者的三种处置：确认／退回（附意见）／拒绝（§3.10）。"""

    id: UUID
    asset_id: UUID
    snapshot_id: UUID
    kind: DecisionKind
    decided_by: UUID
    opinion: str | None = None
    gate_record: Mapping[str, Any] | None = None
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.kind is DecisionKind.退回 and not self.opinion:
            raise InvalidInput("退回裁决必须附退回意见（《领域模型》§3.10）")
        if self.kind is DecisionKind.确认 and self.gate_record is None:
            raise InvalidInput("确认裁决必须留存门禁判定记录（《领域模型》§3.10）")


@dataclass(frozen=True, kw_only=True)
class AuditLog:
    """操作留痕（不可变）——每次治理操作的独立追加记录；留痕失败则业务一并回滚（§3.11，R-098）。"""

    id: UUID
    project_id: UUID
    user_id: UUID
    action: str
    based_on_snapshot: UUID | None = None
    detail: Mapping[str, Any] | None = None
    occurred_at: datetime


@dataclass(frozen=True, kw_only=True)
class AgentRun:
    """运行记录（不可变）——每次智能体运行的模型/提示词/技能包/参数版本登记（§3.12）。"""

    id: UUID
    task_id: UUID
    model_version: str
    prompt_version: str
    skill_versions: Mapping[str, Any] | None = None
    params: Mapping[str, Any] | None = None
    started_at: datetime
    ended_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class TaskReadRecord:
    """任务读取留痕（关系）——某任务读取过哪些解析文本（§3.13）。"""

    task_id: UUID
    parse_result_id: UUID
    read_at: datetime
