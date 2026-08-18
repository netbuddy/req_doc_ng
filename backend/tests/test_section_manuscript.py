"""章节撰稿（AEP-098）+ 候选渲染预览（AEP-099）+ 模板干跑送检（AEP-100）测试义务。

设计事实源：SCN-005-P01 前端交互与接口 §3A/§3B/§5、约束与验收 VAL-P01-11~14。
覆盖：撰稿保存/覆盖默认文本/清除回落/不可撰稿章节拒绝；纯撰稿必填章节裁定；
撰稿区域编辑分类放行；候选预览与生成稿同渲染器；模板送检干跑。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401  register tables
from app.api.schemas import (
    DocIndexEntryRead,
    FinalizeMarkdownCommand,
    GenerateMarkdownCommand,
    MarkdownEditCommand,
    SaveIndexCommand,
    SaveManuscriptCommand,
    TemplateRegisterCommand,
)
from app.adapters.doc_template import TemplateDescriptor, TemplateSection
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import Material, Project, RequirementChart, RequirementItem
from app.domain.errors import InvalidInput, NotFound
from app.repositories.publication import SqlPublicationRepository
from app.repositories.templates import SqlTemplateRegistryRepository
from app.scripts.import_packaged_templates import import_packaged_templates
from app.services.publication import DocumentOrchestrationService, evaluate_slots
from app.services.template_registry import TemplateRegistryService

RAW_TEXT = "系统应支持将确认态需求条目按模板导出为 docx。导出耗时不超过五秒。"


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    import_packaged_templates(s)
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed(session):
    p = Project(name="撰稿演示项目")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text=RAW_TEXT, source_note="评审纪要 2026-07")
    session.add(mat)
    session.flush()
    item = RequirementItem(
        project_id=p.id, parse_result_ref=uuid.uuid4(), formation_context_ref=uuid.uuid4(),
        req_no="FR-001", expression="系统应支持将确认态需求条目按模板导出为 docx",
        req_type="functional", status="confirmed", source_element_refs="[]",
    )
    session.add(item)
    session.flush()
    quality = RequirementItem(
        project_id=p.id, parse_result_ref=uuid.uuid4(), formation_context_ref=uuid.uuid4(),
        req_no="NFR-001", expression="导出耗时不超过五秒",
        req_type="quality", status="confirmed", source_element_refs="[]",
    )
    session.add(quality)
    chart = RequirementChart(
        project_id=p.id, title="导出流程图", chart_kind="behavior",
        chart_type="flowchart", format="mermaid",
        source_code="graph TD; A-->B", status="confirmed", draft_version=1,
        source_refs=json.dumps([str(item.id)]),
    )
    session.add(chart)
    session.flush()
    session.commit()
    return {
        "project": str(p.id), "material": str(mat.id),
        "fr1": str(item.id), "q1": str(quality.id), "chart": str(chart.id),
    }


def _svc(session) -> DocumentOrchestrationService:
    return DocumentOrchestrationService(SqlPublicationRepository(session))


def _entries(w):
    return [
        DocIndexEntryRead(section_key="requirements.functional", asset_type="requirement_item",
                          asset_ref=w["fr1"], order_no=0),
        DocIndexEntryRead(section_key="requirements.quality", asset_type="requirement_item",
                          asset_ref=w["q1"], order_no=0),
    ]


def _save_manuscript(session, w, section_key, content):
    svc = _svc(session)
    read = svc.save_section_manuscript(SaveManuscriptCommand(
        project_ref=w["project"], section_key=section_key,
        content=content, operator_ref="U1",
    ))
    session.commit()
    return read


def _generate(session, w):
    svc = _svc(session)
    svc.save_content_index(SaveIndexCommand(
        project_ref=w["project"], coverage_scope="release-v0.1",
        entries=_entries(w), operator_ref="U1", idempotency_key=f"idx-{uuid.uuid4()}",
    ))
    session.commit()
    svc = _svc(session)
    draft = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=f"gen-{uuid.uuid4()}",
    ))
    session.commit()
    return svc, draft


# ============================================================================
# 章节撰稿（AEP-098）
# ============================================================================

def test_manuscript_save_creates_document_and_revision(session):
    w = _seed(session)
    read = _save_manuscript(session, w, "intro.purpose", "本文档为 {project_name} 编写目的正文。")
    assert read.revision_no == 1
    again = _save_manuscript(session, w, "intro.purpose", "改写后的编写目的。")
    assert again.revision_no == 2
    ws = _svc(session).read_workspace(w["project"])
    assert [m.section_key for m in ws.manuscripts] == ["intro.purpose"]
    assert ws.manuscripts[0].content == "改写后的编写目的。"


def test_manuscript_overrides_boilerplate_in_generated_markdown(session):
    w = _seed(session)
    _save_manuscript(session, w, "intro.purpose", "本文档为 {project_name} 的编写目的（人工撰稿）。")
    _, draft = _generate(session, w)
    assert "撰稿演示项目 的编写目的（人工撰稿）" in draft.content  # 占位替换生效
    authored = [b for b in draft.source_bindings if b.kind == "authored"]
    assert [b.section_key for b in authored] == ["intro.purpose"]
    # 其余默认文本章节仍是 boilerplate 绑定
    assert any(b.kind == "boilerplate" and b.section_key == "intro.scope"
               for b in draft.source_bindings)
    # 重新生成不丢撰稿
    svc = _svc(session)
    draft2 = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key=f"gen-{uuid.uuid4()}",
    ))
    assert "人工撰稿" in draft2.content


def test_manuscript_clear_falls_back_to_boilerplate(session):
    w = _seed(session)
    _save_manuscript(session, w, "intro.purpose", "临时撰稿。")
    cleared = _save_manuscript(session, w, "intro.purpose", "   ")
    assert cleared.revision_no == 0 and cleared.content == ""
    _, draft = _generate(session, w)
    assert "临时撰稿" not in draft.content
    assert not any(b.kind == "authored" for b in draft.source_bindings)


def test_manuscript_rejected_for_non_authoring_section(session):
    w = _seed(session)
    with pytest.raises(InvalidInput):
        _save_manuscript(session, w, "requirements.functional", "不允许给条目槽位撰稿")
    with pytest.raises(InvalidInput):
        _save_manuscript(session, w, "no.such.section", "章节不存在")


def test_authored_only_required_slot_needs_manuscript():
    template = TemplateDescriptor(
        template_id="t", schema_version="1.0", doc_type="srs", title="t", description="",
        export_binding={}, sections=(
            TemplateSection(key="s1", number="1", title="纯撰稿章节", level=1, purpose="",
                            content_types=("authored_text",), required=True,
                            repeatable=False, missing_policy="block"),
        ),
    )
    _, missing = evaluate_slots(template, [], set(), set())
    assert len(missing) == 1 and "需人工撰稿" in missing[0].reason
    statuses, missing2 = evaluate_slots(template, [], set(), {"s1"})
    assert missing2 == [] and statuses[0].satisfied


def test_authored_region_edit_classified_doc_expression(session):
    w = _seed(session)
    _save_manuscript(session, w, "intro.purpose", "编写目的正文第一行。")
    svc, draft = _generate(session, w)
    # 在撰稿区域内插入一段语料不支撑的新文字：不得判为无来源新事实
    lines = draft.content.splitlines()
    anchor = lines.index("编写目的正文第一行。")
    lines.insert(anchor + 1, "补充一段完全出自撰写者的措辞，语料中不存在。")
    result = svc.record_edit(MarkdownEditCommand(
        project_ref=w["project"], draft_ref=draft.draft_ref,
        content="\n".join(lines), operator_ref="U1",
    ))
    session.commit()
    assert result.block_reasons == []
    assert {p.impact for p in result.patches} == {"doc_expression"}
    fin = _svc(session).finalize_markdown(FinalizeMarkdownCommand(
        project_ref=w["project"], draft_ref=draft.draft_ref,
        operator_ref="U1", idempotency_key=f"fin-{uuid.uuid4()}",
    ))
    assert fin.status == "finalized"


def test_insert_outside_authored_region_still_gated(session):
    w = _seed(session)
    svc, draft = _generate(session, w)
    # 条目块区域内插入语料不支撑的新事实：仍然阻断（门禁不变）
    lines = draft.content.splitlines()
    anchor = next(i for i, ln in enumerate(lines) if ln.startswith("**FR-001**"))
    lines.insert(anchor + 1, "系统还应支持区块链存证与量子加密传输。")
    result = svc.record_edit(MarkdownEditCommand(
        project_ref=w["project"], draft_ref=draft.draft_ref,
        content="\n".join(lines), operator_ref="U1",
    ))
    assert any(p.impact in ("no_source_fact", "confirmed_item") for p in result.patches)


# ============================================================================
# 候选渲染预览（AEP-099）
# ============================================================================

def test_candidate_preview_item_matches_document_block(session):
    w = _seed(session)
    svc = _svc(session)
    preview = svc.candidate_preview(w["project"], "requirement_item", w["fr1"])
    assert preview.markdown.startswith("**FR-001**")
    assert "已确认" in preview.markdown
    # 与生成稿同一渲染：生成后条目块应包含预览内容首行
    _, draft = _generate(session, w)
    assert preview.markdown.splitlines()[0] in draft.content


def test_candidate_preview_chart_and_material(session):
    w = _seed(session)
    svc = _svc(session)
    chart = svc.candidate_preview(w["project"], "chart", w["chart"])
    assert chart.markdown.startswith("**图：导出流程图**")
    assert "```mermaid" in chart.markdown
    material = svc.candidate_preview(w["project"], "material", w["material"])
    assert "评审纪要 2026-07" in material.markdown
    assert "原文节选" in material.markdown


def test_candidate_preview_rejects_unknown(session):
    w = _seed(session)
    svc = _svc(session)
    with pytest.raises(NotFound):
        svc.candidate_preview(w["project"], "requirement_item", str(uuid.uuid4()))
    with pytest.raises(InvalidInput):
        svc.candidate_preview(w["project"], "trace", w["fr1"])


# ============================================================================
# 模板干跑送检（AEP-100）
# ============================================================================

def _registry(session) -> TemplateRegistryService:
    return TemplateRegistryService(SqlTemplateRegistryRepository(session))


def _template_json(**overrides):
    base = {
        "template_id": "srs-internal-v1", "schema_version": "1.0", "doc_type": "srs",
        "title": "内部 SRS 模板", "description": "",
        "export_binding": {
            "body_font_east_asia": "仿宋", "body_size_pt": 12,
            "first_line_indent_chars": 2, "heading_sizes_pt": [16, 14, 13],
        },
        "sections": [
            {"key": "intro.purpose", "number": "1.1", "title": "编写目的", "level": 2,
             "purpose": "说明目的", "content_types": ["authored_text"], "required": True,
             "repeatable": False, "missing_policy": "block"},
            {"key": "req.functional", "number": "2.1", "title": "功能需求", "level": 2,
             "purpose": "功能需求", "content_types": ["requirement_item:functional"],
             "required": True, "repeatable": True, "missing_policy": "block"},
        ],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def test_template_validate_ok_returns_descriptor(session):
    result = _registry(session).validate(_template_json())
    assert result.ok and result.error is None
    assert result.descriptor.template_ref == "srs-internal-v1"
    assert [s.key for s in result.descriptor.sections] == ["intro.purpose", "req.functional"]
    # 干跑不落库
    assert all(r.template_key != "srs-internal-v1" for r in _registry(session).list_templates())


def test_template_validate_rejects_with_problem_list(session):
    bad = _template_json(sections=[
        {"key": "s1", "number": "1", "title": "缺元数据", "level": 1,
         "purpose": "", "content_types": ["unknown_type"], "required": True,
         "repeatable": False, "missing_policy": "block"},
    ])
    result = _registry(session).validate(bad)
    assert not result.ok
    assert "未知内容类型" in result.error


def test_authored_text_template_registers_and_blocks_until_manuscript(session):
    w = _seed(session)
    registry = _registry(session)
    row = registry.register(TemplateRegisterCommand(
        content=_template_json(), operator_ref="U1", idempotency_key="tpl-1",
    ))
    session.commit()
    assert row.template_key == "srs-internal-v1"

    svc = _svc(session)
    result = svc.save_content_index(SaveIndexCommand(
        project_ref=w["project"], template_ref="srs-internal-v1",
        entries=[DocIndexEntryRead(section_key="req.functional", asset_type="requirement_item",
                                   asset_ref=w["fr1"], order_no=0)],
        operator_ref="U1", idempotency_key="idx-a",
    ))
    session.commit()
    assert result.status == "index_blocked"
    assert any("需人工撰稿" in m.reason for m in result.missing_list)

    svc = _svc(session)
    svc.save_section_manuscript(SaveManuscriptCommand(
        project_ref=w["project"], template_ref="srs-internal-v1",
        section_key="intro.purpose", content="内部模板的编写目的正文。", operator_ref="U1",
    ))
    session.commit()
    svc = _svc(session)
    result2 = svc.save_content_index(SaveIndexCommand(
        project_ref=w["project"], template_ref="srs-internal-v1",
        entries=[DocIndexEntryRead(section_key="req.functional", asset_type="requirement_item",
                                   asset_ref=w["fr1"], order_no=0)],
        operator_ref="U1", idempotency_key="idx-b",
    ))
    session.commit()
    assert result2.status == "index_ready"
    draft = _svc(session).generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key="gen-x",
    ))
    assert "内部模板的编写目的正文。" in draft.content
