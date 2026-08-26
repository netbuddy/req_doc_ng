"""docx 嵌入 SVG 矢量图（AppImage 单机模式方案 §3.2 第 6 项）。

Word 2016 及以后能直接显示 SVG；文件格式（OOXML）规定图片主体仍是一张位图（PNG），
SVG 以 svgBlip 扩展挂在同一张图上，旧版 Word 读位图、新版 Word 读 SVG。
python-docx 不支持 SVG，本模块用它加 PNG，再手写 XML 补上 SVG 关系；PNG 用 resvg 从 SVG 转出，
不依赖浏览器与 cairo 之类系统库。只做格式转换，不写任何内部仓储。
"""
from __future__ import annotations

import re
from io import BytesIO

import resvg_py
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches

# Office 为 svgBlip 扩展规定的固定标识（微软 SVG 扩展规范）。
_SVG_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"
_ASVG_NS = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# 备用 PNG 的放大倍数：2 倍像素，在只认位图的阅读器里也不发糊。
PNG_ZOOM = 2.0

_LEN_ATTR = re.compile(r'\b(width|height)="([0-9.]+)(px|pt)?"')
_VIEWBOX = re.compile(r'viewBox="\s*[-0-9.]+\s+[-0-9.]+\s+([0-9.]+)\s+([0-9.]+)\s*"')


def svg_size_px(svg: bytes) -> tuple[float, float]:
    """读 SVG 根元素的 width/height（px 或 pt）得到 CSS 像素尺寸；没有则退 viewBox；都没有返回 (0, 0)。"""
    head = svg[:2048].decode("utf-8", errors="ignore")
    root_end = head.find(">")
    root = head[: root_end + 1] if root_end > 0 else head
    found: dict[str, float] = {}
    for name, value, unit in _LEN_ATTR.findall(root):
        px = float(value) * (96.0 / 72.0 if unit == "pt" else 1.0)
        found[name] = px
    if "width" in found and "height" in found:
        return found["width"], found["height"]
    m = _VIEWBOX.search(root)
    if m:
        return float(m.group(1)), float(m.group(2))
    return 0.0, 0.0


def svg_to_png(svg: bytes, zoom: float = PNG_ZOOM) -> bytes:
    """SVG → PNG 字节（白底）。渲染失败由 resvg 抛异常，调用方决定降级。"""
    return bytes(resvg_py.svg_to_bytes(svg_string=svg.decode("utf-8"), zoom=zoom, background="#ffffff"))


def add_svg_picture(paragraph, svg: bytes, width_inches: float) -> None:
    """在段落末尾加一张图：位图主体为 resvg 转出的 PNG，同一张图挂 SVG 扩展。"""
    png = svg_to_png(svg)
    run = paragraph.add_run()
    shape = run.add_picture(BytesIO(png), width=Inches(width_inches))

    doc_part = paragraph.part
    package = doc_part.package
    partname = package.next_partname("/word/media/diagram%d.svg")
    svg_part = Part(PackURI(str(partname)), "image/svg+xml", svg, package)
    rid = doc_part.relate_to(svg_part, RT.IMAGE)

    blip = shape._inline.find(".//" + qn("a:blip"))
    ext_lst = OxmlElement("a:extLst")
    ext = OxmlElement("a:ext")
    ext.set("uri", _SVG_EXT_URI)
    svg_blip = ext.makeelement("{%s}svgBlip" % _ASVG_NS, {"{%s}embed" % _R_NS: rid})
    ext.append(svg_blip)
    ext_lst.append(ext)
    blip.append(ext_lst)
