"""走读文件与代码保持同步的守卫。

扫描 backend/docs/walkthroughs/*.md 里所有 `app/x.py` 或 `app/x.py::symbol` 形式的
代码引用；文件不存在、或函数/类被改名删除 → 测试失败，逼使改代码时同步改走读。
"""
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
# 走读可位于后端目录，也可位于仓库级 docs（前后端全流程走读）。两处都扫。
WALK_DIRS = [
    BACKEND / "docs" / "walkthroughs",
    REPO / "docs" / "50-code-walkthrough",
]

# 只校验 app/ 下的代码引用（走读的锚点），形如 `app/services/x.py` 或 `app/services/x.py::func`
_REF = re.compile(r"`(app/[A-Za-z0-9_./-]+\.py)(?:::([A-Za-z0-9_]+))?`")


def _iter_refs():
    for walk_dir in WALK_DIRS:
        if not walk_dir.is_dir():
            continue
        for md in sorted(walk_dir.glob("*.md")):
            if md.name.startswith("_"):  # _TEMPLATE.md 等模板含占位符，跳过
                continue
            text = md.read_text(encoding="utf-8")
            for m in _REF.finditer(text):
                yield md.name, m.group(1), m.group(2)


def test_walkthrough_code_refs_resolve():
    broken: list[str] = []
    for src, rel_path, symbol in _iter_refs():
        target = BACKEND / rel_path
        if not target.exists():
            broken.append(f"{src}: 引用的文件不存在 → {rel_path}")
            continue
        if symbol:
            code = target.read_text(encoding="utf-8")
            if not re.search(rf"^\s*(async\s+)?(def|class)\s+{re.escape(symbol)}\b", code, re.M):
                broken.append(f"{src}: {rel_path} 里找不到 def/class `{symbol}`（改代码后请同步走读）")
    assert not broken, "走读引用失效：\n" + "\n".join(broken)
