import {
  Alert,
  Button,
  Drawer,
  Empty,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { MermaidPreview } from '../ui/mermaid';
import { WorkbenchFrame } from './WorkbenchFrame';
import { useAgentRunWatcher, type RunPollTick } from '../hooks/useAgentRunWatcher';
import { chartsApi } from '../api/charts';
import type {
  ChartBusinessSourceRead,
  ChartEligibleSourceRead,
  ChartFindingRead,
  ChartFormat,
  ChartListRead,
  ChartSuggestionHandling,
  ChartSuggestionRead,
  ChartType,
  ChartWorkspaceRead,
} from '../api/charts';
import { elementTypeMeta } from '../view-models/requirement-analysis';
import type { ProjectRead } from '../api/projects';
import type { SearchTarget } from '../view-models/search';
import {
  buildChartRows,
  buildChartWorkspaceVM,
  buildTraceLinkRows,
  chartFormatLabels,
  chartTypeLabels,
  findingTypeMeta,
  parseMarkdownTable,
  suggestionStatusLabels,
  typeFormatOptions,
} from '../view-models/diagram';
import { priorityText, requirementItemTypeText } from '../view-models/requirement-item-formation';
import type { RequirementItemType } from '../api/item-formation';
import type { BadgeTone } from '../view-models/common';
import {
  createWorkbenchHandoff,
  type WorkbenchHandoff,
} from '../view-models/workbench-handoff';

const { Text, Paragraph } = Typography;

const TONE_COLOR: Record<BadgeTone, string> = {
  success: 'green',
  processing: 'blue',
  warning: 'orange',
  danger: 'red',
  neutral: 'default',
};

function newKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

interface DiagramWorkbenchProps {
  selectedProject: ProjectRead | null;
  operatorRef: string;
  /** 全局检索深链（P4，05 §4）：命中图表（entityType=chart）时携目标进入，list 载入后一次性 openChart。 */
  searchTarget?: SearchTarget | null;
  workbenchHandoff?: WorkbenchHandoff | null;
  onWorkbenchHandoff?: (handoff: WorkbenchHandoff) => void;
  onConsumeWorkbenchHandoff?: (token: number) => void;
}

function MarkdownTablePreview({ code }: { code: string }) {
  const cells = useMemo(() => parseMarkdownTable(code), [code]);
  if (!cells) {
    return <Empty description="尚无可预览的受控表格" />;
  }
  const [header, ...rows] = cells;
  return (
    <Table
      bordered
      size="small"
      pagination={false}
      columns={header.map((title, idx) => ({ title, dataIndex: `c${idx}`, key: `c${idx}` }))}
      dataSource={rows.map((row, ri) => {
        const record: Record<string, string> = { key: `r${ri}` };
        row.forEach((cell, ci) => {
          record[`c${ci}`] = cell;
        });
        return record;
      })}
    />
  );
}

function ChartPreview({ ws }: { ws: ChartWorkspaceRead }) {
  if (ws.format === 'mermaid') {
    return <MermaidPreview chartRef={ws.chart_ref} version={ws.draft_version} code={ws.source_code} />;
  }
  if (ws.format === 'markdown_table') {
    return <MarkdownTablePreview code={ws.source_code} />;
  }
  return (
    <Alert
      title="PlantUML 源码已通过受控校验，当前不支持实时预览"
      description="是否允许无预览进入核对由图表类型裁定；可继续编辑或直接发起核对。"
      showIcon
      type="info"
    />
  );
}

// ---- 右区·设计页：AI 对话时间线（复用需求管理工作台 az5 会话形态） ----
// 每条建议请求 = 一轮对话：用户意图气泡 + AI 回应（生成中 / 建议卡 / 停靠原因卡）。
// 建议是模型推理结果，经采纳 / 修订采纳才更新图表；停靠结局必须可见，不静默。

function SuggestionAiCard({
  suggestion,
  actionable,
  initial = false,
  onAdopt,
  onRevise,
  onReject,
}: {
  suggestion: ChartSuggestionRead;
  actionable: boolean;
  initial?: boolean;
  onAdopt: () => void;
  onRevise: () => void;
  onReject: () => void;
}) {
  const pending = suggestion.process_status === 'pending';
  const settledLabel =
    initial && suggestion.process_status === 'adopted'
      ? '初稿已应用'
      : suggestionStatusLabels[suggestion.process_status] ?? suggestion.process_status;
  return (
    <div className="az5-msg az5-msg--ai" aria-label="AI 源码建议">
      <span className="az5-ava az5-ava--ai">AI</span>
      <div className="az5-msg__body">
        <span className="az5-who">{initial ? 'AI 图表初稿' : 'AI 源码建议'}</span>
        <div className="az5-card">
          <div className="az5-card__hd">
            <b>{suggestion.explanation || (initial ? 'AI 图表初稿' : 'AI 源码建议')}</b>
            <Tag color="purple">模型推理结果</Tag>
            {pending ? (
              <Tag color="orange">待采纳 · 未生效</Tag>
            ) : (
              <Tag color={initial && suggestion.process_status === 'adopted' ? 'green' : undefined}>
                {settledLabel}
              </Tag>
            )}
          </div>
          <div className="az5-card__bd">
            <Paragraph code copyable ellipsis={{ rows: 5, expandable: true }} className="dw-suggestion-code">
              {suggestion.source_code}
            </Paragraph>
          </div>
          <div className="az5-card__ft">
            {pending && actionable ? (
              <>
                <button className="az5-btn az5-btn--primary" type="button" onClick={onAdopt}>
                  采纳
                </button>
                <button className="az5-btn" type="button" onClick={onRevise}>
                  修订采纳
                </button>
                <button className="az5-btn" type="button" onClick={onReject}>
                  拒绝（理由必填）
                </button>
              </>
            ) : (
              <span className="az5-card__note">
                {pending
                  ? '当前状态下不可处置该建议。'
                  : initial && suggestion.process_status === 'adopted'
                    ? '初稿已应用为当前草稿；可继续对话修订或人工编辑。'
                    : '本轮建议已处置，留档不删除。'}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function SuggestionStoppedCard({ reason }: { reason: string | null | undefined }) {
  return (
    <div className="az5-msg az5-msg--ai" aria-label="AI 未生成建议">
      <span className="az5-ava az5-ava--ai">AI</span>
      <div className="az5-msg__body">
        <span className="az5-who">AI 回复 · 未生成建议</span>
        <div className="az5-card">
          <div className="az5-card__hd">
            <b>本次未生成候选建议</b>
            <Tag color="orange">失败停靠</Tag>
          </div>
          <div className="az5-card__bd">
            <p>{reason || 'AI 建议生成失败；可重试请求或继续人工编辑，不伪造候选建议。'}</p>
          </div>
          <div className="az5-card__ft">
            <span className="az5-card__note">可调整意图后重新发送，或继续人工编辑源码。</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- 主工作台 ----

export function DiagramWorkbench({
  selectedProject,
  operatorRef,
  searchTarget,
  workbenchHandoff,
  onWorkbenchHandoff,
  onConsumeWorkbenchHandoff,
}: DiagramWorkbenchProps) {
  const projectId = selectedProject?.id;
  const [list, setList] = useState<ChartListRead | null>(null);
  // 检索深链一次性消费守卫（范式同 resumeConsumedRef）：每个 token 只 openChart 一次（含 StrictMode）。
  const searchConsumedRef = useRef<number | null>(null);
  const handoffConsumedRef = useRef<number | null>(null);
  const [workspace, setWorkspace] = useState<ChartWorkspaceRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 右区页签：随状态给默认页（草稿=设计，待确认/已确认=核对）
  const [sideTab, setSideTab] = useState<'design' | 'verify' | 'source'>('design');
  const [caret, setCaret] = useState<{ line: number; col: number }>({ line: 1, col: 1 });

  // 渲染预览画布视图：缩放 + 平移偏移（合并为单一状态，保证滚轮缩放的函数式更新一致）
  // 平移用 translate 实现「自由拖动漫游」——无论内容是否溢出都能抓取移动（不依赖滚动条）
  const [view, setView] = useState<{ zoom: number; x: number; y: number }>({ zoom: 1, x: 0, y: 0 });
  const zoom = view.zoom; // 兼容底部状态条等只读展示
  const renderRef = useRef<HTMLDivElement | null>(null);
  const panStart = useRef<{ mx: number; my: number; x: number; y: number } | null>(null);
  const [panning, setPanning] = useState(false);

  // 创建向导（主题不再人工填写：初稿生成结果以语义标题回填）
  const [createOpen, setCreateOpen] = useState(false);
  const [eligibleSources, setEligibleSources] = useState<ChartEligibleSourceRead[]>([]);
  const [createType, setCreateType] = useState<ChartType>('flowchart');
  const [createFormat, setCreateFormat] = useState<ChartFormat>('mermaid');
  const [createSources, setCreateSources] = useState<string[]>([]);
  // P4 06 B：图表来源两翼分组（需求条目 REQUIREMENT_ITEM / 业务知识 SUPPORTING_CONTENT）。
  // 一张图表单一来源翼（后端 source_kind 单值）；切换翼即清空已选。
  const [businessSources, setBusinessSources] = useState<ChartBusinessSourceRead[]>([]);
  const [createWing, setCreateWing] = useState<'requirement' | 'business'>('requirement');

  // 源码编辑（来源集合可在草稿中调整；变化随应用源码变更提交并触发预建立追溯同步）
  const [sourceDraft, setSourceDraft] = useState('');
  const [selectedSourceRefs, setSelectedSourceRefs] = useState<string[]>([]);
  const [editSourceOptions, setEditSourceOptions] = useState<ChartEligibleSourceRead[]>([]);
  const [addSourceOpen, setAddSourceOpen] = useState(false);
  const [addSourceRefs, setAddSourceRefs] = useState<string[]>([]);

  // AI 对话（区4 设计页：建议请求 = 对话轮次，送检中/建议/停靠随工作区读视图呈现）
  const [suggestIntent, setSuggestIntent] = useState('');
  const [reviseTarget, setReviseTarget] = useState<{ ref: string; source: string } | null>(null);
  const [rejectTarget, setRejectTarget] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const chatThreadRef = useRef<HTMLDivElement | null>(null);

  // 核对与复核
  const [verifyPolling, setVerifyPolling] = useState(false);
  const [decisionReject, setDecisionReject] = useState<ChartFindingRead | null>(null);
  const [decisionReason, setDecisionReason] = useState('');
  const [lifecycleModal, setLifecycleModal] = useState<'return' | 'void' | null>(null);
  const [lifecycleReason, setLifecycleReason] = useState('');

  const vm = workspace ? buildChartWorkspaceVM(workspace) : null;

  const refreshList = useCallback(async () => {
    if (!projectId) return;
    try {
      setList(await chartsApi.list(projectId));
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, [projectId]);

  const openChart = useCallback(
    async (chartRef: string) => {
      if (!projectId) return;
      try {
        const ws = await chartsApi.read(projectId, chartRef);
        setWorkspace(ws);
        setSourceDraft(ws.source_code);
        setSelectedSourceRefs(ws.sources.map((s) => s.item_ref));
        if (ws.status === 'draft') {
          const eligible = await chartsApi.eligibleSources(projectId).catch(() => null);
          setEligibleSourceOptions(eligible?.sources ?? ws.sources, ws.sources);
        } else {
          setEditSourceOptions(ws.sources);
        }
        setLoadError(null);
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : String(error));
      }

      function setEligibleSourceOptions(
        eligible: ChartEligibleSourceRead[],
        current: ChartEligibleSourceRead[],
      ) {
        // 候选池可能不含已选来源（如条目状态变化），合并去重保证 chips 可回显
        const merged = new Map<string, ChartEligibleSourceRead>();
        for (const s of [...eligible, ...current]) merged.set(s.item_ref, s);
        setEditSourceOptions([...merged.values()]);
      }
    },
    [projectId],
  );

  useEffect(() => {
    setWorkspace(null);
    setList(null);
    void refreshList();
  }, [refreshList]);

  // 检索深链（05 §4）：命中图表 → 切项目后本台 list 载入完成再 openChart（gate on list!==null 避免被
  // 上方 list 载入 effect 的 setWorkspace(null) 清掉）。token+projectId 双守卫，一次性消费。
  useEffect(() => {
    if (
      !searchTarget ||
      searchTarget.entityType !== 'chart' ||
      searchTarget.projectId !== projectId ||
      list === null ||
      searchConsumedRef.current === searchTarget.token
    ) {
      return;
    }
    searchConsumedRef.current = searchTarget.token;
    void openChart(searchTarget.ref);
  }, [searchTarget, projectId, list, openChart]);

  // 右区默认页随图表与状态切换（时点不混帧：草稿看设计页，待确认/已确认看核对页）
  const wsRef = workspace?.chart_ref ?? null;
  const wsStatus = workspace?.status ?? null;
  useEffect(() => {
    if (!wsStatus) return;
    setSideTab(wsStatus === 'pending_confirmation' || wsStatus === 'confirmed' ? 'verify' : 'design');
    setView({ zoom: 1, x: 0, y: 0 });
  }, [wsRef, wsStatus]);

  // AI 建议 / 核对为异步（inline 时同步落库）：短轮询工作区直至收束（P0 收编为统一 hook 的纯轮询，
  // 原独立短轮询 setTimeout 退役）。建议在途 = 时间线存在 generating 轮次；建议登记 / 失败停靠都会让
  // 该轮次收束，轮询随之停止。核对轮询以 verifyPolling 为闸。闸门（建议 generating 或核对 verifying）
  // 由外部状态变化转为不成立时（切图、decideFinding 改动 workspace 等），下方 effect 同步 stop() 收束
  // 本轮，不留多跑一拍的电平差（旧效果驱动轮询即以 workspace 为 dep 电平触发，切图即清定时器）。
  const suggestGenerating = workspace?.suggestion_thread.some((e) => e.status === 'generating') ?? false;
  const runningVerification =
    (verifyPolling && workspace?.verification?.processing_status === 'verifying') ?? false;

  // hook 持有的 poll 闭包在 start 时定格，须经 ref 取最新工作区/核对开关（否则比对/收束读到定格值）
  const workspaceLatestRef = useRef(workspace);
  workspaceLatestRef.current = workspace;
  const verifyPollingRef = useRef(verifyPolling);
  verifyPollingRef.current = verifyPolling;

  const { watching: chartWatching, start: startChartWatch, stop: stopChartWatch } = useAgentRunWatcher({ intervalMs: 800 });

  const pollChart = useCallback(async (): Promise<RunPollTick> => {
    const current = workspaceLatestRef.current;
    if (!projectId || !current) return { done: true };
    // 读失败即停（原效果驱动轮询：读失败不 setState → 不再触发下一拍），保字节一致
    const ws = await chartsApi.read(projectId, current.chart_ref).catch(() => null);
    if (!ws) return { done: true };
    setWorkspace(ws);
    // 初稿自动应用等服务端写入：仅当用户未分叉编辑时跟进源码，避免覆盖手工输入
    if (ws.source_code !== current.source_code) {
      setSourceDraft((draft) => (draft === current.source_code ? ws.source_code : draft)); // 语义标题/版本随初稿回填，同步列表
      void refreshList();
    }
    const stillVerifying = ws.verification?.processing_status === 'verifying';
    if (ws.verification && !stillVerifying) setVerifyPolling(false);
    const stillGenerating = ws.suggestion_thread.some((e) => e.status === 'generating');
    return { done: !stillGenerating && !(verifyPollingRef.current && stillVerifying) };
  }, [projectId, refreshList]);

  // 建议/核对在途且尚无在途 watch 时启动一次（start 抢占旧 watch；watching 守卫防每拍重启；
  // 首拍延后一个间隔＝原 setTimeout 800ms 语义）。闸门转为不成立（无 generating 且非 verifying）而
  // watch 仍在途时同步 stop()：恢复旧电平触发语义，避免闸门经「循环自身读」以外的路径（切图、
  // decideFinding 改 workspace）达成时已排程的那一拍多跑一次 chartsApi.read（裁定 F3）。
  useEffect(() => {
    const gateActive = suggestGenerating || runningVerification;
    if (gateActive && !chartWatching) {
      startChartWatch(pollChart);
    } else if (!gateActive && chartWatching) {
      stopChartWatch();
    }
  }, [suggestGenerating, runningVerification, chartWatching, startChartWatch, stopChartWatch, pollChart]);

  // 对话时间线滚到底部（新轮次 / 结果承接 / 切回设计页时）
  const threadLength = workspace?.suggestion_thread.length ?? 0;
  useEffect(() => {
    const el = chatThreadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [threadLength, suggestGenerating, sideTab]);

  const applyWorkspace = useCallback((ws: ChartWorkspaceRead) => {
    setWorkspace(ws);
    setSourceDraft(ws.source_code);
    setSelectedSourceRefs(ws.sources.map((s) => s.item_ref));
    if (ws.validation_errors.length > 0) {
      message.warning('源码未通过受控校验，草稿保持不变');
    }
  }, []);

  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, []);

  // ---- 创建向导 ----

  const openCreate = useCallback((sourceItemRef?: string) =>
    run(async () => {
      if (!projectId) return;
      const read = await chartsApi.eligibleSources(projectId);
      setEligibleSources(read.sources);
      setBusinessSources(read.business_sources ?? []);
      setCreateType('flowchart');
      setCreateFormat('mermaid');
      const preselected = sourceItemRef && read.sources.some((source) => source.item_ref === sourceItemRef)
        ? [sourceItemRef]
        : [];
      setCreateSources(preselected);
      setCreateWing('requirement');
      setCreateOpen(true);
      if (sourceItemRef && preselected.length === 0) {
        message.warning('当前需求条目不在确认态图表来源候选中，请返回需求管理检查条目状态');
      }
      if (read.sources.length === 0 && read.next_action) message.info(read.next_action);
    }), [projectId, run]);

  const submitCreate = () =>
    run(async () => {
      if (!projectId) return;
      const result = await chartsApi.create(projectId, {
        project_ref: projectId,
        chart_type: createType,
        format: createFormat,
        source_kind: createWing === 'business' ? 'supporting_content' : 'requirement_item',
        source_refs: createSources,
        generate_initial: true,
        operator_ref: operatorRef,
        idempotency_key: newKey('chart-create'),
      });
      if (result.status !== 'created' || !result.chart_ref) {
        message.warning(result.next_action ?? '图表创建未通过来源准入');
        return;
      }
      message.success('图表已创建，正基于来源条目生成初稿');
      setCreateOpen(false);
      await refreshList();
      await openChart(result.chart_ref);
    });

  // 条目卡交接只预选来源并打开向导；目标工作台不自动创建图表。
  useEffect(() => {
    if (
      !workbenchHandoff ||
      workbenchHandoff.intent !== 'create_chart_from_item' ||
      workbenchHandoff.targetWorkbench !== 'diagram' ||
      workbenchHandoff.projectId !== projectId ||
      workbenchHandoff.anchor.entityType !== 'requirement_item' ||
      list === null ||
      handoffConsumedRef.current === workbenchHandoff.token
    ) {
      return;
    }
    handoffConsumedRef.current = workbenchHandoff.token;
    void openCreate(workbenchHandoff.anchor.ref).finally(() => {
      onConsumeWorkbenchHandoff?.(workbenchHandoff.token);
    });
  }, [list, onConsumeWorkbenchHandoff, openCreate, projectId, workbenchHandoff]);

  // ---- 源码编辑循环 ----

  const applySource = () =>
    run(async () => {
      if (!projectId || !workspace) return;
      const ws = await chartsApi.applySource(projectId, workspace.chart_ref, {
        project_ref: projectId,
        source_code: sourceDraft,
        format: workspace.format,
        chart_type: workspace.chart_type,
        source_refs: selectedSourceRefs,
        expected_draft_version: workspace.draft_version,
        operator_ref: operatorRef,
        idempotency_key: newKey('chart-source'),
      });
      applyWorkspace(ws);
      if (ws.validation_errors.length === 0) message.success(`源码已应用（v${ws.draft_version}）`);
      await refreshList();
    });

  const requestSuggestion = () =>
    run(async () => {
      if (!projectId || !workspace) return;
      const result = await chartsApi.requestSuggestion(projectId, workspace.chart_ref, {
        project_ref: projectId,
        intent: suggestIntent,
        operator_ref: operatorRef,
        idempotency_key: newKey('chart-suggest'),
      });
      if (result.status !== 'submitted') {
        message.warning(result.next_action ?? 'AI 建议请求未受理');
        return;
      }
      setSuggestIntent('');
      await openChart(workspace.chart_ref);
    });

  const handleSuggestion = (suggestionRef: string, handling: ChartSuggestionHandling, revised?: string, reason?: string) =>
    run(async () => {
      if (!projectId || !workspace) return;
      const ws = await chartsApi.handleSuggestion(projectId, workspace.chart_ref, suggestionRef, {
        project_ref: projectId,
        handling,
        revised_source: revised ?? null,
        reason: reason ?? null,
        operator_ref: operatorRef,
        idempotency_key: newKey('chart-handle'),
      });
      applyWorkspace(ws);
      setReviseTarget(null);
      setRejectTarget(null);
      setRejectReason('');
      await refreshList();
    });

  // ---- 核对与确认 ----

  const startVerification = () =>
    run(async () => {
      if (!projectId || !workspace) return;
      const result = await chartsApi.startVerification(projectId, workspace.chart_ref, {
        project_ref: projectId,
        operator_ref: operatorRef,
        idempotency_key: newKey('chart-verify'),
      });
      if (result.status !== 'submitted') {
        message.warning(result.next_action ?? '核对发起未通过准入');
        return;
      }
      message.info('已推进为待确认并冻结源码编辑；AI 图文核对进行中');
      setVerifyPolling(true);
      await openChart(workspace.chart_ref);
      await refreshList();
    });

  const decideFinding = (finding: ChartFindingRead, decision: 'accepted' | 'rejected', reason?: string) =>
    run(async () => {
      if (!projectId || !workspace) return;
      const ws = await chartsApi.submitFindingDecision(projectId, workspace.chart_ref, finding.finding_ref, {
        project_ref: projectId,
        decision,
        reason: reason ?? null,
        operator_ref: operatorRef,
        idempotency_key: newKey('finding'),
      });
      setWorkspace(ws);
      setDecisionReject(null);
      setDecisionReason('');
    });

  const confirmChart = () =>
    run(async () => {
      if (!projectId || !workspace) return;
      const result = await chartsApi.confirm(projectId, workspace.chart_ref, {
        project_ref: projectId,
        operator_ref: operatorRef,
        idempotency_key: newKey('chart-confirm'),
      });
      if (result.status !== 'confirmed') {
        message.warning(result.next_action ?? '确认准入未通过');
      } else {
        message.success(`图表已确认，${result.trace_established_count} 条追溯关系已正式确立`);
      }
      await openChart(workspace.chart_ref);
      await refreshList();
    });

  const sendToPublication = useCallback(() => {
    if (!projectId || !workspace || workspace.status !== 'confirmed' || !onWorkbenchHandoff) return;
    onWorkbenchHandoff(createWorkbenchHandoff({
      projectId,
      targetWorkbench: 'release',
      intent: 'compose_document_from_assets',
      anchor: { entityType: 'chart', ref: workspace.chart_ref, title: workspace.title },
      relatedAssets: workspace.sources.map((source) => ({
        entityType: 'requirement_item' as const,
        ref: source.item_ref,
        title: `${source.req_no} ${source.expression}`,
      })),
    }));
  }, [onWorkbenchHandoff, projectId, workspace]);

  const lifecycle = (kind: 'return' | 'void' | 'resume', reason?: string) =>
    run(async () => {
      if (!projectId || !workspace) return;
      const command = {
        project_ref: projectId,
        reason: reason ?? null,
        operator_ref: operatorRef,
        idempotency_key: newKey(`chart-${kind}`),
      };
      const ws =
        kind === 'return'
          ? await chartsApi.returnForRevision(projectId, workspace.chart_ref, command)
          : kind === 'void'
            ? await chartsApi.voidChart(projectId, workspace.chart_ref, command)
            : await chartsApi.resumeEditing(projectId, workspace.chart_ref, command);
      applyWorkspace(ws);
      setLifecycleModal(null);
      setLifecycleReason('');
      await refreshList();
    });

  const transferToIssue = (finding: ChartFindingRead) =>
    run(async () => {
      if (!projectId || !workspace) return;
      const issue = await chartsApi.createIssue(projectId, workspace.chart_ref, finding.finding_ref, {
        project_ref: projectId,
        operator_ref: operatorRef,
        idempotency_key: newKey('issue'),
      });
      message.success(`已创建问题项：${issue.title}`);
      await openChart(workspace.chart_ref);
    });

  // ---- 派生呈现状态（只读投影，不复算门禁）----

  const updateCaret = (el: HTMLTextAreaElement) => {
    const upto = el.value.slice(0, el.selectionStart ?? 0);
    const lines = upto.split('\n');
    setCaret({ line: lines.length, col: lines[lines.length - 1].length + 1 });
  };

  // 渲染预览自由拖动漫游：按下记录鼠标起点与当前平移量，移动时按位移增量平移 translate。
  // 移动/抬起监听挂在 window 上（见下方 effect），指针移出容器也能持续拖动、松开必然结束。
  const onRenderPanStart = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return; // 仅左键
    e.preventDefault(); // 抑制 SVG/图片原生拖拽与选中
    panStart.current = { mx: e.clientX, my: e.clientY, x: view.x, y: view.y };
    setPanning(true);
  };

  // 缩放并把锚点（光标处，缺省=视口中心）下的图元固定不动：pan' = d - (nz/z)·(d - pan)
  const applyZoom = (nextZoom: (z: number) => number, clientX?: number, clientY?: number) => {
    const el = renderRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const dx = clientX == null ? 0 : clientX - rect.left - rect.width / 2;
    const dy = clientY == null ? 0 : clientY - rect.top - rect.height / 2;
    setView((v) => {
      const nz = Math.min(2, Math.max(0.5, Math.round(nextZoom(v.zoom) * 100) / 100));
      if (nz === v.zoom) return v;
      const ratio = nz / v.zoom;
      return { zoom: nz, x: dx - ratio * (dx - v.x), y: dy - ratio * (dy - v.y) };
    });
  };

  // 拖动漫游：move/up 挂 window，指针移出预览区仍持续、松开必然收束
  useEffect(() => {
    if (!panning) return;
    const onMove = (ev: PointerEvent) => {
      const s = panStart.current;
      if (!s) return;
      setView((v) => ({ ...v, x: s.x + (ev.clientX - s.mx), y: s.y + (ev.clientY - s.my) }));
    };
    const onUp = () => {
      panStart.current = null;
      setPanning(false);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [panning]);

  // 滚轮缩放：React onWheel 可能是 passive，无法 preventDefault，故挂原生非 passive 监听
  useEffect(() => {
    const el = renderRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const rect = el.getBoundingClientRect();
      const dx = ev.clientX - rect.left - rect.width / 2;
      const dy = ev.clientY - rect.top - rect.height / 2;
      const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
      setView((v) => {
        const nz = Math.min(2, Math.max(0.5, Math.round(v.zoom * factor * 100) / 100));
        if (nz === v.zoom) return v;
        const ratio = nz / v.zoom;
        return { zoom: nz, x: dx - ratio * (dx - v.x), y: dy - ratio * (dy - v.y) };
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [wsRef]);

  const sourceLabel = (ref: string): string => {
    const src = editSourceOptions.find((s) => s.item_ref === ref) ?? workspace?.sources.find((s) => s.item_ref === ref);
    return src ? `${src.req_no} · ${src.expression.slice(0, 16)}` : ref;
  };

  const sourceTitle = (ref: string): string => {
    const src = editSourceOptions.find((s) => s.item_ref === ref) ?? workspace?.sources.find((s) => s.item_ref === ref);
    return src ? `${src.req_no} · ${src.expression}` : ref;
  };

  const pendingFindings = workspace?.verification?.findings.filter((f) => f.decision == null) ?? [];
  const traceRows = workspace ? buildTraceLinkRows(workspace.trace_links) : [];
  const editable = vm?.actionEnabled.apply_source_change ?? false;

  if (!projectId) {
    return (
      <WorkbenchFrame title="图表设计工作台">
        <Empty description="请先在顶栏选择项目" />
      </WorkbenchFrame>
    );
  }

  const rows = buildChartRows(list?.charts ?? []);

  return (
    <WorkbenchFrame title="图表设计工作台">
      {loadError ? <Alert title={loadError} showIcon type="error" style={{ marginBottom: 12 }} /> : null}

      {/* ── 图表顶栏：选择器 + 来源上下文 + 阶段动作 ── */}
      <div className="dw-topbar">
        <div className="dw-topbar-row">
          <span className="dw-topbar-label">图表</span>
          <Select
            aria-label="图表选择器"
            className="dw-chart-select"
            placeholder={rows.length === 0 ? (list?.next_action ?? '暂无图表') : '选择图表'}
            value={workspace?.chart_ref ?? undefined}
            options={rows.map((r) => ({
              value: r.chartRef,
              label: `${r.title}（${r.typeLabel} · ${r.statusLabel}）`,
            }))}
            onChange={(ref: string) => void openChart(ref)}
          />
          <Button onClick={() => void openCreate()} loading={busy}>
            ＋ 新建图表
          </Button>
          <span className="dw-entry-note">
            来源上下文：确认态需求条目（不得创建无来源图表；带上下文进入时自动预填）
          </span>
          {vm && workspace ? (
            <div className="dw-stage-actions">
              {vm.isDraft || vm.isPending ? (
                <Tooltip title={vm.actionDisabledReason.start_verification ?? undefined}>
                  <Button
                    disabled={!vm.actionEnabled.start_verification}
                    loading={busy}
                    type={vm.isDraft ? 'primary' : 'default'}
                    onClick={startVerification}
                  >
                    {vm.isPending ? '重新核对' : '发起核对'}
                  </Button>
                </Tooltip>
              ) : null}
              {vm.isPending ? (
                <Button onClick={() => setLifecycleModal('return')}>退回修订</Button>
              ) : null}
              {vm.isReturned ? <Button onClick={() => lifecycle('resume')}>重回编辑</Button> : null}
              {vm.actionEnabled.void_chart ? (
                <Button danger onClick={() => setLifecycleModal('void')}>
                  作废
                </Button>
              ) : null}
              {workspace.status === 'confirmed' && onWorkbenchHandoff ? (
                <Button type="primary" onClick={sendToPublication}>去发布编排</Button>
              ) : null}
            </div>
          ) : null}
        </div>
        {workspace && vm ? (
          <div className="dw-topbar-row dw-topbar-row--chips">
            {selectedSourceRefs.map((ref) => (
              <span className="dw-chip" key={ref} title={sourceTitle(ref)}>
                <span className="dw-chip-kind">来源</span>
                {sourceLabel(ref)}
                {editable ? (
                  <button
                    aria-label={`移除来源 ${ref}`}
                    className="dw-chip-remove"
                    type="button"
                    onClick={() => setSelectedSourceRefs((refs) => refs.filter((r) => r !== ref))}
                  >
                    ✕
                  </button>
                ) : null}
              </span>
            ))}
            {editable ? (
              <button
                className="dw-chip-add"
                type="button"
                onClick={() => {
                  setAddSourceRefs(selectedSourceRefs);
                  setAddSourceOpen(true);
                }}
              >
                ＋ 纳入覆盖资产（确认态条目）
              </button>
            ) : (
              <span className="dw-frozen-note">
                {vm.isTerminal
                  ? '来源集合已随图表定格'
                  : vm.isReturned
                    ? '退回修订态：重回编辑后可调整来源集合'
                    : '待确认阶段来源集合已冻结；调整须先退回修订'}
              </span>
            )}
            <span className="dw-topbar-hint">来源变化随「应用源码变更」提交，由服务端同步预建立追溯</span>
          </div>
        ) : null}
      </div>

      {!workspace || !vm ? (
        <div className="dw-empty">
          <Empty description="从上方选择图表，或新建图表进入设计" />
        </div>
      ) : (
        <>
          {workspace.next_action ? (
            <Alert title={workspace.next_action} showIcon type="info" style={{ marginBottom: 12 }} />
          ) : null}

          {/* ── 主区双视图 + 右区双页 ── */}
          <div className="dw-work">
            <section aria-label="源码编辑" className="dw-pane">
              <div className="dw-pane-head">
                源码编辑 <span className="dw-pane-sub">{vm.formatLabel}</span>
                <div className="dw-pane-head-right">
                  {editable ? (
                    <Tooltip title={vm.actionDisabledReason.apply_source_change ?? undefined}>
                      <Button loading={busy} size="small" type="primary" onClick={applySource}>
                        应用源码变更
                      </Button>
                    </Tooltip>
                  ) : (
                    <span className="dw-frozen-note">只读</span>
                  )}
                </div>
              </div>
              <textarea
                aria-label="图表源码"
                className="dw-code"
                disabled={!editable}
                value={sourceDraft}
                onChange={(e) => {
                  setSourceDraft(e.target.value);
                  updateCaret(e.target);
                }}
                onKeyUp={(e) => updateCaret(e.currentTarget)}
                onClick={(e) => updateCaret(e.currentTarget)}
              />
              {workspace.validation_errors.length > 0 ? (
                <Alert
                  title="受控校验未通过（有效源码未更新）"
                  description={
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {workspace.validation_errors.map((e) => (
                        <li key={e}>{e}</li>
                      ))}
                    </ul>
                  }
                  showIcon
                  type="warning"
                  className="dw-pane-alert"
                />
              ) : null}
              {vm.isPending ? (
                <div className="dw-lock-bar">待确认阶段源码编辑已冻结；退回修订后可继续编辑（对应追溯关系将标记待补全）</div>
              ) : null}
              <div className="dw-pane-foot">
                {editable ? (
                  <span>
                    行 {caret.line}, 列 {caret.col}
                  </span>
                ) : null}
                <span>
                  草稿 <b>v{workspace.draft_version}</b>（受控应用形成；校验未通过则有效源码不更新）
                </span>
                {workspace.validation_errors.length === 0 ? (
                  <span className="dw-ok">✓ 受控校验通过</span>
                ) : (
                  <span className="dw-warn">⚠ 校验未通过</span>
                )}
              </div>
            </section>

            <section aria-label="渲染预览" className="dw-pane">
              <div className="dw-pane-head">
                渲染预览 <span className="dw-pane-sub">外部图表渲染适配器</span>
                <div className="dw-pane-head-right dw-zoom">
                  <Button size="small" onClick={() => applyZoom((z) => z - 0.25)}>
                    －
                  </Button>
                  <span className="dw-zoom-value">{Math.round(zoom * 100)}%</span>
                  <Button size="small" onClick={() => applyZoom((z) => z + 0.25)}>
                    ＋
                  </Button>
                  <Button size="small" onClick={() => setView({ zoom: 1, x: 0, y: 0 })}>
                    复位
                  </Button>
                </div>
              </div>
              <div
                className={`dw-render${panning ? ' dw-render--panning' : ''}`}
                data-testid="chart-preview"
                ref={renderRef}
                onPointerDown={onRenderPanStart}
              >
                <div
                  className="dw-render-scale"
                  style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}
                >
                  <ChartPreview ws={workspace} />
                </div>
              </div>
              <div className="dw-pane-foot">
                <span>代码 ⇄ 渲染 实时联动</span>
                <span className="dw-pane-foot-muted">渲染由外部能力提供，系统不实现渲染</span>
              </div>
            </section>

            <aside aria-label="工作面板" className="dw-side">
              <div className="dw-tabs" role="tablist">
                <button
                  aria-selected={sideTab === 'design'}
                  className={`dw-tab${sideTab === 'design' ? ' dw-tab--on' : ''}`}
                  role="tab"
                  type="button"
                  onClick={() => setSideTab('design')}
                >
                  设计
                </button>
                <button
                  aria-selected={sideTab === 'verify'}
                  className={`dw-tab${sideTab === 'verify' ? ' dw-tab--on' : ''}`}
                  role="tab"
                  type="button"
                  onClick={() => setSideTab('verify')}
                >
                  核对与确认
                  {vm.isPending && pendingFindings.length > 0 ? (
                    <span className="dw-tab-badge">{pendingFindings.length}</span>
                  ) : null}
                </button>
                <button
                  aria-selected={sideTab === 'source'}
                  className={`dw-tab${sideTab === 'source' ? ' dw-tab--on' : ''}`}
                  role="tab"
                  type="button"
                  onClick={() => setSideTab('source')}
                >
                  来源
                  {workspace.sources.length > 0 ? (
                    <span className="dw-tab-badge dw-tab-badge--muted">{workspace.sources.length}</span>
                  ) : null}
                </button>
              </div>

              {sideTab === 'design' ? (
                <div className="dw-side-body dw-side-body--chat">
                  <div className="dw-sec-title">AI 对话（主要调整方式）</div>
                  {vm.isDraft ? null : (
                    <div className="dw-side-note">
                      {vm.isPending
                        ? '待确认阶段 AI 草稿通道与源码编辑一同冻结；退回修订后可继续设计。'
                        : vm.isReturned
                          ? '退回修订态：重回编辑后可继续设计。'
                          : '当前状态下 AI 草稿通道已关闭。'}
                    </div>
                  )}
                  <div aria-label="AI 对话时间线" className="az5-thread dw-chat-thread" ref={chatThreadRef}>
                    {workspace.suggestion_thread.length === 0 ? (
                      <p className="az5-hint">
                        描述生成 / 修订意图后发送（可空：按来源条目生成骨架）；AI 草稿经采纳 / 修订采纳才更新图表源码。
                      </p>
                    ) : null}
                    {workspace.suggestion_thread.map((entry) => (
                      <Fragment key={entry.context_ref}>
                        <div className="az5-msg az5-msg--user">
                          <span className="az5-ava">我</span>
                          <div className="az5-msg__body">
                            <span className="az5-who">{entry.kind === 'initial' ? '我 · 创建图表' : '我 · 修订指令'}</span>
                            <span className="az5-txt">
                              {entry.kind === 'initial'
                                ? '基于来源条目生成图表初稿'
                                : entry.intent || '（未填写意图：按来源条目生成建议）'}
                            </span>
                          </div>
                        </div>
                        {entry.status === 'generating' ? (
                          <p className="az5-sys">
                            {entry.kind === 'initial'
                              ? 'AI 正在生成图表初稿…（经受控校验后自动应用）'
                              : 'AI 源码建议生成中…（登记后需人工采纳才会更新图表）'}
                          </p>
                        ) : entry.status === 'stopped' ? (
                          <SuggestionStoppedCard reason={entry.stop_reason} />
                        ) : entry.suggestion ? (
                          <SuggestionAiCard
                            actionable={vm.isDraft}
                            initial={entry.kind === 'initial'}
                            suggestion={entry.suggestion}
                            onAdopt={() => handleSuggestion(entry.suggestion!.suggestion_ref, 'adopt')}
                            onReject={() => setRejectTarget(entry.suggestion!.suggestion_ref)}
                            onRevise={() =>
                              setReviseTarget({ ref: entry.suggestion!.suggestion_ref, source: entry.suggestion!.source_code })
                            }
                          />
                        ) : null}
                      </Fragment>
                    ))}
                  </div>
                  <div className="az5-composer dw-chat-composer">
                    <div className="az5-input">
                      <textarea
                        aria-label="AI 修订意图"
                        disabled={!vm.actionEnabled.request_suggestion || suggestGenerating}
                        placeholder={
                          vm.actionEnabled.request_suggestion
                            ? '描述生成 / 修订意图（可空）…'
                            : vm.actionDisabledReason.request_suggestion ?? '当前状态不可请求 AI 建议'
                        }
                        rows={1}
                        value={suggestIntent}
                        onChange={(e) => setSuggestIntent(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            if (vm.actionEnabled.request_suggestion && !suggestGenerating && !busy) requestSuggestion();
                          }
                        }}
                      />
                      <Tooltip title={vm.actionDisabledReason.request_suggestion ?? undefined}>
                        <button
                          aria-label="发送"
                          className="az5-send"
                          disabled={!vm.actionEnabled.request_suggestion || suggestGenerating || busy}
                          type="button"
                          onClick={requestSuggestion}
                        >
                          ↑
                        </button>
                      </Tooltip>
                    </div>
                    <p className="az5-note">
                      草稿是模型推理结果，经采纳 / 修订采纳才更新图表源码，不自动生效；拒绝留档不删除。
                    </p>
                  </div>
                </div>
              ) : sideTab === 'verify' ? (
                <div className="dw-side-body">
                  {!workspace.verification ? (
                    <div className="dw-side-note dw-side-note--empty">
                      尚未发起核对。
                      <br />
                      在顶栏 [发起核对] 后：源码与来源集合冻结，图表推进为待确认，AI 图文核对形成源对照与发现项，供逐项复核与确认门禁使用。
                    </div>
                  ) : (
                    <>
                      <div className="dw-round-bar">
                        <b>第 {workspace.verification.round_no} 轮图文核对</b>
                        <Tag
                          color={
                            workspace.verification.processing_status === 'completed'
                              ? 'green'
                              : workspace.verification.processing_status === 'failed'
                                ? 'red'
                                : 'blue'
                          }
                        >
                          {workspace.verification.processing_status === 'completed'
                            ? '核对完成'
                            : workspace.verification.processing_status === 'failed'
                              ? '核对失败'
                              : '核对进行中'}
                        </Tag>
                        {workspace.verification.invalidated ? <Tag>轮次已失效</Tag> : null}
                      </div>
                      {workspace.verification.reason ? (
                        <Alert title={workspace.verification.reason} showIcon type="warning" />
                      ) : null}

                      <div className="dw-sec-title">源对照（图元 ↔ 覆盖资产）</div>
                      <div className="dw-map-list">
                        {workspace.sources.map((s) => {
                          const hit = workspace.verification?.findings.some(
                            (f) => f.decision !== 'rejected' && f.related_source_refs.includes(s.item_ref),
                          );
                          return (
                            <div className={`dw-map-row${hit ? ' dw-map-row--warn' : ''}`} key={s.item_ref}>
                              <span className={`dw-map-dot${hit ? ' dw-map-dot--warn' : ''}`} />
                              <span className="dw-map-text" title={`${s.req_no} · ${s.expression}`}>
                                {s.req_no} · {s.expression.slice(0, 24)}
                              </span>
                              {hit ? <span className="dw-map-note">见发现项</span> : null}
                            </div>
                          );
                        })}
                      </div>

                      <div className="dw-sec-title">发现项（逐项复核，AI 结果不直接成为事实）</div>
                      {workspace.verification.findings.length === 0 ? (
                        <div className="dw-side-note">本轮未形成发现项。</div>
                      ) : (
                        workspace.verification.findings.map((f) => (
                          <div
                            className={`dw-finding${f.decision == null && f.is_blocking ? ' dw-finding--warn' : ''}`}
                            key={f.finding_ref}
                          >
                            <div className="dw-finding-head">
                              <Tag color={TONE_COLOR[findingTypeMeta[f.finding_type]?.tone ?? 'neutral']}>
                                {findingTypeMeta[f.finding_type]?.label ?? f.finding_type}
                              </Tag>
                              <span className="dw-finding-summary">{f.summary}</span>
                              {f.decision ? (
                                <Tag color={f.decision === 'accepted' ? 'green' : 'red'}>
                                  {f.decision === 'accepted' ? '已接受' : '已拒绝'}
                                </Tag>
                              ) : null}
                            </div>
                            <div className="dw-finding-basis">{f.basis_summary}</div>
                            {f.decision_reason ? (
                              <div className="dw-finding-basis">复核理由：{f.decision_reason}</div>
                            ) : null}
                            <div className="dw-finding-actions">
                              {f.decision == null && vm.isPending ? (
                                <>
                                  <Button size="small" onClick={() => decideFinding(f, 'accepted')}>
                                    接受
                                  </Button>
                                  <Button size="small" danger onClick={() => setDecisionReject(f)}>
                                    拒绝（理由必填）
                                  </Button>
                                </>
                              ) : null}
                              {f.decision === 'accepted' && f.is_blocking && !f.issue_ref && vm.isPending ? (
                                <Button size="small" type="link" onClick={() => transferToIssue(f)}>
                                  转问题项
                                </Button>
                              ) : null}
                              {f.issue_ref ? <Tag color="purple">已转问题项</Tag> : null}
                            </div>
                          </div>
                        ))
                      )}

                      {vm.isPending ? (
                        <div className="dw-gate">
                          <div className="dw-sec-title">确认门禁</div>
                          {vm.blockedReasons.length > 0 ? (
                            <div className="dw-gate-block">
                              确认准入未通过：
                              <ul>
                                {vm.blockedReasons.map((r) => (
                                  <li key={r}>{r}</li>
                                ))}
                              </ul>
                              处理路径：复核发现项 / 退回修订 / 转问题项后重新核对。
                            </div>
                          ) : null}
                          <Tooltip title={vm.blockedReasons.length > 0 ? vm.blockedReasons.join('；') : undefined}>
                            <Button
                              disabled={!vm.canSubmitConfirmation}
                              loading={busy}
                              type="primary"
                              onClick={confirmChart}
                            >
                              确认为受控图表（确立预建立追溯关系）
                            </Button>
                          </Tooltip>
                        </div>
                      ) : null}
                      {workspace.confirm_basis ? (
                        <Alert title="确认依据" description={workspace.confirm_basis} showIcon type="success" />
                      ) : null}
                    </>
                  )}
                </div>
              ) : (
                <div className="dw-side-body dw-side-body--source">
                  <div className="dw-sec-title">来源条目（与图表逐条核对）</div>
                  <div className="dw-side-note">
                    图表所依据的确认态需求条目全文，对照左侧渲染预览逐条核对图表是否忠实覆盖来源。
                  </div>
                  {workspace.sources.length === 0 ? (
                    <div className="dw-side-note dw-side-note--empty">本图表暂无来源条目。</div>
                  ) : (
                    <div className="dw-src-list">
                      {workspace.sources.map((s) => (
                        <article className="dw-src-item" key={s.item_ref}>
                          <header className="dw-src-head">
                            <span className="dw-src-no">{s.req_no}</span>
                            <Tag>{requirementItemTypeText(s.req_type as RequirementItemType)}</Tag>
                            {priorityText(s.priority) ? (
                              <Tag color="blue">{priorityText(s.priority)}</Tag>
                            ) : null}
                            <span className="dw-src-status">{s.status === 'confirmed' ? '确认态' : s.status}</span>
                          </header>
                          <p className="dw-src-expr">{s.expression}</p>
                          <dl className="dw-src-fields">
                            {(
                              [
                                { label: '内容整理说明', value: s.curation_note },
                                { label: '条目边界说明', value: s.boundary_note },
                                { label: '验收准则', value: s.verification_note },
                                { label: '验证方式', value: s.verification_method },
                              ] as const
                            ).map((f) => (
                              <div className="dw-src-field" key={f.label}>
                                <dt className="dw-src-field-label">{f.label}</dt>
                                {f.value ? (
                                  <dd className="dw-src-field-value">{f.value}</dd>
                                ) : (
                                  <dd className="dw-src-field-value dw-src-field-empty">未填写</dd>
                                )}
                              </div>
                            ))}
                          </dl>
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </aside>
          </div>

          {/* ── 底部图表状态条 ── */}
          <div className="dw-statusbar">
            <span className="dw-status-item">
              <span className={`dw-status-dot dw-status-dot--${vm.statusTone}`} />
              图表状态：<b>{vm.statusLabel}</b>
            </span>
            {vm.statusReason ? <span className="dw-status-item dw-status-muted">{vm.statusReason}</span> : null}
            <span className="dw-status-item">
              草稿版本：<b>v{workspace.draft_version}</b>
            </span>
            <span className="dw-status-item">
              类型：<b>{vm.typeLabel} · {vm.formatLabel}</b>
            </span>
            {workspace.verification ? (
              <span className="dw-status-item">
                第 <b>{workspace.verification.round_no}</b> 轮核对
                {workspace.verification.processing_status === 'completed' ? '完成' : workspace.verification.processing_status === 'failed' ? '失败' : '中'}
              </span>
            ) : null}
            <span className="dw-status-item dw-status-trace">
              预建立追溯：
              {traceRows.length === 0
                ? '无'
                : traceRows.map((l) => (
                    <Tooltip key={l.linkRef} title={l.statusReason ?? l.statusLabel}>
                      <Tag color={TONE_COLOR[l.statusTone]} style={{ marginInlineEnd: 4 }}>
                        {l.upstreamLabel} · {l.statusLabel}
                      </Tag>
                    </Tooltip>
                  ))}
              <span className="dw-status-muted">（随来源集合同步，确认后正式确立）</span>
            </span>
          </div>
        </>
      )}

      {/* 创建向导：选类型 + 选来源条目 → 直接生成图表初稿（主题由语义自动生成） */}
      <Drawer
        open={createOpen}
        title="创建受控图表（仅确认态需求条目可作为来源）"
        width={520}
        onClose={() => setCreateOpen(false)}
        extra={
          <Button
            disabled={createSources.length === 0}
            loading={busy}
            type="primary"
            onClick={submitCreate}
          >
            创建图表（生成初稿）
          </Button>
        }
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <Text strong>图表类型</Text>
            <Select
              aria-label="图表类型"
              style={{ width: '100%' }}
              value={createType}
              options={Object.entries(chartTypeLabels).map(([value, label]) => ({ value, label }))}
              onChange={(value: ChartType) => {
                setCreateType(value);
                const formats = typeFormatOptions[value];
                if (!formats.includes(createFormat)) setCreateFormat(formats[0]);
              }}
            />
          </div>
          <div>
            <Text strong>表达方式</Text>
            <Select
              aria-label="表达方式"
              style={{ width: '100%' }}
              value={createFormat}
              options={typeFormatOptions[createType].map((value) => ({ value, label: chartFormatLabels[value] }))}
              onChange={(value: ChartFormat) => setCreateFormat(value)}
            />
          </div>
          <div>
            <Text strong>来源类型</Text>
            <Segmented
              aria-label="来源类型"
              block
              value={createWing}
              onChange={(value) => {
                setCreateWing(value as 'requirement' | 'business');
                setCreateSources([]); // 单一来源翼：切换即清空已选
              }}
              options={[
                { value: 'requirement', label: '需求条目' },
                { value: 'business', label: '业务知识' },
              ]}
            />
          </div>
          {createWing === 'requirement' ? (
            <div>
              <Text strong>来源确认态条目（可多选）</Text>
              {eligibleSources.length === 0 ? (
                <Empty description="当前项目暂无确认态需求条目" />
              ) : (
                <Select
                  aria-label="来源条目"
                  mode="multiple"
                  style={{ width: '100%' }}
                  value={createSources}
                  options={eligibleSources.map((s) => ({
                    value: s.item_ref,
                    label: `${s.req_no} ${s.expression.slice(0, 40)}`,
                  }))}
                  onChange={setCreateSources}
                />
              )}
            </div>
          ) : (
            <div>
              <Text strong>来源业务领域知识（确认态；可多选）</Text>
              {businessSources.length === 0 ? (
                <Empty description="当前项目暂无确认态业务领域知识" />
              ) : (
                <Select
                  aria-label="来源业务知识"
                  mode="multiple"
                  style={{ width: '100%' }}
                  value={createSources}
                  options={businessSources.map((s) => ({
                    value: s.element_ref,
                    label: `【${elementTypeMeta(s.element_type).label}】${s.content.slice(0, 40)}`,
                  }))}
                  onChange={setCreateSources}
                />
              )}
            </div>
          )}
          <Text type="secondary" style={{ fontSize: '0.75rem' }}>
            {createWing === 'business'
              ? '业务知识来源只支撑领域事实表达（词汇/规则/参与者），不得替代需求条目承载需求语义（图文核对将拦截术语洗白）。'
              : '创建后将基于所选条目内容自动生成图表初稿与语义主题（初稿仍经受控校验；生成失败时保留空稿可手工编辑）。'}
          </Text>
        </Space>
      </Drawer>

      {/* 纳入覆盖资产 */}
      <Modal
        open={addSourceOpen}
        title="纳入覆盖资产（确认态需求条目；提交随「应用源码变更」生效）"
        okText="更新来源集合"
        confirmLoading={busy}
        onCancel={() => setAddSourceOpen(false)}
        onOk={() => {
          setSelectedSourceRefs(addSourceRefs);
          setAddSourceOpen(false);
        }}
      >
        <Select
          aria-label="图表来源条目"
          mode="multiple"
          style={{ width: '100%' }}
          value={addSourceRefs}
          options={editSourceOptions.map((s) => ({
            value: s.item_ref,
            label: `${s.req_no} ${s.expression.slice(0, 40)}`,
          }))}
          onChange={setAddSourceRefs}
        />
      </Modal>

      {/* 修订采纳 */}
      <Modal
        open={reviseTarget != null}
        title="修订采纳 AI 建议（修订稿仍需受控校验）"
        okText="修订采纳"
        confirmLoading={busy}
        onCancel={() => setReviseTarget(null)}
        onOk={() => reviseTarget && handleSuggestion(reviseTarget.ref, 'revise_and_adopt', reviseTarget.source)}
      >
        <Input.TextArea
          aria-label="修订稿源码"
          autoSize={{ minRows: 8, maxRows: 16 }}
          value={reviseTarget?.source ?? ''}
          onChange={(e) => setReviseTarget((t) => (t ? { ...t, source: e.target.value } : t))}
        />
      </Modal>

      {/* 拒绝建议（理由必填） */}
      <Modal
        open={rejectTarget != null}
        title="拒绝 AI 建议（必须填写理由；建议留档不删除）"
        okText="拒绝"
        okButtonProps={{ danger: true, disabled: !rejectReason.trim() }}
        confirmLoading={busy}
        onCancel={() => {
          setRejectTarget(null);
          setRejectReason('');
        }}
        onOk={() => rejectTarget && handleSuggestion(rejectTarget, 'reject', undefined, rejectReason)}
      >
        <Input.TextArea
          aria-label="拒绝理由"
          autoSize={{ minRows: 3 }}
          placeholder="说明为什么不采纳该建议"
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
        />
      </Modal>

      {/* 拒绝发现项（理由必填） */}
      <Modal
        open={decisionReject != null}
        title="拒绝 AI 发现项（必须记录拒绝理由）"
        okText="拒绝该发现项"
        okButtonProps={{ danger: true, disabled: !decisionReason.trim() }}
        confirmLoading={busy}
        onCancel={() => {
          setDecisionReject(null);
          setDecisionReason('');
        }}
        onOk={() => decisionReject && decideFinding(decisionReject, 'rejected', decisionReason)}
      >
        <Input.TextArea
          aria-label="发现项拒绝理由"
          autoSize={{ minRows: 3 }}
          placeholder="说明为什么该发现项不成立"
          value={decisionReason}
          onChange={(e) => setDecisionReason(e.target.value)}
        />
      </Modal>

      {/* 退回修订 / 作废 */}
      <Modal
        open={lifecycleModal != null}
        title={lifecycleModal === 'void' ? '作废图表（相关追溯关系将失效）' : '退回修订（相关追溯关系标记待补全）'}
        okText={lifecycleModal === 'void' ? '作废' : '退回修订'}
        okButtonProps={{ danger: lifecycleModal === 'void' }}
        confirmLoading={busy}
        onCancel={() => {
          setLifecycleModal(null);
          setLifecycleReason('');
        }}
        onOk={() => lifecycleModal && lifecycle(lifecycleModal, lifecycleReason)}
      >
        <Input.TextArea
          aria-label="处置原因"
          autoSize={{ minRows: 3 }}
          placeholder="处置原因（可选）"
          value={lifecycleReason}
          onChange={(e) => setLifecycleReason(e.target.value)}
        />
      </Modal>
    </WorkbenchFrame>
  );
}
