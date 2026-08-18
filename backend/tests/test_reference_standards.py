"""引用标准目录测试（T20260721-reference-standards-catalog / AEP-118）。

覆盖：内置清单自身的完整性（标识唯一、类别合法、必填非空）、参考资料类章节的标题判定、
自有条目读侧宽容与写侧从严、停用与恢复、目录排序、审计留痕只记字段名不记内容、
通用配置端点不得绕过逐条校验，以及撰稿侧两个下发字段（选取入口标志、起草依据计数）。
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.db.models  # noqa: F401  register tables
from app.api.schemas import (
    DocIndexEntryRead,
    ReferenceStandardSaveCommand,
    ReferenceStandardWrite,
    SaveIndexCommand,
    TemplateRegisterCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import ConfigAudit, Project, RequirementItem
from app.deps import get_config_registry_service
from app.domain.errors import InvalidInput
from app.domain.reference_standards import (
    BUILTIN_KEYS,
    BUILTIN_STANDARDS,
    CATEGORY_KEYS,
    _ENTRY_KEY_RE,
    is_reference_section_title,
    merge_catalog,
    normalize_custom_entries,
    normalize_disabled_keys,
    slug_from_code,
    validate_custom_entries,
    validate_disabled_keys,
)
from app.main import app
from app.repositories.publication import SqlPublicationRepository
from app.repositories.templates import SqlTemplateRegistryRepository
from app.services.config_registry import ConfigRegistryService
from app.services.publication import DocumentOrchestrationService
from app.services.template_registry import TemplateRegistryService

_PATH = "/api/config/reference-standards"


@pytest.fixture()
def session():
    # 共享内存库（StaticPool）：TestClient 在 threadpool 线程跑 sync 路由，需跨线程共用同一 DB。
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture()
def client(session):
    def _override():
        service = ConfigRegistryService(session)
        yield service
        session.commit()

    app.dependency_overrides[get_config_registry_service] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_config_registry_service, None)


def _entry(**over) -> dict:
    base = {
        "code": "Q/AB 001-2026",
        "title": "企业内部需求评审规范",
        "year": "2026",
        "issuer": "示例企业",
        "note": "内部评审流程依据",
        "category": "national",
        "url": "",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 内置清单自身的完整性（数据出错的代价最高：引错标准号比留白更糟）
# ---------------------------------------------------------------------------


def test_builtin_list_is_well_formed():
    assert BUILTIN_STANDARDS, "内置清单不应为空"
    seen: set[str] = set()
    for s in BUILTIN_STANDARDS:
        assert _ENTRY_KEY_RE.match(s.key), f"内置条目标识非法：{s.key!r}"
        assert s.key not in seen, f"内置条目标识重复：{s.key}"
        seen.add(s.key)
        assert s.code.strip(), f"{s.key} 缺标准号"
        assert s.title.strip(), f"{s.key} 缺名称"
        assert s.year.strip(), f"{s.key} 缺版本年份"
        assert s.issuer.strip(), f"{s.key} 缺发布机构"
        assert s.note.strip(), f"{s.key} 缺适用说明"
        assert s.category in CATEGORY_KEYS, f"{s.key} 类别非法：{s.category}"
        assert s.builtin is True
        assert s.url.startswith("https://"), f"{s.key} 的查证出处链接缺失或不是 https"
        # 年份与标准号里的年份要对上——两处各写一遍是笔误的高发地。
        # 只查国际标准与国家标准：它们的标准号按惯例以「编号-年份」或「编号:年份」收尾。指南类
        # 不查——INCOSE 的文档编号 INCOSE-TP-2010-006-04 里的 2010 是文档系列启用年、04 是版次，
        # 都不是这一版的发布年份（2023），拿它对年份会误报。
        if s.category in ("international", "national"):
            assert s.year in s.code, f"{s.key} 的年份 {s.year} 与标准号 {s.code} 对不上"


def test_builtin_keys_carry_no_year():
    """标识不带年份：标准出新版时标识不变，用户此前的停用选择才不会失效。"""
    for s in BUILTIN_STANDARDS:
        assert s.year not in s.key, f"内置条目标识不应含年份：{s.key}"


# ---------------------------------------------------------------------------
# 参考资料类章节判定（按标题，不按章节 key）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", [
    "参考资料", "1.3 参考资料", "参考文献", "2 引用文档", "引用标准", "规范性引用文件", "References",
])
def test_reference_titles_match(title):
    assert is_reference_section_title(title) is True


@pytest.mark.parametrize("title", ["1.3 文档概述", "3.1 功能需求", "总体描述", "", None])
def test_non_reference_titles_do_not_match(title):
    assert is_reference_section_title(title) is False


def test_key_based_detection_would_misjudge_real_template():
    """实证反例（用户自建「4XXB SRS 模板」v2）：按章节 key 判会两头判错。

    该模板里 key 为 `intro.references` 的章节标题是「1.3 文档概述」（不是参考资料），而真正的
    参考资料章节「2 引用文档」的 key 却是 `overview`——自定义模板的 key 从内置骨架复制而来，
    与语义脱钩。这条用例把「按标题判」这个取舍钉住，防日后被改回按 key 判。
    """
    assert is_reference_section_title("1.3 文档概述") is False  # key=intro.references
    assert is_reference_section_title("2 引用文档") is True     # key=overview


# ---------------------------------------------------------------------------
# 自有条目：读侧宽容 / 写侧从严
# ---------------------------------------------------------------------------


def test_normalize_skips_broken_rows_without_raising():
    rows = [
        {"key": "ok-1", "code": "X 1", "title": "甲", "category": "guide"},
        {"key": "ok-1", "code": "X 2", "title": "重复标识"},          # 重复 → 丢
        {"key": "bad key!", "code": "X 3", "title": "非法标识"},       # 标识非法 → 丢
        {"key": "no-code", "title": "缺标准号"},                       # 缺必填 → 丢
        {"key": next(iter(BUILTIN_KEYS)), "code": "X 4", "title": "撞内置"},  # 撞内置 → 丢
        "不是字典",
    ]
    got = normalize_custom_entries(rows)
    assert [s.key for s in got] == ["ok-1"]
    assert got[0].builtin is False
    assert got[0].category == "guide"
    assert normalize_custom_entries(None) == ()
    assert normalize_custom_entries("[]") == ()


def test_normalize_unknown_category_falls_back():
    got = normalize_custom_entries([{"key": "k1", "code": "X 1", "title": "甲", "category": "wat"}])
    assert got[0].category in CATEGORY_KEYS


def test_normalize_disabled_keys_drops_unknown():
    known = next(iter(BUILTIN_KEYS))
    assert normalize_disabled_keys([known, "不存在的键", known]) == (known,)
    assert normalize_disabled_keys(None) == ()


def test_slug_from_code():
    assert slug_from_code("GB/T 8567-2006") == "gb-t-8567-2006"
    assert slug_from_code("中文标准号") == ""


def test_validate_generates_key_from_code_when_absent():
    got = validate_custom_entries([_entry(code="Q/AB 001-2026")])
    assert got[0].key == "q-ab-001-2026"


@pytest.mark.parametrize("bad,expect", [
    (_entry(code=" "), "标准号不能为空"),
    (_entry(title=""), "名称不能为空"),
    (_entry(category="wat"), "类别非法"),
    (_entry(key="bad key!"), "标识只能用"),
    (_entry(url="ftp://x/y"), "http://"),
    (_entry(code="中文标准号", key=""), "无法由标准号生成标识"),
])
def test_validate_rejects_bad_rows(bad, expect):
    with pytest.raises(ValueError) as exc:
        validate_custom_entries([bad])
    assert expect in str(exc.value)


def test_validate_rejects_duplicate_and_builtin_collision():
    with pytest.raises(ValueError) as exc:
        validate_custom_entries([_entry(key="dup"), _entry(key="dup", code="Q/AB 002-2026")])
    assert "标识重复" in str(exc.value)
    builtin_key = next(iter(BUILTIN_KEYS))
    with pytest.raises(ValueError) as exc:
        validate_custom_entries([_entry(key=builtin_key)])
    assert "与内置条目重名" in str(exc.value)


def test_validate_disabled_keys_rejects_unknown():
    """拼错的停用标识必须报错而不是被静默吞掉——静默吞掉会让「停用没生效」无从排查。"""
    with pytest.raises(ValueError) as exc:
        validate_disabled_keys(["拼错了"])
    assert "不存在" in str(exc.value)
    known = next(iter(BUILTIN_KEYS))
    assert validate_disabled_keys([known, known]) == (known,)


# ---------------------------------------------------------------------------
# 合并与排序
# ---------------------------------------------------------------------------


def test_merge_marks_disabled_and_keeps_them_in_list():
    """被停用的内置条目仍留在全集里（否则设置页无从把它恢复），只是 enabled=False。"""
    victim = BUILTIN_STANDARDS[0].key
    merged = merge_catalog((), (victim,))
    assert len(merged) == len(BUILTIN_STANDARDS)
    assert {s.key: enabled for s, enabled in merged}[victim] is False
    assert sum(1 for _, enabled in merged if enabled) == len(BUILTIN_STANDARDS) - 1


def test_merge_sort_is_category_then_code_and_stable():
    custom = normalize_custom_entries([
        {"key": "z-guide", "code": "AAA 1", "title": "甲", "category": "guide"},
        {"key": "a-intl", "code": "AAA 2", "title": "乙", "category": "international"},
    ])
    merged = merge_catalog(custom, ())
    ranks = [CATEGORY_KEYS.index(s.category) for s, _ in merged]
    assert ranks == sorted(ranks), "类别次序应为 国际标准 → 国家标准 → 指南"
    intl_codes = [s.code for s, _ in merged if s.category == "international"]
    assert intl_codes == sorted(intl_codes), "同类别内按标准号升序"
    # 两次合并结果完全一致（排序确定，不随字典序抖动）
    assert [s.key for s, _ in merge_catalog(custom, ())] == [s.key for s, _ in merged]


# ---------------------------------------------------------------------------
# 接口：读、写、留痕、越权写入
# ---------------------------------------------------------------------------


def test_get_returns_builtin_catalog_before_any_save(client):
    body = client.get(_PATH).json()
    assert body["source"] == "builtin"
    assert body["builtin_count"] == len(BUILTIN_STANDARDS)
    assert body["custom_count"] == 0 and body["disabled_count"] == 0
    assert len(body["entries"]) == len(BUILTIN_STANDARDS)
    assert all(e["builtin"] and e["enabled"] for e in body["entries"])
    # 类别标签由后端给（前端不硬编码中文）
    assert [c["key"] for c in body["categories"]] == list(CATEGORY_KEYS)
    assert all(c["label"] for c in body["categories"])
    assert all(e["category_label"] for e in body["entries"])


def test_save_adds_custom_and_disables_builtin(client, session):
    victim = BUILTIN_STANDARDS[0].key
    resp = client.put(_PATH, json={
        "custom_entries": [_entry(key="own-1")],
        "disabled_builtin_keys": [victim],
        "operator_ref": "U1",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "saved"
    assert body["custom_count"] == 1 and body["disabled_count"] == 1
    by_key = {e["key"]: e for e in body["entries"]}
    assert by_key["own-1"]["builtin"] is False and by_key["own-1"]["enabled"] is True
    assert by_key[victim]["enabled"] is False
    # 读回一致（落库不是只在响应里演了一遍）
    assert client.get(_PATH).json()["entries"] == body["entries"]

    # 恢复停用 + 删掉自有条目：缺席即删除（整表替换语义）
    body2 = client.put(_PATH, json={
        "custom_entries": [], "disabled_builtin_keys": [], "operator_ref": "U1",
    }).json()
    assert body2["custom_count"] == 0 and body2["disabled_count"] == 0
    assert all(e["enabled"] for e in body2["entries"])


def test_save_writes_audit_without_entry_content(client, session):
    client.put(_PATH, json={
        "custom_entries": [_entry(key="own-1", title="不该出现在留痕里的名称")],
        "disabled_builtin_keys": [], "operator_ref": "U1",
    })
    audits = session.scalars(
        select(ConfigAudit).where(ConfigAudit.domain == "reference_standards")
    ).all()
    assert len(audits) == 1
    assert audits[0].action == "save_reference_standards"
    assert audits[0].operator_ref == "U1"
    changed = json.loads(audits[0].changed_keys)
    assert changed == ["custom_entries", "disabled_builtin_keys"]  # 只记字段名
    assert "不该出现在留痕里的名称" not in audits[0].changed_keys


@pytest.mark.parametrize("payload,expect", [
    ({"custom_entries": [_entry(title="")], "disabled_builtin_keys": []}, "名称不能为空"),
    ({"custom_entries": [], "disabled_builtin_keys": ["没这条"]}, "不存在"),
    ({"custom_entries": [_entry(key=BUILTIN_STANDARDS[0].key)], "disabled_builtin_keys": []},
     "与内置条目重名"),
])
def test_save_rejects_bad_payload(client, payload, expect):
    resp = client.put(_PATH, json={**payload, "operator_ref": "U1"})
    assert resp.status_code == 400
    assert expect in resp.text


def test_save_requires_operator(client):
    resp = client.put(_PATH, json={
        "custom_entries": [], "disabled_builtin_keys": [], "operator_ref": "  ",
    })
    assert resp.status_code == 400


def test_generic_config_endpoint_cannot_write_catalog(client):
    """通用 PUT /config/{domain} 一个字段都不接受：数组只能经专用端点逐条校验后写入。"""
    resp = client.put("/api/config/reference_standards", json={
        "values": {"custom_entries": "[]"}, "secrets": {}, "operator_ref": "U1",
    })
    assert resp.status_code == 400
    assert "不接受字段" in resp.text


def test_domain_appears_in_domain_status(client):
    domains = {d["domain"]: d for d in client.get("/api/config/domains").json()}
    assert "reference_standards" in domains
    row = domains["reference_standards"]
    assert row["label"] == "引用标准目录"
    assert row["group"] == "文档资源"
    assert row["configured"] is False  # 从未保存过
    client.put(_PATH, json={
        "custom_entries": [], "disabled_builtin_keys": [], "operator_ref": "U1",
    })
    after = {d["domain"]: d for d in client.get("/api/config/domains").json()}
    assert after["reference_standards"]["configured"] is True


def test_service_rejects_blank_operator_directly(session):
    with pytest.raises(InvalidInput):
        ConfigRegistryService(session).save_reference_standards(
            ReferenceStandardSaveCommand(custom_entries=[], disabled_builtin_keys=[], operator_ref="")
        )


def test_service_accepts_write_dto(session):
    got = ConfigRegistryService(session).save_reference_standards(
        ReferenceStandardSaveCommand(
            custom_entries=[ReferenceStandardWrite(
                code="Q/AB 003-2026", title="内部规范", category="guide",
            )],
            disabled_builtin_keys=[], operator_ref="U1",
        )
    )
    assert got.custom_count == 1
    assert any(e.key == "q-ab-003-2026" and not e.builtin for e in got.entries)


# ---------------------------------------------------------------------------
# 撰稿侧下发字段：选取入口标志 / 起草依据计数
# ---------------------------------------------------------------------------

_TEMPLATE = {
    "template_id": "srs-refstd-test",
    "schema_version": "1.0",
    "doc_type": "srs",
    "title": "引用标准目录测试模板",
    "description": "",
    "export_binding": {
        "body_font_east_asia": "仿宋", "body_size_pt": 12,
        "first_line_indent_chars": 2, "heading_sizes_pt": [16, 14, 13],
    },
    "sections": [
        # 参考资料类且可撰稿 → 出选取入口
        {"key": "intro.refs", "number": "1.3", "title": "参考资料", "level": 2,
         "purpose": "列出本文档引用的标准与资料。", "content_types": ["authored_text"],
         "required": False, "repeatable": False, "missing_policy": "skip"},
        # 参考资料类但只挂材料（无撰稿框）→ 不出入口
        {"key": "appendix.refs", "number": "附录A", "title": "参考文献", "level": 1,
         "purpose": "", "content_types": ["material"],
         "required": False, "repeatable": False, "missing_policy": "skip"},
        # 可撰稿但不是参考资料类 → 不出入口；装配功能条目，用于起草依据计数
        {"key": "overview", "number": "2", "title": "总体描述", "level": 1, "purpose": "概述。",
         "content_types": ["authored_text", "requirement_item:functional"],
         "required": False, "repeatable": False, "missing_policy": "skip",
         "examples": ["范例：从业务目标出发概述系统。"]},
    ],
}


def _publication(session) -> DocumentOrchestrationService:
    return DocumentOrchestrationService(SqlPublicationRepository(session))


@pytest.fixture()
def workspace(session):
    TemplateRegistryService(SqlTemplateRegistryRepository(session)).register(
        TemplateRegisterCommand(
            content=json.dumps(_TEMPLATE, ensure_ascii=False), name=None,
            operator_ref="U1", idempotency_key=f"t-{uuid.uuid4()}",
        )
    )
    p = Project(name="引用标准目录测试项目")
    session.add(p)
    session.flush()
    fr = RequirementItem(
        project_id=p.id, parse_result_ref=uuid.uuid4(), formation_context_ref=uuid.uuid4(),
        req_no="FR-001", expression="系统应支持导出", req_type="functional",
        status="confirmed", source_element_refs="[]",
    )
    session.add(fr)
    session.flush()
    ids = {"project": str(p.id), "fr": str(fr.id)}
    session.commit()
    _publication(session).save_content_index(SaveIndexCommand(
        project_ref=ids["project"], template_ref="srs-refstd-test",
        entries=[DocIndexEntryRead(section_key="overview", asset_type="requirement_item",
                                   asset_ref=ids["fr"], order_no=0)],
        operator_ref="U1", idempotency_key="i-1",
    ))
    session.commit()
    return ids


def test_standards_pickable_flag(session, workspace):
    ws = _publication(session).read_workspace(workspace["project"])
    flags = {s.key: s.standards_pickable for s in ws.template.sections}
    assert flags["intro.refs"] is True       # 参考资料 ∧ 可撰稿
    assert flags["appendix.refs"] is False   # 参考文献但只挂材料，没有撰稿框
    assert flags["overview"] is False        # 可撰稿但不是参考资料类


def test_draft_basis_counts_match_drafter_inputs(session, workspace):
    ws = _publication(session).read_workspace(workspace["project"])
    basis = {b.section_key: b for b in ws.draft_basis}
    # 只覆盖可 AI 起草（authored_text）章节
    assert set(basis) == {"intro.refs", "overview"}
    assert basis["overview"].asset_count == 1      # 已装配 1 条确认态条目
    assert basis["overview"].example_count == 1    # 模板给了 1 条样例
    assert basis["intro.refs"].asset_count == 0    # 零依据 → 界面据此在点击前提示
    assert basis["intro.refs"].example_count == 0
