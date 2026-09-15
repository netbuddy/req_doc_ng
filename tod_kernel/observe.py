"""观测：订阅者与重放。加新的观测手段只加订阅者。"""

from __future__ import annotations

import datetime
import enum
import json
import re
from dataclasses import dataclass
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
    """文件订阅者：每次运行写一份文件 <dir>/<任务标识>_<开始时刻>.jsonl，每个事件一行 JSON，永不覆盖。

    - 开始时刻是本订阅者收到该次运行的「任务开始」事件时自己取的挂钟时间，精确到毫秒，
      格式 20260914T153012123；事件对象本身不带挂钟时间。
    - 「任务开始」之前到达的事件（例如宿主读发件箱时的邮箱等待）先缓存，定名后按原序一起写入。
    - 每次运行都有自己的事件流，序号从 1 开始；所以同一任务标识收到的序号不大于上次见过的序号时，
      认定新一次运行开始，另起一份文件。
    - 同一毫秒内定名冲突时，在时刻后加 _2、_3……；以独占方式创建文件，保证不覆盖已有文件。
    - 一次运行始终没有发出「任务开始」时，缓存的事件不写文件。
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self._runs: dict[str, dict] = {}  # 任务标识 → {"last_seq", "path", "buffer"}
        self.paths: list[Path] = []  # 本实例写出的全部文件，按定名顺序

    def path_for(self, task_id: str) -> Path | None:
        """该任务标识当前这次运行的文件路径；还没收到「任务开始」时为 None。"""
        run = self._runs.get(task_id)
        return run["path"] if run else None

    def __call__(self, event: Event) -> None:
        run = self._runs.get(event.task_id)
        if run is None or event.seq <= run["last_seq"]:
            run = {"last_seq": 0, "path": None, "buffer": []}
            self._runs[event.task_id] = run
        run["last_seq"] = event.seq
        line = json.dumps({
            "seq": event.seq,
            "ts": event.ts,
            "task_id": event.task_id,
            "action_id": event.action_id,
            "kind": event.kind,
            "source": event.source,
            "name": event.name,
            "payload": event.payload,
        }, ensure_ascii=False, default=_to_json) + "\n"
        if run["path"] is not None:
            with open(run["path"], "a", encoding="utf-8") as handle:
                handle.write(line)
            return
        run["buffer"].append(line)
        if event.name == TASK_STARTED:
            run["path"] = self._create(event.task_id, "".join(run["buffer"]))
            run["buffer"] = []

    def _create(self, task_id: str, content: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now()
        stamp = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}"
        suffix = 1
        while True:
            name = f"{task_id}_{stamp}" + ("" if suffix == 1 else f"_{suffix}") + ".jsonl"
            path = self.directory / name
            try:
                with open(path, "x", encoding="utf-8") as handle:
                    handle.write(content)
            except FileExistsError:
                suffix += 1
                continue
            self.paths.append(path)
            return path


# ───────────────────────── 运行索引 ─────────────────────────

STAMP_PATTERN = re.compile(r"_(\d{8}T\d{9})(?:_\d+)?\.jsonl$")

FINISHED_ABNORMALLY = "内核错误"
NOT_FINISHED = "未结束"


@dataclass(frozen=True)
class RunSummary:
    """运行索引的一行，全部从运行文件里的事件算出，不另存。"""

    file: str
    task_def_name: str | None
    task_id: str | None
    started_at: str  # 形如 2026-09-14 15:30:12.123
    final_status: str  # 已完成、内核错误、未结束
    reason: str
    loops: int
    actions: int
    events: int
    duration_ms: float
    started_at_source: str  # 「文件名」或「文件修改时间」（旧命名的文件没有开始时刻）


def summarize(path) -> RunSummary:
    """读一份运行文件算运行摘要。

    终态分三种情况：有「任务结束」取它的终态与原因；没有「任务结束」但内核一侧关闭了发件箱
    或出现「任务定义错误」，记「内核错误」，原因优先取任务定义错误的原因，其次取最后一条「已失败」
    的说明；两者都没有，记「未结束」。
    """
    path = Path(path)
    events = read_events(path)
    started = next((e for e in events if e.name == TASK_STARTED), None)
    ended = [e for e in events if e.name == TASK_ENDED]
    if ended:
        final_status, reason = str(ended[-1].payload["final_status"]), ended[-1].payload["reason"]
    else:
        definition_errors = [e for e in events if e.name == TASK_DEFINITION_ERROR]
        kernel_closed_outbox = any(
            e.name == MAILBOX_CLOSED and e.payload.get("box") == OUTBOX and e.payload.get("sender") not in EXTERNAL_SENDERS
            for e in events
        )
        failed = [e for e in events if e.name == ACTION_STATUS_CHANGED and str(e.payload["new_status"]) == "已失败"]
        if definition_errors or kernel_closed_outbox:
            final_status = FINISHED_ABNORMALLY
            reason = definition_errors[-1].payload["reason"] if definition_errors else (failed[-1].payload["note"] if failed else "")
        else:
            final_status, reason = NOT_FINISHED, ""
    match = STAMP_PATTERN.search(path.name)
    if match:
        stamp = match.group(1)
        started_at = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}.{stamp[15:18]}"
        source = "文件名"
    else:
        moment = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        started_at = moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"
        source = "文件修改时间"
    return RunSummary(
        file=path.name,
        task_def_name=started.payload["task_def_name"] if started else None,
        task_id=events[0].task_id if events else None,
        started_at=started_at,
        final_status=final_status,
        reason=reason,
        loops=sum(1 for e in events if e.name == LOOP_STARTED),
        actions=sum(1 for e in events if e.name == ACTION_PROPOSED),
        events=len(events),
        duration_ms=round((events[-1].ts - events[0].ts) * 1000, 3) if events else 0.0,
        started_at_source=source,
    )


def list_runs(directory) -> list[RunSummary]:
    """扫目录，每份 .jsonl 文件一条摘要，按开始时刻倒序（开始时刻相同时按文件名倒序）。"""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    summaries = [summarize(path) for path in directory.glob("*.jsonl") if path.is_file()]
    return sorted(summaries, key=lambda s: (s.started_at, s.file), reverse=True)


# ───────────────────────── 本地服务 ─────────────────────────

PAGE_FILE = Path(__file__).resolve().parent / "observatory.html"
DEFAULT_PORT = 8765


def _run_file_or_none(directory: Path, name: str) -> Path | None:
    """文件名白名单：只接受 runs 目录里现有的 .jsonl 文件名本身，不含路径分隔符与 ..。"""
    if not name or "/" in name or "\\" in name or ".." in name or not name.endswith(".jsonl"):
        return None
    candidate = directory / name
    if not candidate.is_file() or candidate.resolve().parent != directory.resolve():
        return None
    return candidate


def make_server(directory, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
    """建观测台服务但不启动。三个接口：GET / 页面；GET /api/runs 运行索引；GET /api/runs/<文件名> 该运行的全部事件。"""
    import http.server
    import urllib.parse

    directory = Path(directory)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status: int = 200) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802（标准库要求的方法名）
            raw_path = self.path.split("?", 1)[0]  # 用原始请求路径判断，不做规范化
            if raw_path in ("/", "/index.html"):
                if not PAGE_FILE.is_file():
                    self._json({"error": "观测台页面文件不存在"}, 404)
                    return
                self._send(200, PAGE_FILE.read_bytes(), "text/html; charset=utf-8")
            elif raw_path == "/api/runs":
                self._json([summary.__dict__ for summary in list_runs(directory)])
            elif raw_path.startswith("/api/runs/"):
                name = urllib.parse.unquote(raw_path[len("/api/runs/"):])
                path = _run_file_or_none(directory, name)
                if path is None:
                    self._json({"error": "没有这份运行文件"}, 404)
                    return
                with open(path, encoding="utf-8") as handle:
                    self._json([json.loads(line) for line in handle if line.strip()])
            else:
                self._json({"error": "没有这个地址"}, 404)

        def log_message(self, format, *args) -> None:  # 访问日志照常打到标准错误
            super().log_message(format, *args)

    return http.server.ThreadingHTTPServer((host, port), Handler)


def local_addresses(port: int) -> list[str]:
    """本机可访问的地址：本机各网卡的 IPv4 地址，不含回环。"""
    import socket

    found = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))  # 不真正发包，只为让系统选出默认出口地址
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    addresses = []
    for ip in found:
        if not ip.startswith("127.") and ip not in addresses:
            addresses.append(ip)
    return [f"http://{ip}:{port}/" for ip in addresses]


def serve(directory="runs", host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    """起观测台服务并一直运行，启动时打印本机可访问的地址。"""
    server = make_server(directory, host, port)
    actual_port = server.server_address[1]
    print(f"观测台已启动，监听 {host}:{actual_port}，运行目录 {Path(directory).resolve()}", flush=True)
    for address in local_addresses(actual_port) or [f"http://<本机地址>:{actual_port}/"]:
        print(f"  {address}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


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
                    f"定义 {p['definition']}，工具 {p['tools']}")
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


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tod_kernel.observe", description="任务型智能体内核的观测台")
    commands = parser.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve", help="起观测台服务")
    serve_parser.add_argument("--dir", default="runs", help="运行文件目录，默认 runs")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口，默认 {DEFAULT_PORT}")
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.dir, "0.0.0.0", args.port)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
