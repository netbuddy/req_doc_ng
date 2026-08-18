"""T20260712-diagnosis-reliability 卡面附件：manager 预写不变量测试（验收可执行化试点）。

用法（卡面 A9）：worker 认领后将本文件原样迁入 `backend/tests/test_diagnosis_reliability_contract.py`
并使其全绿。**断言不得放宽、不得删除**；被钉接口名（`failure_stage`、`diagnosis_response_schema`）
如需调整，须在验收摘要报备理由并同步改名，语义不得变。

钉死的契约（对应卡面设计裁定 3/4 与 A2/A3/A4/A6）：
- `ItemVerdictOutcome.failure_stage: Optional[str]`——失败时 ∈ {parse, llm_error, structure,
  aggregation, synthesis}；成功时 None。
- `app.adapters.llm.diagnosis_response_schema() -> dict`——response_format 所用 JSON Schema，
  顶层 oneOf 按 verdict_kind 分型（判别联合），取值与领域枚举同源。
- sanitize 失败自动重试一次：坏→好=成功且恰好 2 次调用；好=恰好 1 次；坏→坏=失败+分关。
- 模型原文不泄漏：坏输出中的哨兵串不得出现在 outcome.basis（白话 detail 面）。
"""
from __future__ import annotations

import json

import pytest

from app.adapters.llm import LlmRequirementItemDiagnoser
from app.domain.enums import VerdictKind

ALLOWED_STAGES = {"parse", "llm_error", "structure", "aggregation", "synthesis"}
SENTINEL = "SENTINEL_RAW_54321"

GOOD_JSON = json.dumps({
    "verdict_kind": "pass",
    "verdict_summary": "表达明确可验证，无阻断问题。",
    "findings": [{
        "finding_type": "no_blocker",
        "diagnosis_summary": "未发现阻断性问题。",
        "basis_summary": "全文核对。",
    }],
    "revision_points": [],
    "supplement_gaps": [],
}, ensure_ascii=False)

# 聚合守卫违例：revise 却无修订点；夹带哨兵串模拟模型原文
BAD_AGGREGATION_JSON = json.dumps({
    "verdict_kind": "revise",
    "verdict_summary": f"建议修订。{SENTINEL}",
    "findings": [{
        "finding_type": "untestable",
        "diagnosis_summary": "缺可验证口径。",
        "basis_summary": "对照原文。",
    }],
    "revision_points": [],
    "supplement_gaps": [],
}, ensure_ascii=False)

BAD_STRUCTURE_JSON = json.dumps({"verdict_kind": "banana", "verdict_summary": "x"})
BAD_TRUNCATED = '{"verdict_kind": "rev'  # max_tokens 截断的半截 JSON


class _ScriptedClient:
    """按脚本逐次返回内容的假客户端（duck-type LlmClient.chat）。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    def chat(self, system: str, user: str) -> str:
        self.calls += 1
        if not self._replies:
            raise AssertionError("chat 调用次数超过脚本预期（重试上限=1 被突破？）")
        return self._replies.pop(0)


def _diagnose(client: _ScriptedClient):
    return LlmRequirementItemDiagnoser(client).diagnose(  # type: ignore[arg-type]
        "proj-1", "full",
        {"item_ref": "item-1", "expression": "系统应在 3 秒内返回查询结果。"},
        [], "原文", [], [],
    )


# ---- A3 自动重试语义 ----

def test_first_good_no_gratuitous_retry():
    client = _ScriptedClient([GOOD_JSON])
    outcome = _diagnose(client)
    assert outcome.failed is False
    assert client.calls == 1
    assert getattr(outcome, "failure_stage") is None


def test_bad_then_good_retries_once_and_succeeds():
    client = _ScriptedClient([BAD_AGGREGATION_JSON, GOOD_JSON])
    outcome = _diagnose(client)
    assert outcome.failed is False, "重试成功必须承接为正常结论（用户无感）"
    assert client.calls == 2


def test_bad_twice_fails_with_stage():
    client = _ScriptedClient([BAD_AGGREGATION_JSON, BAD_AGGREGATION_JSON])
    outcome = _diagnose(client)
    assert outcome.failed is True
    assert client.calls == 2, "重试上限=1：不得第三次调用"
    assert getattr(outcome, "failure_stage") in ALLOWED_STAGES
    assert getattr(outcome, "failure_stage") == "aggregation"


# ---- A4 分关与原文不泄漏 ----

def test_structure_stage_and_no_raw_leak():
    client = _ScriptedClient([BAD_STRUCTURE_JSON, BAD_STRUCTURE_JSON])
    outcome = _diagnose(client)
    assert outcome.failed is True
    assert getattr(outcome, "failure_stage") == "structure"


def test_sentinel_not_leaked_into_basis():
    client = _ScriptedClient([BAD_AGGREGATION_JSON, BAD_AGGREGATION_JSON])
    outcome = _diagnose(client)
    assert SENTINEL not in (outcome.basis or ""), "模型原文禁入白话 detail（AGENTS.md 硬规 8）"


# ---- A6 截断半截 JSON → parse 关 ----

def test_truncated_json_goes_parse_stage():
    client = _ScriptedClient([BAD_TRUNCATED, BAD_TRUNCATED])
    outcome = _diagnose(client)
    assert outcome.failed is True
    assert getattr(outcome, "failure_stage") == "parse"


# ---- A2 判别联合 schema 与领域枚举同源 ----

def test_response_schema_is_discriminated_union_from_domain_enum():
    from app.adapters.llm import diagnosis_response_schema

    schema = diagnosis_response_schema()
    variants = schema.get("oneOf")
    assert isinstance(variants, list) and variants, "顶层须为按 verdict_kind 分型的 oneOf 判别联合"

    kinds: set[str] = set()
    by_kind: dict[str, dict] = {}
    for v in variants:
        vk = v.get("properties", {}).get("verdict_kind", {})
        const = vk.get("const") or (vk.get("enum") or [None])[0]
        assert const, "每个变体的 verdict_kind 必须钉死（const/单值 enum）"
        kinds.add(const)
        by_kind[const] = v
    assert kinds == {k.value for k in VerdictKind}, "结论字取值必须与领域枚举同源，禁手写平行副本"

    revise_pts = by_kind["revise"].get("properties", {}).get("revision_points", {})
    assert int(revise_pts.get("minItems") or 0) >= 1, "revise 变体必须至少 1 个修订点"

    pass_pts = by_kind["pass"].get("properties", {}).get("revision_points")
    if pass_pts is not None:
        assert int(pass_pts.get("maxItems", 1)) == 0, "pass 变体不得允许修订点"

    supp_gaps = by_kind["supplement"].get("properties", {}).get("supplement_gaps", {})
    assert int(supp_gaps.get("minItems") or 0) >= 1, "supplement 变体必须至少 1 条缺口"


# ---- A5 守卫不放宽（哨兵回归：合法输出仍正常承接）----

def test_valid_revise_still_accepted():
    good_revise = json.dumps({
        "verdict_kind": "revise",
        "verdict_summary": "建议补可验证口径。",
        "findings": [{
            "finding_type": "untestable",
            "diagnosis_summary": "缺可验证口径。",
            "basis_summary": "对照原文。",
        }],
        "revision_points": [{
            "finding_index": 0,
            "label": "补时限",
            "find": "3 秒内",
            "replace": "3 秒（P95）内",
            "basis": "量化口径",
        }],
        "supplement_gaps": [],
    }, ensure_ascii=False)
    client = _ScriptedClient([good_revise])
    outcome = _diagnose(client)
    assert outcome.failed is False
    assert outcome.verdict_kind == "revise"
    assert len(outcome.revision_points) == 1
