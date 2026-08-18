"""材料接入路由（AEP-001 外化 + 结果查询读视图）。

2026-08-08 用户裁定路线 A：**三拍制保留**（提交→异步受理→查结论，AI 受理判断照常），
应答切换到 V2 信封——成功与业务拒绝同走 200、以 result 字段区分，错误走 4xx/5xx。
V1 原「前检不过混在 200 数据里返回 rejected_precheck」的形态归位为业务拒绝信封
（原因码「材料正文为空」「项目未选定」，正本见 docs/v2/design/业务拒绝原因码表.md）。
项目标识走路径参数，不再要求请求体重复携带。
AEP-002（模型回交）是内部端点，不暴露 HTTP。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.schemas import (
    BusinessRejection,
    BusinessRejectionEnvelope,
    IntakeReceipt,
    IntakeSubmitCommand,
    SuccessOfIntakeConclusion,
    SuccessOfIntakeReceipt,
    TextIntakeCommand,
)
from app.deps import get_service
from app.domain.enums import IntakeRequestStatus
from app.services.material_receiving import MaterialReceivingService

router = APIRouter(tags=["intake"])


def _reject(reason_code: str, message: str) -> BusinessRejectionEnvelope:
    return BusinessRejectionEnvelope(
        result="业务拒绝",
        rejection=BusinessRejection(category="业务拒绝", reason_code=reason_code, message=message),
    )


@router.post(
    "/projects/{project_id}/intake",
    response_model=SuccessOfIntakeReceipt | BusinessRejectionEnvelope,
    summary="提交材料接入",
    description=(
        "提交一份纯文本材料，进入异步受理判断（接收／退回补充／排除三值结论，三拍制，"
        "2026-08-08 用户裁定保留）。成功返回接入上下文与任务标识，结论经「读取接入结论」查询；"
        "同一幂等键重放返回同一上下文。正文为空或项目未选定返回业务拒绝信封（200）。"
    ),
)
def submit_intake(
    project_id: str,
    command: IntakeSubmitCommand,
    service: MaterialReceivingService = Depends(get_service),
) -> SuccessOfIntakeReceipt | BusinessRejectionEnvelope:
    if not command.text.strip():
        return _reject("材料正文为空", "请补充非空正文后重新提交")
    outcome = service.submit_text_intake(TextIntakeCommand(
        project_ref=project_id,
        raw_text=command.text,
        source_note=command.source_note,
        operator_ref=command.operator_ref,
        idempotency_key=command.idempotency_key,
    ))
    if outcome.status is IntakeRequestStatus.REJECTED_PRECHECK:
        # 正文非空已在上方拦截，走到这里的前检不过＝项目未选定/不存在。
        return _reject("项目未选定", outcome.next_action or "请选定项目后重新提交")
    return SuccessOfIntakeReceipt(
        result="成功",
        data=IntakeReceipt(context_ref=outcome.context_ref, agent_run_ref=outcome.agent_run_ref),
    )


@router.get(
    "/projects/{project_id}/intake/{context_ref}",
    response_model=SuccessOfIntakeConclusion,
    summary="读取接入结论",
    description=(
        "按接入上下文标识查询受理结论与依据；判断中、判断失败停靠、三值结论都是合法读取结果，"
        "结论为「接收」时返回正式材料标识。上下文不存在返回 404。"
    ),
)
def read_intake_result(
    project_id: str,
    context_ref: str,
    service: MaterialReceivingService = Depends(get_service),
) -> SuccessOfIntakeConclusion:
    return SuccessOfIntakeConclusion(result="成功", data=service.read_intake_result(context_ref))
