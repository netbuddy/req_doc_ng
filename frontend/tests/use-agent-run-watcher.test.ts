/**
 * useAgentRunWatcher（通用轮询生命周期 hook）：卸载清理 / cancelled 终止（抢占）/ 按 run 隔离 / 停滞派生 /
 * 引用稳定性（合并裁定 F1：消费者以解构后的 start/stop 入 effect deps，整容器随 watching/stalled 换新）。
 * 事实源：issue #10 B2b ④（抽自条目形成页加固范式 issue #8）＋ T20260714-review-frontend-rewire 合并裁定 F1/F4。
 */
import { act, render, renderHook } from '@testing-library/react';
import { createElement, useCallback, useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAgentRunWatcher, type RunPollTick } from '../src/hooks/useAgentRunWatcher';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const noProgress = (): RunPollTick => ({ done: false, stallCandidate: true });

describe('useAgentRunWatcher', () => {
  it('done=true 即停表：watching 落回 false，不再续 poll', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>()
      .mockResolvedValueOnce(noProgress())
      .mockResolvedValueOnce({ done: true });
    const { result } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.watching).toBe(true); // 乐观进行态先落地（首 tick 延后一个间隔）

    await act(async () => { await vi.advanceTimersByTimeAsync(100); }); // 首 tick → running
    expect(result.current.watching).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(100); }); // 次 tick → done
    expect(result.current.watching).toBe(false);

    const callsAfterDone = poll.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(poll.mock.calls.length).toBe(callsAfterDone); // 终态后不再 poll
  });

  it('抢占（cancelled 终止 + 按 run 隔离）：新 start 使旧 loop 立即失效，旧 poll 不再被调用', async () => {
    const pollA = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const pollB = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const { result } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(pollA));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const aCallsBeforePreempt = pollA.mock.calls.length;

    act(() => result.current.start(pollB)); // 抢占
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    expect(pollA.mock.calls.length).toBe(aCallsBeforePreempt); // 旧 loop 不再续
    expect(pollB.mock.calls.length).toBeGreaterThan(1);        // 新 loop 在跑
    expect(result.current.watching).toBe(true);
  });

  it('stop：手动终止清表，watching/stalled 复位，之后不再 poll', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const { result } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(result.current.watching).toBe(true);

    act(() => result.current.stop());
    const callsAfterStop = poll.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(poll.mock.calls.length).toBe(callsAfterStop);
    expect(result.current.watching).toBe(false);
  });

  it('卸载清理：卸载后在途定时器不再触发 poll（无孤儿轮询/卸载后 setState）', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const { result, unmount } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(150); });
    const callsBeforeUnmount = poll.mock.calls.length;

    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(poll.mock.calls.length).toBe(callsBeforeUnmount); // 卸载后不再 poll
  });

  it('停滞派生：连续停滞候选超阈值仍未终态 → stalled=true；改返 done 前保持在途', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const { result } = renderHook(() =>
      useAgentRunWatcher({ intervalMs: 100, stallThresholdMs: 250 }),
    );

    act(() => result.current.start(poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(200); }); // 未过阈值
    expect(result.current.stalled).toBe(false);

    await act(async () => { await vi.advanceTimersByTimeAsync(200); }); // 累计 >250ms
    expect(result.current.stalled).toBe(true);
    expect(result.current.watching).toBe(true); // 停滞≠终止，仍在途
  });

  it('停滞时钟复位（F4）：候选中途观察到进展 → 时钟从头计，跨间隙不累积', async () => {
    // 时序（间隔 100 / 阈值 250）：t100 候选 → t200 候选 → t300 进展（复位）→ t400 起再候选。
    // 若复位分支缺失：t100 起算，t400 已累积 300ms ≥ 阈值 → 误判 stalled（F4 变异存活路径）。
    const poll = vi.fn<() => Promise<RunPollTick>>()
      .mockResolvedValueOnce(noProgress())
      .mockResolvedValueOnce(noProgress())
      .mockResolvedValueOnce({ done: false, stallCandidate: false })
      .mockResolvedValue(noProgress());
    const { result } = renderHook(() =>
      useAgentRunWatcher({ intervalMs: 100, stallThresholdMs: 250 }),
    );

    act(() => result.current.start(poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(500); }); // t500：复位后仅累积 100ms
    expect(result.current.stalled).toBe(false);

    await act(async () => { await vi.advanceTimersByTimeAsync(200); }); // t700：复位起点累积 300ms
    expect(result.current.stalled).toBe(true); // 复位后时钟仍在工作，非永久哑火
  });

  it('停滞清除（F4）：stalled=true 后观察到进展 → stalled 落回 false，watch 不中断', async () => {
    let candidate = true;
    const poll = vi.fn(async (): Promise<RunPollTick> => ({ done: false, stallCandidate: candidate }));
    const { result } = renderHook(() =>
      useAgentRunWatcher({ intervalMs: 100, stallThresholdMs: 250 }),
    );

    act(() => result.current.start(poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(400); }); // 累积 300ms ≥ 阈值
    expect(result.current.stalled).toBe(true);

    candidate = false; // 进展到来
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(result.current.stalled).toBe(false);
    expect(result.current.watching).toBe(true);
  });

  it('引用稳定性（F1 契约钉）：start/stop 恒稳；容器仅随状态换新，无状态变化时 memo 稳定', async () => {
    const { result, rerender } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));
    const initial = result.current;

    rerender(); // 无关重渲染
    expect(result.current).toBe(initial); // 状态未变 → 容器 memo 稳定

    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    act(() => result.current.start(poll));
    // watching 翻转 → 容器换新（故消费者 effect deps 禁依赖整容器），但控制面恒稳
    expect(result.current.start).toBe(initial.start);
    expect(result.current.stop).toBe(initial.stop);
  });

  it('消费者级（F1 出厂路径）：卸载专用 cleanup + 无关重渲染，不自毁在途轮询', async () => {
    // 与 RequirementAnalysisFlow 同构消费：cleanup 依赖解构后的 stop（恒稳）→ 仅卸载时执行。
    function WatcherConsumer({ label, poll }: { label: string; poll: () => Promise<RunPollTick> }) {
      const { start, stop } = useAgentRunWatcher({ intervalMs: 100 });
      const close = useCallback(() => stop(), [stop]);
      useEffect(() => close, [close]); // 卸载专用 cleanup：close 若随渲染换新即退化为每渲染自毁
      useEffect(() => { start(poll); }, [start, poll]);
      return createElement('span', null, label);
    }
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const { rerender, unmount } = render(createElement(WatcherConsumer, { label: 'a', poll }));

    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    const beforeRerender = poll.mock.calls.length;
    expect(beforeRerender).toBeGreaterThan(0);

    rerender(createElement(WatcherConsumer, { label: 'b', poll })); // 无关 props 变化
    rerender(createElement(WatcherConsumer, { label: 'c', poll }));
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(poll.mock.calls.length).toBeGreaterThan(beforeRerender); // 轮询仍在跑，未被 cleanup 链杀死

    unmount();
    const afterUnmount = poll.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(poll.mock.calls.length).toBe(afterUnmount); // 卸载后彻底停
  });

  // ---- P0 收编：EventSource 优先 + 轮询兜底 + immediate 首拍（四页统一实现的新增能力）----

  it('immediate：首拍同步执行（形成页手写轮询 void poll() 语义），无需推进定时器', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const { result } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(poll, { immediate: true }));
    expect(poll).toHaveBeenCalledTimes(1); // 首拍同步（对比默认延后：见「done=true 即停」用例首拍未触发）
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(poll).toHaveBeenCalledTimes(2); // 续拍照常
  });

  it('EventSource 优先：传 subscribe 时先建订阅、不轮询；onFallback 才起轮询兜底', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const close = vi.fn();
    let fallback: () => void = () => {};
    const subscribe = vi.fn((h: { onFallback: () => void }) => {
      fallback = h.onFallback;
      return { close };
    });
    const { result } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(poll, { subscribe }));
    expect(subscribe).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(poll).not.toHaveBeenCalled(); // EventSource 优先：订阅在途不轮询
    expect(result.current.watching).toBe(true);

    act(() => fallback()); // 订阅上报回退（如 EventSource 报错/环境不支持）
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(poll).toHaveBeenCalledTimes(1); // 轮询兜底接管
  });

  it('目标切换：新 start 关闭旧订阅（不泄漏 EventSource）', () => {
    const close1 = vi.fn();
    const close2 = vi.fn();
    const noop = () => Promise.resolve(noProgress());
    const { result } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(noop, { subscribe: () => ({ close: close1 }) }));
    act(() => result.current.start(noop, { subscribe: () => ({ close: close2 }) }));
    expect(close1).toHaveBeenCalledTimes(1); // 旧订阅在新 start 时关闭
    expect(close2).not.toHaveBeenCalled();
  });

  it('stop 关闭订阅；stop 后陈旧 onFallback 不再起轮询（抢占/停表守卫）', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(noProgress());
    const close = vi.fn();
    let fallback: () => void = () => {};
    const { result } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() =>
      result.current.start(poll, {
        subscribe: (h) => {
          fallback = h.onFallback;
          return { close };
        },
      }),
    );
    act(() => result.current.stop());
    expect(close).toHaveBeenCalledTimes(1);

    act(() => fallback()); // 陈旧回退到达
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(poll).not.toHaveBeenCalled();
  });

  it('卸载关闭订阅（无孤儿 EventSource）', () => {
    const close = vi.fn();
    const noop = () => Promise.resolve(noProgress());
    const { result, unmount } = renderHook(() => useAgentRunWatcher({ intervalMs: 100 }));

    act(() => result.current.start(noop, { subscribe: () => ({ close }) }));
    unmount();
    expect(close).toHaveBeenCalledTimes(1);
  });
});
