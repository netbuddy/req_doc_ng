"""精确预览适配器测试：docx→PDF（LibreOffice）。

以 mock 替代真实 soffice，保证 CI 无 LibreOffice 也能跑；覆盖：缺失→PdfRenderUnavailable、
进程失败/无产出→PdfRenderError、超时→PdfRenderError、成功→返回 PDF 路径。
"""
from __future__ import annotations

import subprocess

import pytest

from app.adapters import docx_to_pdf
from app.adapters.docx_to_pdf import (
    PdfRenderError,
    PdfRenderUnavailable,
    convert_docx_to_pdf,
)


def _make_docx(tmp_path):
    docx = tmp_path / "src.docx"
    docx.write_bytes(b"PK\x03\x04 fake docx")
    return docx


def test_unavailable_when_no_soffice(tmp_path, monkeypatch):
    monkeypatch.setattr(docx_to_pdf, "find_soffice", lambda: None)
    with pytest.raises(PdfRenderUnavailable):
        convert_docx_to_pdf(_make_docx(tmp_path), tmp_path)


def test_render_error_when_process_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(docx_to_pdf, "find_soffice", lambda: "/usr/bin/soffice")

    def fake_run(*_a, **_k):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(PdfRenderError):
        convert_docx_to_pdf(_make_docx(tmp_path), tmp_path)  # returncode!=0 且无产出


def test_render_error_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(docx_to_pdf, "find_soffice", lambda: "/usr/bin/soffice")

    def fake_run(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="soffice", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(PdfRenderError):
        convert_docx_to_pdf(_make_docx(tmp_path), tmp_path)


def test_success_returns_pdf_path(tmp_path, monkeypatch):
    monkeypatch.setattr(docx_to_pdf, "find_soffice", lambda: "/usr/bin/soffice")
    docx = _make_docx(tmp_path)

    def fake_run(cmd, *_a, **_k):
        # 模拟 soffice：在 outdir 产出同名 .pdf
        (tmp_path / f"{docx.stem}.pdf").write_bytes(b"%PDF-1.7 fake")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = convert_docx_to_pdf(docx, tmp_path)
    assert out == tmp_path / "src.pdf"
    assert out.exists()


# ---- 版本探测（就绪清单显示用）：与转换路径同一套隔离，且两个输出流都要扫 ----


def test_soffice_version_keeps_the_same_isolation_as_conversion(monkeypatch):
    """探测起 soffice 和转换起 soffice 用同一套隔离：白名单 env + 独立 UserInstallation。"""
    seen = {}

    def fake_run(cmd, *_a, **kwargs):
        seen["cmd"], seen["env"] = cmd, kwargs["env"]
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"LibreOffice 24.2\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert docx_to_pdf.soffice_version("/usr/bin/soffice") == "LibreOffice 24.2"
    assert any(a.startswith("-env:UserInstallation=file://") for a in seen["cmd"])
    assert set(seen["env"]) == {"HOME", "LC_ALL", "LANG", "PATH"}  # 不继承服务账号的环境


def test_version_probe_reads_stderr_when_stdout_is_not_empty(monkeypatch):
    """版本打在 stderr、stdout 只有一个换行：两个流分别扫，不能被 `stdout or stderr` 短路吞掉。"""
    def fake_run(cmd, *_a, **_k):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"\n", stderr=b"java 21.0.2\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert docx_to_pdf.soffice_version("/usr/bin/soffice") == "java 21.0.2"


def test_version_probe_returns_none_on_failure(monkeypatch):
    """探测起不来/非零退出：返回 None（版本未知不等于工具不可用），绝不外抛。"""
    def boom(*_a, **_k):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", boom)
    assert docx_to_pdf.soffice_version("/usr/bin/soffice") is None

    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, *_a, **_k: subprocess.CompletedProcess(args=cmd, returncode=2, stdout=b"", stderr=b""),
    )
    assert docx_to_pdf.soffice_version("/usr/bin/soffice") is None
