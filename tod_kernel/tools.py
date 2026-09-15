"""工具与工具表。

工具是「能做什么」的静态定义，行动是工具的一次调用。工具表分两层：
- 静态工具表（STATIC_TOOLS）：每个工具的工具名、参数名清单、前置条件、可写槽位，不依赖任何任务定义；
  任务定义加载器（taskdef.py）导入它，用来校验工具名与参数名、求前置条件、拼依据说明。
- 运行工具表（build_table 按任务建）：在静态表之上配好工具实现，询问工具绑定任务定义的话语模板，交给内核。
任务定义数据文件里只有工具名，不含任何代码。
工具放进变更组的新值必须是新构造的对象，内核不再复制。
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
    # 可写槽位与前置条件由静态工具表填上；执行控制本步不核验它们，前置条件只在行动选择时用。
    writable_slots: frozenset | None = None  # 可写槽位：槽位名的集合；None 表示由参数决定（询问写入目标所指的槽位）
    preconditions: Any = None  # 前置条件：函数（只读数据, 已求值的参数）→（成立与否, 说明, 命中值）
    required_auth: Any = None  # 本步只预留


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


# ───────────────────────── 告知异常 ─────────────────────────
# 通用工具，对所有任务都登记，不是某个任务的领域工具。行动选择在三种情形下直接构造它的候选，它不走前置条件：
# 一趟走完阶段目标仍未达成；步骤组轮数到「最多」仍未满足「重复直到」；阶段游标越过最后一个阶段而任务未完成。

EXCEPTION_TOOL = "告知异常"
EXCEPTION_PARAM_NAMES = ("阶段", "未达成目标", "步骤现况", "游标", "可选措施")
CURSOR_SLOT = "游标"  # 任务定义加载器加进槽位表的保留槽位；重做本阶段时本工具把它写回
REDO = "重做本阶段"
ABORT_BY_USER = "主动终止"
ABORT_UNABLE = "被动终止"
EXCEPTION_OPTIONS = (REDO, ABORT_BY_USER, ABORT_UNABLE)
# 使用者的选择 → （行动终态, 返回值, 说明）
EXCEPTION_OUTCOMES = {
    REDO: (ActionStatus.SUCCEEDED, "重做", "使用者选择重做本阶段"),
    ABORT_BY_USER: (ActionStatus.FAILED, "主动终止", "使用者主动终止"),
    ABORT_UNABLE: (ActionStatus.FAILED, "被动终止", "任务无法继续，使用者确认终止"),
}


def exception_utterance(params: dict) -> str:
    """告知异常的话：只拼接参数里现成的内容，不经话语生成。

    参数「游标」是 {阶段, 已完成步骤, 说明, 已完成轮数, 是组尾}：游标的三项加上已完成步骤的说明与分组信息。
    句式：阶段『<阶段>』目标未达成：<各未达成目标的文字，以「；」连接>；已完成『<游标所在阶段>』阶段第 <已完成步骤> 步『<说明>』<轮数半句>。
    可选措施：<以「、」连接>。轮数半句：不在组里（已完成轮数为 null）时不写；是组尾写「，该组已完成 r 轮」；
    在组内但不是组尾写「，该组已完成 r 轮，第 r+1 轮进行中」，r 为 0 时写「，该组第 1 轮进行中」。
    游标那一句带阶段名，因为第三种异常里报告的阶段与游标所在的阶段不是同一个；游标所在阶段不是报告的阶段时，
    是阶段游标越过最后一个阶段、报告的阶段被后续阶段破坏，开头改为「阶段『<阶段>』的目标在后续阶段被破坏：」。
    已完成步骤为 null 时：已完成轮数也为 null 是阶段起点，写「在『<游标所在阶段>』阶段起点，尚未执行步骤」；
    已完成轮数不为 null 只有自主规划阶段（没有步骤，轮数是回合数），写「在『<游标所在阶段>』阶段已进行 <已完成轮数> 回合」。
    """
    stage, at = params["阶段"], params["游标"]
    goals = "；".join(goal["文字"] for goal in params["未达成目标"])
    head = f"阶段『{stage}』目标未达成：" if at.get("阶段") == stage else f"阶段『{stage}』的目标在后续阶段被破坏："
    if at.get("已完成步骤") is not None:
        where = f"已完成『{at.get('阶段')}』阶段第 {at.get('已完成步骤')} 步『{at.get('说明')}』"
        where += _rounds_clause(at.get("已完成轮数"), at.get("是组尾"))
    elif at.get("已完成轮数") is None:
        where = f"在『{at.get('阶段')}』阶段起点，尚未执行步骤"
    else:
        where = f"在『{at.get('阶段')}』阶段已进行 {at.get('已完成轮数')} 回合"
    options = "、".join(params["可选措施"])
    return f"{head}{goals}；{where}。可选措施：{options}。"


def _rounds_clause(rounds, group_last) -> str:
    """游标那一句的轮数半句。"""
    if rounds is None:
        return ""
    if group_last:
        return f"，该组已完成 {rounds} 轮"
    if rounds == 0:
        return "，该组第 1 轮进行中"
    return f"，该组已完成 {rounds} 轮，第 {rounds + 1} 轮进行中"


def report_exception(ctx) -> None:
    """告知异常：与询问同族，走发件箱与收件箱。

    往发件箱放问题（内容 = {话, 参数}）→ 记等待中 → 在收件箱阻塞取回复这条问题的回答 → 按选择定终态：
    重做本阶段记已成功（返回值「重做」），变更组把游标写回参数里的阶段起点（已完成步骤与已完成轮数都是 null）；
    主动终止、被动终止记已失败，说明区分两种终止，由循环抛内核错误。
    取不到回答（收件箱已关闭）记已失败；回答不是三个可选措施之一，也记已失败并写明回答原文。
    """
    action = ctx.action
    params = action.params
    question = ctx.outbox.put(Message(
        kind="question",
        sender=TOOL_SOURCE_PREFIX + action.tool,
        recipient="user",
        in_reply_to=None,
        content={"utterance": exception_utterance(params), "params": copy.deepcopy(params)},
        action_id=action.action_id,
    ))
    ctx.set_status(ActionStatus.WAITING, f"已向使用者告知异常，问题 {question.seq}")
    message = ctx.inbox.take(
        match=lambda m: m.kind == "answer" and m.in_reply_to == question.seq,
        block=True,
        waiter=action.action_id,
    )
    if message is None:
        ctx.set_status(ActionStatus.FAILED, "没有可用的回答")
        return
    outcome = EXCEPTION_OUTCOMES.get(message.content) if isinstance(message.content, str) else None
    if outcome is None:
        ctx.set_status(ActionStatus.FAILED, f"回答不是可选措施之一：{message.content}")
        return
    status, result, note = outcome
    action.result = result
    if message.content == REDO:
        old = copy.deepcopy(ctx.data_view.get(CURSOR_SLOT))
        action.changes = [Change(CURSOR_SLOT, old, {"阶段": params["阶段"], "已完成步骤": None, "已完成轮数": None}, action.action_id)]
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(status, note)


# ───────────────────────── 前置条件 ─────────────────────────
# 前置条件是函数，接收只读数据与已求值的参数，返回（成立与否, 说明, 命中值）。
# 说明是与数据无关的固定文字（依据说明在任务开始前就要拼好），动态内容放命中值。
# 前置条件只写「这个工具此刻调用会不会出错」，不写任务要达成什么。

ASK_NOTE = "写入目标指向的位置为 None"
LIST_DIR_NOTE = "文件总表为 None"
REGISTER_FILE_NOTE = "序号等于登记进度且小于文件总数"
GENERATE_MANIFEST_NOTE = "每项是否纳入都不为 None"


def _ask_precondition(data, params):
    target = params.get("target") or {}
    slot, path = target.get("slot"), list(target.get("path") or [])
    node, reachable = data.get(slot), True
    for step in path:
        if isinstance(node, list) and isinstance(step, int) and not isinstance(step, bool) and 0 <= step < len(node):
            node = node[step]
        elif isinstance(node, dict):
            node = node.get(step)
        else:
            node, reachable = None, False
            break
    return reachable and node is None, ASK_NOTE, {"槽位": slot, "路径": path, "当前值": node}


def _list_dir_precondition(data, params):
    return data.get("文件总表") is None, LIST_DIR_NOTE, {"文件总表": data.get("文件总表")}


def _register_file_precondition(data, params):
    files = data.get("文件总表")
    ok = files is not None and params.get("index") == data.get("登记进度") and params.get("index") < len(files)
    hit = {"登记进度": data.get("登记进度"), "文件总数": None if files is None else len(files)}
    return ok, REGISTER_FILE_NOTE, hit


def _generate_manifest_precondition(data, params):
    items = data.get("材料清单") or []
    confirmed = sum(1 for item in items if item["是否纳入"] is not None)
    return all(item["是否纳入"] is not None for item in items), GENERATE_MANIFEST_NOTE, {"已确认项数": confirmed}


# ───────────────────────── 静态工具表与运行工具表 ─────────────────────────

@dataclass(frozen=True)
class ToolSpec:
    """静态工具表的一项：工具名、参数名清单、前置条件、可写槽位。不含实现，不依赖任务定义。"""

    name: str
    param_names: tuple
    preconditions: Any = None
    writable_slots: frozenset | None = None


STATIC_TOOLS = {
    "ask": ToolSpec("ask", ("target", "hint"), _ask_precondition, None),
    "list_dir": ToolSpec("list_dir", (), _list_dir_precondition, frozenset({"文件总表"})),
    "register_file": ToolSpec("register_file", ("index",), _register_file_precondition,
                              frozenset({"材料清单", "登记进度"})),
    "generate_manifest": ToolSpec("generate_manifest", (), _generate_manifest_precondition,
                                  frozenset({"清单文件路径"})),
    EXCEPTION_TOOL: ToolSpec(EXCEPTION_TOOL, EXCEPTION_PARAM_NAMES, None, frozenset()),  # 不走前置条件
}

# 工具实现：工具名 → 函数（任务定义）→ 实现。只有询问要绑定任务定义（读话语模板）。
_IMPLS = {
    "ask": lambda task_def: functools.partial(ask, task_def=task_def),
    "list_dir": lambda task_def: list_dir,
    "register_file": lambda task_def: register_file,
    "generate_manifest": lambda task_def: generate_manifest,
    EXCEPTION_TOOL: lambda task_def: report_exception,
}


def build_table(task_def, tool_names=("ask",)) -> ToolTable:
    """按任务建运行工具表：登记给定名字的工具，另外恒登记「告知异常」。
    工具名、参数名清单、前置条件、可写槽位从静态工具表取；询问工具用 partial 绑定任务定义，
    所以运行工具表与任务绑定，每个任务各建一张。"""
    table = ToolTable()
    names = list(tool_names) + ([] if EXCEPTION_TOOL in tool_names else [EXCEPTION_TOOL])
    for name in names:
        if name not in STATIC_TOOLS:
            raise KernelError(f"没有这个工具：{name!r}")
        spec = STATIC_TOOLS[name]
        table.register(Tool(name=spec.name, param_names=spec.param_names, impl=_IMPLS[name](task_def),
                            writable_slots=spec.writable_slots, preconditions=spec.preconditions))
    return table
