"""跨域共享结构：健康探测、V2 业务拒绝信封（契约 api/schemas/common.yaml）与异步任务进度读侧。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthPayload(BaseModel):
    """GET /api/health（基础设施健康，对齐 frontend/src/api/health.ts）。"""

    status: str
    checks: dict[str, str] = Field(default_factory=dict)
    service: str | None = None
    version: str | None = None
    environment: str | None = None
    ready: bool | None = None


class BusinessRejection(BaseModel):
    """业务拒绝形状（对齐 V2 契约 common.yaml BusinessRejection）。"""

    category: Literal["业务拒绝"] = Field(description="类别，固定值。")
    reason_code: str = Field(description="原因码（中文短语，机器码，正本见 docs/v2/design/业务拒绝原因码表.md）。")
    message: str = Field(description="文案（人可读，可改；码不可改）。")
    details: dict[str, object] | None = Field(default=None, description="详情——随码而异的结构化参数。")


class BusinessRejectionEnvelope(BaseModel):
    """业务拒绝信封：与成功同走 200，以 result 字段区分。"""

    result: Literal["业务拒绝"] = Field(description="应答信封结果字段。")
    rejection: BusinessRejection = Field(description="拒绝的结构化说明。")


class AgentRunEventRead(BaseModel):
    event: str
    at: str


class AgentRunRead(BaseModel):
    """异步任务进度（agentRunApi.get）。"""

    id: str
    kind: str
    status: str  # AgentRunStatus 稳定码
    error: str | None = None
    events: list[AgentRunEventRead] = Field(default_factory=list)
