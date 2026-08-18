"""模板注册表路由（配置域，UINV-19/20：只登记/停用/预览，不编辑内容不做版本治理）。

响应约定同 publication：2xx 裸 DTO；校验失败/不存在/默认拒绝经异常处理器 → 400/404/409。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.adapters.docx_convert import convert_markdown_to_docx
from app.api.schemas import (
    TemplateDraftRead,
    TemplateDraftSaveCommand,
    TemplateRegisterCommand,
    TemplateRegistryDetailRead,
    TemplateRegistryRead,
    TemplateStatusCommand,
    TemplateValidateCommand,
    TemplateValidationRead,
)
from app.deps import get_template_draft_service, get_template_registry_service
from app.services.template_registry import (
    TemplateDraftService,
    TemplateRegistryService,
    build_sample_markdown,
)

router = APIRouter(tags=["templates"])


@router.get("/templates", response_model=list[TemplateRegistryRead])
def list_templates(
    service: TemplateRegistryService = Depends(get_template_registry_service),
) -> list[TemplateRegistryRead]:
    """全部注册模板（含停用；前端按 status 过滤可选项）。"""
    return service.list_templates()


@router.post("/templates", response_model=TemplateRegistryRead)
def register_template(
    command: TemplateRegisterCommand,
    service: TemplateRegistryService = Depends(get_template_registry_service),
) -> TemplateRegistryRead:
    """登记模板：内容按内置 schema 送检，失败整体拒绝（400 携问题清单）；同内容幂等。"""
    return service.register(command)


@router.post("/templates/validate", response_model=TemplateValidationRead)
def validate_template(
    command: TemplateValidateCommand,
    service: TemplateRegistryService = Depends(get_template_registry_service),
) -> TemplateValidationRead:
    """AEP-100：干跑送检（模板定制器实时校验）；只校验不落库不占版本号。"""
    return service.validate(command.content)


# ---- 模板定制草稿（工作态暂存；独立前缀避免与 /templates/{registry_ref} 冲突） ----


@router.get("/template-drafts", response_model=list[TemplateDraftRead])
def list_template_drafts(
    service: TemplateDraftService = Depends(get_template_draft_service),
) -> list[TemplateDraftRead]:
    """全部定制草稿（按最近更新排序；草稿未送检不占版本号，发布侧不可见）。"""
    return service.list_drafts()


@router.post("/template-drafts", response_model=TemplateDraftRead)
def create_template_draft(
    command: TemplateDraftSaveCommand,
    service: TemplateDraftService = Depends(get_template_draft_service),
) -> TemplateDraftRead:
    """新建草稿：暂存定制器状态信封，可退出后继续编辑。"""
    return service.create(command)


@router.put("/template-drafts/{draft_ref}", response_model=TemplateDraftRead)
def update_template_draft(
    draft_ref: str,
    command: TemplateDraftSaveCommand,
    service: TemplateDraftService = Depends(get_template_draft_service),
) -> TemplateDraftRead:
    """覆盖更新草稿（草稿是工作态，允许改写；不存在 → 404）。"""
    return service.update(draft_ref, command)


@router.delete("/template-drafts/{draft_ref}")
def delete_template_draft(
    draft_ref: str,
    operator_ref: str = "",
    service: TemplateDraftService = Depends(get_template_draft_service),
) -> dict:
    """删除草稿（幂等：登记成功后清理或人工放弃）。"""
    service.delete(draft_ref, operator_ref)
    return {"ok": True}


@router.get("/templates/{registry_ref}", response_model=TemplateRegistryDetailRead)
def read_template_detail(
    registry_ref: str,
    service: TemplateRegistryService = Depends(get_template_registry_service),
) -> TemplateRegistryDetailRead:
    """模板注册行详情 + 结构预览（章节树/槽位/必填规则/导出绑定）。"""
    return service.get_detail(registry_ref)


@router.post("/templates/{registry_ref}/status", response_model=TemplateRegistryRead)
def set_template_status(
    registry_ref: str,
    command: TemplateStatusCommand,
    service: TemplateRegistryService = Depends(get_template_registry_service),
) -> TemplateRegistryRead:
    """停用/启用（唯一可变字段；内置模板不可停用；无删除）。"""
    return service.set_status(registry_ref, command.status, command.operator_ref)


@router.get("/templates/{registry_ref}/preview-docx")
def preview_template_docx(
    registry_ref: str,
    service: TemplateRegistryService = Depends(get_template_registry_service),
):
    """样式预览：按模板章节结构 + 占位样例内容生成样例 docx（不触碰治理资产）。"""
    descriptor = service.load_descriptor(registry_ref)
    markdown = build_sample_markdown(descriptor)
    out = Path(tempfile.gettempdir()) / f"template-preview-{registry_ref}.docx"
    convert_markdown_to_docx(
        markdown, out, descriptor.export_binding,
        {"title": f"{descriptor.title}（样式预览）", "project_name": "示例项目", "version": "预览"},
    )
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="模板样式预览.docx",
    )
