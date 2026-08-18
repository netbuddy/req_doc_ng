"""Embedding 适配器 —— 隔离外部嵌入服务（OpenAI 兼容 POST /embeddings）。

全局检索工作包 02 篇 §4 / 06 篇 §3。仿 adapters/llm.py：httpx base_url/api_key/timeout。
降级底线（README 不变式 7）：无 EMBEDDING_BASE_URL → StubEmbedder（全 None）；端点不可达/结果不可解析
→ 记 WARN 后返回 None（不 500、不抛），search_index.embedding 留 NULL，检索静默降级纯词法。
密钥（api_key）只进请求头，绝不落属性名文/日志（AGENTS.md 硬规则 8）。
"""
from __future__ import annotations

from typing import Optional, Protocol

import httpx

from app.config import Settings
from app.log import log_event

_COMPONENT = "embedding-adapter"

# 返回值：每条文本对应一个向量或 None（None=该条无向量，检索走词法）。
Vector = list[float]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[Optional[Vector]]: ...


class EmbeddingClient:
    """最小 OpenAI 兼容嵌入客户端。base_url 需含 /v1（如 http://host:8080/v1）。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int,
        timeout: float = 30.0,
        api_key: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._model = model
        self._dim = dim
        # 密钥只进请求头，不落属性名文/日志（硬规则 8）。
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout, headers=headers)

    def embed(self, texts: list[str]) -> list[Optional[Vector]]:
        if not texts:
            return []
        try:
            resp = self._client.post("/embeddings", json={"model": self._model, "input": texts})
            resp.raise_for_status()
            data = resp.json()["data"]
            # OpenAI 约定 data 按 index 排序；防御式按 index 归位。
            ordered: list[Optional[Vector]] = [None] * len(texts)
            for item in data:
                idx = int(item.get("index", 0))
                vec = item.get("embedding")
                if isinstance(vec, list) and 0 <= idx < len(texts):
                    if len(vec) == self._dim:
                        ordered[idx] = [float(x) for x in vec]
                    else:
                        # 维度不符 = 配置与模型不一致；留 None 走词法，避免向量列写入被 pgvector 拒。
                        log_event(
                            _COMPONENT, "embedding.dim.mismatch", level="WARN",
                            expected_dim=self._dim, got_dim=len(vec),
                        )
            return ordered
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            # 不外泄 texts 原文/异常细节；静默降级（不阻断 indexer）。
            log_event(
                _COMPONENT, "embedding.request.failed", level="WARN",
                error_code=type(exc).__name__, count=len(texts),
            )
            return [None] * len(texts)


class StubEmbedder:
    """无 EMBEDDING_BASE_URL / 测试用：全 None → 索引 embedding 留空 → 检索走词法。"""

    def embed(self, texts: list[str]) -> list[Optional[Vector]]:
        return [None] * len(texts)


def build_embedder(settings: Settings) -> Embedder:
    """按配置装配 embedder：设了 base_url → 真客户端；否则 StubEmbedder（沿用代码库 Stub* 纪律）。"""
    if settings.embedding_base_url:
        return EmbeddingClient(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
            timeout=settings.embedding_timeout,
            api_key=settings.embedding_api_key,
        )
    return StubEmbedder()
