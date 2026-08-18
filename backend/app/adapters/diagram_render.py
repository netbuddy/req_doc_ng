"""图形源码本地栅格化适配器：mermaid / plantuml 源码 → PNG 字节。

全部在本机渲染，运行时不出网、不把需求内容送第三方（数据不出域）：
- mermaid：@mermaid-js/mermaid-cli（mmdc）+ 系统 google-chrome（--no-sandbox，见 tools/puppeteer.json）。
- plantuml：java -jar plantuml.jar -tpng -pipe（graphviz dot 供关系类图使用）。

只做格式转换，不写任何内部仓储。工具缺失抛 DiagramRenderUnavailable，渲染失败抛 DiagramRenderError；
调用方（docx 转换 / 预览端点）应捕获后降级为源码块，绝不因单张图失败而丢内容。
不把源码原文写入日志（遵守 AGENTS.md 硬规则 8），只记 format / 字节数 / returncode 等稳定字段。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.adapters.tool_probe import probe_tool_version
from app.config import settings
from app.log import log_event

_COMPONENT = "diagram_render"

# 预览端点与 docx 均以此为准：可本地栅格化的图形围栏语言。
RENDERABLE_FORMATS = frozenset({"mermaid", "plantuml"})


class DiagramRenderUnavailable(RuntimeError):
    """所需本地渲染工具不可用（mmdc / java / plantuml.jar 缺失）。"""


class DiagramRenderError(RuntimeError):
    """图形栅格化失败（源码非法 / 超时 / 进程错误 / 未产出）。"""


def _resolve_mmdc() -> str | None:
    """定位 mmdc：优先配置，其次 PATH。"""
    if settings.mmdc_path:
        return settings.mmdc_path if Path(settings.mmdc_path).exists() else None
    return shutil.which("mmdc")


def _resolve_java() -> str | None:
    if settings.java_path:
        return settings.java_path if Path(settings.java_path).exists() else None
    return shutil.which("java")


def _subprocess_env(tool_path: str) -> dict[str, str]:
    """继承环境并把工具目录并入 PATH：mmdc 是 node 脚本，需能找到 node 解释器。"""
    import os

    env = os.environ.copy()
    tool_dir = str(Path(tool_path).resolve().parent)
    env["PATH"] = tool_dir + os.pathsep + env.get("PATH", "")
    return env


def resolve_tools() -> dict[str, str | None]:
    """本地图形渲染工具链的定位结果：{mmdc, java, plantuml_jar} → 路径，None＝未找到。

    本模块的渲染函数（`_render_mermaid`/`_render_plantuml`）与设置页的就绪清单都从这里取定位结果，
    因此清单结论与真实渲染能否跑通同源；本函数之外任何地方都不得再写一份路径解析。
    """
    jar = settings.plantuml_jar_path
    return {
        "mmdc": _resolve_mmdc(),
        "java": _resolve_java(),
        "plantuml_jar": jar if jar and Path(jar).exists() else None,
    }


def mmdc_version(mmdc: str) -> str | None:
    """取 mermaid-cli 版本串（`mmdc --version`）；取不到返回 None，不渲染任何图形。

    沿用 `_subprocess_env`：mmdc 是 node 脚本，PATH 里得能找到同目录的 node 解释器。
    """
    return probe_tool_version([mmdc, "--version"], component=_COMPONENT, tool="mmdc",
                              env=_subprocess_env(mmdc))


def plantuml_version(java: str, jar: str) -> str | None:
    """取 PlantUML 版本串（`java -jar plantuml.jar -version`）；取不到返回 None，不渲染任何图形。"""
    return probe_tool_version([java, "-Djava.awt.headless=true", "-jar", jar, "-version"],
                              component=_COMPONENT, tool="plantuml")


def _render_mermaid(source: str) -> bytes:
    mmdc = resolve_tools()["mmdc"]
    if mmdc is None:
        raise DiagramRenderUnavailable("mmdc 不可用（未安装 @mermaid-js/mermaid-cli）")
    puppeteer_cfg = settings.puppeteer_config_path
    with tempfile.TemporaryDirectory(prefix="mmd-") as tmp:
        in_path = Path(tmp) / "d.mmd"
        out_path = Path(tmp) / "d.png"
        in_path.write_text(source, encoding="utf-8")
        cmd = [mmdc, "-i", str(in_path), "-o", str(out_path), "-b", "white", "-s", "2"]
        if Path(puppeteer_cfg).exists():
            cmd += ["-p", puppeteer_cfg]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, env=_subprocess_env(mmdc),
                timeout=settings.diagram_render_timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log_event(_COMPONENT, "mermaid.timeout", level="ERROR", ok=False)
            raise DiagramRenderError("mermaid 渲染超时") from exc
        if proc.returncode != 0 or not out_path.exists():
            log_event(_COMPONENT, "mermaid.failed", level="ERROR", ok=False,
                      returncode=proc.returncode)
            raise DiagramRenderError("mermaid 渲染失败")
        png = out_path.read_bytes()
    log_event(_COMPONENT, "mermaid.ok", ok=True, bytes=len(png))
    return png


def _render_plantuml(source: str) -> bytes:
    # 与就绪清单同走 resolve_tools()：jar 的在位判断只此一份，否则两处会漂移
    # （曾经的分叉：这里对空串 jar 判 Path('').exists() 为真，清单侧判缺失）。
    tools = resolve_tools()
    java, jar = tools["java"], tools["plantuml_jar"]
    if java is None:
        raise DiagramRenderUnavailable("java 不可用")
    if jar is None:
        raise DiagramRenderUnavailable("plantuml.jar 未就绪")
    cmd = [java, "-Djava.awt.headless=true", "-jar", jar, "-tpng", "-pipe", "-charset", "UTF-8"]
    try:
        proc = subprocess.run(
            cmd, input=source.encode("utf-8"), capture_output=True,
            timeout=settings.diagram_render_timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log_event(_COMPONENT, "plantuml.timeout", level="ERROR", ok=False)
        raise DiagramRenderError("plantuml 渲染超时") from exc
    if proc.returncode != 0 or not proc.stdout:
        log_event(_COMPONENT, "plantuml.failed", level="ERROR", ok=False,
                  returncode=proc.returncode)
        raise DiagramRenderError("plantuml 渲染失败")
    log_event(_COMPONENT, "plantuml.ok", ok=True, bytes=len(proc.stdout))
    return proc.stdout


def render_to_png(source: str, fmt: str) -> bytes:
    """把图形源码栅格化为 PNG 字节。fmt ∈ {mermaid, plantuml}；空源码即失败。"""
    if not source.strip():
        raise DiagramRenderError("图形源码为空")
    if fmt == "mermaid":
        return _render_mermaid(source)
    if fmt == "plantuml":
        return _render_plantuml(source)
    raise DiagramRenderError(f"不支持的图形格式：{fmt}")


def png_size(png: bytes) -> tuple[int, int]:
    """读 PNG IHDR 得到像素宽高（无需 Pillow）；非法头返回 (0, 0)。"""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return 0, 0
