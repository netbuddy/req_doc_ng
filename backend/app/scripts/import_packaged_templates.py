"""导入随软件包发布的模板文件到 template_registry。

用法：cd backend && uv run python -m app.scripts.import_packaged_templates
"""
from __future__ import annotations

from app.adapters.doc_template import builtin_template_files
from app.config import settings
from app.db.base import make_engine, make_session_factory
from app.repositories.templates import SqlTemplateRegistryRepository
from app.services.template_registry import PackagedTemplateImportReport, TemplateRegistryService


def import_packaged_templates(session) -> PackagedTemplateImportReport:
    """安装/数据库初始化入口复用的事务内导入函数。"""
    service = TemplateRegistryService(SqlTemplateRegistryRepository(session))
    return service.import_packaged_templates(builtin_template_files())


def main() -> None:
    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        report = import_packaged_templates(session)
        if report.failed:
            session.rollback()
            for failure in report.failures:
                print(f"模板导入失败：{failure.source_ref}：{failure.error}")
            raise SystemExit(1)
        session.commit()
        print(
            "模板导入完成："
            f"total={report.total}, imported={report.imported}, skipped={report.skipped}"
        )
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
