/**
 * 统一 AI 对话控件 · 动作分发器与状态机（01 篇 §4，验收 A4）。
 * 覆盖：dispatching 单飞守卫（防双击）／settled(error) 恢复与错误行入线程／pending-followup 收束标记；
 * 附：未注册 host 降级、confirm 取消、submit/url/component 出口路由。
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useActionDispatch, type ActionDispatchConfig } from '../src/chat-widget/action-dispatch';
import type { ChatHostAdapter, HostActionResult } from '../src/chat-widget/types';

function makeAdapter(actions: ChatHostAdapter['actions']): ChatHostAdapter {
  return {
    hostId: 'test',
    sessionKey: () => 'test:obj:1',
    transport: { send: () => ({ abort: () => {} }), buildCommand: (t) => ({ t }) },
    getContext: () => ({}),
    threadSource: { kind: 'local' },
    actions,
  };
}

function makeConfig(over: Partial<ActionDispatchConfig> & { adapter: ChatHostAdapter }): ActionDispatchConfig {
  return {
    onSubmit: vi.fn(() => Promise.resolve(true)),
    onUrl: vi.fn(),
    onComponent: vi.fn(),
    notifyError: vi.fn(),
    confirmAction: () => true,
    ...over,
  };
}

/** 手动可控的 host handler：返回悬挂 promise，测试自行 resolve。 */
function deferredHandler() {
  let resolve!: (r: HostActionResult) => void;
  const fn = vi.fn(() => new Promise<HostActionResult>((res) => (resolve = res)));
  return { fn, resolve: (r: HostActionResult) => resolve(r) };
}

describe('useActionDispatch（动作状态机）', () => {
  it('单飞守卫：dispatching 期间同实例再次触发被忽略（防双击）', async () => {
    const handler = deferredHandler();
    const adapter = makeAdapter({ adopt: handler.fn });
    const { result } = renderHook(() => useActionDispatch(makeConfig({ adapter })));

    act(() => {
      result.current.dispatch({ kind: 'host', label: '采纳', name: 'adopt' }, 'i1');
      result.current.dispatch({ kind: 'host', label: '采纳', name: 'adopt' }, 'i1'); // 双击
    });
    expect(result.current.phaseOf('i1')).toBe('dispatching');
    expect(handler.fn).toHaveBeenCalledTimes(1); // 只放行一次

    await act(async () => handler.resolve({ ok: true }));
    expect(result.current.phaseOf('i1')).toBe('settled-ok');
  });

  it('settled(error)：按钮恢复可点，错误行入线程（notifyError），可再次分发', async () => {
    const handler = deferredHandler();
    const notifyError = vi.fn();
    const adapter = makeAdapter({ adopt: handler.fn });
    const { result } = renderHook(() => useActionDispatch(makeConfig({ adapter, notifyError })));

    act(() => result.current.dispatch({ kind: 'host', label: '采纳', name: 'adopt' }, 'i1'));
    await act(async () => handler.resolve({ ok: false, message: '写回冲突' }));

    expect(result.current.phaseOf('i1')).toBe('settled-error');
    expect(notifyError).toHaveBeenCalledWith('写回冲突');

    // 恢复：settled-error 非 dispatching，允许再次触发
    act(() => result.current.dispatch({ kind: 'host', label: '采纳', name: 'adopt' }, 'i1'));
    expect(handler.fn).toHaveBeenCalledTimes(2);
    expect(result.current.phaseOf('i1')).toBe('dispatching');
  });

  it('pending-followup：成功后停在 awaiting-followup，markLinked 推进到 linked', async () => {
    const handler = deferredHandler();
    const adapter = makeAdapter({ adopt: handler.fn });
    const { result } = renderHook(() => useActionDispatch(makeConfig({ adapter })));

    act(() =>
      result.current.dispatch(
        { kind: 'host', label: '采纳', name: 'adopt', followup: 'pending-followup' },
        'i1',
      ),
    );
    await act(async () => handler.resolve({ ok: true }));
    expect(result.current.phaseOf('i1')).toBe('awaiting-followup');

    act(() => result.current.markLinked('i1'));
    expect(result.current.phaseOf('i1')).toBe('linked');
  });

  it('未注册 host 动作名：降级不报错（notifyError），相位保持 idle', () => {
    const notifyError = vi.fn();
    const adapter = makeAdapter({});
    const { result } = renderHook(() => useActionDispatch(makeConfig({ adapter, notifyError })));

    expect(result.current.isActionRegistered('adopt')).toBe(false);
    act(() => result.current.dispatch({ kind: 'host', label: '采纳', name: 'adopt' }, 'i1'));
    expect(result.current.phaseOf('i1')).toBe('idle');
    expect(notifyError).toHaveBeenCalled();
  });

  it('confirm 取消：处理函数不被调用，相位保持 idle', () => {
    const handler = deferredHandler();
    const adapter = makeAdapter({ del: handler.fn });
    const { result } = renderHook(() =>
      useActionDispatch(makeConfig({ adapter, confirmAction: () => false })),
    );
    act(() =>
      result.current.dispatch({ kind: 'host', label: '删除', name: 'del', confirm: '确认删除？' }, 'i1'),
    );
    expect(handler.fn).not.toHaveBeenCalled();
    expect(result.current.phaseOf('i1')).toBe('idle');
  });

  it('出口路由：submit→onSubmit、url→onUrl、component→onComponent', async () => {
    const onSubmit = vi.fn(() => Promise.resolve(true));
    const onUrl = vi.fn();
    const onComponent = vi.fn();
    const adapter = makeAdapter({});
    const { result } = renderHook(() =>
      useActionDispatch(makeConfig({ adapter, onSubmit, onUrl, onComponent })),
    );

    await act(async () => result.current.dispatch({ kind: 'submit', label: '采纳修订', data: { x: 1 } }, 's'));
    expect(onSubmit).toHaveBeenCalled();
    expect(result.current.phaseOf('s')).toBe('settled-ok');

    act(() => result.current.dispatch({ kind: 'url', label: '查看原文', href: '/x' }, 'u'));
    expect(onUrl).toHaveBeenCalled();
    expect(result.current.phaseOf('u')).toBe('settled-ok');

    act(() => result.current.dispatch({ kind: 'component', label: '打开', name: 'editor', props: {} }, 'c'));
    expect(onComponent).toHaveBeenCalledWith('editor', {});
  });
});
