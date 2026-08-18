"""需求质量诊断器（v2 签名件）契约单测：质量元数据降级不拒收 + clamp + span 锚定 + 枚举校验。

覆盖 05 篇 AC-P1-02（降级不拒收）/AC-P1-03（prompt 循环注入）/测试义务 test_quality_diagnosis。
强不变式：既有 verdict/finding 三段校验与聚合守卫不受质量字段影响。
"""
import json

import httpx

from app.adapters.llm import (
    _DIAGNOSIS_OUTPUT,
    LlmClient,
    LlmRequirementItemDiagnoser,
    _sanitize_verdict,
)
from app.adapters.prompts.environment import dumps as prompt_dumps
from app.adapters.prompts.environment import render_pair

_BASE = "当订单实付金额 ≥ 500 元时，系统应尽快将订单转入人工审核队列。"


def _diagnoser(content: str):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmRequirementItemDiagnoser(LlmClient("http://test/v1", "qwen2.5", client=http))


def _revise_payload(**verdict_extra) -> dict:
    """一个合规的 revise 结论骨架（核心字段齐全），verdict_extra 追加质量字段。"""
    verdict = {
        "verdict_kind": "revise",
        "verdict_summary": "含模糊量词，建议修订。",
        "findings": [{
            "finding_type": "untestable", "diagnosis_summary": "「尽快」不可量化",
            "basis_summary": "无阈值",
        }],
        "revision_points": [{
            "label": "量化时限", "finding_index": 0,
            "find": "尽快", "replace": "在 5 秒内", "basis": "来源阈值",
        }],
        "supplement_gaps": [],
    }
    verdict.update(verdict_extra)
    return verdict


# ---- 降级不拒收：坏质量数据不拖垮核心结论（AC-P1-02） ----

def test_broken_quality_metadata_degrades_not_rejects():
    v = _revise_payload(
        quality_profile="不是对象",                 # 结构坏 → None
        ears_rewrite={"pattern_type": "??", "lines": []},  # 空 lines → None
    )
    v["findings"][0]["evidence_span"] = "不在基准表达中的片段"  # 定位失败 → 丢 span
    outcome = _sanitize_verdict(v, _BASE)
    assert outcome is not None and not outcome.failed          # 核心结论仍产出
    assert outcome.verdict_kind == "revise" and len(outcome.findings) == 1
    assert outcome.quality_profile is None
    assert outcome.ears_rewrite is None
    assert outcome.findings[0].evidence_span is None           # 定位失败降级为无高亮


def test_all_quality_absent_still_valid():
    outcome = _sanitize_verdict(_revise_payload(), _BASE)
    assert outcome is not None and not outcome.failed
    assert outcome.quality_profile is None and outcome.source_alignments is None
    assert outcome.findings[0].severity == "medium"            # 默认严重度


# ---- clamp + 枚举校验 ----

def test_scores_and_alignment_clamped():
    v = _revise_payload(
        quality_profile={
            "overall": 150,
            "dimensions": [
                {"key": "verifiable", "score": -5, "note": "x"},
                {"key": "不存在维度", "score": 80, "note": "丢"},
            ],
        },
        source_alignments=[
            {"element_ref": "ELM-88", "alignment": 1.5, "note": "偏离"},
            {"element_ref": "", "alignment": 0.5, "note": "空 ref 丢"},
        ],
    )
    outcome = _sanitize_verdict(v, _BASE)
    assert outcome.quality_profile["overall"] == 100
    dims = outcome.quality_profile["dimensions"]
    assert len(dims) == 1 and dims[0]["key"] == "verifiable" and dims[0]["score"] == 0
    assert len(outcome.source_alignments) == 1
    assert outcome.source_alignments[0]["alignment"] == 1.0


def test_finding_enum_fields_validated():
    v = _revise_payload()
    v["findings"][0].update({
        "rule_code": "INCOSE-R7", "dimension": "verifiable", "severity": "high",
        "evidence_span": "尽快",
    })
    outcome = _sanitize_verdict(v, _BASE)
    f = outcome.findings[0]
    assert f.rule_code == "INCOSE-R7" and f.dimension == "verifiable" and f.severity == "high"
    assert f.evidence_span == "尽快"                            # 恰好出现一次 → 保留


def test_illegal_finding_enums_dropped():
    v = _revise_payload()
    v["findings"][0].update({"rule_code": "NOPE", "dimension": "nope", "severity": "critical"})
    outcome = _sanitize_verdict(v, _BASE)
    f = outcome.findings[0]
    assert f.rule_code is None and f.dimension is None
    assert f.severity == "medium"                              # 非法严重度取默认


def test_span_must_anchor_exactly_once():
    base = "系统应记录日志并记录日志"                          # "记录日志" 出现两次
    v = {
        "verdict_kind": "pass", "verdict_summary": "通过",
        "findings": [{"finding_type": "no_blocker", "diagnosis_summary": "ok",
                      "basis_summary": "", "evidence_span": "记录日志"}],
        "revision_points": [], "supplement_gaps": [],
    }
    outcome = _sanitize_verdict(v, base)
    assert outcome is not None and outcome.findings[0].evidence_span is None  # 多次出现 → 丢 span


# ---- 既有聚合守卫不受质量字段影响（回归保护） ----

def test_aggregate_guard_still_rejects_pass_with_points():
    v = _revise_payload(quality_profile={"overall": 90, "dimensions": []})
    v["verdict_kind"] = "pass"                                 # pass 却带修订点 → 整轮拒收
    v["findings"][0]["finding_type"] = "no_blocker"
    assert _sanitize_verdict(v, _BASE) is None


# ---- prompt 循环注入（AC-P1-03） ----

def test_prompt_injects_quality_taxonomy_without_hardcoding():
    system, _user = render_pair(
        "item_diagnosis", project_ref="PRJ", diagnosis_mode="standard",
        item="{}", sources="[]", business_sources="[]", raw_text="", revisions="[]",
        attestation="", prior_findings="[]", excluded_points="[]", thread_context="（无）",
        output_schema=prompt_dumps(_DIAGNOSIS_OUTPUT),
    )
    # 规则码/维度/EARS 均来自 GUIDE 循环注入
    assert "INCOSE-R7" in system and "SRC-DRIFT" in system
    assert "无歧义" in system and "可追溯" in system
    assert "event_driven" in system or "事件驱动" in system
    assert "evidence_span" in system
