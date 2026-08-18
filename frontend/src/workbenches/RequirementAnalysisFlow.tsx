/**
 * 知识抽取阶段五区页面（SCN-001-P02/P03/P04 前端 —— 知识项确认工作台）。
 *
 * 事实源：docs/40 domains/DS-001/state-machines/需求要素.md（3 态 + 重开，2026-07-05 收敛）、
 * docs/30 05A/SCN-001 §4.3 界面契约、SCN-001-P02 页面详细设计 §5（对话式区5）。
 * - ElementWorkspaceRead 是五区唯一刷新权威；View 不直接改 LDM-005。
 * - 区5 = 固定头部（目标 + 出口）+ 对话时间线 + 快捷命令 `/命令词` 预填 + 自由文本输入。
 * - 前端不解析命令词：整段原文发 AEP-096 对话端点，由后端注册表解析命令词、LLM 解释正文；
 *   仅确认 / 拒绝（含批量分诊）与卡片一键裁决保持结构化直发。
 * - AI 卡片只带「采纳」：采纳修订稿即确认（超出原文须先补入）；复核/修订迭代不迁状态。
 * - 批量分诊（勾选 ≥2）仅确认/拒绝有效；合并经复选对话框选参与要素。
 */
import { Button, Checkbox, Modal } from 'antd';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  agentRunApi,
  type AgentRunEventMessage,
} from '../api/agent-runs';
import {
  analysisApi,
  type ElementChangeDraftRead,
  type ElementHistoryRead,
  type ElementWorkspaceRead,
  type GuardedElementRead,
  type MaterialCanvasRead,
  type RequirementElementRead,
} from '../api/analysis';
import { StatusPill } from './WorkbenchWidgets';
import { AiTraceDetail, AiTraceRail } from './AiTraceRail';
import { renderActionIcon, renderElementStatusIcon, renderElementTypeIcon } from '../ui/icons';
import { RelativeTime } from '../ui/RelativeTime';
import { runtimeStatusApi } from '../api/runtime-status';
import { serverNowIso } from '../api/server-clock';
import { fetchChatTranscript } from '../api/transcript';
import { transcriptRowToBubble, type TranscriptBubbleTone } from '../view-models/demo-chat-transcript';
import { useAgentRunWatcher, type RunPollTick } from '../hooks/useAgentRunWatcher';
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
import { createIdempotencyKey } from '../api/idempotency';
import {
  acceptsSelectionAffordance,
  buildCanvasBlocks,
  buildHighlights,
  buildZone5Timeline,
  deriveRecognitionPhase,
  resolveCardPositions,
  buildReidentifyGuard,
  buildRevisionPrefill,
  buildSelectionRanges,
  COMPLETENESS_FILTERS,
  elementStatusMarkKey,
  KNOWLEDGE_CATEGORY_META,
  KNOWLEDGE_CATEGORY_ORDER,
  type KnowledgeCategory,
  STATUS_MARK_META,
  STATUS_MARK_ORDER,
  elementTypeMeta,
  elementTypeOptionsForWing,
  fillSegmentText,
  groupElementListByWing,
  isTriageCandidate,
  mapElementList,
  mapFacetReview,
  mergeHydratedMessages,
  type PositionedCard,
  matchesCompletenessFilter,
  modelVerdictMeta,
  processStatusMeta,
  QUICK_COMMAND_PREFILLS,
  resolveWorkspaceAnchors,
  reviewConclusionMeta,
  splitTriageCandidates,
  verdictReasonText,
  withoutTriageCandidates,
  withSelectionAffordance,
  type CanvasBlockVM,
  type CompletenessFilterKey,
  type ElementListItemVM,
} from '../view-models/requirement-analysis';

const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);
const SSE_TERMINAL_EVENT_STATUS: Record<string, string> = {
  'agent_run.completed': 'succeeded',
  'agent_run.failed': 'failed',
};

type AnalysisPhase = 'idle' | 'recognizing' | 'ready' | 'operating' | 'failed';

/**
 * 识别请求的链路回执条节点序（提案 03 篇 §1）。
 *
 * 比对话请求少一个「解释」节点：识别是把整份材料交给执行器抽取知识项，中间没有
 * 「大模型解释用户这句话要干什么」这一步，列上去就是一盏永远不亮的灯。
 * 其余各盏仍只由后端事实点亮——受理＝识别回执返回，派发＝回执带运行引用，
 * 排队/执行/回写由 AgentRun 状态与事件推进。
 */
const RECOGNITION_TRACE_PATH: TraceStage[] = ['accepted', 'dispatching'];
const RECOGNITION_TRACE_LABEL = '识别知识项';

/** 区5 时间线消息（会话事实的界面留痕；AI 卡片与草案卡由工作区数据投影，不入此列表）。 */
interface ChatMsg {
  /** sys-pending＝异步任务在跑、结果未回（如 AI 正在起草修订稿）；任务收束时就地换成结果回执。 */
  id: number;
  kind: 'user' | 'cmd' | 'sys' | 'sys-ok' | 'sys-warn' | 'sys-pending';
  text: string;
  /** 消息产生时刻（ISO）。本地推的消息取入列时刻，水合而来的取留痕行时刻。 */
  at: string;
  /** 来源留痕行 id（水合而来的消息才有）：水合合并按它去重（裁定 F8） */
  sourceId?: string | null;
  /** 完成回执的可展开链路详情（阶段观测时刻 + 运行引用，与 dialogue.* 日志对账） */
  traceLines?: string[];
}

/**
 * 区5 时间线上两张卡的稳定键（渲染分派与排序共用单一来源）。
 *
 * 键带对象标识而不是写死常量：落位记忆按键存，常量键会让不同知识项的复核卡共用同一份
 * 落位——一次批量 AI 复核在单个请求里写库，这批知识项的最后写入时刻逐字相同，逐条点开
 * 查看时第 2 条起就会沿用第 1 条的落位、插进用户最近几条消息之上（冷审查裁定 C4）。
 */
const ZONE5_CARD_KEY_PREFIX = { element: 'card-element:', draft: 'card-draft:' } as const;
const elementCardKey = (elementId: string) => `${ZONE5_CARD_KEY_PREFIX.element}${elementId}`;
const draftCardKey = (draftRef: string) => `${ZONE5_CARD_KEY_PREFIX.draft}${draftRef}`;

const ELEMENT_TYPE_OPTIONS = [
  'functional_requirement', 'quality_attribute', 'constraint', 'data_requirement',
  'interface_requirement', 'goal', 'scenario', 'term', 'assumption', 'role', 'external_system',
].map((code) => ({ value: code, label: elementTypeMeta(code).label }));

const CREATE_GATE_TEXT: Record<string, string> = {
  creatable: '可创建',
  needs_material_supplement: '需材料补充',
  needs_item_revision: '需条目修订',
  needs_manual: '需人工处理',
  stopped: '失败停靠',
};

// 状态机 3 态；「有修订稿」是会话事实派生筛选，不是状态取值。
const STATUS_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'pending_confirmation', label: '待确认' },
  { key: 'confirmed', label: '已确认' },
  { key: 'revoked', label: '已撤销' },
  { key: 'has_draft', label: '有修订稿' },
];

// 两翼筛选（05 §3）：与状态/完备度并列的第三维；翼归属派生自 ELEMENT_TYPE_META（单一来源）。
const WING_FILTERS: { key: 'all' | KnowledgeCategory; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'requirement', label: KNOWLEDGE_CATEGORY_META.requirement.shortLabel },
  { key: 'business', label: KNOWLEDGE_CATEGORY_META.business.shortLabel },
];

// 区1 翼分组组头下的成员说明（文案照两翼化原型 v4 .wg-members；短名是文案，不是类型清单数据源）。
const WING_GROUP_MEMBERS: Record<KnowledgeCategory, { lead: string; types: string; note: string }> = {
  requirement: { lead: '规定“系统应当如何” · 含 ', types: '功能 / 质量 / 约束 / 数据 / 接口 / 目标 / 场景', note: '' },
  business: {
    lead: '描述“世界本来如此” · 含 ',
    types: '术语 / 业务规则 / 假设 / 角色 / 外部系统',
    note: '（「业务规则」是这里的一类）',
  },
};

interface TextSelectionState {
  start: number;
  end: number;
  text: string;
}

interface OverlapPopoverState {
  refs: string[];
  x: number;
  y: number;
}

/** 共用标注气泡越界钳制的几何输入（全部相对画布台 .analysis-canvas-stage 的坐标/尺寸）。 */
export interface OverlapClampGeometry {
  /** 气泡实测宽/高（固定宽后 popW≈280，popH 随引用条数变化，须测量） */
  popW: number;
  popH: number;
  /** 画布台内容盒宽/高 */
  stageW: number;
  stageH: number;
  /** 选区操作条实占高（offsetHeight + margin-top）；无选区为 0 */
  barH: number;
  /** `.workbench-main` 内边距盒下沿换算到画布台坐标（无宿主时取 +∞，即不额外约束） */
  pageBottomInStage: number;
  /** 点击点相对画布台的纵坐标（= raw.y − 16，气泡默认下移量的还原） */
  cursorY: number;
}

/**
 * K1/D3：一次鼠标交互「按下→松手」的像素位移小于此阈值即判为点击、否则判为拖选。
 * Chrome 文本选择无拖动阈值——按下即吸附最近字符边界、微移就扩选，故 2px 手抖也会造出单字符
 * 选区。阈值须大于常见手抖幅度（裁定实测约 2px），4px 兼顾「小心点选不误判为拖选」。
 */
const SEGMENT_DRAG_THRESHOLD_PX = 4;

/**
 * R2/R3：把气泡落点钳进可视范围。纯函数（不碰 DOM），几何量由布局 effect 测量后喂入，便于单测。
 * - 横向：钳进画布台内（left ∈ [0, stageW − popW]），不越进右侧区5。
 * - 纵向：只防跑出页面——下界＝操作条存在则取其顶（stageH − barH，不压刚拖选出来要用的按钮），
 *   否则取页面下沿（气泡可越出区3 浮在区4 之上，走查已接受）。放不下则翻到光标上方。
 */
export function clampOverlapPosition(
  raw: { x: number; y: number },
  geom: OverlapClampGeometry,
): { left: number; top: number; maxHeight?: number } {
  const GAP = 8;
  const left = Math.max(0, Math.min(raw.x, geom.stageW - geom.popW));
  const maxBottom = geom.barH > 0 ? geom.stageH - geom.barH : geom.pageBottomInStage;
  let top = raw.y;
  if (top + geom.popH > maxBottom) {
    // 下方放不下 → 翻到光标上方，气泡下沿贴光标上方 GAP
    top = geom.cursorY - GAP - geom.popH;
  }
  // K2：翻转后仍须复检下界。popH 大于可用高度（maxBottom）时，翻转算式恒得负、被 Math.max 归 0，
  // 函数退化成常量恒返回 top=0、气泡下沿压过 maxBottom（矮视口下压住操作条）。故这里再钳一次
  // 下界＝maxBottom−popH，与 0 取大防 popH>maxBottom 时出负数；此时 top 钳到 0、由 maxHeight 限高内滚兜住。
  top = Math.min(Math.max(0, top), Math.max(0, maxBottom - geom.popH));
  // popH>maxBottom 时位置无解（放哪都超界），返回从 top 到 maxBottom 的可用高度让气泡限高内滚；
  // maxBottom 为 +∞（无宿主、无操作条，气泡允许越出区3 浮到区4）时不限高，返回 undefined。
  const maxHeight = Number.isFinite(maxBottom) ? Math.max(0, maxBottom - top) : undefined;
  return { left, top, maxHeight };
}

interface RequirementAnalysisFlowProps {
  projectId: string;
  operatorRef: string;
  materialRef: string;
  /** 恢复锚点（AEP-072）：给定时挂载即回放既有要素工作区，而非识别前空态。 */
  initialParseContextRef?: string | null;
  onBackToIntake: () => void;
  onEnterItemFormation?: (workspace: ElementWorkspaceRead) => void;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求处理失败';
}

function isWorkspaceRead(value: unknown): value is ElementWorkspaceRead {
  return (
    typeof value === 'object' && value !== null &&
    'parse_context_ref' in value && 'workspace_version' in value
  );
}

/**
 * 「撤回到正常列表」的说明与回执（三处同文，冷审查裁定 C6）。
 *
 * 撤回只改「这一条显示在哪个列表里」，不迁移确认生命周期。所以文案不能一律说「回到待确认」——
 * 对一条本来就已确认（存量数据）或已撤销的知识项，那是假话。待确认的照旧说「等你审」，其余明说
 * 确认状态不变、现在仍是哪一个。
 */
function triageRestoreStatusNote(processStatus: string | null | undefined): string {
  return processStatus === 'pending_confirmation'
    ? '回到「待确认」等你审'
    : `确认状态不变，仍是「${processStatusMeta(processStatus ?? '').label}」`;
}

function triageRestoreReceipt(processStatus: string | null | undefined): string {
  return `已撤回到正常列表，这一条${triageRestoreStatusNote(processStatus)}；AI 的原判定仍作为证据保留。`;
}

function triageRestoreHint(processStatus: string | null | undefined): string {
  return `AI 判断错了：把这一条放回正常列表，${triageRestoreStatusNote(processStatus)}。AI 的原判定作为证据保留，不会被改写。`;
}

/** 知识项类型徽标：一次 elementTypeMeta 取齐色/提示/名，避免同一入参连调三次（与 :1614 同写法）。 */
function ElementTypeChip({ typeCode }: { typeCode: string }) {
  const meta = elementTypeMeta(typeCode);
  return (
    <span className={`element-type-chip element-type-chip--${meta.colorKey}`} title={meta.hint}>
      {meta.label}
    </span>
  );
}

export function RequirementAnalysisFlow({
  projectId,
  operatorRef,
  materialRef,
  initialParseContextRef,
  onBackToIntake,
  onEnterItemFormation,
}: RequirementAnalysisFlowProps) {
  const [phase, setPhase] = useState<AnalysisPhase>('idle');
  // 这次识别是不是本页发起的：只有本页发起、本页盯着的那次才让识别按钮转圈。
  // 进页回放读回来的「识别中」可能是别处发起、也可能是执行器已经中断，此时按钮必须
  // 还能点——否则用户看着一个转圈的按钮，点下去无声无息（裁定 C1 的 worker 崩溃支）。
  const [recognitionOwned, setRecognitionOwned] = useState(false);
  // 进页只读回放的竞态守卫：异步取回上下文时若用户已自行发起识别，不再覆盖当前态
  const phaseRef = useRef<AnalysisPhase>('idle');
  phaseRef.current = phase;
  const [workspace, setWorkspace] = useState<ElementWorkspaceRead | null>(null);
  const [baseCanvas, setBaseCanvas] = useState<MaterialCanvasRead | null>(null);
  const [parseContextRef, setParseContextRef] = useState<string | null>(null);
  const [selectedElementId, setSelectedElementId] = useState<string | null>(null);
  // K8/K12：applyWorkspace 用 [] 依赖读不到最新 state，用 ref 取「上一次」值——K8 判识别上下文是否
  // 真换了一份、K12 判选中目标是否实际改变（改变才收起组稿浮层，否则普通刷新会打断用户）。
  const parseContextRefRef = useRef<string | null>(null);
  parseContextRefRef.current = parseContextRef;
  const selectedElementIdRef = useRef<string | null>(null);
  selectedElementIdRef.current = selectedElementId;
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState('all');
  const [completenessFilter, setCompletenessFilter] = useState<CompletenessFilterKey>('all');
  const [categoryFilter, setCategoryFilter] = useState<'all' | KnowledgeCategory>('all');
  // 翼内类型子筛选（v4 区1）：仅选中某翼时出现；与状态/完备度是与关系；切翼即重置。
  const [typeFilter, setTypeFilter] = useState('all');
  // 建议剔除候选区默认折叠：它装的是 AI 认为不该占正常列表位置的内容，展开是用户主动复核的动作
  const [triageOpen, setTriageOpen] = useState(false);
  const [selection, setSelection] = useState<TextSelectionState | null>(null);
  const [overlap, setOverlap] = useState<OverlapPopoverState | null>(null);
  // R2/R3：气泡钳制后的实际落点。null＝气泡未开或首次尚未测量（渲染回退到原始 overlap.x/y）；
  // jsdom 无排版时保留上次落点、不复位为 null（S9：早退分支不改 overlapPos，实测均不可达）。
  const [overlapPos, setOverlapPos] = useState<{ left: number; top: number; maxHeight?: number } | null>(null);
  const overlapRef = useRef<HTMLDivElement | null>(null);
  const selectionBarRef = useRef<HTMLDivElement | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [progressText, setProgressText] = useState<string | null>(null);
  const [draftDismissed, setDraftDismissed] = useState(false);
  const [history, setHistory] = useState<ElementHistoryRead | null>(null);

  // 区5 对话态（会话事实的界面侧：时间线消息、输入框、文本组稿弹层）
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [composerText, setComposerText] = useState('');
  const [typePickerOpen, setTypePickerOpen] = useState(false);
  const [mergeDialogOpen, setMergeDialogOpen] = useState(false);
  const [mergeChecked, setMergeChecked] = useState<string[]>([]);
  const msgSeqRef = useRef(0);
  const threadRef = useRef<HTMLDivElement | null>(null);
  // 卡片落位记忆（key → 排序位）：一旦落位就不再被后续新消息推着走；切上下文即清空
  const cardPositionsRef = useRef<Map<string, PositionedCard>>(new Map());

  const pushMsg = useCallback((kind: ChatMsg['kind'], text: string, traceLines?: string[]) => {
    msgSeqRef.current += 1;
    const id = msgSeqRef.current;
    setMessages((current) => [
      ...current,
      // 本地发的消息也按服务器视角打时间戳：整条时间线同一个基准，本机时钟偏差不会
      // 让刚发出的一句显示成「N 分钟前」（走查反馈第⑤组）。
      { id, kind, text, at: serverNowIso(), traceLines },
    ]);
    return id;  // 供在场类消息（sys-pending）在任务收束时找回自己就地改写
  }, []);

  /** 就地改写已在流里的一条消息（用于「起草中…」收敛成结果），保持它原来的时间线位置。 */
  const replaceMsg = useCallback((id: number, kind: ChatMsg['kind'], text: string) => {
    setMessages((current) => current.map(
      (m) => (m.id === id ? { ...m, kind, text } : m),
    ));
  }, []);

  // 演示留痕水合：进入/切换上下文时从服务端拉留痕行水合区5（现状刷新即失）。
  // 效果仅随 parseContextRef 变化重跑（切上下文才重拉）。合并按留痕行 id 去重（裁定 F8）——
  // 旧的 `current.length ? current : rows` 是全有全无，用户抢发一条即丢掉整段历史。
  // 不设「一次性」ref 门——StrictMode 双调用会先 cancel 首个 fetch，若用 ref 门挡住第二次调用则永不水合
  // （strictmode-effect-consume-guard 事故范式）。
  useEffect(() => {
    cardPositionsRef.current.clear(); // 换上下文＝换一段对话，卡片落位重来
  }, [parseContextRef]);

  useEffect(() => {
    if (!parseContextRef) return;
    let cancelled = false;
    void fetchChatTranscript(projectId, 'analysis', parseContextRef)
      .then((res) => {
        if (cancelled || !res.rows.length) return;
        // 序号在更新函数之外算（更新函数须无副作用，裁定 F8 顺带项）
        const hydrated: ChatMsg[] = res.rows.map((r) => {
          msgSeqRef.current += 1;
          const b = transcriptRowToBubble(r);
          // 知识抽取页 ChatMsg 无 'ai' 类（助手仅命令回执/失败回执）；'ai' 落回 'sys-ok' 兜底。
          const kind: ChatMsg['kind'] = b.tone === 'ai' ? 'sys-ok' : (b.tone as Exclude<TranscriptBubbleTone, 'ai'>);
          return { id: msgSeqRef.current, kind, text: b.text, at: b.at, sourceId: r.id };
        });
        setMessages((current) => mergeHydratedMessages(current, hydrated));
      })
      .catch((error) => {
        // 不再静默吞（裁定 N7）：留痕读失败与「本来就没有历史」在界面上原本无从区分
        console.warn('[analysis] 历史消息读取失败', error);
        if (!cancelled) {
          setErrorText('历史消息读取失败，可刷新重试。');
        }
      });
    return () => { cancelled = true; };
  }, [parseContextRef, projectId]);

  // ---- 链路回执条（04A §2.1 增补）：阶段=后端事实；停滞=前端派生 ----
  const [trace, setTrace] = useState<AiRequestTrace | null>(null);
  const [traceNow, setTraceNow] = useState(() => Date.now());
  const traceRef = useRef<AiRequestTrace | null>(null);
  const stallProbedRef = useRef(false);
  const updateTrace = useCallback(
    (fn: (current: AiRequestTrace | null) => AiRequestTrace | null) => {
      traceRef.current = fn(traceRef.current);
      setTrace(traceRef.current);
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
    const current = traceRef.current;
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
  }, [messages]);

  const activeRunRef = useRef<{ runId: string; settled: boolean } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  // 区3 画布台（不滚动的定位承载层）：遮罩/共用标注气泡/选区操作条挂在这一层而非滚动容器内，
  // 气泡落点因此直接取可视区坐标，无需补滚动量。
  const canvasStageRef = useRef<HTMLDivElement | null>(null);
  // K1：画布上一次鼠标按下的视口坐标。onClick / mouseUp 用它算「按下→松手」位移，区分点选与拖选。
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // useModal 而非静态 Modal.confirm：确认弹层需消费 ConfigProvider 动态主题（五主题切换）
  const [reidentifyModal, reidentifyModalContextHolder] = Modal.useModal();
  // AI 修订在途目标：运行结束后若仍无修订稿，提示原因（执行器拒绝编造原文没有的事实）
  const pendingReviseRef = useRef<string | null>(null);
  // 「AI 正在起草修订…」那条在场消息的 id：起草收束时就地改写成结果，不再另起一条
  const pendingReviseMsgRef = useRef<number | null>(null);
  // 在途修订守卫的二次确认：非空＝有条目正被 AI 起草修订，等用户决定怎么继续
  const [inflightGuard, setInflightGuard] = useState<
    { guarded: GuardedElementRead[]; eligible: string[]; skipped: number } | null
  >(null);
  // SSE 主、轮询兜底：统一交由 useAgentRunWatcher（P0 收编——EventSource 订阅由 hook 持有生命周期，
  // 卸载清理/cancelled 终止/按 run 隔离，issue #10 B2b ④）。只解构 start/stop（各自 useCallback 恒稳）：
  // 整容器随 watching/stalled 变化换新，若入 effect deps 会令卸载专用 cleanup 退化为每渲染 cleanup、
  // 自毁在途订阅与轮询（合并裁定 F1）。订阅关闭、卸载清理由 hook 内部完成，页面不再自持 subscriptionRef。
  const { start: startRunWatch, stop: stopRunWatch } = useAgentRunWatcher({ intervalMs: 1000 });

  // 进入分析阶段即加载已接入材料正文（未识别态区3 只读呈现）。
  useEffect(() => {
    let cancelled = false;
    setBaseCanvas(null);
    analysisApi
      .getMaterialCanvas(projectId, materialRef)
      .then((canvas) => {
        if (!cancelled) {
          setBaseCanvas(canvas);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorText(getErrorMessage(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [materialRef, projectId]);

  const applyWorkspace = useCallback((next: ElementWorkspaceRead) => {
    const prevContextRef = parseContextRefRef.current;
    const prevSelectedId = selectedElementIdRef.current;
    setWorkspace(next);
    setParseContextRef(next.parse_context_ref);
    setDraftDismissed(false);
    setHistory(null);
    const ids = (next.elements ?? []).map((e) => e.id);
    // 勾选集＝可裁决集合，既有知识项不该驻留其中，所以只按 next.elements 收窄；
    // 而选中态只是「区4 正在看哪一条」，既有项也可以被选中，故校验集合并上既有项（裁定 C11）
    const selectableIds = new Set([...ids, ...(next.merged_existing_elements ?? []).map((e) => e.id)]);
    // K8：气泡清除收窄——只有识别上下文真换了一份（重新识别），或旧气泡引用在新工作区一条都解析不到
    // （退化成空壳，复用渲染处 `if(!element)return null` 判据；解析集＝ids∪既有项），才清。增量刷新
    // （对话/复核命令结算，同一 parse_context_ref、要素行未换）保留用户正在读的气泡，不被后台结算无声关掉。
    setOverlap((current) => {
      if (!current) return current;
      const contextReplaced = prevContextRef !== next.parse_context_ref;
      const anyRefResolvable = current.refs.some((ref) => selectableIds.has(ref));
      return contextReplaced || !anyRefResolvable ? null : current;
    });
    // 相位三态：识别失败停靠与识别在飞在数据上长得一样，靠后端给的 retry 动作区分（裁定 C1）
    const nextPhase = deriveRecognitionPhase(next);
    setPhase(nextPhase);
    if (nextPhase !== 'recognizing') {
      setRecognitionOwned(false);
    }
    setCheckedIds((current) => current.filter((id) => ids.includes(id)));
    // K12：mergeChecked 是一串旧要素 id，照 checkedIds 的写法按新 id 集裁剪；否则合并浮层开着时经历
    // 后台结算会持有已不存在的旧 id，点「组稿命令文本」组出带空名的合并命令（disabled 数组非空拦不住）。
    setMergeChecked((current) => current.filter((id) => ids.includes(id)));
    // 建议剔除候选区默认折叠，落在里面的行在区1 上看不见，默认选中因此要避开它们（冷审查裁定
    // C2）。后端挑 selected_element_ref 时已按同一判据跳过候选，这里的兜底 ids[0] 也照做，
    // 两侧口径一致；全部知识项都是候选时才落回候选（否则没有目标可选）。
    const triageCandidateIds = new Set(
      (next.elements ?? []).filter((e) => isTriageCandidate(e)).map((e) => e.id),
    );
    const backendSelected = next.selected_element_ref ?? null;
    const fallbackSelectedId =
      (backendSelected && !triageCandidateIds.has(backendSelected) ? backendSelected : null)
      ?? ids.find((id) => !triageCandidateIds.has(id))
      ?? backendSelected
      ?? ids[0]
      ?? null;
    const nextSelectedId = prevSelectedId && selectableIds.has(prevSelectedId)
      ? prevSelectedId
      : fallbackSelectedId;
    setSelectedElementId(nextSelectedId);
    // 换了一份识别上下文＝换一箱候选，候选区展开态跟着复位（冷审查裁定 C8 顺带项）
    if (prevContextRef !== next.parse_context_ref) {
      setTriageOpen(false);
    }
    // 选中项落在候选区里就把分组展开（冷审查裁定 C8）：折叠时区1 根本没有它的行，用户只看到
    // 区4 换了内容、区1 毫无变化，无从知道这一条在哪儿。
    if (nextSelectedId && triageCandidateIds.has(nextSelectedId)) {
      setTriageOpen(true);
    }
    // K12：applyWorkspace 是第三条会静默换选中目标的路径（另两条 selectElement/quickFill 换目标时都收起
    // 两个组稿浮层，否则先选普通项开浮层再被换成「已有」项能绕过只读限制）。仅当目标实际改变才收——
    // 目标没变的普通刷新不打断用户。
    if (nextSelectedId !== prevSelectedId) {
      setTypePickerOpen(false);
      setMergeDialogOpen(false);
    }
  }, []);

  // 返回拉取到的工作区（失败返回 null）：调用方若要在刷新后播报实值（如采纳修订稿后的
  // 新版本号），需要拿到这一份数据——受理接口的返回体里没有版本。
  const refreshWorkspace = useCallback(
    async (contextRef: string): Promise<ElementWorkspaceRead | null> => {
      try {
        const next = await analysisApi.getWorkspace(projectId, contextRef);
        applyWorkspace(next);
        setProgressText(null);
        setErrorText(null); // 刷新成功即清掉错误横幅，否则横幅上的「刷新工作区」自救按钮点了也清不掉
        return next;
      } catch (error) {
        setErrorText(getErrorMessage(error));
        setPhase('failed');
        return null;
      }
    },
    [applyWorkspace, projectId],
  );

  // 恢复回放（AEP-072）：携带恢复锚点进入时，直接读回既有工作区（只读回放，不发起识别）。
  useEffect(() => {
    if (initialParseContextRef) {
      void refreshWorkspace(initialParseContextRef);
    }
    // 仅在挂载/锚点变化时回放一次
  }, [initialParseContextRef, refreshWorkspace]);

  // 无恢复锚点进入时的只读回放：先问后端这份材料最近一次识别上下文，识别过就读回既有工作区。
  // 缺这一步页面会把已识别的材料当成未识别——区5 输入与命令全禁用，而区2 再点识别会另起
  // 一份清单并把既有成果移出工作区（用户 2026-07-19 走查报障）。
  useEffect(() => {
    if (initialParseContextRef) {
      return undefined;
    }
    let cancelled = false;
    analysisApi
      .getMaterialParseContext(projectId, materialRef)
      .then((res) => {
        // 期间用户已自行发起识别或已有工作区 → 不覆盖（回放只补空白，不抢当前态）
        if (cancelled || !res.parse_context_ref || phaseRef.current !== 'idle') {
          return;
        }
        void refreshWorkspace(res.parse_context_ref);
      })
      .catch((error) => {
        // 回放失败不打断进页（页面停在未识别态，用户仍可发起识别），但要留下可查的痕迹
        console.warn('[analysis] 读取材料最近一次识别上下文失败，页面按未识别态呈现', error);
      });
    return () => {
      cancelled = true;
    };
  }, [initialParseContextRef, materialRef, projectId, refreshWorkspace]);

  const settleRun = useCallback(
    (runStatus: string, runError: string | null | undefined, inline: unknown, contextRef: string) => {
      const active = activeRunRef.current;
      if (!active || active.settled || !TERMINAL_RUN_STATUSES.has(runStatus)) {
        return;
      }
      active.settled = true;
      stopRunWatch(); // 停表并关闭 hook 持有的 EventSource 订阅

      // 链路回执条收尾（仅对话请求挂链路；识别/画布扫描等沿用通用回执）
      const activeTrace = traceRef.current;
      if (runStatus === 'succeeded') {
        if (activeTrace && activeTrace.finishedAt === null) {
          const done = traceFinish(
            traceAdvance(activeTrace, 'writing', Date.now()), 'done', Date.now(),
          );
          updateTrace(() => null); // 完成收敛：链路条塌缩进回执
          const label = done.operationLabel ? `［${done.operationLabel}］` : '';
          pushMsg('sys-ok', `${label}AI 已返回结果 · ${traceSummaryText(done)}`, traceDetailLines(done));
        } else {
          pushMsg('sys', 'AI 已返回结果。');
        }
        if (isWorkspaceRead(inline)) {
          applyWorkspace(inline);
          setProgressText(null);
          return;
        }
        void refreshWorkspace(contextRef);
        return;
      }
      if (activeTrace && activeTrace.finishedAt === null) {
        // 失败终态：链路条固定于失败节点（保留至下次发送）
        updateTrace((t) => (t ? traceFinish(t, 'failed', Date.now()) : t));
      }
      pushMsg('sys-warn', runError || `AI 任务未完成（${runStatus}），可重试。`,
        activeTrace ? traceDetailLines(activeTrace) : undefined);
      setProgressText(null);
      void refreshWorkspace(contextRef);
    },
    [applyWorkspace, stopRunWatch, pushMsg, refreshWorkspace, updateTrace],
  );

  const watchRun = useCallback(
    (runId: string, contextRef: string) => {
      activeRunRef.current = { runId, settled: false };

      // 队列支点灯：worker 接单（started）是后端事实，SSE 事件与轮询状态都可点亮「执行」
      const markRunning = () => {
        updateTrace((t) => (t && t.finishedAt === null ? traceAdvance(t, 'running', Date.now()) : t));
      };

      const poll = async (): Promise<RunPollTick> => {
        const active = activeRunRef.current;
        if (!active || active.runId !== runId || active.settled) {
          return { done: true }; // 已被抢占/结算：停表
        }
        try {
          const run = await agentRunApi.get(runId);
          if (run.status === 'started') {
            markRunning();
          }
          if (TERMINAL_RUN_STATUSES.has(run.status)) {
            settleRun(run.status, run.error, null, contextRef);
            return { done: true };
          }
          return { done: false };
        } catch (error) {
          // Analysis 既有行为：poll 出错即停（不续表），就地给错误
          setErrorText(getErrorMessage(error));
          setProgressText(null);
          return { done: true };
        }
      };

      // EventSource 优先、轮询兜底：订阅工厂交 hook，hook 在 onFallback（含 EventSource 不可用时
      // agentRunApi.subscribe 立即回调）起轮询兜底，并持有订阅生命周期（stop/卸载/新 start 时关闭）。
      startRunWatch(poll, {
        subscribe: ({ onFallback }) =>
          agentRunApi.subscribe(
            runId,
            (message: AgentRunEventMessage) => {
              if (message.status === 'started' || message.event === 'agent_run.started') {
                markRunning();
              }
              const status = message.status && TERMINAL_RUN_STATUSES.has(message.status)
                ? message.status
                : message.event
                  ? SSE_TERMINAL_EVENT_STATUS[message.event] ?? (TERMINAL_RUN_STATUSES.has(message.event) ? message.event : null)
                  : null;
              if (status) {
                settleRun(status, message.error, message.result, contextRef);
              }
            },
            () => {
              // 轮询降级检查点（AC-P0-03）：EventSource 被阻断/不支持时兜底轮询接管（不落原文/token）
              console.info('agent_run_watch.fallback_to_poll', { run_id: runId });
              onFallback();
            },
          ),
      });
    },
    [startRunWatch, settleRun, updateTrace],
  );

  // ---- 区2：分析（P02 识别命令）----

  const handleAnalyze = useCallback(async () => {
    setErrorText(null);
    setPhase('recognizing');
    setRecognitionOwned(true);
    setProgressText('知识项识别送检中…');
    stallProbedRef.current = false;
    // 送检即建链路条：点下按钮到后端回执之间原本界面零反馈，用户不知道请求有没有出去。
    // 建链路只是把这条请求的进度条摆出来，各盏灯仍等后端事实才点亮（提案 03 篇 §1）。
    updateTrace(() => ({
      ...createTrace(Date.now()),
      path: RECOGNITION_TRACE_PATH,
      operationLabel: RECOGNITION_TRACE_LABEL,
    }));
    try {
      const result = await analysisApi.submitRecognition(projectId, {
        project_ref: projectId,
        material_ref: materialRef,
        operator_ref: operatorRef || 'current-user',
        idempotency_key: createIdempotencyKey(),
      });
      if (result.status === 'rejected_precheck' || !result.parse_context_ref) {
        updateTrace((t) => (t ? traceFinish(t, 'failed', Date.now()) : t));
        setPhase('failed');
        setRecognitionOwned(false);
        setErrorText(result.next_action ?? '识别前置校验未通过');
        setProgressText(null);
        return;
      }
      // 后端事实①：回执带识别上下文＝请求已被受理
      updateTrace((t) => (t ? traceAdvance(t, 'accepted', Date.now()) : t));
      setParseContextRef(result.parse_context_ref);
      if (result.agent_run_ref) {
        // 后端事实②：回执带运行引用＝已派发出一个排队执行的运行；此后交既有 watchRun/settleRun
        const runRef = result.agent_run_ref;
        updateTrace((t) =>
          t ? traceExtendQueue(traceAdvance(t, 'dispatching', Date.now()), runRef, Date.now()) : t,
        );
        watchRun(runRef, result.parse_context_ref);
        return;
      }
      await refreshWorkspace(result.parse_context_ref);
      // 同步返回（没有排队运行）：链路就地收敛成一条带耗时的回执行，与对话请求同口径
      const done = traceFinish(traceRef.current ?? createTrace(Date.now()), 'done', Date.now(), {
        operationLabel: RECOGNITION_TRACE_LABEL,
      });
      updateTrace(() => null);
      pushMsg('sys-ok', `［${RECOGNITION_TRACE_LABEL}］识别已完成 · ${traceSummaryText(done)}`,
        traceDetailLines(done));
    } catch (error) {
      updateTrace((t) => (t ? traceFinish(t, 'failed', Date.now()) : t));
      setPhase('failed');
      setRecognitionOwned(false);
      setErrorText(getErrorMessage(error));
      setProgressText(null);
    }
  }, [materialRef, operatorRef, projectId, pushMsg, refreshWorkspace, updateTrace, watchRun]);

  // ---- 派生 VM ----

  const anchors = useMemo(
    () => (workspace ? resolveWorkspaceAnchors(workspace) : new Map()),
    [workspace],
  );
  // 既有知识项（本次识别按同名归并到既往材料的项）：只读参与区1 显示与区3 标注，
  // 不进裁决/批量/条目形成门禁——写路径跨材料连锁另议。
  const mergedExistingElements = useMemo(
    () => workspace?.merged_existing_elements ?? [],
    [workspace],
  );
  const mergedExistingIds = useMemo(
    () => new Set(mergedExistingElements.map((e) => e.id)),
    [mergedExistingElements],
  );
  const allListItems: ElementListItemVM[] = useMemo(
    () =>
      workspace
        ? mapElementList([...(workspace.elements ?? []), ...mergedExistingElements], anchors, mergedExistingIds)
        : [],
    [anchors, mergedExistingElements, mergedExistingIds, workspace],
  );
  const listItems = useMemo(
    () =>
      allListItems.filter((item) => {
        if (item.superseded) {
          return (
            statusFilter === 'all' && completenessFilter === 'all' && categoryFilter === 'all' && typeFilter === 'all'
          );
        }
        const statusOk =
          statusFilter === 'all' ||
          (statusFilter === 'has_draft' ? item.hasDraft : item.statusCode === statusFilter);
        const categoryOk =
          categoryFilter === 'all' || elementTypeMeta(item.typeCode).category === categoryFilter;
        const typeOk = typeFilter === 'all' || item.typeCode === typeFilter;
        return statusOk && categoryOk && typeOk && matchesCompletenessFilter(item, completenessFilter);
      }),
    [allListItems, statusFilter, completenessFilter, categoryFilter, typeFilter],
  );
  // 区1 拆两处（乙案）：正常列表只装模型认为有价值的项，建议剔除的候选收进列表底部的独立分组。
  // 正常列表跟着上方筛选器走；候选区不受筛选影响，它是一个独立的箱子——组名上的计数必须等于
  // 箱子里实际有多少条，跟着筛选变就对不上了。故候选取自未筛选的全集。
  const normalListItems = useMemo(() => splitTriageCandidates(listItems).normal, [listItems]);
  const triageCandidates = useMemo(() => splitTriageCandidates(allListItems).candidates, [allListItems]);
  // 候选 id 集合走 ref：selectElement 是零依赖的 useCallback（多处回调与 effect 长期持有它），
  // 用 ref 读最新值，不把整份工作区拖进它的依赖数组。
  const triageCandidateIdsRef = useRef<Set<string>>(new Set());
  triageCandidateIdsRef.current = useMemo(
    () => new Set(triageCandidates.map((item) => item.id)),
    [triageCandidates],
  );
  const canvas = workspace?.material_canvas ?? baseCanvas;
  const canvasBlocks: CanvasBlockVM[] = useMemo(() => {
    if (!canvas) {
      return [];
    }
    const highlights = workspace ? buildHighlights(workspace, anchors) : [];
    const blocks = canvas.blocks ?? [];
    return fillSegmentText(blocks, buildCanvasBlocks(blocks, highlights));
  }, [anchors, canvas, workspace]);

  const elementsById = useMemo(() => {
    const map = new Map<string, RequirementElementRead>();
    for (const e of [...(workspace?.elements ?? []), ...mergedExistingElements]) {
      map.set(e.id, e);
    }
    return map;
  }, [mergedExistingElements, workspace]);

  const liveElements = useMemo(
    () => (workspace?.elements ?? []).filter((e) => !e.superseded),
    [workspace],
  );
  // 偏离原文集合（后端派生投影 source_drift_tokens）：区3 警示样式 + 区4 引导
  const driftedElementIds = useMemo(() => {
    const ids = new Set<string>();
    for (const e of liveElements) {
      if ((e.source_drift_tokens ?? []).length) {
        ids.add(e.id);
      }
    }
    return ids;
  }, [liveElements]);
  const selectedElement = selectedElementId ? elementsById.get(selectedElementId) ?? null : null;
  // 选中的是既有知识项 → 一切写命令关闭（本卡只读，见区5 引导文案）
  const selectedIsMergedExisting = Boolean(selectedElementId && mergedExistingIds.has(selectedElementId));
  // 选中的这条在建议剔除候选区里（→ 区5 的「确认」要拦下：先撤回再确认，否则一条已确认的
  // 知识项会留在候选区里，用户在正常列表怎么也找不到它）
  const selectedIsTriageCandidate = Boolean(
    selectedElement && isTriageCandidate(selectedElement, selectedIsMergedExisting),
  );
  // 已被人工撤回、如今待在正常列表里的建议剔除项（→ 详情区给「移回候选区」，撤回可逆）
  const selectedIsRestoredFromTriage = Boolean(
    selectedElement &&
      selectedElement.model_verdict === 'suspected_noise' &&
      selectedElement.noise_triage === 'restored' &&
      !selectedElement.superseded &&
      !selectedIsMergedExisting,
  );
  const selectedFacetReview = mapFacetReview(selectedElement?.facet_review);
  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of liveElements) {
      counts.set(e.process_status, (counts.get(e.process_status) ?? 0) + 1);
    }
    return counts;
  }, [liveElements]);

  const operationsByKey = useMemo(() => {
    const map = new Map<string, { enabled: boolean; reason?: string | null }>();
    for (const op of workspace?.available_operations ?? []) {
      map.set(op.key, { enabled: op.enabled, reason: op.disabled_reason });
    }
    return map;
  }, [workspace]);

  // 识别失败停靠时后端给的重试出口（裁定 C1：此前 available_actions 里的 retry 前端零消费）
  const canRetryRecognition = Boolean(
    workspace?.available_actions?.find((a) => a.key === 'retry')?.enabled,
  );
  const workspaceVersion = workspace?.workspace_version ?? '1';
  const draft: ElementChangeDraftRead | null = workspace?.change_draft ?? null;
  const isBusy = phase === 'recognizing' || phase === 'operating';
  // 识别遮罩生效期间＝写入类交互一并冻结（R1/P3）。遮罩已改 pointer-events:none 只做视觉，
  // 「识别中禁交互」从遮罩物理拦截移到这个显式守卫；条件与遮罩渲染完全一致（见下方 .analysis-canvas-mask
  // 渲染处）——非本页发起的识别不盖遮罩、让用户读，故也不在此禁掉只读交互。
  const recognitionLocked = phase === 'recognizing' && recognitionOwned;
  const itemFormationAction = workspace?.available_actions?.find((a) => a.key === 'start_item_formation');
  const canEnterItemFormation = Boolean(itemFormationAction?.enabled);

  // 勾选集合里真正可裁决的那些：已替代的与建议剔除候选都不算。候选条目进得来是因为
  // 「撤回到正常列表 → 勾选 → 再移回候选区」这条路径不会清掉勾选（冷审查裁定 C1）。用户拍板的
  // 口径是候选条目不参与任何批量入口，故在此源头滤一次，下游的批量确认/拒绝、复核送检、区5
  // 组稿命令、已选计数一并受益。
  const checkedLive = useMemo(
    () => withoutTriageCandidates(checkedIds, elementsById, mergedExistingIds),
    [checkedIds, elementsById, mergedExistingIds],
  );
  // 批量分诊模式：勾选 ≥2 条 → 区5 仅确认/拒绝有效（勾选 1 条等价单条）
  const batchMode = checkedLive.length >= 2;
  /**
   * 区5 输入框的写入锁（单一来源）：输入框、发送按钮、全部预填药丸、区3 两个写输入框的按钮
   * 共用这一个判据，不再各写各的条件。此前它们各自抄了一份且互不相同，于是出现「按钮可用、
   * 它要写入的输入框已禁用」——文字写进去了，用户改不动也发不出，或发出去后没有识别上下文
   * 可受理，链路条永远转下去。
   *
   * 四项的含义：批量分诊模式只走确认/拒绝；有命令在飞时不接新的；首次识别之前没有识别上下文
   * 可受理；选中的是「已有」的知识项时本页只读（该条锁此前唯独区3 的按钮不遵守）。
   */
  const composerLocked = batchMode || isBusy || phase === 'idle' || selectedIsMergedExisting;
  // 区3「当前选区」另有一道正文形态门：选区说明只有配合「改范围」或用户自己写的描述才读得通，
  // 跟在别的命令词后面会被后端当成那条命令的参数正文（判据与理由见 acceptsSelectionAffordance）。
  const selectionAffordanceAccepted = acceptsSelectionAffordance(composerText);
  // 裁定目标：勾选优先，否则当前选中
  const decisionTargets = useMemo(() => {
    if (checkedLive.length) {
      return checkedLive;
    }
    return selectedElement && !selectedElement.superseded && !selectedIsMergedExisting
      ? [selectedElement.id]
      : [];
  }, [checkedLive, selectedElement, selectedIsMergedExisting]);
  // 确认另有一份更窄的目标：候选条目不能就地确认——确认不会把它挪出候选区（候选判据看的是模型
  // 裁定与人工处置，不是确认状态），于是会留下一条「已确认」却在正常列表遍寻不着的知识项。
  // 撤销不在此列：撤销是候选区的正当出口，处置完这一条随即离箱，不能跟着一起挡掉。
  const confirmTargets = useMemo(
    () => withoutTriageCandidates(decisionTargets, elementsById, mergedExistingIds),
    [decisionTargets, elementsById, mergedExistingIds],
  );

  // ---- 区3：选中 / 文本选择 / 重叠 ----

  const selectElement = useCallback((elementId: string) => {
    setSelectedElementId(elementId);
    setHistory(null);
    setOverlap(null);
    // 换了目标就收起两个组稿浮层（裁定 C9）：此前只在 quickFill 里复位，于是
    // 「先选普通项打开合并浮层 → 再点一条『已有』项 → 在浮层里组稿」能绕过只读限制
    setTypePickerOpen(false);
    setMergeDialogOpen(false);
    // 选中的这条在候选区里就先把分组展开（冷审查裁定 C8）：候选行只在展开态下渲染，折叠时
    // 下面那次 querySelector 取不到行，滚动定位落空且没有任何提示——用户在区3 点了一条候选的
    // 高亮，区4 换了内容而区1 毫无反应。
    if (triageCandidateIdsRef.current.has(elementId)) {
      setTriageOpen(true);
    }
    // 双向联动：无论从区3 标注、重叠浮层还是详情区选中，区1 列表都滚动到对应知识项。
    // rAF 让列表先按新选中态重渲染，再对已存在的行做最小滚动（block:nearest 不越界滚整页）。
    requestAnimationFrame(() => {
      listRef.current?.querySelector(`[data-element-id="${elementId}"]`)
        ?.scrollIntoView({ block: 'nearest' });
    });
  }, []);

  // K1：入口收纯坐标 {x,y} 而非 React.MouseEvent——鼠标传点击坐标、键盘传段自身矩形坐标，
  // 于是键盘打开的气泡也有有效落点（消解 P2 的 NaN 坐标）。段内拖选的「位移判据」不在这里，
  // 只在 JSX 的鼠标 onClick 上（键盘入口不设选区门，否则文档任意位置有选区就键盘激活恒失效）。
  const handleSegmentClick = useCallback(
    (refs: string[], point: { x: number; y: number }) => {
      if (recognitionLocked) return; // R1/P3：识别中禁开气泡/选中（键盘回车也经 onKeyDown 路由至此）
      if (refs.length === 1) {
        selectElement(refs[0]);
        return;
      }
      // 落点基准＝画布台（不随内容滚动），故坐标减去台面矩形即最终落点，不需要滚动量补偿；
      // 越界钳制由下方 overlap 布局 effect 在测量气泡实高后统一算（横向钳进台面、纵向只防出页面）。
      const rect = canvasStageRef.current?.getBoundingClientRect();
      setOverlap({
        refs,
        x: point.x - (rect?.left ?? 0),
        y: point.y - (rect?.top ?? 0) + 16,
      });
    },
    [selectElement, recognitionLocked],
  );

  const handleCanvasMouseUp = useCallback((event: React.MouseEvent) => {
    if (recognitionLocked) return; // R1：识别中禁生成选区
    // K1/D3：手抖点击（Chrome 文本选择无拖动阈值，微移就扩出单字符选区）不该造出「已选原文」条。
    // 按下→松手位移小于阈值即判为点击而非拖选，直接放行、不动选区——既不误弹单字符条（D3），
    // 也不清掉点击落在既有选区内时用户仍在看的操作条（D1/D4）。
    const down = pointerDownRef.current;
    if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) <= SEGMENT_DRAG_THRESHOLD_PX) {
      return;
    }
    const domSelection = window.getSelection();
    if (!domSelection || domSelection.isCollapsed) {
      return;
    }
    const range = domSelection.getRangeAt(0);
    // R4：判定基准＝画布台（操作条/气泡已移出滚动容器 wrap）。真逃逸（从区1/图例/页面别处拖入）时
    // 公共祖先升到台面之外，清掉陈旧选区避免屏幕高亮与操作条读数打架。`contains` 只作廉价前置过滤，
    // 真正决定落空的是下方 toGlobal 取不到 data-seg-start（4753 条 Range 穷举证明二者终态一致）。
    const stageEl = canvasStageRef.current;
    if (!stageEl || !stageEl.contains(range.commonAncestorContainer)) {
      setSelection(null);
      return;
    }
    const toGlobal = (node: Node, offset: number): number | null => {
      const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement);
      const segStart = el?.getAttribute('data-seg-start');
      if (segStart === null || segStart === undefined) {
        return null;
      }
      return Number(segStart) + offset;
    };
    const start = toGlobal(range.startContainer, range.startOffset);
    const end = toGlobal(range.endContainer, range.endOffset);
    if (start === null || end === null || end <= start) {
      setSelection(null);
      return;
    }
    const raw = canvas?.raw_text ?? '';
    setSelection({ start, end, text: raw.slice(start, end) });
  }, [canvas, recognitionLocked]);

  // R2/R3/K3：测量气泡实高与操作条实占高，把落点钳进可视范围（横向不越区5、纵向不出页面、不压操作条），
  // 放不下时给出限高。抽成独立函数供布局 effect 与 ResizeObserver 共用。依赖 selection 是必须的——
  // 先开气泡再拖选时操作条挤压 wrap，气泡 top 是相对台面的常量不会自动跟着变，须在选区变化时重算。
  const recomputeOverlapPos = useCallback(() => {
    if (!overlap) {
      setOverlapPos(null);
      return;
    }
    const pop = overlapRef.current;
    const stage = canvasStageRef.current;
    // jsdom / 尚未排版：clientWidth 为 0，测不了几何，保持原始落点（现有结构用例断言不被打破）
    if (!pop || !stage || stage.clientWidth === 0) {
      return;
    }
    const stageRect = stage.getBoundingClientRect();
    const main = stage.closest('.workbench-main') as HTMLElement | null;
    let pageBottomInStage = Number.POSITIVE_INFINITY;
    if (main) {
      const mainRect = main.getBoundingClientRect();
      const padBottom = parseFloat(getComputedStyle(main).paddingBottom) || 0;
      // K9：≤1080px 断点下 .workbench-main 回落 overflow:visible 随内容长高，mainRect.bottom 变成
      // 文档下沿而非可见页面下沿（实测高估近 1000px）；与视口下沿取小，无选区时气泡不落到视口折线以下。
      pageBottomInStage = Math.min(mainRect.bottom, window.innerHeight) - padBottom - stageRect.top;
    }
    const bar = selectionBarRef.current;
    const barH = bar ? bar.offsetHeight + (parseFloat(getComputedStyle(bar).marginTop) || 0) : 0;
    const next = clampOverlapPosition(
      { x: overlap.x, y: overlap.y },
      {
        popW: pop.offsetWidth,
        popH: pop.offsetHeight,
        stageW: stage.clientWidth,
        stageH: stage.clientHeight,
        barH,
        pageBottomInStage,
        cursorY: overlap.y - 16,
      },
    );
    // S14：相等短路只为省掉相同数值下的多余重渲染——overlapPos 不在依赖里，去掉它也不会自激循环。
    setOverlapPos((prev) =>
      prev && prev.left === next.left && prev.top === next.top && prev.maxHeight === next.maxHeight
        ? prev
        : next,
    );
  }, [overlap, selection]);

  // 布局 effect：气泡开合与选区变化时在 paint 前重算一次（useLayoutEffect 无位移闪烁）。
  useLayoutEffect(() => {
    recomputeOverlapPos();
  }, [recomputeOverlapPos]);

  // K3：台面与气泡各挂 ResizeObserver，几何变化时重算——区4 加载历史挤矮区3（不发 window.resize）、
  // 改窗口宽或缩放使气泡随根字号变高，都要跟着钳。jsdom 无 ResizeObserver → 存在性守卫，
  // 真实重算行为以槽内浏览器证据补位（A4）。
  useEffect(() => {
    if (!overlap || typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const observer = new ResizeObserver(() => recomputeOverlapPos());
    const stage = canvasStageRef.current;
    const pop = overlapRef.current;
    if (stage) observer.observe(stage);
    if (pop) observer.observe(pop);
    return () => observer.disconnect();
  }, [overlap, recomputeOverlapPos]);

  const handleListSelect = useCallback(
    (elementId: string) => {
      selectElement(elementId);
      const target = canvasRef.current?.querySelector(`[data-first-ref="${elementId}"]`);
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },
    [selectElement],
  );

  // 整行承担 option 语义后，键盘激活也落在行上（此前是行内正文按钮的原生按键行为）。
  // 只认落在行自身的按键：行内还有勾选框与「撤回到正常列表」按钮，它们各自的空格/回车
  // 语义不能被行截走（事件冒泡上来时 target 是那个控件，不是行）。
  const handleRowKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>, elementId: string) => {
      if (event.target !== event.currentTarget) {
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); // 空格默认滚动页面
        handleListSelect(elementId);
      }
    },
    [handleListSelect],
  );

  const toggleChecked = useCallback((elementId: string, checked: boolean) => {
    setCheckedIds((current) =>
      checked ? [...current.filter((id) => id !== elementId), elementId] : current.filter((id) => id !== elementId),
    );
  }, []);

  // ---- 命令提交公共骨架 ----

  const runCommand = useCallback(
    async (command: () => Promise<void>) => {
      if (!parseContextRef) {
        return;
      }
      setErrorText(null);
      setPhase('operating');
      try {
        await command();
      } catch (error) {
        setErrorText(getErrorMessage(error));
        await refreshWorkspace(parseContextRef);
      } finally {
        setPhase((current) => (current === 'operating' ? 'ready' : current));
      }
    },
    [parseContextRef, refreshWorkspace],
  );

  const selectionRanges = buildSelectionRanges(selection);

  // ---- 裁决条：确认 / 拒绝（结构化直发；批量=同一事件 N 次应用，逐条回执）----

  /** 真正提交裁决。skipped＝提交目标里非待确认、本次没送出去的条数，只用于回执文案。 */
  const submitDecision = useCallback(
    (
      decision: 'confirm' | 'reject',
      eligible: string[],
      skipped: number,
      options?: { acknowledgeInflightRevision?: boolean; skippedInflight?: number },
    ) => {
      void runCommand(async () => {
        if (!parseContextRef || !eligible.length) return;
        const verb = decision === 'confirm' ? '确认' : '拒绝';
        pushMsg('cmd', `${verb}${eligible.length > 1 ? `（${eligible.length} 条）` : '当前要素'}`);
        const next = await analysisApi.decideElements(projectId, parseContextRef, {
          parse_context_ref: parseContextRef,
          workspace_version: workspaceVersion,
          element_refs: eligible,
          decision,
          operator_ref: operatorRef || 'current-user',
          idempotency_key: createIdempotencyKey(),
          inflight_revision_ack: options?.acknowledgeInflightRevision ?? false,
        });
        setCheckedIds([]);
        applyWorkspace(next);
        // 两类跳过分开报数、各给各的理由：非待确认是勾选时就不合规，正在起草修订是守卫弹层里用户选择跳过
        const skippedParts = [
          skipped ? `${skipped} 条（非待确认）` : '',
          options?.skippedInflight ? `${options.skippedInflight} 条（正在起草修订）` : '',
        ].filter(Boolean);
        pushMsg('sys-ok', `已${verb} ${eligible.length} 条${skippedParts.length ? `，跳过 ${skippedParts.join('、')}` : ''}。`);
        // 裁决后自动前进到下一条待确认；跳过建议剔除候选（冷审查裁定 C2）——候选分组默认折叠，
        // 前进到一条看不见的行会让用户在区1 里找不到当前目标，也不知道确认为什么点不动。
        const nextPending = (next.elements ?? []).find(
          (e) => !e.superseded && e.process_status === 'pending_confirmation' && !isTriageCandidate(e),
        );
        if (nextPending) {
          setSelectedElementId(nextPending.id);
        }
      });
    },
    [applyWorkspace, operatorRef, parseContextRef, projectId, pushMsg, runCommand, workspaceVersion],
  );

  const handleDecide = useCallback(
    (decision: 'confirm' | 'reject') => {
      void runCommand(async () => {
        // 确认与撤销的提交目标不是同一份：候选条目不能确认，但可以撤销（撤销是候选区的正当出口）
        const targets = decision === 'confirm' ? confirmTargets : decisionTargets;
        if (!parseContextRef || !targets.length) return;
        const eligible = targets.filter(
          (id) => elementsById.get(id)?.process_status === 'pending_confirmation',
        );
        const skipped = targets.length - eligible.length;
        if (!eligible.length) {
          pushMsg('sys-warn', '勾选中没有可裁决（待确认）的要素。');
          return;
        }
        if (decision === 'confirm') {
          // 在途修订守卫：确认会把 AI 正在起草的修订稿搁置成没人采纳的孤儿稿，故先问一句。
          // 拒绝不问——拒绝本就把这条连同稿件一起废掉，没有「搁置」可言。
          // 预检失败不挡确认：守卫是提醒，不是门禁，网络抖动不该让人裁决不了。
          let guarded: GuardedElementRead[];
          try {
            const precheck = await analysisApi.precheckDecideElements(projectId, parseContextRef, {
              parse_context_ref: parseContextRef,
              element_refs: eligible,
            });
            guarded = precheck.guarded ?? [];
          } catch (error) {
            // 不再静默吞（裁定 N7 同口径）：预检挂了（401/500/网络）与「真没有在途修订」是两回事，
            // 守卫本次失效必须在 console 可见，否则无人知晓它没在工作。
            console.warn('[analysis] 确认预检失败，本次确认未经在途修订守卫', error);
            guarded = [];
          }
          if (guarded.length) {
            setInflightGuard({ guarded, eligible, skipped });
            return;
          }
        }
        submitDecision(decision, eligible, skipped);
      });
    },
    [confirmTargets, decisionTargets, elementsById, parseContextRef, projectId, pushMsg, runCommand, submitDecision],
  );

  // ---- 建议剔除候选的人工处置：撤回到正常列表 / 移回候选区（不改模型裁定、不迁状态）----

  const handleTriage = useCallback(
    (elementId: string, action: 'restore' | 'return') => {
      void runCommand(async () => {
        if (!parseContextRef) return;
        const verb = action === 'restore' ? '撤回到正常列表' : '移回建议剔除候选';
        pushMsg('cmd', verb);
        const next = await analysisApi.triageElements(projectId, parseContextRef, {
          parse_context_ref: parseContextRef,
          workspace_version: workspaceVersion,
          element_refs: [elementId],
          action,
          operator_ref: operatorRef || 'current-user',
          idempotency_key: createIdempotencyKey(),
        });
        applyWorkspace(next);
        if (action === 'return') {
          // 移回候选区的条目要一并退出勾选集合（冷审查裁定 C1）：勾选是批量裁决的入口，而候选
          // 条目不参与任何批量入口；不清掉的话它会跟着下一次「确认（n 条）」被一起确认掉。
          setCheckedIds((current) => current.filter((id) => id !== elementId));
          setMergeChecked((current) => current.filter((id) => id !== elementId));
        }
        pushMsg(
          'sys-ok',
          action === 'restore'
            ? // 撤回不迁移确认生命周期，所以回执不能一律说「回到待确认」——对本来就已确认或
              // 已撤销的条目那是假话（冷审查裁定 C6）。按这一条实际的确认状态播报。
              triageRestoreReceipt(next.elements?.find((e) => e.id === elementId)?.process_status)
            : '已移回建议剔除候选。',
        );
      });
    },
    [applyWorkspace, operatorRef, parseContextRef, projectId, pushMsg, runCommand, workspaceVersion],
  );

  // ---- 复核 = 对话轮次（不迁移状态）；自由文本复核意图从输入框来 ----

  const handleSubmitReview = useCallback(
    (intent: string, scan: boolean) => {
      void runCommand(async () => {
        if (!parseContextRef) return;
        setProgressText(scan ? '扫原文补漏送检中…' : 'AI 复核中…');
        const result = await analysisApi.submitReview(projectId, parseContextRef, {
          parse_context_ref: parseContextRef,
          workspace_version: workspaceVersion,
          target_element_refs: scan ? [] : decisionTargets,
          selected_text_ranges: scan ? selectionRanges : [],
          review_intent: intent,
          operator_ref: operatorRef || 'current-user',
          idempotency_key: createIdempotencyKey(),
        });
        if (result.status === 'rejected_precheck') {
          pushMsg('sys-warn', result.next_action ?? '复核前置校验未通过');
          setProgressText(null);
          return;
        }
        if (result.agent_run_ref) {
          watchRun(result.agent_run_ref, parseContextRef);
          return;
        }
        await refreshWorkspace(parseContextRef);
      });
    },
    [decisionTargets, operatorRef, parseContextRef, projectId, pushMsg, refreshWorkspace, runCommand, selectionRanges, watchRun, workspaceVersion],
  );

  // ---- 采纳修订稿（采纳即确认）----

  const handleFinalizeRevision = useCallback(
    (action: 'adopt' | 'abandon') => {
      void runCommand(async () => {
        if (!parseContextRef || !selectedElement) return;
        pushMsg('cmd', action === 'adopt' ? '采纳修订稿' : '不采纳（清除修订稿）');
        const result = await analysisApi.finalizeRevision(projectId, parseContextRef, {
          parse_context_ref: parseContextRef,
          workspace_version: workspaceVersion,
          element_ref: selectedElement.id,
          action,
          operator_ref: operatorRef || 'current-user',
          idempotency_key: createIdempotencyKey(),
        });
        if (result.status === 'rejected_precheck') {
          // 超出原文守卫：先补入依据再采纳
          pushMsg('sys-warn', result.next_action ?? '采纳被阻断：修订稿包含原文没有的事实，请先补入依据。');
          return;
        }
        if (action !== 'adopt') {
          pushMsg('sys-ok', '修订稿已清除，状态不变。');
          await refreshWorkspace(parseContextRef);
          return;
        }
        // 采纳的回执要报实值：版本号与状态都从刷新后的工作区读，不用「+1」让用户自己算，
        // 也不写死「→ 已确认」——本来就已确认的知识项采纳修订后状态并未迁移（走查反馈第②组）。
        const next = await refreshWorkspace(parseContextRef);
        const updated = (next?.elements ?? []).find((e) => e.id === selectedElement.id);
        pushMsg(
          'sys-ok',
          updated
            ? `修订已生效（v${updated.version ?? 1}）；这条知识项现在是${processStatusMeta(updated.process_status).label}。原文未被修改。`
            : '修订已生效。原文未被修改。',
        );
      });
    },
    [operatorRef, parseContextRef, projectId, pushMsg, refreshWorkspace, runCommand, selectedElement, workspaceVersion],
  );

  // ---- 重开 / 回流 + 历史 ----

  const handleReopen = useCallback(() => {
    void runCommand(async () => {
      if (!parseContextRef || !selectedElement) return;
      const next = await analysisApi.reopenElement(projectId, parseContextRef, {
        parse_context_ref: parseContextRef,
        workspace_version: workspaceVersion,
        element_ref: selectedElement.id,
        reason: null,
        operator_ref: operatorRef || 'current-user',
        idempotency_key: createIdempotencyKey(),
      });
      applyWorkspace(next);
    });
  }, [applyWorkspace, operatorRef, parseContextRef, projectId, runCommand, selectedElement, workspaceVersion]);

  // 孤儿稿的出路：这条已确认、稿子还搁着，回流退回待确认后才谈得上采纳。
  // 走的是既有回流迁移（产生新版本、旧版本进历史），只是把回流原因写成用户看得懂的话。
  const handleReflowToAdopt = useCallback(() => {
    void runCommand(async () => {
      if (!parseContextRef || !selectedElement) return;
      pushMsg('cmd', '回流以采纳搁置的修订稿');
      const next = await analysisApi.reopenElement(projectId, parseContextRef, {
        parse_context_ref: parseContextRef,
        workspace_version: workspaceVersion,
        element_ref: selectedElement.id,
        reason: '回流以采纳搁置的修订稿',
        operator_ref: operatorRef || 'current-user',
        idempotency_key: createIdempotencyKey(),
      });
      applyWorkspace(next);
      pushMsg('sys-ok', '已退回待确认，修订稿仍在。现在可以点「采纳修订稿」让它生效。');
    });
  }, [applyWorkspace, operatorRef, parseContextRef, projectId, pushMsg, runCommand, selectedElement, workspaceVersion]);

  const handleLoadHistory = useCallback(() => {
    void (async () => {
      if (!parseContextRef || !selectedElement) return;
      try {
        const result = await analysisApi.getElementHistory(projectId, parseContextRef, selectedElement.id);
        setHistory(result);
      } catch (error) {
        setErrorText(getErrorMessage(error));
      }
    })();
  }, [parseContextRef, projectId, selectedElement]);

  // ---- P04：确认创建 ----

  const handleConfirmDraft = useCallback(() => {
    void runCommand(async () => {
      if (!parseContextRef || !draft) return;
      const next = await analysisApi.confirmChangeDraft(projectId, parseContextRef, {
        parse_context_ref: parseContextRef,
        workspace_version: workspaceVersion,
        draft_ref: draft.draft_ref,
        operator_ref: operatorRef || 'current-user',
        idempotency_key: createIdempotencyKey(),
      });
      applyWorkspace(next);
    });
  }, [applyWorkspace, draft, operatorRef, parseContextRef, projectId, runCommand, workspaceVersion]);

  // ---- 区5：快捷命令（生成可编辑提示词）与输入框路由 ----

  // AI 修订运行结束后：把「起草中…」那条在场消息就地改写成结果。三种落点——
  // ① 没出稿：说明拒绝原因（不引入无来源事实是执行器约束）；
  // ② 出了稿、这条还待确认：现状流程，去详情区采纳；
  // ③ 出了稿、这条已被抢先确认：稿子成了孤儿，得先回流才能采纳（2026-07-25 报障场景）。
  useEffect(() => {
    const target = pendingReviseRef.current;
    if (!target || !workspace || progressText) {
      return;
    }
    pendingReviseRef.current = null;
    const settledMsgId = pendingReviseMsgRef.current;
    pendingReviseMsgRef.current = null;
    const el = (workspace.elements ?? []).find((e) => e.id === target);
    const hasDraft = !!(el?.revision_draft ?? '').trim();
    let tone: ChatMsg['kind'] = 'sys-ok';
    let text = '修订稿已就绪，可在详情区采纳。';
    if (!el || !hasDraft) {
      tone = 'sys-warn';
      text = 'AI 未产出修订稿——多半是指令要求的信息在原文（含补块）中没有依据。可换个说法只打磨表达，或先用「补入」登记新事实再请 AI 修订。';
    } else if (el.process_status === 'confirmed') {
      tone = 'sys-warn';
      text = '修订稿已就绪，但这条知识项在起草期间已被确认，稿子没有自动生效。要采纳它，先在详情区「回流以采纳」把这条退回待确认。';
    }
    if (settledMsgId !== null) {
      replaceMsg(settledMsgId, tone, text);
      return;
    }
    // 没有在场消息可改写（如页面中途重挂）时另起一条，事实不因界面状态丢失
    if (tone !== 'sys-ok') {
      pushMsg(tone, text);
    }
  }, [progressText, pushMsg, replaceMsg, workspace]);

  const quickFill = useCallback((text: string) => {
    setTypePickerOpen(false);
    setMergeDialogOpen(false);
    setComposerText(text);
  }, []);

  // 预填/改写正文后把光标交回输入框末尾（rAF 等这一次 setState 落到 DOM 再定位）
  const focusComposerAtEnd = useCallback(() => {
    requestAnimationFrame(() => {
      const composer = composerRef.current;
      if (composer) {
        composer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        composer.focus();
        composer.setSelectionRange(composer.value.length, composer.value.length);
      }
    });
  }, []);

  // ① 区3 拖选就地创建：复用「新增遗漏」写路径（manual.add_missing）——选区文本入表达、
  // 选区范围随 selectionRanges 送后端成为来源锚点；预填到区5 输入框（表达可编辑）并聚焦，
  // 用户核对后发送，行为与区5「新增遗漏」命令创建者完全一致（不新造后端接口）。
  const handleAddMissingFromSelection = useCallback(() => {
    if (!selection) {
      return;
    }
    quickFill(QUICK_COMMAND_PREFILLS.addMissing(selection.text));
    focusComposerAtEnd();
  }, [focusComposerAtEnd, quickFill, selection]);

  // ② 区3「当前选区」：把选区描述写进区5 输入框正文——替换无选区版引导语、就地更新已有的
  // 那一段、或自然衔接追加到用户自己写的正文末尾（规则与幂等由 withSelectionAffordance 纯函数定）。
  // 写进去的只是给人读的文字；选区数据本身仍随 selected_text_ranges 走既有请求上下文通道。
  // 不经 quickFill：那条通道只做整段覆盖，本按钮要先读当前正文再改写。
  const handleAppendSelectionAffordance = useCallback(() => {
    if (!selection) {
      return;
    }
    setTypePickerOpen(false);
    setMergeDialogOpen(false);
    setComposerText((current) => withSelectionAffordance(current, selection));
    focusComposerAtEnd();
  }, [focusComposerAtEnd, selection]);

  // 发送：前端不解析命令词——整段原文 + 上下文引用交 AEP-096（SSE 流式，stage 帧驱动链路回执条）
  const handleSend = useCallback(() => {
    const text = composerText.trim();
    if (!text) {
      return;
    }
    setComposerText('');
    const isCommand = text.startsWith('/') || text.startsWith('／');
    pushMsg(isCommand ? 'cmd' : 'user', text);
    stallProbedRef.current = false;
    updateTrace(() => createTrace(Date.now()));
    void runCommand(async () => {
      if (!parseContextRef) return;
      let result;
      try {
        result = await analysisApi.sendDialogueStream(projectId, parseContextRef, {
          parse_context_ref: parseContextRef,
          workspace_version: workspaceVersion,
          message: text,
          target_element_refs: decisionTargets,
          selected_text_ranges: selectionRanges,
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
        return;
      }
      const echo = result.operation_label ? `［${result.operation_label}］` : '';
      switch (result.outcome) {
        case 'executed': {
          if (result.workspace) {
            applyWorkspace(result.workspace);
          }
          const done = traceFinish(
            traceRef.current ?? createTrace(Date.now()), 'done', Date.now(),
            { operationLabel: result.operation_label },
          );
          updateTrace(() => null); // 完成收敛：链路条塌缩进回执
          pushMsg(
            'sys-ok',
            `${echo}${result.message ?? '已执行（留痕见区4 记录）。'} · ${traceSummaryText(done)}`,
            traceDetailLines(done),
          );
          return;
        }
        case 'queued':
          if (result.operation === 'revise.ai' && decisionTargets[0]) {
            pendingReviseRef.current = decisionTargets[0];
            // 「已受理排队中」这句太容易被读成「已经改完了」——2026-07-25 报障的直接诱因。
            // 换成一条留在流里的在场消息，起草结束时就地改写成结果，中途不会消失。
            pendingReviseMsgRef.current = pushMsg(
              'sys-pending', 'AI 正在为这条起草修订稿，稿子出来前这条知识项还是原样。',
            );
          }
          // 队列支：链路条扩展排队/执行/回写节点，由 AgentRun 状态/事件继续点灯
          updateTrace((t) => (t ? traceExtendQueue(
            { ...t, operationLabel: result.operation_label ?? t.operationLabel },
            result.agent_run_ref ?? null, Date.now(),
          ) : t));
          if (result.agent_run_ref) {
            watchRun(result.agent_run_ref, parseContextRef);
          } else {
            await refreshWorkspace(parseContextRef);
          }
          return;
        default: {
          // clarify / cannot_comply / unknown_command / rejected_precheck：回执文案直接入流
          const outcome = result.outcome === 'cannot_comply' ? 'refused' : 'clarify';
          const settled = traceFinish(
            traceRef.current ?? createTrace(Date.now()), outcome, Date.now(),
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
    });
  }, [applyWorkspace, composerText, decisionTargets, operatorRef, parseContextRef, projectId, pushMsg, refreshWorkspace, runCommand, selectionRanges, updateTrace, watchRun, workspaceVersion]);

  // ---- 渲染 ----

  const parseStatusMeta = workspace?.parse_status === 'parsed'
    ? { label: '已解析', tone: 'success' as const }
    : workspace?.parse_status === 'unprocessable'
      ? { label: '无可处理要素', tone: 'warning' as const }
      : phase === 'recognizing'
        ? { label: '识别中', tone: 'processing' as const }
        : phase === 'failed'
          ? { label: '识别失败', tone: 'danger' as const }
          : { label: '未分析', tone: 'neutral' as const };

  // 可撤销条数＝提交目标里待确认的（候选条目也算：撤销是候选区的正当出口）
  const decidableCount = decisionTargets.filter(
    (id) => elementsById.get(id)?.process_status === 'pending_confirmation',
  ).length;
  // 可确认条数另算：候选条目已在 confirmTargets 里被滤掉
  const confirmableCount = confirmTargets.filter(
    (id) => elementsById.get(id)?.process_status === 'pending_confirmation',
  ).length;
  // 确认按钮为什么灰着＝提交目标里的待确认条目全是建议剔除候选（提示文案按此分单条/批量两种口径）
  const confirmBlockedByTriage = confirmableCount === 0 && decidableCount > 0;
  const confirmedCount = statusCounts.get('confirmed') ?? 0;
  const pendingCount = statusCounts.get('pending_confirmation') ?? 0;
  // 区5 进度条只统计正常列表里的知识项。候选区的条目用户在列表上看不见，算进分母会让进度
  // 永远追不平——他确认完了看得见的每一条，数字却还差着几条。
  // （重新识别的确认框仍按全集计数：那次操作会把候选区一并替换掉，用户该知道总共丢多少条。）
  const normalLiveElements = useMemo(
    () => liveElements.filter((e) => !isTriageCandidate(e)),
    [liveElements],
  );
  const normalConfirmedCount = normalLiveElements.filter((e) => e.process_status === 'confirmed').length;
  const selectedConclusion = reviewConclusionMeta(selectedElement?.review_conclusion);
  // 归并浮层的可选条目：候选条目一并排除（冷审查裁定 P1，与批量确认同一判据）——用户拍板的口径
  // 是建议剔除候选不参与任何批量入口。此处每条只显示前 26 字、无徽标无理由，勾上组稿就把 AI 判为
  // 不承载需求信息的内容并进了正常知识项。
  const mergeCandidates = liveElements.filter(
    (e) =>
      e.id !== selectedElementId &&
      e.process_status === 'pending_confirmation' &&
      !isTriageCandidate(e),
  );
  // 区1 三态全选：只作用于当前筛选后的可见列表
  // 区5 命令按钮的悬停说明：正常态给「什么情况下用」，两种不可用态先说清为什么不可用
  const commandHint = (whenToUse: string) =>
    batchMode
      ? '批量模式下不可用：请切回单条'
      : selectedIsMergedExisting
        ? '选中的是「已有」的知识项，本页只能查看；要改请到它所属的材料页'
        : whenToUse;
  // ④ 区5 时间线：消息与两张卡按时刻升序合流。卡片是「当前态」项——所依据的事实更新时
  // 时刻随之更新、卡片移到新位置；后端没给时刻的（如存量数据）排在最末视为最新，不伪造时刻。
  const elementCardVisible = Boolean(
    !batchMode && selectedElement
    && (selectedElement.review_conclusion || selectedElement.review_basis || selectedElement.revision_draft),
  );
  const draftCardVisible = Boolean(draft && !draftDismissed);
  // 复核卡的内容指纹：卡片正文只由这三个字段渲染。落位判据用它而不用整行的最后写入时刻——
  // 「✓ 确认 / ✗ 拒绝」也会 UPDATE 该行刷新时刻，而 AI 什么都没做，卡片不该跳「刚刚」
  // 并移到时间线末尾（冷审查裁定 C7）。
  const elementCardFingerprint = selectedElement
    ? [
        selectedElement.review_conclusion ?? '',
        selectedElement.review_basis ?? '',
        selectedElement.revision_draft ?? '',
      ].join('')
    : null;
  const zone5Timeline = useMemo(
    () =>
      buildZone5Timeline(
        messages,
        resolveCardPositions(
          [
            ...(elementCardVisible && selectedElement
              ? [{
                  key: elementCardKey(selectedElement.id),
                  at: selectedElement.updated_at ?? null,
                  fingerprint: elementCardFingerprint,
                }]
              : []),
            ...(draftCardVisible && draft
              ? [{ key: draftCardKey(draft.draft_ref), at: draft.updated_at ?? null }]
              : []),
          ],
          messages.length ? messages[messages.length - 1].at : null,
          cardPositionsRef.current,
        ),
      ),
    [draft, draftCardVisible, elementCardFingerprint, elementCardVisible, messages, selectedElement],
  );
  // 候选区条目不参与勾选：全选＋确认不该把「AI 建议剔除」的条目一并确认掉；
  // 对它们的处置是行内逐条的撤回/撤销
  const selectableListIds = normalListItems
    .filter((i) => !i.superseded && !i.mergedExisting)
    .map((i) => i.id);
  // 横幅计数取未筛选的总数（裁定 C5）：既有项固定只归属术语/角色/外部系统三类，
  // 取筛选后的数字会让用户一点「需求」翼，提示条连同全部「已有」行整体消失，读起来像归并失败了
  const mergedExistingCount = mergedExistingElements.length;
  const checkedInList = selectableListIds.filter((id) => checkedIds.includes(id));
  const allListChecked = selectableListIds.length > 0 && checkedInList.length === selectableListIds.length;

  // ② 识别重跑拦截：每次「识别知识项」都新建识别上下文并重新生成一份全新清单，
  // 前端随即切到新上下文——当前工作区已确认/修订/拆分归并的成果全部从视图移除、不再显示
  // （旧上下文数据留库但工作区不可见）。当工作区已有结果时前置确认，计数取自区5 可见口径。
  const handleAnalyzeClick = useCallback(() => {
    const guard = buildReidentifyGuard(liveElements.length, confirmedCount);
    if (!guard.needsConfirm) {
      void handleAnalyze();
      return;
    }
    void reidentifyModal.confirm({
      title: '重新识别将替换当前工作区的知识项',
      content: (
        <div>
          <p>重新识别会基于材料重新生成一份全新的知识项清单。{guard.message}</p>
          <p>确定重新识别吗？</p>
        </div>
      ),
      okText: '仍要重新识别',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => {
        void handleAnalyze();
      },
    });
  }, [confirmedCount, handleAnalyze, liveElements.length, reidentifyModal]);

  return (
    <div className="analysis-grid" aria-label="知识项确认工作台">
      {reidentifyModalContextHolder}
      {/* 在途修订守卫的二次确认：软拦截——列清哪几条正被 AI 起草，怎么继续由人定 */}
      <Modal
        // destroyOnHidden：关掉即卸载内容，下次弹出不会闪出上一批被拦条目的旧名单
        destroyOnHidden
        footer={null}
        onCancel={() => setInflightGuard(null)}
        open={inflightGuard !== null}
        title="这些知识项正在被 AI 起草修订"
      >
        {inflightGuard ? (
          <>
            <p>
              现在确认，AI 起草中的修订稿不会自动生效——它会挂在已确认的知识项上等人处置，
              要采纳还得先回流。建议等起草完成再裁决。
            </p>
            <ul aria-label="正在被 AI 起草修订的知识项">
              {inflightGuard.guarded.map((g) => (
                <li key={g.element_ref}>
                  {g.content_brief}
                  {g.content_brief.length >= 60 ? '…' : ''}
                </li>
              ))}
            </ul>
            <p className="az5-card__note">
              本次共 {inflightGuard.eligible.length} 条待确认，其中 {inflightGuard.guarded.length} 条正在起草。
            </p>
            {/* 复用区5 卡片脚的按钮行样式（flex + 间距），不为这一个弹层新造样式 */}
            <div className="az5-card__ft">
              <Button onClick={() => setInflightGuard(null)}>等起草完成</Button>
              {inflightGuard.eligible.length > inflightGuard.guarded.length ? (
                <Button
                  onClick={() => {
                    const guardedIds = new Set(inflightGuard.guarded.map((g) => g.element_ref));
                    const rest = inflightGuard.eligible.filter((id) => !guardedIds.has(id));
                    setInflightGuard(null);
                    submitDecision('confirm', rest, inflightGuard.skipped, {
                      skippedInflight: inflightGuard.eligible.length - rest.length,
                    });
                  }}
                >
                  跳过这些，确认其余 {inflightGuard.eligible.length - inflightGuard.guarded.length} 条
                </Button>
              ) : null}
              <Button
                danger
                onClick={() => {
                  setInflightGuard(null);
                  submitDecision('confirm', inflightGuard.eligible, inflightGuard.skipped, {
                    acknowledgeInflightRevision: true,
                  });
                }}
                type="primary"
              >
                仍要全部确认
              </Button>
            </div>
          </>
        ) : null}
      </Modal>
      {/* 区1 知识项列表（结构与视觉照两翼化原型 v4 区1 段） */}
      <section className="analysis-zone analysis-zone--list" aria-label="区1 知识项列表">
        <div className="intake-zone__header">
          <span>区1</span>
          <h3>知识项列表</h3>
        </div>
        <div className="az1-material">
          <strong>{workspace?.material_canvas?.title ?? '当前选定材料'}</strong>
          <StatusPill tone={parseStatusMeta.tone}>{parseStatusMeta.label}</StatusPill>
        </div>
        {liveElements.length ? (
          <div className="az1-filters">
            <div className="az1-filter-row" aria-label="知识项两翼筛选">
              <span className="az1-filter-lab">翼</span>
              {WING_FILTERS.map((f) => (
                <button
                  className={
                    categoryFilter === f.key
                      ? `az1-chip ${f.key === 'all' ? 'az1-chip--on' : `az1-chip--on-${f.key}`}`
                      : 'az1-chip'
                  }
                  key={`wing-${f.key}`}
                  onClick={() => {
                    setCategoryFilter(f.key);
                    setTypeFilter('all');
                  }}
                  type="button"
                >
                  {f.key !== 'all' ? (
                    <span aria-hidden="true" className={`az1-chip__dot az1-chip__dot--${f.key}`} />
                  ) : null}
                  {f.label}
                </button>
              ))}
            </div>
            {categoryFilter !== 'all' ? (
              <div className={`az1-subfilter az1-subfilter--${categoryFilter}`} aria-label="翼内类型子筛选">
                <div className="az1-subfilter__cap">
                  已选「{KNOWLEDGE_CATEGORY_META[categoryFilter].shortLabel}」→ 展开该翼类型
                </div>
                <div className="az1-subfilter__chips">
                  <button
                    className={typeFilter === 'all' ? 'az1-sf-chip az1-sf-chip--on' : 'az1-sf-chip'}
                    onClick={() => setTypeFilter('all')}
                    type="button"
                  >
                    全部类型
                  </button>
                  {elementTypeOptionsForWing(categoryFilter).map((t) => (
                    <button
                      className={typeFilter === t.code ? 'az1-sf-chip az1-sf-chip--on' : 'az1-sf-chip'}
                      key={t.code}
                      onClick={() => setTypeFilter(t.code)}
                      title={t.hint}
                      type="button"
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="az1-filter-row" aria-label="要素状态筛选">
              <span className="az1-filter-lab">状态</span>
              {STATUS_FILTERS.map((f) => (
                <button
                  className={statusFilter === f.key ? 'az1-chip az1-chip--on' : 'az1-chip'}
                  key={f.key}
                  onClick={() => setStatusFilter(f.key)}
                  type="button"
                >
                  {f.label}
                </button>
              ))}
            </div>
            {/* 与区4 同名：同一件事在两处不能一处叫「完备度」一处叫「成分体检」（走查反馈第③组） */}
            <div className="az1-filter-row" aria-label="知识项成分体检筛选">
              <span className="az1-filter-lab">成分体检</span>
              {COMPLETENESS_FILTERS.map((f) => (
                <button
                  className={completenessFilter === f.key ? 'az1-chip az1-chip--on' : 'az1-chip'}
                  key={`facet-${f.key}`}
                  onClick={() => setCompletenessFilter(f.key)}
                  type="button"
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <div className="az1-body" role="listbox" aria-label="知识项" ref={listRef}>
          {normalListItems.length ? (
            <>
              {mergedExistingCount ? (
                <div className="az1-merged-note">
                  这份材料里有 <b>{mergedExistingCount}</b> 条知识项，此前的材料里已经登记过同名的（列表中标「已有」）。
                  系统把它们合到既有那条上，没有新建重复项；这类项在本页只能查看，改动请到它所属的材料页。
                </div>
              ) : null}
              {normalListItems.some((i) => !i.superseded) ? (
                <div className="az1-count" aria-label="全选（当前筛选结果）">
                  当前筛选 <b>{selectableListIds.length}</b> 条 · 已选 <b>{checkedLive.length}</b>
                  {selectableListIds.length ? (
                    <button
                      className="az1-count__link"
                      onClick={() =>
                        setCheckedIds((current) =>
                          allListChecked
                            ? current.filter((id) => !selectableListIds.includes(id))
                            : [...new Set([...current, ...selectableListIds])],
                        )
                      }
                      type="button"
                    >
                      {allListChecked ? '取消全选' : '全选'}
                    </button>
                  ) : null}
                  {checkedLive.length ? (
                    <button className="az1-count__link" onClick={() => setCheckedIds([])} type="button">
                      清除
                    </button>
                  ) : null}
                </div>
              ) : null}
              {groupElementListByWing(normalListItems).map((group) => (
                <div className={`az1-wing-group az1-wing-group--${group.wing}`} key={group.wing}>
                  <div className={`az1-wg-head az1-wg-head--${group.wing}`}>
                    <span aria-hidden="true" className="az1-wg-head__dot" />
                    {KNOWLEDGE_CATEGORY_META[group.wing].label}
                    <span className="az1-wg-head__cnt">{group.items.length} 条</span>
                  </div>
                  <div className="az1-wg-members">
                    {WING_GROUP_MEMBERS[group.wing].lead}
                    <b>{WING_GROUP_MEMBERS[group.wing].types}</b>
                    {WING_GROUP_MEMBERS[group.wing].note}
                  </div>
                  <div className="az1-rows">
                    {group.items.map((item) => (
                      <div
                        aria-selected={item.id === selectedElementId}
                        className={[
                          'az1-row',
                          item.id === selectedElementId ? `az1-row--sel az1-row--sel-${group.wing}` : '',
                          item.superseded ? 'az1-row--closed' : '',
                          item.mergedExisting ? 'az1-row--existing' : '',
                        ]
                          .filter(Boolean)
                          .join(' ')}
                        data-element-id={item.id}
                        key={item.id}
                        onClick={() => handleListSelect(item.id)}
                        onKeyDown={(event) => handleRowKeyDown(event, item.id)}
                        role="option"
                        tabIndex={0}
                      >
                        {!item.superseded && !item.mergedExisting ? (
                          // 勾选是与「选中」并列的另一件事：点框只改勾选集，不把选中目标换成这一条
                          // （整行点击选中后 A1 断言的正是这条边界）。
                          <span className="az1-row__pick" onClick={(event) => event.stopPropagation()}>
                            <Checkbox
                              aria-label={`选择要素 ${item.seq}`}
                              checked={checkedIds.includes(item.id)}
                              onChange={(e) => toggleChecked(item.id, e.target.checked)}
                            />
                          </span>
                        ) : null}
                        <span
                          className={[
                            'az1-tt',
                            `az1-tt--${group.wing}`,
                            item.typeCode === 'business_rule' ? 'az1-tt--rule' : '',
                          ]
                            .filter(Boolean)
                            .join(' ')}
                          title={`${KNOWLEDGE_CATEGORY_META[group.wing].label} · ${item.typeLabel}${
                            item.typeHint ? `\n${item.typeHint}` : ''
                          }`}
                        >
                          <span aria-hidden="true" className="az1-tt__ico">
                            {renderElementTypeIcon(item.typeCode)}
                          </span>
                          <span className="az1-tt__nm">{item.typeLabel}</span>
                        </span>
                        {/* 正文不再是自己的点击目标：命中面已扩到整行（role/aria-selected/键盘随迁到行上） */}
                        <span className="az1-row__txt">{item.content}</span>
                        {item.mergedExisting ? (
                          <span
                            className="az1-existing-tag"
                            title="此前的材料里已经登记过同名的知识项，识别这份材料时自动合到那一条上，没有新建重复项；本页只能查看。"
                          >
                            已有
                          </span>
                        ) : null}
                        {item.anchorCount ? (
                          <span className="az1-anchor" title="该知识项在原文中对应的来源片段数量">
                            来源 {item.anchorCount} 段
                          </span>
                        ) : null}
                        {item.anchorHint ? <span className="az1-anchor az1-anchor--warn">{item.anchorHint}</span> : null}
                        {item.verdictLabel ? <StatusPill tone={item.verdictTone}>{item.verdictLabel}</StatusPill> : null}
                        <StatusPill tone={item.statusTone}>{item.superseded ? '已替代' : item.statusLabel}</StatusPill>
                        <span
                          className="az1-conf"
                          title={item.version > 1 ? `版本 v${item.version}` : undefined}
                        >
                          {item.confidenceText}
                          {/* ⑤ 已确认项不显示版本 chip（纵向对齐）；版本事实保留在悬浮 title 与区4 详情 */}
                          {item.version > 1 && item.statusCode !== 'confirmed' ? ` · v${item.version}` : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          ) : (
            <p className="empty-state">
              {phase === 'idle' ? '点击区2『识别知识项』对已接入材料发起识别。' : workspace?.next_action ?? '暂无符合筛选的知识项。'}
            </p>
          )}
          {/* 建议剔除候选区：不受上方筛选影响，恒在列表底部、默认折叠 */}
          {triageCandidates.length ? (
            <div className="az1-triage">
              <button
                aria-expanded={triageOpen}
                className="az1-triage__head"
                onClick={() => setTriageOpen((open) => !open)}
                type="button"
              >
                <span aria-hidden="true" className="az1-triage__caret">
                  {triageOpen ? '▾' : '▸'}
                </span>
                AI 建议剔除的候选
                <span className="az1-triage__cnt">{triageCandidates.length}</span>
              </button>
              {triageOpen ? (
                <>
                  <p className="az1-triage__note">
                    AI 认为这些内容不承载需求信息（如寒暄、下期范围），已从上面的列表里挪开。
                    AI 也会看走眼——判断错了就把它撤回到正常列表，确是多余的就撤销。
                    两种处置都会让这一条从本组移走，组名上的数字随之减少。
                  </p>
                  <div className="az1-rows az1-triage__rows">
                    {triageCandidates.map((item) => (
                      <div
                        aria-selected={item.id === selectedElementId}
                        className={[
                          'az1-row',
                          'az1-row--triage',
                          item.id === selectedElementId ? 'az1-row--sel az1-row--sel-triage' : '',
                        ]
                          .filter(Boolean)
                          .join(' ')}
                        data-element-id={item.id}
                        key={item.id}
                        onClick={() => handleListSelect(item.id)}
                        onKeyDown={(event) => handleRowKeyDown(event, item.id)}
                        role="option"
                        tabIndex={0}
                      >
                        {/* 区1 是窄列，候选行比正常行多一行理由和一个动作，横排会把正文挤没：
                            改纵向卡片——类型与状态一行，正文与理由各一行，动作单独一行 */}
                        <div className="az1-triage__top">
                          <span
                            className="az1-tt az1-tt--triage"
                            title={`${KNOWLEDGE_CATEGORY_META[elementTypeMeta(item.typeCode).category].label} · ${
                              item.typeLabel
                            }`}
                          >
                            <span aria-hidden="true" className="az1-tt__ico">
                              {renderElementTypeIcon(item.typeCode)}
                            </span>
                            <span className="az1-tt__nm">{item.typeLabel}</span>
                          </span>
                          <StatusPill tone={item.statusTone}>{item.statusLabel}</StatusPill>
                        </div>
                        <span className="az1-row__txt">
                          <span className="az1-triage__what">{item.content}</span>
                          <em className="az1-triage__why">
                            {verdictReasonText(item.verdictCode, item.verdictReason)}
                          </em>
                        </span>
                        {/* 撤回是处置动作而非选中：点它不该顺带把选中目标换到这一条。拦截冒泡挂在
                            按钮自己身上——挂在这层容器上会把按钮左侧那片空白也变成不响应的死区，
                            而整行手型光标正邀请用户去点它 */}
                        <div className="az1-triage__act">
                          <Button
                            disabled={isBusy}
                            onClick={(event) => {
                              event.stopPropagation();
                              handleTriage(item.id, 'restore');
                            }}
                            size="small"
                            title={triageRestoreHint(item.statusCode)}
                          >
                            撤回到正常列表
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>

      {/* 中列：区2 / 区3 / 区4 */}
      <div className="analysis-middle">
        <section className="analysis-zone analysis-zone--toolbar" aria-label="区2 导航与工具栏">
          <div className="intake-zone__header">
            <span>区2</span>
            <h3>导航 + 工具栏</h3>
          </div>
          <div className="analysis-toolbar">
            <Button
              icon={renderActionIcon('launch')}
              // 只有本页发起、本页盯着的那次识别才转圈：antd 的按钮在转圈时会吞掉点击，
              // 若把回放读回来的「识别中」也算上，失败或中断的上下文会让按钮永久失灵（裁定 C1）
              loading={phase === 'recognizing' && recognitionOwned}
              onClick={handleAnalyzeClick}
              title={
                phase === 'recognizing' && !recognitionOwned
                  ? '这份材料上一次识别还没有结果——可能仍在后台进行，也可能执行器已中断；可以重新发起识别'
                  : phase === 'failed'
                    ? '上一次识别没有完成，点击重新发起'
                    : liveElements.length > 0
                      ? '重新识别会生成一份全新清单，替换当前工作区的知识项（会先确认）'
                      : '对已接入材料发起知识项识别'
              }
              type="primary"
            >
              识别知识项
            </Button>
            <Button
              disabled={!canEnterItemFormation || !workspace}
              icon={renderActionIcon('launch')}
              onClick={() => {
                if (workspace) {
                  onEnterItemFormation?.(workspace);
                }
              }}
              title={
                canEnterItemFormation
                  ? '基于「已确认」有效要素集合进入条目形成'
                  : itemFormationAction?.disabled_reason ?? '只有「已确认」要素才能进入条目形成'
              }
              type={canEnterItemFormation ? 'primary' : 'default'}
            >
              进入条目形成
            </Button>
            <Button onClick={onBackToIntake}>返回材料接入</Button>
          </div>
        </section>

        <section className="analysis-zone analysis-zone--canvas" aria-label="区3 材料正文（来源画布）">
          <div className="intake-zone__header">
            <span>区3</span>
            <h3>
              材料正文（来源画布）
              {canvas && canvas.source_version > 1 ? `　·　来源版本 v${canvas.source_version}` : ''}
            </h3>
          </div>
          {canvas ? (
            // 图例照 v4 区3 四项（翼色样×2 / 原文外补充 / 小图标=类型）；状态前导标记为区3 既有能力，说明保留在尾部
            <div className="analysis-canvas-legend" aria-label="两翼与状态图例">
              {KNOWLEDGE_CATEGORY_ORDER.map((wing) => (
                <span className="analysis-canvas-legend__item" key={wing}>
                  <span className={`canvas-legend-swatch canvas-legend-swatch--${wing}`} aria-hidden="true" />
                  {KNOWLEDGE_CATEGORY_META[wing].shortLabel}
                </span>
              ))}
              <span className="analysis-canvas-legend__item">
                <span className="canvas-legend-drift">原文外补充</span>
                <span className="analysis-canvas-legend__note">
                  ＝表达含原文里没有的内容，需核对依据（悬停看说明）
                </span>
              </span>
              <span className="analysis-canvas-legend__item">
                <span className="canvas-legend-overlap-swatch">共用</span>
                <span className="analysis-canvas-legend__note">
                  ＝多个知识项共用这段原文：双下划线为各所有者翼色，悬停显名、点击可选
                </span>
              </span>
              <span className="analysis-canvas-legend__item">
                <span className="canvas-legend-relocated">迁移点</span>
                <span className="analysis-canvas-legend__note">
                  ＝原文改版后按引文重新定位到此处的标注（点线为翼色），建议核对是否仍指同一段
                </span>
              </span>
              <span className="analysis-canvas-legend__note">每条前的小图标＝该条类型（不可选中）</span>
              <span className="analysis-canvas-legend__sep" aria-hidden="true" />
              {STATUS_MARK_ORDER.map((mark) => (
                <span className="analysis-canvas-legend__item" key={mark}>
                  <span className={`canvas-status-icon canvas-status-icon--${mark}`} aria-hidden="true">
                    {renderElementStatusIcon(mark)}
                  </span>
                  {STATUS_MARK_META[mark].label}
                </span>
              ))}
            </div>
          ) : null}
          {canvas ? (
            // 画布台只装滚动容器一个子元素；遮罩/气泡/操作条是台面的兄弟，故不随正文滚动
            <div className="analysis-canvas-stage" ref={canvasStageRef}>
              <div className="analysis-canvas-wrap" ref={canvasRef}>
                <article
                  className="analysis-canvas"
                  onMouseDown={(event) => {
                    pointerDownRef.current = { x: event.clientX, y: event.clientY };
                  }}
                  onMouseUp={handleCanvasMouseUp}
                >
                  <h4>{canvas.title}</h4>
                  {canvas.source_note ? (
                    <p className="analysis-canvas__note">{canvas.source_note}</p>
                  ) : null}
                  {canvasBlocks.map((block) => (
                    <p key={block.blockId}>
                      {block.segments.map((seg, segIndex) => {
                        if (!seg.refs.length) {
                          return (
                            <span data-seg-start={seg.start} key={seg.key}>
                              {seg.text}
                            </span>
                          );
                        }
                        // 每个高亮「连段」的首段前置类型图标（图标=类型、颜色=两翼），与区1、v4 原型一致；
                        // 后续同一要素的连段不再重复图标，避免正文被图标切碎。
                        const primaryRef = seg.refs[0];
                        const prevSeg = block.segments[segIndex - 1];
                        const runStart = !prevSeg || prevSeg.refs[0] !== primaryRef;
                        // ① 重叠徽标独立于类型 run：以「重叠 ref 组」的起始为准（重叠可发生在同一要素连段中段）
                        const refsKey = (refs: string[]) => refs.slice().sort().join('|');
                        const overlapRunStart =
                          seg.refs.length > 1 && (!prevSeg || refsKey(prevSeg.refs) !== refsKey(seg.refs));
                        const primaryEl = primaryRef ? elementsById.get(primaryRef) : undefined;
                        const primaryMeta = primaryEl ? elementTypeMeta(primaryEl.element_type) : null;
                        const primaryWing = primaryMeta?.category ?? null;
                        const segDrifted = seg.refs.some((ref) => driftedElementIds.has(ref));
                        // 状态前导标记（与类型正交）：勾=已确认、叉=已撤销、修订笔=有修订稿；纯待确认无标记。
                        const statusMark = primaryEl
                          ? elementStatusMarkKey(primaryEl.process_status, primaryEl.revision_draft)
                          : null;
                        // ① 方案A：重叠段以「两 owner 翼色」的双下划线呈现（行内 style 承载真实翼色，下=refs[0] 上=refs[1]，
                        // 见 .canvas-highlight--overlap 的 background-position：第 1 层贴底、第 2 层上抬 .1875rem）；
                        // 计数徽标（重叠组首段）+ 悬停 title 点名共用者。
                        const wingLineColor = (wing: string | null) =>
                          wing === 'requirement'
                            ? 'var(--color-primary)'
                            : wing === 'business'
                              ? 'var(--color-success)'
                              : 'var(--color-border-strong)';
                        const refWing = (ref: string) => {
                          const el = elementsById.get(ref);
                          return el ? elementTypeMeta(el.element_type).category : null;
                        };
                        // 双下划线用 background 渐变承载（跟随正文文本换行，不受行内徽标盒高影响；
                        // 下线=refs[0] 翼、上线=refs[1] 翼，位置/尺寸在 .canvas-highlight--overlap 里）。
                        const overlapStyle =
                          seg.refs.length > 1
                            ? {
                                backgroundImage:
                                  `linear-gradient(${wingLineColor(refWing(seg.refs[0]))}, ${wingLineColor(refWing(seg.refs[0]))}),` +
                                  `linear-gradient(${wingLineColor(refWing(seg.refs[1]))}, ${wingLineColor(refWing(seg.refs[1]))})`,
                              }
                            : undefined;
                        // 选中态（去方框）：整段加深底色；左侧竖条只标选中「运行起点」，避免重叠拆段时到处是竖条。
                        const selectedId = selectedElementId ?? '';
                        const isSelected = seg.refs.includes(selectedId);
                        const selectedStart = isSelected && (!prevSeg || !prevSeg.refs.includes(selectedId));
                        const overlapTitle =
                          seg.refs.length > 1
                            ? '共用：' +
                              seg.refs
                                .map((ref) => {
                                  const el = elementsById.get(ref);
                                  if (!el) return '';
                                  const meta = elementTypeMeta(el.element_type);
                                  const snip = el.content.length > 14 ? `${el.content.slice(0, 14)}…` : el.content;
                                  return `${meta.label}「${snip}」`;
                                })
                                .filter(Boolean)
                                .join(' · ')
                            : undefined;
                        return (
                          <span
                            className={[
                              'canvas-highlight',
                              // 两级编码（v4 区3）：mark 底色只承载翼；类型交给前缀图标+悬停文字
                              `canvas-highlight--wing-${primaryWing ?? 'business'}`,
                              seg.primaryStatus ? `canvas-highlight--status-${seg.primaryStatus}` : '',
                              isSelected ? 'canvas-highlight--selected' : '',
                              selectedStart ? 'canvas-highlight--selected-start' : '',
                              seg.refs.length > 1 ? 'canvas-highlight--overlap' : '',
                              seg.relocated ? 'canvas-highlight--relocated' : '',
                              segDrifted ? 'canvas-highlight--drift' : '',
                            ].join(' ')}
                            data-first-ref={seg.refs[0]}
                            data-seg-start={seg.start}
                            key={seg.key}
                            style={overlapStyle}
                            title={
                              overlapTitle
                                ? overlapTitle
                                : segDrifted
                                  ? '已偏离原文：要素当前表达包含原文与补入块中没有的事实（详见区4）'
                                  : primaryMeta && primaryWing
                                    ? `${KNOWLEDGE_CATEGORY_META[primaryWing].label} · ${primaryMeta.label}`
                                    : undefined
                            }
                            onClick={(event) => {
                              // K1：位移判据只在鼠标入口——按下→点击位移超阈值＝段内拖选，放行给
                              // mouseUp 处理选区，别误弹气泡；未超阈值（点选/手抖）照常选中/开气泡。
                              const down = pointerDownRef.current;
                              if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) > SEGMENT_DRAG_THRESHOLD_PX) {
                                return;
                              }
                              handleSegmentClick(seg.refs, { x: event.clientX, y: event.clientY });
                            }}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                // 键盘无光标坐标：用段自身矩形左下角作气泡落点（不设选区门）。
                                const rect = event.currentTarget.getBoundingClientRect();
                                handleSegmentClick(seg.refs, { x: rect.left, y: rect.bottom });
                              }
                            }}
                            role="button"
                            tabIndex={0}
                          >
                            {runStart && statusMark ? (
                              <span className={`canvas-status-icon canvas-status-icon--${statusMark}`} aria-hidden="true">
                                {renderElementStatusIcon(statusMark)}
                              </span>
                            ) : null}
                            {runStart && primaryEl && primaryMeta && primaryWing ? (
                              <span
                                className={[
                                  'canvas-mark-icon',
                                  `canvas-mark-icon--${primaryWing}`,
                                  `canvas-mark-icon--shade-${primaryMeta.shade}`,
                                  primaryEl.element_type === 'business_rule' ? 'canvas-mark-icon--rule' : '',
                                ]
                                  .filter(Boolean)
                                  .join(' ')}
                                aria-hidden="true"
                              >
                                {renderElementTypeIcon(primaryEl.element_type)}
                              </span>
                            ) : null}
                            {/* ① 多引用片段标识：重叠组首段挂计数徽标，与单引用可区分（点击看全部引用者） */}
                            {overlapRunStart ? (
                              <span className="canvas-overlap-tag" title={`这段原文被 ${seg.refs.length} 个知识项共用（点击查看）`}>
                                共{seg.refs.length}项
                              </span>
                            ) : null}
                            {seg.text}
                          </span>
                        );
                      })}
                    </p>
                  ))}
                  {(canvas.supplements ?? []).length ? (
                    <div className="analysis-supplements" aria-label="补入来源块">
                      {(canvas.supplements ?? []).map((s) => (
                        <div className="analysis-supplement" key={s.supplement_ref}>
                          <StatusPill tone="warning">补</StatusPill>
                          <div>
                            <p>{s.content}</p>
                            <p className="analysis-supplement__meta">
                              依据：{s.basis}　·　补入人：{s.operator_ref}
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </article>
              </div>
              {/* 遮罩只盖本页正在跑的那次识别：别处发起或已中断的识别不该挡住用户读材料原文。
                  S1：条件复用 recognitionLocked 常量（＝phase==='recognizing' && recognitionOwned），
                  与「识别中禁交互」守卫同一来源，不再手抄第二份。 */}
              {recognitionLocked ? (
                <div className="analysis-canvas-mask">识别进行中…</div>
              ) : null}
              {overlap ? (
                <div
                  className="analysis-overlap-popover"
                  ref={overlapRef}
                  style={overlapPos ?? { left: overlap.x, top: overlap.y }}
                >
                  <strong>该片段被多个要素引用</strong>
                  {overlap.refs.map((ref) => {
                    const element = elementsById.get(ref);
                    if (!element) return null;
                    const meta = elementTypeMeta(element.element_type);
                    return (
                      <button key={ref} onClick={() => selectElement(ref)} type="button">
                        <span className={`element-type-chip element-type-chip--${meta.colorKey}`} title={meta.hint}>
                          {meta.label}
                        </span>
                        {element.content.slice(0, 24)}
                      </button>
                    );
                  })}
                  <button className="analysis-overlap-popover__close" onClick={() => setOverlap(null)} type="button">
                    关闭
                  </button>
                </div>
              ) : null}
              {selection ? (
                <div className="analysis-selection-bar" ref={selectionBarRef}>
                  已选原文（{selection.start}–{selection.end}）：{selection.text.slice(0, 36)}
                  {selection.text.length > 36 ? '…' : ''}
                  <Button
                    disabled={composerLocked || !workspace}
                    onClick={handleAddMissingFromSelection}
                    size="small"
                    title="以选区文本新增一条知识项（走「新增遗漏」，选区范围成为来源锚点）"
                  >
                    ＋ 新增为知识项
                  </Button>
                  <Button
                    disabled={composerLocked || recognitionLocked || !selectionAffordanceAccepted}
                    onClick={handleAppendSelectionAffordance}
                    size="small"
                    title={
                      selectionAffordanceAccepted
                        ? '把这段选区写进区5 输入框，让命令说清作用在哪一段原文；再点一次只更新这段，不会重复堆叠'
                        : '当前选区描述只能配合「改范围」或自己写的描述使用——其它命令会把这段说明当成命令的内容读进去'
                    }
                  >
                    当前选区
                  </Button>
                  <Button
                    disabled={isBusy || !workspace}
                    onClick={() => handleSubmitReview('对选中原文范围扫描漏识别的知识项', true)}
                    size="small"
                    type="primary"
                  >
                    扫原文补漏
                  </Button>
                  <button disabled={recognitionLocked} onClick={() => setSelection(null)} type="button">
                    清除选区
                  </button>
                </div>
              ) : null}
            </div>
          ) : (
            <p className="empty-state">正在加载已接入材料正文…</p>
          )}
        </section>

        <section className="analysis-zone analysis-zone--detail" aria-label="区4 详情与证据">
          <div className="intake-zone__header">
            <span>区4</span>
            <h3>详情 + 证据（选中要素）</h3>
          </div>
          {selectedElement ? (
            <div className="analysis-detail-grid">
              <div className="analysis-detail-card">
                <strong>知识项</strong>
                <ElementTypeChip typeCode={selectedElement.element_type} />
                <p>{selectedElement.content}</p>
                {selectedIsMergedExisting ? (
                  <p className="analysis-detail-existing">
                    已有的知识项：此前的材料里登记过同名的这一条，识别这份材料时合到了它上面。这里显示的状态、版本来自那一条，
                    本页只能查看，不能改。
                  </p>
                ) : null}
                <dl>
                  <dt>确认状态</dt>
                  <dd>
                    {selectedElement.superseded
                      ? '已替代（保留留痕）'
                      : processStatusMeta(selectedElement.process_status).label}
                  </dd>
                  <dt>版本</dt>
                  <dd>v{selectedElement.version ?? 1}</dd>
                  {/* 区1 行上的裁定徽标与这里的裁定行有意分两套口径（冷审查裁定 C5 划的界）：
                      行上的徽标是给列表扫读用的状态标记，只在这一条确实待在候选区时才出「建议
                      剔除」；区4 这一行是证据陈列，模型的原判定与理由无论该条如今在哪个列表、
                      是否被人工撤回，都原样保留在此，不随人工处置改写或隐藏。 */}
                  <dt>模型裁定（证据）</dt>
                  <dd>{modelVerdictMeta(selectedElement.model_verdict)?.label ?? '—'}</dd>
                  {selectedElement.model_verdict && selectedElement.model_verdict !== 'processable' ? (
                    <>
                      <dt>
                        {selectedElement.model_verdict === 'suspected_noise'
                          ? 'AI 建议剔除的理由'
                          : 'AI 这样判的理由'}
                      </dt>
                      <dd>
                        {verdictReasonText(selectedElement.model_verdict, selectedElement.verdict_reason) ?? '—'}
                      </dd>
                    </>
                  ) : null}
                  <dt>置信度</dt>
                  <dd>
                    {selectedElement.confidence !== null && selectedElement.confidence !== undefined
                      ? `${Math.round(selectedElement.confidence * 100)}%`
                      : '—'}
                  </dd>
                </dl>
                {selectedElement.correction_note ? (
                  <p className="analysis-detail-suggestion">依据/留痕：{selectedElement.correction_note}</p>
                ) : null}
                {(selectedElement.origin_refs ?? []).length ? (
                  <p className="analysis-detail-suggestion">
                    版本关系：由 {(selectedElement.origin_refs ?? []).length} 个前身要素拆分/合并而来
                  </p>
                ) : null}
                {!selectedElement.superseded &&
                !selectedIsMergedExisting &&
                (selectedElement.process_status === 'confirmed' || selectedElement.process_status === 'revoked') ? (
                  <Button
                    disabled={isBusy}
                    onClick={handleReopen}
                    size="small"
                    title={
                      selectedElement.process_status === 'revoked'
                        ? '这条之前被撤销了；恢复后回到待确认，可以重新审（会升一个新版本）'
                        : '后续环节发现这条已确认的知识项有问题时，退回待确认状态重新审（会升一个新版本）'
                    }
                  >
                    {selectedElement.process_status === 'revoked' ? '恢复为待确认' : '退回重新确认'}
                  </Button>
                ) : null}
                {selectedIsTriageCandidate ? (
                  <Button
                    disabled={isBusy}
                    onClick={() => handleTriage(selectedElement.id, 'restore')}
                    size="small"
                    title={triageRestoreHint(selectedElement.process_status)}
                  >
                    撤回到正常列表
                  </Button>
                ) : null}
                {selectedIsRestoredFromTriage ? (
                  <Button
                    disabled={isBusy}
                    onClick={() => handleTriage(selectedElement.id, 'return')}
                    size="small"
                    title="撤回错了：把这一条移回「AI 建议剔除的候选」分组"
                  >
                    移回建议剔除候选
                  </Button>
                ) : null}
              </div>
              <div className="analysis-detail-card">
                <strong>来源定位</strong>
                <AnchorDetail element={selectedElement} anchors={anchors} rawText={canvas?.raw_text ?? ''} />
                {(selectedElement.source_drift_tokens ?? []).length ? (
                  <div className="analysis-drift" aria-label="偏离原文提示">
                    <StatusPill tone="warning">已偏离原文</StatusPill>
                    <p className="analysis-detail-suggestion">
                      当前表达包含原文与补入块中没有的事实：
                      {(selectedElement.source_drift_tokens ?? []).join('、')}
                      （引文对照见上）。原文如实保留证据，不随修订改写。
                    </p>
                    {!selectedElement.superseded ? (
                      <div className="analysis-drift__actions">
                        <Button onClick={() => quickFill(QUICK_COMMAND_PREFILLS.erratum(selection?.text))} size="small">
                          材料记错了 · 勘误
                        </Button>
                        <Button onClick={() => quickFill(QUICK_COMMAND_PREFILLS.supplement())} size="small">
                          业务决策变了 · 补入依据
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {selectedElement.review_conclusion || selectedElement.review_basis ? (
                  <>
                    <strong>AI 复核</strong>
                    <p>
                      {reviewConclusionMeta(selectedElement.review_conclusion)?.label ?? '（失败/无结论）'}
                      ：{selectedElement.review_basis ?? ''}
                    </p>
                  </>
                ) : null}
                {selectedFacetReview ? (
                  <>
                    {/* 体检标准的版本号移入悬停：界面文字不出现「判据」这类内部词（走查反馈第③组） */}
                    <strong title={`体检标准版本 v${selectedFacetReview.rubricVersion}`}>
                      成分体检（仅提示，不影响确认）
                    </strong>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '4px 0' }}>
                      {selectedFacetReview.stale ? (
                        <StatusPill tone="warning">修订后未复核（要素已修订）</StatusPill>
                      ) : null}
                      {selectedFacetReview.correctness ? (
                        <StatusPill tone={selectedFacetReview.correctness.tone}>
                          {selectedFacetReview.correctness.label}
                        </StatusPill>
                      ) : null}
                      {selectedFacetReview.completeness ? (
                        <StatusPill tone={selectedFacetReview.completeness.tone}>
                          {selectedFacetReview.completeness.label}
                        </StatusPill>
                      ) : null}
                      {/* 悬停解释挂在外层 span 上：StatusPill 是公共控件，不为本页需求改它的入参 */}
                      {selectedFacetReview.badges.map((b) => (
                        <span key={b.key} title={`${b.statusLabel}：${b.statusHint}`}>
                          <StatusPill tone={b.tone}>
                            {b.label} {b.statusMark}
                          </StatusPill>
                        </span>
                      ))}
                    </div>
                    {selectedFacetReview.stale ? (
                      <p className="analysis-detail-suggestion">
                        以下结果针对旧版本内容，可重新发起 AI 复核刷新体检结果。
                      </p>
                    ) : null}
                    {selectedFacetReview.gaps.map((b) => (
                      <p className="analysis-detail-suggestion" key={`gap-${b.key}`}>
                        {b.label}（{b.statusLabel}）：{b.note ?? ''}
                        {b.revisionHint ? ` ${b.revisionHint}` : ''}
                      </p>
                    ))}
                    {selectedFacetReview.gaps.length && !selectedElement.superseded ? (
                      <Button
                        onClick={() =>
                          quickFill(
                            `/改表达 修订为：${buildRevisionPrefill(selectedElement.content, selectedFacetReview.gaps)}`,
                          )
                        }
                        size="small"
                        title="把上面标出的缺失项整理成一句修订要求，填进下方输入框；填好后你可以再改，发送才生效"
                      >
                        按缺失项生成修订指令（填入输入框）
                      </Button>
                    ) : null}
                  </>
                ) : null}
                {selectedElement.revision_draft ? (
                  <>
                    <strong>当前修订稿</strong>
                    <p>{selectedElement.revision_draft}</p>
                    {/* 孤儿稿：已确认的知识项上还挂着没采纳的稿子（多半是起草期间被抢先确认）。
                        不静默丢弃也不自动采纳——给一条回流的路，采纳永远是人工动作。 */}
                    {selectedElement.process_status === 'confirmed' ? (
                      <>
                        <p className="az5-card__note">
                          这条已确认，修订稿没有生效。要采纳它，先回流退回待确认（旧版本保留在变更历史里）。
                        </p>
                        <Button
                          disabled={isBusy || selectedIsMergedExisting}
                          onClick={handleReflowToAdopt}
                          size="small"
                          type="primary"
                        >
                          回流以采纳
                        </Button>
                      </>
                    ) : null}
                  </>
                ) : null}
              </div>
              <div className="analysis-detail-card">
                <strong>变更历史</strong>
                <Button onClick={handleLoadHistory} size="small">
                  查看历史
                </Button>
                {history && history.element_ref === selectedElement.id ? (
                  <div className="analysis-history-list">
                    {(history.records ?? []).length ? (
                      (history.records ?? []).map((rec, i) => (
                        <p className="analysis-detail-suggestion" key={`${rec.at}-${i}`}>
                          v{rec.version} · {rec.action}
                          {rec.from_status && rec.to_status && rec.from_status !== rec.to_status
                            ? `（${processStatusMeta(rec.from_status).label} → ${processStatusMeta(rec.to_status).label}）`
                            : ''}
                          　{rec.operator_ref}　<RelativeTime iso={rec.at} />
                          {rec.note ? `：${rec.note}` : ''}
                        </p>
                      ))
                    ) : (
                      <p className="empty-state">暂无历史记录。</p>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          ) : (
            <p className="empty-state">选中区1或区3中的知识项后展示详情、来源定位、AI 复核与历史。</p>
          )}
        </section>
      </div>

      {/* 区5 确认操作与 AI 协同（对话式；结构与样式 1:1 复刻 区5重设计方案.html 原型 .rail） */}
      <section className="analysis-zone analysis-zone--operations az5" aria-label="区5 确认操作与 AI 协同区">
        <div className="az5-top">
          <div className="az5-row1">
            <span className="az5-zone">区5</span>
            <h3 className="az5-title">确认操作与 AI 协同</h3>
            <span className="az5-prog">已确认 {normalConfirmedCount}/{normalLiveElements.length || 0}</span>
          </div>
          <div className="az5-target" aria-label="当前目标">
            {batchMode ? (
              <>
                <span className="az5-target__cap">当前目标 · 批量</span>
                <span className="az5-target__body">已勾选 {checkedLive.length} 条 · 可裁决 {decidableCount} 条</span>
                <span className="az5-target__st">
                  <button className="az5-link" onClick={() => setCheckedIds([])} type="button">切回单条</button>
                </span>
              </>
            ) : selectedElement ? (
              <>
                <span className="az5-target__cap">当前目标</span>
                <ElementTypeChip typeCode={selectedElement.element_type} />
                <span className="az5-target__body">{selectedElement.content}</span>
                <span className="az5-target__st">
                  {processStatusMeta(selectedElement.process_status).label} · v{selectedElement.version ?? 1}
                </span>
              </>
            ) : (
              <span className="az5-target__cap">未选择目标——点击区1 或区3 选中要素</span>
            )}
          </div>
        </div>

        <div className="az5-thread" ref={threadRef} aria-label="对话时间线">
          {messages.length === 0 ? (
            <p className="az5-hint">对当前要素说点什么（复核、修订…），或点下方快捷命令预填 /命令词 后续写；✓ 确认 / ✗ 拒绝一键即发。</p>
          ) : null}
          {/* ④ 时间线按时刻升序：消息与 AI 卡片同流，卡片不再固定钉在消息之后
              （旧结构下新发的消息渲染在卡片之上，看着像插进了历史中间）。 */}
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
                ) : (
                  <div className={`az5-sys az5-sys--${m.kind}`} key={m.id}>
                    {m.kind === 'sys-ok' ? '✓ ' : m.kind === 'sys-warn' ? '⚠ ' : m.kind === 'sys-pending' ? '⏳ ' : ''}
                    {m.text}
                    <RelativeTime className="az5-time" iso={m.at} />
                    {m.traceLines ? <AiTraceDetail lines={m.traceLines} /> : null}
                  </div>
                )
              );
            }
            if (entry.key.startsWith(ZONE5_CARD_KEY_PREFIX.element) && selectedElement) {
              return (

                <div className="az5-msg az5-msg--ai" aria-label="AI 结论与修订稿" key={entry.key}>
                  <span className="az5-ava az5-ava--ai">AI</span>
                  <div className="az5-msg__body">
                    <span className="az5-who">
                      AI {selectedElement.revision_draft ? '修订稿' : '复核'}
                      {entry.at ? <RelativeTime className="az5-time" iso={entry.at} /> : null}
                    </span>
                    <div className="az5-card">
                      <div className="az5-card__hd">
                        <b>{selectedElement.revision_draft ? '修订稿（未采纳，不生效）' : '复核结论'}</b>
                        {selectedConclusion ? (
                          <StatusPill tone={selectedConclusion.tone}>{selectedConclusion.label}</StatusPill>
                        ) : null}
                      </div>
                      <div className="az5-card__bd">
                        {selectedElement.review_basis ? <p>{selectedElement.review_basis}</p> : null}
                        {selectedElement.revision_draft ? (
                          <>
                            <p className="az5-diff az5-diff--before">{selectedElement.content}</p>
                            <p className="az5-diff az5-diff--after">{selectedElement.revision_draft}</p>
                          </>
                        ) : null}
                      </div>
                      <div className="az5-card__ft">
                        {selectedElement.revision_draft ? (
                          <>
                            <button
                              className="az5-btn az5-btn--primary"
                              disabled={isBusy || selectedIsMergedExisting}
                              onClick={() => handleFinalizeRevision('adopt')}
                              type="button"
                            >
                              采纳修订稿
                            </button>
                            <button
                              className="az5-btn"
                              disabled={isBusy || selectedIsMergedExisting}
                              onClick={() => handleFinalizeRevision('abandon')}
                              type="button"
                            >
                              清除草稿
                            </button>
                            <span className="az5-card__note">
                              {selectedIsMergedExisting
                                ? '这条是「已有」的知识项，本页只能查看；要处置这份草稿请到它所属的材料页。'
                                : '不满意？接着说一轮；不认可这条要素，点「✗ 拒绝」。'}
                            </span>
                          </>
                        ) : (
                          <span className="az5-card__note">复核只是建议，不迁移状态；可继续对话或直接裁决。</span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            }
            if (entry.key.startsWith(ZONE5_CARD_KEY_PREFIX.draft) && draft) {
              return (

                <div className="az5-msg az5-msg--ai" aria-label="变更草案" key={entry.key}>
                  <span className="az5-ava az5-ava--ai">AI</span>
                  <div className="az5-msg__body">
                    <span className="az5-who">
                      AI 变更草案
                      {entry.at ? <RelativeTime className="az5-time" iso={entry.at} /> : null}
                    </span>
                    <div className="az5-card">
                      <div className="az5-card__hd">
                        <b>变更草案 · {draft.operation_type}</b>
                        <StatusPill tone={draft.create_gate === 'creatable' ? 'processing' : 'warning'}>
                          {CREATE_GATE_TEXT[draft.create_gate ?? 'creatable'] ?? draft.create_gate}
                        </StatusPill>
                      </div>
                      <div className="az5-card__bd">
                        {(draft.before_items ?? []).map((item) => (
                          <p className="az5-diff az5-diff--before" key={item.id}>{item.content}</p>
                        ))}
                        {(draft.after_items ?? []).map((item) => (
                          <p className="az5-diff az5-diff--after" key={item.id}>
                            <ElementTypeChip typeCode={item.element_type} />
                            {item.content}
                          </p>
                        ))}
                        {(draft.impact_summary ?? []).map((line) => (
                          <p key={line}>{line}</p>
                        ))}
                        {draft.next_action ? <p>{draft.next_action}</p> : null}
                      </div>
                      <div className="az5-card__ft">
                        <button
                          className="az5-btn az5-btn--primary"
                          disabled={isBusy || !(operationsByKey.get('confirm_change')?.enabled ?? false)}
                          onClick={handleConfirmDraft}
                          title={operationsByKey.get('confirm_change')?.reason ?? undefined}
                          type="button"
                        >
                          确认创建
                        </button>
                        <button className="az5-btn" disabled={isBusy} onClick={() => setDraftDismissed(true)} type="button">
                          取消
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            }
            return null;
          })}
          {trace ? <AiTraceRail now={traceNow} trace={trace} /> : null}
          {progressText ? <p className="az5-sys">{progressText}</p> : null}
          {errorText ? (
            <p className="az5-sys az5-sys--sys-warn" role="alert">
              ⚠ {errorText}
              {parseContextRef ? (
                <button className="az5-link" onClick={() => void refreshWorkspace(parseContextRef)} type="button">
                  刷新工作区
                </button>
              ) : null}
            </p>
          ) : null}
          {workspace?.next_action && !errorText ? (
            <p className="az5-sys">
              {workspace.next_action}
              {/* 识别失败停靠的出口：后端一直在失败态给出 retry 动作，此前前端从不消费（裁定 C1） */}
              {canRetryRecognition ? (
                <button className="az5-link" onClick={handleAnalyzeClick} type="button">
                  重新识别
                </button>
              ) : null}
            </p>
          ) : null}
          {phase === 'recognizing' && !recognitionOwned ? (
            <p className="az5-sys">
              这次识别不是在本页发起的，本页看不到它的进度。等一会儿点「刷新工作区」看结果；
              如果一直没有结果（比如执行器中断了），可以在区2 重新发起识别。
              {parseContextRef ? (
                <button className="az5-link" onClick={() => void refreshWorkspace(parseContextRef)} type="button">
                  刷新工作区
                </button>
              ) : null}
            </p>
          ) : null}
        </div>

        <div className="az5-composer">
          <div className="az5-pills" role="group" aria-label="快捷命令">
            {typePickerOpen ? (
              <div className="az5-pop" role="menu" aria-label="选择新类型">
                <span className="az5-pop__cap">改为哪个类型？</span>
                {ELEMENT_TYPE_OPTIONS.map((o) => (
                  <button
                    className="az5-qp"
                    key={o.value}
                    onClick={() => quickFill(QUICK_COMMAND_PREFILLS.adjustType(o.label))}
                    type="button"
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            ) : null}
            {mergeDialogOpen ? (
              <div className="az5-pop" role="dialog" aria-label="选择参与合并的要素">
                <span className="az5-pop__cap">与当前目标合并——复选参与要素：</span>
                <div className="az5-pop__list">
                  {mergeCandidates.map((e) => (
                    <label key={e.id}>
                      <Checkbox
                        checked={mergeChecked.includes(e.id)}
                        onChange={(ev) =>
                          setMergeChecked((current) =>
                            ev.target.checked ? [...current, e.id] : current.filter((id) => id !== e.id),
                          )
                        }
                      />
                      <span>{e.content.slice(0, 26)}{e.content.length > 26 ? '…' : ''}</span>
                    </label>
                  ))}
                  {!mergeCandidates.length ? <span className="az5-card__note">没有其它待确认要素可合并。</span> : null}
                </div>
                <button
                  className="az5-btn az5-btn--primary"
                  disabled={!mergeChecked.length}
                  onClick={() => {
                    const names = mergeChecked.map((id) => (elementsById.get(id)?.content ?? '').slice(0, 18));
                    quickFill(QUICK_COMMAND_PREFILLS.merge(names));
                    setMergeChecked([]);
                  }}
                  type="button"
                >
                  组稿命令文本
                </button>
              </div>
            ) : null}
            {/* 裁决：结构化直发（不改知识项文字，也不改原文） */}
            <div className="az5-pillgroup az5-pillgroup--verdict">
              <button
                className="az5-qp az5-qp--ok"
                // 按提交目标判定，不按当前选中项判定（冷审查裁定 C1/C3）：勾选一批正常条目、
                // 顺手点开一条候选看详情，这一批照样能确认；反过来勾选集合里混进的候选条目
                // 已在 confirmTargets 里滤掉，批量确认碰不到它们。
                disabled={isBusy || !confirmableCount}
                onClick={() => handleDecide('confirm')}
                // 只有单条口径需要这句提示：批量口径下候选条目在 checkedLive 就被滤掉了，
                // 一批目标里不可能混着候选，也就不存在「整批因候选而确认不了」这回事
                title={
                  confirmBlockedByTriage
                    ? '这一条在「AI 建议剔除的候选」里，要先撤回到正常列表才能确认'
                    : '结构化直发：待确认 → 已确认；裁决后自动前进'
                }
                type="button"
              >
                ✓ 确认{batchMode ? `（${confirmableCount} 条）` : ''}
              </button>
              <button
                className="az5-qp az5-qp--no"
                disabled={isBusy || !decidableCount}
                onClick={() => handleDecide('reject')}
                title="结构化直发：待确认 → 已撤销（保留识别事实）"
                type="button"
              >
                ✗ 拒绝{batchMode ? `（${decidableCount} 条）` : ''}
              </button>
            </div>
            {/* ③ 改识别结果侧：动的是 AI 认出的知识项，不动材料原文 */}
            <div className="az5-pillgroup">
              <div className="az5-pillgroup__head">
                <span className="az5-pillgroup__cap">调整识别结果</span>
                <span className="az5-pillgroup__sub">改 AI 认出的知识项，不动材料原文</span>
              </div>
              {selectedIsMergedExisting ? (
                <p className="az5-pillgroup__lock">
                  选中的是「已有」的知识项（此前材料登记过同名的）。本页只能查看，要改它请到它所属的材料页。
                  「新增遗漏」和更正原文说的不是这一条，先取消选中再用：
                  <button className="az5-link" onClick={() => setSelectedElementId(null)} type="button">
                    取消选中
                  </button>
                </p>
              ) : null}
              <button
                className="az5-qp"
                disabled={composerLocked || !selectedElement}
                onClick={() => setTypePickerOpen((v) => !v)}
                title={commandHint('这条归错类了的时候用：比如明明是接口需求，却被认成了目标')}
                type="button"
              >
                改类型
              </button>
              <button
                className="az5-qp"
                disabled={composerLocked || !selectedElement}
                onClick={() => quickFill(QUICK_COMMAND_PREFILLS.reviseExpression())}
                title={commandHint('这条话没说清、有歧义或不完整的时候用：重写它的文字')}
                type="button"
              >
                改表达
              </button>
              <button
                className="az5-qp"
                disabled={composerLocked || !selectedElement}
                onClick={() => quickFill(QUICK_COMMAND_PREFILLS.adjustAnchor(selection))}
                title={commandHint(
                  selection
                    ? `这条在材料里标错位置的时候用：将改为区3 当前选中的那段（${selection.start}–${selection.end}）`
                    : '这条在材料里标错位置的时候用：先到区3 拖选正确的那段原文',
                )}
                type="button"
              >
                改范围{selection ? ' · 用当前选区' : ''}
              </button>
              <button
                className="az5-qp"
                disabled={composerLocked || !selectedElement}
                onClick={() => quickFill(QUICK_COMMAND_PREFILLS.split())}
                title={commandHint('一条里塞了好几件事的时候用：拆成各自独立的几条')}
                type="button"
              >
                拆分
              </button>
              <button
                className="az5-qp"
                disabled={composerLocked || !selectedElement || !mergeCandidates.length}
                onClick={() => setMergeDialogOpen((v) => !v)}
                title={
                  batchMode
                    ? '要合并请切回单条，参与合并的其它条在弹出的复选框里挑'
                    : commandHint('几条说的其实是同一件事的时候用：合成一条')
                }
                type="button"
              >
                合并
              </button>
              <button
                className="az5-qp"
                disabled={composerLocked}
                onClick={() => quickFill(QUICK_COMMAND_PREFILLS.addMissing(selection?.text))}
                title={
                  batchMode
                    ? '批量模式下不可用：请切回单条'
                    : selectedIsMergedExisting
                      ? '当前选中的是「已有」的知识项，输入框已锁；先取消选中再新增遗漏'
                      : '材料里写了、AI 没认出来的时候用：手工补登一条'
                }
                type="button"
              >
                新增遗漏
              </button>
            </div>
            {/* ③ 改原文侧：材料原文如实保留，勘误/补入是仅有的两条更正通道 */}
            <div className="az5-pillgroup az5-pillgroup--source">
              <div className="az5-pillgroup__head">
                <span className="az5-pillgroup__cap">更正材料原文</span>
                <span className="az5-pillgroup__sub">材料本身写错了、写漏了才用</span>
              </div>
              <button
                className="az5-qp"
                disabled={composerLocked}
                onClick={() => quickFill(QUICK_COMMAND_PREFILLS.erratum(selection?.text))}
                title={
                  batchMode
                    ? '批量模式下不可用：请切回单条'
                    : selectedIsMergedExisting
                      ? '当前选中的是「已有」的知识项，输入框已锁；先取消选中再勘误'
                      : '材料原文把字句写错了的时候用：改对它（改前的原稿会留档）'
                }
                type="button"
              >
                勘误
              </button>
              <button
                className="az5-qp"
                disabled={composerLocked}
                onClick={() => quickFill(QUICK_COMMAND_PREFILLS.supplement())}
                title={
                  batchMode
                    ? '批量模式下不可用：请切回单条'
                    : selectedIsMergedExisting
                      ? '当前选中的是「已有」的知识项，输入框已锁；先取消选中再补入'
                      : '材料没写、但业务上确有这件事的时候用：附上依据补进来'
                }
                type="button"
              >
                补入
              </button>
            </div>
          </div>
          <div className="az5-input">
            <textarea
              aria-label="消息输入"
              // 写入锁取自 composerLocked（含裁定 C9 的「已有」项只读）：此前只把命令目标清成
              // 空数组，输入框仍可打字，于是任何输入都得到与界面矛盾的回执（界面明明选中着一行，
              // 系统却回「请先选中」），合并命令还会被后端回落成用户没选的另外两条。
              disabled={composerLocked}
              ref={composerRef}
              onChange={(e) => setComposerText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                batchMode
                  ? '批量模式 = 分诊；要与 AI 深聊，请切回单条'
                  : selectedIsMergedExisting
                    ? '选中的是「已有」的知识项，本页只能查看；要改它请到它所属的材料页'
                    : '对当前要素说点什么…'
              }
              rows={1}
              value={composerText}
            />
            <button
              aria-label="发送"
              className="az5-send"
              disabled={composerLocked || !composerText.trim()}
              onClick={handleSend}
              type="button"
            >
              ↑
            </button>
          </div>
          <p className="az5-note">
            {batchMode
              ? '批量 = 分诊：只有确认 / 拒绝有效；其余命令与 AI 对话请切回单条。'
              : '快捷命令只预填 /命令词，可自由续写；发送后由后端解析命令词、AI 解读正文。改表达写完整表达直接生效，只写新值/局部改法会转 AI 起草修订稿再采纳。确认 / 拒绝仍一键直发。'}
          </p>
        </div>
      </section>
    </div>
  );
}
function AnchorDetail({
  element,
  anchors,
  rawText,
}: {
  element: RequirementElementRead;
  anchors: Map<string, { status: string; ranges: { start: number; end: number; relocated: boolean }[] }>;
  rawText?: string;
}) {
  const anchor = anchors.get(element.id);
  if (!anchor || anchor.status === 'none') {
    return <p>未提供来源锚点（补入/新增项见其依据留痕）。</p>;
  }
  if (anchor.status === 'invalid') {
    return <p className="analysis-anchor-hint">来源定位待修正——可在区5『改范围』中修复。</p>;
  }
  return (
    <div>
      {anchor.ranges.map((range) => (
        <p key={`${range.start}-${range.end}`}>
          字符 {range.start}–{range.end}
          {range.relocated ? '（锚点已重定位）' : ''}
          {rawText ? (
            <span className="analysis-anchor-quote">引文「{rawText.slice(range.start, range.end)}」</span>
          ) : null}
        </p>
      ))}
    </div>
  );
}
