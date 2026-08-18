"""文档资产仓储（LDM-014）+ 发布候选资产只读查询（SCN-005）。

只承载持久化读写；准入裁定在 服务/文档编排规则（services/publication.py）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.db.models import (
    DocumentIndexEntry,
    DocxExport,
    Material,
    MaterialParseResult,
    MarkdownDraft,
    MarkdownPatch,
    ReleaseBaseline,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    SectionManuscript,
    TemplateRegistry,
    TraceLink,
)


def _as_uuid(ref: Optional[str]) -> Optional[uuid.UUID]:
    if ref is None:
        return None
    return uuid.UUID(str(ref))


class SqlPublicationRepository:
    """LDM-014 文档资产仓储（文档/索引条目/Markdown 稿/补丁/导出件/基线）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        """供跨仓储写同事务记录（如导出失败落通知），与主事实同 commit。"""
        return self._session

    def commit(self) -> None:
        """落库后再入队铁律（同 model_orchestration）：worker 独立 session，读不到未提交事实。"""
        self._session.commit()

    # ---- LDM-014 需求文档 ----

    def get_document(self, project_ref: str, doc_type: str = "srs") -> Optional[RequirementDocument]:
        stmt = select(RequirementDocument).where(
            RequirementDocument.project_id == _as_uuid(project_ref),
            RequirementDocument.doc_type == doc_type,
        )
        return self._session.scalars(stmt).first()

    def get_document_by_ref(self, document_ref: str) -> Optional[RequirementDocument]:
        return self._session.get(RequirementDocument, _as_uuid(document_ref))

    def create_document(
        self, project_ref: str, template_id: str,
        title: str, status: str, coverage_scope: Optional[str],
    ) -> RequirementDocument:
        doc = RequirementDocument(
            project_id=_as_uuid(project_ref), doc_type="srs", title=title,
            template_id=_as_uuid(template_id),
            coverage_scope=coverage_scope, status=status,
        )
        self._session.add(doc)
        self._session.flush()
        return doc

    # ---- 文档内容索引条目 ----

    def entries_of(self, document_ref: str, index_version: int) -> list[DocumentIndexEntry]:
        stmt = (
            select(DocumentIndexEntry)
            .where(
                DocumentIndexEntry.document_ref == _as_uuid(document_ref),
                DocumentIndexEntry.index_version == index_version,
            )
            .order_by(DocumentIndexEntry.section_key, DocumentIndexEntry.order_no)
        )
        return list(self._session.scalars(stmt).all())

    def write_index_entries(
        self, document_ref: str, index_version: int,
        entries: Sequence[dict],
    ) -> None:
        """写入新索引版本的全部条目（旧版本条目保留为历史）。"""
        for e in entries:
            self._session.add(DocumentIndexEntry(
                document_ref=_as_uuid(document_ref), index_version=index_version,
                section_key=e["section_key"], asset_type=e["asset_type"],
                asset_ref=_as_uuid(e.get("asset_ref")),
                asset_version=str(e.get("asset_version", "1")),
                order_no=int(e.get("order_no", 0)),
            ))
        self._session.flush()

    # ---- 章节撰稿（AEP-098） ----

    def manuscripts_of(self, document_ref: str) -> list[SectionManuscript]:
        stmt = (
            select(SectionManuscript)
            .where(SectionManuscript.document_ref == _as_uuid(document_ref))
            .order_by(SectionManuscript.section_key)
        )
        return list(self._session.scalars(stmt).all())

    def get_manuscript(self, document_ref: str, section_key: str) -> Optional[SectionManuscript]:
        stmt = select(SectionManuscript).where(
            SectionManuscript.document_ref == _as_uuid(document_ref),
            SectionManuscript.section_key == section_key,
        )
        return self._session.scalars(stmt).first()

    def upsert_manuscript(
        self, document_ref: str, section_key: str, content: str, operator_ref: str,
    ) -> SectionManuscript:
        row = self.get_manuscript(document_ref, section_key)
        if row is None:
            row = SectionManuscript(
                document_ref=_as_uuid(document_ref), section_key=section_key,
                content=content, revision_no=1, updated_by=operator_ref,
            )
            self._session.add(row)
        else:
            row.content = content
            row.revision_no += 1
            row.updated_by = operator_ref
        self._session.flush()
        return row

    def delete_manuscript(self, document_ref: str, section_key: str) -> bool:
        row = self.get_manuscript(document_ref, section_key)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    # ---- Markdown 中间稿 / 定稿 ----

    def latest_draft(self, document_ref: str) -> Optional[MarkdownDraft]:
        stmt = (
            select(MarkdownDraft)
            .where(MarkdownDraft.document_ref == _as_uuid(document_ref))
            .order_by(MarkdownDraft.version_no.desc())
        )
        return self._session.scalars(stmt).first()

    def get_draft(self, draft_ref: str) -> Optional[MarkdownDraft]:
        return self._session.get(MarkdownDraft, _as_uuid(draft_ref))

    def create_draft(
        self, document_ref: str, index_version: int, content: str, bindings_json: str,
    ) -> MarkdownDraft:
        prev = self.latest_draft(document_ref)
        version = 1 if prev is None else prev.version_no + 1
        if prev is not None and prev.status in ("draft", "awaiting_item_revision"):
            prev.status = "superseded"
            self.discard_pending_patches(str(prev.id), note="重新生成，编辑补丁随旧稿丢弃")
        draft = MarkdownDraft(
            document_ref=_as_uuid(document_ref), version_no=version,
            index_version=index_version, content=content, generated_content=content,
            source_bindings=bindings_json, status="draft", can_export=False,
        )
        self._session.add(draft)
        self._session.flush()
        return draft

    def supersede_open_drafts(self, document_ref: str) -> None:
        """索引调整后，未定稿/待收束的稿一律标记需重新生成。"""
        stmt = select(MarkdownDraft).where(
            MarkdownDraft.document_ref == _as_uuid(document_ref),
            MarkdownDraft.status.in_(("draft", "awaiting_item_revision", "finalized")),
        )
        for d in self._session.scalars(stmt).all():
            d.status = "superseded"
            d.can_export = False
            self.discard_pending_patches(str(d.id), note="索引已调整，需重新生成")
        self._session.flush()

    # ---- 预览编辑补丁 ----

    def pending_patches(self, draft_ref: str) -> list[MarkdownPatch]:
        stmt = (
            select(MarkdownPatch)
            .where(MarkdownPatch.draft_ref == _as_uuid(draft_ref), MarkdownPatch.status == "pending")
            .order_by(MarkdownPatch.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def discard_pending_patches(self, draft_ref: str, note: str) -> None:
        for p in self.pending_patches(draft_ref):
            p.status = "discarded"
            p.note = note
        self._session.flush()

    def add_patch(
        self, draft_ref: str, impact: str, before_text: str, after_text: str,
        bound_item_ref: Optional[str], operator_ref: str, note: Optional[str] = None,
    ) -> MarkdownPatch:
        patch = MarkdownPatch(
            draft_ref=_as_uuid(draft_ref), impact=impact,
            before_text=before_text, after_text=after_text,
            bound_item_ref=_as_uuid(bound_item_ref), operator_ref=operator_ref, note=note,
        )
        self._session.add(patch)
        self._session.flush()
        return patch

    # ---- 候选 docx 导出件 ----

    def find_export_by_idempotency(self, key: str) -> Optional[DocxExport]:
        stmt = select(DocxExport).where(DocxExport.idempotency_key == key)
        return self._session.scalars(stmt).first()

    def find_active_conversion(self, draft_ref: str) -> Optional[DocxExport]:
        """同一定稿版本是否已有在途（converting）导出：用于生成前防重复入队。"""
        stmt = (
            select(DocxExport)
            .where(
                DocxExport.draft_ref == _as_uuid(draft_ref),
                DocxExport.status == "converting",
            )
            .order_by(DocxExport.created_at.desc())
        )
        return self._session.scalars(stmt).first()

    def create_export(
        self, document_ref: str, draft_ref: str, status: str,
        operator_ref: str, idempotency_key: str,
        manual_fallback: bool = False, failure_reason: Optional[str] = None,
    ) -> DocxExport:
        export = DocxExport(
            document_ref=_as_uuid(document_ref), draft_ref=_as_uuid(draft_ref),
            status=status, operator_ref=operator_ref, idempotency_key=idempotency_key,
            manual_fallback=manual_fallback, failure_reason=failure_reason,
        )
        self._session.add(export)
        self._session.flush()
        return export

    def get_export(self, export_ref: str) -> Optional[DocxExport]:
        return self._session.get(DocxExport, _as_uuid(export_ref))

    def exports_of(self, document_ref: str) -> list[DocxExport]:
        stmt = (
            select(DocxExport)
            .where(DocxExport.document_ref == _as_uuid(document_ref))
            .order_by(DocxExport.created_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def has_failed_export(self, draft_ref: str) -> bool:
        stmt = select(DocxExport).where(
            DocxExport.draft_ref == _as_uuid(draft_ref), DocxExport.status == "failed",
        )
        return self._session.scalars(stmt).first() is not None

    # ---- 发布基线快照 ----

    def create_baseline(
        self, document_ref: str, index_version: int, draft_ref: str,
        template_id: str, export_ref: str, manual_fallback: bool,
        asset_refs_json: str, confirmed_by: str, note: Optional[str],
    ) -> ReleaseBaseline:
        baseline = ReleaseBaseline(
            document_ref=_as_uuid(document_ref), index_version=index_version,
            draft_ref=_as_uuid(draft_ref), template_id=_as_uuid(template_id),
            export_ref=_as_uuid(export_ref),
            manual_fallback=manual_fallback, asset_refs=asset_refs_json,
            confirmed_by=confirmed_by, note=note,
        )
        self._session.add(baseline)
        self._session.flush()
        return baseline

    def baseline_of(self, document_ref: str) -> Optional[ReleaseBaseline]:
        stmt = (
            select(ReleaseBaseline)
            .where(ReleaseBaseline.document_ref == _as_uuid(document_ref))
            .order_by(ReleaseBaseline.created_at.desc())
        )
        return self._session.scalars(stmt).first()

    def get_baseline(self, baseline_ref: str) -> Optional[ReleaseBaseline]:
        return self._session.get(ReleaseBaseline, _as_uuid(baseline_ref))

    # ---- 候选资产只读查询（需求资产目录服务最小承接；候选≠许可）----

    def confirmed_items(self, project_ref: str) -> list[RequirementItem]:
        stmt = (
            select(RequirementItem)
            .where(
                RequirementItem.project_id == _as_uuid(project_ref),
                RequirementItem.status == "confirmed",
            )
            .order_by(RequirementItem.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def pending_item_count(self, project_ref: str) -> int:
        stmt = select(RequirementItem).where(
            RequirementItem.project_id == _as_uuid(project_ref),
            RequirementItem.status == "pending_confirmation",
        )
        return len(list(self._session.scalars(stmt).all()))

    def get_items(self, item_refs: Sequence[str]) -> list[RequirementItem]:
        if not item_refs:
            return []
        stmt = select(RequirementItem).where(
            RequirementItem.id.in_([_as_uuid(r) for r in item_refs])
        )
        return list(self._session.scalars(stmt).all())

    def get_item(self, item_ref: str) -> Optional[RequirementItem]:
        return self._session.get(RequirementItem, _as_uuid(item_ref))

    def create_reflow_item(self, source: RequirementItem, new_expression: str) -> RequirementItem:
        """确认态条目编辑回流：创建新的待确认 LDM-007（旧确认态不原地覆盖）。"""
        item = RequirementItem(
            project_id=source.project_id, parse_result_ref=source.parse_result_ref,
            formation_context_ref=source.formation_context_ref, req_no=source.req_no,
            expression=new_expression, req_type=source.req_type,
            status="pending_confirmation", version_no=source.version_no + 1,
            source_element_refs=source.source_element_refs,
            formation_basis_ref=source.formation_basis_ref,
        )
        self._session.add(item)
        self._session.flush()
        return item

    def confirm_item(self, item: RequirementItem) -> None:
        item.status = "confirmed"
        item.updated_at = datetime.now(timezone.utc)
        self._session.flush()

    def confirmed_charts(self, project_ref: str) -> list[RequirementChart]:
        """受控图表候选（仅 confirmed 可被文档编排消费；候选≠许可）。"""
        stmt = (
            select(RequirementChart)
            .where(
                RequirementChart.project_id == _as_uuid(project_ref),
                RequirementChart.status == "confirmed",
            )
            .order_by(RequirementChart.confirmed_at, RequirementChart.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def get_charts(self, refs: Sequence[str]) -> list[RequirementChart]:
        if not refs:
            return []
        stmt = select(RequirementChart).where(
            RequirementChart.id.in_([_as_uuid(r) for r in refs])
        )
        return list(self._session.scalars(stmt).all())

    def confirmed_business_elements(
        self, project_ref: str, types: Sequence[str]
    ) -> list[RequirementElement]:
        """确认态业务领域知识要素（P5 文档知识表整表投影用，只读；非替代态）。"""
        if not types:
            return []
        stmt = (
            select(RequirementElement)
            .where(
                RequirementElement.project_id == _as_uuid(project_ref),
                RequirementElement.element_type.in_(list(types)),
                RequirementElement.process_status == "confirmed",
                RequirementElement.superseded.is_(False),
            )
            .order_by(RequirementElement.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def elements_by_refs(self, refs: Sequence[str]) -> list[RequirementElement]:
        """条目来源要素（文档条目块"来源依据"渲染用，只读）。"""
        if not refs:
            return []
        stmt = select(RequirementElement).where(
            RequirementElement.id.in_([_as_uuid(r) for r in refs])
        )
        return list(self._session.scalars(stmt).all())

    def parse_results_by_refs(self, refs: Sequence[str]) -> list[MaterialParseResult]:
        if not refs:
            return []
        stmt = select(MaterialParseResult).where(
            MaterialParseResult.id.in_([_as_uuid(r) for r in refs])
        )
        return list(self._session.scalars(stmt).all())

    def effective_chart_titles_by_item(self, project_ref: str) -> dict[str, list[str]]:
        """条目 → 有效追溯覆盖它的受控图表标题（文档条目块"关联图表"渲染用）。"""
        stmt = (
            select(TraceLink.upstream_ref, RequirementChart.title)
            .join(RequirementChart, RequirementChart.id == TraceLink.downstream_ref)
            .where(
                TraceLink.project_id == _as_uuid(project_ref),
                TraceLink.relation_type == "chart",
                TraceLink.status == "effective",
                TraceLink.upstream_type == "requirement_item",
                TraceLink.downstream_type == "chart",
            )
            .order_by(RequirementChart.title)
        )
        result: dict[str, list[str]] = {}
        for upstream_ref, title in self._session.execute(stmt).all():
            result.setdefault(str(upstream_ref), []).append(title)
        return result

    def trace_link_status_counts(self, project_ref: str) -> dict[str, int]:
        """LDM-013 按状态计数（候选池追溯 tab 只读摘要；不构成入索引内容）。"""
        stmt = (
            select(TraceLink.status, func.count(TraceLink.id))
            .where(TraceLink.project_id == _as_uuid(project_ref))
            .group_by(TraceLink.status)
        )
        return {status: count for status, count in self._session.execute(stmt).all()}

    def latest_active_template(self, template_key: str) -> Optional[TemplateRegistry]:
        """注册表最新 active 版本（发布侧只读消费；登记/停用归模板注册表服务）。"""
        stmt = (
            select(TemplateRegistry)
            .where(TemplateRegistry.template_key == template_key, TemplateRegistry.status == "active")
            .order_by(TemplateRegistry.version_no.desc())
        )
        return self._session.scalars(stmt).first()

    def get_template_row(self, registry_ref: str) -> Optional[TemplateRegistry]:
        return self._session.get(TemplateRegistry, _as_uuid(registry_ref))

    def get_project_name(self, project_ref: str) -> Optional[str]:
        from app.db.models import Project

        project = self._session.get(Project, _as_uuid(project_ref))
        return project.name if project else None

    def materials(self, project_ref: str) -> list[Material]:
        stmt = (
            select(Material)
            .where(Material.project_id == _as_uuid(project_ref))
            .order_by(Material.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def get_materials(self, refs: Sequence[str]) -> list[Material]:
        if not refs:
            return []
        stmt = select(Material).where(Material.id.in_([_as_uuid(r) for r in refs]))
        return list(self._session.scalars(stmt).all())
