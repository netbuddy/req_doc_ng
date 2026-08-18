"""追溯分析服务专用只读仓储（AEP-058…AEP-064 投影输入）。

边界（06B §3.12 / 页面设计 §2）：关系网是派生只读投影，可整层重建，不是第二份事实源——
按项目一次载入既有事实源（LDM-002/004/005/007/012/013/014）的最小字段集，
供 TraceAnalysisService 派生邻域窗口、覆盖度与缺口/可疑清单。
跨聚合直查 ORM 表是纯读模型的合法形态（同 overview_read.py），不改各写权威仓储的 Protocol。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentIndexEntry,
    Material,
    MaterialParseResult,
    Project,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    TraceLink,
)


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _refs(raw: Optional[str]) -> list[str]:
    """JSON id 列表字段安全解码（坏数据按空处理，不让读模型抛错）。"""
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(r) for r in parsed] if isinstance(parsed, list) else []


@dataclass(frozen=True)
class MaterialFact:
    id: str
    source_note: str
    raw_text_head: str
    created_at: datetime


@dataclass(frozen=True)
class ParseResultFact:
    id: str
    material_ref: str


@dataclass(frozen=True)
class ElementFact:
    id: str
    parse_result_ref: str
    element_type: str
    content_head: str
    process_status: str
    superseded: bool
    updated_at: datetime
    source_anchor: Optional[str] = None  # LDM-005 来源锚点原始 JSON（材料→要素边引文投影输入）


@dataclass(frozen=True)
class ItemFact:
    id: str
    req_no: str
    expression_head: str
    req_type: str
    status: str
    source_element_refs: tuple[str, ...]
    updated_at: datetime


@dataclass(frozen=True)
class ChartFact:
    id: str
    title: str
    chart_type: str
    status: str
    source_refs: tuple[str, ...]
    updated_at: datetime


@dataclass(frozen=True)
class DocumentFact:
    id: str
    title: str
    status: str
    index_version: int
    updated_at: datetime


@dataclass(frozen=True)
class DocIndexEntryFact:
    document_ref: str
    index_version: int
    asset_type: str
    asset_ref: Optional[str]


@dataclass(frozen=True)
class TraceLinkFact:
    id: str
    relation_type: str
    upstream_type: str
    upstream_ref: str
    downstream_type: str
    downstream_ref: str
    status: str
    initial_basis: str
    status_reason: Optional[str]
    established_basis: Optional[str]
    established_at: Optional[datetime]
    issue_ref: Optional[str]
    updated_at: datetime


@dataclass(frozen=True)
class ProjectTraceFacts:
    """一个项目的关系网事实快照（单次载入，窗口/诊断共用）。"""

    materials: list[MaterialFact]
    parse_results: list[ParseResultFact]
    elements: list[ElementFact]
    items: list[ItemFact]
    charts: list[ChartFact]
    documents: list[DocumentFact]
    doc_index_entries: list[DocIndexEntryFact]
    trace_links: list[TraceLinkFact]


def _head(text: Optional[str], limit: int = 40) -> str:
    stripped = (text or "").strip().replace("\n", " ")
    return stripped[:limit]


class TraceReadRepository:
    """按项目载入关系网投影所需事实（全部走 project_id / ref 索引列）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def project_exists(self, project_ref: str) -> bool:
        pid = _as_uuid(project_ref)
        if pid is None:
            return False
        return self._s.get(Project, pid) is not None

    def load_project_facts(self, project_ref: str) -> ProjectTraceFacts:
        pid = _as_uuid(project_ref)
        if pid is None:
            return ProjectTraceFacts([], [], [], [], [], [], [], [])

        materials = [
            MaterialFact(
                id=str(m.id),
                source_note=(m.source_note or "").strip(),
                raw_text_head=_head(m.raw_text),
                created_at=m.created_at,
            )
            for m in self._s.scalars(select(Material).where(Material.project_id == pid)).all()
        ]
        parse_results = [
            ParseResultFact(id=str(r.id), material_ref=str(r.material_ref))
            for r in self._s.scalars(
                select(MaterialParseResult).where(MaterialParseResult.project_id == pid)
            ).all()
        ]
        elements = [
            ElementFact(
                id=str(e.id),
                parse_result_ref=str(e.parse_result_ref),
                element_type=e.element_type,
                content_head=_head(e.content),
                process_status=e.process_status,
                superseded=bool(e.superseded),
                updated_at=e.updated_at,
                source_anchor=e.source_anchor,
            )
            for e in self._s.scalars(
                select(RequirementElement).where(RequirementElement.project_id == pid)
            ).all()
        ]
        items = [
            ItemFact(
                id=str(i.id),
                req_no=i.req_no,
                expression_head=_head(i.expression),
                req_type=i.req_type,
                status=i.status,
                source_element_refs=tuple(_refs(i.source_element_refs)),
                updated_at=i.updated_at,
            )
            for i in self._s.scalars(
                select(RequirementItem).where(RequirementItem.project_id == pid)
            ).all()
        ]
        charts = [
            ChartFact(
                id=str(c.id),
                title=c.title,
                chart_type=c.chart_type,
                status=c.status,
                source_refs=tuple(_refs(c.source_refs)),
                updated_at=c.updated_at,
            )
            for c in self._s.scalars(
                select(RequirementChart).where(RequirementChart.project_id == pid)
            ).all()
        ]
        documents = [
            DocumentFact(
                id=str(d.id),
                title=d.title,
                status=d.status,
                index_version=d.index_version,
                updated_at=d.updated_at,
            )
            for d in self._s.scalars(
                select(RequirementDocument).where(RequirementDocument.project_id == pid)
            ).all()
        ]
        doc_ids = [_as_uuid(d.id) for d in documents]
        doc_index_entries = (
            [
                DocIndexEntryFact(
                    document_ref=str(en.document_ref),
                    index_version=en.index_version,
                    asset_type=en.asset_type,
                    asset_ref=str(en.asset_ref) if en.asset_ref else None,
                )
                for en in self._s.scalars(
                    select(DocumentIndexEntry).where(DocumentIndexEntry.document_ref.in_(doc_ids))
                ).all()
            ]
            if doc_ids
            else []
        )
        trace_links = [
            TraceLinkFact(
                id=str(t.id),
                relation_type=t.relation_type,
                upstream_type=t.upstream_type,
                upstream_ref=str(t.upstream_ref),
                downstream_type=t.downstream_type,
                downstream_ref=str(t.downstream_ref),
                status=t.status,
                initial_basis=t.initial_basis,
                status_reason=t.status_reason,
                established_basis=t.established_basis,
                established_at=t.established_at,
                issue_ref=str(t.issue_ref) if t.issue_ref else None,
                updated_at=t.updated_at,
            )
            for t in self._s.scalars(select(TraceLink).where(TraceLink.project_id == pid)).all()
        ]
        return ProjectTraceFacts(
            materials=materials,
            parse_results=parse_results,
            elements=elements,
            items=items,
            charts=charts,
            documents=documents,
            doc_index_entries=doc_index_entries,
            trace_links=trace_links,
        )
