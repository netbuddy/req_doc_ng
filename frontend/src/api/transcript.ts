/**
 * 演示留痕读端点（AI 对话演示简化方案 2026-07-18 §2.3）。
 *
 * 三个对话页区5 消息的服务端留痕，供刷新后水合。读模型手写（未走 generated/schema：
 * codegen 指向主栈 8000 端口，槽内不可用且会重刷万行文件；本读模型字段少、稳定，手写更小）。
 */
import { apiGet } from './client';

export type TranscriptChannel = 'analysis' | 'formation' | 'review';

export interface SourceCandidatePayload {
  element_ref: string;
  element_type?: string;
  content?: string;
  source_quote?: string | null;
  reason?: string;
  rank?: number;
}

export interface ChatTranscriptContent {
  text?: string;
  candidates?: SourceCandidatePayload[];
}

export interface ChatTranscriptRow {
  id: string;
  channel: string;
  context_ref: string;
  role: string; // user | assistant | system
  kind: string; // free_text | command | command_result | source_candidates | failure_note
  content: ChatTranscriptContent;
  created_at: string; // ISO，排序键
}

export interface ChatTranscriptRead {
  rows: ChatTranscriptRow[];
}

/** 按 (channel, context_ref) 拉取留痕行（升序）。 */
export function fetchChatTranscript(
  projectId: string,
  channel: TranscriptChannel,
  contextRef: string,
): Promise<ChatTranscriptRead> {
  const q = new URLSearchParams({ channel, context_ref: contextRef });
  return apiGet<ChatTranscriptRead>(
    `/projects/${encodeURIComponent(projectId)}/chat-transcript?${q.toString()}`,
  );
}
