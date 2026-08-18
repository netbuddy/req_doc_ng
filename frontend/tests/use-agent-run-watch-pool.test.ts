/**
 * useAgentRunWatchPool（并发多路 AgentRun 追踪池）：同类型多个在途 run 各持一路循环、互不抢占，
 * 各自跑到终态、各自终态副作用都必被执行。
 *
 * 事实源＝T20260717-ucw-p0-transport 裁定 F1/F2：P0 把条目形成页两路结构复核循环收敛到单实例
 * useAgentRunWatcher 后，新 start 抢占旧 watch——手动复核 R1（ownsBusy，置 recheckBusy=true）在途时，
 * 静默链式复核 R2 抢占并杀死 R1 的在途循环，R1 的终态 releaseBusy 永不执行、recheckBusy 永久卡死。
 * 本池按修复方向 (b) 恢复收编前 pollTimersRef 的共存语义。下方回归钉核心即「被并发发起的另一路不得
 * 杀死在途那一路，两路终态回调都要跑到」——若退回单实例抢占语义，第 1、2 用例即红。
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAgentRunWatchPool } from '../src/hooks/useAgentRunWatchPool';
import type { RunPollTick } from '../src/hooks/useAgentRunWatcher';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const active = (): RunPollTick => ({ done: false });

describe('useAgentRunWatchPool', () => {
  it('并发共存（F1 回归钉）：第二路 start 不抢占第一路——两路都续拍、都跑到各自终态回调', async () => {
    // R1 长跑（第 3 拍才终态并 releaseBusy）；R2 在 R1 在途时发起、第 1 拍即终态。
    // 单实例抢占语义下 R2 的 start 会杀死 R1，onDoneA 永不触发（=F1 的 recheckBusy 卡死）。
    const onDoneA = vi.fn();
    const onDoneB = vi.fn();
    let aTicks = 0;
    const pollA = vi.fn(async (): Promise<RunPollTick> => {
      aTicks += 1;
      if (aTicks >= 3) {
        onDoneA(); // R1 的终态副作用（对应 releaseBusy）
        return { done: true };
      }
      return active();
    });
    const pollB = vi.fn(async (): Promise<RunPollTick> => {
      onDoneB();
      return { done: true };
    });
    const { result } = renderHook(() => useAgentRunWatchPool({ intervalMs: 100 }));

    act(() => result.current.start(pollA, { immediate: true })); // R1 首拍同步
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(pollA).toHaveBeenCalledTimes(1);

    act(() => result.current.start(pollB, { immediate: true })); // R2 在 R1 在途时发起
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(onDoneB).toHaveBeenCalledTimes(1); // R2 立即终态

    await act(async () => { await vi.advanceTimersByTimeAsync(300); }); // R1 续拍至终态
    expect(onDoneA).toHaveBeenCalledTimes(1); // R1 未被 R2 杀死，终态回调跑到
    expect(pollA.mock.calls.length).toBe(3);
  });

  it('各路独立停表：一路终态后不再续拍，另一路照常续拍', async () => {
    const pollShort = vi.fn<() => Promise<RunPollTick>>()
      .mockResolvedValueOnce(active())
      .mockResolvedValue({ done: true });
    const pollLong = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(active());
    const { result } = renderHook(() => useAgentRunWatchPool({ intervalMs: 100 }));

    act(() => result.current.start(pollShort));
    act(() => result.current.start(pollLong));
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });

    const shortCalls = pollShort.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(pollShort.mock.calls.length).toBe(shortCalls); // 短路终态后不再续
    expect(pollLong.mock.calls.length).toBeGreaterThan(shortCalls); // 长路仍在跑
  });

  it('immediate：首拍同步执行（形成页手写轮询 void poll() 语义）', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(active());
    const { result } = renderHook(() => useAgentRunWatchPool({ intervalMs: 100 }));

    act(() => result.current.start(poll, { immediate: true }));
    expect(poll).toHaveBeenCalledTimes(1); // 同步首拍
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(poll).toHaveBeenCalledTimes(2); // 续拍照常

    const other = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(active());
    act(() => result.current.start(other)); // 无 immediate：首拍延后
    expect(other).toHaveBeenCalledTimes(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(other).toHaveBeenCalledTimes(1);
  });

  it('卸载清理：卸载后全部在途路不再续拍（无孤儿轮询/卸载后 setState）', async () => {
    const pollA = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(active());
    const pollB = vi.fn<() => Promise<RunPollTick>>().mockResolvedValue(active());
    const { result, unmount } = renderHook(() => useAgentRunWatchPool({ intervalMs: 100 }));

    act(() => result.current.start(pollA));
    act(() => result.current.start(pollB));
    await act(async () => { await vi.advanceTimersByTimeAsync(150); });
    const aBefore = pollA.mock.calls.length;
    const bBefore = pollB.mock.calls.length;

    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(pollA.mock.calls.length).toBe(aBefore); // 两路都停
    expect(pollB.mock.calls.length).toBe(bBefore);
  });

  it('poll 抛出＝安全网：停本路循环，不泄漏孤儿定时器', async () => {
    const poll = vi.fn<() => Promise<RunPollTick>>().mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useAgentRunWatchPool({ intervalMs: 100 }));

    act(() => result.current.start(poll, { immediate: true }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const calls = poll.mock.calls.length;
    expect(calls).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(poll.mock.calls.length).toBe(calls); // 抛出后不再续拍
  });
});
