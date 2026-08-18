"""需求资产目录服务专用只读仓储（资产树/资产详情/维护列表投影输入）。

边界（04A §5 / UINV-09）：资产工作台是项目级只读目录视图，树节点不是新的事实对象——
按项目一次载入既有事实源（LDM-002/003/005/007/011/012/013/014 + 修订记录）的字段集，
供 AssetCatalogService 派生资产树、资产详情与需求条目维护列表。
跨聚合直查 ORM 表是纯读模型的合法形态（同 overview_read.py / trace_read.py）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentIndexEntry,
    ElementHistory,
    IntakeRecord,
    Issue,
    ItemDiagnosisRound,
    ItemReviewFinding,
    Material,
    MaterialParseResult,
    Project,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    RequirementItemRevision,
    TraceLink,
)


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _refs(raw: Optional[str]) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(r) for r in parsed] if isinstance(parsed, list) else []


@dataclass(frozen=True)
class MaterialAsset:
    id: str
    source_note: str
    raw_text_head: str
    created_at: datetime


@dataclass(frozen=True)
class IntakeRecordFact:
    material_ref: Optional[str]
    intake_conclusion: str
    created_at: datetime


@dataclass(frozen=True)
class ParseResultFact:
    id: str
    material_ref: str


@dataclass(frozen=True)
class ElementAsset:
    id: str
    parse_result_ref: str
    element_type: str
    content: str
    process_status: str
    superseded: bool
    updated_at: datetime
    anchor_count: int = 1  # 来源锚点数 = 1 + 登记归并次数（P3 §2.1 选型 B：merge 留痕计数）


@dataclass(frozen=True)
class ItemAsset:
    id: str
    req_no: str
    expression: str
    req_type: str
    status: str
    source_element_refs: tuple[str, ...]
    parse_result_ref: str
    updated_at: datetime
    verification_method: str | None = None  # 多选逗号连接（29148 属性补齐）
    verification_note: str | None = None
    priority: str | None = None


@dataclass(frozen=True)
class ItemRevisionFact:
    item_ref: str
    field_key: str
    before_value: str
    after_value: str
    revision_mode: str
    reason: Optional[str]
    operator_ref: str
    created_at: datetime


@dataclass(frozen=True)
class ChartAsset:
    id: str
    title: str
    chart_type: str
    status: str
    source_refs: tuple[str, ...]
    updated_at: datetime


@dataclass(frozen=True)
class DocumentAsset:
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
class TraceLinkAsset:
    id: str
    upstream_type: str
    upstream_ref: str
    downstream_type: str
    downstream_ref: str
    status: str
    status_reason: Optional[str]
    updated_at: datetime


@dataclass(frozen=True)
class IssueAsset:
    id: str
    issue_type: str
    status: str
    title: str
    origin_kind: str
    created_at: datetime


@dataclass(frozen=True)
class ProjectAssetFacts:
    """一个项目的资产目录事实快照（单次载入；目录/详情/维护列表共用）。"""

    materials: list[MaterialAsset]
    intake_records: list[IntakeRecordFact]
    parse_results: list[ParseResultFact]
    elements: list[ElementAsset]
    items: list[ItemAsset]
    item_revisions: list[ItemRevisionFact]
    charts: list[ChartAsset]
    documents: list[DocumentAsset]
    doc_index_entries: list[DocIndexEntryFact]
    trace_links: list[TraceLinkAsset]
    issues: list[IssueAsset]


def _head(text: Optional[str], limit: int = 200) -> str:
    stripped = (text or "").strip().replace("\n", " ")
    return stripped[:limit]


class AssetReadRepository:
    """按项目载入资产目录投影所需事实（全部走 project_id / ref 索引列）。"""

    def __init__(self, session: Session) -> None:
        self._s = session

    def project_exists(self, project_ref: str) -> bool:
        pid = _as_uuid(project_ref)
        if pid is None:
            return False
        return self._s.get(Project, pid) is not None

    def _latest_diagnosis_rounds(self, pid) -> dict:
        """逐条目最新一轮诊断（含 verdict 才算有诊断结论；已失效轮次不计）。"""
        latest: dict = {}
        for r in self._s.scalars(
            select(ItemDiagnosisRound).where(ItemDiagnosisRound.project_id == pid)
        ).all():
            # 已失效轮次须在取 max 之前剔除，否则"最新轮失效但存在更早有效轮"的条目会误丢徽标；
            # 系统自己宣告作废的轮次不得再被计作已诊断/质量告警（与评审页读侧口径对齐）
            if r.invalidated:
                continue
            cur = latest.get(r.item_ref)
            if cur is None or (r.round_no or 0) > (cur.round_no or 0):
                latest[r.item_ref] = r
        return {ref: r for ref, r in latest.items() if r.verdict_kind}

    @staticmethod
    def _quality_meta_of(round_) -> dict:
        if not round_.quality_meta:
            return {}
        try:
            return json.loads(round_.quality_meta) or {}
        except (ValueError, TypeError):
            return {}

    def _round_finding_severities(self, round_) -> list[str]:
        """最新轮发现项严重度序列（与质量端点同口径）：每条发现项按 finding_ref 配回
        quality_meta.findings 取 severity，缺省 medium；排除 no_blocker。

        不能按下标 zip：同事务写入的发现项 created_at 全相同，读侧 (created_at, id)
        排序退化为随机 UUID 序，与写入序无关。下标配对会让一份元数据被张冠李戴，且
        跳过 no_blocker 用的是行下标，读出序一变严重度就算错（与 _project_round 同口径）。
        存量轮次的元数据不带 finding_ref，整轮无引用时才回退旧的下标配对。"""
        fmeta = self._quality_meta_of(round_).get("findings") or []
        meta_by_ref = {
            str(m.get("finding_ref")): m
            for m in fmeta
            if isinstance(m, dict) and m.get("finding_ref")
        }
        severities: list[str] = []
        rows = self._s.scalars(
            select(ItemReviewFinding)
            .where(ItemReviewFinding.round_ref == round_.id)
            .order_by(ItemReviewFinding.created_at, ItemReviewFinding.id)
        ).all()
        for i, f in enumerate(rows):
            if f.finding_type == "no_blocker":
                continue
            meta = meta_by_ref.get(str(f.id))
            if meta is None:
                # 存量轮次（整轮无 finding_ref）回退下标配对，与改前行为一致
                meta = fmeta[i] if not meta_by_ref and i < len(fmeta) and isinstance(fmeta[i], dict) else {}
            sev = meta.get("severity")
            severities.append(sev if sev in ("high", "medium", "low") else "medium")
        return severities

    def quality_alert_summary(self, project_ref: str) -> dict:
        """质量告警聚合（KPI）：按各条目最新一轮诊断 quality_meta 的发现项严重度计数。

        只统计有诊断轮次（含 verdict）的条目（diagnosed_items）；未诊断不计入告警，不造假。
        派生投影，可整层重算（v2 需求管理工作台 04 篇 §1.1）。
        """
        pid = _as_uuid(project_ref)
        summary = {"high": 0, "medium": 0, "low": 0, "diagnosed_items": 0}
        if pid is None:
            return summary
        for r in self._latest_diagnosis_rounds(pid).values():
            summary["diagnosed_items"] += 1
            for sev in self._round_finding_severities(r):
                summary[sev] += 1
        return summary

    def item_quality_index(self, project_ref: str) -> dict:
        """逐条目质量索引（维护列表 Q 徽标）：{item_ref: {"score": int|None, "alert": str|None}}。

        score = 最新轮 quality_meta.quality_profile.overall（缺画像为 None，不伪造）；
        alert = 最新轮发现项最重严重度（排除 no_blocker；无发现项为 None）。
        """
        pid = _as_uuid(project_ref)
        if pid is None:
            return {}
        order = {"high": 0, "medium": 1, "low": 2}
        index: dict = {}
        for ref, r in self._latest_diagnosis_rounds(pid).items():
            meta = self._quality_meta_of(r)  # ref 为 UUID，索引键回字符串（与资产投影 id 对齐）
            profile = meta.get("quality_profile") or {}
            overall = profile.get("overall") if isinstance(profile, dict) else None
            score = int(overall) if isinstance(overall, (int, float)) else None
            worst = None
            for sev in self._round_finding_severities(r):
                if worst is None or order[sev] < order[worst]:
                    worst = sev
            index[str(ref)] = {"score": score, "alert": worst}
        return index

    def load(self, project_ref: str) -> ProjectAssetFacts:
        pid = _as_uuid(project_ref)
        if pid is None:
            return ProjectAssetFacts([], [], [], [], [], [], [], [], [], [], [])

        materials = [
            MaterialAsset(
                id=str(m.id),
                source_note=(m.source_note or "").strip(),
                raw_text_head=_head(m.raw_text),
                created_at=m.created_at,
            )
            for m in self._s.scalars(select(Material).where(Material.project_id == pid)).all()
        ]
        intake_records = [
            IntakeRecordFact(
                material_ref=str(r.material_ref) if r.material_ref else None,
                intake_conclusion=r.intake_conclusion,
                created_at=r.created_at,
            )
            for r in self._s.scalars(
                select(IntakeRecord).where(IntakeRecord.project_id == pid)
            ).all()
        ]
        parse_results = [
            ParseResultFact(id=str(r.id), material_ref=str(r.material_ref))
            for r in self._s.scalars(
                select(MaterialParseResult).where(MaterialParseResult.project_id == pid)
            ).all()
        ]
        # 每要素登记归并次数（action="merge"）→ 锚点数 = 1 + merge 次数（P3 §2.1 选型 B）。
        merge_counts = dict(
            self._s.execute(
                select(ElementHistory.element_ref, func.count())
                .where(ElementHistory.project_id == pid, ElementHistory.action == "merge")
                .group_by(ElementHistory.element_ref)
            ).all()
        )
        elements = [
            ElementAsset(
                id=str(e.id),
                parse_result_ref=str(e.parse_result_ref),
                element_type=e.element_type,
                content=_head(e.content, 400),
                process_status=e.process_status,
                superseded=bool(e.superseded),
                updated_at=e.updated_at,
                anchor_count=1 + int(merge_counts.get(e.id, 0)),
            )
            for e in self._s.scalars(
                select(RequirementElement).where(RequirementElement.project_id == pid)
            ).all()
        ]
        items = [
            ItemAsset(
                id=str(i.id),
                req_no=i.req_no,
                expression=_head(i.expression, 800),
                req_type=i.req_type,
                status=i.status,
                source_element_refs=tuple(_refs(i.source_element_refs)),
                parse_result_ref=str(i.parse_result_ref),
                updated_at=i.updated_at,
                verification_method=i.verification_method,
                verification_note=i.verification_note,
                priority=i.priority,
            )
            for i in self._s.scalars(
                select(RequirementItem).where(RequirementItem.project_id == pid)
            ).all()
        ]
        item_ids = [uuid.UUID(i.id) for i in items]
        item_revisions = (
            [
                ItemRevisionFact(
                    item_ref=str(r.item_ref),
                    field_key=r.field_key,
                    before_value=_head(r.before_value, 400),
                    after_value=_head(r.after_value, 400),
                    revision_mode=r.revision_mode,
                    reason=r.reason,
                    operator_ref=r.operator_ref,
                    created_at=r.created_at,
                )
                for r in self._s.scalars(
                    select(RequirementItemRevision)
                    .where(RequirementItemRevision.item_ref.in_(item_ids))
                    .order_by(RequirementItemRevision.created_at)
                ).all()
            ]
            if item_ids
            else []
        )
        charts = [
            ChartAsset(
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
            DocumentAsset(
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
        doc_ids = [uuid.UUID(d.id) for d in documents]
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
            TraceLinkAsset(
                id=str(t.id),
                upstream_type=t.upstream_type,
                upstream_ref=str(t.upstream_ref),
                downstream_type=t.downstream_type,
                downstream_ref=str(t.downstream_ref),
                status=t.status,
                status_reason=t.status_reason,
                updated_at=t.updated_at,
            )
            for t in self._s.scalars(select(TraceLink).where(TraceLink.project_id == pid)).all()
        ]
        issues = [
            IssueAsset(
                id=str(x.id),
                issue_type=x.issue_type,
                status=x.status,
                title=x.title,
                origin_kind=x.origin_kind,
                created_at=x.created_at,
            )
            for x in self._s.scalars(select(Issue).where(Issue.project_id == pid)).all()
        ]
        return ProjectAssetFacts(
            materials=materials,
            intake_records=intake_records,
            parse_results=parse_results,
            elements=elements,
            items=items,
            item_revisions=item_revisions,
            charts=charts,
            documents=documents,
            doc_index_entries=doc_index_entries,
            trace_links=trace_links,
            issues=issues,
        )
