"""任务定义：材料接入登记。

使用者给一个目录，系统列目录、逐个登记文件属性、逐个问使用者是否纳入、最后生成清单文件。
本模块只用工具名引用工具（list_dir、register_file、ask、generate_manifest），不导入工具表。
内核只按属性名访问任务定义：NAME、SLOTS、RULES、is_done、select_action；询问工具另外读 TEMPLATES。

依据的约定与出差申请单相同：（规则序号, 规则的可读条件, 命中时的数据值）三元组，内核原样存放。
「空」沿用第零步约定：未填写是 None，空列表和空串都是已填值；「登记进度」用整数，0 是合法起点。
"""

from __future__ import annotations

import types

NAME = "材料接入登记"

# 槽位名到初始值。材料清单的每一项是字典：文件名、类型、大小、页数、是否纳入（初始 None）。
SLOTS = {
    "目录": "样例材料",
    "文件总表": None,
    "材料清单": [],
    "登记进度": 0,
    "清单文件路径": None,
}

# 选择规则清单：规则序号到可读条件。
RULES = {
    1: "文件总表为 None：列目录",
    2: "登记进度小于文件总数：登记文件",
    3: "材料清单里存在「是否纳入」为 None 的项：询问",
    4: "清单文件路径为 None：生成清单文件",
}

# 话语模板：键是（槽位名, 路径里去掉数字下标后的键组成的元组），值是 str.format 句式，占位符用提示的键。
TEMPLATES = {
    ("材料清单", ("是否纳入",)): "请确认是否把文件 {file} 纳入项目。",
}

# 默认的规则检查顺序。
ORDER = (1, 2, 3, 4)


def is_done(data) -> bool:
    """完成条件：文件总表已列出、全部文件已登记、每项是否纳入都已回答、清单文件路径已写。"""
    files = data.get("文件总表")
    items = data.get("材料清单") or []
    return (
        files is not None
        and data.get("登记进度") == len(files)
        and all(item["是否纳入"] is not None for item in items)
        and data.get("清单文件路径") is not None
    )


def _rule_1(data):
    if data.get("文件总表") is None:
        return "list_dir", {}, None
    return None


def _rule_2(data):
    files = data.get("文件总表")
    progress = data.get("登记进度")
    if files is not None and progress < len(files):
        return "register_file", {"index": progress}, {"登记进度": progress, "文件总数": len(files)}
    return None


def _rule_3(data):
    for index, item in enumerate(data.get("材料清单") or []):
        if item["是否纳入"] is None:
            params = {"target": {"slot": "材料清单", "path": [index, "是否纳入"]}, "hint": {"file": item["文件名"]}}
            return "ask", params, {"序号": index, "文件名": item["文件名"], "是否纳入": None}
    return None


def _rule_4(data):
    if data.get("清单文件路径") is None:
        return "generate_manifest", {}, None
    return None


_RULE_FUNCTIONS = {1: _rule_1, 2: _rule_2, 3: _rule_3, 4: _rule_4}


def _select(data, order):
    for number in order:
        hit = _RULE_FUNCTIONS[number](data)
        if hit is not None:
            tool, params, value = hit
            return (tool, params, (number, RULES[number], value))
    return None


def select_action(data):
    """选择规则：按规则一、二、三、四的顺序检查，返回第一条命中的（工具名, 参数, 依据）；都不命中返回 None。"""
    return _select(data, ORDER)


def variant(name: str, order=ORDER) -> types.SimpleNamespace:
    """构造任务定义变体：规则保留原来的序号与条件，只改检查顺序。"""
    if sorted(order) != sorted(ORDER):
        raise ValueError(f"检查顺序必须恰好包含规则 {ORDER}：{order}")
    return types.SimpleNamespace(
        NAME=name,
        SLOTS=dict(SLOTS),
        RULES={number: RULES[number] for number in order},
        TEMPLATES=dict(TEMPLATES),
        is_done=is_done,
        select_action=lambda data: _select(data, order),
    )


# 规则二、三互换的变体：登记一个、问一个。
SWAPPED = variant("材料接入登记（规则二三互换）", order=(1, 3, 2, 4))
