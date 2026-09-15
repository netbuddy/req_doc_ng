"""任务循环内核。

本模块不认识任何具体任务：代码里不出现槽位名与工具名。任务定义与工具表作为参数传入，
使用者输入经邮箱进入，观测经事件流出去。

事件分两类。状态事件记改动，是历史的正本：写入点共四处，每处写入的同时发布状态事件——
状态更新（update_state）、登记行动（register_action）、记状态（set_status）、邮箱。
追踪事件记做了什么检查、得了什么结论、调了什么，发在循环五步、行动执行、邮箱阻塞取的固定位置，
不参与重放。行动选择（select_action）只返回候选，不写任何东西。

事件的三个分类方向互不相干：类别（kind）答「是不是改动」；记录方（source）答「哪个组件发布了它」，
由发布方经事件流发给它的发布句柄写入，不按事件名推断；消息的发起方（sender）答「谁引起的」，
由放消息的一方自报。
"""

from __future__ import annotations

import copy
import dataclasses
import enum
import functools
import threading
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable


class KernelError(Exception):
    """内核错误：行动执行失败，或创建行动之前发现的任务定义错误。

    action：失败的行动（任务定义错误时为空）；raw：选择规则的原始输出（任务定义错误时携带）。
    """

    def __init__(self, reason: str, action: Any = None, raw: Any = None):
        super().__init__(reason)
        self.reason = reason
        self.action = action
        self.raw = raw


class TaskStatus(enum.Enum):
    NOT_STARTED = "未开始"
    RUNNING = "执行中"
    DONE = "已完成"


class ActionStatus(enum.Enum):
    PROPOSED = "已提出"
    APPROVED = "已获准"
    REJECTED = "已拒绝"  # 本步无人置，留给执行控制的真实核验
    WAITING = "等待中"
    SUCCEEDED = "已成功"
    FAILED = "已失败"


TERMINAL_ACTION_STATUSES = (ActionStatus.SUCCEEDED, ActionStatus.FAILED)


# 事件名：内核固定的词表，每种事件的内容字典有固定的键。
TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"  # old_status, new_status
DATA_CHANGED = "DATA_CHANGED"  # slot, old, new, source
ACTION_PROPOSED = "ACTION_PROPOSED"  # tool, params, proposer, basis
ACTION_STATUS_CHANGED = "ACTION_STATUS_CHANGED"  # new_status, note（终态另有 result）
MESSAGE_PUT = "MESSAGE_PUT"  # kind, sender, recipient, content, seq
MESSAGE_TAKEN = "MESSAGE_TAKEN"  # kind, sender, recipient, content, seq
MAILBOX_CLOSED = "MAILBOX_CLOSED"  # sender
TASK_ENDED = "TASK_ENDED"  # final_status, reason

STATE_EVENT_NAMES = (
    TASK_STATUS_CHANGED,
    DATA_CHANGED,
    ACTION_PROPOSED,
    ACTION_STATUS_CHANGED,
    MESSAGE_PUT,
    MESSAGE_TAKEN,
    MAILBOX_CLOSED,
    TASK_ENDED,
)

# 追踪事件名：记做了什么检查、得了什么结论、调了什么；是诊断信息，不是改动，重放不用它们。
TASK_STARTED = "TASK_STARTED"  # slots, definition, tools, task_def_name
LOOP_STARTED = "LOOP_STARTED"  # loop_no
CHECK_DONE_RESULT = "CHECK_DONE_RESULT"  # done
CONTROL_RESULT = "CONTROL_RESULT"  # action_id, verdict, checked
EXECUTE_CALL = "EXECUTE_CALL"  # action_id, tool, phase（enter/return）, thread
MAILBOX_WAIT = "MAILBOX_WAIT"  # action_id, phase（begin/end）, wait_ms（end 时）, thread
TASK_DEFINITION_ERROR = "TASK_DEFINITION_ERROR"  # reason, raw_output

TRACE_EVENT_NAMES = (
    TASK_STARTED,
    LOOP_STARTED,
    CHECK_DONE_RESULT,
    CONTROL_RESULT,
    EXECUTE_CALL,
    MAILBOX_WAIT,
    TASK_DEFINITION_ERROR,
)

STATE = "state"
TRACE = "trace"

# 记录方：点分层级。工具实现的记录方是 tool.<工具名>，工具名是运行时数据。
SOURCE_LOOP = "kernel.loop"  # 循环、结果检查、登记行动、执行控制、行动执行的调用
SOURCE_UPDATE = "kernel.update"  # 状态更新
SOURCE_MAILBOX = "kernel.mailbox"  # 邮箱
TOOL_SOURCE_PREFIX = "tool."

# 消息的发起方：由放消息（或关闭邮箱）的一方自报。外部是 host、user；内核一侧用记录方的名字，例如 tool.ask。
EXTERNAL_SENDERS = ("host", "user")

# 箱名：收件箱是外部发给内核的，发件箱是内核发给外部的。
INBOX = "inbox"
OUTBOX = "outbox"

INIT_SOURCE = "初始化"


@dataclass(frozen=True)
class Change:
    """一条变更。source：行动编号，初始化时为「初始化」。"""

    slot: str
    old: Any
    new: Any
    source: Any


@dataclass(frozen=True)
class EndRecord:
    final_status: TaskStatus
    reason: str


@dataclass(frozen=True)
class Candidate:
    """候选行动：行动选择的返回，尚未登记成行动。"""

    tool: str
    params: dict
    basis: Any  # 选择规则返回的依据，原样存放，内核不解释
    proposer: str  # "selector" 或 "user"
    # 游标新值：（槽位名, 新值），由任务定义随候选给出，为空表示不动；行动成功时在状态更新里作为一条变更与工具的
    # 变更组一起写入，来源是同一个行动编号，行动失败或被拒绝时不写。内核不解释槽位名与新值。
    cursor_update: tuple | None = None


@dataclass
class Action:
    """行动：工具的一次调用。状态经过与执行前快照不存在这里，由事件流推出。"""

    action_id: int
    tool: str
    params: dict
    proposer: str  # "selector" 或 "user"
    basis: Any  # 选择规则返回的依据，原样存放，内核不解释
    status: Any = None
    result: Any = None
    changes: list = field(default_factory=list)


@dataclass
class Task:
    task_id: str
    task_def: Any
    tools: Any
    inbox: Any
    outbox: Any
    stream: "EventStream"
    status: TaskStatus = TaskStatus.NOT_STARTED
    data: dict = field(default_factory=dict)
    init_changes: list = field(default_factory=list)
    actions: dict = field(default_factory=dict)  # 行动表：行动编号 → 行动，按登记顺序排列
    end_record: EndRecord | None = None
    next_action_id: int = 1
    loop_publisher: Any = None  # 绑定 kernel.loop 的发布句柄
    update_publisher: Any = None  # 绑定 kernel.update 的发布句柄


@dataclass(frozen=True)
class Event:
    """一件已经发生的事。发布后不可修改；内容在发布时深拷贝。"""

    seq: int
    ts: float
    task_id: str
    action_id: int | None
    kind: str  # "state" 或 "trace"，由事件名决定
    source: str  # 记录方：发布它的组件
    name: str
    payload: dict


class EventStream:
    """只追加的事件序列。其中的状态事件是历史的正本，追踪事件是诊断信息。

    发布时用可重入锁把「分配序号与时刻」和「依次交给订阅者」整体串行化，
    保证多线程发布时每个订阅者收到事件的顺序等于序号顺序。订阅者抛出的异常不吞，直接向上传。
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._subscribers: list[Callable[[Event], None]] = []
        self._next_seq = 1
        self._lock = threading.RLock()

    def subscribe(self, subscriber: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.append(subscriber)

    def bind(self, source: str) -> "Publisher":
        """返回绑定了记录方的发布句柄；各组件各持一个，经它发布事件。"""
        if not _valid_source(source):
            raise KernelError(f"记录方不合法：{source!r}")
        return Publisher(self, source)

    def publish(self, name: str, payload: dict, action_id: int | None = None, *, source: str) -> Event:
        if not _valid_source(source):
            raise KernelError(f"记录方不合法：{source!r}")
        if name in STATE_EVENT_NAMES:
            kind = STATE
        elif name in TRACE_EVENT_NAMES:
            kind = TRACE
        else:
            raise KernelError(f"未知的事件名：{name}")
        with self._lock:
            event = Event(
                seq=self._next_seq,
                ts=time.monotonic(),
                task_id=self.task_id,
                action_id=action_id,
                kind=kind,
                source=source,
                name=name,
                payload=copy.deepcopy(payload),
            )
            self._next_seq += 1
            for subscriber in list(self._subscribers):
                subscriber(event)
            return event


class Publisher:
    """绑定了记录方的发布句柄。"""

    def __init__(self, stream: EventStream, source: str):
        self._stream = stream
        self.source = source

    def publish(self, name: str, payload: dict, action_id: int | None = None) -> Event:
        return self._stream.publish(name, payload, action_id, source=self.source)


def _valid_sender(sender) -> bool:
    return sender in EXTERNAL_SENDERS or _valid_source(sender)


def _valid_source(source) -> bool:
    if source in (SOURCE_LOOP, SOURCE_UPDATE, SOURCE_MAILBOX):
        return True
    return isinstance(source, str) and source.startswith(TOOL_SOURCE_PREFIX) and len(source) > len(TOOL_SOURCE_PREFIX)


@dataclass(frozen=True)
class Message:
    """消息。

    kind：本步是 "question"（问题）或 "answer"（回答）；
    sender：发起方，由放消息方自报，外部是 host、user，内核一侧用记录方名（如 tool.ask）；
    recipient：行动编号、"user" 或 "loop"；
    in_reply_to：回复对象，即所回复消息的到达序号，没有时为 None；
    content：内容；seq：到达序号，由邮箱分配；
    action_id：所属行动，可选。邮箱事件的行动编号优先取它，没有才看收件人是否为整数。
    """

    kind: str
    sender: str
    recipient: Any
    in_reply_to: int | None
    content: Any
    seq: int | None = None
    action_id: int | None = None


class Mailbox:
    """邮箱：有序队列加选择性接收（按匹配条件取第一条，不匹配的留在队列里）。

    任务持有两个实例：收件箱（box="inbox"）是外部发给内核的，是内核的入口；
    发件箱（box="outbox"）是内核发给外部的。两个箱行为完全相同，事件靠 box 键区分。
    锁的顺序固定为「邮箱锁 → 事件流锁」：放、取、关闭都在持有邮箱锁时发布事件，
    订阅者在派发过程中不得操作邮箱，因此不会形成反向加锁。邮箱的记录方是 kernel.mailbox；
    放入与关闭的发起方由调用方以 sender 自报，邮箱核实不了调用者是谁。
    """

    def __init__(self, stream: EventStream, task_id: str, box: str):
        if stream.task_id != task_id:
            raise KernelError("事件流绑定的任务标识与邮箱的任务标识不一致")
        if box not in (INBOX, OUTBOX):
            raise KernelError(f"箱名不合法：{box!r}")
        self._publisher = stream.bind(SOURCE_MAILBOX)
        self.task_id = task_id
        self.box = box
        self._queue: list[Message] = []
        self._next_seq = 1
        self._closed = False
        self._cond = threading.Condition()

    def put(self, message: Message) -> Message:
        with self._cond:
            if not _valid_sender(message.sender):
                raise KernelError(f"消息的发起方不合法：{message.sender!r}")
            if self._closed:
                raise KernelError("邮箱已关闭，不能再放消息")
            message = dataclasses.replace(message, seq=self._next_seq)
            self._next_seq += 1
            self._queue.append(message)
            self._publish(MESSAGE_PUT, message)
            self._cond.notify_all()
            return message

    def take(self, match: Callable[[Message], bool], block: bool, waiter: int | None = None) -> Message | None:
        """取第一条匹配的消息。阻塞模式下，进入时与返回前各发一条「邮箱等待」追踪事件，
        不论是否真的等过；waiter 是在取的行动编号，由调用方给出。"""
        with self._cond:
            if block:
                started = time.monotonic()
                self._publish_wait(waiter, "begin")
            while True:
                for index, message in enumerate(self._queue):
                    if match(message):
                        del self._queue[index]
                        if block:
                            self._publish_wait(waiter, "end", started)
                        self._publish(MESSAGE_TAKEN, message)
                        return message
                if self._closed or not block:
                    if block:
                        self._publish_wait(waiter, "end", started)
                    return None
                self._cond.wait()

    def close(self, sender: str) -> None:
        """关闭邮箱：表示不会再有消息。第一次关闭发「邮箱关闭」，重复关闭不再发。"""
        if not _valid_sender(sender):
            raise KernelError(f"关闭邮箱的发起方不合法：{sender!r}")
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._publisher.publish(MAILBOX_CLOSED, {"box": self.box, "sender": sender})
            self._cond.notify_all()

    def _publish(self, name: str, message: Message) -> None:
        payload = {"box": self.box, "kind": message.kind, "sender": message.sender, "recipient": message.recipient,
                   "action_id": message.action_id, "in_reply_to": message.in_reply_to,
                   "content": message.content, "seq": message.seq}
        if message.action_id is not None:
            action_id = message.action_id
        else:
            action_id = message.recipient if _is_action_id(message.recipient) else None
        self._publisher.publish(name, payload, action_id=action_id)

    def _publish_wait(self, waiter, phase: str, started: float | None = None) -> None:
        payload = {"box": self.box, "action_id": waiter, "phase": phase, "thread": threading.current_thread().name}
        if started is not None:
            payload["wait_ms"] = round((time.monotonic() - started) * 1000, 3)
        self._publisher.publish(MAILBOX_WAIT, payload, action_id=waiter)


def new_task(task_id, task_def, tools, inbox, outbox, stream) -> Task:
    """只建对象，状态未开始。初始化变更组按任务定义的槽位表生成，存在任务上。"""
    if stream.task_id != task_id:
        raise KernelError("事件流绑定的任务标识与任务标识不一致")
    if inbox.box != INBOX or outbox.box != OUTBOX:
        raise KernelError("收件箱与发件箱的箱名不对")
    init_changes = [Change(slot, None, copy.deepcopy(value), INIT_SOURCE) for slot, value in task_def.SLOTS.items()]
    return Task(
        task_id=task_id,
        task_def=task_def,
        tools=tools,
        inbox=inbox,
        outbox=outbox,
        stream=stream,
        init_changes=init_changes,
        loop_publisher=stream.bind(SOURCE_LOOP),
        update_publisher=stream.bind(SOURCE_UPDATE),
    )


_ALLOWED_TASK_TRANSITIONS = {
    (TaskStatus.NOT_STARTED, TaskStatus.RUNNING),
    (TaskStatus.RUNNING, TaskStatus.DONE),
}


def update_state(task: Task, item) -> None:
    """唯一写入口。更新项二选一：变更组（Change 列表），或任务状态（TaskStatus）。"""
    if isinstance(item, TaskStatus):
        old = task.status
        if (old, item) not in _ALLOWED_TASK_TRANSITIONS:
            raise KernelError(f"不允许的任务状态变化：{old.value} → {item.value}")
        task.status = item
        task.update_publisher.publish(TASK_STATUS_CHANGED, {"old_status": old, "new_status": item})
        return
    if isinstance(item, list):
        for change in item:
            if not isinstance(change, Change):
                raise KernelError(f"变更组里有不是变更的项：{change!r}")
            current = task.data.get(change.slot)
            if current != change.old:
                raise KernelError(f"旧值不符：槽位 {change.slot!r} 当前为 {current!r}，变更声明为 {change.old!r}")
            task.data[change.slot] = change.new
            source_action = change.source if _is_action_id(change.source) else None
            task.update_publisher.publish(
                DATA_CHANGED,
                {"slot": change.slot, "old": change.old, "new": change.new, "source": change.source},
                action_id=source_action,
            )
        return
    raise KernelError(f"更新项既不是变更组也不是任务状态：{item!r}")


def _is_action_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_done(task: Task) -> bool:
    """任务结果检查：只调任务定义的完成条件函数并返回结果，不写任何东西。"""
    return bool(task.task_def.is_done(types.MappingProxyType(task.data)))


def set_status(task: Task, action: Action, status: ActionStatus, note: str, publisher: Publisher | None = None) -> None:
    """记状态：行动状态的唯一改法。改当前状态并发「行动状态变化」；终态时事件带返回值。

    publisher 是记录方的发布句柄：内核自己调用时为空，用 kernel.loop；
    工具实现经执行上下文拿到的版本预先绑好 tool.<工具名>。
    """
    if not isinstance(status, ActionStatus):
        raise KernelError(f"行动状态不合法：{status!r}")
    action.status = status
    payload = {"new_status": status, "note": note}
    if status in TERMINAL_ACTION_STATUSES:
        payload["result"] = action.result
    (publisher or task.loop_publisher).publish(ACTION_STATUS_CHANGED, payload, action_id=action.action_id)


def select_action(task: Task) -> Candidate:
    """行动选择：调选择规则，到工具表核对工具名与参数名，返回候选。

    只返回候选，不创建行动、不发状态事件、不改任务。选择规则返回空、工具未登记、
    参数名不符都是任务定义错误：先发「任务定义错误」追踪事件，再抛携带原始输出的内核错误。
    """
    raw = task.task_def.select_action(types.MappingProxyType(task.data))
    if raw is None:
        _definition_error(task, "选择规则返回空", raw)
    if not (isinstance(raw, tuple) and len(raw) in (3, 4)):
        _definition_error(task, "选择规则的返回不是（工具名, 参数, 依据）三元组或（工具名, 参数, 依据, 游标新值）四元组", raw)
    tool_name, params, basis = raw[:3]
    cursor_update = raw[3] if len(raw) == 4 else None
    if cursor_update is not None and not (isinstance(cursor_update, tuple) and len(cursor_update) == 2
                                          and isinstance(cursor_update[0], str)):
        _definition_error(task, "游标新值不是（槽位名, 新值）二元组", raw)
    tool = task.tools.get(tool_name)
    if tool is None:
        _definition_error(task, f"工具未登记：{tool_name!r}", raw)
    if not isinstance(params, dict) or set(params) != set(tool.param_names):
        _definition_error(task, f"参数名与工具的参数名清单不符：{tool_name!r}", raw)
    return Candidate(tool=tool_name, params=dict(params), basis=basis, proposer="selector", cursor_update=cursor_update)


def register_action(task: Task, candidate: Candidate) -> int:
    """登记行动：写入点之一。把候选包成行动，编号取任务的下一个编号并递增，放进任务的行动表，
    同一处发「行动提出」，再记「已提出」。返回行动编号，之后按编号从行动表取用。

    候选可以来自行动选择，将来也可以来自使用者的主动输入；登记是所有候选共用的入口。
    """
    action = Action(
        action_id=task.next_action_id,
        tool=candidate.tool,
        params=dict(candidate.params),
        proposer=candidate.proposer,
        basis=candidate.basis,
    )
    task.next_action_id += 1
    task.actions[action.action_id] = action
    task.loop_publisher.publish(
        ACTION_PROPOSED,
        {"tool": action.tool, "params": action.params, "proposer": action.proposer, "basis": action.basis},
        action_id=action.action_id,
    )
    set_status(task, action, ActionStatus.PROPOSED, "行动选择")
    return action.action_id


def _definition_error(task: Task, reason: str, raw) -> None:
    """任务定义错误：先发追踪事件，再抛内核错误。此时还没有行动。"""
    task.loop_publisher.publish(TASK_DEFINITION_ERROR, {"reason": reason, "raw_output": raw})
    raise KernelError(reason, raw=raw)


CONTROL_CHECKED = "恒允许"  # 本步执行控制核验的内容


def control(task: Task, action: Action) -> None:
    """执行控制：本步恒允许，结果照记。"""
    set_status(task, action, ActionStatus.APPROVED, f"本步{CONTROL_CHECKED}")


@dataclass(frozen=True)
class ExecContext:
    """工具实现拿到的全部东西：本行动、任务数据的只读视图、收件箱、发件箱、绑好任务、本行动与记录方的记状态。"""

    action: Action
    data_view: types.MappingProxyType
    inbox: Any
    outbox: Any
    set_status: Callable[[ActionStatus, str], None]


def execute(task: Task, action: Action) -> None:
    """行动执行：按工具名取工具，组装执行上下文，调用工具实现。

    工具实现经记状态改行动状态，并填返回值与变更组；它必须把行动置到终态。
    """
    tool = task.tools.get(action.tool)
    if tool is None:
        raise KernelError(f"工具未登记：{action.tool!r}", action=action)
    ctx = ExecContext(
        action=action,
        data_view=types.MappingProxyType(task.data),
        inbox=task.inbox,
        outbox=task.outbox,
        set_status=functools.partial(
            set_status, task, action,
            publisher=task.stream.bind(TOOL_SOURCE_PREFIX + action.tool),
        ),
    )
    thread = threading.current_thread().name
    call = {"action_id": action.action_id, "tool": action.tool, "thread": thread}
    task.loop_publisher.publish(EXECUTE_CALL, {**call, "phase": "enter"}, action_id=action.action_id)
    tool.impl(ctx)
    task.loop_publisher.publish(EXECUTE_CALL, {**call, "phase": "return"}, action_id=action.action_id)
    if action.status not in TERMINAL_ACTION_STATUSES:
        raise KernelError("工具实现返回时没有把行动置到终态", action=action)


def record_end(task: Task, reason: str) -> None:
    """运行记录的投影：写结束记录（终态取当时的任务状态）并发「任务结束」。"""
    task.end_record = EndRecord(task.status, reason)
    task.loop_publisher.publish(TASK_ENDED, {"final_status": task.status, "reason": reason})


def start_task(task_id, task_def, tools, inbox, outbox, stream) -> Task:
    """唯一入口：调用一次，跑到终态返回任务；行动已失败时抛内核错误（失败的行动从登记起就在行动表里）。

    任务结束或抛内核错误时，内核关闭发件箱（发起方 kernel.loop），告诉外部不会再有问题。
    循环内对 update_state、execute 等函数的调用都经模块全局名，不在这里绑定局部引用。
    """
    task = new_task(task_id, task_def, tools, inbox, outbox, stream)
    try:
        return _run(task)
    except KernelError:
        outbox.close(SOURCE_LOOP)
        raise


def _run(task: Task) -> Task:
    """循环本体。正常完成时关闭发件箱后写结束记录；出错时由 start_task 关闭发件箱。"""
    task_def, tools = task.task_def, task.tools
    # 追踪：任务开始。槽位表、任务定义结构、工具清单都从任务定义与工具表读。
    loop = task.loop_publisher
    loop.publish(TASK_STARTED, {
        "slots": dict(task_def.SLOTS),
        "definition": copy.deepcopy(task_def.DEFINITION),
        "tools": {name: list(tool.param_names) for name, tool in tools.items()},
        "task_def_name": task_def.NAME,
    })
    update_state(task, task.init_changes)
    update_state(task, TaskStatus.RUNNING)
    # 进循环前先做一次结果检查（不发「迭代开始」）：初始即完成时不进循环。
    if _check_and_finish(task):
        return task
    loop_no = 0  # 只用于追踪事件的迭代序号，不是对象
    while True:
        loop_no += 1
        loop.publish(LOOP_STARTED, {"loop_no": loop_no})
        # 第 1 步：行动选择只返回候选，登记行动是写入点。迭代开头取主动类消息的位置留在这里，本步不实现。
        candidate = select_action(task)
        action_id = register_action(task, candidate)
        action = task.actions[action_id]
        # 第 2 步：执行控制。
        control(task, action)
        loop.publish(
            CONTROL_RESULT,
            {"action_id": action.action_id, "verdict": action.status, "checked": CONTROL_CHECKED},
            action_id=action.action_id,
        )
        # 第 3 步：行动执行；未获准时不执行，变更组保持空列表。
        if action.status == ActionStatus.APPROVED:
            execute(task, action)
        # 第 4 步：状态更新。行动在登记时已经在行动表里，这里不再单独记录。
        # 候选带游标新值且行动已成功时，把它作为一条变更接在工具的变更组后面一起写入，来源是同一个行动编号；
        # 行动失败或被拒绝时不写游标。
        if candidate.cursor_update is not None and action.status == ActionStatus.SUCCEEDED:
            slot, new = candidate.cursor_update
            action.changes = list(action.changes) + [Change(slot, copy.deepcopy(task.data.get(slot)), new, action.action_id)]
        update_state(task, action.changes)
        if action.status == ActionStatus.FAILED:
            raise KernelError("行动失败", action=action)
        # 第 5 步：结果检查挪到每次迭代末尾，紧跟状态更新；成立就结束，所以迭代数等于行动数。
        if _check_and_finish(task):
            return task


def _check_and_finish(task: Task) -> bool:
    """结果检查只判不写；成立时写完成状态、关发件箱、写结束记录。返回是否已完成。"""
    done = check_done(task)
    task.loop_publisher.publish(CHECK_DONE_RESULT, {"done": done})
    if done:
        update_state(task, TaskStatus.DONE)
        task.outbox.close(SOURCE_LOOP)
        record_end(task, "完成条件成立")
    return done
