"""来源锚点（SourceAnchor）纯函数 —— offset + 引文选择器（W3C Web Annotation 思路）。

结构（slices/SCN-001-P02/页面详细设计.md §4.2）：
  {"material_ref": "<LDM-002 id>", "ranges": [{"start","end","exact","prefix","suffix"}]}

- start/end 为 raw_text 全局字符偏移；exact/prefix/suffix 供 offset 失效后 fallback 重定位。
- 引文在原文中找不到时 start/end = -1（前端按锚点异常处理，不隐藏要素）。
"""
from __future__ import annotations

import json
from typing import Optional

_CONTEXT_CHARS = 24


def build_anchor_json(material_ref: str, raw_text: str, quote: Optional[str]) -> Optional[str]:
    """由引文（exact quote）在原文中定位，产出结构化锚点 JSON 字符串。

    quote 为空 → None（无锚点）。找不到 → start/end=-1 + exact 保留（异常锚点）。
    """
    exact = (quote or "").strip()
    if not exact:
        return None

    start = raw_text.find(exact)
    if start == -1:
        rng = {"start": -1, "end": -1, "exact": exact, "prefix": "", "suffix": ""}
    else:
        end = start + len(exact)
        rng = {
            "start": start,
            "end": end,
            "exact": exact,
            "prefix": raw_text[max(0, start - _CONTEXT_CHARS): start],
            "suffix": raw_text[end: end + _CONTEXT_CHARS],
        }
    return json.dumps({"material_ref": material_ref, "ranges": [rng]}, ensure_ascii=False)


def anchor_from_ranges(material_ref: str, raw_text: str, ranges: list[dict]) -> Optional[str]:
    """由用户选区（start/end 或 exact）构造锚点 JSON；补齐 exact/prefix/suffix。"""
    out: list[dict] = []
    for r in ranges:
        start, end = int(r.get("start", -1)), int(r.get("end", -1))
        exact = str(r.get("exact") or "")
        if 0 <= start < end <= len(raw_text):
            exact = exact or raw_text[start:end]
            out.append({
                "start": start,
                "end": end,
                "exact": exact,
                "prefix": raw_text[max(0, start - _CONTEXT_CHARS): start],
                "suffix": raw_text[end: end + _CONTEXT_CHARS],
            })
        elif exact:
            built = build_anchor_json(material_ref, raw_text, exact)
            if built:
                out.extend(json.loads(built)["ranges"])
    if not out:
        return None
    return json.dumps({"material_ref": material_ref, "ranges": out}, ensure_ascii=False)


def anchor_quotes(source_anchor: Optional[str]) -> list[str]:
    """锚点 JSON → 逐 range 的 exact 引文列表（容错：坏 JSON/缺字段恒空列表）。

    issue #8 清理债：source_anchor 引文解析各服务多份拷贝收口于此（读侧统一入口）。
    """
    if not source_anchor:
        return []
    try:
        ranges = json.loads(source_anchor).get("ranges", [])
    except (ValueError, AttributeError):
        return []
    return [str(r["exact"]) for r in ranges if isinstance(r, dict) and r.get("exact")]


def first_anchor_quote(source_anchor: Optional[str]) -> Optional[str]:
    """锚点 JSON → 首个 exact 引文（提示词组装最常用形状）；无可用引文恒 None。"""
    quotes = anchor_quotes(source_anchor)
    return quotes[0] if quotes else None


def split_blocks(raw_text: str) -> list[dict]:
    """按换行切分正文为段落块（保留全局 offset；空行不产块但占 offset）。"""
    blocks: list[dict] = []
    offset = 0
    for index, line in enumerate(raw_text.split("\n")):
        end = offset + len(line)
        if line.strip():
            blocks.append({
                "block_id": f"b{len(blocks)}",
                "index": index,
                "start_offset": offset,
                "end_offset": end,
                "text": line,
            })
        offset = end + 1  # 计入换行符
    return blocks
