/**
 * 链路回执条 VM：阶段=后端事实点灯、停滞派生、队列支扩展、完成收敛摘要。
 * 事实源：docs/40 slices/SCN-001-P02/页面详细设计.md §5.1 链路回执条行、04A §2.1 增补。
 */
import { describe, expect, it } from 'vitest';
import {
  createTrace,
  formatDuration,
  projectTrace,
  TRACE_STALL_THRESHOLDS_MS,
  traceAdvance,
  traceCurrentStage,
  traceDetailLines,
  traceExtendQueue,
  traceFinish,
  traceSummaryText,
} from '../src/view-models/ai-request-trace';

const T0 = 1_000_000;

describe('链路推进（只由后端阶段事实点灯）', () => {
  it('初始三节点全 pending；收到 stage 帧才点亮', () => {
    const t = createTrace(T0);
    expect(projectTrace(t, T0).nodes.map((n) => n.state)).toEqual(['pending', 'pending', 'pending']);
    const t2 = traceAdvance(t, 'accepted', T0 + 100);
    const nodes = projectTrace(t2, T0 + 200).nodes;
    expect(nodes[0].state).toBe('active');
    expect(nodes[1].state).toBe('pending');
  });

  it('推进到后续阶段时，前序阶段转 done 并结算阶段耗时', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0 + 100);
    t = traceAdvance(t, 'interpreting', T0 + 600);
    const nodes = projectTrace(t, T0 + 1000).nodes;
    expect(nodes[0].state).toBe('done');
    expect(nodes[0].elapsedMs).toBe(500); // accepted: 100 → 600
    expect(nodes[1].state).toBe('active');
  });

  it('重复/未知阶段帧不改变状态（幂等）', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0 + 100);
    const again = traceAdvance(t, 'accepted', T0 + 900);
    expect(again).toBe(t);
  });

  it('free-text 直达 running（阶段不在初始 path 时按全序插入）', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0 + 50);
    t = traceAdvance(t, 'running', T0 + 200);
    expect(t.path).toEqual(['accepted', 'interpreting', 'dispatching', 'running']);
    expect(traceCurrentStage(t)).toBe('running');
  });
});

describe('队列支与停滞派生', () => {
  it('派发队列型运行：扩展排队/执行/回写节点并立即进入排队', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0 + 10);
    t = traceAdvance(t, 'interpreting', T0 + 20);
    t = traceAdvance(t, 'dispatching', T0 + 30);
    t = traceExtendQueue(t, 'run-1', T0 + 40);
    expect(t.path).toEqual(['accepted', 'interpreting', 'dispatching', 'queued', 'running', 'writing']);
    expect(t.runRef).toBe('run-1');
    expect(traceCurrentStage(t)).toBe('queued');
  });

  it('阶段停留超过阈值 → stalled（显示派生，不改事实）', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0);
    t = traceAdvance(t, 'interpreting', T0 + 100);
    const before = projectTrace(t, T0 + 100 + TRACE_STALL_THRESHOLDS_MS.interpreting - 1);
    expect(before.nodes[1].state).toBe('active');
    const after = projectTrace(t, T0 + 100 + TRACE_STALL_THRESHOLDS_MS.interpreting + 1);
    expect(after.nodes[1].state).toBe('stalled');
    expect(after.stalled).toBe(true);
    expect(after.nodes[1].stallHint).toContain('模型');
  });
});

describe('终态与收敛', () => {
  it('失败终态固定于最后到达的节点', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0);
    t = traceAdvance(t, 'interpreting', T0 + 100);
    t = traceFinish(t, 'failed', T0 + 5000, { message: '解释服务超时' });
    const nodes = projectTrace(t, T0 + 9000).nodes;
    expect(nodes[0].state).toBe('done');
    expect(nodes[1].state).toBe('failed');
    expect(projectTrace(t, T0 + 9000).totalMs).toBe(5000); // 完成后停表
  });

  it('耗时摘要只列有意义阶段并带总时长', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0);
    t = traceAdvance(t, 'interpreting', T0 + 200);
    t = traceAdvance(t, 'dispatching', T0 + 2400);
    t = traceFinish(t, 'done', T0 + 2600);
    const summary = traceSummaryText(t);
    expect(summary).toContain('解释 2.2s');
    expect(summary).toContain('共 2.6s');
    expect(summary).not.toContain('派发'); // 短暂阶段不进摘要
  });

  it('详情行含阶段观测时刻与运行引用（日志对账）', () => {
    let t = createTrace(T0);
    t = traceAdvance(t, 'accepted', T0);
    t = traceExtendQueue(t, 'run-9', T0 + 100);
    const lines = traceDetailLines(t);
    expect(lines.some((l) => l.startsWith('受理 @'))).toBe(true);
    expect(lines).toContain('agent_run_ref: run-9');
  });
});

describe('formatDuration', () => {
  it('亚秒 / 秒 / 分钟三档', () => {
    expect(formatDuration(300)).toBe('<1s');
    expect(formatDuration(2340)).toBe('2.3s');
    expect(formatDuration(65_000)).toBe('1m05s');
  });
});
