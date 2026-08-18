"""确认动作的在途修订守卫（T20260725-revision-inflight-guard）。

背景：2026-07-25 用户报障——对一条知识项发了 AI 修订指令，9 秒后 AI 稿才落库，
其间用户已把这条确认掉，稿件成了没人采纳的孤儿。本组用例覆盖三件：
① 确认前能查出「这条正被 AI 起草修订」，且坚持确认时留痕记下这件事；
② 判活复用 run_liveness——僵尸运行不算在途，不让守卫变成永久的二次确认；
③ 回流要把搁置的修订稿带回来（既有 clear_review 会把它一并清掉）。

守卫是软拦截：一条都不拦，只影响预检读数与确认留痕。故每组都配一条
「无在途修订时行为与守卫上线前一致」的回归断言。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.schemas import (
    ElementDecisionCommand,
    ElementDecisionPrecheckCommand,
    ElementRecognitionCommand,
    ElementReopenCommand,
    ElementRevisionCommand,
    RevisionFinalizeCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.domain.enums import ModelVerdict
from app.db.models import (
    AgentRun,
    IntakeRecord,
    Material,
    Project,
    RequirementElement,
)
from app.repositories.sqlalchemy import build_sql_analysis_service
from app.services.run_liveness import run_liveness_deadline_seconds

_LANE = "run_element_execution"


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed_workspace(session) -> tuple[str, list[str]]:
    """接入材料 → 识别 → 得到一个有 2 条可确认知识项的工作区。返回 (上下文, 知识项 refs)。

    stub 识别把第 2 句判成「建议剔除」，那种条目在撤回到正常列表前本来就确认不了，
    与在途修订守卫无关。故原文给三句、只取模型判为可处理的两条，免得用例撞上另一道门。
    """
    p = Project(name="demo")
    session.add(p)
    session.flush()
    mat = Material(
        project_id=p.id,
        raw_text="系统应支持导出 docx。这段是闲聊。导出结果需保留来源追溯。",
        source_note="访谈",
    )
    session.add(mat)
    session.flush()
    session.add(IntakeRecord(
        project_id=p.id, context_ref=uuid.uuid4(),
        intake_conclusion="accepted", material_ref=mat.id,
    ))
    session.flush()

    svc = build_sql_analysis_service(session, auto_complete=True)
    r = svc.submit_element_recognition(ElementRecognitionCommand(
        project_ref=str(p.id), material_ref=str(mat.id),
        operator_ref="U1", idempotency_key=f"K-{uuid.uuid4()}",
    ))
    session.commit()
    read = svc.read_element_workspace(r.parse_context_ref)
    usable = [e.id for e in read.elements if e.model_verdict is not ModelVerdict.SUSPECTED_NOISE]
    assert len(usable) == 2
    return r.parse_context_ref, usable


def _version(session, context_ref) -> str:
    svc = build_sql_analysis_service(session, auto_complete=True)
    return str(svc.read_element_workspace(context_ref).workspace_version)


def _dispatch_revision(session, context_ref: str, element_ref: str) -> str:
    """派发一次 AI 修订但不让它跑完，返回那条 operation 的 ref。

    auto_complete=False 摘掉执行回交钩子，于是修订停在「已派发未回交」——
    这正是报障当天那 9 秒里的状态。
    """
    svc = build_sql_analysis_service(session, auto_complete=False)
    result = svc.revise_element(ElementRevisionCommand(
        parse_context_ref=context_ref,
        workspace_version=_version(session, context_ref),
        element_ref=element_ref,
        mode="ai",
        instruction="把这条的边界条件写清楚",
        operator_ref="U1",
        idempotency_key=f"REV-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "accepted"
    return result.operation_context_ref


def _seed_run(session, operation_ref: str, *, status: str = "queued",
              age_seconds: int = 0) -> str:
    """给这条 operation 挂一个指定状态与入队龄的 AgentRun。

    同步装配的编排不写 AgentRun 行（它派发即执行，没有「在途」这个中间态），
    所以在途状态只能在此手工构造——与 HK-1 单飞守卫用例的做法一致。
    """
    run = AgentRun(
        kind="element_execution", status=status,
        context_ref=uuid.UUID(operation_ref),
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )
    session.add(run)
    session.commit()
    return str(run.id)


def _precheck(session, context_ref: str, refs: list[str]):
    svc = build_sql_analysis_service(session, auto_complete=True)
    return svc.precheck_decide_elements(ElementDecisionPrecheckCommand(
        parse_context_ref=context_ref, element_refs=refs,
    ))


def _confirm(session, context_ref: str, refs: list[str], *, ack: bool = False):
    svc = build_sql_analysis_service(session, auto_complete=True)
    result = svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=context_ref,
        workspace_version=_version(session, context_ref),
        element_refs=refs,
        decision="confirm",
        operator_ref="U1",
        idempotency_key=f"DEC-{uuid.uuid4()}",
        inflight_revision_ack=ack,
    ))
    session.commit()
    return result


def _confirm_note(session, context_ref: str, element_ref: str) -> str:
    svc = build_sql_analysis_service(session, auto_complete=True)
    history = svc.read_element_history(context_ref, element_ref)
    confirms = [r for r in history.records if r.action == "confirm"]
    assert confirms, "确认动作应留下一条 confirm 历史"
    return confirms[-1].note or ""


# ---------------------------------------------------------------- A1 单条守卫

@pytest.mark.parametrize("run_status", ["queued", "started"])
def test_precheck_reports_element_with_inflight_revision(session, run_status):
    """A1：修订运行未终态时，预检把这条知识项列出来（含摘要与运行状态）。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    run_id = _seed_run(session, op, status=run_status)

    result = _precheck(session, ctx, refs)

    assert len(result.guarded) == 1
    guarded = result.guarded[0]
    assert guarded.element_ref == refs[0]
    assert guarded.agent_run_ref == run_id
    assert guarded.run_status == run_status
    assert guarded.content_brief  # 弹层要能认出是哪一条


def test_precheck_empty_without_inflight_revision(session):
    """A1 回归：没有在途修订时预检为空——守卫不该凭空拦人。"""
    ctx, refs = _seed_workspace(session)
    assert _precheck(session, ctx, refs).guarded == []


def test_precheck_ignores_terminal_run(session):
    """修订已跑完（succeeded）就不是在途——稿子已经在详情区等着采纳，不必再拦。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="succeeded")

    assert _precheck(session, ctx, refs).guarded == []


def test_precheck_ignores_stale_run(session):
    """僵尸运行不算在途：判活走 run_liveness 单点，守卫不得变成永久的二次确认。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="queued",
              age_seconds=run_liveness_deadline_seconds(_LANE) + 60)

    assert _precheck(session, ctx, refs).guarded == []


def test_confirm_over_inflight_revision_succeeds_and_annotates(session):
    """A1：用户坚持确认——状态照常迁移，留痕记下「确认时有 AI 修订在途」。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="started")

    _confirm(session, ctx, [refs[0]], ack=True)

    row = session.get(RequirementElement, uuid.UUID(refs[0]))
    assert row.process_status == "confirmed"      # 软拦截：一条都不拦
    assert "AI 修订在途" in _confirm_note(session, ctx, refs[0])


def test_confirm_without_inflight_revision_keeps_plain_note(session):
    """A1 回归：无在途修订时确认留痕与守卫上线前一字不差。"""
    ctx, refs = _seed_workspace(session)

    _confirm(session, ctx, [refs[0]])

    assert _confirm_note(session, ctx, refs[0]) == "人工直接确认"


def test_confirm_without_ack_still_passes(session):
    """守卫是软拦截不是门禁：前端漏调预检，确认照样成功（另有 WARN 日志留痕）。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="queued")

    _confirm(session, ctx, [refs[0]], ack=False)

    row = session.get(RequirementElement, uuid.UUID(refs[0]))
    assert row.process_status == "confirmed"
    assert "AI 修订在途" in _confirm_note(session, ctx, refs[0])


def test_reject_does_not_consult_guard(session):
    """拒绝不查守卫：拒绝本就把这条连同稿件一起废掉，没有「搁置修订稿」的问题。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="queued")

    svc = build_sql_analysis_service(session, auto_complete=True)
    svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=ctx, workspace_version=_version(session, ctx),
        element_refs=[refs[0]], decision="reject",
        operator_ref="U1", idempotency_key=f"DEC-{uuid.uuid4()}",
    ))
    session.commit()

    history = svc.read_element_history(ctx, refs[0])
    note = [r for r in history.records if r.action == "reject"][-1].note
    assert note == "人工直接拒绝"


# ---------------------------------------------------------------- A2 批量守卫

def test_precheck_lists_only_inflight_members_of_batch(session):
    """A2：混合批只列出有在途修订的那些条目，其余不误报。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[1])
    _seed_run(session, op, status="queued")

    result = _precheck(session, ctx, refs)

    assert [g.element_ref for g in result.guarded] == [refs[1]]


def test_batch_skip_guarded_confirms_the_rest(session):
    """A2「跳过这些、确认其余」：只提交无在途者，被跳过的仍是待确认。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="queued")

    guarded = {g.element_ref for g in _precheck(session, ctx, refs).guarded}
    rest = [r for r in refs if r not in guarded]
    _confirm(session, ctx, rest)

    assert session.get(RequirementElement, uuid.UUID(refs[0])).process_status \
        == "pending_confirmation"
    assert session.get(RequirementElement, uuid.UUID(refs[1])).process_status == "confirmed"
    assert _confirm_note(session, ctx, refs[1]) == "人工直接确认"  # 无在途者不带注记


def test_batch_confirm_all_annotates_only_guarded_members(session):
    """A2「全部确认」：全过，且注记只落在真有在途修订的那条上。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="started")

    _confirm(session, ctx, refs, ack=True)

    assert all(
        session.get(RequirementElement, uuid.UUID(r)).process_status == "confirmed"
        for r in refs
    )
    assert "AI 修订在途" in _confirm_note(session, ctx, refs[0])
    assert _confirm_note(session, ctx, refs[1]) == "人工直接确认"


def test_multiple_revision_rounds_listed_once(session):
    """连发几轮修订指令的知识项在弹层里只出现一次（否则同一条会被列重）。"""
    ctx, refs = _seed_workspace(session)
    for _ in range(2):
        op = _dispatch_revision(session, ctx, refs[0])
        _seed_run(session, op, status="queued")

    assert len(_precheck(session, ctx, refs).guarded) == 1


# ---------------------------------------------- A4 孤儿稿：回流保稿 → 采纳

def _orphan_draft(session, ctx: str, element_ref: str, draft: str) -> None:
    """造出报障当天那个局面：条目已确认，AI 稿随后才落库，成了没人采纳的孤儿。"""
    _confirm(session, ctx, [element_ref], ack=True)
    row = session.get(RequirementElement, uuid.UUID(element_ref))
    row.revision_draft = draft
    session.commit()


def test_reflow_preserves_orphan_revision_draft(session):
    """A4：回流要把搁置的稿子带回来——既有 clear_review 会把它连同复核结论一起清掉。"""
    ctx, refs = _seed_workspace(session)
    _orphan_draft(session, ctx, refs[0], "系统应支持导出 docx，且导出失败时给出原因。")

    svc = build_sql_analysis_service(session, auto_complete=True)
    svc.reopen_element(ElementReopenCommand(
        parse_context_ref=ctx, workspace_version=_version(session, ctx),
        element_ref=refs[0], reason="回流以采纳搁置的修订稿",
        operator_ref="U1", idempotency_key=f"RO-{uuid.uuid4()}",
    ))
    session.commit()

    row = session.get(RequirementElement, uuid.UUID(refs[0]))
    assert row.process_status == "pending_confirmation"
    assert row.revision_draft == "系统应支持导出 docx，且导出失败时给出原因。"


def test_reflow_then_adopt_applies_draft_to_content(session):
    """A4：回流后走常规采纳，正文真的变成修订稿，版本递增。"""
    ctx, refs = _seed_workspace(session)
    draft = "系统应支持导出 docx，且导出失败时给出原因。"
    _orphan_draft(session, ctx, refs[0], draft)
    version_before = session.get(RequirementElement, uuid.UUID(refs[0])).version

    svc = build_sql_analysis_service(session, auto_complete=True)
    svc.reopen_element(ElementReopenCommand(
        parse_context_ref=ctx, workspace_version=_version(session, ctx),
        element_ref=refs[0], reason="回流以采纳搁置的修订稿",
        operator_ref="U1", idempotency_key=f"RO-{uuid.uuid4()}",
    ))
    session.commit()
    svc.finalize_revision(RevisionFinalizeCommand(
        parse_context_ref=ctx, workspace_version=_version(session, ctx),
        element_ref=refs[0], action="adopt",
        operator_ref="U1", idempotency_key=f"AD-{uuid.uuid4()}",
    ))
    session.commit()

    row = session.get(RequirementElement, uuid.UUID(refs[0]))
    assert row.content == draft
    assert row.process_status == "confirmed"      # 采纳即确认
    assert row.version > version_before
    assert not row.revision_draft                 # 稿子已落地，不再悬着


def test_reopen_without_draft_still_clears_review(session):
    """回流保稿只保未采纳的稿件：复核结论照旧随重开清空（不改 clear_review 语义）。"""
    ctx, refs = _seed_workspace(session)
    row = session.get(RequirementElement, uuid.UUID(refs[0]))
    row.review_conclusion = "needs_revision"
    row.review_basis = "边界条件缺失"
    session.commit()
    _confirm(session, ctx, [refs[0]])

    svc = build_sql_analysis_service(session, auto_complete=True)
    svc.reopen_element(ElementReopenCommand(
        parse_context_ref=ctx, workspace_version=_version(session, ctx),
        element_ref=refs[0], reason=None,
        operator_ref="U1", idempotency_key=f"RO-{uuid.uuid4()}",
    ))
    session.commit()

    row = session.get(RequirementElement, uuid.UUID(refs[0]))
    assert row.review_conclusion is None and row.review_basis is None


# ---------------------------------------------------------------- HTTP 契约

@pytest.fixture()
def http_session():
    """共享内存库（StaticPool）：TestClient 在 threadpool 线程跑同步路由，需跨线程共用同一 DB。"""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_http_precheck_endpoint(http_session):
    """预检端点走 HTTP：出参给前端弹层逐条列名用。"""
    from fastapi.testclient import TestClient

    from app.deps import get_analysis_service
    from app.main import app

    session = http_session
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="queued")

    svc = build_sql_analysis_service(session, auto_complete=True)
    previous = app.dependency_overrides.get(get_analysis_service)
    app.dependency_overrides[get_analysis_service] = lambda: svc
    try:
        resp = TestClient(app).post(
            f"/api/projects/x/elements/{ctx}/decide/precheck",
            json={"parse_context_ref": ctx, "element_refs": refs},
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_analysis_service, None)
        else:
            app.dependency_overrides[get_analysis_service] = previous

    assert resp.status_code == 200
    body = resp.json()
    assert [g["element_ref"] for g in body["guarded"]] == [refs[0]]
    assert body["guarded"][0]["run_status"] == "queued"


def test_agent_run_rows_untouched_by_guard(session):
    """守卫是只读的：预检不写库，AgentRun 一行都不动。"""
    ctx, refs = _seed_workspace(session)
    op = _dispatch_revision(session, ctx, refs[0])
    _seed_run(session, op, status="queued")
    before = [(r.id, r.status) for r in session.scalars(select(AgentRun)).all()]

    _precheck(session, ctx, refs)

    assert [(r.id, r.status) for r in session.scalars(select(AgentRun)).all()] == before
