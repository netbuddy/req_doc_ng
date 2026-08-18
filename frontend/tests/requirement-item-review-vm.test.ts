/**
 * 条目评审 VM 投影（v5）：初始工作区投影、显示态分组、会话条/线程/动态流装配、修订点合成。
 * 事实源：docs/40-detailed-design/slices/SCN-003-P01-需求条目评审/页面详细设计.md（v5）§3。
 * 领域规则（显示态派生/结论守卫/裁决副作用链）在后端（backend/tests/test_item_review.py），
 * 本文件只测 UI 投影不复制领域规则。
 */
import { describe, expect, it } from 'vitest';
import type {
  DiagnosisRunProgressRead,
  ItemReviewWorkspaceRead,
  ReviewRequirementItemRead,
  SourceCandidateRead,
  VerdictRead,
} from '../src/api/item-review';
import type { FormationElementRead } from '../src/api/item-formation';
import type { CanvasBlockVM } from '../src/view-models/requirement-analysis';
import { itemFormationWorkspaceFixture } from '../src/fixtures/item-formation';
import { createPendingItemsFromElements } from '../src/view-models/requirement-item-formation';
import {
  attestationRecordText,
  buildInitialReviewWorkspace,
  buildSourceCandidateCards,
  buildSourceRegistrationValue,
  buildThread,
  findSelectionHits,
  buildThreadStrip,
  collectRunFailureToasts,
  composeSelectedPoints,
  deriveDiagnosisRunProgress,
  DIAGNOSIS_MODE_OPTIONS,
  diagnosisLaunchCommand,
  diagnosisScopeHint,
  groupReviewItems,
  isSourceAttestation,
  isSupplementPending,
  mapReviewItems,
  nextAwaitingItem,
  QUICK_COMMAND_PREFILLS,
  receiptText,
  REVIEW_DISPLAY_FALLBACK_GROUP,
  reviewDisplayMeta,
  reviewItemStatusNote,
  revisionRecordText,
  reviewRunHint,
} from '../src/view-models/requirement-item-review';

function initialReviewWorkspace(): ItemReviewWorkspaceRead {
  const formed = createPendingItemsFromElements(
    itemFormationWorkspaceFixture,
    itemFormationWorkspaceFixture.eligible_elements.map((element) => element.id),
  );
  return buildInitialReviewWorkspace(formed);
}

function verdictOf(partial: Partial<VerdictRead>): VerdictRead {
  return {
    round_ref: 'R-1', round_no: 1, diagnosis_mode: 'standard', trigger: 'user_submit',
    status: 'completed', verdict_kind: 'revise', verdict_summary: '建议修订。',
    findings: [{ finding_ref: 'F-1', finding_type: 'untestable', diagnosis_summary: '缺口径', basis_summary: '' }],
    revision_points: [{ point_ref: 'P1', label: '补口径', finding_index: 0, find: 'A', replace: 'A+', basis: '', group: null }],
    supplement_gaps: [], context_coverage: '', model_result_refs: [],
    invalidated: false, invalidated_reason: null, superseded_by: null,
    adjudication: null, overridden: false, confirm_result: null,
    effective: true, reason: null, created_at: '2026-07-06T00:00:00Z',
    ...partial,
  };
}

describe('buildInitialReviewWorkspace', () => {
  it('初始投影全部条目无结论、不伪造结论、对话为空', () => {
    const workspace = initialReviewWorkspace();

    expect(workspace.review_items.length).toBeGreaterThan(0);
    for (const item of workspace.review_items) {
      expect(item.review_status).toBe('no_verdict');
      expect(item.current_verdict).toBeNull();
      expect(item.verdict_history).toHaveLength(0);
      expect(item.dialogue_messages).toHaveLength(0);
    }
    expect(workspace.review_context_ref).toBe(workspace.formation_context_ref);
    expect(workspace.confirmed_count).toBe(0);
  });
});

describe('mapReviewItems / groupReviewItems', () => {
  it('可诊断性来自后端 available_actions；分组按派生显示态', () => {
    const workspace = initialReviewWorkspace();
    const items = workspace.review_items.map((item, index): ReviewRequirementItemRead => ({
      ...item,
      review_status: index === 0 ? 'no_verdict' : 'awaiting_adjudication',
      display_code: index === 0 ? 'pending_diagnosis' : 'awaiting_adjudication',
      current_verdict: index === 0 ? null : verdictOf({}),
      available_actions: index === 0
        ? [{ key: 'request_diagnosis', enabled: true, disabled_reason: null }]
        : [{ key: 'adjudicate_verdict', enabled: true, disabled_reason: null }],
    }));
    const vms = mapReviewItems(items, [items[0].item_ref], items[0].item_ref);

    expect(vms[0].selectableForDiagnosis).toBe(true);
    expect(vms[0].selectedForDiagnosis).toBe(true);
    for (const vm of vms.slice(1)) {
      expect(vm.selectableForDiagnosis).toBe(false);
      expect(vm.checkboxDisabledReason).toBeTruthy();
      expect(vm.verdictGlyph).toBe('修');
    }

    const groups = groupReviewItems(vms);
    expect(groups.map((g) => g.key)).toEqual(['awaiting_adjudication', 'pending_diagnosis']);
  });
});

describe('buildThreadStrip / nextAwaitingItem', () => {
  it('会话条按注意力排序（待裁决优先），徽标=状态字缩写；下一待裁决循环跳转', () => {
    const workspace = initialReviewWorkspace();
    const [a, b] = workspace.review_items;
    const items: ReviewRequirementItemRead[] = [
      { ...a, review_status: 'confirmed', display_code: 'confirmed' },
      { ...b, review_status: 'awaiting_adjudication', display_code: 'awaiting_adjudication', current_verdict: verdictOf({ verdict_kind: 'pass' }) },
    ];
    const strip = buildThreadStrip(items, items[1].item_ref);
    expect(strip[0].itemRef).toBe(items[1].item_ref);
    expect(strip[0].glyph).toBe('通');
    expect(strip[1].done).toBe(true);
    expect(nextAwaitingItem(items, items[0].item_ref)).toBe(items[1].item_ref);
  });
});

describe('buildThread / receiptText', () => {
  it('站立结论=结论卡；已裁决/已替代/失效轮次=收折回执；对话按时间入流', () => {
    const workspace = initialReviewWorkspace();
    const base = workspace.review_items[0];
    const item: ReviewRequirementItemRead = {
      ...base,
      review_status: 'awaiting_adjudication',
      current_verdict: verdictOf({ round_ref: 'R-2', round_no: 2, created_at: '2026-07-06T02:00:00Z' }),
      verdict_history: [
        verdictOf({
          round_ref: 'R-1', effective: false, created_at: '2026-07-06T01:00:00Z',
          adjudication: {
            decision: 'rejected', selected_point_refs: [], excluded_point_refs: [],
            reason: '判定依据不足', operator_ref: 'U1', at: '2026-07-06T01:10:00Z',
          },
        }),
      ],
      dialogue_messages: [{
        message_ref: 'M-1', kind: 'explanation', user_message: '为什么？', text: '依据是…',
        in_flight: false, created_at: '2026-07-06T03:00:00Z',
      }],
    };
    const thread = buildThread(item);
    expect(thread.map((e) => e.kind)).toEqual(['receipt', 'verdict', 'dialogue']);
    const receipt = receiptText(item.verdict_history[0]);
    expect(receipt.mark).toBe('✗');
    expect(receipt.text).toContain('判定依据不足');
  });
});

describe('composeSelectedPoints', () => {
  it('子集合成与后端同规则', () => {
    const base = '系统应支持按订单号检索历史订单';
    const points = [
      { point_ref: 'P1', label: 'a', finding_index: 0, find: '系统应支持', replace: '系统应支持在库内', basis: '', group: 'g' },
      { point_ref: 'P2', label: 'b', finding_index: 0, find: '检索历史订单', replace: '精确检索订单', basis: '', group: 'g' },
      { point_ref: 'P3', label: 'c', finding_index: 0, find: '按订单号', replace: '按订单编号', basis: '', group: null },
    ];
    expect(composeSelectedPoints(base, points, new Set(['P1', 'P3'])))
      .toBe('系统应支持在库内按订单编号检索历史订单');
  });
});

describe('QUICK_COMMAND_PREFILLS（AEP-095 斜杠命令：药丸只预填文本，前端不解析）', () => {
  it('诊断组稿：无勾选=纯模式词；有勾选=写明范围（scope 由后端解释）', () => {
    expect(QUICK_COMMAND_PREFILLS.diagnose('标准')).toBe('/诊断 标准');
    expect(QUICK_COMMAND_PREFILLS.diagnose('全面', 3)).toBe('/诊断 对已勾选的 3 个条目发起全面诊断');
  });

  it('裁决与人工路线命令均以 /命令词 开头且可续写', () => {
    expect(QUICK_COMMAND_PREFILLS.rejectVerdict(2)).toBe('/拒绝结论 第2轮 ');
    expect(QUICK_COMMAND_PREFILLS.adoptDraft()).toBe('/采纳草案');
    expect(QUICK_COMMAND_PREFILLS.manualRevision()).toBe('/修订 把当前条目的表达修订为：');
    expect(QUICK_COMMAND_PREFILLS.overrideConfirm()).toBe('/覆盖确认 理由：');
    expect(QUICK_COMMAND_PREFILLS.withdraw()).toBe('/撤回 理由：');
  });

  it('补充来源出口：findSources 直发无尾空格；specifySource 留尾空格供补说明后重跑', () => {
    expect(QUICK_COMMAND_PREFILLS.findSources()).toBe('/找来源');
    expect(QUICK_COMMAND_PREFILLS.specifySource()).toBe('/找来源 ');
  });
});

describe('补充来源出口（issue #30 出口三部曲之三；ADR-0002 P1/P3）', () => {
  const candidate = (over: Partial<SourceCandidateRead>): SourceCandidateRead => ({
    element_ref: 'el-1',
    element_type: 'functional_requirement',
    content: '订单金额超过 500 元时需人工审核',
    source_quote: '订单金额超过 500 元时，系统应要求人工审核',
    reason: '与本条「大额订单审核」表达同指',
    rank: 1,
    ...over,
  });

  it('buildSourceCandidateCards 按 rank 升序并附类型徽标呈现元数据（1 最相关）', () => {
    const cards = buildSourceCandidateCards([
      candidate({ element_ref: 'el-b', rank: 2 }),
      candidate({ element_ref: 'el-a', rank: 1 }),
    ]);
    expect(cards.map((c) => c.elementRef)).toEqual(['el-a', 'el-b']);
    expect(cards[0].typeLabel).toBe('功能需求');
    expect(cards[0].typeColorKey).toBe('func');
    expect(cards[0].sourceQuote).toContain('人工审核');
  });

  it('buildSourceCandidateCards 容缺原文引文：source_quote 缺省投影为 null', () => {
    const [card] = buildSourceCandidateCards([candidate({ source_quote: null })]);
    expect(card.sourceQuote).toBeNull();
  });

  it('buildSourceRegistrationValue 整集替换语义：并入候选、去重、序列化 JSON 数组字符串', () => {
    const value = buildSourceRegistrationValue(['el-1', 'el-2'], 'el-3');
    expect(JSON.parse(value)).toEqual(['el-1', 'el-2', 'el-3']);
    // 候选已在集内=幂等并集（后端再去重/升序，前端提交并集即可）
    expect(JSON.parse(buildSourceRegistrationValue(['el-1'], 'el-1'))).toEqual(['el-1']);
    // 空来源集登记首条来源
    expect(JSON.parse(buildSourceRegistrationValue([], 'el-9'))).toEqual(['el-9']);
  });

  it('isSupplementPending 仅在「待补充来源」派生态为真（决定出口卡是否渲染）', () => {
    expect(isSupplementPending({ display_code: 'supplement_pending' })).toBe(true);
    expect(isSupplementPending({ display_code: 'awaiting_adjudication' })).toBe(false);
    expect(isSupplementPending({ display_code: 'confirmed' })).toBe(false);
  });
});

describe('双入口共享件（区2 主按钮与区5 药丸同机制）', () => {
  it('模式选项单一来源：恒为四模式且顺序稳定', () => {
    expect(DIAGNOSIS_MODE_OPTIONS).toEqual(['quick', 'standard', 'comprehensive', 'incremental']);
  });

  it('发起命令文本与药丸预填逐字一致（同一轮次语义）', () => {
    expect(diagnosisLaunchCommand('standard', 0)).toBe(QUICK_COMMAND_PREFILLS.diagnose('标准'));
    expect(diagnosisLaunchCommand('comprehensive', 3)).toBe(QUICK_COMMAND_PREFILLS.diagnose('全面', 3));
  });

  it('区2 灰字按勾选范围给事实：全选=默认全部；子集=已勾选 N；零勾选=当前条目', () => {
    expect(diagnosisScopeHint(4, 4)).toBe('默认诊断全部可诊断条目（4 条），或在区1 勾选子集');
    expect(diagnosisScopeHint(2, 4)).toBe('已勾选 2 条纳入本次诊断');
    expect(diagnosisScopeHint(0, 4)).toContain('仅诊断当前条目');
    expect(diagnosisScopeHint(0, 0)).toBe('没有可诊断的条目');
  });

  it('reviewRunHint 按结构事实派生：可诊断且无在途/无待裁决=动态范围说明；其余透传（经显示层映射）', () => {
    expect(reviewRunHint('勾选无结论条目后发起诊断', 4, 4, 0, false)).toBe(diagnosisScopeHint(4, 4));
    expect(reviewRunHint(null, 0, 4, 0, false)).toBe(diagnosisScopeHint(0, 4));
    expect(reviewRunHint('有 2 个条目的结论待你裁决（采纳或拒绝）', 1, 4, 2, false))
      .toBe('有 2 个条目的结论待你裁决（采纳或拒绝）');
    expect(reviewRunHint('诊断进行中：单条目结论实时入流，可先裁决已产出的结论', 1, 4, 0, true))
      .toBe('诊断进行中：单条目结论实时入流，可先裁决已产出的结论');
    // 后端输出已清「无结论」话术（B2a A3）：非发起类文案原样透传，不再前端子串改写
    expect(reviewRunHint('没有可诊断的条目', 0, 0, 0, false)).toBe('没有可诊断的条目');
    expect(reviewRunHint(null, 1, 0, 0, false)).toBeNull();
  });

  it('reviewRunHint 零可选时发起类指引改写为「没有可诊断的条目」（合并裁定修复：不指引勾选全禁用的复选框）', () => {
    expect(reviewRunHint('勾选无结论条目后发起诊断', 0, 0, 0, false)).toBe('没有可诊断的条目');
    expect(reviewRunHint('勾选可诊断条目后发起诊断。', 0, 0, 0, false)).toBe('没有可诊断的条目');
    // 收束句不含发起类指引：原样透传
    expect(reviewRunHint('本阶段条目已收束，可返回维护视图', 0, 0, 0, false))
      .toBe('本阶段条目已收束，可返回维护视图');
  });
});

describe('显示态接线（issue #10 B2b：消费后端 display_code/display_note，deriveReviewDisplay 已退役）', () => {
  const withDisplay = (
    code: ReviewRequirementItemRead['display_code'] | string,
    note: string,
  ): ReviewRequirementItemRead => ({
    ...initialReviewWorkspace().review_items[0],
    display_code: code as ReviewRequirementItemRead['display_code'],
    display_note: note,
  });

  it('reviewDisplayMeta 八码 label/tone 映射（与状态机文档 §3 一致）', () => {
    expect(reviewDisplayMeta('diagnosing')).toEqual({ label: '诊断中', tone: 'processing' });
    expect(reviewDisplayMeta('awaiting_adjudication')).toEqual({ label: '待裁决', tone: 'warning' });
    expect(reviewDisplayMeta('confirmed')).toEqual({ label: '已确认', tone: 'success' });
    expect(reviewDisplayMeta('terminated')).toEqual({ label: '已终止', tone: 'neutral' });
    expect(reviewDisplayMeta('pending_diagnosis')).toEqual({ label: '待诊断', tone: 'neutral' });
    expect(reviewDisplayMeta('diagnosis_failed')).toEqual({ label: '诊断失败', tone: 'danger' });
    expect(reviewDisplayMeta('verdict_rejected')).toEqual({ label: '结论已拒绝', tone: 'warning' });
    expect(reviewDisplayMeta('supplement_pending')).toEqual({ label: '待补充来源', tone: 'warning' });
  });

  it('未知 display_code 兜底为中性「其他状态」（旧码遇新枚举不空徽标）', () => {
    expect(reviewDisplayMeta('some_future_code')).toEqual({ label: '其他状态', tone: 'neutral' });
  });

  it('mapReviewItems 显示态与说明句直取后端 display_code/display_note（不前端派生）', () => {
    const item = withDisplay('diagnosis_failed', '诊断已连续失败 3 次（原因见对话线程），可重试或改人工处理。');
    const [vm] = mapReviewItems([item], [], item.item_ref);
    expect(vm.statusText).toBe('诊断失败');
    expect(vm.statusTone).toBe('danger');
    expect(vm.groupKey).toBe('diagnosis_failed');
    expect(vm.statusNote).toBe('诊断已连续失败 3 次（原因见对话线程），可重试或改人工处理。');
  });

  it('reviewItemStatusNote 直取后端 display_note（区4/区5 空态共用单点）', () => {
    expect(reviewItemStatusNote(withDisplay('pending_diagnosis', '尚未发起过诊断。'))).toBe('尚未发起过诊断。');
    expect(reviewItemStatusNote(withDisplay('verdict_rejected', '上一轮结论已被拒绝，可重新诊断、人工修订、覆盖确认或撤回。')))
      .toBe('上一轮结论已被拒绝，可重新诊断、人工修订、覆盖确认或撤回。');
  });

  it('groupReviewItems 未知 display_code 归尾部兜底组，条目不从区1 消失（A1／债 #2）', () => {
    const known = withDisplay('awaiting_adjudication', '当前结论待你裁决。');
    const unknown = withDisplay('some_future_code', '（后端新增枚举，前端未及更新）');
    const vms = mapReviewItems([known, unknown], [], known.item_ref);
    const groups = groupReviewItems(vms);
    // 已知码入正常组，未知码不丢：条目总数守恒
    const grouped = groups.flatMap((g) => g.items);
    expect(grouped).toHaveLength(2);
    // 兜底组恒在尾部
    const last = groups[groups.length - 1];
    expect(last.key).toBe(REVIEW_DISPLAY_FALLBACK_GROUP);
    expect(last.label).toBe('其他状态');
    expect(last.items.map((i) => i.itemRef)).toEqual([unknown.item_ref]);
  });
});

describe('deriveDiagnosisRunProgress（区2 确定型进度：已处理 n/N）', () => {
  const run = (partial: Partial<DiagnosisRunProgressRead>): DiagnosisRunProgressRead => ({
    run_ref: 'B-1', item_refs: [], diagnosis_mode: 'standard', status: 'running',
    completed_count: 0, total_count: 0, failed_count: 0, next_action: null, ...partial,
  });

  it('在途批次给分数与百分比；分母=发起时捕获的 total_count', () => {
    const progress = deriveDiagnosisRunProgress([run({ completed_count: 2, total_count: 5 })]);
    expect(progress).not.toBeNull();
    expect(progress?.countsText).toBe('已处理 2/5');
    expect(progress?.pct).toBe(40);
  });

  it('多批并行求和；无在途批次返回 null（进度收敛不残留）', () => {
    const progress = deriveDiagnosisRunProgress([
      run({ run_ref: 'B-1', completed_count: 1, total_count: 2 }),
      run({ run_ref: 'B-2', completed_count: 2, total_count: 3 }),
      run({ run_ref: 'B-0', status: 'completed', completed_count: 4, total_count: 4 }),
    ]);
    expect(progress?.countsText).toBe('已处理 3/5');
    expect(deriveDiagnosisRunProgress([run({ status: 'completed', completed_count: 4, total_count: 4 })])).toBeNull();
  });
});

describe('collectRunFailureToasts（run 级聚合失败反馈重建于后端 failed_count——issue #10 B2b）', () => {
  const run = (
    partial: Partial<DiagnosisRunProgressRead> & Pick<DiagnosisRunProgressRead, 'run_ref' | 'status'>,
  ): DiagnosisRunProgressRead => ({
    item_refs: [], diagnosis_mode: 'standard', completed_count: 0, total_count: 0,
    failed_count: 0, next_action: null, ...partial,
  });

  it('running→completed 且 failed_count>0：产出一条聚合 toast（失败条数直取后端事实）', () => {
    const prev = new Map([['B-1', 'running']]);
    const { toasts, nextStatus } = collectRunFailureToasts(prev, [
      run({ run_ref: 'B-1', status: 'completed', item_refs: ['I-1', 'I-2'], failed_count: 1 }),
    ]);
    expect(toasts).toEqual([{ runRef: 'B-1', failedCount: 1 }]);
    expect(nextStatus.get('B-1')).toBe('completed');
  });

  it('同 run 去重：结算后再读同一 completed 快照不再产出', () => {
    const snapshot = [run({ run_ref: 'B-1', status: 'completed', failed_count: 2 })];
    const first = collectRunFailureToasts(new Map([['B-1', 'running']]), snapshot);
    const second = collectRunFailureToasts(first.nextStatus, snapshot);
    expect(first.toasts).toEqual([{ runRef: 'B-1', failedCount: 2 }]);
    expect(second.toasts).toHaveLength(0);
  });

  it('不弹分支：failed_count=0 不弹；进入页面已收束(无 running 前态)不弹；仍在途不弹', () => {
    expect(collectRunFailureToasts(new Map([['B-1', 'running']]),
      [run({ run_ref: 'B-1', status: 'completed', failed_count: 0 })]).toasts).toHaveLength(0);
    expect(collectRunFailureToasts(new Map(),
      [run({ run_ref: 'B-1', status: 'completed', failed_count: 3 })]).toasts).toHaveLength(0);
    expect(collectRunFailureToasts(new Map([['B-1', 'running']]),
      [run({ run_ref: 'B-1', status: 'running', failed_count: 1 })]).toasts).toHaveLength(0);
  });

  // 归因错位三场景免疫（旧启发式以条目全局最新态猜测 → 漏报/误计；新法直取 per-run failed_count）
  it('场景1 结算窗口内新批重诊不漏报：run A.failed_count 收束后稳定，不因条目被 run B 成功重诊归零', () => {
    // 旧法：item0 失败落 run A 后被 run B 成功重诊 → item 全局态非 failed → run A 漏报 0。
    // 新法：run A 自带 failed_count=1，与条目现态无关 → 仍弹。
    const { toasts } = collectRunFailureToasts(new Map([['A', 'running']]), [
      run({ run_ref: 'A', status: 'completed', item_refs: ['item0'], failed_count: 1 }),
      run({ run_ref: 'B', status: 'running', item_refs: ['item0'], failed_count: 0 }),
    ]);
    expect(toasts).toEqual([{ runRef: 'A', failedCount: 1 }]);
  });

  it('场景2 迁移被消费仍发：失败事实固化在本 run，条目迁移/消费不改 failed_count', () => {
    const { toasts } = collectRunFailureToasts(new Map([['A', 'running']]),
      [run({ run_ref: 'A', status: 'completed', item_refs: ['item0'], failed_count: 1 })]);
    expect(toasts).toEqual([{ runRef: 'A', failedCount: 1 }]);
  });

  it('场景3 跨批遗留不误计：run B.failed_count 只计本批成员，遗留失败不渗入', () => {
    // run B 只诊 item1（成功），item0 的遗留失败属 run A，不入 run B 计数 → run B 不弹。
    const { toasts } = collectRunFailureToasts(new Map([['B', 'running']]),
      [run({ run_ref: 'B', status: 'completed', item_refs: ['item1'], failed_count: 0 })]);
    expect(toasts).toHaveLength(0);
  });

  it('归因判别（合并裁定 F3）：两 run 同时收束，失败恰归到自己的 run，不跨批求和', () => {
    // 杀 mutant：`sum(runs.failed_count)` 式跨批渗入在此必挂——B 会带上 A 的失败数或多出一条 toast。
    const { toasts } = collectRunFailureToasts(new Map([['A', 'running'], ['B', 'running']]), [
      run({ run_ref: 'A', status: 'completed', item_refs: ['item0', 'item1'], failed_count: 2 }),
      run({ run_ref: 'B', status: 'completed', item_refs: ['item2'], failed_count: 0 }),
    ]);
    expect(toasts).toEqual([{ runRef: 'A', failedCount: 2 }]);
  });
});

// ---- 区3 拖选指定来源（T20260720-supplement-manual-source-and-attest 能力 A）----

describe('拖选选区落点判定', () => {
  /** 造一个片段：[start,end) 覆盖哪些知识项 */
  function seg(start: number, end: number, refs: string[]) {
    return { key: `s${start}`, text: '', start, end, refs, primaryColorKey: null, primaryStatus: null, relocated: false };
  }

  function element(id: string, content: string, status: 'confirmed' | 'pending_confirmation') {
    return {
      id, content, element_type: 'function', process_status: status,
      version: 1, superseded: false,
    } as unknown as FormationElementRead;
  }

  const elements = new Map<string, FormationElementRead>([
    ['E1', element('E1', '导出为 docx', 'confirmed')],
    ['E2', element('E2', '五秒内完成', 'confirmed')],
    ['E3', element('E3', '尚未确认的知识项', 'pending_confirmation')],
  ]);

  // 正文：[0,10) 无高亮 | [10,20) E1 | [20,30) 无 | [30,40) E2（第二个自然段）
  const blocks: CanvasBlockVM[] = [
    { blockId: 'B1', segments: [seg(0, 10, []), seg(10, 20, ['E1']), seg(20, 30, [])] },
    { blockId: 'B2', segments: [seg(30, 40, ['E2'])] },
  ];

  it('选区与知识项相交即命中，且可登记', () => {
    const hits = findSelectionHits(blocks, { start: 12, end: 16, text: '一段话' }, elements);
    expect(hits.map((h) => h.elementRef)).toEqual(['E1']);
    expect(hits[0].registrable).toBe(true);
  });

  it('只沾到边也算命中：相交判据是区间重叠，不是被包含', () => {
    // 选区 [8,12) 只覆盖 E1 片段的前两个字，仍应认出 E1——用户拖歪一点不该白拖。
    expect(findSelectionHits(blocks, { start: 8, end: 12, text: '两字' }, elements)
      .map((h) => h.elementRef)).toEqual(['E1']);
  });

  it('边界相接不算相交：选区结束正好等于片段起点时不命中', () => {
    // 杀 mutant：把 `seg.end <= sel.start` 写成 `<` 会让紧邻片段被误判为命中。
    expect(findSelectionHits(blocks, { start: 0, end: 10, text: '前十字' }, elements)).toEqual([]);
  });

  it('跨自然段的选区把两段里的知识项都收进来，按正文先后排列', () => {
    const hits = findSelectionHits(blocks, { start: 15, end: 35, text: '跨段' }, elements);
    expect(hits.map((h) => h.elementRef)).toEqual(['E1', 'E2']);
  });

  it('同一条知识项被多个片段覆盖时只出现一次', () => {
    const split: CanvasBlockVM[] = [
      { blockId: 'B1', segments: [seg(10, 15, ['E1']), seg(15, 20, ['E1'])] },
    ];
    expect(findSelectionHits(split, { start: 10, end: 20, text: '整条' }, elements)).toHaveLength(1);
  });

  it('落在空白处：一条都不命中（由界面给去补知识项的指引，不是死按钮）', () => {
    expect(findSelectionHits(blocks, { start: 21, end: 29, text: '空白段' }, elements)).toEqual([]);
  });

  it('命中未确认的知识项：不可登记，并说明为什么', () => {
    const pending: CanvasBlockVM[] = [{ blockId: 'B1', segments: [seg(0, 10, ['E3'])] }];
    const hits = findSelectionHits(pending, { start: 2, end: 6, text: '未确认' }, elements);
    expect(hits[0].registrable).toBe(false);
    expect(hits[0].blockedReason).toContain('确认');
  });

  it('画布里有高亮但工作区查不到该知识项：跳过，不猜一条出来', () => {
    const ghost: CanvasBlockVM[] = [{ blockId: 'B1', segments: [seg(0, 10, ['NOPE'])] }];
    expect(findSelectionHits(ghost, { start: 2, end: 6, text: '幽灵' }, elements)).toEqual([]);
  });
});

describe('修订记录·人工确认背书显示单点（C10：三页共用，防第四处再漏）', () => {
  const attestationRow = {
    field_key: 'source_attestation',
    before_value: '',
    after_value: '已人工确认为真实需求（材料未记载）',
    reason: '客户口头确认，纪要漏记',
    revision_mode: 'manual',
    operator_ref: 'U1',
    created_at: '2026-07-06T10:00:00',
  } as never;
  const expressionRow = {
    field_key: 'expression',
    before_value: '旧表达',
    after_value: '系统应支持批量导入',
    reason: null,
    revision_mode: 'manual',
    operator_ref: 'U1',
    created_at: '2026-07-06T11:00:00',
  } as never;

  it('isSourceAttestation 只认背书 field_key', () => {
    expect(isSourceAttestation(attestationRow)).toBe(true);
    expect(isSourceAttestation(expressionRow)).toBe(false);
  });

  it('背书行走白话整句，不显示 before→after、不露内部键名', () => {
    const text = revisionRecordText(attestationRow);
    expect(text).toBe('人工确认：已人工确认为真实需求（材料未记载）');
    expect(text).not.toContain('source_attestation');
    expect(text).not.toContain('→');
    expect(attestationRecordText(attestationRow)).toBe('人工确认：已人工确认为真实需求（材料未记载）');
  });

  it('普通字段行仍走「字段: 旧 → 新」（评审页语义保持）', () => {
    expect(revisionRecordText(expressionRow)).toBe('表达: 旧表达 → 系统应支持批量导入');
  });
});
