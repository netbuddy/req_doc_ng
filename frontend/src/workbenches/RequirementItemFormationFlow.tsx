/**
 * 条目形成阶段五区页面（SCN-002-P01 前端 —— 区5 对话式 AI 协同，2026-07 重设计）。
 *
 * 事实源：docs/40-detailed-design/slices/SCN-002-P01-需求条目形成/页面详细设计.md、前端交互与接口.md。
 * - 区5 = 固定头部（产出·待确认条目 + 进入评审出口）+ 条目列表 + 对话时间线 + 快捷命令 `/命令词` 预填 + 自由文本输入。
 * - 前端不解析命令词：整段原文发 AEP-097 对话端点，由后端注册表解析命令词、LLM 解释正文；
 *   区2「生成待确认条目」（AEP-038）与建议卡采纳/拒绝（AEP-036）保持结构化直发。
 * - 无上游上下文（sourceWorkspace 与 initialWorkspace 均缺）时呈现引导空态，不再落本地示例工作区。
 * - AEP-008/AEP-017 保持停用；/修订、/规范化 起草走 AEP-097 → item_draft lane 出候选建议卡。
 */
import { Button, Checkbox, Input, Modal, Select } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ElementWorkspaceRead } from '../api/analysis';
import { agentRunApi } from '../api/agent-runs';
import { useAgentRunWatcher, type RunPollTick } from '../hooks/useAgentRunWatcher';
import { useAgentRunWatchPool } from '../hooks/useAgentRunWatchPool';
import {
  itemFormationApi,
  type ItemFormationWorkspaceRead,
  type ItemizationResultRead,
  type ItemizationScopeType,
  type RequirementItemType,
  type StructureRecheckOutcomeRead,
} from '../api/item-formation';
import { requirementsApi, type ItemRevisionCommand, type ItemRevisionMode } from '../api/requirements';
import { runtimeStatusApi } from '../api/runtime-status';
import { serverNowIso } from '../api/server-clock';
import { settingsApi, type RequirementConventionCatalogRead } from '../api/settings';
import { fetchChatTranscript } from '../api/transcript';
import { transcriptRowToBubble } from '../view-models/demo-chat-transcript';
import { renderActionIcon } from '../ui/icons';
import { RelativeTime } from '../ui/RelativeTime';
import {
  createTrace,
  traceAdvance,
  traceCurrentStage,
  traceDetailLines,
  traceExtendQueue,
  traceFinish,
  traceSummaryText,
  TRACE_STALL_THRESHOLDS_MS,
  type AiRequestTrace,
  type TraceStage,
} from '../view-models/ai-request-trace';
import {
  BATCH_PROGRESS_HINT,
  buildFormationWorkspaceFromElementWorkspace,
  buildItemSourceCanvas,
  deriveBatchProgress,
  deriveRecheckTargets,
  deriveReviewGateGaps,
  effectiveCompletenessKey,
  deriveStructureHealthReport,
  FORMATION_QUICK_COMMAND_PREFILLS,
  RECHECK_DISABLED_REASON,
  groupFormationElements,
  ITEM_COMPLETENESS_BADGE_HINTS,
  ITEM_COMPLETENESS_FILTERS,
  itemCompletenessBadge,
  mapFormationElements,
  mapItemStructureReview,
  mapPendingItems,
  mapSourceElementsById,
  matchesItemCompletenessFilter,
  priorityText,
  resolveBatchSubmitFollowup,
  resolveConventionPattern,
  resolveFormationAnchors,
  REVIEW_GATE_NOTES,
  revisionRecordFieldText,
  verificationMethodText,
  type ItemCompletenessFilterKey,
} from '../view-models/requirement-item-formation';
import {
  buildZone5Timeline,
  elementTypeMeta,
  mergeHydratedMessages,
  resolveCardPositions,
  type PositionedCard,
} from '../view-models/requirement-analysis';
import { attestationRecordText, isSourceAttestation } from '../view-models/requirement-item-review';
import { AiTraceDetail, AiTraceRail } from './AiTraceRail';
import { StatusPill } from './WorkbenchWidgets';
import { createIdempotencyKey } from '../api/idempotency';

const { TextArea } = Input;

type RevisionField =
  | 'expression'
  | 'req_type'
  | 'curation_note'
  | 'boundary_note'
  | 'verification_method'
  | 'verification_note'
  | 'priority';

const REVISION_FIELD_OPTIONS: Array<{ value: RevisionField; label: string }> = [
  { value: 'expression', label: '需求表达' },
  { value: 'req_type', label: '需求类型' },
  { value: 'curation_note', label: '整理说明' },
  { value: 'boundary_note', label: '边界说明' },
  { value: 'verification_method', label: '验证方式' },
  { value: 'verification_note', label: '验收准则' },
  { value: 'priority', label: '优先级' },
];

function itemFieldValue(
  item: {
    expression: string;
    req_type: string;
    curation_note?: string | null;
    boundary_note?: string | null;
    verification_method?: string[];
    verification_note?: string | null;
    priority?: string | null;
  },
  field: RevisionField,
): string {
  if (field === 'expression') return item.expression;
  if (field === 'req_type') return item.req_type;
  if (field === 'curation_note') return item.curation_note ?? '';
  if (field === 'boundary_note') return item.boundary_note ?? '';
  if (field === 'verification_method') return (item.verification_method ?? []).join(',');
  if (field === 'verification_note') return item.verification_note ?? '';
  return item.priority ?? '';
}
type BatchUiStatus = 'idle' | 'running' | 'failed';

/**
 * 区2 批次进度事实（T20260711-formation-z2z5-visual）：
 * total=发起时捕获的选中要素数（拿不到时 null → 不定型降级）；
 * scopeRefs=发起范围要素 id（进度账目只统计范围内归因，与勾选集自洽）；
 * results=本批次 formation_context 的逐要素归因快照（在途三拍刷新覆写，避免读到上一批次残留）。
 */
interface BatchRunUi {
  phase: 'running' | 'succeeded' | 'failed';
  total: number | null;
  scopeRefs: readonly string[] | null;
  results: Array<Pick<ItemizationResultRead, 'result_status' | 'element_ref'>>;
  error: string | null;
}

const VERIFICATION_METHOD_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'test', label: '测试' },
  { value: 'demonstration', label: '演示' },
  { value: 'inspection', label: '检查' },
  { value: 'analysis', label: '分析' },
];

const PRIORITY_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
];

const ITEM_TYPE_OPTIONS: Array<{ value: RequirementItemType; label: string }> = [
  { value: 'functional', label: '功能需求' },
  { value: 'quality', label: '质量属性' },
  { value: 'constraint', label: '约束' },
  { value: 'data', label: '数据需求' },
  { value: 'interface', label: '接口需求' },
];

const RESULT_STATUS_TEXT: Record<string, string> = {
  created: '已创建',
  blocked: '未形成',
  failed: '失败',
  skipped: '跳过',
};

// 'cancelled' 与其余页面同口径入终止集（issue #8 缺陷 7：缺失时被取消的 run 永久轮询）
const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

/** 区5 时间线消息（会话事实的界面留痕；候选建议卡由工作区数据投影，不入此列表）。 */
interface ChatMsg {
  id: number;
  kind: 'user' | 'cmd' | 'ai' | 'sys' | 'sys-ok' | 'sys-warn';
  text: string;
  /** 消息产生时刻（ISO）。本地推的消息取入列时刻，水合而来的取留痕行时刻。 */
  at: string;
  /** 来源留痕行 id（水合而来的消息才有）：水合合并按它去重（裁定 F8） */
  sourceId?: string | null;
  /** 完成回执的可展开链路详情（阶段观测时刻 + 运行引用，与 dialogue.* 日志对账） */
  traceLines?: string[];
}

interface RequirementItemFormationFlowProps {
  projectId: string;
  operatorRef: string;
  sourceWorkspace: ElementWorkspaceRead | null;
  initialWorkspace?: ItemFormationWorkspaceRead | null;
  /** 恢复预取失败原因（父级续办路径）；空态页就地展示，替代静默兜底。 */
  prefetchError?: string | null;
  onBackToAnalysis: () => void;
  // → 条目评审（SCN-003）。SCN-003 页面就绪后由父级消费其工作区；未接入时父级仅切换阶段。
  onEnterItemReview?: (workspace: ItemFormationWorkspaceRead) => void;
}

/** 解析 LDM-005 source_anchor JSON，取原文锚点片段（exact 引文，多段用「 … 」相连）。 */
function anchorExactText(sourceAnchor: string | null | undefined): string | null {
  if (!sourceAnchor) {
    return null;
  }
  try {
    const parsed = JSON.parse(sourceAnchor) as { ranges?: { exact?: string }[] };
    const quotes = (parsed.ranges ?? []).map((r) => (r.exact ?? '').trim()).filter(Boolean);
    return quotes.length ? quotes.join(' … ') : null;
  } catch {
    return null;
  }
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求处理失败';
}

/** 无上游上下文返回 null（呈现引导空态）；不再落本地示例工作区（固定数据盘点 #37）。 */
function makeInitialWorkspace(
  sourceWorkspace: ElementWorkspaceRead | null,
  initialWorkspace?: ItemFormationWorkspaceRead | null,
): ItemFormationWorkspaceRead | null {
  if (initialWorkspace) {
    return initialWorkspace;
  }
  return sourceWorkspace ? buildFormationWorkspaceFromElementWorkspace(sourceWorkspace) : null;
}

function FormationEmptyState({
  prefetchError,
  onBackToAnalysis,
}: {
  prefetchError?: string | null;
  onBackToAnalysis: () => void;
}) {
  return (
    <div className="item-formation-grid" aria-label="条目形成页面">
      <section
        aria-label="条目形成空态"
        className="item-formation-zone"
        style={{ gridColumn: '1 / -1' }}
      >
        <div className="intake-zone__header">
          <span>条目形成</span>
          <h3>尚未接入上游要素工作区</h3>
        </div>
        {prefetchError ? (
          <p className="item-formation-notice item-formation-notice--error" role="alert">
            上游工作区读取失败：{prefetchError}
          </p>
        ) : null}
        <p className="empty-state">
          条目形成的输入是「知识抽取」阶段已确认的知识项。请返回知识抽取完成要素确认后，
          从该页「进入条目形成」进入本页；或在需求管理维护列表选择带有形成批次的流程续办。
        </p>
        <div>
          <Button onClick={onBackToAnalysis} type="primary">
            返回知识抽取
          </Button>
        </div>
      </section>
    </div>
  );
}

export function RequirementItemFormationFlow(props: RequirementItemFormationFlowProps) {
  const initial = makeInitialWorkspace(props.sourceWorkspace, props.initialWorkspace);
  if (!initial) {
    return (
      <FormationEmptyState onBackToAnalysis={props.onBackToAnalysis} prefetchError={props.prefetchError} />
    );
  }
  return <FormationWorkspaceView {...props} />;
}

function FormationWorkspaceView({
  projectId,
  operatorRef,
  sourceWorkspace,
  initialWorkspace,
  onBackToAnalysis,
  onEnterItemReview,
}: RequirementItemFormationFlowProps) {
  const [workspace, setWorkspace] = useState<ItemFormationWorkspaceRead>(
    () => makeInitialWorkspace(sourceWorkspace, initialWorkspace) as ItemFormationWorkspaceRead,
  );
  const [selectedElementRefs, setSelectedElementRefs] = useState<string[]>(() =>
    (makeInitialWorkspace(sourceWorkspace, initialWorkspace)?.eligible_elements ?? []).map((e) => e.id),
  );
  const [selectedItemRef, setSelectedItemRef] = useState<string | null>(null);
  // 区4 放大态：用户显式索取的暂态，随会话不落库（常态不挤压区3）
  const [detailZoomed, setDetailZoomed] = useState(false);
  const [revisionField, setRevisionField] = useState<RevisionField>('expression');
  const [revisionValue, setRevisionValue] = useState('');
  const [revisionReason, setRevisionReason] = useState('');
  const [revisionFormOpen, setRevisionFormOpen] = useState(false);
  const [batchRun, setBatchRun] = useState<BatchRunUi | null>(null);
  // AEP-114 结构复核在途态（双入口共用：区2 批量按钮 / 区5 /复核；只判不改，完成后徽标归位）。
  // busy 归属校验（issue #8 缺陷 8）：谁置 true 谁清——静默链式 watcher（从未置 true）
  // 终止时不得清掉手动批量复核的 busy，否则复核/生成按钮 mid-run 复活。
  const [recheckBusy, setRecheckBusy] = useState(false);
  // 批次 UI 状态由 batchRun 单源派生，防两处状态漂移（succeeded 驻留摘要徽标但不阻塞下一批次）
  const batchStatus: BatchUiStatus =
    batchRun?.phase === 'running' ? 'running' : batchRun?.phase === 'failed' ? 'failed' : 'idle';
  // P2：区5 达标度筛选（读投影，纯 UI 态，不作门禁）
  const [completenessFilter, setCompletenessFilter] = useState<ItemCompletenessFilterKey>('all');
  // 区5 清单折叠（纯 UI 态，不入库）：折叠态=横向队列条
  const [itemsCollapsed, setItemsCollapsed] = useState(false);
  const [noticeText, setNoticeText] = useState<string | null>(null);
  const [revisionBusy, setRevisionBusy] = useState(false);
  const [dialogueBusy, setDialogueBusy] = useState(false);
  // 修订态原文回看（SCN-002 详设 §4）：展开中的来源要素溯源块
  const [sourceTraceRef, setSourceTraceRef] = useState<string | null>(null);
  // 异步 AgentRun 追踪（P0 收编手写轮询）。两类任务的并发语义不同，故用两种载体：
  //  - 批次条目化 startBatchWatch：单实例 hook（新 start 抢占旧 watch）。批次由 canStartBatch
  //    门禁保证单飞（batchStatus==='running' 时不可再发），不会同类型并发，抢占不可达——其不变量
  //    不依赖「被抢占 loop 补跑副作用」，用单实例 hook 安全。
  //  - 结构复核 startRecheckWatch：并发池（每个在途复核 run 各持一路，互不抢占，issue #8 缺陷 7）。
  //    复核是可并发的——手动复核 R1（ownsBusy，置 recheckBusy=true）在途时，字段修订/命令改写会触发
  //    静默链式复核 R2；若两路复用单实例 hook，R2 会抢占并杀死 R1 的在途循环，R1 的终态 releaseBusy
  //    （谁置谁清，issue #8 缺陷 8）永不执行，recheckBusy 永久卡死、区2/区5 控件锁死至刷新页面
  //    （T20260717-ucw-p0-transport 裁定 F1，正是 P0 把两路复核收敛到单实例时引入的回归）。改用并发
  //    池后每路各自跑到终态、各自 releaseBusy，恢复收编前 pollTimersRef 的共存语义。
  // 两者轮询目标均由各自 poll 回调自供（纯轮询、无 EventSource——本页现状无推送，换底不改进）；首拍
  // 同步（immediate）保留手写轮询的 `void poll()` 语义。disposedRef 仍由本页持有：poll 回调多次
  // await 间的卸载守卫（hook/池只在 tick 边界查卸载，poll 体内 setState 需自身守卫），mount 复位见下方 effect。
  const { start: startBatchWatch } = useAgentRunWatcher({ intervalMs: 800 });
  const { start: startRecheckWatch } = useAgentRunWatchPool({ intervalMs: 800 });
  const disposedRef = useRef(false);

  // ---- 区5 对话态（会话事实的界面侧：时间线消息、输入框、命令组稿弹层）----
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [composerText, setComposerText] = useState('');
  const [typePickerOpen, setTypePickerOpen] = useState(false);
  const [mergePickerOpen, setMergePickerOpen] = useState(false);
  const [mergeChecked, setMergeChecked] = useState<string[]>([]);
  const msgSeqRef = useRef(0);
  // 卡片落位记忆（key → 排序位）：一旦落位就不再被后续新消息推着走；切上下文即清空
  const cardPositionsRef = useRef<Map<string, PositionedCard>>(new Map());
  const threadRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // useModal 而非静态 Modal.confirm：软门弹层需消费 ConfigProvider 动态主题（五主题切换）
  const [gateModal, gateModalContextHolder] = Modal.useModal();

  // AEP-102 规约方案目录（区4 体检报告的方案名/句式模板数据源；目录不可达时降级隐藏模板块）
  const [conventionCatalog, setConventionCatalog] = useState<RequirementConventionCatalogRead | null>(null);
  useEffect(() => {
    let cancelled = false;
    settingsApi
      .listRequirementConventions()
      .then((catalog) => {
        if (!cancelled) {
          setConventionCatalog(catalog);
        }
      })
      .catch(() => {
        // 目录不可达：人话头回落工作区回传的方案名，模板折叠块不渲染
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pushMsg = useCallback((kind: ChatMsg['kind'], text: string, traceLines?: string[]) => {
    msgSeqRef.current += 1;
    setMessages((current) => [
      ...current,
      // 本地发的消息也按服务器视角打时间戳：整条时间线同一个基准，本机时钟偏差不会
      // 让刚发出的一句显示成「N 分钟前」（走查反馈第⑤组）。
      { id: msgSeqRef.current, kind, text, at: serverNowIso(), traceLines },
    ]);
  }, []);

  // ---- 链路回执条（04A §2.1 增补）：阶段=后端事实；停滞=前端派生 ----
  const [trace, setTrace] = useState<AiRequestTrace | null>(null);
  const [traceNow, setTraceNow] = useState(() => Date.now());
  const aiTraceRef = useRef<AiRequestTrace | null>(null);
  const stallProbedRef = useRef(false);
  const updateTrace = useCallback(
    (fn: (current: AiRequestTrace | null) => AiRequestTrace | null) => {
      aiTraceRef.current = fn(aiTraceRef.current);
      setTrace(aiTraceRef.current);
    },
    [],
  );

  useEffect(() => {
    // 在途时每秒重投影（阶段已用时 / 停滞派生）；完成或无链路时停表
    if (!trace || trace.finishedAt !== null) {
      return;
    }
    const timer = window.setInterval(() => setTraceNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [trace]);

  useEffect(() => {
    // 排队停滞升级：超阈值后查一次运行态事实（worker 是否在线），就地给出可行动错误
    const current = aiTraceRef.current;
    if (!current || current.finishedAt !== null || current.stallAlert || stallProbedRef.current) {
      return;
    }
    const stage = traceCurrentStage(current);
    const enteredAt = stage ? current.reached[stage] : undefined;
    if (stage !== 'queued' || enteredAt === undefined || traceNow - enteredAt <= TRACE_STALL_THRESHOLDS_MS.queued) {
      return;
    }
    stallProbedRef.current = true;
    void runtimeStatusApi.getRuntimeStatus().then((status) => {
      const worker = status.components.find((c) => c.key === 'worker');
      if (worker && worker.status !== 'ok' && worker.status !== 'not_applicable') {
        updateTrace((t) => (t ? {
          ...t,
          stallAlert: '执行器（worker）不在线：任务已入队但不会被执行。请启动 worker 或在右上角运行态徽标进入诊断中心。',
        } : t));
      }
    }).catch(() => {
      // 运行态探测失败不打断链路展示
    });
  }, [traceNow, updateTrace]);

  useEffect(() => {
    // 新消息滚到时间线底部
    const el = threadRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, trace]);

  useEffect(() => {
    // StrictMode 双调用：mount 先复位（否则探测卸载置下的 disposed 吞掉全部 watcher）。
    // 定时器清理已由两个 watcher 实例各自的卸载 effect 负责，本处只维护 poll 体内的卸载守卫标志。
    disposedRef.current = false;
    return () => {
      disposedRef.current = true;
    };
  }, []);

  useEffect(() => {
    const nextWorkspace = makeInitialWorkspace(sourceWorkspace, initialWorkspace);
    if (!nextWorkspace) {
      return; // 上游上下文消失由外层空态接管，不清空在途状态
    }
    setWorkspace(nextWorkspace);
    setSelectedElementRefs(nextWorkspace.eligible_elements.map((element) => element.id));
    setSelectedItemRef(nextWorkspace.selected_item_ref ?? nextWorkspace.pending_items[0]?.item_ref ?? null);
  }, [initialWorkspace, sourceWorkspace]);

  const eligibleItems = useMemo(() => mapFormationElements(workspace.eligible_elements), [workspace.eligible_elements]);
  const blockedItems = useMemo(() => mapFormationElements(workspace.blocked_elements), [workspace.blocked_elements]);
  // P7 §1.1 意图背景：确认态 goal/scenario 只读组（不可勾选、不入批次）
  const intentItems = useMemo(
    () => mapFormationElements(workspace.intent_context ?? []),
    [workspace.intent_context],
  );
  const elementGroups = useMemo(() => groupFormationElements(eligibleItems), [eligibleItems]);
  const [expandedGroups, setExpandedGroups] = useState<string[]>([]);
  const pendingItems = useMemo(() => mapPendingItems(workspace.pending_items), [workspace.pending_items]);
  // 区2 确定型分段进度投影（分母=发起时捕获；快照=本批次逐要素归因，只记发起范围内）
  const batchProgress = useMemo(
    () => deriveBatchProgress(batchRun?.total ?? null, batchRun?.results ?? [], batchRun?.scopeRefs ?? null),
    [batchRun],
  );
  const filteredPendingItems = useMemo(
    () => pendingItems.filter((item) => matchesItemCompletenessFilter(item, completenessFilter)),
    [completenessFilter, pendingItems],
  );
  const selectedItem = useMemo(
    () => workspace.pending_items.find((item) => item.item_ref === selectedItemRef) ?? workspace.pending_items[0] ?? null,
    [selectedItemRef, workspace.pending_items],
  );
  const selectedItemVm = selectedItem ? mapPendingItems([selectedItem])[0] : null;
  // 陈述达标投影（条目档案判定；LDM-015 派生，仅提示不作门禁）
  const selectedStructure = useMemo(
    () => mapItemStructureReview(selectedItem?.structure_review),
    [selectedItem],
  );
  // 区4 体检报告投影：方案名/句式模板经 AEP-102 目录按条目锚定方案解析，facet 文案随投影回传
  const healthReport = useMemo(() => {
    if (!selectedStructure || !selectedItemVm) {
      return null;
    }
    const conventionKey = selectedItem?.structure_review?.convention_key ?? workspace.convention_key ?? null;
    const { conventionName, pattern } = resolveConventionPattern(
      conventionKey,
      selectedItemVm.typeText,
      conventionCatalog,
    );
    return deriveStructureHealthReport(
      selectedStructure.review,
      selectedItemVm.typeText,
      conventionName ?? workspace.convention_display_name ?? null,
      pattern,
    );
  }, [conventionCatalog, selectedItem, selectedItemVm, selectedStructure, workspace.convention_display_name, workspace.convention_key]);
  const suggestions = useMemo(
    () => workspace.revision_suggestions.filter((suggestion) => suggestion.item_ref === selectedItem?.item_ref),
    [selectedItem, workspace.revision_suggestions],
  );
  const candidateSuggestions = useMemo(
    () => suggestions.filter((suggestion) => suggestion.status === 'candidate'),
    [suggestions],
  );
  // ④ 区5 时间线：消息与候选建议卡按时刻升序合流。建议卡此前固定钉在消息之后，
  // 新发的消息渲染在它之上，看着像插进了历史中间。没有生成时刻的建议（存量数据）
  // 排在最末视为最新，不用客户端时钟伪造。
  const zone5Timeline = useMemo(
    () =>
      buildZone5Timeline(
        messages,
        resolveCardPositions(
          candidateSuggestions.map((suggestion) => ({
            key: `suggestion-${suggestion.suggestion_ref}`,
            at: suggestion.created_at ?? null,
          })),
          messages.length ? messages[messages.length - 1].at : null,
          cardPositionsRef.current,
        ),
      ),
    [candidateSuggestions, messages],
  );
  const suggestionByTimelineKey = useMemo(
    () => new Map(candidateSuggestions.map((s) => [`suggestion-${s.suggestion_ref}`, s])),
    [candidateSuggestions],
  );
  const sourceElementsById = useMemo(() => mapSourceElementsById(workspace), [workspace]);
  const anchors = useMemo(() => resolveFormationAnchors(workspace), [workspace]);
  const canvasBlocks = useMemo(
    () => buildItemSourceCanvas(workspace, selectedItem, anchors),
    [anchors, selectedItem, workspace],
  );

  useEffect(() => {
    if (!selectedItem) {
      setRevisionValue('');
      return;
    }
    setRevisionValue(itemFieldValue(selectedItem, revisionField));
  }, [revisionField, selectedItem]);

  // ---- 三区联动定位：区1 要素行 ↔ 区3 来源组 ↔ 区5 条目行（点击任一处，三处高亮 + 滚动容器居中）----
  const [linkedElementRef, setLinkedElementRef] = useState<string | null>(null);
  const [linkFocus, setLinkFocus] = useState<{ itemRef: string | null; elementRef: string | null; nonce: number } | null>(null);
  const elementListRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLElement | null>(null);
  const itemListRef = useRef<HTMLDivElement | null>(null);

  const locateLinkage = useCallback(
    (elementRef: string | null, itemRef?: string | null) => {
      let targetItemRef = itemRef ?? null;
      if (!targetItemRef && elementRef) {
        targetItemRef =
          workspace.pending_items.find(
            (item) =>
              item.status === 'pending_confirmation' && (item.source_element_refs ?? []).includes(elementRef),
          )?.item_ref ??
          workspace.pending_items.find((item) => (item.source_element_refs ?? []).includes(elementRef))?.item_ref ??
          null;
      }
      let targetElementRef = elementRef;
      if (!targetElementRef && targetItemRef) {
        targetElementRef =
          workspace.pending_items.find((item) => item.item_ref === targetItemRef)?.source_element_refs?.[0] ?? null;
      }
      if (targetItemRef) {
        setSelectedItemRef(targetItemRef);
        const targetVm = pendingItems.find((item) => item.itemRef === targetItemRef);
        if (targetVm && !matchesItemCompletenessFilter(targetVm, completenessFilter)) {
          setCompletenessFilter('all'); // 目标被达标度筛选隐藏时回落「全部」，保证区5 可见
        }
      }
      if (targetElementRef) {
        const group = elementGroups.find((g) => g.items.some((item) => item.id === targetElementRef));
        if (group && group.items.findIndex((item) => item.id === targetElementRef) >= 5) {
          setExpandedGroups((current) => (current.includes(group.key) ? current : [...current, group.key]));
        }
      }
      setLinkedElementRef(targetElementRef);
      setLinkFocus({ itemRef: targetItemRef, elementRef: targetElementRef, nonce: Date.now() });
    },
    [completenessFilter, elementGroups, pendingItems, workspace.pending_items],
  );

  useEffect(() => {
    if (!linkFocus) {
      return;
    }
    // 等状态更新（筛选回落/分组展开/选中切换）渲染后再滚动
    const frame = requestAnimationFrame(() => {
      const centerIn = (container: HTMLElement | null, target: Element | null) => {
        if (!container || !(target instanceof HTMLElement)) {
          return;
        }
        const c = container.getBoundingClientRect();
        const t = target.getBoundingClientRect();
        // 垂直清单居中滚动；区5 折叠态横向队列条同时水平居中（纵向容器 left 变化趋近 0，无副作用）
        container.scrollTo({
          top: container.scrollTop + (t.top - c.top) - c.height / 2 + t.height / 2,
          left: container.scrollLeft + (t.left - c.left) - c.width / 2 + t.width / 2,
          behavior: 'smooth',
        });
      };
      if (linkFocus.elementRef) {
        const esc = CSS.escape(linkFocus.elementRef);
        centerIn(elementListRef.current, elementListRef.current?.querySelector(`[data-element-ref="${esc}"]`) ?? null);
        centerIn(canvasRef.current, canvasRef.current?.querySelector(`[data-refs~="${esc}"]`) ?? null);
      }
      if (linkFocus.itemRef) {
        centerIn(
          itemListRef.current,
          itemListRef.current?.querySelector(`[data-item-ref="${CSS.escape(linkFocus.itemRef)}"]`) ?? null,
        );
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [linkFocus]);

  // 批次流式入列滚动可见：在途批次新条目追加在清单尾部，展开清单滚到底、折叠队列条滚到最右
  const prevPendingCountRef = useRef(0);
  useEffect(() => {
    const grew = pendingItems.length > prevPendingCountRef.current;
    prevPendingCountRef.current = pendingItems.length;
    if (!grew || batchStatus !== 'running') {
      return;
    }
    const el = itemListRef.current;
    if (el) {
      el.scrollTo({ top: el.scrollHeight, left: el.scrollWidth, behavior: 'smooth' });
    }
  }, [batchStatus, pendingItems.length]);

  const toggleElement = useCallback((elementRef: string) => {
    setSelectedElementRefs((current) =>
      current.includes(elementRef)
        ? current.filter((id) => id !== elementRef)
        : [...current, elementRef],
    );
  }, []);

  const applyWorkspace = useCallback((next: ItemFormationWorkspaceRead) => {
    setWorkspace(next);
    const eligibleIds = new Set(next.eligible_elements.map((e) => e.id));
    setSelectedElementRefs((current) => current.filter((id) => eligibleIds.has(id)));
    setSelectedItemRef((current) => {
      if (current && next.pending_items.some((i) => i.item_ref === current)) {
        return current;
      }
      return next.selected_item_ref ?? next.pending_items[0]?.item_ref ?? null;
    });
  }, []);

  const refreshWorkspace = useCallback(async (formationContextRef: string) => {
    const next = await itemFormationApi.getWorkspace(projectId, formationContextRef);
    applyWorkspace(next);
    return next;
  }, [applyWorkspace, projectId]);

  const watchBatchRun = useCallback(
    (runId: string, formationContextRef: string, scopeRefs?: readonly string[] | null) => {
      const scope = scopeRefs ? new Set(scopeRefs) : null;
      const inScope = (results: ItemizationResultRead[]) =>
        scope ? results.filter((r) => scope.has(r.element_ref)) : results;
      let tick = 0;
      // poll 返回 RunPollTick 交 batchWatcher：非终态→{done:false}（hook 续表 800ms）；终态/错误→{done:true}
      // （hook 停表）。disposedRef 守卫沿用（poll 体内多次 await 期间卸载后不 setState）。
      const poll = async (): Promise<RunPollTick> => {
        try {
          const run = await agentRunApi.get(runId);
          if (disposedRef.current) {
            return { done: true }; // 组件已卸载：不再 setState、停表
          }
          if (run.status === 'started') {
            updateTrace((t) => (t && t.finishedAt === null ? traceAdvance(t, 'running', Date.now()) : t));
          }
          if (!TERMINAL_RUN_STATUSES.has(run.status)) {
            // 逐要素落库：批次在途也定期刷新工作区，条目逐条实时入流（对齐条目评审）
            tick += 1;
            if (run.status === 'started' && tick % 3 === 0) {
              try {
                const partial = await refreshWorkspace(formationContextRef);
                // 区2 分段进度条随逐条返回推进（快照=本批次上下文的归因，不混上一批次残留）
                setBatchRun((prev) =>
                  prev?.phase === 'running' ? { ...prev, results: partial.batch_results } : prev,
                );
              } catch {
                // 中途刷新失败不影响批次轮询
              }
              if (disposedRef.current) {
                return { done: true };
              }
            }
            return { done: false };
          }
          if (run.status === 'failed' || run.status === 'cancelled') {
            const errorText =
              run.status === 'cancelled'
                ? '条目化批次已取消，可重新发起'
                : run.error || '条目化批次执行失败，可重试';
            setBatchRun((prev) => ({
              phase: 'failed',
              total: prev?.total ?? null,
              scopeRefs: prev?.scopeRefs ?? null,
              results: prev?.results ?? [],
              error: errorText,
            }));
            updateTrace((t) => (t && t.finishedAt === null ? traceFinish(t, 'failed', Date.now()) : t));
            pushMsg('sys-warn', `${errorText}。`);
            return { done: true };
          }
          const next = await refreshWorkspace(formationContextRef);
          if (disposedRef.current) {
            return { done: true };
          }
          setBatchRun((prev) => ({
            phase: 'succeeded',
            total: prev?.total ?? null,
            scopeRefs: prev?.scopeRefs ?? null,
            results: next.batch_results,
            error: null,
          }));
          setNoticeText(next.next_action ?? null);
          setSelectedItemRef(next.selected_item_ref ?? next.pending_items[0]?.item_ref ?? null);
          // 完成摘要与进度条同口径：只统计发起范围内的归因（范围外事实见「批次结果」）
          const scopedResults = inScope(next.batch_results);
          const created = scopedResults.filter((r) => r.result_status === 'created').length;
          const blockedCount = scopedResults.length - created;
          const active = aiTraceRef.current;
          const dockedText = blockedCount > 0 ? `、${blockedCount} 条未形成（原因见「批次结果」）` : '';
          if (active && active.finishedAt === null) {
            const done = traceFinish(traceAdvance(active, 'writing', Date.now()), 'done', Date.now());
            updateTrace(() => null); // 完成收敛：链路条塌缩进回执
            pushMsg(
              'sys-ok',
              `条目化批次完成：创建 ${created} 条${dockedText} · ${traceSummaryText(done)}`,
              traceDetailLines(done),
            );
          } else {
            pushMsg('sys-ok', `条目化批次完成：创建 ${created} 条${dockedText}。`);
          }
          return { done: true };
        } catch (error) {
          if (disposedRef.current) {
            return { done: true };
          }
          setBatchRun((prev) => ({
            phase: 'failed',
            total: prev?.total ?? null,
            scopeRefs: prev?.scopeRefs ?? null,
            results: prev?.results ?? [],
            error: getErrorMessage(error),
          }));
          updateTrace((t) => (t && t.finishedAt === null ? traceFinish(t, 'failed', Date.now()) : t));
          return { done: true };
        }
      };
      startBatchWatch(poll, { immediate: true });
    },
    [startBatchWatch, pushMsg, refreshWorkspace, updateTrace],
  );

  // ---- AEP-114 结构复核 AgentRun 跟踪：在途每 3 拍刷新工作区（逐条目 commit，徽标实时归位），
  // 终态后取批次逐条目结局回执（AEP-114 读侧事实：已重判 / 修订在飞已过期跳过 / 失败保留旧判；
  // 读侧不可达时回落按目标集徽标派生）。
  // silent=内容变更链式自动体检（走查第三轮裁定）：成功零消息（徽标自行翻新），失败给修复提示。
  // ownsBusy=本 watcher 置过 recheckBusy（谁置谁清，issue #8 缺陷 8）。
  const watchRecheckRun = useCallback(
    (
      runId: string, formationContextRef: string, targetRefs: readonly string[],
      opts?: { silent?: boolean; ownsBusy?: boolean; recheckContextRef?: string | null },
    ) => {
      const releaseBusy = () => {
        if (opts?.ownsBusy) {
          setRecheckBusy(false);
        }
      };
      let tick = 0;
      // poll 返回 RunPollTick 交并发池 startRecheckWatch：每次发起各持一路独立循环，同类型复核共存、
      // 互不抢占，故本路的终态 releaseBusy 必被跑到（对齐 issue #8 缺陷 8「谁置谁清」，见上方 hook 注释）。
      const poll = async (): Promise<RunPollTick> => {
        try {
          const run = await agentRunApi.get(runId);
          if (disposedRef.current) {
            return { done: true }; // 组件已卸载：不再 setState、停表
          }
          if (run.status === 'started' && !opts?.silent) {
            updateTrace((t) => (t && t.finishedAt === null ? traceAdvance(t, 'running', Date.now()) : t));
          }
          if (!TERMINAL_RUN_STATUSES.has(run.status)) {
            tick += 1;
            if (run.status === 'started' && tick % 3 === 0) {
              try {
                await refreshWorkspace(formationContextRef);
              } catch {
                // 中途刷新失败不影响复核轮询
              }
              if (disposedRef.current) {
                return { done: true };
              }
            }
            return { done: false };
          }
          releaseBusy();
          if (run.status === 'failed' || run.status === 'cancelled') {
            if (!opts?.silent) {
              updateTrace((t) => (t && t.finishedAt === null ? traceFinish(t, 'failed', Date.now()) : t));
            }
            pushMsg(
              'sys-warn',
              run.status === 'cancelled'
                ? '结构复核已取消。旧体检结果保留原样，可用区2「复核」重新发起。'
                : `结构体检执行失败：${run.error ?? '可重试'}。旧体检结果保留原样，可用区2「复核」重试。`,
            );
            return { done: true };
          }
          const next = await refreshWorkspace(formationContextRef);
          if (disposedRef.current) {
            return { done: true };
          }
          // 回执事实源：批次逐条目结局（信封账目）；读侧不可达时回落徽标派生
          let outcomeRead: StructureRecheckOutcomeRead | null = null;
          if (opts?.recheckContextRef) {
            try {
              outcomeRead = await itemFormationApi.getStructureRecheckOutcome(
                projectId, opts.recheckContextRef,
              );
            } catch {
              // 读侧失败：回落派生口径
            }
            if (disposedRef.current) {
              return { done: true };
            }
          }
          let refreshed: number;
          let expired = 0;
          let failed: number;
          let skipped = 0;
          let pending = 0;
          if (outcomeRead) {
            // 合并裁定修复 K6/K4：信封账目是整批口径，复用在途批时须与本次请求目标求交，
            // 否则 1 条请求会显示整批（如 10 条）计数；同时补 skipped/pending 两集合，
            // 否则单条被跳过会伪装成绿色「0 条已刷新」的成功回执。
            const targets = new Set(targetRefs);
            const inScope = (refs: string[]) => refs.filter((r) => targets.has(r)).length;
            refreshed = inScope(outcomeRead.refreshed_refs);
            expired = inScope(outcomeRead.expired_skipped_refs);
            failed = inScope(outcomeRead.failed_refs);
            skipped = inScope(outcomeRead.skipped_refs);
            pending = inScope(outcomeRead.pending_refs);
          } else {
            const targets = new Set(targetRefs);
            const after = mapPendingItems(next.pending_items).filter((i) => targets.has(i.itemRef));
            failed = after.filter(
              (i) => i.statusText === '待确认' && effectiveCompletenessKey(i) === null,
            ).length;
            refreshed = targetRefs.length - failed;
          }
          if (opts?.silent) {
            // 链式自动体检：成功零消息（徽标自行翻新判定）；过期跳过=新表达的链式批
            // 会自行跟进，同样保持静默；个别失败给修复提示
            if (failed > 0) {
              pushMsg('sys-warn', `${failed} 条条目的体检未能自动刷新（旧结果保留原样），可用区2「复核」重试。`);
            }
            return { done: true };
          }
          const active = aiTraceRef.current;
          const done = active && active.finishedAt === null
            ? traceFinish(traceAdvance(active, 'writing', Date.now()), 'done', Date.now())
            : null;
          updateTrace(() => null); // 完成收敛：链路条塌缩进回执
          const parts: string[] = [`${refreshed} 条已刷新为当前表达`];
          if (expired > 0) {
            parts.push(`${expired} 条在复核期间被再次修订，本次判定已过期跳过（新表达会自动重新体检，也可再次复核）`);
          }
          if (failed > 0) {
            parts.push(`${failed} 条复核失败（旧结果保留原样，可重试）`);
          }
          if (skipped > 0) {
            parts.push(`${skipped} 条已离开待确认状态，未参与本次复核`);
          }
          if (pending > 0) {
            parts.push(`${pending} 条仍在复核中`);
          }
          if (expired === 0 && failed === 0 && skipped === 0 && pending === 0) {
            pushMsg(
              'sys-ok',
              `复核完成：${refreshed} 条条目的体检结果已刷新为当前表达${done ? ` · ${traceSummaryText(done)}` : '。'}`,
              done ? traceDetailLines(done) : undefined,
            );
          } else {
            pushMsg('sys-warn', `复核完成：${parts.join('；')}。`);
          }
          return { done: true };
        } catch (error) {
          if (disposedRef.current) {
            return { done: true };
          }
          releaseBusy();
          if (!opts?.silent) {
            updateTrace((t) => (t && t.finishedAt === null ? traceFinish(t, 'failed', Date.now()) : t));
          }
          pushMsg('sys-warn', getErrorMessage(error));
          return { done: true };
        }
      };
      startRecheckWatch(poll, { immediate: true });
    },
    [projectId, startRecheckWatch, pushMsg, refreshWorkspace, updateTrace],
  );

  const parseResultRef = workspace.parse_result_ref ?? sourceWorkspace?.parse_result_ref ?? null;

  // 找回既有批次：从分析页进入（无 initialWorkspace）时按解析结果回放最近一次形成工作区，
  // 避免已形成的待确认条目被呈现为 0 条而误导重复生成批次。
  useEffect(() => {
    if (initialWorkspace || !parseResultRef) {
      return;
    }
    let disposed = false;
    void itemFormationApi
      .getWorkspaceByParseResult(projectId, parseResultRef)
      .then((next) => {
        if (!disposed) {
          applyWorkspace(next);
        }
      })
      .catch(() => {
        // 无既有批次（404）：保持由上游要素工作区构建的初始形态
      });
    return () => {
      disposed = true;
    };
  }, [applyWorkspace, initialWorkspace, parseResultRef, projectId]);

  // 演示留痕水合：进入/切换上下文（parse_result_ref）时从服务端拉留痕行水合区5。
  // 效果仅随 parseResultRef 变化重跑（切上下文才重拉）。合并按留痕行 id 去重（裁定 F8）——
  // 旧的 `current.length ? current : rows` 是全有全无，用户抢发一条即丢掉整段历史。
  // 不设「一次性」ref 门——StrictMode 双调用会先 cancel 首个 fetch，若用 ref 门挡住第二次调用则永不水合
  // （strictmode-effect-consume-guard 事故范式）。条目形成页 ChatMsg 含 'ai' 类，语气 1:1。
  useEffect(() => {
    cardPositionsRef.current.clear(); // 换上下文＝换一段对话，卡片落位重来
  }, [parseResultRef]);

  useEffect(() => {
    if (!parseResultRef) return;
    let cancelled = false;
    void fetchChatTranscript(projectId, 'formation', parseResultRef)
      .then((res) => {
        if (cancelled || !res.rows.length) return;
        // 序号在更新函数之外算（更新函数须无副作用，裁定 F8 顺带项）
        const hydrated: ChatMsg[] = res.rows.map((r) => {
          msgSeqRef.current += 1;
          const b = transcriptRowToBubble(r);
          return { id: msgSeqRef.current, kind: b.tone as ChatMsg['kind'], text: b.text, at: b.at, sourceId: r.id };
        });
        setMessages((current) => mergeHydratedMessages(current, hydrated));
      })
      .catch((error) => {
        // 不再静默吞（裁定 N7）：留痕读失败与「本来就没有历史」在界面上原本无从区分
        console.warn('[formation] 历史消息读取失败', error);
        if (!cancelled) {
          setNoticeText('历史消息读取失败，可刷新重试。');
        }
      });
    return () => { cancelled = true; };
  }, [parseResultRef, projectId]);

  // ---- 区2：生成待确认条目（AEP-038 结构化直发；勾选集=批次范围）----

  const handleStartBatch = useCallback(async () => {
    if (!parseResultRef) {
      setNoticeText('当前工作区未接入已解析要素集合，无法发起条目化批次。');
      pushMsg('sys-warn', '当前工作区未接入已解析要素集合，无法发起条目化批次。');
      return;
    }
    const eligibleIds = workspace.eligible_elements.map((element) => element.id);
    const scope: ItemizationScopeType =
      selectedElementRefs.length === eligibleIds.length
        ? 'all_eligible'
        : selectedElementRefs.length === 1
          ? 'single_element'
          : 'selected_elements';
    // 进度条分母＝发起时选中的要素数，在此一次性捕获入状态（禁用假分母）；
    // 同时捕获范围要素 id，进度账目只统计范围内归因（与勾选集自洽）
    const launchRefs = scope === 'all_eligible' ? eligibleIds : [...selectedElementRefs];
    const launchTotal = launchRefs.length;
    setBatchRun({ phase: 'running', total: launchTotal, scopeRefs: launchRefs, results: [], error: null });
    setNoticeText(null);
    pushMsg('cmd', `生成待确认条目（${scope === 'all_eligible' ? `全部可条目化 ${eligibleIds.length} 条` : `已选 ${selectedElementRefs.length} 条`}）`);
    stallProbedRef.current = false;
    updateTrace(() => traceExtendQueue(createTrace(Date.now()), null, Date.now()));
    try {
      const result = await itemFormationApi.startBatch(projectId, {
        project_ref: projectId,
        parse_result_ref: parseResultRef,
        workspace_version: workspace.workspace_version,
        scope_type: scope,
        target_element_refs: scope === 'all_eligible' ? [] : selectedElementRefs,
        operator_ref: operatorRef || 'current-user',
        idempotency_key: createIdempotencyKey(),
      });
      const followup = resolveBatchSubmitFollowup(result);
      if (followup.kind === 'rejected') {
        setBatchRun(null);
        setNoticeText(followup.notice);
        updateTrace((t) => (t ? traceFinish(t, 'clarify', Date.now()) : t));
        pushMsg('sys-warn', followup.notice);
        return;
      }
      if (followup.kind === 'reattach') {
        // HK-1 复用在途：后端返回原批次 run，沿用 watchBatchRun 复挂轮询，不报错。
        // 原批次的发起范围/分母不可知（failure_policy）：进度降级为「已返回 N 条」不定型，不造假分母。
        setBatchRun({ phase: 'running', total: null, scopeRefs: null, results: [], error: null });
        setNoticeText(followup.notice);
        pushMsg('sys-ok', followup.notice);
        watchBatchRun(followup.runId, followup.contextRef, null);
        return;
      }
      if (followup.kind === 'watch') {
        watchBatchRun(followup.runId, followup.contextRef, launchRefs);
        return;
      }
      const next = await refreshWorkspace(followup.contextRef);
      setBatchRun({ phase: 'succeeded', total: launchTotal, scopeRefs: launchRefs, results: next.batch_results, error: null });
      setNoticeText(next.next_action ?? null);
      updateTrace(() => null);
    } catch (error) {
      setBatchRun((prev) => ({
        phase: 'failed',
        total: prev?.total ?? null,
        scopeRefs: prev?.scopeRefs ?? null,
        results: prev?.results ?? [],
        error: getErrorMessage(error),
      }));
      updateTrace((t) => (t ? traceFinish(t, 'failed', Date.now()) : t));
      pushMsg('sys-warn', getErrorMessage(error));
    }
  }, [operatorRef, parseResultRef, projectId, pushMsg, refreshWorkspace, selectedElementRefs, updateTrace, watchBatchRun, workspace]);

  // ---- 区2：批量复核（AEP-114 结构化直发；确认弹层报计数，权威目标集由后端受理时重算）----

  const submitRecheck = useCallback(
    async (targetRefs: readonly string[], countsText: string) => {
      const contextRef = workspace.formation_context_ref;
      if (!parseResultRef || !contextRef) {
        return;
      }
      setRecheckBusy(true);
      pushMsg('cmd', `批量复核（${countsText}）`);
      stallProbedRef.current = false;
      updateTrace(() => traceExtendQueue(createTrace(Date.now()), null, Date.now()));
      try {
        const result = await itemFormationApi.startStructureRecheck(projectId, {
          project_ref: projectId,
          parse_result_ref: parseResultRef,
          workspace_version: workspace.workspace_version,
          item_refs: [], // 空=默认目标集：待确认∩（修订后未复核∪无体检结果），后端权威重算
          operator_ref: operatorRef || 'current-user',
          idempotency_key: createIdempotencyKey(),
        });
        if (result.status === 'in_flight' && result.agent_run_ref) {
          pushMsg('sys-ok', result.next_action ?? '结构复核执行中：已恢复进度跟踪。');
          updateTrace((t) => (t ? traceExtendQueue(t, result.agent_run_ref ?? null, Date.now()) : t));
          watchRecheckRun(result.agent_run_ref, contextRef, targetRefs, {
            ownsBusy: true, recheckContextRef: result.recheck_context_ref,
          });
          return;
        }
        if (result.status !== 'submitted' || !result.agent_run_ref) {
          setRecheckBusy(false);
          updateTrace((t) => (t ? traceFinish(t, 'clarify', Date.now()) : t));
          pushMsg('sys-warn', result.next_action ?? '复核未受理，请刷新后重试。');
          return;
        }
        updateTrace((t) => (t ? traceExtendQueue(t, result.agent_run_ref ?? null, Date.now()) : t));
        // 计数与实际入队一致（issue #8 缺陷 3）：以受理回传的目标集为准
        watchRecheckRun(
          result.agent_run_ref, contextRef,
          result.target_item_refs?.length ? result.target_item_refs : targetRefs,
          { ownsBusy: true, recheckContextRef: result.recheck_context_ref },
        );
      } catch (error) {
        setRecheckBusy(false);
        updateTrace((t) => (t ? traceFinish(t, 'failed', Date.now()) : t));
        pushMsg('sys-warn', getErrorMessage(error));
      }
    },
    [operatorRef, parseResultRef, projectId, pushMsg, updateTrace, watchRecheckRun, workspace],
  );

  const handleRecheck = useCallback(() => {
    const targets = deriveRecheckTargets(pendingItems);
    if (!targets) {
      return;
    }
    gateModal.confirm({
      title: `将复核 ${targets.countsText}的条目`,
      content: (
        <div className="if-review-gate">
          <p>正常流程会在修订后自动体检；此处为修复通道（自动体检失败或执行器不可用时）。</p>
          <p>AI 将按句式档案对这些条目的当前表达重新体检，只更新体检结果，不改条目内容。</p>
        </div>
      ),
      okText: '发起复核',
      cancelText: '取消',
      onOk: () => void submitRecheck(targets.targetRefs, targets.countsText),
    });
  }, [gateModal, pendingItems, submitRecheck]);

  const applyRevision = useCallback(
    async (overrides?: Partial<ItemRevisionCommand>) => {
      if (!selectedItem) {
        return;
      }
      if (!workspace.formation_context_ref) {
        setNoticeText('尚未发起后端条目化批次，无法字段修订。');
        return;
      }
      const mode: ItemRevisionMode = overrides?.revision_mode ?? 'manual';
      setRevisionBusy(true);
      try {
        const result = await requirementsApi.applyItemRevision(projectId, selectedItem.item_ref, {
          project_ref: projectId,
          item_ref: selectedItem.item_ref,
          workspace_version: workspace.workspace_version,
          revision_mode: mode,
          field_key: overrides?.field_key ?? revisionField,
          revised_value: overrides?.revised_value ?? revisionValue,
          suggestion_ref: overrides?.suggestion_ref ?? null,
          accept_mode: overrides?.accept_mode ?? null,
          reason: overrides?.reason ?? (revisionReason || null),
          operator_ref: operatorRef || 'current-user',
          idempotency_key: createIdempotencyKey(),
        });
        setNoticeText(result.next_action ?? null);
        pushMsg(
          result.status === 'applied' ? 'sys-ok' : 'sys-warn',
          result.next_action ?? (result.status === 'applied' ? '修订已应用。' : '修订未被承接。'),
        );
        await refreshWorkspace(workspace.formation_context_ref);
        // 走查第三轮裁定：内容修订链式自动体检——静默跟踪，完成后徽标自行翻新判定
        if (result.structure_recheck_run_ref && selectedItem) {
          watchRecheckRun(
            result.structure_recheck_run_ref, workspace.formation_context_ref,
            [selectedItem.item_ref],
            { silent: true, recheckContextRef: result.structure_recheck_context_ref },
          );
        }
      } catch (error) {
        setNoticeText(getErrorMessage(error));
        pushMsg('sys-warn', getErrorMessage(error));
      } finally {
        setRevisionBusy(false);
      }
    },
    [operatorRef, projectId, pushMsg, refreshWorkspace, revisionField, revisionReason, revisionValue, selectedItem, watchRecheckRun, workspace],
  );

  // 区4 属性缺失行「字段修订」入口：展开既有修订表单并选中该字段（提交仍走 AEP-036）
  const openFieldRevision = useCallback(
    (field: RevisionField) => {
      if (!selectedItem) {
        return;
      }
      setRevisionFormOpen(true);
      setRevisionField(field);
      setRevisionValue(itemFieldValue(selectedItem, field));
      setRevisionReason('');
    },
    [selectedItem],
  );

  // 区4 体检报告「让 AI 起草补写」：预填区5 输入框 /修订 补写〔成分名〕： 并聚焦（复用命令预填机制，不直发）
  const prefillGapDraft = useCallback((facetLabel: string) => {
    setTypePickerOpen(false);
    setMergePickerOpen(false);
    setComposerText(FORMATION_QUICK_COMMAND_PREFILLS.reviseGap(facetLabel));
    composerRef.current?.focus();
  }, []);


  // ---- 区5：快捷命令预填 + 发送（整段原文交 AEP-097，前端不解析命令词）----

  const quickFill = useCallback((text: string) => {
    setTypePickerOpen(false);
    setMergePickerOpen(false);
    setComposerText(text);
  }, []);

  const handleSend = useCallback(() => {
    const text = composerText.trim();
    if (!text) {
      return;
    }
    if (!parseResultRef) {
      pushMsg('sys-warn', '当前工作区未接入已解析要素集合，无法对话。');
      return;
    }
    setComposerText('');
    const isCommand = text.startsWith('/') || text.startsWith('／');
    pushMsg(isCommand ? 'cmd' : 'user', text);
    stallProbedRef.current = false;
    updateTrace(() => createTrace(Date.now()));
    setDialogueBusy(true);
    void (async () => {
      let result;
      try {
        result = await itemFormationApi.sendDialogueStream(projectId, {
          project_ref: projectId,
          parse_result_ref: parseResultRef,
          formation_context_ref: workspace.formation_context_ref || null,
          workspace_version: workspace.workspace_version,
          message: text,
          item_ref: selectedItem?.item_ref ?? null,
          selected_element_refs: selectedElementRefs,
          operator_ref: operatorRef || 'current-user',
          idempotency_key: createIdempotencyKey(),
        }, {
          onStage: (stage) =>
            updateTrace((t) => (t ? traceAdvance(t, stage as TraceStage, Date.now()) : t)),
        });
      } catch (error) {
        // 流内错误帧 / 网络中断：链路条固定于失败节点（保留至下次发送），回执就地展示原因
        updateTrace((t) => (t ? traceFinish(t, 'failed', Date.now()) : t));
        pushMsg('sys-warn', getErrorMessage(error));
        setDialogueBusy(false);
        return;
      }
      setDialogueBusy(false);
      const echo = result.operation_label ? `［${result.operation_label}］` : '';
      switch (result.outcome) {
        case 'executed': {
          if (result.workspace) {
            applyWorkspace(result.workspace);
          } else if (workspace.formation_context_ref) {
            await refreshWorkspace(workspace.formation_context_ref);
          }
          if (result.created_item_refs?.length) {
            setSelectedItemRef(result.created_item_refs[0]);
          }
          // 走查第三轮裁定：修订/拆分/归并的链式自动体检——静默跟踪，徽标自行翻新判定
          if (result.structure_recheck_run_ref && workspace.formation_context_ref) {
            watchRecheckRun(
              result.structure_recheck_run_ref, workspace.formation_context_ref,
              result.created_item_refs?.length
                ? result.created_item_refs
                : selectedItem ? [selectedItem.item_ref] : [],
              { silent: true, recheckContextRef: result.structure_recheck_context_ref },
            );
          }
          const done = traceFinish(
            aiTraceRef.current ?? createTrace(Date.now()), 'done', Date.now(),
            { operationLabel: result.operation_label ?? undefined },
          );
          updateTrace(() => null); // 完成收敛：链路条塌缩进回执
          pushMsg(
            'sys-ok',
            `${echo}${result.message ?? '已执行（留痕见区4 修订记录）。'} · ${traceSummaryText(done)}`,
            traceDetailLines(done),
          );
          return;
        }
        case 'queued': {
          if (result.operation === 'structure.recheck') {
            // /复核 队列支（AEP-114）：不进条目化批次进度语义，走复核跟踪——完成后徽标归位
            if (result.message) {
              pushMsg('sys-ok', result.message);
            }
            const recheckCtx = result.formation_context_ref ?? workspace.formation_context_ref;
            if (result.agent_run_ref && recheckCtx) {
              setRecheckBusy(true);
              updateTrace((t) => (t ? traceExtendQueue(
                { ...t, operationLabel: result.operation_label ?? t.operationLabel },
                result.agent_run_ref ?? null, Date.now(),
              ) : t));
              watchRecheckRun(
                result.agent_run_ref, recheckCtx,
                selectedItem ? [selectedItem.item_ref] : [],
                { ownsBusy: true, recheckContextRef: result.structure_recheck_context_ref },
              );
            } else {
              updateTrace(() => null);
            }
            return;
          }
          // 队列支（/生成条目）：链路条扩展排队/执行/回写节点，由 AgentRun 状态继续点灯。
          // 进度分母按后端回显的批次范围取：selected=勾选数、all=可条目化数；回显缺失则降级不定型。
          const echoScope = (result.params_echo as { scope?: string } | null | undefined)?.scope;
          const queuedRefs =
            echoScope === 'selected'
              ? [...selectedElementRefs]
              : echoScope === 'all'
                ? workspace.eligible_elements.map((e) => e.id)
                : null;
          const queuedTotal = queuedRefs?.length ?? null;
          setBatchRun({ phase: 'running', total: queuedTotal, scopeRefs: queuedRefs, results: [], error: null });
          updateTrace((t) => (t ? traceExtendQueue(
            { ...t, operationLabel: result.operation_label ?? t.operationLabel },
            result.agent_run_ref ?? null, Date.now(),
          ) : t));
          const contextRef = result.formation_context_ref ?? workspace.formation_context_ref;
          if (result.agent_run_ref && contextRef) {
            watchBatchRun(result.agent_run_ref, contextRef, queuedRefs);
          } else if (contextRef) {
            const next = await refreshWorkspace(contextRef);
            setBatchRun({ phase: 'succeeded', total: queuedTotal, scopeRefs: queuedRefs, results: next.batch_results, error: null });
          }
          return;
        }
        case 'draft': {
          if (result.workspace) {
            applyWorkspace(result.workspace);
          }
          const done = traceFinish(
            aiTraceRef.current ?? createTrace(Date.now()), 'done', Date.now(),
            { operationLabel: result.operation_label ?? undefined },
          );
          updateTrace(() => null);
          pushMsg(
            'sys-ok',
            `${echo}已起草修订建议（候选，未采纳零副作用）——见下方建议卡 · ${traceSummaryText(done)}`,
            traceDetailLines(done),
          );
          return;
        }
        case 'explanation': {
          const done = traceFinish(aiTraceRef.current ?? createTrace(Date.now()), 'done', Date.now());
          updateTrace(() => null);
          pushMsg('ai', result.explanation ?? '（无解释内容）', traceDetailLines(done));
          return;
        }
        default: {
          // clarify / cannot_comply / unknown_command / rejected_precheck：回执文案直接入流
          const outcome = result.outcome === 'cannot_comply' ? 'refused' : 'clarify';
          const settled = traceFinish(
            aiTraceRef.current ?? createTrace(Date.now()), outcome, Date.now(),
          );
          updateTrace(() => null);
          pushMsg(
            'sys-warn',
            result.message ?? '命令未被受理，请调整后重试。',
            settled.reached.accepted !== undefined ? traceDetailLines(settled) : undefined,
          );
          return;
        }
      }
    })();
  }, [applyWorkspace, composerText, operatorRef, parseResultRef, projectId, pushMsg, refreshWorkspace, selectedElementRefs, selectedItem, updateTrace, watchBatchRun, watchRecheckRun, workspace]);

  // ---- 门禁事实（后端 ActionFact；禁用原因就地展示，前端不自算领域门禁）----

  const opFact = useCallback(
    (key: string) => workspace.available_operations.find((a) => a.key === key) ?? null,
    [workspace.available_operations],
  );
  const actionFact = useCallback(
    (key: string) => workspace.available_actions.find((a) => a.key === key) ?? null,
    [workspace.available_actions],
  );
  const startFact = opFact('start_itemization');
  const reviseFact = opFact('apply_revision');
  const reviewFact = actionFact('start_review');
  // 条目级修订门禁（后端逐条目 ActionFact）：工作区级 apply_revision 只表达"存在待确认条目"，
  // 选中已确认/终止条目时建议卡与修订表单必须按条目级事实禁用——否则采纳/拒绝直发 AEP-036
  // 被状态机默认拒绝，用户看到裸 409（用户走查 2026-07-11 报告）
  const itemReviseFact =
    selectedItem?.available_actions?.find((a) => a.key === 'apply_revision') ?? null;
  const isBusy = batchStatus === 'running' || revisionBusy || dialogueBusy || recheckBusy;
  const canStartBatch =
    selectedElementRefs.length > 0 && batchStatus !== 'running' && !recheckBusy && Boolean(startFact?.enabled);
  // 区2 批量复核门禁（裁定 3）：目标集为空时禁用并 title 说明；批次/复核在途互斥
  const recheckTargets = useMemo(() => deriveRecheckTargets(pendingItems), [pendingItems]);
  const canRecheck = Boolean(
    recheckTargets && workspace.formation_context_ref && parseResultRef,
  ) && !recheckBusy && batchStatus !== 'running';
  const canRevise = Boolean(
    selectedItem && !revisionBusy && reviseFact?.enabled && itemReviseFact?.enabled,
  );
  // 区5 条目级命令 pill 门禁：非待确认条目（确认/终止）表达与判定已冻结，改写与复核类
  // 命令入口必须灰掉并就地说明——不能让徽标/pill 引导用户点出一句拒绝（用户走查第二轮）。
  // /问来源（只读）与 /引用依据（确认态直接成有效边，P7 允许）不受此门禁。
  const selectedItemPending = selectedItemVm?.statusText === '待确认';
  const frozenItemTitle = selectedItem && !selectedItemPending
    ? '条目已离开待确认，表达与判定已冻结；可用 /问来源 查来源'
    : undefined;
  // 禁用原因就地展示：条目级原因（如「仅待确认条目可字段修订」）优先于工作区级
  const reviseDisabledReason =
    (itemReviseFact && !itemReviseFact.enabled ? itemReviseFact.disabled_reason : null) ??
    reviseFact?.disabled_reason ??
    null;
  const canEnterReview = Boolean(reviewFact?.enabled);

  // 知情软门（裁定 3）：事实门禁 canEnterReview 不变；待确认条目有达标缺口时弹确认层说明可携带进入，
  // 不硬阻断（评审即治疗不完备之处；达标判定为可再生投影非权威——依据见覆盖标记表）。零缺口直进不弹。
  const handleEnterReview = useCallback(() => {
    const gate = deriveReviewGateGaps(workspace.pending_items);
    if (!gate) {
      onEnterItemReview?.(workspace);
      return;
    }
    let confirmRef: { destroy: () => void } | null = null;
    const locateFromGate = (itemRef: string) => {
      confirmRef?.destroy(); // 关弹层再定位（联动区1/区4：选中条目→区4 体检报告随之切换，区1/区5 滚动居中）
      locateLinkage(null, itemRef);
    };
    confirmRef = gateModal.confirm({
      title: gate.title,
      icon: null,
      content: (
        <div className="if-review-gate">
          <p className="if-review-gate__counts">待确认条目中：{gate.countsText}</p>
          <ul className="if-review-gate__list" aria-label="不完备条目清单">
            {gate.items.map((it) => (
              <li key={it.itemRef} className="if-review-gate__item">
                <button
                  type="button"
                  className="if-review-gate__locate"
                  onClick={() => locateFromGate(it.itemRef)}
                  title="定位到该条目（区1 来源要素 / 区4 体检报告）"
                >
                  <span className="if-review-gate__no">{it.reqNo}</span>
                  <span className="if-review-gate__expr">{it.expression}</span>
                </button>
                <span className="if-review-gate__facets">
                  {it.gapLabels.length ? `缺：${it.gapLabels.join('、')}` : '缺必备成分'}
                </span>
              </li>
            ))}
          </ul>
          {REVIEW_GATE_NOTES.map((note) => (
            <p key={note}>{note}</p>
          ))}
        </div>
      ),
      okText: '进入评审',
      cancelText: '留在本页',
      onOk: () => onEnterItemReview?.(workspace),
      // 留在本页：自动应用「不完备」筛选，把待处理条目聚焦到区5（门禁语义不变，仅辅助定位）
      onCancel: () => setCompletenessFilter('incomplete'),
    });
  }, [gateModal, locateLinkage, onEnterItemReview, workspace]);
  const eligibleCount = workspace.eligible_elements.length;
  const scopeText = !eligibleCount
    ? '无可条目化要素'
    : selectedElementRefs.length === eligibleCount
      ? `全部可条目化（${eligibleCount} 条）`
      : `已选 ${selectedElementRefs.length}/${eligibleCount} 条`;
  const pendingCount = pendingItems.filter((i) => i.statusText === '待确认').length;
  const mergeCandidates = pendingItems.filter(
    (i) => i.itemRef !== selectedItem?.item_ref && i.statusText === '待确认',
  );
  const composerDisabled = isBusy || !parseResultRef;

  return (
    <div className="item-formation-grid" aria-label="条目形成页面">
      {gateModalContextHolder}
      <section className="item-formation-zone item-formation-zone--elements" aria-label="区1 知识项清单（输入）">
        <div className="intake-zone__header">
          <span>区1 · 输入</span>
          <h3>知识项清单</h3>
        </div>
        <div className="item-formation-summary" title="勾选集就是「生成待确认条目」的批次范围">
          <span>
            可条目化 <strong>{eligibleCount}</strong> · 已勾选 <strong>{selectedElementRefs.length}</strong>
          </span>
          <span className="item-formation-summary__actions">
            <button
              className="az5-link"
              disabled={batchStatus === 'running' || selectedElementRefs.length === eligibleCount}
              onClick={() => setSelectedElementRefs(workspace.eligible_elements.map((e) => e.id))}
              type="button"
            >
              全选
            </button>
            <button
              className="az5-link"
              disabled={batchStatus === 'running' || !selectedElementRefs.length}
              onClick={() => setSelectedElementRefs([])}
              type="button"
            >
              清空
            </button>
          </span>
        </div>
        <div className="if-el-groups" ref={elementListRef} role="listbox" aria-label="可条目化知识项（按语义类型分组；点复选框勾选入批次，点行定位关联条目）" aria-multiselectable>
          {elementGroups.map((group) => {
            const expanded = expandedGroups.includes(group.key);
            const visibleItems = expanded ? group.items : group.items.slice(0, 5);
            const hiddenCount = group.items.length - visibleItems.length;
            return (
              <div className="if-el-group" key={group.key}>
                <div className="if-el-group__head">
                  <span aria-hidden className={`if-el-dot if-el-dot--${group.typeColorKey}`} />
                  {group.typeLabel}
                  <span className="if-el-group__cnt">{group.items.length}</span>
                </div>
                {visibleItems.map((element) => (
                  <button
                    aria-selected={selectedElementRefs.includes(element.id)}
                    className={[
                      'if-el-row',
                      selectedElementRefs.includes(element.id) ? 'if-el-row--checked' : '',
                      linkedElementRef === element.id ||
                      (selectedItem?.source_element_refs ?? []).includes(element.id)
                        ? 'if-el-row--linked'
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    data-element-ref={element.id}
                    disabled={batchStatus === 'running'}
                    key={element.id}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest('.if-el-check')) {
                        toggleElement(element.id);
                      } else {
                        locateLinkage(element.id);
                      }
                    }}
                    onKeyDown={(event) => {
                      if (event.key === ' ') {
                        event.preventDefault();
                        toggleElement(element.id);
                      }
                    }}
                    role="option"
                    title={`${element.content}\n点行定位关联（联动区3 来源组 / 区5 条目）；点复选框或按空格切换入批次`}
                    type="button"
                  >
                    <span aria-hidden className="if-el-check" />
                    <span className="if-el-row__title">{element.content}</span>
                    <span className="if-el-conf">{element.confidenceText}</span>
                  </button>
                ))}
                {hiddenCount > 0 || expanded ? (
                  <button
                    className="if-el-more"
                    onClick={() =>
                      setExpandedGroups((current) =>
                        expanded ? current.filter((key) => key !== group.key) : [...current, group.key],
                      )
                    }
                    type="button"
                  >
                    {expanded ? '收起 ‹' : `展开全部（${hiddenCount}）›`}
                  </button>
                ) : null}
              </div>
            );
          })}
          {!eligibleItems.length ? (
            <p className="empty-state">没有可条目化的已确认需求表达类要素；请返回知识抽取确认要素。</p>
          ) : null}
        </div>
        {blockedItems.length ? (
          <div className="item-formation-blocked" aria-label="不可形成或支撑性要素">
            <strong>支撑或暂不可形成要素（仅作依据，不生成条目）</strong>
            {blockedItems.map((element) => (
              <p key={element.id}>
                {element.content}
                <span>{element.blockedReason}</span>
              </p>
            ))}
          </div>
        ) : null}
        {intentItems.length ? (
          <div className="item-formation-intent" aria-label="意图背景 · 目标/场景（只读）">
            <strong>意图背景 · 目标 / 场景（只读，不入批次）</strong>
            {intentItems.map((element) => (
              <p key={element.id}>
                {element.content}
                <span>{element.typeLabel}</span>
              </p>
            ))}
          </div>
        ) : null}
      </section>

      <div className="item-formation-middle">
        <section className="item-formation-zone item-formation-zone--toolbar" aria-label="区2 导航与形成动作">
          <span className="if-zone-chip">区2</span>
          <span className="item-formation-scope">批次范围：{scopeText}（勾选集＝生成批次范围）</span>
          {workspace.convention_display_name ? (
            <span
              className="if-convention-badge"
              data-testid="if-convention-badge"
              title="本批次条目遵循的需求规约方案；切换入口在设置工作台「生成治理 · 需求规约」"
            >
              规约方案：{workspace.convention_display_name}
            </span>
          ) : null}
          <div className="item-formation-toolbar">
            <Button
              disabled={!canStartBatch}
              icon={renderActionIcon('launch')}
              loading={batchStatus === 'running'}
              onClick={() => void handleStartBatch()}
              title={
                canStartBatch
                  ? `对${scopeText}发起 AEP-038 条目化批次`
                  : startFact?.disabled_reason ?? (selectedElementRefs.length ? undefined : '请先在区1 勾选要素')
              }
              type="primary"
            >
              生成待确认条目
            </Button>
            <Button
              disabled={!canRecheck}
              loading={recheckBusy}
              onClick={handleRecheck}
              title={
                recheckTargets
                  ? `修复通道：${recheckTargets.countsText}，AI 按句式档案重新体检当前表达（只更新体检结果，不改条目内容）`
                  : pendingCount
                    ? RECHECK_DISABLED_REASON
                    : '尚未形成待确认条目'
              }
            >
              复核
            </Button>
            <Button onClick={onBackToAnalysis}>返回知识抽取</Button>
          </div>
          {batchRun ? (
            <div
              aria-label="条目化批次进度"
              className={`if-batch if-batch--${batchRun.phase}`}
              data-testid="if-batch-progress"
              role="status"
            >
              {batchRun.phase === 'failed' ? (
                <>
                  <span className="if-batch__error">✕ {batchRun.error ?? '条目化批次执行失败，可重试'}</span>
                  <Button
                    danger
                    disabled={!canStartBatch}
                    onClick={() => void handleStartBatch()}
                    size="small"
                    title={canStartBatch ? '按当前勾选范围重新发起批次' : startFact?.disabled_reason ?? '请先在区1 勾选要素'}
                  >
                    重试
                  </Button>
                </>
              ) : batchRun.phase === 'succeeded' ? (
                <span className="if-batch__badge" title={BATCH_PROGRESS_HINT}>
                  ✓ 批次完成：已形成{' '}
                  {batchProgress.determinate
                    ? `${batchProgress.formed}/${Math.max(batchRun.total ?? 0, batchProgress.processed)}`
                    : `${batchProgress.formed} 条`}
                  {batchProgress.formed < batchProgress.processed ? '（其余见批次结果）' : ''}
                </span>
              ) : batchProgress.determinate ? (
                <>
                  <span className="if-batch__bar" aria-hidden>
                    <span
                      className="if-batch__seg if-batch__seg--formed"
                      style={{ width: `${batchProgress.processedPct}%` }}
                    />
                  </span>
                  <span className="if-batch__counts" title={BATCH_PROGRESS_HINT}>{batchProgress.countsText}</span>
                </>
              ) : (
                <span className="if-batch__counts" title={BATCH_PROGRESS_HINT}>批次执行中：{batchProgress.countsText}（发起范围未捕获，进度不定型）</span>
              )}
            </div>
          ) : null}
          {!canStartBatch && batchStatus !== 'running' ? (
            <p className="item-formation-notice" role="status">
              {startFact?.disabled_reason ?? (!selectedElementRefs.length ? '请先在区1 勾选进入批次的要素。' : null)}
            </p>
          ) : null}
          {noticeText ? (
            <p className={batchStatus === 'failed' ? 'item-formation-notice item-formation-notice--error' : 'item-formation-notice'} role="status">
              {noticeText}
            </p>
          ) : null}
        </section>

        <section className="item-formation-zone item-formation-zone--canvas" aria-label="区3 材料来源画布">
          <div className="intake-zone__header">
            <span>区3</span>
            <h3>材料来源画布</h3>
            <span className="if-canvas-legend" aria-label="高亮图例">
              <span className="if-lg if-lg--current">当前条目来源组</span>
              <span className="if-lg if-lg--element">可形成要素锚点</span>
            </span>
          </div>
          <article className="item-formation-canvas" ref={canvasRef}>
            <h4>{workspace.material_canvas?.title ?? '当前材料'}</h4>
            {workspace.material_canvas?.source_note ? <p className="analysis-canvas__note">{workspace.material_canvas.source_note}</p> : null}
            {canvasBlocks.map((block) => (
              <p key={block.blockId}>
                {block.segments.map((seg) =>
                  seg.refs.length ? (
                    <span
                      className={[
                        'canvas-highlight',
                        `canvas-highlight--${seg.primaryColorKey ?? 'term'}`,
                        selectedItem?.source_element_refs?.some((ref) => seg.refs.includes(ref)) ||
                        (linkedElementRef && seg.refs.includes(linkedElementRef))
                          ? 'canvas-highlight--selected'
                          : '',
                      ].join(' ')}
                      data-first-ref={seg.refs[0]}
                      data-refs={seg.refs.join(' ')}
                      data-seg-start={seg.start}
                      key={seg.key}
                      onClick={() => locateLinkage(seg.refs[0])}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          locateLinkage(seg.refs[0]);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      title="点击定位关联（联动区1 要素行 / 区5 条目）"
                    >
                      {seg.text}
                    </span>
                  ) : (
                    <span data-seg-start={seg.start} key={seg.key}>{seg.text}</span>
                  ),
                )}
              </p>
            ))}
          </article>
          <div className="item-formation-canvas-legend">
            <span>选中条目的来源组会以描边高亮。</span>
            <span>来源锚点不可断开；无来源不生成条目。</span>
          </div>
        </section>

        <section
          className={`item-formation-zone item-formation-zone--detail${detailZoomed ? ' is-zoomed' : ''}`}
          aria-label="区4 待确认条目详情"
        >
          <div className="intake-zone__header">
            <span>区4</span>
            <h3>待确认条目详情</h3>
            {selectedItem && selectedItemVm ? (
              <button
                aria-pressed={detailZoomed}
                className="item-detail-zoom"
                onClick={() => setDetailZoomed((prev) => !prev)}
                title={detailZoomed ? '还原区4 高度，区3 画布恢复常态' : '临时放大区4 便于细读；区3 画布暂时让位，可一键还原'}
                type="button"
              >
                {detailZoomed ? '⤡ 还原' : '⤢ 放大'}
              </button>
            ) : null}
          </div>
          {selectedItem && selectedItemVm ? (
            <div className={`item-detail-layout${detailZoomed ? ' item-detail-layout--zoomed' : ''}`}>
              <div className="item-detail-card item-detail-card--primary">
                <div className="item-detail-card__title">
                  <strong>{selectedItem.req_no}</strong>
                  <StatusPill tone={selectedItemVm.statusTone}>{selectedItemVm.statusText}</StatusPill>
                </div>
                {/* 走查反馈第⑥组：正文分「条目内容」与「登记信息」两组，三页同一口径。
                    编号与状态徽标留在卡头不并入分组——它们是这张卡的身份标识，挪进正文
                    列表后一眼看不出在看哪一条（用户拍板）。 */}
                <p className="item-detail-expression">{selectedItem.expression}</p>
                <p className="item-detail-group__cap">条目内容</p>
                <dl>
                  <dt>类型</dt>
                  <dd>{selectedItemVm.typeText}</dd>
                  <dt>整理说明</dt>
                  <dd>{selectedItem.curation_note ?? '（未归纳；可经字段修订补写）'}</dd>
                  <dt>边界说明</dt>
                  <dd>{selectedItem.boundary_note ?? '（未归纳；可经字段修订补写）'}</dd>
                  <dt>验证方式</dt>
                  <dd>{verificationMethodText(selectedItem.verification_method) ?? '（未建议；可经字段修订设定）'}</dd>
                  <dt>验收准则</dt>
                  <dd className={selectedItem.verification_note ? undefined : 'item-attr-missing'}>
                    {selectedItem.verification_note ?? '缺失：来源无可归纳验证线索，建议评审补充来源后补写（仅警示，不阻断）'}
                    {!selectedItem.verification_note ? (
                      <button
                        className="item-attr-fix"
                        onClick={() => openFieldRevision('verification_note')}
                        title="展开区5 字段修订表单并选中「验收准则」"
                        type="button"
                      >
                        字段修订
                      </button>
                    ) : null}
                  </dd>
                  <dt>优先级</dt>
                  <dd className={selectedItem.priority ? undefined : 'item-attr-missing'}>
                    {priorityText(selectedItem.priority) ?? '未设定：评审/确认前应人工补齐（仅警示，不阻断）'}
                    {!selectedItem.priority ? (
                      <button
                        className="item-attr-fix"
                        onClick={() => openFieldRevision('priority')}
                        title="展开区5 字段修订表单并选中「优先级」"
                        type="button"
                      >
                        字段修订
                      </button>
                    ) : null}
                  </dd>
                </dl>
                <p className="item-detail-group__cap">登记信息</p>
                <dl>
                  <dt>版本</dt>
                  <dd>v{selectedItem.version_no}</dd>
                  <dt>来源要素</dt>
                  <dd>{selectedItemVm.sourceCountText}</dd>
                  <dt>形成依据</dt>
                  <dd>{selectedItem.formation_basis_ref ? `模型格式化记录 ${selectedItem.formation_basis_ref.slice(0, 8)}…` : '人工形成（拆分/归并）'}</dd>
                </dl>
                {selectedStructure && healthReport ? (() => {
                  // 走查第三轮裁定（2026-07-11）：投影过期在 VM 层视同暂无体检（mapItemStructureReview
                  // 返回 null），本块只呈现锚定当前表达的真判定——内容修订/拆分/归并已链式自动体检
                  const verdictPills = (
                    <>
                    {selectedStructure.conformance ? (
                      <StatusPill tone={selectedStructure.conformance.tone}>
                        {selectedStructure.conformance.label}
                      </StatusPill>
                    ) : null}
                    {selectedStructure.review.completeness ? (
                      <span
                        title={
                          selectedItemVm.completenessKey === 'incomplete'
                            ? ITEM_COMPLETENESS_BADGE_HINTS.incomplete
                            : undefined
                        }
                      >
                        <StatusPill tone={selectedStructure.review.completeness.tone}>
                          {selectedStructure.review.completeness.label}
                        </StatusPill>
                      </span>
                    ) : null}
                    </>
                  );
                  const reportBody = (
                    <>
                  <p className="item-health__intro">{healthReport.intro}</p>
                  {healthReport.pattern ? (
                    <details className="item-health__pattern">
                      <summary>{healthReport.patternTitle}</summary>
                      <p>{healthReport.pattern}</p>
                    </details>
                  ) : null}
                  {healthReport.requiredGaps.length ? (
                    <div className="item-health__gaps" aria-label="待补成分清单">
                      <strong>待补成分 {healthReport.requiredGaps.length} 项</strong>
                      {healthReport.requiredGaps.map((gap) => (
                        <div className="item-health-gap" key={`gap-${gap.key}`}>
                          <div className="item-health-gap__head">
                            <span className="item-health-gap__name">{gap.label}</span>
                            <span className="item-health-gap__st">{gap.statusLabel}</span>
                            <button
                              className="item-health-gap__draft"
                              disabled={composerDisabled}
                              onClick={() => prefillGapDraft(gap.label)}
                              title={`预填区5 输入框「/修订 补写${gap.label}：」，写补写方向后发送，AI 起草建议卡`}
                              type="button"
                            >
                              让 AI 起草补写
                            </button>
                          </div>
                          {gap.note ? <p className="item-health-gap__note">判定原因：{gap.note}</p> : null}
                          {gap.revisionHint ? (
                            <p className="item-health-gap__hint">补写示例：{gap.revisionHint}</p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {healthReport.optionalGaps.map((gap) => (
                    <p className="analysis-detail-suggestion" key={`opt-${gap.key}`}>
                      {gap.label}（可选成分，{gap.statusLabel}，不影响「不完备」判定）：{gap.note ?? ''}
                      {gap.revisionHint ? ` ${gap.revisionHint}` : ''}
                    </p>
                  ))}
                  {healthReport.notApplicable.length ? (
                    <div className="item-health__na" aria-label="不适用成分清单">
                      {healthReport.notApplicable.map((na) => (
                        <p className="item-health-na" key={`na-${na.key}`}>
                          <span className="item-health-na__name">{na.label}</span>
                          <span className="item-health-na__st">不适用</span>
                          {na.note ? <span className="item-health-na__why">：{na.note}</span> : null}
                        </p>
                      ))}
                    </div>
                  ) : null}
                  {healthReport.present.length ? (
                    <details className="item-health__present">
                      <summary>已具备 {healthReport.present.length} 项</summary>
                      {healthReport.present.map((b) => (
                        <p className="analysis-detail-suggestion" key={`ev-${b.key}`}>
                          {b.label}
                          {b.evidence ? `：来源证据「${b.evidence}」` : ''}
                        </p>
                      ))}
                    </details>
                  ) : null}
                    </>
                  );
                  return (
                    <div className="item-detail-structure" aria-label="陈述体检报告">
                      <div className="item-health__head">
                        <strong>陈述体检报告（档案 v{selectedStructure.profileVersion}）</strong>
                        {verdictPills}
                      </div>
                      {reportBody}
                    </div>
                  );
                })() : null}
              </div>
              <div className="item-detail-side">
              <div className="item-detail-card">
                <strong>来源要素</strong>
                {(selectedItem.source_element_refs ?? []).map((ref) => {
                  const element = sourceElementsById.get(ref);
                  if (!element) return null;
                  const meta = elementTypeMeta(element.element_type);
                  const originalQuote = anchorExactText(element.source_anchor);
                  // 修订态：表达经修订（版本>1 或与原文锚点字面不一致）→ 显示修订后文字 + 回看标记
                  const revised = (element.version ?? 1) > 1 || (originalQuote !== null && originalQuote !== element.content);
                  return (
                    <div key={ref}>
                      <p>
                        <span className={`element-type-chip element-type-chip--${meta.colorKey}`}>{meta.label}</span>
                        {revised ? (
                          <button
                            aria-expanded={sourceTraceRef === ref}
                            className="item-trace-toggle"
                            onClick={() => setSourceTraceRef((current) => (current === ref ? null : ref))}
                            title="点击查看原文与修订信息"
                            type="button"
                          >
                            {element.content}
                            <span className="item-trace-chip">修订 v{element.version ?? 1}</span>
                          </button>
                        ) : (
                          element.content
                        )}
                      </p>
                      {revised && sourceTraceRef === ref ? (
                        <div className="item-trace" aria-label="原文回看">
                          <span className="item-trace__cap">原文（LDM-002 锚点片段，未被修改）</span>
                          <p className="item-trace__before">{originalQuote ?? '（无来源锚点：补入/新增项见依据留痕）'}</p>
                          <span className="item-trace__cap">表达版本</span>
                          <p className="item-trace__val">v{element.version ?? 1}（版本链与采纳记录见知识抽取历史）</p>
                          {element.correction_note ? (
                            <>
                              <span className="item-trace__cap">修订 / 补入依据</span>
                              <p className="item-trace__val">{element.correction_note}</p>
                            </>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
              <div className="item-detail-card">
                <strong>字段修订记录</strong>
                {selectedItem.revision_records?.length ? (
                  selectedItem.revision_records.map((record) => (
                    <p key={record.record_ref}>
                      {isSourceAttestation(record)
                        ? attestationRecordText(record)
                        : `${revisionRecordFieldText(record)}: ${record.before_value} → ${record.after_value}`}
                    </p>
                  ))
                ) : (
                  <p>暂无字段修订记录。</p>
                )}
              </div>
              <div className="item-detail-card">
                <strong>模型修订建议</strong>
                {suggestions.length ? (
                  suggestions.map((suggestion) => (
                    <p key={suggestion.suggestion_ref}>
                      <StatusPill tone={suggestion.status === 'candidate' ? 'processing' : suggestion.status === 'accepted' ? 'success' : 'neutral'}>
                        {suggestion.status === 'candidate' ? '候选' : suggestion.status === 'accepted' ? '已采纳' : suggestion.status === 'rejected' ? '已拒绝' : '已过期'}
                      </StatusPill>
                      {suggestion.reason}
                    </p>
                  ))
                ) : (
                  <p>暂无可用建议；可在区5 用 /修订 或 /规范化 请 AI 起草。</p>
                )}
              </div>
              </div>
            </div>
          ) : (
            <p className="empty-state">
              生成待确认条目后，此处展示选中条目的详情、来源要素与修订记录。
              （入口：区2「生成待确认条目」按钮，或区5 输入 /生成条目）
            </p>
          )}
        </section>
      </div>

      {/* 区5 待确认条目 + AI 协同（对话式；结构与样式复用 az5 全局件） */}
      <section className="item-formation-zone item-formation-zone--items az5" aria-label="区5 待确认条目与 AI 协同区">
        <div className="az5-top">
          <div className="az5-row1">
            <span className="az5-zone">区5 · 产出</span>
            <h3 className="az5-title">条目产出 + AI 协同</h3>
            <span className="az5-prog">待确认 {pendingCount} 条</span>
            <button
              className="az5-exit-btn"
              disabled={!canEnterReview}
              onClick={handleEnterReview}
              title={canEnterReview ? '进入 SCN-003 条目评审（有达标缺口时会先确认，可携带进入）' : reviewFact?.disabled_reason ?? '尚未形成待确认条目'}
              type="button"
            >
              进入条目评审
            </button>
          </div>
          <div className="az5-target" aria-label="当前目标条目">
            {selectedItem && selectedItemVm ? (
              <>
                <span className="az5-target__cap">当前条目</span>
                <span className="az5-target__body">
                  {selectedItem.req_no} · {selectedItem.expression}
                </span>
                <span className="az5-target__st">{selectedItemVm.typeText} · {selectedItemVm.statusText}</span>
              </>
            ) : (
              <span className="az5-target__cap">
                尚无待确认条目——点区2「生成待确认条目」或在下方输入 /生成条目
              </span>
            )}
          </div>
          {pendingItems.length ? (
            <div className="ifz5-panel" aria-label="条目清单面板（浅底面板；下方为 AI 协同对话）">
              <div className="ifz5-listhead">
                <button
                  aria-expanded={!itemsCollapsed}
                  className="ifz5-fold"
                  data-testid="ifz5-fold"
                  onClick={() => setItemsCollapsed((v) => !v)}
                  title={itemsCollapsed ? '展开为条目清单' : '折叠为横向队列条'}
                  type="button"
                >
                  <span aria-hidden className="ifz5-fold__chev">{itemsCollapsed ? '▸' : '▾'}</span>
                  条目清单
                </button>
                <span className="ifz5-listhead__chips" role="group" aria-label="条目达标度筛选">
                  {ITEM_COMPLETENESS_FILTERS.map((f) => (
                    <button
                      className={completenessFilter === f.key ? 'filter-chip filter-chip--active' : 'filter-chip'}
                      key={`item-facet-${f.key}`}
                      onClick={() => setCompletenessFilter(f.key)}
                      title={f.key === 'all' ? undefined : ITEM_COMPLETENESS_BADGE_HINTS[f.key]}
                      type="button"
                    >
                      达标度·{f.label}
                      {f.key !== 'all'
                        ? ` ${pendingItems.filter((i) => matchesItemCompletenessFilter(i, f.key)).length}`
                        : ''}
                    </button>
                  ))}
                </span>
              </div>
              {itemsCollapsed ? (
                <div
                  aria-label="待确认条目队列（折叠态；点击切换对话目标并联动区1/区3 定位）"
                  className="ifz5-queue"
                  ref={itemListRef}
                  role="listbox"
                >
                  {filteredPendingItems.length ? (
                    filteredPendingItems.map((item) => (
                      <button
                        aria-selected={selectedItem?.item_ref === item.itemRef}
                        className={selectedItem?.item_ref === item.itemRef ? 'ifz5-q ifz5-q--on' : 'ifz5-q'}
                        data-item-ref={item.itemRef}
                        key={item.itemRef}
                        onClick={() => locateLinkage(null, item.itemRef)}
                        role="option"
                        title={`${item.reqNo} · ${item.expression}\n${item.typeText} · ${item.statusText}`}
                        type="button"
                      >
                        <span aria-hidden className={`ifz5-dot ifz5-dot--${item.statusTone}`} />
                        {item.reqNo}
                      </button>
                    ))
                  ) : (
                    <p className="empty-state">当前达标度筛选下没有条目。</p>
                  )}
                </div>
              ) : (
                <div
                  aria-label="待确认条目（点击切换对话目标并联动区1/区3 定位）"
                  className="az5-itemlist az5-itemlist--compact"
                  ref={itemListRef}
                  role="listbox"
                >
                  {filteredPendingItems.length ? (
                    filteredPendingItems.map((item) => {
                      const badge = itemCompletenessBadge(item);
                      return (
                        <button
                          aria-selected={selectedItem?.item_ref === item.itemRef}
                          className={selectedItem?.item_ref === item.itemRef ? 'ifz5-row ifz5-row--selected' : 'ifz5-row'}
                          data-item-ref={item.itemRef}
                          key={item.itemRef}
                          onClick={() => locateLinkage(null, item.itemRef)}
                          role="option"
                          title={`${item.expression}\n${item.typeText} · ${item.sourceCountText} · ${item.statusText}（点击联动区1/区3/区4 定位）`}
                          type="button"
                        >
                          <span aria-hidden className={`ifz5-dot ifz5-dot--${item.statusTone}`} />
                          <strong className="ifz5-row__no">{item.reqNo}</strong>
                          <span className="ifz5-row__ex">{item.expression}</span>
                          {badge ? (
                            <span className={`ifz5-mini ifz5-mini--${badge.tone}`} title={badge.hint ?? undefined}>
                              {badge.label}
                            </span>
                          ) : null}
                        </button>
                      );
                    })
                  ) : (
                    <p className="empty-state">当前达标度筛选下没有条目。</p>
                  )}
                </div>
              )}
            </div>
          ) : null}
        </div>

        <div className="az5-thread" ref={threadRef} aria-label="对话时间线">
          {messages.length === 0 ? (
            <p className="az5-hint">
              对当前条目说点什么（问来源、修订、规范化…），或点下方快捷命令预填 /命令词 后续写；
              生成条目、建议卡采纳 / 拒绝仍一键直发。
            </p>
          ) : null}
          {/* ④ 时间线按时刻升序：消息与候选建议卡同流，建议卡不再固定钉在消息之后
              （旧结构下新发的消息渲染在建议卡之上，看着像插进了历史中间）。 */}
          {zone5Timeline.map((entry) => {
            if (entry.kind === 'message') {
              const m = entry.message;
              return (
                m.kind === 'user' || m.kind === 'cmd' ? (
                  <div className="az5-msg az5-msg--user" key={m.id}>
                    <span className="az5-ava">我</span>
                    <div className="az5-msg__body">
                      <span className="az5-who">
                        {m.kind === 'cmd' ? '我 · 命令' : '我'}
                        <RelativeTime className="az5-time" iso={m.at} />
                      </span>
                      {m.kind === 'cmd' ? <span className="az5-cmd">{m.text}</span> : <span className="az5-txt">{m.text}</span>}
                    </div>
                  </div>
                ) : m.kind === 'ai' ? (
                  <div className="az5-msg az5-msg--ai" key={m.id}>
                    <span className="az5-ava az5-ava--ai">AI</span>
                    <div className="az5-msg__body">
                      <span className="az5-who">
                        AI
                        <RelativeTime className="az5-time" iso={m.at} />
                      </span>
                      <span className="az5-txt" style={{ whiteSpace: 'pre-wrap' }}>{m.text}</span>
                      {m.traceLines ? <AiTraceDetail lines={m.traceLines} /> : null}
                    </div>
                  </div>
                ) : (
                  <div className={`az5-sys az5-sys--${m.kind}`} key={m.id}>
                    {m.kind === 'sys-ok' ? '✓ ' : m.kind === 'sys-warn' ? '⚠ ' : ''}
                    {m.text}
                    <RelativeTime className="az5-time" iso={m.at} />
                    {m.traceLines ? <AiTraceDetail lines={m.traceLines} /> : null}
                  </div>
                )
              );
            }
            const suggestion = suggestionByTimelineKey.get(entry.key);
            if (!suggestion) {
              return null;
            }
            return (

                <div className="az5-msg az5-msg--ai" aria-label="修订建议卡" key={entry.key}>
                  <span className="az5-ava az5-ava--ai">AI</span>
                  <div className="az5-msg__body">
                    <span className="az5-who">
                  AI 修订建议（候选，未采纳零副作用）
                  {entry.at ? <RelativeTime className="az5-time" iso={entry.at} /> : null}
                </span>
                    <div className="az5-card">
                      <div className="az5-card__hd">
                        <b>{suggestion.field_key === 'expression' ? '条目表达建议' : '条目类型建议'}</b>
                        <StatusPill tone="processing">候选</StatusPill>
                      </div>
                      <div className="az5-card__bd">
                        {selectedItem && suggestion.field_key === 'expression' ? (
                          <p className="az5-diff az5-diff--before">{selectedItem.expression}</p>
                        ) : null}
                        <p className="az5-diff az5-diff--after">{suggestion.proposed_value}</p>
                        <p>{suggestion.reason}</p>
                      </div>
                      <div className="az5-card__ft">
                        <button
                          className="az5-btn az5-btn--primary"
                          disabled={!canRevise}
                          onClick={() =>
                            void applyRevision({
                              revision_mode: 'accept_suggestion',
                              field_key: suggestion.field_key as RevisionField,
                              suggestion_ref: suggestion.suggestion_ref,
                              reason: suggestion.reason,
                            })
                          }
                          title={canRevise ? '采纳=应用字段修订（AEP-036 留痕）' : reviseDisabledReason ?? undefined}
                          type="button"
                        >
                          采纳
                        </button>
                        <button
                          className="az5-btn"
                          disabled={!canRevise}
                          onClick={() =>
                            void applyRevision({
                              revision_mode: 'reject_suggestion',
                              field_key: suggestion.field_key as RevisionField,
                              suggestion_ref: suggestion.suggestion_ref,
                            })
                          }
                          title={canRevise ? undefined : reviseDisabledReason ?? undefined}
                          type="button"
                        >
                          拒绝
                        </button>
                        <span className="az5-card__note">
                          {itemReviseFact && !itemReviseFact.enabled
                            ? '条目已离开待确认，本建议已失效（仅留痕，不可采纳）。'
                            : '不满意？继续说修订方向可原位迭代。'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
            );
          })}
          {trace ? <AiTraceRail now={traceNow} trace={trace} /> : null}
          {batchStatus === 'running' ? <p className="az5-sys">条目化批次执行中…</p> : null}
          {workspace.next_action && messages.length === 0 ? <p className="az5-sys">{workspace.next_action}</p> : null}
        </div>

        <div className="az5-composer">
          <div className="az5-pills" role="group" aria-label="快捷命令">
            {typePickerOpen ? (
              <div className="az5-pop" role="menu" aria-label="选择新类型">
                <span className="az5-pop__cap">改为哪个条目类型？</span>
                {ITEM_TYPE_OPTIONS.map((o) => (
                  <button
                    className="az5-qp"
                    key={o.value}
                    onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.adjustType(o.label))}
                    type="button"
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            ) : null}
            {mergePickerOpen ? (
              <div className="az5-pop" role="dialog" aria-label="选择参与归并的条目">
                <span className="az5-pop__cap">与当前条目归并——复选参与条目（类型须一致）：</span>
                <div className="az5-pop__list">
                  {mergeCandidates.map((item) => (
                    <label key={item.itemRef}>
                      <Checkbox
                        checked={mergeChecked.includes(item.itemRef)}
                        onChange={(ev) =>
                          setMergeChecked((current) =>
                            ev.target.checked ? [...current, item.itemRef] : current.filter((id) => id !== item.itemRef),
                          )
                        }
                      />
                      <span>{item.reqNo} {item.expression.slice(0, 20)}{item.expression.length > 20 ? '…' : ''}</span>
                    </label>
                  ))}
                  {!mergeCandidates.length ? <span className="az5-card__note">没有其它待确认条目可归并。</span> : null}
                </div>
                <button
                  className="az5-btn az5-btn--primary"
                  disabled={!mergeChecked.length}
                  onClick={() => {
                    const reqNos = mergeChecked
                      .map((ref) => pendingItems.find((i) => i.itemRef === ref)?.reqNo ?? ref);
                    quickFill(FORMATION_QUICK_COMMAND_PREFILLS.merge(reqNos));
                    setMergeChecked([]);
                  }}
                  type="button"
                >
                  组稿命令文本
                </button>
              </div>
            ) : null}
            <button
              className="az5-qp az5-qp--ok"
              disabled={composerDisabled}
              onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.generate())}
              title={`预填 /生成条目（当前范围：${scopeText}）`}
              type="button"
            >
              生成条目
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem || !selectedItemPending}
              onClick={() => setTypePickerOpen((v) => !v)}
              title={frozenItemTitle ?? (!selectedItem ? '先在上方选中目标条目' : undefined)}
              type="button"
            >
              改类型
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem || !selectedItemPending}
              onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.revise())}
              title={frozenItemTitle ?? '写完整值直接生效；只写方向会转 AI 起草建议卡'}
              type="button"
            >
              修订
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem || !selectedItemPending}
              onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.normalize())}
              title={frozenItemTitle ?? '按条目类型陈述档案规范化表达（出建议卡，不直接生效）'}
              type="button"
            >
              规范化
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem || !selectedItemPending}
              onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.split())}
              title={frozenItemTitle ?? '写明拆法（每行一条完整表达）：新建 N 条待确认、原条目终止'}
              type="button"
            >
              拆分
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem || !selectedItemPending || !mergeCandidates.length}
              onClick={() => setMergePickerOpen((v) => !v)}
              title={frozenItemTitle ?? (!mergeCandidates.length ? '没有其它待确认条目可归并' : undefined)}
              type="button"
            >
              归并
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem}
              onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.askSource())}
              title="确定性来源指认：来源要素 + 原文锚点 + 形成依据（不调模型，即时回答）"
              type="button"
            >
              问来源
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem || !selectedItemPending}
              onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.recheck())}
              title={
                frozenItemTitle
                  ? '条目已离开待确认，判定随状态冻结不再复核；旧体检结果仅供参考'
                  : '复核当前条目：AI 按句式档案重新体检当前表达（只更新体检结果，不改条目；判定已是当前表达时即时回执）'
              }
              type="button"
            >
              复核
            </button>
            <button
              className="az5-qp"
              disabled={composerDisabled || !selectedItem}
              onClick={() => quickFill(FORMATION_QUICK_COMMAND_PREFILLS.referenceBasis())}
              title="引用业务领域知识（术语/业务规则/角色/外部系统）为当前条目的支撑依据（登记预建立追溯边，随条目确认转有效）"
              type="button"
            >
              引用依据
            </button>
          </div>
          <div className="az5-input">
            <textarea
              aria-label="消息输入"
              disabled={composerDisabled}
              ref={composerRef}
              onChange={(e) => setComposerText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                !parseResultRef
                  ? '未接入已解析要素集合，暂不可对话'
                  : selectedItem
                    ? '对当前条目说点什么…'
                    : '先生成条目，或输入 /生成条目'
              }
              rows={1}
              value={composerText}
            />
            <button
              aria-label="发送"
              className="az5-send"
              disabled={composerDisabled || !composerText.trim()}
              onClick={handleSend}
              type="button"
            >
              ↑
            </button>
          </div>
          <details className="az5-advanced" onToggle={(e) => setRevisionFormOpen((e.target as HTMLDetailsElement).open)} open={revisionFormOpen}>
            <summary>字段修订（表单直改 · AEP-036）</summary>
            <div className="item-revision-panel" aria-label="字段修订面板">
              <label>
                <span>字段</span>
                <Select
                  disabled={!selectedItem}
                  onChange={(value) => setRevisionField(value)}
                  options={REVISION_FIELD_OPTIONS}
                  value={revisionField}
                />
              </label>
              {revisionField === 'req_type' ? (
                <label>
                  <span>修订后类型</span>
                  <Select
                    disabled={!selectedItem}
                    onChange={(value) => setRevisionValue(value)}
                    options={ITEM_TYPE_OPTIONS}
                    value={revisionValue as RequirementItemType}
                  />
                </label>
              ) : revisionField === 'verification_method' ? (
                <label>
                  <span>验证方式（可多选）</span>
                  <Select
                    disabled={!selectedItem}
                    mode="multiple"
                    onChange={(values: string[]) => setRevisionValue(values.join(','))}
                    options={VERIFICATION_METHOD_OPTIONS}
                    value={revisionValue ? revisionValue.split(',') : []}
                  />
                </label>
              ) : revisionField === 'priority' ? (
                <label>
                  <span>优先级（仅人工设定）</span>
                  <Select
                    disabled={!selectedItem}
                    onChange={(value) => setRevisionValue(value)}
                    options={PRIORITY_OPTIONS}
                    value={revisionValue || undefined}
                  />
                </label>
              ) : (
                <label>
                  <span>{REVISION_FIELD_OPTIONS.find((o) => o.value === revisionField)?.label}（修订后）</span>
                  <TextArea disabled={!selectedItem} onChange={(event) => setRevisionValue(event.target.value)} rows={3} value={revisionValue} />
                </label>
              )}
              <label>
                <span>修订说明</span>
                <Input disabled={!selectedItem} onChange={(event) => setRevisionReason(event.target.value)} value={revisionReason} />
              </label>
              <Button
                disabled={!canRevise || !revisionValue.trim()}
                loading={revisionBusy}
                onClick={() => void applyRevision({ revision_mode: 'manual' })}
                title={canRevise ? undefined : reviseDisabledReason ?? undefined}
                type="primary"
              >
                应用字段修订
              </Button>
            </div>
          </details>
          <details className="az5-advanced">
            <summary>
              批次结果（逐要素归因
              {workspace.batch_results.length ? ` · 创建 ${workspace.batch_results.filter((r) => r.result_status === 'created').length} / 未形成 ${workspace.batch_results.filter((r) => r.result_status !== 'created').length}` : ''}
              ）
            </summary>
            <div className="item-batch-results" aria-label="批次结果">
              {workspace.batch_results.length ? (
                workspace.batch_results
                  .filter((r) => r.result_status !== 'created')
                  .map((r) => (
                    <p key={`${r.element_ref}-${r.result_status}`}>
                      <StatusPill tone={r.result_status === 'failed' ? 'danger' : 'neutral'}>
                        {RESULT_STATUS_TEXT[r.result_status] ?? r.result_status}
                      </StatusPill>
                      {sourceElementsById.get(r.element_ref)?.content ?? r.element_ref}：{r.reason}
                      {r.next_action ? `（${r.next_action}）` : ''}
                    </p>
                  ))
              ) : (
                <p>等待首次条目化批次。</p>
              )}
              {workspace.batch_results.length &&
              !workspace.batch_results.some((r) => r.result_status !== 'created') ? (
                <p>全部要素已创建为待确认条目，无停靠项。</p>
              ) : null}
            </div>
          </details>
          <p className="az5-note">
            快捷命令只预填 /命令词，可自由续写；发送后由后端解析命令词、AI 解读正文。
            /修订 写完整值直接生效，只写方向转 AI 起草建议卡再采纳；生成与采纳 / 拒绝一键直发。
          </p>
        </div>
      </section>
    </div>
  );
}
