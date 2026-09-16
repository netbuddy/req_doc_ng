# 第零步实施报告

本报告记录第零步（最小循环）的实现过程。步骤文档第 5 节原有八个工作项，2026-09-14 增补了第九到第十一项，本报告逐项贴出检查代码与实际输出，末尾单列「实现与文档的偏差与疑问」。报告编于 2026-09-14。

## 1 结论

- 八个工作项已按顺序做完，代码在本目录下：`kernel.py`、`tools.py`、`task_travel.py`、`observe.py`、`verify.py`、`__init__.py`。
- 在仓根下运行 `python -m tod_kernel.verify`，四个场景的断言全部通过：场景一 46 条、场景二 15 条、场景三 35 条、场景四 28 条，退出码为 0。连续运行二十次，每次都全部通过。
- 为确认断言确实能发现问题，我在临时副本里故意改坏过三处代码，验证脚本三次都报告了失败，详见第 3 节末尾。
- 步骤文档 6.1 节的验收记录没有填，那是负责人验收时填写的；步骤文档没有改动；代码没有提交。
- 实施前向编制实施简报的会话提过五个问题，对方全部同意了我提出的做法，这些裁定已写进第 4 节。
- 2026-09-14 增补的第九到第十一项（追踪事件、文件订阅者、查看器）也已做完，记录在第 5 节。增补后验证脚本的断言增加到：场景一 72 条、场景二 23 条、场景三 61 条、场景四 48 条，四个场景全部通过；场景一的查看页在仓根的 runs/T-scenario-1.html。此后又做过五轮修订，最新的断言条数与结论见第 10 节。

## 2 怎么运行

- 验证脚本：在仓根下运行 `python -m tod_kernel.verify`，只依赖 Python 3.12 标准库。本机环境变量 PYTHONPATH 被其他软件改过，所以实际运行时用的是 `env -u PYTHONPATH python3 -m tod_kernel.verify`。
- 逐项检查脚本：第一到第七项的检查脚本放在会话临时目录，没有进仓库。下文贴出了每个脚本的完整代码，运行方式都是在仓根下执行 `PYTHONPATH=. python3 <临时目录>/itemN.py <临时目录>`（第二个参数用来导入临时任务定义 `stub_task.py`）。
- 术语说明：2026-09-14 用户裁定把「能力」整体改名为「工具」（见第 8 节）。第 1 到第 7 节的文字已统一改称「工具」，但贴出的代码与输出保持当时原样，其中的 capability、capabilities.py、「能力」，就是现在的 tool、tools.py、「工具」。
- 输出说明：第一到第七项的输出是做完该项当时运行得到的。第二到第四项用的临时工具到第六项已按要求拆掉，所以这几项的检查现在无法原样重跑。下文另外注明了两处重跑：第五项的检查脚本修改后重跑过，第六项在修改打印格式后重跑过。

## 3 逐项记录

（本节写于「能力」改名为「工具」之前，文字已统一改称「工具」，原称「能力」；贴出的代码与输出保持当时原样，其中的 capability、能力，即现在的 tool、工具。）

### 第一项：数据结构、事件流、状态更新函数、两个订阅者

步骤文档要求能看到：新建任务、初始化、置执行中之后，控制台与内存收集器里都是三条数据变更加一条任务状态变化；往工具表登记一个工具，再按名字取出来是同一个对象。实际结果与要求相符。此时控制台打印还是一行一个事件的格式。

检查代码 `item1.py`：

```python
import types
from tod_kernel import kernel as k
from tod_kernel.capabilities import Capability, CapabilityTable
from tod_kernel.observe import ConsolePrinter, MemoryCollector

task_def = types.SimpleNamespace(SLOTS={"目的地": None, "日期": None, "事由": None})
stream = k.EventStream("T-item1")
collector = MemoryCollector()
stream.subscribe(collector)
stream.subscribe(ConsolePrinter())
table = CapabilityTable()
task = k.new_task("T-item1", task_def, table, None, stream)
k.update_state(task, task.init_changes)
k.update_state(task, k.TaskStatus.RUNNING)
print("内存收集器事件数:", len(collector.events), [e.name for e in collector.events])
cap = Capability("ask", ("slot",), impl=lambda ctx: None)
table.register(cap)
print("按名字取回的是同一个能力:", table.get("ask") is cap)
```

实际输出：

```
#1 DATA_CHANGED action=None {'slot': '目的地', 'old': None, 'new': None, 'source': '初始化'}
#2 DATA_CHANGED action=None {'slot': '日期', 'old': None, 'new': None, 'source': '初始化'}
#3 DATA_CHANGED action=None {'slot': '事由', 'old': None, 'new': None, 'source': '初始化'}
#4 TASK_STATUS_CHANGED action=None {'old_status': <TaskStatus.NOT_STARTED: '未开始'>, 'new_status': <TaskStatus.RUNNING: '执行中'>}
内存收集器事件数: 4 ['DATA_CHANGED', 'DATA_CHANGED', 'DATA_CHANGED', 'TASK_STATUS_CHANGED']
按名字取回的是同一个能力: True
```

### 第二项：结果检查函数、行动选择函数、记状态函数

步骤文档要求能看到：行动选择在三个槽位都为空时多出「行动提出」和「已提出」两个事件；把工具表清空后再调用会报错退出。实际结果与要求相符。按编制会话的要求，「行动提出」事件先于「已提出」发出。检查用的临时任务定义 `stub_task.py` 与第七项的正式定义写法相同。工具表里登记的是占位工具 ask，第六项已把它换成真正的实现。

检查代码 `stub_task.py`：

```python
# 检查用的临时任务定义（第七项之前用），与第七项的正式定义同形。
ORDER = ("目的地", "日期", "事由")
SLOTS = {s: None for s in ORDER}
def is_done(data):
    return all(data.get(s) is not None for s in ORDER)
def select_action(data):
    for no, slot in enumerate(ORDER, 1):
        if data.get(slot) is None:
            return ("ask", {"slot": slot}, (no, f"{slot}尚未填写", data.get(slot)))
    return None
```

检查代码 `item2.py`：

```python
import sys
sys.path.insert(0, sys.argv[1])
from tod_kernel import kernel as k
from tod_kernel.capabilities import CapabilityTable, build_table
from tod_kernel.observe import ConsolePrinter, MemoryCollector
import stub_task

stream = k.EventStream("T-item2")
stream.subscribe(MemoryCollector())
stream.subscribe(ConsolePrinter())
task = k.new_task("T-item2", stub_task, build_table(), None, stream)
k.update_state(task, task.init_changes)
k.update_state(task, k.TaskStatus.RUNNING)
print("结果检查:", k.check_done(task))
print("--- 调用行动选择 ---")
action = k.select_action(task)
print("行动对象:", action)
print("--- 清空能力表再调一次 ---")
task.capabilities = CapabilityTable()
try:
    k.select_action(task)
except k.KernelError as e:
    print("内核错误:", e.reason, "| 原始输出:", e.raw)
```

实际输出：

```
#1 DATA_CHANGED action=None {'slot': '目的地', 'old': None, 'new': None, 'source': '初始化'}
#2 DATA_CHANGED action=None {'slot': '日期', 'old': None, 'new': None, 'source': '初始化'}
#3 DATA_CHANGED action=None {'slot': '事由', 'old': None, 'new': None, 'source': '初始化'}
#4 TASK_STATUS_CHANGED action=None {'old_status': <TaskStatus.NOT_STARTED: '未开始'>, 'new_status': <TaskStatus.RUNNING: '执行中'>}
结果检查: False
--- 调用行动选择 ---
#5 ACTION_PROPOSED action=1 {'capability': 'ask', 'params': {'slot': '目的地'}, 'proposer': 'selector', 'basis': (1, '目的地尚未填写', None)}
#6 ACTION_STATUS_CHANGED action=1 {'new_status': <ActionStatus.PROPOSED: '已提出'>, 'note': '行动选择'}
行动对象: Action(action_id=1, capability='ask', params={'slot': '目的地'}, proposer='selector', basis=(1, '目的地尚未填写', None), status=<ActionStatus.PROPOSED: '已提出'>, result=None, changes=[])
--- 清空能力表再调一次 ---
内核错误: 能力未登记：'ask' | 原始输出: ('ask', {'slot': '目的地'}, (1, '目的地尚未填写', None))
```

### 第三项：执行控制函数和行动执行函数

步骤文档要求能看到：行动经过执行控制、行动执行和状态更新之后，多出「已获准」「已成功」和一条来源为行动 1 的数据变更。实际结果与要求相符。这里用的临时实现（直接把槽位填成「固定值」）在第六项已拆掉。

检查代码 `item3.py`：

```python
import sys
sys.path.insert(0, sys.argv[1])
from tod_kernel import kernel as k
from tod_kernel.capabilities import build_table
from tod_kernel.observe import ConsolePrinter, MemoryCollector
import stub_task

stream = k.EventStream("T-item3")
stream.subscribe(MemoryCollector())
stream.subscribe(ConsolePrinter())
task = k.new_task("T-item3", stub_task, build_table(), None, stream)
k.update_state(task, task.init_changes)
k.update_state(task, k.TaskStatus.RUNNING)
action = k.select_action(task)
print("--- 执行控制、行动执行、状态更新 ---")
k.control(task, action)
k.execute(task, action)
k.update_state(task, action.changes)
print("任务数据:", task.data)
```

实际输出：

```
#1 DATA_CHANGED action=None {'slot': '目的地', 'old': None, 'new': None, 'source': '初始化'}
#2 DATA_CHANGED action=None {'slot': '日期', 'old': None, 'new': None, 'source': '初始化'}
#3 DATA_CHANGED action=None {'slot': '事由', 'old': None, 'new': None, 'source': '初始化'}
#4 TASK_STATUS_CHANGED action=None {'old_status': <TaskStatus.NOT_STARTED: '未开始'>, 'new_status': <TaskStatus.RUNNING: '执行中'>}
#5 ACTION_PROPOSED action=1 {'capability': 'ask', 'params': {'slot': '目的地'}, 'proposer': 'selector', 'basis': (1, '目的地尚未填写', None)}
#6 ACTION_STATUS_CHANGED action=1 {'new_status': <ActionStatus.PROPOSED: '已提出'>, 'note': '行动选择'}
--- 执行控制、行动执行、状态更新 ---
#7 ACTION_STATUS_CHANGED action=1 {'new_status': <ActionStatus.APPROVED: '已获准'>, 'note': '本步恒允许'}
#8 ACTION_STATUS_CHANGED action=1 {'new_status': <ActionStatus.SUCCEEDED: '已成功'>, 'note': '临时实现直接填固定值', 'result': '固定值'}
#9 DATA_CHANGED action=1 {'slot': '目的地', 'old': None, 'new': '固定值', 'source': 1}
任务数据: {'目的地': '固定值', '日期': None, '事由': None}
```

### 第四项：启动任务函数（内核循环）与打印分组

步骤文档要求能看到：完整的一次任务，包括初始化一段、三个行动各一段、结束一段；事件序号连续，「已完成」状态变化和「任务结束」各恰好一次，所有事件的任务标识相同。实际结果与要求相符。

检查代码 `item4.py`：

```python
import sys
sys.path.insert(0, sys.argv[1])
from tod_kernel import kernel as k
from tod_kernel.capabilities import build_table
from tod_kernel.observe import ConsolePrinter, MemoryCollector
import stub_task

collector = MemoryCollector()
stream = k.EventStream("T-item4")
stream.subscribe(collector)
stream.subscribe(ConsolePrinter())
task = k.start_task("T-item4", stub_task, build_table(), None, stream)
ev = collector.events
print("--- 检查 ---")
print("序号连续无跳号:", [e.seq for e in ev] == list(range(1, len(ev) + 1)))
done = [e for e in ev if e.name == k.TASK_STATUS_CHANGED and e.payload["new_status"] == k.TaskStatus.DONE]
print("「已完成」状态变化次数:", len(done))
print("「任务结束」次数:", sum(e.name == k.TASK_ENDED for e in ev))
print("任务标识全部相同:", {e.task_id for e in ev})
print("任务状态:", task.status, "行动数:", len(task.actions), "结束记录:", task.end_record)
```

实际输出：

```
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 行动 1 ──
  #5 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #6 行动状态变化：已提出，说明：行动选择
  #7 行动状态变化：已获准，说明：本步恒允许
  #8 行动状态变化：已成功，说明：临时实现直接填固定值，返回值 '固定值'
  #9 数据变更：槽位「目的地」 未填写(None) → '固定值'，来源 1
── 行动 2 ──
  #10 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #11 行动状态变化：已提出，说明：行动选择
  #12 行动状态变化：已获准，说明：本步恒允许
  #13 行动状态变化：已成功，说明：临时实现直接填固定值，返回值 '固定值'
  #14 数据变更：槽位「日期」 未填写(None) → '固定值'，来源 2
── 行动 3 ──
  #15 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #16 行动状态变化：已提出，说明：行动选择
  #17 行动状态变化：已获准，说明：本步恒允许
  #18 行动状态变化：已成功，说明：临时实现直接填固定值，返回值 '固定值'
  #19 数据变更：槽位「事由」 未填写(None) → '固定值'，来源 3
── 结束 ──
  #20 任务状态变化：执行中 → 已完成
  #21 任务结束：终态 已完成，原因：完成条件成立
--- 检查 ---
序号连续无跳号: True
「已完成」状态变化次数: 1
「任务结束」次数: 1
任务标识全部相同: {'T-item4'}
任务状态: TaskStatus.DONE 行动数: 3 结束记录: EndRecord(final_status=<TaskStatus.DONE: '已完成'>, reason='完成条件成立')
```

### 第五项：消息和邮箱

步骤文档要求能看到：放三条消息、按条件取走两条后，控制台上有三个「消息放入」和两个「消息取出」，剩下那条的到达序号对得上；阻塞取消息时，主线程一秒后放入，子线程立即拿到；主线程改为关闭邮箱时，子线程立即拿到空。实际结果与要求相符。关于重跑：检查脚本第一版为了核对剩下那条，又取走了第三条消息，与「只取两条」不一致。改为只查看队列、不取走之后重跑，下面贴的是重跑的输出。控制台上「行动 1」「行动 7」这类分段标题，是按消息收件人的编号分的组。

检查代码 `item5.py`：

```python
import threading, time
from tod_kernel import kernel as k
from tod_kernel.observe import ConsolePrinter, MemoryCollector

stream = k.EventStream("T-item5")
collector = MemoryCollector()
stream.subscribe(collector)
stream.subscribe(ConsolePrinter())
mb = k.Mailbox(stream, "T-item5")
print("--- 放三条，按条件取走两条 ---")
mb.put(k.Message("answer", 1, "上海"))
mb.put(k.Message("answer", 2, "9 月 20 日"))
mb.put(k.Message("answer", 3, "客户拜访"))
print("取收件人 3:", mb.take(lambda m: m.recipient == 3, block=False))
print("取收件人 1:", mb.take(lambda m: m.recipient == 1, block=False))
rest = list(mb._queue)  # 只看不取，检查用
print("队列里剩下:", rest, "它是第二条放入的，到达序号对得上:", len(rest) == 1 and rest[0].seq == 2)
print("放入/取出事件数:", sum(e.name == k.MESSAGE_PUT for e in collector.events), sum(e.name == k.MESSAGE_TAKEN for e in collector.events))
mb.take(lambda m: True, block=False)  # 清掉剩下那条，以免影响后面的阻塞检查
print("非阻塞取不存在的:", mb.take(lambda m: m.recipient == 9, block=False))

def blocking_take(box, got):
    t0 = time.monotonic()
    got["msg"] = box.take(lambda m: m.recipient == 7, block=True)
    got["waited"] = time.monotonic() - t0

print("--- 子线程阻塞取，主线程一秒后放 ---")
got = {}
th = threading.Thread(target=blocking_take, args=(mb, got)); th.start()
time.sleep(1); t_put = time.monotonic()
mb.put(k.Message("answer", 7, "迟到的回答"))
th.join()
print("子线程拿到:", got["msg"], f"等待 {got['waited']:.2f} 秒，线程已结束:", not th.is_alive())

print("--- 再来一次，主线程一秒后关闭邮箱 ---")
got = {}
th = threading.Thread(target=blocking_take, args=(mb, got)); th.start()
time.sleep(1)
mb.close()
th.join()
print("子线程拿到:", got["msg"], f"等待 {got['waited']:.2f} 秒，线程已结束:", not th.is_alive())
print("事件名序列:", [e.name for e in collector.events])
```

实际输出：

```
--- 放三条，按条件取走两条 ---
── 行动 1 ──
  #1 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
── 行动 2 ──
  #2 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
── 行动 3 ──
  #3 消息放入：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #4 消息取出：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
取收件人 3: Message(kind='answer', recipient=3, content='客户拜访', seq=3)
── 行动 1 ──
  #5 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
取收件人 1: Message(kind='answer', recipient=1, content='上海', seq=1)
队列里剩下: [Message(kind='answer', recipient=2, content='9 月 20 日', seq=2)] 它是第二条放入的，到达序号对得上: True
放入/取出事件数: 3 2
── 行动 2 ──
  #6 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
非阻塞取不存在的: None
--- 子线程阻塞取，主线程一秒后放 ---
── 行动 7 ──
  #7 消息放入：类型 answer，收件人 7，内容 '迟到的回答'，到达序号 4
  #8 消息取出：类型 answer，收件人 7，内容 '迟到的回答'，到达序号 4
子线程拿到: Message(kind='answer', recipient=7, content='迟到的回答', seq=4) 等待 1.00 秒，线程已结束: True
--- 再来一次，主线程一秒后关闭邮箱 ---
子线程拿到: None 等待 1.00 秒，线程已结束: True
事件名序列: ['MESSAGE_PUT', 'MESSAGE_PUT', 'MESSAGE_PUT', 'MESSAGE_TAKEN', 'MESSAGE_TAKEN', 'MESSAGE_TAKEN', 'MESSAGE_PUT', 'MESSAGE_TAKEN']
```

### 第六项：工具表里真正的询问

步骤文档要求能看到：主线程收到「等待中」事件后放回答，该行动的事件依次是「已获准、等待中、消息放入、消息取出、已成功」；主线程改为关闭邮箱时，依次是「已获准、等待中、已失败（没有可用的回答）」。实际结果与要求相符。临时实现已拆掉。关于重跑：第一次运行时，失败行动的返回值被打印成「未填写(None)」，而「未填写」是槽位的说法，用在返回值上不对，所以改成直接打印 None 并重跑，下面贴的是重跑的输出。

检查代码 `item6.py`：

```python
import queue, sys, threading
sys.path.insert(0, sys.argv[1])
from tod_kernel import kernel as k
from tod_kernel.capabilities import build_table
from tod_kernel.observe import ConsolePrinter, MemoryCollector
import stub_task

def run_once(label, reply):
    print(f"===== {label} =====")
    task_id = f"T-item6-{label}"
    stream = k.EventStream(task_id)
    collector = MemoryCollector()
    stream.subscribe(collector)
    stream.subscribe(ConsolePrinter())
    waiting = queue.Queue()
    stream.subscribe(lambda e: waiting.put(e.action_id) if e.name == k.ACTION_STATUS_CHANGED and e.payload["new_status"] == k.ActionStatus.WAITING else None)
    mb = k.Mailbox(stream, task_id)
    task = k.new_task(task_id, stub_task, build_table(), mb, stream)
    k.update_state(task, task.init_changes)
    k.update_state(task, k.TaskStatus.RUNNING)
    action = k.select_action(task)
    k.control(task, action)
    th = threading.Thread(target=k.execute, args=(task, action))
    th.start()
    action_id = waiting.get(timeout=5)  # 主线程收到「等待中」事件
    if reply:
        mb.put(k.Message("answer", action_id, "上海"))
    else:
        mb.close()
    th.join()
    names = []
    for e in collector.events:
        if e.action_id == action.action_id and e.seq > 6:
            names.append(e.payload.get("new_status", e.name).value if e.name == k.ACTION_STATUS_CHANGED else e.name)
    print("该行动在控制之后的事件:", names)
    print("行动:", action.status, action.result, action.changes)

run_once("回答到达", reply=True)
run_once("关闭邮箱", reply=False)
```

实际输出：

```
===== 回答到达 =====
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 行动 1 ──
  #5 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #6 行动状态变化：已提出，说明：行动选择
  #7 行动状态变化：已获准，说明：本步恒允许
  #8 行动状态变化：等待中，说明：已向使用者提问
  #9 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #10 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #11 行动状态变化：已成功，说明：回答到达，返回值 '上海'
该行动在控制之后的事件: ['已获准', '等待中', 'MESSAGE_PUT', 'MESSAGE_TAKEN', '已成功']
行动: ActionStatus.SUCCEEDED 上海 [Change(slot='目的地', old=None, new='上海', source=1)]
===== 关闭邮箱 =====
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 行动 1 ──
  #5 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #6 行动状态变化：已提出，说明：行动选择
  #7 行动状态变化：已获准，说明：本步恒允许
  #8 行动状态变化：等待中，说明：已向使用者提问
  #9 行动状态变化：已失败，说明：没有可用的回答，返回值 None
该行动在控制之后的事件: ['已获准', '等待中', '已失败']
行动: ActionStatus.FAILED None []
```

### 第七项：出差申请单的任务定义

步骤文档要求能看到：按第八项的方式跑启动任务，三个行动的事件都依次是「提出、已提出、已获准、等待中、消息放入、消息取出、已成功、数据变更」，数据变更的新值是脚本放入的回答。实际结果与要求相符。场景二、场景四用的两份变体是 `task_travel.ALL_FILLED` 和 `task_travel.DATE_FILLED`。

检查代码 `item7.py`：

```python
import queue, threading
from tod_kernel import kernel as k
from tod_kernel import task_travel
from tod_kernel.capabilities import build_table
from tod_kernel.observe import ConsolePrinter, MemoryCollector

answers = {"目的地": "上海", "日期": "9 月 20 日", "事由": "客户拜访"}
task_id = "T-item7"
stream = k.EventStream(task_id)
collector = MemoryCollector()
stream.subscribe(collector)
stream.subscribe(ConsolePrinter())
pending, slots = queue.Queue(), {}
def answerer(e):
    if e.name == k.ACTION_PROPOSED:
        slots[e.action_id] = e.payload["params"]["slot"]
    elif e.name == k.ACTION_STATUS_CHANGED and e.payload["new_status"] == k.ActionStatus.WAITING:
        pending.put((e.action_id, slots[e.action_id]))
stream.subscribe(answerer)
mb = k.Mailbox(stream, task_id)
out = {}
def run():
    try:
        out["task"] = k.start_task(task_id, task_travel, build_table(), mb, stream)
    finally:
        pending.put(None)
th = threading.Thread(target=run); th.start()
while (item := pending.get(timeout=10)) is not None:
    mb.put(k.Message("answer", item[0], answers[item[1]]))
th.join()
print("--- 检查 ---")
for aid in (1, 2, 3):
    seq = []
    for e in collector.events:
        if e.action_id != aid:
            continue
        seq.append({k.ACTION_PROPOSED: "提出", k.MESSAGE_PUT: "消息放入", k.MESSAGE_TAKEN: "消息取出", k.DATA_CHANGED: "数据变更"}.get(e.name) or e.payload["new_status"].value)
    new = [e.payload["new"] for e in collector.events if e.name == k.DATA_CHANGED and e.action_id == aid]
    print(f"行动 {aid}:", "、".join(seq), "| 数据变更新值:", new)
print("任务数据:", out["task"].data)
```

实际输出：

```
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 行动 1 ──
  #5 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #6 行动状态变化：已提出，说明：行动选择
  #7 行动状态变化：已获准，说明：本步恒允许
  #8 行动状态变化：等待中，说明：已向使用者提问
  #9 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #10 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #11 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  #12 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
── 行动 2 ──
  #13 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #14 行动状态变化：已提出，说明：行动选择
  #15 行动状态变化：已获准，说明：本步恒允许
  #16 行动状态变化：等待中，说明：已向使用者提问
  #17 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #18 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #19 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  #20 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
── 行动 3 ──
  #21 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #22 行动状态变化：已提出，说明：行动选择
  #23 行动状态变化：已获准，说明：本步恒允许
  #24 行动状态变化：等待中，说明：已向使用者提问
  #25 消息放入：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #26 消息取出：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #27 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  #28 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3
── 结束 ──
  #29 任务状态变化：执行中 → 已完成
  #30 任务结束：终态 已完成，原因：完成条件成立
--- 检查 ---
行动 1: 提出、已提出、已获准、等待中、消息放入、消息取出、已成功、数据变更 | 数据变更新值: ['上海']
行动 2: 提出、已提出、已获准、等待中、消息放入、消息取出、已成功、数据变更 | 数据变更新值: ['9 月 20 日']
行动 3: 提出、已提出、已获准、等待中、消息放入、消息取出、已成功、数据变更 | 数据变更新值: ['客户拜访']
任务数据: {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}
```

### 第八项：验证脚本与重放

重放函数 `replay_data`、`action_history` 放在 `observe.py`，验证脚本是 `verify.py`。步骤文档要求脚本一次跑完、所有断言通过。实际结果：四个场景全部通过，退出码为 0。下面是 `python -m tod_kernel.verify` 的完整输出，其中场景一那一段就是交付要求里的「场景一控制台记录样本」。

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 行动 1 ──
  #5 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #6 行动状态变化：已提出，说明：行动选择
  #7 行动状态变化：已获准，说明：本步恒允许
  #8 行动状态变化：等待中，说明：已向使用者提问
  #9 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #10 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #11 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  #12 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
── 行动 2 ──
  #13 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #14 行动状态变化：已提出，说明：行动选择
  #15 行动状态变化：已获准，说明：本步恒允许
  #16 行动状态变化：等待中，说明：已向使用者提问
  #17 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #18 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #19 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  #20 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
── 行动 3 ──
  #21 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #22 行动状态变化：已提出，说明：行动选择
  #23 行动状态变化：已获准，说明：本步恒允许
  #24 行动状态变化：等待中，说明：已向使用者提问
  #25 消息放入：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #26 消息取出：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #27 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  #28 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3
── 结束 ──
  #29 任务状态变化：执行中 → 已完成
  #30 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的能力名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 3 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现能力名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有能力表模块、任务模块和观测模块
  [通过] 内存收集器里事件序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 结束 ──
  #5 任务状态变化：执行中 → 已完成
  #6 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个事件是「任务结束」
  [通过] 行动列表为空，只有结束记录
  [通过] 内存收集器里事件序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 行动 1 ──
  #5 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #6 行动状态变化：已提出，说明：行动选择
  #7 行动状态变化：已获准，说明：本步恒允许
  #8 行动状态变化：等待中，说明：已向使用者提问
  #9 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #10 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #11 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  #12 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
── 行动 2 ──
  #13 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #14 行动状态变化：已提出，说明：行动选择
  #15 行动状态变化：已获准，说明：本步恒允许
  #16 行动状态变化：等待中，说明：已向使用者提问
  #17 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #18 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #19 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  #20 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
── 行动 3 ──
  #21 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #22 行动状态变化：已提出，说明：行动选择
  #23 行动状态变化：已获准，说明：本步恒允许
  #24 行动状态变化：等待中，说明：已向使用者提问
  #25 行动状态变化：已失败，说明：没有可用的回答，返回值 None
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前没有「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动 3 已先记录进行动列表再抛错
  [通过] 内存收集器里事件序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  #1 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #2 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化
  #3 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #4 任务状态变化：未开始 → 执行中
── 行动 1 ──
  #5 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #6 行动状态变化：已提出，说明：行动选择
  #7 行动状态变化：已获准，说明：本步恒允许
  #8 行动状态变化：等待中，说明：已向使用者提问
  #9 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #10 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #11 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  #12 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
── 行动 2 ──
  #13 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #14 行动状态变化：已提出，说明：行动选择
  #15 行动状态变化：已获准，说明：本步恒允许
  #16 行动状态变化：等待中，说明：已向使用者提问
  #17 消息放入：类型 answer，收件人 2，内容 '客户拜访'，到达序号 2
  #18 消息取出：类型 answer，收件人 2，内容 '客户拜访'，到达序号 2
  #19 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  #20 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2
── 结束 ──
  #21 任务状态变化：执行中 → 已完成
  #22 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 内存收集器里事件序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布

════════════ 汇总 ════════════
场景一：正常流程：共 46 条断言，通过 46 条，失败 0 条
场景二：初始即完成：共 15 条断言，通过 15 条，失败 0 条
场景三：回答缺失：共 35 条断言，通过 35 条，失败 0 条
场景四：部分预填：共 28 条断言，通过 28 条，失败 0 条
结论：全部断言通过
```

### 故意改坏代码，检验断言能否发现问题

「断言全部通过」只有在断言确实能发现问题时才有意义，所以我把代码复制到临时目录，分三次各改坏一处，再运行验证脚本。这三处改动都没有留在仓库里。

| 改坏的地方 | 验证脚本的结果 |
|---|---|
| 状态更新照常写入行动 2 的变更，但不发这条「数据变更」事件 | 场景一有 6 条断言失败，场景三、场景四各有 4 条失败，失败的都是目标二、三、五里比对事件和数据的断言；场景二没有行动，所以全部通过。 |
| 选择规则不看槽位是否为空，而是按已填槽位的个数依次提问（步骤文档第 277 行说的「按计数器依次提问也能过」的那种写法） | 场景一、二、三全部通过；场景四有 6 条断言失败。这符合设计意图：只有场景四能区分「按数据选择」和「按计数器选择」。 |
| 询问在「等待中」之后多记一条「已获准」状态 | 场景一、三、四里，凡是核对状态序列的断言都失败了。 |

## 4 实现与文档的偏差与疑问

（本节写于「能力」改名为「工具」之前，文字已统一改称「工具」，原称「能力」；贴出的代码与输出保持当时原样，其中的 capability、能力，即现在的 tool、工具。）

下文「步骤文档第 N 行」指步骤文档《第零步：最小循环》2026-09-14 增补后版本的行号，「文件:行号」指本目录下增补完成后的代码；本节原稿写于第八项完成时，行号已按第 7 节改动完成后的代码重新对过。前五条是实施前向编制简报的会话提出、已得到同意的做法，其余是我自行做的决定。

### 4.1 文档没写清、我不得不自行决定的地方

1. 应答订阅者从「行动提出」事件里记下行动编号对应的槽位，而不是从「等待中」事件里取槽位，因为步骤文档原稿第 99 行说「等待中」事件带槽位，而事件表把这类事件的键固定为新状态、说明、返回值（现第 89 行）；编制会话确认那句是笔误，并已改为「按行动编号回查行动提出事件」（现第 117 行）（原应答订阅者，第 9 节已删去，改为宿主从发件箱读问题：verify.py:191）。
2. 事件流的「发布」用可重入锁把分配序号和派发给订阅者整体串行化，以保证多线程发布时订阅者收到事件的顺序等于序号顺序；步骤文档第 76 行没有写多线程的情况，编制会话要求在报告里写明这条约定（kernel.py:202、kernel.py:223）。
3. 验证脚本在跑场景期间临时替换 kernel 模块上的 update_state 和 execute，在调用前后抄下实际数据，与重放结果比对，跑完即恢复；这是仅供验证的手段，因为设计规定对象上不存快照，内核也没有钩子，否则步骤文档第 283、292 行要求的「实际数据」无从取得（verify.py:142、verify.py:206）。
4. 任务定义的槽位表常量命名为 SLOTS；两份变体由 variant 函数生成 SimpleNamespace，属性名与模块相同，因为简报只允许一个任务定义文件（task_travel.py:18、task_travel.py:44）。
5. 第一到第七项的检查脚本放在会话临时目录，没有进仓库，代码已完整贴在第 3 节。
6. 锁的获取顺序固定为「先邮箱锁、后事件流锁」，所以订阅者在派发过程中不得调用邮箱，否则可能死锁；步骤文档没有提到这一点，验证脚本的应答订阅者因此只投递、不碰邮箱（kernel.py:287）。
7. 订阅者在派发过程中再发布事件（嵌套发布）时，可重入锁不会死锁，但排在它后面的订阅者会先收到新事件、后收到旧事件；我没有拦截这种情况，只靠约定不这么做（kernel.py:235）。
8. 「发布」的签名是 publish(事件名, 内容, 行动编号)，序号、时刻、任务标识由事件流填写，没有让调用方传入一个完整的事件对象；步骤文档第 76 行写的是「发布(事件)」（kernel.py:247）。第 7 节起，发布还必须带记录方，各组件经 `stream.bind(记录方)` 拿到的发布句柄发布。
9. 发布时会检查事件名是否属于内核词表（增补后是七种状态事件名加八种追踪事件名），不属于就抛内核错误（kernel.py:217）。
10. 事件是冻结的数据类，内容字典发布时做了深拷贝，但字典本身仍可修改，所以步骤文档第 75 行的「发布后不可修改」对内容字典只靠约定保证（kernel.py:178）。
11. 事件的行动编号字段按以下规则取值：邮箱事件取整数收件人，收件人是 "loop" 时为空；数据变更事件取整数来源，来源是「初始化」时为空（kernel.py:357、kernel.py:410）。第 9 节起，邮箱事件优先取消息的所属行动 action_id。
12. 关闭邮箱不发事件，因为关闭不在步骤文档第 109 行列出的七种事件之内，所以只看事件流看不出宿主是在什么时候关闭邮箱的（kernel.py:339）。第 7 节已改：现在关闭邮箱发「邮箱关闭」状态事件，发起方由调用方自报。
13. 向已关闭的邮箱放消息会抛内核错误，步骤文档没有写这种情况（kernel.py:309）。
14. 行动对象刚创建时状态为 None，随后由记状态置为「已提出」，而且「行动提出」事件在它之前发出（kernel.py:154、kernel.py:480）。
15. 两个状态枚举的成员名用英文、值用中文，控制台因此直接打印中文状态（kernel.py:42、kernel.py:48）。
16. 初始化变更的旧值一律为 None，状态更新校验旧值时用 data.get(槽位)，槽位还不存在就按 None 处理（kernel.py:373、kernel.py:406）。
17. 变更组是逐条校验、逐条写入的，所以中途某条校验失败时，前面的变更已经写入并发了事件，不会回滚；步骤文档第 155 行只写了「逐条校验旧值后写入」（kernel.py:403）。
18. 状态更新不检查槽位是否在槽位表里，这项核对按步骤文档第 336 行留到第五步（kernel.py:402）。
19. 核对参数名时用集合相等，不看参数顺序（kernel.py:459）。
20. 完成条件函数与选择规则函数拿到的是数据的只读视图，而不是数据字典本身；步骤文档第 132、133 行只写了参数是 data（kernel.py:426、kernel.py:450）。
21. 行动执行在工具实现返回后检查行动是否已到终态，没到就抛内核错误；步骤文档第 122 行规定工具实现「必须」置终态，但没说由谁检查（kernel.py:536）。
22. 回答不是字符串时，询问实现把行动记为「已失败」并写明原因，随后由循环抛内核错误，我把这种做法当作步骤文档第 125 行「按程序错误处理」的落实方式（tools.py:72）。
23. 询问生成的变更，旧值取只读视图里的当前值，而步骤文档第 195 行的伪代码写的是字面 None；本步两者总是相同（tools.py:76）。
24. Tool 与 ToolTable 定义在 tools.py，内核只按属性名使用工具表与工具，不导入这个模块（tools.py:32）。
25. 同一个工具名重复登记时抛内核错误（tools.py:34）。
26. 控制台打印给事件名配了中文标签，槽位值为 None 时打印成「未填写(None)」；分段规则是：有行动编号的事件归该行动的段，没有行动编号的事件在置执行中之前归初始化段、之后归结束段（observe.py:149、observe.py:176）。
27. 验证脚本收内核线程时最多等 30 秒，这只用来防止脚本在代码有缺陷时挂死，与邮箱语义无关（verify.py:77）。
28. 步骤文档第 306 行说场景四「没有针对日期的数据变更事件」，但初始化本身会为日期发一条数据变更，所以我按「除初始化那一条外没有」来断言（verify.py:648）。
29. 场景三额外断言了「没有任务结束事件、任务状态仍是执行中」，依据是步骤文档第 181、182 行的伪代码在行动失败时直接抛错、不写结束记录，但文档正文没有明说这一点（verify.py:621）。
30. 验证脚本在文档所列断言之外还加了几类检查：四个场景都做完整性检查；检查回答由主线程放入、由内核线程取出；检查「行动提出」先于「已提出」；检查内核里不出现工具名。所以断言条数比文档列出的多（verify.py:233、verify.py:307、verify.py:510）。
31. 某个前提断言失败时，依赖它的后续断言会被跳过（例如拿不到任务对象就不做目标五的检查），所以有断言失败时，总条数会比全部通过时少（verify.py:242、verify.py:545）。

### 4.2 我认为文档有错或值得回写的地方

1. 步骤文档原稿第 99 行说「等待中」事件的内容带槽位，与事件表固定的键矛盾；编制会话已确认是笔误，现第 117 行已改正。
2. 步骤文档第 109 行说「凡改动必有事件，无事件即无改动」，但有几处改动本身不发事件：记录函数追加行动列表（第 10 节已删去记录函数，行动在登记时就放进行动表并发事件）、记录结束函数写结束记录、行动选择递增下一个行动编号（第 6 节已据此把写入点改为四处）、询问实现直接写返回值和变更组；这几处的内容都能从其他事件推出或在随后的事件里出现，不影响重放数据的正确性，但原句说得过满（kernel.py:477、kernel.py:542、tools.py:76）。
3. 步骤文档第 230 行第二项的检查写的是「工具名询问」，而按实施简报的名字对照表，工具名是 "ask"，控制台打印出来的也是 ask。
4. 步骤文档第 246 行第六项列出的事件序列从「已获准」开始，省略了之前的「行动提出」和「已提出」，所以检查脚本只统计执行控制之后的事件。
5. 验证目标二（步骤文档第 280 行）的断言无法保证内核线程确实进入过阻塞等待：主线程可能在询问实现调用「取」之前就已放好回答，此时「取」不会阻塞；脚本只能断言回答由主线程放入、由内核线程取出。阻塞等待本身由第五项的一秒延迟检查证明过。增补第九项后，「邮箱等待」结束事件带实际毫秒数，可以逐次看出等没等过，见第 5 节。

### 4.3 我没做的事

1. 我没有填写步骤文档 6.1 节的验收记录，没有修改步骤文档，也没有提交或推送代码。
2. 以下分支写了代码，但验证脚本一次都没有执行到，属于没跑过的代码：执行控制未获准的分支（kernel.py:597）、选择规则返回空的分支（kernel.py:451）、参数名不符的分支（kernel.py:459）、回答不是字符串的分支（tools.py:72）、工具实现没有置终态的分支（kernel.py:536）；循环开头取主动类消息的位置只有注释（kernel.py:585）。「工具未登记」分支只在第二项检查和第九项检查里各手动触发过一次。
3. 工具的可写槽位、前置条件、需要的授权三个字段只做了预留，不参与任何核对。
4. 行动状态的变化没有做合法性检查，例如行动已成功之后再改状态不会被拦住（kernel.py:429）。
5. 事件内容字典没有做成真正不可修改的对象（见 4.1 第 10 条）。
6. 我没有做人工层的判定。步骤文档要求负责人只看场景一的控制台记录，回答每个行动的五个问题，这一步要由负责人自己完成。

## 5 增补：第九到第十一项（2026-09-14）

（本节写于「能力」改名为「工具」之前，文字已统一改称「工具」，原称「能力」；贴出的代码与输出保持当时原样，其中的 capability、能力，即现在的 tool、工具。）

步骤文档在前八项完成后增补了三项可观测性工作：给事件分类并新增追踪事件、写文件订阅者、做查看器。目的是让人不看代码就能知道执行的是什么任务、循环怎么走、槽位的值怎样变化。开工前我向编制简报的会话提了六个问题。它同意了其中五个问题的做法；第二个问题它否决了我的方案，改为「行动选择结论」事件原样放依据、不放规则序号。裁定已写进 5.7 节。

### 5.1 结论

- 三项已按第九、十、十一的顺序做完。新增和改动的文件如下：`kernel.py`、`tools.py`、`task_travel.py`、`observe.py`、`verify.py` 有改动，`view.py` 是新增的，仓根 `.gitignore` 增加了一行 `/runs/`。
- 运行 `python -m tod_kernel.verify`，四个场景全部通过：场景一 72 条、场景二 23 条、场景三 61 条、场景四 48 条，退出码为 0；连续运行二十次，每次都全部通过。前八项的既有断言改为只对状态事件做之后，在加任何新断言之前先单独跑过一次，四个场景仍是 46、15、35、28 条全部通过。
- 场景一的查看页已生成：仓根下的 `runs/T-scenario-1.html`。同目录下还有两张浏览器截图，`T-scenario-1.screenshot-1280.png` 是桌面宽度，`T-scenario-3.screenshot-400.png` 是手机宽度。这个目录不入版本库。
- 验证目标六规定的「查看器四问」要由负责人看页面作答，我没有代答。

### 5.2 第九项：任务开始事件与追踪事件

每个事件增加了类别字段，现有七种事件属于状态事件，新增八种追踪事件，发在内核循环的固定位置。追踪事件内容里的槽位表、规则清单、工具清单、任务定义名，都在运行时从任务定义（新增的 `NAME`、`RULES` 两个属性）和工具表（新增的 `items()` 方法）读取，内核里不写任何名字。重放函数只取状态事件。

步骤文档要求能看到：场景一的控制台在原有事件之间多出追踪行；第一条是「任务开始」，列出三个槽位、三条规则、一个工具；每圈开头有「一圈开始」；每个询问有两条「邮箱等待」，结束那条带等待毫秒数与线程名。下面是验证脚本场景一的控制台输出，实际结果与要求相符。行首 `#` 表示状态事件，`·` 表示追踪事件。

```
════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  ·10 行动选择结论：行动 1，依据 (1, '目的地尚未填写', None)
  #11 行动状态变化：已获准，说明：本步恒允许
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·13 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #14 行动状态变化：等待中，说明：已向使用者提问
  ·15 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1
  #16 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·17 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.16 毫秒
  #18 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #19 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·20 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #21 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 22，追踪） ┈┈
  ·23 结果检查结论：已完成=False
── 行动 2 ──
  #24 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #25 行动状态变化：已提出，说明：行动选择
  ·26 行动选择结论：行动 2，依据 (2, '日期尚未填写', None)
  #27 行动状态变化：已获准，说明：本步恒允许
  ·28 执行控制结论：行动 2，结论 已获准，核验：恒允许
  ·29 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #30 行动状态变化：等待中，说明：已向使用者提问
  ·31 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-1
  #32 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  ·33 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.116 毫秒
  #34 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #35 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  ·36 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #37 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
┈┈ 第 3 圈开始（事件 38，追踪） ┈┈
  ·39 结果检查结论：已完成=False
── 行动 3 ──
  #40 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #41 行动状态变化：已提出，说明：行动选择
  ·42 行动选择结论：行动 3，依据 (3, '事由尚未填写', None)
  #43 行动状态变化：已获准，说明：本步恒允许
  ·44 执行控制结论：行动 3，结论 已获准，核验：恒允许
  ·45 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #46 行动状态变化：等待中，说明：已向使用者提问
  ·47 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-1
  #48 消息放入：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  ·49 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.107 毫秒
  #50 消息取出：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #51 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  ·52 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #53 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3
┈┈ 第 4 圈开始（事件 54，追踪） ┈┈
  ·55 结果检查结论：已完成=True
── 结束 ──
  #56 任务状态变化：执行中 → 已完成
  #57 任务结束：终态 已完成，原因：完成条件成立
```

手动触发一次「任务定义错误」（工具表为空，选择规则返回的 ask 没有登记）。检查代码 `item9_error.py`：

```python
import types
from tod_kernel import kernel as k
from tod_kernel import task_travel
from tod_kernel.capabilities import CapabilityTable
from tod_kernel.observe import ConsolePrinter, MemoryCollector

# 手动触发一次「任务定义错误」：能力表是空的，选择规则返回的 ask 没有登记。
task_id = "T-item9-definition-error"
stream = k.EventStream(task_id)
collector = MemoryCollector()
stream.subscribe(collector)
stream.subscribe(ConsolePrinter())
mb = k.Mailbox(stream, task_id)
try:
    k.start_task(task_id, task_travel, CapabilityTable(), mb, stream)
except k.KernelError as e:
    print("内核错误:", e.reason, "| 携带的原始输出:", e.raw)
last = collector.events[-1]
print("最后一个事件:", last.kind, last.name, last.payload)
print("抛异常前已发出，且没有行动提出:", last.name == k.TASK_DEFINITION_ERROR and not any(e.name == k.ACTION_PROPOSED for e in collector.events))
```

实际输出：

```
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
  ·8 任务定义错误：原因：能力未登记：'ask'，选择规则原始输出 ('ask', {'slot': '目的地'}, (1, '目的地尚未填写', None))
内核错误: 能力未登记：'ask' | 携带的原始输出: ('ask', {'slot': '目的地'}, (1, '目的地尚未填写', None))
最后一个事件: trace TASK_DEFINITION_ERROR {'reason': "能力未登记：'ask'", 'raw_output': ('ask', {'slot': '目的地'}, (1, '目的地尚未填写', None))}
抛异常前已发出，且没有行动提出: True
```

### 5.3 第十项：文件订阅者

文件订阅者 `FileWriter(dir)` 把每个事件写成一行 JSON，写到 `<dir>/<任务标识>.jsonl`；验证脚本给它的目录是仓根下的 `runs/`。验证脚本增加了验证目标六的机器检查：文件里的事件与内存里的逐条一致；用文件重放出的终态数据与运行时一致；用文件得到的每个行动状态经过与内存一致。

步骤文档要求能看到：跑完验证后 `runs/` 下有四个文件，用重放函数读场景一的文件，得到的终态数据与运行时一致。实际结果与要求相符。先运行验证脚本（四个场景全部通过），再在仓根下运行检查代码 `item10.py`：

```python
from pathlib import Path
from tod_kernel.observe import read_events, replay_data

runs = Path("runs")
print("runs/ 下的文件:")
for path in sorted(runs.iterdir()):
    print("  ", path.name, f"{sum(1 for _ in open(path, encoding='utf-8'))} 行")
first_lines = open(runs / "T-scenario-1.jsonl", encoding="utf-8").read().splitlines()
print("场景一文件的前两行与第 21 行原文:")
for line in (first_lines[0], first_lines[1], first_lines[20]):
    print("  ", line)
events = read_events(runs / "T-scenario-1.jsonl")
print("用重放函数读场景一文件得到的终态数据:", replay_data(events))
print("与运行时一致（运行时终态数据是三条回答）:",
      replay_data(events) == {"目的地": "上海", "日期": "9 月 20 日", "事由": "客户拜访"})
```

实际输出：

```
runs/ 下的文件:
   T-scenario-1.jsonl 57 行
   T-scenario-2.jsonl 9 行
   T-scenario-3.jsonl 50 行
   T-scenario-4.jsonl 41 行
场景一文件的前两行与第 21 行原文:
   {"seq": 1, "ts": 689977.071498506, "task_id": "T-scenario-1", "action_id": null, "kind": "trace", "name": "TASK_STARTED", "payload": {"slots": {"目的地": null, "日期": null, "事由": null}, "rules": {"1": "目的地尚未填写", "2": "日期尚未填写", "3": "事由尚未填写"}, "capabilities": {"ask": ["slot"]}, "task_def_name": "出差申请单"}}
   {"seq": 2, "ts": 689977.072196164, "task_id": "T-scenario-1", "action_id": null, "kind": "state", "name": "DATA_CHANGED", "payload": {"slot": "目的地", "old": null, "new": null, "source": "初始化"}}
   {"seq": 21, "ts": 689977.075333419, "task_id": "T-scenario-1", "action_id": 1, "kind": "state", "name": "DATA_CHANGED", "payload": {"slot": "目的地", "old": null, "new": "上海", "source": 1}}
用重放函数读场景一文件得到的终态数据: {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}
与运行时一致（运行时终态数据是三条回答）: True
```

另外确认了 `runs/` 已被忽略：`git check-ignore -v runs/T-scenario-1.jsonl` 的输出是 `.gitignore:54:/runs/	runs/T-scenario-1.jsonl`。

### 5.4 第十一项：查看器

`python -m tod_kernel.view runs/<文件>.jsonl` 读文件，生成同名 `.html`。页面分四块：任务概览、循环时间线、槽位轨迹、行动卡片；顶部有三组过滤：事件类别（全部、只看状态事件、只看追踪事件）、行动、槽位。样式和过滤脚本都写在页面内，不引外部资源；任务名、槽位名、工具名全部来自文件。

生成四个场景的页面，并检查外部资源与写死名字，实际输出：

```
已生成 runs/T-scenario-1.html
已生成 runs/T-scenario-2.html
已生成 runs/T-scenario-3.html
已生成 runs/T-scenario-4.html
页面里出现 http 的次数: 0
页面里出现 src= 或 href= 的次数: 0
view.py 里出现槽位名、任务名、能力名字面量的次数: 0
```

在浏览器里打开场景一页面，执行下面这段脚本检查过滤：先选「只看追踪事件」加「行动 2」，再换成全部类别加槽位「日期」。

```js
document.querySelector('input[name=kind][value=trace]').click();
const a = document.getElementById('f-action'); a.value = '2'; a.dispatchEvent(new Event('change'));
const r1 = {count: document.getElementById('f-count').textContent,
  visibleLoops: [...document.querySelectorAll('.loop')].filter(l => !l.hidden).map(l => l.querySelector('h3').textContent),
  visibleKinds: [...new Set([...document.querySelectorAll('li.ev')].filter(li => !li.hidden).map(li => li.dataset.kind + ':' + li.dataset.action))],
  cards: [...document.querySelectorAll('.card')].filter(c => !c.hidden).map(c => c.dataset.action)};
document.querySelector('input[name=kind][value=all]').click();
a.value = ''; a.dispatchEvent(new Event('change'));
const s = document.getElementById('f-slot'); s.value = '日期'; s.dispatchEvent(new Event('change'));
const r2 = {count: document.getElementById('f-count').textContent,
  seqs: [...document.querySelectorAll('li.ev')].filter(li => !li.hidden).map(li => li.querySelector('.seq').textContent).join(' '),
  slotRows: [...document.querySelectorAll('.slot-row')].filter(r => !r.hidden).map(r => r.querySelector('th').textContent),
  cards: [...document.querySelectorAll('.card')].filter(c => !c.hidden).map(c => c.dataset.action)};
JSON.stringify({trace_and_action2: r1, slot_date: r2}, null, 1)
```

实际输出（浏览器工具原样返回的字符串）：

```
"{\n \"trace_and_action2\": {\n  \"count\": \"时间线显示 6 / 57 个事件\",\n  \"visibleLoops\": [\n   \"第 2 圈 行动 2：ask {\\\"slot\\\": \\\"日期\\\"}\"\n  ],\n  \"visibleKinds\": [\n   \"trace:2\"\n  ],\n  \"cards\": [\n   \"2\"\n  ]\n },\n \"slot_date\": {\n  \"count\": \"时间线显示 15 / 57 个事件\",\n  \"seqs\": \"#3 #24 #25 #26 #27 #28 #29 #30 #31 #32 #33 #34 #35 #36 #37\",\n  \"slotRows\": [\n   \"日期\"\n  ],\n  \"cards\": [\n   \"2\"\n  ]\n }\n}"
```

这段输出说明：选「只看追踪事件」加「行动 2」时，时间线只剩行动 2 的 6 条追踪事件，只显示第 2 圈，行动卡片只剩行动 2；选槽位「日期」时，时间线只剩日期的初始化变更（#3）和行动 2 的全部事件（#24 到 #37），槽位轨迹只剩日期一行。我还在 400 像素宽度下打开了场景三的页面，页面宽度等于视口宽度，没有出现横向滚动；两张截图保存在 `runs/` 下。

步骤文档要求能看到：打开场景一的页面，不看代码能答出任务是什么、循环几圈、目的地在哪个事件由哪个行动改成上海、行动二等了多久。页面上对应的内容分别是：概览写着「出差申请单」，有三个槽位、三条规则、一个工具（ask）；时间线有第 1 到第 4 圈，前三圈各有一个询问，第 4 圈的结果检查结论为「是」；槽位轨迹里，目的地在 #21 由行动 1 写成“上海”（步骤文档当时写的是第 12 号事件，差异见 5.7 节第 20 条）；行动 2 的卡片和第 2 圈第 4 步都写着等待 0.347 毫秒，线程是 kernel-T-scenario-1。以上编号和时长都是第 6 节改动之前那次运行的。按验收规则，这四问的正式答案要由负责人自己看页面作答。

### 5.5 验证脚本完整输出（增补后）

下面是增补全部完成后运行 `python -m tod_kernel.verify` 的完整输出。当时 `runs/` 下的文件和场景一页面都来自这次运行；第 6 节改动后，它们已被重新生成。

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  ·10 行动选择结论：行动 1，依据 (1, '目的地尚未填写', None)
  #11 行动状态变化：已获准，说明：本步恒允许
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·13 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #14 行动状态变化：等待中，说明：已向使用者提问
  ·15 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1
  #16 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·17 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.45 毫秒
  #18 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #19 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·20 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #21 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 22，追踪） ┈┈
  ·23 结果检查结论：已完成=False
── 行动 2 ──
  #24 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #25 行动状态变化：已提出，说明：行动选择
  ·26 行动选择结论：行动 2，依据 (2, '日期尚未填写', None)
  #27 行动状态变化：已获准，说明：本步恒允许
  ·28 执行控制结论：行动 2，结论 已获准，核验：恒允许
  ·29 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #30 行动状态变化：等待中，说明：已向使用者提问
  ·31 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-1
  #32 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  ·33 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.347 毫秒
  #34 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #35 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  ·36 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #37 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
┈┈ 第 3 圈开始（事件 38，追踪） ┈┈
  ·39 结果检查结论：已完成=False
── 行动 3 ──
  #40 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #41 行动状态变化：已提出，说明：行动选择
  ·42 行动选择结论：行动 3，依据 (3, '事由尚未填写', None)
  #43 行动状态变化：已获准，说明：本步恒允许
  ·44 执行控制结论：行动 3，结论 已获准，核验：恒允许
  ·45 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #46 行动状态变化：等待中，说明：已向使用者提问
  ·47 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-1
  #48 消息放入：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  ·49 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.484 毫秒
  #50 消息取出：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #51 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  ·52 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #53 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3
┈┈ 第 4 圈开始（事件 54，追踪） ┈┈
  ·55 结果检查结论：已完成=True
── 结束 ──
  #56 任务状态变化：执行中 → 已完成
  #57 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的能力名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 3 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现能力名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有能力表模块、任务模块和观测模块
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 4 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=True
── 结束 ──
  #8 任务状态变化：执行中 → 已完成
  #9 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个状态事件是「任务结束」
  [通过] 行动列表为空，只有结束记录
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 1 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  ·10 行动选择结论：行动 1，依据 (1, '目的地尚未填写', None)
  #11 行动状态变化：已获准，说明：本步恒允许
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·13 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-3
  #14 行动状态变化：等待中，说明：已向使用者提问
  ·15 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-3
  #16 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·17 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-3，等待 0.566 毫秒
  #18 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #19 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·20 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-3
  #21 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 22，追踪） ┈┈
  ·23 结果检查结论：已完成=False
── 行动 2 ──
  #24 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #25 行动状态变化：已提出，说明：行动选择
  ·26 行动选择结论：行动 2，依据 (2, '日期尚未填写', None)
  #27 行动状态变化：已获准，说明：本步恒允许
  ·28 执行控制结论：行动 2，结论 已获准，核验：恒允许
  ·29 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-3
  #30 行动状态变化：等待中，说明：已向使用者提问
  ·31 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-3
  #32 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  ·33 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-3，等待 0.504 毫秒
  #34 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #35 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  ·36 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-3
  #37 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
┈┈ 第 3 圈开始（事件 38，追踪） ┈┈
  ·39 结果检查结论：已完成=False
── 行动 3 ──
  #40 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #41 行动状态变化：已提出，说明：行动选择
  ·42 行动选择结论：行动 3，依据 (3, '事由尚未填写', None)
  #43 行动状态变化：已获准，说明：本步恒允许
  ·44 执行控制结论：行动 3，结论 已获准，核验：恒允许
  ·45 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-3
  #46 行动状态变化：等待中，说明：已向使用者提问
  ·47 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-3
  ·48 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.241 毫秒
  #49 行动状态变化：已失败，说明：没有可用的回答，返回值 None
  ·50 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-3
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前没有「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动 3 已先记录进行动列表再抛错
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（日期预填），槽位表 {'目的地': None, '日期': '9 月 20 日', '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  ·10 行动选择结论：行动 1，依据 (1, '目的地尚未填写', None)
  #11 行动状态变化：已获准，说明：本步恒允许
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·13 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-4
  #14 行动状态变化：等待中，说明：已向使用者提问
  ·15 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-4
  #16 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·17 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-4，等待 0.522 毫秒
  #18 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #19 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·20 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-4
  #21 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 22，追踪） ┈┈
  ·23 结果检查结论：已完成=False
── 行动 2 ──
  #24 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #25 行动状态变化：已提出，说明：行动选择
  ·26 行动选择结论：行动 2，依据 (3, '事由尚未填写', None)
  #27 行动状态变化：已获准，说明：本步恒允许
  ·28 执行控制结论：行动 2，结论 已获准，核验：恒允许
  ·29 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-4
  #30 行动状态变化：等待中，说明：已向使用者提问
  ·31 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-4
  #32 消息放入：类型 answer，收件人 2，内容 '客户拜访'，到达序号 2
  ·33 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-4，等待 0.559 毫秒
  #34 消息取出：类型 answer，收件人 2，内容 '客户拜访'，到达序号 2
  #35 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  ·36 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-4
  #37 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2
┈┈ 第 3 圈开始（事件 38，追踪） ┈┈
  ·39 结果检查结论：已完成=True
── 结束 ──
  #40 任务状态变化：执行中 → 已完成
  #41 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 投影：任务行动列表的编号等于「行动提出」事件的编号
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「行动选择结论」，依据与「行动提出」里的原样相同，且在它之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致

════════════ 汇总 ════════════
场景一：正常流程：共 72 条断言，通过 72 条，失败 0 条
场景二：初始即完成：共 23 条断言，通过 23 条，失败 0 条
场景三：回答缺失：共 61 条断言，通过 61 条，失败 0 条
场景四：部分预填：共 48 条断言，通过 48 条，失败 0 条
结论：全部断言通过
```

### 5.6 故意改坏追踪事件，检验新断言

我在临时副本里删掉了「邮箱等待」在取到回答时发出的结束事件，再运行验证脚本。场景一的 3 个询问、场景三的前 2 个询问、场景四的 2 个询问，都报告了「邮箱等待恰好两条」这条断言失败。场景三的行动 3 没有报告失败，因为它走的是邮箱关闭这条路径，那条路径的结束事件没有被删；场景二没有询问。这些都与改动的范围一致。

### 5.7 增补部分的偏差与疑问

下文「步骤文档第 N 行」指增补后版本的行号，「文件:行号」已按第 7 节改动完成后的代码重新对过。

#### 已向编制会话提出并得到裁定的做法

1. 任务定义新增 `NAME` 和 `RULES` 两个属性，选择规则的条件文字只从 `RULES` 取；两份变体通过 `variant` 的 name 参数各有自己的名字（task_travel.py:15、task_travel.py:21、task_travel.py:44）。
2. 我原本打算在「行动选择结论」里放从依据第一位读出的规则序号，编制会话没有同意，理由是「内核不解释依据」要守住，于是改为事件内容原样放依据；后来在第 6 节，这个追踪事件因为与「行动提出」重复，已整个删掉。
3. 邮箱的「取」增加可选参数 waiter，询问传入本行动编号；只要是阻塞模式的「取」，就固定发开始、结束两条「邮箱等待」，不论是否真的等过（kernel.py:318、kernel.py:360）。
4. 「序号连续」是唯一一条没有先过滤追踪事件的既有断言，因为序号由两类事件共用，过滤后必然出现空号；其余既有断言都先过滤掉追踪事件再做（verify.py:237）。
5. `runs/` 放在仓根，忽略规则写成带锚点的 `/runs/`，免得把仓里别处同名的 `runs` 目录也忽略掉；同一个文件订阅者实例第一次遇到某个任务标识时清空重写文件；新增的 `read_events` 负责把文件读回事件（.gitignore:54、observe.py:87、observe.py:123）。
6. 工具表新增只读方法 `items()`，按登记顺序返回，内核用它生成工具清单（tools.py:40）。

#### 我自行决定、编制会话已同意的小处

7. 事件的类别由事件名决定，发布时查两张事件名表，调用方不传类别（kernel.py:218）。
8. 「任务开始」在新任务对象建好之后、初始化变更之前发出（kernel.py:565）。
9. 「执行控制结论」的结论字段取行动当时的状态（已获准），核验内容字段取常量「恒允许」；执行控制记状态时的说明「本步恒允许」也从同一个常量拼出（kernel.py:494）。
10. 控制台遇到「一圈开始」打一条分隔线；其他不带行动编号的追踪事件留在当前段里打印；在任何分段标题出现之前到达的追踪事件（即「任务开始」）归初始化段（observe.py:192、observe.py:201）。
11. 查看器的时间线里，最后一圈结果检查为真之后的「已完成」状态变化和「任务结束」归到该圈第 1 步；第一圈之前的「任务开始」和初始化单列「开始」一段（view.py:190）。

#### 文档没写清、我不得不自行决定的地方

12. 「邮箱等待」的结束事件发在「消息取出」事件之前，理由是先结束等待、再取走消息（kernel.py:330）。
13. 「行动执行调用」的 return 事件只在工具实现正常返回时发；工具实现抛异常时不发，以免记录一次并没有发生的正常返回（kernel.py:535）。
14. 「一圈开始」的圈序号只是启动任务函数里的一个局部计数器，不是对象，也不存在任务上（kernel.py:573）。
15. （第 6 节已改，这里记录的是改动之前的做法）查看器按四种追踪事件切分循环五步：结果检查结论之后进入第 2 步，行动选择结论之后进入第 3 步，执行控制结论之后进入第 4 步，行动执行调用的 return 之后进入第 5 步。因此行动未获准时不会有第 4 步的事件，本应属于第 5 步的数据变更会被显示在第 4 步；这个分支本步跑不到，我没有专门处理（view.py:190）。
16. 查看器在「只看某槽位」时，按以下规则判断事件是否与该槽位有关：数据变更事件看它自己的槽位；某个行动的事件，看该行动改过的槽位，以及它参数值里出现的槽位名。判断时不看参数的键名，所以不必知道工具的参数叫什么（view.py:168、view.py:185）。
17. 查看器引用内核模块里的事件名常量和行动状态「已失败」的中文值，只用它们认识事件词表，不依赖内核在跑（view.py:25）。
18. JSON 文件里，状态枚举写成中文值，元组写成列表，规则清单的整数键写成字符串（例如 "1"）；查看器显示时按原样显示（observe.py:100）。
19. 验证脚本另加了三类断言，都超出验证目标六的最低要求：一是追踪事件的形状断言，每个场景都做，包括圈数、每圈结果检查的结论、每个行动的选择结论、控制结论、执行调用和邮箱等待的顺序与内容；二是文件里的事件与内存里的事件逐条一致；三是用文件得到的每个行动状态经过与内存一致（verify.py:318、verify.py:335）。

#### 我认为文档有错或值得回写的地方

20. 步骤文档第 270 行说「目的地在第 12 号事件由行动一改成上海」，这是加追踪事件之前的编号；加入追踪事件后，这条数据变更是第 21 号事件。页面和控制台里都是 #21。
21. 步骤文档第 104 行规定「邮箱等待」不论是否真的等过都发，所以毫秒数可能很小。本次场景一三个询问的等待时长都在零点几毫秒，行动 2 是 0.347 毫秒；从事件顺序看，「开始等待」都在「消息放入」之前，说明内核线程确实阻塞过、在等主线程放入回答，只是主线程反应很快。这补上了第 4.2 节第 5 条的缺口，但「等过」要靠人比对事件顺序判断，没有做成断言。
22. 追踪事件和状态事件共用全局序号，所以状态事件的序号不再连续。步骤文档 4.1 节的「序号（全局递增）」没有说明这一后果，读者可能会误以为缺号表示丢了事件。

#### 我没做的事

23. 「任务定义错误」在四个场景里都不会出现，只在第九项检查脚本里手动触发过一次（工具未登记）；选择规则返回空、返回值不是三元组、参数名不符这三种情况都没有触发过。
24. 查看器没有自动化测试。它的正确性来自两处：我在浏览器里的实际检查，包括 1280 和 400 两种宽度、两种过滤组合；以及上面贴出的输出。验证脚本不会生成页面，也不检查页面。
25. 查看器页面的颜色写了浅色和深色两套，深色只按系统设置切换；我没有在深色模式下截图检查。
26. 我没有填写步骤文档 6.1 节的「查看器四问」一栏，没有修改步骤文档，没有提交或推送代码。

## 6 修订：拆分行动选择、写入点改为四处、删去「行动选择结论」（2026-09-14）

（本节写于「能力」改名为「工具」之前，文字已统一改称「工具」，原称「能力」；贴出的代码与输出保持当时原样，其中的 capability、能力，即现在的 tool、工具。）

第 5 节记录的是当时的状态。本节的改动删掉了「行动选择结论」追踪事件，事件序号因此整体前移，所以第 5 节贴出的输出和提到的事件编号（例如目的地改成上海的 #21），都是改动之前的样子。当时 `runs/` 下的文件、页面和截图都来自本节的那次运行；第 7 节改动后，它们已被重新生成。

### 6.1 起因与裁定

用户看查看页时问：「第 2 步 行动选择」里的「行动提出」为什么算状态事件？分析的结论是，它算状态事件是对的：删掉全部「行动提出」，每个行动的工具名、参数、提出者、依据就无从重建；而删掉「行动选择结论」，什么也不会丢。不过这个问题暴露了三处毛病。用户让我把它们发给编制简报的会话，对方裁定三项都改、现在就改，并已同步修改了步骤文档和简报：

1. 写入点原先说是三处，实际是四处，所以改为：状态更新、登记行动、记状态、邮箱。内核开头的注释照改；验证目标五补上每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等的核对。
2. 行动选择原先既选择又写入，与总纲第 3.4 节第 3 条冲突。现在拆成两个函数：`select_action(task)` 只返回候选 `Candidate(tool, params, basis, proposer)`，不创建对象、不发事件、不改任务；新增的 `register_action(task, candidate)` 是写入点，负责包成行动、取并递增编号、发「行动提出」、记「已提出」。任务定义错误仍在 `select_action` 里先发追踪事件再抛。
3. 删掉「行动选择结论」追踪事件和它的常量，查看器改用「行动提出」和其后的「已提出」来标出第 2 步的边界，相关的形状断言随之删去。「已提出」状态变化保留，因为它是行动状态经过的第一条。

### 6.2 结论

- 运行 `python -m tod_kernel.verify`，四个场景全部通过：场景一 70 条、场景二 24 条、场景三 59 条、场景四 47 条，退出码为 0；连续运行二十次，每次都全部通过。
- 条数变化的原因：每个场景都删掉了每个行动一条「行动选择结论」形状断言；原来只核对行动编号的那条断言，换成了逐字段核对；另外新增一条「行动选择不写任何东西」的断言。
- 故意改坏代码的检验：让行动选择往任务数据里写一个值，以及让登记行动在事件里写错提出者，新断言两次都报告了失败，见 6.4 节。
- 四个场景的页面已重新生成。场景一页面在 `runs/T-scenario-1.html`，两张截图也重新截过：1280 宽度的场景一、400 宽度的场景三；400 宽度下页面宽度仍等于视口宽度。

### 6.3 改动内容

- `kernel.py`：新增候选数据类 `Candidate`（kernel.py:136）；`select_action` 只返回候选（kernel.py:444、kernel.py:461）；新增写入点 `register_action`（kernel.py:464），它递增编号（kernel.py:477）、发「行动提出」（kernel.py:480）、记「已提出」（kernel.py:484）；循环第 2 步改为两行（kernel.py:586、kernel.py:588）；删去常量 `SELECTION_RESULT` 和发布它的那一行；模块开头注释改为四处写入点（kernel.py:6）。
- `observe.py`：控制台打印删去「行动选择结论」的标签和描述。
- `view.py`：第 2 步的结束边界，改为「结果检查结论」之后出现的第一条「已提出」状态变化（view.py:207）；删去「行动选择结论」的标签和描述。
- `verify.py`：跑场景期间多替换一个函数 `select_action`，记录每次调用前后的任务数据、下一个行动编号和事件条数（verify.py:156）；新增逐字段核对（verify.py:278）和「行动选择不写任何东西」的断言（verify.py:289）；删去「行动选择结论」的形状断言。

### 6.4 检查输出

场景一控制台的第一圈，现在「已提出」之后直接是「已获准」，中间不再有「行动选择结论」：

```
════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  #10 行动状态变化：已获准，说明：本步恒允许
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #13 行动状态变化：等待中，说明：已向使用者提问
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1
  #15 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.53 毫秒
  #17 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #18 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False
── 行动 2 ──
```

查看器按新边界切分出的五步。检查代码 `item12_view.py`：

```python
from pathlib import Path
from tod_kernel.view import LABELS, Record, STEP_NAMES, load

for name in ("T-scenario-1", "T-scenario-3"):
    record = Record(load(Path("runs") / f"{name}.jsonl"))
    before, loops = record.loops
    print(f"===== {name}：开始段 {[e['seq'] for e in before]}")
    for loop in loops:
        print(f"第 {loop['loop_no']} 圈")
        for step_name, events in zip(STEP_NAMES, loop["steps"]):
            items = "、".join(f"#{e['seq']} {LABELS[e['name']]}" for e in events) or "（本步没有发出事件）"
            print(f"  {step_name}：{items}")
```

实际输出（场景一与场景三）：

```
===== T-scenario-1：开始段 [1, 2, 3, 4, 5]
第 1 圈
  第 1 步 结果检查：#6 一圈开始、#7 结果检查结论
  第 2 步 行动选择：#8 行动提出、#9 行动状态变化
  第 3 步 执行控制：#10 行动状态变化、#11 执行控制结论
  第 4 步 行动执行：#12 行动执行调用、#13 行动状态变化、#14 邮箱等待、#15 消息放入、#16 邮箱等待、#17 消息取出、#18 行动状态变化、#19 行动执行调用
  第 5 步 状态更新与记录：#20 数据变更
第 2 圈
  第 1 步 结果检查：#21 一圈开始、#22 结果检查结论
  第 2 步 行动选择：#23 行动提出、#24 行动状态变化
  第 3 步 执行控制：#25 行动状态变化、#26 执行控制结论
  第 4 步 行动执行：#27 行动执行调用、#28 行动状态变化、#29 邮箱等待、#30 消息放入、#31 邮箱等待、#32 消息取出、#33 行动状态变化、#34 行动执行调用
  第 5 步 状态更新与记录：#35 数据变更
第 3 圈
  第 1 步 结果检查：#36 一圈开始、#37 结果检查结论
  第 2 步 行动选择：#38 行动提出、#39 行动状态变化
  第 3 步 执行控制：#40 行动状态变化、#41 执行控制结论
  第 4 步 行动执行：#42 行动执行调用、#43 行动状态变化、#44 邮箱等待、#45 消息放入、#46 邮箱等待、#47 消息取出、#48 行动状态变化、#49 行动执行调用
  第 5 步 状态更新与记录：#50 数据变更
第 4 圈
  第 1 步 结果检查：#51 一圈开始、#52 结果检查结论、#53 任务状态变化、#54 任务结束
  第 2 步 行动选择：（本步没有发出事件）
  第 3 步 执行控制：（本步没有发出事件）
  第 4 步 行动执行：（本步没有发出事件）
  第 5 步 状态更新与记录：（本步没有发出事件）
===== T-scenario-3：开始段 [1, 2, 3, 4, 5]
第 1 圈
  第 1 步 结果检查：#6 一圈开始、#7 结果检查结论
  第 2 步 行动选择：#8 行动提出、#9 行动状态变化
  第 3 步 执行控制：#10 行动状态变化、#11 执行控制结论
  第 4 步 行动执行：#12 行动执行调用、#13 行动状态变化、#14 邮箱等待、#15 消息放入、#16 邮箱等待、#17 消息取出、#18 行动状态变化、#19 行动执行调用
  第 5 步 状态更新与记录：#20 数据变更
第 2 圈
  第 1 步 结果检查：#21 一圈开始、#22 结果检查结论
  第 2 步 行动选择：#23 行动提出、#24 行动状态变化
  第 3 步 执行控制：#25 行动状态变化、#26 执行控制结论
  第 4 步 行动执行：#27 行动执行调用、#28 行动状态变化、#29 邮箱等待、#30 消息放入、#31 邮箱等待、#32 消息取出、#33 行动状态变化、#34 行动执行调用
  第 5 步 状态更新与记录：#35 数据变更
第 3 圈
  第 1 步 结果检查：#36 一圈开始、#37 结果检查结论
  第 2 步 行动选择：#38 行动提出、#39 行动状态变化
  第 3 步 执行控制：#40 行动状态变化、#41 执行控制结论
  第 4 步 行动执行：#42 行动执行调用、#43 行动状态变化、#44 邮箱等待、#45 邮箱等待、#46 行动状态变化、#47 行动执行调用
  第 5 步 状态更新与记录：（本步没有发出事件）
```

重跑第九项手动触发「任务定义错误」的检查脚本（代码见 5.2 节）。现在 `select_action` 只返回候选，错误仍在创建行动之前发出追踪事件并抛出。实际输出：

```
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
  ·8 任务定义错误：原因：能力未登记：'ask'，选择规则原始输出 ('ask', {'slot': '目的地'}, (1, '目的地尚未填写', None))
内核错误: 能力未登记：'ask' | 携带的原始输出: ('ask', {'slot': '目的地'}, (1, '目的地尚未填写', None))
最后一个事件: trace TASK_DEFINITION_ERROR {'reason': "能力未登记：'ask'", 'raw_output': ('ask', {'slot': '目的地'}, (1, '目的地尚未填写', None))}
抛异常前已发出，且没有行动提出: True
```

故意改坏一：在 `select_action` 返回候选之前往任务数据里写一个值。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 70 条断言，通过 61 条，失败 9 条
  失败：目标一：终态数据是三条回答
  失败：目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  失败：目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  失败：目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  失败：目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  失败：目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  失败：终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  失败：行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  失败：目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
场景二：初始即完成：共 24 条断言，通过 24 条，失败 0 条
场景三：回答缺失：共 59 条断言，通过 55 条，失败 4 条
  失败：目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  失败：终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  失败：行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  失败：目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
场景四：部分预填：共 47 条断言，通过 42 条，失败 5 条
  失败：任务状态是已完成，数据完整
  失败：目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  失败：终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  失败：行动选择只返回候选、不写任何东西：2 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  失败：目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
结论：有断言失败
```

故意改坏二：`register_action` 发「行动提出」事件时把提出者写成 user，行动对象上仍是 selector。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 70 条断言，通过 69 条，失败 1 条
  失败：目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
场景二：初始即完成：共 24 条断言，通过 24 条，失败 0 条
场景三：回答缺失：共 59 条断言，通过 58 条，失败 1 条
  失败：目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
场景四：部分预填：共 47 条断言，通过 46 条，失败 1 条
  失败：目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
结论：有断言失败
```

改动完成后运行 `python -m tod_kernel.verify` 的完整输出：

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  #10 行动状态变化：已获准，说明：本步恒允许
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #13 行动状态变化：等待中，说明：已向使用者提问
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1
  #15 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.53 毫秒
  #17 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #18 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False
── 行动 2 ──
  #23 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #24 行动状态变化：已提出，说明：行动选择
  #25 行动状态变化：已获准，说明：本步恒允许
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许
  ·27 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #28 行动状态变化：等待中，说明：已向使用者提问
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-1
  #30 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.452 毫秒
  #32 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #33 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  ·34 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #35 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=False
── 行动 3 ──
  #38 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #39 行动状态变化：已提出，说明：行动选择
  #40 行动状态变化：已获准，说明：本步恒允许
  ·41 执行控制结论：行动 3，结论 已获准，核验：恒允许
  ·42 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-1
  #43 行动状态变化：等待中，说明：已向使用者提问
  ·44 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-1
  #45 消息放入：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  ·46 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.525 毫秒
  #47 消息取出：类型 answer，收件人 3，内容 '客户拜访'，到达序号 3
  #48 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  ·49 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-1
  #50 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3
┈┈ 第 4 圈开始（事件 51，追踪） ┈┈
  ·52 结果检查结论：已完成=True
── 结束 ──
  #53 任务状态变化：执行中 → 已完成
  #54 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的能力名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 3 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现能力名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有能力表模块、任务模块和观测模块
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 4 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=True
── 结束 ──
  #8 任务状态变化：执行中 → 已完成
  #9 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个状态事件是「任务结束」
  [通过] 行动列表为空，只有结束记录
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：0 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 1 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  #10 行动状态变化：已获准，说明：本步恒允许
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-3
  #13 行动状态变化：等待中，说明：已向使用者提问
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-3
  #15 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-3，等待 0.494 毫秒
  #17 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #18 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-3
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False
── 行动 2 ──
  #23 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None)
  #24 行动状态变化：已提出，说明：行动选择
  #25 行动状态变化：已获准，说明：本步恒允许
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许
  ·27 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-3
  #28 行动状态变化：等待中，说明：已向使用者提问
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-3
  #30 消息放入：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-3，等待 0.449 毫秒
  #32 消息取出：类型 answer，收件人 2，内容 '9 月 20 日'，到达序号 2
  #33 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日'
  ·34 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-3
  #35 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=False
── 行动 3 ──
  #38 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #39 行动状态变化：已提出，说明：行动选择
  #40 行动状态变化：已获准，说明：本步恒允许
  ·41 执行控制结论：行动 3，结论 已获准，核验：恒允许
  ·42 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-3
  #43 行动状态变化：等待中，说明：已向使用者提问
  ·44 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-3
  ·45 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.245 毫秒
  #46 行动状态变化：已失败，说明：没有可用的回答，返回值 None
  ·47 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-3
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前没有「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动 3 已先记录进行动列表再抛错
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（日期预填），槽位表 {'目的地': None, '日期': '9 月 20 日', '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']}
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化
  #5 任务状态变化：未开始 → 执行中
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None)
  #9 行动状态变化：已提出，说明：行动选择
  #10 行动状态变化：已获准，说明：本步恒允许
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-4
  #13 行动状态变化：等待中，说明：已向使用者提问
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-4
  #15 消息放入：类型 answer，收件人 1，内容 '上海'，到达序号 1
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-4，等待 0.534 毫秒
  #17 消息取出：类型 answer，收件人 1，内容 '上海'，到达序号 1
  #18 行动状态变化：已成功，说明：回答到达，返回值 '上海'
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-4
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False
── 行动 2 ──
  #23 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None)
  #24 行动状态变化：已提出，说明：行动选择
  #25 行动状态变化：已获准，说明：本步恒允许
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许
  ·27 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-4
  #28 行动状态变化：等待中，说明：已向使用者提问
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-4
  #30 消息放入：类型 answer，收件人 2，内容 '客户拜访'，到达序号 2
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-4，等待 0.549 毫秒
  #32 消息取出：类型 answer，收件人 2，内容 '客户拜访'，到达序号 2
  #33 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访'
  ·34 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-4
  #35 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=True
── 结束 ──
  #38 任务状态变化：执行中 → 已完成
  #39 任务结束：终态 已完成，原因：完成条件成立
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：2 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，七种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致

════════════ 汇总 ════════════
场景一：正常流程：共 70 条断言，通过 70 条，失败 0 条
场景二：初始即完成：共 24 条断言，通过 24 条，失败 0 条
场景三：回答缺失：共 59 条断言，通过 59 条，失败 0 条
场景四：部分预填：共 47 条断言，通过 47 条，失败 0 条
结论：全部断言通过
```

### 6.5 本轮的偏差与疑问

#### 我自行决定的地方

1. 候选里的提出者由 `select_action` 直接填为 "selector"，因为本步只有行动选择一个提出者；使用者主动提出的候选（提出者 "user"）本步没有来源，这条路径没有跑过（kernel.py:461）。
2. 登记行动记「已提出」时，说明文字仍是「行动选择」。它说明的是这个行动由谁提出，不是由哪个函数写入；步骤文档的写法也是如此（kernel.py:484）。
3. 候选的参数在 `select_action` 里复制一次，登记行动时再复制一次，这样选择规则后续改动自己的字典也不会影响行动对象（kernel.py:461、kernel.py:473）。
4. 「行动选择不写任何东西」的断言只核对三样东西：任务数据、下一个行动编号、事件条数。任务状态和行动列表没有核对，因为现在的 `select_action` 根本碰不到它们，但这不算完整证明（verify.py:289）。
5. 验证脚本现在临时替换三个内核函数（update_state、execute、select_action），它依赖启动任务通过模块全局名调用这三个函数；这仍是仅供验证的手段（verify.py:156、verify.py:206）。
6. 原来那条「行动列表编号等于行动提出事件编号」的断言，被新的逐字段核对完全覆盖，所以删掉了，没有两条并存（verify.py:278）。
7. 查看器把「结果检查结论为否之后出现的第一条已提出」当作第 2 步的结束。如果将来登记行动之前还有别的状态事件，或者一圈里登记了不止一个行动，这个判断就要重写（view.py:207）。

#### 文档与报告的同步情况

8. 步骤文档第十一项的「做完能看到」（步骤文档第 271 行）已改为「目的地在哪一号事件由行动一改成上海」，不再写死编号，5.7 节第 20 条指出的问题已经解决。本次运行中，这条数据变更是 #20；行动 2 等待了 0.452 毫秒，线程是 kernel-T-scenario-1。
9. 第 4、5 节引用的代码行号，已按第 7 节改动完成后的代码重新对过（本节的行号同样如此）。5.7 节第 15 条描述的五步切分方式是改动之前的，现在的切分方式见本节第 7 条。

#### 我没做的事

10. 我没有填写步骤文档 6.1 节的验收记录，没有修改步骤文档，没有提交或推送代码。

## 7 修订：记录方、发起方与邮箱关闭（2026-09-14）

（本节写于「能力」改名为「工具」之前，文字已统一改称「工具」，原称「能力」；贴出的代码与输出保持当时原样，其中的 capability、能力，即现在的 tool、工具。）

第 5、6 节贴出的输出里没有记录方，也没有「邮箱关闭」事件，那是当时的样子。当时 `runs/` 下的文件、页面和截图都来自本节的那次运行；第 8 节改名后，它们已被重新生成。

### 7.1 起因与裁定

用户指出：「第 4 步 行动执行」里的「消息放入」不是内核做的，是外部系统做的，应该在可观测性系统和网页里明显区分开。用户还追问：以后组件会越来越多，事件和消息里是否应标注出是内核还是哪个组件。

我的分析是：
- 「消息放入」由内核之外的一方发起，但记录点在内核边界上（邮箱），所以仍应作为状态事件留在事件流里，另外标出是谁引起的。
- 光按事件名区分行不通，因为同一个事件名可能由不同组件发出。例如「行动状态变化」里，「已获准」是内核记的，「等待中」「已成功」是询问工具记的。

这些分析发给编制简报的会话后，它的裁定如下，步骤文档 4.1 节与简报 4a 节已同步：

1. 采用通用方案。事件有三个互不相干的分类方向：类别（kind）答「是不是改动」；记录方（source）答「哪个组件发布了它」；消息的发起方（sender）答「谁引起的」。
2. 记录方用点分层级，取值固定为四组：
   - `kernel.loop`：循环、结果检查、登记行动、执行控制、行动执行的调用；
   - `kernel.update`：状态更新；
   - `kernel.mailbox`：邮箱；
   - `tool.<工具名>`：工具实现经绑好的记状态发出的事件。

   事件流提供 `stream.bind(source)`，返回绑定了记录方的发布句柄，各组件各持一个。发起方取值 host 或 user，由放消息的一方自报；「消息放入」「消息取出」的内容带发起方；验证脚本用线程核对外部发起的事件都出自内核线程之外。
3. 新增第八种状态事件「邮箱关闭」`MAILBOX_CLOSED`，内容是发起方；`mailbox.close(sender)`。场景三里宿主关闭邮箱时，发起方是 host。
4. 网页时间线增加独立的「外部」栏，按发起方标出，用不同底色和标签；第 4 步在对应位置标出「内核在此阻塞，等待外部输入」；工具实现的事件按记录方缩进显示；过滤增加「只看内核」「只看外部」；控制台的外部行换行首符号并写明「外部」；文件订阅者和 `read_events` 带上新字段。

### 7.2 结论

- 运行 `python -m tod_kernel.verify`，四个场景全部通过：场景一 76 条、场景二 29 条、场景三 65 条、场景四 53 条，退出码为 0；连续运行二十次，每次都全部通过。
- 新增断言分三类：
  - 记录方：逐个事件核对它的记录方是否符合裁定；场景里同一个事件名「行动状态变化」确实来自 kernel.loop 和 tool.ask 两个记录方。
  - 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外；每条「消息取出」的发起方与对应的「消息放入」相同。
  - 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致；场景三恰有一条，发起方是 host，落在行动 3 的「等待中」与「已失败」之间；其余三个场景都没有。
- 故意改坏代码的检验：一是让工具实现的记状态误用 kernel.loop 发布，二是关闭邮箱时不发事件。新断言两次都报告了失败，见 7.4 节。
- 四个场景的页面已重新生成，并重新截图：场景一 1280 宽度、场景三 1280 宽度、场景三 400 宽度。400 宽度下页面宽度仍等于视口宽度。

### 7.3 改动内容

- `kernel.py`：
  - 事件增加 `source` 字段；`EventStream.bind(source)` 返回发布句柄 `Publisher`；`publish` 必须带记录方，并校验记录方是否合法（kernel.py:208、kernel.py:240、kernel.py:255）。
  - 定义记录方常量与发起方取值（kernel.py:104、kernel.py:110）；任务对象上持有 kernel.loop 与 kernel.update 两个发布句柄（kernel.py:173）；邮箱持有 kernel.mailbox 的句柄（kernel.py:297）。
  - 工具实现拿到的记状态预先绑好 `tool.<工具名>`（kernel.py:528）。
  - 消息增加发起方，放入时校验（kernel.py:307）；新增「邮箱关闭」事件（kernel.py:67），由 `close(sender)` 发布（kernel.py:347）。
- `observe.py`：
  - 新增「谁引起」的判断：「消息放入」「邮箱关闭」由消息的发起方亲自做，发起方取内容里的 sender；其余事件的发起方就是记录方（observe.py:37、observe.py:44、observe.py:53）。
  - 控制台的外部行以「⇢」开头并写明「外部（发起方）」，工具实现发布的行多缩进一格，每行末尾标出记录方；不带行动编号的外部事件不另起分段（observe.py:199、observe.py:211）。
  - 文件订阅者写出记录方（observe.py:131）。
- `view.py`：
  - 时间线每行分成「内核一侧」和「外部」两栏（view.py:220、view.py:337）；外部事件发生在「邮箱等待」的开始与结束之间时，左栏标出「内核在此阻塞，等待外部输入」（view.py:183）。
  - 工具实现的事件缩进显示，并标出记录方；过滤增加「谁引起」一组（view.py:442）。
  - 概览增加「记录方」统计和「外部引起的事件」清单（view.py:294）。
- `verify.py`：答案以发起方 user 放入，宿主以 host 关闭邮箱（第 9 节已改为收件箱：verify.py:196、verify.py:201）；新增记录方与发起方的检查（verify.py:381、verify.py:392）。

### 7.4 检查输出

场景一控制台的第一圈。「⇢」开头的是外部行，缩进一格的是工具实现发布的行，行末〔〕里是记录方：

```
════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.52 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔capability.ask〕
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
```

场景三控制台的第 3 圈。宿主关闭邮箱现在有了自己的事件（#45），发起方是 host：

```
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #38 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #39 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #40 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·41 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·42 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
    #43 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·44 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #45 外部（host）· 邮箱关闭：发起方 host，不会再有消息 〔kernel.mailbox〕
  ·46 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.667 毫秒 〔kernel.mailbox〕
    #47 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔capability.ask〕
  ·48 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
── 断言 ──
```

生成四个场景的页面，并检查外部资源与写死名字，实际输出：

```
已生成 runs/T-scenario-1.html
已生成 runs/T-scenario-2.html
已生成 runs/T-scenario-3.html
已生成 runs/T-scenario-4.html
页面里出现 http 的次数: 0
view.py 里出现槽位名、任务名、能力名字面量的次数: 0
```

在浏览器里打开场景一页面，用下面这段脚本依次选「只看外部」「只看工具实现」「只看内核」：

```js
function pick(name, value){ const el = document.querySelector('input[name=' + name + '][value=' + value + ']'); el.checked = true; el.dispatchEvent(new Event('change')); }
function visible(){ return [...document.querySelectorAll('li.ev')].filter(li => !li.hidden); }
pick('origin', 'external');
const ext = {count: document.getElementById('f-count').textContent,
  rows: visible().map(li => li.querySelector('.ext-cell').textContent.replace(/\s+/g, ' ').trim()),
  leftCells: visible().map(li => li.querySelector('.kernel-cell').textContent.trim())};
pick('origin', 'capability');
const cap = {count: document.getElementById('f-count').textContent,
  seqs: visible().map(li => li.querySelector('.seq').textContent).join(' ')};
pick('origin', 'kernel');
const ker = {count: document.getElementById('f-count').textContent,
  anyExternalOrCapability: visible().some(li => li.dataset.origin !== 'kernel')};
pick('origin', 'all');
JSON.stringify({only_external: ext, only_capability: cap, only_kernel: ker}, null, 1)
```

实际输出（浏览器工具原样返回的字符串）：

```
"{\n \"only_external\": {\n  \"count\": \"时间线显示 3 / 54 个事件\",\n  \"rows\": [\n   \"外部 · user#15状态消息放入类型 answer，发起方 user，收件人 1，内容 “上海”，到达序号 1kernel.mailbox\",\n   \"外部 · user#30状态消息放入类型 answer，发起方 user，收件人 2，内容 “9 月 20 日”，到达序号 2kernel.mailbox\",\n   \"外部 · user#45状态消息放入类型 answer，发起方 user，收件人 3，内容 “客户拜访”，到达序号 3kernel.mailbox\"\n  ],\n  \"leftCells\": [\n   \"内核在此阻塞，等待外部输入\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核在此阻塞，等待外部输入\"\n  ]\n },\n \"only_capability\": {\n  \"count\": \"时间线显示 6 / 54 个事件\",\n  \"seqs\": \"#13 #18 #28 #33 #43 #48\"\n },\n \"only_kernel\": {\n  \"count\": \"时间线显示 45 / 54 个事件\",\n  \"anyExternalOrCapability\": false\n }\n}"
```

这段输出说明：选「只看外部」时，时间线只剩三条「消息放入」，它们都在右侧外部栏，发起方是 user，左栏都标着「内核在此阻塞，等待外部输入」；选「只看工具实现」时，只剩询问工具记的六条状态变化（每个行动的「等待中」和「已成功」）；选「只看内核」时，剩下 45 条，其中没有外部事件和工具实现事件。三组相加是 3 + 6 + 45 = 54 条，正好等于全部事件数。

故意改坏一：工具实现的记状态误用 kernel.loop 发布。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 76 条断言，通过 74 条，失败 2 条
  失败：记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，能力实现记的状态 capability.<能力名>，其余 kernel.loop）
  失败：记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 capability.ask）
场景二：初始即完成：共 29 条断言，通过 29 条，失败 0 条
场景三：回答缺失：共 65 条断言，通过 63 条，失败 2 条
  失败：记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，能力实现记的状态 capability.<能力名>，其余 kernel.loop）
  失败：记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 capability.ask）
场景四：部分预填：共 53 条断言，通过 51 条，失败 2 条
  失败：记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，能力实现记的状态 capability.<能力名>，其余 kernel.loop）
  失败：记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 capability.ask）
结论：有断言失败
```

故意改坏二：关闭邮箱时不发「邮箱关闭」事件。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 76 条断言，通过 76 条，失败 0 条
场景二：初始即完成：共 29 条断言，通过 29 条，失败 0 条
场景三：回答缺失：共 65 条断言，通过 63 条，失败 2 条
  失败：邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  失败：邮箱关闭：恰有一条「邮箱关闭」，发起方 host，落在行动 3 的「等待中」与「已失败」之间
场景四：部分预填：共 53 条断言，通过 53 条，失败 0 条
结论：有断言失败
```

改动完成后运行 `python -m tod_kernel.verify` 的完整输出：

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.52 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔capability.ask〕
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #23 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #24 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #25 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·27 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #28 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #30 外部（user）· 消息放入：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.454 毫秒 〔kernel.mailbox〕
  #32 消息取出：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
    #33 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔capability.ask〕
  ·34 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #35 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #38 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #39 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #40 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·41 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·42 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #43 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·44 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #45 外部（user）· 消息放入：类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3 〔kernel.mailbox〕
  ·46 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.474 毫秒 〔kernel.mailbox〕
  #47 消息取出：类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3 〔kernel.mailbox〕
    #48 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔capability.ask〕
  ·49 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #50 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 51，追踪） ┈┈
  ·52 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #53 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #54 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的能力名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 3 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现能力名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有能力表模块、任务模块和观测模块
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 4 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，能力实现记的状态 capability.<能力名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 capability.ask）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：本场景宿主没有关闭邮箱，事件流里没有「邮箱关闭」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #8 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #9 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个状态事件是「任务结束」
  [通过] 行动列表为空，只有结束记录
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：0 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 1 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，能力实现记的状态 capability.<能力名>，其余 kernel.loop）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：本场景宿主没有关闭邮箱，事件流里没有「邮箱关闭」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-3，等待 0.615 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔capability.ask〕
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #23 行动提出：能力 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #24 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #25 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·27 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
    #28 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #30 外部（user）· 消息放入：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-3，等待 0.549 毫秒 〔kernel.mailbox〕
  #32 消息取出：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
    #33 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔capability.ask〕
  ·34 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #35 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #38 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #39 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #40 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·41 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·42 行动执行调用：行动 3，能力 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
    #43 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·44 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #45 外部（host）· 邮箱关闭：发起方 host，不会再有消息 〔kernel.mailbox〕
  ·46 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.667 毫秒 〔kernel.mailbox〕
    #47 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔capability.ask〕
  ·48 行动执行调用：行动 3，能力 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前没有「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动 3 已先记录进行动列表再抛错
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，能力实现记的状态 capability.<能力名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 capability.ask）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：恰有一条「邮箱关闭」，发起方 host，落在行动 3 的「等待中」与「已失败」之间
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（日期预填），槽位表 {'目的地': None, '日期': '9 月 20 日', '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，能力 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #8 行动提出：能力 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，能力 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-4，等待 0.664 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔capability.ask〕
  ·19 行动执行调用：行动 1，能力 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #23 行动提出：能力 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #24 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #25 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·27 行动执行调用：行动 2，能力 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
    #28 行动状态变化：等待中，说明：已向使用者提问 〔capability.ask〕
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #30 外部（user）· 消息放入：类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2 〔kernel.mailbox〕
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-4，等待 0.545 毫秒 〔kernel.mailbox〕
  #32 消息取出：类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2 〔kernel.mailbox〕
    #33 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔capability.ask〕
  ·34 行动执行调用：行动 2，能力 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #35 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #38 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #39 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、能力名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：2 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、能力清单、任务定义名与任务定义和能力表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，能力实现记的状态 capability.<能力名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 capability.ask）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：本场景宿主没有关闭邮箱，事件流里没有「邮箱关闭」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致

════════════ 汇总 ════════════
场景一：正常流程：共 76 条断言，通过 76 条，失败 0 条
场景二：初始即完成：共 29 条断言，通过 29 条，失败 0 条
场景三：回答缺失：共 65 条断言，通过 65 条，失败 0 条
场景四：部分预填：共 53 条断言，通过 53 条，失败 0 条
结论：全部断言通过
```

### 7.5 本轮的偏差与疑问

#### 我自行决定的地方

1. 判断「谁引起」时，「消息放入」和「邮箱关闭」取内容里的发起方，其余事件取记录方。这是一个按事件名分的规则，理由是这两种事件的定义就是发起方本人的动作。「消息取出」虽然也带发起方，但它记录的是工具实现在取消息，这里的发起方只是在说明这条消息的来历，所以不算外部事件（observe.py:37）。
2. 重复关闭邮箱时不再发「邮箱关闭」事件，因为第二次关闭没有改变任何状态（kernel.py:344）。
3. 关闭邮箱时，发起方先于加锁做校验；放入消息时，发起方在锁内校验。两处的校验内容相同，只是位置不同（kernel.py:307、kernel.py:339）。
4. 「邮箱等待」和「消息取出」的记录方是 kernel.mailbox，不是 tool.ask，这是按裁定「邮箱的记录方是 kernel.mailbox」来的。所以在网页上，这两种事件没有缩进到工具实现里，尽管调用「取」的是询问工具。
5. 查看器的「谁引起」过滤比裁定多了一个「只看工具实现」选项。原因是如果只有「只看内核」和「只看外部」，工具实现发布的事件就只能在「全部」里看到（view.py:442）。
6. 窄屏（720 像素以下）时，外部栏不再单独成列，改为和内核一侧上下排列；此时「内核在此阻塞」的提示仍然显示。
7. 验证脚本读取了邮箱的私有属性 `_closed`，用来核对「邮箱是否关闭」与「有没有邮箱关闭事件」一致；这只用于检查，不改变邮箱（verify.py:210）。
8. 事件对象上的 `source` 字段（记录方）和数据变更内容里的 `source` 键（变更的来源，即行动编号或「初始化」）同名，但不在同一层，意思也不同。这两个名字都来自已定的对照表，我没有改动，只在这里说明。

#### 验证脚本在本轮出过的一次错误

9. 第一次运行时，场景三有一条断言失败：「本场景宿主没有关闭邮箱，事件流里没有邮箱关闭」。原因是我用脚本插入新检查时，把本该给场景四的检查错插到了场景三，于是场景三同时要求「没有邮箱关闭」和「恰有一条邮箱关闭」，而场景四一条也没加。我把这条检查移回场景四之后，四个场景全部通过。这是验证脚本的放置错误，不是为了迁就实现而改断言。

#### 我没做的事

10. 使用者主动放入的消息（收件人是 "loop"）和发起方 user 关闭邮箱，这两条路径都没有跑过。
11. 查看器仍然没有自动化测试，这次只在浏览器里按 1280 和 400 两种宽度检查过，并用脚本检查了「只看外部」「只看工具实现」「只看内核」三种过滤；深色模式没有检查。
12. 我没有填写步骤文档的验收记录，没有修改步骤文档，没有提交或推送代码。

## 8 修订：「能力」整体改名为「工具」（2026-09-14）

从本节起，代码与文字统一使用「工具」。当时 `runs/` 下的文件、页面和截图都来自本节的那次运行；第 9 节改动后，它们已被重新生成。

### 8.1 起因与裁定

用户裁定把「能力」整体改名为「工具」，与智能体语境里的通用说法一致。编制简报的会话已同步修改了步骤文档与简报。步骤文档 4.1 节「工具」一行补充了它与「行动」的关系：工具是「能做什么」的静态定义；行动是工具的一次调用，外加它的提出、核验与状态经过，比行业里的「工具调用」多出执行控制这一段。

改名清单：
- 类 `Capability` 改为 `Tool`，`CapabilityTable` 改为 `ToolTable`；
- 模块 `capabilities.py` 改为 `tools.py`；
- `start_task` 的参数 `capabilities` 改为 `tools`；
- `Candidate` 与 `Action` 的字段 `capability` 改为 `tool`；
- 事件内容里的键 `capability` 改为 `tool`，「任务开始」事件的 `capabilities` 键改为 `tools`；
- 记录方 `capability.<名>` 改为 `tool.<名>`；
- 控制台与查看器里的中文「能力」全部改为「工具」，例如「工具清单」「只看工具实现」；
- 验证脚本的导入检查改为「内核不得导入 tools 模块」；
- 实施报告里的中文术语一并改，写于改名之前的章节加一句「原称能力」。

不改名的有：行动、候选、依据、状态经过，以及 ask 这个工具名。

### 8.2 结论

- 运行 `python -m tod_kernel.verify`，四个场景全部通过：场景一 76 条、场景二 29 条、场景三 65 条、场景四 53 条，与改名前的条数相同，退出码为 0；连续运行二十次，每次都全部通过。
- `tod_kernel/` 下所有 Python 文件里，已经搜不到 capability、Capability 和「能力」；四个场景重新生成的页面里也搜不到。
- 检验「内核不得导入 tools 模块」这条检查是否仍然有效：我在临时副本的内核里加入一条对 tools 模块的导入，验证脚本报告了这条断言失败，见 8.4 节。
- 四个场景的页面已重新生成，并重新截图：场景一 1280 宽度、场景三 1280 宽度、场景三 400 宽度。400 宽度下页面宽度仍等于视口宽度。

### 8.3 改动内容

- 文件改名：`capabilities.py` 改为 `tools.py`，其中定义了 `Tool`（tools.py:16）、`ToolTable`（tools.py:26）、询问工具 `ask`（tools.py:45）和 `build_table`（tools.py:81）。
- `kernel.py`：参数、字段、变量名、事件内容键全部按清单改名；记录方前缀常量改为 `TOOL_SOURCE_PREFIX = "tool."`（kernel.py:107），工具实现的记状态绑定 `tool.<工具名>`（kernel.py:528），「任务开始」事件带 `tools` 键（kernel.py:568）。
- `observe.py` 与 `view.py`：读取的内容键改为 `tool`、`tools`，中文文字改为「工具」；查看器「谁引起」过滤的取值改为 tool（view.py:444），缩进显示工具实现事件用的样式类名也从 cap 改为 tool-impl（view.py:248）。
- `verify.py`：改为从 tools 模块导入（verify.py:31）；导入检查的禁止名单改为 tools、task_travel、observe（verify.py:526）；各处字段与记录方断言按新名字核对。
- `task_travel.py`、`__init__.py`：注释与模块说明里的「能力」改为「工具」；`__init__.py` 顺带补了一行查看器的运行方式。

### 8.4 检查输出

场景一控制台第一圈的一段，其中工具名和记录方都已改为新名字：

```
── 行动 1 ──
  #8 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.519 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·19 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
```

生成四个场景的页面，并检查外部资源、旧名残留与写死名字，实际输出：

```
已生成 runs/T-scenario-1.html
已生成 runs/T-scenario-2.html
已生成 runs/T-scenario-3.html
已生成 runs/T-scenario-4.html
页面里出现 http 的次数: 0
页面里出现「能力」或 capability 的次数: 0
view.py 里出现槽位名、任务名、工具名字面量的次数: 0
```

在浏览器里打开场景一页面，重跑「谁引起」三种过滤的检查（脚本与 7.4 节相同，只把过滤取值 capability 改为 tool），实际输出：

```
"{\n \"only_external\": {\n  \"count\": \"时间线显示 3 / 54 个事件\",\n  \"rows\": [\n   \"外部 · user#15状态消息放入类型 answer，发起方 user，收件人 1，内容 “上海”，到达序号 1kernel.mailbox\",\n   \"外部 · user#30状态消息放入类型 answer，发起方 user，收件人 2，内容 “9 月 20 日”，到达序号 2kernel.mailbox\",\n   \"外部 · user#45状态消息放入类型 answer，发起方 user，收件人 3，内容 “客户拜访”，到达序号 3kernel.mailbox\"\n  ],\n  \"leftCells\": [\n   \"内核在此阻塞，等待外部输入\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核在此阻塞，等待外部输入\"\n  ]\n },\n \"only_tool\": {\n  \"count\": \"时间线显示 6 / 54 个事件\",\n  \"seqs\": \"#13 #18 #28 #33 #43 #48\"\n },\n \"only_kernel\": {\n  \"count\": \"时间线显示 45 / 54 个事件\",\n  \"anyExternalOrTool\": false\n }\n}"
```

检验导入检查：在临时副本的内核末尾加一个从不调用的函数，函数里导入 tools 模块。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 76 条断言，通过 75 条，失败 1 条
  失败：目标四：内核模块的导入语句里没有工具表模块、任务模块和观测模块
场景二：初始即完成：共 29 条断言，通过 29 条，失败 0 条
场景三：回答缺失：共 65 条断言，通过 65 条，失败 0 条
场景四：部分预填：共 53 条断言，通过 53 条，失败 0 条
结论：有断言失败
```

改名完成后运行 `python -m tod_kernel.verify` 的完整输出：

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #8 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.519 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·19 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #23 行动提出：工具 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #24 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #25 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·27 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #28 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #30 外部（user）· 消息放入：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.483 毫秒 〔kernel.mailbox〕
  #32 消息取出：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
    #33 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·34 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #35 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #38 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #39 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #40 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·41 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·42 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
    #43 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·44 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #45 外部（user）· 消息放入：类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3 〔kernel.mailbox〕
  ·46 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.422 毫秒 〔kernel.mailbox〕
  #47 消息取出：类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3 〔kernel.mailbox〕
    #48 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·49 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #50 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 51，追踪） ┈┈
  ·52 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #53 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #54 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的工具名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 3 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现工具名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有工具表模块、任务模块和观测模块
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 4 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：本场景宿主没有关闭邮箱，事件流里没有「邮箱关闭」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #8 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #9 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个状态事件是「任务结束」
  [通过] 行动列表为空，只有结束记录
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：0 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 1 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：本场景宿主没有关闭邮箱，事件流里没有「邮箱关闭」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #8 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-3，等待 0.44 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·19 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #23 行动提出：工具 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #24 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #25 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·27 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
    #28 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #30 外部（user）· 消息放入：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-3，等待 0.379 毫秒 〔kernel.mailbox〕
  #32 消息取出：类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2 〔kernel.mailbox〕
    #33 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·34 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #35 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #38 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #39 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #40 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·41 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·42 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
    #43 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·44 邮箱等待：行动 3，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #45 外部（host）· 邮箱关闭：发起方 host，不会再有消息 〔kernel.mailbox〕
  ·46 邮箱等待：行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.454 毫秒 〔kernel.mailbox〕
    #47 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔tool.ask〕
  ·48 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前没有「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动 3 已先记录进行动列表再抛错
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：恰有一条「邮箱关闭」，发起方 host，落在行动 3 的「等待中」与「已失败」之间
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（日期预填），槽位表 {'目的地': None, '日期': '9 月 20 日', '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #8 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #9 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #10 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·11 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·12 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
    #13 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·14 邮箱等待：行动 1，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #15 外部（user）· 消息放入：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
  ·16 邮箱等待：行动 1，阶段 end，线程 kernel-T-scenario-4，等待 0.56 毫秒 〔kernel.mailbox〕
  #17 消息取出：类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1 〔kernel.mailbox〕
    #18 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·19 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #20 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 21，追踪） ┈┈
  ·22 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #23 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #24 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #25 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·26 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·27 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
    #28 行动状态变化：等待中，说明：已向使用者提问 〔tool.ask〕
  ·29 邮箱等待：行动 2，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #30 外部（user）· 消息放入：类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2 〔kernel.mailbox〕
  ·31 邮箱等待：行动 2，阶段 end，线程 kernel-T-scenario-4，等待 0.481 毫秒 〔kernel.mailbox〕
  #32 消息取出：类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2 〔kernel.mailbox〕
    #33 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·34 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #35 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 36，追踪） ┈┈
  ·37 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #38 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #39 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 1 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条「消息放入」和一条「消息取出」
  [通过] 目标二：行动 2 的放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：2 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 回答跨线程进入：全部「消息放入」由主线程发布，全部「消息取出」由内核线程发布
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：第一个事件是「任务开始」，槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：全部「消息放入」「邮箱关闭」的发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：每条「消息取出」的发起方与它对应的「消息放入」相同，答案都由 user 放入
  [通过] 邮箱关闭：邮箱最终是否关闭，与事件流里有没有「邮箱关闭」事件一致
  [通过] 邮箱关闭：本场景宿主没有关闭邮箱，事件流里没有「邮箱关闭」
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致

════════════ 汇总 ════════════
场景一：正常流程：共 76 条断言，通过 76 条，失败 0 条
场景二：初始即完成：共 29 条断言，通过 29 条，失败 0 条
场景三：回答缺失：共 65 条断言，通过 65 条，失败 0 条
场景四：部分预填：共 53 条断言，通过 53 条，失败 0 条
结论：全部断言通过
```

### 8.5 本轮的偏差与疑问

1. 第 3 到第 7 节贴出的代码与输出，我没有改名，保留了当时的原样，所以里面仍能看到 capability、capabilities.py 和「能力」。改掉它们就等于改写了当时的实际输出，不符合「照实贴输出」的要求。这几节开头已各加一句说明。
2. 改名前写出的 JSONL 文件内容里的键是 capability，改名后的查看器读不了；`runs/` 下的文件已经全部用新代码重新生成，但如果有人手里还留着旧文件，需要重跑验证脚本才能生成新文件。
3. 查看器内部的一个取值和几个样式类名也跟着改了：「谁引起」过滤的取值从 capability 改为 tool，样式类名从 cap、cap-tag 改为 tool-impl、tool-tag。它们都不出现在事件或文件里，只影响页面本身（view.py:248、view.py:444）。
4. 检验导入检查时，我第一次把导入语句直接写在内核文件顶部，结果内核与 tools 模块互相导入，程序在启动阶段就报错，根本没运行到这条检查，所以那次试验无效。第二次改为把导入写在一个从不被调用的函数里，程序能正常运行，这条断言也报告了失败。上面贴的是第二次的输出。
5. 我没有填写步骤文档的验收记录，没有修改步骤文档，没有提交或推送代码。

## 9 修订：邮箱分收发两向，询问真正发出问题（2026-09-14）

第 3 到第 8 节里的邮箱只有一个方向，贴出的输出里也没有「发件箱」「问题」，那是当时的样子。当时 `runs/` 下的文件、页面和截图都来自本节的那次运行；第 10 节改动后，它们已被重新生成。

### 9.1 起因与裁定

用户看了询问工具的实现后指出：`ask` 并没有真正发出询问消息，邮箱需要深化设计，加上「发送」这一向。

我分析后同意，理由有四条：
- 问题本身不是一条消息。界面要知道问了什么，得靠三样线索拼出来：订阅事件流，逐字匹配说明文字「已向使用者提问」，再回查「行动提出」事件里的工具参数。
- 这等于把本来只做观测的事件流当成了指令通道。
- 界面因此依赖一段说明文字和工具的参数结构。
- 下一步改成非阻塞模型后，运行停止期间界面要知道「还有哪些问题待回答」，靠这些线索拼不出来。

这些分析发给编制简报的会话后，它认定这是设计错误，并裁定现在就改，步骤文档与简报已同步。实施中我又提出了五个缺口，也都得到了裁定。汇总如下：

1. 邮箱类不变，构造时给箱名。任务持有收件箱（inbox，外部发给内核）和发件箱（outbox，内核发给外部）两个实例；`start_task(task_id, task_def, tools, inbox, outbox, stream)`，`ExecContext(action, data_view, inbox, outbox, set_status)`。
2. `ask` 分三步：先往发件箱放一条问题，类型 question，发起方 tool.ask，收件人 user，内容是参数字典原样，所属行动是本行动；再记「等待中，说明：已向使用者提问，问题 <到达序号>」；最后在收件箱阻塞取「类型是 answer、回复对象等于该问题到达序号」的回答。
3. 消息的字段是：类型、发起方、收件人、回复对象（in_reply_to）、内容、到达序号，外加可选的所属行动（action_id）。所属行动的作用是：问题里带着行动编号，宿主回答时照抄，不必去事件里查。邮箱事件的行动编号优先取所属行动。
4. 三个事件名不变，「消息放入」「消息取出」的内容增加箱名、所属行动、回复对象；「邮箱关闭」「邮箱等待」的内容增加箱名。
5. 发起方的取值扩大：外部是 host、user，内核一侧用记录方的名字，例如 tool.ask。「谁引起」分两条规则：放入、关闭按发起方判；取出、邮箱等待按箱名判，收件箱只由内核一侧取，发件箱只由外部取。
6. 内核在任务正常结束和抛内核错误时都关闭发件箱，发起方 kernel.loop。正常结束时，关闭发件箱放在「记录结束」之前，所以「任务结束」仍是最后一个状态事件。出错时由 `start_task` 统一捕获内核错误，先关发件箱再重新抛出，行动失败和任务定义错误两条路径都覆盖。
7. 验证脚本：主线程循环阻塞读发件箱，按问题内容里的槽位查答案表，往收件箱放回答；答案表里没有的就关闭收件箱。订阅事件应答的那一套全部删掉。宿主读发件箱时照样发「邮箱等待」事件；因此「第一个事件是任务开始」这条断言改为「内核一侧发出的第一个事件是任务开始」。
8. 查看器：收件箱的放入与关闭、发件箱的取出与等待进「外部」栏；问题的发出留在内核一侧，标「向外部提问」；只有带行动编号的收件箱等待才算「内核在此阻塞」。

### 9.2 结论

- 运行 `python -m tod_kernel.verify`，四个场景全部通过：场景一 94 条、场景二 35 条、场景三 80 条、场景四 67 条，退出码为 0；连续运行二十次，每次都全部通过。
- 验证脚本里已经没有订阅事件做应答的代码，也不再靠说明文字识别提问。「已向使用者提问」只在一条断言里出现，用来核对「等待中」的说明是否写对了问题的到达序号。
- 新增断言中，最有力的一条是按线程做事实核对：每个事件被判为「外部」，当且仅当它出自内核线程之外。它把「谁引起」的两条规则和实际发生的线程一一对上了。
- 故意改坏代码的检验：一是让询问先记「等待中」再发问题，二是出错时不关发件箱。新断言两次都报告了失败，见 9.4 节。
- 四个场景的页面已重新生成并重新截图。400 宽度下四个页面的宽度都等于视口宽度；本轮中途曾出现过一次溢出（418 像素），已修正，见 9.5 节。

### 9.3 改动内容

- `kernel.py`：
  - 箱名常量（kernel.py:113）；发起方校验，外部取值或记录方名都合法（kernel.py:251）。
  - 消息增加回复对象与所属行动（kernel.py:262、kernel.py:276、kernel.py:279）；邮箱构造时带箱名（kernel.py:292）；事件内容带箱名、所属行动、回复对象（kernel.py:352）。
  - 任务持有收件箱与发件箱，并校验箱名（kernel.py:164、kernel.py:371）；执行上下文带两个箱（kernel.py:508）。
  - `start_task` 捕获内核错误后关发件箱再抛出（kernel.py:546、kernel.py:555）；循环本体移到 `_run`，正常结束时先关发件箱再记录结束（kernel.py:560、kernel.py:582）。
- `tools.py`：`ask` 先发问题（tools.py:55），再记「等待中」并写明问题序号（tools.py:63），再按回复对象取回答（tools.py:65）。
- `observe.py`：
  - 「谁引起」改为两条规则（observe.py:37、observe.py:44、observe.py:53）；新增「向外部提问」的判断（observe.py:62）。
  - 控制台里，问题的发出以「↗ 向外部提问」开头（observe.py:214）；描述里带箱名与回复对象。
- `view.py`：
  - 「内核在此阻塞」只看带行动编号的收件箱等待（view.py:179）；问题的发出在内核一侧标「向外部提问 · 发件箱」（view.py:249）。
  - 步骤名下方的等待时长只写内核阻塞（view.py:324）；行动卡片增加「发出的问题」，「取到的回答」带回复对象（view.py:422）。
  - 修正窄屏溢出（view.py:485）。
- `verify.py`：
  - 宿主从发件箱读问题（verify.py:191）；内核因意外异常没关发件箱时由宿主代关（verify.py:182）。
  - 新增断言：按线程核对「谁引起」（verify.py:312）；内核一侧第一个事件（verify.py:344）；发件箱恰由内核关闭一次（verify.py:428）；宿主在发件箱上的等待（verify.py:451）；问题在「等待中」之前（verify.py:472）；回复对象等于问题序号（verify.py:485）。

### 9.4 检查输出

场景一控制台的第一圈。「↗」是内核向外部提问；「⇢」是外部做的事，包括宿主读取发件箱、使用者往收件箱放回答：

```
════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.358 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.718 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
```

场景三控制台的第 3 圈。问题 3 发出后，宿主取走它，发现答案表里没有「事由」，于是关闭收件箱；行动 3 失败后，内核关闭发件箱：

```
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #47 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #48 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #49 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·50 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·51 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #52 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
    #53 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ·54 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ ·55 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.264 毫秒 〔kernel.mailbox〕
  ⇢ #56 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
  ⇢ #57 外部（host）· 邮箱关闭：收件箱，发起方 host，不会再有消息 〔kernel.mailbox〕
  ⇢ ·58 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·59 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.49 毫秒 〔kernel.mailbox〕
    #60 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔tool.ask〕
  ·61 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
── 结束 ──
  #62 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  ⇢ ·63 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 0.898 毫秒 〔kernel.mailbox〕
```

场景二控制台。任务一开始就完成，没有问题；内核在「任务结束」之前关闭发件箱，宿主随后结束读取：

```
════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #9 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #10 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #11 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·12 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.056 毫秒 〔kernel.mailbox〕
── 断言 ──
```

生成四个场景的页面，并检查外部资源、写死名字和应答方式，实际输出：

```
已生成 runs/T-scenario-1.html
已生成 runs/T-scenario-2.html
已生成 runs/T-scenario-3.html
已生成 runs/T-scenario-4.html
页面里出现 http 的次数: 0
view.py 里出现槽位名、任务名、工具名字面量的次数: 0
验证脚本里订阅事件做应答的代码（answerer、ASKED_NOTE）出现次数: 0
验证脚本里「已向使用者提问」出现的位置（只剩核对说明文字的断言）:
452:            waiting.payload["note"] == f"已向使用者
```

在浏览器里打开场景一页面，重跑「谁引起」三种过滤的检查（脚本与 8.4 节相同），实际输出：

```
"{\n \"only_external\": {\n  \"count\": \"时间线显示 14 / 69 个事件\",\n  \"rows\": [\n   \"外部 · 读取发件箱的一方#2追踪邮箱等待发件箱，行动 None，阶段 begin，线程 MainThreadkernel.mailbox\",\n   \"外部 · 读取发件箱的一方#16追踪邮箱等待发件箱，行动 None，阶段 end，线程 MainThread，等待 3.358 毫秒kernel.mailbox\",\n   \"外部 · 读取发件箱的一方#17状态消息取出发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {\\\"slot\\\": \\\"目的地\\\"}，到达序号 1kernel.mailbox\",\n   \"外部 · user#19状态消息放入收件箱，类型 answer，发起方 user，收件人 1，内容 “上海”，到达序号 1，回复问题 1kernel.mailbox\",\n   \"外部 · 读取发件箱的一方#20追踪邮箱等待发件箱，行动 None，阶段 begin，线程 MainThreadkernel.mailbox\",\n   \"外部 · 读取发件箱的一方#35追踪邮箱等待发件箱，行动 None，阶段 end，线程 MainThread，等待 2.529 毫秒kernel.mailbox\",\n   \"外部 · 读取发件箱的一方#36状态消息取出发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {\\\"slot\\\": \\\"日期\\\"}，到达序号 2kernel.mailbox\",\n   \"外部 · user#38状态消息放入收件箱，类型 answer，发起方 user，收件人 2，内容 “9 月 20 日”，到达序号 2，回复问题 2kernel.mailbox\",\n   \"外部 · 读取发件箱的一方#39追踪邮箱等待发件箱，行动 None，阶段 begin，线程 MainThreadkernel.mailbox\",\n   \"外部 · 读取发件箱的一方#54追踪邮箱等待发件箱，行动 None，阶段 end，线程 MainThread，等待 2.4 毫秒kernel.mailbox\",\n   \"外部 · 读取发件箱的一方#55状态消息取出发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {\\\"slot\\\": \\\"事由\\\"}，到达序号 3kernel.mailbox\",\n   \"外部 · user#57状态消息放入收件箱，类型 answer，发起方 user，收件人 3，内容 “客户拜访”，到达序号 3，回复问题 3kernel.mailbox\",\n   \"外部 · 读取发件箱的一方#58追踪邮箱等待发件箱，行动 None，阶段 begin，线程 MainThreadkernel.mailbox\",\n   \"外部 · 读取发件箱的一方#69追踪邮箱等待发件箱，行动 None，阶段 end，线程 MainThread，等待 1.596 毫秒kernel.mailbox\"\n  ],\n  \"leftCells\": [\n   \"内核一侧此刻没有事件\",\n   \"内核一侧此刻没有事件\",\n   \"内核一侧此刻没有事件\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核一侧此刻没有事件\",\n   \"内核一侧此刻没有事件\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核一侧此刻没有事件\",\n   \"内核一侧此刻没有事件\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核在此阻塞，等待外部输入\",\n   \"内核一侧此刻没有事件\"\n  ]\n },\n \"only_tool\": {\n  \"count\": \"时间线显示 6 / 69 个事件\",\n  \"seqs\": \"#15 #23 #34 #42 #53 #61\"\n },\n \"only_kernel\": {\n  \"count\": \"时间线显示 49 / 69 个事件\",\n  \"anyExternalOrTool\": false\n }\n}"
```

三种过滤的条数相加是 14 + 6 + 49 = 69，等于全部事件数。外部的 14 条包括：宿主读取发件箱的等待与取出，使用者往收件箱放的三条回答。

四个页面在 400 宽度下的页面宽度与视口宽度：

```
场景1 400 宽: "400 / 400"
场景2 400 宽: "400 / 400"
场景3 400 宽: "400 / 400"
场景4 400 宽: "400 / 400"
```

故意改坏一：询问先记「等待中」，再发问题。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 91 条断言，通过 88 条，失败 3 条
  失败：目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  失败：目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  失败：目标二：行动 3 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
场景二：初始即完成：共 35 条断言，通过 35 条，失败 0 条
场景三：回答缺失：共 78 条断言，通过 76 条，失败 2 条
  失败：目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  失败：目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
场景四：部分预填：共 65 条断言，通过 63 条，失败 2 条
  失败：目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  失败：目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
结论：有断言失败
```

故意改坏二：出错时不关发件箱。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 91 条断言，通过 91 条，失败 0 条
场景二：初始即完成：共 35 条断言，通过 35 条，失败 0 条
场景三：回答缺失：共 78 条断言，通过 76 条，失败 2 条
  失败：谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  失败：邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
场景四：部分预填：共 65 条断言，通过 65 条，失败 0 条
结论：有断言失败
```

改动完成后运行 `python -m tod_kernel.verify` 的完整输出：

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.358 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.718 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ⇢ ·35 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.529 毫秒 〔kernel.mailbox〕
  ⇢ #36 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
  ·37 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.668 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #44 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #47 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #48 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #49 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·50 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·51 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #52 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
    #53 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ⇢ ·54 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.4 毫秒 〔kernel.mailbox〕
  ⇢ #55 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
  ·56 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #57 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3，回复问题 3 〔kernel.mailbox〕
  ⇢ ·58 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·59 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.652 毫秒 〔kernel.mailbox〕
  #60 消息取出：收件箱，类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3，回复问题 3 〔kernel.mailbox〕
    #61 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·62 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #63 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 64，追踪） ┈┈
  ·65 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #66 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #67 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #68 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·69 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 1.596 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的工具名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 3 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 3 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 3 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 3 的问题与回答，内容里的所属行动都是 3
  [通过] 目标二：行动 3 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现工具名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有工具表模块、任务模块和观测模块
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 4 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #9 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #10 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #11 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·12 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.056 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个状态事件是「任务结束」
  [通过] 行动列表为空，只有结束记录
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：0 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 1 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.743 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-3，等待 0.59 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ·35 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ ·36 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.281 毫秒 〔kernel.mailbox〕
  ⇢ #37 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-3，等待 0.534 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #44 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #47 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #48 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #49 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·50 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·51 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #52 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
    #53 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ·54 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ ·55 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.264 毫秒 〔kernel.mailbox〕
  ⇢ #56 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
  ⇢ #57 外部（host）· 邮箱关闭：收件箱，发起方 host，不会再有消息 〔kernel.mailbox〕
  ⇢ ·58 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·59 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.49 毫秒 〔kernel.mailbox〕
    #60 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔tool.ask〕
  ·61 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
── 结束 ──
  #62 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  ⇢ ·63 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 0.898 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前发出过问题（发件箱放入）
  [通过] 行动 3 在「已失败」之前没有收件箱的「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动 3 已先记录进行动列表再抛错
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：收件箱恰有一条「邮箱关闭」，发起方 host，落在行动 3 的「等待中」与「已失败」之间
  [通过] 邮箱关闭：出错时，发件箱在行动 3「已失败」之后由内核关闭
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（日期预填），槽位表 {'目的地': None, '日期': '9 月 20 日', '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.753 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-4，等待 0.611 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ⇢ ·35 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.26 毫秒 〔kernel.mailbox〕
  ⇢ #36 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 2 〔kernel.mailbox〕
  ·37 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-4，等待 0.535 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #44 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #47 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #48 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #49 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·50 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 1.589 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动选择只返回候选、不写任何东西：2 次调用前后，任务数据、下一个行动编号、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致

════════════ 汇总 ════════════
场景一：正常流程：共 94 条断言，通过 94 条，失败 0 条
场景二：初始即完成：共 35 条断言，通过 35 条，失败 0 条
场景三：回答缺失：共 80 条断言，通过 80 条，失败 0 条
场景四：部分预填：共 67 条断言，通过 67 条，失败 0 条
结论：全部断言通过
```

### 9.5 本轮的偏差与疑问

#### 与文档的出入

1. 步骤文档第五项第六小项「做完能看到」（步骤文档第 255 行）写的询问行动事件顺序是「已获准、消息放入（发件箱，问题）、等待中、消息放入（收件箱，回答）、消息取出、已成功」。实际运行中还多出两类事件：一是宿主从发件箱取走问题的「消息取出（发件箱）」，它带所属行动，所以也归在这个行动下；二是双方的「邮箱等待」。文档列的顺序本身没有错，只是没把宿主一侧的动作列进去。
2. 步骤文档第十一项的页面说明（步骤文档第 279 行）写的「外部」栏只包括「收件箱的消息放入、收件箱关闭」。按问题二的裁定，发件箱上的「消息取出」和「邮箱等待」也是外部做的，实现里它们同样在「外部」栏。

#### 我自行决定的地方

3. 宿主的「邮箱等待」事件行动编号为空；宿主取走问题的「消息取出」事件，因为消息带所属行动，行动编号是该行动。所以在控制台和页面上，前者不归属任何行动，后者归在行动下面。
4. 验证脚本的宿主在内核线程结束时，如果发件箱还没关，会以发起方 host 代为关闭，免得主线程永远阻塞。正常情况下内核已经关过，这里不会发事件。但代关这一步是在内核线程里执行的，所以一旦真的发生，「谁引起」的线程核对和「发件箱恰由内核关闭一次」都会失败。这正好能暴露内核没关发件箱的缺陷：故意改坏二的输出就是这种情况（verify.py:182）。
5. 查看器概览里的「外部引起的事件」只列状态事件，外部引起的追踪事件（宿主读发件箱时的邮箱等待）只给出条数，因为这类事件每次读取都有一对，全部列出来太长。
6. 原来那条「回答跨线程进入」断言，改成了覆盖两个箱的「问题与回答跨线程往返」：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程（verify.py:307）。
7. 验证脚本里原来那个 30 秒的超时，用在「从队列取应答任务」上；应答队列删掉之后，它改为只用在「收内核线程」上（verify.py:77）。

#### 本轮出现过的问题

8. 页面在 400 宽度下一度出现横向溢出，页面宽度是 418 像素。原因是新增的问题与回答内容里有较长的、不能断行的字符串，而时间线网格的列没有设最小宽度为零，窄屏样式里的列宽写法也覆盖了宽屏的修正。修正后四个页面在 400 宽度下都等于视口宽度。唯一超出视口的是槽位轨迹表格，它在自带横向滚动的容器里，不会让整页横向滚动。
9. 故意改坏一的写法：先记「等待中」，说明里的问题序号取发件箱的下一个序号，再发问题。这样说明文字仍然正确，只有顺序错了；验证脚本只在「问题在等待中之前」这一条上报告了失败。

#### 我没做的事

10. 使用者主动放给循环的消息（收件人 "loop"），以及内核一侧关闭收件箱、外部关闭发件箱，这些路径都没有跑过；内核因意外异常退出、由宿主代关发件箱的路径，只在故意改坏的副本里出现过。
11. 查看器仍然没有自动化测试，这次在浏览器里检查了 1280 与 400 两种宽度和「谁引起」三种过滤；深色模式没有检查。
12. 我没有填写步骤文档的验收记录，没有修改步骤文档，没有提交或推送代码。

## 10 修订：登记即放入行动表，删去记录函数（2026-09-14）

本节改动没有改变事件流，四个场景发出的事件种类与顺序和第 9 节相同。`runs/` 下的文件、页面和截图，都来自本节的这次运行。

### 10.1 起因与裁定

用户指出 `register_action` 名实不符：「注册」一般是把对象放进容器，之后按需访问；而原来的实现是生成一个行动对象后直接返回。用户还提到，以后任务会有多个行动并发执行。

我核对后同意。原来的行动在登记后只是循环里的一个局部变量，要到第 5 步才由 `record()` 追加进任务的行动列表，而且那次追加不发事件。所以从登记到记录之间，除了循环自己，谁都找不到这个行动。

我建议保留名字、把实现改成名副其实，编制简报的会话按我的倾向裁定了五项，步骤文档与简报已同步：

1. 保留 `register_action` 这个名字。登记时就把行动放进任务的行动表，之后按编号取用。放进容器就是写入，与「行动提出」在同一处发出，写入点与发布点真正重合。
2. `register_action` 返回行动编号，循环再从行动表按编号取行动。
3. 行动表先用任务上按登记顺序排列的字典 `actions`，键是行动编号，值是行动；暂不建类，等并发带来更多操作时再升级为 `ActionTable`。
4. 删除 `record()`，第 5 步只剩状态更新；全文「行动列表」改为「行动表」；`record_end` 保留。
5. 本步只登记、不并发。并发执行需要的几件事已写进步骤文档第 7 节的遗留：循环不能卡在行动执行里；收件箱的回答按行动分派；同时进行几个行动由执行控制管；进行中的行动由状态推出，不另存一份；查看器按圈、按步的切分方式要重写。

用户在本轮还问过：`select_action` 和 `register_action` 总是连着执行，为什么要拆成两个函数。我当时的回答是：
- 拆开后，「行动选择不写任何东西」可以由验证脚本自动检查；
- 下一步起，使用者的主动输入也会作为候选行动进入循环，不经过行动选择，登记是所有候选共用的入口。

### 10.2 结论

- 运行 `python -m tod_kernel.verify`，四个场景全部通过：场景一 97 条、场景二 37 条、场景三 82 条、场景四 69 条，退出码为 0；连续运行二十次，每次都全部通过。
- 新增三条断言：
  - 行动表的键就是各行动自己的编号，顺序等于登记顺序；
  - 每次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出；
  - 内核已经没有 `record` 函数。
- 原有两条断言随之调整：「行动选择不写任何东西」的核对范围加上了行动表大小；场景三原来的「失败的行动先记录进行动列表再抛错」，改为「失败的行动在行动表里」（先按裁定写作「从登记起就在行动表里」，因名实不符又改名，见 10.5 节第 3 条）。
- 故意改坏代码的检验：模拟原来的做法，登记时不放进行动表、等第 5 步才放。只有「登记即放入行动表」这一条报告了失败，其余断言都通过。这说明其他断言只看最终结果，看不出登记时机的差别，而新断言看得出，见 10.4 节。
- 四个场景的页面已重新生成并重新截图；400 宽度下四个页面的宽度都等于视口宽度。

### 10.3 改动内容

- `kernel.py`：
  - 任务的 `actions` 改为按登记顺序排列的字典（kernel.py:170）；`register_action` 返回行动编号（kernel.py:464），在发「行动提出」之前把行动放进行动表（kernel.py:478）。
  - 循环第 2 步先登记拿到编号，再按编号取行动（kernel.py:587、kernel.py:588）；删去 `record()`，第 5 步只剩状态更新（kernel.py:599）。
- `verify.py`：
  - 跑场景期间多替换一个函数 `register_action`，在它返回时记下行动表的情况（verify.py:162）。
  - 新增断言：行动表的键与顺序（verify.py:280）；登记即放入行动表（verify.py:283）；内核已删去 record（verify.py:566）。
  - 改动断言：行动选择不写任何东西（verify.py:289）；失败的行动在行动表里（verify.py:623）。
  - 各处遍历行动的代码改为遍历行动表的值。
- `observe.py`、`view.py`、`tools.py`：没有改动。查看器本来就按「行动提出」事件整理行动，不读任务对象。

### 10.4 检查输出

场景一控制台的第一圈，与第 9 节的事件完全相同，只有时间戳和等待毫秒数不同：

```
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.078 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.654 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
```

故意改坏：登记时不放进行动表（先放在任务的一个临时属性上），等第 5 步状态更新之后才放进行动表。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 97 条断言，通过 96 条，失败 1 条
  失败：登记即放入行动表：3 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 82 条断言，通过 81 条，失败 1 条
  失败：登记即放入行动表：3 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
场景四：部分预填：共 69 条断言，通过 68 条，失败 1 条
  失败：登记即放入行动表：2 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
结论：有断言失败
```

改动完成后运行 `python -m tod_kernel.verify` 的完整输出：

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.078 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.654 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ⇢ ·35 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.41 毫秒 〔kernel.mailbox〕
  ⇢ #36 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
  ·37 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.584 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #44 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #47 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #48 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #49 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·50 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·51 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #52 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
    #53 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ⇢ ·54 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.322 毫秒 〔kernel.mailbox〕
  ⇢ #55 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
  ·56 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #57 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3，回复问题 3 〔kernel.mailbox〕
  ⇢ ·58 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·59 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.554 毫秒 〔kernel.mailbox〕
  #60 消息取出：收件箱，类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3，回复问题 3 〔kernel.mailbox〕
    #61 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·62 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #63 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 64，追踪） ┈┈
  ·65 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #66 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #67 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #68 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·69 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 1.625 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的工具名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 3 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 3 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 3 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 3 的问题与回答，内容里的所属行动都是 3
  [通过] 目标二：行动 3 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现工具名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有工具表模块、任务模块和观测模块
  [通过] 内核已删去 record 函数，第 5 步只剩状态更新
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：3 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 4 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化 〔kernel.update〕
  ⇢ ·5 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #9 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #10 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #11 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·12 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.072 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个状态事件是「任务结束」
  [通过] 行动表为空，只有结束记录
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：0 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：0 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 1 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.929 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-3，等待 0.775 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'slot': '日期'}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ⇢ ·35 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.293 毫秒 〔kernel.mailbox〕
  ⇢ #36 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '日期'}，到达序号 2 〔kernel.mailbox〕
  ·37 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-3，等待 0.725 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #44 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #47 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #48 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #49 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·50 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·51 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #52 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
    #53 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ·54 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ ·55 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.198 毫秒 〔kernel.mailbox〕
  ⇢ #56 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 3 〔kernel.mailbox〕
  ⇢ #57 外部（host）· 邮箱关闭：收件箱，发起方 host，不会再有消息 〔kernel.mailbox〕
  ⇢ ·58 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·59 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.517 毫秒 〔kernel.mailbox〕
    #60 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔tool.ask〕
  ·61 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
── 结束 ──
  #62 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  ⇢ ·63 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 0.853 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前发出过问题（发件箱放入）
  [通过] 行动 3 在「已失败」之前没有收件箱的「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动在行动表里：行动表的编号是 1、2、3，行动 3 的状态是已失败
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：3 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：收件箱恰有一条「邮箱关闭」，发起方 host，落在行动 3 的「等待中」与「已失败」之间
  [通过] 邮箱关闭：出错时，发件箱在行动 3「已失败」之后由内核关闭
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（日期预填），槽位表 {'目的地': None, '日期': '9 月 20 日', '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['slot']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'slot': '目的地'}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.828 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '目的地'}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-4，等待 0.726 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'slot': '事由'}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ⇢ ·35 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.2 毫秒 〔kernel.mailbox〕
  ⇢ #36 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'slot': '事由'}，到达序号 2 〔kernel.mailbox〕
  ·37 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-4，等待 0.749 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #44 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #47 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #48 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #49 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·50 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 1.538 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是行动参数原样，且在「等待中」之前
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值 = 取出的消息内容 = 「已成功」事件的返回值
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：2 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：2 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是 tool.ask）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致

════════════ 汇总 ════════════
场景一：正常流程：共 97 条断言，通过 97 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 82 条断言，通过 82 条，失败 0 条
场景四：部分预填：共 69 条断言，通过 69 条，失败 0 条
结论：全部断言通过
```

### 10.5 本轮的偏差与疑问

1. 登记函数里，先把行动放进行动表，再发「行动提出」，最后记「已提出」。这样订阅者收到「行动提出」时，行动表里已经有这个行动。步骤文档没有规定表写入与发事件的先后，我按「先写入、再发布」的一贯做法处理（kernel.py:478）。
2. 「登记即放入行动表」的断言是在 `register_action` 返回的那一刻检查的，它证明的是「登记返回时已在表里」，不能证明「发事件时已在表里」。本步没有订阅者会在收到事件时去查行动表，所以没有再加更细的检查。
3. 故意改坏的那次试验里，所有「最终状态」类的断言都通过了，包括场景三那条「失败的行动在行动表里」。原因是它们只看运行结束时的行动表，改坏的版本到第 5 步也放进去了；能抓到登记时机的，只有这次新加的登记断言。那条断言原先按裁定写作「失败的行动从登记起就在行动表里」，名字说的是「从登记起」，实际却只检查最终状态。我报告编制会话后，它裁定改名为「失败的行动在行动表里」，登记时机交给登记断言负责。本节贴出的验证输出是改名后重跑的。
4. 第 3 到第 9 节贴出的代码与输出里，仍能看到 `record()` 与「行动列表」，那是当时的原样；这些节的文字引用的代码行号已按本节改动后的代码重新对过。
5. 并发执行本步没有做，只按裁定写进了步骤文档的遗留清单；验证脚本也没有任何并发场景。
6. 我没有填写步骤文档的验收记录，没有修改步骤文档，没有提交或推送代码。

---

# 第一步：第二个任务（2026-09-14）

本章记录第一步的实现过程，以及第一步文档第 5 节六个工作项的检查代码与实际输出。第零步已提交为 9472ac5，上面第 1 到第 10 节是第零步的记录，其中引用的代码行号对应提交 9472ac5 里的代码，第一步改动后没有重新对齐。本章里「步骤文档」指《第一步：第二个任务》。

## S1 结论

- 本步的目标是：内核三个文件（kernel.py、observe.py、view.py）一个字节不改，用第二个形状完全不同的任务「材料接入登记」跑通同一个循环。目标已达成，三个文件的 sha256 与提交 9472ac5 相同，验证脚本里写死了这三个值。
- 运行 `python -m tod_kernel.verify`，七个场景一次全部通过，退出码为 0；连续运行二十次，每次都全部通过。各场景断言条数：
  - 出差申请单：场景一 100 条、场景二 37 条、场景三 84 条、场景四 71 条；
  - 材料接入登记：场景一 146 条、场景二 141 条、场景三 108 条。
- 为确认断言确实能发现问题，我在临时副本里故意改坏过三处，验证脚本三次都报告了失败：
  - 内核文件开头多一个空格，哈希断言失败；
  - 规则互换变体的检查顺序被改回默认，工具顺序与规则序号两条断言失败；
  - 清单把所有文件都算作纳入，清单内容断言失败。
- 新增与改动的文件：
  - 新增 dialogue.py 与 task_intake.py；
  - tools.py 改写询问工具，加入按路径写值、按任务建工具表、样例表和三个领域工具；
  - task_travel.py 补了话语模板，选择规则的参数改为写入目标与提示；
  - verify.py 适配新的问题内容并新增三个场景；
  - `__init__.py` 更新了模块说明。
- 材料接入登记场景一的查看页在仓根的 `runs/T-intake-1.html`，截图是 `runs/T-intake-1.screenshot-1280.png`。查看页对第二个任务的显示不足列在 S4 节，按规定只记录、没有改 view.py。
- 步骤文档 6.1 节的验收记录没有填，代码没有提交。

## S2 开工前的疑问与裁定

开工前我向编制简报的会话提了四个问题，另附五处打算自定的做法；对方全部同意，并已改好文档：

1. 「显示人话」在不改 observe.py、view.py 的前提下做不到：控制台和查看页会把消息内容整个显示为一段 JSON。裁定：内核三个文件不动，内容字典里把话（utterance）放在参数（params）前面；验收要求改为「消息内容开头能读到那句人话」；「话没有单独成行」记为查看页不足，留给下一步改观测模块。
2. 工具表由 tools.py 的 `build_table(task_def, tool_names)` 按任务建，询问工具用 partial 绑定任务定义。出差申请单只登记询问工具，材料接入登记登记四个工具。
3. 列目录不带参数，从只读数据的「目录」槽位读目录名。
4. 询问工具改写后，第零步的一部分断言必须改，这些改动只做适配、不放宽断言要检查的意思，逐条列在 S5 节。

五处自定做法：
- 话语模板的键是（槽位名, 路径里去掉数字下标后的键组成的元组）；
- 询问工具的返回值是回答理解后的值，变更条的新值是整个槽位的新值；
- 生成清单文件把「是否纳入」等于「是」的项算作纳入；
- 规则互换变体保留规则原来的序号，只改检查顺序；
- 「材料清单」初始空列表没有被内核复制的隐患，不修内核，只记录下来。

## S3 逐项记录

检查脚本放在会话临时目录，没有进仓库；运行方式都是在仓根下执行 `PYTHONPATH=. python3 <临时目录>/<脚本>`。

### 第一项：对话模块与按路径写值

新建 dialogue.py，其中 `generate_utterance` 按写入目标查话语模板并用提示填空，查不到时返回可读拼写；`understand_answer` 原样返回。tools.py 加入 `set_at`。

步骤文档要求能看到两件事：对出差申请单调用话语生成，写入目标是目的地时得到「请提供出差目的地。」；对三项列表调用 `set_at` 改第二项的一个字段，新表只有那一处不同，旧表没被改动。实际结果与要求相符。

做这一项时出差申请单还没有话语模板（第二项才补），所以检查脚本临时给了一份同形的模板表。

检查代码 `s1_item1.py`：

```python
import copy
from tod_kernel import task_travel
from tod_kernel.dialogue import generate_utterance, template_key, understand_answer
from tod_kernel.tools import set_at

# 出差申请单还没有 TEMPLATES（第二项才补），这里临时给一份同形的模板表来检查话语生成。
class TravelWithTemplates:
    TEMPLATES = {("目的地", ()): "请提供出差目的地。"}

params = {"target": {"slot": "目的地", "path": []}, "hint": {}}
print("模板键:", template_key(params["target"]))
print("话语生成:", generate_utterance(TravelWithTemplates, params))
print("查不到模板时:", generate_utterance(task_travel, params))
print("回答理解（原样返回）:", repr(understand_answer(task_travel, params, "上海")))

old = [{"文件名": "a.docx", "是否纳入": None}, {"文件名": "b.pdf", "是否纳入": None}, {"文件名": "c.xlsx", "是否纳入": None}]
snapshot = copy.deepcopy(old)
new = set_at(old, [1, "是否纳入"], "否")
print("set_at 新表:", new)
print("旧表没被改动:", old == snapshot)
print("新旧表不同的位置:", [(i, k) for i in range(3) for k in old[i] if old[i][k] != new[i][k]])
print("新表不与旧表共用任何一项:", all(new[i] is not old[i] for i in range(3)))
print("空路径直接返回新值:", set_at("旧", [], "新"))
```

实际输出：

```
模板键: ('目的地', ())
话语生成: 请提供出差目的地。
查不到模板时: 请提供「目的地」的值。
回答理解（原样返回）: '上海'
set_at 新表: [{'文件名': 'a.docx', '是否纳入': None}, {'文件名': 'b.pdf', '是否纳入': '否'}, {'文件名': 'c.xlsx', '是否纳入': None}]
旧表没被改动: True
新旧表不同的位置: [(1, '是否纳入')]
新表不与旧表共用任何一项: True
空路径直接返回新值: 新
```

### 第二项：改写询问工具，第零步照跑

询问工具改为流水线：先生成话，再往发件箱放问题，内容是 {话, 参数}；记等待中；取回答；回答理解；按路径算出新值；拼变更。`build_table(task_def, tool_names)` 按任务建工具表，询问工具用 partial 绑定任务定义。出差申请单补了三条话语模板，选择规则的参数改为写入目标（槽位，空路径）与空提示。验证脚本按 S5 节第 3 到 9 条适配。

步骤文档要求能看到：第零步四个场景原样全过；问题消息的内容开头能读到「请提供出差目的地。」；kernel.py、observe.py、view.py 与提交 9472ac5 逐字节相同。实际结果与要求相符。下面是三个文件的哈希对比、场景一第 1 圈里提问与回答的几行，以及当时的汇总：

```
--- 三个内核文件与提交 9472ac5 的差异（git diff --stat，空表示没有差异）:
kernel.py 提交里 b28cb651f1ab500cee7306a891b42417f6063897fc4cfb2d6743f65a917974e2 工作区 b28cb651f1ab500cee7306a891b42417f6063897fc4cfb2d6743f65a917974e2
observe.py 提交里 12f7168bfb27a2318cc5e2771adbd7b55e441a07f192a6dfb4bcdaf42999c570 工作区 12f7168bfb27a2318cc5e2771adbd7b55e441a07f192a6dfb4bcdaf42999c570
view.py 提交里 958e62c499ce7771316d2a40d9188208b0678c429e43ee975ea7ddb74cebd651 工作区 958e62c499ce7771316d2a40d9188208b0678c429e43ee975ea7ddb74cebd651
--- 场景一第 1 圈里提问与回答的几行:
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
--- 汇总:
════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
结论：全部断言通过
```

### 第三项：样例表与三个领域工具

tools.py 加入样例表 `SAMPLE_DIRS`（目录「样例材料」下 a.docx、b.pdf、c.xlsx 三个文件），以及列目录、登记文件、生成清单文件三个工具；可写槽位与前置条件按步骤文档 4.1 节填上，本步没有代码读它们。

步骤文档要求能看到：单独对手工构造的执行上下文调用列目录，变更组是「文件总表 None → 三个文件名」；调用登记文件 0，变更组两条，材料清单多了 a 项，进度从 0 到 1。实际结果与要求相符。检查代码 `s1_item3.py`：

```python
import types
from tod_kernel.kernel import Action, ActionStatus, ExecContext
from tod_kernel.tools import build_table

table = build_table(task_def=None, tool_names=("ask", "list_dir", "register_file", "generate_manifest"))
print("登记的工具与参数名:", {name: tool.param_names for name, tool in table.items()})
print("可写槽位:", {name: sorted(tool.writable_slots) for name, tool in table.items() if tool.writable_slots})

def run_tool(name, params, data):
    statuses = []
    action = Action(action_id=1, tool=name, params=params, proposer="selector", basis=None)
    ctx = ExecContext(action=action, data_view=types.MappingProxyType(data), inbox=None, outbox=None,
                      set_status=lambda status, note: statuses.append((status.value, note)))
    table.get(name).impl(ctx)
    return action, statuses

data = {"目录": "样例材料", "文件总表": None, "材料清单": [], "登记进度": 0, "清单文件路径": None}
print("--- 调列目录 ---")
action, statuses = run_tool("list_dir", {}, data)
print("状态:", statuses)
print("变更组:", action.changes)
print("前置条件（执行前的数据）:", table.get("list_dir").preconditions(data, {}))

print("--- 调登记文件 0 ---")
data = {**data, "文件总表": action.result}
action, statuses = run_tool("register_file", {"index": 0}, data)
print("状态:", statuses)
for change in action.changes:
    print("变更:", change)
print("前置条件（执行前的数据）:", table.get("register_file").preconditions(data, {"index": 0}))
print("数据字典没有被工具改动:", data["材料清单"] == [] and data["登记进度"] == 0)
```

实际输出：

```
登记的工具与参数名: {'ask': ('target', 'hint'), 'list_dir': (), 'register_file': ('index',), 'generate_manifest': ()}
可写槽位: {'list_dir': ['文件总表'], 'register_file': ['材料清单', '登记进度'], 'generate_manifest': ['清单文件路径']}
--- 调列目录 ---
状态: [('已成功', '列出 3 个文件')]
变更组: [Change(slot='文件总表', old=None, new=['a.docx', 'b.pdf', 'c.xlsx'], source=1)]
前置条件（执行前的数据）: (True, '文件总表为 None')
--- 调登记文件 0 ---
状态: [('已成功', '登记文件 a.docx')]
变更: Change(slot='材料清单', old=[], new=[{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}], source=1)
变更: Change(slot='登记进度', old=0, new=1, source=1)
前置条件（执行前的数据）: (True, '序号等于登记进度且小于文件总数')
数据字典没有被工具改动: True
```

### 第四项：材料接入登记的任务定义

新建 task_intake.py：NAME、SLOTS、RULES、TEMPLATES、is_done、select_action，另有 `variant(name, order)` 与规则二、三互换的变体 `SWAPPED`。

步骤文档要求能看到：对初始数据调用选择规则得到列目录；对文件总表已填、进度 3、材料清单三项都未问的数据，得到询问，写入目标是材料清单 [0, 是否纳入]，提示是 a.docx。实际结果与要求相符。检查代码 `s1_item4.py`：

```python
import copy, types
from tod_kernel import task_intake

initial = copy.deepcopy(task_intake.SLOTS)
print("初始数据:", initial)
print("初始数据的选择结果:", task_intake.select_action(types.MappingProxyType(initial)))

files = ["a.docx", "b.pdf", "c.xlsx"]
items = [{"文件名": f, "类型": "?", "大小": 0, "页数": 0, "是否纳入": None} for f in files]
data = {**initial, "文件总表": files, "登记进度": 3, "材料清单": items}
print("文件总表已填、进度 3、三项都未问时:", task_intake.select_action(types.MappingProxyType(data)))
print("完成条件（此时）:", task_intake.is_done(data))

print("--- 规则二三互换的变体 ---")
print("变体规则清单顺序:", list(task_intake.SWAPPED.RULES))
partial = {**initial, "文件总表": files, "登记进度": 1, "材料清单": items[:1]}
print("默认顺序（进度 1，a 未问）:", task_intake.select_action(types.MappingProxyType(partial))[:2])
print("互换顺序（进度 1，a 未问）:", task_intake.SWAPPED.select_action(types.MappingProxyType(partial))[:2])
done = {**data, "材料清单": [{**i, "是否纳入": "是"} for i in items], "清单文件路径": "样例材料/材料清单.txt"}
print("全部完成时的选择结果:", task_intake.select_action(types.MappingProxyType(done)), "完成条件:", task_intake.is_done(done))
```

实际输出：

```
初始数据: {'目录': '样例材料', '文件总表': None, '材料清单': [], '登记进度': 0, '清单文件路径': None}
初始数据的选择结果: ('list_dir', {}, (1, '文件总表为 None：列目录', None))
文件总表已填、进度 3、三项都未问时: ('ask', {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}, (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 0, '文件名': 'a.docx', '是否纳入': None}))
完成条件（此时）: False
--- 规则二三互换的变体 ---
变体规则清单顺序: [1, 3, 2, 4]
默认顺序（进度 1，a 未问）: ('register_file', {'index': 1})
互换顺序（进度 1，a 未问）: ('ask', {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}})
全部完成时的选择结果: None 完成条件: True
```

### 第五项：验证脚本加场景

verify.py 新增三个场景：材料接入登记的正常流程、规则互换变体、回答缺失。答案表以写入目标为键；预期的六句话逐条写死在脚本里；三个内核文件的 sha256 写死在脚本里。

步骤文档要求能看到：`python -m tod_kernel.verify` 七个场景一次跑完全过。实际结果与要求相符，完整输出见本节末尾。先贴材料接入登记场景一的两段控制台输出。第 5 圈是第一个询问，「↗」是向外部提问，问题内容开头是话：

```
┈┈ 第 5 圈开始（事件 52，追踪） ┈┈
  ·53 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 5 ──
  #54 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 0, '文件名': 'a.docx', '是否纳入': None}) 〔kernel.loop〕
  #55 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #56 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·57 执行控制结论：行动 5，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·58 行动执行调用：行动 5，工具 ask，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
  ↗ #59 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
    #60 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ·61 邮箱等待：收件箱，行动 5，阶段 begin，线程 kernel-T-intake-1 〔kernel.mailbox〕
  ⇢ ·62 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 9.45 毫秒 〔kernel.mailbox〕
  ⇢ #63 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
  ⇢ #64 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 5，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·65 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·66 邮箱等待：收件箱，行动 5，阶段 end，线程 kernel-T-intake-1，等待 0.82 毫秒 〔kernel.mailbox〕
  #67 消息取出：收件箱，类型 answer，发起方 user，收件人 5，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #68 行动状态变化：已成功，说明：回答到达，返回值 '是' 〔tool.ask〕
  ·69 行动执行调用：行动 5，工具 ask，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #70 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 5 〔kernel.update〕
┈┈ 第 6 圈开始（事件 71，追踪） ┈┈
```

第 8、9 圈：生成清单文件，任务结束：

```
┈┈ 第 8 圈开始（事件 109，追踪） ┈┈
  ·110 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 8 ──
  #111 行动提出：工具 generate_manifest，参数 {}，提出者 selector，依据 (4, '清单文件路径为 None：生成清单文件', None) 〔kernel.loop〕
  #112 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #113 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·114 执行控制结论：行动 8，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·115 行动执行调用：行动 8，工具 generate_manifest，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
    #116 行动状态变化：已成功，说明：清单含 2 项，写到 样例材料/材料清单.txt，返回值 '材料清单（共 2 项）\n1. a.docx，docx，20480 字节，3 页\n2. c.xlsx，xlsx，10240 字节，1 页' 〔tool.generate_manifest〕
  ·117 行动执行调用：行动 8，工具 generate_manifest，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #118 数据变更：槽位「清单文件路径」 未填写(None) → '样例材料/材料清单.txt'，来源 8 〔kernel.update〕
┈┈ 第 9 圈开始（事件 119，追踪） ┈┈
  ·120 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #121 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #122 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #123 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·124 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.241 毫秒 〔kernel.mailbox〕
── 断言 ──
```

故意改坏一：内核文件 kernel.py 开头的文档字符串里多一个空格。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 146 条断言，通过 145 条，失败 1 条
  失败：第一步目标一：kernel.py 的 sha256 等于提交 9472ac5 里的值
材料接入登记·场景二：规则互换变体：共 141 条断言，通过 140 条，失败 1 条
  失败：第一步目标一：kernel.py 的 sha256 等于提交 9472ac5 里的值
材料接入登记·场景三：回答缺失：共 108 条断言，通过 107 条，失败 1 条
  失败：第一步目标一：kernel.py 的 sha256 等于提交 9472ac5 里的值
结论：有断言失败
```

故意改坏二：规则互换变体的检查顺序被改回默认的 1、2、3、4。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 146 条断言，通过 146 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 141 条断言，通过 139 条，失败 2 条
  失败：第一步目标二：工具依次是 列目录、登记文件、询问、登记文件、询问、登记文件、询问、生成清单文件
  失败：第一步目标二：依据里的规则序号依次是 一、二、三、二、三、二、三、四（规则保留原序号，只改检查顺序）
材料接入登记·场景三：回答缺失：共 108 条断言，通过 108 条，失败 0 条
结论：有断言失败
```

故意改坏三：生成清单文件把所有文件都算作纳入。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 146 条断言，通过 145 条，失败 1 条
  失败：生成清单文件的返回值含 a.docx 与 c.xlsx、不含 b.pdf
材料接入登记·场景二：规则互换变体：共 141 条断言，通过 141 条，失败 0 条
材料接入登记·场景三：回答缺失：共 108 条断言，通过 108 条，失败 0 条
结论：有断言失败
```

七个场景的完整输出（连续运行二十次都全部通过，这是其后单独再运行的一次）：

```

════════════ 场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['target', 'hint']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'target': {'slot': '目的地', 'path': []}, 'hint': {}}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.329 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-1，等待 0.698 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'target': {'slot': '日期', 'path': []}, 'hint': {}}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差日期。', 'params': {'target': {'slot': '日期', 'path': []}, 'hint': {}}}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ⇢ ·35 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.571 毫秒 〔kernel.mailbox〕
  ⇢ #36 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差日期。', 'params': {'target': {'slot': '日期', 'path': []}, 'hint': {}}}，到达序号 2 〔kernel.mailbox〕
  ·37 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-1，等待 0.59 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #44 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #47 行动提出：工具 ask，参数 {'target': {'slot': '事由', 'path': []}, 'hint': {}}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #48 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #49 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·50 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·51 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-1 〔kernel.loop〕
  ↗ #52 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差事由。', 'params': {'target': {'slot': '事由', 'path': []}, 'hint': {}}}，到达序号 3 〔kernel.mailbox〕
    #53 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ⇢ ·54 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 4.135 毫秒 〔kernel.mailbox〕
  ⇢ #55 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差事由。', 'params': {'target': {'slot': '事由', 'path': []}, 'hint': {}}}，到达序号 3 〔kernel.mailbox〕
  ·56 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-scenario-1 〔kernel.mailbox〕
  ⇢ #57 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3，回复问题 3 〔kernel.mailbox〕
  ⇢ ·58 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·59 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-scenario-1，等待 0.606 毫秒 〔kernel.mailbox〕
  #60 消息取出：收件箱，类型 answer，发起方 user，收件人 3，内容 '客户拜访'，到达序号 3，回复问题 3 〔kernel.mailbox〕
    #61 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·62 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-1 〔kernel.loop〕
  #63 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 64，追踪） ┈┈
  ·65 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #66 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #67 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #68 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·69 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 1.682 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 目标一：任务状态是已完成
  [通过] 目标一：恰好三个「行动提出」
  [通过] 目标一：三个行动的工具名都是 ask
  [通过] 目标一：参数依次是目的地、日期、事由
  [通过] 目标一：「已完成」状态变化恰好一次
  [通过] 目标一：「任务结束」恰好一次，原因是完成条件成立
  [通过] 目标一：终态数据是三条回答
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 1 问题里的话逐字等于预期句「请提供出差目的地。」
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值按写入目标路径 [] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 2 问题里的话逐字等于预期句「请提供出差日期。」
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值按写入目标路径 [] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 3 问题里的话逐字等于预期句「请提供出差事由。」
  [通过] 目标二：行动 3 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 3 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 3 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 3 的问题与回答，内容里的所属行动都是 3
  [通过] 目标二：行动 3 的数据变更新值按写入目标路径 [] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标四：内核文件里「目的地」「日期」「事由」三个词的命中数为零
  [通过] 目标四（附加）：内核文件里不出现工具名（字符串 "ask" 与「询问」）
  [通过] 目标四：内核模块的导入语句里没有工具表模块、任务模块和观测模块
  [通过] 内核已删去 record 函数，第 5 步只剩状态更新
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：6 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：3 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 4 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是所用工具的 tool.<工具名>：['tool.ask']）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景二：初始即完成 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（三项预填），槽位表 {'目的地': '上海', '日期': '9 月 20 日', '事由': '客户拜访'}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['target', 'hint']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #9 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #10 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #11 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·12 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.094 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 事件流里没有「行动提出」
  [通过] 任务状态是已完成
  [通过] 最后一个状态事件是「任务结束」
  [通过] 行动表为空，只有结束记录
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：3 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：0 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：0 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 1 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据

════════════ 场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单，槽位表 {'目的地': None, '日期': None, '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['target', 'hint']} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「日期」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #6 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 7，追踪） ┈┈
  ·8 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'target': {'slot': '目的地', 'path': []}, 'hint': {}}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ⇢ ·16 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.065 毫秒 〔kernel.mailbox〕
  ⇢ #17 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
  ·18 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-3，等待 0.646 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'target': {'slot': '日期', 'path': []}, 'hint': {}}，提出者 selector，依据 (2, '日期尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差日期。', 'params': {'target': {'slot': '日期', 'path': []}, 'hint': {}}}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ⇢ ·35 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.473 毫秒 〔kernel.mailbox〕
  ⇢ #36 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差日期。', 'params': {'target': {'slot': '日期', 'path': []}, 'hint': {}}}，到达序号 2 〔kernel.mailbox〕
  ·37 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-3，等待 0.558 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '9 月 20 日'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '9 月 20 日' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
  #44 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #47 行动提出：工具 ask，参数 {'target': {'slot': '事由', 'path': []}, 'hint': {}}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #48 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #49 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·50 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·51 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-scenario-3 〔kernel.loop〕
  ↗ #52 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差事由。', 'params': {'target': {'slot': '事由', 'path': []}, 'hint': {}}}，到达序号 3 〔kernel.mailbox〕
    #53 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ⇢ ·54 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.36 毫秒 〔kernel.mailbox〕
  ⇢ #55 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差事由。', 'params': {'target': {'slot': '事由', 'path': []}, 'hint': {}}}，到达序号 3 〔kernel.mailbox〕
  ·56 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-scenario-3 〔kernel.mailbox〕
  ⇢ #57 外部（host）· 邮箱关闭：收件箱，发起方 host，不会再有消息 〔kernel.mailbox〕
  ⇢ ·58 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·59 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-scenario-3，等待 0.535 毫秒 〔kernel.mailbox〕
    #60 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔tool.ask〕
  ·61 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-scenario-3 〔kernel.loop〕
── 结束 ──
  #62 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  ⇢ ·63 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 0.864 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 3
  [通过] 行动 1 恰有一条「行动提出」事件
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 1 问题里的话逐字等于预期句「请提供出差目的地。」
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值按写入目标路径 [] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 行动 2 恰有一条「行动提出」事件
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 2 问题里的话逐字等于预期句「请提供出差日期。」
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值按写入目标路径 [] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 行动 3 恰有一条「行动提出」事件
  [通过] 行动 3 的状态序列是 已提出、已获准、等待中、已失败
  [通过] 行动 3 的最后一个状态变化是「已失败，说明：没有可用的回答」
  [通过] 行动 3 在「已失败」之前发出过问题（发件箱放入）
  [通过] 行动 3 在「已失败」之前没有收件箱的「消息取出」
  [通过] 行动 3 没有数据变更
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中
  [通过] 失败的行动在行动表里：行动表的编号是 1、2、3，行动 3 的状态是已失败
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：3 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：3 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是所用工具的 tool.<工具名>：['tool.ask']）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：收件箱恰有一条「邮箱关闭」，发起方 host，落在行动 3 的「等待中」与「已失败」之间
  [通过] 邮箱关闭：出错时，发件箱在行动 3「已失败」之后由内核关闭
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致

════════════ 场景四：部分预填 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 出差申请单（日期预填），槽位表 {'目的地': None, '日期': '9 月 20 日', '事由': None}，规则 {1: '目的地尚未填写', 2: '日期尚未填写', 3: '事由尚未填写'}，工具 {'ask': ['target', 'hint']} 〔kernel.loop〕
  #2 数据变更：槽位「目的地」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「日期」 未填写(None) → '9 月 20 日'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「事由」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 6，追踪） ┈┈
  ·7 结果检查结论：已完成=False 〔kernel.loop〕
  ⇢ ·8 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
── 行动 1 ──
  #9 行动提出：工具 ask，参数 {'target': {'slot': '目的地', 'path': []}, 'hint': {}}，提出者 selector，依据 (1, '目的地尚未填写', None) 〔kernel.loop〕
  #10 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #11 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·12 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·13 行动执行调用：行动 1，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
  ↗ #14 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
    #15 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ·16 邮箱等待：收件箱，行动 1，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ ·17 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.944 毫秒 〔kernel.mailbox〕
  ⇢ #18 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差目的地。', 'params': {'target': {'slot': '目的地', 'path': []}, 'hint': {}}}，到达序号 1 〔kernel.mailbox〕
  ⇢ #19 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·20 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·21 邮箱等待：收件箱，行动 1，阶段 end，线程 kernel-T-scenario-4，等待 0.765 毫秒 〔kernel.mailbox〕
  #22 消息取出：收件箱，类型 answer，发起方 user，收件人 1，内容 '上海'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #23 行动状态变化：已成功，说明：回答到达，返回值 '上海' 〔tool.ask〕
  ·24 行动执行调用：行动 1，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #25 数据变更：槽位「目的地」 未填写(None) → '上海'，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 26，追踪） ┈┈
  ·27 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #28 行动提出：工具 ask，参数 {'target': {'slot': '事由', 'path': []}, 'hint': {}}，提出者 selector，依据 (3, '事由尚未填写', None) 〔kernel.loop〕
  #29 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #30 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·31 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·32 行动执行调用：行动 2，工具 ask，阶段 enter，线程 kernel-T-scenario-4 〔kernel.loop〕
  ↗ #33 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差事由。', 'params': {'target': {'slot': '事由', 'path': []}, 'hint': {}}}，到达序号 2 〔kernel.mailbox〕
    #34 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ·35 邮箱等待：收件箱，行动 2，阶段 begin，线程 kernel-T-scenario-4 〔kernel.mailbox〕
  ⇢ ·36 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.419 毫秒 〔kernel.mailbox〕
  ⇢ #37 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请提供出差事由。', 'params': {'target': {'slot': '事由', 'path': []}, 'hint': {}}}，到达序号 2 〔kernel.mailbox〕
  ⇢ #38 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·39 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·40 邮箱等待：收件箱，行动 2，阶段 end，线程 kernel-T-scenario-4，等待 0.706 毫秒 〔kernel.mailbox〕
  #41 消息取出：收件箱，类型 answer，发起方 user，收件人 2，内容 '客户拜访'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #42 行动状态变化：已成功，说明：回答到达，返回值 '客户拜访' 〔tool.ask〕
  ·43 行动执行调用：行动 2，工具 ask，阶段 return，线程 kernel-T-scenario-4 〔kernel.loop〕
  #44 数据变更：槽位「事由」 未填写(None) → '客户拜访'，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 45，追踪） ┈┈
  ·46 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #47 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #48 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #49 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·50 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 1.594 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 内核线程正常返回、没有抛异常
  [通过] 恰好两个「行动提出」
  [通过] 参数依次是目的地、事由
  [通过] 除初始化那一条外，没有针对日期的数据变更事件
  [通过] 日期的值全程未变：置执行中之后每个事件时刻重放出的日期都是 9 月 20 日
  [通过] 任务状态是已完成，数据完整
  [通过] 目标二：行动 1 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 1 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 1 问题里的话逐字等于预期句「请提供出差目的地。」
  [通过] 目标二：行动 1 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 1 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 1 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 1 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 1 的问题与回答，内容里的所属行动都是 1
  [通过] 目标二：行动 1 的数据变更新值按写入目标路径 [] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 2 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 2 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 2 问题里的话逐字等于预期句「请提供出差事由。」
  [通过] 目标二：行动 2 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 2 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 2 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 2 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 2 的问题与回答，内容里的所属行动都是 2
  [通过] 目标二：行动 2 的数据变更新值按写入目标路径 [] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：5 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：2 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：2 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 3 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 1 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 2 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是所用工具的 tool.<工具名>：['tool.ask']）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致

════════════ 材料接入登记·场景一：正常流程 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 材料接入登记，槽位表 {'目录': '样例材料', '文件总表': None, '材料清单': [], '登记进度': 0, '清单文件路径': None}，规则 {1: '文件总表为 None：列目录', 2: '登记进度小于文件总数：登记文件', 3: '材料清单里存在「是否纳入」为 None 的项：询问', 4: '清单文件路径为 None：生成清单文件'}，工具 {'ask': ['target', 'hint'], 'list_dir': [], 'register_file': ['index'], 'generate_manifest': []} 〔kernel.loop〕
  ⇢ ·2 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  #3 数据变更：槽位「目录」 未填写(None) → '样例材料'，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「文件总表」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「材料清单」 未填写(None) → []，来源 初始化 〔kernel.update〕
  #6 数据变更：槽位「登记进度」 未填写(None) → 0，来源 初始化 〔kernel.update〕
  #7 数据变更：槽位「清单文件路径」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #8 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 9，追踪） ┈┈
  ·10 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 1 ──
  #11 行动提出：工具 list_dir，参数 {}，提出者 selector，依据 (1, '文件总表为 None：列目录', None) 〔kernel.loop〕
  #12 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #13 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·14 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·15 行动执行调用：行动 1，工具 list_dir，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
    #16 行动状态变化：已成功，说明：列出 3 个文件，返回值 ['a.docx', 'b.pdf', 'c.xlsx'] 〔tool.list_dir〕
  ·17 行动执行调用：行动 1，工具 list_dir，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #18 数据变更：槽位「文件总表」 未填写(None) → ['a.docx', 'b.pdf', 'c.xlsx']，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 19，追踪） ┈┈
  ·20 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #21 行动提出：工具 register_file，参数 {'index': 0}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 0, '文件总数': 3}) 〔kernel.loop〕
  #22 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #23 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·24 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·25 行动执行调用：行动 2，工具 register_file，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
    #26 行动状态变化：已成功，说明：登记文件 a.docx，返回值 {'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None} 〔tool.register_file〕
  ·27 行动执行调用：行动 2，工具 register_file，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #28 数据变更：槽位「材料清单」 [] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}]，来源 2 〔kernel.update〕
  #29 数据变更：槽位「登记进度」 0 → 1，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 30，追踪） ┈┈
  ·31 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #32 行动提出：工具 register_file，参数 {'index': 1}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 1, '文件总数': 3}) 〔kernel.loop〕
  #33 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #34 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·35 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·36 行动执行调用：行动 3，工具 register_file，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
    #37 行动状态变化：已成功，说明：登记文件 b.pdf，返回值 {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None} 〔tool.register_file〕
  ·38 行动执行调用：行动 3，工具 register_file，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #39 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}]，来源 3 〔kernel.update〕
  #40 数据变更：槽位「登记进度」 1 → 2，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 41，追踪） ┈┈
  ·42 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 4 ──
  #43 行动提出：工具 register_file，参数 {'index': 2}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 2, '文件总数': 3}) 〔kernel.loop〕
  #44 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #45 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·46 执行控制结论：行动 4，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·47 行动执行调用：行动 4，工具 register_file，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
    #48 行动状态变化：已成功，说明：登记文件 c.xlsx，返回值 {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None} 〔tool.register_file〕
  ·49 行动执行调用：行动 4，工具 register_file，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #50 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 4 〔kernel.update〕
  #51 数据变更：槽位「登记进度」 2 → 3，来源 4 〔kernel.update〕
┈┈ 第 5 圈开始（事件 52，追踪） ┈┈
  ·53 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 5 ──
  #54 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 0, '文件名': 'a.docx', '是否纳入': None}) 〔kernel.loop〕
  #55 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #56 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·57 执行控制结论：行动 5，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·58 行动执行调用：行动 5，工具 ask，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
  ↗ #59 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
    #60 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ·61 邮箱等待：收件箱，行动 5，阶段 begin，线程 kernel-T-intake-1 〔kernel.mailbox〕
  ⇢ ·62 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 9.45 毫秒 〔kernel.mailbox〕
  ⇢ #63 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
  ⇢ #64 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 5，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·65 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·66 邮箱等待：收件箱，行动 5，阶段 end，线程 kernel-T-intake-1，等待 0.82 毫秒 〔kernel.mailbox〕
  #67 消息取出：收件箱，类型 answer，发起方 user，收件人 5，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #68 行动状态变化：已成功，说明：回答到达，返回值 '是' 〔tool.ask〕
  ·69 行动执行调用：行动 5，工具 ask，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #70 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 5 〔kernel.update〕
┈┈ 第 6 圈开始（事件 71，追踪） ┈┈
  ·72 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 6 ──
  #73 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 1, '文件名': 'b.pdf', '是否纳入': None}) 〔kernel.loop〕
  #74 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #75 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·76 执行控制结论：行动 6，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·77 行动执行调用：行动 6，工具 ask，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
  ↗ #78 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 b.pdf 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}}，到达序号 2 〔kernel.mailbox〕
    #79 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ·80 邮箱等待：收件箱，行动 6，阶段 begin，线程 kernel-T-intake-1 〔kernel.mailbox〕
  ⇢ ·81 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.793 毫秒 〔kernel.mailbox〕
  ⇢ #82 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 b.pdf 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}}，到达序号 2 〔kernel.mailbox〕
  ⇢ #83 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 6，内容 '否'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·84 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·85 邮箱等待：收件箱，行动 6，阶段 end，线程 kernel-T-intake-1，等待 0.765 毫秒 〔kernel.mailbox〕
  #86 消息取出：收件箱，类型 answer，发起方 user，收件人 6，内容 '否'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #87 行动状态变化：已成功，说明：回答到达，返回值 '否' 〔tool.ask〕
  ·88 行动执行调用：行动 6，工具 ask，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #89 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 6 〔kernel.update〕
┈┈ 第 7 圈开始（事件 90，追踪） ┈┈
  ·91 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 7 ──
  #92 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 2, '文件名': 'c.xlsx', '是否纳入': None}) 〔kernel.loop〕
  #93 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #94 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·95 执行控制结论：行动 7，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·96 行动执行调用：行动 7，工具 ask，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
  ↗ #97 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 c.xlsx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}}，到达序号 3 〔kernel.mailbox〕
    #98 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ·99 邮箱等待：收件箱，行动 7，阶段 begin，线程 kernel-T-intake-1 〔kernel.mailbox〕
  ⇢ ·100 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.555 毫秒 〔kernel.mailbox〕
  ⇢ #101 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 c.xlsx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}}，到达序号 3 〔kernel.mailbox〕
  ⇢ #102 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 7，内容 '是'，到达序号 3，回复问题 3 〔kernel.mailbox〕
  ⇢ ·103 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·104 邮箱等待：收件箱，行动 7，阶段 end，线程 kernel-T-intake-1，等待 0.687 毫秒 〔kernel.mailbox〕
  #105 消息取出：收件箱，类型 answer，发起方 user，收件人 7，内容 '是'，到达序号 3，回复问题 3 〔kernel.mailbox〕
    #106 行动状态变化：已成功，说明：回答到达，返回值 '是' 〔tool.ask〕
  ·107 行动执行调用：行动 7，工具 ask，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #108 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': '是'}]，来源 7 〔kernel.update〕
┈┈ 第 8 圈开始（事件 109，追踪） ┈┈
  ·110 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 8 ──
  #111 行动提出：工具 generate_manifest，参数 {}，提出者 selector，依据 (4, '清单文件路径为 None：生成清单文件', None) 〔kernel.loop〕
  #112 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #113 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·114 执行控制结论：行动 8，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·115 行动执行调用：行动 8，工具 generate_manifest，阶段 enter，线程 kernel-T-intake-1 〔kernel.loop〕
    #116 行动状态变化：已成功，说明：清单含 2 项，写到 样例材料/材料清单.txt，返回值 '材料清单（共 2 项）\n1. a.docx，docx，20480 字节，3 页\n2. c.xlsx，xlsx，10240 字节，1 页' 〔tool.generate_manifest〕
  ·117 行动执行调用：行动 8，工具 generate_manifest，阶段 return，线程 kernel-T-intake-1 〔kernel.loop〕
  #118 数据变更：槽位「清单文件路径」 未填写(None) → '样例材料/材料清单.txt'，来源 8 〔kernel.update〕
┈┈ 第 9 圈开始（事件 119，追踪） ┈┈
  ·120 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #121 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #122 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #123 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·124 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.241 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 第一步目标一：kernel.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一：observe.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一：view.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一（附加）：内核文件里不出现材料接入登记的槽位名与工具名
  [通过] 内核线程正常返回、没有抛异常
  [通过] 第一步目标二：恰好八个行动（九圈里最后一圈结果检查为真，不提行动）
  [通过] 第一步目标二：工具依次是 列目录、登记文件×3、询问×3、生成清单文件
  [通过] 第一步目标二：依据里的规则序号依次是 一、二、二、二、三、三、三、四
  [通过] 第一步目标二：登记文件的参数序号依次是 0、1、2
  [通过] 第一步目标二：询问的写入目标依次是 材料清单 [0/1/2, 是否纳入]，提示是文件名
  [通过] 任务状态是已完成，终态数据与预期完全相同
  [通过] 终态材料清单三项的是否纳入依次是 是、否、是
  [通过] 生成清单文件的返回值含 a.docx 与 c.xlsx、不含 b.pdf
  [通过] 目标二：行动 5 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 5 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 5 问题里的话逐字等于预期句「请确认是否把文件 a.docx 纳入项目。」
  [通过] 目标二：行动 5 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 5 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 5 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 5 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 5 的问题与回答，内容里的所属行动都是 5
  [通过] 目标二：行动 5 的数据变更新值按写入目标路径 [0, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 6 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 6 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 6 问题里的话逐字等于预期句「请确认是否把文件 b.pdf 纳入项目。」
  [通过] 目标二：行动 6 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 6 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 6 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 6 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 6 的问题与回答，内容里的所属行动都是 6
  [通过] 目标二：行动 6 的数据变更新值按写入目标路径 [1, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 7 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 7 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 7 问题里的话逐字等于预期句「请确认是否把文件 c.xlsx 纳入项目。」
  [通过] 目标二：行动 7 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 7 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 7 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 7 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 7 的问题与回答，内容里的所属行动都是 7
  [通过] 目标二：行动 7 的数据变更新值按写入目标路径 [2, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 行动 1（list_dir）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['文件总表']
  [通过] 行动 2（register_file）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['材料清单', '登记进度']
  [通过] 行动 3（register_file）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['材料清单', '登记进度']
  [通过] 行动 4（register_file）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['材料清单', '登记进度']
  [通过] 行动 8（generate_manifest）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['清单文件路径']
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 4 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 4 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 5 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 5 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 6 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 6 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 7 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 7 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 8 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 8 返回值与行动对象上的一致
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 4 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 5 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 6 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 7 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 8 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：11 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：8 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：8 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 4 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 5 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 6 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 7 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 8 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 9 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1（工具 list_dir）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 4 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 4 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 4（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 5 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 5 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 5 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 5 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 6 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 6 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 6 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 6 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 7 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 7 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 7 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 7 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 8 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 8 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 8（工具 generate_manifest）不碰邮箱，没有邮箱等待
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是所用工具的 tool.<工具名>：['tool.ask', 'tool.generate_manifest', 'tool.list_dir', 'tool.register_file']）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 4 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 5 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 6 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 7 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 8 状态经过（中文值）与内存事件一致

════════════ 材料接入登记·场景二：规则互换变体 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 材料接入登记（规则二三互换），槽位表 {'目录': '样例材料', '文件总表': None, '材料清单': [], '登记进度': 0, '清单文件路径': None}，规则 {1: '文件总表为 None：列目录', 3: '材料清单里存在「是否纳入」为 None 的项：询问', 2: '登记进度小于文件总数：登记文件', 4: '清单文件路径为 None：生成清单文件'}，工具 {'ask': ['target', 'hint'], 'list_dir': [], 'register_file': ['index'], 'generate_manifest': []} 〔kernel.loop〕
  #2 数据变更：槽位「目录」 未填写(None) → '样例材料'，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「文件总表」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「材料清单」 未填写(None) → []，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「登记进度」 未填写(None) → 0，来源 初始化 〔kernel.update〕
  #6 数据变更：槽位「清单文件路径」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #7 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 8，追踪） ┈┈
  ·9 结果检查结论：已完成=False 〔kernel.loop〕
  ⇢ ·10 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
── 行动 1 ──
  #11 行动提出：工具 list_dir，参数 {}，提出者 selector，依据 (1, '文件总表为 None：列目录', None) 〔kernel.loop〕
  #12 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #13 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·14 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·15 行动执行调用：行动 1，工具 list_dir，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
    #16 行动状态变化：已成功，说明：列出 3 个文件，返回值 ['a.docx', 'b.pdf', 'c.xlsx'] 〔tool.list_dir〕
  ·17 行动执行调用：行动 1，工具 list_dir，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #18 数据变更：槽位「文件总表」 未填写(None) → ['a.docx', 'b.pdf', 'c.xlsx']，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 19，追踪） ┈┈
  ·20 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #21 行动提出：工具 register_file，参数 {'index': 0}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 0, '文件总数': 3}) 〔kernel.loop〕
  #22 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #23 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·24 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·25 行动执行调用：行动 2，工具 register_file，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
    #26 行动状态变化：已成功，说明：登记文件 a.docx，返回值 {'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None} 〔tool.register_file〕
  ·27 行动执行调用：行动 2，工具 register_file，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #28 数据变更：槽位「材料清单」 [] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}]，来源 2 〔kernel.update〕
  #29 数据变更：槽位「登记进度」 0 → 1，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 30，追踪） ┈┈
  ·31 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #32 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 0, '文件名': 'a.docx', '是否纳入': None}) 〔kernel.loop〕
  #33 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #34 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·35 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·36 行动执行调用：行动 3，工具 ask，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
  ↗ #37 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
    #38 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ·39 邮箱等待：收件箱，行动 3，阶段 begin，线程 kernel-T-intake-2 〔kernel.mailbox〕
  ⇢ ·40 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 6.304 毫秒 〔kernel.mailbox〕
  ⇢ #41 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
  ⇢ #42 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 3，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·43 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·44 邮箱等待：收件箱，行动 3，阶段 end，线程 kernel-T-intake-2，等待 0.987 毫秒 〔kernel.mailbox〕
  #45 消息取出：收件箱，类型 answer，发起方 user，收件人 3，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #46 行动状态变化：已成功，说明：回答到达，返回值 '是' 〔tool.ask〕
  ·47 行动执行调用：行动 3，工具 ask，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #48 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}]，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 49，追踪） ┈┈
  ·50 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 4 ──
  #51 行动提出：工具 register_file，参数 {'index': 1}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 1, '文件总数': 3}) 〔kernel.loop〕
  #52 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #53 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·54 执行控制结论：行动 4，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·55 行动执行调用：行动 4，工具 register_file，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
    #56 行动状态变化：已成功，说明：登记文件 b.pdf，返回值 {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None} 〔tool.register_file〕
  ·57 行动执行调用：行动 4，工具 register_file，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #58 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}]，来源 4 〔kernel.update〕
  #59 数据变更：槽位「登记进度」 1 → 2，来源 4 〔kernel.update〕
┈┈ 第 5 圈开始（事件 60，追踪） ┈┈
  ·61 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 5 ──
  #62 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 1, '文件名': 'b.pdf', '是否纳入': None}) 〔kernel.loop〕
  #63 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #64 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·65 执行控制结论：行动 5，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·66 行动执行调用：行动 5，工具 ask，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
  ↗ #67 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 b.pdf 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}}，到达序号 2 〔kernel.mailbox〕
    #68 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ·69 邮箱等待：收件箱，行动 5，阶段 begin，线程 kernel-T-intake-2 〔kernel.mailbox〕
  ⇢ ·70 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 4.584 毫秒 〔kernel.mailbox〕
  ⇢ #71 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 b.pdf 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}}，到达序号 2 〔kernel.mailbox〕
  ⇢ #72 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 5，内容 '否'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·73 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·74 邮箱等待：收件箱，行动 5，阶段 end，线程 kernel-T-intake-2，等待 0.889 毫秒 〔kernel.mailbox〕
  #75 消息取出：收件箱，类型 answer，发起方 user，收件人 5，内容 '否'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #76 行动状态变化：已成功，说明：回答到达，返回值 '否' 〔tool.ask〕
  ·77 行动执行调用：行动 5，工具 ask，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #78 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}]，来源 5 〔kernel.update〕
┈┈ 第 6 圈开始（事件 79，追踪） ┈┈
  ·80 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 6 ──
  #81 行动提出：工具 register_file，参数 {'index': 2}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 2, '文件总数': 3}) 〔kernel.loop〕
  #82 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #83 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·84 执行控制结论：行动 6，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·85 行动执行调用：行动 6，工具 register_file，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
    #86 行动状态变化：已成功，说明：登记文件 c.xlsx，返回值 {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None} 〔tool.register_file〕
  ·87 行动执行调用：行动 6，工具 register_file，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #88 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 6 〔kernel.update〕
  #89 数据变更：槽位「登记进度」 2 → 3，来源 6 〔kernel.update〕
┈┈ 第 7 圈开始（事件 90，追踪） ┈┈
  ·91 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 7 ──
  #92 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 2, '文件名': 'c.xlsx', '是否纳入': None}) 〔kernel.loop〕
  #93 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #94 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·95 执行控制结论：行动 7，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·96 行动执行调用：行动 7，工具 ask，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
  ↗ #97 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 c.xlsx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}}，到达序号 3 〔kernel.mailbox〕
    #98 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ·99 邮箱等待：收件箱，行动 7，阶段 begin，线程 kernel-T-intake-2 〔kernel.mailbox〕
  ⇢ ·100 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 4.269 毫秒 〔kernel.mailbox〕
  ⇢ #101 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 c.xlsx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}}，到达序号 3 〔kernel.mailbox〕
  ⇢ #102 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 7，内容 '是'，到达序号 3，回复问题 3 〔kernel.mailbox〕
  ⇢ ·103 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·104 邮箱等待：收件箱，行动 7，阶段 end，线程 kernel-T-intake-2，等待 0.863 毫秒 〔kernel.mailbox〕
  #105 消息取出：收件箱，类型 answer，发起方 user，收件人 7，内容 '是'，到达序号 3，回复问题 3 〔kernel.mailbox〕
    #106 行动状态变化：已成功，说明：回答到达，返回值 '是' 〔tool.ask〕
  ·107 行动执行调用：行动 7，工具 ask，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #108 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': '是'}]，来源 7 〔kernel.update〕
┈┈ 第 8 圈开始（事件 109，追踪） ┈┈
  ·110 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 8 ──
  #111 行动提出：工具 generate_manifest，参数 {}，提出者 selector，依据 (4, '清单文件路径为 None：生成清单文件', None) 〔kernel.loop〕
  #112 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #113 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·114 执行控制结论：行动 8，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·115 行动执行调用：行动 8，工具 generate_manifest，阶段 enter，线程 kernel-T-intake-2 〔kernel.loop〕
    #116 行动状态变化：已成功，说明：清单含 2 项，写到 样例材料/材料清单.txt，返回值 '材料清单（共 2 项）\n1. a.docx，docx，20480 字节，3 页\n2. c.xlsx，xlsx，10240 字节，1 页' 〔tool.generate_manifest〕
  ·117 行动执行调用：行动 8，工具 generate_manifest，阶段 return，线程 kernel-T-intake-2 〔kernel.loop〕
  #118 数据变更：槽位「清单文件路径」 未填写(None) → '样例材料/材料清单.txt'，来源 8 〔kernel.update〕
┈┈ 第 9 圈开始（事件 119，追踪） ┈┈
  ·120 结果检查结论：已完成=True 〔kernel.loop〕
── 结束 ──
  #121 任务状态变化：执行中 → 已完成 〔kernel.update〕
  #122 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  #123 任务结束：终态 已完成，原因：完成条件成立 〔kernel.loop〕
  ⇢ ·124 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 3.39 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 第一步目标一：kernel.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一：observe.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一：view.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一（附加）：内核文件里不出现材料接入登记的槽位名与工具名
  [通过] 内核线程正常返回、没有抛异常
  [通过] 第一步目标二：工具依次是 列目录、登记文件、询问、登记文件、询问、登记文件、询问、生成清单文件
  [通过] 第一步目标二：依据里的规则序号依次是 一、二、三、二、三、二、三、四（规则保留原序号，只改检查顺序）
  [通过] 第一步目标二：终态数据与场景一相同
  [通过] 目标二：行动 3 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 3 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 3 问题里的话逐字等于预期句「请确认是否把文件 a.docx 纳入项目。」
  [通过] 目标二：行动 3 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 3 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 3 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 3 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 3 的问题与回答，内容里的所属行动都是 3
  [通过] 目标二：行动 3 的数据变更新值按写入目标路径 [0, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 5 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 5 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 5 问题里的话逐字等于预期句「请确认是否把文件 b.pdf 纳入项目。」
  [通过] 目标二：行动 5 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 5 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 5 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 5 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 5 的问题与回答，内容里的所属行动都是 5
  [通过] 目标二：行动 5 的数据变更新值按写入目标路径 [1, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 7 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 7 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 7 问题里的话逐字等于预期句「请确认是否把文件 c.xlsx 纳入项目。」
  [通过] 目标二：行动 7 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 7 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 7 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 7 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 7 的问题与回答，内容里的所属行动都是 7
  [通过] 目标二：行动 7 的数据变更新值按写入目标路径 [2, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 行动 1（list_dir）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['文件总表']
  [通过] 行动 2（register_file）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['材料清单', '登记进度']
  [通过] 行动 4（register_file）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['材料清单', '登记进度']
  [通过] 行动 6（register_file）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['材料清单', '登记进度']
  [通过] 行动 8（generate_manifest）的状态经过是 已提出、已获准、已成功，变更只写可写槽位 ['清单文件路径']
  [通过] 目标三：从空数据起应用全部「数据变更」事件，得到的数据与任务终态数据一致
  [通过] 目标三：重放到行动 1 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 1 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 2 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 2 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 3 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 3 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 4 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 4 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 5 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 5 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 6 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 6 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 7 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 7 返回值与行动对象上的一致
  [通过] 目标三：重放到行动 8 的「行动提出」之前，得到的数据就是它的执行前实际数据
  [通过] 目标三：由事件重建的行动 8 返回值与行动对象上的一致
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 4 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 5 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 6 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 7 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 8 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：11 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：8 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：8 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：结束记录与「任务结束」事件的终态、原因一致
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 4 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 5 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 6 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 7 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 8 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 9 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1（工具 list_dir）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 3 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 4 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 4 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 4（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 5 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 5 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 5 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 5 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 6 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 6 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 6（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 7 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 7 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 7 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 7 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 8 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 8 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 8（工具 generate_manifest）不碰邮箱，没有邮箱等待
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是所用工具的 tool.<工具名>：['tool.ask', 'tool.generate_manifest', 'tool.list_dir', 'tool.register_file']）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：正常结束时，发件箱在「任务结束」之前关闭，「任务结束」仍是最后一个状态事件
  [通过] 邮箱关闭：本场景宿主没有关闭收件箱
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 4 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 5 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 6 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 7 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 8 状态经过（中文值）与内存事件一致

════════════ 材料接入登记·场景三：回答缺失 ════════════
── 初始化 ──
  ·1 任务开始：任务定义 材料接入登记，槽位表 {'目录': '样例材料', '文件总表': None, '材料清单': [], '登记进度': 0, '清单文件路径': None}，规则 {1: '文件总表为 None：列目录', 2: '登记进度小于文件总数：登记文件', 3: '材料清单里存在「是否纳入」为 None 的项：询问', 4: '清单文件路径为 None：生成清单文件'}，工具 {'ask': ['target', 'hint'], 'list_dir': [], 'register_file': ['index'], 'generate_manifest': []} 〔kernel.loop〕
  #2 数据变更：槽位「目录」 未填写(None) → '样例材料'，来源 初始化 〔kernel.update〕
  #3 数据变更：槽位「文件总表」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #4 数据变更：槽位「材料清单」 未填写(None) → []，来源 初始化 〔kernel.update〕
  #5 数据变更：槽位「登记进度」 未填写(None) → 0，来源 初始化 〔kernel.update〕
  #6 数据变更：槽位「清单文件路径」 未填写(None) → 未填写(None)，来源 初始化 〔kernel.update〕
  #7 任务状态变化：未开始 → 执行中 〔kernel.update〕
┈┈ 第 1 圈开始（事件 8，追踪） ┈┈
  ·9 结果检查结论：已完成=False 〔kernel.loop〕
  ⇢ ·10 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
── 行动 1 ──
  #11 行动提出：工具 list_dir，参数 {}，提出者 selector，依据 (1, '文件总表为 None：列目录', None) 〔kernel.loop〕
  #12 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #13 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·14 执行控制结论：行动 1，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·15 行动执行调用：行动 1，工具 list_dir，阶段 enter，线程 kernel-T-intake-3 〔kernel.loop〕
    #16 行动状态变化：已成功，说明：列出 3 个文件，返回值 ['a.docx', 'b.pdf', 'c.xlsx'] 〔tool.list_dir〕
  ·17 行动执行调用：行动 1，工具 list_dir，阶段 return，线程 kernel-T-intake-3 〔kernel.loop〕
  #18 数据变更：槽位「文件总表」 未填写(None) → ['a.docx', 'b.pdf', 'c.xlsx']，来源 1 〔kernel.update〕
┈┈ 第 2 圈开始（事件 19，追踪） ┈┈
  ·20 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 2 ──
  #21 行动提出：工具 register_file，参数 {'index': 0}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 0, '文件总数': 3}) 〔kernel.loop〕
  #22 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #23 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·24 执行控制结论：行动 2，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·25 行动执行调用：行动 2，工具 register_file，阶段 enter，线程 kernel-T-intake-3 〔kernel.loop〕
    #26 行动状态变化：已成功，说明：登记文件 a.docx，返回值 {'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None} 〔tool.register_file〕
  ·27 行动执行调用：行动 2，工具 register_file，阶段 return，线程 kernel-T-intake-3 〔kernel.loop〕
  #28 数据变更：槽位「材料清单」 [] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}]，来源 2 〔kernel.update〕
  #29 数据变更：槽位「登记进度」 0 → 1，来源 2 〔kernel.update〕
┈┈ 第 3 圈开始（事件 30，追踪） ┈┈
  ·31 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 3 ──
  #32 行动提出：工具 register_file，参数 {'index': 1}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 1, '文件总数': 3}) 〔kernel.loop〕
  #33 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #34 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·35 执行控制结论：行动 3，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·36 行动执行调用：行动 3，工具 register_file，阶段 enter，线程 kernel-T-intake-3 〔kernel.loop〕
    #37 行动状态变化：已成功，说明：登记文件 b.pdf，返回值 {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None} 〔tool.register_file〕
  ·38 行动执行调用：行动 3，工具 register_file，阶段 return，线程 kernel-T-intake-3 〔kernel.loop〕
  #39 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}]，来源 3 〔kernel.update〕
  #40 数据变更：槽位「登记进度」 1 → 2，来源 3 〔kernel.update〕
┈┈ 第 4 圈开始（事件 41，追踪） ┈┈
  ·42 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 4 ──
  #43 行动提出：工具 register_file，参数 {'index': 2}，提出者 selector，依据 (2, '登记进度小于文件总数：登记文件', {'登记进度': 2, '文件总数': 3}) 〔kernel.loop〕
  #44 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #45 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·46 执行控制结论：行动 4，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·47 行动执行调用：行动 4，工具 register_file，阶段 enter，线程 kernel-T-intake-3 〔kernel.loop〕
    #48 行动状态变化：已成功，说明：登记文件 c.xlsx，返回值 {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None} 〔tool.register_file〕
  ·49 行动执行调用：行动 4，工具 register_file，阶段 return，线程 kernel-T-intake-3 〔kernel.loop〕
  #50 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 4 〔kernel.update〕
  #51 数据变更：槽位「登记进度」 2 → 3，来源 4 〔kernel.update〕
┈┈ 第 5 圈开始（事件 52，追踪） ┈┈
  ·53 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 5 ──
  #54 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 0, '文件名': 'a.docx', '是否纳入': None}) 〔kernel.loop〕
  #55 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #56 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·57 执行控制结论：行动 5，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·58 行动执行调用：行动 5，工具 ask，阶段 enter，线程 kernel-T-intake-3 〔kernel.loop〕
  ↗ #59 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
    #60 行动状态变化：等待中，说明：已向使用者提问，问题 1 〔tool.ask〕
  ·61 邮箱等待：收件箱，行动 5，阶段 begin，线程 kernel-T-intake-3 〔kernel.mailbox〕
  ⇢ ·62 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 9.672 毫秒 〔kernel.mailbox〕
  ⇢ #63 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 a.docx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [0, '是否纳入']}, 'hint': {'file': 'a.docx'}}}，到达序号 1 〔kernel.mailbox〕
  ⇢ #64 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 5，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
  ⇢ ·65 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·66 邮箱等待：收件箱，行动 5，阶段 end，线程 kernel-T-intake-3，等待 0.947 毫秒 〔kernel.mailbox〕
  #67 消息取出：收件箱，类型 answer，发起方 user，收件人 5，内容 '是'，到达序号 1，回复问题 1 〔kernel.mailbox〕
    #68 行动状态变化：已成功，说明：回答到达，返回值 '是' 〔tool.ask〕
  ·69 行动执行调用：行动 5，工具 ask，阶段 return，线程 kernel-T-intake-3 〔kernel.loop〕
  #70 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': None}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 5 〔kernel.update〕
┈┈ 第 6 圈开始（事件 71，追踪） ┈┈
  ·72 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 6 ──
  #73 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 1, '文件名': 'b.pdf', '是否纳入': None}) 〔kernel.loop〕
  #74 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #75 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·76 执行控制结论：行动 6，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·77 行动执行调用：行动 6，工具 ask，阶段 enter，线程 kernel-T-intake-3 〔kernel.loop〕
  ↗ #78 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 b.pdf 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}}，到达序号 2 〔kernel.mailbox〕
    #79 行动状态变化：等待中，说明：已向使用者提问，问题 2 〔tool.ask〕
  ·80 邮箱等待：收件箱，行动 6，阶段 begin，线程 kernel-T-intake-3 〔kernel.mailbox〕
  ⇢ ·81 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.583 毫秒 〔kernel.mailbox〕
  ⇢ #82 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 b.pdf 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [1, '是否纳入']}, 'hint': {'file': 'b.pdf'}}}，到达序号 2 〔kernel.mailbox〕
  ⇢ #83 外部（user）· 消息放入：收件箱，类型 answer，发起方 user，收件人 6，内容 '否'，到达序号 2，回复问题 2 〔kernel.mailbox〕
  ⇢ ·84 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·85 邮箱等待：收件箱，行动 6，阶段 end，线程 kernel-T-intake-3，等待 0.861 毫秒 〔kernel.mailbox〕
  #86 消息取出：收件箱，类型 answer，发起方 user，收件人 6，内容 '否'，到达序号 2，回复问题 2 〔kernel.mailbox〕
    #87 行动状态变化：已成功，说明：回答到达，返回值 '否' 〔tool.ask〕
  ·88 行动执行调用：行动 6，工具 ask，阶段 return，线程 kernel-T-intake-3 〔kernel.loop〕
  #89 数据变更：槽位「材料清单」 [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': None}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}] → [{'文件名': 'a.docx', '类型': 'docx', '大小': 20480, '页数': 3, '是否纳入': '是'}, {'文件名': 'b.pdf', '类型': 'pdf', '大小': 51200, '页数': 5, '是否纳入': '否'}, {'文件名': 'c.xlsx', '类型': 'xlsx', '大小': 10240, '页数': 1, '是否纳入': None}]，来源 6 〔kernel.update〕
┈┈ 第 7 圈开始（事件 90，追踪） ┈┈
  ·91 结果检查结论：已完成=False 〔kernel.loop〕
── 行动 7 ──
  #92 行动提出：工具 ask，参数 {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}，提出者 selector，依据 (3, '材料清单里存在「是否纳入」为 None 的项：询问', {'序号': 2, '文件名': 'c.xlsx', '是否纳入': None}) 〔kernel.loop〕
  #93 行动状态变化：已提出，说明：行动选择 〔kernel.loop〕
  #94 行动状态变化：已获准，说明：本步恒允许 〔kernel.loop〕
  ·95 执行控制结论：行动 7，结论 已获准，核验：恒允许 〔kernel.loop〕
  ·96 行动执行调用：行动 7，工具 ask，阶段 enter，线程 kernel-T-intake-3 〔kernel.loop〕
  ↗ #97 向外部提问 · 消息放入：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 c.xlsx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}}，到达序号 3 〔kernel.mailbox〕
    #98 行动状态变化：等待中，说明：已向使用者提问，问题 3 〔tool.ask〕
  ·99 邮箱等待：收件箱，行动 7，阶段 begin，线程 kernel-T-intake-3 〔kernel.mailbox〕
  ⇢ ·100 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 2.999 毫秒 〔kernel.mailbox〕
  ⇢ #101 外部（读取发件箱的一方）· 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {'utterance': '请确认是否把文件 c.xlsx 纳入项目。', 'params': {'target': {'slot': '材料清单', 'path': [2, '是否纳入']}, 'hint': {'file': 'c.xlsx'}}}，到达序号 3 〔kernel.mailbox〕
  ⇢ #102 外部（host）· 邮箱关闭：收件箱，发起方 host，不会再有消息 〔kernel.mailbox〕
  ⇢ ·103 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 begin，线程 MainThread 〔kernel.mailbox〕
  ·104 邮箱等待：收件箱，行动 7，阶段 end，线程 kernel-T-intake-3，等待 0.797 毫秒 〔kernel.mailbox〕
    #105 行动状态变化：已失败，说明：没有可用的回答，返回值 None 〔tool.ask〕
  ·106 行动执行调用：行动 7，工具 ask，阶段 return，线程 kernel-T-intake-3 〔kernel.loop〕
── 结束 ──
  #107 邮箱关闭：发件箱，发起方 kernel.loop，不会再有消息 〔kernel.mailbox〕
  ⇢ ·108 外部（读取发件箱的一方）· 邮箱等待：发件箱，行动 None，阶段 end，线程 MainThread，等待 1.011 毫秒 〔kernel.mailbox〕
── 断言 ──
  [通过] 第一步目标一：kernel.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一：observe.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一：view.py 的 sha256 等于提交 9472ac5 里的值
  [通过] 第一步目标一（附加）：内核文件里不出现材料接入登记的槽位名与工具名
  [通过] 内核线程以内核错误结束
  [通过] 内核错误携带的是行动 7
  [通过] 恰好七个「行动提出」，工具依次是 列目录、登记文件×3、询问×3
  [通过] 目标二：行动 5 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 5 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 5 问题里的话逐字等于预期句「请确认是否把文件 a.docx 纳入项目。」
  [通过] 目标二：行动 5 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 5 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 5 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 5 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 5 的问题与回答，内容里的所属行动都是 5
  [通过] 目标二：行动 5 的数据变更新值按写入目标路径 [0, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 目标二：行动 6 的状态序列是 已提出、已获准、等待中、已成功
  [通过] 目标二：行动 6 恰有一条问题（发件箱放入），内容是 {话, 参数}，参数是行动参数原样，且在「等待中」之前
  [通过] 第一步目标三：行动 6 问题里的话逐字等于预期句「请确认是否把文件 b.pdf 纳入项目。」
  [通过] 目标二：行动 6 的「等待中」说明写明了问题的到达序号
  [通过] 目标二：行动 6 在收件箱恰有一条回答放入和一条取出
  [通过] 目标二：行动 6 的回答放入与取出都落在「等待中」与「已成功」之间，且放入在取出之前
  [通过] 目标二：行动 6 的回答的回复对象等于问题的到达序号
  [通过] 目标二：行动 6 的问题与回答，内容里的所属行动都是 6
  [通过] 目标二：行动 6 的数据变更新值按写入目标路径 [1, '是否纳入'] 取出的值 = 取出的回答 = 「已成功」事件的返回值；槽位是写入目标的槽位，除该路径外新旧值相同
  [通过] 行动 7 的状态序列是 已提出、已获准、等待中、已失败，最后一条说明是「没有可用的回答」
  [通过] 行动 7 在「已失败」之前发出过问题（发件箱放入），没有收件箱的「消息取出」
  [通过] 最后一个状态事件是内核关闭发件箱
  [通过] 任务没有结束：没有「任务结束」事件，任务状态仍是执行中；c.xlsx 的是否纳入仍为 None
  [通过] 失败的行动在行动表里：行动表的编号是 1 到 7，行动 7 的状态是已失败
  [通过] 内存收集器里全部事件（两类一起）序号从 1 起连续、没有跳号、顺序等于序号顺序
  [通过] 全部事件（两类一起）的任务标识都等于宿主分配的标识
  [通过] 宿主拿得到任务对象
  [通过] 目标五：初始化变更组与各行动变更组按序拼接，与全部「数据变更」事件的四个键逐位相等
  [通过] 目标五：行动 1 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 2 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 3 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 4 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 5 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 6 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：行动 7 的当前状态等于它最后一个「行动状态变化」事件的状态
  [通过] 目标五：任务状态等于最后一个「任务状态变化」事件的状态
  [通过] 目标五：9 次状态更新的前后，实际任务数据都等于用事件重建的数据
  [通过] 终态数据：返回的任务对象上的真实数据等于从全部事件重放出的数据
  [通过] 目标五：每个行动的编号、工具名、参数、提出者、依据与「行动提出」事件逐字段相等
  [通过] 行动表：键就是各行动自己的编号，顺序等于登记顺序（即「行动提出」事件的顺序）
  [通过] 登记即放入行动表：7 次登记返回时，返回的编号已在行动表里、表大小加一、状态是已提出、「行动提出」事件已为该编号发出
  [通过] 行动选择只返回候选、不写任何东西：7 次调用前后，任务数据、下一个行动编号、行动表、事件条数都没有变化
  [通过] 投影：没有结束记录时事件流里也没有「任务结束」
  [通过] 行动 1 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 2 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 3 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 4 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 5 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 6 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 行动 7 的「行动提出」事件在「已提出」状态变化之前发出
  [通过] 问题与回答跨线程往返：发件箱的放入出自内核线程、取出出自主线程；收件箱的放入出自主线程、取出出自内核线程
  [通过] 谁引起：每个事件被判为外部，当且仅当它出自内核线程之外（按线程做事实核对）
  [通过] 追踪：类别由事件名决定，八种状态事件名的类别都是 state，其余都是 trace
  [通过] 追踪：内核一侧发出的第一个事件是「任务开始」（宿主在发件箱上的等待不算），槽位表、规则清单、工具清单、任务定义名与任务定义和工具表一致
  [通过] 追踪：恰好 7 个「一圈开始」，圈序号从 1 起连续
  [通过] 追踪：每圈一个「结果检查结论」，只有完成的最后一圈为真
  [通过] 追踪：没有「任务定义错误」
  [通过] 追踪：行动 1 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 1 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 1（工具 list_dir）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 2 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 2 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 2（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 3 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 3 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 3（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 4 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 4 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 4（工具 register_file）不碰邮箱，没有邮箱等待
  [通过] 追踪：行动 5 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 5 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 5 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 5 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 6 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 6 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 6 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 6 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 追踪：行动 7 有一条「执行控制结论」，结论已获准、核验恒允许
  [通过] 追踪：行动 7 的「行动执行调用」依次是 enter、return，线程是内核线程
  [通过] 追踪：行动 7 在收件箱上的「邮箱等待」恰好两条，依次是 begin、end，end 带非负毫秒数，线程是内核线程
  [通过] 追踪：行动 7 的邮箱等待夹在执行调用的 enter 与 return 之间，取出（若有）在等待结束之后
  [通过] 记录方：每个事件的记录方都符合裁定（状态更新 kernel.update，邮箱 kernel.mailbox，工具实现记的状态 tool.<工具名>，其余 kernel.loop）
  [通过] 记录方：同一个事件名「行动状态变化」来自不同记录方（已提出、已获准是 kernel.loop，其余是所用工具的 tool.<工具名>：['tool.ask', 'tool.list_dir', 'tool.register_file']）
  [通过] 发起方：收件箱的放入与关闭，发起方都是 host 或 user，且都出自内核线程之外
  [通过] 发起方：发件箱里的问题，发起方是 tool.<工具名>，类型是 question，收件人是 user，出自内核线程
  [通过] 发起方：inbox 的每条「消息取出」与它对应的「消息放入」发起方相同，回答都由 user 放入
  [通过] 发起方：outbox 的每条「消息取出」与它对应的「消息放入」发起方相同
  [通过] 邮箱关闭：两个箱最终是否关闭，与事件流里有没有对应的「邮箱关闭」事件一致
  [通过] 邮箱关闭：发件箱恰由内核关闭一次，发起方 kernel.loop，出自内核线程
  [通过] 邮箱关闭：收件箱恰有一条「邮箱关闭」，发起方 host，落在行动 7 的「等待中」与「已失败」之间
  [通过] 邮箱关闭：出错时，发件箱在行动 7「已失败」之后由内核关闭
  [通过] 邮箱等待：宿主在发件箱上的等待都出自主线程、不带行动编号，begin 与 end 成对
  [通过] 目标六：文件订阅者写出了本场景的 JSONL 文件
  [通过] 目标六：文件里的事件条数、序号、类别、事件名与内存收集器逐条一致
  [通过] 目标六：用文件重放出的终态数据等于运行时任务对象上的真实数据
  [通过] 目标六：用文件得到的行动 1 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 2 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 3 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 4 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 5 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 6 状态经过（中文值）与内存事件一致
  [通过] 目标六：用文件得到的行动 7 状态经过（中文值）与内存事件一致

════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 146 条断言，通过 146 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 141 条断言，通过 141 条，失败 0 条
材料接入登记·场景三：回答缺失：共 108 条断言，通过 108 条，失败 0 条
结论：全部断言通过
```

### 第六项：查看页核对

对材料接入登记场景一生成查看页 `runs/T-intake-1.html`，在浏览器里用脚本逐块取出内容核对，并截图 `runs/T-intake-1.screenshot-1280.png`。检查脚本 `s1_item6.js`：

```js
const q = s => document.querySelector(s);
const overview = q('#overview').innerText;
const loops = [...document.querySelectorAll('.loop')].map(l => l.querySelector('h3').innerText.replace(/\s+/g, ' '));
const slotRows = [...document.querySelectorAll('.slot-row')].map(r => {
  const cells = [...r.querySelectorAll('td')];
  return { slot: r.querySelector('th').innerText, final: cells[0].innerText.slice(0, 80), hits: cells.slice(1).filter(td => td.classList.contains('hit')).map(td => td.innerText.replace(/\s+/g, ' ').slice(0, 90)) };
});
const cards = [...document.querySelectorAll('.card')].filter(c => /询问|ask/.test(c.innerText)).map(c => {
  const dts = [...c.querySelectorAll('dt')]; const dd = name => { const i = dts.findIndex(d => d.innerText === name); return i < 0 ? null : c.querySelectorAll('dd')[i].innerText.replace(/\s+/g, ' '); };
  return { card: c.querySelector('h3').innerText, tool: dd('提出'), question: dd('发出的问题'), answer: dd('取到的回答'), change: (dd('变更') || '').slice(0, 120) };
});
JSON.stringify({ overview_lines: overview.split('\n').filter(l => l.trim()), loops, slotRows, ask_cards: cards.filter(x => /ask/.test(x.tool || '')) }, null, 1)
```

整理后的输出：

```
== 任务概览
   任务概览
   任务标识	T-intake-1
   任务定义名	材料接入登记
   槽位表（5 个）	
   目录 初始值 “样例材料”
   文件总表 初始值 None
   材料清单 初始值 []
   登记进度 初始值 0
   清单文件路径 初始值 None
   选择规则清单（4 条）	
   规则 1：文件总表为 None：列目录
   规则 2：登记进度小于文件总数：登记文件
   规则 3：材料清单里存在「是否纳入」为 None 的项：询问
   规则 4：清单文件路径为 None：生成清单文件
   工具清单（4 个）	
   ask 参数名 ["target", "hint"]
   list_dir 参数名 []
   register_file 参数名 ["index"]
   generate_manifest 参数名 []
   终态与原因	已完成，原因：完成条件成立
   事件数	共 124 个：状态事件 67 个，追踪事件 57 个
   循环	共 9 圈，提出行动 8 个
   记录方	kernel.loop 发布 68 个；kernel.mailbox 发布 27 个；kernel.update 发布 18 个；tool.list_dir 发布 1 个；tool.register_file 发布 3 个；tool.ask 发布 6 个；tool.generate_manifest 发布 1 个
   外部引起的状态事件（6 个）	
   #62 读取发件箱的一方 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {"utterance": "请确认是否把文件 a.docx 纳入项目。", "params": {"target": {"slot": "材料清单", "path": [0, "是否纳入"]}, "hint": {"file": "a.docx"}}}，到达序号 1
   #64 user 消息放入：收件箱，类型 answer，发起方 user，收件人 5，内容 “是”，到达序号 1，回复问题 1
   #82 读取发件箱的一方 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {"utterance": "请确认是否把文件 b.pdf 纳入项目。", "params": {"target": {"slot": "材料清单", "path": [1, "是否纳入"]}, "hint": {"file": "b.pdf"}}}，到达序号 2
   #83 user 消息放入：收件箱，类型 answer，发起方 user，收件人 6，内容 “否”，到达序号 2，回复问题 2
   #101 读取发件箱的一方 消息取出：发件箱，类型 question，发起方 tool.ask，收件人 user，内容 {"utterance": "请确认是否把文件 c.xlsx 纳入项目。", "params": {"target": {"slot": "材料清单", "path": [2, "是否纳入"]}, "hint": {"file": "c.xlsx"}}}，到达序号 3
   #102 user 消息放入：收件箱，类型 answer，发起方 user，收件人 7，内容 “是”，到达序号 3，回复问题 3
   另有 8 个外部引起的追踪事件（读取发件箱时的邮箱等待），见时间线右栏。
== 循环时间线（每圈标题）
   开始 第一圈之前：任务开始与初始化
   第 1 圈 行动 1：list_dir {}
   第 2 圈 行动 2：register_file {"index": 0}
   第 3 圈 行动 3：register_file {"index": 1}
   第 4 圈 行动 4：register_file {"index": 2}
   第 5 圈 行动 5：ask {"target": {"slot": "材料清单", "path": [0, "是否纳入"]}, "hint": {"file": "a.docx"}}
   第 6 圈 行动 6：ask {"target": {"slot": "材料清单", "path": [1, "是否纳入"]}, "hint": {"file": "b.pdf"}}
   第 7 圈 行动 7：ask {"target": {"slot": "材料清单", "path": [2, "是否纳入"]}, "hint": {"file": "c.xlsx"}}
   第 8 圈 行动 8：generate_manifest {}
   第 9 圈 结果检查为真，任务结束
== 槽位轨迹
   {'slot': '目录', 'final': '“样例材料”', 'hits': ['“样例材料” 初始化']}
   {'slot': '文件总表', 'final': '["a.docx", "b.pdf", "c.xlsx"]', 'hits': ['None 初始化', '["a.docx", "b.pdf", "c.xlsx"] 行动 1']}
   {'slot': '材料清单', 'final': '[{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": "是"}, {"文件名": "b.', 'hits': ['[] 初始化', '[{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": null}] 行动 2', '[{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": null}, {"文件名": "b.pdf", "类型', '[{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": null}, {"文件名": "b.pdf", "类型', '[{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": "是"}, {"文件名": "b.pdf", "类型"', '[{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": "是"}, {"文件名": "b.pdf", "类型"', '[{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": "是"}, {"文件名": "b.pdf", "类型"']}
   {'slot': '登记进度', 'final': '3', 'hits': ['0 初始化', '1 行动 2', '2 行动 3', '3 行动 4']}
   {'slot': '清单文件路径', 'final': '“样例材料/材料清单.txt”', 'hits': ['None 初始化', '“样例材料/材料清单.txt” 行动 8']}
== 询问行动的卡片
   {'card': '行动 5', 'tool': '#54 工具 ask，参数 {"target": {"slot": "材料清单", "path": [0, "是否纳入"]}, "hint": {"file": "a.docx"}}，提出者 selector', 'question': '#59 问题 1：内容 {"utterance": "请确认是否把文件 a.docx 纳入项目。", "params": {"target": {"slot": "材料清单", "path": [0, "是否纳入"]}, "hint": {"file": "a.docx"}}}，发起方 tool.ask，收件人 user', 'answer': '#67 “是”（发起方 user，到达序号 1，回复问题 1）', 'change': '#70 「材料清单」 [{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": null}, {"文件名": "b.pdf", "类型": "pdf", "大小": 512'}
   {'card': '行动 6', 'tool': '#73 工具 ask，参数 {"target": {"slot": "材料清单", "path": [1, "是否纳入"]}, "hint": {"file": "b.pdf"}}，提出者 selector', 'question': '#78 问题 2：内容 {"utterance": "请确认是否把文件 b.pdf 纳入项目。", "params": {"target": {"slot": "材料清单", "path": [1, "是否纳入"]}, "hint": {"file": "b.pdf"}}}，发起方 tool.ask，收件人 user', 'answer': '#86 “否”（发起方 user，到达序号 2，回复问题 2）', 'change': '#89 「材料清单」 [{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": "是"}, {"文件名": "b.pdf", "类型": "pdf", "大小": 5120'}
   {'card': '行动 7', 'tool': '#92 工具 ask，参数 {"target": {"slot": "材料清单", "path": [2, "是否纳入"]}, "hint": {"file": "c.xlsx"}}，提出者 selector', 'question': '#97 问题 3：内容 {"utterance": "请确认是否把文件 c.xlsx 纳入项目。", "params": {"target": {"slot": "材料清单", "path": [2, "是否纳入"]}, "hint": {"file": "c.xlsx"}}}，发起方 tool.ask，收件人 user', 'answer': '#105 “是”（发起方 user，到达序号 3，回复问题 3）', 'change': '#108 「材料清单」 [{"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3, "是否纳入": "是"}, {"文件名": "b.pdf", "类型": "pdf", "大小": 512'}
```

另外检查了生成清单文件的返回值显示，以及按槽位「材料清单」过滤的结果：返回值在行动卡片里显示为一行文字，换行没有保留；过滤后行动卡片剩下行动 2 到 7，时间线显示 70 / 124 个事件，槽位轨迹只剩「材料清单」一行。400 宽度下页面宽度等于视口宽度。核对得出的不足见 S4 节。

## S4 查看页对第二个任务的不足

以下都是在浏览器里打开 `runs/T-intake-1.html` 逐块核对后看到的现象。按规定只记录，没有改 view.py。

先说能正确显示的部分：
- 任务概览列出了 5 个槽位与初始值、4 条规则、4 个工具，以及终态「已完成，原因：完成条件成立」；
- 循环时间线正好 9 圈，前 8 圈各提一个行动，第 9 圈结果检查为真后结束；
- 槽位轨迹有 5 行，每个槽位在哪个事件由哪个行动改变都能对上；
- 三个询问的行动卡片都显示了发出的问题、取到的回答（带回复对象）和变更；
- 按槽位「材料清单」过滤，行动卡片剩下行动 2 到 7，时间线剩 70 个事件；
- 400 宽度下页面宽度等于视口宽度。

不足如下：

1. 话没有单独成行。发出的问题在时间线、行动卡片和概览里都显示为一整段 JSON：`{"utterance": "请确认是否把文件 a.docx 纳入项目。", "params": {...}}`。话在开头能读到，但夹在参数中间。这是 S2 第 1 条裁定预期的结果，留给下一步改观测模块。
2. 列表类槽位的值整段显示。槽位轨迹里「材料清单」一行，每个变化格都是整张表的 JSON；表只有一项时还能读，三项之后单元格很长，看不出这次变的是哪一项。步骤文档 4.1 节设想的「项数加变化项摘要」，view.py 现在没有这个行为。
3. 行动卡片里的变更同样是整张旧表 → 整张新表。例如行动 6 只是把 b.pdf 的是否纳入从 null 改成「否」，卡片上却要在两段三项 JSON 里自己比对。这是「变更粒度选甲方案（整槽位）」的直接后果，路径式变更记在步骤文档第 7 节。
4. 圈标题直接显示工具名和参数 JSON，例如「第 5 圈 行动 5：ask {"target": {"slot": "材料清单", "path": [0, "是否纳入"]}, "hint": {"file": "a.docx"}}」，不如直接显示话来得易读。
5. 依据里「命中时的数据值」原样显示为 JSON，例如 `{"序号": 0, "文件名": "a.docx", "是否纳入": null}`；规则序号与条件文字能读，但 null 这类写法对非技术读者不友好。
6. 多行的返回值被压成一行。生成清单文件的返回值是三行文本，在行动卡片里显示为一行，换行没有保留；控制台打印时则显示为带 `\n` 的字符串。
7. 与该槽位有关、但不写它的行动，不会出现在槽位过滤结果里。例如按「材料清单」过滤时，读了材料清单去生成清单的行动 8 被隐藏了，因为查看器只看写入与参数值，不看读取。

## S5 本步的偏差与疑问

下文「步骤文档第 N 行」指《第一步：第二个任务》的行号（按我开工时读到的版本），「文件:行号」指当前代码。

### 与步骤文档的出入

1. 验证目标二（步骤文档第 152 行）写「场景一里恰好九个行动」，但同一句列出的工具只有八个（列目录、登记文件三次、询问三次、生成清单文件），4.3 节的圈序表（第 98 到 108 行）也是九圈、第九圈不提行动。所以实际是九圈八个行动。验证脚本断言的是「恰好八个行动」，并在描述里写明「九圈里最后一圈结果检查为真，不提行动」（verify.py:788 之后的场景一）。
2. 规则互换变体里，依据的规则序号依次是一、二、三、二、三、二、三、四。步骤文档没有写变体的规则序号，这是按 S2 自定做法「保留原序号，只改检查顺序」得到的。

### 第零步断言的适配（逐条列出，均未放宽检查的意思）

3. 宿主应答：原来按问题内容里的槽位查答案表，现在按问题参数里的写入目标（槽位, 路径元组）查；出差申请单的答案表转换为空路径的写法（verify.py:130、verify.py:135、verify.py:237）。
4. 验证目标二「问题内容是行动参数原样」：改为「内容的键依次是 utterance、params，且 params 等于行动参数原样」；另加一条逐字比对话的断言，预期句写死在验证脚本里，不调用话语生成（verify.py:148、verify.py:521、verify.py:529）。
5. 验证目标二「数据变更新值 = 取出的回答 = 返回值」：改为「新值按写入目标路径取出的值 = 取出的回答 = 返回值，且槽位是写入目标的槽位；路径不空时，旧值按该路径写入回答后等于新值」。出差申请单是空路径，与原断言等价（verify.py:546）。
6. 追踪形状「每个行动恰好两条邮箱等待」：改为只对询问行动检查；非询问行动改为检查「没有邮箱等待，也不碰邮箱」（verify.py:411）。
7. 记录方「行动状态变化来自 kernel.loop 与 tool.ask」：改为「来自 kernel.loop 与所用工具各自的 tool.<工具名>」（verify.py:449）。
8. 「任务开始」事件里的工具清单：原来写死 {"ask": ["slot"]}，改为与本场景实际建的工具表比对（verify.py:226、verify.py:391）。
9. 场景一、场景四「参数依次是……」：参数形状从 {"slot": 槽位} 变成 {"target": {...}, "hint": {}}，按新形状比对（verify.py:617、verify.py:709）。这一条在开工前列出的六处之外，是写代码时发现的，性质相同，写在这里。

### 我自行决定的地方

10. 话语生成在找不到模板，或者提示里缺少模板要的占位符时，返回「请提供「槽位」[路径]的值。提示：……。」这样的可读拼写，不报错（dialogue.py:23、dialogue.py:45）。
11. `set_at` 在路径为空时返回新值的深拷贝，而不是新值本身，免得变更条与调用方共用同一个对象（tools.py:48）。
12. 询问工具先把槽位旧值深拷贝一份作为变更的旧值，再用 `set_at` 算出新值，保证变更里的旧值与数据里的现值相等，但不是同一个对象（tools.py:63）。
13. 领域工具遇到样例表里没有的目录或序号时，把行动记为「已失败」并写明原因，由循环抛内核错误；这条路径本步没有跑到（tools.py:122、tools.py:135）。
14. 清单文本格式定为：第一行「材料清单（共 n 项）」，之后每行「序号. 文件名，类型，大小 字节，页数 页」（tools.py:156）。
15. 规则三命中时，依据里「命中时的数据值」给出 {序号, 文件名, 是否纳入}；规则二给出 {登记进度, 文件总数}；规则一、四给出 None（task_intake.py:61、task_intake.py:69）。
16. 规则互换变体的 RULES 字典按检查顺序排列（1、3、2、4），所以查看页任务概览里的规则清单也按这个顺序显示（task_intake.py:100）。
17. 工具的「可写槽位」用 frozenset 表示；Tool 数据类里这个字段的类型注释也从 tuple 改成了 frozenset，并补了两个预留字段的说明注释。Tool 定义在 tools.py，不在内核三个文件里。
18. 验证脚本另加了三类检查：
    - 材料接入登记的非询问行动，状态经过是已提出、已获准、已成功，并且变更只写该工具声明的可写槽位。这是验证脚本自己的检查，执行控制仍然不读这个字段。
    - 内核文件里不出现材料接入登记的槽位名与工具名。
    - 场景三终态里 c.xlsx 的是否纳入仍为 None（verify.py:749、verify.py:762）。

### 隐患与遗留

19. 任务定义 SLOTS 里「材料清单」的初始值是一个空列表，内核初始化时直接用这个列表对象，没有复制；变体也共用同一个列表。本步所有工具都先深拷贝再改，所以没有出错，但只要有工具原地修改它，就会影响下一次运行。已按裁定记进步骤文档第 7 节，作为下一步的内核修改。
20. 生成清单文件认的是回答原文「是」（tools.py:119）。回答理解本步原样返回，所以领域工具要认识一个自然语言词；将来回答理解把「是」转成布尔值后，这里要跟着改。
21. 工具的前置条件函数本步无人调用，只在第三项检查脚本里手动调过列目录和登记文件的两个。
22. 工具表按任务各建一张，是因为执行上下文里没有任务定义，只能用 partial 绑进询问工具。这是权宜做法，已记进步骤文档第 7 节。

### 我没做的事

23. 我没有填写步骤文档 6.1 节的验收记录，没有修改步骤文档，没有提交或推送代码。
24. 步骤文档验证目标三的人工一问（负责人看查看页，确认三个询问能读到人话）要由负责人自己完成。

---

# 第二步：观测台（2026-09-14）

本章记录第二步的实现过程，以及步骤文档《第二步：观测台》第 5 节五个工作项的检查输出。第一步已提交为 185c6d7；上面「第一步」一章引用的代码行号对应提交 185c6d7 的代码，本步改动后没有重新对齐。

## T1 结论

- 目标是把「一次运行一个静态网页」改成观测台，目标已达成：
  - 运行文件按「任务标识_开始时刻」命名，永不覆盖；
  - `python -m tod_kernel.observe serve` 起一个本地服务，提供三个接口；
  - 一个自包含页面 `observatory.html`，含运行索引、运行详情、对比三个视图；
  - view.py 已删除。
- 零差异成立：kernel.py、tools.py、dialogue.py、task_travel.py、task_intake.py 五个文件的 sha256 与提交 185c6d7 相同，验证脚本里写死了这五个值。本步改动的文件只有 observe.py、verify.py、`__init__.py`（只改说明文字），新增 observatory.html，删除 view.py。
- 运行 `python -m tod_kernel.verify`，七个场景加末尾的「第二步：观测台」一次全部通过，退出码为 0；连续运行二十次，每次都全部通过。
  - 断言条数：出差申请单四个场景 100、37、84、71 条；材料接入登记三个场景 148、143、110 条；「第二步：观测台」25 条。
  - 材料接入登记三个场景各比第一步多 2 条，原因是哈希断言从三个文件改成了五个文件。
- 为确认新断言确实能发现问题，我在临时副本里故意改坏过三处，验证脚本三次都报告了失败：
  - 文件订阅者改回固定文件名、覆盖写入；
  - 去掉文件名白名单；
  - 出错结束的终态规则只认任务定义错误。
- 页面在浏览器里按 1280 与 400 两种宽度检查过，索引、详情、对比三个视图的页面宽度都等于视口宽度。截图保存在会话临时目录，没有入库。
- 步骤文档要求的两项人工判定（七条不足逐条打勾、两组对比的观察）要由负责人完成；步骤文档 6.1 节的验收记录没有填，代码没有提交。

## T2 开工前的疑问与裁定

开工前我向编制简报的会话提了五个问题，另附四处自定做法；对方全部同意，并已改好文档：

1. 终态分三种情况：有「任务结束」取它；没有「任务结束」但内核一侧关闭了发件箱或有「任务定义错误」，记「内核错误」；两者都没有，记「未结束」。原先文档一处写「未结束」、一处要求场景三记「内核错误」，现在两处统一。
2. 「连跑两遍、文件数等于两倍场景数」放在临时空目录里做；runs/ 作为长期存储不数文件。runs/ 里前两步留下的 .html 与截图删除，7 份旧命名的 .jsonl 保留，用来验证旧文件照样能读。
3. 圈标题显示工具名 ask（事件里没有中文工具名，工具定义又不许改）；给工具加中文显示名记为遗留。
4. 哈希断言覆盖 kernel.py、tools.py、dialogue.py、task_travel.py、task_intake.py 五个文件；允许改 `__init__.py` 的说明文字。
5. 文件订阅者按「序号回绕」认出同一任务标识的新一次运行；同一毫秒定名冲突时加 _2、_3；始终没有「任务开始」的运行不写文件。

四处自定做法：
- 验证脚本起服务用端口 0，由系统分配空闲端口，请求走本机回环地址；
- 旧命名文件的开始时刻取文件修改时间，并在摘要里标出来源；
- 路径穿越按原始请求路径检查；
- 没有「任务开始」的缓存事件不写文件。

## T3 逐项记录

检查脚本放在会话临时目录，没有进仓库；运行方式都是在仓根下执行 `PYTHONPATH=. python3 <临时目录>/<脚本>`，浏览器检查用 agent-browser 执行页面脚本。

### 第一项：运行文件不覆盖

文件订阅者 `FileWriter` 改写（observe.py:110）：
- 收到「任务开始」时取挂钟时间定名，定名前到达的事件先缓存，定名后按原序写出（observe.py:153）；
- 序号不大于上次见过的序号时，认定新一次运行开始（observe.py:134）；
- 以独占方式创建文件，同名时加 _2、_3（observe.py:157）。

验证脚本在运行结束后才取文件路径，因为定名要等「任务开始」。

这一项有一处和计划不同：文件订阅者一改，observe.py 的哈希就变了，第一步写死的「三个内核文件哈希」断言会立刻失败。所以哈希断言范围在这一项就改成了五个模块（verify.py:162），没有等到第五项。

步骤文档要求能看到：连跑两次验证后，runs/ 下有十四个新文件，文件名里的时刻各不相同，内容各自完整。实际结果与要求相符。两遍验证的汇总都是全部通过。检查代码 `s2_item1.py`：

```python
import collections, re
from pathlib import Path
from tod_kernel.observe import read_events

runs = Path("runs")
new = sorted(p for p in runs.glob("*.jsonl") if re.search(r"_\d{8}T\d{9}(_\d+)?\.jsonl$", p.name))
old = sorted(p for p in runs.glob("*.jsonl") if p not in new)
print(f"带开始时刻的新文件 {len(new)} 个，旧命名文件 {len(old)} 个，其他文件 {len([p for p in runs.iterdir() if p.suffix != '.jsonl'])} 个")
for p in new:
    events = read_events(p)
    names = [e.name for e in events]
    print(f"  {p.name}  {len(events)} 行，序号 {events[0].seq}..{events[-1].seq} 连续={[e.seq for e in events] == list(range(1, len(events) + 1))}，"
          f"含任务开始={'TASK_STARTED' in names}，最后一个事件 {names[-1]}")
by_task = collections.defaultdict(list)
for p in new:
    by_task[p.name.rsplit("_", 1)[0]].append(p.name)
print("每个任务标识的文件数:", {k: len(v) for k, v in by_task.items()})
print("文件名两两不同:", len({p.name for p in new}) == len(new))
```

实际输出（runs/ 里另有 7 份旧命名文件，保留）：

```
带开始时刻的新文件 14 个，旧命名文件 7 个，其他文件 0 个
  T-intake-1_20260914T042000013.jsonl  124 行，序号 1..124 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-intake-1_20260914T042000221.jsonl  124 行，序号 1..124 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-intake-2_20260914T042000038.jsonl  124 行，序号 1..124 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-intake-2_20260914T042000243.jsonl  124 行，序号 1..124 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-intake-3_20260914T042000061.jsonl  108 行，序号 1..108 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-intake-3_20260914T042000267.jsonl  108 行，序号 1..108 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-1_20260914T041959970.jsonl  69 行，序号 1..69 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-1_20260914T042000181.jsonl  69 行，序号 1..69 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-2_20260914T041959988.jsonl  12 行，序号 1..12 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-2_20260914T042000200.jsonl  12 行，序号 1..12 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-3_20260914T041959991.jsonl  63 行，序号 1..63 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-3_20260914T042000203.jsonl  63 行，序号 1..63 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-4_20260914T042000001.jsonl  50 行，序号 1..50 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
  T-scenario-4_20260914T042000213.jsonl  50 行，序号 1..50 连续=True，含任务开始=True，最后一个事件 MAILBOX_WAIT
每个任务标识的文件数: {'T-intake-1': 2, 'T-intake-2': 2, 'T-intake-3': 2, 'T-scenario-1': 2, 'T-scenario-2': 2, 'T-scenario-3': 2, 'T-scenario-4': 2}
文件名两两不同: True
```

### 第二项：运行摘要与索引

新增 `RunSummary`、`summarize`、`list_runs`（observe.py:184、observe.py:200、observe.py:249）：
- 圈数取「一圈开始」事件数，行动数取「行动提出」事件数，耗时取首尾事件单调时刻之差；
- 终态按 T2 第 1 条的三种情况判断；
- 开始时刻优先从文件名解析，旧命名文件取文件修改时间。

步骤文档要求能看到：对 runs/ 调用 list_runs 打出一张表，七次运行的任务定义名、终态、圈数、行动数与预期一致；场景三那一行终态是「内核错误」。实际结果与要求相符，两个场景三的原因都是「没有可用的回答」。7 份旧命名文件也都读出来了。检查代码 `s2_item2.py`：

```python
from tod_kernel.observe import list_runs

EXPECTED = {  # 任务标识 → (任务定义名, 终态, 圈数, 行动数)，来自第零步、第一步文档
    "T-scenario-1": ("出差申请单", "已完成", 4, 3),
    "T-scenario-2": ("出差申请单（三项预填）", "已完成", 1, 0),
    "T-scenario-3": ("出差申请单", "内核错误", 3, 3),
    "T-scenario-4": ("出差申请单（日期预填）", "已完成", 3, 2),
    "T-intake-1": ("材料接入登记", "已完成", 9, 8),
    "T-intake-2": ("材料接入登记（规则二三互换）", "已完成", 9, 8),
    "T-intake-3": ("材料接入登记", "内核错误", 7, 7),
}
runs = list_runs("runs")
print(f"共 {len(runs)} 条，按开始时刻倒序：")
print("文件 | 任务定义名 | 开始时刻（来源） | 终态 | 原因 | 圈数 | 行动数 | 事件数 | 耗时毫秒")
for r in runs:
    print(f"{r.file} | {r.task_def_name} | {r.started_at}（{r.started_at_source}） | {r.final_status} | {r.reason} | {r.loops} | {r.actions} | {r.events} | {r.duration_ms}")
latest = {}
for r in runs:
    if r.started_at_source == "文件名":
        latest.setdefault(r.task_id, r)
print("--- 最近一遍七个运行与预期表比对 ---")
for task_id, expected in EXPECTED.items():
    r = latest[task_id]
    actual = (r.task_def_name, r.final_status, r.loops, r.actions)
    print(f"{task_id}: {'一致' if actual == expected else '不一致'} {actual}")
```

实际输出：

```
共 21 条，按开始时刻倒序：
文件 | 任务定义名 | 开始时刻（来源） | 终态 | 原因 | 圈数 | 行动数 | 事件数 | 耗时毫秒
T-intake-3_20260914T042000267.jsonl | 材料接入登记 | 2026-09-14 04:20:00.267（文件名） | 内核错误 | 没有可用的回答 | 7 | 7 | 108 | 14.3
T-intake-2_20260914T042000243.jsonl | 材料接入登记（规则二三互换） | 2026-09-14 04:20:00.243（文件名） | 已完成 | 完成条件成立 | 9 | 8 | 124 | 16.28
T-intake-1_20260914T042000221.jsonl | 材料接入登记 | 2026-09-14 04:20:00.221（文件名） | 已完成 | 完成条件成立 | 9 | 8 | 124 | 15.663
T-scenario-4_20260914T042000213.jsonl | 出差申请单（日期预填） | 2026-09-14 04:20:00.213（文件名） | 已完成 | 完成条件成立 | 3 | 2 | 50 | 6.684
T-scenario-3_20260914T042000203.jsonl | 出差申请单 | 2026-09-14 04:20:00.203（文件名） | 内核错误 | 没有可用的回答 | 3 | 3 | 63 | 8.795
T-scenario-2_20260914T042000200.jsonl | 出差申请单（三项预填） | 2026-09-14 04:20:00.200（文件名） | 已完成 | 完成条件成立 | 1 | 0 | 12 | 1.776
T-scenario-1_20260914T042000181.jsonl | 出差申请单 | 2026-09-14 04:20:00.181（文件名） | 已完成 | 完成条件成立 | 4 | 3 | 69 | 9.191
T-intake-3_20260914T042000061.jsonl | 材料接入登记 | 2026-09-14 04:20:00.061（文件名） | 内核错误 | 没有可用的回答 | 7 | 7 | 108 | 14.378
T-intake-2_20260914T042000038.jsonl | 材料接入登记（规则二三互换） | 2026-09-14 04:20:00.038（文件名） | 已完成 | 完成条件成立 | 9 | 8 | 124 | 16.427
T-intake-1_20260914T042000013.jsonl | 材料接入登记 | 2026-09-14 04:20:00.013（文件名） | 已完成 | 完成条件成立 | 9 | 8 | 124 | 17.847
T-scenario-4_20260914T042000001.jsonl | 出差申请单（日期预填） | 2026-09-14 04:20:00.001（文件名） | 已完成 | 完成条件成立 | 3 | 2 | 50 | 8.843
T-scenario-3_20260914T041959991.jsonl | 出差申请单 | 2026-09-14 04:19:59.991（文件名） | 内核错误 | 没有可用的回答 | 3 | 3 | 63 | 8.528
T-scenario-2_20260914T041959988.jsonl | 出差申请单（三项预填） | 2026-09-14 04:19:59.988（文件名） | 已完成 | 完成条件成立 | 1 | 0 | 12 | 1.766
T-scenario-1_20260914T041959970.jsonl | 出差申请单 | 2026-09-14 04:19:59.970（文件名） | 已完成 | 完成条件成立 | 4 | 3 | 69 | 9.237
T-intake-3.jsonl | 材料接入登记 | 2026-09-14 03:55:21.932（文件修改时间） | 内核错误 | 没有可用的回答 | 7 | 7 | 108 | 19.191
T-intake-2.jsonl | 材料接入登记（规则二三互换） | 2026-09-14 03:55:21.906（文件修改时间） | 已完成 | 完成条件成立 | 9 | 8 | 124 | 21.884
T-intake-1.jsonl | 材料接入登记 | 2026-09-14 03:55:21.877（文件修改时间） | 已完成 | 完成条件成立 | 9 | 8 | 124 | 21.395
T-scenario-4.jsonl | 出差申请单（日期预填） | 2026-09-14 03:55:21.853（文件修改时间） | 已完成 | 完成条件成立 | 3 | 2 | 50 | 9.187
T-scenario-3.jsonl | 出差申请单 | 2026-09-14 03:55:21.841（文件修改时间） | 内核错误 | 没有可用的回答 | 3 | 3 | 63 | 13.385
T-scenario-2.jsonl | 出差申请单（三项预填） | 2026-09-14 03:55:21.826（文件修改时间） | 已完成 | 完成条件成立 | 1 | 0 | 12 | 2.801
T-scenario-1.jsonl | 出差申请单 | 2026-09-14 03:55:21.808（文件修改时间） | 已完成 | 完成条件成立 | 4 | 3 | 69 | 12.641
--- 最近一遍七个运行与预期表比对 ---
T-scenario-1: 一致 ('出差申请单', '已完成', 4, 3)
T-scenario-2: 一致 ('出差申请单（三项预填）', '已完成', 1, 0)
T-scenario-3: 一致 ('出差申请单', '内核错误', 3, 3)
T-scenario-4: 一致 ('出差申请单（日期预填）', '已完成', 3, 2)
T-intake-1: 一致 ('材料接入登记', '已完成', 9, 8)
T-intake-2: 一致 ('材料接入登记（规则二三互换）', '已完成', 9, 8)
T-intake-3: 一致 ('材料接入登记', '内核错误', 7, 7)
```

### 第三项：本地服务

新增 `make_server`、`serve` 与命令入口 `python -m tod_kernel.observe serve [--dir runs] [--port 8765]`（observe.py:274、observe.py:342、observe.py:483）：
- 服务监听 0.0.0.0；
- `GET /` 返回页面，`GET /api/runs` 返回索引，`GET /api/runs/<文件名>` 返回该运行的全部事件；
- 文件名白名单只接受 runs 目录里现有的 .jsonl 文件名本身（observe.py:264），判断用原始请求路径（observe.py:294）。

本机 8765 端口已被占用（见 T5 第 16 条），所以这一项用 `--port 8766` 起服务，再用 curl 请求。

步骤文档要求能看到：`/api/runs` 返回索引 JSON，`/api/runs/<某文件名>` 返回事件数组，`/api/runs/../x` 返回 404。实际结果与要求相符。当时页面文件还没建，所以 `GET /` 返回 404，这是预期的。实际输出：

```
--- 启动时打印：
观测台已启动，监听 0.0.0.0:8766，运行目录 <仓根>/runs（贴入报告时把本机绝对路径改写成了这种写法）
  http://10.0.0.1:8766/
--- curl http://10.0.0.1:8766/api/runs（只显示条数与第一条）：
21 条
{"file": "T-intake-3_20260914T042000267.jsonl", "task_def_name": "材料接入登记", "task_id": "T-intake-3", "started_at": "2026-09-14 04:20:00.267", "final_status": "内核错误", "reason": "没有可用的回答", "loops": 7, "actions": 7, "events": 108, "duration_ms": 14.3, "started_at_source": "文件名"}
--- curl http://10.0.0.1:8766/api/runs/T-intake-3_20260914T042000267.jsonl（事件数与文件行数）：
接口返回事件数 108，文件行数 108
--- curl --path-as-is http://10.0.0.1:8766/api/runs/../x：
HTTP 404
--- curl --path-as-is http://10.0.0.1:8766/api/runs/..%2Fverify.py 与 http://10.0.0.1:8766/api/runs/nope.jsonl：
HTTP 404
HTTP 404
--- curl http://10.0.0.1:8766/（页面文件第四项才建）：
{"error": "观测台页面文件不存在"}
HTTP 404
```

### 第四项：观测台页面，索引与详情两个视图

新增 `tod_kernel/observatory.html`，页面的 HTML、CSS、JS 都在这一个文件里，不引外部资源：
- 索引视图：可按任务定义名、终态筛选，按开始时刻排序，勾选两行可以对比（observatory.html:400）；
- 详情视图：四块用 JS 重写（observatory.html:246 起），按 4.3 节修了七条不足，修正情况见 T4 节；
- 地址栏用 `#run=<文件名>`、`#compare=<a>,<b>` 记下当前视图（observatory.html:664）。

view.py 已删除，`__init__.py` 的说明文字同步更新。

步骤文档要求能看到：圈标题是「第 5 圈 · ask · 请确认是否把文件 a.docx 纳入项目。」，槽位轨迹里材料清单显示「第 1 项 是否纳入：None → 是」，并截 1280 与 400 两种宽度的图。实际结果与要求相符。开发中在浏览器里修过三处排版：初始化那一格重复显示项数、外部栏的描述被挤成一字一行、槽位轨迹横向滚动后槽位名跟着移出视野。

截图在会话临时目录，没有入库：索引、运行详情、两组对比，各有 1280 与 400 两种宽度，四个视图在两种宽度下的页面宽度都等于视口宽度。检查脚本 `s2_item4.js`（打开材料接入登记场景一的详情页后执行）：

```js
const titles = [...document.querySelectorAll('.loop h3')].map(h => h.innerText);
const slotRow = [...document.querySelectorAll('.slot-row')].find(r => r.querySelector('th').innerText === '材料清单');
const cells = slotRow ? [...slotRow.querySelectorAll('td.hit')].map(td => td.innerText.replace(/\s+/g, ' ')) : [];
const q = [...document.querySelectorAll('li.ev')].find(li => li.querySelector('.ask-tag'));
const card5 = [...document.querySelectorAll('.card')].find(c => c.dataset.action === '5');
const dd = (card, name) => { const dts = [...card.querySelectorAll('dt')]; const i = dts.findIndex(d => d.innerText === name); return card.querySelectorAll('dd')[i].innerText.replace(/\s+/g, ' '); };
const card8 = [...document.querySelectorAll('.card')].find(c => c.dataset.action === '8');
JSON.stringify({
  圈标题: titles,
  槽位轨迹_材料清单各次变化: cells,
  第一处发件箱问题这一行: q ? q.innerText.replace(/\s+/g, ' ') : null,
  行动5卡片_依据: dd(card5, '依据'), 行动5卡片_发出的问题: dd(card5, '发出的问题'), 行动5卡片_变更: dd(card5, '变更'),
  行动8卡片_返回值用pre显示: Boolean(card8.querySelector('pre')), 行动8返回值原文: card8.querySelector('pre') ? card8.querySelector('pre').textContent : null,
}, null, 1)
```

整理后的输出：

```
{
 "圈标题": [
  "开始 第一圈之前：任务开始与初始化",
  "第 1 圈 · list_dir · 无参数",
  "第 2 圈 · register_file · index=0",
  "第 3 圈 · register_file · index=1",
  "第 4 圈 · register_file · index=2",
  "第 5 圈 · ask · 请确认是否把文件 a.docx 纳入项目。",
  "第 6 圈 · ask · 请确认是否把文件 b.pdf 纳入项目。",
  "第 7 圈 · ask · 请确认是否把文件 c.xlsx 纳入项目。",
  "第 8 圈 · generate_manifest · 无参数",
  "第 9 圈 · 结果检查为真，任务结束"
 ],
 "槽位轨迹_材料清单各次变化": [
  "0 项 初始化",
  "新增第 1 项：文件名=a.docx，类型=docx，大小=20480，页数=3，是否纳入=None 共 1 项 行动 2",
  "新增第 2 项：文件名=b.pdf，类型=pdf，大小=51200，页数=5，是否纳入=None 共 2 项 行动 3",
  "新增第 3 项：文件名=c.xlsx，类型=xlsx，大小=10240，页数=1，是否纳入=None 共 3 项 行动 4",
  "第 1 项 是否纳入：None → 是 共 3 项 行动 5",
  "第 2 项 是否纳入：None → 否 共 3 项 行动 6",
  "第 3 项 是否纳入：None → 是 共 3 项 行动 7"
 ],
 "第一处发件箱问题这一行": "向外部提问 · 发件箱 #59 +6.9ms 状态 消息放入 请确认是否把文件 a.docx 纳入项目。 发件箱，类型 question，发起方 tool.ask，收件人 user，到达序号 1 参数 kernel.mailbox",
 "行动5卡片_依据": "规则 3：材料清单里存在「是否纳入」为 None 的项：询问；本次值 序号=0，文件名=a.docx，是否纳入=None",
 "行动5卡片_发出的问题": "#59 请确认是否把文件 a.docx 纳入项目。 发件箱，类型 question，发起方 tool.ask，收件人 user，到达序号 1 参数",
 "行动5卡片_变更": "#70 「材料清单」 第 1 项 是否纳入：None → 是 整值",
 "行动8卡片_返回值用pre显示": true,
 "行动8返回值原文": "材料清单（共 2 项）\n1. a.docx，docx，20480 字节，3 页\n2. c.xlsx，xlsx，10240 字节，1 页"
}
按槽位「材料清单」过滤后的行动卡片与时间线事件数: "2、3、4、5、6、7；时间线显示 70 / 124 个事件"
```

### 第五项：对比视图与验证

页面加入对比视图（observatory.html:604）：
- 两次运行的行动按位置对齐，逐格比较工具名、话或参数、行动终态、变更的槽位；
- 终态数据逐槽位并排，不同的槽位写出差异。

验证脚本末尾加入「第二步：观测台」（verify.py:939）：
- 零差异哈希；
- 在临时空目录把七个场景各跑两遍，检查文件数、文件名、摘要与预期表（verify.py:915）；
- 起服务请求三个接口和六个非法路径，然后关服务；
- 旧命名文件照样能读；
- 同一个文件订阅者序号回绕另起文件。

步骤文档要求能看到：对比场景一与规则互换变体时，第 2 到 7 行工具名交替标黄、终态数据全灰；七个场景验证全过。实际标黄的是第 3、6 行的工具名，以及第 3 到 6 行的话或参数，与文档不同，原因见 T5 第 1 条；终态数据全灰与文档一致；验证全过。浏览器里执行的对比检查脚本 `s2_item5.js`：

```js
const rows = [...document.querySelectorAll('#compare-actions tbody tr')].map(tr => {
  const tds = [...tr.querySelectorAll('td')]; const f = name => tds.filter(td => td.dataset.field === name);
  const mark = cls => cls === 'diff' ? '黄' : cls === 'absent' ? '黄（无）' : '灰';
  return `行 ${tr.dataset.row}：工具 ${f('tool').map(td => td.innerText + '[' + mark(td.className) + ']').join(' | ')}；话或参数 ${f('what').map(td => mark(td.className)).join('/')}；终态 ${f('status').map(td => td.innerText + '[' + mark(td.className) + ']').join(' | ')}`;
});
const data = [...document.querySelectorAll('#compare-data tbody tr')].map(tr => `${tr.dataset.slot}：${tr.dataset.differs === 'true' ? '黄，' + tr.querySelectorAll('td')[2].innerText : '灰，相同'}`);
JSON.stringify({标题: [...document.querySelectorAll('h2')].slice(1).map(h => h.innerText), 行动序列: rows, 终态数据: data}, null, 1)
```

两组对比的输出：

```
=== #compare=T-intake-1_20260914T043210290.jsonl,T-intake-2_20260914T043210313.jsonl
{
 "标题": [
  "行动序列 按位置对齐；4 / 8 行有不同，黄色是不同处，灰色是相同处，「（无）」表示该侧没有这一行",
  "终态数据 逐槽位并排；0 / 5 个槽位不同"
 ],
 "行动序列": [
  "行 1：工具 list_dir[灰] | list_dir[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 2：工具 register_file[灰] | register_file[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 3：工具 register_file[黄] | ask[黄]；话或参数 黄/黄；终态 已成功[灰] | 已成功[灰]",
  "行 4：工具 register_file[灰] | register_file[灰]；话或参数 黄/黄；终态 已成功[灰] | 已成功[灰]",
  "行 5：工具 ask[灰] | ask[灰]；话或参数 黄/黄；终态 已成功[灰] | 已成功[灰]",
  "行 6：工具 ask[黄] | register_file[黄]；话或参数 黄/黄；终态 已成功[灰] | 已成功[灰]",
  "行 7：工具 ask[灰] | ask[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 8：工具 generate_manifest[灰] | generate_manifest[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]"
 ],
 "终态数据": [
  "目录：灰，相同",
  "文件总表：灰，相同",
  "材料清单：灰，相同",
  "登记进度：灰，相同",
  "清单文件路径：灰，相同"
 ]
}
=== #compare=T-intake-1_20260914T043210290.jsonl,T-intake-3_20260914T043210335.jsonl
{
 "标题": [
  "行动序列 按位置对齐；2 / 8 行有不同，黄色是不同处，灰色是相同处，「（无）」表示该侧没有这一行",
  "终态数据 逐槽位并排；2 / 5 个槽位不同"
 ],
 "行动序列": [
  "行 1：工具 list_dir[灰] | list_dir[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 2：工具 register_file[灰] | register_file[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 3：工具 register_file[灰] | register_file[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 4：工具 register_file[灰] | register_file[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 5：工具 ask[灰] | ask[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 6：工具 ask[灰] | ask[灰]；话或参数 灰/灰；终态 已成功[灰] | 已成功[灰]",
  "行 7：工具 ask[灰] | ask[灰]；话或参数 灰/灰；终态 已成功[黄] | 已失败[黄]",
  "行 8：工具 generate_manifest[黄] | （无）[黄（无）]；话或参数 黄/黄（无）；终态 已成功[黄] | （无）[黄（无）]"
 ],
 "终态数据": [
  "目录：灰，相同",
  "文件总表：灰，相同",
  "材料清单：黄，第 3 项 是否纳入：是 → None",
  "登记进度：灰，相同",
  "清单文件路径：黄，样例材料/材料清单.txt → None"
 ]
}
```

故意改坏一：文件订阅者改回固定文件名 `<任务标识>.jsonl`、覆盖写入。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 148 条断言，通过 148 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 143 条断言，通过 143 条，失败 0 条
材料接入登记·场景三：回答缺失：共 110 条断言，通过 110 条，失败 0 条
第二步：观测台：共 25 条断言，通过 21 条，失败 4 条
  失败：第二步目标一：临时空目录里连跑两遍后恰有 14 个文件
  失败：第二步目标一：文件名两两不同，且都是「任务标识_开始时刻.jsonl」
  失败：第二步目标一：每个任务标识恰有两份文件
  失败：第二步：同一个文件订阅者对同一任务标识，序号回绕后另起一份文件；两份都完整且按原序写出缓存的事件；没有「任务开始」的第三次运行不写文件
结论：有断言失败
```

故意改坏二：去掉文件名白名单，只要文件存在就返回。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 148 条断言，通过 148 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 143 条断言，通过 143 条，失败 0 条
材料接入登记·场景三：回答缺失：共 110 条断言，通过 110 条，失败 0 条
第二步：观测台：共 25 条断言，通过 22 条，失败 3 条
  失败：第二步目标二：路径穿越或不存在的文件 /api/runs/../old/T-intake-1.jsonl 返回 404
  失败：第二步目标二：路径穿越或不存在的文件 /api/runs/..%2Fold%2FT-intake-1.jsonl 返回 404
  失败：第二步目标二：路径穿越或不存在的文件 /api/runs/%2E%2E%2Fold%2FT-intake-1.jsonl 返回 404
结论：有断言失败
```

故意改坏三：出错结束的终态规则只认「任务定义错误」，不认内核关闭发件箱。验证脚本汇总：

```
════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 148 条断言，通过 148 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 143 条断言，通过 143 条，失败 0 条
材料接入登记·场景三：回答缺失：共 110 条断言，通过 110 条，失败 0 条
第二步：观测台：共 25 条断言，通过 24 条，失败 1 条
  失败：第二步目标一：每份文件的摘要（任务定义名、终态、圈数、行动数）与预期表逐行相等
结论：有断言失败
```

最终一次运行 `python -m tod_kernel.verify` 的输出。七个场景的部分与第一步格式相同，这里只贴「第二步：观测台」一段和汇总：

```
════════════ 第二步：观测台 ════════════
── 断言 ──
  [通过] 零差异：kernel.py 的 sha256 等于提交 185c6d7 里的值
  [通过] 零差异：tools.py 的 sha256 等于提交 185c6d7 里的值
  [通过] 零差异：dialogue.py 的 sha256 等于提交 185c6d7 里的值
  [通过] 零差异：task_travel.py 的 sha256 等于提交 185c6d7 里的值
  [通过] 零差异：task_intake.py 的 sha256 等于提交 185c6d7 里的值
  [通过] 第一步目标一（附加）：内核文件里不出现材料接入登记的槽位名与工具名
  [通过] 第二步目标一：临时空目录里连跑两遍后恰有 14 个文件
  [通过] 第二步目标一：文件名两两不同，且都是「任务标识_开始时刻.jsonl」
  [通过] 第二步目标一：每份文件的摘要（任务定义名、终态、圈数、行动数）与预期表逐行相等
  [通过] 第二步目标一：每个任务标识恰有两份文件
  [通过] 第二步目标一：每份文件的事件序号从 1 起连续，且含「任务开始」
  [通过] 第二步目标二：仓库里不再有 view.py
  [通过] 第二步目标二：运行目录里只有 .jsonl，没有 .html
  [通过] 第二步：旧命名文件照样能读，开始时刻取文件修改时间并标出来源
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET / HTTP/1.1" 200 -
  [通过] 第二步目标二：GET / 返回 200 和观测台页面
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs HTTP/1.1" 200 -
  [通过] 第二步目标一：GET /api/runs 返回 200，行数等于文件数 14
  [通过] 第二步目标一：索引按开始时刻倒序
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-intake-1_20260914T043210384.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-intake-1_20260914T043210444.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-intake-2_20260914T043210396.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-intake-2_20260914T043210458.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-intake-3_20260914T043210408.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-intake-3_20260914T043210471.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-1_20260914T043210361.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-1_20260914T043210419.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-2_20260914T043210368.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-2_20260914T043210427.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-3_20260914T043210370.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-3_20260914T043210431.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-4_20260914T043210378.jsonl HTTP/1.1" 200 -
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/T-scenario-4_20260914T043210438.jsonl HTTP/1.1" 200 -
  [通过] 第二步目标二：每份文件经 GET /api/runs/<文件名> 返回的事件数等于文件行数
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/../old/T-intake-1.jsonl HTTP/1.1" 404 -
  [通过] 第二步目标二：路径穿越或不存在的文件 /api/runs/../old/T-intake-1.jsonl 返回 404
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/..%2Fold%2FT-intake-1.jsonl HTTP/1.1" 404 -
  [通过] 第二步目标二：路径穿越或不存在的文件 /api/runs/..%2Fold%2FT-intake-1.jsonl 返回 404
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/%2E%2E%2Fold%2FT-intake-1.jsonl HTTP/1.1" 404 -
  [通过] 第二步目标二：路径穿越或不存在的文件 /api/runs/%2E%2E%2Fold%2FT-intake-1.jsonl 返回 404
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/../x HTTP/1.1" 404 -
  [通过] 第二步目标二：路径穿越或不存在的文件 /api/runs/../x 返回 404
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/nope.jsonl HTTP/1.1" 404 -
  [通过] 第二步目标二：路径穿越或不存在的文件 /api/runs/nope.jsonl 返回 404
127.0.0.1 - - [14/Sep/2026 04:32:10] "GET /api/runs/ HTTP/1.1" 404 -
  [通过] 第二步目标二：路径穿越或不存在的文件 /api/runs/ 返回 404
  [通过] 第二步：同一个文件订阅者对同一任务标识，序号回绕后另起一份文件；两份都完整且按原序写出缓存的事件；没有「任务开始」的第三次运行不写文件

════════════ 汇总 ════════════
场景一：正常流程：共 100 条断言，通过 100 条，失败 0 条
场景二：初始即完成：共 37 条断言，通过 37 条，失败 0 条
场景三：回答缺失：共 84 条断言，通过 84 条，失败 0 条
场景四：部分预填：共 71 条断言，通过 71 条，失败 0 条
材料接入登记·场景一：正常流程：共 148 条断言，通过 148 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 143 条断言，通过 143 条，失败 0 条
材料接入登记·场景三：回答缺失：共 110 条断言，通过 110 条，失败 0 条
第二步：观测台：共 25 条断言，通过 25 条，失败 0 条
结论：全部断言通过
```

## T4 第一步七条不足的修正情况

按步骤文档 4.3 节逐条对照，都以材料接入登记场景一的详情页为准。人工逐条打勾要由负责人做，这里只写页面上实际是什么样子。

1. 话单独成行：已修。发件箱的「消息放入」这一行先用粗体单独显示话，下面是箱名、类型、发起方等，参数折叠在「参数」里，点开才显示。行动卡片的「发出的问题」同样如此。
2. 列表槽位看出变的是哪一项：已修。槽位轨迹里材料清单的七次变化依次显示为：「0 项」「新增第 1 项：文件名=a.docx，……」（新增第 2、3 项同理），以及「第 1 项 是否纳入：None → 是」「第 2 项 是否纳入：None → 否」「第 3 项 是否纳入：None → 是」。每格另标「共 n 项」；最终值一列显示「3 项」，整值折叠。
3. 行动卡片的变更只显示差异：已修。例如行动 5 的变更显示「「材料清单」 第 1 项 是否纳入：None → 是」，整张旧表与新表折叠在「整值」里。
4. 圈标题：已修。形如「第 5 圈 · ask · 请确认是否把文件 a.docx 纳入项目。」，没有话的行动显示参数摘要，例如「第 2 圈 · register_file · index=0」。
5. 依据：已修。显示为「规则 3：材料清单里存在「是否纳入」为 None 的项：询问；本次值 序号=0，文件名=a.docx，是否纳入=None」。
6. 多行返回值：已修。生成清单文件的返回值用等宽字体、保留换行显示。
7. 槽位过滤：按 4.3 节的定义修了，但步骤文档第一步记下的那个例子仍然不会出现在过滤结果里，这一点要说清楚：
   - 现在「与该槽位有关」包括两类行动：写过这个槽位的，以及参数里任何位置提到这个槽位名的。比如询问行动的写入目标嵌套在 target.slot 里，以前靠数据变更才被带上，现在靠参数本身也能匹配到。
   - 但第一步的例子是「生成清单文件」：它读了材料清单却没有写，参数里也没有提到。事件里不记录「读了哪些槽位」，所以按「材料清单」过滤时，行动 8 仍然看不到。过滤结果是：行动卡片剩行动 2 到 7，时间线剩 70 / 124 个事件。要覆盖这种情况，得让事件记下工具读了哪些槽位，这属于内核或工具的改动，写进 T5 第 13 条遗留。

## T5 本步的偏差与疑问

下文「步骤文档第 N 行」指《第二步：观测台》的行号（按我开工时读到的版本），「文件:行号」指当前代码。

### 与步骤文档的出入

1. 验证目标三写对比场景一与规则互换变体时，「页面标出第 2 到 7 行工具名不同」（步骤文档第 114 行）；第五项的「做完能看到」写「第 2 到 7 行工具名交替标黄」（步骤文档第 101 行）。实际只有第 3、6 行工具名不同：
   - 场景一的顺序是 列目录、登记、登记、登记、询问、询问、询问、生成清单；
   - 变体的顺序是 列目录、登记、询问、登记、询问、登记、询问、生成清单；
   - 第 4、5 行两边工具名相同，但参数或话不同，所以页面上不同的行是第 3 到 6 行；第 2、7 行两边完全相同。
   页面标出的就是这个实际情况，终态数据五个槽位全部标灰（相同），与文档一致。
2. 同一处又写对比场景一与回答缺失场景时，「第 7 行起右侧为空」。实际第 7 行两边都是询问 c.xlsx，只是左边已成功、右边已失败，终态这一格标黄；右侧从第 8 行起才为空。终态数据里「材料清单第三项」与「清单文件路径」不同，与文档一致。
3. 第四项「做完能看到」写圈标题是「第 5 圈 · 询问 · ……」，已按 T2 第 3 条裁定显示为 ask。

### 我自行决定的地方

4. `make_server(directory, host, port)` 是名字对照表之外新增的函数：它只建服务、不启动，给验证脚本起服务、关服务用；`serve` 在它之上加打印地址与持续运行（observe.py:274、observe.py:342）。
5. 启动时打印的地址，先取本机默认出口的 IPv4 地址，再补上主机名解析出的地址，去掉回环地址（observe.py:319）。本机只打印出一个地址 http://10.0.0.1:端口/。
6. 服务的访问日志照常打到标准错误，所以验证脚本输出里会夹着一行行请求记录，贴在 T3 的完整输出里。
7. 摘要里的耗时取文件里首尾两个事件的单调时刻之差。宿主读发件箱的最后一次「邮箱等待」结束通常排在「任务结束」之后，所以耗时略大于内核实际运行时间（observe.py:200）。
8. 摘要按开始时刻倒序排列，开始时刻相同时按文件名倒序；旧命名文件的开始时刻取文件修改时间，在索引页上以「*」标出（observe.py:249）。
9. 页面的对比视图比较四样东西：工具名、「话或参数」、行动终态、变更的槽位；只要有一样不同，这一行就算不同。每一格单独标色：不同是黄，相同是灰，一侧没有这一行则标「（无）」并标黄（observatory.html:604）。
10. 页面在详情视图里给每个事件加了「相对开始的毫秒数」一列，这是步骤文档 4.1 节「页面显示相对开始的毫秒数」的做法。
11. 在 400 宽度下，索引表放在自带横向滚动的容器里，右侧的终态、圈数等列要横向滑动才能看到；整页不会横向滚动。
12. 验证脚本在「第二步：观测台」里另加了两类检查：
    - 同一个文件订阅者对同一任务标识，序号回绕后另起一份文件、缓存按原序写出、没有「任务开始」时不写文件；
    - 旧命名文件照样能读。
    第一条是把一份真实运行文件的事件重新编号后喂给同一个订阅者两遍，而不是真的在同一进程里跑两次任务（verify.py:939）。

### 本轮出现过的问题

13. 第一次写路径穿越断言时，几个非法请求指向的文件本来就不存在，就算服务不做任何检查也会返回 404，断言证明不了拦截真的起作用。我改成请求运行目录之外一份真实存在的文件（../old/T-intake-1.jsonl 及其两种编码写法），再用「去掉白名单」的改坏副本确认：这三条断言在副本里会失败。
14. 「文件订阅者改回覆盖写入」这次改坏试验第一次运行时，验证脚本自己崩溃了：它按新文件名去找文件，找不到就抛了异常，而不是报告断言失败。我把挑选文件的地方改成按摘要里的任务标识挑，找不到时记为断言失败；再跑那个改坏副本，得到 4 条断言失败。
15. 第四项开发中途，我顺手执行了一次 `git rm --cached tod_kernel/view.py`，把删除放进了暂存区；随即用 `git restore --staged` 撤销，暂存区恢复原状，没有提交。
16. 本机 8765 端口已被一个看不到进程信息的程序占用，我没有动它。第三、四、五项检查时，观测台都起在 8766 端口（用 `--port 8766`）。负责人走查时如果 8765 仍被占用，也需要加 `--port`。

### 遗留

17. 事件不记录工具读取了哪些槽位，所以槽位过滤看不到「只读不写、参数也没提到」的行动（见 T4 第 7 条）。
18. 工具没有中文显示名，圈标题与对比视图显示的是工具名 ask、list_dir 等。
19. 对比只按位置对齐，规则顺序不同时，会把本来对应的行动错开比较，例如场景一与规则互换变体的第 3 到 6 行。
20. runs/ 目录只增不删。本步连跑二十次验证后，runs/ 下已有 182 份文件，索引页每次请求都要全部扫描一遍；目前仍然很快，文件多到扫不动时再加缓存。

### 我没做的事

21. 我没有填写步骤文档 6.1 节的验收记录，没有修改步骤文档，没有提交或推送代码。
22. 验证目标二、三的两项人工判定要由负责人完成：打开观测台逐条确认七条不足的修正，并做两组对比。

---

# 第三步：任务定义数据化（2026-09-14）

本章是第三步的最终实施报告，替换此前两次暂停期间写的中间记录。代码没有提交。

## T1 结论

2026-09-15 的修订批次改了步骤说明、依据说明、告知异常的「游标」参数、观测台运行路径，并按走查意见改了加载器。修订后的结论与数字以 T6 为准；本节以下是 2026-09-14 的原始结论。T5 的 tools.py 差异已换成修订后的全文。

- 验证脚本 `python -m tod_kernel.verify` 全部通过，退出码 0；连续跑三遍结果相同。
- 十六个场景全部通过：原有七个场景、四个异常场景、五个加载错误场景。另有两组不算场景的检查也全部通过：第二步观测台一组、同一份定义连跑两次一组。共 1202 条断言，失败 0 条。
- 七个既有场景改为经加载器从数据文件取任务定义。task_travel.py、task_intake.py 已删除。
- 七个既有场景的行动序列（工具名、参数实际值、依据序号）与第二步提交 01de6ab 前最后一次全量运行留下的七份运行文件逐位相等。**这条断言只在本机成立**：对照文件在仓根 runs/ 目录下，runs/ 不入版本库，换一台机器运行时这条断言判失败。
- kernel.py 相对 01de6ab 共两处改动：`new_task` 初始变更组的值深拷贝；候选带游标新值，行动成功时写入。差异全文见 T5。
- tools.py 相对 01de6ab 有三类改动：拆出静态工具表、前置条件改为三元组、新增「告知异常」工具。差异全文见 T5。
- dialogue.py、observe.py 与 01de6ab 逐字节相同，由验证脚本的哈希断言核对。
- observatory.html 只删掉 `main{...}` 里的 `max-width:1400px`，差异一行。
- 观测台原因栏：异常场景乙、丙两次运行在 `/api/runs` 里的原因字段分别是「使用者主动终止」「任务无法继续，使用者确认终止」，见 T3 第五项。页面上的显示仍需负责人看一眼。

## T2 开工前的疑问与裁定

开工前编制会话对十一条疑问做了裁定；实施途中，用户又先后裁定了游标方案、初始输入，以及游标方案与既有断言的冲突。以上全部已写进步骤文档，这里只列落到代码上的要点。

**开工前的十一条：**

1. 哈希断言在第一项开工时就改为 dialogue.py、observe.py 对 01de6ab。
2. 逐位比对写死 16:59:54 那一组七个运行文件，文件缺失判失败；动手前核对这一组与 04:37 那一组一致。
3. 「登记一个问一个」数据文件里的名字写「材料接入登记（登记一个问一个）」，验证脚本加载时覆盖回「材料接入登记（规则二三互换）」。
4. 步骤组内一轮零候选、且「重复直到」不成立时，直接构造告知异常候选，不回组首。
5. 「最多」求出 null 时跳过整个组；写成字面量时必须是非负整数。
6. 告知异常消息的类型是 question，内容是话加参数；话由工具自己拼，句式固定，数字用阿拉伯数字。
7. 五个参数的形状照提议（后随游标方案调整，见下）。
8. 回答不在三个选项内时记已失败，说明「回答不是可选措施之一：<原文>」；返回值分别是「重做」「主动终止」「被动终止」。
9. 行动记录投影算作内核第二处改动（后被游标方案取代）。
10. 名字：工具名「告知异常」，`taskdef.load(path, name=None, initial=None)`，`LoadError` 带 `path` 与 `where`，数据目录 `task_defs/`。
11. 五份写坏的定义由验证脚本在临时目录复制正确文件后改坏，不入库。

**游标方案（替代从行动记录推算位置）：**

- 位置存在保留槽位「游标」里，值是 `{"阶段", "步骤", "轮"}`，由加载器加进槽位表；任务作者不得使用这个名字。
- 候选 `Candidate` 加字段 `cursor_update`；行动选择接受四元组（工具名, 参数, 依据, 游标新值）。`select_action` 签名不变。ActionRecord 与传行动记录的两行已删除。
- 阶段游标只向前走；异常有三种。告知异常的参数是（阶段, 未达成目标, 步骤现况, 游标, 可选措施），「已做步骤」已去掉。
- 选重做时，由告知异常工具的变更组把游标写回该阶段第 0 步第 0 轮。
- 自主规划阶段必须写「最多」。

**初始输入：**

- 定义文件是一类任务的定义，不写某一次任务的数据。
- 材料接入登记四份文件的「目录」默认值改为 null，验证脚本启动时给初始输入 `{"目录": "样例材料"}`。
- 出差申请单的两个预填变体照旧通过 `initial` 传入。

**游标与既有断言冲突的裁定：**

1. 游标留在任务数据里。凡「工具写了什么」「某行动有几条数据变更」的既有检查只看业务槽位，终态数据比对只比业务槽位；另加一条断言核对游标的终态值。其他判定内容不改。
2. 内核只在行动成功时写游标，失败或被拒绝不写。
3. 以下五处自行决定已获同意：
   - 刚进入新阶段就报异常时，参数里的「游标」报新阶段的起点；
   - 组内最后一步被选中时，轮数就加一；
   - 告知异常候选不带游标新值；
   - 游标的步骤为空时写「游标在『阶段名』阶段第 n 回合」（后改为带阶段名）；
   - 「列表无项为空」的当前值只写为空项的下标。

## T3 逐项记录

检查用的临时脚本放在会话临时目录，没有进仓库。

### 开工前核对：16:59:54 组与 04:37 组

对七个任务标识，各取两组文件里全部「行动提出」事件的（工具名, 参数, 依据序号）逐项比较。实际输出：

```
T-scenario-1 T-scenario-1_20260914T165954567.jsonl T-scenario-1_20260914T043700951.jsonl 一致 3
T-scenario-2 T-scenario-2_20260914T165954587.jsonl T-scenario-2_20260914T043700976.jsonl 一致 0
T-scenario-3 T-scenario-3_20260914T165954590.jsonl T-scenario-3_20260914T043700979.jsonl 一致 3
T-scenario-4 T-scenario-4_20260914T165954599.jsonl T-scenario-4_20260914T043700991.jsonl 一致 2
T-intake-1 T-intake-1_20260914T165954608.jsonl T-intake-1_20260914T043701000.jsonl 一致 8
T-intake-2 T-intake-2_20260914T165954629.jsonl T-intake-2_20260914T043701022.jsonl 一致 8
T-intake-3 T-intake-3_20260914T165954649.jsonl T-intake-3_20260914T043701044.jsonl 一致 7
```

每行依次是：任务标识、16:59:54 组文件、04:37 组文件、是否一致、行动数。

### 第一项：静态工具表与前置条件

- tools.py 新增 `ToolSpec` 与 `STATIC_TOOLS`。每项含工具名、参数名清单、前置条件函数、可写槽位，以及前置条件的固定说明 `precondition_note`。
- `build_table` 改为从静态表取信息，只给询问绑定任务定义，并恒登记「告知异常」。
- 前置条件改为返回（成立与否, 说明, 命中值）。list_dir、register_file、generate_manifest 三个条件的判断与说明文字不变；ask 新增条件，说明是「写入目标指向的位置为 None」。
- 新增「告知异常」工具，实现 `report_exception`，拼话函数 `exception_utterance`。
- 哈希断言改为 dialogue.py、observe.py 对 01de6ab。
- 「任务开始」事件里的工具清单多了「告知异常」一项。

### 第二项：内核两处改动

- `new_task` 初始变更组的值深拷贝。
- `Candidate` 加 `cursor_update` 字段。`select_action` 接受三元组或四元组，四元组的第四项必须是（槽位名, 新值）二元组，否则按任务定义错误处理。
- 第 5 步状态更新时，候选带游标新值且行动已成功，就把它作为一条变更接在工具变更组后面，来源是同一个行动编号。

「只在成功时写游标」由两条既有场景的游标终态断言守住。故意把条件改回「不论成败都写」后，出差申请单场景三与材料接入登记场景三各失败一条（游标终态值），恢复后全过：

```
场景三：回答缺失：共 87 条断言，通过 86 条，失败 1 条
材料接入登记·场景三：回答缺失：共 110 条断言，通过 109 条，失败 1 条
  失败：第三步：游标的终态值是 {'阶段': '收集', '步骤': 2, '轮': 0}
  失败：第三步：游标的终态值是 {'阶段': '确认', '步骤': 1, '轮': 2}
```

### 第三项：加载器与数据文件

- **加载器**：新模块 taskdef.py，含 `load`、`LoadError`、`TaskDefinition`，以及：
  - 三个谓词、四种引用的递归求值；
  - 按第 4.7 节的校验；
  - 依据序号与说明的生成：步骤号在前，各阶段的告知异常号接在后面，自主规划阶段的工具号再接在后面。
- **执行语义**（`TaskDefinition.select_action` 与 `_walk`）：
  - 保留槽位「游标」；
  - 阶段游标只向前走；
  - 一趟从游标所指的步骤起走，跳过引用为 null 或前置条件不成立的步骤；
  - 步骤组有入口检查、轮数、一轮零候选即报异常；
  - 三种异常都构造告知异常候选。
- **数据文件**：`task_defs/` 下 travel.json、intake.json（出差申请单与材料接入登记登记完再问）。
- **验证脚本**改为经加载器取任务定义：
  - 材料接入登记的「目录」、出差申请单的预填变体都作为初始输入给出；
  - 删除 task_travel.py、task_intake.py；
  - 七个既有场景里，「工具写了什么」「某行动有几条数据变更」与终态数据比对改为只看业务槽位，共改了七处取数的写法，判定内容不变。改动都在代码里标了「第三步：只看业务槽位」或「只比业务槽位」。

七个既有场景各新增三条断言：游标终态值一条，旧运行文件存在一条，行动序列与旧运行文件逐位相等一条。实际输出（截取后两条）：

```
  [通过] 第三步：游标的终态值是 {'阶段': '收集', '步骤': 3, '轮': 0}
  [通过] 第三步目标一：行动序列（工具名、参数实际值、依据序号）与 T-scenario-1_20260914T165954567.jsonl 逐位相等，共 3 个行动
  [通过] 第三步：游标的终态值是 {'阶段': '收集', '步骤': 0, '轮': 0}
  [通过] 第三步目标一：行动序列（工具名、参数实际值、依据序号）与 T-scenario-2_20260914T165954587.jsonl 逐位相等，共 0 个行动
  [通过] 第三步：游标的终态值是 {'阶段': '收集', '步骤': 2, '轮': 0}
  [通过] 第三步目标一：行动序列（工具名、参数实际值、依据序号）与 T-scenario-3_20260914T165954590.jsonl 逐位相等，共 3 个行动
  [通过] 第三步：游标的终态值是 {'阶段': '收集', '步骤': 3, '轮': 0}
  [通过] 第三步目标一：行动序列（工具名、参数实际值、依据序号）与 T-scenario-4_20260914T165954599.jsonl 逐位相等，共 2 个行动
  [通过] 第三步：游标的终态值是 {'阶段': '生成清单', '步骤': 1, '轮': 0}
  [通过] 第三步目标一：行动序列（工具名、参数实际值、依据序号）与 T-intake-1_20260914T165954608.jsonl 逐位相等，共 8 个行动
  [通过] 第三步：游标的终态值是 {'阶段': '生成清单', '步骤': 1, '轮': 0}
  [通过] 第三步目标一：行动序列（工具名、参数实际值、依据序号）与 T-intake-2_20260914T165954629.jsonl 逐位相等，共 8 个行动
  [通过] 第三步：游标的终态值是 {'阶段': '确认', '步骤': 1, '轮': 2}
  [通过] 第三步目标一：行动序列（工具名、参数实际值、依据序号）与 T-intake-3_20260914T165954649.jsonl 逐位相等，共 7 个行动
```

加载错误五个场景（验证目标三）：每个场景在临时目录复制正确文件、改坏、写盘，宿主先建事件流并挂内存收集器与文件订阅者，再加载。每个场景 7 条断言：
- 文件真实存在；
- 抛出 LoadError；
- `path` 属性与错误信息含文件路径；
- `where` 属性等于预期位置；
- 错误信息含原因；
- 内存收集器为空；
- 运行目录里没有文件。

### 第四项：变体与异常的数据文件

- intake_interleaved.json：阶段「登记与确认」的步骤组是 [登记文件, 询问首个未答项]。行动序列的依据序号 1、2、3、2、3、2、3、4，与旧运行文件逐位相等。
- intake_bad_goal.json：登记阶段目标写成「相等：登记进度 与 99」，名字「材料接入登记（登记目标写错）」。
- intake_broken_goal.json：阶段顺序是 列目录、确认、登记，名字「材料接入登记（登记破坏确认目标）」。材料清单初始为空列表时，「确认」的目标（列表无项为空）成立，于是游标越过确认进入登记；登记追加三个是否纳入为空的项，确认的目标随之被破坏。

### 第五项：本步验证场景与断言

**验证目标二：同一份定义连跑两次**（8 条断言）。材料接入登记只加载一次，用同一个任务定义对象、同一个任务标识连跑两次。实际输出：

```
  [通过] 两次运行都正常完成
  [通过] 第二次「任务开始」事件里的初始槽位与第一次相同
  [通过] 第二次运行的材料清单初始为空（初始化变更组里材料清单的新值是空列表）
  [通过] 两次运行后，任务定义对象上的槽位表没有被运行改动
  [通过] 两次运行由内核线程发出的事件（共 119 条）除时刻、序号与等待毫秒数外逐条相同
  [通过] 两次运行由宿主线程发出的事件（共 14 条）除时刻、序号与等待毫秒数外逐条相同
  [通过] 两次运行的事件总数相同
  [通过] 两次运行写出了两份不同的运行文件
```

事件比对最初写成两次运行全部事件连同序号逐条相等，连跑三次里有两次失败。原因是宿主线程在发件箱上的「邮箱等待」事件与内核线程的初始化事件交错次序不同，序号因此错开：一次运行里第 2 号事件是宿主的等待开始，另一次是「目录」的初始化变更。这是线程调度造成的，不是两次运行互相影响，所以改为按发布线程分成两列、去掉序号各自比对，见 T4 第 1 条。

**验证目标四：异常告知使用者**（四个场景）。每个场景对每条告知异常行动核对以下各项：
- 依据（序号、说明「阶段名 › 告知异常」、命中值是异常报告）；
- 五个参数逐项等于写死的预期；
- 恰发出一条问题，类型 question，内容是话加参数；
- 话逐字等于预期句；
- 状态经过、最后一条说明、返回值；
- 选重做时变更组只有把游标写回的一条，选终止时没有数据变更。

各场景另做完整性、追踪形状、记录方与发起方、运行文件、可解释性检查。

- 场景甲（重做、重做、被动终止）：七圈七个行动，第五、六、七圈是告知异常。三句话实际输出：

```
  [通过] 行动 5 的话逐字等于「阶段『登记』目标未达成：登记进度 等于 99，当前值 3；游标在『登记』阶段第 1 步第 3 轮。可选措施：重做本阶段、主动终止、被动终止。」
  [通过] 行动 6 的话逐字等于「阶段『登记』目标未达成：登记进度 等于 99，当前值 3；游标在『登记』阶段第 0 步第 0 轮。可选措施：重做本阶段、主动终止、被动终止。」
  [通过] 行动 7 的话逐字等于「阶段『登记』目标未达成：登记进度 等于 99，当前值 3；游标在『登记』阶段第 0 步第 0 轮。可选措施：重做本阶段、主动终止、被动终止。」
  [通过] 运行索引的摘要：终态「内核错误」，原因「任务无法继续，使用者确认终止」
  [通过] 第三步：游标的终态值是 {'阶段': '登记', '步骤': 0, '轮': 0}
```

- 场景乙（主动终止）、场景丙（被动终止）：五圈五个行动，第五圈是告知异常，内核错误携带行动 5，游标不写，终值 `{登记, 1, 3}`。

```
  [通过] 运行索引的摘要：终态「内核错误」，原因「使用者主动终止」
  [通过] 运行索引的摘要：终态「内核错误」，原因「任务无法继续，使用者确认终止」
```

- 场景丁（第三种异常，重做后完成）：九圈八个行动，依据序号 1、3、3、3、5、2、2、2。

```
  [通过] 行动 5 的话逐字等于「阶段『确认』的目标在后续阶段被破坏：材料清单 里没有 是否纳入 为空的项，当前为空项的下标 [0, 1, 2]；游标在『登记』阶段第 1 步第 3 轮。可选措施：重做本阶段、主动终止、被动终止。」
  [通过] 游标的变化：初始在列目录，跳过已成立的确认进入登记，登记三轮后因重做退回确认，确认三轮后停住
  [通过] 第三步：游标的终态值是 {'阶段': '确认', '步骤': 1, '轮': 3}
```

故意把告知异常句式里的「游标在」改成「游标于」后，四个异常场景共失败 6 条（甲 3 条、乙丙丁各 1 条），恢复后全过：

```
第三步·异常场景甲：重做两次后被动终止：共 118 条断言，通过 115 条，失败 3 条
第三步·异常场景乙：主动终止：共 86 条断言，通过 85 条，失败 1 条
第三步·异常场景丙：被动终止：共 86 条断言，通过 85 条，失败 1 条
第三步·异常场景丁：后面阶段破坏前面阶段的目标，重做后完成：共 142 条断言，通过 141 条，失败 1 条
```

**观测台原因栏确认。** 在仓根启动 `python -m tod_kernel.observe serve --dir runs --port 8799`：8765 端口已被本机另一个服务占用，那个服务没有碰。查询 `/api/runs`，每个异常场景取最新一份运行文件，实际输出：

```
T-exception-4_20260914T204030563.jsonl | 材料接入登记（登记破坏确认目标） | 已完成 | 完成条件成立 | 圈 9 行动 8
T-exception-3_20260914T204030546.jsonl | 材料接入登记（登记目标写错） | 内核错误 | 任务无法继续，使用者确认终止 | 圈 5 行动 5
T-exception-2_20260914T204030529.jsonl | 材料接入登记（登记目标写错） | 内核错误 | 使用者主动终止 | 圈 5 行动 5
T-exception-1_20260914T204030502.jsonl | 材料接入登记（登记目标写错） | 内核错误 | 任务无法继续，使用者确认终止 | 圈 7 行动 7
页面状态 200
```

查询完即停掉服务，确认 8799 端口已释放。页面上原因栏的显示没有截图，需要负责人打开观测台看一眼。

### 第六项：实施报告与回写

- 本章即实施报告。
- 包说明 `__init__.py` 的模块清单改为 taskdef.py 与 task_defs/，去掉两个旧任务定义文件。
- 第零步实施简报末尾补了「9 第三步增补」：名字对照、定义文件中文键、参数键中英对照、静态工具表说明、本步硬约束。
- 步骤文档 6.1 节的验收记录没有填，留给负责人验收时填。

### 验证脚本最终汇总（原样）

```
场景一：正常流程：共 103 条断言，通过 103 条，失败 0 条
场景二：初始即完成：共 40 条断言，通过 40 条，失败 0 条
场景三：回答缺失：共 87 条断言，通过 87 条，失败 0 条
场景四：部分预填：共 74 条断言，通过 74 条，失败 0 条
材料接入登记·场景一：正常流程：共 148 条断言，通过 148 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 143 条断言，通过 143 条，失败 0 条
材料接入登记·场景三：回答缺失：共 110 条断言，通过 110 条，失败 0 条
第二步：观测台：共 22 条断言，通过 22 条，失败 0 条
第三步·加载错误：缺顶层键：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：类型与内容不符：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：引用不存在的槽位：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：工具名不在静态表：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：步骤组缺「最多」：共 7 条断言，通过 7 条，失败 0 条
第三步·同一份定义连跑两次：共 8 条断言，通过 8 条，失败 0 条
第三步·异常场景甲：重做两次后被动终止：共 118 条断言，通过 118 条，失败 0 条
第三步·异常场景乙：主动终止：共 86 条断言，通过 86 条，失败 0 条
第三步·异常场景丙：被动终止：共 86 条断言，通过 86 条，失败 0 条
第三步·异常场景丁：后面阶段破坏前面阶段的目标，重做后完成：共 142 条断言，通过 142 条，失败 0 条
结论：全部断言通过
```

与第二步提交时的条数相比：
- 出差申请单四个场景各多 3 条，就是上面新增的三条。
- 材料接入登记三个场景条数不变：哈希断言从五个文件减为两个，少 3 条；新增三条，多 3 条。
- 观测台组少 3 条，原因同样是哈希断言从五个文件减为两个。

## T4 本步的偏差与疑问

### 与步骤文档的出入

1. 验证目标二写的是「两次运行的事件序列除时刻与文件名外逐条相同」。实际比对还去掉了序号与等待毫秒数，并按发布线程分两列比较。原因：宿主线程与内核线程的事件共用序号，交错次序取决于调度，整列带序号比对连跑三次有两次失败；等待毫秒数由时刻算出。建议文档把口径改成「按发布线程分列、去掉时刻与序号后逐条相同」。
2. 验证目标二用来防止「同一份定义跑两次串数据」，但现有工具都新构造值、不原地改列表，所以即使去掉深拷贝，这组断言也发现不了串数据。深拷贝的必要性目前只由代码审读保证，没有能失败的测试。
3. 「列表无项为空」的文字写成「材料清单 里没有 是否纳入 为空的项，当前为空项的下标 [0, 1, 2]」，用「当前为空项的下标」代替「当前值」，免得读者把下标列表当成槽位的值。

### 我自行决定的地方

4. 场景丁的告知异常话原先写「游标在第 1 步第 3 轮」，没有游标所在的阶段名，读者会误以为是被破坏的「确认」阶段的位置。我提出后编制会话裁定改句式，游标那一句带阶段名，写「游标在『登记』阶段第 1 步第 3 轮」，tools.py 与四个异常场景的预期句已同步修改。自主规划阶段的句式随后也裁定带阶段名，写「游标在『阶段名』阶段第 n 回合」，tools.py 已改；本步不运行该阶段，没有断言覆盖。
5. 验证脚本的宿主遇到告知异常的问题（参数里没有写入目标）时，按场景给定的回答表依次回答；回答表用完就关闭收件箱。
6. 七个既有场景里取数写法的改动共七处：询问行动的数据变更、非询问行动的写入槽位、四处终态数据比对、场景三「行动 3 没有数据变更」。都只改为过滤掉「游标」，判定条件本身不变，并在行尾标注。
7. `select_action` 的文档字符串仍写「选择规则返回……三元组」，没有随四元组改动，以免 kernel.py 出现授权两处以外的差异。
8. verify.py 里删掉了一处重复定义的常量 `TASK_DEFS_DIR`（加载错误一节又定义了一遍，值相同）。

### 遗留

9. 逐位比对断言依赖本机 runs/ 下的旧文件，换机器即失败；runs/ 里的文件被清理后也会失败。
10. 观测台页面上原因栏的显示没有截图，只核对了接口返回的原因字段。
11. 「自主规划」阶段运行到时抛 NotImplementedError，本步没有场景覆盖。

### 我没做的事

12. 没有填步骤文档 6.1 节，没有改步骤文档，没有提交或推送代码。

## T5 kernel.py、tools.py、observe.py 相对 01de6ab 的差异全文

以下是 2026-09-15 修订批次（含第九项）之后的最新全文。

### kernel.py

```diff
diff --git a/tod_kernel/kernel.py b/tod_kernel/kernel.py
index 9b9c75a..9329ed4 100644
--- a/tod_kernel/kernel.py
+++ b/tod_kernel/kernel.py
@@ -79,7 +79,7 @@ STATE_EVENT_NAMES = (
 )
 
 # 追踪事件名：记做了什么检查、得了什么结论、调了什么；是诊断信息，不是改动，重放不用它们。
-TASK_STARTED = "TASK_STARTED"  # slots, rules, tools, task_def_name
+TASK_STARTED = "TASK_STARTED"  # slots, definition, tools, task_def_name
 LOOP_STARTED = "LOOP_STARTED"  # loop_no
 CHECK_DONE_RESULT = "CHECK_DONE_RESULT"  # done
 CONTROL_RESULT = "CONTROL_RESULT"  # action_id, verdict, checked
@@ -140,6 +140,9 @@ class Candidate:
     params: dict
     basis: Any  # 选择规则返回的依据，原样存放，内核不解释
     proposer: str  # "selector" 或 "user"
+    # 游标新值：（槽位名, 新值），由任务定义随候选给出，为空表示不动；行动成功时在状态更新里作为一条变更与工具的
+    # 变更组一起写入，来源是同一个行动编号，行动失败或被拒绝时不写。内核不解释槽位名与新值。
+    cursor_update: tuple | None = None
 
 
 @dataclass
@@ -370,7 +373,7 @@ def new_task(task_id, task_def, tools, inbox, outbox, stream) -> Task:
         raise KernelError("事件流绑定的任务标识与任务标识不一致")
     if inbox.box != INBOX or outbox.box != OUTBOX:
         raise KernelError("收件箱与发件箱的箱名不对")
-    init_changes = [Change(slot, None, value, INIT_SOURCE) for slot, value in task_def.SLOTS.items()]
+    init_changes = [Change(slot, None, copy.deepcopy(value), INIT_SOURCE) for slot, value in task_def.SLOTS.items()]
     return Task(
         task_id=task_id,
         task_def=task_def,
@@ -450,15 +453,19 @@ def select_action(task: Task) -> Candidate:
     raw = task.task_def.select_action(types.MappingProxyType(task.data))
     if raw is None:
         _definition_error(task, "选择规则返回空", raw)
-    if not (isinstance(raw, tuple) and len(raw) == 3):
-        _definition_error(task, "选择规则的返回不是（工具名, 参数, 依据）三元组", raw)
-    tool_name, params, basis = raw
+    if not (isinstance(raw, tuple) and len(raw) in (3, 4)):
+        _definition_error(task, "选择规则的返回不是（工具名, 参数, 依据）三元组或（工具名, 参数, 依据, 游标新值）四元组", raw)
+    tool_name, params, basis = raw[:3]
+    cursor_update = raw[3] if len(raw) == 4 else None
+    if cursor_update is not None and not (isinstance(cursor_update, tuple) and len(cursor_update) == 2
+                                          and isinstance(cursor_update[0], str)):
+        _definition_error(task, "游标新值不是（槽位名, 新值）二元组", raw)
     tool = task.tools.get(tool_name)
     if tool is None:
         _definition_error(task, f"工具未登记：{tool_name!r}", raw)
     if not isinstance(params, dict) or set(params) != set(tool.param_names):
         _definition_error(task, f"参数名与工具的参数名清单不符：{tool_name!r}", raw)
-    return Candidate(tool=tool_name, params=dict(params), basis=basis, proposer="selector")
+    return Candidate(tool=tool_name, params=dict(params), basis=basis, proposer="selector", cursor_update=cursor_update)
 
 
 def register_action(task: Task, candidate: Candidate) -> int:
@@ -559,44 +566,58 @@ def start_task(task_id, task_def, tools, inbox, outbox, stream) -> Task:
 
 def _run(task: Task) -> Task:
     """循环本体。正常完成时关闭发件箱后写结束记录；出错时由 start_task 关闭发件箱。"""
-    task_def, tools, outbox = task.task_def, task.tools, task.outbox
-    # 追踪：任务开始。槽位表、规则清单、工具清单都从任务定义与工具表读。
+    task_def, tools = task.task_def, task.tools
+    # 追踪：任务开始。槽位表、任务定义结构、工具清单都从任务定义与工具表读。
     loop = task.loop_publisher
     loop.publish(TASK_STARTED, {
         "slots": dict(task_def.SLOTS),
-        "rules": dict(task_def.RULES),
+        "definition": copy.deepcopy(task_def.DEFINITION),
         "tools": {name: list(tool.param_names) for name, tool in tools.items()},
         "task_def_name": task_def.NAME,
     })
     update_state(task, task.init_changes)
     update_state(task, TaskStatus.RUNNING)
-    loop_no = 0  # 只用于追踪事件的圈序号，不是对象
+    # 进循环前先做一次结果检查（不发「迭代开始」）：初始即完成时不进循环。
+    if _check_and_finish(task):
+        return task
+    loop_no = 0  # 只用于追踪事件的迭代序号，不是对象
     while True:
         loop_no += 1
         loop.publish(LOOP_STARTED, {"loop_no": loop_no})
-        # 第 1 步：结果检查只判不写；正常完成从这里返回，写入在下一行。
-        done = check_done(task)
-        loop.publish(CHECK_DONE_RESULT, {"done": done})
-        if done:
-            update_state(task, TaskStatus.DONE)
-            outbox.close(SOURCE_LOOP)
-            record_end(task, "完成条件成立")
-            return task
-        # 第 2 步：行动选择只返回候选，登记行动是写入点。圈首取主动类消息的位置留在这里，本步不实现。
+        # 第 1 步：行动选择只返回候选，登记行动是写入点。迭代开头取主动类消息的位置留在这里，本步不实现。
         candidate = select_action(task)
         action_id = register_action(task, candidate)
         action = task.actions[action_id]
-        # 第 3 步：执行控制。
+        # 第 2 步：执行控制。
         control(task, action)
         loop.publish(
             CONTROL_RESULT,
             {"action_id": action.action_id, "verdict": action.status, "checked": CONTROL_CHECKED},
             action_id=action.action_id,
         )
-        # 第 4 步：行动执行；未获准时不执行，变更组保持空列表。
+        # 第 3 步：行动执行；未获准时不执行，变更组保持空列表。
         if action.status == ActionStatus.APPROVED:
             execute(task, action)
-        # 第 5 步：状态更新。行动在登记时已经在行动表里，这里不再单独记录。
+        # 第 4 步：状态更新。行动在登记时已经在行动表里，这里不再单独记录。
+        # 候选带游标新值且行动已成功时，把它作为一条变更接在工具的变更组后面一起写入，来源是同一个行动编号；
+        # 行动失败或被拒绝时不写游标。
+        if candidate.cursor_update is not None and action.status == ActionStatus.SUCCEEDED:
+            slot, new = candidate.cursor_update
+            action.changes = list(action.changes) + [Change(slot, copy.deepcopy(task.data.get(slot)), new, action.action_id)]
         update_state(task, action.changes)
         if action.status == ActionStatus.FAILED:
             raise KernelError("行动失败", action=action)
+        # 第 5 步：结果检查挪到每次迭代末尾，紧跟状态更新；成立就结束，所以迭代数等于行动数。
+        if _check_and_finish(task):
+            return task
+
+
+def _check_and_finish(task: Task) -> bool:
+    """结果检查只判不写；成立时写完成状态、关发件箱、写结束记录。返回是否已完成。"""
+    done = check_done(task)
+    task.loop_publisher.publish(CHECK_DONE_RESULT, {"done": done})
+    if done:
+        update_state(task, TaskStatus.DONE)
+        task.outbox.close(SOURCE_LOOP)
+        record_end(task, "完成条件成立")
+    return done
```

### tools.py

```diff
diff --git a/tod_kernel/tools.py b/tod_kernel/tools.py
index 697f9ee..ebd0d72 100644
--- a/tod_kernel/tools.py
+++ b/tod_kernel/tools.py
@@ -1,7 +1,11 @@
 """工具与工具表。
 
-工具是「能做什么」的静态定义，行动是工具的一次调用。工具表独立于任务定义；
-任务定义只用工具名引用工具，不导入本模块。
+工具是「能做什么」的静态定义，行动是工具的一次调用。工具表分两层：
+- 静态工具表（STATIC_TOOLS）：每个工具的工具名、参数名清单、前置条件、可写槽位，不依赖任何任务定义；
+  任务定义加载器（taskdef.py）导入它，用来校验工具名与参数名、求前置条件、拼依据说明。
+- 运行工具表（build_table 按任务建）：在静态表之上配好工具实现，询问工具绑定任务定义的话语模板，交给内核。
+任务定义数据文件里只有工具名，不含任何代码。
+工具放进变更组的新值必须是新构造的对象，内核不再复制。
 """
 
 from __future__ import annotations
@@ -20,10 +24,10 @@ class Tool:
     name: str
     param_names: tuple
     impl: Callable[[Any], None]
-    # 以下三项本步只预留，不参与核验。
-    writable_slots: frozenset | None = None  # 可写槽位：槽位名的集合
-    preconditions: Any = None  # 前置条件：函数（只读数据, 参数）→（成立与否, 说明）
-    required_auth: Any = None
+    # 可写槽位与前置条件由静态工具表填上；执行控制本步不核验它们，前置条件只在行动选择时用。
+    writable_slots: frozenset | None = None  # 可写槽位：槽位名的集合；None 表示由参数决定（询问写入目标所指的槽位）
+    preconditions: Any = None  # 前置条件：函数（只读数据, 已求值的参数）→（成立与否, 说明, 命中值）
+    required_auth: Any = None  # 本步只预留
 
 
 class ToolTable:
@@ -167,40 +171,187 @@ def generate_manifest(ctx) -> None:
     ctx.set_status(ActionStatus.SUCCEEDED, f"清单含 {len(included)} 项，写到 {MANIFEST_PATH}")
 
 
-# 可写槽位与前置条件：按第一步文档填上，本步没有代码读它们，留给执行控制的真实核验。
-# 前置条件是函数，接收只读数据与参数，返回（成立与否, 说明）。
+# ───────────────────────── 告知异常 ─────────────────────────
+# 通用工具，对所有任务都登记，不是某个任务的领域工具。行动选择在三种情形下直接构造它的候选，它不走前置条件：
+# 一趟走完阶段目标仍未达成；步骤组轮数到「最多」仍未满足「重复直到」；阶段游标越过最后一个阶段而任务未完成。
+
+EXCEPTION_TOOL = "告知异常"
+EXCEPTION_PARAM_NAMES = ("阶段", "未达成目标", "步骤现况", "游标", "可选措施")
+CURSOR_SLOT = "游标"  # 任务定义加载器加进槽位表的保留槽位；重做本阶段时本工具把它写回
+REDO = "重做本阶段"
+ABORT_BY_USER = "主动终止"
+ABORT_UNABLE = "被动终止"
+EXCEPTION_OPTIONS = (REDO, ABORT_BY_USER, ABORT_UNABLE)
+# 使用者的选择 → （行动终态, 返回值, 说明）
+EXCEPTION_OUTCOMES = {
+    REDO: (ActionStatus.SUCCEEDED, "重做", "使用者选择重做本阶段"),
+    ABORT_BY_USER: (ActionStatus.FAILED, "主动终止", "使用者主动终止"),
+    ABORT_UNABLE: (ActionStatus.FAILED, "被动终止", "任务无法继续，使用者确认终止"),
+}
+
+
+def exception_utterance(params: dict) -> str:
+    """告知异常的话：只拼接参数里现成的内容，不经话语生成。
+
+    参数「游标」是 {阶段, 已完成步骤, 说明, 已完成轮数, 是组尾}：游标的三项加上已完成步骤的说明与分组信息。
+    句式：阶段『<阶段>』目标未达成：<各未达成目标的文字，以「；」连接>；已完成『<游标所在阶段>』阶段第 <已完成步骤> 步『<说明>』<轮数半句>。
+    可选措施：<以「、」连接>。轮数半句：不在组里（已完成轮数为 null）时不写；是组尾写「，该组已完成 r 轮」；
+    在组内但不是组尾写「，该组已完成 r 轮，第 r+1 轮进行中」，r 为 0 时写「，该组第 1 轮进行中」。
+    游标那一句带阶段名，因为第三种异常里报告的阶段与游标所在的阶段不是同一个；游标所在阶段不是报告的阶段时，
+    是阶段游标越过最后一个阶段、报告的阶段被后续阶段破坏，开头改为「阶段『<阶段>』的目标在后续阶段被破坏：」。
+    已完成步骤为 null 时：已完成轮数也为 null 是阶段起点，写「在『<游标所在阶段>』阶段起点，尚未执行步骤」；
+    已完成轮数不为 null 只有自主规划阶段（没有步骤，轮数是回合数），写「在『<游标所在阶段>』阶段已进行 <已完成轮数> 回合」。
+    """
+    stage, at = params["阶段"], params["游标"]
+    goals = "；".join(goal["文字"] for goal in params["未达成目标"])
+    head = f"阶段『{stage}』目标未达成：" if at.get("阶段") == stage else f"阶段『{stage}』的目标在后续阶段被破坏："
+    if at.get("已完成步骤") is not None:
+        where = f"已完成『{at.get('阶段')}』阶段第 {at.get('已完成步骤')} 步『{at.get('说明')}』"
+        where += _rounds_clause(at.get("已完成轮数"), at.get("是组尾"))
+    elif at.get("已完成轮数") is None:
+        where = f"在『{at.get('阶段')}』阶段起点，尚未执行步骤"
+    else:
+        where = f"在『{at.get('阶段')}』阶段已进行 {at.get('已完成轮数')} 回合"
+    options = "、".join(params["可选措施"])
+    return f"{head}{goals}；{where}。可选措施：{options}。"
+
+
+def _rounds_clause(rounds, group_last) -> str:
+    """游标那一句的轮数半句。"""
+    if rounds is None:
+        return ""
+    if group_last:
+        return f"，该组已完成 {rounds} 轮"
+    if rounds == 0:
+        return "，该组第 1 轮进行中"
+    return f"，该组已完成 {rounds} 轮，第 {rounds + 1} 轮进行中"
+
+
+def report_exception(ctx) -> None:
+    """告知异常：与询问同族，走发件箱与收件箱。
+
+    往发件箱放问题（内容 = {话, 参数}）→ 记等待中 → 在收件箱阻塞取回复这条问题的回答 → 按选择定终态：
+    重做本阶段记已成功（返回值「重做」），变更组把游标写回参数里的阶段起点（已完成步骤与已完成轮数都是 null）；
+    主动终止、被动终止记已失败，说明区分两种终止，由循环抛内核错误。
+    取不到回答（收件箱已关闭）记已失败；回答不是三个可选措施之一，也记已失败并写明回答原文。
+    """
+    action = ctx.action
+    params = action.params
+    question = ctx.outbox.put(Message(
+        kind="question",
+        sender=TOOL_SOURCE_PREFIX + action.tool,
+        recipient="user",
+        in_reply_to=None,
+        content={"utterance": exception_utterance(params), "params": copy.deepcopy(params)},
+        action_id=action.action_id,
+    ))
+    ctx.set_status(ActionStatus.WAITING, f"已向使用者告知异常，问题 {question.seq}")
+    message = ctx.inbox.take(
+        match=lambda m: m.kind == "answer" and m.in_reply_to == question.seq,
+        block=True,
+        waiter=action.action_id,
+    )
+    if message is None:
+        ctx.set_status(ActionStatus.FAILED, "没有可用的回答")
+        return
+    outcome = EXCEPTION_OUTCOMES.get(message.content) if isinstance(message.content, str) else None
+    if outcome is None:
+        ctx.set_status(ActionStatus.FAILED, f"回答不是可选措施之一：{message.content}")
+        return
+    status, result, note = outcome
+    action.result = result
+    if message.content == REDO:
+        old = copy.deepcopy(ctx.data_view.get(CURSOR_SLOT))
+        action.changes = [Change(CURSOR_SLOT, old, {"阶段": params["阶段"], "已完成步骤": None, "已完成轮数": None}, action.action_id)]
+    # 终态事件带返回值，所以先填返回值再记状态。
+    ctx.set_status(status, note)
+
+
+# ───────────────────────── 前置条件 ─────────────────────────
+# 前置条件是函数，接收只读数据与已求值的参数，返回（成立与否, 说明, 命中值）。
+# 说明是与数据无关的固定文字（依据说明在任务开始前就要拼好），动态内容放命中值。
+# 前置条件只写「这个工具此刻调用会不会出错」，不写任务要达成什么。
+
+ASK_NOTE = "写入目标指向的位置为 None"
+LIST_DIR_NOTE = "文件总表为 None"
+REGISTER_FILE_NOTE = "序号等于登记进度且小于文件总数"
+GENERATE_MANIFEST_NOTE = "每项是否纳入都不为 None"
+
+
+def _ask_precondition(data, params):
+    target = params.get("target") or {}
+    slot, path = target.get("slot"), list(target.get("path") or [])
+    node, reachable = data.get(slot), True
+    for step in path:
+        if isinstance(node, list) and isinstance(step, int) and not isinstance(step, bool) and 0 <= step < len(node):
+            node = node[step]
+        elif isinstance(node, dict):
+            node = node.get(step)
+        else:
+            node, reachable = None, False
+            break
+    return reachable and node is None, ASK_NOTE, {"槽位": slot, "路径": path, "当前值": node}
+
+
 def _list_dir_precondition(data, params):
-    return data.get("文件总表") is None, "文件总表为 None"
+    return data.get("文件总表") is None, LIST_DIR_NOTE, {"文件总表": data.get("文件总表")}
 
 
 def _register_file_precondition(data, params):
     files = data.get("文件总表")
     ok = files is not None and params.get("index") == data.get("登记进度") and params.get("index") < len(files)
-    return ok, "序号等于登记进度且小于文件总数"
+    hit = {"登记进度": data.get("登记进度"), "文件总数": None if files is None else len(files)}
+    return ok, REGISTER_FILE_NOTE, hit
 
 
 def _generate_manifest_precondition(data, params):
     items = data.get("材料清单") or []
-    return all(item["是否纳入"] is not None for item in items), "每项是否纳入都不为 None"
+    confirmed = sum(1 for item in items if item["是否纳入"] is not None)
+    return all(item["是否纳入"] is not None for item in items), GENERATE_MANIFEST_NOTE, {"已确认项数": confirmed}
+
+
+# ───────────────────────── 静态工具表与运行工具表 ─────────────────────────
+
+@dataclass(frozen=True)
+class ToolSpec:
+    """静态工具表的一项：工具名、参数名清单、前置条件、可写槽位。不含实现，不依赖任务定义。"""
+
+    name: str
+    param_names: tuple
+    preconditions: Any = None
+    writable_slots: frozenset | None = None
+
+
+STATIC_TOOLS = {
+    "ask": ToolSpec("ask", ("target", "hint"), _ask_precondition, None),
+    "list_dir": ToolSpec("list_dir", (), _list_dir_precondition, frozenset({"文件总表"})),
+    "register_file": ToolSpec("register_file", ("index",), _register_file_precondition,
+                              frozenset({"材料清单", "登记进度"})),
+    "generate_manifest": ToolSpec("generate_manifest", (), _generate_manifest_precondition,
+                                  frozenset({"清单文件路径"})),
+    EXCEPTION_TOOL: ToolSpec(EXCEPTION_TOOL, EXCEPTION_PARAM_NAMES, None, frozenset()),  # 不走前置条件
+}
+
+# 工具实现：工具名 → 函数（任务定义）→ 实现。只有询问要绑定任务定义（读话语模板）。
+_IMPLS = {
+    "ask": lambda task_def: functools.partial(ask, task_def=task_def),
+    "list_dir": lambda task_def: list_dir,
+    "register_file": lambda task_def: register_file,
+    "generate_manifest": lambda task_def: generate_manifest,
+    EXCEPTION_TOOL: lambda task_def: report_exception,
+}
 
 
 def build_table(task_def, tool_names=("ask",)) -> ToolTable:
-    """按任务建工具表：只登记给定名字的工具。询问工具用 partial 绑定任务定义，
-    所以工具表与任务绑定，每个任务各建一张。"""
-    available = {
-        "ask": lambda: Tool(name="ask", param_names=("target", "hint"), impl=functools.partial(ask, task_def=task_def)),
-        "list_dir": lambda: Tool(name="list_dir", param_names=(), impl=list_dir,
-                                 writable_slots=frozenset({"文件总表"}), preconditions=_list_dir_precondition),
-        "register_file": lambda: Tool(name="register_file", param_names=("index",), impl=register_file,
-                                      writable_slots=frozenset({"材料清单", "登记进度"}),
-                                      preconditions=_register_file_precondition),
-        "generate_manifest": lambda: Tool(name="generate_manifest", param_names=(), impl=generate_manifest,
-                                          writable_slots=frozenset({"清单文件路径"}),
-                                          preconditions=_generate_manifest_precondition),
-    }
+    """按任务建运行工具表：登记给定名字的工具，另外恒登记「告知异常」。
+    工具名、参数名清单、前置条件、可写槽位从静态工具表取；询问工具用 partial 绑定任务定义，
+    所以运行工具表与任务绑定，每个任务各建一张。"""
     table = ToolTable()
-    for name in tool_names:
-        if name not in available:
+    names = list(tool_names) + ([] if EXCEPTION_TOOL in tool_names else [EXCEPTION_TOOL])
+    for name in names:
+        if name not in STATIC_TOOLS:
             raise KernelError(f"没有这个工具：{name!r}")
-        table.register(available[name]())
+        spec = STATIC_TOOLS[name]
+        table.register(Tool(name=spec.name, param_names=spec.param_names, impl=_IMPLS[name](task_def),
+                            writable_slots=spec.writable_slots, preconditions=spec.preconditions))
     return table
```

### observe.py

```diff
diff --git a/tod_kernel/observe.py b/tod_kernel/observe.py
index 63d356c..c8c4f26 100644
--- a/tod_kernel/observe.py
+++ b/tod_kernel/observe.py
@@ -463,7 +463,7 @@ class ConsolePrinter:
             return f"终态 {_show(p['final_status'])}，原因：{p['reason']}"
         if event.name == TASK_STARTED:
             return (f"任务定义 {p['task_def_name']}，槽位表 {p['slots']}，"
-                    f"规则 {p['rules']}，工具 {p['tools']}")
+                    f"定义 {p['definition']}，工具 {p['tools']}")
         if event.name == CHECK_DONE_RESULT:
             return f"已完成={p['done']}"
         if event.name == CONTROL_RESULT:
```

## T6 2026-09-15 修订批次

本批次包括用户裁定的四件、taskdef.py 走查发现的四处、`_walk` 与 `load` 的拆分、随后裁定的游标表示法改动与轮数句式，第九项（结构化定义进事件与 JSON Schema），第十项（单步重复写在步骤上、步骤组改键名），第十一项（槽位元数据、路径图分带），第十二项（结果检查挪到迭代末尾、槽位表加表头），第十三项（交付物），以及第十四项（选择经过与迭代视图）。代码没有提交。

### 结论

- 验证脚本全部通过，退出码 0，共 1265 条断言，失败 0 条。与修订前的 1202 条相比多 63 条，来源如下：
  - 「步骤缺说明」「组内步骤带重复键」「槽位缺类型」「交付物来源槽位不存在」四个加载错误场景，各多 7 条；
  - 「步骤组『最多』引用指向列表」运行期场景，多 7 条；
  - JSON Schema 一组，多 16 条；
  - 告知异常话的轮数写法一组，多 6 条；
  - 「选择经过」断言：出差申请单场景四多 2 条，异常场景丁多 8 条；
  - observe.py 解除哈希锁，各场景共少 4 条。
- 既有断言只改了四类预期：依据说明、告知异常参数与话的文字、游标的预期值（按新表示法换算）、「任务开始」事件的 rules 键换成 definition。另外把输出文字里的「圈」改成了「迭代」。判定内容没有改。
- 场景计数：原有七个、异常四个、加载错误九个、运行期任务定义错误一个，共二十一个。另有观测台、连跑两次、JSON Schema、轮数写法四组，这四组不算场景。
- kernel.py 相对 01de6ab 共四处改动：
  - 初始变更组深拷贝；
  - 候选带游标新值；
  - 「任务开始」事件的 rules 键换成 definition，放 DEFINITION 的深拷贝；
  - 结果检查从迭代开头挪到迭代末尾，进循环前另做一次。
  另有随之改动的注释。
- observe.py 只改命令行打印的一行，把 rules 换成 definition，哈希锁解除。dialogue.py 与 01de6ab 逐字节相同，由哈希断言核对。
- tools.py 本批次的改动都属于「告知异常」一类：话按新的「游标」参数与轮数句式拼接；重做写回的游标换成新表示法；另外删掉了静态工具表里没人再用的 `precondition_note` 字段。
- 三个文件的差异全文见 T5。

### 第一件：步骤必填「说明」

- 五份数据文件的每个步骤都补了「说明」，文字照步骤文档的样例写。「登记一个问一个」「登记目标写错」「登记破坏确认目标」三份文件没有样例，同一个工具就用同一句说明。
- 加载器校验「说明」是非空字符串。缺这个键时报「缺少键」；是空串或只有空白时，报「步骤的说明应当是一句非空的话」。新增的第六份写坏定义把「列目录」第一步的说明改成空串。
- 依据说明改为「阶段名 › 第 n 步 步骤说明」，不再拼工具名与前置条件说明。
- 告知异常参数「游标」改为 `{"阶段", "已完成步骤", "说明", "已完成轮数"}`：游标槽位的三项，再加上已完成步骤的说明。参数名清单仍是五项。这一条先按「刚走过的步骤号」做过一版，随后被游标表示法的改动取代，最终写法见下文「游标改记已完成了什么」。

### 第二件：「圈」改「迭代」

- 观测台页面上的「圈」全部改成「迭代」，包括事件标签、循环时间线标题、索引的「迭代数」列、概览和对比视图。
- verify.py 输出里的「圈」也已改完。验证输出里仍有「第 n 圈开始」，它由 observe.py 的命令行查看器打印；observe.py 有哈希断言，不改。
- 事件名 LOOP_STARTED 与字段 loop_no 没有动。

### 第三件：观测台运行路径（只改 observatory.html）

- 任务概览之后新增三节：路线图、路径图、时间轴。原有的循环时间线、槽位轨迹、行动卡片三节保留。
- 联动：拖时间轴、在路径图上悬停或点击、点路线图的步骤格、点行动卡片，这几处都会选中同一次迭代。选中后，路线图、路径图、行动卡片、循环时间线四处一起高亮。
- 概览里的「阶段与步骤」只列全局步骤号与步骤说明。工具名从这次运行实际提出过的行动里补，没执行过的步骤不写工具；预留的告知异常条目不显示。
- 游标从槽位轨迹里拿掉。行动卡片新增「游标」一栏，显示可读位置；循环时间线里游标的数据变更也写成可读位置，不再逐条列差异。
- 行动卡片与事件行里，普通步骤的依据写作「第 n 步：步骤说明；本次值 …」。告知异常的依据写作「告知异常：『确认』阶段的目标在后续阶段被破坏；本次值 未达成目标的文字」；报告阶段与游标所在阶段相同时，改写「目标未达成」。
- 配色用原型的令牌，浅色、深色各一套，跟随系统设置，也认页面根元素上的 data-theme。原有令牌 --bad 的值换成了原型的值。
- 旧文件：rules 里不带阶段名时，全部归到一个无名阶段，按规则序号排格子；时间轴的游标卡片写「这次运行没有游标（旧格式文件）」。
- 自主规划阶段：按 rules 里「阶段 › 自主 › 工具」条目画工具卡片，选择序列与路径图横带只留结构。现有运行文件里没有这种阶段，页面上看不到这一块。
- 截图：用 agent-browser 在 1280 与 400 两种宽度下，各截了材料接入登记场景一、异常场景丁、旧文件 T-scenario-4 三次运行，共六张，截到时间轴一节为止。400 宽度下页面本身不横向滚动，路线图与路径图在各自的框里横向滚动。截图不入本仓。

### 游标改记已完成了什么

用户裁定：游标不再记「下一步的下标」，改记「已经完成了什么」。数据文件与依据序号不变。

- 游标三项：
  - 「阶段」：当前所在阶段的名字；
  - 「已完成步骤」：本阶段最近一次成功执行的步骤的全局步骤号，阶段刚进入时为 null；
  - 「已完成轮数」：已完成步骤在步骤组里时，该组已完整走过几轮；已完成步骤不在组里或为 null 时为 null。
- 初始值与重做写回的值都是 `{阶段, null, null}`。自主规划阶段的已完成步骤恒为 null；已完成轮数按裁定记已进行的回合数，本步不运行这种阶段。
- 与旧写法的换算：旧的「步骤 p」对应阶段内第 p 个步骤（从 1 数）的全局号，p 为 0 时对应 null；旧的「轮 r」在步骤组里就是已完成轮数 r，不在组里时为 null。验证脚本里全部游标预期值（游标终态、重做写回值、场景丁的游标变化序列、告知异常参数里的游标）都按这个换算改了值，判定没有改。
- taskdef.py 的改动：
  - `start_cursor` 返回 null、null；
  - `_try_step` 写入的游标新值里，已完成步骤取选中步骤的全局号；已完成轮数在组里取原来的轮数，不在组里写 null；
  - `_initial_state` 改为按已完成步骤推定起点：为 null 时从第一步进入；是组尾时是「一轮结束」；在组里但不是组尾时是「组内」，从下一步起；不在组里时从下一步进入；
  - 已完成步骤在本阶段找不到时，从末尾之后进入，一趟立即走完，按异常告知使用者。这种情形现有数据不会出现。
- 告知异常的话，游标那一句有四种写法：
  - 一般写「已完成『登记』阶段第 2 步『登记下一个文件的名字、类型、大小、页数』，该组已完成 3 轮」；
  - 已完成轮数为 null 时，不写「该组已完成」那半句；
  - 已完成步骤为 null 时写「在『确认』阶段起点，尚未执行步骤」；
  - 自主规划阶段写「在『阶段名』阶段已进行 n 回合」，没有断言覆盖。
- 观测台：
  - 时间轴、行动卡片、循环时间线里的游标，用同样的说法显示。
  - 第三步早先写出的运行文件里，游标仍是旧写法，页面换算后再显示。旧写法里不在组里的步骤也记轮 0，页面分不出组，所以轮数为 0 时不显示。
  - 场景丁新旧两份运行文件逐次迭代读出的位置一致。
- 轮数句式随后另有裁定，见下一节。

### 轮数句式与「是组尾」

- 告知异常参数「游标」加第五个键「是组尾」：true、false 或 null，由加载器填；已完成步骤不在组里或为 null 时是 null。参数名清单仍是五项。
- 游标那一句的轮数半句：
  - 已完成的步骤是组尾时，写「该组已完成 r 轮」；
  - 在组内但不是组尾时，写「该组已完成 r 轮，第 r+1 轮进行中」，r 为 0 时只写「该组第 1 轮进行中」；
  - 已完成的步骤不在组里时，不写。
- 现有异常场景只经过组尾与阶段起点两种情形，所以另加了「告知异常话的轮数写法」一组 6 条断言：直接调工具的拼话函数，核对组尾、组内、第 1 轮进行中、不在组里、阶段起点、自主规划阶段六种写法。
- 观测台的时间轴、行动卡片、循环时间线用同一套写法，组边界从事件里的 definition 取。没有 definition 的旧文件分不出组尾，只写「该组已完成 r 轮」。

### 第九项：结构化定义进事件与 JSON Schema

- 任务定义对象废除 RULES，改为暴露 DEFINITION，形状是 `{"阶段列表": [...], "告知异常编号": {阶段名: 编号}, "自主工具编号": {阶段名: {工具名: 编号}}}`：
  - 阶段列表与文件里的写法同形，含类型、目标、步骤、步骤组（重复直到、最多）、可选工具集与最多；
  - 每个步骤多一个「编号」键，值是全局步骤号；
  - 「最多」写成 3.0 这类整数值小数时，加载器规整成整数。
- 依据说明表由加载器从结构生成，存在对象的私有属性里，不对外。
- kernel.py 在「任务开始」事件里放 DEFINITION 的深拷贝，键名 definition。验证脚本原有的「任务开始事件内容」断言改为比对 DEFINITION。
- 观测台改为直接读 definition：
  - 概览的「阶段与步骤」列出每个阶段的目标、步骤组的重复条件与「最多」，以及每个步骤的全局号、说明、工具；
  - 路线图的阶段格里写目标，步骤组画成虚线框，框底写「最多 … 轮 · 重复直到 …」。框窄时文字从后面截断，「最多」放在前面保证能看到，完整文字在悬停提示里；
  - 没有 definition 的旧文件仍按 rules 解析，画不出目标与步骤组，页面上会注明。
- `task_defs/任务定义.schema.json`（JSON Schema 2020-12）的约束：
  - 顶层四块，不允许未定义的键；
  - 阶段类型与条件必填：固定步骤必须有步骤，不得有可选工具集与最多；自主规划必须有可选工具集与最多，不得有步骤；阶段名不含「 › 」；
  - 步骤必须有说明、工具、参数，说明至少含一个非空白字符；工具名不得是「告知异常」；
  - 步骤组必须有步骤、重复直到、最多；组内只能是步骤，所以组不嵌套；「最多」是非负整数或引用；
  - 三个谓词各自的字段；四种引用恰好一个键，数组引用的元素个数固定；参数值里任何带引用键的字典都必须整体是合法引用；
  - 槽位表禁用「游标」；话语模板的键各段非空，值是字符串。
- 验证脚本加「任务定义 JSON Schema」一组，用本机 jsonschema 4.10.3，没装时打印提示后跳过。断言内容：
  - schema 本身合法；
  - 五份好文件通过；
  - 写坏的定义里，结构错误被拦下，语义错误放行（第十项之后的清单见下一节）；
- 手工另核对了八种结构错误，都被 schema 拦下：嵌套组、引用多键、槽位表用「游标」、阶段名含分隔符、步骤工具写「告知异常」、阶段上的未知键、自主规划缺最多、「相等」写错字段。
- 加载器与 schema 对照后，发现一处加载器比 schema 严：「最多」写成 3.0 时，schema 的 integer 接受，加载器原先拒绝。已按 schema 为准，改为加载器接受并转成整数。
- 截图：六张已按新的概览、路线图与时间轴文字重截。1280 宽度下三次运行的路线图都不需要横向滚动。

### 第十项：单步重复写在步骤上，步骤组改键名

- 格式：
  - 单个步骤要重复，直接在步骤上写成对的「重复直到」「最多」，要么都写要么都不写；
  - 多个步骤一起重复写成 `{"步骤组": [步骤…], "重复直到": …, "最多": …}`，组不嵌套，组内步骤不得带重复键。
- 加载器把带重复键的单步骤当作只有一个成员的组，执行语义不变，「是组尾」恒为 true。Group 多了一个 single 标记，DEFINITION 据此写回与文件同形的结构：单步带重复键原样写，组写「步骤组」。
- 核对过五份数据文件：DEFINITION 去掉「编号」后与文件的阶段列表逐项相同。
- 加载器的两条新报错：
  - 单步或组只写了一个重复键时，报「……的「重复直到」与「最多」要么都写要么都不写，缺少「最多」」；
  - 组内步骤带重复键时，报「组内步骤不得带「重复直到」「最多」，重复由步骤组统一写」。
- schema 的改动：
  - 步骤列表里含「步骤组」键的一项按组校验，否则按步骤校验；
  - 步骤上两个重复键用 dependentRequired 约束成对出现；
  - 新增「组内步骤」定义，是步骤的形状但禁带重复键，组内只能是它，所以组不嵌套。
- 数据文件：
  - intake.json、intake_bad_goal.json、intake_broken_goal.json 的登记与确认两个阶段改为单步带重复键；
  - intake_interleaved.json 的合并阶段改用「步骤组」；
  - 全局步骤号与行动序列不变，与第二步旧运行文件的逐位比对照样通过。
- 写坏的定义共七份。第十项把「步骤组缺『最多』」换成「步骤只写重复直到不写最多」，新增「组内步骤带重复键」，从「登记一个问一个」复制后改坏。「引用不存在的槽位」的改坏位置随格式调整。
- schema 核对的结果：
  - 五份结构错误被拦下：缺顶层键、类型与内容不符、步骤只写重复直到不写最多、组内步骤带重复键、步骤缺说明；
  - 两份语义错误放行，由加载器拦下：引用不存在的槽位、工具名不在静态表；
  - 手工另核对了三种写法，都被拦下：嵌套组、单步只写最多、组仍用旧键名「步骤」。
- 观测台：
  - 认「步骤组」和带重复键的单步，也兼容第九项后、第十项前写出的文件，那批文件里组的键还是「步骤」；
  - 路线图对单步重复画同样的回环虚线框，框底写「最多 … 轮 · 重复直到 …」；
  - 概览里单步重复写「重复直到目标成立 · 最多 = …」，步骤组前面多「步骤组 · 」。
- kernel.py、tools.py、observe.py 本项没有改动，T5 的差异全文仍是最新。
- 六张截图已重截。

### 第十一项：槽位元数据与路径图分带

- 任务定义文件的顶层改为三块：名字、槽位、阶段列表，「话语模板」块取消。
- 「槽位」的值改为元数据对象：
  - 说明、类型必填，类型是文本、数字、布尔、列表、对象、枚举六选一；
  - 默认值可省，省略即 null；
  - 枚举型必须写取值；
  - 列表型可带「项」，是元素的字段表，每个字段有说明、类型，可选取值、提问；
  - 「提问」是 str.format 句式。
- 加载器：
  - SLOTS 取各槽位的默认值。
  - TEMPLATES 从槽位上的「提问」生成，键是（槽位名, ()）；从列表项字段上的「提问」生成，键是（槽位名, (字段名,)）。键形与 dialogue.py 读的一致，dialogue.py 零差异。
  - 校验说明与类型必填、类型在六个取值内、枚举型有取值、取值是非空数组、提问是字符串；项内字段同样检查。类型不做运行时值检查。
- DEFINITION 多一个「槽位」键，放文件里的槽位元数据原样。
- 五份数据文件按步骤文档 4.8 节样例改写。改写后的核对：
  - 五份文件的 SLOTS 与改写前各槽位的默认值逐项相同；
  - TEMPLATES 与改写前的话语模板逐项相同；
  - 依据序号、行动序列、话都不变，与第二步旧运行文件的逐位比对照样通过。
- 写坏的定义：新增「槽位缺类型」（删掉「目录」的类型），共八份；「缺顶层键」改为删「阶段列表」，因为「话语模板」已不是顶层键。
- schema 同步：顶层三块；槽位值用「槽位元数据」定义，项字段用「项字段」定义，都不允许未知键，枚举型用 if/then 要求取值。
- schema 核对结果：
  - 六份结构错误被拦下：缺顶层键、类型与内容不符、步骤只写重复直到不写最多、组内步骤带重复键、槽位缺类型、步骤缺说明；
  - 两份语义错误放行，由加载器拦下；
  - 手工另核对了五种写法，schema 与加载器都拦下：枚举型缺取值、项字段类型不在六个里、槽位上的未知键、仍写话语模板、槽位仍写成旧的默认值。
- 路径图分带（有 definition 时）：
  - 纵轴按阶段分成带底色的横带，相邻两带底色交替，带左侧写「阶段名 · n 步」；
  - 带内每一步一行，全部步骤都画，没执行过的行标签变灰，并在行首画灰色空心圆；
  - 图顶一句「n 个阶段 · m 步 · 走过 k 步」；
  - 自主规划阶段的带写「✦ 阶段名 · n 个可选工具」，带内可选工具各一行（结构预留）；
  - 旧文件没有 definition，保持原来的画法。
- 概览的「槽位表」：
  - 有 definition 时，每个槽位一行，写名字、类型、说明、初始值，有取值、提问的也写上；
  - 列表型的项字段缩进列出，含提问；
  - 保留槽位「游标」不在定义里，标「保留槽位」并写初始值；
  - 旧文件保持只列名字与初始值。
- 1280 宽度下，三次运行的路线图与路径图都不需要横向滚动。六张截图已重截。
- kernel.py、tools.py、observe.py 本项没有改动，dialogue.py 零差异。

### 第十二项：结果检查挪到迭代末尾，槽位表加表头

- kernel.py：
  - 结果检查抽成 `_check_and_finish`：只判不写，成立时写完成状态、关发件箱、写结束记录。
  - `_run` 在进循环前调它一次，不发「迭代开始」；每次迭代按行动选择、登记、执行控制、执行、状态更新（含游标）、失败抛错的顺序走完后再调一次。
  - 循环内的步骤注释按新顺序改为第 1 到第 5 步；`_run` 里不再用到的局部变量 outbox 删了。
  - 行为等价，每次状态更新之后仍紧跟一次检查。正常完成的运行迭代数减一，等于行动数；以内核错误结束的运行迭代数不变，因为抛错发生在那次迭代的检查之前。
- verify.py 的改动：
  - 迭代数断言：场景一 4 改 3，场景二 1 改 0，场景四 3 改 2，材料接入登记场景一、二 9 改 8，异常场景丁 9 改 8；以内核错误结束的场景三、材料接入登记场景三、异常场景甲乙丙、运行期任务定义错误不变；
  - 摘要预期表同样改；
  - 「结果检查结论」断言改为「循环前一次、每次迭代末尾一次，只有最后那次为真」，以内核错误结束时全为假、条数等于迭代数；
  - 第一步目标二那条的描述同步改。
  - 断言总数不变。与第二步旧运行文件的逐位比对只比行动序列，照样通过。
- 观测台：
  - 按事件的实际结构判断：第一个「结果检查结论」在第一个「迭代开始」之前，就是新结构；
  - 新结构的时间线在「开始」之后单列「启动检查」块，不归任何迭代，每次迭代的五步依次是行动选择、执行控制、行动执行、状态更新、结果检查；
  - 最后一次迭代的标题带「结果检查为真，任务结束」；
  - 场景二这种启动检查即完成的运行没有迭代，时间轴写「这次运行没有迭代」；
  - 旧文件照旧显示，结果检查在迭代开头，最后一次迭代只有检查。
- 槽位表：
  - 时间轴与概览共用一套列：槽位、类型、值、说明。时间轴的值列叫「当前值」，概览的叫「初始值」。
  - 类型与说明取自 definition 的槽位元数据，旧文件留空；说明后面接取值与提问。
  - 列表显示「n 项」，字典显示摘要，都带「展开／收起」开关。
  - 「游标」行的类型写「保留槽位」，值写可读位置。
  - 变过的行标黄；概览里列表型槽位下面缩进列出项字段。
  - 页面上「整值」一词全部换成「展开／收起」开关，涉及数据变更描述、槽位轨迹、行动卡片的变更、对比视图。
- 六张截图已重截。

### 第十三项：交付物

- 任务定义加第四块「交付物」：列表，每项有名字、说明、来源、形态。形态是表单、表格、文本、文件之一；表单的来源是槽位名列表，其余形态的来源是一个槽位名。
- 加载器：
  - 检查四个键齐全，名字与说明非空，形态在四个取值内；
  - 检查来源槽位存在；
  - 检查形态与来源槽位的类型匹配：表单的每个来源是标量槽位（文本、数字、布尔、枚举）；表格的来源是带「项」的列表型；文本与文件的来源是文本型。
- 对外暴露：新增 DELIVERABLES 属性；DEFINITION 多一个「交付物」键，放原样。
- schema：顶层四块必填；新增「交付物」定义，四个字段必填、不允许未知键、形态枚举；形态为表单时来源是非空名称数组，否则是名称。
- 五份数据文件已加交付物：
  - 出差申请单：表单一份，来源是目的地、日期、事由；
  - 材料接入登记、登记一个问一个、登记目标写错三份：表格「材料清单」加文件「清单文件」；
  - 登记破坏确认目标：只有表格一份，因为这份定义没有生成清单阶段，文件永远不会产出。
- 依据序号与行动序列不变。
- 写坏的定义新增「交付物来源槽位不存在」，把第一份交付物的来源改成「登记表」，共九份。它是语义错误，schema 放行，由加载器拦下。
- 手工另核对了八种写法，结果如下：
  - 加载器拦下、schema 放行：表格来源不是列表、文件来源是列表、表单来源含列表槽位；
  - 两边都拦下：形态不在枚举、表格来源写成列表、表单来源写成单个名、缺交付物块、交付物上的未知键。
- 观测台「交付物」一块，放在概览之后、路线图之前，数据取这次运行的终态数据：
  - 标题行：任务名 · 运行标识。完成时标「✓ 已交付 n 份」，并写完成时间与迭代数；完成时间由文件名里的开始时刻加事件耗时算出。未完成或以错误结束时标「✕ 未交付」，并写终止原因与迭代数。
  - 表单：两列单据样式，标签取槽位说明，值加粗，空值灰字「未填」。
  - 表格：列取「项」字段，列头用字段名，悬停提示字段说明。布尔字段显示「✓ 纳入」「— 不纳入」这类文字，没回答的显示「尚未确认」。数字右对齐并加千分位，行底色交替，表尾一句汇总。
  - 文本：引文块，附来源槽位与说明。
  - 文件：文件图标、文件名、路径，内容可展开或收起。内容取自写入该路径槽位的那条行动的返回值，页面上注明是哪条行动，不加文件接口。
  - 缺失的处理：还没有内容的交付物不画卡片，统一在块底写「缺：……」；卡片里未达成的部分写在卡尾。
- 路线图结束旗下面加一行链接：完成时写「交付物 n 份 ↑」，内核错误结束时写「交付物未交付 ↑」，点击滚动到交付物块。
- 没有 definition 或没有交付物块的运行文件不显示这一块，所以旧文件 T-scenario-4 的两张截图里没有交付物块。
- 截图：六张已重截，另补了异常场景乙（主动终止、未交付）的 1280 与 400 两张。
- 「是否纳入」的元数据原是布尔型，但询问工具写入的是「是」「否」两个字，类型与实际值不一致。按裁定改为枚举型，取值 ["是", "否"]，四份材料接入登记数据文件同改；回答理解本步原样照收，不转布尔。观测台把布尔型与取值恰好是「是」「否」的枚举型都当作是否字段，表格里照样显示「✓ 纳入」「— 不纳入」。
- kernel.py、tools.py、observe.py 本项没有改动，dialogue.py 零差异。

### 第十四项：选择经过与迭代视图

- 加载器在依据命中值字典里加「选择经过」键：`{"前进": [阶段名…], "跳过阶段": [[阶段名, 原因]…], "跳过步骤": [[全局步骤号, 原因]…]}`。
  - 「前进」：游标所在阶段的目标已成立、这次离开的阶段；
  - 「跳过阶段」：途经的目标已成立的阶段，原因写「目标成立」；
  - 「跳过步骤」：原因有四种——「参数引用为空」「前置条件不成立：<说明>」「重复直到已成立，越过整组」「最多为空，越过整组」。
- 实现：一次行动选择的经过记在 `_PassRecord` 里，里面也放原来的「组首走起过的组」集合；`_walk` 与三个状态函数改为传它。前置条件命中值不是字典时，放在「命中值」键下；为空时只有「选择经过」。告知异常的命中值不加这一键。
- 既有断言里没有比对步骤命中值内容的，所以没有需要加键的。新增 `check_selection_trail`，逐个行动核对「选择经过」，并核对告知异常的命中值不带它：
  - 出差申请单场景四：行动 2 跳过第 2 步，原因是前置条件不成立；
  - 异常场景丁：行动 2 前进『列目录』、跳过『确认』，其余为空，行动 5 是告知异常。
- 观测台：「循环时间线」整块换成迭代视图，样式依据原型 v3。
  - 卡片的组成：
    - 「开始」卡：任务开始、初始化变更、启动检查。启动检查一行取第一次迭代的选择经过，说从哪个阶段开始。
    - 每次迭代一张卡：卡头写第几次迭代（脚注指向「一次迭代开始」）、阶段与步骤说明、行动与工具调用、结果标签、耗时；中间是五步进度条；下面是五行叙事。
    - 收尾卡：任务状态变为已完成、内核关闭发件箱、任务结束，从第一条起其后的事件都归这里。
  - 五步进度条：圆点按结果着色，判断类蓝、成功绿、与使用者交互紫、异常红、被拒绝虚线；圆点下标本步第一条事件的相对时刻，行动执行一步标跨度。
  - 五行叙事：
    - 「为什么选它」：由选择经过与依据生成，含参数由哪个引用求出什么值。告知异常时这一行叫「为什么异常」，三种异常各一句，种类读依据命中值里的「异常种类」（见下）；早先的运行文件没有这个键，才退回按报告内容推断。
    - 「执行控制」：获准或被拒绝，以及核验方式。
    - 「做了什么」：普通工具写调用、参数与返回；询问与告知异常画成系统、使用者两侧气泡，气泡下小字写问题号、行动等待中、宿主取走、回复问题号、等了多久。
    - 「改了什么」：变更做成标签，列表写「n 项 → m 项（逐项差异）」，游标单独用蓝标签。
    - 「完成了吗」：取下一次迭代的选择经过判定目标；最后一次写「全部阶段目标成立」。
  - 事件的展开：
    - 每行末尾「n 条事件」开关就地展开该行翻译的原始事件，列出序号、相对时刻、类别、事件名、描述、记录方，外部一侧记录方用紫色；
    - 句内脚注 #n 点开单条并标黄；
    - 顶部「显示全部原始事件」总开关。
  - 顶部四个过滤器改为高亮：命中事件标黄并展开所在行，叙事其余变淡，行动卡片与槽位轨迹里不相关的也变淡，都不隐藏；清除过滤器时，收起因过滤而展开的行。
  - 点时间轴、路径图、路线图步骤格、行动卡片都会高亮对应迭代卡；点击（不是拖动或悬停）时还把卡滚到眼前。
  - 旧格式文件按事件实际结构生成叙事：结果检查在迭代开头时，⑤ 行注明旧格式；没有选择经过时，① 行只写依据。
  - 事件不丢不重：在七种运行文件上核对过，页面上展开的事件序号与运行文件逐条相同，都没有缺失或重复。七种是场景丁、场景甲、材料接入登记场景三、出差申请单场景二、运行期任务定义错误、旧文件 T-scenario-4、第三步早先的场景丁文件。
  - 删掉了原来时间线专用的函数与样式：eventLineHTML、stepNote、timelineHTML、loopTitle，以及外部事件阻塞标记。
- 异常种类（本项追加裁定）：
  - 三种异常的判别不在页面上算，由加载器在告知异常那条依据的命中值里加「异常种类」键，取值「一趟走完」「组到上限」「后续破坏」；
  - 「组到上限」包括轮数到「最多」、「最多」为 0，以及同一次行动选择里组内一轮零候选三种情形；
  - 告知异常工具的五个参数不变；
  - check_exception_action 的命中值比对加了这一键：异常场景甲、乙、丙是「一趟走完」，丁是「后续破坏」，断言条数不变；
  - 手工核对过「组到上限」：把登记步骤的「最多」改成 2，登记进度 2 时行动选择给出告知异常，异常种类是「组到上限」，参数里不含这个键。
- 截图：
  - 八张常规截图已重截，截到时间轴为止；
  - 另截场景丁迭代视图两张（1280、400），第 2、5、6 次迭代卡的五行全部展开，范围从迭代视图开头到第 7 次迭代卡之前。
- kernel.py、tools.py、observe.py 本项没有改动，dialogue.py 零差异。

### 走查发现的四处

1. 「最多」写成引用、求出的值既不是 null 也不是非负整数时，`_enter` 与 `_round_end` 直接返回 None，行动选择因此返回空，内核报「任务定义错误」，原因「选择规则返回空」。新增运行期场景：把 intake.json 登记组的「最多」改成 `{"槽位": "文件总表"}`，文件放在临时目录。加载通过；第 2 次迭代报任务定义错误，只提出过列目录一个行动，索引摘要的终态是「内核错误」。
2. 加载时阶段名不得含「 › 」。手工核对过：阶段名写「收集 › 甲」时报加载错误，位置是「阶段列表[0]（收集 › 甲）.名字」。没有为此加断言场景。
3. 进组处原来的「rounds = 0；if rounds >= limit」改为「if limit == 0」，并加了注释；`_try_step` 里没用到的 note 改成 `_`。重复的置零：越过整组的分支里那一处删了；进组前的那一处保留，因为进组后「组内」状态要从第 0 轮开始算。
4. `_evaluate` 的函数注释写明：「槽位」引用返回的是任务数据里的活对象，调用者不得改动，带出去必须先深拷贝。

### 拆分 `_walk` 与 `load`

- `_walk` 拆成五个函数：
  - `_initial_state`：由游标推定起点状态，注释里举了「登记一个问一个」的例子；
  - `_enter`、`_in_group`、`_round_end`：各处理一种状态；
  - `_walk` 本身只剩状态机驱动循环。
- 各状态函数的返回值分两种：结局用 `_Outcome` 包起来，其余是下一状态与轮数。
- `load` 拆出九个函数：`_check_top`、`_check_initial`、`_parse_stages`、`_parse_stage`、`_check_stage_head`、`_parse_steps`、`_parse_group`、`_parse_templates`、`_build_rules`。`load` 本身只留主干顺序。
- taskdef.py 里现在最长的函数不超过 35 行。
- 行为不变的证据有两条：一是全部断言照样通过；二是拿拆分前的副本对比五份数据文件，名字、槽位、说明表、话语模板与阶段结构逐项相同。

### 验证脚本最终汇总（原样）

```
场景一：正常流程：共 103 条断言，通过 103 条，失败 0 条
场景二：初始即完成：共 40 条断言，通过 40 条，失败 0 条
场景三：回答缺失：共 87 条断言，通过 87 条，失败 0 条
场景四：部分预填：共 76 条断言，通过 76 条，失败 0 条
材料接入登记·场景一：正常流程：共 147 条断言，通过 147 条，失败 0 条
材料接入登记·场景二：规则互换变体：共 142 条断言，通过 142 条，失败 0 条
材料接入登记·场景三：回答缺失：共 109 条断言，通过 109 条，失败 0 条
第二步：观测台：共 21 条断言，通过 21 条，失败 0 条
第三步·加载错误：缺顶层键：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：类型与内容不符：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：引用不存在的槽位：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：工具名不在静态表：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：步骤只写重复直到不写最多：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：组内步骤带重复键：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：槽位缺类型：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：交付物来源槽位不存在：共 7 条断言，通过 7 条，失败 0 条
第三步·加载错误：步骤缺说明：共 7 条断言，通过 7 条，失败 0 条
第三步·任务定义 JSON Schema：共 16 条断言，通过 16 条，失败 0 条
第三步·告知异常话的轮数写法：共 6 条断言，通过 6 条，失败 0 条
第三步·运行期任务定义错误：步骤组「最多」引用指向列表：共 7 条断言，通过 7 条，失败 0 条
第三步·同一份定义连跑两次：共 8 条断言，通过 8 条，失败 0 条
第三步·异常场景甲：重做两次后被动终止：共 118 条断言，通过 118 条，失败 0 条
第三步·异常场景乙：主动终止：共 86 条断言，通过 86 条，失败 0 条
第三步·异常场景丙：被动终止：共 86 条断言，通过 86 条，失败 0 条
第三步·异常场景丁：后面阶段破坏前面阶段的目标，重做后完成：共 150 条断言，通过 150 条，失败 0 条
结论：全部断言通过
```

### 遗留与疑问

- 路线图上的阶段是否「达成」由页面推断：任务完成时全部算达成；没完成时，游标最后所在阶段之前的阶段算达成。事件里没有阶段目标的求值结果，这个推断在「前面阶段被破坏后又被使用者终止」的运行上可能不准。
- 路径图没有画自主规划阶段的回合上限竖线：现有运行文件里没有这种阶段，结构留着，没有真实数据验证。
- 步骤文档 4.5 节末尾仍有一句「RULES 属性就是序号到说明的字典」，与第九项废除 RULES 的裁定矛盾。
- 任务定义错误的原因仍只有「选择规则返回空」一句，任务定义一侧还不能带可读原因给内核。

# 第四步：术语澄清任务接模型跑通（2026-09-16）

## F1 结论

第四步的六项工作加上追加的第七项都已做完，验证脚本 `python3 -m tod_kernel.verify` 六个场景与五组检查共 **532 条断言全部通过**（材料接入登记正常 148 条、材料接入登记目标写错 86 条、加载错误三份坏文件 21 条、术语澄清给全初始输入 75 条、术语澄清不给初始输入 107 条、术语澄清模型不可达 45 条、观测台 22 条、任务定义 JSON Schema 8 条、告知异常话的轮数写法 6 条、模型调用件三种模式 9 条、控制台的问答打印 5 条）。控制台 `python3 -m tod_kernel.console` 选 0 跑的东西与直接跑验证脚本完全相同；选 1 能在终端看到材料接入登记的三问三答与「断言：通过 148 条」；选自由输入能选任务定义文件、临场敲术语与原文、看到模型现写的草稿、敲一句改写后拿到交付物。

**录制来自真模型**：`task_defs/recordings/glossary.json` 里那一条是用「录制」模式对着本机内网的一台 OpenAI 兼容接口服务（llama.cpp 起的，模型名以该服务 `/v1/models` 返回的 `qwen3.8-27B` 为准；地址写在不入库的 `config.json` 里）跑一次术语澄清正常场景录下来的，不是手写的。回放模式下三个术语澄清场景都命中这一条，验证脚本因此在没有模型服务的机器上照样全过。

## F2 开工前的疑问与裁定

开工前一次问齐六条，主会话当天答复（第一条是阻塞项）：

| 疑问 | 裁定 |
|---|---|
| 工具表的两个新字段进不了「任务开始」事件，除非改 kernel.py 那一行（它把每个工具的值写死成参数名列表） | 授权改；随后用户进一步裁定：第四步起零差异哈希断言整体取消，四个内核文件都可以改，每处改动列进本报告由用户逐条看 |
| 出差申请单四个场景退役后，「与第二步旧运行文件逐位比对」那条断言怎么办 | 只保留材料接入登记正常场景那一条，删出差四条 |
| 观测台、JSON Schema、轮数写法三组检查如何精简 | 三组保留；JSON Schema 改为「现存三份好文件全过、三份坏文件被拦」；观测台改用保留场景的运行文件；连跑两次那组删 |
| 控制台菜单编号 0 的范围 | 与 `python3 -m tod_kernel.verify` 完全相同，含全部不算场景的检查组 |
| 提示词与录制用的那段数据逐字定稿 | 三段文本（系统提示、术语与原文片段、使用者的改写）逐字确认，照录，定稿后不改 |
| 模型名、录制路径基准、覆盖录制路径的参数、延迟导入、本机 config.json 不入库、__init__.py 说明、任务标识不改名 | 全部同意 |

追加裁定（2026-09-16 用户）：零差异哈希断言取消；新增第七项「候选并入行动」，六项验证全过之后做。

## F3 内核改动逐处（供用户逐条核对）

`kernel.py` 一共改了两处，都在授权范围内：

1. **「任务开始」事件的工具清单换了形状**（第一项）。原来是 `{name: list(tool.param_names) ...}`，每个工具的值是参数名列表；现在是 `{name: {"param_names": [...], "category": ..., "summary": ...}}`。不改这一行，两个新字段就进不了事件，观测台也就看不到它们。连带改动：`verify.py` 里自己算工具清单的那一行、`observatory.html` 概览里画工具清单那一段（旧运行文件里仍是数组，按数组处理，类别与说明显示为空）。`observe.py` 命令行打印那一行原样不动，它把整个字典打出来，打印文字跟着变，没有断言比对这段文字。

2. **候选并入行动**（第七项）。删掉 `Candidate` 类（`kernel.py` 里现在没有这个名字）；`Action` 兼作候选：字段顺序改为 `tool, params, proposer, basis` 在前，新增 `action_id: int | None = None`（候选时为空，登记时才给编号）与 `cursor_update`（游标新值，原来在候选上）；`ActionStatus` 新增取值 `CANDIDATE = "候选"`，排在「已提出」之前；`select_action` 返回一个状态是「候选」、编号为空的 `Action`，不发任何事件；`register_action(task, action)` 给编号、放进行动表、发「行动提出」、记「已提出」；循环第 4 步改从 `action.cursor_update` 读游标新值。没有加「正式／候选」布尔属性，是候选还是正式行动由状态表达。

**事件流不变的核对**：拿第七项改动前后同一个场景（材料接入登记正常）的运行文件比对，按线程分两列（内核一侧 124 条、宿主在发件箱上的等待 8 条），除时刻与等待毫秒数外逐条相同；新运行文件里没有任何一行出现「候选」二字。

`taskdef.py`、`dialogue.py`、`observe.py` 三个文件本步一个字没改。

## F4 逐项记录

**第一项：工具表的两个新字段。** `tools.py` 里 `ToolSpec` 与 `Tool` 各加 `category`、`summary`；类别取值集写成常量 `CATEGORIES = ("与使用者对话", "访问外部资源", "加工任务数据")`，`ToolTable.register` 登记时核对类别在取值集里、说明非空，不合就抛内核错误。五个既有工具按 4.1 节归类：ask 与告知异常归「与使用者对话」，list_dir、register_file、generate_manifest 归「访问外部资源」。做完既有 1265 条断言一条不改全过，差异只有事件里每个工具多两个键。

**第二项：模型调用件与配置文件。** 新模块 `llm.py`，只用标准库（模型服务走 `urllib`）。对外是 `load_config(path=None)` 与 `make_caller(config, recording_path=None)`，后者返回 `call(Request) -> Reply`。三种模式按 4.2 节：运行与录制向 OpenAI 兼容的对话接口发一次请求（温度 0），回放按请求哈希查录制文件，查不到抛错不编造。请求哈希是 `sha256(json.dumps([system, user, shape], ensure_ascii=False, sort_keys=True))`。`LLMError` 带两样文字：`brief` 是不含请求原文的一句话（给行动说明与运行索引的原因栏用），异常全文在这句之后附上请求原文（便于照着补录或改提示词）。`config.example.json` 入库（模式「回放」，录制文件指向 `task_defs/recordings/glossary.json`），真实 `config.json` 进 `.gitignore`。录制文件路径以 `tod_kernel` 包目录为基准，从哪个目录启动都找得到。

**第三项：「生成术语释义」工具。** 无参数，直接读「术语」「原文片段」两个槽位；前置条件是两者都不为 None 且「释义草稿」为 None；回答去掉首尾空白写进「释义草稿」，行动返回值是模型调用的记录；模型调用抛错时记「已失败」，说明就是 `LLMError.brief` 那一句。类别「加工任务数据」（模型是实现细节，不是交往对象），一句话说明「根据原文片段为术语写一条释义草稿，写入释义草稿」。`build_table` 多一个参数 `call`，只给这个工具用 `functools.partial` 绑上；任务用到它而没给 `call` 时，在建表时就抛可读错误，不等跑起来才失败。提示词定稿写在 `tools.py` 的 `DEFINITION_SYSTEM_PROMPT` 与 `definition_request`，注释里写明改了要重录。

**第四项：术语澄清定义文件与录制。** `task_defs/glossary.json` 照第三步 4.9 节抄（两个固定步骤阶段、四个槽位、一份交付物），加载器与 schema 都通过。录制用「录制」模式对着真模型服务跑一次正常场景得到，见 F1。

**第五项：验证场景与断言。** 场景精简为六个，退役的连代码带数据文件删净：删掉出差申请单四个场景与 `travel.json`、材料接入登记「登记一个问一个」与「回答缺失」两个场景与 `intake_interleaved.json`、异常的重做与主动终止两个分支、后续阶段破坏前面阶段目标那个场景与 `intake_broken_goal.json`、六份坏文件的生成代码、运行期任务定义错误场景、连跑两次那组检查，以及随之没人用的常量与函数（出差答案表、话语预期句三条等）。三份保留的坏文件合并成一个场景函数，使场景数正好六个。新增术语澄清三个场景与两组检查（模型调用件三种模式、控制台的问答打印）。零差异哈希断言按用户裁定删除，原来两处内核机械检查（词表、导入）合并成一个 `check_kernel_is_task_agnostic`，词表扩到三个任务的槽位名与工具名，导入禁令加上模型调用件、加载器与控制台。

**第六项：控制台。** 新模块 `console.py`：宿主循环 `host_loop`、两个应答者（`PresetAnswerer` 按答案表答、`KeyboardAnswerer` 从键盘读）、问答打印（「系统：」「使用者：」「使用者（预设）：」）、菜单、自由输入、`--show-actions`（每个行动结束时打印一行）。`verify.py` 原来内嵌的那套宿主逻辑搬到这里，两边共用；答案表的键 `target_key` 也一并搬过来。为让控制台只显示问答，`verify.py` 加了三个模块级开关：`SHOW_EXCHANGES`（打印问答）、`PRINT_EVENTS`（打印事件流水）、`PRINT_CHECKS`（逐条打印断言），直接跑验证脚本时它们保持原样，打印与第三步一字不差。模块之间只有一处函数内延迟导入（`console` 在跑场景时才导入 `verify`），顶层没有互相导入。

**第七项：候选并入行动。** 见 F3 第 2 条。做完六个场景与五组检查照样全过。

## F5 文档与裁定里不清楚的地方（逐条）

1. **第一项的口子实际必须走。** 第 5 节第一项写「kernel.py 若是从 Tool 对象取清单则不用改，取的方式若写死了字段名则改那一行」。实际是后一种：值的形状写死成了列表，不改就做不到「两个字段进事件、观测台显示」。已请示并获授权。
2. **「加载错误，三份坏文件」是一个场景还是三个。** 第 6 节的场景表把它算作一行，而第三步的代码是每份坏文件一个 Checker。我合成一个场景函数（断言描述前面加上这份坏文件的标题），场景数因此正好六个。
3. **第七项裁定说 `select_action` 在 kernel.py 与 taskdef.py 两处。** 实际上 `taskdef.py` 一侧的 `select_action` 返回的是（工具名, 参数, 依据）三元组或加游标新值的四元组，不构造候选对象；把它包成 `Action` 的是 `kernel.select_action`。所以 taskdef.py 没有可改之处，本步一个字没动。
4. **第七项裁定说「update_state 从行动上读游标新值」。** `update_state` 的入参是变更组或任务状态，不是行动。我的实现是循环第 4 步（状态更新那一步）从 `action.cursor_update` 读，原来是从 `candidate.cursor_update` 读，其余不动，运行文件因此逐条不变。若原意是把这段逻辑搬进 `update_state` 并让它接受行动对象，请指出，我再改。
5. **两个 ask 被跳过的原因，文档有两种说法。** 4.4 节写「因 ask 的前置条件（目标位置已有值）不成立而自动跳过」，4.5 节写「因目标位置已有值被跳过」。两者指同一件事，实现按前置条件；选择经过里记的原因原文是「前置条件不成立：写入目标指向的位置为 None」，断言照这个写。
6. **确认那一问的话里有两个连着的句号。** 定义文件的模板是「对术语「{term}」的释义草稿是：{draft}。请确认或改写。」，而模型写的草稿本身以句号结尾，拼出来就是「……以它进行对照。。请确认或改写。」。模板是第三步 4.9 节定稿的，本步没改。若要改成不带句号的写法，录制不受影响（模板不进请求），但话语预期句与相关断言要跟着改。
7. **回放时记录里的模型名来自配置，不是来自录制。** 4.2 节说记录含模型名，而录制条目的格式（简报 10.1 节）只有请求哈希、请求原文、回答原文三样。所以换了配置里的模型名再回放，记录里会显示新名字，录制文件本身不带模型信息。
8. **示例配置里的服务地址是占位值。** `config.example.json` 写的是 `http://127.0.0.1:8084/v1`，不是真实的模型服务地址：这个仓是公开的，不把本机内网地址写进来。把它切成「运行」模式之前要先改地址；真实地址在不入库的 `config.json` 里。
9. **控制台跑预设场景时不能连事件流水一起刷。** 4.6 节只说「代码留在 verify.py 不动，控制台只是调用」，没说事件流水怎么办。第一版跑出来满屏是 `ConsolePrinter` 的事件，问答被淹没，所以加了 `PRINT_EVENTS` 开关。
10. **自由输入的通用检查里「邮箱关闭」那几条要看情形。** `check_sources_and_senders` 的参数 `closed_before_failure_of` 在「宿主关过收件箱」与「没关过」两种情形下断言的事实不同。自由输入时使用者可能按 Ctrl-D 中途结束，所以由 `console.closed_before_failure(run)` 按实际事件推断后再传进去。
11. **管道喂输入时看不到使用者那一行的回显。** 自由输入用的是 `input()`，回显由终端负责；用管道把回答喂进去（例如我自测时）只看得到「使用者：」提示符，看不到敲进去的内容。真人在终端里用不受影响。

## F6 本步遗留

- 步骤文档 6.1 节的验收记录待主会话填（验收日期、代码版本、工具表与验证脚本差异摘要、负责人三问的回答、观测台截图挂链）。观测台截图四张（术语澄清场景一的工具清单、模型不可达那次运行的原因栏、控制台自由输入那次运行、运行索引，均为 1280 宽）已交主会话，不放在这个仓里。
- 验证目标五「负责人在控制台亲手跑一次术语澄清」我代跑了一次（运行模式、自由输入、术语「验收基线」），四问四答跑通、交付物拿到、通用检查 40 条全过；负责人自己那一遍与三问的回答仍待做。
- `runs/` 里第二步留下的旧运行文件只剩材料接入登记那一份还被断言用着，这条历史锚点仍然只在本机成立。

## F7 追加批次：上下文包、有界修改循环与判读回复（2026-09-16）

### F7.1 结论

用户 2026-09-16 的十一条裁定所对应的六件事都已做完（观测台改版按约定另算一次交付），验证脚本 `python3 -m tod_kernel.verify` 六个场景与六组检查共 **576 条断言全部通过**：材料接入登记正常 148 条、材料接入登记目标写错 86 条、加载错误三份坏文件 21 条、术语澄清给全初始输入一次确认 91 条、术语澄清只给术语一次修改后确认 121 条、术语澄清模型不可达 45 条、观测台 22 条、任务定义 JSON Schema 8 条、告知异常话的轮数写法 6 条、模型调用件三种模式 9 条、上下文包与对话历史三条规则 14 条、控制台的问答打印 5 条。

**系统提示逐字对得上定稿**：代码生成的系统提示与负责人给的定稿文本逐字相同（用脚本比对过，无差异）。**录制来自真模型**：`task_defs/recordings/glossary.json` 里 6 条，是用「录制」模式对着本机内网那台 OpenAI 兼容服务（模型名 `qwen3.8-27B`）跑场景一与场景二录下来的——场景一 2 条（写首稿、判读确认），场景二 4 条（写首稿、判为修改、按意见改稿、判读确认）。

### F7.2 模块边界与接线

- `llm.py`：只认识系统提示、用户内容、输出形状三样东西与三种模式，另存三段所有任务共用的固定文字（角色句式、上下文约定、通用输出规矩）与系统提示的拼装函数。它不认识任务定义，也不认识事件。
- `taskdef.py`：任务定义摘要（`summary_text`）、可选块「领域规矩」的校验与取用、静态谓词文字（`predicate_text`、`operand_text`）、阶段目标求值（`stage_goal_holds`）。
- `tools.py`：工具目录（`tool_catalog`，从静态工具表按任务过滤，内部调模型的工具附固定指令）、系统提示装配（`system_prompt_for`）、两个内部调用模型的工具、建表时把模型调用件与读事件函数绑上。
- `context.py`（新模块）：上下文包。六种段、段头【段名 · 来源】、对话历史三条规则、修订记录推导、系统标记。
- 事件从哪来：宿主（验证脚本与控制台）本来就持有内存收集器，建工具表时把「读事件」函数交给工具表，内核不为此改动。代价是今后任何宿主要用这两个工具都得挂一个收集器。
- 顶层导入无环：`context` 顶层导 `kernel` 与 `taskdef`；`tools` 在工具实现与装配函数里按需导入 `context` 与 `taskdef`（`taskdef` 本来就导入 `tools`，顶层导会绕回来）。

### F7.3 内核与既有文件的改动逐处（供用户逐条看）

`kernel.py` 本批次一个字没改（上一批的两处仍在：任务开始事件的工具清单换形状、候选并入行动）。

`taskdef.py` 三处：一是顶层多认一个可选块「领域规矩」，校验它是非空字符串并放进 DEFINITION；二是新增给系统提示用的摘要生成（`summary_text` 与它用到的 `predicate_text`、`operand_text`、`_group_rounds_text`、`domain_rules_of`），这些只读定义、不参与执行；三是 `TaskDefinition` 新增 `stage_goal_holds(stage_name, data)`，上下文包写「任务进度」段时要判当前阶段目标成立没有，原来只有整任务的 `is_done`。

`tools.py`：术语澄清那一段整体重写成两个工具（「生成术语释义」首稿与改稿同一实现，新增「判读回复」）；两个前置条件按 4.3 节改写；静态工具表两项；新增工具目录与系统提示装配；`build_table` 多两个参数（`call`、`read_events`）并把模型调用件、任务定义、读事件函数、系统提示一起绑给这两个工具；「告知异常」的一句话说明按用户裁定改写。

`llm.py`：新增系统提示的三段固定文字与 `build_system_prompt`、`text_hash`；调用记录按 4.8 节的形状（第一次记系统提示全文，之后记「同任务系统提示」并带哈希，另有用户内容、输出形状、返回原文）；`Request` 多一个 `json_schema` 字段，输出形状为 JSON 时作为 `response_format` 交给模型服务（本机 llama.cpp 支持 `json_schema`，实测中文键正常）。

`console.py`：预设应答者的答案表值扩成「字符串或字符串列表」，列表按次序一轮一句。`verify.py`：三个术语澄清场景按第 6 节重写，新增上下文包一组检查，建工具表时接上读事件函数。`task_defs/glossary.json` 按 4.4 节全文重写，schema 加可选块「领域规矩」。

### F7.4 逐件记录

**第一件：定义文件。** 去掉「问原文片段」那一步（系统不向使用者索要原文，它是可选上下文，由启动任务的程序在启动时给）；「确认」阶段改成步骤组（问回复 → 判读回复 → 按修改意见改稿，重复直到确认释义不为空，最多 5 轮）；槽位加「回复」「修改意见」；念草稿那一问的模板在草稿后面不再跟句号；加「领域规矩」块。

**第二件：上下文包与系统提示。** 系统提示六段任务级一份，任务启动时拼好，第二次起调用记录里只写哈希。用户内容六种段按固定顺序组装，段头【段名 · 来源】。对话历史三条规则：按范围取（默认当前阶段，工具可扩到与某几个槽位相关或整个任务）、回答原样留在槽位里的轮不重复装（最近一轮恒装）、预算 800 字从最早整轮裁并留【更早 n 轮已省略】。修订记录从数据变更事件推出（每写一次释义草稿记一行「第 n 稿」，每写一次修改意见记一行「使用者意见」）。回放哈希算在（系统提示、用户内容、输出形状）三样上。

**第三件：两个工具。** 「生成术语释义」首稿与改稿同一实现，靠「修改意见」是不是空分支，改稿后把修改意见清空；前置条件是术语不为空且（释义草稿为空或修改意见不为空），原文片段为空不是障碍。「判读回复」输出 JSON，确认就写确认释义、修改就写修改意见，两种都清空回复；解析失败、决定不在两个取值内、确认时确认文本为空、修改时修改意见为空，四种都记已失败并带模型原文，不重试。

**第四件：录制。** 见 F7.1。录制文件按裁定走旧形状（请求哈希、请求原文、回答原文三样），段列表与解析结果只进行动返回值。

**第五件：验证。** 三个术语澄清场景断言按第 6 节写：场景一三个行动（依据序号 2、3、4，问术语那步被跳过）、场景二六个行动（依据序号 2、3、4、5、3、4）、场景三模型不可达。上下文包那组 14 条不跑任务，手写数据与事件，把六种段的顺序与段头格式、对话历史三条规则各一例、修订记录推导、系统提示六段与哈希复用逐条验出来。

**第六件：控制台。** 路径照旧，只把预设应答者扩成能按轮给不同回答。自由输入用真模型跑通一次：系统只问术语没问原文，使用者提一条修改意见后出第 2 稿，确认后拿到交付物，通用检查 49 条全过。

### F7.5 与文档、原型的出入（逐条）

1. 场景一的调用条数：文档原写 1 条，实际 2 条（写首稿与判读各一次），主会话已改文档。
2. 录制文件形状：4.8 节说「录制文件每条存同样的形状」，简报 10.1 仍是旧形状；裁定走旧形状，段列表与解析结果只进行动返回值。
3. 改稿那次对话历史的范围：原型写「与槽位『释义草稿』『修改意见』相关」，但轮次是按「回答写进了哪个槽位」标记的，念草稿那一轮写的是「回复」，所以实现为「与槽位『回复』相关」。已改文档。
4. 修订记录的行数：第 6 节写 1 行，按原型算法是 2 行（第 1 稿一行、使用者意见一行）。已改文档按 2 行。
5. 任务定义摘要与工具目录由代码生成，措辞比原型手写的长：槽位说明取定义文件原话，工具那半句取工具表的一句话说明。
6. 系统提示的段头用【段名】两行式，与用户内容的【段名 · 来源】区分开：系统提示的段没有「来源」这一说。
7. JSON 的接口参数形状不进请求哈希：同一个输出形状下它是固定的，形状名已经把差别表达清楚。
8. 场景一的游标终值是「已完成第 4 步、该组已完成 0 轮」而不是空：第 1 轮判读即确认，组的「重复直到」当场成立，这一轮没走完。
9. 模型理解自检暴露的六处措辞（角色句式补一句、摘要里「槽位」首现加解释、「宿主」改「启动任务的程序」、步骤组轮数改白话、告知异常的说明改写、判读回复的固定指令补 JSON 形状）已按用户裁定改完，生成结果与定稿附录逐字相同。

### F7.6 本批次遗留

- 第八项观测台改版（4.9 节与 v6 原型）按约定作第二次交付，尚未开工。
- 「读事件」函数由宿主绑给工具：今后任何宿主要用这两个工具都得挂一个收集器，已记进步骤文档第 7 节。
- 提示词一改，录制文件全部失效要重录；这一批就因为六处措辞调整重录过一次。

## F8 第八项：观测台改版（2026-09-16）

### F8.1 结论

按第四步文档 4.9 节与协同记录原型 v6 改完，`python3 -m tod_kernel.verify` 仍是 576 条断言全部通过（观测台那组检查测的是服务的三个接口，不测页面内容，页面由人看）。改完的页面从上到下是：任务与分工、任务概览、交付物、路线图、路径图、时间轴、这次任务的系统提示、上下文变化、协同记录（左）与此刻的状态（右）。运行索引与对比视图原样保留。

### F8.2 做了什么

**新增「这次任务的系统提示」**：从第一条模型调用记录里取系统提示全文，按【段名】切成六段展示，末尾写出这份提示的哈希并说明第二次起记录里只写「同任务系统提示」。

**新增「上下文变化」**：一列一次模型调用（列头是第几次迭代、哪个工具、什么输出形状），行是六种段类型，格子是那一次装进去的内容（表里显示截断并标明模型看到的是全文），黄格＝比上一次调用变了，末行是模型写回了什么（每个键写到哪个槽位，加返回原文摘要）。

**协同记录**（原迭代视图改造）：卡左边色条＝这次谁参与（蓝只有系统、青问了使用者、橙工具内调了模型、红告知异常）；工具内部调模型的卡在「做了什么」那一行展开两块——「提示词构成」按段逐字展示（左色条＝内容从哪来，黄＝比上一次调用变了，不截断），「模型返回」是原文折叠加解析结果表（每一项标出写到了哪个槽位）；连着两次以上只有系统自己在做的迭代压成一行「系统自行完成 n 次迭代（第 a 到第 b 次）」，点开看细节；卡头「机制」的五步叙事、脚注与就地展开的事件与第三步一字不减。

**此刻的状态**：从时间轴节里挪到协同记录右边，做成随页面滚动的侧栏，四块——此刻在哪（游标）、这次迭代的行动、阶段目标（此刻成立与否，前端按三个谓词自己求值）、槽位（标签＝谁填的，变过的标黄）。窄屏改成上下堆叠。

**任务与分工**：页面最上面三张卡，系统自行完成几次迭代、使用者答了几次（系统问了几次、等待共几秒）、模型经工具调用几次（按工具分、合计几秒）。全部从事件与调用记录数出来。

**删掉两块**：槽位轨迹与行动卡片，它们的内容已由右栏的槽位表与协同记录承担（4.9 节的裁定）。

**联动**：拖时间轴、点路径图、点路线图的步骤格、点协同记录的卡，右栏与另外几处一起跳到那一刻。

### F8.3 能推的都推了，推不出的逐条列出

从数据推出来的：分工统计（问答次数、等待时长、模型调用次数与耗时）、谁填的（数据变更事件的来源行动 → 行动的工具 → 工具的类别三步推出，工具内部调过模型的记「模型」）、段「变了没有」（同一次运行里按调用顺序逐类型比对，记录里不预存）、阶段目标此刻成立与否（前端按不为空、相等、列表无项为空三个谓词求值）。

原型里有而这次没做或留空的，逐条：

1. 页面顶栏那句「全程 61.3 秒 · 运行模式（模型服务 qwen3.8-27B）」没做，模式与模型名只出现在每张模型卡的折叠标题里。
2. 交付物卡片下面那句叙述（「原文片段未提供，模型按需求工程通用含义解释；第 1 稿被使用者要求修改，第 2 稿确认」）没做：它能推，但措辞是术语澄清这一个任务特有的，做成通用的会变成一句谁都看不懂的话，留给以后按任务定义配。
3. 路线图的步骤格没有按「谁做的」着色、也没有标次数，仍是第三步的样子（阶段格标目标达成、步骤组画回环与上限）。
4. 时间轴与路径图沿用第三步的实现，没有按 v6 重画。
5. 「重做退回画红色虚线折返箭头」这条没法在这次的场景里看到：会重做的那个场景（告知异常选重做）在这一批退役了，六个场景里没有一次运行走过重做。画折返的代码是第三步写的，仍在。
6. 「高亮」控件沿用第三步的过滤器（只变淡不隐藏），没有按 v6 重做成三个控件。

### F8.4 旧运行文件与两种宽度

旧运行文件（第二步留下的 T-intake-1）打开正常：十一张卡都在，系统提示与上下文变化两块因为没有模型调用记录而不画，槽位的「谁填的」标签因为旧的工具清单是数组、没有类别而留空，页面没有报错。1280 与 400 两种宽度都实测过，`scrollWidth - clientWidth` 都是 0，没有横向溢出；400 宽时协同记录与右栏改成上下堆叠，上下文变化表在自己的容器内横向滚动。

截图四张在设计档案仓步骤目录：任务与分工与交付物、上下文变化、协同记录与此刻的状态、窄屏协同记录（前三张 1280 宽，末一张 400 宽）。

### F8.5 第八项的四处补充（2026-09-16 用户复核后）

1. **路线图按 v6 重画**：步骤格按这一步是谁做的着色（蓝系统、青问了使用者、橙工具内调了模型），没到过的仍是灰虚线、被跳过的是空心虚线；右下角标执行次数「×n」；阶段格照旧按目标达成与否着色；步骤组的框底文字补上实际轮数，写成「最多 5 轮（实际 2 轮）· 重复直到目标成立」，没进过组的写「（这次没进过组）」；结束旗照旧完成绿、终止红；折返箭头代码保留，颜色从橙改红。角色是从行动的依据序号（步骤号）与工具类别推的：同一步骤的角色恒定，取它第一次执行时那次迭代的角色。
2. **路径图按 v6 重画**：点按角色着色（蓝、青、橙），告知异常仍是红色菱形，重做退回仍是红色虚线，拖杆与卡片的联动照旧。图例与说明文字跟着改（原来写「向上的橙色虚线」，橙色现已让给模型）。
3. **顶栏**：面包屑后面补一句「全程 n 秒 · X 模式（模型 Y）」。全程是任务开始到最后一条事件的时间差；模式与模型名取自第一条模型调用记录，这次运行没在工具里调模型就只写全程。
4. **「谁填的」的一处修正**：「判读回复」写进「确认释义」的是使用者的裁定，模型只是把它从那句回复里认出来，所以标「使用者」。做法是工具表上加一个可选映射（`ToolSpec.writer_roles`：槽位 → 来源角色），只给「判读回复」登记了「确认释义 → 使用者」，其余照原来的三步推。这个映射随工具清单进「任务开始」事件（没登记的工具不带这个键），观测台优先查它。连带改动：`kernel.py` 工具清单那一处（本批次内核第三处改动）、验证脚本自己算的那份工具清单。

配色顺带整理了一次：三个角色色（系统蓝、使用者青、模型橙）与它们的浅底做成主题变量，浅色深色各一套，页面里不再有写死的颜色；原来「重做退回」用的橙色与模型的橙撞了，按 v6 把它改成红色。

截图七张（都在设计档案仓步骤目录）：任务与分工与交付物、路线图与路径图、系统提示、上下文变化、协同记录与此刻的状态、窄屏协同记录（400 宽），以及一张异常运行的路线图与路径图。复跑验证脚本仍是 576 条断言全部通过；400 宽横向溢出仍是 0。

一处留给用户定：「回复」这个槽位最后一次是被「判读回复」清空的，所以右栏标的是「模型」。按同样的道理它也可以登记成「使用者」（里面的话本来就是使用者说的），但裁定里只点了确认释义这一处，我没有自作主张加。

## F9 第九项：运行有向图（2026-09-16）

### F9.1 结论

第四步文档第 4.9 节「运行有向图」段与第 5 节第九项已做完。观测台里原来的「路线图」与「路径图」两块删掉了，换成一块「运行有向图」：任务定义画成一张流程图（大底框是阶段，圆角框是步骤，菱形是步骤组进组前看的停止条件），这次运行的结果以标注的形式叠在这张骨架上。骨架完全由代码从任务定义自动布局，页面里没有一处写死的坐标。

本项没有改内核：`kernel.py`、`taskdef.py`、`tools.py`、`llm.py`、`context.py`、`console.py`、`verify.py` 一个字都没动，改的只有 `observatory.html` 一个文件。验证脚本复跑仍是 576 条断言全部通过（六个场景加不算场景的六组检查），结论行是「全部断言通过」。

### F9.2 骨架怎么布局

一个阶段一个大底框，底框内部按任务定义里步骤的先后从左到右摆：普通步骤摆一个圆角框；步骤组先摆一个菱形（菱形里写这个组的「重复直到」条件），菱形后面依次摆组里的各步，组尾再用一条回边接回菱形。框宽按里面的字算，最窄 140、最宽 210 像素，字放不下时截断并把全文放进悬停提示。阶段底框的左右各留 18 像素边距，阶段之间隔 34 像素。整条主轨在同一条水平线上（纵坐标固定 150），起点是一个「始」圆点，终点是一面旗。

四种线各走各的道，互不打架：主轨向右走直线；回边从组尾底部出发，沿阶段框下方绕回菱形底部；旁路（被跳过的步骤）从被跳步骤的前一个节点顶部出发，走阶段框上方越过它，落到后一个节点；出循环的绿边从菱形顶部出发，沿整张图的顶部（纵坐标 34）绕到目标节点。告知异常挂在主轨下方 220 像素处，一个报过异常的阶段占一排。阶段标题连同它的浅色底块在所有边画完之后才画，所以标题永远压在边之上（第 4.9 节明写的规则）。边的标签也统一在所有边画完之后画，否则后画的边会从先画的标签上穿过去——异常那次运行的回边标签一开始就被重做的红色虚线划了一道，是这样修好的。

阶段底框的颜色按这次运行的结果定：目标达成绿、目标未达成红、没到过灰虚线、游标停在这里保持默认色。没到过的阶段里，步骤框与边一律画成灰虚线。

### F9.3 运行结果这一层，四样数据从哪来

**步骤框右下角的迭代号圆点**：遍历这次运行的每一次迭代，取它那个行动的依据序号（也就是任务定义里的全局步骤号），把迭代号记到对应的步骤框上。圆点的颜色就是这次迭代谁参与：只有系统蓝、问了使用者青、工具内调了模型橙。这个判断复用了协同记录已经在用的那个函数（`iterationRole`：看这次迭代有没有发过问题、行动的返回值里有没有模型调用记录），不是另算一套。行动已失败的那次，圆点另加一圈红边。框内左下角写「走了 n 次」或者「没到过」。

**判断菱形旁的判断结果序列**：判为「否」的几次，是这个步骤组每一轮的头一次迭代——按迭代顺序扫，某次迭代走的步骤号不比上一次大（或者是第一次进组），就算新的一轮开始。判为「是」的几次，是这一趟行动选择越过了整个组的那几次：要么这次选中的是组后面的步骤，要么这次报了告知异常而且异常依据里的「异常种类」是「一趟走完」。另外补一次：任务完成时的那一次判断没有留下行动（结果检查为真之后内核不再做行动选择），但它确实判过，所以阶段目标已达成而且组跑过至少一轮时补一个「是」。两串按迭代次序并起来，四次以内逐次写「第 1 次否 · 第 2 次否 · 第 3 次是」，多于四次把连着同结果的几次并成一段写「第 1 到 3 次否 · 第 4 到 6 次是」。

**旁路与它的原因**：有两种来源。一种在运行数据里有留痕——行动依据的命中值里带着「选择经过 › 跳过步骤」，每条是一个步骤号加一句原因，原因照抄留痕原文（例如「前置条件不成立：写入目标指向的位置为 None」），边上标明是哪一次迭代的行动选择里跳的。另一种没有留痕——步骤组里某一步的执行次数比轮数少，说明有那么几轮没走到它，而任务在再次做行动选择之前就结束了，所以「选择经过」里没有这一条。这种情况下原因取上一个行动返回值里的判读结果（模型调用记录的 `parsed` 字段里那个短字符串，术语澄清这里就是「确认」），写成「上一步判为「确认」，停止条件成立 → 余下步骤不走」；取不到判读结果就只写「停止条件成立 → 余下步骤不走」。页面不重算前置条件——那等于把内核的判断逻辑抄一份到页面上。

**告知异常、重做与终止**：报过异常的阶段，在它下方摆一个「阶段目标：⋯？」菱形与一个红色的「告知异常」菱形，红菱形里带着报异常的那几次迭代号。菱形之间的边标「否 · n 次 → 告知使用者」加迭代号。使用者选了重做的那几次，从红菱形拉一条红色虚线回到这个阶段的起点（阶段以步骤组开头时就是回到那个判断菱形），标「使用者选「重做本阶段」· n 次（#5、#6 之后）→ 回到判断」。使用者选了终止的那次，从红菱形拉一条红边到红旗，标「选「被动终止」· 1 次（#7）」。重做与终止都取自告知异常这个行动的返回值（返回值就是使用者选的措施）；第 4.9 节写的是「重做取游标写回事件」，两者是同一件事的两个面——使用者选重做，内核随即把游标写回该阶段起点，我取的是前者。

### F9.4 与原型逐项核对：术语澄清那次

核对用的运行文件是 `runs/T-console-glossary_20260915T203944082.jsonl`（控制台自由输入跑的那次，七次迭代，行动序号依次是 1、2、3、4、5、3、4），原型 `原型_运行有向图_v1.html` 的第一张图画的就是这一次。这是主会话 2026-09-16 的裁定：第四步文档第 5 节第九项写的「回边在 #5 之后、旁路在 #7 之后」对应的是这次运行，不是验证脚本里的场景二。

逐项比对下来，骨架与标注与原型一致：

| 原型上的东西 | 页面画出来的 |
|---|---|
| 阶段「写释义草稿」，绿底，✓ 目标达成，目标：释义草稿 不为空 | 一致 |
| 阶段「确认」，绿底，✓ 目标达成，右上角 ↻ 一句重复说明 | 一致（原型写「这三步会重复：最多 5 次，走了 2 次」，页面写「这 3 步会重复：上限是 5 轮，走了 2 轮」） |
| 步骤「问使用者要澄清的术语」，青边，走了 1 次，圆点 1 | 一致 |
| 步骤「根据术语⋯生成释义草稿」，橙边，走了 1 次，圆点 2 | 一致 |
| 阶段之间的边标「目标达成」 | 一致 |
| 菱形「确认释义 不为空？」，旁注「第 1 次否 · 第 2 次否 · 第 3 次是」 | 一致，一字不差 |
| 菱形到第一步的边标「否 · 2 次 / #3、#6」 | 一致 |
| 步骤「念草稿，请使用者确认或提修改意见」，青边，走了 2 次，圆点 3 与 6 | 一致 |
| 步骤「判读回复」，橙边，走了 2 次，圆点 4 与 7 | 一致 |
| 步骤「按修改意见改稿」，橙边，走了 1 次，圆点 5 | 一致 |
| 蓝色回边标「走完一次，回到判断 · 1 次（#5 之后）」 | 一致 |
| 灰色旁路越过「按修改意见改稿」，标「⋯ 1 次（#7 之后）」 | 一致；原因那半句按主会话裁定改写成「上一步判为「确认」，停止条件成立 → 余下步骤不走」（原型写的是「判为「确认」→ 改稿跳过」） |
| 绿边绕图顶到绿旗，标「是 · 第 3 次判断 → 阶段目标达成 → 完成」 | 一致 |

两处与原型不同，都是有意的：一是旁路原因那半句，理由见上一格；二是原型把步骤名手工缩短了（例如「根据术语生成释义草稿」），页面是按定义里的原文自动截断加省略号，悬停能看全文。

验证脚本里的场景二（「术语澄清，只给术语，一次修改后确认」，运行文件 `runs/T-glossary-2_*.jsonl`）另外核对了一遍。它只有六次迭代：术语由初始输入给全，第 1 步的 ask 被前置条件挡下，所以图上多一条越过第 1 步的灰色旁路，原因写的是留痕原文「前置条件不成立：写入目标指向的位置为 None」；回边标的是「1 次（#4 之后）」，改稿那条旁路标的是「1 次（#6 之后）」，出循环仍是「是 · 第 3 次判断 → 阶段目标达成 → 完成」。这些数与这次运行的事实对得上。

### F9.5 与原型逐项核对：材料接入登记报异常那次

核对用的运行文件是 `runs/T-exception-1_20260915T181101353.jsonl`（七次迭代：列目录一次、登记三次、告知异常三次，其中两次选重做、最后一次选被动终止），原型的第二张图画的就是这一次。这也是主会话的裁定：会重做的那个场景已在第五项随场景退役，验证脚本里现在保留的异常场景不走重做，要与原型逐项对照只能打开这份旧运行文件。

逐项比对：四个阶段底框依次是「列目录」绿、「登记」红（✕ 目标未达成）、「确认」灰虚线（没到过）、「生成清单」灰虚线；步骤框、迭代号圆点 1 与 2、3、4 都对；菱形「登记进度 等于 文件总表 的长度？」旁注「第 1 到 3 次否 · 第 4 到 6 次是」（原型写「3 次否 · 之后 3 次是」，是同一件事的两种写法）；蓝色回边标「走完一次，回到判断 · 3 次（#2、#3、#4 之后）」；从菱形落到下面那排的边标「是 · 3 次 / #5、#6、#7」；「阶段目标：登记进度 等于 99？」菱形旁注「否 · 3 次」；到红菱形的边标「否 · 3 次 → 告知使用者 / #5、#6、#7」；红菱形里带 5、6、7 三个迭代号；红色虚线回边标「使用者选「重做本阶段」· 2 次（#5、#6 之后）→ 回到判断」；红边到红旗标「选「被动终止」· 1 次（#7）」；登记到确认之间一条灰虚线标「没走到」。与原型一一对得上。

与原型的一处不同：原型给没到过的「确认」阶段只画了一个步骤框，页面按定义自动布局，把它的步骤组停止条件菱形也画了出来（那个阶段的那一步带着「重复直到」，本来就是个单成员的步骤组）。骨架从定义来，这是对的。

验证脚本里现在保留的异常场景（`runs/T-exception-3_*.jsonl`，五次迭代，直接选被动终止）也核对过：图上没有红色虚线回边（这次运行确实没重做过），红菱形里只有一个迭代号 5，落到下面那排的边标「是 · 1 次 / #5」，红边到红旗标「选「被动终止」· 1 次（#5）」。

### F9.6 联动、旧运行文件与两种宽度

时间轴拖到第 k 次迭代，图上第 k 个迭代号圆点加深描边，它所在的步骤框同时加粗；实测拖到第 3 次时高亮的是圆点 3、加粗的是第 3 步的框。点图上的一个步骤框，跳到协同记录里它的第一张卡；点某个迭代号圆点，跳到那一次迭代。跳过去的那张卡如果被压在「系统自行完成 n 次迭代」那一行里，现在会先把它展开再滚过去——实测点「登记下一个文件」这一步，压行块从收起变成展开，时间轴跟着跳到第 2 次迭代。

旧运行文件分两类，都打开正常，没有报错：第二步、第三步留下的、「任务开始」事件里带 `definition` 的文件（例如 `T-intake-1`、`T-scenario-1`），骨架照常画；更早的、只有依据说明没有 `definition` 的文件（`runs/` 里有 557 份，例如 `T-exception-1_20260914T203855579.jsonl`），阶段与步骤靠解析依据说明的文字得到，没有阶段目标也没有步骤组，所以图上没有停止条件菱形，阶段底框第二行写「这份运行文件里没有阶段目标」，异常那个菱形写「阶段目标 / 达成了吗？」，落到它的那条边标「一趟走完，阶段目标仍未达成」。

1280 与 400 两种宽度都实测过：两次运行的页面 `document.documentElement.scrollWidth` 都等于视口宽，没有横向溢出；图比容器宽时在自己的容器内横向滚动（`scrollWidth > clientWidth` 为真）。

### F9.7 删掉的东西

删掉的有：`routeHTML`（路线图）、`pathChartHTML`（路径图）、只给路线图用的 `stageTitleParts` 与 `routeGroupText`，以及它们的三十七条样式规则。（**2026-09-17 用户裁定：路径图恢复**，见 F13.1；路线图不恢复。）连带清掉了只为路径图存在的死代码：`parseDefinition` 里按行编号的 `rows`／`rowOf`／`firstRow` 三张表、迭代对象上的 `row` 字段、`markRoles` 里只给路线图算的 `model.roles` 与 `model.rounds` 两张表（`markRoles` 现在只做一件事，标出每次迭代谁参与）。运行索引与对比视图按第九项的要求保留，对比视图实测正常。

跟着改的说明文字：时间轴那句话（原来写「路线图、路径图与协同记录一起高亮」）、任务概览里没有「任务开始」事件时那句话（原来写「画不出路线图、路径图与时间轴」）。

有一处随路线图一起没了：路线图的结束旗旁边原来有一个「交付物 n 份 ↑」的链接，点它跳到交付物那一块。有向图的旗子旁边没有放这个链接（原型上没有），交付物本来就在有向图正上方。主会话 2026-09-16 裁定：不加回。

### F9.8 复核后的一处修改与两条遗留

主会话 2026-09-16 复核，第九项验收通过，同时定了三件事，一件改了、两件记在这里。

改的一件：**同一个步骤走过多次时，迭代号圆点改成从左往右按次序读**（最早的一次在最左，整串仍靠框的右下角对齐）。原来是照原型的画法从右下角往左排，所以「念草稿」那一步看上去是「6 3」；现在是「3 6」，「判读回复」是「4 7」。主会话说原型那样排是画原型时的偷懒。改完复跑验证脚本仍是 576 条断言全过，四张截图也都按新排法重拍了。

两条遗留：

1. 旁路每被跳过一次画一条边。同一步连着被跳过好几次时，这几条边会重叠在一起（标签上写的是合计次数，所以信息不丢）。目前六个场景里没有出现这种情形，主会话裁定先记在遗留里，出现了再改。
2. 自主规划阶段本步没有运行数据（自主规划的运行是下一步的事）。代码里按「一个可选工具画一个框」处理，框里写工具名，能画出来但没有真实运行验证过。

## F10 模型服务切换到 192.168.213.11:8080（2026-09-16）

按 2026-09-16 用户裁定，模型服务从 116 的 8084 换成 192.168.213.11 的 8080。改的是本机配置文件 `tod_kernel/config.json`（不入库，在 `.gitignore` 里）：`base_url` 改成 `http://192.168.213.11:8080/v1`，`model` 改成 `qwen3.8-27b`。模型名以该服务 `/v1/models` 的返回为准，实际返回的是小写 b 的 `qwen3.8-27b`。

入库的示例配置 `config.example.json` 里，模型名同样改成 `qwen3.8-27b`，端口从 8084 改成 8080；地址仍写 `127.0.0.1`（本机回环）而不是那台服务器的局域网地址——这个文件要进公开仓，示例里不该带内网地址，真实地址只写在不入库的 `config.json` 里。示例的模式仍是「回放」，所以没有模型服务的机器照样跑得动验证脚本。

录制文件没有重录：请求哈希只按系统提示、用户内容、输出形状三样算（`llm.request_hash`），与服务地址和模型名无关，换服务不影响命中。代码文件里本来就没有写死过地址或模型名：在 `tod_kernel/` 下搜 `qwen`、`8084`、`192.168`，只命中两份配置文件本身（`config.json` 与 `config.example.json`），`.py` 与 `observatory.html` 一处都没有。

实测：用运行模式跑了一次控制台自由输入（术语澄清，术语给「验收基线」，模型现写了一条释义，敲「可以，就这样。」确认）。终端先打印「配置：⋯；模式 运行；模型服务 http://192.168.213.11:8080/v1；模型 qwen3.8-27b」，跑完打印出交付物，通用检查通过 37 条。这次运行在观测台里打开也正常，顶栏那句写的是「全程 10.1 秒 · 运行模式（模型 qwen3.8-27b）」，运行有向图照常画出来（术语由初始输入给全，所以第 1 步被跳过，图上有相应的灰色旁路）。

## F11 第九项与模型服务切换之后的截图

四张，都在设计档案仓步骤目录：

- `截图_第四步观测台改版_运行有向图_术语澄清_1280.png`：1280 宽，控制台那次术语澄清运行的有向图，页面上看到的样子（图比容器宽，右边一段要横向滚动才看得到）。
- `截图_第四步观测台改版_运行有向图_异常_1280.png`：1280 宽，材料接入登记报异常那次运行的有向图。
- `截图_第四步观测台改版_运行有向图_术语澄清_全图.png`：把浏览器窗口拉宽到 1560，同一张图完整地看一眼，为的是与原型逐项对照（1280 的那张右边被切掉了，对不了「绿边到旗」那一段）。
- `截图_第四步观测台改版_运行有向图_异常_全图.png`：同上，窗口 1880 宽。

后两张是我自己加的，第九项的派发里只要求前两张；要是嫌多，删掉后两张不影响交付。

## F12 第十项：用「当前步」取代「游标」（2026-09-16）

### F12.1 结论

第四步文档第 4.10 节与第 5 节第十项已按裁定做完。任务进行到哪这件事，从任务数据里的一个保留槽位「游标」，改成任务对象上的一个字段「当前步」，形状沿任务定义的三层写（阶段 › 循环 › 步骤），有自己的状态事件「当前步变化」，由任务定义提供五个接口来记录与显示。验证脚本从 576 条断言增加到 601 条，全部通过；新增一组「第四步：当前步的记录、判据与显示」共 20 条。

跑一遍 `python3 -m tod_kernel.verify` 的结果：材料接入登记正常 149 条、材料接入登记报异常 88 条、加载错误 21 条、术语澄清场景一 92 条、场景二 122 条、模型不可达 45 条、观测台 23 条、JSON Schema 8 条、告知异常话的循环次数写法 5 条、当前步 20 条、模型调用件三种模式 9 条、上下文包 14 条、控制台打印 5 条，结论是「全部断言通过」。

### F12.2 内核改动逐处（本项九处，请逐条看）

1. **事件名多一个**：`STEP_CHANGED = "STEP_CHANGED"`，并登记进 `STATE_EVENT_NAMES`（状态事件、历史正本、进重放）。载荷是 `{old, new, source, text, view}` 五个键：键与其余事件一样用英文（键是标识符，值才是中文），`text` 是任务定义给的那句人话，`view` 是它的结构化投影。
2. **更新组里多一种项**：新增 `StepChange(new, source)` 数据类，与 `Change` 并列，由同一个写入口 `update_state` 写入。这样一次行动带来的数据变更与当前步变更在同一次调用里写完，事件流里不会出现半截状态。
3. **新异常类 `DefinitionError(reason)`**：任务定义自己检出的错误（例如当前步结构不合法）抛它，内核捕获后发「任务定义错误」追踪事件、再抛内核错误。内核不判断原因内容，只把那句话原样带走。放在 kernel.py 而不是 taskdef.py，因为它定义的是内核与任务定义之间的约定，而内核不导入任何任务定义模块。
4. **`Action` 删掉 `cursor_update` 字段**：位置不再由行动携带。
5. **`Task` 加 `step` 字段**：当前步，形状由任务定义定，内核只保管与传递，不解读内容。
6. **`update_state` 的更新组分支认 `StepChange`**，另加私有函数 `_write_step`：真变了才发事件；事件里的 `text` 与 `view` 向任务定义的 `step_text`、`step_view` 取回原样放入（内核仍不解读内容，与它已经把 `definition`、`SLOTS` 原样放进「任务开始」事件是同一种做法）；行动编号是数字时事件挂在那个行动上。
7. **`select_action` 改三处**：调用改成 `select_action(数据只读视图, task.step)`；捕获 `DefinitionError` 转成任务定义错误；返回值只认（工具名, 参数, 依据）三元组，四元组那条分支与「游标新值」的校验删掉。
8. **`ExecContext` 加 `step` 字段**，`execute` 组装上下文时把 `task.step` 的深拷贝放进去。工具拼上下文包的「任务进度」段要用它——位置不在任务数据里了，工具再也拿不到。
9. **循环本体改两处**：初始化之后发一条来源「初始化」的当前步变化（否则靠重放看历史的人拿不到起点）；第 4 步状态更新调 `task_def.record_step(当前步, 行动)`，把结果与工具的变更组放在同一个更新组里一次写入。

kernel.py 里现在搜不到「游标」「cursor」，taskdef.py 也搜不到；验证脚本里加了一条断言看着这件事。

### F12.3 任务定义这一侧：五个接口

- `INITIAL_STEP`：第一个阶段的起点，只有「阶段」这一层。
- `select_action(data, step)`：先把当前步换算成一趟的起点状态（`_position_of`），再照旧走「进入 / 段内 / 一次走完」三状态机。`_try_step` 里算游标新值的那段整段删掉，候选回到三元组。
- `record_step(step, action)`：纯函数，不读任务数据。三条规则——行动没到「已成功」原样返回；告知异常且返回值是「重做」回到该阶段起点；其余写该步的阶段与阶段内序号，落在循环段里时带上这段循环的起止与第几次。它靠两张查找表认路：依据序号 → （阶段, 步骤），告知异常的依据序号 → 报异常的阶段。
- `step_text(step)`：一句人话，例如「当前步：『确认』阶段，第 1 到第 3 步循环的第 2 次（最多 5 次），做完了第 2 步『判读回复：确认还是修改』」。循环段只有一步时写「第 1 步循环的第 3 次」；上限写成引用的（例如文件总表的长度）按引用的意思写成「（最多 文件总表 的长度 次）」。读不懂的当前步照实说「读不出来」，不抛错——它只负责显示。
- `step_view(step)`：结构化投影 `{阶段, 步骤, 说明, 循环: {起, 止, 第几次, 最多}, 是段尾}`，给观测台画图，页面不自己算。

**「第几次」的判据放宽了一点**。第 4.10 节的字面判据是「上一步是该段最后一步、这次又回到第一步则加 1」；实现改成「同一阶段、同一循环段里，这一步的阶段内序号不比上一步大就加 1，否则沿用」（2026-09-16 主会话裁定）。原因是循环段末尾几步被前置条件跳过时，上一步停在段中，按字面判据永远不会加一，第几次就卡在 1。放宽之后对文档表格里的全部值与现有六个场景完全一致，另外盖住了跳步的情形；验证脚本里专门有一条断言验这一种。

**不合法的当前步**按结构判：阶段不存在、步骤序号越界、循环段与这一步所在的段对不上、第几次不是正整数、在循环段里却没有「循环」这一层、不在循环段里却写了「循环」、有「循环」却没有「步骤」，七种都抛 `DefinitionError`，由内核报任务定义错误。不再像第三步那样把内部不一致降级成给使用者看的「告知异常」——那是把内核的错误伪装成业务异常报给人看。

### F12.4 用词与连带修正

- 「游标」「步骤组」「轮」「全局步骤号」四个词在界面、提示词与异常话里不再出现。文档里「步骤组」改称循环段，代码注释与文字跟着改；「轮」在循环的意思上改成「次」。
- 告知异常工具的参数名从「游标」改成「当前步」，值的形状是 `{阶段, 步骤, 说明, 循环, 是段尾}`。异常话里那半句改成「这段循环已完成 3 次」，三种句式（停在段尾／停在段中／第 1 次进行中）保留，写在 `tools.py` 的 `_loop_clause` 里（原来叫 `_rounds_clause`）。
- 告知异常工具不再写任何位置：选重做时它只填返回值，当前步由 `record_step` 按这个返回值退回阶段起点。工具的变更组因此是空的。
- `context.py` 的「任务进度」段不再自己算步号，改成直接用 `step_text`。这修掉了一处既有错误：原来把整任务的步骤编号加一当成阶段内序号（「确认」阶段只有三步，却写出「阶段『确认』第 5 步」），而验证脚本把这个错句子写成了预期值；那条断言的预期跟着改成新的整句。上下文包因此多收一个参数 `step`（从执行上下文来），段的来源从「游标」改成「当前步」，「本步」段的来源从「游标所指步骤」改成「本次要执行的步骤」。
- 系统提示里「问一次答一次算一轮，最多 5 轮」改成「这几步走完一遍算一次循环，最多 5 次」。
- 加载器不再往槽位表注入保留槽位，JSON Schema 里「槽位表不得出现『游标』」那条撤销，`console.py` 列槽位时给保留槽位开的特例删掉，验证脚本里为避开游标而设的「业务槽位」三个辅助函数退役。
- 「轮」这个字还留在对话历史那几段里（「2 轮装入」「【更早 n 轮已省略】」）。那是一问一答的对话轮次，与循环段的次数是两个概念，本项没有动它。

### F12.5 录制文件重录

系统提示与「任务进度」段都变了，请求哈希跟着变，6 条录制全部作废。用「录制」模式对 192.168.213.11:8080（模型 qwen3.8-27b）把术语澄清两个场景各跑一遍，仍写 `task_defs/recordings/glossary.json` 一个文件、整体覆盖：场景一 2 条、场景二 4 条，共 6 条。重录后回放模式下两个场景的断言全过。新的系统提示全文已发给编制会话更新定稿附录。

### F12.6 观测台

- 右栏「此刻在哪」改成「当前步」，文字直接取事件载荷里的 `text`，页面不自己算。
- 两种形状都认：运行文件里有「当前步变化」事件就按新形状走；没有就退回旧形状（「数据变更」加保留槽位「游标」），旧文件里那套「阶段 / 已完成步骤 / 已完成轮数」的换算函数原样留着。实测五份文件都正常：今天跑出的新文件两份、今天之前跑出的带游标槽位的文件一份、09-15 与 09-14 的更早格式各一份。
- 时间轴、运行有向图与协同记录跟着改：有向图判「走到哪个阶段、哪几个阶段没到过」用的是位置的第一层「阶段」，两种形状都有这一层，所以那段代码共用；协同记录「改了什么」那一行现在把当前步变化也列成一个标签；开始卡里的起点位置改读来源「初始化」的那条事件；告知异常卡读参数「当前步」，读不到才退回旧的「游标」。
- `observe.py` 的重放加了 `replay_step`：与 `replay_data` 同一种做法，按序应用「当前步变化」得到那一刻的当前步；事件的中文名与一句话描述也补上了。
- 顺带删掉两块死代码：「槽位轨迹」（`slotsHTML`）与「行动卡片」（`cardsHTML`）。它们在第八项已被协同记录取代，函数留着没人调用，里面还各有一处读保留槽位「游标」的代码。

### F12.7 与文档的差别，以及留给用户定的

1. **事件载荷的键已统一成英文**（old、new、source、text、view），与「数据变更」等既有事件一致（2026-09-16 主会话裁定：键是标识符、值才是中文，同一份运行文件不留两套写法）。中文键那一版只在本机存在过几小时、没有提交过，`runs/` 下 84 份带中文键的运行文件已删除并用验证脚本重跑生成，所以读的那一侧不留兼容分支。更早的、根本没有「当前步变化」事件的运行文件仍按保留槽位「游标」那条旧路走，那条兼容是要长期留着的。
2. **「第几次」判据放宽**，见 F12.3，已在报告里注明，与文档字面不同。
3. **告知异常报告里的位置**仍是走一趟时的局部位置：刚换阶段就报异常时它是新阶段的起点，与存着的当前步不是同一个值。第 4.10 节「记账不做」里已经记着这笔账，本项没有动。
4. **自主规划阶段的当前步形状**（`{"阶段", "回合"}`）没有实现，随第五步定。异常话里原来那句「在『X』阶段已进行 4 回合」以及验证脚本里对应的那条断言一并删掉了——它验的是一个现在无法产生的形状。
5. **`step_text` 里「（最多 文件总表 的长度 次）」**这种写法，是上限写成引用时按引用的意思展开的结果，读着有点拗口。要改成别的写法（例如整句省掉上限）说一声。


## F13 2026-09-17 用户走查后的两处修改

### F13.1 路径图恢复，与有向图各占一张卡

用户走查后裁定：两张图答的是两个问题——运行有向图答「结构与循环」，路径图答「随时间怎么走」——两张都要，版式回到原型 v6：

- 「运行有向图」一张卡，与第九项做的一样，卡里不放拖杆。
- 「时间轴与路径图」一张卡：拖杆与「上一次迭代」「下一次迭代」按钮在图的上方，然后是图例，然后是图（原型 v6 那一块就是这个版式）。
- 原先单独的「时间轴」卡删掉，拖杆只在这一张卡里出现一次。

实现上是把第九项删掉的那段代码找回来：路径图函数 `pathChartHTML`（横轴迭代、纵轴定义里的位置、按阶段分带、点按角色着色、告知异常红菱形、重做红色虚线、没执行过的步骤空心标记）、它用到的二十六条样式、`parseDefinition` 里的行表（`rows`／`rowOf`／`firstRow`）、迭代对象上的 `row` 字段、`markRoles` 里按步骤记角色的 `model.roles`。联动也接回来了：拖杆动一下，路径图的竖虚线挪到第 k 列、那一列的点加深描边，同时有向图上第 k 个迭代号圆点高亮、协同记录里那张卡高亮、右栏跟着跳；在路径图上横向悬停就跟着选，点一下还把那张卡滚到眼前。实测拖到第 3 次迭代时路径图有 1 个高亮点、有向图高亮的是 3 号圆点；点有向图上第 5 步跳到第 4 次迭代。

（第八项留下的截图 `截图_第四步观测台改版_路线图与路径图_1280.png` 拍的是路线图还在时的样子，现在已经不是页面的样子了，留着作过程记录。）

### F13.2 当前数据合成一段

原先每个槽位各成一段（「当前数据 · 槽位『术语』」「当前数据 · 槽位『释义草稿（上一稿）』」……），2026-09-17 用户裁定改成一段：

- 段头「【当前数据 · 槽位】」，段内一行一个槽位「名：值」，角色标注跟在名字后面（「释义草稿（上一稿）：…」），空值那一行写「（空）」。
- 修订记录不再单独成段，作为段内最后一项「修订记录（系统从变更事件推出）：…」；它自己有好几行，后面几行缩进两格，免得与槽位那几行混起来。

改的地方：`context.py` 用一个新方法 `data_segment(slots, revision=None)` 取代原来的 `slot_segment` 与 `revision_segment`；`tools.py` 三条调用路径（写首稿、按意见改稿、判读回复）各改一处；验证脚本里段清单那三条断言（改稿那次从九段变六段、判读那次从六段变五段）与修订记录那条断言跟着改，另加一条验「一段里几行的写法」。这改的是发给模型的用户内容，请求哈希跟着变，6 条录制又对 192.168.213.11:8080 重录了一遍（系统提示没变，所以定稿附录不用改）。

观测台不用改：模型卡的「提示词构成」与「上下文变化」表本来就是按段循环渲染的，段少了就少几块、少几行；段正文的样式本来就是 `white-space: pre-wrap`，多行照原样显示。

改完复跑验证脚本，601 条断言全部通过。

**与原型的一处不同**：v6 原型里那四张模型卡把修订记录写在当前数据段的开头，4.8 节的段表与这次的裁定都写的是「作为段内最后一项」，我按后者做的。


### F13.3 界面文案再扫一遍（2026-09-17 用户走查）

用户看页面时指出界面上还留着第十项裁定要消掉的词。逐处改完了，改的都是页面上写出来给人看的字：

| 在哪 | 原来 | 现在 |
|---|---|---|
| 任务概览「阶段与步骤」里循环那一行 | 步骤组 · 重复直到 … · 最多 = 5 | 这几步一起重复 · 重复直到 … · 最多 5 次（只有一步时写「这一步会重复」） |
| 旧运行文件那一句位置的次数半句 | 该组已完成 3 轮／该组第 1 轮进行中／该组已完成 r 轮，第 r+1 轮进行中 | 这段循环已完成 3 次／这段循环第 1 次进行中／这段循环已完成 n 次，第 n+1 次进行中 |
| 原始事件里旧文件的位置那一行 | 游标 → … | 当前步 → … |
| 有向图判断菱形的悬停提示 | 步骤组的停止条件：…；最多 5 轮 | 这段循环的停止条件：…；最多 5 次 |
| 有向图阶段框里那句重复说明 | 上限是 5 轮，走了 2 轮 | 上限是 5 次，走了 2 次 |
| 路径图的说明 | 同一高度连续几个点是步骤组在重复 | 同一高度连续几个点是这段循环在重复 |
| 告知异常那张卡的叙述 | 阶段的步骤组重复到了上限，或这一轮没有可做的步骤 | 阶段里那段循环重复到了上限，或这一次没有可做的步骤 |
| 控制台两处说明文字 | 按次序一轮一句（第 1 轮提意见、第 2 轮确认）；含步骤组里的 | 按次序一次一句（第 1 次提意见、第 2 次确认）；含循环段里的 |

留着没动的三类，都不是页面自己写的字：

1. **代码里认旧运行文件的键名**（常量 `CURSOR = "游标"`、读 `report["游标"]`、读 JSON 文件里的键「步骤组」）与注释，按裁定保留。
2. **旧运行文件自己的内容**。打开 09-14、09-15 跑出来的文件，页面上仍会看到「游标」「该组已完成 3 轮」——那是当时写进文件里的槽位名、工具说明、告知异常的原话与原始事件参数，是历史记录本身。页面照原样显示，不改写它，否则记录就不是记录了。新跑出来的文件页面上这几个词一个都没有（实测两份新文件，`document.body.innerText` 里「步骤组」「游标」「该组」「回合」命中数都是 0）。
3. **「轮」在对话轮次这个意思上的用法**（「1 轮装入」「【更早 n 轮已省略】」「只会整轮省略」）。它指一问一答，与循环的次数是两个概念，第 4.8 节也是这么写的，没有动。
4. **「回合」**是自主规划阶段的词（阶段已进行几回合、回合上限），按裁定保留。

## F14 专项：「行动」改名「工具调用」，顺带更新组先核后写（2026-09-17）

### F14.1 结论

按设计档案仓那份改名专项说明做完了。代码里记录单位从「行动」改称「工具调用」，中文与标识符一起换；观测台上用户看得见的地方一个「行动」都没有了；旧运行文件不改写，读进来时归一到新名字。顺带做了第四步第 7 节第 13 条③：更新组先核对整组再写入。

验证脚本从 602 条断言增加到 612 条，全部通过：新增「更新组先核对再写入」一组 7 条，加上改名的机器检查等 3 条。

### F14.2 改了哪些名字

| 旧 | 新 |
|---|---|
| `Action`（类） | `ToolCall` |
| `ActionStatus` | `CallStatus`（取值不变：候选、已提出、已获准、已拒绝、执行中、等待中、已成功、已失败） |
| `TERMINAL_ACTION_STATUSES` | `TERMINAL_CALL_STATUSES` |
| `ACTION_PROPOSED`／「行动提出」 | `CALL_PROPOSED`／「工具调用提出」 |
| `ACTION_STATUS_CHANGED`／「行动状态变化」 | `CALL_STATUS_CHANGED`／「工具调用状态变化」 |
| 事件键 `action_id`（顶层与载荷里的都算） | `call_id` |
| `select_action`／`register_action` | `select_call`／`register_call` |
| `task.actions`／`task.next_action_id` | `task.calls`／`task.next_call_id` |
| `ctx.action` | `ctx.call` |
| `_is_action_id`／`source_action` | `_is_call_id`／`source_call` |
| 内核五步「行动选择、执行控制、行动执行、状态更新、结果检查」 | 「调用选择、执行控制、调用执行、状态更新、结果检查」（含事件里那句说明） |
| 中文「行动」（注释、文档字符串、断言说明、界面文案） | 「工具调用」 |
| 控制台 `ActionPrinter`／`--show-actions`／`show_actions` | `CallPrinter`／`--show-calls`／`show_calls` |
| 验证脚本 `of_action`／`action_history`／`action_sequence`／`check_exception_action`／`check_other_tool_actions` | `of_call`／`call_history`／`call_sequence`／`check_exception_call`／`check_other_tool_calls` |
| 观测台 `actionSlots`／`loopOfAction`／`data-action`／`f-action`／`#actions`／`actionCallText`／`actionRow` | `callSlots`／`loopOfCall`／`data-call`／`f-call`／`#calls`／`callText`／`callRow` |

**改名撞上的两处重名，顺手理清了**：

1. 两个模型工具的参数 `call` 本来是「模型调用件」（`llm.make_caller` 做出来的那个函数），与改名后的工具调用对象撞名。按专项第 1 节「不缩写成『调用』，免得与函数调用、模型调用混」，把它改名 `call_model`：`draft_definition(ctx, call_model, …)`、`judge_reply(ctx, call_model, …)`、`build_table(…, call_model=None, …)`、`run_scenario(…, call_model=None, …)`。
2. `kernel.execute` 里有个局部字典本来就叫 `call`（追踪事件的内容），与工具调用对象撞名，改叫 `where`。

没改的：依据 `basis`、返回值 `result`、变更组 `changes`、追踪事件名 `EXECUTE_CALL`（它本来就叫这个，只把中文说明从「行动执行调用」改成「调用执行」）。上下文包里预留的段类型「工具调用历史」在代码里还不存在（第 4.8 节只列了它，没实现），所以没有可改的东西。

### F14.3 旧运行文件怎么认

`observe.py` 里加了一个 `normalize_event(raw)`：旧事件名换成新的、顶层与载荷里的 `action_id` 换成 `call_id`。旧名到新名的对照只写在这一个函数里，两条读文件的路都调它——`read_events` 把每行读成事件对象（验证脚本、运行索引、重放走它），HTTP 接口 `/api/runs/<文件>` 把每行发给页面。所以页面、重放与断言都只认新名。`runs/` 里的旧文件一个字都不改：它们是当时的历史记录。

实测六份文件都正常：今天改名后跑出的两份、今天改名前跑出的一份、09-15 的两份（含带重做的那次）、09-14 只有依据说明没有任务定义结构的那份。每份的协同记录卡数、路径图的点数、有向图的迭代号圆点数都对得上，页面上「行动」出现 0 次。

注意一件事：观测台服务是长驻进程，改完代码要重启它，否则接口还在用旧代码归一（我第一次核对时就踩了这个，看到页面上路径图没有点）。

### F14.4 更新组先核对再写入

`update_state` 收到更新组时，原来是边核对边写：核对一条、写一条、发一条事件。这样前几条已经落进数据、事件也发出去了，后一条才发现旧值不符抛错，事件流里就留下半截状态。现在改成两趟：第一趟只核对整组（类型对不对、旧值符不符），任一项不合格就抛内核错误、整组不写、一个事件也不发；第二趟才逐条写并发事件。

同一组里若有两条写同一个槽位，第一趟核对时按前一条的新值核对后一条的旧值，与逐条写入的次序一致（现在六个场景里没有这种组，但规则得对）。

新加的一组断言直接调写入口，不跑任务：一组里第二条旧值不符时抛错且错误话里点名是哪个槽位、当前值与声明值；那一组前面那条合格的变更也没落进数据；那一组一个事件也没发；整组合格时两条都写成、发两条「数据变更」；同一组写同一个槽位两次按次序写成；数据变更与当前步更新同在一组时两样一次写完、发两条事件。

### F14.5 机器检查

`check_kernel_is_task_agnostic` 那一组里加了一条：这一目录下的 `.py` 文件里不再出现「行动」「Action」「action_id」。两个文件例外——`observe.py` 要认出旧运行文件里的旧键，验证脚本自己是这条检查待的地方（旧名字作为被查的词写在那一段里），所以不查自己。观测台那一侧用浏览器核对：页面可见文字里「行动」出现 0 次、「工具调用」出现 30 次。

录制文件没有受影响：提示词里本来就没有「行动」字样（搜出来是 0 条），所以请求哈希不变，6 条录制不用重录，回放照旧全过。
