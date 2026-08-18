import type {
  TraceChainRead,
  TraceDirection,
  TraceEdgeRead,
  TraceEdgeStatus,
  TraceGapKind,
  TraceNodeRead,
  TraceNodeType,
} from '../api/trace';
import type { AssetFragmentRead } from '../api/publication';
import type { BadgeTone } from './common';
import { traceStatusMeta } from './diagram';
import { ELEMENT_TYPE_META } from './requirement-analysis';

// ---- 稳定码 → 展示标签（中文是展示层，稳定码是跨线契约）----

// 配色输出为 CSS 变量引用（styles.css --trace-* 令牌，随主题切换）；视图侧须经 style 应用（SVG 属性不解析 var()）。
export const nodeTypeMeta: Record<TraceNodeType, { label: string; accent: string }> = {
  material: { label: '材料', accent: 'var(--trace-node-material)' },
  element: { label: '知识项', accent: 'var(--trace-node-element)' },
  requirement_item: { label: '需求条目', accent: 'var(--trace-node-item)' },
  chart: { label: '图表', accent: 'var(--trace-node-chart)' },
  document: { label: '文档', accent: 'var(--trace-node-document)' },
};

export const SWIMLANE_ORDER: TraceNodeType[] = [
  'material',
  'element',
  'requirement_item',
  'chart',
  'document',
];

export const relationKindLabels: Record<string, string> = {
  material_element: '来源提取',
  element_item: '条目来源',
  chart_source: '图表来源',
  document_reference: '文档承接',
  supporting_basis: '支撑依据',
};

// ---- P4 06 A.3：追溯图按知识翼过滤 element 节点（业务知识过滤器）----
// element 节点的 sub_label = element_type 稳定码 → 经 ELEMENT_TYPE_META 派生翼归属。
// 非 element 节点不受影响；过滤后端点缺失的悬空边由布局器自动剔除（不绘制）。
export type TraceWingFilter = 'all' | 'business';

export function elementNodeIsBusiness(node: TraceNodeRead): boolean {
  if (node.node_type !== 'element' || !node.sub_label) return false;
  return ELEMENT_TYPE_META[node.sub_label]?.category === 'business';
}

export function filterChainByWing(
  chain: TraceChainRead | null,
  wing: TraceWingFilter,
): TraceChainRead | null {
  if (!chain || wing === 'all') return chain;
  return {
    ...chain,
    levels: chain.levels.map((lv) => ({
      ...lv,
      nodes: lv.nodes.filter((n) => n.node_type !== 'element' || elementNodeIsBusiness(n)),
    })),
  };
}

/** 关系类型配色：有效/派生边按类型着色（04A 原型 v4 的关系图例），诊断状态另行覆盖。 */
export const relationKindMeta: Record<string, { label: string; stroke: string }> = {
  material_element: { label: '来源提取', stroke: 'var(--trace-relation-material-element)' },
  element_item: { label: '条目来源', stroke: 'var(--trace-relation-element-item)' },
  chart_source: { label: '图表来源', stroke: 'var(--trace-relation-chart-source)' },
  document_reference: { label: '文档承接', stroke: 'var(--trace-relation-document-reference)' },
  supporting_basis: { label: '支撑依据', stroke: 'var(--trace-relation-supporting-basis)' },
};

/** 边的最终视觉：成立关系（有效/结构派生）按关系类型着色，诊断状态（预建立/可疑/失效）覆盖为状态色。 */
export function edgeVisual(edge: TraceEdgeRead): {
  stroke: string;
  dashed: boolean;
  marker: '' | '⚠' | '×';
} {
  const statusMeta = edgeStatusMeta[edge.status];
  if (edge.status === 'effective' || edge.status === 'derived') {
    const kind = relationKindMeta[edge.relation_kind];
    return { stroke: kind?.stroke ?? statusMeta.stroke, dashed: false, marker: '' };
  }
  return { stroke: statusMeta.stroke, dashed: statusMeta.dashed, marker: statusMeta.marker };
}

export const edgeStatusMeta: Record<
  TraceEdgeStatus,
  { label: string; tone: BadgeTone; stroke: string; dashed: boolean; marker: '' | '⚠' | '×' }
> = {
  derived: { label: '结构派生', tone: 'neutral', stroke: 'var(--trace-status-derived)', dashed: false, marker: '' },
  pre_established: { label: '预建立', tone: 'processing', stroke: 'var(--trace-status-pre-established)', dashed: true, marker: '' },
  effective: { label: '有效', tone: 'success', stroke: 'var(--trace-status-effective)', dashed: false, marker: '' },
  suspect_pending_review: { label: '可疑待复核', tone: 'warning', stroke: 'var(--trace-status-suspect)', dashed: true, marker: '⚠' },
  invalid: { label: '失效', tone: 'neutral', stroke: 'var(--trace-status-invalid)', dashed: true, marker: '×' },
};

export const gapKindMeta: Record<TraceGapKind, { label: string }> = {
  item_no_source: { label: '条目无来源' },
  item_no_chart: { label: '条目无图表覆盖' },
  item_no_document: { label: '条目未入文档' },
  chart_orphan: { label: '图表无来源关系' },
  element_orphan: { label: '要素未被引用' },
  business_knowledge_unreferenced: { label: '业务知识未被引用' },
};

export const coverageDirectionLabels: Record<string, string> = {
  item_source: '条目 → 来源',
  item_chart: '条目 → 图表',
  item_document: '条目 → 文档',
};

export const navTargetMeta: Record<string, { label: string; workbenchKey: string }> = {
  requirement_workbench: { label: '需求管理工作台', workbenchKey: 'management' },
  diagram_workbench: { label: '图表设计工作台', workbenchKey: 'diagram' },
  publication_workbench: { label: '发布管理工作台', workbenchKey: 'release' },
};

/** sub_label 稳定码 → 中文（条目语义类型 + 要素类型；未知值原样显示）。 */
const ITEM_TYPE_LABELS: Record<string, string> = {
  functional: '功能需求',
  quality: '质量属性',
  constraint: '约束',
  data: '数据需求',
  interface: '接口需求',
};

export function subLabelText(subLabel: string | null | undefined): string | null {
  if (!subLabel) return null;
  return ITEM_TYPE_LABELS[subLabel] ?? ELEMENT_TYPE_META[subLabel]?.label ?? subLabel;
}

export function nodeStatusMeta(
  nodeType: TraceNodeType,
  status: string | null | undefined,
): { label: string; tone: BadgeTone } | null {
  if (!status) return null;
  const map: Record<string, { label: string; tone: BadgeTone }> = {
    pending_confirmation: { label: '待确认', tone: 'warning' },
    confirmed: { label: '已确认', tone: 'success' },
    draft: { label: '草稿中', tone: 'processing' },
    returned_for_revision: { label: '退回修订', tone: 'danger' },
    voided: { label: '已作废', tone: 'neutral' },
    index_draft: { label: '编排中', tone: 'processing' },
    index_blocked: { label: '索引受阻', tone: 'danger' },
    index_ready: { label: '索引形成', tone: 'processing' },
    markdown_draft: { label: '中间稿', tone: 'processing' },
    markdown_finalized: { label: '已定稿', tone: 'success' },
    baseline_published: { label: '已发布基线', tone: 'success' },
  };
  return map[status] ?? { label: status, tone: 'neutral' };
}

// ---- 关系网画布布局（纯函数：确定性坐标，供组件渲染与单测共用）----

export const NODE_W = 176;
// 固定尺寸方框（px）：容纳 图标行 + 两行标签(12px·省略号) + 内边距，留底部余量防亚像素裁切；
// 卡内排版为固定 px（见 styles.css .trace-node 说明），不随流体根字号缩放。
export const NODE_H = 94;
export const H_GAP = 84;
export const V_GAP = 16;
export const PAD = 24;

export type LayoutMode = 'flow' | 'swimlane';

export interface GraphNodeVM {
  key: string; // `${nodeType}:${ref}`；摘要节点=`summary:{column}`
  nodeType: TraceNodeType | 'summary';
  ref: string;
  label: string;
  subLabel: string | null;
  /** 仅材料节点：接入登记的来源说明（详情面板「来源说明」，不上卡片） */
  sourceNote: string | null;
  statusLabel: string | null;
  statusTone: BadgeTone | null;
  isFocus: boolean;
  isSummary: boolean;
  summaryCount: number;
  /** 摘要节点的方向（上游/下游），用于按方向着色（v4 原型 +N 摘要色） */
  summaryDirection: TraceDirection | null;
  x: number;
  y: number;
}

export interface GraphEdgeVM {
  key: string;
  edge: TraceEdgeRead;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  stroke: string;
  dashed: boolean;
  marker: '' | '⚠' | '×';
  /** 层级标注（每层第一条边标 “N层关联”，仅焦点流向布局） */
  levelLabel: string;
}

export interface GraphColumnVM {
  key: string;
  title: string;
  x: number;
}

export interface GraphLayoutVM {
  nodes: GraphNodeVM[];
  edges: GraphEdgeVM[];
  columns: GraphColumnVM[];
  width: number;
  height: number;
}

export function nodeKeyOf(nodeType: string, ref: string): string {
  return `${nodeType}:${ref}`;
}

function toNodeVM(node: TraceNodeRead, isFocus: boolean): GraphNodeVM {
  const meta = nodeStatusMeta(node.node_type, node.status);
  return {
    key: nodeKeyOf(node.node_type, node.ref),
    nodeType: node.node_type,
    ref: node.ref,
    label: node.label || '（无标签）',
    subLabel: node.sub_label ?? null,
    sourceNote: node.source_note ?? null,
    statusLabel: meta?.label ?? null,
    statusTone: meta?.tone ?? null,
    isFocus,
    isSummary: false,
    summaryCount: 0,
    summaryDirection: null,
    x: 0,
    y: 0,
  };
}

function summaryNodeVM(direction: TraceDirection, distance: number, count: number): GraphNodeVM {
  return {
    key: `summary:${direction}:${distance}`,
    nodeType: 'summary',
    ref: '',
    label: direction === 'upstream' ? `+${count} 上游来源` : `+${count} 下游影响`,
    subLabel: null,
    sourceNote: null,
    statusLabel: null,
    statusTone: null,
    isFocus: false,
    isSummary: true,
    summaryCount: count,
    summaryDirection: direction,
    x: 0,
    y: 0,
  };
}

interface ColumnDraft {
  key: string;
  title: string;
  nodes: GraphNodeVM[];
}

function placeColumns(columns: ColumnDraft[]): GraphLayoutVM {
  const maxRows = Math.max(1, ...columns.map((c) => c.nodes.length));
  const height = PAD * 2 + maxRows * (NODE_H + V_GAP) - V_GAP;
  const placed: GraphColumnVM[] = [];
  const nodes: GraphNodeVM[] = [];
  columns.forEach((col, colIdx) => {
    const x = PAD + colIdx * (NODE_W + H_GAP);
    placed.push({ key: col.key, title: col.title, x });
    const colHeight = col.nodes.length * (NODE_H + V_GAP) - V_GAP;
    const yOffset = PAD + Math.max(0, (height - PAD * 2 - colHeight) / 2);
    col.nodes.forEach((node, rowIdx) => {
      nodes.push({ ...node, x, y: yOffset + rowIdx * (NODE_H + V_GAP) });
    });
  });
  const width = PAD * 2 + columns.length * (NODE_W + H_GAP) - H_GAP;
  return { nodes, edges: [], columns: placed, width, height };
}

function buildEdges(
  entries: { edge: TraceEdgeRead; distance?: number }[],
  positions: Map<string, GraphNodeVM>,
): GraphEdgeVM[] {
  const edges: GraphEdgeVM[] = [];
  const seen = new Set<string>();
  const labeledLevels = new Set<string>();
  for (const { edge, distance } of entries) {
    if (seen.has(edge.edge_key)) continue;
    seen.add(edge.edge_key);
    const from = positions.get(nodeKeyOf(edge.upstream_type, edge.upstream_ref));
    const to = positions.get(nodeKeyOf(edge.downstream_type, edge.downstream_ref));
    if (!from || !to) continue; // 端点被折叠进摘要节点时不画悬空边
    const visual = edgeVisual(edge);
    let levelLabel = '';
    if (distance !== undefined) {
      const levelKey = `${distance}`;
      if (!labeledLevels.has(levelKey)) {
        labeledLevels.add(levelKey);
        levelLabel = `${distance}层关联`;
      }
    }
    edges.push({
      key: edge.edge_key,
      edge,
      fromX: from.x + NODE_W,
      fromY: from.y + NODE_H / 2,
      toX: to.x,
      toY: to.y + NODE_H / 2,
      stroke: visual.stroke,
      dashed: visual.dashed,
      marker: visual.marker,
      levelLabel,
    });
  }
  return edges;
}

/** 焦点流向布局：列=带符号距离（上游在左、焦点居中、下游在右），04A §6 默认布局。 */
export function buildFlowLayout(
  focus: TraceNodeRead,
  upstream: TraceChainRead | null,
  downstream: TraceChainRead | null,
): GraphLayoutVM {
  const upLevels = upstream?.levels ?? [];
  const downLevels = downstream?.levels ?? [];
  const columns: ColumnDraft[] = [];
  // 同一节点在窗口内只出现一次（焦点优先；防守折叠回流/过渡帧的重复键）
  const seen = new Set<string>([nodeKeyOf(focus.node_type, focus.ref)]);
  const dedupe = (nodes: TraceNodeRead[]): GraphNodeVM[] => {
    const kept: GraphNodeVM[] = [];
    for (const n of nodes) {
      const key = nodeKeyOf(n.node_type, n.ref);
      if (seen.has(key)) continue;
      seen.add(key);
      kept.push(toNodeVM(n, false));
    }
    return kept;
  };
  for (let i = upLevels.length - 1; i >= 0; i -= 1) {
    const level = upLevels[i];
    const nodes = dedupe(level.nodes);
    if (level.folded_count > 0) {
      nodes.push(summaryNodeVM('upstream', level.distance, level.folded_count));
    }
    columns.push({ key: `up-${level.distance}`, title: `上游${level.distance}层`, nodes });
  }
  columns.push({ key: 'focus', title: '焦点对象', nodes: [toNodeVM(focus, true)] });
  for (const level of downLevels) {
    const nodes = dedupe(level.nodes);
    if (level.folded_count > 0) {
      nodes.push(summaryNodeVM('downstream', level.distance, level.folded_count));
    }
    columns.push({ key: `down-${level.distance}`, title: `下游${level.distance}层`, nodes });
  }
  const layout = placeColumns(columns);
  const positions = new Map(layout.nodes.map((n) => [n.key, n]));
  const entries = [...upLevels, ...downLevels].flatMap((lv) =>
    lv.edges.map((edge) => ({ edge, distance: lv.distance })),
  );
  layout.edges = buildEdges(entries, positions);
  return layout;
}

/** 类型泳道布局：固定五泳道重排当前窗口内节点（只读；双击不重定心），04A §6。 */
export function buildSwimlaneLayout(
  focus: TraceNodeRead,
  upstream: TraceChainRead | null,
  downstream: TraceChainRead | null,
): GraphLayoutVM {
  const byKey = new Map<string, GraphNodeVM>();
  const register = (node: TraceNodeRead, isFocus: boolean) => {
    const vm = toNodeVM(node, isFocus);
    if (!byKey.has(vm.key)) byKey.set(vm.key, vm);
  };
  register(focus, true);
  for (const level of [...(upstream?.levels ?? []), ...(downstream?.levels ?? [])]) {
    for (const node of level.nodes) register(node, false);
  }
  const columns: ColumnDraft[] = SWIMLANE_ORDER.map((laneType) => ({
    key: `lane-${laneType}`,
    title: nodeTypeMeta[laneType].label,
    nodes: [...byKey.values()].filter((n) => n.nodeType === laneType),
  }));
  const layout = placeColumns(columns);
  const positions = new Map(layout.nodes.map((n) => [n.key, n]));
  const entries = [
    ...(upstream?.levels ?? []),
    ...(downstream?.levels ?? []),
  ].flatMap((lv) => lv.edges.map((edge) => ({ edge })));
  layout.edges = buildEdges(entries, positions);
  return layout;
}

// ---- 追溯路径（面包屑；会话内状态，不落库）----

export interface TraceHop {
  nodeType: TraceNodeType;
  ref: string;
  label: string;
}

/** 双击重定心追加一跳；重定心到路径上已有节点时=沿原路回退到该跳。 */
export function pushHop(path: TraceHop[], hop: TraceHop): TraceHop[] {
  const existing = path.findIndex((h) => h.nodeType === hop.nodeType && h.ref === hop.ref);
  if (existing >= 0) return path.slice(0, existing + 1);
  return [...path, hop];
}

export function backTo(path: TraceHop[], index: number): TraceHop[] {
  return path.slice(0, Math.max(0, index) + 1);
}

// ---- 边/链路展示辅助 ----

export function edgeSummary(edge: TraceEdgeRead): string {
  const kind = relationKindLabels[edge.relation_kind] ?? edge.relation_kind;
  const status = edgeStatusMeta[edge.status]?.label ?? edge.status;
  return `${kind} · ${status}`;
}

export function linkStatusMeta(status: string): { label: string; tone: BadgeTone } {
  return (
    traceStatusMeta[status as keyof typeof traceStatusMeta] ?? { label: status, tone: 'neutral' }
  );
}

// ---- 材料卡片锚点引文（2026-07-12 卡片语义修正：卡片=边上下文锚点引文，非接入元数据串）----

export interface MaterialCardQuoteVM {
  /** 卡片主文本引文；null=窗口内无带引文的 material_element 边，回退节点 label（原文头） */
  quote: string | null;
  /** 窗口内该材料带引文的来源提取边数（>1 时卡片附「等 N 处」计数） */
  total: number;
}

/**
 * 焦点流向窗口内材料卡片主文本取值：该材料 material_element 边的 anchor_quote。
 * 取值顺序=选中边优先→路径相关优先→否则第一条。窗口边由焦点 BFS 构造，凡入窗的
 * material_element 边必在通往焦点的路径上，故窗口序（近焦点层在前）即路径相关序。
 * 选中边优先对无引文的选中边同样生效：此时卡片回退原文头（与边详情提示一致），
 * 而非展示其他知识项的引文。
 */
export function materialCardQuote(
  materialRef: string,
  windowEdges: TraceEdgeRead[],
  selectedEdgeKey: string | null,
): MaterialCardQuoteVM {
  const edges = windowEdges.filter(
    (e) => e.relation_kind === 'material_element' && e.upstream_ref === materialRef,
  );
  const candidates = edges.filter((e) => e.anchor_quote);
  const selected = selectedEdgeKey ? edges.find((e) => e.edge_key === selectedEdgeKey) : undefined;
  if (selected) return { quote: selected.anchor_quote ?? null, total: candidates.length };
  if (candidates.length === 0) return { quote: null, total: 0 };
  return { quote: candidates[0].anchor_quote ?? null, total: candidates.length };
}

/** 材料节点详情「原文摘录」：窗口内逐知识项引文清单（元素标签由视图层经 nodeLabels 映射）。 */
export interface MaterialExcerptVM {
  edgeKey: string;
  elementRef: string;
  quotes: string[];
}

export function materialExcerpts(
  materialRef: string,
  windowEdges: TraceEdgeRead[],
): MaterialExcerptVM[] {
  return windowEdges
    .filter(
      (e) =>
        e.relation_kind === 'material_element' &&
        e.upstream_ref === materialRef &&
        (e.anchor_quotes?.length ?? 0) > 0,
    )
    .map((e) => ({ edgeKey: e.edge_key, elementRef: e.downstream_ref, quotes: e.anchor_quotes ?? [] }));
}

// ---- 文档片段预览触发目标（04A §8 + 2026-07-12 文档节点触发分支）----

export type FragmentAssetTarget = { type: 'requirement_item' | 'chart'; ref: string } | null;

export interface FragmentSelectionLike {
  kind: 'node' | 'edge';
  node?: { nodeType: string; ref: string; isSummary: boolean };
  edge?: TraceEdgeRead;
}

/**
 * 片段预览目标：选中条目/图表节点、文档承接边（取上游资产）、文档节点（取当前焦点资产）
 * 或未选中时的焦点本身。文档节点分支：焦点为条目/图表时以焦点资产拉片段；
 * 焦点非条目/图表时无可指资产，不触发、不报错。
 */
export function resolveFragmentTarget(
  selection: FragmentSelectionLike | null,
  focus: Pick<TraceNodeRead, 'node_type' | 'ref'> | null,
): FragmentAssetTarget {
  const focusAsset: FragmentAssetTarget =
    focus && (focus.node_type === 'requirement_item' || focus.node_type === 'chart')
      ? { type: focus.node_type, ref: focus.ref }
      : null;
  if (selection?.kind === 'node' && selection.node && !selection.node.isSummary) {
    const t = selection.node.nodeType;
    if (t === 'requirement_item' || t === 'chart') return { type: t, ref: selection.node.ref };
    if (t === 'document') return focusAsset;
    return null;
  }
  if (selection?.kind === 'edge' && selection.edge?.relation_kind === 'document_reference') {
    const t = selection.edge.upstream_type;
    return t === 'requirement_item' || t === 'chart'
      ? { type: t, ref: selection.edge.upstream_ref }
      : null;
  }
  return selection ? null : focusAsset;
}

// ---- 资产 → 文档片段预览投影（04A §8 增补：追溯依据不入 docx 正文）----

export interface FragmentPreviewVM {
  /** 稿件状态徽标：定稿/中间稿/待重新生成/待条目修订收束 */
  statusText: string;
  statusTone: BadgeTone;
  /** 已冻结为发布基线时的附加徽标 */
  baselineText: string | null;
  /** 文档与版本上下文（如「需求规格说明 · 索引 v2 · Markdown v3」） */
  contextText: string;
  /** 该资产不在当前索引版本时的提示 */
  staleText: string | null;
  fragments: AssetFragmentRead['fragments'];
  emptyText: string | null;
}

const DRAFT_STATUS_META: Record<string, { label: string; tone: BadgeTone }> = {
  draft: { label: 'Markdown 中间稿（内容可能继续调整）', tone: 'processing' },
  finalized: { label: 'Markdown 定稿', tone: 'success' },
  superseded: { label: '索引已调整 · 待重新生成', tone: 'warning' },
  awaiting_item_revision: { label: '待条目修订收束 · 需重新生成', tone: 'warning' },
};

export function buildFragmentPreview(read: AssetFragmentRead): FragmentPreviewVM {
  const meta = read.draft_status
    ? (DRAFT_STATUS_META[read.draft_status] ?? { label: read.draft_status, tone: 'neutral' as BadgeTone })
    : { label: '尚无 Markdown 稿', tone: 'neutral' as BadgeTone };
  const parts: string[] = [];
  if (read.document_title) parts.push(read.document_title);
  if (read.index_version) parts.push(`索引 v${read.index_version}`);
  if (read.draft_version) parts.push(`Markdown v${read.draft_version}`);
  return {
    statusText: meta.label,
    statusTone: meta.tone,
    baselineText: read.baseline_ref ? '已冻结为发布基线' : null,
    contextText: parts.join(' · '),
    staleText:
      read.document_ref && !read.in_current_index
        ? '该资产不在当前文档内容索引中（片段来自历史稿）'
        : null,
    fragments: read.fragments,
    emptyText: read.fragments.length === 0 ? (read.next_action ?? '暂无文档片段') : null,
  };
}
