"""验证脚本的机器检查：观测台（第二步）：跑全部场景写运行文件，核对运行索引、摘要与观测台服务。"""

from __future__ import annotations

import re
import threading
from pathlib import Path
import dataclasses

from tod_kernel import kernel
from tod_kernel.observe import FileWriter, make_server, read_events, summarize
from tod_kernel.verify.base import Checker, banner, check_kernel_is_task_agnostic, intake_def, run_scenario
from tod_kernel.verify.intake import BAD_GOAL_NAME, EXCEPTION_TOOLS, INTAKE_ANSWERS, INTAKE_TOOLS
from tod_kernel.verify.glossary import (GLOSSARY_INPUT_ONE, GLOSSARY_INPUT_TWO, GLOSSARY_TOOLS, REPLY_CONFIRM,
                                        REPLY_KEY, REPLY_REVISE, glossary_call, glossary_def)


# ───────────────────────── 第二步：观测台 ─────────────────────────

# 七个场景的运行参数：（任务标识, 任务定义, 答案表, 工具名）。
def all_scenarios():
    """会真跑起来的场景的运行参数，每项是一份给 run_scenario 的关键字参数。

    观测台那组检查用它连跑两遍看运行文件。加载错误那个场景不产生运行文件，不在其中。
    """
    return [
        dict(task_id="T-intake-1", task_def=intake_def(), answers=INTAKE_ANSWERS, tool_names=INTAKE_TOOLS),
        dict(task_id="T-exception-3", task_def=intake_def("intake_bad_goal.json"), answers=INTAKE_ANSWERS,
             tool_names=EXCEPTION_TOOLS, exception_answers=["被动终止"]),
        dict(task_id="T-glossary-1", task_def=glossary_def(dict(GLOSSARY_INPUT_ONE)), answers={REPLY_KEY: [REPLY_CONFIRM]},
             tool_names=GLOSSARY_TOOLS, call_model=glossary_call()[0]),
        dict(task_id="T-glossary-2", task_def=glossary_def(dict(GLOSSARY_INPUT_TWO)),
             answers={REPLY_KEY: [REPLY_REVISE, REPLY_CONFIRM]}, tool_names=GLOSSARY_TOOLS, call_model=glossary_call()[0]),
    ]


def expected_summaries() -> dict:
    """摘要预期表：任务标识 → （任务定义名, 终态, 迭代数, 工具调用数）。

    写成函数而不是模块级字典，因为任务定义名取自后面定义的常量。
    结果检查第三步起挪到迭代末尾，所以正常完成的运行迭代数等于工具调用数；以内核错误结束的那次迭代没有末尾检查，数目不变。
    """
    return {
        "T-intake-1": ("材料接入登记", "已完成", 8, 8),
        "T-exception-3": (BAD_GOAL_NAME, "内核错误", 5, 5),
        "T-glossary-1": ("术语澄清", "已完成", 3, 3),
        "T-glossary-2": ("术语澄清", "已完成", 6, 6),
    }


def http_get(port: int, raw_path: str):
    """按原始路径发请求（不做 .. 规范化），返回（状态码, 响应体字节）。"""
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", raw_path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def observatory_checks() -> Checker:
    import json as json_module
    import shutil
    import tempfile
    import urllib.parse

    banner("第二步：观测台")
    c = Checker("第二步：观测台")
    print("── 断言 ──")
    check_kernel_is_task_agnostic(c)
    work = Path(tempfile.mkdtemp(prefix="tod-observatory-"))
    try:
        runs_dir = work / "runs"
        expected = expected_summaries()
        for round_no in (1, 2):
            for kwargs in all_scenarios():
                run_scenario(runs_dir=runs_dir, console=False, **kwargs)
        files = sorted(runs_dir.iterdir())
        scenario_count = len(all_scenarios())

        # 目标一：每次运行都留下来、都能找到。
        c.check(f"第二步目标一：临时空目录里连跑两遍后恰有 {2 * scenario_count} 个文件", len(files) == 2 * scenario_count, [f.name for f in files])
        c.check("第二步目标一：文件名两两不同，且都是「任务标识_开始时刻.jsonl」",
                len({f.name for f in files}) == len(files)
                and all(re.fullmatch(r"T-[a-z]+-\d_\d{8}T\d{9}(_\d+)?\.jsonl", f.name) for f in files),
                [f.name for f in files])
        per_task: dict[str, int] = {}
        wrong = []
        for f in files:
            summary = summarize(f)
            per_task[summary.task_id] = per_task.get(summary.task_id, 0) + 1
            actual = (summary.task_def_name, summary.final_status, summary.loops, summary.calls)
            if expected.get(summary.task_id) != actual:
                wrong.append((f.name, actual))
        c.check("第二步目标一：每份文件的摘要（任务定义名、终态、迭代数、工具调用数）与预期表逐行相等", not wrong, wrong)
        c.check("第二步目标一：每个任务标识恰有两份文件", per_task == {task_id: 2 for task_id in expected}, per_task)
        c.check("第二步目标一：每份文件的事件序号从 1 起连续，且含「任务开始」",
                all([e.seq for e in read_events(f)] == list(range(1, len(read_events(f)) + 1))
                    and any(e.name == "TASK_STARTED" for e in read_events(f)) for f in files))

        # 目标二：看一次运行不生成文件；服务三个接口。
        c.check("第二步目标二：仓库里不再有 view.py", not (Path(kernel.__file__).resolve().parent / "view.py").exists())
        c.check("第二步目标二：运行目录里只有 .jsonl，没有 .html", all(f.suffix == ".jsonl" for f in files), [f.name for f in files])
        # 旧命名文件照样能读：复制一份成不带时刻的文件名。
        old_dir = work / "old"
        old_dir.mkdir()
        old_file = old_dir / "T-intake-1.jsonl"
        by_task = {}
        for f in files:
            by_task.setdefault(summarize(f).task_id, f)  # 按摘要里的任务标识挑文件，不依赖文件名
        if "T-intake-1" in by_task:
            shutil.copyfile(by_task["T-intake-1"], old_file)
            old_summary = summarize(old_file)
            c.check("第二步：旧命名文件照样能读，开始时刻取文件修改时间并标出来源",
                    old_summary.started_at_source == "文件修改时间"
                    and (old_summary.task_def_name, old_summary.final_status, old_summary.loops, old_summary.calls) == expected["T-intake-1"],
                    old_summary)
        else:
            c.check("第二步：旧命名文件照样能读（找不到 T-intake-1 的运行文件，无法检查）", False)

        server = make_server(runs_dir, "0.0.0.0", 0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, name="observatory-test", daemon=True)
        thread.start()
        try:
            status, body = http_get(port, "/")
            c.check("第二步目标二：GET / 返回 200 和观测台页面", status == 200 and "观测台".encode("utf-8") in body, status)
            status, body = http_get(port, "/api/runs")
            index = json_module.loads(body) if status == 200 else []
            c.check(f"第二步目标一：GET /api/runs 返回 200，行数等于文件数 {len(files)}", status == 200 and len(index) == len(files), (status, len(index)))
            c.check("第二步目标一：索引按开始时刻倒序", [row["started_at"] for row in index] == sorted((row["started_at"] for row in index), reverse=True))
            mismatched = []
            for f in files:
                status, body = http_get(port, "/api/runs/" + urllib.parse.quote(f.name))
                lines = sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
                if status != 200 or len(json_module.loads(body)) != lines:
                    mismatched.append((f.name, status))
            c.check("第二步目标二：每份文件经 GET /api/runs/<文件名> 返回的事件数等于文件行数", not mismatched, mismatched)
            # 其中 ../old/T-intake-1.jsonl 是运行目录之外真实存在的文件，拦截失效时会返回 200。
            for raw in ("/api/runs/../old/T-intake-1.jsonl", "/api/runs/..%2Fold%2FT-intake-1.jsonl",
                        "/api/runs/%2E%2E%2Fold%2FT-intake-1.jsonl", "/api/runs/../x", "/api/runs/nope.jsonl", "/api/runs/"):
                status, _ = http_get(port, raw)
                c.check(f"第二步目标二：路径穿越或不存在的文件 {raw} 返回 404", status == 404, status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

        # 同一个文件订阅者：序号回绕认作新运行；「任务开始」前的事件缓存后写入；没有「任务开始」不写文件。
        sample = read_events(by_task["T-intake-1"]) if "T-intake-1" in by_task else []
        head = [e for e in sample if e.name != "TASK_STARTED"][:1]  # 取一个非「任务开始」事件放到最前
        replay = head + [e for e in sample if not head or e is not head[0]]
        replay = [dataclasses.replace(e, seq=i + 1) for i, e in enumerate(replay)]
        writer_dir = work / "writer"
        writer = FileWriter(writer_dir)
        for _ in range(2):
            for e in replay:
                writer(e)
        if replay:
            writer(dataclasses.replace(replay[0], seq=1))  # 第三次运行只有一个事件，没有「任务开始」
        written = sorted(writer_dir.iterdir())
        contents = [[json_module.loads(line)["seq"] for line in f.read_text(encoding="utf-8").splitlines()] for f in written]
        c.check("第二步：同一个文件订阅者对同一任务标识，序号回绕后另起一份文件；两份都完整且按原序写出缓存的事件；"
                "没有「任务开始」的第三次运行不写文件",
                replay and len(written) == 2 and all(seqs == list(range(1, len(replay) + 1)) for seqs in contents)
                and writer.paths == written,
                ([f.name for f in written], contents))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c
