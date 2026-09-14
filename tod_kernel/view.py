"""查看器：读一份运行记录 JSONL，生成同名的自包含 HTML 页。

用法：在仓根下运行 `python -m tod_kernel.view runs/<文件>.jsonl`。

页面不依赖内核在跑，不需要服务，不联网，不引外部资源。任务名、槽位名、工具名全部来自文件；
本模块只认识内核固定的事件名词表与循环五步的名称。

循环时间线按事件切分：「一圈开始」开一圈；「结果检查结论」之后进入第 2 步（结论为真时，
其后的事件都留在第 1 步）；第 2 步到「行动提出」之后的第一条「已提出」状态变化为止，之后进入第 3 步；
「执行控制结论」之后进入第 4 步；「行动执行调用」的 return 之后进入第 5 步。

时间线每一行分左右两栏：左栏是内核一侧（记录方为 kernel.* 的事件，工具实现发布的事件缩进显示），
右栏是外部（由宿主或使用者引起的事件，按消息的发起方标出）。外部事件发生时，若内核正处在
「邮箱等待」的开始与结束之间，左栏标出「内核在此阻塞，等待外部输入」。
"""

from __future__ import annotations

import html
import json
import sys
from collections import OrderedDict
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
    TOOL_SOURCE_PREFIX,
    ActionStatus,
)
from tod_kernel.observe import BOX_LABELS, initiator_of, is_external, is_question_sent

LABELS = {
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

STEP_NAMES = ("第 1 步 结果检查", "第 2 步 行动选择", "第 3 步 执行控制", "第 4 步 行动执行", "第 5 步 状态更新与记录")


# ───────────────────────── 读文件与整理 ─────────────────────────


def load(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    return sorted(events, key=lambda e: e["seq"])


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def show(value) -> str:
    """值的显示：None 写成 None，字符串加引号，其余用 JSON 写法。"""
    if value is None:
        return "None"
    if isinstance(value, str):
        return f"“{value}”"
    return json.dumps(value, ensure_ascii=False)


def describe(e: dict) -> str:
    p, name = e["payload"], e["name"]
    if name == TASK_STATUS_CHANGED:
        return f"{p['old_status']} → {p['new_status']}"
    if name == DATA_CHANGED:
        return f"槽位「{p['slot']}」 {show(p['old'])} → {show(p['new'])}，来源 {p['source']}"
    if name == ACTION_PROPOSED:
        return f"工具 {p['tool']}，参数 {show(p['params'])}，提出者 {p['proposer']}，依据 {show(p['basis'])}"
    if name == ACTION_STATUS_CHANGED:
        text = f"{p['new_status']}，说明：{p['note']}"
        if "result" in p:
            text += f"，返回值 {show(p['result'])}"
        return text
    if name in (MESSAGE_PUT, MESSAGE_TAKEN):
        text = (f"{BOX_LABELS[p['box']]}，类型 {p['kind']}，发起方 {p['sender']}，收件人 {p['recipient']}，"
                f"内容 {show(p['content'])}，到达序号 {p['seq']}")
        if p["in_reply_to"] is not None:
            text += f"，回复问题 {p['in_reply_to']}"
        return text
    if name == MAILBOX_CLOSED:
        return f"{BOX_LABELS[p['box']]}，发起方 {p['sender']}，不会再有消息"
    if name == TASK_ENDED:
        return f"终态 {p['final_status']}，原因：{p['reason']}"
    if name == TASK_STARTED:
        return f"任务定义 {p['task_def_name']}"
    if name == LOOP_STARTED:
        return f"第 {p['loop_no']} 圈"
    if name == CHECK_DONE_RESULT:
        return f"已完成 = {'是' if p['done'] else '否'}"
    if name == CONTROL_RESULT:
        return f"行动 {p['action_id']}，结论 {p['verdict']}，核验：{p['checked']}"
    if name == EXECUTE_CALL:
        return f"行动 {p['action_id']}，工具 {p['tool']}，阶段 {p['phase']}，线程 {p['thread']}"
    if name == MAILBOX_WAIT:
        text = f"{BOX_LABELS[p['box']]}，行动 {p['action_id']}，阶段 {p['phase']}，线程 {p['thread']}"
        if "wait_ms" in p:
            text += f"，等待 {p['wait_ms']} 毫秒"
        return text
    if name == TASK_DEFINITION_ERROR:
        return f"原因：{p['reason']}，选择规则原始输出 {show(p['raw_output'])}"
    return show(p)


class Record:
    """从事件列表整理出页面要用的结构。"""

    def __init__(self, events: list[dict]):
        self.events = events
        self.task_id = events[0]["task_id"] if events else "（空文件）"
        started = [e for e in events if e["name"] == TASK_STARTED]
        self.started = started[0]["payload"] if started else None
        ended = [e for e in events if e["name"] == TASK_ENDED]
        self.ended = ended[-1]["payload"] if ended else None
        self.definition_errors = [e for e in events if e["name"] == TASK_DEFINITION_ERROR]
        self.status_changes = [e for e in events if e["name"] == TASK_STATUS_CHANGED]

        # 行动：以「行动提出」为准，按编号收集相关事件。
        self.actions: OrderedDict[int, dict] = OrderedDict()
        for e in events:
            if e["name"] == ACTION_PROPOSED:
                self.actions[e["action_id"]] = {"proposed": e, "events": []}
        for e in events:
            if e["action_id"] in self.actions:
                self.actions[e["action_id"]]["events"].append(e)

        # 槽位：先按任务开始事件的槽位表排，再补上只在数据变更里出现的槽位。
        slots = list(self.started["slots"]) if self.started else []
        for e in events:
            if e["name"] == DATA_CHANGED and e["payload"]["slot"] not in slots:
                slots.append(e["payload"]["slot"])
        self.slots = slots

        # 每个事件关联到哪些槽位：数据变更本身的槽位；某行动的事件关联该行动改过的槽位
        # 与参数值里出现的槽位名。
        action_slots: dict[int, set] = {aid: set() for aid in self.actions}
        for aid, info in self.actions.items():
            for value in (info["proposed"]["payload"]["params"] or {}).values():
                if isinstance(value, str) and value in slots:
                    action_slots[aid].add(value)
            for e in info["events"]:
                if e["name"] == DATA_CHANGED:
                    action_slots[aid].add(e["payload"]["slot"])
        self.action_slots = action_slots
        self.loops = self._split_loops()

        # 外部事件发生时内核是否正在邮箱上阻塞：处在某个带行动编号的收件箱「邮箱等待」begin 与 end 之间。
        # 宿主在发件箱上的等待不带行动编号，不算内核阻塞。
        open_waits, blocked = set(), set()
        for e in events:
            if e["name"] == MAILBOX_WAIT and e["payload"]["box"] == "inbox" and e["action_id"] is not None:
                (open_waits.add if e["payload"]["phase"] == "begin" else open_waits.discard)(e["payload"]["action_id"])
            elif is_external(e["name"], e["payload"]) and open_waits:
                blocked.add(e["seq"])
        self.blocked_seqs = blocked

    def slots_of(self, e: dict) -> list[str]:
        if e["name"] == DATA_CHANGED:
            return [e["payload"]["slot"]]
        return sorted(self.action_slots.get(e["action_id"], set()), key=self.slots.index)

    def _split_loops(self):
        """返回 (开始段事件, [ {loop_no, steps: [[事件]*5]} ])。"""
        before, loops, current, step = [], [], None, 0
        for e in self.events:
            name = e["name"]
            if name == LOOP_STARTED:
                current = {"loop_no": e["payload"]["loop_no"], "start": e, "steps": [[] for _ in STEP_NAMES]}
                loops.append(current)
                step = 0
                current["steps"][0].append(e)
                continue
            if current is None:
                before.append(e)
                continue
            current["steps"][step].append(e)
            if name == CHECK_DONE_RESULT and not e["payload"]["done"]:
                step = 1
            elif (step == 1 and name == ACTION_STATUS_CHANGED
                  and e["payload"]["new_status"] == ActionStatus.PROPOSED.value):
                step = 2
            elif name == CONTROL_RESULT:
                step = 3
            elif name == EXECUTE_CALL and e["payload"]["phase"] == "return":
                step = 4
        return before, loops


# ───────────────────────── 页面片段 ─────────────────────────


def origin_of(e: dict) -> str:
    """时间线分栏与过滤用：external（外部引起）、tool（工具实现发布）、kernel（内核发布）。"""
    if is_external(e["name"], e["payload"]):
        return "external"
    if e["source"].startswith(TOOL_SOURCE_PREFIX):
        return "tool"
    return "kernel"


def event_line(record: Record, e: dict) -> str:
    kind = e["kind"]
    badge = "状态" if kind == STATE else "追踪"
    action = "" if e["action_id"] is None else str(e["action_id"])
    slots = json.dumps(record.slots_of(e), ensure_ascii=False)
    origin = origin_of(e)
    extra = " is-wait" if e["name"] == MAILBOX_WAIT else ""
    line = (
        f'<div class="line {esc(kind)}{extra}">'
        f'<span class="seq">#{e["seq"]}</span><span class="badge {esc(kind)}">{badge}</span>'
        f'<span class="label">{esc(LABELS.get(e["name"], e["name"]))}</span>'
        f'<span class="desc">{esc(describe(e))}<span class="src">{esc(e["source"])}</span></span></div>'
    )
    if origin == "external":
        who = initiator_of(e["name"], e["payload"], e["source"])
        left = ('<span class="blocked">内核在此阻塞，等待外部输入</span>' if e["seq"] in record.blocked_seqs
                else '<span class="idle">内核一侧此刻没有事件</span>')
        right = f'<span class="ext-tag">外部 · {esc(who)}</span>{line}'
    elif origin == "tool":
        left, right = f'<div class="tool-impl"><span class="tool-tag">工具实现 · {esc(e["source"])}</span>{line}</div>', ""
    elif is_question_sent(e["name"], e["payload"]):
        left, right = f'<div class="ask-out"><span class="ask-tag">向外部提问 · 发件箱</span>{line}</div>', ""
    else:
        left, right = line, ""
    return (
        f'<li class="ev origin-{origin}" data-kind="{esc(kind)}" data-origin="{origin}" data-action="{esc(action)}" data-slots="{esc(slots)}">'
        f'<div class="cell kernel-cell">{left}</div><div class="cell ext-cell">{right}</div></li>'
    )


def section_overview(record: Record) -> str:
    rows = []
    s = record.started
    rows.append(("任务标识", esc(record.task_id)))
    if s:
        rows.append(("任务定义名", esc(s["task_def_name"])))
        slot_items = "".join(f"<li><b>{esc(k)}</b> 初始值 {esc(show(v))}</li>" for k, v in s["slots"].items())
        rows.append((f"槽位表（{len(s['slots'])} 个）", f"<ul class='plain'>{slot_items}</ul>"))
        rule_items = "".join(f"<li>规则 {esc(k)}：{esc(v)}</li>" for k, v in s["rules"].items())
        rows.append((f"选择规则清单（{len(s['rules'])} 条）", f"<ul class='plain'>{rule_items}</ul>"))
        tool_items = "".join(f"<li><b>{esc(k)}</b> 参数名 {esc(show(v))}</li>" for k, v in s["tools"].items())
        rows.append((f"工具清单（{len(s['tools'])} 个）", f"<ul class='plain'>{tool_items}</ul>"))
    else:
        rows.append(("任务开始事件", "文件里没有「任务开始」事件，无法列出槽位表、规则与工具"))
    if record.ended:
        rows.append(("终态与原因", f"<b>{esc(record.ended['final_status'])}</b>，原因：{esc(record.ended['reason'])}"))
    else:
        last = record.status_changes[-1]["payload"]["new_status"] if record.status_changes else "（没有任务状态变化事件）"
        failed = []
        for aid, info in record.actions.items():
            history = [e for e in info["events"] if e["name"] == ACTION_STATUS_CHANGED]
            if history and history[-1]["payload"]["new_status"] == ActionStatus.FAILED.value:
                failed.append(f"行动 {aid} 已失败（{history[-1]['payload']['note']}）")
        text = f"文件里没有「任务结束」事件，任务没有正常结束；最后一次任务状态为 <b>{esc(last)}</b>"
        if failed:
            text += "；" + esc("；".join(failed))
        if record.definition_errors:
            text += "；有任务定义错误：" + esc(record.definition_errors[-1]["payload"]["reason"])
        rows.append(("终态与原因", text))
    n_state = sum(e["kind"] == STATE for e in record.events)
    rows.append(("事件数", f"共 {len(record.events)} 个：状态事件 {n_state} 个，追踪事件 {len(record.events) - n_state} 个"))
    rows.append(("循环", f"共 {len(record.loops[1])} 圈，提出行动 {len(record.actions)} 个"))
    sources: OrderedDict[str, int] = OrderedDict()
    for e in record.events:
        sources[e["source"]] = sources.get(e["source"], 0) + 1
    rows.append(("记录方", "；".join(f"{esc(k)} 发布 {v} 个" for k, v in sources.items())))
    external = [e for e in record.events if is_external(e["name"], e["payload"]) and e["kind"] == STATE]
    if external:
        items = "".join(
            f"<li><span class='seq'>#{e['seq']}</span> <b>{esc(initiator_of(e['name'], e['payload'], e['source']))}</b> "
            f"{esc(LABELS[e['name']])}：{esc(describe(e))}</li>" for e in external
        )
        n_trace = sum(1 for e in record.events if is_external(e["name"], e["payload"]) and e["kind"] != STATE)
        rows.append((f"外部引起的状态事件（{len(external)} 个）",
                     f"<ul class='plain'>{items}</ul><p class='hint'>另有 {n_trace} 个外部引起的追踪事件（读取发件箱时的邮箱等待），见时间线右栏。</p>"))
    else:
        rows.append(("外部引起的事件", "没有"))
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    return f'<section id="overview"><h2>任务概览</h2><table class="kv">{body}</table></section>'


def loop_summary(record: Record, loop: dict) -> str:
    steps = loop["steps"]
    check = [e for e in steps[0] if e["name"] == CHECK_DONE_RESULT]
    if check and check[0]["payload"]["done"]:
        return "结果检查为真，任务结束"
    proposed = [e for e in steps[1] if e["name"] == ACTION_PROPOSED]
    if proposed:
        p = proposed[0]["payload"]
        return f"行动 {proposed[0]['action_id']}：{p['tool']} {show(p['params'])}"
    if any(e["name"] == TASK_DEFINITION_ERROR for e in steps[1]):
        return "任务定义错误，未提出行动"
    return "本圈没有提出行动"


def step_note(steps_events: list[dict]) -> str:
    """步骤名下方的提示：只写内核一侧的阻塞，即带行动编号的收件箱等待。宿主读发件箱的等待不在这里写。"""
    waits = [e for e in steps_events if e["name"] == MAILBOX_WAIT and e["payload"]["phase"] == "end"
             and e["payload"]["box"] == "inbox" and e["action_id"] is not None]
    return "".join(
        f'<span class="wait">内核阻塞等待回答 {esc(w["payload"]["wait_ms"])} 毫秒 · 线程 {esc(w["payload"]["thread"])}</span>'
        for w in waits
    )


def section_timeline(record: Record) -> str:
    before, loops = record.loops
    parts = ['<section id="timeline"><h2>循环时间线</h2>'
             '<div class="lane-head"><span>内核一侧（工具实现发布的事件缩进显示）</span><span>外部（宿主或使用者引起）</span></div>']
    parts.append('<div class="loop" data-loop="start"><h3>开始 <small>第一圈之前：任务开始与初始化</small></h3><ol class="events">')
    parts.extend(event_line(record, e) for e in before)
    parts.append("</ol></div>")
    for loop in loops:
        parts.append(
            f'<div class="loop"><h3>第 {loop["loop_no"]} 圈 <small>{esc(loop_summary(record, loop))}</small></h3>'
            '<div class="steps">'
        )
        for name, events in zip(STEP_NAMES, loop["steps"]):
            lines = "".join(event_line(record, e) for e in events)
            empty = "" if events else '<p class="empty">本步没有发出事件</p>'
            parts.append(
                f'<div class="step"><div class="step-name">{esc(name)}{step_note(events)}</div>'
                f'<div class="step-body"><ol class="events">{lines}</ol>{empty}</div></div>'
            )
        parts.append("</div></div>")
    parts.append("</section>")
    return "".join(parts)


def section_slots(record: Record) -> str:
    changes = [e for e in record.events if e["name"] == DATA_CHANGED]
    head = "".join(f"<th class='num'>#{e['seq']}</th>" for e in changes)
    rows = []
    for slot in record.slots:
        cells = []
        for e in changes:
            p = e["payload"]
            if p["slot"] == slot:
                source = "初始化" if not isinstance(p["source"], int) else f"行动 {p['source']}"
                cells.append(f"<td class='hit'><span class='val'>{esc(show(p['new']))}</span><span class='src'>{esc(source)}</span></td>")
            else:
                cells.append("<td class='miss'>·</td>")
        final = [e for e in changes if e["payload"]["slot"] == slot]
        last = esc(show(final[-1]["payload"]["new"])) if final else "—"
        slots_json = esc(json.dumps([slot], ensure_ascii=False))
        rows.append(f"<tr class='slot-row' data-slots=\"{slots_json}\"><th>{esc(slot)}</th><td class='final'>{last}</td>{''.join(cells)}</tr>")
    table = (
        f"<div class='scroll'><table class='grid'><thead><tr><th>槽位</th><th>最终值</th>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    note = "<p class='hint'>列是全部「数据变更」事件，按事件序号从左到右排列；格子里是该事件写入的新值和来源。</p>"
    return f'<section id="slots"><h2>槽位轨迹</h2>{note}{table}</section>'


def section_actions(record: Record) -> str:
    cards = []
    for aid, info in record.actions.items():
        p = info["proposed"]["payload"]
        events = info["events"]
        history = [e for e in events if e["name"] == ACTION_STATUS_CHANGED]
        takes = [e for e in events if e["name"] == MESSAGE_TAKEN and e["payload"]["box"] == "inbox"]
        questions = [e for e in events if e["name"] == MESSAGE_PUT and e["payload"]["box"] == "outbox"]
        changes = [e for e in events if e["name"] == DATA_CHANGED]
        waits = [e for e in events if e["name"] == MAILBOX_WAIT and e["payload"]["phase"] == "end" and e["payload"]["box"] == "inbox"]
        terminal = [e for e in history if "result" in e["payload"]]
        status_items = "".join(
            f"<li><span class='seq'>#{e['seq']}</span> <b>{esc(e['payload']['new_status'])}</b> · {esc(e['payload']['note'])}</li>"
            for e in history
        )
        take_items = "".join(
            f"<li><span class='seq'>#{e['seq']}</span> {esc(show(e['payload']['content']))}（发起方 {esc(e['payload']['sender'])}，到达序号 {esc(e['payload']['seq'])}，回复问题 {esc(e['payload']['in_reply_to'])}）</li>"
            for e in takes
        ) or "<li class='empty'>没有取到消息</li>"
        question_items = "".join(
            f"<li><span class='seq'>#{e['seq']}</span> 问题 {esc(e['payload']['seq'])}：内容 {esc(show(e['payload']['content']))}，发起方 {esc(e['payload']['sender'])}，收件人 {esc(e['payload']['recipient'])}</li>"
            for e in questions
        ) or "<li class='empty'>没有发出问题</li>"
        change_items = "".join(
            f"<li><span class='seq'>#{e['seq']}</span> 「{esc(e['payload']['slot'])}」 {esc(show(e['payload']['old']))} → {esc(show(e['payload']['new']))}</li>"
            for e in changes
        ) or "<li class='empty'>没有变更</li>"
        wait_text = "；".join(f"{w['payload']['wait_ms']} 毫秒，线程 {w['payload']['thread']}" for w in waits) or "没有邮箱等待"
        result = esc(show(terminal[-1]["payload"]["result"])) if terminal else "（没有终态事件）"
        final = esc(history[-1]["payload"]["new_status"]) if history else "—"
        slots_json = esc(json.dumps(sorted(record.action_slots[aid], key=record.slots.index), ensure_ascii=False))
        cards.append(
            f'<article class="card" data-action="{aid}" data-slots="{slots_json}">'
            f"<header><h3>行动 {aid}</h3><span class='status'>{final}</span></header>"
            "<dl>"
            f"<dt>提出</dt><dd>#{info['proposed']['seq']} 工具 <b>{esc(p['tool'])}</b>，参数 {esc(show(p['params']))}，提出者 {esc(p['proposer'])}</dd>"
            f"<dt>提出依据</dt><dd><code>{esc(show(p['basis']))}</code></dd>"
            f"<dt>状态经过</dt><dd><ul class='plain'>{status_items}</ul></dd>"
            f"<dt>等待回答</dt><dd>{esc(wait_text)}</dd>"
            f"<dt>发出的问题</dt><dd><ul class='plain'>{question_items}</ul></dd>"
            f"<dt>取到的回答</dt><dd><ul class='plain'>{take_items}</ul></dd>"
            f"<dt>返回值</dt><dd>{result}</dd>"
            f"<dt>变更</dt><dd><ul class='plain'>{change_items}</ul></dd>"
            "</dl></article>"
        )
    body = "".join(cards) or "<p class='empty'>这次运行没有提出任何行动。</p>"
    return f'<section id="actions"><h2>行动卡片</h2><div class="cards">{body}</div></section>'


def section_filters(record: Record) -> str:
    action_options = "".join(f'<option value="{aid}">行动 {aid}</option>' for aid in record.actions)
    slot_options = "".join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in record.slots)
    return (
        '<nav class="filters" aria-label="过滤">'
        '<fieldset><legend>事件类别</legend>'
        '<label><input type="radio" name="kind" value="all" checked> 全部</label>'
        '<label><input type="radio" name="kind" value="state"> 只看状态事件</label>'
        '<label><input type="radio" name="kind" value="trace"> 只看追踪事件</label></fieldset>'
        '<fieldset><legend>谁引起</legend>'
        '<label><input type="radio" name="origin" value="all" checked> 全部</label>'
        '<label><input type="radio" name="origin" value="kernel"> 只看内核</label>'
        '<label><input type="radio" name="origin" value="tool"> 只看工具实现</label>'
        '<label><input type="radio" name="origin" value="external"> 只看外部</label></fieldset>'
        f'<label>行动 <select id="f-action"><option value="">全部</option>{action_options}</select></label>'
        f'<label>槽位 <select id="f-slot"><option value="">全部</option>{slot_options}</select></label>'
        '<span id="f-count" class="count"></span>'
        "</nav>"
    )


CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1f23;--muted:#62666d;--line:#e2e2de;--state:#1f5fbf;--state-bg:#e8f0fc;
--trace:#6b5a2e;--trace-bg:#f3eedf;--hit:#eef6ee;--hit-ink:#1f6b3a;--wait:#8a4b08;--wait-bg:#fdf1e2;
--ext:#7a2e8f;--ext-bg:#f6ecf9;--ext-line:#c89bd6;--tool:#2f6f6a}
@media (prefers-color-scheme:dark){:root{--bg:#16171a;--panel:#1f2125;--ink:#e6e6e3;--muted:#9a9ea5;--line:#34373c;
--state:#8db4f0;--state-bg:#1d2a3d;--trace:#d8c38e;--trace-bg:#2e2a1f;--hit:#1c2b20;--hit-ink:#8fd1a4;--wait:#f0b46a;--wait-bg:#35281a;
--ext:#e0a8f0;--ext-bg:#2f2236;--ext-line:#6f4a7c;--tool:#7fc8c0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,"Segoe UI","PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif}
header.page{padding:20px 24px 8px}
header.page h1{margin:0;font-size:20px}
header.page p{margin:4px 0 0;color:var(--muted)}
main{padding:0 24px 40px;max-width:1280px}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin:16px 0}
h2{margin:0 0 12px;font-size:16px}
h3{margin:0;font-size:14px}
h3 small{font-weight:normal;color:var(--muted);margin-left:8px}
.filters{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;background:var(--panel);
border-bottom:1px solid var(--line);padding:10px 24px}
.filters fieldset{border:0;margin:0;padding:0;display:flex;gap:12px;align-items:center}
.filters legend{float:left;margin-right:8px;color:var(--muted)}
.filters select{font:inherit;padding:2px 4px}
.filters label{white-space:nowrap}
.filters fieldset{flex-wrap:wrap}
.count{color:var(--muted)}
table.kv{border-collapse:collapse;width:100%}
table.kv th{width:12em;text-align:left;vertical-align:top;color:var(--muted);font-weight:normal;padding:6px 12px 6px 0;border-top:1px solid var(--line)}
table.kv td{padding:6px 0;border-top:1px solid var(--line)}
ul.plain{list-style:none;margin:0;padding:0}
.loop{border-top:1px solid var(--line);padding:12px 0}
.loop:first-of-type{border-top:0}
.steps{display:grid;gap:0;margin-top:8px}
.step{display:grid;grid-template-columns:13em minmax(0,1fr);border-top:1px dashed var(--line);padding:6px 0}
.step-name{color:var(--muted)}
.step-name .wait{display:block;margin-top:4px;color:var(--wait);background:var(--wait-bg);border-radius:4px;padding:2px 6px;width:max-content;max-width:100%}
ol.events{list-style:none;margin:0;padding:0}
.ev{display:grid;grid-template-columns:minmax(0,1fr) minmax(14em,34%);gap:0 10px;align-items:stretch}
.ev .cell{min-width:0}
.step-body,.line,.line .desc,.tool-impl,.ask-out{min-width:0}
.line .desc{overflow-wrap:anywhere}
.ext-cell{border-left:3px solid var(--ext-line);padding-left:8px}
.ev.origin-external .ext-cell{background:var(--ext-bg);border-radius:0 4px 4px 0;padding:2px 8px}
.ext-tag{display:inline-block;font-size:12px;font-weight:600;color:var(--ext);margin-bottom:2px}
.blocked{display:block;color:var(--wait);background:var(--wait-bg);border-radius:4px;padding:2px 6px;margin:2px 0}
.idle{display:block;color:var(--muted);padding:2px 0}
.tool-impl{margin-left:1.5em;border-left:2px solid var(--tool);padding-left:8px}
.tool-tag{display:block;font-size:12px;color:var(--tool)}
.ask-out{border-left:2px dashed var(--ext-line);padding-left:8px}
.ask-tag{display:block;font-size:12px;font-weight:600;color:var(--ext)}
.line{display:grid;grid-template-columns:3.5em 3em 7.5em 1fr;gap:6px;padding:1px 0;align-items:baseline}
.ext-cell .line{grid-template-columns:3em 3em 1fr}.ext-cell .line .desc{grid-column:2/-1}
.src{margin-left:8px;font-size:12px;color:var(--muted);font-family:ui-monospace,Menlo,Consolas,monospace}
.lane-head{display:grid;grid-template-columns:13em minmax(0,1fr) minmax(14em,34%);gap:0 10px;color:var(--muted);font-size:12px;margin-bottom:6px}
.lane-head span:first-child{grid-column:2}
.lane-head span:last-child{border-left:3px solid var(--ext-line);padding-left:8px;color:var(--ext)}
.ev .seq,.seq{color:var(--muted);font-variant-numeric:tabular-nums}
.badge{font-size:12px;border-radius:3px;padding:0 4px;text-align:center}
.badge.state{color:var(--state);background:var(--state-bg)}
.badge.trace{color:var(--trace);background:var(--trace-bg)}
.line.trace .desc,.line.trace .label{color:var(--muted)}
.line.is-wait .desc{color:var(--wait)}
.empty{color:var(--muted);margin:0}
.hint{color:var(--muted);margin:0 0 8px}
.scroll{overflow-x:auto}
table.grid{border-collapse:collapse;font-variant-numeric:tabular-nums}
table.grid th,table.grid td{border:1px solid var(--line);padding:4px 8px;text-align:left;white-space:nowrap}
table.grid thead th{color:var(--muted);font-weight:normal}
td.hit{background:var(--hit)}
td.hit .val{color:var(--hit-ink);font-weight:600;display:block}
td.hit .src{color:var(--muted);font-size:12px}
td.miss{color:var(--line);text-align:center}
td.final{font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.card{border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.card header{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:6px}
.card .status{font-weight:600}
.card dl{margin:0;display:grid;grid-template-columns:6em 1fr;gap:4px 8px}
.card dt{color:var(--muted)}
.card dd{margin:0}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
[hidden]{display:none!important}
@media (max-width:720px){.step{grid-template-columns:minmax(0,1fr)}.ev{grid-template-columns:minmax(0,1fr)}.ext-cell{border-left:0;padding-left:0}
.ev:not(.origin-external) .ext-cell{display:none}
.lane-head{display:none}.line{grid-template-columns:3em 3em minmax(0,1fr)}.line .desc{grid-column:1/-1;padding-left:3em}}
"""

JS = """
(function(){
  const kindInputs = document.querySelectorAll('input[name=kind], input[name=origin]');
  const fAction = document.getElementById('f-action');
  const fSlot = document.getElementById('f-slot');
  const count = document.getElementById('f-count');
  function slotsOf(el){ try { return JSON.parse(el.dataset.slots || '[]'); } catch(e){ return []; } }
  function apply(){
    const kind = document.querySelector('input[name=kind]:checked').value;
    const origin = document.querySelector('input[name=origin]:checked').value;
    const action = fAction.value, slot = fSlot.value;
    let shown = 0, total = 0;
    document.querySelectorAll('li.ev').forEach(function(el){
      total++;
      const ok = (kind === 'all' || el.dataset.kind === kind)
        && (origin === 'all' || el.dataset.origin === origin)
        && (!action || el.dataset.action === action)
        && (!slot || slotsOf(el).indexOf(slot) >= 0);
      el.hidden = !ok; if (ok) shown++;
    });
    document.querySelectorAll('.step').forEach(function(step){
      const lines = step.querySelectorAll('li.ev');
      const visible = Array.prototype.some.call(lines, function(li){ return !li.hidden; });
      step.classList.toggle('filtered-empty', lines.length > 0 && !visible);
    });
    document.querySelectorAll('.loop').forEach(function(loop){
      const lines = loop.querySelectorAll('li.ev');
      loop.hidden = !Array.prototype.some.call(lines, function(li){ return !li.hidden; });
    });
    document.querySelectorAll('.card').forEach(function(card){
      card.hidden = (action && card.dataset.action !== action) || (slot && slotsOf(card).indexOf(slot) < 0);
    });
    document.querySelectorAll('.slot-row').forEach(function(row){
      row.hidden = slot && slotsOf(row).indexOf(slot) < 0;
    });
    count.textContent = '时间线显示 ' + shown + ' / ' + total + ' 个事件';
  }
  kindInputs.forEach(function(i){ i.addEventListener('change', apply); });
  fAction.addEventListener('change', apply);
  fSlot.addEventListener('change', apply);
  apply();
})();
"""


def render(record: Record, source_name: str) -> str:
    title = record.started["task_def_name"] if record.started else record.task_id
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>运行记录 · {esc(title)} · {esc(record.task_id)}</title>"
        f"<style>{CSS}</style></head><body>"
        f"<header class='page'><h1>运行记录：{esc(title)}</h1>"
        f"<p>任务标识 {esc(record.task_id)} · 来源文件 {esc(source_name)}</p></header>"
        f"{section_filters(record)}<main>"
        f"{section_overview(record)}{section_timeline(record)}{section_slots(record)}{section_actions(record)}"
        f"</main><script>{JS}</script></body></html>"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：python -m tod_kernel.view runs/<文件>.jsonl", file=sys.stderr)
        return 2
    source = Path(argv[1])
    record = Record(load(source))
    target = source.with_suffix(".html")
    target.write_text(render(record, source.name), encoding="utf-8")
    print(f"已生成 {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
