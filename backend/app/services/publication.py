"""文档编排服务 + 文档编排规则 + 导出执行服务（SCN-005 P01/P02/P03）。

边界（05A/SCN-005）：
- P01 只形成/调整 `LDM-014.文档内容索引`；不生成 Markdown、不导出 docx、不制造确认态事实。
- P02 只承载 Markdown 中间稿、预览编辑补丁与定稿裁定；确认态条目编辑只能形成修订回流
  （新的待确认 LDM-007），旧确认态不得原地覆盖。
- P03 只从可导出的 Markdown 定稿版本生成候选 docx；导出成功≠发布，基线必须用户显式确认。
- 候选视图≠入文档许可：未确认条目一律拒绝入索引（门禁不因模板必填而降低）。
"""
from __future__ import annotations

import difflib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.adapters.doc_template import (
    DEFAULT_TEMPLATE_REF,
    KNOWLEDGE_CONTENT_TYPES,
    TemplateDescriptor,
    TemplateError,
    TemplateSection,
    parse_template,
)
from app.adapters.docx_convert import ConversionError, convert_markdown_to_docx
from app.api.schemas import (
    AssetFragmentRead,
    CandidateAssetsRead,
    CandidateChartRead,
    CandidateItemRead,
    CandidateMaterialRead,
    CandidatePreviewRead,
    ConfirmBaselineCommand,
    ConfirmBaselineResult,
    DocIndexEntryRead,
    DocumentFragmentRead,
    DocxExportRead,
    ExportCheckCommand,
    FinalizeMarkdownCommand,
    FinalizeMarkdownResult,
    GenerateMarkdownCommand,
    ItemConfirmCommand,
    ItemConfirmResult,
    ManualFallbackCommand,
    MarkdownDraftRead,
    MarkdownEditCommand,
    MarkdownEditResult,
    MarkdownPatchRead,
    MissingItemRead,
    PublicationWorkspaceRead,
    ReleaseBaselineRead,
    ReopenIndexCommand,
    RequirementDocumentRead,
    SaveIndexCommand,
    SaveIndexResult,
    SaveManuscriptCommand,
    SectionDraftBasisRead,
    SectionDraftResultRead,
    SectionManuscriptRead,
    SlotStatusRead,
    SourceBindingRead,
    StartDocxExportCommand,
    StartDocxExportResult,
    TemplateDescriptorRead,
    TemplateSectionRead,
    TraceBindingSummaryRead,
)
from app.adapters.llm import SectionManuscriptDrafter, StubSectionManuscriptDrafter
from app.domain.enums import DocumentStatus, EditImpact
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.domain.labels import ELEMENT_TYPE_LABELS
from app.domain.naming import normalize_element_name, split_name_definition
from app.domain.reference_standards import is_reference_section_title
from app.domain.state_machine import (
    DocEvent,
    DocState,
    ItemEvent,
    ItemState,
    doc_transition,
    item_transition,
)
from app.log import log_event
from app.repositories.publication import SqlPublicationRepository

_COMPONENT = "publication"
# 候选 docx 转换的兜底超时（分钟）：converting 超过此时长且无产物即判定卡死，自愈落 failed。
# 覆盖「Redis 配了但 worker 缺席，任务静默排队」的运行态卡死。
_EXPORT_CONVERT_TIMEOUT_MIN = 10


def _as_uuid_or_none(ref: str | None) -> uuid.UUID | None:
    return uuid.UUID(str(ref)) if ref else None

_TYPE_LABELS = {
    "functional": "功能需求", "quality": "质量属性", "constraint": "约束",
    "data": "数据需求", "interface": "接口需求",
}

# 29148 属性补齐（提案 2026-07-06 拍板）：确定性投影用中文标签（定义见模板"文档约定"静态段）
_VERIFICATION_METHOD_LABELS = {
    "test": "测试", "demonstration": "演示", "inspection": "检查", "analysis": "分析",
}
_PRIORITY_LABELS = {"high": "高", "medium": "中", "low": "低"}

_REBUILD_ENTRIES = {
    "requirement_item": "回到需求管理工作台：材料接入 → 知识抽取 → 条目形成 → 条目确认后重新编排",
    "material": "回到需求管理工作台导入并接入支撑材料后重新编排",
    "chart": "回到图表设计工作台完成图表核对与确认（受控图表）后重新编排",
    "boilerplate": "模板自带内容，无需补建",
    "authored": "在索引编排页该章节「撰稿」编辑正文后重新保存",
    "knowledge": "回到知识抽取页确认业务领域知识（术语/业务规则/参与者/假设）后自动整表投影",
}


# ============================================================================
# 文档编排规则（纯函数：裁定入索引许可与必填覆盖，不写仓储）
# ============================================================================


def _slot_asset_kind(section: TemplateSection) -> str:
    """槽位主承载类型：requirement_item / chart / material / boilerplate / authored。"""
    for ct in section.content_types:
        if ct.startswith("requirement_item:"):
            return "requirement_item"
    if "chart" in section.content_types:
        return "chart"
    if "material" in section.content_types:
        return "material"
    if any(ct in KNOWLEDGE_CONTENT_TYPES for ct in section.content_types):
        return "knowledge"  # 知识类整表投影：满足条件=存在确认态该类业务知识（非阻断）
    if "boilerplate" in section.content_types:
        return "boilerplate"
    return "authored"  # 仅 authored_text：无默认文本，须人工撰稿


def _standards_pickable(section: TemplateSection) -> bool:
    """撰稿时是否给「从目录选取」引用标准的入口（T20260721）。

    两个条件都要满足：章节标题看起来是参考资料类（判定在 domain/reference_standards.py 单点
    定义），且章节支持人工撰稿——只挂材料的章节根本没有撰稿框，给了入口也无处插入。
    """
    return "authored_text" in section.content_types and is_reference_section_title(section.title)


def _draft_basis(
    template: TemplateDescriptor,
    entries: list[DocIndexEntryRead],
    candidates: CandidateAssetsRead,
) -> list[SectionDraftBasisRead]:
    """每个可 AI 起草章节的起草依据计数（T20260721）。

    口径必须与起草服务真正喂给模型的一致（`_section_confirmed_assets` 的关联确认态条目 +
    模板章节样例），否则界面会出现「说有依据、模型照样拒绝」的错报。这里复用工作区已经查好的
    索引条目与确认态候选算，不另发查询。确认态判定源须与 `_section_confirmed_assets` 保持
    同源语义：本函数经 `confirmed_items`（项目内 status=confirmed 全集）、彼处按条目 status
    逐条判——两路对同项目数据等价，任一侧加过滤条件时必须同步另一侧。
    """
    confirmed_refs = {i.item_ref for i in candidates.items}
    counts: dict[str, int] = {}
    for e in entries:
        if e.asset_type == "requirement_item" and e.asset_ref in confirmed_refs:
            counts[e.section_key] = counts.get(e.section_key, 0) + 1
    return [
        SectionDraftBasisRead(
            section_key=s.key,
            asset_count=counts.get(s.key, 0),
            example_count=len(s.examples),
        )
        for s in template.sections
        if "authored_text" in s.content_types
    ]


def _entry_matches_slot(section: TemplateSection, asset_type: str, req_type: str | None) -> bool:
    if asset_type == "requirement_item":
        return f"requirement_item:{req_type}" in section.content_types
    return asset_type in section.content_types


def evaluate_slots(
    template: TemplateDescriptor,
    entries: list[DocIndexEntryRead],
    candidate_kinds: set[str],
    manuscript_keys: set[str] | None = None,
) -> tuple[list[SlotStatusRead], list[MissingItemRead]]:
    """必填覆盖裁定：每个槽位 → 满足状态；必填未满足 → 缺失清单（补建依据）。

    candidate_kinds：项目内现存候选的种类集合——需求条目按 req_type
    （functional/quality/...），受控图表并入 "chart"，用于区分缺失原因。
    manuscript_keys：已有章节撰稿的 section_key 集合（AEP-098）——纯撰稿章节
    （authored_text 无默认文本）必填时以撰稿存在为满足条件。
    """
    manuscript_keys = manuscript_keys or set()
    by_section: dict[str, int] = {}
    for e in entries:
        by_section[e.section_key] = by_section.get(e.section_key, 0) + 1

    statuses: list[SlotStatusRead] = []
    missing: list[MissingItemRead] = []
    for s in template.slot_sections():
        kind = _slot_asset_kind(s)
        if kind == "boilerplate":  # 模板自带文本：由模板满足，非治理资产
            statuses.append(SlotStatusRead(
                section_key=s.key, required=s.required, satisfied=True, filled_count=1,
            ))
            continue
        if kind == "knowledge":  # 知识整表投影：满足=存在该类确认态业务知识；空集非阻断缺项
            tokens = [ct for ct in s.content_types if ct in KNOWLEDGE_CONTENT_TYPES]
            satisfied = any(t in candidate_kinds for t in tokens)
            if not satisfied:
                labels = "、".join(_KNOWLEDGE_EMPTY_LABEL[t] for t in tokens)
                missing.append(MissingItemRead(
                    section_key=s.key, section_title=f"{s.number} {s.title}",
                    reason=f"本项目暂无已确认{labels}（整表投影为空，非阻断）",
                    rebuild_entry=_REBUILD_ENTRIES["knowledge"],
                ))
            statuses.append(SlotStatusRead(
                section_key=s.key, required=s.required, satisfied=satisfied,
                filled_count=1 if satisfied else 0,
                missing_reason=None if satisfied else "暂无已确认业务知识（整表投影为空）",
                rebuild_entry=None if satisfied else _REBUILD_ENTRIES["knowledge"],
            ))
            continue
        if kind == "authored":  # 纯撰稿章节：满足条件 = 撰稿存在
            authored = s.key in manuscript_keys
            satisfied = authored or not s.required
            if not satisfied:
                missing.append(MissingItemRead(
                    section_key=s.key, section_title=f"{s.number} {s.title}",
                    reason="必填槽位缺失：该章节需人工撰稿",
                    rebuild_entry=_REBUILD_ENTRIES["authored"],
                ))
            statuses.append(SlotStatusRead(
                section_key=s.key, required=s.required, satisfied=satisfied,
                filled_count=1 if authored else 0,
                missing_reason=None if satisfied else "该章节需人工撰稿",
                rebuild_entry=None if satisfied else _REBUILD_ENTRIES["authored"],
            ))
            continue
        filled = by_section.get(s.key, 0)
        satisfied = filled > 0 or not s.required
        reason = None
        if not satisfied:
            wanted = [
                ct.split(":", 1)[1] if ":" in ct else ct
                for ct in s.content_types if ct != "boilerplate"
            ]
            has_candidates = any(t in candidate_kinds for t in wanted)
            reason = (
                "已有确认态候选资产但尚未编排到该槽位" if has_candidates
                else "项目中不存在满足该槽位的确认态资产"
            )
            missing.append(MissingItemRead(
                section_key=s.key, section_title=f"{s.number} {s.title}",
                reason=f"必填槽位缺失：{reason}", rebuild_entry=_REBUILD_ENTRIES[kind],
            ))
        statuses.append(SlotStatusRead(
            section_key=s.key, required=s.required, satisfied=satisfied,
            filled_count=filled, missing_reason=reason,
            rebuild_entry=_REBUILD_ENTRIES[kind] if not satisfied else None,
        ))
    return statuses, missing


# ============================================================================
# Markdown 生成（确定性模板渲染；保留行区间 → 源资产绑定）
# ============================================================================


def _table_cell(text: str) -> str:
    """属性表单元格净化：换行折为空格、竖线替换，避免破坏 Markdown 表格结构。"""
    return " ".join(text.split()).replace("|", "｜")


def _render_item_block(item, meta: dict) -> str:
    """条目块（29148 §5.2.8 属性化呈现）：规范陈述一句 + 已确认属性表。

    只投影确认态权威字段与治理事实，不做任何生成式加工；
    结构化 payload 投影（过程记录、非事实源）不进入文档内容。
    """
    label = _TYPE_LABELS.get(item.req_type, item.req_type)
    block = [f"**{item.req_no}**（{label} · v{item.version_no} · 已确认）", "", item.expression]
    rows: list[tuple[str, str]] = []
    if item.curation_note:
        rows.append(("内容整理说明", item.curation_note))
    if item.boundary_note:
        rows.append(("条目边界说明", item.boundary_note))
    # 29148 属性补齐：验证方式与验收准则 / 优先级（确认态字段投影；空值不渲染）
    verification_parts = []
    methods = [c for c in (item.verification_method or "").split(",") if c]
    if methods:
        verification_parts.append(
            "、".join(_VERIFICATION_METHOD_LABELS.get(m, m) for m in methods)
        )
    if item.verification_note:
        verification_parts.append(item.verification_note)
    if verification_parts:
        rows.append(("验证方式与验收准则", "：".join(verification_parts)))
    if item.priority:
        rows.append(("优先级", _PRIORITY_LABELS.get(item.priority, item.priority)))
    if meta.get("sources"):
        source_text = "；".join(meta["sources"])
        if meta.get("element_count"):
            source_text += f"（来源要素 {meta['element_count']} 项）"
        rows.append(("来源依据", source_text))
    if meta.get("charts"):
        rows.append(("关联图表", "、".join(meta["charts"])))
    if rows:
        block += ["", "| 属性 | 说明 |", "| --- | --- |"]
        block += [f"| {name} | {_table_cell(value)} |" for name, value in rows]
    return "\n".join(block)


# 知识类确定性整表投影规格（P5 / 07 §1.2）：token → 投影对象类型 + 列定义。
# 列 kind：name=切分名 / definition=切分余文（切不出置"—"）/ statement=整条陈述（逐字）/
# category=element_type 中文类别 / dash=增强面向（v0.1 恒"—"）/ source=来源材料标题引用。
# 列头取各类型 rubric 必备面向，与判据/知识抽取页同一口径（07 §1.2）。
_KNOWLEDGE_PROJECTION: dict[str, dict] = {
    "knowledge:term_table": {
        "types": ("term",), "empty_label": "术语",
        "columns": (("术语名", "name"), ("定义", "definition"),
                    ("适用范围或同义词", "dash"), ("来源材料", "source")),
    },
    "knowledge:business_rule_table": {
        "types": ("business_rule",), "empty_label": "业务规则",
        "columns": (("规则陈述", "statement"), ("出处或授权依据", "dash"),
                    ("作用范围", "dash"), ("来源材料", "source")),
    },
    "knowledge:participant_table": {
        "types": ("role", "external_system"), "empty_label": "参与者",
        "columns": (("名称", "name"), ("类别", "category"),
                    ("职责或交互目的", "definition"), ("来源材料", "source")),
    },
    "knowledge:assumption_table": {
        "types": ("assumption",), "empty_label": "假设与依赖",
        "columns": (("假设陈述", "statement"), ("失效影响", "dash"), ("来源材料", "source")),
    },
}
# 章节 content_types 中的知识 token（每章节至多一个知识 token）
_KNOWLEDGE_EMPTY_LABEL = {tok: spec["empty_label"] for tok, spec in _KNOWLEDGE_PROJECTION.items()}


def _knowledge_token_of(section: TemplateSection) -> str | None:
    for ct in section.content_types:
        if ct in _KNOWLEDGE_PROJECTION:
            return ct
    return None


def _render_knowledge_table(token: str, rows_src: list[tuple]) -> str:
    """知识类确定性整表投影（07 §1.2）：只切分投射 `LDM-005.content`，禁生成式加工。

    rows_src：`[(element, source_title)]`，已按类型声明序 + 名称规范化序排好。
    空集由调用方渲染占位段（不进本函数）。增强面向列恒 "—"（v0.1 业务知识为自由文本）。
    """
    spec = _KNOWLEDGE_PROJECTION[token]
    columns = spec["columns"]
    lines = ["| " + " | ".join(h for h, _ in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for element, source_title in rows_src:
        name, rest = split_name_definition(element.content)
        cells: list[str] = []
        for _, kind in columns:
            if kind == "name":
                cells.append(_table_cell(name))
            elif kind == "definition":
                cells.append(_table_cell(rest) if rest else "—")
            elif kind == "statement":
                cells.append(_table_cell(element.content))
            elif kind == "category":
                cells.append(ELEMENT_TYPE_LABELS.get(element.element_type, element.element_type))
            elif kind == "source":
                cells.append(_table_cell(source_title) if source_title else "—")
            else:  # dash：增强面向缺省
                cells.append("—")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_markdown(
    template: TemplateDescriptor,
    entries: list,
    items_by_ref: dict,
    materials_by_ref: dict,
    charts_by_ref: dict,
    project_name: str,
    coverage_scope: str,
    item_meta: dict[str, dict] | None = None,
    manuscripts: dict[str, str] | None = None,
    knowledge_by_type: dict[str, list[tuple]] | None = None,
) -> tuple[str, list[dict]]:
    lines: list[str] = []
    bindings: list[dict] = []

    def emit(text: str, kind: str, section_key: str, asset_ref: str | None = None) -> None:
        start = len(lines)
        for ln in text.split("\n"):
            lines.append(ln)
        bindings.append({
            "start_line": start, "end_line": len(lines) - 1,
            "kind": kind, "section_key": section_key, "asset_ref": asset_ref,
        })
        lines.append("")  # 块间空行（不入绑定）

    entries_by_section: dict[str, list] = {}
    for e in entries:
        entries_by_section.setdefault(e.section_key, []).append(e)

    def substitute(text: str) -> str:
        return text.replace("{project_name}", project_name).replace(
            "{coverage_scope}", coverage_scope or "本次发布范围"
        )

    for s in template.sections:
        emit(f"{'#' * s.level} {s.number} {s.title}", "heading", s.key)
        # 章节撰稿（AEP-098）覆盖模板默认文本；两者都做占位替换
        manuscript = (manuscripts or {}).get(s.key)
        if manuscript and s.authoring_capable():
            emit(substitute(manuscript), "authored", s.key)
        elif s.boilerplate:
            emit(substitute(s.boilerplate), "boilerplate", s.key)
        # 知识类整表投影（P5 / 07 §1.2）：整表自动投影，不占 index_entry；空集渲染占位段。
        token = _knowledge_token_of(s)
        if token is not None:
            spec = _KNOWLEDGE_PROJECTION[token]
            rows_src: list[tuple] = []
            for t in spec["types"]:
                rows_src.extend((knowledge_by_type or {}).get(t, []))
            if rows_src:
                emit(_render_knowledge_table(token, rows_src), "knowledge", s.key)
            else:
                emit(f"（本项目暂无已确认{spec['empty_label']}）", "knowledge", s.key)
        for e in sorted(entries_by_section.get(s.key, []), key=lambda x: x.order_no):
            ref = str(e.asset_ref) if e.asset_ref else None
            if e.asset_type == "requirement_item" and ref in items_by_ref:
                item = items_by_ref[ref]
                emit(_render_item_block(item, (item_meta or {}).get(ref, {})), "item", s.key, ref)
            elif e.asset_type == "chart" and ref in charts_by_ref:
                chart = charts_by_ref[ref]
                if chart.format == "markdown_table":
                    body = chart.source_code
                else:  # mermaid / plantuml：源码围栏随文档发布，渲染由外部能力承接
                    body = f"```{chart.format}\n{chart.source_code}\n```"
                emit(f"**图：{chart.title}**（{chart.chart_type}）\n{body}", "chart", s.key, ref)
            elif e.asset_type == "material" and ref in materials_by_ref:
                m = materials_by_ref[ref]
                emit(
                    f"- {m.source_note or '来源材料'}（来源版本 v{m.source_version}）",
                    "material", s.key, ref,
                )
    return "\n".join(lines).rstrip() + "\n", bindings


# ============================================================================
# 编辑影响识别（P02-N10：diff 提交稿 vs 生成稿，按源资产绑定分类）
# ============================================================================


_MD_NOISE = re.compile(r"[\s\*#>\-·，。；：、,.;:!?！？()（）]")


def _normalize(text: str) -> str:
    return _MD_NOISE.sub("", text)


def _classify_edits(
    generated: str, submitted: str, bindings: list[dict],
    support_corpus: list[str],
) -> list[dict]:
    """返回补丁清单：[{impact, before, after, bound_item_ref}]。

    规则（§5.3）：改标题行→索引结构；改条目绑定行→确认态条目修订；
    纯新增且语料不支撑→无来源新事实；其余→纯文档表达。
    """
    old_lines = generated.splitlines()
    new_lines = submitted.splitlines()
    corpus_norm = [_normalize(c) for c in support_corpus if c]
    patches: list[dict] = []

    def overlapped(i1: int, i2: int) -> list[dict]:
        if i1 == i2:  # 纯插入：看插入点前后行的归属
            probe = [i for i in (i1 - 1, i1) if 0 <= i < len(old_lines)]
            return [b for b in bindings if any(b["start_line"] <= i <= b["end_line"] for i in probe)]
        return [b for b in bindings if b["start_line"] < i2 and b["end_line"] >= i1]

    def supported(text: str) -> bool:
        if text.strip().startswith("<!--"):
            return True  # 注释行不进正文，不构成新事实（含失败注入标记）
        norm = _normalize(text)
        if len(norm) < 6:
            return True  # 短标点/衔接词按表达处理
        return any(norm in c for c in corpus_norm)

    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = "\n".join(old_lines[i1:i2])
        after = "\n".join(new_lines[j1:j2])
        region = overlapped(i1, i2)
        new_heading = any(ln.lstrip().startswith("#") for ln in new_lines[j1:j2])
        old_heading = any(b["kind"] == "heading" for b in region) and tag != "insert"

        # 受控图表源码与知识类整表投影均不得经文档窗口改写（OTHER_ASSET 阻断）：
        # 图表回图表设计工作台；知识行回知识抽取页修订（07 §1.2 source_bindings）。
        chart_region = any(b["kind"] in ("chart", "knowledge") for b in region) and tag != "insert"
        # 撰稿/默认文本区域（含纯插入的紧邻区域）：人工撰写内容是第一类正文来源，
        # 不受来源语料门禁约束（VAL-P01-12）
        authored_region = bool(region) and all(
            b["kind"] in ("authored", "boilerplate") for b in region
        )

        if old_heading or (new_heading and tag != "equal"):
            impact, bound = EditImpact.INDEX_STRUCTURE, None
        elif chart_region:
            # 受控图表源码不得经文档窗口改写：修订必须回图表设计工作台
            impact, bound = EditImpact.OTHER_ASSET, None
        elif tag == "insert":
            impact = (
                EditImpact.DOC_EXPRESSION
                if authored_region or all(supported(ln) for ln in new_lines[j1:j2] if ln.strip())
                else EditImpact.NO_SOURCE_FACT
            )
            bound = None
        else:
            item_bindings = [b for b in region if b["kind"] == "item"]
            if item_bindings:
                impact, bound = EditImpact.CONFIRMED_ITEM, item_bindings[0]["asset_ref"]
            else:
                impact, bound = EditImpact.DOC_EXPRESSION, None
        patches.append({
            "impact": impact.value, "before": before, "after": after, "bound_item_ref": bound,
        })
    return patches


_BLOCKING_IMPACTS = {
    EditImpact.NO_SOURCE_FACT.value: "存在来源材料无法支撑的新事实：删除该编辑或回到材料补充",
    EditImpact.INDEX_STRUCTURE.value: "编辑触及章节结构：请回到索引编排页（P01）调整章节映射",
    EditImpact.OTHER_ASSET.value: "编辑触及本流程外正式资产：请进入对应资产流程处理",
}


# ============================================================================
# 文档编排服务（P01 + P02）
# ============================================================================


class DocumentOrchestrationService:
    """文档编排服务：索引编排、Markdown 生成、窗口微调、定稿裁定。"""

    def __init__(
        self, repo: SqlPublicationRepository, drafter: SectionManuscriptDrafter | None = None,
    ) -> None:
        self._repo = repo
        # 章节撰稿 AI 起草（AEP-110）；无模型时用 stub（确定性拼稿/输入不足显式拒绝）
        self._drafter = drafter or StubSectionManuscriptDrafter()

    def _parse_template_row(self, row) -> TemplateDescriptor:
        return parse_template(row.content, row.template_key)

    def _load_template(self, template_key: str) -> tuple[TemplateDescriptor, str]:
        """按业务模板编码读取最新 active 注册行；运行期不读本地模板文件。"""
        row = self._repo.latest_active_template(template_key)
        if row is None:
            raise TemplateError(f"模板未在 template_registry 中登记或已停用：{template_key}")
        return self._parse_template_row(row), str(row.id)

    def _template_for_document(self, doc) -> TemplateDescriptor:
        """已编排文档只用其绑定的注册行快照（模板升级不影响在途文档）。"""
        row = self._repo.get_template_row(str(doc.template_id)) if doc.template_id else None
        if row is None:
            raise TemplateError(f"文档绑定的模板登记行不存在：{doc.template_id}")
        return self._parse_template_row(row)

    # ---- 读视图 ----

    def read_workspace(self, project_ref: str, template_ref: str | None = None) -> PublicationWorkspaceRead:
        doc = self._repo.get_document(project_ref)
        if template_ref:
            template_read, template = self._read_template(template_ref)
        elif doc is not None:
            template_read, template = self._read_document_template(doc)
        else:
            template_read, template = self._read_template(DEFAULT_TEMPLATE_REF)
        candidates = self._read_candidates(project_ref)
        manuscripts = (
            [self._manuscript_read(m) for m in self._repo.manuscripts_of(str(doc.id))]
            if doc is not None else []
        )

        entries: list[DocIndexEntryRead] = []
        slot_status: list[SlotStatusRead] = []
        missing: list[MissingItemRead] = []
        draft_basis: list[SectionDraftBasisRead] = []
        if doc is not None and doc.index_version > 0:
            entries = [
                DocIndexEntryRead(
                    section_key=e.section_key, asset_type=e.asset_type,
                    asset_ref=str(e.asset_ref) if e.asset_ref else None,
                    asset_version=e.asset_version, order_no=e.order_no,
                )
                for e in self._repo.entries_of(str(doc.id), doc.index_version)
            ]
        if template is not None:
            kinds = {i.req_type for i in candidates.items}
            if candidates.charts:
                kinds.add("chart")
            if candidates.materials:
                kinds.add("material")
            slot_status, missing = evaluate_slots(
                template, entries, kinds, {m.section_key for m in manuscripts},
            )
            draft_basis = _draft_basis(template, entries, candidates)

        draft = self._repo.latest_draft(str(doc.id)) if doc else None
        markdown = self._draft_read(draft) if draft is not None else None
        if doc is not None:
            self._reconcile_stale_exports(str(doc.id))
        exports = [self._export_read(x) for x in self._repo.exports_of(str(doc.id))] if doc else []
        baseline_row = self._repo.baseline_of(str(doc.id)) if doc else None

        return PublicationWorkspaceRead(
            project_ref=project_ref,
            document=self._doc_read(doc, template_read) if doc else None,
            template=template_read,
            candidates=candidates,
            manuscripts=manuscripts,
            draft_basis=draft_basis,
            index_entries=entries,
            slot_status=slot_status,
            missing_list=missing,
            markdown=markdown,
            exports=exports,
            baseline=self._baseline_read(baseline_row) if baseline_row else None,
            next_action=self._next_action(doc, markdown, template_read),
        )

    def _section_reads(self, t: TemplateDescriptor) -> list[TemplateSectionRead]:
        """模板章节 → 章节读模型。两条模板读路径（按编码读 / 按文档绑定读）共用一处投影，
        免得新增字段时只补了一边、同一个模板在两条路径下读出不同结果。"""
        return [TemplateSectionRead(
            key=s.key, number=s.number, title=s.title, level=s.level, purpose=s.purpose,
            content_types=list(s.content_types), required=s.required,
            repeatable=s.repeatable, missing_policy=s.missing_policy, boilerplate=s.boilerplate,
            examples=list(s.examples),
            standards_pickable=_standards_pickable(s),
        ) for s in t.sections]

    def _read_template(self, template_key: str) -> tuple[TemplateDescriptorRead, TemplateDescriptor | None]:
        try:
            t, _ = self._load_template(template_key)
        except TemplateError as exc:
            return TemplateDescriptorRead(template_ref=template_key, error=str(exc)), None
        return TemplateDescriptorRead(
            template_ref=template_key, schema_version=t.schema_version, title=t.title,
            description=t.description,
            sections=self._section_reads(t),
        ), t

    def _read_document_template(self, doc) -> tuple[TemplateDescriptorRead, TemplateDescriptor | None]:
        try:
            row = self._repo.get_template_row(str(doc.template_id)) if doc.template_id else None
            if row is None:
                raise TemplateError(f"文档绑定的模板登记行不存在：{doc.template_id}")
            t = self._parse_template_row(row)
        except TemplateError as exc:
            return TemplateDescriptorRead(template_ref="", error=str(exc)), None
        return TemplateDescriptorRead(
            template_ref=row.template_key, schema_version=t.schema_version, title=t.title,
            description=t.description,
            sections=self._section_reads(t),
        ), t

    def _read_candidates(self, project_ref: str) -> CandidateAssetsRead:
        items = self._repo.confirmed_items(project_ref)
        materials = self._repo.materials(project_ref)
        charts = self._repo.confirmed_charts(project_ref)
        trace_counts = self._repo.trace_link_status_counts(project_ref)
        return CandidateAssetsRead(
            items=[CandidateItemRead(
                item_ref=str(i.id), req_no=i.req_no, expression=i.expression,
                req_type=i.req_type, status=i.status, version_no=str(i.version_no),
            ) for i in items],
            materials=[CandidateMaterialRead(
                material_ref=str(m.id), source_note=m.source_note,
                excerpt=(m.raw_text[:120] + "…") if len(m.raw_text) > 120 else m.raw_text,
                source_version=m.source_version,
            ) for m in materials],
            charts=[CandidateChartRead(
                chart_ref=str(c.id), title=c.title, chart_type=c.chart_type,
                format=c.format, status=c.status, draft_version=c.draft_version,
                source_count=len(json.loads(c.source_refs or "[]")),
                confirmed_at=c.confirmed_at.isoformat() if c.confirmed_at else None,
            ) for c in charts],
            trace_summary=TraceBindingSummaryRead(
                effective=trace_counts.get("effective", 0),
                pre_established=trace_counts.get("pre_established", 0),
                suspect=trace_counts.get("suspect_pending_review", 0),
            ),
            pending_item_count=self._repo.pending_item_count(project_ref),
        )

    def _candidate_kinds(self, project_ref: str) -> set[str]:
        """项目内现存候选种类（evaluate_slots 缺失原因判据）。"""
        kinds = {i.req_type for i in self._repo.confirmed_items(project_ref)}
        if self._repo.confirmed_charts(project_ref):
            kinds.add("chart")
        if self._repo.materials(project_ref):
            kinds.add("material")
        # 知识 token 候选（P5）：存在确认态该类业务知识时点亮对应 token
        all_types = [t for spec in _KNOWLEDGE_PROJECTION.values() for t in spec["types"]]
        present = {e.element_type for e in self._repo.confirmed_business_elements(project_ref, all_types)}
        for token, spec in _KNOWLEDGE_PROJECTION.items():
            if any(t in present for t in spec["types"]):
                kinds.add(token)
        return kinds

    def read_asset_fragment(self, project_ref: str, asset_type: str, asset_ref: str) -> AssetFragmentRead:
        """资产 → 文档片段追溯（追溯分析工作台预览用，只读）。

        片段切自 `generated_content` 行区间：item/chart 绑定行的编辑分别触发
        修订回流与 OTHER_ASSET 阻断，定稿中这两类绑定行必与生成稿一致，
        故切片即定稿中该资产片段的精确值。未入文档是正常业务态，不抛 404。
        """
        if asset_type not in ("requirement_item", "chart"):
            raise InvalidInput(f"不支持的片段追溯资产类型：{asset_type}（仅 requirement_item / chart）")
        result = AssetFragmentRead(project_ref=project_ref, asset_type=asset_type, asset_ref=asset_ref)
        doc = self._repo.get_document(project_ref)
        if doc is None:
            result.next_action = "项目尚未进行文档编排（SCN-005-P01）"
            return result
        result.document_ref = str(doc.id)
        result.document_title = doc.title
        result.document_status = doc.status
        result.index_version = doc.index_version
        if doc.index_version > 0:
            result.in_current_index = any(
                e.asset_type == asset_type and str(e.asset_ref) == asset_ref
                for e in self._repo.entries_of(str(doc.id), doc.index_version)
            )
        draft = self._repo.latest_draft(str(doc.id))
        if draft is None:
            result.next_action = (
                "索引已形成但尚未生成 Markdown（片段随生成产生）"
                if doc.index_version > 0 else "文档内容索引尚未保存"
            )
            return result
        result.draft_ref = str(draft.id)
        result.draft_version = draft.version_no
        result.draft_status = draft.status
        baseline = self._repo.baseline_of(str(doc.id))
        if baseline is not None and str(baseline.draft_ref) == str(draft.id):
            result.baseline_ref = str(baseline.id)

        try:
            template = self._template_for_document(doc)
        except TemplateError:
            template = None
        wanted_kind = "item" if asset_type == "requirement_item" else "chart"
        lines = draft.generated_content.splitlines()
        for b in json.loads(draft.source_bindings or "[]"):
            if b.get("kind") != wanted_kind or b.get("asset_ref") != asset_ref:
                continue
            section = template.section(b["section_key"]) if template else None
            result.fragments.append(DocumentFragmentRead(
                section_key=b["section_key"],
                section_number=section.number if section else "",
                section_title=section.title if section else b["section_key"],
                start_line=b["start_line"], end_line=b["end_line"],
                markdown="\n".join(lines[b["start_line"]:b["end_line"] + 1]),
            ))
        if not result.fragments:
            result.next_action = "该资产未编排进当前 Markdown 稿；如需入文档请回索引编排页（P01）"
        log_event(_COMPONENT, "asset_fragment.read", ok=True, asset_type=asset_type,
                  fragment_count=len(result.fragments), draft_status=draft.status)
        return result

    # ---- P01：索引保存（含准入校验）----

    def save_content_index(self, command: SaveIndexCommand) -> SaveIndexResult:
        template_ref = command.template_ref or DEFAULT_TEMPLATE_REF
        try:
            template, template_id = self._load_template(template_ref)
        except TemplateError as exc:
            doc = self._repo.get_document(command.project_ref)
            if doc is not None:
                doc.status = DocumentStatus.INDEX_BLOCKED.value
                doc.blocked_reason = str(exc)
            log_event(_COMPONENT, "index.blocked.template", ok=False, template=template_ref)
            return SaveIndexResult(
                status="index_blocked", document_ref=str(doc.id) if doc else None,
                blocked_reason=str(exc),
                next_action="先完成模板注册表初始化或启用可用模板后重新编排",
            )

        # 入索引许可裁定：资产必须存在且为确认态；类型必须匹配槽位（候选≠许可）
        item_refs = [e.asset_ref for e in command.entries
                     if e.asset_type == "requirement_item" and e.asset_ref]
        items = {str(i.id): i for i in self._repo.get_items(item_refs)}
        material_refs = [e.asset_ref for e in command.entries
                         if e.asset_type == "material" and e.asset_ref]
        materials = {str(m.id): m for m in self._repo.get_materials(material_refs)}
        chart_refs = [e.asset_ref for e in command.entries
                      if e.asset_type == "chart" and e.asset_ref]
        charts = {str(c.id): c for c in self._repo.get_charts(chart_refs)}
        for e in command.entries:
            section = template.section(e.section_key)
            if section is None or not section.content_types:
                raise InvalidInput(f"章节槽位不存在或不可承载内容：{e.section_key}")
            if e.asset_type == "requirement_item":
                item = items.get(e.asset_ref or "")
                if item is None:
                    raise NotFound(f"需求条目不存在：{e.asset_ref}")
                if item.status != "confirmed":
                    raise RejectedTransition(
                        f"条目 {item.req_no} 未确认，不得进入文档内容索引；"
                        "请先回到条目确认流程（门禁不因模板必填而降低）"
                    )
                if not _entry_matches_slot(section, "requirement_item", item.req_type):
                    raise InvalidInput(
                        f"条目 {item.req_no}（{item.req_type}）与槽位 {e.section_key} 内容类型不匹配"
                    )
            elif e.asset_type == "chart":
                chart = charts.get(e.asset_ref or "")
                if chart is None:
                    raise NotFound(f"需求图表不存在：{e.asset_ref}")
                if chart.status != "confirmed":
                    raise RejectedTransition(
                        f"图表「{chart.title}」未确认为受控图表，不得进入文档内容索引；"
                        "请先回到图表设计工作台完成核对与确认（门禁不因模板必填而降低）"
                    )
                if not _entry_matches_slot(section, "chart", None):
                    raise InvalidInput(f"槽位 {e.section_key} 不承载需求图表")
            elif e.asset_type == "material":
                if e.asset_ref not in materials:
                    raise NotFound(f"支撑材料不存在：{e.asset_ref}")
                if not _entry_matches_slot(section, "material", None):
                    raise InvalidInput(f"槽位 {e.section_key} 不承载支撑材料")

        existing_doc = self._repo.get_document(command.project_ref)
        manuscript_keys = (
            {m.section_key for m in self._repo.manuscripts_of(str(existing_doc.id))}
            if existing_doc is not None else set()
        )
        _, missing = evaluate_slots(
            template, command.entries, self._candidate_kinds(command.project_ref),
            manuscript_keys,
        )

        doc = self._ensure_document(
            command.project_ref, template_id, command.coverage_scope,
        )
        # 知识整表投影空集为非阻断缺项（列入缺失清单供提示，但不阻断索引就绪，AC-P5-02）
        blocking_missing = [
            m for m in missing
            if (sec := template.section(m.section_key)) is None or _slot_asset_kind(sec) != "knowledge"
        ]
        ready = not blocking_missing
        current = DocState(doc.status) if doc.status else DocState.INDEX_DRAFT
        doc.status = doc_transition(current, DocEvent.SAVE_INDEX, ready=ready).value \
            if current in (DocState.INDEX_DRAFT, DocState.INDEX_BLOCKED, DocState.INDEX_READY) \
            else (DocState.INDEX_READY if ready else DocState.INDEX_BLOCKED).value
        doc.index_version += 1
        doc.template_id = _as_uuid_or_none(template_id)
        if command.coverage_scope is not None:
            doc.coverage_scope = command.coverage_scope
        doc.blocked_reason = None if ready else "模板必填内容缺失，索引受阻（见缺失清单）"
        doc.missing_list = json.dumps([m.model_dump() for m in missing], ensure_ascii=False)

        self._repo.write_index_entries(str(doc.id), doc.index_version, [
            {
                "section_key": e.section_key, "asset_type": e.asset_type,
                "asset_ref": e.asset_ref,
                "asset_version": str(items[e.asset_ref].version_no)
                if e.asset_type == "requirement_item" and e.asset_ref in items
                else str(materials[e.asset_ref].source_version)
                if e.asset_type == "material" and e.asset_ref in materials
                else str(charts[e.asset_ref].draft_version)
                if e.asset_type == "chart" and e.asset_ref in charts else "1",
                "order_no": e.order_no,
            } for e in command.entries
        ])
        # 索引变更 → 已有 Markdown 稿一律标记需重新生成（不改正式资产）
        self._repo.supersede_open_drafts(str(doc.id))

        log_event(
            _COMPONENT, "index.saved", ok=ready, document_ref=str(doc.id),
            index_version=doc.index_version, entry_count=len(command.entries),
            missing_count=len(missing),
        )
        return SaveIndexResult(
            status="index_ready" if ready else "index_blocked",
            document_ref=str(doc.id), index_version=doc.index_version,
            missing_list=missing, blocked_reason=doc.blocked_reason,
            next_action="进入 Markdown 生成（SCN-005-P02）" if ready
            else "按缺失清单补建/确认资产后重新编排；不可用 Markdown 临时补写",
        )

    def _ensure_document(self, project_ref, template_id, coverage_scope):
        doc = self._repo.get_document(project_ref)
        if doc is None:
            doc = self._repo.create_document(
                project_ref, template_id, "需求规格说明",
                DocumentStatus.INDEX_DRAFT.value, coverage_scope,
            )
        return doc

    # ---- P01：章节撰稿（AEP-098）----

    def _manuscript_read(self, row) -> SectionManuscriptRead:
        return SectionManuscriptRead(
            section_key=row.section_key, content=row.content,
            revision_no=row.revision_no, updated_by=row.updated_by,
            updated_at=row.updated_at.isoformat() if row.updated_at else "",
        )

    def save_section_manuscript(self, command: SaveManuscriptCommand) -> SectionManuscriptRead:
        """保存/清除章节撰稿：只写 ldm014_section_manuscript，不改治理资产事实。

        content 空白 = 删除撰稿行（回落模板默认文本）；文档不存在时按模板先建档
        （同 save_content_index 口径），不推进文档状态、不 supersede 既有 Markdown 稿
        ——撰稿变化经「重新生成」进入下一稿。
        """
        doc = self._repo.get_document(command.project_ref)
        if doc is not None and doc.template_id:
            template = self._template_for_document(doc)
            template_id = str(doc.template_id)
        else:
            template, template_id = self._load_template(
                command.template_ref or DEFAULT_TEMPLATE_REF
            )
        section = template.section(command.section_key)
        if section is None:
            raise InvalidInput(f"模板章节不存在：{command.section_key}")
        if not section.authoring_capable():
            raise InvalidInput(
                f"章节 {section.number} {section.title} 不可撰稿"
                "（仅 boilerplate/authored_text 章节承载人工正文）"
            )
        doc = self._ensure_document(command.project_ref, template_id, None)

        if not command.content.strip():
            self._repo.delete_manuscript(str(doc.id), command.section_key)
            log_event(_COMPONENT, "manuscript.cleared", ok=True, document_ref=str(doc.id),
                      section_key=command.section_key, by=command.operator_ref)
            return SectionManuscriptRead(
                section_key=command.section_key, content="", revision_no=0,
                updated_by=command.operator_ref, updated_at="",
            )
        row = self._repo.upsert_manuscript(
            str(doc.id), command.section_key, command.content, command.operator_ref,
        )
        log_event(_COMPONENT, "manuscript.saved", ok=True, document_ref=str(doc.id),
                  section_key=command.section_key, revision_no=row.revision_no,
                  length=len(command.content), by=command.operator_ref)
        return self._manuscript_read(row)

    # ---- AEP-110：章节撰稿 AI 起草初稿（写撰稿阶段，发布渲染仍确定性）----

    def _content_types_text(self, content_types: tuple[str, ...]) -> str:
        """内容装配 → 可读中文（口吻/结构提示；非枚举清单，仅本章节已装配类型）。"""
        labels: list[str] = []
        for c in content_types:
            if c == "authored_text":
                labels.append("人工撰稿")
            elif c == "boilerplate":
                labels.append("模板默认文本")
            elif c.startswith("requirement_item:"):
                labels.append(_TYPE_LABELS.get(c.split(":", 1)[1], c) + "条目")
            elif c == "chart":
                labels.append("需求图表")
            elif c == "material":
                labels.append("支撑材料")
            else:
                labels.append(c)
        return "、".join(labels) or "（无装配）"

    def _section_confirmed_assets(self, doc, section_key: str) -> list[dict]:
        """该章节槽位经 index_entry 关联的**确认态**需求条目摘要（事实输入）。"""
        if doc.index_version <= 0:
            return []
        refs = [
            str(e.asset_ref)
            for e in self._repo.entries_of(str(doc.id), doc.index_version)
            if e.section_key == section_key and e.asset_type == "requirement_item" and e.asset_ref
        ]
        if not refs:
            return []
        items = {str(i.id): i for i in self._repo.get_items(refs)}
        out: list[dict] = []
        for ref in refs:
            item = items.get(ref)
            if item is None or item.status != "confirmed":
                continue
            out.append({
                "req_no": item.req_no,
                "type": _TYPE_LABELS.get(item.req_type, item.req_type),
                "expression": item.expression,
            })
        return out

    def draft_section_manuscript(
        self, project_ref: str, section_key: str, operator_ref: str, template_ref: str | None = None,
    ) -> SectionDraftResultRead:
        """AEP-110：为 authored_text 章节起草初稿并写入 ldm014_section_manuscript（人工可改可清空）。

        红线：只发生在撰稿阶段、只预填初稿；发布渲染仍走 _assemble/_render_item_block 的
        确定性投影（禁生成式加工）。章节样例/项目上下文仅作少样本风格参考，不得成为内容来源证据；
        关联的确认态需求资产是确认态治理事实，可作事实输入。

        返回信封而非裸撰稿（T20260721）：模型拒绝起草是正常业务结果，走 status='declined' 带
        理由原文，由界面当一等回执呈现。仍抛 InvalidInput（→400）的只剩两种：用错入口（非人工
        撰稿章节的预检拒绝）与模型服务不可用（真故障）。
        """
        doc = self._repo.get_document(project_ref)
        if doc is not None and doc.template_id:
            template = self._template_for_document(doc)
            template_id = str(doc.template_id)
        else:
            template, template_id = self._load_template(template_ref or DEFAULT_TEMPLATE_REF)
        section = template.section(section_key)
        if section is None:
            raise InvalidInput(f"模板章节不存在：{section_key}")
        # 仅 authored_text（可撰稿且非纯 boilerplate）章节可 AI 起草
        if "authored_text" not in section.content_types:
            raise InvalidInput(
                f"章节 {section.number} {section.title} 不支持 AI 起草"
                "（仅人工撰稿 authored_text 章节可起草初稿）"
            )
        doc = self._ensure_document(project_ref, template_id, None)

        assets = self._section_confirmed_assets(doc, section_key)
        examples = list(section.examples)
        log_event(_COMPONENT, "manuscript.draft.started", ok=True, document_ref=str(doc.id),
                  section_key=section_key, asset_count=len(assets), example_count=len(examples),
                  by=operator_ref)
        outcome = self._drafter.draft(
            section_title=section.title,
            section_purpose=section.purpose,
            content_types_text=self._content_types_text(section.content_types),
            assets=assets,
            examples=examples,
        )
        if outcome.failed:
            log_event(_COMPONENT, "manuscript.draft.failed", ok=False, document_ref=str(doc.id),
                      section_key=section_key, error_code="drafter_unavailable")
            raise InvalidInput("AI 起草暂不可用（模型服务未就绪或返回异常），请稍后重试或手动撰写")
        if outcome.reason:
            # 拒绝不写撰稿、不改任何状态：本次调用对治理事实零影响，界面只需把理由显示出来。
            log_event(_COMPONENT, "manuscript.draft.cannot_comply", ok=False, document_ref=str(doc.id),
                      section_key=section_key, asset_count=len(assets), example_count=len(examples))
            return SectionDraftResultRead(status="declined", reason=outcome.reason)
        row = self._repo.upsert_manuscript(str(doc.id), section_key, outcome.draft, operator_ref)
        log_event(_COMPONENT, "manuscript.draft.succeeded", ok=True, document_ref=str(doc.id),
                  section_key=section_key, revision_no=row.revision_no, length=len(outcome.draft),
                  by=operator_ref)
        return SectionDraftResultRead(status="drafted", manuscript=self._manuscript_read(row))

    # ---- P01：候选资产渲染预览（AEP-099；与生成稿同一确定性渲染器）----

    def candidate_preview(self, project_ref: str, asset_type: str, asset_ref: str) -> CandidatePreviewRead:
        if asset_type == "requirement_item":
            items = {str(i.id): i for i in self._repo.get_items([asset_ref])}
            item = items.get(asset_ref)
            if item is None:
                raise NotFound(f"需求条目不存在：{asset_ref}")
            meta = self._build_item_meta(project_ref, {asset_ref: item}).get(asset_ref, {})
            return CandidatePreviewRead(
                asset_type=asset_type, asset_ref=asset_ref,
                title=f"{item.req_no} {_TYPE_LABELS.get(item.req_type, item.req_type)}",
                markdown=_render_item_block(item, meta),
            )
        if asset_type == "chart":
            charts = {str(c.id): c for c in self._repo.get_charts([asset_ref])}
            chart = charts.get(asset_ref)
            if chart is None:
                raise NotFound(f"需求图表不存在：{asset_ref}")
            if chart.format == "markdown_table":
                body = chart.source_code
            else:
                body = f"```{chart.format}\n{chart.source_code}\n```"
            return CandidatePreviewRead(
                asset_type=asset_type, asset_ref=asset_ref, title=chart.title,
                markdown=f"**图：{chart.title}**（{chart.chart_type}）\n{body}",
            )
        if asset_type == "material":
            materials = {str(m.id): m for m in self._repo.get_materials([asset_ref])}
            material = materials.get(asset_ref)
            if material is None:
                raise NotFound(f"支撑材料不存在：{asset_ref}")
            excerpt = material.raw_text[:2000]
            if len(material.raw_text) > 2000:
                excerpt += "\n…（原文节选，全文见材料接入页）"
            return CandidatePreviewRead(
                asset_type=asset_type, asset_ref=asset_ref,
                title=material.source_note or "来源材料",
                markdown=(
                    f"- {material.source_note or '来源材料'}（来源版本 v{material.source_version}）"
                    f"\n\n> 原文节选：\n\n{excerpt}"
                ),
            )
        raise InvalidInput(f"不支持的预览资产类型：{asset_type}")

    # ---- P02：Markdown 生成 / 微调 / 定稿 ----

    def generate_markdown(self, command: GenerateMarkdownCommand) -> MarkdownDraftRead:
        doc = self._repo.get_document(command.project_ref)
        if doc is None or doc.index_version == 0:
            raise RejectedTransition("文档内容索引尚未形成：请先完成索引编排（SCN-005-P01）")
        state = DocState(doc.status)
        if state in (DocState.INDEX_DRAFT, DocState.INDEX_BLOCKED):
            raise RejectedTransition(
                f"内容索引不可生成（{doc.blocked_reason or '索引未就绪'}）：请回到索引编排页处理"
            )
        doc.status = doc_transition(state, DocEvent.GENERATE_MARKDOWN).value

        entries = self._repo.entries_of(str(doc.id), doc.index_version)
        item_refs = [str(e.asset_ref) for e in entries if e.asset_type == "requirement_item" and e.asset_ref]
        items = {str(i.id): i for i in self._repo.get_items(item_refs)}
        # P02-N03 索引引用有效性：条目须仍存在且确认态（失效时回退，不在 P02 修正事实）
        for ref in item_refs:
            item = items.get(ref)
            if item is None or item.status != "confirmed":
                doc.status = DocumentStatus.INDEX_BLOCKED.value
                doc.blocked_reason = f"索引引用失效：条目 {ref} 不存在或已不是确认态"
                raise RejectedTransition(doc.blocked_reason + "；请回到 P01 调整或回条目确认流程")
        chart_refs = [str(e.asset_ref) for e in entries if e.asset_type == "chart" and e.asset_ref]
        charts = {str(c.id): c for c in self._repo.get_charts(chart_refs)}
        # 对称 P02-N03：图表须仍存在且为受控（确认态），失效时回退索引受阻
        for ref in chart_refs:
            chart = charts.get(ref)
            if chart is None or chart.status != "confirmed":
                doc.status = DocumentStatus.INDEX_BLOCKED.value
                doc.blocked_reason = f"索引引用失效：图表 {ref} 不存在或已不是受控图表"
                raise RejectedTransition(doc.blocked_reason + "；请回到 P01 调整或回图表核对确认流程")
        material_refs = [str(e.asset_ref) for e in entries if e.asset_type == "material" and e.asset_ref]
        materials = {str(m.id): m for m in self._repo.get_materials(material_refs)}

        # 生成用编排时冻结的注册行快照（模板后续升级不影响已编排文档）；无注册行时兜底重解析
        template = self._template_for_document(doc)
        project_name = self._repo.get_project_name(str(doc.project_id)) or doc.title
        item_meta = self._build_item_meta(str(doc.project_id), items)
        manuscripts = {m.section_key: m.content for m in self._repo.manuscripts_of(str(doc.id))}
        knowledge_by_type = self._build_knowledge_by_type(str(doc.project_id), template)
        content, bindings = _render_markdown(
            template, entries, items, materials, charts,
            project_name=project_name, coverage_scope=doc.coverage_scope or "",
            item_meta=item_meta, manuscripts=manuscripts,
            knowledge_by_type=knowledge_by_type,
        )
        draft = self._repo.create_draft(
            str(doc.id), doc.index_version, content, json.dumps(bindings, ensure_ascii=False),
        )
        log_event(_COMPONENT, "markdown.generated", ok=True, document_ref=str(doc.id),
                  draft_version=draft.version_no, line_count=content.count("\n"))
        return self._draft_read(draft)

    def _build_item_meta(self, project_ref: str, items: dict) -> dict[str, dict]:
        """条目块属性表的治理事实上下文（来源材料链 + 有效图表覆盖），全部只读。"""
        element_refs_by_item: dict[str, list[str]] = {
            ref: [str(r) for r in json.loads(item.source_element_refs or "[]")]
            for ref, item in items.items()
        }
        all_element_refs = sorted({r for refs in element_refs_by_item.values() for r in refs})
        elements = {str(e.id): e for e in self._repo.elements_by_refs(all_element_refs)}
        pr_refs = sorted({str(e.parse_result_ref) for e in elements.values() if e.parse_result_ref})
        parse_results = {str(p.id): p for p in self._repo.parse_results_by_refs(pr_refs)}
        mat_refs = sorted({
            str(p.material_ref) for p in parse_results.values() if p.material_ref
        })
        src_materials = {str(m.id): m for m in self._repo.get_materials(mat_refs)}
        chart_titles = self._repo.effective_chart_titles_by_item(project_ref)

        meta: dict[str, dict] = {}
        for ref in items:
            sources: list[str] = []
            seen: set[str] = set()
            for element_ref in element_refs_by_item.get(ref, []):
                element = elements.get(element_ref)
                if element is None:
                    continue
                parse_result = parse_results.get(str(element.parse_result_ref))
                material = src_materials.get(str(parse_result.material_ref)) if parse_result else None
                if material is None or str(material.id) in seen:
                    continue
                seen.add(str(material.id))
                sources.append(f"{material.source_note or '来源材料'}（材料 v{material.source_version}）")
            meta[ref] = {
                "sources": sources,
                "element_count": len(element_refs_by_item.get(ref, [])),
                "charts": chart_titles.get(ref, []),
            }
        return meta

    def _build_knowledge_by_type(
        self, project_ref: str, template: TemplateDescriptor
    ) -> dict[str, list[tuple]]:
        """知识表投影上下文（P5 / 07 §1.2）：确认态业务知识按类型分组 + 来源材料标题，只读。

        仅加载模板实际声明的知识 token 覆盖的类型；每类按名称规范化序排（类型声明序由渲染层控）。
        """
        needed_types: list[str] = []
        for s in template.sections:
            token = _knowledge_token_of(s)
            if token is not None:
                for t in _KNOWLEDGE_PROJECTION[token]["types"]:
                    if t not in needed_types:
                        needed_types.append(t)
        if not needed_types:
            return {}
        elements = self._repo.confirmed_business_elements(project_ref, needed_types)
        # 来源材料标题：element → parse_result → material（与 _build_item_meta 同链，不复制原文）
        pr_refs = sorted({str(e.parse_result_ref) for e in elements if e.parse_result_ref})
        parse_results = {str(p.id): p for p in self._repo.parse_results_by_refs(pr_refs)}
        mat_refs = sorted({str(p.material_ref) for p in parse_results.values() if p.material_ref})
        materials = {str(m.id): m for m in self._repo.get_materials(mat_refs)}

        def source_title(element) -> str:
            pr = parse_results.get(str(element.parse_result_ref))
            mat = materials.get(str(pr.material_ref)) if pr else None
            return mat.source_note or "来源材料" if mat else ""

        by_type: dict[str, list[tuple]] = {}
        for e in elements:
            by_type.setdefault(e.element_type, []).append((e, source_title(e)))
        for t in by_type:
            by_type[t].sort(key=lambda pair: normalize_element_name(pair[0].content))
        return by_type

    def record_edit(self, command: MarkdownEditCommand) -> MarkdownEditResult:
        draft = self._repo.get_draft(command.draft_ref)
        if draft is None:
            raise NotFound("Markdown 稿不存在")
        if draft.status not in ("draft",):
            raise RejectedTransition(f"当前稿状态 {draft.status} 不可编辑：请重新生成或回到索引编排")

        doc = self._repo.get_document_by_ref(str(draft.document_ref))
        bindings = json.loads(draft.source_bindings)
        entries = self._repo.entries_of(str(draft.document_ref), draft.index_version)
        item_refs = [str(e.asset_ref) for e in entries if e.asset_type == "requirement_item" and e.asset_ref]
        items = self._repo.get_items(item_refs)
        corpus = [i.expression for i in items]
        corpus += [m.raw_text for m in self._repo.materials(str(doc.project_id))]
        corpus += [m.content for m in self._repo.manuscripts_of(str(doc.id))]
        corpus.append(draft.generated_content)

        classified = _classify_edits(draft.generated_content, command.content, bindings, corpus)
        # 每次提交按生成稿基准重算补丁集：旧 pending 补丁作废，避免叠加歧义
        self._repo.discard_pending_patches(str(draft.id), note="编辑集已按新提交重算")
        patches = [
            self._repo.add_patch(
                str(draft.id), p["impact"], p["before"], p["after"],
                p["bound_item_ref"], command.operator_ref,
            ) for p in classified
        ]
        draft.content = command.content
        block_reasons = sorted({_BLOCKING_IMPACTS[p.impact] for p in patches if p.impact in _BLOCKING_IMPACTS})
        draft.block_reasons = json.dumps(block_reasons, ensure_ascii=False) if block_reasons else None
        pending_items = sorted({str(p.bound_item_ref) for p in patches if p.bound_item_ref})

        log_event(_COMPONENT, "markdown.edit.recorded", ok=True, draft_ref=str(draft.id),
                  patch_count=len(patches), blocked=bool(block_reasons),
                  touched_confirmed_items=len(pending_items))
        return MarkdownEditResult(
            status="recorded", draft_ref=str(draft.id),
            patches=[self._patch_read(p) for p in patches],
            can_finalize=not block_reasons,
            block_reasons=block_reasons,
            pending_item_refs=pending_items,
            next_action="存在不可定稿项：先处理后再定稿" if block_reasons
            else ("定稿时将展示待修订确认态条目清单" if pending_items else None),
        )

    def finalize_markdown(self, command: FinalizeMarkdownCommand) -> FinalizeMarkdownResult:
        draft = self._repo.get_draft(command.draft_ref)
        if draft is None:
            raise NotFound("Markdown 稿不存在")
        if draft.status == "finalized":
            return FinalizeMarkdownResult(
                status="finalized", draft_ref=str(draft.id),
                next_action="该稿已定稿（幂等重放）：可进入 docx 导出",
            )
        if draft.status != "draft":
            raise RejectedTransition(f"当前稿状态 {draft.status} 不可定稿：请重新生成后再试")

        doc = self._repo.get_document_by_ref(str(draft.document_ref))
        patches = self._repo.pending_patches(str(draft.id))
        blockers = sorted({_BLOCKING_IMPACTS[p.impact] for p in patches if p.impact in _BLOCKING_IMPACTS})
        if blockers:
            draft.block_reasons = json.dumps(blockers, ensure_ascii=False)
            log_event(_COMPONENT, "markdown.finalize.blocked", ok=False,
                      draft_ref=str(draft.id), reasons=len(blockers))
            return FinalizeMarkdownResult(
                status="blocked", draft_ref=str(draft.id), block_reasons=blockers,
                next_action="删除无来源编辑、回材料补充或回索引编排后重试",
            )

        item_patches = [p for p in patches if p.impact == EditImpact.CONFIRMED_ITEM.value]
        if item_patches and not command.confirm_reflow:
            # N11 定稿前修订清单确认：未经用户确认不得提交条目修订回流
            return FinalizeMarkdownResult(
                status="pending_item_confirmation", draft_ref=str(draft.id),
                pending_items=[self._patch_read(p) for p in item_patches],
                next_action="确认待修订确认态条目清单后回流（SCN-003-P03），或退回窗口继续编辑",
            )
        if item_patches:
            # N12 修订回流：新的待确认 LDM-007；旧确认态不原地覆盖；当前稿等待收束
            reflowed: list[str] = []
            for p in item_patches:
                source = self._repo.get_item(str(p.bound_item_ref))
                if source is None:
                    raise NotFound(f"待修订条目不存在：{p.bound_item_ref}")
                new_expression = _extract_expression(p.after_text) or source.expression
                new_item = self._repo.create_reflow_item(source, new_expression)
                p.status = "reflowed"
                p.reflow_item_ref = new_item.id
                reflowed.append(str(new_item.id))
            draft.status = "awaiting_item_revision"
            draft.can_export = False
            log_event(_COMPONENT, "markdown.finalize.reflowed", ok=True,
                      draft_ref=str(draft.id), reflow_count=len(reflowed))
            return FinalizeMarkdownResult(
                status="item_revision_reflowed", draft_ref=str(draft.id),
                reflowed_item_refs=reflowed,
                next_action="条目修订已回流为新的待确认条目：完成确认后回到 P01/P02 重新生成再定稿",
            )

        for p in patches:
            p.status = "finalized"
        draft.status = "finalized"
        draft.can_export = True
        draft.finalized_by = command.operator_ref
        draft.finalized_at = datetime.now(timezone.utc)
        doc.status = doc_transition(DocState(doc.status), DocEvent.FINALIZE_MARKDOWN).value
        log_event(_COMPONENT, "markdown.finalized", ok=True, draft_ref=str(draft.id),
                  document_ref=str(doc.id), patch_count=len(patches))
        return FinalizeMarkdownResult(
            status="finalized", draft_ref=str(draft.id),
            next_action="Markdown 已定稿：可发起候选 docx 导出（SCN-005-P03）",
        )

    def reopen_index(self, command: ReopenIndexCommand) -> RequirementDocumentRead:
        doc = self._repo.get_document(command.project_ref)
        if doc is None:
            raise NotFound("需求文档不存在")
        doc.status = doc_transition(DocState(doc.status), DocEvent.REOPEN_INDEX).value
        self._repo.supersede_open_drafts(str(doc.id))
        log_event(_COMPONENT, "index.reopened", ok=True, document_ref=str(doc.id))
        template_read, _ = self._read_document_template(doc)
        return self._doc_read(doc, template_read)

    # ---- 需求条目最小确认门禁（SCN-003 完整评审链另行承接；写权威=需求条目服务侧）----

    def confirm_item(self, command: ItemConfirmCommand) -> ItemConfirmResult:
        item = self._repo.get_item(command.item_ref)
        if item is None:
            raise NotFound("需求条目不存在")
        if item.status == "confirmed":
            return ItemConfirmResult(
                status="confirmed", item_ref=str(item.id), item_status=item.status,
                next_action="条目已是确认态（幂等重放）",
            )
        nxt = item_transition(ItemState(item.status), ItemEvent.CONFIRM)
        assert nxt is ItemState.CONFIRMED
        self._repo.confirm_item(item)
        log_event(_COMPONENT, "item.confirmed.min_gate", ok=True, item_ref=str(item.id))
        return ItemConfirmResult(
            status="confirmed", item_ref=str(item.id), item_status=item.status,
            next_action="条目已确认：可进入发布候选资产池",
        )

    # ---- 读视图组装 ----

    def _doc_read(self, doc, template_read: TemplateDescriptorRead) -> RequirementDocumentRead:
        return RequirementDocumentRead(
            document_ref=str(doc.id), doc_type=doc.doc_type, title=doc.title,
            template_ref=template_read.template_ref,
            template_schema_version=template_read.schema_version or "",
            coverage_scope=doc.coverage_scope, status=doc.status,
            blocked_reason=doc.blocked_reason, index_version=doc.index_version,
        )

    def _draft_read(self, draft) -> MarkdownDraftRead:
        return MarkdownDraftRead(
            draft_ref=str(draft.id), version_no=draft.version_no,
            index_version=draft.index_version, status=draft.status,
            can_export=draft.can_export, content=draft.content,
            source_bindings=[SourceBindingRead(**b) for b in json.loads(draft.source_bindings)],
            block_reasons=json.loads(draft.block_reasons) if draft.block_reasons else [],
            patches=[self._patch_read(p) for p in self._all_patches(draft)],
            finalized_by=draft.finalized_by,
            finalized_at=draft.finalized_at.isoformat() if draft.finalized_at else None,
        )

    def _all_patches(self, draft) -> list:
        pending = self._repo.pending_patches(str(draft.id))
        if pending or draft.status == "draft":
            return pending
        from sqlalchemy import select

        from app.db.models import MarkdownPatch
        stmt = (
            select(MarkdownPatch)
            .where(MarkdownPatch.draft_ref == draft.id, MarkdownPatch.status.in_(("finalized", "reflowed")))
            .order_by(MarkdownPatch.created_at)
        )
        return list(self._repo._session.scalars(stmt).all())

    def _patch_read(self, p) -> MarkdownPatchRead:
        return MarkdownPatchRead(
            patch_ref=str(p.id), impact=p.impact, before_text=p.before_text,
            after_text=p.after_text,
            bound_item_ref=str(p.bound_item_ref) if p.bound_item_ref else None,
            reflow_item_ref=str(p.reflow_item_ref) if p.reflow_item_ref else None,
            status=p.status, note=p.note,
        )

    def _reconcile_stale_exports(self, document_id: str) -> None:
        """读侧自愈：把卡死在 converting 的导出行落终态 failed（幂等）。

        两类卡死来源：
        1) 未预期异常曾使请求 session 的 converting 行被 worker rollback 前遗留 → 其 AgentRun=failed；
        2) Redis 配了但 worker 缺席，任务静默排队 → AgentRun 仍 queued/started，用超时兜底。
        仅落脱敏通用文案，改写留结构化日志（不写异常原文/敏感数据，遵守硬规则 8）。
        """
        from app.repositories.agent_run import SqlAgentRunRepository

        agent_runs = SqlAgentRunRepository(self._repo.session)
        now = datetime.now(timezone.utc)
        changed = False
        for x in self._repo.exports_of(document_id):
            if x.status != "converting":
                continue
            reason: str | None = None
            run = agent_runs.find_by_context(str(x.id), "docx_export")
            if run is not None and run.status == "failed":
                reason = "转换任务已失败，请重试；如持续失败可登记人工降级导出件。"
            elif x.created_at is not None:
                created = x.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if now - created > timedelta(minutes=_EXPORT_CONVERT_TIMEOUT_MIN) and not x.file_path:
                    reason = "转换超时未完成，请重试；如持续失败可登记人工降级导出件。"
            if reason is not None:
                x.status = "failed"
                x.failure_reason = reason
                changed = True
                log_event(_COMPONENT, "export.reconciled", level="WARN", ok=False,
                          export_ref=str(x.id))
        if changed:
            self._repo.commit()

    def _export_read(self, x) -> DocxExportRead:
        return DocxExportRead(
            export_ref=str(x.id), draft_ref=str(x.draft_ref), status=x.status,
            failure_reason=x.failure_reason, manual_fallback=x.manual_fallback,
            check_note=x.check_note,
            file_available=bool(x.file_path and Path(x.file_path).exists()),
            created_at=x.created_at.isoformat() if x.created_at else "",
        )

    def _baseline_read(self, b) -> ReleaseBaselineRead:
        row = self._repo.get_template_row(str(b.template_id))
        return ReleaseBaselineRead(
            baseline_ref=str(b.id), document_ref=str(b.document_ref),
            index_version=b.index_version, draft_ref=str(b.draft_ref),
            template_ref=row.template_key if row else "",
            template_schema_version=row.schema_version if row else "",
            export_ref=str(b.export_ref), manual_fallback=b.manual_fallback,
            asset_refs=json.loads(b.asset_refs), confirmed_by=b.confirmed_by,
            confirmed_at=b.created_at.isoformat() if b.created_at else "", note=b.note,
        )

    def _next_action(self, doc, markdown, template_read) -> str | None:
        if template_read.error:
            return "模板文件不可用：更换模板或修复模板文件"
        if doc is None:
            return "从候选资产池勾选确认态资产，保存文档内容索引（SCN-005-P01）"
        status = doc.status
        if status == DocumentStatus.INDEX_BLOCKED.value:
            return "索引受阻：按缺失清单补建/确认后重新编排"
        if status == DocumentStatus.INDEX_READY.value:
            return "索引就绪：生成 Markdown 中间稿（SCN-005-P02）"
        if status == DocumentStatus.MARKDOWN_DRAFT.value:
            if markdown and markdown.status == "awaiting_item_revision":
                return "等待条目修订收束：完成确认后重新生成"
            return "在 Markdown 窗口核对与微调后确认定稿"
        if status == DocumentStatus.MARKDOWN_FINALIZED.value:
            return "定稿完成：生成候选 docx 并检查（SCN-005-P03）"
        if status == DocumentStatus.BASELINE_PUBLISHED.value:
            return "发布基线已形成：只读复核；改动需走新一轮 P01/P02/P03"
        return None


_REQ_NO_PREFIX = re.compile(r"^\*\*[^*]+\*\*（[^）]*）\s*")


def _extract_expression(after_text: str) -> str:
    """从条目补丁的编辑后文本还原新表达（去掉编号/类型前缀装饰）。

    条目块含标题行与属性表：跳过表格行（属性表编辑不构成新表达，回退原表达），
    取首个剥离前缀后仍有内容的正文行。
    """
    for raw in after_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("|"):
            continue
        candidate = _REQ_NO_PREFIX.sub("", line).strip()
        if candidate:
            return candidate
    return ""


# ============================================================================
# 导出执行服务（P03）
# ============================================================================


class ExportExecutionService:
    """导出执行服务：导出前一致性校验、docx 转换承接、检查结论、发布基线确认。"""

    def __init__(self, repo: SqlPublicationRepository, agent_runs=None, enqueue=None) -> None:
        self._repo = repo
        self._agent_runs = agent_runs
        self._enqueue = enqueue

    def start_export(self, command: StartDocxExportCommand) -> StartDocxExportResult:
        replay = self._repo.find_export_by_idempotency(command.idempotency_key)
        if replay is not None:
            return StartDocxExportResult(
                status="submitted", export_ref=str(replay.id),
                next_action="导出请求已受理（幂等重放）",
            )
        draft = self._repo.get_draft(command.draft_ref)
        if draft is None:
            raise NotFound("Markdown 定稿版本不存在")
        # N03 导出前一致性校验：必须是可导出的定稿版本（未定稿不得生成候选 docx）
        if draft.status != "finalized" or not draft.can_export:
            return StartDocxExportResult(
                status="rejected_precheck",
                next_action=f"Markdown 未定稿或不可导出（当前状态 {draft.status}）："
                            "回到 P02 完成定稿，或回 P01 调整内容索引",
            )
        # 在途去重：同一定稿已有 converting 导出时，复用在途任务而非再建行/再入队（防重复点击/重复提交）
        inflight = self._repo.find_active_conversion(str(draft.id))
        if inflight is not None:
            log_event(_COMPONENT, "export.dedup_inflight", ok=True,
                      export_ref=str(inflight.id), draft_ref=str(draft.id))
            return StartDocxExportResult(
                status="submitted", export_ref=str(inflight.id),
                next_action="候选 docx 正在转换中：已复用在途任务，请等待完成后再检查",
            )
        doc = self._repo.get_document_by_ref(str(draft.document_ref))
        export = self._repo.create_export(
            str(doc.id), str(draft.id), "converting", command.operator_ref, command.idempotency_key,
        )
        run_ref = None
        if self._agent_runs is not None and self._enqueue is not None:
            run_ref = self._agent_runs.create("docx_export", context_ref=str(export.id))
            self._repo.commit()  # 落库后再入队：worker/inline 任务用独立 session
            self._enqueue(str(export.id), run_ref)
        else:
            run_docx_export_judgement(self._repo, str(export.id))
        log_event(_COMPONENT, "export.submitted", ok=True, export_ref=str(export.id),
                  draft_ref=str(draft.id), run_id=run_ref)
        return StartDocxExportResult(
            status="submitted", export_ref=str(export.id), agent_run_ref=run_ref,
            next_action="候选 docx 生成中：完成后检查样式并决定是否确认发布",
        )

    def report_check(self, command: ExportCheckCommand) -> DocxExportRead:
        export = self._repo.get_export(command.export_ref)
        if export is None:
            raise NotFound("候选导出件不存在")
        if export.status not in ("succeeded", "manual_fallback", "check_rejected"):
            raise RejectedTransition(f"导出件状态 {export.status} 不可承接检查结论")
        if command.passed:
            export.check_note = command.note or "检查通过"
        else:
            export.status = "check_rejected"
            export.check_note = command.note or "检查不通过"
        log_event(_COMPONENT, "export.checked", ok=command.passed, export_ref=str(export.id))
        svc = DocumentOrchestrationService(self._repo)
        return svc._export_read(export)

    def register_manual_fallback(self, command: ManualFallbackCommand) -> DocxExportRead:
        draft = self._repo.get_draft(command.draft_ref)
        if draft is None:
            raise NotFound("Markdown 定稿版本不存在")
        if not self._repo.has_failed_export(str(draft.id)):
            raise RejectedTransition("仅在系统转换失败后才可登记人工降级导出件")
        replay = self._repo.find_export_by_idempotency(command.idempotency_key)
        if replay is not None:
            return DocumentOrchestrationService(self._repo)._export_read(replay)
        export = self._repo.create_export(
            str(draft.document_ref), str(draft.id), "manual_fallback",
            command.operator_ref, command.idempotency_key,
            manual_fallback=True, failure_reason=None,
        )
        export.check_note = f"人工降级登记：{command.reason}（非系统转换成功）"
        log_event(_COMPONENT, "export.manual_fallback", ok=True, export_ref=str(export.id))
        return DocumentOrchestrationService(self._repo)._export_read(export)

    def confirm_baseline(self, command: ConfirmBaselineCommand) -> ConfirmBaselineResult:
        export = self._repo.get_export(command.export_ref)
        if export is None:
            raise NotFound("候选导出件不存在")
        if export.status == "baseline_confirmed":
            baseline = self._repo.baseline_of(str(export.document_ref))
            return ConfirmBaselineResult(
                status="confirmed", baseline_ref=str(baseline.id) if baseline else None,
                next_action="发布基线已形成（幂等重放）",
            )
        if export.status not in ("succeeded", "manual_fallback"):
            return ConfirmBaselineResult(
                status="rejected_precheck",
                next_action=f"候选件状态 {export.status} 不可确认发布：重试导出或回 P02/P01 处理",
            )
        doc = self._repo.get_document_by_ref(str(export.document_ref))
        doc.status = doc_transition(DocState(doc.status), DocEvent.CONFIRM_BASELINE).value
        draft = self._repo.get_draft(str(export.draft_ref))
        entries = self._repo.entries_of(str(doc.id), draft.index_version)
        asset_refs = [
            f"{e.asset_type}:{e.asset_ref}@v{e.asset_version}" for e in entries if e.asset_ref
        ]
        baseline = self._repo.create_baseline(
            str(doc.id), draft.index_version, str(draft.id),
            str(doc.template_id), str(export.id),
            export.manual_fallback, json.dumps(asset_refs, ensure_ascii=False),
            command.operator_ref, command.note,
        )
        export.status = "baseline_confirmed"
        # 审计留痕：谁在何时确认了哪个候选件（基线快照即冻结记录）
        log_event(
            _COMPONENT, "baseline.confirmed", ok=True, baseline_ref=str(baseline.id),
            export_ref=str(export.id), manual_fallback=export.manual_fallback,
            confirmed_by=command.operator_ref,
        )
        return ConfirmBaselineResult(
            status="confirmed", baseline_ref=str(baseline.id),
            next_action="发布基线已冻结：可只读复核与下载；后续改动走新一轮 P01/P02/P03",
        )


def run_docx_export_judgement(repo: SqlPublicationRepository, export_ref: str) -> None:
    """docx 转换执行（worker/inline 共用）：成功登记候选件，失败登记原因（业务停靠，不抛）。"""
    # 落盘目录取设置页保存的导出目录，无配置行时回落 env（T20260724：此前只读 env，
    # 页面上保存的导出目录后端从不读取）。局部 import 避开服务层之间的循环依赖。
    from app.services.config_registry import resolve_export_dir

    export = repo.get_export(export_ref)
    if export is None:
        raise NotFound(f"导出任务不存在：{export_ref}")
    draft = repo.get_draft(str(export.draft_ref))
    doc = repo.get_document_by_ref(str(export.document_ref))
    try:
        row = repo.get_template_row(str(doc.template_id)) if doc.template_id else None
        if row is None:
            raise TemplateError(f"文档绑定的模板登记行不存在：{doc.template_id}")
        template = parse_template(row.content, row.template_key)
        out = Path(resolve_export_dir(repo.session)) / f"{export.id}.docx"
        convert_markdown_to_docx(
            draft.content, out, template.export_binding,
            {"title": doc.title, "project_name": doc.coverage_scope or "",
             "version": f"V1.{draft.version_no}"},
        )
        export.status = "succeeded"
        export.file_path = str(out)
        log_event(_COMPONENT, "export.converted", ok=True, export_ref=str(export.id))
    except (ConversionError, TemplateError) as exc:
        export.status = "failed"
        export.failure_reason = str(exc)
        log_event(_COMPONENT, "export.failed", level="WARN", ok=False,
                  export_ref=str(export.id), error_code=type(exc).__name__)
        # 通知徽标（04A §2.1）：导出失败需人工降级 → 同事务落通知（不带异常原文）
        from app.services.notification import notify_export_failed

        notify_export_failed(repo.session, str(export.id), doc.title, str(doc.project_id))
    except Exception as exc:  # noqa: BLE001
        # 兜底：任何未预期异常（写盘 OSError、缺样式 KeyError、运行时错误等）都必须让导出行落终态，
        # 绝不外抛 —— 否则 worker 骨架会 rollback 请求 session 里已提交的 "converting" 行，遗留卡死。
        # 只落脱敏通用文案（不写异常原文/敏感数据，遵守 AGENTS.md 硬规则 8），错误码走结构化日志。
        export.status = "failed"
        if isinstance(exc, OSError):
            # 写盘类失败（目录建不出来、没有写权限、磁盘满）单独给话：配置没改之前，
            # 让用户「重试」永远失败，得把他指到能改的那个地方去。PermissionError 是 OSError 的子类。
            export.failure_reason = (
                "导出目录无法写入，请到 设置 → 导出能力 检查导出目录配置后重试。"
            )
        else:
            export.failure_reason = "转换发生未预期错误，请重试；如持续失败可登记人工降级导出件。"
        log_event(_COMPONENT, "export.failed", level="ERROR", ok=False,
                  export_ref=str(export.id), error_code=type(exc).__name__)
        try:
            from app.services.notification import notify_export_failed

            notify_export_failed(repo.session, str(export.id),
                                 getattr(doc, "title", ""), str(getattr(doc, "project_id", "")))
        except Exception:  # noqa: BLE001 —— 通知尽力而为，绝不因通知失败再次抛出而破坏终态落库
            log_event(_COMPONENT, "export.failed.notify_skipped", level="WARN", ok=False,
                      export_ref=str(export.id))
