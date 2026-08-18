import { apiGet, apiPost } from './client';
import type { ActionFact } from './analysis';

// ---- SCN-004 图表协同服务 / 追溯图谱模块 / 问题项模块 ----

export type ChartStatus =
  | 'draft'
  | 'pending_confirmation'
  | 'confirmed'
  | 'returned_for_revision'
  | 'voided';

export type ChartType =
  | 'flowchart'
  | 'state_diagram'
  | 'relation_diagram'
  | 'sequence_diagram'
  | 'decision_table'
  | 'comparison_table';

export type ChartFormat = 'mermaid' | 'plantuml' | 'markdown_table';

export type TraceLinkStatus =
  | 'pre_established'
  | 'effective'
  | 'suspect_pending_review'
  | 'invalid';

export type ChartFindingType =
  | 'suspected_hidden_requirement'
  | 'chart_text_conflict'
  | 'source_coverage_gap'
  | 'trace_gap'
  | 'no_obvious_issue'
  | 'undeterminable';

export type ChartFindingDecision = 'accepted' | 'rejected';

export type ChartSuggestionHandling = 'adopt' | 'revise_and_adopt' | 'reject';

export type IssueStatus = 'pending' | 'processing' | 'blocked' | 'closed';

export interface ChartEligibleSourceRead {
  item_ref: string;
  req_no: string;
  expression: string;
  req_type: string;
  status: string;
  // 完备性内容（「来源」页签逐条核对用；确认态条目可能缺填）
  curation_note?: string | null;
  boundary_note?: string | null;
  verification_method?: string | null;
  verification_note?: string | null;
  priority?: string | null;
}

// P4 06 B.1：图表候选业务知识来源（SUPPORTING_CONTENT 段；业务翼确认态要素投影）
export interface ChartBusinessSourceRead {
  element_ref: string;
  element_type: string;
  content: string;
  knowledge_category: string; // 恒 business
}

export interface ChartEligibleSourceListRead {
  project_ref: string;
  sources: ChartEligibleSourceRead[];
  business_sources?: ChartBusinessSourceRead[];
  next_action?: string | null;
}

export interface TraceLinkRead {
  link_ref: string;
  relation_type: string;
  upstream_type: string;
  upstream_ref: string;
  upstream_label?: string | null;
  downstream_type: string;
  downstream_ref: string;
  downstream_label?: string | null;
  status: TraceLinkStatus;
  initial_basis: string;
  status_reason?: string | null;
  established_basis?: string | null;
  established_at?: string | null;
  issue_ref?: string | null;
}

export interface TraceLinkListRead {
  project_ref: string;
  links: TraceLinkRead[];
}

export interface ChartSuggestionRead {
  suggestion_ref: string;
  source_code: string;
  explanation: string;
  process_status: string; // pending / adopted / revised_adopted / rejected
  created_for_version?: number | null;
}

export type ChartSuggestionThreadStatus = 'generating' | 'suggested' | 'stopped';

/** AI 建议请求全生命周期（区4 对话时间线；停靠原因随读视图返回，不静默）。 */
export interface ChartSuggestionThreadEntryRead {
  context_ref: string;
  intent: string;
  created_at: string;
  kind: 'initial' | 'revision'; // initial=创建初稿（结果自动应用）/ revision=修订建议
  status: ChartSuggestionThreadStatus;
  stop_reason?: string | null;
  suggestion?: ChartSuggestionRead | null;
}

export interface ChartFindingRead {
  finding_ref: string;
  finding_type: ChartFindingType;
  summary: string;
  basis_summary: string;
  related_source_refs: string[];
  decision?: ChartFindingDecision | null;
  decision_reason?: string | null;
  decision_operator?: string | null;
  decided_at?: string | null;
  issue_ref?: string | null;
  is_blocking: boolean;
}

export interface ChartVerificationRead {
  round_ref: string;
  round_no: number;
  chart_draft_version: number;
  processing_status: 'verifying' | 'completed' | 'failed';
  reason?: string | null;
  invalidated: boolean;
  findings: ChartFindingRead[];
}

export interface ChartRevisionRead {
  revision_ref: string;
  draft_version: number;
  change_origin: string;
  note?: string | null;
  operator_ref: string;
  created_at: string;
}

export interface ChartRead {
  chart_ref: string;
  title: string;
  chart_kind: string;
  chart_type: ChartType;
  format: ChartFormat;
  status: ChartStatus;
  draft_version: number;
  source_count: number;
  updated_at: string;
}

export interface ChartListRead {
  project_ref: string;
  charts: ChartRead[];
  next_action?: string | null;
}

export interface ConfirmationGateRead {
  can_submit: boolean;
  blocked_reasons: string[];
  review_summary_ref?: string | null;
}

export interface ChartWorkspaceRead {
  chart_ref: string;
  project_ref: string;
  title: string;
  chart_kind: string;
  chart_type: ChartType;
  format: ChartFormat;
  source_code: string;
  draft_version: number;
  status: ChartStatus;
  status_reason?: string | null;
  preview_capability: 'renderable' | 'not_previewable';
  creation_basis: string;
  verification_conclusion?: string | null;
  confirm_basis?: string | null;
  sources: ChartEligibleSourceRead[];
  trace_links: TraceLinkRead[];
  suggestions: ChartSuggestionRead[];
  suggestion_thread: ChartSuggestionThreadEntryRead[];
  verification?: ChartVerificationRead | null;
  revisions: ChartRevisionRead[];
  confirmation_gate?: ConfirmationGateRead | null;
  available_actions: ActionFact[];
  validation_errors: string[];
  next_action?: string | null;
}

export interface ChartCreateCommand {
  project_ref: string;
  title?: string; // 可空：初稿生成结果以语义标题回填
  chart_type: ChartType;
  format: ChartFormat;
  source_kind?: string;
  source_refs: string[];
  generate_initial?: boolean; // 创建后立即基于来源条目生成初稿（向导默认路径）
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartCreateResult {
  status: 'created' | 'rejected_precheck';
  chart_ref?: string | null;
  initial_suggestion_context_ref?: string | null;
  next_action?: string | null;
}

export interface ChartSourceChangeCommand {
  project_ref: string;
  source_code: string;
  format: ChartFormat;
  chart_type: ChartType;
  source_refs: string[];
  expected_draft_version: number;
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartSuggestionCommand {
  project_ref: string;
  intent?: string;
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartSuggestionRequestResult {
  status: 'submitted' | 'rejected_precheck';
  suggestion_context_ref?: string | null;
  agent_run_ref?: string | null;
  next_action?: string | null;
}

export interface ChartSuggestionHandlingCommand {
  project_ref: string;
  handling: ChartSuggestionHandling;
  revised_source?: string | null;
  reason?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartVerificationCommand {
  project_ref: string;
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartVerificationRequestResult {
  status: 'submitted' | 'rejected_precheck';
  request_ref?: string | null;
  agent_run_ref?: string | null;
  next_action?: string | null;
}

export interface ChartFindingDecisionCommand {
  project_ref: string;
  decision: ChartFindingDecision;
  reason?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartConfirmationCommand {
  project_ref: string;
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartConfirmationResult {
  status: 'confirmed' | 'rejected_precheck';
  chart_ref: string;
  chart_status: ChartStatus;
  trace_established_count: number;
  next_action?: string | null;
}

export interface ChartLifecycleCommand {
  project_ref: string;
  reason?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface ChartIssueCommand {
  project_ref: string;
  issue_type?: string | null;
  title?: string | null;
  description?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface IssueRead {
  issue_ref: string;
  issue_type: string;
  status: IssueStatus;
  title: string;
  description: string;
  origin_kind: string;
  chart_ref?: string | null;
  finding_ref?: string | null;
  trace_link_refs: string[];
  created_by: string;
  created_at: string;
}

export interface IssueListRead {
  project_ref: string;
  issues: IssueRead[];
  next_action?: string | null;
}

function base(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export const chartsApi = {
  list(projectId: string): Promise<ChartListRead> {
    return apiGet(`${base(projectId)}/charts`);
  },
  eligibleSources(projectId: string): Promise<ChartEligibleSourceListRead> {
    return apiGet(`${base(projectId)}/charts/eligible-sources`);
  },
  create(projectId: string, command: ChartCreateCommand): Promise<ChartCreateResult> {
    return apiPost(`${base(projectId)}/charts`, command);
  },
  read(projectId: string, chartRef: string): Promise<ChartWorkspaceRead> {
    return apiGet(`${base(projectId)}/charts/${encodeURIComponent(chartRef)}`);
  },
  applySource(
    projectId: string,
    chartRef: string,
    command: ChartSourceChangeCommand,
  ): Promise<ChartWorkspaceRead> {
    return apiPost(`${base(projectId)}/charts/${encodeURIComponent(chartRef)}/source`, command);
  },
  requestSuggestion(
    projectId: string,
    chartRef: string,
    command: ChartSuggestionCommand,
  ): Promise<ChartSuggestionRequestResult> {
    return apiPost(`${base(projectId)}/charts/${encodeURIComponent(chartRef)}/suggestions`, command);
  },
  handleSuggestion(
    projectId: string,
    chartRef: string,
    suggestionRef: string,
    command: ChartSuggestionHandlingCommand,
  ): Promise<ChartWorkspaceRead> {
    return apiPost(
      `${base(projectId)}/charts/${encodeURIComponent(chartRef)}/suggestions/${encodeURIComponent(suggestionRef)}/handle`,
      command,
    );
  },
  startVerification(
    projectId: string,
    chartRef: string,
    command: ChartVerificationCommand,
  ): Promise<ChartVerificationRequestResult> {
    return apiPost(`${base(projectId)}/charts/${encodeURIComponent(chartRef)}/verification`, command);
  },
  submitFindingDecision(
    projectId: string,
    chartRef: string,
    findingRef: string,
    command: ChartFindingDecisionCommand,
  ): Promise<ChartWorkspaceRead> {
    return apiPost(
      `${base(projectId)}/charts/${encodeURIComponent(chartRef)}/findings/${encodeURIComponent(findingRef)}/decision`,
      command,
    );
  },
  confirm(
    projectId: string,
    chartRef: string,
    command: ChartConfirmationCommand,
  ): Promise<ChartConfirmationResult> {
    return apiPost(`${base(projectId)}/charts/${encodeURIComponent(chartRef)}/confirm`, command);
  },
  returnForRevision(
    projectId: string,
    chartRef: string,
    command: ChartLifecycleCommand,
  ): Promise<ChartWorkspaceRead> {
    return apiPost(
      `${base(projectId)}/charts/${encodeURIComponent(chartRef)}/return-for-revision`,
      command,
    );
  },
  voidChart(
    projectId: string,
    chartRef: string,
    command: ChartLifecycleCommand,
  ): Promise<ChartWorkspaceRead> {
    return apiPost(`${base(projectId)}/charts/${encodeURIComponent(chartRef)}/void`, command);
  },
  resumeEditing(
    projectId: string,
    chartRef: string,
    command: ChartLifecycleCommand,
  ): Promise<ChartWorkspaceRead> {
    return apiPost(`${base(projectId)}/charts/${encodeURIComponent(chartRef)}/resume-editing`, command);
  },
  createIssue(
    projectId: string,
    chartRef: string,
    findingRef: string,
    command: ChartIssueCommand,
  ): Promise<IssueRead> {
    return apiPost(
      `${base(projectId)}/charts/${encodeURIComponent(chartRef)}/findings/${encodeURIComponent(findingRef)}/issue`,
      command,
    );
  },
  listIssues(projectId: string): Promise<IssueListRead> {
    return apiGet(`${base(projectId)}/issues`);
  },
  listTraceLinks(
    projectId: string,
    filters?: { status?: string; chart_ref?: string },
  ): Promise<TraceLinkListRead> {
    const params = new URLSearchParams();
    if (filters?.status) params.set('status', filters.status);
    if (filters?.chart_ref) params.set('chart_ref', filters.chart_ref);
    const query = params.toString();
    return apiGet(`${base(projectId)}/trace-links${query ? `?${query}` : ''}`);
  },
};
