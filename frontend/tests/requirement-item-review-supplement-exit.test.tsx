/**
 * 条目评审页「补充来源出口卡」组件用例（T20260716-supplement-exit-ui，issue #30 出口三部曲之三）。
 * 口径：采纳 supplement 结论后条目进入「待补充来源」派生态（此时无站立结论卡），出口卡以
 * display_code==='supplement_pending' 为条件在区5 线程重新托出：自动查一次候选（ADR-0002 P3
 * 说缺必说补）→ 逐条〔登记为本条来源〕→ 登记后前端自动接续复诊（后端已解耦不自动复诊）→
 * 兜底出口〔人工确认〕〔撤回该条〕〔按说明查找〕恒在；空/拒绝态无死按钮只给指引（P1 无死胡同）。
 * T20260720-supplement-manual-source-and-attest 后：〔我自己指定来源〕拆成两件——真的自己指定
 * 归区3 拖选，给 AI 一句提示重查改名〔按说明查找〕；另加〔人工确认〕背书出口。
 * 领域规则在后端（backend/tests/test_item_review.py / test_item_formation.py），本文件只测 UI 编排。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const getWorkspaceMock = vi.fn();
const reviewDialogueMock = vi.fn();
const startDiagnosisMock = vi.fn();
const applyItemRevisionMock = vi.fn();
const attestSourceMock = vi.fn();
vi.mock('../src/api/item-review', () => ({
  itemReviewApi: {
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    reviewDialogue: (...args: unknown[]) => reviewDialogueMock(...args),
    reviewDialogueStream: vi.fn(),
    startDiagnosis: (...args: unknown[]) => startDiagnosisMock(...args),
    adjudicateVerdict: vi.fn(),
    attestSource: (...args: unknown[]) => attestSourceMock(...args),
  },
}));
vi.mock('../src/api/quality', () => ({
  qualityApi: { getItemQuality: vi.fn(() => Promise.reject(new Error('no quality projection'))) },
}));
vi.mock('../src/api/requirements', () => ({
  requirementsApi: { applyItemRevision: (...args: unknown[]) => applyItemRevisionMock(...args) },
}));

import type { ItemFormationWorkspaceRead } from '../src/api/item-formation';
import type { ItemReviewWorkspaceRead, ReviewRequirementItemRead } from '../src/api/item-review';
import { itemReviewWorkspaceFixture } from '../src/fixtures/item-review';
import { QUICK_COMMAND_PREFILLS } from '../src/view-models/requirement-item-review';
import { RequirementItemReviewFlow } from '../src/workbenches/RequirementItemReviewFlow';

/** 构造一条「待补充来源」派生态条目的工作区（采纳 supplement 后：无站立结论卡、缺口未闭合）。 */
function supplementWorkspace(): ItemReviewWorkspaceRead {
  const base = itemReviewWorkspaceFixture.review_items[0];
  const item: ReviewRequirementItemRead = {
    ...base,
    item_ref: 'IT-SUP',
    req_no: 'REQ-006',
    expression: '大额订单需人工审核',
    review_status: 'no_verdict',
    display_code: 'supplement_pending',
    display_note: '来源缺口未闭合，补充来源或修订表达后可再诊断。',
    current_verdict: null,
    verdict_history: [],
    dialogue_messages: [],
    supplement_gaps_open: ['本条讲「大额订单审核」，来源要素讲「下单后通知」，不是同一件事'],
    source_element_refs: ['EL-OLD'],
    available_actions: [],
  };
  return { ...itemReviewWorkspaceFixture, workspace_version: '3', review_items: [item] };
}

const CANDIDATE = {
  element_ref: 'EL-NEW',
  element_type: 'functional_requirement',
  content: '订单金额超过 500 元时需人工审核',
  source_quote: '订单金额超过 500 元时，系统应要求人工审核',
  reason: '与本条同指大额订单审核',
  rank: 1,
};

/** sourceWorkspace 带 formation_context_ref 才会触发 getWorkspace 拉取（否则停在本地 fixture）。 */
function renderSupplementFlow() {
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
  reviewDialogueMock.mockReset();
  startDiagnosisMock.mockReset();
  applyItemRevisionMock.mockReset();
  attestSourceMock.mockReset();
});

describe('补充来源出口卡（issue #30；ADR-0002 P1 无死胡同 / P3 说缺必说补）', () => {
  it('进入待补充来源态自动查一次候选：候选带原文/理由/登记按钮 + 兜底出口恒在（A1/A3）', async () => {
    getWorkspaceMock.mockResolvedValue(supplementWorkspace());
    reviewDialogueMock.mockResolvedValue({
      outcome_type: 'command',
      source_candidates: [CANDIDATE],
      message: '为本条找到 1 条候选来源。',
      next_action: '核对候选后登记为本条来源，或撤回本条',
    });
    renderSupplementFlow();

    // 自动查一次（P3：缺口与候选一起出现），命令文本=/找来源
    await waitFor(() => expect(reviewDialogueMock).toHaveBeenCalledTimes(1));
    expect(reviewDialogueMock.mock.calls[0][1].message).toBe(QUICK_COMMAND_PREFILLS.findSources());
    // 能力 C：这一次是页面自己发的，须标明不是用户输入，否则每刷新一次多一对幻影气泡
    expect(reviewDialogueMock.mock.calls[0][1].user_initiated).toBe(false);

    const exit = await screen.findByLabelText('补充来源出口');
    expect(within(exit).getByText(/来源缺口清单/)).toBeInTheDocument();
    expect(within(exit).getByText(CANDIDATE.content)).toBeInTheDocument();
    expect(within(exit).getByText(/原文：/)).toBeInTheDocument();
    expect(within(exit).getByText(CANDIDATE.reason)).toBeInTheDocument();
    expect(within(exit).getByRole('button', { name: '登记为本条来源' })).toBeEnabled();
    // 兜底出口恒在（A3）：人工确认 / 撤回该条 / 按说明查找
    expect(within(exit).getByRole('button', { name: '人工确认' })).toBeInTheDocument();
    expect(within(exit).getByRole('button', { name: '撤回该条' })).toBeInTheDocument();
    expect(within(exit).getByRole('button', { name: '按说明查找' })).toBeInTheDocument();
    // 知道出处就直接去区3 拖选：常驻指路，不必等 AI
    expect(within(exit).getByText(/在左边正文里拖选那句话/)).toBeInTheDocument();
  });

  it('登记候选：整集替换（当前 ∪ 候选）走 source_element_refs 修订通道，只关缺口不自动复诊、回执指引一键发起（A2）', async () => {
    getWorkspaceMock.mockResolvedValue(supplementWorkspace());
    reviewDialogueMock.mockResolvedValue({ outcome_type: 'command', source_candidates: [CANDIDATE] });
    applyItemRevisionMock.mockResolvedValue({ status: 'applied', next_action: '修订已应用：旧结论随版本失效' });
    renderSupplementFlow();

    const registerButton = await screen.findByRole('button', { name: '登记为本条来源' });
    fireEvent.click(registerButton);

    await waitFor(() => expect(applyItemRevisionMock).toHaveBeenCalledTimes(1));
    const [projectId, itemRef, command] = applyItemRevisionMock.mock.calls[0];
    expect(projectId).toBe('P-1');
    expect(itemRef).toBe('IT-SUP');
    expect(command.field_key).toBe('source_element_refs');
    expect(command.revision_mode).toBe('manual');
    // 整集替换：提交「当前来源 ∪ 候选」的 JSON 数组字符串（后端再去重/升序/门禁校验）
    expect(JSON.parse(command.revised_value)).toEqual(['EL-OLD', 'EL-NEW']);
    // 登记只关缺口、不自动复诊（改回一键发起：自动复诊在真实 LLM 上又慢又易失败、还会再判 supplement 成环）
    await waitFor(() =>
      expect(screen.getByText(/点上方「发起诊断」即可复核本条/)).toBeInTheDocument(),
    );
    expect(startDiagnosisMock).not.toHaveBeenCalled();
  });

  it('登记候选遇后端前置检查失败（status!=applied，如版本不一致）：如实报失败、不谎报成功（C8）', async () => {
    // 后端版本不一致时返回 HTTP 200 + status='rejected_precheck'，条目一个字没改。前端若只看 HTTP
    // 200 就谎报「已离开待补充来源，点发起诊断复核」，用户被卡在原地却以为成功了。守卫必须拦住这句。
    getWorkspaceMock.mockResolvedValue(supplementWorkspace());
    reviewDialogueMock.mockResolvedValue({ outcome_type: 'command', source_candidates: [CANDIDATE] });
    applyItemRevisionMock.mockResolvedValue({
      status: 'rejected_precheck',
      next_action: '工作区版本不一致，请刷新后重试',
    });
    renderSupplementFlow();

    const registerButton = await screen.findByRole('button', { name: '登记为本条来源' });
    fireEvent.click(registerButton);

    await waitFor(() => expect(applyItemRevisionMock).toHaveBeenCalledTimes(1));
    // 如实告知失败
    await waitFor(() =>
      expect(screen.getByText(/工作区版本不一致，请刷新后重试/)).toBeInTheDocument(),
    );
    // 不得出现谎报成功的回执
    expect(screen.queryByText(/点上方「发起诊断」即可复核本条/)).toBeNull();
  });

  it('空/拒绝态：无候选不给死按钮，只呈现指引文案，兜底出口仍在（A4）', async () => {
    getWorkspaceMock.mockResolvedValue(supplementWorkspace());
    reviewDialogueMock.mockResolvedValue({
      outcome_type: 'command',
      source_candidates: [],
      message: '当前批次没有可作候选来源的要素。',
      next_action: '撤回本条，或回需求分析补入材料',
    });
    renderSupplementFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    await waitFor(() =>
      expect(within(exit).getByText('当前批次没有可作候选来源的要素。')).toBeInTheDocument(),
    );
    // 无死按钮：没有可登记候选时不渲染〔登记为本条来源〕
    expect(within(exit).queryByRole('button', { name: '登记为本条来源' })).toBeNull();
    // 兜底出口恒在
    expect(within(exit).getByRole('button', { name: '人工确认' })).toBeInTheDocument();
    expect(within(exit).getByRole('button', { name: '撤回该条' })).toBeInTheDocument();
    expect(within(exit).getByRole('button', { name: '按说明查找' })).toBeInTheDocument();
  });

  it('〔按说明查找〕弹理由层，确认后按说明重跑 /找来源（前端不解析、不直发修订）', async () => {
    getWorkspaceMock.mockResolvedValue(supplementWorkspace());
    reviewDialogueMock.mockResolvedValue({ outcome_type: 'command', source_candidates: [] });
    renderSupplementFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    // 进入态自动查一次（mount）后清账，便于断言「指定来源」触发的重跑。
    await waitFor(() => expect(reviewDialogueMock).toHaveBeenCalledTimes(1));
    reviewDialogueMock.mockClear();

    // 卡内按钮弹理由层（用户拍板：先补理由再发，不经输入框预填）：打开时不直发、不改修订。
    fireEvent.click(within(exit).getByRole('button', { name: '按说明查找' }));
    const confirm = await screen.findByRole('button', { name: '开始查找' });
    expect(reviewDialogueMock).not.toHaveBeenCalled();
    expect(applyItemRevisionMock).not.toHaveBeenCalled();

    const reasonBox = screen.getByPlaceholderText('例如：来源在材料第 3 节某段落…') as HTMLTextAreaElement;
    fireEvent.change(reasonBox, { target: { value: '第3节' } });
    fireEvent.click(confirm);

    // 确认后按说明重跑 /找来源（前端不解析命令词，仍走对话通道，不走修订）。
    await waitFor(() => expect(reviewDialogueMock).toHaveBeenCalledTimes(1));
    expect(reviewDialogueMock.mock.calls[0][1].message).toBe(`${QUICK_COMMAND_PREFILLS.specifySource()}第3节`);
    expect(applyItemRevisionMock).not.toHaveBeenCalled();
  });
  it('〔按说明查找〕是用户发的：命令带 user_initiated=true，留痕照写（能力 C 的反面对照）', async () => {
    getWorkspaceMock.mockResolvedValue(supplementWorkspace());
    reviewDialogueMock.mockResolvedValue({ outcome_type: 'command', source_candidates: [] });
    renderSupplementFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    await waitFor(() => expect(reviewDialogueMock).toHaveBeenCalledTimes(1));
    reviewDialogueMock.mockClear();

    fireEvent.click(within(exit).getByRole('button', { name: '按说明查找' }));
    const reasonBox = await screen.findByPlaceholderText('例如：来源在材料第 3 节某段落…');
    fireEvent.change(reasonBox, { target: { value: '第3节' } });
    fireEvent.click(screen.getByRole('button', { name: '开始查找' }));

    await waitFor(() => expect(reviewDialogueMock).toHaveBeenCalledTimes(1));
    expect(reviewDialogueMock.mock.calls[0][1].user_initiated).toBe(true);
  });

  it('〔人工确认〕理由必填：留空时提交置灰，填了才发（能力 B）', async () => {
    getWorkspaceMock.mockResolvedValue(supplementWorkspace());
    reviewDialogueMock.mockResolvedValue({ outcome_type: 'command', source_candidates: [] });
    attestSourceMock.mockResolvedValue(supplementWorkspace());
    renderSupplementFlow();

    const exit = await screen.findByLabelText('补充来源出口');
    fireEvent.click(within(exit).getByRole('button', { name: '人工确认' }));

    const confirm = await screen.findByRole('button', { name: '确认并登记' });
    // 授权例外要赖不掉：没写理由就不让提交
    expect(confirm).toBeDisabled();
    expect(attestSourceMock).not.toHaveBeenCalled();

    const reasonBox = screen.getByPlaceholderText(/评审会上口头确认/) as HTMLTextAreaElement;
    fireEvent.change(reasonBox, { target: { value: '客户口头确认，纪要漏记' } });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);

    await waitFor(() => expect(attestSourceMock).toHaveBeenCalledTimes(1));
    const [projectId, command] = attestSourceMock.mock.calls[0];
    expect(projectId).toBe('P-1');
    expect(command.item_ref).toBe('IT-SUP');
    expect(command.reason).toBe('客户口头确认，纪要漏记');
    // 背书不走修订通道、不碰来源要素
    expect(applyItemRevisionMock).not.toHaveBeenCalled();
  });
});
