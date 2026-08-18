"""修订点（v5）：基准表达上的独立编辑片段，可分点选择、一次采纳。

口径（docs/40 domains/DS-001/data.md LDM-009）：
- 每点 = 局部编辑（find_text 在基准表达中恰出现一次 → replace_text）；
- 点间 find 跨度不重叠 → 任意子集可合成（从右向左替换，顺序无关）；
- 联动组（group）内的点必须整组选择；
- 合成与校验是确定性代码，不信任模型自检（服务端守卫）。
点的 JSON 结构：{point_ref, label, finding_index, find, replace, basis, group}
"""
from __future__ import annotations

from typing import Optional


def validate_points(base: str, points: list[dict]) -> Optional[str]:
    """可合成性校验：返回错误说明；None = 通过。"""
    spans: list[tuple[int, int]] = []
    seen_refs: set[str] = set()
    for p in points:
        ref = str(p.get("point_ref") or "")
        find = str(p.get("find") or "")
        replace = str(p.get("replace") or "")
        if not ref or ref in seen_refs:
            return "修订点引用缺失或重复"
        seen_refs.add(ref)
        if not find or not replace or find == replace:
            return f"修订点 {ref} 编辑片段不完整或无变化"
        first = base.find(find)
        if first < 0:
            return f"修订点 {ref} 的定位片段不在基准表达中"
        if base.find(find, first + 1) >= 0:
            return f"修订点 {ref} 的定位片段在基准表达中出现多次，无法唯一定位"
        spans.append((first, first + len(find)))
    spans.sort()
    for (s1, e1), (s2, _e2) in zip(spans, spans[1:]):
        if s2 < e1:
            return "修订点之间的编辑跨度重叠，无法独立应用"
    return None


def expand_selection(points: list[dict], selected_refs: list[str]) -> Optional[list[str]]:
    """联动组展开：所选点的联动组必须整组入选；返回展开后的点集合；None = 选择为空。"""
    by_ref = {str(p.get("point_ref")): p for p in points}
    chosen = {r for r in selected_refs if r in by_ref}
    if not chosen:
        return None
    groups = {str(by_ref[r].get("group")) for r in chosen if by_ref[r].get("group")}
    for p in points:
        if p.get("group") and str(p.get("group")) in groups:
            chosen.add(str(p.get("point_ref")))
    return [str(p.get("point_ref")) for p in points if str(p.get("point_ref")) in chosen]


def compose(base: str, points: list[dict], selected_refs: list[str]) -> str:
    """按所选点合成修订后表达（从右向左替换，子集顺序无关）。"""
    chosen = [p for p in points if str(p.get("point_ref")) in set(selected_refs)]
    located = sorted(
        ((base.find(str(p.get("find"))), p) for p in chosen),
        key=lambda t: t[0], reverse=True,
    )
    out = base
    for idx, p in located:
        find = str(p.get("find"))
        out = out[:idx] + str(p.get("replace")) + out[idx + len(find):]
    return out
