/**
 * 条目评审页结论卡组件用例（T20260720-review-point-veto-and-edit）。
 *
 * 卡面以**问题**为唯一列表单元（2026-07-20 用户走查 REQ-003 后重设计）：AI 给的改法挂在它
 * 所针对的问题块里，不再与问题并排成第二个列表；没有勾选框；每个问题二选一——在文本框里写
 * 改后的文字，或标「这不是问题」。AI 没给改法的问题给整条重写框，与局部改法互斥。
 *
 * 领域规则在后端（backend/tests/test_item_review_veto.py），本文件只测 UI 编排。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const getWorkspaceMock = vi.fn();
const adjudicateVerdictMock = vi.fn();
const recordFindingVetoMock = vi.fn();
const confirmItemMock = vi.fn();
const applyItemRevisionMock = vi.fn();
vi.mock('../src/api/item-review', () => ({
  itemReviewApi: {
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    reviewDialogue: vi.fn(),
    reviewDialogueStream: vi.fn(),
    startDiagnosis: vi.fn(),
    adjudicateVerdict: (...args: unknown[]) => adjudicateVerdictMock(...args),
    recordFindingVeto: (...args: unknown[]) => recordFindingVetoMock(...args),
    confirmItem: (...args: unknown[]) => confirmItemMock(...args),
  },
}));
vi.mock('../src/api/quality', () => ({
  qualityApi: { getItemQuality: vi.fn(() => Promise.reject(new Error('no quality projection'))) },
}));
vi.mock('../src/api/requirements', () => ({
  requirementsApi: { applyItemRevision: (...args: unknown[]) => applyItemRevisionMock(...args) },
}));

import type { ItemFormationWorkspaceRead } from '../src/api/item-formation';
import type {
  ItemReviewWorkspaceRead,
  ReviewFindingRead,
  ReviewRequirementItemRead,
  RevisionPointRead,
  VerdictRead,
} from '../src/api/item-review';
import { itemReviewWorkspaceFixture } from '../src/fixtures/item-review';
import {
  buildVerdictProblems,
  collectEditedPointTrail,
  receiptText,
} from '../src/view-models/requirement-item-review';
import { RequirementItemReviewFlow } from '../src/workbenches/RequirementItemReviewFlow';

const EXPRESSION = '系统应尽快完成导出，且超时不得发生';

/** 问题①：有 AI 改法 */
const FINDING_A: ReviewFindingRead = {
  finding_ref: 'F-A', finding_type: 'untestable',
  diagnosis_summary: '「尽快」不可测。', basis_summary: '来源无时限口径',
  rule_code: 'INCOSE-R7', evidence_span: '尽快', severity: 'medium',
  can_veto: true, vetoed: false,
};
/** 问题②：AI 只报了问题，没给改法（REQ-003 的问题②就是这种） */
const FINDING_B: ReviewFindingRead = {
  finding_ref: 'F-B', finding_type: 'missing_field',
  diagnosis_summary: '超时阈值未定义。', basis_summary: '来源无阈值',
  rule_code: 'SMELL-UNDEF', evidence_span: '超时', severity: 'medium',
  can_veto: true, vetoed: false,
};
const POINT_A: RevisionPointRead = {
  point_ref: 'P1', label: '量化时限', finding_index: 0, finding_ref: 'F-A',
  find: '尽快', replace: '在三秒内', basis: '来源要求可测',
};
const POINT_B: RevisionPointRead = {
  point_ref: 'P2', label: '补超时阈值', finding_index: 1, finding_ref: 'F-B',
  find: '超时', replace: '超过十秒', basis: '来源要求阈值',
};

function verdict(overrides: Partial<VerdictRead> = {}): VerdictRead {
  return {
    round_ref: 'R-1', round_no: 1, batch_ref: 'B-1', item_ref: 'IT-1',
    diagnosis_mode: 'standard', trigger: 'user_submit', status: 'completed',
    verdict_kind: 'revise', verdict_summary: '表达存在待改问题，建议修订。',
    findings: [FINDING_A, FINDING_B],
    revision_points: [POINT_A, POINT_B],
    supplement_gaps: [], context_coverage: '', model_result_refs: [],
    invalidated: false, superseded_by: null, adjudication: null,
    overridden: false, confirm_result: null, effective: true, created_at: '2026-07-20T02:00:00Z',
    blocking_finding_count: 2, all_blocking_findings_vetoed: false,
    ...overrides,
  };
}

/**
 * 服务端 affordance 的替身：确认入口的可用性由后端算好下发（`confirm_without_override`），
 * 前端只消费不自算（C14(b)）。这里照后端口径造出来，好让组件走真实的读法。
 */
function serverActions(v: VerdictRead): ReviewRequirementItemRead['available_actions'] {
  const cleared = v.verdict_kind === 'revise' && v.all_blocking_findings_vetoed === true;
  return [{
    key: 'confirm_without_override',
    enabled: cleared,
    disabled_reason: cleared ? null : '本轮还有你没处理的问题',
  }];
}

function workspaceWith(
  v: VerdictRead,
  vetoes: ReviewRequirementItemRead['finding_vetoes'] = [],
  actions: ReviewRequirementItemRead['available_actions'] = serverActions(v),
): ItemReviewWorkspaceRead {
  const base = itemReviewWorkspaceFixture.review_items[0];
  const item: ReviewRequirementItemRead = {
    ...base,
    item_ref: 'IT-1', req_no: 'REQ-101', expression: EXPRESSION,
    review_status: 'awaiting_adjudication',
    display_code: 'awaiting_adjudication',
    display_note: '当前结论：建议修订，待你裁决。',
    current_verdict: v,
    verdict_history: [],
    dialogue_messages: [],
    supplement_gaps_open: [],
    finding_vetoes: vetoes,
    available_actions: actions,
  };
  return { ...itemReviewWorkspaceFixture, workspace_version: '3', review_items: [item] };
}

function renderFlow() {
  return render(
    <RequirementItemReviewFlow
      projectId="P-1"
      operatorRef="U-1"
      sourceWorkspace={{ formation_context_ref: 'CTX-1', pending_items: [] } as unknown as ItemFormationWorkspaceRead}
      onBackToFormation={vi.fn()}
      onBackToMaintenance={vi.fn()}
    />,
  );
}

/** 逐个问题块（按类名取——区5 里还有别的卡） */
function problemBlocks(zone5: HTMLElement): HTMLElement[] {
  return Array.from(zone5.querySelectorAll<HTMLElement>('.rv5-problem'));
}

/** 改法/整条重写输入框（区5 里还有 ChatWidget 的对话输入框，故按类名取） */
function editBoxes(root: HTMLElement): HTMLTextAreaElement[] {
  return Array.from(root.querySelectorAll<HTMLTextAreaElement>('textarea.rv5-edit__box'));
}

async function verdictCard(): Promise<HTMLElement> {
  const zone5 = screen.getByLabelText('区5 条目评审操作与确认');
  await waitFor(() => expect(within(zone5).getByText('表达存在待改问题，建议修订。')).toBeInTheDocument());
  return zone5;
}

beforeEach(() => {
  getWorkspaceMock.mockReset();
  adjudicateVerdictMock.mockReset();
  recordFindingVetoMock.mockReset();
  confirmItemMock.mockReset();
  applyItemRevisionMock.mockReset();
});

describe('卡面结构：以问题为唯一单元（走查意见①②的回归）', () => {
  it('两个问题一个改法 → 两个问题块，改法在块内，不另起第三行，全卡无勾选框', async () => {
    // 这正是 REQ-003 的形状：问题①有改法、问题②没有。旧卡把改法排成第三行，
    // 用户读到「三个问题」，而区4 质量诊断卡只列两个——数量看着对不上。
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({ revision_points: [POINT_A] })));
    renderFlow();
    const zone5 = await verdictCard();

    const blocks = problemBlocks(zone5);
    expect(blocks).toHaveLength(2);
    expect(within(zone5).getByText(/发现 2 个问题/)).toBeInTheDocument();
    // 改法落在问题①块里，不是独立一行
    expect(blocks[0].textContent).toContain('「尽快」不可测。');
    expect(blocks[0].textContent).toContain('AI 建议把「尽快」改成：');
    expect(blocks[1].textContent).toContain('超时阈值未定义。');
    expect(blocks[1].textContent).toContain('AI 没有给出改法');
    // 勾选框已随重设计撤除
    expect(zone5.querySelectorAll('.rv5-problem input[type=checkbox]')).toHaveLength(0);
  });

  it('「未发现阻断问题」不当问题列出来（它属于结论摘要）', async () => {
    const noBlocker: ReviewFindingRead = {
      finding_ref: 'F-OK', finding_type: 'no_blocker',
      diagnosis_summary: '来源依据可定位，未发现来源断裂。', basis_summary: '',
      severity: 'medium', can_veto: false, vetoed: false,
    };
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({
      findings: [noBlocker, FINDING_A], revision_points: [POINT_A],
    })));
    renderFlow();
    const zone5 = await verdictCard();
    expect(problemBlocks(zone5)).toHaveLength(1);
    expect(within(zone5).getByText(/发现 1 个问题/)).toBeInTheDocument();
  });
});

describe('逐条否决（AEP-116）', () => {
  it('可指纹化的问题给「这不是问题」入口；填理由后直发否决端点', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict()));
    recordFindingVetoMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    const buttons = within(zone5).getAllByRole('button', { name: '这不是问题' });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0]);

    const modal = await screen.findByRole('dialog');
    fireEvent.change(within(modal).getByRole('textbox'), { target: { value: '业务上就是这么说的' } });
    fireEvent.click(within(modal).getByRole('button', { name: '标记为不是问题' }));

    await waitFor(() => expect(recordFindingVetoMock).toHaveBeenCalledTimes(1));
    expect(recordFindingVetoMock.mock.calls[0][1]).toMatchObject({
      item_ref: 'IT-1', action: 'veto', finding_ref: 'F-A', reason: '业务上就是这么说的',
    });
  });

  it('后端说不可标记（can_veto=false）就不给入口——前端不自算', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({
      findings: [{ ...FINDING_A, can_veto: false }, FINDING_B],
    })));
    renderFlow();
    const zone5 = await verdictCard();
    expect(within(zone5).getAllByRole('button', { name: '这不是问题' })).toHaveLength(1);
  });

  it('已标记的块显示标记与理由、收起输入框，并可「重新计入」撤销', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({
      findings: [{ ...FINDING_A, vetoed: true, veto_ref: 'V-1', veto_reason: '业务已确认' }, FINDING_B],
      revision_points: [{ ...POINT_A, vetoed: true }, POINT_B],
      blocking_finding_count: 1,
    })));
    recordFindingVetoMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    const [first] = problemBlocks(zone5);
    expect(first.textContent).toContain('你已标为不是问题：业务已确认');
    expect(editBoxes(first)).toHaveLength(0);  // 已否决的问题不再给改法输入框
    fireEvent.click(within(first).getByRole('button', { name: '重新计入' }));

    await waitFor(() => expect(recordFindingVetoMock).toHaveBeenCalledTimes(1));
    expect(recordFindingVetoMock.mock.calls[0][1]).toMatchObject({ action: 'restore', veto_ref: 'V-1' });
  });

  it('被标记问题的改法不参与合成，也不进提交载荷', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({
      findings: [{ ...FINDING_A, vetoed: true, veto_ref: 'V-1' }, FINDING_B],
      revision_points: [{ ...POINT_A, vetoed: true }, POINT_B],
      blocking_finding_count: 1,
    })));
    adjudicateVerdictMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    expect(within(zone5).getByText('系统应尽快完成导出，且超过十秒不得发生')).toBeInTheDocument();
    fireEvent.click(within(zone5).getByRole('button', { name: /按上面的内容修改/ }));
    await waitFor(() => expect(adjudicateVerdictMock).toHaveBeenCalledTimes(1));
    expect(adjudicateVerdictMock.mock.calls[0][1].selected_point_refs).toEqual(['P2']);
  });
});

describe('全部问题被标记后的确认出口', () => {
  it('结论卡出「确认这个条目」，走 confirmItem 且 override=false', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({
      findings: [
        { ...FINDING_A, vetoed: true, veto_ref: 'V-1' },
        { ...FINDING_B, vetoed: true, veto_ref: 'V-2' },
      ],
      revision_points: [{ ...POINT_A, vetoed: true }, { ...POINT_B, vetoed: true }],
      blocking_finding_count: 0, all_blocking_findings_vetoed: true,
    })));
    confirmItemMock.mockResolvedValue({ status: 'confirmed', item_ref: 'IT-1', item_status: 'confirmed', next_action: null });
    renderFlow();
    const zone5 = await verdictCard();

    expect(within(zone5).getByText(/这一轮提的问题你都标成了不是问题/)).toBeInTheDocument();
    fireEvent.click(within(zone5).getByRole('button', { name: '确认这个条目' }));

    await waitFor(() => expect(confirmItemMock).toHaveBeenCalledTimes(1));
    expect(confirmItemMock.mock.calls[0][1]).toMatchObject({
      item_ref: 'IT-1', override: false, reason: null, workspace_version: '3',
    });
  });

  it('还有问题成立时不出这个按钮', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();
    expect(within(zone5).queryByRole('button', { name: '确认这个条目' })).toBeNull();
  });

  it('C44：确认写成功但随后刷新失败时，说的是「已确认」加取不到数据，不是确认失败', async () => {
    const cleared = verdict({
      findings: [
        { ...FINDING_A, vetoed: true, veto_ref: 'V-1' },
        { ...FINDING_B, vetoed: true, veto_ref: 'V-2' },
      ],
      revision_points: [{ ...POINT_A, vetoed: true }, { ...POINT_B, vetoed: true }],
      blocking_finding_count: 0, all_blocking_findings_vetoed: true,
    });
    // 首次加载成功，确认之后的那次刷新失败（网络抖动）
    getWorkspaceMock
      .mockResolvedValueOnce(workspaceWith(cleared))
      .mockRejectedValue(new Error('Network Error'));
    confirmItemMock.mockResolvedValue({ status: 'confirmed', item_ref: 'IT-1', item_status: 'confirmed', next_action: null });
    renderFlow();
    const zone5 = await verdictCard();

    fireEvent.click(within(zone5).getByRole('button', { name: '确认这个条目' }));
    await waitFor(() => expect(confirmItemMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/条目已确认/)).toBeInTheDocument());
    expect(screen.getByText(/页面数据没取回来/)).toBeInTheDocument();
    expect(screen.queryByText(/Network Error/)).toBeNull();  // 刷新的错误不冒充写入失败
  });
});

describe('在问题块里写改后的文字', () => {
  it('改法框预填 AI 原案；改后合成预览跟着变，提交只带真正改过的点', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict()));
    adjudicateVerdictMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    const boxes = editBoxes(zone5);
    expect(boxes.map((b) => b.value)).toEqual(['在三秒内', '超过十秒']);

    fireEvent.change(boxes[0], { target: { value: '在两秒内' } });
    await waitFor(() =>
      expect(within(zone5).getByText('系统应在两秒内完成导出，且超过十秒不得发生')).toBeInTheDocument(),
    );

    fireEvent.click(within(zone5).getByRole('button', { name: /按上面的内容修改/ }));
    await waitFor(() => expect(adjudicateVerdictMock).toHaveBeenCalledTimes(1));
    const [, command] = adjudicateVerdictMock.mock.calls[0];
    expect(command.selected_point_refs).toEqual(['P1', 'P2']);  // 无勾选框＝未标记的全部参与
    expect(command.point_edits).toEqual({ P1: '在两秒内' });      // P2 没改，不带
  });

  it('一处都没改时不带改稿字段，行为与本能力引入前一致', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict()));
    adjudicateVerdictMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    fireEvent.click(within(zone5).getByRole('button', { name: /按上面的内容修改/ }));
    await waitFor(() => expect(adjudicateVerdictMock).toHaveBeenCalledTimes(1));
    expect(adjudicateVerdictMock.mock.calls[0][1].point_edits).toBeNull();
  });

  it('改成空文本时提交禁用并说明怎么办（没有「先不改」出口了）', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    fireEvent.change(editBoxes(zone5)[0], { target: { value: '   ' } });
    await waitFor(() =>
      expect(within(zone5).getByRole('button', { name: /按上面的内容修改/ })).toBeDisabled(),
    );
    expect(within(zone5).getByText(/有一处你改成了空的/)).toBeInTheDocument();
  });

  it('改过之后可以一键还原成 AI 写的', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    const boxes = editBoxes(zone5);
    fireEvent.change(boxes[0], { target: { value: '在两秒内' } });
    fireEvent.click(await within(zone5).findByRole('button', { name: '还原成 AI 写的' }));
    await waitFor(() => expect(boxes[0].value).toBe('在三秒内'));
  });
});

describe('AI 没给改法的问题：整条重写', () => {
  /** 问题②无改法（只给 POINT_A），故第二块是整条重写框 */
  const noFixForB = verdict({ revision_points: [POINT_A] });

  it('整条重写框预填条目当前表达，并说明不动它就是没处理', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(noFixForB));
    renderFlow();
    const zone5 = await verdictCard();

    const second = problemBlocks(zone5)[1];
    expect(editBoxes(second)[0].value).toBe(EXPRESSION);
    expect(second.textContent).toContain('不动它就是这一处你还没处理');
  });

  it('一旦整条重写有改动：其余输入框只读、卡面说明、按钮改口', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(noFixForB));
    renderFlow();
    const zone5 = await verdictCard();

    const [first, second] = problemBlocks(zone5);
    fireEvent.change(editBoxes(second)[0], { target: { value: '系统应在三秒内完成导出，且超过十秒不得发生' } });

    await waitFor(() => expect(editBoxes(first)[0]).toHaveAttribute('readonly'));
    expect(first.className).toContain('rv5-problem--locked');
    expect(within(zone5).getByText(/你正在整条重写，上面那些局部改法就不应用了/)).toBeInTheDocument();
    expect(within(zone5).getByRole('button', { name: '按你写的整条替换' })).toBeEnabled();
    // 合成预览直接显示整条重写的内容（同一段文字也在输入框里，故限定在预览块内断言）
    expect(zone5.querySelector('.rv5-compose')?.textContent)
      .toContain('系统应在三秒内完成导出，且超过十秒不得发生');
  });

  it('「取消整条重写」把局部改法放回来', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(noFixForB));
    renderFlow();
    const zone5 = await verdictCard();

    const [first, second] = problemBlocks(zone5);
    fireEvent.change(editBoxes(second)[0], { target: { value: '改成别的' } });
    await waitFor(() => expect(editBoxes(first)[0]).toHaveAttribute('readonly'));

    fireEvent.click(within(second).getByRole('button', { name: '取消整条重写' }));
    await waitFor(() => expect(editBoxes(first)[0]).not.toHaveAttribute('readonly'));
    expect(within(zone5).getByRole('button', { name: /按上面的内容修改/ })).toBeInTheDocument();
  });

  it('提交整条重写走人工修订通道（整条替换），不走结论采纳', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(noFixForB));
    applyItemRevisionMock.mockResolvedValue({ status: 'applied', next_action: null });
    renderFlow();
    const zone5 = await verdictCard();

    const second = problemBlocks(zone5)[1];
    fireEvent.change(editBoxes(second)[0], { target: { value: '系统应在三秒内完成导出，且超过十秒不得发生' } });
    fireEvent.click(await within(zone5).findByRole('button', { name: '按你写的整条替换' }));

    await waitFor(() => expect(applyItemRevisionMock).toHaveBeenCalledTimes(1));
    expect(applyItemRevisionMock.mock.calls[0][2]).toMatchObject({
      item_ref: 'IT-1', revision_mode: 'manual', field_key: 'expression',
      revised_value: '系统应在三秒内完成导出，且超过十秒不得发生',
    });
    expect(adjudicateVerdictMock).not.toHaveBeenCalled();
  });
});

describe('问题块投影 buildVerdictProblems', () => {
  it('改法按 finding_ref 归到它针对的问题（读出序与模型输出序错开时仍正确）', () => {
    const { problems, orphanFixes } = buildVerdictProblems({
      findings: [FINDING_B, FINDING_A],                       // 读出序与下面的 finding_index 错开
      revision_points: [{ ...POINT_A, finding_index: 0 }],    // 下标指向 F-B，引用指向 F-A
    });
    expect(problems.map((p) => p.findingRef)).toEqual(['F-B', 'F-A']);
    expect(problems[0].fixes).toEqual([]);                    // 不该按下标挂到 F-B 上
    expect(problems[1].fixes.map((f) => f.point_ref)).toEqual(['P1']);
    expect(orphanFixes).toEqual([]);
  });

  it('存量轮次没有引用时才回退按下标配对', () => {
    const { problems } = buildVerdictProblems({
      findings: [FINDING_A, FINDING_B],
      revision_points: [{ ...POINT_A, finding_ref: null, finding_index: 1 }],
    });
    expect(problems[0].fixes).toEqual([]);
    expect(problems[1].fixes.map((f) => f.point_ref)).toEqual(['P1']);
  });

  it('no_blocker 不进问题列表；归不到问题的改法单独返回不静默丢弃', () => {
    const noBlocker: ReviewFindingRead = {
      finding_ref: 'F-OK', finding_type: 'no_blocker',
      diagnosis_summary: '未发现阻断。', basis_summary: '', severity: 'medium',
    };
    const { problems, orphanFixes } = buildVerdictProblems({
      findings: [noBlocker, FINDING_A],
      revision_points: [POINT_A, { ...POINT_B, finding_ref: 'F-GONE', finding_index: 9 }],
    });
    expect(problems.map((p) => p.findingRef)).toEqual(['F-A']);
    expect(orphanFixes.map((p) => p.point_ref)).toEqual(['P2']);
  });

  it('一个问题可以挂多条改法', () => {
    const { problems } = buildVerdictProblems({
      findings: [FINDING_A],
      revision_points: [POINT_A, { ...POINT_B, finding_ref: 'F-A' }],
    });
    expect(problems[0].fixes.map((f) => f.point_ref)).toEqual(['P1', 'P2']);
  });
});

describe('留痕投影 collectEditedPointTrail', () => {
  const adjudicated = verdict({
    round_ref: 'R-1', round_no: 1, effective: false,
    adjudication: {
      decision: 'adopted', selected_point_refs: ['P1'], excluded_point_refs: ['P2'],
      point_edits: { P1: '在两秒内' }, reason: null, operator_ref: 'U-1', at: '2026-07-20T03:00:00Z',
    },
  });

  it('把 AI 原案与用户终稿配成一行（原案取自不可变的修订点列）', () => {
    expect(collectEditedPointTrail({ current_verdict: null, verdict_history: [adjudicated] })).toEqual([
      { key: 'R-1:P1', roundNo: 1, label: '量化时限', aiText: '在三秒内', userText: '在两秒内' },
    ]);
  });

  it('没改过稿的轮次不产生行', () => {
    const plain = verdict({
      adjudication: {
        decision: 'adopted', selected_point_refs: ['P1'], excluded_point_refs: [],
        point_edits: {}, reason: null, operator_ref: 'U-1', at: '',
      },
    });
    expect(collectEditedPointTrail({ current_verdict: null, verdict_history: [plain] })).toEqual([]);
  });

  it('多轮时新的在前', () => {
    const second = verdict({
      round_ref: 'R-2', round_no: 2,
      adjudication: {
        decision: 'adopted', selected_point_refs: ['P2'], excluded_point_refs: [],
        point_edits: { P2: '超过五秒' }, reason: null, operator_ref: 'U-1', at: '',
      },
    });
    const rows = collectEditedPointTrail({ current_verdict: null, verdict_history: [adjudicated, second] });
    expect(rows.map((r) => r.roundNo)).toEqual([2, 1]);
  });
});

// ============================================================================
// T20260721 第二档加固：界面口径与门禁一致性
// ============================================================================

describe('第二档加固：收折回执、孤儿改法、整条重写互斥、确认 affordance', () => {
  it('C7：否决消解后的直接确认，回执写「已确认」而不是红字「已拒绝」', () => {
    const confirmed = verdict({
      effective: false, confirm_result: 'confirmed',
      adjudication: {
        decision: 'rejected', selected_point_refs: [], excluded_point_refs: [],
        reason: '本轮建议已被逐条裁定为不是问题（2 条）',
        operator_ref: 'U-1', at: '2026-07-21T02:00:00Z',
      },
    });
    const receipt = receiptText(confirmed);
    expect(receipt.tone).toBe('success');
    expect(receipt.text).toContain('已确认');
    expect(receipt.text).not.toContain('已拒绝');

    // 真的拒绝仍然是拒绝（没有把两件事混成一件）
    const rejected = verdict({
      effective: false, confirm_result: null,
      adjudication: {
        decision: 'rejected', selected_point_refs: [], excluded_point_refs: [],
        reason: '这轮结论不成立', operator_ref: 'U-1', at: '',
      },
    });
    expect(receiptText(rejected).tone).toBe('danger');
    expect(receiptText(rejected).text).toContain('已拒绝');
  });

  it('C15：归不到任何问题的改法不进提交载荷，脚注说明它不会被应用', async () => {
    const orphan: RevisionPointRead = {
      point_ref: 'P9', label: '来路不明的改法', finding_index: 9, finding_ref: 'F-GONE',
      find: '不得发生', replace: '不得出现', basis: '',
    };
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({
      revision_points: [POINT_A, POINT_B, orphan],
    })));
    adjudicateVerdictMock.mockResolvedValue(workspaceWith(verdict()));
    renderFlow();
    const zone5 = await verdictCard();

    expect(within(zone5).getByText(/不会被应用/)).toBeInTheDocument();
    // 合成预览里也没有它（「不得发生」原样留着）
    expect(within(zone5).getByText('系统应在三秒内完成导出，且超过十秒不得发生')).toBeInTheDocument();

    fireEvent.click(within(zone5).getByRole('button', { name: /按上面的内容修改/ }));
    await waitFor(() => expect(adjudicateVerdictMock).toHaveBeenCalledTimes(1));
    expect(adjudicateVerdictMock.mock.calls[0][1].selected_point_refs).toEqual(['P1', 'P2']);
  });

  it('C24：整条重写生效后，此前清空的局部改法框不再禁用提交', async () => {
    getWorkspaceMock.mockResolvedValue(workspaceWith(verdict({ revision_points: [POINT_A] })));
    renderFlow();
    const zone5 = await verdictCard();

    const boxes = editBoxes(zone5);
    fireEvent.change(boxes[0], { target: { value: '   ' } });  // 问题①的改法框清空
    await waitFor(() =>
      expect(within(zone5).getByRole('button', { name: /按上面的内容修改/ })).toBeDisabled(),
    );
    // 转而在问题②（AI 没给改法）的整条重写框里写字 → 局部改法一律不应用，不该再禁用提交
    fireEvent.change(boxes[1], { target: { value: '系统应在三秒内完成导出，且超过十秒不得出现' } });
    await waitFor(() =>
      expect(within(zone5).getByRole('button', { name: /整条替换/ })).toBeEnabled(),
    );
    expect(within(zone5).queryByText(/有一处你改成了空的/)).toBeNull();
  });

  it('C14(b)：后端说确认入口不可用时，前端不自作主张亮出按钮', async () => {
    // 阻断问题已全部标记（前端旧口径会亮按钮），但后端因「诊断进行中」判它不可用
    const cleared = verdict({
      findings: [
        { ...FINDING_A, vetoed: true, veto_ref: 'V-1' },
        { ...FINDING_B, vetoed: true, veto_ref: 'V-2' },
      ],
      blocking_finding_count: 0, all_blocking_findings_vetoed: true,
    });
    getWorkspaceMock.mockResolvedValue(workspaceWith(cleared, [], [
      { key: 'confirm_without_override', enabled: false, disabled_reason: '诊断进行中，请等待本轮结束' },
    ]));
    renderFlow();
    const zone5 = await verdictCard();
    expect(within(zone5).queryByRole('button', { name: '确认这个条目' })).toBeNull();
  });
});
