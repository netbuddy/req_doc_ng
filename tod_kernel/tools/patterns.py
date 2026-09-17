"""对话模式用的工具：答疑（explain）与标记推迟（mark_deferred）。告知是对话端口上的系统工具，在 dialogue 里。"""

from __future__ import annotations

import copy
import functools

from tod_kernel.kernel import CallStatus, Change
from tod_kernel.llm import SHAPE_TEXT, LLMError, Request
from tod_kernel.tools.base import ToolSpec, _call_record, _pack, _plain, prompt_pack_of
from tod_kernel.tools.understanding import DEFERRED_MARK


MARK_DEFERRED_TOOL = "标记推迟"
EXPLAIN_TOOL = "答疑"


# 提示词在提示词包 prompts/答疑.json 里（第五步 4.4A 节）。
EXPLAIN_PROVIDES = {"本步": {"解释": {"问的内容"}}, "片段": {}}


def mark_deferred(ctx, task_def) -> None:
    """标记推迟：在参数 slot 所指的槽位写已推迟标记。本步只对文本槽位写；不是文本槽位时不写，照样记已成功并写明。"""
    call = ctx.call
    slot = call.params.get("slot")
    meta = (task_def.DEFINITION.get("槽位", {}) or {}).get(slot)
    if meta is None:
        ctx.set_status(CallStatus.FAILED, f"没有这个槽位：{slot!r}")
        return
    if meta.get("类型") != "文本":
        call.result = None
        ctx.set_status(CallStatus.SUCCEEDED, f"「{slot}」不是文本槽位，本步不打推迟标记")
        return
    call.result = dict(DEFERRED_MARK)
    call.changes = [Change(slot, copy.deepcopy(ctx.data_view.get(slot)), dict(DEFERRED_MARK), call.call_id)]
    ctx.set_status(CallStatus.SUCCEEDED, f"「{slot}」标已推迟")


def explain(ctx, call_model, task_def=None, read_events=None, system_prompt="") -> None:
    """答疑：把问的词或原话、当前任务全部槽位的现值、对话历史交给模型，要一到两句白话解释。

    不写任何槽位；解释放进返回值（模型调用记录）的「输出」，由对话模式的下一步「告知」说给使用者。
    模型调用失败记已失败；回答去掉首尾空白后为空也记已失败。
    """
    from tod_kernel.context import ContextPack, render

    call = ctx.call
    question = call.params.get("question")
    pack = _pack(task_def, ctx, read_events)
    slots = list((task_def.DEFINITION.get("槽位", {}) or {}).keys())
    segments = [pack.progress(),
                pack.dialogue(),
                pack.data_segment(slots),
                ContextPack.step(prompt_pack_of(EXPLAIN_TOOL).fill_step("解释", 问的内容=_plain(question))),
                ContextPack.shape(SHAPE_TEXT, prompt_pack_of(EXPLAIN_TOOL).shape)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_TEXT)
    try:
        reply = call_model(request)
    except LLMError as exc:
        ctx.set_status(CallStatus.FAILED, exc.brief)
        return
    answer = reply.text.strip()
    record = _call_record(reply, segments, {}, answer)
    record["输出"] = answer
    call.result = record
    if not answer:
        ctx.set_status(CallStatus.FAILED, "模型没有给出解释")
        return
    ctx.set_status(CallStatus.SUCCEEDED, f"模型给出解释，{len(answer)} 字")


# ───────────────────────── 本模块登记的工具 ─────────────────────────

# 静态工具规格：工具名 → ToolSpec，由 base 汇总成静态工具表 STATIC_TOOLS。
TOOL_SPECS = {
    MARK_DEFERRED_TOOL: ToolSpec(MARK_DEFERRED_TOOL, ("slot",), None, None,
                                 category="加工任务数据",
                                 summary="在参数所指的文本槽位写已推迟标记",
                                 writer_roles={"*": "使用者"}),
    EXPLAIN_TOOL: ToolSpec(EXPLAIN_TOOL, ("question",), None, frozenset(),
                           category="加工任务数据",
                           summary="用一到两句白话解释使用者问的词或句子，不写槽位，解释交给告知说出"),
}

# 工具实现：工具名 → 函数（任务定义）→ 实现，由 base 汇总；模型调用件、读事件函数与系统提示由 build_table 另外绑上。
TOOL_IMPLS = {
    MARK_DEFERRED_TOOL: lambda task_def: functools.partial(mark_deferred, task_def=task_def),
    EXPLAIN_TOOL: lambda task_def: explain,
}

# 内部调用模型的工具：值是（本步与片段的占位符声明, 提示词包里有没有输出形状）。
MODEL_TOOLS = {EXPLAIN_TOOL: (EXPLAIN_PROVIDES, True)}
