/**
 * 知识抽取页「进页只读回放」用例（T20260719-demo-analysis-clarity）。
 *
 * 缺陷：不带恢复锚点进入时页面只加载材料正文，不问这份材料是否已经识别过，
 * 于是已识别的材料被当成未识别——区5 输入框与命令按钮全禁用，用户只剩「重新识别」
 * 一条路（那会另起一份清单并把既有成果移出工作区）。
 * 口径：挂载时问后端最近一次识别上下文；有就只读读回工作区，没有才停在未识别态。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import type { ChatTranscriptRow } from '../src/api/transcript';

type TranscriptRow = ChatTranscriptRow;

const getMaterialCanvasMock = vi.fn();
const getMaterialParseContextMock = vi.fn();
const getWorkspaceMock = vi.fn();

vi.mock('../src/api/analysis', () => ({
  analysisApi: {
    getMaterialCanvas: (...args: unknown[]) => getMaterialCanvasMock(...args),
    getMaterialParseContext: (...args: unknown[]) => getMaterialParseContextMock(...args),
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    submitRecognition: vi.fn(),
    sendDialogueStream: vi.fn(),
    decideElements: vi.fn(),
    getElementHistory: vi.fn(),
  },
}));
// 契约是 ChatTranscriptRead 即 { rows: [...] }，不是数组（冷审查裁定 C12）：
// 此前 mock 成数组，组件读 res.rows.length 抛 TypeError 落进 catch，四例里有两例
// 一直带着「历史消息读取失败」横幅通过，留痕水合的接线一行都没被验证过。
const fetchChatTranscriptMock = vi.fn(() => Promise.resolve({ rows: [] as TranscriptRow[] }));
vi.mock('../src/api/transcript', () => ({
  fetchChatTranscript: (...args: unknown[]) => fetchChatTranscriptMock(...(args as [])),
}));
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
  blocks: [{ block_id: 'B1', start: 0, end: RAW.length, text: RAW }],
  supplements: [],
};

const workspace = {
  parse_context_ref: CONTEXT_REF,
  parse_result_ref: 'PR-1',
  workspace_version: '3',
  parse_status: 'parsed',
  material_canvas: canvas,
  elements: [
    {
      id: 'E-1',
      element_type: 'functional_requirement',
      content: '系统应支持批量导入',
      source_anchor: null,
      confidence: 0.9,
      source_drift_tokens: [],
      process_status: 'pending_confirmation',
      model_verdict: 'processable',
      version: 1,
      superseded: false,
      origin_refs: [],
    },
  ],
  merged_existing_elements: [],
  selected_element_ref: 'E-1',
  available_actions: [],
  available_operations: [],
};

function renderFlow() {
  return render(
    <RequirementAnalysisFlow
      materialRef={MATERIAL_REF}
      onBackToIntake={vi.fn()}
      operatorRef="U-1"
      projectId="P-1"
    />,
  );
}

function composer(): HTMLTextAreaElement {
  return screen.getByLabelText('消息输入') as HTMLTextAreaElement;
}

function transcriptRow(patch: Partial<TranscriptRow> & { id: string }): TranscriptRow {
  return {
    channel: 'analysis',
    context_ref: CONTEXT_REF,
    role: 'user',
    kind: 'free_text',
    content: { text: '' },
    created_at: '2026-07-20T10:00:00Z',
    ...patch,
  };
}

beforeEach(() => {
  getMaterialCanvasMock.mockReset().mockResolvedValue(canvas);
  getMaterialParseContextMock.mockReset();
  getWorkspaceMock.mockReset().mockResolvedValue(workspace);
  fetchChatTranscriptMock.mockReset().mockResolvedValue({ rows: [] });
});

describe('知识抽取页进页只读回放', () => {
  it('材料已识别过：读回既有工作区，区5 可用', async () => {
    getMaterialParseContextMock.mockResolvedValue({
      material_ref: MATERIAL_REF,
      parse_context_ref: CONTEXT_REF,
    });
    renderFlow();

    await waitFor(() => expect(getWorkspaceMock).toHaveBeenCalledWith('P-1', CONTEXT_REF));
    expect(getMaterialParseContextMock).toHaveBeenCalledWith('P-1', MATERIAL_REF);
    // 既有知识项显示出来，且区5 输入不再被「尚未识别」判断挡住
    await waitFor(() => expect(screen.getAllByText('系统应支持批量导入').length).toBeGreaterThan(0));
    await waitFor(() => expect(composer().disabled).toBe(false));
    // 留痕水合走的是成功分支：没有错误横幅（裁定 C12——此前这一例一直带着横幅通过）
    await waitFor(() => expect(fetchChatTranscriptMock).toHaveBeenCalledWith('P-1', 'analysis', CONTEXT_REF));
    expect(screen.queryByText(/历史消息读取失败/)).toBeNull();
  });

  it('留痕历史非空：水合进区5 时间线，与本地消息合并且不重复（F8 接线的集成证据）', async () => {
    getMaterialParseContextMock.mockResolvedValue({
      material_ref: MATERIAL_REF,
      parse_context_ref: CONTEXT_REF,
    });
    fetchChatTranscriptMock.mockResolvedValue({
      rows: [
        transcriptRow({ id: 'R-1', role: 'user', kind: 'free_text', content: { text: '这条术语说得清楚吗' } }),
        transcriptRow({
          id: 'R-2',
          role: 'assistant',
          kind: 'command_result',
          content: { text: '已复核：表达完整' },
          created_at: '2026-07-20T10:00:05Z',
        }),
      ],
    });
    renderFlow();

    await waitFor(() => expect(screen.getByText('这条术语说得清楚吗')).toBeTruthy());
    expect(screen.getByText(/已复核：表达完整/)).toBeTruthy();
    expect(screen.queryByText(/历史消息读取失败/)).toBeNull();
    // 空态提示随历史到位而消失（时间线真的有内容了）
    expect(screen.queryByText(/或点下方快捷命令预填/)).toBeNull();
  });

  it('留痕读取失败：给出可见提示并留下可查痕迹（不静默吞）', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    getMaterialParseContextMock.mockResolvedValue({
      material_ref: MATERIAL_REF,
      parse_context_ref: CONTEXT_REF,
    });
    fetchChatTranscriptMock.mockRejectedValue(new Error('transcript down'));
    renderFlow();

    await waitFor(() => expect(screen.getByText(/历史消息读取失败/)).toBeTruthy());
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('材料从未识别过：停在未识别态，不去读工作区', async () => {
    getMaterialParseContextMock.mockResolvedValue({
      material_ref: MATERIAL_REF,
      parse_context_ref: null,
    });
    renderFlow();

    await waitFor(() => expect(getMaterialParseContextMock).toHaveBeenCalled());
    expect(getWorkspaceMock).not.toHaveBeenCalled();
    expect(composer().disabled).toBe(true);
    expect(screen.getByText(/点击区2『识别知识项』/)).toBeTruthy();
  });

  it('带恢复锚点进入：直接用锚点回放，不再多问一次', async () => {
    getMaterialParseContextMock.mockResolvedValue({
      material_ref: MATERIAL_REF,
      parse_context_ref: CONTEXT_REF,
    });
    render(
      <RequirementAnalysisFlow
        initialParseContextRef={CONTEXT_REF}
        materialRef={MATERIAL_REF}
        onBackToIntake={vi.fn()}
        operatorRef="U-1"
        projectId="P-1"
      />,
    );

    await waitFor(() => expect(getWorkspaceMock).toHaveBeenCalledWith('P-1', CONTEXT_REF));
    expect(getMaterialParseContextMock).not.toHaveBeenCalled();
  });

  it('回放查询失败：不打断进页，页面按未识别态呈现', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    getMaterialParseContextMock.mockRejectedValue(new Error('boom'));
    renderFlow();

    await waitFor(() => expect(warn).toHaveBeenCalled());
    expect(getWorkspaceMock).not.toHaveBeenCalled();
    expect(composer().disabled).toBe(true);
    warn.mockRestore();
  });
});
