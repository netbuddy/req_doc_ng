"""总览台专用只读仓储（AEP-052/072 投影输入）。

边界（UINV-21/22 / 页面设计 §1）：只读、不写任何表、不持第二份事实源——
按项目一次载入既有事实源（LDM-002/003/004/005/007 + 过程记录）的最小字段集，
供 OverviewService 派生资产计数与流程阶段。跨聚合直查 ORM 表是纯读模型的
合法形态，避免改动各写权威仓储的 Protocol（与并行开发解耦）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    IntakeRecord,
    IntakeRequest,
    Issue,
    ItemFormationRequest,
    Material,
    MaterialParseResult,
    ParseRequest,
    Project,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
)


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _refs(raw: Optional[str]) -> tuple[str, ...]:
    """JSON id 列表字段安全解码（坏数据按空处理，不让读模型抛错；与 trace_read._refs 同口径）。"""
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return ()
    return tuple(str(r) for r in parsed) if isinstance(parsed, list) else ()


@dataclass(frozen=True)
class IntakeRequestFact:
    id: str
    source_note: str
    stop_next_action: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class IntakeDismissFacts:
    """放弃本次接入（AEP-111）守卫所需事实：接入结论 + 既有软删时间戳。"""

    conclusion: Optional[str]
    dismissed_at: Optional[datetime]


@dataclass(frozen=True)
class IntakeRecordFact:
    context_ref: str
    intake_conclusion: str
    material_ref: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class MaterialFact:
    id: str
    created_at: datetime


@dataclass(frozen=True)
class ParseRequestFact:
    id: str
    material_ref: str
    stop_next_action: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class ParseResultFact:
    id: str  # parse_result_ref
    context_ref: str  # parse_context_ref
    material_ref: str
    parse_status: str
    created_at: datetime


@dataclass(frozen=True)
class ElementFact:
    id: str  # 转化链数字桥按来源引用逐个知识项定位其去向所需
    parse_result_ref: str
    element_type: str
    process_status: str
    superseded: bool
    updated_at: datetime


@dataclass(frozen=True)
class ItemFact:
    id: str  # 一条条目可由多个知识项归并而成，按 id 去重才不会把它重复计数
    parse_result_ref: str
    formation_context_ref: str
    status: str
    req_type: str
    source_element_refs: tuple[str, ...]  # 空元组＝直建条目（无知识项来源）
    updated_at: datetime


@dataclass(frozen=True)
class FormationRequestFact:
    id: str  # formation_context_ref
    parse_context_ref: str
    parse_result_ref: str
    stop_next_action: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class ProjectOverviewFacts:
    """一个项目的总览事实快照（单次载入，派生与计数共用）。"""

    intake_requests: list[IntakeRequestFact]
    intake_records: list[IntakeRecordFact]
    materials: list[MaterialFact]
    parse_requests: list[ParseRequestFact]
    parse_results: list[ParseResultFact]
    elements: list[ElementFact]
    items: list[ItemFact]
    formation_requests: list[FormationRequestFact]


class OverviewReadRepository:
    """按项目载入总览投影所需事实（全部走 project_id / ref 索引列）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def project_exists(self, project_ref: str) -> bool:
        pid = _as_uuid(project_ref)
        if pid is None:
            return False
        return self._s.get(Project, pid) is not None

    def count_catalog_assets(self, project_ref: str) -> tuple[int, int, int]:
        """资产盘点补充计数：(图表, 文档, 问题项)——LDM-012/014/011 只读计数。"""
        pid = _as_uuid(project_ref)
        if pid is None:
            return (0, 0, 0)

        def count(model) -> int:
            return int(
                self._s.scalar(
                    select(func.count()).select_from(model).where(model.project_id == pid)
                )
                or 0
            )

        return (count(RequirementChart), count(RequirementDocument), count(Issue))

    def load_project_facts(self, project_ref: str) -> ProjectOverviewFacts:
        pid = _as_uuid(project_ref)
        if pid is None:
            return ProjectOverviewFacts([], [], [], [], [], [], [], [])

        intake_requests = [
            IntakeRequestFact(
                id=str(r.id),
                source_note=r.source_note or "",
                stop_next_action=r.stop_next_action,
                created_at=r.created_at,
            )
            for r in self._s.scalars(
                select(IntakeRequest)
                # 已放弃（软删）的接入根不进投影（OVW-001 修订 2026-07-10）；行保留可审计
                .where(IntakeRequest.project_id == pid, IntakeRequest.dismissed_at.is_(None))
                .order_by(IntakeRequest.created_at, IntakeRequest.id)
            ).all()
        ]
        intake_records = [
            IntakeRecordFact(
                context_ref=str(r.context_ref),
                intake_conclusion=r.intake_conclusion,
                material_ref=str(r.material_ref) if r.material_ref else None,
                created_at=r.created_at,
            )
            for r in self._s.scalars(
                select(IntakeRecord).where(IntakeRecord.project_id == pid)
            ).all()
        ]
        materials = [
            MaterialFact(id=str(m.id), created_at=m.created_at)
            for m in self._s.scalars(
                select(Material).where(Material.project_id == pid)
            ).all()
        ]
        parse_requests = [
            ParseRequestFact(
                id=str(r.id),
                material_ref=str(r.material_ref),
                stop_next_action=r.stop_next_action,
                created_at=r.created_at,
            )
            for r in self._s.scalars(
                select(ParseRequest)
                .where(ParseRequest.project_id == pid)
                .order_by(ParseRequest.created_at, ParseRequest.id)
            ).all()
        ]
        parse_results = [
            ParseResultFact(
                id=str(r.id),
                context_ref=str(r.context_ref),
                material_ref=str(r.material_ref),
                parse_status=r.parse_status,
                created_at=r.created_at,
            )
            for r in self._s.scalars(
                select(MaterialParseResult).where(MaterialParseResult.project_id == pid)
            ).all()
        ]
        elements = [
            ElementFact(
                id=str(e.id),
                parse_result_ref=str(e.parse_result_ref),
                element_type=e.element_type,
                process_status=e.process_status,
                superseded=bool(e.superseded),
                updated_at=e.updated_at,
            )
            for e in self._s.scalars(
                select(RequirementElement).where(RequirementElement.project_id == pid)
            ).all()
        ]
        items = [
            ItemFact(
                id=str(i.id),
                parse_result_ref=str(i.parse_result_ref),
                formation_context_ref=str(i.formation_context_ref),
                status=i.status,
                req_type=i.req_type,
                source_element_refs=_refs(i.source_element_refs),
                updated_at=i.updated_at,
            )
            for i in self._s.scalars(
                select(RequirementItem).where(RequirementItem.project_id == pid)
            ).all()
        ]
        formation_requests = [
            FormationRequestFact(
                id=str(r.id),
                parse_context_ref=str(r.parse_context_ref),
                parse_result_ref=str(r.parse_result_ref),
                stop_next_action=r.stop_next_action,
                created_at=r.created_at,
            )
            for r in self._s.scalars(
                select(ItemFormationRequest)
                .where(ItemFormationRequest.project_id == pid)
                .order_by(ItemFormationRequest.created_at, ItemFormationRequest.id)
            ).all()
        ]
        return ProjectOverviewFacts(
            intake_requests=intake_requests,
            intake_records=intake_records,
            materials=materials,
            parse_requests=parse_requests,
            parse_results=parse_results,
            elements=elements,
            items=items,
            formation_requests=formation_requests,
        )

    # ---- 终结态流程处置（OVW-001 修订 2026-07-10：AEP-111 放弃 / AEP-112 预填）----

    def _intake_request_in_project(self, project_ref: str, context_ref: str) -> Optional[IntakeRequest]:
        pid, ctx = _as_uuid(project_ref), _as_uuid(context_ref)
        if pid is None or ctx is None:
            return None
        row = self._s.get(IntakeRequest, ctx)
        if row is None or row.project_id != pid:
            return None
        return row

    def read_intake_source(self, project_ref: str, context_ref: str) -> Optional[tuple[str, str]]:
        """读接入上下文提交内容 (raw_text, source_note)（继续编辑预填；raw_text 属用户内容，不进日志）。"""
        row = self._intake_request_in_project(project_ref, context_ref)
        if row is None:
            return None
        return (row.raw_text, row.source_note or "")

    def read_intake_dismiss_facts(self, project_ref: str, context_ref: str) -> Optional[IntakeDismissFacts]:
        row = self._intake_request_in_project(project_ref, context_ref)
        if row is None:
            return None
        record = self._s.scalars(
            select(IntakeRecord).where(IntakeRecord.context_ref == row.id)
        ).first()
        return IntakeDismissFacts(
            conclusion=record.intake_conclusion if record else None,
            dismissed_at=row.dismissed_at,
        )

    def mark_intake_dismissed(self, context_ref: str) -> datetime:
        """置 dismissed_at（软删）；调用方负责守卫终结态与幂等判断。"""
        row = self._s.get(IntakeRequest, _as_uuid(context_ref))
        when = datetime.now(timezone.utc)
        row.dismissed_at = when
        self._s.flush()
        return when
