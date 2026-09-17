"""验证脚本的机器检查：控制台的问答打印。"""

from __future__ import annotations

from tod_kernel.console import PRESET_ANSWER_PREFIX, QUESTION_PREFIX
from tod_kernel.verify.base import Checker, banner, intake_def, run_scenario
from tod_kernel.verify.intake import INTAKE_ANSWERS, INTAKE_TOOLS


# ───────────────────────── 检查组：控制台的问答打印 ─────────────────────────


def console_transcript_checks() -> Checker:
    """第四步验证目标四：控制台两种方式打印同样的问答，预设方式的断言一条不丢。

    截获标准输出跑材料接入登记正常场景，看问答两行交替出现；再经控制台的预设方式跑同一个场景，
    比对断言条数与直接跑验证脚本相同。
    """
    import contextlib
    import io

    from tod_kernel import console as console_module
    from tod_kernel.verify import SCENARIOS  # 场景表在包的 __init__ 里，它导入本模块，所以在函数里导入

    title = "第四步：控制台的问答打印"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        run_scenario("T-console-print", intake_def(), INTAKE_ANSWERS, INTAKE_TOOLS, console=False, show=True)
    marks = [line for line in buffer.getvalue().splitlines()
             if line.startswith(QUESTION_PREFIX) or line.startswith(PRESET_ANSWER_PREFIX)]
    c.check("材料接入登记正常场景打印出三行「系统：」与三行「使用者（预设）：」，两者交替出现",
            [QUESTION_PREFIX if line.startswith(QUESTION_PREFIX) else PRESET_ANSWER_PREFIX for line in marks]
            == [QUESTION_PREFIX, PRESET_ANSWER_PREFIX] * 3, marks)
    c.check("问的是三个文件纳不纳入，答的是 是、否、是",
            [line[len(PRESET_ANSWER_PREFIX):] for line in marks if line.startswith(PRESET_ANSWER_PREFIX)] == ["是", "否", "是"],
            marks)

    console_output = io.StringIO()
    with contextlib.redirect_stdout(console_output):
        via_console = console_module.run_preset(1)
    direct_output = io.StringIO()
    with contextlib.redirect_stdout(direct_output):
        direct = SCENARIOS[0][1]()
    c.check("经控制台跑 1 号场景与直接跑验证脚本，断言条数相同，而且两边都全过",
            len(via_console.results) == len(direct.results)
            and via_console.passed == len(via_console.results) == direct.passed,
            (len(via_console.results), via_console.passed, len(direct.results), direct.passed))
    text = console_output.getvalue()
    c.check(f"控制台跑完打印「断言：通过 {len(via_console.results)} 条」，且不逐条打印断言",
            f"断言：通过 {len(via_console.results)} 条" in text and "[通过]" not in text, text[-300:])
    c.check("控制台跑场景时同样打印一问一答",
            text.count(QUESTION_PREFIX) == 3 and text.count(PRESET_ANSWER_PREFIX) == 3, text[:300])
    return c
