import { apiGet } from './client';

// GET /api/runtime-status(04A §2.1 运行态面板 / 诊断中心,基础设施只读投影)。
// 与 health.ts 同理:infra 端点手写类型,不走 generated schema。

export interface RuntimeComponentRead {
  key: string; // api | db | redis | worker | event_bus
  label: string;
  status: 'ok' | 'degraded' | 'down' | 'not_applicable' | string;
  detail?: string | null;
}

export interface RuntimeAlertRead {
  code: string;
  level: 'WARN' | 'ERROR' | string;
  summary: string;
  hint?: string | null;
}

export interface AsyncJobsSummaryRead {
  mode: 'inline' | 'queued' | string;
  queued: number | null;
  running: number | null;
  failed_recent: number | null;
  oldest_waiting_minutes: number | null;
  queue_depth: number | null;
}

export interface RecentAgentRunRead {
  run_id: string;
  kind: string; // 稳定码
  kind_label: string; // 白话名(后端单一来源,前端不再自建映射)
  status: 'queued' | 'started' | 'succeeded' | 'failed' | string;
  created_at: string; // 发起时刻 ISO
  duration_seconds: number | null; // 终态才有;非终态为 null
  reason_code: string | null; // 失败行的原因稳定码(不含 error 原文)
}

export interface DiagnosticEventRead {
  event: string;
  component: string;
  level: string;
  first_seen: string;
  last_seen: string;
  count: number;
}

export interface RuntimeStatusRead {
  status: 'normal' | 'degraded' | 'down' | string;
  alert_count: number;
  generated_at: string;
  components: RuntimeComponentRead[];
  alerts: RuntimeAlertRead[];
  async_jobs: AsyncJobsSummaryRead;
  recent_jobs?: RecentAgentRunRead[]; // 加性扩展:旧后端不返回该字段
  diagnostics: DiagnosticEventRead[];
}

export const runtimeStatusApi = {
  getRuntimeStatus(): Promise<RuntimeStatusRead> {
    return apiGet<RuntimeStatusRead>('/runtime-status');
  },
};
