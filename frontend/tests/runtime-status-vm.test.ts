import { describe, expect, it } from 'vitest';
import type { RuntimeStatusRead } from '../src/api/runtime-status';
import { buildRuntimeStatusVM, formatDurationSeconds } from '../src/view-models/runtime-status';

function payload(overrides: Partial<RuntimeStatusRead> = {}): RuntimeStatusRead {
  return {
    status: 'normal',
    alert_count: 0,
    generated_at: '2026-07-04T11:59:30Z',
    components: [
      { key: 'api', label: 'API', status: 'ok', detail: '服务响应正常' },
      { key: 'db', label: 'DB', status: 'ok', detail: 'SELECT 1 探活通过' },
      { key: 'redis', label: 'Redis', status: 'not_applicable', detail: '未配置 REDIS_URL' },
      { key: 'worker', label: 'Worker', status: 'down', detail: '无活跃 worker' },
      { key: 'event_bus', label: 'Event Bus / SSE', status: 'degraded', detail: 'SSE 降级' },
    ],
    alerts: [],
    async_jobs: {
      mode: 'inline',
      queued: 2,
      running: 1,
      failed_recent: 0,
      oldest_waiting_minutes: null,
      queue_depth: null,
    },
    diagnostics: [],
    ...overrides,
  };
}

describe('runtime-status view model', () => {
  it('正常态:徽标绿色、计数为 0、组件状态逐个映射', () => {
    const vm = buildRuntimeStatusVM(payload(), 'ready');

    expect(vm.badge).toEqual({ tone: 'normal', statusText: '正常', alertCount: 0 });
    expect(vm.components.map((c) => c.statusText)).toEqual(['正常', '正常', '不适用', '异常', '降级']);
    expect(vm.components.map((c) => c.tone)).toEqual(['ok', 'ok', 'muted', 'error', 'warning']);
    expect(vm.asyncModeText).toBe('同步执行(inline)');
    // VM 只透传 ISO,相对文案由 RelativeTime 呈现(见 relative-time.test.tsx)
    expect(vm.generatedAt).toBe('2026-07-04T11:59:30Z');
  });

  it('降级态:告警映射级别文案,徽标计数=活跃风险组数', () => {
    const vm = buildRuntimeStatusVM(
      payload({
        status: 'degraded',
        alert_count: 2,
        alerts: [
          { code: 'async.worker.absent', level: 'ERROR', summary: 'Worker 未运行', hint: '启动 worker' },
          { code: 'sse.degraded', level: 'WARN', summary: 'SSE 降级为轮询', hint: null },
        ],
      }),
      'ready',
    );

    expect(vm.badge).toEqual({ tone: 'degraded', statusText: '降级', alertCount: 2 });
    expect(vm.alerts).toEqual([
      { code: 'async.worker.absent', levelText: '异常', tone: 'error', summary: 'Worker 未运行', hint: '启动 worker' },
      { code: 'sse.degraded', levelText: '警告', tone: 'warning', summary: 'SSE 降级为轮询', hint: null },
    ]);
  });

  it('异步作业瓦片:计数与空值占位', () => {
    const vm = buildRuntimeStatusVM(
      payload({
        async_jobs: {
          mode: 'queued',
          queued: 12,
          running: 2,
          failed_recent: 3,
          oldest_waiting_minutes: 18,
          queue_depth: 12,
        },
      }),
      'ready',
    );

    expect(vm.asyncModeText).toBe('队列模式(RQ)');
    expect(vm.asyncJobTiles.map((tile) => tile.value)).toEqual(['12', '2', '3', '18 分钟']);
  });

  it('诊断事件:时刻透传与级别文案', () => {
    const vm = buildRuntimeStatusVM(
      payload({
        diagnostics: [
          {
            event: 'agent.run.failed',
            component: 'agent-worker',
            level: 'ERROR',
            first_seen: '2026-07-04T11:00:00Z',
            last_seen: '2026-07-04T11:58:00Z',
            count: 7,
          },
        ],
      }),
      'ready',
    );

    expect(vm.diagnostics).toEqual([
      {
        event: 'agent.run.failed',
        levelText: '错误',
        tone: 'error',
        firstSeen: '2026-07-04T11:00:00Z',
        lastSeen: '2026-07-04T11:58:00Z',
        count: 7,
      },
    ]);
  });

  it('后端不可达:徽标转未知态,面板保留计数规则说明', () => {
    const vm = buildRuntimeStatusVM(null, 'error');

    expect(vm.badge).toEqual({ tone: 'unknown', statusText: '不可达', alertCount: 0 });
    expect(vm.overallStatusText).toBe('后端不可达');
    expect(vm.components).toEqual([]);
    expect(vm.countingRules.length).toBeGreaterThan(0);
  });

  it('加载中:徽标显示检测中', () => {
    const vm = buildRuntimeStatusVM(null, 'loading');

    expect(vm.badge.statusText).toBe('检测中');
  });
});

// ---- 最近作业与采集时刻（T20260724-agent-run-observability ③⑤）----

describe('最近作业投影', () => {
  it('状态与耗时都用用户视角文案，稳定码原样透传', () => {
    const vm = buildRuntimeStatusVM(
      payload({
        recent_jobs: [
          {
            run_id: 'r-1', kind: 'item_formation', kind_label: '需求条目形成',
            status: 'succeeded', created_at: '2026-07-04T11:58:00Z',
            duration_seconds: 72, reason_code: null,
          },
          {
            run_id: 'r-2', kind: 'docx_export', kind_label: 'docx 导出转换',
            status: 'queued', created_at: '2026-07-04T11:57:00Z',
            duration_seconds: null, reason_code: null,
          },
          {
            run_id: 'r-3', kind: 'item_diagnosis', kind_label: '条目评审诊断',
            status: 'failed', created_at: '2026-07-04T11:00:00Z',
            duration_seconds: 3600, reason_code: 'queue.orphaned',
          },
        ],
      }),
      'ready',
    );

    expect(vm.recentJobs.map((job) => job.statusText)).toEqual(['已完成', '等待中', '失败']);
    expect(vm.recentJobs.map((job) => job.statusTone)).toEqual(['ok', 'muted', 'error']);
    expect(vm.recentJobs.map((job) => job.durationText)).toEqual(['1 分 12 秒', '等待中', '1 小时']);
    expect(vm.recentJobs[2].reasonCode).toBe('queue.orphaned');
    expect(vm.recentJobs[0].typeText).toBe('需求条目形成'); // 白话名,稳定码不裸出
  });

  it('后端未返回 recent_jobs 时给空列表,不抛', () => {
    const { recent_jobs: _dropped, ...withoutRecent } = payload();

    const vm = buildRuntimeStatusVM(withoutRecent as RuntimeStatusRead, 'ready');

    expect(vm.recentJobs).toEqual([]);
  });

  it('耗时文案逐档:秒 / 分秒 / 时分 / 天时', () => {
    expect(formatDurationSeconds(0)).toBe('0 秒');
    expect(formatDurationSeconds(59)).toBe('59 秒');
    expect(formatDurationSeconds(60)).toBe('1 分');
    expect(formatDurationSeconds(3599)).toBe('59 分 59 秒');
    expect(formatDurationSeconds(3600)).toBe('1 小时');
    expect(formatDurationSeconds(86_399)).toBe('23 小时 59 分');
    expect(formatDurationSeconds(86_400)).toBe('1 天');
    expect(formatDurationSeconds(1_900_000)).toBe('21 天 23 小时');
  });

  it('采集时刻取投喂的 generated_at 本身;无数据时给占位', () => {
    // 测试时区钉死为 Asia/Shanghai(见 vite.config.ts),故 11:59:30Z 的本地时刻确定为 19:59:30。
    // 比对取值而非形状:只匹配 \d{2}:\d{2}:\d{2} 时,渲染浏览器当前时钟也照过。
    const vm = buildRuntimeStatusVM(payload(), 'ready');

    expect(vm.generatedAtClock).toBe('19:59:30');
    expect(vm.generatedAtTitle).toBe('2026-07-04 19:59:30');
    expect(buildRuntimeStatusVM(null, 'loading').generatedAtClock).toBe('—');
    // 空态不给悬停原值:组件据此省略 title 属性(合并裁定 F6)
    expect(buildRuntimeStatusVM(null, 'loading').generatedAtTitle).toBe('');
  });
});
