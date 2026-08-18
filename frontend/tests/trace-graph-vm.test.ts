import { describe, expect, it } from 'vitest';
import type {
  TraceChainRead,
  TraceEdgeRead,
  TraceLevelRead,
  TraceNodeRead,
} from '../src/api/trace';
import {
  NODE_W,
  PAD,
  backTo,
  buildFlowLayout,
  buildSwimlaneLayout,
  edgeStatusMeta,
  filterChainByWing,
  gapKindMeta,
  navTargetMeta,
  nodeKeyOf,
  pushHop,
  SWIMLANE_ORDER,
} from '../src/view-models/trace-graph';

const focus: TraceNodeRead = {
  node_type: 'requirement_item',
  ref: 'item-1',
  label: 'FR-001 系统应支持导出',
  status: 'confirmed',
};

function node(nodeType: TraceNodeRead['node_type'], ref: string, label = ref): TraceNodeRead {
  return { node_type: nodeType, ref, label };
}

function edge(
  upType: TraceNodeRead['node_type'],
  upRef: string,
  downType: TraceNodeRead['node_type'],
  downRef: string,
  status: TraceEdgeRead['status'] = 'derived',
): TraceEdgeRead {
  return {
    edge_key: `${upRef}->${downRef}`,
    relation_kind: 'chart_source',
    origin: status === 'derived' ? 'derived' : 'ldm013',
    upstream_type: upType,
    upstream_ref: upRef,
    downstream_type: downType,
    downstream_ref: downRef,
    status,
    link_ref: status === 'derived' ? null : `link-${upRef}`,
  };
}

function level(distance: number, nodes: TraceNodeRead[], edges: TraceEdgeRead[], folded = 0): TraceLevelRead {
  return {
    distance,
    nodes,
    edges,
    folded_count: folded,
    folded_by_type: folded > 0 ? { element: folded } : {},
  };
}

function chain(direction: 'upstream' | 'downstream', levels: TraceLevelRead[]): TraceChainRead {
  return {
    project_ref: 'p1',
    direction,
    focus,
    depth: 2,
    limit: 8,
    include_invalid: false,
    levels,
  };
}

const upstream = chain('upstream', [
  level(1, [node('element', 'el-1', '要素一')], [edge('element', 'el-1', 'requirement_item', 'item-1')]),
  level(2, [node('material', 'mat-1', '材料一')], [edge('material', 'mat-1', 'element', 'el-1')], 3),
]);

const downstream = chain('downstream', [
  level(
    1,
    [node('chart', 'chart-1', '流程图'), node('document', 'doc-1', '需求规格说明')],
    [
      edge('requirement_item', 'item-1', 'chart', 'chart-1', 'suspect_pending_review'),
      edge('requirement_item', 'item-1', 'document', 'doc-1'),
    ],
  ),
]);

describe('buildFlowLayout（焦点流向：上游左、焦点中、下游右）', () => {
  const layout = buildFlowLayout(focus, upstream, downstream);

  it('列顺序 = 上游2层, 上游1层, 焦点, 下游1层', () => {
    expect(layout.columns.map((c) => c.key)).toEqual(['up-2', 'up-1', 'focus', 'down-1']);
    // 列 x 单调递增且间距一致
    const xs = layout.columns.map((c) => c.x);
    expect(xs[0]).toBe(PAD);
    expect(new Set(xs.slice(1).map((x, i) => x - xs[i])).size).toBe(1);
  });

  it('焦点节点带 isFocus 标记且位于焦点列', () => {
    const focusNode = layout.nodes.find((n) => n.isFocus);
    expect(focusNode?.ref).toBe('item-1');
    const focusCol = layout.columns.find((c) => c.key === 'focus');
    expect(focusNode?.x).toBe(focusCol?.x);
  });

  it('超预算折叠 → 摘要节点（+N 上游来源）', () => {
    const summary = layout.nodes.find((n) => n.isSummary);
    expect(summary?.label).toBe('+3 上游来源');
    expect(summary?.summaryCount).toBe(3);
  });

  it('边端点坐标来自节点位置（右缘 → 左缘）', () => {
    const e = layout.edges.find((x) => x.key === 'el-1->item-1');
    const from = layout.nodes.find((n) => n.key === nodeKeyOf('element', 'el-1'));
    const to = layout.nodes.find((n) => n.key === nodeKeyOf('requirement_item', 'item-1'));
    expect(e?.fromX).toBe((from?.x ?? 0) + NODE_W);
    expect(e?.toX).toBe(to?.x);
  });

  it('可疑边带 ⚠ 角标与虚线样式', () => {
    const suspect = layout.edges.find((x) => x.edge.status === 'suspect_pending_review');
    expect(suspect?.marker).toBe('⚠');
    expect(suspect?.dashed).toBe(true);
  });

  it('端点被折叠的悬空边不绘制', () => {
    const withDangling = chain('upstream', [
      level(
        1,
        [node('element', 'el-1')],
        [
          edge('element', 'el-1', 'requirement_item', 'item-1'),
          edge('element', 'el-folded', 'requirement_item', 'item-1'), // el-folded 未进窗口
        ],
        1,
      ),
    ]);
    const l = buildFlowLayout(focus, withDangling, null);
    expect(l.edges.map((e) => e.key)).toEqual(['el-1->item-1']);
  });
});

describe('buildSwimlaneLayout（固定五泳道、只读重排）', () => {
  const layout = buildSwimlaneLayout(focus, upstream, downstream);

  it('恒为五列泳道且顺序固定', () => {
    expect(layout.columns.map((c) => c.key)).toEqual(SWIMLANE_ORDER.map((t) => `lane-${t}`));
  });

  it('节点按类型归入泳道，焦点保留高亮', () => {
    const focusNode = layout.nodes.find((n) => n.isFocus);
    const itemLane = layout.columns[SWIMLANE_ORDER.indexOf('requirement_item')];
    expect(focusNode?.x).toBe(itemLane.x);
    // 泳道布局不携带摘要节点
    expect(layout.nodes.some((n) => n.isSummary)).toBe(false);
  });

  it('同一窗口内边照常绘制', () => {
    expect(layout.edges.length).toBeGreaterThanOrEqual(3);
  });
});

describe('追溯路径（面包屑；双击追加、原路回退）', () => {
  const p1 = pushHop([], { nodeType: 'requirement_item', ref: 'item-1', label: 'FR-001' });
  const p2 = pushHop(p1, { nodeType: 'chart', ref: 'chart-1', label: '流程图' });

  it('追加一跳', () => {
    expect(p2.map((h) => h.ref)).toEqual(['item-1', 'chart-1']);
  });

  it('重定心到路径上已有节点 = 回退到该跳', () => {
    const back = pushHop(p2, { nodeType: 'requirement_item', ref: 'item-1', label: 'FR-001' });
    expect(back.map((h) => h.ref)).toEqual(['item-1']);
  });

  it('backTo 截断到指定跳', () => {
    expect(backTo(p2, 0).map((h) => h.ref)).toEqual(['item-1']);
  });
});

describe('展示映射完备性', () => {
  it('五种边状态均有样式（含派生）', () => {
    expect(Object.keys(edgeStatusMeta).sort()).toEqual(
      ['derived', 'effective', 'invalid', 'pre_established', 'suspect_pending_review'].sort(),
    );
  });

  it('缺口类别与补全导航目标齐备', () => {
    expect(Object.keys(gapKindMeta)).toHaveLength(6); // P4 增 business_knowledge_unreferenced
    expect(gapKindMeta.business_knowledge_unreferenced.label).toBe('业务知识未被引用');
    for (const meta of Object.values(navTargetMeta)) {
      expect(meta.workbenchKey).toBeTruthy();
    }
  });
});

describe('filterChainByWing（P4 业务知识过滤器：仅留 element 业务翼节点）', () => {
  const mixed = chain('upstream', [
    level(
      1,
      [
        { node_type: 'element', ref: 'e-term', label: '术语', sub_label: 'term' },
        { node_type: 'element', ref: 'e-func', label: '功能', sub_label: 'functional_requirement' },
        node('material', 'mat-1', '材料一'),
      ],
      [
        edge('element', 'e-term', 'requirement_item', 'item-1'),
        edge('element', 'e-func', 'requirement_item', 'item-1'),
      ],
    ),
  ]);

  it("wing='all' 为恒等（原样返回）", () => {
    expect(filterChainByWing(mixed, 'all')).toBe(mixed);
    expect(filterChainByWing(null, 'business')).toBeNull();
  });

  it("wing='business' 仅保留业务翼 element；非 element 节点不受影响", () => {
    const out = filterChainByWing(mixed, 'business')!;
    const refs = out.levels[0].nodes.map((n) => n.ref).sort();
    expect(refs).toEqual(['e-term', 'mat-1']); // 需求翼 element e-func 被剔除，材料保留
  });

  it('过滤后端点缺失的边由布局器剔除（不绘制悬空边）', () => {
    const out = filterChainByWing(mixed, 'business');
    const layout = buildFlowLayout(focus, out, null);
    expect(layout.edges.map((e) => e.key)).toEqual(['e-term->item-1']);
  });
});

// ---- 资产 → 文档片段预览投影（04A §8 增补）----

import { buildFragmentPreview } from '../src/view-models/trace-graph';
import type { AssetFragmentRead } from '../src/api/publication';

function fragmentFixture(overrides: Partial<AssetFragmentRead> = {}): AssetFragmentRead {
  return {
    project_ref: 'p1',
    asset_type: 'chart',
    asset_ref: 'c1',
    document_ref: 'd1',
    document_title: '需求规格说明',
    document_status: 'markdown_finalized',
    draft_ref: 'md1',
    draft_version: 3,
    draft_status: 'finalized',
    index_version: 2,
    in_current_index: true,
    baseline_ref: null,
    fragments: [
      {
        section_key: 'requirements.charts', section_number: '3.5', section_title: '需求图表',
        start_line: 10, end_line: 14, markdown: '**图：流程图**\n```mermaid\nflowchart TD\n  A-->B\n```',
      },
    ],
    next_action: null,
    ...overrides,
  };
}

describe('buildFragmentPreview（文档片段预览状态口径）', () => {
  it('定稿 + 上下文文本 + 片段透传', () => {
    const vm = buildFragmentPreview(fragmentFixture());
    expect(vm.statusText).toBe('Markdown 定稿');
    expect(vm.statusTone).toBe('success');
    expect(vm.contextText).toBe('需求规格说明 · 索引 v2 · Markdown v3');
    expect(vm.baselineText).toBeNull();
    expect(vm.staleText).toBeNull();
    expect(vm.fragments).toHaveLength(1);
    expect(vm.emptyText).toBeNull();
  });

  it('基线冻结 → 附加徽标；superseded → 待重新生成', () => {
    expect(buildFragmentPreview(fragmentFixture({ baseline_ref: 'b1' })).baselineText).toBe(
      '已冻结为发布基线',
    );
    const stale = buildFragmentPreview(fragmentFixture({ draft_status: 'superseded' }));
    expect(stale.statusText).toContain('待重新生成');
    expect(stale.statusTone).toBe('warning');
  });

  it('不在当前索引 → 历史稿提示；未编排 → 空态承接 next_action', () => {
    const notInIndex = buildFragmentPreview(fragmentFixture({ in_current_index: false }));
    expect(notInIndex.staleText).toContain('不在当前文档内容索引');
    const empty = buildFragmentPreview(
      fragmentFixture({
        document_ref: null, document_title: null, draft_ref: null, draft_version: null,
        draft_status: null, index_version: null, in_current_index: false,
        fragments: [], next_action: '项目尚未进行文档编排（SCN-005-P01）',
      }),
    );
    expect(empty.statusText).toBe('尚无 Markdown 稿');
    expect(empty.emptyText).toContain('尚未进行文档编排');
  });
});

// ---- 卡片语义修正（2026-07-12）：材料卡片锚点引文 + 文档节点片段触发 ----

import {
  materialCardQuote,
  materialExcerpts,
  resolveFragmentTarget,
} from '../src/view-models/trace-graph';
import type { TraceEdgeRead as EdgeRead } from '../src/api/trace';

function quoteEdge(
  matRef: string,
  elRef: string,
  quotes: string[] | null,
): EdgeRead {
  return {
    edge_key: `me:${matRef}:${elRef}`,
    relation_kind: 'material_element',
    origin: 'derived',
    upstream_type: 'material',
    upstream_ref: matRef,
    downstream_type: 'element',
    downstream_ref: elRef,
    status: 'derived',
    anchor_quote: quotes?.[0] ?? null,
    anchor_quotes: quotes ?? [],
  };
}

describe('materialCardQuote（材料卡片主文本取值顺序）', () => {
  const edges: EdgeRead[] = [
    quoteEdge('mat-1', 'el-1', ['系统应支持导出 docx']),
    quoteEdge('mat-1', 'el-2', ['导出耗时不超过五秒']),
    quoteEdge('mat-1', 'el-3', null), // 无锚点边不参与取值
    quoteEdge('mat-2', 'el-9', ['其他材料的引文']),
  ];

  it('默认取窗口序第一条带引文的边，计数=该材料带引文边数', () => {
    expect(materialCardQuote('mat-1', edges, null)).toEqual({
      quote: '系统应支持导出 docx',
      total: 2,
    });
  });

  it('选中边优先：选中该材料另一条边时卡片随选中切换', () => {
    expect(materialCardQuote('mat-1', edges, 'me:mat-1:el-2').quote).toBe('导出耗时不超过五秒');
  });

  it('选中边不属于该材料时不影响取值', () => {
    expect(materialCardQuote('mat-1', edges, 'me:mat-2:el-9').quote).toBe('系统应支持导出 docx');
  });

  it('窗口内无带引文的边 → quote=null（视图回退原文头 label）', () => {
    expect(materialCardQuote('mat-3', edges, null)).toEqual({ quote: null, total: 0 });
    expect(materialCardQuote('mat-1', [quoteEdge('mat-1', 'el-3', null)], null).quote).toBeNull();
  });

  it('单条引文不出「等 N 处」计数（total=1）', () => {
    expect(materialCardQuote('mat-2', edges, null).total).toBe(1);
  });

  it('选中无引文边 → 卡片回退原文头（quote=null），不错借其他知识项引文', () => {
    // 合并裁定修复（2026-07-12 代码审查 K1）：与边详情「卡片回退原文头」提示保持一致
    expect(materialCardQuote('mat-1', edges, 'me:mat-1:el-3')).toEqual({
      quote: null,
      total: 2,
    });
  });
});

describe('materialExcerpts（详情面板「原文摘录」清单）', () => {
  it('逐知识项列全量引文；无引文边不入清单', () => {
    const out = materialExcerpts('mat-1', [
      quoteEdge('mat-1', 'el-1', ['片段一', '片段二']),
      quoteEdge('mat-1', 'el-3', null),
      quoteEdge('mat-2', 'el-9', ['别家']),
    ]);
    expect(out).toEqual([
      { edgeKey: 'me:mat-1:el-1', elementRef: 'el-1', quotes: ['片段一', '片段二'] },
    ]);
  });
});

describe('resolveFragmentTarget（文档片段预览触发目标）', () => {
  const itemFocus = { node_type: 'requirement_item', ref: 'item-1' } as const;
  const materialFocus = { node_type: 'material', ref: 'mat-1' } as const;
  const nodeSel = (nodeType: string, ref: string, isSummary = false) => ({
    kind: 'node' as const,
    node: { nodeType, ref, isSummary },
  });

  it('选中条目/图表节点 → 该节点自身', () => {
    expect(resolveFragmentTarget(nodeSel('requirement_item', 'item-2'), itemFocus)).toEqual({
      type: 'requirement_item',
      ref: 'item-2',
    });
    expect(resolveFragmentTarget(nodeSel('chart', 'chart-1'), itemFocus)).toEqual({
      type: 'chart',
      ref: 'chart-1',
    });
  });

  it('文档节点触发分支：焦点=条目/图表时取焦点资产', () => {
    expect(resolveFragmentTarget(nodeSel('document', 'doc-1'), itemFocus)).toEqual({
      type: 'requirement_item',
      ref: 'item-1',
    });
  });

  it('文档节点 + 焦点非条目/图表 → 不触发（null，不报错）', () => {
    expect(resolveFragmentTarget(nodeSel('document', 'doc-1'), materialFocus)).toBeNull();
    expect(resolveFragmentTarget(nodeSel('document', 'doc-1'), null)).toBeNull();
  });

  it('材料/知识项节点与摘要节点不触发', () => {
    expect(resolveFragmentTarget(nodeSel('material', 'mat-1'), itemFocus)).toBeNull();
    expect(resolveFragmentTarget(nodeSel('element', 'el-1'), itemFocus)).toBeNull();
    expect(resolveFragmentTarget(nodeSel('document', 'doc-1', true), itemFocus)).toBeNull();
  });

  it('文档承接边 → 上游资产；未选中 → 焦点本身（条目/图表时）', () => {
    const docEdge = {
      kind: 'edge' as const,
      edge: {
        ...quoteEdge('mat-1', 'el-1', null),
        edge_key: 'di:item-1:doc-1',
        relation_kind: 'document_reference',
        upstream_type: 'requirement_item' as const,
        upstream_ref: 'item-1',
        downstream_type: 'document' as const,
        downstream_ref: 'doc-1',
      },
    };
    expect(resolveFragmentTarget(docEdge, materialFocus)).toEqual({
      type: 'requirement_item',
      ref: 'item-1',
    });
    expect(resolveFragmentTarget(null, itemFocus)).toEqual({ type: 'requirement_item', ref: 'item-1' });
    expect(resolveFragmentTarget(null, materialFocus)).toBeNull();
  });
});
