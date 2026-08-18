import { describe, expect, it } from 'vitest';
import {
  buildConversionChain,
  buildStatusReconciliation,
  buildTypeBridges,
} from '../src/view-models/overview';
import type { OverviewRead } from '../src/api/overview';

type Chain = NonNullable<OverviewRead['conversion_chain']>;
type Bridge = NonNullable<OverviewRead['type_bridge']>[number];

/** 一套贴近演示库形状的链数据（含直建条目与未形成材料）。 */
function chain(overrides: Partial<Chain> = {}): Chain {
  return {
    elements_total: 426,
    elements_requirement: 290,
    elements_other: 136,
    elements_confirmed: 107,
    elements_pending: 183,
    materials_with_requirement: 40,
    materials_formed: 18,
    materials_unformed: 22,
    items_total: 109,
    items_pending: 81,
    items_confirmed: 25,
    items_closed: 3,
    items_sourced: 101,
    items_direct: 8,
    ...overrides,
  };
}

function bridge(overrides: Partial<Bridge> = {}): Bridge {
  return {
    key: 'functional',
    elements_total: 174,
    elements_confirmed: 64,
    elements_pending: 110,
    entered_formation: 59,
    not_formed: 5,
    not_formed_material_pending: 5,
    not_formed_not_adopted: 0,
    items_from_elements_same_type: 58,
    items_from_elements_other_type: 1,
    items_total: 63,
    items_sourced: 58,
    items_direct: 5,
    ...overrides,
  };
}

const LABELS = new Map([
  ['functional', '功能'],
  ['quality', '质量'],
  ['constraint', '约束'],
  ['data', '数据'],
  ['interface', '接口'],
]);

describe('需求转化链四节点', () => {
  it('四节点顺序与主数字：识别 → 人工确认 → 条目形成 → 需求条目', () => {
    const nodes = buildConversionChain(chain());
    expect(nodes.map((n) => n.key)).toEqual([
      'recognition', 'confirmation', 'formation', 'output',
    ]);
    expect(nodes.map((n) => n.stageLabel)).toEqual(['阶段一', '阶段二', '阶段三', '产出']);
    expect(nodes.map((n) => n.value)).toEqual(['426', '107', '18', '109']);
  });

  it('阶段一拆需求类与非需求类，措辞按原型（场景等归非需求类）', () => {
    const [recognition] = buildConversionChain(chain());
    expect(recognition.valueName).toBe('已有知识项');
    expect(recognition.parts).toEqual([
      { label: '需求类', value: '290' },
      { label: '非需求类（角色/术语/场景等）', value: '136' },
    ]);
    expect(recognition.gateHint).toContain('非需求类作分析上下文，不形成条目');
  });

  it('阶段二给确认进度：分母是需求类知识项，不是全部知识项', () => {
    const confirmation = buildConversionChain(chain())[1];
    expect(confirmation.percent).toBe(37);            // 107/290
    expect(confirmation.progressText).toBe('确认进度 107/290（37%）');
    expect(confirmation.counter).toEqual({ label: '待确认', value: '183', tone: 'orange' });
  });

  it('阶段三按材料计数：已形成 18 份、尚未形成 22 份，分母 40 份', () => {
    const formation = buildConversionChain(chain())[2];
    expect(formation.value).toBe('18');
    expect(formation.valueName).toBe('份材料已形成条目');
    expect(formation.counter).toEqual({ label: '尚未形成', value: '22', tone: 'orange' });
    expect(formation.percent).toBe(45);               // 18/40
    expect(formation.progressText).toBe('识别出需求类知识项的材料共 40 份');
  });

  it('产出节点拆三状态；脚注说直建与可回溯，不写「已确认」也不提演示库', () => {
    const output = buildConversionChain(chain())[3];
    expect(output.parts.map((p) => `${p.label}${p.value}`)).toEqual(['待确认81', '已确认25', '已了结3']);
    expect(output.gateHint).toBe('含 8 条无知识项来源的直建条目；其余 101 条可回溯到知识项来源。');
    expect(output.gateHint).not.toContain('已确认知识项');
    expect(output.gateHint).not.toContain('演示');
    expect(output.gateHint).not.toContain('种子');
  });

  it('直建条目为 0 时产出节点不出「含 0 条直建」脚注', () => {
    const output = buildConversionChain(chain({ items_sourced: 109, items_direct: 0 }))[3];
    expect(output.gateHint).toBeNull();
  });

  it('进度取整不吞掉最后一个未完成的：289/290 显示 99% 而不是 100%', () => {
    const confirmation = buildConversionChain(chain({
      elements_requirement: 290, elements_confirmed: 289, elements_pending: 1,
    }))[1];
    expect(confirmation.percent).toBe(99);
    expect(confirmation.progressText).toBe('确认进度 289/290（99%）');
  });

  it('进度取整不吞掉已完成的第一个：1/300 显示 1% 而不是 0%', () => {
    const confirmation = buildConversionChain(chain({
      elements_requirement: 300, elements_confirmed: 1, elements_pending: 299,
    }))[1];
    expect(confirmation.percent).toBe(1);
  });

  it('真的全完成才给 100%，真的一个都没有才给 0%', () => {
    const allDone = buildConversionChain(chain({
      elements_requirement: 290, elements_confirmed: 290, elements_pending: 0,
    }))[1];
    expect(allDone.percent).toBe(100);
    const noneDone = buildConversionChain(chain({
      elements_requirement: 290, elements_confirmed: 0, elements_pending: 290,
    }))[1];
    expect(noneDone.percent).toBe(0);
  });

  it('空项目：分母为零时进度取 0，不出 NaN/除零', () => {
    const nodes = buildConversionChain(chain({
      elements_total: 0, elements_requirement: 0, elements_other: 0,
      elements_confirmed: 0, elements_pending: 0,
      materials_with_requirement: 0, materials_formed: 0, materials_unformed: 0,
      items_total: 0, items_pending: 0, items_confirmed: 0, items_closed: 0,
      items_sourced: 0, items_direct: 0,
    }));
    expect(nodes[1].percent).toBe(0);
    expect(nodes[2].percent).toBe(0);
    expect(nodes.every((n) => !n.value.includes('NaN'))).toBe(true);
  });
});

describe('数字桥', () => {
  it('四行账逐行成形；跨对象那行用箭头而非等号', () => {
    const [b] = buildTypeBridges(chain(), [bridge()], LABELS);
    expect(b.rows.map((r) => r.key)).toEqual(['existing', 'confirmed', 'entered', 'items']);
    expect(b.rows.map((r) => r.operator)).toEqual(['＝', '＝', '→', '＝']);
    expect(b.rows[0].head).toBe('174 个已有功能知识项');
    expect(b.rows[0].parts.map((p) => p.text)).toEqual([
      '64 已确认', '110 待确认（停在阶段二，不能形成条目）',
    ]);
    expect(b.rows[2].parts.map((p) => p.text)).toEqual([
      '58 条功能条目', '1 条形成时被定为其它类型的条目',
    ]);
    expect(b.rows[3].parts.map((p) => p.text)).toEqual([
      '58 来自知识项', '5 直建（无知识项来源）',
    ]);
  });

  it('残差全是「材料未执行」时用原型措辞', () => {
    const [b] = buildTypeBridges(chain(), [bridge()], LABELS);
    expect(b.rows[1].parts[1].text).toBe('5 所在材料尚未执行形成（停在阶段三）');
  });

  it('出现「材料已执行但未被采用」时措辞降为中性并列出两种原因', () => {
    const [b] = buildTypeBridges(chain(), [bridge({
      not_formed: 5, not_formed_material_pending: 3, not_formed_not_adopted: 2,
    })], LABELS);
    // 不能再说「所在材料尚未执行形成」——其中 2 个的材料其实执行过了
    expect(b.rows[1].parts[1].text).toBe('5 尚未形成条目（所在材料尚未执行 3 · 形成时未采用 2）');
    expect(b.rows[1].parts[1].text).not.toContain('所在材料尚未执行形成');
  });

  it('结论句按原型措辞，X/Y 随类型替换', () => {
    const [b] = buildTypeBridges(chain(), [bridge()], LABELS);
    expect(b.conclusion).toContain('174 与 109 之间没有直接的算术关系');
    expect(b.conclusion).toContain('功能知识项 174 → 功能条目 63');
    expect(b.conclusion).toContain('需求类知识项 290 → 全部条目 109');
  });

  it('切到别的类型：结论句数字随之替换', () => {
    const [b] = buildTypeBridges(chain(), [bridge({
      key: 'quality', elements_total: 54, elements_confirmed: 23, elements_pending: 31,
      entered_formation: 20, not_formed: 3, not_formed_material_pending: 3,
      items_from_elements_same_type: 20, items_from_elements_other_type: 0,
      items_total: 21, items_sourced: 20, items_direct: 1,
    })], LABELS);
    expect(b.label).toBe('质量');
    expect(b.rows[0].head).toBe('54 个已有质量知识项');
    expect(b.conclusion).toContain('54 与 109 之间没有直接的算术关系');
    expect(b.conclusion).toContain('质量知识项 54 → 质量条目 21');
  });

  it('零知识项零条目的类型：给白话空态，不摆空账', () => {
    const [b] = buildTypeBridges(chain(), [bridge({
      key: 'interface', elements_total: 0, elements_confirmed: 0, elements_pending: 0,
      entered_formation: 0, not_formed: 0, not_formed_material_pending: 0,
      items_from_elements_same_type: 0, items_from_elements_other_type: 0,
      items_total: 0, items_sourced: 0, items_direct: 0,
    })], LABELS);
    expect(b.rows).toEqual([]);
    expect(b.emptyText).toBe('本项目暂无接口知识项与接口条目，尚无去向可算。');
    // 上方一行去向都没有，结论句里的「去向如上」无所指，故整句不渲染
    expect(b.conclusion).toBeNull();
  });

  it('零知识项但有直建条目：只给条目那一行，并说明均为直建', () => {
    const [b] = buildTypeBridges(chain(), [bridge({
      key: 'data', elements_total: 0, elements_confirmed: 0, elements_pending: 0,
      entered_formation: 0, not_formed: 0, not_formed_material_pending: 0,
      items_from_elements_same_type: 0, items_from_elements_other_type: 0,
      items_total: 4, items_sourced: 0, items_direct: 4,
    })], LABELS);
    expect(b.rows.map((r) => r.key)).toEqual(['items']);
    expect(b.emptyText).toBe('本项目暂无数据知识项，下列条目均为直建。');
    expect(b.conclusion).not.toBeNull();
  });

  it('零知识项但条目仍有来源：不得断言「均为直建」，与条目行的拆分数字自洽', () => {
    const [b] = buildTypeBridges(chain(), [bridge({
      key: 'interface', elements_total: 0, elements_confirmed: 0, elements_pending: 0,
      entered_formation: 0, not_formed: 0, not_formed_material_pending: 0,
      items_from_elements_same_type: 0, items_from_elements_other_type: 0,
      items_total: 3, items_sourced: 2, items_direct: 1,
    })], LABELS);
    expect(b.emptyText).toBe('本项目暂无接口知识项。');
    expect(b.emptyText).not.toContain('均为直建');
    expect(b.rows.map((r) => r.key)).toEqual(['items']);
    expect(b.rows[0].parts.map((p) => p.text)).toEqual([
      '2 来自知识项', '1 直建（无知识项来源）',
    ]);
  });

  it('直建条目为 0 时，条目行不摆「＋0 直建」这一段', () => {
    const [b] = buildTypeBridges(chain(), [bridge({
      items_total: 63, items_sourced: 63, items_direct: 0,
    })], LABELS);
    const itemsRow = b.rows.find((r) => r.key === 'items');
    expect(itemsRow?.parts.map((p) => p.text)).toEqual(['63 来自知识项']);
  });
});

describe('状态对账行', () => {
  it('三块之和等于资产盘点条目数：给出等式与通过标记', () => {
    const recon = buildStatusReconciliation(chain(), 109);
    expect(recon.equationText).toBe('81＋25＋3＝109');
    expect(recon.resultText).toBe('＝资产盘点「需求条目」✓');
    expect(recon.balanced).toBe(true);
  });

  it('不等时不粉饰：标为不一致并报出资产盘点的数', () => {
    const recon = buildStatusReconciliation(chain({ items_closed: 2 }), 109);
    expect(recon.balanced).toBe(false);
    expect(recon.equationText).toBe('81＋25＋2＝108');
    expect(recon.resultText).toBe('与资产盘点「需求条目」109 不一致，请核对');
  });

  it('资产盘点计数缺失时同样不判为通过', () => {
    const recon = buildStatusReconciliation(chain(), undefined);
    expect(recon.balanced).toBe(false);
  });
});
