/**
 * 条目评审阶段 ViewModel（SCN-003 v5 前端投影：线程 / 会话条 / 动态流）。
 *
 * 事实源：docs/40 slices/SCN-003-P01/页面详细设计.md（v5）§3。
 * 只做 UI 投影，不复制领域规则：派生显示态、按钮可用性、结论有效性均来自后端
 * ItemReviewWorkspaceRead（AEP-033）；本文件仅承担文案映射、线程装配与来源画布。
 * 后端不可达时用 buildInitialReviewWorkspace 做待诊断初始投影（不伪造结论）。
 */
import type { MaterialTextBlockRead } from '../api/analysis';
import type {
  DiagnosisMode,
  DiagnosisRunProgressRead,
  DiagnosisTrigger,
  DialogueMessageRead,
  ItemReviewWorkspaceRead,
  ItemRevisionRecordRead,
  ReviewDisplayCode,
  ReviewFindingRead,
  ReviewFindingType,
  ReviewRequirementItemRead,
  RevisionPointRead,
  SourceCandidateRead,
  VerdictKind,
  VerdictRead,
} from '../api/item-review';
import type { FormationElementRead, ItemFormationWorkspaceRead } from '../api/item-formation';
import type { BadgeTone } from './common';
import {
  buildCanvasBlocks,
  elementTypeMeta,
  fillSegmentText,
  resolveAnchor,
  type CanvasBlockVM,
  type ElementHighlight,
  type ResolvedAnchor,
} from './requirement-analysis';
import { requirementItemTypeText } from './requirement-item-formation';

// ---- 显示态呈现映射（issue #10 B2b 接线）----

/**
 * 显示态纯呈现映射：label/tone 是 UI 关注点，语义分桶（no_verdict 二次细分、到达路径副语）
 * 全部下沉后端 `display_code`/`display_note` 单点（B2a 契约，deriveReviewDisplay 已退役）。
 * 本表只承担 code→{label,tone}，八码与状态机文档 §3 一致。
 */
export interface ReviewDisplayMeta {
  label: string;
  tone: BadgeTone;
}

const REVIEW_DISPLAY_META: Record<ReviewDisplayCode, ReviewDisplayMeta> = {
  diagnosing: { label: '诊断中', tone: 'processing' },
  awaiting_adjudication: { label: '待裁决', tone: 'warning' },
  confirmed: { label: '已确认', tone: 'success' },
  terminated: { label: '已终止', tone: 'neutral' },
  pending_diagnosis: { label: '待诊断', tone: 'neutral' },
  diagnosis_failed: { label: '诊断失败', tone: 'danger' },
  verdict_rejected: { label: '结论已拒绝', tone: 'warning' },
  supplement_pending: { label: '待补充来源', tone: 'warning' },
};

/** 兜底组：未知 display_code（后端新增枚举而前端未及更新）中性呈现，条目不消失（issue #10 债 #2）。 */
export const REVIEW_DISPLAY_FALLBACK_GROUP = '__other__';
const FALLBACK_DISPLAY_META: ReviewDisplayMeta = { label: '其他状态', tone: 'neutral' };

/** code→呈现映射（未知码兜底中性）：徽标不空、条目不从区1 分组消失。 */
export function reviewDisplayMeta(code: string): ReviewDisplayMeta {
  return REVIEW_DISPLAY_META[code as ReviewDisplayCode] ?? FALLBACK_DISPLAY_META;
}

/** 区1 分组顺序（注意力优先级）；空组不渲染，未知码归尾部兜底组。 */
export const REVIEW_DISPLAY_GROUPS: Array<{ key: ReviewDisplayCode; label: string }> = [
  { key: 'awaiting_adjudication', label: '待裁决' },
  { key: 'diagnosing', label: '诊断中' },
  { key: 'diagnosis_failed', label: '诊断失败' },
  { key: 'verdict_rejected', label: '结论已拒绝' },
  { key: 'supplement_pending', label: '待补充来源' },
  { key: 'pending_diagnosis', label: '待诊断' },
  { key: 'confirmed', label: '已确认' },
  { key: 'terminated', label: '已终止' },
];

/**
 * 人工确认的可用性：读后端 affordance，不前端自算门禁。
 *
 * 后端在条目已经确认过一次之后关掉这个入口——出处缺口那时就闭合了，此后再判「建议补充
 * 来源」缺的必定是格式/字段/阈值这类具体值，人工确认一个都提供不了。存量后端没有这条
 * affordance 时按可用处理，与改前行为一致（宁可多给入口，也不静默藏掉功能）。
 */
export function attestAffordance(
  item: Pick<ReviewRequirementItemRead, 'available_actions'>,
): { enabled: boolean; reason: string | null } {
  const fact = item.available_actions.find((a) => a.key === 'attest_source');
  if (!fact) return { enabled: true, reason: null };
  return { enabled: fact.enabled, reason: fact.disabled_reason ?? null };
}

/** 显示态说明单点（区4/区5 空态与结论条共用）：直取后端 display_note，不前端派生。 */
export function reviewItemStatusNote(
  item: Pick<ReviewRequirementItemRead, 'display_note'>,
): string {
  return item.display_note;
}

export function diagnosisModeText(mode: DiagnosisMode): string {
  const map: Record<DiagnosisMode, string> = {
    quick: '快速',
    standard: '标准',
    comprehensive: '全面',
    incremental: '增量',
  };
  return map[mode];
}

/**
 * 区5 快捷命令预填构造器（AEP-095 斜杠命令）：药丸只向输入框预填 `/命令词` 前缀文本，
 * 前端不解析命令词，发送时整段原文交后端确定性解析 + LLM 解释派发。
 */
export const QUICK_COMMAND_PREFILLS = {
  diagnose: (modeLabel: string, selectedCount?: number) =>
    selectedCount ? `/诊断 对已勾选的 ${selectedCount} 个条目发起${modeLabel}诊断` : `/诊断 ${modeLabel}`,
  rejectVerdict: (roundNo: number) => `/拒绝结论 第${roundNo}轮 `,
  adoptDraft: () => '/采纳草案',
  manualRevision: () => '/修订 把当前条目的表达修订为：',
  overrideConfirm: () => '/覆盖确认 理由：',
  withdraw: () => '/撤回 理由：',
  // 补充来源出口（issue #30）：findSources 供自动查一次/重新查找直发；
  // specifySource 供〔按说明查找〕预填（留尾空格让用户补说明后重跑该 lane）
  findSources: () => '/找来源',
  specifySource: () => '/找来源 ',
} as const;

// ---- 补充来源出口（issue #30 出口三部曲之三；ADR-0002 P1 无死胡同 / P3 说缺必说补）----

/** 候选来源卡投影：每条带原文引文、推荐理由与要素类型徽标呈现元数据。 */
export interface SourceCandidateCardVM {
  elementRef: string;
  typeLabel: string;
  typeColorKey: string;
  content: string;
  sourceQuote: string | null;
  reason: string;
  rank: number;
}

/** 候选来源投影：按 rank 升序（1 最相关），附要素类型徽标呈现元数据。 */
export function buildSourceCandidateCards(
  candidates: readonly SourceCandidateRead[],
): SourceCandidateCardVM[] {
  return [...candidates]
    .sort((a, b) => (a.rank || 0) - (b.rank || 0))
    .map((c) => {
      const meta = elementTypeMeta(c.element_type);
      return {
        elementRef: c.element_ref,
        typeLabel: meta.label,
        typeColorKey: meta.colorKey,
        content: c.content,
        sourceQuote: c.source_quote ?? null,
        reason: c.reason,
        rank: c.rank || 0,
      };
    });
}

/** 修订记录里字段名的中文说法。查不到的字段名照原样显示——宁可露出内部名，也不猜。 */
const REVISION_FIELD_LABELS: Record<string, string> = {
  expression: '表达',
  req_type: '类型',
  priority: '优先级',
  verification_method: '验证方式',
  verification_note: '验收准则',
  source_element_refs: '来源要素',
};

// ---- 背书记录的显示单点（评审页/管理台/形成页共用同一 isSourceAttestation 判据，防第四处再漏）----
// 人工确认背书借修订记录表落库，但并没有把某个字段从旧值改成新值。照通用「字段: 旧 → 新」
// 渲染会显示成「source_attestation:  → 已人工确认…」，既露内部键名又像是改过什么，还会被
// 误标「人工修订」。三页用法不同：管理台与形成页把背书行就地渲染成白话（走 attestationRecordText /
// SOURCE_ATTESTATION_LABEL）；评审页在区4 另有独立背书块承载该事实，故 import isSourceAttestation 只为
// 把背书行从修订记录里过滤掉，其余记录仍走 revisionRecordText。背书行的白话特判：只显示一句话、不显示
// before→after、不标修订方式。后端字段码单一来源在 domain/labels.py（SOURCE_ATTESTATION_FIELD_KEY）。

/** 背书借修订表落库的 field_key（前端侧显示单点）。 */
export const SOURCE_ATTESTATION_FIELD = 'source_attestation';

/** 背书行的白话标签（不叫「人工修订」——它一个字段都没改）。 */
export const SOURCE_ATTESTATION_LABEL = '人工确认';

/** 一条修订记录是否为人工确认背书（三页据此走白话特判）。 */
export function isSourceAttestation(record: { field_key: string }): boolean {
  return record.field_key === SOURCE_ATTESTATION_FIELD;
}

/** 背书行的白话显示句：「人工确认：已人工确认为真实需求（材料未记载）」。 */
export function attestationRecordText(record: { after_value: string }): string {
  return `${SOURCE_ATTESTATION_LABEL}：${record.after_value}`;
}

/**
 * 一条修订记录的显示文本（评审页区4；语义保持不变）。
 * 背书行走共用白话单点，其余字段走通用「字段: 旧 → 新」。
 */
export function revisionRecordText(record: ItemRevisionRecordRead): string {
  if (isSourceAttestation(record)) {
    return attestationRecordText(record);
  }
  const label = REVISION_FIELD_LABELS[record.field_key] ?? record.field_key;
  return `${label}: ${record.before_value} → ${record.after_value}`;
}

/**
 * 登记来源的修订值（整集替换语义）：把候选要素并入当前来源集去重后序列化为 JSON 数组字符串。
 * 后端 _normalize_source_element_refs 会再次去重/升序/门禁校验，前端提交并集即「新增该来源」。
 */
export function buildSourceRegistrationValue(
  currentRefs: readonly string[],
  candidateRef: string,
): string {
  const union = Array.from(new Set([...currentRefs, candidateRef]));
  return JSON.stringify(union);
}

/** 是否处于「待补充来源」派生态（补充来源出口卡的渲染条件；采纳 supplement 后无站立结论卡）。 */
export function isSupplementPending(
  item: Pick<ReviewRequirementItemRead, 'display_code'>,
): boolean {
  return item.display_code === 'supplement_pending';
}

/** 诊断模式弹层选项：区2 主按钮与区5 药丸共用同一份（双入口同机制，禁第二套） */
export const DIAGNOSIS_MODE_OPTIONS: readonly DiagnosisMode[] = [
  'quick',
  'standard',
  'comprehensive',
  'incremental',
];

/** 双入口共用的发起命令文本：区5 预填、区2 直发都取这里，保证同一轮次语义 */
export function diagnosisLaunchCommand(mode: DiagnosisMode, selectedCount: number): string {
  return QUICK_COMMAND_PREFILLS.diagnose(diagnosisModeText(mode), selectedCount || undefined);
}

/**
 * 区2 灰字状态说明（从属于主按钮）：按勾选范围动态给出诊断范围事实。
 * 范围口径与后端派发一致：勾选=诊已勾选集；零勾选=只诊当前条目（AEP-095 start_diagnosis）。
 */
export function diagnosisScopeHint(selectedCount: number, selectableCount: number): string {
  if (!selectableCount) return '没有可诊断的条目';
  if (!selectedCount) return '未勾选：仅诊断当前条目，可在区1 勾选纳入范围';
  if (selectedCount >= selectableCount) return `默认诊断全部可诊断条目（${selectableCount} 条），或在区1 勾选子集`;
  return `已勾选 ${selectedCount} 条纳入本次诊断`;
}

/**
 * 灰字投影：处于「有待诊断条目、无在途运行、无待裁决结论」阶段时用动态范围说明
 * （按结构事实派生，与后端 next_action 分支同口径，不依赖文案逐字符稳定）；
 * 其余透传服务端 next_action（经显示层映射）。
 */
export function reviewRunHint(
  nextAction: string | null | undefined,
  selectedCount: number,
  selectableCount: number,
  awaitingCount: number,
  anyRunning: boolean,
): string | null {
  if (!anyRunning && !awaitingCount && selectableCount > 0) {
    return diagnosisScopeHint(selectedCount, selectableCount);
  }
  // 发起类指引在零可选时须改写为「没有可诊断的条目」：后端 selectable 口径（任一 no_verdict）
  // 比前端勾选门禁宽（缺口封锁/离线回落均计入），原样透传会指引用户勾选全被禁用的复选框。
  if (!anyRunning && !awaitingCount && (nextAction ?? '').includes('发起诊断')) {
    return diagnosisScopeHint(selectedCount, selectableCount);
  }
  return nextAction ?? null;
}

export function verdictKindText(kind?: VerdictKind | null): string {
  const map: Record<VerdictKind, string> = {
    pass: '建议通过',
    revise: '建议修订',
    withdraw: '建议撤回',
    supplement: '建议补充来源',
  };
  return kind ? map[kind] : '—';
}

/** 会话条芯片徽标：结论状态字缩写（通/修/撤/补）。 */
export function verdictKindGlyph(kind?: VerdictKind | null): string {
  const map: Record<VerdictKind, string> = { pass: '通', revise: '修', withdraw: '撤', supplement: '补' };
  return kind ? map[kind] : '';
}

/** 采纳键动词随状态字变化（v5 结论卡契约）。 */
export function adoptVerbText(kind?: VerdictKind | null): string {
  const map: Record<VerdictKind, string> = {
    pass: '采纳 · 确认该条目',
    revise: '采纳所选修订并自动重诊',
    withdraw: '采纳 · 终止该条目',
    supplement: '采纳 · 登记来源缺口',
  };
  return kind ? map[kind] : '采纳';
}

export function findingTypeText(type: ReviewFindingType): string {
  const map: Record<ReviewFindingType, string> = {
    source_inconsistency: '来源不一致',
    ambiguous_expression: '表达歧义',
    untestable: '不可测试',
    missing_field: '字段缺漏',
    no_blocker: '无阻断',
  };
  return map[type];
}

export function triggerText(trigger: DiagnosisTrigger): string {
  const map: Record<DiagnosisTrigger, string> = {
    user_submit: '用户提交',
    revision_chained: '修订后自动增量',
    dialogue_reeval: '对话轻量重评',
  };
  return map[trigger];
}

// ---- 区1 列表投影 ----

export interface ReviewItemListItemVM {
  itemRef: string;
  reqNo: string;
  expression: string;
  typeText: string;
  statusText: string;
  statusTone: BadgeTone;
  groupKey: string;
  verdictGlyph: string;
  selectedForDiagnosis: boolean;
  selectableForDiagnosis: boolean;
  checkboxDisabledReason: string | null;
  current: boolean;
  sourceCountText: string;
  statusNote: string;
}

export interface ReviewStatusGroupVM {
  key: string;
  label: string;
  items: ReviewItemListItemVM[];
}

export function mapReviewItems(
  items: ReviewRequirementItemRead[],
  selectedForDiagnosis: string[],
  currentItemRef: string | null,
): ReviewItemListItemVM[] {
  const selected = new Set(selectedForDiagnosis);
  return items.map((item) => {
    const meta = reviewDisplayMeta(item.display_code);
    const selectable = item.available_actions.some(
      (a) => a.key === 'request_diagnosis' && a.enabled,
    );
    return {
      itemRef: item.item_ref,
      reqNo: item.req_no,
      expression: item.expression,
      typeText: requirementItemTypeText(item.req_type),
      statusText: meta.label,
      statusTone: meta.tone,
      groupKey: item.display_code,
      verdictGlyph: verdictKindGlyph(item.current_verdict?.verdict_kind),
      selectedForDiagnosis: selected.has(item.item_ref),
      selectableForDiagnosis: selectable,
      checkboxDisabledReason: selectable ? null : '当前状态不可加入本次诊断范围',
      current: currentItemRef === item.item_ref,
      sourceCountText: `${item.source_element_refs.length} 个来源`,
      statusNote: item.display_note,
    };
  });
}

export function groupReviewItems(items: ReviewItemListItemVM[]): ReviewStatusGroupVM[] {
  const known = new Set<string>(REVIEW_DISPLAY_GROUPS.map((group) => group.key));
  const groups: ReviewStatusGroupVM[] = REVIEW_DISPLAY_GROUPS
    .map((group) => ({ key: group.key as string, label: group.label, items: items.filter((item) => item.groupKey === group.key) }))
    .filter((group) => group.items.length > 0);
  // 兜底组：未知 display_code 的条目归尾部「其他状态」，不消失（issue #10 债 #2）。
  const others = items.filter((item) => !known.has(item.groupKey));
  if (others.length) {
    groups.push({ key: REVIEW_DISPLAY_FALLBACK_GROUP, label: FALLBACK_DISPLAY_META.label, items: others });
  }
  return groups;
}

// ---- 会话条（线程收件箱的并行承载）----

export interface ThreadChipVM {
  itemRef: string;
  reqNo: string;
  glyph: string;          // 结论状态字缩写；空 = 无徽标
  spinning: boolean;      // 诊断中
  done: boolean;          // 已确认/已终止
  active: boolean;
}

/** 工作集芯片：与区1 分组同一注意力排序（待裁决/诊断中优先，失败/被拒次之，终态靠后）；全量保留（条目数有限）。 */
export function buildThreadStrip(
  items: ReviewRequirementItemRead[],
  currentItemRef: string | null,
): ThreadChipVM[] {
  const order = new Map<string, number>(REVIEW_DISPLAY_GROUPS.map((group, index) => [group.key, index]));
  const rank = (item: ReviewRequirementItemRead) => order.get(item.display_code) ?? 99;
  return [...items]
    .sort((a, b) => rank(a) - rank(b) || a.req_no.localeCompare(b.req_no))
    .map((item) => ({
      itemRef: item.item_ref,
      reqNo: item.req_no,
      glyph: item.review_status === 'awaiting_adjudication'
        ? verdictKindGlyph(item.current_verdict?.verdict_kind) : '',
      spinning: item.review_status === 'diagnosing',
      done: item.review_status === 'confirmed' || item.review_status === 'terminated',
      active: currentItemRef === item.item_ref,
    }));
}

/** 下一待裁决线程（分诊跳转）。 */
export function nextAwaitingItem(
  items: ReviewRequirementItemRead[], currentItemRef: string | null,
): string | null {
  const awaiting = items.filter((i) => i.review_status === 'awaiting_adjudication');
  if (!awaiting.length) return null;
  const idx = awaiting.findIndex((i) => i.item_ref === currentItemRef);
  return awaiting[(idx + 1) % awaiting.length]?.item_ref ?? null;
}

// ---- 线程时间线（单条目消息流：LDM-009 事实 + 对话消息按时间序重放）----

export type ThreadEntryVM =
  | { kind: 'system'; key: string; tone: 'info' | 'ok' | 'warn'; text: string; at: string }
  | { kind: 'verdict'; key: string; verdict: VerdictRead; standing: boolean; at: string }
  | { kind: 'receipt'; key: string; verdict: VerdictRead; at: string }  // 已裁决/已替代/失效收折
  | { kind: 'dialogue'; key: string; message: DialogueMessageRead; at: string };

export function buildThread(item: ReviewRequirementItemRead): ThreadEntryVM[] {
  const entries: ThreadEntryVM[] = [];
  const standingRef = item.current_verdict?.round_ref ?? null;

  for (const v of [...item.verdict_history].reverse()) {  // 历史旧→新
    if (v.status === 'running') {
      continue; // 在途轮次由头部/芯片承载
    }
    if (v.status === 'failed') {
      entries.push({
        kind: 'system', key: `fail-${v.round_ref}`, tone: 'warn',
        text: v.reason ?? '诊断未完成，可重试。', at: v.created_at,
      });
      continue;
    }
    entries.push({ kind: 'receipt', key: `rcpt-${v.round_ref}`, verdict: v, at: v.created_at });
  }
  if (item.current_verdict && standingRef) {
    entries.push({
      kind: 'verdict', key: `verdict-${standingRef}`,
      verdict: item.current_verdict, standing: true, at: item.current_verdict.created_at,
    });
  }
  for (const m of item.dialogue_messages) {
    entries.push({ kind: 'dialogue', key: `dlg-${m.message_ref}`, message: m, at: m.created_at });
  }
  entries.sort((a, b) => (a.at || '').localeCompare(b.at || ''));
  return entries;
}

/** 收折回执一行文案（GitHub resolvable conversations 形态）。 */
export function receiptText(v: VerdictRead): { mark: string; tone: BadgeTone; text: string } {
  const kind = verdictKindText(v.verdict_kind);
  if (v.overridden) {
    return { mark: '⊘', tone: 'warning', text: `${kind} · 被覆盖确认（理由留痕）` };
  }
  if (v.adjudication?.decision === 'adopted') {
    const sel = v.adjudication.selected_point_refs.length;
    const exc = v.adjudication.excluded_point_refs.length;
    // 「排除」这个动作词界面上已经没有了（只剩「标为不是问题」）；「已自动增量重诊」也不是
    // 这里能断言的事（是否续接由服务端定），故不再无条件许诺。
    const detail = v.verdict_kind === 'revise'
      ? `（采纳 ${sel}/${sel + exc} 点${exc ? `，${exc} 点因问题被标为不是问题未采纳` : ''}）`
      : v.verdict_kind === 'pass' ? ' → 条目已确认'
      : v.verdict_kind === 'withdraw' ? ' → 条目已终止'
      : ' → 来源缺口已登记';
    return { mark: '✓', tone: 'success', text: `已采纳 · ${kind}${detail}` };
  }
  if (v.adjudication?.decision === 'rejected') {
    // C7：否决消解后的「直接确认」在库里同样按拒绝收口（AI 建议确实没被采纳），但用户刚做的
    // 是确认，不是拒绝。靠 confirm_result 区分这两者，否则回执给他打红叉写「已拒绝」。
    if (v.confirm_result === 'confirmed') {
      return {
        mark: '✓', tone: 'success',
        text: `已确认 · ${kind} · 本轮问题经你逐条裁定后没有剩下`,
      };
    }
    return { mark: '✗', tone: 'danger', text: `已拒绝 · ${kind} · 理由：${v.adjudication.reason ?? ''}` };
  }
  if (v.superseded_by) {
    return { mark: '↻', tone: 'neutral', text: `已替代 · ${kind}（对话重评产出新结论）` };
  }
  if (v.invalidated) {
    return { mark: '↻', tone: 'neutral', text: `已失效 · ${kind}（${v.invalidated_reason ?? '条目已修订'}）` };
  }
  return { mark: '·', tone: 'neutral', text: kind };
}

// ---- 动态流（全局只读事件流）----

export interface FeedLineVM {
  key: string;
  at: string;
  reqNo: string;
  itemRef: string;
  text: string;
}

export function buildActivityFeed(workspace: ItemReviewWorkspaceRead): FeedLineVM[] {
  const lines: FeedLineVM[] = [];
  for (const item of workspace.review_items) {
    const all = item.current_verdict
      ? [...item.verdict_history, item.current_verdict]
      : item.verdict_history;
    for (const v of all) {
      if (v.status === 'running') {
        lines.push({
          key: `run-${v.round_ref}`, at: v.created_at, reqNo: item.req_no, itemRef: item.item_ref,
          text: `诊断进行中（${diagnosisModeText(v.diagnosis_mode)} · ${triggerText(v.trigger)}）`,
        });
        continue;
      }
      if (v.status === 'failed') {
        lines.push({
          key: `fail-${v.round_ref}`, at: v.created_at, reqNo: item.req_no, itemRef: item.item_ref,
          text: `诊断未完成：${v.reason ?? ''}`,
        });
        continue;
      }
      lines.push({
        key: `mint-${v.round_ref}`, at: v.created_at, reqNo: item.req_no, itemRef: item.item_ref,
        text: `结论：${verdictKindText(v.verdict_kind)}${v.effective ? '（待裁决）' : ''}`,
      });
      if (v.adjudication) {
        lines.push({
          key: `adj-${v.round_ref}`, at: v.adjudication.at, reqNo: item.req_no, itemRef: item.item_ref,
          text: receiptText(v).text,
        });
      }
    }
    for (const m of item.dialogue_messages) {
      lines.push({
        key: `dlg-${m.message_ref}`, at: m.created_at, reqNo: item.req_no, itemRef: item.item_ref,
        text: m.kind === 'draft'
          ? `修订草案 D${m.draft_seq ?? 1} 产出${m.in_flight ? '(在途)' : ''}`
          : '解释消息（不改结论）',
      });
    }
  }
  lines.sort((a, b) => (a.at || '').localeCompare(b.at || ''));
  return lines;
}

// ---- 诊断批次进度与 run 级完成反馈（消费 AEP-033 diagnosis_runs 现成字段，不新增端点）----

export interface DiagnosisRunProgressVM {
  completed: number;
  total: number;
  /** 进度填充宽度（百分数；分母以 max(总数, 已处理) 收口防溢出） */
  pct: number;
  /** 只给分数（UI 白话纪律）：已处理 n/N（后端 completed_count=终态轮次数，含失败轮，故不称「已出结论」） */
  countsText: string;
}

/** 在途批次确定型进度：分母=发起时捕获的 total_count，分子=completed_count；多批并行时求和；无在途批次 → null（进度收敛不残留）。 */
export function deriveDiagnosisRunProgress(
  runs: DiagnosisRunProgressRead[],
): DiagnosisRunProgressVM | null {
  const running = runs.filter((run) => run.status === 'running');
  if (!running.length) {
    return null;
  }
  const completed = running.reduce((sum, run) => sum + run.completed_count, 0);
  const total = running.reduce((sum, run) => sum + run.total_count, 0);
  const base = Math.max(total, completed, 1);
  return {
    completed,
    total,
    pct: Math.round((completed / base) * 100),
    countsText: `已处理 ${completed}/${total}`,
  };
}

export interface RunFailureToastVM {
  runRef: string;
  failedCount: number;
}

/**
 * run 级聚合失败反馈（issue #10 B2b 重建于 run 级事实）：只在观察到 running→completed 迁移时
 * 结算一次（同 run 去重靠状态迁移本身），失败条数**直取后端 per-run 事实 `failed_count`**——
 * 弃用旧启发式（以条目全局最新态猜测本 run 失败）。旧法三归因错位场景随之消亡：
 *  - 结算窗口内新批重诊：run.failed_count 批次收束后稳定，不因条目被 run B 成功重诊而漏报为 0；
 *  - 迁移被消费永不再发：失败事实固化在本 run，条目迁移/消费不改 failed_count；
 *  - 跨批遗留误计：failed_count 只计本批成员轮次，遗留失败不跨批渗入。
 * 无失败不弹；逐条目细节不进 toast，归线程与链路回执条。
 */
export function collectRunFailureToasts(
  prevStatus: ReadonlyMap<string, string>,
  runs: DiagnosisRunProgressRead[],
): { toasts: RunFailureToastVM[]; nextStatus: Map<string, string> } {
  const toasts: RunFailureToastVM[] = [];
  const nextStatus = new Map<string, string>();
  for (const run of runs) {
    nextStatus.set(run.run_ref, run.status);
    if (prevStatus.get(run.run_ref) !== 'running' || run.status !== 'completed') {
      continue;
    }
    const failedCount = run.failed_count;
    if (failedCount > 0) {
      toasts.push({ runRef: run.run_ref, failedCount });
    }
  }
  return { toasts, nextStatus };
}

// ---- 修订点合成预览（与后端 domain/revision_points.compose 同规则）----

export function composeSelectedPoints(
  base: string,
  points: Array<{ point_ref: string; find: string; replace: string }>,
  selectedRefs: Set<string>,
): string {
  const located = points
    .filter((p) => selectedRefs.has(p.point_ref))
    .map((p) => ({ idx: base.indexOf(p.find), p }))
    .filter((e) => e.idx >= 0)
    .sort((a, b) => b.idx - a.idx);
  let out = base;
  for (const { idx, p } of located) {
    out = out.slice(0, idx) + p.replace + out.slice(idx + p.find.length);
  }
  return out;
}

/**
 * 结论卡的一个问题块：一条诊断问题，连同 AI 为它给出的改法（可能没有，也可能不止一个）。
 *
 * 为什么以问题为单元：改法是问题的从属物，不是与问题并列的另一件事。此前卡上把发现项与
 * 修订点排成一个列表、还挂同款类型标签，用户读到的就是「三个问题」，而后端其实只报了两个
 * 问题一个改法（2026-07-20 用户走查 REQ-003 提出）。
 */
export interface VerdictProblemVM {
  findingRef: string;
  findingType: ReviewFindingType;
  summary: string;
  basis: string;
  ruleCode?: string | null;
  evidenceSpan?: string | null;
  severity: string;
  /** AI 为这个问题给的改法；空数组＝只报了问题没给改法 */
  fixes: RevisionPointRead[];
  vetoed: boolean;
  vetoRef?: string | null;
  vetoReason?: string | null;
  canVeto: boolean;
  /**
   * 条目已有人工确认来源，这条来源对齐类发现降为非阻断提示（后端读投影给的事实，前端不自算）。
   *
   * S3：结论卡的渲染分区不读它——问题与提示按 `problems` / `attestedNotices` 两个数组归属
   * 区分，所以它在生产渲染代码里没有读取点。保留的理由是这条 VM 会被别处消费（区4 计数、
   * 测试断言）时需要「这一条是不是降格来的」这个事实，删掉就得让消费方回头去猜数组归属。
   */
  sourceAttested: boolean;
}

/**
 * 把一轮结论拆成逐问题的块（结论卡唯一列表单元）。
 *
 * - 改法按 finding_ref 归到它所针对的问题；存量轮次元数据没有引用，才回退按 finding_index
 *   配对（读出序与模型输出序不是一回事，恒用下标会把改法挂到别的问题上）。
 * - `no_blocker`（「未发现阻断问题」）不是问题，不进列表——它属于结论摘要。
 * - 后端标了 source_attested 的来源对齐类发现同样不进问题列表，改由 attestedNotices 单列：
 *   条目的来源缺口已由人工确认闭合，它不再是要用户处理的问题，混在问题列表里会让
 *   「发现 N 个问题」这个数字与后端的阻断计数对不上（本仓纪律：计数须与用户可见输入自洽）。
 * - 归不到任何问题的改法（引用悬空）单独返回，由调用方如实呈现，不静默丢弃。
 */
export function buildVerdictProblems(verdict: {
  findings: ReviewFindingRead[];
  revision_points: RevisionPointRead[];
}): {
  problems: VerdictProblemVM[];
  orphanFixes: RevisionPointRead[];
  attestedNotices: VerdictProblemVM[];
} {
  const blocking = verdict.findings.filter(
    (f) => f.finding_type !== 'no_blocker' && f.source_attested !== true,
  );
  // S5：两个分区的判据要对称。`blocking` 排除了 no_blocker，`attested` 此前只筛
  // source_attested，一条两者皆真的发现项会被渲染成一条写着「未发现阻断问题」的提示。
  // 当前不可达（后端只给 source_inconsistency 打降格标记），补上是健壮性。
  const attested = verdict.findings.filter(
    (f) => f.source_attested === true && f.finding_type !== 'no_blocker',
  );
  // 配对表覆盖问题与降格提示两类：改法挂在降格提示上时若不入表，就会被当成「对应不到任何
  // 问题」的悬空改法报给用户，而它其实有明确归属，只是那条已不需要处理。
  const byRef = new Map(
    [...blocking, ...attested].map((f) => [f.finding_ref, [] as RevisionPointRead[]]),
  );
  const orphanFixes: RevisionPointRead[] = [];
  for (const point of verdict.revision_points) {
    const target = point.finding_ref
      ? point.finding_ref
      : verdict.findings[point.finding_index]?.finding_ref;
    const bucket = target ? byRef.get(target) : undefined;
    if (bucket) bucket.push(point);
    else orphanFixes.push(point);
  }
  const toVM = (f: ReviewFindingRead): VerdictProblemVM => ({
    findingRef: f.finding_ref,
    findingType: f.finding_type,
    summary: f.diagnosis_summary,
    basis: f.basis_summary,
    ruleCode: f.rule_code,
    evidenceSpan: f.evidence_span,
    severity: f.severity ?? 'medium',
    fixes: byRef.get(f.finding_ref) ?? [],
    vetoed: f.vetoed === true,
    vetoRef: f.veto_ref,
    vetoReason: f.veto_reason,
    canVeto: f.can_veto === true,
    sourceAttested: f.source_attested === true,
  });
  return {
    problems: blocking.map(toVM),
    orphanFixes,
    attestedNotices: attested.map(toVM),
  };
}

/** 用户改过稿再采纳的建议：AI 原案与实际应用稿并排一行（区4 留痕卡素材）。 */
export interface EditedPointTrailRow {
  key: string;
  roundNo: number;
  label: string;
  aiText: string;
  userText: string;
}

/**
 * 从条目的全部轮次里收集「改过稿再采纳」的点（新→旧）。
 *
 * AI 原案取自那一轮的 revision_points（该列不可变，永远是模型当时给的原话），
 * 用户终稿取自该轮裁决的 point_edits——两者分别落在不同的地方，所以都不会被对方覆盖。
 * 没有 point_edits 的轮次不产生行：用户没改稿，采纳的就是 AI 原案，无须并排展示。
 */
export function collectEditedPointTrail(item: {
  current_verdict?: VerdictRead | null;
  verdict_history: VerdictRead[];
}): EditedPointTrailRow[] {
  const rounds = [item.current_verdict, ...item.verdict_history].filter(
    (v): v is VerdictRead => v != null,
  );
  const rows: EditedPointTrailRow[] = [];
  for (const round of rounds) {
    const edits = round.adjudication?.point_edits;
    if (!edits) continue;
    for (const [pointRef, userText] of Object.entries(edits)) {
      const point = round.revision_points.find((p) => p.point_ref === pointRef);
      rows.push({
        key: `${round.round_ref}:${pointRef}`,
        roundNo: round.round_no,
        label: point?.label ?? pointRef,
        aiText: point?.replace ?? '（该轮未留下原案）',
        userText,
      });
    }
  }
  return rows.sort((a, b) => b.roundNo - a.roundNo);
}

// ---- 后端不可达时的初始投影（待诊断；不伪造诊断/结论）----

export function buildInitialReviewWorkspace(
  source: ItemFormationWorkspaceRead,
): ItemReviewWorkspaceRead {
  const sourceElements = [...source.eligible_elements, ...source.blocked_elements];
  const reviewItems = source.pending_items.map<ReviewRequirementItemRead>((item) => ({
    item_ref: item.item_ref,
    req_no: item.req_no,
    expression: item.expression,
    req_type: item.req_type,
    status: item.status,
    version_no: String(item.version_no ?? 1),
    source_element_refs: item.source_element_refs,
    formation_basis_ref: item.formation_basis_ref,
    verification_method: item.verification_method ?? [],
    verification_note: item.verification_note ?? null,
    priority: item.priority ?? null,
    revision_records: (item.revision_records ?? []).map((r: Record<string, unknown>) => ({
      record_ref: String(r.record_ref ?? ''),
      field_key: String(r.field_key ?? 'expression'),
      before_value: String(r.before_value ?? ''),
      after_value: String(r.after_value ?? ''),
      revision_mode: String(r.revision_mode ?? 'manual'),
      selected_point_refs: [],
      operator_ref: String(r.operator_ref ?? ''),
      reason: (r.reason as string | null) ?? null,
      created_at: String(r.created_at ?? ''),
    })),
    review_status: item.status === 'confirmed' ? 'confirmed' : 'no_verdict',
    status_note: item.status === 'confirmed' ? '条目已确认。' : '尚未形成当前版本有效结论。',
    // 后端不可达的初始投影：仅二态（已确认/待诊断），不伪造诊断/失败/缺口显示态。
    display_code: item.status === 'confirmed' ? 'confirmed' : 'pending_diagnosis',
    display_note: item.status === 'confirmed' ? '条目已确认。' : '尚未发起过诊断。',
    current_verdict: null,
    verdict_history: [],
    dialogue_messages: [],
    supplement_gaps_open: [],
    available_actions: [],
  }));

  return {
    review_context_ref: source.formation_context_ref,
    formation_context_ref: source.formation_context_ref,
    workspace_version: source.workspace_version,
    material_canvas: source.material_canvas ?? {
      material_ref: 'unknown-material',
      title: '当前材料',
      source_note: null,
      raw_text: '',
      source_version: 1,
      blocks: [],
      supplements: [],
    },
    source_elements: sourceElements,
    review_items: reviewItems,
    diagnosis_options: ['quick', 'standard', 'comprehensive', 'incremental'],
    diagnosis_runs: [],
    available_operations: [
      { key: 'start_diagnosis', enabled: reviewItems.some((i) => i.review_status === 'no_verdict'), disabled_reason: null },
      { key: 'refresh_review_view', enabled: true, disabled_reason: null },
      { key: 'back_to_maintenance', enabled: true, disabled_reason: null },
    ],
    confirmed_count: reviewItems.filter((i) => i.review_status === 'confirmed').length,
    total_count: reviewItems.length,
    next_action: '勾选可诊断条目后发起诊断。',
  };
}

// ---- 区3 来源画布（沿用形成管线只读画布）----

export function resolveReviewAnchors(workspace: ItemReviewWorkspaceRead): Map<string, ResolvedAnchor> {
  const map = new Map<string, ResolvedAnchor>();
  const canvas = workspace.material_canvas;
  for (const element of workspace.source_elements) {
    map.set(element.id, resolveAnchor(element.source_anchor, canvas.material_ref, canvas.raw_text));
  }
  return map;
}

export function buildReviewSourceCanvas(
  workspace: ItemReviewWorkspaceRead,
  selectedItem: ReviewRequirementItemRead | null,
  anchors: Map<string, ResolvedAnchor>,
): CanvasBlockVM[] {
  const sourceRefs = new Set(selectedItem?.source_element_refs ?? []);
  const highlights: ElementHighlight[] = [];
  for (const element of workspace.source_elements) {
    const anchor = anchors.get(element.id);
    if (!anchor?.ranges.length) {
      continue;
    }
    highlights.push({
      elementId: element.id,
      typeColorKey: sourceRefs.has(element.id) ? 'interface' : elementTypeMeta(element.element_type).colorKey,
      processStatus: element.process_status,
      ranges: anchor.ranges,
    });
  }
  const blocks: MaterialTextBlockRead[] = workspace.material_canvas.blocks ?? [];
  return fillSegmentText(blocks, buildCanvasBlocks(blocks, highlights));
}

// ---- 区3 拖选指定来源 ----

/** 区3 里选中的一段原文（坐标＝语料绝对字符位置，与知识项锚点同一把尺）。 */
export interface CanvasTextSelection {
  start: number;
  end: number;
  text: string;
}

/** 拖选命中的一条知识项：能不能登记为来源，以及不能的话为什么。 */
export interface SelectionHit {
  elementRef: string;
  content: string;
  typeLabel: string;
  typeColorKey: string;
  /** 已确认的知识项才能登记为来源（后端 _normalize_source_element_refs 的门禁） */
  registrable: boolean;
  blockedReason: string | null;
}

/**
 * 选区落在哪些知识项上。
 *
 * 判据是区间相交：片段 [seg.start, seg.end) 与选区 [sel.start, sel.end) 有重叠，
 * 该片段覆盖的知识项就算命中。片段坐标由 buildCanvasBlocks 从知识项锚点算出，与选区坐标
 * 同为语料绝对位置，所以这里是纯粹的数值比较——**不做任何文本匹配、不做近似**。
 *
 * 返回按知识项去重，保持首次出现的先后（即在正文里从前往后的顺序）。
 */
export function findSelectionHits(
  blocks: CanvasBlockVM[],
  selection: CanvasTextSelection,
  elementsById: Map<string, FormationElementRead>,
): SelectionHit[] {
  const seen = new Set<string>();
  const hits: SelectionHit[] = [];
  for (const block of blocks) {
    for (const seg of block.segments) {
      if (seg.end <= selection.start || seg.start >= selection.end) {
        continue;  // 不相交
      }
      for (const ref of seg.refs) {
        if (seen.has(ref)) {
          continue;
        }
        seen.add(ref);
        const element = elementsById.get(ref);
        if (!element) {
          continue;  // 画布里有高亮但工作区里查不到这条：跳过，不猜
        }
        const meta = elementTypeMeta(element.element_type);
        const confirmed = element.process_status === 'confirmed';
        hits.push({
          elementRef: ref,
          content: element.content,
          typeLabel: meta.label,
          typeColorKey: meta.colorKey,
          registrable: confirmed,
          blockedReason: confirmed ? null : '这条知识项还没确认，确认后才能登记为来源',
        });
      }
    }
  }
  return hits;
}

export function mapReviewSourceElementsById(workspace: ItemReviewWorkspaceRead): Map<string, FormationElementRead> {
  return new Map(workspace.source_elements.map((element) => [element.id, element]));
}
