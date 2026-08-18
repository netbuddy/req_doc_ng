"""AEP-113 项目删除：覆盖门禁（A1）/ 跨项目隔离（A2）/ 删净（A3）/ 守卫三态（A4）。

删除与残留断言复用 services.project_delete 同一份谓词（DELETE_PLAN / build_scope /
residual_counts），本文件不手写第二份挂靠关系。
"""
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.db.models  # noqa: F401  register tables
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base, make_session_factory
from app.db.models import (
    AdoptionRecord,
    AgentRun,
    AgentRunEvent,
    ChartSourceRevision,
    ChartSuggestionRequest,
    ChartVerificationFinding,
    ChartVerificationRequest,
    ChartVerificationRound,
    ConfigAudit,
    ConfigEntry,
    DemoChatTranscript,
    DocumentIndexEntry,
    DocxExport,
    ElementChangeDraft,
    ElementFacetProjection,
    ElementHistory,
    ElementOperation,
    IntakeRecord,
    IntakeRequest,
    Issue,
    ItemDiagnosisRequest,
    ItemDiagnosisRound,
    ItemFindingVeto,
    ItemFormationRequest,
    ItemizationOutcome,
    ItemRevisionSuggestion,
    ItemReviewFinding,
    ItemStructureProjection,
    KnowledgeAsset,
    KnowledgeSnapshot,
    MarkdownDraft,
    MarkdownPatch,
    Material,
    MaterialParseResult,
    MaterialRevision,
    MaterialSupplement,
    ModelResult,
    Notification,
    ParseRequest,
    Project,
    ReleaseBaseline,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    RequirementItemRevision,
    SearchIndex,
    SectionManuscript,
    TemplateDraft,
    TemplateRegistry,
    TraceLink,
)
from app.deps import get_project_delete_service
from app.domain.errors import NotFound, RejectedTransition
from app.main import app
from app.services.project_delete import (
    DELETE_PLAN,
    GLOBAL_WHITELIST,
    ProjectDeleteService,
    build_scope,
    residual_counts,
)


@pytest.fixture()
def session():
    # StaticPool + check_same_thread=False：TestClient 的同步端点跑在线程池，
    # 内存库须跨线程共享同一连接（否则新线程见到空库）。
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# ---- A1 覆盖门禁 ----

def test_every_table_registered_in_plan_or_whitelist():
    """Base.metadata 全表 ∈ 删除计划 ∪ 白名单；以后新增表不登记即红。"""
    plan_tables = {rule.model.__tablename__ for rule in DELETE_PLAN}
    whitelist_tables = set(GLOBAL_WHITELIST)
    all_tables = set(Base.metadata.tables)

    unregistered = all_tables - plan_tables - whitelist_tables
    assert not unregistered, f"新增表未登记删除计划或白名单：{sorted(unregistered)}"

    ghost = (plan_tables | whitelist_tables) - all_tables
    assert not ghost, f"计划/白名单登记了不存在的表：{sorted(ghost)}"

    assert not plan_tables & whitelist_tables, "同一张表不得同时在计划与白名单"


def test_whitelist_reasons_are_written():
    for table, reason in GLOBAL_WHITELIST.items():
        assert reason.strip(), f"白名单表 {table} 缺书面理由"


def test_plan_columns_exist_on_models():
    """谓词列名拼写门禁：挂靠列必须真实存在（防重命名后计划失效）。"""
    for rule in DELETE_PLAN:
        assert hasattr(rule.model, rule.column), (
            f"{rule.model.__tablename__} 无列 {rule.column}"
        )


# ---- 种子：每张计划表至少 1 行（两个项目对称） ----

def _seed_project(session, tag: str, tmp_path: Path | None = None) -> uuid.UUID:
    """按真实挂靠口径种满 43 张计划表；tag 保证跨项目唯一键不冲突。

    返回 project_id。tmp_path 非空时为 DocxExport 落一个真实磁盘文件。
    """
    def k(name: str) -> str:
        return f"{tag}-{name}"

    project = Project(name=f"项目-{tag}")
    session.add(project)
    session.flush()
    pid = project.id

    intake = IntakeRequest(project_id=pid, raw_text="原文", operator_ref="U1", idempotency_key=k("in"))
    material = Material(project_id=pid, raw_text="材料正文")
    session.add_all([intake, material])
    session.flush()

    # V2 知识层（2026-08-08 第一步）：一条资产带一份快照
    knowledge_asset = KnowledgeAsset(project_id=pid, kind="需求知识")
    session.add(knowledge_asset)
    session.flush()
    session.add(KnowledgeSnapshot(
        asset_id=knowledge_asset.id, seq_no=1,
        content={"kind": "需求知识", "title": "种子", "description": "种子", "category": "其他"},
        content_sha256="0" * 64, content_hash_alg="sha256/jcs",
        author_kind="智能体", task_ref=uuid.uuid4(),
    ))
    session.add_all([
        MaterialRevision(material_ref=material.id, source_version=1, raw_text="旧正文"),
        MaterialSupplement(material_ref=material.id, content="补入块"),
        IntakeRecord(project_id=pid, context_ref=intake.id, intake_conclusion="accepted",
                     material_ref=material.id),
    ])

    parse = ParseRequest(project_id=pid, material_ref=material.id, operator_ref="U1",
                         idempotency_key=k("pr"))
    session.add(parse)
    session.flush()
    parse_result = MaterialParseResult(project_id=pid, material_ref=material.id,
                                       context_ref=parse.id, parse_status="succeeded")
    session.add(parse_result)
    session.flush()

    element = RequirementElement(project_id=pid, parse_result_ref=parse_result.id,
                                 element_type="functional", content="要素", process_status="confirmed")
    session.add(element)
    session.flush()
    operation = ElementOperation(project_id=pid, parse_context_ref=parse.id, kind="review",
                                 payload="{}", operator_ref="U1", idempotency_key=k("op"))
    session.add_all([
        ElementHistory(element_ref=element.id, project_id=pid, version=1, action="register"),
        operation,
        ElementChangeDraft(project_id=pid, parse_context_ref=parse.id, workspace_version=1,
                           operation_type="split", origin="manual", items="[]"),
        ElementFacetProjection(element_ref=element.id, element_version=1, rubric_version=1,
                               facet_key="actor", facet_status="present"),
    ])

    formation = ItemFormationRequest(project_id=pid, parse_context_ref=parse.id,
                                     parse_result_ref=parse_result.id, scope_type="selected",
                                     operator_ref="U1", idempotency_key=k("fm"))
    session.add(formation)
    session.flush()
    item = RequirementItem(project_id=pid, parse_result_ref=parse_result.id,
                           formation_context_ref=formation.id, req_no=f"REQ-{tag}",
                           expression="系统应……", req_type="functional", status="pending_confirm",
                           source_element_refs="[]")
    session.add(item)
    session.flush()
    session.add_all([
        ItemStructureProjection(item_ref=item.id, item_content_rev=1, profile_version=1,
                                row_kind="facet", key="trigger"),
        RequirementItemRevision(item_ref=item.id, field_key="expression", before_value="旧",
                                after_value="新", revision_mode="manual", operator_ref="U1",
                                idempotency_key=k("rev")),
        ItemizationOutcome(formation_context_ref=formation.id, element_ref=element.id,
                           result_status="created", item_ref=item.id),
        ItemRevisionSuggestion(item_ref=item.id, field_key="expression", proposed_value="建议稿"),
    ])

    diagnosis = ItemDiagnosisRequest(project_id=pid, parse_context_ref=parse.id,
                                     parse_result_ref=parse_result.id,
                                     review_context_ref=formation.id, item_refs="[]",
                                     diagnosis_mode="single", operator_ref="U1",
                                     idempotency_key=k("dg"))
    session.add(diagnosis)
    session.flush()
    round_ = ItemDiagnosisRound(project_id=pid, item_ref=item.id, batch_ref=diagnosis.id,
                                diagnosis_mode="single", processing_status="succeeded")
    session.add(round_)
    session.flush()
    session.add(ItemReviewFinding(round_ref=round_.id, item_ref=item.id, finding_type="ambiguity",
                                  diagnosis_summary="含糊", suggested_disposition="revise"))
    session.add(ItemFindingVeto(project_id=pid, item_ref=item.id, finding_type="ambiguity",
                                rule_code="QLT-AMBIG", evidence_span="含糊",
                                finding_summary="含糊", operator_ref="U1",
                                idempotency_key=f"veto-{uuid.uuid4()}"))

    document = RequirementDocument(project_id=pid, status="draft")
    session.add(document)
    session.flush()
    draft = MarkdownDraft(document_ref=document.id, index_version=1, content="# 稿",
                          generated_content="# 稿")
    session.add_all([
        DocumentIndexEntry(document_ref=document.id, index_version=1, section_key="s1",
                           asset_type="requirement_item", asset_ref=item.id),
        SectionManuscript(document_ref=document.id, section_key="s1", content="撰稿"),
        draft,
    ])
    session.flush()
    file_path = None
    if tmp_path is not None:
        file_path = tmp_path / f"{tag}.docx"
        file_path.write_bytes(b"docx-bytes")
    export = DocxExport(document_ref=document.id, draft_ref=draft.id, status="succeeded",
                        file_path=str(file_path) if file_path else None,
                        idempotency_key=k("ex"))
    session.add_all([
        MarkdownPatch(draft_ref=draft.id, impact="wording"),
        export,
    ])
    session.flush()
    session.add(ReleaseBaseline(document_ref=document.id, index_version=1, draft_ref=draft.id,
                                export_ref=export.id, confirmed_by="U1"))

    chart = RequirementChart(project_id=pid, title=f"图-{tag}", chart_kind="flow",
                             chart_type="flowchart", format="mermaid", status="draft")
    session.add(chart)
    session.flush()
    chart_suggestion = ChartSuggestionRequest(project_id=pid, chart_ref=chart.id,
                                              base_draft_version=1, operator_ref="U1",
                                              idempotency_key=k("cs"))
    chart_verification = ChartVerificationRequest(project_id=pid, chart_ref=chart.id,
                                                  chart_draft_version=1, operator_ref="U1",
                                                  idempotency_key=k("cv"))
    session.add_all([
        ChartSourceRevision(chart_ref=chart.id, draft_version=1, source_code="graph TD;",
                            format="mermaid", change_origin="manual"),
        TraceLink(project_id=pid, relation_type="chart_source", upstream_type="requirement_item",
                  upstream_ref=item.id, downstream_type="chart", downstream_ref=chart.id,
                  status="pre_established"),
        Issue(project_id=pid, issue_type="chart_mismatch", title="问题项",
              idempotency_key=k("is")),
        chart_suggestion,
        chart_verification,
    ])
    session.flush()
    chart_round = ChartVerificationRound(chart_ref=chart.id, request_ref=chart_verification.id,
                                         chart_draft_version=1, processing_status="succeeded")
    session.add(chart_round)
    session.flush()
    session.add(ChartVerificationFinding(round_ref=chart_round.id, chart_ref=chart.id,
                                         finding_type="mismatch", summary="图文不符"))

    # LDM-015：两类锚点各 1 行（请求上下文 / 条目），对应盘点表 §3.1 两口径。
    mr_context = ModelResult(applies_to_ref=intake.id, stage="source_intake", judgement="acceptable")
    mr_item = ModelResult(applies_to_ref=item.id, stage="item_revision_draft", judgement="drafted")
    session.add_all([mr_context, mr_item])
    session.flush()
    session.add(AdoptionRecord(model_result_ref=mr_context.id, project_id=pid,
                               stage="source_intake", subject_type="material_intake",
                               subject_ref=material.id, outcome="adopted", operator_ref="U1",
                               idempotency_key=k("ad")))

    # AgentRun：三类 context（请求上下文 / 要素操作 / docx 导出），对应盘点表 §3.2。
    runs = [
        AgentRun(kind="source_intake", status="succeeded", context_ref=intake.id),
        AgentRun(kind="element_review", status="succeeded", context_ref=operation.id),
        AgentRun(kind="docx_export", status="succeeded", context_ref=export.id),
    ]
    session.add_all(runs)
    session.flush()
    session.add_all([
        AgentRunEvent(run_id=runs[0].id, event="agent_run.completed"),
        Notification(kind="agent_run.failed", project_ref=pid, title="通知",
                     dedup_key=k("nt")),
        SearchIndex(project_id=pid, entity_type="requirement_item", ref=f"item:{item.id}"),
        # 演示留痕（项目级挂靠；删项目须一并删，覆盖门禁要求本表删前 ≥1 行）
        DemoChatTranscript(project_ref=pid, channel="review", context_ref=item.id,
                           role="user", kind="command", content="{\"text\": \"/诊断\"}"),
    ])
    session.commit()
    return pid


def _seed_globals(session) -> None:
    """白名单表 + 无归属全局行：删除后必须一行不动。"""
    session.add_all([
        ConfigEntry(domain="model_service", payload="{}"),
        ConfigAudit(domain="model_service", operator_ref="U1"),
        TemplateRegistry(template_key="tpl", name="模板", schema_version="1",
                         content="{}", content_hash="hash-global"),
        TemplateDraft(name="草稿", payload="{}"),
        Notification(kind="system", project_ref=None, title="全局通知", dedup_key="global-nt"),
        ModelResult(applies_to_ref=None, stage="source_intake", judgement="failed"),
    ])
    session.commit()


def _table_row_counts(session) -> dict[str, int]:
    counts = {}
    for table in Base.metadata.sorted_tables:
        counts[table.name] = int(session.scalar(select(func.count()).select_from(table)) or 0)
    return counts


# ---- A2 隔离 + A3 删净 ----

def test_delete_project_a_wipes_clean_and_leaves_b_untouched(session, tmp_path):
    pid_a = _seed_project(session, "A", tmp_path)
    pid_b = _seed_project(session, "B", tmp_path)
    _seed_globals(session)

    scope_a = build_scope(session, pid_a)
    scope_b = build_scope(session, pid_b)

    # 夹具自检：A 的每张计划表删前至少 1 行（保证 43 表全部被真实锻炼）。
    before_a = residual_counts(session, scope_a)
    assert all(v >= 1 for v in before_a.values()), (
        f"种子未覆盖表：{[t for t, v in before_a.items() if v == 0]}"
    )
    before_b = residual_counts(session, scope_b)
    before_all = _table_row_counts(session)
    docx_a = tmp_path / "A.docx"
    docx_b = tmp_path / "B.docx"
    assert docx_a.exists() and docx_b.exists()

    outcome = ProjectDeleteService(session).delete_project(str(pid_a))

    # A3 删净：按计划谓词逐表 0 行（scope 为删前快照，谓词与删除同源）。
    after_a = residual_counts(session, scope_a)
    assert all(v == 0 for v in after_a.values()), (
        f"残留：{[(t, v) for t, v in after_a.items() if v]}"
    )
    assert session.get(Project, pid_a) is None
    assert not docx_a.exists(), "A 的 docx 落盘文件应已删除"
    assert outcome.files_deleted == 1 and outcome.files_failed == 0
    assert outcome.deleted_rows == sum(before_a.values())

    # A2 隔离：B 逐表行数快照比对全表不变；docx 文件仍在。
    assert residual_counts(session, scope_b) == before_b
    assert docx_b.exists()

    # 全库口径：每张表删除行数 == A 的删前行数（白名单表与全局行自然为 0 差）。
    after_all = _table_row_counts(session)
    for table, before_n in before_all.items():
        assert after_all[table] == before_n - before_a.get(table, 0), f"表 {table} 行数异常"

    # 白名单表明确不动 + 无归属全局行保留。
    for table in GLOBAL_WHITELIST:
        assert after_all[table] == before_all[table]
    assert session.scalar(select(func.count()).select_from(Notification)
                          .where(Notification.project_ref.is_(None))) == 1
    assert session.scalar(select(func.count()).select_from(ModelResult)
                          .where(ModelResult.applies_to_ref.is_(None))) == 1


# ---- A4 守卫三态 ----

def test_delete_unknown_project_raises_not_found(session):
    with pytest.raises(NotFound):
        ProjectDeleteService(session).delete_project(str(uuid.uuid4()))
    with pytest.raises(NotFound):
        ProjectDeleteService(session).delete_project("not-a-uuid")


def test_delete_blocked_by_inflight_agent_run_then_succeeds(session):
    pid = _seed_project(session, "C")
    intake_id = session.scalar(select(IntakeRequest.id).where(IntakeRequest.project_id == pid))
    inflight = AgentRun(kind="source_intake", status="queued", context_ref=intake_id)
    session.add(inflight)
    session.commit()

    svc = ProjectDeleteService(session)
    with pytest.raises(RejectedTransition):
        svc.delete_project(str(pid))
    assert session.get(Project, pid) is not None, "409 拒绝时不得有任何删除副作用"

    inflight.status = "succeeded"
    session.commit()
    outcome = svc.delete_project(str(pid))
    assert outcome.deleted_rows > 0
    assert session.get(Project, pid) is None


def test_http_delete_project_three_states(session):
    pid = _seed_project(session, "H")
    app.dependency_overrides[get_project_delete_service] = lambda: ProjectDeleteService(session)
    try:
        client = TestClient(app)

        r = client.delete(f"/api/projects/{uuid.uuid4()}", params={"operator_ref": "测试者"})
        assert r.status_code == 404

        r = client.delete(f"/api/projects/{pid}", params={"operator_ref": "测试者"})
        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "成功"
        report = body["data"]
        assert report["project_id"] == str(pid) and report["deleted_rows"] > 0
        assert report["table_counts"].get("ldm001_project") == 1

        # 缺操作者查询参数 → 422（必填）。
        assert client.delete(f"/api/projects/{uuid.uuid4()}").status_code == 422

        # 删除后再删/再读同为 404（不存在/已删同态）。
        assert client.delete(f"/api/projects/{pid}", params={"operator_ref": "测试者"}).status_code == 404
    finally:
        app.dependency_overrides.pop(get_project_delete_service, None)


def test_http_delete_inflight_returns_rejection_envelope(session):
    """在飞任务阻删：2026-08-07 信封化后走 200＋业务拒绝信封（原 409）。"""
    pid = _seed_project(session, "I")
    intake_id = session.scalar(select(IntakeRequest.id).where(IntakeRequest.project_id == pid))
    session.add(AgentRun(kind="source_intake", status="started", context_ref=intake_id))
    session.commit()
    app.dependency_overrides[get_project_delete_service] = lambda: ProjectDeleteService(session)
    try:
        client = TestClient(app)
        r = client.delete(f"/api/projects/{pid}", params={"operator_ref": "测试者"})
        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "业务拒绝"
        assert body["rejection"]["reason_code"] == "项目内存在执行中任务"
        assert body["rejection"]["details"]["inflight_count"] == 1
        assert "进行中" in body["rejection"]["message"]
    finally:
        app.dependency_overrides.pop(get_project_delete_service, None)
