import type { AssetCatalogRead, ItemMaintenanceCardRead, ItemMaintenanceItemRead } from '../api/assets';
import type { AiEffectivenessRead } from '../api/ai-effectiveness';
import type { ActionVM, BadgeTone, MetricCardVM, StatusSummaryVM } from './common';
import {
  priorityText,
  requirementItemStatusMeta,
  requirementItemTypeText,
  verificationMethodText,
} from './requirement-item-formation';
import {
  SOURCE_ATTESTATION_LABEL,
  isSourceAttestation,
} from './requirement-item-review';
import type { ItemFormationWorkspaceRead, RequirementItemType } from '../api/item-formation';
import type { RequirementFlowRead } from '../api/overview';
import { formatAbsoluteMinute } from './time';

// ---- 维护列表（04A §3.1 默认视图；数据来自资产读侧 AEP，本文件只做展示投影）----

export interface FilterOptionVM {
  key: string;
  label: string;
}

/** 状态/类型筛选是稳定视图配置（稳定码=后端契约）。 */
export const MAINTENANCE_STATUS_FILTERS: FilterOptionVM[] = [
  { key: 'all', label: '全部' },
  { key: 'pending_confirmation', label: '待确认' },
  { key: 'confirmed', label: '已确认' },
];

export const MAINTENANCE_TYPE_FILTERS: FilterOptionVM[] = [
  { key: 'all', label: '全部' },
  { key: 'functional', label: '功能' },
  { key: 'quality', label: '质量属性' },
  { key: 'constraint', label: '约束' },
  { key: 'data', label: '数据' },
  { key: 'interface', label: '接口' },
];

/** 缺失警示筛选（29148 属性补齐）：缺失仅警示不硬卡，此筛选是评审补充回路的工作面。 */
export const MAINTENANCE_GAP_FILTERS: FilterOptionVM[] = [
  { key: 'all', label: '全部' },
  { key: 'verification_note', label: '缺验收准则' },
  { key: 'priority', label: '缺优先级' },
];

// ---- v2 KPI 仪表带（04 篇 §3.1；有数据源接真端点，缺源标 deferred，不显假数）----

export type KpiTone = 'blue' | 'green' | 'red' | 'amber' | 'teal' | 'indigo';

export interface KpiGaugeVM {
  key: string;
  label: string;
  icon: string; // rmv2 线性图标名（RmIcon）
  value: string; // 主数值（deferred 时为 '—'）
  unit?: string;
  sub: string; // 副说明
  tone: KpiTone;
  deferred?: boolean; // 缺后端来源 → DeferredBadge，不显假数
  track?: number; // 0–100 进度条（可选）
  trackRest?: boolean; // 进度条补齐剩余段（基准件确认率形态）
}

/** 从 catalog（quality_alert_summary/trace_summary）+ 条目列表 + AI 效能（AEP-094，可空）拼装 6 项 KPI。 */
export function buildKpiBand(
  catalog: AssetCatalogRead | null,
  items: ItemMaintenanceItemRead[],
  ai?: AiEffectivenessRead | null,
): KpiGaugeVM[] {
  const total = items.length;
  const confirmed = items.filter((i) => i.status === 'confirmed').length;
  const pending = items.filter((i) => i.status === 'pending_confirmation').length;
  const functional = items.filter((i) => i.req_type === 'functional').length;
  const confirmRate = total ? Math.round((confirmed / total) * 100) : 0;
  const verMissing = items.filter((i) => i.verification_missing).length;
  const priMissing = items.filter((i) => i.priority_missing).length;
  const qa = catalog?.quality_alert_summary;
  const trace = catalog?.trace_summary;
  // 追溯覆盖率 = 有效 / 全部关系（有效+预建立+可疑+失效）；无关系时不显百分比
  const traceTotal = trace ? trace.effective + trace.pre_established + trace.suspect + trace.invalid : 0;
  const traceRate = trace && traceTotal > 0 ? Math.round((trace.effective / traceTotal) * 100) : null;
  // AI 处理量 = 各环节收口明细总量；采纳率 =（采纳+修订采纳）/ 收口总量
  const aiStages = ai?.stages ?? [];
  const aiTotal = ai ? aiStages.reduce((sum, s) => sum + s.total, 0) : null;
  const aiAdopted = ai ? aiStages.reduce((sum, s) => sum + s.adopted + s.adopted_with_revision, 0) : 0;
  const aiRate = ai && aiTotal ? Math.round((aiAdopted / aiTotal) * 100) : null;

  return [
    {
      key: 'items', label: '需求条目', icon: 'manage', value: String(total),
      sub: `功能 ${functional} · 待确认 ${pending}`, tone: 'blue',
    },
    {
      key: 'confirm_rate', label: '确认率', icon: 'check', value: String(confirmRate), unit: '%',
      sub: `确认 ${confirmed} · 待确认 ${pending}`, tone: 'green', track: confirmRate, trackRest: true,
    },
    {
      key: 'quality_alert', label: '质量告警', icon: 'alert',
      value: qa ? String(qa.high + qa.medium + qa.low) : '—',
      sub: qa
        ? `高 ${qa.high} · 中 ${qa.medium} · 低 ${qa.low}（已诊断 ${qa.diagnosed_items}）`
        : '待接入',
      tone: 'red', deferred: !qa,
    },
    {
      key: 'completeness_gap', label: '完备缺口', icon: 'warn', value: String(verMissing + priMissing),
      sub: `缺判据 ${verMissing} · 缺优先级 ${priMissing}`, tone: 'amber',
    },
    {
      key: 'trace_coverage', label: '追溯覆盖', icon: 'trace',
      value: traceRate != null ? String(traceRate) : trace ? String(trace.effective) : '—',
      unit: traceRate != null ? '%' : undefined,
      sub: trace ? `有效 ${trace.effective} · 可疑 ${trace.suspect}` : '待接入',
      tone: 'teal', deferred: !trace,
      track: traceRate ?? undefined,
    },
    // AI 处理量：接 AEP-094 效能统计（近 N 日收口明细）；拉取失败回落待接入
    {
      key: 'ai_throughput', label: 'AI 处理量', icon: 'spark',
      value: aiTotal != null ? aiTotal.toLocaleString('en-US') : '—',
      sub: ai && aiTotal != null ? `近 ${ai.window_days} 日${aiRate != null ? ` · 采纳率 ${aiRate}%` : ''}` : '待接入',
      tone: 'indigo', deferred: !ai,
    },
  ];
}

// ---- 需求卡片（选中条目详情） ----

export interface RequirementFactVM {
  key: string;
  label: string;
  value: string;
  /** 负态（未设定/未建议/0 条）弱化呈现，不当告警扎眼 */
  muted?: boolean;
  /** 值前色点（如优先级档位色） */
  dot?: string;
  /** 详情卡分组：条目自身写了什么 vs 这条在系统里的登记情况（三页同一口径） */
  group: RequirementFactGroup;
}

/** 详情卡两组分区（走查反馈第⑥组，条目形成／条目评审／需求管理三页同名同序） */
export type RequirementFactGroup = 'content' | 'registry';

export const REQUIREMENT_FACT_GROUPS: { key: RequirementFactGroup; label: string }[] = [
  { key: 'content', label: '条目内容' },
  { key: 'registry', label: '登记信息' },
];

export interface RequirementGateVM {
  title: string;
  nextActionLabel: string;
  readinessItems: StatusSummaryVM[];
}

export interface RequirementSourceEvidenceVM {
  key: string;
  label: string;
  excerpt: string;
}

export interface RequirementRevisionVM {
  key: string;
  timeText: string;
  /** 人工确认背书行：渲染走白话，不显示 before→after、不标「人工修订」。 */
  isAttestation: boolean;
  fieldText: string;
  beforeText: string;
  afterText: string;
  modeText: string;
  operatorText: string;
}

export interface RequirementRelationActionVM extends ActionVM {
  targetWorkbench?: 'traceability' | 'diagram' | 'release';
}

export interface RequirementCardVM {
  ref: string;
  id: string;
  statusText: string;
  statusTone: BadgeTone;
  typeText: string;
  statement: string;
  facts: RequirementFactVM[];
  gate: RequirementGateVM;
  sourceEvidence: RequirementSourceEvidenceVM[];
  revisions: RequirementRevisionVM[];
  impactMetrics: MetricCardVM[];
  relationActions: RequirementRelationActionVM[];
  /** 追溯关系计数（有效+可疑，trace 页签角标） */
  traceCount: number;
  verificationMissing: boolean;
}

const REVISION_MODE_LABELS: Record<string, string> = {
  manual: '人工修订',
  accept_suggestion: '采纳建议',
  revise_and_accept_suggestion: '修订采纳',
  reject_suggestion: '拒绝建议',
};

const FIELD_KEY_LABELS: Record<string, string> = {
  expression: '需求表达',
  req_type: '语义类型',
  curation_note: '整理说明',
  boundary_note: '边界说明',
  verification_method: '验证方式',
  verification_note: '验收准则',
  priority: '优先级',
};

export function buildRequirementCardVM(card: ItemMaintenanceCardRead): RequirementCardVM {
  const status = requirementItemStatusMeta(card.status);
  const pending = card.status === 'pending_confirmation';
  const sourceEvidence = card.source_evidence ?? [];
  const revisions = card.revisions ?? [];
  return {
    ref: card.ref,
    id: card.req_no,
    statusText: status.label,
    statusTone: status.tone,
    typeText: requirementItemTypeText(card.req_type as RequirementItemType),
    statement: card.expression,
    // 组内顺序与条目形成页、条目评审页对齐：内容组＝类型→验证方式→验收准则→优先级，
    // 登记组＝状态→来源要素→时间（本页无版本与形成依据，缺的不占位）。
    facts: [
      {
        key: 'type', label: '语义类型', group: 'content',
        value: requirementItemTypeText(card.req_type as RequirementItemType),
      },
      {
        key: 'verification', label: '验证方式', group: 'content',
        value: verificationMethodText(card.verification_method) ?? '未建议',
        muted: !card.verification_method,
      },
      {
        key: 'priority', label: '优先级', group: 'content', value: priorityText(card.priority) ?? '未设定',
        muted: !card.priority,
        dot: card.priority === 'high' ? 'var(--red)' : card.priority === 'medium' ? 'var(--amber)' : card.priority === 'low' ? 'var(--ink-4)' : undefined,
      },
      { key: 'status', label: '维护状态', group: 'registry', value: status.label },
      {
        key: 'sources', label: '来源要素', group: 'registry',
        value: `${sourceEvidence.length} 条`, muted: sourceEvidence.length === 0,
      },
      { key: 'updated', label: '最近更新', group: 'registry', value: formatAbsoluteMinute(card.updated_at) },
    ],
    gate: {
      title: '状态门禁',
      nextActionLabel: pending ? '评审确认' : '创建图表 / 去发布编排',
      readinessItems: [
        {
          key: 'source',
          label: '来源依据',
          value: sourceEvidence.length > 0 ? `${sourceEvidence.length} 条完整` : '缺失',
          tone: sourceEvidence.length > 0 ? 'success' : 'warning',
        },
        {
          key: 'verification',
          label: '验收准则',
          value: card.verification_note ? '已归纳' : '缺失（仅警示，不阻断）',
          tone: card.verification_note ? 'success' : 'warning',
        },
        {
          key: 'priority',
          label: '优先级',
          value: card.priority ? (priorityText(card.priority) ?? card.priority) : '未设定（仅警示）',
          tone: card.priority ? 'success' : 'warning',
        },
        {
          key: 'next',
          label: pending ? '承接入口' : '下游承接',
          value: pending
            ? '形成管线·条目评审阶段'
            : `图表覆盖 ${card.related.charts} · 文档收录 ${card.related.documents > 0 ? '是' : '否'}`,
          tone: pending ? 'processing' : card.related.charts > 0 ? 'success' : 'neutral',
        },
      ],
    },
    sourceEvidence: sourceEvidence.map((s, index) => ({
      key: s.element_ref || String(index),
      label: s.material_label ? `${s.material_label}` : '来源要素',
      excerpt: s.content,
    })),
    revisions: revisions.map((r, index) => {
      const attestation = isSourceAttestation(r);
      return {
        key: `${r.created_at}-${index}`,
        timeText: formatAbsoluteMinute(r.created_at),
        isAttestation: attestation,
        // 背书行：字段列显白话「人工确认」、不显示改前值、方式列不标「人工修订」。
        fieldText: attestation ? SOURCE_ATTESTATION_LABEL : (FIELD_KEY_LABELS[r.field_key] ?? r.field_key),
        beforeText: attestation ? '' : r.before_value,
        afterText: r.after_value,
        modeText: attestation ? SOURCE_ATTESTATION_LABEL : (REVISION_MODE_LABELS[r.revision_mode] ?? r.revision_mode),
        operatorText: r.operator_ref,
      };
    }),
    impactMetrics: [
      {
        key: 'charts',
        title: '关联图表',
        value: String(card.related.charts),
        helperText: card.related.charts > 0 ? '可去图表核对' : '尚无覆盖',
        tone: card.related.charts > 0 ? 'processing' : 'neutral',
      },
      {
        key: 'documents',
        title: '文档收录',
        value: card.related.documents > 0 ? '已收录' : '未收录',
        helperText: '发布索引编排',
        tone: card.related.documents > 0 ? 'success' : 'neutral',
      },
      {
        key: 'trace',
        title: '追溯关系',
        value: `有效 ${card.related.trace_effective}`,
        helperText: card.related.trace_suspect > 0 ? `可疑 ${card.related.trace_suspect}` : '无可疑',
        tone: card.related.trace_suspect > 0 ? 'warning' : 'success',
      },
    ],
    relationActions: [
      { key: 'traceability', label: '追溯', iconKey: 'traceability', targetWorkbench: 'traceability' },
      { key: 'diagram', label: '图表', iconKey: 'diagram', targetWorkbench: 'diagram' },
      { key: 'release', label: '发布', iconKey: 'release', targetWorkbench: 'release' },
    ],
    traceCount: card.related.trace_effective + card.related.trace_suspect,
    verificationMissing: !card.verification_note,
  };
}

// ---- 创建/变更流程视图（五区骨架文案 = 稳定视图配置）----

export interface FormationFlowZoneVM {
  title: string;
  body: string;
}

export interface FormationFlowVM {
  stageText: string;
  steps: StatusSummaryVM[];
  inputListVM: FormationFlowZoneVM;
  toolbarVM: FormationFlowZoneVM;
  sourceCanvasVM: FormationFlowZoneVM;
  detailEvidenceVM: FormationFlowZoneVM;
  outputListVM: FormationFlowZoneVM;
}

export type RequirementManagementViewMode = 'maintenance' | 'creationFlow';

export interface RequirementCreationFlowViewVM {
  title: string;
  description: string;
  flow: FormationFlowVM;
  returnAction: ActionVM;
}

export interface RequirementManagementWorkbenchVM {
  viewMode: RequirementManagementViewMode;
  creationFlow: RequirementCreationFlowViewVM;
}

// ---- 台内评审入口（issue #5：维护视图「去评审/评审确认」切台内条目评审 Flow，不走跨台导航）----

/** 台内评审入口（一次性消费：token 范式与检索深链/预填一致）。 */
export interface ReviewFlowEntry {
  token: number;
  workspace: ItemFormationWorkspaceRead;
}

/** 形成批次候选：流程事实（AEP-072 读投影，近更优先）中带形成上下文的批次引用，保序去重。 */
export function reviewBatchCandidates(flows: RequirementFlowRead[]): string[] {
  const seen = new Set<string>();
  const refs: string[] = [];
  for (const flow of flows) {
    const ref = flow.formation_context_ref;
    if (ref && !seen.has(ref)) {
      seen.add(ref);
      refs.push(ref);
    }
  }
  return refs;
}

/** 批次是否包含指定条目（pending_items 覆盖批次全部条目，含已确认/已终止）。 */
export function formationWorkspaceContainsItem(
  workspace: ItemFormationWorkspaceRead,
  itemRef: string,
): boolean {
  return workspace.pending_items.some((item) => item.item_ref === itemRef);
}

/**
 * 聚焦目标条目：评审 Flow 的初始激活线程恒取 review_items[0]（后端刷新保留仍存在的激活项），
 * 把所选条目提到 pending_items 首位使其成为激活线程；区1 分组按状态重排，首位提升不破坏分组。
 */
export function focusFormationWorkspaceOnItem(
  workspace: ItemFormationWorkspaceRead,
  itemRef: string,
): ItemFormationWorkspaceRead {
  const target = workspace.pending_items.find((item) => item.item_ref === itemRef);
  if (!target) return workspace;
  return {
    ...workspace,
    selected_item_ref: itemRef,
    pending_items: [target, ...workspace.pending_items.filter((item) => item.item_ref !== itemRef)],
  };
}

// ---- 终结态行「恢复」＝预填重提（OVW-001 修订 2026-07-10，位置修正同日）----

/** 总览终结态行「恢复」深链：携旧接入上下文提交内容进材料接入表单；提交仍走 AEP-001（新流程）。 */
export interface IntakePrefillTarget {
  /** 一次性消费令牌（与检索深链同范式：token+projectId 双守卫）。 */
  token: number;
  projectId: string;
  /** 流程根 id（AEP-111 放弃本次接入的寻址键；预填模式下接入页显示放弃动作）。 */
  flowId: string;
  /** 总览行标题（放弃确认弹层文案用）。 */
  title: string;
  contextRef: string;
  rawText: string;
  sourceNote: string;
}

export interface ParsedSourceNote {
  sourceType?: string;
  sourceName?: string;
  sourceTime?: string;
  sourceNote?: string;
}

/** 接入表单折叠字段占位值（buildSourceNote 写入的缺省），预填时还原为空。 */
const FOLDED_PLACEHOLDERS: Record<string, string> = {
  来源对象: '未命名材料',
  来源时间: '未填写',
  来源说明: '无',
};

const FOLDED_FIELD_KEYS: Record<string, keyof ParsedSourceNote> = {
  来源类型: 'sourceType',
  来源对象: 'sourceName',
  来源时间: 'sourceTime',
  来源说明: 'sourceNote',
};

/**
 * 把提交时折叠的 source_note（`来源类型:X；来源对象:Y；…`）解析回表单字段。
 * 非折叠格式（外部写入/历史数据）不猜测：整串落来源说明，原文不丢。
 */
export function parseFoldedSourceNote(note: string): ParsedSourceNote {
  const text = note.trim();
  if (!text) {
    return {};
  }
  if (!text.startsWith('来源类型:')) {
    return { sourceNote: text };
  }
  const parsed: ParsedSourceNote = {};
  // 来源说明是折叠串的末字段且允许含「；」：先按标签整体截出，剩余部分再逐段解析。
  const noteMarker = '；来源说明:';
  const noteAt = text.indexOf(noteMarker);
  let head = text;
  if (noteAt >= 0) {
    const value = text.slice(noteAt + noteMarker.length);
    parsed.sourceNote = value === FOLDED_PLACEHOLDERS['来源说明'] ? '' : value;
    head = text.slice(0, noteAt);
  }
  for (const part of head.split('；')) {
    const splitAt = part.indexOf(':');
    if (splitAt < 0) {
      continue;
    }
    const label = part.slice(0, splitAt);
    const value = part.slice(splitAt + 1);
    const key = FOLDED_FIELD_KEYS[label];
    if (!key || key === 'sourceNote') {
      continue; // 提交人等展示性字段不回填
    }
    parsed[key] = value === FOLDED_PLACEHOLDERS[label] ? '' : value;
  }
  return parsed;
}
