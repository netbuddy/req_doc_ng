/**
 * 控件挂载态（dev-only，A7 样式证据）。
 *
 * 本卡不接任何宿主页面（user_walkthrough waived），故用一个桩 HostAdapter 把控件挂起来，
 * 供 agent-browser 隔离会话截图、与原型主视图帧并排对比。桩 adapter/transport 只为演示视觉与
 * 交互，不代表任何真实页面契约。Vite dev 直接服务本页：/src/chat-widget/__mount__/mount.html
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ChatWidget } from '../ChatWidget';
import type {
  ChatHostAdapter,
  ChatMessage,
  DialogueTransport,
  StreamHandlers,
} from '../types';
import '../../styles.css';

// ---- 桩线程：照原型主视图帧的历史对话（文本气泡 + 卡片兜底 + markdown） ----
const seedMessages: ChatMessage[] = [
  {
    id: 's-card-1',
    role: 'assistant',
    at: '2026-07-14T16:20:00.000Z',
    status: 'settled',
    parts: [{ type: 'card', card: { card: 'v1', fallback: '修订候选（REQ-021）：导出文件命名规则统一为「项目号_日期_版本」。已采纳。' } }],
  },
  {
    id: 's-u1',
    role: 'user',
    at: '2026-07-15T14:32:00.000Z',
    status: 'settled',
    parts: [{ type: 'text', text: '/修订 REQ-036 的响应时限表述不可验证，改成可测的指标' }],
  },
  {
    id: 's-a1',
    role: 'assistant',
    at: '2026-07-15T14:32:07.000Z',
    status: 'settled',
    parts: [
      { type: 'text', text: '已起草修订候选：把「快速响应」改为可验证的时限指标。推导得到的数值已标注，需要你核实。' },
      { type: 'markdown', text: '- **目标字段**：需求表述\n- **修订依据**：材料 §2.4「性能要求」\n- **数值来源**：推导 · 待核实' },
      { type: 'card', card: { card: 'v1', fallback: '修订候选（REQ-036）：系统应在用户提交后 2 秒内返回处理结果。数值待核实。' } },
    ],
  },
];

const stubTransport: DialogueTransport = {
  buildCommand: (text, ctx) => ({ text, ctx }),
  send: (_command, handlers: StreamHandlers) => {
    // 演示流：逐帧点亮回执条到「执行」，短暂停留后回执（截图窗口内呈现活跃态）。
    const timers: number[] = [];
    const at = (ms: number, fn: () => void) => timers.push(window.setTimeout(fn, ms));
    at(0, () => handlers.onStage?.('accepted'));
    at(350, () => handlers.onStage?.('interpreting'));
    at(700, () => handlers.onStage?.('dispatching'));
    at(950, () => handlers.onStage?.('queued'));
    at(1250, () => handlers.onStage?.('running'));
    at(3200, () => handlers.onStage?.('writing'));
    at(3600, () => handlers.onResult({ ok: true }));
    return { abort: () => timers.forEach((t) => window.clearTimeout(t)) };
  },
};

const adapter: ChatHostAdapter = {
  // 桩 host：用中性演示标识（本目录零页面感知，A2 grep 不应命中任何真实页面标识）。
  hostId: 'demo-host',
  sessionKey: () => 'demo-host:batch:B-12',
  sessionLabel: () => '演示会话 · 批次 B-12',
  transport: stubTransport,
  getContext: () => ({ item_ref: 'REQ-036', selected_element_refs: ['e1', 'e2'], workspace_version: 'v7' }),
  threadSource: { kind: 'projected', project: () => seedMessages },
  capabilities: { quickCommandSlots: 5 },
  quickCommands: [
    { command: '/生成条目', label: '/生成条目', prefill: () => '/生成条目 ', priority: 90 },
    { command: '/修订', label: '/修订', prefill: () => '/修订 ', priority: 80 },
    { command: '/拆分', label: '/拆分', prefill: () => '/拆分 ', priority: 70 },
    { command: '/归并', label: '/归并', prefill: () => '/归并 ', priority: 60 },
    { command: '/复核', label: '/复核', prefill: () => '/复核 ', priority: 50 },
    { command: '/补充', label: '/补充', prefill: () => '/补充 ', priority: 40 },
    { command: '/追问', label: '/追问', prefill: () => '/追问 ', priority: 30 },
    { command: '/撤销', label: '/撤销', prefill: () => '/撤销 ', priority: 20 },
  ],
};

function MountFrame() {
  return (
    <div style={{ background: 'var(--color-bg)', minHeight: '100vh', padding: '1.5rem', display: 'flex', justifyContent: 'center' }}>
      <div style={{ width: '28rem', height: '42rem', display: 'flex' }} data-screen-label="chat-widget-mount">
        <ChatWidget adapter={adapter} />
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MountFrame />
  </StrictMode>,
);
