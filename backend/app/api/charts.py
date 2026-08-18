"""图表协同路由（SCN-004：P01 受控图表创建与编辑循环 + P02 核对确认与追溯正式确立）。

响应约定同 analysis：2xx 裸 DTO；业务结局在 status/next_action；
默认拒绝/不存在/入参问题经异常处理器 → 409/404/400。
AI 建议与图文核对为 AgentRun 异步；结果登记 LDM-015 后经工作区读视图呈现。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import (
    ChartConfirmationCommand,
    ChartConfirmationResult,
    ChartCreateCommand,
    ChartCreateResult,
    ChartEligibleSourceListRead,
    ChartFindingDecisionCommand,
    ChartIssueCommand,
    ChartLifecycleCommand,
    ChartListRead,
    ChartSourceChangeCommand,
    ChartSuggestionCommand,
    ChartSuggestionHandlingCommand,
    ChartSuggestionRequestResult,
    ChartVerificationCommand,
    ChartVerificationRequestResult,
    ChartWorkspaceRead,
    IssueListRead,
    IssueRead,
    TraceLinkListRead,
)
from app.deps import get_chart_collaboration_service
from app.services.chart_collaboration import ChartCollaborationService

router = APIRouter(tags=["charts"])


def _check_project(project_id: str, project_ref: str) -> None:
    if project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")


@router.get("/projects/{project_id}/charts", response_model=ChartListRead)
def list_charts(
    project_id: str,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartListRead:
    """项目图表列表（图表设计工作台区1）。"""
    return service.list_charts(project_id)


@router.get(
    "/projects/{project_id}/charts/eligible-sources",
    response_model=ChartEligibleSourceListRead,
)
def list_eligible_sources(
    project_id: str,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartEligibleSourceListRead:
    """图表创建候选来源（仅确认态需求条目；来源准入的读侧呈现）。"""
    return service.list_eligible_sources(project_id)


@router.post("/projects/{project_id}/charts", response_model=ChartCreateResult)
def create_chart(
    project_id: str,
    command: ChartCreateCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartCreateResult:
    """P01-N01~N05：创建图表草稿壳并自动预建立追溯（无来源不得创建）。"""
    _check_project(project_id, command.project_ref)
    return service.create_chart(command)


@router.get("/projects/{project_id}/charts/{chart_ref}", response_model=ChartWorkspaceRead)
def read_chart_workspace(
    project_id: str,
    chart_ref: str,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartWorkspaceRead:
    """图表工作区读视图（源码/来源/追溯/建议/核对/门禁单次往返）。"""
    return service.read_chart_workspace(chart_ref)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/source",
    response_model=ChartWorkspaceRead,
)
def apply_source_change(
    project_id: str,
    chart_ref: str,
    command: ChartSourceChangeCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartWorkspaceRead:
    """P01-N07/N10/N11：人工源码变更应用（受控校验 + 追溯同步；待确认已冻结编辑）。"""
    _check_project(project_id, command.project_ref)
    return service.apply_source_change(chart_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/suggestions",
    response_model=ChartSuggestionRequestResult,
)
def request_chart_suggestion(
    project_id: str,
    chart_ref: str,
    command: ChartSuggestionCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartSuggestionRequestResult:
    """P01-N08：AI 源码建议请求（异步；建议登记 LDM-015 后待人工采纳）。"""
    _check_project(project_id, command.project_ref)
    return service.request_chart_suggestion(chart_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/suggestions/{suggestion_ref}/handle",
    response_model=ChartWorkspaceRead,
)
def handle_chart_suggestion(
    project_id: str,
    chart_ref: str,
    suggestion_ref: str,
    command: ChartSuggestionHandlingCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartWorkspaceRead:
    """P01-N09：AI 建议处理（采纳/修订采纳仍需受控校验；拒绝必填理由且不改图表）。"""
    _check_project(project_id, command.project_ref)
    return service.handle_chart_suggestion(chart_ref, suggestion_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/verification",
    response_model=ChartVerificationRequestResult,
)
def start_chart_verification(
    project_id: str,
    chart_ref: str,
    command: ChartVerificationCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartVerificationRequestResult:
    """P02-N01：核对发起（草稿→待确认冻结编辑；重核走待确认自环）。"""
    _check_project(project_id, command.project_ref)
    return service.start_chart_verification(chart_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/findings/{finding_ref}/decision",
    response_model=ChartWorkspaceRead,
)
def submit_chart_finding_decision(
    project_id: str,
    chart_ref: str,
    finding_ref: str,
    command: ChartFindingDecisionCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartWorkspaceRead:
    """P02-N05：发现项复核（接受/拒绝；拒绝必须记录理由；不直接写正式状态）。"""
    _check_project(project_id, command.project_ref)
    return service.submit_chart_finding_decision(chart_ref, finding_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/confirm",
    response_model=ChartConfirmationResult,
)
def confirm_chart(
    project_id: str,
    chart_ref: str,
    command: ChartConfirmationCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartConfirmationResult:
    """P02-N06/N08/N09：确认准入裁定 + 图表确认与追溯正式确立（同批成立）。"""
    _check_project(project_id, command.project_ref)
    return service.confirm_chart(chart_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/return-for-revision",
    response_model=ChartWorkspaceRead,
)
def return_chart_for_revision(
    project_id: str,
    chart_ref: str,
    command: ChartLifecycleCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartWorkspaceRead:
    """P02-N07：退回修订（相关追溯关系标记待补全；旧核对轮次失效）。"""
    _check_project(project_id, command.project_ref)
    return service.return_chart_for_revision(chart_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/void",
    response_model=ChartWorkspaceRead,
)
def void_chart(
    project_id: str,
    chart_ref: str,
    command: ChartLifecycleCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartWorkspaceRead:
    """P02-N07：作废图表（相关追溯关系失效）。"""
    _check_project(project_id, command.project_ref)
    return service.void_chart(chart_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/resume-editing",
    response_model=ChartWorkspaceRead,
)
def resume_chart_editing(
    project_id: str,
    chart_ref: str,
    command: ChartLifecycleCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> ChartWorkspaceRead:
    """退回修订 → 重回源码编辑循环（待补全追溯随之重新同步）。"""
    _check_project(project_id, command.project_ref)
    return service.resume_chart_editing(chart_ref, command)


@router.post(
    "/projects/{project_id}/charts/{chart_ref}/findings/{finding_ref}/issue",
    response_model=IssueRead,
)
def create_issue_from_finding(
    project_id: str,
    chart_ref: str,
    finding_ref: str,
    command: ChartIssueCommand,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> IssueRead:
    """P02-N07：转问题项（LDM-011 最小实现；相关追溯保持未确认并关联问题项）。"""
    _check_project(project_id, command.project_ref)
    return service.create_issue_from_finding(chart_ref, finding_ref, command)


@router.get("/projects/{project_id}/issues", response_model=IssueListRead)
def list_issues(
    project_id: str,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> IssueListRead:
    """问题项列表（本迭代只读；处置闭环归 SCN-006）。"""
    return service.list_issues(project_id)


@router.get("/projects/{project_id}/trace-links", response_model=TraceLinkListRead)
def list_trace_links(
    project_id: str,
    status: str | None = None,
    chart_ref: str | None = None,
    service: ChartCollaborationService = Depends(get_chart_collaboration_service),
) -> TraceLinkListRead:
    """追溯关系列表（区分预建立/有效/可疑待复核/失效；预建立不得作为正式依据消费）。"""
    return service.list_trace_links(project_id, status, chart_ref)
