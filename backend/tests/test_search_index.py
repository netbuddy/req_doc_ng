"""全局检索 P1 · 索引源缝与回填测试（09 §2 P1 AC）。

覆盖：五类投影计数与源实体一致（superseded 知识项排除）；ref = 稳定语义引用（源实体 id）；
body 走全文（回归 _head 200 截断陷阱）；content_hash 免重嵌（未变行 embedder 调用=0）；
删除对账 prune；stub 降级 embedding 全 NULL 不报错；中文类型标签进 body（"流程图"命中 flowchart）。
"""
import uuid

import pytest

import app.db.models  # noqa: F401
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import (
    Material,
    RequirementChart,
    RequirementDocument,
    RequirementElement,
    RequirementItem,
    Project,
    SearchIndex,
)
from app.adapters.embeddings import StubEmbedder
from app.services.search_index import SearchIndexer, content_hash
from app.services.search_source import RelationalSearchSource, ENTITY_TYPES


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


class CountingEmbedder:
    """记录 embed 调用与文本数（免重嵌断言）；恒返回 None（词法降级形状）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.texts = 0

    def embed(self, texts):
        self.calls += 1
        self.texts += len(texts)
        return [None] * len(texts)


def _project(s) -> str:
    p = Project(name="检索测试项目")
    s.add(p)
    s.flush()
    return str(p.id)


def _seed(s, pid: str, *, long_tail_keyword: str = "尾部对账时效关键词") -> dict:
    """种入五类源实体（含 1 条 superseded 知识项，应被排除）。返回各类 id。"""
    # 材料：raw_text > 200 字，尾部埋唯一关键词（验证全文 body 不截断）。
    long_text = "订单" * 250 + long_tail_keyword
    mat = Material(project_id=uuid.UUID(pid), raw_text=long_text, source_note="评审纪要")
    # 知识项：一条活跃 + 一条 superseded（后者应被投影排除）。
    el_active = RequirementElement(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.uuid4(),
        element_type="functional_requirement", content="系统应支持自动对账",
        process_status="confirmed", superseded=False,
    )
    el_dead = RequirementElement(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.uuid4(),
        element_type="functional_requirement", content="旧版被拆分要素",
        process_status="confirmed", superseded=True,
    )
    item = RequirementItem(
        project_id=uuid.UUID(pid), parse_result_ref=uuid.uuid4(),
        formation_context_ref=uuid.uuid4(), req_no="REQ-001",
        expression="系统应在流水截止后自动生成对账报告", req_type="functional",
        status="confirmed", source_element_refs="[]",
    )
    chart = RequirementChart(
        project_id=uuid.UUID(pid), title="订单处理主流程", chart_kind="diagram",
        chart_type="flowchart", format="mermaid", status="confirmed",
    )
    doc = RequirementDocument(
        project_id=uuid.UUID(pid), title="需求规格说明书", doc_type="srs", status="draft",
    )
    s.add_all([mat, el_active, el_dead, item, chart, doc])
    s.flush()
    ids = {
        "material": str(mat.id), "element_active": str(el_active.id),
        "element_dead": str(el_dead.id), "requirement_item": str(item.id),
        "chart": str(chart.id), "document": str(doc.id),
    }
    s.commit()
    return ids


# ---- 投影 ----

def test_projection_counts_and_refs(session):
    pid = _project(session)
    ids = _seed(session, pid)
    nodes = list(RelationalSearchSource(session).iter_nodes(pid))

    by_type: dict[str, list] = {t: [] for t in ENTITY_TYPES}
    for n in nodes:
        by_type[n.node_type].append(n)

    # 五类各 1（知识项排除 superseded 死版本）。
    assert {t: len(v) for t, v in by_type.items()} == {
        "material": 1, "element": 1, "requirement_item": 1, "chart": 1, "document": 1,
    }
    # ref = 源实体 id（稳定语义引用，非派生行 PK）。
    assert by_type["material"][0].ref == ids["material"]
    assert by_type["requirement_item"][0].ref == ids["requirement_item"]
    # superseded 死版本不出现。
    assert ids["element_dead"] not in {n.ref for n in by_type["element"]}
    assert by_type["element"][0].ref == ids["element_active"]


def test_body_full_text_not_truncated(session):
    """长材料（>200 字）尾部关键词进入 body（回归 asset_read._head 200 截断陷阱）。"""
    pid = _project(session)
    kw = "唯一尾部关键词XZ9"
    _seed(session, pid, long_tail_keyword=kw)
    nodes = list(RelationalSearchSource(session).iter_nodes(pid))
    mat = next(n for n in nodes if n.node_type == "material")
    assert len(mat.body) > 200
    assert kw in mat.body  # 若复用 _head(200) 截断则此断言失败


def test_chinese_type_label_in_body(session):
    """chart_type 码 flowchart 的中文标签"流程图"拼进 body（中文类型词可命中）。"""
    pid = _project(session)
    _seed(session, pid)
    nodes = list(RelationalSearchSource(session).iter_nodes(pid))
    chart = next(n for n in nodes if n.node_type == "chart")
    assert "流程图" in chart.body


# ---- 索引器 ----

def test_reindex_all_counts_match_source(session):
    pid = _project(session)
    _seed(session, pid)
    emb = StubEmbedder()
    SearchIndexer(session, RelationalSearchSource(session), emb).reindex_all()

    rows = session.query(SearchIndex).filter(SearchIndex.project_id == uuid.UUID(pid)).all()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    assert counts == {
        "material": 1, "element": 1, "requirement_item": 1, "chart": 1, "document": 1,
    }


def test_stub_embedder_leaves_embedding_null(session):
    pid = _project(session)
    _seed(session, pid)
    SearchIndexer(session, RelationalSearchSource(session), StubEmbedder()).reindex_project(pid)
    rows = session.query(SearchIndex).filter(SearchIndex.project_id == uuid.UUID(pid)).all()
    assert rows and all(r.embedding is None for r in rows)  # 全 NULL，不报错（降级）


def test_reindex_skips_unchanged_no_reembed(session):
    """二次 reindex_project 对未变行 embedder 调用次数=0（免重嵌 AC）。"""
    pid = _project(session)
    _seed(session, pid)
    emb = CountingEmbedder()
    indexer = SearchIndexer(session, RelationalSearchSource(session), emb)

    first = indexer.reindex_project(pid)
    assert first.embedded == 5 and emb.texts == 5  # 首轮五类全嵌
    emb.calls = 0
    emb.texts = 0
    second = indexer.reindex_project(pid)
    assert second.embedded == 0 and emb.calls == 0 and emb.texts == 0  # 未变 → 零重嵌


def test_reindex_prune_on_source_delete(session):
    """删一条源实体后 reindex，对应 search_index 行被 prune。"""
    pid = _project(session)
    ids = _seed(session, pid)
    indexer = SearchIndexer(session, RelationalSearchSource(session), StubEmbedder())
    indexer.reindex_project(pid)
    assert session.query(SearchIndex).filter(SearchIndex.project_id == uuid.UUID(pid)).count() == 5

    # 删除图表源实体。
    chart = session.get(RequirementChart, uuid.UUID(ids["chart"]))
    session.delete(chart)
    session.commit()

    stats = indexer.reindex_project(pid)
    assert stats.pruned == 1
    remaining = session.query(SearchIndex).filter(SearchIndex.project_id == uuid.UUID(pid)).all()
    assert len(remaining) == 4
    assert ("chart", ids["chart"]) not in {(r.entity_type, r.ref) for r in remaining}


def test_reindex_updates_changed_row(session):
    """源内容变更 → content_hash 变 → 该行重嵌并更新 body。"""
    pid = _project(session)
    ids = _seed(session, pid)
    emb = CountingEmbedder()
    indexer = SearchIndexer(session, RelationalSearchSource(session), emb)
    indexer.reindex_project(pid)

    item = session.get(RequirementItem, uuid.UUID(ids["requirement_item"]))
    item.expression = "系统应在对账完成后推送结算通知XYZ"
    session.commit()
    emb.calls = 0
    emb.texts = 0
    stats = indexer.reindex_project(pid)
    assert stats.embedded == 1 and emb.texts == 1  # 仅变更行重嵌
    row = session.query(SearchIndex).filter(
        SearchIndex.entity_type == "requirement_item", SearchIndex.ref == ids["requirement_item"]
    ).one()
    assert "XYZ" in row.body


def test_reindex_all_prunes_deleted_project(session):
    """整项目被删（连 Project 行）→ reindex_all 清除其孤儿 search_index 行（项目级删除对账）。"""
    pid_a = _project(session)
    pid_b = _project(session)
    _seed(session, pid_a)
    _seed(session, pid_b)
    indexer = SearchIndexer(session, RelationalSearchSource(session), StubEmbedder())
    indexer.reindex_all()
    assert session.query(SearchIndex).count() == 10  # 两项目各 5

    # 删除项目 B 的全部源实体 + Project 行（模拟整项目删除）。
    for model in (Material, RequirementElement, RequirementItem, RequirementChart, RequirementDocument):
        for row in session.query(model).filter(model.project_id == uuid.UUID(pid_b)).all():
            session.delete(row)
    session.delete(session.get(Project, uuid.UUID(pid_b)))
    session.commit()

    indexer.reindex_all()
    remaining = session.query(SearchIndex).all()
    assert len(remaining) == 5  # 仅项目 A 存留
    assert all(str(r.project_id) == pid_a for r in remaining)  # 无孤儿行


def test_reindex_node_incremental_interface(session):
    """reindex_node 单节点接口位（Phase 2 增量 hook）：upsert 与消失 prune。"""
    pid = _project(session)
    ids = _seed(session, pid)
    indexer = SearchIndexer(session, RelationalSearchSource(session), StubEmbedder())
    stats = indexer.reindex_node(pid, "chart", ids["chart"])
    assert stats.upserted == 1
    assert session.query(SearchIndex).filter(SearchIndex.ref == ids["chart"]).count() == 1

    # 源消失 → 单节点 prune。
    session.delete(session.get(RequirementChart, uuid.UUID(ids["chart"])))
    session.commit()
    stats2 = indexer.reindex_node(pid, "chart", ids["chart"])
    assert stats2.pruned == 1
    assert session.query(SearchIndex).filter(SearchIndex.ref == ids["chart"]).count() == 0
