/**
 * 统一 AI 对话控件 · 控件本体（工作包 01 篇，业务无关）。
 *
 * 组成：会话头（页面身份/场景徽标）＋线程视图＋链路回执条＋快捷命令药丸＋输入区。控件核心零页面感知
 * （README §3 条 2）：只认识 ChatHostAdapter，页面差异全经适配器注入。视觉照高保真原型主视图帧复刻
 * （01 篇 §0）。样式全 rem、类名 cw- 前缀、按 styles.css 令牌渲染（五主题＋暗色自动适配）。
 *
 * 线程源三态（§3.3）：projected＝页面投影（settle 走重投影）；local＝控件内存队列（乐观行＋结果回填，
 * 刷新即丢，过渡态）；server＝P4 接入，本卡仅占位。回执归属发送会话（切走不显示，§5）。
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useActionDispatch } from './action-dispatch';
import './chat-widget.css';
import { DraftStore } from './drafts';
import { useInflight } from './inflight';
import { logSendSettled, logSessionSwitchTiming, cwLog, hashSessionKey } from './log';
import { ThreadView } from './thread-view';
import { ChatTraceRail, useDialogueTrace } from './trace-rail';
import type {
  ChatHostAdapter,
  ChatMessage,
  QuickCommand,
  QuoteBlock,
  SubmitAction,
  UrlAction,
} from './types';

export interface ChatWidgetProps {
  adapter: ChatHostAdapter;
}

export function ChatWidget({ adapter }: ChatWidgetProps): ReactNode {
  const sessionKey = adapter.sessionKey();
  const adapterRef = useRef(adapter);
  adapterRef.current = adapter;

  const [input, setInput] = useState('');
  const inputRef = useRef(input);
  inputRef.current = input;

  const [switchNote, setSwitchNote] = useState<string | null>(null);
  const [showAllCommands, setShowAllCommands] = useState(false);

  const draftsRef = useRef(new DraftStore());
  const prevSessionRef = useRef<string | null>(null);
  const switchStartRef = useRef<number | null>(null);
  const projectionMsRef = useRef<number | null>(null);
  const localIdRef = useRef(0);

  // 线程数据：local 源的每会话内存队列 + 乐观/失败叠加层（layered on base）。
  const localThreadsRef = useRef<Map<string, ChatMessage[]>>(new Map());
  const overlaysRef = useRef<Map<string, ChatMessage[]>>(new Map());
  const [revision, setRevision] = useState(0);
  const bump = useCallback(() => setRevision((n) => n + 1), []);

  const inflight = useInflight();
  const { trace: traceValue, begin: traceBegin, stage: traceStage, finish: traceFinish } = useDialogueTrace();
  const [traceOwner, setTraceOwner] = useState<string | null>(null);
  const handleRef = useRef<{ abort: () => void } | null>(null);

  // ---- 线程基线投影（render 阶段计算 projected；测投影耗时供切换计时日志） ----
  const source = adapter.threadSource;
  let baseMessages: ChatMessage[];
  if (source.kind === 'projected') {
    const t0 = performance.now();
    baseMessages = source.project();
    projectionMsRef.current = performance.now() - t0;
  } else if (source.kind === 'local') {
    projectionMsRef.current = 0;
    baseMessages = localThreadsRef.current.get(sessionKey) ?? [];
  } else {
    projectionMsRef.current = null;
    baseMessages = []; // server：P4 接入，本卡占位空线程
  }
  const overlay = overlaysRef.current.get(sessionKey) ?? [];
  const messages = overlay.length ? [...baseMessages, ...overlay] : baseMessages;
  void revision; // 订阅 ref 变更触发的重渲

  // ---- F4：投影刷新时清空该会话的终态叠加层，避免与留痕水合出的失败行双份并存 ----
  // 触发键＝投影线程 baseMessages（projected 源下＝页面 threadMessages memo，仅内容变更时换引用）。
  // 失败在 projected 源留一层常驻叠加层（:169），页面按 F1 于对话往返后重拉留痕、把失败行并入投影；
  // 此时清掉叠加层那份，单气泡收束。守卫：仅清全终态叠加层——含 pending 的在途层（乐观行、网络
  // 未归来的失败层）一律保留，故失败行经水合出现前，叠加层照常显示，不破坏 :204-206 的正常清理路径。
  useEffect(() => {
    if (source.kind !== 'projected') return;
    const ov = overlaysRef.current.get(sessionKey);
    if (!ov || ov.length === 0) return;
    if (ov.some((m) => m.status === 'pending')) return; // 在途：保留，等待归来
    overlaysRef.current.delete(sessionKey);
    bump();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseMessages, sessionKey]);

  // ---- 会话切换起点计时（仅确为切换、非初次挂载） ----
  if (prevSessionRef.current !== null && prevSessionRef.current !== sessionKey && switchStartRef.current === null) {
    switchStartRef.current = performance.now();
  }

  // ---- 换会话：存/取草稿 + 提醒行（§6）；不中止在途（回执归属发送会话，§5） ----
  useEffect(() => {
    const prev = prevSessionRef.current;
    if (prev === sessionKey) return;
    const store = draftsRef.current;
    if (prev !== null) {
      const leaving = inputRef.current;
      if (leaving.trim()) {
        store.set(prev, leaving);
        setSwitchNote(`上一会话有未发送的草稿（${store.length(prev)} 字），已保留；切回可继续。`);
      } else {
        store.clear(prev);
        setSwitchNote(null);
      }
    }
    setInput(store.get(sessionKey));
    setShowAllCommands(false);
    prevSessionRef.current = sessionKey;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);

  // ---- 切换到重绘的耗时（§8 chat.session.switch.timing） ----
  useLayoutEffect(() => {
    if (switchStartRef.current === null) return;
    const dur = performance.now() - switchStartRef.current;
    switchStartRef.current = null;
    logSessionSwitchTiming({
      sessionKey,
      totalMs: dur,
      projectionMs: projectionMsRef.current,
      source: source.kind === 'server' ? 'server-fetch' : 'local-projection',
    });
  }, [sessionKey, source.kind]);

  useEffect(() => () => handleRef.current?.abort(), []);

  // ---- 发送核心：一次流式请求（自由输入与 submit 动作共用）。返回 ok 布尔 ----
  const runSend = useCallback(
    (text: string, extraData?: Record<string, unknown>): Promise<boolean> => {
      const a = adapterRef.current;
      const target = a.sessionKey();
      const ctx = { ...a.getContext(), ...(extraData ?? {}) };
      const quotes: QuoteBlock[] | undefined = a.quote?.available() ? a.quote.capture() : undefined;
      const optimisticId = `local-${(localIdRef.current += 1)}`;
      const optimistic: ChatMessage = {
        id: optimisticId,
        role: 'user',
        at: new Date().toISOString(),
        status: 'pending',
        parts: text ? [{ type: 'text', text }] : [],
      };
      overlaysRef.current.set(target, [...(overlaysRef.current.get(target) ?? []), optimistic]);
      inflight.markSession(target);
      traceBegin();
      setTraceOwner(target);
      bump();

      const startedAt = performance.now();

      // 失败收尾：对称撤销本会话已施加的副作用（在途标记、乐观行、回执条），乐观行按源落终态并入一条
      // 错误行。buildCommand 抛出与 transport.send 侧失败共用同一收尾，避免任一失败路径半途泄漏。
      const finalizeFail = (message: string) => {
        traceFinish('failed');
        inflight.clearSession(target);
        const failedUser: ChatMessage = { ...optimistic, status: 'failed' };
        const errorRow: ChatMessage = {
          id: `${optimisticId}-err`,
          role: 'system',
          at: new Date().toISOString(),
          status: 'failed',
          parts: [{ type: 'text', text: `发送失败：${message}` }],
        };
        if (source.kind === 'local') {
          const base = localThreadsRef.current.get(target) ?? [];
          localThreadsRef.current.set(target, [...base, failedUser, errorRow]);
          overlaysRef.current.delete(target);
        } else {
          overlaysRef.current.set(target, [failedUser, errorRow]);
        }
        cwLog('ERROR', 'chat.send.failed', { session_hash: hashSessionKey(target), text_len: text.length });
        bump();
      };

      let command: unknown;
      try {
        command = a.transport.buildCommand(text, ctx, quotes);
      } catch (error) {
        // buildCommand 抛出：send 尚未调用，此处对称收尾后以 ok=false 收束，不向上抛未处理拒绝
        // （自由输入路径 void runSend 因此无 unhandledrejection；submit 路径按钮相位仍落 settled-error）。
        finalizeFail(error instanceof Error && error.message ? error.message : '发送准备失败');
        return Promise.resolve(false);
      }

      return new Promise<boolean>((resolve) => {
        const finishFail = (message: string) => {
          finalizeFail(message);
          resolve(false);
        };

        handleRef.current = a.transport.send(command, {
          onStage: (stage) => traceStage(stage),
          onResult: (result) => {
            traceFinish('done');
            inflight.clearSession(target);
            if (source.kind === 'local') {
              const base = localThreadsRef.current.get(target) ?? [];
              const settledUser: ChatMessage = { ...optimistic, status: 'settled' };
              const src = adapterRef.current.threadSource;
              const appended = src.kind === 'local' ? src.appendResult?.(result) ?? [] : [];
              localThreadsRef.current.set(target, [...base, settledUser, ...appended]);
              overlaysRef.current.delete(target);
            } else {
              // projected：清乐观行，通知页面刷新工作区并重投影（settle 由服务端回读派生）。
              overlaysRef.current.delete(target);
              adapterRef.current.onThreadEvent?.({ kind: 'workspace-updated' });
            }
            logSendSettled({
              sessionKey: target,
              durationMs: performance.now() - startedAt,
              landedOnCurrent: adapterRef.current.sessionKey() === target,
              messageLength: text.length,
            });
            bump();
            resolve(true);
          },
          onError: (error) => finishFail(error instanceof Error && error.message ? error.message : '连接中断'),
        });
      });
    },
    [bump, inflight, source.kind, traceBegin, traceStage, traceFinish],
  );

  // ---- 动作分发器（§4）：submit/url/component 出口注入，host 走 adapter.actions ----
  const notifyError = useCallback((text: string) => setSwitchNote(text), []);
  const onSubmit = useCallback((action: SubmitAction) => runSend('', action.data), [runSend]);
  const onUrl = useCallback((action: UrlAction) => {
    if (action.external) window.open(action.href, '_blank', 'noopener');
    else window.location.assign(action.href);
  }, []);
  const onComponent = useCallback(
    (name: string) => cwLog('INFO', 'chat.component.open', { name }),
    [],
  );
  const actionApi = useActionDispatch({ adapter, onSubmit, onUrl, onComponent, notifyError });

  const send = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    setInput('');
    draftsRef.current.clear(sessionKey);
    setSwitchNote(null);
    void runSend(text);
  }, [input, runSend, sessionKey]);

  // ---- 快捷命令：按 priority 降序稳定排序，容量裁剪，其余折入「更多」（§3.1 / AC-P3-05 机制） ----
  const commands = adapter.quickCommands ?? [];
  const slots = adapter.capabilities?.quickCommandSlots;
  const sortedCommands = useMemo(() => stableByPriority(commands), [commands]);
  const shownCommands =
    slots === undefined || showAllCommands ? sortedCommands : sortedCommands.slice(0, Math.max(0, slots));
  const hiddenCount = sortedCommands.length - shownCommands.length;

  const onPillClick = useCallback(
    (cmd: QuickCommand) => {
      const prefilled = cmd.prefill(adapterRef.current.getContext());
      setInput(prefilled);
    },
    [],
  );

  const sessionInflight = inflight.hasSession(sessionKey);
  // 人读标签优先（原型注 6：携带内容对用户可见、可核对）；缺省回退命令体键名。
  const contextChips = adapter.contextChips?.() ?? Object.keys(adapter.getContext());
  const quoteChips = adapter.quote?.available() ? adapter.quote.capture() : [];
  const label = adapter.sessionLabel?.() ?? adapter.hostId;

  return (
    <div className="cw-root" aria-label="统一 AI 对话控件">
      <header className="cw-head">
        <span className="cw-logo" aria-hidden>
          <SparkIcon />
        </span>
        <span className="cw-head-title">AI 助手</span>
        <span className="cw-scene" title={sessionKey}>
          {label}
        </span>
        <div className="cw-tools">
          <button className="cw-tool" type="button" title="会话列表" aria-label="会话列表">
            <ClockIcon />
          </button>
          <button className="cw-tool" type="button" title="新建会话" aria-label="新建会话">
            <PlusIcon />
          </button>
        </div>
      </header>

      <div className="cw-thread" ref={useAutoScroll(messages.length, sessionKey)}>
        <ThreadView
          messages={messages}
          actions={actionApi}
          capabilities={adapter.capabilities}
          sessionInflight={sessionInflight}
        />
      </div>

      {traceOwner === sessionKey ? <ChatTraceRail trace={traceValue} /> : null}

      {shownCommands.length > 0 ? (
        <div className="cw-quick">
          <span className="cw-quick-label">快捷命令</span>
          {shownCommands.map((cmd) => (
            <button className="cw-pill" type="button" key={cmd.command} onClick={() => onPillClick(cmd)}>
              {cmd.label}
            </button>
          ))}
          {hiddenCount > 0 ? (
            <button className="cw-pill cw-pill--more" type="button" onClick={() => setShowAllCommands(true)}>
              更多 +{hiddenCount}
            </button>
          ) : null}
        </div>
      ) : null}

      <footer className="cw-foot">
        {switchNote ? (
          <p className="cw-note" role="status">
            {switchNote}
          </p>
        ) : null}
        {contextChips.length > 0 || quoteChips.length > 0 ? (
          <div className="cw-ctx-row">
            <span className="cw-ctx-hint">发送时携带：</span>
            {contextChips.map((k, i) => (
              <span className="cw-ctx-tag" key={`${i}-${k}`}>
                <span aria-hidden className={`cw-ctx-sw cw-ctx-sw--${i % 3}`} />
                {k}
              </span>
            ))}
            {quoteChips.map((q) => (
              <span className="cw-ctx-tag cw-ctx-tag--quote" key={q.id}>
                <span aria-hidden className="cw-ctx-sw" />
                {q.label}
              </span>
            ))}
          </div>
        ) : null}
        <div className="cw-compose">
          <textarea
            className="cw-input"
            aria-label="消息输入"
            rows={1}
            value={input}
            placeholder="输入内容，或键入 / 选择命令……"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button
            className="cw-send"
            type="button"
            aria-label="发送"
            disabled={!input.trim()}
            onClick={send}
          >
            <SendIcon />
          </button>
        </div>
      </footer>
    </div>
  );
}

/** 线程底部自动滚动（换会话或新消息时）；幂等赋值，无需守卫。 */
function useAutoScroll(msgCount: number, sessionKey: string) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [msgCount, sessionKey]);
  return ref;
}

/** 按 priority 降序稳定排序（同优先级保持原序，AC-P3-05）。 */
function stableByPriority(commands: QuickCommand[]): QuickCommand[] {
  return commands
    .map((cmd, index) => ({ cmd, index }))
    .sort((a, b) => b.cmd.priority - a.cmd.priority || a.index - b.index)
    .map((x) => x.cmd);
}

// ---- 内联图标（照原型主视图帧；描边用 currentColor 随主题令牌） ----
function SparkIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 1.5l1.8 4.7L14.5 8l-4.7 1.8L8 14.5 6.2 9.8 1.5 8l4.7-1.8z" />
    </svg>
  );
}
function ClockIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
      <circle cx="8" cy="8" r="6.2" />
      <path d="M8 4.6V8l2.3 1.5" />
    </svg>
  );
}
function PlusIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="M8 3.2v9.6M3.2 8h9.6" />
    </svg>
  );
}
function SendIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2L7.3 8.7M14 2L9.7 14 7.3 8.7 2 6.3z" />
    </svg>
  );
}
