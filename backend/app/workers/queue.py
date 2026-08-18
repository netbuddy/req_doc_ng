"""入队抽象 + 异步 worker 存活探测（韧性）。

REDIS_URL 有 → RQ 真异步；无 → inline 同步执行。
task_name 选择 worker 入口（接入=run_source_intake；识别=run_element_recognition）。

韧性：REDIS_URL 已配但没有活跃 worker 时，job 会静默排队、界面表现为"卡住"。
故在启动自检（warn_if_async_without_worker）与每次入队（warn_if_no_worker）时打 WARN，
只记队列名/计数/run_id，不记任何原文。

超时：rq 默认 job timeout 180s 会强杀批次任务，入队按 lane 下发 job_timeout（_LANE_TIER
单一来源），并记 async.enqueue 结构化日志（lane/queue/run_id/job_timeout，无原文）。
inline 模式无 job 超时概念，行为不变。
"""
from __future__ import annotations

from typing import Callable, Optional

from app.config import settings
from app.log import log_event

QUEUE_NAME = "intake"
_COMPONENT = "agent-queue"

# ---- lane → job_timeout（单一来源；2026-07-11 依 tasks.py / model_orchestration.py 逐 lane 核实）----
# rq 默认 job timeout 180s 与单次 LLM 调用预算 llm_timeout（默认 180s）同量级：
# 批次型 lane（worker 内循环逐条调 LLM）必然可能超限被强杀，须随入队下发宽上限。
_BATCH_JOB_TIMEOUT = 1800  # 批次宽上限（任务卡设计裁定）
# run_item_diagnosis 拆逐条目子 job 后（issue #10 卡B1），本 lane = 批次协调 run 的
# 「判活封套」单一来源：批次 run 仍一条、横跨 N 个逐条目子 job，其判死阈值 = job_timeout_for
# ×2 须覆盖整批时长（run_liveness），故保持 batch 档＋翻倍上限（=拆分前口径，判活零回退，
# G2 悬轮自愈不误杀在飞子 job）。逐条目子 job 自身走 single 档（run_item_diagnosis_item）。
_LANE_TIMEOUT_OVERRIDE: dict[str, int] = {"run_item_diagnosis": 2 * _BATCH_JOB_TIMEOUT}

_LANE_TIER: dict[str, str] = {
    # 批次型：orchestration 内 for 循环逐条调 LLM（run_item_diagnosis 拆分后仅作批次协调
    # ＋判活封套，rq 下不再自身循环调 LLM，但 batch 档保留以维持整批判死阈值）
    "run_item_formation": "batch",  # request_item_formation：逐要素各调一次 format_items
    "run_item_diagnosis": "batch",  # 批次协调 run（判活封套；逐条目执行下沉子 job）
    "run_item_structure_recheck": "batch",  # request_item_structure_recheck：逐条目各调一次 recheck
    # 单调用型：每 job 至多一次 LLM 调用
    "run_item_diagnosis_item": "single",  # 诊断逐条目子 job：单条目一次 diagnose（守卫重试上限 1 已含于 single 档 2×llm_timeout）
    "run_source_intake": "single",  # judge 一次
    "run_element_recognition": "single",  # recognize 一次（分段增量未实施，整文单调用）
    "run_element_review": "single",  # review_elements 或 scan_missing 二选一，各一次
    "run_element_execution": "single",  # execute 一次（目标要素合并进单次调用）
    "run_chart_suggestion": "single",  # suggest 一次
    "run_chart_verification": "single",  # verify 一次（单请求单图，非逐条循环）
    # 无 LLM 调用
    "run_docx_export": "single",  # 确定性 docx 转换＋图形栅格化
    "run_search_reindex": "single",  # 派生索引重算（当前无 rq 入队点，防御性登记）
}


def job_timeout_for(task_name: str) -> int:
    """lane 的 rq job_timeout 秒数。未登记 lane 走单调用档并记 WARN（防新 lane 悄悄回落 rq 180s 默认）。"""
    tier = _LANE_TIER.get(task_name)
    single = int(max(2 * settings.llm_timeout, 360))
    if tier is None:
        log_event(
            _COMPONENT,
            "async.job_timeout.unregistered",
            level="WARN",
            lane=task_name,
            job_timeout=single,
            hint="lane missing from _LANE_TIER in app/workers/queue.py; falling back to single-call tier",
        )
        return single
    if tier == "batch":
        return _LANE_TIMEOUT_OVERRIDE.get(task_name, _BATCH_JOB_TIMEOUT)
    return single


def _intake_queue(connection):
    from rq import Queue

    return Queue(QUEUE_NAME, connection=connection)


def worker_count(connection) -> int:
    """监听 QUEUE_NAME 的活跃 RQ worker 数。"""
    from rq import Worker

    return len(Worker.all(connection=connection, queue=_intake_queue(connection)))


def warn_if_no_worker(connection, run_id: Optional[str] = None) -> bool:
    """无活跃 worker → 记 WARN（避免 job 静默排队）。返回是否告警。"""
    try:
        if worker_count(connection) == 0:
            log_event(
                _COMPONENT,
                "async.worker.absent",
                level="WARN",
                queue=QUEUE_NAME,
                run_id=run_id,
                hint="no active rq worker; job will wait. start: docker compose up -d worker",
            )
            return True
    except Exception:  # noqa: BLE001 探测失败不得影响主流程
        pass
    return False


def async_status() -> dict:
    """供启动自检 / 运行态探测：inline | queued(+workers/queued/redis_ok)。

    探测连接带 1s 短超时:redis 不可达时快速返回 redis_ok=False,
    保证 /runtime-status 聚合端点耗时有界(04A §2.1)。
    """
    if not settings.redis_url:
        return {"mode": "inline", "redis_ok": None, "workers": None, "queued": None}
    try:
        from redis import Redis

        conn = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        conn.ping()
        return {
            "mode": "queued",
            "redis_ok": True,
            "workers": worker_count(conn),
            "queued": _intake_queue(conn).count,
        }
    except Exception:  # noqa: BLE001
        return {"mode": "queued", "redis_ok": False, "workers": None, "queued": None}


def warn_if_async_without_worker() -> None:
    """启动自检：REDIS_URL 已配但 redis 不可达 / 无活跃 worker → WARN。"""
    if not settings.redis_url:
        return
    try:
        from redis import Redis

        conn = Redis.from_url(settings.redis_url)
        conn.ping()
    except Exception:  # noqa: BLE001
        log_event(_COMPONENT, "async.redis.unreachable", level="WARN", redis="configured")
        return
    warn_if_no_worker(conn)


def _submit_inline_background(fn: Callable[[str, str], None], context_ref: str, run_id: str) -> None:
    """inline 受理即回：守护线程后台执行治理重活（issue #7）。

    动机：inline 模式把「链式重诊/体检」等挪出 HTTP 请求，接口受理即回。线程内 worker 入口
    自建独立 session（不复用请求 session），无 session 生命周期冲突；任务入口 try/except 自行
    收尸落库。用守护线程（非线程池）：进程退出不被在飞 LLM 任务阻塞（保槽内 PID 纪律）。
    """
    import threading

    threading.Thread(
        target=fn, args=(context_ref, run_id),
        name=f"inline-bg-{run_id}", daemon=True,
    ).start()


def make_enqueue(
    task_name: str = "run_source_intake", *, background_inline: bool = False
) -> Callable[[str, str], None]:
    """入队闭包。rq：真异步入队（携 lane job_timeout）。inline：默认同步就地执行；
    background_inline=True 时提交守护线程（每次单发，无池）、受理即回（issue #7；仅治理重活 lane 开启）。
    """
    task_path = f"app.workers.tasks.{task_name}"
    if settings.redis_url:
        from redis import Redis

        connection = Redis.from_url(settings.redis_url)
        queue = _intake_queue(connection)

        def _enqueue_rq(context_ref: str, run_id: str) -> None:
            warn_if_no_worker(connection, run_id)  # 韧性：无 worker 先告警，再入队
            job_timeout = job_timeout_for(task_name)
            queue.enqueue(task_path, context_ref, run_id, job_timeout=job_timeout)
            log_event(
                _COMPONENT,
                "async.enqueue",
                lane=task_name,
                queue=QUEUE_NAME,
                run_id=run_id,
                job_timeout=job_timeout,
            )

        return _enqueue_rq

    from app.workers import tasks as _tasks

    fn = getattr(_tasks, task_name)

    if background_inline:
        def _enqueue_inline_bg(context_ref: str, run_id: str) -> None:
            # 受理即回：守护线程后台执行、立即返回（任务入口自建 session、失败自行落库）
            _submit_inline_background(fn, context_ref, run_id)
            log_event(_COMPONENT, "async.inline_background", lane=task_name, run_id=run_id)

        return _enqueue_inline_bg

    def _enqueue_inline(context_ref: str, run_id: str) -> None:
        fn(context_ref, run_id)  # 同步就地执行

    return _enqueue_inline
