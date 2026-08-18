"""模板定制草稿（暂存工作态）测试义务。

设计口径：草稿与注册表快照分离——草稿可变可删、未送检不占版本号；
登记成功后清理草稿由调用方（前端）触发，删除幂等。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401  register tables
from app.api.schemas import TemplateDraftSaveCommand
from app.db.base import Base, make_engine, make_session_factory
from app.domain.errors import InvalidInput, NotFound
from app.repositories.templates import SqlTemplateDraftRepository
from app.services.template_registry import TemplateDraftService


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


def _svc(session) -> TemplateDraftService:
    return TemplateDraftService(SqlTemplateDraftRepository(session))


def _payload(title="内部 SRS 模板") -> str:
    return json.dumps({
        "designer_state_version": 1,
        "info": {"templateId": "srs-internal-v1", "title": title, "description": ""},
        "binding": {"bodyFontEastAsia": "宋体", "bodySizePt": 12,
                    "firstLineIndentChars": 2, "headingSizesPt": "16, 14, 13"},
        "tree": [{"id": "n1", "title": "引言", "purpose": "", "contentTypes": ["boilerplate"],
                  "required": True, "repeatable": False, "missingPolicy": "skip",
                  "boilerplate": "", "examples": [], "keyOverride": "", "children": []}],
    }, ensure_ascii=False)


def test_create_and_list_draft(session):
    svc = _svc(session)
    read = svc.create(TemplateDraftSaveCommand(
        name="内部 SRS 模板", payload=_payload(), origin="blank", operator_ref="U1"))
    assert read.draft_ref
    assert read.origin == "blank"
    assert read.created_by == "U1"

    drafts = svc.list_drafts()
    assert len(drafts) == 1
    assert drafts[0].draft_ref == read.draft_ref
    # payload 原样回读（后端不解析不改写）
    assert json.loads(drafts[0].payload)["info"]["templateId"] == "srs-internal-v1"


def test_update_draft_overwrites_and_resumes(session):
    svc = _svc(session)
    created = svc.create(TemplateDraftSaveCommand(
        name="v1", payload=_payload("第一稿"), operator_ref="U1"))
    updated = svc.update(created.draft_ref, TemplateDraftSaveCommand(
        name="v2", payload=_payload("第二稿"), operator_ref="U1"))
    assert updated.draft_ref == created.draft_ref
    assert updated.name == "v2"
    assert json.loads(updated.payload)["info"]["title"] == "第二稿"
    # 覆盖式：列表仍只有一条
    assert len(svc.list_drafts()) == 1


def test_update_missing_draft_raises_not_found(session):
    svc = _svc(session)
    with pytest.raises(NotFound):
        svc.update(str(uuid.uuid4()), TemplateDraftSaveCommand(
            name="x", payload=_payload(), operator_ref="U1"))


def test_delete_draft_is_idempotent(session):
    svc = _svc(session)
    created = svc.create(TemplateDraftSaveCommand(
        name="待删", payload=_payload(), operator_ref="U1"))
    svc.delete(created.draft_ref, "U1")
    assert svc.list_drafts() == []
    # 再删同一 ref：静默成功（登记后清理与人工删除可能竞争）
    svc.delete(created.draft_ref, "U1")


def test_draft_from_registry_edit_records_source(session):
    svc = _svc(session)
    source_ref = str(uuid.uuid4())
    read = svc.create(TemplateDraftSaveCommand(
        name="编辑内置模板", payload=_payload(), origin="edit",
        source_registry_ref=source_ref, operator_ref="U2"))
    assert read.origin == "edit"
    assert read.source_registry_ref == source_ref


def test_draft_payload_guards(session):
    svc = _svc(session)
    with pytest.raises(InvalidInput):
        svc.create(TemplateDraftSaveCommand(name="", payload="   ", operator_ref="U1"))
    with pytest.raises(InvalidInput):
        svc.create(TemplateDraftSaveCommand(
            name="过大", payload="x" * (512 * 1024 + 1), operator_ref="U1"))
    with pytest.raises(InvalidInput):
        svc.create(TemplateDraftSaveCommand(
            name="来源非法", payload=_payload(), origin="fork", operator_ref="U1"))
