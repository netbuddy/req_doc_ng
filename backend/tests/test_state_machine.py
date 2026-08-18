"""材料接入状态机契约：迁移 + 默认拒绝（domains/DS-001/state-machines/材料接入.md）。"""
import pytest

from app.api.schemas import IntakeJudgementResultCommand, TextIntakeCommand
from app.domain.enums import IntakeConclusion, ModelJudgement
from app.domain.errors import RejectedTransition
from app.domain.enums import ElementProcessStatus as ES
from app.domain.state_machine import (
    ELEMENT_TRANSITIONS,
    ElementEvent,
    IntakeEvent,
    IntakeState,
    ParseEvent,
    ParseState,
    default_reject_pairs,
    element_transition,
    listed_pairs,
    parse_default_reject_pairs,
    parse_listed_pairs,
)
from app.repositories.in_memory import build_wiring


def _wiring():
    return build_wiring(auto_complete=False, selected_projects={"PRJ-1"})


def _submit(w, key="K1"):
    return w.service.submit_text_intake(
        TextIntakeCommand(
            project_ref="PRJ-1",
            raw_text="系统应支持导出 docx。",
            source_note="访谈",
            operator_ref="U1",
            idempotency_key=key,
        )
    )


def _accept(w, context, judgement, mrr="MR-1"):
    w.model_results.seed_judgement(mrr, judgement)
    return w.service.accept_intake_judgement_result(
        IntakeJudgementResultCommand(
            model_result_ref=mrr,
            intake_context_ref=context,
            operator_ref="U1",
            idempotency_key="A1",
        )
    )


def test_listed_pairs_count():
    assert len(listed_pairs()) == 3


def test_default_reject_set_size_and_members():
    pairs = default_reject_pairs()
    assert len(pairs) == len(IntakeState) * len(IntakeEvent) - len(listed_pairs())
    assert (IntakeState.INITIAL, IntakeEvent.ACCEPT_RESULT) in pairs
    assert (IntakeState.ACCEPTED, IntakeEvent.SUBMIT) in pairs


def test_accept_from_initial_is_rejected():
    w = _wiring()
    with pytest.raises(RejectedTransition):
        _accept(w, "CTX-NONE", ModelJudgement.ACCEPTABLE)


@pytest.mark.parametrize(
    "conclusion",
    [IntakeConclusion.ACCEPTED, IntakeConclusion.EXCLUDED, IntakeConclusion.RETURNED_FOR_SUPPLEMENT],
)
def test_accept_from_concluded_states_is_rejected(conclusion):
    w = _wiring()
    ctx = _submit(w).context_ref
    w.source_assets.seed_conclusion(ctx, conclusion)
    with pytest.raises(RejectedTransition):
        _accept(w, ctx, ModelJudgement.ACCEPTABLE)


def test_transitions_table_reaches_declared_states():
    # 正向迁移：ACCEPT 三分支
    for judgement, expected in [
        (ModelJudgement.ACCEPTABLE, IntakeConclusion.ACCEPTED),
        (ModelJudgement.INSUFFICIENT_CONTENT, IntakeConclusion.RETURNED_FOR_SUPPLEMENT),
        (ModelJudgement.NO_ASSET_VALUE, IntakeConclusion.EXCLUDED),
    ]:
        w = _wiring()
        ctx = _submit(w).context_ref
        _accept(w, ctx, judgement)
        assert w.source_assets.conclusion_of(ctx) is expected


# ---- 材料解析状态机（LDM-004，SCN-001-P02）----

def test_parse_listed_pairs_count():
    # 去重后 2 组：(INITIAL,START_RECOGNITION)、(PARSING,ACCEPT_RESULT)
    assert len(parse_listed_pairs()) == 2


def test_parse_default_reject_set():
    pairs = parse_default_reject_pairs()
    assert len(pairs) == len(ParseState) * len(ParseEvent) - len(parse_listed_pairs())
    assert (ParseState.INITIAL, ParseEvent.ACCEPT_RESULT) in pairs      # 未送检先承接
    assert (ParseState.PARSED, ParseEvent.START_RECOGNITION) in pairs   # 已解析再启动


# ---- 需求要素状态机（LDM-005 确认生命周期，domains/DS-001/state-machines/需求要素.md）----
# 2026-07-05 收敛为 3 态 + 重开：AI 复核/修订迭代是会话事实，不进入生命周期状态。

def test_element_transitions_table_covers_full_lifecycle():
    """迁移表 = 事实源：待确认 3 出口（确认/采纳修订稿/拒绝）+ 重开 2。"""
    assert len(ELEMENT_TRANSITIONS) == 5


@pytest.mark.parametrize(
    "current,event,expected",
    [
        # 待确认（唯一非终态）
        (ES.PENDING_CONFIRMATION, ElementEvent.CONFIRM, ES.CONFIRMED),
        (ES.PENDING_CONFIRMATION, ElementEvent.ADOPT_REVISION, ES.CONFIRMED),  # 采纳即确认
        (ES.PENDING_CONFIRMATION, ElementEvent.REJECT, ES.REVOKED),
        # 重开 / 回流（新版本）
        (ES.CONFIRMED, ElementEvent.REOPEN, ES.PENDING_CONFIRMATION),
        (ES.REVOKED, ElementEvent.REOPEN, ES.PENDING_CONFIRMATION),
    ],
)
def test_element_transition_matrix(current, event, expected):
    assert element_transition(current, event) is expected


@pytest.mark.parametrize(
    "current,event",
    [
        (ES.CONFIRMED, ElementEvent.CONFIRM),        # 终态不再直接裁定
        (ES.REVOKED, ElementEvent.REJECT),
        (ES.CONFIRMED, ElementEvent.ADOPT_REVISION),  # 终态不采纳修订稿
        (ES.REVOKED, ElementEvent.ADOPT_REVISION),
        (ES.PENDING_CONFIRMATION, ElementEvent.REOPEN),  # 非终态不重开
    ],
)
def test_element_default_reject(current, event):
    with pytest.raises(RejectedTransition):
        element_transition(current, event)
