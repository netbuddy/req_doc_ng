/**
 * 统一 AI 对话控件 · 结构化日志四事件（01 篇 §8，验收 A5「四事件在位且不落原文」）。
 * 会话键只记哈希、消息只记长度，原文（会话对象引用、消息正文）不入日志。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  hashSessionKey,
  logActionDispatch,
  logActionSettled,
  logSendSettled,
  logSessionSwitchTiming,
} from '../src/chat-widget/log';

let info: ReturnType<typeof vi.spyOn>;
let warn: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  info = vi.spyOn(console, 'info').mockImplementation(() => {});
  warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

function lastJson(spy: ReturnType<typeof vi.spyOn>): Record<string, unknown> {
  const calls = spy.mock.calls;
  return JSON.parse(calls[calls.length - 1][0] as string) as Record<string, unknown>;
}

describe('chat-widget 日志四事件', () => {
  it('hashSessionKey：稳定且不等于原文', () => {
    const key = 'item-review:item:0e7c-secret-uuid';
    expect(hashSessionKey(key)).toBe(hashSessionKey(key));
    expect(hashSessionKey(key)).not.toContain('secret');
  });

  it('chat.session.switch.timing：记哈希/耗时/数据源，不落会话原文', () => {
    logSessionSwitchTiming({ sessionKey: 'item-review:item:secret', totalMs: 12.345, projectionMs: 5, source: 'local-projection' });
    const line = lastJson(info);
    expect(line.event).toBe('chat.session.switch.timing');
    expect(line.component).toBe('chat-widget');
    expect(line.source).toBe('local-projection');
    expect(typeof line.session_hash).toBe('string');
    expect(JSON.stringify(line)).not.toContain('secret');
  });

  it('chat.action.dispatch：记 kind/name/会话哈希', () => {
    logActionDispatch({ kind: 'host', name: 'adopt', sessionKey: 'x:y:z' });
    const line = lastJson(info);
    expect(line.event).toBe('chat.action.dispatch');
    expect(line.action_kind).toBe('host');
    expect(line.action_name).toBe('adopt');
  });

  it('chat.action.settled：ok=false 走 WARN，记耗时与 followup', () => {
    logActionSettled({ ok: false, durationMs: 100.6, followup: 'done' });
    const line = lastJson(warn);
    expect(line.event).toBe('chat.action.settled');
    expect(line.ok).toBe(false);
    expect(line.duration_ms).toBe(101);
    expect(line.followup).toBe('done');
  });

  it('chat.send.settled：只记消息长度，不落正文', () => {
    logSendSettled({ sessionKey: 'k', durationMs: 50, landedOnCurrent: false, messageLength: 12 });
    const line = lastJson(info);
    expect(line.event).toBe('chat.send.settled');
    expect(line.message_length).toBe(12);
    expect(line.landed_on_current).toBe(false);
  });
});
