"""TC-06 要素完备性判据 P1：rubric 加载/注入/解析/降级 + 工作区投影 + 门禁不受阻。

设计事实源：docs/40-detailed-design/domains/DS-001-需求形成域/要素完备性判据与诊断投影.md
§1（判据）、§2（诊断结构：只准指缺、必须引证、正确性完备性分列）、§4（徽章与不硬卡确认）。
"""
import json

import httpx

from app.adapters.llm import (
    LlmClient,
    LlmElementReviewer,
    RecognizedElement,
    StubElementReviewer,
    StubSourceElementRecognizer,
    _format_rubrics,
)
from app.api.schemas import (
    ElementDecisionCommand,
    ElementRecognitionCommand,
    ElementReviewCommand,
)
from app.domain.enums import ElementType, ModelVerdict
from app.domain.enums import ElementProcessStatus as ES
from app.domain.rubrics import all_rubrics, get_rubric
from app.repositories.in_memory import build_analysis_wiring

# ============================================================================
# §1 判据层：加载、校验、降级
# ============================================================================

CORE_TYPES = (
    "functional_requirement", "quality_attribute", "constraint",
    "data_requirement", "interface_requirement",
)


def test_all_element_types_covered():
    """TC-06 首批 5 类 + TC-07 第二批 6 类 = 全部 11 类 ElementType。"""
    rubrics = all_rubrics()
    assert set(rubrics) == {t.value for t in ElementType}
    for r in rubrics.values():
        assert r.rubric_version >= 1
        assert r.facets and all(f.key.isascii() for f in r.facets)
        assert all(f.criteria and f.revision_hint for f in r.facets)
    # 第二批为支撑/上下文类（含 P3 business_rule）：宽松基调，必备面向 ≤ 4 且各有至少 2 个必备
    for t in ("goal", "scenario", "term", "assumption", "business_rule", "role", "external_system"):
        required = [f for f in rubrics[t].facets if f.required]
        assert 2 <= len(required) <= 4


def test_business_rule_facets(session=None):
    """P3：business_rule 必备面向 ⊇ {规则陈述, 出处或授权依据, 作用范围}（04 §3）。"""
    r = get_rubric("business_rule")
    assert r is not None
    labels = {f.label for f in r.facets if f.required}
    assert {"规则陈述", "出处或授权依据", "作用范围"} <= labels


def test_unknown_type_returns_none():
    """枚举外/未知类型走通用复核降级（降级路径长期保留）。"""
    assert get_rubric("no_such_type") is None
    assert get_rubric("") is None


def test_completeness_requires_all_required_present():
    r = get_rubric("quality_attribute")
    assert r.completeness_of({"stimulus": "present", "response": "present",
                              "response_measure": "missing"}) == "incomplete"
    # environment 为增强面向，缺失不影响完备
    assert r.completeness_of({"stimulus": "present", "response": "present",
                              "response_measure": "present"}) == "complete"


def test_format_rubrics_injects_only_covered_types():
    text = _format_rubrics([
        {"id": "e1", "element_type": "quality_attribute"},
        {"id": "e2", "element_type": "no_such_type"},
    ])
    assert "quality_attribute 完备性判据" in text
    assert "response_measure" in text
    assert "no_such_type" not in text


def test_format_rubrics_all_uncovered_degrades():
    text = _format_rubrics([{"id": "e1", "element_type": "no_such_type"}])
    assert "无判据" in text


# ============================================================================
# 判据驱动 N/A 通道（T20260714-completeness-na-gate）
# ============================================================================


def test_lifecycle_facet_declares_applicability_others_none():
    """首批只 lifecycle_or_volume 声明适用性；其余成分未声明（行为零变）。"""
    r = get_rubric("data_requirement")
    assert r.facet("lifecycle_or_volume").applicability
    assert r.facet("data_object").applicability is None
    assert r.facet("key_attributes").applicability is None


def test_completeness_na_treated_as_satisfied():
    """not_applicable 视同满足，不计缺口（值域/枚举类数据需求重诊→完备）。"""
    r = get_rubric("data_requirement")
    assert r.completeness_of({
        "data_object": "present", "key_attributes": "present",
        "lifecycle_or_volume": "not_applicable",
    }) == "complete"
    # 反向：真缺失仍不完备
    assert r.completeness_of({
        "data_object": "present", "key_attributes": "present",
        "lifecycle_or_volume": "missing",
    }) == "incomplete"


def test_format_rubrics_injects_applicability_text():
    text = _format_rubrics([{"id": "e1", "element_type": "data_requirement"}])
    assert "适用性" in text and "not_applicable" in text


_DATA_TARGET = [{"id": "e1", "element_type": "data_requirement", "content": "任务状态枚举：待命、运行中、已完成"}]


def _data_finding_json(facet_findings):
    return json.dumps([{
        "element_ref": "e1", "conclusion": "pass", "opinion": "值域定义",
        "correctness": "consistent_with_source", "facet_findings": facet_findings,
    }], ensure_ascii=False)


def test_na_accepted_only_for_declared_facet_with_reason():
    """回归锚：值域/枚举型数据需求——lifecycle_or_volume 判 N/A（带理由）→完备度不因此出缺口。"""
    content = _data_finding_json([
        {"facet": "data_object", "status": "present", "evidence": "任务状态枚举"},
        {"facet": "key_attributes", "status": "present", "evidence": "待命、运行中、已完成"},
        {"facet": "lifecycle_or_volume", "status": "not_applicable", "note": "值域定义无存储维度"},
    ])
    f = _reviewer(content).review_elements("P", "原文", "", _DATA_TARGET, "").findings[0]
    assert {x.facet: x.status for x in f.facets}["lifecycle_or_volume"] == "not_applicable"
    assert f.completeness == "complete"  # N/A 不计缺口


def test_na_without_reason_dropped():
    """N/A 须给判定理由（note）；缺 note 丢弃 → 必备面向未全判定 → completeness 空。"""
    content = _data_finding_json([
        {"facet": "data_object", "status": "present", "evidence": "任务状态枚举"},
        {"facet": "key_attributes", "status": "present", "evidence": "待命"},
        {"facet": "lifecycle_or_volume", "status": "not_applicable", "note": None},
    ])
    f = _reviewer(content).review_elements("P", "原文", "", _DATA_TARGET, "").findings[0]
    assert "lifecycle_or_volume" not in {x.facet for x in f.facets}
    assert f.completeness is None


def test_na_rejected_for_undeclared_facet():
    """未声明适用性的成分不得裁 N/A（行为零变）→ 该判定丢弃。"""
    content = _data_finding_json([
        {"facet": "data_object", "status": "not_applicable", "note": "试图对未声明成分判N/A"},
        {"facet": "key_attributes", "status": "present", "evidence": "待命"},
        {"facet": "lifecycle_or_volume", "status": "present", "evidence": "保存5年"},
    ])
    f = _reviewer(content).review_elements("P", "原文", "", _DATA_TARGET, "").findings[0]
    assert "data_object" not in {x.facet for x in f.facets}


# ============================================================================
# §2 诊断层：LLM 解析（只准指缺、必须引证、服务端推导完备性）
# ============================================================================


def _reviewer(content: str | None = None, status: int = 200) -> LlmElementReviewer:
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmElementReviewer(LlmClient("http://test/v1", "qwen2.5", client=http))


_QA_TARGET = [{"id": "e1", "element_type": "quality_attribute", "content": "查询要快"}]


def _finding_json(facet_findings, correctness="consistent_with_source"):
    return json.dumps([{
        "element_ref": "e1", "conclusion": "needs_revision", "opinion": "缺量化阈值",
        "revised_content": None, "correctness": correctness,
        "facet_findings": facet_findings,
    }], ensure_ascii=False)


def test_llm_facets_parsed_and_completeness_derived():
    content = _finding_json([
        {"facet": "stimulus", "status": "present", "evidence": "查询要快", "note": None},
        {"facet": "response", "status": "present", "evidence": "查询要快", "note": None},
        {"facet": "response_measure", "status": "missing", "evidence": None, "note": "未见量化阈值"},
        {"facet": "environment", "status": "missing", "evidence": None, "note": "未说明环境"},
    ])
    out = _reviewer(content).review_elements("P", "原文", "", _QA_TARGET, "")
    assert not out.failed
    f = out.findings[0]
    assert f.correctness == "consistent_with_source"
    assert f.completeness == "incomplete"  # 服务端由必备面向推导
    assert f.rubric_version == get_rubric("quality_attribute").rubric_version
    assert {x.facet: x.status for x in f.facets}["response_measure"] == "missing"


def test_present_without_evidence_rejected():
    """只准指缺：present 无逐字证据不承接该面向 → 必备面向未全判定 → completeness 为空。"""
    content = _finding_json([
        {"facet": "stimulus", "status": "present", "evidence": None},
        {"facet": "response", "status": "present", "evidence": "查询要快"},
        {"facet": "response_measure", "status": "missing", "note": "缺阈值"},
    ])
    f = _reviewer(content).review_elements("P", "原文", "", _QA_TARGET, "").findings[0]
    assert "stimulus" not in {x.facet for x in f.facets}
    assert f.completeness is None


def test_unknown_facet_and_bad_status_dropped():
    content = _finding_json([
        {"facet": "made_up_key", "status": "present", "evidence": "x"},
        {"facet": "response_measure", "status": "kind_of", "evidence": "x"},
    ])
    f = _reviewer(content).review_elements("P", "原文", "", _QA_TARGET, "").findings[0]
    assert f.facets == () and f.completeness is None and f.rubric_version is None


def test_invalid_correctness_dropped():
    content = _finding_json(
        [{"facet": "response_measure", "status": "missing", "note": "缺阈值"}],
        correctness="looks_fine",
    )
    f = _reviewer(content).review_elements("P", "原文", "", _QA_TARGET, "").findings[0]
    assert f.correctness is None


def test_facet_garbage_degrades_not_fails():
    """facet_findings 整体是坏结构 → 结论仍承接，完备度降级为空（不阻断复核）。"""
    content = json.dumps([{
        "element_ref": "e1", "conclusion": "pass", "opinion": "ok",
        "facet_findings": "not-a-list",
    }])
    out = _reviewer(content).review_elements("P", "原文", "", _QA_TARGET, "")
    assert not out.failed and out.findings[0].facets == ()


def test_uncovered_type_no_facets_even_if_model_returns_them():
    content = json.dumps([{
        "element_ref": "e1", "conclusion": "pass", "opinion": "ok",
        "facet_findings": [{"facet": "trigger", "status": "missing"}],
    }])
    target = [{"id": "e1", "element_type": "no_such_type", "content": "客户"}]
    f = _reviewer(content).review_elements("P", "原文", "", target, "").findings[0]
    assert f.facets == () and f.rubric_version is None


def test_bad_json_fails_review_docking():
    out = _reviewer("完全不是 JSON").review_elements("P", "原文", "", _QA_TARGET, "")
    assert out.failed  # 失败停靠由 AEP-024 review_failed 分支承接，不伪造结论


# ============================================================================
# Stub：确定性完备度（无 LLM 环境同路径）
# ============================================================================


def test_stub_quality_attribute_measure_by_digit():
    out = StubElementReviewer().review_elements("P", "原文", "", [
        {"id": "e1", "element_type": "quality_attribute", "content": "系统查询要快"},
        {"id": "e2", "element_type": "quality_attribute", "content": "95%查询响应小于2秒"},
    ], "")
    by_ref = {f.element_ref: f for f in out.findings}
    assert by_ref["e1"].completeness == "incomplete"
    assert {x.facet: x.status for x in by_ref["e1"].facets}["response_measure"] == "missing"
    assert by_ref["e2"].completeness == "complete"


# ============================================================================
# §4 工作区投影 + 门禁不受阻（服务级，走 AEP-023/024 全链路）
# ============================================================================

RAW = "在月末结算期间系统查询要快。客户指开户主体。"

_ELEMENTS = (
    RecognizedElement(
        element_type=ElementType.QUALITY_ATTRIBUTE, content="在月末结算期间系统查询要快",
        source_anchor="在月末结算期间系统查询要快", confidence=0.9,
        verdict=ModelVerdict.PROCESSABLE,
    ),
    RecognizedElement(
        element_type=ElementType.TERM, content="客户指开户主体",
        source_anchor="客户指开户主体", confidence=0.9,
        verdict=ModelVerdict.PROCESSABLE,
    ),
)


def _facet_workspace():
    w = build_analysis_wiring(
        auto_complete=True,
        recognizer=StubSourceElementRecognizer(elements=_ELEMENTS),
    )
    w.source_assets.seed_material("M-1", raw_text=RAW, accepted=True)
    r = w.service.submit_element_recognition(ElementRecognitionCommand(
        project_ref="P-1", material_ref="M-1", operator_ref="U1", idempotency_key="K1",
    ))
    ctx = r.parse_context_ref
    ws = w.service.read_element_workspace(ctx)
    w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        target_element_refs=[e.id for e in ws.elements],
        review_intent="复核", operator_ref="U1", idempotency_key="KR1",
    ))
    return w, ctx, w.service.read_element_workspace(ctx)


def test_workspace_projects_facet_review_with_rubric_enrichment():
    _w, _ctx, ws = _facet_workspace()
    qa = next(e for e in ws.elements if e.element_type.value == "quality_attribute")
    term = next(e for e in ws.elements if e.element_type.value == "term")

    # TC-07：term 已有第二批判据 → 投影非空，label 来自判据，stub 全 present → complete
    assert term.facet_review is not None
    assert term.facet_review.completeness == "complete"
    assert {f.label for f in term.facet_review.facets} >= {"术语名", "定义"}

    fr = qa.facet_review
    assert fr is not None and fr.completeness == "incomplete"
    missing = next(f for f in fr.facets if f.facet_key == "response_measure")
    assert missing.status == "missing"
    assert missing.label == "响应度量"  # label 由服务端判据补齐
    assert missing.revision_hint  # 缺失面向带修订提示（来自判据，非模型生成）
    present = next(f for f in fr.facets if f.facet_key == "stimulus")
    assert present.revision_hint is None and present.evidence


def test_incomplete_does_not_block_confirmation():
    """§4：确认门禁不硬卡完备性——incomplete 仅提示，可带缺陷确认。"""
    w, ctx, ws = _facet_workspace()
    qa = next(e for e in ws.elements if e.element_type.value == "quality_attribute")
    assert qa.facet_review.completeness == "incomplete"
    assert qa.process_status is ES.PENDING_CONFIRMATION  # 复核是对话轮次，不迁移状态
    ws3 = w.service.decide_elements(ElementDecisionCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_refs=[qa.id], decision="confirm", operator_ref="U1", idempotency_key="KD1",
    ))
    qa2 = next(e for e in ws3.elements if e.id == qa.id)
    assert qa2.process_status is ES.CONFIRMED  # 不完备仍可确认（门禁未被 completeness 阻断）


def test_review_failure_no_facet_projection():
    w = build_analysis_wiring(
        auto_complete=True,
        recognizer=StubSourceElementRecognizer(elements=_ELEMENTS),
        reviewer=StubElementReviewer(failed=True),
    )
    w.source_assets.seed_material("M-1", raw_text=RAW, accepted=True)
    r = w.service.submit_element_recognition(ElementRecognitionCommand(
        project_ref="P-1", material_ref="M-1", operator_ref="U1", idempotency_key="K1",
    ))
    ctx = r.parse_context_ref
    ws = w.service.read_element_workspace(ctx)
    w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        target_element_refs=[ws.elements[0].id],
        review_intent="复核", operator_ref="U1", idempotency_key="KR1",
    ))
    ws2 = w.service.read_element_workspace(ctx)
    assert all(e.facet_review is None for e in ws2.elements)  # 失败不伪造完备度


# ============================================================================
# TC-08 §3 投影层：持久化、多轮不覆盖、版本失效、失败不写
# ============================================================================


def test_projection_multi_round_not_overwritten():
    """分两批复核不同要素后，两批要素的徽章同时可见（消除 P1『最新轮覆盖』简化）。"""
    w = build_analysis_wiring(
        auto_complete=True,
        recognizer=StubSourceElementRecognizer(elements=_ELEMENTS),
    )
    w.source_assets.seed_material("M-1", raw_text=RAW, accepted=True)
    r = w.service.submit_element_recognition(ElementRecognitionCommand(
        project_ref="P-1", material_ref="M-1", operator_ref="U1", idempotency_key="K1",
    ))
    ctx = r.parse_context_ref
    ws = w.service.read_element_workspace(ctx)
    qa = next(e for e in ws.elements if e.element_type.value == "quality_attribute")
    term = next(e for e in ws.elements if e.element_type.value == "term")

    # 第一批：只复核 qa
    w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        target_element_refs=[qa.id], review_intent="复核",
        operator_ref="U1", idempotency_key="KR1",
    ))
    # 第二批：只复核 term
    ws = w.service.read_element_workspace(ctx)
    w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        target_element_refs=[term.id], review_intent="复核",
        operator_ref="U1", idempotency_key="KR2",
    ))

    ws2 = w.service.read_element_workspace(ctx)
    qa2 = next(e for e in ws2.elements if e.id == qa.id)
    term2 = next(e for e in ws2.elements if e.id == term.id)
    assert qa2.facet_review is not None and term2.facet_review is not None
    assert qa2.facet_review.completeness == "incomplete"
    assert term2.facet_review.completeness == "complete"


def test_projection_stale_after_edit_and_refresh_after_rereview():
    """要素修订出新版本 → stale=true（待重诊）；重新复核 → 恢复。"""
    from app.api.schemas import ElementEditCommand

    w, ctx, ws = _facet_workspace()
    qa = next(e for e in ws.elements if e.element_type.value == "quality_attribute")
    assert qa.facet_review is not None and qa.facet_review.stale is False

    # 就地修订出新版本（须先脱离分析中：直接对 term 做即可？qa 在分析中不可编辑）
    term = next(e for e in ws.elements if e.element_type.value == "term")
    assert term.facet_review is not None and term.facet_review.stale is False
    ws = w.service.read_element_workspace(ctx)
    ws2 = w.service.edit_element(ElementEditCommand(
        parse_context_ref=ctx, workspace_version=ws.workspace_version,
        element_ref=term.id, edit_type="revise_expression",
        new_content="客户指与本行建立开户关系的主体",
        operator_ref="U1", idempotency_key="KE1",
    ))
    term2 = next(e for e in ws2.elements if e.id == term.id)
    assert term2.version == 2
    assert term2.facet_review is not None and term2.facet_review.stale is True

    # 重新复核 → 投影按新版本重写，stale 恢复
    w.service.submit_element_review(ElementReviewCommand(
        parse_context_ref=ctx, workspace_version=ws2.workspace_version,
        target_element_refs=[term.id], review_intent="重诊",
        operator_ref="U1", idempotency_key="KR9",
    ))
    ws3 = w.service.read_element_workspace(ctx)
    term3 = next(e for e in ws3.elements if e.id == term.id)
    assert term3.facet_review is not None and term3.facet_review.stale is False
