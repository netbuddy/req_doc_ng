"""SQLAlchemy 基座：Base + engine/session 工厂（跨 Postgres/SQLite）。"""
from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    # SQLite 需 check_same_thread=False（TestClient/多线程）；Postgres 忽略该参数。
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=True, expire_on_commit=False, future=True)
