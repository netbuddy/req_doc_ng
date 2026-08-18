import { describe, expect, it } from 'vitest';
import {
  buildQualityPanelVM,
  segmentAnnotations,
  qualityDimensionText,
} from '../src/view-models/requirement-quality';
import type { ItemQualityRead } from '../src/api/quality';

const BASE = '当订单实付金额 ≥ 500 元时，系统应尽快将订单转入人工审核队列。';

function q(overrides: Partial<ItemQualityRead> = {}): ItemQualityRead {
  return {
    item_ref: 'I1', req_no: 'REQ-002', base_expression: BASE, has_diagnosis: true,
    round_ref: 'R1', round_no: 2, status: 'completed',
    verdict_kind: 'revise', verdict_summary: '含模糊量词，建议修订。',
    quality_profile: { overall: 72, dimensions: [{ key: 'verifiable', score: 70, note: '' }] },
    findings: [
      {
        finding_ref: 'F1', finding_type: 'untestable', diagnosis_summary: '「尽快」不可量化',
        basis_summary: '', rule_code: 'INCOSE-R7', dimension: 'verifiable', severity: 'medium',
        evidence_span: '尽快',
      },
    ],
    revision_points: [
      { point_ref: 'P1', label: '量化', finding_index: 0, find: '尽快', replace: '在 5 秒内', basis: '', group: null },
    ],
    ears_rewrite: { pattern_type: 'event_driven', lines: ['WHEN …'], note: '' },
    source_alignments: [],
    ...overrides,
  } as ItemQualityRead;
}

describe('buildQualityPanelVM', () => {
  it('定位 evidence_span 偏移 + autofix 绑定修订点', () => {
    const vm = buildQualityPanelVM(q());
    expect(vm.hasDiagnosis).toBe(true);
    expect(vm.overall).toBe(72);
    const f = vm.findings[0];
    expect(f.ruleLabel).toBe('模糊量词');
    expect(f.autofix).toBe(true);
    expect(f.pointRef).toBe('P1');
    expect(f.span).toEqual({ start: BASE.indexOf('尽快'), end: BASE.indexOf('尽快') + 2 });
    expect(vm.spans).toHaveLength(1);
    expect(vm.dims[0].label).toBe('可验证');
  });

  it('span 不唯一/不存在 → 无 span（降级不错位）', () => {
    const vm = buildQualityPanelVM(q({
      base_expression: '记录日志并记录日志',
      findings: [{
        finding_ref: 'F1', finding_type: 'untestable', diagnosis_summary: 'x', basis_summary: '',
        rule_code: null, dimension: null, severity: 'low', evidence_span: '记录日志',
      }] as ItemQualityRead['findings'],
      revision_points: [],
    }));
    expect(vm.findings[0].span).toBeNull();
    expect(vm.spans).toHaveLength(0);
    expect(vm.findings[0].autofix).toBe(false);
  });

  it('无诊断 → 空面板', () => {
    const vm = buildQualityPanelVM(q({ has_diagnosis: false }));
    expect(vm.hasDiagnosis).toBe(false);
    expect(vm.baseExpression).toBe(BASE);
  });
});

describe('segmentAnnotations', () => {
  it('按 span 切段，非 span 段无标注', () => {
    const segs = segmentAnnotations('ab尽快cd', [{ n: 1, start: 2, end: 4, severity: 'medium' }]);
    expect(segs.map((s) => s.text)).toEqual(['ab', '尽快', 'cd']);
    expect(segs[1].span?.n).toBe(1);
    expect(segs[0].span).toBeNull();
  });
});

describe('qualityDimensionText', () => {
  it('可追溯（非简洁性）', () => {
    expect(qualityDimensionText('traceable')).toBe('可追溯');
  });
});

// 修订点与发现项的配对（走查反馈第⑨组）：finding_index 是模型输出序，与 findings 数组的
// 读出序不是一回事，照它当下标用会把「一键修复」按钮挂到错误的行上。
describe('修订点绑定发现项', () => {
  const twoFindings: Partial<ItemQualityRead> = {
    findings: [
      {
        finding_ref: 'F-second', finding_type: 'untestable', diagnosis_summary: '第二条：无法验证',
        basis_summary: '', rule_code: 'INCOSE-R7', dimension: 'verifiable', severity: 'low',
        evidence_span: '尽快',
      },
      {
        finding_ref: 'F-first', finding_type: 'ambiguous_expression', diagnosis_summary: '第一条：表达含糊',
        basis_summary: '', rule_code: 'ISO-29148-A', dimension: 'unambiguous', severity: 'high',
        evidence_span: '人工审核队列',
      },
    ],
  } as Partial<ItemQualityRead>;

  it('按发现项引用配对：读出序与模型输出序不同也挂对行', () => {
    // 修订点针对模型输出的第二条（引用 F-second），而它在读出序里排第一。
    const vm = buildQualityPanelVM(q({
      ...twoFindings,
      revision_points: [
        { point_ref: 'P9', label: '量化', finding_index: 1, finding_ref: 'F-second',
          find: '尽快', replace: '在 5 秒内', basis: '', group: null },
      ],
    } as Partial<ItemQualityRead>));
    const target = vm.findings.find((f) => f.findingRef === 'F-second')!;
    const other = vm.findings.find((f) => f.findingRef === 'F-first')!;
    expect(target.pointRef).toBe('P9');
    expect(target.autofix).toBe(true);
    // 下标 1 指向的是读出序第二条（F-first），它不该被挂上按钮。
    expect(other.pointRef).toBeNull();
    expect(other.autofix).toBe(false);
  });

  it('存量轮次没有引用时回退按下标配对，行为与改前一致', () => {
    const vm = buildQualityPanelVM(q({
      ...twoFindings,
      revision_points: [
        { point_ref: 'P9', label: '量化', finding_index: 1,
          find: '尽快', replace: '在 5 秒内', basis: '', group: null },
      ],
    } as Partial<ItemQualityRead>));
    expect(vm.findings[1].pointRef).toBe('P9');
    expect(vm.findings[0].pointRef).toBeNull();
  });
});

// 编号口径（用户走查第 1 轮意见）：编号是问题序号，不是原文标注序号——
// 面板右上写「检出 4 项」而列表只编到 3、还有一行「·」，读的人无法判断到底几个问题。
describe('发现项编号与行序', () => {
  it('每条问题都有编号，行按编号升序；原文标注只留可唯一定位且不重叠者', () => {
    // 第二条的高亮范围被第一条包住 → 参与不了原文标注，但仍须有编号。
    const vm = buildQualityPanelVM(q({
      findings: [
        {
          finding_ref: 'F-outer', finding_type: 'ambiguous_expression', diagnosis_summary: '外层范围',
          basis_summary: '', rule_code: 'ISO-29148-A', dimension: 'unambiguous', severity: 'high',
          evidence_span: '系统应尽快将订单转入人工审核队列', vetoed: false, can_veto: true, source_attested: false,
        },
        {
          finding_ref: 'F-inner', finding_type: 'untestable', diagnosis_summary: '被包住的范围',
          basis_summary: '', rule_code: 'INCOSE-R7', dimension: 'verifiable', severity: 'medium',
          evidence_span: '尽快', vetoed: false, can_veto: true, source_attested: false,
        },
        {
          finding_ref: 'F-nospan', finding_type: 'untestable', diagnosis_summary: '原文里定位不到',
          basis_summary: '', rule_code: 'SMELL-UNDEF', dimension: 'verifiable', severity: 'low',
          evidence_span: '这段文字不在原文里', vetoed: false, can_veto: true, source_attested: false,
        },
      ],
      revision_points: [],
    } as Partial<ItemQualityRead>));

    expect(vm.findings.map((f) => f.n)).toEqual([1, 2, 3]);   // 无一为 0（界面上就不会出现「·」）
    // 包住别人的那条不能把里面的挤掉：内层「尽快」拿到它那一段，外层取剩下最长的一段空档，
    // 于是两条都有上标。编号按上标在原文里出现的先后排，内层在前。
    expect(vm.findings.map((f) => f.findingRef)).toEqual(['F-inner', 'F-outer', 'F-nospan']);
    expect(vm.spans.map((s) => s.n)).toEqual([1, 2]);          // 两条上标，各出现一次
    expect(BASE.slice(vm.findings[0].span!.start, vm.findings[0].span!.end)).toBe('尽快');
    expect(BASE.slice(vm.findings[1].span!.start, vm.findings[1].span!.end)).toBe('将订单转入人工审核队列');
    // 原文里定位不到的那条：有编号，但不给「定位原文」入口（hasSpan=false → 面板不渲染定位按钮）
    expect(vm.findings[2].span).toBeNull();
    expect(vm.findings[2].hasSpan).toBe(false);
    expect(vm.findings[0].hasSpan).toBe(true);
  });
});

// no_blocker 通过项（C1+C2 演示默认画面）：不编号、不计问题数、不给定位入口，
// 单列一行白话通过说明。演示 stub 的 pass 轮次每个已确认条目都只产一条 no_blocker，
// 此前它被编成「① 中 …[↳ 定位原文]」而副驾卡写「检出 0 项」，两个数字当面打架。
describe('no_blocker 通过项', () => {
  it('no_blocker 不入编号列表、进 passNotes；真发现项照常编号', () => {
    const vm = buildQualityPanelVM(q({
      verdict_kind: 'revise',
      findings: [
        {
          finding_ref: 'F-pass', finding_type: 'no_blocker', diagnosis_summary: '来源依据可定位，未发现来源断裂。',
          basis_summary: '', rule_code: null, dimension: null, severity: 'medium', evidence_span: null,
          vetoed: false, can_veto: false, source_attested: false,
        },
        {
          finding_ref: 'F-real', finding_type: 'untestable', diagnosis_summary: '「尽快」不可量化',
          basis_summary: '', rule_code: 'INCOSE-R7', dimension: 'verifiable', severity: 'medium',
          evidence_span: '尽快', vetoed: false, can_veto: true, source_attested: false,
        },
      ],
      revision_points: [],
    } as Partial<ItemQualityRead>));

    // 列表里只剩真发现项，编号从 1 起；no_blocker 不占号。
    expect(vm.findings.map((f) => f.findingRef)).toEqual(['F-real']);
    expect(vm.findings[0].n).toBe(1);
    // 通过说明单列，白话原文（无阻断结论）。
    expect(vm.passNotes).toEqual(['来源依据可定位，未发现来源断裂。']);
    // 计数同源：问题数＝编号列表长度，不含 no_blocker。
    expect(vm.findings.length).toBe(1);
  });

  it('纯 pass 轮次（只有一条 no_blocker）：零问题、只有一行通过说明', () => {
    const vm = buildQualityPanelVM(q({
      verdict_kind: 'pass',
      findings: [
        {
          finding_ref: 'F-pass', finding_type: 'no_blocker', diagnosis_summary: '',
          basis_summary: '', rule_code: null, dimension: null, severity: 'medium', evidence_span: null,
          vetoed: false, can_veto: false, source_attested: false,
        },
      ],
      revision_points: [],
    } as Partial<ItemQualityRead>));

    expect(vm.findings).toHaveLength(0);
    // 摘要为空时退回一句默认白话文案。
    expect(vm.passNotes).toEqual(['未发现阻断问题']);
    // tally 不把 no_blocker 计进任何一档。
    expect(vm.tally).toEqual({ high: 0, medium: 0, low: 0 });
  });
});

describe('已标为「不是问题」的发现项（C41/C10 前端半）', () => {
  it('移出问题列表：不编号、不计严重度、不给一键修复，改列为一行说明', () => {
    const vm = buildQualityPanelVM(q({
      findings: [
        {
          finding_ref: 'F1', finding_type: 'untestable', diagnosis_summary: '「尽快」不可量化',
          basis_summary: '', rule_code: 'INCOSE-R7', dimension: 'verifiable', severity: 'high',
          evidence_span: '尽快', vetoed: true,
        },
        {
          finding_ref: 'F2', finding_type: 'missing_field', diagnosis_summary: '缺审核时限',
          basis_summary: '', rule_code: 'SMELL-UNDEF', dimension: 'complete', severity: 'medium',
          evidence_span: '人工审核队列', vetoed: false,
        },
        // vetoed 是后端已下发、生成的接口类型文件尚未再生的字段（见 requirement-quality.ts 注）
      ] as unknown as ItemQualityRead['findings'],
    }));

    expect(vm.findings.map((f) => f.findingRef)).toEqual(['F2']);
    expect(vm.tally.high).toBe(0);  // 已标记的那条不再计入严重度色块
    expect(vm.spans).toHaveLength(1);  // 原文标注也不给它
    expect(vm.passNotes.some((n) => n.includes('你已标为不是问题'))).toBe(true);
  });

  it('没有 vetoed 字段的存量投影一律按未标记处理', () => {
    const vm = buildQualityPanelVM(q());
    expect(vm.findings).toHaveLength(1);
    expect(vm.findings[0].autofix).toBe(true);
  });
});

// K2（2026-07-26 冷审查消费）：区4 必须与区5 用同一把尺看「哪些发现项还算问题」。
// 此前它只镜像了否决，没镜像人工确认降格：区5 对同一条写着「这条不用改，采纳时不会应用
// 它」，区4 照旧给它序号、严重度色块和会写库的〔一键修复〕——同屏两处指令相反，其中一处
// 点下去会改写条目表达并自动发起增量诊断。
describe('人工确认降格在区4 的镜像', () => {
  const degraded = {
    finding_ref: 'F-SRC', finding_type: 'source_inconsistency' as const,
    diagnosis_summary: '表达与来源要素对不上。', basis_summary: '来源＝人工确认',
    rule_code: 'SRC-DRIFT', dimension: 'consistent', severity: 'high',
    evidence_span: '人工审核', vetoed: false, can_veto: false, source_attested: true,
  };
  const real = {
    finding_ref: 'F-VAG', finding_type: 'untestable' as const,
    diagnosis_summary: '「尽快」不可量化', basis_summary: '', rule_code: 'INCOSE-R7',
    dimension: 'verifiable', severity: 'medium', evidence_span: '尽快',
    vetoed: false, can_veto: true, source_attested: false,
  };

  it('降格项不进问题列表、不计数、不标注原文，改列一行「不用你处理」说明', () => {
    const vm = buildQualityPanelVM(q({
      verdict_kind: 'revise',
      findings: [degraded, real],
      revision_points: [
        { point_ref: 'P1', label: '对齐来源', finding_index: 0, finding_ref: 'F-SRC',
          find: '人工审核', replace: '人工复核', basis: '', group: null },
      ],
    } as Partial<ItemQualityRead>));

    expect(vm.findings.map((f) => f.findingRef)).toEqual(['F-VAG']);
    expect(vm.findings.length).toBe(1);              // 「本条目检出 N 项」与区5 同数
    expect(vm.spans.map((s) => s.n)).toEqual([1]);   // 原文波浪线不再给降格项
    expect(vm.passNotes.some((n) => n.includes('不用你处理（来源＝人工确认）'))).toBe(true);
  });

  it('降格项不给〔一键修复〕：那个按钮会直接写库改写条目表达', () => {
    const vm = buildQualityPanelVM(q({
      verdict_kind: 'revise',
      findings: [degraded],
      revision_points: [
        { point_ref: 'P1', label: '对齐来源', finding_index: 0, finding_ref: 'F-SRC',
          find: '人工审核', replace: '人工复核', basis: '', group: null },
      ],
    } as Partial<ItemQualityRead>));

    expect(vm.findings).toHaveLength(0);
    expect(vm.findings.some((f) => f.autofix)).toBe(false);
  });

  it('没有降格标记时口径不变：来源类发现照常是问题', () => {
    const vm = buildQualityPanelVM(q({
      verdict_kind: 'revise',
      findings: [{ ...degraded, source_attested: false, can_veto: true }],
      revision_points: [],
    } as Partial<ItemQualityRead>));

    expect(vm.findings.map((f) => f.findingRef)).toEqual(['F-SRC']);
  });
});
