"""要素链路提示词模板机制（Jinja2）。

设计（拍板于 2026-07-06，参照 phase1 主模板+分片组合，网络实践佐证见方案记录）：
- 单文件编写、双消息发送：每个用途一个 .jinja2 模板，内含 `{% block system %}` 与
  `{% block user %}` 两块，render_pair() 分别渲染成 (system, user) 两条消息。
- 缓存友好：system 块只放部署期稳定内容（角色/规则/类型清单/输出契约），逐字节稳定
  以命中推理侧前缀缓存（vLLM APC / llama.cpp prefix cache / 商用 API prompt caching）；
  每次变化的语料（原文+补块、目标、修订稿、意图、判据）一律放 user 块。
- 动态单一来源：枚举清单/裁定语义来自 app.domain.labels，判据状态字来自 app.domain.rubrics，
  输出 JSON 形状与解析器同文件定义（llm.py），模板不得手写这些内容。
- SandboxedEnvironment：模板变量含用户可控文本（材料原文、指令），沙箱防模板注入。
"""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from app.domain import rubrics
from app.domain.labels import (
    CHART_FINDING_TYPE_GUIDE,
    CHART_FORMAT_GUIDE,
    CHART_TYPE_GUIDE,
    DIAGNOSIS_MODE_GUIDE,
    EARS_PATTERN_GUIDE,
    ELEMENT_TYPE_LABELS,
    EXECUTION_OPERATION_GUIDE,
    INTAKE_JUDGEMENT_GUIDE,
    ITEM_REVISION_FIELD_GUIDE,
    MODEL_VERDICT_GUIDE,
    QUALITY_DIMENSION_GUIDE,
    QUALITY_RULE_GUIDE,
    QUALITY_SEVERITY_GUIDE,
    REVIEW_CONCLUSION_GUIDE,
    REVIEW_FINDING_TYPE_GUIDE,
    KNOWLEDGE_DISCOURSE_GUIDANCE,
    VERDICT_KIND_GUIDE,
    element_type_entries,
    knowledge_category_entries,
    requirement_item_type_entries,
)

_PROMPT_DIR = Path(__file__).resolve().parent

_env = SandboxedEnvironment(
    loader=FileSystemLoader(str(_PROMPT_DIR)),
    undefined=StrictUndefined,  # 漏传变量直接报错，渲染测试可拦截
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,  # 输出目标是纯文本提示词，非 HTML
    keep_trailing_newline=False,
)


def base_vars() -> dict:
    """部署期稳定变量（进入 system 块；保持逐字节稳定以利前缀缓存）。"""
    return {
        "element_types": element_type_entries(),
        "knowledge_categories": knowledge_category_entries(),
        "knowledge_discourse_guidance": KNOWLEDGE_DISCOURSE_GUIDANCE,
        "model_verdicts": MODEL_VERDICT_GUIDE,
        "review_conclusions": REVIEW_CONCLUSION_GUIDE,
        "execution_operations": EXECUTION_OPERATION_GUIDE,
        "facet_statuses": list(rubrics.FACET_STATUSES),
        "correctness_values": list(rubrics.CORRECTNESS_VALUES),
        "intake_judgements": INTAKE_JUDGEMENT_GUIDE,
        "verdict_kinds": VERDICT_KIND_GUIDE,
        "finding_types": REVIEW_FINDING_TYPE_GUIDE,
        "diagnosis_modes": DIAGNOSIS_MODE_GUIDE,
        "quality_rules": QUALITY_RULE_GUIDE,
        "quality_dimensions": QUALITY_DIMENSION_GUIDE,
        "quality_severities": QUALITY_SEVERITY_GUIDE,
        "ears_patterns": EARS_PATTERN_GUIDE,
        "chart_formats": CHART_FORMAT_GUIDE,
        "chart_types": CHART_TYPE_GUIDE,
        "chart_finding_types": CHART_FINDING_TYPE_GUIDE,
        "item_types": requirement_item_type_entries(),
        "item_revision_fields": ITEM_REVISION_FIELD_GUIDE,
    }


def render_pair(template_name: str, **variables) -> tuple[str, str]:
    """渲染 (system, user) 消息对。

    模板必须定义 system / user 两个 block；缺块即抛错（模板契约）。
    Template.blocks 是 Jinja2 3.x 的稳定内部结构（块名 → 渲染生成器）。
    """
    template = _env.get_template(f"{template_name}.jinja2")
    merged = {**base_vars(), **variables}
    rendered: dict[str, str] = {}
    for block in ("system", "user"):
        fn = template.blocks.get(block)
        if fn is None:
            raise KeyError(f"提示词模板 {template_name} 缺少 {{% block {block} %}}")
        ctx = template.new_context(merged)
        rendered[block] = "".join(fn(ctx)).strip()
    return rendered["system"], rendered["user"]


def dumps(value) -> str:
    """模板内 JSON 序列化的统一口径（中文不转义）。"""
    return json.dumps(value, ensure_ascii=False)


_env.filters["tojson_cn"] = dumps

# 便于渲染测试断言标签覆盖率
__all__ = ["base_vars", "render_pair", "dumps", "ELEMENT_TYPE_LABELS"]
