/**
 * 条目评审页区2 主动作「发起诊断」组件用例（T20260711-review-z2-primary-action）。
 * 口径：区2 主按钮与区5 药丸双入口同机制——同一模式弹层共享件、同一命令文本、
 * 同一 sendDialogue 链（AEP-095）；区2 选模式后直接发起，区5 保持仅预填。
 * 按钮可用性消费服务端 available_operations.start_diagnosis ActionFact，前端不自算门禁。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const getWorkspaceMock = vi.fn();
const reviewDialogueStreamMock = vi.fn();
vi.mock('../src/api/item-review', () => ({
  itemReviewApi: {
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    reviewDialogueStream: (...args: unknown[]) => reviewDialogueStreamMock(...args),
    adjudicateVerdict: vi.fn(),
  },
}));
vi.mock('../src/api/quality', () => ({
  qualityApi: { getItemQuality: vi.fn(() => Promise.reject(new Error('no quality projection'))) },
}));
vi.mock('../src/api/requirements', () => ({
  requirementsApi: { applyItemRevision: vi.fn() },
}));

import type { ItemFormationWorkspaceRead } from '../src/api/item-formation';
import { itemReviewWorkspaceFixture } from '../src/fixtures/item-review';
import { diagnosisLaunchCommand, QUICK_COMMAND_PREFILLS } from '../src/view-models/requirement-item-review';
import { RequirementItemReviewFlow } from '../src/workbenches/RequirementItemReviewFlow';

/** fixture 默认勾选集（与组件 defaultDiagnosisSelection 同口径：可诊断项自动全选） */
const defaultSelectedCount = itemReviewWorkspaceFixture.review_items.filter(
  (item) =>
    item.available_actions.some((a) => a.key === 'request_diagnosis' && a.enabled) ||
    (!item.available_actions.length && item.review_status === 'no_verdict'),
).length;

function renderFlow(sourceWorkspace: ItemFormationWorkspaceRead | null = null) {
  return render(
    <RequirementItemReviewFlow
      projectId="P-1"
      operatorRef="U-1"
      sourceWorkspace={sourceWorkspace}
      onBackToFormation={vi.fn()}
      onBackToMaintenance={vi.fn()}
    />,
  );
}

function zone2() {
  return screen.getByLabelText('区2 导航与进度');
}

function zone2LaunchButton() {
  // 区2 主按钮可访问名=「✨ 发起诊断」，与区5 药丸「发起诊断」可区分
  return within(zone2()).getByRole('button', { name: /✨ 发起诊断/ });
}

beforeEach(() => {
  getWorkspaceMock.mockReset();
  reviewDialogueStreamMock.mockReset();
});

describe('区2 主动作「发起诊断」（双入口同机制）', () => {
  it('主按钮存在、primary 样式、可用态 title 说明诊断范围', () => {
    renderFlow();
    const button = zone2LaunchButton();
    expect(button).toBeInTheDocument();
    expect(button.className).toContain('ant-btn');
    expect(button.className).toContain('primary');
    expect(button).toBeEnabled();
    expect(button.getAttribute('title')).toContain('默认诊断全部可诊断条目');
  });

  it('点击主按钮打开模式弹层（四模式 + 范围说明）；再点收起', () => {
    renderFlow();
    fireEvent.click(zone2LaunchButton());
    const menu = within(zone2()).getByRole('menu', { name: '选择诊断模式' });
    for (const label of ['快速', '标准', '全面', '增量']) {
      expect(within(menu).getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(menu.textContent).toContain(`已勾选 ${defaultSelectedCount} 条`);
    fireEvent.click(zone2LaunchButton());
    expect(within(zone2()).queryByRole('menu')).toBeNull();
  });

  it('选模式后直接发起：同一命令文本走 sendDialogue（AEP-095），进入进行态不重复转圈', async () => {
    reviewDialogueStreamMock.mockResolvedValue({
      outcome_type: 'command',
      operation: 'start_diagnosis',
      operation_label: '发起诊断',
      message: `已发起诊断（${defaultSelectedCount} 条），结论产出后进入待裁决。`,
      agent_run_ref: 'AR-1',
      next_action: null,
    });
    renderFlow();
    fireEvent.click(zone2LaunchButton());
    fireEvent.click(within(zone2()).getByRole('button', { name: '标准' }));

    await waitFor(() => expect(reviewDialogueStreamMock).toHaveBeenCalledTimes(1));
    const [projectId, payload] = reviewDialogueStreamMock.mock.calls[0];
    expect(projectId).toBe('P-1');
    // 与区5 预填逐字一致 = 同一轮次语义（A1）
    expect(payload.message).toBe(QUICK_COMMAND_PREFILLS.diagnose('标准', defaultSelectedCount));
    expect(payload.selected_item_refs).toHaveLength(defaultSelectedCount);
    // 弹层随发起关闭
    expect(within(zone2()).queryByRole('menu')).toBeNull();

    // 进行态：按钮禁用 + title 给原因；进行态指示只有区2「诊断中」徽标，不加第二个转圈
    await waitFor(() => expect(zone2LaunchButton()).toBeDisabled());
    expect(zone2LaunchButton().getAttribute('title')).toBe('诊断进行中');
    expect(within(zone2()).getByText('诊断中')).toBeInTheDocument();
    expect(zone2LaunchButton().className).not.toContain('ant-btn-loading');
  });

  it('禁用态：服务端 start_diagnosis ActionFact 关闭时按钮禁用且 title 给 disabled_reason（A2）', async () => {
    getWorkspaceMock.mockResolvedValue({
      ...itemReviewWorkspaceFixture,
      available_operations: [
        // B2a 后端输出已清「无结论」话术（disabled_reason 直出伞词）：前端 displayReviewText 子串改写已退役，原样透传
        { key: 'start_diagnosis', enabled: false, disabled_reason: '没有可诊断的条目' },
        { key: 'refresh_review_view', enabled: true },
      ],
    });
    renderFlow({ formation_context_ref: 'CTX-1', pending_items: [] } as unknown as ItemFormationWorkspaceRead);

    await waitFor(() => expect(zone2LaunchButton()).toBeDisabled());
    // 后端 disabled_reason 原样透传（不前端改写）
    expect(zone2LaunchButton().getAttribute('title')).toBe('没有可诊断的条目');
    expect(reviewDialogueStreamMock).not.toHaveBeenCalled();
  });

  it('区5 药丸入口回归无损：控件快捷命令药丸仅预填 /诊断（不直发、不弹模式层，A3）', () => {
    renderFlow();
    // 区5 发起诊断迁为控件快捷命令药丸（label 发起诊断，与区2「✨ 发起诊断」区分）：点击仅预填控件输入框。
    fireEvent.click(screen.getByRole('button', { name: '发起诊断' }));

    const composer = screen.getByLabelText('消息输入') as HTMLTextAreaElement;
    expect(composer.value).toBe(diagnosisLaunchCommand('standard', defaultSelectedCount));
    expect(reviewDialogueStreamMock).not.toHaveBeenCalled();
    // 模式选择归区2；区5 不再自带模式弹层。
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
