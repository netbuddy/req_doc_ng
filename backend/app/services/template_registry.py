"""模板注册表服务（配置域，UINV-19/20）。

边界：只登记、停用、预览——不编辑内容、不做版本治理。改内容 = 登记新版本行；
登记时按内置 schema 送检，校验失败整体拒绝不落库；行登记后不可变（status 除外），
发布基线引用的内容快照因此永远可解析。打包模板由初始化脚本显式导入。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from app.adapters.doc_template import (
    TemplateDescriptor,
    TemplateError,
    parse_template,
)
from app.api.schemas import (
    TemplateDescriptorRead,
    TemplateDraftRead,
    TemplateDraftSaveCommand,
    TemplateRegisterCommand,
    TemplateRegistryDetailRead,
    TemplateRegistryRead,
    TemplateSectionRead,
    TemplateValidationRead,
)
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.log import log_event
from app.repositories.templates import SqlTemplateDraftRepository, SqlTemplateRegistryRepository

_COMPONENT = "template-registry"


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PackagedTemplateImportFailure:
    source_ref: str
    error: str


@dataclass(frozen=True)
class PackagedTemplateImportReport:
    total: int
    imported: int
    skipped: int
    failed: int
    failures: tuple[PackagedTemplateImportFailure, ...] = ()


def descriptor_read(template_ref: str, descriptor: TemplateDescriptor) -> TemplateDescriptorRead:
    return TemplateDescriptorRead(
        template_ref=template_ref, schema_version=descriptor.schema_version,
        title=descriptor.title, description=descriptor.description,
        export_binding=descriptor.export_binding,
        sections=[TemplateSectionRead(
            key=s.key, number=s.number, title=s.title, level=s.level, purpose=s.purpose,
            content_types=list(s.content_types), required=s.required,
            repeatable=s.repeatable, missing_policy=s.missing_policy, boilerplate=s.boilerplate,
            examples=list(s.examples),
        ) for s in descriptor.sections],
    )


class TemplateRegistryService:
    def __init__(self, repo: SqlTemplateRegistryRepository) -> None:
        self._repo = repo

    # ---- 登记（内容送检 → 快照落库；失败整体拒绝） ----

    def register(self, command: TemplateRegisterCommand) -> TemplateRegistryRead:
        try:
            descriptor = parse_template(command.content, "（待登记模板）")
        except TemplateError as exc:
            raise InvalidInput(str(exc)) from exc

        content_hash = _hash(command.content)
        existing = self._repo.find_by_hash(content_hash)
        if existing is not None:  # 内容级幂等：同一内容不重复登记
            return self._row_read(existing)

        row = self._repo.add(
            template_key=descriptor.template_id,
            version_no=self._repo.next_version(descriptor.template_id),
            name=command.name or descriptor.title,
            schema_version=descriptor.schema_version, doc_type=descriptor.doc_type,
            content=command.content, content_hash=content_hash,
            source="registered", registered_by=command.operator_ref,
        )
        log_event(_COMPONENT, "template.registered", ok=True,
                  template_key=row.template_key, version=row.version_no,
                  content_hash=content_hash[:12], by=command.operator_ref)
        return self._row_read(row)

    def import_packaged_template(self, content: str, source_ref: str) -> tuple[TemplateRegistryRead, bool]:
        """导入一个随软件包发布的模板内容；返回 (注册行, 是否新建)。"""
        try:
            descriptor = parse_template(content, source_ref)
        except TemplateError as exc:
            raise InvalidInput(f"{source_ref}: {exc}") from exc

        content_hash = _hash(content)
        existing = self._repo.find_by_hash(content_hash)
        if existing is not None:
            return self._row_read(existing), False

        row = self._repo.add(
            template_key=descriptor.template_id,
            version_no=self._repo.next_version(descriptor.template_id),
            name=descriptor.title, schema_version=descriptor.schema_version,
            doc_type=descriptor.doc_type, content=content, content_hash=content_hash,
            source="builtin", registered_by="system",
        )
        return self._row_read(row), True

    def import_packaged_templates(self, files: Iterable[tuple[str, str]]) -> PackagedTemplateImportReport:
        """批量导入打包模板内容；调用方决定扫描目录、提交事务和失败退出策略。"""
        total = imported = skipped = 0
        failures: list[PackagedTemplateImportFailure] = []
        for source_ref, content in files:
            total += 1
            try:
                _, created = self.import_packaged_template(content, source_ref)
            except InvalidInput as exc:
                failures.append(PackagedTemplateImportFailure(source_ref=source_ref, error=str(exc)))
                continue
            if created:
                imported += 1
            else:
                skipped += 1
        report = PackagedTemplateImportReport(
            total=total, imported=imported, skipped=skipped,
            failed=len(failures), failures=tuple(failures),
        )
        log_event(
            _COMPONENT, "template.packaged.imported", ok=report.failed == 0,
            total=report.total, imported=report.imported,
            skipped=report.skipped, failed=report.failed,
        )
        return report

    def validate(self, content: str) -> TemplateValidationRead:
        """干跑送检（AEP-100）：与登记共用 parse_template；不落库、不占版本号。"""
        try:
            descriptor = parse_template(content, "（送检模板）")
        except TemplateError as exc:
            return TemplateValidationRead(ok=False, error=str(exc))
        return TemplateValidationRead(
            ok=True, descriptor=descriptor_read(descriptor.template_id, descriptor),
        )

    # ---- 读取 ----

    def list_templates(self) -> list[TemplateRegistryRead]:
        return [self._row_read(r) for r in self._repo.list_all()]

    def get_detail(self, registry_ref: str) -> TemplateRegistryDetailRead:
        row = self._repo.get(registry_ref)
        if row is None:
            raise NotFound("模板注册行不存在")
        read = self._row_read(row)
        try:
            descriptor = descriptor_read(row.template_key, parse_template(row.content, row.template_key))
        except TemplateError as exc:  # schema 升级后不兼容：登记内容不变，加载报不兼容
            descriptor = TemplateDescriptorRead(template_ref=row.template_key, error=str(exc))
        return TemplateRegistryDetailRead(**read.model_dump(), descriptor=descriptor)

    def load_descriptor(self, registry_ref: str) -> TemplateDescriptor:
        row = self._repo.get(registry_ref)
        if row is None:
            raise NotFound("模板注册行不存在")
        return parse_template(row.content, row.template_key)

    # ---- 停用（唯一可变字段；不提供删除：被基线引用的内容必须永久可解析） ----

    def set_status(self, registry_ref: str, status: str, operator_ref: str) -> TemplateRegistryRead:
        if status not in ("active", "disabled"):
            raise InvalidInput(f"非法状态：{status}（仅 active/disabled）")
        row = self._repo.get(registry_ref)
        if row is None:
            raise NotFound("模板注册行不存在")
        if row.source == "builtin" and status == "disabled":
            raise RejectedTransition("内置模板不可停用（系统默认模板必须始终可用）")
        self._repo.set_status(registry_ref, status)
        log_event(_COMPONENT, "template.status.changed", ok=True,
                  template_key=row.template_key, version=row.version_no,
                  status=status, by=operator_ref)
        return self._row_read(self._repo.get(registry_ref))

    def _row_read(self, row) -> TemplateRegistryRead:
        return TemplateRegistryRead(
            registry_ref=str(row.id), template_key=row.template_key, version_no=row.version_no,
            name=row.name, schema_version=row.schema_version, doc_type=row.doc_type,
            content_hash=row.content_hash, source=row.source, status=row.status,
            registered_by=row.registered_by,
            registered_at=row.created_at.isoformat() if row.created_at else "",
        )


class TemplateDraftService:
    """模板定制草稿服务（配置域工作态）。

    草稿是定制器状态的暂存，不是治理资产：未送检、不占版本号、发布侧不可见；
    因此允许覆盖更新与删除，不违反 UINV-20 的注册表不可变约束。
    payload 为前端定制器状态 JSON 信封，后端只存取不解析（大小设上限防误存）。
    """

    MAX_PAYLOAD_BYTES = 512 * 1024

    def __init__(self, repo: SqlTemplateDraftRepository) -> None:
        self._repo = repo

    def list_drafts(self) -> list[TemplateDraftRead]:
        return [self._read(r) for r in self._repo.list_all()]

    def create(self, command: TemplateDraftSaveCommand) -> TemplateDraftRead:
        self._check_payload(command.payload)
        if command.origin not in ("blank", "copy", "edit"):
            raise InvalidInput(f"非法草稿来源：{command.origin}（仅 blank/copy/edit）")
        row = self._repo.add(
            name=command.name.strip(), payload=command.payload, origin=command.origin,
            source_registry_ref=command.source_registry_ref, created_by=command.operator_ref,
        )
        log_event(_COMPONENT, "template.draft.created", ok=True,
                  draft_ref=str(row.id), origin=row.origin,
                  payload_bytes=len(command.payload.encode("utf-8")), by=command.operator_ref)
        return self._read(row)

    def update(self, draft_ref: str, command: TemplateDraftSaveCommand) -> TemplateDraftRead:
        self._check_payload(command.payload)
        row = self._repo.get(draft_ref)
        if row is None:
            raise NotFound("草稿不存在（可能已在其他会话登记或删除）")
        row = self._repo.update(row, name=command.name.strip(), payload=command.payload)
        log_event(_COMPONENT, "template.draft.updated", ok=True,
                  draft_ref=str(row.id),
                  payload_bytes=len(command.payload.encode("utf-8")), by=command.operator_ref)
        return self._read(row)

    def delete(self, draft_ref: str, operator_ref: str) -> None:
        row = self._repo.get(draft_ref)
        if row is None:  # 删除幂等：重复删除/已登记清理过均静默成功
            log_event(_COMPONENT, "template.draft.deleted", ok=True,
                      draft_ref=draft_ref, already_absent=True, by=operator_ref)
            return
        self._repo.delete(row)
        log_event(_COMPONENT, "template.draft.deleted", ok=True,
                  draft_ref=draft_ref, already_absent=False, by=operator_ref)

    def _check_payload(self, payload: str) -> None:
        if not payload.strip():
            raise InvalidInput("草稿内容为空")
        if len(payload.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
            raise InvalidInput("草稿内容超过 512KB 上限")

    def _read(self, row) -> TemplateDraftRead:
        return TemplateDraftRead(
            draft_ref=str(row.id), name=row.name, payload=row.payload, origin=row.origin,
            source_registry_ref=str(row.source_registry_ref) if row.source_registry_ref else None,
            created_by=row.created_by,
            created_at=row.created_at.isoformat() if row.created_at else "",
            updated_at=row.updated_at.isoformat() if row.updated_at else "",
        )


# ---- 样例内容生成（P2 样式预览：占位内容，不触碰任何治理资产） ----

_SAMPLE_ITEMS = {
    "requirement_item:functional": "**示例-FR-001**（功能需求）系统应支持将确认态需求条目按本模板导出为 docx 交付件。",
    "requirement_item:quality": "**示例-NFR-001**（质量属性）文档生成过程的单次导出耗时不超过五秒。",
    "requirement_item:constraint": "**示例-CON-001**（约束）系统必须部署在企业内网环境。",
    "requirement_item:data": "**示例-DAT-001**（数据需求）历史订单数据至少保留三年。",
    "requirement_item:interface": "**示例-IF-001**（接口需求）系统应提供 OpenAPI 兼容的查询接口。",
}


def build_sample_markdown(descriptor: TemplateDescriptor) -> str:
    """按模板章节结构生成占位样例 Markdown，用于展示导出版式（字体/缩进/层级）。"""
    lines: list[str] = []
    for s in descriptor.sections:
        lines.append(f"{'#' * s.level} {s.number} {s.title}")
        lines.append("")
        if s.boilerplate:
            text = s.boilerplate.replace("{project_name}", "示例项目").replace(
                "{coverage_scope}", "示例发布范围"
            )
            lines.append(text)
            lines.append("")
        for ct in s.content_types:
            if ct in _SAMPLE_ITEMS:
                lines.append(_SAMPLE_ITEMS[ct])
                lines.append("")
            elif ct == "material":
                lines.append("- 示例来源材料（来源版本 v1）")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"
