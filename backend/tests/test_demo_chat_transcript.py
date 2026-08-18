"""演示留痕写点与读端点（AI 对话演示简化方案 2026-07-18 · A1 受理即留痕 / INV-DEMO-1）。

被测对象＝API 层写点 + record_transcript 写辅助 + 读端点，非对话服务本身（后者另有测试）。
故三个 dialogue 服务用 Stub 注入受控结果：同步分支经依赖覆盖，流式分支经 monkeypatch
`_build_async_*`（端点 run() 内从 app.deps 取），两分支均覆盖。留痕写入/读回走真实 DB
（temp 文件 SQLite，独立于配置库；new_session 的全局工厂被指向它）。
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401  register tables
import app.deps as deps
from app.api.schemas import (
    ElementDialogueResult,
    FormationDialogueResult,
    ReviewDialogueResult,
    SourceCandidateRead,
)
from app.db.base import Base, make_engine, make_session_factory
from app.deps import (
    get_analysis_service,
    get_item_formation_service,
    get_item_review_service,
)
from app.domain.enums import DialogueOutcomeType
from app.domain.errors import InvalidInput
from app.main import app


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """temp 文件 SQLite（跨连接/线程共享，流式分支在守护线程写）；全局 session 工厂指向它。"""
    engine = make_engine(f"sqlite:///{tmp_path / 'tx.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(deps, "_SessionFactory", make_session_factory(engine))
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clear_overrides():
    """只回收本文件注入的三个依赖覆盖——不用 .clear()（会连带清掉其它测试模块在导入期设的覆盖）。"""
    yield
    for dep in (get_analysis_service, get_item_formation_service, get_item_review_service):
        app.dependency_overrides.pop(dep, None)


class _StubService:
    """按方法名分派的 dialogue 服务桩：注入 result 或 exc；on_stage 可选。"""

    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc

    def _run(self, command, on_stage=None):
        if on_stage:
            on_stage("accepted")
        if self._exc:
            raise self._exc
        return self._result

    element_dialogue = _run
    formation_dialogue = _run
    review_dialogue = _run


def _install(monkeypatch, dep, build_name, stub):
    """同步分支：覆盖依赖；流式分支：monkeypatch _build_async_*（忽略 session 参数返回桩）。"""
    app.dependency_overrides[dep] = lambda: stub
    monkeypatch.setattr(f"app.deps.{build_name}", lambda session: stub)


def _read(client, pid, channel, ctx):
    r = client.get(
        f"/api/projects/{pid}/chat-transcript",
        params={"channel": channel, "context_ref": ctx},
    )
    assert r.status_code == 200
    return r.json()["rows"]


def _rk(rows):
    return [(x["role"], x["kind"]) for x in rows]


def _stream(client, url, body):
    r = client.post(url, json=body, headers={"Accept": "text/event-stream"})
    _ = r.text  # 读满响应体 → 守护线程 run()（含助手行写入）已结束
    return r


# ---------------------------------------------------------------------------
# 知识抽取页（analysis）：无投影，用户行与助手文本行全写
# ---------------------------------------------------------------------------

def _analysis_body(ctx, message):
    return {
        "parse_context_ref": ctx, "workspace_version": "1", "message": message,
        "operator_ref": "U1", "idempotency_key": uuid.uuid4().hex,
    }


def test_analysis_sync_success_writes_user_and_assistant(db, monkeypatch):
    pid, ctx = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ElementDialogueResult(
        outcome="executed", message="已改类型为质量属性。", operation_label="改类型"))
    _install(monkeypatch, get_analysis_service, "_build_async_analysis_service", stub)
    client = TestClient(app)

    r = client.post(f"/api/projects/{pid}/elements/{ctx}/dialogue",
                    json=_analysis_body(ctx, "/改类型 质量属性"))
    assert r.status_code == 200
    rows = _read(client, pid, "analysis", ctx)
    assert _rk(rows) == [("user", "command"), ("assistant", "command_result")]
    assert rows[0]["content"]["text"] == "/改类型 质量属性"
    assert "改类型" in rows[1]["content"]["text"] and "已改类型" in rows[1]["content"]["text"]


def test_analysis_stream_success_writes_both(db, monkeypatch):
    pid, ctx = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ElementDialogueResult(outcome="executed", message="已执行修订。"))
    _install(monkeypatch, get_analysis_service, "_build_async_analysis_service", stub)
    client = TestClient(app)

    r = _stream(client, f"/api/projects/{pid}/elements/{ctx}/dialogue",
                _analysis_body(ctx, "帮我润色这条表达"))
    assert r.status_code == 200
    assert _rk(_read(client, pid, "analysis", ctx)) == [
        ("user", "free_text"), ("assistant", "command_result")]


def test_analysis_business_failure_keeps_user_row_only(db, monkeypatch):
    pid, ctx = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(exc=InvalidInput("版本冲突"))
    _install(monkeypatch, get_analysis_service, "_build_async_analysis_service", stub)
    client = TestClient(app)

    r = client.post(f"/api/projects/{pid}/elements/{ctx}/dialogue",
                    json=_analysis_body(ctx, "帮我看看有没有问题"))
    assert r.status_code == 400  # InvalidInput → 业务失败
    rows = _read(client, pid, "analysis", ctx)
    assert _rk(rows) == [("user", "free_text")]  # 受理即留痕，业务失败不回滚；无助手行


def test_analysis_queued_writes_no_assistant_row(db, monkeypatch):
    """queued（前端仅链路条+AgentRun，无文本气泡）→ 只 user 行，助手行不写（刷新等价）。"""
    pid, ctx = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ElementDialogueResult(outcome="queued", agent_run_ref="R-1"))
    _install(monkeypatch, get_analysis_service, "_build_async_analysis_service", stub)
    client = TestClient(app)

    client.post(f"/api/projects/{pid}/elements/{ctx}/dialogue",
                json=_analysis_body(ctx, "/改表达 更正式些"))
    assert _rk(_read(client, pid, "analysis", ctx)) == [("user", "command")]


# ---------------------------------------------------------------------------
# 条目形成页（formation）：无投影，同 analysis；解释出口→助手 free_text 行
# ---------------------------------------------------------------------------

def _formation_body(pid, prr, message):
    return {
        "project_ref": pid, "parse_result_ref": prr, "workspace_version": "1",
        "message": message, "operator_ref": "U1", "idempotency_key": uuid.uuid4().hex,
    }


def test_formation_sync_explanation_writes_user_and_ai(db, monkeypatch):
    pid, prr = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=FormationDialogueResult(
        outcome="explanation", explanation="该条目已满足 SMART 表达。"))
    _install(monkeypatch, get_item_formation_service, "_build_async_item_formation_service", stub)
    client = TestClient(app)

    r = client.post(f"/api/projects/{pid}/item-formation/dialogue",
                    json=_formation_body(pid, prr, "这条要素完整吗"))
    assert r.status_code == 200
    rows = _read(client, pid, "formation", prr)
    assert _rk(rows) == [("user", "free_text"), ("assistant", "free_text")]
    assert rows[1]["content"]["text"] == "该条目已满足 SMART 表达。"


def test_formation_stream_failure_keeps_user_row_only(db, monkeypatch):
    pid, prr = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(exc=InvalidInput("工作区未接入"))
    _install(monkeypatch, get_item_formation_service, "_build_async_item_formation_service", stub)
    client = TestClient(app)

    _stream(client, f"/api/projects/{pid}/item-formation/dialogue",
            _formation_body(pid, prr, "/生成条目"))
    # 流式失败经错误帧降级（HTTP 仍 200）；user 行先于分支写入，助手行不写
    assert _rk(_read(client, pid, "formation", prr)) == [("user", "command")]


# ---------------------------------------------------------------------------
# 条目评审页（review）：有 LDM-015 投影，按结果条件写
# ---------------------------------------------------------------------------

def _review_body(pid, item_ref, message):
    return {
        "project_ref": pid, "item_ref": item_ref, "message": message,
        "workspace_version": "1", "operator_ref": "U1", "idempotency_key": uuid.uuid4().hex,
    }


def test_review_command_success_writes_user_and_command_result(db, monkeypatch):
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, command_word="诊断", message="已发起诊断。"))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    r = client.post(f"/api/projects/{pid}/item-reviews/dialogue",
                    json=_review_body(pid, item, "/诊断"))
    assert r.status_code == 200
    rows = _read(client, pid, "review", item)
    assert _rk(rows) == [("user", "command"), ("assistant", "command_result")]
    assert rows[1]["content"]["text"] == "已发起诊断。"
    # user 行 created_at ≤ 助手行（received_at 保序，命令排在其副作用卡之前）
    assert rows[0]["created_at"] <= rows[1]["created_at"]


def test_review_find_sources_attaches_candidates(db, monkeypatch):
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, command_word="找来源",
        source_candidates=[SourceCandidateRead(element_ref="E-1", content="库存要素", rank=1)]))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    client.post(f"/api/projects/{pid}/item-reviews/dialogue",
                json=_review_body(pid, item, "/找来源"))
    rows = _read(client, pid, "review", item)
    assert _rk(rows) == [("user", "command"), ("assistant", "source_candidates")]
    assert rows[1]["content"]["candidates"][0]["element_ref"] == "E-1"


def test_review_auto_triggered_find_sources_writes_nothing(db, monkeypatch):
    """页面自动发起的 /找来源 一行留痕都不写（冷审查 T20260718-demo-chat-transcript F2）。

    条目进入「待补充来源」态时页面会自行发一次 /找来源 取候选。它与用户手敲走同一个端点、
    命令正文也一样，唯一的差别是 user_initiated=False。若照写留痕，用户每刷新一次页面就会
    多出一对自己从未输入过的问答气泡。这里连发三次模拟刷新三次，断言留痕始终为空。
    """
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, command_word="找来源",
        source_candidates=[SourceCandidateRead(element_ref="E-1", content="库存要素", rank=1)]))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    for _ in range(3):
        body = _review_body(pid, item, "/找来源")
        body["user_initiated"] = False
        r = client.post(f"/api/projects/{pid}/item-reviews/dialogue", json=body)
        assert r.status_code == 200  # 命令照常执行，候选照常返回；只是不留痕
        assert r.json()["source_candidates"][0]["element_ref"] == "E-1"
    assert _read(client, pid, "review", item) == []


def test_review_manual_find_sources_still_writes(db, monkeypatch):
    """反面对照：用户手敲的 /找来源 必须照常留痕（演示脚本第 3 节第 2 步正是手敲这条）。

    修复取的是「谁发的」口径而不是「发的是什么」——若按操作名一律不写，这一条会被误伤。
    """
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, command_word="找来源",
        source_candidates=[SourceCandidateRead(element_ref="E-1", content="库存要素", rank=1)]))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    body = _review_body(pid, item, "/找来源")
    body["user_initiated"] = True
    client.post(f"/api/projects/{pid}/item-reviews/dialogue", json=body)
    assert _rk(_read(client, pid, "review", item)) == [
        ("user", "command"), ("assistant", "source_candidates")]


def test_review_auto_triggered_failure_writes_nothing(db, monkeypatch):
    """自动查候选失败也不留痕：那是页面自己的事，不该冒充用户的一次失败发言。"""
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(exc=InvalidInput("候选池为空"))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    body = _review_body(pid, item, "/找来源")
    body["user_initiated"] = False
    r = client.post(f"/api/projects/{pid}/item-reviews/dialogue", json=body)
    assert r.status_code == 400
    assert _read(client, pid, "review", item) == []


def test_review_user_initiated_defaults_true(db, monkeypatch):
    """不传 user_initiated 即按用户输入处理——既有调用方（含流式分支）行为一行不变。"""
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, message="已发起诊断。"))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    body = _review_body(pid, item, "/诊断")
    assert "user_initiated" not in body
    client.post(f"/api/projects/{pid}/item-reviews/dialogue", json=body)
    assert _rk(_read(client, pid, "review", item)) == [
        ("user", "command"), ("assistant", "command_result")]


@pytest.mark.parametrize("outcome", [DialogueOutcomeType.EXPLANATION, DialogueOutcomeType.DRAFT,
                                     DialogueOutcomeType.REEVAL])
def test_review_non_command_writes_nothing(db, monkeypatch, outcome):
    """反例：解释/草案/重评成功一律不写（LDM-015 投影已重放，写则双气泡）。"""
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(outcome_type=outcome, explanation="……"))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    client.post(f"/api/projects/{pid}/item-reviews/dialogue",
                json=_review_body(pid, item, "这条为什么被标记"))
    assert _read(client, pid, "review", item) == []


def test_review_business_failure_writes_user_and_failure_note(db, monkeypatch):
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(exc=InvalidInput("命令解释能力未装配"))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    r = client.post(f"/api/projects/{pid}/item-reviews/dialogue",
                    json=_review_body(pid, item, "/采纳结论"))
    assert r.status_code == 400
    rows = _read(client, pid, "review", item)
    assert _rk(rows) == [("user", "command"), ("assistant", "failure_note")]
    assert rows[1]["content"]["text"] == "命令解释能力未装配"


def test_review_stream_command_success_writes_both(db, monkeypatch):
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, message="已撤回。"))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    _stream(client, f"/api/projects/{pid}/item-reviews/dialogue",
            _review_body(pid, item, "/撤回"))
    assert _rk(_read(client, pid, "review", item)) == [
        ("user", "command"), ("assistant", "command_result")]


# ---------------------------------------------------------------------------
# 读端点：跨渠道隔离 + 升序
# ---------------------------------------------------------------------------

def test_read_endpoint_isolates_by_channel_and_context(db, monkeypatch):
    pid = str(uuid.uuid4())
    ctx_a, item_r = str(uuid.uuid4()), str(uuid.uuid4())
    _install(monkeypatch, get_analysis_service, "_build_async_analysis_service",
             _StubService(result=ElementDialogueResult(outcome="executed", message="A")))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service",
             _StubService(result=ReviewDialogueResult(
                 outcome_type=DialogueOutcomeType.COMMAND, message="R")))
    client = TestClient(app)

    client.post(f"/api/projects/{pid}/elements/{ctx_a}/dialogue", json=_analysis_body(ctx_a, "/x"))
    client.post(f"/api/projects/{pid}/item-reviews/dialogue", json=_review_body(pid, item_r, "/y"))

    assert all(x["channel"] == "analysis" for x in _read(client, pid, "analysis", ctx_a))
    assert all(x["channel"] == "review" for x in _read(client, pid, "review", item_r))
    # 无 context_ref 过滤时返回该渠道全部；升序 created_at
    all_analysis = client.get(f"/api/projects/{pid}/chat-transcript",
                              params={"channel": "analysis"}).json()["rows"]
    ats = [x["created_at"] for x in all_analysis]
    assert ats == sorted(ats)


# ---------------------------------------------------------------------------
# 裁定修复回归（T20260718 · F3/F9 护栏 / F6 非法 UUID / F5 时序 / F7 兜底文案）
# ---------------------------------------------------------------------------

def _at(row):
    """留痕行 created_at → aware UTC（SQLite 可能回读 naive，统一按 UTC 归一）。"""
    dt = datetime.fromisoformat(row["created_at"])
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def test_record_transcript_write_failure_does_not_break_endpoint(db, monkeypatch):
    """F3/F9：留痕写入内部异常被吞——主链路仍成功返回 200，不冒 500 诱发重试重复执行。"""
    pid, ctx = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ElementDialogueResult(
        outcome="executed", message="已执行。", operation_label="改类型"))
    _install(monkeypatch, get_analysis_service, "_build_async_analysis_service", stub)

    def _boom():
        raise RuntimeError("transcript backing store down")  # 模拟库瞬断/表未迁移

    monkeypatch.setattr("app.api.transcript.new_session", _boom)
    client = TestClient(app)

    r = client.post(f"/api/projects/{pid}/elements/{ctx}/dialogue",
                    json=_analysis_body(ctx, "/改类型 质量属性"))
    assert r.status_code == 200  # 留痕写失败被吞，主链路不受影响（不 500）


def test_read_endpoint_invalid_uuid_context_returns_empty(db):
    """F6：非 UUID context_ref（夹具态 ITEM-PENDING-1）返回 200 空 rows，不 500。"""
    pid = str(uuid.uuid4())
    client = TestClient(app)
    r = client.get(f"/api/projects/{pid}/chat-transcript",
                   params={"channel": "review", "context_ref": "ITEM-PENDING-1"})
    assert r.status_code == 200
    assert r.json()["rows"] == []


def test_read_endpoint_invalid_project_returns_empty(db):
    """F6 同源：非 UUID project_id 也走空 rows 而非 500。"""
    client = TestClient(app)
    r = client.get("/api/projects/not-a-uuid/chat-transcript", params={"channel": "review"})
    assert r.status_code == 200
    assert r.json()["rows"] == []


def test_review_command_empty_message_falls_back_with_echo(db, monkeypatch):
    """F7：message 与 next_action 皆空时，助手行落 ［操作名］+_DEFAULT_EXECUTED（非空串、带前缀）。"""
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, operation_label="撤回",
        message=None, next_action=None))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    client.post(f"/api/projects/{pid}/item-reviews/dialogue",
                json=_review_body(pid, item, "/撤回"))
    rows = _read(client, pid, "review", item)
    assert _rk(rows) == [("user", "command"), ("assistant", "command_result")]
    text = rows[1]["content"]["text"]
    assert text  # 非空串气泡
    assert text == "［撤回］已执行。"  # 操作回显前缀 + 兜底文案


def test_review_command_empty_message_uses_next_action(db, monkeypatch):
    """F7：message 空但 next_action 有值时，退回 next_action（仍带 ［操作名］ 前缀）。"""
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    stub = _StubService(result=ReviewDialogueResult(
        outcome_type=DialogueOutcomeType.COMMAND, operation_label="诊断",
        message=None, next_action="结论产出后进入待裁决。"))
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    client.post(f"/api/projects/{pid}/item-reviews/dialogue",
                json=_review_body(pid, item, "/诊断"))
    rows = _read(client, pid, "review", item)
    assert rows[1]["content"]["text"] == "［诊断］结论产出后进入待裁决。"


class _SlowReviewStub:
    """评审服务桩，注入服务耗时（模拟 LLM 解释延迟）：用于验证回执时序锚定 received_at。"""

    def __init__(self, result, delay):
        self._result, self._delay = result, delay

    def review_dialogue(self, command, on_stage=None):
        if on_stage:
            on_stage("accepted")
        time.sleep(self._delay)
        return self._result


def test_review_receipt_anchored_to_received_at_before_verdict_epoch(db, monkeypatch):
    """F5：助手回执 at 晚于用户行、且锚定 received_at（+1ms）——远早于服务派发期的结论卡时刻。

    结论卡＝领域轮次卡（server_default=事务开始时刻），在服务派发期（LLM 解释后）产生；
    此处用服务耗时 delay 建模该时刻上界 t_after。回执取 received_at+1ms << t_after，故排在结论卡之前。
    """
    pid, item = str(uuid.uuid4()), str(uuid.uuid4())
    delay = 0.2
    stub = _SlowReviewStub(
        ReviewDialogueResult(outcome_type=DialogueOutcomeType.COMMAND, message="已发起诊断（1 条）。"),
        delay,
    )
    _install(monkeypatch, get_item_review_service, "_build_async_item_review_service", stub)
    client = TestClient(app)

    t_before = datetime.now(timezone.utc)
    client.post(f"/api/projects/{pid}/item-reviews/dialogue",
                json=_review_body(pid, item, "/诊断 标准"))
    t_after = datetime.now(timezone.utc)  # 服务已完成，结论卡时刻的上界

    rows = _read(client, pid, "review", item)
    user_at, asst_at = _at(rows[0]), _at(rows[1])
    # 回执排在用户命令之后
    assert asst_at > user_at
    # 回执锚定 received_at（与用户行仅差 +1ms 增量），非写入时刻——差值远小于服务耗时
    assert (asst_at - user_at) < timedelta(milliseconds=50)
    # 回执早于结论卡（建模为 t_after 上界；received_at≈t_before，服务耗时 delay 拉开间距）
    assert asst_at < t_after - timedelta(seconds=delay / 2)
    assert asst_at >= t_before
