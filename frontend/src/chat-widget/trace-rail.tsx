/**
 * 统一 AI 对话控件 · 链路回执条内建接线（工作包 01 篇 §7）。
 *
 * 复用既有 `AiTraceRail`（workbenches/）＋ `ai-request-trace` VM（view-models/）作为控件组成部分——
 * 只 import 不重写（本包明示不改回执设施，failure_policy ③）。六段式 stage 帧的灯只由后端事实点亮：
 * transport.send 的 onStage 帧驱动 traceAdvance，停滞态由 VM 前端派生。控件不做任何按时间的猜测点灯。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AiTraceRail } from '../workbenches/AiTraceRail';
import {
  createTrace,
  traceAdvance,
  traceFinish,
  type AiRequestTrace,
  type TraceStage,
} from '../view-models/ai-request-trace';

export interface DialogueTraceController {
  trace: AiRequestTrace | null;
  /** 发送开始：建 trace（受理/解释/派发 三段起手）。 */
  begin: (now?: number) => void;
  /** 收到后端 stage 帧：记录进入时刻（非六段名的帧被 VM 安全忽略）。 */
  stage: (name: string, now?: number) => void;
  /** 终态：done（result 帧）/failed（error 帧或中断）。 */
  finish: (outcome: 'done' | 'failed', now?: number) => void;
  /** 清空（换会话/重置）。 */
  reset: () => void;
}

export function useDialogueTrace(): DialogueTraceController {
  const [trace, setTrace] = useState<AiRequestTrace | null>(null);
  // 方法恒稳（只闭包 setTrace），仅 trace 值随帧变化——消费者可安全把方法入 effect/callback 依赖。
  const begin = useCallback((now = Date.now()) => setTrace(createTrace(now)), []);
  const stage = useCallback(
    (name: string, now = Date.now()) => setTrace((t) => (t ? traceAdvance(t, name as TraceStage, now) : t)),
    [],
  );
  const finish = useCallback(
    (outcome: 'done' | 'failed', now = Date.now()) => setTrace((t) => (t ? traceFinish(t, outcome, now) : t)),
    [],
  );
  const reset = useCallback(() => setTrace(null), []);
  return useMemo(() => ({ trace, begin, stage, finish, reset }), [trace, begin, stage, finish, reset]);
}

/** 回执条容器：trace 存在时渲染 AiTraceRail，随时间推进耗时展示（未终态时定时重算 now）。 */
export function ChatTraceRail({ trace }: { trace: AiRequestTrace | null }): ReactNode {
  const [now, setNow] = useState(() => Date.now());
  const finished = trace?.finishedAt != null;
  const activeRef = useRef(false);
  activeRef.current = !!trace && !finished;

  useEffect(() => {
    if (!activeRef.current) return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [trace, finished]);

  if (!trace) return null;
  return (
    <div className="cw-trace">
      <AiTraceRail trace={trace} now={finished ? (trace.finishedAt as number) : now} />
    </div>
  );
}
