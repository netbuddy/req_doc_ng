"""分析转化服务：三分支 + VAL-002/003/005 + 门禁 + 幂等 + 全集登记 + 持久化。

设计事实源：slices/SCN-001-P02-需求要素识别/约束与验收.md、state-machines/材料解析.md。
"""
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import StubSourceElementRecognizer
from app.api.schemas import (
    ElementDecisionCommand,
    ElementRecognitionCommand,
    ElementTriageCommand,
    RecognitionResultCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    IntakeRecord,
    Material,
    MaterialParseResult,
    ModelResult,
    Project,
    RequirementElement,
)
from app.domain.enums import MaterialParseStatus, RecognitionOutcome, RecognitionRequestStatus
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.interfaces.repositories import RecognitionRead, RecognizedElementRow
from app.repositories.in_memory import build_analysis_wiring
from app.repositories.sqlalchemy import build_sql_analysis_service


# ============================================================================
# in-memory 单元测试
# ============================================================================

def _wiring(auto_complete=False):
    w = build_analysis_wiring(auto_complete=auto_complete)
    w.source_assets.seed_material("M-1", accepted=True)  # 已接入材料
    return w


def _submit(w, material="M-1", key="K1"):
    return w.service.submit_element_recognition(
        ElementRecognitionCommand(
            project_ref="P-1", material_ref=material, operator_ref="U1", idempotency_key=key
        )
    )


def _elements(*specs):
    return tuple(
        RecognizedElementRow(
            element_type=t, content=c, source_anchor=a, confidence=conf, model_verdict=v
        )
        for (t, c, a, conf, v) in specs
    )


def _accept(w, ctx, reco, mrr="MR-1"):
    w.model_results.seed_recognition(mrr, reco)
    return w.service.accept_recognition_result(
        RecognitionResultCommand(
            model_result_ref=mrr, parse_context_ref=ctx, operator_ref="U1", idempotency_key="A1"
        )
    )


# ---- AEP-021 门禁 / 幂等 ----

def test_precheck_pass_submits_without_writing_facts():
    w = _wiring()
    r = _submit(w)
    assert r.status is RecognitionRequestStatus.SUBMITTED_FOR_RECOGNITION
    assert r.parse_context_ref and r.agent_run_ref
    assert w.source_assets.save_parse_calls == 0  # VAL-002：送检前不写 LDM-004/005


def test_precheck_rejects_unaccepted_material():
    w = _wiring()
    r = _submit(w, material="M-UNKNOWN")  # 未接入
    assert r.status is RecognitionRequestStatus.REJECTED_PRECHECK
    assert r.parse_context_ref is None and r.next_action
    assert not w.model_orchestration.dispatched  # 未送检


def test_idempotent_replay_returns_same_context():
    w = _wiring()
    a = _submit(w, key="SAME")
    b = _submit(w, key="SAME")
    assert a.parse_context_ref == b.parse_context_ref
    assert len(w.model_orchestration.dispatched) == 1


# ---- AEP-022 三分支 ----

def test_registered_writes_parse_result_and_all_elements():
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    reco = RecognitionRead(
        result_code="recognized",
        elements=_elements(
            ("functional_requirement", "系统应支持导出", "L1", 0.9, "processable"),
            ("quality_attribute", "低置信度疑似项", "L2", 0.2, "suspected_noise"),  # 全集登记（N06）
        ),
        basis="识别完成",
    )
    r = _accept(w, ctx, reco)
    assert r.outcome is RecognitionOutcome.REGISTERED
    assert r.parse_result_ref and r.element_count == 2  # 含疑似噪声项（不预丢）
    assert w.source_assets.save_parse_calls == 1
    assert w.source_assets.parse_status_of(ctx) == MaterialParseStatus.PARSED.value
    rows = w.source_assets.elements_of(r.parse_result_ref)
    assert len(rows) == 2
    # 初始 process_status 一律「待确认」；模型裁定入证据字段
    assert all(row.process_status == "pending_confirmation" for row in rows)
    assert rows[1].model_verdict == "suspected_noise"


def test_no_processable_elements_writes_ldm004_only():
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    r = _accept(w, ctx, RecognitionRead(result_code="no_elements", elements=(), basis="无可处理"))
    assert r.outcome is RecognitionOutcome.NO_PROCESSABLE_ELEMENTS
    assert r.element_count == 0 and r.next_action
    assert w.source_assets.parse_status_of(ctx) == MaterialParseStatus.UNPROCESSABLE.value
    assert w.source_assets.elements_of(r.parse_result_ref) == []  # 不写 LDM-005


def test_recognition_failed_stops_without_writing_facts():
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    r = _accept(w, ctx, RecognitionRead(result_code="failed", elements=(), basis="模型不可用"))
    assert r.outcome is RecognitionOutcome.RECOGNITION_FAILED
    assert r.parse_result_ref is None and r.next_action
    assert w.source_assets.parse_status_of(ctx) is None  # VAL-005：状态不迁移、不写事实
    assert w.process_records.read_parse_stop_next_action(ctx)  # 保留失败停靠


@pytest.mark.parametrize(
    "reco,expect_ldm004,expect_elements",
    [
        (RecognitionRead("recognized", _elements(("goal", "g", None, 0.8, "processable")), "b"), True, 1),
        (RecognitionRead("no_elements", (), "b"), True, 0),
        (RecognitionRead("failed", (), "b"), False, 0),
    ],
)
def test_val_write_matrix(reco, expect_ldm004, expect_elements):
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    r = _accept(w, ctx, reco)
    has_ldm004 = w.source_assets.parse_status_of(ctx) is not None
    assert has_ldm004 is expect_ldm004
    n = len(w.source_assets.elements_of(r.parse_result_ref)) if r.parse_result_ref else 0
    assert n == expect_elements


# ---- 默认拒绝（状态机未列出组合）----

def test_accept_on_missing_context_is_rejected():
    w = _wiring()
    with pytest.raises(RejectedTransition):
        _accept(w, "PCTX-NONE", RecognitionRead("recognized", _elements(("goal", "g", None, 0.8, "processable")), "b"))


def test_accept_twice_is_rejected():
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    reco = RecognitionRead("recognized", _elements(("goal", "g", None, 0.8, "processable")), "b")
    _accept(w, ctx, reco)
    with pytest.raises(RejectedTransition):  # 已有解析结论
        _accept(w, ctx, reco, mrr="MR-2")


# ---- N07 工作区读视图：available_actions 是后端事实 ----

def test_workspace_parsed_gates_item_formation_on_confirmed():
    """E5 门禁：识别后（全待确认）不开条目形成；确认后才开放。"""
    from app.api.schemas import ElementDecisionCommand

    w = _wiring()
    ctx = _submit(w).parse_context_ref
    _accept(w, ctx, RecognitionRead(
        "recognized", _elements(("functional_requirement", "g", "L1", 0.8, "processable")), "b"
    ))
    read = w.service.read_element_workspace(ctx)
    assert read.parse_status is MaterialParseStatus.PARSED
    assert len(read.elements) == 1
    assert not any(a.key == "start_item_formation" and a.enabled for a in read.available_actions)

    read2 = w.service.decide_elements(ElementDecisionCommand(
        parse_context_ref=ctx, workspace_version=read.workspace_version,
        element_refs=[read.elements[0].id], decision="confirm",
        operator_ref="U1", idempotency_key="D1",
    ))
    assert read2.elements[0].process_status.value == "confirmed"
    assert any(a.key == "start_item_formation" and a.enabled for a in read2.available_actions)


def test_workspace_unprocessable_disables_item_formation():
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    _accept(w, ctx, RecognitionRead("no_elements", (), "无可处理"))
    read = w.service.read_element_workspace(ctx)
    assert read.parse_status is MaterialParseStatus.UNPROCESSABLE
    assert not any(a.key == "start_item_formation" and a.enabled for a in read.available_actions)


def test_workspace_failed_returns_retry():
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    _accept(w, ctx, RecognitionRead("failed", (), "失败"))
    read = w.service.read_element_workspace(ctx)
    assert read.next_action and any(a.key == "retry" for a in read.available_actions)


def test_workspace_unknown_context_not_found():
    w = _wiring()
    with pytest.raises(NotFound):
        w.service.read_element_workspace("PCTX-NONE")


def test_auto_complete_end_to_end_stub():
    """A1 同步全链路：submit → recognize(stub) → 记 LDM-015 → accept → 写 LDM-004/005。"""
    w = build_analysis_wiring(auto_complete=True)
    w.source_assets.seed_material(
        "M-1", raw_text="系统应支持导出 docx。识别结果需可追溯到来源。", accepted=True
    )
    ctx = _submit(w).parse_context_ref
    read = w.service.read_element_workspace(ctx)
    assert read.parse_status is MaterialParseStatus.PARSED
    assert len(read.elements) == 2  # stub 按句派生两条
    # 结构化锚点：offset 可解析回原文
    import json as _json
    anchor = _json.loads(read.elements[0].source_anchor)
    rng = anchor["ranges"][0]
    assert anchor["material_ref"] == "M-1"
    assert read.material_canvas.raw_text[rng["start"]:rng["end"]] == rng["exact"]


# ============================================================================
# 持久化集成测试（SQLite create_all）
# ============================================================================

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


def _seed_accepted_material(session) -> tuple[str, str]:
    p = Project(name="demo")
    session.add(p)
    session.flush()
    mat = Material(
        project_id=p.id,
        raw_text="系统应支持导出 docx。导出结果需保留来源追溯。",
        source_note="访谈",
    )
    session.add(mat)
    session.flush()
    session.add(
        IntakeRecord(
            project_id=p.id, context_ref=uuid.uuid4(),
            intake_conclusion="accepted", material_ref=mat.id,
        )
    )
    session.flush()
    return str(p.id), str(mat.id)


def test_recognition_persists_ldm004_and_ldm005(session):
    pid, mid = _seed_accepted_material(session)
    svc = build_sql_analysis_service(session, auto_complete=True)  # 默认 stub 识别=2 条
    r = svc.submit_element_recognition(
        ElementRecognitionCommand(project_ref=pid, material_ref=mid, operator_ref="U1", idempotency_key="K1")
    )
    session.commit()

    read = svc.read_element_workspace(r.parse_context_ref)
    assert read.parse_status is MaterialParseStatus.PARSED
    assert len(read.elements) == 2

    parse = session.scalar(select(MaterialParseResult))
    assert parse is not None and parse.parse_status == "parsed" and parse.material_ref == uuid.UUID(mid)
    assert len(session.scalars(select(RequirementElement)).all()) == 2  # 全集登记
    mr = session.scalar(select(ModelResult))
    assert mr.stage == "element_recognition" and mr.judgement == "recognized"


def test_recognition_gate_rejects_unaccepted_material(session):
    pid, _ = _seed_accepted_material(session)
    svc = build_sql_analysis_service(session, auto_complete=True)
    r = svc.submit_element_recognition(
        ElementRecognitionCommand(
            project_ref=pid, material_ref=str(uuid.uuid4()),  # 无已接入 LDM-003
            operator_ref="U1", idempotency_key="K2",
        )
    )
    assert r.status is RecognitionRequestStatus.REJECTED_PRECHECK
    assert session.scalar(select(MaterialParseResult)) is None
    assert session.scalar(select(RequirementElement)) is None


def test_recognition_no_elements_persists_ldm004_only(session):
    pid, mid = _seed_accepted_material(session)
    svc = build_sql_analysis_service(
        session, auto_complete=True, recognizer=StubSourceElementRecognizer(elements=())
    )
    r = svc.submit_element_recognition(
        ElementRecognitionCommand(project_ref=pid, material_ref=mid, operator_ref="U1", idempotency_key="K3")
    )
    session.commit()

    read = svc.read_element_workspace(r.parse_context_ref)
    assert read.parse_status is MaterialParseStatus.UNPROCESSABLE
    parse = session.scalar(select(MaterialParseResult))
    assert parse.parse_status == "unprocessable"
    assert session.scalar(select(RequirementElement)) is None  # VAL：无可处理不写 LDM-005


def test_recognition_failed_persists_no_ldm004(session):
    pid, mid = _seed_accepted_material(session)
    svc = build_sql_analysis_service(
        session, auto_complete=True, recognizer=StubSourceElementRecognizer(failed=True)
    )
    r = svc.submit_element_recognition(
        ElementRecognitionCommand(project_ref=pid, material_ref=mid, operator_ref="U1", idempotency_key="K4")
    )
    session.commit()

    read = svc.read_element_workspace(r.parse_context_ref)
    assert read.next_action  # 失败停靠
    assert session.scalar(select(MaterialParseResult)) is None  # VAL-005：不写 LDM-004
    mr = session.scalar(select(ModelResult))
    assert mr.stage == "element_recognition" and mr.judgement == "failed"  # LDM-015 仍登记


# ============================================================================
# HTTP 路由层（TestClient + in-memory 覆盖，不触真 Postgres/LLM）
# ============================================================================

from fastapi.testclient import TestClient  # noqa: E402
from app.deps import get_analysis_service  # noqa: E402
from app.main import app  # noqa: E402

_http_wiring = build_analysis_wiring(auto_complete=True)  # 代 RQ+LLM：inline 识别=stub 按句派生
_http_wiring.source_assets.seed_material(
    "M-http", raw_text="系统应支持导出 docx。导出结果需保留来源追溯。", accepted=True
)
app.dependency_overrides[get_analysis_service] = lambda: _http_wiring.service
_client = TestClient(app)


def test_http_recognition_end_to_end():
    payload = {
        "project_ref": "P-1", "material_ref": "M-http",
        "operator_ref": "U1", "idempotency_key": "K-http-1",
    }
    r = _client.post("/api/projects/P-1/elements/recognition", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "submitted_for_recognition"  # 稳定码
    ctx = data["parse_context_ref"]

    r2 = _client.get(f"/api/projects/P-1/elements/{ctx}")
    assert r2.status_code == 200
    res = r2.json()
    assert res["parse_status"] == "parsed"
    assert len(res["elements"]) == 2
    # 初始全部「待确认」，条目形成门禁关闭
    assert all(e["process_status"] == "pending_confirmation" for e in res["elements"])
    assert not any(a["key"] == "start_item_formation" and a["enabled"] for a in res["available_actions"])

    # 直接确认一条需求表达类要素 → 门禁开放（E5）
    expr = next(e for e in res["elements"] if e["element_type"] == "functional_requirement")
    r3 = _client.post(f"/api/projects/P-1/elements/{ctx}/decide", json={
        "parse_context_ref": ctx, "workspace_version": res["workspace_version"],
        "element_refs": [expr["id"]], "decision": "confirm",
        "operator_ref": "U1", "idempotency_key": "K-http-d1",
    })
    assert r3.status_code == 200
    res3 = r3.json()
    confirmed = next(e for e in res3["elements"] if e["id"] == expr["id"])
    assert confirmed["process_status"] == "confirmed"
    assert any(a["key"] == "start_item_formation" and a["enabled"] for a in res3["available_actions"])


def test_http_precheck_is_business_outcome_200():
    payload = {
        "project_ref": "P-1", "material_ref": "M-not-accepted",
        "operator_ref": "U1", "idempotency_key": "K-http-2",
    }
    r = _client.post("/api/projects/P-1/elements/recognition", json=payload)
    assert r.status_code == 200  # 业务结局非 HTTP 错误
    assert r.json()["status"] == "rejected_precheck"
    assert r.json()["next_action"]


def test_http_project_mismatch_is_400():
    payload = {
        "project_ref": "P-OTHER", "material_ref": "M-http",
        "operator_ref": "U1", "idempotency_key": "K-http-3",
    }
    r = _client.post("/api/projects/P-1/elements/recognition", json=payload)
    assert r.status_code == 400


def test_http_unknown_context_returns_404():
    r = _client.get("/api/projects/P-1/elements/PCTX-NONEXISTENT")
    assert r.status_code == 404
    assert r.json()["success"] is False


def test_http_material_canvas_before_recognition():
    """未识别态：区3 正文来自 LDM-002 快照，进入分析阶段即可读，不依赖识别上下文。"""
    r = _client.get("/api/projects/P-1/materials/M-http/canvas")
    assert r.status_code == 200
    body = r.json()
    assert body["material_ref"] == "M-http"
    assert body["raw_text"]  # 正文非空
    assert body["blocks"]  # 段落块已切分


def test_http_material_canvas_unaccepted_404():
    r = _client.get("/api/projects/P-1/materials/M-not-accepted/canvas")
    assert r.status_code == 404


# ---- 进页只读回放：材料 → 最近一次识别上下文 ----

def test_http_material_parse_context_before_and_after_recognition():
    """已识别过的材料要能被认出来，否则抽取页把它当未识别、区5 全禁用。"""
    _http_wiring.source_assets.seed_material(
        "M-replay", raw_text="系统应支持批量导入。导入失败需给出原因。", accepted=True
    )
    before = _client.get("/api/projects/P-1/materials/M-replay/parse-context")
    assert before.status_code == 200
    assert before.json()["parse_context_ref"] is None  # 从未识别过

    r = _client.post("/api/projects/P-1/elements/recognition", json={
        "project_ref": "P-1", "material_ref": "M-replay",
        "operator_ref": "U1", "idempotency_key": "K-replay-1",
    })
    ctx = r.json()["parse_context_ref"]

    after = _client.get("/api/projects/P-1/materials/M-replay/parse-context")
    assert after.json()["parse_context_ref"] == ctx
    # 拿到的上下文可直接读回工作区（只读回放，不发起识别）
    assert _client.get(f"/api/projects/P-1/elements/{ctx}").json()["parse_status"] == "parsed"


def test_http_material_parse_context_returns_latest_run():
    """重新识别后取最近一次，而不是第一次。"""
    _http_wiring.source_assets.seed_material(
        "M-replay2", raw_text="系统应记录操作日志。", accepted=True
    )
    first = _client.post("/api/projects/P-1/elements/recognition", json={
        "project_ref": "P-1", "material_ref": "M-replay2",
        "operator_ref": "U1", "idempotency_key": "K-replay-2a",
    }).json()["parse_context_ref"]
    second = _client.post("/api/projects/P-1/elements/recognition", json={
        "project_ref": "P-1", "material_ref": "M-replay2",
        "operator_ref": "U1", "idempotency_key": "K-replay-2b",
    }).json()["parse_context_ref"]
    assert first != second
    assert _client.get(
        "/api/projects/P-1/materials/M-replay2/parse-context"
    ).json()["parse_context_ref"] == second


def test_http_material_parse_context_unaccepted_404():
    r = _client.get("/api/projects/P-1/materials/M-not-accepted/parse-context")
    assert r.status_code == 404


# ============================================================================
# T20260724-suspected-noise-triage：裁定理由 + 建议剔除候选的人工处置
# ============================================================================

def _noise_element(reason=None, noise_type="term", noise_first=False):
    normal = RecognizedElementRow(
        element_type="functional_requirement", content="系统应支持导出",
        source_anchor="L1", confidence=0.9, model_verdict="processable",
    )
    noise = RecognizedElementRow(
        element_type=noise_type, content="感谢各位抽空参加",
        source_anchor="L2", confidence=0.2, model_verdict="suspected_noise",
        verdict_reason=reason,
    )
    return RecognitionRead(
        result_code="recognized",
        elements=(noise, normal) if noise_first else (normal, noise),
        basis="识别完成",
    )


def _registered_with_noise(reason=None, noise_type="term", noise_first=False):
    """登记一份含一条建议剔除项的工作区，返回 (wiring, ctx, 正常项 id, 候选项 id)。"""
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    _accept(w, ctx, _noise_element(reason, noise_type=noise_type, noise_first=noise_first))
    read = w.service.read_element_workspace(ctx)
    normal = next(e for e in read.elements if e.model_verdict.value == "processable")
    noise = next(e for e in read.elements if e.model_verdict.value == "suspected_noise")
    return w, ctx, normal.id, noise.id


def _triage(w, ctx, element_ref, action, key="T-x"):
    read = w.service.read_element_workspace(ctx)
    return w.service.triage_elements(ElementTriageCommand(
        parse_context_ref=ctx, workspace_version=read.workspace_version,
        element_refs=[element_ref], action=action,
        operator_ref="U1", idempotency_key=key,
    ))


def _decide(w, ctx, element_ref, decision, key="D-x"):
    read = w.service.read_element_workspace(ctx)
    return w.service.decide_elements(ElementDecisionCommand(
        parse_context_ref=ctx, workspace_version=read.workspace_version,
        element_refs=[element_ref], decision=decision,
        operator_ref="U1", idempotency_key=key,
    ))


def test_verdict_reason_persists_and_projects():
    """A2：模型给的逐条理由随识别落库，并投影到读视图。"""
    w, ctx, _normal, noise_id = _registered_with_noise("这句是会议开场的客套话，没有表述任何系统约束")
    read = w.service.read_element_workspace(ctx)
    noise = next(e for e in read.elements if e.id == noise_id)
    assert noise.verdict_reason == "这句是会议开场的客套话，没有表述任何系统约束"
    # 可处理项模型没给理由 → None，不伪造
    normal = next(e for e in read.elements if e.id != noise_id)
    assert normal.verdict_reason is None


def test_verdict_reason_absent_stays_none():
    """A2：模型漏给理由时落 None（读侧回落通用判据，属前端职责）。"""
    w, ctx, _normal, noise_id = _registered_with_noise(None)
    read = w.service.read_element_workspace(ctx)
    assert next(e for e in read.elements if e.id == noise_id).verdict_reason is None


def test_triage_restore_keeps_model_verdict_and_leaves_status():
    """A3：撤回只写人工标记——模型裁定与理由一字不动，确认状态不迁移。"""
    w, ctx, _normal, noise_id = _registered_with_noise("会议客套话")
    read = w.service.read_element_workspace(ctx)
    out = w.service.triage_elements(ElementTriageCommand(
        parse_context_ref=ctx, workspace_version=read.workspace_version,
        element_refs=[noise_id], action="restore",
        operator_ref="U1", idempotency_key="T1",
    ))
    row = next(e for e in out.elements if e.id == noise_id)
    assert row.noise_triage.value == "restored"
    assert row.model_verdict.value == "suspected_noise"   # 模型证据不可篡改
    assert row.verdict_reason == "会议客套话"
    assert row.process_status.value == "pending_confirmation"  # 撤回≠确认
    assert row.version == 1                                # 不升版本


def test_triage_restore_does_not_open_item_formation_gate():
    """A3：撤回不等于确认——门禁仍要求人工确认后才能进条目形成。

    候选项取可条目化类型（functional_requirement）：门禁只看「已确认且属于可条目化类型」的知识项，
    拿 term 一类不可条目化的类型做这条测试，撤回逻辑错成「直接改已确认」它也照样绿（冷审查裁定 Q2）。
    断言分两段——撤回后门禁仍关着，再人工确认同一条后门禁打开，后一段证明前一段不是恒真的。
    """
    w, ctx, _normal, noise_id = _registered_with_noise(noise_type="functional_requirement")
    out = _triage(w, ctx, noise_id, "restore", key="T2")
    assert next(e for e in out.elements if e.id == noise_id).process_status.value == "pending_confirmation"
    gate = next(a for a in out.available_actions if a.key == "start_item_formation")
    assert not gate.enabled

    confirmed = _decide(w, ctx, noise_id, "confirm", key="T2-confirm")
    assert next(e for e in confirmed.elements if e.id == noise_id).process_status.value == "confirmed"
    gate_after = next(a for a in confirmed.available_actions if a.key == "start_item_formation")
    assert gate_after.enabled


def test_triage_is_reversible():
    """A3：误撤回可再移回候选区（标记清空回未处置）。"""
    w, ctx, _normal, noise_id = _registered_with_noise()
    read = w.service.read_element_workspace(ctx)
    out = w.service.triage_elements(ElementTriageCommand(
        parse_context_ref=ctx, workspace_version=read.workspace_version,
        element_refs=[noise_id], action="restore",
        operator_ref="U1", idempotency_key="T3",
    ))
    back = w.service.triage_elements(ElementTriageCommand(
        parse_context_ref=ctx, workspace_version=out.workspace_version,
        element_refs=[noise_id], action="return",
        operator_ref="U1", idempotency_key="T4",
    ))
    assert next(e for e in back.elements if e.id == noise_id).noise_triage is None


def test_triage_records_history_with_operator():
    """A3：撤回留痕（操作者/动作），前后状态相同。"""
    w, ctx, _normal, noise_id = _registered_with_noise()
    read = w.service.read_element_workspace(ctx)
    w.service.triage_elements(ElementTriageCommand(
        parse_context_ref=ctx, workspace_version=read.workspace_version,
        element_refs=[noise_id], action="restore",
        operator_ref="U-tri", idempotency_key="T5",
    ))
    records = w.service.read_element_history(ctx, noise_id).records
    entry = next(r for r in records if r.action == "restore_from_triage")
    assert entry.operator_ref == "U-tri"
    assert entry.from_status == entry.to_status == "pending_confirmation"


def test_triage_rejects_non_candidate_element():
    """A3：候选区只装模型判为建议剔除的项；对其余条目这个动作要明确拒绝。"""
    w, ctx, normal_id, _noise = _registered_with_noise()
    read = w.service.read_element_workspace(ctx)
    with pytest.raises(InvalidInput):
        w.service.triage_elements(ElementTriageCommand(
            parse_context_ref=ctx, workspace_version=read.workspace_version,
            element_refs=[normal_id], action="restore",
            operator_ref="U1", idempotency_key="T6",
        ))


def test_triage_rejects_unknown_action():
    w, ctx, _normal, noise_id = _registered_with_noise()
    read = w.service.read_element_workspace(ctx)
    with pytest.raises(InvalidInput):
        w.service.triage_elements(ElementTriageCommand(
            parse_context_ref=ctx, workspace_version=read.workspace_version,
            element_refs=[noise_id], action="delete",
            operator_ref="U1", idempotency_key="T7",
        ))


# ============================================================================
# 冷审查裁定消费：确认守卫（C1）/ 默认选中（C2）/ 复核可见人工处置（C4）/ SQL 侧往返（Q1）
# ============================================================================

def test_confirm_rejects_triage_candidate():
    """C1 第二道防线：候选区里的条目不能被确认——前端守卫绕过后（勾选集合里混进候选），
    库里会留下一条「已确认」却在正常列表遍寻不着的知识项。"""
    w, ctx, _normal, noise_id = _registered_with_noise()
    with pytest.raises(InvalidInput):
        _decide(w, ctx, noise_id, "confirm", key="G1")
    read = w.service.read_element_workspace(ctx)
    assert next(e for e in read.elements if e.id == noise_id).process_status.value == "pending_confirmation"


def test_reject_is_the_legitimate_exit_of_the_triage_box():
    """C1/C7：撤销不拦——它是候选区的正当出口，页面提示语也是这么写的（确是多余的就撤销）。"""
    w, ctx, _normal, noise_id = _registered_with_noise()
    out = _decide(w, ctx, noise_id, "reject", key="G2")
    row = next(e for e in out.elements if e.id == noise_id)
    assert row.process_status.value == "revoked"
    assert row.model_verdict.value == "suspected_noise"  # 模型证据仍不动


def test_confirm_allowed_after_manual_restore():
    """C1：守卫认的是「人工尚未撤回」，撤回之后这一条与普通知识项一样可以确认。"""
    w, ctx, _normal, noise_id = _registered_with_noise()
    _triage(w, ctx, noise_id, "restore", key="G3")
    out = _decide(w, ctx, noise_id, "confirm", key="G4")
    assert next(e for e in out.elements if e.id == noise_id).process_status.value == "confirmed"


def test_default_selection_skips_triage_candidate():
    """C2：默认选中跳过候选——材料开头是寒暄时，候选项正好排在第一条，而候选分组默认折叠，
    选中它会让区4 显示着某条内容、区1 却没有任何一行处于选中态。"""
    w, ctx, normal_id, _noise = _registered_with_noise(noise_first=True)
    read = w.service.read_element_workspace(ctx)
    assert read.selected_element_ref == normal_id


def test_default_selection_falls_back_when_everything_is_a_candidate():
    """C2 的边界：全是候选时仍要给出一个目标，否则页面没有可选中的知识项。"""
    w = _wiring()
    ctx = _submit(w).parse_context_ref
    _accept(w, ctx, RecognitionRead(
        result_code="recognized",
        elements=_elements(("term", "感谢各位抽空参加", "L1", 0.2, "suspected_noise")),
        basis="识别完成",
    ))
    read = w.service.read_element_workspace(ctx)
    assert read.selected_element_ref == read.elements[0].id


def test_review_sees_manual_restore_and_stops_judging_by_model_verdict():
    """C4：人工撤回要对 AI 复核可见——送检快照带上人工处置标记，复核不再照旧判「不可通过」。

    走的是真实链路（送检快照由 model_orchestration 摊字典 → 桩复核器按快照判定），
    两条同内容的知识项只差人工标记，结论必须不同；否则说明标记根本没送到复核侧。
    """
    from app.adapters.llm import RecognizedElement, StubSourceElementRecognizer
    from app.api.schemas import ElementReviewCommand
    from app.domain.enums import ElementType, ModelVerdict

    recognized = (
        RecognizedElement(
            element_type=ElementType.TERM, content="感谢各位抽空参加",
            source_anchor="感谢各位抽空参加", confidence=0.2,
            verdict=ModelVerdict.SUSPECTED_NOISE, verdict_reason="会议开场客套话",
        ),
        RecognizedElement(
            element_type=ElementType.TERM, content="下期再约时间",
            source_anchor="下期再约时间", confidence=0.2,
            verdict=ModelVerdict.SUSPECTED_NOISE, verdict_reason="下期范围，不承载本次需求",
        ),
    )
    w = build_analysis_wiring(
        auto_complete=True, recognizer=StubSourceElementRecognizer(elements=recognized)
    )
    w.source_assets.seed_material("M-1", raw_text="感谢各位抽空参加。下期再约时间。", accepted=True)
    ctx = _submit(w).parse_context_ref
    read = w.service.read_element_workspace(ctx)
    restored = next(e for e in read.elements if e.content == "感谢各位抽空参加").id
    untouched = next(e for e in read.elements if e.content == "下期再约时间").id

    _triage(w, ctx, restored, "restore", key="R1")
    version = w.service.read_element_workspace(ctx).workspace_version
    w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=version,
        target_element_refs=[restored, untouched], review_intent="复核",
        operator_ref="U1", idempotency_key="R2",
    ))

    after = w.service.read_element_workspace(ctx)
    assert next(e for e in after.elements if e.id == restored).review_conclusion.value != "fail"
    # 对照组：没被人工撤回的那条仍按模型裁定判不可通过（证明上一条断言不是恒真的）
    assert next(e for e in after.elements if e.id == untouched).review_conclusion.value == "fail"


def test_sql_triage_restore_persists_and_keeps_model_evidence(session):
    """Q1：撤回链路的 SQL 侧集成覆盖——识别结果 JSON 往返、读投影、撤回写入三跳都只在生产上存在。

    内存替身把识别结果对象整个存进字典、不经序列化，理由字段在 JSON 往返里被改坏时全部
    后端测试仍会全绿，而生产上每一条裁定理由都会静默变成空。
    """
    from app.adapters.llm import RecognizedElement, StubSourceElementRecognizer
    from app.domain.enums import ElementType, ModelVerdict

    reason = "这句是会议开场的客套话，没有表述任何系统约束"
    pid, mid = _seed_accepted_material(session)
    recognized = (
        RecognizedElement(
            element_type=ElementType.FUNCTIONAL_REQUIREMENT, content="系统应支持导出 docx",
            source_anchor="系统应支持导出 docx", confidence=0.9,
            verdict=ModelVerdict.PROCESSABLE,
        ),
        RecognizedElement(
            element_type=ElementType.TERM, content="导出结果需保留来源追溯",
            source_anchor="导出结果需保留来源追溯", confidence=0.2,
            verdict=ModelVerdict.SUSPECTED_NOISE, verdict_reason=reason,
        ),
    )
    svc = build_sql_analysis_service(
        session, auto_complete=True,
        recognizer=StubSourceElementRecognizer(elements=recognized),
    )
    r = svc.submit_element_recognition(ElementRecognitionCommand(
        project_ref=pid, material_ref=mid, operator_ref="U1", idempotency_key="K-sql-triage",
    ))
    session.commit()

    read = svc.read_element_workspace(r.parse_context_ref)
    noise = next(e for e in read.elements if e.model_verdict.value == "suspected_noise")
    assert noise.verdict_reason == reason  # 识别结果 JSON 写入→读回这一跳里理由存活

    svc.triage_elements(ElementTriageCommand(
        parse_context_ref=r.parse_context_ref, workspace_version=read.workspace_version,
        element_refs=[noise.id], action="restore",
        operator_ref="U-sql", idempotency_key="K-sql-triage-2",
    ))
    session.commit()

    row = next(
        e for e in session.scalars(select(RequirementElement)).all() if str(e.id) == noise.id
    )
    assert row.noise_triage == "restored"
    assert row.model_verdict == "suspected_noise"  # 模型证据一字不动
    assert row.verdict_reason == reason
    assert row.process_status == "pending_confirmation"  # 撤回不迁移确认生命周期
