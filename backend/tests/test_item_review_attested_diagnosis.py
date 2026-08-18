"""人工确认背书进诊断上下文与来源类判据降格（T20260721-attested-diagnosis-context）。

背书是「材料里没写这条，由人确认它成立」的授权例外。此前诊断链两头都不知道这件事：
模型收不到背书事实，于是照旧判「需补充来源」；读侧也没有降格口径，于是那条发现项照旧
挡着确认。用户因此被卡在「背书 → 重诊 → 又判补充 → 手动拒绝」的来回里。

本文件钉住两件事，以及它们各自的边界：

- A2 上下文：背书条目的诊断上下文带上背书事实（理由原文/操作者/时间），无背书条目恒为 None
  （提示词逐字节不变的前提，模板侧断言见 test_prompt_templates.py）。
- A3 降格：背书条目上的来源对齐类发现项降为非阻断提示，读投影打标记、不计入阻断计数、
  不给否决入口。**降格严格限于来源对齐一类**——与业务规则矛盾（BIZ-RULE-CONFLICT）、
  表达歧义、可测试性等判据一条都不放宽，否则背书就成了整体放行。
- 边界（2026-07-25 用户拍板，取代原口径）：一轮里的阻断项全部被降格、界面上零待处理时，
  直接确认通道要**开**——通道按「本轮已无成立的阻断问题」开门；但留痕那句「已被逐条裁定
  （N 条）」仍只由用户的真实裁定驱动，降格顶不开它，两者拆成两个谓词。

设计事实源：任务卡 harness-engineering/worktree-pool/tasks/
T20260721-attested-diagnosis-context.card.md 的「## 方案确认」节；冷审查裁定
verification/review/T20260721-attested-diagnosis-context-verdict.md 的 K1/K4/K5/K6/K10 节。
"""
import uuid

import pytest

from app.adapters.llm import DiagnosedFinding, ItemVerdictOutcome
from app.api.schemas import ReviewDialogueCommand
from app.domain.enums import RequirementQualityRule, VerdictDecision, VerdictKind
from app.domain.errors import InvalidInput, RejectedTransition
from app.repositories.sqlalchemy import build_sql_item_review_service

from tests.test_item_review import (  # 复用既有夹具（同一被测服务，口径单一来源）
    _adjudicate,
    _diag_command,
    _item_view,
    _seed_pending_items,
    _version,
    _workspace,
    session,  # noqa: F401  pytest fixture
)
from tests.test_item_review_source_attestation import (
    _attest,
    _seed_supplement_pending,
)
from tests.test_item_review_veto import _veto


class _ScriptedDiagnoser:
    """按给定发现项返回同一个「建议修订」结论，并捕获喂给模型的背书上下文。"""

    def __init__(self, *findings: DiagnosedFinding) -> None:
        self._findings = findings
        self.seen_attestation: object = "<未调用>"

    def diagnose(
        self, project_ref, diagnosis_mode, item, sources, raw_text, revisions,
        prior_findings, excluded_points=None, thread_context="", business_sources=None,
        attestation=None,
    ) -> ItemVerdictOutcome:
        self.seen_attestation = attestation
        return ItemVerdictOutcome(
            verdict_kind="revise", verdict_summary="有待改问题。",
            findings=self._findings,
            # 「建议修订必须携带修订点」是既有聚合守卫；挂在第一条发现项上即可满足，
            # 本文件断言的是发现项的降格与计数，与点本身无关。
            revision_points=({
                "point_ref": "P1", "label": "补口径", "finding_index": 0,
                "find": "导出", "replace": "导出（每次）", "basis": "stub", "group": None,
            },),
            supplement_gaps=(), basis="stub 条目诊断完成",
        )


def _source_finding(summary="表达与来源要素对不上。", rule=RequirementQualityRule.SRC_DRIFT.value):
    return DiagnosedFinding(
        "source_inconsistency", summary, "stub 依据",
        rule_code=rule, evidence_span="导出", severity="medium", dimension="consistent",
    )


def _untestable_finding():
    return DiagnosedFinding(
        "untestable", "「尽快」不可测。", "stub 依据",
        rule_code=RequirementQualityRule.INCOSE_R7.value, evidence_span="尽快",
        severity="medium", dimension="verifiable",
    )


class _SupplementDiagnoser:
    """恒判「建议补充来源」且缺口落在缺具体值上（人工确认闭合不了这类缺口）。"""

    def diagnose(self, project_ref, diagnosis_mode, item, sources, raw_text, revisions,
                 prior_findings, excluded_points=None, thread_context="",
                 business_sources=None, attestation=None) -> ItemVerdictOutcome:
        return ItemVerdictOutcome(
            verdict_kind="supplement",
            verdict_summary="来源缺口已由人工确认闭合，但缺具体验收口径。",
            findings=(DiagnosedFinding(
                "missing_field", "未定义导出格式与必含字段，无法验收。", "stub 依据",
                rule_code=RequirementQualityRule.SMELL_UNDEF.value, evidence_span="导出",
                severity="medium", dimension="complete"),),
            revision_points=(),
            supplement_gaps=("需向财务确认导出格式与必含字段清单。",),
            basis="stub 条目诊断完成",
        )


def _diagnose_with(session, w, item_ref, diagnoser):
    """用给定诊断器对该条目跑一轮诊断，返回站立结论的读投影。"""
    svc = build_sql_item_review_service(session, auto_complete=True, item_diagnoser=diagnoser)
    svc.start_item_diagnosis(_diag_command(session, w, item_refs=[item_ref], mode="standard"))
    session.commit()
    view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert view.current_verdict is not None
    assert view.current_verdict.verdict_kind is VerdictKind.REVISE
    return view


def _finding_of(view, finding_type):
    return next(f for f in view.current_verdict.findings if f.finding_type == finding_type)


def _dialogue(svc, session, w, item_ref, message):
    """区5 对话面一问（意图分流由服务自判：质疑走重评、提问走解释）。"""
    result = svc.review_dialogue(ReviewDialogueCommand(
        project_ref=w["project"], item_ref=item_ref, message=message,
        workspace_version=_version(session, w),
        operator_ref="U1", idempotency_key=f"dlg-{uuid.uuid4()}",
    ))
    session.commit()
    return result


# ---- A2 背书事实进上下文 ----

def test_attested_item_carries_attestation_into_diagnosis_context(session):
    """A2：背书条目的诊断上下文带理由原文、操作者与时间——理由逐字，不摘编不转义。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    reason = "客户在启动会上口头提出，会议纪要漏记了这一条"
    _attest(svc, session, w, item_ref, reason=reason)

    diagnoser = _ScriptedDiagnoser(_untestable_finding())
    _diagnose_with(session, w, item_ref, diagnoser)

    assert diagnoser.seen_attestation is not None
    assert diagnoser.seen_attestation["reason"] == reason
    assert diagnoser.seen_attestation["operator_ref"] == "U1"
    assert diagnoser.seen_attestation["at"]


def test_item_without_attestation_gets_none(session):
    """A1：没有背书的条目恒传 None——模板据此整段不渲染，提示词逐字节不变。"""
    w = _seed_pending_items(session)
    item_ref = w["items"][0]

    diagnoser = _ScriptedDiagnoser(_untestable_finding())
    _diagnose_with(session, w, item_ref, diagnoser)

    assert diagnoser.seen_attestation is None


# ---- A3 来源类判据降格 ----

def test_source_finding_on_attested_item_is_degraded_to_non_blocking(session):
    """A3：背书条目上的来源对齐类发现降为提示——打标记、不计阻断、不给否决入口。

    不给否决入口是有意的：对一条已经不是问题的提示再问「这是不是问题」，会让用户
    以为自己还欠一次处理。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)

    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    finding = _finding_of(view, "source_inconsistency")
    assert finding.source_attested is True
    assert finding.can_veto is False
    assert view.current_verdict.blocking_finding_count == 0


def test_same_source_finding_still_blocks_without_attestation(session):
    """A3 对照例：同一条来源类发现，条目没有背书时照常阻断（降格的因是背书，不是发现项本身）。"""
    w = _seed_pending_items(session)
    item_ref = w["items"][0]

    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    finding = _finding_of(view, "source_inconsistency")
    assert finding.source_attested is False
    assert view.current_verdict.blocking_finding_count == 1


def test_business_rule_conflict_is_not_degraded_by_attestation(session):
    """A3 红线：与业务规则矛盾不因背书降格——那是「和领域知识打架」，不是「材料没写出处」。

    它虽然也归 source_inconsistency 粗类，但规则码是 BIZ-RULE-CONFLICT。背书闭合的是
    来源缺口，替这条放行就是把授权例外扩大成整体放水。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)

    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(
        _source_finding(summary="条目动作与所引业务规则矛盾。",
                        rule=RequirementQualityRule.BIZ_RULE_CONFLICT.value)))

    finding = _finding_of(view, "source_inconsistency")
    assert finding.source_attested is False
    assert view.current_verdict.blocking_finding_count == 1


def test_non_source_judgements_untouched_by_attestation(session):
    """A3 对照例：可测试性等其余判据在背书条目上照常阻断（背书≠有材料出处）。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)

    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(
        _untestable_finding(), _source_finding()))

    assert _finding_of(view, "untestable").source_attested is False
    assert _finding_of(view, "source_inconsistency").source_attested is True
    # 只剩可测试性那一条挡着：降格改的是计数，不是把问题全抹掉
    assert view.current_verdict.blocking_finding_count == 1


def test_all_blocking_degraded_opens_the_confirm_channel_with_a_true_reason(session):
    """K5（2026-07-25 用户拍板，取代原「降格不开通道」用例）：界面零待处理时通道要开。

    原口径：降格不顶开直接确认通道，理由是留痕会写出「已被逐条裁定（0 条）」这种假话。
    实测下来它有两个后果，都比它想防的那件事更糟：
    - 一轮里的阻断项**全部**被降格时，界面上一条待处理的问题都没有，确认按钮却被禁用，
      禁用提示还写着「本轮还有你没处理的问题」——与用户眼前看到的界面正相反；
    - 用户只剩覆盖确认可走，而覆盖确认要填理由、要打覆盖标记进效能统计，与「问题都已消解」
      不是一回事，效能账目会失真。

    改法是把两件事拆成两个谓词：通道按「本轮已无成立的阻断问题」开门，留痕那句「已被逐条
    裁定（N 条）」仍只由用户的真实裁定驱动（见下一个用例）。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)

    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    # 用户一条都没裁定，所以「已被逐条裁定」仍为假——留痕口径不被降格顶开
    assert view.current_verdict.all_blocking_findings_vetoed is False
    # 但界面上零待处理，通道要开
    assert view.current_verdict.blocking_finding_count == 0
    assert view.current_verdict.blocking_findings_cleared is True
    confirm = next(a for a in view.available_actions if a.key == "confirm_without_override")
    assert confirm.enabled is True
    assert confirm.disabled_reason is None
    # 说明句不得说「你都标成了不是问题」——用户一次都没标过
    assert "你都标成了不是问题" not in view.status_note
    assert "不用你处理" in view.status_note


def test_confirming_a_degraded_round_does_not_claim_the_user_ruled_on_it(session):
    """K5/K10(c)：留痕如实分口径——没裁定过就不许写「已被逐条裁定（N 条）」。"""
    from tests.test_item_review_veto import _confirm

    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    result = _confirm(build_sql_item_review_service(session), session, w, item_ref)
    assert result.status == "confirmed"

    after = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    trail = " ".join(
        (v.adjudication.reason or "") for v in after.verdict_history if v.adjudication
    )
    assert "已被逐条裁定" not in trail
    assert "因来源已由人工确认而降为提示 1 条" in trail


def test_veto_count_in_the_trail_matches_what_the_user_actually_clicked(session):
    """K10(c)：留痕条数＝用户点过几次，与「你标为不是问题的」列表可见条数自洽。

    触发路径：用户在人工确认之前否决过一条来源类发现（当时 can_veto 为真），确认之后新一轮
    按指纹重新认领同一条发现项。此前 blocking_veto_refs 把降格项排除在外，于是这一条既不
    计数也不显示，留痕写的条数比用户在列表里数得到的少（本仓纪律：计数须与用户可见输入自洽）。
    """
    from tests.test_item_review_veto import _confirm

    from tests.test_item_review import _set_expression
    from tests.test_item_review_source_attestation import NO_SOURCE_EXPRESSION

    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    _set_expression(session, item_ref, NO_SOURCE_EXPRESSION)

    # ① 人工确认之前先诊断一轮，那条来源类发现此刻照常阻断、可被裁定——用户点了一次
    before = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))
    target = _finding_of(before, "source_inconsistency")
    assert target.can_veto is True
    _veto(build_sql_item_review_service(session), session, w, item_ref, target.finding_ref,
          reason="口径是对的，来源写法不同而已")

    # ② 拒绝该轮 → 再诊断出一个「建议补充来源」结论并采纳，把条目推进「待补充来源」
    rejected_round = before.current_verdict.round_ref
    review = build_sql_item_review_service(session)
    _adjudicate(review, session, w, item_ref, rejected_round, VerdictDecision.REJECTED,
                reason="先按另一条路走")
    supplement = build_sql_item_review_service(
        session, auto_complete=True, item_diagnoser=_SupplementDiagnoser())
    supplement.start_item_diagnosis(_diag_command(session, w, item_refs=[item_ref], mode="standard"))
    session.commit()
    gap_view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    _adjudicate(supplement, session, w, item_ref, gap_view.current_verdict.round_ref,
                VerdictDecision.ADOPTED)

    # ③ 人工确认闭合缺口，再诊断——同指纹的那条发现项这次既在否决集里、又被降格
    _attest(build_sql_item_review_service(session), session, w, item_ref)
    after = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))
    claimed = _finding_of(after, "source_inconsistency")
    assert claimed.source_attested is True
    assert claimed.vetoed is True  # 按指纹认领的是用户当初点的那一次

    # ④ 留痕条数＝用户点过几次（1），与「你标为不是问题的」列表可见条数自洽。
    #    此前 blocking_veto_refs 把降格项排除在外，这条既不计数也不显示，账面会写成 0 条。
    visible = [v for v in after.finding_vetoes if not v.revoked]
    assert len(visible) == 1
    result = _confirm(build_sql_item_review_service(session), session, w, item_ref)
    assert result.status == "confirmed"
    done = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    trail = " ".join(
        (v.adjudication.reason or "") for v in done.verdict_history if v.adjudication
    )
    assert "已被逐条裁定为不是问题（1 条）" in trail
    # 同一条发现项不许在两句话里各数一遍（界面上只有一条）
    assert "另有" not in trail


def test_missing_rule_code_is_not_degraded(session):
    """K6：规则码取不到时**不**降格（白名单口径，2026-07-25 用户拍板）。

    此前的谓词是「规则码不等于 BIZ-RULE-CONFLICT 就降格」，于是三条路径都会落进降格分支：
    存量轮次没有 quality_meta、部分带引用的元数据配不上、模型漏写或写错枚举被适配器置 None。
    红线在信息缺失时倒向宽松——一条「与业务规则矛盾」报成 source_inconsistency 却漏写规则码
    的发现项会被静默放行。改成白名单后信息缺失倒向保守：不降格，照旧阻断。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)

    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(DiagnosedFinding(
        "source_inconsistency", "条目动作与所引业务规则矛盾。", "stub 依据",
        rule_code=None, evidence_span="导出", severity="high", dimension="consistent")))

    finding = _finding_of(view, "source_inconsistency")
    assert finding.source_attested is False
    assert view.current_verdict.blocking_finding_count == 1


def test_vetoing_a_degraded_finding_is_rejected_server_side(session):
    """K10(b)：降格项的「这不是问题」在服务端也要拒，不只是界面藏起入口。

    界面隐藏不是门禁：页面陈旧或直接调接口仍能对一条提示登记否决，而这行否决既不进阻断
    计数、也不在卡片上显示，等于一次没有任何可见后果的写入。与 can_veto=False 对称。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))
    target = _finding_of(view, "source_inconsistency")
    assert target.can_veto is False

    with pytest.raises(RejectedTransition) as exc:
        _veto(build_sql_item_review_service(session), session, w, item_ref, target.finding_ref)
    assert "已经不需要你处理" in str(exc.value)


# ---- K1：降格发现项的修订点不得被采纳 ----

def test_degraded_points_are_not_applied_when_no_points_are_named(session):
    """K1 场景一：不点名修订点的采纳（区5 斜杠命令恒传 None）不得应用降格项的改法。

    区5 的结论卡对用户写着「AI 就此给过改法，但这条不用改，**采纳时不会应用它**」。此前后端
    一行都没为这句话改过：默认选择集只剔除被否决的点，降格项的点照旧入选并被应用，条目表达
    被改写而用户从未选过那个改法。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))
    before_expression = view.expression

    review = build_sql_item_review_service(session)
    with pytest.raises(InvalidInput) as exc:
        _adjudicate(review, session, w, item_ref, view.current_verdict.round_ref,
                    VerdictDecision.ADOPTED)
    # 唯一的点绑在降格发现项上，默认选择集因此为空——如实拒绝，而不是静默改写条目
    assert "没有选中任何改法" in str(exc.value) or "不用处理" in str(exc.value)

    after = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert after.expression == before_expression


def test_degraded_points_are_not_dragged_back_by_a_linked_group(session):
    """K1 场景二：联动组整组展开不得把降格项的点拉回来。

    前端按承诺只送非降格的点，但联动组不可拆、同组整组入选，此前整组剔除只对被否决的点做，
    于是降格项的点被整组拉回并应用（界面路径同样中招）。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)

    class _GroupedDiagnoser:
        """两条发现项：降格的来源对齐项与照常阻断的可测试性项，其修订点同属一个联动组。"""

        def diagnose(self, project_ref, diagnosis_mode, item, sources, raw_text, revisions,
                     prior_findings, excluded_points=None, thread_context="",
                     business_sources=None, attestation=None) -> ItemVerdictOutcome:
            return ItemVerdictOutcome(
                verdict_kind="revise", verdict_summary="有待改问题。",
                findings=(_source_finding(), _untestable_finding()),
                revision_points=(
                    {"point_ref": "P1", "label": "对齐来源", "finding_index": 0,
                     "find": "导出", "replace": "导出（每次）", "basis": "stub", "group": "G1"},
                    {"point_ref": "P2", "label": "补口径", "finding_index": 1,
                     "find": "docx", "replace": "docx（UTF-8）", "basis": "stub", "group": "G1"},
                ),
                supplement_gaps=(), basis="stub 条目诊断完成",
            )

    view = _diagnose_with(session, w, item_ref, _GroupedDiagnoser())
    review = build_sql_item_review_service(session)
    # 前端按承诺只送 P2；整组展开会把 P1 拉回来，整组剔除必须把它连同整组一起剔掉
    with pytest.raises(InvalidInput) as exc:
        _adjudicate(review, session, w, item_ref, view.current_verdict.round_ref,
                    VerdictDecision.ADOPTED, selected=["P2"])
    assert "整组采纳的联动组" in str(exc.value)

    after = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert "导出（每次）" not in after.expression


# ---- K4：对话面两条通道的上下文同样带人工确认 ----

def test_reeval_context_carries_attestation_and_degrade_marks(session):
    """K4：轻量重评的上下文带人工确认与降格标记。

    轻量重评是第二条会铸出正式诊断轮次的路径。模型不知道有过人工确认时可以重新判「建议
    补充来源」并带缺口，用户一采纳，条目就退回「待补充来源」——而人工确认的准入不许第二次
    确认，本卡关掉的正是那条逃生口。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    reason = "客户在启动会上口头提出，会议纪要漏记了这一条"
    _attest(svc, session, w, item_ref, reason=reason)
    _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    seen: dict = {}

    class _CapturingReeval:
        def reeval(self, item, standing_verdict, message, excluded_points, thread_context):
            seen["context"] = standing_verdict
            from app.adapters.llm import ReevalOutcome
            return ReevalOutcome(action="maintain", explanation="维持结论（探针）。", verdict=None)

    review = build_sql_item_review_service(session)
    review._reeval_responder = _CapturingReeval()
    _dialogue(review, session, w, item_ref, "我认为这条判得太严了")

    context = seen["context"]
    assert context["attestation"]["reason"] == reason
    assert context["attestation"]["operator_ref"] == "U1"
    degraded = [f for f in context["findings"] if f["finding_type"] == "source_inconsistency"]
    assert degraded and all(f["source_attested"] for f in degraded)


def test_explain_context_carries_attestation_too(session):
    """K4 第二处：解释通道同样不再把降格项当阻断问题解释。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    seen: dict = {}

    class _CapturingExplainer:
        def explain(self, item, verdict_context, question):
            seen["context"] = verdict_context
            return "这是解释（探针）。"

    review = build_sql_item_review_service(session)
    review._explainer = _CapturingExplainer()
    _dialogue(review, session, w, item_ref, "这条结论的依据是什么？")

    context = seen["context"]
    assert context["attestation"] is not None
    degraded = [f for f in context["findings"] if f["finding_type"] == "source_inconsistency"]
    assert degraded and all(f["source_attested"] for f in degraded)


def test_context_shape_is_unchanged_without_attestation(session):
    """K4 边界：没有背书的条目，结论上下文一个键都不新增（提示词渲染因此不变）。"""
    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    seen: dict = {}

    class _CapturingExplainer:
        def explain(self, item, verdict_context, question):
            seen["context"] = verdict_context
            return "这是解释（探针）。"

    review = build_sql_item_review_service(session)
    review._explainer = _CapturingExplainer()
    _dialogue(review, session, w, item_ref, "这条结论的依据是什么？")

    context = seen["context"]
    assert "attestation" not in context
    assert all("source_attested" not in f for f in context["findings"])


def test_history_rounds_are_degraded_with_the_same_yardstick(session):
    """A3 一致性：历史轮次与站立轮用同一把尺——同一条发现项不会在两处呈现相反。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))
    # 再跑一轮：上一轮转入历史
    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    assert view.verdict_history, "第二轮之后应有历史轮次"
    # S8：先钉住降格条数，再逐条断言。原写法把断言写在双层循环的 if 里——历史轮若一条来源类
    # 发现都没有（比如投影漏了 findings），循环空过，用例照样绿。
    degraded = [
        f for verdict in view.verdict_history for f in verdict.findings
        if f.finding_type == "source_inconsistency"
    ]
    assert degraded, "历史轮里应有来源类发现项可供比对（空过等于没测）"
    assert all(f.source_attested for f in degraded)


def test_quality_projection_uses_the_same_degrade(session):
    """A3 一致性：质量投影（详情卡「质量诊断」页签）与工作区读投影同一口径。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(_source_finding()))

    quality = build_sql_item_review_service(session).read_item_quality(w["project"], item_ref)
    degraded = [f for f in quality.findings if f.finding_type == "source_inconsistency"]
    assert degraded and all(f.source_attested for f in degraded)


def test_veto_still_clears_the_remaining_real_problem(session):
    """混合场景：降格项不占名额，用户裁定掉真问题后直接确认通道照常打开、账目如实。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    view = _diagnose_with(session, w, item_ref, _ScriptedDiagnoser(
        _untestable_finding(), _source_finding()))

    target = _finding_of(view, "untestable")
    _veto(build_sql_item_review_service(session), session, w, item_ref, target.finding_ref,
          reason="行业惯例如此")

    after = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert after.current_verdict.blocking_finding_count == 0
    assert after.current_verdict.all_blocking_findings_vetoed is True


# ---- 重复人工确认：出处缺口只闭合一次 ----

def test_second_attestation_is_rejected_with_an_actionable_reason(session):
    """走查发现（2026-07-25，REQ-008）：已确认过的条目不得再人工确认。

    人工确认闭合的是「这条需求找不到出处」。出处缺口闭合之后，AI 再判「建议补充来源」，
    缺的必定是格式/字段/阈值这类**具体值**——人工确认一个值都提供不了。放行只会让用户在
    「确认→重诊→又说缺→再确认」里绕圈：每一圈都闭合一次已经闭合的东西，真正缺的值一次
    也没补上。拒绝理由必须说清这一点并指向真正能解决的路，不能只说「不允许」。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    # 再诊断一轮并采纳其 supplement 结论，把条目重新推回「待补充来源」（缺的是具体值）
    review = build_sql_item_review_service(session, auto_complete=True,
                                           item_diagnoser=_SupplementDiagnoser())
    review.start_item_diagnosis(_diag_command(session, w, item_refs=[item_ref], mode="standard"))
    session.commit()
    view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    _adjudicate(review, session, w, item_ref, view.current_verdict.round_ref,
                VerdictDecision.ADOPTED)

    after = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert after.supplement_gaps_open  # 确实又回到了「待补充来源」

    attest = next(a for a in after.available_actions if a.key == "attest_source")
    assert attest.enabled is False
    assert "已经人工确认过" in (attest.disabled_reason or "")

    with pytest.raises(RejectedTransition) as exc:
        _attest(build_sql_item_review_service(session), session, w, item_ref, reason="再确认一次")
    assert "已经人工确认过" in str(exc.value)


def test_disabled_reason_names_the_judgement_that_actually_failed(session):
    """禁用理由要落到真正不成立的那一条判据上（C14(a)：说错就是对用户说假话）。

    已确认过、但此刻并无未闭合缺口的条目（比如正待裁决），原因是「没有缺口可闭合」，
    不是「本轮缺的是具体口径」——后者会让用户以为系统认定了一个并不存在的缺口。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)  # 背书后缺口闭合，条目回到待诊断

    view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert not view.supplement_gaps_open
    attest = next(a for a in view.available_actions if a.key == "attest_source")
    assert attest.enabled is False
    assert attest.disabled_reason == "这个条目当前没有未闭合的来源缺口"


def test_first_attestation_is_offered_when_gap_is_open(session):
    """对照例：没确认过的条目，缺口未闭合时入口照常给（别把功能整个关掉）。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)

    view = _item_view(_workspace(svc, w), item_ref)
    attest = next(a for a in view.available_actions if a.key == "attest_source")
    assert attest.enabled is True
    assert attest.disabled_reason is None


def test_disabled_reason_for_an_item_that_left_pending_confirmation(session):
    """V5 补测：`not pending` 这一支此前零覆盖（三条禁用理由只测了两条）。

    把 `elif not pending` 与 `elif not gaps` 两支对调顺序，改动前没有任何用例会红；而一个
    「不在待确认状态、却仍有未闭合缺口」的条目（例如刚被覆盖确认过）会拿到「这个条目当前
    没有未闭合的来源缺口」——一句与事实相反的禁用理由，正是 C14(a) 要防的。
    """
    from tests.test_item_review_veto import _confirm

    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _confirm(build_sql_item_review_service(session), session, w, item_ref,
             override=True, reason="口径已在项目群里确认，先放行")

    view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert view.supplement_gaps_open  # 缺口仍在（确认不闭合缺口）
    attest = next(a for a in view.available_actions if a.key == "attest_source")
    assert attest.enabled is False
    assert attest.disabled_reason == "条目不在待确认状态"


def test_attestation_closed_gap_is_false_once_the_item_leaves_pending_diagnosis(session):
    """K7 行为侧：显示态不是「待诊断」时标志位必须为假。

    人工确认与撤回都不动轮次，最新轮永远停在「被人工确认判失效」的那一轮上。不加显示态
    守卫，条目确认之后区5 仍会挂着「人工确认」标签配「条目已确认。」这句话——标签断言这句
    话讲的是人工确认，其实不是；撤回路径同理（「【人工确认】 条目已终止。」）。
    """
    from tests.test_item_review_veto import _confirm

    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    just_attested = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert just_attested.attestation_closed_gap is True

    _confirm(build_sql_item_review_service(session), session, w, item_ref,
             override=True, reason="材料没写，但这条确实要做")
    after = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert after.display_note == "条目已确认。"
    assert after.attestation_closed_gap is False
