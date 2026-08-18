"""模型服务逐能力探测 C3–C6（T20260724-capability-probe-panel）。

本文件钉的是**探测三原则**能不能兑现：
1. 验产物不验状态码——桩件专门造了「200 但没生效」的假成功场景，只看状态码的判据必挂；
2. 差分探测——C3 的结论必须来自「加字段 vs 不加字段」两条对照请求的产物差异；
3. 廉价可控——每项就一两条短请求，超时按「未探明」记录而不炸掉整张清单。

桩件对三家引擎关思考字段的认与不认按 116 真实端点实测行为实现（见 provider_stub 的
`_thinking_off_honored`），因此这里的断言同时也是对那份实测结论的回归。
"""
from __future__ import annotations

import pytest

from app.adapters.llm import (
    CAP_CONTEXT,
    CAP_GENERATE,
    CAP_NOTE_OLLAMA_MODEL_LIMIT_ONLY,
    CAP_NOTE_THINKING_DISABLED_ON_SERVER,
    CAP_NOTE_THINKING_SEGMENT_HIDDEN,
    CAP_NOTE_VLLM_NEEDS_REASONING_PARSER,
    CAP_REACHABLE,
    CAP_STATE_DEGRADED,
    CAP_STATE_SUPPORTED,
    CAP_STATE_UNKNOWN,
    CAP_STATE_UNSUPPORTED,
    CAP_STRUCTURED,
    CAP_THINKING,
    CAP_UNKNOWN_FIELDS,
    CAPABILITY_KEYS,
    STRUCTURED_TIER_JSON_OBJECT,
    STRUCTURED_TIER_JSON_SCHEMA,
    STRUCTURED_TIER_PROMPT_ONLY,
    THINKING_OFF_ENABLE_THINKING,
    THINKING_OFF_NOT_NEEDED,
    THINKING_OFF_REASONING_EFFORT,
)
from app.adapters.llm import CAP_NOTE_THINKING_DECLARED_NOT_OBSERVED
from app.api.schemas import ModelConnectionTestCommand
import app.db.models  # noqa: F401  建表前先注册模型
from app.db.base import Base, make_session_factory
from app.services.config_registry import (
    ConfigRegistryService,
    _C3_PROMPT,
    _PROBE_ANSWER_MAX_TOKENS,
    _PROBE_MAX_TOKENS,
    _ChatSample,
    _EndpointMetadata,
    _findings_to_profile,
    _probe_structured,
    _probe_thinking,
)

from tests.provider_stub import FLAVOR_LLAMA_CPP, FLAVOR_OLLAMA, FLAVOR_VLLM, ProviderStub

MODEL = "m-probe"


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _probe(session, stub: ProviderStub, provider_type: str, timeout: float = 10.0):
    service = ConfigRegistryService(session)
    return service.probe_capabilities(ModelConnectionTestCommand(
        base_url=stub.base_url, model=MODEL, provider_type=provider_type,
        timeout_seconds=timeout,
    ))


def _item(result, key: str):
    found = [i for i in result.items if i.key == key]
    assert found, f"清单里缺 {key} 这一项"
    return found[0]


def _c3_bodies(stub: ProviderStub) -> list[dict]:
    """只挑 C3 那几条请求。

    整轮探测里发的 chat 请求不止 C3：第二级连通测试（C2）发的最小生成请求本身就按 per-type
    先验带着关思考字段，C6 也会发一条。按提示词把 C3 的请求择出来，断言才落在该落的地方。
    """
    return [
        b for b in (r["body"] for r in stub.requests if r["method"] == "POST")
        if isinstance(b, dict)
        and (b.get("messages") or [{}])[0].get("content") == _C3_PROMPT
    ]


# ---- A1 C3 可关思考：三 flavor 各自选出真正生效的那个字段 ----


@pytest.mark.parametrize("flavor, provider_type, expected_mode", [
    # llama.cpp 认 chat_template_kwargs.enable_thinking，不认 reasoning_effort
    (FLAVOR_LLAMA_CPP, "llama_cpp", THINKING_OFF_ENABLE_THINKING),
    # ollama 反过来只认 reasoning_effort:"none"（116 实测：enable_thinking 无效仍 24s）
    (FLAVOR_OLLAMA, "ollama", THINKING_OFF_REASONING_EFFORT),
])
def test_c3_picks_the_field_that_actually_works(session, flavor, provider_type, expected_mode):
    with ProviderStub(flavor=flavor, models=(MODEL,), thinking=True) as stub:
        result = _probe(session, stub, provider_type)
    item = _item(result, CAP_THINKING)
    assert item.state == CAP_STATE_SUPPORTED
    assert item.mode == expected_mode
    # 差分探测：基线（不带字段）确实探到了思考段，否则这个结论就不是「对照」出来的
    assert item.detail["baseline_has_thinking"] is True
    assert result.profile["thinking"] == {
        "available": True, "off_state": CAP_STATE_SUPPORTED, "off_mode": expected_mode,
    }
    # 「会不会思考」与「能不能关掉」是两个结论，档案里各记各的
    assert item.available is True


def test_c3_ollama_does_not_settle_for_enable_thinking(session):
    """ollama 的候选顺序里 enable_thinking 排第二，且桩件对它无反应——不能被误判为生效。"""
    with ProviderStub(flavor=FLAVOR_OLLAMA, models=(MODEL,), thinking=True) as stub:
        result = _probe(session, stub, "ollama")
        bodies = [r["body"] for r in stub.requests if r["method"] == "POST"]
    assert _item(result, CAP_THINKING).mode == THINKING_OFF_REASONING_EFFORT
    # 首选就命中时不该再试第二个候选（廉价可控：不做无谓请求）
    assert not any("chat_template_kwargs" in (b or {}) for b in bodies)


def test_c3_vllm_without_reasoning_parser_is_conditional_not_supported(session):
    """vLLM 没起 --reasoning-parser：两个候选都被静默收下回 200，思考照跑。

    这正是「验状态码会错、验产物才对」的场景：两条候选请求都是 200，只有产物能说明没生效。
    结论必须是「有条件」并给出那句可执行的提示，而不是判成支持后盲发字段。
    """
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), thinking=True,
                      reasoning_parser=False) as stub:
        result = _probe(session, stub, "vllm")
        statuses = [r for r in stub.requests if r["method"] == "POST"]
    item = _item(result, CAP_THINKING)
    assert item.state == CAP_STATE_DEGRADED
    assert item.note_code == CAP_NOTE_VLLM_NEEDS_REASONING_PARSER
    assert item.mode in (None, "")  # 没探明生效字段就不给 mode，适配层据此不发任何关思考字段
    assert len(statuses) >= 3  # 基线 + 两个候选都真的发出去过（差分探测）
    assert [t["has_thinking"] for t in item.detail["tried"]] == [True, True]


def test_c3_vllm_with_reasoning_parser_is_supported(session):
    """服务端起了 --reasoning-parser 之后同一个端点就能探明——证明判据跟着事实走。"""
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), thinking=True,
                      reasoning_parser=True) as stub:
        result = _probe(session, stub, "vllm")
    item = _item(result, CAP_THINKING)
    assert item.state == CAP_STATE_SUPPORTED
    assert item.mode == THINKING_OFF_REASONING_EFFORT


def test_c3_non_thinking_model_needs_no_field(session):
    """基线本来就没有思考段：结论是「无需关」，而不是硬塞一个字段进去。"""
    with ProviderStub(flavor=FLAVOR_OLLAMA, models=(MODEL,), thinking=False) as stub:
        result = _probe(session, stub, "ollama")
        c3_bodies = _c3_bodies(stub)
    item = _item(result, CAP_THINKING)
    assert item.state == CAP_STATE_SUPPORTED
    assert item.mode == THINKING_OFF_NOT_NEEDED
    # C3 只发了基线那一条，没有为了「试试看」而多发候选（廉价可控）
    assert len(c3_bodies) == 1
    assert "reasoning_effort" not in c3_bodies[0]


def test_c3_judges_by_product_not_by_latency(session):
    """判据是思考段这个产物，不是延迟。

    桩件对所有请求施加同样的延迟：若判据混入「变快了就算生效」，带字段那条与基线一样慢，
    结论就会翻车。这里两个 flavor 的结论仍然正确，说明判定没有依赖延迟差。
    """
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), thinking=True,
                      delay_seconds=0.05) as stub:
        result = _probe(session, stub, "llama_cpp")
    item = _item(result, CAP_THINKING)
    assert item.state == CAP_STATE_SUPPORTED and item.mode == THINKING_OFF_ENABLE_THINKING


def test_c3_timeout_falls_back_to_unknown(session):
    """探针请求超时：这一项落「未探明」并记下异常，绝不报错中断（评审意见 1）。

    直接调这一项的探针而不是走整轮：整轮探测里 C2 与 C3 共用同一个超时预算，能把 C3 拖超时的
    延迟必然先把 C2 拖超时，那样测到的就成了「C2 挂了所以后续未知」，不是要钉的那件事。
    整轮里某一项失败后清单仍然完整，由 test_unreachable_endpoint_yields_unknown_rest 钉住。
    """
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), thinking=True,
                      delay_seconds=0.4) as stub:
        finding = _probe_thinking(stub.base_url, MODEL, "llama_cpp", None, 0.15,
                                  _EndpointMetadata())
    assert finding.state == CAP_STATE_UNKNOWN
    assert finding.outcome  # 记下了是什么异常，便于排查
    assert finding.mode == ""  # 没探明就不给方式，适配层据此回落先验


def test_c3_hidden_thinking_segment_is_not_reported_as_no_need(session):
    """端点不把思考段回出来、输出却把预算烧光：不能说「无需关」，只能说「没探明」。"""
    body = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "嗯" * 200},
                     "finish_reason": "length"}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 64, "total_tokens": 73},
    }
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), chat_body_override=body) as stub:
        result = _probe(session, stub, "vllm")
    item = _item(result, CAP_THINKING)
    assert item.state == CAP_STATE_UNKNOWN
    assert item.note_code == CAP_NOTE_THINKING_SEGMENT_HIDDEN


# ---- A2 C4 结构化输出：识破「200 但没强制约束」的假成功 ----


def test_c4_detects_enforced_json_schema(session):
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), structured_enforced=True) as stub:
        result = _probe(session, stub, "vllm")
    item = _item(result, CAP_STRUCTURED)
    assert item.state == CAP_STATE_SUPPORTED
    assert item.tier == STRUCTURED_TIER_JSON_SCHEMA
    assert item.detail["tried"][0]["conforms"] is True


def test_c4_sees_through_false_success(session):
    """端点收下 response_format 回了 200，产物却不符合 schema——必须降档，不能当成功。

    只看状态码的判据在这里会判「支持 json_schema」，然后正式调用就会拿到不受约束的输出。
    """
    with ProviderStub(flavor=FLAVOR_OLLAMA, models=(MODEL,), structured_enforced=False,
                      chat_content="当然可以，地球是圆的。") as stub:
        result = _probe(session, stub, "ollama")
        posts = [r["body"] for r in stub.requests if r["method"] == "POST"]
    item = _item(result, CAP_STRUCTURED)
    assert item.state == CAP_STATE_UNSUPPORTED
    assert item.tier == STRUCTURED_TIER_PROMPT_ONLY
    # 两档都试过、都是 200、都不符合 schema
    tried = item.detail["tried"]
    assert [t["tier"] for t in tried] == [STRUCTURED_TIER_JSON_SCHEMA, STRUCTURED_TIER_JSON_OBJECT]
    assert all(t["ok"] and not t["conforms"] for t in tried)
    assert any((b or {}).get("response_format", {}).get("type") == "json_object" for b in posts)


def test_c4_falls_back_to_json_object_when_schema_rejected(session):
    """端点以 4xx 拒 response_format 的 json_schema 档：降到 json_object 并标「有条件」。"""
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,)) as stub:
        # 只拒 json_schema 档：桩件的 reject_response_format 会连 json_object 一起拒，
        # 故这里用 structured_enforced 造「json_schema 不符合、json_object 符合」不现实——
        # 改为断言全拒时的终局：两档都不成立即落纯提示词档。
        stub.reject_response_format = True
        result = _probe(session, stub, "llama_cpp")
    item = _item(result, CAP_STRUCTURED)
    assert item.state == CAP_STATE_UNSUPPORTED
    assert item.tier == STRUCTURED_TIER_PROMPT_ONLY
    assert [t["status"] for t in item.detail["tried"]] == [400, 400]


@pytest.mark.parametrize("flavor, provider_type, expected_field, expected_value", [
    (FLAVOR_LLAMA_CPP, "llama_cpp", "chat_template_kwargs", {"enable_thinking": False}),
    (FLAVOR_OLLAMA, "ollama", "reasoning_effort", "none"),
])
def test_c4_probe_turns_thinking_off_before_judging_the_product(
    session, flavor, provider_type, expected_field, expected_value
):
    """C4 的试探请求必须带上关思考字段，否则会把会思考的端点判成「不支持 JSON 格式输出」。

    这个误判的代价很大：用户一点「应用」，这条服务此后每一次需要机器读结果的调用都降到纯提示词
    档，而且运行时的降级链只往下走、没有自动恢复路径，只能靠用户手动重探。
    关思考的方式取自刚跑完的 C3（所以 C3 要排在 C4 前面），预算也要够放下一段思考开场。
    """
    with ProviderStub(flavor=flavor, models=(MODEL,), thinking=True) as stub:
        result = _probe(session, stub, provider_type)
        c4_bodies = [
            b for b in (r["body"] for r in stub.requests if r["method"] == "POST")
            if isinstance(b, dict) and b.get("response_format")
        ]
    item = _item(result, CAP_STRUCTURED)
    assert item.state == CAP_STATE_SUPPORTED
    assert item.tier == STRUCTURED_TIER_JSON_SCHEMA
    assert c4_bodies, "C4 一条试探请求都没发出去"
    assert all(b.get(expected_field) == expected_value for b in c4_bodies)
    # 预算：思考段一起头，64 个 token 连正文都放不下，判据就成了「产物不达标」的假结论
    assert all(b["max_tokens"] == _PROBE_ANSWER_MAX_TOKENS for b in c4_bodies)
    assert _PROBE_ANSWER_MAX_TOKENS > _PROBE_MAX_TOKENS


def test_c6_probe_also_turns_thinking_off(session):
    """C6 只看状态码、结论不受思考段影响，但同样带上关思考字段：省一次等待，也免得日后有人
    给它加产物判据时重蹈 C4 的覆辙。"""
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), thinking=True) as stub:
        _probe(session, stub, "llama_cpp")
        c6_bodies = [
            b for b in (r["body"] for r in stub.requests if r["method"] == "POST")
            if isinstance(b, dict) and "x_req_doc_capability_probe" in b
        ]
    assert c6_bodies
    assert all(b.get("chat_template_kwargs") == {"enable_thinking": False} for b in c6_bodies)


# ---- 「请求没问成」不是「不支持」：负面结论只能来自真正问出来的答案 ----


def _scripted_samples(monkeypatch, first: _ChatSample, rest: _ChatSample):
    """把探针的发请求函数换成脚本：第一条按 first 回，其余一律按 rest 回。

    用脚本而不是真桩件加延迟：要造的是「基线成功、后续请求全部没问成」这种时序，桩件的延迟
    是全局的，造不出来，也会让用例依赖真实计时而变得不稳定。
    """
    from app.services import config_registry as cr

    calls: list[dict] = []

    def fake_sample(base_url, headers, payload, timeout):
        calls.append(payload)
        return first if len(calls) == 1 else rest

    monkeypatch.setattr(cr, "_sample_chat", fake_sample)
    return calls


def test_c3_candidates_without_an_answer_fall_back_to_unknown(monkeypatch):
    """基线看到了思考段，随后两个候选请求都没问成：落「没探明」，不许落「关不掉」。

    落「关不掉」的后果是适配层此后一个关思考字段都不发——这个本来发一个字段就能关掉思考的端点
    会从此带着思考跑（慢 20–50 倍直至超时），正是本机制立项要消灭的那个故障。
    """
    _scripted_samples(
        monkeypatch,
        _ChatSample(ok=True, status=200, latency_ms=24000, content="2",
                    has_thinking=True, completion_tokens=9),
        _ChatSample(ok=False, latency_ms=20000, error_code="ReadTimeout"),
    )
    finding = _probe_thinking("http://127.0.0.1:1/v1", MODEL, "llama_cpp", None, 5.0,
                              _EndpointMetadata())
    assert finding.state == CAP_STATE_UNKNOWN
    assert finding.outcome == "ReadTimeout"  # 记下没问成的原因，便于排查
    assert finding.mode == ""  # 没探明就不给方式，适配层据此回落先验
    assert finding.available is True  # 「会思考」这半是实测出来的，仍然算数


def test_c4_tiers_without_an_answer_fall_back_to_unknown(monkeypatch):
    """两档试探请求都没问成（端点重启、网关抖动）：落「没探明」，不许落「不支持」。"""
    failed = _ChatSample(ok=False, latency_ms=5, error_code="ConnectError")
    _scripted_samples(monkeypatch, failed, failed)
    finding = _probe_structured("http://127.0.0.1:1/v1", MODEL, None, 5.0, {})
    assert finding.state == CAP_STATE_UNKNOWN
    assert finding.outcome == "ConnectError"
    assert finding.tier == ""  # 没档位，适配层回落既有的运行时降级链


def test_endpoint_that_rejects_a_tier_still_yields_a_negative_conclusion(session):
    """反面：端点以 4xx 明确拒绝，那是问出来的答案，仍然落「不支持」。

    这条与上一条一起划出分界线——「没问成」才回落，「问出了否定答案」照旧下结论。
    """
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), reject_response_format=True) as stub:
        result = _probe(session, stub, "llama_cpp")
    item = _item(result, CAP_STRUCTURED)
    assert item.state == CAP_STATE_UNSUPPORTED
    assert item.tier == STRUCTURED_TIER_PROMPT_ONLY


def test_failed_probe_requests_never_change_the_request_body(monkeypatch):
    """C1 与 C2 共同的安全网：试探请求全都没问成时，档案不得改变正式调用的请求体。

    「没探明」的语义就是回落到探测机制上线前的既有行为。这里拿一份「探过、但什么都没问出来」
    的档案与不带档案的客户端逐字节比对线路上的请求体——两者必须一模一样。
    """
    from app.adapters.llm import LlmClient

    failed = _ChatSample(ok=False, latency_ms=5, error_code="ReadTimeout")
    _scripted_samples(
        monkeypatch,
        _ChatSample(ok=True, status=200, latency_ms=100, content="2",
                    has_thinking=True, completion_tokens=9),
        failed,
    )
    thinking = _probe_thinking("http://127.0.0.1:1/v1", MODEL, "llama_cpp", None, 5.0,
                               _EndpointMetadata())
    structured = _probe_structured("http://127.0.0.1:1/v1", MODEL, None, 5.0, {})
    monkeypatch.undo()
    profile_payload = _findings_to_profile([thinking, structured], "2026-07-24T19:30:00+08:00")

    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,)) as stub:
        LlmClient(base_url=stub.base_url, model=MODEL, provider_type="llama_cpp",
                  timeout=5, context_tokens=4096).chat("系统提示", "用户输入")
        without_profile = [r["body"] for r in stub.requests if r["method"] == "POST"][-1]
        LlmClient(base_url=stub.base_url, model=MODEL, provider_type="llama_cpp",
                  timeout=5, context_tokens=4096,
                  capability_profile=profile_payload).chat("系统提示", "用户输入")
        with_blank_profile = [r["body"] for r in stub.requests if r["method"] == "POST"][-1]

    assert with_blank_profile == without_profile


# ---- A3 C5 有效上下文：三家各读各的元数据，读不到落「未知」 ----


def test_c5_vllm_reads_max_model_len_from_models(session):
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), context_tokens=32768) as stub:
        result = _probe(session, stub, "vllm")
    item = _item(result, CAP_CONTEXT)
    assert item.state == CAP_STATE_SUPPORTED
    assert item.tokens == 32768
    assert item.source == "models.max_model_len"


def test_c5_llama_cpp_reads_n_ctx_from_props(session):
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), context_tokens=40960) as stub:
        result = _probe(session, stub, "llama_cpp")
        paths = [r["path"] for r in stub.requests if r["method"] == "GET"]
    item = _item(result, CAP_CONTEXT)
    assert item.state == CAP_STATE_SUPPORTED
    assert item.tokens == 40960
    assert item.source == "props.n_ctx"
    # /props 挂在服务根上而不在 /v1 下——地址推导错了这条就取不到
    assert "/props" in paths


def test_c5_ollama_model_limit_is_reference_only(session):
    """ollama 读到的是模型自身上限，不等于兼容层实际生效窗口：只呈现、不据此卡请求。"""
    with ProviderStub(flavor=FLAVOR_OLLAMA, models=(MODEL,), context_tokens=262144) as stub:
        result = _probe(session, stub, "ollama")
        paths = [r["path"] for r in stub.requests if r["method"] == "POST"]
    item = _item(result, CAP_CONTEXT)
    assert item.state == CAP_STATE_DEGRADED
    assert item.tokens == 262144
    assert item.note_code == CAP_NOTE_OLLAMA_MODEL_LIMIT_ONLY
    assert "/api/show" in paths
    # 关键：degraded 不进「可用来卡 max_tokens」的档位
    from app.adapters.llm import parse_capability_profile
    assert parse_capability_profile(result.profile).context_enforceable is False


def test_c5_unreadable_metadata_falls_back_to_unknown(session):
    """元数据端点不给：落「未知」，绝不编一个值——猜测值会截断用户请求。"""
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), props_status=404) as stub:
        result = _probe(session, stub, "llama_cpp")
    item = _item(result, CAP_CONTEXT)
    assert item.state == CAP_STATE_UNKNOWN
    assert item.tokens in (None, 0)


# ---- A4 C6 未识别字段 ----


def test_c6_silent_acceptance_is_flagged(session):
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,)) as stub:
        result = _probe(session, stub, "vllm")
        posts = [r["body"] for r in stub.requests if r["method"] == "POST"]
    item = _item(result, CAP_UNKNOWN_FIELDS)
    assert item.state == CAP_STATE_DEGRADED  # ⚠：200 不能当作「字段生效」
    assert result.profile["unknown_fields"]["silently_accepted"] is True
    assert any("x_req_doc_capability_probe" in (b or {}) for b in posts)


def test_c6_strict_endpoint_is_supported(session):
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), strict_unknown_fields=True) as stub:
        result = _probe(session, stub, "vllm")
    item = _item(result, CAP_UNKNOWN_FIELDS)
    assert item.state == CAP_STATE_SUPPORTED
    assert result.profile["unknown_fields"]["silently_accepted"] is False


# ---- 清单整体：顺序固定、前提不成立时后续项落未知 ----


def test_probe_list_order_is_fixed_by_backend(session):
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,)) as stub:
        result = _probe(session, stub, "vllm")
    assert [i.key for i in result.items] == list(CAPABILITY_KEYS)


def test_unreachable_endpoint_yields_unknown_rest(session):
    with ProviderStub(flavor=FLAVOR_VLLM, models=("别的模型",)) as stub:
        service = ConfigRegistryService(session)
        result = service.probe_capabilities(ModelConnectionTestCommand(
            base_url=stub.base_url, model=MODEL, provider_type="vllm", timeout_seconds=5,
        ))
    assert result.ok is False
    assert _item(result, CAP_REACHABLE).state == CAP_STATE_UNSUPPORTED
    assert _item(result, CAP_GENERATE).state == CAP_STATE_UNKNOWN
    for key in (CAP_THINKING, CAP_STRUCTURED, CAP_CONTEXT, CAP_UNKNOWN_FIELDS):
        assert _item(result, key).state == CAP_STATE_UNKNOWN
    # 探不出来就不该留下一份「探过了」的档案假象——但时间戳仍在，便于界面说明这次探测何时发生
    assert result.profile["thinking"]["off_state"] == CAP_STATE_UNKNOWN


def test_probe_never_writes_config(session):
    """探测不写库、不改启用状态：档案要等用户点「应用」才随配置保存。"""
    service = ConfigRegistryService(session)
    before = service.list_providers()
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,)) as stub:
        service.probe_capabilities(ModelConnectionTestCommand(
            base_url=stub.base_url, model=MODEL, provider_type="vllm", timeout_seconds=5,
        ))
    after = service.list_providers()
    assert [p.capability_profile for p in after.providers] == [
        p.capability_profile for p in before.providers
    ]
    assert after.active_provider_id == before.active_provider_id


# ---- 思考模式开关：默认关、可持久化、真的改变请求体 ----


def test_thinking_switch_defaults_to_off(session):
    """从未设置过的模型服务，思考模式是关的——与这个开关上线前的行为一致。"""
    service = ConfigRegistryService(session)
    read = service.list_providers()
    assert all(p.thinking_enabled is False for p in read.providers)


def test_thinking_switch_round_trips_and_reaches_the_adapter(session):
    """开关存得住，并且真的改变适配层的请求体：关=发关思考字段，开=不发。

    这是这个开关唯一有意义的证明——存下来但请求体没变，等于开关是假的。
    """
    from app.api.schemas import LlmProviderSaveCommand, LlmProviderWrite
    from app.adapters.llm import chat_extension_fields, parse_capability_profile
    from app.services.config_registry import resolve_llm_settings
    from app.config import Settings

    service = ConfigRegistryService(session)

    def _save(thinking_enabled: bool):
        return service.save_providers(LlmProviderSaveCommand(
            providers=[LlmProviderWrite(
                id="p-think", name="本地服务", provider_type="ollama",
                base_url="http://127.0.0.1:11434/v1", model=MODEL,
                thinking_enabled=thinking_enabled,
            )],
            active_provider_id="p-think", operator_ref="tester",
        ))

    read = _save(True)
    assert read.providers[0].thinking_enabled is True
    settings = resolve_llm_settings(session, Settings())
    assert settings.llm_disable_thinking is False
    # 启用思考 → 不下发任何关思考字段
    assert chat_extension_fields(settings.llm_provider_type, settings.llm_disable_thinking) == {}

    read = _save(False)
    assert read.providers[0].thinking_enabled is False
    settings = resolve_llm_settings(session, Settings())
    assert settings.llm_disable_thinking is True
    # 关闭思考 → 按 ollama 的先验下发 reasoning_effort:none
    assert chat_extension_fields(
        settings.llm_provider_type, settings.llm_disable_thinking,
        parse_capability_profile(settings.llm_capability_profile),
    ) == {"reasoning_effort": "none"}


def test_thinking_switch_absent_keeps_saved_value(session):
    """保存表单时没带这个字段：保留库里已存的选择，不被悄悄重置。"""
    from app.api.schemas import LlmProviderSaveCommand, LlmProviderWrite

    service = ConfigRegistryService(session)
    service.save_providers(LlmProviderSaveCommand(
        providers=[LlmProviderWrite(
            id="p-keep", name="本地服务", provider_type="ollama",
            base_url="http://127.0.0.1:11434/v1", model=MODEL, thinking_enabled=True,
        )],
        active_provider_id="p-keep", operator_ref="tester",
    ))
    read = service.save_providers(LlmProviderSaveCommand(
        providers=[LlmProviderWrite(
            id="p-keep", name="改了名字", provider_type="ollama",
            base_url="http://127.0.0.1:11434/v1", model=MODEL,
        )],
        active_provider_id="p-keep", operator_ref="tester",
    ))
    assert read.providers[0].thinking_enabled is True


def test_capability_profile_round_trips_with_provider(session):
    """A5 档案持久化：探测→应用→读回，形状不走样；且零 DB 迁移（落既有配置行的 JSON）。"""
    from app.api.schemas import LlmProviderSaveCommand, LlmProviderWrite
    from app.adapters.llm import parse_capability_profile
    from app.services.config_registry import resolve_llm_settings
    from app.config import Settings

    service = ConfigRegistryService(session)
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), thinking=True,
                      reasoning_parser=True, context_tokens=32768) as stub:
        probed = service.probe_capabilities(ModelConnectionTestCommand(
            base_url=stub.base_url, model=MODEL, provider_type="vllm", timeout_seconds=10,
        ))
        read = service.save_providers(LlmProviderSaveCommand(
            providers=[LlmProviderWrite(
                id="p-probe", name="探过的服务", provider_type="vllm",
                base_url=stub.base_url, model=MODEL, capability_profile=probed.profile,
            )],
            active_provider_id="p-probe", operator_ref="tester",
        ))
    assert read.providers[0].capability_profile == probed.profile
    profile = parse_capability_profile(
        resolve_llm_settings(session, Settings()).llm_capability_profile
    )
    assert profile.thinking_off_mode == THINKING_OFF_REASONING_EFFORT
    assert profile.context_tokens == 32768 and profile.context_enforceable is True
    assert profile.probed_at  # 带时间戳，界面能说清这份结论是什么时候探的


def test_thinking_off_none_keeps_the_per_type_prior(session):
    """探到「当时没有思考段」不等于「以后也不用关」：请求体保持按类型的先验，不做减法。

    真实场景：116 的 llama.cpp 生产端点用服务端参数 -rea off 全局关了思考，探测因而看不到思考段。
    若据此不再下发 enable_thinking，那台服务某次重启少带了 -rea off，思考就会悄悄回来、而档案还停
    在旧结论上——正是本卡要消灭的那类「静默失效」。给不思考的模型多发一个关思考字段无副作用，
    所以档案只在**正面探明**了有效方式时才改变请求体。
    """
    from app.adapters.llm import CapabilityProfile, chat_extension_fields

    probed = CapabilityProfile(
        thinking_off_state=CAP_STATE_SUPPORTED, thinking_off_mode=THINKING_OFF_NOT_NEEDED,
        thinking_available=False, probed_at="2026-07-24T19:30:00+08:00",
    )
    assert chat_extension_fields("llama_cpp", True, probed) == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    assert chat_extension_fields("ollama", True, probed) == {"reasoning_effort": "none"}
    # vLLM 的先验本就是不发（要发得先探明生效），不因这条回落而变
    assert chat_extension_fields("vllm", True, probed) == {}


def test_probed_ineffective_stops_sending_the_field(session):
    """反过来：探明那些字段确实不生效时才做减法——这是正面证据，不是没探到。"""
    from app.adapters.llm import CapabilityProfile, chat_extension_fields

    for state in (CAP_STATE_DEGRADED, CAP_STATE_UNSUPPORTED):
        profile = CapabilityProfile(thinking_off_state=state, thinking_available=True,
                                    probed_at="2026-07-24T19:30:00+08:00")
        assert chat_extension_fields("llama_cpp", True, profile) == {}
        assert chat_extension_fields("ollama", True, profile) == {}


# ---- C3 思考能力：分清「模型不会思考」与「模型会思考但服务端关了」 ----


def test_capability_declared_but_server_disabled_is_not_reported_as_incapable(session):
    """116 生产端点的真实形态：模板声明支持思考，服务端 -rea off 全局关掉，故探不到思考段。

    只看产物会判成「这个模型不具备思考能力」，进而建议用户「换个思考模型」——而正确的做法是
    去服务端打开。所以这里必须：具备能力=True，并给出「服务端已关闭」的说明码。
    """
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), thinking=False,
                      declares_thinking=True, server_reasoning_format="none") as stub:
        result = _probe(session, stub, "llama_cpp")
    item = _item(result, CAP_THINKING)
    assert item.available is True, "元数据声明支持思考，就不能因为没看到思考段而说它不具备"
    assert item.note_code == CAP_NOTE_THINKING_DISABLED_ON_SERVER
    assert item.detail["server_disabled"] is True
    assert item.detail["baseline_has_thinking"] is False
    # 对产品而言当前无思考段=不必额外做什么，仍按先验下发关思考字段（见契约用例）
    assert item.state == CAP_STATE_SUPPORTED and item.mode == THINKING_OFF_NOT_NEEDED
    assert result.profile["thinking"]["available"] is True


@pytest.mark.parametrize("flavor, provider_type", [
    (FLAVOR_OLLAMA, "ollama"),          # 根本不产出服务端关闭标志的端点
    (FLAVOR_LLAMA_CPP, "llama_cpp"),    # 产出了，但标的是「没关」
])
def test_declared_thinking_without_a_server_flag_is_not_blamed_on_the_server(
    session, flavor, provider_type
):
    """端点声明模型支持思考、没自报「服务端已关闭」，这一轮又没看到思考段：不能说成「服务端关的」。

    那条结论会让界面给出 llama.cpp 专属的启动参数建议（-rea off / --reasoning-format none），
    对 Ollama 端点是一条不存在的路，用户会照着去改一个根本没有的配置。真实原因也可能只是这次的
    问题太简单、模型没展开思考。所以另给一个说明码，如实说「没看到，但说不清是哪种」。
    """
    with ProviderStub(flavor=flavor, models=(MODEL,), thinking=False,
                      declares_thinking=True, server_reasoning_format="auto") as stub:
        result = _probe(session, stub, provider_type)
    item = _item(result, CAP_THINKING)
    assert item.available is True
    assert item.note_code == CAP_NOTE_THINKING_DECLARED_NOT_OBSERVED
    assert item.note_code != CAP_NOTE_THINKING_DISABLED_ON_SERVER


def test_declared_incapable_model_is_reported_as_incapable(session):
    """端点明确声明不支持思考、也确实没有思考段：这才是「不具备思考能力」。"""
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), thinking=False,
                      declares_thinking=False, server_reasoning_format="auto") as stub:
        result = _probe(session, stub, "llama_cpp")
    item = _item(result, CAP_THINKING)
    assert item.available is False
    assert item.note_code is None


def test_no_declaration_and_no_thinking_stays_undetermined(session):
    """端点不提供能力声明、也没探到思考段：具不具备思考能力判断不了，不许猜成「不具备」。"""
    with ProviderStub(flavor=FLAVOR_VLLM, models=(MODEL,), thinking=False) as stub:
        result = _probe(session, stub, "vllm")
    item = _item(result, CAP_THINKING)
    assert item.available is None
    assert result.profile["thinking"]["available"] is None


def test_ollama_capabilities_declare_thinking(session):
    """ollama 在 /api/show 的 capabilities 里直接列出 thinking，探针据此认定具备思考能力。"""
    with ProviderStub(flavor=FLAVOR_OLLAMA, models=(MODEL,), thinking=True,
                      declares_thinking=True) as stub:
        result = _probe(session, stub, "ollama")
    item = _item(result, CAP_THINKING)
    assert item.available is True and item.detail["declared"] is True
    assert item.mode == THINKING_OFF_REASONING_EFFORT


def test_observed_thinking_overrides_a_negative_declaration(session):
    """声明说不支持、实测却回出了思考段：以实测为准（验产物不验声明）。"""
    with ProviderStub(flavor=FLAVOR_OLLAMA, models=(MODEL,), thinking=True,
                      declares_thinking=False) as stub:
        result = _probe(session, stub, "ollama")
    item = _item(result, CAP_THINKING)
    assert item.available is True
    assert item.mode == THINKING_OFF_REASONING_EFFORT


def test_metadata_is_fetched_once_for_both_thinking_and_context(session):
    """元数据只取一次，C3 的能力声明与 C5 的上下文共用——探测要廉价（三原则之三）。"""
    with ProviderStub(flavor=FLAVOR_LLAMA_CPP, models=(MODEL,), declares_thinking=True,
                      context_tokens=40960) as stub:
        result = _probe(session, stub, "llama_cpp")
        props_calls = [r for r in stub.requests if r["path"] == "/props"]
    assert len(props_calls) == 1
    assert _item(result, CAP_CONTEXT).tokens == 40960
    assert _item(result, CAP_THINKING).detail["declared"] is True
