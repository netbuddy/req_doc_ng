/**
 * 条目评审页·人工确认后的两处呈现（T20260721-attested-diagnosis-context）。
 *
 * A3 降格：条目的来源缺口已由人工确认闭合后，AI 仍报出的来源对齐类发现不再是要用户处理的
 * 问题。降格判定在后端读投影（source_attested），本文件只测前端有没有如实照它呈现——
 * 前端一律不自己判断哪条该降格。
 * A4 醒目化：背书刚闭合缺口的那一刻，状态说明句以横幅呈现；文案逐字取后端 display_note，
 * 前端不造第二套；判据取后端 attestation_closed_gap，不匹配文本、也不看「背书过没有」。
 *
 * 领域规则在后端（backend/tests/test_item_review_attested_diagnosis.py），本文件只测 UI 编排。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const getWorkspaceMock = vi.fn();
const recordFindingVetoMock = vi.fn();
vi.mock('../src/api/item-review', () => ({
  itemReviewApi: {
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    reviewDialogue: vi.fn(),
    reviewDialogueStream: vi.fn(),
    startDiagnosis: vi.fn(),
    adjudicateVerdict: vi.fn(),
    attestSource: vi.fn(),
    recordFindingVeto: (...args: unknown[]) => recordFindingVetoMock(...args),
    confirmItem: vi.fn(),
  },
}));
vi.mock('../src/api/quality', () => ({
  qualityApi: { getItemQuality: vi.fn(() => Promise.reject(new Error('no quality projection'))) },
}));

import type { ItemFormationWorkspaceRead } from '../src/api/item-formation';
import type {
  ItemReviewWorkspaceRead,
  ReviewFindingRead,
  ReviewRequirementItemRead,
} from '../src/api/item-review';
import { itemReviewWorkspaceFixture } from '../src/fixtures/item-review';
import { buildVerdictProblems } from '../src/view-models/requirement-item-review';
import { RequirementItemReviewFlow } from '../src/workbenches/RequirementItemReviewFlow';

const ATTESTATION = {
  record_ref: 'RV-ATT',
  reason: '客户在启动会上口头提出，会议纪要漏记了这一条',
  operator_ref: 'U-1',
  at: '2026-07-25T02:00:00Z',
};

const ATTEST_NOTE = '来源缺口已由人工确认闭合（材料未记载该需求）；可重新诊断。';

function finding(over: Partial<ReviewFindingRead> = {}): ReviewFindingRead {
  return {
    finding_ref: 'F-1',
    finding_type: 'source_inconsistency',
    diagnosis_summary: '表达与来源要素讲的不是同一件事。',
    basis_summary: '来源要素引文：「下单后通知用户」',
    rule_code: 'SRC-DRIFT',
    severity: 'medium',
    evidence_span: '人工审核',
    ...over,
  };
}

/** 背书刚闭合缺口、尚未重新诊断的条目（横幅场景）。 */
function attestedPendingWorkspace(over: Partial<ReviewRequirementItemRead> = {}): ItemReviewWorkspaceRead {
  const base = itemReviewWorkspaceFixture.review_items[0];
  const item: ReviewRequirementItemRead = {
    ...base,
    item_ref: 'IT-ATT',
    req_no: 'REQ-006',
    expression: '大额订单需人工审核',
    review_status: 'no_verdict',
    display_code: 'pending_diagnosis',
    display_note: ATTEST_NOTE,
    current_verdict: null,
    verdict_history: [],
    dialogue_messages: [],
    supplement_gaps_open: [],
    source_attestation: ATTESTATION,
    attestation_closed_gap: true,
    available_actions: [],
    ...over,
  };
  return { ...itemReviewWorkspaceFixture, workspace_version: '4', review_items: [item] };
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

beforeEach(() => {
  getWorkspaceMock.mockReset();
  recordFindingVetoMock.mockReset();
});

describe('A3 降格投影 buildVerdictProblems', () => {
  it('后端标了 source_attested 的发现项不进问题列表，单列为提示', () => {
    const { problems, attestedNotices } = buildVerdictProblems({
      findings: [
        finding({ finding_ref: 'F-SRC', source_attested: true }),
        finding({ finding_ref: 'F-VAG', finding_type: 'untestable', diagnosis_summary: '「尽快」不可测。' }),
      ],
      revision_points: [],
    });

    expect(problems.map((p) => p.findingRef)).toEqual(['F-VAG']);
    expect(attestedNotices.map((n) => n.findingRef)).toEqual(['F-SRC']);
    expect(attestedNotices[0].sourceAttested).toBe(true);
  });

  it('挂在降格提示上的改法不算悬空——它有明确归属，只是那条不需要处理', () => {
    const point = {
      point_ref: 'P1', label: '对齐来源', finding_index: 0, finding_ref: 'F-SRC',
      find: '大额订单', replace: '金额超过 500 元的订单', basis: '来源要素引文', group: null,
    };
    const { problems, orphanFixes, attestedNotices } = buildVerdictProblems({
      findings: [finding({ finding_ref: 'F-SRC', source_attested: true })],
      revision_points: [point],
    });

    expect(problems).toHaveLength(0);
    expect(orphanFixes).toHaveLength(0);   // 不得被报成「对应不到任何问题」
    expect(attestedNotices[0].fixes.map((p) => p.point_ref)).toEqual(['P1']);
  });

  it('没有背书标记时口径不变：来源类发现照常是问题（降格的因是背书，不是发现项本身）', () => {
    const { problems, attestedNotices } = buildVerdictProblems({
      findings: [finding({ finding_ref: 'F-SRC' })],
      revision_points: [],
    });

    expect(problems.map((p) => p.findingRef)).toEqual(['F-SRC']);
    expect(problems[0].sourceAttested).toBe(false);
    expect(attestedNotices).toHaveLength(0);
  });
});

describe('A4 说明句醒目化', () => {
  it('背书刚闭合缺口：说明句以横幅呈现，文本与后端 display_note 逐字一致', async () => {
    getWorkspaceMock.mockResolvedValue(attestedPendingWorkspace());
    renderFlow();

    const banner = await screen.findByRole('status');
    expect(banner).toHaveTextContent(ATTEST_NOTE);
    // 界面用语纪律：对用户说「人工确认」，不说「背书」
    expect(banner).toHaveTextContent('人工确认');
    expect(banner.textContent).not.toContain('背书');
  });

  it('背书之后又做过普通修订：不再是「来源缺口刚闭合」，横幅撤走（说明句照旧显示）', async () => {
    getWorkspaceMock.mockResolvedValue(attestedPendingWorkspace({
      attestation_closed_gap: false,
      display_note: '条目已修订，旧结论已失效；可重新诊断。',
    }));
    renderFlow();

    await waitFor(() => expect(getWorkspaceMock).toHaveBeenCalled());
    // 说明句在「当前结论」条与区4 空态两处同源呈现，故用 findAll
    expect(await screen.findAllByText('条目已修订，旧结论已失效；可重新诊断。')).not.toHaveLength(0);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('从未背书过的条目：状态区呈现零变化（不出横幅）', async () => {
    getWorkspaceMock.mockResolvedValue(attestedPendingWorkspace({
      source_attestation: null,
      attestation_closed_gap: false,
      display_note: '可发起诊断。',
    }));
    renderFlow();

    await waitFor(() => expect(getWorkspaceMock).toHaveBeenCalled());
    expect(await screen.findAllByText('可发起诊断。')).not.toHaveLength(0);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

describe('重复人工确认：出处缺口只闭合一次', () => {
  /** 已确认过、又被判回「待补充来源」的条目（缺的是具体值，不是出处）。 */
  function attestedSupplementWorkspace(): ItemReviewWorkspaceRead {
    return attestedPendingWorkspace({
      display_code: 'supplement_pending',
      display_note: '来源缺口未闭合，补充来源或修订表达后可再诊断。',
      attestation_closed_gap: false,
      supplement_gaps_open: ['需向财务确认月度对账报表的导出格式与必含字段清单。'],
      available_actions: [
        {
          key: 'attest_source',
          enabled: false,
          disabled_reason: '这条已经人工确认过来源了；本轮缺的是具体口径，人工确认提供不了这些值',
        },
      ],
    });
  }

  it('已确认过就不再摆〔人工确认〕，改说清原因并指向真正能解决的路', async () => {
    getWorkspaceMock.mockResolvedValue(attestedSupplementWorkspace());
    renderFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    expect(within(exit).queryByRole('button', { name: '人工确认' })).not.toBeInTheDocument();
    // 只把按钮藏掉、不说为什么，用户只会觉得功能没了
    expect(within(exit).getByText(/已经人工确认过来源了/)).toBeInTheDocument();
    expect(within(exit).getByText(/把缺的口径直接写进条目表达/)).toBeInTheDocument();
    // 其余出口不受影响
    expect(within(exit).getByRole('button', { name: '撤回该条' })).toBeInTheDocument();
    expect(within(exit).getByRole('button', { name: '按说明查找' })).toBeInTheDocument();
  });

  it('没确认过的条目照常给入口（别把功能整个关掉）', async () => {
    getWorkspaceMock.mockResolvedValue(attestedPendingWorkspace({
      source_attestation: null,
      attestation_closed_gap: false,
      display_code: 'supplement_pending',
      display_note: '来源缺口未闭合，补充来源或修订表达后可再诊断。',
      supplement_gaps_open: ['需补充该需求的正式来源文档。'],
      available_actions: [{ key: 'attest_source', enabled: true, disabled_reason: null }],
    }));
    renderFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    expect(within(exit).getByRole('button', { name: '人工确认' })).toBeInTheDocument();
  });
});

describe('理由必填：不给空点击（走查发现）', () => {
  it('〔撤回该条〕理由未填时确认按钮不可点，填了才放行', async () => {
    getWorkspaceMock.mockResolvedValue(attestedPendingWorkspace({
      display_code: 'supplement_pending',
      display_note: '来源缺口未闭合，补充来源或修订表达后可再诊断。',
      attestation_closed_gap: false,
      supplement_gaps_open: ['需向财务确认导出格式与必含字段。'],
      available_actions: [{ key: 'attest_source', enabled: false, disabled_reason: '这条已经人工确认过来源了' }],
    }));
    renderFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    fireEvent.click(within(exit).getByRole('button', { name: '撤回该条' }));

    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getAllByRole('button')
      .find((b) => b.textContent?.replace(/\s/g, '') === '撤回该条')!;
    // 空理由不放行：否则命令带着空理由发出去，后端的追问只落在区2 一行灰字里，
    // 用户在区5 点的按钮看上去毫无反应（2026-07-25 走查实测两次）
    expect(confirm).toBeDisabled();

    fireEvent.change(within(dialog).getByRole('textbox'), {
      target: { value: '这条口径始终补不齐，先撤回' },
    });
    expect(confirm).toBeEnabled();
  });
});


// ---- V4：降格提示在界面上真的被渲染出来（冷审查补测） ----

/** 站立「建议修订」结论：一条降格的来源类发现（可选再带一条真问题）。 */
function attestedVerdictWorkspace(opts: {
  withRealProblem?: boolean;
  vetoedNotice?: boolean;
} = {}): ItemReviewWorkspaceRead {
  const notice = finding({
    finding_ref: 'F-SRC',
    source_attested: true,
    can_veto: false,
    vetoed: opts.vetoedNotice === true,
    veto_ref: opts.vetoedNotice ? 'V-1' : null,
    veto_reason: opts.vetoedNotice ? '当时觉得来源写法不同而已' : null,
  });
  const real = finding({
    finding_ref: 'F-VAG', finding_type: 'untestable',
    diagnosis_summary: '「尽快」不可测。', rule_code: 'INCOSE-R7',
    evidence_span: '尽快', can_veto: true, vetoed: false,
  });
  const findings = opts.withRealProblem ? [notice, real] : [notice];
  return attestedPendingWorkspace({
    review_status: 'awaiting_adjudication',
    display_code: 'awaiting_adjudication',
    display_note: '本轮提的问题都不用你处理了（来源已由人工确认），可以直接确认这个条目。',
    attestation_closed_gap: false,
    current_verdict: {
      round_ref: 'R-1', round_no: 2, batch_ref: 'B-1', item_ref: 'IT-ATT',
      diagnosis_mode: 'standard', trigger: 'user_submit', status: 'completed',
      verdict_kind: 'revise', verdict_summary: '表达与来源要素对不上，建议修订。',
      findings,
      revision_points: [{
        point_ref: 'P1', label: '对齐来源', finding_index: 0, finding_ref: 'F-SRC',
        find: '大额订单', replace: '金额超过 500 元的订单', basis: '来源要素引文', group: null,
      }],
      supplement_gaps: [], context_coverage: '', model_result_refs: [],
      invalidated: false, superseded_by: null, adjudication: null,
      overridden: false, confirm_result: null, effective: true,
      created_at: '2026-07-25T03:00:00Z',
      blocking_finding_count: opts.withRealProblem ? 1 : 0,
      all_blocking_findings_vetoed: false,
      blocking_findings_cleared: !opts.withRealProblem,
    },
    available_actions: [{
      key: 'confirm_without_override',
      enabled: !opts.withRealProblem,
      disabled_reason: opts.withRealProblem ? '本轮还有你没处理的问题' : null,
    }],
  });
}

async function verdictZone(): Promise<HTMLElement> {
  const zone5 = screen.getByLabelText('区5 条目评审操作与确认');
  await waitFor(() =>
    expect(within(zone5).getByText('表达与来源要素对不上，建议修订。')).toBeInTheDocument());
  return zone5;
}

describe('V4：降格提示的渲染（此前前端从未渲染过一次）', () => {
  it('带 source_attested 的结论卡渲染出「不用你处理」提示卡与「来源＝人工确认」徽标', async () => {
    getWorkspaceMock.mockResolvedValue(attestedVerdictWorkspace());
    renderFlow();

    const zone5 = await verdictZone();
    const notice = zone5.querySelector<HTMLElement>('.rv5-attested')!;
    expect(notice).toBeTruthy();
    expect(notice.textContent).toContain('不用你处理');
    expect(within(notice).getByText('来源＝人工确认')).toBeInTheDocument();
    // 它不得同时出现在「问题」列表里（那会与后端的阻断计数对不上）
    expect(zone5.querySelectorAll('.rv5-problem')).toHaveLength(0);
    // 改法照实说明，但明确不会被应用
    expect(within(zone5).getByText(/采纳时不会应用它/)).toBeInTheDocument();
  });

  it('一个待处理问题都不剩时，引导语不再说「每个问题要么改、要么标为不是问题」', async () => {
    getWorkspaceMock.mockResolvedValue(attestedVerdictWorkspace());
    renderFlow();

    const zone5 = await verdictZone();
    const note = zone5.querySelector<HTMLElement>('.az5-card__ft .az5-card__note')!;
    expect(note.textContent).toContain('本轮只剩来源类提示');
    expect(note.textContent).not.toContain('每个问题要么改、要么标为不是问题');
    expect(note.textContent).not.toContain('你都标成了不是问题');
  });

  it('还剩真问题时，问题列表与提示区并存，引导语回到常规口径', async () => {
    getWorkspaceMock.mockResolvedValue(attestedVerdictWorkspace({ withRealProblem: true }));
    renderFlow();

    const zone5 = await verdictZone();
    expect(zone5.querySelectorAll('.rv5-problem')).toHaveLength(1);
    expect(within(zone5).getByText('来源＝人工确认')).toBeInTheDocument();
    const note = zone5.querySelector<HTMLElement>('.az5-card__ft .az5-card__note')!;
    expect(note.textContent).toContain('每个问题要么改、要么标为不是问题');
  });

  it('K10(a)：提示区渲染既有否决状态并保留撤销入口', async () => {
    getWorkspaceMock.mockResolvedValue(attestedVerdictWorkspace({ vetoedNotice: true }));
    recordFindingVetoMock.mockResolvedValue(attestedVerdictWorkspace({ vetoedNotice: true }));
    renderFlow();

    const zone5 = await verdictZone();
    expect(within(zone5).getByText(/你还把它标过「不是问题」/)).toBeInTheDocument();
    const undo = within(zone5).getByRole('button', { name: '撤销这个标记' });
    fireEvent.click(undo);
    await waitFor(() => expect(recordFindingVetoMock).toHaveBeenCalled());
    expect(recordFindingVetoMock.mock.calls[0][1]).toMatchObject({ action: 'restore', veto_ref: 'V-1' });
  });
});

describe('S10：理由必填对纯空白同样不放行', () => {
  it('只敲空格不算填了理由（实现用 trim，去掉 trim 的变异要被拦住）', async () => {
    getWorkspaceMock.mockResolvedValue(attestedPendingWorkspace({
      display_code: 'supplement_pending',
      display_note: '来源缺口未闭合，补充来源或修订表达后可再诊断。',
      attestation_closed_gap: false,
      supplement_gaps_open: ['需向财务确认导出格式与必含字段。'],
      available_actions: [{ key: 'attest_source', enabled: false, disabled_reason: '这条已经人工确认过来源了' }],
    }));
    renderFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    fireEvent.click(within(exit).getByRole('button', { name: '撤回该条' }));

    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getAllByRole('button')
      .find((b) => b.textContent?.replace(/\s/g, '') === '撤回该条')!;
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: '   ' } });
    expect(confirm).toBeDisabled();
  });
});
