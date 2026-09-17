"""材料接入登记的领域工具：列目录、登记文件、生成清单文件。"""

from __future__ import annotations

import copy

from tod_kernel.kernel import CallStatus, Change
from tod_kernel.tools.base import ToolSpec


# ───────────────────────── 材料接入登记用的领域工具 ─────────────────────────
# 这三个工具认识材料接入登记的槽位与材料清单项的键，是领域工具；内核仍不认识它们。
# 样例表放在工具一侧而不是任务定义里，因为工具不导入任务定义；任务定义只给目录名。

SAMPLE_DIRS = {
    "样例材料": [
        {"文件名": "a.docx", "类型": "docx", "大小": 20480, "页数": 3},
        {"文件名": "b.pdf", "类型": "pdf", "大小": 51200, "页数": 5},
        {"文件名": "c.xlsx", "类型": "xlsx", "大小": 10240, "页数": 1},
    ],
}

MANIFEST_PATH = "样例材料/材料清单.txt"  # 固定路径字符串，不落盘
INCLUDED = "是"  # 回答理解本步原样返回，所以这里认的是回答原文「是」


def list_dir(ctx) -> None:
    """列目录：从只读数据的「目录」槽位取目录名，查样例表，把文件名列表写进「文件总表」。"""
    call = ctx.call
    directory = ctx.data_view.get("目录")
    if directory not in SAMPLE_DIRS:
        ctx.set_status(CallStatus.FAILED, f"样例表里没有目录：{directory!r}")
        return
    names = [entry["文件名"] for entry in SAMPLE_DIRS[directory]]
    call.result = names
    call.changes = [Change("文件总表", copy.deepcopy(ctx.data_view.get("文件总表")), names, call.call_id)]
    ctx.set_status(CallStatus.SUCCEEDED, f"列出 {len(names)} 个文件")


def register_file(ctx) -> None:
    """登记文件：按参数序号从样例表取该文件的类型、大小、页数，
    给「材料清单」追加一项（是否纳入为 None），「登记进度」加一。"""
    call = ctx.call
    index = call.params["index"]
    entries = SAMPLE_DIRS.get(ctx.data_view.get("目录"), [])
    if not 0 <= index < len(entries):
        ctx.set_status(CallStatus.FAILED, f"样例表里没有序号 {index} 的文件")
        return
    entry = entries[index]
    old_list = copy.deepcopy(ctx.data_view.get("材料清单"))
    item = {**entry, "是否纳入": None}
    old_progress = ctx.data_view.get("登记进度")
    call.result = copy.deepcopy(item)
    call.changes = [
        Change("材料清单", old_list, old_list + [item], call.call_id),
        Change("登记进度", old_progress, old_progress + 1, call.call_id),
    ]
    ctx.set_status(CallStatus.SUCCEEDED, f"登记文件 {entry['文件名']}")


def generate_manifest(ctx) -> None:
    """生成清单文件：把「材料清单」里纳入的项拼成固定格式的文本作为返回值，
    把固定路径写进「清单文件路径」。不落盘。"""
    call = ctx.call
    items = ctx.data_view.get("材料清单") or []
    included = [item for item in items if item["是否纳入"] == INCLUDED]
    lines = [f"材料清单（共 {len(included)} 项）"]
    lines += [f"{n}. {item['文件名']}，{item['类型']}，{item['大小']} 字节，{item['页数']} 页"
              for n, item in enumerate(included, start=1)]
    call.result = "\n".join(lines)
    call.changes = [Change("清单文件路径", copy.deepcopy(ctx.data_view.get("清单文件路径")), MANIFEST_PATH, call.call_id)]
    ctx.set_status(CallStatus.SUCCEEDED, f"清单含 {len(included)} 项，写到 {MANIFEST_PATH}")


LIST_DIR_NOTE = "文件总表为 None"
REGISTER_FILE_NOTE = "序号等于登记进度且小于文件总数"
GENERATE_MANIFEST_NOTE = "每项是否纳入都不为 None"


def _list_dir_precondition(data, params):
    return data.get("文件总表") is None, LIST_DIR_NOTE, {"文件总表": data.get("文件总表")}


def _register_file_precondition(data, params):
    files = data.get("文件总表")
    ok = files is not None and params.get("index") == data.get("登记进度") and params.get("index") < len(files)
    hit = {"登记进度": data.get("登记进度"), "文件总数": None if files is None else len(files)}
    return ok, REGISTER_FILE_NOTE, hit


def _generate_manifest_precondition(data, params):
    items = data.get("材料清单") or []
    confirmed = sum(1 for item in items if item["是否纳入"] is not None)
    return all(item["是否纳入"] is not None for item in items), GENERATE_MANIFEST_NOTE, {"已确认项数": confirmed}


# ───────────────────────── 本模块登记的工具 ─────────────────────────

# 静态工具规格：工具名 → ToolSpec，由 base 汇总成静态工具表 STATIC_TOOLS。
TOOL_SPECS = {
    "list_dir": ToolSpec("list_dir", (), _list_dir_precondition, frozenset({"文件总表"}),
                         category="访问外部资源",
                         summary="列出目录里的文件名，写入文件总表"),
    "register_file": ToolSpec("register_file", ("index",), _register_file_precondition,
                              frozenset({"材料清单", "登记进度"}),
                              category="访问外部资源",
                              summary="登记序号所指文件的名字、类型、大小、页数，写入材料清单并把登记进度加一"),
    "generate_manifest": ToolSpec("generate_manifest", (), _generate_manifest_precondition,
                                  frozenset({"清单文件路径"}),
                                  category="访问外部资源",
                                  summary="把材料清单里纳入的项拼成清单文件的内容，写入清单文件路径"),
}

# 工具实现：工具名 → 函数（任务定义）→ 实现，由 base 汇总；模型调用件、读事件函数与系统提示由 build_table 另外绑上。
TOOL_IMPLS = {
    "list_dir": lambda task_def: list_dir,
    "register_file": lambda task_def: register_file,
    "generate_manifest": lambda task_def: generate_manifest,
}
