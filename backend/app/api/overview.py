"""需求资产目录服务路由（AEP-052 资产盘点 / AEP-072 跨任务状态聚合，只读投影）。

响应约定同 shared/前端契约适配（2xx 裸 DTO）；项目不存在 → NotFound → 404。
总览台边界（UINV-21/22）：投影只读；唯一例外是终结态处置（OVW-001 修订 2026-07-10）——
AEP-111 放弃本次接入（软删过程记录展示位，不碰需求事实/门禁）+ AEP-112 继续编辑预填读。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.schemas import (
    FlowDismissCommand,
    FlowDismissRead,
    IntakePrefillRead,
    OverviewRead,
    RequirementFlowRead,
)
from app.deps import get_overview_service
from app.services.overview import OverviewService

router = APIRouter(tags=["overview"])


@router.get("/projects/{project_id}/overview", response_model=OverviewRead)
def read_project_overview(
    project_id: str,
    svc: OverviewService = Depends(get_overview_service),
) -> OverviewRead:
    """总览台单次往返：资产/类型/状态计数 + 流程阶段投影。"""
    return svc.read_project_overview(project_id)


@router.get(
    "/projects/{project_id}/requirement-flows",
    response_model=list[RequirementFlowRead],
)
def list_requirement_flows(
    project_id: str,
    svc: OverviewService = Depends(get_overview_service),
) -> list[RequirementFlowRead]:
    """精简别名：仅流程投影（需求管理工作台恢复入口用）。"""
    return svc.list_requirement_flows(project_id)


@router.get(
    "/projects/{project_id}/requirement-flows/{context_ref}/intake-prefill",
    response_model=IntakePrefillRead,
)
def read_intake_prefill(
    project_id: str,
    context_ref: str,
    svc: OverviewService = Depends(get_overview_service),
) -> IntakePrefillRead:
    """AEP-112 继续编辑预填：读终结态旧上下文的 raw_text/source_note（重提走 AEP-001）。"""
    return svc.read_intake_prefill(project_id, context_ref)


@router.post(
    "/projects/{project_id}/requirement-flows/{context_ref}/dismiss",
    response_model=FlowDismissRead,
)
def dismiss_requirement_flow(
    project_id: str,
    context_ref: str,
    command: FlowDismissCommand,
    svc: OverviewService = Depends(get_overview_service),
) -> FlowDismissRead:
    """AEP-111 放弃本次接入（软删）：仅终结态可放弃；非终结态 → RejectedTransition → 409。"""
    return svc.dismiss_intake_flow(project_id, context_ref, command.operator_ref)
