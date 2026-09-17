"""验证脚本的机器检查：更新组先核对再写入。"""

from __future__ import annotations

from tod_kernel import kernel
from tod_kernel.tools import build_table
from tod_kernel.kernel import DATA_CHANGED, STEP_CHANGED, INBOX, OUTBOX, Change, StepChange, KernelError, Mailbox
from tod_kernel.observe import MemoryCollector
from tod_kernel.verify.base import Checker, banner, intake_def
from tod_kernel.verify.intake import INTAKE_TOOLS


def update_group_checks() -> Checker:
    """更新组先核对整组再写入（第四步第 7 节第 13 条③）：任一项旧值不符，整组不写、一个事件也不发。

    直接调写入口，不跑任务：这一组要验的是写入口本身的规矩。
    """
    title = "第四步：更新组先核对再写入"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    task_def = intake_def("intake.json")
    task_id = "T-update-group"
    stream = kernel.EventStream(task_id)
    collector = MemoryCollector()
    stream.subscribe(collector)
    inbox, outbox = Mailbox(stream, task_id, INBOX), Mailbox(stream, task_id, OUTBOX)
    task = kernel.new_task(task_id, task_def, build_table(task_def, INTAKE_TOOLS), inbox, outbox, stream)
    kernel.update_state(task, task.init_changes)

    data_before, events_before = dict(task.data), len(collector.events)
    bad = [Change("登记进度", 0, 1, 7), Change("清单文件路径", "对不上的旧值", "清单.md", 7)]
    raised = None
    try:
        kernel.update_state(task, bad)
    except KernelError as error:
        raised = error.reason
    c.check("整组里有一项旧值不符：抛内核错误，错误话里写明是哪个槽位、当前值与声明值",
            raised is not None and "清单文件路径" in raised and "对不上的旧值" in raised, raised)
    c.check("那一组整组没写：前面那条合格的变更也没落到数据上", task.data == data_before, task.data)
    c.check("那一组一个事件也没发：事件条数与调用前一样", len(collector.events) == events_before,
            [e.name for e in collector.events[events_before:]])

    good = [Change("登记进度", 0, 1, 7), Change("清单文件路径", None, "清单.md", 7)]
    kernel.update_state(task, good)
    c.check("整组都合格：两条都写进数据，发出两条「数据变更」",
            task.data["登记进度"] == 1 and task.data["清单文件路径"] == "清单.md"
            and [e.name for e in collector.events[events_before:]] == [DATA_CHANGED, DATA_CHANGED],
            (task.data["登记进度"], [e.name for e in collector.events[events_before:]]))

    events_before = len(collector.events)
    twice = [Change("登记进度", 1, 2, 8), Change("登记进度", 2, 3, 8)]
    kernel.update_state(task, twice)
    c.check("同一组里两条写同一个槽位：后一条按前一条的新值核对，两条都写成，与逐条写入的次序一致",
            task.data["登记进度"] == 3 and len(collector.events) - events_before == 2, task.data["登记进度"])

    events_before = len(collector.events)
    mixed = [Change("登记进度", 3, 4, 9), StepChange({"阶段": "没有这个阶段"}, 9)]
    raised = None
    try:
        kernel.update_state(task, mixed)
    except Exception as error:  # 阶段不存在时 step_text／step_view 读不出来，但不该抛错
        raised = repr(error)
    c.check("更新组里可以同时有数据变更与当前步更新，两样一次写完", raised is None and task.data["登记进度"] == 4, raised)
    c.check("同一次写入口发出的事件：两条（一条数据变更、一条当前步变化）",
            [e.name for e in collector.events[events_before:]] == [DATA_CHANGED, STEP_CHANGED],
            [e.name for e in collector.events[events_before:]])
    outbox.close("host")
    return c
