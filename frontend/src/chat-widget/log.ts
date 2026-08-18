/**
 * 统一 AI 对话控件 · 结构化日志（组件 chat-widget，工作包 01 篇 §8）。
 *
 * 内容纪律（README §3 条 7）：会话键只记哈希、消息只记长度/哈希，原文（prompt/模型响应/用户输入）
 * 永不入日志；token 与密钥不进任何输出。四个核心事件在此集中定义，字段与 §8 事件表逐行对齐。
 */
export type CwLevel = 'INFO' | 'WARN' | 'ERROR';

export function cwLog(level: CwLevel, event: string, body: Record<string, unknown> = {}): void {
  const line = { ts: new Date().toISOString(), level, component: 'chat-widget', event, ...body };
  const text = JSON.stringify(line);
  if (level === 'ERROR') console.error(text);
  else if (level === 'WARN') console.warn(text);
  else console.info(text);
}

/** 会话键摘要（脱敏：不落对象原始引用全文；FNV 式 31 进制哈希，仅作日志维度）。 */
export function hashSessionKey(sessionKey: string): string {
  let h = 0;
  for (let i = 0; i < sessionKey.length; i += 1) {
    h = (h * 31 + sessionKey.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(16);
}

// ----- §8 四事件（时机与必含字段见事件表） -----

/** 会话切换完成：sessionKey 哈希、总耗时、投影耗时、数据源。 */
export function logSessionSwitchTiming(fields: {
  sessionKey: string;
  totalMs: number;
  projectionMs: number | null;
  source: 'local-projection' | 'server-fetch';
}): void {
  cwLog('INFO', 'chat.session.switch.timing', {
    session_hash: hashSessionKey(fields.sessionKey),
    total_ms: Math.round(fields.totalMs * 100) / 100,
    projection_ms: fields.projectionMs,
    source: fields.source,
  });
}

/** 动作分发：动作 kind/name、sessionKey 哈希。 */
export function logActionDispatch(fields: {
  kind: string;
  name?: string | null;
  sessionKey: string;
}): void {
  cwLog('INFO', 'chat.action.dispatch', {
    action_kind: fields.kind,
    action_name: fields.name ?? null,
    session_hash: hashSessionKey(fields.sessionKey),
  });
}

/** 动作终态：ok、耗时、followup 声明。 */
export function logActionSettled(fields: {
  ok: boolean;
  durationMs: number;
  followup: 'done' | 'pending-followup';
}): void {
  cwLog(fields.ok ? 'INFO' : 'WARN', 'chat.action.settled', {
    ok: fields.ok,
    duration_ms: Math.round(fields.durationMs),
    followup: fields.followup,
  });
}

/** 对话回执落线程：耗时、是否落在当前显示会话、消息长度（不落原文）。 */
export function logSendSettled(fields: {
  sessionKey: string;
  durationMs: number;
  landedOnCurrent: boolean;
  messageLength: number;
}): void {
  cwLog('INFO', 'chat.send.settled', {
    session_hash: hashSessionKey(fields.sessionKey),
    duration_ms: Math.round(fields.durationMs),
    landed_on_current: fields.landedOnCurrent,
    message_length: fields.messageLength,
  });
}
