"""P5 文档发布知识投影测试义务（09 §2 P5 测试表；设计 07）。

覆盖：切分函数正/负例；四类知识表渲染（列/排序/增强面向"—"/来源材料/类别）；
空集占位 + missing_list（非阻断）；知识行编辑闸（OTHER_ASSET 阻断）；
v2 模板 token 放行 / 未知 token 拒绝 / knowledge 章节 authoring_capable=False；v1 回归零知识表。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401
from app.adapters.doc_template import KNOWLEDGE_CONTENT_TYPES, TemplateError, parse_template
from app.api.schemas import (
    DocIndexEntryRead,
    GenerateMarkdownCommand,
    MarkdownEditCommand,
    SaveIndexCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import Material, MaterialParseResult, Project, RequirementElement, RequirementItem
from app.domain.naming import split_name_definition
from app.repositories.publication import SqlPublicationRepository
from app.scripts.import_packaged_templates import import_packaged_templates
from app.services.publication import (
    _KNOWLEDGE_PROJECTION,
    _render_knowledge_table,
    DocumentOrchestrationService,
)

V2 = "srs-iso29148-v2"


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    import_packaged_templates(s)
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# ---- 切分函数（naming.split_name_definition）----

def test_split_name_definition_cases():
    assert split_name_definition("履约单是指从下单到出库的一次完整订单处理流程") == (
        "履约单", "从下单到出库的一次完整订单处理流程")
    assert split_name_definition("订单管理员") == ("订单管理员", "")  # 无分隔 → 余文空
    assert split_name_definition("WMS：仓储管理系统") == ("WMS", "仓储管理系统")
    assert split_name_definition("  留白  ") == ("留白", "")
    assert split_name_definition("") == ("", "")


# ---- 纯渲染（_render_knowledge_table）----

class _El:
    def __init__(self, content, element_type):
        self.content = content
        self.element_type = element_type


def test_render_term_table_split_and_dash():
    rows = [(_El("履约单是指从下单到出库", "term"), "评审纪要")]
    md = _render_knowledge_table("knowledge:term_table", rows)
    assert "| 术语名 | 定义 | 适用范围或同义词 | 来源材料 |" in md
    assert "| 履约单 | 从下单到出库 | — | 评审纪要 |" in md  # 增强面向恒 —


def test_render_participant_table_category_and_source_dash():
    rows = [
        (_El("订单管理员：负责审核大额订单", "role"), ""),
        (_El("外部支付网关", "external_system"), "接口清单"),
    ]
    md = _render_knowledge_table("knowledge:participant_table", rows)
    assert "| 订单管理员 | 角色 | 负责审核大额订单 | — |" in md  # 冒号切分职责；无来源 → —
    assert "| 外部支付网关 | 外部系统 | — | 接口清单 |" in md  # 无余文 → 职责 —


def test_render_statement_tables_verbatim():
    rule = "单笔订单金额超过一万元的须经部门经理审批，依据订单管理办法"
    md = _render_knowledge_table("knowledge:business_rule_table", [(_El(rule, "business_rule"), "制度")])
    assert f"| {rule} | — | — | 制度 |" in md  # 整条陈述逐字投影


# ---- 集成：v2 建档 → 生成 → 四章节投影 ----

def _seed(session, *, with_knowledge=True):
    p = Project(name="知识投影项目")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text="源文", source_note="评审纪要 2026-06")
    session.add(mat)
    session.flush()
    pr = MaterialParseResult(
        project_id=p.id, material_ref=mat.id, context_ref=uuid.uuid4(), parse_status="parsed")
    session.add(pr)
    session.flush()

    def elem(content, etype, status="confirmed"):
        e = RequirementElement(
            project_id=p.id, parse_result_ref=pr.id, element_type=etype,
            content=content, source_anchor=content, process_status=status)
        session.add(e)
        session.flush()
        return str(e.id)

    def item(req_no, expr, rtype):
        r = RequirementItem(
            project_id=p.id, parse_result_ref=pr.id, formation_context_ref=uuid.uuid4(),
            req_no=req_no, expression=expr, req_type=rtype, status="confirmed",
            source_element_refs="[]")
        session.add(r)
        session.flush()
        return str(r.id)

    w = {
        "project": str(p.id), "material": str(mat.id),
        "fr": item("FR-001", "系统应导出 docx", "functional"),
        "q": item("NFR-001", "响应不超两秒", "quality"),
    }
    if with_knowledge:
        # 乱序录入 → 验证渲染按名称规范化排序
        w["term_b"] = elem("履约单是指从下单到出库的一次完整订单处理流程", "term")
        w["term_a"] = elem("SKU：最小库存单位", "term")
        w["role"] = elem("订单管理员：负责审核大额订单", "role")
        w["extsys"] = elem("外部支付网关", "external_system")
        w["rule"] = elem("单笔订单金额超过一万元的须经部门经理审批", "business_rule")
        w["assume"] = elem("假设订单基础数据由上游 ERP 保证准确", "assumption")
        w["term_pending"] = elem("待确认术语", "term", status="pending_confirmation")
    session.commit()
    return w


def _entries(w):
    return [
        DocIndexEntryRead(section_key="requirements.functional", asset_type="requirement_item",
                          asset_ref=w["fr"], order_no=0),
        DocIndexEntryRead(section_key="requirements.quality", asset_type="requirement_item",
                          asset_ref=w["q"], order_no=0),
    ]


def _generate_v2(session, w):
    svc = DocumentOrchestrationService(SqlPublicationRepository(session))
    save = svc.save_content_index(SaveIndexCommand(
        project_ref=w["project"], template_ref=V2, coverage_scope="release-v0.1",
        entries=_entries(w), operator_ref="U1", idempotency_key=f"idx-{uuid.uuid4()}"))
    session.commit()
    draft = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=f"gen-{uuid.uuid4()}"))
    session.commit()
    return svc, save, draft


def test_v2_projects_four_knowledge_sections(session):
    w = _seed(session)
    _, save, draft = _generate_v2(session, w)
    assert save.status == "index_ready"  # 知识空集非阻断，此处有知识
    md = draft.content
    # 四章节标题在
    for title in ("定义与术语", "参与者与外部接口环境", "业务规则", "假设与依赖"):
        assert title in md
    # 逐字一致（AC-P5-01）：确认态内容对应部分完全一致
    assert "| 履约单 | 从下单到出库的一次完整订单处理流程 |" in md
    assert "单笔订单金额超过一万元的须经部门经理审批" in md
    assert "假设订单基础数据由上游 ERP 保证准确" in md
    assert "| 订单管理员 | 角色 |" in md and "| 外部支付网关 | 外部系统 |" in md
    # 来源材料列引用锚点材料标题（不复制原文）
    assert "评审纪要 2026-06" in md
    # 待确认术语不投影
    assert "待确认术语" not in md


def test_v2_term_table_sorted_by_normalized_name(session):
    w = _seed(session)
    _, _, draft = _generate_v2(session, w)
    md = draft.content
    # SKU（s）规范化序在 履约单（l… 实为中文，按 lower 后码位）之前？按 normalize_element_name 排序
    assert md.index("SKU") < md.index("履约单")


def test_empty_project_placeholder_and_missing_list(session):
    w = _seed(session, with_knowledge=False)
    svc, save, draft = _generate_v2(session, w)
    assert save.status == "index_ready"  # 知识空集不阻断（AC-P5-02）
    md = draft.content
    assert "（本项目暂无已确认术语）" in md
    assert "（本项目暂无已确认参与者）" in md
    assert "（本项目暂无已确认业务规则）" in md
    assert "（本项目暂无已确认假设与依赖）" in md
    # 缺失清单出现四类知识缺项（非阻断）
    sections = {m.section_key for m in save.missing_list}
    assert {"intro.terms", "overview.participants",
            "overview.business_rules", "overview.assumptions"} <= sections


def test_knowledge_row_edit_blocked(session):
    w = _seed(session)
    svc, _, draft = _generate_v2(session, w)
    tampered = draft.content.replace(
        "从下单到出库的一次完整订单处理流程", "被篡改的定义")
    assert tampered != draft.content
    result = svc.record_edit(MarkdownEditCommand(
        project_ref=w["project"], draft_ref=draft.draft_ref, content=tampered,
        operator_ref="U1", idempotency_key=f"edit-{uuid.uuid4()}"))
    assert result.can_finalize is False
    assert any("正式资产" in r for r in result.block_reasons)  # OTHER_ASSET 阻断语义


# ---- 模板校验（doc_template）----

def test_template_accepts_knowledge_tokens_rejects_unknown():
    base = {
        "template_id": "t", "schema_version": "1.0", "doc_type": "srs", "title": "T",
        "export_binding": {"body_font_east_asia": "宋体", "body_size_pt": 12,
                           "first_line_indent_chars": 2, "heading_sizes_pt": {"1": 16}},
    }

    def sec(ct):
        return {"key": "s", "number": "1", "title": "S", "level": 1, "purpose": "p",
                "content_types": ct, "required": False, "repeatable": False,
                "missing_policy": "skip"}

    ok = parse_template(json.dumps({**base, "sections": [sec(list(KNOWLEDGE_CONTENT_TYPES))]}))
    assert ok.sections[0].content_types == KNOWLEDGE_CONTENT_TYPES
    # knowledge 章节不可撰稿
    assert ok.sections[0].authoring_capable() is False
    with pytest.raises(TemplateError):
        parse_template(json.dumps({**base, "sections": [sec(["knowledge:bogus"])]}))


def test_v2_docx_export_renders_knowledge_table(session, tmp_path):
    """AC-P5-04：v2 生成物含知识表 → docx 转换为 Word 表格（列与 Markdown 一致）。"""
    from docx import Document as DocxDocument

    from app.adapters.docx_convert import convert_markdown_to_docx

    w = _seed(session)
    _, _, draft = _generate_v2(session, w)
    out = convert_markdown_to_docx(
        draft.content, tmp_path / "srs.docx",
        binding={"body_size_pt": 12, "first_line_indent_chars": 2}, meta={"title": "SRS"})
    docx = DocxDocument(str(out))
    headers = {tuple(c.text for c in t.rows[0].cells) for t in docx.tables if t.rows}
    assert ("术语名", "定义", "适用范围或同义词", "来源材料") in headers
    assert ("名称", "类别", "职责或交互目的", "来源材料") in headers


def test_v1_document_has_no_knowledge_tables(session):
    """AC-P5-05 回归：v1 模板生成物不含任何知识表，行为不变。"""
    w = _seed(session)
    svc = DocumentOrchestrationService(SqlPublicationRepository(session))
    svc.save_content_index(SaveIndexCommand(
        project_ref=w["project"], coverage_scope="release-v0.1",
        entries=_entries(w), operator_ref="U1", idempotency_key=f"idx-{uuid.uuid4()}"))
    session.commit()
    draft = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=f"gen-{uuid.uuid4()}"))
    md = draft.content
    assert "定义与术语" not in md and "参与者与外部接口环境" not in md
    assert "履约单" not in md
