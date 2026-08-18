"""需求资产目录服务·资产读侧（04A §3.1 需求管理工作台资产维护视图 + §5 资产口径）。

只读投影：资产树/资产详情/需求条目维护列表都是既有事实源的派生视图，可整层重建，
不形成第二份事实源（UINV-09/22）。展示标签映射归前端；本服务只输出稳定码与事实文本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.api.schemas import (
    AssetAttributeRead,
    AssetCatalogRead,
    AssetDetailRead,
    AssetGroupRead,
    AssetNodeRead,
    AssetRelationRead,
    AssetTraceSummaryRead,
    BusinessKnowledgeListRead,
    BusinessKnowledgeRowRead,
    ItemMaintenanceCardRead,
    ItemMaintenanceItemRead,
    ItemMaintenanceListRead,
    ItemRelatedCountsRead,
    ItemRevisionRead,
    ItemSourceEvidenceRead,
    QualityAlertSummaryRead,
)
from app.domain.enums import KnowledgeCategory, knowledge_category_of
from app.domain.errors import InvalidInput, NotFound
from app.domain.labels import NON_REVISION_FIELD_KEYS
from app.repositories.asset_read import AssetReadRepository, ProjectAssetFacts
from app.services.item_formation import split_verification_methods

_ASSET_TYPES = ("material", "element", "requirement_item", "chart", "trace_link", "document", "issue")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _counts_as_revision(r) -> bool:
    """「修订次数」计数口径：真实字段修订才算。人工确认背书借表落库但没改任何字段
    （NON_REVISION_FIELD_KEYS），不算；拒绝建议等 before==after 的无变更留痕也不算。
    详情属性「revisions」与维护列表 revision_count 共用本口径。"""
    return r.before_value != r.after_value and r.field_key not in NON_REVISION_FIELD_KEYS


def _material_label(note: str, head: str) -> str:
    # 与追溯节点回退口径一致（2026-07-12 卡片语义修正）：目录树无边上下文，恒原文头优先；
    # source_note 是接入自动拼装的元数据串，仅在原文为空时兜底。
    return head or note or "（未命名材料）"


class AssetCatalogService:
    """资产目录读服务：目录树 / 资产详情 / 维护列表 / 条目卡片。"""

    def __init__(self, repo: AssetReadRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # 目录树
    # ------------------------------------------------------------------

    def read_catalog(self, project_ref: str) -> AssetCatalogRead:
        facts = self._require(project_ref)
        material_label = {m.id: _material_label(m.source_note, m.raw_text_head) for m in facts.materials}
        item_label = {i.id: f"{i.req_no} {i.expression}" for i in facts.items}
        chart_label = {c.id: c.title for c in facts.charts}
        element_label = {e.id: e.content for e in facts.elements}
        document_label = {d.id: d.title for d in facts.documents}

        def link_endpoint(node_type: str, ref: str) -> str:
            label = {
                "material": material_label,
                "element": element_label,
                "requirement_item": item_label,
                "chart": chart_label,
                "document": document_label,
            }.get(node_type, {}).get(ref, ref[:8])
            return label[:24]

        groups = [
            AssetGroupRead(
                asset_type="material",
                count=len(facts.materials),
                nodes=[
                    AssetNodeRead(
                        ref=m.id, label=_material_label(m.source_note, m.raw_text_head)[:60],
                        sub_label=None, status=self._intake_status(facts, m.id),
                        updated_at=_iso(m.created_at),
                    )
                    for m in sorted(facts.materials, key=lambda m: m.created_at, reverse=True)
                ],
            ),
            AssetGroupRead(
                asset_type="element",
                count=len(facts.elements),
                nodes=[
                    AssetNodeRead(
                        ref=e.id, label=e.content[:60], sub_label=e.element_type,
                        status=e.process_status, updated_at=_iso(e.updated_at),
                    )
                    for e in sorted(facts.elements, key=lambda e: e.updated_at, reverse=True)
                    if not e.superseded
                ],
            ),
            AssetGroupRead(
                asset_type="requirement_item",
                count=len(facts.items),
                nodes=[
                    AssetNodeRead(
                        ref=i.id, label=f"{i.req_no} {i.expression}"[:60], sub_label=i.req_type,
                        status=i.status, updated_at=_iso(i.updated_at),
                    )
                    for i in sorted(facts.items, key=lambda i: i.req_no)
                ],
            ),
            AssetGroupRead(
                asset_type="chart",
                count=len(facts.charts),
                nodes=[
                    AssetNodeRead(
                        ref=c.id, label=c.title[:60], sub_label=c.chart_type,
                        status=c.status, updated_at=_iso(c.updated_at),
                    )
                    for c in sorted(facts.charts, key=lambda c: c.updated_at, reverse=True)
                ],
            ),
            AssetGroupRead(
                asset_type="trace_link",
                count=len(facts.trace_links),
                nodes=[
                    AssetNodeRead(
                        ref=t.id,
                        label=f"{link_endpoint(t.upstream_type, t.upstream_ref)} → "
                              f"{link_endpoint(t.downstream_type, t.downstream_ref)}",
                        sub_label=None, status=t.status, updated_at=_iso(t.updated_at),
                    )
                    for t in sorted(facts.trace_links, key=lambda t: t.updated_at, reverse=True)
                ],
            ),
            AssetGroupRead(
                asset_type="document",
                count=len(facts.documents),
                nodes=[
                    AssetNodeRead(
                        ref=d.id, label=d.title[:60], sub_label=f"索引 v{d.index_version}",
                        status=d.status, updated_at=_iso(d.updated_at),
                    )
                    for d in facts.documents
                ],
            ),
            AssetGroupRead(
                asset_type="issue",
                count=len(facts.issues),
                nodes=[
                    AssetNodeRead(
                        ref=x.id, label=x.title[:60], sub_label=x.issue_type,
                        status=x.status, updated_at=_iso(x.created_at),
                    )
                    for x in sorted(facts.issues, key=lambda x: x.created_at, reverse=True)
                ],
            ),
        ]
        by_status = {"effective": 0, "pre_established": 0, "suspect_pending_review": 0, "invalid": 0}
        for t in facts.trace_links:
            if t.status in by_status:
                by_status[t.status] += 1
        return AssetCatalogRead(
            project_ref=project_ref,
            groups=groups,
            trace_summary=AssetTraceSummaryRead(
                effective=by_status["effective"],
                pre_established=by_status["pre_established"],
                suspect=by_status["suspect_pending_review"],
                invalid=by_status["invalid"],
            ),
            quality_alert_summary=QualityAlertSummaryRead(
                **self._repo.quality_alert_summary(project_ref)
            ),
        )

    # ------------------------------------------------------------------
    # 资产详情
    # ------------------------------------------------------------------

    def read_asset_detail(self, project_ref: str, asset_type: str, ref: str) -> AssetDetailRead:
        if asset_type not in _ASSET_TYPES:
            raise NotFound(f"未知资产类型：{asset_type}")
        facts = self._require(project_ref)
        reader = {
            "material": self._material_detail,
            "element": self._element_detail,
            "requirement_item": self._item_detail,
            "chart": self._chart_detail,
            "trace_link": self._link_detail,
            "document": self._document_detail,
            "issue": self._issue_detail,
        }[asset_type]
        detail = reader(facts, ref)
        if detail is None:
            raise NotFound(f"资产不存在：{asset_type}/{ref}")
        return detail

    def _intake_status(self, facts: ProjectAssetFacts, material_ref: str) -> Optional[str]:
        for r in facts.intake_records:
            if r.material_ref == material_ref:
                return r.intake_conclusion
        return None

    def _item_trace(self, facts: ProjectAssetFacts, item_ref: str) -> tuple[int, int]:
        effective = sum(
            1 for t in facts.trace_links
            if t.status == "effective" and item_ref in (t.upstream_ref, t.downstream_ref)
        )
        suspect = sum(
            1 for t in facts.trace_links
            if t.status == "suspect_pending_review" and item_ref in (t.upstream_ref, t.downstream_ref)
        )
        return effective, suspect

    def _material_detail(self, facts: ProjectAssetFacts, ref: str) -> Optional[AssetDetailRead]:
        m = next((x for x in facts.materials if x.id == ref), None)
        if m is None:
            return None
        parse_refs = {p.id for p in facts.parse_results if p.material_ref == ref}
        derived_elements = [e for e in facts.elements if e.parse_result_ref in parse_refs and not e.superseded]
        return AssetDetailRead(
            asset_type="material", ref=ref,
            label=_material_label(m.source_note, m.raw_text_head),
            sub_label=None, status=self._intake_status(facts, ref),
            summary=m.raw_text_head,
            attributes=[
                AssetAttributeRead(key="source_note", value=m.source_note or "—"),
                AssetAttributeRead(key="created_at", value=_iso(m.created_at) or "—"),
                AssetAttributeRead(key="derived_elements", value=str(len(derived_elements))),
            ],
            relations=[
                AssetRelationRead(kind="derived_element", asset_type="element", ref=e.id, label=e.content[:40])
                for e in derived_elements[:10]
            ],
        )

    def _element_detail(self, facts: ProjectAssetFacts, ref: str) -> Optional[AssetDetailRead]:
        e = next((x for x in facts.elements if x.id == ref), None)
        if e is None:
            return None
        material_ref = next((p.material_ref for p in facts.parse_results if p.id == e.parse_result_ref), None)
        material = next((m for m in facts.materials if m.id == material_ref), None)
        referencing = [i for i in facts.items if ref in i.source_element_refs]
        relations = []
        if material:
            relations.append(AssetRelationRead(
                kind="source_material", asset_type="material", ref=material.id,
                label=_material_label(material.source_note, material.raw_text_head)[:40],
            ))
        relations.extend(
            AssetRelationRead(kind="referenced_by_item", asset_type="requirement_item", ref=i.id,
                              label=f"{i.req_no} {i.expression}"[:40])
            for i in referencing[:10]
        )
        return AssetDetailRead(
            asset_type="element", ref=ref, label=e.content[:60],
            sub_label=e.element_type, status=e.process_status, summary=e.content,
            attributes=[
                AssetAttributeRead(key="element_type", value=e.element_type),
                AssetAttributeRead(key="referenced_by", value=str(len(referencing))),
                AssetAttributeRead(key="updated_at", value=_iso(e.updated_at) or "—"),
            ],
            relations=relations,
        )

    def _item_detail(self, facts: ProjectAssetFacts, ref: str) -> Optional[AssetDetailRead]:
        i = next((x for x in facts.items if x.id == ref), None)
        if i is None:
            return None
        charts = [c for c in facts.charts if ref in c.source_refs]
        indexed = any(en.asset_ref == ref for en in facts.doc_index_entries)
        revisions = [r for r in facts.item_revisions if r.item_ref == ref and _counts_as_revision(r)]
        effective, suspect = self._item_trace(facts, ref)
        return AssetDetailRead(
            asset_type="requirement_item", ref=ref, label=f"{i.req_no} {i.expression}"[:60],
            sub_label=i.req_type, status=i.status, summary=i.expression,
            attributes=[
                AssetAttributeRead(key="req_no", value=i.req_no),
                AssetAttributeRead(key="req_type", value=i.req_type),
                AssetAttributeRead(key="source_elements", value=str(len(i.source_element_refs))),
                AssetAttributeRead(key="chart_coverage", value=str(len(charts))),
                AssetAttributeRead(key="in_document_index", value="yes" if indexed else "no"),
                AssetAttributeRead(key="revisions", value=str(len(revisions))),
                AssetAttributeRead(key="trace_effective", value=str(effective)),
                AssetAttributeRead(key="trace_suspect", value=str(suspect)),
                AssetAttributeRead(key="updated_at", value=_iso(i.updated_at) or "—"),
            ],
            relations=[
                AssetRelationRead(kind="covered_by_chart", asset_type="chart", ref=c.id, label=c.title[:40])
                for c in charts[:10]
            ],
        )

    def _chart_detail(self, facts: ProjectAssetFacts, ref: str) -> Optional[AssetDetailRead]:
        c = next((x for x in facts.charts if x.id == ref), None)
        if c is None:
            return None
        item_label = {i.id: f"{i.req_no} {i.expression}" for i in facts.items}
        return AssetDetailRead(
            asset_type="chart", ref=ref, label=c.title, sub_label=c.chart_type,
            status=c.status, summary=c.title,
            attributes=[
                AssetAttributeRead(key="chart_type", value=c.chart_type),
                AssetAttributeRead(key="covered_items", value=str(len(c.source_refs))),
                AssetAttributeRead(key="updated_at", value=_iso(c.updated_at) or "—"),
            ],
            relations=[
                AssetRelationRead(kind="covers_item", asset_type="requirement_item", ref=r,
                                  label=item_label.get(r, r[:8])[:40])
                for r in c.source_refs[:10]
            ],
        )

    def _link_detail(self, facts: ProjectAssetFacts, ref: str) -> Optional[AssetDetailRead]:
        t = next((x for x in facts.trace_links if x.id == ref), None)
        if t is None:
            return None
        item_label = {i.id: f"{i.req_no} {i.expression}" for i in facts.items}
        chart_label = {c.id: c.title for c in facts.charts}

        def label_of(node_type: str, node_ref: str) -> str:
            return {
                "requirement_item": item_label,
                "chart": chart_label,
            }.get(node_type, {}).get(node_ref, node_ref[:8])

        up = label_of(t.upstream_type, t.upstream_ref)
        down = label_of(t.downstream_type, t.downstream_ref)
        return AssetDetailRead(
            asset_type="trace_link", ref=ref, label=f"{up[:24]} → {down[:24]}",
            sub_label=None, status=t.status, summary=t.status_reason or "",
            attributes=[
                AssetAttributeRead(key="upstream", value=up[:60]),
                AssetAttributeRead(key="downstream", value=down[:60]),
                AssetAttributeRead(key="status_reason", value=t.status_reason or "—"),
                AssetAttributeRead(key="updated_at", value=_iso(t.updated_at) or "—"),
            ],
            relations=[
                AssetRelationRead(kind="upstream", asset_type=t.upstream_type, ref=t.upstream_ref, label=up[:40]),
                AssetRelationRead(kind="downstream", asset_type=t.downstream_type, ref=t.downstream_ref, label=down[:40]),
            ],
        )

    def _document_detail(self, facts: ProjectAssetFacts, ref: str) -> Optional[AssetDetailRead]:
        d = next((x for x in facts.documents if x.id == ref), None)
        if d is None:
            return None
        entries = [en for en in facts.doc_index_entries
                   if en.document_ref == ref and en.index_version == d.index_version]
        return AssetDetailRead(
            asset_type="document", ref=ref, label=d.title, sub_label=f"索引 v{d.index_version}",
            status=d.status, summary=d.title,
            attributes=[
                AssetAttributeRead(key="index_version", value=f"v{d.index_version}"),
                AssetAttributeRead(key="index_entries", value=str(len(entries))),
                AssetAttributeRead(key="updated_at", value=_iso(d.updated_at) or "—"),
            ],
            relations=[],
        )

    def _issue_detail(self, facts: ProjectAssetFacts, ref: str) -> Optional[AssetDetailRead]:
        x = next((i for i in facts.issues if i.id == ref), None)
        if x is None:
            return None
        return AssetDetailRead(
            asset_type="issue", ref=ref, label=x.title, sub_label=x.issue_type,
            status=x.status, summary=x.title,
            attributes=[
                AssetAttributeRead(key="issue_type", value=x.issue_type),
                AssetAttributeRead(key="origin_kind", value=x.origin_kind),
                AssetAttributeRead(key="created_at", value=_iso(x.created_at) or "—"),
            ],
            relations=[],
        )

    # ------------------------------------------------------------------
    # 需求条目维护列表 / 条目卡片（04A §3.1 默认维护视图）
    # ------------------------------------------------------------------

    def list_requirement_items(
        self,
        project_ref: str,
        status: Optional[str] = None,
        req_type: Optional[str] = None,
        search: Optional[str] = None,
        gap: Optional[str] = None,
    ) -> ItemMaintenanceListRead:
        """维护列表；gap=verification_note/priority 筛出属性缺失条目（29148 属性补齐；
        缺失仅警示不硬卡，此筛选是评审 supplement 缺口回路的工作面）。"""
        facts = self._require(project_ref)
        revision_counts: dict[str, int] = {}
        for r in facts.item_revisions:
            if _counts_as_revision(r):
                revision_counts[r.item_ref] = revision_counts.get(r.item_ref, 0) + 1
        quality_index = self._repo.item_quality_index(project_ref)
        items = []
        needle = (search or "").strip().lower()
        for i in sorted(facts.items, key=lambda x: x.req_no):
            if status and i.status != status:
                continue
            if req_type and i.req_type != req_type:
                continue
            if needle and needle not in i.req_no.lower() and needle not in i.expression.lower():
                continue
            verification_missing = not (i.verification_note or "").strip()
            priority_missing = not (i.priority or "").strip()
            if gap == "verification_note" and not verification_missing:
                continue
            if gap == "priority" and not priority_missing:
                continue
            items.append(ItemMaintenanceItemRead(
                ref=i.id, req_no=i.req_no, expression=i.expression,
                req_type=i.req_type, status=i.status, updated_at=_iso(i.updated_at),
                source_count=len(i.source_element_refs),
                revision_count=revision_counts.get(i.id, 0),
                priority=i.priority,
                verification_missing=verification_missing,
                priority_missing=priority_missing,
                quality_score=(quality_index.get(i.id) or {}).get("score"),
                quality_alert=(quality_index.get(i.id) or {}).get("alert"),
            ))
        return ItemMaintenanceListRead(project_ref=project_ref, items=items, total=len(items))

    def list_business_knowledge(
        self,
        project_ref: str,
        element_type: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> BusinessKnowledgeListRead:
        """AEP-104 业务知识清单（05 §2）：只读列出业务领域知识翼要素。

        翼过滤由 ELEMENT_KNOWLEDGE_CATEGORY 派生（不字面列举）；element_type 过滤限业务翼成员。
        referenced_count 在 P4 支撑依据投影落地前恒 0。
        """
        facts = self._require(project_ref)
        if element_type and knowledge_category_of(element_type) != KnowledgeCategory.BUSINESS.value:
            raise InvalidInput(f"element_type={element_type} 不属于业务领域知识翼")
        needle = (search or "").strip().lower()
        rows: list[BusinessKnowledgeRowRead] = []
        # 默认按类型分组 + 更新时间倒序（05 §2）。
        ordered = sorted(facts.elements, key=lambda e: (e.element_type, e.updated_at), reverse=False)
        ordered.sort(key=lambda e: e.updated_at, reverse=True)
        ordered.sort(key=lambda e: e.element_type)
        for e in ordered:
            if e.superseded:
                continue
            if knowledge_category_of(e.element_type) != KnowledgeCategory.BUSINESS.value:
                continue
            if element_type and e.element_type != element_type:
                continue
            if status and e.process_status != status:
                continue
            if needle and needle not in e.content.lower():
                continue
            rows.append(BusinessKnowledgeRowRead(
                ref=e.id,
                element_type=e.element_type,
                knowledge_category=knowledge_category_of(e.element_type),
                content=e.content[:120],
                process_status=e.process_status,
                source_count=e.anchor_count,
                referenced_count=0,
                updated_at=_iso(e.updated_at),
            ))
        return BusinessKnowledgeListRead(project_ref=project_ref, items=rows, total=len(rows))

    def read_item_card(self, project_ref: str, item_ref: str) -> ItemMaintenanceCardRead:
        facts = self._require(project_ref)
        i = next((x for x in facts.items if x.id == item_ref), None)
        if i is None:
            raise NotFound(f"需求条目不存在：{item_ref}")
        material_label = {m.id: _material_label(m.source_note, m.raw_text_head) for m in facts.materials}
        parse_material = {p.id: p.material_ref for p in facts.parse_results}
        element_by_id = {e.id: e for e in facts.elements}
        evidence = []
        for ref in i.source_element_refs:
            e = element_by_id.get(ref)
            if e is None:
                continue
            evidence.append(ItemSourceEvidenceRead(
                element_ref=ref, element_type=e.element_type, content=e.content,
                material_label=material_label.get(parse_material.get(e.parse_result_ref, ""), None),
            ))
        revisions = [
            ItemRevisionRead(
                field_key=r.field_key, before_value=r.before_value, after_value=r.after_value,
                revision_mode=r.revision_mode, reason=r.reason,
                operator_ref=r.operator_ref, created_at=_iso(r.created_at) or "",
            )
            for r in facts.item_revisions
            if r.item_ref == item_ref
        ]
        charts = [c for c in facts.charts if item_ref in c.source_refs]
        indexed = any(en.asset_ref == item_ref for en in facts.doc_index_entries)
        effective, suspect = self._item_trace(facts, item_ref)
        return ItemMaintenanceCardRead(
            ref=i.id, req_no=i.req_no, expression=i.expression,
            req_type=i.req_type, status=i.status, updated_at=_iso(i.updated_at),
            verification_method=split_verification_methods(i.verification_method),
            verification_note=i.verification_note, priority=i.priority,
            source_evidence=evidence, revisions=revisions,
            related=ItemRelatedCountsRead(
                charts=len(charts), documents=1 if indexed else 0,
                trace_effective=effective, trace_suspect=suspect,
            ),
        )

    # ------------------------------------------------------------------

    def _require(self, project_ref: str) -> ProjectAssetFacts:
        if not self._repo.project_exists(project_ref):
            raise NotFound(f"项目不存在：{project_ref}")
        return self._repo.load(project_ref)
