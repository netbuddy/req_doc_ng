"""工具的底座：工具、运行工具表、静态工具规格，与各工具模块共用的几个助手；末尾把各工具模块登记的规格与实现汇总成表。

各工具模块（dialogue、understanding、patterns、intake、glossary）在自己文件里登记 TOOL_SPECS 与 TOOL_IMPLS，
内部调模型的另登记 MODEL_TOOLS；本模块只汇总，并按任务建运行工具表、拼工具目录与系统提示。
汇总放在本文件末尾：各工具模块在顶部从本模块导入 ToolSpec 等底座名字，所以底座部分必须先于汇总处的导入定义好。
"""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass
from typing import Any, Callable

from tod_kernel.kernel import KernelError
from tod_kernel.llm import build_system_prompt


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


def _plain(value) -> str:
    import json

    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _pack(task_def, ctx, read_events):
    """建这次调用的上下文包。事件由宿主给的「读事件」函数提供，内核不为此改动。"""
    from tod_kernel.context import ContextPack  # 函数内导入：context 要读任务定义，顶层导会与加载器绕回来

    return ContextPack.build(task_def, dict(ctx.data_view), read_events() if read_events else [], step=ctx.step)


def _call_record(reply, segments, writes, parsed) -> dict:
    """模型调用记录：调用件记的那几项，加上段列表、解析结果、每项写到了哪个槽位。

    段的「变了没有」要与上一次调用比，本步由观测台按相邻两次调用的段自行比对，记录里不预先算。
    """
    record = dict(reply.record)
    record["segments"] = [segment.as_record() for segment in segments]
    record["parsed"] = parsed
    record["writes"] = {key: slot for key, slot in writes.items() if slot}
    return record


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


@functools.lru_cache(maxsize=None)
def prompt_pack_of(tool: str):
    """读这个工具的提示词包（每个工具读一次）；不合格抛提示词包错误。"""
    from tod_kernel import prompt_pack

    provides, has_shape = MODEL_TOOLS[tool]
    return prompt_pack.load(tool, provides, has_shape)


# ───────────────────────── 汇总：各工具模块登记的工具 ─────────────────────────
# 这几行导入必须在底座名字之后：各工具模块在顶部从本模块取 ToolSpec、set_at 等。

from tod_kernel.tools import dialogue, glossary, intake, patterns, understanding  # noqa: E402
from tod_kernel.tools.dialogue import EXCEPTION_TOOL  # noqa: E402
from tod_kernel.tools.understanding import CONFIDENCE_FLOOR, UNDERSTAND_TOOL  # noqa: E402

# 静态工具表：工具名 → ToolSpec，顺序与拆分前的单文件一致。
STATIC_TOOLS = {**dialogue.TOOL_SPECS, **intake.TOOL_SPECS, **glossary.TOOL_SPECS, **understanding.TOOL_SPECS,
                **patterns.TOOL_SPECS}
STATIC_TOOLS = {name: STATIC_TOOLS[name] for name in
                ("ask", "list_dir", "register_file", "generate_manifest", glossary.DRAFT_TOOL, understanding.UNDERSTAND_TOOL,
                 dialogue.NOTIFY_TOOL, patterns.MARK_DEFERRED_TOOL, patterns.EXPLAIN_TOOL, dialogue.EXCEPTION_TOOL)}

# 工具实现：工具名 → 函数（任务定义）→ 实现。只有询问与标记推迟要绑定任务定义。
_IMPLS = {**dialogue.TOOL_IMPLS, **intake.TOOL_IMPLS, **glossary.TOOL_IMPLS, **understanding.TOOL_IMPLS,
          **patterns.TOOL_IMPLS}

# 内部调用模型的工具，与它们进系统提示工具目录的固定指令。
# 值是（本步与片段的占位符声明, 包里有没有输出形状）；输出为 JSON 的对话理解由代码现算结构，包里没有输出形状。
MODEL_TOOLS = {**glossary.MODEL_TOOLS, **understanding.MODEL_TOOLS, **patterns.MODEL_TOOLS}


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
