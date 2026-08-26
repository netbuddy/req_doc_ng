"""图形渲染端点 SVG 输出（AppImage 单机模式方案 §3.2 第 1 项）。

plantuml 以 -tsvg 出 SVG；mermaid 的 SVG 由浏览器端渲染，服务器侧拒绝；默认 output 仍为 png。
子进程用 monkeypatch 替身，不依赖本机 Java。
"""
from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from app.adapters import diagram_render
from app.main import app


@pytest.fixture
def fake_plantuml(monkeypatch):
    """替换工具定位与子进程：记录 plantuml 命令行，按 -t 参数返回假字节。"""
    calls: list[list[str]] = []

    def _tools():
        return {"java": "/fake/java", "plantuml_jar": "/fake/plantuml.jar"}

    def _run(cmd, **kwargs):
        calls.append(cmd)
        out = b"<svg xmlns='http://www.w3.org/2000/svg'/>" if "-tsvg" in cmd else b"\x89PNG"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=b"")

    monkeypatch.setattr(diagram_render, "resolve_tools", _tools)
    monkeypatch.setattr(diagram_render.subprocess, "run", _run)
    return calls


def test_render_to_svg_plantuml_uses_tsvg(fake_plantuml):
    out = diagram_render.render_to_svg("@startuml\nA->B\n@enduml", "plantuml")
    assert out.startswith(b"<svg")
    assert "-tsvg" in fake_plantuml[0] and "-tpng" not in fake_plantuml[0]


def test_render_to_png_plantuml_still_uses_tpng(fake_plantuml):
    diagram_render.render_to_png("@startuml\nA->B\n@enduml", "plantuml")
    assert "-tpng" in fake_plantuml[0]


def test_render_to_svg_rejects_mermaid_and_empty():
    with pytest.raises(diagram_render.DiagramRenderError):
        diagram_render.render_to_svg("graph TD; A-->B", "mermaid")
    with pytest.raises(diagram_render.DiagramRenderError):
        diagram_render.render_to_svg("   ", "plantuml")


def test_endpoint_svg_output(fake_plantuml):
    client = TestClient(app)
    r = client.post("/api/diagrams/render",
                    json={"format": "plantuml", "source": "@startuml\nA->B\n@enduml", "output": "svg"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.content.startswith(b"<svg")


def test_endpoint_default_output_is_png(fake_plantuml):
    client = TestClient(app)
    r = client.post("/api/diagrams/render",
                    json={"format": "plantuml", "source": "@startuml\nA->B\n@enduml"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")


def test_endpoint_mermaid_is_422_for_both_outputs():
    """mermaid 由浏览器渲染：服务器端点对 png 与 svg 一律拒绝。"""
    client = TestClient(app)
    for output in ("svg", "png"):
        r = client.post("/api/diagrams/render",
                        json={"format": "mermaid", "source": "graph TD; A-->B", "output": output})
        assert r.status_code == 422


def test_endpoint_rejects_unknown_output():
    client = TestClient(app)
    r = client.post("/api/diagrams/render",
                    json={"format": "plantuml", "source": "x", "output": "pdf"})
    assert r.status_code == 422


def test_plantuml_uses_smetana_layout_when_dot_missing(fake_plantuml, monkeypatch):
    """没有 graphviz 的机器（AppImage 单机包）：自动加 -Playout=smetana，用纯 Java 布局。"""
    monkeypatch.setattr(diagram_render, "_resolve_dot", lambda: None)
    diagram_render.render_to_svg("@startuml\nclass A\n@enduml", "plantuml")
    assert "-Playout=smetana" in fake_plantuml[-1]
    monkeypatch.setattr(diagram_render, "_resolve_dot", lambda: "/usr/bin/dot")
    diagram_render.render_to_svg("@startuml\nclass A\n@enduml", "plantuml")
    assert "-Playout=smetana" not in fake_plantuml[-1]
