"""模板注册表（配置域）。

自 schemas.py 按业务域拆包（命名规范见包入口 __init__.py）。
"""
from __future__ import annotations

from pydantic import BaseModel

from .publication import TemplateDescriptorRead


# ---- 模板注册表（配置域：登记快照 / 停用 / 预览）----


class TemplateRegistryRead(BaseModel):
    """模板注册行读视图（登记快照；内容不外发，经 descriptor/预览消费）。"""

    registry_ref: str
    template_key: str
    version_no: int
    name: str
    schema_version: str
    doc_type: str
    content_hash: str
    source: str  # builtin / registered
    status: str  # active / disabled
    registered_by: str
    registered_at: str


class TemplateRegisterCommand(BaseModel):
    """模板登记入参：内容送检（内置 schema），失败整体拒绝不落库。"""

    content: str  # 模板文件 JSON 原文
    name: str | None = None  # 缺省取模板 title
    operator_ref: str
    idempotency_key: str


class TemplateRegistryDetailRead(TemplateRegistryRead):
    """模板注册行详情（含结构预览 descriptor）。"""

    descriptor: TemplateDescriptorRead


class TemplateStatusCommand(BaseModel):
    """模板停用/启用（唯一可变字段；无删除：基线引用内容须永久可解析）。"""

    status: str  # active / disabled
    operator_ref: str


class TemplateValidateCommand(BaseModel):
    """模板干跑送检入参（AEP-100：只校验，不落库不占版本号）。"""

    content: str  # 模板文件 JSON 原文


class TemplateValidationRead(BaseModel):
    """模板干跑送检结果（模板定制器实时校验消费）。"""

    ok: bool
    error: str | None = None  # 问题清单全文（一次性列出）
    descriptor: TemplateDescriptorRead | None = None  # ok 时的结构预览


class TemplateDraftRead(BaseModel):
    """模板定制草稿读视图（工作态：未送检、不占版本号、不可被发布消费）。"""

    draft_ref: str
    name: str
    payload: str  # 定制器状态 JSON 信封（designer_state_version + info/binding/tree）
    origin: str  # blank / copy / edit
    source_registry_ref: str | None = None  # copy/edit 起点登记行
    created_by: str
    created_at: str
    updated_at: str


class TemplateDraftSaveCommand(BaseModel):
    """草稿暂存入参（POST 新建 / PUT 覆盖；payload 后端不解析只存取）。"""

    name: str = ""  # 展示名（取定制器模板名称，可为空）
    payload: str
    origin: str = "blank"  # blank / copy / edit（仅新建时生效）
    source_registry_ref: str | None = None  # 仅新建时生效
    operator_ref: str
