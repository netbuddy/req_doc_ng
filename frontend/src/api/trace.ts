import { apiGet, apiPost } from './client';
import type { IssueRead, TraceLinkRead } from './charts';

// ---- TRC-001 追溯分析服务（AEP-058…AEP-066）----

export type TraceNodeType = 'material' | 'element' | 'requirement_item' | 'chart' | 'document';

export type TraceDirection = 'upstream' | 'downstream';

export type TraceEdgeStatus =
  | 'derived'
  | 'pre_established'
  | 'effective'
  | 'suspect_pending_review'
  | 'invalid';

export type TraceGapKind =
  | 'item_no_source'
  | 'item_no_chart'
  | 'item_no_document'
  | 'chart_orphan'
  | 'element_orphan'
  | 'business_knowledge_unreferenced';

export interface TraceNodeRead {
  node_type: TraceNodeType;
  ref: string;
  label: string; // 材料节点=原文头优先（source_note 降为详情面板字段）
  sub_label?: string | null;
  status?: string | null;
  updated_at?: string | null;
  source_note?: string | null; // 仅材料节点：接入登记的来源说明（详情面板「来源说明」）
}

export interface TraceEdgeRead {
  edge_key: string;
  relation_kind: string; // material_element / element_item / chart_source / document_reference
  origin: 'ldm013' | 'derived';
  upstream_type: TraceNodeType;
  upstream_ref: string;
  downstream_type: TraceNodeType;
  downstream_ref: string;
  status: TraceEdgeStatus;
  link_ref?: string | null;
  status_reason?: string | null;
  // 仅 material_element 边：下游知识项来源锚点引文（LDM-005.source_anchor.ranges[].exact）。
  // anchor_quote=首条（卡片用），anchor_quotes=全部（详情面板列全）；缺失/解析失败=null/空。
  anchor_quote?: string | null;
  anchor_quotes?: string[];
}

export interface TraceLevelRead {
  distance: number;
  nodes: TraceNodeRead[];
  edges: TraceEdgeRead[];
  folded_count: number;
  folded_by_type: Record<string, number>;
}

export interface TraceChainRead {
  project_ref: string;
  direction: TraceDirection;
  focus: TraceNodeRead;
  depth: number;
  limit: number;
  include_invalid: boolean;
  levels: TraceLevelRead[];
}

export interface TraceAnchorGroupRead {
  node_type: TraceNodeType;
  nodes: TraceNodeRead[];
}

export interface TraceCountsRead {
  links_total: number;
  effective: number;
  pre_established: number;
  suspect: number;
  invalid: number;
  gaps: number;
  conflicts: number;
  conflicts_available: boolean;
}

export interface TraceEntryRead {
  project_ref: string;
  anchors: TraceAnchorGroupRead[];
  default_focus?: TraceNodeRead | null;
  counts: TraceCountsRead;
  next_action?: string | null;
}

export interface TraceCoverageDirectionRead {
  key: 'item_source' | 'item_chart' | 'item_document';
  covered: number;
  total: number;
  ratio: number;
}

export interface TraceCoverageRead {
  project_ref: string;
  directions: TraceCoverageDirectionRead[];
}

export interface TraceGapItemRead {
  kind: TraceGapKind;
  node_type: TraceNodeType;
  node_ref: string;
  label: string;
  detail: string;
  nav_target: 'requirement_workbench' | 'diagram_workbench' | 'publication_workbench';
}

export interface TraceGapListRead {
  project_ref: string;
  items: TraceGapItemRead[];
  total: number;
}

export interface TraceSuspectListRead {
  project_ref: string;
  items: TraceLinkRead[];
  total: number;
}

export interface TraceReviewCommand {
  conclusion: 'restore' | 'maintain';
  reason?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface TraceReviewResult {
  status: 'restored' | 'maintained';
  link: TraceLinkRead;
  next_action?: string | null;
}

export interface TraceIssueCommand {
  title: string;
  description?: string | null;
  issue_type?: string | null;
  trace_link_ref?: string | null;
  chart_ref?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface TraceWindowParams {
  depth: number;
  limit?: number;
  includeInvalid?: boolean;
}

function base(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/trace`;
}

function chainQuery(focusType: TraceNodeType, focusRef: string, params: TraceWindowParams): string {
  const q = new URLSearchParams({
    focus_type: focusType,
    focus_ref: focusRef,
    depth: String(params.depth),
  });
  if (params.limit) q.set('limit', String(params.limit));
  if (params.includeInvalid) q.set('include_invalid', 'true');
  return q.toString();
}

export const traceApi = {
  entry(projectId: string): Promise<TraceEntryRead> {
    return apiGet(`${base(projectId)}/entry`);
  },
  upstream(
    projectId: string,
    focusType: TraceNodeType,
    focusRef: string,
    params: TraceWindowParams,
  ): Promise<TraceChainRead> {
    return apiGet(`${base(projectId)}/upstream?${chainQuery(focusType, focusRef, params)}`);
  },
  downstream(
    projectId: string,
    focusType: TraceNodeType,
    focusRef: string,
    params: TraceWindowParams,
  ): Promise<TraceChainRead> {
    return apiGet(`${base(projectId)}/downstream?${chainQuery(focusType, focusRef, params)}`);
  },
  linkDetail(projectId: string, linkRef: string): Promise<TraceLinkRead> {
    return apiGet(`${base(projectId)}/links/${encodeURIComponent(linkRef)}`);
  },
  coverage(projectId: string): Promise<TraceCoverageRead> {
    return apiGet(`${base(projectId)}/coverage`);
  },
  gaps(projectId: string, kind?: TraceGapKind): Promise<TraceGapListRead> {
    const q = kind ? `?kind=${encodeURIComponent(kind)}` : '';
    return apiGet(`${base(projectId)}/gaps${q}`);
  },
  suspects(projectId: string, includeInvalid = false): Promise<TraceSuspectListRead> {
    return apiGet(`${base(projectId)}/suspects${includeInvalid ? '?include_invalid=true' : ''}`);
  },
  review(
    projectId: string,
    linkRef: string,
    command: TraceReviewCommand,
  ): Promise<TraceReviewResult> {
    return apiPost(`${base(projectId)}/links/${encodeURIComponent(linkRef)}/review`, command);
  },
  createIssue(projectId: string, command: TraceIssueCommand): Promise<IssueRead> {
    return apiPost(`${base(projectId)}/issues`, command);
  },
};
