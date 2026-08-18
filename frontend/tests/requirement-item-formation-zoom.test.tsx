/**
 * 条目形成页区4 放大/还原开关组件用例（T20260714-formation-z4-layout 方案 B）。
 * 口径：区4 常态不挤压区3（红线），放大态是用户显式索取的暂态——
 * 开关只切换本地 class，不发任何命令、不落库；关闭选中条目时开关随详情一同消失。
 * 布局本体（方案 C 流体主从）是纯 CSS，几何断言由浏览器实测证据承担，此处只锁交互契约。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../src/api/item-formation', async () => {
  const actual = await vi.importActual<typeof import('../src/api/item-formation')>(
    '../src/api/item-formation',
  );
  return {
    ...actual,
    itemFormationApi: {
      getWorkspace: vi.fn(),
    },
  };
});
vi.mock('../src/api/requirements', () => ({
  requirementsApi: { applyItemRevision: vi.fn() },
}));
vi.mock('../src/api/settings', () => ({
  settingsApi: {
    listRequirementConventions: vi.fn(() => Promise.reject(new Error('no conventions'))),
  },
}));

import type {
  ItemFormationWorkspaceRead,
  PendingRequirementItemRead,
} from '../src/api/item-formation';
import { itemFormationWorkspaceFixture } from '../src/fixtures/item-formation';
import { RequirementItemFormationFlow } from '../src/workbenches/RequirementItemFormationFlow';

/** 共享 fixture 的 pending_items 为空；区4 详情需选中条目才渲染，故本用例自带一条。 */
const pendingItem: PendingRequirementItemRead = {
  item_ref: 'IT-001',
  req_no: 'REQ-001',
  expression: '异常链路出现时，系统应在 5 秒内展示诊断提示，并保留可追溯的来源依据。',
  req_type: 'functional',
  status: 'pending_confirmation',
  version_no: 1,
  source_element_refs: ['EL-001'],
  formation_basis_ref: null,
  curation_note: null,
  boundary_note: null,
  verification_method: [],
  verification_note: null,
  revision_records: [],
};

const workspace: ItemFormationWorkspaceRead = {
  ...itemFormationWorkspaceFixture,
  pending_items: [pendingItem],
  selected_item_ref: null,
};

/** 组件恒自动选中 pending_items[0]（见 RequirementItemFormationFlow selectedItem 派生），
 *  故有条目即渲染区4 详情与开关，无需点击；「无选中」只在条目为空时成立。 */
function renderFlow(ws: ItemFormationWorkspaceRead = workspace) {
  return render(
    <RequirementItemFormationFlow
      projectId="P-1"
      operatorRef="U-1"
      sourceWorkspace={null}
      initialWorkspace={ws}
      onBackToAnalysis={vi.fn()}
    />,
  );
}

describe('区4 放大/还原开关（方案 B）', () => {
  it('无待确认条目时不渲染开关（无详情可放大）', () => {
    renderFlow({ ...workspace, pending_items: [] });
    expect(screen.queryByRole('button', { name: /放大|还原/ })).toBeNull();
  });

  it('有条目时开关随详情出现，默认常态（aria-pressed=false，区4 不带 is-zoomed）', () => {
    const { container } = renderFlow();

    const zoom = screen.getByRole('button', { name: /放大/ });
    expect(zoom.getAttribute('aria-pressed')).toBe('false');
    expect(container.querySelector('.item-formation-zone--detail.is-zoomed')).toBeNull();
    expect(container.querySelector('.item-detail-layout--zoomed')).toBeNull();
  });

  it('点击放大 → 区4 与栅格进入放大态；再点还原 → 回到常态（可逆，不残留）', () => {
    const { container } = renderFlow();

    fireEvent.click(screen.getByRole('button', { name: /放大/ }));
    expect(container.querySelector('.item-formation-zone--detail.is-zoomed')).not.toBeNull();
    expect(container.querySelector('.item-detail-layout--zoomed')).not.toBeNull();

    const restore = screen.getByRole('button', { name: /还原/ });
    expect(restore.getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(restore);
    expect(container.querySelector('.item-formation-zone--detail.is-zoomed')).toBeNull();
    expect(container.querySelector('.item-detail-layout--zoomed')).toBeNull();
    expect(screen.getByRole('button', { name: /放大/ }).getAttribute('aria-pressed')).toBe('false');
  });

  it('区4 主从结构成立：主卡为栅格直接子级，后三联收在侧栏内（等分形态已退役）', () => {
    const { container } = renderFlow();

    const layout = container.querySelector('.item-detail-layout');
    const side = container.querySelector('.item-detail-side');
    expect(layout).not.toBeNull();
    expect(side).not.toBeNull();
    // 主卡直挂栅格（吃余量列），后三联在侧栏内纵向堆叠
    expect(layout?.querySelector(':scope > .item-detail-card--primary')).not.toBeNull();
    expect(side?.querySelectorAll('.item-detail-card').length).toBe(3);
  });
});
