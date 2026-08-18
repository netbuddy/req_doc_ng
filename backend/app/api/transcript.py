"""演示留痕：写辅助 + 读端点（AI 对话演示简化方案 2026-07-18 §2.2/§2.3）。

三个对话页（知识抽取/条目形成/条目评审）区5 消息的服务端留痕，供刷新后水合——现状刷新即失。

不变式：
- **服务层零改动**：三个 dialogue 端点的写点全部经本模块 record_transcript()，用独立短 session
  即写即提交（不掺入服务事务；业务失败不回滚已写的 user 行）。
- **append-only**：本模块只 INSERT，绝不 UPDATE/DELETE（唯一删除例外＝seed_full_demo --reset）。
- **写点边界＝「现状刷新后会消失的内容」**：analysis/formation 无投影→用户消息与助手文本行全写；
  review 有 LDM-015 投影→按结果条件写（COMMAND 写、EXPLANATION/DRAFT/REEVAL 不写，避免与投影双气泡）。
- created_at 显式赋微秒精度 UTC 值：SQLite CURRENT_TIMESTAMP 仅秒级，独立事务同秒会撞排序键。
  review 的 user 行用端点入口时刻 received_at（命令先于其副作用卡，合并保序）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ChatTranscriptRead,
    ChatTranscriptRowRead,
    ElementDialogueResult,
    FormationDialogueResult,
    ReviewDialogueCommand,
    ReviewDialogueResult,
)
from app.db.models import DemoChatTranscript
from app.domain.enums import DialogueOutcomeType
from app.deps import new_session
from app.log import log_event

router = APIRouter(tags=["demo-chat-transcript"])

_COMPONENT = "backend-api"

# 渠道（== DemoChatTranscript.channel）
CHANNEL_ANALYSIS = "analysis"
CHANNEL_FORMATION = "formation"
CHANNEL_REVIEW = "review"

# kind（== DemoChatTranscript.kind；供水合映射气泡语气）
KIND_FREE_TEXT = "free_text"
KIND_COMMAND = "command"
KIND_COMMAND_RESULT = "command_result"
KIND_SOURCE_CANDIDATES = "source_candidates"
KIND_FAILURE_NOTE = "failure_note"

_SLASH_PREFIXES = ("/", "／")
_DEFAULT_EXECUTED = "已执行。"
_DEFAULT_REFUSED = "命令未被受理，请调整后重试。"
_DEFAULT_EXPLANATION = "（无解释内容）"
_DEFAULT_DRAFT = "已起草修订建议（候选，未采纳零副作用）——见下方建议卡"
_DEFAULT_CANDIDATES = "已找到候选来源。"


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def user_kind(message: str) -> str:
    """斜杠前缀（/ 或全角／）判为命令，否则自由文本。"""
    return KIND_COMMAND if message.lstrip().startswith(_SLASH_PREFIXES) else KIND_FREE_TEXT


def _echo(operation_label: str | None) -> str:
    """助手文本前的操作回显（与前端 `${echo}` 一致；无 label 时为空）。"""
    return f"［{operation_label}］" if operation_label else ""


# ---------------------------------------------------------------------------
# 写辅助
# ---------------------------------------------------------------------------

def record_transcript(
    *,
    channel: str,
    project_ref: str | uuid.UUID,
    context_ref: str | uuid.UUID,
    role: str,
    kind: str,
    content: dict,
    at: datetime | None = None,
) -> None:
    """独立短 session 即写即提交一行留痕（不掺入服务事务）。

    留痕是演示期旁路记录，绝不拖累主链路：整体包 try/except，写失败只记 WARN 日志、
    不上抛（F3/F9）——避免已提交的领域命令因留痕写失败被报成 500 而诱发用户重试重复执行。
    """
    try:
        session = new_session()
        try:
            session.add(DemoChatTranscript(
                project_ref=_as_uuid(project_ref),
                channel=channel,
                context_ref=_as_uuid(context_ref),
                role=role,
                kind=kind,
                content=json.dumps(content, ensure_ascii=False),
                created_at=at or datetime.now(timezone.utc),
            ))
            session.commit()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 旁路留痕绝不影响主链路：写失败只记日志，绝不上抛
        log_event(
            _COMPONENT, "demo_transcript.write.failed", level="WARN",
            channel=channel, role=role, kind=kind,
            msg="留痕写入失败，已吞异常不影响主链路",
        )


def record_user_message(
    *, channel: str, project_ref: str, context_ref: str, message: str,
    at: datetime | None = None,
) -> None:
    """受理即留痕：写 user 行（业务失败不回滚——独立事务先行提交）。"""
    record_transcript(
        channel=channel, project_ref=project_ref, context_ref=context_ref,
        role="user", kind=user_kind(message), content={"text": message}, at=at,
    )


def _analysis_assistant_row(result: ElementDialogueResult) -> tuple[str, dict] | None:
    """知识抽取页助手终态行（(kind, content) 或 None＝无可见气泡如 queued）。"""
    outcome = result.outcome
    if outcome == "executed":
        text = f"{_echo(result.operation_label)}{result.message or _DEFAULT_EXECUTED}"
        return KIND_COMMAND_RESULT, {"text": text}
    if outcome in ("clarify", "cannot_comply", "unknown_command", "rejected_precheck"):
        return KIND_FAILURE_NOTE, {"text": result.message or _DEFAULT_REFUSED}
    # queued：前端仅链路条 + AgentRun 跟踪，无文本气泡
    return None


def _formation_assistant_row(result: FormationDialogueResult) -> tuple[str, dict] | None:
    """条目形成页助手终态行（(kind, content) 或 None）。"""
    outcome = result.outcome
    if outcome == "executed":
        return KIND_COMMAND_RESULT, {"text": f"{_echo(result.operation_label)}{result.message or _DEFAULT_EXECUTED}"}
    if outcome == "draft":
        return KIND_COMMAND_RESULT, {"text": f"{_echo(result.operation_label)}{_DEFAULT_DRAFT}"}
    if outcome == "explanation":
        return KIND_FREE_TEXT, {"text": result.explanation or _DEFAULT_EXPLANATION}
    if outcome == "queued":
        # /复核（structure.recheck）带 message 时有 sys-ok 气泡；/生成条目 无气泡
        if result.operation == "structure.recheck" and result.message:
            return KIND_COMMAND_RESULT, {"text": result.message}
        return None
    if outcome in ("clarify", "cannot_comply", "unknown_command", "rejected_precheck"):
        return KIND_FAILURE_NOTE, {"text": result.message or _DEFAULT_REFUSED}
    return None


def record_analysis_assistant(project_ref: str, context_ref: str, result: ElementDialogueResult) -> None:
    row = _analysis_assistant_row(result)
    if row is None:
        return
    kind, content = row
    record_transcript(
        channel=CHANNEL_ANALYSIS, project_ref=project_ref, context_ref=context_ref,
        role="assistant", kind=kind, content=content,
    )


def record_formation_assistant(project_ref: str, context_ref: str, result: FormationDialogueResult) -> None:
    row = _formation_assistant_row(result)
    if row is None:
        return
    kind, content = row
    record_transcript(
        channel=CHANNEL_FORMATION, project_ref=project_ref, context_ref=context_ref,
        role="assistant", kind=kind, content=content,
    )


def _skip_auto_triggered(command: ReviewDialogueCommand) -> bool:
    """页面自行发起的命令要不要跳过留痕。

    留痕记的是对话——用户说了什么、助手答了什么。页面为了把界面填满而自己发的命令不是对话：
    条目一进入「待补充来源」态，页面就自动发一次 /找来源 去取候选（前端 useEffect），
    这次调用与用户手敲同一个端点，后端看到的命令正文也一样。若照写留痕，用户每刷新一次
    页面就会多出一对自己从未输入过的问答气泡（冷审查 T20260718-demo-chat-transcript F2）。

    判据取命令自带的 user_initiated 标记而不是「操作是不是 find_sources」：用户手敲的
    /找来源 是真的对话，必须留下（演示脚本第 3 节第 2 步正是手敲这条命令）。区分「谁发的」
    才是这里要的口径，「发的是什么」不是。
    """
    if command.user_initiated:
        return False
    log_event(
        _COMPONENT, "demo_transcript.skip.auto_triggered", level="INFO",
        channel=CHANNEL_REVIEW, item_ref=command.item_ref,
        msg="页面自动发起的命令不写留痕",
    )
    return True


def record_review_success(
    project_ref: str, context_ref: str, command: ReviewDialogueCommand,
    result: ReviewDialogueResult, received_at: datetime,
) -> None:
    """评审页成功路径按结果条件写：仅 COMMAND 写（EXPLANATION/DRAFT/REEVAL 由 LDM-015 投影重放）。

    user 行 created_at 用 received_at（端点入口时刻）——保证命令排在其副作用卡（投影按派发时刻）之前。
    页面自行发起的命令（user_initiated=False）一律不写：见 _skip_auto_triggered。
    """
    if _skip_auto_triggered(command):
        return
    if result.outcome_type != DialogueOutcomeType.COMMAND:
        return
    record_user_message(
        channel=CHANNEL_REVIEW, project_ref=project_ref, context_ref=context_ref,
        message=command.message, at=received_at,
    )
    # 助手回执显式取 received_at 加极小增量（F5）：与用户行同一时间基准，保证回执排在
    # 用户命令之后、其副作用产生的结论卡（事务开始时刻 server_default）之前。
    assistant_at = received_at + timedelta(milliseconds=1)
    if result.source_candidates:
        record_transcript(
            channel=CHANNEL_REVIEW, project_ref=project_ref, context_ref=context_ref,
            role="assistant", kind=KIND_SOURCE_CANDIDATES,
            content={
                "text": result.message or _DEFAULT_CANDIDATES,
                "candidates": [c.model_dump() for c in result.source_candidates],
            },
            at=assistant_at,
        )
    else:
        # 与 analysis/formation 两渠道对齐（F7）：套操作回显前缀，且 message 空时退回
        # next_action，再退回 _DEFAULT_EXECUTED，避免落成空串气泡且补回 ［操作名］ 前缀。
        text = f"{_echo(result.operation_label)}{result.message or result.next_action or _DEFAULT_EXECUTED}"
        record_transcript(
            channel=CHANNEL_REVIEW, project_ref=project_ref, context_ref=context_ref,
            role="assistant", kind=KIND_COMMAND_RESULT, content={"text": text},
            at=assistant_at,
        )


def record_review_failure(
    project_ref: str, context_ref: str, command: ReviewDialogueCommand,
    message: str, received_at: datetime,
) -> None:
    """评审页异常路径：写 user 行（受理即留痕）＋ failure_note 行（用户可见失败原因）。

    页面自行发起的命令同样不写——自动查候选失败是页面自己的事，不该冒充用户的一次失败发言。
    """
    if _skip_auto_triggered(command):
        return
    record_user_message(
        channel=CHANNEL_REVIEW, project_ref=project_ref, context_ref=context_ref,
        message=command.message, at=received_at,
    )
    record_transcript(
        channel=CHANNEL_REVIEW, project_ref=project_ref, context_ref=context_ref,
        role="assistant", kind=KIND_FAILURE_NOTE, content={"text": message},
    )


# ---------------------------------------------------------------------------
# 读端点
# ---------------------------------------------------------------------------

def _read_session() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()


@router.get("/projects/{project_id}/chat-transcript", response_model=ChatTranscriptRead)
def read_chat_transcript(
    project_id: str,
    channel: str | None = None,
    context_ref: str | None = None,
    session: Session = Depends(_read_session),
) -> ChatTranscriptRead:
    """按 (channel, context_ref) 拉取留痕行（升序，演示容量不分页）。

    非法 UUID（如夹具态标识 "ITEM-PENDING-1"）返回空 rows 而非 500（F6）：留痕表主键为 UUID，
    非 UUID 引用不可能有留痕行，静默返回空集，避免每次进页在服务端积累一条未处理 500。
    """
    try:
        project_ref = _as_uuid(project_id)
        context_uuid = _as_uuid(context_ref) if context_ref else None
    except (ValueError, AttributeError, TypeError):
        log_event(
            _COMPONENT, "demo_transcript.read.invalid_ref", level="WARN",
            channel=channel, msg="非法 UUID 引用，返回空 rows",
        )
        return ChatTranscriptRead(rows=[])
    stmt = select(DemoChatTranscript).where(DemoChatTranscript.project_ref == project_ref)
    if channel:
        stmt = stmt.where(DemoChatTranscript.channel == channel)
    if context_uuid is not None:
        stmt = stmt.where(DemoChatTranscript.context_ref == context_uuid)
    stmt = stmt.order_by(DemoChatTranscript.created_at, DemoChatTranscript.id)
    rows = session.scalars(stmt).all()
    return ChatTranscriptRead(rows=[
        ChatTranscriptRowRead(
            id=str(r.id),
            channel=r.channel,
            context_ref=str(r.context_ref),
            role=r.role,
            kind=r.kind,
            content=json.loads(r.content or "{}"),
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ])
