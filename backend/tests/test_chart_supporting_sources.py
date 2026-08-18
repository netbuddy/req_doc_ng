"""P4 图表业务知识来源测试（06 B）：source_refs 兼容 + SUPPORTING_CONTENT 准入 + 两段候选。"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401
from app.api.schemas import ChartCreateCommand
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    Material, MaterialParseResult, Project, RequirementElement, RequirementItem,
)
from app.domain.enums import ChartFormat, ChartSourceKind, ChartType
from app.repositories.sqlalchemy import build_sql_chart_service
from app.services.chart_collaboration import _parse_source_refs, _source_ref_ids


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


def test_parse_source_refs_three_forms():
    # 旧格式（纯 id）
    assert _parse_source_refs('["i1","i2"]') == [
        {"kind": "requirement_item", "ref": "i1"}, {"kind": "requirement_item", "ref": "i2"}]
    # 新格式（对象）
    assert _parse_source_refs('[{"kind":"supporting_content","ref":"e1"}]') == [
        {"kind": "supporting_content", "ref": "e1"}]
    # 混合
    mixed = _parse_source_refs('["i1",{"kind":"supporting_content","ref":"e1"}]')
    assert mixed[0]["kind"] == "requirement_item" and mixed[1]["kind"] == "supporting_content"
    assert _source_ref_ids('["i1","i2"]') == ["i1", "i2"]
    assert _parse_source_refs(None) == [] and _parse_source_refs("") == []


def _seed(session, *, el_type="role", el_status="confirmed", el_content="订单管理员"):
    p = Project(name="图表来源测试"); session.add(p); session.flush()
    m = Material(project_id=p.id, raw_text="x", source_note="n"); session.add(m); session.flush()
    pr = MaterialParseResult(project_id=p.id, material_ref=m.id, context_ref=uuid.uuid4(),
                             parse_status="parsed"); session.add(pr); session.flush()
    it = RequirementItem(project_id=p.id, parse_result_ref=pr.id, formation_context_ref=uuid.uuid4(),
                         req_no="REQ-001", expression="系统应支持导出", req_type="functional",
                         status="confirmed", source_element_refs=json.dumps([]))
    session.add(it)
    el = RequirementElement(project_id=p.id, parse_result_ref=pr.id, element_type=el_type,
                            content=el_content, process_status=el_status)
    session.add(el)
    session.commit()
    return str(p.id), str(it.id), str(el.id)


def _cmd(pid, kind, refs, key):
    return ChartCreateCommand(
        project_ref=pid, title="关系图", chart_type=ChartType.FLOWCHART, format=ChartFormat.MERMAID,
        source_kind=kind, source_refs=refs, generate_initial=False, operator_ref="U1",
        idempotency_key=key)


def test_eligible_sources_two_segments(session):
    pid, it, el = _seed(session)
    res = build_sql_chart_service(session).list_eligible_sources(pid)
    assert [s.item_ref for s in res.sources] == [it]          # 需求条目段
    assert [b.element_ref for b in res.business_sources] == [el]  # 业务知识段
    assert res.business_sources[0].knowledge_category == "business"


def test_create_chart_from_supporting_content(session):
    pid, it, el = _seed(session)
    svc = build_sql_chart_service(session)
    r = svc.create_chart(_cmd(pid, ChartSourceKind.SUPPORTING_CONTENT, [el], "K1"))
    assert r.status == "created" and r.chart_ref
    # creation_basis 提及业务知识；source_refs 存新格式（kind=supporting_content）
    chart = svc._charts.get_chart(r.chart_ref)
    assert "业务知识" in chart.creation_basis
    assert _parse_source_refs(chart.source_refs)[0]["kind"] == "supporting_content"


def test_precheck_rejects_unconfirmed_business_source(session):
    pid, it, el = _seed(session, el_status="pending_confirmation")
    r = build_sql_chart_service(session).create_chart(
        _cmd(pid, ChartSourceKind.SUPPORTING_CONTENT, [el], "K1"))
    assert r.status == "rejected_precheck"


def test_precheck_rejects_requirement_wing_as_supporting(session):
    # 需求翼要素不能作 supporting_content 来源
    pid, it, el = _seed(session, el_type="functional_requirement", el_content="系统应导出")
    r = build_sql_chart_service(session).create_chart(
        _cmd(pid, ChartSourceKind.SUPPORTING_CONTENT, [el], "K1"))
    assert r.status == "rejected_precheck"


def test_legacy_pure_id_chart_still_works(session):
    # 存量纯 id 来源图表：从条目创建（新格式）+ 读兼容一致
    pid, it, el = _seed(session)
    svc = build_sql_chart_service(session)
    r = svc.create_chart(_cmd(pid, ChartSourceKind.REQUIREMENT_ITEM, [it], "K1"))
    assert r.status == "created"
    chart = svc._charts.get_chart(r.chart_ref)
    assert _source_ref_ids(chart.source_refs) == [it]  # 兼容读取回 id
