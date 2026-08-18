"""对等架构单元发布的公开接口（一单元一份，消费者共享 import）。

各接口标注其拥有单元（docs/40 domains/*/interfaces/）。多个服务需要同一单元时，
一律 import 这里的同一份，勿各写各的。
"""
from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol, runtime_checkable


@runtime_checkable
class ProjectScope(Protocol):
    """项目上下文服务（DS-001）· AEP-071 readProjectScope 的本切片所需片段（gate『已选定项目』）。"""

    def is_project_selected(self, project_ref: str) -> bool: ...


@runtime_checkable
class ModelOrchestration(Protocol):
    """模型推理编排服务（DS-004）· 送检返回 agent_run_ref（异步，结果经内部回交）。

    AEP-003 来源接入判断（结果经材料接收服务 AEP-002 回交）；
    AEP-004 需求要素识别（结果经分析转化服务 AEP-022 回交）；
    AEP-005 需求要素复核（结果经分析转化服务 AEP-024 回交）；
    AEP-006 指定操作 AI 执行（结果经分析转化服务 AEP-028 回交）；
    AEP-007 条目格式化送检（结果经条目形成服务承接；逐要素归因）；
    条目诊断送检（SCN-003-P01-N08；结果经条目评审服务逐条目承接）；
    图表源码建议送检（SCN-004-P01-N08；结果经图表协同服务承接）；
    图文一致性核对送检（SCN-004-P02-N03；结果经图表协同服务承接）。
    """

    def request_source_intake_judgement(self, context_ref: str) -> str: ...

    def request_element_recognition(self, context_ref: str) -> str: ...

    def request_element_review(self, operation_ref: str) -> str: ...

    def request_element_execution(self, operation_ref: str) -> str: ...

    def request_item_formation(self, formation_context_ref: str) -> str: ...

    def request_item_diagnosis(self, batch_ref: str) -> str: ...

    def request_item_structure_recheck(self, recheck_context_ref: str) -> str: ...

    def request_chart_suggestion(self, context_ref: str) -> str: ...

    def request_chart_verification(self, request_ref: str) -> str: ...


@runtime_checkable
class TraceGraph(Protocol):
    """追溯图谱模块（DS-002）· AEP-077：来源→预建立追溯。"""

    def pre_establish_source_trace(self, material_ref: str) -> None: ...


@runtime_checkable
class AuditTrail(Protocol):
    """审计留痕（横切）· 不含 Prompt/密钥/敏感原文。"""

    def record_intake_accepted(self, material_ref: str, operator_ref: str) -> None: ...


@runtime_checkable
class AgentRuns(Protocol):
    """异步任务台账（AgentRun）· 编排入队时登记（ADR-007）。"""

    def create(self, kind: str, context_ref: Optional[str]) -> str: ...

    def mark_failed(self, run_id: str, error: str) -> None:
        """入队补偿（issue #12 K1）：enqueue 抛异常时把 queued run 置 failed，
        令 run_liveness 立即判死、in_flight 修复通道解堵（防幻影 queued 批锁死入口）。"""
        ...


@runtime_checkable
class AgentRunEventBus(Protocol):
    """异步任务进度事件总线（ADR-007 / 25-05）· 跨进程推送 AgentRun 状态迁移。

    worker 侧在 DB 提交后 publish 事件名；SSE 侧 subscribe 阻塞消费并按 id 续传。
    有 REDIS_URL → Redis Streams 实现；无 → Null（SSE 退回 DB 轮询降级）。
    """

    def publish(self, run_id: str, event: str) -> None:
        """worker 侧，同步。发布一条进度事件（仅事件名，无敏感原文）。"""
        ...

    def subscribe(self, run_id: str, last_id: str = "0") -> AsyncIterator[tuple[str, str]]:
        """SSE 侧，异步。产出 (event, entry_id)；从 last_id 之后消费，超时产出心跳哨兵。"""
        ...
