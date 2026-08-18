"""人工确认背书（T20260720-supplement-manual-source-and-attest 能力 B）测试义务。

背书是对「条目的依据必须能在材料里指出来」的**授权例外**：材料漏写了某条需求，由人确认
它成立并负责登记。既然是例外，就得看得见、赖不掉，且不能偷偷把自己伪装成正常来源。
本文件按这三条把义务钉死：

- A2 全链：登记背书 → 旧结论失效 → 缺口闭合 → 条目离开「待补充来源」→ 可继续诊断。
- A2 不造假：背书一个字都不改条目的来源要素，读视图把它投影成独立字段而非来源要素的一员；
  状态说明句也不得谎称「条目已修订」——背书什么都没修订。
- A4 例外不外溢：理由必填、无缺口不给背书、幂等重放不重复记账；背书之后普通内容修订的
  失效行为照旧，不因为有过背书而被放宽。

设计事实源：任务卡 harness-engineering/worktree-pool/tasks/
T20260720-supplement-manual-source-and-attest.card.md 的「## 方案确认」节。
"""
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.api.schemas import ItemRevisionCommand, SourceAttestationCommand
from app.db.models import ItemDiagnosisRound, RequirementItem, RequirementItemRevision
from app.domain.enums import (
    ItemRevisionMode,
    ReviewDisplayCode as RDC,
    VerdictDecision,
    VerdictKind,
)
from app.domain.errors import InvalidInput, RejectedTransition
from app.interfaces import ItemRevisionRow
from app.repositories.sqlalchemy import (
    SqlRequirementItemRepository,
    build_sql_item_review_service,
    build_sql_requirement_item_service,
)
from app.services.item_formation import content_revision_seq

from tests.test_item_review import (  # 复用既有夹具与助手（同一被测服务，口径单一来源）
    _adjudicate,
    _diag_command,
    _item_view,
    _run_diagnosis,
    _seed_pending_items,
    _set_expression,
    _version,
    _workspace,
    session,  # noqa: F401  pytest fixture
)

# 触发 stub 诊断器给出「建议补充来源」结论的表达（与 test_item_review 既有用例同口径）
NO_SOURCE_EXPRESSION = "系统应支持导出 docx（缺来源：口径出处不明）"


def _seed_supplement_pending(session):
    """把第一条条目推进到「待补充来源」态，返回 (svc, w, item_ref, round_ref)。"""
    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    _set_expression(session, item_ref, NO_SOURCE_EXPRESSION)
    svc, _ = _run_diagnosis(session, w, item_refs=[item_ref])
    view = _item_view(_workspace(svc, w), item_ref)
    assert view.current_verdict.verdict_kind is VerdictKind.SUPPLEMENT
    round_ref = view.current_verdict.round_ref
    _adjudicate(svc, session, w, item_ref, round_ref, VerdictDecision.ADOPTED)
    blocked = _item_view(_workspace(svc, w), item_ref)
    assert blocked.supplement_gaps_open
    assert blocked.display_code is RDC.SUPPLEMENT_PENDING
    return svc, w, item_ref, round_ref


def _attestation_rows(session, item_ref):
    """该条目名下的背书记录行（直读库，绕开读视图独立取证）。"""
    rows = session.scalars(select(RequirementItemRevision).where(
        RequirementItemRevision.item_ref == uuid.UUID(item_ref))).all()
    return [r for r in rows if r.field_key == "source_attestation"]


def _attest(svc, session, w, item_ref, reason="访谈时客户口头提的，纪要漏记了", key=None):
    workspace = svc.attest_source(SourceAttestationCommand(
        project_ref=w["project"], item_ref=item_ref, reason=reason,
        operator_ref="U1", idempotency_key=key or f"att-{uuid.uuid4()}",
    ))
    session.commit()
    return workspace


# ---- A2 全链 ----

def test_attestation_closes_gap_and_unblocks_diagnosis(session):
    """A2 主链：背书四点串联——旧轮失效、缺口判空、离开「待补充来源」、再诊断前置放行。

    与「登记来源」殊途同归：都靠让轮次失效来解除派生态，而不是去清什么标志位。
    """
    svc, w, item_ref, round_ref = _seed_supplement_pending(session)

    _attest(svc, session, w, item_ref)

    # ① 旧诊断轮失效，且失效理由据实说明是人工确认（不是「条目已修订」）
    round_row = session.get(ItemDiagnosisRound, uuid.UUID(round_ref))
    assert round_row.invalidated is True
    assert "人工确认" in (round_row.invalidated_reason or "")

    review = build_sql_item_review_service(session)
    after = _item_view(_workspace(review, w), item_ref)
    # ② 缺口判定为空　③ 派生显示态离开「待补充来源」
    assert not after.supplement_gaps_open
    assert after.display_code is not RDC.SUPPLEMENT_PENDING
    # ④ 可继续诊断（缺口前置不再拦）
    assert review.start_item_diagnosis(
        _diag_command(session, w, item_refs=[item_ref])).status == "submitted"


def test_attestation_projected_as_independent_evidence(session):
    """A2 呈现：背书投影成条目上的独立字段，带理由/操作者/时间，供界面单独显示。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    reason = "客户在 3 月 12 日的评审会上口头确认，会议纪要漏记"

    _attest(svc, session, w, item_ref, reason=reason)

    view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert view.source_attestation is not None
    assert view.source_attestation.reason == reason  # 理由原文照录，不改写不摘要
    assert view.source_attestation.operator_ref == "U1"
    assert view.source_attestation.at


def test_attestation_never_fabricates_a_source_element(session):
    """A2 红线：背书绝不塞假的来源要素——source_element_refs 一个字都不动。

    这是背书与「登记来源」的分界：登记来源指向材料里真实存在的要素，背书承认材料里没有。
    若为了让条目「看上去有来源」而写一个占位要素编号，下游就再也分不清哪条有材料依据。
    """
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    before = json.loads(
        session.get(RequirementItem, uuid.UUID(item_ref)).source_element_refs or "[]")

    _attest(svc, session, w, item_ref)

    session.expire_all()
    after_raw = json.loads(
        session.get(RequirementItem, uuid.UUID(item_ref)).source_element_refs or "[]")
    assert after_raw == before
    view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert view.source_element_refs == before  # 读视图同样不多出任何来源要素


def test_attestation_state_note_does_not_claim_a_revision(session):
    """A2 说人话：背书什么都没改，状态说明句就不能说「条目已修订」。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)

    _attest(svc, session, w, item_ref)

    view = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert view.display_code is RDC.PENDING_DIAGNOSIS
    assert "人工确认" in view.display_note
    assert "条目已修订" not in view.display_note


def test_attestation_recorded_as_revision_row_for_downstream(session):
    """A2 可追溯：背书落成一条修订记录，下游读修订记录即可看见这笔账（零迁移承载面）。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)

    _attest(svc, session, w, item_ref, reason="口头确认，纪要漏记")

    attestations = _attestation_rows(session, item_ref)
    assert len(attestations) == 1
    assert attestations[0].reason == "口头确认，纪要漏记"
    assert attestations[0].operator_ref == "U1"
    assert attestations[0].revision_mode == ItemRevisionMode.MANUAL.value


# ---- A4 例外不外溢 ----

@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_attestation_requires_reason(session, reason):
    """A4：理由必填。授权例外要赖不掉，空白理由等于没留痕。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    with pytest.raises(InvalidInput) as exc:
        _attest(svc, session, w, item_ref, reason=reason)
    assert "理由" in str(exc.value)


def test_attestation_rejected_when_no_open_gap(session):
    """A4：不在「待补充来源」态就不给背书——没有缺口可闭合时放行只会凭空多一条背书记录。

    状态类拒绝用 RejectedTransition(409)，与本文件既有口径一致（C18）：客户端据此能分辨
    「状态变了，刷新后可能就能做」与「你填错了参数」（后者才是 InvalidInput/400）。
    """
    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    svc = build_sql_item_review_service(session)
    with pytest.raises(RejectedTransition) as exc:
        _attest(svc, session, w, item_ref)
    assert "缺口" in str(exc.value)


def test_attestation_replay_is_idempotent(session):
    """A4：同一幂等键重发不重复记账（网络重试不该背两次书）。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    key = f"att-{uuid.uuid4()}"

    _attest(svc, session, w, item_ref, key=key)
    _attest(build_sql_item_review_service(session), session, w, item_ref, key=key)

    assert len(_attestation_rows(session, item_ref)) == 1


def test_normal_content_revision_still_invalidates_after_attestation(session):
    """A4：背书之后普通内容修订的行为照旧——例外不外溢成「这条目从此免检」。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    # A4 字段断言（2026-07-26 补，冷审查 V3）：attestation_closed_gap 此前全仓零后端断言，
    # 把它接到「确认过没有」这个粘性事实上（而不是接到共用谓词上）后端全绿、前端也全绿
    # ——前端三个用例把这个布尔值写死在夹具里。缺的是**谓词到字段的接线**，这里补上。
    just_attested = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert just_attested.attestation_closed_gap is True
    assert just_attested.display_note == "来源缺口已由人工确认闭合（材料未记载该需求）；可重新诊断。"
    review = build_sql_item_review_service(session)
    assert review.start_item_diagnosis(
        _diag_command(session, w, item_refs=[item_ref])).status == "submitted"
    session.commit()
    new_round = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)

    # 修改表达＝内容变更，仍须让刚产生的这一轮失效（与未背书条目同一口径）
    item_service = build_sql_requirement_item_service(session)
    item_service.apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=_version(session, w),
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value="系统应支持把需求清单导出为 docx 文件",
        operator_ref="U1", idempotency_key=f"rev-{uuid.uuid4()}"))
    session.commit()

    # C5：这次失效由普通内容修订造成，不是背书造成——说明句必须回到「条目已修订，旧结论已失效」，
    # 不得再谎称「来源缺口已由人工确认闭合」（背书是粘性事实，只看「背书过」会把这次普通修订误报）。
    after = _item_view(_workspace(build_sql_item_review_service(session), w), item_ref)
    assert "人工确认" not in after.display_note
    # 同一次判据的字段侧：普通修订之后标志位必须转假，否则区5 会把这次修订说成
    # 「来源缺口刚由人工确认闭合」（设计注释点名要防的 C5）
    assert after.attestation_closed_gap is False

    if new_round.current_verdict is not None:
        assert session.get(
            ItemDiagnosisRound, uuid.UUID(new_round.current_verdict.round_ref)).invalidated is True


# ---- A1 借表外溢根治：背书不推进内容修订序号（版本锚）----

def _rev(field_key, before, after, mode="manual"):
    return ItemRevisionRow(
        id="r", item_ref="i", field_key=field_key, before_value=before,
        after_value=after, revision_mode=mode, suggestion_ref=None,
        selected_point_refs=None, reason=None, operator_ref="U1", at="",
    )


def test_content_revision_seq_excludes_attestation_attribute_and_noop():
    """A1 锚定逻辑：内容修订序号只计真实内容变更。背书（借表落库没改字段）、属性字段
    （验证方式/验收准则/优先级）、无变更留痕（before==after）三类都不推进序号。"""
    rows = [
        _rev("expression", "旧", "新"),                          # 内容修订 → 计入
        _rev("source_attestation", "", "已人工确认为真实需求（材料未记载）"),  # 背书 → 不计
        _rev("priority", "", "high"),                            # 属性字段 → 不计
        _rev("req_type", "functional", "functional"),            # 无变更 → 不计
    ]
    assert content_revision_seq(rows) == 2   # 基线 1 + expression
    # 只有一条背书时序号退回基线 1：序号是从修订行现算的派生值，故存量背书条目自动恢复、无需迁移
    assert content_revision_seq([_rev("source_attestation", "", "已人工确认…")]) == 1


def test_attestation_does_not_advance_version_anchor(session):
    """A1 根修（集成）：背书借表落库但一个字段都没改，读侧现算的内容修订序号不因它跳变——
    否则投影判 stale、区4 体检整块消失且永不自愈。序号是派生值，存量已背书条目打开即恢复。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    repo = SqlRequirementItemRepository(session)
    before = content_revision_seq(repo.revisions_of(item_ref))

    _attest(svc, session, w, item_ref)

    assert _attestation_rows(session, item_ref)   # 背书行确实落库（不是因为没写才不跳）
    after = content_revision_seq(repo.revisions_of(item_ref))
    assert after == before                        # 锚不跳变 → 投影不判 stale → 体检不消失


# ---- A3 借表外溢根治：背书伪修订不进诊断提示词 ----

class _CapturingDiagnoser:
    """捕获喂给模型的 revisions（诊断提示词 user 块「字段修订记录」段的来源）。"""

    def __init__(self):
        self.revisions = None

    def diagnose(self, project_ref, diagnosis_mode, item, sources, raw_text,
                 revisions, prior_findings, excluded_points=None, thread_context="",
                 business_sources=None, attestation=None):
        from app.adapters.llm import DiagnosedFinding, ItemVerdictOutcome
        self.revisions = revisions
        return ItemVerdictOutcome(
            verdict_kind="pass", verdict_summary="ok",
            findings=(DiagnosedFinding("no_blocker", "通过", "b"),),
            revision_points=(), supplement_gaps=(), basis="ok",
        )


def _apply_expression_revision(session, w, item_ref, value):
    build_sql_requirement_item_service(session).apply_item_revision(ItemRevisionCommand(
        project_ref=w["project"], item_ref=item_ref, workspace_version=_version(session, w),
        revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
        revised_value=value, operator_ref="U1", idempotency_key=f"rev-{uuid.uuid4()}"))
    session.commit()


def _capture_prompt_revisions(session, w, item_ref):
    cap = _CapturingDiagnoser()
    review = build_sql_item_review_service(session, auto_complete=True, item_diagnoser=cap)
    review.start_item_diagnosis(_diag_command(session, w, item_refs=[item_ref], mode="standard"))
    session.commit()
    assert cap.revisions is not None
    return [r["field_key"] for r in cap.revisions]


def test_attestation_excluded_from_diagnosis_prompt_but_real_revisions_kept(session):
    """A3：背书条目诊断提示词 user 块不再含 source_attestation 伪修订行；真实内容修订仍在。"""
    svc, w, item_ref, _ = _seed_supplement_pending(session)
    _attest(svc, session, w, item_ref)
    _apply_expression_revision(session, w, item_ref, "系统应支持把需求清单导出为 docx 文件")

    keys = _capture_prompt_revisions(session, w, item_ref)
    assert "source_attestation" not in keys   # 背书伪修订被过滤
    assert "expression" in keys               # 真实修订保留


def test_no_attestation_item_prompt_revisions_unchanged(session):
    """A3 反面：无背书条目的提示词 revisions 是纯 no-op 过滤（逐字节不变）——真实修订原样透传。"""
    w = _seed_pending_items(session)
    item_ref = w["items"][0]
    _apply_expression_revision(session, w, item_ref, "系统应支持导出 docx 文件并给出验收口径")

    assert _capture_prompt_revisions(session, w, item_ref) == ["expression"]
