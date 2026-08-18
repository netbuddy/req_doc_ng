"""受控图表表达校验规则（SCN-004-P01-N10）—— 纯函数，无 IO。

边界：只校验「源码格式受控 ∧ 图表类型与源码匹配」；不判断图文一致性（归 P02 AI 核对），
不判断来源准入（归图表协同服务）。校验失败返回原因清单（中文，用户可见停靠原因）。
"""
from __future__ import annotations

import re

from app.domain.enums import ChartFormat, ChartKind, ChartType

# 表达方式 × 图表类型 受控矩阵（SCN-004 §4.2：类型与表达方式必须匹配）
FORMAT_TYPE_MATRIX: dict[ChartFormat, frozenset[ChartType]] = {
    ChartFormat.MERMAID: frozenset({
        ChartType.FLOWCHART, ChartType.STATE_DIAGRAM,
        ChartType.RELATION_DIAGRAM, ChartType.SEQUENCE_DIAGRAM,
    }),
    ChartFormat.PLANTUML: frozenset({
        ChartType.FLOWCHART, ChartType.STATE_DIAGRAM,
        ChartType.RELATION_DIAGRAM, ChartType.SEQUENCE_DIAGRAM,
    }),
    ChartFormat.MARKDOWN_TABLE: frozenset({
        ChartType.DECISION_TABLE, ChartType.COMPARISON_TABLE,
    }),
}

# 图表类型 → chart_kind 派生（枚举字典：表格/图形/UML）
TYPE_KIND_MAP: dict[ChartType, ChartKind] = {
    ChartType.FLOWCHART: ChartKind.GRAPHIC,
    ChartType.STATE_DIAGRAM: ChartKind.UML,
    ChartType.RELATION_DIAGRAM: ChartKind.GRAPHIC,
    ChartType.SEQUENCE_DIAGRAM: ChartKind.UML,
    ChartType.DECISION_TABLE: ChartKind.TABLE,
    ChartType.COMPARISON_TABLE: ChartKind.TABLE,
}

# mermaid 首个有效行头关键字 × 图表类型
_MERMAID_HEADERS: dict[ChartType, tuple[str, ...]] = {
    ChartType.FLOWCHART: ("flowchart", "graph"),
    ChartType.STATE_DIAGRAM: ("stateDiagram-v2", "stateDiagram"),
    ChartType.RELATION_DIAGRAM: ("erDiagram", "classDiagram"),
    ChartType.SEQUENCE_DIAGRAM: ("sequenceDiagram",),
}

# 渲染预览能力由 format 派生（不落库；PlantUML 本迭代不可预览，SCN-004 §4.5 行8 裁定）
PREVIEWABLE_FORMATS: frozenset[ChartFormat] = frozenset({
    ChartFormat.MERMAID, ChartFormat.MARKDOWN_TABLE,
})


def preview_capability(format_: ChartFormat) -> str:
    return "renderable" if format_ in PREVIEWABLE_FORMATS else "not_previewable"


def _effective_lines(source_code: str) -> list[str]:
    """去空行与 %% 注释行后的有效行。"""
    lines = []
    for raw in source_code.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        lines.append(line)
    return lines


def _validate_mermaid(chart_type: ChartType, source_code: str) -> list[str]:
    lines = _effective_lines(source_code)
    if not lines:
        return ["Mermaid 源码为空，不构成受控表达"]
    headers = _MERMAID_HEADERS.get(chart_type, ())
    first = lines[0]
    if not any(first == h or first.startswith(h + " ") for h in headers):
        expect = " / ".join(headers)
        return [f"Mermaid 首行 “{first[:40]}” 与图表类型不匹配（期望 {expect} 开头）"]
    if len(lines) < 2:
        return ["Mermaid 源码只有声明行，缺少图表内容"]
    return []


def _validate_plantuml(source_code: str) -> list[str]:
    lines = _effective_lines(source_code)
    if not lines:
        return ["PlantUML 源码为空，不构成受控表达"]
    if not lines[0].startswith("@startuml"):
        return ["PlantUML 源码必须以 @startuml 开始"]
    if not lines[-1].startswith("@enduml"):
        return ["PlantUML 源码必须以 @enduml 结束"]
    if len(lines) < 3:
        return ["PlantUML 源码缺少图表内容（@startuml/@enduml 之间为空）"]
    return []


_TABLE_SEPARATOR = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+$")


def _validate_markdown_table(source_code: str) -> list[str]:
    lines = _effective_lines(source_code)
    if len(lines) < 3:
        return ["Markdown 表格至少需要表头、分隔行和一行内容"]
    header, separator = lines[0], lines[1]
    if not (header.startswith("|") and header.endswith("|")):
        return ["Markdown 表格表头行必须以 | 包裹"]
    if not _TABLE_SEPARATOR.match(separator):
        return ["Markdown 表格第二行必须是 |---| 形式的分隔行"]
    columns = header.count("|") - 1
    if separator.count("|") - 1 != columns:
        return ["Markdown 表格分隔行列数与表头不一致"]
    for i, row in enumerate(lines[2:], start=3):
        if not (row.startswith("|") and row.endswith("|")):
            return [f"Markdown 表格第 {i} 行不是受控表格行"]
        if row.count("|") - 1 != columns:
            return [f"Markdown 表格第 {i} 行列数与表头不一致"]
    return []


def validate_controlled_source(
    format_: ChartFormat, chart_type: ChartType, source_code: str,
) -> list[str]:
    """受控表达校验；返回不可应用原因清单（空 = 通过）。"""
    errors: list[str] = []
    allowed = FORMAT_TYPE_MATRIX.get(format_, frozenset())
    if chart_type not in allowed:
        errors.append(
            f"表达方式 {format_.value} 不支持图表类型 {chart_type.value}"
        )
        return errors
    if format_ is ChartFormat.MERMAID:
        errors.extend(_validate_mermaid(chart_type, source_code))
    elif format_ is ChartFormat.PLANTUML:
        errors.extend(_validate_plantuml(source_code))
    else:
        errors.extend(_validate_markdown_table(source_code))
    return errors
