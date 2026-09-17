"""验证脚本的机器检查：告知异常话的循环次数写法。"""

from __future__ import annotations

from tod_kernel.tools import EXCEPTION_OPTIONS
from tod_kernel.verify.base import Checker, banner
from tod_kernel.verify.intake import OPTIONS_TEXT, position


def utterance_round_clause_checks() -> Checker:
    """告知异常的话里当前步那一句的写法：段尾、段内（第 n 次进行中）、第 1 次进行中、不在循环段里、阶段起点。
    现有异常场景只经过段尾与阶段起点，其余写法直接调工具的拼话函数核对。
    第四步 4.10 节起「该组已完成 r 轮」改成「这段循环已完成 n 次」；自主规划阶段那一种写法随「回合」形状留到第五步，这里不再验。"""
    from tod_kernel.tools import exception_utterance

    title = "第三步·告知异常话的循环次数写法"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    base = {"阶段": "登记与确认", "未达成目标": [{"文字": "登记进度 等于 99，当前值 1"}], "步骤现况": [], "可选措施": list(EXCEPTION_OPTIONS)}
    head = "阶段『登记与确认』目标未达成：登记进度 等于 99，当前值 1；"
    cases = [
        ("段尾", position("登记与确认", 3, "询问", (2, 3, 2), True), "已完成『登记与确认』阶段第 3 步『询问』，这段循环已完成 2 次"),
        ("段内、第 2 次进行中", position("登记与确认", 2, "登记", (2, 3, 2), False),
         "已完成『登记与确认』阶段第 2 步『登记』，这段循环已完成 1 次，第 2 次进行中"),
        ("段内、第 1 次进行中", position("登记与确认", 2, "登记", (2, 3, 1), False),
         "已完成『登记与确认』阶段第 2 步『登记』，这段循环第 1 次进行中"),
        ("不在循环段里", position("登记与确认", 1, "列目录", None, None), "已完成『登记与确认』阶段第 1 步『列目录』"),
        ("阶段起点", position("登记与确认", None, None, None, None), "在『登记与确认』阶段起点，尚未执行步骤"),
    ]
    for name, at, where in cases:
        expected = f"{head}{where}。{OPTIONS_TEXT}"
        actual = exception_utterance({**base, "当前步": at})
        c.check(f"{name}：话逐字等于「{expected}」", actual == expected, actual)
    return c
