"""总览台只读投影（AEP-052 资产盘点 / AEP-072 流程阶段派生）测试义务。

设计事实源：app/services/overview.py 派生迁移表（14 行）与
docs/iterations/OVW-001/覆盖标记表.md §1 —— 逐行断言；再加计数口径与 HTTP 形状。
种子直写 ORM 表（与真实写路径同表同列），另以 build_sql_service 驱动一条真实链路校验一致性。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401  register tables
from app.api.schemas import TextIntakeCommand
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    IntakeRecord,
    IntakeRequest,
    ItemFormationRequest,
    Material,
    MaterialParseResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
)
from app.domain.errors import NotFound
from app.repositories.overview_read import OverviewReadRepository
from app.repositories.sqlalchemy import build_sql_service
from app.services.overview import STAGES, OverviewService


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


# ---- 种子助手（直写既有事实源表；列与真实写路径一致）----

def _project(session, name="demo") -> str:
    p = Project(name=name)
    session.add(p)
    session.flush()
    return str(p.id)


def _intake_request(session, pid, note="访谈纪要", stop=None) -> str:
    r = IntakeRequest(
        project_id=uuid.UUID(pid), raw_text="系统应支持导出 docx。", source_note=note,
        operator_ref="U1", idempotency_key=f"K-{uuid.uuid4()}", stop_next_action=stop,
    )
    session.add(r)
    session.flush()
    return str(r.id)


def _record(session, pid, ctx, conclusion, material=None) -> None:
    session.add(IntakeRecord(
        project_id=uuid.UUID(pid), context_ref=uuid.UUID(ctx),
        intake_conclusion=conclusion,
        material_ref=uuid.UUID(material) if material else None,
    ))
    session.flush()


def _material(session, pid) -> str:
    m = Material(project_id=uuid.UUID(pid), raw_text="系统应支持导出 docx。", source_note="访谈纪要")
    session.add(m)
    session.flush()
    return str(m.id)


def _parse_request(session, pid, material, stop=None) -> str:
    r = ParseRequest(
        project_id=uuid.UUID(pid), material_ref=uuid.UUID(material), operator_ref="U1",
        idempotency_key=f"P-{uuid.uuid4()}", stop_next_action=stop,
    )
    session.add(r)
    session.flush()
    return str(r.id)


def _parse_result(session, pid, material, ctx, status="parsed") -> str:
    r = MaterialParseResult(
        project_id=uuid.UUID(pid), material_ref=uuid.UUID(material),
        context_ref=uuid.UUID(ctx), parse_status=status,
    )
    session.add(r)
    session.flush()
    return str(r.id)


def _element(session, pid, pr, etype="functional_requirement",
             status="confirmed", superseded=False) -> str:
    e = RequirementElement(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.UUID(pr), element_type=etype,
        content="系统应支持导出 docx", process_status=status, superseded=superseded,
    )
    session.add(e)
    session.flush()
    return str(e.id)


def _formation(session, pid, parse_ctx, pr, stop=None) -> str:
    r = ItemFormationRequest(
        project_id=uuid.UUID(pid), parse_context_ref=uuid.UUID(parse_ctx),
        parse_result_ref=uuid.UUID(pr), scope_type="all_eligible",
        operator_ref="U1", idempotency_key=f"F-{uuid.uuid4()}", stop_next_action=stop,
    )
    session.add(r)
    session.flush()
    return str(r.id)


def _item(session, pid, pr, fctx, status="pending_confirmation") -> str:
    i = RequirementItem(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.UUID(pr),
        formation_context_ref=uuid.UUID(fctx), req_no="REQ-T1",
        expression="系统应支持导出 docx", req_type="functional", status=status,
        source_element_refs="[]",
    )
    session.add(i)
    session.flush()
    return str(i.id)


def _svc(session) -> OverviewService:
    return OverviewService(OverviewReadRepository(session))


def _single_flow(session, pid):
    flows = _svc(session).list_requirement_flows(pid)
    assert len(flows) == 1
    return flows[0]


def _stage(flow, stage) -> str:
    return next(s.status for s in flow.stages if s.stage == stage)


# ============================================================================
# 派生迁移表逐行断言（行号对应 overview.py 模块注释表）
# ============================================================================

def test_row1_intake_in_progress(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    f = _single_flow(session, pid)
    assert _stage(f, "intake") == "in_progress"
    assert f.current_stage == "intake" and f.resumable
    assert f.intake_context_ref == ctx and f.parse_context_ref is None


def test_row2_intake_stopped_retryable(session):
    pid = _project(session)
    _intake_request(session, pid, stop="重试接入判断")
    f = _single_flow(session, pid)
    assert _stage(f, "intake") == "stopped"
    assert f.current_stage == "intake" and f.resumable


def test_row3_returned_for_supplement_not_resumable(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    _record(session, pid, ctx, "returned_for_supplement")
    f = _single_flow(session, pid)
    assert _stage(f, "intake") == "stopped"
    assert not f.resumable and f.current_stage == "intake"


def test_row4_excluded_not_resumable(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    _record(session, pid, ctx, "excluded")
    f = _single_flow(session, pid)
    assert _stage(f, "intake") == "stopped"
    assert not f.resumable


def test_row5_accepted_no_parse_request(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    f = _single_flow(session, pid)
    assert _stage(f, "intake") == "done"
    assert _stage(f, "analysis") == "not_started"
    assert f.current_stage == "analysis" and f.resumable
    assert f.material_ref == mat and f.parse_context_ref is None


def test_row6_recognition_in_flight(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "in_progress"
    assert f.current_stage == "analysis" and f.parse_context_ref == pctx


def test_row7_recognition_stopped_retryable(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    _parse_request(session, pid, mat, stop="重试识别")
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "stopped"
    assert f.resumable


def test_row8_unprocessable_dead_end(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    _parse_result(session, pid, mat, pctx, status="unprocessable")
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "stopped"
    assert not f.resumable and f.current_stage == "analysis"


def test_row9_parsed_no_confirmed_elements(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr, status="pending_confirmation")
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "in_progress"
    assert f.current_stage == "analysis"


def test_row10_confirmed_but_not_formable_type(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr, etype="term", status="confirmed")  # 支撑类不开门禁
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "in_progress"


def test_row11_formable_confirmed_no_batch(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr)  # confirmed functional_requirement
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "done"
    assert _stage(f, "itemFormation") == "not_started"
    assert f.current_stage == "itemFormation" and f.formation_context_ref is None


def test_row12_batch_in_flight(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr)
    fctx = _formation(session, pid, pctx, pr)
    f = _single_flow(session, pid)
    assert _stage(f, "itemFormation") == "in_progress"
    assert f.formation_context_ref == fctx


def test_row13_batch_stopped_retryable(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr)
    _formation(session, pid, pctx, pr, stop="重试条目化")
    f = _single_flow(session, pid)
    assert _stage(f, "itemFormation") == "stopped"
    assert f.resumable


def test_row14_items_formed_done(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr)
    fctx = _formation(session, pid, pctx, pr)
    _item(session, pid, pr, fctx)
    f = _single_flow(session, pid)
    assert _stage(f, "itemFormation") == "done"
    assert f.current_stage == "itemFormation"
    assert f.formation_context_ref == fctx and f.parse_context_ref == pctx


# ============================================================================
# 补充派生约定
# ============================================================================

def test_item_review_always_not_started(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    f = _single_flow(session, pid)
    assert _stage(f, "itemReview") == "not_started"
    assert [s.stage for s in f.stages] == list(STAGES)


def test_confirmed_gate_excludes_superseded_and_revoked(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr, status="confirmed", superseded=True)  # 被替代不算
    _element(session, pid, pr, status="revoked")  # 已撤销不算
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "in_progress"  # 门禁未开


def test_representative_picks_deepest_parse_request(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    _parse_request(session, pid, mat)  # 浅：裸请求
    deep_ctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, deep_ctx)
    _element(session, pid, pr)
    f = _single_flow(session, pid)
    assert f.parse_context_ref == deep_ctx  # 取到达最深代表
    assert _stage(f, "analysis") == "done"


def test_parsed_without_elements_defensive(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    _parse_result(session, pid, mat, pctx)  # parsed 但无要素（防御边缘）
    f = _single_flow(session, pid)
    assert _stage(f, "analysis") == "in_progress"


def test_flow_title_defaults_when_note_empty(session):
    pid = _project(session)
    _intake_request(session, pid, note="")
    f = _single_flow(session, pid)
    assert f.title == "未命名来源"


# ============================================================================
# AEP-052 计数口径
# ============================================================================

def test_overview_metrics_counts(session):
    pid = _project(session)
    ctx = _intake_request(session, pid, note="盘点材料")
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    pr = _parse_result(session, pid, mat, pctx)
    _element(session, pid, pr, etype="functional_requirement")
    _element(session, pid, pr, etype="quality_attribute", status="pending_confirmation")
    _element(session, pid, pr, etype="term")  # 支撑类：计入存量，不进五卡
    _element(session, pid, pr, etype="functional_requirement", superseded=True)  # 不计
    _element(session, pid, pr, etype="constraint", status="revoked")  # 不计
    fctx = _formation(session, pid, pctx, pr)
    _item(session, pid, pr, fctx)
    _item(session, pid, pr, fctx, status="confirmed")

    ov = _svc(session).read_project_overview(pid)
    asset = {m.key: m.value for m in ov.asset_metrics}
    assert asset == {"materials": 1, "elements": 3, "items": 2,
                     "charts": 0, "documents": 0, "issues": 0}
    types = {m.key: m.value for m in ov.requirement_type_metrics}
    assert types == {"functional": 1, "quality": 1, "constraint": 0, "data": 0, "interface": 0}
    status = {m.key: m.value for m in ov.requirement_status_metrics}
    # closed（已了结＝被替代＋已终止）随转化链一并下发，本例无终态条目故为 0
    assert status == {"pending": 1, "confirmed": 1, "closed": 0}
    assert len(ov.flows) == 1 and ov.flows[0].title == "盘点材料"


def test_empty_project_all_zero(session):
    pid = _project(session, name="空白项目")
    ov = _svc(session).read_project_overview(pid)
    assert all(m.value == 0 for m in ov.asset_metrics)
    assert all(m.value == 0 for m in ov.requirement_type_metrics)
    assert all(m.value == 0 for m in ov.requirement_status_metrics)
    assert ov.flows == []


def test_unknown_project_not_found(session):
    with pytest.raises(NotFound):
        _svc(session).read_project_overview(str(uuid.uuid4()))


def test_projects_isolated(session):
    pid_a = _project(session, name="A")
    pid_b = _project(session, name="B")
    ctx = _intake_request(session, pid_a)
    mat = _material(session, pid_a)
    _record(session, pid_a, ctx, "accepted", material=mat)
    assert len(_svc(session).list_requirement_flows(pid_a)) == 1
    assert _svc(session).list_requirement_flows(pid_b) == []


# ============================================================================
# 终结态处置（OVW-001 修订 2026-07-10：AEP-111 放弃软删 / AEP-112 预填 / dismissable）
# ============================================================================

def test_terminal_rows_dismissable(session):
    pid = _project(session)
    ctx_a = _intake_request(session, pid, note="需补充件")
    _record(session, pid, ctx_a, "returned_for_supplement")
    ctx_b = _intake_request(session, pid, note="已排除件")
    _record(session, pid, ctx_b, "excluded")
    flows = {f.intake_context_ref: f for f in _svc(session).list_requirement_flows(pid)}
    assert flows[ctx_a].dismissable and not flows[ctx_a].resumable
    assert flows[ctx_b].dismissable and not flows[ctx_b].resumable


def test_dead_end_and_active_rows_not_dismissable(session):
    pid = _project(session)
    # 行8 死路：resumable=False 但不可放弃（两口径分离）
    ctx = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    pctx = _parse_request(session, pid, mat)
    _parse_result(session, pid, mat, pctx, status="unprocessable")
    # 行1 进行中：不可放弃
    ctx2 = _intake_request(session, pid, note="进行中件")
    flows = {f.intake_context_ref: f for f in _svc(session).list_requirement_flows(pid)}
    assert not flows[ctx].dismissable and not flows[ctx].resumable
    assert not flows[ctx2].dismissable and flows[ctx2].resumable


def test_dismiss_removes_flow_but_keeps_record(session):
    pid = _project(session)
    ctx = _intake_request(session, pid, note="待放弃件")
    _record(session, pid, ctx, "excluded")
    svc = _svc(session)
    result = svc.dismiss_intake_flow(pid, ctx, "U1")
    assert result.context_ref == ctx and result.dismissed_at
    # 投影即时消失
    assert svc.list_requirement_flows(pid) == []
    # 行仍在库，dismissed_at 非空（过程记录只增不删）
    row = session.get(IntakeRequest, uuid.UUID(ctx))
    assert row is not None and row.dismissed_at is not None


def test_dismiss_idempotent_replays_timestamp(session):
    pid = _project(session)
    ctx = _intake_request(session, pid)
    _record(session, pid, ctx, "returned_for_supplement")
    svc = _svc(session)
    first = svc.dismiss_intake_flow(pid, ctx, "U1")
    second = svc.dismiss_intake_flow(pid, ctx, "U1")
    assert first.dismissed_at == second.dismissed_at


def test_dismiss_rejected_for_non_terminal(session):
    from app.domain.errors import RejectedTransition

    pid = _project(session)
    svc = _svc(session)
    # 无结论（判断在途）
    ctx_pending = _intake_request(session, pid)
    with pytest.raises(RejectedTransition):
        svc.dismiss_intake_flow(pid, ctx_pending, "U1")
    # 已接入
    ctx_ok = _intake_request(session, pid)
    mat = _material(session, pid)
    _record(session, pid, ctx_ok, "accepted", material=mat)
    with pytest.raises(RejectedTransition):
        svc.dismiss_intake_flow(pid, ctx_ok, "U1")


def test_dismiss_not_found_wrong_project_or_ctx(session):
    pid_a = _project(session, name="A")
    pid_b = _project(session, name="B")
    ctx = _intake_request(session, pid_a)
    _record(session, pid_a, ctx, "excluded")
    svc = _svc(session)
    with pytest.raises(NotFound):
        svc.dismiss_intake_flow(pid_b, ctx, "U1")  # 跨项目不可见
    with pytest.raises(NotFound):
        svc.dismiss_intake_flow(pid_a, str(uuid.uuid4()), "U1")


def test_intake_prefill_returns_submitted_content(session):
    pid = _project(session)
    ctx = _intake_request(session, pid, note="来源类型:会议纪要；来源说明:补充件")
    _record(session, pid, ctx, "returned_for_supplement")
    prefill = _svc(session).read_intake_prefill(pid, ctx)
    assert prefill.context_ref == ctx
    assert prefill.raw_text == "系统应支持导出 docx。"
    assert prefill.source_note == "来源类型:会议纪要；来源说明:补充件"


def test_intake_prefill_not_found_cross_project(session):
    pid_a = _project(session, name="A")
    pid_b = _project(session, name="B")
    ctx = _intake_request(session, pid_a)
    with pytest.raises(NotFound):
        _svc(session).read_intake_prefill(pid_b, ctx)


# ============================================================================
# 真实写路径一致性（stub 判定驱动材料接收服务）
# ============================================================================

def test_real_intake_chain_projects_flow(session):
    pid = _project(session)
    svc = build_sql_service(session, auto_complete=True)  # stub 判定=可接入
    svc.submit_text_intake(TextIntakeCommand(
        project_ref=pid, raw_text="系统应支持导出 docx。", source_note="真实链路",
        operator_ref="U1", idempotency_key="K-real-1",
    ))
    session.commit()
    f = _single_flow(session, pid)
    assert f.title == "真实链路"
    assert _stage(f, "intake") == "done"
    assert f.current_stage == "analysis" and f.material_ref


# ============================================================================
# HTTP 形状（依赖覆盖为本测试 SQLite session）
# ============================================================================

def test_http_overview_and_flows_shapes():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.deps import get_overview_service
    from app.main import app

    # 共享内存库（StaticPool）：TestClient 在 threadpool 线程跑 sync 路由，需跨线程共用同一 DB。
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()

    pid = _project(session)
    ctx = _intake_request(session, pid, note="HTTP 形状")
    mat = _material(session, pid)
    _record(session, pid, ctx, "accepted", material=mat)
    session.commit()

    app.dependency_overrides[get_overview_service] = lambda: _svc(session)
    try:
        client = TestClient(app)
        r = client.get(f"/api/projects/{pid}/overview")
        assert r.status_code == 200
        body = r.json()
        assert body["project_ref"] == pid
        assert {m["key"] for m in body["asset_metrics"]} == {
            "materials", "elements", "items", "charts", "documents", "issues"}
        assert len(body["flows"]) == 1
        flow = body["flows"][0]
        assert flow["current_stage"] == "analysis" and flow["resumable"] is True
        assert [s["stage"] for s in flow["stages"]] == list(STAGES)

        r2 = client.get(f"/api/projects/{pid}/requirement-flows")
        assert r2.status_code == 200
        assert r2.json()[0]["flow_id"] == flow["flow_id"]

        r3 = client.get(f"/api/projects/{uuid.uuid4()}/overview")
        assert r3.status_code == 404
        assert r3.json()["success"] is False
    finally:
        app.dependency_overrides.pop(get_overview_service, None)
        session.close()
        engine.dispose()


def test_http_dismiss_and_prefill_shapes():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.deps import get_overview_service
    from app.main import app

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()

    pid = _project(session)
    ctx = _intake_request(session, pid, note="HTTP 终结态")
    _record(session, pid, ctx, "returned_for_supplement")
    ctx_active = _intake_request(session, pid, note="HTTP 进行中")
    session.commit()

    app.dependency_overrides[get_overview_service] = lambda: _svc(session)
    try:
        client = TestClient(app)
        flows = client.get(f"/api/projects/{pid}/requirement-flows").json()
        by_ctx = {f["intake_context_ref"]: f for f in flows}
        assert by_ctx[ctx]["dismissable"] is True
        assert by_ctx[ctx_active]["dismissable"] is False

        r_prefill = client.get(f"/api/projects/{pid}/requirement-flows/{ctx}/intake-prefill")
        assert r_prefill.status_code == 200
        body = r_prefill.json()
        assert body["raw_text"] == "系统应支持导出 docx。"
        assert body["source_note"] == "HTTP 终结态"

        r_reject = client.post(
            f"/api/projects/{pid}/requirement-flows/{ctx_active}/dismiss",
            json={"operator_ref": "U1"},
        )
        assert r_reject.status_code == 409

        r_ok = client.post(
            f"/api/projects/{pid}/requirement-flows/{ctx}/dismiss",
            json={"operator_ref": "U1"},
        )
        assert r_ok.status_code == 200 and r_ok.json()["dismissed_at"]

        remaining = client.get(f"/api/projects/{pid}/requirement-flows").json()
        assert [f["intake_context_ref"] for f in remaining] == [ctx_active]
    finally:
        app.dependency_overrides.pop(get_overview_service, None)
        session.close()
        engine.dispose()
