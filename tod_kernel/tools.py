"""工具与工具表。

工具是「能做什么」的静态定义，行动是工具的一次调用。工具表独立于任务定义；
任务定义只用工具名引用工具，不导入本模块。
"""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass
from typing import Any, Callable

from tod_kernel.dialogue import generate_utterance, understand_answer
from tod_kernel.kernel import TOOL_SOURCE_PREFIX, ActionStatus, Change, KernelError, Message


@dataclass(frozen=True)
class Tool:
    name: str
    param_names: tuple
    impl: Callable[[Any], None]
    # 以下三项本步只预留，不参与核验。
    writable_slots: frozenset | None = None  # 可写槽位：槽位名的集合
    preconditions: Any = None  # 前置条件：函数（只读数据, 参数）→（成立与否, 说明）
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


def set_at(value, path, new):
    """按路径写值：深拷贝 value，沿 path 改一处为 new，返回整个新值；value 本身不被改动。

    path 是列表下标与字典键交替的列表；path 为空表示整个槽位，直接返回 new 的深拷贝。
    """
    if not path:
        return copy.deepcopy(new)
    result = copy.deepcopy(value)
    node = result
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = copy.deepcopy(new)
    return result


def ask(ctx, task_def) -> None:
    """询问：一条流水线，自己不认识任何数据结构。

    取参数里的写入目标与提示 → 话语生成得到话 → 往发件箱放问题（内容 = {话, 参数}）→ 记等待中
    → 在收件箱阻塞取回复这条问题的回答 → 回答理解得到值 → 按写入目标的路径算出槽位的新整值
    → 变更一条「槽位：旧整值 → 新整值」→ 记已成功。
    取不到回答（收件箱已关闭且没有匹配的回答）：记「已失败」，由循环记录后抛内核错误。
    回答不是字符串属于程序错误，同样记「已失败」并写明原因。
    task_def 由建工具表时用 partial 绑定，执行上下文里没有它。
    """
    action = ctx.action
    params = action.params
    target = params["target"]
    slot, path = target["slot"], list(target.get("path", []))
    utterance = generate_utterance(task_def, params)
    question = ctx.outbox.put(Message(
        kind="question",
        sender=TOOL_SOURCE_PREFIX + action.tool,
        recipient="user",
        in_reply_to=None,
        content={"utterance": utterance, "params": copy.deepcopy(params)},
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
    value = understand_answer(task_def, params, message.content)
    old = copy.deepcopy(ctx.data_view.get(slot))
    action.result = value
    action.changes = [Change(slot, old, set_at(old, path, value), action.action_id)]
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(ActionStatus.SUCCEEDED, "回答到达")


# ───────────────────────── 材料接入登记用的领域工具 ─────────────────────────
# 这三个工具认识材料接入登记的槽位与材料清单项的键，是领域工具；内核仍不认识它们。
# 样例表放在工具一侧而不是任务定义里，因为工具不导入任务定义；任务定义只给目录名。

SAMPLE_DIRS = {
    "样例材料": [
        {"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3},
        {"文件名": "b.pdf", "类型": "pdf", "大小": 51200, "页数": 5},
        {"文件名": "c.xlsx", "类型": "xlsx", "大小": 10240, "页数": 1},
    ],
}

MANIFEST_PATH = "样例材料/材料清单.txt"  # 固定路径字符串，不落盘
INCLUDED = "是"  # 回答理解本步原样返回，所以这里认的是回答原文「是」


def list_dir(ctx) -> None:
    """列目录：从只读数据的「目录」槽位取目录名，查样例表，把文件名列表写进「文件总表」。"""
    action = ctx.action
    directory = ctx.data_view.get("目录")
    if directory not in SAMPLE_DIRS:
        ctx.set_status(ActionStatus.FAILED, f"样例表里没有目录：{directory!r}")
        return
    names = [entry["文件名"] for entry in SAMPLE_DIRS[directory]]
    action.result = names
    action.changes = [Change("文件总表", copy.deepcopy(ctx.data_view.get("文件总表")), names, action.action_id)]
    ctx.set_status(ActionStatus.SUCCEEDED, f"列出 {len(names)} 个文件")


def register_file(ctx) -> None:
    """登记文件：按参数序号从样例表取该文件的类型、大小、页数，
    给「材料清单」追加一项（是否纳入为 None），「登记进度」加一。"""
    action = ctx.action
    index = action.params["index"]
    entries = SAMPLE_DIRS.get(ctx.data_view.get("目录"), [])
    if not 0 <= index < len(entries):
        ctx.set_status(ActionStatus.FAILED, f"样例表里没有序号 {index} 的文件")
        return
    entry = entries[index]
    old_list = copy.deepcopy(ctx.data_view.get("材料清单"))
    item = {**entry, "是否纳入": None}
    old_progress = ctx.data_view.get("登记进度")
    action.result = copy.deepcopy(item)
    action.changes = [
        Change("材料清单", old_list, old_list + [item], action.action_id),
        Change("登记进度", old_progress, old_progress + 1, action.action_id),
    ]
    ctx.set_status(ActionStatus.SUCCEEDED, f"登记文件 {entry['文件名']}")


def generate_manifest(ctx) -> None:
    """生成清单文件：把「材料清单」里纳入的项拼成固定格式的文本作为返回值，
    把固定路径写进「清单文件路径」。不落盘。"""
    action = ctx.action
    items = ctx.data_view.get("材料清单") or []
    included = [item for item in items if item["是否纳入"] == INCLUDED]
    lines = [f"材料清单（共 {len(included)} 项）"]
    lines += [f"{n}. {item['文件名']}，{item['类型']}，{item['大小']} 字节，{item['页数']} 页"
              for n, item in enumerate(included, start=1)]
    action.result = "\n".join(lines)
    action.changes = [Change("清单文件路径", copy.deepcopy(ctx.data_view.get("清单文件路径")), MANIFEST_PATH, action.action_id)]
    ctx.set_status(ActionStatus.SUCCEEDED, f"清单含 {len(included)} 项，写到 {MANIFEST_PATH}")


# 可写槽位与前置条件：按第一步文档填上，本步没有代码读它们，留给执行控制的真实核验。
# 前置条件是函数，接收只读数据与参数，返回（成立与否, 说明）。
def _list_dir_precondition(data, params):
    return data.get("文件总表") is None, "文件总表为 None"


def _register_file_precondition(data, params):
    files = data.get("文件总表")
    ok = files is not None and params.get("index") == data.get("登记进度") and params.get("index") < len(files)
    return ok, "序号等于登记进度且小于文件总数"


def _generate_manifest_precondition(data, params):
    items = data.get("材料清单") or []
    return all(item["是否纳入"] is not None for item in items), "每项是否纳入都不为 None"


def build_table(task_def, tool_names=("ask",)) -> ToolTable:
    """按任务建工具表：只登记给定名字的工具。询问工具用 partial 绑定任务定义，
    所以工具表与任务绑定，每个任务各建一张。"""
    available = {
        "ask": lambda: Tool(name="ask", param_names=("target", "hint"), impl=functools.partial(ask, task_def=task_def)),
        "list_dir": lambda: Tool(name="list_dir", param_names=(), impl=list_dir,
                                 writable_slots=frozenset({"文件总表"}), preconditions=_list_dir_precondition),
        "register_file": lambda: Tool(name="register_file", param_names=("index",), impl=register_file,
                                      writable_slots=frozenset({"材料清单", "登记进度"}),
                                      preconditions=_register_file_precondition),
        "generate_manifest": lambda: Tool(name="generate_manifest", param_names=(), impl=generate_manifest,
                                          writable_slots=frozenset({"清单文件路径"}),
                                          preconditions=_generate_manifest_precondition),
    }
    table = ToolTable()
    for name in tool_names:
        if name not in available:
            raise KernelError(f"没有这个工具：{name!r}")
        table.register(available[name]())
    return table
