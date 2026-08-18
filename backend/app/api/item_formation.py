"""条目形成路由（SCN-002-P01：AEP-038 批次 + 工作区读视图 + AEP-036 待确认字段修订）。

响应约定同 analysis：2xx 裸 DTO；业务结局在 status/next_action；
默认拒绝/不存在经异常处理器 → 409/404。
AEP-038 不承接字段修订；字段修订命令只进 AEP-036（需求条目服务）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.schemas import (
    FormationDialogueCommand,
    FormationDialogueResult,
    ItemFormationWorkspaceRead,
    ItemizationBatchCommand,
    ItemizationBatchRequestResult,
    ItemRevisionCommand,
    ItemRevisionResult,
    RequirementConventionCatalogRead,
    StructureRecheckCommand,
    StructureRecheckOutcomeRead,
    StructureRecheckRequestResult,
)
from app.api import transcript as tx
from app.api.sse_dialogue import stream_dialogue, wants_event_stream
from app.deps import get_item_formation_service, get_requirement_item_service
from app.services.item_formation import ItemFormationService, RequirementItemService

router = APIRouter(tags=["item-formation"])


@router.get(
    "/requirement-conventions",
    response_model=RequirementConventionCatalogRead,
)
def list_requirement_conventions(
    service: ItemFormationService = Depends(get_item_formation_service),
) -> RequirementConventionCatalogRead:
    """AEP-102：需求规约方案目录（全局只读；设置页详情与区2 徽标数据源，含当前生效方案）。"""
    return service.list_requirement_conventions()


@router.post(
    "/projects/{project_id}/item-formation/dialogue",
    response_model=FormationDialogueResult,
)
def formation_dialogue(
    project_id: str,
    command: FormationDialogueCommand,
    request: Request,
    service: ItemFormationService = Depends(get_item_formation_service),
):
    """AEP-097 区5 对话命令解释：命令词确定性解析 + LLM 正文解释 + 校验派发既有端点逻辑。

    body 锚定 parse_result_ref（/生成条目 须在 formation_context_ref 存在之前可用）。
    `Accept: text/event-stream` 时流式推送 stage 帧 + result 终帧（链路回执条数据源）；
    流式分支自建 session（服务在独立线程执行），不复用请求级依赖注入的 session。
    """
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    # 演示留痕：受理即写 user 行（键＝parse_result_ref，与水合键同源）
    tx.record_user_message(
        channel=tx.CHANNEL_FORMATION, project_ref=project_id,
        context_ref=command.parse_result_ref, message=command.message,
    )
    if not wants_event_stream(request):
        result = service.formation_dialogue(command)
        tx.record_formation_assistant(project_id, command.parse_result_ref, result)
        return result

    def run(on_stage):
        from app.deps import _build_async_item_formation_service, new_session

        session = new_session()
        try:
            result = _build_async_item_formation_service(session).formation_dialogue(
                command, on_stage=on_stage
            )
            session.commit()
            # 助手终态行：服务事务提交后，独立短 session 写
            tx.record_formation_assistant(project_id, command.parse_result_ref, result)
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return stream_dialogue(run)


@router.post(
    "/projects/{project_id}/item-formation/batches",
    response_model=ItemizationBatchRequestResult,
)
def start_itemization_batch(
    project_id: str,
    command: ItemizationBatchCommand,
    service: ItemFormationService = Depends(get_item_formation_service),
) -> ItemizationBatchRequestResult:
    """AEP-038：发起条目化批次（全部/勾选子集/单个；受理立即返回，批次经 AgentRun 追踪）。"""
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    return service.start_element_itemization_batch(command)


@router.post(
    "/projects/{project_id}/item-formation/structure-rechecks",
    response_model=StructureRecheckRequestResult,
)
def start_structure_recheck(
    project_id: str,
    command: StructureRecheckCommand,
    service: ItemFormationService = Depends(get_item_formation_service),
) -> StructureRecheckRequestResult:
    """AEP-114：结构复核批次受理（只判不改；item_refs 空=默认目标集＝待确认∩(修订后未复核∪无体检结果)；
    受理立即返回，逐条目异步经 AgentRun 追踪，完成后工作区刷新可见）。"""
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    return service.start_structure_recheck(command)


@router.get(
    "/projects/{project_id}/item-formation/structure-rechecks/{recheck_context_ref}",
    response_model=StructureRecheckOutcomeRead,
)
def read_structure_recheck_outcome(
    project_id: str,
    recheck_context_ref: str,
    service: ItemFormationService = Depends(get_item_formation_service),
) -> StructureRecheckOutcomeRead:
    """AEP-114 读侧：复核批次逐条目结局（已重判/已过期跳过/失败保留旧判/离开流程跳过；
    终态回执两集合口径的事实源，前端在 AgentRun 终态后取一次）。"""
    return service.read_structure_recheck_outcome(recheck_context_ref, project_ref=project_id)


@router.get(
    "/projects/{project_id}/item-formation/by-parse-result/{parse_result_ref}",
    response_model=ItemFormationWorkspaceRead,
)
def read_latest_formation_workspace(
    project_id: str,
    parse_result_ref: str,
    service: ItemFormationService = Depends(get_item_formation_service),
) -> ItemFormationWorkspaceRead:
    """回放该解析结果最近一次批次的形成工作区（无批次时 404；形成页找回既有待确认条目）。"""
    return service.read_latest_workspace_of_parse_result(parse_result_ref)


@router.get(
    "/projects/{project_id}/item-formation/{formation_context_ref}",
    response_model=ItemFormationWorkspaceRead,
)
def read_item_formation_workspace(
    project_id: str,
    formation_context_ref: str,
    service: ItemFormationService = Depends(get_item_formation_service),
) -> ItemFormationWorkspaceRead:
    """条目形成工作区读视图（五区同一 workspace_version；批次/修订后由此刷新）。"""
    return service.read_item_formation_workspace(formation_context_ref)


@router.post(
    "/projects/{project_id}/requirements/{item_ref}/revision",
    response_model=ItemRevisionResult,
)
def apply_item_revision(
    project_id: str,
    item_ref: str,
    command: ItemRevisionCommand,
    service: RequirementItemService = Depends(get_requirement_item_service),
) -> ItemRevisionResult:
    """AEP-036：待确认条目字段修订（manual/采纳/修订采纳/拒绝建议；修订后仍待确认）。"""
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    if command.item_ref != item_ref:
        raise HTTPException(status_code=400, detail="path item_ref 与 body.item_ref 不一致")
    return service.apply_item_revision(command)
