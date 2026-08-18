"""配置管理入口（04 §3.5 / CONN-006 / 04A §9）：支撑能力配置的读写与留痕。

边界铁律：
- 只写配置、不写治理事实——不形成确认结论、追溯关系或发布基线；
- 配置期写入供适配器读取，运行时对适配器的调用仍由 L2 服务发起（调用链不变）；
- 保存经 `审计留痕` 记录（config_audit：谁/何时/哪个域/哪些字段名，绝不记值）；
- 密钥只写不回显：读侧仅返回“已设置”+脱敏占位；密钥绝不进入日志（硬规则 8）；
- 外观偏好是浏览器本地偏好（04A §9.1），本服务不承接、不建域。
"""
from __future__ import annotations

import dataclasses
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    CapabilityItemRead,
    ConfigDomainRead,
    ConfigDomainStatusRead,
    ConfigFieldRead,
    ConfigSaveCommand,
    ConfigSaveResult,
    ConfigSecretRead,
    ExportReadinessItemRead,
    ExportReadinessRead,
    LlmProviderListRead,
    LlmProviderRead,
    LlmProviderSaveCommand,
    LlmProviderTypeRead,
    ModelCapabilityProbeResult,
    ModelConnectionTestCommand,
    ModelConnectionTestResult,
    ReferenceStandardCatalogRead,
    ReferenceStandardCategoryRead,
    ReferenceStandardRead,
    ReferenceStandardSaveCommand,
)
from app.adapters.diagram_render import mmdc_version, plantuml_version, resolve_tools
from app.adapters.docx_to_pdf import find_soffice, soffice_version
from app.adapters.llm import (
    CAP_CONTEXT,
    CAP_GENERATE,
    CAP_NOTE_OLLAMA_MODEL_LIMIT_ONLY,
    CAP_NOTE_THINKING_SEGMENT_HIDDEN,
    CAP_NOTE_VLLM_NEEDS_REASONING_PARSER,
    CAP_STATE_DEGRADED,
    CAP_STATE_SUPPORTED,
    CAP_STATE_UNKNOWN,
    CAP_STATE_UNSUPPORTED,
    CAP_REACHABLE,
    CAP_NOTE_THINKING_DECLARED_NOT_OBSERVED,
    CAP_NOTE_THINKING_DISABLED_ON_SERVER,
    CAP_STRUCTURED,
    CAP_THINKING,
    CAP_UNKNOWN_FIELDS,
    CAPABILITY_KEYS,
    DEFAULT_PROVIDER_TYPE,
    PROVIDER_LLAMA_CPP,
    PROVIDER_OLLAMA,
    PROVIDER_TYPE_KEYS,
    PROVIDER_TYPES,
    PROVIDER_VLLM,
    STRUCTURED_TIER_JSON_OBJECT,
    STRUCTURED_TIER_JSON_SCHEMA,
    STRUCTURED_TIER_PROMPT_ONLY,
    THINKING_OFF_CANDIDATES,
    THINKING_OFF_NOT_NEEDED,
    CapabilityProfile,
    chat_extension_fields,
    minimal_chat_payload,
    thinking_off_fields,
)
from app.config import Settings, settings as env_settings
from app.db.models import ConfigAudit, ConfigEntry
from app.domain.errors import InvalidInput, NotFound
from app.domain.item_profiles import CONVENTION_KEYS, DEFAULT_CONVENTION
from app.domain.reference_standards import (
    BUILTIN_STANDARDS,
    CATEGORY_KEYS,
    CATEGORY_LABELS,
    merge_catalog,
    normalize_custom_entries,
    normalize_disabled_keys,
    validate_custom_entries,
    validate_disabled_keys,
)
from app.log import log_event

_COMPONENT = "config-registry"

SECRET_PLACEHOLDER = "••••••••"

# 路径字段填错的白话说明：保存侧拒绝与前端提示共用这一句，两处措辞不各写一份。
PATH_FIELD_HINT = "需填绝对路径（以 / 开头），不支持 ~ 与相对路径"


def is_absolute_path_value(value: str) -> bool:
    """路径字段取值是否合规：绝对路径且不以 `~` 开头。

    这两种写法都会让文件落到「执行该次操作的那个进程的当前目录」下，而且不报任何错：
    相对路径的落点随进程启动目录漂移（请求内执行与 worker 执行不是同一个进程）；
    `~` 更隐蔽——pathlib 不展开它，`mkdir` 会真造出一个名叫 `~` 的目录，用户以为存进了家目录。
    """
    text = value.strip()
    return bool(text) and not text.startswith("~") and PurePosixPath(text).is_absolute()


@dataclass(frozen=True)
class DomainSpec:
    """配置域定义：字段白名单 + env 默认值来源 + 下游单元口径（04 §3.5 配置域模块表）。"""

    domain: str
    label: str
    group: str
    downstream: str
    fields: tuple[str, ...]
    secret_fields: tuple[str, ...]
    env_defaults: Callable[[Settings], dict[str, Any]]
    # 字段取值白名单（封闭枚举域）：{字段名: 允许值元组}；保存时超出白名单拒绝（InvalidInput）。
    field_whitelist: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)
    # 文件系统路径字段：保存时校验取值形态（必须绝对路径），越界拒绝（InvalidInput）。
    path_fields: tuple[str, ...] = ()


DOMAIN_SPECS: dict[str, DomainSpec] = {
    spec.domain: spec
    for spec in (
        DomainSpec(
            domain="model_service",
            label="模型服务",
            group="外部能力",
            downstream="模型服务适配器",
            fields=("service_name", "base_url", "model", "timeout_seconds", "max_retries", "concurrency_limit"),
            secret_fields=("api_key",),
            env_defaults=lambda s: {
                "service_name": "",
                "base_url": s.llm_base_url or "",
                "model": s.llm_model,
                "timeout_seconds": s.llm_timeout,
                "max_retries": 3,
                "concurrency_limit": 5,
            },
        ),
        DomainSpec(
            domain="export",
            label="导出能力",
            group="外部能力",
            downstream="文档转换适配器",
            fields=("export_dir",),
            secret_fields=(),
            env_defaults=lambda s: {"export_dir": s.export_dir},
            path_fields=("export_dir",),
        ),
        DomainSpec(
            domain="chart_rendering",
            label="图表渲染",
            group="外部能力",
            downstream="图表渲染适配器",
            fields=("renderer", "security_level"),
            secret_fields=(),
            # 当前渲染由前端 mermaid 承接（04 §3.5：系统不实现渲染）；此处登记能力口径。
            env_defaults=lambda s: {"renderer": "mermaid（前端内置渲染）", "security_level": "strict"},
        ),
        DomainSpec(
            domain="requirement_convention",
            label="需求规约",
            group="生成治理",
            downstream="条目形成服务 / 模型推理编排服务（AEP-007 lane）",
            fields=("active_convention",),
            secret_fields=(),
            # 无配置行 = ears-cn（与现状行为完全一致，零迁移）。
            env_defaults=lambda s: {"active_convention": DEFAULT_CONVENTION},
            # 封闭三方案白名单（选型文档 §1.1）：未知方案 key 保存被拒。
            field_whitelist={"active_convention": CONVENTION_KEYS},
        ),
        DomainSpec(
            domain="reference_standards",
            label="引用标准目录",
            group="文档资源",
            downstream="文档编排服务（参考资料章节撰稿）",
            # 空字段白名单是有意的：本域的数据是数组（自有条目、停用清单），只能经专用端点
            # 写入并逐条校验。通用 PUT /config/{domain} 只收平铺标量，一个字段都不接受。
            fields=(),
            secret_fields=(),
            env_defaults=lambda s: {},
        ),
    )
}


def _loads(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


# ===========================================================================
# 模型服务 provider 列表（T20260720-model-provider-registry）
# ---------------------------------------------------------------------------
# 存储零迁移：providers 数组与启用指针落 model_service 域**既有那一行** ConfigEntry 的
# payload JSON；逐 provider 密钥落同一行的 secrets JSON，键名 `api_key:<provider_id>`。
# 既有平铺字段（base_url/model/…）与既有密钥键 `api_key` 一律保留不动、不搬数据——
# 库里没有 providers 数组时，读侧把平铺字段投影成一个 id 为 default 的启用 provider，
# 老配置照旧生效（存量兼容全靠读侧归一，不靠数据迁移）。
# ===========================================================================

DEFAULT_PROVIDER_ID = "default"
# provider 标识的字符集（它会成为密钥字典的键）。
_PROVIDER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
_LEGACY_SECRET_KEY = "api_key"
_PROVIDER_SECRET_PREFIX = "api_key:"


def _provider_secret_key(provider_id: str) -> str:
    return f"{_PROVIDER_SECRET_PREFIX}{provider_id}"


def provider_api_key(secrets: dict[str, Any], provider_id: str) -> str | None:
    """取某个 provider 的密钥。

    id 为 default 的那个 provider 回落既有 `api_key` 键——存量单表单时代保存的密钥就在那儿，
    升级后不搬不改仍然可用；一旦用户为 default 重新输入过密钥（写入 `api_key:default`），
    以新键为准。
    """
    value = secrets.get(_provider_secret_key(provider_id))
    if not value and provider_id == DEFAULT_PROVIDER_ID:
        value = secrets.get(_LEGACY_SECRET_KEY)
    return str(value) if value else None


def _coerce_provider(raw: dict[str, Any], base: Settings) -> dict[str, Any]:
    """单条 provider 记录归一：缺字段补默认，类型越界回落 llama.cpp（读侧求稳）。"""
    provider_type = str(raw.get("provider_type") or "")
    if provider_type not in PROVIDER_TYPE_KEYS:
        provider_type = DEFAULT_PROVIDER_TYPE
    try:
        timeout = float(raw.get("timeout_seconds") or base.llm_timeout)
    except (TypeError, ValueError):
        timeout = float(base.llm_timeout)
    def _int(key: str, fallback: int) -> int:
        try:
            return int(raw.get(key) or fallback)
        except (TypeError, ValueError):
            return fallback
    profile = raw.get("capability_profile")
    thinking_enabled = raw.get("thinking_enabled")
    return {
        "id": str(raw.get("id") or DEFAULT_PROVIDER_ID),
        "name": str(raw.get("name") or ""),
        "provider_type": provider_type,
        "base_url": str(raw.get("base_url") or ""),
        "model": str(raw.get("model") or ""),
        "timeout_seconds": timeout,
        "max_retries": _int("max_retries", 3),
        "concurrency_limit": _int("concurrency_limit", 5),
        # 能力探测档案（T20260724）：探完点「应用」才写进来，没探过就是空字典＝按先验走。
        # 形状不校验、越界不修正：唯一的解析口在 app/adapters/llm.py 的 parse_capability_profile，
        # 它对坏形状一律回落「没探明」，这里再校一遍只会两处口径分叉。
        "capability_profile": profile if isinstance(profile, dict) else {},
        # 思考模式开关（每个模型服务各自设，默认关）。缺席时回落 env 的 LLM_DISABLE_THINKING，
        # 即从未设置过的配置行为与这个开关上线前完全一致。
        # 默认关的理由是实测：思考模型开着思考跑，重流程慢 20–50 倍直至超时，且思考段可能吃光
        # 输出预算导致正文为空、任务判失败（见 chat_extension_fields 与提案第一部分结论 1）。
        "thinking_enabled": (
            thinking_enabled if isinstance(thinking_enabled, bool) else not base.llm_disable_thinking
        ),
    }


def normalize_providers(payload: dict[str, Any], base: Settings) -> tuple[list[dict[str, Any]], str]:
    """payload → (provider 列表, 启用 provider 的 id)。

    没有 providers 数组（存量单表单配置）→ 用平铺字段投影出唯一的 default provider，
    其空缺字段回落 env，效果与升级前完全一致。
    """
    rows = payload.get("providers")
    if isinstance(rows, list) and rows:
        providers = [_coerce_provider(row, base) for row in rows if isinstance(row, dict)]
    else:
        providers = []
    if not providers:
        providers = [
            _coerce_provider(
                {
                    "id": DEFAULT_PROVIDER_ID,
                    "name": payload.get("service_name") or "默认模型服务",
                    "provider_type": payload.get("provider_type") or base.llm_provider_type,
                    "base_url": payload.get("base_url") or base.llm_base_url or "",
                    "model": payload.get("model") or base.llm_model,
                    "timeout_seconds": payload.get("timeout_seconds") or base.llm_timeout,
                    "max_retries": payload.get("max_retries"),
                    "concurrency_limit": payload.get("concurrency_limit"),
                },
                base,
            )
        ]
    known = {p["id"] for p in providers}
    active_id = str(payload.get("active_provider_id") or "")
    if active_id not in known:
        active_id = providers[0]["id"]
    return providers, active_id


# ---------------------------------------------------------------------------
# 两级连通测试的结果分级（封闭集）
# ---------------------------------------------------------------------------
# 后端只回稳定结果码，白话文案由前端映射：走查阶段改措辞不必动后端，且文案可单测。
OUTCOME_OK = "ok"
OUTCOME_UNREACHABLE = "unreachable"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_AUTH_FAILED = "auth_failed"
OUTCOME_MODEL_MISSING = "model_missing"
OUTCOME_BAD_RESPONSE = "bad_response"


def _transport_outcome(exc: Exception) -> str:
    """网络层异常 → 结果码。超时先判：httpx 的超时异常本身也是传输异常的子类。"""
    if isinstance(exc, httpx.TimeoutException):
        return OUTCOME_TIMEOUT
    if isinstance(exc, httpx.InvalidURL):
        # 地址本身写坏了（端口含字母、坏 IPv6 等）：httpx 在构造请求时就抛，尚未发出网络 I/O。
        # 对使用者而言就是「这个地址连不上」，归入封闭集里的 unreachable，别逃成未捕获 500。
        return OUTCOME_UNREACHABLE
    if isinstance(exc, httpx.TransportError):
        return OUTCOME_UNREACHABLE
    return OUTCOME_BAD_RESPONSE


def _probe_reachability(
    base_url: str, command: ModelConnectionTestCommand, headers: dict[str, str] | None
) -> ModelConnectionTestResult:
    """第一级「可达」：带鉴权 GET {base_url}/models，并核对配置的模型是否在列表里。"""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        response = httpx.get(
            base_url.rstrip("/") + "/models", headers=headers, timeout=command.timeout_seconds
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        return ModelConnectionTestResult(
            ok=False, latency_ms=elapsed(), error_code=f"http_{status}", level="reachability",
            outcome=OUTCOME_AUTH_FAILED if status in (401, 403) else OUTCOME_BAD_RESPONSE,
        )
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        # InvalidURL 不是 HTTPError/ValueError 的子类，必须显式列出，否则畸形地址逃成未捕获 500。
        return ModelConnectionTestResult(
            ok=False, latency_ms=elapsed(), error_code=type(exc).__name__, level="reachability",
            outcome=_transport_outcome(exc),
        )

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        # OpenAI 兼容面的 /models 必须回 {"data": [...]}；回别的形状说明地址指错了地方。
        return ModelConnectionTestResult(
            ok=False, latency_ms=elapsed(), error_code="model_list_shape", level="reachability",
            outcome=OUTCOME_BAD_RESPONSE,
        )
    ids = [str(row.get("id")) for row in data if isinstance(row, dict) and row.get("id")]
    wanted = (command.model or "").strip()
    listed = (wanted in ids) if wanted else None
    return ModelConnectionTestResult(
        ok=listed is not False,
        latency_ms=elapsed(),
        model_count=len(data),
        error_code=None if listed is not False else "model_not_listed",
        level="reachability",
        outcome=OUTCOME_OK if listed is not False else OUTCOME_MODEL_MISSING,
        model_listed=listed,
        models=ids[:20],
    )


def _probe_generation(
    base_url: str,
    command: ModelConnectionTestCommand,
    provider_type: str,
    headers: dict[str, str] | None,
) -> ModelConnectionTestResult:
    """第二级「正确响应」：发一次最小生成请求，验证端点真的回得出内容。

    请求体经 `minimal_chat_payload` 构造——「哪些扩展字段发给哪类 provider」与正式调用同一来源，
    因此这一级也顺带验证了扩展字段没有错发给不认它的端点。
    """
    model = (command.model or "").strip()
    if not model:
        raise InvalidInput("第二级测试需要模型标识")
    payload = minimal_chat_payload(model, provider_type)
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        response = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers=headers,
            json=payload,
            timeout=command.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            outcome = OUTCOME_AUTH_FAILED
        elif status == 404:
            # ollama / vLLM 对未知模型名都回 404（ollama 尤其常见于模型名漏了 :标签）。
            outcome = OUTCOME_MODEL_MISSING
        else:
            outcome = OUTCOME_BAD_RESPONSE
        return ModelConnectionTestResult(
            ok=False, latency_ms=elapsed(), error_code=f"http_{status}", level="generation",
            outcome=outcome,
        )
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        # InvalidURL 不是 HTTPError/ValueError 的子类，必须显式列出，否则畸形地址逃成未捕获 500。
        return ModelConnectionTestResult(
            ok=False, latency_ms=elapsed(), error_code=type(exc).__name__, level="generation",
            outcome=_transport_outcome(exc),
        )

    content: object = None
    if isinstance(body, dict):
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
    text = content if isinstance(content, str) else ""
    if not text.strip():
        # 200 但没回出内容（形状不对、或推理段吃光了 token）：对使用者同样是不可用。
        return ModelConnectionTestResult(
            ok=False, latency_ms=elapsed(), error_code="empty_content", level="generation",
            outcome=OUTCOME_BAD_RESPONSE,
        )
    return ModelConnectionTestResult(
        ok=True, latency_ms=elapsed(), level="generation", outcome=OUTCOME_OK,
        reply_length=len(text.strip()),
    )


# ===========================================================================
# 能力探测 C3–C6（T20260724-capability-probe-panel）
# ---------------------------------------------------------------------------
# 三原则（提案 3.2，硬约束）：
# 1) **验产物不验状态码**——vLLM/ollama 对不认识的字段静默收下回 200，「发了」不等于「生效了」，
#    所以 C3 看回复里思考段的有无、C4 看返回内容是否真符合给定 schema，都不看状态码；
# 2) **差分探测**——C3 用「加字段 vs 不加字段」两条对照请求，把变化归因到那个字段；
# 3) **廉价可控**——每项 1–2 条短请求、小 max_tokens、幂等无状态、逐项超时预算。
#
# 逐项独立：任一项超时或出错只把该项记成「未探明」，绝不中断整张清单（评审意见 1）。
# ===========================================================================

# C3 探针请求的输出上限：只要够回一个短答案。思考模型会把它烧光，那本身就是有思考段的旁证
# （「输出把预算烧光却看不到思考段」这条判据就建立在这个小预算上，不能随手抬高）。
_PROBE_MAX_TOKENS = 64
# C4／C6 探针请求的输出上限：它们的判据是**回复正文**（要是一段完整的 JSON），而这两条请求
# 完全可能仍带着思考段——关不掉思考的端点、以及先验就是「不发关思考字段」的 vLLM 与通用兼容
# 端点都是如此。给 64 个 token，思考段一起头正文就没了，产物必然不达标，探针会得出「这个端点
# 不支持 JSON 格式输出」的错误结论。给到 512：够一段思考开场加上探测 schema 那两个字段的答案，
# 又不至于让一轮探测慢到影响体验。
_PROBE_ANSWER_MAX_TOKENS = 512
# 单条探针请求的超时上限（受用户给的总超时钳制）：思考模型的基线请求可能跑数十秒，
# 不设上限就会把整张清单拖死。
_PROBE_REQUEST_TIMEOUT = 20.0
# 元数据端点（/models、/props、/api/show）只是读一行配置，不该等太久。
_PROBE_METADATA_TIMEOUT = 8.0
# C3 全项预算：基线 + 至多两个候选字段。超出即停止试探、按「未探明」记录。
_C3_TOTAL_BUDGET = 60.0
# C3 的问题要短、要有确定答案，思考模型对它照样会展开思考。
_C3_PROMPT = "1 加 1 等于几？只回答数字。"
# C4 的探测 schema：两个不同类型的必填字段——端点若只是「看起来回了 JSON」而没真强制约束，
# 很难恰好把两个字段的类型都蒙对。
_C4_PROMPT = "请回答：地球是圆的吗？按要求的 JSON 结构作答。"
_C4_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "confident": {"type": "boolean"}},
    "required": ["answer", "confident"],
    "additionalProperties": False,
}
# C6 的杜撰字段名：带本仓前缀，避免与任何引擎的真实参数重名。
_C6_FIELD = "x_req_doc_capability_probe"


@dataclass(frozen=True)
class CapabilityFinding:
    """单项能力的探测结论。只承载稳定代码与实测数值，白话文案由前端映射。"""

    key: str
    state: str
    mode: str = ""
    # C3：这个端点/模型会不会思考（None=没探明）。与 state 是两件事——state 说的是「能不能关」。
    available: bool | None = None
    tier: str = ""
    tokens: int = 0
    source: str = ""
    note_code: str | None = None
    outcome: str | None = None
    latency_ms: int | None = None
    # 数值事实（基线与候选各自的延迟、输出 token 数、试过哪些字段）：界面按需展开，
    # 也是「结论怎么来的」的证据。绝不放响应正文（硬规则 8）。
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class _ChatSample:
    """一条探针请求的产物切片：够判结论即可，绝不带出响应正文。"""

    ok: bool
    status: int | None = None
    latency_ms: int = 0
    content: str = ""
    has_thinking: bool = False
    completion_tokens: int = 0
    error_code: str | None = None


def _service_root(base_url: str) -> str:
    """OpenAI 兼容面地址 → 服务根地址。

    llama.cpp 的 `/props` 与 ollama 的 `/api/show` 都挂在服务根路径上、不在 `/v1` 下，
    而本仓配置里存的 base_url 恒含 `/v1`，故取元数据前要把它摘掉。
    """
    root = base_url.rstrip("/")
    return root[: -len("/v1")].rstrip("/") if root.endswith("/v1") else root


def _has_thinking_segment(message: dict[str, Any], content: str) -> bool:
    """回复里有没有思考段——C3 的主判据（验产物）。

    载体三家不同：vLLM（起了 --reasoning-parser 时）走 `reasoning_content`，ollama 走
    `reasoning`，llama.cpp 把 `<think>…</think>` 内联在正文里。三种都要认得出，任一命中即算有。
    """
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return "<think>" in content


def _sample_chat(
    base_url: str, headers: dict[str, str] | None, payload: dict[str, Any], timeout: float
) -> _ChatSample:
    """发一条探针请求并切出判结论要用的产物特征。异常一律收成 ok=False，不抛。"""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        response = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers=headers, json=payload, timeout=timeout,
        )
        status = response.status_code
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        return _ChatSample(ok=False, status=exc.response.status_code, latency_ms=elapsed(),
                           error_code=f"http_{exc.response.status_code}")
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        return _ChatSample(ok=False, latency_ms=elapsed(), error_code=type(exc).__name__)

    message: dict[str, Any] = {}
    if isinstance(body, dict):
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            candidate = choices[0].get("message")
            if isinstance(candidate, dict):
                message = candidate
    content = message.get("content")
    content = content if isinstance(content, str) else ""
    usage = body.get("usage") if isinstance(body, dict) else None
    try:
        completion_tokens = int((usage or {}).get("completion_tokens") or 0)
    except (TypeError, ValueError):
        completion_tokens = 0
    return _ChatSample(
        ok=True, status=status, latency_ms=elapsed(), content=content,
        has_thinking=_has_thinking_segment(message, content),
        completion_tokens=completion_tokens,
    )


def _probe_conclusive(sample: _ChatSample) -> bool:
    """这条试探请求到底问出答案了没有——「问出了负面答案」与「压根没问成」的分界线。

    回 200 是答案（产物达不达标另说）；端点以 4xx 明确拒绝也是答案：它不认这个参数或这一档，
    这是正面证据。连接被拒、读超时、5xx 都不是答案，只能记「没探明」——把它们当成负面结论，
    就会让一次网络抖动把「关不掉思考」「不支持 JSON 格式输出」这类会改变请求构造的结论固化
    进配置，而且没有自动恢复路径。
    """
    return sample.ok or (sample.status is not None and 400 <= sample.status < 500)


def _probe_payload(
    model: str,
    prompt: str,
    extra: dict[str, Any] | None = None,
    max_tokens: int = _PROBE_MAX_TOKENS,
) -> dict[str, Any]:
    """探针请求体：短提示词、小输出、无副作用（探测三原则之三）。"""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if extra:
        payload.update(extra)
    return payload


@dataclass(frozen=True)
class _EndpointMetadata:
    """端点**自报**的元数据，一次取回、C3 与 C5 共用。

    为什么要读它：光靠发请求看产物，分不清「模型不会思考」与「模型会思考但服务端把它关了」。
    116 的 llama.cpp 生产端点就是后者——`/props` 里 `chat_template_caps.supports_preserve_reasoning`
    为 true（模板支持思考）而 `params.reasoning_format` 为 `none`（服务端 -rea off 全局关掉）。
    只看产物会得出「这个模型不具备思考能力」的错误结论，进而给出「换个思考模型」这种错误建议。
    """

    context_tokens: int = 0
    context_source: str = ""
    context_error: str | None = None
    # 元数据是否声明这个模型具备思考能力（None=该端点不提供这类声明，判断不了）。
    thinking_declared: bool | None = None
    # 服务端是否已全局关闭思考输出（llama.cpp 的 reasoning_format=none）。
    thinking_server_disabled: bool = False


def _fetch_endpoint_metadata(
    base_url: str, model: str, provider_type: str, headers: dict[str, str] | None
) -> _EndpointMetadata:
    """按 provider 类型读端点元数据：有效上下文 + 思考能力声明。

    出处各不相同：llama.cpp 在服务根的 `/props`（不在 /v1 下）；ollama 在原生面的 `/api/show`；
    vLLM 与通用兼容端点在 `/models` 的模型条目上（只有上下文，不声明思考能力）。
    """
    root = _service_root(base_url)
    if provider_type == PROVIDER_LLAMA_CPP:
        try:
            response = httpx.get(root + "/props", headers=headers, timeout=_PROBE_METADATA_TIMEOUT)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            return _EndpointMetadata(context_error=type(exc).__name__)
        body = body if isinstance(body, dict) else {}
        generation = body.get("default_generation_settings")
        generation = generation if isinstance(generation, dict) else {}
        try:
            n_ctx = int(generation.get("n_ctx") or 0)
        except (TypeError, ValueError):
            n_ctx = 0
        caps = body.get("chat_template_caps")
        declared: bool | None = None
        if isinstance(caps, dict) and "supports_preserve_reasoning" in caps:
            declared = bool(caps.get("supports_preserve_reasoning"))
        params = generation.get("params")
        params = params if isinstance(params, dict) else {}
        # reasoning_format=none 即服务端不解析/不输出思考段（启动参数 -rea off）。
        server_disabled = str(params.get("reasoning_format") or "") == "none"
        return _EndpointMetadata(
            context_tokens=max(n_ctx, 0), context_source="props.n_ctx" if n_ctx > 0 else "",
            thinking_declared=declared, thinking_server_disabled=server_disabled,
        )

    if provider_type == PROVIDER_OLLAMA:
        try:
            # 新版 ollama 用 model 字段、老版用 name；ollama 对多余字段一律静默忽略（R1 已销账），
            # 两个都带上即可兼容两代，不必先探版本。
            response = httpx.post(root + "/api/show", headers=headers,
                                  json={"model": model, "name": model},
                                  timeout=_PROBE_METADATA_TIMEOUT)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            return _EndpointMetadata(context_error=type(exc).__name__)
        body = body if isinstance(body, dict) else {}
        info = body.get("model_info")
        tokens = 0
        if isinstance(info, dict):
            for key, value in info.items():
                # 键名带模型架构前缀（如 qwen3.context_length），架构名随模型变，只认后缀。
                if str(key).endswith(".context_length"):
                    try:
                        tokens = int(value)
                    except (TypeError, ValueError):
                        tokens = 0
                    break
        caps = body.get("capabilities")
        # ollama 在 /api/show 里直接列出模型能力（completion/tools/thinking/…）。
        # 待真实端点复核：本条按 ollama 官方接口文档实现，116 的 ollama 当时未运行（见桩件 R7）。
        declared = ("thinking" in [str(c) for c in caps]) if isinstance(caps, list) else None
        return _EndpointMetadata(
            context_tokens=max(tokens, 0),
            context_source="api_show.context_length" if tokens > 0 else "",
            thinking_declared=declared,
        )

    try:
        response = httpx.get(base_url.rstrip("/") + "/models", headers=headers,
                             timeout=_PROBE_METADATA_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        return _EndpointMetadata(context_error=type(exc).__name__)
    rows = (body or {}).get("data") if isinstance(body, dict) else None
    rows = rows if isinstance(rows, list) else []
    for row in rows:
        if not isinstance(row, dict) or (model and row.get("id") != model):
            continue
        try:
            tokens = int(row.get("max_model_len") or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens > 0:
            return _EndpointMetadata(context_tokens=tokens, context_source="models.max_model_len")
        break
    return _EndpointMetadata()


def _context_finding(meta: _EndpointMetadata, provider_type: str) -> CapabilityFinding:
    """C5 有效上下文：用已取回的元数据出结论，读不到就落「未探明」。

    ollama 报的是**模型自身**的上限，而兼容层实际生效的窗口更小（实测 27B 模型报 262144、
    实际落 32768），所以只作参考值呈现、不拿来卡请求——用猜测值截断用户请求是探测三原则
    明令禁止的（评审意见 2）。
    """
    if meta.context_tokens <= 0:
        return CapabilityFinding(key=CAP_CONTEXT, state=CAP_STATE_UNKNOWN,
                                 outcome=meta.context_error, detail={})
    if provider_type == PROVIDER_OLLAMA:
        return CapabilityFinding(
            key=CAP_CONTEXT, state=CAP_STATE_DEGRADED, tokens=meta.context_tokens,
            source=meta.context_source, note_code=CAP_NOTE_OLLAMA_MODEL_LIMIT_ONLY,
        )
    return CapabilityFinding(key=CAP_CONTEXT, state=CAP_STATE_SUPPORTED,
                             tokens=meta.context_tokens, source=meta.context_source)


def _probe_thinking(
    base_url: str, model: str, provider_type: str, headers: dict[str, str] | None,
    timeout: float, meta: _EndpointMetadata,
) -> CapabilityFinding:
    """C3 思考能力：这个模型会不会思考、当前在不在思考、能不能关掉。

    两个信源合起来才说得清：
    - **元数据声明**（`meta.thinking_declared`）：端点自己说这个模型支不支持思考。
    - **实测产物**：发一条不带任何关思考字段的基线请求，看回复里有没有思考段。

    为什么不能只看实测：没看到思考段有三种可能——模型确实不会思考、模型会思考但**服务端**
    全局关掉了（llama.cpp 的 `-rea off`）、端点不把思考段单独回出来。只看产物会把后两种误判成
    第一种，然后给出「换个思考模型」这类错误建议。116 的生产端点正是第二种。

    基线确实带思考段时，再按 provider 类型的候选顺序逐个试，哪个字段让思考段消失就是哪个
    （差分探测）。延迟与输出 token 数只作佐证记进 detail，不参与判定——单看延迟会把网络抖动
    当成关思考成功。
    """
    deadline = time.monotonic() + _C3_TOTAL_BUDGET
    per_request = min(timeout, _PROBE_REQUEST_TIMEOUT)
    baseline = _sample_chat(base_url, headers, _probe_payload(model, _C3_PROMPT), per_request)
    detail: dict[str, Any] = {
        "baseline_latency_ms": baseline.latency_ms,
        "baseline_completion_tokens": baseline.completion_tokens,
        "baseline_has_thinking": baseline.has_thinking,
        "declared": meta.thinking_declared,
        "server_disabled": meta.thinking_server_disabled,
        "tried": [],
    }
    if not baseline.ok:
        # 基线都没回出来（超时/报错）：关不关得掉判不了，但元数据的声明仍然算数。
        return CapabilityFinding(key=CAP_THINKING, state=CAP_STATE_UNKNOWN,
                                 available=meta.thinking_declared,
                                 outcome=baseline.error_code, detail=detail)

    if not baseline.has_thinking:
        if baseline.completion_tokens >= _PROBE_MAX_TOKENS * 0.9:
            # 没看到思考段，输出却把预算烧光了：很可能在思考但不把思考段单独回出来。
            return CapabilityFinding(
                key=CAP_THINKING, state=CAP_STATE_UNKNOWN, available=meta.thinking_declared,
                note_code=CAP_NOTE_THINKING_SEGMENT_HIDDEN, detail=detail,
            )
        # 当前没有思考段。是「不会思考」「被服务端关了」还是「这次没展开」，要两个事实一起看：
        # 端点声明支持思考、且端点自报服务端已把思考输出全局关掉（llama.cpp 的 reasoning_format
        # =none），才是「服务端关掉了」——那句「去改 -rea off」的建议是 llama.cpp 专属的，只凭
        # 声明就给出来，会让 Ollama 用户照着一条不存在的路去改配置。
        # 声明支持但端点没报关闭标志：说不清是问题太简单没触发思考，还是服务端关了，另给一个码。
        if meta.thinking_declared is True:
            note = (
                CAP_NOTE_THINKING_DISABLED_ON_SERVER
                if meta.thinking_server_disabled
                else CAP_NOTE_THINKING_DECLARED_NOT_OBSERVED
            )
        else:
            note = None
        return CapabilityFinding(
            key=CAP_THINKING, state=CAP_STATE_SUPPORTED, mode=THINKING_OFF_NOT_NEEDED,
            available=meta.thinking_declared, note_code=note, detail=detail,
        )

    # 实测有思考段：无论元数据怎么声明，这个模型确实会思考（实测优先于声明）。
    candidates = THINKING_OFF_CANDIDATES.get(provider_type, THINKING_OFF_CANDIDATES[DEFAULT_PROVIDER_TYPE])
    # 「候选请求没问成」与「问成了但思考照旧」是两件事，必须分开记（见下方 return）。
    last_error: str | None = None
    all_conclusive = True
    for candidate in candidates:
        if time.monotonic() >= deadline:
            detail["budget_exhausted"] = True
            return CapabilityFinding(key=CAP_THINKING, state=CAP_STATE_UNKNOWN,
                                     available=True, detail=detail)
        sample = _sample_chat(
            base_url, headers,
            _probe_payload(model, _C3_PROMPT, thinking_off_fields(candidate)), per_request,
        )
        detail["tried"].append({
            "mode": candidate, "ok": sample.ok, "latency_ms": sample.latency_ms,
            "completion_tokens": sample.completion_tokens, "has_thinking": sample.has_thinking,
        })
        if not _probe_conclusive(sample):
            all_conclusive = False
            last_error = sample.error_code
        if sample.ok and not sample.has_thinking:
            return CapabilityFinding(key=CAP_THINKING, state=CAP_STATE_SUPPORTED,
                                     mode=candidate, available=True,
                                     latency_ms=sample.latency_ms, detail=detail)
    if not all_conclusive:
        # 有候选请求没问成（端点繁忙、连接被重置、超时）：这是「没探明」，不是「关不掉」。
        # 落成「关不掉」的后果很重——适配层此后一个关思考字段都不发，这个本来发一个字段就能关掉
        # 思考的端点会从此带着思考跑，正是本机制立项要消灭的那个故障。
        # 判据取「每个候选都问出了答案」而不是「至少问成一条」：没问成的那个候选压根没被试过，
        # 「试过的参数都不认」这句结论对它不成立。
        return CapabilityFinding(key=CAP_THINKING, state=CAP_STATE_UNKNOWN,
                                 available=True, outcome=last_error, detail=detail)
    if provider_type == PROVIDER_VLLM:
        # vLLM 收下了字段却照旧思考：几乎总是服务端没起 --reasoning-parser。给出这句可执行的提示，
        # 并且**不下发**该字段——发了也不生效，只会掩盖真实情况。
        return CapabilityFinding(key=CAP_THINKING, state=CAP_STATE_DEGRADED, available=True,
                                 note_code=CAP_NOTE_VLLM_NEEDS_REASONING_PARSER, detail=detail)
    return CapabilityFinding(key=CAP_THINKING, state=CAP_STATE_UNSUPPORTED,
                             available=True, detail=detail)


def _matches_c4_schema(content: str) -> bool:
    """C4 的产物校验：返回内容必须**本身**就是符合探测 schema 的 JSON 对象。

    不做「从杂字里捞第一个 {…}」的容错——那是正式调用为了不浪费一次生成才做的防御，
    放在这里会把「模型自己写了段 JSON」误判成「端点强制约束生效」，正好是要识破的假成功。
    """
    try:
        data = json.loads(content.strip())
    except ValueError:
        return False
    return (
        isinstance(data, dict)
        and isinstance(data.get("answer"), str)
        and isinstance(data.get("confident"), bool)
    )


def _thinking_off_extra(provider_type: str, thinking: CapabilityFinding | None) -> dict[str, Any]:
    """C4／C6 的试探请求要带的关思考字段，口径与正式调用完全一致。

    为什么这两项也要关思考：它们的判据是回复正文，而思考段会把输出预算吃光——C4 因此会把一个
    完全支持 JSON 格式输出的端点判成「不支持」，用户一「应用」，这条服务的所有 AI 调用就永久
    降到纯提示词档且没有自动恢复路径。

    取值来源：C3 已探明有效的关思考方式就用它（所以 C3 必须排在这两项之前跑），没探明才回落
    provider 类型的先验。这一层判断不自己写一份，直接复用适配层的 `chat_extension_fields`——
    探针发出去的请求体与正式调用的请求体口径就此保持同一个来源。
    """
    profile = _findings_to_profile([thinking], "") if thinking is not None else None
    return chat_extension_fields(provider_type, True, profile)


def _probe_structured(
    base_url: str, model: str, headers: dict[str, str] | None, timeout: float,
    thinking_off: dict[str, Any] | None = None,
) -> CapabilityFinding:
    """C4 结构化输出：从高到低逐档试，验产物是否真符合 schema，定在**实测强制生效**的最高档。

    「200 但内容不符 schema」= 端点收下了 response_format 却没强制约束（vLLM/ollama 静默接受的
    典型表现）。只看状态码的运行时降级链识不破它，这里验产物就识得破。

    请求带上 `thinking_off`（关思考字段，由 C3 结论或类型先验给出）并给足输出预算：不然思考段
    会把预算吃光、正文为空，判据就成了「这个端点不支持 JSON 格式输出」的假结论。
    """
    per_request = min(timeout, _PROBE_REQUEST_TIMEOUT)
    detail: dict[str, Any] = {"tried": []}
    tiers = (
        (STRUCTURED_TIER_JSON_SCHEMA, {"type": "json_schema", "json_schema": {
            "name": "capability_probe", "schema": _C4_SCHEMA, "strict": True}}),
        (STRUCTURED_TIER_JSON_OBJECT, {"type": "json_object"}),
    )
    last_error: str | None = None
    all_conclusive = True
    for tier, response_format in tiers:
        sample = _sample_chat(
            base_url, headers,
            _probe_payload(model, _C4_PROMPT,
                           {"response_format": response_format, **(thinking_off or {})},
                           max_tokens=_PROBE_ANSWER_MAX_TOKENS),
            per_request,
        )
        conforms = sample.ok and _matches_c4_schema(sample.content)
        if not _probe_conclusive(sample):
            all_conclusive = False
            last_error = sample.error_code
        detail["tried"].append({
            "tier": tier, "ok": sample.ok, "status": sample.status,
            "latency_ms": sample.latency_ms, "conforms": conforms,
        })
        if conforms:
            return CapabilityFinding(
                key=CAP_STRUCTURED,
                # json_schema 生效=完整可靠；只到 json_object=有条件（结构靠提示词兜，已降一档）。
                state=CAP_STATE_SUPPORTED if tier == STRUCTURED_TIER_JSON_SCHEMA else CAP_STATE_DEGRADED,
                tier=tier, latency_ms=sample.latency_ms, detail=detail,
            )
    if not all_conclusive:
        # 有一档的请求没问成（端点重启、网关抖动）：那一档到底行不行没试出来，只能记「没探明」，
        # 由适配层回落既有的运行时降级链。记「不支持」会把这条服务永久钉在纯提示词档上。
        # 端点以 4xx 明确拒绝那一档不算「没问成」——那是「它不支持这一档」的正面证据。
        return CapabilityFinding(key=CAP_STRUCTURED, state=CAP_STATE_UNKNOWN,
                                 outcome=last_error, detail=detail)
    return CapabilityFinding(key=CAP_STRUCTURED, state=CAP_STATE_UNSUPPORTED,
                             tier=STRUCTURED_TIER_PROMPT_ONLY, detail=detail)


def _probe_unknown_fields(
    base_url: str, model: str, headers: dict[str, str] | None, timeout: float,
    thinking_off: dict[str, Any] | None = None,
) -> CapabilityFinding:
    """C6 未识别字段是否静默接受：发一个杜撰字段，看端点收不收。

    收下回 200（vLLM/ollama 的实测行为）→ 「返回 200」不能当作「字段生效」，C3/C4 的结论必须
    以产物为准，界面据此提示；明确回 4xx → 这个端点会拒绝不认识的参数，状态码本身就有信息量。

    这一项只看状态码，思考段不影响结论，但请求同样带上关思考字段：一是省掉一次思考的等待，
    二是日后有人给它加产物判据时不必再想起这件事（C4 就是这么栽的）。
    """
    per_request = min(timeout, _PROBE_REQUEST_TIMEOUT)
    sample = _sample_chat(
        base_url, headers,
        _probe_payload(model, "请回复 OK 两个字。",
                       {_C6_FIELD: "capability-probe", **(thinking_off or {})}),
        per_request,
    )
    detail = {"status": sample.status, "latency_ms": sample.latency_ms}
    if sample.ok:
        return CapabilityFinding(key=CAP_UNKNOWN_FIELDS, state=CAP_STATE_DEGRADED,
                                 latency_ms=sample.latency_ms, detail=detail)
    if sample.status is not None and 400 <= sample.status < 500:
        return CapabilityFinding(key=CAP_UNKNOWN_FIELDS, state=CAP_STATE_SUPPORTED,
                                 latency_ms=sample.latency_ms, detail=detail)
    return CapabilityFinding(key=CAP_UNKNOWN_FIELDS, state=CAP_STATE_UNKNOWN,
                             outcome=sample.error_code, detail=detail)


def _findings_to_profile(findings: list[CapabilityFinding], probed_at: str) -> CapabilityProfile:
    """探测结论 → 可持久化的能力档案（适配层据此构造请求）。"""
    by_key = {f.key: f for f in findings}
    thinking = by_key.get(CAP_THINKING)
    structured = by_key.get(CAP_STRUCTURED)
    context = by_key.get(CAP_CONTEXT)
    unknown = by_key.get(CAP_UNKNOWN_FIELDS)
    notes = tuple(f.note_code for f in findings if f.note_code)
    return CapabilityProfile(
        thinking_off_state=thinking.state if thinking else CAP_STATE_UNKNOWN,
        thinking_off_mode=thinking.mode if thinking else "",
        thinking_available=thinking.available if thinking else None,
        structured_state=structured.state if structured else CAP_STATE_UNKNOWN,
        structured_tier=structured.tier if structured else "",
        context_state=context.state if context else CAP_STATE_UNKNOWN,
        context_tokens=context.tokens if context else 0,
        context_source=context.source if context else "",
        unknown_fields_state=unknown.state if unknown else CAP_STATE_UNKNOWN,
        unknown_fields_silently_accepted=bool(unknown and unknown.state == CAP_STATE_DEGRADED),
        notes=notes,
        probed_at=probed_at,
    )


class ConfigRegistryService:
    """配置域读写 + 模型服务 provider 管理 + 两级连通测试 + 逐能力探测。响应绝不含密钥明文。"""

    def __init__(self, session: Session, base_settings: Settings | None = None) -> None:
        self._session = session
        self._settings = base_settings or env_settings

    # ---- 读 ----

    def _entry(self, domain: str) -> ConfigEntry | None:
        return self._session.scalar(select(ConfigEntry).where(ConfigEntry.domain == domain))

    def _spec(self, domain: str) -> DomainSpec:
        spec = DOMAIN_SPECS.get(domain)
        if spec is None:
            raise NotFound(f"未知配置域：{domain}")
        return spec

    def list_domain_status(self) -> list[ConfigDomainStatusRead]:
        result: list[ConfigDomainStatusRead] = []
        for spec in DOMAIN_SPECS.values():
            entry = self._entry(spec.domain)
            result.append(
                ConfigDomainStatusRead(
                    domain=spec.domain,
                    label=spec.label,
                    group=spec.group,
                    downstream=spec.downstream,
                    configured=entry is not None,
                    source="saved" if entry is not None else "env",
                    updated_at=entry.updated_at.isoformat() if entry is not None and entry.updated_at else None,
                    updated_by=entry.updated_by if entry is not None else None,
                )
            )
        return result

    def get_domain(self, domain: str) -> ConfigDomainRead:
        spec = self._spec(domain)
        entry = self._entry(domain)
        saved = _loads(entry.payload) if entry is not None else {}
        secrets = _loads(entry.secrets) if entry is not None else {}
        defaults = spec.env_defaults(self._settings)
        fields = [
            ConfigFieldRead(
                key=key,
                value=saved.get(key, defaults.get(key)),
                source="saved" if key in saved else "env",
            )
            for key in spec.fields
        ]
        secret_reads = [
            ConfigSecretRead(
                key=key,
                set=bool(secrets.get(key)),
                placeholder=SECRET_PLACEHOLDER if secrets.get(key) else "",
            )
            for key in spec.secret_fields
        ]
        return ConfigDomainRead(
            domain=spec.domain,
            label=spec.label,
            group=spec.group,
            downstream=spec.downstream,
            source="saved" if entry is not None else "env",
            updated_at=entry.updated_at.isoformat() if entry is not None and entry.updated_at else None,
            updated_by=entry.updated_by if entry is not None else None,
            fields=fields,
            secrets=secret_reads,
        )

    # ---- 写（保存经审计留痕） ----

    def save_domain(self, domain: str, command: ConfigSaveCommand) -> ConfigSaveResult:
        spec = self._spec(domain)
        if not command.operator_ref.strip():
            raise InvalidInput("operator_ref 不能为空（审计留痕需要操作者）")
        unknown = [key for key in command.values if key not in spec.fields]
        unknown += [key for key in command.secrets if key not in spec.secret_fields]
        if unknown:
            raise InvalidInput(f"配置域 {domain} 不接受字段：{', '.join(sorted(unknown))}")
        # 封闭枚举字段的取值白名单校验（未知值拒绝，选型文档 §1.1 封闭集不变式）。
        for key, allowed in spec.field_whitelist.items():
            if key in command.values and command.values[key] not in allowed:
                raise InvalidInput(f"配置域 {domain} 字段 {key} 取值非法：{command.values[key]!r}（允许：{', '.join(allowed)}）")
        # 路径字段的取值形态校验：坏值在保存这一步就拒掉，用户当场看到原因；读取侧只做最后兜底。
        # 空白串是例外——它表示「清掉保存值、回落 env 默认」，不是一个要落盘的路径。
        for key in spec.path_fields:
            value = command.values.get(key)
            if value is None or not isinstance(value, str) or not value.strip():
                continue
            if not is_absolute_path_value(value):
                raise InvalidInput(f"配置域 {domain} 字段 {key} 取值非法：{PATH_FIELD_HINT}")

        entry = self._entry(domain)
        if entry is None:
            entry = ConfigEntry(domain=domain, payload="{}", secrets="{}")
            self._session.add(entry)

        payload = _loads(entry.payload)
        secrets = _loads(entry.secrets)
        changed: list[str] = []
        for key, value in command.values.items():
            if payload.get(key) != value:
                changed.append(key)
            payload[key] = value
        for key, value in command.secrets.items():
            # 空串 = 保留原值（前端脱敏占位下未重新输入）
            if not value:
                continue
            changed.append(key)
            secrets[key] = value

        entry.payload = json.dumps(payload, ensure_ascii=False)
        entry.secrets = json.dumps(secrets, ensure_ascii=False)
        entry.updated_by = command.operator_ref

        audit = ConfigAudit(
            domain=domain,
            action="save",
            operator_ref=command.operator_ref,
            changed_keys=json.dumps(sorted(set(changed)), ensure_ascii=False),
        )
        self._session.add(audit)
        self._session.flush()
        # 留痕日志：只记域/操作者/字段名个数，绝不记值（硬规则 8）
        log_event(
            _COMPONENT,
            "config.saved",
            domain=domain,
            operator_ref=command.operator_ref,
            changed_count=len(set(changed)),
        )
        return ConfigSaveResult(
            domain=domain,
            saved=True,
            changed_keys=sorted(set(changed)),
            audit_ref=str(audit.id),
        )

    # ---- 模型服务：provider 列表读写（存储零迁移，落既有 model_service 行的 JSON 值） ----

    def list_providers(self) -> LlmProviderListRead:
        entry = self._entry("model_service")
        payload = _loads(entry.payload) if entry is not None else {}
        secrets = _loads(entry.secrets) if entry is not None else {}
        providers, active_id = normalize_providers(payload, self._settings)
        return LlmProviderListRead(
            active_provider_id=active_id,
            providers=[
                LlmProviderRead(
                    **p,
                    api_key_set=bool(provider_api_key(secrets, p["id"])),
                    active=p["id"] == active_id,
                )
                for p in providers
            ],
            provider_types=[
                LlmProviderTypeRead(key=key, label=label, description=desc)
                for key, label, desc in PROVIDER_TYPES
            ],
            source="saved" if isinstance(payload.get("providers"), list) and payload["providers"] else "env",
            updated_at=entry.updated_at.isoformat() if entry is not None and entry.updated_at else None,
            updated_by=entry.updated_by if entry is not None else None,
        )

    def save_providers(self, command: LlmProviderSaveCommand) -> LlmProviderListRead:
        """整表替换 providers 并写审计留痕。

        缺席的 provider 视为删除，其密钥一并清除——留着孤儿密钥既无用又是泄露面。
        密钥留空=保留原值（前端脱敏占位下没重输），与既有单表单保存语义一致。
        """
        if not command.operator_ref.strip():
            raise InvalidInput("operator_ref 不能为空（审计留痕需要操作者）")

        entry = self._entry("model_service")
        if entry is None:
            entry = ConfigEntry(domain="model_service", payload="{}", secrets="{}")
            self._session.add(entry)
        payload = _loads(entry.payload)
        secrets = _loads(entry.secrets)

        # 保存前库里已有的能力档案（按 provider 标识索引），供「请求体没带档案就保留原值」用。
        existing_rows, _existing_active = normalize_providers(payload, self._settings)
        saved_profiles = {p["id"]: p["capability_profile"] for p in existing_rows}
        saved_thinking = {p["id"]: p["thinking_enabled"] for p in existing_rows}

        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        new_secrets: dict[str, str] = {}
        cleared_ids: set[str] = set()
        for item in command.providers:
            name = (item.name or "").strip()
            base_url = (item.base_url or "").strip()
            model = (item.model or "").strip()
            if not name:
                raise InvalidInput("provider 名称不能为空")
            if item.provider_type not in PROVIDER_TYPE_KEYS:
                raise InvalidInput(
                    f"provider 类型非法：{item.provider_type!r}（允许：{', '.join(PROVIDER_TYPE_KEYS)}）"
                )
            if not base_url:
                raise InvalidInput(f"provider「{name}」的服务地址不能为空")
            if not model:
                raise InvalidInput(f"provider「{name}」的模型标识不能为空")
            pid = (item.id or "").strip() or f"p{uuid.uuid4().hex[:12]}"
            # id 会成为密钥字典的键（api_key:<id>），限定字符集免得奇怪的键混进配置行。
            # 允许调用方自带 id：界面新增一条时就地派号，「设为使用中」才能在保存前先选上。
            if not _PROVIDER_ID_RE.fullmatch(pid):
                raise InvalidInput(f"provider 标识只能用字母、数字、连字符与下划线（至多 40 位）：{pid!r}")
            if pid in seen_ids:
                raise InvalidInput(f"provider 标识重复：{pid}")
            seen_ids.add(pid)
            rows.append({
                "id": pid,
                "name": name,
                "provider_type": item.provider_type,
                "base_url": base_url,
                "model": model,
                "timeout_seconds": float(item.timeout_seconds),
                "max_retries": int(item.max_retries),
                "concurrency_limit": int(item.concurrency_limit),
                # 档案缺席=保留库里原有的那份（与密钥「留空=保留原值」同一套语义）：保存表单的
                # 请求体不带档案时不该把探测成果抹掉；显式给了才覆盖，给空字典即清空。
                "capability_profile": (
                    saved_profiles.get(pid, {})
                    if item.capability_profile is None
                    else dict(item.capability_profile)
                ),
                # 同上：缺席=保留原值（没设过就回落 env 默认，即关思考）。
                "thinking_enabled": (
                    saved_thinking.get(pid, not self._settings.llm_disable_thinking)
                    if item.thinking_enabled is None
                    else bool(item.thinking_enabled)
                ),
            })
            if item.clear_api_key:
                cleared_ids.add(pid)
            elif item.api_key:
                new_secrets[pid] = item.api_key

        if not rows:
            raise InvalidInput("至少要保留一个模型服务")
        active_id = (command.active_provider_id or "").strip()
        if active_id not in seen_ids:
            active_id = rows[0]["id"]

        payload["providers"] = rows
        payload["active_provider_id"] = active_id
        # 启用中 provider 的连接参数同步回平铺字段：既有读端点 /config/model_service 与任何
        # 仍读平铺值的旧路径继续看到「生效中的那一个」，不出现两处配置各说各话。
        active_row = next(row for row in rows if row["id"] == active_id)
        payload.update({
            "service_name": active_row["name"],
            "base_url": active_row["base_url"],
            "model": active_row["model"],
            "timeout_seconds": active_row["timeout_seconds"],
            "max_retries": active_row["max_retries"],
            "concurrency_limit": active_row["concurrency_limit"],
            "provider_type": active_row["provider_type"],
        })

        # 密钥：先按存活 id 剪枝（含既有 default 键），再写入本次新输入的。
        kept: dict[str, Any] = {}
        for key, value in secrets.items():
            if key == _LEGACY_SECRET_KEY:
                if DEFAULT_PROVIDER_ID in seen_ids and DEFAULT_PROVIDER_ID not in cleared_ids:
                    kept[key] = value
                continue
            if key.startswith(_PROVIDER_SECRET_PREFIX):
                pid = key[len(_PROVIDER_SECRET_PREFIX):]
                if pid in seen_ids and pid not in cleared_ids:
                    kept[key] = value
                continue
            kept[key] = value  # 非本机制的键不越权删除
        for pid, value in new_secrets.items():
            kept[_provider_secret_key(pid)] = value
            if pid == DEFAULT_PROVIDER_ID:
                # 为 default 重新输入过密钥：作废存量老键，避免两处密钥并存、以后不知哪个生效。
                kept.pop(_LEGACY_SECRET_KEY, None)
        for pid in cleared_ids:
            kept.pop(_provider_secret_key(pid), None)

        entry.payload = json.dumps(payload, ensure_ascii=False)
        entry.secrets = json.dumps(kept, ensure_ascii=False)
        entry.updated_by = command.operator_ref

        # 留痕只记字段名与条数，绝不记地址以外的值、绝不记密钥（硬规则 8）。
        changed = ["providers", "active_provider_id"]
        if new_secrets or cleared_ids:
            changed.append("api_key")
        audit = ConfigAudit(
            domain="model_service",
            action="save_providers",
            operator_ref=command.operator_ref,
            changed_keys=json.dumps(sorted(changed), ensure_ascii=False),
        )
        self._session.add(audit)
        self._session.flush()
        log_event(
            _COMPONENT,
            "config.providers.saved",
            domain="model_service",
            operator_ref=command.operator_ref,
            provider_count=len(rows),
            secret_changed=len(new_secrets) + len(cleared_ids),
        )
        return self.list_providers()

    # ---- 引用标准目录：读写（AEP-118；存储零迁移，落 reference_standards 域一行 JSON） ----

    def list_reference_standards(self) -> ReferenceStandardCatalogRead:
        """内置清单（标注停用）＋ 用户自有条目，按类别与标准号稳定排序。

        从未保存过用户层数据时返回纯内置清单，source 标 builtin——与「保存过但清空了自有
        条目」区分开，界面上能说清目录当前是哪来的。
        """
        entry = self._entry("reference_standards")
        payload = _loads(entry.payload) if entry is not None else {}
        custom = normalize_custom_entries(payload.get("custom_entries"))
        disabled = normalize_disabled_keys(payload.get("disabled_builtin_keys"))
        return ReferenceStandardCatalogRead(
            entries=[
                ReferenceStandardRead(
                    key=s.key, code=s.code, title=s.title, year=s.year, issuer=s.issuer,
                    note=s.note, category=s.category,
                    category_label=CATEGORY_LABELS.get(s.category, s.category),
                    url=s.url, builtin=s.builtin, enabled=enabled,
                )
                for s, enabled in merge_catalog(custom, disabled)
            ],
            categories=[
                ReferenceStandardCategoryRead(key=key, label=CATEGORY_LABELS[key])
                for key in CATEGORY_KEYS
            ],
            builtin_count=len(BUILTIN_STANDARDS),
            custom_count=len(custom),
            disabled_count=len(disabled),
            source="saved" if entry is not None else "builtin",
            updated_at=entry.updated_at.isoformat() if entry is not None and entry.updated_at else None,
            updated_by=entry.updated_by if entry is not None else None,
        )

    def save_reference_standards(
        self, command: ReferenceStandardSaveCommand
    ) -> ReferenceStandardCatalogRead:
        """整表替换用户层（自有条目 + 停用清单）并写审计留痕。

        内置条目不进配置存储：它们随代码版本化，用户侧只留「停用了哪几条」的标识清单，
        所以内置条目日后修订（如标准出了新版）时不会有一份过期副本留在库里。
        """
        if not command.operator_ref.strip():
            raise InvalidInput("operator_ref 不能为空（审计留痕需要操作者）")
        try:
            custom = validate_custom_entries(
                [item.model_dump() for item in command.custom_entries]
            )
            disabled = validate_disabled_keys(list(command.disabled_builtin_keys))
        except ValueError as exc:
            raise InvalidInput(str(exc)) from exc

        entry = self._entry("reference_standards")
        if entry is None:
            entry = ConfigEntry(domain="reference_standards", payload="{}", secrets="{}")
            self._session.add(entry)
        payload = _loads(entry.payload)
        payload["custom_entries"] = [
            {
                "key": s.key, "code": s.code, "title": s.title, "year": s.year,
                "issuer": s.issuer, "note": s.note, "category": s.category, "url": s.url,
            }
            for s in custom
        ]
        payload["disabled_builtin_keys"] = list(disabled)
        entry.payload = json.dumps(payload, ensure_ascii=False)
        entry.updated_by = command.operator_ref

        audit = ConfigAudit(
            domain="reference_standards",
            action="save_reference_standards",
            operator_ref=command.operator_ref,
            changed_keys=json.dumps(
                ["custom_entries", "disabled_builtin_keys"], ensure_ascii=False
            ),
        )
        self._session.add(audit)
        self._session.flush()
        # 留痕只记条数，不记条目内容（硬规则 8 的一贯口径）。
        log_event(
            _COMPONENT,
            "config.reference_standards.saved",
            domain="reference_standards",
            operator_ref=command.operator_ref,
            custom_count=len(custom),
            disabled_count=len(disabled),
        )
        return self.list_reference_standards()

    # ---- 模型服务：两级连通测试（不落任何治理事实；响应不含密钥/原始响应体） ----

    # ---- 导出能力就绪清单（04A §9「按域提供专属操作」在导出域的落点）----

    def export_readiness(self) -> ExportReadinessRead:
        """逐项探测 docx 导出实际依赖的本地工具链，返回就绪清单。

        判定复用适配器：soffice 走 `docx_to_pdf.find_soffice()`，mmdc/java/plantuml.jar 走
        `diagram_render.resolve_tools()`——本方法**不做任何路径解析**，防两处漂移。
        探测零副作用：只定位可执行文件并跑 `--version` 级命令，不发起转换、不写文件、不出网；
        版本取不到不改变就绪结论。日志只记结果码与就绪与否，不记路径（路径源自环境变量，硬规则 8）。
        """
        # 发起先留痕：三次版本探测各有最长 10 秒的超时，探测期间若没有这一行，
        # 一次卡住或半途抛出的调用在日志里什么都不会留下。
        log_event(_COMPONENT, "export.readiness.started", domain="export")
        items: list[ExportReadinessItemRead] = []

        soffice = find_soffice()
        items.append(
            ExportReadinessItemRead(
                key="pdf_preview",
                ready=soffice is not None,
                outcome="ready" if soffice is not None else "soffice_missing",
                path=soffice,
                version=soffice_version(soffice) if soffice is not None else None,
            )
        )

        tools = resolve_tools()
        mmdc = tools["mmdc"]
        items.append(
            ExportReadinessItemRead(
                key="mermaid_diagram",
                ready=mmdc is not None,
                outcome="ready" if mmdc is not None else "mmdc_missing",
                path=mmdc,
                version=mmdc_version(mmdc) if mmdc is not None else None,
            )
        )

        java, jar = tools["java"], tools["plantuml_jar"]
        if java is None:
            # 两个依赖都可能缺，结果码只报一个：先报 java——jar 在手也跑不起来。
            plantuml = ExportReadinessItemRead(key="plantuml_diagram", ready=False, outcome="java_missing")
        elif jar is None:
            plantuml = ExportReadinessItemRead(
                key="plantuml_diagram", ready=False, outcome="plantuml_jar_missing", path=java,
            )
        else:
            plantuml = ExportReadinessItemRead(
                key="plantuml_diagram", ready=True, outcome="ready", path=jar,
                version=plantuml_version(java, jar),
            )
        items.append(plantuml)

        for item in items:
            log_event(
                _COMPONENT, "export.readiness.item", ok=item.ready,
                capability=item.key, outcome=item.outcome, version_known=item.version is not None,
            )
        all_ready = all(item.ready for item in items)
        log_event(_COMPONENT, "export.readiness.done", ok=all_ready, ready_count=sum(i.ready for i in items))
        return ExportReadinessRead(
            checked_at=datetime.now(timezone.utc).isoformat(),
            all_ready=all_ready,
            items=items,
        )

    def _probe_api_key(self, command: ModelConnectionTestCommand, base_url: str) -> str | None:
        """取本次探测要用的密钥：现输的优先，没现输且允许时才取已存的。

        已存密钥只对它保存时的那个地址有效：把已存密钥发往请求体里任意 base_url 会外泄密钥
        （无鉴权的本地工具端点，恶意网页的跨站请求即可驱动）。取密钥前先断言请求地址与该
        provider 已存地址一致，改了地址想用已存密钥就得重新输入。归一化只忽略结尾多余斜杠。
        连通测试与能力探测共用这一份判断，免得两条入口的密钥纪律各写一套、日后改漏一处。
        """
        api_key = command.api_key or None
        if api_key or not command.use_saved_key:
            return api_key
        entry = self._entry("model_service")
        payload = _loads(entry.payload) if entry is not None else {}
        secrets = _loads(entry.secrets) if entry is not None else {}
        provider_id = command.provider_id or DEFAULT_PROVIDER_ID
        providers, _active_id = normalize_providers(payload, self._settings)
        saved = next((p for p in providers if p["id"] == provider_id), None)
        saved_base = (saved["base_url"] if saved else "").rstrip("/")
        if saved_base and saved_base != base_url.rstrip("/"):
            raise InvalidInput(
                "改了服务地址后要用已存密钥测试，请重新输入密钥"
                "（已存密钥只对保存时的地址有效）"
            )
        return provider_api_key(secrets, provider_id)

    def test_model_connection(self, command: ModelConnectionTestCommand) -> ModelConnectionTestResult:
        base_url = (command.base_url or "").strip()
        if not base_url:
            raise InvalidInput("base_url 不能为空")
        api_key = self._probe_api_key(command, base_url)
        level = command.level if command.level in ("reachability", "generation") else "reachability"
        provider_type = command.provider_type if command.provider_type in PROVIDER_TYPE_KEYS else DEFAULT_PROVIDER_TYPE
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None

        if level == "generation":
            result = _probe_generation(base_url, command, provider_type, headers)
        else:
            result = _probe_reachability(base_url, command, headers)

        # 测试动作同样留痕（外部调用口径）；绝不记密钥、绝不记响应正文
        log_event(
            _COMPONENT,
            "config.model_connection.tested",
            domain="model_service",
            level=level,
            provider_type=provider_type,
            ok=result.ok,
            outcome=result.outcome,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
        )
        return result

    def probe_capabilities(
        self, command: ModelConnectionTestCommand
    ) -> ModelCapabilityProbeResult:
        """对一个端点逐项探测产品依赖的六项能力，回清单 + 一份可「应用」的能力档案。

        探测**不写库、不改启用状态**：档案要等用户点「应用」、随 provider 配置保存才生效
        （提案 3.3「『应用』动作才把档案固化进配置」）。
        逐项独立：前两项（可达、能生成）是后四项的前提——连回话都不行就没法验产物，此时后四项
        一律记「未探明」；前两项过了之后，后四项各自超时或出错也只影响自己那一行。
        """
        base_url = (command.base_url or "").strip()
        if not base_url:
            raise InvalidInput("base_url 不能为空")
        model = (command.model or "").strip()
        if not model:
            raise InvalidInput("逐能力探测需要模型标识")
        api_key = self._probe_api_key(command, base_url)
        provider_type = (
            command.provider_type if command.provider_type in PROVIDER_TYPE_KEYS
            else DEFAULT_PROVIDER_TYPE
        )
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        timeout = float(command.timeout_seconds or _PROBE_REQUEST_TIMEOUT)
        started = time.monotonic()

        reach = _probe_reachability(base_url, command, headers)
        findings = [CapabilityFinding(
            key=CAP_REACHABLE,
            state=CAP_STATE_SUPPORTED if reach.ok else CAP_STATE_UNSUPPORTED,
            outcome=reach.outcome, latency_ms=reach.latency_ms,
            detail={"model_count": reach.model_count, "model_listed": reach.model_listed},
        )]
        generation_ok = False
        if reach.ok:
            gen = _probe_generation(base_url, command, provider_type, headers)
            generation_ok = gen.ok
            findings.append(CapabilityFinding(
                key=CAP_GENERATE,
                state=CAP_STATE_SUPPORTED if gen.ok else CAP_STATE_UNSUPPORTED,
                outcome=gen.outcome, latency_ms=gen.latency_ms,
                detail={"reply_length": gen.reply_length},
            ))
        else:
            findings.append(CapabilityFinding(key=CAP_GENERATE, state=CAP_STATE_UNKNOWN))

        if generation_ok:
            # 元数据先读一次，C3（思考能力声明）与 C5（有效上下文）共用，不重复调端点。
            meta = _fetch_endpoint_metadata(base_url, model, provider_type, headers)
            # C3 排在最前：它探明这个端点用哪个字段关思考，C6 与 C4 的试探请求要带上那个字段，
            # 否则思考段会吃光输出预算，C4 会把支持 JSON 格式输出的端点误判成不支持。
            thinking = _probe_thinking(base_url, model, provider_type, headers, timeout, meta)
            thinking_off = _thinking_off_extra(provider_type, thinking)
            findings.append(thinking)
            # C6 排在 C4 之前：它决定「返回 200」这件事本身有多少信息量，是读 C4 结论的前提。
            findings.append(_probe_unknown_fields(base_url, model, headers, timeout, thinking_off))
            findings.append(_probe_structured(base_url, model, headers, timeout, thinking_off))
            findings.append(_context_finding(meta, provider_type))
        else:
            findings.extend(
                CapabilityFinding(key=key, state=CAP_STATE_UNKNOWN)
                for key in (CAP_UNKNOWN_FIELDS, CAP_THINKING, CAP_STRUCTURED, CAP_CONTEXT)
            )

        order = {key: i for i, key in enumerate(CAPABILITY_KEYS)}
        findings.sort(key=lambda f: order.get(f.key, len(order)))
        probed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        profile = _findings_to_profile(findings, probed_at)

        # 留痕只记结论代码与耗时，不记密钥、不记响应正文（硬规则 8）。
        log_event(
            _COMPONENT,
            "config.capabilities.probed",
            domain="model_service",
            provider_type=provider_type,
            ok=generation_ok,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            states={f.key: f.state for f in findings},
            thinking_off_mode=profile.thinking_off_mode,
            structured_tier=profile.structured_tier,
            context_tokens=profile.context_tokens,
        )
        return ModelCapabilityProbeResult(
            items=[CapabilityItemRead(**dataclasses.asdict(f)) for f in findings],
            profile=profile.to_payload(),
            probed_at=probed_at,
            ok=generation_ok,
        )


def resolve_llm_settings(session: Session, base: Settings | None = None) -> Settings:
    """模型服务域配置读通（配置期写入 → 适配器读取）：库内**启用中 provider** 覆盖 env 默认。

    运行时调用链不变：worker/L2 仍自行构建并调用适配器，这里只换参数来源。
    无保存配置或字段为空时回落 env，保证行为与配置前一致。

    **每次构建 LLM 客户端都必须先过这里**——进程启动时冻结的 env 配置直接喂给客户端工厂，
    会让界面上改的配置对该条链路永不生效（守护测试钉死，见 tests/test_llm_settings_guard.py）。
    """
    base = base or env_settings
    entry = session.scalar(select(ConfigEntry).where(ConfigEntry.domain == "model_service"))
    if entry is None:
        return base
    payload = _loads(entry.payload)
    secrets = _loads(entry.secrets)
    providers, active_id = normalize_providers(payload, base)
    active = next((p for p in providers if p["id"] == active_id), None)
    # normalize_providers 契约保证 providers 恒非空、active_id 恒被校正为其中一个已知 id，
    # 因此 active 不可能是 None。若哪天该契约被破坏，宁可响亮报错也不要静默回落 env（那会
    # 让「界面配置不生效」的老 bug 悄悄复活）。
    assert active is not None, "normalize_providers 违约：active_id 不在 providers 里"
    overrides: dict[str, Any] = {}
    if active["base_url"]:
        overrides["llm_base_url"] = active["base_url"]
    if active["model"]:
        overrides["llm_model"] = active["model"]
    if active["timeout_seconds"]:
        overrides["llm_timeout"] = float(active["timeout_seconds"])
    if active["provider_type"]:
        overrides["llm_provider_type"] = active["provider_type"]
    # 思考模式开关：库里存的是「启不启用思考」，适配层读的是「要不要关思考」，此处取反投影。
    # 恒设（不像其他字段那样「非空才覆盖」）——布尔值的 False 也是用户的明确选择，不是缺省。
    overrides["llm_disable_thinking"] = not active["thinking_enabled"]
    if active["capability_profile"]:
        # 能力档案是「这个端点实测能做什么」的事实，适配层据此构造请求（关思考发哪个字段、
        # 结构化输出从哪档起、输出上限卡在哪）。没探测过就不设，适配层照旧按类型先验走。
        overrides["llm_capability_profile"] = json.dumps(
            active["capability_profile"], ensure_ascii=False
        )
    api_key = provider_api_key(secrets, active["id"])
    if api_key:
        overrides["llm_api_key"] = api_key
    return dataclasses.replace(base, **overrides) if overrides else base


def resolve_llm_settings_or_env(session: Session, base: Settings) -> Settings:
    """`resolve_llm_settings` 的韧性包装：读取失败时回落 env 并记一行 WARN，绝不阻断调用方。

    配置面故障（数据库抖动、payload 解析出错）不该让整条 LLM 链路不可用，因此吞掉异常回落
    进程 env——但「回落」不等于「沉默」：留一行 `config.resolve.failed` WARN，好在事后知道某次
    请求/任务用的其实是 env 而非界面配置。请求链路（deps.py）与异步任务链路（workers/tasks.py）
    共用这一份实现与这一行日志，避免两处逐字复制、日志口径分叉。
    """
    try:
        return resolve_llm_settings(session, base)
    except Exception:  # noqa: BLE001 配置读取失败绝不阻断调用方，回落 env
        log_event(_COMPONENT, "config.resolve.failed", level="WARN", domain="model_service")
        return base


def resolve_active_convention(session: Session) -> str:
    """需求规约域配置读通：返回当前生效的规约方案 key。

    无配置行或字段为空 → 回落默认方案（ears-cn），与配置前行为完全一致（选型文档 §2 零迁移）。
    取值恒在封闭集内（保存侧白名单裁决），此处对越界值同样回落默认以求稳。
    """
    entry = session.scalar(select(ConfigEntry).where(ConfigEntry.domain == "requirement_convention"))
    if entry is None:
        return DEFAULT_CONVENTION
    value = _loads(entry.payload).get("active_convention")
    if isinstance(value, str) and value in CONVENTION_KEYS:
        return value
    return DEFAULT_CONVENTION


def resolve_export_dir(session: Session, base: Settings | None = None) -> str:
    """导出域配置读通：返回 docx 落盘目录（已保存值覆盖 env 默认）。

    没有这个函数之前，设置页保存的导出目录后端从不读取——页面上能编能存，实际落盘仍走 env，
    与 convert_timeout_seconds 是同一种缺陷（T20260724 走查发现）。
    无配置行、字段缺失或为空串 → 回落 env（与配置前行为完全一致，零迁移）。
    保存侧已拒绝非绝对路径，这里对坏值再兜一道并记一行 WARN——库里能出现坏值只有两种来路：
    校验上线前存下的旧行，或绕开界面直写库。回落不等于沉默：留痕好让事后能答上「这次导出用的
    到底是保存值还是 env」，因为落盘失败时用户可见的只有一句失败文案。
    只记来源与原因码，不记目录取值（硬规则 8）。
    """
    entry = session.scalar(select(ConfigEntry).where(ConfigEntry.domain == "export"))
    if entry is not None:
        value = _loads(entry.payload).get("export_dir")
        if isinstance(value, str) and value.strip():
            if is_absolute_path_value(value):
                log_event(_COMPONENT, "config.export_dir.resolved", domain="export", source="saved")
                return value.strip()
            log_event(_COMPONENT, "config.export_dir.invalid_fallback", level="WARN",
                      domain="export", source="env", error_code="not_absolute_path")
    log_event(_COMPONENT, "config.export_dir.resolved", domain="export", source="env")
    return (base or env_settings).export_dir
