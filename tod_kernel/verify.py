"""第零步验证脚本：在仓根下运行 `python -m tod_kernel.verify`。

脚本同时扮演宿主和使用者。
- 宿主：分配任务标识、建事件流、挂订阅者、建收件箱与发件箱、起线程跑 start_task、最后收线程。
- 使用者：主线程循环阻塞读发件箱，取到问题就按内容里的槽位查答案表，往收件箱放一条回答
  （回复对象填问题的到达序号，所属行动与收件人填问题的所属行动，发起方 user）；答案表里没有
  的槽位就关闭收件箱（发起方 host）。内核结束或出错时关闭发件箱，主线程取到空即停止读取。
  事件流只做观测，不参与应答。

全部断言都对内存收集器里的事件做。事件分状态、追踪两类：前五条验证目标的断言都先过滤掉
追踪事件，只对状态事件做；唯一例外是「序号连续」，序号由两类事件共用，所以对全部事件断言。
任一断言失败时脚本以非零状态退出。

仅供验证的手段：跑场景期间临时替换 kernel 模块上的 update_state、execute、select_action 与
register_action，在调用原函数前后抄下任务数据的实际值（select_action 另抄下下一个行动编号、行动表大小
与事件条数，用来确认行动选择不写任何东西；register_action 抄下返回后行动表的情况，用来确认登记即放入
行动表），与事件重放的结果比对；跑完即恢复。
内核本身没有这类钩子，也不依赖它们。
"""

from __future__ import annotations

import ast
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import dataclasses

from tod_kernel import kernel, llm, taskdef
# 控制台是宿主一侧的东西：宿主循环、两个应答者与答案表的键都在那里，验证脚本与它共用同一套。
from tod_kernel.console import PRESET_ANSWER_PREFIX, QUESTION_PREFIX, PresetAnswerer, host_loop, target_key
from tod_kernel.tools import DRAFT_TOOL, EXCEPTION_OPTIONS, EXCEPTION_TOOL, JUDGE_TOOL, build_table
from tod_kernel.tools import set_at as tools_set_at
from tod_kernel.kernel import (
    ACTION_PROPOSED,
    ACTION_STATUS_CHANGED,
    CHECK_DONE_RESULT,
    CONTROL_RESULT,
    DATA_CHANGED,
    STEP_CHANGED,
    EXECUTE_CALL,
    LOOP_STARTED,
    MAILBOX_CLOSED,
    MAILBOX_WAIT,
    MESSAGE_PUT,
    MESSAGE_TAKEN,
    STATE,
    TASK_DEFINITION_ERROR,
    TASK_ENDED,
    TASK_STARTED,
    TASK_STATUS_CHANGED,
    TRACE,
    TOOL_SOURCE_PREFIX,
    EXTERNAL_SENDERS,
    INBOX,
    OUTBOX,
    SOURCE_LOOP,
    SOURCE_MAILBOX,
    SOURCE_UPDATE,
    STATE_EVENT_NAMES,
    Action,
    ActionStatus,
    EventStream,
    KernelError,
    Mailbox,
    TaskStatus,
)
from tod_kernel.tools import system_prompt_for as tools_system_prompt_for
from tod_kernel.observe import (
    ConsolePrinter,
    FileWriter,
    MemoryCollector,
    action_history,
    is_external,
    make_server,
    read_events,
    replay_data,
    summarize,
)

KERNEL_THREAD_PREFIX = "kernel-"
TASK_DEFS_DIR = Path(__file__).resolve().parent / "task_defs"  # 任务定义数据文件


# ───────────────────────── 任务定义：经加载器从数据文件取 ─────────────────────────
# 每次调用都重新加载，得到新对象。材料接入登记的「目录」与出差申请单的预填都作为一次任务的初始输入给出。

SAMPLE_DIR_INPUT = {"目录": "样例材料"}


def intake_def(file="intake.json", name=None):
    return taskdef.load(TASK_DEFS_DIR / file, name=name, initial=dict(SAMPLE_DIR_INPUT))


RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"  # 仓根下的 runs/，不入版本库
HOST_JOIN_SECONDS = 30  # 收内核线程时最多等多久，只防验证脚本在缺陷下挂死，不参与邮箱语义


# ───────────────────────── 断言记账 ─────────────────────────


# 控制台跑场景时把这几个开关拨一下：问答打印出来、事件流水与逐条断言都不打印、另挂一个显示行动的订阅者。
# 直接跑验证脚本时它们保持原样，打印与第三步一字不差。
SHOW_EXCHANGES = False
PRINT_EVENTS = True
PRINT_CHECKS = True
EXTRA_SUBSCRIBERS: tuple = ()
LAST_RUN = None  # 最近一次跑完的运行，控制台用它打印任务标识与运行文件


class Checker:
    def __init__(self, title: str):
        self.title = title
        self.results: list[tuple[str, bool]] = []

    def check(self, description: str, condition, detail: str = "") -> None:
        ok = bool(condition)
        self.results.append((description, ok))
        if not PRINT_CHECKS:
            return
        mark = "通过" if ok else "失败"
        line = f"  [{mark}] {description}"
        if not ok and detail:
            line += f"\n         实际情况：{detail}"
        print(line, flush=True)

    @property
    def passed(self) -> int:
        return sum(ok for _, ok in self.results)


# ───────────────────────── 宿主与使用者 ─────────────────────────


@dataclass
class Run:
    task_id: str
    task_def: object = None
    task: object = None
    error: BaseException | None = None
    all_events: list = field(default_factory=list)  # 两类事件都在
    events: list = field(default_factory=list)  # 过滤掉追踪事件后的状态事件
    update_snapshots: list = field(default_factory=list)  # (前数据, 前事件数, 后数据, 后事件数)
    execute_snapshots: dict = field(default_factory=dict)  # 行动编号 → 执行前实际数据
    select_snapshots: list = field(default_factory=list)  # 每次行动选择前后的（数据, 下一个行动编号, 行动表大小, 事件条数）
    register_snapshots: list = field(default_factory=list)  # 每次登记返回时的（返回的编号, 登记前表大小, 登记后表大小, 表里该行动的状态, 当时最后一个行动提出事件的编号）
    thread_of_seq: dict = field(default_factory=dict)  # 事件序号 → 发布它的线程
    run_file: Path | None = None  # 文件订阅者写出的 JSONL
    inbox_closed: bool = False
    outbox_closed: bool = False
    host_thread: int = 0
    kernel_thread: int = 0
    tools_spec: dict = field(default_factory=dict)  # 本场景工具表：工具名 → {参数名清单, 类别, 一句话说明}
    writable: dict = field(default_factory=dict)  # 本场景工具表：工具名 → 可写槽位（没有声明时为空）


def get_at(value, path):
    """按路径取值，路径为空时就是 value 本身。"""
    for step in path:
        value = value[step]
    return value


# 第四步 4.10 节起任务位置不在任务数据里（保留槽位「游标」废除），所以数据里每一项都是任务作者写的槽位，
# 原先为了避开游标而设的「业务槽位」三个辅助函数退役；位置的终态另用一条断言核对。
def step_at(stage, index=None, loop=None) -> dict:
    """当前步的值：阶段、循环（起、止、第几次）、阶段内序号（从 1 起）。阶段起点只有阶段这一层。"""
    value = {"阶段": stage}
    if loop is not None:
        value["循环"] = {"起": loop[0], "止": loop[1], "第几次": loop[2]}
    if index is not None:
        value["步骤"] = index
    return value


def step_sequence(run) -> list:
    """这次运行里当前步的变化序列：[(来源, 新值), …]，按事件序号。来源是「初始化」或行动编号。"""
    return [(e.payload["source"], e.payload["new"]) for e in named(run.events, STEP_CHANGED)]


def check_step_final(c, run, expected: dict) -> None:
    """当前步的终态值（任务对象上的字段，不在任务数据里）。"""
    actual = run.task.step if run.task is not None else None
    c.check(f"第四步：当前步的终态值是 {expected}", actual == expected, actual)


# 话语的预期句：按写入目标逐条写死的完整句子，不调用话语生成、也不读模板，用来逐字比对问题里的话。
EXPECTED_UTTERANCES = {
    ("术语", ()): "请给出要澄清的术语。",
    ("原文片段", ()): "请给出术语「基线」出现的原文片段。",
    ("材料清单", (0, "是否纳入")): "请确认是否把文件 a.docx 纳入项目。",
    ("材料清单", (1, "是否纳入")): "请确认是否把文件 b.pdf 纳入项目。",
    ("材料清单", (2, "是否纳入")): "请确认是否把文件 c.xlsx 纳入项目。",
}


def run_scenario(task_id: str, task_def, answers: dict, tool_names=("ask",), runs_dir=None, console=True,
                 exception_answers=None, call=None, answerer=None, show=None, subscribers=None) -> Run:
    """跑一个场景。

    exception_answers：第三步新增，使用者对「告知异常」依次给的回答；用完后再来告知异常就关闭收件箱。
    call：第四步新增，模型调用件（llm.make_caller 做出来的函数），只有用到「生成术语释义」的任务需要。
    answerer：应答者，默认按答案表答；控制台的自由输入方式换成从键盘取回答的那个。
    show：是否打印一问一答，默认随模块开关 SHOW_EXCHANGES。
    subscribers：另外挂的事件订阅者，默认随模块开关 EXTRA_SUBSCRIBERS。
    """
    global LAST_RUN
    run = Run(task_id=task_id, task_def=task_def, host_thread=threading.get_ident())

    # 宿主：事件流、订阅者、邮箱。
    stream = EventStream(task_id)
    collector = MemoryCollector()
    stream.subscribe(collector)
    if console and PRINT_EVENTS:
        stream.subscribe(ConsolePrinter())
    writer = FileWriter(RUNS_DIR if runs_dir is None else runs_dir)
    stream.subscribe(writer)
    for subscriber in (EXTRA_SUBSCRIBERS if subscribers is None else subscribers):
        stream.subscribe(subscriber)
    stream.subscribe(lambda e: run.thread_of_seq.__setitem__(e.seq, threading.get_ident()))

    inbox = Mailbox(stream, task_id, INBOX)
    outbox = Mailbox(stream, task_id, OUTBOX)

    # 仅供验证：临时包住 update_state 与 execute，抄下实际数据。
    original_update, original_execute = kernel.update_state, kernel.execute
    original_select = kernel.select_action
    original_register = kernel.register_action

    def update_probe(task, item):
        run.task = task
        before, n_before = dict(task.data), len(collector.events)
        original_update(task, item)
        run.update_snapshots.append((before, n_before, dict(task.data), len(collector.events)))

    def execute_probe(task, action):
        run.execute_snapshots[action.action_id] = dict(task.data)
        original_execute(task, action)

    def select_probe(task):
        before = (dict(task.data), task.next_action_id, len(task.actions), len(collector.events))
        candidate = original_select(task)
        run.select_snapshots.append((before, (dict(task.data), task.next_action_id, len(task.actions), len(collector.events))))
        return candidate

    def register_probe(task, candidate):
        size_before = len(task.actions)
        action_id = original_register(task, candidate)
        in_table = task.actions.get(action_id)
        proposed = [e for e in collector.events if e.name == ACTION_PROPOSED]
        run.register_snapshots.append((
            action_id, size_before, len(task.actions),
            in_table.status if in_table is not None else None,
            proposed[-1].action_id if proposed else None,
        ))
        return action_id

    def kernel_main():
        try:
            run.task = kernel.start_task(task_id, task_def, table, inbox, outbox, stream)
        except BaseException as exc:  # 内核错误与意外异常都交给主线程断言
            run.error = exc
        finally:
            # 正常情况下内核已关闭发件箱，这里不会再发事件；只有内核因意外异常退出而没关时，
            # 由宿主代关，免得主线程永远阻塞在发件箱上。
            outbox.close("host")

    kernel.update_state, kernel.execute, kernel.select_action = update_probe, execute_probe, select_probe
    kernel.register_action = register_probe
    # 上下文包要读至今的事件（对话历史、修订记录）：宿主本来就持有内存收集器，把读它的函数交给工具表，内核不动。
    table = build_table(task_def, tool_names, call=call, read_events=lambda: list(collector.events))
    run.tools_spec = {name: {"param_names": list(tool.param_names), "category": tool.category, "summary": tool.summary,
                             **({"writer_roles": dict(tool.writer_roles)} if tool.writer_roles else {})}
                      for name, tool in table.items()}
    run.writable = {name: tool.writable_slots for name, tool in table.items()}
    try:
        thread = threading.Thread(target=kernel_main, name=KERNEL_THREAD_PREFIX + task_id)
        thread.start()
        run.kernel_thread = thread.ident
        host_loop(inbox, outbox, PresetAnswerer(answers, exception_answers) if answerer is None else answerer,
                  show=SHOW_EXCHANGES if show is None else show)
        thread.join(HOST_JOIN_SECONDS)
        if thread.is_alive():
            run.error = run.error or TimeoutError("验证脚本等待内核线程结束超时")
    finally:
        kernel.update_state, kernel.execute, kernel.select_action = original_update, original_execute, original_select
        kernel.register_action = original_register
    run.run_file = writer.path_for(task_id)  # 收到「任务开始」时才定名，所以运行结束后再取
    run.all_events = list(collector.events)
    run.events = [e for e in run.all_events if e.kind == STATE]
    run.inbox_closed = inbox._closed  # 只读检查用：两个箱最终是否关闭
    run.outbox_closed = outbox._closed
    LAST_RUN = run
    return run


# ───────────────────────── 事件查询小工具 ─────────────────────────


def named(events, name):
    return [e for e in events if e.name == name]


def of_action(events, name, action_id):
    return [e for e in events if e.name == name and e.action_id == action_id]


def status_values(history):
    return [e.payload["new_status"] for e in history]


# ───────────────────────── 各验证目标 ─────────────────────────


def check_integrity(c: Checker, run: Run) -> None:
    """每个场景都做的完整性检查：序号、任务标识、写入点即发布点（验证目标五）、投影一致。"""
    events, task = run.events, run.task
    all_events = run.all_events
    c.check("内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序",
            [e.seq for e in all_events] == list(range(1, len(all_events) + 1)), [e.seq for e in all_events])
    c.check("全部事件（两类一起）的任务标识都等于宿主分配的标识",
            {e.task_id for e in all_events} == {run.task_id}, {e.task_id for e in all_events})
    c.check("宿主拿得到任务对象", task is not None)
    if task is None:
        return

    expected = [(ch.slot, ch.old, ch.new, ch.source) for ch in task.init_changes]
    for action in task.actions.values():
        expected += [(ch.slot, ch.old, ch.new, ch.source) for ch in action.changes]
    actual = [(e.payload["slot"], e.payload["old"], e.payload["new"], e.payload["source"]) for e in named(events, DATA_CHANGED)]
    c.check("目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等",
            expected == actual, f"对象上 {expected}；事件里 {actual}")

    for action in task.actions.values():
        history = action_history(events, action.action_id)
        c.check(f"目标五：行动 {action.action_id} 的当前状态等于它最后一个「行动状态变化」事件的状态",
                history and history[-1].payload["new_status"] == action.status,
                f"对象 {action.status}，事件 {status_values(history)}")

    task_status_events = named(events, TASK_STATUS_CHANGED)
    c.check("目标五：任务状态等于最后一个「任务状态变化」事件的状态",
            task_status_events and task_status_events[-1].payload["new_status"] == task.status,
            f"对象 {task.status}")

    mismatches = []
    for before, n_before, after, n_after in run.update_snapshots:
        if before != replay_data(all_events[:n_before]) or after != replay_data(all_events[:n_after]):
            mismatches.append((before, after))
    c.check(f"目标五：{len(run.update_snapshots)} 次状态更新的前后，实际任务数据都等于用事件重建的数据",
            run.update_snapshots and not mismatches, mismatches)

    c.check("终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据",
            replay_data(events) == task.data, f"重放 {replay_data(events)}，真实 {task.data}")

    proposed_events = named(events, ACTION_PROPOSED)
    proposed_ids = [e.action_id for e in proposed_events]
    on_objects = [(a.action_id, a.tool, a.params, a.proposer, a.basis) for a in task.actions.values()]
    in_events = [(e.action_id, e.payload["tool"], e.payload["params"], e.payload["proposer"], e.payload["basis"])
                 for e in proposed_events]
    c.check("目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等",
            on_objects == in_events, f"对象上 {on_objects}；事件里 {in_events}")
    c.check("行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）",
            all(k == a.action_id for k, a in task.actions.items()) and list(task.actions) == proposed_ids,
            (list(task.actions), proposed_ids))
    c.check(f"登记即放入行动表：{len(run.register_snapshots)} 次登记返回时，返回的编号已在行动表里、表大小加一、"
            "状态是已提出、「行动提出」事件已为该编号发出",
            all(size_after == size_before + 1 and status == ActionStatus.PROPOSED and last_proposed == aid
                for aid, size_before, size_after, status, last_proposed in run.register_snapshots)
            and len(run.register_snapshots) == len(proposed_ids),
            run.register_snapshots)
    c.check(f"行动选择只返回候选、不写任何东西：{len(run.select_snapshots)} 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化",
            all(before == after for before, after in run.select_snapshots), run.select_snapshots)
    ended = named(events, TASK_ENDED)
    if task.end_record is None:
        c.check("投影：没有结束记录时事件流里也没有「任务结束」", not ended)
    else:
        c.check("投影：结束记录与「任务结束」事件的终态、原因一致",
                len(ended) == 1 and ended[0].payload == {"final_status": task.end_record.final_status,
                                                          "reason": task.end_record.reason})

    for action_id in proposed_ids:
        proposed = of_action(events, ACTION_PROPOSED, action_id)[0]
        first_status = action_history(events, action_id)[0]
        c.check(f"行动 {action_id} 的「行动提出」事件在「已提出」状态变化之前发出",
                proposed.seq < first_status.seq and first_status.payload["new_status"] == ActionStatus.PROPOSED)

    def on(box, name):
        return [e for e in events if e.name == name and e.payload["box"] == box]
    c.check("问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程",
            all(run.thread_of_seq[e.seq] == run.kernel_thread for e in on(OUTBOX, MESSAGE_PUT))
            and all(run.thread_of_seq[e.seq] == run.host_thread for e in on(OUTBOX, MESSAGE_TAKEN))
            and all(run.thread_of_seq[e.seq] == run.host_thread for e in on(INBOX, MESSAGE_PUT))
            and all(run.thread_of_seq[e.seq] == run.kernel_thread for e in on(INBOX, MESSAGE_TAKEN)))
    c.check("谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）",
            all(is_external(e.name, e.payload) == (run.thread_of_seq[e.seq] != run.kernel_thread) for e in all_events),
            [(e.seq, e.name, e.payload.get("box")) for e in all_events
             if is_external(e.name, e.payload) != (run.thread_of_seq[e.seq] != run.kernel_thread)])


def check_run_file(c: Checker, run: Run) -> None:
    """验证目标六的机器检查：用文件订阅者写出的 JSONL 重放，终态数据与运行时一致。"""
    c.check("目标六：文件订阅者写出了本场景的 JSONL 文件", run.run_file is not None and run.run_file.exists(), run.run_file)
    if run.run_file is None or not run.run_file.exists():
        return
    from_file = read_events(run.run_file)
    c.check("目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致",
            [(e.seq, e.kind, e.source, e.name) for e in from_file] == [(e.seq, e.kind, e.source, e.name) for e in run.all_events])
    if run.task is not None:
        c.check("目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据",
                replay_data(from_file) == run.task.data, f"文件重放 {replay_data(from_file)}，真实 {run.task.data}")
        for action in run.task.actions.values():
            history = [e.payload["new_status"] for e in action_history(from_file, action.action_id)]
            c.check(f"目标六：用文件得到的行动 {action.action_id} 状态经过（中文值）与内存事件一致",
                    history == [s.value for s in status_values(action_history(run.events, action.action_id))], history)


def check_selection_trail(c: Checker, run: Run, expected: dict) -> None:
    """第三步第十四项：依据命中值里的「选择经过」（前进过的阶段、跳过的阶段与原因、跳过的步骤与原因）；告知异常的命中值不带它。"""
    for proposed in named(run.events, ACTION_PROPOSED):
        hit = proposed.payload["basis"][2]
        aid = proposed.action_id
        if proposed.payload["tool"] == EXCEPTION_TOOL:
            c.check(f"行动 {aid} 是告知异常，命中值是异常报告，不带「选择经过」", isinstance(hit, dict) and "选择经过" not in hit, hit)
        elif aid in expected:
            c.check(f"行动 {aid} 的依据命中值里「选择经过」是 {expected[aid]}",
                    isinstance(hit, dict) and hit.get("选择经过") == expected[aid], hit)


def check_trace_shape(c: Checker, run: Run, loops: int, mailbox_tools=("ask",)) -> None:
    """追踪事件的形状：位置、数量与内容。只作诊断信息的检查，不参与前五条目标。"""
    all_events, events, task_def = run.all_events, run.events, run.task_def
    trace = [e for e in all_events if e.kind == TRACE]
    state_names = set(STATE_EVENT_NAMES)
    c.check("追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace",
            all((e.name in state_names) == (e.kind == STATE) for e in all_events))
    kernel_side = [e for e in all_events if not is_external(e.name, e.payload)]
    first = kernel_side[0] if kernel_side else None
    c.check("追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），"
            "槽位表、任务定义结构、工具清单、任务定义名与任务定义和工具表一致",
            first is not None and first.name == TASK_STARTED
            and first.payload == {"slots": dict(task_def.SLOTS), "definition": task_def.DEFINITION,
                                  "tools": run.tools_spec, "task_def_name": task_def.NAME},
            first.payload if first else None)
    loop_nos = [e.payload["loop_no"] for e in trace if e.name == LOOP_STARTED]
    c.check(f"追踪：恰好 {loops} 个「一次迭代开始」，迭代序号从 1 起连续", loop_nos == list(range(1, loops + 1)), loop_nos)
    done_flags = [e.payload["done"] for e in trace if e.name == CHECK_DONE_RESULT]
    # 结果检查在进循环前一次、每次迭代末尾一次；以内核错误结束的那次迭代在检查之前就抛错，没有末尾那次。
    expected_done = [False] * loops if run.error else [False] * loops + [True]
    c.check("追踪：「结果检查结论」循环前一次、每次迭代末尾一次，只有最后那次为真", done_flags == expected_done, done_flags)
    c.check("追踪：没有「任务定义错误」", not [e for e in trace if e.name == TASK_DEFINITION_ERROR])

    for proposed in [e for e in events if e.name == ACTION_PROPOSED]:
        aid = proposed.action_id
        mine = [e for e in all_events if e.action_id == aid]
        ctl = [e for e in mine if e.name == CONTROL_RESULT]
        c.check(f"追踪：行动 {aid} 有一条「执行控制结论」，结论已获准、核验恒允许",
                len(ctl) == 1 and ctl[0].payload == {"action_id": aid, "verdict": ActionStatus.APPROVED, "checked": "恒允许"})
        calls = [e for e in mine if e.name == EXECUTE_CALL]
        waits = [e for e in mine if e.name == MAILBOX_WAIT and e.payload["box"] == INBOX]
        c.check(f"追踪：行动 {aid} 的「行动执行调用」依次是 enter、return，线程是内核线程",
                [e.payload["phase"] for e in calls] == ["enter", "return"]
                and all(e.payload["thread"] == KERNEL_THREAD_PREFIX + run.task_id for e in calls))
        if proposed.payload["tool"] not in mailbox_tools:  # 第三步起告知异常也走邮箱，由新场景传入
            c.check(f"追踪：行动 {aid}（工具 {proposed.payload['tool']}）不碰邮箱，没有邮箱等待",
                    not waits and not [e for e in mine if e.name in (MESSAGE_PUT, MESSAGE_TAKEN)])
            continue
        c.check(f"追踪：行动 {aid} 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程",
                [e.payload["phase"] for e in waits] == ["begin", "end"]
                and isinstance(waits[1].payload.get("wait_ms"), (int, float)) and waits[1].payload["wait_ms"] >= 0
                and "wait_ms" not in waits[0].payload
                and all(e.payload["thread"] == KERNEL_THREAD_PREFIX + run.task_id for e in waits),
                [e.payload for e in waits])
        if len(calls) == 2 and len(waits) == 2:
            takes = [e for e in mine if e.name == MESSAGE_TAKEN and e.payload["box"] == INBOX]
            c.check(f"追踪：行动 {aid} 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后",
                    calls[0].seq < waits[0].seq < waits[1].seq < calls[1].seq
                    and all(t.seq > waits[1].seq for t in takes))


def expected_source(event, tool_of: dict) -> str:
    """按设计裁定，每个事件应有的记录方。"""
    if event.name in (DATA_CHANGED, TASK_STATUS_CHANGED, STEP_CHANGED):
        return SOURCE_UPDATE
    if event.name in (MESSAGE_PUT, MESSAGE_TAKEN, MAILBOX_CLOSED, MAILBOX_WAIT):
        return SOURCE_MAILBOX
    if event.name == ACTION_STATUS_CHANGED and event.payload["new_status"] not in (ActionStatus.PROPOSED, ActionStatus.APPROVED):
        return TOOL_SOURCE_PREFIX + tool_of[event.action_id]
    return SOURCE_LOOP


def check_sources_and_senders(c: Checker, run: Run, closed_before_failure_of: int | None) -> None:
    """记录方与发起方：记录方按组件写对；发起方自报的值用线程做事实核对；邮箱关闭有事件。"""
    all_events = run.all_events
    tool_of = {e.action_id: e.payload["tool"] for e in all_events if e.name == ACTION_PROPOSED}
    wrong = [(e.seq, e.name, e.source, expected_source(e, tool_of)) for e in all_events
             if e.source != expected_source(e, tool_of)]
    c.check("记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，"
            "工具实现记的状态 tool.<工具名>，其余 kernel.loop）", not wrong, wrong)
    status_sources = sorted({(e.payload["new_status"].value, e.source) for e in all_events if e.name == ACTION_STATUS_CHANGED})
    if run.events and any(e.name == ACTION_PROPOSED for e in run.events):
        expected_sources = {SOURCE_LOOP} | {TOOL_SOURCE_PREFIX + name for name in set(tool_of.values())}
        c.check("记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，"
                f"其余是所用工具的 tool.<工具名>：{sorted(expected_sources - {SOURCE_LOOP})}）",
                {src for _, src in status_sources} == expected_sources, status_sources)

    def on(box, name):
        return [e for e in all_events if e.name == name and e.payload["box"] == box]

    inbox_acts = on(INBOX, MESSAGE_PUT) + on(INBOX, MAILBOX_CLOSED)
    c.check("发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外",
            all(e.payload["sender"] in EXTERNAL_SENDERS and run.thread_of_seq[e.seq] != run.kernel_thread for e in inbox_acts),
            [(e.seq, e.name, e.payload["sender"]) for e in inbox_acts])
    questions = on(OUTBOX, MESSAGE_PUT)
    c.check("发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程",
            all(e.payload["sender"] == TOOL_SOURCE_PREFIX + tool_of[e.action_id] and e.payload["kind"] == "question"
                and e.payload["recipient"] == "user" and run.thread_of_seq[e.seq] == run.kernel_thread for e in questions),
            [(e.seq, e.payload) for e in questions])
    for box, expected_sender in ((INBOX, "user"), (OUTBOX, None)):
        puts = {e.payload["seq"]: e for e in on(box, MESSAGE_PUT)}
        takes = on(box, MESSAGE_TAKEN)
        c.check(f"发起方：{box} 的每条「消息取出」与它对应的「消息放入」发起方相同"
                + ("，回答都由 user 放入" if expected_sender else ""),
                all(t.payload["seq"] in puts and t.payload["sender"] == puts[t.payload["seq"]].payload["sender"]
                    and (expected_sender is None or t.payload["sender"] == expected_sender) for t in takes))

    inbox_closed, outbox_closed = on(INBOX, MAILBOX_CLOSED), on(OUTBOX, MAILBOX_CLOSED)
    c.check("邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致",
            run.inbox_closed == bool(inbox_closed) and run.outbox_closed == bool(outbox_closed))
    c.check("邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程",
            len(outbox_closed) == 1 and outbox_closed[0].payload == {"box": OUTBOX, "sender": SOURCE_LOOP}
            and run.thread_of_seq[outbox_closed[0].seq] == run.kernel_thread,
            [(e.seq, e.payload) for e in outbox_closed])
    ended = [e for e in all_events if e.name == TASK_ENDED]
    if ended:
        c.check("邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件",
                outbox_closed and outbox_closed[0].seq < ended[0].seq and run.events[-1].name == TASK_ENDED)
    if closed_before_failure_of is None:
        c.check("邮箱关闭：本场景宿主没有关闭收件箱", not inbox_closed)
    else:
        aid = closed_before_failure_of
        history = action_history(run.events, aid)
        waiting = [e for e in history if e.payload["new_status"] == ActionStatus.WAITING]
        failed = [e for e in history if e.payload["new_status"] == ActionStatus.FAILED]
        c.check(f"邮箱关闭：收件箱恰有一条「邮箱关闭」，发起方 host，落在行动 {aid} 的「等待中」与「已失败」之间",
                len(inbox_closed) == 1 and inbox_closed[0].payload == {"box": INBOX, "sender": "host"} and waiting and failed
                and waiting[0].seq < inbox_closed[0].seq < failed[0].seq,
                [(e.seq, e.payload) for e in inbox_closed])
        c.check(f"邮箱关闭：出错时，发件箱在行动 {aid}「已失败」之后由内核关闭",
                failed and outbox_closed and outbox_closed[0].seq > failed[0].seq)

    host_waits = [e for e in all_events if e.name == MAILBOX_WAIT and e.payload["box"] == OUTBOX]
    c.check("邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对",
            host_waits and all(run.thread_of_seq[e.seq] == run.host_thread and e.action_id is None for e in host_waits)
            and [e.payload["phase"] for e in host_waits] == ["begin", "end"] * (len(host_waits) // 2),
            [(e.seq, e.payload["phase"]) for e in host_waits])


def check_waiting_then_success(c: Checker, run: Run, action_id: int, expected_utterance=None) -> None:
    """验证目标二：询问行动在拿到回答前处于等待中，回答到达后完成。

    expected_utterance 由调用方给出时以它为准，不查预期句表：术语澄清确认那一问的话里带着模型现写的草稿，
    写不进模块级的常量表。
    """
    events = run.events
    history = action_history(events, action_id)
    c.check(f"目标二：行动 {action_id} 的状态序列是 已提出、已获准、等待中、已成功",
            status_values(history) == [ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.WAITING, ActionStatus.SUCCEEDED],
            [s.value for s in status_values(history)])
    if len(history) != 4:
        return
    waiting, succeeded = history[2], history[3]
    mine = [e for e in events if e.action_id == action_id]
    questions = [e for e in mine if e.name == MESSAGE_PUT and e.payload["box"] == OUTBOX]
    puts = [e for e in mine if e.name == MESSAGE_PUT and e.payload["box"] == INBOX]
    takes = [e for e in mine if e.name == MESSAGE_TAKEN and e.payload["box"] == INBOX]
    proposed = of_action(events, ACTION_PROPOSED, action_id)
    c.check(f"目标二：行动 {action_id} 恰有一条问题（发件箱放入），内容是 {{话, 参数}}，参数是行动参数原样，且在「等待中」之前",
            len(questions) == 1 and proposed and list(questions[0].payload["content"]) == ["utterance", "params"]
            and questions[0].payload["content"]["params"] == proposed[0].payload["params"]
            and questions[0].seq < waiting.seq)
    if len(questions) != 1:
        return
    target = proposed[0].payload["params"]["target"]
    if expected_utterance is None:
        expected_utterance = EXPECTED_UTTERANCES.get(target_key(target))
    c.check(f"第一步目标三：行动 {action_id} 问题里的话逐字等于预期句「{expected_utterance}」",
            expected_utterance is not None and questions[0].payload["content"]["utterance"] == expected_utterance,
            questions[0].payload["content"]["utterance"])
    c.check(f"目标二：行动 {action_id} 的「等待中」说明写明了问题的到达序号",
            waiting.payload["note"] == f"已向使用者提问，问题 {questions[0].payload['seq']}", waiting.payload["note"])
    c.check(f"目标二：行动 {action_id} 在收件箱恰有一条回答放入和一条取出", len(puts) == 1 and len(takes) == 1)
    if len(puts) != 1 or len(takes) != 1:
        return
    c.check(f"目标二：行动 {action_id} 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前",
            waiting.seq < puts[0].seq < takes[0].seq < succeeded.seq,
            (waiting.seq, puts[0].seq, takes[0].seq, succeeded.seq))
    c.check(f"目标二：行动 {action_id} 的回答的回复对象等于问题的到达序号",
            puts[0].payload["in_reply_to"] == takes[0].payload["in_reply_to"] == questions[0].payload["seq"])
    c.check(f"目标二：行动 {action_id} 的问题与回答，内容里的所属行动都是 {action_id}",
            questions[0].payload["action_id"] == puts[0].payload["action_id"] == takes[0].payload["action_id"] == action_id)
    changes = of_action(events, DATA_CHANGED, action_id)
    path = target["path"]
    c.check(f"目标二：行动 {action_id} 的数据变更新值按写入目标路径 {path} 取出的值 = 取出的回答 = 「已成功」事件的返回值；"
            "槽位是写入目标的槽位，除该路径外新旧值相同",
            len(changes) == 1 and changes[0].payload["slot"] == target["slot"]
            and get_at(changes[0].payload["new"], path) == takes[0].payload["content"] == succeeded.payload["result"]
            and (not path or tools_set_at(changes[0].payload["old"], path, takes[0].payload["content"]) == changes[0].payload["new"]))


def check_explainable(c: Checker, run: Run) -> None:
    """验证目标三的机器层。"""
    events, task = run.events, run.task
    c.check("目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致",
            replay_data(events) == task.data)
    for action in task.actions.values():
        proposed = of_action(events, ACTION_PROPOSED, action.action_id)[0]
        before = replay_data([e for e in events if e.seq < proposed.seq])
        c.check(f"目标三：重放到行动 {action.action_id} 的「行动提出」之前，得到的数据就是它的执行前实际数据",
                before == run.execute_snapshots.get(action.action_id),
                f"重放 {before}，实际 {run.execute_snapshots.get(action.action_id)}")
        terminal = action_history(events, action.action_id)[-1]
        c.check(f"目标三：由事件重建的行动 {action.action_id} 返回值与行动对象上的一致",
                terminal.payload.get("result") == action.result)


def check_kernel_is_task_agnostic(c: Checker) -> None:
    """内核与任务无关：源码里不出现任何任务的槽位名与工具名，也不导入工具表、任务与观测模块。

    第四步起零差异哈希断言取消（2026-09-16 用户裁定：四个内核文件都可以改，每处改动列进实施报告由用户逐条看），
    这组机械检查才是真正的不变量，所以把原先分在两处的词表检查与导入检查并到一起。
    """
    source_path = Path(kernel.__file__)
    source = source_path.read_text(encoding="utf-8")
    words = ("目的地", "日期", "事由",  # 出差申请单（该任务的场景已退役，词表留着看住内核）
             "目录", "文件总表", "材料清单", "登记进度", "清单文件路径", "list_dir", "register_file", "generate_manifest",
             "术语", "原文片段", "释义草稿", "确认释义", "生成术语释义")  # 术语澄清
    hits = {word: source.count(word) for word in words if source.count(word)}
    c.check("内核文件里不出现三个任务的槽位名与工具名", not hits, hits)
    c.check("内核文件里不出现工具名（字符串 \"ask\" 与「询问」）",
            not re.search(r"""["']ask["']""", source) and "询问" not in source)
    from tod_kernel import taskdef as taskdef_module
    loader_source = Path(taskdef_module.__file__).read_text(encoding="utf-8")
    stale = {name: text.count(word) for name, text in (("kernel.py", source), ("taskdef.py", loader_source))
             for word in ("游标", "cursor") if text.count(word)}
    c.check("内核与任务定义加载器里不再出现「游标」「cursor」（第四步 4.10 节废除了这个词与那个保留槽位）", not stale, stale)
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported |= {f"{module}.{alias.name}" for alias in node.names}
    forbidden = {name for name in imported
                 if any(part in ("tools", "task_travel", "observe", "llm", "taskdef", "console") for part in name.split("."))}
    c.check("内核模块的导入语句里没有工具表、任务定义加载器、模型调用件、观测模块与控制台", not forbidden, sorted(imported))


# 第三步目标一留下的历史锚点：材料接入登记正常场景在第二步提交 01de6ab 前最后一次全量运行的运行文件。
# runs/ 不入版本库，所以这条断言只在留有这份文件的机器上成立；文件缺失判失败，不跳过。
# 出差申请单四个场景第四步退役，它们那四行随之删掉（2026-09-16 用户裁定）。
STEP_TWO_RUN_FILES = {
    "T-intake-1": "T-intake-1_20260914T165954608.jsonl",
}


def action_sequence(events) -> list:
    """行动序列：每个「行动提出」的（工具名, 参数实际值, 依据序号），经 JSON 往返统一元组与列表。"""
    import json as json_module

    rows = [(e.payload["tool"], e.payload["params"], e.payload["basis"][0]) for e in events if e.name == ACTION_PROPOSED]
    return json_module.loads(json_module.dumps(rows, ensure_ascii=False))


def check_matches_step_two(c: Checker, run: Run) -> None:
    """第三步目标一：行动序列与第二步留下的旧运行文件逐位相等。"""
    old_path = RUNS_DIR / STEP_TWO_RUN_FILES[run.task_id]
    c.check(f"第三步目标一：第二步的旧运行文件 {old_path.name} 存在（只在本机成立）", old_path.is_file(), old_path)
    if not old_path.is_file():
        return
    old, new = action_sequence(read_events(old_path)), action_sequence(run.events)
    c.check(f"第三步目标一：行动序列（工具名、参数实际值、依据序号）与 {old_path.name} 逐位相等，共 {len(old)} 个行动",
            old == new, f"旧 {old}；新 {new}")


# ───────────────────────── 打印横幅 ─────────────────────────

def banner(text: str) -> None:
    print(f"\n{'═' * 12} {text} {'═' * 12}", flush=True)


# ───────────────────────── 场景：材料接入登记 ─────────────────────────

INTAKE_TOOLS = ("ask", "list_dir", "register_file", "generate_manifest")
INTAKE_ANSWERS = {
    ("材料清单", (0, "是否纳入")): "是",
    ("材料清单", (1, "是否纳入")): "否",
    ("材料清单", (2, "是否纳入")): "是",
}
INTAKE_FINAL_DATA = {
    "目录": "样例材料",
    "文件总表": ["a.docx", "b.pdf", "c.xlsx"],
    "材料清单": [
        {"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": "是"},
        {"文件名": "b.pdf", "类型": "pdf", "大小": 51200, "页数": 5, "是否纳入": "否"},
        {"文件名": "c.xlsx", "类型": "xlsx", "大小": 10240, "页数": 1, "是否纳入": "是"},
    ],
    "登记进度": 3,
    "清单文件路径": "样例材料/材料清单.txt",
}


def check_other_tool_actions(c: Checker, run: Run) -> None:
    """非询问行动：状态经过是已提出、已获准、已成功；变更只写该工具声明的可写槽位。"""
    events, task = run.events, run.task
    for action in task.actions.values():
        if action.tool == "ask":
            continue
        history = status_values(action_history(events, action.action_id))
        slots = {e.payload["slot"] for e in of_action(events, DATA_CHANGED, action.action_id)}
        c.check(f"行动 {action.action_id}（{action.tool}）的状态经过是 已提出、已获准、已成功，"
                f"变更只写可写槽位 {sorted(run.writable.get(action.tool) or [])}",
                history == [ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.SUCCEEDED]
                and slots and slots <= set(run.writable.get(action.tool) or ()),
                (history, slots))


def common_checks(c: Checker, run: Run, loops: int, closed_before_failure_of=None) -> None:
    """每个跑起来的场景都做的四组检查：完整性、追踪事件的形状、记录方与发起方、运行文件。"""
    check_integrity(c, run)
    check_trace_shape(c, run, loops=loops)
    check_sources_and_senders(c, run, closed_before_failure_of=closed_before_failure_of)
    check_run_file(c, run)


def rule_numbers(proposed_events) -> list:
    return [e.payload["basis"][0] for e in proposed_events]


def intake_scenario_one() -> Checker:
    banner("材料接入登记·场景一：正常流程")
    run = run_scenario("T-intake-1", intake_def(), INTAKE_ANSWERS, INTAKE_TOOLS)
    c = Checker("材料接入登记·场景一：正常流程")
    print("── 断言 ──")
    check_kernel_is_task_agnostic(c)
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    events, task = run.events, run.task
    proposed = named(events, ACTION_PROPOSED)
    tools = [e.payload["tool"] for e in proposed]
    c.check("第一步目标二：恰好八个行动（八次迭代每次一个行动，第八次迭代末尾的结果检查为真）", len(proposed) == 8, len(proposed))
    c.check("第一步目标二：工具依次是 列目录、登记文件×3、询问×3、生成清单文件",
            tools == ["list_dir", "register_file", "register_file", "register_file", "ask", "ask", "ask", "generate_manifest"], tools)
    c.check("第一步目标二：依据里的规则序号依次是 一、二、二、二、三、三、三、四",
            rule_numbers(proposed) == [1, 2, 2, 2, 3, 3, 3, 4], rule_numbers(proposed))
    c.check("第一步目标二：登记文件的参数序号依次是 0、1、2",
            [e.payload["params"] for e in proposed if e.payload["tool"] == "register_file"] == [{"index": 0}, {"index": 1}, {"index": 2}])
    c.check("第一步目标二：询问的写入目标依次是 材料清单 [0/1/2, 是否纳入]，提示是文件名",
            [e.payload["params"] for e in proposed if e.payload["tool"] == "ask"]
            == [{"target": {"slot": "材料清单", "path": [i, "是否纳入"]}, "hint": {"file": f}}
                for i, f in enumerate(["a.docx", "b.pdf", "c.xlsx"])])
    c.check("任务状态是已完成，终态数据与预期完全相同", task.status == TaskStatus.DONE  # 第三步：只比业务槽位
            and task.data == INTAKE_FINAL_DATA, task.data)
    check_step_final(c, run, step_at("生成清单", 1))
    check_matches_step_two(c, run)
    c.check("终态材料清单三项的是否纳入依次是 是、否、是",
            [item["是否纳入"] for item in task.data["材料清单"]] == ["是", "否", "是"])
    manifest = [a for a in task.actions.values() if a.tool == "generate_manifest"]
    text = manifest[0].result if manifest else ""
    c.check("生成清单文件的返回值含 a.docx 与 c.xlsx、不含 b.pdf",
            manifest and "a.docx" in text and "c.xlsx" in text and "b.pdf" not in text, text)
    for action in task.actions.values():
        if action.tool == "ask":
            check_waiting_then_success(c, run, action.action_id)
    check_other_tool_actions(c, run)
    check_explainable(c, run)
    common_checks(c, run, loops=8)
    return c


# ───────────────────────── 第二步：观测台 ─────────────────────────

# 七个场景的运行参数：（任务标识, 任务定义, 答案表, 工具名）。
def all_scenarios():
    """会真跑起来的场景的运行参数，每项是一份给 run_scenario 的关键字参数。

    观测台那组检查用它连跑两遍看运行文件。加载错误那个场景不产生运行文件，不在其中。
    """
    return [
        dict(task_id="T-intake-1", task_def=intake_def(), answers=INTAKE_ANSWERS, tool_names=INTAKE_TOOLS),
        dict(task_id="T-exception-3", task_def=intake_def("intake_bad_goal.json"), answers=INTAKE_ANSWERS,
             tool_names=EXCEPTION_TOOLS, exception_answers=["被动终止"]),
        dict(task_id="T-glossary-1", task_def=glossary_def(dict(GLOSSARY_INPUT_ONE)), answers=GLOSSARY_ANSWERS_ONE,
             tool_names=GLOSSARY_TOOLS, call=glossary_call()[0]),
        dict(task_id="T-glossary-2", task_def=glossary_def(dict(GLOSSARY_INPUT_TWO)), answers=GLOSSARY_ANSWERS_TWO,
             tool_names=GLOSSARY_TOOLS, call=glossary_call()[0]),
    ]


def expected_summaries() -> dict:
    """摘要预期表：任务标识 → （任务定义名, 终态, 迭代数, 行动数）。

    写成函数而不是模块级字典，因为任务定义名取自后面定义的常量。
    结果检查第三步起挪到迭代末尾，所以正常完成的运行迭代数等于行动数；以内核错误结束的那次迭代没有末尾检查，数目不变。
    """
    return {
        "T-intake-1": ("材料接入登记", "已完成", 8, 8),
        "T-exception-3": (BAD_GOAL_NAME, "内核错误", 5, 5),
        "T-glossary-1": ("术语澄清", "已完成", 3, 3),
        "T-glossary-2": ("术语澄清", "已完成", 6, 6),
    }


def http_get(port: int, raw_path: str):
    """按原始路径发请求（不做 .. 规范化），返回（状态码, 响应体字节）。"""
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", raw_path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def observatory_checks() -> Checker:
    import json as json_module
    import shutil
    import tempfile
    import urllib.parse

    banner("第二步：观测台")
    c = Checker("第二步：观测台")
    print("── 断言 ──")
    check_kernel_is_task_agnostic(c)
    work = Path(tempfile.mkdtemp(prefix="tod-observatory-"))
    try:
        runs_dir = work / "runs"
        expected = expected_summaries()
        for round_no in (1, 2):
            for kwargs in all_scenarios():
                run_scenario(runs_dir=runs_dir, console=False, **kwargs)
        files = sorted(runs_dir.iterdir())
        scenario_count = len(all_scenarios())

        # 目标一：每次运行都留下来、都能找到。
        c.check(f"第二步目标一：临时空目录里连跑两遍后恰有 {2 * scenario_count} 个文件", len(files) == 2 * scenario_count, [f.name for f in files])
        c.check("第二步目标一：文件名两两不同，且都是「任务标识_开始时刻.jsonl」",
                len({f.name for f in files}) == len(files)
                and all(re.fullmatch(r"T-[a-z]+-\d_\d{8}T\d{9}(_\d+)?\.jsonl", f.name) for f in files),
                [f.name for f in files])
        per_task: dict[str, int] = {}
        wrong = []
        for f in files:
            summary = summarize(f)
            per_task[summary.task_id] = per_task.get(summary.task_id, 0) + 1
            actual = (summary.task_def_name, summary.final_status, summary.loops, summary.actions)
            if expected.get(summary.task_id) != actual:
                wrong.append((f.name, actual))
        c.check("第二步目标一：每份文件的摘要（任务定义名、终态、迭代数、行动数）与预期表逐行相等", not wrong, wrong)
        c.check("第二步目标一：每个任务标识恰有两份文件", per_task == {task_id: 2 for task_id in expected}, per_task)
        c.check("第二步目标一：每份文件的事件序号从 1 起连续，且含「任务开始」",
                all([e.seq for e in read_events(f)] == list(range(1, len(read_events(f)) + 1))
                    and any(e.name == "TASK_STARTED" for e in read_events(f)) for f in files))

        # 目标二：看一次运行不生成文件；服务三个接口。
        c.check("第二步目标二：仓库里不再有 view.py", not (Path(kernel.__file__).resolve().parent / "view.py").exists())
        c.check("第二步目标二：运行目录里只有 .jsonl，没有 .html", all(f.suffix == ".jsonl" for f in files), [f.name for f in files])
        # 旧命名文件照样能读：复制一份成不带时刻的文件名。
        old_dir = work / "old"
        old_dir.mkdir()
        old_file = old_dir / "T-intake-1.jsonl"
        by_task = {}
        for f in files:
            by_task.setdefault(summarize(f).task_id, f)  # 按摘要里的任务标识挑文件，不依赖文件名
        if "T-intake-1" in by_task:
            shutil.copyfile(by_task["T-intake-1"], old_file)
            old_summary = summarize(old_file)
            c.check("第二步：旧命名文件照样能读，开始时刻取文件修改时间并标出来源",
                    old_summary.started_at_source == "文件修改时间"
                    and (old_summary.task_def_name, old_summary.final_status, old_summary.loops, old_summary.actions) == expected["T-intake-1"],
                    old_summary)
        else:
            c.check("第二步：旧命名文件照样能读（找不到 T-intake-1 的运行文件，无法检查）", False)

        server = make_server(runs_dir, "0.0.0.0", 0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, name="observatory-test", daemon=True)
        thread.start()
        try:
            status, body = http_get(port, "/")
            c.check("第二步目标二：GET / 返回 200 和观测台页面", status == 200 and "观测台".encode("utf-8") in body, status)
            status, body = http_get(port, "/api/runs")
            index = json_module.loads(body) if status == 200 else []
            c.check(f"第二步目标一：GET /api/runs 返回 200，行数等于文件数 {len(files)}", status == 200 and len(index) == len(files), (status, len(index)))
            c.check("第二步目标一：索引按开始时刻倒序", [row["started_at"] for row in index] == sorted((row["started_at"] for row in index), reverse=True))
            mismatched = []
            for f in files:
                status, body = http_get(port, "/api/runs/" + urllib.parse.quote(f.name))
                lines = sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
                if status != 200 or len(json_module.loads(body)) != lines:
                    mismatched.append((f.name, status))
            c.check("第二步目标二：每份文件经 GET /api/runs/<文件名> 返回的事件数等于文件行数", not mismatched, mismatched)
            # 其中 ../old/T-intake-1.jsonl 是运行目录之外真实存在的文件，拦截失效时会返回 200。
            for raw in ("/api/runs/../old/T-intake-1.jsonl", "/api/runs/..%2Fold%2FT-intake-1.jsonl",
                        "/api/runs/%2E%2E%2Fold%2FT-intake-1.jsonl", "/api/runs/../x", "/api/runs/nope.jsonl", "/api/runs/"):
                status, _ = http_get(port, raw)
                c.check(f"第二步目标二：路径穿越或不存在的文件 {raw} 返回 404", status == 404, status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

        # 同一个文件订阅者：序号回绕认作新运行；「任务开始」前的事件缓存后写入；没有「任务开始」不写文件。
        sample = read_events(by_task["T-intake-1"]) if "T-intake-1" in by_task else []
        head = [e for e in sample if e.name != "TASK_STARTED"][:1]  # 取一个非「任务开始」事件放到最前
        replay = head + [e for e in sample if not head or e is not head[0]]
        replay = [dataclasses.replace(e, seq=i + 1) for i, e in enumerate(replay)]
        writer_dir = work / "writer"
        writer = FileWriter(writer_dir)
        for _ in range(2):
            for e in replay:
                writer(e)
        if replay:
            writer(dataclasses.replace(replay[0], seq=1))  # 第三次运行只有一个事件，没有「任务开始」
        written = sorted(writer_dir.iterdir())
        contents = [[json_module.loads(line)["seq"] for line in f.read_text(encoding="utf-8").splitlines()] for f in written]
        c.check("第二步：同一个文件订阅者对同一任务标识，序号回绕后另起一份文件；两份都完整且按原序写出缓存的事件；"
                "没有「任务开始」的第三次运行不写文件",
                replay and len(written) == 2 and all(seqs == list(range(1, len(replay) + 1)) for seqs in contents)
                and writer.paths == written,
                ([f.name for f in written], contents))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c


# ───────────────────────── 场景：加载错误 ─────────────────────────

def _drop_top_key(definition):
    del definition["阶段列表"]


def _unknown_slot(definition):
    definition["阶段列表"][1]["步骤"][0]["参数"]["index"] = {"槽位": "登记序号"}


def _repeat_without_max(definition):
    del definition["阶段列表"][1]["步骤"][0]["最多"]  # 登记步骤只写「重复直到」不写「最多」


# 三份写坏的定义：（标题, 从哪份正确文件复制, 怎么改坏, 错误位置, 错误原因里应含的文字）。
# 第四步按用户裁定精简为三份，另外六份连生成代码一起退役；它们证明过的事在第三步 6.1 验收记录里有据可查。
BAD_DEFINITIONS = [
    ("缺顶层键", "intake.json", _drop_top_key, "顶层", "缺少键「阶段列表」"),
    ("引用不存在的槽位", "intake.json", _unknown_slot, "阶段列表[1]（登记）.步骤[0].参数.index.槽位", "不存在的槽位「登记序号」"),
    ("步骤只写重复直到不写最多", "intake.json", _repeat_without_max, "阶段列表[1]（登记）.步骤[0]", "要么都写要么都不写，缺少「最多」"),
]


def load_error_checks() -> Checker:
    """场景：加载错误，三份坏文件。三份写在一个场景里（第 6 节的场景表把它们算作一行）。"""
    title = "加载错误：三份坏文件"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    for bad_title, source, breaker, where, reason_part in BAD_DEFINITIONS:
        check_one_bad_definition(c, bad_title, source, breaker, where, reason_part)
    return c


def check_one_bad_definition(c: Checker, title: str, source: str, breaker, where: str, reason_part: str) -> None:
    """第三步目标三：一份写坏的定义在启动任务之前被加载器挡住，不写出任何事件。

    在临时目录里从正确文件复制一份再改坏；宿主照常先建事件流、挂订阅者，再加载，加载成功才启动任务。
    """
    import json as json_module
    import shutil
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="tod-load-error-"))
    try:
        original = TASK_DEFS_DIR / source
        taskdef.load(original)  # 正确文件本身能加载，否则下面的错误说明不了问题
        definition = json_module.loads(original.read_text(encoding="utf-8"))
        breaker(definition)
        path = work / f"bad_{source}"
        path.write_text(json_module.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
        c.check(f"{title}：写坏的定义文件真实存在，从 {source} 复制后改坏", path.is_file(), path)

        task_id = "T-load-error"
        stream = EventStream(task_id)
        collector = MemoryCollector()
        stream.subscribe(collector)
        runs_dir = work / "runs"
        stream.subscribe(FileWriter(runs_dir))
        error = None
        try:
            task_def = taskdef.load(path, initial=dict(SAMPLE_DIR_INPUT) if source.startswith("intake") else None)
            inbox, outbox = Mailbox(stream, task_id, INBOX), Mailbox(stream, task_id, OUTBOX)
            kernel.start_task(task_id, task_def, build_table(task_def, INTAKE_TOOLS), inbox, outbox, stream)
        except BaseException as exc:  # 加载错误与意外异常都交给断言
            error = exc
        c.check(f"{title}：加载抛出加载错误（LoadError）", isinstance(error, taskdef.LoadError), repr(error))
        if isinstance(error, taskdef.LoadError):
            c.check(f"{title}：加载错误的 path 属性是写坏的文件路径，错误信息里含该路径",
                    error.path == str(path) and str(path) in str(error), (error.path, str(error)))
            c.check(f"{title}：加载错误的 where 属性是「{where}」，错误信息里含该位置",
                    error.where == where and where in str(error), (error.where, str(error)))
            c.check(f"{title}：错误信息写明原因，含「{reason_part}」", reason_part in str(error), str(error))
        c.check(f"{title}：没有任何事件写出，内存收集器为空", collector.events == [], [e.name for e in collector.events])
        c.check(f"{title}：没有任何事件写出，运行目录里没有文件",
                not runs_dir.exists() or not any(runs_dir.iterdir()),
                sorted(p.name for p in runs_dir.iterdir()) if runs_dir.exists() else None)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# 三份写坏的定义里属于结构错误、应当被 JSON Schema 拦下的两份；「引用不存在的槽位」是语义错误，schema 管不了，由加载器拦。
SCHEMA_STRUCTURAL = {"缺顶层键", "步骤只写重复直到不写最多"}
SCHEMA_FILE = "任务定义.schema.json"


def schema_checks() -> Checker:
    """第三步第九项：任务定义文件的 JSON Schema。现存三份好文件通过；三份写坏的定义里结构错误被拦下，语义错误放行。
    用本机的 jsonschema 库；没装时这组不做断言，打印提示。"""
    import json as json_module

    title = "第三步·任务定义 JSON Schema"
    banner(title)
    c = Checker(title)
    try:
        import jsonschema
    except ImportError:
        print("提示：本机没有安装 jsonschema，这组断言跳过（pip install jsonschema 后重跑）")
        return c
    print("── 断言 ──")
    schema = json_module.loads((TASK_DEFS_DIR / SCHEMA_FILE).read_text(encoding="utf-8"))
    schema_error = None
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        schema_error = exc
    c.check("schema 文件本身是合法的 JSON Schema 2020-12", schema_error is None, repr(schema_error))
    if schema_error is not None:
        return c
    validator = jsonschema.Draft202012Validator(schema)
    good = sorted(path for path in TASK_DEFS_DIR.glob("*.json") if path.name != SCHEMA_FILE)
    for path in good:
        errors = [e.message for e in validator.iter_errors(json_module.loads(path.read_text(encoding="utf-8")))]
        c.check(f"好文件 {path.name} 通过 schema", not errors, errors)
    c.check("好文件恰好三份（材料接入登记、它的目标写错样例、术语澄清）", len(good) == 3, [path.name for path in good])
    for bad_title, source, breaker, _, _ in BAD_DEFINITIONS:
        definition = json_module.loads((TASK_DEFS_DIR / source).read_text(encoding="utf-8"))
        breaker(definition)
        errors = [e.message for e in validator.iter_errors(definition)]
        if bad_title in SCHEMA_STRUCTURAL:
            c.check(f"写坏的定义「{bad_title}」是结构错误，被 schema 拦下", bool(errors), errors)
        else:
            c.check(f"写坏的定义「{bad_title}」是语义错误，schema 放行、由加载器拦下", not errors, errors)
    return c


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


# ───────────────────────── 场景：材料接入登记的目标写错与异常告知 ─────────────────────────

EXCEPTION_TOOLS = INTAKE_TOOLS + (EXCEPTION_TOOL,)
BAD_GOAL_NAME = "材料接入登记（登记目标写错）"
OPTIONS_TEXT = "可选措施：重做本阶段、主动终止、被动终止。"

# 登记目标写错的样例：登记阶段目标是「相等：登记进度 与 99」，告知异常的依据序号是 6（四个步骤号之后，第二个阶段）。
BAD_GOAL_PREDICATE = {"谓词": "相等", "左": {"槽位": "登记进度"}, "右": 99}
BAD_GOAL_EXCEPTION_NUMBER = 6


REGISTER_NOTE = "登记下一个文件的名字、类型、大小、页数"


def position(stage, index, note, loop, loop_last) -> dict:
    """告知异常参数「当前步」：阶段、阶段内序号、这一步的说明、循环（起、止、第几次）、是不是段尾。"""
    return {"阶段": stage, "步骤": index, "说明": note,
            "循环": None if loop is None else {"起": loop[0], "止": loop[1], "第几次": loop[2]},
            "是段尾": loop_last}


def bad_goal_params(at: dict) -> dict:
    return {
        "阶段": "登记",
        "未达成目标": [{"谓词": BAD_GOAL_PREDICATE, "当前值": {"左": 3, "右": 99}, "文字": "登记进度 等于 99，当前值 3"}],
        "步骤现况": [{"步骤": 2, "工具": "register_file", "成立": False, "说明": "序号等于登记进度且小于文件总数",
                   "命中值": {"登记进度": 3, "文件总数": 3}}],
        "当前步": at,
        "可选措施": list(EXCEPTION_OPTIONS),
    }


# 登记三次之后：登记阶段只有一步，它自己是一段循环，所以阶段内序号是 1、这段循环已完成 3 次。
BAD_GOAL_AT_END = position("登记", 1, REGISTER_NOTE, (1, 1, 3), True)
BAD_GOAL_UTTERANCE_END = (f"阶段『登记』目标未达成：登记进度 等于 99，当前值 3；已完成『登记』阶段第 1 步『{REGISTER_NOTE}』，这段循环已完成 3 次。"
                          f"{OPTIONS_TEXT}")


def check_exception_action(c: Checker, run: Run, action_id: int, number: int, stage: str,
                           params: dict, utterance: str, answer: str, kind: str = "一趟走完") -> None:
    """一条告知异常行动：候选内容、发出的问题、状态经过、返回值、变更组。"""
    events = run.events
    proposed = of_action(events, ACTION_PROPOSED, action_id)
    c.check(f"行动 {action_id} 是告知异常，提出者是行动选择，依据序号 {number}、说明「{stage} › 告知异常」，"
            f"命中值是这份异常报告外加异常种类「{kind}」",
            len(proposed) == 1 and proposed[0].payload["tool"] == EXCEPTION_TOOL and proposed[0].payload["proposer"] == "selector"
            and list(proposed[0].payload["basis"]) == [number, f"{stage} › {EXCEPTION_TOOL}", {**params, "异常种类": kind}],
            proposed[0].payload if proposed else None)
    c.check(f"行动 {action_id} 的五个参数与预期逐项相等",
            proposed and proposed[0].payload["params"] == params, proposed[0].payload["params"] if proposed else None)
    questions = [e for e in of_action(events, MESSAGE_PUT, action_id) if e.payload["box"] == OUTBOX]
    c.check(f"行动 {action_id} 恰发出一条问题，类型 question，内容是 {{话, 参数}}，参数与行动参数相同",
            len(questions) == 1 and questions[0].payload["kind"] == "question"
            and list(questions[0].payload["content"]) == ["utterance", "params"]
            and questions[0].payload["content"]["params"] == params,
            [e.payload for e in questions])
    if len(questions) == 1:
        c.check(f"行动 {action_id} 的话逐字等于「{utterance}」",
                questions[0].payload["content"]["utterance"] == utterance, questions[0].payload["content"]["utterance"])
    history = action_history(events, action_id)
    terminal, result, note = {
        "重做本阶段": (ActionStatus.SUCCEEDED, "重做", "使用者选择重做本阶段"),
        "主动终止": (ActionStatus.FAILED, "主动终止", "使用者主动终止"),
        "被动终止": (ActionStatus.FAILED, "被动终止", "任务无法继续，使用者确认终止"),
    }[answer]
    c.check(f"行动 {action_id} 的状态经过是 已提出、已获准、等待中、{terminal.value}，最后一条说明「{note}」，返回值「{result}」",
            status_values(history) == [ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.WAITING, terminal]
            and history[-1].payload["note"] == note and history[-1].payload.get("result") == result,
            [(e.payload["new_status"].value, e.payload["note"], e.payload.get("result")) for e in history])
    changes = of_action(events, DATA_CHANGED, action_id)
    steps = of_action(events, STEP_CHANGED, action_id)
    c.check(f"行动 {action_id} 没有数据变更：告知异常工具不写任何槽位", not changes, [e.payload for e in changes])
    if answer == "重做本阶段":
        c.check(f"行动 {action_id} 选重做：发一条当前步变化，新值是「{stage}」阶段起点，来源是这个行动",
                len(steps) == 1 and steps[0].payload["new"] == step_at(stage) and steps[0].payload["source"] == action_id,
                [e.payload for e in steps])
    else:
        c.check(f"行动 {action_id} 选终止：当前步不动，没有当前步变化事件", not steps, [e.payload for e in steps])


def exception_common(c: Checker, run: Run, loops: int) -> None:
    check_integrity(c, run)
    check_trace_shape(c, run, loops=loops, mailbox_tools=("ask", EXCEPTION_TOOL))
    check_sources_and_senders(c, run, closed_before_failure_of=None)
    check_run_file(c, run)
    check_explainable(c, run)


def check_registered_three(c: Checker, run: Run) -> None:
    proposed = named(run.events, ACTION_PROPOSED)[:4]
    c.check("前四个行动依次是 列目录、登记文件×3，依据序号 1、2、2、2",
            [(e.payload["tool"], e.payload["basis"][0]) for e in proposed]
            == [("list_dir", 1), ("register_file", 2), ("register_file", 2), ("register_file", 2)],
            [(e.payload["tool"], e.payload["basis"][0]) for e in proposed])


def check_terminated(c: Checker, run: Run, action_id: int, note: str) -> None:
    c.check(f"内核错误携带行动 {action_id}", isinstance(run.error, KernelError) and run.error.action is not None
            and run.error.action.action_id == action_id, repr(run.error))
    c.check("任务没有结束：没有「任务结束」事件，任务状态仍是执行中",
            not named(run.events, TASK_ENDED) and run.task is not None and run.task.status == TaskStatus.RUNNING)
    summary = summarize(run.run_file) if run.run_file is not None and run.run_file.exists() else None
    c.check(f"运行索引的摘要：终态「内核错误」，原因「{note}」",
            summary is not None and summary.final_status == "内核错误" and summary.reason == note,
            (summary.final_status, summary.reason) if summary else None)


def intake_exception_scenario() -> Checker:
    """场景：材料接入登记的登记阶段目标写错，系统告知异常，使用者选被动终止。

    第四步起异常路径只留这一个场景（重做、主动终止、后续阶段破坏前面阶段目标三个分支退役，
    2026-09-16 用户裁定的取舍，它们证明过的事在第三步 6.1 验收记录里有据可查）。
    """
    title = "材料接入登记：登记阶段目标写错报异常，使用者选被动终止"
    answer, note = "被动终止", "任务无法继续，使用者确认终止"
    banner(title)
    run = run_scenario("T-exception-3", intake_def("intake_bad_goal.json"), INTAKE_ANSWERS, EXCEPTION_TOOLS,
                       exception_answers=[answer])
    c = Checker(title)
    print("── 断言 ──")
    check_registered_three(c, run)
    proposed = named(run.events, ACTION_PROPOSED)
    c.check("恰好五个行动，第五个（第五次迭代）是告知异常",
            [e.payload["tool"] for e in proposed] == ["list_dir"] + ["register_file"] * 3 + [EXCEPTION_TOOL],
            [e.payload["tool"] for e in proposed])
    check_exception_action(c, run, 5, BAD_GOAL_EXCEPTION_NUMBER, "登记", bad_goal_params(BAD_GOAL_AT_END),
                           BAD_GOAL_UTTERANCE_END, answer)
    check_terminated(c, run, 5, note)
    check_step_final(c, run, step_at("登记", 1, loop=(1, 1, 3)))
    # 第 5 次迭代是告知异常，使用者选终止：行动记已失败，当前步不动，所以只有五条当前步变化。
    c.check("当前步逐次记下走到哪：初始化、列目录一步、登记那一步循环三次；告知异常那次不写当前步",
            step_sequence(run) == [
                ("初始化", step_at("列目录")),
                (1, step_at("列目录", 1)),
                (2, step_at("登记", 1, loop=(1, 1, 1))),
                (3, step_at("登记", 1, loop=(1, 1, 2))),
                (4, step_at("登记", 1, loop=(1, 1, 3)))], step_sequence(run))
    exception_common(c, run, loops=5)
    return c


def current_step_checks() -> Checker:
    """当前步（第四步 4.10 节）：初始值、记录本步的三条规则、「第几次」怎么加、不合法怎么报、两个显示接口。

    手写当前步与行动，不跑任务：这一组要验的是规则本身。场景里的当前步序列另在各场景断言里逐次核对。
    """
    from tod_kernel.kernel import DefinitionError
    from tod_kernel.taskdef import STEP_STAGE

    title = "第四步：当前步的记录、判据与显示"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    glossary = glossary_def(dict(GLOSSARY_INPUT_ONE))
    intake = intake_def("intake.json")

    c.check("初始当前步是第一个阶段的起点，只有「阶段」这一层",
            glossary.INITIAL_STEP == step_at("写释义草稿") and intake.INITIAL_STEP == step_at("列目录"),
            (glossary.INITIAL_STEP, intake.INITIAL_STEP))

    def action(number, status=ActionStatus.SUCCEEDED, result=None):
        """一个只有记录本步用得着的部分的行动：依据序号、终态、返回值。"""
        made = Action(tool="无所谓", params={}, proposer="selector", basis=(number, "无所谓", {}))
        made.status, made.result = status, result
        return made

    # 规则一：行动没成功，当前步不动。
    before = step_at("确认", 2, loop=(1, 3, 2))
    c.check("记录本步规则一：行动已失败时当前步原样不动",
            glossary.record_step(before, action(3, ActionStatus.FAILED)) == before,
            glossary.record_step(before, action(3, ActionStatus.FAILED)))
    # 规则二：告知异常且返回值「重做」回到该阶段起点；终止不动。
    redo = intake.record_step(step_at("登记", 1, loop=(1, 1, 3)), action(6, result="重做"))
    stop = intake.record_step(step_at("登记", 1, loop=(1, 1, 3)), action(6, result="被动终止"))
    c.check("记录本步规则二：告知异常选重做，当前步回到该阶段起点『登记』；选终止时不动",
            redo == step_at("登记") and stop == step_at("登记", 1, loop=(1, 1, 3)), (redo, stop))
    # 规则三：其余写该步的阶段与阶段内序号，落在循环段里时带起止与第几次。
    c.check("记录本步规则三：不在循环段里的一步只写阶段与阶段内序号",
            glossary.record_step(step_at("写释义草稿"), action(2)) == step_at("写释义草稿", 2),
            glossary.record_step(step_at("写释义草稿"), action(2)))

    # 「第几次」：进这段循环记 1；同一次里沿用；回到段首加 1；段尾被跳过、停在段中时回段首也加 1。
    first = glossary.record_step(step_at("写释义草稿", 2), action(3))
    same = glossary.record_step(first, action(4))
    again = glossary.record_step(step_at("确认", 3, loop=(1, 3, 1)), action(3))
    skipped = glossary.record_step(step_at("确认", 2, loop=(1, 3, 1)), action(3))
    c.check("「第几次」：从循环段外进来记第 1 次",
            first == step_at("确认", 1, loop=(1, 3, 1)), first)
    c.check("「第几次」：同一次里往后走，第几次沿用",
            same == step_at("确认", 2, loop=(1, 3, 1)), same)
    c.check("「第几次」：走完段尾又回到段首，第几次加一",
            again == step_at("确认", 1, loop=(1, 3, 2)), again)
    c.check("「第几次」：段尾被前置条件跳过、上一步停在段中时，回到段首也加一"
            "（4.10 节字面判据只说「上一步是段尾」，这里按 2026-09-16 主会话裁定放宽成「序号不比上一步大」）",
            skipped == step_at("确认", 1, loop=(1, 3, 2)), skipped)

    # 不合法：内层必须属于外层，不合法按任务定义错误处理，不再降级成给使用者看的告知异常。
    bad = [
        ("阶段不存在", {"阶段": "没有这个阶段"}),
        ("步骤越界", step_at("确认", 9, loop=(1, 3, 1))),
        ("循环段对不上", step_at("确认", 2, loop=(1, 2, 1))),
        ("第几次不是正整数", step_at("确认", 2, loop=(1, 3, 0))),
        ("在循环段里却没有循环这一层", step_at("确认", 2)),
        ("不在循环段里却写了循环", step_at("写释义草稿", 2, loop=(1, 3, 1))),
        ("有循环却没有步骤", {"阶段": "确认", "循环": {"起": 1, "止": 3, "第几次": 1}}),
    ]
    for name, value in bad:
        try:
            glossary.select_action({}, value)
            raised = None
        except DefinitionError as error:
            raised = error.reason
        c.check(f"不合法的当前步（{name}）抛任务定义错误，原因是一句能读的话", bool(raised), raised)

    c.check("当前步合法时行动选择照常返回候选（不合法判据没有误伤正常值）",
            glossary.select_action({**glossary.SLOTS, "术语": "基线"}, step_at("写释义草稿"))[0] == DRAFT_TOOL,
            glossary.select_action({**glossary.SLOTS, "术语": "基线"}, step_at("写释义草稿")))

    # 显示用的两个接口。
    texts = [glossary.step_text(step_at("写释义草稿")),
             glossary.step_text(step_at("写释义草稿", 2)),
             glossary.step_text(step_at("确认", 2, loop=(1, 3, 2)))]
    c.check("step_text 三种形状：阶段起点、不在循环段里、在循环段里（写明起止、第几次与上限）",
            texts == ["当前步：『写释义草稿』阶段，还没有做完任何一步",
                      "当前步：『写释义草稿』阶段，做完了第 2 步『根据术语（与原文片段，若有）生成释义草稿』",
                      "当前步：『确认』阶段，第 1 到第 3 步循环的第 2 次（最多 5 次），做完了第 2 步『判读回复：确认还是修改』"],
            texts)
    view = glossary.step_view(step_at("确认", 2, loop=(1, 3, 2)))
    c.check("step_view 给出阶段名、阶段内序号、这一步的说明、循环起止与第几次与上限、是不是段尾，页面不用自己算",
            view == {"阶段": "确认", "步骤": 2, "说明": "判读回复：确认还是修改",
                     "循环": {"起": 1, "止": 3, "第几次": 2, "最多": 5}, "是段尾": False}, view)
    c.check("step_text 读不懂时照实说，不抛错（旧运行文件或宿主给了别的东西）",
            glossary.step_text({"阶段": "没有这个阶段"}).startswith("当前步：读不出来")
            and glossary.step_view("不是字典") is None, glossary.step_text({"阶段": "没有这个阶段"}))
    c.check("阶段名这个键就叫「阶段」（当前步的第一层，与告知异常参数里的写法一致）", STEP_STAGE == "阶段")
    return c


# ───────────────────────── 场景：术语澄清 ─────────────────────────

GLOSSARY_TOOLS = ("ask", DRAFT_TOOL, JUDGE_TOOL)
GLOSSARY_FILE = "glossary.json"
# 录制文件：模式「回放」时模型的回答从这里来。路径以 tod_kernel 包目录为基准。
GLOSSARY_RECORDING = "task_defs/recordings/glossary.json"

# 两个场景的定稿文本（2026-09-16）。它们与提示词一起决定请求哈希：改一个字，录制文件就全部失效要重录。
GLOSSARY_TERM_ONE = "基线"
GLOSSARY_SOURCE = ("每一轮评审通过后，把当时的全部条目连同它们的版本号一并冻结下来，"
                   "形成一份此后只能经变更流程修改的参照物；后续的改动都以它为对照。")
GLOSSARY_TERM_TWO = "需求确认"
# 使用者看过草稿后的两种回答：一句提修改意见，一句确认。它们是回答，不进请求，但会经对话历史进下一次调用。
GLOSSARY_REVISE_REPLY = "太长了，压成两句，并且要说明它的产出是一份签字确认的需求清单。"
GLOSSARY_CONFIRM_REPLY = "可以，就这样。"

# 答案表的值是一串时按次序一轮一句：场景二第 1 轮提意见、第 2 轮确认。
GLOSSARY_ANSWERS_ONE = {("回复", ()): [GLOSSARY_CONFIRM_REPLY]}
GLOSSARY_ANSWERS_TWO = {("回复", ()): [GLOSSARY_REVISE_REPLY, GLOSSARY_CONFIRM_REPLY]}
GLOSSARY_INPUT_ONE = {"术语": GLOSSARY_TERM_ONE, "原文片段": GLOSSARY_SOURCE}
GLOSSARY_INPUT_TWO = {"术语": GLOSSARY_TERM_TWO}


def glossary_def(initial=None):
    return taskdef.load(TASK_DEFS_DIR / GLOSSARY_FILE, initial=initial)


def glossary_call(recording=GLOSSARY_RECORDING, mode=llm.MODE_REPLAY):
    """术语澄清场景用的模型调用件，以及它读的那份配置。

    模式与录制文件由场景定死（一律回放，回答来自仓内的录制文件），所以验证脚本在没有模型服务的机器上照样全过；
    模型名、超时这些照读配置文件，不在代码里写死。录制是用「录制」模式对着真模型服务跑出来的，见实施报告。
    """
    config = {**llm.load_config(), "mode": mode}
    return llm.make_caller(config, recording_path=recording), config


def recorded_answers(recording=GLOSSARY_RECORDING) -> list:
    """录制文件里的回答原文，按录的顺序。断言拿它与槽位值比，不把模型写的话抄进脚本。"""
    path = llm.recording_path_of({"recording_path": recording})
    return [entry.get("response") for entry in llm.read_recording(path)]


def glossary_common(c: Checker, run: Run, loops: int, closed_before_failure_of=None) -> None:
    common_checks(c, run, loops=loops, closed_before_failure_of=closed_before_failure_of)


def confirm_utterance(term: str, draft: str) -> str:
    """念草稿那一问的预期句：模板在定义文件里，草稿是模型现写的，所以由场景现拼，不进常量表。"""
    return f"对术语「{term}」的释义草稿是：{draft} 请确认，或提出修改意见。"


def model_record(run: Run, action_id: int) -> dict:
    """某条行动的返回值（模型工具的返回值就是调用记录）。行动不存在时返回空字典，让断言判失败而不是抛异常。"""
    action = run.task.actions.get(action_id) if run.task else None
    record = action.result if action is not None else None
    return record if isinstance(record, dict) else {}


def check_model_record(c: Checker, run: Run, action_id: int, config: dict, shape: str, segments: list) -> None:
    """一条在工具里调模型的行动，它的返回值是完整的调用记录。"""
    record = model_record(run, action_id)
    c.check(f"行动 {action_id} 的返回值是模型调用记录：模式「{llm.MODE_REPLAY}」、模型名取自配置、"
            f"输出形状「{shape}」，另有系统提示哈希、用户内容、返回原文、请求哈希与耗时",
            record.get("mode") == llm.MODE_REPLAY and record.get("model") == config["model"]
            and record.get("shape") == shape and len(record.get("system_prompt_hash") or "") == 64
            and isinstance(record.get("user_content"), str) and isinstance(record.get("response"), str)
            and len(record.get("request_hash") or "") == 64 and isinstance(record.get("elapsed_ms"), int),
            {key: record.get(key) for key in ("mode", "model", "shape", "elapsed_ms")})
    actual = [segment["type"] for segment in record.get("segments") or []]
    c.check(f"行动 {action_id} 的段列表按固定顺序装了：{'、'.join(segments)}", actual == segments, actual)
    body = {segment["type"]: segment for segment in record.get("segments") or []}
    c.check(f"行动 {action_id} 的每段都带类型、来源与正文，正文是逐字原文（不截断）",
            all(segment.get("source") and isinstance(segment.get("text"), str)
                for segment in record.get("segments") or []), body.keys())


def segment_text(run: Run, action_id: int, seg_type: str, source_part: str = "") -> str:
    """取某条调用记录里某一段的正文；source_part 用来在同类型多段里挑（例如当前数据段有好几段）。"""
    for segment in model_record(run, action_id).get("segments") or []:
        if segment["type"] == seg_type and source_part in segment["source"]:
            return segment["text"]
    return ""


def segment_source(run: Run, action_id: int, seg_type: str, source_part: str = "") -> str:
    for segment in model_record(run, action_id).get("segments") or []:
        if segment["type"] == seg_type and source_part in segment["source"]:
            return segment["source"]
    return ""


def glossary_scenario_one() -> Checker:
    """场景：术语澄清，给全初始输入，一次确认。证明模型在工具里被调用、JSON 判读、问术语那步被前置条件跳过。"""
    title = "术语澄清：给全初始输入，一次确认"
    banner(title)
    call, config = glossary_call()
    run = run_scenario("T-glossary-1", glossary_def(dict(GLOSSARY_INPUT_ONE)), GLOSSARY_ANSWERS_ONE,
                       GLOSSARY_TOOLS, call=call)
    c = Checker(title)
    print("── 断言 ──")
    check_kernel_is_task_agnostic(c)
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    task = run.task
    proposed = named(run.events, ACTION_PROPOSED)
    actual = [(e.payload["tool"], e.payload["basis"][0]) for e in proposed]
    c.check("三个行动：生成术语释义（第 2 步）、念草稿问回复（第 3 步）、判读回复（第 4 步）；"
            "问术语那一步因写入目标已有值被跳过",
            actual == [(DRAFT_TOOL, 2), ("ask", 3), (JUDGE_TOOL, 4)], actual)
    if len(proposed) < 3:  # 行动没跑齐（例如录制缺失），后面的断言无从谈起
        return c
    skip_reason = "前置条件不成立：写入目标指向的位置为 None"
    check_selection_trail(c, run, {1: {"前进": [], "跳过阶段": [], "跳过步骤": [[1, skip_reason]]}})

    check_model_record(c, run, 1, config, "文本", ["任务进度", "对话历史", "当前数据", "参考材料", "本步", "输出形状"])
    c.check("写首稿时参考材料段装的是初始输入给的原文片段，不是「（未提供）」",
            segment_text(run, 1, "参考材料") == GLOSSARY_SOURCE, segment_text(run, 1, "参考材料"))
    c.check("写首稿时对话历史是空的（这个阶段还没问过话）", segment_text(run, 1, "对话历史") == "（无）",
            segment_text(run, 1, "对话历史"))
    draft = model_record(run, 1).get("response", "").strip()
    c.check("释义草稿等于模型返回原文去掉首尾空白", task.data.get("释义草稿") == draft, task.data.get("释义草稿"))
    check_waiting_then_success(c, run, 2, expected_utterance=confirm_utterance(GLOSSARY_TERM_ONE, draft))

    check_model_record(c, run, 3, config, "JSON", ["任务进度", "对话历史", "当前数据", "本步", "输出形状"])
    parsed = model_record(run, 3).get("parsed") or {}
    c.check("判读那条的解析结果是 {\"决定\": \"确认\", …}，确认文本非空",
            parsed.get("决定") == "确认" and isinstance(parsed.get("确认文本"), str) and parsed["确认文本"].strip(), parsed)
    c.check("确认释义等于解析出的确认文本，「回复」判读后被清空",
            task.data.get("确认释义") == (parsed.get("确认文本") or "").strip() and task.data.get("回复") is None,
            (task.data.get("确认释义"), task.data.get("回复")))
    c.check("判读那条的记录写明每个键写到了哪个槽位：确认文本 → 确认释义",
            model_record(run, 3).get("writes") == {"确认文本": "确认释义"}, model_record(run, 3).get("writes"))
    c.check("交付物只有一项，名字「术语释义」，来源「确认释义」，形态文本",
            [(d["名字"], d["来源"], d["形态"]) for d in run.task_def.DEFINITION.get("交付物", [])]
            == [("术语释义", "确认释义", "文本")], run.task_def.DEFINITION.get("交付物"))
    c.check("任务状态是已完成，术语与原文片段仍是初始输入给的那两段",
            task.status == TaskStatus.DONE and task.data.get("术语") == GLOSSARY_TERM_ONE
            and task.data.get("原文片段") == GLOSSARY_SOURCE, task.status)
    check_other_tool_actions(c, run)
    check_explainable(c, run)
    check_step_final(c, run, step_at("确认", 2, loop=(1, 3, 1)))  # 第 1 次循环里判读即确认，这一次没走到第 3 步
    glossary_common(c, run, loops=3)
    return c


def glossary_scenario_two() -> Checker:
    """场景：术语澄清，只给术语，使用者先提一次修改意见再确认。修改循环、上下文包三件事都在这个场景里。"""
    title = "术语澄清：只给术语，一次修改后确认"
    banner(title)
    call, config = glossary_call()
    run = run_scenario("T-glossary-2", glossary_def(dict(GLOSSARY_INPUT_TWO)), GLOSSARY_ANSWERS_TWO,
                       GLOSSARY_TOOLS, call=call)
    c = Checker(title)
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    task = run.task
    proposed = named(run.events, ACTION_PROPOSED)
    actual = [(e.payload["tool"], e.payload["basis"][0]) for e in proposed]
    c.check("六个行动：写首稿、念草稿、判为修改、按意见改稿、再念草稿、判为确认；"
            "依据序号依次是 2、3、4、5、3、4（第二轮回到组首的第 3 步）",
            actual == [(DRAFT_TOOL, 2), ("ask", 3), (JUDGE_TOOL, 4), (DRAFT_TOOL, 5), ("ask", 3), (JUDGE_TOOL, 4)], actual)
    if len(proposed) < 6:
        return c
    c.check("系统自始至终没有问过原文片段：三次提问分别是问回复两次（念草稿），没有问原文片段那一句",
            all("原文片段" not in e.payload["content"]["utterance"]
                for e in run.events if e.name == MESSAGE_PUT and e.payload["box"] == OUTBOX), None)

    first_draft = model_record(run, 1).get("response", "").strip()
    c.check("写首稿时没有原文片段，参考材料段写「（未提供，按通用含义解释）」",
            segment_text(run, 1, "参考材料") == "（未提供，按通用含义解释）", segment_text(run, 1, "参考材料"))
    parsed_revise = model_record(run, 3).get("parsed") or {}
    c.check("第 3 次迭代判为修改：解析结果的决定是「修改」、修改意见非空，确认文本是 null",
            parsed_revise.get("决定") == "修改" and parsed_revise.get("确认文本") is None
            and isinstance(parsed_revise.get("修改意见"), str) and parsed_revise["修改意见"].strip(), parsed_revise)
    def written(slot):  # 只看行动写的，初始化那条（来源「初始化」、不挂行动编号）不算
        return [e.payload["new"] for e in named(run.events, DATA_CHANGED)
                if e.payload["slot"] == slot and e.action_id is not None]

    feedback_written, reply_written = written("修改意见"), written("回复")
    c.check("修改意见写了一次又被清空；回复写了两次、每次判读后都清空",
            feedback_written == [parsed_revise.get("修改意见", "").strip(), None]
            and reply_written == [GLOSSARY_REVISE_REPLY, None, GLOSSARY_CONFIRM_REPLY, None],
            (feedback_written, reply_written))

    check_model_record(c, run, 4, config, "文本",
                       ["任务进度", "对话历史", "当前数据", "参考材料", "本步", "输出形状"])
    c.check("改稿那次的任务进度段写明这段循环的第 1 次与上限 5、做完的是哪一步，并说明这次写第 2 稿",
            segment_text(run, 4, "任务进度").startswith(
                "当前步：『确认』阶段，第 1 到第 3 步循环的第 1 次（最多 5 次），做完了第 2 步『判读回复：确认还是修改』")
            and "第 1 稿被要求修改，本次写第 2 稿" in segment_text(run, 4, "任务进度"),
            segment_text(run, 4, "任务进度"))
    c.check("改稿那次的对话历史只装 1 轮，装的正是那一轮回答已被判读清空的问答",
            "1 轮装入（回答已被清空的 1 轮）" in segment_source(run, 4, "对话历史")
            and segment_text(run, 4, "对话历史").count("系统：") == 1
            and GLOSSARY_REVISE_REPLY in segment_text(run, 4, "对话历史"),
            segment_source(run, 4, "对话历史"))
    data_lines = segment_text(run, 4, "当前数据").splitlines()
    c.check("改稿那次的当前数据是一段：术语、上一稿、修改意见各一行，修订记录是段内最后一项（第 1 稿一行、使用者意见一行，续行缩进两格）",
            data_lines == [f"术语：{GLOSSARY_TERM_TWO}",
                           f"释义草稿（上一稿）：{first_draft}",
                           f"修改意见：{parsed_revise.get('修改意见', '').strip()}",
                           f"修订记录（系统从变更事件推出）：第 1 稿：{first_draft}",
                           f"  使用者意见：{parsed_revise.get('修改意见', '').strip()}"],
            data_lines)
    second_draft = model_record(run, 4).get("response", "").strip()
    c.check("改稿写出第 2 稿，与第 1 稿不同，写完把修改意见清空",
            task.data.get("释义草稿") == second_draft and second_draft != first_draft
            and task.data.get("修改意见") is None, (second_draft[:40], task.data.get("修改意见")))
    check_waiting_then_success(c, run, 2, expected_utterance=confirm_utterance(GLOSSARY_TERM_TWO, first_draft))
    check_waiting_then_success(c, run, 5, expected_utterance=confirm_utterance(GLOSSARY_TERM_TWO, second_draft))

    parsed_confirm = model_record(run, 6).get("parsed") or {}
    c.check("第 6 次迭代判为确认，确认释义写入，组结束，任务完成",
            parsed_confirm.get("决定") == "确认"
            and task.data.get("确认释义") == (parsed_confirm.get("确认文本") or "").strip()
            and task.status == TaskStatus.DONE, (parsed_confirm.get("决定"), task.status))
    check_other_tool_actions(c, run)
    check_explainable(c, run)
    check_step_final(c, run, step_at("确认", 2, loop=(1, 3, 2)))
    # 当前步逐次核对（第四步第 5 节第十项的验收点）：术语由初始输入给全，所以第 1 步的 ask 被跳过，
    # 第 1 次迭代做的是「写释义草稿」阶段第 2 步；随后三次迭代是「确认」阶段这段循环的第 1 次的三步；
    # 第 5 次迭代回到段首，第几次加一；第 6 次迭代判为确认，任务完成。
    c.check("当前步逐次记下走到哪：初始化一条，六次迭代各一条，第 4 次之后是这段循环第 1 次的第 3 步，第 6 次之后是第 2 次的第 2 步",
            step_sequence(run) == [
                ("初始化", step_at("写释义草稿")),
                (1, step_at("写释义草稿", 2)),
                (2, step_at("确认", 1, loop=(1, 3, 1))),
                (3, step_at("确认", 2, loop=(1, 3, 1))),
                (4, step_at("确认", 3, loop=(1, 3, 1))),
                (5, step_at("确认", 1, loop=(1, 3, 2))),
                (6, step_at("确认", 2, loop=(1, 3, 2)))], step_sequence(run))
    glossary_common(c, run, loops=6)
    return c


def glossary_scenario_three() -> Checker:
    """场景：术语澄清，模型不可达。回放模式对着一份空录制，第一个行动就失败，任务以内核错误结束。"""
    import json as json_module
    import shutil
    import tempfile

    title = "术语澄清：模型不可达（录制文件里没有这条请求）"
    banner(title)
    work = Path(tempfile.mkdtemp(prefix="tod-glossary-empty-"))
    try:
        empty = work / "empty.json"
        empty.write_text(json_module.dumps([], ensure_ascii=False) + "\n", encoding="utf-8")
        call, config = glossary_call(recording=str(empty))
        run = run_scenario("T-glossary-3", glossary_def(dict(GLOSSARY_INPUT_ONE)), GLOSSARY_ANSWERS_ONE,
                           GLOSSARY_TOOLS, call=call)
        c = Checker(title)
        print("── 断言 ──")
        proposed = named(run.events, ACTION_PROPOSED)
        c.check("恰好一个行动，就是生成术语释义", [e.payload["tool"] for e in proposed] == [DRAFT_TOOL],
                [e.payload["tool"] for e in proposed])
        note = f"录制文件里没有这条请求：{empty}"
        history = status_values(action_history(run.events, 1))
        c.check("行动 1 的状态经过是 已提出、已获准、已失败（没有等待中：它不问使用者）",
                history == [ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.FAILED],
                [s.value for s in history])
        failed = [e for e in action_history(run.events, 1) if e.payload["new_status"] == ActionStatus.FAILED]
        c.check(f"行动 1 已失败的说明是模型调用那一句错误：「{note}」",
                failed and failed[0].payload["note"] == note, failed[0].payload["note"] if failed else None)
        c.check("释义草稿仍然为空：模型没写出东西就不写槽位",
                run.task is not None and run.task.data.get("释义草稿") is None,
                run.task.data.get("释义草稿") if run.task else None)
        check_terminated(c, run, 1, note)
        glossary_common(c, run, loops=1)
        return c
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ───────────────────────── 检查组：上下文包 ─────────────────────────
# 不跑任务，手写一份任务数据与事件列表，把六种段与对话历史的三条规则逐条验出来。

CTX_TERM = "基线"
CTX_DRAFT_ONE = "第一稿：基线是冻结下来的条目集合。"
CTX_DRAFT_TWO = "第二稿：基线是某一轮评审通过时冻结的条目集合。"
CTX_FEEDBACK = "说清楚它是什么时候冻结的。"
CTX_REPLY = "再具体些。"
CTX_REPLY_TWO = "还要说明它的作用。"


def fake_event(seq, name, payload, action_id=None, kind=STATE, source=SOURCE_LOOP):
    return kernel.Event(seq=seq, ts=0.0, task_id="T-context", action_id=action_id,
                        kind=kind, source=source, name=name, payload=payload)


def ask_events(seq, action_id, stage, slot, question, answer) -> list:
    """一问一答在事件流里的样子：行动提出（带依据说明，阶段名从这里来）、发件箱的问题、收件箱的回答。"""
    note = f"{stage}{taskdef.STAGE_NAME_SEPARATOR}第 1 步 问一句"
    return [
        fake_event(seq, ACTION_PROPOSED, {"tool": "ask", "params": {"target": {"slot": slot, "path": []}},
                                          "proposer": "selector", "basis": [1, note, {}]}, action_id),
        fake_event(seq + 1, MESSAGE_PUT, {"box": OUTBOX, "kind": "question", "sender": "tool.ask", "recipient": "user",
                                          "action_id": action_id, "in_reply_to": None,
                                          "content": {"utterance": question, "params": {"target": {"slot": slot, "path": []}}},
                                          "seq": action_id}, action_id),
        fake_event(seq + 2, MESSAGE_PUT, {"box": INBOX, "kind": "answer", "sender": "user", "recipient": action_id,
                                          "action_id": action_id, "in_reply_to": action_id,
                                          "content": answer, "seq": action_id}, action_id),
    ]


def context_pack_checks() -> Checker:
    """第四步验证目标三的一部分：上下文包按六种段组装，对话历史按三条确定性规则装入。

    手写数据与事件，不跑任务：这一组要验的是规则本身，跑任务反而把规则埋进流程里看不清。
    """
    from tod_kernel.context import (EMPTY_MARK, MISSING_MARK, SEG_DIALOGUE, SEG_MATERIAL, SEG_ORDER,
                                    SEG_PROGRESS, SEG_STEP, SPEAKER_SYSTEM, ContextPack, render, revision_log)

    title = "第四步：上下文包与对话历史的三条规则"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    task_def = glossary_def(dict(GLOSSARY_INPUT_ONE))
    data = {**task_def.SLOTS, "术语": CTX_TERM, "原文片段": None, "释义草稿": CTX_DRAFT_TWO,
            "回复": None, "修改意见": CTX_FEEDBACK, "确认释义": None}
    # 当前步不在任务数据里，单独给：『确认』阶段第 1 到第 3 步这段循环的第 2 次，做完了第 2 步「判读回复」。
    ctx_step = step_at("确认", 2, loop=(1, 3, 2))
    # 三轮问答：第一轮在「写释义草稿」阶段问术语（回答至今原样留在槽位里），
    # 后两轮在「确认」阶段问回复（回答都已被判读清空），这样三条规则各有例子可验。
    events = (ask_events(1, 1, "写释义草稿", "术语", "请给出要澄清的术语。", CTX_TERM)
              + [fake_event(4, DATA_CHANGED, {"slot": "释义草稿", "old": None, "new": CTX_DRAFT_ONE, "source": 2}, 2)]
              + ask_events(5, 3, "确认", "回复", f"草稿是：{CTX_DRAFT_ONE} 请确认", CTX_REPLY)
              + [fake_event(8, DATA_CHANGED, {"slot": "修改意见", "old": None, "new": CTX_FEEDBACK, "source": 4}, 4),
                 fake_event(9, DATA_CHANGED, {"slot": "回复", "old": CTX_REPLY, "new": None, "source": 4}, 4),
                 fake_event(10, DATA_CHANGED, {"slot": "释义草稿", "old": CTX_DRAFT_ONE, "new": CTX_DRAFT_TWO, "source": 5}, 5)]
              + ask_events(11, 6, "确认", "回复", f"草稿是：{CTX_DRAFT_TWO} 请确认", CTX_REPLY_TWO)
              + [fake_event(14, DATA_CHANGED, {"slot": "回复", "old": CTX_REPLY_TWO, "new": None, "source": 7}, 7)])
    pack = ContextPack.build(task_def, data, events, step=ctx_step)

    segments = [pack.progress("本次写第 3 稿。"), pack.dialogue(), pack.data_segment(["术语"]),
                pack.material("原文片段"), ContextPack.step(DRAFT_TOOL, "改写。"),
                ContextPack.shape("文本", "只输出释义正文。")]
    c.check("六种段按固定顺序：任务进度、对话历史、当前数据、参考材料、本步、输出形状",
            [segment.type for segment in segments] == list(SEG_ORDER), [segment.type for segment in segments])
    text = render(segments)
    heads = re.findall(r"^【(.+?) · (.+?)】$", text, flags=re.MULTILINE)
    c.check("每段以【段名 · 来源】开头，六段六个段头，段名与段类型逐一对应",
            [head[0] for head in heads] == list(SEG_ORDER) and all(head[1] for head in heads), heads)

    progress = pack.progress()
    # 第四步 4.10 节：这句话由任务定义的 step_text 生成，上下文包不自己算第几步、第几次。
    # 第三步的写法把整任务的步骤编号加一当成了阶段内序号（「确认」阶段只有三步却写成「第 5 步」），那条错误的预期跟着改。
    c.check("任务进度段用任务定义给的那句当前步，阶段、循环起止、第几次、上限、做完的是哪一步都在里面，来源是当前步",
            progress.type == SEG_PROGRESS and progress.source == "当前步"
            and progress.text.startswith("当前步：『确认』阶段，第 1 到第 3 步循环的第 2 次（最多 5 次），"
                                         "做完了第 2 步『判读回复：确认还是修改』"), progress.text)

    dialogue = pack.dialogue()
    c.check("对话历史第一条规则（按范围取）：默认只装当前阶段『确认』的两轮问答，"
            "「写释义草稿」阶段问术语那一轮不在范围内",
            dialogue.text.count(SPEAKER_SYSTEM) == 2 and CTX_REPLY in dialogue.text
            and CTX_REPLY_TWO in dialogue.text and "请给出要澄清的术语。" not in dialogue.text, dialogue.text)
    c.check("对话历史的来源写明范围、装了几轮、预算与用量",
            dialogue.type == SEG_DIALOGUE and "范围：当前阶段" in dialogue.source
            and "2 轮装入" in dialogue.source and "预算 800 字" in dialogue.source, dialogue.source)

    whole = ContextPack.build(task_def, data, events, step=ctx_step).dialogue(scope="task")
    c.check("对话历史第二条规则（已在数据里的不重复装）：整任务范围下有三轮，问术语那一轮的回答仍原样留在槽位「术语」里，"
            "不重复装；回答已被清空的两轮装",
            whole.text.count(SPEAKER_SYSTEM) == 2 and "请给出要澄清的术语" not in whole.text
            and "回答已被清空的 2 轮" in whole.source, (whole.source, whole.text))

    tight = ContextPack.build(task_def, data, events, budget_chars=10, step=ctx_step).dialogue(scope="task")
    c.check("对话历史第三条规则（预算封顶）：预算调到 10 字时，两轮装不下，从最早那一轮整轮裁起，"
            "至少留最近一轮，省略处标出【更早 1 轮已省略】",
            tight.text.startswith("【更早 1 轮已省略】") and tight.text.count(SPEAKER_SYSTEM) == 1
            and CTX_REPLY_TWO in tight.text, tight.text[:120])

    revisions = revision_log(events, "释义草稿", "修改意见")
    c.check("修订记录从数据变更事件推出：写了两稿、提了一条意见，就是「第 1 稿」「使用者意见」「第 2 稿」三行",
            revisions.splitlines() == [f"第 1 稿：{CTX_DRAFT_ONE}", f"使用者意见：{CTX_FEEDBACK}",
                                       f"第 2 稿：{CTX_DRAFT_TWO}"], revisions.splitlines())

    empty_pack = ContextPack.build(task_def, {**data, "修改意见": None, "原文片段": None}, [], step=ctx_step)
    c.check("系统标记：槽位为空那一行写「（空）」，参考材料没有写「（未提供）」",
            empty_pack.data_segment(["修改意见"]).text == f"修改意见：{EMPTY_MARK}"
            and empty_pack.material("原文片段").text == MISSING_MARK,
            (empty_pack.data_segment(["修改意见"]).text, empty_pack.material("原文片段").text))
    # 当前数据合成一段（2026-09-17 用户裁定）：段头写「当前数据 · 槽位」，段内一行一个槽位，修订记录是最后一项。
    merged = pack.data_segment(["术语", ("释义草稿", "（上一稿）")], revision=("释义草稿", "修改意见"))
    c.check("当前数据是一段：来源写「槽位」，段内一行一个槽位「名：值」，角色标注跟在名字后面，修订记录是最后一项",
            merged.source == "槽位"
            and merged.text.splitlines()[:2] == [f"术语：{CTX_TERM}", f"释义草稿（上一稿）：{CTX_DRAFT_TWO}"]
            and merged.text.splitlines()[2].startswith("修订记录（系统从变更事件推出）：第 1 稿：")
            and merged.text.splitlines()[3].startswith("  使用者意见："),
            merged.text.splitlines())
    c.check("参考材料段的来源写明是哪个槽位；本步段指向工具，输出形状段写形状",
            pack.material("原文片段").type == SEG_MATERIAL
            and ContextPack.step(JUDGE_TOOL).type == SEG_STEP
            and ContextPack.shape("JSON", "{}", api_note=True).source.startswith("JSON（同时作为接口参数"),
            ContextPack.shape("JSON", "{}", api_note=True).source)

    prompt = tools_system_prompt_for(task_def, list(GLOSSARY_TOOLS) + [EXCEPTION_TOOL])
    heads = re.findall(r"^【(.+?)】$", prompt, flags=re.MULTILINE)
    c.check("系统提示六段齐全，顺序是角色与任务、任务定义摘要、工具目录、上下文约定、领域规矩、通用输出规矩",
            heads == ["角色与任务", "任务定义摘要", "工具目录", "上下文约定", "领域规矩", "通用输出规矩"], heads)
    c.check("系统提示里有两个模型工具的固定指令，也有任务定义的领域规矩",
            "你是术语解释员" in prompt and "明确同意、或给出一段改写后的完整释义算确认" in prompt
            and "释义按需求工程语境写" in prompt, None)

    import shutil
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="tod-context-"))
    try:
        recording = work / "r.json"
        request_one = llm.Request(system=prompt, user="用户内容一", shape="文本")
        request_two = llm.Request(system=prompt, user="用户内容二", shape="文本")
        llm.save_to_recording(recording, request_one, "回答一")
        llm.save_to_recording(recording, request_two, "回答二")
        call = llm.make_caller({**llm.load_config(), "mode": llm.MODE_REPLAY}, recording_path=str(recording))
        first, second = call(request_one), call(request_two)
        c.check("模型调用记录：第一次调用记下系统提示全文，第二次只写「同任务系统提示」，两次的系统提示哈希相同",
                first.record["system_prompt"] == prompt
                and second.record["system_prompt"] == llm.SAME_SYSTEM_PROMPT
                and first.record["system_prompt_hash"] == second.record["system_prompt_hash"] == llm.text_hash(prompt),
                (second.record["system_prompt"], first.record["system_prompt_hash"][:12]))
        c.check("模型调用记录还带着用户内容、输出形状、返回原文、请求哈希与耗时",
                {"user_content", "shape", "response", "request_hash", "elapsed_ms"} <= set(first.record)
                and first.record["response"] == "回答一", sorted(first.record))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c


# ───────────────────────── 检查组：模型调用件的三种模式 ─────────────────────────

FAKE_SERVICE_TEXT = "  这是假服务回的固定文字。  "  # 首尾留空白，用来看「去掉首尾空白」是在工具里做的


def fake_service():
    """起一个只回固定文字的本地小服务，替真模型服务受一次请求；返回（服务对象, 端口, 线程）。"""
    import http.server
    import json as json_module

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json_module.dumps({"choices": [{"message": {"content": FAKE_SERVICE_TEXT}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 不往标准错误刷访问日志
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="fake-llm-service", daemon=True)
    thread.start()
    return server, server.server_address[1], thread


def closed_port() -> int:
    """找一个此刻没人监听的端口：绑上再放开，拿它的号。运行模式那条检查用它当打不开的地址。"""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def llm_mode_checks() -> Checker:
    """第四步验证目标二：模型调用件三种模式各走各的路，不联网也能验。

    三个检查都不碰真的模型服务：回放读临时录制文件，录制对着本机起的假服务，运行故意指向一个打不开的端口。
    """
    import shutil
    import tempfile

    title = "第四步：模型调用件的三种模式"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    work = Path(tempfile.mkdtemp(prefix="tod-llm-modes-"))
    try:
        recorded = llm.Request(system="你只回一句话。", user="这条请求已经录过。", shape=llm.SHAPE_TEXT)
        fresh = llm.Request(system="你只回一句话。", user="这条请求没有录过。", shape=llm.SHAPE_TEXT)
        recorded_text = "这是录制文件里的回答原文。"
        recording = work / "recording.json"
        llm.save_to_recording(recording, recorded, recorded_text)
        entries = llm.read_recording(recording)
        c.check("录制文件是一个列表，每项有请求哈希、请求原文（系统提示、用户内容、输出形状）与回答原文四样",
                len(entries) == 1 and set(entries[0]) == {"request_hash", "request", "response"}
                and set(entries[0]["request"]) == {"system", "user", "shape"}, entries)

        base = {"mode": llm.MODE_REPLAY, "base_url": "http://127.0.0.1:1/v1", "model": "假模型",
                "timeout_s": 5, "recording_path": str(recording), "print_calls": False}
        reply = llm.make_caller(base)(recorded)
        c.check("回放模式：请求哈希命中时返回录制的回答原文，记录里模式是「回放」、请求哈希是这条请求的",
                reply.text == recorded_text and reply.record["mode"] == llm.MODE_REPLAY
                and reply.record["request_hash"] == llm.request_hash(recorded), reply)
        error = None
        try:
            llm.make_caller(base)(fresh)
        except llm.LLMError as exc:
            error = exc
        c.check("回放模式：请求哈希不命中时抛出模型调用错误，一句话说明是「录制文件里没有这条请求」，"
                "错误全文附上请求原文，好让人照着补录或改提示词",
                error is not None and "录制文件里没有这条请求" in error.brief
                and fresh.user in str(error) and fresh.system in str(error), repr(error))

        server, port, thread = fake_service()
        try:
            second = work / "recording2.json"
            record_config = {**base, "mode": llm.MODE_RECORD, "base_url": f"http://127.0.0.1:{port}/v1",
                             "recording_path": str(second)}
            reply = llm.make_caller(record_config)(fresh)
            saved = llm.read_recording(second)
            c.check("录制模式：对着假服务调一次，回答是它回的固定文字，记录里模式是「录制」",
                    reply.text == FAKE_SERVICE_TEXT and reply.record["mode"] == llm.MODE_RECORD, reply)
            c.check("录制模式：录制文件里多出这一条，请求哈希、请求原文与回答原文都在",
                    len(saved) == 1 and saved[0]["request_hash"] == llm.request_hash(fresh)
                    and saved[0]["request"] == {"system": fresh.system, "user": fresh.user, "shape": fresh.shape}
                    and saved[0]["response"] == FAKE_SERVICE_TEXT, saved)
            replayed = llm.make_caller({**base, "recording_path": str(second)})(fresh)
            c.check("刚录下的这一条，换回放模式再调同一个请求就能命中，回答逐字相同",
                    replayed.text == FAKE_SERVICE_TEXT and replayed.record["mode"] == llm.MODE_REPLAY, replayed)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

        port = closed_port()
        url = f"http://127.0.0.1:{port}/v1"
        error = None
        try:
            llm.make_caller({**base, "mode": llm.MODE_RUN, "base_url": url})(fresh)
        except llm.LLMError as exc:
            error = exc
        c.check(f"运行模式：服务地址指向打不开的端口时，抛出的错误里带着那个地址 {url}/chat/completions",
                error is not None and "连不上模型服务" in error.brief and f"{url}/chat/completions" in error.brief,
                repr(error))

        error = None
        try:
            llm.make_caller(base)(llm.Request(system="x", user="y", shape="表格"))
        except llm.LLMError as exc:
            error = exc
        c.check("输出形状只认「文本」与「JSON」，给别的形状就报错",
                error is not None and "输出形状" in str(error), repr(error))

        c.check("入库的示例配置六项齐全，模式是「回放」，录制文件指向术语澄清那一份",
                llm.load_config(llm.PACKAGE_DIR / llm.EXAMPLE_CONFIG_NAME)["mode"] == llm.MODE_REPLAY
                and llm.load_config(llm.PACKAGE_DIR / llm.EXAMPLE_CONFIG_NAME)["recording_path"] == GLOSSARY_RECORDING,
                llm.load_config(llm.PACKAGE_DIR / llm.EXAMPLE_CONFIG_NAME))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c


# ───────────────────────── 检查组：控制台的问答打印 ─────────────────────────


def console_transcript_checks() -> Checker:
    """第四步验证目标四：控制台两种方式打印同样的问答，预设方式的断言一条不丢。

    截获标准输出跑材料接入登记正常场景，看问答两行交替出现；再经控制台的预设方式跑同一个场景，
    比对断言条数与直接跑验证脚本相同。
    """
    import contextlib
    import io

    from tod_kernel import console as console_module

    title = "第四步：控制台的问答打印"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        run_scenario("T-console-print", intake_def(), INTAKE_ANSWERS, INTAKE_TOOLS, console=False, show=True)
    marks = [line for line in buffer.getvalue().splitlines()
             if line.startswith(QUESTION_PREFIX) or line.startswith(PRESET_ANSWER_PREFIX)]
    c.check("材料接入登记正常场景打印出三行「系统：」与三行「使用者（预设）：」，两者交替出现",
            [QUESTION_PREFIX if line.startswith(QUESTION_PREFIX) else PRESET_ANSWER_PREFIX for line in marks]
            == [QUESTION_PREFIX, PRESET_ANSWER_PREFIX] * 3, marks)
    c.check("问的是三个文件纳不纳入，答的是 是、否、是",
            [line[len(PRESET_ANSWER_PREFIX):] for line in marks if line.startswith(PRESET_ANSWER_PREFIX)] == ["是", "否", "是"],
            marks)

    console_output = io.StringIO()
    with contextlib.redirect_stdout(console_output):
        via_console = console_module.run_preset(1)
    direct_output = io.StringIO()
    with contextlib.redirect_stdout(direct_output):
        direct = SCENARIOS[0][1]()
    c.check("经控制台跑 1 号场景与直接跑验证脚本，断言条数相同，而且两边都全过",
            len(via_console.results) == len(direct.results)
            and via_console.passed == len(via_console.results) == direct.passed,
            (len(via_console.results), via_console.passed, len(direct.results), direct.passed))
    text = console_output.getvalue()
    c.check(f"控制台跑完打印「断言：通过 {len(via_console.results)} 条」，且不逐条打印断言",
            f"断言：通过 {len(via_console.results)} 条" in text and "[通过]" not in text, text[-300:])
    c.check("控制台跑场景时同样打印一问一答",
            text.count(QUESTION_PREFIX) == 3 and text.count(PRESET_ANSWER_PREFIX) == 3, text[:300])
    return c


# 六个场景：（标题, 跑它的函数）。控制台按这张表列菜单，编号 1 起，0 是全部。
# 一种机制留一个场景（2026-09-16 用户裁定），顺序与第四步文档第 6 节的场景表相同。
SCENARIOS = [
    ("材料接入登记，正常（登记完再问）", intake_scenario_one),
    ("材料接入登记，目标写错报异常，使用者选被动终止", intake_exception_scenario),
    ("加载错误，三份坏文件", load_error_checks),
    ("术语澄清，给全初始输入，一次确认", glossary_scenario_one),
    ("术语澄清，只给术语，一次修改后确认", glossary_scenario_two),
    ("术语澄清，模型不可达", glossary_scenario_three),
]

# 不算场景的检查组：（标题, 跑它的函数）。控制台选 0 时与场景一起跑，范围与直接跑验证脚本相同。
CHECK_GROUPS = [
    ("观测台", observatory_checks),
    ("任务定义 JSON Schema", schema_checks),
    ("告知异常话的循环次数写法", utterance_round_clause_checks),
    ("当前步的记录、判据与显示", current_step_checks),
    ("模型调用件的三种模式", llm_mode_checks),
    ("上下文包与对话历史的三条规则", context_pack_checks),
    ("控制台的问答打印", console_transcript_checks),
]


def main() -> int:
    checkers = [scenario() for _, scenario in SCENARIOS] + [group() for _, group in CHECK_GROUPS]
    banner("汇总")
    all_ok = True
    for c in checkers:
        total = len(c.results)
        print(f"{c.title}：共 {total} 条断言，通过 {c.passed} 条，失败 {total - c.passed} 条")
        for description, ok in c.results:
            if not ok:
                print(f"  失败：{description}")
        all_ok = all_ok and c.passed == total
    print("结论：全部断言通过" if all_ok else "结论：有断言失败")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
