"""诊断可靠性（T20260712）：结构化输出三档降级 + 分关白话原因。

契约面（重试语义/分关枚举/判别联合/哨兵不泄漏）由 test_diagnosis_reliability_contract.py
钉死（卡面附件原样迁入）；本文件补充 A1 客户端三档探测降级与 A4 synthesis 白话细节。
"""
import json

import httpx
import pytest

from app.adapters.llm import (
    LlmClient,
    LlmError,
    LlmRequirementItemDiagnoser,
    diagnosis_response_schema,
)

_OK = {"choices": [{"message": {"content": "{}"}}]}


def _client(handler, structured: str = "auto") -> LlmClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    return LlmClient("http://test/v1", "qwen2.5", client=http, structured_output=structured)


def _rf_type(payload: dict):
    return (payload.get("response_format") or {}).get("type")


# ---- A1 三档：json_schema 档请求体 ----

def test_json_schema_tier_sends_response_format():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=_OK)

    schema = {"oneOf": [{"type": "object"}]}
    _client(handler).chat_structured("s", "u", schema, "item_diagnosis")
    rf = seen[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "item_diagnosis"
    assert rf["json_schema"]["schema"] == schema
    assert rf["json_schema"]["strict"] is True


# ---- A1 三档：端点 4xx 拒绝 → 降档并缓存（探测只发生一次）----

def test_probe_downgrades_to_json_object_and_caches():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        seen.append(payload)
        if _rf_type(payload) == "json_schema":
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, json=_OK)

    c = _client(handler)
    c.chat_structured("s", "u", {}, "item_diagnosis")
    c.chat_structured("s", "u", {}, "item_diagnosis")
    assert [_rf_type(p) for p in seen] == ["json_schema", "json_object", "json_object"]


def test_probe_downgrades_all_the_way_to_prompt_only():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        seen.append(payload)
        if "response_format" in payload:
            return httpx.Response(400, json={"error": "no structured output"})
        return httpx.Response(200, json=_OK)

    c = _client(handler)
    c.chat_structured("s", "u", {}, "item_diagnosis")
    assert [_rf_type(p) for p in seen] == ["json_schema", "json_object", None]
    seen.clear()
    c.chat_structured("s", "u", {}, "item_diagnosis")  # 降级已缓存：直接纯提示词
    assert [_rf_type(p) for p in seen] == [None]


def test_off_mode_never_sends_response_format():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=_OK)

    _client(handler, structured="off").chat_structured("s", "u", {}, "item_diagnosis")
    assert "response_format" not in seen[0]


def test_server_5xx_is_not_capability_probe_failure():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    c = _client(handler)
    with pytest.raises(LlmError):
        c.chat_structured("s", "u", {}, "item_diagnosis")
    assert c._structured_tier == "json_schema"  # 5xx 不降档：能力探测只认 4xx


def test_transient_4xx_is_not_capability_probe_failure():
    """429 限流/401 鉴权等与 response_format 能力无关的 4xx：不降档、原样抛（合并裁定修复）。"""
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    c = _client(handler)
    with pytest.raises(LlmError):
        c.chat_structured("s", "u", {}, "item_diagnosis")
    assert c._structured_tier == "json_schema"  # 瞬时 4xx 不得整批毒化档位缓存


def test_null_content_lands_in_parse_stage_not_crash():
    """端点 200 但 content 为 null：走 parse 关分关落账，禁止 AttributeError 炸整批（合并裁定修复）。"""
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    diagnoser = LlmRequirementItemDiagnoser(_client(handler))
    outcome = diagnoser.diagnose(
        "PRJ", "standard",
        {"item_ref": "I1", "expression": "系统应在 3 秒内返回查询结果。"},
        [], "原文", [], [],
    )
    assert outcome.failed and outcome.failure_stage == "parse"


# ---- A4 synthesis 分关：detail 点名修订点序号；诊断请求真带判别联合 ----

_BAD_SYNTHESIS = json.dumps({
    "verdict_kind": "revise",
    "verdict_summary": "建议修订。",
    "findings": [{"finding_type": "untestable",
                  "diagnosis_summary": "缺口径。", "basis_summary": "核对。"}],
    "revision_points": [{"label": "改", "finding_index": 0,
                         "find": "条目中不存在的原文", "replace": "替换文", "basis": "b"}],
    "supplement_gaps": [],
}, ensure_ascii=False)


def test_synthesis_failure_names_point_and_request_carries_union_schema():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": _BAD_SYNTHESIS}}]})

    diagnoser = LlmRequirementItemDiagnoser(_client(handler))
    outcome = diagnoser.diagnose(
        "PRJ", "standard",
        {"item_ref": "I1", "expression": "系统应在 3 秒内返回查询结果。"},
        [], "原文", [], [],
    )
    assert outcome.failed and outcome.failure_stage == "synthesis"
    assert "修订点 P1" in outcome.basis  # 第三关 detail 点名对不上的修订点序号
    assert "条目中不存在的原文" not in outcome.basis  # 模型产出的片段原文不进白话面
    assert len(seen) == 2  # 自动重试一次
    assert seen[0]["response_format"]["json_schema"]["schema"] == diagnosis_response_schema()
