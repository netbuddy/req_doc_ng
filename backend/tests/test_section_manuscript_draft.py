"""AEP-110 · 章节撰稿 AI 起草初稿 测试义务（04 篇 / 05 P4）。

覆盖：仅 authored_text 可起草 / 非撰稿类拒绝 / 写入 ldm014_section_manuscript /
examples 注入存在性（lane 渲染断言）/ cannot_comply 路径 /
发布渲染确定性红线（起草不越界到渲染，AC-P4-02）。禁写 raw prompt/response。

T20260721 起改为信封响应：起草成功 status='drafted'、模型拒绝 status='declined'（同为 200），
模型服务不可用与用错入口仍抛 InvalidInput（→400）。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401  register tables
from app.adapters.llm import (
    StubSectionManuscriptDrafter,
    _SECTION_MANUSCRIPT_DRAFT_OUTPUT,
)
from app.adapters.prompts.environment import dumps, render_pair
from app.api.schemas import (
    DocIndexEntryRead,
    GenerateMarkdownCommand,
    SaveIndexCommand,
    TemplateRegisterCommand,
)
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import Project, RequirementItem
from app.domain.errors import InvalidInput
from app.repositories.publication import SqlPublicationRepository
from app.repositories.templates import SqlTemplateRegistryRepository
from app.services.publication import DocumentOrchestrationService
from app.services.template_registry import TemplateRegistryService


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# 测试模板：boilerplate(引言) + authored_text 且装配功能条目(总体描述,含 examples) + 空说明撰稿章节
_TEMPLATE = {
    "template_id": "srs-p4-draft",
    "schema_version": "1.0",
    "doc_type": "srs",
    "title": "P4 起草测试模板",
    "description": "",
    "export_binding": {
        "body_font_east_asia": "仿宋", "body_size_pt": 12,
        "first_line_indent_chars": 2, "heading_sizes_pt": [16, 14, 13],
    },
    "sections": [
        {"key": "intro", "number": "1", "title": "引言", "level": 1, "purpose": "",
         "content_types": ["boilerplate"], "required": True, "repeatable": False,
         "missing_policy": "skip", "boilerplate": "本章说明目的。"},
        {"key": "overview", "number": "2", "title": "总体描述", "level": 1,
         "purpose": "概述系统总体情况与主要功能。",
         "content_types": ["authored_text", "requirement_item:functional"],
         "required": True, "repeatable": False, "missing_policy": "skip",
         "examples": ["范例：本章从业务目标出发概述系统总体情况。"]},
        {"key": "blank", "number": "3", "title": "空说明章节", "level": 1, "purpose": "",
         "content_types": ["authored_text"], "required": False, "repeatable": False,
         "missing_policy": "skip"},
    ],
}


def _svc(session, drafter=None) -> DocumentOrchestrationService:
    return DocumentOrchestrationService(SqlPublicationRepository(session), drafter=drafter)


def _register_template(session) -> None:
    TemplateRegistryService(SqlTemplateRegistryRepository(session)).register(
        TemplateRegisterCommand(
            content=json.dumps(_TEMPLATE, ensure_ascii=False), name=None,
            operator_ref="U1", idempotency_key=f"t-{uuid.uuid4()}",
        )
    )
    session.commit()


def _seed(session) -> dict:
    p = Project(name="P4 起草项目")
    session.add(p)
    session.flush()
    fr = RequirementItem(
        project_id=p.id, parse_result_ref=uuid.uuid4(), formation_context_ref=uuid.uuid4(),
        req_no="FR-001", expression="系统应支持将订单导出为 PDF", req_type="functional",
        status="confirmed", source_element_refs="[]",
    )
    session.add(fr)
    session.flush()
    seeded = {"project": str(p.id), "fr": str(fr.id)}
    session.commit()
    return seeded


def _index(session, w) -> None:
    """把功能条目编排进 overview（authored_text + functional）章节，建档。"""
    _svc(session).save_content_index(SaveIndexCommand(
        project_ref=w["project"], template_ref="srs-p4-draft",
        entries=[DocIndexEntryRead(section_key="overview", asset_type="requirement_item",
                                   asset_ref=w["fr"], order_no=0)],
        operator_ref="U1", idempotency_key="i-1",
    ))
    session.commit()


# ---- 权限边界：仅 authored_text 可起草 ----

def test_only_authored_text_can_draft(session):
    _register_template(session)
    w = _seed(session)
    _index(session, w)
    svc = _svc(session)  # 默认 stub drafter
    # boilerplate 章节拒绝
    with pytest.raises(InvalidInput) as exc:
        svc.draft_section_manuscript(w["project"], "intro", "U1")
    assert "不支持 AI 起草" in str(exc.value)
    # 不存在的章节拒绝
    with pytest.raises(InvalidInput):
        svc.draft_section_manuscript(w["project"], "nope", "U1")


# ---- 写入 manuscript + 关联确认态资产进入起草 ----

def test_draft_writes_manuscript_with_confirmed_assets(session):
    _register_template(session)
    w = _seed(session)
    _index(session, w)
    svc = _svc(session)
    result = svc.draft_section_manuscript(w["project"], "overview", "U1")
    session.commit()
    assert result.status == "drafted"
    assert result.reason is None
    read = result.manuscript
    assert read is not None
    assert read.section_key == "overview"
    assert read.content  # 非空初稿
    assert "1 项确认态需求资产" in read.content  # stub 反映关联资产计数（FR-001 已装配）
    assert "参考章节样例风格" in read.content  # overview 有 examples
    # 落库可读回（人工可再改）
    ws = svc.read_workspace(w["project"])
    assert any(m.section_key == "overview" and m.content == read.content for m in ws.manuscripts)


# ---- cannot_comply 路径：输入不足以起草时显式拒绝、不落库 ----

def test_cannot_comply_returns_declined_envelope(session):
    """模型拒绝是正常业务结果：200 + status='declined' + 理由原文，不是 400。

    界面据此把理由当一等回执呈现；当错误抛时理由会被接口层拼的 URL 与状态码前缀淹没
    （T20260721 的源头报障就是这个形态）。
    """
    _register_template(session)
    w = _seed(session)
    _index(session, w)
    svc = _svc(session)
    # blank 章节：purpose 空、无装配资产 → stub 返回 cannot_comply
    result = svc.draft_section_manuscript(w["project"], "blank", "U1")
    assert result.status == "declined"
    assert result.manuscript is None
    assert result.reason  # 理由原文原样带出，不加技术前缀
    assert "HTTP" not in result.reason and "http" not in result.reason
    ws = svc.read_workspace(w["project"])
    assert all(m.section_key != "blank" for m in ws.manuscripts)  # 未落库


def test_drafter_failed_is_infra_error(session):
    _register_template(session)
    w = _seed(session)
    _index(session, w)
    svc = _svc(session, drafter=StubSectionManuscriptDrafter(failed=True))
    with pytest.raises(InvalidInput) as exc:
        svc.draft_section_manuscript(w["project"], "overview", "U1")
    assert "暂不可用" in str(exc.value)


# ---- examples 注入存在性（lane 渲染断言；不看 raw model）----

def test_examples_injected_into_lane_user_block():
    _, user = render_pair(
        "section_manuscript_draft",
        section_title="总体描述", section_purpose="概述",
        content_types="人工撰稿",
        assets="[]",
        examples='["范例：从业务目标出发概述"]',
        project_scope="", project_background="",
        output_schema=dumps(_SECTION_MANUSCRIPT_DRAFT_OUTPUT),
    )
    assert "范例：从业务目标出发概述" in user
    assert "章节样例" in user  # few-shot 段存在


# ---- 确定性渲染红线（AC-P4-02）：起草不越界到发布渲染 ----

class _SpyDrafter:
    """记录是否被调用；发布渲染路径若调用它即违反红线。"""

    def __init__(self):
        self.calls = 0

    def draft(self, **kwargs):
        self.calls += 1
        from app.adapters.llm import SectionManuscriptDraftOutcome
        return SectionManuscriptDraftOutcome(draft="不应被渲染路径调用")


def test_render_path_is_deterministic_and_never_calls_drafter(session):
    _register_template(session)
    w = _seed(session)
    _index(session, w)
    spy = _SpyDrafter()
    svc = _svc(session, drafter=spy)
    d1 = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key="g-1"))
    session.commit()
    d2 = svc.generate_markdown(GenerateMarkdownCommand(
        project_ref=w["project"], operator_ref="U1", idempotency_key="g-2"))
    session.commit()
    # 同一确认态输入两次生成结果一致（确定性投影）
    assert d1.content == d2.content
    # 发布渲染路径从不调用起草 lane（禁生成式加工）
    assert spy.calls == 0
