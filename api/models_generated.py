# 机器生成文件，勿手改。正本＝api/openapi.yaml 与 api/schemas/*.yaml；重新生成命令见 docs/design/接口设计说明.md。

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, RootModel


class Uuid(RootModel[UUID]):
    root: UUID = Field(
        ..., description='全局唯一标识符，UUID v7 变体（共享结构决定点一）。'
    )


class BasedOnBody(BaseModel):
    based_on: Uuid = Field(
        ...,
        description='所依据的内容快照标识（规范中文名：所依据快照）——治理者审阅时所见的那份候选稿，写操作的并发校验凭据。',
    )


class BusinessRejection(BaseModel):
    category: Literal['业务拒绝'] = Field(..., description='类别，固定值。')
    reason_code: str = Field(
        ...,
        description='原因码（中文短语，机器码，正本见 docs/design/业务拒绝原因码表.md）。',
        examples=['快照已过时', '存在阻断级标记', '引用未确认领域知识', '非项目成员'],
    )
    message: str = Field(..., description='文案（人可读，可改；码不可改）。')
    details: dict[str, Any] | None = Field(
        None, description='详情——随码而异的结构化参数，逐码的详情字段随码表登记。'
    )


class ErrorShape(BaseModel):
    category: Literal['错误'] = Field(..., description='类别，固定值。')
    message: str = Field(..., description='文案。')
    fault_id: str = Field(
        ..., description='故障标识——关联服务端日志的唯一标识，报障凭据。'
    )
    retryable: bool = Field(..., description='可重试——调用方据此决定重试或放弃。')


class BusinessRejectionEnvelope(BaseModel):
    result: Literal['业务拒绝']
    rejection: BusinessRejection


class SuccessEmpty(BaseModel):
    result: Literal['成功']


class Category(StrEnum):
    功能需求 = '功能需求'
    约束 = '约束'
    其他 = '其他'


class AttributeResolved(BaseModel):
    name: str = Field(..., description='属性名（开放命名）。', min_length=1)
    value: str = Field(..., description='属性值（骨架期一律为文字）。')


class AttributeAmbiguous(BaseModel):
    name: str = Field(..., description='属性名（开放命名）。', min_length=1)
    candidates: list[str] = Field(
        ...,
        description='候选解释列表——该属性的多种可能取值，待治理者选定。',
        min_length=2,
    )


class AttributeItem(RootModel[AttributeResolved | AttributeAmbiguous]):
    root: AttributeResolved | AttributeAmbiguous = Field(
        ...,
        description='一条结构化属性。两种形态之一：歧义已消解＝name 加 value；\n歧义未消解＝name 加 candidates（候选解释列表，R-007：提取遇多种可能解释不得自行择一送审）。\n带着未消解 candidates 的知识过不了「结构规范」绿灯（R-038），确认被拦；\n治理者经人工修订（reviseCandidate）选定其一后方可确认（2026-08-04 默认裁定）。\n',
    )


class ConceptContent(BaseModel):
    kind: Literal['领域概念']
    name: str = Field(..., description='名称。', min_length=1)
    definition: str = Field(
        ...,
        description='定义——即本类知识单元的描述字段（R-005 对两类知识同样成立）。',
        min_length=1,
    )
    aliases: list[str] | None = Field(
        None, description='别名——同一概念在材料中出现过的其他叫法。'
    )
    attributes: list[AttributeItem] | None = Field(
        None, description='开放键值属性（可选，与需求知识同构）。'
    )
    concept_refs: list[Uuid] | None = Field(
        None, description='领域引用——概念之间的引用（如上位概念），可选。'
    )


class MaterialReceipt(BaseModel):
    material_id: Uuid
    content_sha256: str = Field(
        ..., description='文件哈希（SHA-256）——机器核验「导入即不可改写」的凭据。'
    )
    imported_at: AwareDatetime = Field(..., description='导入时刻。')


class SourceKind(StrEnum):
    文件 = '文件'
    粘贴 = '粘贴'


class MaterialSummary(BaseModel):
    material_id: Uuid
    name: str = Field(..., description='材料名称。')
    source_kind: SourceKind = Field(..., description='来源形态。')
    imported_at: AwareDatetime


class TaskReceipt(BaseModel):
    task_id: Uuid
    status: Literal['已登记'] = Field(
        ..., description='初始状态恒为「已登记」（任务五状态之首，冻结包裁定甲-30）。'
    )


class FailureItem(BaseModel):
    reason: str = Field(..., description='该失败项的原因说明。')


class Kind(StrEnum):
    提取 = '提取'


class Status(StrEnum):
    已登记 = '已登记'
    进行中 = '进行中'
    待裁决 = '待裁决'
    已完成 = '已完成'
    失败 = '失败'


class TaskStatus(BaseModel):
    task_id: Uuid
    kind: Kind = Field(
        ..., description='任务类别（骨架期只有提取；成文任务随条目形成环加入）。'
    )
    status: Status = Field(
        ...,
        description='封闭五状态（冻结包裁定甲-30）——「待裁决」＝候选已全部提交、等治理者逐条裁决；「已完成」＝该批候选全部裁决完毕；「失败」＝整体失败零候选提交。',
    )
    failure_items: list[FailureItem] | None = Field(
        None,
        description='失败项清单——部分候选解析失败不阻碍任务完成，失败项在此可查（甲-30 对「部分成功」的处置）。',
    )
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Kind1(StrEnum):
    需求知识 = '需求知识'
    领域概念 = '领域概念'


class CandidateSummary(BaseModel):
    asset_id: Uuid
    kind: Kind1 = Field(..., description='知识类别。')
    title: str = Field(..., description='显示名——需求知识取其标题，领域概念取其名称。')
    snapshot_id: Uuid = Field(..., description='当前候选稿的快照标识。')
    task_id: Uuid | None = Field(
        None, description='产生它的任务标识（追溯与运行记录对上用）。'
    )
    submitted_at: AwareDatetime = Field(..., description='当前候选稿的提交时刻。')


class SourceAnchorView(BaseModel):
    material_id: Uuid = Field(..., description='材料标识。')
    material_name: str | None = Field(None, description='材料名称（展示用）。')
    parse_result_id: Uuid = Field(..., description='解析结果标识。')
    start_offset: int = Field(
        ..., description='起始偏移——按 Unicode 码点计数（数「字」不数字节）。', ge=0
    )
    end_offset: int = Field(..., description='结束偏移（码点计数）。', ge=0)
    quote: str = Field(
        ..., description='引文精确子串——提交时经硬校验，必为解析文本的原文子串。'
    )
    hit_count: int = Field(
        ...,
        description='命中数——引文在查找范围内出现的次数，常态为 1，多命中取首个定锚、命中数如实记（共享结构决定点二）。',
        ge=1,
    )
    context_fragment: str | None = Field(
        None,
        description='原文语境片段——引文及其前后文，供确认界面「来源片段同屏」展示（R-036）。',
    )


class AuthorKind(StrEnum):
    智能体 = '智能体'
    治理者 = '治理者'


class VersionReceipt(BaseModel):
    asset_id: Uuid
    version_no: int = Field(
        ..., description='版本号——治理层在固化时刻授予的序号（不是第二套标识）。', ge=1
    )
    snapshot_id: Uuid = Field(..., description='被固化为版本的那份快照。')


class SnapshotReceipt(BaseModel):
    asset_id: Uuid
    snapshot_id: Uuid = Field(
        ..., description='新产生的候选稿快照标识（此后写操作以它为 based_on）。'
    )
    author_kind: Literal['治理者'] = Field(
        ..., description='作者身份——人工修订产生的快照恒记「治理者」。'
    )


class SuccessOfMaterialReceipt(BaseModel):
    result: Literal['成功']
    data: MaterialReceipt


class SuccessOfMaterialList(BaseModel):
    result: Literal['成功']
    data: list[MaterialSummary]


class SuccessOfTaskReceipt(BaseModel):
    result: Literal['成功']
    data: TaskReceipt


class SuccessOfTaskStatus(BaseModel):
    result: Literal['成功']
    data: TaskStatus


class SuccessOfCandidateList(BaseModel):
    result: Literal['成功']
    data: list[CandidateSummary]


class SuccessOfVersionReceipt(BaseModel):
    result: Literal['成功']
    data: VersionReceipt


class SuccessOfSnapshotReceipt(BaseModel):
    result: Literal['成功']
    data: SnapshotReceipt


class AnchorInput(BaseModel):
    material_id: Uuid = Field(..., description='材料标识——引文出自哪份材料。')
    quote: str = Field(
        ..., description='引文——材料解析文本的精确子串，提交时经硬校验。', min_length=1
    )


class MaterialParsedText(BaseModel):
    material_id: Uuid = Field(..., description='材料标识。')
    parse_result_id: Uuid = Field(
        ...,
        description='解析结果标识——锚点六字段之一，随候选提交时由内核回填对应锚点。',
    )
    text: str = Field(
        ..., description='解析后的规范化全文（偏移按 Unicode 码点在此文本上计算）。'
    )


class CandidateSubmissionReceipt(BaseModel):
    asset_id: Uuid = Field(
        ..., description='资产标识——首次提交时新建，退回重做时沿用原资产。'
    )
    snapshot_id: Uuid = Field(..., description='本次提交固化的候选稿快照标识。')


class RequirementContent(BaseModel):
    kind: Literal['需求知识']
    title: str = Field(..., description='标题——短名，队列与列表显示用。', min_length=1)
    description: str = Field(
        ...,
        description='描述字段——面向人阅读的陈述文字，存储件而非临时生成物（R-005）；人阅读与核对从这里入手。',
        min_length=1,
    )
    category: Category = Field(
        ..., description='类别——骨架期粗分三档（2026-08-04 用户裁定），细分类目延后。'
    )
    attributes: list[AttributeItem] | None = Field(
        None,
        description='开放键值属性——属性名由提取智能体按材料实际情况自定（如「时限」「执行角色」）；机器检测与投影以属性为操作对象。',
    )
    concept_refs: list[Uuid] | None = Field(
        None,
        description='领域引用——指向所涉领域概念的资产标识。同组新提出的概念候选同样以标识指；「需求知识不得正式引用未确认领域知识」（R-008）与「同组原子确认」（R-009）由治理状态机在确认门禁执行，内容结构不作特殊表示（2026-08-04 默认裁定）。',
    )


class KnowledgeContent(RootModel[RequirementContent | ConceptContent]):
    root: RequirementContent | ConceptContent = Field(
        ...,
        description='知识单元内容——两类知识各一个结构，以 kind 字段区分。',
        discriminator='kind',
    )


class CandidateDetail(BaseModel):
    asset_id: Uuid
    kind: Kind1
    snapshot_id: Uuid = Field(
        ..., description='当前候选稿的快照标识——治理者随后写操作的 based_on 凭据。'
    )
    content: KnowledgeContent = Field(..., description='当前候选稿内容。')
    anchors: list[SourceAnchorView] = Field(
        ..., description='来源锚点——知识单元与锚点一对多，逐条附原文语境。'
    )
    author_kind: AuthorKind | None = Field(
        None, description='当前候选稿的作者身份（人工修订过的候选稿为「治理者」）。'
    )
    task_id: Uuid | None = None
    submitted_at: AwareDatetime


class SuccessOfCandidateDetail(BaseModel):
    result: Literal['成功']
    data: CandidateDetail
