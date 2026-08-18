/**
 * 统一 AI 对话控件 · 消息列表渲染（工作包 01 篇 §2.2）。
 *
 * 分部五型逐型渲染 + 未知型统一降级：
 *  - text     → 文本气泡（保留换行）
 *  - markdown → MarkdownPart（复用 MarkdownPreview 管线，异常退 text）
 *  - image    → <img>（max-width:100%），加载失败显 alt ＋占位框
 *  - card     → P1 期后端不出此分部、渲染器属 P5，控件用 card.fallback 文本兜底
 *  - component→ 逃生舱：查 capabilities.customCards[name] 渲染；未注册→占位「此内容需在原页面查看」
 *  - 未知 type → 折叠计数提示「N 个内容无法显示」，绝不抛错、绝不阻断整条消息（§2.2 / 00 篇裁定 4）
 *
 * P4 引入虚拟列表时只改本文件（§1 目录结构注）。
 */
import { useState, type ReactNode } from 'react';
import { MarkdownPart } from './markdown-part';
import {
  isCardPart,
  isComponentPart,
  isImagePart,
  isMarkdownPart,
  isTextPart,
  type ChatActionApi,
  type ChatMessage,
  type HostCapabilities,
  type MessagePart,
} from './types';

export interface ThreadViewProps {
  messages: ChatMessage[];
  actions: ChatActionApi;
  capabilities?: HostCapabilities;
  /** 该会话有对话请求在途：显示「AI 正在回复本会话…」（会话级在途，§5）。 */
  sessionInflight?: boolean;
}

export function ThreadView({ messages, actions, capabilities, sessionInflight = false }: ThreadViewProps): ReactNode {
  if (messages.length === 0 && !sessionInflight) {
    return <p className="cw-empty">该会话暂无消息。发送问题或采纳结论开始。</p>;
  }
  // 日期分隔芯片（原型主视图帧：「7月14日」「今天」）：相邻消息跨天时插入。
  let prevDay: string | null = null;
  return (
    <>
      {messages.map((m) => {
        const day = dayKey(m.at);
        const chip = day && day !== prevDay ? <div className="cw-date-chip">{dayLabel(m.at)}</div> : null;
        if (day) prevDay = day;
        return (
          <div className="cw-msg-group" key={m.id}>
            {chip}
            <MessageRow message={m} actions={actions} capabilities={capabilities} />
          </div>
        );
      })}
      {sessionInflight ? <p className="cw-inflight">AI 正在回复本会话…</p> : null}
    </>
  );
}

function MessageRow({
  message,
  actions,
  capabilities,
}: {
  message: ChatMessage;
  actions: ChatActionApi;
  capabilities?: HostCapabilities;
}): ReactNode {
  if (message.role === 'system') {
    return (
      <p className="cw-sys">
        <span aria-hidden className="cw-sys-dot" />
        {plainText(message)}
      </p>
    );
  }
  const isUser = message.role === 'user';
  const failed = message.status === 'failed';
  const known = message.parts.filter(isKnownPart);
  const unknownCount = message.parts.length - known.length;
  // 逃生舱纯卡消息自带头行（含时间），不再重复时间元信息行（原型 msg-meta 只挂在气泡消息下）。
  const componentOnly = known.length > 0 && known.every(isComponentPart);
  // 空分部消息（如 submit 型动作的乐观占位）不挂时间元信息行，避免渲染无气泡的裸时间戳。
  const meta = componentOnly || known.length === 0 ? null : timeLabel(message.at);
  return (
    <div className={`cw-msg ${isUser ? 'cw-msg--user' : 'cw-msg--ai'}${failed ? ' cw-msg--failed' : ''}`}>
      {known.map((part, idx) => (
        <PartView key={idx} part={part} isUser={isUser} actions={actions} capabilities={capabilities} messageId={message.id} />
      ))}
      {unknownCount > 0 ? <div className="cw-fold">{unknownCount} 个内容无法显示</div> : null}
      {meta ? (
        <span className="cw-meta">
          {meta}
          {failed ? ' · 发送失败' : message.status === 'pending' ? ' · 发送中…' : ''}
        </span>
      ) : null}
    </div>
  );
}

/** 消息时间戳的天粒度键（无效时间返回 null，不出日期芯片）。 */
function dayKey(at: string): string | null {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? null : `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** 日期芯片文案：今天／昨天／M月D日（原型形态）。 */
function dayLabel(at: string): string {
  const d = new Date(at);
  const now = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOf(now) - startOf(d)) / 86_400_000);
  if (diffDays === 0) return '今天';
  if (diffDays === 1) return '昨天';
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

/** 气泡下时间元信息（原型 msg-meta「14:32」）；无效时间返回 null。 */
function timeLabel(at: string): string | null {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function PartView({
  part,
  isUser,
  actions,
  capabilities,
  messageId,
}: {
  part: MessagePart;
  isUser: boolean;
  actions: ChatActionApi;
  capabilities?: HostCapabilities;
  messageId: string;
}): ReactNode {
  if (isTextPart(part)) {
    return <div className={`cw-bubble${isUser ? ' cw-bubble--user' : ''}`}>{part.text}</div>;
  }
  if (isMarkdownPart(part)) {
    return (
      <div className={`cw-bubble${isUser ? ' cw-bubble--user' : ''}`}>
        <MarkdownPart text={part.text} />
      </div>
    );
  }
  if (isImagePart(part)) {
    return <ImagePartView src={part.src} alt={part.alt} />;
  }
  if (isCardPart(part)) {
    // P1：card 渲染器属 P5，此处以 fallback 文本兜底（03 篇降级三层的最外层）。
    return <div className="cw-card-fallback">{part.card.fallback}</div>;
  }
  if (isComponentPart(part)) {
    const Comp = capabilities?.customCards?.[part.name];
    if (!Comp) {
      return <div className="cw-fold">此内容需在原页面查看</div>;
    }
    return <Comp props={part.props ?? {}} actions={actions} messageId={messageId} />;
  }
  // 理论不可达（known 已过滤未知型）；保底再折叠一次。
  return <div className="cw-fold">1 个内容无法显示</div>;
}

function ImagePartView({ src, alt }: { src: string; alt?: string }): ReactNode {
  const [broken, setBroken] = useState(false);
  if (broken) {
    return <div className="cw-img-broken">{alt ? `图片：${alt}` : '图片加载失败'}</div>;
  }
  return <img className="cw-img" src={src} alt={alt ?? ''} onError={() => setBroken(true)} />;
}

function isKnownPart(p: MessagePart): boolean {
  return isTextPart(p) || isMarkdownPart(p) || isImagePart(p) || isCardPart(p) || isComponentPart(p);
}

/** system 行取纯文本（system 只承载线程内提示，如「已切换方案」）。 */
function plainText(message: ChatMessage): string {
  return message.parts.map((p) => (isTextPart(p) ? p.text : isMarkdownPart(p) ? p.text : '')).join('');
}
