"""发布产物文件落盘（AppImage 单机模式方案 §3.2 第 5 项，裁定 D7）。

导出目录下每个导出件一个子目录 `{export_id}/`：
- `prerendered/{围栏序号}.svg`：受理导出时落下的浏览器预渲染 mermaid SVG（API 进程写、转换任务读；
  两者可能不在同一进程甚至同一容器，只共享导出目录，因此不走内存传递）；
- `document.md` 与 `assets/diagram-N.svg`：Markdown 发布产物；
- `render-report.json`：图形处理失败清单（空表示全部成功）。
产物整体打成 `{export_id}-markdown.zip` 供下载。路径全部由导出件 id 推导，不落数据库。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

PRERENDER_DIR = "prerendered"
MARKDOWN_NAME = "document.md"
REPORT_NAME = "render-report.json"


def bundle_dir(export_dir: str | Path, export_id: str) -> Path:
    return Path(export_dir) / export_id


def bundle_zip_path(export_dir: str | Path, export_id: str) -> Path:
    return Path(export_dir) / f"{export_id}-markdown.zip"


def save_prerendered(export_dir: str | Path, export_id: str, svgs: dict[int, bytes]) -> None:
    """落下浏览器预渲染 SVG；空表不建目录。"""
    if not svgs:
        return
    target = bundle_dir(export_dir, export_id) / PRERENDER_DIR
    target.mkdir(parents=True, exist_ok=True)
    for index, data in svgs.items():
        (target / f"{int(index)}.svg").write_bytes(data)


def load_prerendered(export_dir: str | Path, export_id: str) -> dict[int, bytes]:
    target = bundle_dir(export_dir, export_id) / PRERENDER_DIR
    if not target.is_dir():
        return {}
    out: dict[int, bytes] = {}
    for f in target.glob("*.svg"):
        if f.stem.isdigit():
            out[int(f.stem)] = f.read_bytes()
    return out


def write_markdown_bundle(
    export_dir: str | Path, export_id: str, markdown: str,
    assets: dict[str, bytes], failures: list[str],
) -> Path:
    """写 document.md + assets/ + render-report.json 并打包；返回 zip 路径。"""
    root = bundle_dir(export_dir, export_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKDOWN_NAME).write_text(markdown, encoding="utf-8")
    for rel, data in assets.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / REPORT_NAME).write_text(
        json.dumps({"diagram_failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    zip_path = bundle_zip_path(export_dir, export_id)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(root / MARKDOWN_NAME, MARKDOWN_NAME)
        for rel in assets:
            z.write(root / rel, rel)
        z.write(root / REPORT_NAME, REPORT_NAME)
    return zip_path


def read_diagram_failures(export_dir: str | Path, export_id: str) -> list[str]:
    report = bundle_dir(export_dir, export_id) / REPORT_NAME
    if not report.exists():
        return []
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    failures = data.get("diagram_failures", [])
    return [str(x) for x in failures] if isinstance(failures, list) else []
