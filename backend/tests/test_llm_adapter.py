"""模型服务适配器单测（httpx.MockTransport，无网络）：解析/映射/围栏/失败路径。"""
import json

import httpx

from app.adapters.llm import (
    _INTAKE_OUTPUT,
    LlmClient,
    LlmSourceElementRecognizer,
    LlmSourceIntakeJudge,
    StubSourceIntakeJudge,
)
from app.adapters.prompts.environment import dumps as prompt_dumps
from app.adapters.prompts.environment import render_pair
from app.domain.enums import ModelJudgement, ModelVerdict


def _judge(content: str | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmSourceIntakeJudge(LlmClient("http://test/v1", "qwen2.5", client=http))


def test_acceptable_parsed():
    j = _judge('{"judgement":"acceptable","basis":"含明确需求"}').judge("PRJ", "系统应支持导出", "访谈")
    assert j.judgement is ModelJudgement.ACCEPTABLE
    assert "需求" in j.basis


def test_chinese_category_mapped():
    j = _judge('{"judgement":"内容不足","basis":"太短"}').judge("PRJ", "x", "")
    assert j.judgement is ModelJudgement.INSUFFICIENT_CONTENT


def test_fenced_json_tolerated():
    j = _judge('```json\n{"judgement":"no_asset_value","basis":"闲聊"}\n```').judge("PRJ", "hi", "")
    assert j.judgement is ModelJudgement.NO_ASSET_VALUE


def test_http_error_to_failed():
    j = _judge(status=500).judge("PRJ", "x", "")
    assert j.judgement is ModelJudgement.JUDGEMENT_FAILED


def test_unparseable_to_failed():
    j = _judge("完全不是 JSON").judge("PRJ", "x", "")
    assert j.judgement is ModelJudgement.JUDGEMENT_FAILED


def test_unknown_category_to_failed():
    j = _judge('{"judgement":"maybe","basis":"?"}').judge("PRJ", "x", "")
    assert j.judgement is ModelJudgement.JUDGEMENT_FAILED


def test_stub_returns_canned():
    assert StubSourceIntakeJudge().judge("PRJ", "x", "").judgement is ModelJudgement.ACCEPTABLE


def test_intake_prompt_renders_via_template_mechanism():
    # 模板缺失/变量改名会在这里暴露（StrictUndefined + 契约常量注入）。
    system, user = render_pair(
        "source_intake", project_ref="PRJ", source_note="访谈",
        raw_text="系统应支持导出", output_schema=prompt_dumps(_INTAKE_OUTPUT),
    )
    assert "acceptable" in system
    assert "PRJ" in user and "访谈" in user and "系统应支持导出" in user


# ---- 条目诊断适配器（SCN-003 v5：结论对象/聚合守卫/可合成性/失败路径） ----

from app.adapters.llm import (  # noqa: E402
    _DIAGNOSIS_OUTPUT,
    LlmChartSourceSuggester,
    LlmItemDraftComposer,
    LlmRequirementItemDiagnoser,
    LlmRequirementItemFormatter,
    StubItemDraftComposer,
    StubItemReevalResponder,
    StubRequirementItemDiagnoser,
)


def _diagnoser(content: str | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmRequirementItemDiagnoser(LlmClient("http://test/v1", "qwen2.5", client=http))


_ITEM = {"item_ref": "I1", "req_no": "REQ-001", "expression": "系统应支持导出", "req_type": "functional"}


def _diag(content=None, status=200):
    return _diagnoser(content, status).diagnose("PRJ", "standard", _ITEM, [], "", [], [])


def test_diagnoser_parses_verdict_with_points():
    outcome = _diag(
        '{"verdict_kind":"revise","verdict_summary":"缺可验证口径，建议按点修订。",'
        '"findings":[{"finding_type":"untestable","diagnosis_summary":"缺可验证口径","basis_summary":"无阈值"}],'
        '"revision_points":[{"label":"补口径","finding_index":0,'
        '"find":"系统应支持导出","replace":"系统应支持导出，超时不超过五秒","basis":"来源阈值"}],'
        '"supplement_gaps":[]}'
    )
    assert not outcome.failed
    assert outcome.verdict_kind == "revise"
    assert len(outcome.findings) == 1
    assert outcome.revision_points[0]["point_ref"] == "P1"


def test_diagnoser_guard_rejects_pass_with_points():
    outcome = _diag(
        '{"verdict_kind":"pass","verdict_summary":"通过",'
        '"findings":[{"finding_type":"no_blocker","diagnosis_summary":"ok","basis_summary":""}],'
        '"revision_points":[{"label":"x","finding_index":0,"find":"系统应支持导出","replace":"y"}],'
        '"supplement_gaps":[]}'
    )
    assert outcome.failed  # 聚合守卫：pass 不得携带修订点


def test_diagnoser_guard_rejects_uncomposable_points():
    outcome = _diag(
        '{"verdict_kind":"revise","verdict_summary":"改",'
        '"findings":[{"finding_type":"untestable","diagnosis_summary":"x","basis_summary":""}],'
        '"revision_points":[{"label":"a","finding_index":0,"find":"不存在的片段","replace":"y"}],'
        '"supplement_gaps":[]}'
    )
    assert outcome.failed  # 可合成性：find 不在基准表达中


def test_diagnoser_http_error_failed():
    assert _diag(status=500).failed


def test_diagnoser_unparseable_failed():
    assert _diag("不是 JSON").failed


def test_stub_diagnoser_deterministic_paths():
    stub = StubRequirementItemDiagnoser()
    first = stub.diagnose("PRJ", "standard", _ITEM, [], "", [], [])
    assert first.verdict_kind == "revise" and first.revision_points
    revised = dict(_ITEM, expression="系统应支持导出，并明确验收观察口径。")
    second = stub.diagnose("PRJ", "incremental", revised, [], "", [], [])
    assert second.verdict_kind == "pass"
    withdraw = stub.diagnose("PRJ", "standard", dict(_ITEM, expression="重复条目应撤回"), [], "", [], [])
    assert withdraw.verdict_kind == "withdraw"
    supplement = stub.diagnose("PRJ", "standard", dict(_ITEM, expression="缺来源的表达"), [], "", [], [])
    assert supplement.verdict_kind == "supplement" and supplement.supplement_gaps
    # 只有「采纳修订时未勾选的点」（kind == excluded_point）才让 stub 判「尊重已排除点、建议通过」。
    # 被否决的问题（kind == vetoed_finding）走同一上下文通道，但不当作判通过依据（冷审查 C2）。
    respected = stub.diagnose("PRJ", "incremental", _ITEM, [], "", [], [],
                              excluded_points=[{"kind": "excluded_point", "label": "补口径"}])
    assert respected.verdict_kind == "pass"  # 尊重已排除点，不重复纠缠
    not_a_veto_pass = stub.diagnose("PRJ", "incremental", _ITEM, [], "", [], [],
                                    excluded_points=[{"kind": "vetoed_finding", "rule_code": "INCOSE-R7"}])
    assert not_a_veto_pass.verdict_kind == "revise"  # 否决不等于「整条没问题」，照常报其余问题


def test_stub_dialogue_adapters():
    reeval = StubItemReevalResponder()
    maintain = reeval.reeval(_ITEM, {"verdict_summary": "s"}, "太严了", [], "")
    assert maintain.action == "maintain" and maintain.explanation
    supersede = reeval.reeval(_ITEM, {"verdict_summary": "s"}, "请改判", [], "")
    assert supersede.action == "supersede" and supersede.verdict.verdict_kind == "pass"
    draft = StubItemDraftComposer().compose(_ITEM, [], "改成：加一句", None)
    assert not draft.failed and "加一句" in draft.proposed_value


def test_item_diagnosis_prompt_renders_via_template_mechanism():
    system, user = render_pair(
        "item_diagnosis", project_ref="PRJ", diagnosis_mode="standard", item="{}",
        sources="[]", business_sources="[]", raw_text="原文", revisions="[]",
        attestation="", prior_findings="[]",
        excluded_points="[]", thread_context="（无）",
        output_schema=prompt_dumps(_DIAGNOSIS_OUTPUT),
    )
    assert "verdict_kind" in system
    assert "PRJ" in user and "standard" in user


# ---- 迁移新增：结论对象拒绝通道（cannot_comply）与旧裸数组兼容 ----


def _formatter(content: str | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmRequirementItemFormatter(LlmClient("http://test/v1", "qwen2.5", client=http))


_ELEMENTS = [{"id": "E1", "element_type": "functional_requirement",
              "content": "导出报表", "source_quote": "导出报表"}]


def test_formatter_parses_conclusion_object():
    out = _formatter(
        '{"status":"done","items":[{"element_ref":"E1","expression":"系统应支持导出报表。"}]}'
    ).format_items("PRJ", "导出报表", _ELEMENTS)
    assert not out.failed and out.items[0].element_ref == "E1"


def test_formatter_tolerates_legacy_bare_array():
    out = _formatter(
        '[{"element_ref":"E1","expression":"系统应支持导出报表。"}]'
    ).format_items("PRJ", "导出报表", _ELEMENTS)
    assert not out.failed and out.items[0].expression == "系统应支持导出报表。"


def test_formatter_cannot_comply_surfaces_reason():
    out = _formatter(
        '{"status":"cannot_comply","reason":"要素内容整体缺失，无法格式化","items":[]}'
    ).format_items("PRJ", "", _ELEMENTS)
    assert out.failed and "要素内容整体缺失" in out.basis


def test_formatter_empty_items_not_silent():
    out = _formatter('{"status":"done","items":[]}').format_items("PRJ", "x", _ELEMENTS)
    assert out.failed and out.basis  # 空结果带可展示原因，不静默


# ---- 条目档案结构判定（增补 §2：只指缺、必须引证、服务端推导完备性）----

_TYPED_ELEMENTS = [{"id": "E1", "element_type": "quality_attribute",
                    "content": "查询要快", "source_quote": "查询要快",
                    "req_type": "quality"}]


def test_formatter_parses_profile_structure():
    out = _formatter(json.dumps({"status": "done", "items": [{
        "element_ref": "E1", "expression": "在正常运行状态下，系统应快速返回查询结果。",
        "statement_conformance": "deviates",
        "facet_findings": [
            {"facet": "stimulus", "status": "present", "evidence": "查询", "note": None},
            {"facet": "response", "status": "present", "evidence": "查询要快", "note": None},
            {"facet": "response_measure", "status": "missing", "evidence": None,
             "note": "未见量化阈值"},
            {"facet": "environment", "status": "missing", "evidence": None, "note": None},
        ],
        "payload_values": [
            {"field": "response", "value": "快速返回查询结果"},
            {"field": "response_measure", "value": None},
            {"field": "bogus", "value": "越档案字段丢弃"},
        ],
    }]}, ensure_ascii=False)).format_items("PRJ", "查询要快", _TYPED_ELEMENTS)
    item = out.items[0]
    assert item.req_type == "quality" and item.profile_version == 1
    assert item.statement_conformance == "deviates"
    assert {f.facet for f in item.facets} == {"stimulus", "response", "response_measure", "environment"}
    assert item.completeness == "incomplete"  # 必备 response_measure=missing → 服务端推导
    assert dict(item.payload_values) == {"response": "快速返回查询结果", "response_measure": None}


def test_formatter_structure_degrades_not_fails():
    """present 无证据/越档案 key/无 req_type：结构判定降级为空，表达承接不受影响。"""
    out = _formatter(json.dumps({"status": "done", "items": [{
        "element_ref": "E1", "expression": "系统应快速返回查询结果。",
        "statement_conformance": "conforms",
        "facet_findings": [
            {"facet": "response", "status": "present", "evidence": ""},  # 无证据不承接
            {"facet": "nonsense", "status": "missing"},                   # 越档案 key
        ],
    }]}, ensure_ascii=False)).format_items("PRJ", "查询要快", _TYPED_ELEMENTS)
    item = out.items[0]
    assert not item.failed if hasattr(item, "failed") else True
    assert item.expression and item.facets == () and item.completeness is None
    # 未映射 req_type 的输入：整体无结构判定
    out2 = _formatter(json.dumps({"status": "done", "items": [{
        "element_ref": "E1", "expression": "系统应快速返回查询结果。",
        "facet_findings": [{"facet": "response", "status": "missing"}],
    }]}, ensure_ascii=False)).format_items("PRJ", "查询要快", _ELEMENTS)
    assert out2.items[0].facets == () and out2.items[0].profile_version is None


def test_stub_formatter_emits_profile_structure():
    from app.adapters.llm import StubRequirementItemFormatter
    out = StubRequirementItemFormatter().format_items("PRJ", "查询要快", _TYPED_ELEMENTS)
    item = out.items[0]
    assert item.req_type == "quality" and item.facets and item.completeness == "incomplete"


def _composer(content: str | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmItemDraftComposer(LlmClient("http://test/v1", "qwen2.5", client=http))


def test_draft_composer_done_parsed():
    out = _composer('{"status":"done","proposed_value":"系统应支持导出，超时不超过五秒。","note":""}')\
        .compose(_ITEM, [], "补时限", None)
    assert not out.failed and "五秒" in out.proposed_value and out.reason == ""


def test_draft_composer_cannot_comply_carries_reason():
    out = _composer('{"status":"cannot_comply","reason":"该意图与本条目表达无关，无法起草"}')\
        .compose(_ITEM, [], "讲个笑话", None)
    assert not out.failed and out.proposed_value == ""
    assert "无法起草" in out.reason  # 拒绝通道：原因直接展示给用户


def test_draft_composer_empty_value_still_failed():
    assert _composer('{"status":"done","proposed_value":""}').compose(_ITEM, [], "x", None).failed


# ---- 为条目找候选来源 lane（issue #30；A2 装配完整 + 候选只从差集选）----


def _source_finder(content: str | None = None, status: int = 200):
    from app.adapters.llm import LlmItemSourceCandidateComposer

    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmItemSourceCandidateComposer(LlmClient("http://test/v1", "qwen2.5", client=http))


_CANDIDATES = [
    {"id": "E1", "element_type": "business_rule", "content": "大额订单需人工审核",
     "source_quote": "订单金额超过 500 元时应人工审核"},
    {"id": "E2", "element_type": "functional_requirement", "content": "订单可导出",
     "source_quote": "系统应支持导出订单"},
]


def test_source_finder_ranks_candidates_from_pool():
    out = _source_finder(json.dumps({"status": "done", "candidates": [
        {"element_id": "E2", "reason": "次相关", "rank": 2},
        {"element_id": "E1", "reason": "讲的正是大额订单审核", "rank": 1},
    ]}, ensure_ascii=False)).find(_ITEM, _CANDIDATES)
    assert not out.failed
    # 按 rank 升序：E1（rank1）在前
    assert [c["element_id"] for c in out.candidates] == ["E1", "E2"]
    assert out.candidates[0]["reason"] == "讲的正是大额订单审核"


def test_source_finder_drops_hallucinated_ids():
    """候选只能来自输入差集：不在候选集里的 id 一律丢弃（依据可追溯纪律）。"""
    out = _source_finder(json.dumps({"status": "done", "candidates": [
        {"element_id": "E1", "reason": "真实候选", "rank": 1},
        {"element_id": "E_GHOST", "reason": "模型自拟不存在的来源", "rank": 2},
    ]}, ensure_ascii=False)).find(_ITEM, _CANDIDATES)
    assert [c["element_id"] for c in out.candidates] == ["E1"]  # 幻觉 id 被丢弃


def test_source_finder_cannot_comply_surfaces_reason():
    out = _source_finder(json.dumps({
        "status": "cannot_comply",
        "reason": "候选要素讲的都是导出，与本条大额审核不是同一件事",
    }, ensure_ascii=False)).find(_ITEM, _CANDIDATES)
    assert not out.failed and out.candidates == ()
    assert "不是同一件事" in out.reason


def test_source_finder_all_hallucinated_becomes_cannot_comply():
    """全部 id 不在差集内 → 无有效候选，等同 cannot_comply，不静默返回空成功。"""
    out = _source_finder(json.dumps({"status": "done", "candidates": [
        {"element_id": "E_GHOST", "reason": "x", "rank": 1},
    ]}, ensure_ascii=False)).find(_ITEM, _CANDIDATES)
    assert not out.failed and out.candidates == () and out.reason


def test_source_finder_infra_failure_flagged():
    assert _source_finder(None, status=500).find(_ITEM, _CANDIDATES).failed


def test_stub_source_finder_returns_pool_and_rejects_empty():
    from app.adapters.llm import StubItemSourceCandidateComposer, build_item_source_candidate_composer
    from app.config import Settings

    stub = StubItemSourceCandidateComposer()
    out = stub.find(_ITEM, _CANDIDATES)
    assert not out.failed and [c["element_id"] for c in out.candidates] == ["E1", "E2"]
    empty = stub.find(_ITEM, [])
    assert empty.candidates == () and empty.reason  # 空差集走 cannot_comply
    # A2 builder：有 base_url → 真实实现；无 → 桩
    from app.adapters.llm import LlmItemSourceCandidateComposer
    real = build_item_source_candidate_composer(Settings(llm_base_url="http://x/v1"))
    assert isinstance(real, LlmItemSourceCandidateComposer)
    assert isinstance(build_item_source_candidate_composer(Settings(llm_base_url=None)),
                      StubItemSourceCandidateComposer)


def _suggester(content: str | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmChartSourceSuggester(LlmClient("http://test/v1", "qwen2.5", client=http))


_CHART = {"title": "导出流程", "chart_type": "flowchart", "format": "mermaid"}


def test_chart_suggester_done_parsed():
    out = _suggester('{"status":"done","source_code":"flowchart TD\\n  A-->B","explanation":"覆盖 REQ-001"}')\
        .suggest("PRJ", _CHART, [], "", "")
    assert not out.failed and out.proposal.source_code.startswith("flowchart")


def test_chart_suggester_cannot_comply_surfaces_reason():
    out = _suggester('{"status":"cannot_comply","reason":"意图要求的分支在来源条目中不存在"}')\
        .suggest("PRJ", _CHART, [], "", "补一个来源里没有的分支")
    assert out.failed and out.proposal is None
    assert "来源条目中不存在" in out.basis  # reason 经 basis 停靠给用户


def test_max_tokens_clamp_is_logged_at_info_while_exhaustion_stays_warn(monkeypatch):
    """按窗口卡住输出上限记 INFO，提示词把窗口占满记 WARN——两者的级别不能一样。

    出厂默认的输出上限（131072）远大于任何本地端点的窗口，所以应用能力档案之后**每一次**调用
    都会走一次钳制。把这条常态行为记成 WARN，运行态诊断中心（只收 WARN 与 ERROR，按事件码累计
    计数）会被它长期占住首位，真正的问题被挤下去。提示词已经把窗口占满则确属异常，仍记 WARN。
    """
    from app.adapters import llm as llm_module
    from app.adapters.llm import CAP_STATE_SUPPORTED, CapabilityProfile, LlmClient

    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        llm_module, "log_event",
        lambda component, event, level="INFO", **kw: events.append((event, level)),
    )
    profile = CapabilityProfile(context_state=CAP_STATE_SUPPORTED, context_tokens=1000,
                                probed_at="2026-07-24T19:30:00+08:00")
    client = LlmClient("http://test/v1", "m", max_tokens=4096, context_tokens=1000,
                       capability_profile=profile)

    assert client._effective_max_tokens("系统提示", "用户输入") < 4096
    assert events == [("llm.max_tokens.clamped", "INFO")]

    events.clear()
    # 提示词长到把 1000 的窗口占满：这才是该报警的那一种
    assert client._effective_max_tokens("系统提示", "很长的输入" * 500) == 256
    assert events == [("llm.max_tokens.context_exhausted", "WARN")]


# ---- 知识项识别：裁定理由解析与回落（T20260724-suspected-noise-triage）----

def _recognizer(content: str | None = None, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmSourceElementRecognizer(LlmClient("http://test/v1", "qwen2.5", client=http))


def _one(items: list[dict]):
    return _recognizer(json.dumps(items, ensure_ascii=False)).recognize("PRJ", "原文", "访谈")


def test_recognition_verdict_reason_parsed():
    r = _one([{
        "element_type": "term", "content": "感谢各位抽空参加", "source_anchor": "感谢各位抽空参加",
        "confidence": 0.2, "model_verdict": "suspected_noise",
        "verdict_reason": "这句是会议开场的客套话，没有表述任何系统约束",
    }])
    assert r.elements[0].verdict is ModelVerdict.SUSPECTED_NOISE
    assert r.elements[0].verdict_reason == "这句是会议开场的客套话，没有表述任何系统约束"


def test_recognition_verdict_reason_missing_or_blank_to_none():
    """模型漏给或给空白 → None（读侧回落通用判据，不伪造理由）。"""
    r = _one([
        {"element_type": "term", "content": "甲", "model_verdict": "suspected_noise"},
        {"element_type": "term", "content": "乙", "model_verdict": "suspected_noise",
         "verdict_reason": "   "},
    ])
    assert [e.verdict_reason for e in r.elements] == [None, None]


def test_recognition_verdict_reason_truncated():
    """超长理由截断到 500 字（同 source_anchor 口径），不让模型的长篇撑爆列位。"""
    r = _one([{"element_type": "term", "content": "甲", "model_verdict": "suspected_noise",
               "verdict_reason": "很" * 900}])
    assert len(r.elements[0].verdict_reason) == 500


def test_recognition_verdict_falls_back_to_processable():
    """越界裁定码回落 processable（既有语义，随本卡钉住不变）。"""
    r = _one([{"element_type": "term", "content": "甲", "model_verdict": "不认识的码"}])
    assert r.elements[0].verdict is ModelVerdict.PROCESSABLE


def test_recognition_logs_verdict_reason_coverage_counts(monkeypatch):
    """理由缺失回落的观测口（冷审查裁定 L1）：按批记两个计数，不记要素内容与理由全文。

    卡尾遗留问题正是「真实模型的理由遵循率存疑」，而「非 processable 的裁定里有多少条模型没给
    理由」是量化它的唯一观测口——没有这条日志，一条数据都拿不到。
    """
    from app.adapters import llm as llm_module

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        llm_module, "log_event",
        lambda component, event, level="INFO", **kw: events.append((event, kw)),
    )
    _one([
        {"element_type": "term", "content": "甲", "model_verdict": "processable"},
        {"element_type": "term", "content": "乙", "model_verdict": "suspected_noise",
         "verdict_reason": "会议开场客套话"},
        {"element_type": "term", "content": "丙", "model_verdict": "suspected_noise"},
        {"element_type": "term", "content": "丁", "model_verdict": "suspected_needs_supplement"},
    ])
    coverage = [kw for event, kw in events if event == "element.recognition.verdict_reason_coverage"]
    assert len(coverage) == 1, "按批发一条，不逐条发"
    assert coverage[0]["non_processable_count"] == 3
    assert coverage[0]["missing_reason_count"] == 2
    # 只记计数：日志字段里不出现要素内容与理由全文
    assert "会议开场客套话" not in json.dumps(coverage[0], ensure_ascii=False)


def test_recognition_verdict_reason_coverage_log_absent_when_all_processable(monkeypatch):
    """全是可处理裁定时不发这条日志——没有该给理由的条目，发一条恒为零的记录只是噪声。"""
    from app.adapters import llm as llm_module

    events: list[str] = []
    monkeypatch.setattr(
        llm_module, "log_event",
        lambda component, event, level="INFO", **kw: events.append(event),
    )
    _one([{"element_type": "term", "content": "甲", "model_verdict": "processable"}])
    assert "element.recognition.verdict_reason_coverage" not in events


def test_recognition_legacy_chinese_verdict_label_still_maps():
    """旧中文标签「疑似误识别」仍要认：提示词换口径后模型可能沿用，认不出会静默变成可处理。"""
    r = _one([{"element_type": "term", "content": "甲", "model_verdict": "疑似误识别"}])
    assert r.elements[0].verdict is ModelVerdict.SUSPECTED_NOISE


# ---- 人工确认背书：服务层 → 适配器 → 提示词的接缝（冷审查 V2(b) 补测） ----


def _capture_diagnoser(sink: list[dict]):
    """收下真实请求体的诊断器：两侧各自被测，中间这一步此前没有任何用例覆盖。

    逃逸变异（B 车道实证）：删掉 `if attestation else ""`，`json.dumps(None)` 得到字符串
    "null"——它是真值，于是**每一条无背书条目**的 user 块都会渲染出「人工确认来源（JSON…）：
    null」，A1 直接破功，而既有的真调用用例都不检查请求消息体的 user 文本，全套仍绿。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        sink.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "verdict_kind": "pass", "verdict_summary": "未发现阻断问题，建议通过。",
            "findings": [{"finding_type": "no_blocker", "diagnosis_summary": "无阻断问题",
                          "basis_summary": "来源可定位"}],
            "revision_points": [], "supplement_gaps": [],
        }, ensure_ascii=False)}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmRequirementItemDiagnoser(LlmClient("http://test/v1", "qwen2.5", client=http))


def _user_text(body: dict) -> str:
    return next(m["content"] for m in body["messages"] if m["role"] == "user")


def test_diagnose_without_attestation_sends_no_attestation_section():
    sink: list[dict] = []
    _capture_diagnoser(sink).diagnose("PRJ", "standard", _ITEM, [], "", [], [], attestation=None)
    user = _user_text(sink[0])
    # 钉段落标题而非「人工确认来源」四字：后者在材料原文那行的证据封闭列举里对所有条目常在
    assert "人工确认来源（JSON" not in user
    assert "null" not in user  # 传 None 而非 "null" 是这一步的全部要点


def test_diagnose_with_attestation_sends_reason_verbatim():
    sink: list[dict] = []
    _capture_diagnoser(sink).diagnose(
        "PRJ", "standard", _ITEM, [], "", [], [],
        attestation={"reason": "客户在启动会上口头提出，纪要未记录",
                     "operator_ref": "u-analyst-1", "at": "2026-07-25T02:00:00Z"},
    )
    user = _user_text(sink[0])
    assert "人工确认来源" in user
    assert "客户在启动会上口头提出，纪要未记录" in user  # 理由原文逐字，不摘编
    assert "u-analyst-1" in user
