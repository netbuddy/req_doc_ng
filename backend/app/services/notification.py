"""通知服务（04A §2.1 通知徽标）。

只承接"需要人处理或确认"的事件：运行日志、结构化事件、健康探测只作为来源，
不逐条转通知。notify_safely 绝不抛出——通知失败不得影响业务主流程。
铁律（AGENTS.md 规则 8）：title/summary 只放稳定码与提示文案，
绝不放 error 原文、prompt、模型响应或用户敏感输入。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.log import log_event
from app.repositories.notification import SqlNotificationRepository

_COMPONENT = "notification"

# AgentRun.kind → 用户可读任务名（通知标题用，避免暴露内部命名细节之外还要可读）
AGENT_RUN_KIND_LABELS = {
    "source_intake": "来源接入判定",
    "element_recognition": "知识项识别",
    "element_review": "要素 AI 复核",
    "element_execution": "要素操作 AI 执行",
    "item_formation": "需求条目形成",
    "item_diagnosis": "条目评审诊断",
    "item_structure_recheck": "条目结构复核",
    "chart_suggestion": "图表源码建议",
    "chart_verification": "图文一致性核对",
    "docx_export": "docx 导出转换",
}


def notify_safely(
    session: Session,
    *,
    kind: str,
    dedup_key: str,
    title: str,
    summary: str = "",
    project_ref: Optional[str] = None,
    ref: Optional[str] = None,
) -> None:
    """落一条通知（insert-or-touch）；任何异常只记 WARN，不上抛。"""
    try:
        SqlNotificationRepository(session).notify(
            kind=kind, dedup_key=dedup_key, title=title, summary=summary,
            project_ref=project_ref, ref=ref,
        )
        log_event(_COMPONENT, "notification.recorded", kind=kind, ok=True)
    except Exception as exc:  # noqa: BLE001 通知失败不得影响主流程
        log_event(
            _COMPONENT, "notification.record_failed", level="WARN",
            kind=kind, ok=False, error_code=type(exc).__name__,
        )


def notify_agent_run_failed(session: Session, run_id: str, run_kind: str) -> None:
    """AgentRun 失败 → 需人工重试/降级的通知（04A §2.1 计数来源）。"""
    label = AGENT_RUN_KIND_LABELS.get(run_kind, run_kind)
    notify_safely(
        session,
        kind="agent_run.failed",
        dedup_key=f"agent_run.failed:{run_id}",
        title=f"AI 任务失败：{label}",
        summary="任务已标记失败，可在对应工作台重试、补充材料或转人工处理。",
        ref=run_id,
    )


def notify_agent_run_lost(session: Session, run_kind: str, subject_ref: str) -> None:
    """悬轮读侧对账（HK-2）run 缺失分支：无 run 行可标记失败时的防静默通知。

    run_stale 分支经 mark_failed 走 notify_agent_run_failed，不用本函数；
    subject_ref=被收尸对象（轮次/批次 ref），兼作 dedup 锚。
    """
    label = AGENT_RUN_KIND_LABELS.get(run_kind, run_kind)
    notify_safely(
        session,
        kind="agent_run.failed",
        dedup_key=f"agent_run.lost:{subject_ref}",
        title=f"AI 任务失联：{label}",
        summary="执行记录缺失，卡住的轮次已自动对账为失败；可在对应工作台重新发起。",
        ref=subject_ref,
    )


def notify_export_failed(
    session: Session, export_ref: str, document_title: str, project_ref: Optional[str]
) -> None:
    """docx 导出失败（业务停靠）→ 需人工降级导出的通知。"""
    notify_safely(
        session,
        kind="export.failed",
        dedup_key=f"export.failed:{export_ref}",
        title=f"docx 导出失败：{document_title}",
        summary="候选 docx 转换未完成，可重试导出或改用人工降级导出并上传定稿件。",
        project_ref=project_ref,
        ref=export_ref,
    )
