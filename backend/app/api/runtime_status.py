"""运行态面板 / 诊断中心读端点(04A §2.1)。

基础设施只读投影,与 /health 同属 infra 层:不落业务表、不做写操作。
探测失败一律降级进响应体(组件 down/告警),端点本身永不 500。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from app.adapters.llm import probe_llm_service
from app.api.schemas import RuntimeStatusRead
from app.config import settings
from app.deps import agent_run_event_bus, new_session
from app.diagnostics import buffer as diagnostics_buffer
from app.log import log_event
from app.services.runtime_status import build_runtime_status
from app.workers.queue import QUEUE_NAME, async_status

router = APIRouter(tags=["infra"])

_COMPONENT = "runtime-status"


def _llm_probe() -> dict:
    """按生效配置（DB 保存优先，回落 env）探测模型服务；配置读取失败回落 env。"""
    from app.services.config_registry import resolve_llm_settings

    session = new_session()
    try:
        effective = resolve_llm_settings(session, settings)
    except Exception:  # noqa: BLE001 配置面失败不破坏探测
        effective = settings
    finally:
        session.close()
    return probe_llm_service(effective)


def _queued_run_ids() -> Optional[set[str]]:
    """队列中仍挂着任务的 run_id 集合，供悬队自愈判断"该 run 是否还有活任务"。

    - inline 模式（无 REDIS_URL）压根没有队列，空集就是事实：超期仍 queued 的行不可能
      还等着被执行；
    - rq 模式扫待执行队列与执行中/延迟/定时三个登记表，取每个 job 的 run_id
      （入队约定 `queue.enqueue(task_path, context_ref, run_id)`，run_id 是第二个位置参数）；
    - 探测失败（Redis 不可达等）返回 None＝查不到，此时调用方一律不判死。
    """
    if not settings.redis_url:
        return set()
    try:
        from redis import Redis
        from rq import Queue
        from rq.job import Job
        from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry

        conn = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        queue = Queue(QUEUE_NAME, connection=conn)
        job_ids = set(queue.get_job_ids())
        for registry_class in (StartedJobRegistry, DeferredJobRegistry, ScheduledJobRegistry):
            job_ids |= set(registry_class(queue=queue).get_job_ids())
        run_ids: set[str] = set()
        for job in Job.fetch_many(list(job_ids), conn):
            if job is not None and len(job.args or ()) >= 2 and job.args[1]:
                run_ids.add(str(job.args[1]))
        return run_ids
    except Exception as exc:  # noqa: BLE001 查不到就不判死，不是错误路径
        log_event(
            _COMPONENT, "runtime.queue_scan.failed", level="WARN",
            ok=False, error_code=type(exc).__name__,
            hint="队列状态查不到，本次跳过悬队自愈（不判死）",
        )
        return None


@router.get("/runtime-status", response_model=RuntimeStatusRead)
def get_runtime_status() -> RuntimeStatusRead:
    return build_runtime_status(
        session_factory=new_session,
        async_probe=async_status,
        event_bus_live=bool(getattr(agent_run_event_bus, "live", False)),
        diagnostics_snapshot=diagnostics_buffer.snapshot,
        llm_probe=_llm_probe,
        queued_run_ids_probe=_queued_run_ids,
    )
