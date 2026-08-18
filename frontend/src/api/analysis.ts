import { apiGet, apiPost } from './client';
import { sendDialogueStream as streamDialogueSse } from './dialogue-stream';
import type { components } from './generated/schema';

export type ElementRecognitionCommand = components['schemas']['ElementRecognitionCommand'];
export type RecognitionRequestResult = components['schemas']['RecognitionRequestResult'];
export type ElementWorkspaceRead = components['schemas']['ElementWorkspaceRead'];
export type RequirementElementRead = components['schemas']['RequirementElementRead'];
export type ElementFacetReviewRead = components['schemas']['ElementFacetReviewRead'];
export type ElementFacetFindingRead = components['schemas']['ElementFacetFindingRead'];
export type MaterialCanvasRead = components['schemas']['MaterialCanvasRead'];
export type MaterialParseContextRead = components['schemas']['MaterialParseContextRead'];
export type MaterialTextBlockRead = components['schemas']['MaterialTextBlockRead'];
export type MaterialSupplementRead = components['schemas']['MaterialSupplementRead'];
export type SourceAnchorRange = components['schemas']['SourceAnchorRange'];
export type ElementChangeDraftRead = components['schemas']['ElementChangeDraftRead'];
export type ElementOperationRequestResult = components['schemas']['ElementOperationRequestResult'];
export type ElementReviewCommand = components['schemas']['ElementReviewCommand'];
export type ElementAiExecutionCommand = components['schemas']['ElementAiExecutionCommand'];
export type ManualElementCorrectionCommand = components['schemas']['ManualElementCorrectionCommand'];
export type ElementChangeConfirmCommand = components['schemas']['ElementChangeConfirmCommand'];
export type ElementDecisionCommand = components['schemas']['ElementDecisionCommand'];
export type ElementDecisionPrecheckCommand = components['schemas']['ElementDecisionPrecheckCommand'];
export type ElementDecisionPrecheckRead = components['schemas']['ElementDecisionPrecheckRead'];
export type GuardedElementRead = components['schemas']['GuardedElementRead'];
export type ElementTriageCommand = components['schemas']['ElementTriageCommand'];
export type ElementRevisionCommand = components['schemas']['ElementRevisionCommand'];
export type RevisionFinalizeCommand = components['schemas']['RevisionFinalizeCommand'];
export type ElementEditCommand = components['schemas']['ElementEditCommand'];
export type ElementReopenCommand = components['schemas']['ElementReopenCommand'];
export type MaterialErratumCommand = components['schemas']['MaterialErratumCommand'];
export type MaterialSupplementCommand = components['schemas']['MaterialSupplementCommand'];
export type ElementDialogueCommand = components['schemas']['ElementDialogueCommand'];
export type ElementDialogueResult = components['schemas']['ElementDialogueResult'];
export type ElementHistoryRead = components['schemas']['ElementHistoryRead'];
export type ElementHistoryRecordRead = components['schemas']['ElementHistoryRecordRead'];
export type ActionFact = components['schemas']['ActionFact'];
export type ElementType = components['schemas']['ElementType'];
export type ElementProcessStatus = components['schemas']['ElementProcessStatus'];
export type ReviewConclusion = components['schemas']['ReviewConclusion'];

function elementsPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/elements`;
}

function contextPath(projectId: string, contextRef: string): string {
  return `${elementsPath(projectId)}/${encodeURIComponent(contextRef)}`;
}

export const analysisApi = {
  // P02：知识项识别（AEP-021）
  submitRecognition(
    projectId: string,
    command: ElementRecognitionCommand,
  ): Promise<RecognitionRequestResult> {
    return apiPost<RecognitionRequestResult>(`${elementsPath(projectId)}/recognition`, command);
  },

  // 未识别态区3 只读正文（LDM-002 快照；识别后由工作区读视图接管）
  getMaterialCanvas(projectId: string, materialRef: string): Promise<MaterialCanvasRead> {
    return apiGet<MaterialCanvasRead>(
      `${elementsPath(projectId).replace('/elements', '/materials')}/${encodeURIComponent(materialRef)}/canvas`,
    );
  },

  // 进页只读回放：这份材料最近一次识别上下文（没识别过则为空）
  getMaterialParseContext(projectId: string, materialRef: string): Promise<MaterialParseContextRead> {
    return apiGet<MaterialParseContextRead>(
      `${elementsPath(projectId).replace('/elements', '/materials')}/${encodeURIComponent(materialRef)}/parse-context`,
    );
  },

  // 工作区读取（五区唯一刷新权威）
  getWorkspace(projectId: string, contextRef: string): Promise<ElementWorkspaceRead> {
    return apiGet<ElementWorkspaceRead>(contextPath(projectId, contextRef));
  },

  // AEP-096 流式变体（链路回执条数据源）：改薄包装，SSE 实现体收敛至 api/dialogue-stream.ts（P0）
  // 注：非流式兄弟接口 sendDialogue 无调用点，P0 清理时删除（四页对话均走流式变体点亮回执条）
  sendDialogueStream(
    projectId: string,
    contextRef: string,
    command: ElementDialogueCommand,
    handlers: { onStage?: (stage: string) => void },
  ): Promise<ElementDialogueResult> {
    return streamDialogueSse<ElementDialogueResult>(
      `${contextPath(projectId, contextRef)}/dialogue`, command, handlers,
    );
  },

  // P03：直接裁定（确认→已确认 / 拒绝→已撤销，单条或批量）
  decideElements(
    projectId: string,
    contextRef: string,
    command: ElementDecisionCommand,
  ): Promise<ElementWorkspaceRead> {
    return apiPost<ElementWorkspaceRead>(`${contextPath(projectId, contextRef)}/decide`, command);
  },

  // 确认前预检：这批知识项里哪几条正被 AI 起草修订（只读，不迁移状态、不升工作区版本）
  precheckDecideElements(
    projectId: string,
    contextRef: string,
    command: ElementDecisionPrecheckCommand,
  ): Promise<ElementDecisionPrecheckRead> {
    return apiPost<ElementDecisionPrecheckRead>(
      `${contextPath(projectId, contextRef)}/decide/precheck`,
      command,
    );
  },

  // 建议剔除候选的人工处置（restore=撤回到正常列表 / return=移回候选区）；不改模型裁定、不迁状态
  triageElements(
    projectId: string,
    contextRef: string,
    command: ElementTriageCommand,
  ): Promise<ElementWorkspaceRead> {
    return apiPost<ElementWorkspaceRead>(`${contextPath(projectId, contextRef)}/triage`, command);
  },

  // P03：审核送检（核选中要素 → 分析中 / 扫原文补漏 → 新待确认要素）
  submitReview(
    projectId: string,
    contextRef: string,
    command: ElementReviewCommand,
  ): Promise<ElementOperationRequestResult> {
    return apiPost<ElementOperationRequestResult>(
      `${contextPath(projectId, contextRef)}/review`,
      command,
    );
  },

  // P03：修订迭代（对话轮次，不迁移状态；AI 辅助 / 人工直改修订稿）
  reviseElement(
    projectId: string,
    contextRef: string,
    command: ElementRevisionCommand,
  ): Promise<ElementOperationRequestResult> {
    return apiPost<ElementOperationRequestResult>(
      `${contextPath(projectId, contextRef)}/revision`,
      command,
    );
  },

  // P03：修订定夺（采纳→已确认 / 放弃→已撤销 / 转 AI 复核→分析中）
  finalizeRevision(
    projectId: string,
    contextRef: string,
    command: RevisionFinalizeCommand,
  ): Promise<ElementOperationRequestResult> {
    return apiPost<ElementOperationRequestResult>(
      `${contextPath(projectId, contextRef)}/revision-finalize`,
      command,
    );
  },

  // E3：就地修订（改类型/改范围/改表达 —— 版本+1，不迁状态）
  editElement(
    projectId: string,
    contextRef: string,
    command: ElementEditCommand,
  ): Promise<ElementWorkspaceRead> {
    return apiPost<ElementWorkspaceRead>(`${contextPath(projectId, contextRef)}/edit`, command);
  },

  // E3：勘误（原文出新来源版本，受影响要素回待确认）
  materialErratum(
    projectId: string,
    contextRef: string,
    command: MaterialErratumCommand,
  ): Promise<ElementWorkspaceRead> {
    return apiPost<ElementWorkspaceRead>(
      `${contextPath(projectId, contextRef)}/source/erratum`,
      command,
    );
  },

  // E3：补入（追加「补」来源块，相关要素回待确认）
  materialSupplement(
    projectId: string,
    contextRef: string,
    command: MaterialSupplementCommand,
  ): Promise<ElementWorkspaceRead> {
    return apiPost<ElementWorkspaceRead>(
      `${contextPath(projectId, contextRef)}/source/supplement`,
      command,
    );
  },

  // E4：重开（已撤销→待确认）/ 回流（已确认→待确认）
  reopenElement(
    projectId: string,
    contextRef: string,
    command: ElementReopenCommand,
  ): Promise<ElementWorkspaceRead> {
    return apiPost<ElementWorkspaceRead>(`${contextPath(projectId, contextRef)}/reopen`, command);
  },

  // E4：变更历史
  getElementHistory(
    projectId: string,
    contextRef: string,
    elementRef: string,
  ): Promise<ElementHistoryRead> {
    return apiGet<ElementHistoryRead>(
      `${contextPath(projectId, contextRef)}/history/${encodeURIComponent(elementRef)}`,
    );
  },

  // P04：AI 执行指定操作（AEP-025）
  submitAiExecution(
    projectId: string,
    contextRef: string,
    command: ElementAiExecutionCommand,
  ): Promise<ElementOperationRequestResult> {
    return apiPost<ElementOperationRequestResult>(
      `${contextPath(projectId, contextRef)}/operations/ai-execution`,
      command,
    );
  },

  // P04：人工校正（拆分/合并/新增，AEP-027）
  submitManualCorrection(
    projectId: string,
    contextRef: string,
    command: ManualElementCorrectionCommand,
  ): Promise<ElementChangeDraftRead> {
    return apiPost<ElementChangeDraftRead>(
      `${contextPath(projectId, contextRef)}/operations/manual-correction`,
      command,
    );
  },

  // P04：确认创建（AEP-029）
  confirmChangeDraft(
    projectId: string,
    contextRef: string,
    command: ElementChangeConfirmCommand,
  ): Promise<ElementWorkspaceRead> {
    return apiPost<ElementWorkspaceRead>(
      `${contextPath(projectId, contextRef)}/operations/confirm-change`,
      command,
    );
  },
};
