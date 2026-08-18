import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SseFrame } from '../src/api/client';

// 可编程帧序列驱动 apiPostSse mock（vi.hoisted 保证工厂可安全引用可变容器）
const h = vi.hoisted(() => ({ frames: [] as SseFrame[] }));
vi.mock('../src/api/client', () => ({
  apiPostSse: vi.fn(async (_path: string, _body: unknown, onFrame: (f: SseFrame) => void) => {
    for (const f of h.frames) onFrame(f);
  }),
}));

import { sendDialogueStream } from '../src/api/dialogue-stream';
import { ApiError } from '../src/api/errors';

// P0 传输层收敛：三页对话 SSE 帧解析收敛为本一份实现，语义与原三份逐字一致。
// 覆盖测试义务四分支：stage / result / error / 连接中断（无 result 帧）。
describe('sendDialogueStream（唯一 SSE 实现）', () => {
  beforeEach(() => {
    h.frames = [];
  });

  it('stage 分支：逐帧回调 onStage（stage 名），并按 result 帧返回结果', async () => {
    h.frames = [
      { event: 'stage', data: JSON.stringify({ stage: '受理' }) },
      { event: 'stage', data: JSON.stringify({ stage: '执行' }) },
      { event: 'result', data: JSON.stringify({ ok: true }) },
    ];
    const stages: string[] = [];
    const r = await sendDialogueStream<{ ok: boolean }>('/x/dialogue', {}, { onStage: (s) => stages.push(s) });
    expect(stages).toEqual(['受理', '执行']);
    expect(r).toEqual({ ok: true });
  });

  it('result 分支：终帧作为返回值（按 TResult 收窄）', async () => {
    h.frames = [{ event: 'result', data: JSON.stringify({ outcome: 'explanation', id: 7 }) }];
    const r = await sendDialogueStream<{ outcome: string; id: number }>('/x/dialogue', {}, {});
    expect(r).toEqual({ outcome: 'explanation', id: 7 });
  });

  it('error 分支：抛 ApiError（kind=http），文案取帧内 message', async () => {
    h.frames = [{ event: 'error', data: JSON.stringify({ message: '后端拒绝执行' }) }];
    await expect(sendDialogueStream('/x/dialogue', {}, {})).rejects.toBeInstanceOf(ApiError);
    await expect(sendDialogueStream('/x/dialogue', {}, {})).rejects.toMatchObject({
      message: '后端拒绝执行',
      kind: 'http',
    });
  });

  it('error 分支：帧 data 非 JSON 时文案回退为原始 data', async () => {
    h.frames = [{ event: 'error', data: '裸文本错误' }];
    await expect(sendDialogueStream('/x/dialogue', {}, {})).rejects.toMatchObject({ message: '裸文本错误' });
  });

  it('中断分支：无 result 帧（连接被中断）抛 invalid-response，文案逐字保持', async () => {
    h.frames = [{ event: 'stage', data: JSON.stringify({ stage: '受理' }) }];
    await expect(sendDialogueStream('/x/dialogue', {}, {})).rejects.toMatchObject({
      message: '流式响应未返回结果帧（连接被中断）',
      kind: 'invalid-response',
    });
  });

  it('stage 帧损坏（data 非 JSON）：不点灯、不抛错，不影响后续 result（帧损坏只影响点灯）', async () => {
    h.frames = [
      { event: 'stage', data: '坏帧{' },
      { event: 'result', data: JSON.stringify({ ok: 1 }) },
    ];
    const stages: string[] = [];
    const r = await sendDialogueStream<{ ok: number }>('/x/dialogue', {}, { onStage: (s) => stages.push(s) });
    expect(stages).toEqual([]);
    expect(r).toEqual({ ok: 1 });
  });
});
