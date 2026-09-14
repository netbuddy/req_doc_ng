"""对话模块：话语生成与回答理解。

这两件事都不在工具里，也不在内核里。工具（例如询问）只调用它们：
- 话语生成：按写入目标查任务定义的话语模板表，用提示填空，得到对使用者说的一句话；
- 回答理解：把使用者的回答文本变成要写进槽位的值。

本步话语生成是查表填空，回答理解原样返回；将来可以换成模型调用，签名不变。
"""

from __future__ import annotations


def template_key(target: dict) -> tuple:
    """话语模板的键：（槽位名, 路径里去掉数字下标后剩下的键组成的元组）。

    例如写入目标 {"slot": "目的地", "path": []} 的键是 ("目的地", ())；
    {"slot": "材料清单", "path": [1, "是否纳入"]} 的键是 ("材料清单", ("是否纳入",))。
    """
    keys = tuple(step for step in target.get("path", []) if not isinstance(step, int))
    return (target["slot"], keys)


def generate_utterance(task_def, params: dict) -> str:
    """话语生成：按参数里的写入目标查任务定义的话语模板，用提示填空，返回一句话。

    查不到模板、或提示里缺少模板要的占位符时，不报错，返回写入目标与提示的可读拼写。
    """
    target = params.get("target") or {}
    hint = params.get("hint") or {}
    templates = getattr(task_def, "TEMPLATES", {}) or {}
    template = templates.get(template_key(target)) if target else None
    if template is not None:
        try:
            return template.format(**hint)
        except (KeyError, IndexError):
            pass
    return _readable(target, hint)


def understand_answer(task_def, params: dict, text):
    """回答理解：把回答文本变成要写的值。本步原样返回。"""
    return text


def _readable(target: dict, hint: dict) -> str:
    slot = target.get("slot", "（未指明槽位）")
    path = target.get("path") or []
    where = f"「{slot}」" + "".join(f"[{step!r}]" for step in path)
    text = f"请提供{where}的值。"
    if hint:
        text += "提示：" + "，".join(f"{key}={value}" for key, value in hint.items()) + "。"
    return text
