import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Empty,
  Input,
  Popover,
  Progress,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ApartmentOutlined,
  ArrowLeftOutlined,
  ExclamationCircleOutlined,
  FileDoneOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  PieChartOutlined,
  ProfileOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { WorkbenchFrame } from './WorkbenchFrame';
import { traceApi } from '../api/trace';
import { publicationApi } from '../api/publication';
import type { AssetFragmentRead } from '../api/publication';
import { renderMarkdownHtml } from '../view-models/publication';
import { MermaidPreview } from '../ui/mermaid';
import { PlantumlPreview } from '../ui/PlantumlPreview';
import type {
  TraceChainRead,
  TraceCoverageRead,
  TraceEdgeRead,
  TraceEntryRead,
  TraceGapItemRead,
  TraceGapKind,
  TraceGapListRead,
  TraceNodeRead,
  TraceNodeType,
  TraceSuspectListRead,
} from '../api/trace';
import type { TraceLinkRead } from '../api/charts';
import type { ProjectRead } from '../api/projects';
import type { SearchTarget } from '../view-models/search';
import type { WorkbenchHandoff } from '../view-models/workbench-handoff';
import type { GraphLayoutVM, GraphNodeVM, TraceHop } from '../view-models/trace-graph';
import {
  NODE_H,
  NODE_W,
  backTo,
  buildFlowLayout,
  buildFragmentPreview,
  buildSwimlaneLayout,
  coverageDirectionLabels,
  edgeStatusMeta,
  filterChainByWing,
  gapKindMeta,
  linkStatusMeta,
  materialCardQuote,
  materialExcerpts,
  navTargetMeta,
  nodeKeyOf,
  nodeStatusMeta,
  nodeTypeMeta,
  pushHop,
  resolveFragmentTarget,
  relationKindLabels,
  relationKindMeta,
  subLabelText,
} from '../view-models/trace-graph';
import type { TraceWingFilter } from '../view-models/trace-graph';
import type { BadgeTone } from '../view-models/common';
import { formatAbsoluteTime } from '../view-models/time';

const { Text, Paragraph } = Typography;

const TONE_COLOR: Record<BadgeTone, string> = {
  success: 'green',
  processing: 'blue',
  warning: 'orange',
  danger: 'red',
  neutral: 'default',
};

const NODE_TYPE_ICON: Record<TraceNodeType, ReactNode> = {
  material: <FileTextOutlined />,
  element: <TagsOutlined />,
  requirement_item: <ProfileOutlined />,
  chart: <ApartmentOutlined />,
  document: <FileDoneOutlined />,
};

type DirectionMode = 'both' | 'upstream' | 'downstream';
type LayoutChoice = 'flow' | 'swimlane';
type DiagFilter = 'gaps' | 'suspects' | null;

const DIRECTION_LABELS: Record<DirectionMode, string> = {
  both: '双向',
  upstream: '仅上游',
  downstream: '仅下游',
};

const LAYOUT_LABELS: Record<LayoutChoice, string> = {
  flow: '焦点流向布局',
  swimlane: '类型泳道布局',
};

interface Selection {
  kind: 'node' | 'edge';
  node?: GraphNodeVM;
  edge?: TraceEdgeRead;
}

function newKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

function foldedTotal(...chains: (TraceChainRead | null)[]): number {
  return chains.reduce(
    (sum, chain) => sum + (chain?.levels.reduce((s, lv) => s + lv.folded_count, 0) ?? 0),
    0,
  );
}

interface TraceabilityWorkbenchProps {
  selectedProject: ProjectRead | null;
  operatorRef: string;
  /** 全局检索深链（P4 预留，05 §4）：五类资产默认不路由本台（映射表无 traceability），
   *  此 prop 为图谱化/未来"追溯落点"承接；若命中路由至此则一次性聚焦目标节点。 */
  searchTarget?: SearchTarget | null;
  workbenchHandoff?: WorkbenchHandoff | null;
  onConsumeWorkbenchHandoff?: (token: number) => void;
  onNavigate?: (key: string) => void;
}

export function TraceabilityWorkbench({
  selectedProject,
  operatorRef,
  searchTarget,
  workbenchHandoff,
  onConsumeWorkbenchHandoff,
  onNavigate,
}: TraceabilityWorkbenchProps) {
  const projectId = selectedProject?.id;
  // 检索深链一次性消费守卫（预留；范式同 resumeConsumedRef）。
  const searchConsumedRef = useRef<number | null>(null);
  const handoffConsumedRef = useRef<number | null>(null);
  // 入口加载可能晚于交接消费；保留本次文档聚焦意图，避免 default_focus 覆盖目标文档。
  const incomingHandoffRef = useRef<WorkbenchHandoff | null>(workbenchHandoff ?? null);
  if (workbenchHandoff) incomingHandoffRef.current = workbenchHandoff;
  const [entry, setEntry] = useState<TraceEntryRead | null>(null);
  const [coverage, setCoverage] = useState<TraceCoverageRead | null>(null);
  const [focus, setFocus] = useState<TraceNodeRead | null>(null);
  const [path, setPath] = useState<TraceHop[]>([]);
  const [upChain, setUpChain] = useState<TraceChainRead | null>(null);
  const [downChain, setDownChain] = useState<TraceChainRead | null>(null);
  const [depth, setDepth] = useState(2);
  const [limit, setLimit] = useState(8);
  const [direction, setDirection] = useState<DirectionMode>('both');
  const [layout, setLayout] = useState<LayoutChoice>('flow');
  const [showInvalid, setShowInvalid] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [linkDetail, setLinkDetail] = useState<TraceLinkRead | null>(null);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<TraceNodeType | 'all'>('all');
  const [diagFilter, setDiagFilter] = useState<DiagFilter>(null);
  const [wingFilter, setWingFilter] = useState<TraceWingFilter>('all'); // P4 06 A.3 业务知识过滤器
  const [diagRefs, setDiagRefs] = useState<Set<string> | null>(null);
  const [reviewReason, setReviewReason] = useState('');
  const [fragment, setFragment] = useState<AssetFragmentRead | null>(null);
  const [fragmentLoading, setFragmentLoading] = useState(false);
  const [fragmentError, setFragmentError] = useState(false);

  // 文档片段预览目标：选中条目/图表节点、文档承接边（取上游资产）、文档节点（取焦点资产）或当前焦点
  const fragmentTarget = useMemo(() => resolveFragmentTarget(selection, focus), [selection, focus]);

  useEffect(() => {
    let cancelled = false;
    setFragment(null);
    setFragmentError(false);
    if (!projectId || !fragmentTarget) {
      setFragmentLoading(false);
      return undefined;
    }
    setFragmentLoading(true);
    publicationApi
      .getAssetFragment(projectId, fragmentTarget.type, fragmentTarget.ref)
      .then((read) => {
        if (!cancelled) setFragment(read);
      })
      .catch(() => {
        // 失败≠无可指资产：单独记错误态，右栏据此给出重试提示而非「焦点非条目/图表」误导文案
        if (!cancelled) {
          setFragment(null);
          setFragmentError(true);
        }
      })
      .finally(() => {
        if (!cancelled) setFragmentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, fragmentTarget]);

  const refreshEntry = useCallback(async () => {
    if (!projectId) return null;
    try {
      const [result, cov] = await Promise.all([
        traceApi.entry(projectId),
        traceApi.coverage(projectId).catch(() => null),
      ]);
      setEntry(result);
      setCoverage(cov);
      setLoadError(null);
      return result;
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
      return null;
    }
  }, [projectId]);

  // 项目切换：AEP-058 入口 → 默认焦点落地（页面设计 §3 编排）
  useEffect(() => {
    let cancelled = false;
    setEntry(null);
    setCoverage(null);
    setFocus(null);
    setPath([]);
    setSelection(null);
    setDiagnosticsOpen(false);
    setDiagFilter(null);
    setDiagRefs(null);
    if (!projectId) return undefined;
    void refreshEntry().then((result) => {
      if (cancelled || !result) return;
      const incoming = incomingHandoffRef.current;
      const documentFocus =
        incoming?.intent === 'inspect_document_trace' &&
        incoming.targetWorkbench === 'traceability' &&
        incoming.projectId === projectId &&
        incoming.anchor.entityType === 'document'
          ? { node_type: 'document' as const, ref: incoming.anchor.ref, label: incoming.anchor.title }
          : null;
      const initialFocus = documentFocus ?? result.default_focus;
      if (initialFocus) {
        if (documentFocus) {
          setDepth(3);
          setDirection('both');
          setLayout('flow');
        }
        setFocus(initialFocus);
        setPath([
          {
            nodeType: initialFocus.node_type,
            ref: initialFocus.ref,
            label: initialFocus.label,
          },
        ]);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshEntry]);

  // 检索深链一次性聚焦（05 §4 预留）：token+projectId 双守卫；优先于 default_focus。
  // 五类资产默认不路由本台，故常态不触发；保留结构供图谱化/未来追溯落点承接。
  useEffect(() => {
    if (
      !searchTarget ||
      searchTarget.projectId !== projectId ||
      searchConsumedRef.current === searchTarget.token
    ) {
      return;
    }
    searchConsumedRef.current = searchTarget.token;
    const node: TraceNodeRead = {
      node_type: searchTarget.entityType as TraceNodeRead['node_type'],
      ref: searchTarget.ref,
      label: searchTarget.title,
    };
    setFocus(node);
    setPath([{ nodeType: node.node_type, ref: node.ref, label: node.label }]);
  }, [searchTarget, projectId]);

  // 发布基线交接：以文档为焦点打开三层、双向、焦点流向关系网。
  useEffect(() => {
    if (
      !workbenchHandoff ||
      workbenchHandoff.intent !== 'inspect_document_trace' ||
      workbenchHandoff.targetWorkbench !== 'traceability' ||
      workbenchHandoff.projectId !== projectId ||
      workbenchHandoff.anchor.entityType !== 'document' ||
      entry === null ||
      handoffConsumedRef.current === workbenchHandoff.token
    ) {
      return;
    }
    const node: TraceNodeRead = {
      node_type: 'document',
      ref: workbenchHandoff.anchor.ref,
      label: workbenchHandoff.anchor.title,
    };
    handoffConsumedRef.current = workbenchHandoff.token;
    setDepth(3);
    setDirection('both');
    setLayout('flow');
    setFocus(node);
    setPath([{ nodeType: node.node_type, ref: node.ref, label: node.label }]);
    setSelection(null);
    onConsumeWorkbenchHandoff?.(workbenchHandoff.token);
  }, [entry, onConsumeWorkbenchHandoff, projectId, workbenchHandoff]);

  // 焦点/窗口参数变化：并行取 AEP-059/060（漫游=以新焦点重取）
  const refreshChains = useCallback(async () => {
    if (!projectId || !focus) return;
    setChainLoading(true);
    try {
      const params = { depth, limit, includeInvalid: showInvalid };
      const [up, down] = await Promise.all([
        direction !== 'downstream'
          ? traceApi.upstream(projectId, focus.node_type, focus.ref, params)
          : Promise.resolve(null),
        direction !== 'upstream'
          ? traceApi.downstream(projectId, focus.node_type, focus.ref, params)
          : Promise.resolve(null),
      ]);
      setUpChain(up);
      setDownChain(down);
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    } finally {
      setChainLoading(false);
    }
    // 依赖按 focus 的 type/ref 取键（回调体只读这两个字段）：
    // 展示字段合并（见下方效果）替换 focus 对象时不触发重取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, focus?.node_type, focus?.ref, depth, limit, direction, showInvalid]);

  useEffect(() => {
    setUpChain(null);
    setDownChain(null);
    void refreshChains();
  }, [refreshChains]);

  // 链路响应带回权威焦点节点：面包屑回跳/深链等路径只以 {type,ref,label} 重建焦点，
  // 在此把 source_note/status/sub_label 展示字段合并回来（label 保留当前值，尊重 via 语义）
  useEffect(() => {
    if (!focus) return;
    const authoritative = [upChain, downChain]
      .map((c) =>
        c && c.focus.node_type === focus.node_type && c.focus.ref === focus.ref ? c.focus : null,
      )
      .find((f) => f !== null);
    if (!authoritative) return;
    const differs =
      (authoritative.source_note ?? null) !== (focus.source_note ?? null) ||
      (authoritative.status ?? null) !== (focus.status ?? null) ||
      (authoritative.sub_label ?? null) !== (focus.sub_label ?? null);
    if (differs) setFocus({ ...authoritative, label: focus.label });
  }, [upChain, downChain, focus]);

  // 左区诊断筛选（次）：缺口/可疑 → 过滤对象导航到诊断涉及的对象（AEP-063/064）
  useEffect(() => {
    let cancelled = false;
    if (!projectId || !diagFilter) {
      setDiagRefs(null);
      return undefined;
    }
    const load = async () => {
      try {
        if (diagFilter === 'gaps') {
          const list = await traceApi.gaps(projectId);
          if (cancelled) return;
          setDiagRefs(new Set(list.items.map((g) => nodeKeyOf(g.node_type, g.node_ref))));
        } else {
          const list = await traceApi.suspects(projectId, false);
          if (cancelled) return;
          const refs = new Set<string>();
          for (const link of list.items) {
            refs.add(nodeKeyOf(link.upstream_type, link.upstream_ref));
            refs.add(nodeKeyOf(link.downstream_type, link.downstream_ref));
          }
          setDiagRefs(refs);
        }
      } catch (error) {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [projectId, diagFilter]);

  const graph: GraphLayoutVM | null = useMemo(() => {
    if (!focus) return null;
    // 重定心的过渡帧：旧链路仍挂载但焦点已换，只采用归属当前焦点的链路
    const matches = (chain: TraceChainRead | null) =>
      chain && chain.focus.node_type === focus.node_type && chain.focus.ref === focus.ref
        ? chain
        : null;
    // 业务知识过滤器：仅保留 element 泳道的业务翼节点（悬空边由布局器自动剔除）。
    const up = filterChainByWing(matches(upChain), wingFilter);
    const down = filterChainByWing(matches(downChain), wingFilter);
    return layout === 'flow'
      ? buildFlowLayout(focus, up, down)
      : buildSwimlaneLayout(focus, up, down);
  }, [focus, upChain, downChain, layout, wingFilter]);

  const recenter = useCallback(
    (node: GraphNodeVM | TraceNodeRead, viaLabel?: string) => {
      const nodeTypeRaw = 'node_type' in node ? node.node_type : node.nodeType;
      if (nodeTypeRaw === 'summary') return;
      const nodeType = nodeTypeRaw as TraceNodeType;
      const ref = node.ref;
      const label = viaLabel ?? node.label;
      const focusNode: TraceNodeRead = {
        node_type: nodeType,
        ref,
        label,
        sub_label: 'subLabel' in node ? node.subLabel : node.sub_label,
        status: 'statusLabel' in node ? null : node.status,
        source_note: 'sourceNote' in node ? node.sourceNote : node.source_note,
      };
      setFocus(focusNode);
      setPath((prev) => pushHop(prev, { nodeType, ref, label }));
      setSelection(null);
      setDiagnosticsOpen(false);
    },
    [],
  );

  const selectNode = useCallback((node: GraphNodeVM) => {
    if (node.isSummary) {
      // 摘要节点点击=该窗口预算翻倍重取（展开局部，页面设计 §4）
      setLimit((prev) => Math.min(80, prev * 2));
      return;
    }
    setSelection({ kind: 'node', node });
    setLinkDetail(null);
  }, []);

  const selectEdge = useCallback(
    (edge: TraceEdgeRead) => {
      setSelection({ kind: 'edge', edge });
      setLinkDetail(null);
      if (edge.origin === 'ldm013' && edge.link_ref && projectId) {
        void traceApi
          .linkDetail(projectId, edge.link_ref)
          .then(setLinkDetail)
          .catch((error) => setLoadError(error instanceof Error ? error.message : String(error)));
      }
    },
    [projectId],
  );

  const submitReview = useCallback(
    async (linkRef: string, conclusion: 'restore' | 'maintain') => {
      if (!projectId) return;
      try {
        const result = await traceApi.review(projectId, linkRef, {
          conclusion,
          reason: reviewReason || null,
          operator_ref: operatorRef,
          idempotency_key: newKey('trc-review'),
        });
        setLinkDetail(result.link);
        setReviewReason('');
        message.success(
          result.status === 'restored'
            ? '已恢复为预建立；须重走图表核对确认后方可正式确立为有效'
            : '已维持可疑并留痕',
        );
        await Promise.all([refreshEntry(), refreshChains()]);
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    },
    [projectId, operatorRef, reviewReason, refreshEntry, refreshChains],
  );

  const createIssue = useCallback(
    async (title: string, description: string, linkRef?: string | null, chartRef?: string | null) => {
      if (!projectId) return;
      try {
        await traceApi.createIssue(projectId, {
          title,
          description,
          trace_link_ref: linkRef ?? null,
          chart_ref: chartRef ?? null,
          operator_ref: operatorRef,
          idempotency_key: newKey('trc-issue'),
        });
        message.success('已转问题项（来源=追溯诊断；处置闭环归后续迭代）');
        await refreshEntry();
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error));
      }
    },
    [projectId, operatorRef, refreshEntry],
  );

  const navigateTo = useCallback(
    (navTarget: string) => {
      const meta = navTargetMeta[navTarget];
      if (meta && onNavigate) {
        onNavigate(meta.workbenchKey);
      } else {
        message.info(`补全请前往：${meta?.label ?? navTarget}`);
      }
    },
    [onNavigate],
  );

  const backOneHop = useCallback(() => {
    if (path.length < 2) return;
    const target = path[path.length - 2];
    setPath((prev) => backTo(prev, prev.length - 2));
    setFocus({ node_type: target.nodeType, ref: target.ref, label: target.label });
    setSelection(null);
  }, [path]);

  const jumpToHop = useCallback(
    (index: number) => {
      const target = path[index];
      if (!target) return;
      setPath((prev) => backTo(prev, index));
      setFocus({ node_type: target.nodeType, ref: target.ref, label: target.label });
      setSelection(null);
    },
    [path],
  );

  if (!projectId) {
    return (
      <WorkbenchFrame title="追溯分析工作台">
        <Empty description="请先在顶栏选择项目" />
      </WorkbenchFrame>
    );
  }

  const counts = entry?.counts ?? null;
  const windowFolded = foldedTotal(upChain, downChain);
  const headlineCoverage = coverage?.directions.find((d) => d.key === 'item_source') ?? null;
  // 窗口内节点标签索引：结构派生边无 LDM-013 详情，用图内标签兜底显示端点
  const nodeLabels = new Map<string, string>();
  for (const n of graph?.nodes ?? []) {
    if (!n.isSummary) nodeLabels.set(n.key, n.label);
  }
  // 窗口内实际绘制的边（材料卡片引文取值与详情「原文摘录」共用）
  const windowEdges = (graph?.edges ?? []).map((e) => e.edge);

  return (
    <WorkbenchFrame title="追溯分析工作台">
      {loadError ? (
        <Alert title={loadError} showIcon type="error" style={{ marginBottom: 12 }} />
      ) : null}

      {entry && !entry.default_focus && !focus ? (
        <Empty
          description={
            <span>
              项目暂无关系网
              <br />
              <Text type="secondary">{entry.next_action}</Text>
            </span>
          }
        />
      ) : (
        <div className="trace-layout">
          {/* ---- 左区：对象导航（主）+ 诊断筛选（次） ---- */}
          <aside className="trace-left">
            <div className="trace-panel-head">对象导航</div>
            <Input.Search
              allowClear
              placeholder="搜索材料、条目、图表、文档"
              size="small"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="trace-chip-row">
              {(
                [
                  { value: 'all', label: '全部' },
                  ...Object.entries(nodeTypeMeta).map(([value, meta]) => ({
                    value,
                    label: meta.label.replace('需求', ''),
                  })),
                ] as { value: TraceNodeType | 'all'; label: string }[]
              ).map((chip) => (
                <button
                  className={
                    typeFilter === chip.value ? 'trace-chip trace-chip--active' : 'trace-chip'
                  }
                  key={chip.value}
                  type="button"
                  onClick={() => setTypeFilter(chip.value)}
                >
                  {chip.label}
                </button>
              ))}
            </div>
            <div className="trace-left-section">
              <span className="trace-left-caption">诊断筛选</span>
              <div className="trace-chip-row">
                <button
                  className={
                    diagFilter === 'gaps'
                      ? 'trace-chip trace-chip--gap trace-chip--active'
                      : 'trace-chip trace-chip--gap'
                  }
                  type="button"
                  onClick={() => setDiagFilter((v) => (v === 'gaps' ? null : 'gaps'))}
                >
                  ◌ 缺口
                </button>
                <button
                  className={
                    diagFilter === 'suspects'
                      ? 'trace-chip trace-chip--suspect trace-chip--active'
                      : 'trace-chip trace-chip--suspect'
                  }
                  type="button"
                  onClick={() => setDiagFilter((v) => (v === 'suspects' ? null : 'suspects'))}
                >
                  ⚠ 可疑
                </button>
                <Tooltip title="待接入：冲突判定规则尚无事实源（AEP-065 延期）">
                  <button className="trace-chip" disabled type="button">
                    × 冲突
                  </button>
                </Tooltip>
              </div>
            </div>
            {path.length > 0 ? (
              <div className="trace-left-section">
                <span className="trace-left-caption">当前选中对象</span>
                <div className="trace-left-path">
                  {path.map((hop, index) => (
                    <span key={`${hop.nodeType}:${hop.ref}`}>
                      {index > 0 ? <span className="trace-left-path-sep">›</span> : null}
                      <button className="trace-path-hop" type="button" onClick={() => jumpToHop(index)}>
                        {hop.label.length > 14 ? `${hop.label.slice(0, 14)}…` : hop.label}
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="trace-left-groups">
              {(entry?.anchors ?? []).map((group) => {
                if (typeFilter !== 'all' && group.node_type !== typeFilter) return null;
                const matched = group.nodes.filter((n) => {
                  if (search) {
                    // label 已改为原文头优先，来源说明（source_note）降为详情字段后仍须可检索
                    const q = search.toLowerCase();
                    const hit =
                      n.label.toLowerCase().includes(q) ||
                      (n.source_note ?? '').toLowerCase().includes(q);
                    if (!hit) return false;
                  }
                  if (diagRefs && !diagRefs.has(nodeKeyOf(group.node_type, n.ref))) return false;
                  return true;
                });
                if (matched.length === 0) return null;
                const meta = nodeTypeMeta[group.node_type];
                return (
                  <section key={group.node_type} className="trace-left-group">
                    <Text strong style={{ color: meta.accent }}>
                      {meta.label}
                    </Text>
                    {matched.map((n) => {
                      const active = focus && focus.ref === n.ref;
                      const status = n.status
                        ? (() => {
                            const m = nodeStatusMeta(group.node_type, n.status);
                            return m ? <Tag color={TONE_COLOR[m.tone]}>{m.label}</Tag> : null;
                          })()
                        : null;
                      return (
                        <button
                          className={active ? 'trace-obj-card trace-obj-card--active' : 'trace-obj-card'}
                          key={n.ref}
                          type="button"
                          onClick={() => recenter({ ...n, node_type: group.node_type })}
                        >
                          <span className="trace-obj-icon" style={{ background: meta.accent }}>
                            {NODE_TYPE_ICON[group.node_type]}
                          </span>
                          <span className="trace-obj-main">
                            <span className="trace-obj-title">{n.label}</span>
                            <span className="trace-obj-sub">
                              {meta.label}
                              {n.sub_label ? ` · ${subLabelText(n.sub_label)}` : ''}
                            </span>
                          </span>
                          {status}
                        </button>
                      );
                    })}
                  </section>
                );
              })}
            </div>
            <Popover
              content={
                <div className="trace-full-path">
                  {path.map((hop, index) => (
                    <button
                      className="trace-path-hop"
                      key={`${hop.nodeType}:${hop.ref}`}
                      type="button"
                      onClick={() => jumpToHop(index)}
                    >
                      {index + 1}. {nodeTypeMeta[hop.nodeType].label} · {hop.label}
                    </button>
                  ))}
                  {path.length === 0 ? <Text type="secondary">尚未形成追溯路径</Text> : null}
                </div>
              }
              placement="right"
              title="完整追溯路径"
              trigger="click"
            >
              <Button block size="small">
                查看完整路径
              </Button>
            </Popover>
          </aside>

          {/* ---- 中区：追溯关系总览（工具条 + 路径 + 画布 + 统计条 + 诊断叠加） ---- */}
          <section className="trace-center">
            <div className="trace-panel-head">
              追溯关系总览
              <Tooltip title="以当前焦点对象为中心的可漫游邻域窗口，不一次性呈现全量关系图">
                <InfoCircleOutlined className="trace-head-info" />
              </Tooltip>
            </div>
            <div className="trace-toolbar">
              <span className="trace-toolbar-label">关联层级:</span>
              <Segmented
                size="small"
                value={depth}
                options={[
                  { label: '1层', value: 1 },
                  { label: '2层', value: 2 },
                  { label: '3层', value: 3 },
                ]}
                onChange={(value) => setDepth(Number(value))}
              />
              <span className="trace-toolbar-label">布局方式:</span>
              <Select
                size="small"
                style={{ width: 148 }}
                value={layout}
                options={[
                  { label: '焦点流向布局', value: 'flow' },
                  { label: '类型泳道布局', value: 'swimlane' },
                  { label: '分层布局（待接入）', value: 'layered', disabled: true },
                  { label: '紧凑布局（待接入）', value: 'compact', disabled: true },
                ]}
                onChange={(value) => setLayout(value as LayoutChoice)}
              />
              <span className="trace-toolbar-label">方向:</span>
              <Select
                size="small"
                style={{ width: 92 }}
                value={direction}
                options={Object.entries(DIRECTION_LABELS).map(([value, label]) => ({
                  value,
                  label,
                }))}
                onChange={(value) => setDirection(value as DirectionMode)}
              />
              <span className="trace-toolbar-label">最多显示</span>
              <Select
                size="small"
                style={{ width: 72 }}
                value={limit}
                options={[8, 16, 32, 80].map((v) => ({ value: v, label: `${v}` }))}
                onChange={(value) => setLimit(Number(value))}
              />
              <span className="trace-toolbar-label">
                个节点
                <Tooltip title="每层每方向的节点预算；关联层级、方向和节点上限是所有布局共享的窗口参数">
                  <InfoCircleOutlined className="trace-head-info" />
                </Tooltip>
              </span>
              <Checkbox
                checked={wingFilter === 'business'}
                style={{ marginLeft: 'auto' }}
                onChange={(e) => setWingFilter(e.target.checked ? 'business' : 'all')}
              >
                <Tooltip title="仅保留业务领域知识翼的知识项节点（术语/角色/外部系统/假设/业务规则）；关闭恢复全部">
                  仅业务知识
                </Tooltip>
              </Checkbox>
              <Checkbox
                checked={showInvalid}
                onChange={(e) => setShowInvalid(e.target.checked)}
              >
                显示失效边
              </Checkbox>
            </div>
            <div className="trace-toolbar-hint">
              <span>
                超出范围折叠为摘要节点
                <Tooltip title="窗口外的对象统一用摘要节点表达，点击后预算翻倍展开局部">
                  <InfoCircleOutlined className="trace-head-info" />
                </Tooltip>
              </span>
              <span className="trace-toolbar-hint-right">
                {layout === 'flow' ? '🖱 双击节点：设为新焦点并重定心' : '泳道布局只读：双击不重定心'}
              </span>
            </div>

            {path.length > 0 ? (
              <div className="trace-path">
                <Button
                  disabled={path.length < 2}
                  icon={<ArrowLeftOutlined />}
                  size="small"
                  type="text"
                  onClick={backOneHop}
                />
                {path.map((hop, index) => (
                  <span key={`${hop.nodeType}:${hop.ref}`}>
                    {index > 0 ? <span className="trace-left-path-sep">›</span> : null}
                    <button
                      className={
                        index === path.length - 1
                          ? 'trace-path-pill trace-path-pill--current'
                          : 'trace-path-pill'
                      }
                      type="button"
                      onClick={() => jumpToHop(index)}
                    >
                      {hop.label.length > 16 ? `${hop.label.slice(0, 16)}…` : hop.label}
                    </button>
                  </span>
                ))}
              </div>
            ) : null}

            <div className="trace-canvas-wrap">
              {chainLoading ? <Spin className="trace-canvas-spin" /> : null}
              {graph ? (
                <TraceGraphCanvas
                  graph={graph}
                  allowRecenter={layout === 'flow'}
                  selection={selection}
                  onSelectNode={selectNode}
                  onRecenter={(node) => recenter(node)}
                  onSelectEdge={selectEdge}
                />
              ) : (
                <Empty description="左区选择一个对象进入关系网" style={{ paddingTop: 96 }} />
              )}
            </div>

            <div className="trace-statsbar">
              <span className="trace-statsbar-item">
                <PieChartOutlined style={{ color: 'var(--color-primary)' }} />
                <Tooltip
                  title={(coverage?.directions ?? [])
                    .map(
                      (d) =>
                        `${coverageDirectionLabels[d.key] ?? d.key} ${Math.round(d.ratio * 100)}%（${d.covered}/${d.total}）`,
                    )
                    .join('；')}
                >
                  <span>
                    覆盖度{' '}
                    <b className="trace-statsbar-strong">
                      {headlineCoverage ? `${Math.round(headlineCoverage.ratio * 100)}%` : '—'}
                    </b>
                  </span>
                </Tooltip>
              </span>
              <span className="trace-statsbar-sep">|</span>
              <span className="trace-statsbar-item">
                ◌ 缺口 <b className="trace-statsbar-num trace-statsbar-num--gap">{counts?.gaps ?? '—'}</b>
              </span>
              <span className="trace-statsbar-item">
                <ExclamationCircleOutlined style={{ color: 'var(--color-warning)' }} /> 可疑{' '}
                <b className="trace-statsbar-num trace-statsbar-num--suspect">{counts?.suspect ?? '—'}</b>
              </span>
              <span className="trace-statsbar-item">
                × 冲突{' '}
                {counts?.conflicts_available ? (
                  <b className="trace-statsbar-num">{counts.conflicts}</b>
                ) : (
                  <Tooltip title="待接入：冲突判定规则尚无事实源（AEP-065 延期）">
                    <span className="trace-statsbar-muted">待接入</span>
                  </Tooltip>
                )}
              </span>
              <span className="trace-statsbar-item">
                ○ 窗口外 <b className="trace-statsbar-num">{windowFolded}</b> 项
              </span>
              <Button
                size="small"
                style={{ marginLeft: 'auto' }}
                type={diagnosticsOpen ? 'primary' : 'default'}
                onClick={() => setDiagnosticsOpen((v) => !v)}
              >
                {diagnosticsOpen ? '收起诊断面板 ▴' : '展开诊断面板 ▾'}
              </Button>
            </div>

            {diagnosticsOpen ? (
              <DiagnosticsPanel
                projectId={projectId}
                conflictsAvailable={entry?.counts.conflicts_available ?? false}
                onFocus={(node) => recenter(node)}
                onNavigate={navigateTo}
                onCreateIssue={createIssue}
                onInspectLink={(link) => {
                  setDiagnosticsOpen(false);
                  setSelection({
                    kind: 'edge',
                    edge: {
                      edge_key: `tl:${link.link_ref}`,
                      relation_kind: 'chart_source',
                      origin: 'ldm013',
                      upstream_type: link.upstream_type as TraceNodeType,
                      upstream_ref: link.upstream_ref,
                      downstream_type: link.downstream_type as TraceNodeType,
                      downstream_ref: link.downstream_ref,
                      status: link.status,
                      link_ref: link.link_ref,
                      status_reason: link.status_reason,
                    },
                  });
                  setLinkDetail(link);
                }}
              />
            ) : null}
          </section>

          {/* ---- 右区：详情 + 处置 ---- */}
          <aside className="trace-right">
            <div className="trace-panel-head">详情 + 处置</div>
            <RightPanel
              selection={selection}
              focus={focus}
              nodeLabels={nodeLabels}
              windowEdges={windowEdges}
              linkDetail={linkDetail}
              counts={counts}
              depth={depth}
              direction={direction}
              layout={layout}
              reviewReason={reviewReason}
              fragment={fragment}
              fragmentLoading={fragmentLoading}
              fragmentError={fragmentError}
              hasFragmentTarget={fragmentTarget !== null}
              onReviewReason={setReviewReason}
              onRecenter={(node) => recenter(node)}
              onReview={submitReview}
              onNavigate={navigateTo}
              onCreateIssue={createIssue}
              allowRecenter={layout === 'flow'}
            />
          </aside>
        </div>
      )}
    </WorkbenchFrame>
  );
}

// ---- 中区画布（节点卡片 + SVG 边；坐标由 view-model 决定）----

interface CanvasProps {
  graph: GraphLayoutVM;
  allowRecenter: boolean;
  selection: Selection | null;
  onSelectNode: (node: GraphNodeVM) => void;
  onRecenter: (node: GraphNodeVM) => void;
  onSelectEdge: (edge: TraceEdgeRead) => void;
}

function TraceGraphCanvas({
  graph,
  allowRecenter,
  selection,
  onSelectNode,
  onRecenter,
  onSelectEdge,
}: CanvasProps) {
  // 材料卡片主文本=窗口内来源提取边的锚点引文（选中边优先）；无引文回退原文头 label
  const canvasEdges = graph.edges.map((e) => e.edge);
  const selectedEdgeKey = selection?.kind === 'edge' ? (selection.edge?.edge_key ?? null) : null;
  return (
    <div className="trace-canvas" style={{ minWidth: graph.width, minHeight: graph.height + 32 }}>
      <div className="trace-columns">
        {graph.columns.map((col) => (
          <span className="trace-column-title" key={col.key} style={{ left: col.x, width: NODE_W }}>
            {col.title}
          </span>
        ))}
      </div>
      <svg className="trace-edges" width={graph.width} height={graph.height}>
        {graph.edges.map((e) => {
          const midX = (e.fromX + e.toX) / 2;
          const d = `M ${e.fromX} ${e.fromY} C ${midX} ${e.fromY}, ${midX} ${e.toY}, ${e.toX} ${e.toY}`;
          const isSelected = selection?.kind === 'edge' && selection.edge?.edge_key === e.key;
          return (
            <g key={e.key}>
              {/* stroke 为 var(--trace-*) 令牌引用，SVG 属性不解析 var()，须经 style 应用 */}
              <path
                d={d}
                fill="none"
                style={{ stroke: e.stroke }}
                strokeWidth={isSelected ? 3 : 1.8}
                strokeDasharray={e.dashed ? '6 4' : undefined}
              />
              {/* 加宽透明命中区：单击边 → 右区关系详情 */}
              <path
                d={d}
                fill="none"
                stroke="transparent"
                strokeWidth={12}
                style={{ cursor: 'pointer' }}
                onClick={() => onSelectEdge(e.edge)}
              />
              <polygon
                points={`${e.toX - 7},${e.toY - 4} ${e.toX},${e.toY} ${e.toX - 7},${e.toY + 4}`}
                style={{ fill: e.stroke }}
              />
              {e.levelLabel ? (
                <text x={midX} y={(e.fromY + e.toY) / 2 - 8} className="trace-edge-level">
                  {e.levelLabel}
                </text>
              ) : null}
              {e.marker ? (
                <text
                  x={midX}
                  y={(e.fromY + e.toY) / 2 + (e.levelLabel ? 12 : -6)}
                  className={e.marker === '⚠' ? 'trace-edge-mark trace-edge-mark--warn' : 'trace-edge-mark'}
                >
                  {e.marker === '⚠' ? '⚠可疑边' : e.marker}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {graph.nodes.map((node) => {
        const isSelected = selection?.kind === 'node' && selection.node?.key === node.key;
        const accent = node.isSummary ? 'var(--trace-node-summary)' : nodeTypeMeta[node.nodeType as TraceNodeType].accent;
        if (node.isSummary) {
          const dirClass = node.summaryDirection
            ? ` trace-node--summary-${node.summaryDirection}`
            : '';
          return (
            <button
              className={`trace-node trace-node--summary${dirClass}`}
              key={node.key}
              style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
              title="点击展开局部（预算翻倍重取）"
              type="button"
              onClick={() => onSelectNode(node)}
            >
              <span className="trace-node-summary-label">{node.label}</span>
            </button>
          );
        }
        const status = node.statusLabel;
        const materialQuote =
          node.nodeType === 'material'
            ? materialCardQuote(node.ref, canvasEdges, selectedEdgeKey)
            : null;
        const cardText = materialQuote?.quote ?? node.label;
        return (
          <button
            className={[
              'trace-node',
              node.isFocus ? 'trace-node--focus' : '',
              isSelected ? 'trace-node--selected' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            key={node.key}
            style={{
              left: node.x,
              top: node.y,
              width: NODE_W,
              height: NODE_H,
              // 类型左侧色条（v4 原型：节点卡按类型着色左边），焦点节点保留主色全边框
              ...(node.isFocus ? {} : { borderLeft: `0.1875rem solid ${accent}` }),
            }}
            title={cardText}
            type="button"
            onClick={() => onSelectNode(node)}
            onDoubleClick={() => {
              if (allowRecenter && !node.isSummary) onRecenter(node);
            }}
          >
            <span className="trace-node-head">
              <span className="trace-obj-icon trace-obj-icon--sm" style={{ background: accent }}>
                {NODE_TYPE_ICON[node.nodeType as TraceNodeType]}
              </span>
              <span className="trace-node-kind" style={{ color: accent }}>
                {nodeTypeMeta[node.nodeType as TraceNodeType].label}
              </span>
              {materialQuote && materialQuote.total > 1 ? (
                <span className="trace-node-quote-count">等 {materialQuote.total} 处</span>
              ) : null}
              {status ? (
                <span className={`trace-node-status trace-node-status--${node.statusTone ?? 'neutral'}`}>
                  {status}
                </span>
              ) : null}
            </span>
            <span className="trace-node-label">{cardText}</span>
          </button>
        );
      })}
    </div>
  );
}

// ---- 右区面板 ----

interface RightPanelProps {
  selection: Selection | null;
  focus: TraceNodeRead | null;
  nodeLabels: Map<string, string>;
  /** 窗口内实际绘制的边（材料「原文摘录」清单取值） */
  windowEdges: TraceEdgeRead[];
  linkDetail: TraceLinkRead | null;
  counts: TraceEntryRead['counts'] | null;
  depth: number;
  direction: DirectionMode;
  layout: LayoutChoice;
  reviewReason: string;
  allowRecenter: boolean;
  fragment: AssetFragmentRead | null;
  fragmentLoading: boolean;
  /** 片段拉取失败（与「无可指资产」区分，避免误导文案） */
  fragmentError: boolean;
  /** 当前选中/焦点组合是否解析出片段目标资产 */
  hasFragmentTarget: boolean;
  onReviewReason: (value: string) => void;
  onRecenter: (node: GraphNodeVM) => void;
  onReview: (linkRef: string, conclusion: 'restore' | 'maintain') => void;
  onNavigate: (navTarget: string) => void;
  onCreateIssue: (
    title: string,
    description: string,
    linkRef?: string | null,
    chartRef?: string | null,
  ) => void;
}

function RightPanel({
  selection,
  focus,
  nodeLabels,
  windowEdges,
  linkDetail,
  counts,
  depth,
  direction,
  layout,
  reviewReason,
  allowRecenter,
  fragment,
  fragmentLoading,
  fragmentError,
  hasFragmentTarget,
  onReviewReason,
  onRecenter,
  onReview,
  onNavigate,
  onCreateIssue,
}: RightPanelProps) {
  const legend = (
    <div className="trace-legend-block">
      <Text strong>关系图例</Text>
      <div className="trace-legend">
        {Object.entries(relationKindMeta).map(([key, meta]) => (
          <span className="trace-legend-item" key={key}>
            <i className="trace-legend-line" style={{ borderTopColor: meta.stroke, borderTopStyle: 'solid' }} />
            {meta.label}
          </span>
        ))}
        <span className="trace-legend-item">
          <i
            className="trace-legend-line"
            style={{ borderTopColor: edgeStatusMeta.pre_established.stroke, borderTopStyle: 'dashed' }}
          />
          预建立
        </span>
        <span className="trace-legend-item">
          <i
            className="trace-legend-line"
            style={{ borderTopColor: edgeStatusMeta.suspect_pending_review.stroke, borderTopStyle: 'dashed' }}
          />
          可疑关系 ⚠
        </span>
        <span className="trace-legend-item">
          <i
            className="trace-legend-line"
            style={{ borderTopColor: edgeStatusMeta.invalid.stroke, borderTopStyle: 'dashed' }}
          />
          失效关系 ×
        </span>
        <span className="trace-legend-item">
          <i className="trace-legend-line" style={{ borderTopColor: 'var(--trace-node-summary)', borderTopStyle: 'dotted' }} />
          摘要节点（折叠）
        </span>
      </div>
    </div>
  );

  // 所选对象：优先选中节点，否则当前焦点（原型：右区常驻对象详情）
  const shownNode: { nodeType: TraceNodeType; ref: string; label: string; subLabel: string | null; sourceNote: string | null; statusLabel: string | null; statusTone: BadgeTone | null; fromSelection: boolean; vm?: GraphNodeVM } | null =
    selection?.kind === 'node' && selection.node && !selection.node.isSummary
      ? {
          nodeType: selection.node.nodeType as TraceNodeType,
          ref: selection.node.ref,
          label: selection.node.label,
          subLabel: selection.node.subLabel,
          sourceNote: selection.node.sourceNote,
          statusLabel: selection.node.statusLabel,
          statusTone: selection.node.statusTone,
          fromSelection: true,
          vm: selection.node,
        }
      : focus
        ? (() => {
            const meta = nodeStatusMeta(focus.node_type, focus.status);
            return {
              nodeType: focus.node_type,
              ref: focus.ref,
              label: focus.label,
              subLabel: focus.sub_label ?? null,
              sourceNote: focus.source_note ?? null,
              statusLabel: meta?.label ?? null,
              statusTone: meta?.tone ?? null,
              fromSelection: false,
            };
          })()
        : null;

  // 材料节点详情：窗口内逐知识项「原文摘录」（锚点引文清单）
  const excerpts =
    shownNode?.nodeType === 'material' ? materialExcerpts(shownNode.ref, windowEdges) : [];

  const edge = selection?.kind === 'edge' ? selection.edge : null;
  // 复核/处置后以 AEP-061 详情为准（selection.edge 是选中时的快照）
  const effectiveStatus = edge ? ((linkDetail?.status ?? edge.status) as TraceEdgeRead['status']) : null;
  const statusMeta = effectiveStatus ? edgeStatusMeta[effectiveStatus] : null;
  const suspect = effectiveStatus === 'suspect_pending_review';

  return (
    <div className="trace-right-body">
      {shownNode ? (
        <div className="trace-right-section">
          <span className="trace-left-caption">所选对象</span>
          <div className="trace-selected-card">
            <span
              className="trace-obj-icon trace-obj-icon--lg"
              style={{ background: nodeTypeMeta[shownNode.nodeType].accent }}
            >
              {NODE_TYPE_ICON[shownNode.nodeType]}
            </span>
            <span className="trace-obj-main">
              <span className="trace-obj-title trace-obj-title--lg">{shownNode.label}</span>
              <span className="trace-obj-sub">
                {nodeTypeMeta[shownNode.nodeType].label}
                {shownNode.subLabel ? ` · ${subLabelText(shownNode.subLabel)}` : ''}
              </span>
            </span>
          </div>
          <Descriptions colon column={1} size="small">
            <Descriptions.Item label="类型">
              {nodeTypeMeta[shownNode.nodeType].label}
              {shownNode.subLabel ? ` · ${subLabelText(shownNode.subLabel)}` : ''}
            </Descriptions.Item>
            {shownNode.statusLabel ? (
              <Descriptions.Item label="状态">
                <Tag color={TONE_COLOR[shownNode.statusTone ?? 'neutral']}>{shownNode.statusLabel}</Tag>
              </Descriptions.Item>
            ) : null}
            {!shownNode.fromSelection ? (
              <Descriptions.Item label="角色">当前焦点对象（关系网以它为中心）</Descriptions.Item>
            ) : null}
          </Descriptions>
          {shownNode.nodeType === 'material' ? (
            <div className="trace-material-detail" data-testid="material-detail">
              <span className="trace-left-caption">原文摘录</span>
              {excerpts.length > 0 ? (
                excerpts.map((ex) => (
                  <div className="trace-excerpt" key={ex.edgeKey}>
                    <Text className="trace-excerpt__element" type="secondary">
                      {nodeLabels.get(nodeKeyOf('element', ex.elementRef)) ?? '知识项'}
                    </Text>
                    {ex.quotes.map((q, i) => (
                      <blockquote className="trace-excerpt__quote" key={i}>
                        {q}
                      </blockquote>
                    ))}
                  </div>
                ))
              ) : (
                <Text type="secondary">窗口内知识项暂无来源锚点（卡片回退原文头）</Text>
              )}
              <span className="trace-left-caption">来源说明</span>
              <Text type="secondary">{shownNode.sourceNote || '—'}</Text>
            </div>
          ) : null}
          {shownNode.fromSelection && shownNode.vm ? (
            allowRecenter ? (
              <Button size="small" type="primary" onClick={() => onRecenter(shownNode.vm!)}>
                设为焦点
              </Button>
            ) : (
              <Text type="secondary">泳道布局只读；切回焦点流向后可设为焦点</Text>
            )
          ) : null}
        </div>
      ) : null}

      {edge && statusMeta ? (
        <div className="trace-right-section">
          <span className="trace-left-caption">关系详情</span>
          <Descriptions colon column={1} size="small">
            <Descriptions.Item label="关系">
              {(
                linkDetail?.upstream_label ??
                nodeLabels.get(nodeKeyOf(edge.upstream_type, edge.upstream_ref)) ??
                edge.upstream_ref
              ).slice(0, 18)}{' '}
              →{' '}
              {(
                linkDetail?.downstream_label ??
                nodeLabels.get(nodeKeyOf(edge.downstream_type, edge.downstream_ref)) ??
                edge.downstream_ref
              ).slice(0, 18)}
            </Descriptions.Item>
            <Descriptions.Item label="关系类型">
              {relationKindLabels[edge.relation_kind] ?? edge.relation_kind}
            </Descriptions.Item>
            <Descriptions.Item label="窗口范围">
              {depth}层{DIRECTION_LABELS[direction]}
            </Descriptions.Item>
            <Descriptions.Item label="布局方式">{LAYOUT_LABELS[layout]}</Descriptions.Item>
            <Descriptions.Item label="诊断">
              <Tag color={TONE_COLOR[statusMeta.tone]}>
                {statusMeta.marker ? `${statusMeta.marker} ` : ''}
                {statusMeta.label}
              </Tag>
            </Descriptions.Item>
            {edge.origin === 'derived' ? (
              <Descriptions.Item label="说明">
                结构派生关系：随权威来源字段/当前文档索引成立，无独立复核动作
              </Descriptions.Item>
            ) : null}
            {edge.relation_kind === 'material_element' ? (
              <Descriptions.Item label="锚点片段">
                {(edge.anchor_quotes?.length ?? 0) > 0 ? (
                  <div className="trace-excerpt" data-testid="edge-anchor-quotes">
                    {(edge.anchor_quotes ?? []).map((q, i) => (
                      <blockquote className="trace-excerpt__quote trace-excerpt__quote--full" key={i}>
                        {q}
                      </blockquote>
                    ))}
                  </div>
                ) : (
                  <Text type="secondary">该知识项暂无来源锚点（卡片回退原文头）</Text>
                )}
              </Descriptions.Item>
            ) : null}
            {effectiveStatus === 'pre_established' ? (
              <Descriptions.Item label="边界">预建立不作为正式追溯依据</Descriptions.Item>
            ) : null}
            {linkDetail ? (
              <>
                <Descriptions.Item label="初始依据">{linkDetail.initial_basis || '—'}</Descriptions.Item>
                {linkDetail.established_at ? (
                  <Descriptions.Item label="正式确立">
                    {formatAbsoluteTime(linkDetail.established_at)}
                  </Descriptions.Item>
                ) : null}
                {linkDetail.status_reason ? (
                  <Descriptions.Item label="状态原因">{linkDetail.status_reason}</Descriptions.Item>
                ) : null}
                {linkDetail.issue_ref ? (
                  <Descriptions.Item label="关联问题项">
                    <Tag color="purple">有</Tag>
                  </Descriptions.Item>
                ) : null}
              </>
            ) : null}
          </Descriptions>
        </div>
      ) : null}

      {edge && edge.origin === 'ldm013' && edge.link_ref ? (
        <div className="trace-right-section">
          <span className="trace-left-caption">处置操作</span>
          <Space orientation="vertical" style={{ width: '100%' }}>
            {suspect ? (
              <Input.TextArea
                placeholder="复核依据（恢复/维持均留痕）"
                rows={2}
                value={reviewReason}
                onChange={(e) => onReviewReason(e.target.value)}
              />
            ) : null}
            <Space wrap>
              {suspect ? (
                <>
                  <Tooltip title="覆盖对象仍成立时恢复为预建立；须重走图表核对确认">
                    <Button size="small" type="primary" onClick={() => onReview(edge.link_ref!, 'restore')}>
                      复核·恢复
                    </Button>
                  </Tooltip>
                  <Button size="small" onClick={() => onReview(edge.link_ref!, 'maintain')}>
                    复核·维持可疑
                  </Button>
                </>
              ) : null}
              <Button size="small" onClick={() => onNavigate('diagram_workbench')}>
                补全
              </Button>
              <Button
                danger
                size="small"
                onClick={() =>
                  onCreateIssue(
                    `追溯诊断：${relationKindLabels[edge.relation_kind] ?? edge.relation_kind}关系${statusMeta?.label ?? ''}`,
                    edge.status_reason ?? '',
                    edge.link_ref,
                    edge.downstream_type === 'chart' ? edge.downstream_ref : null,
                  )
                }
              >
                转问题项
              </Button>
            </Space>
          </Space>
        </div>
      ) : null}

      {shownNode?.nodeType === 'document' && !edge && !fragmentLoading && !fragment ? (
        fragmentError ? (
          <div className="trace-right-section" data-testid="fragment-doc-error">
            <span className="trace-left-caption">文档片段预览</span>
            <Text type="secondary">片段加载失败。请重新选择文档节点重试，或检查文档发布状态。</Text>
          </div>
        ) : !hasFragmentTarget ? (
          <div className="trace-right-section" data-testid="fragment-doc-guide">
            <span className="trace-left-caption">文档片段预览</span>
            <Text type="secondary">
              该节点是文档本体。当前焦点非需求条目/图表，无可指资产；选择上游资产节点（需求条目 /
              图表）、文档承接边，或以条目/图表为焦点后再点文档节点，可预览资产在文档中的片段。
            </Text>
          </div>
        ) : null
      ) : null}

      {fragmentLoading || fragment ? (
        <div className="trace-right-section" data-testid="fragment-preview">
          <span className="trace-left-caption">文档片段预览</span>
          {fragmentLoading ? (
            <Text type="secondary">片段加载中…</Text>
          ) : fragment ? (
            <FragmentPreviewCard read={fragment} />
          ) : null}
        </div>
      ) : null}

      {legend}

      {counts ? (
        <Paragraph className="trace-right-hint" type="secondary">
          单击节点看详情，双击重定心漫游；单击边看关系与处置。缺口 {counts.gaps} 项、可疑{' '}
          {counts.suspect} 条可在「诊断面板」集中查看。
        </Paragraph>
      ) : null}
    </div>
  );
}

// ---- 资产 → 文档片段预览（04A §8 增补：预览 = Markdown 定稿片段，docx 同源派生）----

type FragmentSegment =
  | { kind: 'text'; content: string }
  | { kind: 'fence'; lang: string; content: string };

function splitFencedSegments(markdown: string): FragmentSegment[] {
  const segments: FragmentSegment[] = [];
  let fenceLang: string | null = null;
  let buffer: string[] = [];
  const flush = () => {
    if (buffer.length === 0) return;
    segments.push(
      fenceLang !== null
        ? { kind: 'fence', lang: fenceLang, content: buffer.join('\n') }
        : { kind: 'text', content: buffer.join('\n') },
    );
    buffer = [];
  };
  for (const line of markdown.split('\n')) {
    if (line.trim().startsWith('```')) {
      flush();
      fenceLang = fenceLang === null ? line.trim().slice(3).trim() : null;
      continue;
    }
    buffer.push(line);
  }
  flush();
  return segments;
}

function FragmentPreviewCard({ read }: { read: AssetFragmentRead }) {
  const vm = buildFragmentPreview(read);
  return (
    <div className="trace-fragment">
      <Space size={6} wrap>
        <Tag color={TONE_COLOR[vm.statusTone]}>{vm.statusText}</Tag>
        {vm.baselineText ? <Tag color="purple">{vm.baselineText}</Tag> : null}
      </Space>
      {vm.contextText ? (
        <div className="trace-fragment__context">
          <Text type="secondary">{vm.contextText}</Text>
        </div>
      ) : null}
      {vm.staleText ? <Alert type="warning" showIcon title={vm.staleText} /> : null}
      {vm.emptyText ? (
        <Text type="secondary">{vm.emptyText}</Text>
      ) : (
        vm.fragments.map((frag) => (
          <div className="trace-fragment__card" key={`${frag.section_key}-${frag.start_line}`}>
            <div className="trace-fragment__section">
              <Text strong>
                §{frag.section_number} {frag.section_title}
              </Text>
              <Text type="secondary" className="trace-fragment__lines">
                第 {frag.start_line + 1}–{frag.end_line + 1} 行
              </Text>
            </div>
            {splitFencedSegments(frag.markdown).map((seg, index) =>
              seg.kind === 'fence' && seg.lang === 'mermaid' ? (
                <MermaidPreview
                  key={index}
                  chartRef={read.asset_ref}
                  version={read.draft_version ?? 0}
                  code={seg.content}
                />
              ) : seg.kind === 'fence' && seg.lang === 'plantuml' ? (
                <PlantumlPreview key={index} code={seg.content} />
              ) : seg.kind === 'fence' ? (
                <pre className="trace-fragment__code" key={index}>
                  <code>{seg.content}</code>
                </pre>
              ) : (
                // 内容经 renderMarkdownHtml 全量转义后再注入（标题/加粗/属性表安全渲染）
                <div
                  className="trace-fragment__text"
                  key={index}
                  dangerouslySetInnerHTML={{ __html: renderMarkdownHtml(seg.content) }}
                />
              ),
            )}
          </div>
        ))
      )}
      <Paragraph className="trace-fragment__footnote" type="secondary">
        预览内容为 Markdown 定稿片段；发布 docx 由该定稿派生、二者同源。追溯依据不写入 docx
        正文——资产与文档的追溯绑定在索引保存/定稿时由系统自动建立。
      </Paragraph>
    </div>
  );
}

// ---- 诊断叠加面板（次要、可切换；AEP-062/063/064，冲突待接入）----

interface DiagnosticsPanelProps {
  projectId: string;
  conflictsAvailable: boolean;
  onFocus: (node: TraceNodeRead) => void;
  onNavigate: (navTarget: string) => void;
  onCreateIssue: (title: string, description: string) => void;
  onInspectLink: (link: TraceLinkRead) => void;
}

function DiagnosticsPanel({
  projectId,
  conflictsAvailable,
  onFocus,
  onNavigate,
  onCreateIssue,
  onInspectLink,
}: DiagnosticsPanelProps) {
  const [coverage, setCoverage] = useState<TraceCoverageRead | null>(null);
  const [gaps, setGaps] = useState<TraceGapListRead | null>(null);
  const [gapKind, setGapKind] = useState<TraceGapKind | 'all'>('all');
  const [suspects, setSuspects] = useState<TraceSuspectListRead | null>(null);
  const [includeInvalid, setIncludeInvalid] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      traceApi.coverage(projectId),
      traceApi.gaps(projectId, gapKind === 'all' ? undefined : gapKind),
      traceApi.suspects(projectId, includeInvalid),
    ])
      .then(([cov, gapList, suspectList]) => {
        if (cancelled) return;
        setCoverage(cov);
        setGaps(gapList);
        setSuspects(suspectList);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, gapKind, includeInvalid]);

  return (
    <div className="trace-diagnostics">
      {error ? <Alert title={error} showIcon type="error" style={{ marginBottom: 8 }} /> : null}
      <Tabs
        size="small"
        items={[
          {
            key: 'coverage',
            label: '覆盖度',
            children: (
              <div className="trace-coverage">
                {(coverage?.directions ?? []).map((d) => (
                  <div className="trace-coverage-item" key={d.key}>
                    <Text>{coverageDirectionLabels[d.key] ?? d.key}</Text>
                    <Progress
                      percent={Math.round(d.ratio * 100)}
                      size="small"
                      status={d.ratio >= 1 ? 'success' : 'active'}
                    />
                    <Text type="secondary">
                      {d.covered}/{d.total}
                    </Text>
                  </div>
                ))}
                <Text type="secondary">预建立不计入「条目 → 图表」覆盖（不作为正式追溯依据）</Text>
              </div>
            ),
          },
          {
            key: 'gaps',
            label: `缺口/孤儿（${gaps?.total ?? 0}）`,
            children: (
              <>
                <Segmented
                  size="small"
                  style={{ marginBottom: 8 }}
                  value={gapKind}
                  options={[
                    { label: '全部', value: 'all' },
                    ...Object.entries(gapKindMeta).map(([value, meta]) => ({
                      label: meta.label,
                      value,
                    })),
                  ]}
                  onChange={(value) => setGapKind(value as TraceGapKind | 'all')}
                />
                <Table<TraceGapItemRead>
                  rowKey={(g) => `${g.kind}:${g.node_ref}`}
                  size="small"
                  locale={{ emptyText: '无缺口' }}
                  pagination={{ pageSize: 8, hideOnSinglePage: true }}
                  dataSource={gaps?.items ?? []}
                  columns={[
                    {
                      title: '类别',
                      dataIndex: 'kind',
                      width: 140,
                      render: (kind: TraceGapKind) => <Tag color="volcano">{gapKindMeta[kind].label}</Tag>,
                    },
                    { title: '对象', dataIndex: 'label' },
                    { title: '说明', dataIndex: 'detail' },
                    {
                      title: '处置',
                      width: 260,
                      render: (_, gap) => (
                        <Space wrap>
                          <Button
                            size="small"
                            onClick={() =>
                              onFocus({ node_type: gap.node_type, ref: gap.node_ref, label: gap.label })
                            }
                          >
                            以此为焦点
                          </Button>
                          <Button size="small" onClick={() => onNavigate(gap.nav_target)}>
                            补全导航
                          </Button>
                          <Button
                            danger
                            size="small"
                            onClick={() => onCreateIssue(`追溯缺口：${gap.label}`, gap.detail)}
                          >
                            转问题项
                          </Button>
                        </Space>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: 'suspects',
            label: `可疑链路（${suspects?.total ?? 0}）`,
            children: (
              <>
                <Checkbox
                  checked={includeInvalid}
                  style={{ marginBottom: 8 }}
                  onChange={(e) => setIncludeInvalid(e.target.checked)}
                >
                  并列失效项
                </Checkbox>
                <Table<TraceLinkRead>
                  rowKey="link_ref"
                  size="small"
                  locale={{ emptyText: '无可疑链路' }}
                  pagination={{ pageSize: 8, hideOnSinglePage: true }}
                  dataSource={suspects?.items ?? []}
                  columns={[
                    { title: '上游', dataIndex: 'upstream_label', render: (v, l) => v ?? l.upstream_ref },
                    { title: '下游', dataIndex: 'downstream_label', render: (v, l) => v ?? l.downstream_ref },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 120,
                      render: (status: string) => {
                        const meta = linkStatusMeta(status);
                        return <Tag color={TONE_COLOR[meta.tone]}>{meta.label}</Tag>;
                      },
                    },
                    { title: '原因', dataIndex: 'status_reason', render: (v) => v ?? '—' },
                    {
                      title: '处置',
                      width: 140,
                      render: (_, link) => (
                        <Button size="small" onClick={() => onInspectLink(link)}>
                          查看与复核
                        </Button>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: 'conflicts',
            label: '冲突',
            children: (
              <Empty
                description={
                  conflictsAvailable
                    ? '暂无冲突'
                    : '待接入：冲突判定规则尚无事实源（AEP-065 延期，本版不显示虚构数据）'
                }
              />
            ),
          },
        ]}
      />
    </div>
  );
}
