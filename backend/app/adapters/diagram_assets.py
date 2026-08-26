"""图形围栏 → SVG 文件（AppImage 单机模式方案 §3.2 第 4 项）。

发布产物里每张图是一个独立的 .svg 文件（裁定 D5），Markdown 用图片语法引用；docx 消费同一组文件。
- mermaid：SVG 由用户浏览器里的前端渲染后随发布请求提交（服务器不跑 JavaScript，也不装浏览器）；
- plantuml：后端调本机 Java 出 SVG（diagram_render.render_to_svg）。
任一图形失败：围栏原样保留，并记入失败清单——绝不因单图失败丢内容，也不静默。
只做格式转换，不写任何内部仓储。围栏解析规则与 docx_convert 保持一致（``` 起止、语言标签小写）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.adapters.diagram_render import DiagramRenderError, DiagramRenderUnavailable, render_to_svg

DIAGRAM_FORMATS = frozenset({"mermaid", "plantuml"})
ASSET_DIR = "assets"
# 单张预渲染 SVG 上限；超过视为异常输入拒收（浏览器端 mermaid 出图通常几十 KB）。
MAX_SVG_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Fence:
    index: int          # 围栏在正文中的序号（从 0 计，所有语言的围栏都计数）
    lang: str
    source: str
    start_line: int     # 开栏行号（含）
    end_line: int       # 闭栏行号（含）；未闭合时为最后一行


@dataclass
class DiagramAssetsResult:
    markdown: str
    assets: dict[str, bytes] = field(default_factory=dict)   # 相对路径 → SVG 字节，如 assets/diagram-1.svg
    failures: list[str] = field(default_factory=list)        # 人可读的失败说明，供导出结果展示


def iter_fences(markdown: str) -> list[Fence]:
    """列出正文中的全部围栏块。前端预渲染按同一规则计序号，后端据序号对号入座，不比对源码。"""
    fences: list[Fence] = []
    lines = markdown.splitlines()
    in_fence = False
    lang = ""
    start = 0
    buf: list[str] = []
    for no, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence, lang, start, buf = True, stripped[3:].strip().lower(), no, []
            else:
                fences.append(Fence(len(fences), lang, "\n".join(buf), start, no))
                in_fence = False
            continue
        if in_fence:
            buf.append(raw.rstrip())
    if in_fence:
        fences.append(Fence(len(fences), lang, "\n".join(buf), start, len(lines) - 1))
    return fences


def validate_svg(data: bytes) -> str | None:
    """校验浏览器提交的 SVG：返回 None 表示可用，否则返回拒收原因。不接受脚本，不接受超大文件。"""
    if not data or len(data) > MAX_SVG_BYTES:
        return "SVG 为空或超过大小上限"
    head = data.lstrip()[:512].lower()
    if not (head.startswith(b"<svg") or head.startswith(b"<?xml")):
        return "不是 SVG 文档"
    if b"<script" in data.lower():
        return "SVG 含脚本，拒收"
    return None


def build_diagram_assets(markdown: str, prerendered: dict[int, bytes]) -> DiagramAssetsResult:
    """把图形围栏替换为 SVG 文件引用。prerendered：围栏序号 → 浏览器预渲染的 mermaid SVG。"""
    lines = markdown.splitlines()
    result = DiagramAssetsResult(markdown=markdown)
    replacements: list[tuple[Fence, str]] = []
    figure_no = 0
    for fence in iter_fences(markdown):
        if fence.lang not in DIAGRAM_FORMATS:
            continue
        ordinal = fence.index + 1
        svg: bytes | None = None
        if fence.lang == "mermaid":
            svg = prerendered.get(fence.index)
            if svg is None:
                result.failures.append(f"第 {ordinal} 个围栏（mermaid）没有浏览器预渲染结果，已保留源码")
                continue
            reason = validate_svg(svg)
            if reason:
                result.failures.append(f"第 {ordinal} 个围栏（mermaid）预渲染结果无效：{reason}，已保留源码")
                continue
        else:
            try:
                svg = render_to_svg(fence.source, "plantuml")
            except DiagramRenderUnavailable:
                result.failures.append(f"第 {ordinal} 个围栏（plantuml）渲染工具不可用（缺 Java 或 plantuml.jar），已保留源码")
                continue
            except DiagramRenderError:
                result.failures.append(f"第 {ordinal} 个围栏（plantuml）渲染失败（源码可能有误），已保留源码")
                continue
        figure_no += 1
        rel = f"{ASSET_DIR}/diagram-{figure_no}.svg"
        result.assets[rel] = svg
        replacements.append((fence, f"![图 {figure_no}]({rel})"))
    for fence, ref in reversed(replacements):  # 从后往前替换，行号不漂移
        lines[fence.start_line:fence.end_line + 1] = [ref]
    result.markdown = "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")
    return result
