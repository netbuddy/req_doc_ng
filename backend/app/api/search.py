"""全局检索·跨项目端点（04 篇 §1）。

顶层端点（不在 /projects/{id} 下，语义即"跨项目检索"）。q 校验失败走 FastAPI 422；
无命中返回 200 + 空 groups（非 404）；embedding 端点不可达时服务内静默降级词法（不 500）。
单租户全可见，按项目授权过滤为遗留边界（04 §1）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.schemas import SearchResultsRead
from app.deps import get_search_service
from app.services.search import SearchService

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResultsRead)
def search(
    q: str = Query(..., min_length=1, max_length=120, description="检索词（自然语言或编号）"),
    types: str | None = Query(None, description="限定 entity_type 子集，CSV；缺省=全部五类"),
    limit: int = Query(8, ge=1, le=50, description="每类返回上限"),
    svc: SearchService = Depends(get_search_service),
) -> SearchResultsRead:
    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
    return svc.search(q, types=type_list, limit=limit)
