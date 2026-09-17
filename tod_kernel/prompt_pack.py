"""提示词包：每个内部调用模型的工具一份数据文件 prompts/<工具名>.json（第五步 4.4A 节）。

一份包里有四样东西：
- 固定指令：进系统提示的工具目录，任务期间不变；
- 本步：【本步】段的模板，按情形分键，例如对话理解按上一问的类型分五种写法；
- 片段：其他段里用到的短句模板，例如任务进度段末尾的一句、参考材料缺失时的说明、可写位置的写法；
- 输出形状：输出形状为文本的工具才有，是一句固定的话；输出为 JSON 的工具由代码现算结构，不进包。

模板里不写条件逻辑，用哪一个情形由工具代码选。占位符写成 {名字}，填空只用标准库的 str.format_map；
模板里要出现字面的花括号时写成 {{ 与 }}。加载时按工具代码声明的「每个模板能提供哪些占位符」逐个核对：
情形或片段缺了、多了，或者模板用了代码提供不了的占位符，一律报提示词包错误，不等到跑起来才发现。
"""

from __future__ import annotations

import json
import string
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

KEY_TOOL = "工具"
KEY_INSTRUCTION = "固定指令"
KEY_STEP = "本步"
KEY_PIECES = "片段"
KEY_SHAPE = "输出形状"


class PromptPackError(Exception):
    """提示词包不合格：带文件路径、出错位置与原因。"""

    def __init__(self, path, where: str, reason: str):
        super().__init__(f"{path}：{where}：{reason}")
        self.path = str(path)
        self.where = where
        self.reason = reason


@dataclass(frozen=True)
class PromptPack:
    """加载好的提示词包。step 与 pieces 都是 键 → 模板文字。"""

    tool: str
    instruction: str
    step: dict
    pieces: dict
    shape: str | None

    def fill_step(self, situation: str, **values) -> str:
        return self.step[situation].format_map(values)

    def fill_piece(self, key: str, **values) -> str:
        return self.pieces[key].format_map(values)


def placeholders(template: str) -> set:
    """模板里用到的占位符名字。格式写错（例如花括号不成对）抛 ValueError。"""
    return {field for _, field, _, _ in string.Formatter().parse(template) if field is not None}


def load(tool: str, provides: dict, has_shape: bool, directory=None) -> PromptPack:
    """读一份提示词包并核对。

    provides 是工具代码的声明：{"本步": {情形: 占位符集合}, "片段": {键: 占位符集合}}；has_shape 说这个工具的包里要不要有输出形状。
    包里的情形与片段必须与声明的键恰好一致，每个模板用到的占位符必须是声明集合的子集；固定指令与输出形状不许有占位符。
    """
    path = Path(directory or PROMPTS_DIR) / f"{tool}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromptPackError(path, "文件", "找不到这份提示词包") from exc
    except json.JSONDecodeError as exc:
        raise PromptPackError(path, "文件", f"不是合法的 JSON（{exc}）") from exc
    if not isinstance(raw, dict):
        raise PromptPackError(path, "顶层", "应当是对象")
    required = [KEY_TOOL, KEY_INSTRUCTION, KEY_STEP, KEY_PIECES] + ([KEY_SHAPE] if has_shape else [])
    missing = [key for key in required if key not in raw]
    extra = [key for key in raw if key not in required]
    if missing or extra:
        raise PromptPackError(path, "顶层", f"键应当恰好是 {'、'.join(required)}"
                              + (f"；缺 {'、'.join(missing)}" if missing else "") + (f"；多了 {'、'.join(extra)}" if extra else ""))
    if raw[KEY_TOOL] != tool:
        raise PromptPackError(path, KEY_TOOL, f"应当是「{tool}」，实际是 {raw[KEY_TOOL]!r}")
    _check_text(path, KEY_INSTRUCTION, raw[KEY_INSTRUCTION], set())
    if has_shape:
        _check_text(path, KEY_SHAPE, raw[KEY_SHAPE], set())
    step = _check_group(path, KEY_STEP, raw[KEY_STEP], provides.get(KEY_STEP, {}))
    pieces = _check_group(path, KEY_PIECES, raw[KEY_PIECES], provides.get(KEY_PIECES, {}))
    return PromptPack(tool, raw[KEY_INSTRUCTION], step, pieces, raw[KEY_SHAPE] if has_shape else None)


def _check_group(path, name: str, group, declared: dict) -> dict:
    if not isinstance(group, dict):
        raise PromptPackError(path, name, "应当是对象（键 → 模板）")
    missing = [key for key in declared if key not in group]
    extra = [key for key in group if key not in declared]
    if missing:
        raise PromptPackError(path, name, f"缺少 {'、'.join(missing)}（代码会用到）")
    if extra:
        raise PromptPackError(path, name, f"多了 {'、'.join(extra)}（代码不会用到）")
    for key, template in group.items():
        _check_text(path, f"{name}.{key}", template, set(declared[key]))
    return dict(group)


def _check_text(path, where: str, template, allowed: set) -> None:
    if not isinstance(template, str) or not template.strip():
        raise PromptPackError(path, where, "应当是非空字符串")
    try:
        used = placeholders(template)
    except ValueError as exc:
        raise PromptPackError(path, where, f"花括号写法不对（{exc}）；字面的花括号写成 {{{{ 与 }}}}") from exc
    unknown = sorted(used - allowed)
    if unknown:
        offered = "、".join(sorted(allowed)) if allowed else "（不提供任何占位符）"
        raise PromptPackError(path, where, f"占位符 {'、'.join('{' + name + '}' for name in unknown)} 代码提供不了；"
                                           f"这里能用的是 {offered}")
