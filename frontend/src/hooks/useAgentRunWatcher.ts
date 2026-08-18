import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/**
 * 通用异步任务追踪 hook（issue #10 B2b ④；抽自条目形成页加固范式 issue #8）。
 *
 * P0 传输层收敛后为四页唯一实现：**EventSource 优先 + 轮询兜底 + 轮询目标参数化**。
 * 轮询目标由各页自供（poll 回调：Analysis 供 agentRunApi.get(runId)、ReviewFlow/Formation/Diagram
 * 供各自业务读视图）；EventSource 订阅可选（仅知识抽取页现状有推送——传 subscribe 工厂启用，
 * 其余三页纯轮询、不新增推送通道，P0 是换底不是改进）。收口三类跨页复现的轮询缺陷：
 *  - 卸载清理：卸载即清表、关订阅、置 disposed，杜绝孤儿定时器/订阅与卸载后 setState；
 *  - cancelled 终止：新 start 抢占旧 watch（seq 递增使在途 loop 立即失效），stop 手动终止；
 *  - 按 run 隔离：每次 start 取新 watcherId，被抢占的 loop 不再 setState/续表。
 * 另派生「停滞」态：run 持续 active（如滞留 started）超阈值仍未终态（复用 AI 请求链路回执条
 * 「停滞=前端派生」范式，见 B1 审查 O1：inline 后台线程被杀致 run 滞留 started）。
 */
export interface RunPollTick {
  /** 已到终态：停表、结束本次 watch。 */
  done: boolean;
  /**
   * 停滞候选（真实契约=「本 tick 是否观察到前进」的否定，而非「后端是否在跑」）：
   * 真值持续超阈值 → stalled；false=观察到进展（或取数失败等不计入累积的 tick），复位停滞时钟。
   * 缺省视为 true（无进展信息按累积计）。
   */
  stallCandidate?: boolean;
}

/** hook 拥有生命周期的订阅句柄（如 EventSource 封装）：stop/卸载/新 start 时由 hook 关闭。 */
export interface AgentRunSubscriptionHandle {
  close: () => void;
}

export interface RunWatchStartOptions {
  /**
   * EventSource 优先：提供订阅工厂时，start 先建订阅、**不立即轮询**，仅在订阅上报回退
   * （`onFallback`，如 EventSource 报错或环境不支持）时才起轮询兜底。工厂内自行接线业务
   * 消息处理（终态处置由页面在其消息回调里做，通常再调本 hook 的 stop）；hook 只负责在
   * stop/卸载/新 start 时关闭订阅句柄。不传 subscribe＝纯轮询（评审/形成/图表现状）。
   */
  subscribe?: (handlers: { onFallback: () => void }) => AgentRunSubscriptionHandle;
  /**
   * 首拍立即执行（默认延后一个间隔）：形成页手写轮询原为同步首拍（`void poll()`），迁移时置
   * true 保字节一致；抽取/评审维持默认延后（乐观渲染先落地，见下方 tick 首拍注释）。
   * 仅作用于轮询兜底的首拍；EventSource 优先时不影响订阅建立。
   */
  immediate?: boolean;
}

export interface UseAgentRunWatcherOptions {
  /** 轮询间隔（毫秒）。 */
  intervalMs: number;
  /** 停滞阈值（毫秒）：连续停滞候选 tick 累积超此时长仍未终态 → stalled；省略=不做停滞派生。 */
  stallThresholdMs?: number;
}

export interface AgentRunWatcher {
  /** 是否有在途 watch（已 start 且未到终态/未 stop）。 */
  watching: boolean;
  /** 停滞派生态：连续停滞候选超阈值仍未终态。 */
  stalled: boolean;
  /** 启动 watch（抢占旧 watch）；poll 每 intervalMs 调一次，返回 done=true 即停。opts 见 RunWatchStartOptions。 */
  start: (poll: () => Promise<RunPollTick>, opts?: RunWatchStartOptions) => void;
  /** 手动停止当前 watch（清表、关订阅、复位 watching/stalled）。 */
  stop: () => void;
}

export function useAgentRunWatcher(options: UseAgentRunWatcherOptions): AgentRunWatcher {
  const { intervalMs, stallThresholdMs } = options;
  const seqRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const subscriptionRef = useRef<AgentRunSubscriptionHandle | null>(null);
  const disposedRef = useRef(false);
  const stallSinceRef = useRef<number | null>(null); // 首个停滞候选 tick 的时刻（停滞时钟起点）
  const [watching, setWatching] = useState(false);
  const [stalled, setStalled] = useState(false);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const closeSubscription = useCallback(() => {
    subscriptionRef.current?.close();
    subscriptionRef.current = null;
  }, []);

  const stop = useCallback(() => {
    seqRef.current += 1; // 让在途 loop 失效（按 run 隔离）
    clearTimer();
    closeSubscription();
    stallSinceRef.current = null;
    if (!disposedRef.current) {
      setWatching(false);
      setStalled(false);
    }
  }, [clearTimer, closeSubscription]);

  const start = useCallback(
    (poll: () => Promise<RunPollTick>, opts?: RunWatchStartOptions) => {
      seqRef.current += 1;
      const watcherId = seqRef.current;
      clearTimer();
      closeSubscription();
      stallSinceRef.current = null;
      if (!disposedRef.current) {
        setWatching(true);
        setStalled(false);
      }

      const tick = async () => {
        let result: RunPollTick;
        try {
          result = await poll();
        } catch {
          // poll 应自行消化瞬时错误并返回 done/active；意外抛出=安全网：停表结束本次 watch，
          // 不 setState 已由 disposed/抢占守卫兜住。避免异常泄漏导致 loop 静默失活或 runaway。
          if (disposedRef.current || watcherId !== seqRef.current) {
            return;
          }
          clearTimer();
          stallSinceRef.current = null;
          setWatching(false);
          setStalled(false);
          return;
        }
        if (disposedRef.current || watcherId !== seqRef.current) {
          return; // 卸载或被抢占：不 setState、不续表
        }
        if (result.done) {
          clearTimer();
          stallSinceRef.current = null;
          setWatching(false);
          setStalled(false);
          return;
        }
        // 停滞派生：连续停滞候选（未观察到前进）超阈值仍未终态
        const stallCandidate = result.stallCandidate ?? true;
        if (stallThresholdMs !== undefined && stallCandidate) {
          const now = Date.now();
          if (stallSinceRef.current === null) {
            stallSinceRef.current = now;
          } else if (now - stallSinceRef.current >= stallThresholdMs) {
            setStalled(true);
          }
        } else {
          stallSinceRef.current = null;
          setStalled(false);
        }
        timerRef.current = window.setTimeout(tick, intervalMs);
      };

      // 首个 tick 默认延后一个间隔而非同步执行：使 watching=true 的乐观渲染先落地（进行态优先本地——
      // 派发后首次刷新前服务端事实还没翻转），且调用方在 start 前通常已 refresh 过工作区，不丢首帧数据。
      // opts.immediate=true 时同步首拍（形成页手写轮询原语义，保字节一致）。
      const startPolling = () => {
        if (disposedRef.current || watcherId !== seqRef.current) {
          return; // 已卸载/被抢占（订阅回退在异步回调里到达时可能已过期）
        }
        clearTimer();
        if (opts?.immediate) {
          void tick();
        } else {
          timerRef.current = window.setTimeout(tick, intervalMs);
        }
      };

      if (opts?.subscribe) {
        // EventSource 优先：先建订阅、不轮询；订阅上报回退才起轮询兜底。
        // （agentRunApi.subscribe 在 EventSource 不可用时会立即回调 onFallback，故此处天然覆盖环境降级。）
        subscriptionRef.current = opts.subscribe({ onFallback: startPolling });
      } else {
        startPolling();
      }
    },
    [clearTimer, closeSubscription, intervalMs, stallThresholdMs],
  );

  useEffect(() => {
    disposedRef.current = false; // StrictMode 双调用：mount 先复位（否则卸载置下的 disposed 吞掉全部 watch）
    return () => {
      disposedRef.current = true;
      clearTimer();
      closeSubscription();
    };
  }, [clearTimer, closeSubscription]);

  // 返回容器 memo 化：整对象入消费者 deps 时不随渲染换新（合并裁定 F1 加固）。注意这**不**豁免
  // 消费者——watching/stalled 变化仍会换新容器，effect deps 应依赖解构后的 start/stop（各自恒稳）。
  return useMemo(() => ({ watching, stalled, start, stop }), [watching, stalled, start, stop]);
}
