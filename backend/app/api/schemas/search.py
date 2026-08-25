"""全局检索（GET /api/search）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---- 全局检索（GET /api/search；04 篇 §2）----
class SearchHitRead(BaseModel):
    """单条命中。ref = 稳定语义引用（(asset_type, ref) 口径）→ 深链锚；
    workbench/label 由服务端派生（04 §3），前端不自行判定落点。"""

    project_id: str
    project_name: str          # 跨项目：面板按项目标注、导航切项目所需
    entity_type: str           # material|element|requirement_item|chart|document
    ref: str
    title: str
    snippet: str               # 匹配片段（服务端生成，03 §5）
    workbench: str             # 目标工作台 WorkbenchKey 码（management|diagram|release…）
    score: float               # RRF 融合分
    status: str | None = None


class SearchGroupRead(BaseModel):
    entity_type: str
    label: str                 # 中文组头，取 labels.SEARCH_ENTITY_GROUP_LABELS 单一来源
    hits: list[SearchHitRead] = Field(default_factory=list)
    total: int                 # 该类命中总数（可 > len(hits)）


class SearchResultsRead(BaseModel):
    query: str
    groups: list[SearchGroupRead] = Field(default_factory=list)
    total: int


class ChatTranscriptRowRead(BaseModel):
    """演示留痕一行（AI 对话演示简化方案 2026-07-18 §2.3 读点）。

    content 为已解析的 JSON 载荷：`{text}` 或找来源的 `{text, candidates:[...]}`。
    role/kind 供前端水合时映射气泡语气（role+kind→ChatMsg.kind / ChatMessage 部件）。
    """

    id: str
    channel: str
    context_ref: str
    role: str
    kind: str
    content: dict = Field(default_factory=dict)
    created_at: str


class ChatTranscriptRead(BaseModel):
    """按 (channel, context_ref) 拉取的留痕行（created_at 升序，(created_at, id) 消歧保序）。"""

    rows: list[ChatTranscriptRowRead] = Field(default_factory=list)
