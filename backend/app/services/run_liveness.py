"""AgentRun 在飞判活（幂等普查 HK-1 落地；HK-2 悬轮自愈 / HK-3 全局对账复用）。

判活口径（普查表 §6 依赖关系 + HK-1 卡设计裁定）：
- 在飞 = 状态处 queued/started 且入队龄 < job_timeout_for(lane) × 2；
- 超过判死阈值的悬 run 视为死批，不得再充当单飞守卫依据（防僵尸 run 永久锁死入口）；
  判死后的收尸/对账（改写状态、通知）不在本模块职责，由 HK-2/HK-3 承接。

lane 取 rq task 名（app/workers/queue.py `_LANE_TIER` 键，如 "run_item_formation"），
阈值单一来源 = job_timeout_for(lane)，不新引配置。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol

from app.domain.enums import AgentRunStatus
from app.workers.queue import job_timeout_for

# 判死宽容系数：job 级强杀最晚发生在 job_timeout，×2 覆盖排队等待与状态回写延迟
_LIVENESS_GRACE_FACTOR = 2

INFLIGHT_RUN_STATUSES = frozenset({AgentRunStatus.QUEUED.value, AgentRunStatus.STARTED.value})


class RunLike(Protocol):
    """判活所需的最小 run 形状（AgentRun ORM 行或仓储读投影均可）。"""

    status: str
    created_at: datetime


def lane_for_kind(kind: str) -> str:
    """AgentRun.kind → rq lane（worker 入口 task 名）。

    派发侧的既有约定就是「lane = run_ + kind」（deps.py 的 make_enqueue 名与
    model_orchestration.py 的 _dispatch kind 逐个对应），此处只把这条约定写成代码，
    好让按 kind 存的 AgentRun 行能取到自己 lane 的判死阈值。
    未登记的 lane 由 job_timeout_for 回落单调用档并记 WARN，不在此重复判断。
    """
    return f"run_{kind}"


def run_liveness_deadline_seconds(lane: str) -> int:
    """lane 的判死阈值（秒）：入队龄达到该值即视为死批。"""
    return job_timeout_for(lane) * _LIVENESS_GRACE_FACTOR


def is_run_alive(lane: str, run: RunLike, *, now: Optional[datetime] = None) -> bool:
    """run 是否仍在飞：queued/started 且入队龄 < job_timeout_for(lane)×2。

    created_at 无时区信息时按 UTC 解释（SQLite CURRENT_TIMESTAMP 为 UTC 裸值）。
    """
    if run.status not in INFLIGHT_RUN_STATUSES:
        return False
    created = run.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age_seconds = (reference - created).total_seconds()
    return age_seconds < run_liveness_deadline_seconds(lane)


def dead_run_verdict(
    lane: str, run: Optional[RunLike], *, now: Optional[datetime] = None
) -> Optional[str]:
    """悬轮读侧对账（HK-2）的判死依据；None=不收尸。

    - "run_missing"：run 缺失（历史脏数据）视同死（HK-2 卡设计裁定 2）；
    - "run_failed"：run 已失败（job 级强杀等，worker 骨架已 mark_failed）；
    - "run_stale"：run 仍处 queued/started 但超判死阈值（硬杀僵尸，S4）；
    - None：在飞未超龄（不得误杀），或 run 已 succeeded（轮次归属各业务承接路径，
      不属失联对账范畴——正常收束不会留悬轮）。
    """
    if run is None:
        return "run_missing"
    if run.status == AgentRunStatus.FAILED.value:
        return "run_failed"
    if run.status in INFLIGHT_RUN_STATUSES and not is_run_alive(lane, run, now=now):
        return "run_stale"
    return None
