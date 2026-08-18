"""模型服务适配器 —— 隔离外部 LLM（llama.cpp OpenAI 兼容 /v1/chat/completions）。

来源接入判断：把提交文本交给模型，四选一并给出简短依据。
隔离底线（VAL-002 / AC-005）：只返回分类+依据，不外泄/持久化 Prompt 与原始响应；
任何失败（不可用/超时/结果不可解析）→ JUDGEMENT_FAILED（VAL-005 失败不污染事实）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from app.adapters.prompts.environment import dumps as prompt_dumps
from app.adapters.prompts.environment import render_pair
from app.config import Settings
from app.domain.revision_points import validate_points
from app.domain import item_profiles, rubrics
from app.domain.enums import (
    ChartFindingType,
    DiagnosisMode,
    EarsPattern,
    ElementType,
    ModelJudgement,
    ModelVerdict,
    NoiseTriage,
    QualityDimension,
    QualitySeverity,
    RequirementQualityRule,
    ReviewConclusion,
    ReviewFindingType,
    VerdictKind,
    VerificationMethod,
)
from app.domain.enums import ReviewConclusion as RC
from app.log import log_event

_LLM_COMPONENT = "llm-adapter"


class LlmError(Exception):
    """外部 LLM 调用/响应错误。"""


def _estimate_tokens(text: str) -> int:
    """粗估 token 数（不依赖分词器、不触网）：CJK≈0.7 tok/字，其余≈0.3 tok/字符。

    偏保守（略高估），仅用于"提示词超长"告警阈值——宁可早告警，不漏告警。
    """
    cjk = 0
    for ch in text:
        o = ord(ch)
        if 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x30FF or 0xFF00 <= o <= 0xFFEF:
            cjk += 1
    return int(cjk * 0.7 + (len(text) - cjk) * 0.3) + 1


@dataclass(frozen=True)
class IntakeJudgement:
    judgement: ModelJudgement
    basis: str


class SourceIntakeJudge(Protocol):
    """来源接入判断能力（适配器实现之；stub 供无模型/测试）。"""

    def judge(self, project_ref: str, raw_text: str, source_note: str) -> IntakeJudgement: ...


# 输出 JSON 形状与解析器同文件定义（模板经 output_schema 变量渲染，消灭提示词/解析器双写）。
# judgement_failed 为系统侧失败语义（VAL-005），不供模型选择。
_INTAKE_OUTPUT = {
    "judgement": "|".join(j.value for j in ModelJudgement if j is not ModelJudgement.JUDGEMENT_FAILED),
    "basis": "<一句中文判定依据，≤50字>",
}

_JUDGEMENT_MAP = {
    "acceptable": ModelJudgement.ACCEPTABLE,
    "可接入": ModelJudgement.ACCEPTABLE,
    "insufficient_content": ModelJudgement.INSUFFICIENT_CONTENT,
    "内容不足": ModelJudgement.INSUFFICIENT_CONTENT,
    "unclear_attribution": ModelJudgement.UNCLEAR_ATTRIBUTION,
    "归属不明": ModelJudgement.UNCLEAR_ATTRIBUTION,
    "no_asset_value": ModelJudgement.NO_ASSET_VALUE,
    "无需求资产价值": ModelJudgement.NO_ASSET_VALUE,
    "无价值": ModelJudgement.NO_ASSET_VALUE,
}


def _map_judgement(raw: object) -> Optional[ModelJudgement]:
    key = str(raw or "").strip()
    return _JUDGEMENT_MAP.get(key) or _JUDGEMENT_MAP.get(key.lower())


def _extract_json_object(text: str) -> dict:
    """防御式解析：容忍 ```json 围栏与前后杂字，取第一个 {...}。"""
    if not isinstance(text, str) or not text.strip():
        # 端点 200 但 content 为 null/空（reasoning 吃满 token、拒答、网关怪癖）：
        # 统一走 ValueError=parse 关，禁止 AttributeError 逃逸炸整批（设计裁定 5）。
        raise ValueError("模型回复为空或非文本（content 缺失）")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("响应中未找到 JSON 对象")
    return json.loads(text[start : end + 1])


# 结构化输出降级链（诊断可靠性设计裁定 1）：json_schema → json_object → None（纯提示词）。
_STRUCTURED_TIER_FALLBACK = {"json_schema": "json_object", "json_object": None}
_STRUCTURED_INITIAL_TIER = {"auto": "json_schema", "json_schema": "json_schema", "json_object": "json_object"}
# 明确与 response_format 能力无关的 4xx（鉴权/限流/体积/超时类）：不降档，原样抛给上层按
# llm_error 处置。残留风险：400 超上下文与 400 拒 response_format 仅凭状态码无法区分（issue 记账）。
_NON_CAPABILITY_4XX = {401, 402, 403, 407, 408, 413, 429}

# 按探明的有效上下文卡输出上限时用的两个数（见 LlmClient._effective_max_tokens）：
# 余量吸收 token 估算误差（_estimate_tokens 是按字符数估的，不是真分词）；下限保证即便提示词
# 已占满窗口，也还给模型留一点输出空间，而不是把 max_tokens 压成 0 或负数。
_CONTEXT_SAFETY_MARGIN = 128
_MAX_TOKENS_FLOOR = 256


# ============================================================================
# 模型服务 provider 类型（T20260720-model-provider-registry）
# ----------------------------------------------------------------------------
# 本仓只对接各推理引擎的 **OpenAI 兼容面**（base_url 含 /v1、实现 POST /chat/completions）；
# ollama 原生 /api 不支持。类型的唯一作用是决定请求体里带哪些**非标准扩展字段**——
# 这类差异全部在本适配器层吸收，lane 层与提示词不感知 provider 类型。
# 键与显示名在此单点定义：配置服务经 API 投影给前端，前端不得另写一份类型清单。
# ============================================================================

PROVIDER_LLAMA_CPP = "llama_cpp"
PROVIDER_OLLAMA = "ollama"
PROVIDER_VLLM = "vllm"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"

# (键, 显示名, 一句话说明)；显示名用产品名（界面用语纪律允许），说明供设置页下拉。
PROVIDER_TYPES: tuple[tuple[str, str, str], ...] = (
    (PROVIDER_LLAMA_CPP, "llama.cpp", "llama.cpp 自带的 OpenAI 兼容服务，地址通常形如 http://主机:8080/v1"),
    (PROVIDER_OLLAMA, "Ollama", "Ollama 的 OpenAI 兼容层，地址通常形如 http://主机:11434/v1；模型标识需带标签，如 qwen2.5:7b"),
    (PROVIDER_VLLM, "vLLM", "vLLM 的 OpenAI 兼容服务，地址通常形如 http://主机:8000/v1"),
    (PROVIDER_OPENAI_COMPATIBLE, "通用 OpenAI 兼容", "其他兼容 OpenAI 接口的服务（如云端模型平台）"),
)
PROVIDER_TYPE_KEYS: tuple[str, ...] = tuple(key for key, _, _ in PROVIDER_TYPES)
DEFAULT_PROVIDER_TYPE = PROVIDER_LLAMA_CPP


# ============================================================================
# 能力探测档案（T20260724-capability-probe-panel）
# ----------------------------------------------------------------------------
# 按 provider 类型写死的适配（下面 chat_extension_fields 那套）只是**先验默认**：同一类型的
# 不同版本、不同服务端启动参数，实际能力并不相同（vLLM 关思考要服务端起 --reasoning-parser；
# ollama 的有效上下文由 Modelfile 定），客户端看不见，只能对具体端点探测。设置页探到的事实
# 存成一份**能力档案**随该 provider 配置持久化，经 Settings 送到这里构造请求。
#
# 本节是档案的唯一形状定义：探针（services/config_registry.py）按此产出、接口层按此投影、
# 适配层按此消费，三侧都不得另写一份字段名或取值集。
# 设计依据：docs/proposals/llm-provider-feasibility/能力探测与参数适配提案.md 第三部分。
# ============================================================================

# 六项能力的键。前两项由既有两级连通测试承担，只上清单、不进档案（它们不改请求怎么构造）；
# 后四项是本机制新增的探测项，结论进档案供适配层消费。
CAP_REACHABLE = "reachable"            # C1 可达＋模型在列
CAP_GENERATE = "generate"              # C2 能生成
# C3 思考能力：这个模型会不会思考、当前是不是在思考、能不能关掉。三问一行答完——
# 它们是同一件事的三个面，分成三行会逼读者自己拼。
CAP_THINKING = "thinking"
CAP_STRUCTURED = "structured"          # C4 结构化输出
CAP_CONTEXT = "context"                # C5 有效上下文
CAP_UNKNOWN_FIELDS = "unknown_fields"  # C6 未识别字段是否静默接受
# 清单的固定顺序（界面逐条呈现即按此序，由后端定，前端不另排）。
CAPABILITY_KEYS: tuple[str, ...] = (
    CAP_REACHABLE, CAP_GENERATE, CAP_THINKING, CAP_STRUCTURED, CAP_CONTEXT, CAP_UNKNOWN_FIELDS,
)

# 每项能力的结论（封闭集）。后端只回这些稳定代码，白话文案由前端映射。
CAP_STATE_SUPPORTED = "supported"      # 探明可用
CAP_STATE_DEGRADED = "degraded"        # 探明有条件（附已采取的降级或所需服务端参数）
CAP_STATE_UNSUPPORTED = "unsupported"  # 探明不可用
CAP_STATE_UNKNOWN = "unknown"          # 没探明（超时/端点没给元数据）——一律回落先验，绝不猜
CAP_STATES: tuple[str, ...] = (
    CAP_STATE_SUPPORTED, CAP_STATE_DEGRADED, CAP_STATE_UNSUPPORTED, CAP_STATE_UNKNOWN,
)

# 关思考的方式（封闭集）：三家引擎认的字段各不相同，实测见提案第一部分结论 2。
THINKING_OFF_REASONING_EFFORT = "reasoning_effort"  # ollama 的兼容面认这个
THINKING_OFF_ENABLE_THINKING = "enable_thinking"    # llama.cpp（配 Qwen3 类模板）认这个
THINKING_OFF_NOT_NEEDED = "none"                    # 该端点无需/无法用请求字段关：什么都不发
THINKING_OFF_MODES: tuple[str, ...] = (
    THINKING_OFF_REASONING_EFFORT, THINKING_OFF_ENABLE_THINKING, THINKING_OFF_NOT_NEEDED,
)

# 结构化输出的档位（封闭集，与既有降级链同名）。
STRUCTURED_TIER_JSON_SCHEMA = "json_schema"
STRUCTURED_TIER_JSON_OBJECT = "json_object"
STRUCTURED_TIER_PROMPT_ONLY = "prompt_only"
STRUCTURED_TIERS: tuple[str, ...] = (
    STRUCTURED_TIER_JSON_SCHEMA, STRUCTURED_TIER_JSON_OBJECT, STRUCTURED_TIER_PROMPT_ONLY,
)

# 附加说明码（封闭集）：探测结论之外还需要告诉用户的那一句话，界面据此给白话提示。
# 需要改服务端启动参数才能拿到的能力：
CAP_NOTE_VLLM_NEEDS_REASONING_PARSER = "vllm_needs_reasoning_parser"
# 只探到参考值而非实际生效值（ollama 的 /api/show 报的是模型自身上限，兼容层生效窗口更小）：
CAP_NOTE_OLLAMA_MODEL_LIMIT_ONLY = "ollama_model_limit_only"
# 端点不把思考段单独回出来，故「没看到思考段」不足以断定它没在思考：
CAP_NOTE_THINKING_SEGMENT_HIDDEN = "thinking_segment_hidden"
# 模型具备思考能力，但**服务端**把它全局关掉了（llama.cpp 的 -rea off / --reasoning-format none）：
# 此时界面上的思考开关打开也不会有思考，要改的是服务端启动参数，不是换模型。
# 只有端点**自报了**服务端关闭标志时才用这个码，不能光凭「声明支持却没看到思考段」就断定。
CAP_NOTE_THINKING_DISABLED_ON_SERVER = "thinking_disabled_on_server"
# 端点声明模型支持思考，但这一轮探测没看到思考段，而端点也没自报「服务端已关闭」：
# 说不清是这次的问题太简单没触发思考，还是服务端关掉了。不许沿用上一条那句 llama.cpp 专属建议。
CAP_NOTE_THINKING_DECLARED_NOT_OBSERVED = "thinking_declared_not_observed"

# 关思考方式的 per-type 先验：没有档案时按这张表下发，即探测机制上线前的既有行为。
_THINKING_OFF_PRIOR: dict[str, str] = {
    PROVIDER_LLAMA_CPP: THINKING_OFF_ENABLE_THINKING,
    PROVIDER_OLLAMA: THINKING_OFF_REASONING_EFFORT,
    # vLLM / 通用兼容端点先验为「不发」：vLLM 的 reasoning_effort 生效依赖服务端
    # --reasoning-parser，盲发会被静默接受回 200 而实际没关（假成功）。要发得先探明。
    PROVIDER_VLLM: THINKING_OFF_NOT_NEEDED,
    PROVIDER_OPENAI_COMPATIBLE: THINKING_OFF_NOT_NEEDED,
}

# C3 探针的候选试探顺序（首个验出生效即停）：先试该类型最可能的那个，再试另一个。
THINKING_OFF_CANDIDATES: dict[str, tuple[str, ...]] = {
    PROVIDER_LLAMA_CPP: (THINKING_OFF_ENABLE_THINKING, THINKING_OFF_REASONING_EFFORT),
    PROVIDER_OLLAMA: (THINKING_OFF_REASONING_EFFORT, THINKING_OFF_ENABLE_THINKING),
    PROVIDER_VLLM: (THINKING_OFF_REASONING_EFFORT, THINKING_OFF_ENABLE_THINKING),
    PROVIDER_OPENAI_COMPATIBLE: (THINKING_OFF_REASONING_EFFORT, THINKING_OFF_ENABLE_THINKING),
}


def thinking_off_fields(mode: str) -> dict:
    """关思考方式 → 请求体里的字段。正式调用、连通测试、C3 探针共用这一份映射。"""
    if mode == THINKING_OFF_ENABLE_THINKING:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if mode == THINKING_OFF_REASONING_EFFORT:
        return {"reasoning_effort": "none"}
    return {}


@dataclass(frozen=True)
class CapabilityProfile:
    """一个端点的能力档案：探到什么就记什么，没探到的一律留 unknown。

    字段扁平（消费方读起来直接），持久化时经 `to_payload` 折成按能力分组的 JSON——
    存储形状要能逐项加说明码，扁平形状则免去消费方层层取值。
    """

    thinking_off_state: str = CAP_STATE_UNKNOWN
    thinking_off_mode: str = ""
    # 这个端点/模型到底会不会思考（基线请求里探到思考段就是会）。None=没探明。
    # 与 thinking_off_state 是两件事：前者说「有没有思考这回事」，后者说「能不能关掉」。
    # 界面上的「思考模式」开关据此说明「打开了有没有用」。
    thinking_available: bool | None = None
    structured_state: str = CAP_STATE_UNKNOWN
    structured_tier: str = ""
    context_state: str = CAP_STATE_UNKNOWN
    context_tokens: int = 0
    context_source: str = ""
    unknown_fields_state: str = CAP_STATE_UNKNOWN
    unknown_fields_silently_accepted: bool = False
    notes: tuple[str, ...] = ()
    probed_at: str = ""

    @property
    def probed(self) -> bool:
        """探测过（有时间戳）——用于日志区分「按档案走」还是「按先验走」。"""
        return bool(self.probed_at)

    @property
    def thinking_off_decided(self) -> bool:
        """关思考这一项有没有结论。有结论就按结论办，没结论才回落 per-type 先验。"""
        return self.thinking_off_state != CAP_STATE_UNKNOWN

    @property
    def structured_decided(self) -> bool:
        return (
            self.structured_state != CAP_STATE_UNKNOWN
            and self.structured_tier in STRUCTURED_TIERS
        )

    @property
    def context_enforceable(self) -> bool:
        """能不能拿这个上下文值去卡 max_tokens。

        只有 supported（探到端点**实际生效**的窗口）才允许卡。degraded 表示只探到参考值
        （如 ollama /api/show 给的是模型自身上限，兼容层实际生效窗口更小），拿它截断用户
        请求就是用猜测值截断——探测三原则明令禁止，故只呈现不生效。
        """
        return self.context_state == CAP_STATE_SUPPORTED and self.context_tokens > 0

    def to_payload(self) -> dict:
        """持久化形状（落 provider 配置的 JSON）。"""
        return {
            CAP_THINKING: {
                # available=模型具不具备思考能力；off_state/off_mode=能不能关、用什么关。
                # 分开记是因为两者会各自成立：具备能力但服务端已关（available=true、当前无思考段），
                # 或会思考却关不掉（available=true、off_state=degraded）。
                "available": self.thinking_available,
                "off_state": self.thinking_off_state,
                "off_mode": self.thinking_off_mode,
            },
            CAP_STRUCTURED: {"state": self.structured_state, "tier": self.structured_tier},
            CAP_CONTEXT: {
                "state": self.context_state,
                "tokens": self.context_tokens,
                "source": self.context_source,
            },
            CAP_UNKNOWN_FIELDS: {
                "state": self.unknown_fields_state,
                "silently_accepted": self.unknown_fields_silently_accepted,
            },
            "notes": list(self.notes),
            "probed_at": self.probed_at,
            "probe_version": CAPABILITY_PROBE_VERSION,
        }


# 档案结构的版本号：日后探针口径变了（判据换了、能力项增删），据此判定旧档案要不要重探。
CAPABILITY_PROBE_VERSION = 1

EMPTY_CAPABILITY_PROFILE = CapabilityProfile()


def _cap_state(raw: object) -> str:
    return raw if isinstance(raw, str) and raw in CAP_STATES else CAP_STATE_UNKNOWN


def parse_capability_profile(raw: object) -> CapabilityProfile:
    """档案 JSON（字符串或已解析的字典）→ CapabilityProfile。

    宽容解析：空、坏形状、越界取值一律回落「没探明」，绝不抛异常——配置面的一处脏数据不该
    让整条 LLM 链路不可用，而「没探明」的语义恰好就是回落到探测机制上线前的既有行为。
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return EMPTY_CAPABILITY_PROFILE
        try:
            raw = json.loads(text)
        except ValueError:
            log_event(_LLM_COMPONENT, "llm.capability.profile_unparsable", level="WARN")
            return EMPTY_CAPABILITY_PROFILE
    if not isinstance(raw, dict):
        return EMPTY_CAPABILITY_PROFILE

    def _section(key: str) -> dict:
        value = raw.get(key)
        return value if isinstance(value, dict) else {}

    thinking, structured = _section(CAP_THINKING), _section(CAP_STRUCTURED)
    context, unknown = _section(CAP_CONTEXT), _section(CAP_UNKNOWN_FIELDS)
    mode = thinking.get("off_mode")
    available = thinking.get("available")
    tier = structured.get("tier")
    try:
        tokens = int(context.get("tokens") or 0)
    except (TypeError, ValueError):
        tokens = 0
    notes = raw.get("notes")
    return CapabilityProfile(
        thinking_off_state=_cap_state(thinking.get("off_state")),
        thinking_off_mode=mode if isinstance(mode, str) and mode in THINKING_OFF_MODES else "",
        thinking_available=available if isinstance(available, bool) else None,
        structured_state=_cap_state(structured.get("state")),
        structured_tier=tier if isinstance(tier, str) and tier in STRUCTURED_TIERS else "",
        context_state=_cap_state(context.get("state")),
        context_tokens=max(tokens, 0),
        context_source=str(context.get("source") or ""),
        unknown_fields_state=_cap_state(unknown.get("state")),
        unknown_fields_silently_accepted=bool(unknown.get("silently_accepted")),
        notes=tuple(str(n) for n in notes) if isinstance(notes, list) else (),
        probed_at=str(raw.get("probed_at") or ""),
    )


def chat_extension_fields(
    provider_type: str, disable_thinking: bool, profile: CapabilityProfile | None = None
) -> dict:
    """请求体里的**非标准扩展字段**：关思考字段按能力档案分发，没档案则按 provider 类型。

    优先级是**档案 > 先验**：设置页对这个端点探明了关思考方式，就按探明的那个字段发；
    没探明（或从未探测）才回落下面这张按类型写死的先验表——因而未探测过的 provider 请求体
    与探测机制上线前逐字节一致（契约桩件有负向断言钉住）。

    先验表的由来（116 真实端点实测，2026-07-24，见提案第一部分结论 2）：
    `chat_template_kwargs.enable_thinking` 是 llama.cpp（配 Qwen3 类模板）的专属参数；
    ollama 的 OpenAI 兼容层**不认**它（静默丢弃、思考照跑，仍 24s），关思考走
    `reasoning_effort:"none"`（生效 1.4s）；vLLM 与通用兼容端点先验为不发任何字段——
    vLLM 的 reasoning_effort 要服务端起 `--reasoning-parser` 才生效，盲发会被静默接受回 200
    而实际没关（假成功），所以要发得先由 C3 探针验出它真生效。
    不关思考的后果：思考模型带思考跑，重流程慢 20–50 倍直至超时。

    本函数是「哪些扩展字段发给哪个端点」的单一来源：正式调用（`_base_payload`）与设置页
    连通测试（最小生成请求）都从这里取，契约桩件的请求体断言才有意义。
    """
    if not disable_thinking:
        return {}
    if profile is not None and profile.thinking_off_decided:
        if profile.thinking_off_state == CAP_STATE_SUPPORTED:
            if profile.thinking_off_mode == THINKING_OFF_NOT_NEEDED:
                # 探测时没看到思考段（比如服务端已用 -rea off 全局关掉了）。这只说明「当时不需要
                # 关」，不说明「以后也不需要」：服务端某次重启少带了那个参数，思考就会悄悄回来，
                # 而档案还停在旧结论上。给不思考的模型多发一个关思考字段没有任何副作用，因此这里
                # 保持按类型的先验下发——档案只在**正面探明**了更好的方式时才改变请求体。
                return thinking_off_fields(
                    _THINKING_OFF_PRIOR.get(provider_type, THINKING_OFF_NOT_NEEDED)
                )
            return thinking_off_fields(profile.thinking_off_mode)
        # 探明关不掉（unsupported）或有条件（degraded，如 vLLM 缺服务端 --reasoning-parser）：
        # 什么都不发。这个是正面证据——那些字段试过了确实不生效，发了只会让请求体里多一个被
        # 静默接受的字段、掩盖真实情况。
        return {}
    return thinking_off_fields(_THINKING_OFF_PRIOR.get(provider_type, THINKING_OFF_NOT_NEEDED))


def minimal_chat_payload(
    model: str,
    provider_type: str,
    max_tokens: int = 96,
    profile: CapabilityProfile | None = None,
) -> dict:
    """设置页第二级连通测试用的最小生成请求体（短输出、固定提示词，只为验证能真的回话）。"""
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "请回复 OK 两个字。"}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    payload.update(chat_extension_fields(provider_type, disable_thinking=True, profile=profile))
    return payload


class LlmClient:
    """最小 OpenAI 兼容聊天客户端。base_url 需含 /v1（如 http://host:8080/v1）。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        max_tokens: int = 256,
        disable_thinking: bool = True,
        client: Optional[httpx.Client] = None,
        context_tokens: int = 0,
        api_key: Optional[str] = None,
        structured_output: str = "off",
        provider_type: str = DEFAULT_PROVIDER_TYPE,
        capability_profile: Optional[CapabilityProfile] = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._disable_thinking = disable_thinking
        # provider 类型只影响请求体里的非标准扩展字段（见 chat_extension_fields）；
        # 未知值不落任何已知分支、因而不发扩展字段——宁可少发也不发端点不认的字段。
        self._provider_type = provider_type or DEFAULT_PROVIDER_TYPE
        # 设置页对本端点探到的能力档案（None=从未探测）：关思考发哪个字段、结构化输出从哪档起、
        # 单次输出上限卡在哪，三处都优先按它，没探明的项各自回落既有先验。
        self._profile = capability_profile or EMPTY_CAPABILITY_PROFILE
        # 上下文窗口（token）；>0 时启用"提示词超长"告警。0 = 关闭（如测试/未配置）。
        # 探明了端点实际生效窗口就以探明值为准——配置里的那个数是先验，端点上的才是事实。
        self._context_tokens = (
            self._profile.context_tokens if self._profile.context_enforceable else context_tokens
        )
        # 结构化输出当前档位（None=纯提示词）；探测失败原地降档并缓存（每客户端=每端点一次）。
        # 档案已探明该端点真正强制生效的档位时直接从那一档起，不再在正式请求上试探降级——
        # 降级链退居安全网（档案过期/漏网时仍兜底）。
        self._structured_tier: Optional[str] = _STRUCTURED_INITIAL_TIER.get(structured_output)
        if structured_output != "off" and structured_output not in _STRUCTURED_INITIAL_TIER:
            # 未识别的配置值若静默等同 off，特性关闭将不可发现：如实告警（配置值非密钥）。
            log_event(_LLM_COMPONENT, "llm.structured.config_unrecognized", level="WARN",
                      value=structured_output, effective_tier="prompt_only")
        if structured_output != "off" and self._profile.structured_decided:
            probed = self._profile.structured_tier
            self._structured_tier = None if probed == STRUCTURED_TIER_PROMPT_ONLY else probed
        # 密钥只进请求头，不落属性名文/日志（AGENTS.md 硬规则 8）。
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout, headers=headers)

    def _warn_if_prompt_too_long(self, system: str, user: str) -> None:
        """估算 输入 token + 输出上限 是否超过上下文窗口，超过则告警（只记计数，不记原文）。"""
        if self._context_tokens <= 0 or self._profile.context_enforceable:
            # 探明了实际生效窗口时改由 `_effective_max_tokens` 一处告警并真的卡住输出上限，
            # 这里不再重复报同一件事（一个条件两行 WARN 会让日志读者以为出了两个问题）。
            return
        prompt_tokens = _estimate_tokens(system) + _estimate_tokens(user)
        budget = prompt_tokens + self._max_tokens
        if budget > self._context_tokens:
            log_event(
                _LLM_COMPONENT,
                "llm.prompt.too_long",
                level="WARN",
                prompt_tokens_est=prompt_tokens,
                max_tokens=self._max_tokens,
                context_tokens=self._context_tokens,
                overflow_est=budget - self._context_tokens,
            )

    def _effective_max_tokens(self, system: str, user: str) -> int:
        """按探明的有效上下文卡住单次输出上限：提示词 + 输出上限必须落在窗口内。

        为什么必须卡：vLLM 对 `prompt_tokens + max_tokens > max_model_len` 的请求直接返 400，
        而适配层把 400 吞成「模型不可用或结果不可解析」的笼统文案——2026-07-24 Gemma4 首跑四条
        业务流程瞬时全败就是这么来的（服务端 --max-model-len 8192 对上请求 max_tokens 8192），
        排查了很久才定位到是参数越界而非模型问题。llama.cpp 与 ollama 则是静默截断提示词，
        模型只看到半截输入、产物质量下降且不报错，同样要卡。

        只在 C5 探明端点**实际生效**窗口时才卡（`context_enforceable`）：没探明就照配置原样下发，
        绝不拿先验或猜测值截断用户请求（探测三原则，评审意见 2）。
        """
        if not self._profile.context_enforceable:
            return self._max_tokens
        prompt_tokens = _estimate_tokens(system) + _estimate_tokens(user)
        budget = self._context_tokens - prompt_tokens - _CONTEXT_SAFETY_MARGIN
        if budget >= self._max_tokens:
            return self._max_tokens
        if budget < _MAX_TOKENS_FLOOR:
            # 提示词本身就快把窗口占满了：再压输出上限也救不回来（真正该做的是缩短输入），
            # 保底给下限并如实告警，让这次请求至少还能回出点东西。
            log_event(
                _LLM_COMPONENT, "llm.max_tokens.context_exhausted", level="WARN",
                prompt_tokens_est=prompt_tokens, context_tokens=self._context_tokens,
                configured_max_tokens=self._max_tokens, effective_max_tokens=_MAX_TOKENS_FLOOR,
            )
            return _MAX_TOKENS_FLOOR
        # 记 INFO 而不是 WARN：钳制是这个机制正常工作的表现，不是异常。出厂默认的输出上限
        # （131072）远大于任何本地端点的窗口，因此应用能力档案之后**每一次**调用都会走到这里；
        # 记 WARN 会把运行态诊断中心（只收 WARN/ERROR）长期占满，把真正的问题挤下去。
        # 紧邻的 context_exhausted 分支保持 WARN——提示词把窗口占满才是该报警的异常。
        log_event(
            _LLM_COMPONENT, "llm.max_tokens.clamped", level="INFO",
            prompt_tokens_est=prompt_tokens, context_tokens=self._context_tokens,
            configured_max_tokens=self._max_tokens, effective_max_tokens=budget,
            context_source=self._profile.context_source,
        )
        return budget

    def _base_payload(self, system: str, user: str) -> dict:
        payload: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": self._effective_max_tokens(system, user),
            "stream": False,
        }
        # 非标准扩展字段按能力档案下发，没探明则按 provider 类型的先验（见 chat_extension_fields）。
        payload.update(
            chat_extension_fields(self._provider_type, self._disable_thinking, self._profile)
        )
        return payload

    def _post_chat(self, payload: dict) -> str:
        try:
            resp = self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LlmError(str(exc)) from exc

    def chat(self, system: str, user: str) -> str:
        self._warn_if_prompt_too_long(system, user)
        return self._post_chat(self._base_payload(system, user))

    @staticmethod
    def _response_format(tier: str, lane: str, schema: dict) -> dict:
        if tier == "json_schema":
            return {"type": "json_schema",
                    "json_schema": {"name": lane, "schema": schema, "strict": True}}
        return {"type": "json_object"}

    def chat_structured(self, system: str, user: str, schema: dict, lane: str) -> str:
        """带 response_format 的 chat：按当前档位请求；端点以 4xx 拒绝该参数时降档重试并缓存。

        三档（json_schema / json_object / 纯提示词）均如实记结构化日志，不静默；
        5xx/网络错误不属于能力探测失败，原样抛 LlmError（不降档）。
        """
        self._warn_if_prompt_too_long(system, user)
        while self._structured_tier is not None:
            tier = self._structured_tier
            payload = self._base_payload(system, user)
            payload["response_format"] = self._response_format(tier, lane, schema)
            try:
                content = self._post_chat(payload)
            except LlmError as exc:
                status = getattr(getattr(getattr(exc, "__cause__", None), "response", None),
                                 "status_code", None)
                if status is not None and 400 <= status < 500 and status not in _NON_CAPABILITY_4XX:
                    # 端点不认 response_format（llama.cpp 旧版/网关差异）：降档并缓存，不静默。
                    # 429/401 等瞬时/鉴权类 4xx 不在此列——那不是能力探测失败，降档会整批误伤。
                    self._structured_tier = _STRUCTURED_TIER_FALLBACK.get(tier)
                    log_event(
                        _LLM_COMPONENT, "llm.structured.downgrade", level="WARN",
                        lane=lane, from_tier=tier,
                        to_tier=self._structured_tier or "prompt_only", status_code=status,
                    )
                    continue
                raise
            log_event(_LLM_COMPONENT, "llm.structured.request", lane=lane, tier=tier, ok=True)
            return content
        content = self._post_chat(self._base_payload(system, user))
        # 与分档分支同语义：成功后才记 ok=True，失败请求不得留下成功记录（日志=验证证据）。
        log_event(_LLM_COMPONENT, "llm.structured.request", lane=lane, tier="prompt_only", ok=True)
        return content


class LlmSourceIntakeJudge:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def judge(self, project_ref: str, raw_text: str, source_note: str) -> IntakeJudgement:
        system, user = render_pair(
            "source_intake",
            project_ref=project_ref,
            source_note=source_note or "（无）",
            raw_text=raw_text,
            output_schema=prompt_dumps(_INTAKE_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_object(content)
        except (LlmError, ValueError):
            # 不外泄 Prompt/原始响应/异常细节
            return IntakeJudgement(ModelJudgement.JUDGEMENT_FAILED, "模型不可用或结果不可解析")

        judged = _map_judgement(data.get("judgement"))
        if judged is None:
            return IntakeJudgement(ModelJudgement.JUDGEMENT_FAILED, "模型返回的判定类别不可识别")
        basis = str(data.get("basis") or "").strip()[:500]
        return IntakeJudgement(judged, basis)


class StubSourceIntakeJudge:
    """无模型/测试用：返回固定判定。"""

    def __init__(
        self,
        judgement: ModelJudgement = ModelJudgement.ACCEPTABLE,
        basis: str = "stub 判定（未接入真实模型）",
    ) -> None:
        self._judgement = judgement
        self._basis = basis

    def judge(self, project_ref: str, raw_text: str, source_note: str) -> IntakeJudgement:
        return IntakeJudgement(self._judgement, self._basis)


def build_source_intake_judge(settings: Settings) -> SourceIntakeJudge:
    """有 LLM_BASE_URL → 真实适配器；否则 stub。"""
    if settings.llm_base_url:
        return LlmSourceIntakeJudge(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubSourceIntakeJudge()


# ============================================================================
# SCN-001-P02 需求要素识别（分析转化服务送检 AEP-004）
# 隔离底线同接入判断：只返回结构化要素集+依据，不外泄/持久化 Prompt 与原始响应；
# 任何失败（不可用/超时/结果不可解析）→ failed=True（VAL-005 失败不污染事实）。
# ============================================================================


@dataclass(frozen=True)
class RecognizedElement:
    element_type: ElementType
    content: str
    source_anchor: Optional[str]
    confidence: Optional[float]
    verdict: Optional[ModelVerdict] = None  # 模型裁定（证据预标记，不是状态）
    # 该条被这样裁定的具体理由（一句话，指向本条内容）；模型漏给 → None，读侧回落通用判据
    verdict_reason: Optional[str] = None


@dataclass(frozen=True)
class RecognitionResult:
    elements: tuple[RecognizedElement, ...]
    basis: str
    failed: bool = False  # True → 识别失败停靠（不迁移状态、不写 LDM-004/005）


class SourceElementRecognizer(Protocol):
    """需求要素识别能力（适配器实现之；stub 供无模型/测试）。"""

    def recognize(self, project_ref: str, raw_text: str, source_note: str,
                  project_scope: str | None = None,
                  project_background: str | None = None,
                  domain_profile=None) -> RecognitionResult: ...


# 输出 JSON 形状与解析器同文件定义（模板经 output_schema 变量渲染，消灭提示词/解析器双写）
_RECOGNITION_OUTPUT_ITEM = {
    "element_type": "<类型码>", "content": "<要素内容>",
    "source_anchor": "<来源锚点引文>", "confidence": 0.0, "model_verdict": "<裁定码>",
    "verdict_reason": "<裁定不是 processable 时必填：一句话说明这一条为什么这样裁定>",
}

_ELEMENT_TYPE_MAP = {
    "functional_requirement": ElementType.FUNCTIONAL_REQUIREMENT, "功能需求": ElementType.FUNCTIONAL_REQUIREMENT,
    "quality_attribute": ElementType.QUALITY_ATTRIBUTE, "质量属性": ElementType.QUALITY_ATTRIBUTE,
    "constraint": ElementType.CONSTRAINT, "约束": ElementType.CONSTRAINT,
    "data_requirement": ElementType.DATA_REQUIREMENT, "数据需求": ElementType.DATA_REQUIREMENT,
    "interface_requirement": ElementType.INTERFACE_REQUIREMENT, "接口需求": ElementType.INTERFACE_REQUIREMENT,
    "goal": ElementType.GOAL, "目标": ElementType.GOAL,
    "scenario": ElementType.SCENARIO, "场景": ElementType.SCENARIO,
    "term": ElementType.TERM, "术语": ElementType.TERM,
    "assumption": ElementType.ASSUMPTION, "假设": ElementType.ASSUMPTION,
    "business_rule": ElementType.BUSINESS_RULE, "业务规则": ElementType.BUSINESS_RULE,
    "role": ElementType.ROLE, "角色": ElementType.ROLE,
    "external_system": ElementType.EXTERNAL_SYSTEM, "外部系统": ElementType.EXTERNAL_SYSTEM,
}

_VERDICT_MAP = {
    "processable": ModelVerdict.PROCESSABLE, "可处理": ModelVerdict.PROCESSABLE,
    "suspected_needs_supplement": ModelVerdict.SUSPECTED_NEEDS_SUPPLEMENT,
    "疑似需补充": ModelVerdict.SUSPECTED_NEEDS_SUPPLEMENT,
    "suspected_noise": ModelVerdict.SUSPECTED_NOISE, "建议剔除": ModelVerdict.SUSPECTED_NOISE,
    # 旧口径兼容（历史提示词/模型漂移）；「疑似误识别」是 2026-07-25 前的中文标签，
    # 提示词换口径后模型仍可能沿用，保留映射避免解析回落成 processable
    "疑似误识别": ModelVerdict.SUSPECTED_NOISE,
    "valid": ModelVerdict.PROCESSABLE, "pending": ModelVerdict.PROCESSABLE,
    "needs_supplement": ModelVerdict.SUSPECTED_NEEDS_SUPPLEMENT,
    "excluded": ModelVerdict.SUSPECTED_NOISE,
}


def _map_element_type(raw: object) -> Optional[ElementType]:
    key = str(raw or "").strip()
    return _ELEMENT_TYPE_MAP.get(key) or _ELEMENT_TYPE_MAP.get(key.lower())


def _map_verdict(raw: object) -> ModelVerdict:
    key = str(raw or "").strip()
    return _VERDICT_MAP.get(key) or _VERDICT_MAP.get(key.lower()) or ModelVerdict.PROCESSABLE


def _clean_verdict_reason(raw: object) -> Optional[str]:
    """裁定理由：空/空白 → None（模型漏给，读侧回落通用判据）；超长截断（同 source_anchor 口径）。"""
    text = str(raw or "").strip()
    return text[:500] if text else None


def _coerce_confidence(raw: object) -> Optional[float]:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _extract_json_array(text: str) -> list:
    """防御式解析：容忍 ```json 围栏与前后杂字，取第一个 [...]。"""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("响应中未找到 JSON 数组")
    return json.loads(text[start : end + 1])


class LlmSourceElementRecognizer:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def recognize(self, project_ref: str, raw_text: str, source_note: str,
                  project_scope: str | None = None,
                  project_background: str | None = None,
                  domain_profile=None) -> RecognitionResult:
        from app.domain.domain_profiles import render_domain_reference
        system, user = render_pair(
            "element_recognition",
            project_ref=project_ref,
            project_scope=project_scope or "",
            project_background=project_background or "",
            source_note=source_note or "（无）",
            raw_text=raw_text,
            output_schema=prompt_dumps(_RECOGNITION_OUTPUT_ITEM),
            **render_domain_reference(domain_profile),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_array(content)
        except (LlmError, ValueError):
            # 不外泄 Prompt/原始响应/异常细节
            return RecognitionResult(elements=(), basis="模型不可用或结果不可解析", failed=True)

        elements: list[RecognizedElement] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            etype = _map_element_type(item.get("element_type"))
            content_text = str(item.get("content") or "").strip()
            if etype is None or not content_text:
                continue  # 跳过不可识别类型/空内容项，不伪造要素
            elements.append(
                RecognizedElement(
                    element_type=etype,
                    content=content_text[:2000],
                    source_anchor=(str(item.get("source_anchor")).strip()[:500] if item.get("source_anchor") else None),
                    confidence=_coerce_confidence(item.get("confidence")),
                    verdict=_map_verdict(item.get("model_verdict") or item.get("process_status")),
                    verdict_reason=_clean_verdict_reason(item.get("verdict_reason")),
                )
            )
        # 理由缺失回落的观测口（卡面 constraints 末条要求的第二个日志点）：提示词要求模型对每条
        # 非 processable 的裁定给一句理由，模型的遵循率只能在这里量到。按批发一条、只记两个计数，
        # 不记要素内容与理由全文（卡面「只记有无不记全文」）。
        non_processable = [e for e in elements if e.verdict is not ModelVerdict.PROCESSABLE]
        if non_processable:
            log_event(
                _LLM_COMPONENT, "element.recognition.verdict_reason_coverage",
                lane="element_recognition",
                non_processable_count=len(non_processable),
                missing_reason_count=sum(1 for e in non_processable if not e.verdict_reason),
                ok=True,
            )
        return RecognitionResult(elements=tuple(elements), basis="需求要素识别完成", failed=False)


def _split_sentences(raw_text: str) -> list[str]:
    """按中文标点/换行切句，返回非空句子（均为原文逐字子串，可作 exact 引文）。"""
    out: list[str] = []
    buf = ""
    for ch in raw_text:
        if ch in "。！？；\n":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


_STUB_TYPE_CYCLE = (
    ElementType.FUNCTIONAL_REQUIREMENT,
    ElementType.QUALITY_ATTRIBUTE,
    ElementType.CONSTRAINT,
    ElementType.GOAL,
)


class StubSourceElementRecognizer:
    """无模型/测试用：按句切分原文派生要素（source_anchor=原文 exact 引文，锚点可解析）。

    elements 显式给定时原样返回；failed/空集可配置以覆盖分支。
    """

    def __init__(
        self,
        elements: Optional[tuple[RecognizedElement, ...]] = None,
        basis: str = "stub 识别（未接入真实模型）",
        failed: bool = False,
    ) -> None:
        self._elements = elements
        self._basis = basis
        self._failed = failed

    def recognize(self, project_ref: str, raw_text: str, source_note: str,
                  project_scope: str | None = None,
                  project_background: str | None = None,
                  domain_profile=None) -> RecognitionResult:
        if self._failed:
            return RecognitionResult(elements=(), basis=self._basis, failed=True)
        if self._elements is not None:
            return RecognitionResult(elements=self._elements, basis=self._basis, failed=False)
        sentences = _split_sentences(raw_text)[:4]
        derived = tuple(
            RecognizedElement(
                element_type=_STUB_TYPE_CYCLE[i % len(_STUB_TYPE_CYCLE)],
                content=s,
                source_anchor=s,  # exact 引文，由承接侧换算 offset
                confidence=(0.9, 0.45, 0.8, 0.6)[i % 4],
                verdict=ModelVerdict.SUSPECTED_NOISE if i == 1 else ModelVerdict.PROCESSABLE,
                # 第 2 句的理由留空，让无模型环境也覆盖「模型漏给理由 → 读侧回落通用判据」这条路径
                verdict_reason=None,
            )
            for i, s in enumerate(sentences)
        )
        return RecognitionResult(elements=derived, basis=self._basis, failed=False)


def build_source_element_recognizer(settings: Settings) -> SourceElementRecognizer:
    """有 LLM_BASE_URL → 真实适配器；否则 stub。"""
    if settings.llm_base_url:
        return LlmSourceElementRecognizer(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubSourceElementRecognizer()


# ============================================================================
# SCN-001-P03 需求要素 AI 复核（分析转化服务送检 AEP-005）
# 核要素：逐条给「可通过/须修订/不可通过」结论供人裁定；
# 扫原文补漏：对划选范围找漏识别项（产物为新「待确认」要素）。
# 复核只产生结论/补漏项，不改写要素集合；失败 → failed=True（VAL-005）。
# ============================================================================


@dataclass(frozen=True)
class FacetFinding:
    """完备性判据单面向判定（只指缺、必须引证；设计增补 §2）。"""

    facet: str
    status: str  # present / missing / ambiguous
    evidence: Optional[str]  # present/ambiguous 必填：原文/要素内容逐字片段
    note: Optional[str]


@dataclass(frozen=True)
class ReviewFinding:
    """核要素单条结论（裁定矩阵输入）。"""

    element_ref: str
    conclusion: ReviewConclusion
    opinion: str
    revised_content: Optional[str]  # 仅 须修订 时给修订稿
    facets: tuple[FacetFinding, ...] = ()  # 有判据类型才有；解析失败降级为空
    correctness: Optional[str] = None  # consistent_with_source / deviates / unverifiable
    completeness: Optional[str] = None  # complete / incomplete（服务端由 facets 推导）
    rubric_version: Optional[int] = None


@dataclass(frozen=True)
class ElementReviewOutcome:
    findings: tuple[ReviewFinding, ...]
    basis: str
    failed: bool = False


@dataclass(frozen=True)
class ScanFinding:
    """扫原文补漏单条漏识别项（登记为新「待确认」要素）。"""

    element_type: ElementType
    content: str
    source_quote: Optional[str]
    confidence: Optional[float]


@dataclass(frozen=True)
class ScanOutcome:
    items: tuple[ScanFinding, ...]
    basis: str
    failed: bool = False


class ElementReviewer(Protocol):
    """需求要素 AI 复核能力（核要素结论 + 扫原文补漏；不重写集合）。"""

    def review_elements(
        self, project_ref: str, raw_text: str, source_note: str,
        targets: list[dict], intent: str,
    ) -> ElementReviewOutcome: ...

    def scan_missing(
        self, project_ref: str, raw_text: str, source_note: str,
        elements: list[dict], quotes: list[str], intent: str,
    ) -> ScanOutcome: ...


# 输出 JSON 形状与解析器同文件定义（枚举取值由枚举/判据常量拼出，模板经 output_schema 变量渲染）
_REVIEW_OUTPUT_ITEM = {
    "element_ref": "<要素id>",
    "conclusion": "|".join(c.value for c in RC),
    "opinion": "<先回应复核意图的结论依据，≤50字>",
    "revised_content": "<修订稿，仅 needs_revision 时给出>",
    "correctness": "|".join(rubrics.CORRECTNESS_VALUES),
    "facet_findings": [{
        "facet": "<判据key>", "status": "|".join(rubrics.FACET_STATUSES),
        "evidence": "<来源片段>", "note": "<说明>",
    }],
}
_SCAN_OUTPUT_ITEM = {
    "element_type": "<类型码>", "content": "<要素内容>",
    "source_quote": "<原文引文>", "confidence": 0.8,
}

_CONCLUSION_MAP = {
    "pass": ReviewConclusion.PASS, "可通过": ReviewConclusion.PASS,
    "needs_revision": ReviewConclusion.NEEDS_REVISION, "须修订": ReviewConclusion.NEEDS_REVISION,
    "fail": ReviewConclusion.FAIL, "不可通过": ReviewConclusion.FAIL,
}


def _map_conclusion(raw: object) -> Optional[ReviewConclusion]:
    key = str(raw or "").strip()
    return _CONCLUSION_MAP.get(key) or _CONCLUSION_MAP.get(key.lower())


def _format_rubrics(targets: list[dict]) -> str:
    """按目标要素类型拼判据注入块；全部无判据时给通用提示（降级）。"""
    blocks: list[str] = []
    for etype in sorted({str(t.get("element_type") or "") for t in targets}):
        rubric = rubrics.get_rubric(etype)
        if rubric is None:
            continue
        lines = [f"【{etype} 完备性判据 v{rubric.rubric_version}】"]
        for f in rubric.facets:
            kind = "必备" if f.required else "增强"
            line = f"- {f.key}（{f.label}，{kind}）：{f.criteria}"
            if f.applicability:  # 适用性条件（N/A 通道）：不满足即判 not_applicable
                line += f"〔适用性：{f.applicability}〕"
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "（本次目标要素类型均无判据，按通用要求复核，不输出 facet_findings）"


def _parse_facets(item: dict, element_type: str) -> tuple[
    tuple[FacetFinding, ...], Optional[str], Optional[str], Optional[int]
]:
    """容错解析单条结论的完备性判定；任何不合规部分直接丢弃（降级，不失败）。

    只承接判据内的 facet key；present/ambiguous 无逐字证据不承接（只准指缺）。
    completeness 由服务端从 facets 推导，且要求必备面向全被判定，不采信模型自评。
    """
    rubric = rubrics.get_rubric(element_type)
    if rubric is None:
        return (), None, None, None
    facets: list[FacetFinding] = []
    status_map: dict[str, str] = {}
    raw_findings = item.get("facet_findings")
    if isinstance(raw_findings, list):
        for fr in raw_findings:
            if not isinstance(fr, dict):
                continue
            key = str(fr.get("facet") or "").strip()
            status = str(fr.get("status") or "").strip().lower()
            spec = rubric.facet(key)
            if spec is None or status not in rubrics.FACET_STATUSES or key in status_map:
                continue
            evidence_raw = fr.get("evidence")
            evidence = str(evidence_raw).strip()[:300] if evidence_raw else None
            if status in ("present", "ambiguous") and not evidence:
                continue
            note = str(fr.get("note") or "").strip()[:300] or None
            # N/A（判据驱动）：仅声明了适用性的成分可裁，且须给判定理由（note）；否则丢弃→成分行为零变。
            if status == "not_applicable" and (spec.applicability is None or not note):
                continue
            facets.append(FacetFinding(facet=key, status=status, evidence=evidence, note=note))
            status_map[key] = status
    if not facets:
        return (), None, None, None
    correctness = str(item.get("correctness") or "").strip().lower() or None
    if correctness not in rubrics.CORRECTNESS_VALUES:
        correctness = None
    required_keys = {f.key for f in rubric.facets if f.required}
    completeness = rubric.completeness_of(status_map) if required_keys <= set(status_map) else None
    return tuple(facets), correctness, completeness, rubric.rubric_version


class LlmElementReviewer:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def review_elements(
        self, project_ref: str, raw_text: str, source_note: str,
        targets: list[dict], intent: str,
    ) -> ElementReviewOutcome:
        system, user = render_pair(
            "element_review",
            project_ref=project_ref,
            source_note=source_note or "（无）",
            raw_text=raw_text,
            targets=json.dumps(targets, ensure_ascii=False),
            rubrics_text=_format_rubrics(targets),
            intent=intent or "复核要素是否有原文依据、表达是否清晰、类型是否正确",
            output_schema=prompt_dumps(_REVIEW_OUTPUT_ITEM),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_array(content)
        except (LlmError, ValueError):
            return ElementReviewOutcome(findings=(), basis="复核模型不可用或结果不可解析", failed=True)

        target_types = {str(t.get("id", "")): str(t.get("element_type") or "") for t in targets}
        findings: list[ReviewFinding] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("element_ref") or "").strip()
            conclusion = _map_conclusion(item.get("conclusion"))
            if ref not in target_types or conclusion is None:
                continue
            revised = item.get("revised_content")
            facets, correctness, completeness, rubric_version = _parse_facets(item, target_types[ref])
            findings.append(ReviewFinding(
                element_ref=ref,
                conclusion=conclusion,
                opinion=str(item.get("opinion") or "").strip()[:500],
                revised_content=(str(revised).strip()[:2000] or None) if revised else None,
                facets=facets,
                correctness=correctness,
                completeness=completeness,
                rubric_version=rubric_version,
            ))
        if not findings:
            return ElementReviewOutcome(findings=(), basis="复核结果不可承接（无有效结论）", failed=True)
        return ElementReviewOutcome(findings=tuple(findings), basis="AI 复核完成", failed=False)

    def scan_missing(
        self, project_ref: str, raw_text: str, source_note: str,
        elements: list[dict], quotes: list[str], intent: str,
    ) -> ScanOutcome:
        system, user = render_pair(
            "element_scan",
            project_ref=project_ref,
            source_note=source_note or "（无）",
            raw_text=raw_text,
            quotes=json.dumps(quotes, ensure_ascii=False),
            elements=json.dumps(elements, ensure_ascii=False),
            intent=intent or "在划选范围内找漏识别的需求要素",
            output_schema=prompt_dumps(_SCAN_OUTPUT_ITEM),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_array(content)
        except (LlmError, ValueError):
            return ScanOutcome(items=(), basis="补漏模型不可用或结果不可解析", failed=True)

        items: list[ScanFinding] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            etype = _map_element_type(item.get("element_type"))
            content_text = str(item.get("content") or "").strip()
            if etype is None or not content_text:
                continue
            quote = item.get("source_quote")
            items.append(ScanFinding(
                element_type=etype,
                content=content_text[:2000],
                source_quote=(str(quote).strip()[:500] or None) if quote else None,
                confidence=_coerce_confidence(item.get("confidence")),
            ))
        return ScanOutcome(items=tuple(items), basis="扫原文补漏完成", failed=False)


class StubElementReviewer:
    """无模型/测试用：确定性结论/补漏项（可注入固定结果或失败）。

    核要素启发式：内容含「通知」等含糊表述 → 须修订并给修订稿；
    模型裁定为建议剔除（verdict=suspected_noise）且人工未撤回 → 不可通过；其余 → 可通过。
    人工已把该条撤回到正常列表（noise_triage=restored）时不按建议剔除处理：人工裁定是对模型
    误杀的纠正，复核不该拿模型的原判把它再判死一次（冷审查裁定 C4）。
    """

    def __init__(
        self,
        findings: Optional[tuple[ReviewFinding, ...]] = None,
        scan_items: Optional[tuple[ScanFinding, ...]] = None,
        failed: bool = False,
    ) -> None:
        self._findings = findings
        self._scan_items = scan_items
        self._failed = failed

    def review_elements(
        self, project_ref: str, raw_text: str, source_note: str,
        targets: list[dict], intent: str,
    ) -> ElementReviewOutcome:
        if self._failed:
            return ElementReviewOutcome(findings=(), basis="复核模型不可用", failed=True)
        if self._findings is not None:
            return ElementReviewOutcome(findings=self._findings, basis="stub 复核", failed=False)
        out: list[ReviewFinding] = []
        for t in targets:
            ref = str(t.get("id", ""))
            content = str(t.get("content", ""))
            facets, correctness, completeness, rubric_version = self._stub_facets(
                str(t.get("element_type") or ""), content
            )
            restored = t.get("noise_triage") == NoiseTriage.RESTORED.value
            if t.get("model_verdict") == "suspected_noise" and not restored:
                out.append(ReviewFinding(ref, ReviewConclusion.FAIL, "不承载需求信息，无需求资产价值", None))
            elif "通知" in content and "短信" not in content:
                out.append(ReviewFinding(
                    ref, ReviewConclusion.NEEDS_REVISION,
                    "通知方式不明确，建议补充具体渠道",
                    f"{content}（须明确通知方式）",
                    facets=facets, correctness=correctness,
                    completeness=completeness, rubric_version=rubric_version,
                ))
            else:
                out.append(ReviewFinding(
                    ref, ReviewConclusion.PASS, "表达清晰、有原文依据", None,
                    facets=facets, correctness=correctness,
                    completeness=completeness, rubric_version=rubric_version,
                ))
        return ElementReviewOutcome(findings=tuple(out), basis="stub 复核完成", failed=False)

    @staticmethod
    def _stub_facets(element_type: str, content: str) -> tuple[
        tuple[FacetFinding, ...], Optional[str], Optional[str], Optional[int]
    ]:
        """确定性完备度判定：response_measure 看内容有无数字，其余面向视为存在。"""
        rubric = rubrics.get_rubric(element_type)
        if rubric is None:
            return (), None, None, None
        quote = content[:40] or None
        facets: list[FacetFinding] = []
        status_map: dict[str, str] = {}
        for f in rubric.facets:
            if f.key == "response_measure" and not any(c.isdigit() for c in content):
                facets.append(FacetFinding(f.key, "missing", None, "未见量化阈值"))
                status_map[f.key] = "missing"
            else:
                facets.append(FacetFinding(f.key, "present", quote, None))
                status_map[f.key] = "present"
        return (
            tuple(facets), "consistent_with_source",
            rubric.completeness_of(status_map), rubric.rubric_version,
        )

    def scan_missing(
        self, project_ref: str, raw_text: str, source_note: str,
        elements: list[dict], quotes: list[str], intent: str,
    ) -> ScanOutcome:
        if self._failed:
            return ScanOutcome(items=(), basis="补漏模型不可用", failed=True)
        if self._scan_items is not None:
            return ScanOutcome(items=self._scan_items, basis="stub 补漏", failed=False)
        anchored = {str(e.get("content", "")) for e in elements}
        out: list[ScanFinding] = []
        for q in quotes:
            for s in _split_sentences(q):
                if s and s not in anchored:
                    out.append(ScanFinding(ElementType.GOAL, s, s, 0.7))
        return ScanOutcome(items=tuple(out), basis="stub 补漏完成", failed=False)


def build_element_reviewer(settings: Settings) -> ElementReviewer:
    if settings.llm_base_url:
        return LlmElementReviewer(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubElementReviewer()


# ============================================================================
# SCN-001-P04 指定操作 AI 执行（分析转化服务送检 AEP-006）
# 只执行用户指定操作，返回结构化变更结果；不直接写仓储。
# ============================================================================


@dataclass(frozen=True)
class ExecutionResult:
    after_items: tuple[RecognizedElement, ...]  # 拟生成/替代的要素（结构化变更结果）
    basis: str
    failed: bool = False


class ElementOperationExecutor(Protocol):
    """AI 执行用户指定的校正/修订操作。"""

    def execute(
        self, project_ref: str, raw_text: str, operation_type: str,
        instruction: str, targets: list[dict], quotes: list[str],
        current_draft: str = "",
    ) -> ExecutionResult: ...


# 结论对象契约：status/reason/after_items（形状与 _extract_execution_payload 消费口径同源）
_EXECUTION_OUTPUT = {
    "status": "done|cannot_comply",
    "reason": "<仅 cannot_comply 时说明原因>",
    "after_items": [{
        "element_type": "<类型码>", "content": "<要素内容>",
        "source_quote": "<原文引文>", "confidence": 0.9,
    }],
}


class LlmElementOperationExecutor:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def execute(
        self, project_ref: str, raw_text: str, operation_type: str,
        instruction: str, targets: list[dict], quotes: list[str],
        current_draft: str = "",
    ) -> ExecutionResult:
        system, user = render_pair(
            "element_execution",
            project_ref=project_ref,
            raw_text=raw_text,
            operation_type=operation_type,
            instruction=instruction,
            targets=json.dumps(targets, ensure_ascii=False),
            current_draft=current_draft or "无",
            quotes=json.dumps(quotes, ensure_ascii=False),
            output_schema=prompt_dumps(_EXECUTION_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_execution_payload(content)
        except (LlmError, ValueError):
            return ExecutionResult(after_items=(), basis="执行模型不可用或结果不可解析", failed=True)

        # 结论对象契约：status=cannot_comply 时 reason 是给用户看的拒绝原因（随 basis 流入历史与草案停靠）
        status = str(data.get("status") or "done").strip()
        reason = str(data.get("reason") or "").strip()
        if status == "cannot_comply":
            return ExecutionResult(
                after_items=(),
                basis=reason or "AI 判断该指令无法在来源依据内完成（未给出原因）",
                failed=False,
            )

        items: list[RecognizedElement] = []
        for item in data.get("after_items", []):
            if not isinstance(item, dict):
                continue
            etype = _map_element_type(item.get("element_type"))
            content_text = str(item.get("content") or "").strip()
            if etype is None or not content_text:
                continue
            quote = item.get("source_quote") or item.get("source_anchor")
            items.append(RecognizedElement(
                element_type=etype,
                content=content_text[:2000],
                source_anchor=(str(quote).strip()[:500] if quote else None),
                confidence=_coerce_confidence(item.get("confidence")),
            ))
        if not items:
            return ExecutionResult(
                after_items=(), basis=reason or "AI 未产出可承接的变更结果", failed=False,
            )
        return ExecutionResult(after_items=tuple(items), basis="AI 执行完成", failed=False)


def _extract_execution_payload(text: str) -> dict:
    """执行结果解析：结论对象为准；兼容旧裸数组输出（按首个界符判别，包装为 done 对象）。"""
    array_at, object_at = text.find("["), text.find("{")
    if array_at != -1 and (object_at == -1 or array_at < object_at):
        return {"status": "done", "after_items": _extract_json_array(text)}
    return _extract_json_object(text)


class StubElementOperationExecutor:
    """无模型/测试用：按操作类型机械生成结构化变更结果。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def execute(
        self, project_ref: str, raw_text: str, operation_type: str,
        instruction: str, targets: list[dict], quotes: list[str],
        current_draft: str = "",
    ) -> ExecutionResult:
        if self._failed:
            return ExecutionResult(after_items=(), basis="执行模型不可用", failed=True)
        items: list[RecognizedElement] = []
        for t in targets:
            etype = _map_element_type(t.get("element_type")) or ElementType.FUNCTIONAL_REQUIREMENT
            content = str(t.get("content", ""))
            anchor = str(t.get("source_quote") or "") or None
            if operation_type == "revise_expression":
                items.append(RecognizedElement(etype, f"{content}（修订：{instruction}）"[:2000], anchor, 0.85))
            elif operation_type == "split":
                parts = [p for p in _split_sentences(content) if p] or [content]
                for p in parts[:3]:
                    items.append(RecognizedElement(etype, p, anchor, 0.8))
            else:
                items.append(RecognizedElement(etype, content, anchor, 0.8))
        if operation_type == "merge" and len(items) > 1:
            merged = "；".join(i.content for i in items)
            items = [RecognizedElement(items[0].element_type, merged[:2000], items[0].source_anchor, 0.8)]
        return ExecutionResult(after_items=tuple(items), basis="stub 执行完成", failed=False)


def build_element_operation_executor(settings: Settings) -> ElementOperationExecutor:
    if settings.llm_base_url:
        return LlmElementOperationExecutor(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubElementOperationExecutor()


# ============================================================================
# SCN-002-P01 条目格式化建议（条目形成服务送检 AEP-007）
# 只把单个需求要素的表达规范化为条目表达建议，不改变含义、不重新发现需求、
# 不改写要素集合；物理批量调用，输出逐要素归因（element_ref 必带）。
# 失败（不可用/超时/不可解析）→ failed=True；建议先落 LDM-015，不直写 LDM-007。
# ============================================================================


@dataclass(frozen=True)
class FormattedItem:
    """单个要素的条目格式化建议（逐要素归因）。

    结构判定字段为条目档案派生（设计增补《条目完备性档案与结构投影》§2）：
    req_type 由输入要素类型确定性映射（不采信模型）；completeness 由服务端从
    facets 推导；解析失败整体降级为空，不影响表达承接。
    """

    element_ref: str
    expression: str                    # 规范化条目表达（不得改变要素含义）
    suggestion: Optional[str] = None   # 可选替代表达（进入字段修订候选建议）
    suggestion_reason: str = ""
    req_type: Optional[str] = None
    statement_conformance: Optional[str] = None  # conforms / deviates / not_applicable
    facets: tuple[FacetFinding, ...] = ()        # 档案 facet 判定（只指缺、必须引证）
    completeness: Optional[str] = None           # complete / incomplete（服务端推导）
    profile_version: Optional[int] = None
    convention_key: Optional[str] = None         # 本批次固定的规约方案（口径锚，随投影/LDM-015 记录）
    payload_values: tuple[tuple[str, Optional[str]], ...] = ()  # (档案字段key, 陈述内取值)
    curation_note: Optional[str] = None  # 内容整理说明初稿（只准归纳来源要素）
    boundary_note: Optional[str] = None  # 条目边界说明初稿（同上）
    verification_note: Optional[str] = None  # 验收准则初稿（只准归纳来源可观察判据，不得编造阈值）
    verification_method: tuple[str, ...] = ()  # 验证方式建议（工程判断，允许多选组合）


@dataclass(frozen=True)
class ItemFormationSuggestion:
    items: tuple[FormattedItem, ...]
    basis: str
    failed: bool = False


class RequirementItemFormatter(Protocol):
    """条目格式化：把已确认需求表达类要素规范化为条目表达建议。"""

    def format_items(
        self, project_ref: str, raw_text: str, elements: list[dict],
        convention_key: str = item_profiles.DEFAULT_CONVENTION,
    ) -> ItemFormationSuggestion: ...


# 结论对象契约：status/reason/items（cannot_comply=显式拒绝通道，reason 经失败类 LDM-015
# basis 停靠给用户；单条无法规范化由模板约定省略、服务端逐要素裁定标记失败）。
# statement_conformance/facet_findings/payload_values 为条目档案结构判定（增补 §2）。
_ITEM_FORMATION_OUTPUT = {
    "status": "done|cannot_comply",
    "reason": "<仅 cannot_comply 时给用户的一句中文原因>",
    "items": [{
        "element_ref": "<输入要素id>", "expression": "<规范化条目表达>",
        "statement_conformance": "|".join(item_profiles.STATEMENT_CONFORMANCE_VALUES),
        "facet_findings": [{
            "facet": "<档案facet key>", "status": "|".join(item_profiles.FACET_STATUSES),
            "evidence": "<来源要素或原文逐字片段>", "note": "<一句说明>",
        }],
        "payload_values": [{"field": "<档案字段key>", "value": "<陈述中已有内容，缺失为null>"}],
        "curation_note": "<内容整理说明初稿≤80字，无法归纳为null>",
        "boundary_note": "<条目边界说明初稿≤80字，无法归纳为null>",
        "verification_note": "<验收准则初稿≤120字，只准归纳来源可观察判据，无法归纳为null>",
        "verification_method": ["|".join(m.value for m in VerificationMethod)],
        "suggestion": "<可选替代表达，可省略>", "suggestion_reason": "<替代理由≤50字，可省略>",
    }],
}


def _format_item_profiles(convention_key: str = item_profiles.DEFAULT_CONVENTION) -> str:
    """指定规约方案下全部条目档案的注入块 + 方案无关公共写作约束（common.yaml）。

    方案在批次发起时固定并随批次传入；同一批次内 profiles_text 稳定（前缀缓存友好）。
    """
    profiles = item_profiles.profiles_of(convention_key)
    display = item_profiles.convention_display_name(convention_key)
    blocks: list[str] = [
        f"【当前生效规约方案】{display}（{convention_key}）——本批次全部条目按此方案档案格式化。",
        item_profiles.common_constraints_text(),
    ]
    for req_type in sorted(profiles):
        p = profiles[req_type]
        lines = [f"【{req_type} 条目陈述档案 v{p.profile_version}】", f"句式：{p.statement_pattern}"]
        for f in p.facets:
            kind = "必备" if f.required else "增强"
            line = f"- {f.key}（{f.label}，{kind}）：{f.criteria}"
            if f.applicability:  # 适用性条件（N/A 通道）：不满足即判 not_applicable
                line += f"〔适用性：{f.applicability}〕"
            lines.append(line)
        if p.payload_fields:
            lines.append("结构字段：" + "、".join(f"{fl.key}（{fl.label}）" for fl in p.payload_fields))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _parse_item_structure(
    item: dict, req_type: str, convention_key: str = item_profiles.DEFAULT_CONVENTION,
) -> tuple[
    Optional[str], tuple[FacetFinding, ...], Optional[str], Optional[int],
    tuple[tuple[str, Optional[str]], ...],
]:
    """容错解析单条建议的档案结构判定；任何不合规部分直接丢弃（降级，不失败）。

    按本批次固定方案取档案（convention_key）。只承接档案内的 facet/字段 key；
    present/ambiguous 无逐字证据不承接（只准指缺）。completeness 由服务端从 facets 推导，
    且要求必备面向全被判定，不采信模型自评。
    """
    profile = item_profiles.get_profile(req_type or "", convention_key)
    if profile is None:
        return None, (), None, None, ()
    facets: list[FacetFinding] = []
    status_map: dict[str, str] = {}
    raw_findings = item.get("facet_findings")
    if isinstance(raw_findings, list):
        for fr in raw_findings:
            if not isinstance(fr, dict):
                continue
            key = str(fr.get("facet") or "").strip()
            status = str(fr.get("status") or "").strip().lower()
            spec = profile.facet(key)
            if spec is None or status not in item_profiles.FACET_STATUSES or key in status_map:
                continue
            evidence_raw = fr.get("evidence")
            evidence = str(evidence_raw).strip()[:300] if evidence_raw else None
            if status in ("present", "ambiguous") and not evidence:
                continue
            note = str(fr.get("note") or "").strip()[:300] or None
            # N/A（判据驱动）：仅声明了适用性的成分可裁，且须给判定理由（note）；否则丢弃→成分行为零变。
            if status == "not_applicable" and (spec.applicability is None or not note):
                continue
            facets.append(FacetFinding(facet=key, status=status, evidence=evidence, note=note))
            status_map[key] = status
    if not facets:
        return None, (), None, None, ()
    conformance = str(item.get("statement_conformance") or "").strip().lower() or None
    if conformance not in item_profiles.STATEMENT_CONFORMANCE_VALUES:
        conformance = None
    required_keys = {f.key for f in profile.facets if f.required}
    completeness = profile.completeness_of(status_map) if required_keys <= set(status_map) else None
    payload: list[tuple[str, Optional[str]]] = []
    seen_fields: set[str] = set()
    raw_payload = item.get("payload_values")
    if isinstance(raw_payload, list):
        field_keys = {f.key for f in profile.payload_fields}
        for pv in raw_payload:
            if not isinstance(pv, dict):
                continue
            key = str(pv.get("field") or "").strip()
            if key not in field_keys or key in seen_fields:
                continue
            seen_fields.add(key)
            value_raw = pv.get("value")
            value = str(value_raw).strip()[:300] if value_raw else None
            payload.append((key, value or None))
    return conformance, tuple(facets), completeness, profile.profile_version, tuple(payload)


_VERIFICATION_METHOD_CODES = {m.value for m in VerificationMethod}


def _parse_verification_methods(raw) -> tuple[str, ...]:
    """验证方式建议解析：仅收编枚举内取值（去重保序），其余静默丢弃（建议初稿，降级不失败）。"""
    parts = raw if isinstance(raw, list) else str(raw or "").split(",")
    codes: list[str] = []
    for part in parts:
        code = str(part or "").strip().lower()
        if code in _VERIFICATION_METHOD_CODES and code not in codes:
            codes.append(code)
    return tuple(codes)


def _extract_formation_payload(text: str) -> dict:
    """格式化结果解析：结论对象为准；兼容旧裸数组输出（按首个界符判别，包装为 done 对象）。"""
    array_at, object_at = text.find("["), text.find("{")
    if array_at != -1 and (object_at == -1 or array_at < object_at):
        return {"status": "done", "items": _extract_json_array(text)}
    return _extract_json_object(text)


class LlmRequirementItemFormatter:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def format_items(
        self, project_ref: str, raw_text: str, elements: list[dict],
        convention_key: str = item_profiles.DEFAULT_CONVENTION,
    ) -> ItemFormationSuggestion:
        system, user = render_pair(
            "item_formation",
            project_ref=project_ref,
            raw_text=raw_text,
            elements=json.dumps(elements, ensure_ascii=False),
            profiles_text=_format_item_profiles(convention_key),
            output_schema=prompt_dumps(_ITEM_FORMATION_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_formation_payload(content)
        except (LlmError, ValueError):
            return ItemFormationSuggestion(items=(), basis="格式化模型不可用或结果不可解析", failed=True)

        if str(data.get("status") or "done").strip() == "cannot_comply":
            reason = str(data.get("reason") or "").strip()[:500]
            return ItemFormationSuggestion(
                items=(), basis=reason or "AI 判断本批要素无法完成条目格式化（未给出原因）",
                failed=True,
            )

        known_refs = {str(e.get("id")) for e in elements}
        req_type_of = {str(e.get("id")): str(e.get("req_type") or "") for e in elements}
        items: list[FormattedItem] = []
        for item in data.get("items", []):
            if not isinstance(item, dict):
                continue
            ref = str(item.get("element_ref") or "").strip()
            expression = str(item.get("expression") or "").strip()
            if ref not in known_refs or not expression:
                continue  # 无归因或空表达的输出不可承接
            suggestion = str(item.get("suggestion") or "").strip() or None
            req_type = req_type_of.get(ref) or None
            conformance, facets, completeness, profile_version, payload = (
                _parse_item_structure(item, req_type or "", convention_key)
            )
            items.append(FormattedItem(
                element_ref=ref,
                expression=expression[:2000],
                suggestion=suggestion[:2000] if suggestion else None,
                suggestion_reason=str(item.get("suggestion_reason") or "").strip()[:500],
                req_type=req_type,
                statement_conformance=conformance,
                facets=facets,
                completeness=completeness,
                profile_version=profile_version,
                convention_key=convention_key if profile_version is not None else None,
                payload_values=payload,
                curation_note=str(item.get("curation_note") or "").strip()[:500] or None,
                boundary_note=str(item.get("boundary_note") or "").strip()[:500] or None,
                verification_note=str(item.get("verification_note") or "").strip()[:500] or None,
                verification_method=_parse_verification_methods(item.get("verification_method")),
            ))
        if not items:
            # 空结果不静默：给可展示原因（服务端 formation_failed 分支以 basis 停靠）
            return ItemFormationSuggestion(
                items=(), basis="模型未产出可承接的格式化建议（缺归因或空表达）", failed=True,
            )
        return ItemFormationSuggestion(items=tuple(items), basis="条目格式化完成", failed=False)


def _stub_normalize_expression(content: str) -> str:
    normalized = " ".join(content.split())
    if not normalized:
        return ""
    if normalized.startswith("系统应"):
        return normalized
    return "系统应" + normalized.removeprefix("应").removeprefix("系统")


# stub 验证方式建议：按类型确定性映射（quality 走双方法，演示多选组合）
_STUB_METHODS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "functional": ("test",),
    "quality": ("analysis", "test"),
    "constraint": ("inspection",),
    "data": ("analysis",),
    "interface": ("demonstration",),
}


class StubRequirementItemFormatter:
    """无模型/测试用：机械规范化为「系统应…」表述，并给一条替代建议。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def format_items(
        self, project_ref: str, raw_text: str, elements: list[dict],
        convention_key: str = item_profiles.DEFAULT_CONVENTION,
    ) -> ItemFormationSuggestion:
        if self._failed:
            return ItemFormationSuggestion(items=(), basis="格式化模型不可用", failed=True)
        items: list[FormattedItem] = []
        for e in elements:
            expression = _stub_normalize_expression(str(e.get("content", "")))
            if not expression:
                continue
            req_type = str(e.get("req_type") or "") or None
            profile = item_profiles.get_profile(req_type or "", convention_key)
            facets: tuple[FacetFinding, ...] = ()
            completeness = None
            if profile is not None:
                # 确定性混合判定（首个必备面向 present，其余 missing）：供测试与无模型演示走通徽章链路
                required = [f for f in profile.facets if f.required]
                facets = tuple(
                    FacetFinding(
                        facet=f.key,
                        status="present" if i == 0 else "missing",
                        evidence=expression[:30] if i == 0 else None,
                        note=None if i == 0 else "stub：陈述未见该面向",
                    )
                    for i, f in enumerate(required)
                )
                completeness = profile.completeness_of({f.facet: f.status for f in facets})
            items.append(FormattedItem(
                element_ref=str(e.get("id")),
                expression=expression[:2000],
                suggestion=f"{expression}，并保留可追溯的来源依据。"[:2000],
                suggestion_reason="收敛表达并强调来源可追溯（stub 建议）",
                req_type=req_type,
                statement_conformance="conforms" if profile is not None else None,
                facets=facets,
                completeness=completeness,
                profile_version=profile.profile_version if profile is not None else None,
                convention_key=convention_key if profile is not None else None,
                curation_note="补主语并统一为「系统应…」式陈述（stub 整理说明）",
                boundary_note="仅覆盖来源要素表述的范围，未提及的场景不含（stub 边界说明）",
                # data 类不产出验收准则：模拟"来源无可归纳验证线索→为 null"通道（缺失警示演示）
                verification_note=(
                    None if req_type == "data"
                    else f"依据来源要素可观察验证：{expression[:60]}"
                ),
                verification_method=_STUB_METHODS_BY_TYPE.get(req_type or "", ("test",)),
            ))
        return ItemFormationSuggestion(items=tuple(items), basis="stub 条目格式化完成", failed=False)


def build_requirement_item_formatter(settings: Settings) -> RequirementItemFormatter:
    if settings.llm_base_url:
        return LlmRequirementItemFormatter(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubRequirementItemFormatter()


# ============================================================================
# 条目结构复核（AEP-114 / item_structure_recheck lane）——只判不改：
# 结果只刷新陈述达标投影（锚定当前内容修订序号），不产 expression、不触 LDM-007。
# ============================================================================


# 结论对象契约：判定形状与格式化 lane 的档案结构判定段同源（枚举取值单一来源 item_profiles）。
_ITEM_RECHECK_OUTPUT = {
    "status": "done|cannot_comply",
    "reason": "<仅 cannot_comply 时给用户的一句中文原因>",
    "statement_conformance": "|".join(item_profiles.STATEMENT_CONFORMANCE_VALUES),
    "facet_findings": [{
        "facet": "<档案facet key>", "status": "|".join(item_profiles.FACET_STATUSES),
        "evidence": "<条目表达/来源要素/原文逐字片段>", "note": "<一句说明>",
    }],
    "payload_values": [{"field": "<档案字段key>", "value": "<陈述中已有内容，缺失为null>"}],
}


@dataclass(frozen=True)
class StructureRecheckOutcome:
    """结构复核结论：仅档案结构判定，无表达产物（completeness 服务端口径同格式化 lane）。"""

    statement_conformance: Optional[str] = None
    facets: tuple[FacetFinding, ...] = ()
    completeness: Optional[str] = None
    profile_version: Optional[int] = None
    payload_values: tuple[tuple[str, Optional[str]], ...] = ()
    basis: str = ""
    failed: bool = False


class ItemStructureRechecker(Protocol):
    """条目结构复核：按条目当前表达重做陈述档案体检（只判不改）。"""

    def recheck(
        self, project_ref: str, raw_text: str, item: dict, sources: list[dict],
        convention_key: str = item_profiles.DEFAULT_CONVENTION,
    ) -> StructureRecheckOutcome: ...


class LlmItemStructureRechecker:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def recheck(
        self, project_ref: str, raw_text: str, item: dict, sources: list[dict],
        convention_key: str = item_profiles.DEFAULT_CONVENTION,
    ) -> StructureRecheckOutcome:
        system, user = render_pair(
            "item_structure_recheck",
            project_ref=project_ref,
            raw_text=raw_text,
            item=json.dumps(item, ensure_ascii=False),
            sources=json.dumps(sources, ensure_ascii=False),
            profiles_text=_format_item_profiles(convention_key),
            output_schema=prompt_dumps(_ITEM_RECHECK_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_object(content)
        except (LlmError, ValueError):
            return StructureRecheckOutcome(basis="复核模型不可用或结果不可解析", failed=True)
        if str(data.get("status") or "done").strip() == "cannot_comply":
            reason = str(data.get("reason") or "").strip()[:500]
            return StructureRecheckOutcome(
                basis=reason or "AI 判断该条目无法完成结构复核（未给出原因）", failed=True,
            )
        conformance, facets, completeness, profile_version, payload = _parse_item_structure(
            data, str(item.get("req_type") or ""), convention_key
        )
        if profile_version is None or not facets:
            # 无可承接判定 → 失败停靠（服务端保留旧投影原样，A4）
            return StructureRecheckOutcome(
                basis="模型未产出可承接的结构判定（缺 facet 判定或档案不适用）", failed=True,
            )
        if completeness is None:
            # 输出强制（issue #8 缺陷 5，服务端校验路线）：必备面向未全被判定则
            # completeness 推导不出——部分覆盖不承接为「已重判」，失败停靠保留旧判。
            # 「判定不造假」红线：不为收敛伪造完备性。
            return StructureRecheckOutcome(
                basis="必备面向未全被判定，完备性无法推导；结果不承接（旧体检保留原样）",
                failed=True,
            )
        return StructureRecheckOutcome(
            statement_conformance=conformance,
            facets=facets,
            completeness=completeness,
            profile_version=profile_version,
            payload_values=payload,
            basis="结构复核完成",
        )


class StubItemStructureRechecker:
    """无模型/测试用：确定性判定——missing_facets 点名的必备面向判缺，其余判 present。"""

    def __init__(self, failed: bool = False, missing_facets: tuple[str, ...] = ()) -> None:
        self._failed = failed
        self._missing = set(missing_facets)
        self.calls: list[str] = []  # 被调条目 item_ref 留痕（现行判定零调用断言用）

    def recheck(
        self, project_ref: str, raw_text: str, item: dict, sources: list[dict],
        convention_key: str = item_profiles.DEFAULT_CONVENTION,
    ) -> StructureRecheckOutcome:
        self.calls.append(str(item.get("item_ref") or ""))
        if self._failed:
            return StructureRecheckOutcome(basis="复核模型不可用", failed=True)
        profile = item_profiles.get_profile(str(item.get("req_type") or ""), convention_key)
        if profile is None:
            return StructureRecheckOutcome(basis="档案不适用", failed=True)
        expression = str(item.get("expression") or "")
        facets = tuple(
            FacetFinding(
                facet=f.key,
                status="missing" if f.key in self._missing else "present",
                evidence=None if f.key in self._missing else expression[:30],
                note="stub：陈述未见该面向" if f.key in self._missing else None,
            )
            for f in profile.facets if f.required
        )
        return StructureRecheckOutcome(
            statement_conformance="conforms",
            facets=facets,
            completeness=profile.completeness_of({f.facet: f.status for f in facets}),
            profile_version=profile.profile_version,
            basis="stub 结构复核完成",
        )


def build_item_structure_rechecker(settings: Settings) -> ItemStructureRechecker:
    if settings.llm_base_url:
        return LlmItemStructureRechecker(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubItemStructureRechecker()


# ============================================================================
# 需求条目诊断（SCN-003-P01；诊断结论=模型结果记录，正式事实由条目评审服务裁定写入）
# ============================================================================


# 取值单一来源：领域枚举（_sanitize_verdict 校验与 _DIAGNOSIS_OUTPUT 输出形状共用）
_FINDING_TYPES = frozenset(t.value for t in ReviewFindingType)
_VERDICT_KINDS = frozenset(k.value for k in VerdictKind)
# 质量诊断器（v2 签名件）取值单一来源；质量元数据校验降级不拒收，与上面聚合守卫互不影响
_QUALITY_RULES = frozenset(r.value for r in RequirementQualityRule)
_QUALITY_DIMS = frozenset(d.value for d in QualityDimension)
_QUALITY_SEVERITIES = frozenset(s.value for s in QualitySeverity)
_EARS_PATTERNS = frozenset(p.value for p in EarsPattern)


def _anchor_once(base: str, needle: str) -> bool:
    """片段在基准表达中恰好出现一次（与 revision_points 唯一定位同口径，供 evidence_span 复用）。"""
    if not needle:
        return False
    first = base.find(needle)
    return first >= 0 and base.find(needle, first + 1) < 0


def _clamp_int(value: object, lo: int, hi: int) -> Optional[int]:
    try:
        return max(lo, min(hi, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _clamp_float(value: object, lo: float, hi: float) -> Optional[float]:
    try:
        return max(lo, min(hi, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_quality_profile(raw: object) -> Optional[dict]:
    """质量画像（总分 + 各维度分）尽力解析；结构不合规 → None（降级不拒收）。"""
    if not isinstance(raw, dict):
        return None
    overall = _clamp_int(raw.get("overall"), 0, 100)
    dims: list[dict] = []
    for d in raw.get("dimensions") or []:
        if not isinstance(d, dict):
            continue
        key = str(d.get("key") or "").strip()
        score = _clamp_int(d.get("score"), 0, 100)
        if key not in _QUALITY_DIMS or score is None:
            continue
        dims.append({"key": key, "score": score, "note": str(d.get("note") or "").strip()[:200]})
    if overall is None and not dims:
        return None
    return {"overall": overall, "dimensions": dims}


def _parse_ears_rewrite(raw: object) -> Optional[dict]:
    """EARS 改写脚手架尽力解析；结构不合规 → None（降级不拒收）。"""
    if not isinstance(raw, dict):
        return None
    pattern = str(raw.get("pattern_type") or "").strip()
    if pattern not in _EARS_PATTERNS:
        pattern = None
    lines = [str(x).strip()[:500] for x in (raw.get("lines") or []) if str(x).strip()][:8]
    if not lines:
        return None
    return {"pattern_type": pattern, "lines": lines, "note": str(raw.get("note") or "").strip()[:300]}


def _parse_source_alignments(raw: object) -> Optional[list]:
    """逐源对齐分（LLM 判分，clamp[0,1]）；空 element_ref 丢弃。drift/drift_tokens 在读投影处派生。"""
    if not isinstance(raw, list):
        return None
    out: list[dict] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        element_ref = str(a.get("element_ref") or "").strip()
        if not element_ref:
            continue
        out.append({
            "element_ref": element_ref,
            "alignment": _clamp_float(a.get("alignment"), 0.0, 1.0),
            "note": str(a.get("note") or "").strip()[:200],
        })
    return out or None


@dataclass(frozen=True)
class DiagnosedFinding:
    """诊断发现项（v5：结论的只读证据行）。

    v2 质量诊断器 additive 增旁路字段（rule_code/evidence_span/severity/dimension，默认值保证
    既有构造点无需改动）；这些字段解析失败时降级为默认/None，不影响 finding 本体与聚合守卫。
    """

    finding_type: str
    diagnosis_summary: str
    basis_summary: str
    rule_code: Optional[str] = None       # RequirementQualityRule（无则 None）
    evidence_span: Optional[str] = None   # 基准表达中恰好出现一次的逐字片段，供高亮（定位失败则 None）
    severity: str = "medium"              # QualitySeverity
    dimension: Optional[str] = None       # QualityDimension


def serialize_diagnosed_finding(f: "DiagnosedFinding") -> dict:
    """把一条诊断发现项序列化成 stage payload 里的 finding dict（含质量旁路四字段）。

    单一来源：常规诊断（model_orchestration）与对话重评改判（item_review）两条写入路径共用此
    函数。否决的跨轮匹配靠 rule_code/evidence_span 建指纹，任一路径漏写这两个字段会让该轮
    quality_meta 为空、否决永不命中（GitHub issue #55 第 2 条）——集中一处序列化以杜绝漏写。
    """
    return {
        "finding_type": f.finding_type,
        "diagnosis_summary": f.diagnosis_summary,
        "basis_summary": f.basis_summary,
        "rule_code": f.rule_code,
        "evidence_span": f.evidence_span,
        "severity": f.severity,
        "dimension": f.dimension,
    }


@dataclass(frozen=True)
class ItemVerdictOutcome:
    """条目级结论（v5：结论=判断，仅诊断轮次铸造）。

    revision_points/supplement_gaps 为 dict/str 列表（与 LDM-009 JSON 结构一致）；
    聚合一致性与修订点可合成性先在适配器校验，服务端承接时再守卫。
    quality_profile/ears_rewrite/source_alignments 为 v2 质量诊断器旁路元数据（默认 None → 完全兼容）。
    """

    verdict_kind: str
    verdict_summary: str
    findings: tuple[DiagnosedFinding, ...]
    revision_points: tuple[dict, ...]
    supplement_gaps: tuple[str, ...]
    basis: str
    failed: bool = False
    quality_profile: Optional[dict] = None    # {overall, dimensions:[{key,score,note}]}
    ears_rewrite: Optional[dict] = None       # {pattern_type, lines:[...], note}
    source_alignments: Optional[list] = None  # [{element_ref, alignment:0.0-1.0, note}]（LLM 逐源判分）
    # 失败分关（诊断可靠性设计裁定 4）：parse|llm_error|structure|aggregation|synthesis；成功=None。
    failure_stage: Optional[str] = None


def _failed_verdict(reason: str, stage: str) -> ItemVerdictOutcome:
    return ItemVerdictOutcome(
        verdict_kind="", verdict_summary="", findings=(),
        revision_points=(), supplement_gaps=(), basis=reason, failed=True,
        failure_stage=stage,
    )


def _sanitize_verdict(data: dict, base_expression: str) -> Optional[ItemVerdictOutcome]:
    """三段校验（复用入口，供 item_reeval 等嵌套结论使用）；失败返回 None（整轮不承接）。"""
    outcome, _stage, _detail = _sanitize_verdict_staged(data, base_expression)
    return outcome


def _sanitize_verdict_staged(
    data: dict, base_expression: str
) -> tuple[Optional[ItemVerdictOutcome], Optional[str], Optional[str]]:
    """三段校验：结构 → 聚合守卫 → 修订点可合成性。

    返回 (结论, 失败关, 白话原因)：成功=(outcome, None, None)；失败=(None, stage, detail)。
    detail 是确定性代码产出的白话说明（可点名修订点序号），绝不夹带模型原文（硬规 8）。
    """
    def rejected(stage: str, detail: str) -> tuple[None, str, str]:
        return None, stage, detail

    if not isinstance(data, dict):
        return rejected("structure", "回复不是 JSON 对象")
    kind = str(data.get("verdict_kind") or "").strip()
    summary = str(data.get("verdict_summary") or "").strip()
    if kind not in _VERDICT_KINDS:
        return rejected("structure", "结论状态字缺失或不在允许取值内")
    if not summary:
        return rejected("structure", "缺少结论总结")

    findings: list[DiagnosedFinding] = []
    for entry in data.get("findings") or []:
        if not isinstance(entry, dict):
            continue
        ftype = str(entry.get("finding_type") or "").strip()
        fsummary = str(entry.get("diagnosis_summary") or "").strip()
        if ftype not in _FINDING_TYPES or not fsummary:
            continue
        # v2 质量诊断器 additive 旁路字段（降级不拒收：非法枚举丢字段、span 定位失败丢高亮）
        rule_code = str(entry.get("rule_code") or "").strip() or None
        if rule_code is not None and rule_code not in _QUALITY_RULES:
            rule_code = None
        severity = str(entry.get("severity") or "").strip()
        if severity not in _QUALITY_SEVERITIES:
            severity = QualitySeverity.MEDIUM.value
        dimension = str(entry.get("dimension") or "").strip() or None
        if dimension is not None and dimension not in _QUALITY_DIMS:
            dimension = None
        span = str(entry.get("evidence_span") or "").strip() or None
        if span is not None and not _anchor_once(base_expression, span):
            span = None  # 定位失败降级为无高亮，保留 finding 本体
        findings.append(DiagnosedFinding(
            finding_type=ftype, diagnosis_summary=fsummary[:1000],
            basis_summary=str(entry.get("basis_summary") or "").strip()[:1000],
            rule_code=rule_code, evidence_span=span, severity=severity, dimension=dimension,
        ))
    if not findings:
        return rejected("structure", "缺少有效的证据发现项")
    if len(findings) > 6:
        return rejected("structure", f"证据发现项超过上限（{len(findings)} 条，上限 6 条）")

    points: list[dict] = []
    for i, entry in enumerate(data.get("revision_points") or []):
        if not isinstance(entry, dict):
            return rejected("structure", f"修订点{i + 1} 不是结构化对象")
        try:
            finding_index = int(entry.get("finding_index"))
        except (TypeError, ValueError):
            return rejected("structure", f"修订点{i + 1} 缺少绑定的发现项序号")
        if not (0 <= finding_index < len(findings)):
            return rejected("structure", f"修订点{i + 1} 绑定的发现项序号超出范围")
        points.append({
            "point_ref": f"P{i + 1}",
            "label": str(entry.get("label") or "").strip()[:100] or f"修订点{i + 1}",
            "finding_index": finding_index,
            "find": str(entry.get("find") or ""),
            "replace": str(entry.get("replace") or ""),
            "basis": str(entry.get("basis") or "").strip()[:500],
            "group": (str(entry.get("group")).strip() or None) if entry.get("group") else None,
        })
    gaps = [str(g).strip()[:500] for g in (data.get("supplement_gaps") or []) if str(g).strip()]

    # 聚合守卫（确定性规则；服务端承接时按同一规则复核）
    if kind == "revise" and not points:
        return rejected("aggregation", "结论为「建议修订」但没有给出任何修订点")
    if kind != "revise" and points:
        return rejected("aggregation", "结论不是「建议修订」却携带了修订点")
    if kind == "pass" and any(f.finding_type != "no_blocker" for f in findings):
        return rejected("aggregation", "结论为「建议通过」但证据中仍有阻断性发现项")
    if kind == "supplement" and not gaps:
        return rejected("aggregation", "结论为「建议补充来源」但没有说明缺口")
    if kind != "supplement" and gaps:
        return rejected("aggregation", "结论不是「建议补充来源」却携带了缺口")

    if points:
        synth_error = validate_points(base_expression, points)
        if synth_error is not None:
            # validate_points 的错误说明只含修订点序号与定位结论，不含模型原文
            return rejected("synthesis", synth_error)

    # 质量元数据（v2 签名件）：三段校验通过后尽力解析，任一失败降级为 None，不拒收整轮
    return ItemVerdictOutcome(
        verdict_kind=kind, verdict_summary=summary[:1000],
        findings=tuple(findings), revision_points=tuple(points),
        supplement_gaps=tuple(gaps), basis="条目诊断完成", failed=False,
        quality_profile=_parse_quality_profile(data.get("quality_profile")),
        ears_rewrite=_parse_ears_rewrite(data.get("ears_rewrite")),
        source_alignments=_parse_source_alignments(data.get("source_alignments")),
    ), None, None


class RequirementItemDiagnoser(Protocol):
    """条目诊断：对单个待确认 LDM-007 产出一个结论对象（状态字+证据+修订点）。

    attestation=人工确认背书（可空）：条目的来源缺口已由人工确认闭合时给出理由原文/操作者/时间，
    无背书恒为 None——模板据此整段不渲染，无背书条目的提示词逐字节不变。
    """

    def diagnose(
        self, project_ref: str, diagnosis_mode: str, item: dict,
        sources: list[dict], raw_text: str, revisions: list[dict],
        prior_findings: list[dict], excluded_points: Optional[list[dict]] = None,
        thread_context: str = "", business_sources: Optional[list[dict]] = None,
        attestation: Optional[dict] = None,
    ) -> ItemVerdictOutcome: ...


# 结论对象契约（v5）：状态字+证据发现项+修订点/缺口（形状与 _sanitize_verdict 消费口径同源；
# item_reeval 的 supersede 替代结论嵌套复用同一形状）
_DIAGNOSIS_OUTPUT = {
    "verdict_kind": "|".join(k.value for k in VerdictKind),
    "verdict_summary": "<一句话说清为什么给这个结论，≤50字>",
    "findings": [{
        "finding_type": "|".join(t.value for t in ReviewFindingType),
        "diagnosis_summary": "<一句话说清问题或说明无阻断>",
        "basis_summary": "<依据摘要：引用来源片段或说明判断依据>",
        # 以下为质量诊断器旁路字段（可省略；rule_code 细化 finding_type）
        "rule_code": "|".join(r.value for r in RequirementQualityRule) + "|（无则省略）",
        "evidence_span": "<基准表达中恰好出现一次的逐字片段，供高亮；无则省略>",
        "severity": "|".join(s.value for s in QualitySeverity),
        "dimension": "|".join(d.value for d in QualityDimension),
    }],
    "revision_points": [{
        "label": "<修订点短标签>", "finding_index": 0,
        "find": "<基准表达中恰好出现一次的原文片段>", "replace": "<替换后的片段>",
        "basis": "<该点依据（来源锚点或口径出处）>", "group": None,
    }],
    "supplement_gaps": ["<缺口描述，仅 supplement 时>"],
    # 质量画像（6 维评分）/ EARS 改写脚手架 / 逐源语义对齐分（均可省略；不写入需求事实）
    "quality_profile": {
        "overall": "<0-100 整数>",
        "dimensions": [{
            "key": "|".join(d.value for d in QualityDimension),
            "score": "<0-100>", "note": "<一句口径>",
        }],
    },
    "ears_rewrite": {
        "pattern_type": "|".join(p.value for p in EarsPattern),
        "lines": ["<EARS 句式改写，每行一句>"], "note": "<拆分/改写提示>",
    },
    "source_alignments": [{
        "element_ref": "<给定来源知识项 id>",
        "alignment": "<0.0-1.0，当前表达对该来源的忠实度>",
        "note": "<一句依据，锚定来源引文>",
    }],
}


def _finding_schema(finding_types: list[str]) -> dict:
    """诊断发现项 JSON Schema（取值与领域枚举同源；旁路字段可省略）。"""
    return {
        "type": "object",
        "properties": {
            "finding_type": {"type": "string", "enum": finding_types},
            "diagnosis_summary": {"type": "string", "minLength": 1},
            "basis_summary": {"type": "string"},
            "rule_code": {"type": "string", "enum": [r.value for r in RequirementQualityRule]},
            "evidence_span": {"type": "string"},
            "severity": {"type": "string", "enum": [s.value for s in QualitySeverity]},
            "dimension": {"type": "string", "enum": [d.value for d in QualityDimension]},
        },
        "required": ["finding_type", "diagnosis_summary", "basis_summary"],
    }


_REVISION_POINT_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "finding_index": {"type": "integer", "minimum": 0},
        "find": {"type": "string", "minLength": 1},
        "replace": {"type": "string", "minLength": 1},
        "basis": {"type": "string"},
        "group": {"type": ["string", "null"]},
    },
    "required": ["label", "finding_index", "find", "replace", "basis"],
}

_QUALITY_META_SCHEMAS = {
    # v2 旁路元数据（可省略；解析侧降级不拒收，schema 只约束基本形状不设必填）
    "quality_profile": {
        "type": "object",
        "properties": {
            "overall": {"type": "integer", "minimum": 0, "maximum": 100},
            "dimensions": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": [d.value for d in QualityDimension]},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "note": {"type": "string"},
                },
                "required": ["key", "score"],
            }},
        },
    },
    "ears_rewrite": {
        "type": "object",
        "properties": {
            "pattern_type": {"type": "string", "enum": [p.value for p in EarsPattern]},
            "lines": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"},
        },
        "required": ["lines"],
    },
    "source_alignments": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "element_ref": {"type": "string"},
                "alignment": {"type": "number", "minimum": 0, "maximum": 1},
                "note": {"type": "string"},
            },
            "required": ["element_ref", "alignment"],
        },
    },
}


def _diagnosis_variant(kind: str) -> dict:
    """单个结论状态字的 schema 变体：把聚合守卫的一致性规则钉进语法层。

    revise 必带修订点；supplement 必带缺口；pass 发现项仅 no_blocker；
    非 revise 不得带修订点、非 supplement 不得带缺口（第二关违例不可生成）。
    """
    finding_types = ["no_blocker"] if kind == "pass" else [t.value for t in ReviewFindingType]
    points: dict = {"type": "array", "items": _REVISION_POINT_SCHEMA}
    gaps: dict = {"type": "array", "items": {"type": "string", "minLength": 1}}
    if kind == "revise":
        points["minItems"] = 1
    else:
        points["maxItems"] = 0
    if kind == "supplement":
        gaps["minItems"] = 1
    else:
        gaps["maxItems"] = 0
    return {
        "type": "object",
        "properties": {
            "verdict_kind": {"type": "string", "const": kind},
            "verdict_summary": {"type": "string", "minLength": 1},
            "findings": _wrap_findings(finding_types),
            "revision_points": points,
            "supplement_gaps": gaps,
            **_QUALITY_META_SCHEMAS,
        },
        "required": ["verdict_kind", "verdict_summary", "findings",
                     "revision_points", "supplement_gaps"],
    }


def _wrap_findings(finding_types: list[str]) -> dict:
    return {"type": "array", "minItems": 1, "maxItems": 6,
            "items": _finding_schema(finding_types)}


def diagnosis_response_schema() -> dict:
    """item_diagnosis 的 response_format JSON Schema：按 verdict_kind 分型的判别联合。

    取值单一来源=领域枚举（VerdictKind/ReviewFindingType/…），随枚举增删自动随动；
    与 _DIAGNOSIS_OUTPUT 提示词内嵌文本互为双保险（设计裁定 2）。
    """
    return {"oneOf": [_diagnosis_variant(k.value) for k in VerdictKind]}


class LlmRequirementItemDiagnoser:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def diagnose(
        self, project_ref: str, diagnosis_mode: str, item: dict,
        sources: list[dict], raw_text: str, revisions: list[dict],
        prior_findings: list[dict], excluded_points: Optional[list[dict]] = None,
        thread_context: str = "", business_sources: Optional[list[dict]] = None,
        attestation: Optional[dict] = None,
    ) -> ItemVerdictOutcome:
        system, user = render_pair(
            "item_diagnosis",
            project_ref=project_ref,
            diagnosis_mode=diagnosis_mode,
            item=json.dumps(item, ensure_ascii=False),
            sources=json.dumps(sources, ensure_ascii=False),
            business_sources=json.dumps(business_sources or [], ensure_ascii=False),
            raw_text=raw_text,
            revisions=json.dumps(revisions, ensure_ascii=False),
            # 无背书恒传空串（不是 "null"）：模板据其真值判断「人工确认来源」那一**分段**渲
            # 不渲染，无背书条目的 **user 块**因此逐字节等于本次改动之前。范围仅限 user 块：
            # system 块新增的人工确认处置规则不受该条件约束，对所有条目统一生效（这是刻意的，
            # 否则模型会在没有背书的条目上凭空声称「人工确认已闭合出处缺口」）。
            attestation=json.dumps(attestation, ensure_ascii=False) if attestation else "",
            prior_findings=json.dumps(prior_findings, ensure_ascii=False),
            excluded_points=json.dumps(excluded_points or [], ensure_ascii=False),
            thread_context=thread_context or "（无）",
            output_schema=prompt_dumps(_DIAGNOSIS_OUTPUT),
        )
        expression = str(item.get("expression") or "")
        stage, detail = "", ""
        # 守卫拒收自动重试一次（设计裁定 3）：重发同一请求，上限=1（预算不失控）。
        log_item_ref = str(item.get("item_ref") or "")  # 多条目批内按条目归因（日志=定位证据）
        for attempt in (1, 2):
            outcome, stage, detail = self._attempt(system, user, expression)
            if outcome is not None:
                if attempt > 1:
                    log_event(_LLM_COMPONENT, "llm.diagnosis.retry_succeeded",
                              lane="item_diagnosis", item_ref=log_item_ref, attempt=attempt, ok=True)
                return outcome
            log_event(_LLM_COMPONENT, "llm.diagnosis.attempt_rejected", level="WARN",
                      lane="item_diagnosis", item_ref=log_item_ref, stage=stage, attempt=attempt, ok=False)
        return _failed_verdict(detail, stage)

    def _attempt(
        self, system: str, user: str, expression: str
    ) -> tuple[Optional[ItemVerdictOutcome], str, str]:
        """单次诊断请求：返回 (结论, 失败关, 白话原因)。

        parse 与 llm_error 分关（验收契约）：llm_error=传输/服务失败；
        parse=拿到回复但不可解析（含 max_tokens 截断的半截 JSON——grammar 约束下仍可能，
        设计裁定 5：此分支不得删除）。
        """
        try:
            chat_structured = getattr(self._client, "chat_structured", None)
            if chat_structured is not None:
                content = chat_structured(system, user, diagnosis_response_schema(), "item_diagnosis")
            else:  # 测试替身等只实现 chat 的最小客户端
                content = self._client.chat(system, user)
        except LlmError:
            return None, "llm_error", "诊断模型服务不可用或调用失败"
        try:
            data = _extract_json_object(content)
        except ValueError:
            return None, "parse", "模型回复不是完整可解析的 JSON（可能被输出长度截断）"
        outcome, stage, detail = _sanitize_verdict_staged(data, expression)
        if outcome is not None:
            return outcome, "", ""
        return None, stage or "structure", detail or "模型输出未通过结论校验"


_STUB_TESTABLE_MARK = "验收观察口径"
# stub 演示词表：模糊词 → (finding_type, rule_code, dimension, severity, 描述)
_STUB_DEMO_RULES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("尽快", "untestable", "INCOSE-R7", "verifiable", "medium", "为不可测量量词，无法验证，建议量化时限"),
    ("及时", "untestable", "INCOSE-R7", "verifiable", "medium", "为不可测量量词，建议量化时限"),
    ("快速", "untestable", "INCOSE-R7", "verifiable", "medium", "为不可测量量词，建议给出阈值"),
    ("良好", "untestable", "INCOSE-R7", "verifiable", "medium", "为不可测量表述，建议给量化指标"),
    ("较大", "missing_field", "SMELL-UNDEF", "complete", "medium", "未定义具体阈值，完整性不足"),
    ("超时", "missing_field", "SMELL-UNDEF", "complete", "medium", "未定义超时阈值，建议补入具体时长"),
    ("尽量", "ambiguous_expression", "MODAL-WEAK", "unambiguous", "medium", "弱化语气使强制性不明确，宜用「应/必须」"),
    ("所有", "missing_field", "SMELL-UNIVERSAL", "complete", "low", "全称量词范围过宽，建议限定适用边界"),
    ("任何", "missing_field", "SMELL-UNIVERSAL", "complete", "low", "全称量词范围过宽，建议限定适用边界"),
)


def _stub_demo_findings(expression: str, basis: str) -> tuple["DiagnosedFinding", ...]:
    """stub 演示：逐个恰好出现一次的模糊词产出带 span/规则/维度的发现项（多标注）。

    无匹配词时回退单条不可测发现项（无 span），保证 revise 结论仍有可绑定的发现项。
    """
    out: list[DiagnosedFinding] = []
    for word, ftype, rule, dim, sev, desc in _STUB_DEMO_RULES:
        if expression.count(word) == 1 and len(out) < 5:
            out.append(DiagnosedFinding(
                ftype, f"「{word}」{desc}。", basis,
                rule_code=rule, evidence_span=word, severity=sev, dimension=dim,
            ))
    if not out:
        out.append(DiagnosedFinding(
            "untestable", "当前表达缺少可验证口径。", basis,
            rule_code="INCOSE-R7", severity="medium", dimension="verifiable",
        ))
    return tuple(out)


class StubRequirementItemDiagnoser:
    """无模型/测试用（确定性规则）：
    表达含「验收观察口径」→ 建议通过；含「应撤回」→ 建议撤回；含「缺来源」→ 建议补充来源；
    已排除点非空 → 建议通过（尊重排除，不重复纠缠）；否则 → 建议修订（单点：补可验证口径）。
    """

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def diagnose(
        self, project_ref: str, diagnosis_mode: str, item: dict,
        sources: list[dict], raw_text: str, revisions: list[dict],
        prior_findings: list[dict], excluded_points: Optional[list[dict]] = None,
        thread_context: str = "", business_sources: Optional[list[dict]] = None,
        attestation: Optional[dict] = None,
    ) -> ItemVerdictOutcome:
        if self._failed:
            return _failed_verdict("诊断模型不可用", "llm_error")
        expression = str(item.get("expression") or "")
        # P7 与业务知识一致性（stub 确定性）：表达含「自动放行」且存在业务依据 → 与所引业务规则
        # 矛盾，走 source_inconsistency + 规则码 BIZ-RULE-CONFLICT（证据单列），建议修订以消解矛盾。
        if business_sources and "自动放行" in expression:
            rule = next((s.get("content") for s in business_sources
                         if s.get("element_type") == "business_rule"), "所引业务规则")
            return ItemVerdictOutcome(
                verdict_kind="revise",
                verdict_summary="条目动作与所引业务规则矛盾（R=S∧D 不成立），建议修订以消解。",
                findings=(DiagnosedFinding(
                    "source_inconsistency",
                    f"条目「自动放行」与业务依据矛盾：{rule}。",
                    "依据业务依据段所引业务规则（stub）",
                    rule_code="BIZ-RULE-CONFLICT", dimension="consistent", severity="high",
                ),),
                revision_points=({
                    "point_ref": "P1", "label": "消解与业务规则矛盾", "finding_index": 0,
                    "find": "自动放行", "replace": "经审批后放行",
                    "basis": "所引业务规则要求审批（stub）", "group": None,
                },),
                supplement_gaps=(), basis="stub 条目诊断完成",
            )
        basis = (
            "增量诊断覆盖本次修订表达和原来源依据（stub）"
            if diagnosis_mode == "incremental"
            else "诊断依据来自当前来源要素与条目字段（stub）"
        )
        # 桩件的 source_inconsistency 发现项一律带规则码 SRC-DRIFT（2026-07-25 冷审查 K6 消费）：
        # 服务侧的人工确认降格谓词已收窄为白名单「规则码 == SRC-DRIFT 才降格」，不带规则码的
        # 发现项不再降格。桩件是演示与离线链路的诊断器，不补规则码这两条就永远不会被降格，
        # 人工确认这个功能在那两条链路上等于整体失效。
        if "应撤回" in expression or "重复条目" in expression:
            return ItemVerdictOutcome(
                verdict_kind="withdraw",
                verdict_summary="该条目与既有确认态条目重复或不构成独立需求，建议撤回。",
                findings=(DiagnosedFinding(
                    "source_inconsistency", "条目不构成独立需求表达。", basis,
                    rule_code="SRC-DRIFT", dimension="consistent",
                ),),
                revision_points=(), supplement_gaps=(), basis="stub 条目诊断完成",
            )
        if "缺来源" in expression:
            return ItemVerdictOutcome(
                verdict_kind="supplement",
                verdict_summary="表达含来源中不存在的关键口径，需先补充来源依据。",
                findings=(DiagnosedFinding(
                    "source_inconsistency", "关键口径在来源中无锚点。", basis,
                    rule_code="SRC-DRIFT", dimension="consistent",
                ),),
                revision_points=(),
                supplement_gaps=("关键口径的出处（谁提出/哪次会议/什么凭证）",),
                basis="stub 条目诊断完成",
            )
        # 只有「采纳修订时用户没勾选的点」（kind == excluded_point）才当作「用户已表过态、判通过」。
        # 被否决的问题（kind == vetoed_finding）也走同一个上下文通道，但它只说明「这一条不是问题」，
        # 不代表整条表达没有别的问题——若把它也当成判通过的依据，用户否决过任意一条后此后每一轮
        # stub 诊断都会凭空放行、把没被否决的问题一起吞掉（veto 卡冷审查 C2，2026-07-21 消费修复）。
        stub_excluded = [
            e for e in (excluded_points or [])
            if isinstance(e, dict) and e.get("kind") == "excluded_point"
        ]
        if _STUB_TESTABLE_MARK in expression or stub_excluded:
            note = "未发现阻断问题。" if not stub_excluded else "尊重已排除的修订点，不重复纠缠；未发现新的阻断问题。"
            return ItemVerdictOutcome(
                verdict_kind="pass",
                verdict_summary=note + "表达可测且与来源一致，建议通过。",
                findings=(DiagnosedFinding("no_blocker", note, basis),),
                revision_points=(), supplement_gaps=(), basis="stub 条目诊断完成",
            )
        replace = expression.rstrip("。") + "，并明确" + _STUB_TESTABLE_MARK + "。"
        # v2 质量诊断器旁路（stub 演示数据）：逐个模糊词挂规则/span/维度（多标注）+ 6 维评分 + EARS
        vague = _stub_demo_findings(expression, basis)
        findings = (DiagnosedFinding("no_blocker", "来源依据可定位，未发现来源断裂。", basis),) + vague
        return ItemVerdictOutcome(
            verdict_kind="revise",
            verdict_summary="表达存在模糊/不可测表述，建议按修订点修订后自动增量重诊。",
            findings=findings,
            revision_points=({
                "point_ref": "P1", "label": "补充可验证口径", "finding_index": len(findings) - 1,
                "find": expression, "replace": replace,
                "basis": "验收判据需要可观察口径（stub）", "group": None,
            },),
            supplement_gaps=(), basis="stub 条目诊断完成",
            quality_profile={
                "overall": 72,
                "dimensions": [
                    {"key": "unambiguous", "score": 68, "note": "措辞尚清晰"},
                    {"key": "verifiable", "score": 58, "note": "缺可观察口径"},
                    {"key": "singular", "score": 78, "note": "单一职责"},
                    {"key": "complete", "score": 70, "note": "要件基本齐全"},
                    {"key": "consistent", "score": 74, "note": "与来源基本一致"},
                    {"key": "traceable", "score": 88, "note": "来源锚点齐全"},
                ],
            },
            ears_rewrite={
                "pattern_type": "event_driven",
                "lines": ["WHEN <触发条件>，THE 系统 SHALL <可观测响应>（附验收观察口径）。"],
                "note": "stub 脚手架；补入可验证口径后按 EARS 规整。",
            },
        )


def build_requirement_item_diagnoser(settings: Settings) -> RequirementItemDiagnoser:
    if settings.llm_base_url:
        # 结构化输出按 lane 灰度：item_diagnosis 在白名单内才开（其余 lane 本期不动）。
        lanes = {s.strip() for s in settings.llm_structured_lanes.split(",") if s.strip()}
        structured = settings.llm_structured_output if "item_diagnosis" in lanes else "off"
        return LlmRequirementItemDiagnoser(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
                structured_output=structured,
            )
        )
    return StubRequirementItemDiagnoser()


# ============================================================================
# SCN-003 v5 评审对话面：轻量重评 / 草案起草 / 解释问答
# 隔离底线同上：不外泄/持久化 Prompt 与原始响应；
# 草案不输出判断、解释不输出结构化产物；改判只经重评（maintain|supersede 显式二选一）。
# ============================================================================


@dataclass(frozen=True)
class ReevalOutcome:
    """轻量重评：maintain（附解释）或 supersede（附新结论对象）。"""

    action: str  # maintain / supersede
    explanation: str
    verdict: Optional[ItemVerdictOutcome]
    failed: bool = False


class ItemReevalResponder(Protocol):
    def reeval(
        self, item: dict, standing_verdict: dict, message: str,
        excluded_points: list[dict], thread_context: str,
    ) -> ReevalOutcome: ...


# maintain|supersede 二选一；supersede 附与诊断同构的结论对象（_sanitize_verdict 复用校验）
_REEVAL_ACTIONS = ("maintain", "supersede")
_REEVAL_OUTPUT = {
    "action": "|".join(_REEVAL_ACTIONS),
    "explanation": "<对用户质疑的回应，≤200字；maintain 时必填>",
    "verdict": _DIAGNOSIS_OUTPUT,
}


class LlmItemReevalResponder:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def reeval(
        self, item: dict, standing_verdict: dict, message: str,
        excluded_points: list[dict], thread_context: str,
    ) -> ReevalOutcome:
        # 人工确认背书从结论上下文里取出来单独渲染成条件区块（与 item_diagnosis 同形态）：
        # 同一事实不以两种格式重复入上下文，故取出后不再留在 standing_verdict 的 JSON 里。
        context = dict(standing_verdict)
        attestation = context.pop("attestation", None)
        system, user = render_pair(
            "item_reeval",
            item=json.dumps(item, ensure_ascii=False),
            standing_verdict=json.dumps(context, ensure_ascii=False),
            attestation=json.dumps(attestation, ensure_ascii=False) if attestation else "",
            message=message,
            excluded_points=json.dumps(excluded_points, ensure_ascii=False),
            thread_context=thread_context or "（无）",
            output_schema=prompt_dumps(_REEVAL_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_object(content)
        except (LlmError, ValueError):
            return ReevalOutcome(action="maintain", explanation="", verdict=None, failed=True)
        action = str(data.get("action") or "").strip()
        explanation = str(data.get("explanation") or "").strip()
        if action == "maintain":
            if not explanation:
                return ReevalOutcome(action="maintain", explanation="", verdict=None, failed=True)
            return ReevalOutcome(action="maintain", explanation=explanation[:2000], verdict=None)
        if action == "supersede":
            verdict = _sanitize_verdict(data.get("verdict") or {}, str(item.get("expression") or ""))
            if verdict is None:
                return ReevalOutcome(action="maintain", explanation="", verdict=None, failed=True)
            return ReevalOutcome(action="supersede", explanation=explanation[:2000], verdict=verdict)
        return ReevalOutcome(action="maintain", explanation="", verdict=None, failed=True)


class StubItemReevalResponder:
    """无模型/测试用：消息含「改判」→ supersede 为建议通过；否则 maintain + 解释。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def reeval(
        self, item: dict, standing_verdict: dict, message: str,
        excluded_points: list[dict], thread_context: str,
    ) -> ReevalOutcome:
        if self._failed:
            return ReevalOutcome(action="maintain", explanation="", verdict=None, failed=True)
        if "改判" in message:
            return ReevalOutcome(
                action="supersede",
                explanation="你的依据成立，改判为建议通过（stub）。",
                verdict=ItemVerdictOutcome(
                    verdict_kind="pass",
                    verdict_summary="经重评，原判定依据不成立，建议通过。",
                    findings=(DiagnosedFinding("no_blocker", "重评未发现阻断问题。", "对话重评（stub）"),),
                    revision_points=(), supplement_gaps=(), basis="stub 重评完成",
                ),
            )
        summary = str(standing_verdict.get("verdict_summary") or "当前结论")
        return ReevalOutcome(
            action="maintain",
            explanation=f"判定依据：{summary}（stub 解释）。结论维持；若你认为依据不成立，请给出理由，我会重评。",
            verdict=None,
        )


def build_item_reeval_responder(settings: Settings) -> ItemReevalResponder:
    if settings.llm_base_url:
        return LlmItemReevalResponder(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                timeout=settings.llm_dialogue_timeout, max_tokens=settings.llm_dialogue_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubItemReevalResponder()


@dataclass(frozen=True)
class DraftOutcome:
    """修订草案（作品）：未采纳零副作用；note 承载缺来源预警。

    reason：cannot_comply 拒绝通道——proposed_value 为空、failed=False，
    携带一句可直接展示给用户的中文原因（区别于 failed=True 的基础设施失败）。
    """

    proposed_value: str
    note: str
    reason: str = ""
    failed: bool = False


class ItemDraftComposer(Protocol):
    def compose(
        self, item: dict, sources: list[dict], intent: str, current_draft: Optional[str],
        structure_context: Optional[dict] = None,
    ) -> DraftOutcome: ...


# 结论对象契约：status=cannot_comply 为显式拒绝通道（意图不是修订诉求时不得静默空草案）
_DRAFT_OUTPUT = {
    "status": "done|cannot_comply",
    "reason": "<仅 cannot_comply 时给用户的一句中文原因>",
    "proposed_value": "<修订后的完整表达（一句完整的需求表述）>",
    "note": "<新增表述缺来源锚点时的一句提醒，≤50字；无则空字符串>",
}


class LlmItemDraftComposer:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def compose(
        self, item: dict, sources: list[dict], intent: str, current_draft: Optional[str],
        structure_context: Optional[dict] = None,
    ) -> DraftOutcome:
        system, user = render_pair(
            "item_draft",
            item=json.dumps(item, ensure_ascii=False),
            sources=json.dumps(sources, ensure_ascii=False),
            intent=intent,
            current_draft=current_draft or "（无，在当前表达上起草第 1 稿）",
            # 结构体检上下文（缺失成分＋判定原因＋补写示例＋句式模板）：与区4 体检报告同源。
            # 没有有效体检时传空对象，模板渲染成「无」，不让模型以为漏给了什么。
            structure_context=(
                json.dumps(structure_context, ensure_ascii=False) if structure_context else ""
            ),
            output_schema=prompt_dumps(_DRAFT_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_object(content)
        except (LlmError, ValueError):
            return DraftOutcome(proposed_value="", note="", failed=True)
        if str(data.get("status") or "done").strip() == "cannot_comply":
            reason = str(data.get("reason") or "").strip()[:500]
            return DraftOutcome(
                proposed_value="", note="",
                reason=reason or "AI 判断该意图无法起草为修订草案（未给出原因）",
            )
        proposed = str(data.get("proposed_value") or "").strip()
        if not proposed:
            return DraftOutcome(proposed_value="", note="", failed=True)
        return DraftOutcome(
            proposed_value=proposed[:2000],
            note=str(data.get("note") or "").strip()[:500],
        )


class StubItemDraftComposer:
    """无模型/测试用：在当前稿（或当前表达）上追加意图正文。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def compose(
        self, item: dict, sources: list[dict], intent: str, current_draft: Optional[str],
        structure_context: Optional[dict] = None,
    ) -> DraftOutcome:
        if self._failed:
            return DraftOutcome(proposed_value="", note="", failed=True)
        base = (current_draft or str(item.get("expression") or "")).rstrip("。")
        payload = intent.split("：", 1)[-1].strip().rstrip("。") if "：" in intent else intent.strip().rstrip("。")
        proposed = f"{base}；{payload}。" if payload else base + "。"
        corpus = "".join(str(s.get("content") or "") + str(s.get("source_quote") or "") for s in sources)
        novel = [t for t in payload.replace("，", " ").split() if any(c.isdigit() for c in t) and t not in corpus]
        note = (
            f"新增表述「{novel[0]}」在现有来源中无锚点；采纳后自动重诊会以「建议补充来源」拦住缺口（stub）"
            if novel else ""
        )
        return DraftOutcome(proposed_value=proposed[:2000], note=note)


def build_item_draft_composer(settings: Settings) -> ItemDraftComposer:
    if settings.llm_base_url:
        return LlmItemDraftComposer(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                # 对话 lane 独立预算（起草/解释/重评输出有界，不共用长生成预算）
                timeout=settings.llm_dialogue_timeout, max_tokens=settings.llm_dialogue_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubItemDraftComposer()


# ---- 为条目找候选来源（issue #30 出口三部曲之二 / AEP-095 对话面扩展）----
# 输入＝条目内容＋同批次「已确认且未链接到本条」的要素差集（带原文引文）；
# 输出＝按相关度排序、逐条以要素 id 引用并带推荐理由的候选。ADR-0002 P3「说缺必说补」：
# supplement 结论不再只报缺口，同时给出候选来源与登记动作（动作接线属前端后续卡）。


@dataclass(frozen=True)
class SourceCandidateOutcome:
    """候选来源（作品）：candidates 为按相关度排序的要素 id 引用（逐条带推荐理由）。

    reason：cannot_comply 拒绝通道——candidates 为空、failed=False，携带一句可直接
    展示给用户的中文原因（区别于 failed=True 的基础设施失败）。诚实性优先：候选集为空
    或语料不足以支撑任何推荐时如实拒绝，不凑数（ADR-0002 §2.2）。
    """

    candidates: tuple[dict, ...]
    reason: str = ""
    failed: bool = False


class ItemSourceCandidateComposer(Protocol):
    def find(self, item: dict, candidates: list[dict]) -> SourceCandidateOutcome: ...


# 结论对象契约：status=cannot_comply 为显式拒绝通道；候选只能引用输入差集中的要素 id
# （禁自拟不存在的来源句，与「依据可追溯」纪律同源）。
_SOURCE_CANDIDATE_OUTPUT = {
    "status": "done|cannot_comply",
    "reason": "<仅 cannot_comply 时给用户的一句中文原因>",
    "candidates": [
        {
            "element_id": "<候选要素 id，必须逐字取自输入候选集中的 id>",
            "reason": "<为何这条要素可能是本条目的真实来源，一句中文，可引原文引文为据>",
            "rank": "<相关度排序，1 为最相关（整数）>",
        }
    ],
}


class LlmItemSourceCandidateComposer:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def find(self, item: dict, candidates: list[dict]) -> SourceCandidateOutcome:
        system, user = render_pair(
            "item_source_candidates",
            item=json.dumps(item, ensure_ascii=False),
            candidates=json.dumps(candidates, ensure_ascii=False),
            output_schema=prompt_dumps(_SOURCE_CANDIDATE_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_object(content)
        except (LlmError, ValueError):
            return SourceCandidateOutcome(candidates=(), failed=True)
        if str(data.get("status") or "done").strip() == "cannot_comply":
            reason = str(data.get("reason") or "").strip()[:500]
            return SourceCandidateOutcome(
                candidates=(),
                reason=reason or "AI 判断当前语料不足以为本条目推荐候选来源（未给出原因）",
            )
        allowed = {str(c.get("id")) for c in candidates}
        ranked: list[dict] = []
        seen: set[str] = set()
        for raw in data.get("candidates") or []:
            if not isinstance(raw, dict):
                continue
            eid = str(raw.get("element_id") or "").strip()
            if eid not in allowed or eid in seen:  # 幻觉 id 或重复：丢弃，候选只能来自输入差集
                continue
            seen.add(eid)
            try:
                rank = int(raw.get("rank"))
            except (TypeError, ValueError):
                rank = len(ranked) + 1
            ranked.append({
                "element_id": eid, "rank": rank,
                "reason": str(raw.get("reason") or "").strip()[:500],
            })
        if not ranked:  # 无任何落在差集内的候选：等同 cannot_comply，不静默返回空成功
            return SourceCandidateOutcome(
                candidates=(),
                reason=str(data.get("reason") or "").strip()[:500]
                or "AI 未能在给定要素中找到可作为本条目来源的候选",
            )
        ranked.sort(key=lambda c: c["rank"])
        return SourceCandidateOutcome(candidates=tuple(ranked))


class StubItemSourceCandidateComposer:
    """无模型/测试用：把输入差集按原序返回为候选（每条桩理由）；空输入走 cannot_comply。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def find(self, item: dict, candidates: list[dict]) -> SourceCandidateOutcome:
        if self._failed:
            return SourceCandidateOutcome(candidates=(), failed=True)
        if not candidates:
            return SourceCandidateOutcome(
                candidates=(), reason="当前批次没有可作候选的已确认要素（stub）",
            )
        ranked = [
            {"element_id": str(c.get("id")),
             "rank": i + 1,
             "reason": f"与条目表达可能相关（stub，按输入序 {i + 1}）"}
            for i, c in enumerate(candidates)
        ]
        return SourceCandidateOutcome(candidates=tuple(ranked))


def build_item_source_candidate_composer(settings: Settings) -> ItemSourceCandidateComposer:
    if settings.llm_base_url:
        return LlmItemSourceCandidateComposer(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                # 对话 lane 独立预算（找来源输出有界，不共用长生成预算）
                timeout=settings.llm_dialogue_timeout, max_tokens=settings.llm_dialogue_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubItemSourceCandidateComposer()


# ---- 章节撰稿 AI 起草初稿（AEP-110）：只预填撰稿阶段草稿，发布渲染仍确定性 ----


@dataclass(frozen=True)
class SectionManuscriptDraftOutcome:
    """章节撰稿初稿（作品）：写入撰稿阶段供人工完善确认，未确认前不进渲染事实。

    reason：cannot_comply 拒绝通道（输入不足以起草时明确拒绝、不杜撰）；
    failed=True 为基础设施失败（模型不可用/解析失败），与 cannot_comply 区分。
    """

    draft: str
    reason: str = ""
    failed: bool = False


class SectionManuscriptDrafter(Protocol):
    def draft(
        self,
        *,
        section_title: str,
        section_purpose: str,
        content_types_text: str,
        assets: list[dict],
        examples: list[str],
        project_scope: str = "",
        project_background: str = "",
    ) -> SectionManuscriptDraftOutcome: ...


# 结论对象契约：status=cannot_comply 为显式拒绝通道（输入不足以起草时不得静默空初稿）
_SECTION_MANUSCRIPT_DRAFT_OUTPUT = {
    "status": "done|cannot_comply",
    "reason": "<仅 cannot_comply 时给用户的一句中文原因>",
    "draft": "<该章节初稿正文（通顺的说明性正文，可含 {project_name}/{coverage_scope} 占位符）>",
}


class LlmSectionManuscriptDrafter:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def draft(
        self,
        *,
        section_title: str,
        section_purpose: str,
        content_types_text: str,
        assets: list[dict],
        examples: list[str],
        project_scope: str = "",
        project_background: str = "",
    ) -> SectionManuscriptDraftOutcome:
        system, user = render_pair(
            "section_manuscript_draft",
            section_title=section_title,
            section_purpose=section_purpose or "",
            content_types=content_types_text,
            assets=json.dumps(assets, ensure_ascii=False),
            examples=json.dumps(examples, ensure_ascii=False),
            project_scope=project_scope or "",
            project_background=project_background or "",
            output_schema=prompt_dumps(_SECTION_MANUSCRIPT_DRAFT_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_object(content)
        except (LlmError, ValueError):
            return SectionManuscriptDraftOutcome(draft="", failed=True)
        if str(data.get("status") or "done").strip() == "cannot_comply":
            reason = str(data.get("reason") or "").strip()[:500]
            return SectionManuscriptDraftOutcome(
                draft="",
                reason=reason or "AI 判断当前输入不足以起草该章节初稿（未给出原因）",
            )
        draft = str(data.get("draft") or "").strip()
        if not draft:
            return SectionManuscriptDraftOutcome(draft="", failed=True)
        return SectionManuscriptDraftOutcome(draft=draft[:8000])


class StubSectionManuscriptDrafter:
    """无模型/测试用：由章节说明 + 关联资产计数确定性拼出初稿；输入全空则显式拒绝。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def draft(
        self,
        *,
        section_title: str,
        section_purpose: str,
        content_types_text: str,
        assets: list[dict],
        examples: list[str],
        project_scope: str = "",
        project_background: str = "",
    ) -> SectionManuscriptDraftOutcome:
        if self._failed:
            return SectionManuscriptDraftOutcome(draft="", failed=True)
        purpose = (section_purpose or "").strip()
        if not purpose and not assets:
            return SectionManuscriptDraftOutcome(
                draft="", reason="章节说明为空且无关联确认态资产，输入不足以起草初稿（stub）",
            )
        base = purpose or (section_title or "").strip()
        asset_hint = f"（依据 {len(assets)} 项确认态需求资产起草）" if assets else ""
        style_hint = "（参考章节样例风格）" if examples else ""
        return SectionManuscriptDraftOutcome(draft=f"{base}{asset_hint}{style_hint}（AI 起草初稿，待完善）")


def build_section_manuscript_drafter(settings: Settings) -> SectionManuscriptDrafter:
    if settings.llm_base_url:
        return LlmSectionManuscriptDrafter(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                timeout=settings.llm_dialogue_timeout, max_tokens=settings.llm_dialogue_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubSectionManuscriptDrafter()


class ItemExplainer(Protocol):
    def explain(self, item: dict, verdict_context: dict, question: str) -> str:
        """只读解释；返回空串 = 失败。禁止输出结构化产物。"""
        ...


class LlmItemExplainer:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def explain(self, item: dict, verdict_context: dict, question: str) -> str:
        # 纯文本输出 lane：无 output_schema；拒绝通道=正文直说不知道/缺什么上下文
        # 人工确认背书取出来单独渲染成条件区块（与 item_diagnosis / item_reeval 同形态）。
        context = dict(verdict_context)
        attestation = context.pop("attestation", None)
        system, user = render_pair(
            "item_explain",
            item=json.dumps(item, ensure_ascii=False),
            verdict_context=json.dumps(context, ensure_ascii=False),
            attestation=json.dumps(attestation, ensure_ascii=False) if attestation else "",
            question=question,
        )
        try:
            return self._client.chat(system, user).strip()[:2000]
        except LlmError:
            return ""


class StubItemExplainer:
    def explain(self, item: dict, verdict_context: dict, question: str) -> str:
        summary = str(verdict_context.get("verdict_summary") or "当前结论")
        return f"判定依据：{summary}。证据均可回溯来源锚点（stub 解释）。"


def build_item_explainer(settings: Settings) -> ItemExplainer:
    if settings.llm_base_url:
        return LlmItemExplainer(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                timeout=settings.llm_dialogue_timeout, max_tokens=settings.llm_dialogue_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubItemExplainer()


# ============================================================================
# SCN-004-P01-N08 图表源码建议（图表协同服务送检）
# 隔离底线同上：只返回候选源码+说明+依据，不外泄/持久化 Prompt 与原始响应；
# 任何失败 → failed=True（候选建议不得伪造；用户未采纳前不改 LDM-012）。
# ============================================================================


@dataclass(frozen=True)
class ChartSourceProposal:
    source_code: str
    explanation: str
    title: str = ""  # 语义标题（创建初稿时回填图表主题；修订建议时服务端忽略）


@dataclass(frozen=True)
class ChartSuggestionOutcome:
    proposal: Optional[ChartSourceProposal]
    basis: str
    failed: bool = False


class ChartSourceSuggester(Protocol):
    """图表源码建议：基于来源条目与当前源码生成候选受控源码（建议不得自动生效）。"""

    def suggest(
        self, project_ref: str, chart: dict, sources: list[dict],
        current_source: str, intent: str,
    ) -> ChartSuggestionOutcome: ...


# 结论对象契约：status=cannot_comply 为显式拒绝通道（reason 经失败类 LDM-015 basis 停靠给用户）
_CHART_SUGGESTION_OUTPUT = {
    "status": "done|cannot_comply",
    "reason": "<仅 cannot_comply 时给用户的一句中文原因>",
    "title": "<基于来源条目与图表类型语义的图表标题，≤16字（修订时可沿用当前主题）>",
    "source_code": "<完整的图表源码（可直接替换当前源码）>",
    "explanation": "<一句话说明本次生成/修订做了什么、覆盖了哪些来源条目，≤50字>",
}


class LlmChartSourceSuggester:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def suggest(
        self, project_ref: str, chart: dict, sources: list[dict],
        current_source: str, intent: str,
    ) -> ChartSuggestionOutcome:
        system, user = render_pair(
            "chart_suggestion",
            project_ref=project_ref,
            chart=json.dumps(chart, ensure_ascii=False),
            sources=json.dumps(sources, ensure_ascii=False),
            current_source=current_source or "（空）",
            intent=intent or "（无）",
            output_schema=prompt_dumps(_CHART_SUGGESTION_OUTPUT),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_object(content)
        except (LlmError, ValueError):
            return ChartSuggestionOutcome(proposal=None, basis="图表建议模型不可用或结果不可解析", failed=True)

        if str(data.get("status") or "done").strip() == "cannot_comply":
            reason = str(data.get("reason") or "").strip()[:500]
            return ChartSuggestionOutcome(
                proposal=None,
                basis=reason or "AI 判断该意图无法在来源支撑内生成图表（未给出原因）",
                failed=True,
            )

        source_code = str(data.get("source_code") or "").strip()
        if not source_code:
            return ChartSuggestionOutcome(proposal=None, basis="模型未返回可用图表源码，结果不可承接", failed=True)
        explanation = str(data.get("explanation") or "").strip()[:1000]
        title = str(data.get("title") or "").strip()[:32]
        return ChartSuggestionOutcome(
            proposal=ChartSourceProposal(source_code=source_code, explanation=explanation, title=title),
            basis="图表源码建议生成完成",
        )


class StubChartSourceSuggester:
    """无模型/测试用：按表达方式确定性生成覆盖全部来源条目的源码骨架。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def suggest(
        self, project_ref: str, chart: dict, sources: list[dict],
        current_source: str, intent: str,
    ) -> ChartSuggestionOutcome:
        if self._failed:
            return ChartSuggestionOutcome(proposal=None, basis="图表建议模型不可用", failed=True)
        format_ = str(chart.get("format") or "mermaid")
        labels = [
            f"{s.get('req_no') or s.get('id', '')[:8]} {str(s.get('expression') or '')[:20]}".strip()
            for s in sources
        ] or ["（无来源条目）"]
        if format_ == "markdown_table":
            rows = "\n".join(f"| {label} | 待补充 |" for label in labels)
            code = "| 需求条目 | 说明 |\n|---|---|\n" + rows
        elif format_ == "plantuml":
            notes = "\n".join(f"note left: {label}" for label in labels)
            code = "@startuml\nstart\n" + notes + "\nstop\n@enduml"
        else:
            chart_type = str(chart.get("chart_type") or "flowchart")
            header = {
                "state_diagram": "stateDiagram-v2",
                "relation_diagram": "erDiagram",
                "sequence_diagram": "sequenceDiagram",
            }.get(chart_type, "flowchart TD")
            if header == "flowchart TD":
                nodes = "\n".join(f"  N{i}[\"{label}\"]" for i, label in enumerate(labels, 1))
                edges = "\n".join(f"  N{i} --> N{i + 1}" for i in range(1, len(labels)))
                code = header + "\n" + nodes + ("\n" + edges if edges else "")
            elif header == "sequenceDiagram":
                code = header + "\n" + "\n".join(
                    f"  A->>B: {label}" for label in labels
                )
            elif header == "stateDiagram-v2":
                code = header + "\n  [*] --> S1\n" + "\n".join(
                    f"  S{i} --> S{i + 1}: {label}" for i, label in enumerate(labels, 1)
                )
            else:
                code = header + "\n" + "\n".join(
                    f"  E{i} ||--o{{ E{i + 1} : \"{label}\"" for i, label in enumerate(labels, 1)
                )
        first_label = labels[0][:12] if labels else ""
        return ChartSuggestionOutcome(
            proposal=ChartSourceProposal(
                source_code=code,
                explanation="stub 建议：按来源条目生成源码骨架" + (f"（意图：{intent[:50]}）" if intent else ""),
                title=f"{first_label}示意".strip(),
            ),
            basis="stub 图表源码建议完成",
        )


def build_chart_source_suggester(settings: Settings) -> ChartSourceSuggester:
    if settings.llm_base_url:
        return LlmChartSourceSuggester(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubChartSourceSuggester()


# ============================================================================
# SCN-004-P02-N03 图文一致性与隐藏需求核对（图表协同服务送检）
# AI 只形成发现项（疑似隐藏需求/图文冲突/来源覆盖缺口/追溯缺口/无明显问题/无法判断）；
# 不得确认图表；失败 → failed=True，本流程不得降级为纯人工确认。
# ============================================================================


# 取值单一来源：领域枚举（_sanitize_chart_findings 校验与输出形状共用）
_CHART_FINDING_TYPES = frozenset(t.value for t in ChartFindingType)


@dataclass(frozen=True)
class ChartCheckFinding:
    finding_type: str
    summary: str
    basis_summary: str
    related_source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChartVerificationOutcome:
    findings: tuple[ChartCheckFinding, ...]
    basis: str
    failed: bool = False


class ChartVerifier(Protocol):
    """图文一致性核对：判断图表表达是否被来源支撑并形成发现项集合。"""

    def verify(
        self, project_ref: str, chart: dict, sources: list[dict],
        trace_links: list[dict],
    ) -> ChartVerificationOutcome: ...


# 输出 JSON 形状与解析器同文件定义（undeterminable 即本 lane 的拒绝通道）
_CHART_VERIFICATION_OUTPUT_ITEM = {
    "finding_type": "|".join(t.value for t in ChartFindingType),
    "summary": "<一句话说清问题或说明无明显问题，≤50字>",
    "basis_summary": "<依据摘要：图表中的具体表达与对应来源条目>",
    "related_source_refs": ["<涉及的来源条目id>"],
}


def _sanitize_chart_findings(data: list) -> list[ChartCheckFinding]:
    findings: list[ChartCheckFinding] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        ftype = str(entry.get("finding_type") or "").strip()
        summary = str(entry.get("summary") or "").strip()
        if ftype not in _CHART_FINDING_TYPES or not summary:
            continue  # 结构不完整的发现项不可承接
        refs = entry.get("related_source_refs")
        related = tuple(str(r) for r in refs) if isinstance(refs, list) else ()
        findings.append(ChartCheckFinding(
            finding_type=ftype,
            summary=summary[:1000],
            basis_summary=str(entry.get("basis_summary") or "").strip()[:1000],
            related_source_refs=related,
        ))
    return findings


class LlmChartVerifier:
    def __init__(self, client: LlmClient) -> None:
        self._client = client

    def verify(
        self, project_ref: str, chart: dict, sources: list[dict],
        trace_links: list[dict],
    ) -> ChartVerificationOutcome:
        system, user = render_pair(
            "chart_verification",
            project_ref=project_ref,
            chart=json.dumps(chart, ensure_ascii=False),
            sources=json.dumps(sources, ensure_ascii=False),
            trace_links=json.dumps(trace_links, ensure_ascii=False),
            output_schema=prompt_dumps(_CHART_VERIFICATION_OUTPUT_ITEM),
        )
        try:
            content = self._client.chat(system, user)
            data = _extract_json_array(content)
        except (LlmError, ValueError):
            return ChartVerificationOutcome(findings=(), basis="图文核对模型不可用或结果不可解析", failed=True)

        findings = _sanitize_chart_findings(data)
        if not findings:
            if isinstance(data, list) and len(data) == 0:
                # 健康路径容错：模型对无问题图表倾向返回 []（实测确定性复现，提示词
                # 禁止空数组也压不住）。空数组语义无歧义=未发现问题，映射为系统标注的
                # no_obvious_issue 发现项——仍进入人工逐项复核，不降级确认门禁。
                return ChartVerificationOutcome(
                    findings=(ChartCheckFinding(
                        finding_type=ChartFindingType.NO_OBVIOUS_ISSUE.value,
                        summary="模型核对未报告任何问题，图表表达可被来源条目支撑。",
                        basis_summary="模型返回空发现项集合；按契约映射为无明显问题，供人工复核裁定。",
                        related_source_refs=tuple(str(s.get("id") or "") for s in sources),
                    ),),
                    basis="图文一致性核对完成（模型未报告问题；空发现集映射为无明显问题）",
                )
            # 非空但全部结构不合格（类型不在枚举/缺 summary）→ 仍按失败停靠，可重试
            return ChartVerificationOutcome(findings=(), basis="模型输出缺少必要核对结构，结果不可承接", failed=True)
        return ChartVerificationOutcome(findings=tuple(findings), basis="图文一致性核对完成")


class StubChartVerifier:
    """无模型/测试用：魔标驱动分支（源码含 @hidden/@conflict/@unknown），来源/追溯为空给缺口。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def verify(
        self, project_ref: str, chart: dict, sources: list[dict],
        trace_links: list[dict],
    ) -> ChartVerificationOutcome:
        if self._failed:
            return ChartVerificationOutcome(findings=(), basis="图文核对模型不可用", failed=True)
        source_code = str(chart.get("source_code") or "")
        source_ids = tuple(str(s.get("id") or "") for s in sources)
        findings: list[ChartCheckFinding] = []
        if "@hidden" in source_code:
            findings.append(ChartCheckFinding(
                finding_type="suspected_hidden_requirement",
                summary="图表中存在来源条目未覆盖的新增语义，疑似隐藏需求。",
                basis_summary="stub：源码含 @hidden 标记",
                related_source_refs=source_ids,
            ))
        if "@conflict" in source_code:
            findings.append(ChartCheckFinding(
                finding_type="chart_text_conflict",
                summary="图表表达与来源条目文字表述存在冲突。",
                basis_summary="stub：源码含 @conflict 标记",
                related_source_refs=source_ids,
            ))
        if "@unknown" in source_code:
            findings.append(ChartCheckFinding(
                finding_type="undeterminable",
                summary="当前上下文无法判断图表表达是否被来源支撑。",
                basis_summary="stub：源码含 @unknown 标记",
            ))
        if not sources:
            findings.append(ChartCheckFinding(
                finding_type="source_coverage_gap",
                summary="图表缺少可支撑的来源条目。",
                basis_summary="stub：来源集合为空",
            ))
        if not trace_links:
            findings.append(ChartCheckFinding(
                finding_type="trace_gap",
                summary="图表缺少预建立追溯关系。",
                basis_summary="stub：追溯关系集合为空",
            ))
        if not findings:
            findings.append(ChartCheckFinding(
                finding_type="no_obvious_issue",
                summary="图表表达可被来源条目支撑，未发现明显问题。",
                basis_summary="stub：逐来源比对未见冲突或缺口",
                related_source_refs=source_ids,
            ))
        return ChartVerificationOutcome(findings=tuple(findings), basis="stub 图文核对完成")


def build_chart_verifier(settings: Settings) -> ChartVerifier:
    if settings.llm_base_url:
        return LlmChartVerifier(
            LlmClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                timeout=settings.llm_timeout,
                max_tokens=settings.llm_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens,
                api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            )
        )
    return StubChartVerifier()


# ---- 区5 对话命令解释（AEP-095 斜杠预处理 / AEP-096）----

@dataclass(frozen=True)
class CommandInterpretation:
    """命令解释结论对象。

    status：done=解释出可派发操作；clarify=向用户追问（缺参/指代不明）；
    cannot_comply=显式拒绝通道。failed=True 为基础设施失败（≠拒绝）。
    """

    status: str
    operation: str = ""
    params: dict = None  # type: ignore[assignment]
    reason: str = ""
    failed: bool = False

    def __post_init__(self) -> None:  # frozen dataclass 的默认 dict 防共享
        if self.params is None:
            object.__setattr__(self, "params", {})


class ElementCommandInterpreter(Protocol):
    def interpret(self, command_word: Optional[str], message: str, context: dict) -> CommandInterpretation: ...


class ItemCommandInterpreter(Protocol):
    def interpret(self, command_word: str, message: str, context: dict) -> CommandInterpretation: ...


class FormationCommandInterpreter(Protocol):
    def interpret(self, command_word: str, message: str, context: dict) -> CommandInterpretation: ...


_COMMAND_STATUSES = {"done", "clarify", "cannot_comply"}

_ELEMENT_COMMAND_OUTPUT = {
    "status": "done|clarify|cannot_comply",
    "reason": "<clarify/cannot_comply 时给用户的一句中文追问或原因>",
    "operation": "<操作码：命令表白名单之一；自由文本取 revise.ai|review>",
    "params": {
        "new_element_type": "<要素类型稳定码（edit.adjust_type）>",
        "new_content": "<目标表达 / 拆分结果（换行分隔，每行一条）/ 补登表达>",
        "target_element_refs": ["<上下文要素清单中的 id（merge 系，含当前目标）>"],
        "old_text": "<勘误原文片段（erratum）>",
        "new_text": "<更正后文本（erratum）>",
        "content": "<补入的新事实（supplement）>",
        "basis": "<补入依据（supplement）>",
        "instruction": "<AI 修订/执行指令原样（revise.ai | ai_execution.*）>",
        "review_intent": "<复核意图原样（review）>",
    },
}

_ITEM_COMMAND_OUTPUT = {
    "status": "done|clarify|cannot_comply",
    "reason": "<clarify/cannot_comply 时给用户的一句中文追问或原因>",
    "operation": "<操作码：命令表白名单之一>",
    "params": {
        "diagnosis_mode": "<诊断模式稳定码（start_diagnosis，未写默认 standard）>",
        "scope": "selected|current（start_diagnosis）",
        "selected_point_ordinals": ["<采纳时点名的修订点序号（整数；未点名省略=全部）>"],
        "reason": "<拒绝结论/覆盖确认/撤回的理由>",
        "new_expression": "<修订后的完整表达（manual_revision）>",
        "instruction": "<修订方向原样（draft）>",
    },
}


_FORMATION_COMMAND_OUTPUT = {
    "status": "done|clarify|cannot_comply",
    "reason": "<clarify/cannot_comply 时给用户的一句中文追问或原因>",
    "operation": "<操作码：命令表白名单之一>",
    "params": {
        "scope": "selected|all（start_itemization）",
        "new_req_type": "<条目类型稳定码（revise.req_type）>",
        "field_key": "<修订字段稳定码（revise.field / draft.field，未点名默认 expression）>",
        "new_value": "<修订后的完整字段值（revise.field）>",
        "new_expressions": "<拆分结果（换行分隔，每行一条完整表达，split.manual）>",
        "target_item_refs": ["<上下文条目清单中的 item_ref（merge.manual，含当前目标）>"],
        "new_expression": "<归并后的完整表达（merge.manual，必填）>",
        "instruction": "<起草/规范化要求原样（draft.field | draft.normalize）>",
    },
}

# 模板名 → 输出契约（与解析器同文件单一来源；新增解释 lane 只加表项）
_COMMAND_OUTPUTS = {
    "element_command": _ELEMENT_COMMAND_OUTPUT,
    "item_command": _ITEM_COMMAND_OUTPUT,
    "formation_command": _FORMATION_COMMAND_OUTPUT,
}


def _parse_interpretation(content: str) -> CommandInterpretation:
    data = _extract_json_object(content)
    status = str(data.get("status") or "").strip()
    if status not in _COMMAND_STATUSES:
        return CommandInterpretation(status="clarify", failed=True)
    reason = str(data.get("reason") or "").strip()[:500]
    if status != "done":
        return CommandInterpretation(status=status, reason=reason)
    operation = str(data.get("operation") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if not operation:
        return CommandInterpretation(status="clarify", failed=True)
    return CommandInterpretation(status="done", operation=operation, params=params)


class _LlmCommandInterpreterBase:
    """两页共享的调用骨架：模板名与命令表不同，解析与失败语义相同。"""

    _template = ""

    def __init__(self, client: LlmClient, commands: list[dict]) -> None:
        self._client = client
        self._commands = commands

    def interpret(self, command_word: Optional[str], message: str, context: dict) -> CommandInterpretation:
        system, user = render_pair(
            self._template,
            commands=self._commands,
            command_word=command_word or "（无）",
            message=message,
            context=json.dumps(context, ensure_ascii=False),
            output_schema=prompt_dumps(_COMMAND_OUTPUTS[self._template]),
        )
        try:
            content = self._client.chat(system, user)
            return _parse_interpretation(content)
        except (LlmError, ValueError):
            return CommandInterpretation(status="clarify", failed=True)


class LlmElementCommandInterpreter(_LlmCommandInterpreterBase):
    _template = "element_command"


class LlmItemCommandInterpreter(_LlmCommandInterpreterBase):
    _template = "item_command"


class LlmFormationCommandInterpreter(_LlmCommandInterpreterBase):
    _template = "formation_command"


# ---- Stub：把原前端确定性解析移植到后端（无模型/测试环境保持确定性）----

def _after_colon(text: str) -> str:
    for sep in ("：", ":"):
        idx = text.find(sep)
        if idx >= 0:
            return text[idx + 1 :].strip()
    return ""


def _strip_command_word(message: str, word: str) -> str:
    """去掉 /命令词 前缀，返回命令正文。"""
    stripped = message.lstrip()
    for prefix in ("/", "／"):
        if stripped.startswith(prefix + word):
            return stripped[len(prefix) + len(word) :].lstrip(" \t：:，,")
    return stripped


_REVISE_VERBS = ("修订", "改写", "重写", "扩写", "润色", "改为", "改成", "起草", "完善")


class StubElementCommandInterpreter:
    """确定性解析（原 RequirementAnalysisFlow.handleSend 前端逻辑的移植）。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def interpret(self, command_word: Optional[str], message: str, context: dict) -> CommandInterpretation:
        if self._failed:
            return CommandInterpretation(status="clarify", failed=True)
        if command_word is None:
            if any(v in message for v in _REVISE_VERBS):
                return CommandInterpretation(status="done", operation="revise.ai", params={"instruction": message})
            return CommandInterpretation(status="done", operation="review", params={"review_intent": message})
        body = _strip_command_word(message, command_word)
        handler = getattr(self, f"_cmd_{_STUB_ELEMENT_HANDLERS[command_word]}", None)
        if handler is None:
            return CommandInterpretation(status="clarify", reason=f"命令 /{command_word} 暂不支持（stub）。")
        return handler(body, context)

    def _cmd_adjust_type(self, body: str, context: dict) -> CommandInterpretation:
        from app.domain.labels import ELEMENT_TYPE_LABELS

        for element_type, label in ELEMENT_TYPE_LABELS.items():
            if label in body:
                return CommandInterpretation(
                    status="done", operation="edit.adjust_type",
                    params={"new_element_type": element_type.value},
                )
        return CommandInterpretation(status="clarify", reason="请写出目标类型（如「功能需求」「约束」）。")

    def _cmd_revise_expression(self, body: str, context: dict) -> CommandInterpretation:
        content = _after_colon(body)
        if content:
            return CommandInterpretation(
                status="done", operation="edit.revise_expression", params={"new_content": content},
            )
        if body:
            return CommandInterpretation(status="done", operation="revise.ai", params={"instruction": body})
        return CommandInterpretation(status="clarify", reason="请在「修订为：」后写出目标表达，或写出修订方向由 AI 起草。")

    def _cmd_adjust_anchor(self, body: str, context: dict) -> CommandInterpretation:
        if not context.get("selection_text") and not context.get("has_selection"):
            return CommandInterpretation(status="clarify", reason="请先在区3 选中新的原文范围。")
        return CommandInterpretation(status="done", operation="edit.adjust_anchor", params={})

    def _cmd_split(self, body: str, context: dict) -> CommandInterpretation:
        import re

        payload = _after_colon(body) or body
        lines = [re.sub(r"^\s*\d+[.、）)]\s*", "", ln).strip() for ln in re.split(r"\n+", payload)]
        lines = [ln for ln in lines if ln]
        if len(lines) >= 2:
            return CommandInterpretation(
                status="done", operation="manual.split", params={"new_content": "\n".join(lines)},
            )
        if body:
            return CommandInterpretation(status="done", operation="ai_execution.split", params={"instruction": body})
        return CommandInterpretation(status="clarify", reason="请写出拆法（每行一条，至少两条），或写出拆分要求由 AI 建议。")

    def _cmd_merge(self, body: str, context: dict) -> CommandInterpretation:
        import re

        names = re.findall(r"「([^」]*)」", body)
        refs: list[str] = []
        selected = context.get("selected_element") or {}
        if selected.get("id"):
            refs.append(str(selected["id"]))
        for name in names:
            if not name:
                continue
            for element in context.get("elements") or []:
                content = str(element.get("content") or "")
                if content.startswith(name) or name in content:
                    ref = str(element.get("id") or "")
                    if ref and ref not in refs:
                        refs.append(ref)
                    break
        if len(refs) < 2:
            return CommandInterpretation(status="clarify", reason="请用「要素表达」点名参与合并的要素（至少一条其它要素）。")
        explicit = re.search(r"合并后表达[：:]\s*(\S[\s\S]*)$", body)
        if explicit and not explicit.group(1).strip().startswith("由 AI 起草"):
            return CommandInterpretation(
                status="done", operation="manual.merge",
                params={"target_element_refs": refs, "new_content": explicit.group(1).strip()},
            )
        return CommandInterpretation(
            status="done", operation="ai_execution.merge",
            params={"target_element_refs": refs, "instruction": body},
        )

    def _cmd_add_missing(self, body: str, context: dict) -> CommandInterpretation:
        content = _after_colon(body) or body or str(context.get("selection_text") or "")
        if not content:
            return CommandInterpretation(status="clarify", reason="请写出要补登的要素表达，或先在区3 选中原文。")
        return CommandInterpretation(status="done", operation="manual.add_missing", params={"new_content": content})

    def _cmd_erratum(self, body: str, context: dict) -> CommandInterpretation:
        import re

        quoted = re.findall(r"「([^」]*)」", body)
        if len(quoted) < 2 or not quoted[0].strip():
            return CommandInterpretation(status="clarify", reason="勘误格式：把「原文片段」改正为「更正后」。")
        return CommandInterpretation(
            status="done", operation="erratum",
            params={"old_text": quoted[0], "new_text": quoted[1]},
        )

    def _cmd_supplement(self, body: str, context: dict) -> CommandInterpretation:
        import re

        m = re.search(r"^(?:补入新事实[：:]\s*)?([\s\S]*?)\s*[（(]依据[：:]\s*([\s\S]*?)[)）]\s*$", body)
        if not m or not m.group(1).strip() or not m.group(2).strip():
            return CommandInterpretation(status="clarify", reason="补入格式：<内容>（依据：<谁说的/哪次会议/什么凭证>）。")
        return CommandInterpretation(
            status="done", operation="supplement",
            params={"content": m.group(1).strip(), "basis": m.group(2).strip()},
        )


_STUB_ELEMENT_HANDLERS = {
    "改类型": "adjust_type",
    "改表达": "revise_expression",
    "改范围": "adjust_anchor",
    "拆分": "split",
    "合并": "merge",
    "新增遗漏": "add_missing",
    "勘误": "erratum",
    "补入": "supplement",
}


class StubItemCommandInterpreter:
    """确定性解析（原 RequirementItemReviewFlow.handleSend 正则的移植）。"""

    _MODE_WORDS = {
        "快速": DiagnosisMode.QUICK,
        "标准": DiagnosisMode.STANDARD,
        "全面": DiagnosisMode.COMPREHENSIVE,
        "增量": DiagnosisMode.INCREMENTAL,
    }

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def interpret(self, command_word: str, message: str, context: dict) -> CommandInterpretation:
        if self._failed:
            return CommandInterpretation(status="clarify", failed=True)
        body = _strip_command_word(message, command_word)
        if command_word == "诊断":
            mode = DiagnosisMode.STANDARD
            for word, candidate in self._MODE_WORDS.items():
                if word in body:
                    mode = candidate
                    break
            scope = "selected" if ("勾选" in body and context.get("selected_item_refs")) else "current"
            return CommandInterpretation(
                status="done", operation="start_diagnosis",
                params={"diagnosis_mode": mode.value, "scope": scope},
            )
        if command_word == "采纳结论":
            import re

            ordinals = [int(n) for n in re.findall(r"\d+", body)] if "修订点" in body else []
            return CommandInterpretation(
                status="done", operation="adjudicate_adopt",
                params={"selected_point_ordinals": ordinals} if ordinals else {},
            )
        if command_word == "拒绝结论":
            import re

            reason = _after_colon(body) or re.sub(r"^第\s*\d+\s*轮\s*", "", body).strip()
            if not reason:
                return CommandInterpretation(status="clarify", reason="请写出拒绝理由（理由=回复正文，必填）。")
            return CommandInterpretation(status="done", operation="adjudicate_reject", params={"reason": reason})
        if command_word == "采纳草案":
            if not (context.get("draft") or {}).get("suggestion_ref"):
                return CommandInterpretation(status="clarify", reason="当前条目没有在途修订草案。")
            return CommandInterpretation(status="done", operation="adopt_draft", params={})
        if command_word == "修订":
            content = ""
            if "修订为" in body:
                content = _after_colon(body[body.find("修订为") :])
            if content:
                return CommandInterpretation(
                    status="done", operation="manual_revision", params={"new_expression": content},
                )
            if body:
                return CommandInterpretation(status="done", operation="draft", params={"instruction": body})
            return CommandInterpretation(status="clarify", reason="请写出「修订为：<表达>」或修订方向（转 AI 起草）。")
        if command_word == "找来源":
            # 无参命令：为当前条目在同批次未链接的已确认要素中检索候选来源
            return CommandInterpretation(status="done", operation="find_sources", params={})
        if command_word in ("覆盖确认", "撤回"):
            reason = _after_colon(body) or body
            if not reason:
                return CommandInterpretation(status="clarify", reason="理由必填：请在命令后写出理由。")
            operation = "override_confirm" if command_word == "覆盖确认" else "withdraw"
            return CommandInterpretation(status="done", operation=operation, params={"reason": reason})
        return CommandInterpretation(status="clarify", reason=f"命令 /{command_word} 暂不支持（stub）。")


class StubFormationCommandInterpreter:
    """确定性解析（无模型/测试环境保持确定性；与 FORMATION_COMMANDS 白名单对齐）。"""

    def __init__(self, failed: bool = False) -> None:
        self._failed = failed

    def interpret(self, command_word: str, message: str, context: dict) -> CommandInterpretation:
        if self._failed:
            return CommandInterpretation(status="clarify", failed=True)
        body = _strip_command_word(message, command_word)
        if command_word == "生成条目":
            scope = "selected" if ("勾选" in body and context.get("selected_element_refs")) else "all"
            return CommandInterpretation(status="done", operation="start_itemization", params={"scope": scope})
        if command_word == "改类型":
            from app.domain.labels import REQUIREMENT_ITEM_TYPE_LABELS

            for item_type, label in REQUIREMENT_ITEM_TYPE_LABELS.items():
                if label in body:
                    return CommandInterpretation(
                        status="done", operation="revise.req_type",
                        params={"new_req_type": item_type.value},
                    )
            return CommandInterpretation(status="clarify", reason="请写出目标条目类型（如「功能需求」「约束」）。")
        if command_word == "修订":
            from app.domain.labels import ITEM_REVISION_FIELD_LABELS

            field_key = "expression"
            for code, label in ITEM_REVISION_FIELD_LABELS.items():
                if label in body:
                    field_key = code
                    break
            content = ""
            if "修订为" in body:
                content = _after_colon(body[body.find("修订为"):])
            if content:
                return CommandInterpretation(
                    status="done", operation="revise.field",
                    params={"field_key": field_key, "new_value": content},
                )
            if body:
                if field_key != "expression":
                    return CommandInterpretation(
                        status="clarify", reason="该字段仅支持「修订为：<完整值>」直改，起草只支持条目表达。",
                    )
                return CommandInterpretation(
                    status="done", operation="draft.field",
                    params={"field_key": field_key, "instruction": body},
                )
            return CommandInterpretation(status="clarify", reason="请写出「修订为：<值>」或修订方向（转 AI 起草）。")
        if command_word == "规范化":
            return CommandInterpretation(
                status="done", operation="draft.normalize",
                params={"instruction": body} if body else {},
            )
        if command_word == "拆分":
            import re

            payload = _after_colon(body) or body
            lines = [re.sub(r"^\s*\d+[.、）)]\s*", "", ln).strip() for ln in re.split(r"\n+", payload)]
            lines = [ln for ln in lines if ln]
            if len(lines) >= 2:
                return CommandInterpretation(
                    status="done", operation="split.manual",
                    params={"new_expressions": "\n".join(lines)},
                )
            return CommandInterpretation(status="clarify", reason="请写出拆法（每行一条完整表达，至少两条）。")
        if command_word == "归并":
            import re

            refs: list[str] = []
            selected = context.get("selected_item") or {}
            if selected.get("item_ref"):
                refs.append(str(selected["item_ref"]))
            names = re.findall(r"「([^」]*)」", body) + re.findall(r"REQ-\d+", body)
            for name in names:
                if not name:
                    continue
                for item in context.get("pending_items") or []:
                    expression = str(item.get("expression") or "")
                    if item.get("req_no") == name or expression.startswith(name) or name in expression:
                        ref = str(item.get("item_ref") or "")
                        if ref and ref not in refs:
                            refs.append(ref)
                        break
            if len(refs) < 2:
                return CommandInterpretation(status="clarify", reason="请用 REQ 编号或「条目表达」点名参与归并的条目（至少一条其它条目）。")
            explicit = re.search(r"归并后表达[：:]\s*(\S[\s\S]*)$", body)
            if not explicit:
                return CommandInterpretation(status="clarify", reason="请写出「归并后表达：<完整表达>」（归并必填）。")
            return CommandInterpretation(
                status="done", operation="merge.manual",
                params={"target_item_refs": refs, "new_expression": explicit.group(1).strip()},
            )
        if command_word == "问来源":
            return CommandInterpretation(status="done", operation="explain.source", params={})
        if command_word == "引用依据":
            # P7：按名称在「业务知识候选」清单中解析用户点名的业务知识 → element_refs
            candidates = context.get("business_candidates") or []
            refs: list[str] = []
            for c in candidates:
                content = str(c.get("content") or "")
                name = content.split("：")[0].split(":")[0].split("是指")[0].strip()
                if (name and name in body) or (content and content[:8] in body):
                    ref = str(c.get("id") or "")
                    if ref and ref not in refs:
                        refs.append(ref)
            if not refs and body.strip() in ("", "全部", "推荐", "推荐的"):
                refs = [str(c.get("id")) for c in candidates if c.get("id")]
            if not refs:
                return CommandInterpretation(
                    status="clarify",
                    reason="请点名要引用的业务知识（术语/业务规则/角色/外部系统），或说「引用推荐的」。")
            return CommandInterpretation(
                status="done", operation="reference.supporting_basis",
                params={"element_refs": refs},
            )
        return CommandInterpretation(status="clarify", reason=f"命令 /{command_word} 暂不支持（stub）。")


def build_element_command_interpreter(settings: Settings) -> ElementCommandInterpreter:
    if settings.llm_base_url:
        from app.domain.chat_commands import ANALYSIS_COMMANDS, command_guide

        return LlmElementCommandInterpreter(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                # 解释 lane 独立预算：输出仅操作码+参数，30s 未回≈链路故障
                timeout=settings.llm_interpret_timeout, max_tokens=settings.llm_interpret_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            ),
            commands=command_guide(ANALYSIS_COMMANDS),
        )
    return StubElementCommandInterpreter()


def build_item_command_interpreter(settings: Settings) -> ItemCommandInterpreter:
    if settings.llm_base_url:
        from app.domain.chat_commands import ITEM_REVIEW_COMMANDS, command_guide

        return LlmItemCommandInterpreter(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                timeout=settings.llm_interpret_timeout, max_tokens=settings.llm_interpret_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            ),
            commands=command_guide(ITEM_REVIEW_COMMANDS),
        )
    return StubItemCommandInterpreter()


def build_formation_command_interpreter(settings: Settings) -> FormationCommandInterpreter:
    if settings.llm_base_url:
        from app.domain.chat_commands import FORMATION_COMMANDS, command_guide

        return LlmFormationCommandInterpreter(
            LlmClient(
                base_url=settings.llm_base_url, model=settings.llm_model,
                timeout=settings.llm_interpret_timeout, max_tokens=settings.llm_interpret_max_tokens,
                disable_thinking=settings.llm_disable_thinking,
                context_tokens=settings.llm_context_tokens, api_key=settings.llm_api_key,
                provider_type=settings.llm_provider_type,
                capability_profile=parse_capability_profile(settings.llm_capability_profile),
            ),
            commands=command_guide(FORMATION_COMMANDS),
        )
    return StubFormationCommandInterpreter()


def probe_llm_service(settings: Settings) -> dict:
    """模型服务有界探测（运行态面板组件；1s 超时，异常降级不抛出）。

    只报可达性与延迟，不发生成请求、不记录响应体（AGENTS.md 硬规则 8）。
    """
    if not settings.llm_base_url:
        return {"configured": False, "ok": None, "latency_ms": None}
    import time

    started = time.monotonic()
    try:
        resp = httpx.get(f"{settings.llm_base_url.rstrip('/')}/models", timeout=1.0)
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"configured": True, "ok": resp.status_code < 500, "latency_ms": latency_ms}
    except Exception:  # noqa: BLE001 探测失败降级
        return {"configured": True, "ok": False, "latency_ms": None}
