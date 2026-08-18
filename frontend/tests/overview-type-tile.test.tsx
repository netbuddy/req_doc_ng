import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { OverviewWorkbench } from '../src/workbenches/OverviewWorkbench';
import { overviewWorkbenchFixture } from '../src/fixtures/overview';
import type { OverviewTypeBridgeVM, OverviewWorkbenchVM } from '../src/view-models/overview';

// 按类型瓦片的双动作：主体切换数字桥、右上角箭头跳转工作台。
// 本文件盯的是「桥数据还没到」的那一段时间——瓦片主体不能因此变成不可点的死块（冷审查 C7）。

function bridgeVM(key: string, label: string): OverviewTypeBridgeVM {
  return {
    key,
    label,
    rows: [
      {
        key: 'items',
        head: `2 条${label}条目`,
        operator: '＝',
        parts: [{ text: '2 来自知识项' }],
      },
    ],
    emptyText: null,
    conclusion: `${label}知识项 2 → ${label}条目 2（去向如上）。`,
  };
}

function renderWorkbench(vm: OverviewWorkbenchVM, onNavigate = vi.fn()) {
  render(
    <OverviewWorkbench
      vm={vm}
      selectedProject={null}
      onNavigate={onNavigate}
      onCreateProject={vi.fn()}
    />,
  );
  return onNavigate;
}

function tileBody(typeKey: string) {
  return within(screen.getByTestId(`overview-type-tile-${typeKey}`)).getAllByRole('button')[0];
}

describe('按类型瓦片主体的两种行为', () => {
  it('桥数据未就绪：主体仍可点，退回导航行为，不禁用', () => {
    const onNavigate = renderWorkbench({ ...overviewWorkbenchFixture, typeBridges: [] });

    const body = tileBody('functional');
    expect(body).not.toBeDisabled();
    expect(body).toHaveAccessibleName('功能 —，跳转到管理');
    // 未就绪时没有「选中/未选中」这个概念，不该向读屏暴露按下态
    expect(body).not.toHaveAttribute('aria-pressed');

    fireEvent.click(body);
    expect(onNavigate).toHaveBeenCalledWith('management');
  });

  it('桥数据就绪：主体改为切换数字桥，并暴露按下态', () => {
    const vm: OverviewWorkbenchVM = {
      ...overviewWorkbenchFixture,
      typeBridges: [bridgeVM('functional', '功能'), bridgeVM('quality', '质量')],
    };
    const onNavigate = renderWorkbench(vm);

    const quality = tileBody('quality');
    expect(quality).toHaveAccessibleName('质量知识项 —，查看该类型的数字桥');
    expect(quality).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(quality);
    expect(onNavigate).not.toHaveBeenCalled();
    expect(tileBody('quality')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('overview-type-bridge')).toHaveTextContent('2 条质量条目');
  });
});
