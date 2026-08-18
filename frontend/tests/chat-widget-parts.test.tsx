/**
 * 统一 AI 对话控件 · 分部渲染与降级（01 篇 §2.2，验收 A3）。
 * 未知 type 折叠计数、component 未注册占位、其余分部照常渲染、整卡失败退 fallback 文本。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ThreadView } from '../src/chat-widget/thread-view';
import type { ChatActionApi, ChatMessage, ComponentPartRenderProps, HostCapabilities } from '../src/chat-widget/types';

const noopActions: ChatActionApi = {
  dispatch: vi.fn(),
  phaseOf: () => 'idle',
  isActionRegistered: () => false,
};

function CustomCard({ props }: ComponentPartRenderProps) {
  return <div>自定义卡片：{String(props.title ?? '')}</div>;
}
const capabilities: HostCapabilities = { customCards: { verdict: CustomCard } };

describe('ThreadView 分部降级', () => {
  it('未知 type：折叠计数「N 个内容无法显示」，其余分部照常渲染', () => {
    const messages: ChatMessage[] = [
      {
        id: 'm1',
        role: 'assistant',
        at: '2026-07-17T00:00:00Z',
        status: 'settled',
        parts: [
          { type: 'text', text: '一段解释文本' },
          { type: 'voice', url: 'x' } as unknown as ChatMessage['parts'][number], // 未来未知型
          { type: 'video', url: 'y' } as unknown as ChatMessage['parts'][number],
        ],
      },
    ];
    render(<ThreadView messages={messages} actions={noopActions} />);
    expect(screen.getByText('一段解释文本')).toBeInTheDocument(); // 其余分部正常
    expect(screen.getByText('2 个内容无法显示')).toBeInTheDocument(); // 折叠计数
  });

  it('component 未注册：占位「此内容需在原页面查看」，不失败', () => {
    const messages: ChatMessage[] = [
      {
        id: 'm2',
        role: 'assistant',
        at: '2026-07-17T00:00:00Z',
        status: 'settled',
        parts: [{ type: 'component', name: '未注册组件', props: {} }],
      },
    ];
    render(<ThreadView messages={messages} actions={noopActions} />);
    expect(screen.getByText('此内容需在原页面查看')).toBeInTheDocument();
  });

  it('component 已注册：渲染逃生舱组件并透传 props', () => {
    const messages: ChatMessage[] = [
      {
        id: 'm3',
        role: 'assistant',
        at: '2026-07-17T00:00:00Z',
        status: 'settled',
        parts: [{ type: 'component', name: 'verdict', props: { title: 'REQ-036' } }],
      },
    ];
    render(<ThreadView messages={messages} actions={noopActions} capabilities={capabilities} />);
    expect(screen.getByText('自定义卡片：REQ-036')).toBeInTheDocument();
  });

  it('card 分部 P1 期以 fallback 文本兜底（渲染器属 P5）', () => {
    const messages: ChatMessage[] = [
      {
        id: 'm4',
        role: 'assistant',
        at: '2026-07-17T00:00:00Z',
        status: 'settled',
        parts: [{ type: 'card', card: { card: 'v1', fallback: '修订候选：改为 2 秒内返回。' } }],
      },
    ];
    render(<ThreadView messages={messages} actions={noopActions} />);
    expect(screen.getByText('修订候选：改为 2 秒内返回。')).toBeInTheDocument();
  });

  it('空线程且无会话在途：空态提示', () => {
    render(<ThreadView messages={[]} actions={noopActions} />);
    expect(screen.getByText(/该会话暂无消息/)).toBeInTheDocument();
  });
});
