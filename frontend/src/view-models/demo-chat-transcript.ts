/**
 * 演示留痕 → 界面消息映射（AI 对话演示简化方案 2026-07-18 §2.3）。
 *
 * 两类目标形态：
 *  - 知识抽取页 / 条目形成页（旧实现，区5 消息＝组件内存 ChatMsg 数组）→ TranscriptBubble
 *    （tone 由 role+kind 决定；组件按各自 ChatMsg 联合类型落地）。
 *  - 条目评审页（已迁 ChatWidget，投影 ChatMessage[]）→ ChatMessage，并与领域投影按时间序合并。
 *
 * 语气映射（role+kind → tone）单一来源在本文件，写侧 kind 契约见 backend/app/api/transcript.py。
 */
import type { ChatMessage } from '../chat-widget';
import type { ChatTranscriptRow, SourceCandidatePayload } from '../api/transcript';

// ---------------------------------------------------------------------------
// 知识抽取页 / 条目形成页：留痕行 → 气泡
// ---------------------------------------------------------------------------

export type TranscriptBubbleTone = 'user' | 'cmd' | 'ai' | 'sys-ok' | 'sys-warn';

export interface TranscriptBubble {
  tone: TranscriptBubbleTone;
  text: string;
  at: string;
}

/** role+kind → 气泡语气；assistant free_text=解释（'ai'，仅条目形成页产出）。 */
export function transcriptRowToBubble(row: ChatTranscriptRow): TranscriptBubble {
  const text = row.content?.text ?? '';
  const at = row.created_at;
  if (row.role === 'user') {
    return { tone: row.kind === 'command' ? 'cmd' : 'user', text, at };
  }
  // assistant
  if (row.kind === 'failure_note') {
    return { tone: 'sys-warn', text, at };
  }
  if (row.kind === 'free_text') {
    return { tone: 'ai', text, at };
  }
  // command_result / source_candidates（旧两页不产 source_candidates，command_result 即执行回执）
  return { tone: 'sys-ok', text, at };
}

// ---------------------------------------------------------------------------
// 条目评审页：留痕行 → ChatMessage，并与领域投影合并
// ---------------------------------------------------------------------------

/**
 * 逐字段剥换行与行首块级标记（F10 注入面）：MarkdownPreview 逐行解析，某行以 `|` 起判为表格、
 * 出现 ``` 开代码围栏会吞掉后续整段。候选内容是模型抽取的需求原文（可能含表格/围栏），
 * 先把换行折成空格（消除后续行首），再剥掉行首块级标记，保证候选只占一行、安全内联。
 */
function inlineField(s: string | null | undefined): string {
  if (!s) return '';
  return s.replace(/[\r\n]+/g, ' ').replace(/^[\s|>#`*-]+/, '').trim();
}

function candidatesMarkdown(text: string, candidates: SourceCandidatePayload[]): string {
  const head = inlineField(text) || '已为该条目找到候选来源：';
  // 先按 rank 升序排（与出口卡 buildSourceCandidateCards 同口径：(a.rank||0)-(b.rank||0)），
  // 令对话区与出口卡对同一候选集给出同一顺序（F10）；rank 0=未排名，序号退回排序后位次。
  const ordered = [...candidates].sort((a, b) => (a.rank || 0) - (b.rank || 0));
  const lines = ordered.map((c, i) => {
    const type = c.element_type ? `［${inlineField(c.element_type)}］` : '';
    const reason = c.reason ? ` —— ${inlineField(c.reason)}` : '';
    const quote = c.source_quote ? `（引文：${inlineField(c.source_quote)}）` : '';
    return `${c.rank || i + 1}. ${type}${inlineField(c.content)}${reason}${quote}`;
  });
  return [head, ...lines].join('\n');
}

/** 单条留痕行 → 评审页 ChatMessage（id 冠 `tx-` 前缀防与投影 id 冲突）。 */
export function transcriptRowToReviewMessage(row: ChatTranscriptRow): ChatMessage {
  const id = `tx-${row.id}`;
  const at = row.created_at;
  const text = row.content?.text ?? '';
  if (row.role === 'user') {
    return { id, role: 'user', at, status: 'settled', parts: [{ type: 'text', text }] };
  }
  if (row.kind === 'source_candidates') {
    const md = candidatesMarkdown(text, row.content?.candidates ?? []);
    return { id, role: 'assistant', at, status: 'settled', parts: [{ type: 'markdown', text: md }] };
  }
  if (row.kind === 'failure_note') {
    return { id, role: 'assistant', at, status: 'settled', parts: [{ type: 'text', text: `⚠ ${text}` }] };
  }
  // command_result
  return { id, role: 'assistant', at, status: 'settled', parts: [{ type: 'markdown', text }] };
}

/**
 * 领域投影线程 ⊕ 留痕行，按 created_at 升序合并。
 *
 * 两路内容天然无重叠：投影只重放 explanation/draft/verdict/receipt，留痕只存 COMMAND 交换
 * 与失败回执（写点边界见方案 §2.2）——故直接并接排序，无需去重。
 */
export function mergeReviewThread(projected: ChatMessage[], rows: ChatTranscriptRow[]): ChatMessage[] {
  const merged = [...projected, ...rows.map(transcriptRowToReviewMessage)];
  return merged.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
}
