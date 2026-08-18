"""检索源接口缝：IndexableNode（图形态中间体）+ SearchSourceProvider + RelationalSearchSource。

全局检索工作包 01 篇 §2-4 / 02 篇 §1-2。本模块是"事实源"与"检索层"之间**唯一**的数据契约：
检索层（indexer / SearchService / API / 前端）只认 iter_nodes() 产出的 IndexableNode，永不直接
触碰源表结构。**本文件是唯一允许 import 源表 ORM 的检索侧模块**（README 不变式 3）——换源即换
provider 实现（未来 GraphSearchSource），其余层零改动。

身份（01 §3）：IndexableNode.ref = 源实体资产寻址 ref（= 源实体 id，对齐
GET /projects/{id}/assets/{asset_type}/{ref}），稳定、跨索引重建不漂移，天然映射未来图节点 id。
唯一键 = (project_id, entity_type, ref)。

正文（02 §2）：body 走**全文**——直接读 raw_text/content/expression 全字段，绕开 AssetReadRepository
为列表展示做的 _head(…,200/400/800) 截断，避免长材料尾部关键词无法召回。type 码经 labels.py 单一
来源拼中文标签进 body（"流程图"能命中 flowchart 图表）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Material,
    Project,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
)
from app.domain.labels import (
    CHART_TYPE_GUIDE,
    element_type_entries,
    requirement_item_type_entries,
)

# 五类资产 entity_type 码（= asset_type 寻址口径 = IndexableNode.node_type = 未来图节点 label）。
ENTITY_TYPES: tuple[str, ...] = ("material", "element", "requirement_item", "chart", "document")

# 码 → 中文标签（labels.py 单一来源；拼进 body 让中文类型词也能命中）。
_ELEMENT_TYPE_LABELS: dict[str, str] = {e["code"]: e["label"] for e in element_type_entries()}
_REQ_TYPE_LABELS: dict[str, str] = {e["code"]: e["label"] for e in requirement_item_type_entries()}
_CHART_TYPE_LABELS: dict[str, str] = {d["code"]: d["label"] for d in CHART_TYPE_GUIDE}


@dataclass(frozen=True)
class IndexableNode:
    """源与检索层之间的唯一契约（图形态中间体）。字段刻意用图词汇命名，使 GraphSearchSource
    落地时是自然产出而非翻译适配（01 §2）。"""

    node_type: str                    # == entity_type，且 == 未来图节点 label
    ref: str                          # 稳定语义引用（源实体 id），禁用检索层派生 PK（01 §3）
    project_id: str
    title: str                        # 展示标题（短）
    body: str                         # 可搜正文（全文拼接）
    status: Optional[str] = None
    updated_at: Optional[datetime] = None
    # 前向挂钩：本包不消费，图谱落地后供 GraphRAG 邻域扩展（06 §6）
    edges: list[tuple[str, str]] = field(default_factory=list)  # (rel_type, target_ref)


class SearchSourceProvider(Protocol):
    """源接口缝：检索/索引服务只依赖本抽象，换源 = 只改 deps 注入的实现（01 §4）。"""

    def iter_nodes(self, project_id: str) -> Iterable[IndexableNode]: ...
    def iter_all_projects(self) -> Iterable[str]: ...


def _join(*parts: Optional[str]) -> str:
    """拼接非空片段为可搜正文（去空白、丢空段）。"""
    return " ".join(p.strip() for p in parts if p and p.strip())


def _first_line(text: Optional[str], limit: int) -> str:
    """取首行前 limit 字作展示标题（正文另走全文，不受此截断影响）。"""
    return (text or "").strip().replace("\n", " ")[:limit]


class RelationalSearchSource:
    """P1 实现：把五类关系源实体投影成 IndexableNode（全文 body / 稳定 ref）。

    直查五类 ORM 表（纯读投影，同 asset_read/overview_read 的合法形态）；body 读全文字段，
    不复用 ProjectAssetFacts 的截断投影（02 §2 方案 A）。
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def iter_all_projects(self) -> Iterable[str]:
        # 跨全部项目检索：不按状态过滤（含归档），全项目可见（单租户，权限过滤为遗留边界）。
        return [str(pid) for pid in self._s.scalars(select(Project.id)).all()]

    def iter_nodes(self, project_id: str) -> Iterable[IndexableNode]:
        pid = _as_uuid(project_id)
        if pid is None:
            return []
        nodes: list[IndexableNode] = []
        nodes.extend(self._materials(pid, project_id))
        nodes.extend(self._elements(pid, project_id))
        nodes.extend(self._items(pid, project_id))
        nodes.extend(self._charts(pid, project_id))
        nodes.extend(self._documents(pid, project_id))
        return nodes

    # ---- 逐实体投影（02 §1 口径）----

    def _materials(self, pid: uuid.UUID, project_id: str) -> list[IndexableNode]:
        rows = self._s.scalars(
            select(Material).where(Material.project_id == pid)
        ).all()
        return [
            IndexableNode(
                node_type="material", ref=str(m.id), project_id=project_id,
                title=(m.source_note or "").strip() or _first_line(m.raw_text, 40) or "（材料）",
                # 全文 raw_text（绕开 _head 200 截断）+ 来源标注
                body=_join(m.source_note, m.raw_text),
                status=m.content_form, updated_at=m.created_at,
            )
            for m in rows
        ]

    def _elements(self, pid: uuid.UUID, project_id: str) -> list[IndexableNode]:
        # 只投影活跃知识项：superseded=拆分/合并替代的死版本，索引它会造成陈旧重复命中。
        rows = self._s.scalars(
            select(RequirementElement).where(
                RequirementElement.project_id == pid,
                RequirementElement.superseded == False,  # noqa: E712 ORM 布尔比较
            )
        ).all()
        return [
            IndexableNode(
                node_type="element", ref=str(e.id), project_id=project_id,
                title=_first_line(e.content, 60) or "（知识项）",
                # 全文 content（绕开 _head 400）+ 校正依据 + element_type 中文标签
                body=_join(e.content, e.correction_note, _ELEMENT_TYPE_LABELS.get(e.element_type)),
                status=e.process_status, updated_at=e.updated_at,
            )
            for e in rows
        ]

    def _items(self, pid: uuid.UUID, project_id: str) -> list[IndexableNode]:
        rows = self._s.scalars(
            select(RequirementItem).where(RequirementItem.project_id == pid)
        ).all()
        return [
            IndexableNode(
                node_type="requirement_item", ref=str(i.id), project_id=project_id,
                title=_join(i.req_no, _first_line(i.expression, 60)),
                # 全文 expression（绕开 _head 800）+ req_no + 各撰写字段 + req_type 中文标签
                body=_join(
                    i.req_no, i.expression, i.curation_note, i.boundary_note,
                    i.verification_note, _REQ_TYPE_LABELS.get(i.req_type),
                ),
                status=i.status, updated_at=i.updated_at,
            )
            for i in rows
        ]

    def _charts(self, pid: uuid.UUID, project_id: str) -> list[IndexableNode]:
        rows = self._s.scalars(
            select(RequirementChart).where(RequirementChart.project_id == pid)
        ).all()
        return [
            IndexableNode(
                node_type="chart", ref=str(c.id), project_id=project_id,
                title=c.title or "（图表）",
                # 标题 + chart_type 中文标签（"流程图"命中 flowchart）+ chart_kind 码
                body=_join(c.title, _CHART_TYPE_LABELS.get(c.chart_type), c.chart_type, c.chart_kind),
                status=c.status, updated_at=c.updated_at,
            )
            for c in rows
        ]

    def _documents(self, pid: uuid.UUID, project_id: str) -> list[IndexableNode]:
        rows = self._s.scalars(
            select(RequirementDocument).where(RequirementDocument.project_id == pid)
        ).all()
        return [
            IndexableNode(
                node_type="document", ref=str(d.id), project_id=project_id,
                title=d.title or "（文档）",
                body=_join(d.title, d.doc_type, d.coverage_scope),
                status=d.status, updated_at=d.updated_at,
            )
            for d in rows
        ]


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None
