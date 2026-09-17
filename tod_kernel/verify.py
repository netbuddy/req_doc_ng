"""第零步验证脚本：在仓根下运行 `python -m tod_kernel.verify`。

脚本同时扮演宿主和使用者。
- 宿主：分配任务标识、建事件流、挂订阅者、建收件箱与发件箱、起线程跑 start_task、最后收线程。
- 使用者：主线程循环阻塞读发件箱，取到问题就按内容里的槽位查答案表，往收件箱放一条回答
  （回复对象填问题的到达序号，所属工具调用与收件人填问题的所属工具调用，发起方 user）；答案表里没有
  的槽位就关闭收件箱（发起方 host）。内核结束或出错时关闭发件箱，主线程取到空即停止读取。
  事件流只做观测，不参与应答。

全部断言都对内存收集器里的事件做。事件分状态、追踪两类：前五条验证目标的断言都先过滤掉
追踪事件，只对状态事件做；唯一例外是「序号连续」，序号由两类事件共用，所以对全部事件断言。
任一断言失败时脚本以非零状态退出。

仅供验证的手段：跑场景期间临时替换 kernel 模块上的 update_state、execute、select_call 与
register_call，在调用原函数前后抄下任务数据的实际值（select_call 另抄下下一个工具调用编号、工具调用表大小
与事件条数，用来确认调用选择不写任何东西；register_call 抄下返回后工具调用表的情况，用来确认登记即放入
工具调用表），与事件重放的结果比对；跑完即恢复。
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
from tod_kernel.tools import DRAFT_TOOL, EXCEPTION_OPTIONS, EXCEPTION_TOOL, UNDERSTAND_TOOL, build_table
from tod_kernel.tools import REASK_LINE, prompt_pack_of
from tod_kernel.tools import set_at as tools_set_at
from tod_kernel.kernel import (
    CALL_PROPOSED,
    CALL_STATUS_CHANGED,
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
    ToolCall,
    Change,
    CallStatus,
    StepChange,
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
    call_history,
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


# 控制台跑场景时把这几个开关拨一下：问答打印出来、事件流水与逐条断言都不打印、另挂一个显示工具调用的订阅者。
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
    execute_snapshots: dict = field(default_factory=dict)  # 工具调用编号 → 执行前实际数据
    select_snapshots: list = field(default_factory=list)  # 每次调用选择前后的（数据, 下一个工具调用编号, 工具调用表大小, 事件条数）
    register_snapshots: list = field(default_factory=list)  # 每次登记返回时的（返回的编号, 登记前表大小, 登记后表大小, 表里该工具调用的状态, 当时最后一个工具调用提出事件的编号）
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


def stack(main, inserts=None) -> dict:
    """当前步的地址栈（第五步 4.12 节）：主线地址加插入段列表（栈底在前）。"""
    return {"主线": main, "插入": list(inserts or [])}


def frame(mode, done, inputs, **extra) -> dict:
    """插入段的一帧：模式名、做完的步数、触发时带的输入；结果、再问次数这类键按需给。"""
    return {"模式": mode, "步骤": done, "输入": inputs, **extra}


def stacked(sequence) -> list:
    """把只写主线地址的当前步序列包成地址栈（插入为空），给不涉及插入段的场景比对用。"""
    return [(source, stack(main)) for source, main in sequence]


def step_sequence(run) -> list:
    """这次运行里当前步的变化序列：[(来源, 新值), …]，按事件序号。来源是「初始化」或工具调用编号。"""
    return [(e.payload["source"], e.payload["new"]) for e in named(run.events, STEP_CHANGED)]


def check_step_final(c, run, expected: dict) -> None:
    """当前步的终态值（任务对象上的字段，不在任务数据里）。"""
    actual = run.task.step if run.task is not None else None
    if not (isinstance(expected, dict) and "主线" in expected):
        expected = stack(expected)
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
                 exception_answers=None, call_model=None, answerer=None, show=None, subscribers=None,
                 confidence_floor=None) -> Run:
    """跑一个场景。

    exception_answers：第三步新增，使用者对「告知异常」依次给的回答；用完后再来告知异常就关闭收件箱。
    call_model：第四步新增，模型调用件（llm.make_caller 做出来的函数），只有用到「生成术语释义」的任务需要。
    answerer：应答者，默认按答案表答；控制台的自由输入方式换成从键盘取回答的那个。
    show：是否打印一问一答，默认随模块开关 SHOW_EXCHANGES。
    subscribers：另外挂的事件订阅者，默认随模块开关 EXTRA_SUBSCRIBERS。
    confidence_floor：第五步新增，对话理解的把握阈值；不给就用工具一侧的默认值 0.6。
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
    original_select = kernel.select_call
    original_register = kernel.register_call

    def update_probe(task, item):
        run.task = task
        before, n_before = dict(task.data), len(collector.events)
        original_update(task, item)
        run.update_snapshots.append((before, n_before, dict(task.data), len(collector.events)))

    def execute_probe(task, call):
        run.execute_snapshots[call.call_id] = dict(task.data)
        original_execute(task, call)

    def select_probe(task):
        before = (dict(task.data), task.next_call_id, len(task.calls), len(collector.events))
        candidate = original_select(task)
        run.select_snapshots.append((before, (dict(task.data), task.next_call_id, len(task.calls), len(collector.events))))
        return candidate

    def register_probe(task, candidate):
        size_before = len(task.calls)
        call_id = original_register(task, candidate)
        in_table = task.calls.get(call_id)
        proposed = [e for e in collector.events if e.name == CALL_PROPOSED]
        run.register_snapshots.append((
            call_id, size_before, len(task.calls),
            in_table.status if in_table is not None else None,
            proposed[-1].call_id if proposed else None,
        ))
        return call_id

    def kernel_main():
        try:
            run.task = kernel.start_task(task_id, task_def, table, inbox, outbox, stream)
        except BaseException as exc:  # 内核错误与意外异常都交给主线程断言
            run.error = exc
        finally:
            # 正常情况下内核已关闭发件箱，这里不会再发事件；只有内核因意外异常退出而没关时，
            # 由宿主代关，免得主线程永远阻塞在发件箱上。
            outbox.close("host")

    kernel.update_state, kernel.execute, kernel.select_call = update_probe, execute_probe, select_probe
    kernel.register_call = register_probe
    # 上下文包要读至今的事件（对话历史、修订记录）：宿主本来就持有内存收集器，把读它的函数交给工具表，内核不动。
    floor = {} if confidence_floor is None else {"confidence_floor": confidence_floor}
    table = build_table(task_def, tool_names, call_model=call_model, read_events=lambda: list(collector.events), **floor)
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
        kernel.update_state, kernel.execute, kernel.select_call = original_update, original_execute, original_select
        kernel.register_call = original_register
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


def of_call(events, name, call_id):
    return [e for e in events if e.name == name and e.call_id == call_id]


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
    for call in task.calls.values():
        expected += [(ch.slot, ch.old, ch.new, ch.source) for ch in call.changes]
    actual = [(e.payload["slot"], e.payload["old"], e.payload["new"], e.payload["source"]) for e in named(events, DATA_CHANGED)]
    c.check("目标五：初始化变更组与各工具调用变更组按序拼接，与全部「数据变更」事件的四个键逐位相等",
            expected == actual, f"对象上 {expected}；事件里 {actual}")

    for call in task.calls.values():
        history = call_history(events, call.call_id)
        c.check(f"目标五：工具调用 {call.call_id} 的当前状态等于它最后一个「工具调用状态变化」事件的状态",
                history and history[-1].payload["new_status"] == call.status,
                f"对象 {call.status}，事件 {status_values(history)}")

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

    proposed_events = named(events, CALL_PROPOSED)
    proposed_ids = [e.call_id for e in proposed_events]
    on_objects = [(a.call_id, a.tool, a.params, a.proposer, a.basis) for a in task.calls.values()]
    in_events = [(e.call_id, e.payload["tool"], e.payload["params"], e.payload["proposer"], e.payload["basis"])
                 for e in proposed_events]
    c.check("目标五：每个工具调用的编号、工具名、参数、提出者、依据与「工具调用提出」事件逐字段相等",
            on_objects == in_events, f"对象上 {on_objects}；事件里 {in_events}")
    c.check("工具调用表：键就是各工具调用自己的编号，顺序等于登记顺序（即「工具调用提出」事件的顺序）",
            all(k == a.call_id for k, a in task.calls.items()) and list(task.calls) == proposed_ids,
            (list(task.calls), proposed_ids))
    c.check(f"登记即放入工具调用表：{len(run.register_snapshots)} 次登记返回时，返回的编号已在工具调用表里、表大小加一、"
            "状态是已提出、「工具调用提出」事件已为该编号发出",
            all(size_after == size_before + 1 and status == CallStatus.PROPOSED and last_proposed == aid
                for aid, size_before, size_after, status, last_proposed in run.register_snapshots)
            and len(run.register_snapshots) == len(proposed_ids),
            run.register_snapshots)
    c.check(f"调用选择只返回候选、不写任何东西：{len(run.select_snapshots)} 次调用前后，任务数据、下一个工具调用编号、工具调用表、事件条数都没有变化",
            all(before == after for before, after in run.select_snapshots), run.select_snapshots)
    ended = named(events, TASK_ENDED)
    if task.end_record is None:
        c.check("投影：没有结束记录时事件流里也没有「任务结束」", not ended)
    else:
        c.check("投影：结束记录与「任务结束」事件的终态、原因一致",
                len(ended) == 1 and ended[0].payload == {"final_status": task.end_record.final_status,
                                                          "reason": task.end_record.reason})

    for call_id in proposed_ids:
        proposed = of_call(events, CALL_PROPOSED, call_id)[0]
        first_status = call_history(events, call_id)[0]
        c.check(f"工具调用 {call_id} 的「工具调用提出」事件在「已提出」状态变化之前发出",
                proposed.seq < first_status.seq and first_status.payload["new_status"] == CallStatus.PROPOSED)

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
        for call in run.task.calls.values():
            history = [e.payload["new_status"] for e in call_history(from_file, call.call_id)]
            c.check(f"目标六：用文件得到的工具调用 {call.call_id} 状态经过（中文值）与内存事件一致",
                    history == [s.value for s in status_values(call_history(run.events, call.call_id))], history)


def check_selection_trail(c: Checker, run: Run, expected: dict) -> None:
    """第三步第十四项：依据命中值里的「选择经过」（前进过的阶段、跳过的阶段与原因、跳过的步骤与原因）；告知异常的命中值不带它。"""
    for proposed in named(run.events, CALL_PROPOSED):
        hit = proposed.payload["basis"][2]
        aid = proposed.call_id
        if proposed.payload["tool"] == EXCEPTION_TOOL:
            c.check(f"工具调用 {aid} 是告知异常，命中值是异常报告，不带「选择经过」", isinstance(hit, dict) and "选择经过" not in hit, hit)
        elif aid in expected:
            c.check(f"工具调用 {aid} 的依据命中值里「选择经过」是 {expected[aid]}",
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

    for proposed in [e for e in events if e.name == CALL_PROPOSED]:
        aid = proposed.call_id
        mine = [e for e in all_events if e.call_id == aid]
        ctl = [e for e in mine if e.name == CONTROL_RESULT]
        c.check(f"追踪：工具调用 {aid} 有一条「执行控制结论」，结论已获准、核验恒允许",
                len(ctl) == 1 and ctl[0].payload == {"call_id": aid, "verdict": CallStatus.APPROVED, "checked": "恒允许"})
        calls = [e for e in mine if e.name == EXECUTE_CALL]
        waits = [e for e in mine if e.name == MAILBOX_WAIT and e.payload["box"] == INBOX]
        c.check(f"追踪：工具调用 {aid} 的「调用执行调用」依次是 enter、return，线程是内核线程",
                [e.payload["phase"] for e in calls] == ["enter", "return"]
                and all(e.payload["thread"] == KERNEL_THREAD_PREFIX + run.task_id for e in calls))
        if proposed.payload["tool"] == "告知":  # 第五步增补：告知只往发件箱放一条，不等回答
            c.check(f"追踪：工具调用 {aid}（工具 告知）只往发件箱放一条告知，不在收件箱上等待",
                    not waits and [(e.name, e.payload["box"]) for e in mine if e.name in (MESSAGE_PUT, MESSAGE_TAKEN)]
                    in ([(MESSAGE_PUT, OUTBOX)], [(MESSAGE_PUT, OUTBOX), (MESSAGE_TAKEN, OUTBOX)]))
            continue
        if proposed.payload["tool"] not in mailbox_tools:  # 第三步起告知异常也走邮箱，由新场景传入
            c.check(f"追踪：工具调用 {aid}（工具 {proposed.payload['tool']}）不碰邮箱，没有邮箱等待",
                    not waits and not [e for e in mine if e.name in (MESSAGE_PUT, MESSAGE_TAKEN)])
            continue
        c.check(f"追踪：工具调用 {aid} 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程",
                [e.payload["phase"] for e in waits] == ["begin", "end"]
                and isinstance(waits[1].payload.get("wait_ms"), (int, float)) and waits[1].payload["wait_ms"] >= 0
                and "wait_ms" not in waits[0].payload
                and all(e.payload["thread"] == KERNEL_THREAD_PREFIX + run.task_id for e in waits),
                [e.payload for e in waits])
        if len(calls) == 2 and len(waits) == 2:
            takes = [e for e in mine if e.name == MESSAGE_TAKEN and e.payload["box"] == INBOX]
            c.check(f"追踪：工具调用 {aid} 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后",
                    calls[0].seq < waits[0].seq < waits[1].seq < calls[1].seq
                    and all(t.seq > waits[1].seq for t in takes))


def expected_source(event, tool_of: dict) -> str:
    """按设计裁定，每个事件应有的记录方。"""
    if event.name in (DATA_CHANGED, TASK_STATUS_CHANGED, STEP_CHANGED):
        return SOURCE_UPDATE
    if event.name in (MESSAGE_PUT, MESSAGE_TAKEN, MAILBOX_CLOSED, MAILBOX_WAIT):
        return SOURCE_MAILBOX
    if event.name == CALL_STATUS_CHANGED and event.payload["new_status"] not in (CallStatus.PROPOSED, CallStatus.APPROVED):
        return TOOL_SOURCE_PREFIX + tool_of[event.call_id]
    return SOURCE_LOOP


def check_sources_and_senders(c: Checker, run: Run, closed_before_failure_of: int | None) -> None:
    """记录方与发起方：记录方按组件写对；发起方自报的值用线程做事实核对；邮箱关闭有事件。"""
    all_events = run.all_events
    tool_of = {e.call_id: e.payload["tool"] for e in all_events if e.name == CALL_PROPOSED}
    wrong = [(e.seq, e.name, e.source, expected_source(e, tool_of)) for e in all_events
             if e.source != expected_source(e, tool_of)]
    c.check("记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，"
            "工具实现记的状态 tool.<工具名>，其余 kernel.loop）", not wrong, wrong)
    status_sources = sorted({(e.payload["new_status"].value, e.source) for e in all_events if e.name == CALL_STATUS_CHANGED})
    if run.events and any(e.name == CALL_PROPOSED for e in run.events):
        expected_sources = {SOURCE_LOOP} | {TOOL_SOURCE_PREFIX + name for name in set(tool_of.values())}
        c.check("记录方：同一个事件名「工具调用状态变化」来自不同记录方（已提出、已获准是 kernel.loop，"
                f"其余是所用工具的 tool.<工具名>：{sorted(expected_sources - {SOURCE_LOOP})}）",
                {src for _, src in status_sources} == expected_sources, status_sources)

    def on(box, name):
        return [e for e in all_events if e.name == name and e.payload["box"] == box]

    inbox_acts = on(INBOX, MESSAGE_PUT) + on(INBOX, MAILBOX_CLOSED)
    c.check("发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外",
            all(e.payload["sender"] in EXTERNAL_SENDERS and run.thread_of_seq[e.seq] != run.kernel_thread for e in inbox_acts),
            [(e.seq, e.name, e.payload["sender"]) for e in inbox_acts])
    questions = on(OUTBOX, MESSAGE_PUT)
    # 第五步增补起发件箱里还有告知（种类 notice，出自工具「告知」，不等回答）
    c.check("发起方：发件箱里的问题与告知，发起方是 tool.<工具名>，类型是 question（告知工具放的是 notice），收件人是 user，出自内核线程",
            all(e.payload["sender"] == TOOL_SOURCE_PREFIX + tool_of[e.call_id]
                and e.payload["kind"] == ("notice" if tool_of[e.call_id] == "告知" else "question")
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
        history = call_history(run.events, aid)
        waiting = [e for e in history if e.payload["new_status"] == CallStatus.WAITING]
        failed = [e for e in history if e.payload["new_status"] == CallStatus.FAILED]
        c.check(f"邮箱关闭：收件箱恰有一条「邮箱关闭」，发起方 host，落在工具调用 {aid} 的「等待中」与「已失败」之间",
                len(inbox_closed) == 1 and inbox_closed[0].payload == {"box": INBOX, "sender": "host"} and waiting and failed
                and waiting[0].seq < inbox_closed[0].seq < failed[0].seq,
                [(e.seq, e.payload) for e in inbox_closed])
        c.check(f"邮箱关闭：出错时，发件箱在工具调用 {aid}「已失败」之后由内核关闭",
                failed and outbox_closed and outbox_closed[0].seq > failed[0].seq)

    host_waits = [e for e in all_events if e.name == MAILBOX_WAIT and e.payload["box"] == OUTBOX]
    c.check("邮箱等待：宿主在发件箱上的等待都出自主线程、不带工具调用编号，begin 与 end 成对",
            host_waits and all(run.thread_of_seq[e.seq] == run.host_thread and e.call_id is None for e in host_waits)
            and [e.payload["phase"] for e in host_waits] == ["begin", "end"] * (len(host_waits) // 2),
            [(e.seq, e.payload["phase"]) for e in host_waits])


def check_waiting_then_success(c: Checker, run: Run, call_id: int, expected_utterance=None) -> None:
    """验证目标二：询问工具调用在拿到回答前处于等待中，回答到达后完成。

    expected_utterance 由调用方给出时以它为准，不查预期句表：术语澄清确认那一问的话里带着模型现写的草稿，
    写不进模块级的常量表。
    """
    events = run.events
    history = call_history(events, call_id)
    c.check(f"目标二：工具调用 {call_id} 的状态序列是 已提出、已获准、等待中、已成功",
            status_values(history) == [CallStatus.PROPOSED, CallStatus.APPROVED, CallStatus.WAITING, CallStatus.SUCCEEDED],
            [s.value for s in status_values(history)])
    if len(history) != 4:
        return
    waiting, succeeded = history[2], history[3]
    mine = [e for e in events if e.call_id == call_id]
    questions = [e for e in mine if e.name == MESSAGE_PUT and e.payload["box"] == OUTBOX]
    puts = [e for e in mine if e.name == MESSAGE_PUT and e.payload["box"] == INBOX]
    takes = [e for e in mine if e.name == MESSAGE_TAKEN and e.payload["box"] == INBOX]
    proposed = of_call(events, CALL_PROPOSED, call_id)
    c.check(f"目标二：工具调用 {call_id} 恰有一条问题（发件箱放入），内容是 {{话, 参数}}，参数是工具调用参数原样，且在「等待中」之前",
            len(questions) == 1 and proposed and list(questions[0].payload["content"]) == ["utterance", "params"]
            and questions[0].payload["content"]["params"] == proposed[0].payload["params"]
            and questions[0].seq < waiting.seq)
    if len(questions) != 1:
        return
    target = proposed[0].payload["params"]["target"]
    if expected_utterance is None:
        expected_utterance = EXPECTED_UTTERANCES.get(target_key(target))
    c.check(f"第一步目标三：工具调用 {call_id} 问题里的话逐字等于预期句「{expected_utterance}」",
            expected_utterance is not None and questions[0].payload["content"]["utterance"] == expected_utterance,
            questions[0].payload["content"]["utterance"])
    c.check(f"目标二：工具调用 {call_id} 的「等待中」说明写明了问题的到达序号",
            waiting.payload["note"] == f"已向使用者提问，问题 {questions[0].payload['seq']}", waiting.payload["note"])
    c.check(f"目标二：工具调用 {call_id} 在收件箱恰有一条回答放入和一条取出", len(puts) == 1 and len(takes) == 1)
    if len(puts) != 1 or len(takes) != 1:
        return
    c.check(f"目标二：工具调用 {call_id} 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前",
            waiting.seq < puts[0].seq < takes[0].seq < succeeded.seq,
            (waiting.seq, puts[0].seq, takes[0].seq, succeeded.seq))
    c.check(f"目标二：工具调用 {call_id} 的回答的回复对象等于问题的到达序号",
            puts[0].payload["in_reply_to"] == takes[0].payload["in_reply_to"] == questions[0].payload["seq"])
    c.check(f"目标二：工具调用 {call_id} 的问题与回答，内容里的所属工具调用都是 {call_id}",
            questions[0].payload["call_id"] == puts[0].payload["call_id"] == takes[0].payload["call_id"] == call_id)
    # 第五步起询问还可能登记上一问、清空待处理，这里只看写入目标那一条，另外两条由场景自己核对。
    changes = [e for e in of_call(events, DATA_CHANGED, call_id) if e.payload["slot"] == target["slot"]]
    path = target["path"]
    c.check(f"目标二：工具调用 {call_id} 的数据变更新值按写入目标路径 {path} 取出的值 = 取出的回答 = 「已成功」事件的返回值；"
            "槽位是写入目标的槽位，除该路径外新旧值相同",
            len(changes) == 1 and changes[0].payload["slot"] == target["slot"]
            and get_at(changes[0].payload["new"], path) == takes[0].payload["content"] == succeeded.payload["result"]
            and (not path or tools_set_at(changes[0].payload["old"], path, takes[0].payload["content"]) == changes[0].payload["new"]))


def check_explainable(c: Checker, run: Run) -> None:
    """验证目标三的机器层。"""
    events, task = run.events, run.task
    c.check("目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致",
            replay_data(events) == task.data)
    for call in task.calls.values():
        proposed = of_call(events, CALL_PROPOSED, call.call_id)[0]
        before = replay_data([e for e in events if e.seq < proposed.seq])
        c.check(f"目标三：重放到工具调用 {call.call_id} 的「工具调用提出」之前，得到的数据就是它的执行前实际数据",
                before == run.execute_snapshots.get(call.call_id),
                f"重放 {before}，实际 {run.execute_snapshots.get(call.call_id)}")
        terminal = call_history(events, call.call_id)[-1]
        c.check(f"目标三：由事件重建的工具调用 {call.call_id} 返回值与工具调用对象上的一致",
                terminal.payload.get("result") == call.result)


def check_kernel_is_task_agnostic(c: Checker) -> None:
    """内核与任务无关：源码里不出现任何任务的槽位名与工具名，也不导入工具表、任务与观测模块。

    第四步起零差异哈希断言取消（2026-09-16 用户裁定：四个内核文件都可以改，每处改动列进实施报告由用户逐条看），
    这组机械检查才是真正的不变量，所以把原先分在两处的词表检查与导入检查并到一起。
    """
    source_path = Path(kernel.__file__)
    source = source_path.read_text(encoding="utf-8")
    words = ("目的地", "日期", "事由",  # 出差申请单（该任务的场景已退役，词表留着看住内核）
             "目录", "文件总表", "材料清单", "登记进度", "清单文件路径", "list_dir", "register_file", "generate_manifest",
             "术语", "原文片段", "释义草稿", "确认释义", "生成术语释义",  # 术语澄清
             "对话理解", "上一问", "修改意见",  # 第五步：对话理解与它的必备槽位
             "答疑", "标记推迟", "主线", "插入")  # 第五步增补：对话模式层只有任务定义加载器认识
    hits = {word: source.count(word) for word in words if source.count(word)}
    c.check("内核文件里不出现三个任务的槽位名与工具名", not hits, hits)
    c.check("内核文件里不出现工具名（字符串 \"ask\" 与「询问」）",
            not re.search(r"""["']ask["']""", source) and "询问" not in source)
    from tod_kernel import taskdef as taskdef_module
    loader_source = Path(taskdef_module.__file__).read_text(encoding="utf-8")
    stale = {name: text.count(word) for name, text in (("kernel.py", source), ("taskdef.py", loader_source))
             for word in ("游标", "cursor") if text.count(word)}
    c.check("内核与任务定义加载器里不再出现「游标」「cursor」（第四步 4.10 节废除了这个词与那个保留槽位）", not stale, stale)
    # 2026-09-17「行动」改名「工具调用」：本目录下的代码里不该再有旧名字。
    # observe.py 是唯一的例外：它要认出旧运行文件里的旧键 action_id，把它归一成 call_id。
    # 两个文件例外：observe.py 要认出旧运行文件里的旧键 action_id 好归一成 call_id；
    # 本文件是这条检查自己待的地方，旧名字作为被查的词写在这一段里，所以不查自己。
    renamed = {}
    for path in sorted(Path(kernel.__file__).resolve().parent.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [word for word in ("行动", "Action") if word in text]
        if "action_id" in text and path.name != "observe.py":
            hits.append("action_id")
        if hits:
            renamed[path.name] = hits
    c.check("这一目录下的代码里不再出现「行动」「Action」「action_id」（observe.py 认旧运行文件的旧键除外）",
            not renamed, renamed)
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


def call_sequence(events) -> list:
    """工具调用序列：每个「工具调用提出」的（工具名, 参数实际值, 依据序号），经 JSON 往返统一元组与列表。"""
    import json as json_module

    rows = [(e.payload["tool"], e.payload["params"], e.payload["basis"][0]) for e in events if e.name == CALL_PROPOSED]
    return json_module.loads(json_module.dumps(rows, ensure_ascii=False))


def check_matches_step_two(c: Checker, run: Run) -> None:
    """第三步目标一：工具调用序列与第二步留下的旧运行文件逐位相等。"""
    old_path = RUNS_DIR / STEP_TWO_RUN_FILES[run.task_id]
    c.check(f"第三步目标一：第二步的旧运行文件 {old_path.name} 存在（只在本机成立）", old_path.is_file(), old_path)
    if not old_path.is_file():
        return
    old, new = call_sequence(read_events(old_path)), call_sequence(run.events)
    c.check(f"第三步目标一：工具调用序列（工具名、参数实际值、依据序号）与 {old_path.name} 逐位相等，共 {len(old)} 个工具调用",
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


def check_other_tool_calls(c: Checker, run: Run) -> None:
    """非询问工具调用：状态经过是已提出、已获准、已成功；变更只写该工具声明的可写槽位。"""
    events, task = run.events, run.task
    for call in task.calls.values():
        if call.tool == "ask":
            continue
        history = status_values(call_history(events, call.call_id))
        slots = {e.payload["slot"] for e in of_call(events, DATA_CHANGED, call.call_id)}
        allowed = run.writable.get(call.tool)
        if allowed is not None and not allowed:
            # 不写槽位的工具（告知、答疑）：一条变更都不该有
            c.check(f"工具调用 {call.call_id}（{call.tool}）的状态经过是 已提出、已获准、已成功，不写任何槽位",
                    history == [CallStatus.PROPOSED, CallStatus.APPROVED, CallStatus.SUCCEEDED] and not slots,
                    (history, slots))
            continue
        if allowed is None:
            # 可写槽位由数据决定的工具（对话理解）：只能写使用者可写的槽位，外加它自己读后清空、登记的必备槽位
            metas = run.task_def.DEFINITION.get("槽位", {})
            allowed = {name for name, meta in metas.items() if meta.get("使用者可写", True) is not False}
            label = "使用者可写的槽位与必备槽位"
        else:
            label = f"可写槽位 {sorted(allowed)}"
        c.check(f"工具调用 {call.call_id}（{call.tool}）的状态经过是 已提出、已获准、已成功，变更只写{label}",
                history == [CallStatus.PROPOSED, CallStatus.APPROVED, CallStatus.SUCCEEDED]
                and slots and slots <= set(allowed),
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
    proposed = named(events, CALL_PROPOSED)
    tools = [e.payload["tool"] for e in proposed]
    c.check("第一步目标二：恰好八个工具调用（八次迭代每次一个工具调用，第八次迭代末尾的结果检查为真）", len(proposed) == 8, len(proposed))
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
    manifest = [a for a in task.calls.values() if a.tool == "generate_manifest"]
    text = manifest[0].result if manifest else ""
    c.check("生成清单文件的返回值含 a.docx 与 c.xlsx、不含 b.pdf",
            manifest and "a.docx" in text and "c.xlsx" in text and "b.pdf" not in text, text)
    for call in task.calls.values():
        if call.tool == "ask":
            check_waiting_then_success(c, run, call.call_id)
    check_other_tool_calls(c, run)
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
        dict(task_id="T-glossary-1", task_def=glossary_def(dict(GLOSSARY_INPUT_ONE)), answers={REPLY_KEY: [REPLY_CONFIRM]},
             tool_names=GLOSSARY_TOOLS, call_model=glossary_call()[0]),
        dict(task_id="T-glossary-2", task_def=glossary_def(dict(GLOSSARY_INPUT_TWO)),
             answers={REPLY_KEY: [REPLY_REVISE, REPLY_CONFIRM]}, tool_names=GLOSSARY_TOOLS, call_model=glossary_call()[0]),
    ]


def expected_summaries() -> dict:
    """摘要预期表：任务标识 → （任务定义名, 终态, 迭代数, 工具调用数）。

    写成函数而不是模块级字典，因为任务定义名取自后面定义的常量。
    结果检查第三步起挪到迭代末尾，所以正常完成的运行迭代数等于工具调用数；以内核错误结束的那次迭代没有末尾检查，数目不变。
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
            actual = (summary.task_def_name, summary.final_status, summary.loops, summary.calls)
            if expected.get(summary.task_id) != actual:
                wrong.append((f.name, actual))
        c.check("第二步目标一：每份文件的摘要（任务定义名、终态、迭代数、工具调用数）与预期表逐行相等", not wrong, wrong)
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
                    and (old_summary.task_def_name, old_summary.final_status, old_summary.loops, old_summary.calls) == expected["T-intake-1"],
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
    # 对话模式文件 patterns.json 不是任务定义，格式由加载器的 load_patterns 校验
    good = sorted(path for path in TASK_DEFS_DIR.glob("*.json") if path.name not in (SCHEMA_FILE, "patterns.json"))
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


def check_exception_call(c: Checker, run: Run, call_id: int, number: int, stage: str,
                           params: dict, utterance: str, answer: str, kind: str = "一趟走完") -> None:
    """一条告知异常工具调用：候选内容、发出的问题、状态经过、返回值、变更组。"""
    events = run.events
    proposed = of_call(events, CALL_PROPOSED, call_id)
    c.check(f"工具调用 {call_id} 是告知异常，提出者是调用选择，依据序号 {number}、说明「{stage} › 告知异常」，"
            f"命中值是这份异常报告外加异常种类「{kind}」",
            len(proposed) == 1 and proposed[0].payload["tool"] == EXCEPTION_TOOL and proposed[0].payload["proposer"] == "selector"
            and list(proposed[0].payload["basis"]) == [number, f"{stage} › {EXCEPTION_TOOL}", {**params, "异常种类": kind}],
            proposed[0].payload if proposed else None)
    c.check(f"工具调用 {call_id} 的五个参数与预期逐项相等",
            proposed and proposed[0].payload["params"] == params, proposed[0].payload["params"] if proposed else None)
    questions = [e for e in of_call(events, MESSAGE_PUT, call_id) if e.payload["box"] == OUTBOX]
    c.check(f"工具调用 {call_id} 恰发出一条问题，类型 question，内容是 {{话, 参数}}，参数与工具调用参数相同",
            len(questions) == 1 and questions[0].payload["kind"] == "question"
            and list(questions[0].payload["content"]) == ["utterance", "params"]
            and questions[0].payload["content"]["params"] == params,
            [e.payload for e in questions])
    if len(questions) == 1:
        c.check(f"工具调用 {call_id} 的话逐字等于「{utterance}」",
                questions[0].payload["content"]["utterance"] == utterance, questions[0].payload["content"]["utterance"])
    history = call_history(events, call_id)
    terminal, result, note = {
        "重做本阶段": (CallStatus.SUCCEEDED, "重做", "使用者选择重做本阶段"),
        "主动终止": (CallStatus.FAILED, "主动终止", "使用者主动终止"),
        "被动终止": (CallStatus.FAILED, "被动终止", "任务无法继续，使用者确认终止"),
    }[answer]
    c.check(f"工具调用 {call_id} 的状态经过是 已提出、已获准、等待中、{terminal.value}，最后一条说明「{note}」，返回值「{result}」",
            status_values(history) == [CallStatus.PROPOSED, CallStatus.APPROVED, CallStatus.WAITING, terminal]
            and history[-1].payload["note"] == note and history[-1].payload.get("result") == result,
            [(e.payload["new_status"].value, e.payload["note"], e.payload.get("result")) for e in history])
    changes = of_call(events, DATA_CHANGED, call_id)
    steps = of_call(events, STEP_CHANGED, call_id)
    c.check(f"工具调用 {call_id} 没有数据变更：告知异常工具不写任何槽位", not changes, [e.payload for e in changes])
    if answer == "重做本阶段":
        c.check(f"工具调用 {call_id} 选重做：发一条当前步变化，新值是「{stage}」阶段起点，来源是这个工具调用",
                len(steps) == 1 and steps[0].payload["new"] == stack(step_at(stage)) and steps[0].payload["source"] == call_id,
                [e.payload for e in steps])
    else:
        c.check(f"工具调用 {call_id} 选终止：当前步不动，没有当前步变化事件", not steps, [e.payload for e in steps])


def exception_common(c: Checker, run: Run, loops: int) -> None:
    check_integrity(c, run)
    check_trace_shape(c, run, loops=loops, mailbox_tools=("ask", EXCEPTION_TOOL))
    check_sources_and_senders(c, run, closed_before_failure_of=None)
    check_run_file(c, run)
    check_explainable(c, run)


def check_registered_three(c: Checker, run: Run) -> None:
    proposed = named(run.events, CALL_PROPOSED)[:4]
    c.check("前四个工具调用依次是 列目录、登记文件×3，依据序号 1、2、2、2",
            [(e.payload["tool"], e.payload["basis"][0]) for e in proposed]
            == [("list_dir", 1), ("register_file", 2), ("register_file", 2), ("register_file", 2)],
            [(e.payload["tool"], e.payload["basis"][0]) for e in proposed])


def check_terminated(c: Checker, run: Run, call_id: int, note: str) -> None:
    c.check(f"内核错误携带工具调用 {call_id}", isinstance(run.error, KernelError) and run.error.call is not None
            and run.error.call.call_id == call_id, repr(run.error))
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
    proposed = named(run.events, CALL_PROPOSED)
    c.check("恰好五个工具调用，第五个（第五次迭代）是告知异常",
            [e.payload["tool"] for e in proposed] == ["list_dir"] + ["register_file"] * 3 + [EXCEPTION_TOOL],
            [e.payload["tool"] for e in proposed])
    check_exception_call(c, run, 5, BAD_GOAL_EXCEPTION_NUMBER, "登记", bad_goal_params(BAD_GOAL_AT_END),
                           BAD_GOAL_UTTERANCE_END, answer)
    check_terminated(c, run, 5, note)
    check_step_final(c, run, step_at("登记", 1, loop=(1, 1, 3)))
    # 第 5 次迭代是告知异常，使用者选终止：工具调用记已失败，当前步不动，所以只有五条当前步变化。
    c.check("当前步逐次记下走到哪：初始化、列目录一步、登记那一步循环三次；告知异常那次不写当前步",
            step_sequence(run) == stacked([
                ("初始化", step_at("列目录")),
                (1, step_at("列目录", 1)),
                (2, step_at("登记", 1, loop=(1, 1, 1))),
                (3, step_at("登记", 1, loop=(1, 1, 2))),
                (4, step_at("登记", 1, loop=(1, 1, 3)))]), step_sequence(run))
    exception_common(c, run, loops=5)
    return c


def update_group_checks() -> Checker:
    """更新组先核对整组再写入（第四步第 7 节第 13 条③）：任一项旧值不符，整组不写、一个事件也不发。

    直接调写入口，不跑任务：这一组要验的是写入口本身的规矩。
    """
    title = "第四步：更新组先核对再写入"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    task_def = intake_def("intake.json")
    task_id = "T-update-group"
    stream = kernel.EventStream(task_id)
    collector = MemoryCollector()
    stream.subscribe(collector)
    inbox, outbox = Mailbox(stream, task_id, INBOX), Mailbox(stream, task_id, OUTBOX)
    task = kernel.new_task(task_id, task_def, build_table(task_def, INTAKE_TOOLS), inbox, outbox, stream)
    kernel.update_state(task, task.init_changes)

    data_before, events_before = dict(task.data), len(collector.events)
    bad = [Change("登记进度", 0, 1, 7), Change("清单文件路径", "对不上的旧值", "清单.md", 7)]
    raised = None
    try:
        kernel.update_state(task, bad)
    except KernelError as error:
        raised = error.reason
    c.check("整组里有一项旧值不符：抛内核错误，错误话里写明是哪个槽位、当前值与声明值",
            raised is not None and "清单文件路径" in raised and "对不上的旧值" in raised, raised)
    c.check("那一组整组没写：前面那条合格的变更也没落到数据上", task.data == data_before, task.data)
    c.check("那一组一个事件也没发：事件条数与调用前一样", len(collector.events) == events_before,
            [e.name for e in collector.events[events_before:]])

    good = [Change("登记进度", 0, 1, 7), Change("清单文件路径", None, "清单.md", 7)]
    kernel.update_state(task, good)
    c.check("整组都合格：两条都写进数据，发出两条「数据变更」",
            task.data["登记进度"] == 1 and task.data["清单文件路径"] == "清单.md"
            and [e.name for e in collector.events[events_before:]] == [DATA_CHANGED, DATA_CHANGED],
            (task.data["登记进度"], [e.name for e in collector.events[events_before:]]))

    events_before = len(collector.events)
    twice = [Change("登记进度", 1, 2, 8), Change("登记进度", 2, 3, 8)]
    kernel.update_state(task, twice)
    c.check("同一组里两条写同一个槽位：后一条按前一条的新值核对，两条都写成，与逐条写入的次序一致",
            task.data["登记进度"] == 3 and len(collector.events) - events_before == 2, task.data["登记进度"])

    events_before = len(collector.events)
    mixed = [Change("登记进度", 3, 4, 9), StepChange({"阶段": "没有这个阶段"}, 9)]
    raised = None
    try:
        kernel.update_state(task, mixed)
    except Exception as error:  # 阶段不存在时 step_text／step_view 读不出来，但不该抛错
        raised = repr(error)
    c.check("更新组里可以同时有数据变更与当前步更新，两样一次写完", raised is None and task.data["登记进度"] == 4, raised)
    c.check("同一次写入口发出的事件：两条（一条数据变更、一条当前步变化）",
            [e.name for e in collector.events[events_before:]] == [DATA_CHANGED, STEP_CHANGED],
            [e.name for e in collector.events[events_before:]])
    outbox.close("host")
    return c


def current_step_checks() -> Checker:
    """当前步（第四步 4.10 节）：初始值、记录本步的三条规则、「第几次」怎么加、不合法怎么报、两个显示接口。

    手写当前步与工具调用，不跑任务：这一组要验的是规则本身。场景里的当前步序列另在各场景断言里逐次核对。
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
            glossary.INITIAL_STEP == stack(step_at("写释义草稿")) and intake.INITIAL_STEP == stack(step_at("列目录")),
            (glossary.INITIAL_STEP, intake.INITIAL_STEP))

    def a_call(number, status=CallStatus.SUCCEEDED, result=None):
        """一个只有记录本步用得着的部分的工具调用：依据序号、终态、返回值。"""
        made = ToolCall(tool="无所谓", params={}, proposer="selector", basis=(number, "无所谓", {}))
        made.status, made.result = status, result
        return made

    # 规则一：工具调用没成功，当前步不动。
    before = step_at("确认", 2, loop=(1, 3, 2))
    def main_of(value):
        """第五步起记录本步返回地址栈；这组检查只看主线那一层，插入段另有检查。"""
        return value.get("主线") if isinstance(value, dict) and "主线" in value else value

    c.check("记录本步规则一：工具调用已失败时当前步原样不动",
            glossary.record_step(before, a_call(3, CallStatus.FAILED)) == before,
            glossary.record_step(before, a_call(3, CallStatus.FAILED)))
    # 规则二：告知异常且返回值「重做」回到该阶段起点；终止不动。
    redo = main_of(intake.record_step(step_at("登记", 1, loop=(1, 1, 3)), a_call(6, result="重做")))
    stop = main_of(intake.record_step(step_at("登记", 1, loop=(1, 1, 3)), a_call(6, result="被动终止")))
    c.check("记录本步规则二：告知异常选重做，当前步回到该阶段起点『登记』；选终止时不动",
            redo == step_at("登记") and stop == step_at("登记", 1, loop=(1, 1, 3)), (redo, stop))
    # 规则三：其余写该步的阶段与阶段内序号，落在循环段里时带起止与第几次。
    c.check("记录本步规则三：不在循环段里的一步只写阶段与阶段内序号",
            main_of(glossary.record_step(step_at("写释义草稿"), a_call(2))) == step_at("写释义草稿", 2),
            glossary.record_step(step_at("写释义草稿"), a_call(2)))

    # 「第几次」：进这段循环记 1；同一次里沿用；回到段首加 1；段尾被跳过、停在段中时回段首也加 1。
    first = main_of(glossary.record_step(step_at("写释义草稿", 2), a_call(3)))
    same = main_of(glossary.record_step(first, a_call(4)))
    again = main_of(glossary.record_step(step_at("确认", 3, loop=(1, 3, 1)), a_call(3)))
    skipped = main_of(glossary.record_step(step_at("确认", 2, loop=(1, 3, 1)), a_call(3)))
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
            glossary.select_call({}, value)
            raised = None
        except DefinitionError as error:
            raised = error.reason
        c.check(f"不合法的当前步（{name}）抛任务定义错误，原因是一句能读的话", bool(raised), raised)

    c.check("当前步合法时调用选择照常返回候选（不合法判据没有误伤正常值）",
            glossary.select_call({**glossary.SLOTS, "术语": "基线"}, step_at("写释义草稿"))[0] == DRAFT_TOOL,
            glossary.select_call({**glossary.SLOTS, "术语": "基线"}, step_at("写释义草稿")))

    # 显示用的两个接口。
    texts = [glossary.step_text(step_at("写释义草稿")),
             glossary.step_text(step_at("写释义草稿", 2)),
             glossary.step_text(step_at("确认", 2, loop=(1, 3, 2)))]
    c.check("step_text 三种形状：阶段起点、不在循环段里、在循环段里（写明起止、第几次与上限）",
            texts == ["当前步：『写释义草稿』阶段，还没有做完任何一步",
                      "当前步：『写释义草稿』阶段，做完了第 2 步『根据术语（与原文片段，若有）生成释义草稿』",
                      "当前步：『确认』阶段，第 1 到第 3 步循环的第 2 次（最多 5 次），做完了第 2 步『理解使用者的回复』"],
            texts)
    view = glossary.step_view(step_at("确认", 2, loop=(1, 3, 2)))
    c.check("step_view 给出阶段名、阶段内序号、这一步的说明、循环起止与第几次与上限、是不是段尾，页面不用自己算",
            view == {"阶段": "确认", "步骤": 2, "说明": "理解使用者的回复",
                     "循环": {"起": 1, "止": 3, "第几次": 2, "最多": 5}, "是段尾": False, "插入": []}, view)
    c.check("step_text 读不懂时照实说，不抛错（旧运行文件或宿主给了别的东西）",
            glossary.step_text({"阶段": "没有这个阶段"}).startswith("当前步：读不出来")
            and glossary.step_view("不是字典") is None, glossary.step_text({"阶段": "没有这个阶段"}))
    c.check("阶段名这个键就叫「阶段」（当前步的第一层，与告知异常参数里的写法一致）", STEP_STAGE == "阶段")
    return c


# ───────────────────────── 场景：术语澄清 ─────────────────────────

GLOSSARY_TOOLS = ("ask", DRAFT_TOOL, UNDERSTAND_TOOL)
GLOSSARY_FILE = "glossary.json"
# 录制文件：模式「回放」时模型的回答从这里来。路径以 tod_kernel 包目录为基准。
GLOSSARY_RECORDING = "task_defs/recordings/glossary.json"

# 场景的定稿文本（第四步 2026-09-16 定下两段输入，第五步 2026-09-17 定下六句回答，回答取第五步 4.9 节的原句）。
# 它们与提示词一起决定请求哈希：改一个字，录制文件就全部失效要重录。
GLOSSARY_TERM_ONE = "基线"
GLOSSARY_SOURCE = ("每一轮评审通过后，把当时的全部条目连同它们的版本号一并冻结下来，"
                   "形成一份此后只能经变更流程修改的参照物；后续的改动都以它为对照。")
GLOSSARY_TERM_TWO = "需求确认"
GLOSSARY_INPUT_ONE = {"术语": GLOSSARY_TERM_ONE, "原文片段": GLOSSARY_SOURCE}
GLOSSARY_INPUT_TWO = {"术语": GLOSSARY_TERM_TWO}
REPLY_CONFIRM = "可以，就这样。"
REPLY_REVISE = "太长了，压成两句，并且要说明产出是签字确认的需求清单。"
REPLY_REWRITE = "需求确认是相关方逐条审阅需求并签字认可的活动，产出是一份双方签字的需求清单。"
REPLY_ALTS = "换个说法吧。"
REPLY_DEFER = "先放着，术语我想改成「需求评审」。"
REPLY_DEFER_TERM = "需求评审"
REPLY_ASK = "「产出」是什么意思？"
REPLY_KEY = ("回复", ())
SUGGEST_QUESTION = {"类型": "建议", "槽位": "释义草稿", "路径": [], "选项": None, "采纳到": "确认释义"}
# 对话理解本步段的五种白话写法（第五步 4.4A 节），以术语澄清的槽位表为例；逐字核对，改提示词包要同步改这里。
GLOSSARY_PLACES = "术语、原文片段、修改意见、确认释义"
STEP_TEXT_SUGGEST = ("本步执行工具「对话理解」：系统刚才念了一份「释义草稿」，请使用者确认或提修改意见；"
                     "使用者同意时，这份草稿会写进「确认释义」。现在请判断使用者的回答：可以是同意、否定、要换一份；"
                     f"也可以顺带给出内容（可写的位置：{GLOSSARY_PLACES}）、提问、推迟、无关的话；只有语气词或分不清意思时要澄清，不要当成同意。")
STEP_TEXT_CHECK = ("本步执行工具「对话理解」：系统刚才向使用者核对，使用者说的「需求评审」是不是应该记成「术语」。"
                   "现在请判断使用者的回答：可以是同意（记得对）、否定（记得不对）；"
                   f"也可以顺带给出内容（可写的位置：{GLOSSARY_PLACES}）、提问、推迟、无关的话；只有语气词或分不清意思时要澄清，不要当成同意。")
STEP_TEXT_CHOICE = ("本步执行工具「对话理解」：系统刚才请使用者从这几项里选一项：1 确认这一稿、2 修改这一稿、3 先放一放。"
                    "现在请判断使用者的回答：可以是同意（选了其中一项，给出那一项的序号）、否定（哪一项都不要）；"
                    f"也可以顺带给出内容（可写的位置：{GLOSSARY_PLACES}）、提问、推迟、无关的话；只有语气词或分不清意思时要澄清，不要当成同意。")
STEP_TEXT_REQUEST = ("本步执行工具「对话理解」：系统刚才请使用者提供「术语」。现在请判断使用者的回答："
                     f"可以是给出内容（可写的位置：{GLOSSARY_PLACES}）、否定（不提供）；也可以是提问、推迟、无关的话；只有语气词或分不清意思时要澄清，不要当成同意。")
STEP_TEXT_NONE = ("本步执行工具「对话理解」：系统这次没有向使用者提问，是使用者主动说了一句话。现在请判断这句话："
                  f"可以是给出内容（可写的位置：{GLOSSARY_PLACES}）、提问、推迟、无关的话；只有语气词或分不清意思时要澄清。")


def glossary_def(initial=None):
    return taskdef.load(TASK_DEFS_DIR / GLOSSARY_FILE, initial=initial)


def glossary_call(recording=GLOSSARY_RECORDING, mode=llm.MODE_REPLAY):
    """术语澄清场景用的模型调用件，以及它读的那份配置。

    模式与录制文件由场景定死（一律回放，回答来自仓内的录制文件），所以验证脚本在没有模型服务的机器上照样全过；
    模型名、超时这些照读配置文件，不在代码里写死。录制是用「录制」模式对着真模型服务跑出来的，见实施报告。
    """
    config = {**llm.load_config(), "mode": mode}
    return llm.make_caller(config, recording_path=recording), config


def confirm_utterance(term: str, draft: str) -> str:
    """念草稿那一问的预期句：模板在定义文件里，草稿是模型现写的，所以由场景现拼。"""
    return f"对术语「{term}」的释义草稿是：{draft} 请确认，或提出修改意见。"


def model_record(run: Run, call_id: int) -> dict:
    """某条工具调用的返回值（模型工具的返回值就是调用记录）。工具调用不存在时返回空字典，让断言判失败而不是抛异常。"""
    call = run.task.calls.get(call_id) if run.task else None
    record = call.result if call is not None else None
    return record if isinstance(record, dict) else {}


def check_model_record(c: Checker, run: Run, call_id: int, config: dict, shape: str, segments: list) -> None:
    """一条在工具里调模型的工具调用，它的返回值是完整的调用记录。"""
    record = model_record(run, call_id)
    c.check(f"工具调用 {call_id} 的返回值是模型调用记录：模式「{llm.MODE_REPLAY}」、模型名取自配置、"
            f"输出形状「{shape}」，另有系统提示哈希、用户内容、返回原文、请求哈希与耗时",
            record.get("mode") == llm.MODE_REPLAY and record.get("model") == config["model"]
            and record.get("shape") == shape and len(record.get("system_prompt_hash") or "") == 64
            and isinstance(record.get("user_content"), str) and isinstance(record.get("response"), str)
            and len(record.get("request_hash") or "") == 64 and isinstance(record.get("elapsed_ms"), int),
            {key: record.get(key) for key in ("mode", "model", "shape", "elapsed_ms")})
    actual = [segment["type"] for segment in record.get("segments") or []]
    c.check(f"工具调用 {call_id} 的段列表按固定顺序装了：{'、'.join(segments)}", actual == segments, actual)
    c.check(f"工具调用 {call_id} 的每段都带类型、来源与正文，正文是逐字原文（不截断）",
            all(segment.get("source") and isinstance(segment.get("text"), str)
                for segment in record.get("segments") or []), actual)


def segment_text(run: Run, call_id: int, seg_type: str, source_part: str = "") -> str:
    """取某条调用记录里某一段的正文；source_part 用来在同类型多段里挑。"""
    for segment in model_record(run, call_id).get("segments") or []:
        if segment["type"] == seg_type and source_part in segment["source"]:
            return segment["text"]
    return ""


def segment_source(run: Run, call_id: int, seg_type: str, source_part: str = "") -> str:
    for segment in model_record(run, call_id).get("segments") or []:
        if segment["type"] == seg_type and source_part in segment["source"]:
            return segment["source"]
    return ""


def acts_of(run: Run, call_id: int) -> list:
    """某条对话理解工具调用规范化后的行为列表。"""
    return model_record(run, call_id).get("acts") or []


def functions_of(run: Run, call_id: int) -> list:
    return [act.get("功能") for act in acts_of(run, call_id)]


def written(run: Run, slot: str) -> list:
    """工具调用写进某个槽位的值，按事件顺序；初始化那条（来源「初始化」、不挂工具调用编号）不算。"""
    return [e.payload["new"] for e in named(run.events, DATA_CHANGED) if e.payload["slot"] == slot and e.call_id is not None]


def run_glossary(task_id: str, initial: dict, replies: list):
    call_model, config = glossary_call()
    run = run_scenario(task_id, glossary_def(dict(initial)), {REPLY_KEY: list(replies)}, GLOSSARY_TOOLS,
                       call_model=call_model)
    return run, config


def check_tool_sequence(c: Checker, run: Run, expected: list, description: str) -> bool:
    actual = [(e.payload["tool"], e.payload["basis"][0]) for e in named(run.events, CALL_PROPOSED)]
    c.check(description, actual == expected, actual)
    return len(actual) >= len(expected)


def check_understanding(c: Checker, run: Run, call_id: int, config: dict, reply: str, functions: list) -> None:
    """一条对话理解工具调用：调用记录的形状、这句原话、规范化后的功能列表、「回复」与「上一问」读后清空。"""
    check_model_record(c, run, call_id, config, "JSON", ["任务进度", "对话历史", "当前数据", "本步", "输出形状"])
    record = model_record(run, call_id)
    c.check(f"工具调用 {call_id}（对话理解）的记录带着原话「{reply}」、模型输出能按 Schema 解析（没有走没听懂的兜底）",
            record.get("reply") == reply and record.get("problem") is None, (record.get("reply"), record.get("problem")))
    c.check(f"工具调用 {call_id}（对话理解）规范化后的行为列表的功能依次是 {'、'.join(functions)}，每项都记了落成了什么",
            functions_of(run, call_id) == functions and all(act.get("落成") for act in acts_of(run, call_id)),
            [(act.get("功能"), act.get("内容"), act.get("落成")) for act in acts_of(run, call_id)])
    cleared = {e.payload["slot"]: e.payload["new"] for e in of_call(run.events, DATA_CHANGED, call_id)}
    c.check(f"工具调用 {call_id}（对话理解）读过后把「回复」与「上一问」清空",
            "回复" in cleared and cleared["回复"] is None and "上一问" in cleared and cleared["上一问"] is None, cleared)
    step = segment_text(run, call_id, "本步")
    c.check(f"工具调用 {call_id}（对话理解）的本步段逐字是建议类那一种白话写法；输出形状段说明这份结构也作为接口参数",
            step == STEP_TEXT_SUGGEST and "接口参数" in segment_source(run, call_id, "输出形状"), step)


def registered_questions(run: Run, call_id: int) -> list:
    """某条询问调用登记的上一问（数据变更里「上一问」的新值）。"""
    return [e.payload["new"] for e in of_call(run.events, DATA_CHANGED, call_id) if e.payload["slot"] == "上一问"]


def utterance_of(run: Run, call_id: int):
    """某条询问调用放进发件箱的那句话；没有就是 None。"""
    for event in of_call(run.events, MESSAGE_PUT, call_id):
        if event.payload["box"] == OUTBOX and isinstance(event.payload.get("content"), dict):
            return event.payload["content"].get("utterance")
    return None


def check_question_registered(c: Checker, run: Run, call_id: int, expected: dict) -> None:
    registered = registered_questions(run, call_id)
    c.check(f"工具调用 {call_id}（念草稿）登记了上一问 {expected}", registered == [expected], registered)


def glossary_tail(c: Checker, run: Run, loops: int) -> None:
    check_other_tool_calls(c, run)
    check_explainable(c, run)
    common_checks(c, run, loops=loops)


def glossary_scenario_confirm() -> Checker:
    """场景：术语澄清，确认。给全初始输入，念草稿后使用者一句「可以，就这样。」，对话理解出一项 AFFIRM，草稿抄进确认释义。"""
    title = "术语澄清：确认"
    banner(title)
    run, config = run_glossary("T-glossary-1", GLOSSARY_INPUT_ONE, [REPLY_CONFIRM])
    c = Checker(title)
    print("── 断言 ──")
    check_kernel_is_task_agnostic(c)
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None or not check_tool_sequence(
            c, run, [(DRAFT_TOOL, 2), ("ask", 3), (UNDERSTAND_TOOL, 4)],
            "三个工具调用：生成术语释义（第 2 步）、念草稿（第 3 步）、对话理解（第 4 步）；问术语那一步因写入目标已有值被跳过"):
        return c
    task = run.task
    skip_reason = "前置条件不成立：写入目标指向的位置为 None"
    check_selection_trail(c, run, {1: {"前进": [], "跳过阶段": [], "跳过步骤": [[1, skip_reason]]}})
    check_model_record(c, run, 1, config, "文本", ["任务进度", "对话历史", "当前数据", "参考材料", "本步", "输出形状"])
    c.check("写首稿时参考材料段装的是初始输入给的原文片段", segment_text(run, 1, "参考材料") == GLOSSARY_SOURCE,
            segment_text(run, 1, "参考材料"))
    draft = model_record(run, 1).get("response", "").strip()
    c.check("释义草稿等于模型返回原文去掉首尾空白", task.data.get("释义草稿") == draft, task.data.get("释义草稿"))
    check_waiting_then_success(c, run, 2, expected_utterance=confirm_utterance(GLOSSARY_TERM_ONE, draft))
    check_question_registered(c, run, 2, {**SUGGEST_QUESTION, "念的值": draft})
    check_understanding(c, run, 3, config, REPLY_CONFIRM, ["AFFIRM"])
    c.check("AFFIRM 按建议类上一问落：释义草稿的当前值写进确认释义，没有待路由的行为（不压插入段）",
            task.data.get("确认释义") == draft and model_record(run, 3).get("routes") == [], task.data.get("确认释义"))
    c.check("交付物只有一项，名字「术语释义」，来源「确认释义」，形态文本",
            [(d["名字"], d["来源"], d["形态"]) for d in run.task_def.DEFINITION.get("交付物", [])]
            == [("术语释义", "确认释义", "文本")], run.task_def.DEFINITION.get("交付物"))
    c.check("任务状态是已完成，术语与原文片段仍是初始输入给的那两段",
            task.status == TaskStatus.DONE and task.data.get("术语") == GLOSSARY_TERM_ONE
            and task.data.get("原文片段") == GLOSSARY_SOURCE, task.status)
    check_step_final(c, run, step_at("确认", 2, loop=(1, 3, 1)))
    glossary_tail(c, run, loops=3)
    return c


def glossary_scenario_revise() -> Checker:
    """场景：术语澄清，提修改意见。只给术语；第 1 次念草稿后使用者提意见（DENY 加 INFORM 修改意见），改稿，第 2 次确认。"""
    title = "术语澄清：提修改意见"
    banner(title)
    run, config = run_glossary("T-glossary-2", GLOSSARY_INPUT_TWO, [REPLY_REVISE, REPLY_CONFIRM])
    c = Checker(title)
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None or not check_tool_sequence(
            c, run, [(DRAFT_TOOL, 2), ("ask", 3), (UNDERSTAND_TOOL, 4), (DRAFT_TOOL, 5), ("ask", 3), (UNDERSTAND_TOOL, 4)],
            "六个工具调用：写首稿、念草稿、对话理解、按意见改稿、再念草稿、对话理解；依据序号 2、3、4、5、3、4"):
        return c
    task = run.task
    c.check("写首稿时没有原文片段，参考材料段写「（未提供，按通用含义解释）」",
            segment_text(run, 1, "参考材料") == "（未提供，按通用含义解释）", segment_text(run, 1, "参考材料"))
    check_understanding(c, run, 3, config, REPLY_REVISE, ["DENY", "INFORM"])
    feedback = next((act["内容"]["值"] for act in acts_of(run, 3) if act.get("功能") == "INFORM"), None)
    c.check("INFORM 写的是修改意见（槽位「修改意见」、路径空），值是非空文字；DENY 不写",
            any(act.get("功能") == "INFORM" and act["内容"]["槽位"] == "修改意见" and act["内容"]["路径"] == []
                for act in acts_of(run, 3)) and isinstance(feedback, str) and feedback.strip(),
            acts_of(run, 3))
    c.check("修改意见写了一次又被改稿清空；回复写了两次、每次对话理解读后都清空",
            written(run, "修改意见") == [feedback, None]
            and written(run, "回复") == [REPLY_REVISE, None, REPLY_CONFIRM, None],
            (written(run, "修改意见"), written(run, "回复")))
    first_draft = model_record(run, 1).get("response", "").strip()
    check_model_record(c, run, 4, config, "文本", ["任务进度", "对话历史", "当前数据", "参考材料", "本步", "输出形状"])
    c.check("改稿那次的任务进度段写明这段循环的第 1 次与上限 5、做完的是哪一步，并说明这次写第 2 稿",
            segment_text(run, 4, "任务进度").startswith(
                "当前步：『确认』阶段，第 1 到第 3 步循环的第 1 次（最多 5 次），做完了第 2 步『理解使用者的回复』")
            and "第 1 稿被要求修改，本次写第 2 稿" in segment_text(run, 4, "任务进度"), segment_text(run, 4, "任务进度"))
    c.check("改稿那次的当前数据：术语、上一稿、修改意见各一行，修订记录是段内最后一项",
            segment_text(run, 4, "当前数据").splitlines() == [
                f"术语：{GLOSSARY_TERM_TWO}", f"释义草稿（上一稿）：{first_draft}", f"修改意见：{feedback}",
                f"修订记录（系统从变更事件推出）：第 1 稿：{first_draft}", f"  使用者意见：{feedback}"],
            segment_text(run, 4, "当前数据").splitlines())
    second_draft = model_record(run, 4).get("response", "").strip()
    c.check("改稿写出第 2 稿，与第 1 稿不同", second_draft and second_draft != first_draft, second_draft[:40])
    check_waiting_then_success(c, run, 2, expected_utterance=confirm_utterance(GLOSSARY_TERM_TWO, first_draft))
    check_waiting_then_success(c, run, 5, expected_utterance=confirm_utterance(GLOSSARY_TERM_TWO, second_draft))
    c.check("改稿之后再念草稿：念的值变了，照常全文念第 2 稿，不说再问短句",
            utterance_of(run, 5) == confirm_utterance(GLOSSARY_TERM_TWO, second_draft) and utterance_of(run, 5) != REASK_LINE
            and registered_questions(run, 5) == [{**SUGGEST_QUESTION, "念的值": second_draft}],
            (utterance_of(run, 5), registered_questions(run, 5)))
    check_understanding(c, run, 6, config, REPLY_CONFIRM, ["AFFIRM"])
    c.check("第 6 次迭代 AFFIRM：第 2 稿写进确认释义，循环段结束，任务完成",
            task.data.get("确认释义") == second_draft and task.status == TaskStatus.DONE, task.data.get("确认释义"))
    c.check("当前步逐次记下走到哪：六次迭代各一条，第 5 次回到段首时第几次加一",
            step_sequence(run) == stacked([
                ("初始化", step_at("写释义草稿")), (1, step_at("写释义草稿", 2)),
                (2, step_at("确认", 1, loop=(1, 3, 1))), (3, step_at("确认", 2, loop=(1, 3, 1))),
                (4, step_at("确认", 3, loop=(1, 3, 1))), (5, step_at("确认", 1, loop=(1, 3, 2))),
                (6, step_at("确认", 2, loop=(1, 3, 2)))]), step_sequence(run))
    glossary_tail(c, run, loops=6)
    return c


def glossary_scenario_rewrite() -> Checker:
    """场景：术语澄清，给整段改写。使用者直接给出一段完整的释义：AFFIRM 加 INFORM 写确认释义，按写值落，不再抄草稿。"""
    title = "术语澄清：给整段改写"
    banner(title)
    run, config = run_glossary("T-glossary-3", GLOSSARY_INPUT_TWO, [REPLY_REWRITE])
    c = Checker(title)
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None or not check_tool_sequence(
            c, run, [(DRAFT_TOOL, 2), ("ask", 3), (UNDERSTAND_TOOL, 4)], "三个工具调用：写首稿、念草稿、对话理解"):
        return c
    task = run.task
    check_understanding(c, run, 3, config, REPLY_REWRITE, ["AFFIRM", "INFORM"])
    value = next((act["内容"]["值"] for act in acts_of(run, 3)
                  if act.get("功能") == "INFORM" and act["内容"]["槽位"] == "确认释义"), None)
    c.check("INFORM 写确认释义，值含使用者那段话的关键内容；AFFIRM 记「按写值落，不再抄草稿」",
            isinstance(value, str) and "逐条审阅需求并签字认可" in value
            and any(act.get("功能") == "AFFIRM" and "不再抄草稿" in act.get("落成", "") for act in acts_of(run, 3)),
            acts_of(run, 3))
    c.check("确认释义等于这项写值，不是草稿；确认释义只被写了一次", task.data.get("确认释义") == value
            and task.data.get("确认释义") != task.data.get("释义草稿") and written(run, "确认释义") == [value],
            written(run, "确认释义"))
    c.check("交付物术语释义取自确认释义，任务完成", task.status == TaskStatus.DONE, task.status)
    glossary_tail(c, run, loops=3)
    return c


def glossary_scenario_alts() -> Checker:
    """场景：术语澄清，要换一份。「换个说法吧。」是 REQALTS，修改意见写「换一份」，改稿后再念、确认。"""
    title = "术语澄清：要换一份"
    banner(title)
    run, config = run_glossary("T-glossary-4", GLOSSARY_INPUT_TWO, [REPLY_ALTS, REPLY_CONFIRM])
    c = Checker(title)
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None or not check_tool_sequence(
            c, run, [(DRAFT_TOOL, 2), ("ask", 3), (UNDERSTAND_TOOL, 4), (DRAFT_TOOL, 5), ("ask", 3), (UNDERSTAND_TOOL, 4)],
            "六个工具调用：写首稿、念草稿、对话理解（要换一份）、改稿、再念草稿、对话理解（确认）"):
        return c
    task = run.task
    check_understanding(c, run, 3, config, REPLY_ALTS, ["REQALTS"])
    c.check("REQALTS 把修改意见写成「换一份」，改稿后清空", written(run, "修改意见") == ["换一份", None],
            written(run, "修改意见"))
    c.check("改稿那次当前数据里的修改意见就是「换一份」", "修改意见：换一份" in segment_text(run, 4, "当前数据"),
            segment_text(run, 4, "当前数据"))
    second_draft = model_record(run, 4).get("response", "").strip()
    check_understanding(c, run, 6, config, REPLY_CONFIRM, ["AFFIRM"])
    c.check("换出来的第 2 稿写进确认释义，循环第 2 次结束，任务完成",
            task.data.get("确认释义") == second_draft and task.status == TaskStatus.DONE, task.data.get("确认释义"))
    check_step_final(c, run, step_at("确认", 2, loop=(1, 3, 2)))
    glossary_tail(c, run, loops=6)
    return c


REPLY_VAGUE = "嗯。"
REPLY_CHOOSE_ONE = "1"
CLARIFY_TEXT = "你是想：1 确认这一稿 2 修改这一稿 3 先放一放？"
FALLBACK_OPTIONS = ["确认这一稿", "修改这一稿", "先放一放"]


def pattern_number(task_def, mode: str, nth: int) -> int:
    """对话模式里某一步的依据序号：模式并进任务定义后，编号接在任务定义全部编号之后，这里从定义里查，不写死。"""
    for pattern in task_def.DEFINITION["对话模式"]["模式"]:
        if pattern["名字"] == mode:
            return pattern["步骤"][nth - 1]["编号"]
    raise KeyError(mode)


def notices_of(run: Run, call_id: int) -> list:
    """某个工具调用往发件箱放的告知消息的话。"""
    return [e.payload["content"]["utterance"] for e in of_call(run.events, MESSAGE_PUT, call_id)
            if e.payload["box"] == OUTBOX and e.payload["kind"] == "notice"]


def check_notice(c: Checker, run: Run, call_id: int, text: str) -> None:
    """告知：发件箱恰有一条种类为告知的消息、话逐字等于预期；不等回答（没有等待中，收件箱没有回复它的消息）。"""
    history = status_values(call_history(run.events, call_id))
    answers = [e for e in run.events if e.name == MESSAGE_PUT and e.payload["box"] == INBOX
               and e.payload["call_id"] == call_id]
    c.check(f"工具调用 {call_id}（告知）往发件箱放了一条告知，话逐字是「{text}」；状态经过 已提出、已获准、已成功，没有等回答",
            notices_of(run, call_id) == [text] and not answers
            and history == [CallStatus.PROPOSED, CallStatus.APPROVED, CallStatus.SUCCEEDED],
            (notices_of(run, call_id), [h.value for h in history]))


def glossary_scenario_defer() -> Checker:
    """场景：术语澄清，推迟并顺手改术语。一句话两项：INFORM 术语＝需求评审直接写；DEFER 确认释义路由到模式「推迟」。

    推迟模式先告知「『确认释义』先放着，回头再问。」，再标记推迟（2026-09-18 裁定问题一）；标记后停止条件成立，任务以完成收尾，
    交付物是推迟标记（2026-09-18 裁定问题六）。插入段刚做完第 2 步就结束了任务，所以当前步终态里插入段已弹出。
    """
    title = "术语澄清：推迟并顺手改术语"
    banner(title)
    run, config = run_glossary("T-glossary-5", GLOSSARY_INPUT_TWO, [REPLY_DEFER])
    c = Checker(title)
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    task_def = run.task_def
    tell, mark = pattern_number(task_def, "推迟", 1), pattern_number(task_def, "推迟", 2)
    if run.task is None or not check_tool_sequence(
            c, run, [(DRAFT_TOOL, 2), ("ask", 3), (UNDERSTAND_TOOL, 4), ("告知", tell), ("标记推迟", mark)],
            f"五个工具调用：写首稿、念草稿、对话理解，然后插入段「推迟」的告知（依据序号 {tell}）与标记推迟（{mark}）"):
        return c
    task = run.task
    record = model_record(run, 3)
    c.check("对话理解出两项：DEFER 与 INFORM（顺序不限），模型输出按 Schema 解析",
            sorted(functions_of(run, 3)) == ["DEFER", "INFORM"] and record.get("problem") is None,
            [(act.get("功能"), act.get("内容")) for act in acts_of(run, 3)])
    c.check("INFORM 写术语，值是「需求评审」，直接落；DEFER 的落成写明路由到模式『推迟』",
            any(act.get("功能") == "INFORM" and act["内容"] == {"槽位": "术语", "路径": [], "值": REPLY_DEFER_TERM}
                for act in acts_of(run, 3))
            and any(act.get("功能") == "DEFER" and act.get("落成") == "DEFER → 路由到模式『推迟』" for act in acts_of(run, 3)),
            acts_of(run, 3))
    c.check("对话理解的待路由的行为只有一项：DEFER，输入是目标槽位确认释义；它自己只写术语，并清空回复与上一问",
            record.get("routes") == [{"功能": "DEFER", "输入": {"槽位": "确认释义"}}]
            and {e.payload["slot"] for e in of_call(run.events, DATA_CHANGED, 3)} == {"术语", "回复", "上一问"},
            (record.get("routes"), sorted({e.payload["slot"] for e in of_call(run.events, DATA_CHANGED, 3)})))
    check_notice(c, run, 4, "『确认释义』先放着，回头再问。")
    c.check("工具调用 5（标记推迟）把确认释义写成推迟标记，任务完成，交付物术语释义取到的是推迟标记",
            [e.payload["new"] for e in of_call(run.events, DATA_CHANGED, 5)] == [{"已推迟": True}]
            and task.data.get("术语") == REPLY_DEFER_TERM and task.status == TaskStatus.DONE
            and task.data.get(run.task_def.DELIVERABLES[0]["来源"]) == {"已推迟": True},
            (task.data.get("确认释义"), task.status))
    in_loop = step_at("确认", 2, loop=(1, 3, 1))
    deferred = frame("推迟", 0, {"槽位": "确认释义"})
    c.check("当前步序列：对话理解之后压一帧「推迟」；告知做完第 1 步、结果记下那句话；标记推迟做完第 2 步，弹帧回到主线原地址",
            step_sequence(run) == stacked([("初始化", step_at("写释义草稿")), (1, step_at("写释义草稿", 2)),
                                           (2, step_at("确认", 1, loop=(1, 3, 1)))])
            + [(3, stack(in_loop, [deferred])),
               (4, stack(in_loop, [frame("推迟", 1, {"槽位": "确认释义"}, 结果="『确认释义』先放着，回头再问。")])),
               (5, stack(in_loop))], step_sequence(run))
    c.check("当前步的显示：插入段里时 step_text 在主线那句后面接插入段做到哪，step_view 带插入段列表",
            run.task_def.step_text(stack(in_loop, [deferred])).endswith("；插入段『推迟』还没有做完任何一步")
            and run.task_def.step_view(stack(in_loop, [deferred]))["插入"]
            == [{"模式": "推迟", "步骤": 0, "共": 2, "说明": None, "输入": {"槽位": "确认释义"}}],
            run.task_def.step_text(stack(in_loop, [deferred])))
    glossary_tail(c, run, loops=5)
    return c


def glossary_scenario_ask() -> Checker:
    """场景：术语澄清，提问后再确认。「「产出」是什么意思？」是 REQUEST，路由到模式「答疑」：
    答疑用一两句白话解释，告知说出来，弹帧；主线原地续接，改稿被跳过，下一次循环再念同一份草稿；使用者确认。"""
    title = "术语澄清：提问后再确认"
    banner(title)
    run, config = run_glossary("T-glossary-6", GLOSSARY_INPUT_TWO, [REPLY_ASK, REPLY_CONFIRM])
    c = Checker(title)
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    task_def = run.task_def
    answer_no, tell_no = pattern_number(task_def, "答疑", 1), pattern_number(task_def, "答疑", 2)
    if run.task is None or not check_tool_sequence(
            c, run, [(DRAFT_TOOL, 2), ("ask", 3), (UNDERSTAND_TOOL, 4), ("答疑", answer_no), ("告知", tell_no),
                     ("ask", 3), (UNDERSTAND_TOOL, 4)],
            "七个工具调用：写首稿、念草稿、对话理解（提问），插入段「答疑」的答疑与告知，再念草稿、对话理解（确认）；改稿被跳过"):
        return c
    task = run.task
    check_understanding(c, run, 3, config, REPLY_ASK, ["REQUEST"])
    asked = (acts_of(run, 3)[0].get("内容") or {}).get("问") if acts_of(run, 3) else None
    c.check("REQUEST 问的是「产出」，不写值；待路由的行为是一项 REQUEST，输入是问的内容",
            isinstance(asked, str) and "产出" in asked
            and model_record(run, 3).get("routes") == [{"功能": "REQUEST", "输入": {"问": asked}}]
            and {e.payload["slot"] for e in of_call(run.events, DATA_CHANGED, 3)} == {"回复", "上一问"},
            model_record(run, 3).get("routes"))
    check_model_record(c, run, 4, config, "文本", ["任务进度", "对话历史", "当前数据", "本步", "输出形状"])
    explanation = model_record(run, 4).get("输出")
    c.check("答疑那次的本步段写明使用者问了什么，当前数据段装任务的全部槽位，模型回答去掉首尾空白放在返回值的「输出」",
            segment_text(run, 4, "本步") == f"本步执行工具「答疑」：解释使用者问的这句话——{asked}"
            and segment_text(run, 4, "当前数据").startswith("术语：需求确认")
            and isinstance(explanation, str) and explanation
            and explanation == model_record(run, 4).get("response", "").strip(),
            (segment_text(run, 4, "本步"), explanation))
    check_notice(c, run, 5, explanation)
    draft = task.data.get("释义草稿")
    check_waiting_then_success(c, run, 2, expected_utterance=confirm_utterance(GLOSSARY_TERM_TWO, draft))
    check_waiting_then_success(c, run, 6, expected_utterance=REASK_LINE)
    c.check("答疑返回后再念草稿：草稿没变，只说再问短句「回到刚才那一稿：请确认，或提出修改意见。」，不复述草稿；"
            "两次登记的上一问相同，都带着念的值",
            utterance_of(run, 6) == REASK_LINE and draft not in utterance_of(run, 6)
            and registered_questions(run, 2) == registered_questions(run, 6) == [{**SUGGEST_QUESTION, "念的值": draft}],
            (utterance_of(run, 6), registered_questions(run, 6)))
    c.check("第二次对话理解的对话历史里有那句告知（系统一行，没有使用者那一行），接着是再问短句",
            f"系统：{explanation}\n系统：{REASK_LINE}" in segment_text(run, 7, "对话历史"), segment_text(run, 7, "对话历史"))
    check_understanding(c, run, 7, config, REPLY_CONFIRM, ["AFFIRM"])
    c.check("释义草稿只写过一稿（提问不改稿）；确认后草稿写进确认释义，任务完成",
            written(run, "释义草稿") == [draft] and task.data.get("确认释义") == draft and task.status == TaskStatus.DONE,
            written(run, "释义草稿"))
    in_loop = step_at("确认", 2, loop=(1, 3, 1))
    c.check("当前步序列：对话理解之后压一帧「答疑」，答疑做完第 1 步记下解释，告知做完第 2 步弹帧；主线从原地址续接，"
            "第 6 次迭代回到段首第几次加一",
            step_sequence(run)[3:] == [
                (3, stack(in_loop, [frame("答疑", 0, {"问": asked})])),
                (4, stack(in_loop, [frame("答疑", 1, {"问": asked}, 结果=explanation)])),
                (5, stack(in_loop)),
                (6, stack(step_at("确认", 1, loop=(1, 3, 2)))),
                (7, stack(step_at("确认", 2, loop=(1, 3, 2))))], step_sequence(run)[3:])
    glossary_tail(c, run, loops=7)
    return c


def glossary_scenario_vague() -> Checker:
    """场景：术语澄清，含糊后选择（第五步增补）。「嗯。」是 CLARIFY，路由到模式「澄清」：以选择类固定三项再问，
    使用者答「1」，对话理解按原问（建议类）落，把草稿采纳进确认释义，弹帧，任务完成。"""
    title = "术语澄清：含糊后选择"
    banner(title)
    run, config = run_glossary("T-glossary-7", GLOSSARY_INPUT_TWO, [REPLY_VAGUE, REPLY_CHOOSE_ONE])
    c = Checker(title)
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    task_def = run.task_def
    ask_no, understand_no = pattern_number(task_def, "澄清", 1), pattern_number(task_def, "澄清", 2)
    if run.task is None or not check_tool_sequence(
            c, run, [(DRAFT_TOOL, 2), ("ask", 3), (UNDERSTAND_TOOL, 4), ("ask", ask_no), (UNDERSTAND_TOOL, understand_no)],
            "五个工具调用：写首稿、念草稿、对话理解（含糊），插入段「澄清」的再问与对话理解"):
        return c
    task = run.task
    draft = task.data.get("释义草稿")
    check_understanding(c, run, 3, config, REPLY_VAGUE, ["CLARIFY"])
    c.check("CLARIFY 的待路由的行为是一项，输入是原问（念草稿那一问）；这次对话理解什么槽位都不写，只清空回复与上一问",
            model_record(run, 3).get("routes") == [{"功能": "CLARIFY", "输入": {"原问": SUGGEST_QUESTION}}]
            and {e.payload["slot"] for e in of_call(run.events, DATA_CHANGED, 3)} == {"回复", "上一问"},
            model_record(run, 3).get("routes"))
    check_waiting_then_success(c, run, 4, expected_utterance=CLARIFY_TEXT)
    check_question_registered(c, run, 4, {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": FALLBACK_OPTIONS,
                                          "采纳到": "确认释义", "原问": SUGGEST_QUESTION, "念的值": draft})
    record = model_record(run, 5)
    step = segment_text(run, 5, "本步")
    c.check("插入段里的对话理解：本步段逐字是选择类那一种白话写法，列出三个选项；使用者答「1」理解成 AFFIRM 选项序号 1",
            step == STEP_TEXT_CHOICE
            and [(act.get("功能"), act.get("内容")) for act in acts_of(run, 5)] == [("AFFIRM", {"选项序号": 1})]
            and record.get("problem") is None, [(act.get("功能"), act.get("内容")) for act in acts_of(run, 5)])
    c.check("选 1 按原问落：草稿采纳进确认释义；没有待路由的行为，也不在本帧再问；任务完成",
            task.data.get("确认释义") == draft and record.get("routes") == [] and not record.get("reask_in_frame")
            and task.status == TaskStatus.DONE, (task.data.get("确认释义"), record.get("routes")))
    in_loop = step_at("确认", 2, loop=(1, 3, 1))
    clarify = {"原问": SUGGEST_QUESTION}
    c.check("当前步序列：压一帧「澄清」，再问做完第 1 步，对话理解做完第 2 步弹帧，主线原地址不动",
            step_sequence(run)[3:] == [
                (3, stack(in_loop, [frame("澄清", 0, clarify)])),
                (4, stack(in_loop, [frame("澄清", 1, clarify, 结果=REPLY_CHOOSE_ONE)])),
                (5, stack(in_loop))], step_sequence(run)[3:])
    glossary_tail(c, run, loops=5)
    return c


# ───────────────────────── 检查组：对话理解标注集 ─────────────────────────
# 第五步第 6 节第一层：工具级评测。逐条调对话理解（回放），比对功能与内容，算逐句全对率与逐项功能准确率。

EVAL_FILE = TASK_DEFS_DIR / "eval" / "对话理解标注集.json"
EVAL_RECORDING = "task_defs/recordings/对话理解标注集.json"
EVAL_FLOOR = 0.8  # 准确率下限（2026-09-17 用户确认）
EVAL_STEP = {"阶段": "确认", "循环": {"起": 1, "止": 3, "第几次": 1}, "步骤": 1}


def load_eval_cases() -> list:
    import json as json_module

    return json_module.loads(EVAL_FILE.read_text(encoding="utf-8"))["条目"]


def understand_case(case: dict, call_model):
    """跑一条标注：按条目拼好任务数据与一问一答的事件，直接调对话理解工具（不启动任务）。

    返回（工具调用对象, 状态记录）：工具调用的返回值是调用记录，变更组是它要写的东西；状态记录是 [(状态, 说明), …]。
    """
    import functools as functools_module
    import types as types_module

    from tod_kernel.tools import system_prompt_for, understand

    task_def = glossary_def()
    data = {**task_def.SLOTS, **case["数据"], "回复": case["原话"], "上一问": case["上一问"]}
    events = ask_events(1, 1, "确认", "回复", case["系统问话"], case["原话"])
    made = ToolCall(tool=UNDERSTAND_TOOL, params={}, proposer="selector", basis=(4, "确认 › 第 4 步 理解使用者的回复", {}),
                    call_id=2, status=CallStatus.APPROVED)
    statuses = []

    def set_status(status, note):
        made.status = status
        statuses.append((status, note))

    ctx = kernel.ExecContext(call=made, data_view=types_module.MappingProxyType(data), step=dict(EVAL_STEP),
                             inbox=None, outbox=None, set_status=set_status)
    prompt = system_prompt_for(task_def, list(GLOSSARY_TOOLS) + [EXCEPTION_TOOL])
    understand(ctx, call_model, task_def=task_def, read_events=lambda: list(events), system_prompt=prompt)
    return made, statuses


def _value_matches(expected, actual) -> bool:
    if expected == "*":
        return actual not in (None, "", [], {})
    if isinstance(expected, dict) and set(expected) == {"含"}:
        return isinstance(actual, str) and all(word in actual for word in expected["含"])
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(_value_matches(expected[k], actual[k]) for k in expected)
    return expected == actual


def _content_matches(expected_act, actual_act, question) -> bool:
    from tod_kernel.tools import defer_target

    if expected_act["功能"] == "DEFER":
        target = defer_target(question, actual_act.get("内容"))
        return target == (expected_act.get("内容") or {}).get("槽位")
    expected, actual = expected_act.get("内容"), actual_act.get("内容")
    if expected == "*":
        return actual is not None
    if isinstance(actual, dict):
        actual = {key: value for key, value in actual.items() if key != "原问"}
    return _value_matches(expected, actual)


def score_case(case: dict, acts: list) -> tuple:
    """一条的比对：返回（逐句全对, 功能对上的项数, 分母, 不一致说明）。

    按期望逐项在实际里找一个没用过的同功能项；功能对上算一项功能正确，内容也对上才算这一项全对。
    分母取期望项数与实际项数的较大者，实际多出来的项因此也算错。
    """
    used, function_hits, full_hits, problems = set(), 0, 0, []
    for expected in case["期望"]:
        index = next((i for i, act in enumerate(acts) if i not in used and act.get("功能") == expected["功能"]), None)
        if index is None:
            problems.append(f"少了 {expected['功能']}")
            continue
        used.add(index)
        function_hits += 1
        if _content_matches(expected, acts[index], case["上一问"]):
            full_hits += 1
        else:
            problems.append(f"{expected['功能']} 的内容不对")
    extra = [act.get("功能") for i, act in enumerate(acts) if i not in used]
    if extra:
        problems.append(f"多了 {'、'.join(extra)}")
    denominator = max(len(case["期望"]), len(acts))
    whole = full_hits == len(case["期望"]) == len(acts)
    return whole, function_hits, denominator, problems


def understand_eval_checks() -> Checker:
    title = "第五步：对话理解标注集评测"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    cases = load_eval_cases()
    kinds = {}
    for case in cases:
        kinds[case["上一问"]["类型"]] = kinds.get(case["上一问"]["类型"], 0) + 1
    situations = {}
    for case in cases:
        situations[case["情形"]] = situations.get(case["情形"], 0) + 1
    seven = ("确认", "提修改意见", "给整段改写", "要换一份", "推迟并顺手改另一属性", "提问", "含糊")
    c.check(f"标注集三十到四十条，4.9 节七种情形各至少三句，四种上一问类型各至少五句，另有无关句（共 {len(cases)} 条）",
            30 <= len(cases) <= 40 and all(situations.get(name, 0) >= 3 for name in seven)
            and all(kinds.get(kind, 0) >= 5 for kind in ("建议", "求证", "选择", "请求")) and situations.get("无关", 0) >= 1,
            (kinds, situations))
    call_model, _ = glossary_call(recording=EVAL_RECORDING)
    rows, structure_bad = [], []
    for case in cases:
        made, statuses = understand_case(case, call_model)
        record = made.result if isinstance(made.result, dict) else {}
        acts = record.get("acts") or []
        if made.status != CallStatus.SUCCEEDED or record.get("problem") is not None:
            structure_bad.append((case["编号"], statuses[-1][1] if statuses else None, record.get("problem")))
        rows.append((case, acts, score_case(case, acts)))
    c.check("硬断言：每条都调通（回放命中录制）、模型输出都合 Schema（功能与槽位路径不越出候选、没有走没听懂的兜底）",
            not structure_bad, structure_bad)
    whole = sum(1 for _, _, score in rows if score[0])
    hits = sum(score[1] for _, _, score in rows)
    denominator = sum(score[2] for _, _, score in rows)
    sentence_rate = whole / len(rows) if rows else 0.0
    function_rate = hits / denominator if denominator else 0.0
    print(f"逐句全对率：{whole}/{len(rows)} = {sentence_rate:.3f}")
    print(f"逐项功能准确率：{hits}/{denominator} = {function_rate:.3f}（下限 {EVAL_FLOOR}）")
    mismatched = [(case, acts, score) for case, acts, score in rows if not score[0]]
    if mismatched:
        print("不一致清单：")
        for case, acts, score in mismatched:
            expected = "；".join(f"{e['功能']} {e.get('内容')}" for e in case["期望"])
            actual = "；".join(f"{a.get('功能')} {a.get('内容')}" for a in acts)
            print(f"  第 {case['编号']} 条（{case['上一问']['类型']}·{case['情形']}）原话「{case['原话']}」")
            print(f"    期望：{expected}")
            print(f"    实际：{actual}")
            print(f"    差在：{'；'.join(score[3])}")
    c.check(f"逐项功能准确率 {function_rate:.3f} 不低于下限 {EVAL_FLOOR}（逐句全对率 {sentence_rate:.3f} 只报告）",
            function_rate >= EVAL_FLOOR, function_rate)
    four_nine = {1: ["AFFIRM"], 4: ["DENY", "INFORM"], 7: ["AFFIRM", "INFORM"], 10: ["REQALTS"],
                 13: ["DEFER", "INFORM"], 16: ["REQUEST"], 19: ["CLARIFY"]}
    got = {number: sorted(a.get("功能") for a in acts) for case, acts, _ in rows
           for number in [case["编号"]] if number in four_nine}
    c.check("第五步 4.9 节七句（标注集第 1、4、7、10、13、16、19 条）回放得到表里的行为列表（按功能核对）",
            all(got.get(number) == sorted(functions) for number, functions in four_nine.items()), got)
    return c


# ───────────────────────── 检查组：对话理解的机器检查 ─────────────────────────
# 第五步第 6 节第三层：不调模型、不跑任务，手写输入把候选、清单、六条规范化与必备槽位逐条验出来。

FAKE_SLOTS = {
    "标题": {"说明": "用例标题", "类型": "文本"},
    "优先级": {"说明": "高中低", "类型": "枚举", "取值": ["高", "中", "低"]},
    "步骤": {"说明": "主流程步骤", "类型": "列表", "项": {"动作": {"说明": "谁做什么", "类型": "文本"},
                                                  "序号": {"说明": "第几步", "类型": "数字"}}},
    "标签": {"说明": "自由标签", "类型": "列表"},
    "附加": {"说明": "其他属性", "类型": "对象"},
    "备注": {"说明": "系统写的备注", "类型": "文本", "使用者可写": False},
    "回复": {"说明": "原话", "类型": "文本"},
    "上一问": {"说明": "上一问", "类型": "对象"},
    "修改意见": {"说明": "改动要求", "类型": "文本", "使用者可写": False},
}
FAKE_DATA = {"步骤": [{"动作": "登录", "序号": 1}, {"动作": "下单", "序号": 2}], "标签": ["甲"], "附加": {"来源": "访谈"}}


def _raises_definition_error(task_def, step) -> bool:
    from tod_kernel.kernel import DefinitionError

    try:
        task_def.select_call(dict(task_def.SLOTS), step)
    except DefinitionError:
        return True
    return False


def _stub_caller(text: str):
    """只回一段固定文字的模型调用件，机器检查组用它验对话理解的代码路径，不碰录制文件。"""
    def call(request):
        return llm.Reply(text=text, record={"mode": "桩", "model": "桩", "system_prompt_hash": "0" * 64,
                                            "user_content": request.user, "shape": request.shape, "response": text,
                                            "request_hash": "0" * 64, "elapsed_ms": 0})
    return call


def _understand_once(task_def, call_model, data, step):
    """直接调一次对话理解（不启动任务），返回（工具调用, 状态记录）。"""
    import types as types_module

    from tod_kernel.tools import understand

    made = ToolCall(tool=UNDERSTAND_TOOL, params={}, proposer="selector", basis=(0, "", {}), call_id=1,
                    status=CallStatus.APPROVED)
    statuses = []

    def set_status(status, note):
        made.status = status
        statuses.append((status, note))

    ctx = kernel.ExecContext(call=made, data_view=types_module.MappingProxyType(data), step=step,
                             inbox=None, outbox=None, set_status=set_status)
    understand(ctx, call_model, task_def=task_def, read_events=lambda: [], system_prompt="")
    return made, statuses


def _ask_once(task_def, data, *registrations):
    """直接调一次念草稿那一问（不启动任务）：事件流里依次是给定的几条登记上一问的数据变更，回答恒为「好」。

    返回（问题里的话, 这次登记的上一问）。
    """
    import types as types_module

    from tod_kernel.tools import ask

    params = {"target": {"slot": "回复", "path": []}, "hint": {"term": GLOSSARY_TERM_ONE, "draft": data.get("释义草稿")},
              "type": "建议", "about": {"slot": "释义草稿", "path": []}, "adopt_to": "确认释义"}
    made = ToolCall(tool="ask", params=params, proposer="selector", basis=(0, "", {}), call_id=2,
                    status=CallStatus.APPROVED)
    sent = []

    class Outbox:
        def put(self, message):
            message = dataclasses.replace(message, seq=1)
            sent.append(message)
            return message

    class Inbox:
        def take(self, match, block, waiter=None):
            return kernel.Message(kind="answer", sender="user", recipient=2, in_reply_to=1, content="好", seq=2)

    def set_status(status, note):
        made.status = status

    registered = [kernel.Event(seq=seq, ts=0.0, task_id="T-reask", call_id=seq, kind="state", source="update",
                               name=DATA_CHANGED, payload={"slot": "上一问", "old": None, "new": question, "source": seq})
                  for seq, question in enumerate(registrations, start=1)]
    view = {**task_def.SLOTS, **data}
    ctx = kernel.ExecContext(call=made, data_view=types_module.MappingProxyType(view), step=None,
                             inbox=Inbox(), outbox=Outbox(), set_status=set_status)
    ask(ctx, task_def, read_events=lambda: list(registered))
    question = next((change.new for change in made.changes or [] if change.slot == "上一问"), None)
    return (sent[0].content["utterance"] if sent else None), question


def understand_machine_checks() -> Checker:
    import json as json_module
    import shutil
    import tempfile
    import types as types_module

    from tod_kernel import tools as tools_module
    from tod_kernel.context import candidate_functions, writable_paths

    title = "第五步：对话理解的机器检查"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    active = ["INFORM", "REQUEST", "DEFER", "OTHER", "CLARIFY"]
    expected = {"建议": ["AFFIRM", "DENY", "REQALTS"], "求证": ["AFFIRM", "DENY"], "选择": ["AFFIRM", "DENY"], "请求": ["DENY"]}
    for kind, response in expected.items():
        got = candidate_functions({"类型": kind})
        c.check(f"候选功能集：上一问是{kind}时是 {'、'.join(response + active)}（配对表的回应类加四个主动类加 CLARIFY）",
                got == response + active and tools_module.PAIRING[kind] == response, got)
    c.check("候选功能集：上一问为空时没有回应类，只有四个主动类与 CLARIFY", candidate_functions(None) == active,
            candidate_functions(None))

    rows = writable_paths(FAKE_SLOTS, None, FAKE_DATA)
    want = [
        {"槽位": "标题", "路径": []}, {"槽位": "优先级", "路径": []},
        {"槽位": "步骤", "路径": ["+"]},
        {"槽位": "步骤", "路径": [0]}, {"槽位": "步骤", "路径": [0, "动作"]}, {"槽位": "步骤", "路径": [0, "序号"]},
        {"槽位": "步骤", "路径": [1]}, {"槽位": "步骤", "路径": [1, "动作"]}, {"槽位": "步骤", "路径": [1, "序号"]},
        {"槽位": "步骤", "路径": ["+", "动作"]}, {"槽位": "步骤", "路径": ["+", "序号"]},
        {"槽位": "标签", "路径": ["+"]}, {"槽位": "标签", "路径": [0]},
        {"槽位": "附加", "路径": ["来源"]},
    ]
    c.check(f"可写路径清单：含列表与项字段的假槽位表算出 {len(want)} 行——标量各一行、列表不给整表替换行、"
            "每个现有项与项字段各一行、末尾新增与它的字段各一行、对象按当前值的键各一行；三个必备槽位与使用者不可写的槽位不进",
            rows == want, rows)
    suggest_rows = writable_paths(FAKE_SLOTS, {"类型": "建议"}, FAKE_DATA)
    c.check("可写路径清单：上一问是建议类时，使用者不可写的「修改意见」也开放（加在末尾），其余不变",
            suggest_rows == want + [{"槽位": "修改意见", "路径": []}], suggest_rows[len(want):])
    c.check("可写路径清单：对象槽位当前值为空时没有行，列表为空时只有末尾新增那几行",
            writable_paths(FAKE_SLOTS, None, {}) == [
                {"槽位": "标题", "路径": []}, {"槽位": "优先级", "路径": []}, {"槽位": "步骤", "路径": ["+"]},
                {"槽位": "步骤", "路径": ["+", "动作"]}, {"槽位": "步骤", "路径": ["+", "序号"]},
                {"槽位": "标签", "路径": ["+"]}], writable_paths(FAKE_SLOTS, None, {}))

    question = {"类型": "建议", "槽位": "标题", "路径": [], "选项": None, "采纳到": None}
    candidates = candidate_functions(question)
    paths = writable_paths(FAKE_SLOTS, question, FAKE_DATA)
    schema = tools_module.understand_schema(candidates, paths, question)
    variants = schema["properties"]["行为"]["items"]["anyOf"]
    enum = [name for variant in variants for name in variant["properties"]["功能"]["enum"]]
    write_slots = [variant["properties"]["内容"] for variant in variants if "INFORM" in variant["properties"]["功能"]["enum"]]
    c.check("输出 Schema 现算：功能枚举恰是候选功能集，每个功能一支；INFORM 那支按槽位配对路径枚举",
            sorted(enum) == sorted(candidates) and write_slots
            and [branch["properties"]["槽位"]["enum"] for branch in write_slots[0]["anyOf"]]
            == [["标题"], ["优先级"], ["步骤"], ["标签"], ["附加"], ["修改意见"]], enum)

    def described(node) -> bool:
        if isinstance(node, dict):
            return "description" in node or any(described(value) for value in node.values())
        return isinstance(node, list) and any(described(value) for value in node)
    choice_schema = tools_module.understand_schema(candidate_functions({"类型": "选择"}), paths, {"类型": "选择"})
    c.check("输出 Schema 只留裸结构：建议类与选择类两份 Schema 里任何一层都没有说明文字（description）",
            not described(schema) and not described(choice_schema), None)

    from tod_kernel.context import understand_step_text, writable_places_text
    from tod_kernel.prompt_pack import PROMPTS_DIR, PromptPackError
    from tod_kernel.prompt_pack import load as load_prompt_pack

    glossary = glossary_def()
    glossary_meta = glossary.DEFINITION["槽位"]
    prompt = prompt_pack_of(UNDERSTAND_TOOL)
    cases = [("建议", SUGGEST_QUESTION, STEP_TEXT_SUGGEST),
             ("求证", {"类型": "求证", "槽位": "术语", "路径": [], "选项": ["需求评审"], "采纳到": None}, STEP_TEXT_CHECK),
             ("选择", {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": FALLBACK_OPTIONS, "采纳到": "确认释义",
                     "原问": SUGGEST_QUESTION}, STEP_TEXT_CHOICE),
             ("请求", {"类型": "请求", "槽位": "术语", "路径": [], "选项": None, "采纳到": None}, STEP_TEXT_REQUEST),
             ("上一问为空", None, STEP_TEXT_NONE)]
    for kind, asked_question, want in cases:
        got = understand_step_text(prompt, asked_question, writable_paths(glossary_meta, asked_question, glossary.SLOTS),
                                   glossary_meta, glossary.SLOTS)
        c.check(f"对话理解的本步段（{kind}）逐字等于定稿写法，不出现功能标识与代码名",
                got == want and not re.search(r"[A-Z]{4,}|候选功能集|可写路径清单|\[\]", got), got)
    list_meta = {"材料清单": {"说明": "清单", "类型": "列表", "项": {"文件名": {"说明": "名", "类型": "文本"}}},
                 "备注": {"说明": "备注", "类型": "文本"}}
    places = [writable_places_text(prompt, writable_paths(list_meta, None, {"材料清单": items}), list_meta,
                                   {"材料清单": items}) for items in ([], [{"文件名": "a"}], [{"文件名": "a"}, {"文件名": "b"}])]
    c.check("可写位置只列槽位名，列表槽位说明可以新增一项、或改第几项（没有项、一项、多项三种写法）",
            places == ["材料清单（可以新增一项）、备注", "材料清单（可以新增一项，或改第 1 项）、备注",
                       "材料清单（可以新增一项，或改第 1 到第 2 项）、备注"], places)

    import shutil as shutil_module
    import tempfile as tempfile_module
    folder = Path(tempfile_module.mkdtemp(prefix="tod-prompts-"))
    try:
        raw_pack = json_module.loads((PROMPTS_DIR / "答疑.json").read_text(encoding="utf-8"))
        raw_pack["本步"]["解释"] += "（上一稿：{上一稿}）"
        (folder / "答疑.json").write_text(json_module.dumps(raw_pack, ensure_ascii=False), encoding="utf-8")
        provides, has_shape = tools_module.MODEL_TOOLS["答疑"]
        try:
            load_prompt_pack("答疑", provides, has_shape, folder)
            error = None
        except PromptPackError as exc:
            error = exc
        c.check("提示词包的占位符核对：答疑的本步模板多用一个代码提供不了的 {上一稿}，加载就报错，位置写到「本步.解释」",
                error is not None and error.where == "本步.解释" and "{上一稿}" in error.reason, repr(error))
        del raw_pack["本步"]["解释"]
        (folder / "答疑.json").write_text(json_module.dumps(raw_pack, ensure_ascii=False), encoding="utf-8")
        try:
            load_prompt_pack("答疑", provides, has_shape, folder)
            error = None
        except PromptPackError as exc:
            error = exc
        c.check("提示词包的占位符核对：答疑的包缺了代码要用的情形「解释」，加载就报错",
                error is not None and error.where == "本步" and "解释" in error.reason, repr(error))
    finally:
        shutil_module.rmtree(folder, ignore_errors=True)
    c.check("三个调模型的工具的提示词包都能加载，固定指令进系统提示的工具目录",
            all(prompt_pack_of(name).instruction for name in tools_module.MODEL_TOOLS)
            and all(prompt_pack_of(name).instruction in tools_module.tool_catalog([name]) for name in tools_module.MODEL_TOOLS),
            None)
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        jsonschema.Draft202012Validator.check_schema(schema)
        good = {"行为": [{"功能": "INFORM", "回应上一问": False, "内容": {"槽位": "步骤", "路径": [1, "动作"], "值": "付款"},
                          "把握": 0.9, "规范化修订": None}]}
        bad = {"行为": [{"功能": "INFORM", "回应上一问": False, "内容": {"槽位": "备注", "路径": [], "值": "x"},
                         "把握": 0.9, "规范化修订": None}]}
        validator = jsonschema.Draft202012Validator(schema)
        c.check("输出 Schema 是合法的 JSON Schema，清单内的写值通过、写不可写的「备注」被拦下",
                not list(validator.iter_errors(good)) and list(validator.iter_errors(bad)), None)

    def act(function, content=None, confidence=0.9, responds=True):
        return {"功能": function, "回应上一问": responds, "内容": content, "把握": confidence, "规范化修订": None}

    # 第 1 条：解析失败、为空、越出清单，都按没听懂处理。
    for text, reason in (("不是 JSON", "不是 JSON"), ('{"行为": []}', "为空"),
                         (json_module.dumps({"行为": [act("INFORM", {"槽位": "备注", "路径": [], "值": "x"})]}, ensure_ascii=False),
                          "写值越出可写路径清单")):
        acts, problem = tools_module.parse_acts(text, candidates, paths, question)
        c.check(f"规范化第 1 条（{reason}）：解析判不合格，兜底产出一条代码写的 CLARIFY，选项固定三项并带原问",
                acts is None and problem and tools_module.fallback_acts(question)[0]["内容"]["选项"] == ["确认这一稿", "修改这一稿", "先放一放"]
                and tools_module.fallback_acts(question)[0]["内容"]["原问"] == question, problem)
    normalized, dropped = tools_module.normalize_acts(
        [act("INFORM", {"槽位": "标题", "路径": [], "值": "甲"}), act("INFORM", {"槽位": "标题", "路径": [], "值": "乙"})],
        question, FAKE_SLOTS)
    c.check("规范化第 2 条：同一槽位同一路径两项写值，留后一项并记模型原值",
            [a["内容"]["值"] for a in normalized] == ["乙"] and normalized[0]["模型原值"] == [{"槽位": "标题", "路径": [], "值": "甲"}]
            and len(dropped) == 1, normalized)
    normalized, _ = tools_module.normalize_acts([act("DENY", {"问": "x"})], question, FAKE_SLOTS)
    c.check("规范化第 3 条：回应类项带了内容，内容置空并记原值",
            normalized[0]["内容"] is None and normalized[0]["模型原值"] == [{"问": "x"}], normalized)
    choice_question = {"类型": "选择", "槽位": "优先级", "路径": [], "选项": ["高", "低"], "采纳到": None}
    normalized, _ = tools_module.normalize_acts([act("AFFIRM", {"选项序号": 2})], choice_question, FAKE_SLOTS)
    c.check("规范化第 3 条的例外：上一问是选择类时 AFFIRM 带的选项序号保留", normalized[0]["内容"] == {"选项序号": 2}, normalized)
    normalized, dropped = tools_module.normalize_acts(
        [act("INFORM", {"槽位": "标题", "路径": [], "值": "甲"}), act("CLARIFY", {"问话": "哪个？", "选项": ["甲", "乙"]})],
        question, FAKE_SLOTS)
    c.check("规范化第 4 条：CLARIFY 与其他项同时出现，只留 CLARIFY",
            [a["功能"] for a in normalized] == ["CLARIFY"] and len(dropped) == 1, normalized)
    normalized, dropped = tools_module.normalize_acts([act("AFFIRM"), act("DENY"), act("OTHER")], question, FAKE_SLOTS)
    c.check("规范化第 5 条：回应类至多一项，留第一项", [a["功能"] for a in normalized] == ["AFFIRM", "OTHER"], normalized)
    normalized, _ = tools_module.normalize_acts(
        [act("AFFIRM"), act("INFORM", {"槽位": "优先级", "路径": [], "值": "紧急"})], question, FAKE_SLOTS)
    c.check("规范化第 6 条：枚举不在取值内的写值改为 CLARIFY「你说的『紧急』我记成『优先级』，对吗？」，"
            "选项「对」「不对，我重新说」，同一句里的 AFFIRM 保留（不再触发第 4 条）",
            [a["功能"] for a in normalized] == ["AFFIRM", "CLARIFY"]
            and normalized[1]["内容"] == {"问话": "你说的『紧急』我记成『优先级』，对吗？", "选项": ["对", "不对，我重新说"]},
            normalized)

    fallback_choice_question = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": ["确认这一稿", "修改这一稿", "先放一放"],
                                "采纳到": "确认释义", "原问": SUGGEST_QUESTION}
    normalized, dropped = tools_module.normalize_acts(
        [act("AFFIRM", {"选项序号": 3}), act("DEFER", {"槽位": "确认释义"}, responds=False)],
        fallback_choice_question, FAKE_SLOTS)
    c.check("规范化第 7 条：选择类 AFFIRM 选了「先放一放」又另有一项 DEFER 同一槽位，两项落到同一目标同一动作，"
            "留前一项并记重复项（2026-09-18 裁定问题三）",
            [a["功能"] for a in normalized] == ["AFFIRM"] and len(normalized[0].get("重复项", [])) == 1
            and [rule for _, rule in dropped] == ["第 7 条"], normalized)

    # 落数据里两条不经场景的路径：求证类 AFFIRM 写选项里的待写值；代码澄清后的选择类按序号落。
    task_def = glossary_def(dict(GLOSSARY_INPUT_TWO))
    glossary_slots = task_def.DEFINITION["槽位"]
    data = {**task_def.SLOTS, "释义草稿": "草稿", "回复": "对"}
    check_question = {"类型": "求证", "槽位": "术语", "路径": [], "选项": ["需求评审"], "采纳到": None}
    land = tools_module.land_acts([act("AFFIRM")], check_question, data,
                                  writable_paths(glossary_slots, check_question, data), glossary_slots, 0.6)
    c.check("落数据：上一问是求证时 AFFIRM 把选项里的待写值写进上一问的槽位（2026-09-18 裁定问题五）",
            land.work.get("术语") == "需求评审", land.work)
    fallback_choice = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": list(tools_module.FALLBACK_OPTIONS),
                       "采纳到": "确认释义", "原问": SUGGEST_QUESTION}
    outcomes = {}
    for number in (1, 2, 3):
        land = tools_module.land_acts([act("AFFIRM", {"选项序号": number})], fallback_choice, data,
                                      writable_paths(glossary_slots, fallback_choice, data), glossary_slots, 0.6)
        outcomes[number] = (land.work.get("确认释义"), land.routes)
    c.check("落数据：代码澄清的固定三项按序号落——1 把草稿采纳进确认释义，2 不写，3 按 DEFER 路由推迟确认释义"
            "（在插入段里由对话理解换成帧替换，2026-09-18 裁定问题六）",
            outcomes == {1: ("草稿", []), 2: (None, []),
                         3: (None, [{"功能": "DEFER", "输入": {"槽位": "确认释义"}, "替换": True}])}, outcomes)
    free_choice = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": ["甲", "乙"], "采纳到": "确认释义"}
    chosen = act("AFFIRM", {"选项序号": 1})
    land = tools_module.land_acts([chosen], free_choice, data,
                                  writable_paths(glossary_slots, free_choice, data), glossary_slots, 0.6)
    c.check("落数据：自由文字选项的选择类上一问，所选位置不可写时不写并记「选择项不可写，未落」",
            not land.work and chosen.get("落成") == "选择项不可写，未落", chosen.get("落成"))
    modes = tools_module.route_modes_of(task_def)
    c.check("路由表并进了任务定义：REQUEST→答疑、CLARIFY→澄清、DEFER→推迟、INFORM（把握低于阈值）→求证",
            modes == {"REQUEST": "答疑", "CLARIFY": "澄清", "DEFER": "推迟", "INFORM": "求证"}, modes)
    mixed = [act("AFFIRM"), act("REQUEST", {"问": "产出"}, responds=False),
             act("CLARIFY", {"问话": "哪个？", "选项": ["甲", "乙"]}, responds=False),
             act("DEFER", None), act("INFORM", {"槽位": "术语", "路径": [], "值": "需求评审"}, confidence=0.4),
             act("INFORM", {"槽位": "原文片段", "路径": [], "值": "片段"}), act("OTHER", responds=False)]
    land = tools_module.land_acts(mixed, SUGGEST_QUESTION, data, writable_paths(glossary_slots, SUGGEST_QUESTION, data),
                                  glossary_slots, 0.6, modes)
    c.check("路由：AFFIRM 与把握够的写值直接落，OTHER 不落；REQUEST、CLARIFY（模型的选项弃用，带原问）、DEFER（目标取采纳到）、"
            "把握不够的写值按话里的顺序列进待路由的行为，落成写明去向",
            land.work == {"确认释义": "草稿", "原文片段": "片段"}
            and land.routes == [{"功能": "REQUEST", "输入": {"问": "产出"}},
                                {"功能": "CLARIFY", "输入": {"原问": SUGGEST_QUESTION}},
                                {"功能": "DEFER", "输入": {"槽位": "确认释义"}},
                                {"功能": "INFORM", "输入": {"槽位": "术语", "路径": [], "值": "需求评审"}}]
            and [a.get("落成") for a in mixed][1:4] == ["REQUEST → 路由到模式『答疑』", "CLARIFY → 路由到模式『澄清』",
                                                       "DEFER → 路由到模式『推迟』"]
            and mixed[4]["落成"].endswith("INFORM → 路由到模式『求证』") and mixed[6]["落成"] == "不落，记进调用记录",
            (land.work, land.routes))

    normalized, dropped = tools_module.normalize_acts([act("DEFER", {"槽位": "确认释义"}, responds=False)],
                                                      fallback_choice, glossary_slots)
    c.check("澄清再问里使用者说「先放一放」、模型给了 DEFER 原问的推迟目标：规范化成 AFFIRM 选项序号 3，并记模型原值"
            "（2026-09-18 增补裁定第 9 条）",
            [(a["功能"], a["内容"]) for a in normalized] == [("AFFIRM", {"选项序号": 3})]
            and normalized[0]["模型原值"] == [{"功能": "DEFER", "内容": {"槽位": "确认释义"}}] and not dropped, normalized)
    call_model_stub = _stub_caller('{"行为": [{"功能": "DEFER", "回应上一问": true, "内容": {"槽位": "确认释义"}, "把握": 0.9, "规范化修订": null}]}')
    made, _ = _understand_once(task_def, call_model_stub, {**data, "回复": "先放一放。", "上一问": fallback_choice},
                               stack(step_at("确认", 2, loop=(1, 3, 1)), [frame("澄清", 1, {"原问": SUGGEST_QUESTION})]))
    c.check("插入段里同一句「先放一放。」：走帧替换（换成推迟帧），不是本帧再问",
            made.result.get("replace_frame") == {"功能": "DEFER", "输入": {"槽位": "确认释义"}}
            and not made.result.get("reask_in_frame"), (made.result.get("replace_frame"), made.result.get("reask_in_frame")))

    # 地址栈：压帧、选帧里的步骤、弹帧，只由任务定义认识（第五步 4.12 节）。
    def a_call(number, result=None, hit=None):
        made = ToolCall(tool="无所谓", params={}, proposer="selector", basis=(number, "无所谓", hit or {}))
        made.status, made.result = CallStatus.SUCCEEDED, result
        return made

    in_loop = step_at("确认", 2, loop=(1, 3, 1))
    before = stack(step_at("确认", 1, loop=(1, 3, 1)))
    two_routes = {"routes": [{"功能": "REQUEST", "输入": {"问": "产出"}}, {"功能": "DEFER", "输入": {"槽位": "确认释义"}}]}
    pushed = task_def.record_step(before, a_call(4, two_routes))
    c.check("压帧：一句话里两项要路由，按话里的顺序执行，所以倒着压——栈顶（列表末尾）是第一项「答疑」，下面排着「推迟」",
            pushed == stack(in_loop, [frame("推迟", 0, {"槽位": "确认释义"}), frame("答疑", 0, {"问": "产出"})]), pushed)
    chosen_call = task_def.select_call({**data, "回复": None}, pushed)
    answer_no = pattern_number(task_def, "答疑", 1)
    c.check("选帧：插入段不为空时从栈顶那一帧选，依据说明带主线阶段名与「插入段 · 模式 第 n 步」，命中值记下是哪一层",
            chosen_call[0] == "答疑" and chosen_call[1] == {"question": "产出"} and chosen_call[2][0] == answer_no
            and chosen_call[2][1] == "确认 › 插入段 · 答疑 第 1 步 用一两句白话解释使用者问的内容"
            and chosen_call[2][2]["插入段"] == {"层": 1, "模式": "答疑", "步骤": 1, "共": 2}, chosen_call)
    hit = {"插入段": {"层": 1}}
    after_answer = task_def.record_step(pushed, a_call(answer_no, {"输出": "指这个活动交出的东西。"}, hit))
    tell_call = task_def.select_call({**data, "回复": None}, after_answer)
    after_tell = task_def.record_step(after_answer, a_call(pattern_number(task_def, "答疑", 2), "指这个活动交出的东西。", hit))
    c.check("帧内往下走：答疑做完第 1 步，返回值里的「输出」记进帧的结果；告知的参数用「上一步结果」取到它；告知做完弹出栈顶，下面的「推迟」成了栈顶",
            after_answer["插入"][-1] == frame("答疑", 1, {"问": "产出"}, 结果="指这个活动交出的东西。")
            and tell_call[0] == "告知" and tell_call[1] == {"text": "指这个活动交出的东西。"}
            and after_tell == stack(in_loop, [frame("推迟", 0, {"槽位": "确认释义"})]), (tell_call, after_tell))
    defer_tell = task_def.select_call({**data, "回复": None}, after_tell)
    c.check("推迟模式先告知再标记（2026-09-18 裁定问题一）：句式用输入填空",
            defer_tell[0] == "告知" and defer_tell[1] == {"text": "『确认释义』先放着，回头再问。"}, defer_tell)

    clarify_frame = stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION})])
    understand_no = pattern_number(task_def, "澄清", 2)
    reasked = task_def.record_step(clarify_frame, a_call(understand_no, {"reask_in_frame": True,
                                                                          "reask_original": SUGGEST_QUESTION},
                                                         {"插入段": {"层": 0}}))
    reasked_twice = task_def.record_step(stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION}, 再问=1)]),
                                         a_call(understand_no, {"reask_in_frame": True, "reask_original": SUGGEST_QUESTION},
                                                {"插入段": {"层": 0}}))
    given_up = task_def.record_step(stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION}, 再问=2)]),
                                    a_call(understand_no, {"reask_in_frame": True, "reask_original": SUGGEST_QUESTION},
                                           {"插入段": {"层": 0}}))
    c.check("本帧再问：插入段里的对话理解仍含糊，栈顶换成新的澄清帧（从第 1 步起）并记再问次数，不压新帧；"
            "同一帧再问到 2 次后第 3 次仍含糊就弹帧、不落，主线原地续接（2026-09-18 裁定问题五）",
            reasked == stack(in_loop, [frame("澄清", 0, {"原问": SUGGEST_QUESTION}, 再问=1)])
            and reasked_twice == stack(in_loop, [frame("澄清", 0, {"原问": SUGGEST_QUESTION}, 再问=2)])
            and given_up == stack(in_loop), (reasked, given_up))
    replaced = task_def.record_step(clarify_frame, a_call(understand_no, {
        "routes": [], "replace_frame": {"功能": "DEFER", "输入": {"槽位": "确认释义"}}}, {"插入段": {"层": 0}}))
    c.check("帧替换：澄清帧里选了「先放一放」，栈顶换成推迟帧（输入是原问的目标槽位），不嵌套（2026-09-18 裁定问题六）",
            replaced == stack(in_loop, [frame("推迟", 0, {"槽位": "确认释义"})]), replaced)
    c.check("地址栈的插入段不合法（模式不存在、步数越界）抛任务定义错误",
            all(_raises_definition_error(task_def, bad) for bad in (
                stack(in_loop, [frame("没有这个模式", 0, {})]), stack(in_loop, [frame("答疑", 9, {})]))), None)

    # 插入段里不嵌套路由：对话理解整句换成本帧再问；选了「先放一放」换成帧替换。
    call_model_stub = _stub_caller('{"行为": [{"功能": "REQUEST", "回应上一问": false, "内容": {"问": "产出"}, "把握": 0.9, "规范化修订": null}]}')
    made, _ = _understand_once(task_def, call_model_stub, {**data, "回复": "产出是什么", "上一问": fallback_choice},
                               stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION})]))
    c.check("插入段里的对话理解产出 REQUEST 时不压帧：行为列表换成一条代码澄清，返回值标本帧再问并带原问，待路由的行为为空",
            [a["功能"] for a in made.result["acts"]] == ["CLARIFY"] and made.result.get("reask_in_frame") is True
            and made.result.get("reask_original") == SUGGEST_QUESTION and made.result.get("routes") == [], made.result.get("acts"))
    call_model_stub = _stub_caller('{"行为": [{"功能": "AFFIRM", "回应上一问": true, "内容": {"选项序号": 3}, "把握": 0.9, "规范化修订": null}]}')
    made, _ = _understand_once(task_def, call_model_stub, {**data, "回复": "3", "上一问": fallback_choice},
                               stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION})]))
    c.check("插入段里选了「先放一放」：返回值给出帧替换（DEFER，推迟确认释义），不写任何槽位（只清空回复与上一问）",
            made.result.get("replace_frame") == {"功能": "DEFER", "输入": {"槽位": "确认释义"}}
            and {change.slot for change in made.changes} == {"回复", "上一问"}, made.result.get("replace_frame"))
    glossary_text = (TASK_DEFS_DIR / GLOSSARY_FILE).read_text(encoding="utf-8")
    c.check("退役：术语澄清定义里不再有「待处理」槽位与 {pending} 占位；工具模块里不再有待处理拼句",
            "待处理" not in glossary_text and "{pending}" not in glossary_text
            and not hasattr(tools_module, "pending_sentences") and not hasattr(tools_module, "PENDING_SLOT"), None)

    work = Path(tempfile.mkdtemp(prefix="tod-understand-"))
    try:
        for missing in ("回复", "上一问"):
            definition = json_module.loads((TASK_DEFS_DIR / GLOSSARY_FILE).read_text(encoding="utf-8"))
            del definition["槽位"][missing]
            broken = work / f"缺{missing}.json"
            broken.write_text(json_module.dumps(definition, ensure_ascii=False), encoding="utf-8")
            try:
                taskdef.load(broken)
                error = None
            except taskdef.LoadError as exc:
                error = exc
            c.check(f"必备槽位：术语澄清缺「{missing}」时加载报错，位置「槽位」，原因写明对话理解要这个槽位",
                    error is not None and error.where == "槽位" and f"「{missing}」" in error.reason and "对话理解" in error.reason,
                    repr(error))
        empty = work / "empty.json"
        empty.write_text("[]\n", encoding="utf-8")
        call_model, _ = glossary_call(recording=str(empty))
        made, statuses = understand_case(load_eval_cases()[0], call_model)
        c.check("模型不可达（回放时录制文件里没有这条请求）：对话理解记已失败，说明是模型调用那一句错误，不写任何东西",
                made.status == CallStatus.FAILED and statuses and "录制文件里没有这条请求" in statuses[-1][1]
                and not made.changes, statuses)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    glossary = glossary_def(dict(GLOSSARY_INPUT_ONE))
    last = {**SUGGEST_QUESTION, "念的值": "第一稿"}
    changed = _ask_once(glossary, {"释义草稿": "第二稿"}, last)
    same = _ask_once(glossary, {"释义草稿": "第一稿"}, last)
    c.check("询问再问同一个问题：念的值变了（改稿之后）照常全文念，登记的念的值是新值；值没变只说再问短句",
            changed[0] == confirm_utterance(GLOSSARY_TERM_ONE, "第二稿") and changed[1].get("念的值") == "第二稿"
            and same[0] == tools_module.REASK_LINE and same[1] == last, (changed, same))
    between = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": FALLBACK_OPTIONS, "采纳到": "确认释义",
               "原问": SUGGEST_QUESTION, "念的值": "第一稿"}
    after_choice = _ask_once(glossary, {"释义草稿": "第一稿"}, last, between)
    c.check("询问再问同一个问题：中间隔着一次选择类登记（澄清段到上限回主线），比对的是最近一次同类型同槽位同路径的登记，草稿没变仍说短句",
            after_choice[0] == tools_module.REASK_LINE, after_choice)
    work = Path(tempfile.mkdtemp(prefix="tod-reask-"))
    try:
        raw = json_module.loads((TASK_DEFS_DIR / GLOSSARY_FILE).read_text(encoding="utf-8"))
        raw["槽位"]["回复"]["再问短句"] = "还是刚才那一稿，确认吗？"
        custom = work / "glossary_reask.json"
        custom.write_text(json_module.dumps(raw, ensure_ascii=False), encoding="utf-8")
        reask = _ask_once(taskdef.load(custom, initial=dict(GLOSSARY_INPUT_ONE)), {"释义草稿": "第一稿"}, last)
        c.check("任务定义在写入目标槽位上写了「再问短句」，再问时说的就是这句",
                reask[0] == "还是刚才那一稿，确认吗？", reask)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c


# ───────────────────────── 检查组：上下文包 ─────────────────────────
# 不跑任务，手写一份任务数据与事件列表，把六种段与对话历史的三条规则逐条验出来。

CTX_TERM = "基线"
CTX_DRAFT_ONE = "第一稿：基线是冻结下来的条目集合。"
CTX_DRAFT_TWO = "第二稿：基线是某一轮评审通过时冻结的条目集合。"
CTX_FEEDBACK = "说清楚它是什么时候冻结的。"
CTX_REPLY = "再具体些。"
CTX_REPLY_TWO = "还要说明它的作用。"


def fake_event(seq, name, payload, call_id=None, kind=STATE, source=SOURCE_LOOP):
    return kernel.Event(seq=seq, ts=0.0, task_id="T-context", call_id=call_id,
                        kind=kind, source=source, name=name, payload=payload)


def ask_events(seq, call_id, stage, slot, question, answer) -> list:
    """一问一答在事件流里的样子：工具调用提出（带依据说明，阶段名从这里来）、发件箱的问题、收件箱的回答。"""
    note = f"{stage}{taskdef.STAGE_NAME_SEPARATOR}第 1 步 问一句"
    return [
        fake_event(seq, CALL_PROPOSED, {"tool": "ask", "params": {"target": {"slot": slot, "path": []}},
                                          "proposer": "selector", "basis": [1, note, {}]}, call_id),
        fake_event(seq + 1, MESSAGE_PUT, {"box": OUTBOX, "kind": "question", "sender": "tool.ask", "recipient": "user",
                                          "call_id": call_id, "in_reply_to": None,
                                          "content": {"utterance": question, "params": {"target": {"slot": slot, "path": []}}},
                                          "seq": call_id}, call_id),
        fake_event(seq + 2, MESSAGE_PUT, {"box": INBOX, "kind": "answer", "sender": "user", "recipient": call_id,
                                          "call_id": call_id, "in_reply_to": call_id,
                                          "content": answer, "seq": call_id}, call_id),
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
    # 当前步不在任务数据里，单独给：『确认』阶段第 1 到第 3 步这段循环的第 2 次，做完了第 2 步「理解使用者的回复」。
    ctx_step = step_at("确认", 2, loop=(1, 3, 2))
    # 三轮问答：第一轮在「写释义草稿」阶段问术语（回答至今原样留在槽位里），
    # 后两轮在「确认」阶段问回复（回答都已被对话理解清空），这样三条规则各有例子可验。
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
                pack.material("原文片段"), ContextPack.step("本步执行工具「生成术语释义」：改写。"),
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
                                         "做完了第 2 步『理解使用者的回复』"), progress.text)

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
            and ContextPack.step("本步执行工具「对话理解」：…").type == SEG_STEP
            and ContextPack.shape("JSON", "{}", api_note=True).source.startswith("JSON（同一份结构也作为接口参数"),
            ContextPack.shape("JSON", "{}", api_note=True).source)

    from tod_kernel.context import exchanges_of, utterances_of

    notice = fake_event(30, MESSAGE_PUT, {"box": OUTBOX, "kind": "notice", "sender": "tool.告知", "recipient": "user",
                                          "call_id": 9, "in_reply_to": None, "content": {"utterance": "「产出」指签字的清单。",
                                                                                         "params": {"text": "…"}},
                                          "seq": 9}, 9)
    pending = ask_events(31, 10, "确认", "回复", "还有要改的吗？", None)[:2]
    talk = ask_events(1, 1, "确认", "术语", "请给出要澄清的术语。", CTX_TERM) + [notice] + pending
    spoken = utterances_of(talk)
    c.check("对话历史按话轮存：系统的提问、系统的告知、使用者的回答各一条，没有回答的提问也照记，按事件序号排",
            [(u.speaker, u.kind, u.text, u.seq, u.stage, u.call_id) for u in spoken]
            == [("系统", "提问", "请给出要澄清的术语。", 2, "确认", 1), ("使用者", "回答", CTX_TERM, 3, "确认", 1),
                ("系统", "告知", "「产出」指签字的清单。", 30, "", 9), ("系统", "提问", "还有要改的吗？", 32, "确认", 10)],
            [(u.speaker, u.kind, u.text, u.seq, u.stage, u.call_id) for u in spoken])
    parts = exchanges_of(spoken)
    lone = exchanges_of([spoken[2]] + spoken[:2])
    c.check("交互轮次的划分：从一个提问到下一个提问之前为一段，中间的告知归前一段；第一个提问之前的告知自成一段；"
            "渲染仍是逐行「系统：」「使用者：」原文",
            [[u.kind for u in part.utterances] for part in parts] == [["提问", "回答", "告知"], ["提问"]]
            and [[u.kind for u in part.utterances] for part in lone] == [["告知"], ["提问", "回答"]]
            and parts[0].render() == f"系统：请给出要澄清的术语。\n使用者：{CTX_TERM}\n系统：「产出」指签字的清单。"
            and parts[1].answer is None and parts[1].question.text == "还有要改的吗？",
            [[u.kind for u in part.utterances] for part in parts])

    prompt = tools_system_prompt_for(task_def, list(GLOSSARY_TOOLS) + [EXCEPTION_TOOL])
    heads = re.findall(r"^【(.+?)】$", prompt, flags=re.MULTILINE)
    c.check("系统提示六段齐全，顺序是角色与任务、任务定义摘要、工具目录、上下文约定、领域规矩、通用输出规矩",
            heads == ["角色与任务", "任务定义摘要", "工具目录", "上下文约定", "领域规矩", "通用输出规矩"], heads)
    c.check("系统提示里有两个模型工具的固定指令（生成术语释义、对话理解，逐字取自各自的提示词包），也有任务定义的领域规矩",
            "你是术语解释员" in prompt and ("固定指令：" + prompt_pack_of(UNDERSTAND_TOOL).instruction) in prompt
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


# 十个场景：（标题, 跑它的函数）。控制台按这张表列菜单，编号 1 起，0 是全部。
# 一种机制留一个场景（2026-09-16 用户裁定）；第五步把术语澄清换成对话理解的六个场景，「模型不可达」场景退役，
# 它验的事改由对话理解机器检查组里一条断言验（2026-09-18 裁定问题九）。
SCENARIOS = [
    ("材料接入登记，正常（登记完再问）", intake_scenario_one),
    ("材料接入登记，目标写错报异常，使用者选被动终止", intake_exception_scenario),
    ("加载错误，三份坏文件", load_error_checks),
    ("术语澄清，确认", glossary_scenario_confirm),
    ("术语澄清，提修改意见", glossary_scenario_revise),
    ("术语澄清，给整段改写", glossary_scenario_rewrite),
    ("术语澄清，要换一份", glossary_scenario_alts),
    ("术语澄清，推迟并顺手改术语", glossary_scenario_defer),
    ("术语澄清，提问后再确认", glossary_scenario_ask),
    ("术语澄清，含糊后选择", glossary_scenario_vague),
]

# 不算场景的检查组：（标题, 跑它的函数）。控制台选 0 时与场景一起跑，范围与直接跑验证脚本相同。
CHECK_GROUPS = [
    ("观测台", observatory_checks),
    ("任务定义 JSON Schema", schema_checks),
    ("告知异常话的循环次数写法", utterance_round_clause_checks),
    ("当前步的记录、判据与显示", current_step_checks),
    ("更新组先核对再写入", update_group_checks),
    ("模型调用件的三种模式", llm_mode_checks),
    ("上下文包与对话历史的三条规则", context_pack_checks),
    ("控制台的问答打印", console_transcript_checks),
    ("对话理解标注集评测", understand_eval_checks),
    ("对话理解的机器检查", understand_machine_checks),
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
