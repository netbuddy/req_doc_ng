"""P4 追溯支撑依据边测试（06 A）：SUPPORTING_BASIS 正式边投影 + 名称匹配派生边 + 缺口。

派生边为读时投影（不落库、可整层重算）；正式边优先去重；business_rule 不做名称匹配。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    Material, MaterialParseResult, Project, RequirementElement, RequirementItem, TraceLink,
)
from app.repositories.sqlalchemy import SqlIssueRepository, SqlTraceLinkRepository
from app.repositories.trace_read import TraceReadRepository
from app.services.trace_analysis import TraceAnalysisService


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


def _svc(session):
    return TraceAnalysisService(
        TraceReadRepository(session),
        trace_links=SqlTraceLinkRepository(session),
        issues=SqlIssueRepository(session),
    )


def _setup(session, *, term_content="履约单是指从下单到出库的作业",
           item_expr="履约单需在系统中登记并流转", el_type="term",
           el_status="confirmed", item_status="confirmed"):
    p = Project(name="支撑依据测试"); session.add(p); session.flush()
    m = Material(project_id=p.id, raw_text="x", source_note="n"); session.add(m); session.flush()
    pr = MaterialParseResult(project_id=p.id, material_ref=m.id, context_ref=uuid.uuid4(),
                             parse_status="parsed"); session.add(pr); session.flush()
    el = RequirementElement(project_id=p.id, parse_result_ref=pr.id, element_type=el_type,
                            content=term_content, process_status=el_status); session.add(el)
    it = RequirementItem(project_id=p.id, parse_result_ref=pr.id, formation_context_ref=uuid.uuid4(),
                         req_no="REQ-001", expression=item_expr, req_type="functional",
                         status=item_status, source_element_refs=json.dumps([]))
    session.add(it); session.commit()
    return str(p.id), str(el.id), str(it.id)


def _downstream_edges(session, pid, el):
    chain = _svc(session).read_chain(pid, "element", el, "downstream", depth=1)
    return [e for lvl in chain.levels for e in lvl.edges]


def test_derived_supporting_basis_edge_appears(session):
    pid, el, it = _setup(session)
    edges = _downstream_edges(session, pid, el)
    sb = [e for e in edges if e.relation_kind == "supporting_basis"]
    assert len(sb) == 1 and sb[0].origin == "derived"


def test_business_rule_not_name_matched(session):
    # business_rule 无稳定短名，显式排除名称匹配
    pid, el, it = _setup(session, el_type="business_rule",
                         term_content="履约单相关的审批规则", item_expr="履约单需在系统中登记")
    edges = _downstream_edges(session, pid, el)
    assert not [e for e in edges if e.relation_kind == "supporting_basis"]


def test_no_match_when_name_absent(session):
    pid, el, it = _setup(session, term_content="波次是指批量拣货", item_expr="订单需导出为 docx")
    assert not [e for e in _downstream_edges(session, pid, el) if e.relation_kind == "supporting_basis"]


def test_pending_element_no_derived_edge(session):
    # 仅确认态业务翼要素派生支撑边
    pid, el, it = _setup(session, el_status="pending_confirmation")
    assert not [e for e in _downstream_edges(session, pid, el) if e.relation_kind == "supporting_basis"]


def test_formal_edge_projected_and_dedup(session):
    pid, el, it = _setup(session)
    # 建正式 SUPPORTING_BASIS 边（element→item）
    session.add(TraceLink(
        project_id=uuid.UUID(pid), relation_type="supporting_basis",
        upstream_type="element", upstream_ref=uuid.UUID(el),
        downstream_type="requirement_item", downstream_ref=uuid.UUID(it),
        status="effective", initial_basis="人工补全"))
    session.commit()
    sb = [e for e in _downstream_edges(session, pid, el) if e.relation_kind == "supporting_basis"]
    # 正式边优先去重：同对只投一条（origin=ldm013，非 derived）
    assert len(sb) == 1 and sb[0].origin == "ldm013"


def test_business_knowledge_unreferenced_gap(session):
    pid, el, it = _setup(session, term_content="波次是指批量拣货", item_expr="订单需导出为 docx")
    # 该业务翼确认态要素无任何支撑边/图表边 → 缺口
    gaps = _svc(session).read_gaps(pid)
    biz_gaps = [g for g in gaps.items if g.kind == "business_knowledge_unreferenced"]
    assert any(g.node_ref == el for g in biz_gaps)


def test_referenced_business_knowledge_no_gap(session):
    pid, el, it = _setup(session)  # 名称匹配 → 派生支撑边 → 已引用
    gaps = _svc(session).read_gaps(pid)
    biz_gaps = [g for g in gaps.items if g.kind == "business_knowledge_unreferenced"]
    assert not any(g.node_ref == el for g in biz_gaps)


# ---- P4 06 A.1 人工补全支撑依据边写通道 ----
from app.api.schemas import SupportingBasisCommand  # noqa: E402
from app.domain.errors import InvalidInput  # noqa: E402


def _cmd(el, it):
    return SupportingBasisCommand(element_ref=el, item_ref=it, operator_ref="U1")


def test_create_supporting_basis_confirmed_item_effective(session):
    pid, el, it = _setup(session, term_content="波次是指批量拣货", item_expr="订单需导出 docx")
    res = _svc(session).create_supporting_basis(pid, _cmd(el, it))
    assert res.status == "effective"  # 条目确认态 → 边有效
    edges = _downstream_edges(session, pid, el)
    sb = [e for e in edges if e.relation_kind == "supporting_basis"]
    assert len(sb) == 1 and sb[0].origin == "ldm013"


def test_create_supporting_basis_pending_item_pre_established(session):
    pid, el, it = _setup(session, term_content="波次是指批量拣货",
                         item_expr="订单需导出 docx", item_status="pending_confirmation")
    res = _svc(session).create_supporting_basis(pid, _cmd(el, it))
    assert res.status == "pre_established"  # 条目待确认 → 预建立（P7 引用依据）


def test_create_supporting_basis_idempotent(session):
    pid, el, it = _setup(session, term_content="波次是指批量拣货", item_expr="订单需导出 docx")
    svc = _svc(session)
    r1 = svc.create_supporting_basis(pid, _cmd(el, it))
    r2 = svc.create_supporting_basis(pid, _cmd(el, it))
    assert r1.link_ref == r2.link_ref  # 幂等


def test_create_supporting_basis_rejects_requirement_wing(session):
    pid, el, it = _setup(session, el_type="functional_requirement",
                         term_content="系统应导出", item_expr="订单需导出")
    with pytest.raises(InvalidInput):
        _svc(session).create_supporting_basis(pid, _cmd(el, it))


def test_create_supporting_basis_rejects_pending_element(session):
    pid, el, it = _setup(session, el_status="pending_confirmation",
                         term_content="波次是指批量拣货", item_expr="订单需导出")
    with pytest.raises(InvalidInput):
        _svc(session).create_supporting_basis(pid, _cmd(el, it))
