"""知识项名称规范化（domain 单一来源，显式导出）。

登记归并键（03 §2.1）、支撑依据边名称匹配（06 A.2）、条目侧引用推荐（10 §1.2）、
文档知识表内容切分（07 §1.2）共用同一函数族——只在此定义，不在服务/前端复制。
"""
from __future__ import annotations

import unicodedata

# 定义/描述分隔标记：取其前段为「名」（术语名 / 角色名 / 系统名）。
_NAME_CUTS = ("\n", "：", ":", "，", ",", "。", "、", "（", "(", "是指", "指的是", "指")


def normalize_element_name(content: str) -> str:
    """归并/匹配用名称规范化：NFKC(全半角统一) + 去空白 + 取主词(首行/分隔前段) + 小写。

    例：「履约单是指从下单到出库…」→「履约单」；「  WMS 系统 」→「wms 系统」。
    切不出明确名时返回规范化后的整串（宁缺勿滥，下游按精确相等匹配）。
    """
    s = unicodedata.normalize("NFKC", content or "").strip()
    cut_at = len(s)
    for marker in _NAME_CUTS:
        idx = s.find(marker)
        if 0 < idx < cut_at:
            cut_at = idx
    return s[:cut_at].strip().lower()


def normalize_text(text: str) -> str:
    """匹配用文本规范化（不取主词）：NFKC + 小写 + 去空白。作名称子串匹配的 haystack。"""
    return unicodedata.normalize("NFKC", text or "").strip().lower()


def split_name_definition(content: str) -> tuple[str, str]:
    """文档知识表投影切分（07 §1.2）：按同一 `_NAME_CUTS` 首个分隔取（名, 余文）。

    与 `normalize_element_name` 共用分隔标记族，但**不做 NFKC/小写**——投影单元格须与
    `LDM-005.content` 逐字一致（AC-P5-01）。切不出分隔时返回（整串, ""）→ 余文列置 "—"。
    例：「履约单是指从下单到出库…」→（"履约单", "从下单到出库…"）；「订单管理员」→（"订单管理员", ""）。
    """
    s = (content or "").strip()
    cut_at, cut_marker = len(s), ""
    for marker in _NAME_CUTS:
        idx = s.find(marker)
        if 0 < idx < cut_at:
            cut_at, cut_marker = idx, marker
    name = s[:cut_at].strip()
    rest = s[cut_at + len(cut_marker):].strip() if cut_marker else ""
    return (name or s, rest)


def material_default_name(raw_text: str, imported_at_label: str) -> str:
    """材料默认名称（2026-08-07 命名三规则之三：粘贴文本取正文首行截断）。

    取首个非空行，NFKC 规范化去首尾空白，超 20 字截断加省略号；
    全文无可用文字时兜底为「粘贴材料-<导入时刻>」。文件导入的默认名＝文件名，
    由导入口在调用前自行给定名称，不经本函数。
    """
    for line in (raw_text or "").splitlines():
        s = unicodedata.normalize("NFKC", line).strip()
        if s:
            return s[:20] + ("…" if len(s) > 20 else "")
    return f"粘贴材料-{imported_at_label}"
