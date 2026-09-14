"""工具与工具表。

工具是「能做什么」的静态定义，行动是工具的一次调用。工具表独立于任务定义；
任务定义只用工具名引用工具，不导入本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tod_kernel.kernel import TOOL_SOURCE_PREFIX, ActionStatus, Change, KernelError, Message


@dataclass(frozen=True)
class Tool:
    name: str
    param_names: tuple
    impl: Callable[[Any], None]
    # 以下三项本步只预留，不参与核验。
    writable_slots: tuple | None = None
    preconditions: Any = None
    required_auth: Any = None


class ToolTable:
    """工具名到工具的登记处。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise KernelError(f"工具重复登记：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def items(self) -> list[tuple[str, Tool]]:
        """只读：按登记顺序返回（工具名, 工具）。"""
        return list(self._tools.items())


def ask(ctx) -> None:
    """询问：往发件箱发一条问题，记「等待中」，再到收件箱阻塞取回复这条问题的回答。

    问题的内容是本行动的参数字典原样，工具不加措辞。
    取到字符串回答：填返回值、生成一条变更、记「已成功」。
    取不到（收件箱已关闭且没有匹配的回答）：记「已失败」，由循环记录后抛内核错误。
    回答不是字符串属于程序错误，同样记「已失败」并写明原因。
    """
    action = ctx.action
    slot = action.params["slot"]
    question = ctx.outbox.put(Message(
        kind="question",
        sender=TOOL_SOURCE_PREFIX + action.tool,
        recipient="user",
        in_reply_to=None,
        content=dict(action.params),
        action_id=action.action_id,
    ))
    ctx.set_status(ActionStatus.WAITING, f"已向使用者提问，问题 {question.seq}")
    message = ctx.inbox.take(
        match=lambda m: m.kind == "answer" and m.in_reply_to == question.seq,
        block=True,
        waiter=action.action_id,
    )
    if message is None:
        ctx.set_status(ActionStatus.FAILED, "没有可用的回答")
        return
    if not isinstance(message.content, str):
        ctx.set_status(ActionStatus.FAILED, f"回答不是字符串：{type(message.content).__name__}")
        return
    action.result = message.content
    action.changes = [Change(slot, ctx.data_view.get(slot), message.content, action.action_id)]
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(ActionStatus.SUCCEEDED, "回答到达")


def build_table() -> ToolTable:
    """建本步的工具表：只登记「询问」。"""
    table = ToolTable()
    table.register(Tool(name="ask", param_names=("slot",), impl=ask))
    return table
