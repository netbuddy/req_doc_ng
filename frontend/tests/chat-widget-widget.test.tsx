/**
 * 统一 AI 对话控件 · 控件本体集成（01 篇 §3–§6）。
 * 覆盖：换会话草稿存取与提醒行（A5）／自由输入发送经 transport 拼装并发出、回执落线程并记
 * chat.send.settled（消息只记长度，不落正文）。渲染桩 adapter/transport，不接任何真实页面。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatWidget } from '../src/chat-widget/ChatWidget';
import type { ChatHostAdapter, ChatMessage, StreamHandlers } from '../src/chat-widget/types';

let info: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  info = vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

function inputEl(): HTMLTextAreaElement {
  return screen.getByLabelText('消息输入') as HTMLTextAreaElement;
}

describe('ChatWidget 换会话草稿（§6）', () => {
  it('切走留草稿→提醒行；切回→恢复输入', () => {
    let session = 'host:obj:1';
    const adapter: ChatHostAdapter = {
      hostId: 'host',
      sessionKey: () => session,
      transport: { send: () => ({ abort: () => {} }), buildCommand: (t) => ({ t }) },
      getContext: () => ({}),
      threadSource: { kind: 'local' },
    };
    const { rerender } = render(<ChatWidget adapter={adapter} />);

    fireEvent.change(inputEl(), { target: { value: '未发完的草稿' } });
    session = 'host:obj:2';
    rerender(<ChatWidget adapter={adapter} />);

    expect(screen.getByText(/上一会话有未发送的草稿/)).toBeInTheDocument();
    expect(inputEl().value).toBe(''); // 新会话输入为空

    session = 'host:obj:1';
    rerender(<ChatWidget adapter={adapter} />);
    expect(inputEl().value).toBe('未发完的草稿'); // 切回恢复
  });
});

describe('ChatWidget 发送（§3.2/§8）', () => {
  it('自由输入发送：经 transport 拼装并发出，回执落线程，记 chat.send.settled（只记长度）', () => {
    const buildCommand = vi.fn((text: string, ctx: Record<string, unknown>) => ({ text, ctx }));
    const send = vi.fn((_cmd: unknown, handlers: StreamHandlers) => {
      handlers.onStage?.('accepted');
      handlers.onResult({ ok: true });
      return { abort: () => {} };
    });
    const appended: ChatMessage[] = [
      { id: 'a1', role: 'assistant', at: '2026-07-17T00:00:00Z', status: 'settled', parts: [{ type: 'text', text: 'AI 的回复' }] },
    ];
    const adapter: ChatHostAdapter = {
      hostId: 'host',
      sessionKey: () => 'host:obj:1',
      transport: { send, buildCommand },
      getContext: () => ({}),
      threadSource: { kind: 'local', appendResult: () => appended },
    };
    render(<ChatWidget adapter={adapter} />);

    fireEvent.change(inputEl(), { target: { value: '你好世界' } });
    fireEvent.click(screen.getByLabelText('发送'));

    expect(buildCommand).toHaveBeenCalledWith('你好世界', expect.any(Object), undefined);
    expect(send).toHaveBeenCalledTimes(1);
    expect(inputEl().value).toBe(''); // 发送后清空
    expect(screen.getByText('AI 的回复')).toBeInTheDocument(); // local 源回填 assistant 行

    const settled = info.mock.calls
      .map((c: unknown[]) => String(c[0]))
      .find((s: string) => s.includes('chat.send.settled'));
    expect(settled).toBeTruthy();
    expect(settled).toContain('"message_length":4'); // 只记长度
    expect(settled).not.toContain('你好世界'); // 不落正文
  });

  it('buildCommand 抛出：会话在途被清、错误行落线程、无未处理拒绝', async () => {
    const send = vi.fn();
    const buildCommand = vi.fn(() => {
      throw new Error('上下文缺字段');
    });
    const adapter: ChatHostAdapter = {
      hostId: 'host',
      sessionKey: () => 'host:obj:1',
      transport: { send, buildCommand },
      getContext: () => ({}),
      threadSource: { kind: 'local' },
    };

    const rejections: unknown[] = [];
    const onRejection = (e: PromiseRejectionEvent) => {
      e.preventDefault();
      rejections.push(e.reason);
    };
    window.addEventListener('unhandledrejection', onRejection);
    try {
      render(<ChatWidget adapter={adapter} />);

      fireEvent.change(inputEl(), { target: { value: '触发拼装失败' } });
      fireEvent.click(screen.getByLabelText('发送'));

      // buildCommand 抛出后 send 从不被调用（失败发生在拼装阶段）。
      expect(buildCommand).toHaveBeenCalledTimes(1);
      expect(send).not.toHaveBeenCalled();
      // 对称收尾：错误行入线程、会话在途指示灯熄灭（clearSession 已执行）。
      expect(screen.getByText(/发送失败：上下文缺字段/)).toBeInTheDocument();
      expect(screen.queryByText('AI 正在回复本会话…')).not.toBeInTheDocument();

      // 微任务与宏任务冲洗后，自由输入路径的 void runSend 未遗留未处理拒绝。
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
      expect(rejections).toEqual([]);
    } finally {
      window.removeEventListener('unhandledrejection', onRejection);
    }
  });

  it('发送失败：错误行入线程，不静默消失', () => {
    const send = vi.fn((_cmd: unknown, handlers: StreamHandlers) => {
      handlers.onError(new Error('连接中断'));
      return { abort: () => {} };
    });
    const adapter: ChatHostAdapter = {
      hostId: 'host',
      sessionKey: () => 'host:obj:1',
      transport: { send, buildCommand: (t) => ({ t }) },
      getContext: () => ({}),
      threadSource: { kind: 'local' },
    };
    render(<ChatWidget adapter={adapter} />);

    fireEvent.change(inputEl(), { target: { value: '触发失败' } });
    fireEvent.click(screen.getByLabelText('发送'));
    expect(screen.getByText(/发送失败：连接中断/)).toBeInTheDocument();
  });
});
