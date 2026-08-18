"""通知徽标(04A §2.1):去重/复发转未读/已读、生产点挂钩、HTTP 端点。"""
import pytest
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401  register tables
from app.db.base import Base, make_engine, make_session_factory
from app.deps import get_notification_repo
from app.main import app
from app.repositories.agent_run import SqlAgentRunRepository
from app.repositories.notification import SqlNotificationRepository
from app.services.notification import notify_export_failed


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


# ---- 仓储:insert-or-touch 去重语义 ----


def test_notify_creates_unread_item(session):
    repo = SqlNotificationRepository(session)
    nid = repo.notify(kind="agent_run.failed", dedup_key="k1", title="AI 任务失败", summary="可重试")

    items = repo.list()
    assert len(items) == 1
    assert str(items[0].id) == nid
    assert items[0].read_at is None
    assert items[0].occurrences == 1
    assert repo.unread_count() == 1


def test_notify_same_dedup_key_touches_not_duplicates(session):
    repo = SqlNotificationRepository(session)
    first = repo.notify(kind="agent_run.failed", dedup_key="k1", title="t", summary="s")
    second = repo.notify(kind="agent_run.failed", dedup_key="k1", title="t", summary="s")

    assert first == second  # 同一事项
    items = repo.list()
    assert len(items) == 1  # 不重复加条目(徽标按事项去重)
    assert items[0].occurrences == 2
    assert repo.unread_count() == 1


def test_recurrence_after_read_becomes_unread_again(session):
    repo = SqlNotificationRepository(session)
    nid = repo.notify(kind="export.failed", dedup_key="k2", title="t", summary="s")
    repo.mark_read(nid)
    assert repo.unread_count() == 0

    repo.notify(kind="export.failed", dedup_key="k2", title="t", summary="s")  # 复发
    assert repo.unread_count() == 1  # 需再次处理 → 回未读


def test_mark_read_and_mark_all_read(session):
    repo = SqlNotificationRepository(session)
    a = repo.notify(kind="agent_run.failed", dedup_key="ka", title="a", summary="")
    repo.notify(kind="agent_run.failed", dedup_key="kb", title="b", summary="")
    assert repo.unread_count() == 2

    repo.mark_read(a)
    assert repo.unread_count() == 1
    assert [n.dedup_key for n in repo.list(unread_only=True)] == ["kb"]

    assert repo.mark_all_read() == 1
    assert repo.unread_count() == 0

    assert repo.mark_read("00000000-0000-0000-0000-000000000000") is None


# ---- 生产点:AgentRun 失败 / 导出失败 ----


def test_mark_failed_produces_notification_without_raw_error(session):
    runs = SqlAgentRunRepository(session)
    run_id = runs.create("item_formation")
    runs.mark_failed(run_id, "Boom: raw exception detail with secrets")

    repo = SqlNotificationRepository(session)
    items = repo.list()
    assert len(items) == 1
    n = items[0]
    assert n.kind == "agent_run.failed"
    assert str(n.ref) == run_id
    assert "需求条目形成" in n.title  # kind → 可读任务名
    # 铁律:通知不携带 error 原文
    assert "Boom" not in n.title and "Boom" not in n.summary
    assert repo.unread_count() == 1

    # 同一 run 重复标记失败 → 触发去重,不新增
    runs.mark_failed(run_id, "again")
    assert len(repo.list()) == 1


def test_notify_export_failed_helper(session):
    notify_export_failed(session, "11111111-1111-1111-1111-111111111111", "需求规格说明", None)
    items = SqlNotificationRepository(session).list()
    assert len(items) == 1
    assert items[0].kind == "export.failed"
    assert "需求规格说明" in items[0].title


# ---- HTTP 端点(sqlite 覆盖依赖)----


@pytest.fixture()
def client():
    # TestClient 在独立线程执行端点:内存 sqlite 需 StaticPool 共享单连接,否则各线程各见各库
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    def _override():
        s = factory()
        try:
            yield SqlNotificationRepository(s)
        finally:
            s.close()

    app.dependency_overrides[get_notification_repo] = _override
    try:
        yield TestClient(app), factory
    finally:
        app.dependency_overrides.pop(get_notification_repo, None)
        engine.dispose()


def _seed(factory, dedup_key: str, title: str = "AI 任务失败：来源接入判定") -> str:
    s = factory()
    try:
        repo = SqlNotificationRepository(s)
        nid = repo.notify(kind="agent_run.failed", dedup_key=dedup_key, title=title, summary="可重试")
        s.commit()
        return nid
    finally:
        s.close()


def test_http_list_and_unread_count(client):
    c, factory = client
    _seed(factory, "h1")
    _seed(factory, "h2")

    r = c.get("/api/notifications")
    assert r.status_code == 200
    body = r.json()
    assert body["unread_count"] == 2
    assert len(body["notifications"]) == 2
    item = body["notifications"][0]
    assert set(item) >= {"id", "kind", "title", "summary", "occurrences", "read", "created_at", "updated_at"}
    assert item["read"] is False


def test_http_mark_read_then_unread_filter(client):
    c, factory = client
    nid = _seed(factory, "h3")
    _seed(factory, "h4")

    r = c.post(f"/api/notifications/{nid}/read")
    assert r.status_code == 200
    assert r.json() == {"status": "marked_read", "unread_count": 1}

    r2 = c.post(f"/api/notifications/{nid}/read")
    assert r2.json()["status"] == "already_read"

    r3 = c.get("/api/notifications", params={"unread": True})
    assert [n["id"] for n in r3.json()["notifications"]] != [nid]
    assert r3.json()["unread_count"] == 1


def test_http_read_all_and_not_found(client):
    c, factory = client
    _seed(factory, "h5")
    _seed(factory, "h6")

    r = c.post("/api/notifications/read-all")
    assert r.status_code == 200
    assert r.json() == {"status": "all_read", "unread_count": 0}

    r2 = c.post("/api/notifications/00000000-0000-0000-0000-000000000000/read")
    assert r2.status_code == 404
