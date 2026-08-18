import { describe, expect, it } from 'vitest';
import {
  MAX_LEVEL,
  assemblesRequirementItems,
  autoKey,
  buildDesignerPreview,
  buildTemplateJson,
  DEFAULT_EXPORT_BINDING,
  emptyNode,
  hasAuthoredText,
  numberByNodeId,
  parseDraftState,
  sectionsToTree,
  serializeDraftState,
  treeToSections,
  type DesignerNode,
} from '../src/view-models/template-designer';
import type { TemplateSectionRead } from '../src/api/publication';

function node(overrides: Partial<DesignerNode>): DesignerNode {
  return emptyNode(overrides);
}

describe('treeToSections（树 → 扁平投影）', () => {
  it('level=深度、number 前序派生、key 由编号派生且唯一', () => {
    const tree: DesignerNode[] = [
      node({
        title: '引言',
        children: [
          node({ title: '编写目的' }),
          node({ title: '范围', children: [node({ title: '子范围' })] }),
        ],
      }),
      node({ title: '总体描述' }),
    ];
    const flat = treeToSections(tree);
    expect(flat.map((s) => s.number)).toEqual(['1', '1.1', '1.2', '1.2.1', '2']);
    expect(flat.map((s) => s.level)).toEqual([1, 2, 2, 3, 1]);
    expect(flat.map((s) => s.key)).toEqual(['sec-1', 'sec-1-1', 'sec-1-2', 'sec-1-2-1', 'sec-2']);
    // key 唯一
    expect(new Set(flat.map((s) => s.key)).size).toBe(flat.length);
  });

  it('删除中间节点后其后兄弟编号自动前移（投影每次重算）', () => {
    const tree: DesignerNode[] = [
      node({ title: 'A' }),
      node({ title: 'B' }),
      node({ title: 'C' }),
    ];
    expect(treeToSections(tree).map((s) => s.number)).toEqual(['1', '2', '3']);
    const afterDelete = [tree[0], tree[2]]; // 删 B
    expect(treeToSections(afterDelete).map((s) => s.number)).toEqual(['1', '2']);
    expect(treeToSections(afterDelete).map((s) => s.title)).toEqual(['A', 'C']);
  });

  it('level 封顶 MAX_LEVEL（深于 4 级不越界）', () => {
    let deep = node({ title: 'L5' });
    for (let i = 0; i < 5; i += 1) deep = node({ title: `L${5 - i - 1}`, children: [deep] });
    const flat = treeToSections([deep]);
    expect(Math.max(...flat.map((s) => s.level))).toBe(MAX_LEVEL);
  });

  it('boilerplate 仅装配 boilerplate 时带；examples 去空后仅非空时带', () => {
    const flat = treeToSections([
      node({ title: '默认文本', contentTypes: ['boilerplate'], boilerplate: '预填' }),
      node({ title: '撰稿', contentTypes: ['authored_text'], examples: ['范例一', '  ', ''] }),
      node({ title: '纯结构' }),
    ]);
    expect(flat[0].boilerplate).toBe('预填');
    expect(flat[1].boilerplate).toBeUndefined();
    expect(flat[1].examples).toEqual(['范例一']);
    expect(flat[2].examples).toBeUndefined();
  });

  it('keyOverride 优先于派生 key', () => {
    const flat = treeToSections([node({ title: '引言', keyOverride: 'intro' })]);
    expect(flat[0].key).toBe('intro');
  });
});

describe('sectionsToTree（扁平 → 树，复制起草反填）', () => {
  const sections: TemplateSectionRead[] = [
    { key: 'intro', number: '1', title: '引言', level: 1, purpose: '', content_types: [], required: false, repeatable: false, missing_policy: 'skip', examples: ['范例'] },
    { key: 'intro.purpose', number: '1.1', title: '编写目的', level: 2, purpose: '说明目的', content_types: ['boilerplate'], required: true, repeatable: false, missing_policy: 'skip', boilerplate: '本节…', examples: [] },
    { key: 'intro.scope', number: '1.2', title: '范围', level: 2, purpose: '', content_types: [], required: false, repeatable: false, missing_policy: 'skip' },
    { key: 'req', number: '2', title: '需求', level: 1, purpose: '', content_types: ['requirement_item:functional'], required: true, repeatable: true, missing_policy: 'block' },
  ];

  it('按 level 前序重建父子结构', () => {
    const tree = sectionsToTree(sections);
    expect(tree.map((n) => n.title)).toEqual(['引言', '需求']);
    expect(tree[0].children.map((n) => n.title)).toEqual(['编写目的', '范围']);
    expect(tree[0].children[0].children).toEqual([]);
  });

  it('examples/keyOverride/装配回显', () => {
    const tree = sectionsToTree(sections);
    expect(tree[0].examples).toEqual(['范例']);
    expect(tree[0].keyOverride).toBe('intro');
    expect(tree[1].contentTypes).toEqual(['requirement_item:functional']);
    expect(tree[1].repeatable).toBe(true);
  });

  it('往返一致：sectionsToTree → treeToSections 复现关键字段', () => {
    const roundTrip = treeToSections(sectionsToTree(sections));
    expect(roundTrip.map((s) => [s.key, s.number, s.level])).toEqual([
      ['intro', '1', 1],
      ['intro.purpose', '1.1', 2],
      ['intro.scope', '1.2', 2],
      ['req', '2', 1],
    ]);
    expect(roundTrip[0].examples).toEqual(['范例']);
    expect(roundTrip[1].boilerplate).toBe('本节…');
  });
});

describe('装配语义与预览', () => {
  it('assemblesRequirementItems / hasAuthoredText', () => {
    expect(assemblesRequirementItems(['requirement_item:functional'])).toBe(true);
    expect(assemblesRequirementItems(['chart', 'material'])).toBe(false);
    expect(hasAuthoredText(['authored_text'])).toBe(true);
    expect(hasAuthoredText(['boilerplate'])).toBe(false);
  });

  it('numberByNodeId 与 treeToSections 同序派生', () => {
    const tree = [node({ title: 'A', children: [node({ title: 'A1' })] }), node({ title: 'B' })];
    const map = numberByNodeId(tree);
    expect(map.get(tree[0].id)).toBe('1');
    expect(map.get(tree[0].children[0].id)).toBe('1.1');
    expect(map.get(tree[1].id)).toBe('2');
  });

  it('预览槽位标识：撰稿/逐条目成节/默认文本', () => {
    const rows = buildDesignerPreview([
      node({ title: '撰稿', contentTypes: ['authored_text'] }),
      node({ title: '功能', contentTypes: ['requirement_item:functional'], repeatable: true }),
      node({ title: '结构' }),
    ]);
    expect(rows[0].slotText).toContain('AI 起草初稿');
    expect(rows[1].slotText).toContain('逐条目成节');
    expect(rows[2].slotText).toBeNull();
  });

  it('autoKey：编号路径 → 稳定 key', () => {
    expect(autoKey('1.2.3')).toBe('sec-1-2-3');
  });
});

describe('buildTemplateJson（表单 → 模板 JSON）', () => {
  it('组装合法 JSON：schema 1.0 + 扁平 sections + heading 数组化', () => {
    const json = buildTemplateJson(
      { templateId: 'srs-x', title: '模板 X', description: '说明' },
      { ...DEFAULT_EXPORT_BINDING, headingSizesPt: '16, 14, 13' },
      [node({ title: '引言', contentTypes: ['boilerplate'], boilerplate: '预填' })],
    );
    const parsed = JSON.parse(json);
    expect(parsed.schema_version).toBe('1.0');
    expect(parsed.template_id).toBe('srs-x');
    expect(parsed.export_binding.heading_sizes_pt).toEqual([16, 14, 13]);
    expect(parsed.sections[0]).toMatchObject({ key: 'sec-1', number: '1', level: 1, boilerplate: '预填' });
  });
});

describe('草稿信封（serializeDraftState ↔ parseDraftState）', () => {
  const state = {
    info: { templateId: 'srs-y', title: '模板 Y', description: '' },
    binding: { ...DEFAULT_EXPORT_BINDING },
    tree: [node({ title: '引言', contentTypes: ['boilerplate'], boilerplate: '预填', children: [node({ title: '目的' })] })],
  };

  it('往返一致：info/binding/树结构与字段完整恢复（id 重生成不影响投影）', () => {
    const restored = parseDraftState(serializeDraftState(state));
    expect(restored).not.toBeNull();
    expect(restored!.info).toEqual(state.info);
    expect(restored!.binding).toEqual(state.binding);
    // 投影级等价：树 → sections 结果一致（id 不进投影）
    expect(treeToSections(restored!.tree)).toEqual(treeToSections(state.tree));
  });

  it('损坏/不兼容 payload 返回 null（回退空白起草由调用方处理）', () => {
    expect(parseDraftState('not-json')).toBeNull();
    expect(parseDraftState('{"designer_state_version":99,"tree":[]}')).toBeNull();
    expect(parseDraftState(JSON.stringify({ designer_state_version: 1, tree: [] }))).toBeNull();
  });
});

describe('buildDesignerPreview 实时同步编辑态', () => {
  it('章节说明（purpose）随行投影，供右栏预览展示', () => {
    const rows = buildDesignerPreview([
      node({ title: '引言', purpose: '说明文档的目的、范围与阅读约定。' }),
    ]);
    expect(rows[0].purpose).toBe('说明文档的目的、范围与阅读约定。');
  });
});
