"""任务定义加载器：把 JSON 任务定义文件读成内核能跑的对象。

一份任务定义文件定义的是一类任务，顶层三个键：名字、槽位（槽位名到元数据：说明、类型、默认值、取值、项、提问）、阶段列表。
一次任务的实例由宿主分配的任务标识加上启动时给的初始输入确定，初始输入经 load 的 initial 参数给出，覆盖槽位默认值。
加载后的对象带内核按属性名访问的 NAME、SLOTS（各槽位默认值）、DEFINITION、is_done、select_call，以及询问工具读的 TEMPLATES
（由槽位与列表项字段上的「提问」生成）。DEFINITION 是加载后的结构化定义（槽位元数据原样，阶段列表原样、步骤带全局编号，
外加告知异常与自主工具的编号表），内核把它放进「任务开始」事件。
文件格式的权威定义是同目录 task_defs/任务定义.schema.json；本模块只用标准库，结构检查是它的子集，另做 schema 管不了的语义检查。
文件格式只有本模块认识。

阶段有两种类型：「固定步骤」按步骤列表执行，步骤可以成组重复；「自主规划」只识别，运行到它时抛未实现错误。
阶段目标、步骤组的「重复直到」用三个谓词（不为空、相等、列表无项为空）写，取「且」；
参数、目标、「最多」里可以用四种引用（槽位、长度、首个为空项、首个为空项字段）取数据。

本模块导入静态工具表（tools.STATIC_TOOLS）：加载时校验工具名与参数名；调用选择时求前置条件。数据文件里只有工具名。
每个步骤必填一句「说明」，由任务作者写这一步做什么；依据说明、告知异常的话与观测台都用它，不用工具标识。

执行语义（调用选择每次迭代做一次，只读数据，不读工具调用记录，不写任何东西）：
- 任务进行到哪叫「当前步」（第四步 4.10 节），是任务对象上的一个字段，不在任务数据里，内核只保管不解读。
  它记的是最近成功完成的那一步的地址，沿定义三层从外到内写：{"阶段": 阶段名, "循环": {"起", "止", "第几次"}, "步骤": 阶段内序号}。
  「循环」只在这一步落在循环段（阶段里连续几步的循环执行）里时才有，「起」「止」是该段在本阶段内的起止序号；
  阶段刚开始一步都没做时只有「阶段」这一层。阶段内序号从 1 起。
- 本模块提供五个接口：INITIAL_STEP（初始当前步）、select_call(data, step)、record_step(step, call)、step_text、step_view。
- 阶段只向前走：当前阶段目标成立就前进到后面第一个目标未达成的阶段的起点，从不自动后退。
- 一趟从当前步的下一步起往后走，跳过引用为 null 或前置条件不成立的步骤。
- 三种异常构造「告知异常」候选：一趟走完阶段目标仍未达成；循环段次数到「最多」仍未满足「重复直到」（含同一次调用选择里
  段内一次零候选）；阶段越过最后一个而任务未完成。使用者选重做时由 record_step 把当前步退回该阶段起点。
- 当前步不合法（阶段不存在、步骤或循环不属于这个阶段）时抛 DefinitionError，由内核报任务定义错误。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tod_kernel.kernel import CallStatus, DefinitionError
from tod_kernel.tools import (EXCEPTION_OPTIONS, EXCEPTION_TOOL, REASK_KEY, REASK_ORIGINAL_KEY, REDO_RESULT, REPLACE_KEY,
                              ROUTES_KEY, STATIC_TOOLS)

# 文件里的键。
KEY_NAME = "名字"
KEY_SLOTS = "槽位"
KEY_STAGES = "阶段列表"
KEY_DELIVERABLES = "交付物"
KEY_DOMAIN_RULES = "领域规矩"  # 可选块：写给模型看的领域写法要求，原样进系统提示
TOP_KEYS = (KEY_NAME, KEY_SLOTS, KEY_STAGES, KEY_DELIVERABLES)
TOP_OPTIONAL_KEYS = (KEY_DOMAIN_RULES,)

# 交付物的键与形态。形态决定来源槽位应当是什么类型：表单是若干标量槽位，表格是带「项」的列表型，文本与文件是文本型。
DELIV_NAME = "名字"
DELIV_NOTE = "说明"
DELIV_SOURCE = "来源"
DELIV_FORM = "形态"
FORM_FORM, FORM_TABLE, FORM_TEXT, FORM_FILE = "表单", "表格", "文本", "文件"
DELIV_FORMS = (FORM_FORM, FORM_TABLE, FORM_TEXT, FORM_FILE)
SCALAR_TYPES = ("文本", "数字", "布尔", "枚举")

# 槽位元数据的键与类型取值。
SLOT_NOTE = "说明"
SLOT_TYPE = "类型"
SLOT_DEFAULT = "默认值"
SLOT_VALUES = "取值"
SLOT_ITEM = "项"
SLOT_QUESTION = "提问"
SLOT_REASK = "再问短句"  # 可选：询问再问同一个问题且念的值没变时说的短句（第五步 4.12 节实施裁定第 11 条）
SLOT_USER_WRITABLE = "使用者可写"  # 可选布尔，默认 true；系统写的槽位（例如释义草稿）标 false，不进对话理解的可写路径清单
SLOT_TYPES = ("文本", "数字", "布尔", "列表", "对象", "枚举")
TYPE_ENUM = "枚举"

STAGE_NAME = "名字"
STAGE_TYPE = "类型"
STAGE_GOAL = "目标"
STAGE_STEPS = "步骤"
STAGE_TOOLSET = "可选工具集"
STAGE_MAX = "最多"  # 自主规划阶段的回合上限
STAGE_NAME_SEPARATOR = " › "  # 依据说明里阶段名与其后内容的分隔；阶段名不得含它
TYPE_FIXED = "固定步骤"
TYPE_PLANNED = "自主规划"

STEP_NOTE = "说明"
STEP_TOOL = "工具"
STEP_PARAMS = "参数"
GROUP_STEPS = "步骤组"  # 多个步骤一起重复；单个步骤要重复直接在步骤上写「重复直到」「最多」
GROUP_UNTIL = "重复直到"
GROUP_MAX = "最多"

PREDICATE = "谓词"
PRED_NOT_NULL = "不为空"
PRED_EQUAL = "相等"
PRED_NO_NULL_ITEM = "列表无项为空"
PRED_KEYS = {
    PRED_NOT_NULL: ("槽位",),
    PRED_EQUAL: ("左", "右"),
    PRED_NO_NULL_ITEM: ("槽位", "字段"),
}

REF_SLOT = "槽位"
REF_LENGTH = "长度"
REF_FIRST_NULL = "首个为空项"
REF_FIRST_NULL_FIELD = "首个为空项字段"
REF_KEYS = (REF_SLOT, REF_LENGTH, REF_FIRST_NULL, REF_FIRST_NULL_FIELD)

# 当前步是地址栈（第五步 4.12 节）：主线是原来的三层地址，插入是插入段的列表（列表末尾是栈顶）。
STACK_MAIN = "主线"
STACK_INSERTS = "插入"
# 插入段的一帧：模式名、做完的步数（0 是一步都没做）、触发时带的输入、上一步的结果、在本帧内再问过几次。
FRAME_PATTERN = "模式"
FRAME_STEP = "步骤"
FRAME_INPUT = "输入"
FRAME_RESULT = "结果"
FRAME_REASKED = "再问"
FRAME_REASK_LIMIT = 2  # 同一帧最多再问 2 次，第 3 次仍含糊就弹帧、不落，主线原地续接（2026-09-18 裁定问题五）
INSERT_KEY = "插入段"  # 依据命中值里的键：这一步属于哪一层插入段
PATTERNS_FILE = Path(__file__).resolve().parent / "task_defs" / "patterns.json"
KEY_PATTERNS = "对话模式"  # DEFINITION 里并进来的对话模式定义
PAT_ROUTES = "路由表"
PAT_MODES = "模式"
PAT_NAME = "名字"
PAT_INPUTS = "输入"
PAT_STEPS = "步骤"
PREF_INPUT = "输入"  # 模式步骤参数里的三种引用
PREF_LAST = "上一步结果"
PREF_SENTENCE = "句式"
PREF_KEYS = (PREF_INPUT, PREF_LAST, PREF_SENTENCE)

# 当前步主线地址的三层。
STEP_STAGE = "阶段"
STEP_LOOP = "循环"
STEP_INDEX = "步骤"
LOOP_FROM = "起"
LOOP_TO = "止"
LOOP_NTH = "第几次"
# 告知异常参数「当前步」的几项：当前步的三层加上这一步的说明与「是段尾」。
AT_STAGE = "阶段"
AT_INDEX = "步骤"
AT_NOTE = "说明"
AT_LOOP = "循环"
AT_LOOP_LAST = "是段尾"

NO_PRECONDITION = "无前置条件"
REF_NULL_NOTE = "参数引用为空"
# 依据命中值里的「选择经过」：前进过的阶段、跳过的阶段与原因、跳过的步骤与原因（供观测台叙事）。告知异常的命中值不加。
TRAIL_KEY = "选择经过"
TRAIL_FORWARD = "前进"
TRAIL_SKIPPED_STAGES = "跳过阶段"
TRAIL_SKIPPED_STEPS = "跳过步骤"
REASON_GOAL_HOLDS = "目标成立"
REASON_UNTIL_HOLDS = "重复直到已成立，越过整段循环"
REASON_LIMIT_NULL = "最多为空，越过整段循环"
# 告知异常依据命中值里的「异常种类」。
EXCEPTION_KIND_KEY = "异常种类"
KIND_PASS_DONE = "一趟走完"
KIND_GROUP_LIMIT = "循环到上限"
KIND_BROKEN = "后续破坏"
INITIAL_INPUT = "初始输入"


class LoadError(Exception):
    """加载错误：任务定义文件或初始输入不合格。发生在启动任务之前，不进事件流。

    path：文件路径；where：出错位置（形如「阶段列表[1]（登记）.步骤[0].参数.index」）；reason：出了什么错。
    """

    def __init__(self, path, where: str, reason: str):
        super().__init__(f"任务定义加载失败：文件 {path}，位置 {where}：{reason}")
        self.path = str(path)
        self.where = where
        self.reason = reason


# ───────────────────────── 加载后的结构 ─────────────────────────


@dataclass(frozen=True)
class Step:
    number: int  # 全局步骤编号，也是依据序号
    note: str  # 任务作者写的一句话说明
    tool: str
    params: Any  # 原文参数，含引用
    pos: int  # 阶段内序号，从 0 起（当前步里的「步骤」是它加一）
    group: Any  # 所在步骤组；不在组里时为 None


@dataclass(frozen=True)
class Group:
    first: int  # 组内第一个步骤的阶段内序号
    last: int  # 组内最后一个步骤的阶段内序号
    until: tuple  # 谓词元组
    max: Any  # 非负整数或引用
    single: bool = False  # 文件里是带重复键的单个步骤（按只有一个成员的组执行，DEFINITION 里写回步骤同形）


@dataclass(frozen=True)
class Stage:
    name: str
    type: str
    goal: tuple
    steps: tuple  # Step 元组，按阶段内序号排列；自主规划阶段为空
    toolset: tuple  # 自主规划阶段才有
    max: Any  # 自主规划阶段的回合上限；固定步骤阶段为 None
    exception_number: int  # 本阶段「告知异常」的依据序号

    def start_step(self) -> dict:
        """本阶段的起点：一步都没做完，所以只有「阶段」这一层。"""
        return {STEP_STAGE: self.name}


class TaskDefinition:
    """加载后的任务定义。内核按属性名访问 NAME、SLOTS、DEFINITION、is_done、select_call；询问工具读 TEMPLATES；
    DELIVERABLES 是交付物列表（名字、说明、来源、形态），给观测台与将来的「告知」工具用。

    依据说明（序号 → 「阶段名 › 第 n 步 步骤说明」等）由加载器从结构生成，存在 _notes 里，不作为属性对外。
    """

    def __init__(self, name, slots, definition, notes, templates, stages, deliverables=(), patterns=None):
        self.NAME = name
        self.SLOTS = slots
        self.DEFINITION = definition
        self.DELIVERABLES = deliverables
        self._notes = notes
        self.TEMPLATES = templates
        self._stages = stages
        self._stage_index = {stage.name: index for index, stage in enumerate(stages)}
        # 依据序号 → 这一步属于哪个阶段、是哪一步；告知异常的依据序号 → 报异常的那个阶段。record_step 靠这两张表认路。
        self._step_of_number = {step.number: (stage, step) for stage in stages for step in stage.steps}
        self._exception_stage = {stage.exception_number: stage for stage in stages}
        # 对话模式：模式名 → 模式；依据序号 → （模式, 模式内序号从 0 起）；功能 → 模式名。没用对话理解的任务三张表都空。
        patterns = patterns or {"modes": {}, "numbers": {}, "routes": {}}
        self._modes = patterns["modes"]
        self._mode_step_of_number = patterns["numbers"]
        self._route_of = patterns["routes"]
        self.INITIAL_STEP = {STACK_MAIN: stages[0].start_step() if stages else {}, STACK_INSERTS: []}

    # ── 内核调用的两个函数 ──

    def is_done(self, data) -> bool:
        """任务完成：每个阶段的目标都成立。只看槽位数据。"""
        return all(_holds_all(stage.goal, data) for stage in self._stages)

    def stage_goal_holds(self, stage_name, data) -> bool:
        """某个阶段的目标此刻成立没有。上下文包写「任务进度」段时用它；没有这个阶段名时当作成立。"""
        index = self._stage_index.get(stage_name)
        return True if index is None else _holds_all(self._stages[index].goal, data)

    def select_call(self, data, step):
        """调用选择：返回（工具名, 参数, 依据）——普通一步或告知异常；全部阶段目标都已成立时返回 None。

        只读，不写任何东西。step 是当前步（地址栈）；它不合法时抛 DefinitionError，由内核报任务定义错误。
        插入段不为空时先从栈顶那一帧往下找能做的步骤；各帧都做完了，才回到主线原地址往下走（第五步 4.12 节）。
        """
        step, frames = self._split(step)
        for layer in range(len(frames) - 1, -1, -1):
            candidate = self._frame_candidate(frames[layer], layer, data, step)
            if candidate is not None:
                return candidate
        stage, position = self._locate(step)
        here = step
        index = self._stage_index[stage.name]
        record = _PassRecord()
        if _holds_all(stage.goal, data):
            # 阶段只向前走：跳过目标已成立的阶段，停在后面第一个目标未达成的阶段。
            ahead = next((i for i in range(index + 1, len(self._stages)) if not _holds_all(self._stages[i].goal, data)), None)
            if ahead is None:
                # 越过最后一个阶段而任务未完成：前面某个已过阶段的目标被破坏，报第一个目标未达成的阶段。
                broken = next((s for s in self._stages if not _holds_all(s.goal, data)), None)
                return None if broken is None else self._exception(broken, data, here, KIND_BROKEN)
            record.trail[TRAIL_FORWARD].append(stage.name)
            record.trail[TRAIL_SKIPPED_STAGES].extend([s.name, REASON_GOAL_HOLDS] for s in self._stages[index + 1:ahead])
            stage = self._stages[ahead]
            here = stage.start_step()
            position = (("enter", 0), 0)
        if stage.type == TYPE_PLANNED:
            raise NotImplementedError(f"「自主规划」阶段本步不运行：{stage.name}")
        return self._walk(stage, data, here, position, record)

    # ── 当前步的四个接口（另一个是 INITIAL_STEP） ──

    def record_step(self, step, call):
        """记录本步：工具调用到终态后，把这个工具调用做的那一步记进当前步并返回新值。纯函数，不读任务数据。

        三条规则：工具调用没成功不动；告知异常且返回值是「重做」则回到该阶段起点；
        其余写该步的阶段与阶段内序号，落在循环段里时带上这段循环的起止与第几次。
        """
        if getattr(call, "status", None) is not CallStatus.SUCCEEDED:
            return step
        main, frames = self._split(step)
        number = call.basis[0] if isinstance(call.basis, tuple) and call.basis else None
        reported = self._exception_stage.get(number)
        if reported is not None:
            if call.result == REDO_RESULT:
                return {STACK_MAIN: reported.start_step(), STACK_INSERTS: []}
            return {STACK_MAIN: main, STACK_INSERTS: frames}
        if number in self._mode_step_of_number:
            return {STACK_MAIN: main, STACK_INSERTS: self._record_frame_step(frames, call, number)}
        new_main = self._record_main(main, call, number)
        return {STACK_MAIN: new_main, STACK_INSERTS: self._push_routes(call)}

    def _record_main(self, step, call, number):
        """主线一步做完：写该步的阶段与阶段内序号，落在循环段里时带上这段循环的起止与第几次。"""
        located = self._step_of_number.get(number)
        if located is None:
            raise DefinitionError(f"工具调用的依据序号不是任何一步，记不了当前步：{number!r}")
        stage, done = located
        new = {STEP_STAGE: stage.name}
        if done.group is not None:
            new[STEP_LOOP] = {LOOP_FROM: done.group.first + 1, LOOP_TO: done.group.last + 1,
                              LOOP_NTH: _nth_time(step, stage, done)}
        new[STEP_INDEX] = done.pos + 1
        return new

    def step_text(self, step) -> str:
        """一句人话，给界面与提示词。读不懂的当前步照实说，不抛错——它只负责显示。

        不在插入段里时与原来的写法逐字相同；在插入段里时后面接一句插入段做到哪。
        """
        text = self._main_text(step.get(STACK_MAIN) if isinstance(step, dict) and STACK_MAIN in step else step)
        frames = step.get(STACK_INSERTS) if isinstance(step, dict) and STACK_MAIN in step else None
        if isinstance(frames, list) and frames:
            top = frames[-1]
            mode = self._modes.get(top.get(FRAME_PATTERN)) if isinstance(top, dict) else None
            done = top.get(FRAME_STEP) if isinstance(top, dict) else None
            if mode is None or not isinstance(done, int):
                text += f"；插入段读不出来（{top!r}）"
            elif done == 0:
                text += f"；插入段『{mode.name}』还没有做完任何一步"
            else:
                text += f"；插入段『{mode.name}』做完了第 {done} 步『{mode.steps[done - 1].note}』"
            if len(frames) > 1:
                text += f"，后面还排着 {len(frames) - 1} 段"
        return text

    def _main_text(self, step) -> str:
        view = self._main_view(step)
        if view is None:
            return f"当前步：读不出来（{step!r}）"
        parts = [f"当前步：『{view[AT_STAGE]}』阶段"]
        loop = view.get(AT_LOOP)
        if loop is not None:
            limit = loop.get("最多")
            # 上限写死成数字就直接写数字，写成引用（例如文件总表的长度）就照引用的意思写。
            limit_text = "" if limit is None else f"（最多 {operand_text(limit) if isinstance(limit, dict) else limit} 次）"
            span = (f"第 {loop[LOOP_FROM]} 步" if loop[LOOP_FROM] == loop[LOOP_TO]
                    else f"第 {loop[LOOP_FROM]} 到第 {loop[LOOP_TO]} 步")
            parts.append(f"{span}循环的第 {loop[LOOP_NTH]} 次{limit_text}")
        if view.get(AT_INDEX) is None:
            parts.append("还没有做完任何一步")
        else:
            parts.append(f"做完了第 {view[AT_INDEX]} 步『{view[AT_NOTE]}』")
        return "，".join(parts)

    def step_view(self, step) -> dict | None:
        """结构化投影（阶段名、阶段内序号、步骤说明、循环起止与第几次与上限、是不是段尾），给观测台画图，页面不自己算。

        主线那几项照旧放在顶层；另加「插入」：插入段列表（栈底在前），每项是模式名、做完的步数、共几步、做完那一步的说明、输入。
        读不出来时返回 None（旧运行文件或宿主给了别的东西时，显示那一侧自己兜底）。
        """
        main = step.get(STACK_MAIN) if isinstance(step, dict) and STACK_MAIN in step else step
        view = self._main_view(main)
        if view is None:
            return None
        frames = step.get(STACK_INSERTS) if isinstance(step, dict) and STACK_MAIN in step else []
        inserts = []
        for frame in frames if isinstance(frames, list) else []:
            mode = self._modes.get(frame.get(FRAME_PATTERN)) if isinstance(frame, dict) else None
            done = frame.get(FRAME_STEP) if isinstance(frame, dict) else None
            if mode is None or not isinstance(done, int):
                continue
            inserts.append({FRAME_PATTERN: mode.name, FRAME_STEP: done, "共": len(mode.steps),
                            AT_NOTE: mode.steps[done - 1].note if done else None,
                            FRAME_INPUT: copy.deepcopy(frame.get(FRAME_INPUT))})
        return {**view, STACK_INSERTS: inserts}

    def _main_view(self, step) -> dict | None:
        if not isinstance(step, dict):
            return None
        index = self._stage_index.get(step.get(STEP_STAGE))
        if index is None:
            return None
        stage = self._stages[index]
        number = step.get(STEP_INDEX)
        if not (isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= len(stage.steps)):
            return {AT_STAGE: stage.name, AT_INDEX: None, AT_NOTE: None, AT_LOOP: None, AT_LOOP_LAST: None}
        done = stage.steps[number - 1]
        loop = None
        if done.group is not None:
            raw = step.get(STEP_LOOP) if isinstance(step.get(STEP_LOOP), dict) else {}
            loop = {LOOP_FROM: done.group.first + 1, LOOP_TO: done.group.last + 1,
                    LOOP_NTH: raw.get(LOOP_NTH), "最多": done.group.max}
        return {AT_STAGE: stage.name, AT_INDEX: number, AT_NOTE: done.note, AT_LOOP: loop,
                AT_LOOP_LAST: None if done.group is None else done.group.last == done.pos}

    # ── 插入段（第五步 4.12 节）──

    def _split(self, step):
        """把当前步拆成（主线地址, 插入段列表的副本）。原来的单个地址也认，按插入为空处理；插入段不合法抛 DefinitionError。"""
        if not (isinstance(step, dict) and STACK_MAIN in step):
            return step, []
        frames = step.get(STACK_INSERTS, [])
        if not isinstance(frames, list):
            raise DefinitionError(f"当前步的「{STACK_INSERTS}」不是列表：{frames!r}")
        for frame in frames:
            mode = self._modes.get(frame.get(FRAME_PATTERN)) if isinstance(frame, dict) else None
            if mode is None:
                raise DefinitionError(f"插入段的模式不存在：{frame!r}")
            done = frame.get(FRAME_STEP)
            if not (isinstance(done, int) and not isinstance(done, bool) and 0 <= done <= len(mode.steps)):
                raise DefinitionError(f"插入段『{mode.name}』做完的步数 {done!r} 不在 0 到 {len(mode.steps)} 之内")
        return step[STACK_MAIN], copy.deepcopy(frames)

    def _frame_candidate(self, frame, layer, data, main):
        """从一帧做完的那一步往后找第一个前置条件成立的步骤，找到返回（工具名, 参数, 依据），这一帧做完了返回 None。

        依据说明带主线所在的阶段名，对话历史按阶段取问答时插入段里的问答仍算在这个阶段里。
        """
        mode = self._modes[frame[FRAME_PATTERN]]
        stage = main.get(STEP_STAGE, "") if isinstance(main, dict) else ""
        for pos in range(frame[FRAME_STEP], len(mode.steps)):
            step = mode.steps[pos]
            params = _evaluate_pattern_tree(step.params, frame)
            ok, note, hit = _precondition(step.tool, data, params)
            if not ok:
                continue
            hit = {**(hit if isinstance(hit, dict) else {} if hit is None else {"命中值": hit}),
                   INSERT_KEY: {"层": layer, FRAME_PATTERN: mode.name, FRAME_STEP: pos + 1, "共": len(mode.steps)}}
            text = f"{stage}{STAGE_NAME_SEPARATOR}插入段 · {mode.name} 第 {pos + 1} 步 {step.note}"
            return (step.tool, params, (step.number, text, hit))
        return None

    def _record_frame_step(self, frames, call, number):
        """插入段里一步做完：更新那一帧做完的步数与结果；本帧再问换成澄清帧（到上限就弹帧），帧替换换成新模式，做完弹帧。"""
        mode, pos = self._mode_step_of_number[number]
        hit = call.basis[2] if len(call.basis) > 2 and isinstance(call.basis[2], dict) else {}
        layer = (hit.get(INSERT_KEY) or {}).get("层", len(frames) - 1)
        if not (isinstance(layer, int) and 0 <= layer < len(frames)):
            raise DefinitionError(f"插入段里的工具调用找不到它所在的那一帧：层 {layer!r}")
        frames = frames[:layer + 1]  # 上面几帧在选中这一步时已经做完了
        frame = frames[layer]
        frame[FRAME_STEP] = pos + 1
        result = call.result
        if isinstance(result, dict) and result.get(REASK_KEY):
            reasked = frame.get(FRAME_REASKED, 0)
            if reasked >= FRAME_REASK_LIMIT:
                return frames[:layer]  # 再问到上限仍含糊：弹帧、不落，主线原地续接
            frames[layer] = {**self._new_frame(CLARIFY_FUNCTION, {"原问": result.get(REASK_ORIGINAL_KEY)}),
                             FRAME_REASKED: reasked + 1}
            return frames
        if isinstance(result, dict) and isinstance(result.get(REPLACE_KEY), dict):
            replace = result[REPLACE_KEY]
            frames[layer] = self._new_frame(replace.get("功能"), replace.get("输入"))
            if frame.get(FRAME_REASKED):
                frames[layer][FRAME_REASKED] = frame[FRAME_REASKED]
            return frames
        if isinstance(result, dict) and "输出" in result:
            frame[FRAME_RESULT] = copy.deepcopy(result["输出"])
        elif result is not None and not isinstance(result, dict):
            frame[FRAME_RESULT] = copy.deepcopy(result)
        if frame[FRAME_STEP] >= len(mode.steps):
            return frames[:layer]
        return frames

    def _push_routes(self, call):
        """主线一步做完后，按它返回值里待路由的行为压插入段：话里第一项最先做，所以倒着压，第一项在栈顶。"""
        result = call.result
        routes = result.get(ROUTES_KEY) if isinstance(result, dict) else None
        if not routes:
            return []
        return [self._new_frame(route.get("功能"), route.get("输入")) for route in reversed(routes)]

    def _new_frame(self, function, inputs):
        name = self._route_of.get(function)
        if name is None:
            raise DefinitionError(f"路由表里没有功能「{function}」的去向")
        return {FRAME_PATTERN: name, FRAME_STEP: 0, FRAME_INPUT: copy.deepcopy(inputs)}

    def _locate(self, step):
        """当前步 → （阶段, 一趟的起点状态）。不合法就抛 DefinitionError。"""
        if not isinstance(step, dict):
            raise DefinitionError(f"当前步不是一个字典：{step!r}")
        name = step.get(STEP_STAGE)
        index = self._stage_index.get(name)
        if index is None:
            raise DefinitionError(f"当前步所指的阶段不存在：{name!r}")
        stage = self._stages[index]
        return stage, _position_of(stage, step)

    # ── 一趟 ──

    def _walk(self, stage, data, here, position, record):
        """从当前步的下一步起往后走：驱动「进入」「段内」「一次走完」三状态机，直到得出结局。

        here 是这一趟起点的当前步（前进过阶段时是新阶段的起点），只在构造告知异常时用来报位置。
        position 是由 here 推出的起点状态。结局是候选、告知异常候选或 None（任务定义缺口）。
        record 记本次调用选择的经过（段首走起过的循环段、选择经过），候选的依据命中值带上选择经过。
        """
        (kind, pos), rounds = position
        handlers = {"enter": self._enter, "in": self._in_group, "round_end": self._round_end}
        while True:
            outcome = handlers[kind](stage, data, here, pos, rounds, record)
            if isinstance(outcome, _Outcome):
                return outcome.value
            (kind, pos), rounds = outcome

    def _enter(self, stage, data, here, pos, rounds, record):
        """「进入」：pos 是阶段内序号（从 0 起，内部用）。走过末尾是一趟走完；普通步骤试一次；循环段先查「最多」与「重复直到」再决定进不进。"""
        steps = stage.steps
        if pos >= len(steps):
            return _Outcome(self._exception(stage, data, here, KIND_PASS_DONE))  # 一趟走完，目标仍未达成
        step = steps[pos]
        if step.group is None:
            candidate = self._try_step(stage, step, data, 0, record)
            return _Outcome(candidate) if candidate is not None else (("enter", pos + 1), rounds)
        group = step.group
        limit = _evaluate(group.max, data)
        if not _valid_limit(limit):
            return _Outcome(None)  # 「最多」求出的既不是 null 也不是非负整数：任务定义缺口，调用选择返回空，由内核报任务定义错误
        if limit is None or _holds_all(group.until, data):
            # 「最多」为 null 跳过整段循环；「重复直到」已成立，一次不走越过整段
            reason = REASON_LIMIT_NULL if limit is None else REASON_UNTIL_HOLDS
            record.trail[TRAIL_SKIPPED_STEPS].extend([s.number, reason] for s in steps[group.first:group.last + 1])
            return ("enter", group.last + 1), rounds
        if limit == 0:
            return _Outcome(self._exception(stage, data, here, KIND_GROUP_LIMIT))  # 「最多」为 0 而「重复直到」不成立：一次都不许跑，按异常处理
        return ("in", group.first), 0

    def _in_group(self, stage, data, here, pos, rounds, record):
        """「段内」：试循环段里的一步。试中就是候选；段尾试不中就结束这一次（次数加一），否则往段内下一步。"""
        step = stage.steps[pos]
        group = step.group
        if pos == group.first:
            record.walked_from_first.add(group.first)
        is_last = pos == group.last
        candidate = self._try_step(stage, step, data, rounds + 1 if is_last else rounds, record)
        if candidate is not None:
            return _Outcome(candidate)
        return (("round_end", pos), rounds + 1) if is_last else (("in", pos + 1), rounds)

    def _round_end(self, stage, data, here, pos, rounds, record):
        """「一次走完」：pos 是段尾，rounds 已含这一次。先看「重复直到」，再看「最多」与次数，最后看本次是否段内一次零候选。"""
        group = stage.steps[pos].group
        if _holds_all(group.until, data):
            return ("enter", group.last + 1), 0
        limit = _evaluate(group.max, data)
        if not _valid_limit(limit):
            return _Outcome(None)  # 同「进入」：任务定义缺口
        if limit is None:
            return ("enter", group.last + 1), 0
        if rounds >= limit:
            return _Outcome(self._exception(stage, data, here, KIND_GROUP_LIMIT))  # 次数到「最多」仍未满足停止条件
        if group.first in record.walked_from_first:
            return _Outcome(self._exception(stage, data, here, KIND_GROUP_LIMIT))  # 本次调用选择里段内一次零候选，不回段首
        return ("in", group.first), rounds

    def _try_step(self, stage, step, data, rounds_after, record):
        """求一个步骤：引用为 null 或前置条件不成立返回 None 并记进选择经过；成立返回（工具名, 参数, 依据）。

        依据的命中值是前置条件返回的命中值字典，外加「选择经过」键。当前步不在这里写，由 record_step 在工具调用成功后记。
        rounds_after 现在只给状态机用（段尾试中时这一次就算走完），不再进候选。
        """
        params = _evaluate_params(step.params, data)
        if params is None:
            record.trail[TRAIL_SKIPPED_STEPS].append([step.number, REF_NULL_NOTE])
            return None
        ok, note, hit = _precondition(step.tool, data, params)
        if not ok:
            record.trail[TRAIL_SKIPPED_STEPS].append([step.number, f"前置条件不成立：{note}"])
            return None
        hit = {**(hit if isinstance(hit, dict) else {} if hit is None else {"命中值": hit}), TRAIL_KEY: copy.deepcopy(record.trail)}
        return (step.tool, params, (step.number, self._notes[step.number], hit))

    def _exception(self, stage, data, here, kind):
        """构造「告知异常」候选：五个参数，依据的命中值是这份异常报告外加「异常种类」键。

        kind 是三种异常之一：一趟走完目标仍未达成；循环段到上限（含段内一次零候选）；已过阶段的目标被后续阶段破坏。
        异常种类只进依据的命中值，不进告知异常工具的参数。
        报的位置是这一趟起点的当前步 here；刚换阶段时它是新阶段的起点，与存着的当前步不是同一个值，这笔账仍记在第四步 4.10 节。
        """
        unmet = []
        for predicate in stage.goal:
            if not _holds(predicate, data):
                unmet.append({"谓词": copy.deepcopy(predicate), "当前值": _current_value(predicate, data),
                              "文字": _goal_text(predicate, data)})
        status = []
        for step in stage.steps:
            params = _evaluate_params(step.params, data)
            if params is None:
                status.append({"步骤": step.number, "工具": step.tool, "成立": False, "说明": REF_NULL_NOTE, "命中值": None})
                continue
            ok, note, hit = _precondition(step.tool, data, params)
            status.append({"步骤": step.number, "工具": step.tool, "成立": bool(ok), "说明": note, "命中值": hit})
        params = {
            "阶段": stage.name,
            "未达成目标": unmet,
            "步骤现况": status,
            "当前步": self._position(here),
            "可选措施": list(EXCEPTION_OPTIONS),
        }
        number = stage.exception_number
        return (EXCEPTION_TOOL, params, (number, self._notes[number], {**copy.deepcopy(params), EXCEPTION_KIND_KEY: kind}))


    def _position(self, here):
        """告知异常参数「当前步」：阶段、阶段内序号、这一步的说明、循环（起、止、第几次）、是不是段尾。

        阶段起点时序号与说明都是 null；「是段尾」在这一步不在循环段里或还没做完任何一步时是 null。
        """
        view = self._main_view(here)
        if view is None:
            return {AT_STAGE: here.get(STEP_STAGE) if isinstance(here, dict) else None,
                    AT_INDEX: None, AT_NOTE: None, AT_LOOP: None, AT_LOOP_LAST: None}
        loop = view[AT_LOOP]
        return {AT_STAGE: view[AT_STAGE], AT_INDEX: view[AT_INDEX], AT_NOTE: view[AT_NOTE],
                AT_LOOP: None if loop is None else {LOOP_FROM: loop[LOOP_FROM], LOOP_TO: loop[LOOP_TO], LOOP_NTH: loop[LOOP_NTH]},
                AT_LOOP_LAST: view[AT_LOOP_LAST]}


class _PassRecord:
    """一次调用选择的经过：组首走起过的组（以组首序号记，判「组内一轮零候选」用），以及写进依据命中值的选择经过。"""

    __slots__ = ("walked_from_first", "trail")

    def __init__(self):
        self.walked_from_first = set()
        self.trail = {TRAIL_FORWARD: [], TRAIL_SKIPPED_STAGES: [], TRAIL_SKIPPED_STEPS: []}


class _Outcome:
    """一趟的结局：候选、告知异常候选或 None。用它与状态机的「下一状态」区分开。"""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


def _position_of(stage, step):
    """由当前步推定一趟的起点状态，返回（（状态, 阶段内序号）, 已走完的次数）。状态机内部用阶段内序号（从 0 起）定位步骤。

    - 没有「步骤」这一层：阶段起点 →「进入」，从第一步起，进循环段前先查「重复直到」；
    - 这一步是某个循环段的段尾：刚走完一次，这一次已计入 →「一次走完」，先判「重复直到」再决定回段首还是越过；
    - 这一步在循环段里但不是段尾 →「段内」，从下一步起；
    - 这一步不在循环段里 →「进入」，从它的下一步起。
    「第几次」记的是含这一次在内的次数，所以落在段尾时已走完的次数就是它，落在段中时是它减一。
    结构不合法（有循环没步骤、序号越界、循环段对不上、第几次不是正整数）一律抛 DefinitionError。
    """
    steps = stage.steps
    if STEP_INDEX not in step:
        if step.get(STEP_LOOP) is not None:
            raise DefinitionError(f"当前步有「{STEP_LOOP}」却没有「{STEP_INDEX}」：{step!r}")
        return ("enter", 0), 0
    number = step[STEP_INDEX]
    if not (isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= len(steps)):
        raise DefinitionError(f"当前步的步骤 {number!r} 不在『{stage.name}』阶段的 1 到 {len(steps)} 之内")
    done = steps[number - 1]
    loop = step.get(STEP_LOOP)
    if done.group is None:
        if loop is not None:
            raise DefinitionError(f"『{stage.name}』阶段第 {number} 步不在循环段里，当前步却写了「{STEP_LOOP}」")
        return ("enter", done.pos + 1), 0
    if not isinstance(loop, dict):
        raise DefinitionError(f"『{stage.name}』阶段第 {number} 步在循环段里，当前步却没有「{STEP_LOOP}」这一层")
    want = (done.group.first + 1, done.group.last + 1)
    if (loop.get(LOOP_FROM), loop.get(LOOP_TO)) != want:
        raise DefinitionError(f"当前步的循环段 {loop.get(LOOP_FROM)}–{loop.get(LOOP_TO)} 与"
                              f"『{stage.name}』阶段第 {number} 步所在的循环段 {want[0]}–{want[1]} 对不上")
    nth = loop.get(LOOP_NTH)
    if not (isinstance(nth, int) and not isinstance(nth, bool) and nth >= 1):
        raise DefinitionError(f"当前步的「{LOOP_NTH}」不是正整数：{nth!r}")
    if done.group.last == done.pos:
        return ("round_end", done.pos), nth
    return ("in", done.pos + 1), nth - 1


def _nth_time(step, stage, done):
    """这一步是这段循环的第几次。

    第四步 4.10 节写的判据是「上一步是该段最后一步、这次又回到第一步则加 1」；这里放宽成
    「同一阶段、同一循环段里，这一步的阶段内序号不比上一步大就加 1，否则沿用」（2026-09-16 主会话裁定）：
    循环段的最后几步被前置条件跳过时，上一步停在段中，按字面判据就永远不加一了。
    进入这段循环之前（阶段起点、循环段外、换了阶段或换了循环段）一律从 1 起。
    """
    if not isinstance(step, dict) or step.get(STEP_STAGE) != stage.name:
        return 1
    loop = step.get(STEP_LOOP)
    if not isinstance(loop, dict) or (loop.get(LOOP_FROM), loop.get(LOOP_TO)) != (done.group.first + 1, done.group.last + 1):
        return 1
    nth, before = loop.get(LOOP_NTH), step.get(STEP_INDEX)
    if not (isinstance(nth, int) and not isinstance(nth, bool) and nth >= 1):
        return 1
    if not (isinstance(before, int) and not isinstance(before, bool)):
        return 1
    return nth + 1 if done.pos + 1 <= before else nth


CLARIFY_FUNCTION = "CLARIFY"


def _evaluate_pattern_tree(value, frame):
    """模式步骤的参数逐层求值：三种引用取插入段那一帧的东西，其余原样深拷贝。引用求出 null 不跳过步骤，值就是 null。"""
    if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in PREF_KEYS:
        (key, arg), = value.items()
        inputs = frame.get(FRAME_INPUT) if isinstance(frame.get(FRAME_INPUT), dict) else {}
        if key == PREF_INPUT:
            node = inputs
            for part in str(arg).split("."):
                node = node.get(part) if isinstance(node, dict) else None
            return copy.deepcopy(node)
        if key == PREF_LAST:
            return copy.deepcopy(frame.get(FRAME_RESULT))
        shown = {name: (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))
                 for name, item in inputs.items()}
        return str(arg).format(**shown)
    if isinstance(value, dict):
        return {key: _evaluate_pattern_tree(child, frame) for key, child in value.items()}
    if isinstance(value, list):
        return [_evaluate_pattern_tree(child, frame) for child in value]
    return copy.deepcopy(value)


def _precondition(tool_name, data, params):
    spec = STATIC_TOOLS[tool_name]
    if spec.preconditions is None:
        return True, NO_PRECONDITION, None
    return spec.preconditions(data, params)


# ───────────────────────── 谓词与引用 ─────────────────────────


def _is_reference(value) -> bool:
    return isinstance(value, dict) and any(key in value for key in REF_KEYS)


def _first_null_index(data, slot, field_name):
    items = data.get(slot)
    if not isinstance(items, list):
        return None
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get(field_name) is None:
            return index
    return None


def _valid_limit(limit) -> bool:
    """「最多」求出的值可用：null（跳过整个组）或非负整数。其余（例如引用指向一个列表）是任务定义缺口。"""
    return limit is None or (isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0)


def _evaluate(value, data):
    """求一个引用或字面量的值。引用求出 null 时返回 None。

    「槽位」引用返回的是任务数据里的活对象本身，不是拷贝：调用者不得改动它，要把它带出去（放进参数、报告、事件）必须先深拷贝。
    """
    if not _is_reference(value):
        return value
    (key, arg), = value.items()
    if key == REF_SLOT:
        return data.get(arg)
    if key == REF_LENGTH:
        target = data.get(arg)
        return len(target) if isinstance(target, (list, dict, str)) else None
    if key == REF_FIRST_NULL:
        return _first_null_index(data, arg[0], arg[1])
    index = _first_null_index(data, arg[0], arg[1])
    if index is None:
        return None
    item = data.get(arg[0])[index]
    return item.get(arg[2]) if isinstance(item, dict) else None


_NULL = object()


def _evaluate_params(params, data):
    """参数逐层递归求值；任一引用求出 null 返回 None（步骤跳过）。求出的实际值放进新构造的对象。"""
    result = _evaluate_tree(params, data)
    return None if result is _NULL else result


def _evaluate_tree(value, data):
    if _is_reference(value):
        evaluated = _evaluate(value, data)
        return _NULL if evaluated is None else copy.deepcopy(evaluated)
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            evaluated = _evaluate_tree(child, data)
            if evaluated is _NULL:
                return _NULL
            out[key] = evaluated
        return out
    if isinstance(value, list):
        out = []
        for child in value:
            evaluated = _evaluate_tree(child, data)
            if evaluated is _NULL:
                return _NULL
            out.append(evaluated)
        return out
    return copy.deepcopy(value)


def _holds(predicate, data) -> bool:
    kind = predicate[PREDICATE]
    if kind == PRED_NOT_NULL:
        return data.get(predicate["槽位"]) is not None
    if kind == PRED_EQUAL:
        left, right = _evaluate(predicate["左"], data), _evaluate(predicate["右"], data)
        return left is not None and right is not None and left == right
    items = data.get(predicate["槽位"])
    if not isinstance(items, list):
        return False
    return all(isinstance(item, dict) and item.get(predicate["字段"]) is not None for item in items)


def _holds_all(predicates, data) -> bool:
    return all(_holds(predicate, data) for predicate in predicates)


def _null_item_indexes(data, slot, field_name):
    """列表里该字段为 null 的项的下标列表；槽位不是列表时为 None。"""
    items = data.get(slot)
    if not isinstance(items, list):
        return None
    return [index for index, item in enumerate(items) if not isinstance(item, dict) or item.get(field_name) is None]


def _current_value(predicate, data):
    """未达成目标的「当前值」：「相等」是 {"左", "右"} 两边的值，「不为空」是该槽位的值，
    「列表无项为空」是为空项的下标列表（槽位不是列表时为 None）。"""
    kind = predicate[PREDICATE]
    if kind == PRED_EQUAL:
        return {"左": copy.deepcopy(_evaluate(predicate["左"], data)), "右": copy.deepcopy(_evaluate(predicate["右"], data))}
    if kind == PRED_NO_NULL_ITEM:
        return _null_item_indexes(data, predicate["槽位"], predicate["字段"])
    return copy.deepcopy(data.get(predicate["槽位"]))


def _show(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _operand_text(value) -> str:
    if not _is_reference(value):
        return _show(value)
    (key, arg), = value.items()
    if key == REF_SLOT:
        return arg
    if key == REF_LENGTH:
        return f"{arg} 的长度"
    if key == REF_FIRST_NULL:
        return f"{arg[0]} 里首个 {arg[1]} 为空的项的下标"
    return f"{arg[0]} 里首个 {arg[1]} 为空的项的 {arg[2]}"


def _goal_text(predicate, data) -> str:
    """未达成目标的「文字」，例如「登记进度 等于 99，当前值 3」。

    「相等」只写引用一侧的当前值；两侧都是引用或都是字面量时写「当前值 左值 与 右值」。
    """
    kind = predicate[PREDICATE]
    if kind == PRED_NOT_NULL:
        return f"{predicate['槽位']} 不为空，当前值 {_show(data.get(predicate['槽位']))}"
    if kind == PRED_NO_NULL_ITEM:
        indexes = _null_item_indexes(data, predicate["槽位"], predicate["字段"])
        return f"{predicate['槽位']} 里没有 {predicate['字段']} 为空的项，当前为空项的下标 {_show(indexes)}"
    left, right = predicate["左"], predicate["右"]
    head = f"{_operand_text(left)} 等于 {_operand_text(right)}"
    left_value, right_value = _show(_evaluate(left, data)), _show(_evaluate(right, data))
    if _is_reference(left) and not _is_reference(right):
        return f"{head}，当前值 {left_value}"
    if _is_reference(right) and not _is_reference(left):
        return f"{head}，当前值 {right_value}"
    return f"{head}，当前值 {left_value} 与 {right_value}"


# ───────────────────────── 加载与校验 ─────────────────────────


class _Checker:
    """校验时持有文件路径与槽位表，出错即抛带位置的加载错误。"""

    def __init__(self, path, slots=None):
        self.path = path
        self.slots = slots or {}

    def fail(self, where, reason):
        raise LoadError(self.path, where, reason)

    def dict_with(self, value, where, required, optional=()):
        if not isinstance(value, dict):
            self.fail(where, f"应当是对象，实际是 {type(value).__name__}")
        for key in required:
            if key not in value:
                self.fail(where, f"缺少键「{key}」")
        unknown = [key for key in value if key not in required and key not in optional]
        if unknown:
            self.fail(where, f"不认识的键：{'、'.join(unknown)}")

    def slot(self, name, where):
        if not isinstance(name, str):
            self.fail(where, f"槽位名应当是字符串，实际是 {name!r}")
        if name not in self.slots:
            self.fail(where, f"引用了不存在的槽位「{name}」")

    def field_name(self, name, where):
        if not isinstance(name, str) or not name:
            self.fail(where, f"字段名应当是非空字符串，实际是 {name!r}")

    def reference(self, value, where):
        """引用：只有一个键，键是四种引用之一。"""
        if len(value) != 1:
            self.fail(where, f"引用只能有一个键，实际有：{'、'.join(value)}")
        (key, arg), = value.items()
        if key not in REF_KEYS:
            self.fail(where, f"不认识的引用「{key}」")
        where = f"{where}.{key}"
        if key in (REF_SLOT, REF_LENGTH):
            self.slot(arg, where)
            return
        size = 2 if key == REF_FIRST_NULL else 3
        if not isinstance(arg, list) or len(arg) != size:
            self.fail(where, f"应当是 {size} 个字符串组成的数组（槽位名与字段名）")
        self.slot(arg[0], f"{where}[0]")
        for index in range(1, size):
            self.field_name(arg[index], f"{where}[{index}]")

    def value_tree(self, value, where):
        """参数值：逐层检查，遇到引用形状的字典按引用校验。"""
        if _is_reference(value):
            self.reference(value, where)
        elif isinstance(value, dict):
            for key, child in value.items():
                self.value_tree(child, f"{where}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self.value_tree(child, f"{where}[{index}]")

    def predicates(self, value, where):
        if not isinstance(value, list) or not value:
            self.fail(where, "应当是非空的谓词数组")
        for index, predicate in enumerate(value):
            here = f"{where}[{index}]"
            if not isinstance(predicate, dict) or PREDICATE not in predicate:
                self.fail(here, f"缺少键「{PREDICATE}」")
            kind = predicate[PREDICATE]
            if kind not in PRED_KEYS:
                self.fail(f"{here}.{PREDICATE}", f"不认识的谓词「{kind}」，只有{'、'.join(PRED_KEYS)}")
            self.dict_with(predicate, here, (PREDICATE,) + PRED_KEYS[kind])
            if kind == PRED_EQUAL:
                self.value_tree(predicate["左"], f"{here}.左")
                self.value_tree(predicate["右"], f"{here}.右")
            else:
                self.slot(predicate["槽位"], f"{here}.槽位")
                if kind == PRED_NO_NULL_ITEM:
                    self.field_name(predicate["字段"], f"{here}.字段")

    def tool_name(self, name, where):
        if not isinstance(name, str) or name not in STATIC_TOOLS:
            self.fail(where, f"工具「{name}」不在静态工具表里")
        if name == EXCEPTION_TOOL:
            self.fail(where, f"「{EXCEPTION_TOOL}」由调用选择自动构造，不能写进任务定义")

    def step(self, value, where):
        self.dict_with(value, where, (STEP_NOTE, STEP_TOOL, STEP_PARAMS), (GROUP_UNTIL, GROUP_MAX))
        if not isinstance(value[STEP_NOTE], str) or not value[STEP_NOTE].strip():
            self.fail(f"{where}.{STEP_NOTE}", "步骤的说明应当是一句非空的话，写这一步做什么")
        self.tool_name(value[STEP_TOOL], f"{where}.{STEP_TOOL}")
        params = value[STEP_PARAMS]
        here = f"{where}.{STEP_PARAMS}"
        expected = STATIC_TOOLS[value[STEP_TOOL]].param_names
        if not isinstance(params, dict):
            self.fail(here, "应当是对象")
        unknown = [key for key in params if key not in expected]
        if unknown:
            self.fail(here, f"工具 {value[STEP_TOOL]} 没有参数：{'、'.join(unknown)}")
        optional = STATIC_TOOLS[value[STEP_TOOL]].optional_params
        missing = [key for key in expected if key not in params and key not in optional]
        if missing:
            self.fail(here, f"工具 {value[STEP_TOOL]} 缺少参数：{'、'.join(missing)}")
        for key, child in params.items():
            self.value_tree(child, f"{here}.{key}")


def _read_json(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise LoadError(path, "文件", f"读不到文件：{exc}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoadError(path, f"第 {exc.lineno} 行第 {exc.colno} 列", f"不是合法的 JSON：{exc.msg}") from None


def _limit(check, value, where):
    """「最多」：非负整数或引用。返回规整后的值：与 JSON Schema 的 integer 一致，3.0 这样的整数值小数也接受，转成 3。"""
    if _is_reference(value):
        check.reference(value, where)
        return copy.deepcopy(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
        check.fail(where, f"应当是非负整数或引用，实际是 {value!r}")
    return value


def load(path, name=None, initial=None) -> TaskDefinition:
    """加载一份任务定义文件，返回新的任务定义对象（每次调用都是新对象）。

    name：覆盖文件里的名字。initial：初始输入，即启动一次任务时给定的输入，槽位名到值，覆盖文件里的默认值；
    槽位必须在文件里存在。文件或初始输入不合格抛加载错误（LoadError），带文件路径与出错位置。
    主干顺序：读文件、查顶层四块、解析槽位元数据（得默认值与提问句式）、查交付物、查初始输入、解析阶段与步骤、生成说明表、建对象。
    """
    path = str(path)
    raw = _read_json(path)
    check = _Checker(path)
    _check_top(check, raw)
    slots, templates = _parse_slots(check, raw[KEY_SLOTS])
    check.slots = slots  # 引用只能指向任务作者写的槽位
    _check_deliverables(check, raw[KEY_DELIVERABLES], raw[KEY_SLOTS])
    _check_initial(check, initial, slots)
    parsed, number = _parse_stages(check, raw[KEY_STAGES])
    _check_required_slots(check, parsed, raw[KEY_SLOTS])
    definition, notes, stages = _build_definition(parsed, number)
    definition[KEY_SLOTS] = copy.deepcopy(raw[KEY_SLOTS])
    definition[KEY_DELIVERABLES] = copy.deepcopy(raw[KEY_DELIVERABLES])
    if KEY_DOMAIN_RULES in raw:
        definition[KEY_DOMAIN_RULES] = raw[KEY_DOMAIN_RULES]
    patterns = None
    if any(STATIC_TOOLS[step.tool].uses_patterns for entry in parsed for step in entry[3]):
        patterns = load_patterns(max(notes) if notes else 0)
        definition[KEY_PATTERNS] = patterns["definition"]
        notes.update(patterns["notes"])
    if initial is not None:
        slots.update(copy.deepcopy(initial))
    name_out = raw[KEY_NAME] if name is None else name
    return TaskDefinition(name_out, slots, definition, notes, templates, tuple(stages), copy.deepcopy(raw[KEY_DELIVERABLES]),
                          patterns)


@dataclass(frozen=True)
class Mode:
    """一个对话模式：名字、触发时带的输入键、步骤（Step 元组，编号接在任务定义的全部编号之后）。"""

    name: str
    inputs: tuple
    steps: tuple


PATTERN_FUNCTIONS = ("REQUEST", "CLARIFY", "DEFER", "INFORM")  # 路由表里可以有去向的功能（第五步 4.12 节）


def load_patterns(last_number: int, path=None) -> dict:
    """读对话模式文件，用与任务定义同一套校验查步骤（工具名、参数名、说明），另查三种模式引用与路由表。

    返回 {definition, notes, modes, numbers, routes}：definition 并进任务定义的 DEFINITION，步骤带编号；
    notes 是依据说明；modes 是模式名到 Mode；numbers 是编号到（Mode, 模式内序号）；routes 是功能到模式名。
    """
    path = str(PATTERNS_FILE if path is None else path)
    raw = _read_json(path)
    check = _Checker(path)
    check.dict_with(raw, "顶层", (PAT_ROUTES, PAT_MODES), ("说明",))
    if not isinstance(raw[PAT_MODES], list) or not raw[PAT_MODES]:
        check.fail(PAT_MODES, "应当是非空数组")
    modes, numbers, notes, entries, number = {}, {}, {}, [], last_number
    for index, item in enumerate(raw[PAT_MODES]):
        where = f"{PAT_MODES}[{index}]"
        check.dict_with(item, where, (PAT_NAME, PAT_INPUTS, PAT_STEPS))
        name = item[PAT_NAME]
        if not isinstance(name, str) or not name or name in modes:
            check.fail(f"{where}.{PAT_NAME}", f"应当是不重名的非空字符串，实际是 {name!r}")
        where = f"{where}（{name}）"
        inputs = item[PAT_INPUTS]
        if not isinstance(inputs, list) or not all(isinstance(key, str) and key for key in inputs):
            check.fail(f"{where}.{PAT_INPUTS}", "应当是非空字符串的数组")
        if not isinstance(item[PAT_STEPS], list) or not item[PAT_STEPS]:
            check.fail(f"{where}.{PAT_STEPS}", "应当是非空数组")
        steps, rows = [], []
        for pos, raw_step in enumerate(item[PAT_STEPS]):
            here = f"{where}.{PAT_STEPS}[{pos}]"
            check.step(raw_step, here)
            if GROUP_UNTIL in raw_step or GROUP_MAX in raw_step:
                check.fail(here, "模式的步骤不重复")
            _check_pattern_refs(check, raw_step[STEP_PARAMS], f"{here}.{STEP_PARAMS}", inputs)
            number += 1
            step = Step(number, raw_step[STEP_NOTE], raw_step[STEP_TOOL], copy.deepcopy(raw_step[STEP_PARAMS]), pos, None)
            steps.append(step)
            notes[number] = f"插入段{STAGE_NAME_SEPARATOR}{name} 第 {pos + 1} 步 {step.note}"
            rows.append(_step_definition(step))
        mode = Mode(name, tuple(inputs), tuple(steps))
        modes[name] = mode
        numbers.update({step.number: (mode, step.pos) for step in steps})
        entries.append({PAT_NAME: name, PAT_INPUTS: list(inputs), PAT_STEPS: rows})
    routes = {}
    if not isinstance(raw[PAT_ROUTES], list):
        check.fail(PAT_ROUTES, "应当是数组")
    for index, row in enumerate(raw[PAT_ROUTES]):
        where = f"{PAT_ROUTES}[{index}]"
        check.dict_with(row, where, ("功能", PAT_MODES), ("条件",))
        if row["功能"] not in PATTERN_FUNCTIONS or row["功能"] in routes:
            check.fail(f"{where}.功能", f"只能是{'、'.join(PATTERN_FUNCTIONS)}之一且不重复，实际是 {row['功能']!r}")
        if row[PAT_MODES] not in modes:
            check.fail(f"{where}.{PAT_MODES}", f"没有这个模式：{row[PAT_MODES]!r}")
        routes[row["功能"]] = row[PAT_MODES]
    definition = {PAT_ROUTES: copy.deepcopy(raw[PAT_ROUTES]), PAT_MODES: entries}
    return {"definition": definition, "notes": notes, "modes": modes, "numbers": numbers, "routes": routes}


def _check_pattern_refs(check, value, where, inputs) -> None:
    """模式引用：{"输入": 键}（点号路径的第一段必须是模式的输入键）、{"上一步结果": true}、{"句式": 句子}（占位名必须是输入键）。"""
    import string

    if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in PREF_KEYS:
        (key, arg), = value.items()
        if key == PREF_INPUT and not (isinstance(arg, str) and arg.split(".")[0] in inputs):
            check.fail(f"{where}.{key}", f"输入引用 {arg!r} 不是模式的输入键（{'、'.join(inputs)}）")
        if key == PREF_LAST and arg is not True:
            check.fail(f"{where}.{key}", "应当写 true")
        if key == PREF_SENTENCE:
            if not isinstance(arg, str):
                check.fail(f"{where}.{key}", "句式应当是字符串")
            names = [field for _, field, _, _ in string.Formatter().parse(arg) if field is not None]
            unknown = [field for field in names if field not in inputs]
            if unknown:
                check.fail(f"{where}.{key}", f"句式里的占位 {'、'.join(unknown)} 不是模式的输入键")
        return
    if isinstance(value, dict):
        for child_key, child in value.items():
            _check_pattern_refs(check, child, f"{where}.{child_key}", inputs)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_pattern_refs(check, child, f"{where}[{index}]", inputs)


# ───────────────────────── 系统提示用的任务定义摘要（第四步 4.8 节）─────────────────────────
# 摘要是写给模型看的一段话，不是把定义文件的 JSON 塞过去：槽位与各自的说明、阶段与目标、交付物各一句。
# 内容全部来自定义文件，任务期间不变，所以它进系统提示而不进用户内容。


def operand_text(operand) -> str:
    """把引用或字面量写成一句人话：槽位引用写槽位名，长度引用写「X 的长度」，字面量原样。"""
    if isinstance(operand, dict):
        if REF_SLOT in operand:
            return f"{operand[REF_SLOT]}"
        if REF_LENGTH in operand:
            return f"{operand[REF_LENGTH]} 的长度"
        if REF_FIRST_NULL in operand:
            return f"{operand[REF_FIRST_NULL][0]} 里首个 {operand[REF_FIRST_NULL][1]} 为空的项"
        if REF_FIRST_NULL_FIELD in operand:
            target = operand[REF_FIRST_NULL_FIELD]
            return f"{target[0]} 里首个 {target[1]} 为空的项的 {target[2]}"
    return str(operand)


def predicate_text(predicate) -> str:
    """谓词写成一句不带当前值的话，用在摘要与进度陈述里；带当前值的那份在 _goal_text 里，给告知异常用。"""
    kind = predicate.get(PREDICATE)
    if kind == PRED_NOT_NULL:
        return f"{predicate[REF_SLOT]}不为空"
    if kind == PRED_EQUAL:
        return f"{operand_text(predicate['左'])} 等于 {operand_text(predicate['右'])}"
    return f"{predicate[REF_SLOT]} 里没有 {predicate['字段']} 为空的项"


def _group_rounds_text(stage) -> str:
    """阶段里若有循环段且上限是个整数，摘要里补一句说明它最多循环几次。上限写成引用的（如文件总表的长度）不写死数字。"""
    for step in stage.get(STAGE_STEPS, []) or []:
        limit = step.get(GROUP_MAX)
        if GROUP_STEPS in step and isinstance(limit, int) and not isinstance(limit, bool):
            return f"；这几步走完一遍算一次循环，最多 {limit} 次"
    return ""


def summary_text(task_def) -> str:
    """任务定义摘要：槽位（含说明）、阶段（含目标）、交付物，拼成一段话。"""
    definition = task_def.DEFINITION
    slots = [f"{name}（{meta.get(SLOT_NOTE, '')}）" for name, meta in definition[KEY_SLOTS].items()]
    stages = []
    for stage in definition[DEF_STAGES]:
        goal = "、".join(predicate_text(item) for item in stage.get(STAGE_GOAL, []) or [])
        stages.append(f"{stage[STAGE_NAME]}（目标：{goal}{_group_rounds_text(stage)}）")
    delivs = "；".join(f"{item[DELIV_NAME]}，来源{item[DELIV_SOURCE]}，{item[DELIV_FORM]}"
                      for item in definition.get(KEY_DELIVERABLES, []) or [])
    return (f"槽位（任务的数据项，每项一个名字一个值）：{'、'.join(slots)}。"
            f"阶段：{'、'.join(stages)}。交付物：{delivs}。")


def domain_rules_of(task_def):
    """任务定义的可选块「领域规矩」，没有就是 None。"""
    return task_def.DEFINITION.get(KEY_DOMAIN_RULES)


def _check_top(check, raw) -> None:
    """顶层四块必填、「领域规矩」可选：键齐全、类型正确。"""
    check.dict_with(raw, "顶层", TOP_KEYS, TOP_OPTIONAL_KEYS)
    if KEY_DOMAIN_RULES in raw and not (isinstance(raw[KEY_DOMAIN_RULES], str) and raw[KEY_DOMAIN_RULES].strip()):
        check.fail(KEY_DOMAIN_RULES, "应当是非空字符串（写给模型看的领域写法要求）")
    if not isinstance(raw[KEY_NAME], str) or not raw[KEY_NAME]:
        check.fail(KEY_NAME, "应当是非空字符串")
    if not isinstance(raw[KEY_SLOTS], dict):
        check.fail(KEY_SLOTS, "应当是对象（槽位名到槽位元数据）")
    if not isinstance(raw[KEY_STAGES], list) or not raw[KEY_STAGES]:
        check.fail(KEY_STAGES, "应当是非空数组")
    if not isinstance(raw[KEY_DELIVERABLES], list):
        check.fail(KEY_DELIVERABLES, "应当是数组（交付物列表）")


def _check_deliverables(check, deliverables, slot_metas) -> None:
    """交付物：每项名字、说明、来源、形态齐全；来源槽位存在；形态与来源槽位的类型匹配。"""
    for index, item in enumerate(deliverables):
        where = f"{KEY_DELIVERABLES}[{index}]"
        if isinstance(item, dict) and isinstance(item.get(DELIV_NAME), str) and item[DELIV_NAME]:
            where = f"{where}（{item[DELIV_NAME]}）"
        check.dict_with(item, where, (DELIV_NAME, DELIV_NOTE, DELIV_SOURCE, DELIV_FORM))
        for key in (DELIV_NAME, DELIV_NOTE):
            if not isinstance(item[key], str) or not item[key].strip():
                check.fail(f"{where}.{key}", "应当是非空字符串")
        form = item[DELIV_FORM]
        if form not in DELIV_FORMS:
            check.fail(f"{where}.{DELIV_FORM}", f"只能是{'、'.join(DELIV_FORMS)}之一，实际是 {form!r}")
        source, here = item[DELIV_SOURCE], f"{where}.{DELIV_SOURCE}"
        if form == FORM_FORM:
            if not isinstance(source, list) or not source:
                check.fail(here, "表单的来源应当是非空的槽位名列表")
            for slot_index, name in enumerate(source):
                check.slot(name, f"{here}[{slot_index}]")
                if slot_metas[name][SLOT_TYPE] not in SCALAR_TYPES:
                    check.fail(f"{here}[{slot_index}]", f"表单的来源应当是标量槽位（{'、'.join(SCALAR_TYPES)}），「{name}」是{slot_metas[name][SLOT_TYPE]}")
            continue
        if not isinstance(source, str):
            check.fail(here, f"{form}的来源应当是一个槽位名")
        check.slot(source, here)
        meta = slot_metas[source]
        if form == FORM_TABLE and (meta[SLOT_TYPE] != "列表" or SLOT_ITEM not in meta):
            check.fail(here, f"表格的来源应当是带「{SLOT_ITEM}」的列表型槽位，「{source}」不是")
        if form in (FORM_TEXT, FORM_FILE) and meta[SLOT_TYPE] != "文本":
            check.fail(here, f"{form}的来源应当是文本型槽位，「{source}」是{meta[SLOT_TYPE]}")



def _check_required_slots(check, parsed, slot_metas) -> None:
    """任务用到的工具在静态工具表里声明了必备槽位的（例如对话理解要回复、上一问、待处理），槽位表里必须有、类型必须对。"""
    for _, _, _, steps, toolset, _ in parsed:
        for tool in [step.tool for step in steps] + list(toolset):
            for slot, kind in (STATIC_TOOLS[tool].required_slots or {}).items():
                if slot not in slot_metas:
                    check.fail(f"{KEY_SLOTS}", f"用了工具「{tool}」的任务必须有槽位「{slot}」（{kind}）")
                if slot_metas[slot][SLOT_TYPE] != kind:
                    check.fail(f"{KEY_SLOTS}.{slot}", f"工具「{tool}」要求槽位「{slot}」的类型是{kind}，实际是{slot_metas[slot][SLOT_TYPE]}")


def _check_initial(check, initial, slots) -> None:
    """初始输入：是字典、槽位都在文件里存在。"""
    if initial is None:
        return
    if not isinstance(initial, dict):
        check.fail(INITIAL_INPUT, "应当是字典")
    unknown = [slot for slot in initial if slot not in slots]
    if unknown:
        check.fail(INITIAL_INPUT, f"槽位不存在：{'、'.join(map(str, unknown))}")


def _parse_stages(check, raw_stages):
    """阶段列表：逐个校验并按阶段顺序给步骤编号。返回（解析结果列表, 最后用掉的步骤号）。

    解析结果每项是（名字, 类型, 目标, 步骤元组, 工具集, 回合上限）。
    """
    parsed, seen_names, number = [], set(), 0
    for stage_index, stage in enumerate(raw_stages):
        entry, number = _parse_stage(check, stage, f"{KEY_STAGES}[{stage_index}]", seen_names, number)
        parsed.append(entry)
    return parsed, number


def _parse_stage(check, stage, where, seen_names, number):
    """一个阶段：先查阶段头（名字、类型、目标），再按类型解析步骤或可选工具集。返回（解析结果, 最后用掉的步骤号）。"""
    if not isinstance(stage, dict):
        check.fail(where, "应当是对象")
    stage_name = stage.get(STAGE_NAME)
    if isinstance(stage_name, str) and stage_name:
        where = f"{where}（{stage_name}）"
    stage_type = _check_stage_head(check, stage, where, seen_names)
    goal = tuple(copy.deepcopy(stage[STAGE_GOAL]))
    if stage_type == TYPE_FIXED:
        steps, number = _parse_steps(check, stage[STAGE_STEPS], f"{where}.{STAGE_STEPS}", number)
        return (stage_name, stage_type, goal, tuple(steps), (), None), number
    tools = stage[STAGE_TOOLSET]
    if not isinstance(tools, list) or not tools:
        check.fail(f"{where}.{STAGE_TOOLSET}", "应当是非空数组")
    for tool_index, tool in enumerate(tools):
        check.tool_name(tool, f"{where}.{STAGE_TOOLSET}[{tool_index}]")
    stage_max = _limit(check, stage[STAGE_MAX], f"{where}.{STAGE_MAX}")
    return (stage_name, stage_type, goal, (), tuple(tools), stage_max), number


def _check_stage_head(check, stage, where, seen_names) -> str:
    """阶段头：键、名字（非空、不含分隔符、不重名）、类型（取值与内容一致）、目标。返回类型。"""
    check.dict_with(stage, where, (STAGE_NAME, STAGE_TYPE, STAGE_GOAL), (STAGE_STEPS, STAGE_TOOLSET, STAGE_MAX))
    stage_name = stage[STAGE_NAME]
    if not isinstance(stage_name, str) or not stage_name:
        check.fail(f"{where}.{STAGE_NAME}", "应当是非空字符串")
    if STAGE_NAME_SEPARATOR in stage_name:
        check.fail(f"{where}.{STAGE_NAME}", f"阶段名不得含「{STAGE_NAME_SEPARATOR}」：依据说明用它分隔阶段名，观测台靠它拆出阶段")
    if stage_name in seen_names:
        check.fail(f"{where}.{STAGE_NAME}", f"阶段重名：{stage_name}")
    seen_names.add(stage_name)
    stage_type = stage[STAGE_TYPE]
    if stage_type not in (TYPE_FIXED, TYPE_PLANNED):
        check.fail(f"{where}.{STAGE_TYPE}", f"只能是「{TYPE_FIXED}」或「{TYPE_PLANNED}」，实际是 {stage_type!r}")
    if stage_type == TYPE_FIXED and (STAGE_STEPS not in stage or STAGE_TOOLSET in stage or STAGE_MAX in stage):
        check.fail(f"{where}.{STAGE_TYPE}",
                   f"类型与内容不符：「{TYPE_FIXED}」必须有「{STAGE_STEPS}」且不得有「{STAGE_TOOLSET}」「{STAGE_MAX}」")
    if stage_type == TYPE_PLANNED and (STAGE_TOOLSET not in stage or STAGE_STEPS in stage):
        check.fail(f"{where}.{STAGE_TYPE}", f"类型与内容不符：「{TYPE_PLANNED}」必须有「{STAGE_TOOLSET}」且不得有「{STAGE_STEPS}」")
    if stage_type == TYPE_PLANNED and STAGE_MAX not in stage:
        check.fail(where, f"「{TYPE_PLANNED}」阶段必须写「{STAGE_MAX}」（回合上限）")
    check.predicates(stage[STAGE_GOAL], f"{where}.{STAGE_GOAL}")
    return stage_type


def _parse_steps(check, items, where, number):
    """固定步骤阶段的步骤列表：每项是步骤（可带成对的重复键）或步骤组。返回（Step 列表, 最后用掉的步骤号）。"""
    if not isinstance(items, list) or not items:
        check.fail(where, "应当是非空数组")
    steps = []
    for item_index, item in enumerate(items):
        here = f"{where}[{item_index}]"
        if isinstance(item, dict) and GROUP_STEPS in item:
            number = _parse_group(check, item, here, steps, number)
        else:
            number = _parse_step(check, item, here, steps, number)
    return steps, number


def _repeat_pair(check, item, here, what) -> bool:
    """「重复直到」与「最多」要么都写要么都不写。返回是否写了；写了就校验两者。"""
    present = [key for key in (GROUP_UNTIL, GROUP_MAX) if key in item]
    if present and len(present) < 2:
        missing = GROUP_MAX if GROUP_UNTIL in present else GROUP_UNTIL
        check.fail(here, f"{what}的「{GROUP_UNTIL}」与「{GROUP_MAX}」要么都写要么都不写，缺少「{missing}」")
    return bool(present)


def _parse_step(check, item, here, steps, number) -> int:
    """一个步骤。带成对的重复键时按只有一个成员的组处理。追加进 steps，返回最后用掉的步骤号。"""
    repeats = isinstance(item, dict) and _repeat_pair(check, item, here, "单个步骤")
    check.step(item, here)
    group = None
    if repeats:
        check.predicates(item[GROUP_UNTIL], f"{here}.{GROUP_UNTIL}")
        group_max = _limit(check, item[GROUP_MAX], f"{here}.{GROUP_MAX}")
        group = Group(len(steps), len(steps), tuple(copy.deepcopy(item[GROUP_UNTIL])), group_max, single=True)
    number += 1
    steps.append(Step(number, item[STEP_NOTE], item[STEP_TOOL], copy.deepcopy(item[STEP_PARAMS]), len(steps), group))
    return number


def _parse_group(check, item, here, steps, number) -> int:
    """一个步骤组：「重复直到」与「最多」同时写、组内步骤非空、不嵌套、不带重复键。组内步骤追加进 steps，返回最后用掉的步骤号。"""
    if not _repeat_pair(check, item, here, "步骤组"):
        check.fail(here, f"步骤组必须同时写「{GROUP_UNTIL}」与「{GROUP_MAX}」")
    check.dict_with(item, here, (GROUP_STEPS, GROUP_UNTIL, GROUP_MAX))
    inner_steps = item[GROUP_STEPS]
    if not isinstance(inner_steps, list) or not inner_steps:
        check.fail(f"{here}.{GROUP_STEPS}", "应当是非空数组")
    for inner_index, inner in enumerate(inner_steps):
        inner_where = f"{here}.{GROUP_STEPS}[{inner_index}]"
        if isinstance(inner, dict) and GROUP_STEPS in inner:
            check.fail(inner_where, "步骤组不能嵌套")
        if isinstance(inner, dict) and (GROUP_UNTIL in inner or GROUP_MAX in inner):
            check.fail(inner_where, f"组内步骤不得带「{GROUP_UNTIL}」「{GROUP_MAX}」，重复由步骤组统一写")
        check.step(inner, inner_where)
    check.predicates(item[GROUP_UNTIL], f"{here}.{GROUP_UNTIL}")
    group_max = _limit(check, item[GROUP_MAX], f"{here}.{GROUP_MAX}")
    group = Group(len(steps), len(steps) + len(inner_steps) - 1, tuple(copy.deepcopy(item[GROUP_UNTIL])), group_max)
    for inner in inner_steps:
        number += 1
        steps.append(Step(number, inner[STEP_NOTE], inner[STEP_TOOL], copy.deepcopy(inner[STEP_PARAMS]), len(steps), group))
    return number


def _parse_slots(check, raw_slots):
    """槽位元数据：返回（槽位名 → 默认值, 话语模板）。

    话语模板的键与 dialogue.py 读的一致：槽位上的「提问」是（槽位名, ()），列表项字段上的是（槽位名, (字段名,)）；
    「再问短句」的键是提问的键后面再接「再问短句」，例如（槽位名, (), "再问短句"）。
    类型只做加载校验，不做运行时值检查。
    """
    slots, templates = {}, {}
    for name, meta in raw_slots.items():
        where = f"{KEY_SLOTS}.{name}"
        _check_slot_meta(check, meta, where, (SLOT_DEFAULT, SLOT_VALUES, SLOT_ITEM, SLOT_QUESTION, SLOT_REASK,
                                                    SLOT_USER_WRITABLE))
        if SLOT_USER_WRITABLE in meta and not isinstance(meta[SLOT_USER_WRITABLE], bool):
            check.fail(f"{where}.{SLOT_USER_WRITABLE}", "应当是布尔值（true 或 false）")
        slots[name] = copy.deepcopy(meta.get(SLOT_DEFAULT))
        if SLOT_QUESTION in meta:
            templates[(name, ())] = meta[SLOT_QUESTION]
        if SLOT_REASK in meta:
            templates[(name, (), SLOT_REASK)] = meta[SLOT_REASK]
        if SLOT_ITEM not in meta:
            continue
        if not isinstance(meta[SLOT_ITEM], dict) or not meta[SLOT_ITEM]:
            check.fail(f"{where}.{SLOT_ITEM}", "应当是非空对象（字段名到字段元数据）")
        for field_name, field in meta[SLOT_ITEM].items():
            _check_slot_meta(check, field, f"{where}.{SLOT_ITEM}.{field_name}", (SLOT_VALUES, SLOT_QUESTION, SLOT_REASK))
            if SLOT_QUESTION in field:
                templates[(name, (field_name,))] = field[SLOT_QUESTION]
            if SLOT_REASK in field:
                templates[(name, (field_name,), SLOT_REASK)] = field[SLOT_REASK]
    return slots, templates


def _check_slot_meta(check, meta, where, optional) -> None:
    """一个槽位或列表项字段的元数据：说明与类型必填，类型六选一，枚举型必须有取值，提问是字符串，再问短句是非空字符串。"""
    check.dict_with(meta, where, (SLOT_NOTE, SLOT_TYPE), optional)
    if not isinstance(meta[SLOT_NOTE], str) or not meta[SLOT_NOTE].strip():
        check.fail(f"{where}.{SLOT_NOTE}", "应当是一句非空的话，说这个槽位装什么")
    if meta[SLOT_TYPE] not in SLOT_TYPES:
        check.fail(f"{where}.{SLOT_TYPE}", f"只能是{'、'.join(SLOT_TYPES)}之一，实际是 {meta[SLOT_TYPE]!r}")
    if meta[SLOT_TYPE] == TYPE_ENUM and SLOT_VALUES not in meta:
        check.fail(where, f"枚举型必须写「{SLOT_VALUES}」")
    if SLOT_VALUES in meta and (not isinstance(meta[SLOT_VALUES], list) or not meta[SLOT_VALUES]):
        check.fail(f"{where}.{SLOT_VALUES}", "应当是非空数组（允许的值）")
    if SLOT_QUESTION in meta and not isinstance(meta[SLOT_QUESTION], str):
        check.fail(f"{where}.{SLOT_QUESTION}", "应当是字符串（str.format 句式）")
    if SLOT_REASK in meta and not (isinstance(meta[SLOT_REASK], str) and meta[SLOT_REASK].strip()):
        check.fail(f"{where}.{SLOT_REASK}", "应当是非空字符串（再问同一个问题时说的短句）")


# DEFINITION 里的键。
DEF_STAGES = "阶段列表"  # 另有「槽位」：文件里的槽位元数据原样
DEF_NUMBER = "编号"
DEF_EXCEPTION_NUMBERS = "告知异常编号"
DEF_TOOL_NUMBERS = "自主工具编号"


def _build_definition(parsed, number):
    """结构化定义、依据说明表与阶段对象。number 是最后用掉的步骤号。返回（DEFINITION, 说明表, Stage 列表）。

    编号：步骤号已编好；每个阶段的告知异常接在全部步骤号之后各占一个号；自主规划阶段可选工具集里的工具再各占一个号。
    说明表：步骤是「阶段名 › 第 n 步 步骤说明」，告知异常是「阶段名 › 告知异常」，自主工具是「阶段名 › 自主 › 工具名」。
    """
    notes, stages, exception_numbers, tool_numbers = {}, [], {}, {}
    for stage_name, _, _, steps, _, _ in parsed:
        for step in steps:
            notes[step.number] = f"{stage_name}{STAGE_NAME_SEPARATOR}第 {step.number} 步 {step.note}"
    for stage_name, stage_type, goal, steps, toolset, stage_max in parsed:
        number += 1
        notes[number] = f"{stage_name}{STAGE_NAME_SEPARATOR}{EXCEPTION_TOOL}"
        exception_numbers[stage_name] = number
        stages.append(Stage(stage_name, stage_type, goal, steps, toolset, stage_max, number))
    for stage_name, _, _, _, toolset, _ in parsed:
        for tool in toolset:
            number += 1
            notes[number] = f"{stage_name}{STAGE_NAME_SEPARATOR}自主{STAGE_NAME_SEPARATOR}{tool}"
            tool_numbers.setdefault(stage_name, {})[tool] = number
    definition = {DEF_STAGES: [_stage_definition(stage) for stage in stages],
                  DEF_EXCEPTION_NUMBERS: exception_numbers, DEF_TOOL_NUMBERS: tool_numbers}
    return definition, notes, stages


def _stage_definition(stage) -> dict:
    """一个阶段的结构化定义：与文件里的写法同形，步骤多一个「编号」（全局步骤号）。"""
    entry = {STAGE_NAME: stage.name, STAGE_TYPE: stage.type, STAGE_GOAL: copy.deepcopy(list(stage.goal))}
    if stage.type == TYPE_PLANNED:
        entry[STAGE_TOOLSET] = list(stage.toolset)
        entry[STAGE_MAX] = copy.deepcopy(stage.max)
        return entry
    items, index = [], 0
    while index < len(stage.steps):
        step = stage.steps[index]
        if step.group is None:
            items.append(_step_definition(step))
            index += 1
            continue
        group = step.group
        repeat = {GROUP_UNTIL: copy.deepcopy(list(group.until)), GROUP_MAX: copy.deepcopy(group.max)}
        if group.single:
            items.append({**_step_definition(step), **repeat})
        else:
            items.append({GROUP_STEPS: [_step_definition(inner) for inner in stage.steps[group.first:group.last + 1]], **repeat})
        index = group.last + 1
    entry[STAGE_STEPS] = items
    return entry


def _step_definition(step) -> dict:
    return {DEF_NUMBER: step.number, STEP_NOTE: step.note, STEP_TOOL: step.tool, STEP_PARAMS: copy.deepcopy(step.params)}
