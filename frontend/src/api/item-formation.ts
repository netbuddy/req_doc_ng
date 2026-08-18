import { apiGet, apiPost } from './client';
import { sendDialogueStream as streamDialogueSse } from './dialogue-stream';
import type { components } from './generated/schema';

// SCN-002-P01 条目形成（AEP-038 + 工作区读视图）—— 类型一律来自 OpenAPI 生成契约。
// 后端 default_factory 列表字段在生成契约中是可选的；后端序列化时恒有值，这里收窄为必填。
export type ItemizationBatchCommand = components['schemas']['ItemizationBatchCommand'];
export type ItemizationBatchRequestResult = components['schemas']['ItemizationBatchRequestResult'];
export type ItemizationResultRead = components['schemas']['ItemizationResultRead'];

type GeneratedPendingItem = components['schemas']['PendingRequirementItemRead'];
export type PendingRequirementItemRead = GeneratedPendingItem &
  Required<Pick<GeneratedPendingItem, 'source_element_refs' | 'revision_records'>>;

type GeneratedWorkspace = components['schemas']['ItemFormationWorkspaceRead'];
export type ItemFormationWorkspaceRead = Omit<GeneratedWorkspace, 'pending_items'> &
  Required<
    Pick<
      GeneratedWorkspace,
      | 'eligible_elements'
      | 'blocked_elements'
      | 'intent_context'
      | 'batch_results'
      | 'revision_suggestions'
      | 'available_actions'
      | 'available_operations'
    >
  > & { pending_items: PendingRequirementItemRead[] };
export type ItemStructureReviewRead = components['schemas']['ItemStructureReviewRead'];
export type BlockedElementRead = components['schemas']['BlockedElementRead'];
export type ItemRevisionRecordRead = components['schemas']['ItemRevisionRecordRead'];
export type ItemRevisionSuggestionRead = components['schemas']['ItemRevisionSuggestionRead'];
export type RequirementItemType = components['schemas']['RequirementItemType'];
export type RequirementItemStatus = components['schemas']['RequirementItemStatus'];
export type ItemizationScopeType = components['schemas']['ItemizationScopeType'];
export type FormationDialogueCommand = components['schemas']['FormationDialogueCommand'];
export type StructureRecheckCommand = components['schemas']['StructureRecheckCommand'];
export type StructureRecheckRequestResult = components['schemas']['StructureRecheckRequestResult'];
type GeneratedRecheckOutcome = components['schemas']['StructureRecheckOutcomeRead'];
export type StructureRecheckOutcomeRead = GeneratedRecheckOutcome &
  Required<
    Pick<
      GeneratedRecheckOutcome,
      | 'target_item_refs'
      | 'refreshed_refs'
      | 'expired_skipped_refs'
      | 'failed_refs'
      | 'skipped_refs'
      | 'pending_refs'
    >
  >;

type GeneratedDialogueResult = components['schemas']['FormationDialogueResult'];
export type FormationDialogueResult = Omit<GeneratedDialogueResult, 'workspace'> & {
  workspace?: ItemFormationWorkspaceRead | null;
};

// 区1 投影用联合：可形成要素（RequirementElementRead）与停靠/支撑要素（BlockedElementRead）
export type FormationElementRead = components['schemas']['RequirementElementRead'] &
  Partial<Pick<BlockedElementRead, 'formation_role' | 'blocked_reason'>>;

function itemFormationPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/item-formation`;
}

export const itemFormationApi = {
  // AEP-038：发起条目化批次（受理立即返回；批次经 AgentRun 追踪）
  startBatch(projectId: string, command: ItemizationBatchCommand): Promise<ItemizationBatchRequestResult> {
    return apiPost<ItemizationBatchRequestResult>(`${itemFormationPath(projectId)}/batches`, command);
  },

  // AEP-114：结构复核批次受理（只判不改；item_refs 空=默认目标集；经 AgentRun 追踪）
  startStructureRecheck(
    projectId: string,
    command: StructureRecheckCommand,
  ): Promise<StructureRecheckRequestResult> {
    return apiPost<StructureRecheckRequestResult>(
      `${itemFormationPath(projectId)}/structure-rechecks`,
      command,
    );
  },

  // AEP-114 读侧：复核批次逐条目结局（终态后取一次；回执两集合口径的事实源）
  getStructureRecheckOutcome(
    projectId: string,
    recheckContextRef: string,
  ): Promise<StructureRecheckOutcomeRead> {
    return apiGet<StructureRecheckOutcomeRead>(
      `${itemFormationPath(projectId)}/structure-rechecks/${encodeURIComponent(recheckContextRef)}`,
    ) as Promise<StructureRecheckOutcomeRead>;
  },

  // 条目形成工作区读视图（批次/字段修订后由此刷新五区）
  getWorkspace(projectId: string, formationContextRef: string): Promise<ItemFormationWorkspaceRead> {
    return apiGet<ItemFormationWorkspaceRead>(
      `${itemFormationPath(projectId)}/${encodeURIComponent(formationContextRef)}`,
    );
  },

  // 回放该解析结果最近一次批次的形成工作区（无批次时 404；进入形成页找回既有待确认条目）
  getWorkspaceByParseResult(projectId: string, parseResultRef: string): Promise<ItemFormationWorkspaceRead> {
    return apiGet<ItemFormationWorkspaceRead>(
      `${itemFormationPath(projectId)}/by-parse-result/${encodeURIComponent(parseResultRef)}`,
    );
  },

  // AEP-097 区5 对话（整段原文 + 上下文引用；前端不解析命令词）。
  // 流式变体（链路回执条数据源）：改薄包装，SSE 实现体收敛至 api/dialogue-stream.ts（P0）。
  sendDialogueStream(
    projectId: string,
    command: FormationDialogueCommand,
    handlers: { onStage?: (stage: string) => void },
  ): Promise<FormationDialogueResult> {
    return streamDialogueSse<FormationDialogueResult>(
      `${itemFormationPath(projectId)}/dialogue`, command, handlers,
    );
  },
};
