"""运行态面板与诊断中心（基础设施只读投影）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel


# ---- 运行态面板 / 诊断中心（04A §2.1,基础设施只读投影,非业务事实源）----


class RuntimeComponentRead(BaseModel):
    """单个平台组件的探测结果。status:ok/degraded/down/not_applicable。"""

    key: str  # api | db | redis | worker | event_bus
    label: str
    status: str
    detail: str | None = None


class RuntimeAlertRead(BaseModel):
    """活跃风险组(按组去重,现算不累加)。level:WARN/ERROR。"""

    code: str
    level: str
    summary: str
    hint: str | None = None


class AsyncJobsSummaryRead(BaseModel):
    """异步作业摘要(agent_run 表聚合 + Redis 队列深度)。"""

    mode: str  # inline | queued
    queued: int | None = None
    running: int | None = None
    failed_recent: int | None = None  # 近 24h 失败数
    oldest_waiting_minutes: int | None = None
    queue_depth: int | None = None  # Redis 队列积压(inline 模式为 None)


class RecentAgentRunRead(BaseModel):
    """最近一条异步作业(运行态面板明细表一行)。

    只投影稳定码与派生读数:kind_label 是给人看的白话名,reason_code 是登记过的失败原因码,
    AgentRun.error 的原文一律不出现在本结构里(硬规则 8)。
    """

    run_id: str
    kind: str  # AgentRun.kind 稳定码
    kind_label: str  # 白话名(单一来源=services/notification.AGENT_RUN_KIND_LABELS)
    status: str  # queued | started | succeeded | failed
    created_at: str  # 发起时刻 ISO
    duration_seconds: int | None = None  # 终态才有;非终态=None(等待中/进行中)
    reason_code: str | None = None  # 失败行的原因稳定码


class DiagnosticEventRead(BaseModel):
    """诊断事件白名单摘要(进程内环形缓冲,只读展示)。"""

    event: str
    component: str
    level: str
    first_seen: str
    last_seen: str
    count: int


class RuntimeStatusRead(BaseModel):
    """GET /api/runtime-status。status:normal/degraded/down;alert_count=活跃风险组数。"""

    status: str
    alert_count: int
    generated_at: str
    components: list[RuntimeComponentRead]
    alerts: list[RuntimeAlertRead]
    async_jobs: AsyncJobsSummaryRead
    recent_jobs: list[RecentAgentRunRead] = []  # 最近异步作业明细(加性扩展)
    diagnostics: list[DiagnosticEventRead]
