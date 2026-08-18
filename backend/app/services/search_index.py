"""检索索引器：把 IndexableNode 嵌入并 upsert 进 search_index 派生表。

全局检索工作包 02 篇 §3。核心不变式：search_index 是**可整层重算的派生索引**，源表权威不动
（README 不变式 2）。三条纪律：
- 免重嵌：content_hash = sha256(node_type + body)，命中未变行则**跳过 embedding 调用**（最贵一步）。
- 删除对账：投影后 prune 源中已消失的 (entity_type, ref)，索引不残留幽灵命中。
- 降级透明：embedder 返回 None（stub / 端点不可达）→ embedding 留 NULL，检索走词法（不变式 7）。

本模块只 import search_index 派生表模型，**不 import 任何源表**（源投影收口于 search_source.py）。
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.embeddings import Embedder, build_embedder
from app.config import settings
from app.db.models import SearchIndex
from app.log import log_event
from app.services.search_source import (
    IndexableNode,
    RelationalSearchSource,
    SearchSourceProvider,
)

_COMPONENT = "search-indexer"


def content_hash(node: IndexableNode) -> str:
    """body 指纹（含 node_type 命名空间），跳过未变行重嵌。"""
    raw = f"{node.node_type}\x00{node.body}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ReindexStats:
    project_id: str
    projected: int      # 投影出的节点数
    embedded: int       # 本次调用 embedder 的文本数（=stale 行数）
    upserted: int       # 新增 + 更新行数
    pruned: int         # 删除对账行数


class SearchIndexer:
    """重算 search_index。reindex_project 幂等；reindex_all 遍历全项目。"""

    def __init__(
        self,
        session: Session,
        source: SearchSourceProvider,
        embedder: Embedder,
        batch_size: int = 32,
    ) -> None:
        self._s = session
        self._source = source
        self._embedder = embedder
        self._batch = max(1, batch_size)

    def reindex_all(self) -> list[ReindexStats]:
        project_ids = [str(p) for p in self._source.iter_all_projects()]
        stats = [self.reindex_project(pid) for pid in project_ids]
        # 项目级删除对账：reindex_project 只 prune 项目内消失的实体，整项目被删则其行永不被访问。
        # 全量回填时清除 project_id 已不在源的孤儿行（避免指向已删项目的幽灵跨项目命中）。
        orphaned = self._prune_orphan_projects(project_ids)
        log_event(
            _COMPONENT, "reindex.all.done",
            projects=len(stats),
            projected=sum(s.projected for s in stats),
            upserted=sum(s.upserted for s in stats),
            pruned=sum(s.pruned for s in stats) + orphaned,
            orphan_project_rows=orphaned,
        )
        return stats

    def _prune_orphan_projects(self, valid_project_ids: list[str]) -> int:
        """删除 project_id 已不在源项目集的 search_index 行（整项目删除对账）。"""
        valid = [uuid.UUID(p) for p in valid_project_ids]
        query = self._s.query(SearchIndex)
        if valid:
            query = query.filter(SearchIndex.project_id.not_in(valid))
        # valid 为空 → 无有效项目 → 全部为孤儿，query 不加过滤即全删。
        count = query.count()
        if count:
            query.delete(synchronize_session=False)
            self._s.commit()
        return count

    def reindex_project(self, project_id: str) -> ReindexStats:
        nodes = list(self._source.iter_nodes(project_id))
        existing = self._load_existing(project_id)  # (entity_type, ref) -> SearchIndex row

        seen: set[tuple[str, str]] = set()
        stale: list[tuple[IndexableNode, str]] = []  # (node, hash) 需重嵌/新写
        for n in nodes:
            key = (n.node_type, n.ref)
            seen.add(key)
            h = content_hash(n)
            row = existing.get(key)
            if row is not None and row.content_hash == h:
                continue  # 未变 → 跳过（免重嵌）
            stale.append((n, h))

        # 免重嵌：仅当有 stale 行才调 embedder（未变行 embedder 调用次数=0，AC）。
        embedded = 0
        if stale:
            embedded = self._embed_and_write(project_id, stale, existing)

        pruned = self._prune(existing, seen)
        upserted = len(stale)
        self._s.commit()

        log_event(
            _COMPONENT, "reindex.project.done",
            project_id=project_id, projected=len(nodes),
            embedded=embedded, upserted=upserted, pruned=pruned,
        )
        return ReindexStats(project_id, len(nodes), embedded, upserted, pruned)

    def reindex_node(self, project_id: str, entity_type: str, ref: str) -> ReindexStats:
        """增量刷新单节点（Phase 2 增量 hook 的接口位，02 §5.2）。

        本包 P1 仅提供接口与最小实现（从项目投影里挑该节点 upsert / 消失则 prune）；
        接入各写服务列 Phase 2。触发语义（entity_type, ref）不变、只换源。
        """
        target = (entity_type, ref)
        node = next(
            (n for n in self._source.iter_nodes(project_id)
             if (n.node_type, n.ref) == target),
            None,
        )
        existing = self._load_existing(project_id)
        if node is None:
            pruned = 1 if target in existing else 0
            if pruned:
                self._s.delete(existing[target])
            self._s.commit()
            return ReindexStats(project_id, 0, 0, 0, pruned)

        h = content_hash(node)
        row = existing.get(target)
        embedded = 0
        if row is None or row.content_hash != h:
            embedded = self._embed_and_write(project_id, [(node, h)], existing)
            self._s.commit()
            return ReindexStats(project_id, 1, embedded, 1, 0)
        return ReindexStats(project_id, 1, 0, 0, 0)

    # ---- 内部 ----

    def _load_existing(self, project_id: str) -> dict[tuple[str, str], SearchIndex]:
        # project_id 列为 Uuid，比较/写入需 uuid.UUID（同 asset_read 约定）。
        rows = self._s.scalars(
            select(SearchIndex).where(SearchIndex.project_id == uuid.UUID(project_id))
        ).all()
        return {(r.entity_type, r.ref): r for r in rows}

    def _embed_and_write(
        self,
        project_id: str,
        stale: list[tuple[IndexableNode, str]],
        existing: dict[tuple[str, str], SearchIndex],
    ) -> int:
        embedded = 0
        for start in range(0, len(stale), self._batch):
            chunk = stale[start:start + self._batch]
            vecs = self._embedder.embed([n.body for n, _ in chunk])
            embedded += len(chunk)
            for (node, h), vec in zip(chunk, vecs):
                self._upsert(project_id, node, h, vec, existing)
        return embedded

    def _upsert(
        self,
        project_id: str,
        node: IndexableNode,
        h: str,
        vec: Optional[list[float]],
        existing: dict[tuple[str, str], SearchIndex],
    ) -> None:
        key = (node.node_type, node.ref)
        row = existing.get(key)
        if row is None:
            row = SearchIndex(
                project_id=uuid.UUID(project_id), entity_type=node.node_type, ref=node.ref,
            )
            self._s.add(row)
            existing[key] = row
        row.title = node.title
        row.body = node.body
        row.content_hash = h
        row.updated_at = node.updated_at
        row.embedding = vec  # None → NULL（词法降级）；list[float] → Vector（Postgres）

    def _prune(
        self, existing: dict[tuple[str, str], SearchIndex], seen: set[tuple[str, str]]
    ) -> int:
        pruned = 0
        for key, row in existing.items():
            if key not in seen:
                self._s.delete(row)
                pruned += 1
        return pruned


def build_search_indexer(session: Session) -> SearchIndexer:
    """装配 indexer：RelationalSearchSource + 按配置的 embedder（供 worker/CLI/seed 复用）。"""
    return SearchIndexer(session, RelationalSearchSource(session), build_embedder(settings))
