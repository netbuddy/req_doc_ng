"""对话理解（第五步第 4 节）：候选与 Schema 现算、解析、规范化、按行为列表落数据、路由到对话模式。"""

from __future__ import annotations

import copy

from tod_kernel.kernel import CallStatus, Change
from tod_kernel.llm import SHAPE_JSON, LLMError, Request
from tod_kernel.tools.base import ToolSpec, _call_record, _pack, _plain, prompt_pack_of, set_at


READ_VALUE = "念的值"  # 上一问登记里的副本：这一问念的是哪个值
FEEDBACK_SLOT = "修改意见"


# ───────────────────────── 对话理解（第五步第 4 节）─────────────────────────
# 通用工具：把使用者对上一问的回答变成对话行为列表，再按列表落数据。它读的是三个必备槽位（回复、上一问、待处理）
# 与槽位表，不认识任何具体任务；唯一的例外是「修改意见」——建议类上一问的改动要求写进它，这是第五步 4.7 节的裁定。
# 配对表、对话功能、把握阈值、固定句式都写死在这里，第六步抽成表。
# 固定指令与本步段的写法在提示词包 prompts/对话理解.json 里（第五步 4.4A 节）：改一个字，录制文件全部失效要重录。

UNDERSTAND_TOOL = "对话理解"
REPLY_SLOT = "回复"
LAST_QUESTION_SLOT = "上一问"
UNDERSTAND_REQUIRED_SLOTS = {REPLY_SLOT: "文本", LAST_QUESTION_SLOT: "对象"}

# 对话功能：回应类三个、主动类四个、系统侧一个。
AFFIRM, DENY, REQALTS = "AFFIRM", "DENY", "REQALTS"
INFORM, REQUEST, DEFER, OTHER = "INFORM", "REQUEST", "DEFER", "OTHER"
CLARIFY = "CLARIFY"
RESPONSE_FUNCTIONS = (AFFIRM, DENY, REQALTS)
ACTIVE_FUNCTIONS = (INFORM, REQUEST, DEFER, OTHER)

# 上一问的四种类型与配对表：上一问是哪种类型，回应类功能就只能从哪几个里选。
QUESTION_SUGGEST, QUESTION_CHECK, QUESTION_CHOICE, QUESTION_REQUEST = "建议", "求证", "选择", "请求"
PAIRING = {
    QUESTION_SUGGEST: [AFFIRM, DENY, REQALTS],
    QUESTION_CHECK: [AFFIRM, DENY],
    QUESTION_CHOICE: [AFFIRM, DENY],
    QUESTION_REQUEST: [DENY],
}
ORIGINAL_QUESTION = "原问"  # 代码产出的澄清项与由它再问的选择类上一问带着它：被澄清的那一问

# 语义内容的键。
KEY_SLOT, KEY_PATH, KEY_VALUE = "槽位", "路径", "值"
KEY_ASK = "问"
KEY_CHOICE = "选项序号"
KEY_CLARIFY_TEXT, KEY_OPTIONS = "问话", "选项"
ACT_KEYS = ("功能", "回应上一问", "内容", "把握", "规范化修订")
APPEND = "+"  # 路径里的「末尾新增」

# 对话理解返回值里交给任务定义的两个键（第五步 4.12 节）：待路由的行为，与插入段内的「本帧再问」。
ROUTES_KEY = "routes"
REASK_KEY = "reask_in_frame"
REASK_ORIGINAL_KEY = "reask_original"
REPLACE_KEY = "replace_frame"  # 插入段内帧替换：澄清帧里选了「先放一放」，换成推迟帧（2026-09-18 裁定问题六）
STEP_INSERTS = "插入"  # 当前步地址栈里插入段那一层的键；工具只用它判断自己是不是在插入段里

CONFIDENCE_FLOOR = 0.6  # 把握阈值的默认值；配置键 confidence_floor 可改，由程序入口经 build_table 传进来
DEFERRED_MARK = {"已推迟": True}
ALTS_FEEDBACK = "换一份"
# 代码产出的澄清（第 1 条规范化）恒为固定三项，序号含义代码知道：1 按原问的 AFFIRM 落，2 不落，3 按 DEFER 落。
FALLBACK_OPTIONS = ["确认这一稿", "修改这一稿", "先放一放"]
FALLBACK_CHOICE_ADOPT, FALLBACK_CHOICE_REVISE, FALLBACK_CHOICE_DEFER = 1, 2, 3
FALLBACK_TEXT = "我没听懂，你是想：{options}？"
MISMATCH_TEXT = "你说的『{value}』我记成『{slot}』，对吗？"
MISMATCH_OPTIONS = ["对", "不对，我重新说"]

UNDERSTAND_REPLY_LABEL = "（待理解的原话）"
# 提示词（固定指令与五种情形的本步段、可写位置与选项的写法）在提示词包 prompts/对话理解.json 里（第五步 4.4A 节）。
# 八个功能各自的用法说明写在固定指令里，输出 Schema 只留裸结构，不带说明文字。
UNDERSTAND_VALUES = {"槽位", "采纳到", "值", "选项", "可写位置"}
NO_QUESTION = "上一问为空"
UNDERSTAND_PROVIDES = {
    "本步": {kind: set(UNDERSTAND_VALUES) for kind in (QUESTION_SUGGEST, QUESTION_CHECK, QUESTION_CHOICE, QUESTION_REQUEST,
                                                     NO_QUESTION)},
    "片段": {"位置": {"槽位"}, "列表位置·没有项": {"槽位"}, "列表位置·一项": {"槽位"}, "列表位置·多项": {"槽位", "项数"},
           "位置之间": set(), "没有可写位置": set(), "选项": {"序号", "选项"}, "选项之间": set()},
}


def _path_text(path) -> str:
    import json

    return json.dumps(list(path), ensure_ascii=False)


def understand_schema(candidates, paths, question) -> dict:
    """输出 Schema：每次调用现算，同时进「输出形状」段与接口参数 response_format。

    三栏枚举写死进去——功能取候选功能集，写值的槽位与路径按可写路径清单逐槽位配对，推迟的槽位取清单里的槽位。
    每项按功能分支（anyOf）：选定功能后内容只能是第五步 4.5 节表里配给它的形状。模型服务照 Schema 逐键生成，
    「功能」排第一个键，所以功能一定下来，内容的形状就被约束住（实测只给一个不分功能的内容 anyOf 时，
    模型会给 DEFER 配上选项序号、给 REQUEST 配上槽位）。
    Schema 里不带任何说明文字（description）：各功能的用法写在提示词包的固定指令里（第五步 4.4A 节）。
    """
    by_slot: dict = {}
    for row in paths:
        by_slot.setdefault(row[KEY_SLOT], []).append(list(row[KEY_PATH]))
    null = {"type": "null"}
    choice = {"type": "object", "properties": {KEY_CHOICE: {"type": "integer", "minimum": 1}},
              "required": [KEY_CHOICE], "additionalProperties": False}
    writes = [{"type": "object",
               "properties": {KEY_SLOT: {"enum": [slot]}, KEY_PATH: {"enum": slot_paths}, KEY_VALUE: {}},
               "required": [KEY_SLOT, KEY_PATH, KEY_VALUE], "additionalProperties": False}
              for slot, slot_paths in by_slot.items()]
    asked = {"type": "object", "properties": {KEY_ASK: {"type": "string"}},
             "required": [KEY_ASK], "additionalProperties": False}
    deferred = {"type": "object", "properties": {KEY_SLOT: {"enum": list(by_slot)}},
                "required": [KEY_SLOT], "additionalProperties": False}
    clarify = {"type": "object",
               "properties": {KEY_CLARIFY_TEXT: {"type": "string"},
                              KEY_OPTIONS: {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3}},
               "required": [KEY_CLARIFY_TEXT, KEY_OPTIONS], "additionalProperties": False}
    is_choice = isinstance(question, dict) and question.get("类型") == QUESTION_CHOICE
    shapes = {AFFIRM: [null, choice] if is_choice else [null], DENY: [null], REQALTS: [null], OTHER: [null],
              INFORM: writes, REQUEST: [asked], DEFER: [null, deferred] if by_slot else [null], CLARIFY: [clarify]}
    revision = {"anyOf": [null, {"type": "object",
                                 "properties": {"原文": {"type": "string"}, "修订后": {"type": "string"}},
                                 "required": ["原文", "修订后"], "additionalProperties": False}]}
    variants, grouped = [], {}
    for function in candidates:
        if not shapes[function]:
            continue  # 清单为空时没有 INFORM 这一支
        key = str(shapes[function])
        if key in grouped:  # 内容形状相同的功能并成一支，枚举里列几个
            grouped[key]["properties"]["功能"]["enum"].append(function)
            continue
        content = shapes[function][0] if len(shapes[function]) == 1 else {"anyOf": shapes[function]}
        variant = {"type": "object",
                   "properties": {"功能": {"enum": [function]}, "回应上一问": {"type": "boolean"}, "内容": content,
                                  "把握": {"type": "number", "minimum": 0, "maximum": 1}, "规范化修订": revision},
                   "required": list(ACT_KEYS), "additionalProperties": False}
        grouped[key] = variant
        variants.append(variant)
    return {"type": "object", "properties": {"行为": {"type": "array", "items": {"anyOf": variants}}},
            "required": ["行为"], "additionalProperties": False}


# ── 校验与规范化（第五步 4.6 节）──

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _content_shape(content):
    """内容是五种形状里的哪一种：空、选项序号、写值、提问、推迟、澄清；都不是返回 None。"""
    if content is None:
        return "空"
    if not isinstance(content, dict):
        return None
    keys = set(content)
    if keys == {KEY_CHOICE} and isinstance(content[KEY_CHOICE], int) and not isinstance(content[KEY_CHOICE], bool):
        return "选项序号"
    if keys == {KEY_SLOT, KEY_PATH, KEY_VALUE} and isinstance(content[KEY_SLOT], str) and isinstance(content[KEY_PATH], list):
        return "写值"
    if keys == {KEY_ASK} and isinstance(content[KEY_ASK], str):
        return "提问"
    if keys == {KEY_SLOT} and isinstance(content[KEY_SLOT], str):
        return "推迟"
    if (keys == {KEY_CLARIFY_TEXT, KEY_OPTIONS} and isinstance(content[KEY_CLARIFY_TEXT], str)
            and isinstance(content[KEY_OPTIONS], list) and 2 <= len(content[KEY_OPTIONS]) <= 3
            and all(isinstance(option, str) for option in content[KEY_OPTIONS])):
        return "澄清"
    return None


def parse_acts(text, candidates, paths, question):
    """解析模型回的 JSON 并做结构校验。返回（行为列表, 毛病）：毛病不为 None 时按第 1 条规范化处理。

    结构校验只管 Schema 管得住的：键齐全、功能在候选内、内容是五种形状之一、写值的槽位与路径在清单内、把握是 0 到 1 的数。
    功能与内容配不上的情形里，第 3 条规范化管得了的（回应类带了内容、OTHER 带了内容）留给规范化，其余算不合 Schema。
    """
    import json

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, "模型回的不是 JSON"
    if not isinstance(parsed, dict) or set(parsed) != {"行为"} or not isinstance(parsed["行为"], list):
        return None, "模型回的 JSON 不是 {\"行为\": [...]}"
    if not parsed["行为"]:
        return None, "行为列表为空"
    allowed = set(candidates) | {CLARIFY}
    listed = {(row[KEY_SLOT], json.dumps(row[KEY_PATH], ensure_ascii=False)) for row in paths}
    slots = {row[KEY_SLOT] for row in paths}
    acts = []
    for index, item in enumerate(parsed["行为"]):
        where = f"第 {index + 1} 项"
        if not isinstance(item, dict) or set(item) != set(ACT_KEYS):
            return None, f"{where}的键不是 {'、'.join(ACT_KEYS)}"
        function, content = item["功能"], item["内容"]
        if function not in allowed:
            return None, f"{where}的功能「{function}」不在候选功能集里"
        if not isinstance(item["回应上一问"], bool):
            return None, f"{where}的「回应上一问」不是布尔值"
        if not (_is_number(item["把握"]) and 0 <= item["把握"] <= 1):
            return None, f"{where}的把握不是 0 到 1 的数"
        revision = item["规范化修订"]
        if revision is not None and not (isinstance(revision, dict) and set(revision) == {"原文", "修订后"}
                                         and all(isinstance(v, str) for v in revision.values())):
            return None, f"{where}的规范化修订形状不对"
        shape = _content_shape(content)
        if shape is None:
            return None, f"{where}的内容不是五种形状之一"
        if shape == "写值" and (content[KEY_SLOT], json.dumps(content[KEY_PATH], ensure_ascii=False)) not in listed:
            return None, f"{where}写值的槽位与路径不在可写路径清单里"
        if shape == "推迟" and content[KEY_SLOT] not in slots and content[KEY_SLOT] != (question or {}).get(KEY_SLOT):
            return None, f"{where}推迟的槽位不可写"
        if function == INFORM and shape != "写值":
            return None, f"{where}是 INFORM 却没有写值"
        if function == REQUEST and shape != "提问":
            return None, f"{where}是 REQUEST 却没有提问"
        if function == DEFER and shape not in ("推迟", "空"):
            return None, f"{where}是 DEFER 却不是推迟的内容"
        if function == CLARIFY and shape != "澄清":
            return None, f"{where}是 CLARIFY 却没有问话与两三个选项"
        if shape in ("写值", "提问", "澄清") and function not in (INFORM, REQUEST, CLARIFY) + RESPONSE_FUNCTIONS + (OTHER,):
            return None, f"{where}的功能与内容配不上"
        acts.append({key: copy.deepcopy(item[key]) for key in ACT_KEYS})
    return acts, None


def fallback_acts(question) -> list:
    """第 1 条规范化：低把握的空理解，产出一条代码写的 CLARIFY，选项恒为固定三项，并带上被澄清的那一问。"""
    return [{"功能": CLARIFY, "回应上一问": False,
             "内容": {KEY_CLARIFY_TEXT: FALLBACK_TEXT.format(options="／".join(FALLBACK_OPTIONS)),
                    KEY_OPTIONS: list(FALLBACK_OPTIONS), ORIGINAL_QUESTION: copy.deepcopy(_original_of(question))},
             "把握": 0.0, "规范化修订": None, "规范化": ["第 1 条：模型输出不可用，按低把握空理解处理"]}]


def _original_of(question):
    """被澄清的那一问：上一问本身就是代码澄清之后的再问时，取它记着的原问，免得原问一层套一层。

    「念的值」只给询问比对再问用，不带进原问（原问会进插入段的输入与观测台，带着整段草稿没有用处）。
    """
    if isinstance(question, dict) and ORIGINAL_QUESTION in question:
        question = question[ORIGINAL_QUESTION]
    if isinstance(question, dict) and READ_VALUE in question:
        question = {key: value for key, value in question.items() if key != READ_VALUE}
    return question


def value_fits(slots_meta, slot, path, value) -> bool:
    """写值的值类型与槽位（或列表项字段）类型相符：文本是字符串、数字是数、布尔是布尔、枚举在取值内、列表是列表、对象是字典。"""
    meta = slots_meta.get(slot) or {}
    kind = meta.get("类型")
    if path:
        if kind == "列表":
            item_meta = meta.get("项")
            if len(path) == 1:
                return (not item_meta) or (isinstance(value, dict) and set(value) <= set(item_meta))
            field_meta = (item_meta or {}).get(path[1])
            if field_meta is None:
                return False
            return _type_fits(field_meta, value)
        return True  # 对象槽位按键写，键下的值没有元数据可核
    return _type_fits(meta, value)


def _type_fits(meta, value) -> bool:
    kind = meta.get("类型")
    if kind == "文本":
        return isinstance(value, str)
    if kind == "数字":
        return _is_number(value)
    if kind == "布尔":
        return isinstance(value, bool)
    if kind == "枚举":
        return value in (meta.get("取值") or [])
    if kind == "列表":
        return isinstance(value, list)
    if kind == "对象":
        return isinstance(value, dict)
    return False


def normalize_acts(acts, question, slots_meta) -> tuple:
    """第 2 到第 7 条规范化，按编号顺序执行。返回（规范化后的行为列表, 删掉的项）。

    每项被改动时在「规范化」里记一句是哪一条、做了什么，原来的内容记进「模型原值」。
    第 6 条产生的 CLARIFY 不再触发第 4 条（2026-09-18 裁定问题八）。
    """
    import json

    acts = [dict(act, 规范化=list(act.get("规范化") or [])) for act in acts]
    dropped = []
    # 代码固定三项的澄清再问里，使用者用话说「先放一放」而模型给了 DEFER：目标就是原问的推迟目标时，按选项 3 处理，
    # 在插入段里因此走帧替换而不是本帧再问（2026-09-18 增补裁定第 9 条）。先于第 2 到第 7 条执行。
    # 同一句里已经有回应类项（例如 AFFIRM 选项序号 3）时不改写，那一项 DEFER 留给第 7 条记成重复项。
    if (isinstance(question, dict) and question.get("类型") == QUESTION_CHOICE and ORIGINAL_QUESTION in question
            and not any(act["功能"] in RESPONSE_FUNCTIONS for act in acts)):
        target = defer_target(question.get(ORIGINAL_QUESTION), None)
        for act in acts:
            if act["功能"] == DEFER and defer_target(question, act["内容"]) == target:
                act.setdefault("模型原值", []).append({"功能": DEFER, "内容": copy.deepcopy(act["内容"])})
                act.update({"功能": AFFIRM, "回应上一问": True, "内容": {KEY_CHOICE: FALLBACK_CHOICE_DEFER}})
                act["规范化"].append("澄清再问里推迟原问的目标，按选项 3「先放一放」处理")
    # 第 2 条：同一槽位同一路径两项写值，留后一项。
    last_of = {}
    for index, act in enumerate(acts):
        if act["功能"] == INFORM:
            last_of[(act["内容"][KEY_SLOT], json.dumps(act["内容"][KEY_PATH], ensure_ascii=False))] = index
    kept = []
    for index, act in enumerate(acts):
        if act["功能"] == INFORM:
            key = (act["内容"][KEY_SLOT], json.dumps(act["内容"][KEY_PATH], ensure_ascii=False))
            if last_of[key] != index:
                later = acts[last_of[key]]
                later.setdefault("模型原值", []).append(copy.deepcopy(act["内容"]))
                later["规范化"].append("第 2 条：同一槽位同一路径有两项写值，留后一项")
                dropped.append((copy.deepcopy(act), "第 2 条"))
                continue
        kept.append(act)
    acts = kept
    # 第 3 条：回应类带了内容（选择类的选项序号除外）置空；OTHER 带了内容也置空。
    choice = isinstance(question, dict) and question.get("类型") == QUESTION_CHOICE
    for act in acts:
        exempt = act["功能"] == AFFIRM and choice and _content_shape(act["内容"]) == "选项序号"
        if act["功能"] in RESPONSE_FUNCTIONS + (OTHER,) and act["内容"] is not None and not exempt:
            act.setdefault("模型原值", []).append(copy.deepcopy(act["内容"]))
            act["内容"] = None
            act["规范化"].append("第 3 条：回应类项带了内容，置空")
    # 第 4 条：CLARIFY 与其他项同时出现，只留第一条 CLARIFY。
    clarifies = [act for act in acts if act["功能"] == CLARIFY]
    if clarifies and len(acts) > 1:
        dropped.extend((copy.deepcopy(act), "第 4 条") for act in acts if act is not clarifies[0])
        clarifies[0]["规范化"].append("第 4 条：CLARIFY 与其他项同时出现，只留 CLARIFY")
        acts = [clarifies[0]]
    # 第 5 条：回应类至多一项，留第一项。
    seen_response = False
    kept = []
    for act in acts:
        if act["功能"] in RESPONSE_FUNCTIONS:
            if seen_response:
                dropped.append((copy.deepcopy(act), "第 5 条"))
                continue
            seen_response = True
        kept.append(act)
    acts = kept
    # 第 6 条：写值的值类型与槽位类型不符，改为 CLARIFY。
    for act in acts:
        if act["功能"] == INFORM and not value_fits(slots_meta, act["内容"][KEY_SLOT], act["内容"][KEY_PATH],
                                                   act["内容"][KEY_VALUE]):
            act.setdefault("模型原值", []).append(copy.deepcopy(act["内容"]))
            act["功能"] = CLARIFY
            act["内容"] = {KEY_CLARIFY_TEXT: MISMATCH_TEXT.format(value=_plain(act["内容"][KEY_VALUE]),
                                                               slot=act["内容"][KEY_SLOT]),
                         KEY_OPTIONS: list(MISMATCH_OPTIONS)}
            act["规范化"].append("第 6 条：值的类型与槽位类型不符，改为 CLARIFY")
    # 第 7 条：两项落到同一目标同一动作（选择类 AFFIRM 选了推迟，又另有一项 DEFER 同一槽位），留前一项，记重复项
    #（2026-09-18 裁定问题三）。
    kept, seen = [], {}
    for act in acts:
        key = _landing_key(act, question)
        if key is not None and key in seen:
            first = seen[key]
            first.setdefault("重复项", []).append(copy.deepcopy(act))
            first["规范化"].append(f"第 7 条：另有一项 {act['功能']} 落到同一目标同一动作，留前一项，记重复项")
            dropped.append((copy.deepcopy(act), "第 7 条"))
            continue
        if key is not None:
            seen[key] = act
        kept.append(act)
    return kept, dropped


def _landing_key(act, question):
    """一项会落成的（动作, 目标槽位）；只算推迟这一种动作，其余返回 None（同槽位同路径的写值由第 2 条管）。"""
    question = question if isinstance(question, dict) else {}
    if act["功能"] == DEFER:
        return ("推迟", defer_target(question, act["内容"]))
    choice = (act["内容"] or {}).get(KEY_CHOICE) if isinstance(act["内容"], dict) else None
    if (act["功能"] == AFFIRM and question.get("类型") == QUESTION_CHOICE and ORIGINAL_QUESTION in question
            and choice == FALLBACK_CHOICE_DEFER):
        return ("推迟", defer_target(question.get(ORIGINAL_QUESTION), None))
    return None


# ── 按行为列表落数据（第五步 4.7 节）──

class _Landing:
    """一次对话理解落下的全部改动：先在工作副本上改，最后每个槽位出一条变更（旧值是执行前的值）。"""

    def __init__(self, data):
        self.data = data
        self.work: dict = {}
        self.routes: list = []  # 待路由的行为：{功能, 输入}，由任务定义按路由表压成插入段
        self.appended: dict = {}  # 槽位 → 这次「末尾新增」出来的那一项的下标（同一句里的几个 ["+", 字段] 并进同一项）

    def get(self, slot):
        return self.work[slot] if slot in self.work else copy.deepcopy(self.data.get(slot))

    def write(self, slot, path, value) -> None:
        current = self.get(slot)
        path = list(path)
        if path and path[0] == APPEND:
            items = list(current or [])
            if len(path) == 1:
                items.append(copy.deepcopy(value))
                self.appended[slot] = len(items) - 1
            else:
                if slot not in self.appended:
                    items.append({})
                    self.appended[slot] = len(items) - 1
                items[self.appended[slot]] = set_at(items[self.appended[slot]], path[1:], value)
            self.work[slot] = items
            return
        self.work[slot] = set_at(current, path, value)

    def changes(self, call_id) -> list:
        return [Change(slot, copy.deepcopy(self.data.get(slot)), value, call_id)
                for slot, value in self.work.items() if value != self.data.get(slot)]


def _get_at(value, path):
    for step in path or []:
        if isinstance(value, list) and isinstance(step, int) and 0 <= step < len(value):
            value = value[step]
        elif isinstance(value, dict):
            value = value.get(step)
        else:
            return None
    return value


def _land_affirm_as(question, land, paths, act) -> str:
    """按上一问的类型落 AFFIRM。选择类要看选项从哪来：代码产出的固定三项按序号落，模型写的自由文字写进上一问的位置。"""
    import json

    kind = question.get("类型")
    slot, path = question.get(KEY_SLOT), list(question.get(KEY_PATH) or [])
    if kind == QUESTION_SUGGEST:
        adopt = question.get("采纳到")
        if not adopt:
            return "上一问没有登记采纳到，不写"
        land.write(adopt, [], _get_at(land.get(slot), path))
        return f"把「{slot}」的当前值写进「{adopt}」"
    if kind == QUESTION_CHECK:
        options = question.get(KEY_OPTIONS) or []
        if not options:
            return "求证类上一问没有待写的值，不写"
        land.write(slot, path, options[0])
        return f"把求证的值写进「{slot}」{_path_text(path)}"
    if kind == QUESTION_CHOICE:
        choice = (act["内容"] or {}).get(KEY_CHOICE)
        options = question.get(KEY_OPTIONS) or []
        if not (isinstance(choice, int) and 1 <= choice <= len(options)):
            return "没有给出有效的选项序号，不写"
        original = question.get(ORIGINAL_QUESTION)
        if ORIGINAL_QUESTION in question:  # 代码产出的固定三项
            if choice == FALLBACK_CHOICE_ADOPT:
                if not isinstance(original, dict):
                    return "选了「确认这一稿」，但被澄清的那一问为空，不写"
                return "选了「确认这一稿」：" + _land_affirm_as(original, land, paths, {"内容": None})
            if choice == FALLBACK_CHOICE_REVISE:
                return "选了「修改这一稿」：不写，下一次照常再念"
            target = defer_target(original, None, {row[KEY_SLOT] for row in paths})
            land.routes.append({"功能": DEFER, "输入": {KEY_SLOT: target}, "替换": True})
            return f"选了「先放一放」：按 DEFER 路由，推迟「{target}」"
        listed = {(row[KEY_SLOT], json.dumps(row[KEY_PATH], ensure_ascii=False)) for row in paths}
        if (slot, json.dumps(path, ensure_ascii=False)) not in listed:
            return "选择项不可写，未落"
        land.write(slot, path, options[choice - 1])
        return f"把所选的「{options[choice - 1]}」写进「{slot}」{_path_text(path)}"
    return "上一问不是建议、求证或选择，不写"


def defer_target(question, content, writable=None):
    """推迟的目标槽位：模型给了可写的槽位就用它；没给或给的是上一问的槽位（不可写的草稿）时，上一问有采纳到就取采纳到。

    writable 是可写槽位名的集合；不给时把上一问的槽位一律当作不可写（评测比对时用）。
    """
    question = question if isinstance(question, dict) else {}
    slot = (content or {}).get(KEY_SLOT) if isinstance(content, dict) else None
    if slot is None or (slot == question.get(KEY_SLOT) and (writable is None or slot not in writable)):
        slot = question.get("采纳到") or question.get(KEY_SLOT)
    return slot


def land_acts(acts, question, data, paths, slots_meta, floor, route_modes=None):
    """按规范化后的行为列表落数据，返回落数据的工作副本（其中 routes 是待路由的行为）。

    回应类与把握够的写值直接落（第五步 4.7 节）；REQUEST、CLARIFY、DEFER 与把握不够的写值不落，列进待路由的行为，
    由任务定义按路由表压成插入段（4.12 节）。route_modes 是路由表（功能 → 模式名），只用来写「落成」那句去向。
    回复与上一问不在这里清空，由调用方最后清空。
    """
    route_modes = route_modes or {}
    land = _Landing(data)
    land.slots_meta = slots_meta
    question_dict = question if isinstance(question, dict) else {}
    writable = {row[KEY_SLOT] for row in paths}
    informs = [act for act in acts if act["功能"] == INFORM and act["把握"] >= floor]

    def route(act, function, payload, head=""):
        land.routes.append({"功能": function, "输入": payload})
        act["落成"] = f"{head}{function} → 路由到模式『{route_modes.get(function, '？')}』"

    for act in acts:
        function, content = act["功能"], act["内容"]
        if function == AFFIRM:
            adopt = question_dict.get("采纳到")
            if question_dict.get("类型") == QUESTION_SUGGEST and any(i["内容"][KEY_SLOT] == adopt for i in informs):
                act["落成"] = f"使用者给了完整的新值，按写值落进「{adopt}」，不再抄草稿"
            elif question_dict:
                act["落成"] = _land_affirm_as(question_dict, land, paths, act)
            else:
                act["落成"] = "没有上一问，不写"
        elif function == DENY:
            act["落成"] = "不写"
        elif function == REQALTS:
            if any(i["内容"][KEY_SLOT] == FEEDBACK_SLOT for i in informs):
                act["落成"] = f"同一句里另有写值写进「{FEEDBACK_SLOT}」，按写值落"
            else:
                land.write(FEEDBACK_SLOT, [], ALTS_FEEDBACK)
                act["落成"] = f"「{FEEDBACK_SLOT}」写「{ALTS_FEEDBACK}」"
        elif function == INFORM:
            slot, path, value = content[KEY_SLOT], content[KEY_PATH], content[KEY_VALUE]
            if act["把握"] >= floor:
                land.write(slot, path, value)
                act["落成"] = f"写进「{slot}」{_path_text(path)}"
            else:
                route(act, INFORM, {KEY_SLOT: slot, KEY_PATH: list(path), KEY_VALUE: copy.deepcopy(value)},
                      f"把握 {act['把握']} 低于阈值 {floor}：")
        elif function == REQUEST:
            route(act, REQUEST, {KEY_ASK: content[KEY_ASK]})
        elif function == DEFER:
            route(act, DEFER, {KEY_SLOT: defer_target(question_dict, content, writable)})
        elif function == CLARIFY:
            # 澄清模式对所有原问都用代码固定三项再问，模型写的选项一律弃用（2026-09-18 裁定问题七）
            original = content[ORIGINAL_QUESTION] if ORIGINAL_QUESTION in content else _original_of(question)
            route(act, CLARIFY, {ORIGINAL_QUESTION: copy.deepcopy(original)})
        else:
            act["落成"] = "不落，记进调用记录"
    return land


def understand(ctx, call_model, task_def=None, read_events=None, system_prompt="",
               confidence_floor=CONFIDENCE_FLOOR) -> None:
    """对话理解：把「回复」里使用者的原话变成对话行为列表，校验规范化后按列表落数据，最后清空「回复」与「上一问」。

    候选功能集与可写路径清单由代码算（context.py），进输出 Schema 的枚举作接口参数；本步段按上一问的类型选提示词包里的
    一种白话写法，用中文说能判成哪几种回答、可写的位置有哪些，不出现功能标识与代码名（第五步 4.4A 节）。
    模型输出不可用时按第 1 条规范化产出一条代码写的 CLARIFY，不重试；模型调用本身失败（服务不可达、回放查不到）记已失败。
    返回值＝模型调用记录（全段）＋规范化后的行为列表（每项的落成）＋删掉的项＋这句原话。
    """
    import json

    from tod_kernel.context import ContextPack, candidate_functions, render, understand_step_text, writable_paths

    call = ctx.call
    data = dict(ctx.data_view)
    slots_meta = task_def.DEFINITION.get("槽位", {})
    question = data.get(LAST_QUESTION_SLOT)
    reply_text = data.get(REPLY_SLOT)
    candidates = candidate_functions(question)
    paths = writable_paths(slots_meta, question, data)
    schema = understand_schema(candidates, paths, question)
    pack = _pack(task_def, ctx, read_events)
    focus = [name for name in slots_meta
             if name not in UNDERSTAND_REQUIRED_SLOTS and slots_meta[name].get("使用者可写", True) is not False]
    if isinstance(question, dict) and question.get("类型") == QUESTION_SUGGEST and FEEDBACK_SLOT in slots_meta \
            and FEEDBACK_SLOT not in focus:
        focus.append(FEEDBACK_SLOT)
    segments = [pack.progress(),
                pack.dialogue(),
                pack.data_segment(focus + [(REPLY_SLOT, UNDERSTAND_REPLY_LABEL)]),
                ContextPack.step(understand_step_text(prompt_pack_of(UNDERSTAND_TOOL), question, paths, slots_meta, data)),
                ContextPack.shape(SHAPE_JSON, json.dumps(schema, ensure_ascii=False), api_note=True)]
    request = Request(system=system_prompt, user=render(segments), shape=SHAPE_JSON, json_schema=schema)
    try:
        reply = call_model(request)
    except LLMError as exc:
        ctx.set_status(CallStatus.FAILED, exc.brief)
        return
    parsed_acts, problem = parse_acts(reply.text, candidates, paths, question)
    try:
        parsed = json.loads(reply.text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if problem is not None:
        acts, dropped = fallback_acts(question), []
    else:
        acts, dropped = normalize_acts(parsed_acts, question, slots_meta)
    route_modes = route_modes_of(task_def)
    land = land_acts(acts, question, data, paths, slots_meta, confidence_floor, route_modes)
    in_frame = isinstance(ctx.step, dict) and bool(ctx.step.get(STEP_INSERTS))
    reask, replace = False, None
    if in_frame and land.routes:
        if all(item.get("替换") for item in land.routes):
            # 澄清帧里选了「先放一放」：帧替换成推迟帧，不嵌套（2026-09-18 裁定问题六）
            replace = {"功能": land.routes[0]["功能"], "输入": land.routes[0]["输入"]}
        else:
            # 插入段里不再嵌套路由：整句换成一条代码澄清，在本帧内以选择类再问（4.12 节边界）
            original = _original_of(question)
            acts, dropped = fallback_acts(question), [{"项": act, "规范化": "插入段内不嵌套"} for act in acts]
            for act in acts:
                act["落成"] = "插入段里不再压帧：本帧内以选择类再问"
            land = _Landing(data)
            reask = True
    elif land.routes:
        for item in land.routes:
            item.pop("替换", None)
    land.work[REPLY_SLOT] = None
    land.work[LAST_QUESTION_SLOT] = None
    writes = {}
    for number, act in enumerate(acts, start=1):
        content = act["内容"] if isinstance(act["内容"], dict) else {}
        if act["功能"] == INFORM and content.get(KEY_SLOT) in land.work and act["把握"] >= confidence_floor:
            writes[f"{number} {act['功能']}"] = content[KEY_SLOT]
    record = _call_record(reply, segments, writes, parsed)
    record["reply"] = reply_text
    record["acts"] = acts
    record["dropped"] = [item if isinstance(item, dict) else {"项": item[0], "规范化": item[1]} for item in dropped]
    record["problem"] = problem
    record["landed"] = [act["落成"] for act in acts]
    record[ROUTES_KEY] = [] if (reask or replace) else land.routes
    if reask:
        record[REASK_KEY] = True
        record[REASK_ORIGINAL_KEY] = copy.deepcopy(original)
    if replace:
        record[REPLACE_KEY] = replace
    call.result = record
    call.changes = land.changes(call.call_id)
    functions = "、".join(act["功能"] for act in acts)
    note = f"理解出 {len(acts)} 项：{functions}" + (f"（{problem}，按没听懂处理）" if problem else "")
    ctx.set_status(CallStatus.SUCCEEDED, note)


def route_modes_of(task_def) -> dict:
    """路由表（功能 → 模式名），取自任务定义加载时并进来的对话模式定义；没有就是空表。"""
    patterns = (getattr(task_def, "DEFINITION", {}) or {}).get("对话模式") or {}
    return {row["功能"]: row["模式"] for row in patterns.get("路由表", [])}


UNDERSTAND_NOTE = "回复不为 None"


def _understand_precondition(data, params):
    reply = data.get(REPLY_SLOT)
    return reply is not None, UNDERSTAND_NOTE, {REPLY_SLOT: reply, LAST_QUESTION_SLOT: data.get(LAST_QUESTION_SLOT)}


# ───────────────────────── 本模块登记的工具 ─────────────────────────

# 静态工具规格：工具名 → ToolSpec，由 base 汇总成静态工具表 STATIC_TOOLS。
TOOL_SPECS = {
    UNDERSTAND_TOOL: ToolSpec(UNDERSTAND_TOOL, (), _understand_precondition, None,
                              category="加工任务数据",
                              summary="把使用者对上一问的回答理解成对话行为列表，按列表写入槽位或登记待处理，并清空回复与上一问",
                              # 写进槽位的是使用者的话，模型只是把它认出来；待处理是系统记下的待办。
                              writer_roles={"*": "使用者"},
                              required_slots=dict(UNDERSTAND_REQUIRED_SLOTS), uses_patterns=True),
}

# 工具实现：工具名 → 函数（任务定义）→ 实现，由 base 汇总；模型调用件、读事件函数与系统提示由 build_table 另外绑上。
TOOL_IMPLS = {
    UNDERSTAND_TOOL: lambda task_def: understand,
}

# 内部调用模型的工具：值是（本步与片段的占位符声明, 提示词包里有没有输出形状）。
MODEL_TOOLS = {UNDERSTAND_TOOL: (UNDERSTAND_PROVIDES, False)}
