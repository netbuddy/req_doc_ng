"""需求资产目录·资产读侧（资产树/资产详情/维护列表/条目卡片）测试义务。

设计事实源：04A §5（资产树只读目录、详情只呈现已有事实）+ §3.1（维护列表只显示需求条目）。
种子直写 ORM 表（与真实写路径同表同列，同 test_trace_analysis.py 惯例）。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401  register tables
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    DocumentIndexEntry,
    IntakeRecord,
    Issue,
    Material,
    MaterialParseResult,
    Project,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    RequirementItemRevision,
    TraceLink,
)
from app.domain.errors import NotFound
from app.repositories.asset_read import AssetReadRepository
from app.services.asset_catalog import AssetCatalogService


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


def _svc(session) -> AssetCatalogService:
    return AssetCatalogService(AssetReadRepository(session))


def _seed(session):
    """材料→要素→条目（含修订）→图表→追溯边→文档索引→问题项 的最小资产全集。"""
    p = Project(name="资产目录测试")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text="系统应支持导出 docx。导出耗时不超过五秒。",
                   source_note="评审纪要 2026-06")
    session.add(mat)
    session.flush()
    session.add(IntakeRecord(project_id=p.id, context_ref=uuid.uuid4(),
                             intake_conclusion="accepted", material_ref=mat.id))
    pr = MaterialParseResult(project_id=p.id, material_ref=mat.id,
                             context_ref=uuid.uuid4(), parse_status="parsed")
    session.add(pr)
    session.flush()
    el = RequirementElement(project_id=p.id, parse_result_ref=pr.id,
                            element_type="functional_requirement",
                            content="系统应支持导出 docx", process_status="confirmed")
    session.add(el)
    session.flush()
    item = RequirementItem(
        project_id=p.id, parse_result_ref=pr.id, formation_context_ref=uuid.uuid4(),
        req_no="REQ-001", expression="系统应支持导出 docx", req_type="functional",
        status="confirmed", source_element_refs=json.dumps([str(el.id)]),
    )
    session.add(item)
    session.flush()
    session.add(RequirementItemRevision(
        item_ref=item.id, field_key="expression", before_value="旧表达",
        after_value="系统应支持导出 docx", revision_mode="manual",
        operator_ref="U1", idempotency_key=f"rev-{uuid.uuid4()}",
    ))
    chart = RequirementChart(
        project_id=p.id, title="导出流程图", chart_kind="graphic", chart_type="flowchart",
        format="mermaid", status="confirmed", source_refs=json.dumps([str(item.id)]),
    )
    session.add(chart)
    session.flush()
    session.add(TraceLink(
        project_id=p.id, relation_type="chart",
        upstream_type="requirement_item", upstream_ref=item.id,
        downstream_type="chart", downstream_ref=chart.id,
        status="effective", initial_basis="图表创建预建立",
    ))
    doc = RequirementDocument(project_id=p.id, title="需求规格说明",
                              status="index_ready", index_version=1)
    session.add(doc)
    session.flush()
    session.add(DocumentIndexEntry(document_ref=doc.id, index_version=1,
                                   section_key="4.1", asset_type="requirement_item",
                                   asset_ref=item.id))
    session.add(Issue(project_id=p.id, issue_type="gap", title="追溯缺口待补证据",
                      origin_kind="trace_diagnosis",
                      idempotency_key=f"iss-{uuid.uuid4()}"))
    session.commit()
    return {"project": str(p.id), "material": str(mat.id), "element": str(el.id),
            "item": str(item.id), "chart": str(chart.id), "document": str(doc.id)}


def test_catalog_groups_cover_seven_asset_types_with_counts(session):
    w = _seed(session)
    catalog = _svc(session).read_catalog(w["project"])
    by_type = {g.asset_type: g for g in catalog.groups}
    assert set(by_type) == {"material", "element", "requirement_item", "chart",
                            "trace_link", "document", "issue"}
    assert by_type["material"].count == 1
    assert by_type["requirement_item"].nodes[0].label.startswith("REQ-001")
    assert by_type["requirement_item"].nodes[0].status == "confirmed"
    assert catalog.trace_summary.effective == 1
    assert catalog.trace_summary.suspect == 0


def test_material_label_prefers_raw_text_head_over_source_note(session):
    """目录树材料标签=原文头优先（与追溯节点回退口径一致）；原文为空回退 source_note。"""
    w = _seed(session)
    catalog = _svc(session).read_catalog(w["project"])
    mats = next(g for g in catalog.groups if g.asset_type == "material")
    assert mats.nodes[0].label.startswith("系统应支持导出 docx。")

    p2 = Project(name="空原文材料")
    session.add(p2)
    session.flush()
    session.add(Material(project_id=p2.id, raw_text="", source_note="口头访谈补记"))
    session.commit()
    catalog2 = _svc(session).read_catalog(str(p2.id))
    mats2 = next(g for g in catalog2.groups if g.asset_type == "material")
    assert mats2.nodes[0].label == "口头访谈补记"


def test_material_detail_shows_intake_conclusion_and_derived_elements(session):
    w = _seed(session)
    detail = _svc(session).read_asset_detail(w["project"], "material", w["material"])
    assert detail.status == "accepted"
    attrs = {a.key: a.value for a in detail.attributes}
    assert attrs["derived_elements"] == "1"
    assert detail.relations[0].asset_type == "element"


def test_item_detail_covers_chart_document_and_trace(session):
    w = _seed(session)
    detail = _svc(session).read_asset_detail(w["project"], "requirement_item", w["item"])
    attrs = {a.key: a.value for a in detail.attributes}
    assert attrs["chart_coverage"] == "1"
    assert attrs["in_document_index"] == "yes"
    assert attrs["revisions"] == "1"
    assert attrs["trace_effective"] == "1"


def test_revision_count_excludes_attestation_and_noop_keeps_attribute(session):
    """A4：「修订次数」只算真实字段修订。人工确认背书借修订表落库、没改任何字段——不计；
    拒绝建议等 before==after 的无变更留痕——不计；属性字段编辑（优先级）是真编辑——仍计入。
    详情属性「revisions」与维护列表 revision_count 同口径。"""
    w = _seed(session)  # 种子已含 1 条 expression 修订
    item_id = uuid.UUID(w["item"])
    session.add(RequirementItemRevision(  # 背书：不计
        item_ref=item_id, field_key="source_attestation", before_value="",
        after_value="已人工确认为真实需求（材料未记载）", revision_mode="manual",
        operator_ref="U1", idempotency_key=f"att-{uuid.uuid4()}"))
    session.add(RequirementItemRevision(  # 拒绝建议 before==after：不计
        item_ref=item_id, field_key="expression", before_value="同值", after_value="同值",
        revision_mode="reject_suggestion", operator_ref="U1", idempotency_key=f"rej-{uuid.uuid4()}"))
    session.add(RequirementItemRevision(  # 属性字段编辑：仍计入
        item_ref=item_id, field_key="priority", before_value="", after_value="high",
        revision_mode="manual", operator_ref="U1", idempotency_key=f"pri-{uuid.uuid4()}"))
    session.commit()

    svc = _svc(session)
    detail = svc.read_asset_detail(w["project"], "requirement_item", w["item"])
    attrs = {a.key: a.value for a in detail.attributes}
    assert attrs["revisions"] == "2"   # expression(种子) + priority；背书/no-op 不计

    listed = svc.list_requirement_items(w["project"])
    assert listed.items[0].revision_count == 2


def test_maintenance_list_filters_by_status_type_and_search(session):
    w = _seed(session)
    svc = _svc(session)
    assert svc.list_requirement_items(w["project"]).total == 1
    assert svc.list_requirement_items(w["project"], status="pending_confirmation").total == 0
    assert svc.list_requirement_items(w["project"], req_type="functional").total == 1
    assert svc.list_requirement_items(w["project"], search="docx").total == 1
    assert svc.list_requirement_items(w["project"], search="不存在的词").total == 0


def test_item_card_carries_evidence_revisions_and_related_counts(session):
    w = _seed(session)
    card = _svc(session).read_item_card(w["project"], w["item"])
    assert card.req_no == "REQ-001"
    assert card.source_evidence[0].element_ref == w["element"]
    # 材料标签口径（2026-07-12 卡片语义修正）：原文头优先，source_note 兜底
    assert card.source_evidence[0].material_label == "系统应支持导出 docx。导出耗时不超过五秒。"
    assert card.revisions[0].field_key == "expression"
    assert card.related.charts == 1 and card.related.documents == 1
    assert card.related.trace_effective == 1


def test_unknown_refs_raise_not_found(session):
    w = _seed(session)
    svc = _svc(session)
    with pytest.raises(NotFound):
        svc.read_catalog(str(uuid.uuid4()))
    with pytest.raises(NotFound):
        svc.read_asset_detail(w["project"], "material", str(uuid.uuid4()))
    with pytest.raises(NotFound):
        svc.read_asset_detail(w["project"], "unknown_type", w["material"])
    with pytest.raises(NotFound):
        svc.read_item_card(w["project"], str(uuid.uuid4()))


def test_overview_a_tier_aggregation_covers_charts_documents_issues_and_trace(session):
    """总览台 A 档接线：资产计数补三类 + 覆盖度/追溯风险来自追溯服务口径（04A §10 边界）。"""
    from app.repositories.overview_read import OverviewReadRepository
    from app.repositories.sqlalchemy import SqlIssueRepository, SqlTraceLinkRepository
    from app.repositories.trace_read import TraceReadRepository
    from app.services.overview import OverviewService
    from app.services.trace_analysis import TraceAnalysisService

    w = _seed(session)
    trace = TraceAnalysisService(
        TraceReadRepository(session),
        trace_links=SqlTraceLinkRepository(session),
        issues=SqlIssueRepository(session),
    )
    svc = OverviewService(OverviewReadRepository(session), trace_service=trace)
    overview = svc.read_project_overview(w["project"])

    assets = {m.key: m.value for m in overview.asset_metrics}
    assert assets["charts"] == 1 and assets["documents"] == 1 and assets["issues"] == 1
    coverage = {c.key: c for c in overview.coverage}
    assert coverage["item_source"].ratio == 1.0  # 唯一条目有来源要素
    assert coverage["item_chart"].covered == 1
    assert overview.trace_risk is not None
    assert overview.trace_risk.suspects == 0 and overview.trace_risk.issues == 1

    # 未注入追溯服务时保持空（前端显示待接入），不算第二份事实源
    bare = OverviewService(OverviewReadRepository(session)).read_project_overview(w["project"])
    assert bare.coverage == [] and bare.trace_risk is None


def test_maintenance_list_gap_filter_and_warning_flags(session):
    """29148 属性补齐：缺验收准则/缺优先级警示筛选（仅警示不硬卡的评审工作面）。"""
    w = _seed(session)
    svc = _svc(session)
    # 种子条目两属性均缺失 → 两种 gap 筛选都命中，行上警示标志为真
    listed = svc.list_requirement_items(w["project"])
    assert listed.items[0].verification_missing and listed.items[0].priority_missing
    assert svc.list_requirement_items(w["project"], gap="verification_note").total == 1
    assert svc.list_requirement_items(w["project"], gap="priority").total == 1

    row = session.get(RequirementItem, uuid.UUID(w["item"]))
    row.verification_note = "导出结果可打开"
    row.priority = "medium"
    session.commit()
    svc2 = _svc(session)
    assert svc2.list_requirement_items(w["project"], gap="verification_note").total == 0
    assert svc2.list_requirement_items(w["project"], gap="priority").total == 0
    fresh = svc2.list_requirement_items(w["project"]).items[0]
    assert not fresh.verification_missing and not fresh.priority_missing
    assert fresh.priority == "medium"


def test_item_card_carries_verification_and_priority(session):
    w = _seed(session)
    row = session.get(RequirementItem, uuid.UUID(w["item"]))
    row.verification_method = "test,analysis"
    row.verification_note = "导出结果可打开"
    row.priority = "high"
    session.commit()
    card = _svc(session).read_item_card(w["project"], w["item"])
    assert card.verification_method == ["test", "analysis"]
    assert card.verification_note == "导出结果可打开"
    assert card.priority == "high"


def _seed_business_elements(session):
    """播一个含混翼要素的项目：functional_requirement + term/role/external_system。"""
    p = Project(name="业务知识测试")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text="术语与角色材料", source_note="n")
    session.add(mat)
    session.flush()
    pr = MaterialParseResult(project_id=p.id, material_ref=mat.id,
                             context_ref=uuid.uuid4(), parse_status="parsed")
    session.add(pr)
    session.flush()
    specs = [
        ("functional_requirement", "系统应导出", "confirmed"),
        ("term", "履约单：一次完整作业指令", "confirmed"),
        ("term", "波次：批量拣货任务", "pending_confirmation"),
        ("role", "拣货员", "confirmed"),
        ("external_system", "WMS", "confirmed"),
    ]
    for et, content, st in specs:
        session.add(RequirementElement(
            project_id=p.id, parse_result_ref=pr.id, element_type=et,
            content=content, process_status=st,
        ))
    session.commit()
    return str(p.id)


def test_aep104_lists_only_business_wing(session):
    pid = _seed_business_elements(session)
    res = _svc(session).list_business_knowledge(pid)
    types = {r.element_type for r in res.items}
    assert types == {"term", "role", "external_system"}  # functional_requirement 不入
    assert res.total == 4
    assert all(r.knowledge_category == "business" for r in res.items)
    assert all(r.referenced_count == 0 for r in res.items)  # P4 前恒 0


def test_aep104_filters_by_type_and_status(session):
    pid = _seed_business_elements(session)
    svc = _svc(session)
    terms = svc.list_business_knowledge(pid, element_type="term")
    assert {r.element_type for r in terms.items} == {"term"} and terms.total == 2
    confirmed = svc.list_business_knowledge(pid, status="confirmed")
    assert all(r.process_status == "confirmed" for r in confirmed.items)
    assert confirmed.total == 3  # term(confirmed)+role+external_system


def test_aep104_rejects_requirement_wing_type_filter(session):
    from app.domain.errors import InvalidInput
    pid = _seed_business_elements(session)
    with pytest.raises(InvalidInput):
        _svc(session).list_business_knowledge(pid, element_type="functional_requirement")


def test_maintenance_list_quality_index_from_latest_diagnosis_round(session):
    """维护列表 Q 徽标数据（与质量端点同口径）：LDM-009 发现项 x quality_meta 按序 zip →
    quality_score/quality_alert；no_blocker 不计；未诊断为 None。"""
    from app.db.models import ItemDiagnosisRound, ItemReviewFinding

    w = _seed(session)

    def add_round(round_no, meta, finding_types):
        r = ItemDiagnosisRound(
            project_id=uuid.UUID(w["project"]), item_ref=uuid.UUID(w["item"]),
            batch_ref=uuid.uuid4(), round_no=round_no, diagnosis_mode="standard",
            processing_status="completed", verdict_kind="pass_with_findings",
            quality_meta=json.dumps(meta, ensure_ascii=False) if meta else None,
        )
        session.add(r)
        session.flush()
        for ft in finding_types:
            session.add(ItemReviewFinding(
                round_ref=r.id, item_ref=uuid.UUID(w["item"]), finding_type=ft,
                diagnosis_summary="s", suggested_disposition="revise",
            ))
        session.commit()

    add_round(1, {
        "quality_profile": {"overall": 72},
        "findings": [
            {"finding_type": "ambiguity", "severity": "medium"},
            {"finding_type": "no_blocker"},
        ],
    }, ["ambiguity", "no_blocker"])

    listed = _svc(session).list_requirement_items(w["project"])
    row = next(r for r in listed.items if r.ref == w["item"])
    assert row.quality_score == 72
    assert row.quality_alert == "medium"

    # 新一轮无 quality_meta（旧数据形态）：score 回 None，severity 缺省 medium
    add_round(2, None, ["ambiguity"])
    row2 = next(r for r in _svc(session).list_requirement_items(w["project"]).items if r.ref == w["item"])
    assert row2.quality_score is None
    assert row2.quality_alert == "medium"


def test_maintenance_list_quality_fields_none_without_diagnosis(session):
    w = _seed(session)
    row = _svc(session).list_requirement_items(w["project"]).items[0]
    assert row.quality_score is None
    assert row.quality_alert is None
