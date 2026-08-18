"""材料接收服务：四分支 + VAL-002/003/005 + 幂等 + gate（slices/SCN-001-P01/约束与验收.md）。"""
import pytest

from app.api.schemas import IntakeJudgementResultCommand, TextIntakeCommand
from app.domain.enums import IntakeConclusion, IntakeOutcome, IntakeRequestStatus, ModelJudgement
from app.repositories.in_memory import build_wiring


def _wiring():
    return build_wiring(auto_complete=False, selected_projects={"PRJ-1"})


def _submit(w, project="PRJ-1", text="系统应支持导出 docx。", key="K1"):
    return w.service.submit_text_intake(
        TextIntakeCommand(
            project_ref=project, raw_text=text, source_note="访谈", operator_ref="U1", idempotency_key=key
        )
    )


def _accept(w, context, judgement, service_accepts=True, mrr="MR-1"):
    w.model_results.seed_judgement(mrr, judgement)
    return w.service.accept_intake_judgement_result(
        IntakeJudgementResultCommand(
            model_result_ref=mrr,
            intake_context_ref=context,
            operator_ref="U1",
            idempotency_key="A1",
            service_accepts=service_accepts,
        )
    )


# ---- AEP-001 gate / 幂等 ----

def test_precheck_pass_submits_without_writing_material():
    w = _wiring()
    r = _submit(w)
    assert r.status is IntakeRequestStatus.SUBMITTED_FOR_JUDGEMENT
    assert r.context_ref and r.agent_run_ref
    assert w.source_assets.save_material_calls == 0  # VAL-002：送检前不写事实


def test_precheck_rejects_unselected_project():
    w = _wiring()
    r = _submit(w, project="PRJ-UNKNOWN")
    assert r.status is IntakeRequestStatus.REJECTED_PRECHECK
    assert r.context_ref is None and r.next_action


def test_precheck_rejects_empty_text():
    w = _wiring()
    r = _submit(w, text="   ")
    assert r.status is IntakeRequestStatus.REJECTED_PRECHECK


def test_idempotent_replay_returns_same_context():
    w = _wiring()
    a = _submit(w, key="SAME")
    b = _submit(w, key="SAME")
    assert a.context_ref == b.context_ref
    assert len(w.model_orchestration.dispatched) == 1


# ---- AEP-002 四分支 ----

def test_acceptable_writes_material_and_traces():
    w = _wiring()
    ctx = _submit(w).context_ref
    r = _accept(w, ctx, ModelJudgement.ACCEPTABLE)
    assert r.outcome is IntakeOutcome.ACCEPTED
    assert r.intake_conclusion is IntakeConclusion.ACCEPTED
    assert r.material_ref
    assert w.source_assets.save_material_calls == 1
    assert w.trace_graph.pre_established == [r.material_ref]
    assert w.audit.accepted == [r.material_ref]


def test_insufficient_returns_supplement_without_material():
    w = _wiring()
    ctx = _submit(w).context_ref
    r = _accept(w, ctx, ModelJudgement.INSUFFICIENT_CONTENT)
    assert r.outcome is IntakeOutcome.RETURNED_FOR_SUPPLEMENT
    assert r.material_ref is None
    assert w.source_assets.save_material_calls == 0
    assert r.next_action


def test_no_asset_value_excluded_without_material():
    w = _wiring()
    ctx = _submit(w).context_ref
    r = _accept(w, ctx, ModelJudgement.NO_ASSET_VALUE)
    assert r.outcome is IntakeOutcome.EXCLUDED
    assert w.source_assets.save_material_calls == 0


def test_judgement_failed_stops_and_preserves_continuation():
    w = _wiring()
    ctx = _submit(w).context_ref
    r = _accept(w, ctx, ModelJudgement.JUDGEMENT_FAILED)
    assert r.outcome is IntakeOutcome.JUDGEMENT_FAILED
    assert r.material_ref is None
    assert w.source_assets.save_material_calls == 0  # VAL-005 不污染事实
    assert r.next_action
    assert w.process_records.read_stop_next_action(ctx)  # 保留失败停靠


def test_acceptable_but_service_declines_writes_no_material():
    w = _wiring()
    ctx = _submit(w).context_ref
    r = _accept(w, ctx, ModelJudgement.ACCEPTABLE, service_accepts=False)
    assert r.material_ref is None
    assert w.source_assets.save_material_calls == 0


# ---- VAL-002/003：仅可接入写 LDM-002 ----

@pytest.mark.parametrize("judgement", list(ModelJudgement))
def test_material_written_only_for_acceptable(judgement):
    w = _wiring()
    ctx = _submit(w).context_ref
    _accept(w, ctx, judgement)
    expected = 1 if judgement is ModelJudgement.ACCEPTABLE else 0
    assert w.source_assets.save_material_calls == expected


# ---- 结果查询读视图：available_actions 是后端事实 ----

def test_result_read_accepted_opens_p02():
    w = _wiring()
    ctx = _submit(w).context_ref
    _accept(w, ctx, ModelJudgement.ACCEPTABLE)
    read = w.service.read_intake_result(ctx)
    assert read.intake_conclusion is IntakeConclusion.ACCEPTED
    assert any(a.key == "start_recognition" and a.enabled for a in read.available_actions)
