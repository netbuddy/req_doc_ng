"""追溯分析服务（TRC-001：AEP-058…AEP-066）测试义务。

设计事实源：docs/40-detailed-design/shared/追溯分析工作台页面设计.md §2–§6 与
docs/iterations/TRC-001/覆盖标记表.md 标"内部"各行 —— 邻域遍历/折叠、失效边窗口口径、
覆盖度与缺口派生、复核重判守卫与默认拒绝、转问题项幂等。
种子直写 ORM 表（与真实写路径同表同列）。
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401  register tables
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    DocumentIndexEntry,
    Material,
    MaterialParseResult,
    Project,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    TraceLink,
)
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.repositories.sqlalchemy import SqlIssueRepository, SqlTraceLinkRepository
from app.repositories.trace_read import TraceReadRepository
from app.api.schemas import TraceIssueCommand, TraceReviewCommand
from app.services.trace_analysis import TraceAnalysisService


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


def _material(session, pid, note="访谈纪要") -> str:
    m = Material(project_id=uuid.UUID(pid), raw_text="系统应支持导出 docx。", source_note=note)
    session.add(m)
    session.flush()
    return str(m.id)


def _parse_result(session, pid, material) -> str:
    r = MaterialParseResult(
        project_id=uuid.UUID(pid), material_ref=uuid.UUID(material),
        context_ref=uuid.uuid4(), parse_status="parsed",
    )
    session.add(r)
    session.flush()
    return str(r.id)


def _element(session, pid, pr, etype="functional_requirement",
             status="confirmed", superseded=False, content="系统应支持导出 docx",
             source_anchor=None) -> str:
    e = RequirementElement(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.UUID(pr), element_type=etype,
        content=content, process_status=status, superseded=superseded,
        source_anchor=source_anchor,
    )
    session.add(e)
    session.flush()
    return str(e.id)


def _item(session, pid, pr, sources=(), status="confirmed", req_no="FR-001") -> str:
    i = RequirementItem(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.UUID(pr),
        formation_context_ref=uuid.uuid4(), req_no=req_no,
        expression="系统应支持导出 docx", req_type="functional", status=status,
        source_element_refs=json.dumps(list(sources)),
    )
    session.add(i)
    session.flush()
    return str(i.id)


def _chart(session, pid, sources=(), status="confirmed", title="导出流程图") -> str:
    c = RequirementChart(
        project_id=uuid.UUID(pid), title=title, chart_kind="graphic",
        chart_type="flowchart", format="mermaid", status=status,
        source_refs=json.dumps(list(sources)),
    )
    session.add(c)
    session.flush()
    return str(c.id)


def _link(session, pid, item, chart, status="effective", reason=None) -> str:
    t = TraceLink(
        project_id=uuid.UUID(pid), relation_type="chart",
        upstream_type="requirement_item", upstream_ref=uuid.UUID(item),
        downstream_type="chart", downstream_ref=uuid.UUID(chart),
        status=status, initial_basis="图表创建预建立", status_reason=reason,
    )
    session.add(t)
    session.flush()
    return str(t.id)


def _document(session, pid, index_version=1, title="需求规格说明") -> str:
    d = RequirementDocument(
        project_id=uuid.UUID(pid), title=title, status="index_ready",
        index_version=index_version,
    )
    session.add(d)
    session.flush()
    return str(d.id)


def _index_entry(session, doc, asset_ref, index_version=1, asset_type="requirement_item") -> None:
    session.add(DocumentIndexEntry(
        document_ref=uuid.UUID(doc), index_version=index_version,
        section_key="4.1", asset_type=asset_type,
        asset_ref=uuid.UUID(asset_ref),
    ))
    session.flush()


def _svc(session) -> TraceAnalysisService:
    return TraceAnalysisService(
        TraceReadRepository(session),
        trace_links=SqlTraceLinkRepository(session),
        issues=SqlIssueRepository(session),
    )


def _seed_chain(session):
    """材料→要素→条目→图表(有效)→文档 的最小全链。"""
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    chart = _chart(session, pid, sources=[item])
    link = _link(session, pid, item, chart, status="effective")
    doc = _document(session, pid)
    _index_entry(session, doc, item)
    return pid, mat, pr, el, item, chart, link, doc


# ============================================================================
# AEP-058 入口锚点 + 小计数
# ============================================================================

def test_entry_counts_anchors_and_default_focus(session):
    pid, mat, pr, el, item, chart, link, doc = _seed_chain(session)
    entry = _svc(session).read_entry(pid)
    assert entry.counts.links_total == 1
    assert entry.counts.effective == 1
    assert entry.counts.suspect == 0
    assert entry.default_focus is not None
    # 默认焦点=最近更新的确认态条目
    assert entry.default_focus.node_type == "requirement_item"
    assert entry.default_focus.ref == item
    types = {g.node_type for g in entry.anchors}
    assert types == {"material", "element", "requirement_item", "chart", "document"}


def test_entry_empty_project_has_no_focus(session):
    pid = _project(session)
    entry = _svc(session).read_entry(pid)
    assert entry.default_focus is None
    assert entry.counts.links_total == 0
    assert entry.next_action is not None


def test_entry_project_not_found(session):
    with pytest.raises(NotFound):
        _svc(session).read_entry(str(uuid.uuid4()))


# ============================================================================
# AEP-059/060 邻域遍历、折叠、失效边口径
# ============================================================================

def test_upstream_chain_from_chart_two_levels(session):
    pid, mat, pr, el, item, chart, link, doc = _seed_chain(session)
    chain = _svc(session).read_chain(pid, "chart", chart, "upstream", depth=3)
    assert chain.focus.ref == chart
    # d1=条目、d2=要素、d3=材料
    assert [lv.distance for lv in chain.levels] == [1, 2, 3]
    assert chain.levels[0].nodes[0].ref == item
    assert chain.levels[1].nodes[0].ref == el
    assert chain.levels[2].nodes[0].ref == mat
    # d1 边来自 LDM-013，带 link_ref
    assert chain.levels[0].edges[0].origin == "ldm013"
    assert chain.levels[0].edges[0].link_ref == link
    # d2 边为结构派生
    assert chain.levels[1].edges[0].origin == "derived"


def test_downstream_chain_from_material(session):
    pid, mat, pr, el, item, chart, link, doc = _seed_chain(session)
    chain = _svc(session).read_chain(pid, "material", mat, "downstream", depth=3)
    assert chain.levels[0].nodes[0].ref == el
    assert chain.levels[1].nodes[0].ref == item
    refs_d3 = {n.ref for n in chain.levels[2].nodes}
    assert refs_d3 == {chart, doc}  # 条目下游=图表+文档承接（派生）


def test_folding_over_budget_keeps_recent_and_summarizes(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    for i in range(10):
        _element(session, pid, pr, content=f"要素{i}")
    chain = _svc(session).read_chain(pid, "material", mat, "downstream", depth=1, limit=4)
    lv = chain.levels[0]
    assert len(lv.nodes) == 4
    assert lv.folded_count == 6
    assert lv.folded_by_type == {"element": 6}


def test_invalid_edge_excluded_by_default(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    chart = _chart(session, pid, sources=[])
    _link(session, pid, item, chart, status="invalid", reason="来源引用移除")
    svc = _svc(session)
    chain = svc.read_chain(pid, "requirement_item", item, "downstream", depth=1)
    assert chain.levels == []  # 失效边默认不进窗口
    chain2 = svc.read_chain(
        pid, "requirement_item", item, "downstream", depth=1, include_invalid=True,
    )
    assert chain2.levels[0].nodes[0].ref == chart


def test_chain_rejects_bad_params_and_missing_focus(session):
    pid, *_ = _seed_chain(session)
    svc = _svc(session)
    with pytest.raises(InvalidInput):
        svc.read_chain(pid, "requirement_item", "x", "sideways")
    with pytest.raises(InvalidInput):
        svc.read_chain(pid, "galaxy", "x", "upstream")
    with pytest.raises(NotFound):
        svc.read_chain(pid, "requirement_item", str(uuid.uuid4()), "upstream")


def test_revoked_and_superseded_elements_out_of_graph(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    _element(session, pid, pr, status="revoked")
    _element(session, pid, pr, superseded=True)
    chain = _svc(session).read_chain(pid, "material", mat, "downstream", depth=1)
    assert chain.levels == []


# ============================================================================
# 卡片语义修正（2026-07-12）：材料节点 label 口径 + material_element 边锚点引文投影
# ============================================================================

def _anchor_json(*exacts) -> str:
    return json.dumps({"version": 1, "ranges": [{"exact": e} for e in exacts]})


def test_material_node_label_prefers_raw_text_head(session):
    """材料节点基础 label=原文头优先；source_note（接入元数据串）降为详情面板字段。"""
    pid = _project(session)
    mat = _material(session, pid, note="来源类型:评审记录；来源对象:结项评审")
    pr = _parse_result(session, pid, mat)
    _element(session, pid, pr)
    chain = _svc(session).read_chain(pid, "material", mat, "downstream", depth=1)
    focus = chain.focus
    assert focus.label == "系统应支持导出 docx。"
    assert focus.source_note == "来源类型:评审记录；来源对象:结项评审"


def test_material_node_label_falls_back_to_source_note(session):
    """原文为空的材料回退 source_note（追溯与资产目录同回退口径）。"""
    pid = _project(session)
    m = Material(project_id=uuid.UUID(pid), raw_text="", source_note="口头访谈补记")
    session.add(m)
    session.flush()
    entry = _svc(session).read_entry(pid)
    mats = next(g for g in entry.anchors if g.node_type == "material")
    assert mats.nodes[0].label == "口头访谈补记"


def test_material_element_edge_projects_anchor_quote(session):
    """边 anchor_quote=下游知识项 source_anchor.ranges[0].exact；全量引文列 anchor_quotes。"""
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    _element(session, pid, pr, source_anchor=_anchor_json("系统应支持导出 docx", "导出耗时不超过五秒"))
    chain = _svc(session).read_chain(pid, "material", mat, "downstream", depth=1)
    edge = chain.levels[0].edges[0]
    assert edge.relation_kind == "material_element"
    assert edge.anchor_quote == "系统应支持导出 docx"  # 卡片取首条
    assert edge.anchor_quotes == ["系统应支持导出 docx", "导出耗时不超过五秒"]  # 详情列全


def test_material_element_edge_anchor_missing_or_broken_is_null(session):
    """锚点缺失/坏 JSON/形状不符/exact 为空 → null（读模型容错，卡片回退原文头）。"""
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    cases = {
        _element(session, pid, pr, content="无锚点"): None,
        _element(session, pid, pr, content="坏JSON", source_anchor="{not-json"): None,
        _element(session, pid, pr, content="非对象", source_anchor='["ranges"]'): None,
        _element(session, pid, pr, content="空exact",
                 source_anchor=json.dumps({"ranges": [{"exact": "  "}]})): None,
        _element(session, pid, pr, content="range非dict",
                 source_anchor=json.dumps({"ranges": ["x"]})): None,
    }
    chain = _svc(session).read_chain(pid, "material", mat, "downstream", depth=1, limit=8)
    edges = {e.downstream_ref: e for e in chain.levels[0].edges}
    for el_ref, expected in cases.items():
        assert edges[el_ref].anchor_quote is expected
        assert edges[el_ref].anchor_quotes == []


def test_non_material_edges_have_no_anchor_quote(session):
    """引文投影仅挂 material_element 边；其余边恒 null（不误挂）。"""
    pid, mat, pr, el, item, chart, link, doc = _seed_chain(session)
    chain = _svc(session).read_chain(pid, "material", mat, "downstream", depth=3)
    for lv in chain.levels:
        for e in lv.edges:
            if e.relation_kind != "material_element":
                assert e.anchor_quote is None and e.anchor_quotes == []


# ============================================================================
# AEP-061 关系详情
# ============================================================================

def test_link_detail_with_labels(session):
    pid, mat, pr, el, item, chart, link, doc = _seed_chain(session)
    read = _svc(session).read_link_detail(pid, link)
    assert read.link_ref == link
    assert read.upstream_label.startswith("FR-001")
    assert read.downstream_label == "导出流程图"
    with pytest.raises(NotFound):
        _svc(session).read_link_detail(pid, str(uuid.uuid4()))


# ============================================================================
# AEP-062 覆盖度（预建立不计入条目→图表）
# ============================================================================

def test_coverage_three_directions(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    covered = _item(session, pid, pr, sources=[el], req_no="FR-001")
    uncovered = _item(session, pid, pr, sources=[], req_no="FR-002")
    chart = _chart(session, pid, sources=[covered])
    _link(session, pid, covered, chart, status="effective")
    pre_chart = _chart(session, pid, sources=[uncovered], title="预建立图")
    _link(session, pid, uncovered, pre_chart, status="pre_established")
    doc = _document(session, pid, index_version=2)
    _index_entry(session, doc, covered, index_version=2)
    _index_entry(session, doc, uncovered, index_version=1)  # 旧索引版本不计

    cov = {d.key: d for d in _svc(session).read_coverage(pid).directions}
    assert (cov["item_source"].covered, cov["item_source"].total) == (1, 2)
    # 预建立不计入：uncovered 虽有预建立关系但不算覆盖
    assert (cov["item_chart"].covered, cov["item_chart"].total) == (1, 2)
    assert (cov["item_document"].covered, cov["item_document"].total) == (1, 2)


def test_coverage_empty_denominator_is_full(session):
    pid = _project(session)
    cov = {d.key: d for d in _svc(session).read_coverage(pid).directions}
    assert all(d.ratio == 1.0 for d in cov.values())


# ============================================================================
# AEP-063 缺口派生（五类）
# ============================================================================

def test_gaps_all_kinds_derived(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el_orphan = _element(session, pid, pr, content="孤儿要素")
    el_used = _element(session, pid, pr, content="在用要素")
    revoked = _element(session, pid, pr, status="revoked", content="已撤销")
    item_no_source = _item(session, pid, pr, sources=[revoked], req_no="FR-001")
    item_ok = _item(session, pid, pr, sources=[el_used], req_no="FR-002")
    chart = _chart(session, pid, sources=[item_ok])
    _link(session, pid, item_ok, chart, status="effective")
    orphan_chart = _chart(session, pid, sources=[], title="孤儿图")
    doc = _document(session, pid)
    _index_entry(session, doc, item_ok)

    gaps = _svc(session).read_gaps(pid)
    by_kind = {}
    for g in gaps.items:
        by_kind.setdefault(g.kind, []).append(g.node_ref)
    # 引用已撤销要素 → 无存量来源
    assert item_no_source in by_kind["item_no_source"]
    # item_no_source 无有效图表、未入索引 → 同时是 chart/document 缺口
    assert item_no_source in by_kind["item_no_chart"]
    assert item_no_source in by_kind["item_no_document"]
    assert by_kind["chart_orphan"] == [orphan_chart]
    assert by_kind["element_orphan"] == [el_orphan]
    assert item_ok not in by_kind["item_no_chart"]
    # 导航目标齐备
    assert all(g.nav_target for g in gaps.items)


def test_gaps_kind_filter_and_invalid_kind(session):
    pid, *_ = _seed_chain(session)
    svc = _svc(session)
    gaps = svc.read_gaps(pid, kind="element_orphan")
    assert all(g.kind == "element_orphan" for g in gaps.items)
    with pytest.raises(InvalidInput):
        svc.read_gaps(pid, kind="black_hole")


# ============================================================================
# AEP-064 可疑清单
# ============================================================================

def test_suspects_list_and_include_invalid(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    c1 = _chart(session, pid, sources=[item], title="图1")
    c2 = _chart(session, pid, sources=[item], title="图2")
    _link(session, pid, item, c1, status="suspect_pending_review", reason="退回修订")
    _link(session, pid, item, c2, status="invalid", reason="作废")
    svc = _svc(session)
    only_suspect = svc.read_suspects(pid)
    assert only_suspect.total == 1
    assert only_suspect.items[0].status == "suspect_pending_review"
    both = svc.read_suspects(pid, include_invalid=True)
    assert both.total == 2


# ============================================================================
# AEP-066 复核重判（守卫 + 默认拒绝 + 幂等）
# ============================================================================

def _cmd(conclusion, key="K1", reason="人工核实来源仍成立"):
    return TraceReviewCommand(
        conclusion=conclusion, reason=reason, operator_ref="U1", idempotency_key=key,
    )


def test_review_restore_suspect_to_pre_established(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    chart = _chart(session, pid, sources=[item], status="draft")
    link = _link(session, pid, item, chart, status="suspect_pending_review", reason="退回修订")
    svc = _svc(session)
    result = svc.review_suspect_link(pid, link, _cmd("restore"))
    assert result.status == "restored"
    assert result.link.status == "pre_established"
    assert "复核恢复" in result.link.status_reason
    # 幂等重放：同 key 再提交按当前状态回放，不再迁移、不报默认拒绝
    replay = svc.review_suspect_link(pid, link, _cmd("restore"))
    assert replay.status == "restored"


def test_review_restore_rejected_when_coverage_not_held(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    chart = _chart(session, pid, sources=[], status="draft")  # 来源已移除
    link = _link(session, pid, item, chart, status="suspect_pending_review")
    with pytest.raises(RejectedTransition):
        _svc(session).review_suspect_link(pid, link, _cmd("restore"))


def test_review_restore_rejected_when_chart_voided(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    chart = _chart(session, pid, sources=[item], status="voided")
    link = _link(session, pid, item, chart, status="suspect_pending_review")
    with pytest.raises(RejectedTransition):
        _svc(session).review_suspect_link(pid, link, _cmd("restore"))


def test_review_non_suspect_default_rejected(session):
    pid, mat, pr, el, item, chart, link, doc = _seed_chain(session)  # link=effective
    with pytest.raises(RejectedTransition):
        _svc(session).review_suspect_link(pid, link, _cmd("restore"))


def test_review_maintain_keeps_status_with_note(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    chart = _chart(session, pid, sources=[item], status="draft")
    link = _link(session, pid, item, chart, status="suspect_pending_review")
    result = _svc(session).review_suspect_link(pid, link, _cmd("maintain", reason="上游仍有争议"))
    assert result.status == "maintained"
    assert result.link.status == "suspect_pending_review"
    assert "复核维持可疑" in result.link.status_reason


def test_review_invalid_conclusion(session):
    pid, *_rest = _seed_chain(session)
    link = _rest[5]
    with pytest.raises(InvalidInput):
        _svc(session).review_suspect_link(pid, link, _cmd("approve"))


# ============================================================================
# AEP-066 转问题项（幂等 + 关联回写）
# ============================================================================

def test_create_diagnosis_issue_idempotent_and_linked(session):
    pid = _project(session)
    mat = _material(session, pid)
    pr = _parse_result(session, pid, mat)
    el = _element(session, pid, pr)
    item = _item(session, pid, pr, sources=[el])
    chart = _chart(session, pid, sources=[item], status="draft")
    link = _link(session, pid, item, chart, status="suspect_pending_review")
    svc = _svc(session)
    cmd = TraceIssueCommand(
        title="可疑链路无法闭合", description="上游条目修订影响待评估",
        trace_link_ref=link, chart_ref=chart,
        operator_ref="U1", idempotency_key="ISSUE-1",
    )
    issue = svc.create_diagnosis_issue(pid, cmd)
    assert issue.origin_kind == "trace_diagnosis"
    assert issue.trace_link_refs == [link]
    # 关联回写：边详情带 issue_ref
    assert svc.read_link_detail(pid, link).issue_ref == issue.issue_ref
    # 幂等重放
    replay = svc.create_diagnosis_issue(pid, cmd)
    assert replay.issue_ref == issue.issue_ref


def test_create_issue_validates_type_and_title(session):
    pid, *_ = _seed_chain(session)
    svc = _svc(session)
    with pytest.raises(InvalidInput):
        svc.create_diagnosis_issue(pid, TraceIssueCommand(
            title="x", issue_type="nonsense", operator_ref="U1", idempotency_key="I-2",
        ))
    with pytest.raises(InvalidInput):
        svc.create_diagnosis_issue(pid, TraceIssueCommand(
            title="  ", operator_ref="U1", idempotency_key="I-3",
        ))


# ============================================================================
# HTTP 形状（路由 + 错误映射）
# ============================================================================

def test_http_shapes_and_error_mapping():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.deps import get_trace_analysis_service
    from app.main import app

    # 共享内存库（StaticPool）：TestClient 在 threadpool 线程跑 sync 路由，需跨线程共用同一 DB。
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()

    pid, mat, pr, el, item, chart, link, doc = _seed_chain(session)
    session.commit()

    app.dependency_overrides[get_trace_analysis_service] = lambda: _svc(session)
    try:
        client = TestClient(app)
        entry = client.get(f"/api/projects/{pid}/trace/entry")
        assert entry.status_code == 200
        assert entry.json()["counts"]["links_total"] == 1

        up = client.get(
            f"/api/projects/{pid}/trace/upstream",
            params={"focus_type": "chart", "focus_ref": chart},
        )
        assert up.status_code == 200
        assert up.json()["direction"] == "upstream"

        cov = client.get(f"/api/projects/{pid}/trace/coverage")
        assert cov.status_code == 200
        assert {d["key"] for d in cov.json()["directions"]} == {
            "item_source", "item_chart", "item_document",
        }

        missing = client.get(f"/api/projects/{uuid.uuid4()}/trace/entry")
        assert missing.status_code == 404

        rejected = client.post(
            f"/api/projects/{pid}/trace/links/{link}/review",
            json={"conclusion": "restore", "operator_ref": "U1", "idempotency_key": "R-1"},
        )
        assert rejected.status_code == 409  # effective 不接受复核（默认拒绝）
    finally:
        app.dependency_overrides.pop(get_trace_analysis_service, None)
        session.close()
        engine.dispose()
