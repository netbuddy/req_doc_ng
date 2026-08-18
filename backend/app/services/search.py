"""混合检索服务：语义 lane + 词法 lane + RRF 融合 + 分组 + 三级降级（全局检索 03 篇）。

查询对象是 search_index 派生表（**不触碰五类源表**——换源零改动）；只额外读 Project 元数据取跨项目
展示名（非资产投影耦合）。三级降级（README 不变式 7）对调用方透明、DTO 形状不变：
- Postgres + embedding 可用：pgvector 语义 lane + pg_trgm 词法 lane + RRF。
- Postgres 无 embedding（stub/端点不可达）：纯词法 lane（能力不缺失，只少语义召回）。
- SQLite / 无扩展：Python 子串扫描内存过滤（复用 asset_catalog 的 needle-in-field 思路）。

排序：RRF 按名次融合（避免语义距离与 trgm 相似度不可比分数直接相加），精确标识（exact/id_hit）给
融合后置顶硬加成——解决稠密向量对 REQ-087 类编号的弱项（00 §3）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.adapters.embeddings import Embedder
from app.api.schemas import SearchGroupRead, SearchHitRead, SearchResultsRead
from app.db.models import Project, SearchIndex
from app.domain.labels import SEARCH_ENTITY_GROUP_LABELS
from app.log import log_event
from app.services.search_source import ENTITY_TYPES

_COMPONENT = "search-service"

# 组序（03 §3）：与产品主链顺序一致。
_GROUP_ORDER: tuple[str, ...] = ("requirement_item", "element", "chart", "document", "material")

# entity_type → 目标工作台 WorkbenchKey 码（04 §3，服务端单一来源；前端存"码→选中动作"同源映射）。
_WORKBENCH: dict[str, str] = {
    "requirement_item": "management",
    "element": "management",
    "material": "management",
    "chart": "diagram",
    "document": "release",
}


@dataclass
class _Candidate:
    project_id: str
    entity_type: str
    ref: str
    title: str
    body: str
    updated_at: Optional[datetime]
    exact: bool      # 标题与 q 完全相等
    id_hit: bool     # 标题前缀命中 / ref 命中（编号类精确）


def _neg_ts(dt: Optional[datetime]) -> float:
    """updated_at 降序排序键（None 最旧）。"""
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return -dt.timestamp()


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SearchService:
    def __init__(
        self,
        session: Session,
        embedder: Embedder,
        k_sem: int = 30,
        k_lex: int = 30,
        rrf_k: int = 60,
        per_group: int = 8,
        guard: int = 50,
    ) -> None:
        self._s = session
        self._embedder = embedder
        self._k_sem = k_sem
        self._k_lex = k_lex
        self._rrf_k = rrf_k
        self._per_group = per_group
        self._guard = guard
        self._is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"

    def search(self, q: str, types: Optional[list[str]] = None, limit: Optional[int] = None) -> SearchResultsRead:
        q = (q or "").strip()
        limit = limit or self._per_group
        valid_types = self._valid_types(types)
        if not q:
            return SearchResultsRead(query=q, groups=[], total=0)

        lanes = self._collect_lanes(q, valid_types)
        fused = self._rrf(lanes)
        results = self._assemble(q, fused, limit)
        log_event(
            _COMPONENT, "search.query",
            dialect=("postgresql" if self._is_pg else "sqlite"),
            lanes=len(lanes), types=(len(valid_types) if valid_types else 0),
            groups=len(results.groups), total=results.total,
        )
        return results

    # ---- lane 收集 ----

    def _collect_lanes(self, q: str, types: Optional[list[str]]) -> list[list[_Candidate]]:
        lanes: list[list[_Candidate]] = []
        qvec = self._embed_query(q)
        if qvec is not None and self._is_pg:
            lanes.append(self._semantic_pg(qvec, types, q))
        lanes.append(self._lexical_pg(q, types) if self._is_pg else self._lexical_python(q, types))
        return [lane for lane in lanes if lane]

    def _embed_query(self, q: str) -> Optional[list[float]]:
        try:
            return self._embedder.embed([q])[0]
        except Exception:  # noqa: BLE001 embedder 异常不阻断检索（降级词法）
            return None

    def _semantic_pg(self, qvec: list[float], types: Optional[list[str]], q: str) -> list[_Candidate]:
        stmt = select(SearchIndex).where(SearchIndex.embedding.isnot(None))
        if types:
            stmt = stmt.where(SearchIndex.entity_type.in_(types))
        stmt = stmt.order_by(SearchIndex.embedding.cosine_distance(qvec)).limit(self._k_sem)
        return [self._cand(r, q) for r in self._s.scalars(stmt).all()]

    def _lexical_pg(self, q: str, types: Optional[list[str]]) -> list[_Candidate]:
        like = f"%{_escape_like(q)}%"
        stmt = select(SearchIndex).where(
            or_(
                SearchIndex.title.ilike(like, escape="\\"),
                SearchIndex.body.ilike(like, escape="\\"),
                SearchIndex.ref.ilike(like, escape="\\"),
            )
        )
        if types:
            stmt = stmt.where(SearchIndex.entity_type.in_(types))
        # 精确/前缀命中在 Python 端加成；SQL 端按 title 相似度取回相关候选。
        stmt = stmt.order_by(func.similarity(SearchIndex.title, q).desc()).limit(self._k_lex)
        return [self._cand(r, q) for r in self._s.scalars(stmt).all()]

    def _lexical_python(self, q: str, types: Optional[list[str]]) -> list[_Candidate]:
        stmt = select(SearchIndex)
        if types:
            stmt = stmt.where(SearchIndex.entity_type.in_(types))
        ql = q.lower()
        cands: list[_Candidate] = []
        for r in self._s.scalars(stmt).all():
            if ql in (r.title or "").lower() or ql in (r.body or "").lower() or ql in (r.ref or "").lower():
                cands.append(self._cand(r, q))
        # 词法排序：精确标题 > 前缀/ref 命中 > 标题内位置靠前 > 新近。
        cands.sort(key=lambda c: (
            not c.exact, not c.id_hit, (c.title or "").lower().find(ql) % (len(c.title) + 1), _neg_ts(c.updated_at),
        ))
        return cands[:self._k_lex]

    def _cand(self, r: SearchIndex, q: str) -> _Candidate:
        tl = (r.title or "").strip().lower()
        ql = q.lower()
        return _Candidate(
            project_id=str(r.project_id), entity_type=r.entity_type, ref=r.ref,
            title=r.title or "", body=r.body or "", updated_at=r.updated_at,
            exact=(tl == ql),
            id_hit=(tl.startswith(ql) or ql in (r.ref or "").lower()),
        )

    # ---- RRF 融合 ----

    def _rrf(self, lanes: list[list[_Candidate]]) -> dict[tuple[str, str, str], tuple[_Candidate, float]]:
        fused: dict[tuple[str, str, str], list] = {}
        for lane in lanes:
            for rank, c in enumerate(lane):
                key = (c.project_id, c.entity_type, c.ref)
                contrib = 1.0 / (self._rrf_k + rank + 1)  # RRF: 1/(K + 名次)
                if key in fused:
                    fused[key][1] += contrib  # 双 lane 命中 → 相加（语义+词法双命中加权）
                else:
                    fused[key] = [c, contrib]
        # 精确标识硬加成：确保 REQ-xxx 等精确命中稳居前列，不被语义近邻挤下（远大于 RRF 量级）。
        for pair in fused.values():
            c = pair[0]
            if c.exact:
                pair[1] += 1.0
            elif c.id_hit:
                pair[1] += 0.5
        return {k: (v[0], v[1]) for k, v in fused.items()}

    # ---- 分组组装 ----

    def _assemble(self, q: str, fused: dict, limit: int) -> SearchResultsRead:
        # 总量 guard（03 §6）：融合后按分降序截 guard，再分组。
        ranked = sorted(fused.values(), key=lambda cs: (-cs[1], _neg_ts(cs[0].updated_at)))[: self._guard]
        names = self._project_names()

        buckets: dict[str, list[tuple[_Candidate, float]]] = {}
        for c, score in ranked:
            buckets.setdefault(c.entity_type, []).append((c, score))

        groups: list[SearchGroupRead] = []
        total = 0
        for et in _GROUP_ORDER:
            items = buckets.get(et)
            if not items:
                continue
            hits = [self._hit(c, score, q, names) for c, score in items[:limit]]
            groups.append(SearchGroupRead(
                entity_type=et, label=SEARCH_ENTITY_GROUP_LABELS.get(et, et),
                hits=hits, total=len(items),
            ))
            total += len(items)
        return SearchResultsRead(query=q, groups=groups, total=total)

    def _hit(self, c: _Candidate, score: float, q: str, names: dict[str, str]) -> SearchHitRead:
        return SearchHitRead(
            project_id=c.project_id, project_name=names.get(c.project_id, ""),
            entity_type=c.entity_type, ref=c.ref, title=c.title,
            snippet=self._snippet(c.body, q),
            workbench=_WORKBENCH.get(c.entity_type, "management"),
            score=round(score, 6),
        )

    def _snippet(self, body: str, q: str) -> str:
        body = body or ""
        pos = body.lower().find(q.lower())
        if pos == -1:
            return body[:90].strip()  # 纯语义命中：取正文前段
        start = max(0, pos - 30)
        end = min(len(body), pos + len(q) + 60)
        return ("…" if start > 0 else "") + body[start:end].strip() + ("…" if end < len(body) else "")

    def _project_names(self) -> dict[str, str]:
        return {str(pid): name for pid, name in self._s.execute(select(Project.id, Project.name)).all()}

    def _valid_types(self, types: Optional[list[str]]) -> Optional[list[str]]:
        if not types:
            return None
        valid = [t for t in types if t in ENTITY_TYPES]
        dropped = [t for t in types if t not in ENTITY_TYPES]
        if dropped:
            # 忽略非法项（04 §5 倾向忽略并记 WARN），不记具体值原文以外的稳定码。
            log_event(_COMPONENT, "search.types.ignored", level="WARN", dropped=len(dropped))
        return valid or None
