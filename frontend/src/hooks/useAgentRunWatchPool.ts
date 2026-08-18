import { useCallback, useEffect, useRef } from 'react';
import type { RunPollTick } from './useAgentRunWatcher';

/**
 * 并发多路 AgentRun 追踪池（issue #8 缺陷 7「同类型多个在途 run 各持一路循环、互不抢占」的语义
 * 载体；对齐 P0 收编前手写轮询的 `pollTimersRef` 多循环共存）。
 *
 * 与 useAgentRunWatcher（单实例、新 start 抢占旧 watch）的区别：本池每次 start 取一个独立
 * watcherId，各自 setTimeout 自排期、跑到各自终态才停，互不干扰——被并发发起的另一路不会杀死在途
 * 那一路的续拍与终态副作用。适用于「同类型多个在途 run 需共存、各自终态副作用都必须跑到」的场景：
 * 结构复核即典型——手动复核 R1（置 recheckBusy=true）在途时，字段修订又触发链式复核 R2，若 R2 抢占
 * R1，R1 的终态 releaseBusy 永不执行，recheckBusy 永久卡死（见 T20260717-ucw-p0-transport 裁定 F1；
 * 该缺陷正是 P0 把两路复核循环收敛到单实例 hook 时引入，本池按修复方向 (b) 恢复旧共存语义）。
 *
 * 与单实例 hook 一致的守卫：卸载即置 disposed 并清空全部在途定时器（无孤儿轮询/卸载后续拍）；poll
 * 意外抛出=安全网，停本路循环。不派生 watching/stalled 态——消费方按各自 run 的后端事实自行呈现，
 * 故无 watch 相关重渲染。poll 体内多次 await 间的卸载后 setState 由消费方自持守卫（hook 只在 tick
 * 边界查卸载），与单实例 hook 同约。
 */
export interface RunWatchPoolStartOptions {
  /**
   * 首拍立即执行（默认延后一个间隔）：形成页手写轮询原为同步首拍（`void poll()`），迁移时置 true
   * 保字节一致。本池不支持 EventSource 优先（复核路径纯轮询，无推送通道）。
   */
  immediate?: boolean;
}

export interface AgentRunWatchPool {
  /** 启动一路独立 watch（不抢占其它在途路）；poll 每 intervalMs 调一次，返回 done=true 即停本路。 */
  start: (poll: () => Promise<RunPollTick>, opts?: RunWatchPoolStartOptions) => void;
}

export function useAgentRunWatchPool(options: { intervalMs: number }): AgentRunWatchPool {
  const { intervalMs } = options;
  const timersRef = useRef<Map<number, number>>(new Map());
  const seqRef = useRef(0);
  const disposedRef = useRef(false);

  const start = useCallback(
    (poll: () => Promise<RunPollTick>, opts?: RunWatchPoolStartOptions) => {
      seqRef.current += 1;
      const watcherId = seqRef.current;
      const timers = timersRef.current;

      const clearOwn = () => {
        const timer = timers.get(watcherId);
        if (timer !== undefined) {
          window.clearTimeout(timer);
        }
        timers.delete(watcherId);
      };

      const tick = async () => {
        let result: RunPollTick;
        try {
          result = await poll();
        } catch {
          // poll 应自行消化瞬时错误并返回 done；意外抛出=安全网：停本路循环（不 setState 由消费方
          // 卸载守卫兜住），避免异常泄漏致孤儿定时器或 runaway。
          clearOwn();
          return;
        }
        if (disposedRef.current) {
          clearOwn();
          return; // 卸载：不续表（poll 体内 setState 由消费方守卫）
        }
        if (result.done) {
          clearOwn();
          return;
        }
        timers.set(watcherId, window.setTimeout(tick, intervalMs));
      };

      if (opts?.immediate) {
        void tick();
      } else {
        timers.set(watcherId, window.setTimeout(tick, intervalMs));
      }
    },
    [intervalMs],
  );

  useEffect(() => {
    disposedRef.current = false; // StrictMode 双调用：mount 先复位（否则卸载置下的 disposed 吞掉全部 watch）
    const timers = timersRef.current;
    return () => {
      disposedRef.current = true;
      timers.forEach((timer) => window.clearTimeout(timer));
      timers.clear();
    };
  }, []);

  return { start };
}
