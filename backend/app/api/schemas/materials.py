"""材料接收服务与材料接入 V2 三拍制。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import (
    IntakeConclusion,
    IntakeOutcome,
    IntakeRequestStatus,
)


# ---- 材料接收服务 接口专用数据模型 ----

class TextIntakeCommand(BaseModel):
    """AEP-001 入参。"""

    project_ref: str = Field(description="项目标识——须与路径参数 project_id 一致，不一致返回 400。")
    raw_text: str = Field(description="材料正文（纯文本）。提交后由受理判断决定是否形成正式材料。")
    source_note: str = Field(default="", description="来源备注——材料从哪里来的自由说明，选填。")
    operator_ref: str = Field(description="操作者标识——记入操作留痕，回答「这份材料是谁提交的」。")
    idempotency_key: str = Field(description="幂等键——同一键重复提交不重复受理，用于断网重试等场景防重。")


class IntakeRequestResult(BaseModel):
    """AEP-001 返回（受理立即返回；异步判断经 agent_run_ref 追踪）。"""

    status: IntakeRequestStatus = Field(description="受理状态——提交是否被接收进入异步判断。")
    context_ref: str | None = Field(default=None, description="接入上下文标识——后续用它查询受理结论。")
    agent_run_ref: str | None = Field(default=None, description="异步任务标识——经任务通道（轮询或 SSE）追踪受理进度。")
    next_action: str | None = Field(default=None, description="建议的下一步动作，供界面导航。")


class IntakeJudgementResultCommand(BaseModel):
    """AEP-002 入参（模型编排内部回交，不暴露 HTTP）。"""

    model_result_ref: str
    intake_context_ref: str
    operator_ref: str
    idempotency_key: str
    service_accepts: bool = True


class IntakeDecisionResult(BaseModel):
    """AEP-002 返回。material_ref 仅在 accepted 分支非空。"""

    outcome: IntakeOutcome
    intake_conclusion: IntakeConclusion | None = None
    material_ref: str | None = None
    next_action: str | None = None


class ActionFact(BaseModel):
    """可执行动作事实（领域裁定）；ViewModel 映射为 ActionVM，前端不自算门禁。"""

    key: str = Field(description="动作标识，例如「重新提交」。")
    enabled: bool = Field(description="该动作当前是否可执行——由后端领域规则裁定，前端不自行推断。")
    disabled_reason: str | None = Field(default=None, description="不可执行时的原因说明，供界面展示。")


class IntakeResultRead(BaseModel):
    """结果查询读视图（intakeApi.getResult）。"""

    context_ref: str = Field(description="接入上下文标识——与提交时返回的 context_ref 对应。")
    intake_conclusion: IntakeConclusion | None = Field(default=None, description="受理结论三值：accepted＝接收成为正式材料；returned_for_supplement＝退回补充；excluded＝排除。判断未完成时为空。")
    material_ref: str | None = Field(default=None, description="材料标识——仅结论为「接收」时非空，此后以它引用该材料。")
    basis: str | None = Field(default=None, description="结论依据——受理判断给出的理由说明。")
    next_action: str | None = Field(default=None, description="建议的下一步动作，供界面导航。")
    available_actions: list[ActionFact] = Field(default_factory=list, description="当前可用动作清单——门禁裁定结果，前端照单呈现。")


# ---- 材料接入 V2 形态（2026-08-08 用户裁定路线 A：三拍制保留，应答改 V2 信封）----

class IntakeSubmitCommand(BaseModel):
    """提交材料接入的入参（V2 形态：项目标识走路径，不再入请求体）。"""

    text: str = Field(description="材料正文（纯文本）。提交后由受理判断决定是否形成正式材料。")
    source_note: str = Field(default="", description="来源备注——材料从哪里来的自由说明，选填。")
    operator_ref: str = Field(description="操作者标识——记入操作留痕，回答「这份材料是谁提交的」。")
    idempotency_key: str = Field(description="幂等键——同一键重复提交不重复受理，重放返回同一接入上下文。")


class IntakeReceipt(BaseModel):
    """提交回执——受理已登记、进入异步判断。"""

    context_ref: str = Field(description="接入上下文标识——后续用它查询受理结论。")
    agent_run_ref: str | None = Field(default=None, description="异步任务标识——经任务通道追踪受理进度；幂等重放命中时为空。")


class SuccessOfIntakeReceipt(BaseModel):
    """成功信封：提交回执。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，受理登记成功为「成功」。")
    data: IntakeReceipt = Field(description="提交回执。")


class SuccessOfIntakeConclusion(BaseModel):
    """成功信封：受理结论读视图（判断中／失败停靠／三值结论都是合法的读取结果）。"""

    result: Literal["成功"] = Field(description="应答信封结果字段，本操作恒为「成功」。")
    data: IntakeResultRead = Field(description="受理结论读视图。")
