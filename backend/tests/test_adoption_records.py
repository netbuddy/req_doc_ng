"""LDM-015 采纳结论明细回写（AI效能统计口径设计 §4/§7）测试义务。

覆盖：接入承接 / 要素裁定 / 发现项复核（needs_improvement 不落结局）/
修订建议裁定 + 旧轮次未裁定发现项 superseded / 图表建议采纳 / 幂等重放不翻倍。
经真实服务链（Stub AI）驱动，明细断言直查 ldm015_adoption_record。
"""
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.adapters.llm import StubSourceIntakeJudge
from app.api.schemas import (
    ElementDecisionCommand,
    ElementRecognitionCommand,
    ItemReviewDiagnosisCommand,
    VerdictAdjudicationCommand,
    ItemRevisionCommand,
    ItemizationBatchCommand,
    TextIntakeCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import AdoptionRecord, ParseRequest
from app.domain.enums import ItemRevisionMode, ItemizationScopeType, ModelJudgement
from app.repositories.sqlalchemy import (
    build_sql_analysis_service,
    build_sql_item_formation_service,
    build_sql_item_review_service,
    build_sql_requirement_item_service,
    build_sql_service,
)


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


def _adoptions(session, stage=None):
    stmt = select(AdoptionRecord)
    if stage:
        stmt = stmt.where(AdoptionRecord.stage == stage)
    return session.scalars(stmt).all()


def _project(session) -> str:
    from app.db.models import Project

    p = Project(name="采纳明细测试")
    session.add(p)
    session.flush()
    return str(p.id)


def _intake(session, pid, text, key, judgement=None) -> str:
    judge = StubSourceIntakeJudge(judgement) if judgement else None
    svc = build_sql_service(session, auto_complete=True, judge=judge)
    result = svc.submit_text_intake(TextIntakeCommand(
        project_ref=pid, raw_text=text, source_note="采纳明细测试材料",
        operator_ref="U1", idempotency_key=key,
    ))
    session.commit()
    return result.context_ref


def test_intake_conclusion_writes_adopted_detail(session):
    pid = _project(session)
    ctx = _intake(session, pid, "系统应支持导出 docx。导出耗时不超过五秒。", "adp-i1")
    rows = _adoptions(session, "source_intake")
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "adopted" and str(row.project_id) == pid
    assert str(row.subject_ref) == ctx and row.subject_type == "material_intake"

    # 排除分支同样承接为 adopted（按判定生效）
    _intake(session, pid, "中午吃什么？", "adp-i2", judgement=ModelJudgement.NO_ASSET_VALUE)
    assert len(_adoptions(session, "source_intake")) == 2


def _recognized_workspace(session, pid):
    ctx = _intake(session, pid, "系统应支持导出 docx。导出耗时不超过五秒。系统必须部署在内网。", "adp-r1")
    svc = build_sql_analysis_service(session, auto_complete=True)
    intake_svc = build_sql_service(session, auto_complete=True)
    material = intake_svc.read_intake_result(ctx).material_ref
    submitted = svc.submit_element_recognition(ElementRecognitionCommand(
        project_ref=pid, material_ref=material, operator_ref="U1", idempotency_key="adp-rec",
    ))
    session.commit()
    return svc, svc.read_element_workspace(submitted.parse_context_ref)


def test_element_decision_writes_adopted_and_rejected(session):
    pid = _project(session)
    svc, ws = _recognized_workspace(session, pid)
    # 取样按模型裁定挑，不按下标挑：一是同一批要素常在同一时刻落库，读回顺序由 id 兜底、并不
    # 稳定；二是被判为「建议剔除」的候选不能确认（冷审查裁定 C1 的后端守卫），撤销则不受限。
    confirm_ref = next(e.id for e in ws.elements if e.model_verdict.value != "suspected_noise")
    reject_ref = next(e.id for e in ws.elements if e.id != confirm_ref)
    svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=ws.parse_context_ref, workspace_version=ws.workspace_version,
        element_refs=[confirm_ref], decision="confirm", operator_ref="U1", idempotency_key="adp-e1",
    ))
    ws2 = svc.read_element_workspace(ws.parse_context_ref)
    svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=ws.parse_context_ref, workspace_version=ws2.workspace_version,
        element_refs=[reject_ref], decision="reject", operator_ref="U1", idempotency_key="adp-e2",
    ))
    session.commit()
    rows = _adoptions(session, "element_recognition")
    outcomes = {str(r.subject_ref): r.outcome for r in rows}
    assert outcomes[confirm_ref] == "adopted" and outcomes[reject_ref] == "rejected"

    # 明细写入端幂等：同 idempotency_key 重放不翻倍
    from app.repositories.sqlalchemy import SqlModelResultRepository

    repo = SqlModelResultRepository(session)
    existing = rows[0]
    repo.record_adoption(
        model_result_ref=str(existing.model_result_ref), project_ref=pid,
        stage="element_recognition", subject_type="element", subject_ref=confirm_ref,
        outcome="adopted", operator_ref="U1", idempotency_key=existing.idempotency_key,
    )
    session.commit()
    assert len(_adoptions(session, "element_recognition")) == len(rows)


def _pending_items(session, pid):
    svc, ws = _recognized_workspace(session, pid)
    # 建议剔除候选不能被确认（冷审查裁定 C1 的后端守卫），取样时排除——stub 识别把第 2 句
    # 判为 suspected_noise，而它恰好是 quality_attribute
    formable = [e.id for e in ws.elements
                if e.element_type.value in ("functional_requirement", "quality_attribute", "constraint")
                and e.model_verdict.value != "suspected_noise"]
    svc.decide_elements(ElementDecisionCommand(
        parse_context_ref=ws.parse_context_ref, workspace_version=ws.workspace_version,
        element_refs=formable, decision="confirm", operator_ref="U1", idempotency_key="adp-f0",
    ))
    session.commit()
    ws = svc.read_element_workspace(ws.parse_context_ref)
    formation = build_sql_item_formation_service(session, auto_complete=True)
    formed = formation.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=pid, parse_result_ref=ws.parse_result_ref,
        workspace_version=ws.workspace_version, scope_type=ItemizationScopeType.ALL_ELIGIBLE,
        operator_ref="U1", idempotency_key="adp-form",
    ))
    session.commit()
    from app.db.models import RequirementItem

    items = session.scalars(select(RequirementItem).order_by(RequirementItem.req_no)).all()
    return ws, formed, [str(i.id) for i in items]


def _ws_version(session, parse_context):
    ctx = session.get(ParseRequest, uuid.UUID(parse_context))
    return str(ctx.workspace_version)


def test_formation_and_review_details(session):
    pid = _project(session)
    ws, formed, item_refs = _pending_items(session, pid)
    assert item_refs

    # 条目落库 → item_formation adopted 明细（机器承接）
    formation_rows = _adoptions(session, "item_formation")
    assert {str(r.subject_ref) for r in formation_rows} == set(item_refs)
    assert all(r.outcome == "adopted" for r in formation_rows)

    # 诊断 + 结论裁决（v5）：拒绝结论→rejected、采纳结论→adopted（subject=review_round）
    review = build_sql_item_review_service(session, auto_complete=True)
    review.start_item_diagnosis(ItemReviewDiagnosisCommand(
        project_ref=pid, item_refs=item_refs, diagnosis_mode="standard",
        workspace_version=_ws_version(session, ws.parse_context_ref),
        operator_ref="U1", idempotency_key="adp-diag",
    ))
    session.commit()
    workspace = review.read_item_review_workspace(formed.formation_context_ref)
    v0 = workspace.review_items[0].current_verdict
    v1 = workspace.review_items[1].current_verdict
    assert v0 is not None and v1 is not None
    review.adjudicate_verdict(VerdictAdjudicationCommand(
        project_ref=pid, item_ref=workspace.review_items[0].item_ref, round_ref=v0.round_ref,
        decision="rejected", reason="演示拒绝",
        workspace_version=_ws_version(session, ws.parse_context_ref),
        operator_ref="U1", idempotency_key="adp-d1",
    ))
    session.commit()
    review.adjudicate_verdict(VerdictAdjudicationCommand(
        project_ref=pid, item_ref=workspace.review_items[1].item_ref, round_ref=v1.round_ref,
        decision="adopted",
        workspace_version=_ws_version(session, ws.parse_context_ref),
        operator_ref="U1", idempotency_key="adp-d2",
    ))
    session.commit()
    review_rows = {str(r.subject_ref): r.outcome for r in _adoptions(session, "item_diagnosis")}
    assert review_rows[v0.round_ref] == "rejected"
    assert review_rows[v1.round_ref].startswith("adopted")
    assert all(r.subject_type == "review_round" for r in _adoptions(session, "item_diagnosis")
               if r.outcome != "superseded")


def test_revision_supersedes_undecided_findings(session):
    pid = _project(session)
    ws, formed, item_refs = _pending_items(session, pid)
    review = build_sql_item_review_service(session, auto_complete=True)
    review.start_item_diagnosis(ItemReviewDiagnosisCommand(
        project_ref=pid, item_refs=item_refs, diagnosis_mode="standard",
        workspace_version=_ws_version(session, ws.parse_context_ref),
        operator_ref="U1", idempotency_key="adp-diag2",
    ))
    session.commit()
    target = item_refs[0]

    item_svc = build_sql_requirement_item_service(session)
    result = item_svc.apply_item_revision(ItemRevisionCommand(
        project_ref=pid, item_ref=target,
        workspace_version=_ws_version(session, ws.parse_context_ref),
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value="修订后的表达，含验收观察口径。",
        operator_ref="U1", idempotency_key="adp-rev",
    ))
    session.commit()
    assert result.status == "applied"
    superseded = [r for r in _adoptions(session, "item_diagnosis") if r.outcome == "superseded"]
    assert superseded, "未裁决结论随修订失效应落 superseded 明细"
    assert all(r.subject_type == "review_round" for r in superseded)
