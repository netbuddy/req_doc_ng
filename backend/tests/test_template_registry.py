"""模板注册表（登记快照/停用/预览）+ 发布侧注册表加载 测试义务。

设计口径：UINV-19/20（只登记不管理）+ SCN-005 §4.3（schema 校验输入契约）
+ 基线可复现性（冻结注册行引用，模板升级不影响在途文档与历史基线）。
"""
import json
import uuid

import pytest
from docx import Document as DocxDocument
from docx.shared import Pt

import app.db.models  # noqa: F401  register tables
from app.adapters.doc_template import BUILTIN_TEMPLATE_DIR
from app.api.schemas import (
    DocIndexEntryRead,
    GenerateMarkdownCommand,
    SaveIndexCommand,
    TemplateRegisterCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import Material, Project, RequirementItem
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.repositories.publication import SqlPublicationRepository
from app.repositories.templates import SqlTemplateRegistryRepository
from app.services.publication import DocumentOrchestrationService
from app.services.template_registry import (
    TemplateRegistryService,
    build_sample_markdown,
)


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _svc(session) -> TemplateRegistryService:
    return TemplateRegistryService(SqlTemplateRegistryRepository(session))


def _builtin_content() -> str:
    return (BUILTIN_TEMPLATE_DIR / "srs_iso29148_v1.json").read_text(encoding="utf-8")


def _variant_content(title="需求规格说明（企业定制版）") -> str:
    raw = json.loads(_builtin_content())
    raw["title"] = title
    return json.dumps(raw, ensure_ascii=False)


def _register(session, content, name=None, operator="U1"):
    result = _svc(session).register(TemplateRegisterCommand(
        content=content, name=name, operator_ref=operator, idempotency_key=f"t-{uuid.uuid4()}",
    ))
    session.commit()
    return result


def _import_builtin(session):
    return _svc(session).import_packaged_templates([
        ("srs-iso29148-v1", _builtin_content()),
    ])


# ---- 登记：送检 / 幂等 / 版本递增 ----

def test_register_valid_template(session):
    row = _register(session, _builtin_content())
    assert row.template_key == "srs-iso29148-v1"
    assert row.version_no == 1
    assert row.source == "registered" and row.status == "active"
    assert len(row.content_hash) == 64


def test_register_invalid_template_rejected_entirely(session):
    broken = (BUILTIN_TEMPLATE_DIR / "srs_broken_v1.json").read_text(encoding="utf-8")
    with pytest.raises(InvalidInput) as exc:
        _register(session, broken)
    assert "schema" in str(exc.value)
    assert _svc(session).list_templates() == []  # 失败不落库


def test_register_same_content_idempotent(session):
    first = _register(session, _builtin_content())
    replay = _register(session, _builtin_content(), operator="U2")
    assert replay.registry_ref == first.registry_ref  # 内容哈希幂等


def test_register_new_content_new_immutable_version(session):
    v1 = _register(session, _builtin_content())
    v2 = _register(session, _variant_content())
    assert v2.template_key == v1.template_key
    assert v2.version_no == 2
    # 旧行不可变：内容与哈希保持
    old = SqlTemplateRegistryRepository(session).get(v1.registry_ref)
    assert old.content == _builtin_content()


# ---- 打包模板导入 / 停用 ----

def test_import_packaged_template_idempotent(session):
    svc = _svc(session)
    row, created = svc.import_packaged_template(_builtin_content(), "srs-iso29148-v1")
    assert created
    assert row.template_key == "srs-iso29148-v1"
    assert row.source == "builtin" and row.registered_by == "system"
    session.commit()
    replay, replay_created = svc.import_packaged_template(_builtin_content(), "srs-iso29148-v1")
    assert not replay_created
    assert replay.registry_ref == row.registry_ref


def test_import_packaged_template_rejects_invalid_content(session):
    broken = (BUILTIN_TEMPLATE_DIR / "srs_broken_v1.json").read_text(encoding="utf-8")
    with pytest.raises(InvalidInput):
        _svc(session).import_packaged_template(broken, "srs-broken-v1")
    assert _svc(session).list_templates() == []


def test_import_packaged_templates_reports_invalid_file(session):
    broken = (BUILTIN_TEMPLATE_DIR / "srs_broken_v1.json").read_text(encoding="utf-8")
    report = _svc(session).import_packaged_templates([
        ("srs-iso29148-v1", _builtin_content()),
        ("srs-broken-v1", broken),
    ])
    assert report.total == 2
    assert report.imported == 1
    assert report.skipped == 0
    assert report.failed == 1
    assert "srs-broken-v1" in report.failures[0].source_ref
    rows = _svc(session).list_templates()
    assert [r.template_key for r in rows] == ["srs-iso29148-v1"]
    assert rows[0].source == "builtin"


def test_disable_registered_template(session):
    _import_builtin(session)
    session.commit()
    row = _register(session, _variant_content())
    svc = _svc(session)
    disabled = svc.set_status(row.registry_ref, "disabled", "U1")
    assert disabled.status == "disabled"
    # 停用后发布侧回退到仍 active 的内置版本
    active = SqlPublicationRepository(session).latest_active_template("srs-iso29148-v1")
    assert str(active.id) != row.registry_ref


def test_builtin_template_cannot_be_disabled(session):
    _import_builtin(session)
    session.commit()
    builtin = _svc(session).list_templates()[0]
    with pytest.raises(RejectedTransition):
        _svc(session).set_status(builtin.registry_ref, "disabled", "U1")


def test_detail_and_missing(session):
    row = _register(session, _builtin_content())
    detail = _svc(session).get_detail(row.registry_ref)
    assert detail.descriptor.error is None
    assert len(detail.descriptor.sections) == 15  # 含 1.4 文档约定（29148 属性补齐静态段）
    with pytest.raises(NotFound):
        _svc(session).get_detail(str(uuid.uuid4()))


# ---- 章节样例 examples[]（P1 加法式扩展；AI 起草少样本参考）----

def _content_with_examples(examples):
    """在首个章节挂上 examples；examples=None 表示删除该键（缺省态）。"""
    raw = json.loads(_builtin_content())
    if examples is None:
        raw["sections"][0].pop("examples", None)
    else:
        raw["sections"][0]["examples"] = examples
    return json.dumps(raw, ensure_ascii=False)


def test_examples_valid_list_passes_and_persists(session):
    content = _content_with_examples(["范例一：本章应说明目的与范围。", "范例二：另一段落。"])
    row = _register(session, content)
    detail = _svc(session).get_detail(row.registry_ref)
    assert detail.descriptor.sections[0].examples == [
        "范例一：本章应说明目的与范围。", "范例二：另一段落。",
    ]


def test_examples_absent_unaffected(session):
    """缺省（无 examples）解析结果与基线一致：所有章节 examples 为空列表。"""
    row = _register(session, _content_with_examples(None))
    detail = _svc(session).get_detail(row.registry_ref)
    assert all(s.examples == [] for s in detail.descriptor.sections)


def test_examples_invalid_rejected_entirely_via_validate(session):
    """AEP-100 干跑：examples 含空串/非字符串 → 整体拒绝、problems 明示、不落库。"""
    result = _svc(session).validate(_content_with_examples(["", 123]))
    assert result.ok is False
    assert "examples 必须是非空字符串列表" in result.error
    assert _svc(session).list_templates() == []


def test_examples_invalid_rejected_on_register(session):
    with pytest.raises(InvalidInput) as exc:
        _register(session, _content_with_examples(["合法", "  "]))  # 纯空白视为空串
    assert "examples" in str(exc.value)
    assert _svc(session).list_templates() == []


# ---- 预览：样例 Markdown 覆盖全部槽位类型 ----

def test_sample_markdown_covers_all_slots(session):
    row = _register(session, _builtin_content())
    descriptor = _svc(session).load_descriptor(row.registry_ref)
    md = build_sample_markdown(descriptor)
    assert "# 1 引言" in md and "## 3.1 功能需求" in md
    assert "示例-FR-001" in md and "示例-NFR-001" in md
    assert "示例来源材料" in md
    assert "示例项目" in md  # 占位符替换


def test_preview_docx_renders_with_binding(session, tmp_path):
    from app.adapters.docx_convert import convert_markdown_to_docx
    row = _register(session, _builtin_content())
    descriptor = _svc(session).load_descriptor(row.registry_ref)
    out = convert_markdown_to_docx(
        build_sample_markdown(descriptor), tmp_path / "preview.docx",
        descriptor.export_binding, {"title": "样式预览", "project_name": "示例项目"},
    )
    doc = DocxDocument(str(out))
    body = [p for p in doc.paragraphs if p.style.name == "Normal"
            and p.paragraph_format.first_line_indent is not None]
    assert body and all(p.paragraph_format.first_line_indent == Pt(24) for p in body)


# ---- 发布侧：注册表加载 + 冻结引用 ----

def _seed_publication(session):
    p = Project(name="模板验收项目")
    session.add(p)
    session.flush()
    mat = Material(project_id=p.id, raw_text="系统应支持导出。导出耗时不超过五秒。", source_note="纪要")
    session.add(mat)
    session.flush()

    def item(req_no, expression, req_type):
        r = RequirementItem(
            project_id=p.id, parse_result_ref=uuid.uuid4(), formation_context_ref=uuid.uuid4(),
            req_no=req_no, expression=expression, req_type=req_type, status="confirmed",
            source_element_refs="[]",
        )
        session.add(r)
        session.flush()
        return str(r.id)

    seeded = {
        "project": str(p.id),
        "fr": item("FR-001", "系统应支持导出", "functional"),
        "q": item("NFR-001", "导出耗时不超过五秒", "quality"),
    }
    session.commit()
    return seeded


def test_publication_loads_template_from_registry_and_freezes_ref(session):
    registered = _register(session, _variant_content("注册表版模板"))
    w = _seed_publication(session)
    pub = DocumentOrchestrationService(SqlPublicationRepository(session))
    result = pub.save_content_index(SaveIndexCommand(
        project_ref=w["project"],
        entries=[
            DocIndexEntryRead(section_key="requirements.functional",
                              asset_type="requirement_item", asset_ref=w["fr"], order_no=0),
            DocIndexEntryRead(section_key="requirements.quality",
                              asset_type="requirement_item", asset_ref=w["q"], order_no=0),
        ],
        operator_ref="U1", idempotency_key="i-1",
    ))
    session.commit()
    assert result.status == "index_ready"
    doc = SqlPublicationRepository(session).get_document(w["project"])
    assert str(doc.template_id) == registered.registry_ref  # 冻结注册行

    # 模板升级出 v2 后，已编排文档仍用冻结快照生成
    _register(session, _variant_content("升级后的 v2 模板"))
    draft = pub.generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key="g-1",
    ))
    session.commit()
    assert draft.content.startswith("# 1 引言")  # 用 v1 快照正常生成


def test_publication_blocks_when_registry_empty(session):
    w = _seed_publication(session)
    pub = DocumentOrchestrationService(SqlPublicationRepository(session))
    ws = pub.read_workspace(w["project"])
    assert ws.template.error is not None
    assert "template_registry" in ws.template.error
    result = pub.save_content_index(SaveIndexCommand(
        project_ref=w["project"], entries=[],
        operator_ref="U1", idempotency_key="i-empty",
    ))
    assert result.status == "index_blocked"
    assert result.document_ref is None
