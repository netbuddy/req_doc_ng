import { afterEach, describe, expect, it, vi } from 'vitest';
import { createWorkbenchHandoff } from '../src/view-models/workbench-handoff';

describe('WorkbenchHandoff', () => {
  afterEach(() => vi.restoreAllMocks());

  it('为条目到图表交接生成一次性 token，并完整保留对象上下文', () => {
    vi.spyOn(Date, 'now').mockReturnValue(1720000000000);

    const handoff = createWorkbenchHandoff({
      projectId: 'project-1',
      targetWorkbench: 'diagram',
      intent: 'create_chart_from_item',
      anchor: {
        entityType: 'requirement_item',
        ref: 'item-1',
        title: 'FR-001 大额订单审批',
      },
      relatedAssets: [],
    });

    expect(handoff).toEqual({
      token: 1720000000000,
      projectId: 'project-1',
      targetWorkbench: 'diagram',
      intent: 'create_chart_from_item',
      anchor: {
        entityType: 'requirement_item',
        ref: 'item-1',
        title: 'FR-001 大额订单审批',
      },
      relatedAssets: [],
    });
  });

  it('允许图表携来源条目进入发布编排', () => {
    const handoff = createWorkbenchHandoff({
      projectId: 'project-1',
      targetWorkbench: 'release',
      intent: 'compose_document_from_assets',
      anchor: { entityType: 'chart', ref: 'chart-1', title: '大额订单审批流程' },
      relatedAssets: [
        { entityType: 'requirement_item', ref: 'item-1', title: 'FR-001 大额订单审批' },
      ],
    });

    expect(handoff.anchor.entityType).toBe('chart');
    expect(handoff.relatedAssets).toHaveLength(1);
    expect(handoff.relatedAssets[0].ref).toBe('item-1');
  });
});
