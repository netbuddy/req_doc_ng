/**
 * AI 请求链路回执 VM（04A §2.1 增补：链路回执条）。
 *
 * 原则：每盏灯只能由后端阶段事实点亮——内联段来自对话端点 SSE stage 帧，
 * 队列段来自 AgentRun 状态/事件（queued/started/终态）；本模块只做投影与
 * 「停滞」派生（阶段停留超阈值 → 显示态转 stalled），不做任何按时间的猜测点灯。
 * 时间戳为前端观测值（收到帧/事件的时刻），用于耗时展示与日志对账，非权威审计。
 */

export type TraceStage = 'accepted' | 'interpreting' | 'dispatching' | 'queued' | 'running' | 'writing';
export type TraceOutcome = 'pending' | 'done' | 'clarify' | 'refused' | 'failed';

/** 阶段全序（节点渲染顺序；path 是它的子序列） */
const STAGE_ORDER: TraceStage[] = ['accepted', 'interpreting', 'dispatching', 'queued', 'running', 'writing'];

export const TRACE_STAGE_LABELS: Record<TraceStage, string> = {
  accepted: '受理',
  interpreting: '解释',
  dispatching: '派发',
  queued: '排队',
  running: '执行',
  writing: '回写',
};

/** 停滞阈值（ms）：超过即节点转琥珀并给出可行动文案（页面详细设计 §5.1 链路回执条行） */
export const TRACE_STALL_THRESHOLDS_MS: Record<TraceStage, number> = {
  accepted: 8_000,
  interpreting: 30_000,
  dispatching: 10_000,
  queued: 10_000,
  running: 120_000,
  writing: 10_000,
};

export const TRACE_STALL_HINTS: Record<TraceStage, string> = {
  accepted: '后端未确认受理，可能网络或服务异常。',
  interpreting: '模型响应缓慢：可能有其它 AI 任务正在占用模型服务。',
  dispatching: '派发耗时异常，请留意工作区版本冲突。',
  queued: '排队时间偏长：请检查执行器（worker）是否在线。',
  running: 'AI 执行中耗时偏长：长任务或模型服务拥塞。',
  writing: '回写耗时异常。',
};

export interface AiRequestTrace {
  path: TraceStage[];
  /** stage -> 进入时刻（前端观测，epoch ms） */
  reached: Partial<Record<TraceStage, number>>;
  startedAt: number;
  finishedAt: number | null;
  outcome: TraceOutcome;
  message?: string | null;
  operationLabel?: string | null;
  runRef?: string | null;
  /** 排队停滞升级文案（结合运行态事实，如 worker 离线；一次性设置） */
  stallAlert?: string | null;
}

export function createTrace(now: number): AiRequestTrace {
  return {
    path: ['accepted', 'interpreting', 'dispatching'],
    reached: {},
    startedAt: now,
    finishedAt: null,
    outcome: 'pending',
  };
}

/** 收到后端阶段事实：记录进入时刻；阶段不在 path 时按全序插入（如 free-text 直达 running）。 */
export function traceAdvance(trace: AiRequestTrace, stage: TraceStage, now: number): AiRequestTrace {
  if (!STAGE_ORDER.includes(stage) || trace.reached[stage] !== undefined) {
    return trace;
  }
  const path = trace.path.includes(stage)
    ? trace.path
    : STAGE_ORDER.filter((s) => trace.path.includes(s) || s === stage);
  return { ...trace, path, reached: { ...trace.reached, [stage]: now } };
}

/** 派发出队列型运行：扩展队列支节点并立刻进入排队阶段（后端事实 = 受理回执带 run 引用）。 */
export function traceExtendQueue(trace: AiRequestTrace, runRef: string | null, now: number): AiRequestTrace {
  const path = STAGE_ORDER.filter(
    (s) => trace.path.includes(s) || s === 'queued' || s === 'running' || s === 'writing',
  );
  return {
    ...trace,
    path,
    runRef: runRef ?? trace.runRef ?? null,
    reached: { ...trace.reached, queued: trace.reached.queued ?? now },
  };
}

export function traceFinish(
  trace: AiRequestTrace,
  outcome: Exclude<TraceOutcome, 'pending'>,
  now: number,
  opts?: { message?: string | null; operationLabel?: string | null },
): AiRequestTrace {
  return {
    ...trace,
    finishedAt: now,
    outcome,
    message: opts?.message ?? trace.message,
    operationLabel: opts?.operationLabel ?? trace.operationLabel,
  };
}

export function traceCurrentStage(trace: AiRequestTrace): TraceStage | null {
  if (trace.finishedAt !== null) return null;
  let current: TraceStage | null = null;
  for (const stage of trace.path) {
    if (trace.reached[stage] !== undefined) current = stage;
  }
  return current;
}

export type TraceNodeState = 'pending' | 'active' | 'stalled' | 'done' | 'failed';

export interface TraceNodeVM {
  stage: TraceStage;
  label: string;
  state: TraceNodeState;
  /** active/stalled：阶段已用时；done：阶段耗时（进入下一阶段为界） */
  elapsedMs: number | null;
  stallHint: string | null;
}

export interface TraceRailVM {
  nodes: TraceNodeVM[];
  totalMs: number;
  finished: boolean;
  outcome: TraceOutcome;
  stalled: boolean;
}

export function projectTrace(trace: AiRequestTrace, now: number): TraceRailVM {
  const current = traceCurrentStage(trace);
  const endAt = trace.finishedAt ?? now;
  let stalled = false;
  const nodes: TraceNodeVM[] = trace.path.map((stage) => {
    const enteredAt = trace.reached[stage];
    if (enteredAt === undefined) {
      return { stage, label: TRACE_STAGE_LABELS[stage], state: 'pending', elapsedMs: null, stallHint: null };
    }
    // 阶段耗时上界 = 下一个已达阶段的进入时刻（或整体结束/当前时刻）
    const laterEntries = trace.path
      .map((s) => trace.reached[s])
      .filter((t): t is number => t !== undefined && t > enteredAt);
    const stageEnd = stage === current ? endAt : Math.min(...(laterEntries.length ? laterEntries : [endAt]));
    const elapsedMs = Math.max(0, stageEnd - enteredAt);
    if (trace.finishedAt !== null) {
      const failedHere = trace.outcome !== 'done' && stage === _lastReached(trace);
      return {
        stage, label: TRACE_STAGE_LABELS[stage],
        state: failedHere ? ('failed' as const) : ('done' as const),
        elapsedMs, stallHint: null,
      };
    }
    if (stage !== current) {
      return { stage, label: TRACE_STAGE_LABELS[stage], state: 'done', elapsedMs, stallHint: null };
    }
    const isStalled = elapsedMs > TRACE_STALL_THRESHOLDS_MS[stage];
    if (isStalled) stalled = true;
    return {
      stage, label: TRACE_STAGE_LABELS[stage],
      state: isStalled ? 'stalled' : 'active',
      elapsedMs,
      stallHint: isStalled ? TRACE_STALL_HINTS[stage] : null,
    };
  });
  return {
    nodes,
    totalMs: Math.max(0, endAt - trace.startedAt),
    finished: trace.finishedAt !== null,
    outcome: trace.outcome,
    stalled,
  };
}

function _lastReached(trace: AiRequestTrace): TraceStage | null {
  let last: TraceStage | null = null;
  for (const stage of trace.path) {
    if (trace.reached[stage] !== undefined) last = stage;
  }
  return last;
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return '<1s';
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m${String(seconds).padStart(2, '0')}s`;
}

/** 完成收敛的一行耗时摘要：只列有耗时意义的阶段 + 总时长。 */
export function traceSummaryText(trace: AiRequestTrace): string {
  const end = trace.finishedAt ?? trace.startedAt;
  const parts: string[] = [];
  for (const stage of ['interpreting', 'queued', 'running'] as TraceStage[]) {
    const enteredAt = trace.reached[stage];
    if (enteredAt === undefined) continue;
    const later = trace.path
      .map((s) => trace.reached[s])
      .filter((t): t is number => t !== undefined && t > enteredAt);
    const stageEnd = Math.min(...(later.length ? later : [end]));
    if (stageEnd - enteredAt >= 500) {
      parts.push(`${TRACE_STAGE_LABELS[stage]} ${formatDuration(stageEnd - enteredAt)}`);
    }
  }
  const total = `共 ${formatDuration(Math.max(0, end - trace.startedAt))}`;
  return parts.length ? `${parts.join(' · ')} · ${total}` : total;
}

/** 展开详情行（与 dialogue.* 结构化日志对账：阶段 + 进入时刻 + 运行引用）。 */
export function traceDetailLines(trace: AiRequestTrace): string[] {
  const lines = trace.path
    .filter((stage) => trace.reached[stage] !== undefined)
    .map((stage) => `${TRACE_STAGE_LABELS[stage]} @ ${new Date(trace.reached[stage] as number).toISOString()}`);
  if (trace.runRef) {
    lines.push(`agent_run_ref: ${trace.runRef}`);
  }
  return lines;
}
