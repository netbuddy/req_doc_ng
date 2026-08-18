"""需求转化链 + 数字桥 + 状态对账的派生口径测试（任务卡 T20260724-overview-conversion-chain A1/A4）。

口径事实源＝该卡「## 方案确认」节。本文件的职责是把界面上四组数字之间的**恒等式**钉住：
读者在界面看到的「81＋25＋3＝109＝资产盘点需求条目」这类对账行，成立与否取决于这些断言。

种子直写既有事实源表（列与真实写路径一致，与 test_overview_flows.py 同手法）。
"""
import json
import uuid

import pytest

import app.db.models  # noqa: F401  register tables
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    ItemFormationRequest,
    Material,
    MaterialParseResult,
    ParseRequest,
    Project,
    RequirementElement,
    RequirementItem,
)
from app.repositories.overview_read import OverviewReadRepository
from app.services.overview import OverviewService


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


# ---- 种子助手 ----

def _project(session, name="转化链演示") -> str:
    p = Project(name=name)
    session.add(p)
    session.flush()
    return str(p.id)


def _material_with_parse_result(session, pid) -> tuple[str, str]:
    """建一份材料并跑到「有解析结果」，返回 (material_ref, parse_result_ref)。"""
    m = Material(project_id=uuid.UUID(pid), raw_text="系统应支持导出 docx。", source_note="访谈纪要")
    session.add(m)
    session.flush()
    req = ParseRequest(
        project_id=uuid.UUID(pid), material_ref=m.id, operator_ref="U1",
        idempotency_key=f"P-{uuid.uuid4()}",
    )
    session.add(req)
    session.flush()
    result = MaterialParseResult(
        project_id=uuid.UUID(pid), material_ref=m.id, context_ref=req.id, parse_status="parsed",
    )
    session.add(result)
    session.flush()
    return str(m.id), str(result.id)


def _element(session, pid, pr, etype="functional_requirement",
             status="confirmed", superseded=False) -> str:
    e = RequirementElement(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.UUID(pr), element_type=etype,
        content="系统应支持导出 docx", process_status=status, superseded=superseded,
    )
    session.add(e)
    session.flush()
    return str(e.id)


def _formation(session, pid, pr) -> str:
    r = ItemFormationRequest(
        project_id=uuid.UUID(pid), parse_context_ref=uuid.uuid4(),
        parse_result_ref=uuid.UUID(pr), scope_type="all_eligible",
        operator_ref="U1", idempotency_key=f"F-{uuid.uuid4()}",
    )
    session.add(r)
    session.flush()
    return str(r.id)


def _item(session, pid, pr, fctx, req_type="functional",
          status="pending_confirmation", sources=()) -> str:
    i = RequirementItem(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.UUID(pr),
        formation_context_ref=uuid.UUID(fctx), req_no=f"REQ-{uuid.uuid4().hex[:6]}",
        expression="系统应支持导出 docx", req_type=req_type, status=status,
        source_element_refs=json.dumps(list(sources)),
    )
    session.add(i)
    session.flush()
    return str(i.id)


def _direct_item(session, pid, req_type="functional", status="pending_confirmation") -> str:
    """直建条目：无知识项来源，且 parse_result_ref 不指向任何解析结果（真实直建通道同形）。"""
    return _item(session, pid, str(uuid.uuid4()), str(uuid.uuid4()),
                 req_type=req_type, status=status, sources=())


def _svc(session) -> OverviewService:
    return OverviewService(OverviewReadRepository(session))


def _bridge(overview, key):
    return next(b for b in overview.type_bridge if b.key == key)


# ============================================================================
# A1 转化链四节点 + 恒等式
# ============================================================================

def _full_fixture(session) -> str:
    """一套覆盖全部分支的事实集：

    材料甲（已执行形成）：功能类已确认 3 个——其一形成同类条目、其二与其三归并成一条条目；
                          功能类已确认 1 个未被采用；质量类待确认 1 个；术语 1 个（非需求类）；
                          被替代 1 个与已撤销 1 个（都不计入存量）。
    材料乙（未执行形成）：约束类已确认 1 个、功能类待确认 1 个。
    材料丙（无需求类知识项）：只有场景 1 个——不进「识别出需求类知识项的材料」分母。
    另有直建条目 2 条（功能 1 条、数据 1 条）与四态条目各一。
    """
    pid = _project(session)

    mat_a, pr_a = _material_with_parse_result(session, pid)
    e_solo = _element(session, pid, pr_a)
    e_merge_1 = _element(session, pid, pr_a)
    e_merge_2 = _element(session, pid, pr_a)
    _element(session, pid, pr_a)  # 已确认但不被任何条目采用
    _element(session, pid, pr_a, etype="quality_attribute", status="pending_confirmation")
    _element(session, pid, pr_a, etype="term")
    _element(session, pid, pr_a, superseded=True)
    _element(session, pid, pr_a, etype="constraint", status="revoked")
    fctx_a = _formation(session, pid, pr_a)
    _item(session, pid, pr_a, fctx_a, sources=[e_solo])
    _item(session, pid, pr_a, fctx_a, status="confirmed", sources=[e_merge_1, e_merge_2])

    mat_b, pr_b = _material_with_parse_result(session, pid)
    _element(session, pid, pr_b, etype="constraint")
    _element(session, pid, pr_b, status="pending_confirmation")

    _mat_c, pr_c = _material_with_parse_result(session, pid)
    _element(session, pid, pr_c, etype="scenario")

    _direct_item(session, pid, req_type="functional", status="superseded")
    _direct_item(session, pid, req_type="data", status="terminated")
    return pid


def test_chain_four_nodes(session):
    pid = _full_fixture(session)
    chain = _svc(session).read_project_overview(pid).conversion_chain

    # 阶段一：存量 10 个（8 个材料甲的减去被替代与已撤销 2 个 ＝ 6，加材料乙 2、材料丙 1 ＝ 9）
    assert chain.elements_total == 9
    assert chain.elements_requirement == 7   # 功能 4＋质量 1（材料甲）＋约束 1＋功能 1（材料乙）
    assert chain.elements_other == 2         # 术语、场景
    # 阶段二
    assert chain.elements_confirmed == 5     # 材料甲功能 4 个 ＋ 材料乙约束 1 个
    assert chain.elements_pending == 2       # 材料甲质量 1 ＋ 材料乙功能 1
    # 阶段三：材料丙无需求类知识项，不进分母；直建条目不把不存在的材料算进已形成
    assert chain.materials_with_requirement == 2
    assert chain.materials_formed == 1
    assert chain.materials_unformed == 1
    # 产出
    assert chain.items_total == 4
    assert (chain.items_pending, chain.items_confirmed, chain.items_closed) == (1, 1, 2)
    assert chain.items_sourced == 2
    assert chain.items_direct == 2


def test_chain_identities_hold(session):
    """四条恒等式——界面对账行与进度条分母全靠它们成立。"""
    pid = _full_fixture(session)
    chain = _svc(session).read_project_overview(pid).conversion_chain

    assert chain.elements_total == chain.elements_requirement + chain.elements_other
    assert chain.elements_requirement == chain.elements_confirmed + chain.elements_pending
    assert chain.materials_with_requirement == chain.materials_formed + chain.materials_unformed
    assert chain.items_total == chain.items_pending + chain.items_confirmed + chain.items_closed
    assert chain.items_total == chain.items_sourced + chain.items_direct


def test_status_metrics_reconcile_with_asset_catalog(session):
    """A4：状态三瓦片之和 ＝ 资产盘点「需求条目」（同一次事实载入派生）。"""
    pid = _full_fixture(session)
    ov = _svc(session).read_project_overview(pid)
    status = {m.key: m.value for m in ov.requirement_status_metrics}
    assets = {m.key: m.value for m in ov.asset_metrics}

    assert status == {"pending": 1, "confirmed": 1, "closed": 2}
    assert sum(status.values()) == assets["items"] == ov.conversion_chain.items_total


def test_closed_counts_superseded_and_terminated(session):
    """已了结＝被替代 ＋ 已终止两态之和，缺一不可。"""
    pid = _project(session)
    _direct_item(session, pid, status="superseded")
    _direct_item(session, pid, status="terminated")
    _direct_item(session, pid, status="terminated")
    chain = _svc(session).read_project_overview(pid).conversion_chain
    assert chain.items_closed == 3
    assert chain.items_pending == 0 and chain.items_confirmed == 0


def test_type_metrics_equal_chain_requirement_total(session):
    """五个类型瓦片之和恒等于阶段一的「需求类」——两处数字同源，不许各算一遍。"""
    pid = _full_fixture(session)
    ov = _svc(session).read_project_overview(pid)
    assert sum(m.value for m in ov.requirement_type_metrics) == ov.conversion_chain.elements_requirement


# ============================================================================
# A1/A3 数字桥逐行账
# ============================================================================

def test_bridge_functional_rows(session):
    pid = _full_fixture(session)
    b = _bridge(_svc(session).read_project_overview(pid), "functional")

    # 行1：已有 ＝ 已确认 ＋ 待确认
    assert b.elements_total == 5           # 材料甲 4 个已确认 ＋ 材料乙 1 个待确认
    assert b.elements_confirmed == 4
    assert b.elements_pending == 1
    # 行2：已确认 ＝ 已进入形成 ＋ 尚未形成；残差按两种原因分开
    assert b.entered_formation == 3        # e_solo、e_merge_1、e_merge_2
    assert b.not_formed == 1
    assert b.not_formed_material_pending == 0
    assert b.not_formed_not_adopted == 1   # 材料甲执行过形成，但该知识项未被采用
    # 行3：3 个知识项 → 2 条条目（归并：两个知识项合成一条），全为功能类
    assert b.items_from_elements_same_type == 2
    assert b.items_from_elements_other_type == 0
    # 行4：该类条目 ＝ 来自知识项 ＋ 直建
    assert b.items_total == 3
    assert b.items_sourced == 2
    assert b.items_direct == 1


def test_bridge_merge_counts_item_once(session):
    """归并：两个知识项合成一条条目时，该条目只算一次（按条目 id 去重）。"""
    pid = _project(session)
    _mat, pr = _material_with_parse_result(session, pid)
    a = _element(session, pid, pr)
    c = _element(session, pid, pr)
    fctx = _formation(session, pid, pr)
    _item(session, pid, pr, fctx, sources=[a, c])

    b = _bridge(_svc(session).read_project_overview(pid), "functional")
    assert b.entered_formation == 2                  # 两个知识项都进入了形成
    assert b.items_from_elements_same_type == 1      # 但只产出一条条目
    assert b.items_total == 1


def test_bridge_split_counts_each_item(session):
    """拆分：一个知识项拆成两条条目时，两条都计入去向。"""
    pid = _project(session)
    _mat, pr = _material_with_parse_result(session, pid)
    a = _element(session, pid, pr)
    fctx = _formation(session, pid, pr)
    _item(session, pid, pr, fctx, sources=[a])
    _item(session, pid, pr, fctx, sources=[a])

    b = _bridge(_svc(session).read_project_overview(pid), "functional")
    assert b.entered_formation == 1
    assert b.items_from_elements_same_type == 2
    assert b.items_total == 2


def test_bridge_cross_type_item(session):
    """形成时被定为其它类型：功能类知识项产出一条质量类条目，计入「其它类型」而非本类。"""
    pid = _project(session)
    _mat, pr = _material_with_parse_result(session, pid)
    a = _element(session, pid, pr)
    fctx = _formation(session, pid, pr)
    _item(session, pid, pr, fctx, req_type="quality", sources=[a])

    ov = _svc(session).read_project_overview(pid)
    functional = _bridge(ov, "functional")
    assert functional.entered_formation == 1
    assert functional.items_from_elements_same_type == 0
    assert functional.items_from_elements_other_type == 1
    assert functional.items_total == 0                 # 功能类条目一条也没有
    quality = _bridge(ov, "quality")
    assert quality.items_total == 1 and quality.items_sourced == 1


def test_bridge_not_formed_reason_material_pending(session):
    """所在材料尚未执行形成：残差归入「材料未执行」，不是「未被采用」——界面措辞据此选择。"""
    pid = _project(session)
    _mat, pr = _material_with_parse_result(session, pid)
    _element(session, pid, pr)
    b = _bridge(_svc(session).read_project_overview(pid), "functional")
    assert b.entered_formation == 0
    assert (b.not_formed, b.not_formed_material_pending, b.not_formed_not_adopted) == (1, 1, 0)


def test_bridge_rows_close_for_every_type(session):
    """五类的行内加总逐一闭合（含零知识项的类型）。"""
    pid = _full_fixture(session)
    ov = _svc(session).read_project_overview(pid)
    assert [b.key for b in ov.type_bridge] == [
        "functional", "quality", "constraint", "data", "interface",
    ]
    for b in ov.type_bridge:
        assert b.elements_total == b.elements_confirmed + b.elements_pending
        assert b.elements_confirmed == b.entered_formation + b.not_formed
        assert b.not_formed == b.not_formed_material_pending + b.not_formed_not_adopted
        assert b.items_total == b.items_sourced + b.items_direct


def test_bridge_zero_type_is_all_zero(session):
    """零知识项的类型给全零行（前端据此渲染空态，不显示 0÷0 式空账）。"""
    pid = _full_fixture(session)
    b = _bridge(_svc(session).read_project_overview(pid), "interface")
    assert (b.elements_total, b.elements_confirmed, b.entered_formation, b.items_total) == (0, 0, 0, 0)


def test_empty_project_chain_all_zero(session):
    pid = _project(session, name="空白项目")
    ov = _svc(session).read_project_overview(pid)
    chain = ov.conversion_chain
    assert chain.elements_total == 0 and chain.items_total == 0
    assert chain.materials_with_requirement == 0
    assert len(ov.type_bridge) == 5
    assert all(b.elements_total == 0 and b.items_total == 0 for b in ov.type_bridge)


def test_superseded_and_revoked_elements_excluded(session):
    """存量口径：被拆分归并替代的与已撤销的知识项不进转化链任何节点。"""
    pid = _project(session)
    _mat, pr = _material_with_parse_result(session, pid)
    _element(session, pid, pr, superseded=True)
    _element(session, pid, pr, status="revoked")
    chain = _svc(session).read_project_overview(pid).conversion_chain
    assert chain.elements_total == 0
    assert chain.elements_requirement == 0
    assert chain.materials_with_requirement == 0   # 该材料没有存量需求类知识项


def test_http_shape_carries_chain_and_bridge():
    """端点形状：加性字段随既有响应一并下发（前端一次往返取全）。"""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.deps import get_overview_service
    from app.main import app

    # 共享内存库（StaticPool）：TestClient 在 threadpool 线程跑 sync 路由，需跨线程共用同一 DB。
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = make_session_factory(engine)()
    pid = _full_fixture(s)
    s.commit()

    app.dependency_overrides[get_overview_service] = lambda: _svc(s)
    try:
        r = TestClient(app).get(f"/api/projects/{pid}/overview")
        assert r.status_code == 200
        body = r.json()
        chain = body["conversion_chain"]
        assert chain["items_total"] == 4
        assert chain["items_pending"] + chain["items_confirmed"] + chain["items_closed"] == 4
        assert {m["key"] for m in body["requirement_status_metrics"]} == {
            "pending", "confirmed", "closed"}
        assert [b["key"] for b in body["type_bridge"]] == [
            "functional", "quality", "constraint", "data", "interface"]
    finally:
        app.dependency_overrides.pop(get_overview_service, None)
        s.close()
        engine.dispose()
