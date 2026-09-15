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

import copy as copy_module
import dataclasses
import hashlib

from tod_kernel import kernel, taskdef
from tod_kernel.tools import CURSOR_SLOT, EXCEPTION_OPTIONS, EXCEPTION_TOOL, build_table
from tod_kernel.tools import set_at as tools_set_at
from tod_kernel.kernel import (
    ACTION_PROPOSED,
    ACTION_STATUS_CHANGED,
    CHECK_DONE_RESULT,
    CONTROL_RESULT,
    DATA_CHANGED,
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
    ActionStatus,
    EventStream,
    KernelError,
    Mailbox,
    Message,
    TaskStatus,
)
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


def travel_def(name=None, initial=None):
    return taskdef.load(TASK_DEFS_DIR / "travel.json", name=name, initial=initial)


def travel_all_filled_def():
    return travel_def("出差申请单（三项预填）", {"目的地": "上海", "日期": "9 月 20 日", "事由": "客户拜访"})


def travel_date_filled_def():
    return travel_def("出差申请单（日期预填）", {"日期": "9 月 20 日"})


def intake_def(file="intake.json", name=None):
    return taskdef.load(TASK_DEFS_DIR / file, name=name, initial=dict(SAMPLE_DIR_INPUT))


def intake_swapped_def():
    return intake_def("intake_interleaved.json", "材料接入登记（规则二三互换）")
RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"  # 仓根下的 runs/，不入版本库
HOST_JOIN_SECONDS = 30  # 收内核线程时最多等多久，只防验证脚本在缺陷下挂死，不参与邮箱语义


# ───────────────────────── 断言记账 ─────────────────────────


class Checker:
    def __init__(self, title: str):
        self.title = title
        self.results: list[tuple[str, bool]] = []

    def check(self, description: str, condition, detail: str = "") -> None:
        ok = bool(condition)
        self.results.append((description, ok))
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
    tools_spec: dict = field(default_factory=dict)  # 本场景工具表：工具名 → 参数名清单
    writable: dict = field(default_factory=dict)  # 本场景工具表：工具名 → 可写槽位（没有声明时为空）


def target_key(target: dict) -> tuple:
    """答案表的键：（槽位名, 路径元组）。宿主按写入目标查答案，不读问题里的话。"""
    return (target["slot"], tuple(target.get("path", [])))


def slot_answers(by_slot: dict) -> dict:
    """出差申请单的答案表：写入目标都是整个槽位（空路径）。"""
    return {(slot, ()): value for slot, value in by_slot.items()}


def get_at(value, path):
    """按路径取值，路径为空时就是 value 本身。"""
    for step in path:
        value = value[step]
    return value


# 第三步：「游标」是加载器加进槽位表的保留槽位。既有断言里「工具写了什么」「某行动有几条数据变更」
# 与终态数据比对只看业务槽位（2026-09-14 用户裁定）；游标的终态值另用一条断言核对。
def is_business_slot(slot) -> bool:
    return slot != CURSOR_SLOT


def business_data(data) -> dict:
    return {slot: value for slot, value in data.items() if is_business_slot(slot)}


def business_changes(events, action_id):
    return [e for e in of_action(events, DATA_CHANGED, action_id) if is_business_slot(e.payload["slot"])]


def cursor(stage, done, rounds) -> dict:
    """游标的值：阶段、已完成步骤（全局步骤号或 None）、已完成轮数（步骤组里的轮数或 None）。"""
    return {"阶段": stage, "已完成步骤": done, "已完成轮数": rounds}


def check_cursor_final(c, run, expected: dict) -> None:
    """第三步：游标的终态值。"""
    actual = run.task.data.get(CURSOR_SLOT) if run.task is not None else None
    c.check(f"第三步：游标的终态值是 {expected}", actual == expected, actual)


# 话语的预期句：按写入目标逐条写死的完整句子，不调用话语生成、也不读模板，用来逐字比对问题里的话。
EXPECTED_UTTERANCES = {
    ("目的地", ()): "请提供出差目的地。",
    ("日期", ()): "请提供出差日期。",
    ("事由", ()): "请提供出差事由。",
    ("材料清单", (0, "是否纳入")): "请确认是否把文件 a.docx 纳入项目。",
    ("材料清单", (1, "是否纳入")): "请确认是否把文件 b.pdf 纳入项目。",
    ("材料清单", (2, "是否纳入")): "请确认是否把文件 c.xlsx 纳入项目。",
}

# 零差异断言：第三步起 kernel.py、tools.py 允许授权范围内的改动（差异全文人工确认）；
# observe.py 第三步第九项只改命令行打印的一行，解除哈希锁（差异附进实施报告）。下面的模块必须与提交 01de6ab 逐字节相同。
KERNEL_SHA256_COMMIT = "01de6ab"
KERNEL_SHA256 = {
    "dialogue.py": "0a7a6509581ba96cf6da573362b4d2e4992a962a1287f8f3105ca12102297d7e",
}


def run_scenario(task_id: str, task_def, answers: dict, tool_names=("ask",), runs_dir=None, console=True,
                 exception_answers=None) -> Run:
    """跑一个场景。exception_answers：第三步新增，使用者对「告知异常」依次给的回答；用完后再来告知异常就关闭收件箱。"""
    run = Run(task_id=task_id, task_def=task_def, host_thread=threading.get_ident())
    exception_answers = list(exception_answers or [])

    # 宿主：事件流、订阅者、邮箱。
    stream = EventStream(task_id)
    collector = MemoryCollector()
    stream.subscribe(collector)
    if console:
        stream.subscribe(ConsolePrinter())
    writer = FileWriter(RUNS_DIR if runs_dir is None else runs_dir)
    stream.subscribe(writer)
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
    table = build_table(task_def, tool_names)
    run.tools_spec = {name: list(tool.param_names) for name, tool in table.items()}
    run.writable = {name: tool.writable_slots for name, tool in table.items()}
    try:
        thread = threading.Thread(target=kernel_main, name=KERNEL_THREAD_PREFIX + task_id)
        thread.start()
        run.kernel_thread = thread.ident
        while True:
            question = outbox.take(match=lambda m: m.kind == "question", block=True)
            if question is None:  # 发件箱已关闭：不会再有问题
                break
            if "target" not in question.content["params"]:  # 第三步：告知异常的问题没有写入目标，按回答表依次回答
                if exception_answers:
                    inbox.put(Message(
                        kind="answer", sender="user", recipient=question.action_id,
                        in_reply_to=question.seq, content=exception_answers.pop(0), action_id=question.action_id,
                    ))
                else:
                    inbox.close("host")
                continue
            key = target_key(question.content["params"]["target"])
            if key in answers:
                inbox.put(Message(
                    kind="answer", sender="user", recipient=question.action_id,
                    in_reply_to=question.seq, content=answers[key], action_id=question.action_id,
                ))
            else:
                inbox.close("host")
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
    if event.name in (DATA_CHANGED, TASK_STATUS_CHANGED):
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


def check_waiting_then_success(c: Checker, run: Run, action_id: int) -> None:
    """验证目标二：询问行动在拿到回答前处于等待中，回答到达后完成。"""
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
    changes = business_changes(events, action_id)  # 第三步：只看业务槽位
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
    """验证目标四：两条机械检查。"""
    source_path = Path(kernel.__file__)
    source = source_path.read_text(encoding="utf-8")
    hits = {word: source.count(word) for word in ("目的地", "日期", "事由")}
    c.check("目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零", sum(hits.values()) == 0, hits)
    c.check("目标四（附加）：内核文件里不出现工具名（字符串 \"ask\" 与「询问」）",
            not re.search(r"""["']ask["']""", source) and "询问" not in source)
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported |= {f"{module}.{alias.name}" for alias in node.names}
    forbidden = {name for name in imported if any(part in ("tools", "task_travel", "observe") for part in name.split("."))}
    c.check("目标四：内核模块的导入语句里没有工具表模块、任务模块和观测模块", not forbidden, sorted(imported))


# 第三步目标一：七个既有场景在第二步提交 01de6ab 前最后一次全量运行留下的运行文件。runs/ 不入版本库，
# 所以这条断言只在留有这些文件的机器上成立；文件缺失判失败，不跳过。
STEP_TWO_RUN_FILES = {
    "T-scenario-1": "T-scenario-1_20260914T165954567.jsonl",
    "T-scenario-2": "T-scenario-2_20260914T165954587.jsonl",
    "T-scenario-3": "T-scenario-3_20260914T165954590.jsonl",
    "T-scenario-4": "T-scenario-4_20260914T165954599.jsonl",
    "T-intake-1": "T-intake-1_20260914T165954608.jsonl",
    "T-intake-2": "T-intake-2_20260914T165954629.jsonl",
    "T-intake-3": "T-intake-3_20260914T165954649.jsonl",
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


# ───────────────────────── 四个场景 ─────────────────────────

FULL_ANSWERS = {"目的地": "上海", "日期": "9 月 20 日", "事由": "客户拜访"}


def travel_params(slot: str) -> dict:
    return {"target": {"slot": slot, "path": []}, "hint": {}}


def banner(text: str) -> None:
    print(f"\n{'═' * 12} {text} {'═' * 12}", flush=True)


def scenario_one() -> Checker:
    banner("场景一：正常流程")
    run = run_scenario("T-scenario-1", travel_def(), slot_answers(FULL_ANSWERS))
    c = Checker("场景一：正常流程")
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    events, task = run.events, run.task

    c.check("目标一：任务状态是已完成", task.status == TaskStatus.DONE, task.status)
    proposed = named(events, ACTION_PROPOSED)
    c.check("目标一：恰好三个「行动提出」", len(proposed) == 3, len(proposed))
    c.check("目标一：三个行动的工具名都是 ask", [e.payload["tool"] for e in proposed] == ["ask"] * 3)
    c.check("目标一：参数依次是目的地、日期、事由",
            [e.payload["params"] for e in proposed] == [travel_params("目的地"), travel_params("日期"), travel_params("事由")])
    done = [e for e in named(events, TASK_STATUS_CHANGED) if e.payload["new_status"] == TaskStatus.DONE]
    c.check("目标一：「已完成」状态变化恰好一次", len(done) == 1)
    ended = named(events, TASK_ENDED)
    c.check("目标一：「任务结束」恰好一次，原因是完成条件成立",
            len(ended) == 1 and ended[0].payload["reason"] == "完成条件成立")
    c.check("目标一：终态数据是三条回答", business_data(task.data) == FULL_ANSWERS, task.data)  # 第三步：只比业务槽位
    check_cursor_final(c, run, cursor("收集", 3, None))
    check_matches_step_two(c, run)

    for action in task.actions.values():
        check_waiting_then_success(c, run, action.action_id)
    check_explainable(c, run)
    check_kernel_is_task_agnostic(c)
    c.check("内核已删去 record 函数，第 5 步只剩状态更新", not hasattr(kernel, "record"))
    check_integrity(c, run)
    check_trace_shape(c, run, loops=3)
    check_sources_and_senders(c, run, closed_before_failure_of=None)
    check_run_file(c, run)
    return c


def scenario_two() -> Checker:
    banner("场景二：初始即完成")
    run = run_scenario("T-scenario-2", travel_all_filled_def(), {})
    c = Checker("场景二：初始即完成")
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    events = run.events
    c.check("事件流里没有「行动提出」", not named(events, ACTION_PROPOSED))
    c.check("任务状态是已完成", run.task.status == TaskStatus.DONE, run.task.status)
    c.check("最后一个状态事件是「任务结束」", events and events[-1].name == TASK_ENDED, events[-1].name if events else None)
    c.check("行动表为空，只有结束记录", run.task.actions == {} and run.task.end_record is not None)
    check_cursor_final(c, run, cursor("收集", None, None))
    check_matches_step_two(c, run)
    check_integrity(c, run)
    check_trace_shape(c, run, loops=0)
    check_sources_and_senders(c, run, closed_before_failure_of=None)
    check_run_file(c, run)
    return c


def scenario_three() -> Checker:
    banner("场景三：回答缺失")
    answers = {"目的地": "上海", "日期": "9 月 20 日"}
    run = run_scenario("T-scenario-3", travel_def(), slot_answers(answers))
    c = Checker("场景三：回答缺失")
    print("── 断言 ──")
    c.check("内核线程以内核错误结束", isinstance(run.error, KernelError), repr(run.error))
    if isinstance(run.error, KernelError):
        c.check("内核错误携带的是行动 3", run.error.action is not None and run.error.action.action_id == 3)
    events = run.events
    for action_id in (1, 2):
        c.check(f"行动 {action_id} 恰有一条「行动提出」事件", len(of_action(events, ACTION_PROPOSED, action_id)) == 1)
        check_waiting_then_success(c, run, action_id)
    c.check("行动 3 恰有一条「行动提出」事件", len(of_action(events, ACTION_PROPOSED, 3)) == 1)
    history = action_history(events, 3)
    c.check("行动 3 的状态序列是 已提出、已获准、等待中、已失败",
            status_values(history) == [ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.WAITING, ActionStatus.FAILED],
            [s.value for s in status_values(history)])
    c.check("行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」",
            history and history[-1].payload["new_status"] == ActionStatus.FAILED
            and history[-1].payload["note"] == "没有可用的回答")
    failed_seq = history[-1].seq if history else 0
    c.check("行动 3 在「已失败」之前发出过问题（发件箱放入）",
            [e for e in of_action(events, MESSAGE_PUT, 3) if e.payload["box"] == OUTBOX and e.seq < failed_seq])
    c.check("行动 3 在「已失败」之前没有收件箱的「消息取出」",
            not [e for e in of_action(events, MESSAGE_TAKEN, 3) if e.payload["box"] == INBOX and e.seq < failed_seq])
    c.check("行动 3 没有数据变更", not business_changes(events, 3))  # 第三步：只看业务槽位
    check_cursor_final(c, run, cursor("收集", 2, None))  # 失败的行动 3 不写游标，停在行动 2 之后
    check_matches_step_two(c, run)
    c.check("任务没有结束：没有「任务结束」事件，任务状态仍是执行中",
            not named(events, TASK_ENDED) and run.task is not None and run.task.status == TaskStatus.RUNNING)
    c.check("失败的行动在行动表里：行动表的编号是 1、2、3，行动 3 的状态是已失败",
            run.task is not None and list(run.task.actions) == [1, 2, 3]
            and run.task.actions[3].status == ActionStatus.FAILED)
    check_integrity(c, run)
    check_trace_shape(c, run, loops=3)
    check_sources_and_senders(c, run, closed_before_failure_of=3)
    check_run_file(c, run)
    return c


def scenario_four() -> Checker:
    banner("场景四：部分预填")
    answers = {"目的地": "上海", "事由": "客户拜访"}
    run = run_scenario("T-scenario-4", travel_date_filled_def(), slot_answers(answers))
    c = Checker("场景四：部分预填")
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    events = run.events
    proposed = named(events, ACTION_PROPOSED)
    c.check("恰好两个「行动提出」", len(proposed) == 2, len(proposed))
    c.check("参数依次是目的地、事由",
            [e.payload["params"] for e in proposed] == [travel_params("目的地"), travel_params("事由")])
    date_changes = [e for e in named(events, DATA_CHANGED) if e.payload["slot"] == "日期"]
    c.check("除初始化那一条外，没有针对日期的数据变更事件",
            [e.payload["source"] for e in date_changes] == [kernel.INIT_SOURCE])
    init_date = "9 月 20 日"
    running_index = next(i for i, e in enumerate(events) if e.name == TASK_STATUS_CHANGED)
    c.check("日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日",
            all(replay_data(events[:n]).get("日期") == init_date for n in range(running_index + 1, len(events) + 1)))
    c.check("任务状态是已完成，数据完整", run.task.status == TaskStatus.DONE  # 第三步：只比业务槽位
            and business_data(run.task.data) == {"目的地": "上海", "日期": init_date, "事由": "客户拜访"}, run.task.data)
    check_cursor_final(c, run, cursor("收集", 3, None))
    check_matches_step_two(c, run)
    for action in run.task.actions.values():
        check_waiting_then_success(c, run, action.action_id)
    check_integrity(c, run)
    check_selection_trail(c, run, {
        1: {"前进": [], "跳过阶段": [], "跳过步骤": []},
        2: {"前进": [], "跳过阶段": [], "跳过步骤": [[2, "前置条件不成立：写入目标指向的位置为 None"]]},
    })
    check_trace_shape(c, run, loops=2)
    check_sources_and_senders(c, run, closed_before_failure_of=None)
    check_run_file(c, run)
    return c


# ───────────────────────── 第一步：材料接入登记的三个场景 ─────────────────────────

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


def check_kernel_unchanged(c: Checker) -> None:
    """零差异断言：模块的内容哈希等于写死的值（第一步是三个内核文件对 9472ac5，第二步是五个模块对 185c6d7，
    第三步是 dialogue.py 对 01de6ab）。"""
    here = Path(kernel.__file__).resolve().parent
    actual = {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in KERNEL_SHA256}
    for name, expected in KERNEL_SHA256.items():
        c.check(f"零差异：{name} 的 sha256 等于提交 {KERNEL_SHA256_COMMIT} 里的值", actual[name] == expected,
                f"写死 {expected}，实际 {actual[name]}")
    source = (here / "kernel.py").read_text(encoding="utf-8")
    words = ("目录", "文件总表", "材料清单", "登记进度", "清单文件路径", "list_dir", "register_file", "generate_manifest")
    hits = {word: source.count(word) for word in words}
    c.check("第一步目标一（附加）：内核文件里不出现材料接入登记的槽位名与工具名", sum(hits.values()) == 0, hits)


def check_other_tool_actions(c: Checker, run: Run) -> None:
    """非询问行动：状态经过是已提出、已获准、已成功；变更只写该工具声明的可写槽位。"""
    events, task = run.events, run.task
    for action in task.actions.values():
        if action.tool == "ask":
            continue
        history = status_values(action_history(events, action.action_id))
        slots = {e.payload["slot"] for e in business_changes(events, action.action_id)}  # 第三步：只看业务槽位
        c.check(f"行动 {action.action_id}（{action.tool}）的状态经过是 已提出、已获准、已成功，"
                f"变更只写可写槽位 {sorted(run.writable.get(action.tool) or [])}",
                history == [ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.SUCCEEDED]
                and slots and slots <= set(run.writable.get(action.tool) or ()),
                (history, slots))


def intake_common(c: Checker, run: Run, loops: int, closed_before_failure_of=None) -> None:
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
    check_kernel_unchanged(c)
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
            and business_data(task.data) == INTAKE_FINAL_DATA, task.data)
    check_cursor_final(c, run, cursor("生成清单", 4, None))
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
    intake_common(c, run, loops=8)
    return c


def intake_scenario_two() -> Checker:
    banner("材料接入登记·场景二：规则互换变体")
    run = run_scenario("T-intake-2", intake_swapped_def(), INTAKE_ANSWERS, INTAKE_TOOLS)
    c = Checker("材料接入登记·场景二：规则互换变体")
    print("── 断言 ──")
    check_kernel_unchanged(c)
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    events, task = run.events, run.task
    proposed = named(events, ACTION_PROPOSED)
    tools = [e.payload["tool"] for e in proposed]
    c.check("第一步目标二：工具依次是 列目录、登记文件、询问、登记文件、询问、登记文件、询问、生成清单文件",
            tools == ["list_dir", "register_file", "ask", "register_file", "ask", "register_file", "ask", "generate_manifest"], tools)
    c.check("第一步目标二：依据里的规则序号依次是 一、二、三、二、三、二、三、四（规则保留原序号，只改检查顺序）",
            rule_numbers(proposed) == [1, 2, 3, 2, 3, 2, 3, 4], rule_numbers(proposed))
    c.check("第一步目标二：终态数据与场景一相同", task.status == TaskStatus.DONE  # 第三步：只比业务槽位
            and business_data(task.data) == INTAKE_FINAL_DATA, task.data)
    check_cursor_final(c, run, cursor("生成清单", 4, None))
    check_matches_step_two(c, run)
    for action in task.actions.values():
        if action.tool == "ask":
            check_waiting_then_success(c, run, action.action_id)
    check_other_tool_actions(c, run)
    check_explainable(c, run)
    intake_common(c, run, loops=8)
    return c


def intake_scenario_three() -> Checker:
    banner("材料接入登记·场景三：回答缺失")
    answers = {key: value for key, value in INTAKE_ANSWERS.items() if key[1][0] in (0, 1)}
    run = run_scenario("T-intake-3", intake_def(), answers, INTAKE_TOOLS)
    c = Checker("材料接入登记·场景三：回答缺失")
    print("── 断言 ──")
    check_kernel_unchanged(c)
    c.check("内核线程以内核错误结束", isinstance(run.error, KernelError), repr(run.error))
    if isinstance(run.error, KernelError):
        c.check("内核错误携带的是行动 7", run.error.action is not None and run.error.action.action_id == 7)
    events = run.events
    proposed = named(events, ACTION_PROPOSED)
    c.check("恰好七个「行动提出」，工具依次是 列目录、登记文件×3、询问×3",
            [e.payload["tool"] for e in proposed] == ["list_dir", "register_file", "register_file", "register_file", "ask", "ask", "ask"],
            [e.payload["tool"] for e in proposed])
    for action_id in (5, 6):
        check_waiting_then_success(c, run, action_id)
    history = action_history(events, 7)
    c.check("行动 7 的状态序列是 已提出、已获准、等待中、已失败，最后一条说明是「没有可用的回答」",
            status_values(history) == [ActionStatus.PROPOSED, ActionStatus.APPROVED, ActionStatus.WAITING, ActionStatus.FAILED]
            and history[-1].payload["note"] == "没有可用的回答",
            [(e.payload["new_status"].value, e.payload["note"]) for e in history])
    failed_seq = history[-1].seq if history else 0
    c.check("行动 7 在「已失败」之前发出过问题（发件箱放入），没有收件箱的「消息取出」",
            [e for e in of_action(events, MESSAGE_PUT, 7) if e.payload["box"] == OUTBOX and e.seq < failed_seq]
            and not [e for e in of_action(events, MESSAGE_TAKEN, 7) if e.payload["box"] == INBOX])
    c.check("最后一个状态事件是内核关闭发件箱",
            events and events[-1].name == MAILBOX_CLOSED and events[-1].payload == {"box": OUTBOX, "sender": SOURCE_LOOP},
            events[-1].name if events else None)
    c.check("任务没有结束：没有「任务结束」事件，任务状态仍是执行中；c.xlsx 的是否纳入仍为 None",
            not named(events, TASK_ENDED) and run.task is not None and run.task.status == TaskStatus.RUNNING
            and [item["是否纳入"] for item in run.task.data["材料清单"]] == ["是", "否", None])
    c.check("失败的行动在行动表里：行动表的编号是 1 到 7，行动 7 的状态是已失败",
            run.task is not None and list(run.task.actions) == list(range(1, 8))
            and run.task.actions[7].status == ActionStatus.FAILED)
    check_cursor_final(c, run, cursor("确认", 3, 2))  # 失败的行动 7 不写游标，停在行动 6（第 2 轮）之后
    check_matches_step_two(c, run)
    intake_common(c, run, loops=7, closed_before_failure_of=7)
    return c


# ───────────────────────── 第二步：观测台 ─────────────────────────

# 七个场景的运行参数：（任务标识, 任务定义, 答案表, 工具名）。
def all_scenarios():
    return [
        ("T-scenario-1", travel_def(), slot_answers(FULL_ANSWERS), ("ask",)),
        ("T-scenario-2", travel_all_filled_def(), {}, ("ask",)),
        ("T-scenario-3", travel_def(), slot_answers({"目的地": "上海", "日期": "9 月 20 日"}), ("ask",)),
        ("T-scenario-4", travel_date_filled_def(), slot_answers({"目的地": "上海", "事由": "客户拜访"}), ("ask",)),
        ("T-intake-1", intake_def(), INTAKE_ANSWERS, INTAKE_TOOLS),
        ("T-intake-2", intake_swapped_def(), INTAKE_ANSWERS, INTAKE_TOOLS),
        ("T-intake-3", intake_def(), {k: v for k, v in INTAKE_ANSWERS.items() if k[1][0] in (0, 1)}, INTAKE_TOOLS),
    ]


# 摘要预期表：任务标识 → （任务定义名, 终态, 迭代数, 行动数），来自第零步、第一步文档。
# 第三步第十二项起结果检查挪到迭代末尾，正常完成的运行迭代数减一，等于行动数；以内核错误结束的不变。
EXPECTED_SUMMARIES = {
    "T-scenario-1": ("出差申请单", "已完成", 3, 3),
    "T-scenario-2": ("出差申请单（三项预填）", "已完成", 0, 0),
    "T-scenario-3": ("出差申请单", "内核错误", 3, 3),
    "T-scenario-4": ("出差申请单（日期预填）", "已完成", 2, 2),
    "T-intake-1": ("材料接入登记", "已完成", 8, 8),
    "T-intake-2": ("材料接入登记（规则二三互换）", "已完成", 8, 8),
    "T-intake-3": ("材料接入登记", "内核错误", 7, 7),
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
    check_kernel_unchanged(c)
    work = Path(tempfile.mkdtemp(prefix="tod-observatory-"))
    try:
        runs_dir = work / "runs"
        for round_no in (1, 2):
            for task_id, task_def, answers, tools in all_scenarios():
                run_scenario(task_id, task_def, answers, tools, runs_dir=runs_dir, console=False)
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
            if EXPECTED_SUMMARIES.get(summary.task_id) != actual:
                wrong.append((f.name, actual))
        c.check("第二步目标一：每份文件的摘要（任务定义名、终态、迭代数、行动数）与预期表逐行相等", not wrong, wrong)
        c.check("第二步目标一：每个任务标识恰有两份文件", per_task == {task_id: 2 for task_id in EXPECTED_SUMMARIES}, per_task)
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
                    and (old_summary.task_def_name, old_summary.final_status, old_summary.loops, old_summary.actions) == EXPECTED_SUMMARIES["T-intake-1"],
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
        sample = read_events(by_task["T-scenario-2"]) if "T-scenario-2" in by_task else []
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


# ───────────────────────── 第三步：加载错误 ─────────────────────────

def _drop_top_key(definition):
    del definition["阶段列表"]


def _type_mismatch(definition):
    definition["阶段列表"][0]["类型"] = "自主规划"  # 内容仍是步骤列表


def _unknown_slot(definition):
    definition["阶段列表"][1]["步骤"][0]["参数"]["index"] = {"槽位": "登记序号"}


def _unknown_tool(definition):
    definition["阶段列表"][0]["步骤"][0]["工具"] = "list_directory"


def _repeat_without_max(definition):
    del definition["阶段列表"][1]["步骤"][0]["最多"]  # 登记步骤只写「重复直到」不写「最多」


def _repeat_keys_in_group(definition):
    # 「登记一个问一个」步骤组里的登记步骤带上重复键
    definition["阶段列表"][1]["步骤"][0]["步骤组"][0]["重复直到"] = [{"谓词": "不为空", "槽位": "文件总表"}]
    definition["阶段列表"][1]["步骤"][0]["步骤组"][0]["最多"] = 3


def _slot_without_type(definition):
    del definition["槽位"]["目录"]["类型"]


def _deliverable_source_missing(definition):
    definition["交付物"][0]["来源"] = "登记表"


def _step_without_note(definition):
    definition["阶段列表"][0]["步骤"][0]["说明"] = ""


# 九份写坏的定义：（场景标题, 从哪份正确文件复制, 怎么改坏, 错误位置, 错误原因里应含的文字）。
BAD_DEFINITIONS = [
    ("缺顶层键", "intake.json", _drop_top_key, "顶层", "缺少键「阶段列表」"),
    ("类型与内容不符", "travel.json", _type_mismatch, "阶段列表[0]（收集）.类型", "类型与内容不符"),
    ("引用不存在的槽位", "intake.json", _unknown_slot, "阶段列表[1]（登记）.步骤[0].参数.index.槽位", "不存在的槽位「登记序号」"),
    ("工具名不在静态表", "intake.json", _unknown_tool, "阶段列表[0]（列目录）.步骤[0].工具", "不在静态工具表"),
    ("步骤只写重复直到不写最多", "intake.json", _repeat_without_max, "阶段列表[1]（登记）.步骤[0]", "要么都写要么都不写，缺少「最多」"),
    ("组内步骤带重复键", "intake_interleaved.json", _repeat_keys_in_group, "阶段列表[1]（登记与确认）.步骤[0].步骤组[0]", "组内步骤不得带「重复直到」「最多」"),
    ("槽位缺类型", "intake.json", _slot_without_type, "槽位.目录", "缺少键「类型」"),
    ("交付物来源槽位不存在", "intake.json", _deliverable_source_missing, "交付物[0]（材料清单）.来源", "不存在的槽位「登记表」"),
    ("步骤缺说明", "intake.json", _step_without_note, "阶段列表[0]（列目录）.步骤[0].说明", "步骤的说明应当是一句非空的话"),
]


def load_error_scenario(title: str, source: str, breaker, where: str, reason_part: str) -> Checker:
    """第三步目标三：写坏的定义在启动任务之前被加载器挡住，不写出任何事件。

    在临时目录里从正确文件复制一份再改坏；宿主照常先建事件流、挂订阅者，再加载，加载成功才启动任务。
    """
    import json as json_module
    import shutil
    import tempfile

    banner(f"第三步·加载错误：{title}")
    c = Checker(f"第三步·加载错误：{title}")
    print("── 断言 ──")
    work = Path(tempfile.mkdtemp(prefix="tod-load-error-"))
    try:
        original = TASK_DEFS_DIR / source
        taskdef.load(original)  # 正确文件本身能加载，否则下面的错误说明不了问题
        definition = json_module.loads(original.read_text(encoding="utf-8"))
        breaker(definition)
        path = work / f"bad_{source}"
        path.write_text(json_module.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
        c.check(f"写坏的定义文件真实存在：从 {source} 复制后改坏", path.is_file(), path)

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
        c.check("加载抛出加载错误（LoadError）", isinstance(error, taskdef.LoadError), repr(error))
        if isinstance(error, taskdef.LoadError):
            c.check("加载错误的 path 属性是写坏的文件路径，错误信息里含该路径",
                    error.path == str(path) and str(path) in str(error), (error.path, str(error)))
            c.check(f"加载错误的 where 属性是「{where}」，错误信息里含该位置",
                    error.where == where and where in str(error), (error.where, str(error)))
            c.check(f"错误信息写明原因，含「{reason_part}」", reason_part in str(error), str(error))
        c.check("没有任何事件写出：内存收集器为空", collector.events == [], [e.name for e in collector.events])
        c.check("没有任何事件写出：运行目录里没有文件",
                not runs_dir.exists() or not any(runs_dir.iterdir()),
                sorted(p.name for p in runs_dir.iterdir()) if runs_dir.exists() else None)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c


# 九份写坏的定义里属于结构错误、应当被 JSON Schema 拦下的；其余三份是语义错误（槽位不存在、工具不在表里、交付物来源槽位不存在），schema 管不了。
SCHEMA_STRUCTURAL = {"缺顶层键", "类型与内容不符", "步骤只写重复直到不写最多", "组内步骤带重复键", "槽位缺类型", "步骤缺说明"}
SCHEMA_FILE = "任务定义.schema.json"


def schema_checks() -> Checker:
    """第三步第九项：任务定义文件的 JSON Schema。五份好文件通过；九份写坏的定义里结构错误被拦下，语义错误放行。
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
    c.check("好文件恰好五份", len(good) == 5, [path.name for path in good])
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
    """告知异常的话里游标那一句的写法：组尾、组内（第 r+1 轮进行中）、第 1 轮进行中、不在组里、阶段起点、自主规划阶段。
    现有异常场景只经过组尾与阶段起点，其余写法直接调工具的拼话函数核对。"""
    from tod_kernel.tools import exception_utterance

    title = "第三步·告知异常话的轮数写法"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    base = {"阶段": "登记与确认", "未达成目标": [{"文字": "登记进度 等于 99，当前值 1"}], "步骤现况": [], "可选措施": list(EXCEPTION_OPTIONS)}
    head = "阶段『登记与确认』目标未达成：登记进度 等于 99，当前值 1；"
    cases = [
        ("组尾", position("登记与确认", 3, "询问", 2, True), "已完成『登记与确认』阶段第 3 步『询问』，该组已完成 2 轮"),
        ("组内、已完成 1 轮", position("登记与确认", 2, "登记", 1, False), "已完成『登记与确认』阶段第 2 步『登记』，该组已完成 1 轮，第 2 轮进行中"),
        ("组内、已完成 0 轮", position("登记与确认", 2, "登记", 0, False), "已完成『登记与确认』阶段第 2 步『登记』，该组第 1 轮进行中"),
        ("不在组里", position("登记与确认", 1, "列目录", None, None), "已完成『登记与确认』阶段第 1 步『列目录』"),
        ("阶段起点", position("登记与确认", None, None, None, None), "在『登记与确认』阶段起点，尚未执行步骤"),
        ("自主规划阶段", position("登记与确认", None, None, 4, None), "在『登记与确认』阶段已进行 4 回合"),
    ]
    for name, at, where in cases:
        expected = f"{head}{where}。{OPTIONS_TEXT}"
        actual = exception_utterance({**base, "游标": at})
        c.check(f"{name}：话逐字等于「{expected}」", actual == expected, actual)
    return c


def runtime_limit_error_scenario() -> Checker:
    """第三步：「最多」写成引用却指向一个列表。加载通过（加载器只查引用形状与槽位存在），
    运行到这个步骤组时行动选择返回空，内核报任务定义错误；不得让 Python 的类型错误穿透内核。"""
    import json as json_module
    import shutil
    import tempfile

    title = "第三步·运行期任务定义错误：步骤组「最多」引用指向列表"
    banner(title)
    c = Checker(title)
    work = Path(tempfile.mkdtemp(prefix="tod-limit-error-"))
    try:
        definition = json_module.loads((TASK_DEFS_DIR / "intake.json").read_text(encoding="utf-8"))
        definition["阶段列表"][1]["步骤"][0]["最多"] = {"槽位": "文件总表"}  # 文件总表是个列表，不是数
        path = work / "bad_limit_intake.json"
        path.write_text(json_module.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
        load_error = None
        try:
            task_def = taskdef.load(path, initial=dict(SAMPLE_DIR_INPUT))
        except taskdef.LoadError as exc:
            load_error = exc
        run = None if load_error else run_scenario("T-limit-error", task_def, INTAKE_ANSWERS, INTAKE_TOOLS)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("── 断言 ──")
    c.check("写坏的定义加载通过（加载器不求引用的值）", load_error is None, repr(load_error))
    if run is None:
        return c
    c.check("内核线程以内核错误结束，不是 Python 的类型错误", isinstance(run.error, KernelError), repr(run.error))
    errors = named(run.all_events, TASK_DEFINITION_ERROR)
    c.check("恰好一条「任务定义错误」，原因是选择规则返回空",
            len(errors) == 1 and errors[0].payload["reason"] == "选择规则返回空", [e.payload for e in errors])
    proposed = named(run.events, ACTION_PROPOSED)
    c.check("只提出过一个行动：列目录（第 1 次迭代）；第 2 次迭代进登记组时求「最多」出错，未提出行动",
            [e.payload["tool"] for e in proposed] == ["list_dir"], [e.payload["tool"] for e in proposed])
    loops = [e.payload["loop_no"] for e in run.all_events if e.name == LOOP_STARTED]
    c.check("恰好两次迭代", loops == [1, 2], loops)
    c.check("任务没有结束：没有「任务结束」事件", not named(run.events, TASK_ENDED))
    summary = summarize(run.run_file) if run.run_file is not None and run.run_file.exists() else None
    c.check("运行索引的摘要：终态「内核错误」，原因「选择规则返回空」",
            summary is not None and summary.final_status == "内核错误" and summary.reason == "选择规则返回空",
            (summary.final_status, summary.reason) if summary else None)
    return c


# ───────────────────────── 第三步：同一份定义连跑两次 ─────────────────────────


def event_signature(event) -> tuple:
    """事件去掉时刻与序号后的内容。「邮箱等待」结束时的等待毫秒数由时刻算出，一并去掉。

    序号也去掉：宿主线程在发件箱上的等待、取出与内核线程的事件共用序号，两条线程的交错次序取决于调度，
    每次运行可能不同，这不是运行之间互相影响。所以按发布线程分成两列，各自逐条比对。
    """
    payload = {key: value for key, value in event.payload.items() if key != "wait_ms"}
    return (event.task_id, event.action_id, event.kind, event.source, event.name, payload)


def events_by_thread(run: Run) -> tuple:
    kernel_side = [event_signature(e) for e in run.all_events if run.thread_of_seq[e.seq] == run.kernel_thread]
    host_side = [event_signature(e) for e in run.all_events if run.thread_of_seq[e.seq] != run.kernel_thread]
    return kernel_side, host_side


def run_twice_checks() -> Checker:
    """第三步目标二：材料接入登记的同一个加载结果连跑两次，互不影响。"""
    banner("第三步·同一份定义连跑两次")
    c = Checker("第三步·同一份定义连跑两次")
    print("── 断言 ──")
    task_def = intake_def()  # 只加载一次，两次运行用同一个对象
    slots_before = copy_module.deepcopy(task_def.SLOTS)
    first = run_scenario("T-intake-twice", task_def, INTAKE_ANSWERS, INTAKE_TOOLS, console=False)
    second = run_scenario("T-intake-twice", task_def, INTAKE_ANSWERS, INTAKE_TOOLS, console=False)
    c.check("两次运行都正常完成", first.error is None and second.error is None
            and first.task.status == second.task.status == TaskStatus.DONE, (repr(first.error), repr(second.error)))
    started = [next((e for e in run.all_events if e.name == TASK_STARTED), None) for run in (first, second)]
    c.check("第二次「任务开始」事件里的初始槽位与第一次相同",
            started[0] is not None and started[1] is not None and started[0].payload["slots"] == started[1].payload["slots"],
            [e.payload["slots"] if e else None for e in started])
    c.check("第二次运行的材料清单初始为空（初始化变更组里材料清单的新值是空列表）",
            [ch.new for ch in second.task.init_changes if ch.slot == "材料清单"] == [[]],
            [(ch.slot, ch.new) for ch in second.task.init_changes])
    c.check("两次运行后，任务定义对象上的槽位表没有被运行改动", task_def.SLOTS == slots_before, task_def.SLOTS)
    (first_kernel, first_host), (second_kernel, second_host) = events_by_thread(first), events_by_thread(second)
    c.check(f"两次运行由内核线程发出的事件（共 {len(first_kernel)} 条）除时刻、序号与等待毫秒数外逐条相同",
            first_kernel and first_kernel == second_kernel, (len(first_kernel), len(second_kernel)))
    c.check(f"两次运行由宿主线程发出的事件（共 {len(first_host)} 条）除时刻、序号与等待毫秒数外逐条相同",
            first_host and first_host == second_host, (len(first_host), len(second_host)))
    c.check("两次运行的事件总数相同", len(first.all_events) == len(second.all_events),
            (len(first.all_events), len(second.all_events)))
    c.check("两次运行写出了两份不同的运行文件",
            first.run_file is not None and second.run_file is not None and first.run_file != second.run_file,
            (first.run_file, second.run_file))
    return c


# ───────────────────────── 第三步：异常告知使用者 ─────────────────────────

EXCEPTION_TOOLS = INTAKE_TOOLS + (EXCEPTION_TOOL,)
BAD_GOAL_NAME = "材料接入登记（登记目标写错）"
BROKEN_GOAL_NAME = "材料接入登记（登记破坏确认目标）"
OPTIONS_TEXT = "可选措施：重做本阶段、主动终止、被动终止。"

# 登记目标写错的样例：登记阶段目标是「相等：登记进度 与 99」，告知异常的依据序号是 6（四个步骤号之后，第二个阶段）。
BAD_GOAL_PREDICATE = {"谓词": "相等", "左": {"槽位": "登记进度"}, "右": 99}
BAD_GOAL_EXCEPTION_NUMBER = 6


REGISTER_NOTE = "登记下一个文件的名字、类型、大小、页数"


def position(stage, done, note, rounds, group_last) -> dict:
    """告知异常参数「游标」：游标的三项加上已完成步骤的说明与「是组尾」。"""
    return {"阶段": stage, "已完成步骤": done, "说明": note, "已完成轮数": rounds, "是组尾": group_last}


def bad_goal_params(at: dict) -> dict:
    return {
        "阶段": "登记",
        "未达成目标": [{"谓词": BAD_GOAL_PREDICATE, "当前值": {"左": 3, "右": 99}, "文字": "登记进度 等于 99，当前值 3"}],
        "步骤现况": [{"步骤": 2, "工具": "register_file", "成立": False, "说明": "序号等于登记进度且小于文件总数",
                   "命中值": {"登记进度": 3, "文件总数": 3}}],
        "游标": at,
        "可选措施": list(EXCEPTION_OPTIONS),
    }


BAD_GOAL_AT_END = position("登记", 2, REGISTER_NOTE, 3, True)  # 登记三轮后：已完成第 2 步，该组已完成 3 轮
BAD_GOAL_AT_START = position("登记", None, None, None, None)  # 重做写回阶段起点
BAD_GOAL_UTTERANCE_END = (f"阶段『登记』目标未达成：登记进度 等于 99，当前值 3；已完成『登记』阶段第 2 步『{REGISTER_NOTE}』，该组已完成 3 轮。"
                          f"{OPTIONS_TEXT}")
BAD_GOAL_UTTERANCE_START = f"阶段『登记』目标未达成：登记进度 等于 99，当前值 3；在『登记』阶段起点，尚未执行步骤。{OPTIONS_TEXT}"


# 登记破坏确认目标的样例：阶段顺序是 列目录、确认、登记；确认阶段的告知异常依据序号是 5（三个步骤号之后，第二个阶段）。
BROKEN_GOAL_PARAMS = {
    "阶段": "确认",
    "未达成目标": [{"谓词": {"谓词": "列表无项为空", "槽位": "材料清单", "字段": "是否纳入"}, "当前值": [0, 1, 2],
                "文字": "材料清单 里没有 是否纳入 为空的项，当前为空项的下标 [0, 1, 2]"}],
    "步骤现况": [{"步骤": 2, "工具": "ask", "成立": True, "说明": "写入目标指向的位置为 None",
               "命中值": {"槽位": "材料清单", "路径": [0, "是否纳入"], "当前值": None}}],
    "游标": position("登记", 3, REGISTER_NOTE, 3, True),
    "可选措施": list(EXCEPTION_OPTIONS),
}
BROKEN_GOAL_UTTERANCE = ("阶段『确认』的目标在后续阶段被破坏：材料清单 里没有 是否纳入 为空的项，当前为空项的下标 [0, 1, 2]；"
                         f"已完成『登记』阶段第 3 步『{REGISTER_NOTE}』，该组已完成 3 轮。{OPTIONS_TEXT}")
BROKEN_GOAL_EXCEPTION_NUMBER = 5


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
    if answer == "重做本阶段":
        c.check(f"行动 {action_id} 选重做：变更组只有一条，把游标写回「{stage}」阶段起点",
                len(changes) == 1 and changes[0].payload["slot"] == CURSOR_SLOT and changes[0].payload["new"] == cursor(stage, None, None),
                [e.payload for e in changes])
    else:
        c.check(f"行动 {action_id} 选终止：没有数据变更，游标也不写", not changes, [e.payload for e in changes])


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


def exception_redo_scenario() -> Checker:
    banner("第三步·异常场景甲：重做两次后被动终止")
    run = run_scenario("T-exception-1", intake_def("intake_bad_goal.json"), INTAKE_ANSWERS, EXCEPTION_TOOLS,
                       exception_answers=["重做本阶段", "重做本阶段", "被动终止"])
    c = Checker("第三步·异常场景甲：重做两次后被动终止")
    print("── 断言 ──")
    c.check("任务定义名是数据文件里的名字", run.task_def.NAME == BAD_GOAL_NAME, run.task_def.NAME)
    check_registered_three(c, run)
    proposed = named(run.events, ACTION_PROPOSED)
    c.check("恰好七个行动：列目录、登记文件×3、告知异常×3（第五、六、七次迭代）",
            [e.payload["tool"] for e in proposed] == ["list_dir"] + ["register_file"] * 3 + [EXCEPTION_TOOL] * 3,
            [e.payload["tool"] for e in proposed])
    check_exception_action(c, run, 5, BAD_GOAL_EXCEPTION_NUMBER, "登记", bad_goal_params(BAD_GOAL_AT_END),
                           BAD_GOAL_UTTERANCE_END, "重做本阶段")
    check_exception_action(c, run, 6, BAD_GOAL_EXCEPTION_NUMBER, "登记", bad_goal_params(BAD_GOAL_AT_START),
                           BAD_GOAL_UTTERANCE_START, "重做本阶段")
    check_exception_action(c, run, 7, BAD_GOAL_EXCEPTION_NUMBER, "登记", bad_goal_params(BAD_GOAL_AT_START),
                           BAD_GOAL_UTTERANCE_START, "被动终止")
    c.check("告知异常三条行动里，两条返回值是「重做」", [run.task.actions[i].result for i in (5, 6)] == ["重做", "重做"])
    check_terminated(c, run, 7, "任务无法继续，使用者确认终止")
    check_cursor_final(c, run, cursor("登记", None, None))
    exception_common(c, run, loops=7)
    return c


def exception_abort_scenario(task_id: str, answer: str, note: str, title: str) -> Checker:
    banner(f"第三步·{title}")
    run = run_scenario(task_id, intake_def("intake_bad_goal.json"), INTAKE_ANSWERS, EXCEPTION_TOOLS, exception_answers=[answer])
    c = Checker(f"第三步·{title}")
    print("── 断言 ──")
    check_registered_three(c, run)
    proposed = named(run.events, ACTION_PROPOSED)
    c.check("恰好五个行动，第五个（第五次迭代）是告知异常",
            [e.payload["tool"] for e in proposed] == ["list_dir"] + ["register_file"] * 3 + [EXCEPTION_TOOL],
            [e.payload["tool"] for e in proposed])
    check_exception_action(c, run, 5, BAD_GOAL_EXCEPTION_NUMBER, "登记", bad_goal_params(BAD_GOAL_AT_END),
                           BAD_GOAL_UTTERANCE_END, answer)
    check_terminated(c, run, 5, note)
    check_cursor_final(c, run, cursor("登记", 2, 3))
    exception_common(c, run, loops=5)
    return c


def exception_broken_goal_scenario() -> Checker:
    banner("第三步·异常场景丁：后面阶段破坏前面阶段的目标，重做后完成")
    run = run_scenario("T-exception-4", intake_def("intake_broken_goal.json"), INTAKE_ANSWERS, EXCEPTION_TOOLS,
                       exception_answers=["重做本阶段"])
    c = Checker("第三步·异常场景丁：后面阶段破坏前面阶段的目标，重做后完成")
    print("── 断言 ──")
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    c.check("任务定义名是数据文件里的名字", run.task_def.NAME == BROKEN_GOAL_NAME, run.task_def.NAME)
    proposed = named(run.events, ACTION_PROPOSED)
    nothing = {"前进": [], "跳过阶段": [], "跳过步骤": []}
    check_selection_trail(c, run, {1: nothing, 2: {"前进": ["列目录"], "跳过阶段": [["确认", "目标成立"]], "跳过步骤": []},
                                   3: nothing, 4: nothing, 6: nothing, 7: nothing, 8: nothing})
    c.check("八个行动：列目录、登记文件×3、告知异常、询问×3；依据序号 1、3、3、3、5、2、2、2",
            [(e.payload["tool"], e.payload["basis"][0]) for e in proposed]
            == [("list_dir", 1), ("register_file", 3), ("register_file", 3), ("register_file", 3),
                (EXCEPTION_TOOL, 5), ("ask", 2), ("ask", 2), ("ask", 2)],
            [(e.payload["tool"], e.payload["basis"][0]) for e in proposed])
    check_exception_action(c, run, 5, BROKEN_GOAL_EXCEPTION_NUMBER, "确认", BROKEN_GOAL_PARAMS, BROKEN_GOAL_UTTERANCE, "重做本阶段", "后续破坏")
    cursors = [e.payload["new"] for e in named(run.events, DATA_CHANGED) if e.payload["slot"] == CURSOR_SLOT]
    c.check("游标的变化：初始在列目录，跳过已成立的确认进入登记，登记三轮后因重做退回确认，确认三轮后停住",
            cursors == [cursor("列目录", None, None), cursor("列目录", 1, None), cursor("登记", 3, 1), cursor("登记", 3, 2),
                        cursor("登记", 3, 3), cursor("确认", None, None), cursor("确认", 2, 1), cursor("确认", 2, 2), cursor("确认", 2, 3)],
            cursors)
    for action_id in (6, 7, 8):
        check_waiting_then_success(c, run, action_id)
    expected = {**INTAKE_FINAL_DATA, "清单文件路径": None}  # 这份定义没有生成清单阶段
    c.check("任务状态是已完成，业务槽位的终态数据与预期相同",
            run.task.status == TaskStatus.DONE and business_data(run.task.data) == expected, run.task.data)
    check_cursor_final(c, run, cursor("确认", 2, 3))
    ended = named(run.events, TASK_ENDED)
    c.check("「任务结束」恰好一次，原因是完成条件成立", len(ended) == 1 and ended[0].payload["reason"] == "完成条件成立")
    exception_common(c, run, loops=8)
    return c


def main() -> int:
    checkers = [scenario_one(), scenario_two(), scenario_three(), scenario_four(),
                intake_scenario_one(), intake_scenario_two(), intake_scenario_three(),
                observatory_checks()]
    checkers += [load_error_scenario(*bad) for bad in BAD_DEFINITIONS]
    checkers += [schema_checks(), utterance_round_clause_checks(), runtime_limit_error_scenario()]
    checkers += [run_twice_checks(),
                 exception_redo_scenario(),
                 exception_abort_scenario("T-exception-2", "主动终止", "使用者主动终止", "异常场景乙：主动终止"),
                 exception_abort_scenario("T-exception-3", "被动终止", "任务无法继续，使用者确认终止", "异常场景丙：被动终止"),
                 exception_broken_goal_scenario()]
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
