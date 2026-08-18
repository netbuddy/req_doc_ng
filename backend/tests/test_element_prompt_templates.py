"""要素链路提示词模板渲染测试（模板机制契约）。

守护点：
- 四个模板均可用样例变量渲染出非空 (system, user) 对；
- 枚举/判据取值经动态渲染全数出现（labels/enums 是唯一来源，新增值漏改模板即失败）；
- 输出 schema 与解析器同源常量可注入；
- system 块逐字节稳定（前缀缓存前提）；漏传变量立刻报错（StrictUndefined）。
"""
from __future__ import annotations

import pytest
from jinja2.exceptions import UndefinedError

from app.adapters.llm import (
    _EXECUTION_OUTPUT,
    _RECOGNITION_OUTPUT_ITEM,
    _REVIEW_OUTPUT_ITEM,
    _SCAN_OUTPUT_ITEM,
)
from app.adapters.prompts.environment import dumps, render_pair
from app.domain import rubrics
from app.domain.enums import ElementType, ModelVerdict, ReviewConclusion
from app.domain.labels import ELEMENT_TYPE_LABELS, EXECUTION_OPERATION_GUIDE


def _recognition_vars() -> dict:
    return {
        "project_ref": "P-1", "source_note": "访谈", "raw_text": "系统应支持导出。",
        "output_schema": dumps(_RECOGNITION_OUTPUT_ITEM),
    }


def _review_vars() -> dict:
    return {
        "project_ref": "P-1", "source_note": "访谈", "raw_text": "系统应支持导出。",
        "targets": "[]", "rubrics_text": "（无判据）", "intent": "检查边界",
        "output_schema": dumps(_REVIEW_OUTPUT_ITEM),
    }


def _scan_vars() -> dict:
    return {
        "project_ref": "P-1", "source_note": "访谈", "raw_text": "系统应支持导出。",
        "quotes": "[]", "elements": "[]", "intent": "找漏",
        "output_schema": dumps(_SCAN_OUTPUT_ITEM),
    }


def _execution_vars() -> dict:
    return {
        "project_ref": "P-1", "raw_text": "系统应支持导出。",
        "operation_type": "revise_expression", "instruction": "更规范",
        "targets": "[]", "current_draft": "无", "quotes": "[]",
        "output_schema": dumps(_EXECUTION_OUTPUT),
    }


_CASES = [
    ("element_recognition", _recognition_vars),
    ("element_review", _review_vars),
    ("element_scan", _scan_vars),
    ("element_execution", _execution_vars),
]


@pytest.mark.parametrize("name,vars_fn", _CASES)
def test_templates_render_nonempty_pairs(name, vars_fn):
    system, user = render_pair(name, **vars_fn())
    assert system and user
    assert "{{" not in system and "{%" not in system  # 无残留模板语法
    assert "{{" not in user and "{%" not in user


@pytest.mark.parametrize(
    "name,vars_fn",
    [c for c in _CASES if c[0] != "element_review"],  # 复核不选类型，无类型清单
)
def test_element_types_fully_injected(name, vars_fn):
    """类型清单动态化：全部枚举码与中文名必须出现（新增类型漏渲染即红）。"""
    system, _ = render_pair(name, **vars_fn())
    for t in ElementType:
        assert t.value in system, f"{name} 缺类型码 {t.value}"
        assert ELEMENT_TYPE_LABELS[t] in system, f"{name} 缺类型标签 {ELEMENT_TYPE_LABELS[t]}"


def test_recognition_verdicts_injected():
    system, _ = render_pair("element_recognition", **_recognition_vars())
    for v in ModelVerdict:
        assert v.value in system


def test_review_conclusions_and_facets_injected():
    system, user = render_pair("element_review", **_review_vars())
    for c in ReviewConclusion:
        assert c.value in system
    for s in rubrics.FACET_STATUSES:
        assert s in system
    for c in rubrics.CORRECTNESS_VALUES:
        assert c in system
    assert "检查边界" in user  # 复核意图进 user 块（动态内容不进 system）


def test_execution_contract_injected():
    system, user = render_pair("element_execution", **_execution_vars())
    assert "cannot_comply" in system
    for op in EXECUTION_OPERATION_GUIDE:
        assert op["code"] in system
    assert "当前修订稿" in user and "无" in user


@pytest.mark.parametrize("name,vars_fn", _CASES)
def test_system_block_byte_stable(name, vars_fn):
    """system 只放部署期稳定内容：同变量两次渲染逐字节一致（前缀缓存前提）。"""
    s1, _ = render_pair(name, **vars_fn())
    s2, _ = render_pair(name, **vars_fn())
    assert s1 == s2


def test_missing_variable_fails_fast():
    with pytest.raises(UndefinedError):
        render_pair("element_recognition", project_ref="P-1")  # 缺 raw_text 等


# ---- P6a 项目领域上下文注入两态渲染（AC-P6-01；08 §1 幻觉防线）----

_PROJECT_CONTEXT_LANES = ("element_recognition", "element_scan", "element_review", "item_diagnosis")
_GUARD_TEXT = "仅用于理解语境与判别归类"


def _lane_vars(name: str) -> dict:
    from tests.test_prompt_templates import _diagnosis_vars

    if name == "item_diagnosis":
        return _diagnosis_vars()
    return {
        "element_recognition": _recognition_vars,
        "element_scan": _scan_vars,
        "element_review": _review_vars,
    }[name]()


@pytest.mark.parametrize("name", _PROJECT_CONTEXT_LANES)
def test_project_context_injected_when_present(name):
    _, user = render_pair(name, project_scope="电商订单履约", project_background="B2C 平台", **_lane_vars(name))
    assert _GUARD_TEXT in user  # 段自带幻觉防线声明
    assert "电商订单履约" in user and "B2C 平台" in user


@pytest.mark.parametrize("name", _PROJECT_CONTEXT_LANES)
def test_project_context_omitted_when_absent(name):
    _, user = render_pair(name, **_lane_vars(name))  # 不传 scope/background
    assert _GUARD_TEXT not in user  # 空值省略整段（|default 兼容 StrictUndefined）


def test_recognition_output_schema_is_single_source():
    """A5：识别输出的字段名只在 _RECOGNITION_OUTPUT_ITEM 定义一次，提示词由它渲染。

    钉这条是为了防提示词与解析器各写一份字段名后悄悄漂移——漂移的表现是模型按提示词
    给了字段、解析器却按另一个名字取，取不到就静默落 None，界面上只看到「模型没给理由」。
    本条只管住「提示词这一半」（schema 常量 → 渲染后的提示词），解析器那一半见下一条。
    """
    system, _ = render_pair("element_recognition", **_recognition_vars())
    for field in _RECOGNITION_OUTPUT_ITEM:
        assert field in system, f"识别提示词缺输出字段 {field}"


# ---- 解析器读取的字段名 ⊆ 输出 schema 声明的字段名（冷审查裁定 Q3）----
#
# 上一条只钉住了「schema 常量与提示词同源」，解析器读的却是硬编码的字符串字面量，与常量之间
# 没有任何连接：把 item.get("verdict_reason") 改成 item.get("reason")，提示词照旧、测试照旧全绿，
# 而生产上每一条理由都会静默落 None。本组测试补上缺的那一半——用语法树取出解析函数体内所有
# 形如 X.get("字面量") 的读取键，断言它们都在对应 lane 的输出 schema 里。
#
# 前提是一条命名约定：解析模型输出的循环/载荷变量固定叫 item / fr / data（复核 lane 里的 t 是
# 送检目标即入参，不是模型输出，故不在其列）。新增解析函数请沿用这套变量名，否则本测试看不见它。
_OUTPUT_PAYLOAD_VARS = frozenset({"item", "fr", "data"})

# 历史提示词用过、解析器保留兼容读取的别名：不在输出 schema 里，但允许读（读到就当对应字段用）
_LEGACY_INPUT_ALIASES = {
    "element_recognition": frozenset({"process_status"}),
    "element_execution": frozenset({"source_anchor"}),
}


def _schema_field_names(schema) -> set[str]:
    """输出 schema 声明的全部字段名（含嵌套数组元素里的字段）。"""
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            names.add(key)
            names |= _schema_field_names(value)
    elif isinstance(schema, list):
        for value in schema:
            names |= _schema_field_names(value)
    return names


def _parser_read_keys(*functions) -> set[str]:
    """解析函数实际读取的 JSON 字段名（语法树扫 X.get("字面量")，X 限于输出载荷变量）。"""
    import ast
    import inspect
    import textwrap

    keys: set[str] = set()
    for fn in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "get" or not node.args:
                continue
            receiver = node.func.value
            if not (isinstance(receiver, ast.Name) and receiver.id in _OUTPUT_PAYLOAD_VARS):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.add(arg.value)
    return keys


def _parser_lane_cases():
    from app.adapters.llm import (
        LlmElementOperationExecutor,
        LlmElementReviewer,
        LlmSourceElementRecognizer,
        _parse_facets,
    )

    return [
        ("element_recognition", _RECOGNITION_OUTPUT_ITEM, (LlmSourceElementRecognizer.recognize,)),
        ("element_review", _REVIEW_OUTPUT_ITEM, (LlmElementReviewer.review_elements, _parse_facets)),
        ("element_scan", _SCAN_OUTPUT_ITEM, (LlmElementReviewer.scan_missing,)),
        ("element_execution", _EXECUTION_OUTPUT, (LlmElementOperationExecutor.execute,)),
    ]


@pytest.mark.parametrize("lane,schema,parsers", _parser_lane_cases())
def test_parser_read_keys_are_declared_in_output_schema(lane, schema, parsers):
    read_keys = _parser_read_keys(*parsers)
    assert read_keys, f"{lane} 未扫到任何解析读取键（变量命名约定可能已改，见本节注释）"
    allowed = _schema_field_names(schema) | _LEGACY_INPUT_ALIASES.get(lane, frozenset())
    undeclared = read_keys - allowed
    assert not undeclared, f"{lane} 解析器读取了输出 schema 未声明的字段：{sorted(undeclared)}"


def test_recognition_parser_reads_the_verdict_reason_key():
    """裁定理由这一条单独钉死：它是本卡的核心交付，读错键名的后果是界面永远显示「模型没给理由」。"""
    from app.adapters.llm import LlmSourceElementRecognizer

    assert "verdict_reason" in _parser_read_keys(LlmSourceElementRecognizer.recognize)
