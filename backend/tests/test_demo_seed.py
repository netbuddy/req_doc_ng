"""演示项目 seed：启动不自动写入，脚本显式幂等创建。"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import DEMO_PROJECT_ID
from app.db.base import Base, make_engine, make_session_factory
from app.db.models import Project
from app.main import app
from app.scripts.seed_demo_project import ensure_demo_project


def test_app_startup_does_not_seed_demo_project(monkeypatch):
    called = False

    def forbidden_seed():
        nonlocal called
        called = True

    monkeypatch.setattr("app.main.ensure_demo_project", forbidden_seed, raising=False)
    monkeypatch.setattr("app.main.warn_if_async_without_worker", lambda: None)

    with TestClient(app):
        pass

    assert called is False


def test_app_startup_does_not_import_packaged_templates():
    import app.main as main

    assert not hasattr(main, "sync_" + "builtin_templates")
    assert "import_packaged_templates" not in vars(main)


def test_demo_project_seed_is_explicit_and_idempotent():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    try:
        first = ensure_demo_project(session)
        session.commit()
        second = ensure_demo_project(session)
        session.commit()

        rows = session.scalars(
            select(Project).where(Project.id == first)
        ).all()
        assert str(first) == DEMO_PROJECT_ID
        assert second == first
        assert len(rows) == 1
        assert rows[0].name == "Demo Project"
    finally:
        session.close()
