"""ollama / vLLM / llama.cpp 的请求体契约（T20260720-model-provider-registry · A4）。

核心不变式（关思考字段按类型分发，单一来源＝chat_extension_fields）：
- llama.cpp 收 `chat_template_kwargs.enable_thinking:false`（专属参数）；
- ollama 收 `reasoning_effort:"none"`（其 OpenAI 兼容层不认 enable_thinking，116 实测 2026-07-24）；
- vLLM / 通用兼容端点什么扩展字段都不收（vLLM 关思考依赖服务端 --reasoning-parser，盲发有假成功风险）。
本文件对着真实的本地契约桩件跑，逐条断言线路上真正发出去的请求体。

桩件的响应形状依据与「待真实环境复核」清单见 tests/provider_stub.py。
"""
from __future__ import annotations

import pytest

from app.adapters.llm import (
    CAP_STATE_DEGRADED,
    CAP_STATE_SUPPORTED,
    CAP_STATE_UNSUPPORTED,
    EMPTY_CAPABILITY_PROFILE,
    STRUCTURED_TIER_JSON_OBJECT,
    STRUCTURED_TIER_JSON_SCHEMA,
    STRUCTURED_TIER_PROMPT_ONLY,
    CapabilityProfile,
    LlmClient,
    LlmSourceIntakeJudge,
    chat_extension_fields,
    minimal_chat_payload,
)
from app.config import Settings
from app.domain.enums import ModelJudgement
from tests.provider_stub import FLAVOR_LLAMA_CPP, FLAVOR_OLLAMA, FLAVOR_VLLM, ProviderStub


# ---- 关思考扩展字段按类型分发 ----


def test_extension_fields_dispatch_by_provider_type():
    assert chat_extension_fields("llama_cpp", True) == {"chat_template_kwargs": {"enable_thinking": False}}
    assert chat_extension_fields("ollama", True) == {"reasoning_effort": "none"}
    # vLLM 与通用兼容端点零扩展字段
    for other in ("vllm", "openai_compatible"):
        assert chat_extension_fields(other, True) == {}
    # 关掉 disable_thinking 时谁都不发
    for pt in ("llama_cpp", "ollama", "vllm", "openai_compatible"):
        assert chat_extension_fields(pt, False) == {}


def test_minimal_payload_extension_dispatch():
    assert "chat_template_kwargs" in minimal_chat_payload("m", "llama_cpp")
    assert minimal_chat_payload("m", "ollama")["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in minimal_chat_payload("m", "ollama")
    for other in ("vllm", "openai_compatible"):
        body = minimal_chat_payload("m", other)
        assert "chat_template_kwargs" not in body
        assert "reasoning_effort" not in body


@pytest.mark.parametrize(
    "provider_type, flavor, model, expected_extension",
    [
        ("llama_cpp", FLAVOR_LLAMA_CPP, "qwen2.5", ("chat_template_kwargs", {"enable_thinking": False})),
        ("ollama", FLAVOR_OLLAMA, "qwen2.5:7b", ("reasoning_effort", "none")),
        ("vllm", FLAVOR_VLLM, "Qwen2.5-7B-Instruct", None),
        ("openai_compatible", FLAVOR_LLAMA_CPP, "qwen-plus", None),
    ],
)
def test_real_lane_request_body_matches_provider_contract(
    provider_type, flavor, model, expected_extension
):
    """正式 lane 调用（非仅测试按钮）线路上的请求体，逐类型核对。"""
    with ProviderStub(flavor=flavor, models=(model,), chat_content='{"judgement":"acceptable","basis":"够用"}') as stub:
        judge = LlmSourceIntakeJudge(
            LlmClient(base_url=stub.base_url, model=model, provider_type=provider_type, timeout=5)
        )
        outcome = judge.judge("p1", "一段需求原文", "备注")
        sent = stub.requests[-1]

    assert outcome.judgement is ModelJudgement.ACCEPTABLE
    assert sent["path"].endswith("/chat/completions")
    assert sent["body"]["model"] == model
    assert sent["body"]["stream"] is False
    extension_keys = {"chat_template_kwargs", "reasoning_effort"} & sent["body"].keys()
    if expected_extension is None:
        assert extension_keys == set()
    else:
        key, value = expected_extension
        assert extension_keys == {key}
        assert sent["body"][key] == value


@pytest.mark.parametrize("provider_type, flavor", [
    ("ollama", FLAVOR_OLLAMA), ("vllm", FLAVOR_VLLM),
])
def test_connection_test_body_extension_dispatch(provider_type, flavor):
    """设置页第二级测试与正式调用共用同一个请求体构造器，因此扩展字段口径也一致。"""
    from app.api.schemas import ModelConnectionTestCommand
    from app.services.config_registry import _probe_generation

    with ProviderStub(flavor=flavor, models=("m1",)) as stub:
        result = _probe_generation(
            stub.base_url,
            ModelConnectionTestCommand(base_url=stub.base_url, model="m1", timeout_seconds=5),
            provider_type,
            None,
        )
        sent = stub.requests[-1]
    assert result.ok is True
    assert "chat_template_kwargs" not in sent["body"]
    # ollama 的连通测试同样走关思考口径；vLLM 不带任何扩展字段
    assert ("reasoning_effort" in sent["body"]) is (provider_type == "ollama")


# ---- ollama 模型名漏标签 → 404 → 「模型不存在」 ----


def test_ollama_model_without_tag_maps_to_model_missing():
    """ollama 的模型标识形如 name:tag，漏了标签端点回 404（桩件按其源码形状复现）。"""
    from app.api.schemas import ModelConnectionTestCommand
    from app.services.config_registry import OUTCOME_MODEL_MISSING, _probe_generation

    with ProviderStub(flavor=FLAVOR_OLLAMA, models=("qwen2.5:7b",)) as stub:
        result = _probe_generation(
            stub.base_url,
            ModelConnectionTestCommand(base_url=stub.base_url, model="qwen2.5", timeout_seconds=5),
            "ollama",
            None,
        )
    assert result.ok is False
    assert result.outcome == OUTCOME_MODEL_MISSING
    assert result.error_code == "http_404"


def test_ollama_model_list_ids_carry_tag():
    """第一级的「模型是否在列表里」对 ollama 必须用带标签全名比对。"""
    from app.api.schemas import ModelConnectionTestCommand
    from app.services.config_registry import OUTCOME_MODEL_MISSING, OUTCOME_OK, _probe_reachability

    with ProviderStub(flavor=FLAVOR_OLLAMA, models=("qwen2.5:7b",)) as stub:
        missing = _probe_reachability(
            stub.base_url,
            ModelConnectionTestCommand(base_url=stub.base_url, model="qwen2.5", timeout_seconds=5),
            None,
        )
        listed = _probe_reachability(
            stub.base_url,
            ModelConnectionTestCommand(base_url=stub.base_url, model="qwen2.5:7b", timeout_seconds=5),
            None,
        )
    assert missing.outcome == OUTCOME_MODEL_MISSING
    assert missing.models == ["qwen2.5:7b"]
    assert listed.outcome == OUTCOME_OK


# ---- vLLM 未知模型错误体形状（R4：116 实测订正 param 为 null）----


def test_vllm_unknown_model_error_body_shape():
    """R4 订正的钉值断言：vLLM 未知模型 404 错误体 `param` 为 null。

    116 真实端点实测（2026-07-23，见 docs/proposals/llm-provider-feasibility/116摸底报告.md §A3）：
    错误体 `{"type":"NotFoundError","code":404,"param":null,...}`。桩件先前把 param 误写成 "model"，
    本断言直接钉值 `param is None`——将来若有人把它改回 "model"，此测试即红（不许只改桩件不改测试）。
    code 为整数 404（非字符串、亦非 ollama 的 null），type 为 NotFoundError，一并固定。
    """
    import httpx

    with ProviderStub(flavor=FLAVOR_VLLM, models=("Qwen2.5-7B-Instruct",)) as stub:
        resp = httpx.post(
            stub.base_url + "/chat/completions",
            json={"model": "does-not-exist", "messages": [{"role": "user", "content": "hi"}]},
            timeout=5,
        )
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["param"] is None          # ← R4 订正：实测为 null，非 "model"
    assert error["code"] == 404            # 整数 404（非字符串），形状与 ollama 的 null 不同
    assert error["type"] == "NotFoundError"


# ---- 结构化输出降级链对三类 provider 行为一致 ----


@pytest.mark.parametrize("provider_type, flavor", [
    ("llama_cpp", FLAVOR_LLAMA_CPP), ("ollama", FLAVOR_OLLAMA), ("vllm", FLAVOR_VLLM),
])
def test_structured_output_downgrade_chain_is_provider_agnostic(provider_type, flavor):
    """端点以 4xx 拒绝 response_format → 降档到 json_object → 再降纯提示词，三类型口径一致。"""
    with ProviderStub(flavor=flavor, models=("m1",), reject_response_format=True) as stub:
        client = LlmClient(base_url=stub.base_url, model="m1", provider_type=provider_type,
                           timeout=5, structured_output="auto")
        content = client.chat_structured("s", "u", {"type": "object"}, lane="item_diagnosis")
        bodies = [r["body"] for r in stub.requests if r["method"] == "POST"]

    assert content  # 最终仍拿到回复（降级不失败）
    tiers = [b.get("response_format", {}).get("type") if b.get("response_format") else None
             for b in bodies]
    # 三次请求：json_schema → json_object → 纯提示词（无 response_format）
    assert tiers == ["json_schema", "json_object", None]
    # 降级过程不改变扩展字段口径
    assert all(("chat_template_kwargs" in b) is (provider_type == "llama_cpp") for b in bodies)
    assert all(("reasoning_effort" in b) is (provider_type == "ollama") for b in bodies)


def test_structured_output_survives_when_endpoint_accepts_it():
    with ProviderStub(models=("m1",)) as stub:
        client = LlmClient(base_url=stub.base_url, model="m1", provider_type="vllm",
                           timeout=5, structured_output="auto")
        client.chat_structured("s", "u", {"type": "object"}, lane="item_diagnosis")
        bodies = [r["body"] for r in stub.requests if r["method"] == "POST"]
    assert len(bodies) == 1
    assert bodies[0]["response_format"]["type"] == "json_schema"


# ---- 客户端默认类型：未指定即按 llama.cpp（与升级前行为一致） ----


def test_default_provider_type_preserves_pre_upgrade_behaviour():
    with ProviderStub(models=("m1",)) as stub:
        LlmClient(base_url=stub.base_url, model="m1", timeout=5).chat("s", "u")
        sent = stub.requests[-1]
    assert sent["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_env_default_provider_type_is_llama_cpp():
    assert Settings().llm_provider_type == "llama_cpp"


# ---- 能力档案对请求体的影响（T20260724-capability-probe-panel）----
# 这一组钉的是「加性非破坏」：没有档案时线路上的请求体与档案机制上线前逐字节一致；
# 有档案时才按探明的事实改变请求体。


@pytest.mark.parametrize("provider_type, flavor", [
    ("llama_cpp", FLAVOR_LLAMA_CPP), ("ollama", FLAVOR_OLLAMA), ("vllm", FLAVOR_VLLM),
    ("openai_compatible", FLAVOR_VLLM),
])
def test_no_profile_request_body_is_byte_identical(provider_type, flavor):
    """未探测过的 provider：请求体与不传档案参数时逐字节相同（负向断言护栏）。

    逐字节比对整个请求体而不是只看扩展字段：档案机制往请求构造里插了好几处判断
    （关思考字段、结构化起始档、max_tokens 钳制），任何一处对无档案客户端产生副作用都会在此暴露。
    """
    with ProviderStub(flavor=flavor, models=("m1",)) as stub:
        LlmClient(base_url=stub.base_url, model="m1", provider_type=provider_type,
                  timeout=5, max_tokens=256, context_tokens=4096).chat("系统提示", "用户输入")
        without_profile = [r["body"] for r in stub.requests if r["method"] == "POST"][-1]

    with ProviderStub(flavor=flavor, models=("m1",)) as stub:
        LlmClient(base_url=stub.base_url, model="m1", provider_type=provider_type,
                  timeout=5, max_tokens=256, context_tokens=4096,
                  capability_profile=EMPTY_CAPABILITY_PROFILE).chat("系统提示", "用户输入")
        with_empty_profile = [r["body"] for r in stub.requests if r["method"] == "POST"][-1]

    assert without_profile == with_empty_profile
    # 并且仍是升级前那份请求体：扩展字段按类型、max_tokens 原样不钳制
    assert without_profile["max_tokens"] == 256
    assert ("chat_template_kwargs" in without_profile) is (provider_type == "llama_cpp")
    assert ("reasoning_effort" in without_profile) is (provider_type == "ollama")


@pytest.mark.parametrize("provider_type, flavor, prior_key", [
    # vLLM 的先验是「一个字段都不发」：探明 reasoning_effort 真生效后必须开始发它。
    # 这正是本卡点名交付的「vLLM 关思考追发」，没有这条断言它就没有回归护栏。
    ("vllm", FLAVOR_VLLM, None),
    # llama.cpp 的先验是 enable_thinking：探明这个端点认的是 reasoning_effort 后，
    # 线路上必须换成后者，而不是继续发先验那个字段。
    ("llama_cpp", FLAVOR_LLAMA_CPP, "chat_template_kwargs"),
])
def test_probed_thinking_mode_overrides_the_type_prior(provider_type, flavor, prior_key):
    """档案探明的关思考方式与类型先验不同时，线路上真正发出去的是档案里那个。

    这是「档案 > 先验」这条设计的正向证据：只断言「没档案时与升级前一致」是证不出来的——
    把 chat_extension_fields 改成「一律按先验」，那些负向断言仍然全绿。
    """
    profile = CapabilityProfile(
        thinking_off_state=CAP_STATE_SUPPORTED,
        thinking_off_mode="reasoning_effort",
        thinking_available=True,
        probed_at="2026-07-24T19:30:00+08:00",
    )
    with ProviderStub(flavor=flavor, models=("m1",)) as stub:
        LlmClient(base_url=stub.base_url, model="m1", provider_type=provider_type,
                  timeout=5, capability_profile=profile).chat("s", "u")
        body = stub.requests[-1]["body"]

    assert body["reasoning_effort"] == "none"
    # 先验那个字段不该再出现：档案是对这个端点的实测结论，不是在先验之上做叠加
    if prior_key:
        assert prior_key not in body


def test_probed_tier_is_used_on_the_first_request():
    """档案已探明结构化只到 json_object 档：正式请求首发即该档，不再在线上试探降级。

    没有档案时的行为（从 json_schema 起、被 4xx 拒绝后降档）由既有降级链用例守住，两者对照
    正是「探明就直取、没探明才试探」这条设计的证据。
    """
    profile = CapabilityProfile(
        structured_state=CAP_STATE_DEGRADED, structured_tier=STRUCTURED_TIER_JSON_OBJECT,
        probed_at="2026-07-24T19:30:00+08:00",
    )
    with ProviderStub(flavor=FLAVOR_OLLAMA, models=("m1",)) as stub:
        client = LlmClient(base_url=stub.base_url, model="m1", provider_type="ollama",
                           timeout=5, structured_output="auto", capability_profile=profile)
        client.chat_structured("s", "u", {"type": "object"}, lane="item_diagnosis")
        bodies = [r["body"] for r in stub.requests if r["method"] == "POST"]

    assert len(bodies) == 1, "探明了档位就不该再有试探性的第二次请求"
    assert bodies[0]["response_format"]["type"] == "json_object"


def test_probed_prompt_only_tier_sends_no_response_format():
    profile = CapabilityProfile(
        structured_state=CAP_STATE_UNSUPPORTED, structured_tier=STRUCTURED_TIER_PROMPT_ONLY,
        probed_at="2026-07-24T19:30:00+08:00",
    )
    with ProviderStub(flavor=FLAVOR_VLLM, models=("m1",)) as stub:
        LlmClient(base_url=stub.base_url, model="m1", provider_type="vllm", timeout=5,
                  structured_output="auto", capability_profile=profile).chat_structured(
            "s", "u", {"type": "object"}, lane="item_diagnosis")
        bodies = [r["body"] for r in stub.requests if r["method"] == "POST"]

    assert len(bodies) == 1
    assert "response_format" not in bodies[0]


def test_structured_output_off_is_not_overridden_by_profile():
    """配置把结构化输出整体关掉时，档案不得把它打开——档案是能力事实，不是开关。"""
    profile = CapabilityProfile(
        structured_state=CAP_STATE_SUPPORTED, structured_tier=STRUCTURED_TIER_JSON_SCHEMA,
        probed_at="2026-07-24T19:30:00+08:00",
    )
    with ProviderStub(flavor=FLAVOR_VLLM, models=("m1",)) as stub:
        LlmClient(base_url=stub.base_url, model="m1", provider_type="vllm", timeout=5,
                  structured_output="off", capability_profile=profile).chat_structured(
            "s", "u", {"type": "object"}, lane="item_diagnosis")
        bodies = [r["body"] for r in stub.requests if r["method"] == "POST"]

    assert "response_format" not in bodies[0]
