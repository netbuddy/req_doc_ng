"""需求资产目录服务·资产读侧路由（04A §5 资产树/详情 + §3.1 维护列表，只读投影）。

响应约定同 shared/前端契约适配（2xx 裸 DTO）；项目/资产不存在 → NotFound → 404。
边界（UINV-09）：本路由只读；追溯诊断/补全归追溯分析工作台，正式写入归各写服务。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.schemas import (
    AssetCatalogRead,
    AssetDetailRead,
    BusinessKnowledgeListRead,
    ItemMaintenanceCardRead,
    ItemMaintenanceListRead,
)
from app.deps import get_asset_catalog_service
from app.services.asset_catalog import AssetCatalogService

router = APIRouter(tags=["assets"])


@router.get("/projects/{project_id}/assets/catalog", response_model=AssetCatalogRead)
def read_asset_catalog(
    project_id: str,
    svc: AssetCatalogService = Depends(get_asset_catalog_service),
) -> AssetCatalogRead:
    """项目级资产树：按资产类型分组的只读目录 + 追溯状态小计。"""
    return svc.read_catalog(project_id)


@router.get(
    "/projects/{project_id}/assets/business-knowledge",
    response_model=BusinessKnowledgeListRead,
)
def list_business_knowledge(
    project_id: str,
    element_type: str | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None, max_length=120),
    svc: AssetCatalogService = Depends(get_asset_catalog_service),
) -> BusinessKnowledgeListRead:
    """AEP-104 业务知识清单：只读列出业务领域知识翼要素（术语/假设/角色/外部系统…），
    按 element_type/状态/关键词过滤。翼过滤派生自单一来源，element_type 限业务翼成员。"""
    return svc.list_business_knowledge(
        project_id, element_type=element_type, status=status, search=search,
    )


@router.get(
    "/projects/{project_id}/assets/{asset_type}/{ref}",
    response_model=AssetDetailRead,
)
def read_asset_detail(
    project_id: str,
    asset_type: str,
    ref: str,
    svc: AssetCatalogService = Depends(get_asset_catalog_service),
) -> AssetDetailRead:
    """选中资产的摘要、状态、来源与下游引用（只呈现已有事实和派生摘要）。"""
    return svc.read_asset_detail(project_id, asset_type, ref)


@router.get(
    "/projects/{project_id}/requirement-items",
    response_model=ItemMaintenanceListRead,
)
def list_requirement_items(
    project_id: str,
    status: str | None = Query(None),
    req_type: str | None = Query(None),
    search: str | None = Query(None, max_length=120),
    gap: str | None = Query(None, pattern="^(verification_note|priority)$"),
    svc: AssetCatalogService = Depends(get_asset_catalog_service),
) -> ItemMaintenanceListRead:
    """维护列表：按状态/语义类型/关键词过滤的需求条目；gap 筛出缺验收准则/缺优先级条目。"""
    return svc.list_requirement_items(
        project_id, status=status, req_type=req_type, search=search, gap=gap,
    )


@router.get(
    "/projects/{project_id}/requirement-items/{item_ref}",
    response_model=ItemMaintenanceCardRead,
)
def read_item_card(
    project_id: str,
    item_ref: str,
    svc: AssetCatalogService = Depends(get_asset_catalog_service),
) -> ItemMaintenanceCardRead:
    """需求卡片：选中条目详情、来源依据、修订留痕与关联计数。"""
    return svc.read_item_card(project_id, item_ref)
