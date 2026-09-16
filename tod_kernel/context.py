"""上下文包：把任务数据与事件流投影成模型读得懂的段。

模型每次调用看到的用户内容由六种段组成，段头一律是【段名 · 来源】。哪几段、各段装什么，由工具声明；
怎么从任务数据与事件流算出来，全在本模块，而且只用确定性规则——同一份数据与事件拼出的段逐字相同，
回放时才对得上请求哈希。

模块位置：本模块读事件名与任务定义的投影，所以不放在模型调用件（llm.py）里——那边只认识系统提示、
用户内容、输出形状三样东西与三种模式。事件从哪来也不由本模块决定：宿主在建运行工具表时把一个
「读事件」函数交给工具，工具再传进来（内核不为此改动）。
"""

from __future__ import annotations

from dataclasses import dataclass

from tod_kernel.kernel import ACTION_PROPOSED, DATA_CHANGED, INBOX, MESSAGE_PUT, OUTBOX
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


@dataclass(frozen=True)
class Turn:
    """一问一答：问题原文、回答原文、发生在哪个阶段、回答写进了哪个槽位。"""

    question: str
    answer: str
    stage: str
    slot: str | None
    action_id: int

    def render(self) -> str:
        return f"{SPEAKER_SYSTEM}{self.question}\n{SPEAKER_USER}{self.answer}"


def _stage_of_note(note) -> str:
    """依据说明形如「阶段名 › 第 n 步 步骤说明」，取分隔符前面的阶段名。"""
    if isinstance(note, str) and STAGE_NAME_SEPARATOR in note:
        return note.split(STAGE_NAME_SEPARATOR, 1)[0]
    return ""


def turns_of(events) -> list:
    """从事件流里把一问一答配成轮：发件箱里的问题按到达序号与收件箱里的回答配对。

    没有回答的问题（使用者中途不答了）不成轮，不装进对话历史。
    """
    proposed = {}
    for event in events:
        if event.name == ACTION_PROPOSED:
            proposed[event.action_id] = event.payload
    questions, answers = {}, {}
    for event in events:
        if event.name != MESSAGE_PUT:
            continue
        payload = event.payload
        if payload["box"] == OUTBOX and payload["kind"] == "question":
            questions[payload["seq"]] = (payload["action_id"], payload["content"])
        elif payload["box"] == INBOX and payload["kind"] == "answer":
            answers[payload["in_reply_to"]] = payload["content"]
    turns = []
    for seq in sorted(questions):
        action_id, content = questions[seq]
        if seq not in answers or not isinstance(content, dict):
            continue
        params = content.get("params") or {}
        target = params.get("target") or {}
        basis = (proposed.get(action_id) or {}).get("basis") or ("", "", None)
        turns.append(Turn(question=content.get("utterance", ""), answer=answers[seq],
                          stage=_stage_of_note(basis[1] if len(basis) > 1 else ""),
                          slot=target.get("slot"), action_id=action_id))
    return turns


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
        self.turns = turns_of(self.events)
        self.step = step  # 当前步：由执行上下文给进来，不在任务数据里（第四步 4.10 节）

    @classmethod
    def build(cls, task_def, data, events, budget_chars: int = DEFAULT_DIALOGUE_BUDGET, step=None) -> "ContextPack":
        return cls(task_def, data, events, budget_chars, step)

    # ── 当前步与阶段 ──

    @property
    def stage_name(self) -> str:
        return self.step.get("阶段") if isinstance(self.step, dict) else ""

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
        """对话历史：按三条确定性规则装入——按范围取、已在数据里的不重复装、预算封顶从最早整轮裁。"""
        scope_text, in_scope = self._scoped_turns(scope, slots)
        kept, dropped_reason = self._drop_turns_already_in_data(in_scope)
        kept, omitted = self._fit_budget(kept)
        text = "\n".join(turn.render() for turn in kept) if kept else NO_DIALOGUE_MARK
        if omitted:
            text = OMITTED_TEMPLATE.format(n=omitted) + "\n" + text
        used = sum(len(turn.render()) for turn in kept)
        source = (f"收发件箱 · 范围：{scope_text} · {len(kept)} 轮装入{dropped_reason}"
                  f" · 预算 {self.budget_chars} 字，用 {used} 字")
        return Segment(SEG_DIALOGUE, source, text)

    def _scoped_turns(self, scope, slots):
        if scope == SCOPE_TASK:
            return "整个任务", list(self.turns)
        if isinstance(scope, (tuple, list)) and scope and scope[0] == "slots":
            wanted = list(scope[1])
            names = "".join(f"「{name}」" for name in wanted)
            return f"与槽位{names}相关", [turn for turn in self.turns if turn.slot in wanted]
        if slots:
            names = "".join(f"「{name}」" for name in slots)
            return f"与槽位{names}相关", [turn for turn in self.turns if turn.slot in slots]
        return "当前阶段", [turn for turn in self.turns if turn.stage == self.stage_name]

    def _drop_turns_already_in_data(self, turns):
        """第二条规则：回答原样留在某个槽位里的轮不重复装（它已在当前数据段），最近一轮无论如何都装。"""
        kept, dropped = [], 0
        for index, turn in enumerate(turns):
            last = index == len(turns) - 1
            in_data = turn.slot is not None and self.data.get(turn.slot) == turn.answer
            if in_data and not last:
                dropped += 1
                continue
            kept.append((turn, last, in_data))
        cleared = sum(1 for _, last, in_data in kept if not (last and in_data))
        forced = any(last and in_data for _, last, in_data in kept)
        parts = []
        if cleared:
            parts.append(f"回答已被清空的 {cleared} 轮")
        if forced:
            parts.append("最近一轮恒装")
        if not kept and dropped:
            note = "（范围内的问答其回答都原样留在槽位里，已在当前数据段，不重复装）"
        elif not kept:
            note = "（范围内没有问答）"
        else:
            note = "（" + " ＋ ".join(parts) + "）"
        return [turn for turn, _, _ in kept], note

    def _fit_budget(self, turns):
        """第三条规则：超预算从最早的整轮裁起，裁掉几轮由调用方记进段头与调用记录。"""
        kept = list(turns)
        omitted = 0
        while kept and sum(len(turn.render()) for turn in kept) > self.budget_chars and len(kept) > 1:
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
            lines.append(f"{slot}{label}：{EMPTY_MARK if value is None else value}")
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
    def step(tool_name: str, note: str = "") -> Segment:
        """本步：执行哪个工具，按系统提示工具目录里它的固定指令；note 是这一次的特别说明。"""
        text = f"本步执行工具「{tool_name}」，按系统提示工具目录中它的固定指令"
        text += f"：{note}" if note else "。"
        return Segment(SEG_STEP, "本次要执行的步骤", text)

    @staticmethod
    def shape(shape: str, text: str, api_note: bool = False) -> Segment:
        """输出形状：文本或 JSON；JSON 同时作为接口参数传给模型服务，来源里写明这一点。"""
        source = shape + ("（同时作为接口参数 response_format 传给模型服务）" if api_note else "")
        return Segment(SEG_SHAPE, source, text)
