import type { RuntimeStatusRead } from '../api/runtime-status';
import { formatAbsoluteTime, formatClockTime } from './time';

// 运行态徽标 + 诊断中心抽屉的展示模型(04A §2.1)。
// 纯映射:payload → 文案/色调;不发请求、不持状态。

export type RuntimeFetchPhase = 'loading' | 'error' | 'ready';

export type RuntimeBadgeTone = 'normal' | 'degraded' | 'down' | 'unknown';

export interface RuntimeBadgeVM {
  tone: RuntimeBadgeTone;
  statusText: string;
  alertCount: number;
}

export type RuntimeItemTone = 'ok' | 'warning' | 'error' | 'muted';

export interface RuntimeComponentVM {
  key: string;
  label: string;
  statusText: string;
  tone: RuntimeItemTone;
  detail: string;
}

export interface RuntimeAlertVM {
  code: string;
  levelText: string;
  tone: 'warning' | 'error';
  summary: string;
  hint: string | null;
}

export interface RuntimeStatTileVM {
  key: string;
  label: string;
  value: string;
}

export interface RuntimeDiagnosticVM {
  event: string;
  levelText: string;
  tone: 'warning' | 'error';
  /** ISO 时刻;相对文案与悬停原值交由 RelativeTime 呈现(相对时间会过期,须由单钟刷新) */
  firstSeen: string;
  lastSeen: string;
  count: number;
}

export interface RuntimeRecentJobVM {
  runId: string;
  /** 类型白话名(后端给,前端不再自建 lane 映射) */
  typeText: string;
  statusText: string;
  statusTone: RuntimeItemTone;
  /** ISO 时刻;相对文案交由 RelativeTime 呈现 */
  createdAt: string;
  /** 终态给时长文案,非终态给"等待中/进行中" */
  durationText: string;
  reasonCode: string | null;
}

export interface RuntimeStatusVM {
  badge: RuntimeBadgeVM;
  overallStatusText: string;
  /** ISO 时刻;空串=尚无时刻(未取到数据) */
  generatedAt: string;
  /** 本屏数据的采集时刻(hh:mm:ss);'—'=尚无时刻 */
  generatedAtClock: string;
  /** 采集时刻的悬停原值(本地时区完整时刻);空串=尚无时刻,组件据此省略 title 属性
   * ——title="" 是无意义 tooltip(合并裁定 F6,与 RelativeTime 同口径) */
  generatedAtTitle: string;
  asyncModeText: string;
  components: RuntimeComponentVM[];
  alerts: RuntimeAlertVM[];
  asyncJobTiles: RuntimeStatTileVM[];
  recentJobs: RuntimeRecentJobVM[];
  diagnostics: RuntimeDiagnosticVM[];
  countingRules: string[];
}

const OVERALL_TEXT: Record<string, string> = {
  normal: '正常',
  degraded: '降级',
  down: '异常',
};

const COMPONENT_TEXT: Record<string, { text: string; tone: RuntimeItemTone }> = {
  ok: { text: '正常', tone: 'ok' },
  degraded: { text: '降级', tone: 'warning' },
  down: { text: '异常', tone: 'error' },
  not_applicable: { text: '不适用', tone: 'muted' },
};

// 计数口径说明(照 04A §2.1 徽标计数表,只做提示文案,不是新事实源)
const COUNTING_RULES = [
  '运行态徽标:当前活跃运行风险组数量,按风险组去重,不按日志次数累加。',
  '通知徽标:需要当前用户处理的未读事项数量,按事项去重(P02 落地)。',
];

// 异步作业状态的用户视角文案(不出稳定码)
const JOB_STATUS_TEXT: Record<string, { text: string; tone: RuntimeItemTone }> = {
  queued: { text: '等待中', tone: 'muted' },
  started: { text: '进行中', tone: 'warning' },
  succeeded: { text: '已完成', tone: 'ok' },
  failed: { text: '失败', tone: 'error' },
};

export const RECENT_JOBS_EMPTY_TEXT = '最近没有异步作业';

function countText(value: number | null): string {
  return value === null ? '—' : String(value);
}

/** 耗时文案:秒→分秒→时分→天时,只保留两级,够读即可。
 * 名字带 Seconds 是刻意的:同目录 ai-request-trace.ts 另有一个收毫秒的 formatDuration,
 * 两者签名同为 (number) => string,选错导入即 1000 倍量级错误且类型检查照过。 */
export function formatDurationSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) {
    const rest = seconds % 60;
    return rest === 0 ? `${Math.floor(seconds / 60)} 分` : `${Math.floor(seconds / 60)} 分 ${rest} 秒`;
  }
  if (seconds < 86400) {
    const rest = Math.floor((seconds % 3600) / 60);
    return rest === 0 ? `${Math.floor(seconds / 3600)} 小时` : `${Math.floor(seconds / 3600)} 小时 ${rest} 分`;
  }
  const rest = Math.floor((seconds % 86400) / 3600);
  return rest === 0 ? `${Math.floor(seconds / 86400)} 天` : `${Math.floor(seconds / 86400)} 天 ${rest} 小时`;
}

export function buildRuntimeStatusVM(
  data: RuntimeStatusRead | null,
  phase: RuntimeFetchPhase,
): RuntimeStatusVM {
  if (data === null) {
    return {
      badge:
        phase === 'error'
          ? { tone: 'unknown', statusText: '不可达', alertCount: 0 }
          : { tone: 'unknown', statusText: '检测中', alertCount: 0 },
      overallStatusText: phase === 'error' ? '后端不可达' : '检测中',
      generatedAt: '',
      generatedAtClock: '—',
      generatedAtTitle: '',
      asyncModeText: '—',
      components: [],
      alerts: [],
      asyncJobTiles: [],
      recentJobs: [],
      diagnostics: [],
      countingRules: COUNTING_RULES,
    };
  }

  const overallText = OVERALL_TEXT[data.status] ?? '未知';
  const jobs = data.async_jobs;

  return {
    badge: {
      tone: (data.status in OVERALL_TEXT ? data.status : 'unknown') as RuntimeBadgeTone,
      statusText: overallText,
      alertCount: data.alert_count,
    },
    overallStatusText: overallText,
    generatedAt: data.generated_at,
    generatedAtClock: formatClockTime(data.generated_at),
    generatedAtTitle: formatAbsoluteTime(data.generated_at),
    asyncModeText: jobs.mode === 'inline' ? '同步执行(inline)' : '队列模式(RQ)',
    components: data.components.map((component) => {
      const mapped = COMPONENT_TEXT[component.status] ?? { text: '未知', tone: 'muted' as const };
      return {
        key: component.key,
        label: component.label,
        statusText: mapped.text,
        tone: mapped.tone,
        detail: component.detail ?? '',
      };
    }),
    alerts: data.alerts.map((alert) => ({
      code: alert.code,
      levelText: alert.level === 'ERROR' ? '异常' : '警告',
      tone: alert.level === 'ERROR' ? 'error' : 'warning',
      summary: alert.summary,
      hint: alert.hint ?? null,
    })),
    asyncJobTiles: [
      { key: 'queued', label: '等待', value: countText(jobs.queued) },
      { key: 'running', label: '运行中', value: countText(jobs.running) },
      { key: 'failed_recent', label: '近24h失败', value: countText(jobs.failed_recent) },
      {
        key: 'oldest_waiting',
        label: '最久等待',
        value: jobs.oldest_waiting_minutes === null ? '—' : `${jobs.oldest_waiting_minutes} 分钟`,
      },
    ],
    recentJobs: (data.recent_jobs ?? []).map((job) => {
      const mapped = JOB_STATUS_TEXT[job.status] ?? { text: '未知', tone: 'muted' as const };
      return {
        runId: job.run_id,
        typeText: job.kind_label,
        statusText: mapped.text,
        statusTone: mapped.tone,
        createdAt: job.created_at,
        // 非终态没有耗时可算,直接复用状态文案(等待中/进行中),不编造数字。
        durationText:
          job.duration_seconds === null
            ? mapped.text
            : formatDurationSeconds(job.duration_seconds),
        reasonCode: job.reason_code,
      };
    }),
    diagnostics: data.diagnostics.map((entry) => ({
      event: entry.event,
      levelText: entry.level === 'ERROR' ? '错误' : '警告',
      tone: entry.level === 'ERROR' ? 'error' : 'warning',
      firstSeen: entry.first_seen,
      lastSeen: entry.last_seen,
      count: entry.count,
    })),
    countingRules: COUNTING_RULES,
  };
}
