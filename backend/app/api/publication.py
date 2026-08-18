"""发布管理路由（SCN-005：P01 索引编排 / P02 Markdown 定稿 / P03 docx 导出与发布基线）。

响应约定同 analysis：2xx 裸 DTO；业务结局在 status/next_action；
默认拒绝/不存在经异常处理器 → 409/404。界面只承接意图，正式写入在服务侧。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.schemas import (
    AssetFragmentRead,
    CandidatePreviewRead,
    ConfirmBaselineCommand,
    ConfirmBaselineResult,
    DocxExportRead,
    DraftManuscriptCommand,
    ExportCheckCommand,
    FinalizeMarkdownCommand,
    FinalizeMarkdownResult,
    GenerateMarkdownCommand,
    ItemConfirmCommand,
    ItemConfirmResult,
    ManualFallbackCommand,
    MarkdownDraftRead,
    MarkdownEditCommand,
    MarkdownEditResult,
    PublicationWorkspaceRead,
    ReleaseBaselineRead,
    ReopenIndexCommand,
    RequirementDocumentRead,
    SaveIndexCommand,
    SaveIndexResult,
    SaveManuscriptCommand,
    SectionDraftResultRead,
    SectionManuscriptRead,
    StartDocxExportCommand,
    StartDocxExportResult,
)
from app.deps import get_export_execution_service, get_publication_service
from app.domain.errors import NotFound
from app.services.publication import DocumentOrchestrationService, ExportExecutionService

router = APIRouter(tags=["publication"])
_PREFIX = "/projects/{project_id}/publication"


def _check_project(project_id: str, project_ref: str) -> None:
    if project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")


@router.get(_PREFIX + "/workspace", response_model=PublicationWorkspaceRead)
def read_publication_workspace(
    project_id: str,
    template_ref: str | None = None,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> PublicationWorkspaceRead:
    """发布工作台唯一读视图（模板章节/候选池/索引/Markdown/导出件/基线同一版本）。"""
    return service.read_workspace(project_id, template_ref)


@router.get(_PREFIX + "/asset-fragment", response_model=AssetFragmentRead)
def read_asset_fragment(
    project_id: str,
    asset_type: str,
    asset_ref: str,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> AssetFragmentRead:
    """资产 → 文档片段追溯预览（只读；追溯依据不入 docx 正文，绑定由生成时落库）。"""
    return service.read_asset_fragment(project_id, asset_type, asset_ref)


@router.get(_PREFIX + "/candidate-preview", response_model=CandidatePreviewRead)
def read_candidate_preview(
    project_id: str,
    asset_type: str,
    asset_ref: str,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> CandidatePreviewRead:
    """AEP-099：候选资产最终渲染预览（与生成稿同一确定性渲染器；只读）。"""
    return service.candidate_preview(project_id, asset_type, asset_ref)


@router.post(_PREFIX + "/manuscripts", response_model=SectionManuscriptRead)
def save_section_manuscript(
    project_id: str,
    command: SaveManuscriptCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> SectionManuscriptRead:
    """AEP-098：保存章节撰稿（人工正文第一类来源）；content 空白 = 回落模板默认文本。"""
    _check_project(project_id, command.project_ref)
    return service.save_section_manuscript(command)


@router.post(
    _PREFIX + "/manuscripts/{section_key}/draft",
    response_model=SectionDraftResultRead,
    operation_id="draft_section_manuscript",
)
def draft_section_manuscript(
    project_id: str,
    section_key: str,
    command: DraftManuscriptCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> SectionDraftResultRead:
    """AEP-110：为 authored_text 章节 AI 起草初稿并写入撰稿（人工可改可确认）。

    仅撰稿阶段预填初稿；发布渲染仍确定性投影。非 authored_text 章节拒绝。

    响应是信封（T20260721）：起草成功 status='drafted' 带撰稿；模型拒绝起草 status='declined'
    带理由原文，同为 HTTP 200——拒绝是正常业务结果，当错误抛会让理由被 URL 与状态码前缀淹没。
    模型服务不可用与用错入口仍是 400。
    """
    _check_project(project_id, command.project_ref)
    return service.draft_section_manuscript(
        command.project_ref, section_key, command.operator_ref, command.template_ref,
    )


@router.post(_PREFIX + "/index", response_model=SaveIndexResult)
def save_content_index(
    project_id: str,
    command: SaveIndexCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> SaveIndexResult:
    """P01：保存文档内容索引（准入校验；必填缺失→受阻+缺失清单，不降低确认门禁）。"""
    _check_project(project_id, command.project_ref)
    return service.save_content_index(command)


@router.post(_PREFIX + "/markdown/generate", response_model=MarkdownDraftRead)
def generate_markdown(
    project_id: str,
    command: GenerateMarkdownCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> MarkdownDraftRead:
    """P02：从就绪索引生成/重新生成 Markdown 中间稿（不改索引与正式资产）。"""
    _check_project(project_id, command.project_ref)
    return service.generate_markdown(command)


@router.post(_PREFIX + "/markdown/edit", response_model=MarkdownEditResult)
def record_markdown_edit(
    project_id: str,
    command: MarkdownEditCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> MarkdownEditResult:
    """P02：窗口微调补丁记录 + 编辑影响识别（补丁未定稿前不是正式资产）。"""
    _check_project(project_id, command.project_ref)
    return service.record_edit(command)


@router.post(_PREFIX + "/markdown/finalize", response_model=FinalizeMarkdownResult)
def finalize_markdown(
    project_id: str,
    command: FinalizeMarkdownCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> FinalizeMarkdownResult:
    """P02：定稿裁定（确认态条目编辑须经用户确认清单后回流；不可定稿项阻断）。"""
    _check_project(project_id, command.project_ref)
    return service.finalize_markdown(command)


@router.post(_PREFIX + "/markdown/reopen-index", response_model=RequirementDocumentRead)
def reopen_index(
    project_id: str,
    command: ReopenIndexCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> RequirementDocumentRead:
    """P02→P01：调整索引编排（当前稿标记需重新生成）。"""
    _check_project(project_id, command.project_ref)
    return service.reopen_index(command)


@router.post(_PREFIX + "/items/{item_ref}/confirm", response_model=ItemConfirmResult)
def confirm_requirement_item(
    project_id: str,
    item_ref: str,
    command: ItemConfirmCommand,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> ItemConfirmResult:
    """需求条目最小确认门禁（SCN-003 完整评审链另行承接；仅供确认态资产进入候选池）。"""
    _check_project(project_id, command.project_ref)
    if command.item_ref != item_ref:
        raise HTTPException(status_code=400, detail="path item_ref 与 body.item_ref 不一致")
    return service.confirm_item(command)


@router.post(_PREFIX + "/exports", response_model=StartDocxExportResult)
def start_docx_export(
    project_id: str,
    command: StartDocxExportCommand,
    service: ExportExecutionService = Depends(get_export_execution_service),
) -> StartDocxExportResult:
    """P03：发起候选 docx 导出（仅可导出的定稿版本；转换经 AgentRun 承载）。"""
    _check_project(project_id, command.project_ref)
    return service.start_export(command)


@router.post(_PREFIX + "/exports/{export_ref}/check", response_model=DocxExportRead)
def report_export_check(
    project_id: str,
    export_ref: str,
    command: ExportCheckCommand,
    service: ExportExecutionService = Depends(get_export_execution_service),
) -> DocxExportRead:
    """P03：候选 docx 检查结论承接（不通过→打回，不形成基线）。"""
    _check_project(project_id, command.project_ref)
    if command.export_ref != export_ref:
        raise HTTPException(status_code=400, detail="path export_ref 与 body.export_ref 不一致")
    return service.report_check(command)


@router.post(_PREFIX + "/exports/manual-fallback", response_model=DocxExportRead)
def register_manual_fallback(
    project_id: str,
    command: ManualFallbackCommand,
    service: ExportExecutionService = Depends(get_export_execution_service),
) -> DocxExportRead:
    """P03：人工降级导出件登记（仅转换失败后；明确标记，不算系统转换成功）。"""
    _check_project(project_id, command.project_ref)
    return service.register_manual_fallback(command)


@router.post(_PREFIX + "/exports/{export_ref}/confirm-baseline", response_model=ConfirmBaselineResult)
def confirm_release_baseline(
    project_id: str,
    export_ref: str,
    command: ConfirmBaselineCommand,
    service: ExportExecutionService = Depends(get_export_execution_service),
) -> ConfirmBaselineResult:
    """P03：发布基线确认（导出成功≠发布；未经用户显式确认不得冻结基线）。"""
    _check_project(project_id, command.project_ref)
    if command.export_ref != export_ref:
        raise HTTPException(status_code=400, detail="path export_ref 与 body.export_ref 不一致")
    return service.confirm_baseline(command)


@router.get(_PREFIX + "/exports/{export_ref}/file")
def download_export_file(
    project_id: str,
    export_ref: str,
    service: DocumentOrchestrationService = Depends(get_publication_service),
):
    """P03：候选/基线 docx 下载（只读；不反向覆盖任何内部治理事实）。"""
    export = service._repo.get_export(export_ref)
    if export is None or not export.file_path or not Path(export.file_path).exists():
        raise NotFound("导出件文件不存在或尚未生成")
    return FileResponse(
        export.file_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="需求规格说明.docx",
    )


# GET+HEAD 拆为两个显式路由并各带唯一 operation_id：单一 api_route(methods=[GET,HEAD])
# 会让两个方法共用同一 operationId，openapi-typescript 生成重复标识符（tsc 报错）。
@router.get(_PREFIX + "/exports/{export_ref}/pdf", operation_id="preview_export_pdf")
@router.head(_PREFIX + "/exports/{export_ref}/pdf", operation_id="preview_export_pdf_head")
def preview_export_pdf(
    project_id: str,
    export_ref: str,
    service: DocumentOrchestrationService = Depends(get_publication_service),
):
    """精确预览：候选/基线 docx → PDF（LibreOffice 真实排版；inline 供浏览器原生查看器分页呈现）。

    结果按 {export.id}.pdf 缓存在 export_dir，docx 更新则重转；LibreOffice 缺失回 503（前端降级到内容预览）。
    """
    from app.adapters.docx_to_pdf import (
        PdfRenderError,
        PdfRenderUnavailable,
        convert_docx_to_pdf,
    )

    export = service._repo.get_export(export_ref)
    if export is None or not export.file_path or not Path(export.file_path).exists():
        raise NotFound("导出件文件不存在或尚未生成")
    docx_path = Path(export.file_path)
    pdf_path = docx_path.with_suffix(".pdf")
    # 缓存命中：PDF 已存在且不早于 docx（docx 快照不可变，一般一次转换长期有效）。
    if not (pdf_path.exists() and pdf_path.stat().st_mtime >= docx_path.stat().st_mtime):
        try:
            convert_docx_to_pdf(docx_path, docx_path.parent)
        except PdfRenderUnavailable:
            raise HTTPException(
                status_code=503,
                detail="服务端未安装 LibreOffice：精确预览暂不可用，请使用内容预览或下载查看",
            )
        except PdfRenderError:
            raise HTTPException(status_code=500, detail="docx 转 PDF 失败，请下载查看")
    return FileResponse(
        pdf_path, media_type="application/pdf",
        content_disposition_type="inline", filename="需求规格说明.pdf",
    )


@router.get(_PREFIX + "/baselines/{baseline_ref}", response_model=ReleaseBaselineRead)
def read_release_baseline(
    project_id: str,
    baseline_ref: str,
    service: DocumentOrchestrationService = Depends(get_publication_service),
) -> ReleaseBaselineRead:
    """P03-N10：发布基线只读复核（发现问题只能走新一轮 P01/P02/P03）。"""
    baseline = service._repo.get_baseline(baseline_ref)
    if baseline is None:
        raise NotFound("发布基线不存在")
    return service._baseline_read(baseline)
