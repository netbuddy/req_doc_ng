"""验证脚本的机器检查：上下文包与对话历史的三条规则。"""

from __future__ import annotations

import re
from pathlib import Path

from tod_kernel import llm
from tod_kernel.tools import EXCEPTION_TOOL, UNDERSTAND_TOOL
from tod_kernel.tools import prompt_pack_of
from tod_kernel.kernel import DATA_CHANGED, MESSAGE_PUT, OUTBOX
from tod_kernel.tools import system_prompt_for as tools_system_prompt_for
from tod_kernel.verify.base import Checker, ask_events, banner, fake_event, step_at
from tod_kernel.verify.glossary import GLOSSARY_INPUT_ONE, GLOSSARY_TOOLS, glossary_def


# ───────────────────────── 检查组：上下文包 ─────────────────────────
# 不跑任务，手写一份任务数据与事件列表，把六种段与对话历史的三条规则逐条验出来。

CTX_TERM = "基线"
CTX_DRAFT_ONE = "第一稿：基线是冻结下来的条目集合。"
CTX_DRAFT_TWO = "第二稿：基线是某一轮评审通过时冻结的条目集合。"
CTX_FEEDBACK = "说清楚它是什么时候冻结的。"
CTX_REPLY = "再具体些。"
CTX_REPLY_TWO = "还要说明它的作用。"


def context_pack_checks() -> Checker:
    """第四步验证目标三的一部分：上下文包按六种段组装，对话历史按三条确定性规则装入。

    手写数据与事件，不跑任务：这一组要验的是规则本身，跑任务反而把规则埋进流程里看不清。
    """
    from tod_kernel.context import (EMPTY_MARK, MISSING_MARK, SEG_DIALOGUE, SEG_MATERIAL, SEG_ORDER,
                                    SEG_PROGRESS, SEG_STEP, SPEAKER_SYSTEM, ContextPack, render, revision_log)

    title = "第四步：上下文包与对话历史的三条规则"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    task_def = glossary_def(dict(GLOSSARY_INPUT_ONE))
    data = {**task_def.SLOTS, "术语": CTX_TERM, "原文片段": None, "释义草稿": CTX_DRAFT_TWO,
            "回复": None, "修改意见": CTX_FEEDBACK, "确认释义": None}
    # 当前步不在任务数据里，单独给：『确认』阶段第 1 到第 3 步这段循环的第 2 次，做完了第 2 步「理解使用者的回复」。
    ctx_step = step_at("确认", 2, loop=(1, 3, 2))
    # 三轮问答：第一轮在「写释义草稿」阶段问术语（回答至今原样留在槽位里），
    # 后两轮在「确认」阶段问回复（回答都已被对话理解清空），这样三条规则各有例子可验。
    events = (ask_events(1, 1, "写释义草稿", "术语", "请给出要澄清的术语。", CTX_TERM)
              + [fake_event(4, DATA_CHANGED, {"slot": "释义草稿", "old": None, "new": CTX_DRAFT_ONE, "source": 2}, 2)]
              + ask_events(5, 3, "确认", "回复", f"草稿是：{CTX_DRAFT_ONE} 请确认", CTX_REPLY)
              + [fake_event(8, DATA_CHANGED, {"slot": "修改意见", "old": None, "new": CTX_FEEDBACK, "source": 4}, 4),
                 fake_event(9, DATA_CHANGED, {"slot": "回复", "old": CTX_REPLY, "new": None, "source": 4}, 4),
                 fake_event(10, DATA_CHANGED, {"slot": "释义草稿", "old": CTX_DRAFT_ONE, "new": CTX_DRAFT_TWO, "source": 5}, 5)]
              + ask_events(11, 6, "确认", "回复", f"草稿是：{CTX_DRAFT_TWO} 请确认", CTX_REPLY_TWO)
              + [fake_event(14, DATA_CHANGED, {"slot": "回复", "old": CTX_REPLY_TWO, "new": None, "source": 7}, 7)])
    pack = ContextPack.build(task_def, data, events, step=ctx_step)

    segments = [pack.progress("本次写第 3 稿。"), pack.dialogue(), pack.data_segment(["术语"]),
                pack.material("原文片段"), ContextPack.step("本步执行工具「生成术语释义」：改写。"),
                ContextPack.shape("文本", "只输出释义正文。")]
    c.check("六种段按固定顺序：任务进度、对话历史、当前数据、参考材料、本步、输出形状",
            [segment.type for segment in segments] == list(SEG_ORDER), [segment.type for segment in segments])
    text = render(segments)
    heads = re.findall(r"^【(.+?) · (.+?)】$", text, flags=re.MULTILINE)
    c.check("每段以【段名 · 来源】开头，六段六个段头，段名与段类型逐一对应",
            [head[0] for head in heads] == list(SEG_ORDER) and all(head[1] for head in heads), heads)

    progress = pack.progress()
    # 第四步 4.10 节：这句话由任务定义的 step_text 生成，上下文包不自己算第几步、第几次。
    # 第三步的写法把整任务的步骤编号加一当成了阶段内序号（「确认」阶段只有三步却写成「第 5 步」），那条错误的预期跟着改。
    c.check("任务进度段用任务定义给的那句当前步，阶段、循环起止、第几次、上限、做完的是哪一步都在里面，来源是当前步",
            progress.type == SEG_PROGRESS and progress.source == "当前步"
            and progress.text.startswith("当前步：『确认』阶段，第 1 到第 3 步循环的第 2 次（最多 5 次），"
                                         "做完了第 2 步『理解使用者的回复』"), progress.text)

    dialogue = pack.dialogue()
    c.check("对话历史第一条规则（按范围取）：默认只装当前阶段『确认』的两轮问答，"
            "「写释义草稿」阶段问术语那一轮不在范围内",
            dialogue.text.count(SPEAKER_SYSTEM) == 2 and CTX_REPLY in dialogue.text
            and CTX_REPLY_TWO in dialogue.text and "请给出要澄清的术语。" not in dialogue.text, dialogue.text)
    c.check("对话历史的来源写明范围、装了几轮、预算与用量",
            dialogue.type == SEG_DIALOGUE and "范围：当前阶段" in dialogue.source
            and "2 轮装入" in dialogue.source and "预算 800 字" in dialogue.source, dialogue.source)

    whole = ContextPack.build(task_def, data, events, step=ctx_step).dialogue(scope="task")
    c.check("对话历史第二条规则（已在数据里的不重复装）：整任务范围下有三轮，问术语那一轮的回答仍原样留在槽位「术语」里，"
            "不重复装；回答已被清空的两轮装",
            whole.text.count(SPEAKER_SYSTEM) == 2 and "请给出要澄清的术语" not in whole.text
            and "回答已被清空的 2 轮" in whole.source, (whole.source, whole.text))

    tight = ContextPack.build(task_def, data, events, budget_chars=10, step=ctx_step).dialogue(scope="task")
    c.check("对话历史第三条规则（预算封顶）：预算调到 10 字时，两轮装不下，从最早那一轮整轮裁起，"
            "至少留最近一轮，省略处标出【更早 1 轮已省略】",
            tight.text.startswith("【更早 1 轮已省略】") and tight.text.count(SPEAKER_SYSTEM) == 1
            and CTX_REPLY_TWO in tight.text, tight.text[:120])

    revisions = revision_log(events, "释义草稿", "修改意见")
    c.check("修订记录从数据变更事件推出：写了两稿、提了一条意见，就是「第 1 稿」「使用者意见」「第 2 稿」三行",
            revisions.splitlines() == [f"第 1 稿：{CTX_DRAFT_ONE}", f"使用者意见：{CTX_FEEDBACK}",
                                       f"第 2 稿：{CTX_DRAFT_TWO}"], revisions.splitlines())

    empty_pack = ContextPack.build(task_def, {**data, "修改意见": None, "原文片段": None}, [], step=ctx_step)
    c.check("系统标记：槽位为空那一行写「（空）」，参考材料没有写「（未提供）」",
            empty_pack.data_segment(["修改意见"]).text == f"修改意见：{EMPTY_MARK}"
            and empty_pack.material("原文片段").text == MISSING_MARK,
            (empty_pack.data_segment(["修改意见"]).text, empty_pack.material("原文片段").text))
    # 当前数据合成一段（2026-09-17 用户裁定）：段头写「当前数据 · 槽位」，段内一行一个槽位，修订记录是最后一项。
    merged = pack.data_segment(["术语", ("释义草稿", "（上一稿）")], revision=("释义草稿", "修改意见"))
    c.check("当前数据是一段：来源写「槽位」，段内一行一个槽位「名：值」，角色标注跟在名字后面，修订记录是最后一项",
            merged.source == "槽位"
            and merged.text.splitlines()[:2] == [f"术语：{CTX_TERM}", f"释义草稿（上一稿）：{CTX_DRAFT_TWO}"]
            and merged.text.splitlines()[2].startswith("修订记录（系统从变更事件推出）：第 1 稿：")
            and merged.text.splitlines()[3].startswith("  使用者意见："),
            merged.text.splitlines())
    c.check("参考材料段的来源写明是哪个槽位；本步段指向工具，输出形状段写形状",
            pack.material("原文片段").type == SEG_MATERIAL
            and ContextPack.step("本步执行工具「对话理解」：…").type == SEG_STEP
            and ContextPack.shape("JSON", "{}", api_note=True).source.startswith("JSON（同一份结构也作为接口参数"),
            ContextPack.shape("JSON", "{}", api_note=True).source)

    from tod_kernel.context import exchanges_of, utterances_of

    notice = fake_event(30, MESSAGE_PUT, {"box": OUTBOX, "kind": "notice", "sender": "tool.告知", "recipient": "user",
                                          "call_id": 9, "in_reply_to": None, "content": {"utterance": "「产出」指签字的清单。",
                                                                                         "params": {"text": "…"}},
                                          "seq": 9}, 9)
    pending = ask_events(31, 10, "确认", "回复", "还有要改的吗？", None)[:2]
    talk = ask_events(1, 1, "确认", "术语", "请给出要澄清的术语。", CTX_TERM) + [notice] + pending
    spoken = utterances_of(talk)
    c.check("对话历史按话轮存：系统的提问、系统的告知、使用者的回答各一条，没有回答的提问也照记，按事件序号排",
            [(u.speaker, u.kind, u.text, u.seq, u.stage, u.call_id) for u in spoken]
            == [("系统", "提问", "请给出要澄清的术语。", 2, "确认", 1), ("使用者", "回答", CTX_TERM, 3, "确认", 1),
                ("系统", "告知", "「产出」指签字的清单。", 30, "", 9), ("系统", "提问", "还有要改的吗？", 32, "确认", 10)],
            [(u.speaker, u.kind, u.text, u.seq, u.stage, u.call_id) for u in spoken])
    parts = exchanges_of(spoken)
    lone = exchanges_of([spoken[2]] + spoken[:2])
    c.check("交互轮次的划分：从一个提问到下一个提问之前为一段，中间的告知归前一段；第一个提问之前的告知自成一段；"
            "渲染仍是逐行「系统：」「使用者：」原文",
            [[u.kind for u in part.utterances] for part in parts] == [["提问", "回答", "告知"], ["提问"]]
            and [[u.kind for u in part.utterances] for part in lone] == [["告知"], ["提问", "回答"]]
            and parts[0].render() == f"系统：请给出要澄清的术语。\n使用者：{CTX_TERM}\n系统：「产出」指签字的清单。"
            and parts[1].answer is None and parts[1].question.text == "还有要改的吗？",
            [[u.kind for u in part.utterances] for part in parts])

    prompt = tools_system_prompt_for(task_def, list(GLOSSARY_TOOLS) + [EXCEPTION_TOOL])
    heads = re.findall(r"^【(.+?)】$", prompt, flags=re.MULTILINE)
    c.check("系统提示六段齐全，顺序是角色与任务、任务定义摘要、工具目录、上下文约定、领域规矩、通用输出规矩",
            heads == ["角色与任务", "任务定义摘要", "工具目录", "上下文约定", "领域规矩", "通用输出规矩"], heads)
    c.check("系统提示里有两个模型工具的固定指令（生成术语释义、对话理解，逐字取自各自的提示词包），也有任务定义的领域规矩",
            "你是术语解释员" in prompt and ("固定指令：" + prompt_pack_of(UNDERSTAND_TOOL).instruction) in prompt
            and "释义按需求工程语境写" in prompt, None)

    import shutil
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="tod-context-"))
    try:
        recording = work / "r.json"
        request_one = llm.Request(system=prompt, user="用户内容一", shape="文本")
        request_two = llm.Request(system=prompt, user="用户内容二", shape="文本")
        llm.save_to_recording(recording, request_one, "回答一")
        llm.save_to_recording(recording, request_two, "回答二")
        call = llm.make_caller({**llm.load_config(), "mode": llm.MODE_REPLAY}, recording_path=str(recording))
        first, second = call(request_one), call(request_two)
        c.check("模型调用记录：第一次调用记下系统提示全文，第二次只写「同任务系统提示」，两次的系统提示哈希相同",
                first.record["system_prompt"] == prompt
                and second.record["system_prompt"] == llm.SAME_SYSTEM_PROMPT
                and first.record["system_prompt_hash"] == second.record["system_prompt_hash"] == llm.text_hash(prompt),
                (second.record["system_prompt"], first.record["system_prompt_hash"][:12]))
        c.check("模型调用记录还带着用户内容、输出形状、返回原文、请求哈希与耗时",
                {"user_content", "shape", "response", "request_hash", "elapsed_ms"} <= set(first.record)
                and first.record["response"] == "回答一", sorted(first.record))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c
