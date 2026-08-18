/**
 * 统一 AI 对话控件 · 按会话草稿存取（01 篇 §6，验收 A5 一部分）。
 * 空白草稿等价删除（避免误报「有未发送内容」）；length 取去空白字数（提醒行用）。
 */
import { describe, expect, it } from 'vitest';
import { DraftStore } from '../src/chat-widget/drafts';

describe('DraftStore', () => {
  it('存取往返：按 sessionKey 隔离', () => {
    const store = new DraftStore();
    store.set('a:1', 'draft-A');
    store.set('a:2', 'draft-B');
    expect(store.get('a:1')).toBe('draft-A');
    expect(store.get('a:2')).toBe('draft-B');
    expect(store.get('a:3')).toBe('');
  });

  it('空白草稿等价删除：has=false、length=0', () => {
    const store = new DraftStore();
    store.set('k', '   \n  ');
    expect(store.has('k')).toBe(false);
    expect(store.get('k')).toBe('');
  });

  it('has/length 取去空白后的字数', () => {
    const store = new DraftStore();
    store.set('k', '  你好世界  ');
    expect(store.has('k')).toBe(true);
    expect(store.length('k')).toBe(4);
  });

  it('clear 移除草稿', () => {
    const store = new DraftStore();
    store.set('k', 'x');
    store.clear('k');
    expect(store.has('k')).toBe(false);
  });
});
