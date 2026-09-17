"""验证脚本的机器检查：对话理解的机器检查：候选、Schema、规范化、落数据、提示词包与询问再问。"""

from __future__ import annotations

import re
from pathlib import Path
import dataclasses

from tod_kernel import kernel, llm, taskdef
from tod_kernel.tools import UNDERSTAND_TOOL
from tod_kernel.tools import prompt_pack_of
from tod_kernel.kernel import DATA_CHANGED, ToolCall, CallStatus
from tod_kernel.verify.base import Checker, TASK_DEFS_DIR, banner, frame, stack, step_at
from tod_kernel.verify.glossary import (FALLBACK_OPTIONS, GLOSSARY_FILE, GLOSSARY_INPUT_ONE, GLOSSARY_INPUT_TWO,
                                        GLOSSARY_TERM_ONE, STEP_TEXT_CHECK, STEP_TEXT_CHOICE, STEP_TEXT_NONE,
                                        STEP_TEXT_REQUEST, STEP_TEXT_SUGGEST, SUGGEST_QUESTION, confirm_utterance,
                                        glossary_call, glossary_def, pattern_number)
from tod_kernel.verify.eval import load_eval_cases, understand_case


# ───────────────────────── 检查组：对话理解的机器检查 ─────────────────────────
# 第五步第 6 节第三层：不调模型、不跑任务，手写输入把候选、清单、六条规范化与必备槽位逐条验出来。

FAKE_SLOTS = {
    "标题": {"说明": "用例标题", "类型": "文本"},
    "优先级": {"说明": "高中低", "类型": "枚举", "取值": ["高", "中", "低"]},
    "步骤": {"说明": "主流程步骤", "类型": "列表", "项": {"动作": {"说明": "谁做什么", "类型": "文本"},
                                                  "序号": {"说明": "第几步", "类型": "数字"}}},
    "标签": {"说明": "自由标签", "类型": "列表"},
    "附加": {"说明": "其他属性", "类型": "对象"},
    "备注": {"说明": "系统写的备注", "类型": "文本", "使用者可写": False},
    "回复": {"说明": "原话", "类型": "文本"},
    "上一问": {"说明": "上一问", "类型": "对象"},
    "修改意见": {"说明": "改动要求", "类型": "文本", "使用者可写": False},
}
FAKE_DATA = {"步骤": [{"动作": "登录", "序号": 1}, {"动作": "下单", "序号": 2}], "标签": ["甲"], "附加": {"来源": "访谈"}}


def _raises_definition_error(task_def, step) -> bool:
    from tod_kernel.kernel import DefinitionError

    try:
        task_def.select_call(dict(task_def.SLOTS), step)
    except DefinitionError:
        return True
    return False


def _stub_caller(text: str):
    """只回一段固定文字的模型调用件，机器检查组用它验对话理解的代码路径，不碰录制文件。"""
    def call(request):
        return llm.Reply(text=text, record={"mode": "桩", "model": "桩", "system_prompt_hash": "0" * 64,
                                            "user_content": request.user, "shape": request.shape, "response": text,
                                            "request_hash": "0" * 64, "elapsed_ms": 0})
    return call


def _understand_once(task_def, call_model, data, step):
    """直接调一次对话理解（不启动任务），返回（工具调用, 状态记录）。"""
    import types as types_module

    from tod_kernel.tools import understand

    made = ToolCall(tool=UNDERSTAND_TOOL, params={}, proposer="selector", basis=(0, "", {}), call_id=1,
                    status=CallStatus.APPROVED)
    statuses = []

    def set_status(status, note):
        made.status = status
        statuses.append((status, note))

    ctx = kernel.ExecContext(call=made, data_view=types_module.MappingProxyType(data), step=step,
                             inbox=None, outbox=None, set_status=set_status)
    understand(ctx, call_model, task_def=task_def, read_events=lambda: [], system_prompt="")
    return made, statuses


def _ask_once(task_def, data, *registrations):
    """直接调一次念草稿那一问（不启动任务）：事件流里依次是给定的几条登记上一问的数据变更，回答恒为「好」。

    返回（问题里的话, 这次登记的上一问）。
    """
    import types as types_module

    from tod_kernel.tools import ask

    params = {"target": {"slot": "回复", "path": []}, "hint": {"term": GLOSSARY_TERM_ONE, "draft": data.get("释义草稿")},
              "type": "建议", "about": {"slot": "释义草稿", "path": []}, "adopt_to": "确认释义"}
    made = ToolCall(tool="ask", params=params, proposer="selector", basis=(0, "", {}), call_id=2,
                    status=CallStatus.APPROVED)
    sent = []

    class Outbox:
        def put(self, message):
            message = dataclasses.replace(message, seq=1)
            sent.append(message)
            return message

    class Inbox:
        def take(self, match, block, waiter=None):
            return kernel.Message(kind="answer", sender="user", recipient=2, in_reply_to=1, content="好", seq=2)

    def set_status(status, note):
        made.status = status

    registered = [kernel.Event(seq=seq, ts=0.0, task_id="T-reask", call_id=seq, kind="state", source="update",
                               name=DATA_CHANGED, payload={"slot": "上一问", "old": None, "new": question, "source": seq})
                  for seq, question in enumerate(registrations, start=1)]
    view = {**task_def.SLOTS, **data}
    ctx = kernel.ExecContext(call=made, data_view=types_module.MappingProxyType(view), step=None,
                             inbox=Inbox(), outbox=Outbox(), set_status=set_status)
    ask(ctx, task_def, read_events=lambda: list(registered))
    question = next((change.new for change in made.changes or [] if change.slot == "上一问"), None)
    return (sent[0].content["utterance"] if sent else None), question


def understand_machine_checks() -> Checker:
    import json as json_module
    import shutil
    import tempfile
    import types as types_module

    from tod_kernel import tools as tools_module
    from tod_kernel.context import candidate_functions, writable_paths

    title = "第五步：对话理解的机器检查"
    banner(title)
    c = Checker(title)
    print("── 断言 ──")
    active = ["INFORM", "REQUEST", "DEFER", "OTHER", "CLARIFY"]
    expected = {"建议": ["AFFIRM", "DENY", "REQALTS"], "求证": ["AFFIRM", "DENY"], "选择": ["AFFIRM", "DENY"], "请求": ["DENY"]}
    for kind, response in expected.items():
        got = candidate_functions({"类型": kind})
        c.check(f"候选功能集：上一问是{kind}时是 {'、'.join(response + active)}（配对表的回应类加四个主动类加 CLARIFY）",
                got == response + active and tools_module.PAIRING[kind] == response, got)
    c.check("候选功能集：上一问为空时没有回应类，只有四个主动类与 CLARIFY", candidate_functions(None) == active,
            candidate_functions(None))

    rows = writable_paths(FAKE_SLOTS, None, FAKE_DATA)
    want = [
        {"槽位": "标题", "路径": []}, {"槽位": "优先级", "路径": []},
        {"槽位": "步骤", "路径": ["+"]},
        {"槽位": "步骤", "路径": [0]}, {"槽位": "步骤", "路径": [0, "动作"]}, {"槽位": "步骤", "路径": [0, "序号"]},
        {"槽位": "步骤", "路径": [1]}, {"槽位": "步骤", "路径": [1, "动作"]}, {"槽位": "步骤", "路径": [1, "序号"]},
        {"槽位": "步骤", "路径": ["+", "动作"]}, {"槽位": "步骤", "路径": ["+", "序号"]},
        {"槽位": "标签", "路径": ["+"]}, {"槽位": "标签", "路径": [0]},
        {"槽位": "附加", "路径": ["来源"]},
    ]
    c.check(f"可写路径清单：含列表与项字段的假槽位表算出 {len(want)} 行——标量各一行、列表不给整表替换行、"
            "每个现有项与项字段各一行、末尾新增与它的字段各一行、对象按当前值的键各一行；三个必备槽位与使用者不可写的槽位不进",
            rows == want, rows)
    suggest_rows = writable_paths(FAKE_SLOTS, {"类型": "建议"}, FAKE_DATA)
    c.check("可写路径清单：上一问是建议类时，使用者不可写的「修改意见」也开放（加在末尾），其余不变",
            suggest_rows == want + [{"槽位": "修改意见", "路径": []}], suggest_rows[len(want):])
    c.check("可写路径清单：对象槽位当前值为空时没有行，列表为空时只有末尾新增那几行",
            writable_paths(FAKE_SLOTS, None, {}) == [
                {"槽位": "标题", "路径": []}, {"槽位": "优先级", "路径": []}, {"槽位": "步骤", "路径": ["+"]},
                {"槽位": "步骤", "路径": ["+", "动作"]}, {"槽位": "步骤", "路径": ["+", "序号"]},
                {"槽位": "标签", "路径": ["+"]}], writable_paths(FAKE_SLOTS, None, {}))

    question = {"类型": "建议", "槽位": "标题", "路径": [], "选项": None, "采纳到": None}
    candidates = candidate_functions(question)
    paths = writable_paths(FAKE_SLOTS, question, FAKE_DATA)
    schema = tools_module.understand_schema(candidates, paths, question)
    variants = schema["properties"]["行为"]["items"]["anyOf"]
    enum = [name for variant in variants for name in variant["properties"]["功能"]["enum"]]
    write_slots = [variant["properties"]["内容"] for variant in variants if "INFORM" in variant["properties"]["功能"]["enum"]]
    c.check("输出 Schema 现算：功能枚举恰是候选功能集，每个功能一支；INFORM 那支按槽位配对路径枚举",
            sorted(enum) == sorted(candidates) and write_slots
            and [branch["properties"]["槽位"]["enum"] for branch in write_slots[0]["anyOf"]]
            == [["标题"], ["优先级"], ["步骤"], ["标签"], ["附加"], ["修改意见"]], enum)

    def described(node) -> bool:
        if isinstance(node, dict):
            return "description" in node or any(described(value) for value in node.values())
        return isinstance(node, list) and any(described(value) for value in node)
    choice_schema = tools_module.understand_schema(candidate_functions({"类型": "选择"}), paths, {"类型": "选择"})
    c.check("输出 Schema 只留裸结构：建议类与选择类两份 Schema 里任何一层都没有说明文字（description）",
            not described(schema) and not described(choice_schema), None)

    from tod_kernel.context import understand_step_text, writable_places_text
    from tod_kernel.prompt_pack import PROMPTS_DIR, PromptPackError
    from tod_kernel.prompt_pack import load as load_prompt_pack

    glossary = glossary_def()
    glossary_meta = glossary.DEFINITION["槽位"]
    prompt = prompt_pack_of(UNDERSTAND_TOOL)
    cases = [("建议", SUGGEST_QUESTION, STEP_TEXT_SUGGEST),
             ("求证", {"类型": "求证", "槽位": "术语", "路径": [], "选项": ["需求评审"], "采纳到": None}, STEP_TEXT_CHECK),
             ("选择", {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": FALLBACK_OPTIONS, "采纳到": "确认释义",
                     "原问": SUGGEST_QUESTION}, STEP_TEXT_CHOICE),
             ("请求", {"类型": "请求", "槽位": "术语", "路径": [], "选项": None, "采纳到": None}, STEP_TEXT_REQUEST),
             ("上一问为空", None, STEP_TEXT_NONE)]
    for kind, asked_question, want in cases:
        got = understand_step_text(prompt, asked_question, writable_paths(glossary_meta, asked_question, glossary.SLOTS),
                                   glossary_meta, glossary.SLOTS)
        c.check(f"对话理解的本步段（{kind}）逐字等于定稿写法，不出现功能标识与代码名",
                got == want and not re.search(r"[A-Z]{4,}|候选功能集|可写路径清单|\[\]", got), got)
    list_meta = {"材料清单": {"说明": "清单", "类型": "列表", "项": {"文件名": {"说明": "名", "类型": "文本"}}},
                 "备注": {"说明": "备注", "类型": "文本"}}
    places = [writable_places_text(prompt, writable_paths(list_meta, None, {"材料清单": items}), list_meta,
                                   {"材料清单": items}) for items in ([], [{"文件名": "a"}], [{"文件名": "a"}, {"文件名": "b"}])]
    c.check("可写位置只列槽位名，列表槽位说明可以新增一项、或改第几项（没有项、一项、多项三种写法）",
            places == ["材料清单（可以新增一项）、备注", "材料清单（可以新增一项，或改第 1 项）、备注",
                       "材料清单（可以新增一项，或改第 1 到第 2 项）、备注"], places)

    import shutil as shutil_module
    import tempfile as tempfile_module
    folder = Path(tempfile_module.mkdtemp(prefix="tod-prompts-"))
    try:
        raw_pack = json_module.loads((PROMPTS_DIR / "答疑.json").read_text(encoding="utf-8"))
        raw_pack["本步"]["解释"] += "（上一稿：{上一稿}）"
        (folder / "答疑.json").write_text(json_module.dumps(raw_pack, ensure_ascii=False), encoding="utf-8")
        provides, has_shape = tools_module.MODEL_TOOLS["答疑"]
        try:
            load_prompt_pack("答疑", provides, has_shape, folder)
            error = None
        except PromptPackError as exc:
            error = exc
        c.check("提示词包的占位符核对：答疑的本步模板多用一个代码提供不了的 {上一稿}，加载就报错，位置写到「本步.解释」",
                error is not None and error.where == "本步.解释" and "{上一稿}" in error.reason, repr(error))
        del raw_pack["本步"]["解释"]
        (folder / "答疑.json").write_text(json_module.dumps(raw_pack, ensure_ascii=False), encoding="utf-8")
        try:
            load_prompt_pack("答疑", provides, has_shape, folder)
            error = None
        except PromptPackError as exc:
            error = exc
        c.check("提示词包的占位符核对：答疑的包缺了代码要用的情形「解释」，加载就报错",
                error is not None and error.where == "本步" and "解释" in error.reason, repr(error))
    finally:
        shutil_module.rmtree(folder, ignore_errors=True)
    c.check("三个调模型的工具的提示词包都能加载，固定指令进系统提示的工具目录",
            all(prompt_pack_of(name).instruction for name in tools_module.MODEL_TOOLS)
            and all(prompt_pack_of(name).instruction in tools_module.tool_catalog([name]) for name in tools_module.MODEL_TOOLS),
            None)
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        jsonschema.Draft202012Validator.check_schema(schema)
        good = {"行为": [{"功能": "INFORM", "回应上一问": False, "内容": {"槽位": "步骤", "路径": [1, "动作"], "值": "付款"},
                          "把握": 0.9, "规范化修订": None}]}
        bad = {"行为": [{"功能": "INFORM", "回应上一问": False, "内容": {"槽位": "备注", "路径": [], "值": "x"},
                         "把握": 0.9, "规范化修订": None}]}
        validator = jsonschema.Draft202012Validator(schema)
        c.check("输出 Schema 是合法的 JSON Schema，清单内的写值通过、写不可写的「备注」被拦下",
                not list(validator.iter_errors(good)) and list(validator.iter_errors(bad)), None)

    def act(function, content=None, confidence=0.9, responds=True):
        return {"功能": function, "回应上一问": responds, "内容": content, "把握": confidence, "规范化修订": None}

    # 第 1 条：解析失败、为空、越出清单，都按没听懂处理。
    for text, reason in (("不是 JSON", "不是 JSON"), ('{"行为": []}', "为空"),
                         (json_module.dumps({"行为": [act("INFORM", {"槽位": "备注", "路径": [], "值": "x"})]}, ensure_ascii=False),
                          "写值越出可写路径清单")):
        acts, problem = tools_module.parse_acts(text, candidates, paths, question)
        c.check(f"规范化第 1 条（{reason}）：解析判不合格，兜底产出一条代码写的 CLARIFY，选项固定三项并带原问",
                acts is None and problem and tools_module.fallback_acts(question)[0]["内容"]["选项"] == ["确认这一稿", "修改这一稿", "先放一放"]
                and tools_module.fallback_acts(question)[0]["内容"]["原问"] == question, problem)
    normalized, dropped = tools_module.normalize_acts(
        [act("INFORM", {"槽位": "标题", "路径": [], "值": "甲"}), act("INFORM", {"槽位": "标题", "路径": [], "值": "乙"})],
        question, FAKE_SLOTS)
    c.check("规范化第 2 条：同一槽位同一路径两项写值，留后一项并记模型原值",
            [a["内容"]["值"] for a in normalized] == ["乙"] and normalized[0]["模型原值"] == [{"槽位": "标题", "路径": [], "值": "甲"}]
            and len(dropped) == 1, normalized)
    normalized, _ = tools_module.normalize_acts([act("DENY", {"问": "x"})], question, FAKE_SLOTS)
    c.check("规范化第 3 条：回应类项带了内容，内容置空并记原值",
            normalized[0]["内容"] is None and normalized[0]["模型原值"] == [{"问": "x"}], normalized)
    choice_question = {"类型": "选择", "槽位": "优先级", "路径": [], "选项": ["高", "低"], "采纳到": None}
    normalized, _ = tools_module.normalize_acts([act("AFFIRM", {"选项序号": 2})], choice_question, FAKE_SLOTS)
    c.check("规范化第 3 条的例外：上一问是选择类时 AFFIRM 带的选项序号保留", normalized[0]["内容"] == {"选项序号": 2}, normalized)
    normalized, dropped = tools_module.normalize_acts(
        [act("INFORM", {"槽位": "标题", "路径": [], "值": "甲"}), act("CLARIFY", {"问话": "哪个？", "选项": ["甲", "乙"]})],
        question, FAKE_SLOTS)
    c.check("规范化第 4 条：CLARIFY 与其他项同时出现，只留 CLARIFY",
            [a["功能"] for a in normalized] == ["CLARIFY"] and len(dropped) == 1, normalized)
    normalized, dropped = tools_module.normalize_acts([act("AFFIRM"), act("DENY"), act("OTHER")], question, FAKE_SLOTS)
    c.check("规范化第 5 条：回应类至多一项，留第一项", [a["功能"] for a in normalized] == ["AFFIRM", "OTHER"], normalized)
    normalized, _ = tools_module.normalize_acts(
        [act("AFFIRM"), act("INFORM", {"槽位": "优先级", "路径": [], "值": "紧急"})], question, FAKE_SLOTS)
    c.check("规范化第 6 条：枚举不在取值内的写值改为 CLARIFY「你说的『紧急』我记成『优先级』，对吗？」，"
            "选项「对」「不对，我重新说」，同一句里的 AFFIRM 保留（不再触发第 4 条）",
            [a["功能"] for a in normalized] == ["AFFIRM", "CLARIFY"]
            and normalized[1]["内容"] == {"问话": "你说的『紧急』我记成『优先级』，对吗？", "选项": ["对", "不对，我重新说"]},
            normalized)

    fallback_choice_question = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": ["确认这一稿", "修改这一稿", "先放一放"],
                                "采纳到": "确认释义", "原问": SUGGEST_QUESTION}
    normalized, dropped = tools_module.normalize_acts(
        [act("AFFIRM", {"选项序号": 3}), act("DEFER", {"槽位": "确认释义"}, responds=False)],
        fallback_choice_question, FAKE_SLOTS)
    c.check("规范化第 7 条：选择类 AFFIRM 选了「先放一放」又另有一项 DEFER 同一槽位，两项落到同一目标同一动作，"
            "留前一项并记重复项（2026-09-18 裁定问题三）",
            [a["功能"] for a in normalized] == ["AFFIRM"] and len(normalized[0].get("重复项", [])) == 1
            and [rule for _, rule in dropped] == ["第 7 条"], normalized)

    # 落数据里两条不经场景的路径：求证类 AFFIRM 写选项里的待写值；代码澄清后的选择类按序号落。
    task_def = glossary_def(dict(GLOSSARY_INPUT_TWO))
    glossary_slots = task_def.DEFINITION["槽位"]
    data = {**task_def.SLOTS, "释义草稿": "草稿", "回复": "对"}
    check_question = {"类型": "求证", "槽位": "术语", "路径": [], "选项": ["需求评审"], "采纳到": None}
    land = tools_module.land_acts([act("AFFIRM")], check_question, data,
                                  writable_paths(glossary_slots, check_question, data), glossary_slots, 0.6)
    c.check("落数据：上一问是求证时 AFFIRM 把选项里的待写值写进上一问的槽位（2026-09-18 裁定问题五）",
            land.work.get("术语") == "需求评审", land.work)
    fallback_choice = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": list(tools_module.FALLBACK_OPTIONS),
                       "采纳到": "确认释义", "原问": SUGGEST_QUESTION}
    outcomes = {}
    for number in (1, 2, 3):
        land = tools_module.land_acts([act("AFFIRM", {"选项序号": number})], fallback_choice, data,
                                      writable_paths(glossary_slots, fallback_choice, data), glossary_slots, 0.6)
        outcomes[number] = (land.work.get("确认释义"), land.routes)
    c.check("落数据：代码澄清的固定三项按序号落——1 把草稿采纳进确认释义，2 不写，3 按 DEFER 路由推迟确认释义"
            "（在插入段里由对话理解换成帧替换，2026-09-18 裁定问题六）",
            outcomes == {1: ("草稿", []), 2: (None, []),
                         3: (None, [{"功能": "DEFER", "输入": {"槽位": "确认释义"}, "替换": True}])}, outcomes)
    free_choice = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": ["甲", "乙"], "采纳到": "确认释义"}
    chosen = act("AFFIRM", {"选项序号": 1})
    land = tools_module.land_acts([chosen], free_choice, data,
                                  writable_paths(glossary_slots, free_choice, data), glossary_slots, 0.6)
    c.check("落数据：自由文字选项的选择类上一问，所选位置不可写时不写并记「选择项不可写，未落」",
            not land.work and chosen.get("落成") == "选择项不可写，未落", chosen.get("落成"))
    modes = tools_module.route_modes_of(task_def)
    c.check("路由表并进了任务定义：REQUEST→答疑、CLARIFY→澄清、DEFER→推迟、INFORM（把握低于阈值）→求证",
            modes == {"REQUEST": "答疑", "CLARIFY": "澄清", "DEFER": "推迟", "INFORM": "求证"}, modes)
    mixed = [act("AFFIRM"), act("REQUEST", {"问": "产出"}, responds=False),
             act("CLARIFY", {"问话": "哪个？", "选项": ["甲", "乙"]}, responds=False),
             act("DEFER", None), act("INFORM", {"槽位": "术语", "路径": [], "值": "需求评审"}, confidence=0.4),
             act("INFORM", {"槽位": "原文片段", "路径": [], "值": "片段"}), act("OTHER", responds=False)]
    land = tools_module.land_acts(mixed, SUGGEST_QUESTION, data, writable_paths(glossary_slots, SUGGEST_QUESTION, data),
                                  glossary_slots, 0.6, modes)
    c.check("路由：AFFIRM 与把握够的写值直接落，OTHER 不落；REQUEST、CLARIFY（模型的选项弃用，带原问）、DEFER（目标取采纳到）、"
            "把握不够的写值按话里的顺序列进待路由的行为，落成写明去向",
            land.work == {"确认释义": "草稿", "原文片段": "片段"}
            and land.routes == [{"功能": "REQUEST", "输入": {"问": "产出"}},
                                {"功能": "CLARIFY", "输入": {"原问": SUGGEST_QUESTION}},
                                {"功能": "DEFER", "输入": {"槽位": "确认释义"}},
                                {"功能": "INFORM", "输入": {"槽位": "术语", "路径": [], "值": "需求评审"}}]
            and [a.get("落成") for a in mixed][1:4] == ["REQUEST → 路由到模式『答疑』", "CLARIFY → 路由到模式『澄清』",
                                                       "DEFER → 路由到模式『推迟』"]
            and mixed[4]["落成"].endswith("INFORM → 路由到模式『求证』") and mixed[6]["落成"] == "不落，记进调用记录",
            (land.work, land.routes))

    normalized, dropped = tools_module.normalize_acts([act("DEFER", {"槽位": "确认释义"}, responds=False)],
                                                      fallback_choice, glossary_slots)
    c.check("澄清再问里使用者说「先放一放」、模型给了 DEFER 原问的推迟目标：规范化成 AFFIRM 选项序号 3，并记模型原值"
            "（2026-09-18 增补裁定第 9 条）",
            [(a["功能"], a["内容"]) for a in normalized] == [("AFFIRM", {"选项序号": 3})]
            and normalized[0]["模型原值"] == [{"功能": "DEFER", "内容": {"槽位": "确认释义"}}] and not dropped, normalized)
    call_model_stub = _stub_caller('{"行为": [{"功能": "DEFER", "回应上一问": true, "内容": {"槽位": "确认释义"}, "把握": 0.9, "规范化修订": null}]}')
    made, _ = _understand_once(task_def, call_model_stub, {**data, "回复": "先放一放。", "上一问": fallback_choice},
                               stack(step_at("确认", 2, loop=(1, 3, 1)), [frame("澄清", 1, {"原问": SUGGEST_QUESTION})]))
    c.check("插入段里同一句「先放一放。」：走帧替换（换成推迟帧），不是本帧再问",
            made.result.get("replace_frame") == {"功能": "DEFER", "输入": {"槽位": "确认释义"}}
            and not made.result.get("reask_in_frame"), (made.result.get("replace_frame"), made.result.get("reask_in_frame")))

    # 地址栈：压帧、选帧里的步骤、弹帧，只由任务定义认识（第五步 4.12 节）。
    def a_call(number, result=None, hit=None):
        made = ToolCall(tool="无所谓", params={}, proposer="selector", basis=(number, "无所谓", hit or {}))
        made.status, made.result = CallStatus.SUCCEEDED, result
        return made

    in_loop = step_at("确认", 2, loop=(1, 3, 1))
    before = stack(step_at("确认", 1, loop=(1, 3, 1)))
    two_routes = {"routes": [{"功能": "REQUEST", "输入": {"问": "产出"}}, {"功能": "DEFER", "输入": {"槽位": "确认释义"}}]}
    pushed = task_def.record_step(before, a_call(4, two_routes))
    c.check("压帧：一句话里两项要路由，按话里的顺序执行，所以倒着压——栈顶（列表末尾）是第一项「答疑」，下面排着「推迟」",
            pushed == stack(in_loop, [frame("推迟", 0, {"槽位": "确认释义"}), frame("答疑", 0, {"问": "产出"})]), pushed)
    chosen_call = task_def.select_call({**data, "回复": None}, pushed)
    answer_no = pattern_number(task_def, "答疑", 1)
    c.check("选帧：插入段不为空时从栈顶那一帧选，依据说明带主线阶段名与「插入段 · 模式 第 n 步」，命中值记下是哪一层",
            chosen_call[0] == "答疑" and chosen_call[1] == {"question": "产出"} and chosen_call[2][0] == answer_no
            and chosen_call[2][1] == "确认 › 插入段 · 答疑 第 1 步 用一两句白话解释使用者问的内容"
            and chosen_call[2][2]["插入段"] == {"层": 1, "模式": "答疑", "步骤": 1, "共": 2}, chosen_call)
    hit = {"插入段": {"层": 1}}
    after_answer = task_def.record_step(pushed, a_call(answer_no, {"输出": "指这个活动交出的东西。"}, hit))
    tell_call = task_def.select_call({**data, "回复": None}, after_answer)
    after_tell = task_def.record_step(after_answer, a_call(pattern_number(task_def, "答疑", 2), "指这个活动交出的东西。", hit))
    c.check("帧内往下走：答疑做完第 1 步，返回值里的「输出」记进帧的结果；告知的参数用「上一步结果」取到它；告知做完弹出栈顶，下面的「推迟」成了栈顶",
            after_answer["插入"][-1] == frame("答疑", 1, {"问": "产出"}, 结果="指这个活动交出的东西。")
            and tell_call[0] == "告知" and tell_call[1] == {"text": "指这个活动交出的东西。"}
            and after_tell == stack(in_loop, [frame("推迟", 0, {"槽位": "确认释义"})]), (tell_call, after_tell))
    defer_tell = task_def.select_call({**data, "回复": None}, after_tell)
    c.check("推迟模式先告知再标记（2026-09-18 裁定问题一）：句式用输入填空",
            defer_tell[0] == "告知" and defer_tell[1] == {"text": "『确认释义』先放着，回头再问。"}, defer_tell)

    clarify_frame = stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION})])
    understand_no = pattern_number(task_def, "澄清", 2)
    reasked = task_def.record_step(clarify_frame, a_call(understand_no, {"reask_in_frame": True,
                                                                          "reask_original": SUGGEST_QUESTION},
                                                         {"插入段": {"层": 0}}))
    reasked_twice = task_def.record_step(stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION}, 再问=1)]),
                                         a_call(understand_no, {"reask_in_frame": True, "reask_original": SUGGEST_QUESTION},
                                                {"插入段": {"层": 0}}))
    given_up = task_def.record_step(stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION}, 再问=2)]),
                                    a_call(understand_no, {"reask_in_frame": True, "reask_original": SUGGEST_QUESTION},
                                           {"插入段": {"层": 0}}))
    c.check("本帧再问：插入段里的对话理解仍含糊，栈顶换成新的澄清帧（从第 1 步起）并记再问次数，不压新帧；"
            "同一帧再问到 2 次后第 3 次仍含糊就弹帧、不落，主线原地续接（2026-09-18 裁定问题五）",
            reasked == stack(in_loop, [frame("澄清", 0, {"原问": SUGGEST_QUESTION}, 再问=1)])
            and reasked_twice == stack(in_loop, [frame("澄清", 0, {"原问": SUGGEST_QUESTION}, 再问=2)])
            and given_up == stack(in_loop), (reasked, given_up))
    replaced = task_def.record_step(clarify_frame, a_call(understand_no, {
        "routes": [], "replace_frame": {"功能": "DEFER", "输入": {"槽位": "确认释义"}}}, {"插入段": {"层": 0}}))
    c.check("帧替换：澄清帧里选了「先放一放」，栈顶换成推迟帧（输入是原问的目标槽位），不嵌套（2026-09-18 裁定问题六）",
            replaced == stack(in_loop, [frame("推迟", 0, {"槽位": "确认释义"})]), replaced)
    c.check("地址栈的插入段不合法（模式不存在、步数越界）抛任务定义错误",
            all(_raises_definition_error(task_def, bad) for bad in (
                stack(in_loop, [frame("没有这个模式", 0, {})]), stack(in_loop, [frame("答疑", 9, {})]))), None)

    # 插入段里不嵌套路由：对话理解整句换成本帧再问；选了「先放一放」换成帧替换。
    call_model_stub = _stub_caller('{"行为": [{"功能": "REQUEST", "回应上一问": false, "内容": {"问": "产出"}, "把握": 0.9, "规范化修订": null}]}')
    made, _ = _understand_once(task_def, call_model_stub, {**data, "回复": "产出是什么", "上一问": fallback_choice},
                               stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION})]))
    c.check("插入段里的对话理解产出 REQUEST 时不压帧：行为列表换成一条代码澄清，返回值标本帧再问并带原问，待路由的行为为空",
            [a["功能"] for a in made.result["acts"]] == ["CLARIFY"] and made.result.get("reask_in_frame") is True
            and made.result.get("reask_original") == SUGGEST_QUESTION and made.result.get("routes") == [], made.result.get("acts"))
    call_model_stub = _stub_caller('{"行为": [{"功能": "AFFIRM", "回应上一问": true, "内容": {"选项序号": 3}, "把握": 0.9, "规范化修订": null}]}')
    made, _ = _understand_once(task_def, call_model_stub, {**data, "回复": "3", "上一问": fallback_choice},
                               stack(in_loop, [frame("澄清", 1, {"原问": SUGGEST_QUESTION})]))
    c.check("插入段里选了「先放一放」：返回值给出帧替换（DEFER，推迟确认释义），不写任何槽位（只清空回复与上一问）",
            made.result.get("replace_frame") == {"功能": "DEFER", "输入": {"槽位": "确认释义"}}
            and {change.slot for change in made.changes} == {"回复", "上一问"}, made.result.get("replace_frame"))
    glossary_text = (TASK_DEFS_DIR / GLOSSARY_FILE).read_text(encoding="utf-8")
    c.check("退役：术语澄清定义里不再有「待处理」槽位与 {pending} 占位；工具模块里不再有待处理拼句",
            "待处理" not in glossary_text and "{pending}" not in glossary_text
            and not hasattr(tools_module, "pending_sentences") and not hasattr(tools_module, "PENDING_SLOT"), None)

    work = Path(tempfile.mkdtemp(prefix="tod-understand-"))
    try:
        for missing in ("回复", "上一问"):
            definition = json_module.loads((TASK_DEFS_DIR / GLOSSARY_FILE).read_text(encoding="utf-8"))
            del definition["槽位"][missing]
            broken = work / f"缺{missing}.json"
            broken.write_text(json_module.dumps(definition, ensure_ascii=False), encoding="utf-8")
            try:
                taskdef.load(broken)
                error = None
            except taskdef.LoadError as exc:
                error = exc
            c.check(f"必备槽位：术语澄清缺「{missing}」时加载报错，位置「槽位」，原因写明对话理解要这个槽位",
                    error is not None and error.where == "槽位" and f"「{missing}」" in error.reason and "对话理解" in error.reason,
                    repr(error))
        empty = work / "empty.json"
        empty.write_text("[]\n", encoding="utf-8")
        call_model, _ = glossary_call(recording=str(empty))
        made, statuses = understand_case(load_eval_cases()[0], call_model)
        c.check("模型不可达（回放时录制文件里没有这条请求）：对话理解记已失败，说明是模型调用那一句错误，不写任何东西",
                made.status == CallStatus.FAILED and statuses and "录制文件里没有这条请求" in statuses[-1][1]
                and not made.changes, statuses)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    glossary = glossary_def(dict(GLOSSARY_INPUT_ONE))
    last = {**SUGGEST_QUESTION, "念的值": "第一稿"}
    changed = _ask_once(glossary, {"释义草稿": "第二稿"}, last)
    same = _ask_once(glossary, {"释义草稿": "第一稿"}, last)
    c.check("询问再问同一个问题：念的值变了（改稿之后）照常全文念，登记的念的值是新值；值没变只说再问短句",
            changed[0] == confirm_utterance(GLOSSARY_TERM_ONE, "第二稿") and changed[1].get("念的值") == "第二稿"
            and same[0] == tools_module.REASK_LINE and same[1] == last, (changed, same))
    between = {"类型": "选择", "槽位": "释义草稿", "路径": [], "选项": FALLBACK_OPTIONS, "采纳到": "确认释义",
               "原问": SUGGEST_QUESTION, "念的值": "第一稿"}
    after_choice = _ask_once(glossary, {"释义草稿": "第一稿"}, last, between)
    c.check("询问再问同一个问题：中间隔着一次选择类登记（澄清段到上限回主线），比对的是最近一次同类型同槽位同路径的登记，草稿没变仍说短句",
            after_choice[0] == tools_module.REASK_LINE, after_choice)
    work = Path(tempfile.mkdtemp(prefix="tod-reask-"))
    try:
        raw = json_module.loads((TASK_DEFS_DIR / GLOSSARY_FILE).read_text(encoding="utf-8"))
        raw["槽位"]["回复"]["再问短句"] = "还是刚才那一稿，确认吗？"
        custom = work / "glossary_reask.json"
        custom.write_text(json_module.dumps(raw, ensure_ascii=False), encoding="utf-8")
        reask = _ask_once(taskdef.load(custom, initial=dict(GLOSSARY_INPUT_ONE)), {"释义草稿": "第一稿"}, last)
        c.check("任务定义在写入目标槽位上写了「再问短句」，再问时说的就是这句",
                reask[0] == "还是刚才那一稿，确认吗？", reask)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return c
