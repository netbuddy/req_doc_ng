"""图形源码本地渲染适配器：plantuml 源码 → PNG / SVG 字节。

全部在本机渲染，运行时不出网、不把需求内容送第三方（数据不出域）：
- plantuml：java -jar plantuml.jar -tpng|-tsvg -pipe（graphviz dot 供关系类图使用）。
- mermaid 不在本模块渲染：它是 JavaScript 库，由用户浏览器里的前端出 SVG，随发布请求提交
  （AppImage 单机模式方案 §3，裁定 D4：服务器侧不依赖浏览器；原 mmdc + Chrome 路径已退役）。

只做格式转换，不写任何内部仓储。工具缺失抛 DiagramRenderUnavailable，渲染失败抛 DiagramRenderError；
调用方（docx 转换 / 预览端点）应捕获后降级为源码块，绝不因单张图失败而丢内容。
不把源码原文写入日志（遵守 AGENTS.md 硬规则 8），只记 format / 字节数 / returncode 等稳定字段。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.adapters.tool_probe import probe_tool_version
from app.config import settings
from app.log import log_event

_COMPONENT = "diagram_render"

# 预览端点以此为准：后端可本地渲染的图形围栏语言（mermaid 由浏览器渲染，不在此列）。
RENDERABLE_FORMATS = frozenset({"plantuml"})


class DiagramRenderUnavailable(RuntimeError):
    """所需本地渲染工具不可用（java / plantuml.jar 缺失）。"""


class DiagramRenderError(RuntimeError):
    """图形栅格化失败（源码非法 / 超时 / 进程错误 / 未产出）。"""


def _resolve_java() -> str | None:
    if settings.java_path:
        return settings.java_path if Path(settings.java_path).exists() else None
    return shutil.which("java")


def _resolve_dot() -> str | None:
    """定位 graphviz 的 dot（类图/对象图排版用）。找不到时 PlantUML 改用内置纯 Java 布局引擎 Smetana，
    不需要任何外部程序（AppImage 单机包不带 graphviz，走的就是这条路）。"""
    return shutil.which("dot")


def resolve_tools() -> dict[str, str | None]:
    """本地图形渲染工具链的定位结果：{java, plantuml_jar} → 路径，None＝未找到。

    本模块的渲染函数（`_render_plantuml`）与设置页的就绪清单都从这里取定位结果，
    因此清单结论与真实渲染能否跑通同源；本函数之外任何地方都不得再写一份路径解析。
    """
    jar = settings.plantuml_jar_path
    return {
        "java": _resolve_java(),
        "plantuml_jar": jar if jar and Path(jar).exists() else None,
    }


def plantuml_version(java: str, jar: str) -> str | None:
    """取 PlantUML 版本串（`java -jar plantuml.jar -version`）；取不到返回 None，不渲染任何图形。"""
    return probe_tool_version([java, "-Djava.awt.headless=true", "-jar", jar, "-version"],
                              component=_COMPONENT, tool="plantuml")


def _render_plantuml(source: str, output: str = "png") -> bytes:
    """PlantUML 源码 → 图片字节。output ∈ {png, svg}，对应 plantuml 的 -tpng / -tsvg。"""
    # 与就绪清单同走 resolve_tools()：jar 的在位判断只此一份，否则两处会漂移
    # （曾经的分叉：这里对空串 jar 判 Path('').exists() 为真，清单侧判缺失）。
    tools = resolve_tools()
    java, jar = tools["java"], tools["plantuml_jar"]
    if java is None:
        raise DiagramRenderUnavailable("java 不可用")
    if jar is None:
        raise DiagramRenderUnavailable("plantuml.jar 未就绪")
    cmd = [java, "-Djava.awt.headless=true", "-jar", jar, f"-t{output}", "-pipe", "-charset", "UTF-8"]
    if _resolve_dot() is None:
        cmd.append("-Playout=smetana")
    try:
        proc = subprocess.run(
            cmd, input=source.encode("utf-8"), capture_output=True,
            timeout=settings.diagram_render_timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log_event(_COMPONENT, "plantuml.timeout", level="ERROR", ok=False, output=output)
        raise DiagramRenderError("plantuml 渲染超时") from exc
    if proc.returncode != 0 or not proc.stdout:
        log_event(_COMPONENT, "plantuml.failed", level="ERROR", ok=False,
                  returncode=proc.returncode, output=output)
        raise DiagramRenderError("plantuml 渲染失败")
    log_event(_COMPONENT, "plantuml.ok", ok=True, bytes=len(proc.stdout), output=output)
    return proc.stdout


def render_to_png(source: str, fmt: str) -> bytes:
    """把图形源码栅格化为 PNG 字节。只支持 plantuml；空源码即失败。"""
    if not source.strip():
        raise DiagramRenderError("图形源码为空")
    if fmt == "plantuml":
        return _render_plantuml(source)
    if fmt == "mermaid":
        raise DiagramRenderError("mermaid 由浏览器端渲染，服务器不提供")
    raise DiagramRenderError(f"不支持的图形格式：{fmt}")


def render_to_svg(source: str, fmt: str) -> bytes:
    """把图形源码渲染为 SVG 字节（UTF-8）。

    只支持 plantuml：mermaid 是 JavaScript 库，SVG 由用户浏览器里的前端代码渲染后随请求提交，
    服务器侧不再依赖浏览器（AppImage 单机模式方案 §3，2026-08-25 裁定 D4）；
    对 mermaid 调用本函数一律抛 DiagramRenderError。
    """
    if not source.strip():
        raise DiagramRenderError("图形源码为空")
    if fmt == "plantuml":
        return _render_plantuml(source, output="svg")
    if fmt == "mermaid":
        raise DiagramRenderError("mermaid 的 SVG 由浏览器端渲染，服务器不提供")
    raise DiagramRenderError(f"不支持的图形格式：{fmt}")
