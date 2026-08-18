"""分析转化路由（P02 识别 + P03 确认工作台 + P04 版本关系层）。

响应约定：2xx 裸 DTO；业务结局在 data.outcome/status；默认拒绝/不存在经异常处理器→409/404。
AEP-022/024/028（模型回交）是内部端点，不暴露 HTTP。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.schemas import (
    ElementAiExecutionCommand,
    ElementChangeConfirmCommand,
    ElementChangeDraftRead,
    ElementDecisionCommand,
    ElementDecisionPrecheckCommand,
    ElementDecisionPrecheckRead,
    ElementDialogueCommand,
    ElementDialogueResult,
    ElementEditCommand,
    ElementHistoryRead,
    ElementOperationRequestResult,
    ElementRecognitionCommand,
    ElementReopenCommand,
    ElementReviewCommand,
    ElementRevisionCommand,
    ElementTriageCommand,
    ElementWorkspaceRead,
    ManualElementCorrectionCommand,
    MaterialCanvasRead,
    MaterialParseContextRead,
    MaterialErratumCommand,
    MaterialSupplementCommand,
    RecognitionRequestResult,
    RevisionFinalizeCommand,
)
from app.api import transcript as tx
from app.api.sse_dialogue import stream_dialogue, wants_event_stream
from app.deps import get_analysis_service
from app.services.analysis_transformation import AnalysisTransformationService

router = APIRouter(tags=["analysis"])


def _require_ctx_match(path_ctx: str, body_ctx: str) -> None:
    if path_ctx != body_ctx:
        raise HTTPException(status_code=400, detail="path parse_context_ref 与 body 不一致")


@router.post("/projects/{project_id}/elements/recognition", response_model=RecognitionRequestResult)
def submit_recognition(
    project_id: str,
    command: ElementRecognitionCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> RecognitionRequestResult:
    if command.project_ref != project_id:
        raise HTTPException(status_code=400, detail="path project_id 与 body.project_ref 不一致")
    return service.submit_element_recognition(command)


@router.get("/projects/{project_id}/materials/{material_ref}/canvas", response_model=MaterialCanvasRead)
def read_material_canvas(
    project_id: str,
    material_ref: str,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> MaterialCanvasRead:
    # 未识别态区3 只读正文（LDM-002 快照）；识别后由工作区读视图接管并叠加高亮。
    return service.read_material_canvas(material_ref)


@router.get(
    "/projects/{project_id}/materials/{material_ref}/parse-context",
    response_model=MaterialParseContextRead,
)
def read_material_parse_context(
    project_id: str,
    material_ref: str,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> MaterialParseContextRead:
    # 进页只读回放：告诉前端这份材料最近一次识别上下文（没识别过则为空），
    # 前端据此读回既有工作区，而不是把已识别的材料当成未识别。
    return service.read_material_parse_context(material_ref)


@router.get("/projects/{project_id}/elements/{parse_context_ref}", response_model=ElementWorkspaceRead)
def read_element_workspace(
    project_id: str,
    parse_context_ref: str,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    return service.read_element_workspace(parse_context_ref)


# ---- P03：确认工作台 ----

@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/dialogue",
    response_model=ElementDialogueResult,
)
def element_dialogue(
    project_id: str,
    parse_context_ref: str,
    command: ElementDialogueCommand,
    request: Request,
    service: AnalysisTransformationService = Depends(get_analysis_service),
):
    """AEP-096 区5 对话命令解释：命令词确定性解析 + LLM 正文解释 + 校验派发既有端点逻辑。

    `Accept: text/event-stream` 时流式推送 stage 帧 + result 终帧（链路回执条数据源）；
    流式分支自建 session（服务在独立线程执行），不复用请求级依赖注入的 session。
    """
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    # 演示留痕：受理即写 user 行（独立短 session，业务失败不回滚）
    tx.record_user_message(
        channel=tx.CHANNEL_ANALYSIS, project_ref=project_id,
        context_ref=command.parse_context_ref, message=command.message,
    )
    if not wants_event_stream(request):
        result = service.element_dialogue(command)
        tx.record_analysis_assistant(project_id, command.parse_context_ref, result)
        return result

    def run(on_stage):
        from app.deps import _build_async_analysis_service, new_session

        session = new_session()
        try:
            result = _build_async_analysis_service(session).element_dialogue(command, on_stage=on_stage)
            session.commit()
            # 助手终态行：服务事务提交后，独立短 session 写（不掺入服务事务）
            tx.record_analysis_assistant(project_id, command.parse_context_ref, result)
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return stream_dialogue(run)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/decide",
    response_model=ElementWorkspaceRead,
)
def decide_elements(
    project_id: str,
    parse_context_ref: str,
    command: ElementDecisionCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    """直接裁定：确认→已确认 / 拒绝→已撤销（单条或批量；含分析中越过复核）。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.decide_elements(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/decide/precheck",
    response_model=ElementDecisionPrecheckRead,
)
def precheck_decide_elements(
    project_id: str,
    parse_context_ref: str,
    command: ElementDecisionPrecheckCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementDecisionPrecheckRead:
    """确认前预检：这批知识项里哪几条正被 AI 起草修订（只读，不迁移状态、不升版本）。

    确认它们会把在起草的修订稿搁置成孤儿稿，故前端据此弹二次确认。守卫是软拦截，
    本端点只报事实、不做裁决——用户坚持时照常走 decide。
    """
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.precheck_decide_elements(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/triage",
    response_model=ElementWorkspaceRead,
)
def triage_elements(
    project_id: str,
    parse_context_ref: str,
    command: ElementTriageCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    """建议剔除候选的人工处置：restore=撤回到正常列表 / return=移回候选区。

    只写人工标记，不改模型裁定，也不迁移确认生命周期（撤回≠确认）。
    """
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.triage_elements(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/review",
    response_model=ElementOperationRequestResult,
)
def submit_review(
    project_id: str,
    parse_context_ref: str,
    command: ElementReviewCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementOperationRequestResult:
    """审核送检：核选中要素（→分析中）或扫原文补漏（产物为新「待确认」要素）。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.submit_element_review(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/revision",
    response_model=ElementOperationRequestResult,
)
def revise_element(
    project_id: str,
    parse_context_ref: str,
    command: ElementRevisionCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementOperationRequestResult:
    """修订迭代（对话轮次，不迁移状态）：AI 辅助或人工直改修订稿。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.revise_element(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/revision-finalize",
    response_model=ElementOperationRequestResult,
)
def finalize_revision(
    project_id: str,
    parse_context_ref: str,
    command: RevisionFinalizeCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementOperationRequestResult:
    """修订稿定夺：adopt=采纳即确认（超出原文须先补入）；abandon=清除草稿（状态不变）。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.finalize_revision(command)


# ---- E3：就地修订 + 改源联动 ----

@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/edit",
    response_model=ElementWorkspaceRead,
)
def edit_element(
    project_id: str,
    parse_context_ref: str,
    command: ElementEditCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    """就地修订：改类型/改范围/改表达（版本+1、保留改前版本、不迁状态）。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.edit_element(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/source/erratum",
    response_model=ElementWorkspaceRead,
)
def material_erratum(
    project_id: str,
    parse_context_ref: str,
    command: MaterialErratumCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    """勘误：原文出新来源版本（旧快照保留），受影响要素回「待确认」。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.material_erratum(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/source/supplement",
    response_model=ElementWorkspaceRead,
)
def material_supplement(
    project_id: str,
    parse_context_ref: str,
    command: MaterialSupplementCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    """补入：追加「补」来源块（留痕补入人/依据，原快照不动），相关要素回「待确认」。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.material_supplement(command)


# ---- E4：重开/回流 + 变更历史 ----

@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/reopen",
    response_model=ElementWorkspaceRead,
)
def reopen_element(
    project_id: str,
    parse_context_ref: str,
    command: ElementReopenCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    """重开（已撤销→待确认）/ 回流（已确认→待确认），产生新版本、旧结论留历史。"""
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.reopen_element(command)


@router.get(
    "/projects/{project_id}/elements/{parse_context_ref}/history/{element_ref}",
    response_model=ElementHistoryRead,
)
def read_element_history(
    project_id: str,
    parse_context_ref: str,
    element_ref: str,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementHistoryRead:
    """要素变更历史（谁/何时/改了什么；版本与状态迁移留痕）。"""
    return service.read_element_history(parse_context_ref, element_ref)


# ---- P04：AI 执行 / 人工校正（拆分/合并/新增） / 确认创建 ----

@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/operations/ai-execution",
    response_model=ElementOperationRequestResult,
)
def submit_ai_execution(
    project_id: str,
    parse_context_ref: str,
    command: ElementAiExecutionCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementOperationRequestResult:
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.submit_element_ai_execution(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/operations/manual-correction",
    response_model=ElementChangeDraftRead,
)
def submit_manual_correction(
    project_id: str,
    parse_context_ref: str,
    command: ManualElementCorrectionCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementChangeDraftRead:
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.submit_manual_element_correction(command)


@router.post(
    "/projects/{project_id}/elements/{parse_context_ref}/operations/confirm-change",
    response_model=ElementWorkspaceRead,
)
def confirm_change(
    project_id: str,
    parse_context_ref: str,
    command: ElementChangeConfirmCommand,
    service: AnalysisTransformationService = Depends(get_analysis_service),
) -> ElementWorkspaceRead:
    _require_ctx_match(parse_context_ref, command.parse_context_ref)
    return service.confirm_element_change_draft(command)
