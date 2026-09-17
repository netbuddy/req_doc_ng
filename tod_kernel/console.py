"""控制台：在终端里把使用者与系统的一问一答仿真出来。

到第三步为止，「使用者」一直由验证脚本扮演，按答案表自动应答，终端上看到的是断言编号而不是问答。
本模块把两种方式做成同一个程序：宿主循环只做一件事——从发件箱取系统的问题，拿到一个回答放进收件箱；
差别只在「回答从哪来」。预设场景的回答来自答案表，自由输入的回答来自键盘，其余全部共用，
两种方式打印得一模一样：一行「系统：……」，一行「使用者：……」，预设的那行前面标「（预设）」。

本模块是宿主一侧的东西，验证脚本（verify 包）导入它的宿主循环与预设应答者；反过来，本模块只在跑场景时
才在函数内部导入验证脚本，避免两个模块在加载时互相等待。

用法：`python3 -m tod_kernel.console [--config 配置文件] [--show-calls]`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tod_kernel import llm
from tod_kernel.kernel import (
    CALL_PROPOSED,
    CALL_STATUS_CHANGED,
    INBOX,
    LOOP_STARTED,
    MAILBOX_CLOSED,
    TERMINAL_CALL_STATUSES,
    CallStatus,
    Message,
)
from tod_kernel.tools import CONFIDENCE_FLOOR, NOTICE_KIND

QUESTION_PREFIX = "系统："
ANSWER_PREFIX = "使用者："
PRESET_ANSWER_PREFIX = "使用者（预设）："
NO_ANSWER_TEXT = "（答案表里没有这一问的回答，关闭收件箱）"


def target_key(target: dict) -> tuple:
    """答案表的键：（槽位名, 路径元组）。宿主按写入目标查答案，不读问题里的话。"""
    return (target["slot"], tuple(target.get("path", [])))


# ───────────────────────── 应答者 ─────────────────────────
# 应答者只回答一件事：这一问的回答是什么。返回字符串就是回答，返回 None 表示没有回答，
# 宿主随即关闭收件箱（内核那边的询问工具调用因此记已失败）。


class PresetAnswerer:
    """按答案表答的应答者：验证脚本原来内嵌的那套使用者行为。

    答案表按写入目标查，值有两种写法：一个字符串表示这个位置问几次都答同一句；一串字符串表示按次序一次一句
    （术语澄清的修改循环要第 1 次提意见、第 2 次确认），取完就当没有回答，宿主随即关闭收件箱。
    「告知异常」的问题没有写入目标，按异常回答表依次答，表用完也没有回答了。
    """

    preset = True

    def __init__(self, answers=None, exception_answers=None):
        self.answers = {key: (list(value) if isinstance(value, (list, tuple)) else value)
                        for key, value in (answers or {}).items()}
        self.exception_answers = list(exception_answers or [])

    def answer(self, question) -> str | None:
        params = question.content["params"]
        if "target" not in params:  # 告知异常的问题没有写入目标
            return self.exception_answers.pop(0) if self.exception_answers else None
        value = self.answers.get(target_key(params["target"]))
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value


class KeyboardAnswerer:
    """从键盘取回答的应答者：读一行原样交给宿主。

    读到文件结束（在终端里按 Ctrl-D）表示使用者不再回答，返回 None，宿主关闭收件箱。
    这一行由 input 自己带提示符打印，所以宿主不再另打印回答那一行。
    """

    preset = False

    def answer(self, question) -> str | None:
        try:
            return input(ANSWER_PREFIX)
        except EOFError:
            print()
            return None


# ───────────────────────── 宿主循环 ─────────────────────────


def host_loop(inbox, outbox, answerer, show: bool = False) -> None:
    """宿主循环：取问题、要回答、放回答；发件箱关闭就结束。

    show 为真时把问答打印出来。键盘应答者自带提示符，所以只有预设应答者的回答由这里打印。
    """
    while True:
        question = outbox.take(match=lambda m: m.kind in ("question", NOTICE_KIND), block=True)
        if question is None:  # 发件箱已关闭：不会再有问题
            break
        if show:
            print(f"{QUESTION_PREFIX}{question.content['utterance']}", flush=True)
        if question.kind == NOTICE_KIND:  # 告知：系统说了一句，不等回答
            continue
        answer = answerer.answer(question)
        if answer is None:
            if show and answerer.preset:
                print(f"{PRESET_ANSWER_PREFIX}{NO_ANSWER_TEXT}", flush=True)
            inbox.close("host")
            continue
        if show and answerer.preset:
            print(f"{PRESET_ANSWER_PREFIX}{answer}", flush=True)
        inbox.put(Message(
            kind="answer", sender="user", recipient=question.call_id,
            in_reply_to=question.seq, content=answer, call_id=question.call_id,
        ))


class CallPrinter:
    """显示工具调用：每个工具调用结束时打印一行（第几次迭代、步骤说明、结果）。加了「显示工具调用」开关才挂上。"""

    def __init__(self):
        self.loop_no = 0
        self.loop_of: dict[int, int] = {}
        self.note_of: dict[int, str] = {}

    def __call__(self, event) -> None:
        if event.name == LOOP_STARTED:
            self.loop_no = event.payload["loop_no"]
        elif event.name == CALL_PROPOSED:
            self.loop_of[event.call_id] = self.loop_no
            self.note_of[event.call_id] = event.payload["basis"][1]
        elif event.name == CALL_STATUS_CHANGED and event.payload["new_status"] in TERMINAL_CALL_STATUSES:
            aid = event.call_id
            status = event.payload["new_status"].value
            note = event.payload.get("note") or ""
            print(f"（工具调用 {aid}：第 {self.loop_of.get(aid, self.loop_no)} 次迭代，"
                  f"{self.note_of.get(aid, '')}，{status}，{note}）", flush=True)


# ───────────────────────── 预设场景 ─────────────────────────


def run_preset(index: int, show_calls: bool = False):
    """跑一个预设场景：问答照样打印，断言照样跑，逐条断言不打印，跑完打印通过条数或第一条失败。

    返回这个场景的断言记账本（Checker），调用方据它定退出状态；验证脚本那组检查也拿它比对断言条数。
    """
    from tod_kernel import verify

    title, scenario = verify.SCENARIOS[index - 1]
    verify.base.SHOW_EXCHANGES = True
    verify.base.PRINT_EVENTS = False  # 控制台上要看的是问答，不是事件流水
    verify.base.PRINT_CHECKS = False
    verify.base.EXTRA_SUBSCRIBERS = (CallPrinter(),) if show_calls else ()
    print(f"── 跑「{title}」 ──")
    try:
        checker = scenario()
    finally:
        verify.base.SHOW_EXCHANGES = False
        verify.base.PRINT_EVENTS = True
        verify.base.PRINT_CHECKS = True
        verify.base.EXTRA_SUBSCRIBERS = ()
    total = len(checker.results)
    failed = [description for description, ok in checker.results if not ok]
    if failed:
        print(f"断言：共 {total} 条，通过 {checker.passed} 条；第一条失败是「{failed[0]}」")
    else:
        print(f"断言：通过 {total} 条")
    run = verify.base.LAST_RUN
    if run is not None:
        print(f"这次运行的任务标识是 {run.task_id}，运行文件 {run.run_file}；"
              f"用观测台打开：python3 -m tod_kernel.observe serve")
    return checker


def run_everything() -> int:
    """菜单编号 0：跑的东西与 `python3 -m tod_kernel.verify` 完全相同，包括不算场景的那几组检查。"""
    from tod_kernel import verify

    return verify.main()


# ───────────────────────── 自由输入 ─────────────────────────


def tool_names_of(task_def) -> tuple:
    """从任务定义的结构里收集它用到的工具名：固定步骤阶段的每个步骤（含循环段里的），自主规划阶段的可选工具集。"""
    names: list[str] = []

    def add(name):
        if name and name not in names:
            names.append(name)

    for stage in task_def.DEFINITION.get("阶段列表", []):
        for tool in stage.get("可选工具集", []) or []:
            add(tool)
        for step in stage.get("步骤", []) or []:
            add(step.get("工具"))
            for inner in step.get("步骤组", []) or []:
                add(inner.get("工具"))
    return tuple(names)


def definition_files() -> list[Path]:
    from tod_kernel import verify

    # 对话模式文件 patterns.json 不是任务定义，不列
    return sorted(path for path in verify.TASK_DEFS_DIR.glob("*.json")
                  if not path.name.endswith(".schema.json") and path.name != "patterns.json")


def ask_initial_input(task_def) -> dict:
    """问使用者要不要先给几个槽位的初始值。直接回车表示都不给，之后由系统逐个问。"""
    print("这个任务的槽位：")
    for slot, value in task_def.SLOTS.items():
        note = (task_def.DEFINITION.get("槽位", {}).get(slot) or {}).get("说明", "")
        print(f"  {slot}：{note}，初始值 {value!r}")
    print("要先给哪些槽位的初始值？写成「槽位名=值」，多个用分号隔开；直接回车表示都不给。")
    line = input("初始输入：").strip()
    initial: dict = {}
    for piece in line.split("；") if "；" in line else line.split(";"):
        if "=" in piece:
            slot, _, value = piece.partition("=")
            initial[slot.strip()] = value.strip()
    return initial


def closed_before_failure(run) -> int | None:
    """宿主关过收件箱（使用者按 Ctrl-D 不再回答）而随后有工具调用失败时，返回那个工具调用的编号，否则返回空。

    这是给通用检查里「邮箱关闭」那几条断言用的：关过与没关过，该看的事实不一样。
    """
    closed = [e for e in run.all_events if e.name == MAILBOX_CLOSED and e.payload["box"] == INBOX]
    failed_call = getattr(run.error, "call", None)
    if closed and failed_call is not None:
        return failed_call.call_id
    return None


def run_free_input(config: dict, show_calls: bool = False) -> int:
    """自由输入：选一份任务定义文件，使用者敲的话原样进收件箱，模型是真的。

    跑完只做与内容无关的通用检查（事件序号连续、每个工具调用有始有终、每条回答对应一个问题、运行文件写得出读得回）；
    依赖具体回答的断言对临场输入不成立，不跑；模型答得对不对由使用者自己看。
    """
    from tod_kernel import verify

    if config["mode"] != llm.MODE_RUN:
        print(f"自由输入要用「{llm.MODE_RUN}」模式：你临场敲的话是新的，"
              f"录制文件里查不到这条请求，回放模式下模型调用只会报错。"
              f"请把配置文件的 mode 改成「{llm.MODE_RUN}」再来。")
        return 1
    files = definition_files()
    for number, path in enumerate(files, start=1):
        print(f"  {number}  {path.name}")
    raw = input("选一份任务定义文件（输入编号）：").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(files):
        print("没有这个编号。")
        return 1
    path = files[int(raw) - 1]
    probe = verify.taskdef.load(path)  # 先加载一次，为的是把槽位表列给使用者看
    initial = ask_initial_input(probe)
    task_def = verify.taskdef.load(path, initial=initial)
    task_id = f"T-console-{path.stem if path.stem.isascii() else 'free'}"
    call = llm.make_caller(config)
    print(f"── 开始跑「{task_def.NAME}」，任务标识 {task_id}。回答不下去时按 Ctrl-D 结束 ──")
    run = verify.run_scenario(task_id, task_def, answers={}, tool_names=tool_names_of(task_def),
                              console=False, call_model=call, answerer=KeyboardAnswerer(), show=True,
                              subscribers=(CallPrinter(),) if show_calls else (),
                              confidence_floor=config.get("confidence_floor", CONFIDENCE_FLOOR))
    print("── 跑完了 ──")
    if run.error is not None:
        print(f"任务没有跑到完成：{run.error}")
    else:
        for item in task_def.DEFINITION.get("交付物", []):
            source = item["来源"]
            slots = [source] if isinstance(source, str) else list(source)
            print(f"交付物「{item['名字']}」（{item['形态']}）：")
            for slot in slots:
                print(f"  {slot} = {run.task.data.get(slot)!r}")
    c = verify.Checker("自由输入的通用检查")
    verify.base.PRINT_CHECKS = False
    try:
        verify.check_integrity(c, run)
        verify.check_run_file(c, run)
        verify.check_sources_and_senders(c, run, closed_before_failure_of=closed_before_failure(run))
    finally:
        verify.base.PRINT_CHECKS = True
    failed = [description for description, ok in c.results if not ok]
    if failed:
        print(f"通用检查：共 {len(c.results)} 条，通过 {c.passed} 条；失败的有：")
        for description in failed:
            print(f"  {description}")
    else:
        print(f"通用检查：通过 {len(c.results)} 条")
    print(f"运行文件 {run.run_file}；用观测台打开：python3 -m tod_kernel.observe serve")
    return 0 if not failed else 1


# ───────────────────────── 对话理解标注集 ─────────────────────────


def run_eval_listing() -> int:
    """逐条打印对话理解标注集：上一问、系统那一问、使用者原话、期望与实际的行为列表、每项落成了什么。回放模式，不调模型服务。"""
    from tod_kernel import verify

    call_model, _ = verify.glossary_call(recording=verify.EVAL_RECORDING)
    cases = verify.load_eval_cases()
    whole = 0
    for case in cases:
        made, statuses = verify.understand_case(case, call_model)
        record = made.result if isinstance(made.result, dict) else {}
        acts = record.get("acts") or []
        ok, _, _, problems = verify.score_case(case, acts)
        whole += ok
        question = case["上一问"]
        print(f"── 第 {case['编号']} 条 · 上一问{question['类型']} · {case['情形']} · {'全对' if ok else '不一致'} ──")
        print(f"{QUESTION_PREFIX}{case['系统问话']}")
        print(f"{ANSWER_PREFIX}{case['原话']}")
        if made.status != CallStatus.SUCCEEDED:
            print(f"  对话理解没有做成：{statuses[-1][1] if statuses else ''}")
            continue
        print("  期望：" + "；".join(f"{e['功能']} {e.get('内容')}" for e in case["期望"]))
        for number, act in enumerate(acts, start=1):
            print(f"  实际 {number}：{act.get('功能')} {act.get('内容')}，把握 {act.get('把握')}，落成：{act.get('落成')}")
        if problems:
            print(f"  差在：{'；'.join(problems)}")
    print(f"逐句全对：{whole}/{len(cases)}")
    return 0


# ───────────────────────── 菜单 ─────────────────────────


def print_menu() -> tuple:
    """打印菜单，返回（标注集那一项的编号, 自由输入那一项的编号）。"""
    from tod_kernel import verify

    print("可以跑的：")
    print(f"  0  全部：{len(verify.SCENARIOS)} 个场景与不算场景的几组检查（与 python3 -m tod_kernel.verify 相同）")
    for number, (title, _) in enumerate(verify.SCENARIOS, start=1):
        print(f"  {number}  {title}")
    listing = len(verify.SCENARIOS) + 1
    free = listing + 1
    print(f"  {listing}  对话理解标注集：逐条打印原话与行为列表（回放）")
    print(f"  {free}  自由输入：自己选任务定义文件，自己敲回答")
    return listing, free


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="任务型智能体最小内核的控制台：在终端里一问一答地跑任务。")
    parser.add_argument("--config", default=None, help="配置文件路径；不给就用包目录下的 config.json，它不存在时用 config.example.json")
    parser.add_argument("--show-calls", action="store_true", help="每个工具调用结束时打印一行（第几次迭代、步骤说明、结果）")
    args = parser.parse_args(argv)

    config = llm.load_config(args.config)
    print(llm.describe_config(config, args.config))
    listing, free = print_menu()
    raw = input("输入编号：").strip()
    if not raw.isdigit():
        print("请输入一个编号。")
        return 1
    number = int(raw)
    if number == 0:
        return run_everything()
    if number == listing:
        return run_eval_listing()
    if number == free:
        return run_free_input(config, show_calls=args.show_calls)
    from tod_kernel import verify

    if 1 <= number <= len(verify.SCENARIOS):
        checker = run_preset(number, show_calls=args.show_calls)
        return 0 if checker.passed == len(checker.results) else 1
    print("没有这个编号。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
