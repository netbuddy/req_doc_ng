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

import hashlib

from tod_kernel import kernel, task_intake, task_travel
from tod_kernel.tools import build_table
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
    read_events,
    replay_data,
)

KERNEL_THREAD_PREFIX = "kernel-"
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


# 话语的预期句：按写入目标逐条写死的完整句子，不调用话语生成、也不读模板，用来逐字比对问题里的话。
EXPECTED_UTTERANCES = {
    ("目的地", ()): "请提供出差目的地。",
    ("日期", ()): "请提供出差日期。",
    ("事由", ()): "请提供出差事由。",
    ("材料清单", (0, "是否纳入")): "请确认是否把文件 a.docx 纳入项目。",
    ("材料清单", (1, "是否纳入")): "请确认是否把文件 b.pdf 纳入项目。",
    ("材料清单", (2, "是否纳入")): "请确认是否把文件 c.xlsx 纳入项目。",
}

# 第一步验证目标一：内核三个文件在提交 9472ac5 里的 sha256，写死在这里。改了一个字节，本步即不通过。
KERNEL_SHA256 = {
    "kernel.py": "b28cb651f1ab500cee7306a891b42417f6063897fc4cfb2d6743f65a917974e2",
    "observe.py": "12f7168bfb27a2318cc5e2771adbd7b55e441a07f192a6dfb4bcdaf42999c570",
    "view.py": "958e62c499ce7771316d2a40d9188208b0678c429e43ee975ea7ddb74cebd651",
}


def run_scenario(task_id: str, task_def, answers: dict, tool_names=("ask",)) -> Run:
    run = Run(task_id=task_id, task_def=task_def, host_thread=threading.get_ident())

    # 宿主：事件流、订阅者、邮箱。
    stream = EventStream(task_id)
    collector = MemoryCollector()
    stream.subscribe(collector)
    stream.subscribe(ConsolePrinter())
    writer = FileWriter(RUNS_DIR)
    stream.subscribe(writer)
    run.run_file = writer.path_for(task_id)
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


def check_trace_shape(c: Checker, run: Run, loops: int) -> None:
    """追踪事件的形状：位置、数量与内容。只作诊断信息的检查，不参与前五条目标。"""
    all_events, events, task_def = run.all_events, run.events, run.task_def
    trace = [e for e in all_events if e.kind == TRACE]
    state_names = set(STATE_EVENT_NAMES)
    c.check("追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace",
            all((e.name in state_names) == (e.kind == STATE) for e in all_events))
    kernel_side = [e for e in all_events if not is_external(e.name, e.payload)]
    first = kernel_side[0] if kernel_side else None
    c.check("追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），"
            "槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致",
            first is not None and first.name == TASK_STARTED
            and first.payload == {"slots": dict(task_def.SLOTS), "rules": dict(task_def.RULES),
                                  "tools": run.tools_spec, "task_def_name": task_def.NAME},
            first.payload if first else None)
    loop_nos = [e.payload["loop_no"] for e in trace if e.name == LOOP_STARTED]
    c.check(f"追踪：恰好 {loops} 个「一圈开始」，圈序号从 1 起连续", loop_nos == list(range(1, loops + 1)), loop_nos)
    done_flags = [e.payload["done"] for e in trace if e.name == CHECK_DONE_RESULT]
    expected_done = [False] * loops if run.error else [False] * (loops - 1) + [True]
    c.check("追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真", done_flags == expected_done, done_flags)
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
        if proposed.payload["tool"] != "ask":
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


# ───────────────────────── 四个场景 ─────────────────────────

FULL_ANSWERS = {"目的地": "上海", "日期": "9 月 20 日", "事由": "客户拜访"}


def travel_params(slot: str) -> dict:
    return {"target": {"slot": slot, "path": []}, "hint": {}}


def banner(text: str) -> None:
    print(f"\n{'═' * 12} {text} {'═' * 12}", flush=True)


def scenario_one() -> Checker:
    banner("场景一：正常流程")
    run = run_scenario("T-scenario-1", task_travel, slot_answers(FULL_ANSWERS))
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
    c.check("目标一：终态数据是三条回答", task.data == FULL_ANSWERS, task.data)

    for action in task.actions.values():
        check_waiting_then_success(c, run, action.action_id)
    check_explainable(c, run)
    check_kernel_is_task_agnostic(c)
    c.check("内核已删去 record 函数，第 5 步只剩状态更新", not hasattr(kernel, "record"))
    check_integrity(c, run)
    check_trace_shape(c, run, loops=4)
    check_sources_and_senders(c, run, closed_before_failure_of=None)
    check_run_file(c, run)
    return c


def scenario_two() -> Checker:
    banner("场景二：初始即完成")
    run = run_scenario("T-scenario-2", task_travel.ALL_FILLED, {})
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
    check_integrity(c, run)
    check_trace_shape(c, run, loops=1)
    check_sources_and_senders(c, run, closed_before_failure_of=None)
    check_run_file(c, run)
    return c


def scenario_three() -> Checker:
    banner("场景三：回答缺失")
    answers = {"目的地": "上海", "日期": "9 月 20 日"}
    run = run_scenario("T-scenario-3", task_travel, slot_answers(answers))
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
    c.check("行动 3 没有数据变更", not of_action(events, DATA_CHANGED, 3))
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
    run = run_scenario("T-scenario-4", task_travel.DATE_FILLED, slot_answers(answers))
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
    c.check("任务状态是已完成，数据完整", run.task.status == TaskStatus.DONE
            and run.task.data == {"目的地": "上海", "日期": init_date, "事由": "客户拜访"}, run.task.data)
    for action in run.task.actions.values():
        check_waiting_then_success(c, run, action.action_id)
    check_integrity(c, run)
    check_trace_shape(c, run, loops=3)
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
    """第一步验证目标一：内核三个文件的内容哈希等于提交 9472ac5 里的值。"""
    here = Path(kernel.__file__).resolve().parent
    actual = {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in KERNEL_SHA256}
    for name, expected in KERNEL_SHA256.items():
        c.check(f"第一步目标一：{name} 的 sha256 等于提交 9472ac5 里的值", actual[name] == expected,
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
        slots = {e.payload["slot"] for e in of_action(events, DATA_CHANGED, action.action_id)}
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
    run = run_scenario("T-intake-1", task_intake, INTAKE_ANSWERS, INTAKE_TOOLS)
    c = Checker("材料接入登记·场景一：正常流程")
    print("── 断言 ──")
    check_kernel_unchanged(c)
    c.check("内核线程正常返回、没有抛异常", run.error is None, repr(run.error))
    if run.task is None:
        return c
    events, task = run.events, run.task
    proposed = named(events, ACTION_PROPOSED)
    tools = [e.payload["tool"] for e in proposed]
    c.check("第一步目标二：恰好八个行动（九圈里最后一圈结果检查为真，不提行动）", len(proposed) == 8, len(proposed))
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
    c.check("任务状态是已完成，终态数据与预期完全相同", task.status == TaskStatus.DONE and task.data == INTAKE_FINAL_DATA, task.data)
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
    intake_common(c, run, loops=9)
    return c


def intake_scenario_two() -> Checker:
    banner("材料接入登记·场景二：规则互换变体")
    run = run_scenario("T-intake-2", task_intake.SWAPPED, INTAKE_ANSWERS, INTAKE_TOOLS)
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
    c.check("第一步目标二：终态数据与场景一相同", task.status == TaskStatus.DONE and task.data == INTAKE_FINAL_DATA, task.data)
    for action in task.actions.values():
        if action.tool == "ask":
            check_waiting_then_success(c, run, action.action_id)
    check_other_tool_actions(c, run)
    check_explainable(c, run)
    intake_common(c, run, loops=9)
    return c


def intake_scenario_three() -> Checker:
    banner("材料接入登记·场景三：回答缺失")
    answers = {key: value for key, value in INTAKE_ANSWERS.items() if key[1][0] in (0, 1)}
    run = run_scenario("T-intake-3", task_intake, answers, INTAKE_TOOLS)
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
    intake_common(c, run, loops=7, closed_before_failure_of=7)
    return c


def main() -> int:
    checkers = [scenario_one(), scenario_two(), scenario_three(), scenario_four(),
                intake_scenario_one(), intake_scenario_two(), intake_scenario_three()]
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
