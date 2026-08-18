/**
 * 确认前的在途修订守卫与孤儿稿回流（T20260725-revision-inflight-guard）。
 *
 * 缺陷背景：2026-07-25 用户对一条知识项发了 AI 修订指令，AI 稿 9 秒后才落库，
 * 其间用户已把这条确认掉，稿子成了没人采纳的孤儿。此前确认按钮完全不问在途修订。
 *
 * 本文件守三件的界面契约：确认前调预检并按结果弹二次确认（两种继续方式各自送什么），
 * 已确认条目上的孤儿稿给不给回流入口，以及回流请求带不带可读的原因。
 * 「起草中」气泡的视觉可区分性由浏览器走查证据兜底，这里只守它作为一条在场消息存在。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getMaterialCanvasMock = vi.fn();
const getWorkspaceMock = vi.fn();
const decideElementsMock = vi.fn();
const precheckDecideElementsMock = vi.fn();
const reopenElementMock = vi.fn();

vi.mock('../src/api/analysis', () => ({
  analysisApi: {
    getMaterialCanvas: (...args: unknown[]) => getMaterialCanvasMock(...args),
    getMaterialParseContext: vi.fn(),
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    submitRecognition: vi.fn(),
    decideElements: (...args: unknown[]) => decideElementsMock(...args),
    precheckDecideElements: (...args: unknown[]) => precheckDecideElementsMock(...args),
    reopenElement: (...args: unknown[]) => reopenElementMock(...args),
    sendDialogueStream: vi.fn(),
    getElementHistory: vi.fn(),
  },
}));
vi.mock('../src/api/transcript', () => ({ fetchChatTranscript: vi.fn(() => Promise.resolve({ rows: [] })) }));
vi.mock('../src/api/runtime-status', () => ({ runtimeStatusApi: { probe: vi.fn() } }));

import { RequirementAnalysisFlow } from '../src/workbenches/RequirementAnalysisFlow';

const RAW = '系统应支持批量导入。导入失败需给出原因。';
const MATERIAL_REF = 'M-1';
const CONTEXT_REF = 'PCTX-9';

const canvas = {
  material_ref: MATERIAL_REF,
  title: '导入需求说明',
  source_note: '走查材料',
  raw_text: RAW,
  source_version: 1,
  blocks: [{ block_id: 'B1', start_offset: 0, end_offset: RAW.length, text: RAW }],
  supplements: [],
};

function anchorFor(text: string): string {
  const start = RAW.indexOf(text);
  return JSON.stringify({ material_ref: MATERIAL_REF, ranges: [{ start, end: start + text.length, exact: text }] });
}

function element(overrides: Record<string, unknown>) {
  return {
    element_type: 'functional_requirement',
    source_drift_tokens: [],
    confidence: 0.9,
    process_status: 'pending_confirmation',
    model_verdict: 'processable',
    version: 1,
    superseded: false,
    origin_refs: [],
    ...overrides,
  };
}

const beingRevised = element({
  id: 'E-REVISING',
  content: '系统应支持批量导入',
  source_anchor: anchorFor('系统应支持批量导入'),
});

const untouched = element({
  id: 'E-CALM',
  content: '导入失败需给出原因',
  source_anchor: anchorFor('导入失败需给出原因'),
});

function workspaceWith(elements: unknown[], selected = 'E-REVISING') {
  return {
    parse_context_ref: CONTEXT_REF,
    parse_result_ref: 'PR-1',
    workspace_version: '3',
    parse_status: 'parsed',
    material_canvas: canvas,
    elements,
    merged_existing_elements: [],
    selected_element_ref: selected,
    available_actions: [],
    available_operations: [],
  };
}

const guardedHit = {
  element_ref: 'E-REVISING',
  content_brief: '系统应支持批量导入',
  agent_run_ref: 'AR-77',
  run_status: 'started',
};

function renderFlow() {
  return render(
    <RequirementAnalysisFlow
      initialParseContextRef={CONTEXT_REF}
      materialRef={MATERIAL_REF}
      onBackToIntake={vi.fn()}
      operatorRef="U-1"
      projectId="P-1"
    />,
  );
}

/** 等区1 行渲染完：工作区是异步拉的，先有行才谈得上点确认。 */
async function rows(container: HTMLElement): Promise<HTMLElement[]> {
  return waitFor(() => {
    const found = [...container.querySelectorAll('.az1-row')] as HTMLElement[];
    if (!found.length) throw new Error('区1 行未渲染');
    return found;
  });
}

/** 区5 裁决条的「✓ 确认」（.az5-qp--ok）——区1 顶部的「待确认」是状态筛选片，不是它。 */
async function clickConfirm(container: HTMLElement) {
  await rows(container);
  const confirmButton = container.querySelector('.az5-qp--ok') as HTMLButtonElement | null;
  if (!confirmButton) throw new Error('未找到区5 确认按钮');
  fireEvent.click(confirmButton);
}

function dialogButton(label: string): HTMLElement {
  const found = [...document.querySelectorAll('.ant-modal button')]
    .find((b) => (b.textContent ?? '').includes(label));
  if (!found) throw new Error(`弹层里没有「${label}」按钮`);
  return found as HTMLElement;
}

beforeEach(() => {
  getMaterialCanvasMock.mockReset().mockResolvedValue(canvas);
  getWorkspaceMock.mockReset().mockResolvedValue(workspaceWith([beingRevised, untouched]));
  decideElementsMock.mockReset().mockImplementation(
    () => Promise.resolve(workspaceWith([beingRevised, untouched])),
  );
  precheckDecideElementsMock.mockReset().mockResolvedValue({ guarded: [] });
  reopenElementMock.mockReset().mockImplementation(
    () => Promise.resolve(workspaceWith([beingRevised, untouched])),
  );
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('A1 单条确认的在途修订守卫', () => {
  it('没有在途修订时直接确认：预检查过一次，确认照常发出且不带 ack 标记', async () => {
    const { container } = renderFlow();
    await clickConfirm(container);

    await waitFor(() => expect(decideElementsMock).toHaveBeenCalledTimes(1));
    expect(precheckDecideElementsMock).toHaveBeenCalledTimes(1);
    expect(decideElementsMock.mock.calls[0][2]).toMatchObject({
      decision: 'confirm',
      inflight_revision_ack: false,
    });
  });

  it('有在途修订时先弹二次确认：确认请求此刻一条都没发出', async () => {
    precheckDecideElementsMock.mockResolvedValue({ guarded: [guardedHit] });
    const { container } = renderFlow();
    await clickConfirm(container);

    await screen.findByText(/这些知识项正在被 AI 起草修订/);
    expect(decideElementsMock).not.toHaveBeenCalled();
  });

  it('弹层里逐条列出被拦下的知识项，供用户认人', async () => {
    precheckDecideElementsMock.mockResolvedValue({ guarded: [guardedHit] });
    const { container } = renderFlow();
    await clickConfirm(container);

    const list = await screen.findByLabelText('正在被 AI 起草修订的知识项');
    expect(list.textContent).toContain('系统应支持批量导入');
  });

  it('选「等起草完成」＝什么都不做：弹层关掉，确认不发出', async () => {
    precheckDecideElementsMock.mockResolvedValue({ guarded: [guardedHit] });
    const { container } = renderFlow();
    await clickConfirm(container);
    await screen.findByText(/这些知识项正在被 AI 起草修订/);

    fireEvent.click(dialogButton('等起草完成'));

    await waitFor(() => expect(screen.queryByLabelText('正在被 AI 起草修订的知识项')).toBeNull());
    expect(decideElementsMock).not.toHaveBeenCalled();
  });

  it('选「仍要全部确认」＝带 ack 提交全部目标（软拦截，人工有最终权威）', async () => {
    precheckDecideElementsMock.mockResolvedValue({ guarded: [guardedHit] });
    const { container } = renderFlow();
    await clickConfirm(container);
    await screen.findByText(/这些知识项正在被 AI 起草修订/);

    fireEvent.click(dialogButton('仍要全部确认'));

    await waitFor(() => expect(decideElementsMock).toHaveBeenCalledTimes(1));
    const sent = decideElementsMock.mock.calls[0][2];
    expect(sent.inflight_revision_ack).toBe(true);
    expect(sent.element_refs).toContain('E-REVISING');
  });

  it('预检失败不挡确认：守卫是提醒不是门禁，网络抖动不该让人裁决不了', async () => {
    precheckDecideElementsMock.mockRejectedValue(new Error('network down'));
    const { container } = renderFlow();
    await clickConfirm(container);

    await waitFor(() => expect(decideElementsMock).toHaveBeenCalledTimes(1));
    expect(decideElementsMock.mock.calls[0][2].inflight_revision_ack).toBe(false);
  });
});

describe('A2 批量确认的两种继续方式', () => {
  /** 勾上两条 → 提交目标是批量的（确认按钮按提交目标算，不按当前选中项算）。 */
  async function checkBoth(container: HTMLElement) {
    await rows(container);
    const boxes = [...container.querySelectorAll('.az1-row input[type="checkbox"]')];
    boxes.forEach((box) => fireEvent.click(box));
    await waitFor(() => {
      const checked = [...container.querySelectorAll('.az1-row input[type="checkbox"]:checked')];
      if (checked.length < 2) throw new Error('两条都要勾上');
    });
  }

  it('「跳过这些，确认其余」只送没在起草的那条', async () => {
    precheckDecideElementsMock.mockResolvedValue({ guarded: [guardedHit] });
    const { container } = renderFlow();
    await checkBoth(container);
    await clickConfirm(container);
    await screen.findByText(/这些知识项正在被 AI 起草修订/);

    fireEvent.click(dialogButton('跳过这些，确认其余'));

    await waitFor(() => expect(decideElementsMock).toHaveBeenCalledTimes(1));
    const sent = decideElementsMock.mock.calls[0][2];
    expect(sent.element_refs).toEqual(['E-CALM']);
    expect(sent.inflight_revision_ack).toBe(false);  // 送出去的这批本来就没有在途修订
  });

  it('「仍要全部确认」两条都送，并带 ack', async () => {
    precheckDecideElementsMock.mockResolvedValue({ guarded: [guardedHit] });
    const { container } = renderFlow();
    await checkBoth(container);
    await clickConfirm(container);
    await screen.findByText(/这些知识项正在被 AI 起草修订/);

    fireEvent.click(dialogButton('仍要全部确认'));

    await waitFor(() => expect(decideElementsMock).toHaveBeenCalledTimes(1));
    const sent = decideElementsMock.mock.calls[0][2];
    expect(sent.element_refs).toEqual(expect.arrayContaining(['E-REVISING', 'E-CALM']));
    expect(sent.inflight_revision_ack).toBe(true);
  });

  it('全批都在起草时不给「跳过其余」这条路——没有其余可跳', async () => {
    precheckDecideElementsMock.mockResolvedValue({
      guarded: [guardedHit, { ...guardedHit, element_ref: 'E-CALM', content_brief: '导入失败需给出原因' }],
    });
    const { container } = renderFlow();
    await checkBoth(container);
    await clickConfirm(container);
    await screen.findByText(/这些知识项正在被 AI 起草修订/);

    expect(() => dialogButton('跳过这些，确认其余')).toThrow();
  });
});

describe('A4 已确认条目上的孤儿稿', () => {
  const orphaned = element({
    id: 'E-REVISING',
    content: '系统应支持批量导入',
    source_anchor: anchorFor('系统应支持批量导入'),
    process_status: 'confirmed',
    revision_draft: '系统应支持批量导入，且失败时给出原因。',
  });

  it('详情区给「回流以采纳」入口，并说明稿子为什么没生效', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith([orphaned, untouched]));
    const { container } = renderFlow();
    await rows(container);

    expect(await screen.findByText('回流以采纳')).toBeTruthy();
    expect(container.textContent).toContain('这条已确认，修订稿没有生效');
  });

  it('点回流走既有回流端点，原因写成用户看得懂的话（留痕可读）', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith([orphaned, untouched]));
    renderFlow();
    fireEvent.click(await screen.findByText('回流以采纳'));

    await waitFor(() => expect(reopenElementMock).toHaveBeenCalledTimes(1));
    expect(reopenElementMock.mock.calls[0][2]).toMatchObject({
      element_ref: 'E-REVISING',
      reason: '回流以采纳搁置的修订稿',
    });
  });

  it('稿子还挂在待确认条目上时不给回流入口——那条走常规采纳即可', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith([
      element({
        id: 'E-REVISING',
        content: '系统应支持批量导入',
        source_anchor: anchorFor('系统应支持批量导入'),
        revision_draft: '系统应支持批量导入，且失败时给出原因。',
      }),
      untouched,
    ]));
    const { container } = renderFlow();
    await rows(container);

    await waitFor(() => expect(container.textContent).toContain('当前修订稿'));
    expect(screen.queryByText('回流以采纳')).toBeNull();
  });
});
