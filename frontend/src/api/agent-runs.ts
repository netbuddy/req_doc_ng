import { API_BASE, apiGet } from './client';
import type { components } from './generated/schema';

export type AgentRunRead = components['schemas']['AgentRunRead'];
export type AgentRunEventRead = components['schemas']['AgentRunEventRead'];

export interface AgentRunEventMessage {
  event?: string;
  status?: string;
  error?: string;
  // 终态帧（SSE Redis 推送 / DB 轮询降级）内联的最终结论；按 AgentRun.kind 为
  // IntakeResultRead 或 ElementWorkspaceRead，消费方各自收窄；后端结论装配失败时为 null。
  result?: unknown;
}

export interface AgentRunSubscription {
  close: () => void;
}

function runPath(runId: string): string {
  return `/agent-runs/${encodeURIComponent(runId)}`;
}

export const agentRunApi = {
  get(runId: string): Promise<AgentRunRead> {
    return apiGet<AgentRunRead>(runPath(runId));
  },

  subscribe(
    runId: string,
    onMessage: (message: AgentRunEventMessage) => void,
    onError?: () => void,
  ): AgentRunSubscription {
    if (typeof EventSource === 'undefined') {
      onError?.();
      return {
        close: () => {},
      };
    }

    const source = new EventSource(`${API_BASE}${runPath(runId)}/events`);

    const handleMessage = (event: MessageEvent<string>, eventName?: string) => {
      try {
        const message = JSON.parse(event.data) as AgentRunEventMessage;
        onMessage(eventName && !message.event ? { ...message, event: eventName } : message);
      } catch {
        onMessage({ event: eventName ?? String(event.data) });
      }
    };

    source.onmessage = handleMessage;
    source.addEventListener('agent_run.completed', (event) => handleMessage(event as MessageEvent<string>, 'agent_run.completed'));
    source.addEventListener('agent_run.failed', (event) => handleMessage(event as MessageEvent<string>, 'agent_run.failed'));
    source.onerror = () => {
      source.close();
      onError?.();
    };

    return {
      close: () => source.close(),
    };
  },
};
