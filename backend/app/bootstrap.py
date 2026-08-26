"""单机模式启动准备（AppImage 单机模式方案 §4）。

服务器模式的库表由 alembic 迁移管理；单机模式用 SQLite，迁移脚本里 49 处 alter_column / drop_column
在 SQLite 上支持不全，改为启动时 `create_all` 建表（幂等：已有的表不动），并导入随包的内置文档模板
（幂等：已登记的跳过）。单机库的后续结构升级机制是已知遗留（方案 §8）。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from app.config import is_standalone
from app.db.base import Base, make_session_factory
from app.log import log_event

_COMPONENT = "bootstrap"


@dataclass(frozen=True)
class StandalonePrepareReport:
    tables: int
    templates_imported: int
    templates_skipped: int


def prepare_standalone_database(engine: Engine) -> StandalonePrepareReport | None:
    """SQLite 库：建全表 + 导入内置模板。非 SQLite 返回 None，不做任何事。"""
    if not is_standalone(str(engine.url)):
        return None
    import app.db.models  # noqa: F401  确保全部模型已注册到 Base.metadata
    from app.scripts.import_packaged_templates import import_packaged_templates

    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    try:
        report = import_packaged_templates(session)
        if report.failed:
            session.rollback()
            log_event(_COMPONENT, "standalone.templates.failed", level="ERROR", ok=False,
                      failed=report.failed)
        else:
            session.commit()
    finally:
        session.close()
    result = StandalonePrepareReport(
        tables=len(Base.metadata.tables),
        templates_imported=report.imported, templates_skipped=report.skipped,
    )
    log_event(_COMPONENT, "standalone.ready", ok=True, tables=result.tables,
              templates_imported=result.templates_imported, templates_skipped=result.templates_skipped)
    return result
