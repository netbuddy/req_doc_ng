/**
 * 条目形成 VM 核心逻辑：有效要素条目化输入投影、支撑性要素停靠、本地演示投影。
 * 真实批次与字段修订走 AEP-038/AEP-036 后端（见 tests/backend pytest 覆盖）。
 * 事实源：docs/40-detailed-design/slices/SCN-002-P01-需求条目形成/页面详细设计.md、约束与验收.md。
 */
import { describe, expect, it } from 'vitest';
import type { ElementWorkspaceRead } from '../src/api/analysis';
import type { PendingRequirementItemRead } from '../src/api/item-formation';
import type { RequirementConventionCatalogRead } from '../src/api/settings';
import type { FacetReviewVM } from '../src/view-models/requirement-analysis';
import {
  FORMATION_QUICK_COMMAND_PREFILLS,
  ITEM_COMPLETENESS_BADGE_HINTS,
  ITEM_COMPLETENESS_FILTERS,
  RECHECK_DISABLED_REASON,
  buildFormationWorkspaceFromElementWorkspace,
  createPendingItemsFromElements,
  deriveBatchProgress,
  deriveRecheckTargets,
  deriveReviewGateGaps,
  deriveStructureHealthReport,
  effectiveCompletenessKey,
  itemCompletenessBadge,
  mapFormationElements,
  mapPendingItems,
  resolveBatchSubmitFollowup,
  resolveConventionPattern,
} from '../src/view-models/requirement-item-formation';

const rawText = '系统应支持异常诊断提示，并在5秒内展示。业务方还描述了异常链路出现的场景。';

const sourceWorkspace: ElementWorkspaceRead = {
  parse_context_ref: 'parse-1',
  workspace_version: '1',
  parse_status: 'parsed',
  material_canvas: {
    material_ref: 'MAT-1',
    title: '访谈纪要',
    source_note: null,
    raw_text: rawText,
    source_version: 1,
    blocks: [{ block_id: 'b0', index: 0, start_offset: 0, end_offset: rawText.length, text: rawText }],
  },
  elements: [
    {
      id: 'EL-1',
      element_type: 'functional_requirement',
      knowledge_category: 'requirement',
      content: '支持异常诊断提示',
      source_anchor: JSON.stringify({ material_ref: 'MAT-1', ranges: [{ start: 3, end: 11, exact: rawText.slice(3, 11) }] }),
      confidence: 0.91,
      process_status: 'confirmed',
      version: 1,
      superseded: false,
    },
    {
      id: 'EL-2',
      element_type: 'quality_attribute',
      knowledge_category: 'requirement',
      content: '在5秒内展示',
      source_anchor: JSON.stringify({ material_ref: 'MAT-1', ranges: [{ start: 15, end: 21, exact: rawText.slice(15, 21) }] }),
      confidence: 0.84,
      process_status: 'confirmed',
      version: 1,
      superseded: false,
    },
    {
      id: 'EL-3',
      element_type: 'scenario',
      knowledge_category: 'requirement',
      content: '异常链路出现的场景',
      source_anchor: JSON.stringify({ material_ref: 'MAT-1', ranges: [{ start: 29, end: 38, exact: rawText.slice(29, 38) }] }),
      confidence: 0.72,
      process_status: 'confirmed',
      version: 1,
      superseded: false,
    },
  ],
  available_actions: [],
  available_operations: [],
  next_action: null,
};

describe('buildFormationWorkspaceFromElementWorkspace', () => {
  it('只把需求表达类有效要素作为可条目化输入，支撑性要素停靠', () => {
    const workspace = buildFormationWorkspaceFromElementWorkspace(sourceWorkspace);

    expect(workspace.eligible_elements.map((element) => element.id)).toEqual(['EL-1', 'EL-2']);
    expect(workspace.blocked_elements).toHaveLength(1);
    expect(workspace.blocked_elements[0]).toMatchObject({
      id: 'EL-3',
      blocked_reason: '支撑或上下文类要素仅作为依据',
    });
    expect(workspace.available_operations.find((action) => action.key === 'start_itemization')?.enabled).toBe(true);
  });
});

describe('createPendingItemsFromElements（本地演示投影）', () => {
  it('逐要素创建待确认条目并逐要素归因批次结果', () => {
    const workspace = buildFormationWorkspaceFromElementWorkspace(sourceWorkspace);
    const formed = createPendingItemsFromElements(workspace, ['EL-1', 'EL-2']);

    expect(formed.pending_items).toHaveLength(2);
    expect(formed.pending_items.every((item) => item.status === 'pending_confirmation')).toBe(true);
    expect(formed.batch_results).toHaveLength(2);
    expect(formed.batch_results.every((result) => result.result_status === 'created')).toBe(true);
    expect(formed.batch_results.map((result) => result.element_ref)).toEqual(['EL-1', 'EL-2']);
    expect(mapPendingItems(formed.pending_items)[0].statusText).toBe('待确认');
    expect(formed.available_operations.find((action) => action.key === 'apply_revision')?.enabled).toBe(true);
  });
});

describe('deriveBatchProgress（区2 执行进度分数「已处理 X/Y」）', () => {
  const results = (statuses: Array<'created' | 'blocked' | 'failed' | 'skipped'>) =>
    statuses.map((result_status, index) => ({ result_status, element_ref: `EL-${index + 1}` }));

  it('确定型：分子=已处理（含未能成条的归因），进度与分数口径一致', () => {
    const progress = deriveBatchProgress(5, results(['created', 'created', 'blocked']));
    expect(progress.determinate).toBe(true);
    expect(progress).toMatchObject({ processed: 3, formed: 2, remaining: 2 });
    expect(progress.processedPct).toBe(60);
    expect(progress.countsText).toBe('已处理 3/5');
  });

  it('failed/skipped/blocked 都推进执行进度（结果好坏不进进度条），formed 供终态摘要', () => {
    const progress = deriveBatchProgress(4, results(['created', 'failed', 'skipped', 'blocked']));
    expect(progress).toMatchObject({ processed: 4, formed: 1, remaining: 0, processedPct: 100 });
  });

  it('已处理数超过发起分母（未提供范围的兜底）时按已处理收口，不产生负剩余或超 100%', () => {
    const progress = deriveBatchProgress(2, results(['created', 'blocked', 'blocked']));
    expect(progress.remaining).toBe(0);
    expect(progress.processedPct).toBe(100);
    expect(progress.countsText).toBe('已处理 3/2');
  });

  it('给定发起范围时只统计范围内归因：范围外要素（如已撤销）的归因不进账目，输入输出自洽', () => {
    // EL-1/EL-2 为发起时勾选的 2 条；EL-3 是后端对范围外要素的归因
    const progress = deriveBatchProgress(
      2,
      results(['created', 'created', 'blocked']),
      ['EL-1', 'EL-2'],
    );
    expect(progress).toMatchObject({ processed: 2, formed: 2, remaining: 0, processedPct: 100 });
    expect(progress.countsText).toBe('已处理 2/2');
  });

  it('分母缺失时降级为「已返回 N 条」不定型模式，不造假分母', () => {
    const progress = deriveBatchProgress(null, results(['created', 'blocked']));
    expect(progress.determinate).toBe(false);
    expect(progress.countsText).toBe('已返回 2 条');
    expect(progress.processedPct).toBe(0);
  });

  it('发起瞬间（无归因快照）确定型进度为 0，剩余=全部分母', () => {
    const progress = deriveBatchProgress(3, []);
    expect(progress).toMatchObject({ processed: 0, formed: 0, remaining: 3, processedPct: 0 });
    expect(progress.countsText).toBe('已处理 0/3');
  });
});

describe('itemCompletenessBadge（区5 单行 mini 达标徽标口径）', () => {
  it('只呈现锚定当前表达的真判定：不完备/完备/无档案分别映射', () => {
    expect(itemCompletenessBadge({ completenessKey: 'incomplete', structureStale: false }))
      .toEqual({ label: '不完备', tone: 'warning', hint: ITEM_COMPLETENESS_BADGE_HINTS.incomplete });
    expect(itemCompletenessBadge({ completenessKey: 'complete', structureStale: false }))
      .toEqual({ label: '完备', tone: 'success', hint: null });
    expect(itemCompletenessBadge({ completenessKey: null, structureStale: false })).toBeNull();
  });

  it('走查第三轮裁定：过期判定不呈现——「修订后未复核」不再是用户可见状态（无徽标，视同暂无体检）', () => {
    // 内容修订/拆分/归并已链式自动体检；过期只是在途瞬态或待修复残留，不按旧判定误导
    expect(itemCompletenessBadge({ completenessKey: 'complete', structureStale: true })).toBeNull();
    expect(itemCompletenessBadge({ completenessKey: 'incomplete', structureStale: true })).toBeNull();
    // 「未复核」字样彻底退出释义词表
    expect(JSON.stringify(ITEM_COMPLETENESS_BADGE_HINTS)).not.toContain('未复核');
  });

  it('徽标释义口径：说明缺什么、指向区4、申明不阻断', () => {
    expect(ITEM_COMPLETENESS_BADGE_HINTS.incomplete).toContain('必备成分');
    expect(ITEM_COMPLETENESS_BADGE_HINTS.incomplete).toContain('区4');
    expect(ITEM_COMPLETENESS_BADGE_HINTS.incomplete).toContain('仅提示不阻断');
  });
});

describe('区4 陈述体检报告（T20260711-item-completeness-ux 裁定 1）', () => {
  const facet = (over: Partial<FacetReviewVM['badges'][number]>): FacetReviewVM['badges'][number] => ({
    key: 'trigger', label: '触发条件', required: true, status: 'missing',
    statusMark: '✗', statusLabel: '缺失', statusHint: '这项内容没写，建议补上', tone: 'danger',
    evidence: null, note: null, revisionHint: null,
    ...over,
  });
  const review: FacetReviewVM = {
    rubricVersion: 1,
    correctness: null,
    completeness: { label: '不完备（可带缺陷确认）', tone: 'warning' },
    badges: [
      facet({ key: 'trigger', label: '触发条件', status: 'missing', note: '未写明触发事件', revisionHint: '请补写触发条件，例如「当用户提交订单时…」' }),
      facet({ key: 'actor', label: '执行者', status: 'present', evidence: '系统应' }),
      facet({ key: 'metric', label: '响应度量', required: false, status: 'ambiguous', statusLabel: '含糊' }),
      facet({ key: 'lifecycle', label: '生存期或量级', status: 'not_applicable', statusLabel: '不适用', tone: 'neutral', note: '值域定义无存储维度' }),
    ],
    gaps: [],
    stale: false,
  };
  // not_applicable（判据不适用）与 present 一样不计缺口
  review.gaps = review.badges.filter((b) => b.status !== 'present' && b.status !== 'not_applicable');

  const catalog: RequirementConventionCatalogRead = {
    active_convention: 'ears-cn',
    conventions: [{
      convention_key: 'ears-cn',
      display_name: '中文 EARS',
      blueprint: '',
      positioning: '',
      pattern_overview: [{ label: '功能需求', pattern: '「〔当/在 <触发条件> 时/期间，〕<执行者> 应 <可执行的系统响应>」' }],
      examples: [],
    }],
  };

  it('resolveConventionPattern：按方案 key + 类型标签从 AEP-102 目录取方案名与句式模板', () => {
    expect(resolveConventionPattern('ears-cn', '功能需求', catalog)).toEqual({
      conventionName: '中文 EARS',
      pattern: '「〔当/在 <触发条件> 时/期间，〕<执行者> 应 <可执行的系统响应>」',
    });
    // 目录未回传该类型模板 → pattern 为 null（模板块不渲染），方案名仍可用
    expect(resolveConventionPattern('ears-cn', '数据需求', catalog).pattern).toBeNull();
    expect(resolveConventionPattern(null, '功能需求', catalog)).toEqual({ conventionName: null, pattern: null });
    expect(resolveConventionPattern('ears-cn', '功能需求', null)).toEqual({ conventionName: null, pattern: null });
  });

  it('体检报告分组：必备缺口置顶、可选缺口单列、已具备折叠组含证据；文案取档案数据', () => {
    const report = deriveStructureHealthReport(review, '功能需求', '中文 EARS', '模板原文');
    expect(report.requiredGaps.map((g) => g.key)).toEqual(['trigger']);
    expect(report.requiredGaps[0].note).toBe('未写明触发事件');
    expect(report.requiredGaps[0].revisionHint).toContain('当用户提交订单时');
    expect(report.optionalGaps.map((g) => g.key)).toEqual(['metric']);
    expect(report.present.map((g) => g.key)).toEqual(['actor']);
    expect(report.present[0].evidence).toBe('系统应');
    expect(report.pattern).toBe('模板原文');
    // N/A 成分单列中性区（不进缺口），判定理由可见
    expect(report.notApplicable.map((g) => g.key)).toEqual(['lifecycle']);
    expect(report.notApplicable[0].note).toBe('值域定义无存储维度');
    expect(report.requiredGaps.map((g) => g.key)).not.toContain('lifecycle');
  });

  it('人话头包含方案名/类型/『不完备』语义与不拦流程申明；方案名缺失回落「当前规约方案」', () => {
    const report = deriveStructureHealthReport(review, '功能需求', '中文 EARS', null);
    expect(report.intro).toContain('中文 EARS');
    expect(report.intro).toContain('功能需求');
    expect(report.intro).toContain('必备成分缺失即『不完备』');
    expect(report.intro).toContain('不拦任何流程');
    expect(deriveStructureHealthReport(review, '功能需求', null, null).intro).toContain('当前规约方案');
  });

  it('「让 AI 起草补写」预填 /修订 补写〔成分名〕：（预填不直发）', () => {
    expect(FORMATION_QUICK_COMMAND_PREFILLS.reviseGap('触发条件')).toBe('/修订 补写触发条件：');
  });
});

describe('进入评审知情软门（裁定 3：有缺口弹确认、零缺口直进；事实门禁不变）', () => {
  type Facet = { required: boolean; status: string; label: string };
  const item = (
    over: Partial<{
      itemRef: string; reqNo: string; expression: string; status: string;
      completeness: string | null; stale: boolean; facets: Facet[];
    }> = {},
  ): PendingRequirementItemRead => ({
    item_ref: over.itemRef ?? 'I-1',
    req_no: over.reqNo ?? 'REQ-001',
    expression: over.expression ?? '系统应记录任务状态',
    req_type: 'data',
    status: over.status ?? 'pending_confirmation',
    priority: null,
    source_element_refs: [],
    revision_records: [],
    available_actions: [],
    structure_review:
      over.completeness === undefined && over.facets === undefined && over.stale === undefined
        ? {
            profile_version: 1,
            convention_key: 'ears-cn',
            statement_conformance: 'conforms',
            completeness: 'complete',
            facets: [],
            stale: false,
          }
        : {
            profile_version: 1,
            convention_key: 'ears-cn',
            statement_conformance: 'conforms',
            completeness: over.completeness ?? 'complete',
            facets: (over.facets ?? []).map((f) => ({
              facet_key: f.label, label: f.label, required: f.required,
              status: f.status, evidence: null, note: null, revision_hint: null,
            })),
            stale: over.stale ?? false,
          },
  } as unknown as PendingRequirementItemRead);

  const incompleteFacets: Facet[] = [
    { required: true, status: 'present', label: '数据对象' },
    { required: true, status: 'missing', label: '关键属性' },
    { required: true, status: 'not_applicable', label: '生存期或量级' },
  ];

  it('零缺口（全部完备或无档案）→ null，不弹层直进', () => {
    expect(deriveReviewGateGaps([item(), item({ completeness: null })])).toBeNull();
    expect(deriveReviewGateGaps([])).toBeNull();
  });

  it('逐条清单：只计不完备；过期判定视同暂无体检不入缺口；缺口成分名列出（N/A 不算缺口）', () => {
    const gate = deriveReviewGateGaps([
      item({ itemRef: 'I-1', reqNo: 'REQ-007', expression: '记录任务状态', completeness: 'incomplete', facets: incompleteFacets }),
      item({ completeness: 'incomplete', stale: true }), // 过期旧判定：不入缺口
      item({ completeness: null, stale: true }),
      item(),
    ]);
    expect(gate).toMatchObject({ incomplete: 1 });
    expect(gate?.title).toBe('带着 1 条缺口进入评审？');
    expect(gate?.countsText).toBe('1 条不完备');
    expect(gate?.items).toHaveLength(1);
    expect(gate?.items[0]).toMatchObject({ itemRef: 'I-1', reqNo: 'REQ-007', expression: '记录任务状态' });
    // 缺口成分＝必备 missing/ambiguous；not_applicable、present 均不列
    expect(gate?.items[0].gapLabels).toEqual(['关键属性']);
  });

  it('只统计待确认条目', () => {
    const gate = deriveReviewGateGaps([
      item({ completeness: 'incomplete', facets: incompleteFacets }),
      item({ status: 'terminated', completeness: 'incomplete', facets: incompleteFacets }),
    ]);
    expect(gate).toMatchObject({ incomplete: 1 });
    expect(gate?.countsText).toBe('1 条不完备');
  });
});

describe('AEP-114 手动复核目标集（区2 修复通道弹层计数；走查第三轮裁定后单一口径）', () => {
  const item = (
    ref: string,
    over: Partial<{ completenessKey: string | null; structureStale: boolean; statusText: string }>,
  ) => ({
    itemRef: ref,
    completenessKey: 'complete' as string | null,
    structureStale: false,
    statusText: '待确认',
    ...over,
  });

  it('目标集=待确认∩暂无当前体检（过期∪缺失合并单口径）；现行判定与已终止排除', () => {
    const targets = deriveRecheckTargets([
      item('a', { structureStale: true }),                             // 过期（自动体检失败残留）
      item('b', { completenessKey: null }),                            // 从未判定/未得出完备性
      item('c', {}),                                                   // 现行判定（complete）→ 排除
      item('d', { completenessKey: 'incomplete' }),                    // 现行判定（incomplete）→ 排除
      item('e', { structureStale: true, statusText: '已终止' }),       // 已终止 → 排除
    ]);
    expect(targets).toMatchObject({ total: 2 });
    expect(targets?.targetRefs).toEqual(['a', 'b']);
    expect(targets?.countsText).toBe('2 条暂无当前体检');
  });

  it('目标集为空 → null（按钮禁用并 title 说明）', () => {
    expect(deriveRecheckTargets([item('a', {}), item('b', { completenessKey: 'incomplete' })])).toBeNull();
    expect(deriveRecheckTargets([])).toBeNull();
    expect(RECHECK_DISABLED_REASON).toContain('没有需要复核的条目');
  });

  it('区5 /复核 预填（无自由参数，直发通道）；达标度筛选只剩 全部/不完备', () => {
    expect(FORMATION_QUICK_COMMAND_PREFILLS.recheck()).toBe('/复核');
    expect(ITEM_COMPLETENESS_FILTERS.map((f) => f.key)).toEqual(['all', 'incomplete']);
  });
});

describe('P7 条目侧业务知识消费（意图背景 + 引用依据）', () => {
  it('引用依据命令预填 /引用依据 命令词（后续写业务知识名称）', () => {
    expect(FORMATION_QUICK_COMMAND_PREFILLS.referenceBasis()).toBe('/引用依据 ');
  });

  it('intent_context 投影为只读意图元素（携类型标签）', () => {
    const intent = mapFormationElements([
      {
        id: 'g1', element_type: 'goal', knowledge_category: 'requirement',
        content: '提升订单处理效率', source_anchor: null, confidence: 0.9,
        process_status: 'confirmed', version: 1, superseded: false,
      },
    ]);
    expect(intent).toHaveLength(1);
    expect(intent[0].typeLabel).toBe('目标');
    expect(intent[0].content).toBe('提升订单处理效率');
  });
});

describe('resolveBatchSubmitFollowup（HK-1 单飞守卫：in_flight 复挂原批次轮询）', () => {
  it('in_flight 携原批次 refs → reattach（沿用原 run 复挂 watchBatchRun，不报错）', () => {
    const followup = resolveBatchSubmitFollowup({
      status: 'in_flight',
      formation_context_ref: 'CTX-ORIGINAL',
      agent_run_ref: 'RUN-ORIGINAL',
      next_action: '条目化批次执行中：已复用在途批次并恢复进度跟踪，请等待完成',
    });
    expect(followup).toEqual({
      kind: 'reattach',
      runId: 'RUN-ORIGINAL',
      contextRef: 'CTX-ORIGINAL',
      notice: '条目化批次执行中：已复用在途批次并恢复进度跟踪，请等待完成',
    });
  });

  it('in_flight 无 next_action 时给默认恢复提示', () => {
    const followup = resolveBatchSubmitFollowup({
      status: 'in_flight', formation_context_ref: 'CTX-1', agent_run_ref: 'RUN-1',
    });
    expect(followup.kind).toBe('reattach');
    expect((followup as { notice: string }).notice).toContain('已恢复进度跟踪');
  });

  it('submitted＋run → watch（新批次挂轮询）', () => {
    expect(
      resolveBatchSubmitFollowup({
        status: 'submitted', formation_context_ref: 'CTX-NEW', agent_run_ref: 'RUN-NEW',
      }),
    ).toEqual({ kind: 'watch', runId: 'RUN-NEW', contextRef: 'CTX-NEW' });
  });

  it('submitted 无 run（同步收束）→ refresh', () => {
    expect(
      resolveBatchSubmitFollowup({ status: 'submitted', formation_context_ref: 'CTX-SYNC' }),
    ).toEqual({ kind: 'refresh', contextRef: 'CTX-SYNC' });
  });

  it('rejected_precheck → rejected（next_action 原样透传）', () => {
    expect(
      resolveBatchSubmitFollowup({ status: 'rejected_precheck', next_action: '版本不一致，请刷新后重试' }),
    ).toEqual({ kind: 'rejected', notice: '版本不一致，请刷新后重试' });
    expect(resolveBatchSubmitFollowup({ status: 'rejected_precheck' })).toEqual({
      kind: 'rejected', notice: '批次未受理',
    });
  });
});

describe('effectiveCompletenessKey（issue #8 清理债：stale 抑制单点收口）', () => {
  it('过期投影视同暂无体检 → null；现行判定原样透出', () => {
    expect(effectiveCompletenessKey({ completenessKey: 'incomplete', structureStale: true })).toBeNull();
    expect(effectiveCompletenessKey({ completenessKey: 'complete', structureStale: false })).toBe('complete');
    expect(effectiveCompletenessKey({ completenessKey: null, structureStale: false })).toBeNull();
  });
});
