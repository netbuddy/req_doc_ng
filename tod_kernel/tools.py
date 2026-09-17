"""工具与工具表。

工具是「能做什么」的静态定义，工具调用是工具的一次调用。工具表分两层：
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
from tod_kernel.kernel import TOOL_SOURCE_PREFIX, CallStatus, Change, KernelError, Message
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
    # 可写槽位与前置条件由静态工具表填上；执行控制本步不核验它们，前置条件只在调用选择时用。
    writable_slots: frozenset | None = None  # 可写槽位：槽位名的集合；None 表示由参数决定（询问写入目标所指的槽位）
    preconditions: Any = None  # 前置条件：函数（只读数据, 已求值的参数）→（成立与否, 说明, 命中值）
    required_auth: Any = None  # 本步只预留
    category: str | None = None  # 类别：CATEGORIES 之一
    summary: str | None = None  # 一句话说明：这个工具做什么、写哪个槽位
    writer_roles: dict | None = None  # 可选：槽位 → 这一槽位的内容实际来自谁（观测台标「谁填的」用）；键「*」指其余槽位
    optional_params: frozenset = frozenset()  # 参数名清单里可以不写的那几个（内核核对参数名时放行）


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
READ_VALUE = "念的值"  # 上一问登记里的副本：这一问念的是哪个值


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


def _plain(value) -> str:
    import json

    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


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
    call = ctx.call
    directory = ctx.data_view.get("目录")
    if directory not in SAMPLE_DIRS:
        ctx.set_status(CallStatus.FAILED, f"样例表里没有目录：{directory!r}")
        return
    names = [entry["文件名"] for entry in SAMPLE_DIRS[directory]]
    call.result = names
    call.changes = [Change("文件总表", copy.deepcopy(ctx.data_view.get("文件总表")), names, call.call_id)]
    ctx.set_status(CallStatus.SUCCEEDED, f"列出 {len(names)} 个文件")


def register_file(ctx) -> None:
    """登记文件：按参数序号从样例表取该文件的类型、大小、页数，
    给「材料清单」追加一项（是否纳入为 None），「登记进度」加一。"""
    call = ctx.call
    index = call.params["index"]
    entries = SAMPLE_DIRS.get(ctx.data_view.get("目录"), [])
    if not 0 <= index < len(entries):
        ctx.set_status(CallStatus.FAILED, f"样例表里没有序号 {index} 的文件")
        return
    entry = entries[index]
    old_list = copy.deepcopy(ctx.data_view.get("材料清单"))
    item = {**entry, "是否纳入": None}
    old_progress = ctx.data_view.get("登记进度")
    call.result = copy.deepcopy(item)
    call.changes = [
        Change("材料清单", old_list, old_list + [item], call.call_id),
        Change("登记进度", old_progress, old_progress + 1, call.call_id),
    ]
    ctx.set_status(CallStatus.SUCCEEDED, f"登记文件 {entry['文件名']}")


def generate_manifest(ctx) -> None:
    """生成清单文件：把「材料清单」里纳入的项拼成固定格式的文本作为返回值，
    把固定路径写进「清单文件路径」。不落盘。"""
    call = ctx.call
    items = ctx.data_view.get("材料清单") or []
    included = [item for item in items if item["是否纳入"] == INCLUDED]
    lines = [f"材料清单（共 {len(included)} 项）"]
    lines += [f"{n}. {item['文件名']}，{item['类型']}，{item['大小']} 字节，{item['页数']} 页"
              for n, item in enumerate(included, start=1)]
    call.result = "\n".join(lines)
    call.changes = [Change("清单文件路径", copy.deepcopy(ctx.data_view.get("清单文件路径")), MANIFEST_PATH, call.call_id)]
    ctx.set_status(CallStatus.SUCCEEDED, f"清单含 {len(included)} 项，写到 {MANIFEST_PATH}")


# ───────────────────────── 术语澄清用的模型工具：生成术语释义 ─────────────────────────
# 模型在这个内核里的第二种使用场景：工具实现内部调模型，回答是数据，写进槽位，对错由使用者确认判。
# 工具无参数、直接读槽位；请求由上下文包（context.py）按段组装，系统提示在建工具表时拼好。
# 提示词（固定指令、本步段、进度与参考材料里的短句、输出形状）在提示词包 prompts/生成术语释义.json 里（第五步 4.4A 节）：
# 改一个字，请求哈希就变，录制文件全部失效要重录。

DRAFT_TOOL = "生成术语释义"
TERM_SLOT = "术语"
SOURCE_SLOT = "原文片段"
DRAFT_SLOT = "释义草稿"
FEEDBACK_SLOT = "修改意见"
CONFIRMED_SLOT = "确认释义"

# 提示词包里每个模板能用哪些占位符，由这里声明，加载时逐个核对（prompt_pack.load）。
DRAFT_PROVIDES = {"本步": {"首稿": {TERM_SLOT}, "改稿": {TERM_SLOT}},
                  "片段": {"进度·首稿": {"稿数"}, "进度·改稿": {"稿数", "上一稿"}, "参考材料缺失": set()}}


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


def draft_definition(ctx, call_model, task_def=None, read_events=None, system_prompt="") -> None:
    """生成术语释义：首稿与改稿同一个实现，按「修改意见」是不是空分支。

    首稿读「术语」与可选的「原文片段」；改稿另读上一稿与「修改意见」，写完把「修改意见」清空。
    回答去掉首尾空白后写进「释义草稿」；工具调用的返回值是这次模型调用的完整记录。
    模型调用失败（模型服务不可达、回放时录制文件里查不到）时记已失败，说明就是那条错误的一句话。
    """
    from tod_kernel.context import ContextPack, render  # 同上，函数内导入

    call = ctx.call
    pack = _pack(task_def, ctx, read_events)
    feedback = ctx.data_view.get(FEEDBACK_SLOT)
    revising = feedback is not None
    drafts = _draft_count(pack)
    prompt = prompt_pack_of(DRAFT_TOOL)
    term = _plain(ctx.data_view.get(TERM_SLOT))
    if revising:
        segments = [pack.progress(prompt.fill_piece("进度·改稿", 上一稿=drafts, 稿数=drafts + 1)),
                    pack.dialogue(scope=("slots", [REPLY_SLOT])),
                    pack.data_segment([TERM_SLOT, (DRAFT_SLOT, "（上一稿）"), FEEDBACK_SLOT],
                                      revision=(DRAFT_SLOT, FEEDBACK_SLOT)),
                    pack.material(SOURCE_SLOT),
                    ContextPack.step(prompt.fill_step("改稿", 术语=term)),
                    ContextPack.shape(SHAPE_TEXT, prompt.shape)]
    else:
        segments = [pack.progress(prompt.fill_piece("进度·首稿", 稿数=drafts + 1)),
                    pack.dialogue(),
                    pack.data_segment([TERM_SLOT]),
                    pack.material(SOURCE_SLOT, prompt.fill_piece("参考材料缺失")),
                    ContextPack.step(prompt.fill_step("首稿", 术语=term)),
                    ContextPack.shape(SHAPE_TEXT, prompt.shape)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_TEXT)
    try:
        reply = call_model(request)
    except LLMError as exc:
        ctx.set_status(CallStatus.FAILED, exc.brief)
        return
    draft = reply.text.strip()
    changes = [Change(DRAFT_SLOT, copy.deepcopy(ctx.data_view.get(DRAFT_SLOT)), draft, call.call_id)]
    if revising:  # 意见已经落实到这一稿里，清空，否则下一次循环又会被当成待改
        changes.append(Change(FEEDBACK_SLOT, copy.deepcopy(feedback), None, call.call_id))
    call.result = _call_record(reply, segments, {"（文本）": DRAFT_SLOT}, draft)
    call.changes = changes
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(CallStatus.SUCCEEDED, f"模型写出第 {drafts + 1} 稿，{len(draft)} 字")


# ───────────────────────── 对话理解（第五步第 4 节）─────────────────────────
# 通用工具：把使用者对上一问的回答变成对话行为列表，再按列表落数据。它读的是三个必备槽位（回复、上一问、待处理）
# 与槽位表，不认识任何具体任务；唯一的例外是「修改意见」——建议类上一问的改动要求写进它，这是第五步 4.7 节的裁定。
# 配对表、对话功能、把握阈值、固定句式都写死在这里，第六步抽成表。
# 固定指令与本步段的写法在提示词包 prompts/对话理解.json 里（第五步 4.4A 节）：改一个字，录制文件全部失效要重录。

UNDERSTAND_TOOL = "对话理解"
REPLY_SLOT = "回复"
LAST_QUESTION_SLOT = "上一问"
UNDERSTAND_REQUIRED_SLOTS = {REPLY_SLOT: "文本", LAST_QUESTION_SLOT: "对象"}

# 对话功能：回应类三个、主动类四个、系统侧一个。
AFFIRM, DENY, REQALTS = "AFFIRM", "DENY", "REQALTS"
INFORM, REQUEST, DEFER, OTHER = "INFORM", "REQUEST", "DEFER", "OTHER"
CLARIFY = "CLARIFY"
RESPONSE_FUNCTIONS = (AFFIRM, DENY, REQALTS)
ACTIVE_FUNCTIONS = (INFORM, REQUEST, DEFER, OTHER)

# 上一问的四种类型与配对表：上一问是哪种类型，回应类功能就只能从哪几个里选。
QUESTION_SUGGEST, QUESTION_CHECK, QUESTION_CHOICE, QUESTION_REQUEST = "建议", "求证", "选择", "请求"
PAIRING = {
    QUESTION_SUGGEST: [AFFIRM, DENY, REQALTS],
    QUESTION_CHECK: [AFFIRM, DENY],
    QUESTION_CHOICE: [AFFIRM, DENY],
    QUESTION_REQUEST: [DENY],
}
ORIGINAL_QUESTION = "原问"  # 代码产出的澄清项与由它再问的选择类上一问带着它：被澄清的那一问

# 语义内容的键。
KEY_SLOT, KEY_PATH, KEY_VALUE = "槽位", "路径", "值"
KEY_ASK = "问"
KEY_CHOICE = "选项序号"
KEY_CLARIFY_TEXT, KEY_OPTIONS = "问话", "选项"
ACT_KEYS = ("功能", "回应上一问", "内容", "把握", "规范化修订")
APPEND = "+"  # 路径里的「末尾新增」

# 对话理解返回值里交给任务定义的两个键（第五步 4.12 节）：待路由的行为，与插入段内的「本帧再问」。
ROUTES_KEY = "routes"
REASK_KEY = "reask_in_frame"
REASK_ORIGINAL_KEY = "reask_original"
REPLACE_KEY = "replace_frame"  # 插入段内帧替换：澄清帧里选了「先放一放」，换成推迟帧（2026-09-18 裁定问题六）
STEP_INSERTS = "插入"  # 当前步地址栈里插入段那一层的键；工具只用它判断自己是不是在插入段里

CONFIDENCE_FLOOR = 0.6  # 把握阈值的默认值；配置键 confidence_floor 可改，由程序入口经 build_table 传进来
DEFERRED_MARK = {"已推迟": True}
ALTS_FEEDBACK = "换一份"
# 代码产出的澄清（第 1 条规范化）恒为固定三项，序号含义代码知道：1 按原问的 AFFIRM 落，2 不落，3 按 DEFER 落。
FALLBACK_OPTIONS = ["确认这一稿", "修改这一稿", "先放一放"]
FALLBACK_CHOICE_ADOPT, FALLBACK_CHOICE_REVISE, FALLBACK_CHOICE_DEFER = 1, 2, 3
FALLBACK_TEXT = "我没听懂，你是想：{options}？"
MISMATCH_TEXT = "你说的『{value}』我记成『{slot}』，对吗？"
MISMATCH_OPTIONS = ["对", "不对，我重新说"]

UNDERSTAND_REPLY_LABEL = "（待理解的原话）"
# 提示词（固定指令与五种情形的本步段、可写位置与选项的写法）在提示词包 prompts/对话理解.json 里（第五步 4.4A 节）。
# 八个功能各自的用法说明写在固定指令里，输出 Schema 只留裸结构，不带说明文字。
UNDERSTAND_VALUES = {"槽位", "采纳到", "值", "选项", "可写位置"}
NO_QUESTION = "上一问为空"
UNDERSTAND_PROVIDES = {
    "本步": {kind: set(UNDERSTAND_VALUES) for kind in (QUESTION_SUGGEST, QUESTION_CHECK, QUESTION_CHOICE, QUESTION_REQUEST,
                                                     NO_QUESTION)},
    "片段": {"位置": {"槽位"}, "列表位置·没有项": {"槽位"}, "列表位置·一项": {"槽位"}, "列表位置·多项": {"槽位", "项数"},
           "位置之间": set(), "没有可写位置": set(), "选项": {"序号", "选项"}, "选项之间": set()},
}


def _path_text(path) -> str:
    import json

    return json.dumps(list(path), ensure_ascii=False)


def understand_schema(candidates, paths, question) -> dict:
    """输出 Schema：每次调用现算，同时进「输出形状」段与接口参数 response_format。

    三栏枚举写死进去——功能取候选功能集，写值的槽位与路径按可写路径清单逐槽位配对，推迟的槽位取清单里的槽位。
    每项按功能分支（anyOf）：选定功能后内容只能是第五步 4.5 节表里配给它的形状。模型服务照 Schema 逐键生成，
    「功能」排第一个键，所以功能一定下来，内容的形状就被约束住（实测只给一个不分功能的内容 anyOf 时，
    模型会给 DEFER 配上选项序号、给 REQUEST 配上槽位）。
    Schema 里不带任何说明文字（description）：各功能的用法写在提示词包的固定指令里（第五步 4.4A 节）。
    """
    by_slot: dict = {}
    for row in paths:
        by_slot.setdefault(row[KEY_SLOT], []).append(list(row[KEY_PATH]))
    null = {"type": "null"}
    choice = {"type": "object", "properties": {KEY_CHOICE: {"type": "integer", "minimum": 1}},
              "required": [KEY_CHOICE], "additionalProperties": False}
    writes = [{"type": "object",
               "properties": {KEY_SLOT: {"enum": [slot]}, KEY_PATH: {"enum": slot_paths}, KEY_VALUE: {}},
               "required": [KEY_SLOT, KEY_PATH, KEY_VALUE], "additionalProperties": False}
              for slot, slot_paths in by_slot.items()]
    asked = {"type": "object", "properties": {KEY_ASK: {"type": "string"}},
             "required": [KEY_ASK], "additionalProperties": False}
    deferred = {"type": "object", "properties": {KEY_SLOT: {"enum": list(by_slot)}},
                "required": [KEY_SLOT], "additionalProperties": False}
    clarify = {"type": "object",
               "properties": {KEY_CLARIFY_TEXT: {"type": "string"},
                              KEY_OPTIONS: {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3}},
               "required": [KEY_CLARIFY_TEXT, KEY_OPTIONS], "additionalProperties": False}
    is_choice = isinstance(question, dict) and question.get("类型") == QUESTION_CHOICE
    shapes = {AFFIRM: [null, choice] if is_choice else [null], DENY: [null], REQALTS: [null], OTHER: [null],
              INFORM: writes, REQUEST: [asked], DEFER: [null, deferred] if by_slot else [null], CLARIFY: [clarify]}
    revision = {"anyOf": [null, {"type": "object",
                                 "properties": {"原文": {"type": "string"}, "修订后": {"type": "string"}},
                                 "required": ["原文", "修订后"], "additionalProperties": False}]}
    variants, grouped = [], {}
    for function in candidates:
        if not shapes[function]:
            continue  # 清单为空时没有 INFORM 这一支
        key = str(shapes[function])
        if key in grouped:  # 内容形状相同的功能并成一支，枚举里列几个
            grouped[key]["properties"]["功能"]["enum"].append(function)
            continue
        content = shapes[function][0] if len(shapes[function]) == 1 else {"anyOf": shapes[function]}
        variant = {"type": "object",
                   "properties": {"功能": {"enum": [function]}, "回应上一问": {"type": "boolean"}, "内容": content,
                                  "把握": {"type": "number", "minimum": 0, "maximum": 1}, "规范化修订": revision},
                   "required": list(ACT_KEYS), "additionalProperties": False}
        grouped[key] = variant
        variants.append(variant)
    return {"type": "object", "properties": {"行为": {"type": "array", "items": {"anyOf": variants}}},
            "required": ["行为"], "additionalProperties": False}


# ── 校验与规范化（第五步 4.6 节）──

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _content_shape(content):
    """内容是五种形状里的哪一种：空、选项序号、写值、提问、推迟、澄清；都不是返回 None。"""
    if content is None:
        return "空"
    if not isinstance(content, dict):
        return None
    keys = set(content)
    if keys == {KEY_CHOICE} and isinstance(content[KEY_CHOICE], int) and not isinstance(content[KEY_CHOICE], bool):
        return "选项序号"
    if keys == {KEY_SLOT, KEY_PATH, KEY_VALUE} and isinstance(content[KEY_SLOT], str) and isinstance(content[KEY_PATH], list):
        return "写值"
    if keys == {KEY_ASK} and isinstance(content[KEY_ASK], str):
        return "提问"
    if keys == {KEY_SLOT} and isinstance(content[KEY_SLOT], str):
        return "推迟"
    if (keys == {KEY_CLARIFY_TEXT, KEY_OPTIONS} and isinstance(content[KEY_CLARIFY_TEXT], str)
            and isinstance(content[KEY_OPTIONS], list) and 2 <= len(content[KEY_OPTIONS]) <= 3
            and all(isinstance(option, str) for option in content[KEY_OPTIONS])):
        return "澄清"
    return None


def parse_acts(text, candidates, paths, question):
    """解析模型回的 JSON 并做结构校验。返回（行为列表, 毛病）：毛病不为 None 时按第 1 条规范化处理。

    结构校验只管 Schema 管得住的：键齐全、功能在候选内、内容是五种形状之一、写值的槽位与路径在清单内、把握是 0 到 1 的数。
    功能与内容配不上的情形里，第 3 条规范化管得了的（回应类带了内容、OTHER 带了内容）留给规范化，其余算不合 Schema。
    """
    import json

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, "模型回的不是 JSON"
    if not isinstance(parsed, dict) or set(parsed) != {"行为"} or not isinstance(parsed["行为"], list):
        return None, "模型回的 JSON 不是 {\"行为\": [...]}"
    if not parsed["行为"]:
        return None, "行为列表为空"
    allowed = set(candidates) | {CLARIFY}
    listed = {(row[KEY_SLOT], json.dumps(row[KEY_PATH], ensure_ascii=False)) for row in paths}
    slots = {row[KEY_SLOT] for row in paths}
    acts = []
    for index, item in enumerate(parsed["行为"]):
        where = f"第 {index + 1} 项"
        if not isinstance(item, dict) or set(item) != set(ACT_KEYS):
            return None, f"{where}的键不是 {'、'.join(ACT_KEYS)}"
        function, content = item["功能"], item["内容"]
        if function not in allowed:
            return None, f"{where}的功能「{function}」不在候选功能集里"
        if not isinstance(item["回应上一问"], bool):
            return None, f"{where}的「回应上一问」不是布尔值"
        if not (_is_number(item["把握"]) and 0 <= item["把握"] <= 1):
            return None, f"{where}的把握不是 0 到 1 的数"
        revision = item["规范化修订"]
        if revision is not None and not (isinstance(revision, dict) and set(revision) == {"原文", "修订后"}
                                         and all(isinstance(v, str) for v in revision.values())):
            return None, f"{where}的规范化修订形状不对"
        shape = _content_shape(content)
        if shape is None:
            return None, f"{where}的内容不是五种形状之一"
        if shape == "写值" and (content[KEY_SLOT], json.dumps(content[KEY_PATH], ensure_ascii=False)) not in listed:
            return None, f"{where}写值的槽位与路径不在可写路径清单里"
        if shape == "推迟" and content[KEY_SLOT] not in slots and content[KEY_SLOT] != (question or {}).get(KEY_SLOT):
            return None, f"{where}推迟的槽位不可写"
        if function == INFORM and shape != "写值":
            return None, f"{where}是 INFORM 却没有写值"
        if function == REQUEST and shape != "提问":
            return None, f"{where}是 REQUEST 却没有提问"
        if function == DEFER and shape not in ("推迟", "空"):
            return None, f"{where}是 DEFER 却不是推迟的内容"
        if function == CLARIFY and shape != "澄清":
            return None, f"{where}是 CLARIFY 却没有问话与两三个选项"
        if shape in ("写值", "提问", "澄清") and function not in (INFORM, REQUEST, CLARIFY) + RESPONSE_FUNCTIONS + (OTHER,):
            return None, f"{where}的功能与内容配不上"
        acts.append({key: copy.deepcopy(item[key]) for key in ACT_KEYS})
    return acts, None


def fallback_acts(question) -> list:
    """第 1 条规范化：低把握的空理解，产出一条代码写的 CLARIFY，选项恒为固定三项，并带上被澄清的那一问。"""
    return [{"功能": CLARIFY, "回应上一问": False,
             "内容": {KEY_CLARIFY_TEXT: FALLBACK_TEXT.format(options="／".join(FALLBACK_OPTIONS)),
                    KEY_OPTIONS: list(FALLBACK_OPTIONS), ORIGINAL_QUESTION: copy.deepcopy(_original_of(question))},
             "把握": 0.0, "规范化修订": None, "规范化": ["第 1 条：模型输出不可用，按低把握空理解处理"]}]


def _original_of(question):
    """被澄清的那一问：上一问本身就是代码澄清之后的再问时，取它记着的原问，免得原问一层套一层。

    「念的值」只给询问比对再问用，不带进原问（原问会进插入段的输入与观测台，带着整段草稿没有用处）。
    """
    if isinstance(question, dict) and ORIGINAL_QUESTION in question:
        question = question[ORIGINAL_QUESTION]
    if isinstance(question, dict) and READ_VALUE in question:
        question = {key: value for key, value in question.items() if key != READ_VALUE}
    return question


def value_fits(slots_meta, slot, path, value) -> bool:
    """写值的值类型与槽位（或列表项字段）类型相符：文本是字符串、数字是数、布尔是布尔、枚举在取值内、列表是列表、对象是字典。"""
    meta = slots_meta.get(slot) or {}
    kind = meta.get("类型")
    if path:
        if kind == "列表":
            item_meta = meta.get("项")
            if len(path) == 1:
                return (not item_meta) or (isinstance(value, dict) and set(value) <= set(item_meta))
            field_meta = (item_meta or {}).get(path[1])
            if field_meta is None:
                return False
            return _type_fits(field_meta, value)
        return True  # 对象槽位按键写，键下的值没有元数据可核
    return _type_fits(meta, value)


def _type_fits(meta, value) -> bool:
    kind = meta.get("类型")
    if kind == "文本":
        return isinstance(value, str)
    if kind == "数字":
        return _is_number(value)
    if kind == "布尔":
        return isinstance(value, bool)
    if kind == "枚举":
        return value in (meta.get("取值") or [])
    if kind == "列表":
        return isinstance(value, list)
    if kind == "对象":
        return isinstance(value, dict)
    return False


def normalize_acts(acts, question, slots_meta) -> tuple:
    """第 2 到第 7 条规范化，按编号顺序执行。返回（规范化后的行为列表, 删掉的项）。

    每项被改动时在「规范化」里记一句是哪一条、做了什么，原来的内容记进「模型原值」。
    第 6 条产生的 CLARIFY 不再触发第 4 条（2026-09-18 裁定问题八）。
    """
    import json

    acts = [dict(act, 规范化=list(act.get("规范化") or [])) for act in acts]
    dropped = []
    # 代码固定三项的澄清再问里，使用者用话说「先放一放」而模型给了 DEFER：目标就是原问的推迟目标时，按选项 3 处理，
    # 在插入段里因此走帧替换而不是本帧再问（2026-09-18 增补裁定第 9 条）。先于第 2 到第 7 条执行。
    # 同一句里已经有回应类项（例如 AFFIRM 选项序号 3）时不改写，那一项 DEFER 留给第 7 条记成重复项。
    if (isinstance(question, dict) and question.get("类型") == QUESTION_CHOICE and ORIGINAL_QUESTION in question
            and not any(act["功能"] in RESPONSE_FUNCTIONS for act in acts)):
        target = defer_target(question.get(ORIGINAL_QUESTION), None)
        for act in acts:
            if act["功能"] == DEFER and defer_target(question, act["内容"]) == target:
                act.setdefault("模型原值", []).append({"功能": DEFER, "内容": copy.deepcopy(act["内容"])})
                act.update({"功能": AFFIRM, "回应上一问": True, "内容": {KEY_CHOICE: FALLBACK_CHOICE_DEFER}})
                act["规范化"].append("澄清再问里推迟原问的目标，按选项 3「先放一放」处理")
    # 第 2 条：同一槽位同一路径两项写值，留后一项。
    last_of = {}
    for index, act in enumerate(acts):
        if act["功能"] == INFORM:
            last_of[(act["内容"][KEY_SLOT], json.dumps(act["内容"][KEY_PATH], ensure_ascii=False))] = index
    kept = []
    for index, act in enumerate(acts):
        if act["功能"] == INFORM:
            key = (act["内容"][KEY_SLOT], json.dumps(act["内容"][KEY_PATH], ensure_ascii=False))
            if last_of[key] != index:
                later = acts[last_of[key]]
                later.setdefault("模型原值", []).append(copy.deepcopy(act["内容"]))
                later["规范化"].append("第 2 条：同一槽位同一路径有两项写值，留后一项")
                dropped.append((copy.deepcopy(act), "第 2 条"))
                continue
        kept.append(act)
    acts = kept
    # 第 3 条：回应类带了内容（选择类的选项序号除外）置空；OTHER 带了内容也置空。
    choice = isinstance(question, dict) and question.get("类型") == QUESTION_CHOICE
    for act in acts:
        exempt = act["功能"] == AFFIRM and choice and _content_shape(act["内容"]) == "选项序号"
        if act["功能"] in RESPONSE_FUNCTIONS + (OTHER,) and act["内容"] is not None and not exempt:
            act.setdefault("模型原值", []).append(copy.deepcopy(act["内容"]))
            act["内容"] = None
            act["规范化"].append("第 3 条：回应类项带了内容，置空")
    # 第 4 条：CLARIFY 与其他项同时出现，只留第一条 CLARIFY。
    clarifies = [act for act in acts if act["功能"] == CLARIFY]
    if clarifies and len(acts) > 1:
        dropped.extend((copy.deepcopy(act), "第 4 条") for act in acts if act is not clarifies[0])
        clarifies[0]["规范化"].append("第 4 条：CLARIFY 与其他项同时出现，只留 CLARIFY")
        acts = [clarifies[0]]
    # 第 5 条：回应类至多一项，留第一项。
    seen_response = False
    kept = []
    for act in acts:
        if act["功能"] in RESPONSE_FUNCTIONS:
            if seen_response:
                dropped.append((copy.deepcopy(act), "第 5 条"))
                continue
            seen_response = True
        kept.append(act)
    acts = kept
    # 第 6 条：写值的值类型与槽位类型不符，改为 CLARIFY。
    for act in acts:
        if act["功能"] == INFORM and not value_fits(slots_meta, act["内容"][KEY_SLOT], act["内容"][KEY_PATH],
                                                   act["内容"][KEY_VALUE]):
            act.setdefault("模型原值", []).append(copy.deepcopy(act["内容"]))
            act["功能"] = CLARIFY
            act["内容"] = {KEY_CLARIFY_TEXT: MISMATCH_TEXT.format(value=_plain(act["内容"][KEY_VALUE]),
                                                               slot=act["内容"][KEY_SLOT]),
                         KEY_OPTIONS: list(MISMATCH_OPTIONS)}
            act["规范化"].append("第 6 条：值的类型与槽位类型不符，改为 CLARIFY")
    # 第 7 条：两项落到同一目标同一动作（选择类 AFFIRM 选了推迟，又另有一项 DEFER 同一槽位），留前一项，记重复项
    #（2026-09-18 裁定问题三）。
    kept, seen = [], {}
    for act in acts:
        key = _landing_key(act, question)
        if key is not None and key in seen:
            first = seen[key]
            first.setdefault("重复项", []).append(copy.deepcopy(act))
            first["规范化"].append(f"第 7 条：另有一项 {act['功能']} 落到同一目标同一动作，留前一项，记重复项")
            dropped.append((copy.deepcopy(act), "第 7 条"))
            continue
        if key is not None:
            seen[key] = act
        kept.append(act)
    return kept, dropped


def _landing_key(act, question):
    """一项会落成的（动作, 目标槽位）；只算推迟这一种动作，其余返回 None（同槽位同路径的写值由第 2 条管）。"""
    question = question if isinstance(question, dict) else {}
    if act["功能"] == DEFER:
        return ("推迟", defer_target(question, act["内容"]))
    choice = (act["内容"] or {}).get(KEY_CHOICE) if isinstance(act["内容"], dict) else None
    if (act["功能"] == AFFIRM and question.get("类型") == QUESTION_CHOICE and ORIGINAL_QUESTION in question
            and choice == FALLBACK_CHOICE_DEFER):
        return ("推迟", defer_target(question.get(ORIGINAL_QUESTION), None))
    return None


# ── 按行为列表落数据（第五步 4.7 节）──

class _Landing:
    """一次对话理解落下的全部改动：先在工作副本上改，最后每个槽位出一条变更（旧值是执行前的值）。"""

    def __init__(self, data):
        self.data = data
        self.work: dict = {}
        self.routes: list = []  # 待路由的行为：{功能, 输入}，由任务定义按路由表压成插入段
        self.appended: dict = {}  # 槽位 → 这次「末尾新增」出来的那一项的下标（同一句里的几个 ["+", 字段] 并进同一项）

    def get(self, slot):
        return self.work[slot] if slot in self.work else copy.deepcopy(self.data.get(slot))

    def write(self, slot, path, value) -> None:
        current = self.get(slot)
        path = list(path)
        if path and path[0] == APPEND:
            items = list(current or [])
            if len(path) == 1:
                items.append(copy.deepcopy(value))
                self.appended[slot] = len(items) - 1
            else:
                if slot not in self.appended:
                    items.append({})
                    self.appended[slot] = len(items) - 1
                items[self.appended[slot]] = set_at(items[self.appended[slot]], path[1:], value)
            self.work[slot] = items
            return
        self.work[slot] = set_at(current, path, value)

    def changes(self, call_id) -> list:
        return [Change(slot, copy.deepcopy(self.data.get(slot)), value, call_id)
                for slot, value in self.work.items() if value != self.data.get(slot)]


def _get_at(value, path):
    for step in path or []:
        if isinstance(value, list) and isinstance(step, int) and 0 <= step < len(value):
            value = value[step]
        elif isinstance(value, dict):
            value = value.get(step)
        else:
            return None
    return value


def _land_affirm_as(question, land, paths, act) -> str:
    """按上一问的类型落 AFFIRM。选择类要看选项从哪来：代码产出的固定三项按序号落，模型写的自由文字写进上一问的位置。"""
    import json

    kind = question.get("类型")
    slot, path = question.get(KEY_SLOT), list(question.get(KEY_PATH) or [])
    if kind == QUESTION_SUGGEST:
        adopt = question.get("采纳到")
        if not adopt:
            return "上一问没有登记采纳到，不写"
        land.write(adopt, [], _get_at(land.get(slot), path))
        return f"把「{slot}」的当前值写进「{adopt}」"
    if kind == QUESTION_CHECK:
        options = question.get(KEY_OPTIONS) or []
        if not options:
            return "求证类上一问没有待写的值，不写"
        land.write(slot, path, options[0])
        return f"把求证的值写进「{slot}」{_path_text(path)}"
    if kind == QUESTION_CHOICE:
        choice = (act["内容"] or {}).get(KEY_CHOICE)
        options = question.get(KEY_OPTIONS) or []
        if not (isinstance(choice, int) and 1 <= choice <= len(options)):
            return "没有给出有效的选项序号，不写"
        original = question.get(ORIGINAL_QUESTION)
        if ORIGINAL_QUESTION in question:  # 代码产出的固定三项
            if choice == FALLBACK_CHOICE_ADOPT:
                if not isinstance(original, dict):
                    return "选了「确认这一稿」，但被澄清的那一问为空，不写"
                return "选了「确认这一稿」：" + _land_affirm_as(original, land, paths, {"内容": None})
            if choice == FALLBACK_CHOICE_REVISE:
                return "选了「修改这一稿」：不写，下一次照常再念"
            target = defer_target(original, None, {row[KEY_SLOT] for row in paths})
            land.routes.append({"功能": DEFER, "输入": {KEY_SLOT: target}, "替换": True})
            return f"选了「先放一放」：按 DEFER 路由，推迟「{target}」"
        listed = {(row[KEY_SLOT], json.dumps(row[KEY_PATH], ensure_ascii=False)) for row in paths}
        if (slot, json.dumps(path, ensure_ascii=False)) not in listed:
            return "选择项不可写，未落"
        land.write(slot, path, options[choice - 1])
        return f"把所选的「{options[choice - 1]}」写进「{slot}」{_path_text(path)}"
    return "上一问不是建议、求证或选择，不写"


def defer_target(question, content, writable=None):
    """推迟的目标槽位：模型给了可写的槽位就用它；没给或给的是上一问的槽位（不可写的草稿）时，上一问有采纳到就取采纳到。

    writable 是可写槽位名的集合；不给时把上一问的槽位一律当作不可写（评测比对时用）。
    """
    question = question if isinstance(question, dict) else {}
    slot = (content or {}).get(KEY_SLOT) if isinstance(content, dict) else None
    if slot is None or (slot == question.get(KEY_SLOT) and (writable is None or slot not in writable)):
        slot = question.get("采纳到") or question.get(KEY_SLOT)
    return slot


def land_acts(acts, question, data, paths, slots_meta, floor, route_modes=None):
    """按规范化后的行为列表落数据，返回落数据的工作副本（其中 routes 是待路由的行为）。

    回应类与把握够的写值直接落（第五步 4.7 节）；REQUEST、CLARIFY、DEFER 与把握不够的写值不落，列进待路由的行为，
    由任务定义按路由表压成插入段（4.12 节）。route_modes 是路由表（功能 → 模式名），只用来写「落成」那句去向。
    回复与上一问不在这里清空，由调用方最后清空。
    """
    route_modes = route_modes or {}
    land = _Landing(data)
    land.slots_meta = slots_meta
    question_dict = question if isinstance(question, dict) else {}
    writable = {row[KEY_SLOT] for row in paths}
    informs = [act for act in acts if act["功能"] == INFORM and act["把握"] >= floor]

    def route(act, function, payload, head=""):
        land.routes.append({"功能": function, "输入": payload})
        act["落成"] = f"{head}{function} → 路由到模式『{route_modes.get(function, '？')}』"

    for act in acts:
        function, content = act["功能"], act["内容"]
        if function == AFFIRM:
            adopt = question_dict.get("采纳到")
            if question_dict.get("类型") == QUESTION_SUGGEST and any(i["内容"][KEY_SLOT] == adopt for i in informs):
                act["落成"] = f"使用者给了完整的新值，按写值落进「{adopt}」，不再抄草稿"
            elif question_dict:
                act["落成"] = _land_affirm_as(question_dict, land, paths, act)
            else:
                act["落成"] = "没有上一问，不写"
        elif function == DENY:
            act["落成"] = "不写"
        elif function == REQALTS:
            if any(i["内容"][KEY_SLOT] == FEEDBACK_SLOT for i in informs):
                act["落成"] = f"同一句里另有写值写进「{FEEDBACK_SLOT}」，按写值落"
            else:
                land.write(FEEDBACK_SLOT, [], ALTS_FEEDBACK)
                act["落成"] = f"「{FEEDBACK_SLOT}」写「{ALTS_FEEDBACK}」"
        elif function == INFORM:
            slot, path, value = content[KEY_SLOT], content[KEY_PATH], content[KEY_VALUE]
            if act["把握"] >= floor:
                land.write(slot, path, value)
                act["落成"] = f"写进「{slot}」{_path_text(path)}"
            else:
                route(act, INFORM, {KEY_SLOT: slot, KEY_PATH: list(path), KEY_VALUE: copy.deepcopy(value)},
                      f"把握 {act['把握']} 低于阈值 {floor}：")
        elif function == REQUEST:
            route(act, REQUEST, {KEY_ASK: content[KEY_ASK]})
        elif function == DEFER:
            route(act, DEFER, {KEY_SLOT: defer_target(question_dict, content, writable)})
        elif function == CLARIFY:
            # 澄清模式对所有原问都用代码固定三项再问，模型写的选项一律弃用（2026-09-18 裁定问题七）
            original = content[ORIGINAL_QUESTION] if ORIGINAL_QUESTION in content else _original_of(question)
            route(act, CLARIFY, {ORIGINAL_QUESTION: copy.deepcopy(original)})
        else:
            act["落成"] = "不落，记进调用记录"
    return land


def understand(ctx, call_model, task_def=None, read_events=None, system_prompt="",
               confidence_floor=CONFIDENCE_FLOOR) -> None:
    """对话理解：把「回复」里使用者的原话变成对话行为列表，校验规范化后按列表落数据，最后清空「回复」与「上一问」。

    候选功能集与可写路径清单由代码算（context.py），进输出 Schema 的枚举作接口参数；本步段按上一问的类型选提示词包里的
    一种白话写法，用中文说能判成哪几种回答、可写的位置有哪些，不出现功能标识与代码名（第五步 4.4A 节）。
    模型输出不可用时按第 1 条规范化产出一条代码写的 CLARIFY，不重试；模型调用本身失败（服务不可达、回放查不到）记已失败。
    返回值＝模型调用记录（全段）＋规范化后的行为列表（每项的落成）＋删掉的项＋这句原话。
    """
    import json

    from tod_kernel.context import ContextPack, candidate_functions, render, understand_step_text, writable_paths

    call = ctx.call
    data = dict(ctx.data_view)
    slots_meta = task_def.DEFINITION.get("槽位", {})
    question = data.get(LAST_QUESTION_SLOT)
    reply_text = data.get(REPLY_SLOT)
    candidates = candidate_functions(question)
    paths = writable_paths(slots_meta, question, data)
    schema = understand_schema(candidates, paths, question)
    pack = _pack(task_def, ctx, read_events)
    focus = [name for name in slots_meta
             if name not in UNDERSTAND_REQUIRED_SLOTS and slots_meta[name].get("使用者可写", True) is not False]
    if isinstance(question, dict) and question.get("类型") == QUESTION_SUGGEST and FEEDBACK_SLOT in slots_meta \
            and FEEDBACK_SLOT not in focus:
        focus.append(FEEDBACK_SLOT)
    segments = [pack.progress(),
                pack.dialogue(),
                pack.data_segment(focus + [(REPLY_SLOT, UNDERSTAND_REPLY_LABEL)]),
                ContextPack.step(understand_step_text(prompt_pack_of(UNDERSTAND_TOOL), question, paths, slots_meta, data)),
                ContextPack.shape(SHAPE_JSON, json.dumps(schema, ensure_ascii=False), api_note=True)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_JSON, json_schema=schema)
    try:
        reply = call_model(request)
    except LLMError as exc:
        ctx.set_status(CallStatus.FAILED, exc.brief)
        return
    parsed_acts, problem = parse_acts(reply.text, candidates, paths, question)
    try:
        parsed = json.loads(reply.text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if problem is not None:
        acts, dropped = fallback_acts(question), []
    else:
        acts, dropped = normalize_acts(parsed_acts, question, slots_meta)
    route_modes = route_modes_of(task_def)
    land = land_acts(acts, question, data, paths, slots_meta, confidence_floor, route_modes)
    in_frame = isinstance(ctx.step, dict) and bool(ctx.step.get(STEP_INSERTS))
    reask, replace = False, None
    if in_frame and land.routes:
        if all(item.get("替换") for item in land.routes):
            # 澄清帧里选了「先放一放」：帧替换成推迟帧，不嵌套（2026-09-18 裁定问题六）
            replace = {"功能": land.routes[0]["功能"], "输入": land.routes[0]["输入"]}
        else:
            # 插入段里不再嵌套路由：整句换成一条代码澄清，在本帧内以选择类再问（4.12 节边界）
            original = _original_of(question)
            acts, dropped = fallback_acts(question), [{"项": act, "规范化": "插入段内不嵌套"} for act in acts]
            for act in acts:
                act["落成"] = "插入段里不再压帧：本帧内以选择类再问"
            land = _Landing(data)
            reask = True
    elif land.routes:
        for item in land.routes:
            item.pop("替换", None)
    land.work[REPLY_SLOT] = None
    land.work[LAST_QUESTION_SLOT] = None
    writes = {}
    for number, act in enumerate(acts, start=1):
        content = act["内容"] if isinstance(act["内容"], dict) else {}
        if act["功能"] == INFORM and content.get(KEY_SLOT) in land.work and act["把握"] >= confidence_floor:
            writes[f"{number} {act['功能']}"] = content[KEY_SLOT]
    record = _call_record(reply, segments, writes, parsed)
    record["reply"] = reply_text
    record["acts"] = acts
    record["dropped"] = [item if isinstance(item, dict) else {"项": item[0], "规范化": item[1]} for item in dropped]
    record["problem"] = problem
    record["landed"] = [act["落成"] for act in acts]
    record[ROUTES_KEY] = [] if (reask or replace) else land.routes
    if reask:
        record[REASK_KEY] = True
        record[REASK_ORIGINAL_KEY] = copy.deepcopy(original)
    if replace:
        record[REPLACE_KEY] = replace
    call.result = record
    call.changes = land.changes(call.call_id)
    functions = "、".join(act["功能"] for act in acts)
    note = f"理解出 {len(acts)} 项：{functions}" + (f"（{problem}，按没听懂处理）" if problem else "")
    ctx.set_status(CallStatus.SUCCEEDED, note)


def _call_record(reply, segments, writes, parsed) -> dict:
    """模型调用记录：调用件记的那几项，加上段列表、解析结果、每项写到了哪个槽位。

    段的「变了没有」要与上一次调用比，本步由观测台按相邻两次调用的段自行比对，记录里不预先算。
    """
    record = dict(reply.record)
    record["segments"] = [segment.as_record() for segment in segments]
    record["parsed"] = parsed
    record["writes"] = {key: slot for key, slot in writes.items() if slot}
    return record


# ───────────────────────── 对话模式用的三个系统工具（第五步 4.12 节）─────────────────────────
# 对话模式（答疑、澄清、推迟、求证）是系统级的小任务定义，写在 task_defs/patterns.json。模式里除了询问与对话理解，
# 另用这三个工具：告知（只说一句，不等回答）、标记推迟（在目标槽位写已推迟标记）、答疑（内部调模型，一两句白话解释）。

NOTIFY_TOOL = "告知"
MARK_DEFERRED_TOOL = "标记推迟"
EXPLAIN_TOOL = "答疑"
NOTICE_KIND = "notice"  # 告知消息的种类：放进发件箱，不等回答

# 提示词在提示词包 prompts/答疑.json 里（第五步 4.4A 节）。
EXPLAIN_PROVIDES = {"本步": {"解释": {"问的内容"}}, "片段": {}}


def route_modes_of(task_def) -> dict:
    """路由表（功能 → 模式名），取自任务定义加载时并进来的对话模式定义；没有就是空表。"""
    patterns = (getattr(task_def, "DEFINITION", {}) or {}).get("对话模式") or {}
    return {row["功能"]: row["模式"] for row in patterns.get("路由表", [])}


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


def mark_deferred(ctx, task_def) -> None:
    """标记推迟：在参数 slot 所指的槽位写已推迟标记。本步只对文本槽位写；不是文本槽位时不写，照样记已成功并写明。"""
    call = ctx.call
    slot = call.params.get("slot")
    meta = (task_def.DEFINITION.get("槽位", {}) or {}).get(slot)
    if meta is None:
        ctx.set_status(CallStatus.FAILED, f"没有这个槽位：{slot!r}")
        return
    if meta.get("类型") != "文本":
        call.result = None
        ctx.set_status(CallStatus.SUCCEEDED, f"「{slot}」不是文本槽位，本步不打推迟标记")
        return
    call.result = dict(DEFERRED_MARK)
    call.changes = [Change(slot, copy.deepcopy(ctx.data_view.get(slot)), dict(DEFERRED_MARK), call.call_id)]
    ctx.set_status(CallStatus.SUCCEEDED, f"「{slot}」标已推迟")


def explain(ctx, call_model, task_def=None, read_events=None, system_prompt="") -> None:
    """答疑：把问的词或原话、当前任务全部槽位的现值、对话历史交给模型，要一到两句白话解释。

    不写任何槽位；解释放进返回值（模型调用记录）的「输出」，由对话模式的下一步「告知」说给使用者。
    模型调用失败记已失败；回答去掉首尾空白后为空也记已失败。
    """
    from tod_kernel.context import ContextPack, render

    call = ctx.call
    question = call.params.get("question")
    pack = _pack(task_def, ctx, read_events)
    slots = list((task_def.DEFINITION.get("槽位", {}) or {}).keys())
    segments = [pack.progress(),
                pack.dialogue(),
                pack.data_segment(slots),
                ContextPack.step(prompt_pack_of(EXPLAIN_TOOL).fill_step("解释", 问的内容=_plain(question))),
                ContextPack.shape(SHAPE_TEXT, prompt_pack_of(EXPLAIN_TOOL).shape)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_TEXT)
    try:
        reply = call_model(request)
    except LLMError as exc:
        ctx.set_status(CallStatus.FAILED, exc.brief)
        return
    answer = reply.text.strip()
    record = _call_record(reply, segments, {}, answer)
    record["输出"] = answer
    call.result = record
    if not answer:
        ctx.set_status(CallStatus.FAILED, "模型没有给出解释")
        return
    ctx.set_status(CallStatus.SUCCEEDED, f"模型给出解释，{len(answer)} 字")


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
LIST_DIR_NOTE = "文件总表为 None"
REGISTER_FILE_NOTE = "序号等于登记进度且小于文件总数"
GENERATE_MANIFEST_NOTE = "每项是否纳入都不为 None"
DRAFT_DEFINITION_NOTE = "术语不为 None，且（释义草稿为 None 或修改意见不为 None）"
UNDERSTAND_NOTE = "回复不为 None"


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


def _understand_precondition(data, params):
    reply = data.get(REPLY_SLOT)
    return reply is not None, UNDERSTAND_NOTE, {REPLY_SLOT: reply, LAST_QUESTION_SLOT: data.get(LAST_QUESTION_SLOT)}


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
    optional_params: frozenset = frozenset()  # 参数名清单里可以不写的那几个
    # 用这个工具的任务，槽位表里必须有的槽位：槽位名 → 类型。加载器据它校验，缺一个报加载错误。
    required_slots: dict | None = None
    uses_patterns: bool = False  # 用这个工具的任务要带上对话模式（task_defs/patterns.json），加载器据它并进来


STATIC_TOOLS = {
    "ask": ToolSpec("ask", ("target", "hint", "type", "about", "adopt_to", "options", "original", "text"),
                    _ask_precondition, None,
                    optional_params=frozenset({"type", "about", "adopt_to", "options", "original", "text"}),
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
    UNDERSTAND_TOOL: ToolSpec(UNDERSTAND_TOOL, (), _understand_precondition, None,
                              category="加工任务数据",
                              summary="把使用者对上一问的回答理解成对话行为列表，按列表写入槽位或登记待处理，并清空回复与上一问",
                              # 写进槽位的是使用者的话，模型只是把它认出来；待处理是系统记下的待办。
                              writer_roles={"*": "使用者"},
                              required_slots=dict(UNDERSTAND_REQUIRED_SLOTS), uses_patterns=True),
    NOTIFY_TOOL: ToolSpec(NOTIFY_TOOL, ("text",), None, frozenset(),
                          category="与使用者对话",
                          summary="对使用者说一句话，不等回答；对话模式里用它说出解释或推迟的结果"),
    MARK_DEFERRED_TOOL: ToolSpec(MARK_DEFERRED_TOOL, ("slot",), None, None,
                                 category="加工任务数据",
                                 summary="在参数所指的文本槽位写已推迟标记",
                                 writer_roles={"*": "使用者"}),
    EXPLAIN_TOOL: ToolSpec(EXPLAIN_TOOL, ("question",), None, frozenset(),
                           category="加工任务数据",
                           summary="用一到两句白话解释使用者问的词或句子，不写槽位，解释交给告知说出"),
    EXCEPTION_TOOL: ToolSpec(EXCEPTION_TOOL, EXCEPTION_PARAM_NAMES, None, frozenset(),  # 不走前置条件
                             category="与使用者对话",
                             summary="任务无法继续时告知使用者原因，请使用者在重做本阶段、主动终止、被动终止里选一个"),
}

# 工具实现：工具名 → 函数（任务定义）→ 实现。只有询问要绑定任务定义（读话语模板）。
_IMPLS = {
    "ask": lambda task_def: functools.partial(ask, task_def=task_def),  # 读事件函数由 build_table 另外绑上
    "list_dir": lambda task_def: list_dir,
    "register_file": lambda task_def: register_file,
    "generate_manifest": lambda task_def: generate_manifest,
    DRAFT_TOOL: lambda task_def: draft_definition,  # 模型调用件、读事件函数与系统提示由 build_table 另外绑上
    UNDERSTAND_TOOL: lambda task_def: understand,
    NOTIFY_TOOL: lambda task_def: notify,
    MARK_DEFERRED_TOOL: lambda task_def: functools.partial(mark_deferred, task_def=task_def),
    EXPLAIN_TOOL: lambda task_def: explain,
    EXCEPTION_TOOL: lambda task_def: report_exception,
}


# 内部调用模型的工具，与它们进系统提示工具目录的固定指令。
# 值是（本步与片段的占位符声明, 包里有没有输出形状）；输出为 JSON 的对话理解由代码现算结构，包里没有输出形状。
MODEL_TOOLS = {DRAFT_TOOL: (DRAFT_PROVIDES, True), UNDERSTAND_TOOL: (UNDERSTAND_PROVIDES, False),
               EXPLAIN_TOOL: (EXPLAIN_PROVIDES, True)}


@functools.lru_cache(maxsize=None)
def prompt_pack_of(tool: str):
    """读这个工具的提示词包（每个工具读一次）；不合格抛提示词包错误。"""
    from tod_kernel import prompt_pack

    provides, has_shape = MODEL_TOOLS[tool]
    return prompt_pack.load(tool, provides, has_shape)

CATALOG_HEAD = "本任务可用的工具（来自任务定义引用的工具，取自静态工具表）："


def tool_catalog(tool_names) -> str:
    """工具目录：这个任务引用的每个工具一行，内部调用模型的附上它的固定指令。进系统提示，任务期间不变。

    整份目录都进去（不只是模型实现的那几个），第五步自主规划选工具看的是同一份。
    """
    lines = [CATALOG_HEAD]
    for name in tool_names:
        spec = STATIC_TOOLS[name]
        kind = f"{spec.category}，内部调用模型" if name in MODEL_TOOLS else spec.category
        line = f"· {name}（{kind}）：{spec.summary}。"
        if spec.param_names:
            line += f"参数：{'、'.join(spec.param_names)}。"
        if name in MODEL_TOOLS:
            line += f"固定指令：{prompt_pack_of(name).instruction}"
        lines.append(line)
    return "\n".join(lines)


def system_prompt_for(task_def, tool_names) -> str:
    """这次任务的系统提示：六段，任务启动时拼一次，任务期间不变。

    三段固定文字在模型调用件里，任务定义摘要由加载器生成，工具目录由本模块生成，领域规矩是任务定义的可选块。
    """
    from tod_kernel.taskdef import domain_rules_of, summary_text  # 函数内导入：加载器导入本模块，顶层导会绕回来

    return build_system_prompt(task_def.NAME, summary_text(task_def), tool_catalog(tool_names),
                               domain_rules_of(task_def))


def pattern_tool_names(task_def) -> list:
    """任务定义并进来的对话模式里用到的工具名，按模式与步骤的顺序去重。"""
    names = []
    patterns = (getattr(task_def, "DEFINITION", {}) or {}).get("对话模式") or {}
    for pattern in patterns.get("模式", []):
        for step in pattern.get("步骤", []):
            if step.get("工具") not in names:
                names.append(step.get("工具"))
    return names


def build_table(task_def, tool_names=("ask",), call_model=None, read_events=None,
                confidence_floor=CONFIDENCE_FLOOR) -> ToolTable:
    """按任务建运行工具表：登记给定名字的工具，另外恒登记「告知异常」，任务带着对话模式时再登记模式里用到的工具。

    工具名、参数名清单、前置条件、可写槽位、类别、一句话说明从静态工具表取；询问工具用 partial 绑定任务定义与读事件函数，
    内部调用模型的工具另外绑上模型调用件、任务定义、读事件函数与这次任务的系统提示，
    所以运行工具表与任务绑定，每个任务各建一张。
    call_model 是 llm.make_caller 做出来的函数（给模型发一次请求），read_events 是宿主给的「把至今的事件读出来」的函数（上下文包要用），
    两者都由程序入口（验证脚本、控制台）准备；任务用到这两个工具而没给 call_model 时，在这里就报错，不等到跑起来才失败。
    read_events 没给时上下文包按「没有事件」处理，对话历史与修订记录会是空的。
    confidence_floor 是对话理解的把握阈值，程序入口从配置键 confidence_floor 读，没配就用默认 0.6。
    """
    table = ToolTable()
    names = list(tool_names)
    for name in pattern_tool_names(task_def) + [EXCEPTION_TOOL]:
        if name not in names:
            names.append(name)
    system_prompt = system_prompt_for(task_def, names) if any(name in MODEL_TOOLS for name in names) else ""
    for name in names:
        if name not in STATIC_TOOLS:
            raise KernelError(f"没有这个工具：{name!r}")
        spec = STATIC_TOOLS[name]
        impl = _IMPLS[name](task_def)
        if name == "ask":
            impl = functools.partial(impl, read_events=read_events)
        if name in MODEL_TOOLS:
            if call_model is None:
                raise KernelError(f"任务用到工具 {name!r}，但建工具表时没有给模型调用件（build_table 的 call_model 参数）")
            impl = functools.partial(impl, call_model=call_model, task_def=task_def, read_events=read_events,
                                     system_prompt=system_prompt)
            if name == UNDERSTAND_TOOL:
                impl = functools.partial(impl, confidence_floor=confidence_floor)
        table.register(Tool(name=spec.name, param_names=spec.param_names, impl=impl,
                            writable_slots=spec.writable_slots, preconditions=spec.preconditions,
                            category=spec.category, summary=spec.summary, writer_roles=spec.writer_roles,
                            optional_params=spec.optional_params))
    return table
