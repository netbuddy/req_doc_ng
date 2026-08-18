import type {
  FormationElementRead,
  ItemFormationWorkspaceRead,
  ItemizationBatchRequestResult,
  ItemizationResultRead,
  ItemRevisionRecordRead,
  ItemStructureReviewRead,
  PendingRequirementItemRead,
  RequirementItemType,
} from '../api/item-formation';
import type { ElementFacetReviewRead, ElementWorkspaceRead, MaterialTextBlockRead } from '../api/analysis';
import type { RequirementConventionCatalogRead } from '../api/settings';
import {
  buildCanvasBlocks,
  elementTypeMeta,
  fillSegmentText,
  mapFacetReview,
  resolveAnchor,
  type CanvasBlockVM,
  type ElementHighlight,
  type FacetBadgeVM,
  type FacetReviewVM,
  type ResolvedAnchor,
} from './requirement-analysis';
import type { BadgeTone } from './common';

const ITEMIZABLE_ELEMENT_TYPES = new Set([
  'functional_requirement',
  'quality_attribute',
  'constraint',
  'data_requirement',
  'interface_requirement',
]);

const ELEMENT_TO_ITEM_TYPE: Record<string, RequirementItemType> = {
  functional_requirement: 'functional',
  quality_attribute: 'quality',
  constraint: 'constraint',
  data_requirement: 'data',
  interface_requirement: 'interface',
};

export interface FormationElementListItemVM {
  id: string;
  typeLabel: string;
  typeColorKey: string;
  content: string;
  confidenceText: string;
  statusText: string;
  blockedReason: string | null;
}

export interface PendingRequirementItemVM {
  itemRef: string;
  reqNo: string;
  expression: string;
  typeText: string;
  statusText: string;
  statusTone: BadgeTone;
  sourceCountText: string;
  /** 达标投影键（区5 达标度筛选用；无档案判定为 null） */
  completenessKey: string | null;
  structureStale: boolean;
}

export function requirementItemTypeText(type: RequirementItemType): string {
  const map: Record<RequirementItemType, string> = {
    functional: '功能需求',
    quality: '质量属性',
    constraint: '约束',
    data: '数据需求',
    interface: '接口需求',
  };
  return map[type];
}

export function requirementItemStatusMeta(status: string): { label: string; tone: BadgeTone } {
  const map: Record<string, { label: string; tone: BadgeTone }> = {
    pending_confirmation: { label: '待确认', tone: 'processing' },
    confirmed: { label: '确认态', tone: 'success' },
    superseded: { label: '被替代', tone: 'neutral' },
    terminated: { label: '已终止', tone: 'danger' },
  };
  return map[status] ?? { label: status, tone: 'neutral' };
}

// ---- 陈述达标投影（structure_review；格式化 LDM-015 派生、非权威，只作提示不作门禁）----

const STATEMENT_CONFORMANCE_META: Record<string, { label: string; tone: BadgeTone }> = {
  conforms: { label: '句式符合', tone: 'success' },
  deviates: { label: '句式偏离', tone: 'warning' },
  not_applicable: { label: '句式不适用', tone: 'neutral' },
};

export interface ItemStructureReviewVM {
  profileVersion: number;
  conformance: { label: string; tone: BadgeTone } | null;
  /** 徽章/缺口/完备性复用要素侧 FacetReviewVM（facet 行同形，rubricVersion=profile_version） */
  review: FacetReviewVM;
}

/** structure_review → 徽章 VM；无档案类型（facets 为空）返回 null，不渲染徽章区。
 * 投影过期（stale）同样返回 null（走查第三轮裁定 2026-07-11：内容修订/拆分/归并已
 * 链式自动体检，「修订后未复核」不再是用户可见状态——过期投影在 UI 视同暂无当前体检，
 * 区4 不渲染旧表达的判定；链式失败的残留由区2「复核」修复通道兜底）。 */
export function mapItemStructureReview(
  review: ItemStructureReviewRead | null | undefined,
): ItemStructureReviewVM | null {
  if (!review || !(review.facets ?? []).length || review.stale) {
    return null;
  }
  const base = mapFacetReview({
    rubric_version: review.profile_version,
    correctness: null,
    completeness: review.completeness ?? null,
    facets: review.facets,
    stale: review.stale,
  } as ElementFacetReviewRead);
  if (!base) {
    return null;
  }
  return {
    profileVersion: review.profile_version,
    conformance: review.statement_conformance
      ? (STATEMENT_CONFORMANCE_META[review.statement_conformance] ?? null)
      : null,
    review: base,
  };
}

export function mapFormationElements(elements: FormationElementRead[]): FormationElementListItemVM[] {
  return elements.map((element) => {
    const type = elementTypeMeta(element.element_type);
    return {
      id: element.id,
      typeLabel: type.label,
      typeColorKey: type.colorKey,
      content: element.content,
      confidenceText:
        element.confidence !== null && element.confidence !== undefined
          ? `${Math.round(element.confidence * 100)}%`
          : '—',
      statusText: element.process_status === 'confirmed' ? '可形成' : '不可形成',
      blockedReason: element.blocked_reason ?? null,
    };
  });
}

// ---- 区1 分组清单（视觉基准件 v2：按语义类型分组，可条目化五类固定序，其余按出现序）----

const ELEMENT_GROUP_ORDER = ['func', 'quality', 'constraint', 'data', 'interface'];

export interface FormationElementGroupVM {
  key: string;
  typeLabel: string;
  typeColorKey: string;
  items: FormationElementListItemVM[];
}

export function groupFormationElements(items: FormationElementListItemVM[]): FormationElementGroupVM[] {
  const groups = new Map<string, FormationElementGroupVM>();
  for (const item of items) {
    let group = groups.get(item.typeColorKey);
    if (!group) {
      group = { key: item.typeColorKey, typeLabel: item.typeLabel, typeColorKey: item.typeColorKey, items: [] };
      groups.set(item.typeColorKey, group);
    }
    group.items.push(item);
  }
  const orderOf = (key: string) => {
    const index = ELEMENT_GROUP_ORDER.indexOf(key);
    return index === -1 ? ELEMENT_GROUP_ORDER.length : index;
  };
  return [...groups.values()].sort((a, b) => orderOf(a.key) - orderOf(b.key));
}

export function mapPendingItems(items: PendingRequirementItemRead[]): PendingRequirementItemVM[] {
  return items.map((item) => {
    const status = requirementItemStatusMeta(item.status);
    return {
      itemRef: item.item_ref,
      reqNo: item.req_no,
      expression: item.expression,
      typeText: requirementItemTypeText(item.req_type),
      statusText: status.label,
      statusTone: status.tone,
      sourceCountText: `${(item.source_element_refs ?? []).length} 个来源要素`,
      completenessKey: item.structure_review?.completeness ?? null,
      structureStale: Boolean(item.structure_review?.stale),
    };
  });
}

// ---- 区2 批次确定型进度（T20260711-formation-z2z5-visual；用户走查第 2 轮定稿）----
// 进度条只表达执行进度：分数「已处理 X/总数」，分母=发起批次时捕获的选中要素数，
// 分子=已返回归因数（含未能形成条目的要素——结果好坏归终态摘要与区5「批次结果」，不进进度条）。
// 口径与勾选集自洽：给定 scopeRefs 时只统计发起范围内要素的归因（后端对范围外
// 不可见要素——如已撤销——的归因不进入进度账目，事实仍见区5「批次结果」）。
// 分母拿不到（恢复会话等）时降级为「已返回 N 条」不定型模式，不造假分母。

/** 进度分数悬停释义（避免行话；「已处理」含未能成条的要素） */
export const BATCH_PROGRESS_HINT =
  '已处理 / 批次总数。已处理包含未能形成条目的要素，逐要素原因见区5「批次结果」';

export interface BatchProgressVM {
  /** 有真实分母 = 确定型；false = 降级不定型 */
  determinate: boolean;
  /** 已返回归因的要素数（执行进度分子） */
  processed: number;
  /** 其中已形成条目数（终态摘要用） */
  formed: number;
  remaining: number;
  /** 进度填充宽度（百分数；分母以 max(总数, 已处理) 收口防溢出） */
  processedPct: number;
  /** 旁挂计数文案：确定型「已处理 X/Y」；不定型「已返回 N 条」 */
  countsText: string;
}

export function deriveBatchProgress(
  total: number | null,
  results: Array<Pick<ItemizationResultRead, 'result_status' | 'element_ref'>>,
  scopeRefs?: readonly string[] | null,
): BatchProgressVM {
  const scope = scopeRefs ? new Set(scopeRefs) : null;
  const scoped = scope ? results.filter((r) => scope.has(r.element_ref)) : results;
  const processed = scoped.length;
  const formed = scoped.filter((r) => r.result_status === 'created').length;
  if (total === null || total <= 0) {
    return {
      determinate: false,
      processed,
      formed,
      remaining: 0,
      processedPct: 0,
      countsText: `已返回 ${processed} 条`,
    };
  }
  // 兜底钳制：未提供范围（不定型恢复等）时已处理数仍可能超过分母，按已处理收口防溢出
  const base = Math.max(total, processed);
  return {
    determinate: true,
    processed,
    formed,
    remaining: Math.max(total - processed, 0),
    processedPct: Math.round((processed / base) * 100),
    countsText: `已处理 ${processed}/${total}`,
  };
}

// ---- 区5 条目行 mini 达标徽标（单行紧凑行与旧两行行同口径）----

/** 徽标悬停释义（mini 徽标与达标度筛选 chips 同文案）。
 * 走查第三轮裁定（2026-07-11）：「修订后未复核」不再是用户可见状态——内容修订/拆分/归并
 * 已链式自动体检，徽标只呈现锚定当前表达的真判定；投影过期视同暂无体检（无徽标）。 */
export const ITEM_COMPLETENESS_BADGE_HINTS = {
  incomplete: '陈述缺少句式档案的必备成分（如触发条件/可观测结果），详见区4；仅提示不阻断',
} as const;

/** 现行判定键（issue #8 清理债：stale 抑制单点收口，各消费点共用）——
 * 投影过期（修订后未复核在途瞬态）视同暂无体检 → null（走查第三轮裁定）。 */
export function effectiveCompletenessKey(
  item: Pick<PendingRequirementItemVM, 'completenessKey' | 'structureStale'>,
): string | null {
  return item.structureStale ? null : item.completenessKey;
}

export function itemCompletenessBadge(
  item: Pick<PendingRequirementItemVM, 'completenessKey' | 'structureStale'>,
): { label: string; tone: BadgeTone; hint: string | null } | null {
  const key = effectiveCompletenessKey(item);
  if (key === 'incomplete') {
    return { label: '不完备', tone: 'warning', hint: ITEM_COMPLETENESS_BADGE_HINTS.incomplete };
  }
  if (key) {
    return { label: '完备', tone: 'success', hint: null };
  }
  return null;
}

// ---- 区4 陈述体检报告（T20260711-item-completeness-ux 裁定 1）----
// 界面文案取档案数据（facet label/note/revision_hint 随投影回传；方案名与
// statement_pattern 经 AEP-102 目录取），前端不手写第二套判据。

export interface StructureHealthReportVM {
  /** 人话头：这份报告是什么、不完备意味着什么、不拦流程 */
  intro: string;
  /** 句式模板折叠区标题（方案名 · 类型） */
  patternTitle: string;
  /** 档案 statement_pattern 原文；AEP-102 目录不可达时 null → 模板块不渲染 */
  pattern: string | null;
  /** 待补成分（missing/ambiguous 的必备 facet），置顶展示 */
  requiredGaps: FacetBadgeVM[];
  /** 可选成分缺口（required=false，不影响「不完备」判定） */
  optionalGaps: FacetBadgeVM[];
  /** 已具备成分（折叠展示，含来源证据） */
  present: FacetBadgeVM[];
  /** 判据不适用成分（N/A，中性呈现＋判定理由；不计缺口，用户可核对不是漏检） */
  notApplicable: FacetBadgeVM[];
}

/** 按条目锚定的方案 key 从 AEP-102 目录解析方案名与该类型的句式模板。 */
export function resolveConventionPattern(
  conventionKey: string | null | undefined,
  typeText: string,
  catalog: RequirementConventionCatalogRead | null,
): { conventionName: string | null; pattern: string | null } {
  const convention = conventionKey
    ? catalog?.conventions.find((c) => c.convention_key === conventionKey)
    : undefined;
  if (!convention) {
    return { conventionName: null, pattern: null };
  }
  return {
    conventionName: convention.display_name,
    pattern: convention.pattern_overview.find((p) => p.label === typeText)?.pattern ?? null,
  };
}

export function deriveStructureHealthReport(
  review: FacetReviewVM,
  typeText: string,
  conventionName: string | null,
  pattern: string | null,
): StructureHealthReportVM {
  const name = conventionName ?? '当前规约方案';
  return {
    intro: `按「${name}」的「${typeText}」句式档案对这条陈述做的机器体检——必备成分缺失即『不完备』。仅提示，不拦任何流程。`,
    patternTitle: `${name} · ${typeText} 句式模板`,
    pattern,
    requiredGaps: review.gaps.filter((g) => g.required),
    optionalGaps: review.gaps.filter((g) => !g.required),
    present: review.badges.filter((b) => b.status === 'present'),
    notApplicable: review.badges.filter((b) => b.status === 'not_applicable'),
  };
}

// ---- 进入评审知情软门（裁定 3：canEnterReview 事实门禁不变；有缺口→确认弹层，零缺口直进）----

/** 软门清单单条：条目名（REQ 编号）＋表达＋缺口成分名，供弹层列出并点击定位。 */
export interface ReviewGateItemVM {
  itemRef: string;
  reqNo: string;
  expression: string;
  /** 缺口成分名（必备且 missing/ambiguous；not_applicable/present 已排除） */
  gapLabels: string[];
}

export interface ReviewGateGapsVM {
  incomplete: number;
  title: string;
  /** 「X 条不完备」（走查第三轮裁定：过期投影视同暂无体检，不再作为缺口类目） */
  countsText: string;
  /** 不完备条目清单（名称＋缺口成分），弹层逐条列出、可点定位 */
  items: ReviewGateItemVM[];
}

/** 待确认条目中的不完备清单（名称＋缺口成分）；无缺口返回 null（直进不弹）。
 *
 * 直接读结构投影读模型：completeness/stale/facets 一手取——stale（修订后未复核在途瞬态）
 * 视同暂无当前体检，不计缺口（与 effectiveCompletenessKey 同口径）。 */
export function deriveReviewGateGaps(
  items: PendingRequirementItemRead[],
): ReviewGateGapsVM | null {
  const gateItems: ReviewGateItemVM[] = [];
  for (const item of items) {
    if (item.status !== 'pending_confirmation') {
      continue;
    }
    const sr = item.structure_review;
    // 无档案投影 / 投影过期 → 暂无当前体检，不计缺口
    if (!sr || sr.stale || sr.completeness !== 'incomplete') {
      continue;
    }
    const gapLabels = (sr.facets ?? [])
      .filter((f) => f.required && f.status !== 'present' && f.status !== 'not_applicable')
      .map((f) => f.label);
    gateItems.push({
      itemRef: item.item_ref,
      reqNo: item.req_no,
      expression: item.expression,
      gapLabels,
    });
  }
  if (!gateItems.length) {
    return null;
  }
  return {
    incomplete: gateItems.length,
    title: `带着 ${gateItems.length} 条缺口进入评审？`,
    countsText: `${gateItems.length} 条不完备`,
    items: gateItems,
  };
}

/** 软门弹层两行说明（裁定 3；不做硬阻断的依据见覆盖标记表） */
export const REVIEW_GATE_NOTES = [
  '评审诊断会逐条深查并给出修订建议，缺口可以携带进入评审处理。',
  '也可以先留在本页用 /修订 或字段修订补齐后再进入。',
] as const;

// ---- AEP-114 批量复核目标集（区2 按钮确认弹层计数；修复通道）----
// 走查第三轮裁定后手动复核降级为修复通道：内容修订/拆分/归并已链式自动体检，正常
// 流程不会残留目标；目标集 = 待确认 ∩ 暂无当前体检（投影过期 ∪ 从未判定/未得出
// 完备性判定），排除已终止与现行判定条目。权威目标集由后端受理时重算，此处只供
// 弹层计数与按钮禁用。

export interface RecheckTargetsVM {
  total: number;
  /** 「N 条暂无当前体检」（单一口径；走查第三轮裁定不再区分过期/缺失两类） */
  countsText: string;
  targetRefs: string[];
}

/** 待确认条目中的可复核目标计数；无目标返回 null（区2 按钮禁用）。 */
export function deriveRecheckTargets(
  items: Array<Pick<PendingRequirementItemVM, 'itemRef' | 'completenessKey' | 'structureStale' | 'statusText'>>,
): RecheckTargetsVM | null {
  const targetRefs = items
    .filter((i) => i.statusText === '待确认')
    .filter((i) => effectiveCompletenessKey(i) === null)
    .map((i) => i.itemRef);
  if (!targetRefs.length) {
    return null;
  }
  return {
    total: targetRefs.length,
    countsText: `${targetRefs.length} 条暂无当前体检`,
    targetRefs,
  };
}

/** 复核目标为空时区2 按钮的禁用说明（title；与后端 rejected_precheck 文案同口径） */
export const RECHECK_DISABLED_REASON = '没有需要复核的条目：待确认条目均有当前体检';

// ---- AEP-038 批次受理后续处置（HK-1：in_flight=复用在途批次，复挂原 run 轮询不报错）----

export type BatchSubmitFollowup =
  | { kind: 'reattach'; runId: string; contextRef: string; notice: string }
  | { kind: 'watch'; runId: string; contextRef: string }
  | { kind: 'refresh'; contextRef: string }
  | { kind: 'rejected'; notice: string };

export function resolveBatchSubmitFollowup(
  result: ItemizationBatchRequestResult,
): BatchSubmitFollowup {
  if (result.status === 'in_flight' && result.agent_run_ref && result.formation_context_ref) {
    return {
      kind: 'reattach',
      runId: result.agent_run_ref,
      contextRef: result.formation_context_ref,
      notice: result.next_action ?? '条目化批次执行中，已恢复进度跟踪。',
    };
  }
  if (result.status !== 'submitted' || !result.formation_context_ref) {
    return { kind: 'rejected', notice: result.next_action ?? '批次未受理' };
  }
  if (result.agent_run_ref) {
    return { kind: 'watch', runId: result.agent_run_ref, contextRef: result.formation_context_ref };
  }
  return { kind: 'refresh', contextRef: result.formation_context_ref };
}

// ---- AEP-097 区5 快捷命令（前端只预填 /命令词，可自由续写；后端注册表定词）----

export const FORMATION_QUICK_COMMAND_PREFILLS = {
  generate: () => '/生成条目',
  adjustType: (typeLabel: string) => `/改类型 ${typeLabel}`,
  revise: () => '/修订 修订为：',
  // 区4 体检报告「让 AI 起草补写」：只写方向 → AI 起草建议卡（item_draft lane），不直发
  reviseGap: (facetLabel: string) => `/修订 补写${facetLabel}：`,
  normalize: () => '/规范化',
  split: () => '/拆分：\n1. \n2. ',
  merge: (reqNos: string[]) =>
    `/归并 ${reqNos.map((n) => `「${n}」`).join('')}归并后表达：`,
  askSource: () => '/问来源',
  referenceBasis: () => '/引用依据 ',  // P7 §1.2：引用业务知识为支撑依据（后接名称）
  // AEP-114：复核当前条目（无自由参数，后端直发通道不经解释 lane；批次复核归区2 按钮）
  recheck: () => '/复核',
} as const;

// ---- P2：区5 达标度筛选（纯 UI 态，归 ViewModel；读投影，不作门禁）----

export const ITEM_COMPLETENESS_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'incomplete', label: '不完备' },
] as const;

export type ItemCompletenessFilterKey = (typeof ITEM_COMPLETENESS_FILTERS)[number]['key'];

export function matchesItemCompletenessFilter(
  item: Pick<PendingRequirementItemVM, 'completenessKey' | 'structureStale'>,
  filter: ItemCompletenessFilterKey,
): boolean {
  if (filter === 'all') {
    return true;
  }
  // 过期判定视同暂无体检：不按旧判定计入不完备（走查第三轮裁定）
  return effectiveCompletenessKey(item) === 'incomplete';
}

/**
 * 首次进入条目形成页（尚未发起批次、没有 formation_context_ref）时，
 * 从要素工作区本地投影五区初始状态；发起批次后一律以后端工作区读视图为准。
 */
export function buildFormationWorkspaceFromElementWorkspace(
  source: ElementWorkspaceRead,
): ItemFormationWorkspaceRead {
  const materialCanvas = source.material_canvas ?? {
    material_ref: 'unknown-material',
    title: '当前材料',
    source_note: null,
    raw_text: '',
    source_version: 1,
    blocks: [],
    supplements: [],
  };
  const eligible: ItemFormationWorkspaceRead['eligible_elements'] = [];
  const blocked: ItemFormationWorkspaceRead['blocked_elements'] = [];

  for (const element of source.elements ?? []) {
    if (element.superseded) {
      continue;
    }
    if (!ITEMIZABLE_ELEMENT_TYPES.has(element.element_type)) {
      if (element.process_status !== 'revoked') {
        blocked.push({ ...element, formation_role: 'supporting', blocked_reason: '支撑或上下文类要素仅作为依据' });
      }
      continue;
    }
    if (element.process_status !== 'confirmed') {
      const reason = element.process_status === 'revoked'
        ? '已撤销要素不参与条目形成'
        : '要素未确认：请回需求分析确认或校正';
      blocked.push({ ...element, formation_role: 'blocked', blocked_reason: reason });
      continue;
    }
    eligible.push(element);
  }

  return {
    formation_context_ref: '',
    parse_result_ref: source.parse_result_ref ?? null,
    workspace_version: source.workspace_version,
    material_canvas: materialCanvas,
    eligible_elements: eligible,
    blocked_elements: blocked,
    intent_context: [],
    pending_items: [],
    selected_item_ref: null,
    batch_results: [],
    revision_suggestions: [],
    available_actions: [
      { key: 'start_review', enabled: false, disabled_reason: '尚未形成待确认条目' },
      { key: 'return_to_elements', enabled: true, disabled_reason: null },
    ],
    available_operations: [
      {
        key: 'start_itemization',
        enabled: eligible.length > 0,
        disabled_reason: eligible.length ? null : '没有可条目化的已确认需求表达类要素',
      },
      { key: 'apply_revision', enabled: false, disabled_reason: '尚未形成待确认条目' },
      { key: 'accept_revision_suggestion', enabled: false, disabled_reason: '没有候选修订建议' },
    ],
    next_action: eligible.length
      ? '选择区1要素后点击“生成待确认条目”。'
      : '没有适合条目形成的有效需求表达类要素。',
  };
}

/**
 * 本地演示投影（仅供 SCN-003 条目评审页 fixture 组装使用；
 * 条目形成页真实批次一律走 AEP-038 后端）。
 */
export function createPendingItemsFromElements(
  workspace: ItemFormationWorkspaceRead,
  selectedElementRefs: string[],
): ItemFormationWorkspaceRead {
  const selected = new Set(selectedElementRefs);
  const candidates = workspace.eligible_elements.filter((element) => selected.has(element.id));
  const existingBySource = new Set(
    workspace.pending_items.flatMap((item) => item.source_element_refs ?? []),
  );
  const newCandidates = candidates.filter((element) => !existingBySource.has(element.id));

  const offset = workspace.pending_items.length;
  const created: PendingRequirementItemRead[] = newCandidates.map((element, index) => ({
    item_ref: `ITEM-PENDING-${offset + index + 1}`,
    req_no: `REQ-${String(offset + index + 1).padStart(3, '0')}`,
    expression: normalizeRequirementExpression(element.content),
    req_type: ELEMENT_TO_ITEM_TYPE[element.element_type] ?? 'functional',
    status: 'pending_confirmation',
    version_no: 1,
    source_element_refs: [element.id],
    formation_basis_ref: `LDM015-ITEM-${element.id}`,
    revision_records: [],
    available_actions: [
      { key: 'apply_revision', enabled: true, disabled_reason: null },
      { key: 'enter_review', enabled: true, disabled_reason: null },
    ],
  }));

  const suggestions = created.slice(0, 2).map((item, index) => ({
    suggestion_ref: `SUG-${item.item_ref}`,
    item_ref: item.item_ref,
    field_key: 'expression',
    proposed_value: `${item.expression}，并保留可追溯来源依据。`,
    reason: index === 0 ? '补充来源约束，避免形成无来源新事实。' : '收敛表达，保持待确认条目字段完整。',
    status: 'candidate',
  }));

  return {
    ...workspace,
    workspace_version: incrementVersion(workspace.workspace_version),
    pending_items: [...workspace.pending_items, ...created],
    selected_item_ref: created[0]?.item_ref ?? workspace.selected_item_ref,
    revision_suggestions: [...workspace.revision_suggestions, ...suggestions],
    batch_results: created.map((item) => ({
      element_ref: (item.source_element_refs ?? [])[0] ?? '',
      result_status: 'created' as const,
      item_ref: item.item_ref,
      formation_basis_ref: item.formation_basis_ref,
      reason: null,
      next_action: null,
    })),
    available_actions: [
      { key: 'start_review', enabled: created.length > 0, disabled_reason: null },
      { key: 'return_to_elements', enabled: true, disabled_reason: null },
    ],
    available_operations: [
      { key: 'start_itemization', enabled: true, disabled_reason: null },
      { key: 'apply_revision', enabled: created.length > 0, disabled_reason: null },
      { key: 'accept_revision_suggestion', enabled: suggestions.length > 0, disabled_reason: null },
    ],
    next_action: '待确认条目已形成；可在同页字段修订后进入条目评审。',
  };
}

export function resolveFormationAnchors(workspace: ItemFormationWorkspaceRead): Map<string, ResolvedAnchor> {
  const map = new Map<string, ResolvedAnchor>();
  const canvas = workspace.material_canvas;
  if (!canvas) {
    return map;
  }
  for (const element of [...workspace.eligible_elements, ...workspace.blocked_elements]) {
    map.set(element.id, resolveAnchor(element.source_anchor ?? null, canvas.material_ref, canvas.raw_text));
  }
  return map;
}

export function buildItemSourceCanvas(
  workspace: ItemFormationWorkspaceRead,
  selectedItem: PendingRequirementItemRead | null,
  anchors: Map<string, ResolvedAnchor>,
): CanvasBlockVM[] {
  const sourceRefs = new Set(selectedItem?.source_element_refs ?? []);
  const highlights: ElementHighlight[] = [];
  for (const element of workspace.eligible_elements) {
    const anchor = anchors.get(element.id);
    if (!anchor?.ranges.length) {
      continue;
    }
    highlights.push({
      elementId: element.id,
      typeColorKey: sourceRefs.has(element.id) ? 'interface' : elementTypeMeta(element.element_type).colorKey,
      processStatus: sourceRefs.has(element.id) ? 'confirmed' : element.process_status,
      ranges: anchor.ranges,
    });
  }
  const blocks: MaterialTextBlockRead[] = workspace.material_canvas?.blocks ?? [];
  return fillSegmentText(blocks, buildCanvasBlocks(blocks, highlights));
}

export function mapSourceElementsById(workspace: ItemFormationWorkspaceRead): Map<string, FormationElementRead> {
  return new Map(
    [...workspace.eligible_elements, ...workspace.blocked_elements].map((element) => [element.id, element]),
  );
}

export function revisionRecordFieldText(record: ItemRevisionRecordRead): string {
  const map: Record<string, string> = {
    expression: '需求表达',
    req_type: '需求类型',
    curation_note: '整理说明',
    boundary_note: '边界说明',
    verification_method: '验证方式',
    verification_note: '验收准则',
    priority: '优先级',
  };
  return map[record.field_key] ?? record.field_key;
}

// ---- 29148 属性补齐（验证方式/优先级）展示口径：稳定码=后端契约，标签=视图配置 ----

export const VERIFICATION_METHOD_LABELS: Record<string, string> = {
  test: '测试',
  demonstration: '演示',
  inspection: '检查',
  analysis: '分析',
};

export const PRIORITY_LABELS: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

/** 验证方式多选（稳定码列表）→ 中文顿号连接；空值返回 null（由调用侧给缺失文案）。 */
export function verificationMethodText(methods: string[] | null | undefined): string | null {
  if (!methods || methods.length === 0) return null;
  return methods.map((m) => VERIFICATION_METHOD_LABELS[m] ?? m).join('、');
}

export function priorityText(priority: string | null | undefined): string | null {
  return priority ? (PRIORITY_LABELS[priority] ?? priority) : null;
}

function normalizeRequirementExpression(content: string): string {
  const trimmed = content.trim().replace(/\s+/g, ' ');
  if (!trimmed) {
    return '待补充需求表达。';
  }
  return trimmed.startsWith('系统应') ? trimmed : `系统应${trimmed.replace(/^应/, '')}`;
}

function incrementVersion(version: string): string {
  const current = Number(version);
  return Number.isFinite(current) ? String(current + 1) : `${version}.1`;
}
