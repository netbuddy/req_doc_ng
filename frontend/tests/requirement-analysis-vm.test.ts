/**
 * 知识抽取 VM 核心逻辑：锚点解析（offset/fallback/失效）、画布分段（重叠）、列表投影。
 * 事实源：docs/40 slices/SCN-001-P02-需求要素识别/页面详细设计.md §4.2/§4.3/§4.5。
 */
import { describe, expect, it } from 'vitest';
import type {
  ElementFacetReviewRead,
  ElementWorkspaceRead,
  MaterialTextBlockRead,
  RequirementElementRead,
} from '../src/api/analysis';
import {
  acceptsSelectionAffordance,
  buildCanvasBlocks,
  buildReidentifyGuard,
  buildZone5Timeline,
  deriveRecognitionPhase,
  resolveCardPositions,
  buildSelectionRanges,
  elementStatusMarkKey,
  elementTypeOptionsForWing,
  fillSegmentText,
  buildRevisionPrefill,
  groupElementListByWing,
  mapElementList,
  MODEL_VERDICT_META,
  splitTriageCandidates,
  verdictReasonText,
  withoutTriageCandidates,
  mapFacetReview,
  mergeHydratedMessages,
  matchesCompletenessFilter,
  QUICK_COMMAND_PREFILLS,
  resolveAnchor,
  SELECTION_PROMPT_GUIDANCE,
  buildSelectionAffordance,
  withSelectionAffordance,
  type ElementHighlight,
  type ElementListItemVM,
} from '../src/view-models/requirement-analysis';

const RAW = '系统应支持一键导出所需数据。导出任务需在30秒内完成。';

function anchor(materialRef: string, ranges: object[]): string {
  return JSON.stringify({ material_ref: materialRef, ranges });
}

describe('resolveAnchor（§4.2 锚点解析规则）', () => {
  it('offset 合法且 exact 匹配 → ok', () => {
    const result = resolveAnchor(
      anchor('M-1', [{ start: 0, end: 13, exact: RAW.slice(0, 13), prefix: '', suffix: '' }]),
      'M-1',
      RAW,
    );
    expect(result.status).toBe('ok');
    expect(result.ranges[0]).toMatchObject({ start: 0, end: 13, relocated: false });
  });

  it('offset 失效但 exact 唯一命中 → relocated（quote fallback）', () => {
    const result = resolveAnchor(
      anchor('M-1', [{ start: 99, end: 120, exact: '导出任务需在30秒内完成', prefix: '', suffix: '' }]),
      'M-1',
      RAW,
    );
    expect(result.status).toBe('relocated');
    expect(RAW.slice(result.ranges[0].start, result.ranges[0].end)).toBe('导出任务需在30秒内完成');
  });

  it('material_ref 不一致 → invalid（不渲染但不隐藏要素）', () => {
    const result = resolveAnchor(anchor('M-OTHER', [{ start: 0, end: 5, exact: RAW.slice(0, 5) }]), 'M-1', RAW);
    expect(result.status).toBe('invalid');
  });

  it('exact 无命中且 offset 越界 → invalid', () => {
    const result = resolveAnchor(anchor('M-1', [{ start: -1, end: -1, exact: '不存在的引文' }]), 'M-1', RAW);
    expect(result.status).toBe('invalid');
  });

  it('多次命中且 prefix/suffix 可消歧 → relocated 唯一定位', () => {
    const text = '导出。导出。';
    const result = resolveAnchor(
      anchor('M-1', [{ start: 90, end: 92, exact: '导出', prefix: '。', suffix: '。' }]),
      'M-1',
      text,
    );
    expect(result.status).toBe('relocated');
    expect(result.ranges[0].start).toBe(3);
  });

  it('非 JSON 旧锚点 → invalid（锚点异常路径）', () => {
    expect(resolveAnchor('L1', 'M-1', RAW).status).toBe('invalid');
    expect(resolveAnchor(null, 'M-1', RAW).status).toBe('none');
  });
});

describe('buildCanvasBlocks（§4.3 分段 / §4.5 重叠）', () => {
  const blocks: MaterialTextBlockRead[] = [
    { block_id: 'b0', index: 0, start_offset: 0, end_offset: RAW.length, text: RAW },
  ];

  it('单要素高亮切分出携带 refs 的 segment', () => {
    const highlights: ElementHighlight[] = [
      { elementId: 'E1', typeColorKey: 'func', processStatus: 'confirmed', ranges: [{ start: 0, end: 13, relocated: false }] },
    ];
    const segments = fillSegmentText(blocks, buildCanvasBlocks(blocks, highlights))[0].segments;
    expect(segments[0]).toMatchObject({ start: 0, end: 13, refs: ['E1'] });
    expect(segments[0].text).toBe(RAW.slice(0, 13));
    expect(segments[1].refs).toEqual([]);
  });

  it('重叠范围产生 refs.length > 1 的 segment（重叠选择浮层入口）', () => {
    const highlights: ElementHighlight[] = [
      { elementId: 'E1', typeColorKey: 'func', processStatus: 'confirmed', ranges: [{ start: 0, end: 13, relocated: false }] },
      { elementId: 'E2', typeColorKey: 'goal', processStatus: 'pending_confirmation', ranges: [{ start: 5, end: 20, relocated: false }] },
    ];
    const segments = buildCanvasBlocks(blocks, highlights)[0].segments;
    const overlapped = segments.find((s) => s.refs.length === 2);
    expect(overlapped).toBeTruthy();
    expect(overlapped).toMatchObject({ start: 5, end: 13 });
  });
});

describe('mapElementList（区1 投影）', () => {
  it('失效锚点显示“来源定位待修正”，不隐藏要素', () => {
    const elements = [
      {
        id: 'E1',
        element_type: 'functional_requirement',
        content: '系统应支持一键导出所需数据',
        source_anchor: anchor('M-1', [{ start: -1, end: -1, exact: '不存在' }]),
        confidence: 0.9,
        process_status: 'confirmed',
        version: 1,
        superseded: false,
      },
    ] as RequirementElementRead[];
    const anchors = new Map([['E1', resolveAnchor(elements[0].source_anchor, 'M-1', RAW)]]);
    const items = mapElementList(elements, anchors);
    expect(items).toHaveLength(1);
    expect(items[0].anchorHint).toBe('来源定位待修正');
    expect(items[0].typeLabel).toBe('功能需求');
    expect(items[0].confidenceText).toBe('90%');
    // 失效锚点无成功解析段 → 行内 ⚓×N 不显示
    expect(items[0].anchorCount).toBe(0);
  });

  it('anchorCount = 解析成功的锚点段数（区1 行内 ⚓×N）', () => {
    const elements = [
      {
        id: 'E1',
        element_type: 'term',
        content: '导出任务',
        source_anchor: anchor('M-1', [
          { start: 0, end: 13, exact: RAW.slice(0, 13), prefix: '', suffix: '' },
          { start: 14, end: 25, exact: RAW.slice(14, 25), prefix: '', suffix: '' },
        ]),
        confidence: null,
        process_status: 'confirmed',
        version: 1,
        superseded: false,
      },
    ] as RequirementElementRead[];
    const anchors = new Map([['E1', resolveAnchor(elements[0].source_anchor, 'M-1', RAW)]]);
    expect(mapElementList(elements, anchors)[0].anchorCount).toBe(2);
  });

  // 「已有知识项只读」这条边界靠一行布尔位在 11 处逐个置灰，此前零测试覆盖——
  // 归并族的三条缺陷（选中即命令自相矛盾、横幅计数、刷新丢选中）都因此没有任何自动化关卡（裁定 N10）
  describe('既有知识项只读标记（第三个形参 mergedExistingIds）', () => {
    const twoElements = [
      {
        id: 'E1',
        element_type: 'term',
        content: '履约单',
        source_anchor: null,
        confidence: 0.8,
        process_status: 'pending_confirmation',
        version: 1,
        superseded: false,
      },
      {
        id: 'E2',
        element_type: 'functional_requirement',
        content: '系统应支持一键导出所需数据',
        source_anchor: null,
        confidence: 0.9,
        process_status: 'pending_confirmation',
        version: 1,
        superseded: false,
      },
    ] as RequirementElementRead[];
    const noAnchors = new Map<string, ReturnType<typeof resolveAnchor>>();

    it('传了 id 集合：命中的项标为既有（只读），其余不受影响', () => {
      const items = mapElementList(twoElements, noAnchors, new Set(['E1']));
      expect(items.map((i) => [i.id, i.mergedExisting])).toEqual([
        ['E1', true],
        ['E2', false],
      ]);
    });

    it('不传第三个形参：一律不是既有项（无归并的工作区行为不变）', () => {
      expect(mapElementList(twoElements, noAnchors).every((i) => !i.mergedExisting)).toBe(true);
    });

    it('空集合与不传等价（后端给了空的既有项清单时不误标）', () => {
      expect(mapElementList(twoElements, noAnchors, new Set()).every((i) => !i.mergedExisting)).toBe(
        true,
      );
    });
  });
});

describe('区1 v4 高保真：类型子筛选选项与两翼分组投影', () => {
  it('elementTypeOptionsForWing：来源=ELEMENT_TYPE_META（单一来源，禁手写清单）', () => {
    expect(elementTypeOptionsForWing('requirement').map((t) => t.code)).toEqual([
      'functional_requirement', 'quality_attribute', 'constraint', 'data_requirement',
      'interface_requirement', 'goal', 'scenario',
    ]);
    expect(elementTypeOptionsForWing('business').map((t) => t.label)).toEqual([
      '术语', '前提假设', '业务规则', '角色', '外部系统',
    ]);
  });

  const itemOfType = (id: string, typeCode: string) =>
    ({ id, typeCode } as unknown as ElementListItemVM);

  it('groupElementListByWing：翼序需求在前、组内保持原序', () => {
    const groups = groupElementListByWing([
      itemOfType('E1', 'term'),
      itemOfType('E2', 'functional_requirement'),
      itemOfType('E3', 'business_rule'),
      itemOfType('E4', 'goal'),
    ]);
    expect(groups.map((g) => g.wing)).toEqual(['requirement', 'business']);
    expect(groups[0].items.map((i) => i.id)).toEqual(['E2', 'E4']);
    expect(groups[1].items.map((i) => i.id)).toEqual(['E1', 'E3']);
  });

  it('groupElementListByWing：空翼组整组隐藏（筛选后某翼 0 条）', () => {
    const groups = groupElementListByWing([itemOfType('E1', 'term')]);
    expect(groups).toHaveLength(1);
    expect(groups[0].wing).toBe('business');
    expect(groupElementListByWing([])).toEqual([]);
  });
});

describe('mapFacetReview（TC-06 完备度投影 → 徽章 VM）', () => {
  const review = {
    rubric_version: 1,
    correctness: 'consistent_with_source',
    completeness: 'incomplete',
    facets: [
      {
        facet_key: 'stimulus',
        label: '刺激',
        required: true,
        status: 'present',
        evidence: '在月末结算期间',
        note: null,
        revision_hint: null,
      },
      {
        facet_key: 'response_measure',
        label: '响应度量',
        required: true,
        status: 'missing',
        evidence: null,
        note: '未见量化阈值',
        revision_hint: '请给出可验证的量化指标',
      },
    ],
  } as ElementFacetReviewRead;

  it('有判据类型 → 徽章 + 缺口列表 + 完备/正确性文案', () => {
    const vm = mapFacetReview(review);
    expect(vm).not.toBeNull();
    expect(vm?.rubricVersion).toBe(1);
    expect(vm?.correctness?.label).toBe('与原文一致');
    expect(vm?.completeness?.tone).toBe('warning'); // incomplete 仅警示，不阻断
    expect(vm?.badges).toHaveLength(2);
    expect(vm?.badges[0]).toMatchObject({ key: 'stimulus', statusMark: '✓', tone: 'success' });
    expect(vm?.gaps).toHaveLength(1);
    expect(vm?.gaps[0]).toMatchObject({
      key: 'response_measure',
      statusLabel: '缺失',
      revisionHint: '请给出可验证的量化指标',
    });
  });

  it('无判据类型（facet_review 空/无 facets）→ null，不渲染徽章区', () => {
    expect(mapFacetReview(null)).toBeNull();
    expect(mapFacetReview(undefined)).toBeNull();
    expect(mapFacetReview({ rubric_version: 1, facets: [] } as unknown as ElementFacetReviewRead)).toBeNull();
  });

  it('未知 facet 状态与未知正确性码降级为中性，不抛错', () => {
    const vm = mapFacetReview({
      rubric_version: 2,
      correctness: 'something_new',
      completeness: null,
      facets: [
        {
          facet_key: 'x',
          label: 'X',
          required: false,
          status: 'unexpected',
          evidence: null,
          note: null,
          revision_hint: null,
        },
      ],
    } as unknown as ElementFacetReviewRead);
    expect(vm?.correctness).toBeNull();
    expect(vm?.completeness).toBeNull();
    expect(vm?.badges[0].tone).toBe('neutral');
    expect(vm?.gaps).toHaveLength(1); // 非 present/not_applicable 进入缺口提示
  });

  it('判据驱动 N/A（not_applicable）：中性态徽章，不计入缺口', () => {
    const vm = mapFacetReview({
      rubric_version: 1,
      correctness: null,
      completeness: 'complete',
      facets: [
        { facet_key: 'data_object', label: '数据对象', required: true, status: 'present', evidence: '任务状态枚举', note: null, revision_hint: null },
        { facet_key: 'lifecycle_or_volume', label: '生存期或量级', required: true, status: 'not_applicable', evidence: null, note: '值域定义无存储维度', revision_hint: null },
      ],
    } as unknown as ElementFacetReviewRead);
    const na = vm?.badges.find((b) => b.key === 'lifecycle_or_volume');
    expect(na).toMatchObject({ status: 'not_applicable', statusLabel: '不适用', tone: 'neutral', statusMark: '—' });
    // N/A 不进缺口（与 present 一样排除）
    expect(vm?.gaps).toHaveLength(0);
  });
});

describe('TC-08 完备度筛选与修订预填', () => {
  const itemOf = (completenessKey: string | null, facetStale: boolean) => ({ completenessKey, facetStale });

  it('matchesCompletenessFilter：all/incomplete/stale 语义', () => {
    expect(matchesCompletenessFilter(itemOf(null, false), 'all')).toBe(true);
    expect(matchesCompletenessFilter(itemOf('incomplete', false), 'incomplete')).toBe(true);
    expect(matchesCompletenessFilter(itemOf('complete', false), 'incomplete')).toBe(false);
    expect(matchesCompletenessFilter(itemOf(null, false), 'incomplete')).toBe(false);
    // 过期项归「修订后未复核」（与条目侧同口径更名，改词不改机制），不再按旧判定计入不完备
    expect(matchesCompletenessFilter(itemOf('incomplete', true), 'incomplete')).toBe(false);
    expect(matchesCompletenessFilter(itemOf('incomplete', true), 'stale')).toBe(true);
    expect(matchesCompletenessFilter(itemOf('complete', false), 'stale')).toBe(false);
  });

  it('mapFacetReview 透传 stale', () => {
    const vm = mapFacetReview({
      rubric_version: 1,
      correctness: null,
      completeness: 'incomplete',
      stale: true,
      facets: [
        {
          facet_key: 'response_measure',
          label: '响应度量',
          required: true,
          status: 'missing',
          evidence: null,
          note: null,
          revision_hint: '请给出量化指标',
        },
      ],
    } as unknown as ElementFacetReviewRead);
    expect(vm?.stale).toBe(true);
  });

  it('buildRevisionPrefill：原表达 + 逐缺失面向 note/hint 模板（预填≠生效）', () => {
    const text = buildRevisionPrefill('查询要快', [
      {
        key: 'response_measure',
        label: '响应度量',
        required: true,
        status: 'missing',
        statusMark: '✗',
        statusLabel: '缺失',
        statusHint: '这项内容没写，建议补上',
        tone: 'danger',
        evidence: null,
        note: '未见量化阈值',
        revisionHint: '请给出可验证的量化指标',
      },
    ]);
    expect(text.startsWith('查询要快\n')).toBe(true);
    expect(text).toContain('【响应度量·待补充】未见量化阈值；请给出可验证的量化指标');
  });
});

describe('QUICK_COMMAND_PREFILLS（AEP-096：药丸只预填 /命令词 文本，不暗挂结构化参数）', () => {
  it('每个构造器产出以 /命令词 开头的可续写文本', () => {
    expect(QUICK_COMMAND_PREFILLS.adjustType('功能需求')).toBe('/改类型 功能需求');
    expect(QUICK_COMMAND_PREFILLS.reviseExpression()).toBe('/改表达 修订为：');
    expect(QUICK_COMMAND_PREFILLS.adjustAnchor().startsWith('/改范围 ')).toBe(true);
    expect(QUICK_COMMAND_PREFILLS.split().startsWith('/拆分 ')).toBe(true);
    expect(QUICK_COMMAND_PREFILLS.supplement().startsWith('/补入 ')).toBe(true);
  });

  it('合并组稿：复选对话框选中的要素以「名称」写进命令正文（refs 由后端按名称解析）', () => {
    expect(QUICK_COMMAND_PREFILLS.merge(['导出任务需在30秒内完成', '系统要发通知'])).toBe(
      '/合并 与「导出任务需在30秒内完成」「系统要发通知」合并，合并后表达由 AI 起草。',
    );
  });

  it('勘误两形态：有选区→选区文本入「原文」空位；无选区→空脚手架＋占位提示（不出现空「」脏文本）', () => {
    // 有区3 选区
    expect(QUICK_COMMAND_PREFILLS.erratum('30秒')).toBe('/勘误 把「30秒」改正为「」');
    // 选区首尾空白被裁剪（防「前 」之类脏文本）
    expect(QUICK_COMMAND_PREFILLS.erratum('  前  ')).toBe('/勘误 把「前」改正为「」');
    // 无选区：占位提示脚手架，绝不产出空「」
    const empty = QUICK_COMMAND_PREFILLS.erratum(null);
    expect(empty).toBe('/勘误 把「原文里写错的片段」改正为「更正后的文本」');
    expect(empty).not.toContain('把「」');
    expect(QUICK_COMMAND_PREFILLS.erratum('   ')).toBe('/勘误 把「原文里写错的片段」改正为「更正后的文本」');
    // 新增遗漏：无选区不带脏尾字，选区裁剪空白
    expect(QUICK_COMMAND_PREFILLS.addMissing(null)).toBe('/新增遗漏 ');
    expect(QUICK_COMMAND_PREFILLS.addMissing('  漏识别的规则  ')).toBe('/新增遗漏 漏识别的规则');
  });

  it('改范围两形态（⑥ affordance）：有选区→明示将用当前选区并附摘要；无选区→引导先选区', () => {
    const withSel = QUICK_COMMAND_PREFILLS.adjustAnchor({ start: 12, end: 18, text: '30 秒内' });
    expect(withSel).toBe('/改范围 把来源改到区3 当前选区（12–18）：「30 秒内」');
    // 长选区截断预览
    expect(QUICK_COMMAND_PREFILLS.adjustAnchor({ start: 0, end: 40, text: 'a'.repeat(40) })).toContain('…」');
    // 无选区：引导文案，不含区间括号
    const noSel = QUICK_COMMAND_PREFILLS.adjustAnchor(null);
    expect(noSel.startsWith('/改范围 ')).toBe(true);
    expect(noSel).toContain('先在区3 拖选');
  });
});

describe('withSelectionAffordance（区3「当前选区」→ 区5 输入框正文改写）', () => {
  const SEL = { start: 12, end: 18, text: '30 秒内' };
  const OTHER = { start: 40, end: 52, text: '次日凌晨完成' };
  const AFF = '来源改到区3 当前选区（12–18）：「30 秒内」';

  it('选区描述与「改范围」预填共用同一构造，故预填出来的那段能被认出并就地更新', () => {
    expect(buildSelectionAffordance(SEL)).toBe(AFF);
    expect(QUICK_COMMAND_PREFILLS.adjustAnchor(SEL)).toBe(`/改范围 把${AFF}`);
  });

  it('形态①空正文：只写选区描述本身，补「把」成句', () => {
    expect(withSelectionAffordance('', SEL)).toBe(`把${AFF}`);
    expect(withSelectionAffordance('   \n ', SEL)).toBe(`把${AFF}`);
  });

  it('形态②含无选区版引导语：引导语被整段替换，不残留', () => {
    const prefilled = QUICK_COMMAND_PREFILLS.adjustAnchor(null);
    const next = withSelectionAffordance(prefilled, SEL);
    expect(next).toBe(`/改范围 把${AFF}`);
    expect(next).not.toContain(SELECTION_PROMPT_GUIDANCE);
    expect(next).not.toContain('先在区3 拖选');
  });

  it('形态③已含一段选区描述：就地更新那一段，正文里始终只有一段选区文字', () => {
    const withOld = `/改范围 把${AFF}`;
    const next = withSelectionAffordance(withOld, OTHER);
    expect(next).toBe('/改范围 把来源改到区3 当前选区（40–52）：「次日凌晨完成」');
    expect(next.match(/当前选区/g)).toHaveLength(1);
    // 末尾追加形态（不带「把」）的那一段同样被就地更新，且不给它硬加上「把」
    const appended = withSelectionAffordance('这条位置标错了', SEL);
    const updated = withSelectionAffordance(appended, OTHER);
    expect(updated).toBe('这条位置标错了，来源改到区3 当前选区（40–52）：「次日凌晨完成」');
    expect(updated.match(/当前选区/g)).toHaveLength(1);
  });

  it('形态③幂等：同一选区连点两次，正文不变（不产生第二段选区文字）', () => {
    const once = withSelectionAffordance('/改范围 ', SEL);
    const twice = withSelectionAffordance(once, SEL);
    expect(twice).toBe(once);
    expect(withSelectionAffordance(twice, SEL)).toBe(once);
    const freeOnce = withSelectionAffordance('这条位置标错了', SEL);
    expect(withSelectionAffordance(freeOnce, SEL)).toBe(freeOnce);
  });

  it('形态④用户自由编辑的正文：以逗号自然衔接追加到末尾', () => {
    expect(withSelectionAffordance('这条位置标错了', SEL)).toBe(`这条位置标错了，${AFF}`);
    // 原文已以标点收尾就不再补逗号（不出现「。，」）
    expect(withSelectionAffordance('这条位置标错了。', SEL)).toBe(`这条位置标错了。${AFF}`);
    expect(withSelectionAffordance('这条位置标错了，', SEL)).toBe(`这条位置标错了，${AFF}`);
    // 尾部空白不带进拼接结果
    expect(withSelectionAffordance('这条位置标错了  \n', SEL)).toBe(`这条位置标错了，${AFF}`);
  });

  it('摘要里的「」被剥掉：它是描述自身的定界符，留着会让下一次点击认不出边界而重复堆叠', () => {
    const quoted = { start: 3, end: 9, text: '写作「原路退回」' };
    const first = withSelectionAffordance('这条位置标错了', quoted);
    expect(first).toBe('这条位置标错了，来源改到区3 当前选区（3–9）：「写作原路退回」');
    // 摘要含定界符时仍然幂等
    expect(withSelectionAffordance(first, quoted)).toBe(first);
  });

  it('长选区摘要截断到 20 字并以省略号收尾（与改范围预填同口径）', () => {
    const long = { start: 0, end: 40, text: '甲'.repeat(40) };
    expect(buildSelectionAffordance(long)).toBe(`来源改到区3 当前选区（0–40）：「${'甲'.repeat(20)}…」`);
    expect(withSelectionAffordance('', long)).toContain('…」');
  });

  it('形态④正文以冒号收尾：换行另起一段，不接在冒号后面成为它的取值', () => {
    // 冒号后面的文字读作冒号前那个名目的取值（命令的参数槽正是这么断句），
    // 直接接上会让这段给人读的选区说明变成参数正文
    expect(withSelectionAffordance('作用范围如下：', SEL)).toBe(`作用范围如下：\n${AFF}`);
    expect(withSelectionAffordance('scope:', SEL)).toBe(`scope:\n${AFF}`);
    // 冒号后仍然幂等：换行接上的那一段照样被就地更新
    const once = withSelectionAffordance('作用范围如下：', SEL);
    expect(withSelectionAffordance(once, SEL)).toBe(once);
  });

  it('形态④正文以 ASCII 句点或省略号收尾：直接接上，不出现两个标点连排', () => {
    // 「拆分」预填 `/拆分 1. \n2. ` 去掉尾部空白后以 ASCII 句点收尾
    const splitPrefill = QUICK_COMMAND_PREFILLS.split();
    expect(withSelectionAffordance(splitPrefill, SEL)).toBe(`/拆分 1. \n2.${AFF}`);
    expect(withSelectionAffordance(splitPrefill, SEL)).not.toContain('.，');
    expect(withSelectionAffordance('先说到这里…', SEL)).toBe(`先说到这里…${AFF}`);
  });

  it('形态②替换引导语走函数式替换：选区原文里的 $& 之类不被当成替换模式展开', () => {
    // $&／$`／$'／$$ 在替换字符串里恒有特殊含义，与 search 是字符串还是正则无关
    const dollar = { start: 3, end: 9, text: '总价 $& 元' };
    const prefilled = QUICK_COMMAND_PREFILLS.adjustAnchor(null);
    const next = withSelectionAffordance(prefilled, dollar);
    expect(next).toBe('/改范围 把来源改到区3 当前选区（3–9）：「总价 $& 元」');
    // 展开的痕迹：被替换掉的引导语反被塞回引号内的摘要里
    expect(next).not.toContain('先在区3 拖选');
    // 同一选区再点一次仍不变（展开会破坏幂等）
    expect(withSelectionAffordance(next, dollar)).toBe(next);

    for (const text of ['总价 $$ 处理', "总价 $' 元", '总价 $` 元']) {
      const sel = { start: 3, end: 9, text };
      const out = withSelectionAffordance(QUICK_COMMAND_PREFILLS.adjustAnchor(null), sel);
      expect(out).toBe(`/改范围 把来源改到区3 当前选区（3–9）：「${text}」`);
      expect(withSelectionAffordance(out, sel)).toBe(out);
    }
  });
});

describe('acceptsSelectionAffordance（「当前选区」按钮的正文形态门）', () => {
  const SEL = { start: 12, end: 18, text: '30 秒内' };

  it('自由正文（含空正文）放行：后端不按参数语法切，整段交 AI 解读', () => {
    expect(acceptsSelectionAffordance('')).toBe(true);
    expect(acceptsSelectionAffordance('   ')).toBe(true);
    expect(acceptsSelectionAffordance('这条位置标错了')).toBe(true);
    expect(acceptsSelectionAffordance('  这条位置标错了')).toBe(true);
    // 斜杠不在开头就不是命令
    expect(acceptsSelectionAffordance('见 a/b 两处')).toBe(true);
  });

  it('「改范围」放行：选区说明的语义正是它的参数（三种预填形态都放行）', () => {
    expect(acceptsSelectionAffordance('/改范围')).toBe(true);
    expect(acceptsSelectionAffordance('/改范围 ')).toBe(true);
    expect(acceptsSelectionAffordance(QUICK_COMMAND_PREFILLS.adjustAnchor(null))).toBe(true);
    expect(acceptsSelectionAffordance(QUICK_COMMAND_PREFILLS.adjustAnchor(SEL))).toBe(true);
    // 全角斜杠与半角同等对待
    expect(acceptsSelectionAffordance('／改范围 ')).toBe(true);
  });

  it('其它命令词一律拦下：说明会被读进那条命令的参数正文', () => {
    const blocked = [
      QUICK_COMMAND_PREFILLS.reviseExpression(),
      QUICK_COMMAND_PREFILLS.adjustType('外部系统'),
      QUICK_COMMAND_PREFILLS.split(),
      QUICK_COMMAND_PREFILLS.merge(['系统要发通知']),
      QUICK_COMMAND_PREFILLS.addMissing('漏识别的规则'),
      QUICK_COMMAND_PREFILLS.erratum('原路退回'),
      QUICK_COMMAND_PREFILLS.supplement(),
    ];
    for (const text of blocked) {
      expect(acceptsSelectionAffordance(text)).toBe(false);
    }
    expect(acceptsSelectionAffordance('／改表达 修订为：')).toBe(false);
    // 命令词整词比对：前缀相同但不是同一个词的不放行
    expect(acceptsSelectionAffordance('/改范围围 ')).toBe(false);
  });
});

describe('buildSelectionRanges（① 拖选就地创建：选区→来源锚点载荷）', () => {
  it('有选区：文本入 exact、范围入 start/end，供后端「新增遗漏」建 source_anchor', () => {
    const ranges = buildSelectionRanges({ start: 12, end: 18, text: '30 秒内' });
    expect(ranges).toEqual([{ start: 12, end: 18, exact: '30 秒内', prefix: '', suffix: '' }]);
  });

  it('无选区：空数组（命令不挂结构化锚点）', () => {
    expect(buildSelectionRanges(null)).toEqual([]);
  });

  it('与「新增遗漏」预填同源：同一选区文本既入表达预填、又入锚点载荷（区1 计数自洽）', () => {
    const selection = { start: 3, end: 9, text: '漏识别的规则' };
    expect(QUICK_COMMAND_PREFILLS.addMissing(selection.text)).toBe('/新增遗漏 漏识别的规则');
    expect(buildSelectionRanges(selection)[0].exact).toBe('漏识别的规则');
  });
});

describe('buildReidentifyGuard（② 识别重跑拦截：破坏性前置确认 + 真实计数）', () => {
  it('工作区为空：不拦截（首次识别安全）', () => {
    expect(buildReidentifyGuard(0, 0)).toEqual({ needsConfirm: false, message: '' });
  });

  it('已有知识项、无已确认：拦截，文案给总数不虚报已确认', () => {
    const guard = buildReidentifyGuard(7, 0);
    expect(guard.needsConfirm).toBe(true);
    expect(guard.message).toContain('已有 7 条');
    expect(guard.message).not.toContain('已确认');
    expect(guard.message).toContain('不再显示在工作区');
  });

  it('已有已确认：拦截，明示已确认条数（计数与用户可见口径自洽）', () => {
    const guard = buildReidentifyGuard(7, 3);
    expect(guard.needsConfirm).toBe(true);
    expect(guard.message).toContain('已有 7 条');
    expect(guard.message).toContain('已确认 3 条');
  });
});

describe('两翼映射（P2：knowledge_category 单点映射）', () => {
  it('ELEMENT_TYPE_META 每类带 category，且与后端归属一致', async () => {
    const { ELEMENT_TYPE_META } = await import('../src/view-models/requirement-analysis');
    const req = ['functional_requirement', 'quality_attribute', 'constraint', 'data_requirement',
      'interface_requirement', 'goal', 'scenario'];
    const biz = ['term', 'assumption', 'role', 'external_system'];
    for (const t of req) expect(ELEMENT_TYPE_META[t].category).toBe('requirement');
    for (const t of biz) expect(ELEMENT_TYPE_META[t].category).toBe('business');
  });

  it('区3 两级编码：全部 12 类带装饰性色阶 shade（v4 .mk-ico 映射），未知类型兜底 a', async () => {
    const { ELEMENT_TYPE_META, elementTypeMeta } = await import('../src/view-models/requirement-analysis');
    // v4 原型逐条映射：r-a=功能/数据/场景 r-b=约束/接口 r-c=质量/目标；b-a=术语/业务规则 b-b=角色/外部 b-c=假设
    expect(
      Object.fromEntries(Object.entries(ELEMENT_TYPE_META).map(([k, m]) => [k, m.shade])),
    ).toEqual({
      functional_requirement: 'a', quality_attribute: 'c', constraint: 'b', data_requirement: 'a',
      interface_requirement: 'b', goal: 'c', scenario: 'a',
      term: 'a', assumption: 'c', business_rule: 'a', role: 'b', external_system: 'b',
    });
    expect(elementTypeMeta('unknown_type').shade).toBe('a');
  });

  it('KNOWLEDGE_CATEGORY_META 两翼短名，顺序需求翼在前', async () => {
    const { KNOWLEDGE_CATEGORY_META, KNOWLEDGE_CATEGORY_ORDER, elementTypeMeta } = await import(
      '../src/view-models/requirement-analysis'
    );
    expect(KNOWLEDGE_CATEGORY_ORDER).toEqual(['requirement', 'business']);
    expect(KNOWLEDGE_CATEGORY_META.requirement.shortLabel).toBe('需求知识');
    expect(KNOWLEDGE_CATEGORY_META.business.shortLabel).toBe('业务知识');
    // 未知类型兜底归业务翼（不抛错）
    expect(elementTypeMeta('unknown_type').category).toBe('business');
  });
});

describe('elementStatusMarkKey（区3 状态前导标记优先级）', () => {
  it('已确认 → confirmed', () => {
    expect(elementStatusMarkKey('confirmed', null)).toBe('confirmed');
  });

  it('已撤销 → revoked', () => {
    expect(elementStatusMarkKey('revoked', null)).toBe('revoked');
  });

  it('待确认且有修订稿 → has_draft', () => {
    expect(elementStatusMarkKey('pending_confirmation', '改后的表达')).toBe('has_draft');
  });

  it('纯待确认（无修订稿）→ null（不打标记）', () => {
    expect(elementStatusMarkKey('pending_confirmation', null)).toBeNull();
    expect(elementStatusMarkKey('pending_confirmation', '   ')).toBeNull();
  });

  it('终态优先于修订稿：confirmed/revoked 即使带草稿也取终态', () => {
    expect(elementStatusMarkKey('confirmed', '残留草稿')).toBe('confirmed');
    expect(elementStatusMarkKey('revoked', '残留草稿')).toBe('revoked');
  });
});

// ---- 区5 时间线合流（第④组）与留痕真合并（冷审查裁定 F8）----

describe('deriveRecognitionPhase（裁定 C1：失败停靠不能被当成识别中）', () => {
  const ws = (patch: Partial<ElementWorkspaceRead>) =>
    ({
      parse_context_ref: 'PCTX-1',
      workspace_version: '1',
      elements: [],
      merged_existing_elements: [],
      available_actions: [],
      available_operations: [],
      ...patch,
    }) as ElementWorkspaceRead;

  it('识别已出结果 → 就绪', () => {
    expect(deriveRecognitionPhase(ws({ parse_status: 'parsed' }))).toBe('ready');
  });

  it('没识别出可处理知识项也算出了结果 → 就绪（不锁页面）', () => {
    expect(deriveRecognitionPhase(ws({ parse_status: 'unprocessable' }))).toBe('ready');
  });

  it('识别失败停靠（后端给了 retry 出口）→ 失败态，而不是识别中', () => {
    expect(
      deriveRecognitionPhase(
        ws({ next_action: '模型服务不可用，可重试', available_actions: [{ key: 'retry', enabled: true }] }),
      ),
    ).toBe('failed');
  });

  it('retry 出口被后端标为不可用时不当失败态处理（不给点不动的按钮）', () => {
    expect(deriveRecognitionPhase(ws({ available_actions: [{ key: 'retry', enabled: false }] }))).toBe(
      'recognizing',
    );
  });

  it('执行器崩溃支：识别结果与停靠原因双空、动作清单为空 → 如实报识别中，不谎报失败', () => {
    expect(deriveRecognitionPhase(ws({ next_action: '识别进行中' }))).toBe('recognizing');
  });
});

describe('buildZone5Timeline 与 resolveCardPositions', () => {
  const msg = (id: number, at: string) => ({ id, kind: 'user', text: `m${id}`, at });
  const place = (
    cards: { key: string; at: string | null; fingerprint?: string | null }[],
    latestMessageAt: string | null,
    memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>(),
  ) => resolveCardPositions(cards, latestMessageAt, memory);

  it('刚由命令产生的卡片按事实时刻落位，排在该命令之后', () => {
    const messages = [msg(1, '2026-07-19T10:00:00Z'), msg(2, '2026-07-19T10:10:00Z')];
    const cards = place([{ key: 'card-draft', at: '2026-07-19T10:10:05Z' }], '2026-07-19T10:10:00Z');
    expect(buildZone5Timeline(messages, cards).map((i) => i.key)).toEqual(['msg-1', 'msg-2', 'card-draft']);
  });

  it('落位之后再发消息，新消息排在卡片之下（缺陷复现口径）', () => {
    const memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>();
    const first = [msg(1, '2026-07-19T10:00:00Z')];
    place([{ key: 'card-draft', at: '2026-07-19T10:00:05Z' }], '2026-07-19T10:00:00Z', memory);
    const later = [...first, msg(2, '2026-07-19T10:30:00Z')];
    const cards = place([{ key: 'card-draft', at: '2026-07-19T10:00:05Z' }], '2026-07-19T10:30:00Z', memory);
    expect(buildZone5Timeline(later, cards).map((i) => i.key)).toEqual(['msg-1', 'card-draft', 'msg-2']);
  });

  it('只是切到一条早就有修订稿的知识项：卡片落在最新消息之后，不浮到历史顶部', () => {
    const messages = [msg(1, '2026-07-19T10:00:00Z'), msg(2, '2026-07-19T10:30:00Z')];
    // 事实时刻比全部消息都早（一小时前写下的修订稿）
    const cards = place([{ key: 'card-element', at: '2026-07-19T09:00:00Z' }], '2026-07-19T10:30:00Z');
    expect(buildZone5Timeline(messages, cards).map((i) => i.key)).toEqual(['msg-1', 'msg-2', 'card-element']);
    expect(cards[0].at).toBe('2026-07-19T09:00:00Z'); // 显示的仍是事实时刻，不伪造
  });

  it('卡片内容更新（事实时刻变化）即重新落位到最新处', () => {
    const memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>();
    const messages = [msg(1, '2026-07-19T10:00:00Z'), msg(2, '2026-07-19T10:30:00Z')];
    place([{ key: 'card-element', at: '2026-07-19T09:00:00Z' }], '2026-07-19T10:00:00Z', memory);
    const cards = place([{ key: 'card-element', at: '2026-07-19T10:31:00Z' }], '2026-07-19T10:30:00Z', memory);
    expect(buildZone5Timeline(messages, cards).map((i) => i.key)).toEqual(['msg-1', 'msg-2', 'card-element']);
  });

  it('卡片消失后再出现＝重新落位（不留旧位）', () => {
    const memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>();
    place([{ key: 'card-draft', at: '2026-07-19T10:00:05Z' }], '2026-07-19T10:00:00Z', memory);
    place([], '2026-07-19T10:30:00Z', memory);
    expect(memory.size).toBe(0);
  });

  it('同刻时消息在前、卡片在后（卡片是命令的后果）', () => {
    const cards = place([{ key: 'card-element', at: '2026-07-19T10:00:00Z' }], null);
    expect(buildZone5Timeline([msg(1, '2026-07-19T10:00:00Z')], cards).map((i) => i.key)).toEqual([
      'msg-1', 'card-element',
    ]);
  });

  it('后端没给时刻的卡片排在最末（视为最新，不伪造时刻）', () => {
    const cards = place([{ key: 'card-draft', at: null }], '2026-07-19T10:00:00Z');
    const items = buildZone5Timeline([msg(1, '2026-07-19T10:00:00Z')], cards);
    expect(items.map((i) => i.key)).toEqual(['msg-1', 'card-draft']);
    expect(items[1].at).toBeNull();
  });

  it('无时刻的卡片排在时刻更晚的卡片之后（「末尾」是真末尾，裁定 N3/N2）', () => {
    const cards = place(
      [
        { key: 'card-draft', at: null },
        { key: 'card-element', at: '2026-07-19T11:00:00Z' },
      ],
      '2026-07-19T10:00:00Z',
    );
    expect(buildZone5Timeline([msg(1, '2026-07-19T10:00:00Z')], cards).map((i) => i.key)).toEqual([
      'msg-1', 'card-element', 'card-draft',
    ]);
  });

  it('一条消息都还没有时不记死落位：留痕历史水合进来后卡片不浮到历史之上（裁定 C3）', () => {
    const memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>();
    // 首帧：留痕历史的请求还没回来，消息为空——此刻落位只是暂定，不进记忆
    place([{ key: 'card-element:E-1', at: '2026-07-19T09:00:00Z' }], null, memory);
    expect(memory.size).toBe(0);
    // 历史水合进来（都比卡片的事实时刻晚）：卡片重新落位到最新消息之后
    const messages = [msg(1, '2026-07-19T10:00:00Z'), msg(2, '2026-07-19T10:30:00Z')];
    const cards = place(
      [{ key: 'card-element:E-1', at: '2026-07-19T09:00:00Z' }],
      '2026-07-19T10:30:00Z',
      memory,
    );
    expect(buildZone5Timeline(messages, cards).map((i) => i.key)).toEqual([
      'msg-1', 'msg-2', 'card-element:E-1',
    ]);
  });

  it('不同知识项的复核卡各自落位，不共用同一份记忆（裁定 C4）', () => {
    const memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>();
    // 一次批量 AI 复核在单个请求里写库，两条知识项的最后写入时刻逐字相同
    const sameAt = '2026-07-19T10:00:05Z';
    place([{ key: 'card-element:E-1', at: sameAt }], '2026-07-19T10:00:00Z', memory);
    const messages = [msg(1, '2026-07-19T10:00:00Z'), msg(2, '2026-07-19T10:30:00Z')];
    // 切到第 2 条：键不同 → 不命中 E-1 的旧落位，按「切到旧事实」规则落在最新消息之后
    const cards = place([{ key: 'card-element:E-2', at: sameAt }], '2026-07-19T10:30:00Z', memory);
    expect(buildZone5Timeline(messages, cards).map((i) => i.key)).toEqual([
      'msg-1', 'msg-2', 'card-element:E-2',
    ]);
  });

  it('复核内容没变时整行写入时刻刷新不重新落位、也不换显示时刻（点「确认」不跳「刚刚」，裁定 C7）', () => {
    const memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>();
    const fingerprint = '合格|依据一|';
    place(
      [{ key: 'card-element:E-1', at: '2026-07-19T10:00:05Z', fingerprint }],
      '2026-07-19T10:00:00Z',
      memory,
    );
    const messages = [msg(1, '2026-07-19T10:00:00Z'), msg(2, '2026-07-19T10:30:00Z')];
    // 用户点「✓ 确认」：整行被 UPDATE、写入时刻刷新，但复核三字段一字未动
    const cards = place(
      [{ key: 'card-element:E-1', at: '2026-07-19T11:00:00Z', fingerprint }],
      '2026-07-19T10:30:00Z',
      memory,
    );
    expect(buildZone5Timeline(messages, cards).map((i) => i.key)).toEqual([
      'msg-1', 'card-element:E-1', 'msg-2',
    ]);
    expect(cards[0].at).toBe('2026-07-19T10:00:05Z'); // 时间标签仍是复核那一刻
  });

  it('复核结论真的变了（AI 又复核一轮）才重新落位（裁定 C7 反面）', () => {
    const memory = new Map<string, ReturnType<typeof resolveCardPositions>[number]>();
    place(
      [{ key: 'card-element:E-1', at: '2026-07-19T10:00:05Z', fingerprint: '合格|依据一|' }],
      '2026-07-19T10:00:00Z',
      memory,
    );
    const messages = [msg(1, '2026-07-19T10:00:00Z'), msg(2, '2026-07-19T10:30:00Z')];
    const cards = place(
      [{ key: 'card-element:E-1', at: '2026-07-19T11:00:00Z', fingerprint: '存疑|依据二|新表达' }],
      '2026-07-19T10:30:00Z',
      memory,
    );
    expect(buildZone5Timeline(messages, cards).map((i) => i.key)).toEqual([
      'msg-1', 'msg-2', 'card-element:E-1',
    ]);
    expect(cards[0].at).toBe('2026-07-19T11:00:00Z');
  });
});

describe('mergeHydratedMessages', () => {
  const local = (id: number, text: string, at: string) =>
    ({ id, kind: 'user', text, at }) as { id: number; kind: string; text: string; at: string; sourceId?: string };
  const row = (id: number, text: string, at: string, sourceId: string) =>
    ({ id, kind: 'user', text, at, sourceId });

  it('用户抢发一条时不再丢弃整段历史（F8 缺陷口径）', () => {
    const merged = mergeHydratedMessages(
      [local(9, '刚发的', '2026-07-19T10:30:00Z')],
      [row(1, '历史一', '2026-07-19T09:00:00Z', 'R1'), row(2, '历史二', '2026-07-19T09:05:00Z', 'R2')],
    );
    expect(merged.map((m) => m.text)).toEqual(['历史一', '历史二', '刚发的']);
  });

  it('已水合过的行不重复追加（按留痕行 id 去重）', () => {
    const first = mergeHydratedMessages([], [row(1, '历史一', '2026-07-19T09:00:00Z', 'R1')]);
    const second = mergeHydratedMessages(first, [row(2, '历史一', '2026-07-19T09:00:00Z', 'R1')]);
    expect(second).toHaveLength(1);
  });

  it('刚发出、后端已记录的同一条不会变成两条', () => {
    const merged = mergeHydratedMessages(
      [local(9, '补一条：退货单', '2026-07-19T10:30:00Z')],
      [row(1, '补一条：退货单', '2026-07-19T10:30:02Z', 'R9')],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].sourceId).toBeUndefined(); // 保留本地那条（含链路详情等本地信息）
  });

  it('隔了很久的同文本消息按两条处理（不是同一条）', () => {
    const merged = mergeHydratedMessages(
      [local(9, '再说一次', '2026-07-19T12:00:00Z')],
      [row(1, '再说一次', '2026-07-19T09:00:00Z', 'R1')],
    );
    expect(merged).toHaveLength(2);
  });

  it('空水合结果不动现有消息', () => {
    const current = [local(1, 'a', '2026-07-19T10:00:00Z')];
    expect(mergeHydratedMessages(current, [])).toBe(current);
  });
});

// ---- 建议剔除候选区（T20260724-suspected-noise-triage）----

function elem(over: Partial<RequirementElementRead> & { id: string }): RequirementElementRead {
  return {
    element_type: 'functional_requirement',
    content: '系统应支持一键导出所需数据',
    source_anchor: null,
    confidence: 0.9,
    process_status: 'pending_confirmation',
    version: 1,
    superseded: false,
    ...over,
  } as RequirementElementRead;
}

describe('建议剔除候选区（splitTriageCandidates）', () => {
  const items = (elements: RequirementElementRead[], merged?: Set<string>) =>
    mapElementList(elements, new Map(), merged);

  it('模型判为建议剔除且未撤回的项进候选区，其余留正常列表', () => {
    const { normal, candidates } = splitTriageCandidates(
      items([
        elem({ id: 'E1', model_verdict: 'processable' }),
        elem({ id: 'E2', model_verdict: 'suspected_noise' }),
      ]),
    );
    expect(normal.map((i) => i.id)).toEqual(['E1']);
    expect(candidates.map((i) => i.id)).toEqual(['E2']);
  });

  it('撤回后回正常列表，不需要为「撤回过」单开支路', () => {
    const { normal, candidates } = splitTriageCandidates(
      items([elem({ id: 'E2', model_verdict: 'suspected_noise', noise_triage: 'restored' })]),
    );
    expect(normal.map((i) => i.id)).toEqual(['E2']);
    expect(candidates).toHaveLength(0);
  });

  it('疑似需补充留在正常列表——它是「有价值但不完整」，不是建议剔除', () => {
    const { normal, candidates } = splitTriageCandidates(
      items([elem({ id: 'E3', model_verdict: 'suspected_needs_supplement' })]),
    );
    expect(normal.map((i) => i.id)).toEqual(['E3']);
    expect(candidates).toHaveLength(0);
  });

  it('已替代与「已有」的建议剔除项不进候选区（撤回对它们没有意义）', () => {
    const superseded = elem({ id: 'E4', model_verdict: 'suspected_noise', superseded: true });
    const existing = elem({ id: 'E5', model_verdict: 'suspected_noise' });
    const { candidates } = splitTriageCandidates(items([superseded, existing], new Set(['E5'])));
    expect(candidates).toHaveLength(0);
  });

  it('撤销过的条目出候选区——候选区装的是待人工处置的队列，处置完就该离箱（裁定 C7）', () => {
    const { normal, candidates } = splitTriageCandidates(
      items([
        elem({ id: 'E6', model_verdict: 'suspected_noise', process_status: 'revoked' }),
        elem({ id: 'E7', model_verdict: 'suspected_noise' }),
      ]),
    );
    expect(candidates.map((i) => i.id)).toEqual(['E7']);
    expect(normal.map((i) => i.id)).toEqual(['E6']);
  });

  it('已确认的候选仍在箱里——确认不是候选区的出口，撤回与撤销才是', () => {
    const { candidates } = splitTriageCandidates(
      items([elem({ id: 'E8', model_verdict: 'suspected_noise', process_status: 'confirmed' })]),
    );
    expect(candidates.map((i) => i.id)).toEqual(['E8']);
  });

  it('候选行带模型裁定码与理由，供分组与理由摘要使用', () => {
    const [item] = items([
      elem({ id: 'E2', model_verdict: 'suspected_noise', verdict_reason: '会议开场客套话' }),
    ]);
    expect(item.triageCandidate).toBe(true);
    expect(item.verdictCode).toBe('suspected_noise');
    expect(item.verdictReason).toBe('会议开场客套话');
  });
});

describe('批量入口的目标过滤（withoutTriageCandidates，裁定 C1/P1）', () => {
  const byId = (elements: RequirementElementRead[]) =>
    new Map(elements.map((e) => [e.id, e]));

  it('勾选集合里混进的候选条目被滤掉——它是「撤回→勾选→再移回候选区」留下的', () => {
    const elements = [
      elem({ id: 'E1', model_verdict: 'processable' }),
      elem({ id: 'E2', model_verdict: 'suspected_noise' }),
    ];
    expect(withoutTriageCandidates(['E1', 'E2'], byId(elements))).toEqual(['E1']);
  });

  it('撤回到正常列表的那条照旧可以批量确认——守卫认的是「人工尚未撤回」', () => {
    const elements = [elem({ id: 'E2', model_verdict: 'suspected_noise', noise_triage: 'restored' })];
    expect(withoutTriageCandidates(['E2'], byId(elements))).toEqual(['E2']);
  });

  it('已替代的与工作区里已不存在的 id 一并滤掉（都不可裁决，留着只会让计数虚高）', () => {
    const elements = [elem({ id: 'E3', superseded: true })];
    expect(withoutTriageCandidates(['E3', 'E-gone'], byId(elements))).toEqual([]);
  });

  it('「已有」的建议剔除项不按候选滤——它压根没进候选区，不参与批量另有一道门（区1 不给它复选框）', () => {
    const elements = [elem({ id: 'E4', model_verdict: 'suspected_noise' })];
    expect(withoutTriageCandidates(['E4'], byId(elements), new Set(['E4']))).toEqual(['E4']);
  });
});

describe('verdictReasonText（区4 理由回落）', () => {
  it('模型给了理由就用模型的', () => {
    expect(verdictReasonText('suspected_noise', '这句是会议客套话')).toBe('这句是会议客套话');
  });

  it('模型漏给时回落通用判据，并说明是模型没给——不留空让人分不清是谁没给', () => {
    const text = verdictReasonText('suspected_noise', null);
    expect(text).toContain('模型没有给出');
    expect(text).toContain('不承载需求信息');
  });

  it('空白字符串按漏给处理', () => {
    expect(verdictReasonText('suspected_noise', '   ')).toContain('模型没有给出');
  });

  it('「建议剔除」是全站唯一口径，不出现「噪声」字样', () => {
    expect(MODEL_VERDICT_META.suspected_noise.label).toBe('建议剔除');
    expect(JSON.stringify(MODEL_VERDICT_META)).not.toContain('噪声');
  });
});

describe('撤回后的行标签（本卡要消掉的自相矛盾）', () => {
  it('回到正常列表的那条显示「已撤回」，不再挂「建议剔除」', () => {
    const [item] = mapElementList(
      [elem({ id: 'E2', model_verdict: 'suspected_noise', noise_triage: 'restored' })],
      new Map(),
    );
    expect(item.verdictLabel).toBe('已撤回');
    expect(item.verdictTone).toBe('neutral');
  });

  it('未撤回的候选仍标「建议剔除」', () => {
    const [item] = mapElementList([elem({ id: 'E3', model_verdict: 'suspected_noise' })], new Map());
    expect(item.verdictLabel).toBe('建议剔除');
  });
});

describe('正常列表零「剔除」字样（裁定 C5：徽标判据与候选判据同源）', () => {
  const labelOf = (element: RequirementElementRead, merged?: Set<string>) =>
    mapElementList([element], new Map(), merged)[0].verdictLabel;

  it('已替代的旧版本留在正常列表，不挂「建议剔除」——它没有撤回按钮，标了也无计可施', () => {
    expect(labelOf(elem({ id: 'E4', model_verdict: 'suspected_noise', superseded: true }))).toBeNull();
  });

  it('「已有」的知识项只读，不挂「建议剔除」', () => {
    expect(labelOf(elem({ id: 'E5', model_verdict: 'suspected_noise' }), new Set(['E5']))).toBeNull();
  });

  it('已撤销的条目已离箱回到正常列表，不再挂「建议剔除」', () => {
    expect(
      labelOf(elem({ id: 'E6', model_verdict: 'suspected_noise', process_status: 'revoked' })),
    ).toBeNull();
  });

  it('「疑似需补充」不受此限：它本就留在正常列表并带自己的徽标', () => {
    expect(labelOf(elem({ id: 'E7', model_verdict: 'suspected_needs_supplement' }))).toBe('疑似需补充');
  });
});
