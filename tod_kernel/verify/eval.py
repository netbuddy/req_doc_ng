"""验证脚本的对话理解标注集评测：逐条回放标注集，打印逐句全对率与逐项功能准确率、不一致清单。"""

from __future__ import annotations

from tod_kernel import kernel
from tod_kernel.tools import EXCEPTION_TOOL, UNDERSTAND_TOOL
from tod_kernel.kernel import ToolCall, CallStatus
from tod_kernel.verify.base import Checker, TASK_DEFS_DIR, ask_events, banner
from tod_kernel.verify.glossary import GLOSSARY_TOOLS, glossary_call, glossary_def


# ───────────────────────── 检查组：对话理解标注集 ─────────────────────────
# 第五步第 6 节第一层：工具级评测。逐条调对话理解（回放），比对功能与内容，算逐句全对率与逐项功能准确率。

EVAL_FILE = TASK_DEFS_DIR / "eval" / "对话理解标注集.json"
EVAL_RECORDING = "task_defs/recordings/对话理解标注集.json"
EVAL_FLOOR = 0.8  # 准确率下限（2026-09-17 用户确认）
EVAL_STEP = {"阶段": "确认", "循环": {"起": 1, "止": 3, "第几次": 1}, "步骤": 1}


def load_eval_cases() -> list:
    import json as json_module

    return json_module.loads(EVAL_FILE.read_text(encoding="utf-8"))["条目"]


def understand_case(case: dict, call_model):
    """跑一条标注：按条目拼好任务数据与一问一答的事件，直接调对话理解工具（不启动任务）。

    返回（工具调用对象, 状态记录）：工具调用的返回值是调用记录，变更组是它要写的东西；状态记录是 [(状态, 说明), …]。
    """
    import functools as functools_module
    import types as types_module

    from tod_kernel.tools import system_prompt_for, understand

    task_def = glossary_def()
    data = {**task_def.SLOTS, **case["数据"], "回复": case["原话"], "上一问": case["上一问"]}
    events = ask_events(1, 1, "确认", "回复", case["系统问话"], case["原话"])
    made = ToolCall(tool=UNDERSTAND_TOOL, params={}, proposer="selector", basis=(4, "确认 › 第 4 步 理解使用者的回复", {}),
                    call_id=2, status=CallStatus.APPROVED)
    statuses = []

    def set_status(status, note):
        made.status = status
        statuses.append((status, note))

    ctx = kernel.ExecContext(call=made, data_view=types_module.MappingProxyType(data), step=dict(EVAL_STEP),
                             inbox=None, outbox=None, set_status=set_status)
    prompt = system_prompt_for(task_def, list(GLOSSARY_TOOLS) + [EXCEPTION_TOOL])
    understand(ctx, call_model, task_def=task_def, read_events=lambda: list(events), system_prompt=prompt)
    return made, statuses


def _value_matches(expected, actual) -> bool:
    if expected == "*":
        return actual not in (None, "", [], {})
    if isinstance(expected, dict) and set(expected) == {"含"}:
        return isinstance(actual, str) and all(word in actual for word in expected["含"])
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(_value_matches(expected[k], actual[k]) for k in expected)
    return expected == actual


def _content_matches(expected_act, actual_act, question) -> bool:
    from tod_kernel.tools import defer_target

    if expected_act["功能"] == "DEFER":
        target = defer_target(question, actual_act.get("内容"))
        return target == (expected_act.get("内容") or {}).get("槽位")
    expected, actual = expected_act.get("内容"), actual_act.get("内容")
    if expected == "*":
        return actual is not None
    if isinstance(actual, dict):
        actual = {key: value for key, value in actual.items() if key != "原问"}
    return _value_matches(expected, actual)


def score_case(case: dict, acts: list) -> tuple:
    """一条的比对：返回（逐句全对, 功能对上的项数, 分母, 不一致说明）。

    按期望逐项在实际里找一个没用过的同功能项；功能对上算一项功能正确，内容也对上才算这一项全对。
    分母取期望项数与实际项数的较大者，实际多出来的项因此也算错。
    """
    used, function_hits, full_hits, problems = set(), 0, 0, []
    for expected in case["期望"]:
        index = next((i for i, act in enumerate(acts) if i not in used and act.get("功能") == expected["功能"]), None)
        if index is None:
            problems.append(f"少了 {expected['功能']}")
            continue
        used.add(index)
        function_hits += 1
        if _content_matches(expected, acts[index], case["上一问"]):
            full_hits += 1
        else:
            problems.append(f"{expected['功能']} 的内容不对")
    extra = [act.get("功能") for i, act in enumerate(acts) if i not in used]
    if extra:
        problems.append(f"多了 {'、'.join(extra)}")
    denominator = max(len(case["期望"]), len(acts))
    whole = full_hits == len(case["期望"]) == len(acts)
    return whole, function_hits, denominator, problems


def understand_eval_checks() -> Checker:
    title = "第五步：对话理解标注集评测"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    cases = load_eval_cases()
    kinds = {}
    for case in cases:
        kinds[case["上一问"]["类型"]] = kinds.get(case["上一问"]["类型"], 0) + 1
    situations = {}
    for case in cases:
        situations[case["情形"]] = situations.get(case["情形"], 0) + 1
    seven = ("确认", "提修改意见", "给整段改写", "要换一份", "推迟并顺手改另一属性", "提问", "含糊")
    c.check(f"标注集三十到四十条，4.9 节七种情形各至少三句，四种上一问类型各至少五句，另有无关句（共 {len(cases)} 条）",
            30 <= len(cases) <= 40 and all(situations.get(name, 0) >= 3 for name in seven)
            and all(kinds.get(kind, 0) >= 5 for kind in ("建议", "求证", "选择", "请求")) and situations.get("无关", 0) >= 1,
            (kinds, situations))
    call_model, _ = glossary_call(recording=EVAL_RECORDING)
    rows, structure_bad = [], []
    for case in cases:
        made, statuses = understand_case(case, call_model)
        record = made.result if isinstance(made.result, dict) else {}
        acts = record.get("acts") or []
        if made.status != CallStatus.SUCCEEDED or record.get("problem") is not None:
            structure_bad.append((case["编号"], statuses[-1][1] if statuses else None, record.get("problem")))
        rows.append((case, acts, score_case(case, acts)))
    c.check("硬断言：每条都调通（回放命中录制）、模型输出都合 Schema（功能与槽位路径不越出候选、没有走没听懂的兜底）",
            not structure_bad, structure_bad)
    whole = sum(1 for _, _, score in rows if score[0])
    hits = sum(score[1] for _, _, score in rows)
    denominator = sum(score[2] for _, _, score in rows)
    sentence_rate = whole / len(rows) if rows else 0.0
    function_rate = hits / denominator if denominator else 0.0
    print(f"逐句全对率：{whole}/{len(rows)} = {sentence_rate:.3f}")
    print(f"逐项功能准确率：{hits}/{denominator} = {function_rate:.3f}（下限 {EVAL_FLOOR}）")
    mismatched = [(case, acts, score) for case, acts, score in rows if not score[0]]
    if mismatched:
        print("不一致清单：")
        for case, acts, score in mismatched:
            expected = "；".join(f"{e['功能']} {e.get('内容')}" for e in case["期望"])
            actual = "；".join(f"{a.get('功能')} {a.get('内容')}" for a in acts)
            print(f"  第 {case['编号']} 条（{case['上一问']['类型']}·{case['情形']}）原话「{case['原话']}」")
            print(f"    期望：{expected}")
            print(f"    实际：{actual}")
            print(f"    差在：{'；'.join(score[3])}")
    c.check(f"逐项功能准确率 {function_rate:.3f} 不低于下限 {EVAL_FLOOR}（逐句全对率 {sentence_rate:.3f} 只报告）",
            function_rate >= EVAL_FLOOR, function_rate)
    four_nine = {1: ["AFFIRM"], 4: ["DENY", "INFORM"], 7: ["AFFIRM", "INFORM"], 10: ["REQALTS"],
                 13: ["DEFER", "INFORM"], 16: ["REQUEST"], 19: ["CLARIFY"]}
    got = {number: sorted(a.get("功能") for a in acts) for case, acts, _ in rows
           for number in [case["编号"]] if number in four_nine}
    c.check("第五步 4.9 节七句（标注集第 1、4、7、10、13、16、19 条）回放得到表里的行为列表（按功能核对）",
            all(got.get(number) == sorted(functions) for number, functions in four_nine.items()), got)
    return c
