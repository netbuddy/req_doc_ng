"""精确预览适配器：候选/基线 docx → PDF（LibreOffice 无头转换）。

用途：docx-preview（浏览器 HTML 近似）无法与真实 docx 的分页/版式一致，页数也不可信。
精确预览走真实排版引擎（LibreOffice soffice --headless --convert-to pdf），得到确定性分页与版式，
再由浏览器原生 PDF 查看器呈现正确页数。渲染是 LibreOffice 的，对多数文档接近 Word 但非逐像素等同。

只做格式转换，不写任何内部仓储；soffice 缺失抛 PdfRenderUnavailable，转换失败抛 PdfRenderError。
不把异常原文/敏感路径写入返回体或日志（遵守 AGENTS.md 硬规则 8），错误码走结构化日志。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.adapters.tool_probe import probe_tool_version
from app.config import settings
from app.log import log_event

_COMPONENT = "docx_to_pdf"


class PdfRenderUnavailable(RuntimeError):
    """服务端未安装 LibreOffice（soffice/libreoffice 不可用）：精确预览暂不可用。"""


class PdfRenderError(RuntimeError):
    """docx→PDF 转换失败（超时/进程错误/未产出）。"""


def find_soffice() -> str | None:
    """定位 LibreOffice 可执行文件：优先配置，其次 PATH 上的 soffice/libreoffice。"""
    if settings.soffice_path:
        return settings.soffice_path if Path(settings.soffice_path).exists() else None
    return shutil.which("soffice") or shutil.which("libreoffice")


def pdf_render_available() -> bool:
    return find_soffice() is not None


def _isolated_env(tmp: str) -> dict[str, str]:
    """soffice 专用的最小 env 白名单：独立 HOME 保证并发安全，固定 locale 规避语言检测失败。"""
    return {"HOME": tmp, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"}


def _user_installation(tmp: str) -> str:
    """独立的 LibreOffice 用户配置目录参数：起 soffice 的每条路径都要传，不碰服务账号默认 profile。"""
    return f"-env:UserInstallation=file://{tmp}/profile"


def soffice_version(soffice: str) -> str | None:
    """取 LibreOffice 版本串（`soffice --version`）；取不到返回 None。

    只供设置页的就绪清单显示用：**不做任何转换、不写文件、不出网**。版本取不到不代表不可用，
    可用与否只由 find_soffice() 的定位结果决定（与转换时的判据同源）。
    与 convert_docx_to_pdf 用同一套隔离（临时 HOME + 独立 UserInstallation，用后即弃）：
    同一个二进制在本模块只有一种起法，探测不该比转换少一道隔离。
    """
    with tempfile.TemporaryDirectory(prefix="lo-ver-") as tmp:
        return probe_tool_version(
            [soffice, _user_installation(tmp), "--version"],
            component=_COMPONENT, tool="soffice", env=_isolated_env(tmp),
        )


def convert_docx_to_pdf(docx_path: Path, out_dir: Path) -> Path:
    """把 docx 转为 PDF 落到 out_dir，返回 PDF 路径（文件名同 docx stem）。

    每次转换用独立的临时 HOME/UserInstallation 目录，避免并发实例争用同一 LibreOffice 用户配置。
    """
    soffice = find_soffice()
    if soffice is None:
        raise PdfRenderUnavailable("LibreOffice 不可用")
    if not docx_path.exists():
        raise PdfRenderError("源 docx 不存在")

    out_dir.mkdir(parents=True, exist_ok=True)
    expected = out_dir / f"{docx_path.stem}.pdf"
    # 独立临时 profile/HOME：并发安全 + 规避 "user interface language cannot be determined"。
    with tempfile.TemporaryDirectory(prefix="lo-pdf-") as tmp:
        cmd = [
            soffice, "--headless", "--nologo", "--nofirststartwizard",
            _user_installation(tmp),
            "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path),
        ]
        try:
            proc = subprocess.run(
                cmd, env=_isolated_env(tmp), capture_output=True,
                timeout=settings.pdf_render_timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            log_event(_COMPONENT, "convert.timeout", level="ERROR", ok=False,
                      docx=docx_path.name)
            raise PdfRenderError("转换超时") from exc

    if proc.returncode != 0 or not expected.exists():
        log_event(_COMPONENT, "convert.failed", level="ERROR", ok=False,
                  docx=docx_path.name, returncode=proc.returncode)
        raise PdfRenderError("转换失败")

    log_event(_COMPONENT, "convert.ok", ok=True, docx=docx_path.name)
    return expected
