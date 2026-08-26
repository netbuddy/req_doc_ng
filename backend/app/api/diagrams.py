"""图形渲染端点：mermaid/plantuml 源码 → PNG 或 SVG，供前端预览。

浏览器已能本地渲染 mermaid；plantuml 无浏览器端渲染器，走本端点用本机 plantuml.jar 出图。
output=svg 只支持 plantuml（mermaid 的 SVG 由浏览器端出，见 diagram_render.render_to_svg）。
本机渲染、运行时不出网、不把需求内容送第三方。项目无关（图形即源码→图片，不产治理事实）。
渲染工具缺失 → 503；源码非法/渲染失败 → 422：前端据此降级为源码块（绝不因单图失败丢内容）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.adapters.diagram_render import (
    RENDERABLE_FORMATS,
    DiagramRenderError,
    DiagramRenderUnavailable,
    render_to_png,
    render_to_svg,
)

router = APIRouter(tags=["diagram"])


class DiagramRenderRequest(BaseModel):
    format: str = Field(..., description="mermaid | plantuml")
    source: str
    output: Literal["png", "svg"] = Field("png", description="输出格式；svg 仅 plantuml 支持")


@router.post("/diagrams/render", operation_id="render_diagram_png")
def render_diagram(req: DiagramRenderRequest) -> Response:
    if req.format not in RENDERABLE_FORMATS:
        raise HTTPException(status_code=422, detail="unsupported diagram format")
    try:
        if req.output == "svg":
            return Response(content=render_to_svg(req.source, req.format), media_type="image/svg+xml")
        return Response(content=render_to_png(req.source, req.format), media_type="image/png")
    except DiagramRenderUnavailable as exc:
        raise HTTPException(status_code=503, detail="diagram renderer unavailable") from exc
    except DiagramRenderError as exc:
        raise HTTPException(status_code=422, detail="diagram render failed") from exc
