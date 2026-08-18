"""条目评审路由（SCN-003 v5：AEP-032 诊断 + AEP-033 工作区读 + AEP-034 结论裁决 + AEP-095 对话
+ AEP-116 问题否决 + 覆盖确认/撤回）。

响应约定同 item_formation：2xx 裸 DTO；业务结局在 status/next_action；
默认拒绝/不存在经异常处理器 → 409/404。
结论与草案不直接改 LDM-007；修订应用走 AEP-036（item_formation 路由或采纳副作用链内联）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.schemas import (
    FindingVetoCommand,
    ItemConfirmationCommand,
    ItemConfirmationResult,
    ItemQualityRead,
    ItemReviewDiagnosisCommand,
    ItemReviewDiagnosisRequestResult,
    ItemReviewWorkspaceRead,
    ItemWithdrawCommand,
    ItemWithdrawResult,
    ReviewDialogueCommand,
    ReviewDialogueResult,
    SourceAttestationCommand,
    VerdictAdjudicationCommand,
)
from app.api import transcript as tx
from app.api.sse_dialogue import stream_dialogue, wants_event_stream
from app.deps import get_item_review_service
from app.domain.errors import DomainError
from app.services.item_review import ItemReviewService

router = APIRouter(tags=["item-review"])


@router.post(
    "/projects/{project_id}/item-reviews/diagnosis",
    response_model=ItemReviewDiagnosisRequestResult,
)
def start_item_diagnosis(
    project_id: str,
    command: ItemReviewDiagnosisCommand,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemReviewDiagnosisRequestResult:
    """AEP-032：提交评审诊断请求（批次级提交、条目级处理、条目级结论实时返回）。"""
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    return service.start_item_diagnosis(command)


@router.get(
    "/projects/{project_id}/item-reviews/{review_context_ref}",
    response_model=ItemReviewWorkspaceRead,
)
def read_item_review_workspace(
    project_id: str,
    review_context_ref: str,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemReviewWorkspaceRead:
    """AEP-033：评审工作区读视图（线程/会话条/动态流三投影的唯一素材）。"""
    return service.read_item_review_workspace(review_context_ref)


@router.get(
    "/projects/{project_id}/requirement-items/{item_ref}/quality",
    response_model=ItemQualityRead,
)
def read_item_quality(
    project_id: str,
    item_ref: str,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemQualityRead:
    """AEP-105：条目质量投影（最新一轮诊断的 6 维评分 / span 标注 / EARS / 逐源对齐分）。

    v2 详情卡「质量诊断」页签数据源；无诊断轮次 → has_diagnosis:false 空投影（不伪造评分）。
    """
    return service.read_item_quality(project_id, item_ref)


@router.post(
    "/projects/{project_id}/item-reviews/rounds/{round_ref}/adjudication",
    response_model=ItemReviewWorkspaceRead,
)
def adjudicate_verdict(
    project_id: str,
    round_ref: str,
    command: VerdictAdjudicationCommand,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemReviewWorkspaceRead:
    """AEP-034（v5）：结论裁决——采纳（副作用链按状态字原子执行）或拒绝（理由必填）。"""
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    if command.round_ref != round_ref:
        raise HTTPException(status_code=400, detail="path round_ref 与 body.round_ref 不一致")
    return service.adjudicate_verdict(command)


@router.post(
    "/projects/{project_id}/requirement-items/{item_ref}/finding-vetoes",
    response_model=ItemReviewWorkspaceRead,
)
def record_finding_veto(
    project_id: str,
    item_ref: str,
    command: FindingVetoCommand,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemReviewWorkspaceRead:
    """AEP-116：把一条诊断问题裁定为「不是问题」（action=veto），或撤销该裁定（action=restore）。

    否决登记的是问题指纹（规则码 + 证据片段），此后所有轮次里同一个问题都不再计入阻断；
    判定是确定性字符串比对，不经模型。
    """
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    if command.item_ref != item_ref:
        raise HTTPException(status_code=400, detail="path item_ref 与 body.item_ref 不一致")
    return service.record_finding_veto(command)


@router.post(
    "/projects/{project_id}/requirement-items/{item_ref}/source-attestation",
    response_model=ItemReviewWorkspaceRead,
)
def attest_source(
    project_id: str,
    item_ref: str,
    command: SourceAttestationCommand,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemReviewWorkspaceRead:
    """AEP-117：人工确认背书——材料里漏写了这条，人工确认它是真实需求，条目据此离开「待补充来源」。

    对「条目的依据必须能在材料里指出来」的授权例外，故理由必填、全程留痕。服务端只登记
    背书事实——不写任何材料锚点、不生成引文、不改条目的来源要素。不设撤销动作。
    """
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    if command.item_ref != item_ref:
        raise HTTPException(status_code=400, detail="path item_ref 与 body.item_ref 不一致")
    return service.attest_source(command)


@router.post(
    "/projects/{project_id}/item-reviews/dialogue",
    response_model=ReviewDialogueResult,
)
def review_dialogue(
    project_id: str,
    command: ReviewDialogueCommand,
    request: Request,
    service: ItemReviewService = Depends(get_item_review_service),
):
    """AEP-095：评审对话（解释/草案/轻量重评三出口 + 斜杠命令解释派发）。

    `Accept: text/event-stream` 时流式推送 stage 帧 + result 终帧（链路回执条数据源）。
    """
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    # 演示留痕（按结果条件写，受理时无法预知出口）：user 行 created_at 用入口时刻，保证命令排在其副作用卡之前。
    received_at = datetime.now(timezone.utc)
    if not wants_event_stream(request):
        try:
            result = service.review_dialogue(command)
        except DomainError as exc:
            tx.record_review_failure(project_id, command.item_ref, command, str(exc), received_at)
            raise
        tx.record_review_success(project_id, command.item_ref, command, result, received_at)
        return result

    def run(on_stage):
        from app.deps import _build_async_item_review_service, new_session

        session = new_session()
        try:
            result = _build_async_item_review_service(session).review_dialogue(command, on_stage=on_stage)
            session.commit()
            # 助手行按结果条件写（COMMAND 写、EXPLANATION/DRAFT/REEVAL 由投影重放不写），独立短 session
            tx.record_review_success(project_id, command.item_ref, command, result, received_at)
            return result
        except DomainError as exc:
            session.rollback()
            tx.record_review_failure(project_id, command.item_ref, command, str(exc), received_at)
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return stream_dialogue(run)


@router.post(
    "/projects/{project_id}/requirements/{item_ref}/confirm",
    response_model=ItemConfirmationResult,
)
def confirm_requirement(
    project_id: str,
    item_ref: str,
    command: ItemConfirmationCommand,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemConfirmationResult:
    """AEP-037（v5）：直写确认的两条通道——覆盖确认（override=True，理由必填），以及
    否决消解后的直接确认（AEP-116：站立「建议修订」结论所报问题已被逐条裁定为不是问题，
    服务端重新核算该谓词，不采信前端判断）。常规确认走 AEP-034 采纳「建议通过」。"""
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    if command.item_ref != item_ref:
        raise HTTPException(status_code=400, detail="path item_ref 与 body.item_ref 不一致")
    return service.confirm_item(command)


@router.post(
    "/projects/{project_id}/requirements/{item_ref}/withdraw",
    response_model=ItemWithdrawResult,
)
def withdraw_requirement(
    project_id: str,
    item_ref: str,
    command: ItemWithdrawCommand,
    service: ItemReviewService = Depends(get_item_review_service),
) -> ItemWithdrawResult:
    """人工撤回：待确认 → 已终止（理由必填、留痕）。"""
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    if command.item_ref != item_ref:
        raise HTTPException(status_code=400, detail="path item_ref 与 body.item_ref 不一致")
    return service.withdraw_item(command)
