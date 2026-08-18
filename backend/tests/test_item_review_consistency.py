"""P7 条目评审侧业务知识一致性测试义务（09 §2 P7；设计 10 §2）。

覆盖：诊断上下文 business_sources 段（supporting_basis 组装）；与业务知识一致性单列档
（rule_code BIZ-RULE-CONFLICT、finding_type source_inconsistency，走 source_inconsistency
不新增 verdict_kind）正/负例。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.adapters.llm import StubRequirementItemDiagnoser
from app.api.schemas import (
    ItemReviewDiagnosisCommand, ItemizationBatchCommand, SupportingBasisCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    IntakeRecord, Material, MaterialParseResult, ModelResult, ParseRequest,
    Project, RequirementElement, RequirementItem,
)
from app.domain.enums import ItemizationScopeType
from app.repositories.sqlalchemy import (
    SqlIssueRepository, SqlTraceLinkRepository,
    build_sql_item_formation_service, build_sql_item_review_service,
)
from app.repositories.trace_read import TraceReadRepository
from app.services.trace_analysis import TraceAnalysisService

RAW = ("订单金额超过五万元的付款自动放行。单笔订单金额超过五万元的须经二级审批。"
       "导出耗时不超过五秒。")


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
    p = Project(name="p7-review"); session.add(p); session.flush()
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
                               content=content, source_anchor=_anchor(content), confidence=0.9,
                               process_status=status)
        session.add(e); session.flush()
        return str(e.id)

    el("functional_requirement", "订单金额超过五万元的付款自动放行")
    el("quality_attribute", "导出耗时不超过五秒")
    rule = el("business_rule", "单笔订单金额超过五万元的须经二级审批")
    session.commit()

    formation = build_sql_item_formation_service(session, auto_complete=True)
    res = formation.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=str(p.id), parse_result_ref=str(parse.id), workspace_version="1",
        scope_type=ItemizationScopeType.ALL_ELIGIBLE, target_element_refs=[],
        operator_ref="U1", idempotency_key=f"form-{uuid.uuid4()}"))
    session.commit()
    items = session.scalars(select(RequirementItem).order_by(RequirementItem.req_no)).all()
    auto = next(i for i in items if "自动放行" in i.expression)
    return {
        "project": str(p.id), "parse_context": str(ctx.id), "parse_result": str(parse.id),
        "formation_context": res.formation_context_ref,
        "items": [str(i.id) for i in items], "rule": rule, "auto_item": str(auto.id),
    }


def _reference_rule(session, w):
    tsvc = TraceAnalysisService(TraceReadRepository(session), SqlTraceLinkRepository(session),
                               SqlIssueRepository(session))
    tsvc.create_supporting_basis(w["project"], SupportingBasisCommand(
        element_ref=w["rule"], item_ref=w["auto_item"], operator_ref="U1"))
    session.commit()


def _version(session, w) -> str:
    return str(session.get(ParseRequest, uuid.UUID(w["parse_context"])).workspace_version)


def _run_diag(session, w):
    svc = build_sql_item_review_service(session, auto_complete=True)
    svc.start_item_diagnosis(ItemReviewDiagnosisCommand(
        project_ref=w["project"], item_refs=[w["auto_item"]], diagnosis_mode="standard",
        workspace_version=_version(session, w), operator_ref="U1",
        idempotency_key=f"diag-{uuid.uuid4()}"))
    session.commit()
    return svc


def _findings(session, item_ref) -> list[dict]:
    """诊断发现项（含旁路 rule_code）从 LDM-015 诊断结果 JSON 读回。"""
    out: list[dict] = []
    for mr in session.scalars(select(ModelResult).where(ModelResult.stage == "item_diagnosis")):
        body = json.loads(mr.result_content)
        if body.get("item_ref") == item_ref:
            out.extend(body.get("verdict", {}).get("findings", []))
    return out


# ---- business_sources 段组装 ----

def test_prepare_diagnosis_assembles_business_sources(session):
    w = _seed(session)
    _reference_rule(session, w)
    svc = build_sql_item_review_service(session, auto_complete=False)
    # 起一轮以便 prepare（auto_complete=False 不自动跑模型；手工取上下文）
    svc2 = build_sql_item_review_service(session, auto_complete=True)
    svc2.start_item_diagnosis(ItemReviewDiagnosisCommand(
        project_ref=w["project"], item_refs=[w["auto_item"]], diagnosis_mode="standard",
        workspace_version=_version(session, w), operator_ref="U1", idempotency_key="d1"))
    session.commit()
    # 诊断已跑（stub）；断言业务依据被消费 → 产出一致性发现项
    findings = _findings(session, w["auto_item"])
    assert any(f.get("rule_code") == "BIZ-RULE-CONFLICT" for f in findings)


# ---- 与业务知识一致性单列（正/负例）----

def test_conflict_finding_single_row_when_referenced_rule_contradicts(session):
    w = _seed(session)
    _reference_rule(session, w)  # 引用矛盾业务规则
    _run_diag(session, w)
    findings = _findings(session, w["auto_item"])
    conflict = [f for f in findings if f.get("rule_code") == "BIZ-RULE-CONFLICT"]
    assert len(conflict) == 1  # 单列一档
    assert conflict[0]["finding_type"] == "source_inconsistency"  # 走既有 source_inconsistency


def test_no_conflict_when_no_business_reference(session):
    w = _seed(session)
    # 不建引用边 → business_sources 空 → 不出一致性档（即便表达含「自动放行」）
    _run_diag(session, w)
    findings = _findings(session, w["auto_item"])
    assert not any(f.get("rule_code") == "BIZ-RULE-CONFLICT" for f in findings)


# ---- stub 诊断器直接单测（口径固定）----

def test_stub_diagnoser_conflict_and_compatible():
    stub = StubRequirementItemDiagnoser()
    biz = [{"id": "e1", "element_type": "business_rule", "content": "超五万须二级审批"}]
    conflict = stub.diagnose("p", "standard", {"expression": "超五万元付款自动放行"},
                             [], "", [], [], business_sources=biz)
    assert conflict.verdict_kind == "revise"
    f = conflict.findings[0]
    assert f.finding_type == "source_inconsistency" and f.rule_code == "BIZ-RULE-CONFLICT"
    # 相容条目（无「自动放行」）→ 不出该档
    ok = stub.diagnose("p", "standard", {"expression": "超五万元付款须二级审批后放行"},
                       [], "", [], [], business_sources=biz)
    assert not any(x.rule_code == "BIZ-RULE-CONFLICT" for x in ok.findings)
