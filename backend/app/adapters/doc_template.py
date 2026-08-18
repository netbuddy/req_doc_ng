"""模板文件适配器（SCN-005-P01-N03 / P02-N04 / P03-N04）。

按系统内置模板 schema 校验外部只读模板文件，抽取章节元数据、章节槽位、
必填规则与渲染绑定；不判断内容能否入文档（那是文档编排规则的职责）。
模板不符合 schema 时抛 TemplateError（列出缺失项），不得用默认结构冒充。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SCHEMA_VERSIONS = ("1.0",)
BUILTIN_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_TEMPLATE_REF = "srs-iso29148-v1"
KNOWLEDGE_TEMPLATE_REF = "srs-iso29148-v2"  # P5：补业务知识四章节；v1 存量不受影响
PACKAGED_TEMPLATE_REFS = (DEFAULT_TEMPLATE_REF, KNOWLEDGE_TEMPLATE_REF)

# 内置 schema：模板文件必须表达的章节描述键（§4.3 输入契约）
_REQUIRED_SECTION_KEYS = (
    "key", "number", "title", "level", "purpose",
    "content_types", "required", "repeatable", "missing_policy",
)
# 知识类确定性整表投影 token（P5 / 07 §1.1）：声明章节由渲染期自动整表投影该类全部
# 确认态业务领域知识；不占 index_entry、不需人工勾选（SlotAssetType 不加新值）。
KNOWLEDGE_CONTENT_TYPES = (
    "knowledge:term_table",
    "knowledge:business_rule_table",
    "knowledge:participant_table",
    "knowledge:assumption_table",
)
_KNOWN_CONTENT_TYPES = (
    "boilerplate", "authored_text", "material", "chart",
    "requirement_item:functional", "requirement_item:quality",
    "requirement_item:constraint", "requirement_item:data",
    "requirement_item:interface",
    *KNOWLEDGE_CONTENT_TYPES,
)
_REQUIRED_EXPORT_BINDING_KEYS = (
    "body_font_east_asia", "body_size_pt", "first_line_indent_chars", "heading_sizes_pt",
)


class TemplateError(Exception):
    """模板缺失 / 不可读 / 不符合 schema / 描述不足 —— 只阻塞文档编排。"""


@dataclass(frozen=True)
class TemplateSection:
    key: str
    number: str
    title: str
    level: int
    purpose: str
    content_types: tuple[str, ...]
    required: bool
    repeatable: bool
    missing_policy: str  # block / skip
    boilerplate: str | None = None
    examples: tuple[str, ...] = ()  # 章节样例：AI 起草少样本参考；空 = 无（可选加法项）

    def authoring_capable(self) -> bool:
        """可撰稿章节：模板默认文本（boilerplate）或人工撰稿槽位（authored_text）。"""
        return "boilerplate" in self.content_types or "authored_text" in self.content_types


@dataclass(frozen=True)
class TemplateDescriptor:
    template_id: str
    schema_version: str
    doc_type: str
    title: str
    description: str
    export_binding: dict
    sections: tuple[TemplateSection, ...] = field(default_factory=tuple)

    def section(self, key: str) -> TemplateSection | None:
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def slot_sections(self) -> tuple[TemplateSection, ...]:
        """有内容槽位的章节（content_types 非空）。"""
        return tuple(s for s in self.sections if s.content_types)


def _template_path(template_ref: str, base_dir: Path | None = None) -> Path:
    name = template_ref.replace("-", "_")
    return (base_dir or BUILTIN_TEMPLATE_DIR) / f"{name}.json"


def load_template(template_ref: str, base_dir: Path | None = None) -> TemplateDescriptor:
    """从内置模板附件目录加载并校验；仅用于数据库初始化/测试种子。"""
    path = _template_path(template_ref, base_dir)
    if not path.exists():
        raise TemplateError(f"模板文件缺失：{template_ref}（请更换模板或修复模板文件）")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"模板文件不可读：{template_ref}（{type(exc).__name__}）") from exc
    return parse_template(text, template_ref)


def parse_template(content: str, template_ref: str = "") -> TemplateDescriptor:
    """按系统内置 schema 校验模板内容（登记与加载共用）；问题以 TemplateError 一次性列出。"""
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise TemplateError(f"模板文件不是合法 JSON：{template_ref}（{type(exc).__name__}）") from exc
    if not isinstance(raw, dict):
        raise TemplateError(f"模板文件结构非法：{template_ref}（顶层必须是 JSON 对象）")

    problems: list[str] = []
    schema_version = raw.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        problems.append(f"schema 版本不兼容：{schema_version}（支持 {'/'.join(SUPPORTED_SCHEMA_VERSIONS)}）")
    for k in ("template_id", "doc_type", "title"):
        if not raw.get(k):
            problems.append(f"缺少模板描述项：{k}")

    binding = raw.get("export_binding")
    if not isinstance(binding, dict):
        problems.append("缺少渲染绑定：export_binding（docx 导出绑定不可解析）")
        binding = {}
    else:
        for k in _REQUIRED_EXPORT_BINDING_KEYS:
            if k not in binding:
                problems.append(f"渲染绑定缺失项：export_binding.{k}")

    sections_raw = raw.get("sections")
    sections: list[TemplateSection] = []
    if not isinstance(sections_raw, list) or not sections_raw:
        problems.append("缺少章节结构：sections")
    else:
        seen: set[str] = set()
        for i, s in enumerate(sections_raw):
            missing = [k for k in _REQUIRED_SECTION_KEYS if k not in s]
            if missing:
                problems.append(f"章节 #{i}（{s.get('key', '?')}）缺少元数据：{', '.join(missing)}")
                continue
            if s["key"] in seen:
                problems.append(f"章节 key 重复：{s['key']}")
                continue
            seen.add(s["key"])
            unknown = [c for c in s["content_types"] if c not in _KNOWN_CONTENT_TYPES]
            if unknown:
                problems.append(f"章节 {s['key']} 含未知内容类型：{', '.join(unknown)}")
                continue
            if s["missing_policy"] not in ("block", "skip"):
                problems.append(f"章节 {s['key']} 缺失处理规则非法：{s['missing_policy']}")
                continue
            if "boilerplate" in s["content_types"] and not s.get("boilerplate"):
                problems.append(f"章节 {s['key']} 声明模板文本槽位但未提供 boilerplate 内容")
                continue
            ex = s.get("examples")
            if ex is not None and (
                not isinstance(ex, list) or any(not isinstance(e, str) or not e.strip() for e in ex)
            ):
                problems.append(f"章节 {s['key']} 的 examples 必须是非空字符串列表")
                continue
            sections.append(TemplateSection(
                key=s["key"], number=str(s["number"]), title=s["title"], level=int(s["level"]),
                purpose=s["purpose"], content_types=tuple(s["content_types"]),
                required=bool(s["required"]), repeatable=bool(s["repeatable"]),
                missing_policy=s["missing_policy"], boilerplate=s.get("boilerplate"),
                examples=tuple(s.get("examples") or ()),
            ))

    if problems:
        raise TemplateError("模板文件不符合系统内置 schema：" + "；".join(problems))

    return TemplateDescriptor(
        template_id=raw["template_id"], schema_version=schema_version,
        doc_type=raw["doc_type"], title=raw["title"],
        description=raw.get("description", ""), export_binding=binding,
        sections=tuple(sections),
    )


def builtin_template_files(base_dir: Path | None = None) -> list[tuple[str, str]]:
    """随软件包发布的可导入模板 (template_ref, 原始内容)；验收坏样例不在默认导入集。"""
    directory = base_dir or BUILTIN_TEMPLATE_DIR
    results: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        ref = path.stem.replace("_", "-")
        if ref not in PACKAGED_TEMPLATE_REFS:
            continue
        try:
            results.append((ref, path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return results
