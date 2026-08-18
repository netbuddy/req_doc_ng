"""P7 条目形成侧业务知识消费测试义务（09 §2 P7；设计 10 §1）。

覆盖：intent_context 只读投影（goal/scenario 不入批次/不可条目化）；
/引用依据 建预建立 supporting_basis 边、条目确认后转有效、business_rule 只走引用不匹配。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.api.schemas import (
    FormationDialogueCommand,
    ItemConfirmationCommand,
    ItemizationBatchCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    IntakeRecord, Material, MaterialParseResult, ParseRequest, Project,
    RequirementElement, RequirementItem, TraceLink,
)
from app.domain.enums import ItemizationScopeType
from app.repositories.sqlalchemy import (
    build_sql_item_formation_service,
    build_sql_item_review_service,
)

RAW = ("系统应支持履约单的状态查询。履约单是指从下单到出库的一次完整订单处理流程。"
       "订单管理员负责审核大额订单。单笔订单金额超过一万元的须经部门经理审批。"
       "目标是提升订单处理效率。用户在下单后查看履约进度的场景。")


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _anchor(exact: str) -> str:
    i = RAW.find(exact)
    return json.dumps({"ranges": [{"start": i, "end": i + len(exact), "exact": exact}]})


def _seed(session):
    p = Project(name="p7-formation")
    session.add(p); session.flush()
    mat = Material(project_id=p.id, raw_text=RAW, source_note="访谈纪要")
    session.add(mat); session.flush()
    session.add(IntakeRecord(project_id=p.id, context_ref=uuid.uuid4(),
                             intake_conclusion="accepted", material_ref=mat.id))
    ctx = ParseRequest(project_id=p.id, material_ref=mat.id, operator_ref="U1",
                       idempotency_key=f"seed-{uuid.uuid4()}", workspace_version=1)
    session.add(ctx); session.flush()
    parse = MaterialParseResult(project_id=p.id, material_ref=mat.id, context_ref=ctx.id,
                                parse_status="parsed")
    session.add(parse); session.flush()

    def el(etype, content, status="confirmed"):
        e = RequirementElement(project_id=p.id, parse_result_ref=parse.id, element_type=etype,
                               content=content, source_anchor=_anchor(content) if content in RAW else None,
                               confidence=0.9, process_status=status)
        session.add(e); session.flush()
        return str(e.id)

    w = {
        "project": str(p.id), "parse_result": str(parse.id), "parse_context": str(ctx.id),
        "e_func": el("functional_requirement", "系统应支持履约单的状态查询"),
        "e_term": el("term", "履约单是指从下单到出库的一次完整订单处理流程"),
        "e_role": el("role", "订单管理员负责审核大额订单"),
        "e_rule": el("business_rule", "单笔订单金额超过一万元的须经部门经理审批"),
        "e_goal": el("goal", "目标是提升订单处理效率"),
        "e_scenario": el("scenario", "用户在下单后查看履约进度的场景"),
    }
    session.commit()
    return w


def _run_batch(session, w):
    svc = build_sql_item_formation_service(session, auto_complete=True)
    res = svc.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"], workspace_version="1",
        scope_type=ItemizationScopeType.ALL_ELIGIBLE, target_element_refs=[],
        operator_ref="U1", idempotency_key=f"B-{uuid.uuid4()}"))
    session.commit()
    return svc, res.formation_context_ref


def test_intent_context_lists_confirmed_goal_scenario(session):
    w = _seed(session)
    svc, fctx = _run_batch(session, w)
    ws = svc.read_item_formation_workspace(fctx)
    intent_ids = {e.id for e in ws.intent_context}
    assert intent_ids == {w["e_goal"], w["e_scenario"]}  # 只 goal/scenario
    # 不进 blocked（支撑列），也不进 eligible（可条目化）
    assert w["e_goal"] not in {b.id for b in ws.blocked_elements}
    assert w["e_goal"] not in {e.id for e in ws.eligible_elements}
    # 业务知识（term/role/business_rule）仍在 blocked 支撑列，不在意图组
    assert w["e_term"] in {b.id for b in ws.blocked_elements}
    assert w["e_term"] not in intent_ids


def _pending_item_ref(session, w) -> str:
    item = session.scalar(select(RequirementItem).where(
        RequirementItem.project_id == uuid.UUID(w["project"]),
        RequirementItem.status == "pending_confirmation"))
    return str(item.id)


def _reference(svc, w, fctx, item_ref, message):
    version = svc.read_item_formation_workspace(fctx).workspace_version
    return svc.formation_dialogue(FormationDialogueCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        formation_context_ref=fctx, workspace_version=version, message=message,
        item_ref=item_ref, operator_ref="U1", idempotency_key=f"ref-{uuid.uuid4()}"))


def _link(session, w, upstream_ref, item_ref):
    return session.scalar(select(TraceLink).where(
        TraceLink.project_id == uuid.UUID(w["project"]),
        TraceLink.relation_type == "supporting_basis",
        TraceLink.upstream_ref == uuid.UUID(upstream_ref),
        TraceLink.downstream_ref == uuid.UUID(item_ref)))


def test_reference_builds_pre_established_edge_then_effective_on_confirm(session):
    w = _seed(session)
    svc, fctx = _run_batch(session, w)
    item_ref = _pending_item_ref(session, w)
    # /引用依据 履约单 → 名称匹配候选解析 → 预建立边（条目待确认）
    res = _reference(svc, w, fctx, item_ref, "/引用依据 履约单")
    assert res.outcome == "executed", res.message
    link = _link(session, w, w["e_term"], item_ref)
    assert link is not None and link.status == "pre_established"
    # 确认条目（最小门禁）→ 边转有效
    version = svc.read_item_formation_workspace(fctx).workspace_version
    review = build_sql_item_review_service(session)
    review.confirm_item(ItemConfirmationCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=version,
        override=True, reason="演示：直接覆盖确认以验证支撑边转有效",
        operator_ref="U1", idempotency_key="c1"))
    session.commit()
    session.refresh(link)
    assert link.status == "effective"


def test_business_rule_only_via_reference_not_recommended(session):
    w = _seed(session)
    svc, fctx = _run_batch(session, w)
    item_ref = _pending_item_ref(session, w)
    # 推荐候选（名称匹配）含 term「履约单」（名在表达中），不含 business_rule（名不稳定不匹配）
    cands = svc._business_knowledge_candidates(w["project"], "系统应支持履约单的状态查询")
    cand_ids = {c["id"] for c in cands}
    assert w["e_term"] in cand_ids
    assert w["e_rule"] not in cand_ids
    # 但 business_rule 可经显式引用（dispatch 写通道不拒业务规则）
    from app.services.item_formation import ItemFormationService
    res = svc._dispatch_formation_operation(
        FormationDialogueCommand(
            project_ref=w["project"], parse_result_ref=w["parse_result"],
            formation_context_ref=fctx, workspace_version="1", message="x",
            item_ref=item_ref, operator_ref="U1", idempotency_key=f"r-{uuid.uuid4()}"),
        "引用依据", "reference.supporting_basis", {"element_refs": [w["e_rule"]]},
        lambda *a: None)
    assert res.outcome == "executed"
    assert _link(session, w, w["e_rule"], item_ref) is not None
