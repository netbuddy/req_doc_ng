"""AEP-094 AI 效能统计（口径设计 §5/§6）测试义务。

覆盖：环节计数与分母口径（superseded 不计入）/ ECE 手算样例 / 样本不足评级 /
覆盖三分（直写=not_applicable）/ 风险信号阈值 / 空项目零值 / 404。
种子直写明细表（明细语义已在 test_adoption_records 经真实服务链验证）。
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    AdoptionRecord,
    AgentRun,
    IntakeRequest,
    ItemDiagnosisRequest,
    ItemFormationRequest,
    ModelResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
)
from app.domain.errors import NotFound
from app.services.ai_effectiveness import AiEffectivenessService


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


def _project(session) -> uuid.UUID:
    p = Project(name="效能统计测试")
    session.add(p)
    session.flush()
    return p.id


def _detail(session, pid, stage, outcome, subject_ref=None, subject_type="element",
            days_ago=0):
    row = AdoptionRecord(
        model_result_ref=uuid.uuid4(), project_id=pid, stage=stage,
        subject_type=subject_type, subject_ref=subject_ref or uuid.uuid4(),
        outcome=outcome, operator_ref="U1", idempotency_key=f"k-{uuid.uuid4()}",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    session.add(row)
    session.flush()
    return row


def test_stage_counts_exclude_superseded_from_total(session):
    pid = _project(session)
    _detail(session, pid, "element_recognition", "adopted")
    _detail(session, pid, "element_recognition", "adopted_with_revision")
    _detail(session, pid, "element_recognition", "rejected")
    _detail(session, pid, "element_recognition", "superseded")  # 不计入分母
    _detail(session, pid, "element_recognition", "adopted", days_ago=90)  # 窗口外
    session.commit()

    read = AiEffectivenessService(session).read(str(pid), window_days=30)
    stage = next(s for s in read.stages if s.stage == "element_recognition")
    assert stage.total == 3
    assert (stage.adopted, stage.adopted_with_revision, stage.rejected) == (1, 1, 1)


def test_calibration_ece_hand_computed(session):
    """手算样例：桶 [0.9,1.0] 4 样本 conf=0.9 全采纳 → |1-0.9|*4；
    桶 [0.3,0.4) 4 样本 conf=0.3 全拒绝 → |0-0.3|*4；ECE=(0.4+1.2)/8=0.2。"""
    pid = _project(session)
    pr_ref = uuid.uuid4()
    for conf, adopted, n in ((0.9, True, 4), (0.3, False, 4)):
        for _ in range(n):
            el = RequirementElement(
                project_id=pid, parse_result_ref=pr_ref,
                element_type="functional_requirement", content="样本要素",
                confidence=conf, process_status="confirmed",
            )
            session.add(el)
            session.flush()
            _detail(session, pid, "element_recognition",
                    "adopted" if adopted else "rejected", subject_ref=el.id)
    session.commit()

    calibration = AiEffectivenessService(session).read(str(pid)).calibration
    assert calibration.sample_size == 8
    assert calibration.ece == pytest.approx(0.2, abs=1e-6)
    assert calibration.rating == "insufficient"  # n<20 一律样本不足
    by_range = {b.range: b for b in calibration.buckets}
    assert by_range["0.9-1.0"].accuracy == 1.0 and by_range["0.3-0.4"].accuracy == 0.0


def test_coverage_three_way_split(session):
    pid = _project(session)
    req = ItemFormationRequest(
        project_id=pid, parse_context_ref=uuid.uuid4(), parse_result_ref=uuid.uuid4(),
        scope_type="all_eligible", target_refs="[]", operator_ref="U1",
        idempotency_key=f"f-{uuid.uuid4()}",
    )
    session.add(req)
    session.flush()

    def item(formation_ctx):
        row = RequirementItem(
            project_id=pid, parse_result_ref=uuid.uuid4(), formation_context_ref=formation_ctx,
            req_no=f"R-{uuid.uuid4().hex[:4]}", expression="条目", req_type="functional",
            status="confirmed", source_element_refs="[]",
        )
        session.add(row)
        session.flush()
        return row

    touched = item(req.id)
    item(req.id)  # 管线产生但无明细 → untouched
    item(uuid.uuid4())  # 直写导入 → not_applicable
    _detail(session, pid, "item_formation", "adopted",
            subject_ref=touched.id, subject_type="requirement_item")
    session.commit()

    coverage = AiEffectivenessService(session).read(str(pid)).coverage
    assert (coverage.touched, coverage.untouched, coverage.not_applicable) == (1, 1, 1)
    assert coverage.total_items == 3


def test_risk_signals_thresholds_and_deferred_conflict(session):
    pid = _project(session)
    for _ in range(9):
        _detail(session, pid, "item_diagnosis", "adopted", subject_type="finding")
    _detail(session, pid, "item_diagnosis", "transferred_to_issue", subject_type="finding")
    session.commit()

    signals = {s.key: s for s in AiEffectivenessService(session).read(str(pid)).risk_signals}
    assert signals["issue_conversion"].level == "high"  # 1/10 = 10% > 8%
    assert signals["issue_conversion"].value == 1
    assert signals["source_conflict"].level == "deferred"  # AEP-065 延期不显示虚构值


def test_empty_project_returns_zero_values(session):
    pid = _project(session)
    session.commit()
    read = AiEffectivenessService(session).read(str(pid))
    assert all(s.total == 0 for s in read.stages)
    assert read.calibration.rating == "insufficient" and read.calibration.ece is None
    assert read.coverage.total_items == 0
    assert read.delivery_failures == []  # 交付失败块空态（口径 §5.5）

    with pytest.raises(NotFound):
        AiEffectivenessService(session).read(str(uuid.uuid4()))


# --- 交付失败块（口径设计 §5.5）：lane × 失败关卡，只读 LDM-015 judgement=*_failed ---


def _intake_ctx(session, pid) -> uuid.UUID:
    r = IntakeRequest(project_id=pid, raw_text="x", operator_ref="U1",
                      idempotency_key=f"i-{uuid.uuid4()}")
    session.add(r)
    session.flush()
    return r.id


def _parse_ctx(session, pid) -> uuid.UUID:
    r = ParseRequest(project_id=pid, material_ref=uuid.uuid4(), operator_ref="U1",
                     idempotency_key=f"p-{uuid.uuid4()}")
    session.add(r)
    session.flush()
    return r.id


def _diag_ctx(session, pid) -> uuid.UUID:
    r = ItemDiagnosisRequest(
        project_id=pid, parse_context_ref=uuid.uuid4(), parse_result_ref=uuid.uuid4(),
        review_context_ref=uuid.uuid4(), item_refs="[]", diagnosis_mode="standard",
        operator_ref="U1", idempotency_key=f"d-{uuid.uuid4()}",
    )
    session.add(r)
    session.flush()
    return r.id


def _model_result(session, stage, ctx_ref, judgement, failure_stage=None, days_ago=0):
    content = None
    if failure_stage is not None:
        content = json.dumps({"item_ref": str(uuid.uuid4()),
                              "failure": {"stage": failure_stage, "detail": "摔倒白话"}})
    mr = ModelResult(
        stage=stage, judgement=judgement, applies_to_ref=ctx_ref,
        result_content=content, process_status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    session.add(mr)
    session.flush()
    return mr


def test_delivery_failures_by_failure_stage_and_denominator(session):
    """分关行：item_diagnosis 写 failure.stage；分母=lane 全部判定行；窗口外剔除。"""
    pid = _project(session)
    diag = _diag_ctx(session, pid)
    _model_result(session, "item_diagnosis", diag, "diagnosed")   # 成功，进分母不进分子
    _model_result(session, "item_diagnosis", diag, "diagnosed")
    _model_result(session, "item_diagnosis", diag, "diagnosis_failed", failure_stage="synthesis")
    _model_result(session, "item_diagnosis", diag, "diagnosis_failed", failure_stage="synthesis")
    _model_result(session, "item_diagnosis", diag, "diagnosis_failed", failure_stage="parse")
    _model_result(session, "item_diagnosis", diag, "diagnosis_failed")  # 缺 failure.stage → 未分关
    _model_result(session, "item_diagnosis", diag, "diagnosis_failed",
                  failure_stage="synthesis", days_ago=90)  # 窗口外，不计
    session.commit()

    read = AiEffectivenessService(session).read(str(pid), window_days=30)
    row = next(d for d in read.delivery_failures if d.stage == "item_diagnosis")
    assert row.total == 6 and row.failed == 4
    buckets = {b.failure_stage: b.count for b in row.by_failure_stage}
    assert buckets == {"synthesis": 2, "parse": 1, "unclassified": 1}


def test_delivery_failures_unclassified_for_non_diagnosis_lane(session):
    """未分关行：非诊断 lane 的失败行无 failure.stage → 全归 unclassified 桶。"""
    pid = _project(session)
    intake = _intake_ctx(session, pid)
    _model_result(session, "source_intake", intake, "acceptable")        # 成功
    _model_result(session, "source_intake", intake, "judgement_failed")  # 失败无分关
    _model_result(session, "source_intake", intake, "judgement_failed")
    # 他项目失败行不得串入本项目分母/分子
    other = _project(session)
    _model_result(session, "source_intake", _intake_ctx(session, other), "judgement_failed")
    session.commit()

    read = AiEffectivenessService(session).read(str(pid))
    row = next(d for d in read.delivery_failures if d.stage == "source_intake")
    assert (row.total, row.failed) == (3, 2)
    assert [(b.failure_stage, b.count) for b in row.by_failure_stage] == [("unclassified", 2)]


def test_delivery_failures_recognition_bare_failed_counted(session):
    """识别 lane 失败码是裸 failed（非 *_failed 后缀）：必须计入分子并归未分关桶。

    审查裁定 T20260713-delivery-failure-stats #1/#2 的回归钉：endswith("_failed")
    会漏计该 lane，令识别失败恒显示 0%。"""
    pid = _project(session)
    parse = _parse_ctx(session, pid)
    _model_result(session, "element_recognition", parse, "recognized")  # 成功，仅进分母
    _model_result(session, "element_recognition", parse, "failed")
    session.commit()

    read = AiEffectivenessService(session).read(str(pid))
    row = next(d for d in read.delivery_failures if d.stage == "element_recognition")
    assert (row.total, row.failed) == (2, 1)
    assert [(b.failure_stage, b.count) for b in row.by_failure_stage] == [("unclassified", 1)]


def test_delivery_failures_zero_failure_lane_shown_with_empty_buckets(session):
    """零失败空态：lane 有判定行但零失败 → failed=0、桶空，仍出现（分母>0）。"""
    pid = _project(session)
    intake = _intake_ctx(session, pid)
    _model_result(session, "source_intake", intake, "acceptable")
    session.commit()

    read = AiEffectivenessService(session).read(str(pid))
    row = next(d for d in read.delivery_failures if d.stage == "source_intake")
    assert row.total == 1 and row.failed == 0 and row.by_failure_stage == []


def _item(session, pid, req_no) -> RequirementItem:
    row = RequirementItem(
        project_id=pid, parse_result_ref=uuid.uuid4(), formation_context_ref=uuid.uuid4(),
        req_no=req_no, expression="条目", req_type="functional", status="confirmed",
        source_element_refs="[]",
    )
    session.add(row)
    session.flush()
    return row


def test_delivery_failure_instances_drilldown(session):
    """个案钻取：白话详情/条目编号/AgentRun 状态解析 + 失败关卡过滤 + 未知 lane/404。"""
    pid = _project(session)
    diag = _diag_ctx(session, pid)
    item = _item(session, pid, "REQ-042")
    # synthesis 失败 + 关联真条目
    syn_row = _model_result(session, "item_diagnosis", diag, "diagnosis_failed",
                            failure_stage="synthesis")
    syn_row.result_content = json.dumps({"item_ref": str(item.id),
        "failure": {"stage": "synthesis", "detail": "综合阶段校验未过"}}, ensure_ascii=False)
    # unclassified 失败：无 failure.detail → 回落 basis
    unc = _model_result(session, "item_diagnosis", diag, "diagnosis_failed")
    unc.basis = "模型服务不可用"
    _model_result(session, "item_diagnosis", diag, "diagnosed")  # 成功不计
    # 关联 AgentRun（kind==stage ∧ context_ref==applies_to_ref）
    session.add(AgentRun(kind="item_diagnosis", status="failed", context_ref=diag))
    session.commit()

    svc = AiEffectivenessService(session)
    allx = svc.delivery_failure_instances(str(pid), "item_diagnosis")
    assert allx.total_failed == 2 and len(allx.instances) == 2
    syn = next(i for i in allx.instances if i.failure_stage == "synthesis")
    assert syn.subject_req_no == "REQ-042"
    assert syn.run_status == "failed"
    assert "综合" in syn.detail
    uc = next(i for i in allx.instances if i.failure_stage == "unclassified")
    assert uc.detail == "模型服务不可用" and uc.subject_req_no is None

    only_syn = svc.delivery_failure_instances(str(pid), "item_diagnosis", failure_stage="synthesis")
    assert only_syn.total_failed == 1 and only_syn.instances[0].failure_stage == "synthesis"

    assert svc.delivery_failure_instances(str(pid), "mystery_lane").instances == []

    with pytest.raises(NotFound):
        svc.delivery_failure_instances(str(uuid.uuid4()), "item_diagnosis")
