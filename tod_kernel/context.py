"""上下文包：把任务数据与事件流投影成模型读得懂的段。

模型每次调用看到的用户内容由六种段组成，段头一律是【段名 · 来源】。哪几段、各段装什么，由工具声明；
怎么从任务数据与事件流算出来，全在本模块，而且只用确定性规则——同一份数据与事件拼出的段逐字相同，
回放时才对得上请求哈希。

模块位置：本模块读事件名与任务定义的投影，所以不放在模型调用件（llm.py）里——那边只认识系统提示、
用户内容、输出形状三样东西与三种模式。事件从哪来也不由本模块决定：宿主在建运行工具表时把一个
「读事件」函数交给工具，工具再传进来（内核不为此改动）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from tod_kernel.kernel import CALL_PROPOSED, DATA_CHANGED, INBOX, MESSAGE_PUT, OUTBOX
from tod_kernel.taskdef import STAGE_NAME_SEPARATOR, predicate_text

# 六种段类型，顺序固定：工具声明要哪几段，组装时一律按这个顺序排。
SEG_PROGRESS = "任务进度"
SEG_DIALOGUE = "对话历史"
SEG_DATA = "当前数据"
SEG_MATERIAL = "参考材料"
SEG_STEP = "本步"
SEG_SHAPE = "输出形状"
SEG_ORDER = (SEG_PROGRESS, SEG_DIALOGUE, SEG_DATA, SEG_MATERIAL, SEG_STEP, SEG_SHAPE)

# 系统标记：这三个词在系统提示的「上下文约定」段里讲过，模型知道它们不是内容。
EMPTY_MARK = "（空）"
MISSING_MARK = "（未提供）"
NO_DIALOGUE_MARK = "（无）"
OMITTED_TEMPLATE = "【更早 {n} 轮已省略】"
REVISION_LABEL = "修订记录（系统从变更事件推出）"  # 当前数据段里的最后一项，衍生自数据变更事件

DEFAULT_DIALOGUE_BUDGET = 800  # 对话历史的字符上限，配置里可改

SPEAKER_SYSTEM = "系统："
SPEAKER_USER = "使用者："

API_NOTE = "（同一份结构也作为接口参数传给模型服务，输出必须符合它）"

SCOPE_STAGE = "stage"  # 只装当前阶段内的问答（默认）
SCOPE_TASK = "task"  # 整个任务的问答


@dataclass(frozen=True)
class Segment:
    """用户内容里的一段：类型、来源、正文。发给模型的正文一律逐字原文，不改写、不摘要、不截句。"""

    type: str
    source: str
    text: str

    def render(self) -> str:
        return f"【{self.type} · {self.source}】\n{self.text}"

    def as_record(self, changed: bool = False) -> dict:
        """进模型调用记录的形状。changed 是「与上一次调用比变了没有」，由调用方比对后填。"""
        return {"type": self.type, "source": self.source, "text": self.text, "changed": changed}


def render(segments) -> str:
    """把段按给定顺序拼成用户内容。"""
    return "\n\n".join(segment.render() for segment in segments)


SPEAKER_NAMES = {"系统": SPEAKER_SYSTEM, "使用者": SPEAKER_USER}
KIND_QUESTION, KIND_NOTICE, KIND_ANSWER = "提问", "告知", "回答"


@dataclass(frozen=True)
class Utterance:
    """话轮（turn）：一方的一段发言，对话历史的存储单位（04 文档 4.5 节用词更正）。

    系统的提问、系统的告知、使用者的回答各是一条；没有得到回答的提问也照记。
    说话方是「系统」或「使用者」；原文逐字；事件序号是它进收发件箱那条事件的序号；阶段取所属工具调用的依据说明；
    另记两项供三条规则用：种类（提问、告知、回答）与写入槽位（提问的写入目标，回答跟着它的提问，告知没有）。
    """

    speaker: str
    text: str
    seq: int
    stage: str
    call_id: int
    kind: str
    slot: str | None

    def render(self) -> str:
        return f"{SPEAKER_NAMES[self.speaker]}{self.text}"


@dataclass(frozen=True)
class Exchange:
    """交互轮次的一段：不是存下来的对象，是 exchanges_of 按话轮现算的划分，三条规则按它计数与裁剪。"""

    utterances: tuple

    @property
    def question(self):
        first = self.utterances[0]
        return first if first.kind == KIND_QUESTION else None

    @property
    def answer(self):
        return next((u for u in self.utterances if u.kind == KIND_ANSWER), None)

    @property
    def notices(self) -> int:
        return sum(1 for u in self.utterances if u.kind == KIND_NOTICE)

    def render(self) -> str:
        return "\n".join(u.render() for u in self.utterances)


def _stage_of_note(note) -> str:
    """依据说明形如「阶段名 › 第 n 步 步骤说明」，取分隔符前面的阶段名。"""
    if isinstance(note, str) and STAGE_NAME_SEPARATOR in note:
        return note.split(STAGE_NAME_SEPARATOR, 1)[0]
    return ""


def utterances_of(events) -> list:
    """从事件流里取出全部话轮，按事件序号排：发件箱里的提问与告知、收件箱里的回答各一条。

    回答跟着它回复的那条提问取阶段、工具调用编号与写入槽位；找不到提问的回答也照记，阶段为空。
    """
    proposed = {}
    for event in events:
        if event.name == CALL_PROPOSED:
            proposed[event.call_id] = event.payload
    questions, result = {}, []
    for event in sorted((e for e in events if e.name == MESSAGE_PUT), key=lambda e: e.seq):
        payload = event.payload
        if payload["box"] == OUTBOX and payload["kind"] in ("question", "notice"):
            content = payload["content"]
            if not isinstance(content, dict):
                continue
            call_id = payload["call_id"]
            basis = (proposed.get(call_id) or {}).get("basis") or ("", "", None)
            stage = _stage_of_note(basis[1] if len(basis) > 1 else "")
            if payload["kind"] == "question":
                slot = ((content.get("params") or {}).get("target") or {}).get("slot")
                questions[payload["seq"]] = (stage, call_id, slot)
                result.append(Utterance("系统", content.get("utterance", ""), event.seq, stage, call_id, KIND_QUESTION, slot))
            else:
                result.append(Utterance("系统", content.get("utterance", ""), event.seq, stage, call_id, KIND_NOTICE, None))
        elif payload["box"] == INBOX and payload["kind"] == "answer":
            stage, call_id, slot = questions.get(payload["in_reply_to"], ("", payload.get("call_id"), None))
            result.append(Utterance("使用者", payload["content"] if isinstance(payload["content"], str)
                                    else str(payload["content"]), event.seq, stage, call_id, KIND_ANSWER, slot))
    return result


def exchanges_of(utterances) -> list:
    """交互轮次的划分：从系统的一个提问到下一个提问之前的全部话轮为一段，中间的告知归前一段；
    第一个提问之前的话轮（例如开头就有的告知）自成一段。"""
    groups = []
    for utterance in utterances:
        if utterance.kind == KIND_QUESTION or not groups:
            groups.append([utterance])
        else:
            groups[-1].append(utterance)
    return [Exchange(tuple(group)) for group in groups]


def revision_log(events, draft_slot: str, feedback_slot: str) -> str:
    """修订记录：从数据变更事件推出，每次写「释义草稿」记一行「第 n 稿」，每次写「修改意见」记一行「使用者意见」。

    它是当前数据段的衍生项，不是使用者写的东西，所以在系统提示的「上下文约定」段里专门说明过来源。
    """
    lines, draft_no = [], 0
    for event in events:
        if event.name != DATA_CHANGED:
            continue
        slot, new = event.payload["slot"], event.payload["new"]
        if slot == draft_slot and new is not None:
            draft_no += 1
            lines.append(f"第 {draft_no} 稿：{new}")
        elif slot == feedback_slot and new is not None:
            lines.append(f"使用者意见：{new}")
    return "\n".join(lines)


class ContextPack:
    """一次模型调用能取用的全部上下文。工具按需要取段，取到的段拼成用户内容。"""

    def __init__(self, task_def, data: dict, events, budget_chars: int = DEFAULT_DIALOGUE_BUDGET, step=None):
        self.task_def = task_def
        self.data = dict(data)
        self.events = list(events or [])
        self.budget_chars = budget_chars
        self.utterances = utterances_of(self.events)
        self.step = step  # 当前步：由执行上下文给进来，不在任务数据里（第四步 4.10 节）

    @classmethod
    def build(cls, task_def, data, events, budget_chars: int = DEFAULT_DIALOGUE_BUDGET, step=None) -> "ContextPack":
        return cls(task_def, data, events, budget_chars, step)

    # ── 当前步与阶段 ──

    @property
    def stage_name(self) -> str:
        """当前步所在的阶段名。第五步起当前步是地址栈，阶段在主线那一层；原来的单个地址也认。"""
        main = self.step.get("主线", self.step) if isinstance(self.step, dict) else None
        return main.get("阶段") if isinstance(main, dict) else ""

    def _stage_definition(self) -> dict:
        for stage in self.task_def.DEFINITION.get("阶段列表", []):
            if stage.get("名字") == self.stage_name:
                return stage
        return {}

    # ── 六种段 ──

    def progress(self, note: str = "") -> Segment:
        """任务进度：一句话说清任务进行到哪，末尾接工具给的一句特别说明（没给就写阶段目标成立与否）。

        这句话由任务定义的 step_text 生成，上下文包不自己算第几步、第几次——那要懂阶段与循环段的结构（第四步 4.10 节）。
        """
        text = self.task_def.step_text(self.step)
        if not note:
            goals = self._stage_definition().get("目标", []) or []
            holds = self.task_def.stage_goal_holds(self.stage_name, self.data) if goals else True
            note = "阶段目标已达成。" if holds else "阶段目标未达成。"
        return Segment(SEG_PROGRESS, "当前步", f"{text}；{note}")

    def dialogue(self, scope=SCOPE_STAGE, slots=None) -> Segment:
        """对话历史：按三条确定性规则装入——按范围取、已在数据里的不重复装、预算封顶从最早整轮裁。

        范围按话轮筛，筛剩的话轮再划成交互轮次；后两条规则按交互轮次计数与裁剪。渲染是逐行「系统：」「使用者：」原文。
        """
        scope_text, in_scope = self._scoped_utterances(scope, slots)
        kept, dropped_reason = self._drop_exchanges_already_in_data(exchanges_of(in_scope))
        kept, omitted = self._fit_budget(kept)
        text = "\n".join(exchange.render() for exchange in kept) if kept else NO_DIALOGUE_MARK
        if omitted:
            text = OMITTED_TEMPLATE.format(n=omitted) + "\n" + text
        used = sum(len(exchange.render()) for exchange in kept)
        source = (f"收发件箱 · 范围：{scope_text} · {len(kept)} 轮装入{dropped_reason}"
                  f" · 预算 {self.budget_chars} 字，用 {used} 字")
        return Segment(SEG_DIALOGUE, source, text)

    def _scoped_utterances(self, scope, slots):
        if scope == SCOPE_TASK:
            return "整个任务", list(self.utterances)
        if isinstance(scope, (tuple, list)) and scope and scope[0] == "slots":
            wanted = list(scope[1])
            names = "".join(f"「{name}」" for name in wanted)
            return f"与槽位{names}相关", [u for u in self.utterances if u.slot in wanted]
        if slots:
            names = "".join(f"「{name}」" for name in slots)
            return f"与槽位{names}相关", [u for u in self.utterances if u.slot in slots]
        return "当前阶段", [u for u in self.utterances if u.stage == self.stage_name]

    def _drop_exchanges_already_in_data(self, exchanges):
        """第二条规则：回答原样留在某个槽位里的交互轮次不重复装（它已在当前数据段），最近一段无论如何都装。"""
        kept, dropped = [], 0
        for index, exchange in enumerate(exchanges):
            last = index == len(exchanges) - 1
            answer = exchange.answer
            in_data = answer is not None and answer.slot is not None and self.data.get(answer.slot) == answer.text
            if in_data and not last:
                dropped += 1
                continue
            kept.append((exchange, last, in_data))
        notices = sum(exchange.notices for exchange, _, _ in kept)
        cleared = sum(1 for exchange, last, in_data in kept if not (last and in_data) and exchange.answer is not None)
        unanswered = sum(1 for exchange, _, _ in kept if exchange.question is not None and exchange.answer is None)
        forced = any(last and in_data for _, last, in_data in kept)
        parts = []
        if cleared:
            parts.append(f"回答已被清空的 {cleared} 轮")
        if unanswered:
            parts.append(f"没有回答的提问 {unanswered} 个")
        if notices:
            parts.append(f"告知 {notices} 句")
        if forced:
            parts.append("最近一轮恒装")
        if not kept and dropped:
            note = "（范围内的问答其回答都原样留在槽位里，已在当前数据段，不重复装）"
        elif not kept:
            note = "（范围内没有问答）"
        else:
            note = "（" + " ＋ ".join(parts) + "）"
        return [exchange for exchange, _, _ in kept], note

    def _fit_budget(self, exchanges):
        """第三条规则：超预算从最早的交互轮次整段裁起，裁掉几段由调用方记进段头与调用记录。"""
        kept = list(exchanges)
        omitted = 0
        while kept and sum(len(exchange.render()) for exchange in kept) > self.budget_chars and len(kept) > 1:
            kept.pop(0)
            omitted += 1
        return kept, omitted

    def data_segment(self, slots, revision=None) -> Segment:
        """当前数据：一段，段头「【当前数据 · 槽位】」，段内一行一个槽位「名：值」。

        值原样，空值写「（空）」。slots 每项是槽位名，或（槽位名, 角色标注）——标注跟在名字后面，
        例如「释义草稿（上一稿）：…」。revision 是（草稿槽位, 意见槽位），给了就在段内最后加一项修订记录：
        它是当前数据的衍生项，由上下文包从数据变更事件推出，不是使用者写的；它自己有好几行，后面几行缩进两格，
        免得与槽位那几行混起来（2026-09-17 用户裁定：当前数据合成一段，不再每个槽位各成一段）。
        """
        lines = []
        for item in slots:
            slot, label = item if isinstance(item, tuple) else (item, "")
            value = self.data.get(slot)
            if value is None:
                shown = EMPTY_MARK
            else:  # 字符串原样；列表、字典、推迟标记这类结构化的值写成 JSON，不写 Python 的显示形式
                shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            lines.append(f"{slot}{label}：{shown}")
        if revision is not None:
            log = revision_log(self.events, revision[0], revision[1]) or EMPTY_MARK
            first, *rest = log.split("\n")
            lines.append(f"{REVISION_LABEL}：{first}")
            lines.extend(f"  {line}" for line in rest)
        return Segment(SEG_DATA, "槽位", "\n".join(lines))

    def material(self, slot: str, missing_note: str = "") -> Segment:
        """参考材料：宿主提供的原文。没有就写「（未提供）」，后面可以跟一句工具给的说明。"""
        value = self.data.get(slot)
        text = str(value) if value is not None else (MISSING_MARK if not missing_note else f"（未提供，{missing_note}）")
        return Segment(SEG_MATERIAL, f"槽位「{slot}」", text)

    @staticmethod
    def step(text: str) -> Segment:
        """本步：正文是工具从它的提示词包里选好情形、填好占位符的那句话，这里原样装段，不再自己拼（第五步 4.4A 节）。"""
        return Segment(SEG_STEP, "本次要执行的步骤", text)

    @staticmethod
    def shape(shape: str, text: str, api_note: bool = False) -> Segment:
        """输出形状：文本或 JSON；JSON 同时作为接口参数传给模型服务，来源里写明这一点。"""
        source = shape + (API_NOTE if api_note else "")
        return Segment(SEG_SHAPE, source, text)


# ───────────────────────── 对话理解的候选（第五步 4.3 节）─────────────────────────
# 模型只从代码算好的候选里挑：对话功能从候选功能集挑，写值的槽位与路径从可写路径清单挑。两样都只读槽位表、上一问与当前数据。


def candidate_functions(question) -> list:
    """候选功能集：按上一问的类型查配对表得回应类功能（上一问为空就没有回应类），再加四个主动类，CLARIFY 恒可产出。"""
    from tod_kernel.tools import ACTIVE_FUNCTIONS, CLARIFY, PAIRING

    kind = question.get("类型") if isinstance(question, dict) else None
    return list(PAIRING.get(kind, [])) + list(ACTIVE_FUNCTIONS) + [CLARIFY]


def writable_paths(slots_meta, question, data) -> list:
    """可写路径清单：本任务此刻允许写的位置，每项 {"槽位", "路径"}，按槽位表的顺序。

    文本、数字、布尔、枚举槽位各一行，路径为空；列表槽位不给整表替换一行，给「末尾新增」一行与每个现有项一行，
    有「项」字段的再给每个现有项的每个字段一行与「末尾新增」的每个字段一行；对象槽位按当前值的键各一行，当前值为空就没有行。
    对话理解的三个必备槽位与「使用者可写」为 false 的槽位不进清单；上一问是建议类时清单里一定有「修改意见」
    （2026-09-17 用户裁定：建议类上一问的改动要求写进修改意见）。
    """
    from tod_kernel.tools import APPEND, FEEDBACK_SLOT, QUESTION_SUGGEST, UNDERSTAND_REQUIRED_SLOTS

    rows = []
    data = data or {}
    for slot, meta in slots_meta.items():
        if slot in UNDERSTAND_REQUIRED_SLOTS or meta.get("使用者可写", True) is False:
            continue
        kind = meta.get("类型")
        if kind == "列表":
            fields = list((meta.get("项") or {}).keys())
            items = data.get(slot) if isinstance(data.get(slot), list) else []
            rows.append({"槽位": slot, "路径": [APPEND]})
            for index in range(len(items)):
                rows.append({"槽位": slot, "路径": [index]})
                rows.extend({"槽位": slot, "路径": [index, field]} for field in fields)
            rows.extend({"槽位": slot, "路径": [APPEND, field]} for field in fields)
        elif kind == "对象":
            value = data.get(slot)
            rows.extend({"槽位": slot, "路径": [key]} for key in (value if isinstance(value, dict) else {}))
        else:
            rows.append({"槽位": slot, "路径": []})
    suggest = isinstance(question, dict) and question.get("类型") == QUESTION_SUGGEST
    if suggest and FEEDBACK_SLOT in slots_meta and not any(row["槽位"] == FEEDBACK_SLOT for row in rows):
        rows.append({"槽位": FEEDBACK_SLOT, "路径": []})
    return rows


def understand_step_text(prompt, question, paths, slots_meta, data) -> str:
    """对话理解的本步段：按上一问的类型选情形（四种类型加上一问为空），填上一问的槽位、采纳到、求证的值、选项与可写位置。

    prompt 是对话理解的提示词包；情形的选择与各值的算法在这里，句子全在包里（第五步 4.4A 节）。
    """
    from tod_kernel.tools import KEY_OPTIONS, NO_QUESTION, PAIRING, QUESTION_CHECK, _plain

    asked = question if isinstance(question, dict) else {}
    kind = asked.get("类型")
    situation = kind if kind in PAIRING else NO_QUESTION
    options = asked.get(KEY_OPTIONS) or []
    listed = prompt.fill_piece("选项之间").join(
        prompt.fill_piece("选项", 序号=number, 选项=_plain(option)) for number, option in enumerate(options, start=1))
    return prompt.fill_step(situation,
                            槽位=asked.get("槽位") or EMPTY_MARK,
                            采纳到=asked.get("采纳到") or EMPTY_MARK,
                            值=_plain(options[0]) if kind == QUESTION_CHECK and options else EMPTY_MARK,
                            选项=listed or EMPTY_MARK,
                            可写位置=writable_places_text(prompt, paths, slots_meta, data))


def writable_places_text(prompt, paths, slots_meta, data) -> str:
    """可写位置的白话：只列槽位名，按清单里首次出现的顺序；列表槽位另说明可以新增一项、或改第几项。"""
    names = []
    for row in paths:
        if row["槽位"] not in names:
            names.append(row["槽位"])
    if not names:
        return prompt.fill_piece("没有可写位置")
    parts = []
    for name in names:
        if (slots_meta.get(name) or {}).get("类型") != "列表":
            parts.append(prompt.fill_piece("位置", 槽位=name))
            continue
        count = len(data.get(name)) if isinstance((data or {}).get(name), list) else 0
        if count == 0:
            parts.append(prompt.fill_piece("列表位置·没有项", 槽位=name))
        elif count == 1:
            parts.append(prompt.fill_piece("列表位置·一项", 槽位=name))
        else:
            parts.append(prompt.fill_piece("列表位置·多项", 槽位=name, 项数=count))
    return prompt.fill_piece("位置之间").join(parts)
