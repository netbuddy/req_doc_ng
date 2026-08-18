"""运行态聚合服务(04A §2.1 运行态面板 / 诊断中心)。

六个风险组告警全部由"当前探测态"现算,不从日志累加——天然满足计数口径:
按风险组去重、已恢复的波动不计数、同一风险组重复日志不加数。
诊断事件另路来自进程内环形缓冲(app/diagnostics.py),仅展示,不参与徽标计数。
所有探测短超时、异常降级为 unknown/down,本服务永不抛出。

写动作只有一处:排队后无人执行的 AgentRun 判死(见 reconcile_orphan_queued_runs)。
这是读侧自愈,与既有悬轮自愈(HK-2)同一通道——发现才收尸,不引巡检进程、不引定时器;
除此之外本服务对业务表只读。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.schemas import (
    AsyncJobsSummaryRead,
    DiagnosticEventRead,
    RecentAgentRunRead,
    RuntimeAlertRead,
    RuntimeComponentRead,
    RuntimeStatusRead,
)
from app.db.models import AgentRun
from app.domain.enums import AgentRunStatus
from app.log import log_event
from app.services.notification import AGENT_RUN_KIND_LABELS
from app.services.run_liveness import is_run_alive, lane_for_kind

_COMPONENT = "runtime-status"

# 阈值(v0.1 拍板值;调整只动这里,不动判定逻辑)
QUEUE_BACKLOG_THRESHOLD = 10  # Redis 队列深度超过即告警"队列积压"
FAILURE_SPIKE_WINDOW_MINUTES = 15  # AgentRun 失败突增观察窗
FAILURE_SPIKE_THRESHOLD = 3  # 窗口内失败数达到即告警
FAILED_RECENT_HOURS = 24  # 异步作业摘要中"近期失败"的统计窗
_SCAN_LIMIT = 1000  # agent_run 聚合扫描上限(v0.1 演示规模足够)
RECENT_JOBS_LIMIT = 8  # 最近异步作业明细表的条数

# 判死原因稳定码。写进 AgentRun.error 作前缀,读侧据此还原稳定码——
# 面板只投影稳定码,永不投影 error 原文(硬规则 8:原文可能含异常/用户内容)。
ORPHANED_QUEUE_REASON = "queue.orphaned"
# 兜底原因码:这行确实失败了,但它的 error 没有以任何登记稳定码开头(worker 侧曾直接
# 写异常字符串),故归不出原因。取值刻意不与事件名重名——曾用 "agent_run.failed",与
# event_bus.EVENT_FAILED 逐字节相同,面板上看着像在引用事件总线的哨兵值。
GENERIC_FAILURE_REASON = "failure.unclassified"
_ORPHANED_QUEUE_ERROR = f"{ORPHANED_QUEUE_REASON}：排队超期且队列中无对应任务，读侧对账判死"
_REGISTERED_REASON_CODES = (ORPHANED_QUEUE_REASON,)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """sqlite 会返回 naive datetime(存储即 UTC),统一补时区再比较。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _agent_run_summary(session: Session, now: datetime) -> dict:
    """agent_run 表聚合:等待/运行中/近 24h 失败/失败突增窗计数/最久等待分钟。

    两个失败窗都排除读侧对账判死的行(error 以 ORPHANED_QUEUE_REASON 开头),
    理由见循环内注释:它们的 updated_at 是记录时刻而非发生时刻。
    """
    rows = session.execute(
        select(AgentRun.status, AgentRun.created_at, AgentRun.updated_at, AgentRun.error)
        .where(
            AgentRun.status.in_(
                [
                    AgentRunStatus.QUEUED.value,
                    AgentRunStatus.STARTED.value,
                    AgentRunStatus.FAILED.value,
                ]
            )
        )
        .order_by(AgentRun.updated_at.desc())
        .limit(_SCAN_LIMIT)
    ).all()

    queued = running = failed_recent = failure_spike = 0
    oldest_queued: Optional[datetime] = None
    recent_cutoff = now - timedelta(hours=FAILED_RECENT_HOURS)
    spike_cutoff = now - timedelta(minutes=FAILURE_SPIKE_WINDOW_MINUTES)

    for status, created_at, updated_at, error in rows:
        if status == AgentRunStatus.QUEUED.value:
            queued += 1
            created = _as_utc(created_at)
            if created is not None and (oldest_queued is None or created < oldest_queued):
                oldest_queued = created
        elif status == AgentRunStatus.STARTED.value:
            running += 1
        else:  # failed
            if (error or "").startswith(ORPHANED_QUEUE_REASON):
                # 对账判死的行不计入两个新鲜度窗:它的 updated_at 是「被记录的时刻」
                # (读侧自愈判死当刻),而失败「发生的时刻」可能在几周前。不排除的话,
                # 一次回收三行就会在同一次响应里点亮「失败突增」并把整体状态打成降级——
                # 打开面板这个动作本身制造出一条假告警。明细表照常显示这些行,不隐藏事实。
                continue
            updated = _as_utc(updated_at)
            if updated is not None and updated >= recent_cutoff:
                failed_recent += 1
                if updated >= spike_cutoff:
                    failure_spike += 1

    oldest_waiting_minutes = (
        max(0, int((now - oldest_queued).total_seconds() // 60)) if oldest_queued else None
    )
    return {
        "queued": queued,
        "running": running,
        "failed_recent": failed_recent,
        "failure_spike": failure_spike,
        "oldest_waiting_minutes": oldest_waiting_minutes,
    }


def reconcile_orphan_queued_runs(
    session: Session, now: datetime, queued_run_ids: Optional[set[str]]
) -> int:
    """读侧自愈:排队后无人执行的 AgentRun 判死为 failed。返回被回收的行数。

    判死须同时满足两条,少一条都不动(防 worker 忙碌时误杀在飞任务):
    ① 入队龄已过该 lane 的判死期限(复用 run_liveness 的判活单点,不另立阈值);
    ② 队列中没有对应的待执行任务。

    `queued_run_ids=None` 表示队列状态查不到(Redis 不可达等)——查不到就不判死,
    宁可放过也不误杀。动作幂等:判死后行不再是 queued,重跑不会重复处理。
    """
    if queued_run_ids is None:
        return 0
    from app.repositories.agent_run import SqlAgentRunRepository

    rows = list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.status == AgentRunStatus.QUEUED.value)
            .order_by(AgentRun.created_at)
            .limit(_SCAN_LIMIT)
        )
    )
    repo = SqlAgentRunRepository(session)
    reclaimed = 0
    for run in rows:
        lane = lane_for_kind(run.kind)
        if is_run_alive(lane, run, now=now):
            continue  # 未过判死期限:在飞,不动
        if str(run.id) in queued_run_ids:
            continue  # 队列里还挂着对应任务:等它执行,不动
        created = _as_utc(run.created_at)
        # notify=False:对账判死的是早已死掉的历史行,不是用户此刻要处理的待办;
        # 留痕改由下面这条 WARN 日志与面板明细表(failed + queue.orphaned 稳定码)承担。
        repo.mark_failed(str(run.id), _ORPHANED_QUEUE_ERROR, notify=False)
        reclaimed += 1
        log_event(
            _COMPONENT, "agent_run.queue.orphan_reclaimed", level="WARN", ok=False,
            run_id=str(run.id), kind=run.kind, lane=lane,
            reason_code=ORPHANED_QUEUE_REASON,
            waited_seconds=int((now - created).total_seconds()) if created else None,
        )
    if reclaimed:
        session.commit()
    return reclaimed


def _reason_code(status: str, error: Optional[str]) -> Optional[str]:
    """失败行的原因稳定码。

    error 列存的是自由文本(worker 侧曾直接写异常字符串),绝不投影到界面;
    只在它以本模块登记的稳定码开头时还原该码,其余失败一律给通用码。
    """
    if status != AgentRunStatus.FAILED.value:
        return None
    text_value = error or ""
    for code in _REGISTERED_REASON_CODES:
        if text_value.startswith(code):
            return code
    return GENERIC_FAILURE_REASON


def _recent_agent_runs(session: Session) -> list[RecentAgentRunRead]:
    """最近若干条异步作业明细(终态与非终态混排,按发起时间倒序)。

    耗时只对终态给值:updated_at 是终态迁移时刻(取语句时刻,见 models.AgentRun),
    与 created_at 之差即本次作业从发起到收束的时长;非终态给 None,由前端呈现"等待中/进行中"。
    """
    rows = session.execute(
        select(AgentRun.id, AgentRun.kind, AgentRun.status, AgentRun.error,
               AgentRun.created_at, AgentRun.updated_at)
        .order_by(AgentRun.created_at.desc())
        .limit(RECENT_JOBS_LIMIT)
    ).all()

    terminal = {AgentRunStatus.SUCCEEDED.value, AgentRunStatus.FAILED.value}
    recent: list[RecentAgentRunRead] = []
    for run_id, kind, status, error, created_at, updated_at in rows:
        created = _as_utc(created_at)
        updated = _as_utc(updated_at)
        duration = None
        if status in terminal and created is not None and updated is not None:
            # 兜底 max(0, …):时钟回拨/历史脏行不该渲染出负耗时。
            duration = max(0, int((updated - created).total_seconds()))
        recent.append(
            RecentAgentRunRead(
                run_id=str(run_id),
                kind=kind,
                kind_label=AGENT_RUN_KIND_LABELS.get(kind, kind),
                status=status,
                created_at=created.isoformat() if created else "",
                duration_seconds=duration,
                reason_code=_reason_code(status, error),
            )
        )
    return recent


def build_runtime_status(
    session_factory: Callable[[], Session],
    async_probe: Callable[[], dict],
    event_bus_live: bool,
    diagnostics_snapshot: Callable[[], list[dict]],
    llm_probe: Optional[Callable[[], dict]] = None,  # 模型服务探测（04A §2.1 增补；None=不展示该组件）
    # 队列中仍挂着任务的 run_id 集合;返回 None=查不到(此时不判死)。None 回调=不做自愈。
    queued_run_ids_probe: Optional[Callable[[], Optional[set[str]]]] = None,
    now: Optional[datetime] = None,
) -> RuntimeStatusRead:
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)

    # ---- DB 探活 + 悬队自愈 + agent_run 聚合(同一 session;探活失败则聚合不可用)----
    db_ok = False
    runs: Optional[dict] = None
    recent_jobs: list[RecentAgentRunRead] = []
    try:
        session = session_factory()
        try:
            session.execute(text("SELECT 1"))
            db_ok = True
            if queued_run_ids_probe is not None:
                # 自愈先于聚合:本次响应的等待数即已扣除刚回收的孤儿行。
                # 自愈失败只记 WARN,绝不拖垮运行态读取。
                try:
                    reconcile_orphan_queued_runs(session, now, queued_run_ids_probe())
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    log_event(
                        _COMPONENT, "agent_run.queue.reconcile_failed", level="WARN",
                        ok=False, error_code=type(exc).__name__,
                    )
            runs = _agent_run_summary(session, now)
            recent_jobs = _recent_agent_runs(session)
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 探测失败降级,不抛出
        log_event(
            _COMPONENT, "runtime.db.unreachable", level="WARN",
            ok=False, error_code=type(exc).__name__,
        )

    # ---- Redis / Worker / 队列深度(workers/queue.py 已带 1s 短超时)----
    try:
        async_state = async_probe()
    except Exception as exc:  # noqa: BLE001
        log_event(
            _COMPONENT, "runtime.async_probe.failed", level="WARN",
            ok=False, error_code=type(exc).__name__,
        )
        async_state = {"mode": "queued", "redis_ok": False, "workers": None, "queued": None}

    mode = async_state.get("mode", "inline")
    redis_ok = async_state.get("redis_ok")
    workers = async_state.get("workers")
    queue_depth = async_state.get("queued")

    # ---- 组件状态(04A §2.1:至少覆盖 API/DB/Redis/Worker/Event Bus-SSE)----
    components = [
        RuntimeComponentRead(key="api", label="API", status="ok", detail="服务响应正常"),
        RuntimeComponentRead(
            key="db", label="DB",
            status="ok" if db_ok else "down",
            detail="SELECT 1 探活通过" if db_ok else "探活失败,业务读写不可用",
        ),
    ]
    if mode == "inline":
        components.append(
            RuntimeComponentRead(
                key="redis", label="Redis", status="not_applicable",
                detail="未配置 REDIS_URL(inline 同步模式)",
            )
        )
        components.append(
            RuntimeComponentRead(
                key="worker", label="Worker", status="not_applicable",
                detail="inline 模式:任务在 API 进程内同步执行",
            )
        )
    else:
        components.append(
            RuntimeComponentRead(
                key="redis", label="Redis",
                status="ok" if redis_ok else "down",
                detail="ping 通过" if redis_ok else "ping 失败",
            )
        )
        worker_ok = bool(redis_ok) and (workers or 0) > 0
        components.append(
            RuntimeComponentRead(
                key="worker", label="Worker",
                status="ok" if worker_ok else "down",
                detail=f"{workers} 个活跃 worker" if worker_ok else "无活跃 worker,任务将静默排队",
            )
        )
    components.append(
        RuntimeComponentRead(
            key="event_bus", label="Event Bus / SSE",
            status="ok" if event_bus_live else "degraded",
            detail="Redis Streams 真推送" if event_bus_live else "SSE 降级为 DB 轮询",
        )
    )

    # ---- 模型服务（04A §2.1 增补：AI 请求全链路的共享瓶颈，可达性 + 延迟采样）----
    llm_down = False
    if llm_probe is not None:
        try:
            llm_state = llm_probe()
        except Exception as exc:  # noqa: BLE001 探测失败降级
            log_event(_COMPONENT, "runtime.llm_probe.failed", level="WARN",
                      ok=False, error_code=type(exc).__name__)
            llm_state = {"configured": True, "ok": False, "latency_ms": None}
        if not llm_state.get("configured"):
            components.append(RuntimeComponentRead(
                key="llm", label="模型服务", status="not_applicable",
                detail="未配置 LLM_BASE_URL（stub 模式，AI 判定为确定性桩）",
            ))
        elif llm_state.get("ok"):
            latency = llm_state.get("latency_ms")
            components.append(RuntimeComponentRead(
                key="llm", label="模型服务", status="ok",
                detail=f"探测通过（{latency} ms）" if latency is not None else "探测通过",
            ))
        else:
            llm_down = True
            components.append(RuntimeComponentRead(
                key="llm", label="模型服务", status="down",
                detail="探测失败：AI 解释/生成请求将超时或停滞",
            ))

    # ---- 六个风险组(现算,按组去重)----
    alerts: list[RuntimeAlertRead] = []
    if not db_ok:
        alerts.append(
            RuntimeAlertRead(
                code="db.unavailable", level="ERROR", summary="数据库不可用",
                hint="SELECT 1 探活失败;检查 Postgres 容器与 DATABASE_URL",
            )
        )
    if mode == "queued" and not redis_ok:
        alerts.append(
            RuntimeAlertRead(
                code="async.redis.unreachable", level="ERROR", summary="Redis 不可达",
                hint="REDIS_URL 已配置但连接失败;异步任务无法入队",
            )
        )
    if mode == "queued" and redis_ok and (workers or 0) == 0:
        alerts.append(
            RuntimeAlertRead(
                code="async.worker.absent", level="ERROR", summary="Worker 未运行",
                hint="任务将静默排队;启动:docker compose up -d worker",
            )
        )
    if queue_depth is not None and queue_depth > QUEUE_BACKLOG_THRESHOLD:
        alerts.append(
            RuntimeAlertRead(
                code="async.queue.backlog", level="WARN", summary="异步任务排队等待",
                hint=f"队列深度 {queue_depth} 超过阈值 {QUEUE_BACKLOG_THRESHOLD}",
            )
        )
    if not event_bus_live:
        alerts.append(
            RuntimeAlertRead(
                code="sse.degraded", level="WARN", summary="SSE 降级为轮询",
                hint="未配置 REDIS_URL;进度推送退回 DB 轮询,不影响结果正确性",
            )
        )
    if llm_down:
        alerts.append(
            RuntimeAlertRead(
                code="llm.unreachable", level="ERROR", summary="模型服务不可达",
                hint="LLM_BASE_URL 已配置但探测失败;检查推理服务进程与网络,期间 AI 请求将超时",
            )
        )
    if runs is not None and runs["failure_spike"] >= FAILURE_SPIKE_THRESHOLD:
        alerts.append(
            RuntimeAlertRead(
                code="agent_run.failure_spike", level="WARN", summary="AgentRun 失败突增",
                hint=(
                    f"近 {FAILURE_SPIKE_WINDOW_MINUTES} 分钟失败 {runs['failure_spike']} 次"
                    f"(阈值 {FAILURE_SPIKE_THRESHOLD});检查模型服务与 worker 日志"
                ),
            )
        )

    # ---- 总体状态:DB 不可用=down;有任何告警=degraded;否则 normal ----
    overall = "down" if not db_ok else ("degraded" if alerts else "normal")

    async_jobs = AsyncJobsSummaryRead(
        mode=mode,
        queued=runs["queued"] if runs else None,
        running=runs["running"] if runs else None,
        failed_recent=runs["failed_recent"] if runs else None,
        oldest_waiting_minutes=runs["oldest_waiting_minutes"] if runs else None,
        queue_depth=queue_depth,
    )

    try:
        diagnostics = [DiagnosticEventRead(**entry) for entry in diagnostics_snapshot()]
    except Exception:  # noqa: BLE001 诊断投影失败不影响主结构
        diagnostics = []

    log_event(
        _COMPONENT, "runtime.status.read",
        ok=True, status=overall, alert_count=len(alerts), db_ok=db_ok, mode=mode,
        workers=workers, queue_depth=queue_depth,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return RuntimeStatusRead(
        status=overall,
        alert_count=len(alerts),
        generated_at=now.isoformat(),
        components=components,
        alerts=alerts,
        async_jobs=async_jobs,
        recent_jobs=recent_jobs,
        diagnostics=diagnostics,
    )
