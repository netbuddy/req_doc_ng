"""链式自动增量诊断的守卫与阶段策略解耦 —— T20260714-chained-first-diagnosis-guard
＋ T20260715-stage-policy-p1 测试义务。

设计事实源：SCN-003-P01 页面详细设计（首诊显式＋链式复诊双状态机前置不变式）、
docs/proposals/stage-policy-decoupling/README.md（策略在阶段边缘：链式复诊由评审裁决
采纳动作续接，对象层修订只报告事实）、两卡任务书与方案确认节。

阶段策略解耦 P1 后：对象层 `apply_item_revision` 不再经 on_revised 无差别触发链式复诊，
只写事实并发布 ItemRevised 事件；链式增量诊断迁回评审服务 `_adopt_revise`（裁决采纳
revise 结论）显式续接。故本文件按驱动路径分层：
  - 直发/形成语境（AEP-036 直发、区5 对话人工修订）→ **不链**（无论有无诊断史）；
  - 评审裁决采纳 revise 结论 → 经 `adjudicate_verdict → _adopt_revise` **续接链式复诊**；
  - 守卫与计数谓词由 `start_chained_incremental` 内部裁决，直接或经真实采纳路径覆盖。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.api.schemas import (
    ItemizationBatchCommand,
    ItemReviewDiagnosisCommand,
    ItemRevisionCommand,
    VerdictAdjudicationCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    IntakeRecord,
    ItemDiagnosisRequest,
    ItemDiagnosisRound,
    Material,
    MaterialParseResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
)
from app.domain.enums import (
    DiagnosisTrigger,
    ItemizationScopeType,
    ItemRevisionMode,
    VerdictDecision,
    VerdictKind,
)
from app.repositories.sqlalchemy import (
    build_sql_item_formation_service,
    build_sql_item_review_service,
    build_sql_requirement_item_service,
)
from app.services.item_review import _REPEATED_REVISE_HINT_AT

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
    """已接入材料 + 已解析结果 + 两条已确认可形成要素。"""
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

    element("functional_requirement", "系统应支持导出 docx")
    element("quality_attribute", "导出耗时不超过五秒")
    session.commit()
    return {"project": str(p.id), "parse_context": str(ctx.id), "parse_result": str(parse.id)}


def _formed(session):
    """发起形成批次（stub 格式化：两条待确认条目，均无诊断史）。"""
    svc = build_sql_item_formation_service(session, auto_complete=True)
    w = _seed_workspace(session)
    result = svc.start_element_itemization_batch(ItemizationBatchCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        workspace_version="1", scope_type=ItemizationScopeType.ALL_ELIGIBLE,
        target_element_refs=[], operator_ref="U1", idempotency_key=f"B-{uuid.uuid4()}",
    ))
    session.commit()
    read = svc.read_item_formation_workspace(result.formation_context_ref)
    return w, svc, read


def _rounds(session, item_ref):
    """该条目全部轮次（旧→新）。"""
    return session.scalars(
        select(ItemDiagnosisRound)
        .where(ItemDiagnosisRound.item_ref == uuid.UUID(item_ref))
        .order_by(ItemDiagnosisRound.round_no)
    ).all()


def _current_version(session, w) -> str:
    """当前工作区版本（每次修订后自增；采纳前须读最新，否则撞版本冲突）。"""
    return str(session.get(ParseRequest, uuid.UUID(w["parse_context"])).workspace_version)


def _revise(session, w, item_ref, version, value="修订后的全新表达内容"):
    """AEP-036 直发内容修订（对象层完整装配）。

    阶段策略解耦 P1 后：直发路径**不再链式复诊**——只写事实、发布 ItemRevised 事件；
    故本助手返回的 agent_run_ref 恒为 None，轮次不因直发而新增。
    """
    svc = build_sql_requirement_item_service(session)
    result = svc.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=version,
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value=value, suggestion_ref=None, reason="走查修订",
        operator_ref="U1", idempotency_key=f"R-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "applied"
    return result


class _AlwaysReviseDiagnoser:
    """持续判「建议修订」的 stub 诊断器（驱动真实采纳链）。

    修订点 = 把整段当前表达替换为「表达+一字符」：整段 find 恒唯一可定位、恒有变化，
    应用后表达仍不含任何通过标记 → 下一轮仍判 revise，从而让每次采纳都触发下一轮 revise。
    """

    def diagnose(
        self, project_ref, diagnosis_mode, item, sources, raw_text, revisions,
        prior_findings, excluded_points=None, thread_context="", business_sources=None, attestation=None,
    ):
        from app.adapters.llm import DiagnosedFinding, ItemVerdictOutcome
        expr = str(item.get("expression") or "")
        return ItemVerdictOutcome(
            verdict_kind="revise",
            verdict_summary="表达仍缺可验证口径，建议继续修订（stub 持续 revise）。",
            findings=(DiagnosedFinding(
                "untestable", "当前表达缺少可验证口径。", "stub 持续 revise"),),
            revision_points=({
                "point_ref": "P1", "label": "追加可验证口径占位", "finding_index": 0,
                "find": expr, "replace": expr + "·",
                "basis": "stub 持续 revise", "group": None,
            },),
            supplement_gaps=(), basis="stub 条目诊断完成",
        )


def _revise_review(session):
    """持续判 revise 的评审服务（真实采纳链驱动器）。"""
    return build_sql_item_review_service(
        session, auto_complete=True, item_diagnoser=_AlwaysReviseDiagnoser())


def _user_diagnose(session, review, w, item_ref):
    """用户在评审页显式发起首诊（USER_SUBMIT；驱动器给出首轮 revise 结论）。"""
    result = review.start_item_diagnosis(ItemReviewDiagnosisCommand(
        project_ref=w["project"], item_refs=[item_ref], diagnosis_mode="standard",
        workspace_version=_current_version(session, w), operator_ref="U1",
        idempotency_key=f"D-{uuid.uuid4()}",
    ))
    session.commit()
    assert result.status == "submitted"
    return result


def _standing_round_ref(review, formation_context_ref, item_ref):
    """该条目当前待裁决的站立结论轮次引用（无则 None）。"""
    workspace = review.read_item_review_workspace(formation_context_ref)
    view = next(i for i in workspace.review_items if i.item_ref == item_ref)
    return view.current_verdict.round_ref if view.current_verdict else None


def _adopt_standing_revise(session, review, w, read, item_ref, capture=None):
    """采纳当前站立的 revise 结论（真实采纳路径 → _adopt_revise → 显式续接链式复诊）。

    capture 非空时，把该次采纳内部 revision_applier 的修订结果（含 agent_run_ref /
    next_action / structure_recheck_run_ref）追加进列表——adjudicate_verdict 不外传该回执。
    """
    round_ref = _standing_round_ref(review, read.formation_context_ref, item_ref)
    assert round_ref is not None, "应有待裁决的 revise 结论可采纳"
    if capture is not None:
        real = review.revision_applier

        def _cap(cmd, **kw):
            r = real(cmd, **kw)
            capture.append(r)
            return r

        review.revision_applier = _cap
    try:
        review.adjudicate_verdict(VerdictAdjudicationCommand(
            project_ref=w["project"], item_ref=item_ref, round_ref=round_ref,
            decision=VerdictDecision.ADOPTED, selected_point_refs=None, reason=None,
            workspace_version=_current_version(session, w),
            operator_ref="U1", idempotency_key=f"adj-{uuid.uuid4()}",
        ))
        session.commit()
    finally:
        if capture is not None:
            review.revision_applier = real


# ============================================================================
# A1 无史守卫：形成语境两路径修订后零新轮次（且不再链式复诊）
# ============================================================================

def test_no_history_direct_revision_creates_no_round(session):
    """AEP-036 直发：从未诊断的条目修订后不得凭空产生首轮结论。

    阶段策略解耦后直发路径根本不触发链式复诊（对象层只发事件），故无轮次、agent_run_ref 空。
    """
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    assert _rounds(session, item_ref) == []

    result = _revise(session, w, item_ref, read.workspace_version)

    assert _rounds(session, item_ref) == []
    assert result.agent_run_ref is None


def test_no_history_suggestion_adoption_creates_no_round(session):
    """区5 对话 /修订 起草 → 建议卡采纳：与直发同一写权威，同样不产轮次。"""
    from app.api.schemas import FormationDialogueCommand

    w, svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref

    dialogue = svc.formation_dialogue(FormationDialogueCommand(
        project_ref=w["project"], parse_result_ref=w["parse_result"],
        formation_context_ref=read.formation_context_ref,
        workspace_version=read.workspace_version,
        message="/修订：改为「系统应在五秒内导出 docx 文件」", item_ref=item_ref,
        selected_element_refs=[], operator_ref="U1", idempotency_key=f"D-{uuid.uuid4()}",
    ))
    session.commit()
    assert dialogue.outcome == "draft" and dialogue.suggestion is not None

    # 采纳候选建议卡即内容修订落库（mode=accept_suggestion 分支）
    item_svc = build_sql_requirement_item_service(session)
    result = item_svc.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=read.workspace_version,
        revision_mode=ItemRevisionMode.ACCEPT_SUGGESTION, field_key="expression",
        revised_value=dialogue.suggestion.proposed_value,
        suggestion_ref=dialogue.suggestion.suggestion_ref, reason="采纳对话候选",
        operator_ref="U1", idempotency_key=f"R-{uuid.uuid4()}",
    ))
    session.commit()

    assert result.status == "applied"
    assert result.agent_run_ref is None
    assert _rounds(session, item_ref) == []


# ============================================================================
# A3 阶段策略解耦：直发（非采纳）修订不链，评审裁决采纳 revise 结论才链
# （T20260715-stage-policy-p1 核心验收）
# ============================================================================

def test_direct_revision_of_diagnosed_item_does_not_chain(session):
    """有诊断史的条目经 AEP-036 直发（非采纳路径）修订 → **不触发**链式复诊。

    解耦前此路会链（on_revised 无差别触发）；解耦后链式复诊只属评审裁决采纳动作，
    直发修订仅写事实、发布 ItemRevised 事件。钉住「策略在阶段边缘」。
    """
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)  # 造出用户发起诊断史（首轮 revise）

    before = _rounds(session, item_ref)
    assert len(before) == 1 and before[0].trigger == DiagnosisTrigger.USER_SUBMIT.value

    result = _revise(session, w, item_ref, _current_version(session, w))

    # 直发修订不链：轮次不新增、agent_run_ref 空、首轮随内容修订失效（旧结论失效仍照常）
    assert len(_rounds(session, item_ref)) == 1
    assert result.agent_run_ref is None
    assert _rounds(session, item_ref)[0].invalidated is True


def test_adopt_revise_verdict_chains_incremental(session):
    """评审裁决采纳 revise 结论 → 经 _adopt_revise 显式续接链式增量诊断。

    前提=条目待确认且有用户发起诊断史；操作=采纳 revise 裁决；预期=链式复诊被续接
    （REVISION_CHAINED 新轮次，幂等键 chain:<revision_ref>；四态回执之 submitted）。
    """
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)

    before = _rounds(session, item_ref)
    assert len(before) == 1 and before[0].trigger == DiagnosisTrigger.USER_SUBMIT.value

    captured = []
    _adopt_standing_revise(session, review, w, read, item_ref, capture=captured)

    after = _rounds(session, item_ref)
    assert len(after) == 2
    assert after[1].trigger == DiagnosisTrigger.REVISION_CHAINED.value
    # 链式轮次的前置首诊轮在续接前已被置失效——"含已失效算有史"是守卫的刚性前置
    assert after[0].invalidated is True
    revision_ref = captured[-1].revision_record_ref
    batch = session.get(ItemDiagnosisRequest, after[1].batch_ref)
    assert batch.idempotency_key == f"chain:{revision_ref}"


def test_repeated_adopt_keeps_chaining(session):
    """连续采纳：历史全为已失效轮次时，采纳链仍必须续接下一轮复诊（回归有史链保留）。"""
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)

    _adopt_standing_revise(session, review, w, read, item_ref)
    assert len(_rounds(session, item_ref)) == 2

    _adopt_standing_revise(session, review, w, read, item_ref)

    rounds = _rounds(session, item_ref)
    assert len(rounds) == 3
    assert rounds[2].trigger == DiagnosisTrigger.REVISION_CHAINED.value


# ============================================================================
# A′ 谓词：链式轮次不自证历史（存量凭空首诊自愈；直接叩 start_chained_incremental）
# ============================================================================

def test_phantom_chained_only_history_does_not_chain(session):
    """存量缺陷形态（唯一轮次即 revision_chained 首轮）不得被当作诊断史。

    库中实证形态：test 项目 REQ-004/010 单轮 round_no=1 且 trigger=revision_chained。
    该形态证明的恰是"用户从未要求过诊断"，守卫据此自愈，无需数据迁移。
    直接叩评审服务 start_chained_incremental（守卫落点），验证不产链式轮。
    """
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)

    # 把唯一轮次改写成链式首轮，复现存量缺陷行形态
    phantom = _rounds(session, item_ref)[0]
    phantom.trigger = DiagnosisTrigger.REVISION_CHAINED.value
    session.commit()

    outcome = review.start_chained_incremental(item_ref, str(uuid.uuid4()), "U1")
    session.commit()

    assert outcome.status == "skipped_no_history"
    assert len(_rounds(session, item_ref)) == 1  # 零新增：假历史不喂链


def test_invalidated_user_round_still_counts_as_history(session):
    """已失效的用户发起轮次仍算有史 → start_chained_incremental 照常续接链式轮。"""
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)

    round_ = _rounds(session, item_ref)[0]
    round_.invalidated = True
    round_.invalidated_reason = "先行失效"
    session.commit()

    outcome = review.start_chained_incremental(item_ref, str(uuid.uuid4()), "U1")
    session.commit()

    assert outcome.status == "submitted"
    rounds = _rounds(session, item_ref)
    assert len(rounds) == 2 and rounds[1].trigger == DiagnosisTrigger.REVISION_CHAINED.value


# ============================================================================
# A3 存量修复脚本：判据收窄（误伤保护）＋幂等（历史经真实采纳链构造）
# ============================================================================

def test_repair_script_targets_phantom_only_and_is_idempotent(session):
    """脚本谓词收敛到守卫谓词：无用户诊断史却有在世轮次者命中，失效其**全部**在世轮；
    正常采纳链（含 user_submit 轮）永不误伤；多轮凭空形态（首轮已失效＋次轮在世）也被彻底修复。"""
    from app.scripts.repair_phantom_first_diagnosis import (
        find_phantom_items,
        invalidate_phantom_rounds,
    )

    review = _revise_review(session)
    w, _svc, read = _formed(session)
    legit_ref = read.pending_items[0].item_ref
    phantom_ref = read.pending_items[1].item_ref

    # 两条目均先用户首诊（user_submit）
    _user_diagnose(session, review, w, legit_ref)
    _user_diagnose(session, review, w, phantom_ref)

    # 多轮凭空形态：采纳 phantom 一次 → round2(chained)、round1 失效，再把 round1 也改成
    # chained → 该条目全链皆链式、无任何用户发起轮，round1 已失效、round2 在世。
    _adopt_standing_revise(session, review, w, read, phantom_ref)
    p_rounds = _rounds(session, phantom_ref)
    assert len(p_rounds) == 2 and p_rounds[0].invalidated is True  # round1 已失效
    p_rounds[0].trigger = DiagnosisTrigger.REVISION_CHAINED.value
    session.commit()

    # 正常链：采纳 legit → round2 revision_chained（round1 user_submit 被失效，但仍是用户史）
    _adopt_standing_revise(session, review, w, read, legit_ref)

    hits = find_phantom_items(session)
    assert [str(item_ref) for item_ref, *_ in hits] == [phantom_ref]  # 只命中凭空条目
    assert hits[0][3] == 1  # 在世凭空轮计数=1（round1 已失效不计，命中的是 round2）

    # 写路径回路：find → invalidate → find == []（幂等由"无在世轮可失效"自然得出）
    invalidate_phantom_rounds(session)
    session.commit()
    assert find_phantom_items(session) == []
    assert all(r.invalidated for r in _rounds(session, phantom_ref))  # 全部在世轮已失效

    # 误伤保护：正常采纳链的 round2 未被触碰，仍在世
    legit_rounds = _rounds(session, legit_ref)
    assert len(legit_rounds) == 2
    assert legit_rounds[1].trigger == DiagnosisTrigger.REVISION_CHAINED.value
    assert legit_rounds[1].invalidated is False


def test_repaired_phantom_item_displays_pending_diagnosis(session):
    """存量修复后的评审页显示态：作废凭空首诊 → 条目回到「待诊断」（A3 读侧口径）。

    复现库中 test 项目 REQ-004/010 修复后的行内形态（唯一轮次=链式首轮且已失效）。
    """
    from app.domain.enums import ReviewDisplayCode

    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)

    # 凭空首诊 + 脚本作废后的存量行形态
    phantom = _rounds(session, item_ref)[0]
    phantom.trigger = DiagnosisTrigger.REVISION_CHAINED.value
    phantom.invalidated = True
    phantom.invalidated_reason = "形成阶段误产首诊，按守卫修复随动作废"
    session.commit()

    workspace = review.read_item_review_workspace(read.formation_context_ref)
    projected = next(i for i in workspace.review_items if i.item_ref == item_ref)

    assert projected.display_code == ReviewDisplayCode.PENDING_DIAGNOSIS  # 不再「待裁决·补/修」
    assert projected.current_verdict is None  # 失效轮不得再充当现行结论


# ============================================================================
# A5 漏斗回显：无史条目区5 修订后回执不得谎报「已触发链式增量诊断」（F1）
# 解耦后：直发/对话修订根本不触发链式复诊，回执自然不含诊断措辞（更强的不谎报保证）。
# ============================================================================

def test_dialogue_no_history_revision_does_not_claim_diagnosis(session):
    """F1：评审页对话 /修订 一条从未诊断的条目 → 回执不得谎报触发了链式增量诊断。

    解耦后对话人工修订走对象层直发（非采纳路径），根本不链式复诊；回执只陈述修订已应用，
    不写「已触发链式增量诊断」，且轮次不新增（用户不会等一个永不到来的结论）。
    """
    from app.api.schemas import ReviewDialogueCommand
    from app.repositories.sqlalchemy import build_sql_item_review_service

    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    assert _rounds(session, item_ref) == []

    review = build_sql_item_review_service(session, auto_complete=True)
    r = review.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=item_ref,
        message="/修订 把当前条目的表达修订为：系统应在五秒内导出 docx 文件",
        workspace_version=read.workspace_version,
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()

    assert r.operation == "manual_revision"
    assert "链式增量诊断" not in (r.message or "")  # 不谎报触发了链式诊断
    assert _rounds(session, item_ref) == []         # 直发修订不产链式轮


# ============================================================================
# A4 谓词白名单穷举：新增 trigger 枚举值默认不算诊断史（失败关闭）
# ============================================================================

def test_diagnosis_trigger_whitelist_is_exhaustive(session):
    """守卫按白名单 {user_submit, dialogue_reeval} 判有史；每个 trigger 成员显式归类。

    新增 DiagnosisTrigger 成员时 `set(expected) == set(DiagnosisTrigger)` 必红，
    迫使把它显式归类——凭空首诊的洞不会因新枚举值静默重开（详设「采纳副作用链」行不变式）。
    """
    from app.repositories.sqlalchemy import SqlItemReviewRepository

    expected = {
        DiagnosisTrigger.USER_SUBMIT: True,       # 用户显式发起 → 算诊断史
        DiagnosisTrigger.DIALOGUE_REEVAL: True,   # 对话重评（要求已有站立结论）→ 算诊断史
        DiagnosisTrigger.REVISION_CHAINED: False,  # 修订后链式增量 → 不自证历史
    }
    assert set(expected) == set(DiagnosisTrigger)  # 穷举守卫：新成员未归类即红

    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)
    round_ = _rounds(session, item_ref)[0]

    reviews = SqlItemReviewRepository(session)
    for trigger, is_history in expected.items():
        round_.trigger = trigger.value
        session.commit()
        assert reviews.has_user_initiated_round(item_ref) is is_history
    # 注：NULL trigger 的 coalesce 兜底针对历史裸行；现模型 trigger NOT NULL，无法经 ORM 造 NULL。


# ============================================================================
# 修订往复无上限（2026-07-20 用户拍板废除原「采纳链空转熔断」）
#
# 评审是「AI 提建议 → 用户给反馈」的往复过程，终点只有两个：AI 判通过，或人工撤回该条目。
# 什么时候不值得再改，是用户的判断，不是机器数出来的。原来那道闸也没真停住什么——它只掐
# 自动链，用户照样能手动发起诊断，代价却是条目莫名掉回「待诊断」。
# 采纳次数保留为**只读事实**（提示用），不再是任何流程的前置。
# ============================================================================

def _capture_review_events(monkeypatch):
    """评审服务模块 log_event 事件流。"""
    import app.services.item_review as mod

    events = []
    real = mod.log_event

    def spy(component, event, msg="", level="INFO", **fields):
        events.append({"event": event, "level": level, **fields})
        return real(component, event, msg, level, **fields)

    monkeypatch.setattr(mod, "log_event", spy)
    return events


def test_revise_chain_has_no_round_limit(session, monkeypatch):
    """连采多次「建议修订」，每一次都照常续接下一轮复诊——往复不设上限（命题本身，非「上限≥5」）。

    钉住废除熔断这件事本身：原实现在第 3 次采纳当刻停发自动链（轮次数就此冻在 3）。采纳次数随
    提示阈值派生（HINT_AT*2+1），远超任何「把闸装回到提示阈值附近」的取值；断言轮次数 = 采纳数+1，
    这样闸只要在这个区间内的任意一次被装回来（无论阈值设成几）都会让这里少一轮而转红。
    （旧断言写死「轮次数 == 6」——把闸阈值改成 10 时 5 例仍全绿，等于只验证到「上限≥5」。）
    """
    events = _capture_review_events(monkeypatch)
    review = _revise_review(session)
    w, _fsvc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)

    adoptions = _REPEATED_REVISE_HINT_AT * 2 + 1
    for _ in range(adoptions):
        _adopt_standing_revise(session, review, w, read, item_ref)

    rounds = _rounds(session, item_ref)
    assert len(rounds) == adoptions + 1, "首诊 1 轮 + 每次采纳各续 1 轮；任何一次被停发都会让这里少一轮"
    assert all(r.trigger == DiagnosisTrigger.REVISION_CHAINED.value for r in rounds[1:])
    assert not [e for e in events if e["event"] == "review.chained.skipped_no_convergence"]


def test_repeated_revise_only_logs_and_hints_never_blocks(session, monkeypatch):
    """往复多次只留痕、只提示：日志记事实，读投影给次数，流程一步不拦。

    关键断言（C28）：不再拿「disabled in ([], ['request_diagnosis'])」这个恒走宽松分支的写法——
    它把一个与往复次数无关的既有行为（有站立结论时 request_diagnosis 本就禁用）焊进了断言，
    等于没验证。改为比较「同一待裁决态、采纳 0 次 vs 采纳 4 次」两组 affordance 是否逐一相等：
    只有「因往复次数而变的入口开关」才会让这条红。
    """
    events = _capture_review_events(monkeypatch)
    review = _revise_review(session)
    w, _fsvc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)

    def _item():
        return next(
            i for i in review.read_item_review_workspace(read.formation_context_ref).review_items
            if i.item_ref == item_ref
        )

    # 采纳 0 次的待裁决态作为基线
    base_item = _item()
    assert base_item.adopted_revise_rounds == 0
    base_affordances = {(a.key, a.enabled) for a in base_item.available_actions}

    for _ in range(4):
        _adopt_standing_revise(session, review, w, read, item_ref)

    # 事实留痕：达到提示阈值后逐次记录，但 ok=True——它不是失败，只是值得注意
    hints = [e for e in events if e["event"] == "review.chained.repeated_revise"]
    assert hints and all(e["ok"] is True for e in hints)
    assert hints[-1]["adopted_revise_rounds"] >= _REPEATED_REVISE_HINT_AT

    item = _item()
    # 次数如实投影给界面；条目仍在正常回环里（诊断中/待裁决），没有被打回「待诊断」
    assert item.adopted_revise_rounds >= _REPEATED_REVISE_HINT_AT
    assert item.display_code.value == base_item.display_code.value == "awaiting_adjudication"
    # 往复次数不改变任何评审入口的可用性：两组 (key, enabled) 集合必须逐一相等
    after_affordances = {(a.key, a.enabled) for a in item.available_actions}
    assert after_affordances == base_affordances, "往复次数不得改变任何评审入口的可用性"


def _mark_all_adopted_revise(session, item_ref):
    """把该条目全部现有轮次记为「建议修订·已采纳」（构造采纳计数史）。"""
    for r in _rounds(session, item_ref):
        r.verdict_kind = VerdictKind.REVISE.value
        r.adjudication_decision = VerdictDecision.ADOPTED.value
    session.commit()


def test_repeated_revise_count_includes_invalidated_rounds(session):
    """计数含失效轮：链式前置必然全失效，排除失效会让这个数永远是 0，提示形同虚设。"""
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)
    for _ in range(2):
        _adopt_standing_revise(session, review, w, read, item_ref)
    _mark_all_adopted_revise(session, item_ref)
    for r in _rounds(session, item_ref):
        r.invalidated = True
    session.commit()

    item = next(
        i for i in review.read_item_review_workspace(read.formation_context_ref).review_items
        if i.item_ref == item_ref
    )
    assert item.adopted_revise_rounds == 3, "失效轮未计入 → 提示永不出现"


def test_repeated_revise_count_ignores_adopted_non_revise_rounds(session):
    """只有「已采纳的 revise 轮」计入：混进一条 adopted 的 pass 轮不得被算作一次往复。"""
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)
    for _ in range(2):
        _adopt_standing_revise(session, review, w, read, item_ref)
    rounds = _rounds(session, item_ref)
    assert len(rounds) == 3
    for r, kind in zip(rounds, [VerdictKind.REVISE, VerdictKind.PASS, VerdictKind.REVISE]):
        r.verdict_kind = kind.value
        r.adjudication_decision = VerdictDecision.ADOPTED.value
    session.commit()

    item = next(
        i for i in review.read_item_review_workspace(read.formation_context_ref).review_items
        if i.item_ref == item_ref
    )
    assert item.adopted_revise_rounds == 2, "pass-adopted 轮被误计入"


def test_manual_diagnosis_always_available(session):
    """手动发起诊断这条出口恒在，往复多少次都不受影响。"""
    review = _revise_review(session)
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    _user_diagnose(session, review, w, item_ref)
    for _ in range(4):
        _adopt_standing_revise(session, review, w, read, item_ref)
    before = len(_rounds(session, item_ref))

    manual = _user_diagnose(session, review, w, item_ref)

    assert manual.status == "submitted"
    assert len(_rounds(session, item_ref)) == before + 1


# ============================================================================
# 领域事件骨架 + 装配解耦（T20260715-stage-policy-p1：对象层只报告事实；issue #23）
# ============================================================================

def _apply_direct(session, svc, w, item_ref, version, *, value="内容确有变化的新表达", origin=None):
    cmd = ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=version,
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value=value, reason="事件测试",
        operator_ref="U1", idempotency_key=f"R-{uuid.uuid4()}",
    )
    result = svc.apply_item_revision(cmd) if origin is None else svc.apply_item_revision(cmd, origin=origin)
    session.commit()
    return result


def test_apply_item_revision_publishes_item_revised(session):
    """对象层内容修订落库后发布 ItemRevised 事件（item_ref/revision_ref/origin 齐备，缺省 origin=direct）。"""
    from app.events import ItemRevised

    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    svc = build_sql_requirement_item_service(session)
    collected = []
    svc._events.subscribe(collected.append)

    result = _apply_direct(session, svc, w, item_ref, read.workspace_version)

    assert result.status == "applied"
    assert len(collected) == 1
    ev = collected[0]
    assert isinstance(ev, ItemRevised)
    assert ev.item_ref == item_ref
    assert ev.revision_ref == result.revision_record_ref
    assert ev.origin == "direct"


def test_apply_item_revision_threads_review_adoption_origin(session):
    """origin 关键字随事件外发——评审采纳路径传 review_adoption，供未来订阅者按阶段分流。"""
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    svc = build_sql_requirement_item_service(session)
    collected = []
    svc._events.subscribe(collected.append)

    _apply_direct(session, svc, w, item_ref, read.workspace_version, origin="review_adoption")

    assert len(collected) == 1
    assert collected[0].origin == "review_adoption"


def test_attribute_only_revision_publishes_no_event(session):
    """属性字段修订（不失效诊断、非内容变更）不发 ItemRevised——事件只报内容修订事实。"""
    w, _svc, read = _formed(session)
    item_ref = read.pending_items[0].item_ref
    svc = build_sql_requirement_item_service(session)
    collected = []
    svc._events.subscribe(collected.append)

    result = svc.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=read.workspace_version,
        revision_mode=ItemRevisionMode.MANUAL, field_key="priority",
        revised_value="high", reason="属性修订",
        operator_ref="U1", idempotency_key=f"R-{uuid.uuid4()}",
    ))
    session.commit()

    assert result.status == "applied"
    assert collected == []


def test_no_assembly_point_binds_chain_on_revision(session):
    """五处装配点全部摘除 on_revised → start_chained_incremental 绑定（issue #23 消化）。

    构造器已删去 on_revised 形参——任一装配点若再想绑链式回环，构造期即 TypeError（覆盖
    deps.py 三处 provider）。两处可直接叩的装配入口（build_sql_requirement_item_service、
    build_sql_item_review_service 内的采纳承接方）产出的对象层服务无链式钩子、且持有事件发布器。
    """
    import inspect

    from app.events import DomainEventPublisher
    from app.services.item_formation import RequirementItemService

    params = inspect.signature(RequirementItemService.__init__).parameters
    assert "on_revised" not in params  # 形参已删：装配点无法再绑链
    assert "events" in params

    direct = build_sql_requirement_item_service(session)
    assert not hasattr(direct, "on_revised")
    assert isinstance(direct._events, DomainEventPublisher)

    review = build_sql_item_review_service(session, auto_complete=True)
    applier_self = review.revision_applier.__self__  # 采纳承接方回指其 RequirementItemService
    assert not hasattr(applier_self, "on_revised")
    assert isinstance(applier_self._events, DomainEventPublisher)
