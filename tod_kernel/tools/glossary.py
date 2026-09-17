"""术语澄清的领域工具：生成术语释义（首稿与改稿）。"""

from __future__ import annotations

import copy

from tod_kernel.kernel import CallStatus, Change
from tod_kernel.llm import SHAPE_TEXT, LLMError, Request
from tod_kernel.tools.base import ToolSpec, _call_record, _pack, _plain, prompt_pack_of
from tod_kernel.tools.understanding import FEEDBACK_SLOT, REPLY_SLOT


# ───────────────────────── 术语澄清用的模型工具：生成术语释义 ─────────────────────────
# 模型在这个内核里的第二种使用场景：工具实现内部调模型，回答是数据，写进槽位，对错由使用者确认判。
# 工具无参数、直接读槽位；请求由上下文包（context.py）按段组装，系统提示在建工具表时拼好。
# 提示词（固定指令、本步段、进度与参考材料里的短句、输出形状）在提示词包 prompts/生成术语释义.json 里（第五步 4.4A 节）：
# 改一个字，请求哈希就变，录制文件全部失效要重录。

DRAFT_TOOL = "生成术语释义"
TERM_SLOT = "术语"
SOURCE_SLOT = "原文片段"
DRAFT_SLOT = "释义草稿"
CONFIRMED_SLOT = "确认释义"

# 提示词包里每个模板能用哪些占位符，由这里声明，加载时逐个核对（prompt_pack.load）。
DRAFT_PROVIDES = {"本步": {"首稿": {TERM_SLOT}, "改稿": {TERM_SLOT}},
                  "片段": {"进度·首稿": {"稿数"}, "进度·改稿": {"稿数", "上一稿"}, "参考材料缺失": set()}}


def _draft_count(pack) -> int:
    """已经写过几稿：数事件流里「释义草稿」被写过几次。进度段里说「第几稿」用它。

    数事件而不是数修订记录那段文字，免得草稿正文恰好以「第 」开头时数错。
    """
    from tod_kernel.kernel import DATA_CHANGED

    return sum(1 for event in pack.events
               if event.name == DATA_CHANGED and event.payload["slot"] == DRAFT_SLOT
               and event.payload["new"] is not None)


def draft_definition(ctx, call_model, task_def=None, read_events=None, system_prompt="") -> None:
    """生成术语释义：首稿与改稿同一个实现，按「修改意见」是不是空分支。

    首稿读「术语」与可选的「原文片段」；改稿另读上一稿与「修改意见」，写完把「修改意见」清空。
    回答去掉首尾空白后写进「释义草稿」；工具调用的返回值是这次模型调用的完整记录。
    模型调用失败（模型服务不可达、回放时录制文件里查不到）时记已失败，说明就是那条错误的一句话。
    """
    from tod_kernel.context import ContextPack, render  # 同上，函数内导入

    call = ctx.call
    pack = _pack(task_def, ctx, read_events)
    feedback = ctx.data_view.get(FEEDBACK_SLOT)
    revising = feedback is not None
    drafts = _draft_count(pack)
    prompt = prompt_pack_of(DRAFT_TOOL)
    term = _plain(ctx.data_view.get(TERM_SLOT))
    if revising:
        segments = [pack.progress(prompt.fill_piece("进度·改稿", 上一稿=drafts, 稿数=drafts + 1)),
                    pack.dialogue(scope=("slots", [REPLY_SLOT])),
                    pack.data_segment([TERM_SLOT, (DRAFT_SLOT, "（上一稿）"), FEEDBACK_SLOT],
                                      revision=(DRAFT_SLOT, FEEDBACK_SLOT)),
                    pack.material(SOURCE_SLOT),
                    ContextPack.step(prompt.fill_step("改稿", 术语=term)),
                    ContextPack.shape(SHAPE_TEXT, prompt.shape)]
    else:
        segments = [pack.progress(prompt.fill_piece("进度·首稿", 稿数=drafts + 1)),
                    pack.dialogue(),
                    pack.data_segment([TERM_SLOT]),
                    pack.material(SOURCE_SLOT, prompt.fill_piece("参考材料缺失")),
                    ContextPack.step(prompt.fill_step("首稿", 术语=term)),
                    ContextPack.shape(SHAPE_TEXT, prompt.shape)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_TEXT)
    try:
        reply = call_model(request)
    except LLMError as exc:
        ctx.set_status(CallStatus.FAILED, exc.brief)
        return
    draft = reply.text.strip()
    changes = [Change(DRAFT_SLOT, copy.deepcopy(ctx.data_view.get(DRAFT_SLOT)), draft, call.call_id)]
    if revising:  # 意见已经落实到这一稿里，清空，否则下一次循环又会被当成待改
        changes.append(Change(FEEDBACK_SLOT, copy.deepcopy(feedback), None, call.call_id))
    call.result = _call_record(reply, segments, {"（文本）": DRAFT_SLOT}, draft)
    call.changes = changes
    # 终态事件带返回值，所以先填返回值再记状态。
    ctx.set_status(CallStatus.SUCCEEDED, f"模型写出第 {drafts + 1} 稿，{len(draft)} 字")


DRAFT_DEFINITION_NOTE = "术语不为 None，且（释义草稿为 None 或修改意见不为 None）"


def _draft_definition_precondition(data, params):
    """首稿与改稿共用一个条件：有术语，而且要么还没有草稿（写首稿），要么有修改意见（改稿）。

    原文片段为空不是障碍，它只是可选的上下文：系统不向使用者索要原文片段，那是系统自己的功课。
    """
    term, source = data.get(TERM_SLOT), data.get(SOURCE_SLOT)
    draft, feedback = data.get(DRAFT_SLOT), data.get(FEEDBACK_SLOT)
    ok = term is not None and (draft is None or feedback is not None)
    return ok, DRAFT_DEFINITION_NOTE, {TERM_SLOT: term, SOURCE_SLOT: source,
                                       DRAFT_SLOT: draft, FEEDBACK_SLOT: feedback}


# ───────────────────────── 本模块登记的工具 ─────────────────────────

# 静态工具规格：工具名 → ToolSpec，由 base 汇总成静态工具表 STATIC_TOOLS。
TOOL_SPECS = {
    DRAFT_TOOL: ToolSpec(DRAFT_TOOL, (), _draft_definition_precondition,
                         frozenset({DRAFT_SLOT, FEEDBACK_SLOT}),
                         category="加工任务数据",
                         summary="根据术语（与原文片段，若有）写一条释义草稿，有修改意见时按意见改稿，写入释义草稿"),
}

# 工具实现：工具名 → 函数（任务定义）→ 实现，由 base 汇总；模型调用件、读事件函数与系统提示由 build_table 另外绑上。
TOOL_IMPLS = {
    DRAFT_TOOL: lambda task_def: draft_definition,  # 模型调用件、读事件函数与系统提示由 build_table 另外绑上
}

# 内部调用模型的工具：值是（本步与片段的占位符声明, 提示词包里有没有输出形状）。
MODEL_TOOLS = {DRAFT_TOOL: (DRAFT_PROVIDES, True)}
