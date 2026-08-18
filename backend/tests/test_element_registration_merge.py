"""P3 登记归并测试（03 §2.1）：同名 term/role/external_system 按名称规范化归并。

选型 B：主锚点保留单材料，锚点数走 merge 留痕计数；确认态命中走草案通道。
"""
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.adapters.llm import RecognitionResult, RecognizedElement
from app.api.schemas import ElementRecognitionCommand
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import ElementHistory, IntakeRecord, Material, Project, RequirementElement
from app.domain.enums import ElementProcessStatus, ElementType, ModelVerdict
from app.domain.naming import normalize_element_name
from app.repositories.sqlalchemy import build_sql_analysis_service


class _Scripted:
    def __init__(self, spec, anchor_quote=None):
        self._spec = spec  # [(etype, content, conf)]
        # None＝引文取表达本身（默认）；给了字符串就照给（空串＝模型没给引文）
        self._anchor_quote = anchor_quote

    def recognize(self, project_ref, raw_text, source_note,
                  project_scope=None, project_background=None, domain_profile=None):
        els = tuple(
            RecognizedElement(
                element_type=ElementType(t), content=c,
                source_anchor=c if self._anchor_quote is None else self._anchor_quote,
                confidence=cf, verdict=ModelVerdict.PROCESSABLE,
            )
            for t, c, cf in self._spec
        )
        return RecognitionResult(elements=els, basis="scripted", failed=False)


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


def _project(session) -> str:
    p = Project(name="归并测试")
    session.add(p)
    session.flush()
    return str(p.id)


def _material(session, pid: str, text: str) -> str:
    mat = Material(project_id=uuid.UUID(pid), raw_text=text, source_note="n")
    session.add(mat)
    session.flush()
    session.add(IntakeRecord(project_id=uuid.UUID(pid), context_ref=uuid.uuid4(),
                             intake_conclusion="accepted", material_ref=mat.id))
    session.flush()
    return str(mat.id)


def _recognize(session, pid: str, mid: str, spec, key: str, anchor_quote=None):
    """返回 (服务, 该次识别的请求上下文引用)——工作区读投影断言用。"""
    svc = build_sql_analysis_service(
        session, auto_complete=True, recognizer=_Scripted(spec, anchor_quote))
    result = svc.submit_element_recognition(ElementRecognitionCommand(
        project_ref=pid, material_ref=mid, operator_ref="U1", idempotency_key=key))
    session.commit()
    return svc, result.parse_context_ref


def _live_terms(session):
    return session.scalars(
        select(RequirementElement).where(
            RequirementElement.element_type == "term",
            RequirementElement.superseded.is_(False),
        )
    ).all()


def test_same_name_term_merges_not_duplicated(session):
    pid = _project(session)
    m1 = _material(session, pid, "履约单是指从下单到出库的完整流程。")
    _recognize(session, pid, m1, [("term", "履约单是指从下单到出库的完整流程", 0.9)], "K1")
    m2 = _material(session, pid, "履约单是指订单处理的作业指令。")
    _recognize(session, pid, m2, [("term", "履约单是指订单处理的作业指令", 0.9)], "K2")

    terms = _live_terms(session)
    assert len(terms) == 1  # 不产生第二条同名 term
    t = terms[0]
    assert t.version >= 2  # 版本 +1
    merges = session.scalars(select(ElementHistory).where(
        ElementHistory.element_ref == t.id, ElementHistory.action == "merge")).all()
    assert len(merges) == 1  # merge 留痕
    assert t.content == "履约单是指从下单到出库的完整流程"  # 内容不被第二份覆盖（只追加锚点）


def test_confirmed_hit_goes_to_draft(session):
    pid = _project(session)
    m1 = _material(session, pid, "波次是指批量拣货任务。")
    _recognize(session, pid, m1, [("term", "波次是指批量拣货任务", 0.9)], "K1")
    # 确认该术语（走 ORM 直置确认态，模拟人工确认）
    t = _live_terms(session)[0]
    t.process_status = ElementProcessStatus.CONFIRMED.value
    session.commit()
    before_content = t.content

    m2 = _material(session, pid, "波次是指按配送区域合并的拣货批次。")
    _recognize(session, pid, m2, [("term", "波次是指按配送区域合并的拣货批次", 0.9)], "K2")

    terms = _live_terms(session)
    assert len(terms) == 1  # 仍一条，不新建
    t2 = terms[0]
    assert t2.content == before_content  # 确认态内容不变（不静默改事实）
    assert (t2.revision_draft or "").startswith("[锚点追加草案]")  # 登记锚点追加草案
    merges = session.scalars(select(ElementHistory).where(
        ElementHistory.element_ref == t2.id, ElementHistory.action == "merge")).all()
    assert len(merges) == 1


def test_non_mergeable_type_not_merged(session):
    pid = _project(session)
    m1 = _material(session, pid, "系统应支持导出。")
    _recognize(session, pid, m1, [("functional_requirement", "系统应支持导出", 0.9)], "K1")
    m2 = _material(session, pid, "系统应支持导出。")
    _recognize(session, pid, m2, [("functional_requirement", "系统应支持导出", 0.9)], "K2")
    fr = session.scalars(select(RequirementElement).where(
        RequirementElement.element_type == "functional_requirement",
        RequirementElement.superseded.is_(False))).all()
    assert len(fr) == 2  # 需求表达类不归并（重复由复核/评审裁定）


def test_role_and_external_system_merge(session):
    pid = _project(session)
    m1 = _material(session, pid, "拣货员负责取货。外部支付网关处理扣款。")
    _recognize(session, pid, m1, [("role", "拣货员", 0.9), ("external_system", "外部支付网关", 0.9)], "K1")
    m2 = _material(session, pid, "拣货员执行复核。外部支付网关回调结果。")
    _recognize(session, pid, m2, [("role", "拣货员", 0.9), ("external_system", "外部支付网关", 0.9)], "K2")
    roles = session.scalars(select(RequirementElement).where(
        RequirementElement.element_type == "role", RequirementElement.superseded.is_(False))).all()
    ext = session.scalars(select(RequirementElement).where(
        RequirementElement.element_type == "external_system",
        RequirementElement.superseded.is_(False))).all()
    assert len(roles) == 1 and len(ext) == 1  # 角色/外部系统按名归并


def test_name_normalization_boundaries():
    assert normalize_element_name("  履约单 ") == "履约单"            # 去空白
    assert normalize_element_name("ＷＭＳ") == "wms"                  # 全角→半角 + 小写
    assert normalize_element_name("履约单：从下单到出库") == "履约单"   # 冒号切分
    assert normalize_element_name("履约单是指从下单到出库") == "履约单"  # 是指切分
    assert normalize_element_name("") == ""


# ---- 既有知识项在本材料工作区的可见投影（纯读，零迁移）----


def test_merged_existing_visible_in_workspace_when_pending(session):
    """未确认态归并：第二份材料的工作区能看到被归并到的既有知识项。"""
    pid = _project(session)
    m1 = _material(session, pid, "履约单是指从下单到出库的完整流程。")
    _recognize(session, pid, m1, [("term", "履约单是指从下单到出库的完整流程", 0.9)], "K1")
    m2 = _material(session, pid, "履约单是指订单处理的作业指令。")
    svc2, ctx2 = _recognize(session, pid, m2, [("term", "履约单是指订单处理的作业指令", 0.9)], "K2")

    ws = svc2.read_element_workspace(ctx2)
    assert [e.content for e in ws.elements] == []  # 本次识别全被归并，无新建要素
    assert len(ws.merged_existing_elements) == 1
    existing = ws.merged_existing_elements[0]
    assert existing.content == "履约单是指从下单到出库的完整流程"   # 既有那条的当前表达
    assert existing.process_status == ElementProcessStatus.PENDING_CONFIRMATION
    assert m2 in (existing.source_anchor or "")                    # 锚点已换算到本材料


def test_merged_existing_visible_when_target_confirmed(session):
    """确认态归并：走锚点追加草案，工作区同样可见，且带出草案留痕。"""
    pid = _project(session)
    m1 = _material(session, pid, "波次是指批量拣货任务。")
    _recognize(session, pid, m1, [("term", "波次是指批量拣货任务", 0.9)], "K1")
    t = _live_terms(session)[0]
    t.process_status = ElementProcessStatus.CONFIRMED.value
    session.commit()

    m2 = _material(session, pid, "波次是指按配送区域合并的拣货批次。")
    svc2, ctx2 = _recognize(session, pid, m2, [("term", "波次是指按配送区域合并的拣货批次", 0.9)], "K2")

    ws = svc2.read_element_workspace(ctx2)
    assert len(ws.merged_existing_elements) == 1
    existing = ws.merged_existing_elements[0]
    assert existing.process_status == ElementProcessStatus.CONFIRMED
    assert (existing.revision_draft or "").startswith("[锚点追加草案]")
    assert m2 in (existing.source_anchor or "")


def test_no_merge_yields_empty_existing_set(session):
    """无归并（首份材料 / 不可归并类型）：投影为空集，不影响原有工作区。"""
    pid = _project(session)
    m1 = _material(session, pid, "系统应支持导出。拣货员负责取货。")
    svc1, ctx1 = _recognize(
        session, pid, m1,
        [("functional_requirement", "系统应支持导出", 0.9), ("role", "拣货员", 0.9)], "K1",
    )
    ws = svc1.read_element_workspace(ctx1)
    assert len(ws.elements) == 2
    assert ws.merged_existing_elements == []


def test_same_material_reidentified_does_not_double_list_element(session):
    """同一份材料重复识别后，上一轮的要素不再被当成「既有项」重复投影（冷审查裁定 C2）。

    第二轮识别把第一轮的同名要素当作既往同名要素归并，留下指向本材料的 merge 留痕；
    此后从总览恢复执行会落回第一轮那个上下文。若读侧不排除，同一个 id 会同时出现在
    elements 与 merged_existing_elements 里——界面按 id 判「既有」，两份副本都被判只读，
    该要素在知识抽取页彻底无法确认或重开。
    """
    pid = _project(session)
    m1 = _material(session, pid, "履约单是指从下单到出库的完整流程。")
    svc1, ctx1 = _recognize(session, pid, m1, [("term", "履约单是指从下单到出库的完整流程", 0.9)], "K1")
    # 同一份材料再识别一次
    _recognize(session, pid, m1, [("term", "履约单是指从下单到出库的完整流程", 0.9)], "K2")

    ws = svc1.read_element_workspace(ctx1)
    element_ids = {e.id for e in ws.elements}
    assert len(element_ids) == 1
    assert [e.id for e in ws.merged_existing_elements] == []  # 不再重复列一遍
    # 该要素仍是可裁决的普通项（不被界面判为只读的「已有」项）
    assert ws.elements[0].process_status == ElementProcessStatus.PENDING_CONFIRMATION


def test_merged_existing_without_anchor_gets_none_not_other_material_anchor(session):
    """归并项没有引文时，锚点给空而不是回落到既往材料的锚点（冷审查裁定 C6）。

    回落会把别的材料的锚点投影到本材料工作区上，前端比对材料引用不等即判失效，
    打出「来源定位待修正」并指向一个对既有项恒禁用的按钮——假告警加点不动的指引。
    """
    pid = _project(session)
    m1 = _material(session, pid, "拣货员负责取货。")
    _recognize(session, pid, m1, [("role", "拣货员", 0.9)], "K1")

    m2 = _material(session, pid, "拣货员执行复核。")
    # 模型这次没给引文：识别项的 source_anchor 为空 → build_anchor_json 给 None
    svc2, ctx2 = _recognize(session, pid, m2, [("role", "拣货员", 0.9)], "K2", anchor_quote="")

    ws = svc2.read_element_workspace(ctx2)
    assert len(ws.merged_existing_elements) == 1
    assert ws.merged_existing_elements[0].source_anchor is None
