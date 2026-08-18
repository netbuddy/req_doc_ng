"""条目形成服务（AEP-038）+ 需求条目服务（AEP-036）—— SCN-002-P01 测试义务。

设计事实源：slices/SCN-002-P01-需求条目形成/约束与验收.md §3、state-machines/需求条目.md。
覆盖：状态机迁移 / 默认拒绝 / VAL 断言 / 模型输出隔离 / 来源锚点 / 字段修订 / 幂等。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import StubRequirementItemFormatter
from app.api.schemas import ItemizationBatchCommand, ItemRevisionCommand
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    IntakeRecord,
    ItemStructureProjection,
    Material,
    MaterialParseResult,
    ModelResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
    RequirementItemRevision,
)
from app.domain.enums import (
    ItemizationScopeType,
    ItemRevisionMode,
    RequirementItemStatus,
)
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.domain.state_machine import ItemEvent, ItemState, item_transition
from app.repositories.sqlalchemy import (
    build_sql_item_formation_service,
    build_sql_requirement_item_service,
)

RAW_TEXT = "系统应支持导出 docx。导出耗时不超过五秒。导出是把内容写成文件的过程。系统应支持批量导入。"


# ============================================================================
# 状态机单元测试（迁移表是事实源；未列出默认拒绝）
# ============================================================================

def test_item_state_machine_form_creates_pending():
    assert item_transition(ItemState.INITIAL, ItemEvent.FORM) is ItemState.PENDING_CONFIRMATION


def test_item_state_machine_revise_self_loop():
    assert item_transition(ItemState.PENDING_CONFIRMATION, ItemEvent.REVISE) is ItemState.PENDING_CONFIRMATION


@pytest.mark.parametrize("state", [ItemState.CONFIRMED, ItemState.SUPERSEDED, ItemState.TERMINATED])
def test_item_state_machine_default_rejects_revise_on_non_pending(state):
    with pytest.raises(RejectedTransition):
        item_transition(state, ItemEvent.REVISE)


def test_item_state_machine_default_rejects_form_on_existing():
    with pytest.raises(RejectedTransition):
        item_transition(ItemState.PENDING_CONFIRMATION, ItemEvent.FORM)


# ============================================================================
# 持久化集成测试（SQLite create_all）
# ============================================================================

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
    """已接入材料 + 已解析结果 + 五类要素（已确认可形成×2、支撑、未确认、锚点失效）。"""
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

    def element(etype, content, status, anchor):
        e = RequirementElement(
            project_id=p.id, parse_result_ref=parse.id, element_type=etype,
            content=content, source_anchor=anchor, confidence=0.9, process_status=status,
        )
        session.add(e)
        session.flush()
        return str(e.id)

    e_func = element("functional_requirement", "系统应支持导出 docx", "confirmed", _anchor("系统应支持导出 docx"))
    e_quality = element("quality_attribute", "导出耗时不超过五秒", "confirmed", _anchor("导出耗时不超过五秒"))
    e_term = element("term", "导出是把内容写成文件的过程", "confirmed", _anchor("导出是把内容写成文件的过程"))
    e_pending = element("functional_requirement", "系统应支持批量导入", "pending_confirmation", _anchor("系统应支持批量导入"))
    e_no_anchor = element("functional_requirement", "原文没有的表达", "confirmed", None)
    session.commit()
    return {
        "project": str(p.id), "material": str(mat.id),
        "parse_context": str(ctx.id), "parse_result": str(parse.id),
        "e_func": e_func, "e_quality": e_quality, "e_term": e_term,
        "e_pending": e_pending, "e_no_anchor": e_no_anchor,
    }


def _batch_command(w, scope=ItemizationScopeType.ALL_ELIGIBLE, targets=(), key="B1", version="1"):
    return ItemizationBatchCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        workspace_version=version, scope_type=scope,
        target_element_refs=list(targets), operator_ref="U1", idempotency_key=key,
    )


def _run_batch(session, w, **kwargs):
    svc = build_sql_item_formation_service(session, auto_complete=True)
    result = svc.start_element_itemization_batch(_batch_command(w, **kwargs))
    session.commit()
    return svc, result


# ---- AEP-038 主路径 + 准入 + 逐要素归因 ----

def test_batch_creates_pending_items_per_eligible_element(session):
    w = _seed_workspace(session)
    svc, result = _run_batch(session, w)
    assert result.status == "submitted" and result.formation_context_ref

    items = session.scalars(select(RequirementItem)).all()
    assert len(items) == 2  # 仅已确认 ∧ 需求表达类 ∧ 锚点可回原文（e_func + e_quality）
    assert all(i.status == RequirementItemStatus.PENDING_CONFIRMATION.value for i in items)  # VAL：只写待确认
    assert all(i.version_no == 1 for i in items)
    sources = {json.loads(i.source_element_refs)[0] for i in items}
    assert sources == {w["e_func"], w["e_quality"]}  # 逐要素归因：一要素一条目
    assert all(i.formation_basis_ref is not None for i in items)  # 形成依据指向 LDM-015

    read = svc.read_item_formation_workspace(result.formation_context_ref)
    assert len(read.pending_items) == 2
    by_status = {}
    for r in read.batch_results:
        by_status.setdefault(r.result_status.value, []).append(r)
    assert len(by_status["created"]) == 2
    blocked_refs = {r.element_ref for r in by_status["blocked"]}
    assert blocked_refs == {w["e_pending"], w["e_no_anchor"]}  # 未确认 + 锚点失效逐要素停靠
    assert all(r.next_action for r in by_status["blocked"])  # AC-004：停靠结局 next_action 非空
    # 支撑性要素不成条目、也不算全选批次的停靠，只在 blocked_elements 呈现依据角色
    roles = {b.id: b.formation_role for b in read.blocked_elements}
    assert roles[w["e_term"]] == "supporting"


def test_batch_model_output_isolated_in_ldm015(session):
    w = _seed_workspace(session)
    _run_batch(session, w)
    results = session.scalars(
        select(ModelResult).where(ModelResult.stage == "item_formation")
    ).all()
    assert results and all(r.judgement == "formatted" for r in results)  # VAL-002：建议先落 LDM-015
    refs = set()
    for r in results:
        payload = json.loads(r.result_content)
        assert len(payload["items"]) == 1  # 逐要素送检：每要素一条格式化类 LDM-015
        refs.add(payload["element_ref"])
    assert refs == {w["e_func"], w["e_quality"]}  # 逐要素归因
    for item in session.scalars(select(RequirementItem)).all():
        assert item.expression  # 裁定通过后才写 LDM-007


def test_batch_selected_subset_and_single(session):
    w = _seed_workspace(session)
    svc, result = _run_batch(
        session, w, scope=ItemizationScopeType.SELECTED_ELEMENTS, targets=[w["e_func"]]
    )
    assert result.status == "submitted"
    items = session.scalars(select(RequirementItem)).all()
    assert len(items) == 1
    assert json.loads(items[0].source_element_refs) == [w["e_func"]]

    with pytest.raises(InvalidInput):  # single_element 只能携带一个要素
        svc.start_element_itemization_batch(_batch_command(
            w, scope=ItemizationScopeType.SINGLE_ELEMENT,
            targets=[w["e_func"], w["e_quality"]], key="B2", version="2",
        ))


def test_batch_no_eligible_rejected_precheck(session):
    w = _seed_workspace(session)
    session.execute(  # 把可形成要素全部改为未确认
        RequirementElement.__table__.update().values(process_status="pending_confirmation")
    )
    session.commit()
    svc = build_sql_item_formation_service(session, auto_complete=True)
    result = svc.start_element_itemization_batch(_batch_command(w))
    assert result.status == "rejected_precheck" and result.next_action  # 无可形成条目停靠
    assert session.scalar(select(RequirementItem)) is None


def test_batch_version_conflict_rejected(session):
    w = _seed_workspace(session)
    svc = build_sql_item_formation_service(session, auto_complete=True)
    result = svc.start_element_itemization_batch(_batch_command(w, version="99"))
    assert result.status == "rejected_precheck" and "版本" in result.next_action
    assert session.scalar(select(RequirementItem)) is None


def test_batch_idempotent_replay(session):
    w = _seed_workspace(session)
    svc, first = _run_batch(session, w, key="SAME")
    replay = svc.start_element_itemization_batch(_batch_command(w, key="SAME", version="99"))
    assert replay.formation_context_ref == first.formation_context_ref  # 幂等：返回原批次
    assert len(session.scalars(select(RequirementItem)).all()) == 2  # 不重复创建


def test_batch_formatter_failure_creates_no_items(session):
    w = _seed_workspace(session)
    svc = build_sql_item_formation_service(
        session, auto_complete=True, item_formatter=StubRequirementItemFormatter(failed=True)
    )
    result = svc.start_element_itemization_batch(_batch_command(w))
    session.commit()
    assert result.status == "submitted"
    assert session.scalar(select(RequirementItem)) is None  # 失败不伪造条目
    mr = session.scalar(select(ModelResult).where(ModelResult.stage == "item_formation"))
    assert mr.judgement == "formation_failed"  # 失败类 LDM-015 已登记
    read = svc.read_item_formation_workspace(result.formation_context_ref)
    failed = [r for r in read.batch_results if r.result_status.value == "failed"]
    assert len(failed) == 2 and all(r.next_action for r in failed)  # 逐要素失败归因 + AC-004
    assert read.next_action  # 批次级停靠提示


def test_batch_already_itemized_element_blocked(session):
    w = _seed_workspace(session)
    svc, first = _run_batch(session, w, key="B1")
    read = svc.read_item_formation_workspace(first.formation_context_ref)
    result = svc.start_element_itemization_batch(_batch_command(
        w, scope=ItemizationScopeType.SINGLE_ELEMENT, targets=[w["e_func"]],
        key="B2", version=read.workspace_version,
    ))
    assert result.status == "rejected_precheck"  # 已形成条目的要素不再重复形成
    assert len(session.scalars(select(RequirementItem)).all()) == 2


# ---- AEP-036 待确认字段修订 ----

def _formed(session):
    w = _seed_workspace(session)
    svc, result = _run_batch(session, w)
    read = svc.read_item_formation_workspace(result.formation_context_ref)
    return w, svc, read


def _revision_command(w, item_ref, version, mode=ItemRevisionMode.MANUAL, key="R1", **kwargs):
    defaults = dict(field_key="expression", revised_value=None, suggestion_ref=None, reason=None)
    defaults.update(kwargs)
    return ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=version,
        revision_mode=mode, operator_ref="U1", idempotency_key=key, **defaults,
    )


def test_revision_manual_expression_keeps_pending(session):
    w, form_svc, read = _formed(session)
    item = read.pending_items[0]
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version, revised_value="系统应支持导出 docx 格式文档", reason="表达更明确",
    ))
    session.commit()
    assert result.status == "applied" and result.revision_record_ref
    row = session.get(RequirementItem, uuid.UUID(item.item_ref))
    assert row.expression == "系统应支持导出 docx 格式文档"
    assert row.status == RequirementItemStatus.PENDING_CONFIRMATION.value  # 修订后仍待确认
    # 阶段策略解耦 P1：直发修订回执只陈述真发生的事（修订已应用、旧结论随版本失效），
    # 不再提「链式增量诊断」——链式复诊迁回评审采纳动作，直发路径不触发。
    assert "修订已应用" in result.next_action and "旧结论随版本失效" in result.next_action
    assert int(result.workspace_version) > int(read.workspace_version)  # 工作区版本递增
    rev = session.scalar(select(RequirementItemRevision))
    assert rev.before_value == item.expression and rev.after_value == row.expression


def test_revision_req_type(session):
    w, _, read = _formed(session)
    item = read.pending_items[0]
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version,
        field_key="req_type", revised_value="constraint",
    ))
    assert result.status == "applied"
    assert session.get(RequirementItem, uuid.UUID(item.item_ref)).req_type == "constraint"

    with pytest.raises(InvalidInput):  # 非法类型码
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, result.workspace_version,
            field_key="req_type", revised_value="not-a-type", key="R2",
        ))


def test_revision_accept_suggestion(session):
    w, _, read = _formed(session)
    item = read.pending_items[0]
    suggestion = next(s for s in read.revision_suggestions if s.item_ref == item.item_ref)
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version,
        mode=ItemRevisionMode.ACCEPT_SUGGESTION, suggestion_ref=suggestion.suggestion_ref,
    ))
    assert result.status == "applied"
    row = session.get(RequirementItem, uuid.UUID(item.item_ref))
    assert row.expression == suggestion.proposed_value  # 采纳建议值
    from app.db.models import ItemRevisionSuggestion
    assert session.get(ItemRevisionSuggestion, uuid.UUID(suggestion.suggestion_ref)).status == "accepted"


def test_revision_reject_suggestion_keeps_fields(session):
    w, _, read = _formed(session)
    item = read.pending_items[0]
    suggestion = next(s for s in read.revision_suggestions if s.item_ref == item.item_ref)
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version,
        mode=ItemRevisionMode.REJECT_SUGGESTION, suggestion_ref=suggestion.suggestion_ref,
    ))
    assert result.status == "applied"
    row = session.get(RequirementItem, uuid.UUID(item.item_ref))
    assert row.expression == item.expression  # 拒绝建议不改字段
    from app.db.models import ItemRevisionSuggestion
    assert session.get(ItemRevisionSuggestion, uuid.UUID(suggestion.suggestion_ref)).status == "rejected"

    with pytest.raises(RejectedTransition):  # 已处置建议不能重复采纳
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, result.workspace_version,
            mode=ItemRevisionMode.ACCEPT_SUGGESTION, suggestion_ref=suggestion.suggestion_ref, key="R2",
        ))


def test_revision_version_conflict_rejected(session):
    w, _, read = _formed(session)
    item = read.pending_items[0]
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, "99", revised_value="任意值",
    ))
    assert result.status == "rejected_precheck" and "刷新" in result.next_action
    assert session.get(RequirementItem, uuid.UUID(item.item_ref)).expression == item.expression


def test_revision_idempotent_replay(session):
    w, _, read = _formed(session)
    item = read.pending_items[0]
    svc = build_sql_requirement_item_service(session)
    cmd = _revision_command(w, item.item_ref, read.workspace_version,
                            revised_value="修订一次", key="SAME")
    first = svc.apply_item_revision(cmd)
    session.commit()
    replay = svc.apply_item_revision(cmd)
    assert replay.revision_record_ref == first.revision_record_ref  # 幂等：返回原修订
    assert len(session.scalars(select(RequirementItemRevision)).all()) == 1


def test_revision_default_rejects_non_pending_item(session):
    w, _, read = _formed(session)
    item = read.pending_items[0]
    session.get(RequirementItem, uuid.UUID(item.item_ref)).status = "confirmed"
    session.commit()
    svc = build_sql_requirement_item_service(session)
    with pytest.raises(RejectedTransition):  # 确认态不可原地修改（VAL-001 + 默认拒绝）
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, read.workspace_version, revised_value="不该生效",
        ))


def test_revision_unknown_item_not_found(session):
    w, _, read = _formed(session)
    svc = build_sql_requirement_item_service(session)
    with pytest.raises(NotFound):
        svc.apply_item_revision(_revision_command(w, str(uuid.uuid4()), read.workspace_version, revised_value="x"))


# ---- AEP-036 来源要素登记通道（issue #30 出口：source_element_refs 开放为修订字段）----

def test_revision_source_element_refs_persists_and_records(session):
    """A1：以 field_key=source_element_refs 提交修订成功登记来源——清单落库更新（去重升序
    规范化），修订记录含 before/after 且 field_key 可辨。乱序输入被规范化为稳定形。"""
    w, _, read = _formed(session)
    item = read.pending_items[0]
    svc = build_sql_requirement_item_service(session)
    expected = sorted([w["e_func"], w["e_quality"]])
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version,
        field_key="source_element_refs",
        revised_value=json.dumps([w["e_quality"], w["e_func"], w["e_func"]]),  # 乱序 + 重复
        reason="登记正确来源",
    ))
    session.commit()
    assert result.status == "applied" and result.revision_record_ref
    row = session.get(RequirementItem, uuid.UUID(item.item_ref))
    assert json.loads(row.source_element_refs) == expected  # 去重 + 升序规范化落库
    assert row.status == RequirementItemStatus.PENDING_CONFIRMATION.value  # 登记后仍待确认
    rev = session.scalar(select(RequirementItemRevision).where(
        RequirementItemRevision.field_key == "source_element_refs"))
    assert rev is not None  # field_key 可辨
    assert json.loads(rev.after_value) == expected
    assert rev.before_value != rev.after_value  # before/after 皆留痕且确有变更


def test_revision_source_element_refs_gate_rejects_illegal(session):
    """A2：未确认要素、异批次要素、不存在的要素 id 三类非法输入均整体拒绝——
    条目来源与修订记录均保持不变（不落半成品）。"""
    w, _, read = _formed(session)
    w2 = _seed_workspace(session)  # 另一解析批次，供异批次来源用例
    item = read.pending_items[0]
    item_uuid = uuid.UUID(item.item_ref)
    before_refs = session.get(RequirementItem, item_uuid).source_element_refs
    svc = build_sql_requirement_item_service(session)

    with pytest.raises(InvalidInput, match="未确认"):  # 未确认要素
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, read.workspace_version, key="GATE-1",
            field_key="source_element_refs", revised_value=json.dumps([w["e_pending"]])))
    with pytest.raises(InvalidInput, match="批次"):  # 异批次要素
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, read.workspace_version, key="GATE-2",
            field_key="source_element_refs", revised_value=json.dumps([w2["e_func"]])))
    with pytest.raises(InvalidInput, match="批次"):  # 不存在的要素 id
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, read.workspace_version, key="GATE-3",
            field_key="source_element_refs", revised_value=json.dumps([str(uuid.uuid4())])))
    with pytest.raises(InvalidInput):  # 空集拒绝（登记后不得为空）
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, read.workspace_version, key="GATE-4",
            field_key="source_element_refs", revised_value=json.dumps([])))

    assert session.get(RequirementItem, item_uuid).source_element_refs == before_refs  # 来源未变
    assert session.scalar(select(RequirementItemRevision).where(
        RequirementItemRevision.field_key == "source_element_refs")) is None  # 无半成品修订记录


# ---- 条目档案陈述达标投影（增补 §2/§3：P1 直读格式化 LDM-015，非权威不作门禁）----

# ---- 判据驱动 N/A 通道（T20260714-completeness-na-gate；解析层单测）----


def test_format_item_profiles_injects_data_applicability():
    from app.adapters.llm import _format_item_profiles

    text = _format_item_profiles("ears-cn")
    assert "适用性" in text and "not_applicable" in text


def _structure_item(facet_findings):
    return {"element_ref": "e1", "expression": "任务状态枚举：待命、运行中、已完成",
            "statement_conformance": "conforms", "facet_findings": facet_findings}


def test_parse_structure_na_regression_anchor():
    """回归锚：值域/枚举型数据条目——lifecycle_or_volume 判 N/A（带理由）→ 完备度不出缺口。"""
    from app.adapters.llm import _parse_item_structure

    item = _structure_item([
        {"facet": "data_object", "status": "present", "evidence": "任务状态枚举"},
        {"facet": "key_attributes", "status": "present", "evidence": "待命、运行中、已完成"},
        {"facet": "lifecycle_or_volume", "status": "not_applicable", "note": "值域定义无存储维度"},
    ])
    _conf, facets, completeness, _pv, _payload = _parse_item_structure(item, "data", "ears-cn")
    assert {f.facet: f.status for f in facets}["lifecycle_or_volume"] == "not_applicable"
    assert completeness == "complete"


def test_parse_structure_na_without_reason_dropped():
    from app.adapters.llm import _parse_item_structure

    item = _structure_item([
        {"facet": "data_object", "status": "present", "evidence": "任务状态枚举"},
        {"facet": "key_attributes", "status": "present", "evidence": "待命"},
        {"facet": "lifecycle_or_volume", "status": "not_applicable", "note": None},
    ])
    _conf, facets, completeness, _pv, _payload = _parse_item_structure(item, "data", "ears-cn")
    assert "lifecycle_or_volume" not in {f.facet for f in facets}
    assert completeness is None  # 必备面向未全判定


def test_parse_structure_na_rejected_for_undeclared_facet():
    from app.adapters.llm import _parse_item_structure

    item = _structure_item([
        {"facet": "data_object", "status": "not_applicable", "note": "对未声明成分试图判N/A"},
        {"facet": "key_attributes", "status": "present", "evidence": "待命"},
        {"facet": "lifecycle_or_volume", "status": "present", "evidence": "保存5年"},
    ])
    _conf, facets, _completeness, _pv, _payload = _parse_item_structure(item, "data", "ears-cn")
    assert "data_object" not in {f.facet for f in facets}


def test_workspace_projects_structure_review(session):
    w, _svc, read = _formed(session)
    by_type = {i.req_type: i for i in read.pending_items}
    quality = by_type["quality"]
    review = quality.structure_review
    assert review is not None and review.profile_version == 1 and not review.stale
    assert review.completeness == "incomplete"  # stub：首必备 present，其余 missing
    labels = {f.facet_key: f.label for f in review.facets}
    assert labels.get("stimulus") == "刺激"  # label 由服务端档案补齐
    missing = [f for f in review.facets if f.status == "missing"]
    assert missing and all(f.revision_hint for f in missing)  # 缺失面向带修订提示
    present = [f for f in review.facets if f.status == "present"]
    assert present and all(f.evidence for f in present)  # present 必有证据


def test_structure_review_auto_refreshed_after_revision(session):
    """内容修订 → 链式自动结构体检（走查第三轮裁定 2026-07-11）：
    投影锚定新内容修订序号，「修订后未复核」不再作为可见状态残留。"""
    w, form_svc, read = _formed(session)
    item = next(i for i in read.pending_items if i.structure_review is not None)
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version,
        revised_value="修订后的全新表达内容", reason="触发自动体检",
    ))
    session.commit()
    assert result.structure_recheck_run_ref  # 链式体检运行引用回传
    after = form_svc.read_item_formation_workspace(read.formation_context_ref)
    revised = next(i for i in after.pending_items if i.item_ref == item.item_ref)
    assert revised.structure_review is not None and not revised.structure_review.stale


# ---- P2：撰写字段落列 + 达标投影落表 + 说明字段修订（增补 §3/§4）----

def test_formation_writes_notes_and_projection_table(session):
    w, _svc, read = _formed(session)
    item = read.pending_items[0]
    assert item.curation_note and item.boundary_note  # 模型初稿落列（stub）
    rows = session.scalars(select(ItemStructureProjection)).all()
    assert rows and all(r.item_content_rev == 1 for r in rows)  # 形成时版本锚=1
    assert {r.row_kind for r in rows} <= {"facet", "field"} and any(r.row_kind == "facet" for r in rows)
    assert all(r.model_result_ref is not None for r in rows)  # 受控入链：来源 LDM-015


def test_revision_of_boundary_note_chains_auto_recheck(session):
    """撰写说明字段属内容修订：同样链式自动体检、投影随动刷新（不残留过期态）。"""
    w, form_svc, read = _formed(session)
    item = next(i for i in read.pending_items if i.structure_review is not None)
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version, field_key="boundary_note",
        revised_value="仅覆盖导出功能本身，不含权限控制", reason="收窄边界",
    ))
    session.commit()
    assert result.status == "applied" and result.structure_recheck_run_ref
    after = form_svc.read_item_formation_workspace(read.formation_context_ref)
    revised = next(i for i in after.pending_items if i.item_ref == item.item_ref)
    assert revised.boundary_note == "仅覆盖导出功能本身，不含权限控制"
    assert revised.status == RequirementItemStatus.PENDING_CONFIRMATION.value
    assert revised.structure_review is not None and not revised.structure_review.stale  # 自动体检已刷新


def test_unknown_field_key_still_rejected(session):
    w, _form_svc, read = _formed(session)
    item = read.pending_items[0]
    svc = build_sql_requirement_item_service(session)
    with pytest.raises(InvalidInput):
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, read.workspace_version, field_key="status",
            revised_value="confirmed",
        ))


# ============================================================================
# 29148 属性补齐（LDM-007 属性补齐提案，2026-07-06 拍板）：
# 验证方式（多选）/ 验收准则（归纳初稿）/ 优先级（无模型通道）
# ============================================================================

def test_formation_drafts_verification_fields(session):
    """形成初稿：note/method 随 stub 建议落列；priority 无模型通道恒空。"""
    w, _svc, read = _formed(session)
    by_type = {i.req_type: i for i in read.pending_items}
    func, quality = by_type["functional"], by_type["quality"]
    assert func.verification_note and func.verification_method == ["test"]
    assert quality.verification_method == ["analysis", "test"]  # 多选组合建议
    assert func.priority is None and quality.priority is None  # 优先级无模型初稿

    items = session.scalars(select(RequirementItem)).all()
    assert all(i.priority is None for i in items)
    assert {i.verification_method for i in items} == {"test", "analysis,test"}  # 落库逗号连接


def test_stub_formatter_leaves_data_verification_note_empty():
    """"无法归纳为空"通道：stub 对 data 类不产出验收准则（模拟来源无验证线索）。"""
    result = StubRequirementItemFormatter().format_items(
        "P1", "历史订单数据至少保留三年", [{
            "id": "E1", "element_type": "data_requirement",
            "content": "历史订单数据至少保留三年", "req_type": "data",
        }],
    )
    assert result.items[0].verification_note is None
    assert result.items[0].verification_method == ("analysis",)


def test_revision_of_verification_and_priority_with_validation(session):
    w, form_svc, read = _formed(session)
    item = read.pending_items[0]
    svc = build_sql_requirement_item_service(session)

    # 验证方式：多选合法值规范化（去重保序）；非法值拒绝
    r1 = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version, key="RV1",
        field_key="verification_method", revised_value="demonstration, analysis,demonstration",
        reason="工程判断调整",
    ))
    session.commit()
    assert r1.status == "applied"
    with pytest.raises(InvalidInput):
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, r1.workspace_version, key="RV2",
            field_key="verification_method", revised_value="magic",
        ))

    # 优先级：三级枚举；非法值拒绝
    r2 = svc.apply_item_revision(_revision_command(
        w, item.item_ref, r1.workspace_version, key="RV3",
        field_key="priority", revised_value="high", reason="干系人裁量",
    ))
    session.commit()
    assert r2.status == "applied"
    with pytest.raises(InvalidInput):
        svc.apply_item_revision(_revision_command(
            w, item.item_ref, r2.workspace_version, key="RV4",
            field_key="priority", revised_value="urgent",
        ))

    # 验收准则：文本修订
    r3 = svc.apply_item_revision(_revision_command(
        w, item.item_ref, r2.workspace_version, key="RV5",
        field_key="verification_note", revised_value="导出结果可在验收环境打开且内容一致",
    ))
    session.commit()
    assert r3.status == "applied"

    after = form_svc.read_item_formation_workspace(read.formation_context_ref)
    revised = next(i for i in after.pending_items if i.item_ref == item.item_ref)
    assert revised.verification_method == ["demonstration", "analysis"]
    assert revised.priority == "high"
    assert revised.verification_note == "导出结果可在验收环境打开且内容一致"
    # 修订留痕：三条 AEP-036 记录（before 空串留痕）
    keys = {r.field_key for r in revised.revision_records}
    assert {"verification_method", "priority", "verification_note"} <= keys


def test_attribute_revision_keeps_projection_fresh(session):
    """属性字段修订不推进投影版本锚：达标判定不因设优先级而"待重诊"。"""
    w, form_svc, read = _formed(session)
    item = next(i for i in read.pending_items if i.structure_review is not None)
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(_revision_command(
        w, item.item_ref, read.workspace_version, key="RP1",
        field_key="priority", revised_value="medium",
    ))
    session.commit()
    assert result.status == "applied"
    assert result.agent_run_ref is None  # 不触发链式增量诊断
    assert "属性字段" in result.next_action
    after = form_svc.read_item_formation_workspace(read.formation_context_ref)
    revised = next(i for i in after.pending_items if i.item_ref == item.item_ref)
    assert revised.structure_review is not None and not revised.structure_review.stale


def test_confirmed_item_rejects_attribute_revision(session):
    """确认冻结：三个新字段与既有字段同受状态机默认拒绝（存量不回补）。"""
    w, _form_svc, read = _formed(session)
    item = read.pending_items[0]
    row = session.get(RequirementItem, uuid.UUID(item.item_ref))
    row.status = RequirementItemStatus.CONFIRMED.value
    session.commit()
    svc = build_sql_requirement_item_service(session)
    for field_key, value in (
        ("verification_method", "test"),
        ("verification_note", "补写准则"),
        ("priority", "low"),
    ):
        with pytest.raises(RejectedTransition):
            svc.apply_item_revision(_revision_command(
                w, item.item_ref, read.workspace_version, key=f"RF-{field_key}",
                field_key=field_key, revised_value=value,
            ))


# ============================================================================
# AEP-097 区5 对话（命令词确定性解析 / 派发 / 拆分归并 / 来源指认）
# ============================================================================

def _dialogue_command(w, message, version, item_ref=None, formation_context_ref=None,
                      selected=(), key=None):
    from app.api.schemas import FormationDialogueCommand

    return FormationDialogueCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        formation_context_ref=formation_context_ref, workspace_version=version,
        message=message, item_ref=item_ref, selected_element_refs=list(selected),
        operator_ref="U1", idempotency_key=key or f"D-{uuid.uuid4()}",
    )


def test_dialogue_unknown_command_deterministic_receipt(session):
    """未注册命令词：确定性回执，不调模型、不产生任何写入。"""
    w = _seed_workspace(session)
    svc = build_sql_item_formation_service(session)
    result = svc.formation_dialogue(_dialogue_command(w, "/不存在 xxx", "1"))
    assert result.outcome == "unknown_command" and result.command_word == "不存在"
    assert "可用命令" in result.message
    assert session.scalar(select(RequirementItem)) is None


def test_dialogue_version_conflict_rejected(session):
    w = _seed_workspace(session)
    svc = build_sql_item_formation_service(session)
    result = svc.formation_dialogue(_dialogue_command(w, "/生成条目", "99"))
    assert result.outcome == "rejected_precheck" and "版本" in result.message


def test_dialogue_start_itemization_before_batch_context(session):
    """/生成条目 在 formation_context_ref 存在之前可用（body 锚定 parse_result_ref）。"""
    w = _seed_workspace(session)
    svc = build_sql_item_formation_service(session, auto_complete=True)
    result = svc.formation_dialogue(_dialogue_command(w, "/生成条目", "1"))
    session.commit()
    assert result.outcome == "queued" and result.formation_context_ref
    read = svc.read_item_formation_workspace(result.formation_context_ref)
    assert len(read.pending_items) == 2  # 同步装配 inline 收束


def test_dialogue_explain_source_bypasses_interpreter(session):
    """/问来源：确定性来源指认；解释器未装配也可用（不调模型）。"""
    w, svc, read = _formed(session)
    svc._command_interpreter = None  # 证明短路：无解释器仍可回答
    item = next(i for i in read.pending_items if "docx" in i.expression)
    result = svc.formation_dialogue(_dialogue_command(
        w, "/问来源", read.workspace_version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    assert result.outcome == "explanation"
    assert "来源要素" in result.explanation and "「" in result.explanation
    assert "形成依据" in result.explanation


def test_dialogue_revise_req_type_executes_and_returns_workspace(session):
    w, svc, read = _formed(session)
    item = next(i for i in read.pending_items if "docx" in i.expression)
    result = svc.formation_dialogue(_dialogue_command(
        w, "/改类型 改为约束", read.workspace_version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    session.commit()
    assert result.outcome == "executed" and result.operation == "revise.req_type"
    revised = next(i for i in result.workspace.pending_items if i.item_ref == item.item_ref)
    assert revised.req_type.value == "constraint"
    assert any(r.field_key == "req_type" for r in revised.revision_records)  # AEP-036 留痕


def test_dialogue_split_forms_new_items_and_terminates_original(session):
    w, svc, read = _formed(session)
    item = next(i for i in read.pending_items if "docx" in i.expression)
    result = svc.formation_dialogue(_dialogue_command(
        w, "/拆分：\n1. 系统应支持导出 docx 文件\n2. 系统应支持选择导出范围",
        read.workspace_version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    session.commit()
    assert result.outcome == "executed" and len(result.created_item_refs) == 2
    rows = {str(r.id): r for r in session.scalars(select(RequirementItem)).all()}
    assert rows[item.item_ref].status == RequirementItemStatus.TERMINATED.value
    for ref in result.created_item_refs:
        row = rows[ref]
        assert row.status == RequirementItemStatus.PENDING_CONFIRMATION.value
        assert row.formation_basis_ref is None  # 人工形成，无模型形成依据
        assert json.loads(row.source_element_refs) == [w["e_func"]]  # 继承来源集合
    # 工作区版本推进：旧版本再派发被拒
    stale = svc.formation_dialogue(_dialogue_command(
        w, "/问来源", read.workspace_version,
        item_ref=result.created_item_refs[0],
        formation_context_ref=read.formation_context_ref,
    ))
    assert stale.outcome == "rejected_precheck" and "版本" in stale.message


def test_dialogue_split_replay_rejected_by_state_machine(session):
    """重放安全：原条目已终止，再次拆分被状态机默认拒绝。"""
    w, svc, read = _formed(session)
    item = next(i for i in read.pending_items if "docx" in i.expression)
    first = svc.formation_dialogue(_dialogue_command(
        w, "/拆分：\n1. 表达甲\n2. 表达乙", read.workspace_version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    session.commit()
    assert first.outcome == "executed"
    version = svc.read_item_formation_workspace(read.formation_context_ref).workspace_version
    replay = svc.formation_dialogue(_dialogue_command(
        w, "/拆分：\n1. 表达甲\n2. 表达乙", version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    assert replay.outcome == "rejected_precheck"


def test_dialogue_merge_requires_same_type_then_merges(session):
    w, svc, read = _formed(session)
    func_item = next(i for i in read.pending_items if "docx" in i.expression)
    quality_item = next(i for i in read.pending_items if "耗时" in i.expression)
    # 跨类型归并被拒（功能 + 质量）
    rejected = svc.formation_dialogue(_dialogue_command(
        w, f"/归并 「{quality_item.expression[:6]}」归并后表达：合并后的表达",
        read.workspace_version,
        item_ref=func_item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    assert rejected.outcome == "rejected_precheck" and "类型" in rejected.message

    # 先拆分出两条同类型条目，再归并回去
    split = svc.formation_dialogue(_dialogue_command(
        w, "/拆分：\n1. 系统应支持导出 docx 文件\n2. 系统应支持选择导出范围",
        read.workspace_version,
        item_ref=func_item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    session.commit()
    version = svc.read_item_formation_workspace(read.formation_context_ref).workspace_version
    merged = svc.formation_dialogue(_dialogue_command(
        w, "/归并 「系统应支持选择导出范围」归并后表达：系统应支持按选定范围导出 docx 文件",
        version,
        item_ref=split.created_item_refs[0],
        formation_context_ref=read.formation_context_ref,
    ))
    session.commit()
    assert merged.outcome == "executed" and len(merged.created_item_refs) == 1
    rows = {str(r.id): r for r in session.scalars(select(RequirementItem)).all()}
    new_row = rows[merged.created_item_refs[0]]
    assert new_row.status == RequirementItemStatus.PENDING_CONFIRMATION.value
    assert new_row.expression == "系统应支持按选定范围导出 docx 文件"
    assert json.loads(new_row.source_element_refs) == [w["e_func"]]  # 并集（同源去重）
    for ref in split.created_item_refs:
        assert rows[ref].status == RequirementItemStatus.TERMINATED.value


def test_dialogue_freetext_draft_saves_candidate_suggestion(session):
    """自由文本修订动词 → 起草建议卡（候选）；新稿替代旧稿（旧候选过期）。"""
    w, svc, read = _formed(session)
    item = next(i for i in read.pending_items if "docx" in i.expression)
    first = svc.formation_dialogue(_dialogue_command(
        w, "把导出格式改成 docx 与 pdf", read.workspace_version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    session.commit()
    assert first.outcome == "draft" and first.suggestion.status == "candidate"
    second = svc.formation_dialogue(_dialogue_command(
        w, "再加上导出进度提示，改成完整表达", read.workspace_version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    session.commit()
    assert second.outcome == "draft"
    suggestions = [
        s for s in second.workspace.revision_suggestions if s.item_ref == item.item_ref
    ]
    statuses = sorted(s.status for s in suggestions if s.suggestion_ref in
                      (first.suggestion.suggestion_ref, second.suggestion.suggestion_ref))
    assert statuses == ["candidate", "expired"]  # 原位迭代：旧稿过期、新稿候选


def test_dialogue_freetext_question_routes_to_explanation(session):
    w, svc, read = _formed(session)
    item = read.pending_items[0]
    result = svc.formation_dialogue(_dialogue_command(
        w, "为什么这条是功能需求？", read.workspace_version,
        item_ref=item.item_ref, formation_context_ref=read.formation_context_ref,
    ))
    assert result.outcome == "explanation" and result.explanation


# ============================================================================
# 需求规约方案可配置化（选型文档 §5）：批次固定方案 + 投影落列 + AEP-102
# ============================================================================

from app.api.schemas import ConfigSaveCommand  # noqa: E402
from app.db.models import ItemFormationRequest  # noqa: E402
from app.services.config_registry import ConfigRegistryService  # noqa: E402


def _set_active_convention(session, key: str) -> None:
    ConfigRegistryService(session).save_domain(
        "requirement_convention",
        ConfigSaveCommand(values={"active_convention": key}, secrets={}, operator_ref="U1"),
    )
    session.commit()


def test_batch_fixes_active_convention_and_projection_records_it(session):
    w = _seed_workspace(session)
    _set_active_convention(session, "master-cn")
    svc, result = _run_batch(session, w)
    ctx = result.formation_context_ref

    # 批次行固定了发起时的生效方案
    req = session.get(ItemFormationRequest, uuid.UUID(ctx))
    assert req.convention_key == "master-cn"

    # 投影行按批次方案落列（口径锚）
    projs = session.query(ItemStructureProjection).all()
    assert projs and {p.convention_key for p in projs} == {"master-cn"}

    # 工作区区2 徽标读出批次方案名
    ws = svc.read_item_formation_workspace(ctx)
    assert ws.convention_key == "master-cn"
    assert ws.convention_display_name == "中文 MASTeR"

    # 区4 达标条按记录方案渲染：功能条目出现 master-cn 特有 facet interaction_kind
    func_reviews = [
        it.structure_review for it in ws.pending_items
        if it.req_type == "functional" and it.structure_review
    ]
    assert func_reviews
    for rv in func_reviews:
        assert rv.convention_key == "master-cn"
        assert any(f.facet_key == "interaction_kind" for f in rv.facets)


def test_convention_switch_does_not_retroactively_change_existing_projection(session):
    w = _seed_workspace(session)
    _set_active_convention(session, "boilerplate-cn")
    svc, result = _run_batch(session, w)
    ctx = result.formation_context_ref
    # 形成后切换到 master-cn：既有批次/投影不追溯改写
    _set_active_convention(session, "master-cn")
    ws = svc.read_item_formation_workspace(ctx)
    assert ws.convention_key == "boilerplate-cn"
    projs = session.query(ItemStructureProjection).all()
    assert {p.convention_key for p in projs} == {"boilerplate-cn"}
    # 且不因方案切换判过期（过期只由内容修订序号触发）
    for it in ws.pending_items:
        if it.structure_review:
            assert it.structure_review.stale is False


def test_list_requirement_conventions_catalog(session):
    svc = build_sql_item_formation_service(session)
    catalog = svc.list_requirement_conventions()
    assert catalog.active_convention == "ears-cn"  # 无配置=默认
    assert [c.convention_key for c in catalog.conventions] == [
        "ears-cn", "boilerplate-cn", "master-cn",
    ]
    for c in catalog.conventions:
        assert c.display_name and c.positioning and c.blueprint
        assert len(c.pattern_overview) == 5
        assert {e.req_type for e in c.examples} == {
            "functional", "quality", "constraint", "data", "interface",
        }


# ============================================================================
# HK-1 形成批次单飞守卫（幂等普查 G1）：在飞复用 / 死批不挡 / 判活函数
# ============================================================================

def _seed_agent_run(session, formation_context_ref, status="queued", age_seconds=0):
    """挂到批次上下文的 AgentRun（模拟异步在飞/悬死批次；SQLite 存 UTC 裸值）。"""
    from datetime import datetime, timedelta, timezone

    from app.db.models import AgentRun

    run = AgentRun(
        kind="item_formation", status=status,
        context_ref=uuid.UUID(formation_context_ref),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age_seconds),
    )
    session.add(run)
    session.commit()
    return str(run.id)


def _pending_formation(session, w, key="INFLIGHT-1"):
    """未收束批次上下文（auto_complete=False：不执行、不 bump 版本，模拟执行中）。"""
    svc = build_sql_item_formation_service(session, auto_complete=False)
    result = svc.start_element_itemization_batch(_batch_command(w, key=key))
    session.commit()
    assert result.status == "submitted"
    return result.formation_context_ref


@pytest.mark.parametrize("run_status", ["queued", "started"])
def test_second_submit_reuses_inflight_batch(session, run_status):
    """A1：在飞批次存在时二次 submit 返回 in_flight＋原批次 refs，不建新批不新 run。"""
    from app.db.models import AgentRun, ItemFormationRequest

    w = _seed_workspace(session)
    first_ctx = _pending_formation(session, w)
    run_id = _seed_agent_run(session, first_ctx, status=run_status)

    svc = build_sql_item_formation_service(session, auto_complete=True)
    second = svc.start_element_itemization_batch(_batch_command(w, key="ANOTHER-KEY"))
    assert second.status == "in_flight"
    assert second.formation_context_ref == first_ctx  # 复用在途：返回原批次
    assert second.agent_run_ref == run_id             # 原 run 供前端复挂轮询
    assert second.next_action
    assert len(session.scalars(select(ItemFormationRequest)).all()) == 1  # 不建新批
    assert len(session.scalars(select(AgentRun)).all()) == 1              # 不重复入队
    assert session.scalar(select(RequirementItem)) is None                # 无重复条目


def test_inflight_guard_wins_over_version_precheck(session):
    """在飞守卫先于版本预检：携旧版本的重复 submit 也复挂原批次而非报版本冲突。"""
    w = _seed_workspace(session)
    first_ctx = _pending_formation(session, w)
    _seed_agent_run(session, first_ctx, status="started")

    svc = build_sql_item_formation_service(session, auto_complete=True)
    second = svc.start_element_itemization_batch(_batch_command(w, key="K2", version="99"))
    assert second.status == "in_flight"
    assert second.formation_context_ref == first_ctx


def test_stale_inflight_run_does_not_block_new_batch(session):
    """A1：超判死阈值的悬批（僵尸 run）不挡新批——守卫不得变成永久锁。"""
    from app.db.models import ItemFormationRequest
    from app.services.run_liveness import run_liveness_deadline_seconds

    w = _seed_workspace(session)
    first_ctx = _pending_formation(session, w)
    stale_age = run_liveness_deadline_seconds("run_item_formation") + 60
    _seed_agent_run(session, first_ctx, status="started", age_seconds=stale_age)

    svc = build_sql_item_formation_service(session, auto_complete=True)
    second = svc.start_element_itemization_batch(_batch_command(w, key="RETRY-KEY"))
    session.commit()
    assert second.status == "submitted"
    assert second.formation_context_ref != first_ctx  # 新批次
    assert len(session.scalars(select(ItemFormationRequest)).all()) == 2


def test_terminal_run_does_not_trigger_inflight_reuse(session):
    """收束批次（run 已终态）不触发在途复用：无在飞正常建批。"""
    w = _seed_workspace(session)
    first_ctx = _pending_formation(session, w)
    _seed_agent_run(session, first_ctx, status="succeeded")

    svc = build_sql_item_formation_service(session, auto_complete=True)
    second = svc.start_element_itemization_batch(_batch_command(w, key="NEXT-KEY"))
    session.commit()
    assert second.status == "submitted"
    assert second.formation_context_ref != first_ctx


def test_dialogue_start_itemization_reattaches_inflight_batch(session):
    """/生成条目 在飞时按队列支返回原批次 run（前端沿用 watchBatchRun 复挂）。"""
    w = _seed_workspace(session)
    first_ctx = _pending_formation(session, w)
    run_id = _seed_agent_run(session, first_ctx, status="queued")

    svc = build_sql_item_formation_service(session, auto_complete=True)
    result = svc.formation_dialogue(_dialogue_command(w, "/生成条目", "1"))
    assert result.outcome == "queued"
    assert result.formation_context_ref == first_ctx
    assert result.agent_run_ref == run_id
    assert "恢复进度跟踪" in result.message


# ---- 判活函数单测（queued/started × 龄界值；供 HK-2/HK-3 复用的口径锚）----

def test_run_liveness_threshold_is_twice_lane_job_timeout():
    from app.services.run_liveness import run_liveness_deadline_seconds
    from app.workers.queue import job_timeout_for

    assert run_liveness_deadline_seconds("run_item_formation") \
        == 2 * job_timeout_for("run_item_formation") == 3600  # 批次档 1800×2


@pytest.mark.parametrize("run_status", ["queued", "started"])
def test_run_alive_inside_deadline_and_dead_at_boundary(run_status):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.services.run_liveness import is_run_alive, run_liveness_deadline_seconds

    lane = "run_item_formation"
    deadline = run_liveness_deadline_seconds(lane)
    now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)

    fresh = SimpleNamespace(status=run_status, created_at=now - timedelta(seconds=deadline - 1))
    assert is_run_alive(lane, fresh, now=now) is True   # 界值内=在飞
    stale = SimpleNamespace(status=run_status, created_at=now - timedelta(seconds=deadline))
    assert is_run_alive(lane, stale, now=now) is False  # 龄=阈值即判死


def test_run_terminal_status_never_alive():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.services.run_liveness import is_run_alive

    now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    for status in ("succeeded", "failed"):
        run = SimpleNamespace(status=status, created_at=now)
        assert is_run_alive("run_item_formation", run, now=now) is False


def test_run_liveness_naive_created_at_treated_as_utc():
    """SQLite CURRENT_TIMESTAMP 为 UTC 裸值：裸 created_at 按 UTC 解释，不误判死。"""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.services.run_liveness import is_run_alive

    now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    naive_recent = now.replace(tzinfo=None) - timedelta(seconds=30)
    run = SimpleNamespace(status="queued", created_at=naive_recent)
    assert is_run_alive("run_item_formation", run, now=now) is True


# ---- A2 HTTP 层（AEP-038 端点同三态：正常建批 / 在飞复用 / 死批不挡）----

@pytest.fixture()
def http_session():
    # 共享内存库（StaticPool）：TestClient 在 threadpool 线程跑 sync 路由，需跨线程共用同一 DB
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


def _post_batch(client, w, key, version="1"):
    return client.post(
        f"/api/projects/{w['project']}/item-formation/batches",
        json={
            "project_ref": w["project"], "parse_result_ref": w["parse_result"],
            "workspace_version": version, "scope_type": "all_eligible",
            "target_element_refs": [], "operator_ref": "U1", "idempotency_key": key,
        },
    )


def test_http_batch_submit_normal(client, http_session):
    w = _seed_workspace(http_session)
    resp = _post_batch(client, w, key="H1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "submitted" and body["formation_context_ref"]


def test_http_batch_second_submit_returns_in_flight(client, http_session):
    w = _seed_workspace(http_session)
    first_ctx = _pending_formation(http_session, w, key="H-FIRST")
    run_id = _seed_agent_run(http_session, first_ctx, status="started")

    resp = _post_batch(client, w, key="H-SECOND")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_flight"
    assert body["formation_context_ref"] == first_ctx  # 原批次供前端复挂轮询
    assert body["agent_run_ref"] == run_id
    assert body["next_action"]


def test_http_batch_stale_run_not_blocking(client, http_session):
    from app.services.run_liveness import run_liveness_deadline_seconds

    w = _seed_workspace(http_session)
    first_ctx = _pending_formation(http_session, w, key="H-STALE")
    stale_age = run_liveness_deadline_seconds("run_item_formation") + 60
    _seed_agent_run(http_session, first_ctx, status="queued", age_seconds=stale_age)

    resp = _post_batch(client, w, key="H-RETRY")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["formation_context_ref"] != first_ctx  # 僵尸 run 不锁死入口


# ============================================================================
# 起草请求随附结构体检上下文（2026-07-20 走查反馈第⑦组）
#
# 此前起草只传条目四字段＋来源简报＋用户那句意图，界面上已经算好并展示给用户的判定原因、
# 补写示例、句式模板一样都没进提示词，模型只能凭一个成分名自行推导。
# ============================================================================

class _RecordingComposer:
    """记录 compose 收到的入参，不真起草（本组要测的是喂进去什么，不是产出什么）。"""

    def __init__(self):
        self.calls = []

    def compose(self, item, sources, intent, current_draft, structure_context=None):
        from app.adapters.llm import DraftOutcome

        self.calls.append(structure_context)
        return DraftOutcome(proposed_value="起草后的完整表达。", note="")


def _draft_once(session, w, svc, item_ref, version, message="/修订 补写〔响应度量〕："):
    return svc.formation_dialogue(_dialogue_command(
        w, message, version, item_ref=item_ref,
        formation_context_ref=w.get("formation_context"),
    ))


def test_draft_request_carries_structure_check_context(session):
    """待补成分的判定原因与补写示例、条目类型的句式模板都随起草请求送出。"""
    w, svc, read = _formed(session)
    item = next(i for i in read.pending_items if i.structure_review is not None)
    recorder = _RecordingComposer()
    svc._draft_composer = recorder

    _draft_once(session, w, svc, item.item_ref, read.workspace_version)

    assert len(recorder.calls) == 1
    context = recorder.calls[0]
    assert context is not None, "起草请求没带上体检上下文"
    gaps = context["待补成分"]
    assert gaps, "该条目有待补成分，却一条都没送出"
    # 逐条同源同口径：成分名、判定状态、判定原因、补写示例都取自区4 展示的同一份体检结果
    review_missing = {f.label for f in item.structure_review.facets if f.status in ("missing", "ambiguous")}
    assert {g["成分"] for g in gaps} == review_missing
    assert all(g["判定"] in ("missing", "ambiguous") for g in gaps)
    assert any(g["补写示例"] for g in gaps)
    assert context.get("句式模板")


def test_draft_request_omits_stale_structure_context(session):
    """投影过期（条目内容改过、还没重新体检）就不注入——旧判定说的是旧内容，喂进去只会误导。"""
    from app.db.models import ItemStructureProjection

    w, svc, read = _formed(session)
    item = next(i for i in read.pending_items if i.structure_review is not None)
    # 把投影锚到一个落后于当前内容修订序号的值（0 < 现算序号），制造过期态：等价于内容已往前
    # 改、投影还停在旧锚。真实过期必是现算序号 > 锚（修订只追加、现算只增不减）；锚高于现算是
    # 计数规则变更造成的假过期，不算内容变更，故这里必须把锚设低而非设高。
    rows = session.scalars(
        select(ItemStructureProjection).where(ItemStructureProjection.item_ref == uuid.UUID(item.item_ref))
    ).all()
    assert rows
    for row in rows:
        row.item_content_rev = 0
    session.flush()

    recorder = _RecordingComposer()
    svc._draft_composer = recorder
    _draft_once(session, w, svc, item.item_ref, read.workspace_version)

    assert recorder.calls == [None]


def test_formation_draft_payload_marks_its_origin(session):
    """形成页写下的起草交换标明来源页面——评审页据此把它挡在自己的对话历史之外。"""
    w, svc, read = _formed(session)
    item = next(i for i in read.pending_items if i.structure_review is not None)
    svc._draft_composer = _RecordingComposer()
    _draft_once(session, w, svc, item.item_ref, read.workspace_version)

    rows = svc._model_results.stage_payloads_of("item_revision_draft", [item.item_ref])
    assert rows
    assert json.loads(rows[-1].payload)["origin"] == "formation"
