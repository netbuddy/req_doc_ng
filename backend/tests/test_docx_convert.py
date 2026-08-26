"""docx 转换器·图形围栏栅格化单测（SCN-005 图形渲染修复）。

覆盖：mermaid/plantuml 围栏 → 内嵌图片；普通代码围栏保持等宽源码；
渲染不可用/失败时降级为源码块（绝不因单图失败丢内容）。
"""
from pathlib import Path

from docx import Document as DocxDocument

from app.adapters import docx_convert
from app.adapters.diagram_render import DiagramRenderUnavailable

_BINDING = {"body_size_pt": 12, "heading_sizes_pt": {"1": 16}}
_META = {"title": "需求规格说明", "version": "V1.0"}


def _convert(md: str, tmp_path: Path):
    out = docx_convert.convert_markdown_to_docx(md, tmp_path / "d.docx", _BINDING, _META)
    return DocxDocument(str(out))


def _convert_with_assets(md: str, tmp_path: Path, prerendered: dict[int, bytes] | None = None):
    """走真实发布流程的图形处理：围栏 → SVG 资产表 → docx。"""
    from app.adapters.diagram_assets import build_diagram_assets

    assets = build_diagram_assets(md, prerendered or {})
    out = docx_convert.convert_markdown_to_docx(
        assets.markdown, tmp_path / "d.docx", _BINDING, _META, assets=assets.assets,
    )
    return DocxDocument(str(out)), assets


def test_plantuml_and_plain_code_coexist(tmp_path):
    md = (
        "# 1 概述\n\n正文一段。\n\n"
        "```plantuml\n@startuml\nAlice -> Bob: 下单\n@enduml\n```\n\n"
        "```python\nprint('hello')\n```\n"
    )
    doc, assets = _convert_with_assets(md, tmp_path)
    assert not assets.failures
    texts = [p.text for p in doc.paragraphs]
    assert doc.inline_shapes and any("PICTURE" in str(s.type) for s in doc.inline_shapes)
    assert not any("startuml" in t for t in texts)  # 图形源码不外泄
    assert any("print('hello')" in t for t in texts)  # 普通代码仍作源码保留


def test_mermaid_without_prerender_falls_back_to_source(tmp_path):
    """mermaid 没有浏览器预渲染结果：不产图、源码保留、失败清单有记录。"""
    md = "# 1\n\n```mermaid\nflowchart LR\n A --> B\n```\n"
    doc, assets = _convert_with_assets(md, tmp_path)
    texts = [p.text for p in doc.paragraphs]
    assert not doc.inline_shapes
    assert any("flowchart LR" in t for t in texts)
    assert assets.failures and "mermaid" in assets.failures[0]


def test_plantuml_render_failure_falls_back_to_source(tmp_path, monkeypatch):
    from app.adapters import diagram_assets

    def _boom(source: str, fmt: str):
        raise DiagramRenderUnavailable("tool missing")

    monkeypatch.setattr(diagram_assets, "render_to_svg", _boom)
    md = "# 1\n\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n"
    doc, assets = _convert_with_assets(md, tmp_path)
    assert not doc.inline_shapes
    assert any("A -> B" in p.text for p in doc.paragraphs)
    assert assets.failures


def test_unknown_image_reference_becomes_caption(tmp_path):
    """图片引用指向资产表之外：以说明文字替代，不报错不丢信息。"""
    md = "# 1\n\n![外部图](http://example.com/a.png)\n"
    doc = _convert(md, tmp_path)
    assert not doc.inline_shapes
    assert any("[图片：外部图]" in p.text for p in doc.paragraphs)


def test_list_form_heading_sizes_convert_without_error(tmp_path):
    """定制器登记的模板 heading_sizes_pt 是列表形态（[16,14,13]），转换器须按位次归一。

    2026-07-26 生产故障：只认字典形态时，全部模板定制器产出的模板在导出一步炸
    AttributeError，被兜底 except 吞成「转换发生未预期错误」。列表按位次映射 1..n 级。
    """
    binding = {"body_size_pt": 12, "heading_sizes_pt": [16, 14, 13]}
    md = "# 1 概述\n\n## 1.1 目标\n\n### 细则\n\n正文。\n"
    out = docx_convert.convert_markdown_to_docx(md, tmp_path / "d.docx", binding, _META)
    doc = DocxDocument(str(out))
    headings = [p for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert len(headings) == 3
    # 位次映射生效：一级 16pt、二级 14pt、三级 13pt
    got = [h.runs[0].font.size.pt for h in headings]
    assert got == [16.0, 14.0, 13.0]


def test_list_form_heading_sizes_deeper_level_falls_back(tmp_path):
    """列表只给三级时，第四级标题回落默认 13pt，不越界取值。"""
    binding = {"body_size_pt": 12, "heading_sizes_pt": [16, 14]}
    md = "# 1\n\n### 深层\n\n正文。\n"
    out = docx_convert.convert_markdown_to_docx(md, tmp_path / "d2.docx", binding, _META)
    doc = DocxDocument(str(out))
    h3 = [p for p in doc.paragraphs if p.style.name == "Heading 3"]
    assert h3 and h3[0].runs[0].font.size.pt == 13.0


def test_plantuml_embeds_svg_with_png_fallback(tmp_path, monkeypatch):
    """PlantUML 图以 SVG 嵌入 docx：包内有 image/svg+xml 部件，图片的 blip 挂 svgBlip 扩展并指向它；
    位图主体（PNG 备用）仍存在。子进程用替身，不依赖本机 Java。"""
    import zipfile

    from app.adapters import diagram_render

    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="200px" height="80px">'
           b'<rect width="200" height="80" fill="#fff" stroke="#000"/></svg>')
    monkeypatch.setattr(diagram_render, "resolve_tools",
                        lambda: {"mmdc": None, "java": "/fake/java", "plantuml_jar": "/fake/p.jar"})

    def _run(cmd, **kwargs):
        import subprocess
        assert "-tsvg" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=svg, stderr=b"")

    monkeypatch.setattr(diagram_render.subprocess, "run", _run)
    md = "# 1\n\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n"
    doc, assets = _convert_with_assets(md, tmp_path)
    assert list(assets.assets) == ["assets/diagram-1.svg"]
    assert "![图 1](assets/diagram-1.svg)" in assets.markdown
    assert doc.inline_shapes
    out = next(tmp_path.rglob("*.docx"))
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert any(n.endswith(".svg") for n in names), names
        assert any(n.endswith(".png") for n in names), names
        document_xml = z.read("word/document.xml").decode()
        assert "svgBlip" in document_xml
        assert "96DAC541-7B7A-43D3-8B79-37D633B846F1" in document_xml
        assert "image/svg+xml" in z.read("[Content_Types].xml").decode()


def test_svg_size_px_parses_px_pt_and_viewbox():
    from app.adapters.docx_svg import svg_size_px

    assert svg_size_px(b'<svg width="300px" height="100px"/>') == (300.0, 100.0)
    assert svg_size_px(b'<svg width="72pt" height="36pt"/>') == (96.0, 48.0)
    assert svg_size_px(b'<svg viewBox="0 0 640 480"/>') == (640.0, 480.0)
    assert svg_size_px(b'<svg/>') == (0.0, 0.0)
