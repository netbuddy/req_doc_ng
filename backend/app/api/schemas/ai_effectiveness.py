"""AI 效能按环节统计读面。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# AEP-094 AI 效能按环节统计（模型推理结果仓储·统计读面；AI效能统计口径设计 §6）
# ---------------------------------------------------------------------------


class AiStageEffectRead(BaseModel):
    """按环节采纳明细计数（比率与中文标签归前端；分母口径=口径设计 D3）。"""

    stage: str
    total: int = 0                    # 窗口内已收口明细（adopted+adopted_with_revision+rejected+transferred_to_issue）
    pending_records: int = 0          # 窗口内记录级 pending 提示
    adopted: int = 0
    adopted_with_revision: int = 0
    rejected: int = 0
    transferred_to_issue: int = 0


class AiCalibrationBucketRead(BaseModel):
    range: str                        # 例 "0.6-0.7"
    avg_confidence: float
    accuracy: float
    count: int


class AiCalibrationRead(BaseModel):
    """置信度校准（样本=识别明细×要素置信度；口径设计 §5.2）。"""

    ece: float | None = None
    rating: str = "insufficient"      # excellent/good/fair/poor/insufficient
    sample_size: int = 0
    buckets: list[AiCalibrationBucketRead] = Field(default_factory=list)


class AiCoverageRead(BaseModel):
    touched: int = 0
    untouched: int = 0
    not_applicable: int = 0           # 非管线产生（直写导入）
    total_items: int = 0


class AiRiskSignalRead(BaseModel):
    key: str                          # low_confidence / rejection_rising / issue_conversion / source_conflict
    level: str                        # high / medium / low / deferred
    value: int = 0


class AiFailureStageCountRead(BaseModel):
    """失败关卡分桶计数（口径设计 §5.5）。"""

    failure_stage: str                # parse/llm_error/structure/aggregation/synthesis/unclassified
    count: int = 0


class AiDeliveryFailureRead(BaseModel):
    """交付失败＝AI 未能交出合法结论（LDM-015 judgement=*_failed），按 lane 聚合。

    与「拒绝率」（人工不采纳，采纳明细 rejected）是正交维度，禁混算（口径设计 §5.5）。
    失败率=failed/total 归前端；total=该 lane 窗口内总判定行数（分母）。
    """

    stage: str                        # lane 稳定码（LDM-015.stage）
    total: int = 0                    # 窗口内该 lane 总判定行数（分母）
    failed: int = 0                   # *_failed 行数（分子）
    by_failure_stage: list[AiFailureStageCountRead] = Field(default_factory=list)


class AiEffectivenessRead(BaseModel):
    """GET /projects/{id}/ai-effectiveness —— 只读统计，不改变 LDM-015 处理状态（UINV-23）。"""

    project_ref: str
    window_days: int
    stages: list[AiStageEffectRead] = Field(default_factory=list)
    calibration: AiCalibrationRead
    coverage: AiCoverageRead
    risk_signals: list[AiRiskSignalRead] = Field(default_factory=list)
    delivery_failures: list[AiDeliveryFailureRead] = Field(default_factory=list)


class AiDeliveryFailureInstanceRead(BaseModel):
    """交付失败个案（钻取；口径 §5.5）。白话字段，不含 Prompt/模型原文（AGENTS 硬规 8）。

    run_status = best-effort 关联 AgentRun（kind==stage ∧ context_ref==applies_to_ref）状态，
    用于从统计接入运行态·诊断中心的重试/降级跟进；无法关联时为 null。
    """

    occurred_at: str                  # LDM-015.created_at（ISO）
    failure_stage: str                # parse/llm_error/structure/aggregation/synthesis/unclassified
    detail: str = ""                  # 白话失败详情（failure.detail 或 basis）
    subject_req_no: str | None = None # 受影响条目编号（条目类 lane 可解析时）
    run_status: str | None = None     # queued/started/succeeded/failed（best-effort）


class AiDeliveryFailureInstancesRead(BaseModel):
    """GET /projects/{id}/ai-effectiveness/delivery-failures —— 某 lane[×失败关卡] 的失败个案钻取。"""

    stage: str
    failure_stage: str | None = None
    window_days: int
    total_failed: int = 0             # 过滤下失败总数（可能 > 返回条数）
    instances: list[AiDeliveryFailureInstanceRead] = Field(default_factory=list)
