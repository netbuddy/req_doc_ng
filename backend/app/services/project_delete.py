"""项目删除计划（AEP-113 DELETE /projects/{project_id}）。

47 张 ORM 表零 FK 约束（全软引用），DB 级 CASCADE 不可行；本模块以数据结构声明
每张表的挂靠谓词（DELETE_PLAN），删除与残留断言复用同一份谓词，不许两处手写。
盘点事实源：docs/iterations/PRJ-001/删除计划盘点表.md（与本文件同步维护）。

覆盖门禁：tests/test_project_delete.py 遍历 Base.metadata 强制
每张表 ∈ DELETE_PLAN ∪ GLOBAL_WHITELIST，新增表不登记即测试红。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AdoptionRecord,
    AgentRun,
    AgentRunEvent,
    ChartSourceRevision,
    ChartSuggestionRequest,
    ChartVerificationFinding,
    ChartVerificationRequest,
    ChartVerificationRound,
    DemoChatTranscript,
    DocumentIndexEntry,
    DocxExport,
    ElementChangeDraft,
    ElementFacetProjection,
    ElementHistory,
    ElementOperation,
    IntakeRecord,
    IntakeRequest,
    Issue,
    ItemDiagnosisRequest,
    ItemDiagnosisRound,
    ItemFindingVeto,
    ItemFormationRequest,
    ItemizationOutcome,
    ItemRevisionSuggestion,
    ItemReviewFinding,
    ItemStructureProjection,
    MarkdownDraft,
    MarkdownPatch,
    KnowledgeAsset,
    KnowledgeSnapshot,
    Material,
    MaterialParseResult,
    MaterialRevision,
    MaterialSupplement,
    ModelResult,
    Notification,
    ParseRequest,
    Project,
    ReleaseBaseline,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    RequirementItemRevision,
    SearchIndex,
    SectionManuscript,
    TraceLink,
)
from app.domain.errors import NotFound, RejectedTransition
from app.log import log_event

_COMPONENT = "backend-api"

# AgentRun 在飞状态（AgentRunStatus）：删除守卫按此拒绝删除。
_INFLIGHT_STATUSES = ("queued", "started")


class InFlightTasksBlockDeletion(RejectedTransition):
    """删除被拒：项目内存在执行中的 AI 任务（2026-08-07 信封化后由 API 层转业务拒绝信封）。"""

    def __init__(self, count: int) -> None:
        super().__init__(f"项目内有 {count} 个进行中的 AI 任务，请稍后再删")
        self.count = count


@dataclass(frozen=True)
class TableRule:
    """一张表的挂靠谓词：`column ∈ scope[scope_key]`。"""

    model: type
    column: str
    scope_key: str


# 删除计划（43 表；顺序=子先父后，Project 根最后）。谓词口径见盘点表 §2/§3。
DELETE_PLAN: tuple[TableRule, ...] = (
    # 多态挂靠（先删事件再删 run；ModelResult 锚 = 六类请求上下文 ∪ 条目）
    TableRule(AgentRunEvent, "run_id", "run_ids"),
    TableRule(AgentRun, "context_ref", "agent_context_ids"),
    TableRule(ModelResult, "applies_to_ref", "model_result_anchor_ids"),
    TableRule(AdoptionRecord, "project_id", "project_ids"),
    TableRule(Notification, "project_ref", "project_ids"),
    TableRule(SearchIndex, "project_id", "project_ids"),
    # 演示留痕表（AI对话演示简化方案 2026-07-18）：项目级挂靠，删项目一并删留痕行防孤儿。
    # 与 §2.1「本方案不触碰 project_delete.py」的偏离，经用户 2026-07-18 拍板授权（治理测试
    # test_every_table_registered 要求每张表登记；本表有 project_ref 故入删除计划而非白名单）。
    TableRule(DemoChatTranscript, "project_ref", "project_ids"),
    # SCN-004 图表/核对/追溯/问题项
    TableRule(ChartVerificationFinding, "round_ref", "chart_round_ids"),
    TableRule(ChartVerificationRound, "chart_ref", "chart_ids"),
    TableRule(ChartSourceRevision, "chart_ref", "chart_ids"),
    TableRule(ChartVerificationRequest, "project_id", "project_ids"),
    TableRule(ChartSuggestionRequest, "project_id", "project_ids"),
    TableRule(RequirementChart, "project_id", "project_ids"),
    TableRule(TraceLink, "project_id", "project_ids"),
    TableRule(Issue, "project_id", "project_ids"),
    # SCN-005 文档族（LDM-014）
    TableRule(MarkdownPatch, "draft_ref", "draft_ids"),
    TableRule(ReleaseBaseline, "document_ref", "document_ids"),
    TableRule(DocxExport, "document_ref", "document_ids"),
    TableRule(MarkdownDraft, "document_ref", "document_ids"),
    TableRule(SectionManuscript, "document_ref", "document_ids"),
    TableRule(DocumentIndexEntry, "document_ref", "document_ids"),
    TableRule(RequirementDocument, "project_id", "project_ids"),
    # SCN-003 评审（LDM-009）
    TableRule(ItemReviewFinding, "item_ref", "item_ids"),
    TableRule(ItemFindingVeto, "item_ref", "item_ids"),  # 问题否决留痕（AEP-116）
    TableRule(ItemDiagnosisRound, "project_id", "project_ids"),
    TableRule(ItemDiagnosisRequest, "project_id", "project_ids"),
    # SCN-002 条目（LDM-007 + 过程记录）
    TableRule(ItemStructureProjection, "item_ref", "item_ids"),
    TableRule(RequirementItemRevision, "item_ref", "item_ids"),
    TableRule(ItemRevisionSuggestion, "item_ref", "item_ids"),
    TableRule(ItemizationOutcome, "formation_context_ref", "formation_ids"),
    TableRule(ItemFormationRequest, "project_id", "project_ids"),
    TableRule(RequirementItem, "project_id", "project_ids"),
    # SCN-001 要素（LDM-005 + 过程记录）
    TableRule(ElementFacetProjection, "element_ref", "element_ids"),
    TableRule(ElementHistory, "project_id", "project_ids"),
    TableRule(ElementChangeDraft, "project_id", "project_ids"),
    TableRule(ElementOperation, "project_id", "project_ids"),
    TableRule(RequirementElement, "project_id", "project_ids"),
    TableRule(MaterialParseResult, "project_id", "project_ids"),
    TableRule(ParseRequest, "project_id", "project_ids"),
    # 材料接入（LDM-002/003 + 过程记录）
    TableRule(MaterialRevision, "material_ref", "material_ids"),
    TableRule(MaterialSupplement, "material_ref", "material_ids"),
    TableRule(Material, "project_id", "project_ids"),
    TableRule(IntakeRecord, "project_id", "project_ids"),
    TableRule(IntakeRequest, "project_id", "project_ids"),
    # V2 知识层（2026-08-08 第一步）：快照先删（外键指向资产），资产后删
    TableRule(KnowledgeSnapshot, "asset_id", "knowledge_asset_ids"),
    TableRule(KnowledgeAsset, "project_id", "project_ids"),
    # 根
    TableRule(Project, "id", "project_ids"),
)

# 全局白名单：删项目一行不动（逐表理由，盘点表 §4 同步）。
GLOBAL_WHITELIST: dict[str, str] = {
    "config_entry": "支撑能力配置按 domain 全局唯一，无项目挂靠列；删项目不得影响模型服务/导出/图表渲染配置。",
    "config_audit": "配置保存审计留痕（域/操作者/字段名），全局审计链不随项目删除截断。",
    "template_registry": (
        "全局模板库：无项目挂靠列，内置模板按 content_hash 幂等同步，登记行不可变且被跨项目"
        "文档/发布基线引用，删除会击穿其它项目基线可解析性。"
    ),
    "template_draft": "模板定制器工作态：无项目挂靠列，属配置域暂存，不承载项目治理事实。",
}


def _ids(session: Session, model: type, column: str, values: list[uuid.UUID]) -> list[uuid.UUID]:
    if not values:
        return []
    col = getattr(model, column)
    return list(session.scalars(select(model.id).where(col.in_(values))).all())


def build_scope(session: Session, project_id: uuid.UUID) -> dict[str, list[uuid.UUID]]:
    """取材全部挂靠范围集（盘点表 §1）；删除与残留断言共用。"""
    project_ids = [project_id]
    material_ids = _ids(session, Material, "project_id", project_ids)
    element_ids = _ids(session, RequirementElement, "project_id", project_ids)
    item_ids = _ids(session, RequirementItem, "project_id", project_ids)
    chart_ids = _ids(session, RequirementChart, "project_id", project_ids)
    document_ids = _ids(session, RequirementDocument, "project_id", project_ids)
    draft_ids = _ids(session, MarkdownDraft, "document_ref", document_ids)
    docx_export_ids = _ids(session, DocxExport, "document_ref", document_ids)
    formation_ids = _ids(session, ItemFormationRequest, "project_id", project_ids)
    operation_ids = _ids(session, ElementOperation, "project_id", project_ids)
    chart_round_ids = _ids(session, ChartVerificationRound, "chart_ref", chart_ids)
    request_context_ids = (
        _ids(session, IntakeRequest, "project_id", project_ids)
        + _ids(session, ParseRequest, "project_id", project_ids)
        + formation_ids
        + _ids(session, ItemDiagnosisRequest, "project_id", project_ids)
        + _ids(session, ChartSuggestionRequest, "project_id", project_ids)
        + _ids(session, ChartVerificationRequest, "project_id", project_ids)
    )
    agent_context_ids = request_context_ids + operation_ids + docx_export_ids
    run_ids = _ids(session, AgentRun, "context_ref", agent_context_ids)
    knowledge_asset_ids = _ids(session, KnowledgeAsset, "project_id", project_ids)
    return {
        "project_ids": project_ids,
        "knowledge_asset_ids": knowledge_asset_ids,
        "material_ids": material_ids,
        "element_ids": element_ids,
        "item_ids": item_ids,
        "chart_ids": chart_ids,
        "document_ids": document_ids,
        "draft_ids": draft_ids,
        "docx_export_ids": docx_export_ids,
        "formation_ids": formation_ids,
        "operation_ids": operation_ids,
        "chart_round_ids": chart_round_ids,
        "request_context_ids": request_context_ids,
        "model_result_anchor_ids": request_context_ids + item_ids,
        "agent_context_ids": agent_context_ids,
        "run_ids": run_ids,
    }


def residual_counts(session: Session, scope: dict[str, list[uuid.UUID]]) -> dict[str, int]:
    """按计划谓词逐表数残留行（复用 DELETE_PLAN，删净断言唯一口径）。"""
    counts: dict[str, int] = {}
    for rule in DELETE_PLAN:
        values = scope[rule.scope_key]
        if not values:
            counts[rule.model.__tablename__] = 0
            continue
        col = getattr(rule.model, rule.column)
        counts[rule.model.__tablename__] = int(
            session.scalar(select(func.count()).select_from(rule.model).where(col.in_(values))) or 0
        )
    return counts


@dataclass(frozen=True)
class ProjectDeleteOutcome:
    project_ref: str
    project_name: str
    deleted_rows: int
    table_counts: dict[str, int]
    files_deleted: int
    files_failed: int


class ProjectDeleteService:
    """AEP-113：应用层级联删净（单事务）＋跨项目零误删。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def delete_project(self, project_ref: str, operator_ref: str = "") -> ProjectDeleteOutcome:
        try:
            pid = uuid.UUID(project_ref)
        except (ValueError, AttributeError, TypeError):
            raise NotFound(f"业务项目不存在：{project_ref}")
        project = self._s.get(Project, pid)
        if project is None:
            raise NotFound(f"业务项目不存在：{project_ref}")

        scope = build_scope(self._s, pid)

        # 守卫：项目内存在在飞 AgentRun → 409（不与执行中的 worker 抢写）。
        inflight = 0
        if scope["agent_context_ids"]:
            inflight = int(self._s.scalar(
                select(func.count()).select_from(AgentRun).where(
                    AgentRun.context_ref.in_(scope["agent_context_ids"]),
                    AgentRun.status.in_(_INFLIGHT_STATUSES),
                )
            ) or 0)
        if inflight:
            raise InFlightTasksBlockDeletion(inflight)

        # 落盘导出件路径先取材（行删掉后无从取）；文件删除在事务提交后尽力执行。
        file_paths = [
            p for p in self._s.scalars(
                select(DocxExport.file_path).where(
                    DocxExport.document_ref.in_(scope["document_ids"]),
                    DocxExport.file_path.is_not(None),
                )
            ).all()
        ] if scope["document_ids"] else []

        project_name = project.name
        table_counts: dict[str, int] = {}
        for rule in DELETE_PLAN:
            values = scope[rule.scope_key]
            if not values:
                table_counts[rule.model.__tablename__] = 0
                continue
            col = getattr(rule.model, rule.column)
            result = self._s.execute(delete(rule.model).where(col.in_(values)))
            table_counts[rule.model.__tablename__] = int(result.rowcount or 0)

        total = sum(table_counts.values())
        log_event(
            _COMPONENT, "project.delete.executed",
            msg="项目级联删除完成（单事务）",
            project_ref=project_ref,
            operator_ref=operator_ref,
            deleted_rows=total,
            table_counts={k: v for k, v in table_counts.items() if v},
            docx_files=len(file_paths),
        )
        self._s.commit()

        files_deleted = 0
        files_failed = 0
        for path in file_paths:
            try:
                target = Path(path)
                if target.exists():
                    target.unlink()
                    files_deleted += 1
            except OSError as exc:
                files_failed += 1
                log_event(
                    _COMPONENT, "project.delete.file_cleanup_failed",
                    msg="导出件落盘文件删除失败（不回滚）", level="ERROR",
                    project_ref=project_ref, error_code=type(exc).__name__,
                )

        return ProjectDeleteOutcome(
            project_ref=project_ref,
            project_name=project_name,
            deleted_rows=total,
            table_counts={k: v for k, v in table_counts.items() if v},
            files_deleted=files_deleted,
            files_failed=files_failed,
        )
