"""项目上下文服务：创建/查询（service 单测 + HTTP）。

2026-08-07 项目管理组重构后形态：V2 应答信封、创建带操作者与幂等键（同键重放
返回同一项目）、列表只回摘要、status 死列已删。
"""
import pytest
from fastapi.testclient import TestClient

from app.api.schemas import CreateProjectCommand
from app.deps import get_project_service
from app.domain.errors import InvalidInput, NotFound
from app.main import app
from app.repositories.in_memory import InMemoryProjectRepository
from app.services.project_context import ProjectContextService


def _svc() -> ProjectContextService:
    return ProjectContextService(InMemoryProjectRepository())


def _cmd(name: str, key: str = "k-1", **extra) -> CreateProjectCommand:
    return CreateProjectCommand(name=name, operator_ref="测试者", idempotency_key=key, **extra)


# ---- service ----

def test_create_returns_project_detail():
    p = _svc().create_project(_cmd("我的项目", scope="范围", background="背景"))
    assert p.project_id and p.name == "我的项目" and p.scope == "范围"
    assert p.domain_profile_label  # 派生显示名恒有值（generic 兜底）


def test_create_empty_name_rejected():
    with pytest.raises(InvalidInput):
        _svc().create_project(_cmd("   "))


def test_create_idempotent_replay_returns_same_project():
    svc = _svc()
    first = svc.create_project(_cmd("网站需求", key="k-same"))
    replay = svc.create_project(_cmd("网站需求", key="k-same"))
    assert replay.project_id == first.project_id
    assert len(svc.list_projects()) == 1  # 同键不重复建行


def test_get_unknown_raises_not_found():
    with pytest.raises(NotFound):
        _svc().get_project("PRJ-NONE")


def test_list_and_get_roundtrip():
    svc = _svc()
    a = svc.create_project(_cmd("A", key="k-a"))
    svc.create_project(_cmd("B", key="k-b"))
    assert a.project_id in {row.id for row in svc.list_projects()}
    assert svc.get_project(a.project_id).name == "A"


# ---- HTTP（V2 信封）----

def test_http_project_crud():
    svc = _svc()  # 跨请求共享同一 in-memory service
    app.dependency_overrides[get_project_service] = lambda: svc
    try:
        client = TestClient(app)
        r = client.post("/api/projects", json={
            "name": "网站需求", "scope": "v1",
            "operator_ref": "测试者", "idempotency_key": "http-k1",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "成功"
        pid = body["data"]["project_id"]

        listing = client.get("/api/projects").json()
        assert listing["result"] == "成功"
        summary = next(p for p in listing["data"] if p["project_id"] == pid)
        assert set(summary) == {"project_id", "name", "created_at"}  # 列表只回摘要

        detail = client.get(f"/api/projects/{pid}").json()
        assert detail["result"] == "成功" and detail["data"]["name"] == "网站需求"
        assert detail["data"]["scope"] == "v1"

        # 空名 400；缺操作者/幂等键 422；未知项目 404
        assert client.post("/api/projects", json={
            "name": "   ", "operator_ref": "测试者", "idempotency_key": "http-k2",
        }).status_code == 400
        assert client.post("/api/projects", json={"name": "裸请求"}).status_code == 422
        assert client.get("/api/projects/PRJ-NONE").status_code == 404
    finally:
        app.dependency_overrides.pop(get_project_service, None)
