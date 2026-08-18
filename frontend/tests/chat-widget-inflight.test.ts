/**
 * 统一 AI 对话控件 · 两级在途模型（01 篇 §5）。
 * 动作级与会话级互不牵连；动作级键含 sessionKey 与实例 id（回执归属发送会话）。
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useInflight } from '../src/chat-widget/inflight';

describe('useInflight（两级在途）', () => {
  it('会话级与动作级独立：标记会话不牵连动作，反之亦然', () => {
    const { result } = renderHook(() => useInflight());

    act(() => result.current.markSession('s1'));
    expect(result.current.hasSession('s1')).toBe(true);
    expect(result.current.hasSession('s2')).toBe(false);
    expect(result.current.hasAction('s1', 'a1')).toBe(false); // 会话级不牵连动作级

    act(() => result.current.markAction('s1', 'a1'));
    expect(result.current.hasAction('s1', 'a1')).toBe(true);
    expect(result.current.hasAction('s1', 'a2')).toBe(false);
    expect(result.current.hasAction('s2', 'a1')).toBe(false); // 动作级键含 sessionKey

    act(() => result.current.clearAction('s1', 'a1'));
    expect(result.current.hasAction('s1', 'a1')).toBe(false);
    expect(result.current.hasSession('s1')).toBe(true); // 清动作不影响会话
  });

  it('sessionsInflight 列出全部在途会话（会话条徽标用）', () => {
    const { result } = renderHook(() => useInflight());
    act(() => {
      result.current.markSession('s1');
      result.current.markSession('s2');
    });
    expect(new Set(result.current.sessionsInflight())).toEqual(new Set(['s1', 's2']));
    act(() => result.current.clearSession('s1'));
    expect(result.current.sessionsInflight()).toEqual(['s2']);
  });
});
