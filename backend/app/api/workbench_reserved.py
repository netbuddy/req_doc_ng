"""v2 需求管理工作台预留接口（AEP-106/107/108）。

追溯覆盖矩阵 / AI 评审副驾聚合 / 变更影响·返工风险预测三模块本轮仅预留：类型就位、返回
deferred 占位，前端 DeferredBadge 呈现，后端后续 drop-in（仿 overview deferredNote）。
不查库、不造假数据（v2 方案 04 篇 §2；README §5 非覆盖纪律：全新文件，不碰既有资产/追溯读侧）。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import WorkbenchReservedRead

router = APIRouter(tags=["workbench-reserved"])


@router.get(
    "/projects/{project_id}/workbench/trace-coverage",
    response_model=WorkbenchReservedRead,
)
def read_trace_coverage(project_id: str) -> WorkbenchReservedRead:
    """AEP-106（预留）：追溯覆盖矩阵（条目 × 承接资产追溯边 + 完备度）。"""
    return WorkbenchReservedRead(deferred=True, note="追溯覆盖矩阵待接入", items=[])


@router.get(
    "/projects/{project_id}/workbench/ai-copilot",
    response_model=WorkbenchReservedRead,
)
def read_ai_copilot(project_id: str, item_ref: str | None = None) -> WorkbenchReservedRead:
    """AEP-107（预留）：AI 评审副驾聚合（本条目质量问题 / 来源偏离 / 相似条目 / 建议卡）。"""
    return WorkbenchReservedRead(deferred=True, note="AI 副驾待接入", items=[])


@router.get(
    "/projects/{project_id}/workbench/change-impact",
    response_model=WorkbenchReservedRead,
)
def read_change_impact(project_id: str, item_ref: str | None = None) -> WorkbenchReservedRead:
    """AEP-108（预留）：变更影响传播面 + 返工风险预测。"""
    return WorkbenchReservedRead(deferred=True, note="变更影响预测待接入", items=[])
