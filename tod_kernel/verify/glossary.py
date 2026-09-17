"""验证脚本的术语澄清一组：七个场景（确认、提修改意见、给整段改写、要换一份、推迟并顺手改术语、提问后再确认、含糊后选择）与它们共用的核对助手。"""

from __future__ import annotations

from tod_kernel import llm, taskdef
from tod_kernel.tools import DRAFT_TOOL, UNDERSTAND_TOOL
from tod_kernel.tools import REASK_LINE
from tod_kernel.kernel import CALL_PROPOSED, DATA_CHANGED, MESSAGE_PUT, INBOX, OUTBOX, CallStatus, TaskStatus
from tod_kernel.observe import call_history
from tod_kernel.verify.base import (Checker, Run, TASK_DEFS_DIR, banner, check_explainable,
                                    check_kernel_is_task_agnostic, check_selection_trail, check_step_final,
                                    check_waiting_then_success, frame, named, of_call, run_scenario, stack, stacked,
                                    status_values, step_at, step_sequence)
from tod_kernel.verify.intake import check_other_tool_calls, common_checks


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
