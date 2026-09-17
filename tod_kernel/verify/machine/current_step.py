"""验证脚本的机器检查：当前步的记录、判据与显示。"""

from __future__ import annotations

from tod_kernel.tools import DRAFT_TOOL
from tod_kernel.kernel import ToolCall, CallStatus
from tod_kernel.verify.base import Checker, banner, intake_def, stack, step_at
from tod_kernel.verify.glossary import GLOSSARY_INPUT_ONE, glossary_def


def current_step_checks() -> Checker:
    """当前步（第四步 4.10 节）：初始值、记录本步的三条规则、「第几次」怎么加、不合法怎么报、两个显示接口。

    手写当前步与工具调用，不跑任务：这一组要验的是规则本身。场景里的当前步序列另在各场景断言里逐次核对。
    """
    from tod_kernel.kernel import DefinitionError
    from tod_kernel.taskdef import STEP_STAGE

    title = "第四步：当前步的记录、判据与显示"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    glossary = glossary_def(dict(GLOSSARY_INPUT_ONE))
    intake = intake_def("intake.json")

    c.check("初始当前步是第一个阶段的起点，只有「阶段」这一层",
            glossary.INITIAL_STEP == stack(step_at("写释义草稿")) and intake.INITIAL_STEP == stack(step_at("列目录")),
            (glossary.INITIAL_STEP, intake.INITIAL_STEP))

    def a_call(number, status=CallStatus.SUCCEEDED, result=None):
        """一个只有记录本步用得着的部分的工具调用：依据序号、终态、返回值。"""
        made = ToolCall(tool="无所谓", params={}, proposer="selector", basis=(number, "无所谓", {}))
        made.status, made.result = status, result
        return made

    # 规则一：工具调用没成功，当前步不动。
    before = step_at("确认", 2, loop=(1, 3, 2))
    def main_of(value):
        """第五步起记录本步返回地址栈；这组检查只看主线那一层，插入段另有检查。"""
        return value.get("主线") if isinstance(value, dict) and "主线" in value else value

    c.check("记录本步规则一：工具调用已失败时当前步原样不动",
            glossary.record_step(before, a_call(3, CallStatus.FAILED)) == before,
            glossary.record_step(before, a_call(3, CallStatus.FAILED)))
    # 规则二：告知异常且返回值「重做」回到该阶段起点；终止不动。
    redo = main_of(intake.record_step(step_at("登记", 1, loop=(1, 1, 3)), a_call(6, result="重做")))
    stop = main_of(intake.record_step(step_at("登记", 1, loop=(1, 1, 3)), a_call(6, result="被动终止")))
    c.check("记录本步规则二：告知异常选重做，当前步回到该阶段起点『登记』；选终止时不动",
            redo == step_at("登记") and stop == step_at("登记", 1, loop=(1, 1, 3)), (redo, stop))
    # 规则三：其余写该步的阶段与阶段内序号，落在循环段里时带起止与第几次。
    c.check("记录本步规则三：不在循环段里的一步只写阶段与阶段内序号",
            main_of(glossary.record_step(step_at("写释义草稿"), a_call(2))) == step_at("写释义草稿", 2),
            glossary.record_step(step_at("写释义草稿"), a_call(2)))

    # 「第几次」：进这段循环记 1；同一次里沿用；回到段首加 1；段尾被跳过、停在段中时回段首也加 1。
    first = main_of(glossary.record_step(step_at("写释义草稿", 2), a_call(3)))
    same = main_of(glossary.record_step(first, a_call(4)))
    again = main_of(glossary.record_step(step_at("确认", 3, loop=(1, 3, 1)), a_call(3)))
    skipped = main_of(glossary.record_step(step_at("确认", 2, loop=(1, 3, 1)), a_call(3)))
    c.check("「第几次」：从循环段外进来记第 1 次",
            first == step_at("确认", 1, loop=(1, 3, 1)), first)
    c.check("「第几次」：同一次里往后走，第几次沿用",
            same == step_at("确认", 2, loop=(1, 3, 1)), same)
    c.check("「第几次」：走完段尾又回到段首，第几次加一",
            again == step_at("确认", 1, loop=(1, 3, 2)), again)
    c.check("「第几次」：段尾被前置条件跳过、上一步停在段中时，回到段首也加一"
            "（4.10 节字面判据只说「上一步是段尾」，这里按 2026-09-16 主会话裁定放宽成「序号不比上一步大」）",
            skipped == step_at("确认", 1, loop=(1, 3, 2)), skipped)

    # 不合法：内层必须属于外层，不合法按任务定义错误处理，不再降级成给使用者看的告知异常。
    bad = [
        ("阶段不存在", {"阶段": "没有这个阶段"}),
        ("步骤越界", step_at("确认", 9, loop=(1, 3, 1))),
        ("循环段对不上", step_at("确认", 2, loop=(1, 2, 1))),
        ("第几次不是正整数", step_at("确认", 2, loop=(1, 3, 0))),
        ("在循环段里却没有循环这一层", step_at("确认", 2)),
        ("不在循环段里却写了循环", step_at("写释义草稿", 2, loop=(1, 3, 1))),
        ("有循环却没有步骤", {"阶段": "确认", "循环": {"起": 1, "止": 3, "第几次": 1}}),
    ]
    for name, value in bad:
        try:
            glossary.select_call({}, value)
            raised = None
        except DefinitionError as error:
            raised = error.reason
        c.check(f"不合法的当前步（{name}）抛任务定义错误，原因是一句能读的话", bool(raised), raised)

    c.check("当前步合法时调用选择照常返回候选（不合法判据没有误伤正常值）",
            glossary.select_call({**glossary.SLOTS, "术语": "基线"}, step_at("写释义草稿"))[0] == DRAFT_TOOL,
            glossary.select_call({**glossary.SLOTS, "术语": "基线"}, step_at("写释义草稿")))

    # 显示用的两个接口。
    texts = [glossary.step_text(step_at("写释义草稿")),
             glossary.step_text(step_at("写释义草稿", 2)),
             glossary.step_text(step_at("确认", 2, loop=(1, 3, 2)))]
    c.check("step_text 三种形状：阶段起点、不在循环段里、在循环段里（写明起止、第几次与上限）",
            texts == ["当前步：『写释义草稿』阶段，还没有做完任何一步",
                      "当前步：『写释义草稿』阶段，做完了第 2 步『根据术语（与原文片段，若有）生成释义草稿』",
                      "当前步：『确认』阶段，第 1 到第 3 步循环的第 2 次（最多 5 次），做完了第 2 步『理解使用者的回复』"],
            texts)
    view = glossary.step_view(step_at("确认", 2, loop=(1, 3, 2)))
    c.check("step_view 给出阶段名、阶段内序号、这一步的说明、循环起止与第几次与上限、是不是段尾，页面不用自己算",
            view == {"阶段": "确认", "步骤": 2, "说明": "理解使用者的回复",
                     "循环": {"起": 1, "止": 3, "第几次": 2, "最多": 5}, "是段尾": False, "插入": []}, view)
    c.check("step_text 读不懂时照实说，不抛错（旧运行文件或宿主给了别的东西）",
            glossary.step_text({"阶段": "没有这个阶段"}).startswith("当前步：读不出来")
            and glossary.step_view("不是字典") is None, glossary.step_text({"阶段": "没有这个阶段"}))
    c.check("阶段名这个键就叫「阶段」（当前步的第一层，与告知异常参数里的写法一致）", STEP_STAGE == "阶段")
    return c
