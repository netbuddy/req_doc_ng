"""追溯分析服务（TRC-001：AEP-058…AEP-066）。

只读投影 + 复核路由 + 转问题项（06B §3.12 / 页面设计 §2–§6）：
关系网、覆盖度、缺口、可疑清单都是从权威事实源派生的读模型，可整层重建；
本服务不建立、不删除、不改写追溯关系的成立事实——可疑链路复核结论交
追溯图谱模块按 `TRACE_TRANSITIONS` 迁移表重判（本版仅 sync-trace 恢复预建立
与维持可疑；「恢复有效」重判随 SCN-006，见页面设计 §7.4）。

边投影（页面设计 §2）：
  材料→要素、要素→条目、条目/图表→文档（当前索引版本）为结构派生边（derived）；
  条目→图表来自 LDM-013 四态照录（ldm013）；失效边默认不进窗口。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.api.schemas import (
    IssueRead,
    TraceAnchorGroupRead,
    TraceChainRead,
    TraceCountsRead,
    TraceCoverageDirectionRead,
    TraceCoverageRead,
    TraceEdgeRead,
    TraceEntryRead,
    TraceGapItemRead,
    TraceGapListRead,
    TraceIssueCommand,
    TraceLevelRead,
    TraceLinkRead,
    TraceNodeRead,
    TraceReviewCommand,
    TraceReviewResult,
    TraceSuspectListRead,
    SupportingBasisCommand,
    SupportingBasisResult,
)
from app.domain.enums import (
    ChartStatus,
    ElementProcessStatus,
    ITEMIZABLE_ELEMENT_TYPES,
    IssueType,
    KnowledgeCategory,
    RequirementItemStatus,
    TraceLinkStatus,
    TraceRelationType,
    knowledge_category_of,
)
from app.domain.naming import normalize_element_name, normalize_text
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.domain.state_machine import TraceEvent, TraceState, trace_transition
from app.interfaces.repositories import IssueRepository, IssueRow, TraceLinkRepository
from app.log import log_event
from app.repositories.trace_read import ProjectTraceFacts, TraceReadRepository

NODE_TYPES = ("material", "element", "requirement_item", "chart", "document")
DIRECTIONS = ("upstream", "downstream")
DEFAULT_DEPTH = 2
DEFAULT_LIMIT = 8
ANCHORS_PER_TYPE = 5

_LIVE_ITEM_STATUSES = {
    RequirementItemStatus.PENDING_CONFIRMATION.value,
    RequirementItemStatus.CONFIRMED.value,
}

_NAV_BY_GAP_KIND = {
    "item_no_source": "requirement_workbench",
    "item_no_chart": "diagram_workbench",
    "item_no_document": "publication_workbench",
    "chart_orphan": "diagram_workbench",
    "element_orphan": "requirement_workbench",
    "business_knowledge_unreferenced": "requirement_workbench",  # 业务知识未被引用（06 A.4）
}

_REVIEW_MARK = "[review:{key}]"

NodeKey = tuple[str, str]


@dataclass(frozen=True)
class _Edge:
    edge_key: str
    relation_kind: str
    origin: str  # ldm013 / derived
    upstream: NodeKey
    downstream: NodeKey
    status: str
    link_ref: Optional[str] = None
    status_reason: Optional[str] = None
    # 仅 material_element 边：下游知识项来源锚点引文（首条=卡片用，全量=详情面板列全）
    anchor_quote: Optional[str] = None
    anchor_quotes: tuple[str, ...] = ()

    def to_read(self) -> TraceEdgeRead:
        return TraceEdgeRead(
            edge_key=self.edge_key,
            relation_kind=self.relation_kind,
            origin=self.origin,
            upstream_type=self.upstream[0],
            upstream_ref=self.upstream[1],
            downstream_type=self.downstream[0],
            downstream_ref=self.downstream[1],
            status=self.status,
            link_ref=self.link_ref,
            status_reason=self.status_reason,
            anchor_quote=self.anchor_quote,
            anchor_quotes=list(self.anchor_quotes),
        )


@dataclass
class _Graph:
    """项目关系网内存投影（每次请求从事实重建，非事实源）。"""

    nodes: dict[NodeKey, TraceNodeRead] = field(default_factory=dict)
    up_adj: dict[NodeKey, list[_Edge]] = field(default_factory=dict)
    down_adj: dict[NodeKey, list[_Edge]] = field(default_factory=dict)

    def add_edge(self, edge: _Edge) -> None:
        if edge.upstream not in self.nodes or edge.downstream not in self.nodes:
            return  # 端点不在存量投影内（已撤销/已替代）→ 边不进网
        self.down_adj.setdefault(edge.upstream, []).append(edge)
        self.up_adj.setdefault(edge.downstream, []).append(edge)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _anchor_quotes(source_anchor: Optional[str]) -> tuple[str, ...]:
    """LDM-005 来源锚点引文解析（惯用法同 item_review._source_row，容错同款）。

    返回 ranges[].exact 逐字原文序列；锚点缺失/JSON 坏数据/形状不符 = 空（读模型不抛错）。
    """
    if not source_anchor:
        return ()
    try:
        ranges = json.loads(source_anchor).get("ranges", [])
        return tuple(
            r.get("exact") for r in ranges
            if isinstance(r, dict) and isinstance(r.get("exact"), str) and r.get("exact").strip()
        )
    except (ValueError, AttributeError, TypeError):
        return ()


def _build_graph(facts: ProjectTraceFacts) -> _Graph:
    g = _Graph()
    for m in facts.materials:
        # 卡片语义修正（2026-07-12）：基础 label=原文头优先；source_note 是接入自动拼装的
        # 元数据串（「来源类型:…；来源对象:…」），降为详情面板「来源说明」字段。
        g.nodes[("material", m.id)] = TraceNodeRead(
            node_type="material", ref=m.id,
            label=m.raw_text_head or m.source_note or "未命名材料",
            source_note=m.source_note or None,
            updated_at=_iso(m.created_at),
        )
    live_elements = [
        e for e in facts.elements
        if not e.superseded and e.process_status != ElementProcessStatus.REVOKED.value
    ]
    for e in live_elements:
        g.nodes[("element", e.id)] = TraceNodeRead(
            node_type="element", ref=e.id, label=e.content_head,
            sub_label=e.element_type, status=e.process_status, updated_at=_iso(e.updated_at),
        )
    live_items = [i for i in facts.items if i.status in _LIVE_ITEM_STATUSES]
    for i in live_items:
        g.nodes[("requirement_item", i.id)] = TraceNodeRead(
            node_type="requirement_item", ref=i.id,
            label=f"{i.req_no} {i.expression_head}".strip(),
            sub_label=i.req_type, status=i.status, updated_at=_iso(i.updated_at),
        )
    for c in facts.charts:
        g.nodes[("chart", c.id)] = TraceNodeRead(
            node_type="chart", ref=c.id, label=c.title,
            sub_label=c.chart_type, status=c.status, updated_at=_iso(c.updated_at),
        )
    for d in facts.documents:
        g.nodes[("document", d.id)] = TraceNodeRead(
            node_type="document", ref=d.id, label=d.title,
            status=d.status, updated_at=_iso(d.updated_at),
        )

    material_by_pr = {pr.id: pr.material_ref for pr in facts.parse_results}
    for e in live_elements:
        material_ref = material_by_pr.get(e.parse_result_ref)
        if material_ref:
            quotes = _anchor_quotes(e.source_anchor)
            g.add_edge(_Edge(
                edge_key=f"me:{material_ref}:{e.id}", relation_kind="material_element",
                origin="derived", upstream=("material", material_ref),
                downstream=("element", e.id), status="derived",
                anchor_quote=quotes[0] if quotes else None, anchor_quotes=quotes,
            ))
    for i in live_items:
        for el_ref in i.source_element_refs:
            g.add_edge(_Edge(
                edge_key=f"ei:{el_ref}:{i.id}", relation_kind="element_item",
                origin="derived", upstream=("element", el_ref),
                downstream=("requirement_item", i.id), status="derived",
            ))
    formal_supporting: set[tuple] = set()
    for t in facts.trace_links:
        if t.relation_type == TraceRelationType.CHART.value:
            g.add_edge(_Edge(
                edge_key=f"tl:{t.id}", relation_kind="chart_source", origin="ldm013",
                upstream=(t.upstream_type, t.upstream_ref),
                downstream=(t.downstream_type, t.downstream_ref),
                status=t.status, link_ref=t.id, status_reason=t.status_reason,
            ))
        elif t.relation_type == TraceRelationType.SUPPORTING_BASIS.value:
            # 业务翼要素 → 需求条目 的支撑依据正式边（LDM-013 四态照录；06 A.1）
            up = (t.upstream_type, t.upstream_ref)
            down = (t.downstream_type, t.downstream_ref)
            g.add_edge(_Edge(
                edge_key=f"tl:{t.id}", relation_kind="supporting_basis", origin="ldm013",
                upstream=up, downstream=down,
                status=t.status, link_ref=t.id, status_reason=t.status_reason,
            ))
            formal_supporting.add((up, down))
        # 其余 relation_type 稳定码保留、不投影

    # 确定性名称匹配派生支撑依据边（06 A.2；读时计算、不落库、可整层重算）：
    # 业务翼确认态要素名称出现在确认态条目 expression 中 → derived supporting_basis 边。
    # business_rule 无稳定短名，显式排除以防误匹配；正式边优先去重。
    confirmed_items = [i for i in live_items if i.status == RequirementItemStatus.CONFIRMED.value]
    for be in live_elements:
        if be.process_status != ElementProcessStatus.CONFIRMED.value:
            continue
        if knowledge_category_of(be.element_type) != KnowledgeCategory.BUSINESS.value:
            continue
        if be.element_type == "business_rule":
            continue
        name = normalize_element_name(be.content_head)
        if len(name) < 2:
            continue  # 过短名不匹配
        for it in confirmed_items:
            pair = (("element", be.id), ("requirement_item", it.id))
            if pair in formal_supporting:
                continue  # 正式边优先去重
            if name in normalize_text(it.expression_head):
                g.add_edge(_Edge(
                    edge_key=f"sbd:{be.id}:{it.id}", relation_kind="supporting_basis",
                    origin="derived", upstream=pair[0], downstream=pair[1], status="derived",
                ))
    current_index = {d.id: d.index_version for d in facts.documents}
    for en in facts.doc_index_entries:
        if en.asset_ref is None or en.index_version != current_index.get(en.document_ref):
            continue  # 只投影当前索引版本（页面设计 §2 文档承接派生口径）
        upstream_type = {"requirement_item": "requirement_item", "chart": "chart"}.get(en.asset_type)
        if upstream_type is None:
            continue  # boilerplate/material 槽位不构成承接边
        g.add_edge(_Edge(
            edge_key=f"di:{en.asset_ref}:{en.document_ref}", relation_kind="document_reference",
            origin="derived", upstream=(upstream_type, en.asset_ref),
            downstream=("document", en.document_ref), status="derived",
        ))
    return g


class TraceAnalysisService:
    """AEP-058…AEP-066：查询、漫游、诊断、导航与复核路由（不写正式追溯事实）。"""

    def __init__(
        self,
        read_repo: TraceReadRepository,
        trace_links: TraceLinkRepository,
        issues: IssueRepository,
    ) -> None:
        self._repo = read_repo
        self._trace_links = trace_links
        self._issues = issues

    # ---- AEP-058 入口锚点 + 小计数 ----

    def read_entry(self, project_ref: str) -> TraceEntryRead:
        facts = self._load(project_ref)
        graph = _build_graph(facts)
        anchors = []
        for node_type in NODE_TYPES:
            nodes = [n for (t, _), n in graph.nodes.items() if t == node_type]
            nodes.sort(key=lambda n: n.updated_at or "", reverse=True)
            if nodes:
                anchors.append(TraceAnchorGroupRead(node_type=node_type, nodes=nodes[:ANCHORS_PER_TYPE]))
        status_count = {s.value: 0 for s in TraceLinkStatus}
        for t in facts.trace_links:
            status_count[t.status] = status_count.get(t.status, 0) + 1
        gaps = self._derive_gaps(facts, graph)
        counts = TraceCountsRead(
            links_total=len(facts.trace_links),
            effective=status_count[TraceLinkStatus.EFFECTIVE.value],
            pre_established=status_count[TraceLinkStatus.PRE_ESTABLISHED.value],
            suspect=status_count[TraceLinkStatus.SUSPECT_PENDING_REVIEW.value],
            invalid=status_count[TraceLinkStatus.INVALID.value],
            gaps=len(gaps),
        )
        default_focus = self._default_focus(graph)
        log_event(
            "trace-analysis", "aep058.entry_read", project_ref=project_ref,
            nodes=len(graph.nodes), links=counts.links_total,
            gaps=counts.gaps, suspect=counts.suspect,
            has_focus=default_focus is not None,
        )
        return TraceEntryRead(
            project_ref=project_ref, anchors=anchors,
            default_focus=default_focus, counts=counts,
            next_action=None if default_focus else "项目暂无关系网：先走 材料→要素→条目→图表 主链形成资产关系",
        )

    @staticmethod
    def _default_focus(graph: _Graph) -> Optional[TraceNodeRead]:
        """默认焦点：最近更新的确认态条目 → 任一条目 → 图表 → 要素 → 材料（页面设计 §3）。"""
        items = [n for (t, _), n in graph.nodes.items() if t == "requirement_item"]
        confirmed = [n for n in items if n.status == RequirementItemStatus.CONFIRMED.value]
        for pool_type, pool in (
            (None, confirmed), (None, items),
            ("chart", None), ("element", None), ("material", None),
        ):
            candidates = pool if pool is not None else [
                n for (t, _), n in graph.nodes.items() if t == pool_type
            ]
            if candidates:
                return max(candidates, key=lambda n: n.updated_at or "")
        return None

    # ---- AEP-059 / AEP-060 焦点邻域链路（漫游=以新焦点重取） ----

    def read_chain(
        self, project_ref: str, focus_type: str, focus_ref: str, direction: str,
        depth: int = DEFAULT_DEPTH, limit: int = DEFAULT_LIMIT, include_invalid: bool = False,
    ) -> TraceChainRead:
        if direction not in DIRECTIONS:
            raise InvalidInput(f"方向不合法：{direction}（应为 upstream/downstream）")
        if focus_type not in NODE_TYPES:
            raise InvalidInput(f"焦点类型不合法：{focus_type}")
        depth = max(1, min(3, depth))
        limit = max(1, min(32, limit))
        facts = self._load(project_ref)
        graph = _build_graph(facts)
        focus_key: NodeKey = (focus_type, focus_ref)
        focus = graph.nodes.get(focus_key)
        if focus is None:
            raise NotFound(f"焦点对象不存在或不在存量关系网中：{focus_type}/{focus_ref}")

        adjacency = graph.up_adj if direction == "upstream" else graph.down_adj
        visited: set[NodeKey] = {focus_key}
        frontier: list[NodeKey] = [focus_key]
        levels: list[TraceLevelRead] = []
        for distance in range(1, depth + 1):
            candidate_edges: list[_Edge] = []
            for key in frontier:
                for edge in adjacency.get(key, []):
                    if edge.status == TraceLinkStatus.INVALID.value and not include_invalid:
                        continue  # 失效边默认不进窗口（页面设计 §2）
                    candidate_edges.append(edge)
            neighbor_keys: list[NodeKey] = []
            for edge in candidate_edges:
                other = edge.upstream if direction == "upstream" else edge.downstream
                if other not in visited and other not in neighbor_keys:
                    neighbor_keys.append(other)
            if not neighbor_keys:
                break
            neighbor_keys.sort(key=lambda k: graph.nodes[k].updated_at or "", reverse=True)
            kept = neighbor_keys[:limit]
            folded = neighbor_keys[limit:]
            folded_by_type: dict[str, int] = {}
            for key in folded:
                folded_by_type[key[0]] = folded_by_type.get(key[0], 0) + 1
            kept_set = set(kept)
            level_edges = [
                e for e in candidate_edges
                if (e.upstream if direction == "upstream" else e.downstream) in kept_set
            ]
            levels.append(TraceLevelRead(
                distance=distance,
                nodes=[graph.nodes[k] for k in kept],
                edges=[e.to_read() for e in level_edges],
                folded_count=len(folded),
                folded_by_type=folded_by_type,
            ))
            visited.update(kept)
            frontier = kept
        log_event(
            "trace-analysis",
            "aep059.upstream_read" if direction == "upstream" else "aep060.downstream_read",
            project_ref=project_ref, focus_type=focus_type, depth=depth, limit=limit,
            levels=len(levels), nodes=sum(len(lv.nodes) for lv in levels),
            folded=sum(lv.folded_count for lv in levels),
        )
        return TraceChainRead(
            project_ref=project_ref, direction=direction, focus=focus,
            depth=depth, limit=limit, include_invalid=include_invalid, levels=levels,
        )

    # ---- AEP-061 单条关系详情（仅 LDM-013 边；derived 边由前端就地组装） ----

    def read_link_detail(self, project_ref: str, link_ref: str) -> TraceLinkRead:
        facts = self._load(project_ref)
        fact = next((t for t in facts.trace_links if t.id == link_ref), None)
        if fact is None:
            raise NotFound(f"追溯关系不存在：{link_ref}")
        read = self._link_read(fact, facts)
        log_event(
            "trace-analysis", "aep061.link_detail_read",
            project_ref=project_ref, status=fact.status, relation_type=fact.relation_type,
        )
        return read

    # ---- AEP-062 覆盖度（预建立不计入条目→图表覆盖） ----

    def read_coverage(self, project_ref: str) -> TraceCoverageRead:
        facts = self._load(project_ref)
        graph = _build_graph(facts)
        live_items = [i for i in facts.items if i.status in _LIVE_ITEM_STATUSES]
        confirmed = [i for i in live_items if i.status == RequirementItemStatus.CONFIRMED.value]
        live_element_ids = {ref for (t, ref) in graph.nodes if t == "element"}
        with_source = [
            i for i in live_items if any(r in live_element_ids for r in i.source_element_refs)
        ]
        effective_upstreams = {
            t.upstream_ref for t in facts.trace_links
            if t.relation_type == TraceRelationType.CHART.value
            and t.status == TraceLinkStatus.EFFECTIVE.value
        }
        current_index = {d.id: d.index_version for d in facts.documents}
        indexed_items = {
            en.asset_ref for en in facts.doc_index_entries
            if en.asset_type == "requirement_item" and en.asset_ref
            and en.index_version == current_index.get(en.document_ref)
        }

        def direction(key: str, covered: int, total: int) -> TraceCoverageDirectionRead:
            return TraceCoverageDirectionRead(
                key=key, covered=covered, total=total,
                ratio=1.0 if total == 0 else round(covered / total, 4),
            )

        read = TraceCoverageRead(
            project_ref=project_ref,
            directions=[
                direction("item_source", len(with_source), len(live_items)),
                direction(
                    "item_chart",
                    sum(1 for i in confirmed if i.id in effective_upstreams),
                    len(confirmed),
                ),
                direction(
                    "item_document",
                    sum(1 for i in confirmed if i.id in indexed_items),
                    len(confirmed),
                ),
            ],
        )
        log_event(
            "trace-analysis", "aep062.coverage_read", project_ref=project_ref,
            **{d.key: d.ratio for d in read.directions},
        )
        return read

    # ---- AEP-063 缺口/孤儿清单 ----

    def read_gaps(
        self, project_ref: str, kind: Optional[str] = None,
        offset: int = 0, limit: int = 50,
    ) -> TraceGapListRead:
        if kind is not None and kind not in _NAV_BY_GAP_KIND:
            raise InvalidInput(f"缺口类别不合法：{kind}")
        facts = self._load(project_ref)
        graph = _build_graph(facts)
        gaps = self._derive_gaps(facts, graph)
        if kind:
            gaps = [g for g in gaps if g.kind == kind]
        total = len(gaps)
        log_event(
            "trace-analysis", "aep063.gaps_read", project_ref=project_ref,
            kind=kind, total=total,
        )
        return TraceGapListRead(
            project_ref=project_ref,
            items=gaps[max(0, offset): max(0, offset) + max(1, min(200, limit))],
            total=total,
        )

    @staticmethod
    def _derive_gaps(facts: ProjectTraceFacts, graph: _Graph) -> list[TraceGapItemRead]:
        """缺口判定（页面设计 §5 类别定义表；派生读模型，可重建）。"""
        gaps: list[TraceGapItemRead] = []
        live_items = [i for i in facts.items if i.status in _LIVE_ITEM_STATUSES]
        confirmed = [i for i in live_items if i.status == RequirementItemStatus.CONFIRMED.value]
        live_element_ids = {ref for (t, ref) in graph.nodes if t == "element"}
        chart_links = [
            t for t in facts.trace_links if t.relation_type == TraceRelationType.CHART.value
        ]
        effective_upstreams = {
            t.upstream_ref for t in chart_links if t.status == TraceLinkStatus.EFFECTIVE.value
        }
        alive_link_downstreams = {
            t.downstream_ref for t in chart_links if t.status != TraceLinkStatus.INVALID.value
        }
        current_index = {d.id: d.index_version for d in facts.documents}
        indexed_items = {
            en.asset_ref for en in facts.doc_index_entries
            if en.asset_type == "requirement_item" and en.asset_ref
            and en.index_version == current_index.get(en.document_ref)
        }
        item_label = {i.id: f"{i.req_no} {i.expression_head}".strip() for i in facts.items}

        def add(kind: str, node_type: str, node_ref: str, label: str, detail: str) -> None:
            gaps.append(TraceGapItemRead(
                kind=kind, node_type=node_type, node_ref=node_ref, label=label,
                detail=detail, nav_target=_NAV_BY_GAP_KIND[kind],
            ))

        for i in live_items:
            if not any(r in live_element_ids for r in i.source_element_refs):
                add("item_no_source", "requirement_item", i.id, item_label[i.id],
                    "条目无存量来源要素（来源被撤销/替代或未登记）")
        for i in confirmed:
            if i.id not in effective_upstreams:
                add("item_no_chart", "requirement_item", i.id, item_label[i.id],
                    "确认态条目无有效图表来源关系（预建立不计入）")
            if i.id not in indexed_items:
                add("item_no_document", "requirement_item", i.id, item_label[i.id],
                    "确认态条目未进入当前文档内容索引")
        for c in facts.charts:
            if c.status != ChartStatus.VOIDED.value and c.id not in alive_link_downstreams:
                add("chart_orphan", "chart", c.id, c.title, "图表无任何非失效来源关系")
        item_used_elements = {r for i in live_items for r in i.source_element_refs}
        for e in facts.elements:
            if (
                not e.superseded
                and e.process_status == ElementProcessStatus.CONFIRMED.value
                and e.element_type in ITEMIZABLE_ELEMENT_TYPES
                and e.id not in item_used_elements
            ):
                add("element_orphan", "element", e.id, e.content_head,
                    "已确认可形成类型要素未被任何条目引用")
        # 业务知识未引用缺口（06 A.4；P5 前只查支撑依据边 + 图表来源边两者）：
        # 业务翼确认态要素无任何 supporting_basis 边（正式+派生）、无图表来源边。
        referenced_biz: set[str] = set()
        for (ntype, ref), out_edges in graph.down_adj.items():
            if ntype != "element":
                continue
            if any(ed.relation_kind in ("supporting_basis", "chart_source") for ed in out_edges):
                referenced_biz.add(ref)
        for e in facts.elements:
            if (
                not e.superseded
                and e.process_status == ElementProcessStatus.CONFIRMED.value
                and knowledge_category_of(e.element_type) == KnowledgeCategory.BUSINESS.value
                and e.id not in referenced_biz
            ):
                add("business_knowledge_unreferenced", "element", e.id, e.content_head,
                    "确认态业务知识未被任何条目引用或图表采用")
        return gaps

    # ---- AEP-064 可疑失效链路清单 ----

    def read_suspects(
        self, project_ref: str, include_invalid: bool = False,
        offset: int = 0, limit: int = 50,
    ) -> TraceSuspectListRead:
        facts = self._load(project_ref)
        wanted = {TraceLinkStatus.SUSPECT_PENDING_REVIEW.value}
        if include_invalid:
            wanted.add(TraceLinkStatus.INVALID.value)
        links = [t for t in facts.trace_links if t.status in wanted]
        links.sort(key=lambda t: t.updated_at, reverse=True)
        total = len(links)
        page = links[max(0, offset): max(0, offset) + max(1, min(200, limit))]
        log_event(
            "trace-analysis", "aep064.suspects_read", project_ref=project_ref,
            include_invalid=include_invalid, total=total,
        )
        return TraceSuspectListRead(
            project_ref=project_ref,
            items=[self._link_read(t, facts) for t in page],
            total=total,
        )

    # ---- AEP-066 复核路由（结论交追溯图谱模块按迁移表重判） ----

    def review_suspect_link(
        self, project_ref: str, link_ref: str, command: TraceReviewCommand,
    ) -> TraceReviewResult:
        if command.conclusion not in ("restore", "maintain"):
            raise InvalidInput(f"复核结论不合法：{command.conclusion}（应为 restore/maintain）")
        facts = self._load(project_ref)
        fact = next((t for t in facts.trace_links if t.id == link_ref), None)
        if fact is None:
            raise NotFound(f"追溯关系不存在：{link_ref}")
        mark = _REVIEW_MARK.format(key=command.idempotency_key)
        if mark in (fact.status_reason or ""):
            # 幂等重放：同一复核命令已生效，按当前状态回放结果
            status = (
                "restored"
                if fact.status == TraceLinkStatus.PRE_ESTABLISHED.value else "maintained"
            )
            return TraceReviewResult(status=status, link=self._link_read(fact, facts))
        if fact.status != TraceLinkStatus.SUSPECT_PENDING_REVIEW.value:
            raise RejectedTransition(
                f"默认拒绝：追溯关系状态 {fact.status} 不接受复核（仅可疑待复核可复核）"
            )
        reason = (command.reason or "").strip()
        if command.conclusion == "maintain":
            new_reason = f"复核维持可疑：{reason or '未填写依据'} {mark}"
            self._trace_links.set_link_status(link_ref, fact.status, status_reason=new_reason)
            log_event(
                "trace-analysis", "aep066.review_maintained",
                project_ref=project_ref, operator=command.operator_ref,
            )
            return TraceReviewResult(
                status="maintained",
                link=self._link_read(self._reload_link(project_ref, link_ref), facts),
                next_action="可疑链路保持待复核；无法闭合可转问题项",
            )
        # restore：守卫=覆盖对象仍成立（上游条目仍是图表来源 ∧ 图表未作废）
        chart = next((c for c in facts.charts if c.id == fact.downstream_ref), None)
        if (
            chart is None
            or chart.status == ChartStatus.VOIDED.value
            or fact.upstream_ref not in chart.source_refs
        ):
            log_event(
                "trace-analysis", "aep066.review_rejected", level="WARN",
                project_ref=project_ref, error_code="coverage_not_held",
                msg="复核恢复被拒：覆盖对象已不成立",
            )
            raise RejectedTransition("复核恢复被拒：覆盖对象已不成立（来源移除或图表作废）")
        new_state = trace_transition(TraceState.SUSPECT_PENDING_REVIEW, TraceEvent.SYNC)
        self._trace_links.set_link_status(
            link_ref, new_state.value,
            status_reason=f"复核恢复为预建立：{reason or '覆盖对象仍成立'} {mark}",
        )
        log_event(
            "trace-analysis", "aep066.review_restored",
            project_ref=project_ref, operator=command.operator_ref,
            from_status=fact.status, to_status=new_state.value,
        )
        return TraceReviewResult(
            status="restored",
            link=self._link_read(self._reload_link(project_ref, link_ref), facts),
            next_action="已恢复预建立；须重走图表核对确认后方可正式确立为有效",
        )

    # ---- AEP-066 转问题项 ----

    def create_diagnosis_issue(self, project_ref: str, command: TraceIssueCommand) -> IssueRead:
        if not self._repo.project_exists(project_ref):
            raise NotFound(f"项目不存在：{project_ref}")
        existing = self._issues.find_issue_by_idempotency(command.idempotency_key)
        if existing:
            row = self._issues.get_issue(existing)
            if row:
                return self._issue_read(row)
        issue_type = command.issue_type or IssueType.GAP.value
        if issue_type not in {t.value for t in IssueType}:
            raise InvalidInput(f"问题项类型不合法：{issue_type}")
        if not command.title.strip():
            raise InvalidInput("问题项标题不能为空")
        link_refs = [command.trace_link_ref] if command.trace_link_ref else []
        issue_ref = self._issues.create_issue(
            project_ref=project_ref, issue_type=issue_type,
            title=command.title.strip(), description=(command.description or "").strip(),
            origin_kind="trace_diagnosis", chart_ref=command.chart_ref, finding_ref=None,
            trace_link_refs_json=json.dumps(link_refs),
            created_by=command.operator_ref, idempotency_key=command.idempotency_key,
        )
        if command.trace_link_ref:
            self._trace_links.set_link_issue(command.trace_link_ref, issue_ref)
        log_event(
            "trace-analysis", "aep066.issue_created", project_ref=project_ref,
            issue_type=issue_type, linked=bool(command.trace_link_ref),
            operator=command.operator_ref,
        )
        row = self._issues.get_issue(issue_ref)
        assert row is not None
        return self._issue_read(row)

    def create_supporting_basis(
        self, project_ref: str, command: SupportingBasisCommand
    ) -> SupportingBasisResult:
        """人工补全支撑依据边（06 A.1）：上游=业务翼确认态要素、下游=需求条目。

        条目确认态 → 边直接有效；条目待确认 → 预建立（预建立不作正式依据；P7 引用依据）。
        同对已有正式边则幂等返回（正式边优先）。
        """
        facts = self._load(project_ref)
        el = next(
            (e for e in facts.elements if e.id == command.element_ref and not e.superseded), None
        )
        if el is None:
            raise NotFound("上游知识项不存在或已被替代")
        if el.process_status != ElementProcessStatus.CONFIRMED.value:
            raise InvalidInput("支撑依据上游必须是确认态知识项")
        if knowledge_category_of(el.element_type) != KnowledgeCategory.BUSINESS.value:
            raise InvalidInput("支撑依据上游必须是业务领域知识翼要素")
        it = next((i for i in facts.items if i.id == command.item_ref), None)
        if it is None or it.status not in _LIVE_ITEM_STATUSES:
            raise InvalidInput("支撑依据下游必须是存量需求条目")
        existing = self._trace_links.find_link(
            command.element_ref, command.item_ref, TraceRelationType.SUPPORTING_BASIS.value
        )
        if existing is not None:
            return SupportingBasisResult(
                link_ref=existing.id, status=existing.status, next_action="支撑依据边已存在（幂等）"
            )
        status = (
            TraceLinkStatus.EFFECTIVE.value
            if it.status == RequirementItemStatus.CONFIRMED.value
            else TraceLinkStatus.PRE_ESTABLISHED.value
        )
        link_id = self._trace_links.create_link(
            project_ref, TraceRelationType.SUPPORTING_BASIS.value,
            "element", command.element_ref, "requirement_item", command.item_ref,
            status, "人工补全支撑依据",
        )
        log_event("trace-analysis", "supporting_basis.created", project_ref=project_ref,
                  element_ref=command.element_ref, item_ref=command.item_ref, status=status, ok=True)
        return SupportingBasisResult(
            link_ref=link_id, status=status,
            next_action="支撑依据边已建立" + ("（有效）" if status == "effective" else "（预建立，随条目确认转有效）"),
        )

    # ---- 内部 ----

    def _load(self, project_ref: str) -> ProjectTraceFacts:
        if not self._repo.project_exists(project_ref):
            raise NotFound(f"项目不存在：{project_ref}")
        return self._repo.load_project_facts(project_ref)

    def _reload_link(self, project_ref: str, link_ref: str):
        facts = self._repo.load_project_facts(project_ref)
        fact = next((t for t in facts.trace_links if t.id == link_ref), None)
        assert fact is not None
        return fact

    @staticmethod
    def _link_read(fact, facts: ProjectTraceFacts) -> TraceLinkRead:
        item_label = {
            i.id: f"{i.req_no} {i.expression_head}".strip() for i in facts.items
        }
        chart_label = {c.id: c.title for c in facts.charts}
        doc_label = {d.id: d.title for d in facts.documents}

        def label_of(node_type: str, ref: str) -> Optional[str]:
            if node_type == "requirement_item":
                return item_label.get(ref)
            if node_type == "chart":
                return chart_label.get(ref)
            if node_type == "document":
                return doc_label.get(ref)
            return None

        return TraceLinkRead(
            link_ref=fact.id,
            relation_type=fact.relation_type,
            upstream_type=fact.upstream_type,
            upstream_ref=fact.upstream_ref,
            upstream_label=label_of(fact.upstream_type, fact.upstream_ref),
            downstream_type=fact.downstream_type,
            downstream_ref=fact.downstream_ref,
            downstream_label=label_of(fact.downstream_type, fact.downstream_ref),
            status=fact.status,
            initial_basis=fact.initial_basis,
            status_reason=fact.status_reason,
            established_basis=fact.established_basis,
            established_at=_iso(fact.established_at),
            issue_ref=fact.issue_ref,
        )

    @staticmethod
    def _issue_read(row: IssueRow) -> IssueRead:
        return IssueRead(
            issue_ref=row.id, issue_type=row.issue_type, status=row.status,
            title=row.title, description=row.description, origin_kind=row.origin_kind,
            chart_ref=row.chart_ref, finding_ref=row.finding_ref,
            trace_link_refs=json.loads(row.trace_link_refs or "[]"),
            created_by=row.created_by, created_at=row.created_at,
        )
