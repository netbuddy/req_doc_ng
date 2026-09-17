"""验证脚本的共用部分：断言记账、宿主与使用者、跑场景、事件查询与各场景共用的验证目标。

整个验证脚本在仓根下运行 `python -m tod_kernel.verify`（包入口 verify/__main__.py）；场景与检查组按组分文件：
intake（材料接入登记与加载错误）、glossary（术语澄清）、eval（对话理解标注集评测），机器检查在子包 machine/，一组检查一个文件。


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
import threading
from dataclasses import dataclass, field
from pathlib import Path

from tod_kernel import kernel, taskdef
# 控制台是宿主一侧的东西：宿主循环、两个应答者与答案表的键都在那里，验证脚本与它共用同一套。
from tod_kernel.console import PresetAnswerer, host_loop, target_key
from tod_kernel.tools import EXCEPTION_TOOL, build_table
from tod_kernel.tools import set_at as tools_set_at
from tod_kernel.kernel import (CALL_PROPOSED, CALL_STATUS_CHANGED, CHECK_DONE_RESULT, CONTROL_RESULT, DATA_CHANGED,
                               STEP_CHANGED, EXECUTE_CALL, LOOP_STARTED, MAILBOX_CLOSED, MAILBOX_WAIT, MESSAGE_PUT,
                               MESSAGE_TAKEN, STATE, TASK_DEFINITION_ERROR, TASK_ENDED, TASK_STARTED,
                               TASK_STATUS_CHANGED, TRACE, TOOL_SOURCE_PREFIX, EXTERNAL_SENDERS, INBOX, OUTBOX,
                               SOURCE_LOOP, SOURCE_MAILBOX, SOURCE_UPDATE, STATE_EVENT_NAMES, CallStatus, EventStream,
                               Mailbox)
from tod_kernel.observe import (ConsolePrinter, FileWriter, MemoryCollector, call_history, is_external, read_events,
                                replay_data)


KERNEL_THREAD_PREFIX = "kernel-"
TASK_DEFS_DIR = Path(__file__).resolve().parent.parent / "task_defs"  # 任务定义数据文件


# ───────────────────────── 任务定义：经加载器从数据文件取 ─────────────────────────
# 每次调用都重新加载，得到新对象。材料接入登记的「目录」与出差申请单的预填都作为一次任务的初始输入给出。

SAMPLE_DIR_INPUT = {"目录": "样例材料"}


def intake_def(file="intake.json", name=None):
    return taskdef.load(TASK_DEFS_DIR / file, name=name, initial=dict(SAMPLE_DIR_INPUT))


RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "runs"  # 仓根下的 runs/，不入版本库
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
    # 任务定义加载器拆成两块后，当前步与对话模式那一半在 taskdef_step.py，一并查。
    loader_source = (Path(taskdef_module.__file__).read_text(encoding="utf-8")
                     + Path(taskdef_module.__file__).with_name("taskdef_step.py").read_text(encoding="utf-8"))
    stale = {name: text.count(word) for name, text in (("kernel.py", source), ("taskdef.py", loader_source))
             for word in ("游标", "cursor") if text.count(word)}
    c.check("内核与任务定义加载器里不再出现「游标」「cursor」（第四步 4.10 节废除了这个词与那个保留槽位）", not stale, stale)
    # 2026-09-17「行动」改名「工具调用」：本目录下的代码里不该再有旧名字。
    # observe.py 是唯一的例外：它要认出旧运行文件里的旧键 action_id，把它归一成 call_id。
    # 两个文件例外：observe.py 要认出旧运行文件里的旧键 action_id 好归一成 call_id；
    # 本文件是这条检查自己待的地方，旧名字作为被查的词写在这一段里，所以不查自己。
    renamed = {}
    # 工具与验证脚本拆成包之后，子目录里的代码也要查，所以递归找；跳过的仍是本条检查所在的文件。
    for path in sorted(Path(kernel.__file__).resolve().parent.rglob("*.py")):
        if path == Path(__file__).resolve():
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
