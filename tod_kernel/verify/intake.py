"""验证脚本的材料接入登记一组：正常场景、登记阶段目标写错报异常的场景、三份坏文件的加载错误。"""

from __future__ import annotations

from pathlib import Path

from tod_kernel import kernel, taskdef
from tod_kernel.tools import EXCEPTION_OPTIONS, EXCEPTION_TOOL, build_table
from tod_kernel.kernel import (CALL_PROPOSED, DATA_CHANGED, STEP_CHANGED, MESSAGE_PUT, TASK_ENDED, INBOX, OUTBOX,
                               CallStatus, EventStream, KernelError, Mailbox, TaskStatus)
from tod_kernel.observe import FileWriter, MemoryCollector, call_history, summarize
from tod_kernel.verify.base import (Checker, Run, SAMPLE_DIR_INPUT, TASK_DEFS_DIR, banner, check_explainable,
                                    check_integrity, check_kernel_is_task_agnostic, check_matches_step_two,
                                    check_run_file, check_sources_and_senders, check_step_final, check_trace_shape,
                                    check_waiting_then_success, intake_def, named, of_call, run_scenario, stack,
                                    stacked, status_values, step_at, step_sequence)


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
