"""任务定义：出差申请单。

三个槽位（目的地、日期、事由），全部靠询问使用者取得。本模块只用工具名 "ask" 引用工具，
不导入工具表。内核只按属性名访问任务定义：NAME、SLOTS、RULES、is_done、select_action；
询问工具另外读 TEMPLATES 生成对使用者说的话。

依据的约定：选择规则返回的依据是（规则序号, 规则的可读条件, 命中时的数据值）三元组；
内核原样存放、原样放进事件，不拆它的结构。
"""

from __future__ import annotations

import types

# 任务定义名。
NAME = "出差申请单"

# 槽位名到初始值。未填写唯一用 None 表示；空字符串是合法的已填值。
SLOTS = {"目的地": None, "日期": None, "事由": None}

# 选择规则清单：规则序号到可读条件。选择规则的条件文字只从这里取。
RULES = {1: "目的地尚未填写", 2: "日期尚未填写", 3: "事由尚未填写"}

# 话语模板：键是（槽位名, 路径里去掉数字下标后的键组成的元组），值是 str.format 句式，占位符用提示的键。
TEMPLATES = {
    ("目的地", ()): "请提供出差目的地。",
    ("日期", ()): "请提供出差日期。",
    ("事由", ()): "请提供出差事由。",
}

# 每条规则检查的槽位，按规则序号排列。
_RULE_SLOTS = {1: "目的地", 2: "日期", 3: "事由"}


def is_done(data) -> bool:
    """完成条件：三个槽位都不为 None。"""
    return all(data.get(slot) is not None for slot in _RULE_SLOTS.values())


def select_action(data):
    """选择规则：按规则序号依次检查，命中第一个值为 None 的槽位。

    返回（工具名, 参数, 依据）；参数是写入目标（槽位，空路径）与空提示。全部不为 None 时返回 None。
    """
    for number, slot in _RULE_SLOTS.items():
        value = data.get(slot)
        if value is None:
            return ("ask", {"target": {"slot": slot, "path": []}, "hint": {}}, (number, RULES[number], value))
    return None


def variant(initial: dict, name: str) -> types.SimpleNamespace:
    """按给定初始值构造任务定义变体，属性与本模块同名，规则与完成条件不变。"""
    unknown = set(initial) - set(SLOTS)
    if unknown:
        raise ValueError(f"未知槽位：{sorted(unknown)}")
    return types.SimpleNamespace(
        NAME=name,
        SLOTS={**SLOTS, **initial},
        RULES=dict(RULES),
        TEMPLATES=dict(TEMPLATES),
        is_done=is_done,
        select_action=select_action,
    )


# 验证场景用的两份变体。
ALL_FILLED = variant({"目的地": "上海", "日期": "9 月 20 日", "事由": "客户拜访"}, "出差申请单（三项预填）")
DATE_FILLED = variant({"日期": "9 月 20 日"}, "出差申请单（日期预填）")
