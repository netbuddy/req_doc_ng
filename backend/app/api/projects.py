"""项目管理路由（2026-08-07 项目管理组重构：整组切换到 V2 应答信封）。

响应约定＝V2 治理接口的应答信封（api/openapi.yaml 项目四操作）：成功与业务拒绝
同走 200、以 result 字段区分，错误走 4xx/5xx。这是继材料列表之后第一组整体
信封化的存量接口；裁定依据＝docs/v2/drafts/项目管理字段级差异表-讨论稿.md。

四操作分工：列表只回摘要（标识、名称、创建时刻），详情走单读；创建带操作者与
幂等键（同键重放返回同一项目）；删除记操作者入结构化日志，项目内有执行中 AI
任务时以业务拒绝信封拒绝（原因码「项目内存在执行中任务」）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.schemas import (
    BusinessRejection,
    BusinessRejectionEnvelope,
    CreateProjectCommand,
    ProjectSummary,
    SuccessOfProjectDeletion,
    SuccessOfProjectDetail,
    SuccessOfProjectList,
    ProjectDeletionReport,
)
from app.deps import get_project_delete_service, get_project_service
from app.services.project_context import ProjectContextService
from app.services.project_delete import InFlightTasksBlockDeletion, ProjectDeleteService

router = APIRouter(tags=["projects"])


@router.get(
    "/projects",
    response_model=SuccessOfProjectList,
    summary="列出全部项目",
    description="返回全部项目的摘要列表（标识、名称、创建时刻），按创建时刻升序。详情经「读单个项目」获取。",
)
def list_projects(
    svc: ProjectContextService = Depends(get_project_service),
) -> SuccessOfProjectList:
    return SuccessOfProjectList(
        result="成功",
        data=[
            ProjectSummary(project_id=r.id, name=r.name, created_at=r.created_at or "")
            for r in svc.list_projects()
        ],
    )


@router.get(
    "/projects/{project_id}",
    response_model=SuccessOfProjectDetail,
    summary="读单个项目",
    description="按项目标识返回项目详情（名称、范围、背景、领域档案与其显示名、创建时刻）。项目不存在返回 404。",
)
def get_project(
    project_id: str,
    svc: ProjectContextService = Depends(get_project_service),
) -> SuccessOfProjectDetail:
    return SuccessOfProjectDetail(result="成功", data=svc.get_project(project_id))


@router.post(
    "/projects",
    response_model=SuccessOfProjectDetail,
    summary="创建项目",
    description="创建业务项目并返回其详情。请求须带操作者标识与幂等键；同一幂等键重放返回同一项目，不重复创建。空名返回 400。",
)
def create_project(
    command: CreateProjectCommand,
    svc: ProjectContextService = Depends(get_project_service),
) -> SuccessOfProjectDetail:
    return SuccessOfProjectDetail(result="成功", data=svc.create_project(command))


@router.delete(
    "/projects/{project_id}",
    response_model=SuccessOfProjectDeletion | BusinessRejectionEnvelope,
    summary="删除项目",
    description=(
        "应用层级联删净（单事务），返回删除清点回执。项目不存在返回 404；"
        "项目内有执行中的 AI 任务时返回业务拒绝信封（200，原因码「项目内存在执行中任务」）。"
        "操作者标识经查询参数传入，记入结构化日志。"
    ),
)
def delete_project(
    project_id: str,
    operator_ref: str = Query(description="操作者标识——删除发起者，记入结构化日志。"),
    svc: ProjectDeleteService = Depends(get_project_delete_service),
) -> SuccessOfProjectDeletion | BusinessRejectionEnvelope:
    try:
        outcome = svc.delete_project(project_id, operator_ref=operator_ref)
    except InFlightTasksBlockDeletion as exc:
        return BusinessRejectionEnvelope(
            result="业务拒绝",
            rejection=BusinessRejection(
                category="业务拒绝",
                reason_code="项目内存在执行中任务",
                message=str(exc),
                details={"inflight_count": exc.count},
            ),
        )
    return SuccessOfProjectDeletion(
        result="成功",
        data=ProjectDeletionReport(
            project_id=outcome.project_ref,
            project_name=outcome.project_name,
            deleted_rows=outcome.deleted_rows,
            table_counts=outcome.table_counts,
            files_deleted=outcome.files_deleted,
            files_failed=outcome.files_failed,
        ),
    )
