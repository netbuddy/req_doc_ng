"""图形围栏 → SVG 文件（diagram_assets）：围栏计序、替换、校验与失败保留。"""
from __future__ import annotations

import pytest

from app.adapters import diagram_assets
from app.adapters.diagram_assets import build_diagram_assets, iter_fences, validate_svg

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10px" height="10px"/>'
MD = (
    "# 标题\n\n"
    "```python\nprint(1)\n```\n\n"
    "```mermaid\nflowchart LR\n A --> B\n```\n\n"
    "正文。\n\n"
    "```plantuml\n@startuml\nA -> B\n@enduml\n```\n"
)


def test_iter_fences_counts_every_fence_in_order():
    fences = iter_fences(MD)
    assert [(f.index, f.lang) for f in fences] == [(0, "python"), (1, "mermaid"), (2, "plantuml")]
    assert fences[1].source == "flowchart LR\n A --> B"
    assert (fences[1].start_line, fences[1].end_line) == (6, 9)


def test_iter_fences_unclosed_fence_runs_to_end():
    fences = iter_fences("```mermaid\ngraph TD\n A")
    assert len(fences) == 1 and fences[0].end_line == 2


def test_build_replaces_mermaid_with_prerendered_and_plantuml_with_java(monkeypatch):
    monkeypatch.setattr(diagram_assets, "render_to_svg", lambda src, fmt: b"<svg>plantuml</svg>")
    result = build_diagram_assets(MD, {1: SVG})
    assert result.failures == []
    assert list(result.assets) == ["assets/diagram-1.svg", "assets/diagram-2.svg"]
    assert result.assets["assets/diagram-1.svg"] == SVG
    assert "![图 1](assets/diagram-1.svg)" in result.markdown
    assert "![图 2](assets/diagram-2.svg)" in result.markdown
    assert "flowchart LR" not in result.markdown and "@startuml" not in result.markdown
    assert "```python\nprint(1)\n```" in result.markdown  # 非图形围栏原样保留
    assert result.markdown.endswith("\n")


def test_build_keeps_source_and_reports_when_prerender_missing_or_invalid(monkeypatch):
    monkeypatch.setattr(diagram_assets, "render_to_svg", lambda src, fmt: b"<svg/>")
    missing = build_diagram_assets(MD, {})
    assert "flowchart LR" in missing.markdown
    assert len(missing.failures) == 1 and "第 2 个围栏（mermaid）" in missing.failures[0]
    assert list(missing.assets) == ["assets/diagram-1.svg"]  # plantuml 仍成图，编号从 1 起

    bad = build_diagram_assets(MD, {1: b"<svg><script>alert(1)</script></svg>"})
    assert "flowchart LR" in bad.markdown and "脚本" in bad.failures[0]


def test_build_reports_plantuml_failures(monkeypatch):
    def _boom(src, fmt):
        raise diagram_assets.DiagramRenderError("bad")

    monkeypatch.setattr(diagram_assets, "render_to_svg", _boom)
    result = build_diagram_assets(MD, {1: SVG})
    assert "@startuml" in result.markdown
    assert any("plantuml" in f for f in result.failures)


@pytest.mark.parametrize("data,ok", [
    (SVG, True),
    (b"  <?xml version='1.0'?><svg/>", True),
    (b"", False),
    (b"<html></html>", False),
    (b"<svg><script>x</script></svg>", False),
    (b"<svg/>" + b"x" * diagram_assets.MAX_SVG_BYTES, False),
])
def test_validate_svg(data, ok):
    assert (validate_svg(data) is None) is ok
