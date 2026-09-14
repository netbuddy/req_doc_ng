"""观测：订阅者与重放。加新的观测手段只加订阅者。"""

from __future__ import annotations

import enum
import json
from pathlib import Path

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
    EXTERNAL_SENDERS,
    OUTBOX,
    TOOL_SOURCE_PREFIX,
    Event,
    TaskStatus,
)

# 「谁引起」的两条规则：
# 1. 放入、关闭是发起方本人做的事，按内容里的 sender 判：host、user 是外部，其余是内核一侧。
# 2. 取出、邮箱等待是来取的一方做的事，内容里的 sender 说的是消息原来的发起方，不是来取的人，
#    所以按箱名判：收件箱只由内核一侧取，发件箱只由外部取。
SENDER_ACTS = (MESSAGE_PUT, MAILBOX_CLOSED)
READER_ACTS = (MESSAGE_TAKEN, MAILBOX_WAIT)
OUTBOX_READER = "读取发件箱的一方"

BOX_LABELS = {"inbox": "收件箱", "outbox": "发件箱"}


def initiator_of(name: str, payload: dict, source: str) -> str:
    """谁引起了这件事：放入、关闭返回 sender；发件箱上的取出与等待返回「读取发件箱的一方」；其余返回记录方。"""
    if name in SENDER_ACTS:
        return payload["sender"]
    if name in READER_ACTS and payload.get("box") == OUTBOX:
        return OUTBOX_READER
    return source


def is_external(name: str, payload: dict) -> bool:
    """是否由内核之外的一方（宿主或使用者）引起。"""
    if name in SENDER_ACTS:
        return payload["sender"] in EXTERNAL_SENDERS
    if name in READER_ACTS:
        return payload.get("box") == OUTBOX
    return False


def is_question_sent(name: str, payload: dict) -> bool:
    """内核一侧往发件箱放问题：页面上标「向外部提问」。"""
    return name == MESSAGE_PUT and payload.get("box") == OUTBOX and payload["sender"] not in EXTERNAL_SENDERS


def replay_data(events) -> dict:
    """重放：从空数据起按序应用给定事件里的每个「数据变更」，得到那一刻的任务数据。

    只取状态事件，追踪事件不参与。要得到某一时刻的数据，传入该时刻之前的事件切片。
    """
    data: dict = {}
    for event in sorted(events, key=lambda e: e.seq):
        if event.kind == STATE and event.name == DATA_CHANGED:
            data[event.payload["slot"]] = event.payload["new"]
    return data


def action_history(events, action_id) -> list[Event]:
    """某个行动的状态经过：按行动编号过滤出的「行动状态变化」事件，按序号排列。"""
    return sorted(
        (e for e in events if e.kind == STATE and e.name == ACTION_STATUS_CHANGED and e.action_id == action_id),
        key=lambda e: e.seq,
    )


def read_events(path) -> list[Event]:
    """把文件订阅者写出的 JSONL 读回事件对象。

    读回后状态枚举是中文字符串、元组是列表、字典的整数键是字符串；比对状态时按中文值比。
    """
    events = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(Event(**json.loads(line)))
    return events


def _to_json(value):
    """JSON 序列化兜底：枚举写中文值，其余无法序列化的值用 str()。"""
    if isinstance(value, enum.Enum):
        return value.value
    return str(value)


class FileWriter:
    """文件订阅者：每个事件序列化成一行 JSON，追加到 <dir>/<任务标识>.jsonl。

    同一个实例第一次遇到某个任务标识时清空重写该文件，之后逐行追加，
    所以重跑不会把多次运行的记录累积在一个文件里。
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self._started: set[str] = set()

    def path_for(self, task_id: str) -> Path:
        return self.directory / f"{task_id}.jsonl"

    def __call__(self, event: Event) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        mode = "a" if event.task_id in self._started else "w"
        self._started.add(event.task_id)
        record = {
            "seq": event.seq,
            "ts": event.ts,
            "task_id": event.task_id,
            "action_id": event.action_id,
            "kind": event.kind,
            "source": event.source,
            "name": event.name,
            "payload": event.payload,
        }
        with open(self.path_for(event.task_id), mode, encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=_to_json) + "\n")


class MemoryCollector:
    """把事件存成列表，供断言与重放。"""

    def __init__(self):
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)


_LABELS = {
    TASK_STATUS_CHANGED: "任务状态变化",
    DATA_CHANGED: "数据变更",
    ACTION_PROPOSED: "行动提出",
    ACTION_STATUS_CHANGED: "行动状态变化",
    MESSAGE_PUT: "消息放入",
    MESSAGE_TAKEN: "消息取出",
    MAILBOX_CLOSED: "邮箱关闭",
    TASK_ENDED: "任务结束",
    TASK_STARTED: "任务开始",
    LOOP_STARTED: "一圈开始",
    CHECK_DONE_RESULT: "结果检查结论",
    CONTROL_RESULT: "执行控制结论",
    EXECUTE_CALL: "行动执行调用",
    MAILBOX_WAIT: "邮箱等待",
    TASK_DEFINITION_ERROR: "任务定义错误",
}


def _show(value) -> str:
    if value is None:
        return "未填写(None)"
    if isinstance(value, enum.Enum):
        return value.value
    return repr(value)


class ConsolePrinter:
    """按段打印：先一段初始化，然后每个行动一段，最后一段结束。

    段的划分只看事件自身：行动编号不为空的事件归该行动的段；行动编号为空的状态事件，
    在任务置为执行中之前（含这一条）归初始化段，之后归结束段。行动编号为空的追踪事件留在
    当前段里打印，不另起分段；「一圈开始」打一条分隔线，并让下一段重新打印标题。
    追踪事件行首用「·」标出，状态事件行首用「#」。由外部（宿主或使用者）引起的事件行首用「⇢」，
    并写明「外部」与发起方；工具实现发布的事件多缩进一格。每行末尾用〔〕写出记录方。
    只打印事件里有的内容。
    """

    def __init__(self):
        self._segment = None
        self._init_done = False

    def __call__(self, event: Event) -> None:
        if event.name == LOOP_STARTED:
            print(f"┈┈ 第 {event.payload['loop_no']} 圈开始（事件 {event.seq}，追踪） ┈┈", flush=True)
            self._segment = None
            return
        is_state = event.kind == STATE
        if event.action_id is not None:
            segment = f"行动 {event.action_id}"
        elif not is_state or is_external(event.name, event.payload):
            # 追踪事件与外部引起的事件不带行动编号时，不另起分段，留在当前段里打印。
            segment = self._segment if (self._segment or self._init_done) else "初始化"
        elif not self._init_done:
            segment = "初始化"
        else:
            segment = "结束"
        if segment is not None and segment != self._segment:
            self._segment = segment
            print(f"── {segment} ──", flush=True)
        mark = "#" if is_state else "·"
        tail = f"〔{event.source}〕"
        if is_external(event.name, event.payload):
            who = initiator_of(event.name, event.payload, event.source)
            print(f"  ⇢ {mark}{event.seq} 外部（{who}）· {_LABELS[event.name]}：{self._describe(event)} {tail}", flush=True)
        elif is_question_sent(event.name, event.payload):
            print(f"  ↗ {mark}{event.seq} 向外部提问 · {_LABELS[event.name]}：{self._describe(event)} {tail}", flush=True)
        else:
            indent = "    " if event.source.startswith(TOOL_SOURCE_PREFIX) else "  "
            print(f"{indent}{mark}{event.seq} {_LABELS[event.name]}：{self._describe(event)} {tail}", flush=True)
        if event.name == TASK_STATUS_CHANGED and event.payload["new_status"] == TaskStatus.RUNNING:
            self._init_done = True

    @staticmethod
    def _describe(event: Event) -> str:
        p = event.payload
        if event.name == TASK_STATUS_CHANGED:
            return f"{_show(p['old_status'])} → {_show(p['new_status'])}"
        if event.name == DATA_CHANGED:
            return f"槽位「{p['slot']}」 {_show(p['old'])} → {_show(p['new'])}，来源 {p['source']}"
        if event.name == ACTION_PROPOSED:
            return f"工具 {p['tool']}，参数 {p['params']}，提出者 {p['proposer']}，依据 {p['basis']}"
        if event.name == ACTION_STATUS_CHANGED:
            text = f"{_show(p['new_status'])}，说明：{p['note']}"
            if "result" in p:
                text += f"，返回值 {p['result']!r}"
            return text
        if event.name in (MESSAGE_PUT, MESSAGE_TAKEN):
            text = (f"{BOX_LABELS[p['box']]}，类型 {p['kind']}，发起方 {p['sender']}，收件人 {p['recipient']}，"
                    f"内容 {_show(p['content'])}，到达序号 {p['seq']}")
            if p["in_reply_to"] is not None:
                text += f"，回复问题 {p['in_reply_to']}"
            return text
        if event.name == MAILBOX_CLOSED:
            return f"{BOX_LABELS[p['box']]}，发起方 {p['sender']}，不会再有消息"
        if event.name == TASK_ENDED:
            return f"终态 {_show(p['final_status'])}，原因：{p['reason']}"
        if event.name == TASK_STARTED:
            return (f"任务定义 {p['task_def_name']}，槽位表 {p['slots']}，"
                    f"规则 {p['rules']}，工具 {p['tools']}")
        if event.name == CHECK_DONE_RESULT:
            return f"已完成={p['done']}"
        if event.name == CONTROL_RESULT:
            return f"行动 {p['action_id']}，结论 {_show(p['verdict'])}，核验：{p['checked']}"
        if event.name == EXECUTE_CALL:
            return f"行动 {p['action_id']}，工具 {p['tool']}，阶段 {p['phase']}，线程 {p['thread']}"
        if event.name == MAILBOX_WAIT:
            text = f"{BOX_LABELS[p['box']]}，行动 {p['action_id']}，阶段 {p['phase']}，线程 {p['thread']}"
            if "wait_ms" in p:
                text += f"，等待 {p['wait_ms']} 毫秒"
            return text
        if event.name == TASK_DEFINITION_ERROR:
            return f"原因：{p['reason']}，选择规则原始输出 {p['raw_output']!r}"
        return repr(p)
