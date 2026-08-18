/**
 * 资产导航树 VM 投影与跳转映射测试（04A §3.1 整合后的管理工作台默认视图）。
 * 事实源：docs/40-detailed-design/shared/需求资产工作台页面设计.md §4.1、§5、§6.2；
 * 数据来自资产读侧 AEP（AssetCatalogRead/ItemMaintenanceItemRead/AssetDetailRead），本测试验证 DTO→VM 投影。
 */
import { describe, expect, it } from 'vitest';
import type { AssetCatalogRead, AssetDetailRead, ItemMaintenanceItemRead } from '../src/api/assets';
import {
  ASSET_CONTEXT_ACTIONS,
  NAV_ASSET_TYPES,
  buildAssetDetailVM,
  buildAssetNavVM,
  buildTraceSummarySection,
  parseNodeKey,
  resolveAssetActionTarget,
} from '../src/view-models/requirement-assets';

const catalog: AssetCatalogRead = {
  project_ref: 'p1',
  groups: [
    {
      asset_type: 'requirement_item',
      count: 2,
      nodes: [
        { ref: 'i1', label: 'REQ-001 导出 docx', sub_label: 'functional', status: 'confirmed', updated_at: '2026-07-01T10:00:00' },
        { ref: 'i2', label: 'REQ-002 响应两秒', sub_label: 'quality', status: 'pending_confirmation', updated_at: '2026-07-02T09:00:00' },
      ],
    },
    {
      asset_type: 'material',
      count: 1,
      nodes: [{ ref: 'm1', label: '评审纪要', status: 'accepted', updated_at: '2026-06-30T08:00:00' }],
    },
    { asset_type: 'element', count: 0, nodes: [] },
    { asset_type: 'chart', count: 0, nodes: [] },
    { asset_type: 'trace_link', count: 5, nodes: [] },
    { asset_type: 'document', count: 0, nodes: [] },
    { asset_type: 'issue', count: 3, nodes: [] },
  ],
  trace_summary: { effective: 2, pre_established: 1, suspect: 1, invalid: 0 },
};

const items: ItemMaintenanceItemRead[] = [
  {
    ref: 'i1',
    req_no: 'REQ-001',
    expression: '导出 docx',
    req_type: 'functional',
    status: 'confirmed',
    updated_at: '2026-07-01T10:00:00',
    source_count: 1,
    revision_count: 0,
    verification_missing: false,
    priority_missing: false,
  },
  {
    ref: 'i2',
    req_no: 'REQ-002',
    expression: '响应两秒',
    req_type: 'quality',
    status: 'pending_confirmation',
    updated_at: '2026-07-02T09:00:00',
    source_count: 1,
    revision_count: 1,
    verification_missing: true,
    priority_missing: false,
  },
];

describe('资产导航树投影', () => {
  it('树只组织五类资产（追溯关系/问题项不入树），分组带计数', () => {
    const nav = buildAssetNavVM(catalog, items);
    expect(nav.map((g) => g.assetType)).toEqual([...NAV_ASSET_TYPES]);
    expect(nav.map((g) => `${g.label}${g.count}`)).toEqual([
      '材料1',
      '知识项0',
      '需求条目2',
      '图表0',
      '文档0',
    ]);
    expect(nav.some((g) => g.assetType === 'trace_link' || g.assetType === 'issue')).toBe(false);
  });

  it('需求条目分组按语义类型子分组，条目叶子带编号/状态/完备警示', () => {
    const nav = buildAssetNavVM(catalog, items);
    const itemGroup = nav.find((g) => g.assetType === 'requirement_item')!;
    expect(itemGroup.leaves).toHaveLength(0);
    expect(itemGroup.subgroups.map((s) => `${s.label}${s.count}`)).toEqual(['功能需求1', '质量属性1']);
    const qualityLeaf = itemGroup.subgroups[1].leaves[0];
    expect(qualityLeaf.idText).toBe('REQ-002');
    expect(qualityLeaf.statusText).toBe('待确认');
    expect(qualityLeaf.warnings).toEqual(['缺验收准则']);
    expect(qualityLeaf.key).toBe('requirement_item:i2');
  });

  it('非条目分组的叶子来自资产目录，状态映射为中文标签', () => {
    const nav = buildAssetNavVM(catalog, items);
    const materialGroup = nav.find((g) => g.assetType === 'material')!;
    expect(materialGroup.subgroups).toHaveLength(0);
    expect(materialGroup.leaves[0].idText).toBeNull();
    expect(materialGroup.leaves[0].title).toBe('评审纪要');
    expect(materialGroup.leaves[0].statusText).toBe('已接入');
    expect(parseNodeKey(materialGroup.leaves[0].key)).toEqual({ assetType: 'material', ref: 'm1' });
  });
});

describe('详情与摘要投影', () => {
  it('详情属性 key 映射为中文标签，yes/no 转 是/否', () => {
    const detail: AssetDetailRead = {
      asset_type: 'requirement_item',
      ref: 'i1',
      label: 'REQ-001 导出 docx',
      sub_label: 'functional',
      status: 'confirmed',
      summary: '系统应支持导出 docx',
      attributes: [
        { key: 'chart_coverage', value: '1' },
        { key: 'in_document_index', value: 'yes' },
      ],
      relations: [{ kind: 'covered_by_chart', asset_type: 'chart', ref: 'c1', label: '导出流程图' }],
    };
    const vm = buildAssetDetailVM(detail);
    expect(vm.typeText).toBe('需求条目 · 功能需求');
    expect(vm.statusText).toBe('已确认');
    expect(vm.facts).toContainEqual({ label: '图表覆盖数', value: '1' });
    expect(vm.facts).toContainEqual({ label: '已入文档索引', value: '是' });
    expect(vm.relations[0].kindText).toBe('图表覆盖');
  });

  it('时刻属性落本地时区；后端 "—" 兜底原样透传（issue #21）', () => {
    // 值域恒 ∈ {ISO 串, 字面量 "—"}（后端 _iso(x) or "—"）：前者换算落本地(17:30Z ⇒ 次日 01:30，
    // 旧 slice 手法给 UTC 原串 17:30)；后者不可解析 → 与占位同文，故 key 白名单已足。
    const detail: AssetDetailRead = {
      asset_type: 'requirement_item',
      ref: 'i1',
      label: 'REQ-001 导出 docx',
      sub_label: 'functional',
      status: 'confirmed',
      summary: '系统应支持导出 docx',
      attributes: [
        { key: 'updated_at', value: '2026-07-04T17:30:00+00:00' },
        { key: 'created_at', value: '—' },
      ],
      relations: [],
    };
    const vm = buildAssetDetailVM(detail);
    expect(vm.facts).toContainEqual({ label: '最近更新', value: '2026-07-05 01:30:00' });
    expect(vm.facts).toContainEqual({ label: '创建时间', value: '—' });
  });

  it('树底追溯摘要条四状态齐备，动作文案为去追溯分析', () => {
    const section = buildTraceSummarySection(catalog);
    expect(section.items.map((i) => `${i.label}${i.value}`)).toEqual(['有效2', '预建立1', '可疑1', '失效0']);
    expect(section.actionLabel).toBe('去追溯分析');
  });
});

describe('上下文动作跳转映射', () => {
  it('整合后动作收敛为三项（去需求维护/转问题项已撤销），并映射到目标工作台', () => {
    const targets = Object.fromEntries(
      ASSET_CONTEXT_ACTIONS.map((action) => [action.key, resolveAssetActionTarget(action)]),
    );
    expect(targets).toEqual({
      'go-traceability': 'traceability',
      'go-diagram': 'diagram',
      'go-release': 'release',
    });
  });

  it('禁用动作不产生跳转目标', () => {
    expect(
      resolveAssetActionTarget({ key: 'x', label: 'x', disabled: true, targetWorkbench: 'management' }),
    ).toBeNull();
  });
});

describe('知识项组两翼分层（P2）', () => {
  const catalogWithElements: AssetCatalogRead = {
    project_ref: 'p1',
    groups: [
      {
        asset_type: 'element',
        count: 4,
        nodes: [
          { ref: 'e1', label: '系统应导出', sub_label: 'functional_requirement', status: 'confirmed', updated_at: '2026-07-01T10:00:00' },
          { ref: 'e2', label: '角色：拣货员', sub_label: 'role', status: 'confirmed', updated_at: '2026-07-02T10:00:00' },
          { ref: 'e3', label: '术语：履约单', sub_label: 'term', status: 'confirmed', updated_at: '2026-07-03T10:00:00' },
          { ref: 'e4', label: '目标：提效', sub_label: 'goal', status: 'confirmed', updated_at: '2026-07-04T10:00:00' },
        ],
      },
      { asset_type: 'material', count: 0, nodes: [] },
      { asset_type: 'requirement_item', count: 0, nodes: [] },
      { asset_type: 'chart', count: 0, nodes: [] },
      { asset_type: 'trace_link', count: 0, nodes: [] },
      { asset_type: 'document', count: 0, nodes: [] },
      { asset_type: 'issue', count: 0, nodes: [] },
    ],
    trace_summary: { effective: 0, pre_established: 0, suspect: 0, invalid: 0 },
  };

  it('element 组恰好两子组「需求知识/业务知识」，叶子数守恒', () => {
    const nav = buildAssetNavVM(catalogWithElements, []);
    const elementGroup = nav.find((g) => g.assetType === 'element')!;
    expect(elementGroup.subgroups.map((s) => s.label)).toEqual(['需求知识', '业务知识']);
    const leafSum = elementGroup.subgroups.reduce((n, s) => n + s.leaves.length, 0);
    // 两子组叶子数之和 = 扁平叶子总数（AC-P2-01 守恒）
    expect(leafSum).toBe(4);
    expect(elementGroup.subgroups[0].count + elementGroup.subgroups[1].count).toBe(4);
  });

  it('翼内按 element_type 声明序排序（需求翼 functional_requirement 先于 goal；业务翼 term 先于 role）', () => {
    const nav = buildAssetNavVM(catalogWithElements, []);
    const elementGroup = nav.find((g) => g.assetType === 'element')!;
    const req = elementGroup.subgroups[0];
    const biz = elementGroup.subgroups[1];
    expect(req.leaves.map((l) => l.idText)).toEqual(['功能需求', '目标']);
    expect(biz.leaves.map((l) => l.idText)).toEqual(['术语', '角色']);
  });
});
