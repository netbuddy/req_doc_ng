"""推理引擎 OpenAI 兼容面的**契约桩件**（T20260720-model-provider-registry）。

为什么起真实的本地 HTTP 服务器，而不是在 httpx 传输层打桩：本卡要证明的核心事实是
「界面上改完配置，进程不重启，下一次请求就打到新地址」——这件事只有让被测代码真的把
HTTP 请求发到网络上、由另一端收下来，才算证明。传输层打桩会把这条链路的后半截替换掉，
证不了它。服务器绑 0 号端口取随机空闲端口，因此不占槽位端口配额、可并行跑。

响应形状按各引擎官方仓库的实现核对（2026-07-20 核对；prose 文档常只给支持矩阵、不给完整
JSON 例子，故以官方源码为准），逐条出处标在下面各处理函数里。各处理函数原按文档/源码推断的
行为，已于 2026-07-23 对 116 真实 ollama/vllm 端点逐条复核销账，逐条结论见文件末尾
「真实端点复核销账清单」。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# provider 类型键与 app.adapters.llm.PROVIDER_TYPES 对齐（此处只用作桩件行为分支）。
FLAVOR_LLAMA_CPP = "llama_cpp"
FLAVOR_OLLAMA = "ollama"
FLAVOR_VLLM = "vllm"


class ProviderStub:
    """一个可配置行为的 OpenAI 兼容端点桩件。

    `requests` 记录收到的每一次请求（方法/路径/请求头有无鉴权/请求体），供契约断言使用。
    """

    def __init__(
        self,
        flavor: str = FLAVOR_LLAMA_CPP,
        models: tuple[str, ...] = ("qwen2.5",),
        *,
        require_api_key: str | None = None,
        chat_status: int | None = None,
        chat_content: str = "OK",
        delay_seconds: float = 0.0,
        chat_body_override: Any = None,
        models_body_override: Any = None,
        reject_response_format: bool = False,
        thinking: bool = False,
        reasoning_parser: bool = False,
        structured_enforced: bool = True,
        strict_unknown_fields: bool = False,
        context_tokens: int = 32768,
        props_status: int | None = None,
        show_status: int | None = None,
        declares_thinking: bool | None = None,
        server_reasoning_format: str = "auto",
    ) -> None:
        self.flavor = flavor
        self.models = models
        self.require_api_key = require_api_key
        self.chat_status = chat_status
        self.chat_content = chat_content
        self.delay_seconds = delay_seconds
        self.chat_body_override = chat_body_override
        self.models_body_override = models_body_override
        # 模拟「端点不认 response_format 参数」：带该参数就回 400，不带则正常。
        # 这是结构化输出降级链（json_schema → json_object → 纯提示词）的触发条件。
        self.reject_response_format = reject_response_format
        # ---- 以下为能力探测（C3–C6）用的行为开关（T20260724-capability-probe-panel）----
        # thinking：这个模型是思考模型，回复里带思考段（除非请求带了本 flavor 真正认的关思考字段）。
        self.thinking = thinking
        # reasoning_parser：模拟 vLLM 服务端起了 --reasoning-parser。不起时 vLLM 对
        # reasoning_effort/enable_thinking 都只是静默收下回 200、思考照跑（实测行为）。
        self.reasoning_parser = reasoning_parser
        # structured_enforced=False：收下 response_format 回 200，但产物**不符合**给定 schema。
        # 这就是「返回 200 ≠ 字段生效」的假成功，只看状态码的降级链识不破它。
        self.structured_enforced = structured_enforced
        # strict_unknown_fields=True：请求体里出现未声明字段就回 400（与静默接受相对的那一端）。
        self.strict_unknown_fields = strict_unknown_fields
        # 有效上下文（token）：vLLM 从 /models 的 max_model_len 报，llama.cpp 从 /props 的
        # n_ctx 报，ollama 从 /api/show 的模型元数据报（且报的是模型上限而非兼容层生效值）。
        self.context_tokens = context_tokens
        # 元数据端点故障模拟（探不出 → 必须落「未知」态而不是编一个值）。
        self.props_status = props_status
        self.show_status = show_status
        # declares_thinking：端点元数据是否**声明**这个模型具备思考能力（None=不提供该声明）。
        # llama.cpp 经 /props 的 chat_template_caps.supports_preserve_reasoning 表达，
        # ollama 经 /api/show 的 capabilities 数组（含 "thinking"）表达。
        self.declares_thinking = declares_thinking
        # server_reasoning_format：llama.cpp 服务端的 reasoning_format 启动参数；"none" 即
        # -rea off——模型会思考但服务端全局关掉了输出。这是「不会思考」与「被关了」的分水岭。
        self.server_reasoning_format = server_reasoning_format
        self.requests: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----

    def __enter__(self) -> "ProviderStub":
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # 静音：测试输出不要被访问日志淹没
                pass

            def _record(self, method: str, body: Any) -> None:
                stub.requests.append({
                    "method": method,
                    "path": self.path,
                    # 只记「有没有带鉴权头」，绝不记密钥本身（硬规则 8）——原始 Bearer 头此处
                    # 一律不落，避免未来某个整字典 assert / 调试 print 把令牌打进 CI 输出。
                    "authorized": bool(self.headers.get("Authorization")),
                    "body": body,
                })

            def _send(self, status: int, payload: Any) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _auth_failed(self) -> bool:
                if stub.require_api_key is None:
                    return False
                return self.headers.get("Authorization") != f"Bearer {stub.require_api_key}"

            def do_GET(self) -> None:  # noqa: N802 （BaseHTTPRequestHandler 的约定命名）
                self._record("GET", None)
                if stub.delay_seconds:
                    time.sleep(stub.delay_seconds)
                if self.path == "/props":
                    # llama.cpp 的服务端属性端点，挂在服务根路径上而**不在 /v1 下**
                    # （出处：llama.cpp server 的 handle_props；n_ctx 即启动参数 -c 的每 slot 值）。
                    if stub.props_status is not None:
                        self._send(stub.props_status, {"error": {"message": "props unavailable"}})
                        return
                    props: dict[str, Any] = {
                        "default_generation_settings": {
                            "n_ctx": stub.context_tokens,
                            "params": {
                                "stream": False,
                                "reasoning_format": stub.server_reasoning_format,
                                "reasoning_in_content": False,
                            },
                        },
                        "total_slots": 1,
                    }
                    if stub.declares_thinking is not None:
                        props["chat_template_caps"] = {
                            "supports_preserve_reasoning": stub.declares_thinking,
                            "supports_tools": True,
                        }
                    self._send(200, props)
                    return
                if not self.path.endswith("/models"):
                    self._send(404, {"error": {"message": "not found"}})
                    return
                if self._auth_failed():
                    self._send(401, stub._auth_error())
                    return
                self._send(200, stub.models_body_override or stub.model_list_body())

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = None
                self._record("POST", body)
                if stub.delay_seconds:
                    time.sleep(stub.delay_seconds)
                if self.path == "/api/show":
                    # ollama 的模型信息端点（原生 /api 面，不在 /v1 下）。model_info 里带的是
                    # **模型自身**的上下文上限，不等于兼容层实际生效的窗口（实测 27B 模型报
                    # 262144，兼容层实际落 32768）——探针据此只作参考值呈现，不拿来卡请求。
                    if stub.show_status is not None:
                        self._send(stub.show_status, {"error": "model not found"})
                        return
                    show: dict[str, Any] = {
                        "details": {"family": "qwen3"},
                        "model_info": {
                            "general.architecture": "qwen3",
                            "qwen3.context_length": stub.context_tokens,
                        },
                    }
                    if stub.declares_thinking is not None:
                        caps = ["completion", "tools"]
                        if stub.declares_thinking:
                            caps.append("thinking")
                        show["capabilities"] = caps
                    self._send(200, show)
                    return
                if not self.path.endswith("/chat/completions"):
                    self._send(404, {"error": {"message": "not found"}})
                    return
                if self._auth_failed():
                    self._send(401, stub._auth_error())
                    return
                if stub.chat_status is not None:
                    self._send(stub.chat_status, stub._error_body(stub.chat_status, body))
                    return
                if stub.strict_unknown_fields and stub._unknown_fields(body):
                    self._send(400, {"error": {
                        "message": f"unrecognized fields: {', '.join(stub._unknown_fields(body))}",
                        "type": "invalid_request_error"}})
                    return
                if stub.reject_response_format and (body or {}).get("response_format") is not None:
                    self._send(400, {"error": {"message": "response_format is not supported",
                                               "type": "invalid_request_error"}})
                    return
                requested = (body or {}).get("model")
                if requested not in stub.models:
                    self._send(404, stub._model_missing_body(str(requested)))
                    return
                self._send(200, stub.chat_body_override or stub.chat_body(str(requested), body))

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        assert self._server is not None, "桩件未启动"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    # ---- 各引擎的响应形状 ----

    def model_list_body(self) -> dict:
        """GET /v1/models。

        三家都是 `{"object": "list", "data": [...]}`，差别只在 data 元素带哪些字段：
        - ollama：id/object/created/owned_by 四个字段，id 形如 `name:tag`
          （出处：ollama 仓库 openai/openai.go 的 Model struct 与 ToListCompletion）；
        - vLLM：另有 root/parent/max_model_len/permission
          （出处：vllm/entrypoints/openai/engine/protocol.py 的 ModelCard/ModelPermission）；
        - llama.cpp：本仓现网端点即此形状，与 OpenAI 最小集一致。
        """
        now = int(time.time())
        if self.flavor == FLAVOR_VLLM:
            data = [
                {
                    "id": m, "object": "model", "created": now, "owned_by": "vllm",
                    "root": m, "parent": None, "max_model_len": self.context_tokens,
                    "permission": [{"id": f"modelperm-{i}", "object": "model_permission",
                                    "created": now, "allow_sampling": True, "allow_view": True,
                                    "organization": "*", "group": None, "is_blocking": False}],
                }
                for i, m in enumerate(self.models)
            ]
        elif self.flavor == FLAVOR_OLLAMA:
            data = [
                {"id": m, "object": "model", "created": now, "owned_by": "library"}
                for m in self.models
            ]
        else:
            data = [{"id": m, "object": "model", "created": now, "owned_by": "llamacpp"}
                    for m in self.models]
        return {"object": "list", "data": data}

    # ---- 能力探测用的请求体判读（C3 关思考 / C4 结构化 / C6 未识别字段）----

    # 本仓正式请求会用到的字段：其余一律算「未声明字段」，供 strict_unknown_fields 判 400。
    _KNOWN_FIELDS = frozenset({
        "model", "messages", "temperature", "max_tokens", "stream", "response_format",
        "chat_template_kwargs", "reasoning_effort",
    })

    def _unknown_fields(self, body: Any) -> list[str]:
        if not isinstance(body, dict):
            return []
        return sorted(k for k in body if k not in self._KNOWN_FIELDS)

    def _thinking_off_honored(self, body: Any) -> bool:
        """请求体里带的关思考字段，本 flavor 认不认（三家各不相同，116 实测）。

        llama.cpp 认 `chat_template_kwargs.enable_thinking`，不认 reasoning_effort；
        ollama 反过来只认 `reasoning_effort:"none"`（enable_thinking 静默丢弃、思考照跑）；
        vLLM 两个都只在服务端起了 `--reasoning-parser` 时才生效，否则同样静默收下回 200。
        """
        body = body if isinstance(body, dict) else {}
        kwargs = body.get("chat_template_kwargs")
        asked_enable_thinking = isinstance(kwargs, dict) and kwargs.get("enable_thinking") is False
        asked_reasoning_effort = body.get("reasoning_effort") == "none"
        if self.flavor == FLAVOR_LLAMA_CPP:
            return asked_enable_thinking
        if self.flavor == FLAVOR_OLLAMA:
            return asked_reasoning_effort
        if self.flavor == FLAVOR_VLLM:
            return self.reasoning_parser and (asked_enable_thinking or asked_reasoning_effort)
        return asked_enable_thinking or asked_reasoning_effort

    @staticmethod
    def _schema_sample(schema: Any) -> Any:
        """按 JSON Schema 造一个**符合**它的最小样本（只覆盖探针会用到的浅层形状）。

        `structured_enforced=True` 的端点回这个；False 的端点回普通文本——后者就是「收下了
        response_format 却没强制约束」的假成功，只看状态码识不破，验产物才识得破。
        """
        if not isinstance(schema, dict):
            return {}
        kind = schema.get("type")
        if kind == "object" or "properties" in schema:
            props = schema.get("properties")
            props = props if isinstance(props, dict) else {}
            required = schema.get("required")
            keys = required if isinstance(required, list) and required else list(props)
            return {str(k): ProviderStub._schema_sample(props.get(k, {"type": "string"}))
                    for k in keys}
        if kind == "array":
            return [ProviderStub._schema_sample(schema.get("items", {"type": "string"}))]
        if kind == "integer":
            return 1
        if kind == "number":
            return 1.0
        if kind == "boolean":
            return True
        return "ok"

    def chat_body(self, model: str, request_body: Any = None) -> dict:
        """POST /v1/chat/completions 的成功响应。

        ollama 的 usage 只有 prompt/completion/total 三个字段、并带固定的
        system_fingerprint="fp_ollama"（出处：ollama 仓库 openai/openai.go 的
        Usage/ChatCompletion struct 与 ToChatCompletion）；vLLM 的 usage 可多一层
        prompt_tokens_details（出处：vllm engine/protocol.py 的 UsageInfo）。
        本仓只读 choices[0].message.content，其余字段照实给出以便发现将来的形状假设。
        """
        body = request_body if isinstance(request_body, dict) else {}
        response_format = body.get("response_format")
        content = self.chat_content
        if isinstance(response_format, dict) and self.structured_enforced:
            # 端点真的强制约束了输出：按请求的 schema 回一个符合它的对象。
            if response_format.get("type") == "json_schema":
                schema = (response_format.get("json_schema") or {}).get("schema")
            else:
                schema = {"type": "object"}
            content = json.dumps(self._schema_sample(schema), ensure_ascii=False)
        base = {
            "id": "chatcmpl-stub", "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
        }
        if self.thinking and not self._thinking_off_honored(body):
            # 思考模型没被关住：回复里带思考段，输出 token 暴涨（真实端点上就是这样慢 20–50 倍）。
            # 思考段的载体三家不同：llama.cpp 内联 <think> 标签在正文里，ollama 走 message.reasoning，
            # vLLM（起了 parser 时）走 message.reasoning_content——探针三种都要认得出。
            message = base["choices"][0]["message"]
            reasoning = "让我想想：先看题目要求，再逐步推导，最后给出结论。" * 4
            if self.flavor == FLAVOR_LLAMA_CPP:
                message["content"] = f"<think>{reasoning}</think>{content}"
            elif self.flavor == FLAVOR_OLLAMA:
                message["reasoning"] = reasoning
            else:
                message["reasoning_content"] = reasoning
            base["usage"]["completion_tokens"] = 512
            base["usage"]["total_tokens"] = 521
        if self.flavor == FLAVOR_OLLAMA:
            base["system_fingerprint"] = "fp_ollama"
        if self.flavor == FLAVOR_VLLM:
            base["choices"][0]["stop_reason"] = None
            base["usage"]["prompt_tokens_details"] = {"cached_tokens": 0}
        return base

    def _model_missing_body(self, model: str) -> dict:
        """模型不存在：三家都是 404，错误体形状不同。

        ollama：`{"error": {"message": "model 'x' not found", "type": "not_found_error",
        "param": null, "code": null}}`（出处：server/routes.go 抛 404 + middleware/openai.go
        的 BaseWriter.writeError 转成 OpenAI 形状，状态码→type 映射见 openai/openai.go NewError）。
        模型名漏了 `:标签` 时走的就是这一条。
        vLLM：`{"error": {"message": "The model `x` does not exist.", "type": "NotFoundError",
        "code": 404, "param": null}}`，注意 code 是整数而非字符串，且 param 为 null
        （出处：vllm/entrypoints/openai/models/serving.py 的 check_model 与 protocol.py 的 ErrorInfo；
        param 取值 2026-07-23 对 116 真实端点复核订正——实测为 null，本桩件先前误写 "model"，见文件末尾 R4）。
        """
        if self.flavor == FLAVOR_OLLAMA:
            return {"error": {"message": f"model '{model}' not found",
                              "type": "not_found_error", "param": None, "code": None}}
        if self.flavor == FLAVOR_VLLM:
            return {"error": {"message": f"The model `{model}` does not exist.",
                              "type": "NotFoundError", "code": 404, "param": None}}
        return {"error": {"message": f"model '{model}' not found", "type": "not_found_error"}}

    def _auth_error(self) -> dict:
        return {"error": {"message": "invalid api key", "type": "invalid_request_error",
                          "code": "invalid_api_key"}}

    def _error_body(self, status: int, body: Any) -> dict:
        if status == 404:
            return self._model_missing_body(str((body or {}).get("model")))
        if status in (401, 403):
            return self._auth_error()
        return {"error": {"message": f"stub error {status}", "type": "api_error"}}


# ===========================================================================
# 真实端点复核销账清单（R1–R6：2026-07-23 对 116 的真实 ollama/vllm 端点逐条核对）
# ---------------------------------------------------------------------------
# 以下行为原据官方仓库源码/文档推断、桩件按此实现，现已对 116 真实端点复核销账；
# 每条附终局结论与《116摸底报告.md》对应条目，原官方源码/文档出处保留。
# 报告：docs/proposals/llm-provider-feasibility/116摸底报告.md
#
# R1 ollama 对请求体里未声明的额外字段（如 chat_template_kwargs）静默忽略、不报 400。
#    依据：middleware/openai.go 用 Gin 的 ShouldBindJSON，全仓无 DisallowUnknownFields。
#    本仓策略是不向 ollama 发 chat_template_kwargs 这个字段；reasoning_effort 已在下发
#    （T20260724 关思考修复），其生效性靠真实端点实测判断，不以 200 状态码为据。
#    ✅ 销账（116摸底报告 §A2）：带该字段请求 → 200，桩件行为正确。
# R2 ollama 模型名漏 `:标签` 时确为 404（而非 400 或 200 回空）。
#    ✅ 销账（116摸底报告 §A2）：请求 `qwen2.5`（无 tag）→ 404，错误体
#    `{"type":"not_found_error","param":null,"code":null}`，与桩件 _model_missing_body 完全一致。
# R3 ollama /v1/models 的 id 恒为 `name:tag` 全名（因而设置页「模型是否在列表里」的比对
#    对 ollama 必须用带标签的全名，写 `qwen2.5` 而端点上是 `qwen2.5:7b` 会判为不在列表）。
#    ✅ 销账（116摸底报告 §A2）：返回 id = `qwen2.5:0.5b`，带标签全名结论成立。
# R4 vLLM 未知模型名的错误体里 code 为整数 404（形状与 ollama 的 null 不同）。
#    ✅ 销账（116摸底报告 §A3）：请求不存在模型 → 404，体 code=404（整数）、type=NotFoundError。
#    ⟳ 一处订正：实测 `param` 为 null，本桩件先前误写 "model"，已改为 None（见上 _model_missing_body
#    的 vLLM 分支与 test_provider_contracts.py 的 param is None 钉值断言）。
# R5 vLLM 的 chat_template_kwargs 是一等声明字段、放请求体顶层即生效——也就是说发过去
#    不会报错；本仓仍不发，因为 enable_thinking 是 llama.cpp 侧 Qwen 模板的专属开关，
#    交给别家的模板可能语义不同或无此变量。此条属「策略选择」而非「兼容性所迫」。
#    ✅ 销账（116摸底报告 §A3）：带该字段顶层请求 → 200，不报错；本仓不发属策略选择，结论不变。
# R7 ollama 在 /api/show 的 `capabilities` 数组里直接列出模型能力（completion/tools/thinking/…），
#    llama.cpp 在 /props 的 `chat_template_caps.supports_preserve_reasoning` 表达同一件事。
#    ⧗ 待真实端点复核（ollama 半）：116 的 ollama 在本卡实施期未运行，本条按 ollama 官方接口
#    文档实现。llama.cpp 半已对 116 真实端点核实（2026-07-24）：/props 确有 chat_template_caps
#    且该端点 supports_preserve_reasoning=true、params.reasoning_format="none"——即模型具备思考
#    能力而服务端 -rea off 全局关掉，正是「不会思考」与「被服务端关了」的分水岭实例。
# R6 三家在鉴权失败时的状态码（401 与 403 的取用）与错误体形状。本仓只按状态码分级，
#    不解析错误体，故风险有限。
#    ✅ 销账（116摸底报告 §A3 R6 汇总）：ollama 默认无鉴权（伪造 key → 200，无 401/403 可测）；
#    vllm 加 --api-key 后鉴权失败为 401。两引擎均落既有 auth_failed（401/403）分支，无需改动。
# ===========================================================================
