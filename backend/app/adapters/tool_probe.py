"""本地工具版本探测：跑 `--version` 级命令取一行版本串，仅供设置页的就绪清单显示。

docx_to_pdf（LibreOffice）与 diagram_render（mermaid-cli / PlantUML）两个适配器共用这一份实现，
避免两处逐字复制后探测口径与日志分叉。

探测零副作用：不发起任何转换/渲染、不写业务文件、不出网。版本取不到不代表工具不可用——
可用与否只由各适配器自己的定位结果决定（find_soffice / resolve_tools）。
失败分支只记工具标识与错误码，不记路径（遵守 AGENTS.md 硬规则 8）。
"""
from __future__ import annotations

import subprocess

from app.log import log_event

# 版本探测的单次超时秒数：远小于转换/渲染超时，探不到就当版本未知。
VERSION_PROBE_TIMEOUT = 10.0


def probe_tool_version(
    cmd: list[str],
    *,
    component: str,
    tool: str,
    env: dict[str, str] | None = None,
) -> str | None:
    """跑 cmd 取首个非空行作为版本串；起不来/超时/非零退出/无版本行一律返回 None 并记一行 WARN。

    component 传调用方适配器的组件名，tool 传工具标识（soffice / mmdc / plantuml），
    好让「版本未知」这一个布尔值背后的三种原因（超时、进程报错、输出里没有版本行）事后能分开。
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, env=env, timeout=VERSION_PROBE_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        log_event(component, "version.probe.timeout", level="WARN", ok=False,
                  tool=tool, error_code="TimeoutExpired")
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        log_event(component, "version.probe.failed", level="WARN", ok=False,
                  tool=tool, error_code=type(exc).__name__)
        return None
    if proc.returncode != 0:
        log_event(component, "version.probe.failed", level="WARN", ok=False,
                  tool=tool, error_code="non_zero_exit", returncode=proc.returncode)
        return None
    # java 一类工具把版本打到 stderr：两个流各扫一遍，先 stdout 后 stderr。
    # 不能写成 `stdout or stderr`——stdout 只要有内容（哪怕只是一个换行符）就再也读不到 stderr，
    # 而「版本在 stderr 且 stdout 非空」恰是这段兜底本来要处理的那种工具。
    for stream in (proc.stdout, proc.stderr):
        for line in stream.decode("utf-8", errors="replace").splitlines():
            if line.strip():
                return line.strip()
    log_event(component, "version.probe.blank", level="WARN", ok=False,
              tool=tool, error_code="no_version_line")
    return None
