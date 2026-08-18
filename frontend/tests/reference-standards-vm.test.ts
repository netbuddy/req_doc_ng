// 引用标准目录 VM 纯测（T20260721）：目录行投影与校验、撰稿引用行的编号与插入、起草依据提示。
import { describe, expect, it } from 'vitest';
import {
  buildStandardRows,
  catalogStamp,
  emptyStandardDraft,
  standardDraftFrom,
  standardDraftToWrite,
  validateStandardDrafts,
  type StandardDraftVM,
} from '../src/view-models/settings';
import {
  draftBasisHint,
  formatCitationLines,
  insertCitations,
  nextCitationNumber,
} from '../src/view-models/publication';
import type {
  ReferenceStandardCatalogRead,
  ReferenceStandardRead,
} from '../src/api/settings';

function entry(overrides: Partial<ReferenceStandardRead> = {}): ReferenceStandardRead {
  return {
    key: 'gbt-8567',
    code: 'GB/T 8567-2006',
    title: '计算机软件文档编制规范',
    year: '2006',
    issuer: '国家标准化管理委员会',
    note: '文档种类与格式',
    category: 'national',
    category_label: '国家标准',
    url: 'https://openstd.samr.gov.cn/x',
    builtin: true,
    enabled: true,
    ...overrides,
  };
}

function catalog(overrides: Partial<ReferenceStandardCatalogRead> = {}): ReferenceStandardCatalogRead {
  return {
    entries: [
      entry({ key: 'iso-25010', code: 'ISO/IEC 25010:2023', title: '产品质量模型',
              category: 'international', category_label: '国际标准' }),
      entry(),
      entry({ key: 'gbt-11457', code: 'GB/T 11457-2006', title: '信息技术 软件工程术语' }),
    ],
    categories: [
      { key: 'international', label: '国际标准' },
      { key: 'national', label: '国家标准' },
      { key: 'guide', label: '指南' },
    ],
    builtin_count: 3,
    custom_count: 0,
    disabled_count: 0,
    source: 'builtin',
    updated_at: null,
    updated_by: null,
    ...overrides,
  };
}

function draft(overrides: Partial<StandardDraftVM> = {}): StandardDraftVM {
  return {
    key: 'own-1',
    code: 'Q/AB 001-2026',
    title: '企业内部需求评审规范',
    year: '2026',
    issuer: '示例企业',
    note: '内部评审依据',
    category: 'guide',
    url: '',
    ...overrides,
  };
}

describe('buildStandardRows', () => {
  it('内置条目与自有条目同表，按「类别次序 → 标准号」排序（与后端一致）', () => {
    const rows = buildStandardRows(catalog(), [draft()], new Set());
    expect(rows.map((r) => r.key)).toEqual(['iso-25010', 'gbt-11457', 'gbt-8567', 'own-1']);
    expect(rows.map((r) => r.categoryKey)).toEqual([
      'international', 'national', 'national', 'guide',
    ]);
  });

  it('被停用的内置条目仍在表里（供恢复），标 enabled=false 与「内置 · 已停用」', () => {
    const rows = buildStandardRows(catalog(), [], new Set(['gbt-8567']));
    const disabled = rows.find((r) => r.key === 'gbt-8567');
    expect(disabled).toMatchObject({ enabled: false, sourceText: '内置 · 已停用', builtin: true });
    expect(rows.find((r) => r.key === 'gbt-11457')).toMatchObject({
      enabled: true, sourceText: '内置',
    });
    expect(rows).toHaveLength(3); // 停用不等于从表里消失
  });

  it('自有条目带回它在草稿数组里的下标（编辑/删除按此定位），内置条目为 -1', () => {
    const rows = buildStandardRows(catalog(), [draft({ key: 'a' }), draft({ key: 'b' })], new Set());
    expect(rows.find((r) => r.key === 'a')?.draftIndex).toBe(0);
    expect(rows.find((r) => r.key === 'b')?.draftIndex).toBe(1);
    expect(rows.find((r) => r.key === 'gbt-8567')?.draftIndex).toBe(-1);
    expect(rows.find((r) => r.key === 'a')?.sourceText).toBe('自有');
  });

  it('自有条目的类别标签取自后端给的类别目录（前端不硬编码中文）', () => {
    const rows = buildStandardRows(catalog(), [draft({ category: 'guide' })], new Set());
    expect(rows.find((r) => r.key === 'own-1')?.categoryLabel).toBe('指南');
  });

  it('目录未加载时返回空数组（后端不可达不崩）', () => {
    expect(buildStandardRows(null, [draft()], new Set())).toEqual([]);
  });
});

describe('validateStandardDrafts', () => {
  it('合规草稿返回 null', () => {
    expect(validateStandardDrafts([draft(), draft({ key: 'x', url: 'https://a/b' })])).toBeNull();
  });

  it('标准号、名称必填；链接要带协议头', () => {
    expect(validateStandardDrafts([draft({ code: '  ' })])).toContain('标准号');
    expect(validateStandardDrafts([draft({ title: '' })])).toContain('名称');
    expect(validateStandardDrafts([draft({ url: 'openstd.samr.gov.cn' })])).toContain('http://');
  });

  it('空清单合规：把自有条目全删掉是正当操作', () => {
    expect(validateStandardDrafts([])).toBeNull();
  });
});

describe('自有条目草稿', () => {
  it('新增草稿自带标识（保存前行就稳定，改标准号不会让表格行错位）', () => {
    const fresh = emptyStandardDraft('national');
    expect(fresh.key).toMatch(/^[A-Za-z0-9_-]{1,40}$/);
    expect(fresh.category).toBe('national');
    expect(fresh.code).toBe('');
  });

  it('写入项去首尾空白，链接留空即空串', () => {
    const write = standardDraftToWrite(draft({ code: '  Q/AB 001-2026 ', title: ' 甲 ', url: ' ' }));
    expect(write).toMatchObject({ code: 'Q/AB 001-2026', title: '甲', url: '', category: 'guide' });
  });

  it('从读模型转草稿保留全部字段', () => {
    expect(standardDraftFrom(entry({ builtin: false, key: 'own-9' }))).toMatchObject({
      key: 'own-9', code: 'GB/T 8567-2006', category: 'national',
    });
  });
});

describe('catalogStamp', () => {
  it('从未保存过时说明目录全部来自内置清单', () => {
    expect(catalogStamp(catalog())).toContain('尚未保存过');
  });

  it('保存过则给「时刻 · 操作人」落款', () => {
    const stamp = catalogStamp(catalog({
      source: 'saved', updated_at: '2026-07-04T17:30:00+00:00', updated_by: 'U1',
    }));
    expect(stamp).toBe('已保存配置 · 2026-07-05 01:30 · U1');
  });

  it('目录未加载给 —（不显示假状态）', () => {
    expect(catalogStamp(null)).toBe('—');
  });
});

describe('nextCitationNumber（接着已有编号往下排）', () => {
  it('空白撰稿从 1 起', () => {
    expect(nextCitationNumber('')).toBe(1);
    expect(nextCitationNumber('本章列出引用的标准。')).toBe(1);
  });

  it('取已有最大序号 +1，不是行数 +1', () => {
    expect(nextCitationNumber('[1] A\n[2] B')).toBe(3);
    expect(nextCitationNumber('[3] C\n[1] A')).toBe(4);
    expect(nextCitationNumber('[9] I\n[10] J')).toBe(11);
  });

  it('只认行首的方括号数字：正文里顺带提到的不算', () => {
    expect(nextCitationNumber('见文献 [7] 的说明')).toBe(1);
    expect(nextCitationNumber('  [2] 缩进的引用行也算')).toBe(3);
  });
});

describe('formatCitationLines', () => {
  it('统一格式为「[序号] 标准号 名称」，逐条递增', () => {
    expect(formatCitationLines(
      [{ code: 'GB/T 8567-2006', title: '计算机软件文档编制规范' },
       { code: 'ISO/IEC 25010:2023', title: '产品质量模型' }],
      3,
    )).toBe('[3] GB/T 8567-2006 计算机软件文档编制规范\n[4] ISO/IEC 25010:2023 产品质量模型');
  });
});

describe('insertCitations', () => {
  it('插到光标处，前后按需补换行让引用行自成一行', () => {
    const got = insertCitations('前文一段。\n后文一段。', '[1] X', 6);
    expect(got.text).toBe('前文一段。\n[1] X\n后文一段。');
  });

  it('空白撰稿直接插入，不补多余的前置换行', () => {
    expect(insertCitations('', '[1] X', 0).text).toBe('[1] X');
  });

  it('光标为空（未聚焦过）时插到末尾', () => {
    expect(insertCitations('已有正文', '[1] X', null).text).toBe('已有正文\n[1] X');
  });

  it('光标落在插入内容之后：接着输入不会顶开刚插入的引用', () => {
    const got = insertCitations('前文', '[1] X', 2);
    expect(got.text.slice(0, got.caret)).toBe('前文\n[1] X');
  });

  it('越界光标被夹回文本范围内（不抛错、不丢字）', () => {
    expect(insertCitations('abc', '[1] X', 999).text).toBe('abc\n[1] X');
    expect(insertCitations('abc', '[1] X', -5).text).toBe('[1] X\nabc');
  });

  it('两次选取接续编号，不会出现两个 [1]', () => {
    const first = insertCitations('', formatCitationLines([{ code: 'A', title: '甲' }], 1), 0);
    const second = insertCitations(
      first.text,
      formatCitationLines([{ code: 'B', title: '乙' }], nextCitationNumber(first.text)),
      first.caret,
    );
    expect(second.text).toBe('[1] A 甲\n[2] B 乙');
  });
});

describe('draftBasisHint（零依据时把拒绝提到点击之前）', () => {
  it('两项依据都为 0 才提示', () => {
    expect(draftBasisHint({ section_key: 's', asset_count: 0, example_count: 0 }))
      .toBe('本章节没有可作依据的内容：关联需求资产 0 条、章节样例 0 条。AI 起草通常会拒绝。');
  });

  it('任一项有依据即不提示', () => {
    expect(draftBasisHint({ section_key: 's', asset_count: 1, example_count: 0 })).toBeNull();
    expect(draftBasisHint({ section_key: 's', asset_count: 0, example_count: 2 })).toBeNull();
  });

  it('计数缺失时不提示：宁可不提示，也不凭前端猜一个可能不准的数', () => {
    expect(draftBasisHint(undefined)).toBeNull();
  });
});
