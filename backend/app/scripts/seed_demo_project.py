"""显式创建本地演示项目。

用法：cd backend && uv run python -m app.scripts.seed_demo_project
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.config import DEMO_PROJECT_ID, settings
from app.db.base import make_engine, make_session_factory
from app.db.models import Project

DEMO_PROJECT_NAME = "Demo Project"


def ensure_demo_project(session: Session, *, name: str = DEMO_PROJECT_NAME) -> uuid.UUID:
    """幂等创建固定 ID 的本地演示项目。"""
    pid = uuid.UUID(DEMO_PROJECT_ID)
    if session.get(Project, pid) is None:
        session.add(Project(id=pid, name=name))
        session.flush()
    return pid


def main() -> None:
    engine = make_engine(settings.database_url)
    session = make_session_factory(engine)()
    try:
        pid = ensure_demo_project(session)
        session.commit()
        print(f"seed 完成：演示项目 {pid}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
