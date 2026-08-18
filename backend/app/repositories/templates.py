"""模板注册表仓储（配置域：登记快照读写；行不可变，status 除外）。"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TemplateDraft, TemplateRegistry


class SqlTemplateRegistryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_hash(self, content_hash: str) -> Optional[TemplateRegistry]:
        stmt = select(TemplateRegistry).where(TemplateRegistry.content_hash == content_hash)
        return self._session.scalars(stmt).first()

    def get(self, registry_ref: str) -> Optional[TemplateRegistry]:
        return self._session.get(TemplateRegistry, uuid.UUID(str(registry_ref)))

    def latest_active(self, template_key: str) -> Optional[TemplateRegistry]:
        stmt = (
            select(TemplateRegistry)
            .where(TemplateRegistry.template_key == template_key, TemplateRegistry.status == "active")
            .order_by(TemplateRegistry.version_no.desc())
        )
        return self._session.scalars(stmt).first()

    def next_version(self, template_key: str) -> int:
        stmt = (
            select(TemplateRegistry)
            .where(TemplateRegistry.template_key == template_key)
            .order_by(TemplateRegistry.version_no.desc())
        )
        latest = self._session.scalars(stmt).first()
        return 1 if latest is None else latest.version_no + 1

    def list_all(self) -> list[TemplateRegistry]:
        stmt = select(TemplateRegistry).order_by(
            TemplateRegistry.template_key, TemplateRegistry.version_no.desc()
        )
        return list(self._session.scalars(stmt).all())

    def add(
        self, template_key: str, version_no: int, name: str, schema_version: str,
        doc_type: str, content: str, content_hash: str, source: str, registered_by: str,
    ) -> TemplateRegistry:
        row = TemplateRegistry(
            template_key=template_key, version_no=version_no, name=name,
            schema_version=schema_version, doc_type=doc_type, content=content,
            content_hash=content_hash, source=source, registered_by=registered_by,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def set_status(self, registry_ref: str, status: str) -> None:
        row = self.get(registry_ref)
        if row is not None:
            row.status = status
            self._session.flush()


class SqlTemplateDraftRepository:
    """模板定制草稿仓储（可变工作态：整行覆盖更新，允许删除）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, draft_ref: str) -> Optional[TemplateDraft]:
        return self._session.get(TemplateDraft, uuid.UUID(str(draft_ref)))

    def list_all(self) -> list[TemplateDraft]:
        stmt = select(TemplateDraft).order_by(TemplateDraft.updated_at.desc())
        return list(self._session.scalars(stmt).all())

    def add(
        self, name: str, payload: str, origin: str,
        source_registry_ref: Optional[str], created_by: str,
    ) -> TemplateDraft:
        row = TemplateDraft(
            name=name, payload=payload, origin=origin,
            source_registry_ref=uuid.UUID(str(source_registry_ref)) if source_registry_ref else None,
            created_by=created_by,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update(self, row: TemplateDraft, name: str, payload: str) -> TemplateDraft:
        row.name = name
        row.payload = payload
        self._session.flush()
        return row

    def delete(self, row: TemplateDraft) -> None:
        self._session.delete(row)
        self._session.flush()
