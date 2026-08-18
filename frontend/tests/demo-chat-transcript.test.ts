/**
 * 演示留痕映射器单测（AI 对话演示简化方案 2026-07-18 §2.3）。
 *
 * 断言重点：role+kind → 气泡语气映射；评审页留痕行 → ChatMessage 分部；
 * 领域投影 ⊕ 留痕行按 created_at 升序合并且无重叠去重需求。
 */
import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '../src/chat-widget';
import type { ChatTranscriptRow } from '../src/api/transcript';
import {
  mergeReviewThread,
  transcriptRowToBubble,
  transcriptRowToReviewMessage,
} from '../src/view-models/demo-chat-transcript';

function row(partial: Partial<ChatTranscriptRow>): ChatTranscriptRow {
  return {
    id: 'r1', channel: 'review', context_ref: 'c1', role: 'user', kind: 'free_text',
    content: { text: '文本' }, created_at: '2026-07-18T10:00:00+00:00', ...partial,
  };
}

describe('transcriptRowToBubble（知识抽取/条目形成页语气映射）', () => {
  it('user + command → cmd', () => {
    expect(transcriptRowToBubble(row({ role: 'user', kind: 'command', content: { text: '/诊断' } })))
      .toMatchObject({ tone: 'cmd', text: '/诊断' });
  });
  it('user + free_text → user', () => {
    expect(transcriptRowToBubble(row({ role: 'user', kind: 'free_text' })).tone).toBe('user');
  });
  it('assistant + command_result → sys-ok', () => {
    expect(transcriptRowToBubble(row({ role: 'assistant', kind: 'command_result' })).tone).toBe('sys-ok');
  });
  it('assistant + free_text → ai（解释）', () => {
    expect(transcriptRowToBubble(row({ role: 'assistant', kind: 'free_text' })).tone).toBe('ai');
  });
  it('assistant + failure_note → sys-warn', () => {
    expect(transcriptRowToBubble(row({ role: 'assistant', kind: 'failure_note' })).tone).toBe('sys-warn');
  });
});

describe('transcriptRowToReviewMessage（评审页 ChatMessage 分部）', () => {
  it('user 行 → text 分部，id 冠 tx- 前缀', () => {
    const m = transcriptRowToReviewMessage(row({ id: 'abc', role: 'user', content: { text: '/采纳结论' } }));
    expect(m).toMatchObject({ id: 'tx-abc', role: 'user', status: 'settled' });
    expect(m.parts[0]).toEqual({ type: 'text', text: '/采纳结论' });
  });
  it('command_result → assistant markdown 分部', () => {
    const m = transcriptRowToReviewMessage(row({ role: 'assistant', kind: 'command_result', content: { text: '已发起诊断。' } }));
    expect(m.role).toBe('assistant');
    expect(m.parts[0]).toEqual({ type: 'markdown', text: '已发起诊断。' });
  });
  it('failure_note → assistant text 分部带 ⚠ 前缀', () => {
    const m = transcriptRowToReviewMessage(row({ role: 'assistant', kind: 'failure_note', content: { text: '命令解释能力未装配' } }));
    expect(m.parts[0]).toEqual({ type: 'text', text: '⚠ 命令解释能力未装配' });
  });
  it('source_candidates → 候选清单降级为 markdown 文本', () => {
    const m = transcriptRowToReviewMessage(row({
      role: 'assistant', kind: 'source_candidates',
      content: {
        text: '已找到候选来源。',
        candidates: [{ element_ref: 'E-1', element_type: '功能', content: '库存要素', reason: '相关', rank: 1 }],
      },
    }));
    expect(m.parts[0].type).toBe('markdown');
    const md = (m.parts[0] as { text: string }).text;
    expect(md).toContain('已找到候选来源。');
    expect(md).toContain('库存要素');
    expect(md).toContain('相关');
  });
});

// candidatesMarkdown 不导出，经 source_candidates 分支的 transcriptRowToReviewMessage 间接验证。
function candidatesMd(candidates: Array<Record<string, unknown>>): string {
  const m = transcriptRowToReviewMessage(row({
    role: 'assistant', kind: 'source_candidates',
    content: { text: '候选：', candidates: candidates as never },
  }));
  return (m.parts[0] as { text: string }).text;
}

describe('candidatesMarkdown（F10：与出口卡同口径排序 + 换行/块级标记剥除）', () => {
  it('乱序 rank 输入 → 按 rank 升序输出（与 buildSourceCandidateCards 同口径）', () => {
    const md = candidatesMd([
      { element_ref: 'E-3', content: '候选丙', rank: 3 },
      { element_ref: 'E-1', content: '候选甲', rank: 1 },
      { element_ref: 'E-2', content: '候选乙', rank: 2 },
    ]);
    expect(md.indexOf('候选甲')).toBeLessThan(md.indexOf('候选乙'));
    expect(md.indexOf('候选乙')).toBeLessThan(md.indexOf('候选丙'));
  });

  it('rank 0（未排名）与出口卡 (a.rank||0)-(b.rank||0) 同口径落最前', () => {
    // 出口卡 buildSourceCandidateCards 用 (a.rank||0)-(b.rank||0)：rank 0 视作 0，排在 rank 1 之前。
    // 对齐即消除「同一候选集两处顺序不同」的 F10 缺陷（对齐是目的，非「rank 0 落尾」）。
    const md = candidatesMd([
      { element_ref: 'E-1', content: '有排名', rank: 1 },
      { element_ref: 'E-0', content: '未排名', rank: 0 },
    ]);
    expect(md.indexOf('未排名')).toBeLessThan(md.indexOf('有排名'));
  });

  it('候选 content/source_quote 内换行折成空格、行首块级标记剥除，不破坏 markdown 渲染', () => {
    const md = candidatesMd([
      {
        element_ref: 'E-1',
        content: '| 表格首列 | 次列 |',              // 行首 | 会被 MarkdownPreview 判为表格
        source_quote: '引文一\n```\ncode 围栏',        // 换行 + 代码围栏会吞后续整段
        reason: '原因',
        rank: 1,
      },
    ]);
    // 逐行检查：无任何行以 | 或 ``` 起（否则触发表格/围栏块级渲染）
    for (const line of md.split('\n')) {
      const t = line.trim();
      expect(t.startsWith('|')).toBe(false);
      expect(t.startsWith('```')).toBe(false);
    }
    // content 行首的 | 被剥除
    expect(md).toContain('表格首列');
    // source_quote 换行折成空格（原 '引文一\n```\ncode 围栏' → 单行）
    expect(md).toContain('引文一');
    expect(md).not.toContain('引文一\n');
  });
});

describe('mergeReviewThread（投影 ⊕ 留痕，按 created_at 升序）', () => {
  it('命令排在其副作用卡之前（received_at 保序）', () => {
    const projected: ChatMessage[] = [
      { id: 'verdict-1', role: 'assistant', at: '2026-07-18T10:00:05+00:00', status: 'settled',
        parts: [{ type: 'text', text: '结论卡' }] },
    ];
    const rows: ChatTranscriptRow[] = [
      row({ id: 'u', role: 'user', kind: 'command', content: { text: '/采纳结论' }, created_at: '2026-07-18T10:00:00+00:00' }),
      row({ id: 'a', role: 'assistant', kind: 'command_result', content: { text: '已采纳。' }, created_at: '2026-07-18T10:00:09+00:00' }),
    ];
    const merged = mergeReviewThread(projected, rows);
    expect(merged.map((m) => m.id)).toEqual(['tx-u', 'verdict-1', 'tx-a']);
  });
  it('空留痕行时原样返回投影', () => {
    const projected: ChatMessage[] = [
      { id: 'v', role: 'assistant', at: '2026-07-18T10:00:00+00:00', status: 'settled', parts: [] },
    ];
    expect(mergeReviewThread(projected, [])).toEqual(projected);
  });
});
