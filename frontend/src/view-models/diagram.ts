import type {
  ChartFindingType,
  ChartFormat,
  ChartRead,
  ChartStatus,
  ChartType,
  ChartWorkspaceRead,
  TraceLinkRead,
  TraceLinkStatus,
} from '../api/charts';
import type { BadgeTone } from './common';

// ---- 稳定码 → 展示标签（中文是展示层，稳定码是跨线契约）----

export const chartStatusMeta: Record<ChartStatus, { label: string; tone: BadgeTone }> = {
  draft: { label: '草稿中', tone: 'processing' },
  pending_confirmation: { label: '待确认', tone: 'warning' },
  confirmed: { label: '已确认', tone: 'success' },
  returned_for_revision: { label: '退回修订', tone: 'danger' },
  voided: { label: '已作废', tone: 'neutral' },
};

export const chartTypeLabels: Record<ChartType, string> = {
  flowchart: '流程图',
  state_diagram: '状态图',
  relation_diagram: '关系图',
  sequence_diagram: '时序图',
  decision_table: '决策表',
  comparison_table: '对照表',
};

export const chartFormatLabels: Record<ChartFormat, string> = {
  mermaid: 'Mermaid',
  plantuml: 'PlantUML',
  markdown_table: 'Markdown 表格',
};

export const traceStatusMeta: Record<TraceLinkStatus, { label: string; tone: BadgeTone }> = {
  pre_established: { label: '预建立', tone: 'processing' },
  effective: { label: '有效', tone: 'success' },
  suspect_pending_review: { label: '可疑待复核', tone: 'warning' },
  invalid: { label: '失效', tone: 'neutral' },
};

export const findingTypeMeta: Record<ChartFindingType, { label: string; tone: BadgeTone }> = {
  suspected_hidden_requirement: { label: '疑似隐藏需求', tone: 'danger' },
  chart_text_conflict: { label: '图文冲突', tone: 'danger' },
  source_coverage_gap: { label: '来源覆盖缺口', tone: 'warning' },
  trace_gap: { label: '追溯缺口', tone: 'warning' },
  no_obvious_issue: { label: '无明显问题', tone: 'success' },
  undeterminable: { label: '无法判断', tone: 'neutral' },
};

export const suggestionStatusLabels: Record<string, string> = {
  pending: '待处理',
  adopted: '已采纳',
  revised_adopted: '已修订采纳',
  rejected: '已拒绝',
  transferred_to_issue: '已转问题项',
};

// 图表类型 → 表达方式候选（受控矩阵的展示层投影；后端 chart_rules 是裁定源）
export const typeFormatOptions: Record<ChartType, ChartFormat[]> = {
  flowchart: ['mermaid', 'plantuml'],
  state_diagram: ['mermaid', 'plantuml'],
  relation_diagram: ['mermaid', 'plantuml'],
  sequence_diagram: ['mermaid', 'plantuml'],
  decision_table: ['markdown_table'],
  comparison_table: ['markdown_table'],
};

// ---- 列表行 VM ----

export interface ChartListRowVM {
  chartRef: string;
  title: string;
  typeLabel: string;
  formatLabel: string;
  statusLabel: string;
  statusTone: BadgeTone;
  draftVersion: number;
  sourceCount: number;
  updatedAt: string;
}

export function buildChartRows(charts: ChartRead[]): ChartListRowVM[] {
  return charts.map((c) => ({
    chartRef: c.chart_ref,
    title: c.title,
    typeLabel: chartTypeLabels[c.chart_type] ?? c.chart_type,
    formatLabel: chartFormatLabels[c.format] ?? c.format,
    statusLabel: chartStatusMeta[c.status]?.label ?? c.status,
    statusTone: chartStatusMeta[c.status]?.tone ?? 'neutral',
    draftVersion: c.draft_version,
    sourceCount: c.source_count,
    updatedAt: c.updated_at,
  }));
}

// ---- 工作区 VM（只读投影后端事实：门禁/动作不在前端复算）----

export interface ChartWorkspaceVM {
  chartRef: string;
  title: string;
  statusLabel: string;
  statusTone: BadgeTone;
  statusReason: string | null;
  typeLabel: string;
  formatLabel: string;
  isDraft: boolean;
  isPending: boolean;
  isReturned: boolean;
  isTerminal: boolean;
  previewable: boolean;
  canSubmitConfirmation: boolean;
  blockedReasons: string[];
  actionEnabled: Record<string, boolean>;
  actionDisabledReason: Record<string, string | null>;
}

export function buildChartWorkspaceVM(ws: ChartWorkspaceRead): ChartWorkspaceVM {
  const actionEnabled: Record<string, boolean> = {};
  const actionDisabledReason: Record<string, string | null> = {};
  for (const action of ws.available_actions) {
    actionEnabled[action.key] = action.enabled;
    actionDisabledReason[action.key] = action.disabled_reason ?? null;
  }
  return {
    chartRef: ws.chart_ref,
    title: ws.title,
    statusLabel: chartStatusMeta[ws.status]?.label ?? ws.status,
    statusTone: chartStatusMeta[ws.status]?.tone ?? 'neutral',
    statusReason: ws.status_reason ?? null,
    typeLabel: chartTypeLabels[ws.chart_type] ?? ws.chart_type,
    formatLabel: chartFormatLabels[ws.format] ?? ws.format,
    isDraft: ws.status === 'draft',
    isPending: ws.status === 'pending_confirmation',
    isReturned: ws.status === 'returned_for_revision',
    isTerminal: ws.status === 'confirmed' || ws.status === 'voided',
    previewable: ws.preview_capability === 'renderable',
    canSubmitConfirmation: ws.confirmation_gate?.can_submit ?? false,
    blockedReasons: ws.confirmation_gate?.blocked_reasons ?? [],
    actionEnabled,
    actionDisabledReason,
  };
}

// ---- 追溯行 VM ----

export interface TraceLinkRowVM {
  linkRef: string;
  upstreamLabel: string;
  downstreamLabel: string;
  statusLabel: string;
  statusTone: BadgeTone;
  statusReason: string | null;
  establishedAt: string | null;
  issueRef: string | null;
}

export function buildTraceLinkRows(links: TraceLinkRead[]): TraceLinkRowVM[] {
  return links.map((l) => ({
    linkRef: l.link_ref,
    upstreamLabel: l.upstream_label ?? l.upstream_ref,
    downstreamLabel: l.downstream_label ?? l.downstream_ref,
    statusLabel: traceStatusMeta[l.status]?.label ?? l.status,
    statusTone: traceStatusMeta[l.status]?.tone ?? 'neutral',
    statusReason: l.status_reason ?? null,
    establishedAt: l.established_at ?? null,
    issueRef: l.issue_ref ?? null,
  }));
}

// ---- Markdown 表格预览（受控表格 → 单元格矩阵；不引入 HTML 注入面）----

export function parseMarkdownTable(source: string): string[][] | null {
  const lines = source
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  if (lines.length < 3) return null;
  const toCells = (line: string) =>
    line
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim());
  const header = toCells(lines[0]);
  const rows = lines.slice(2).map(toCells);
  return [header, ...rows];
}
