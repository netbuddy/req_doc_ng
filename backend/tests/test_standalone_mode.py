"""单机模式（AppImage 单机模式方案 §4）：REQDOC_HOME 推导默认值、SQLite 启动建库与模板导入、运行态说明。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from app.bootstrap import prepare_standalone_database
from app.config import home_defaults, is_standalone, resolve_home
from app.db.base import make_engine, make_session_factory
from app.services.runtime_status import _db_detail


def test_resolve_home_and_defaults(tmp_path):
    assert resolve_home({}) is None
    assert resolve_home({"REQDOC_HOME": "  "}) is None
    home = resolve_home({"REQDOC_HOME": str(tmp_path / "reqdoc")})
    assert home == (tmp_path / "reqdoc").resolve()
    d = home_defaults(home)
    assert d["DATABASE_URL"] == f"sqlite:///{home / 'req.db'}"
    assert d["EXPORT_DIR"] == str(home / "exports")
    assert home_defaults(None) == {}


def test_is_standalone_only_looks_at_url():
    assert is_standalone("sqlite:////home/u/.local/share/reqdoc/req.db")
    assert is_standalone("sqlite://")
    assert not is_standalone("postgresql+psycopg://req_doc@localhost:5432/req_v1")


def test_prepare_standalone_creates_tables_and_templates_idempotently(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'req.db'}")
    report = prepare_standalone_database(engine)
    assert report is not None and report.tables > 50
    names = set(inspect(engine).get_table_names())
    assert {"ldm014_docx_export", "template_registry"} <= names or "ldm014_docx_export" in names
    assert report.templates_imported > 0
    session = make_session_factory(engine)()
    try:
        count = session.execute(text("SELECT COUNT(*) FROM template_registry")).scalar()
    finally:
        session.close()
    assert count == report.templates_imported
    # 再跑一次：表不动、模板全部跳过
    again = prepare_standalone_database(engine)
    assert again.templates_imported == 0 and again.templates_skipped == report.templates_imported
    assert (tmp_path / "req.db").exists()


def test_prepare_is_noop_for_postgres_url(monkeypatch):
    class _FakeEngine:
        url = "postgresql+psycopg://req_doc@localhost:5432/req_v1"

    assert prepare_standalone_database(_FakeEngine()) is None  # type: ignore[arg-type]


def test_runtime_db_detail_mentions_standalone_and_lexical_only():
    assert "单机模式" in _db_detail(True, "sqlite") and "词法" in _db_detail(True, "sqlite")
    assert _db_detail(True, "postgresql") == "SELECT 1 探活通过"
    assert "探活失败" in _db_detail(False, "sqlite")
