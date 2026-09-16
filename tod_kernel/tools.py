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
from tod_kernel.llm import SHAPE_JSON, SHAPE_TEXT, LLMError, Request, build_system_prompt

# 工具的「类别」：按这个工具与谁打交道分，取值封闭，登记时核对。
# 类别说的是工具对外界做什么，不说它内部用什么实现：用模型写数据的工具仍归「加工任务数据」，
# 因为模型是工具的实现细节，不是工具的交往对象。
CATEGORIES = ("与使用者对话", "访问外部资源", "加工任务数据")


@dataclass(frozen=True)
class Tool:
    name: str
    param_names: tuple
    impl: Callable[[Any], None]
    # 可写槽位与前置条件由静态工具表填上；执行控制本步不核验它们，前置条件只在行动选择时用。
    writable_slots: frozenset | None = None  # 可写槽位：槽位名的集合；None 表示由参数决定（询问写入目标所指的槽位）
    preconditions: Any = None  # 前置条件：函数（只读数据, 已求值的参数）→（成立与否, 说明, 命中值）
    required_auth: Any = None  # 本步只预留
    category: str | None = None  # 类别：CATEGORIES 之一
    summary: str | None = None  # 一句话说明：这个工具做什么、写哪个槽位
    writer_roles: dict | None = None  # 可选：槽位 → 这一槽位的内容实际来自谁（观测台标「谁填的」用）


class ToolTable:
    """工具名到工具的登记处。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise KernelError(f"工具重复登记：{tool.name}")
        if tool.category not in CATEGORIES:
            raise KernelError(f"工具 {tool.name} 的类别不是 {'、'.join(CATEGORIES)} 之一：{tool.category!r}")
        if not tool.summary:
            raise KernelError(f"工具 {tool.name} 没有一句话说明")
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


# ───────────────────────── 术语澄清用的两个工具：内部调用模型 ─────────────────────────
# 模型在这个内核里的第二种使用场景：工具实现内部调模型，回答是数据，写进槽位，对错由使用者确认判。
# 两个工具都无参数、直接读槽位；请求由上下文包（context.py）按段组装，系统提示在建工具表时拼好。
# 下面这些固定文字（两条固定指令、本步说明、输出形状说明）是提示词的一部分，定稿于 2026-09-16：
# 改一个字，请求哈希就变，录制文件全部失效要重录。

DRAFT_TOOL = "生成术语释义"
JUDGE_TOOL = "判读回复"
TERM_SLOT = "术语"
SOURCE_SLOT = "原文片段"
DRAFT_SLOT = "释义草稿"
REPLY_SLOT = "回复"
FEEDBACK_SLOT = "修改意见"
CONFIRMED_SLOT = "确认释义"

# 固定指令：进系统提示的工具目录，任务期间不变，所以不在每次调用的用户内容里重复。
DRAFT_INSTRUCTION = (
    "你是术语解释员。有原文片段时只根据原文片段解释这个术语；没有原文片段时按需求工程语境的通用含义解释。"
    "一到三句话，不引入片段之外的事实。若给了上一稿与修改意见，按修改意见改写上一稿，保留使用者没有要求改的内容。")
JUDGE_INSTRUCTION = (
    "明确同意、或给出一段改写后的完整释义算确认（改写文本填入确认文本）；"
    "提出意见、要求改动算修改（意见原文填入修改意见）。"
    '输出一个 JSON 对象：{"决定": "确认"或"修改", "确认文本": 字符串或 null, "修改意见": 字符串或 null}。')

DRAFT_FIRST_NOTE = "为这个术语写一条释义草稿。"
DRAFT_REVISE_NOTE = "这次有上一稿与修改意见，按修改意见改写上一稿，保留使用者没有要求改的内容。"
DRAFT_SHAPE_TEXT = "只输出释义正文。"
DRAFT_MATERIAL_MISSING_NOTE = "按通用含义解释"

JUDGE_SHAPE_TEXT = '{"决定": "确认"|"修改", "确认文本": 字符串或 null, "修改意见": 字符串或 null}'
# 输出形状不只写在提示词里，同时作为接口参数交给模型服务，两道保险防着系统提示里几个工具的指令互相串。
JUDGE_JSON_SCHEMA = {
    "type": "object",
    "properties": {"决定": {"type": "string", "enum": ["确认", "修改"]},
                   "确认文本": {"type": ["string", "null"]},
                   "修改意见": {"type": ["string", "null"]}},
    "required": ["决定", "确认文本", "修改意见"],
    "additionalProperties": False,
}
JUDGE_CONFIRM = "确认"
JUDGE_REVISE = "修改"


def _pack(task_def, ctx, read_events):
    """建这次调用的上下文包。事件由宿主给的「读事件」函数提供，内核不为此改动。"""
    from tod_kernel.context import ContextPack  # 函数内导入：context 要读任务定义，顶层导会与加载器绕回来

    return ContextPack.build(task_def, dict(ctx.data_view), read_events() if read_events else [], step=ctx.step)


def _draft_count(pack) -> int:
    """已经写过几稿：数事件流里「释义草稿」被写过几次。进度段里说「第几稿」用它。

    数事件而不是数修订记录那段文字，免得草稿正文恰好以「第 」开头时数错。
    """
    from tod_kernel.kernel import DATA_CHANGED

    return sum(1 for event in pack.events
               if event.name == DATA_CHANGED and event.payload["slot"] == DRAFT_SLOT
               and event.payload["new"] is not None)


def draft_definition(ctx, call, task_def=None, read_events=None, system_prompt="") -> None:
    """生成术语释义：首稿与改稿同一个实现，按「修改意见」是不是空分支。

    首稿读「术语」与可选的「原文片段」；改稿另读上一稿与「修改意见」，写完把「修改意见」清空。
    回答去掉首尾空白后写进「释义草稿」；行动的返回值是这次模型调用的完整记录。
    模型调用失败（模型服务不可达、回放时录制文件里查不到）时记已失败，说明就是那条错误的一句话。
    """
    from tod_kernel.context import ContextPack, SEG_ORDER, render  # 同上，函数内导入

    action = ctx.action
    pack = _pack(task_def, ctx, read_events)
    feedback = ctx.data_view.get(FEEDBACK_SLOT)
    revising = feedback is not None
    drafts = _draft_count(pack)
    if revising:
        progress_note = f"第 {drafts} 稿被要求修改，本次写第 {drafts + 1} 稿。"
        segments = [pack.progress(progress_note),
                    pack.dialogue(scope=("slots", [REPLY_SLOT])),
                    pack.data_segment([TERM_SLOT, (DRAFT_SLOT, "（上一稿）"), FEEDBACK_SLOT],
                                      revision=(DRAFT_SLOT, FEEDBACK_SLOT)),
                    pack.material(SOURCE_SLOT),
                    ContextPack.step(DRAFT_TOOL, DRAFT_REVISE_NOTE),
                    ContextPack.shape(SHAPE_TEXT, DRAFT_SHAPE_TEXT)]
    else:
        segments = [pack.progress(f"本次写第 {drafts + 1} 稿。"),
                    pack.dialogue(),
                    pack.data_segment([TERM_SLOT]),
                    pack.material(SOURCE_SLOT, DRAFT_MATERIAL_MISSING_NOTE),
                    ContextPack.step(DRAFT_TOOL, DRAFT_FIRST_NOTE),
                    ContextPack.shape(SHAPE_TEXT, DRAFT_SHAPE_TEXT)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_TEXT)
    try:
        reply = call(request)
    except LLMError as exc:
        ctx.set_status(ActionStatus.FAILED, exc.brief)
        return
    draft = reply.text.strip()
    changes = [Change(DRAFT_SLOT, copy.deepcopy(ctx.data_view.get(DRAFT_SLOT)), draft, action.action_id)]
    if revising:  # 意见已经落实到这一稿里，清空，否则下一次循环又会被当成待改
        changes.append(Change(FEEDBACK_SLOT, copy.deepcopy(feedback), None, action.action_id))
    action.result = _call_record(reply, segments, {"（文本）": DRAFT_SLOT}, draft)
    action.changes = changes
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(ActionStatus.SUCCEEDED, f"模型写出第 {drafts + 1} 稿，{len(draft)} 字")


def judge_reply(ctx, call, task_def=None, read_events=None, system_prompt="") -> None:
    """判读回复：使用者看过草稿后的那句话是确认还是修改，输出形状是 JSON。

    判为确认就把确认文本写进「确认释义」，判为修改就把修改意见写进「修改意见」，两种都把「回复」清空。
    解析不出对象、决定不在两个取值内、确认时确认文本为空、修改时修改意见为空，四种都记已失败并带上模型原文；
    本步不重试，循环必须显式有界。
    """
    from tod_kernel.context import ContextPack, render

    action = ctx.action
    pack = _pack(task_def, ctx, read_events)
    segments = [pack.progress(f"已有第 {_draft_count(pack)} 稿。"),
                pack.dialogue(),
                pack.data_segment([DRAFT_SLOT, REPLY_SLOT]),
                ContextPack.step(JUDGE_TOOL),
                ContextPack.shape(SHAPE_JSON, JUDGE_SHAPE_TEXT, api_note=True)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_JSON, json_schema=JUDGE_JSON_SCHEMA)
    try:
        reply = call(request)
    except LLMError as exc:
        ctx.set_status(ActionStatus.FAILED, exc.brief)
        return
    parsed, problem = _parse_judgement(reply.text)
    if problem is not None:
        action.result = _call_record(reply, segments, {}, None)
        ctx.set_status(ActionStatus.FAILED, f"{problem}：{reply.text.strip()[:200]}")
        return
    decision = parsed["决定"]
    old_reply = copy.deepcopy(ctx.data_view.get(REPLY_SLOT))
    if decision == JUDGE_CONFIRM:
        writes = {"决定": None, "确认文本": CONFIRMED_SLOT, "修改意见": None}
        changes = [Change(CONFIRMED_SLOT, copy.deepcopy(ctx.data_view.get(CONFIRMED_SLOT)),
                          parsed["确认文本"].strip(), action.action_id)]
    else:
        writes = {"决定": None, "确认文本": None, "修改意见": FEEDBACK_SLOT}
        changes = [Change(FEEDBACK_SLOT, copy.deepcopy(ctx.data_view.get(FEEDBACK_SLOT)),
                          parsed["修改意见"].strip(), action.action_id)]
    changes.append(Change(REPLY_SLOT, old_reply, None, action.action_id))  # 判读过了就清空，下一次循环才好再问
    action.result = _call_record(reply, segments, writes, parsed)
    action.changes = changes
    ctx.set_status(ActionStatus.SUCCEEDED, f"判为「{decision}」")


def _parse_judgement(text):
    """把模型回的 JSON 解析成判读结果。返回（结果, 毛病）：毛病为 None 表示解析通过。"""
    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "模型回的不是 JSON"
    if not isinstance(parsed, dict):
        return None, "模型回的 JSON 不是一个对象"
    decision = parsed.get("决定")
    if decision not in (JUDGE_CONFIRM, JUDGE_REVISE):
        return None, f"「决定」不是「{JUDGE_CONFIRM}」或「{JUDGE_REVISE}」"
    if decision == JUDGE_CONFIRM and not (isinstance(parsed.get("确认文本"), str) and parsed["确认文本"].strip()):
        return None, "判为确认却没有给确认文本"
    if decision == JUDGE_REVISE and not (isinstance(parsed.get("修改意见"), str) and parsed["修改意见"].strip()):
        return None, "判为修改却没有给修改意见"
    return parsed, None


def _call_record(reply, segments, writes, parsed) -> dict:
    """模型调用记录：调用件记的那几项，加上段列表、解析结果、每项写到了哪个槽位。

    段的「变了没有」要与上一次调用比，本步由观测台按相邻两次调用的段自行比对，记录里不预先算。
    """
    record = dict(reply.record)
    record["segments"] = [segment.as_record() for segment in segments]
    record["parsed"] = parsed
    record["writes"] = {key: slot for key, slot in writes.items() if slot}
    return record


# ───────────────────────── 告知异常 ─────────────────────────
# 通用工具，对所有任务都登记，不是某个任务的领域工具。行动选择在三种情形下直接构造它的候选，它不走前置条件：
# 一趟走完阶段目标仍未达成；循环段次数到「最多」仍未满足「重复直到」；阶段越过最后一个而任务未完成。

EXCEPTION_TOOL = "告知异常"
EXCEPTION_PARAM_NAMES = ("阶段", "未达成目标", "步骤现况", "当前步", "可选措施")
REDO = "重做本阶段"  # 给使用者看的措施名
REDO_RESULT = "重做"  # 行动的返回值；任务定义据它把当前步退回该阶段起点
ABORT_BY_USER = "主动终止"
ABORT_UNABLE = "被动终止"
EXCEPTION_OPTIONS = (REDO, ABORT_BY_USER, ABORT_UNABLE)
# 使用者的选择 → （行动终态, 返回值, 说明）
EXCEPTION_OUTCOMES = {
    REDO: (ActionStatus.SUCCEEDED, REDO_RESULT, "使用者选择重做本阶段"),
    ABORT_BY_USER: (ActionStatus.FAILED, "主动终止", "使用者主动终止"),
    ABORT_UNABLE: (ActionStatus.FAILED, "被动终止", "任务无法继续，使用者确认终止"),
}


def exception_utterance(params: dict) -> str:
    """告知异常的话：只拼接参数里现成的内容，不经话语生成。

    参数「当前步」是 {阶段, 步骤, 说明, 循环, 是段尾}：当前步的三层加上这一步的说明与分段信息，
    其中「步骤」是阶段内序号（从 1 起），「循环」是 {起, 止, 第几次} 或 null。
    句式：阶段『<阶段>』目标未达成：<各未达成目标的文字，以「；」连接>；已完成『<当前步所在阶段>』阶段第 <步骤> 步『<说明>』<循环半句>。
    可选措施：<以「、」连接>。循环半句见 _loop_clause。
    当前步那一句带阶段名，因为第三种异常里报告的阶段与当前步所在的阶段不是同一个；两者不同时，
    是阶段越过了最后一个、报告的阶段被后续阶段破坏，开头改为「阶段『<阶段>』的目标在后续阶段被破坏：」。
    「步骤」为 null 是阶段起点，写「在『<当前步所在阶段>』阶段起点，尚未执行步骤」。
    """
    stage, at = params["阶段"], params["当前步"]
    goals = "；".join(goal["文字"] for goal in params["未达成目标"])
    head = f"阶段『{stage}』目标未达成：" if at.get("阶段") == stage else f"阶段『{stage}』的目标在后续阶段被破坏："
    if at.get("步骤") is not None:
        where = f"已完成『{at.get('阶段')}』阶段第 {at.get('步骤')} 步『{at.get('说明')}』"
        where += _loop_clause(at.get("循环"), at.get("是段尾"))
    else:
        where = f"在『{at.get('阶段')}』阶段起点，尚未执行步骤"
    options = "、".join(params["可选措施"])
    return f"{head}{goals}；{where}。可选措施：{options}。"


def _loop_clause(loop, loop_last) -> str:
    """当前步那一句的循环半句：不在循环段里不写；停在段尾写「，这段循环已完成 n 次」；
    停在段中写「，这段循环已完成 n-1 次，第 n 次进行中」，n 为 1 时写「，这段循环第 1 次进行中」。"""
    if not isinstance(loop, dict) or loop.get("第几次") is None:
        return ""
    nth = loop["第几次"]
    if loop_last:
        return f"，这段循环已完成 {nth} 次"
    if nth <= 1:
        return "，这段循环第 1 次进行中"
    return f"，这段循环已完成 {nth - 1} 次，第 {nth} 次进行中"


def report_exception(ctx) -> None:
    """告知异常：与询问同族，走发件箱与收件箱。

    往发件箱放问题（内容 = {话, 参数}）→ 记等待中 → 在收件箱阻塞取回复这条问题的回答 → 按选择定终态：
    重做本阶段记已成功（返回值「重做」），本工具不写任何位置，当前步由任务定义的「记录本步」按这个返回值退回阶段起点；
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
DRAFT_DEFINITION_NOTE = "术语不为 None，且（释义草稿为 None 或修改意见不为 None）"
JUDGE_REPLY_NOTE = "回复不为 None"


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


def _draft_definition_precondition(data, params):
    """首稿与改稿共用一个条件：有术语，而且要么还没有草稿（写首稿），要么有修改意见（改稿）。

    原文片段为空不是障碍，它只是可选的上下文：系统不向使用者索要原文片段，那是系统自己的功课。
    """
    term, source = data.get(TERM_SLOT), data.get(SOURCE_SLOT)
    draft, feedback = data.get(DRAFT_SLOT), data.get(FEEDBACK_SLOT)
    ok = term is not None and (draft is None or feedback is not None)
    return ok, DRAFT_DEFINITION_NOTE, {TERM_SLOT: term, SOURCE_SLOT: source,
                                       DRAFT_SLOT: draft, FEEDBACK_SLOT: feedback}


def _judge_reply_precondition(data, params):
    reply, draft = data.get(REPLY_SLOT), data.get(DRAFT_SLOT)
    return reply is not None, JUDGE_REPLY_NOTE, {REPLY_SLOT: reply, DRAFT_SLOT: draft}


# ───────────────────────── 静态工具表与运行工具表 ─────────────────────────

@dataclass(frozen=True)
class ToolSpec:
    """静态工具表的一项：工具名、参数名清单、前置条件、可写槽位、类别、一句话说明。

    不含实现，不依赖任务定义。类别与一句话说明是给人和给模型看的静态文字，
    与前置条件的说明是两样东西：一句话说明讲这个工具做什么，前置条件讲此刻能不能调它。
    """

    name: str
    param_names: tuple
    preconditions: Any = None
    writable_slots: frozenset | None = None
    category: str | None = None
    summary: str | None = None
    # 可选映射：这个工具写进某个槽位的内容，实际出自谁。默认按工具类别推（与使用者对话＝使用者，
    # 内部调模型＝模型，其余＝系统）；有些工具写进去的东西不是自己产出的，就在这里写明白。
    writer_roles: dict | None = None


STATIC_TOOLS = {
    "ask": ToolSpec("ask", ("target", "hint"), _ask_precondition, None,
                    category="与使用者对话",
                    summary="问使用者一个问题，把回答写进写入目标所指的槽位"),
    "list_dir": ToolSpec("list_dir", (), _list_dir_precondition, frozenset({"文件总表"}),
                         category="访问外部资源",
                         summary="列出目录里的文件名，写入文件总表"),
    "register_file": ToolSpec("register_file", ("index",), _register_file_precondition,
                              frozenset({"材料清单", "登记进度"}),
                              category="访问外部资源",
                              summary="登记序号所指文件的名字、类型、大小、页数，写入材料清单并把登记进度加一"),
    "generate_manifest": ToolSpec("generate_manifest", (), _generate_manifest_precondition,
                                  frozenset({"清单文件路径"}),
                                  category="访问外部资源",
                                  summary="把材料清单里纳入的项拼成清单文件的内容，写入清单文件路径"),
    DRAFT_TOOL: ToolSpec(DRAFT_TOOL, (), _draft_definition_precondition,
                         frozenset({DRAFT_SLOT, FEEDBACK_SLOT}),
                         category="加工任务数据",
                         summary="根据术语（与原文片段，若有）写一条释义草稿，有修改意见时按意见改稿，写入释义草稿"),
    JUDGE_TOOL: ToolSpec(JUDGE_TOOL, (), _judge_reply_precondition,
                         frozenset({CONFIRMED_SLOT, FEEDBACK_SLOT, REPLY_SLOT}),
                         category="加工任务数据",
                         summary="判断使用者对草稿的回复是确认还是修改，写入确认释义或修改意见",
                         # 确认释义是使用者的裁定，模型只是把它从那句回复里认出来，所以标「使用者」而不是「模型」。
                         writer_roles={CONFIRMED_SLOT: "使用者"}),
    EXCEPTION_TOOL: ToolSpec(EXCEPTION_TOOL, EXCEPTION_PARAM_NAMES, None, frozenset(),  # 不走前置条件
                             category="与使用者对话",
                             summary="任务无法继续时告知使用者原因，请使用者在重做本阶段、主动终止、被动终止里选一个"),
}

# 工具实现：工具名 → 函数（任务定义）→ 实现。只有询问要绑定任务定义（读话语模板）。
_IMPLS = {
    "ask": lambda task_def: functools.partial(ask, task_def=task_def),
    "list_dir": lambda task_def: list_dir,
    "register_file": lambda task_def: register_file,
    "generate_manifest": lambda task_def: generate_manifest,
    DRAFT_TOOL: lambda task_def: draft_definition,  # 模型调用件、读事件函数与系统提示由 build_table 另外绑上
    JUDGE_TOOL: lambda task_def: judge_reply,
    EXCEPTION_TOOL: lambda task_def: report_exception,
}


# 内部调用模型的工具，与它们进系统提示工具目录的固定指令。
MODEL_INSTRUCTIONS = {DRAFT_TOOL: DRAFT_INSTRUCTION, JUDGE_TOOL: JUDGE_INSTRUCTION}

CATALOG_HEAD = "本任务可用的工具（来自任务定义引用的工具，取自静态工具表）："


def tool_catalog(tool_names) -> str:
    """工具目录：这个任务引用的每个工具一行，内部调用模型的附上它的固定指令。进系统提示，任务期间不变。

    整份目录都进去（不只是模型实现的那几个），第五步自主规划选工具看的是同一份。
    """
    lines = [CATALOG_HEAD]
    for name in tool_names:
        spec = STATIC_TOOLS[name]
        kind = f"{spec.category}，内部调用模型" if name in MODEL_INSTRUCTIONS else spec.category
        line = f"· {name}（{kind}）：{spec.summary}。"
        if spec.param_names:
            line += f"参数：{'、'.join(spec.param_names)}。"
        if name in MODEL_INSTRUCTIONS:
            line += f"固定指令：{MODEL_INSTRUCTIONS[name]}"
        lines.append(line)
    return "\n".join(lines)


def system_prompt_for(task_def, tool_names) -> str:
    """这次任务的系统提示：六段，任务启动时拼一次，任务期间不变。

    三段固定文字在模型调用件里，任务定义摘要由加载器生成，工具目录由本模块生成，领域规矩是任务定义的可选块。
    """
    from tod_kernel.taskdef import domain_rules_of, summary_text  # 函数内导入：加载器导入本模块，顶层导会绕回来

    return build_system_prompt(task_def.NAME, summary_text(task_def), tool_catalog(tool_names),
                               domain_rules_of(task_def))


def build_table(task_def, tool_names=("ask",), call=None, read_events=None) -> ToolTable:
    """按任务建运行工具表：登记给定名字的工具，另外恒登记「告知异常」。

    工具名、参数名清单、前置条件、可写槽位、类别、一句话说明从静态工具表取；询问工具用 partial 绑定任务定义，
    两个内部调用模型的工具另外绑上模型调用件、任务定义、读事件函数与这次任务的系统提示，
    所以运行工具表与任务绑定，每个任务各建一张。
    call 是 llm.make_caller 做出来的函数，read_events 是宿主给的「把至今的事件读出来」的函数（上下文包要用），
    两者都由程序入口（验证脚本、控制台）准备；任务用到这两个工具而没给 call 时，在这里就报错，不等到跑起来才失败。
    read_events 没给时上下文包按「没有事件」处理，对话历史与修订记录会是空的。
    """
    table = ToolTable()
    names = list(tool_names) + ([] if EXCEPTION_TOOL in tool_names else [EXCEPTION_TOOL])
    system_prompt = system_prompt_for(task_def, names) if any(name in MODEL_INSTRUCTIONS for name in names) else ""
    for name in names:
        if name not in STATIC_TOOLS:
            raise KernelError(f"没有这个工具：{name!r}")
        spec = STATIC_TOOLS[name]
        impl = _IMPLS[name](task_def)
        if name in MODEL_INSTRUCTIONS:
            if call is None:
                raise KernelError(f"任务用到工具 {name!r}，但建工具表时没有给模型调用件（build_table 的 call 参数）")
            impl = functools.partial(impl, call=call, task_def=task_def, read_events=read_events,
                                     system_prompt=system_prompt)
        table.register(Tool(name=spec.name, param_names=spec.param_names, impl=impl,
                            writable_slots=spec.writable_slots, preconditions=spec.preconditions,
                            category=spec.category, summary=spec.summary, writer_roles=spec.writer_roles))
    return table
