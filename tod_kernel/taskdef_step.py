"""任务定义的当前步与对话模式：调用选择、记录本步、当前步的显示，插入段的地址栈、路由、压帧弹帧与帧替换。

执行语义见 taskdef.py 模块说明。本模块提供混入类 StepMixin，由 taskdef.py 末尾的 TaskDefinition 继承；
谓词求值与参数求值的助手在 taskdef.py 里，本模块从那里导入。taskdef.py 在文件末尾才导入本模块，
所以外部一律从 taskdef 导入，不要先于它直接导入本模块。
"""

from __future__ import annotations

import copy
import json

from tod_kernel.kernel import CallStatus, DefinitionError
from tod_kernel.tools import (EXCEPTION_OPTIONS, EXCEPTION_TOOL, REASK_KEY, REASK_ORIGINAL_KEY, REDO_RESULT,
                              REPLACE_KEY, ROUTES_KEY, STATIC_TOOLS)
from tod_kernel.taskdef import (INSERT_KEY, PREF_INPUT, PREF_KEYS, PREF_LAST, STAGE_NAME_SEPARATOR, TYPE_PLANNED,
                                _current_value, _evaluate, _evaluate_params, _goal_text, _holds, _holds_all,
                                _valid_limit, operand_text)

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


class StepMixin:
    """任务定义的「当前步与对话模式」这一半：调用选择、记录本步、当前步的显示，插入段（地址栈、路由、压帧弹帧、帧替换），
    以及一趟往后走的状态机与告知异常候选。只由 TaskDefinition 继承，用到的 _stages、_modes 等表在 TaskDefinition 里建。
    """

    # ── 内核调用的调用选择 ──

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
