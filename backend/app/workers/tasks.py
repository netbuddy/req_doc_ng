"""RQ worker 任务：来源接入判定（judge→登记 LDM-015→accept + AgentRun 状态 + 进度事件）。

worker 进程 `rq worker -u <redis> intake` 执行 run_source_intake；inline 模式也调它。
每次 AgentRun 状态迁移在 **DB 提交之后** 经事件总线 publish，跨进程推送给 SSE。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters.event_bus import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_STARTED,
    NullAgentRunEventBus,
    build_agent_run_event_bus,
)
from typing import Callable

from app.adapters.llm import (
    SourceElementRecognizer,
    SourceIntakeJudge,
    build_chart_source_suggester,
    build_chart_verifier,
    build_element_operation_executor,
    build_element_reviewer,
    build_item_structure_rechecker,
    build_requirement_item_diagnoser,
    build_requirement_item_formatter,
    build_source_element_recognizer,
    build_source_intake_judge,
)
from app.config import settings
from app.db.base import make_engine, make_session_factory
from app.interfaces.services import AgentRunEventBus
from app.log import log_event
from app.repositories.agent_run import SqlAgentRunRepository
from app.repositories.sqlalchemy import (
    run_chart_suggestion_judgement,
    run_chart_verification_judgement,
    run_element_execution_judgement,
    run_element_recognition_judgement,
    run_element_review_judgement,
    run_item_diagnosis_judgement,
    run_item_diagnosis_step,
    run_item_formation_judgement,
    run_item_structure_recheck_judgement,
    run_source_intake_judgement,
)

_COMPONENT = "agent-worker"

# 进程级引擎（真 Postgres）；worker 与 inline 都用它，独立 session、同库。
_engine = make_engine(settings.database_url)
_SessionFactory = make_session_factory(_engine)


def _llm_settings():
    """模型服务域配置读通（CONN-006：配置期写入 → 适配器读取）。

    DB 已保存配置覆盖 env 默认；读取失败回落 env（配置面失败不破坏治理任务）。
    运行时调用链不变：仍由本 worker 构建并调用适配器，只换参数来源。
    """
    from app.services.config_registry import resolve_llm_settings_or_env

    session = _SessionFactory()
    try:
        # 读取失败回落 env 并记 WARN 的逻辑与交互式 lane 共用一份实现（config_registry 里）。
        return resolve_llm_settings_or_env(session, settings)
    finally:
        session.close()


def _process(
    session: Session,
    context_ref: str,
    agent_run_id: str,
    judge: SourceIntakeJudge,
    bus: AgentRunEventBus | None = None,
) -> None:
    """可测核心：显式 session + judge + 事件总线（默认 Null，不推送）。

    顺序铁律：先 commit 再 publish（DB 是事实源，通知不得早于事实）。
    """
    bus = bus or NullAgentRunEventBus()
    agent_runs = SqlAgentRunRepository(session)

    agent_runs.mark_started(agent_run_id)
    session.commit()
    bus.publish(agent_run_id, EVENT_STARTED)
    log_event(_COMPONENT, "agent.run.started", run_id=agent_run_id, kind="source_intake")

    run_source_intake_judgement(session, context_ref, judge)

    agent_runs.mark_succeeded(agent_run_id)
    session.commit()
    bus.publish(agent_run_id, EVENT_COMPLETED)
    log_event(_COMPONENT, "agent.run.succeeded", run_id=agent_run_id, ok=True)


def run_source_intake(context_ref: str, agent_run_id: str) -> None:
    """RQ 入口（也供 inline 调用）。worker 独立进程，自建 judge / 事件总线连接。"""
    session = _SessionFactory()
    judge = build_source_intake_judge(_llm_settings())
    bus = build_agent_run_event_bus(settings)
    try:
        _process(session, context_ref, agent_run_id, judge, bus)
    except Exception as exc:  # 判定/写库异常 → 标记失败，不吞事实
        session.rollback()
        SqlAgentRunRepository(session).mark_failed(agent_run_id, str(exc))
        session.commit()
        bus.publish(agent_run_id, EVENT_FAILED)
        log_event(
            _COMPONENT,
            "agent.run.failed",
            level="ERROR",
            run_id=agent_run_id,
            ok=False,
            error_code=type(exc).__name__,
        )
    finally:
        session.close()


def _process_recognition(
    session: Session,
    context_ref: str,
    agent_run_id: str,
    recognizer: SourceElementRecognizer,
    bus: AgentRunEventBus | None = None,
) -> None:
    """要素识别可测核心：显式 session + recognizer + 事件总线。先 commit 再 publish。"""
    bus = bus or NullAgentRunEventBus()
    agent_runs = SqlAgentRunRepository(session)

    agent_runs.mark_started(agent_run_id)
    session.commit()
    bus.publish(agent_run_id, EVENT_STARTED)
    log_event(_COMPONENT, "agent.run.started", run_id=agent_run_id, kind="element_recognition")

    run_element_recognition_judgement(session, context_ref, recognizer)

    agent_runs.mark_succeeded(agent_run_id)
    session.commit()
    bus.publish(agent_run_id, EVENT_COMPLETED)
    log_event(_COMPONENT, "agent.run.succeeded", run_id=agent_run_id, ok=True)


def run_element_recognition(context_ref: str, agent_run_id: str) -> None:
    """RQ 入口（也供 inline 调用）。worker 独立进程，自建 recognizer / 事件总线连接。"""
    session = _SessionFactory()
    recognizer = build_source_element_recognizer(_llm_settings())
    bus = build_agent_run_event_bus(settings)
    try:
        _process_recognition(session, context_ref, agent_run_id, recognizer, bus)
    except Exception as exc:  # 识别/写库异常 → 标记失败，不吞事实
        session.rollback()
        SqlAgentRunRepository(session).mark_failed(agent_run_id, str(exc))
        session.commit()
        bus.publish(agent_run_id, EVENT_FAILED)
        log_event(
            _COMPONENT,
            "agent.run.failed",
            level="ERROR",
            run_id=agent_run_id,
            ok=False,
            error_code=type(exc).__name__,
        )
    finally:
        session.close()


def _run_operation_task(
    kind: str, operation_ref: str, agent_run_id: str,
    judgement: Callable[[Session, str], None],
) -> None:
    """P03/P04 操作任务公共骨架（复核/AI执行）：先 commit 再 publish；失败标记不吞事实。"""
    session = _SessionFactory()
    bus = build_agent_run_event_bus(settings)
    agent_runs = SqlAgentRunRepository(session)
    try:
        agent_runs.mark_started(agent_run_id)
        session.commit()
        bus.publish(agent_run_id, EVENT_STARTED)
        log_event(_COMPONENT, "agent.run.started", run_id=agent_run_id, kind=kind)

        judgement(session, operation_ref)

        agent_runs.mark_succeeded(agent_run_id)
        session.commit()
        bus.publish(agent_run_id, EVENT_COMPLETED)
        log_event(_COMPONENT, "agent.run.succeeded", run_id=agent_run_id, ok=True)
    except Exception as exc:
        session.rollback()
        SqlAgentRunRepository(session).mark_failed(agent_run_id, str(exc))
        session.commit()
        bus.publish(agent_run_id, EVENT_FAILED)
        log_event(
            _COMPONENT, "agent.run.failed", level="ERROR",
            run_id=agent_run_id, ok=False, error_code=type(exc).__name__,
        )
    finally:
        session.close()


def run_element_review(operation_ref: str, agent_run_id: str) -> None:
    """RQ 入口：需求要素 AI 复核（P03）。"""
    reviewer = build_element_reviewer(_llm_settings())
    _run_operation_task(
        "element_review", operation_ref, agent_run_id,
        lambda session, op: run_element_review_judgement(session, op, reviewer),
    )


def run_element_execution(operation_ref: str, agent_run_id: str) -> None:
    """RQ 入口：指定操作 AI 执行（P04）。"""
    executor = build_element_operation_executor(_llm_settings())
    _run_operation_task(
        "element_execution", operation_ref, agent_run_id,
        lambda session, op: run_element_execution_judgement(session, op, executor),
    )


def run_item_formation(formation_context_ref: str, agent_run_id: str) -> None:
    """RQ 入口：条目化批次格式化送检 + 逐要素裁定创建（SCN-002-P01）。"""
    formatter = build_requirement_item_formatter(_llm_settings())
    _run_operation_task(
        "item_formation", formation_context_ref, agent_run_id,
        lambda session, ctx: run_item_formation_judgement(session, ctx, formatter),
    )


def run_item_structure_recheck(recheck_context_ref: str, agent_run_id: str) -> None:
    """RQ 入口：条目结构复核批次（AEP-114）。只判不改，逐条目 commit 投影实时刷新；
    单条目复核失败=失败类 LDM-015、旧投影原样保留，不算任务失败。"""
    rechecker = build_item_structure_rechecker(_llm_settings())
    _run_operation_task(
        "item_structure_recheck", recheck_context_ref, agent_run_id,
        lambda session, ref: run_item_structure_recheck_judgement(session, ref, rechecker),
    )


_diag_item_enqueue: Callable[[str, str], None] | None = None


def _enqueue_item_diagnosis_item(batch_ref: str, agent_run_id: str) -> None:
    """逐条目子 job 链式再入队（rq 真入队；构造一次并缓存复用连接）。

    inline 模式的整批循环不经此路（run_item_diagnosis 直接就地循环收尾）；仅 rq 协调/子
    job 调用，故 make_enqueue 走 rq 分支。惰性构造规避 tasks 自引用的导入期时序。
    """
    global _diag_item_enqueue
    if _diag_item_enqueue is None:
        from app.workers.queue import make_enqueue

        _diag_item_enqueue = make_enqueue("run_item_diagnosis_item")
    _diag_item_enqueue(batch_ref, agent_run_id)


def _process_diagnosis_kickoff(
    session: Session, batch_ref: str, agent_run_id: str,
    agent_runs: SqlAgentRunRepository, bus: AgentRunEventBus,
    enqueue_item: Callable[[str, str], None],
) -> None:
    """rq 批次协调（可测核心）：标记 started + 派发首个逐条目子 job。

    整批诊断 run 仍为一条（批次级）；逐条目执行/收尾由 run_item_diagnosis_item 承接。
    先 commit 再 publish（DB 是事实源）。
    """
    agent_runs.mark_started(agent_run_id)
    session.commit()
    bus.publish(agent_run_id, EVENT_STARTED)
    log_event(_COMPONENT, "agent.run.started", run_id=agent_run_id, kind="item_diagnosis")
    enqueue_item(batch_ref, agent_run_id)


def _process_diagnosis_item(
    session: Session, batch_ref: str, agent_run_id: str,
    diagnoser, agent_runs: SqlAgentRunRepository, bus: AgentRunEventBus,
    enqueue_item: Callable[[str, str], None],
) -> None:
    """逐条目子 job（可测核心）：处理一个待诊断条目并 commit。

    有余 → 链式再入队下一子 job（FIFO 尾部，增量重诊得以交错插入）；
    无余 → 批次收束，mark_succeeded（先 commit 再 publish）。
    """
    has_more = run_item_diagnosis_step(session, batch_ref, diagnoser)
    if has_more:
        enqueue_item(batch_ref, agent_run_id)
        log_event(_COMPONENT, "agent.run.item_step", run_id=agent_run_id,
                  kind="item_diagnosis", has_more=True)
        return
    agent_runs.mark_succeeded(agent_run_id)
    session.commit()
    bus.publish(agent_run_id, EVENT_COMPLETED)
    log_event(_COMPONENT, "agent.run.succeeded", run_id=agent_run_id, ok=True)


def _fail_run(session: Session, agent_run_id: str, exc: BaseException, bus: AgentRunEventBus) -> None:
    """诊断子 job 意外异常收尾：回滚 → 标记批次 run 失败 → publish（不吞事实）。

    单条目诊断失败（LLM 判定失败）已在执行体内落 diagnosis_failed、不抛出，不走此路；
    此处仅承接子 job 骨架级意外异常（失败传播语义与拆分前一致）。
    """
    session.rollback()
    SqlAgentRunRepository(session).mark_failed(agent_run_id, str(exc))
    session.commit()
    bus.publish(agent_run_id, EVENT_FAILED)
    log_event(
        _COMPONENT, "agent.run.failed", level="ERROR",
        run_id=agent_run_id, ok=False, error_code=type(exc).__name__,
    )


def run_item_diagnosis(batch_ref: str, agent_run_id: str) -> None:
    """RQ 入口：条目评审诊断批次（SCN-003-P01；issue #10 卡B1 拆逐条目子 job）。

    rq 模式=批次协调子 job：标记 started 后派发逐条目子 job（run_item_diagnosis_item），
      各子 job 逐条目执行、逐条目 commit、链式再入队；增量重诊在同队列 FIFO 交错插入，
      等待量级从整批余量降为单条目时延。批次 run 仍一条（批次级），判活口径不变
      （lane run_item_diagnosis 仍 batch 档，判死阈值覆盖整批时长，G2 不误杀在飞子 job）。
    inline 模式=整批就地循环（同 N13，逐条目实时刷新）；受理即回由入队层 background_inline
      承接（响应返回后后台执行，issue #7）。
    """
    if not settings.redis_url:  # inline：整批就地循环
        diagnoser = build_requirement_item_diagnoser(_llm_settings())
        _run_operation_task(
            "item_diagnosis", batch_ref, agent_run_id,
            lambda session, ref: run_item_diagnosis_judgement(session, ref, diagnoser),
        )
        return
    session = _SessionFactory()
    bus = build_agent_run_event_bus(settings)
    try:
        _process_diagnosis_kickoff(
            session, batch_ref, agent_run_id, SqlAgentRunRepository(session), bus,
            _enqueue_item_diagnosis_item,
        )
    except Exception as exc:  # 协调阶段意外异常 → 标记批次 run 失败
        _fail_run(session, agent_run_id, exc, bus)
    finally:
        session.close()


def run_item_diagnosis_item(batch_ref: str, agent_run_id: str) -> None:
    """RQ 入口：条目诊断逐条目子 job（拆分调度单元，single 档）。

    处理一个待诊断条目：有余链式再入队、无余收尾 mark_succeeded；单条目诊断失败=该条目
    diagnosis_failed 落库、不夭折批次 run（失败传播语义不变）。inline 模式不派发本子 job。
    """
    session = _SessionFactory()
    diagnoser = build_requirement_item_diagnoser(_llm_settings())
    bus = build_agent_run_event_bus(settings)
    try:
        _process_diagnosis_item(
            session, batch_ref, agent_run_id, diagnoser,
            SqlAgentRunRepository(session), bus, _enqueue_item_diagnosis_item,
        )
    except Exception as exc:  # 子 job 骨架级意外异常 → 标记批次 run 失败
        _fail_run(session, agent_run_id, exc, bus)
    finally:
        session.close()


def run_chart_suggestion(context_ref: str, agent_run_id: str) -> None:
    """RQ 入口：图表源码建议送检（SCN-004-P01-N08；建议登记后待人工采纳）。"""
    suggester = build_chart_source_suggester(_llm_settings())
    _run_operation_task(
        "chart_suggestion", context_ref, agent_run_id,
        lambda session, ref: run_chart_suggestion_judgement(session, ref, suggester),
    )


def run_chart_verification(request_ref: str, agent_run_id: str) -> None:
    """RQ 入口：图文一致性核对送检（SCN-004-P02-N03/N04；失败不降级为纯人工确认）。"""
    verifier = build_chart_verifier(_llm_settings())
    _run_operation_task(
        "chart_verification", request_ref, agent_run_id,
        lambda session, ref: run_chart_verification_judgement(session, ref, verifier),
    )


def run_docx_export(export_ref: str, agent_run_id: str) -> None:
    """RQ 入口：候选 docx 转换执行（SCN-005-P03；转换失败为业务停靠，不算任务失败）。"""
    from app.repositories.publication import SqlPublicationRepository
    from app.services.publication import run_docx_export_judgement

    _run_operation_task(
        "docx_export", export_ref, agent_run_id,
        lambda session, ref: run_docx_export_judgement(SqlPublicationRepository(session), ref),
    )


def run_search_reindex(scope: str = "all", run_id: str = "") -> None:
    """RQ 入口（也供 inline / CLI / seed 调用）：重算 search_index 派生索引（全局检索 02 §5.1）。

    scope="all" → 全项目回填；否则视为 project_id 单项目重算。派生索引，失败不污染事实源；
    只记 scope/计数/error_code，不记 q/body 原文（硬规则 8）。
    """
    from app.services.search_index import build_search_indexer

    session = _SessionFactory()
    try:
        indexer = build_search_indexer(session)
        if scope == "all":
            indexer.reindex_all()
        else:
            indexer.reindex_project(scope)
    except Exception as exc:  # noqa: BLE001 派生索引失败不阻断主流程
        session.rollback()
        log_event(
            _COMPONENT, "search.reindex.failed", level="ERROR",
            run_id=run_id or None, scope=("all" if scope == "all" else "project"),
            error_code=type(exc).__name__,
        )
    finally:
        session.close()
