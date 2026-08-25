"""导出能力就绪清单（docx 导出本地工具链探测）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---- 导出能力就绪清单（T20260724：docx 导出依赖的本地工具链逐项探测；只读，无写接口）----


class ExportReadinessItemRead(BaseModel):
    """单条导出能力的就绪结果：只给稳定结果码与探到的事实，白话文案由前端映射。

    `key` 是能力（不是二进制名）的封闭集：
    pdf_preview（文档转 PDF 预览）/ mermaid_diagram（流程图渲染）/ plantuml_diagram（结构图渲染）。
    `outcome` 是封闭集里的稳定结果码，缺失时指出缺的是哪一个依赖：
    ready / soffice_missing / mmdc_missing / java_missing / plantuml_jar_missing。
    `path` 是定位到的可执行文件或 jar 路径（缺失时为 None）；`version` 取不到时为 None，
    且**不影响 `ready`**——就绪与否只由定位结果决定，与渲染时的判据同源。
    """

    key: str
    ready: bool
    outcome: str
    path: str | None = None
    version: str | None = None


class ExportReadinessRead(BaseModel):
    """导出能力就绪清单：逐项探测本地工具链，纯定位＋版本，不做任何转换。"""

    checked_at: str
    all_ready: bool
    items: list[ExportReadinessItemRead] = Field(default_factory=list)

class CapabilityItemRead(BaseModel):
    """能力清单里的一条。只回稳定代码与实测数值，**白话文案由前端映射**。

    这样走查阶段改措辞不必动后端，且文案本身可单测——与既有两级连通测试的 outcome 同一套口径。
    `key` 取值：reachable / generate / thinking_off / structured / context / unknown_fields；
    `state` 取值：supported（可用）/ degraded（有条件）/ unsupported（不可用）/ unknown（没探明）。
    键与取值的封闭集定义在 app/adapters/llm.py，前端不得另写一份。
    """

    key: str
    state: str
    # C3：探明的关思考方式（reasoning_effort / enable_thinking / none）。
    mode: str | None = None
    # C3：这个端点/模型会不会思考（null=没探明）。「思考模式」开关的可用性说明取自这里——
    # 它与 state 回答的是两个问题：available 说有没有思考这回事，state 说能不能把它关掉。
    available: bool | None = None
    # C4：实测强制生效的最高档（json_schema / json_object / prompt_only）。
    tier: str | None = None
    # C5：有效上下文（token）与它的出处（models.max_model_len / props.n_ctx / api_show.context_length）。
    tokens: int | None = None
    source: str | None = None
    # 结论之外还要告诉用户的那一句话的代码（如 vllm_needs_reasoning_parser）。
    note_code: str | None = None
    # C1/C2 沿用既有两级连通测试的稳定结果码；其余项在探测出错时放异常类名。
    outcome: str | None = None
    latency_ms: int | None = None
    # 判定依据的数值事实（基线/候选各自的延迟与输出 token 数、试过哪些字段）。绝不含响应正文。
    detail: dict = Field(default_factory=dict)


class ModelCapabilityProbeResult(BaseModel):
    """逐能力探测的结果：清单 + 一份可「应用」的能力档案。

    探测本身不写库、不改启用状态——档案要等用户点「应用」、随 provider 配置保存才生效。
    """

    items: list[CapabilityItemRead] = Field(default_factory=list)
    # 可直接写回 provider 的 capability_profile（形状见 app/adapters/llm.py）。
    profile: dict = Field(default_factory=dict)
    probed_at: str
    # 基线两项（可达＋能生成）是否都过。没过时后四项一律 unknown：连回话都不行就谈不上验产物。
    ok: bool = False
