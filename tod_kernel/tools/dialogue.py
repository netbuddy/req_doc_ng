"""对话端口上的系统工具：询问（ask）、告知（notify）、告知异常（report_exception），以及它们的话语拼装。"""

from __future__ import annotations

import copy
import functools

from tod_kernel.dialogue import generate_utterance, understand_answer
from tod_kernel.kernel import TOOL_SOURCE_PREFIX, CallStatus, Change, Message
from tod_kernel.tools.base import ToolSpec, set_at
from tod_kernel.tools.understanding import LAST_QUESTION_SLOT, ORIGINAL_QUESTION, READ_VALUE


def ask(ctx, task_def, read_events=None) -> None:
    """询问：一条流水线，自己不认识任何数据结构。

    取参数里的写入目标与提示 → 话语生成得到话 → 往发件箱放问题（内容 = {话, 参数}）→ 记等待中
    → 在收件箱阻塞取回复这条问题的回答 → 回答理解得到值 → 按写入目标的路径算出槽位的新整值
    → 变更一条「槽位：旧整值 → 新整值」→ 记已成功。
    取不到回答（收件箱已关闭且没有匹配的回答）：记「已失败」，由循环记录后抛内核错误。
    回答不是字符串属于程序错误，同样记「已失败」并写明原因。
    task_def 由建工具表时用 partial 绑定，执行上下文里没有它。

    第五步起询问还做两件事（第五步 4.8、4.12 节）：
    - 参数带 text 时直接说这句，不查话语模板（对话模式里的澄清与求证那一问用它）；
    - 参数带 type 时登记「上一问」：类型、槽位与路径（取参数 about）、选项、采纳到，参数带 original 时另记「原问」；
      另记「念的值」：这一问念的是 about 所指位置的哪个值，是给下一次再问比对用的副本（上一问读后会被清空）。
    - 再问同一个问题只说短句（4.12 节实施裁定第 11 条）：没给 text、带 type，且本次要登记的上一问与事件流里
      最近一次同类型、同槽位、同路径的登记念的值相同时，话语换成目标槽位上的「再问短句」，没写就用 REASK_LINE。
      read_events 是宿主给的读事件函数，没给时找不到上一次登记，照常全文念。
    """
    call = ctx.call
    params = call.params
    target = params["target"]
    slot, path = target["slot"], list(target.get("path", []))
    text = params.get("text")
    about = params.get("about") or {}
    reading = _value_at(ctx.data_view.get(about.get("slot")), about.get("path") or []) if about else None
    if isinstance(text, str) and text:
        utterance = text
    elif params.get("type") is not None and _same_as_last_question(read_events, params, reading):
        utterance = _reask_line(task_def, target)
    else:
        utterance = generate_utterance(task_def, params)
    question = ctx.outbox.put(Message(
        kind="question",
        sender=TOOL_SOURCE_PREFIX + call.tool,
        recipient="user",
        in_reply_to=None,
        content={"utterance": utterance, "params": copy.deepcopy(params)},
        call_id=call.call_id,
    ))
    ctx.set_status(CallStatus.WAITING, f"已向使用者提问，问题 {question.seq}")
    message = ctx.inbox.take(
        match=lambda m: m.kind == "answer" and m.in_reply_to == question.seq,
        block=True,
        waiter=call.call_id,
    )
    if message is None:
        ctx.set_status(CallStatus.FAILED, "没有可用的回答")
        return
    if not isinstance(message.content, str):
        ctx.set_status(CallStatus.FAILED, f"回答不是字符串：{type(message.content).__name__}")
        return
    value = understand_answer(task_def, params, message.content)
    old = copy.deepcopy(ctx.data_view.get(slot))
    call.result = value
    changes = [Change(slot, old, set_at(old, path, value), call.call_id)]
    if params.get("type") is not None:
        question = {"类型": params["type"], "槽位": about.get("slot"), "路径": list(about.get("path") or []),
                    "选项": copy.deepcopy(params.get("options")), "采纳到": params.get("adopt_to"),
                    READ_VALUE: copy.deepcopy(reading)}
        if params.get("original") is not None:
            question[ORIGINAL_QUESTION] = copy.deepcopy(params["original"])
        changes.append(Change(LAST_QUESTION_SLOT, copy.deepcopy(ctx.data_view.get(LAST_QUESTION_SLOT)), question,
                              call.call_id))
    call.changes = changes
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(CallStatus.SUCCEEDED, "回答到达")


REASK_TEMPLATE_KEY = "再问短句"  # 槽位元数据里的可选键，与「提问」并列
REASK_LINE = "回到刚才那一稿：请确认，或提出修改意见。"  # 任务定义没写再问短句时用这句


def _value_at(value, path):
    """沿路径取值；路径走不通返回 None。"""
    node = value
    for step in path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def _last_same_question(read_events, kind, slot, path):
    """事件流里最近一次同类型、同槽位、同路径的上一问登记（数据变更里「上一问」的非空新值）；没有返回 None。

    中间隔着别的登记（例如澄清段里那一问是选择类）不影响，只看同一个问题最近那次（4.12 节实施裁定第 11 条）。
    """
    from tod_kernel.kernel import DATA_CHANGED

    for event in reversed(read_events() if read_events else []):
        new = event.payload.get("new") if event.name == DATA_CHANGED else None
        if event.payload.get("slot") == LAST_QUESTION_SLOT and isinstance(new, dict) \
                and new.get("类型") == kind and new.get("槽位") == slot and list(new.get("路径") or []) == path:
            return new
    return None


def _same_as_last_question(read_events, params, reading) -> bool:
    """再问同一个问题：有同类型、同槽位、同路径的登记，且最近那次念的值与这次要念的相同。"""
    about = params.get("about") or {}
    last = _last_same_question(read_events, params.get("type"), about.get("slot"), list(about.get("path") or []))
    return last is not None and READ_VALUE in last and last[READ_VALUE] == reading


def _reask_line(task_def, target) -> str:
    """再问短句：目标槽位（或列表项字段）上写了就用它，没写用固定文字。"""
    from tod_kernel.dialogue import template_key

    templates = getattr(task_def, "TEMPLATES", {}) or {}
    return templates.get(template_key(target) + (REASK_TEMPLATE_KEY,)) or REASK_LINE


# ───────────────────────── 对话模式用的三个系统工具（第五步 4.12 节）─────────────────────────
# 对话模式（答疑、澄清、推迟、求证）是系统级的小任务定义，写在 task_defs/patterns.json。模式里除了询问与对话理解，
# 另用这三个工具：告知（只说一句，不等回答）、标记推迟（在目标槽位写已推迟标记）、答疑（内部调模型，一两句白话解释）。

NOTIFY_TOOL = "告知"
NOTICE_KIND = "notice"  # 告知消息的种类：放进发件箱，不等回答


def notify(ctx) -> None:
    """告知：往发件箱放一条种类为告知的消息（内容 = {话, 参数}），不等回答，记已成功。话不是非空字符串时记已失败。"""
    call = ctx.call
    text = call.params.get("text")
    if not (isinstance(text, str) and text.strip()):
        ctx.set_status(CallStatus.FAILED, f"要告知的话不是非空字符串：{text!r}")
        return
    message = ctx.outbox.put(Message(
        kind=NOTICE_KIND,
        sender=TOOL_SOURCE_PREFIX + call.tool,
        recipient="user",
        in_reply_to=None,
        content={"utterance": text, "params": copy.deepcopy(call.params)},
        call_id=call.call_id,
    ))
    call.result = text
    ctx.set_status(CallStatus.SUCCEEDED, f"已告知使用者，消息 {message.seq}")


# ───────────────────────── 告知异常 ─────────────────────────
# 通用工具，对所有任务都登记，不是某个任务的领域工具。调用选择在三种情形下直接构造它的候选，它不走前置条件：
# 一趟走完阶段目标仍未达成；循环段次数到「最多」仍未满足「重复直到」；阶段越过最后一个而任务未完成。

EXCEPTION_TOOL = "告知异常"
EXCEPTION_PARAM_NAMES = ("阶段", "未达成目标", "步骤现况", "当前步", "可选措施")
REDO = "重做本阶段"  # 给使用者看的措施名
REDO_RESULT = "重做"  # 工具调用的返回值；任务定义据它把当前步退回该阶段起点
ABORT_BY_USER = "主动终止"
ABORT_UNABLE = "被动终止"
EXCEPTION_OPTIONS = (REDO, ABORT_BY_USER, ABORT_UNABLE)
# 使用者的选择 → （工具调用终态, 返回值, 说明）
EXCEPTION_OUTCOMES = {
    REDO: (CallStatus.SUCCEEDED, REDO_RESULT, "使用者选择重做本阶段"),
    ABORT_BY_USER: (CallStatus.FAILED, "主动终止", "使用者主动终止"),
    ABORT_UNABLE: (CallStatus.FAILED, "被动终止", "任务无法继续，使用者确认终止"),
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
    call = ctx.call
    params = call.params
    question = ctx.outbox.put(Message(
        kind="question",
        sender=TOOL_SOURCE_PREFIX + call.tool,
        recipient="user",
        in_reply_to=None,
        content={"utterance": exception_utterance(params), "params": copy.deepcopy(params)},
        call_id=call.call_id,
    ))
    ctx.set_status(CallStatus.WAITING, f"已向使用者告知异常，问题 {question.seq}")
    message = ctx.inbox.take(
        match=lambda m: m.kind == "answer" and m.in_reply_to == question.seq,
        block=True,
        waiter=call.call_id,
    )
    if message is None:
        ctx.set_status(CallStatus.FAILED, "没有可用的回答")
        return
    outcome = EXCEPTION_OUTCOMES.get(message.content) if isinstance(message.content, str) else None
    if outcome is None:
        ctx.set_status(CallStatus.FAILED, f"回答不是可选措施之一：{message.content}")
        return
    status, result, note = outcome
    call.result = result
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(status, note)


# ───────────────────────── 前置条件 ─────────────────────────
# 前置条件是函数，接收只读数据与已求值的参数，返回（成立与否, 说明, 命中值）。
# 说明是与数据无关的固定文字（依据说明在任务开始前就要拼好），动态内容放命中值。
# 前置条件只写「这个工具此刻调用会不会出错」，不写任务要达成什么。

ASK_NOTE = "写入目标指向的位置为 None"


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


# ───────────────────────── 本模块登记的工具 ─────────────────────────

# 静态工具规格：工具名 → ToolSpec，由 base 汇总成静态工具表 STATIC_TOOLS。
TOOL_SPECS = {
    "ask": ToolSpec("ask", ("target", "hint", "type", "about", "adopt_to", "options", "original", "text"),
                    _ask_precondition, None,
                    optional_params=frozenset({"type", "about", "adopt_to", "options", "original", "text"}),
                    category="与使用者对话",
                    summary="问使用者一个问题，把回答写进写入目标所指的槽位"),
    NOTIFY_TOOL: ToolSpec(NOTIFY_TOOL, ("text",), None, frozenset(),
                          category="与使用者对话",
                          summary="对使用者说一句话，不等回答；对话模式里用它说出解释或推迟的结果"),
    EXCEPTION_TOOL: ToolSpec(EXCEPTION_TOOL, EXCEPTION_PARAM_NAMES, None, frozenset(),  # 不走前置条件
                             category="与使用者对话",
                             summary="任务无法继续时告知使用者原因，请使用者在重做本阶段、主动终止、被动终止里选一个"),
}

# 工具实现：工具名 → 函数（任务定义）→ 实现，由 base 汇总；模型调用件、读事件函数与系统提示由 build_table 另外绑上。
TOOL_IMPLS = {
    "ask": lambda task_def: functools.partial(ask, task_def=task_def),  # 读事件函数由 build_table 另外绑上
    NOTIFY_TOOL: lambda task_def: notify,
    EXCEPTION_TOOL: lambda task_def: report_exception,
}
