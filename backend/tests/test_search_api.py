"""全局检索 P2 · 检索 API 契约与降级测试（09 §2 P2 AC）。

在 SQLite（Python 子串降级 lane）下验证 GET /api/search 的契约与 DTO 形状——降级 AC 与完整能力 AC
共用同一断言集（03 §4）；pgvector/pg_trgm 完整混合检索在 live Postgres 经 curl 验证。
覆盖：REQ 编号精确置顶 + project_name/workbench/ref；跨项目标注与分组；types 过滤；limit/total；
空态 200+空 groups；q 缺失 422；workbench 服务端派生（item→management/chart→diagram/document→release）。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.adapters.embeddings import StubEmbedder
from app.db.base import Base, make_session_factory
from app.db.models import (
    Material,
    Project,
    RequirementChart,
    RequirementDocument,
    RequirementItem,
)
from app.deps import get_search_service
from app.main import app
from app.services.search import SearchService
from app.services.search_index import SearchIndexer
from app.services.search_source import RelationalSearchSource


@pytest.fixture()
def session():
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


def _project(s, name: str) -> str:
    p = Project(name=name)
    s.add(p)
    s.flush()
    return str(p.id)


def _item(s, pid: str, req_no: str, expr: str) -> None:
    s.add(RequirementItem(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.uuid4(),
        formation_context_ref=uuid.uuid4(), req_no=req_no, expression=expr,
        req_type="functional", status="confirmed", source_element_refs="[]",
    ))


@pytest.fixture()
def seeded(session):
    """项目 A：REQ-001 对账条目 + 订单流程图 + 文档 + 材料；项目 B：REQ-050 对账条目（跨项目同词）。"""
    pa = _project(session, "电商订单中心")
    pb = _project(session, "结算中心")
    _item(session, pa, "REQ-001", "系统应在流水截止后自动生成对账报告并推送")
    _item(session, pa, "REQ-002", "系统应支持订单批量导出")
    _item(session, pb, "REQ-050", "系统应按日切执行对账核销")
    session.add(RequirementChart(
        project_id=uuid.UUID(pa), title="订单处理主流程", chart_kind="diagram",
        chart_type="flowchart", format="mermaid", status="confirmed",
    ))
    session.add(RequirementDocument(
        project_id=uuid.UUID(pa), title="订单需求规格说明书", doc_type="srs", status="draft",
    ))
    session.add(Material(project_id=uuid.UUID(pa), raw_text="订单评审纪要：对账时效需提升", source_note="评审纪要"))
    session.commit()
    SearchIndexer(session, RelationalSearchSource(session), StubEmbedder()).reindex_all()
    return {"pa": pa, "pb": pb}


@pytest.fixture()
def client(session):
    def _override():
        yield SearchService(session, StubEmbedder())
        session.commit()

    app.dependency_overrides[get_search_service] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_search_service, None)


def _find_hit(body: dict, entity_type: str):
    for g in body["groups"]:
        if g["entity_type"] == entity_type:
            return g
    return None


# ---- 契约 ----

def test_req_no_exact_tops_and_carries_nav_fields(client, seeded):
    r = client.get("/api/search", params={"q": "REQ-001"})
    assert r.status_code == 200
    body = r.json()
    grp = _find_hit(body, "requirement_item")
    assert grp is not None and grp["label"] == "需求条目"
    top = grp["hits"][0]
    assert "REQ-001" in top["title"]  # 精确编号稳居首位
    # 携导航所需字段
    assert top["ref"] and top["project_name"] == "电商订单中心"
    assert top["workbench"] == "management"
    assert top["project_id"] == seeded["pa"]


def test_cross_project_grouping_and_project_tagging(client, seeded):
    r = client.get("/api/search", params={"q": "对账"})
    body = r.json()
    grp = _find_hit(body, "requirement_item")
    refs = {(h["project_id"], h["project_name"]) for h in grp["hits"]}
    # 跨项目同词命中：A 的 REQ-001 与 B 的 REQ-050 都在，且 project 标注正确。
    assert (seeded["pa"], "电商订单中心") in refs
    assert (seeded["pb"], "结算中心") in refs


def test_types_filter_returns_only_requested_group(client, seeded):
    r = client.get("/api/search", params={"q": "订单", "types": "chart"})
    body = r.json()
    assert {g["entity_type"] for g in body["groups"]} == {"chart"}
    assert _find_hit(body, "chart")["hits"][0]["workbench"] == "diagram"


def test_chinese_type_word_matches_chart(client, seeded):
    """"流程图"（chart_type flowchart 的中文标签，进 body）命中订单流程图。"""
    r = client.get("/api/search", params={"q": "流程图"})
    grp = _find_hit(r.json(), "chart")
    assert grp is not None and grp["hits"][0]["title"] == "订单处理主流程"


def test_document_workbench_is_release(client, seeded):
    r = client.get("/api/search", params={"q": "规格说明书"})
    grp = _find_hit(r.json(), "document")
    assert grp is not None and grp["hits"][0]["workbench"] == "release"


def test_limit_and_total(client, seeded):
    r = client.get("/api/search", params={"q": "系统", "limit": 1})
    body = r.json()
    grp = _find_hit(body, "requirement_item")
    # 三条条目均含"系统"，limit=1 → 每组只回 1 条，但 total 反映真实命中数。
    assert len(grp["hits"]) == 1
    assert grp["total"] >= 3
    assert body["total"] >= 3


def test_empty_result_is_200_with_empty_groups(client, seeded):
    r = client.get("/api/search", params={"q": "绝不存在的词XZQ9"})
    assert r.status_code == 200
    body = r.json()
    assert body["groups"] == [] and body["total"] == 0
    assert body["query"] == "绝不存在的词XZQ9"


def test_missing_q_is_422(client, seeded):
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_illegal_types_ignored_not_500(client, seeded):
    # 非法 type 忽略（不 422/500）；合法项仍生效。
    r = client.get("/api/search", params={"q": "对账", "types": "chart,bogus_type"})
    assert r.status_code == 200
