"""AEP-094 AI 效能按环节统计路由（模型推理结果仓储·统计读面）。

口径事实源：docs/40-detailed-design/shared/AI效能统计口径设计.md §6。
只读投影；项目不存在 → NotFound → 404；无窗口数据 → 零值 + rating=insufficient
（前端显示空态，不显示虚构比率，UINV-23）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.schemas import AiDeliveryFailureInstancesRead, AiEffectivenessRead
from app.deps import get_ai_effectiveness_service
from app.services.ai_effectiveness import AiEffectivenessService

router = APIRouter(tags=["ai-effectiveness"])


@router.get(
    "/projects/{project_id}/ai-effectiveness",
    response_model=AiEffectivenessRead,
)
def read_ai_effectiveness(
    project_id: str,
    window_days: int = Query(30, ge=1, le=365),
    svc: AiEffectivenessService = Depends(get_ai_effectiveness_service),
) -> AiEffectivenessRead:
    """按环节效果 / 置信度校准 / AI 覆盖 / 风险信号（窗口参数共享）。"""
    return svc.read(project_id, window_days=window_days)


@router.get(
    "/projects/{project_id}/ai-effectiveness/delivery-failures",
    response_model=AiDeliveryFailureInstancesRead,
)
def read_delivery_failure_instances(
    project_id: str,
    stage: str = Query(..., description="lane 稳定码（LDM-015.stage）"),
    failure_stage: str | None = Query(None, description="失败关卡过滤（含 unclassified）"),
    window_days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    svc: AiEffectivenessService = Depends(get_ai_effectiveness_service),
) -> AiDeliveryFailureInstancesRead:
    """交付失败个案钻取（口径 §5.5）：某 lane[×失败关卡] 的失败行明细，接运行态跟进。"""
    return svc.delivery_failure_instances(
        project_id, stage, failure_stage=failure_stage,
        window_days=window_days, limit=limit,
    )
