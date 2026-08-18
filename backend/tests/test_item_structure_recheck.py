"""条目结构复核（AEP-114 / item_structure_recheck lane）—— T20260711-item-structure-recheck 测试义务。

设计事实源：域文档《条目完备性档案与结构投影》§3（本卡增补结构复核为合法写入者）、任务卡设计裁定。
覆盖：目标集收窄过滤 / 投影锚定当前内容修订序号 / 在飞去重 / 失败保留旧投影 /
现行判定条目零 LLM 调用直发回执 / 区5 /复核 直发通道 / AEP-114 HTTP 面。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import StubItemStructureRechecker
from app.api.schemas import ItemizationBatchCommand, ItemRevisionCommand, StructureRecheckCommand
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    AgentRun,
    IntakeRecord,
    ItemStructureProjection,
    Material,
    MaterialParseResult,
    ModelResult,
    ParseRequest,
    Project,
    RequirementElement,
)
from app.domain.chat_commands import FORMATION_COMMANDS
from app.domain.enums import ItemizationScopeType, ItemRevisionMode
from app.repositories.agent_run import SqlAgentRunRepository
from app.repositories.sqlalchemy import (
    SqlItemFormationProcessRepository,
    SqlModelResultRepository,
    build_sql_item_formation_service,
    build_sql_requirement_item_service,
)
from app.services.item_formation import build_recheck_envelope
from app.services.model_orchestration import QueuedModelOrchestration

RAW_TEXT = "系统应支持导出 docx。导出耗时不超过五秒。"


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


def _anchor(exact: str) -> str:
    start = RAW_TEXT.find(exact)
    return json.dumps({"ranges": [{"start": start, "end": start + len(exact), "exact": exact}]})


def _seed_workspace(session):
    """已接入材料 + 已解析结果 + 两条已确认可形成要素（功能/质量）。"""
    p = Project(name="demo")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text=RAW_TEXT, source_note="接入对象:访谈纪要")
    session.add(mat)
    session.flush()
    session.add(IntakeRecord(
        project_id=p.id, context_ref=uuid.uuid4(), intake_conclusion="accepted", material_ref=mat.id,
    ))
    ctx = ParseRequest(
        project_id=p.id, material_ref=mat.id, operator_ref="U1",
        idempotency_key=f"seed-{uuid.uuid4()}", workspace_version=1,
    )
    session.add(ctx)
    session.flush()
    parse = MaterialParseResult(
        project_id=p.id, material_ref=mat.id, context_ref=ctx.id, parse_status="parsed",
    )
    session.add(parse)
    session.flush()

    def element(etype, content):
        e = RequirementElement(
            project_id=p.id, parse_result_ref=parse.id, element_type=etype,
            content=content, source_anchor=_anchor(content), confidence=0.9,
            process_status="confirmed",
        )
        session.add(e)
        session.flush()
        return str(e.id)

    e_func = element("functional_requirement", "系统应支持导出 docx")
    e_quality = element("quality_attribute", "导出耗时不超过五秒")
    session.commit()
    return {
        "project": str(p.id), "parse_context": str(ctx.id), "parse_result": str(parse.id),
        "e_func": e_func, "e_quality": e_quality,
    }


def _formed(session, rechecker=None):
    """发起形成批次（stub 格式化：两条待确认条目，均带达标投影 rev=1）。"""
    svc = build_sql_item_formation_service(session, auto_complete=True, item_rechecker=rechecker)
    w = _seed_workspace(session)
    result = svc.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        workspace_version="1", scope_type=ItemizationScopeType.ALL_ELIGIBLE,
        target_element_refs=[], operator_ref="U1", idempotency_key=f"B-{uuid.uuid4()}",
    ))
    session.commit()
    read = svc.read_item_formation_workspace(result.formation_context_ref)
    return w, svc, read


def _revise_expression(session, w, item_ref, version, value="修订后的全新表达内容", chained=False):
    """内容修订。默认断开链式自动体检（制造 stale 供手动修复通道用例）；
    chained=True 走完整装配（走查第三轮裁定的自动体检路径）。"""
    svc = build_sql_requirement_item_service(session)
    if not chained:
        svc.on_content_changed_recheck = None
    result = svc.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=version,
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value=value, suggestion_ref=None, reason="触发投影过期",
        operator_ref="U1", idempotency_key=f"R-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "applied"
    return result


def _recheck_command(w, version, item_refs=(), key=None):
    return StructureRecheckCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        workspace_version=version, item_refs=list(item_refs),
        operator_ref="U1", idempotency_key=key or f"RC-{uuid.uuid4()}",
    )


def _projection_rows(session, item_ref):
    return session.scalars(
        select(ItemStructureProjection)
        .where(ItemStructureProjection.item_ref == uuid.UUID(item_ref))
    ).all()


# ============================================================================
# 目标集收窄（裁定 2）：待确认 ∩（修订后未复核 ∪ 无体检结果），排除已终止与现行判定
# ============================================================================

def test_default_targets_filter_stale_and_missing_only(session):
    w, svc, read = _formed(session)
    stale_item = read.pending_items[0]
    current_item = read.pending_items[1]
    version = _revise_expression(session, w, stale_item.item_ref, read.workspace_version).workspace_version

    # 拆分现行条目 → 原条目终止（排除），两条人工新条目无投影（missing）。
    # 断开拆分的链式自动体检（本用例制造 missing 供手动修复通道验证；
    # 触发点已随 issue #8 清理债移入写方法，seam=写权威的挂点）
    svc._item_service.on_content_changed_recheck = None
    dialogue = svc.formation_dialogue(_dialogue(w, "/拆分：\n1. 表达甲乙丙\n2. 表达丁戊己",
                                                version, current_item.item_ref,
                                                read.formation_context_ref))
    session.commit()
    assert dialogue.outcome == "executed" and len(dialogue.created_item_refs) == 2
    version = dialogue.workspace.workspace_version

    result = svc.start_structure_recheck(_recheck_command(w, version))
    session.commit()
    assert result.status == "submitted"
    assert set(result.target_item_refs) == {stale_item.item_ref, *dialogue.created_item_refs}
    assert current_item.item_ref not in result.target_item_refs  # 已终止排除


def test_all_current_targets_rejected_precheck(session):
    w, svc, read = _formed(session)
    result = svc.start_structure_recheck(_recheck_command(w, read.workspace_version))
    assert result.status == "rejected_precheck"
    assert "没有需要复核" in result.next_action
    # 现行判定不烧预算：无复核类 LDM-015 产生
    assert not session.scalars(
        select(ModelResult).where(ModelResult.stage == "item_structure_recheck")
    ).all()


def test_version_conflict_rejected(session):
    w, svc, read = _formed(session)
    result = svc.start_structure_recheck(_recheck_command(w, "99"))
    assert result.status == "rejected_precheck" and "版本" in result.next_action


# ============================================================================
# K1 幻影 queued 批补偿（issue #12 卡A / A1）：enqueue 抛异常 →
# run 置 failed（白话原因）+ 信封不滞留 queued + 修复通道 in_flight 去重立即解堵
# ============================================================================

def _queued_orch_with_failing_enqueue(session):
    """把结构复核编排换成异步形态，其 enqueue 必抛异常（模拟 redis 抖动/入队故障）。"""
    def _boom(context_ref, run_id):
        raise ConnectionError("redis unreachable")

    return QueuedModelOrchestration(
        session, SqlAgentRunRepository(session), _boom,
        enqueue_item_structure_recheck=_boom,
    )


def test_enqueue_failure_compensates_run_envelope_and_unblocks_redispatch(session):
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    # 修订使投影过期（stale）→ 成为复核目标（否则现行判定无目标直发回执）
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    svc._model_orchestration = _queued_orch_with_failing_enqueue(session)

    result = svc.start_structure_recheck(
        _recheck_command(w, version, item_refs=[item.item_ref], key="RC-boom-1")
    )
    session.commit()
    # 受理即回不因入队失败而抛（补偿吞异常）
    assert result.status == "submitted" and result.agent_run_ref

    # ① run 态：补偿置 failed + 白话原因（不落异常原文）
    run = session.get(AgentRun, uuid.UUID(result.agent_run_ref))
    assert run.status == "failed"
    assert run.error == "任务入队失败，可重试" and "redis" not in run.error

    # ② 信封态：不滞留 queued —— 在飞去重联查（按 run 状态 queued/started）已查不到该批
    inflight = SqlItemFormationProcessRepository(session).find_inflight_recheck_of_parse_result(
        w["parse_result"]
    )
    assert inflight is None

    # ③ 再派发通行：修复通道（新 key）不被 in_flight 去重堵死，正常入队新批
    redispatch = svc.start_structure_recheck(
        _recheck_command(w, version, item_refs=[item.item_ref], key="RC-boom-2")
    )
    session.commit()
    assert redispatch.status == "submitted"  # 非 in_flight（未被幻影批阻挡）
    assert redispatch.recheck_context_ref != result.recheck_context_ref


def test_enqueue_failure_compensation_is_generic_across_lanes(session):
    """补偿是派发单一来源的通用行为（非仅复核 lane）：诊断 lane 入队失败同样置 failed。"""
    def _boom(context_ref, run_id):
        raise ConnectionError("redis unreachable")

    orch = QueuedModelOrchestration(session, SqlAgentRunRepository(session), _boom)
    ctx = str(uuid.uuid4())
    run_id = orch.request_item_diagnosis(ctx)  # 不抛
    run = session.get(AgentRun, uuid.UUID(run_id))
    assert run.status == "failed" and run.error == "任务入队失败，可重试"


# ============================================================================
# 投影锚定：复核后 item_content_rev = 当前内容修订序号；形成路径仍恒 1
# ============================================================================

def test_recheck_anchors_projection_to_current_revision_seq(session):
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    before = _projection_rows(session, item.item_ref)
    assert before and all(r.item_content_rev == 1 for r in before)  # 形成路径显式传 1

    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    result = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    session.commit()
    assert result.status == "submitted" and result.target_item_refs == [item.item_ref]

    after = _projection_rows(session, item.item_ref)
    assert after and all(r.item_content_rev == 2 for r in after)  # 锚定当前内容修订序号
    refreshed = svc.read_item_formation_workspace(read.formation_context_ref)
    row = next(i for i in refreshed.pending_items if i.item_ref == item.item_ref)
    assert row.structure_review is not None and not row.structure_review.stale  # 徽标转现行判定
    recheck_results = session.scalars(
        select(ModelResult).where(ModelResult.stage == "item_structure_recheck")
    ).all()
    assert any(r.judgement == "rechecked" for r in recheck_results)  # 判定先落 LDM-015（VAL-002）
    # 只判不改：表达与形成依据未被触碰
    assert row.expression == "修订后的全新表达内容"


# ============================================================================
# 失败通道（A4）：复核失败旧投影原样保留；不阻断
# ============================================================================

def test_recheck_failure_keeps_old_projection(session):
    rechecker = StubItemStructureRechecker(failed=True)
    w, svc, read = _formed(session, rechecker=rechecker)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    before = [(r.key, r.facet_status, r.item_content_rev) for r in _projection_rows(session, item.item_ref)]

    result = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    session.commit()
    assert result.status == "submitted"  # 受理成功，失败是逐条目业务结局

    after = [(r.key, r.facet_status, r.item_content_rev) for r in _projection_rows(session, item.item_ref)]
    assert after == before  # 旧投影原样
    refreshed = svc.read_item_formation_workspace(read.formation_context_ref)
    row = next(i for i in refreshed.pending_items if i.item_ref == item.item_ref)
    assert row.structure_review is not None and row.structure_review.stale  # 仍为修订后未复核
    failed = session.scalars(
        select(ModelResult).where(ModelResult.stage == "item_structure_recheck")
    ).all()
    assert any(r.judgement == "recheck_failed" for r in failed)  # 失败类 LDM-015 留痕


# ============================================================================
# 在飞去重（仿 HK-1）：在途复用原批次；悬死批不挡新批
# ============================================================================

def _seed_recheck_inflight(session, w, item_refs, status="started", age_seconds=0):
    """人造在途复核批次：受理信封 LDM-015 + 挂其上的 AgentRun。"""
    from datetime import datetime, timedelta, timezone

    from app.db.models import AgentRun

    envelope = ModelResult(
        judgement="batch_accepted", basis="结构复核批次受理（过程信封）",
        result_content=json.dumps({
            "project_ref": w["project"], "parse_result_ref": w["parse_result"],
            "item_refs": item_refs, "operator_ref": "U1",
        }, ensure_ascii=False),
        applies_to_ref=uuid.UUID(w["parse_result"]), stage="item_structure_recheck",
        process_status="pending",
    )
    session.add(envelope)
    session.flush()
    run = AgentRun(
        kind="item_structure_recheck", status=status, context_ref=envelope.id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age_seconds),
    )
    session.add(run)
    session.commit()
    return str(envelope.id), str(run.id)


def test_second_submit_reuses_inflight_recheck(session):
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    envelope_ref, run_id = _seed_recheck_inflight(session, w, [item.item_ref])

    second = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    assert second.status == "in_flight"
    assert second.recheck_context_ref == envelope_ref  # 复用原批次
    assert second.agent_run_ref == run_id
    # 不重复执行：没有新的逐条目复核结果
    results = session.scalars(
        select(ModelResult).where(
            ModelResult.stage == "item_structure_recheck",
            ModelResult.judgement != "batch_accepted",
        )
    ).all()
    assert results == []


def test_stale_inflight_recheck_does_not_block(session):
    from app.services.run_liveness import run_liveness_deadline_seconds

    w, svc, read = _formed(session)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    stale_age = run_liveness_deadline_seconds("run_item_structure_recheck") + 60
    envelope_ref, _ = _seed_recheck_inflight(session, w, [item.item_ref], age_seconds=stale_age)

    second = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    session.commit()
    assert second.status == "submitted"
    assert second.recheck_context_ref != envelope_ref  # 僵尸 run 不锁死入口


# ============================================================================
# 区5 /复核 直发通道（裁定 3）：不经解释 lane；现行判定零 LLM 调用（裁定 2）
# ============================================================================

def _dialogue(w, message, version, item_ref=None, formation_context_ref=None):
    from app.api.schemas import FormationDialogueCommand

    return FormationDialogueCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        formation_context_ref=formation_context_ref, workspace_version=version,
        message=message, item_ref=item_ref, selected_element_refs=[],
        operator_ref="U1", idempotency_key=f"D-{uuid.uuid4()}",
    )


def test_recheck_command_registered_direct_channel():
    command = FORMATION_COMMANDS.get("复核")
    assert command is not None and command.operations == ("structure.recheck",)


def test_dialogue_recheck_current_verdict_zero_llm(session):
    """现行判定条目 /复核：确定性直发回执，零 LLM 调用、不产生复核 LDM-015。"""
    rechecker = StubItemStructureRechecker()
    w, svc, read = _formed(session, rechecker=rechecker)
    svc._command_interpreter = None  # 证明短路：无解释器仍可回答（直发通道）
    item = read.pending_items[0]
    result = svc.formation_dialogue(_dialogue(
        w, "/复核", read.workspace_version, item.item_ref, read.formation_context_ref,
    ))
    assert result.outcome == "explanation" and result.operation == "structure.recheck"
    assert "无需复核" in result.explanation
    assert rechecker.calls == []  # 零 LLM 调用
    assert not session.scalars(
        select(ModelResult).where(ModelResult.stage == "item_structure_recheck")
    ).all()  # 不产生新 AgentRun/LDM-015（A1）


def test_dialogue_recheck_stale_item_queues_and_refreshes(session):
    w, svc, read = _formed(session)
    svc._command_interpreter = None  # 直发通道不依赖解释 lane
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    result = svc.formation_dialogue(_dialogue(
        w, "/复核", version, item.item_ref, read.formation_context_ref,
    ))
    session.commit()
    assert result.outcome == "queued" and result.operation == "structure.recheck"
    assert result.agent_run_ref
    refreshed = svc.read_item_formation_workspace(read.formation_context_ref)
    row = next(i for i in refreshed.pending_items if i.item_ref == item.item_ref)
    assert row.structure_review is not None and not row.structure_review.stale  # 同步装配 inline 收束


def test_dialogue_recheck_without_selected_item_clarifies(session):
    w, svc, read = _formed(session)
    result = svc.formation_dialogue(_dialogue(
        w, "/复核", read.workspace_version, None, read.formation_context_ref,
    ))
    assert result.outcome == "clarify" and "选中" in result.message


# ============================================================================
# AEP-114 HTTP 面
# ============================================================================

@pytest.fixture()
def http_session():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture()
def client(http_session):
    from fastapi.testclient import TestClient

    from app.deps import get_item_formation_service
    from app.main import app

    def _override():
        service = build_sql_item_formation_service(http_session, auto_complete=True)
        yield service
        http_session.commit()

    app.dependency_overrides[get_item_formation_service] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_item_formation_service, None)


def test_http_structure_recheck_accepts_and_reports_counts(client, http_session):
    w, svc, read = _formed(http_session)
    item = read.pending_items[0]
    version = _revise_expression(
        http_session, w, item.item_ref, read.workspace_version,
    ).workspace_version

    resp = client.post(
        f"/api/projects/{w['project']}/item-formation/structure-rechecks",
        json={
            "project_ref": w["project"], "parse_result_ref": w["parse_result"],
            "workspace_version": version, "item_refs": [],
            "operator_ref": "U1", "idempotency_key": "H-RC-1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["target_item_refs"] == [item.item_ref]
    # stale/missing 细分计数已撤除（issue #8 清理债：线上无消费者；弹层计数由前端按徽标派生）
    assert "stale_count" not in body and "missing_count" not in body
    assert body["agent_run_ref"] and body["recheck_context_ref"]


def test_dialogue_recheck_on_confirmed_item_rejected(session):
    """已确认条目 /复核：判定随状态冻结——明确拒绝，不误报「判定已是当前表达的结果」。"""
    from app.db.models import RequirementItem

    w, svc, read = _formed(session)
    item = read.pending_items[0]
    row = session.get(RequirementItem, uuid.UUID(item.item_ref))
    row.status = "confirmed"
    session.commit()
    result = svc.formation_dialogue(_dialogue(
        w, "/复核", read.workspace_version, item.item_ref, read.formation_context_ref,
    ))
    assert result.outcome == "rejected_precheck" and "冻结" in result.message
    assert not session.scalars(
        select(ModelResult).where(ModelResult.stage == "item_structure_recheck")
    ).all()


# ============================================================================
# 链式自动体检（走查第三轮裁定 2026-07-11）：内容修订/拆分/归并后自动复核，
# 「修订后未复核」退化为在途瞬态，不再作为用户可见状态
# ============================================================================

def test_revision_chains_recheck_and_refreshes_projection(session):
    """内容修订 → 自动结构体检（同步装配 inline 收束）→ 投影锚定新序号、不再 stale。"""
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    result = _revise_expression(
        session, w, item.item_ref, read.workspace_version, chained=True,
    )
    assert result.structure_recheck_run_ref  # 链式体检运行引用回传（前端静默跟踪）
    assert "结构体检" in result.next_action
    rows = _projection_rows(session, item.item_ref)
    assert rows and all(r.item_content_rev == 2 for r in rows)  # 自动锚定当前内容修订序号
    refreshed = svc.read_item_formation_workspace(read.formation_context_ref)
    row = next(i for i in refreshed.pending_items if i.item_ref == item.item_ref)
    assert row.structure_review is not None and not row.structure_review.stale


def test_split_chains_recheck_for_created_items(session):
    """拆分 → 新条目自动获体检投影（无「无体检结果」用户可见态）。"""
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    dialogue = svc.formation_dialogue(_dialogue(
        w, "/拆分：\n1. 系统应支持导出 docx 文件\n2. 系统应支持选择导出范围",
        read.workspace_version, item.item_ref, read.formation_context_ref,
    ))
    session.commit()
    assert dialogue.outcome == "executed" and dialogue.structure_recheck_run_ref
    for ref in dialogue.created_item_refs:
        rows = _projection_rows(session, ref)
        assert rows and all(r.item_content_rev == 1 for r in rows)  # 新条目无修订，锚=1


def test_chained_recheck_failure_does_not_block_revision(session):
    """链式体检失败：修订照常应用、旧投影原样保留（修复通道=AEP-114 手动入口）。"""
    rechecker = StubItemStructureRechecker(failed=True)
    w, svc, read = _formed(session, rechecker=rechecker)
    item = read.pending_items[0]
    item_svc = build_sql_requirement_item_service(session)
    # 用同一失败 rechecker 的形成服务作链式挂点
    item_svc.on_content_changed_recheck = svc.dispatch_chained_recheck
    result = item_svc.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item.item_ref,
        workspace_version=read.workspace_version,
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value="修订后的全新表达内容", suggestion_ref=None,
        reason="链式失败演练", operator_ref="U1", idempotency_key=f"R-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "applied"  # 体检失败不阻断修订主流程
    rows = _projection_rows(session, item.item_ref)
    assert rows and all(r.item_content_rev == 1 for r in rows)  # 旧投影原样（stale 内部保留）
    # 手动修复通道可用：目标集含该条目
    repair = svc.start_structure_recheck(_recheck_command(
        w, result.workspace_version, item_refs=[item.item_ref],
    ))
    assert repair.status == "submitted" and repair.target_item_refs == [item.item_ref]


def test_recheck_executes_for_confirmed_item(session):
    """准入放宽（竞态覆盖）：执行时点条目已确认仍落投影——确认后内容冻结，判一次永久现行。"""
    from app.adapters.llm import StubItemStructureRechecker as _Stub
    from app.db.models import RequirementItem
    from app.repositories.sqlalchemy import run_item_structure_recheck_judgement

    w, svc, read = _formed(session)
    item = read.pending_items[0]
    version = _revise_expression(
        session, w, item.item_ref, read.workspace_version,
    ).workspace_version  # 未链（模拟受理后、执行前的窗口）
    envelope_ref, _run = _seed_recheck_inflight(session, w, [item.item_ref])
    row = session.get(RequirementItem, uuid.UUID(item.item_ref))
    row.status = "confirmed"  # 执行前抢先确认
    session.commit()

    run_item_structure_recheck_judgement(session, envelope_ref, _Stub())
    session.commit()
    rows = _projection_rows(session, item.item_ref)
    assert rows and all(r.item_content_rev == 2 for r in rows)  # 确认态仍落投影，锚=当前序号


# ============================================================================
# T20260712-recheck-hardening（issue #8）：CAS 版本锚 / 方案解析链 / 覆盖集去重 /
# 派发解耦 / completeness 强制 / 冻结守卫 / 幂等重放 / 单条失败持久通知
# ============================================================================

class _RevisingRechecker(StubItemStructureRechecker):
    """时序桩（缺陷 1）：复核执行中（prepare 之后、accept 之前）条目被修订——
    模拟 rq 多 worker 下旧批 LLM 往返期间用户修订/链式纠正批先行落盘的竞态。"""

    def __init__(self, session, w, mutate_once):
        super().__init__()
        self._session = session
        self._w = w
        self._mutate_once = mutate_once
        self._fired = False

    def recheck(self, project_ref, raw_text, item, sources, convention_key="ears-cn"):
        if not self._fired:
            self._fired = True
            self._mutate_once()
        return super().recheck(project_ref, raw_text, item, sources, convention_key)


def test_cas_discards_stale_judgement_when_revised_in_flight(session):
    """A1：在飞期间修订 → 旧批判定 CAS 丢弃（不写投影、信封记过期跳过），条目可重跑。"""
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version

    def _revise_mid_flight():
        item_svc = build_sql_requirement_item_service(session)
        item_svc.on_content_changed_recheck = None  # 只造版本前进，不级联新批
        item_svc.apply_item_revision(ItemRevisionCommand(
            project_ref=w["project"], item_ref=item.item_ref,
            workspace_version=version, revision_mode=ItemRevisionMode.MANUAL,
            field_key="expression", revised_value="在飞期间的更新表达",
            suggestion_ref=None, reason="在飞竞态", operator_ref="U2",
            idempotency_key=f"R-{uuid.uuid4()}",
        ))

    from app.repositories.sqlalchemy import run_item_structure_recheck_judgement

    envelope_ref, _run = _seed_recheck_inflight(session, w, [item.item_ref], status="started")
    rechecker = _RevisingRechecker(session, w, _revise_mid_flight)
    run_item_structure_recheck_judgement(session, envelope_ref, rechecker)
    session.commit()

    # 旧批判定被丢弃：投影仍是形成时 rev=1 的旧行（stale），未被盖成现行
    rows = _projection_rows(session, item.item_ref)
    assert rows and all(r.item_content_rev == 1 for r in rows)
    refreshed = svc.read_item_formation_workspace(read.formation_context_ref)
    row = next(i for i in refreshed.pending_items if i.item_ref == item.item_ref)
    assert row.structure_review is not None and row.structure_review.stale

    # 信封记「已过期跳过」（回执口径）；条目仍在目标集，双入口可重跑（可重入）
    outcome = svc.read_structure_recheck_outcome(envelope_ref)
    assert outcome.expired_skipped_refs == [item.item_ref]
    assert outcome.refreshed_refs == []
    from app.db.models import AgentRun as _AgentRun
    session.execute(  # 旧批终态化（succeeded），重跑不再被在飞去重挡住
        _AgentRun.__table__.update().where(_AgentRun.context_ref == uuid.UUID(envelope_ref))
        .values(status="succeeded")
    )
    session.commit()
    retry = svc.start_structure_recheck(_recheck_command(
        w, svc.read_item_formation_workspace(read.formation_context_ref).workspace_version,
        item_refs=[item.item_ref],
    ))
    assert retry.status == "submitted" and retry.target_item_refs == [item.item_ref]


def test_cas_does_not_overwrite_newer_projection(session):
    """A1（last-writer-wins 反超）：链式纠正批已写新判定后，旧批 accept 不得回盖旧判定。"""
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version

    def _revise_with_chained_recheck():
        # 完整装配：修订链式自动体检 inline 收束——rev=2 的新判定先行落盘（反超者）
        item_svc = build_sql_requirement_item_service(session)
        item_svc.on_content_changed_recheck = svc.dispatch_chained_recheck
        item_svc.apply_item_revision(ItemRevisionCommand(
            project_ref=w["project"], item_ref=item.item_ref,
            workspace_version=version, revision_mode=ItemRevisionMode.MANUAL,
            field_key="expression", revised_value="反超批次针对的新表达",
            suggestion_ref=None, reason="链式纠正", operator_ref="U2",
            idempotency_key=f"R-{uuid.uuid4()}",
        ))

    from app.repositories.sqlalchemy import run_item_structure_recheck_judgement

    envelope_ref, _run = _seed_recheck_inflight(session, w, [item.item_ref], status="started")
    rechecker = _RevisingRechecker(session, w, _revise_with_chained_recheck)
    run_item_structure_recheck_judgement(session, envelope_ref, rechecker)
    session.commit()

    # 反超者判定（rev=3：setup 修订 rev2＋中途修订 rev3 的链式批）保留；
    # 旧批（锚 rev=2 表达的判定）被 CAS 丢弃，不回盖
    rows = _projection_rows(session, item.item_ref)
    assert rows and all(r.item_content_rev == 3 for r in rows)
    outcome = svc.read_structure_recheck_outcome(envelope_ref)
    assert outcome.expired_skipped_refs == [item.item_ref]


def test_item_convention_resolves_batch_key_for_unprojected_items(session):
    """A2：无投影条目（拆分/归并产物）按批次固定方案判定，不再硬编码回退默认方案。"""
    from app.db.models import ItemFormationRequest as _Req

    w, svc, read = _formed(session)
    item = read.pending_items[0]
    svc._item_service.on_content_changed_recheck = None  # 拆出无投影条目
    dialogue = svc.formation_dialogue(_dialogue(
        w, "/拆分：\n1. 系统应支持导出甲\n2. 系统应支持导出乙",
        read.workspace_version, item.item_ref, read.formation_context_ref,
    ))
    session.commit()
    created = dialogue.created_item_refs
    assert created and all(not _projection_rows(session, r) for r in created)

    # 批次固定方案改为 boilerplate-cn（模拟非默认方案批次）
    req = session.get(_Req, uuid.UUID(read.formation_context_ref))
    req.convention_key = "boilerplate-cn"
    session.commit()

    envelope_ref, _run = _seed_recheck_inflight(session, w, list(created))
    context = svc.prepare_item_structure_recheck(envelope_ref, created[0])
    assert context is not None
    assert context["convention_key"] == "boilerplate-cn"  # 批次方案，非 ears-cn 硬编码

    # docstring 一致性：投影记录仍优先（形成时固定，切换不回溯）——
    # 另一条带 rev=1 投影的待确认条目仍按其投影记录的 ears-cn 取档
    other = read.pending_items[1]
    projected = svc.prepare_item_structure_recheck(envelope_ref, other.item_ref)
    assert projected is not None and projected["convention_key"] == "ears-cn"


def test_inflight_dedup_enqueues_uncovered_targets(session):
    """A3：在途批次只挡已覆盖目标；未覆盖目标正常入队，无「从未尝试却报失败」。"""
    w, svc, read = _formed(session)
    item_a, item_b = read.pending_items[0], read.pending_items[1]
    version = read.workspace_version
    version = _revise_expression(session, w, item_a.item_ref, version).workspace_version
    version = _revise_expression(session, w, item_b.item_ref, version).workspace_version

    # 在途批次只覆盖 A
    _seed_recheck_inflight(session, w, [item_a.item_ref])
    result = svc.start_structure_recheck(_recheck_command(
        w, version, item_refs=[item_a.item_ref, item_b.item_ref],
    ))
    session.commit()
    assert result.status == "submitted"
    assert result.target_item_refs == [item_b.item_ref]  # 只入队未覆盖目标
    assert "在途" in result.next_action  # 回执如实：A 复用在途批次

    # 全覆盖时仍复用在途（原语义保留）
    again = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item_a.item_ref]))
    assert again.status == "in_flight" and again.target_item_refs == [item_a.item_ref]


def test_chained_dispatch_failure_preserves_user_write(session):
    """A4：链式派发失败 → 用户写入已落库＋持久通知＋后续请求无脏事务。"""
    from app.db.models import Notification, RequirementItemRevision

    w, svc, read = _formed(session)
    item = read.pending_items[0]

    def _boom(_ref):
        raise RuntimeError("queue down")

    svc._model_orchestration.request_item_structure_recheck = _boom
    item_svc = build_sql_requirement_item_service(session)
    item_svc.on_content_changed_recheck = svc.dispatch_chained_recheck
    result = item_svc.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item.item_ref,
        workspace_version=read.workspace_version,
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value="派发失败也必须保住的修订", suggestion_ref=None,
        reason="派发失败演练", operator_ref="U1", idempotency_key=f"R-{uuid.uuid4()}",
    ))
    assert result.status == "applied" and result.structure_recheck_run_ref is None

    session.rollback()  # 模拟请求结束；用户写入必须已在派发前 commit 落库
    revisions = session.scalars(
        select(RequirementItemRevision).where(
            RequirementItemRevision.item_ref == uuid.UUID(item.item_ref)
        )
    ).all()
    assert any(r.after_value == "派发失败也必须保住的修订" for r in revisions)
    notices = session.scalars(
        select(Notification).where(Notification.kind == "recheck.dispatch_failed")
    ).all()
    assert len(notices) == 1  # 持久通知：用户离页仍可发现

    # 后续请求无 PendingRollbackError：工作区照常可读
    refreshed = svc.read_item_formation_workspace(read.formation_context_ref)
    assert any(i.item_ref == item.item_ref for i in refreshed.pending_items)


def test_split_dispatch_failure_preserves_created_items(session):
    """A4（拆分路径）：链式派发失败不吞新条目——拆分写入先 commit，失败独立捕获。"""
    from app.db.models import Notification

    w, svc, read = _formed(session)
    item = read.pending_items[0]

    def _boom(_ref):
        raise RuntimeError("queue down")

    svc._model_orchestration.request_item_structure_recheck = _boom
    dialogue = svc.formation_dialogue(_dialogue(
        w, "/拆分：\n1. 系统应支持导出甲\n2. 系统应支持导出乙",
        read.workspace_version, item.item_ref, read.formation_context_ref,
    ))
    assert dialogue.outcome == "executed" and len(dialogue.created_item_refs) == 2
    assert dialogue.structure_recheck_run_ref is None

    session.rollback()  # 拆分写入必须已在派发前落库
    for ref in dialogue.created_item_refs:
        from app.db.models import RequirementItem
        assert session.get(RequirementItem, uuid.UUID(ref)) is not None
    assert session.scalars(
        select(Notification).where(Notification.kind == "recheck.dispatch_failed")
    ).all()


class _PartialRechecker(StubItemStructureRechecker):
    """缺陷 5 桩：只判定部分必备面向（completeness 推导不出 → None）。"""

    def recheck(self, project_ref, raw_text, item, sources, convention_key="ears-cn"):
        from app.adapters.llm import StructureRecheckOutcome
        from app.domain import item_profiles

        self.calls.append(str(item.get("item_ref") or ""))
        profile = item_profiles.get_profile(str(item.get("req_type") or ""), convention_key)
        required = [f for f in profile.facets if f.required]
        from app.adapters.llm import FacetFinding
        facets = tuple(
            FacetFinding(facet=f.key, status="present",
                         evidence=str(item.get("expression") or "")[:30], note=None)
            for f in required[:1]  # 只判第一个必备面向
        )
        return StructureRecheckOutcome(
            statement_conformance="conforms", facets=facets, completeness=None,
            profile_version=profile.profile_version, basis="partial stub",
        )


def test_null_completeness_not_accepted_as_recheck(session):
    """A5：completeness 未得出 → 不承接为已重判（旧投影原样），回执记失败而非假成功。"""
    rechecker = _PartialRechecker()
    w, svc, read = _formed(session, rechecker=rechecker)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    before = [(r.key, r.item_content_rev) for r in _projection_rows(session, item.item_ref)]

    result = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    session.commit()
    assert result.status == "submitted"
    after = [(r.key, r.item_content_rev) for r in _projection_rows(session, item.item_ref)]
    assert after == before  # 旧投影未被 null 判定替换（不再「写入却报失败」双重失实）
    outcome = svc.read_structure_recheck_outcome(result.recheck_context_ref)
    assert outcome.failed_refs == [item.item_ref] and outcome.refreshed_refs == []

    # 不永久重进目标集导致的失实：目标仍可重跑，但每轮结局如实为 failed
    retry = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    assert retry.status == "submitted"


def test_llm_rechecker_rejects_partial_facet_coverage():
    """A5（适配器面）：LLM 输出漏判必备面向 → failed 停靠，不产出 completeness=None 结果。"""
    from app.adapters.llm import LlmItemStructureRechecker

    class _FakeClient:
        def chat(self, system, user):
            return json.dumps({
                "status": "done", "statement_conformance": "conforms",
                "facet_findings": [{
                    "facet": "trigger", "status": "present",
                    "evidence": "当用户点击导出", "note": None,
                }],
                "payload_values": [],
            }, ensure_ascii=False)

    rechecker = LlmItemStructureRechecker(_FakeClient())
    outcome = rechecker.recheck(
        "P1", "原文", {"item_ref": "I1", "req_no": "REQ-001",
                      "expression": "当用户点击导出时系统应生成 docx", "req_type": "functional"},
        [],
    )
    assert outcome.failed and "必备面向未全被判定" in outcome.basis


def test_http_recheck_on_frozen_items_rejected(client, http_session):
    """A6：AEP-114 端点对已确认/已终止条目不再误报「判定已是当前表达的结果」。"""
    from app.db.models import RequirementItem

    w, svc, read = _formed(http_session)
    item = read.pending_items[0]
    _revise_expression(http_session, w, item.item_ref, read.workspace_version)
    row = http_session.get(RequirementItem, uuid.UUID(item.item_ref))
    row.status = "confirmed"  # 已确认＋投影过期：旧口径会落进 noop_current 误报
    http_session.commit()
    version = str(svc.read_item_formation_workspace(read.formation_context_ref).workspace_version)

    resp = client.post(
        f"/api/projects/{w['project']}/item-formation/structure-rechecks",
        json={
            "project_ref": w["project"], "parse_result_ref": w["parse_result"],
            "workspace_version": version, "item_refs": [item.item_ref],
            "operator_ref": "U1", "idempotency_key": f"H-RC-{uuid.uuid4()}",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected_precheck"
    assert "冻结" in body["next_action"] and "已是当前表达" not in body["next_action"]


def test_idempotency_key_replays_original_batch(session):
    """A9：同 idempotency_key 重放返回原批次，不产生重复批次（含含失败条目的批次）。"""
    rechecker = StubItemStructureRechecker(failed=True)  # recheck_failed 批次场景
    w, svc, read = _formed(session, rechecker=rechecker)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version

    key = f"RC-{uuid.uuid4()}"
    first = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref], key=key))
    session.commit()
    assert first.status == "submitted"

    replay = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref], key=key))
    assert replay.status == "submitted"
    assert replay.recheck_context_ref == first.recheck_context_ref  # 原批次
    envelopes = session.scalars(
        select(ModelResult).where(
            ModelResult.stage == "item_structure_recheck",
            ModelResult.judgement == "batch_accepted",
        )
    ).all()
    assert len(envelopes) == 1  # 无重复批次


def test_item_recheck_failure_leaves_persistent_notification(session):
    """A10：run 成功内的单条 recheck_failed 也有持久可见面（通知），用户离页可发现。"""
    from app.db.models import Notification

    rechecker = StubItemStructureRechecker(failed=True)
    w, svc, read = _formed(session, rechecker=rechecker)
    item = read.pending_items[0]
    version = _revise_expression(session, w, item.item_ref, read.workspace_version).workspace_version
    result = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    session.commit()
    assert result.status == "submitted"

    notices = session.scalars(
        select(Notification).where(Notification.kind == "recheck.item_failed")
    ).all()
    assert len(notices) == 1 and item.item_ref in str(notices[0].ref or "")
    outcome = svc.read_structure_recheck_outcome(result.recheck_context_ref)
    assert outcome.failed_refs == [item.item_ref]


# ============================================================================
# K_LIKE 幂等键索引列（issue #12 卡B）：三病灭活 + 跨项目隔离 + 序列化漂移免疫
# 旧实现＝result_content LIKE 片段匹配（find_recheck_by_idempotency）。以下用例钉住
# 索引列等值＋域过滤语义；标注「旧实现下红」者在 LIKE 语义下会误判。
# ============================================================================
def _write_recheck_envelope(session, key, parse_result_ref, project_ref="proj-x"):
    """直接落一条结构复核受理信封（含幂等键索引列）；repo 级用例免全流程铺垫。"""
    ref = build_recheck_envelope(
        SqlModelResultRepository(session),
        project_ref=project_ref, parse_result_ref=parse_result_ref,
        item_refs=[], operator_ref="U1", idempotency_key=key,
    )
    session.flush()
    return ref


@pytest.mark.parametrize("key", ['a%b', 'a_b', 'a\\b', 'pre%_\\post', '100% done'])
def test_recheck_idempotency_special_char_keys_self_match(session, key):
    """A2：含 `%`/`_`/反斜杠的键仍能自匹配找回原批次。

    旧实现下红：反斜杠键经 json 转义后 LIKE 永不自匹配（幂等死）；`%`/`_` 虽自匹配却
    连带误命中他键（见下两例）。索引列等值天然免疫。
    """
    parse = str(uuid.uuid4())
    ref = _write_recheck_envelope(session, key, parse)
    found = SqlItemFormationProcessRepository(session).find_recheck_by_idempotency(key, parse)
    assert found is not None and found.formation_context_ref == ref


def test_recheck_idempotency_percent_key_no_false_positive(session):
    """A2：查询键含 `%` 不得当通配命中他键的信封。

    旧实现下红：LIKE 语义下 `a%b` 会命中 result_content 内含 `aXXXb` 的信封。
    """
    parse = str(uuid.uuid4())
    _write_recheck_envelope(session, "aXXXb", parse)
    found = SqlItemFormationProcessRepository(session).find_recheck_by_idempotency("a%b", parse)
    assert found is None


def test_recheck_idempotency_underscore_key_no_false_positive(session):
    """A2：查询键含 `_` 不得当单字通配命中他键的信封（旧实现下红）。"""
    parse = str(uuid.uuid4())
    _write_recheck_envelope(session, "aXb", parse)
    found = SqlItemFormationProcessRepository(session).find_recheck_by_idempotency("a_b", parse)
    assert found is None


def test_recheck_idempotency_cross_project_isolation(session):
    """A2：同 key 的信封只在其所属 parse_result 域内命中，跨域不泄漏（旧实现无域过滤）。"""
    key = f"SHARED-{uuid.uuid4()}"
    parse_a = str(uuid.uuid4())
    parse_b = str(uuid.uuid4())
    ref_a = _write_recheck_envelope(session, key, parse_a)
    repo = SqlItemFormationProcessRepository(session)
    assert repo.find_recheck_by_idempotency(key, parse_a).formation_context_ref == ref_a
    assert repo.find_recheck_by_idempotency(key, parse_b) is None  # 跨项目不互命中


def test_recheck_idempotency_immune_to_serialization_drift(session):
    """A3（K_LIKE-c）：命中不依赖 result_content 的 json 序列化形状。

    写入后把 payload 重写成完全不含 idempotency_key 字段的另一种序列化，索引列等值仍命中。
    旧实现下红：LIKE 片段匹配依赖 result_content 内的序列化片段，改形即失配。
    """
    key = f"DRIFT-{uuid.uuid4()}"
    parse = str(uuid.uuid4())
    ref = _write_recheck_envelope(session, key, parse)
    # 抹除 payload 内键（模拟序列化漂移 / 键从 JSON 消失），仅索引列留存
    SqlModelResultRepository(session).update_stage_payload(
        ref, json.dumps({"parse_result_ref": parse, "item_refs": []}, separators=(",", ":"))
    )
    session.flush()
    found = SqlItemFormationProcessRepository(session).find_recheck_by_idempotency(key, parse)
    assert found is not None and found.formation_context_ref == ref


def test_http_recheck_outcome_read(client, http_session):
    """AEP-114 读侧：终态后逐条目结局回执（已重判集合）。"""
    w, svc, read = _formed(http_session)
    item = read.pending_items[0]
    version = _revise_expression(http_session, w, item.item_ref, read.workspace_version).workspace_version
    result = svc.start_structure_recheck(_recheck_command(w, version, item_refs=[item.item_ref]))
    http_session.commit()

    resp = client.get(
        f"/api/projects/{w['project']}/item-formation/structure-rechecks/{result.recheck_context_ref}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_item_refs"] == [item.item_ref]
    assert body["refreshed_refs"] == [item.item_ref]
    assert body["pending_refs"] == [] and body["failed_refs"] == []
