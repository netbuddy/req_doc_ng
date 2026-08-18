import type {
  CandidateItemRead,
  DocIndexEntryRead,
  DocumentStatus,
  DocxExportRead,
  EditImpact,
  MarkdownDraftRead,
  MarkdownPatchRead,
  MissingItemRead,
  PublicationWorkspaceRead,
  SectionDraftBasisRead,
  SlotAssetType,
  SlotStatusRead,
  TemplateSectionRead,
} from '../api/publication';
import type { BadgeTone } from './common';
import { requirementItemTypeText } from './requirement-item-formation';
import { chartTypeLabels } from './diagram';

// ---- 发布管理工作台 ViewModel（04A §8：索引编排页 + 发布主工作台）----

// 三步视图：模板选择（选定/预览模板）→ 索引编排（P01）→ 发布主工作台（P02/P03）
export type PublicationViewMode = 'template' | 'index' | 'main';

export interface SlotSectionVM {
  key: string;
  number: string;
  title: string;
  level: number;
  purpose: string;
  isSlot: boolean; // content_types 非空（可承接治理资产或模板文本）
  acceptsAssets: boolean; // 可勾选治理资产（requirement_item/material）
  acceptTypeText: string; // 展示：功能条目槽位 / 支撑材料槽位 / 模板文本
  requiredText: string; // 必填 / 可选
  statusText: string; // 已满足 n 条 / 缺失 / 模板自带
  statusTone: BadgeTone;
  missingReason: string | null;
  rebuildEntry: string | null;
}

export interface CandidateGroupVM {
  key: string;
  title: string;
  items: {
    ref: string;
    label: string;
    description: string;
    disabledReason: string | null;
  }[];
}

export interface ArrangedEntryVM {
  sectionKey: string;
  sectionTitle: string;
  entries: { assetRef: string; label: string; assetType: string }[];
}

export interface FooterSummaryVM {
  templateText: string;
  templateTone: BadgeTone;
  requiredCoverageText: string;
  missingCount: number;
  /** 已编排资产条目数（不含模板文本） */
  selectedCount: number;
  /** 准入校验口径：候选池仅确认态/受控资产，草稿即全部通过；正式裁定在保存时由服务端复核 */
  admissionText: string;
  admissionTone: BadgeTone;
  canEnterMarkdown: boolean;
}

export function documentStatusMeta(status: DocumentStatus | undefined | null): { label: string; tone: BadgeTone } {
  const map: Record<DocumentStatus, { label: string; tone: BadgeTone }> = {
    index_draft: { label: '索引编排中', tone: 'processing' },
    index_blocked: { label: '索引受阻', tone: 'danger' },
    index_ready: { label: '索引就绪', tone: 'success' },
    markdown_draft: { label: 'Markdown 微调中', tone: 'processing' },
    markdown_finalized: { label: 'Markdown 已定稿', tone: 'success' },
    baseline_published: { label: '发布基线已形成', tone: 'success' },
  };
  return status ? map[status] : { label: '未编排', tone: 'neutral' };
}

export function editImpactMeta(impact: EditImpact): { label: string; tone: BadgeTone } {
  const map: Record<EditImpact, { label: string; tone: BadgeTone }> = {
    doc_expression: { label: '纯文档表达', tone: 'success' },
    confirmed_item: { label: '待修订确认态条目', tone: 'warning' },
    index_structure: { label: '章节结构调整（回索引编排）', tone: 'danger' },
    no_source_fact: { label: '无来源新事实', tone: 'danger' },
    other_asset: { label: '触及其它正式资产', tone: 'danger' },
  };
  return map[impact];
}

export function exportStatusMeta(status: DocxExportRead['status']): { label: string; tone: BadgeTone } {
  const map: Record<DocxExportRead['status'], { label: string; tone: BadgeTone }> = {
    converting: { label: '转换中', tone: 'processing' },
    succeeded: { label: '候选件待检查', tone: 'success' },
    failed: { label: '转换失败', tone: 'danger' },
    check_rejected: { label: '检查不通过', tone: 'danger' },
    baseline_confirmed: { label: '已确认为发布基线', tone: 'success' },
    manual_fallback: { label: '人工降级登记', tone: 'warning' },
  };
  return map[status];
}

function slotAcceptText(section: TemplateSectionRead): string {
  const wanted = section.content_types
    .filter((c) => c.startsWith('requirement_item:'))
    .map((c) => requirementItemTypeText(c.split(':')[1] as never));
  if (wanted.length > 0) return `${wanted.join('/')}条目槽位`;
  if (section.content_types.includes('chart')) return '图表槽位';
  if (section.content_types.includes('material')) return '支撑材料槽位';
  if (section.content_types.includes('boilerplate')) return '模板文本（可撰稿）';
  if (section.content_types.includes('authored_text')) return '人工撰稿';
  return '结构章节';
}

/** 可撰稿章节（AEP-098）：模板默认文本（boilerplate）或人工撰稿槽位（authored_text）。 */
export function isAuthorable(section: TemplateSectionRead): boolean {
  return (
    section.content_types.includes('boilerplate') ||
    section.content_types.includes('authored_text')
  );
}

export function buildSlotSections(
  sections: TemplateSectionRead[],
  slotStatus: SlotStatusRead[],
): SlotSectionVM[] {
  const statusByKey = new Map(slotStatus.map((s) => [s.section_key, s]));
  return sections.map((section) => {
    const status = statusByKey.get(section.key);
    const isSlot = section.content_types.length > 0;
    const acceptsAssets = section.content_types.some(
      (c) => c.startsWith('requirement_item:') || c === 'material' || c === 'chart',
    );
    const boilerplate = section.content_types.includes('boilerplate');
    const authoredOnly = !boilerplate && !acceptsAssets && section.content_types.includes('authored_text');
    let statusText = '结构章节';
    let statusTone: BadgeTone = 'neutral';
    if (boilerplate) {
      statusText = '模板自带';
      statusTone = 'success';
    } else if (authoredOnly) {
      const authored = status?.satisfied ?? false;
      statusText = authored ? '已撰稿' : section.required ? '需撰稿' : '未撰稿';
      statusTone = authored ? 'success' : section.required ? 'danger' : 'neutral';
    } else if (acceptsAssets && status) {
      statusText = status.satisfied ? `已满足 ${status.filled_count} 条` : '缺失';
      statusTone = status.satisfied ? 'success' : section.required ? 'danger' : 'warning';
    } else if (acceptsAssets) {
      statusText = '未编排';
      statusTone = section.required ? 'warning' : 'neutral';
    }
    return {
      key: section.key,
      number: section.number,
      title: section.title,
      level: section.level,
      purpose: section.purpose,
      isSlot,
      acceptsAssets,
      acceptTypeText: slotAcceptText(section),
      requiredText:
        acceptsAssets || authoredOnly
          ? section.required ? '必填' : '可选'
          : boilerplate ? '必填' : '—',
      statusText,
      statusTone,
      missingReason: status?.missing_reason ?? null,
      rebuildEntry: status?.rebuild_entry ?? null,
    };
  });
}

export function buildCandidateGroups(workspace: PublicationWorkspaceRead): CandidateGroupVM[] {
  const byType = new Map<string, CandidateItemRead[]>();
  for (const item of workspace.candidates.items) {
    const list = byType.get(item.req_type) ?? [];
    list.push(item);
    byType.set(item.req_type, list);
  }
  const groups: CandidateGroupVM[] = [];
  for (const [type, items] of byType) {
    groups.push({
      key: `item:${type}`,
      title: `${requirementItemTypeText(type as never)}（确认态 ${items.length}）`,
      items: items.map((item) => ({
        ref: item.item_ref,
        label: `${item.req_no} · ${item.expression}`,
        description: `v${item.version_no} · 确认态`,
        disabledReason: null,
      })),
    });
  }
  groups.push({
    key: 'material',
    title: `支撑材料（${workspace.candidates.materials.length}）`,
    items: workspace.candidates.materials.map((m) => ({
      ref: m.material_ref,
      label: m.source_note || '来源材料',
      description: m.excerpt,
      disabledReason: null,
    })),
  });
  groups.push({
    key: 'chart',
    title: `受控图表（${workspace.candidates.charts.length}）`,
    items: workspace.candidates.charts.map((c) => ({
      ref: c.chart_ref,
      label: c.title,
      description: `v${c.draft_version} · 受控图表`,
      disabledReason: null,
    })),
  });
  groups.push({
    key: 'trace',
    title: '追溯依据（只读：不入文档内容，绑定随定稿派生建立）',
    items: [],
  });
  return groups;
}

export function buildArrangedEntries(
  entries: DocIndexEntryRead[],
  sections: TemplateSectionRead[],
  workspace: PublicationWorkspaceRead,
): ArrangedEntryVM[] {
  const sectionByKey = new Map(sections.map((s) => [s.key, s]));
  const itemByRef = new Map(workspace.candidates.items.map((i) => [i.item_ref, i]));
  const materialByRef = new Map(workspace.candidates.materials.map((m) => [m.material_ref, m]));
  const grouped = new Map<string, DocIndexEntryRead[]>();
  for (const entry of entries) {
    const list = grouped.get(entry.section_key) ?? [];
    list.push(entry);
    grouped.set(entry.section_key, list);
  }
  const result: ArrangedEntryVM[] = [];
  for (const [sectionKey, sectionEntries] of grouped) {
    const section = sectionByKey.get(sectionKey);
    result.push({
      sectionKey,
      sectionTitle: section ? `${section.number} ${section.title}` : sectionKey,
      entries: sectionEntries
        .slice()
        .sort((a, b) => (a.order_no ?? 0) - (b.order_no ?? 0))
        .map((entry) => {
          const ref = entry.asset_ref ?? '';
          const item = itemByRef.get(ref);
          const material = materialByRef.get(ref);
          return {
            assetRef: ref,
            assetType: entry.asset_type,
            label: item
              ? `${item.req_no} ${item.expression}`
              : material
                ? material.source_note || '来源材料'
                : ref,
          };
        }),
    });
  }
  return result;
}

// ---- 草稿态槽位判据（缺失清单 / 底栏缺失计数 / 槽位树三处同源；issue #14）----

/**
 * 槽位承载类型：镜像后端 `_slot_asset_kind`（backend/app/services/publication.py:119）的优先级。
 * other = 知识整表投影与结构章节——与草稿无关，不计入必填覆盖统计。
 */
type DraftSlotKind = 'asset' | 'boilerplate' | 'authored' | 'other';

function draftSlotKind(section: TemplateSectionRead): DraftSlotKind {
  const acceptsAssets = section.content_types.some(
    (c) => c.startsWith('requirement_item:') || c === 'material' || c === 'chart',
  );
  if (acceptsAssets) return 'asset';
  if (section.content_types.includes('boilerplate')) return 'boilerplate';
  if (section.content_types.includes('authored_text')) return 'authored';
  return 'other';
}

/** 资产槽位主承载类型：镜像后端 `_slot_asset_kind` 的 requirement_item > chart > material 优先级。 */
function assetSlotKind(section: TemplateSectionRead): 'requirement_item' | 'chart' | 'material' {
  if (section.content_types.some((c) => c.startsWith('requirement_item:'))) return 'requirement_item';
  if (section.content_types.includes('chart')) return 'chart';
  return 'material';
}

/**
 * 资产槽位补建入口：镜像后端 `_REBUILD_ENTRIES`（backend/app/services/publication.py:104）。
 * 仅取消勾选场景需要——该槽位在服务端判为已满足，slot_status 不下发 rebuild_entry；
 * 其余情形一律沿用服务端 missing_list 原文，不在前端另造判据。
 */
const DRAFT_REBUILD_ENTRIES: Record<'requirement_item' | 'chart' | 'material', string> = {
  requirement_item: '回到需求管理工作台：材料接入 → 知识抽取 → 条目形成 → 条目确认后重新编排',
  material: '回到需求管理工作台导入并接入支撑材料后重新编排',
  chart: '回到图表设计工作台完成图表核对与确认（受控图表）后重新编排',
};

export interface DraftCoverageVM {
  requiredTotal: number;
  coveredTotal: number;
  missingCount: number;
}

/**
 * 必填覆盖的单一判据（底栏与槽位树同源）：资产槽位以草稿条目为准（未保存也即时反馈），
 * boilerplate 由模板满足，纯撰稿章节以撰稿存在为满足（AEP-098）。
 */
export function evaluateDraftCoverage(
  sections: TemplateSectionRead[],
  draftEntries: DocIndexEntryRead[],
  manuscriptKeys: Set<string>,
): DraftCoverageVM {
  const draftBySection = new Set(draftEntries.map((e) => e.section_key));
  let requiredTotal = 0;
  let coveredTotal = 0;
  for (const section of sections) {
    const kind = draftSlotKind(section);
    if (kind === 'other' || !section.required) continue;
    requiredTotal += 1;
    const covered =
      kind === 'asset' ? draftBySection.has(section.key)
      : kind === 'boilerplate' ? true
      : manuscriptKeys.has(section.key);
    if (covered) coveredTotal += 1;
  }
  return { requiredTotal, coveredTotal, missingCount: requiredTotal - coveredTotal };
}

/**
 * 缺失行：blocking=计入「缺失槽位」的必填阻断项（红），否则为非阻断提示（中性）。
 * 知识整表投影为空是后端刻意产出的提示（reason 明写「非阻断」）且 required=False，
 * 混入必填缺失会使清单计数与底栏「缺失槽位」读数不一致。
 */
export interface MissingRowVM extends MissingItemRead {
  blocking: boolean;
}

/**
 * 缺失清单读侧（issue #14）：资产槽位按草稿实时重算，与 evaluateDraftCoverage/buildSlotTree 同源；
 * 知识整表投影与纯撰稿章节与草稿无关，沿用服务端 missing_list 原行。
 * 保存后草稿等于服务端条目，派生结果即回落服务端口径。
 * blocking 口径与 evaluateDraftCoverage 完全一致——阻断行数恒等于底栏 missingCount。
 */
export function buildDraftMissingList(
  workspace: PublicationWorkspaceRead,
  draftEntries: DocIndexEntryRead[],
): MissingRowVM[] {
  const serverByKey = new Map(workspace.missing_list.map((m) => [m.section_key, m]));
  const draftBySection = new Set(draftEntries.map((e) => e.section_key));
  const rows: MissingRowVM[] = [];
  for (const section of workspace.template.sections) {
    if (section.content_types.length === 0) continue; // 结构章节非槽位
    const serverRow = serverByKey.get(section.key);
    if (draftSlotKind(section) !== 'asset') {
      // 非资产槽位（知识/撰稿）：原样透传；是否阻断与底栏统计口径一致
      if (serverRow) rows.push({ ...serverRow, blocking: draftSlotKind(section) !== 'other' && section.required });
      continue;
    }
    if (!section.required || draftBySection.has(section.key)) continue;
    rows.push({
      ...(serverRow ?? {
        section_key: section.key,
        section_title: `${section.number} ${section.title}`,
        // 取消勾选路径：资产必然来自候选池，故候选存在这一分支是确定的
        reason: '必填槽位缺失：已有确认态候选资产但尚未编排到该槽位',
        rebuild_entry: DRAFT_REBUILD_ENTRIES[assetSlotKind(section)],
      }),
      blocking: true,
    });
  }
  return rows;
}

/**
 * 全覆盖成功提示的去重判据：仅在「有缺失 → 无缺失」跃迁时提示一次。
 * previous=null 表示尚无基线（首次渲染 / 离开索引页），此时只记录不提示——
 * 初始即全覆盖不弹，StrictMode 双调用亦不重复弹。
 */
export function shouldAnnounceFullCoverage(previous: number | null, current: number): boolean {
  return previous !== null && previous > 0 && current === 0;
}

export interface CoverageAnnouncement {
  /** 本次渲染是否弹出全覆盖成功提示 */
  announce: boolean;
  /** 供组件写回 missingCountRef 的下一基线（恒为本次 missingCount） */
  nextBaseline: number | null;
}

/**
 * 全覆盖成功提示的跃迁判据（有状态去重的纯函数化，供组件消费 missingCountRef 生命周期）：
 * 仅当「有缺失 → 无缺失」跃迁（shouldAnnounceFullCoverage）且当前可进入 Markdown
 * （canEnterMarkdown=模板校验通过且全覆盖）时提示。模板校验失败时 missingCount 因空章节集恒为 0，
 * 单看跃迁会误报，故合取 canEnterMarkdown 抑制（P1）。nextBaseline 恒为本次 missingCount。
 */
export function nextCoverageAnnouncement(
  previous: number | null,
  current: number,
  canEnterMarkdown: boolean,
): CoverageAnnouncement {
  return {
    announce: shouldAnnounceFullCoverage(previous, current) && canEnterMarkdown,
    nextBaseline: current,
  };
}

export function buildFooterSummary(
  workspace: PublicationWorkspaceRead,
  draftEntries: DocIndexEntryRead[],
): FooterSummaryVM {
  const templateError = workspace.template.error ?? null;
  const manuscriptKeys = new Set((workspace.manuscripts ?? []).map((m) => m.section_key));
  const { requiredTotal, coveredTotal } = evaluateDraftCoverage(
    workspace.template.sections,
    draftEntries,
    manuscriptKeys,
  );
  const selectedCount = draftEntries.length;
  return {
    templateText: templateError ? '模板校验失败' : '模板校验通过',
    templateTone: templateError ? 'danger' : 'success',
    requiredCoverageText: `必填覆盖 ${coveredTotal}/${requiredTotal}`,
    missingCount: requiredTotal - coveredTotal,
    selectedCount,
    admissionText: selectedCount > 0 ? '全部通过' : '—',
    admissionTone: selectedCount > 0 ? 'success' : 'neutral',
    canEnterMarkdown: !templateError && coveredTotal === requiredTotal,
  };
}

// ---- 索引编排页高保真投影（04A §8 原型：信息条 / 槽位树 / 候选池 / 槽位分组索引）----

/** 资产类型 → 可承接槽位候选（换槽位 / 添加目标；同 content_type 匹配）。 */
export function compatibleSlotOptions(
  sections: TemplateSectionRead[],
  assetType: SlotAssetType,
  reqType?: string,
): { key: string; label: string }[] {
  const wanted = assetType === 'requirement_item' ? `requirement_item:${reqType}` : assetType;
  return sections
    .filter((s) => s.content_types.includes(wanted))
    .map((s) => ({ key: s.key, label: `${s.number} ${s.title}` }));
}

export interface IndexHeaderVM {
  docTitle: string;
  statusText: string;
  statusTone: BadgeTone;
  stats: {
    /** 需求条目总数 = 确认态 + 待确认（治理口径内可统计的事实） */
    total: number;
    confirmed: number;
    pending: number;
    /** 必填槽位缺失数（按当前草稿实时计算；原型「已阻塞」无事实来源，以此替代） */
    missingSlots: number;
  };
}

export function buildIndexHeader(
  workspace: PublicationWorkspaceRead,
  draftEntries: DocIndexEntryRead[],
): IndexHeaderVM {
  const meta = documentStatusMeta(workspace.document?.status);
  const confirmed = workspace.candidates.items.length;
  const pending = workspace.candidates.pending_item_count;
  return {
    docTitle: workspace.document?.title ?? '需求规格说明',
    statusText: meta.label,
    statusTone: meta.tone,
    stats: {
      total: confirmed + pending,
      confirmed,
      pending,
      missingSlots: buildFooterSummary(workspace, draftEntries).missingCount,
    },
  };
}

export interface SlotRowVM {
  key: string;
  number: string;
  title: string;
  acceptsAssets: boolean;
  /** 可撰稿章节（boilerplate/authored_text）：行尾提供「撰稿」入口 */
  authorable: boolean;
  acceptTypeText: string;
  requiredText: '必填' | '可选' | '—';
  coverageText: string;
  coverageTone: BadgeTone;
}

export interface SlotTreeGroupVM {
  key: string;
  number: string;
  title: string;
  rows: SlotRowVM[];
}

export interface SlotTreeVM {
  groups: SlotTreeGroupVM[];
  requiredProgress: { covered: number; total: number; percent: number; missing: number };
}

function slotRow(section: TemplateSectionRead, filledCount: number, hasManuscript: boolean): SlotRowVM {
  const acceptsAssets = section.content_types.some(
    (c) => c.startsWith('requirement_item:') || c === 'material' || c === 'chart',
  );
  const boilerplate = section.content_types.includes('boilerplate');
  const authorable = isAuthorable(section);
  const authoredOnly = authorable && !boilerplate && !acceptsAssets;
  let coverageText = '—';
  let coverageTone: BadgeTone = 'neutral';
  if (boilerplate) {
    coverageText = hasManuscript ? '已撰稿' : '已满足';
    coverageTone = 'success';
  } else if (authoredOnly) {
    coverageText = hasManuscript ? '已撰稿' : section.required ? '需撰稿' : '未撰稿';
    coverageTone = hasManuscript ? 'success' : section.required ? 'danger' : 'neutral';
  } else if (acceptsAssets) {
    if (filledCount > 0) {
      coverageText = `已满足 · ${filledCount} 项`;
      coverageTone = 'success';
    } else {
      coverageText = section.required ? '缺失' : '未编排';
      coverageTone = section.required ? 'danger' : 'neutral';
    }
  }
  return {
    key: section.key,
    number: section.number,
    title: section.title,
    acceptsAssets,
    authorable,
    acceptTypeText: slotAcceptText(section),
    requiredText: acceptsAssets || boilerplate || authoredOnly ? (section.required ? '必填' : '可选') : '—',
    coverageText,
    coverageTone,
  };
}

/** 左栏槽位树：level-1 分组 + 覆盖状态按当前草稿实时计算（未保存也即时反馈）。 */
export function buildSlotTree(
  sections: TemplateSectionRead[],
  draftEntries: DocIndexEntryRead[],
  manuscriptKeys: Set<string> = new Set(),
): SlotTreeVM {
  const filledBySection = new Map<string, number>();
  for (const entry of draftEntries) {
    filledBySection.set(entry.section_key, (filledBySection.get(entry.section_key) ?? 0) + 1);
  }
  const groups: SlotTreeGroupVM[] = [];
  for (const section of sections) {
    const row = slotRow(
      section,
      filledBySection.get(section.key) ?? 0,
      manuscriptKeys.has(section.key),
    );
    if (section.level === 1) {
      groups.push({
        key: section.key,
        number: section.number,
        title: section.title,
        // level-1 自身是槽位时（如 附录A 支撑材料）作为该组唯一行
        rows: section.content_types.length > 0 ? [row] : [],
      });
    } else if (groups.length > 0) {
      groups[groups.length - 1].rows.push(row);
    }
  }
  // 必填进度与底栏缺失计数同源（evaluateDraftCoverage 为唯一判据）
  const { requiredTotal, coveredTotal, missingCount } = evaluateDraftCoverage(
    sections,
    draftEntries,
    manuscriptKeys,
  );
  return {
    groups,
    requiredProgress: {
      covered: coveredTotal,
      total: requiredTotal,
      percent: requiredTotal === 0 ? 100 : Math.round((coveredTotal / requiredTotal) * 100),
      missing: missingCount,
    },
  };
}

export type CandidateTabKey = 'items' | 'charts' | 'traces' | 'materials';

export interface CandidateTabVM {
  key: CandidateTabKey;
  label: string;
  count: number;
}

export function buildCandidateTabs(workspace: PublicationWorkspaceRead): CandidateTabVM[] {
  return [
    { key: 'items', label: '需求条目', count: workspace.candidates.items.length },
    { key: 'charts', label: '图表', count: workspace.candidates.charts.length },
    { key: 'traces', label: '追溯依据', count: 0 },
    { key: 'materials', label: '支撑材料', count: workspace.candidates.materials.length },
  ];
}

export interface CandidateRowVM {
  ref: string;
  kind: 'requirement_item' | 'chart' | 'material';
  no: string;
  title: string;
  typeText: string;
  /** 条目语义类型稳定码（勾选时决定目标槽位）；图表/材料为 null */
  reqType: string | null;
  statusText: string;
  statusTone: BadgeTone;
  sourceCount: number | null;
  admissionText: string;
  admissionTone: BadgeTone;
}

export function buildCandidateRows(
  workspace: PublicationWorkspaceRead,
  tab: CandidateTabKey,
): CandidateRowVM[] {
  if (tab === 'items') {
    return workspace.candidates.items.map((item) => ({
      ref: item.item_ref,
      kind: 'requirement_item' as const,
      no: item.req_no,
      title: item.expression,
      typeText: requirementItemTypeText(item.req_type),
      reqType: item.req_type,
      statusText: '已确认',
      statusTone: 'success' as BadgeTone,
      sourceCount: null,
      admissionText: '已确认',
      admissionTone: 'success' as BadgeTone,
    }));
  }
  if (tab === 'charts') {
    return workspace.candidates.charts.map((chart) => ({
      ref: chart.chart_ref,
      kind: 'chart' as const,
      no: `v${chart.draft_version}`,
      title: chart.title,
      typeText:
        (chartTypeLabels as Record<string, string>)[chart.chart_type] ?? chart.chart_type,
      reqType: null,
      statusText: '受控图表',
      statusTone: 'success' as BadgeTone,
      sourceCount: chart.source_count,
      admissionText: '受控图表',
      admissionTone: 'success' as BadgeTone,
    }));
  }
  if (tab === 'materials') {
    return workspace.candidates.materials.map((material) => ({
      ref: material.material_ref,
      kind: 'material' as const,
      no: `v${material.source_version}`,
      title: material.source_note || '来源材料',
      typeText: '支撑材料',
      reqType: null,
      statusText: '已接入',
      statusTone: 'processing' as BadgeTone,
      sourceCount: null,
      admissionText: '可支撑',
      admissionTone: 'processing' as BadgeTone,
    }));
  }
  return [];
}

export interface CandidatePoolFilter {
  keyword: string;
  /** 条目 tab：req_type 稳定码；其它 tab 忽略 */
  typeFilter: string | 'all';
}

export function filterCandidateRows(
  rows: CandidateRowVM[],
  filter: CandidatePoolFilter,
  page: number,
  pageSize: number,
): { rows: CandidateRowVM[]; total: number } {
  const keyword = filter.keyword.trim().toLowerCase();
  const matched = rows.filter((row) => {
    if (filter.typeFilter !== 'all' && row.reqType !== filter.typeFilter) return false;
    if (!keyword) return true;
    return (
      row.no.toLowerCase().includes(keyword) ||
      row.title.toLowerCase().includes(keyword) ||
      row.typeText.toLowerCase().includes(keyword)
    );
  });
  const start = Math.max(0, (page - 1) * pageSize);
  return { rows: matched.slice(start, start + pageSize), total: matched.length };
}

export interface ArrangedRowVM {
  assetRef: string;
  assetType: SlotAssetType;
  no: string;
  title: string;
  typeText: string;
  statusText: string;
  statusTone: BadgeTone;
  /** 换槽位候选（不含当前槽位；空 = 无处可换，控件禁用） */
  slotOptions: { key: string; label: string }[];
}

export interface ArrangedSlotGroupVM {
  sectionKey: string;
  number: string;
  title: string;
  requiredText: '必填' | '可选';
  badgeText: string;
  badgeTone: BadgeTone;
  /** 「+添加到此槽位」联动候选池的目标 tab */
  addTab: CandidateTabKey;
  entries: ArrangedRowVM[];
}

/** 右栏：全部资产槽位分组（含空槽位，承载「+添加到此槽位」），条目按 order_no 排序。 */
export function buildArrangedSlotGroups(
  draftEntries: DocIndexEntryRead[],
  workspace: PublicationWorkspaceRead,
): ArrangedSlotGroupVM[] {
  const sections = workspace.template.sections;
  const itemByRef = new Map(workspace.candidates.items.map((i) => [i.item_ref, i]));
  const chartByRef = new Map(workspace.candidates.charts.map((c) => [c.chart_ref, c]));
  const materialByRef = new Map(workspace.candidates.materials.map((m) => [m.material_ref, m]));
  const grouped = new Map<string, DocIndexEntryRead[]>();
  for (const entry of draftEntries) {
    const list = grouped.get(entry.section_key) ?? [];
    list.push(entry);
    grouped.set(entry.section_key, list);
  }
  const result: ArrangedSlotGroupVM[] = [];
  for (const section of sections) {
    const acceptsAssets = section.content_types.some(
      (c) => c.startsWith('requirement_item:') || c === 'material' || c === 'chart',
    );
    if (!acceptsAssets) continue;
    const entries = (grouped.get(section.key) ?? [])
      .slice()
      .sort((a, b) => (a.order_no ?? 0) - (b.order_no ?? 0));
    const filled = entries.length > 0;
    result.push({
      sectionKey: section.key,
      number: section.number,
      title: section.title,
      requiredText: section.required ? '必填' : '可选',
      badgeText: filled
        ? `${entries.length} 项 · 已满足`
        : section.required
          ? '缺失'
          : '未编排',
      badgeTone: filled ? 'success' : section.required ? 'danger' : 'neutral',
      addTab: section.content_types.includes('chart')
        ? 'charts'
        : section.content_types.includes('material')
          ? 'materials'
          : 'items',
      entries: entries.map((entry) => {
        const ref = entry.asset_ref ?? '';
        const item = itemByRef.get(ref);
        const chart = chartByRef.get(ref);
        const material = materialByRef.get(ref);
        const reqType = item?.req_type;
        return {
          assetRef: ref,
          assetType: entry.asset_type,
          no: item?.req_no ?? (chart ? `图表 v${chart.draft_version}` : material ? '材料' : ref.slice(0, 8)),
          title: item?.expression ?? chart?.title ?? material?.source_note ?? ref,
          typeText: item
            ? requirementItemTypeText(item.req_type)
            : chart
              ? ((chartTypeLabels as Record<string, string>)[chart.chart_type] ?? chart.chart_type)
              : '支撑材料',
          statusText: item ? '已确认' : chart ? '受控图表' : '已接入',
          statusTone: item || chart ? ('success' as BadgeTone) : ('processing' as BadgeTone),
          slotOptions: compatibleSlotOptions(sections, entry.asset_type, reqType).filter(
            (option) => option.key !== section.key,
          ),
        };
      }),
    });
  }
  return result;
}

// ---- Markdown 窗口辅助 ----

export interface MarkdownStateVM {
  statusText: string;
  statusTone: BadgeTone;
  canEdit: boolean;
  canFinalize: boolean;
  needsRegenerate: boolean;
  blockReasons: string[];
  pendingItemPatches: MarkdownDraftRead['patches'];
}

export function buildMarkdownState(draft: MarkdownDraftRead | null | undefined): MarkdownStateVM {
  if (!draft) {
    return {
      statusText: '尚未生成',
      statusTone: 'neutral',
      canEdit: false,
      canFinalize: false,
      needsRegenerate: false,
      blockReasons: [],
      pendingItemPatches: [],
    };
  }
  const map: Record<MarkdownDraftRead['status'], { label: string; tone: BadgeTone }> = {
    draft: { label: `中间稿 v${draft.version_no}`, tone: 'processing' },
    finalized: { label: `定稿 v${draft.version_no} · 可导出`, tone: 'success' },
    superseded: { label: '已失效（索引调整/重新生成）', tone: 'warning' },
    awaiting_item_revision: { label: '等待条目修订收束', tone: 'warning' },
  };
  const meta = map[draft.status];
  const pendingItemPatches = draft.patches.filter(
    (p) => p.impact === 'confirmed_item' && p.status === 'pending',
  );
  return {
    statusText: meta.label,
    statusTone: meta.tone,
    canEdit: draft.status === 'draft',
    canFinalize: draft.status === 'draft' && draft.block_reasons.length === 0,
    needsRegenerate: draft.status === 'superseded' || draft.status === 'awaiting_item_revision',
    blockReasons: draft.block_reasons,
    pendingItemPatches,
  };
}

// ---- 发布主工作台（P02/P03）投影：文档大纲树 + 编辑影响汇总 ----

export interface OutlineRowVM {
  key: string;
  number: string;
  title: string;
  statusText: string;
  statusTone: BadgeTone;
  /** 是否展示状态徽标（承接资产/可撰稿/模板自带的章节才有意义） */
  showStatus: boolean;
}

export interface OutlineChapterVM {
  key: string;
  number: string;
  title: string;
  /** 章级绑定徽标：缺失 > 有改动 > 已绑定 > 结构 */
  bindingText: '已绑定' | '有改动' | '缺失' | '结构';
  bindingTone: BadgeTone;
  childCount: number;
  rows: OutlineRowVM[];
}

export interface DocumentOutlineVM {
  chapters: OutlineChapterVM[];
  chapterCount: number;
  sectionCount: number;
}

/**
 * 左区文档大纲树（04A §8 原型）：level-1 分组 + 子节计数 + 章级绑定徽标。
 * - 缺失：本章有必填槽位未满足（statusTone=danger）。
 * - 有改动：本章有可归因的 Markdown 编辑补丁（补丁绑定条目 → 索引 → 章）。
 * - 已绑定：本章有已满足/已撰稿/模板自带内容。
 * - 结构：纯结构章节。
 */
export function buildOutlineTree(
  sections: TemplateSectionRead[],
  slotStatus: SlotStatusRead[],
  patches: MarkdownPatchRead[],
  indexEntries: DocIndexEntryRead[],
): DocumentOutlineVM {
  const slotVMs = buildSlotSections(sections, slotStatus);
  const vmByKey = new Map(slotVMs.map((v) => [v.key, v]));

  // 章归属：按顺序，每个 level-1 开新章，其后 section 归入该章
  const chapterKeyBySection = new Map<string, string>();
  let currentChapter = '';
  for (const s of sections) {
    if (s.level === 1) currentChapter = s.key;
    chapterKeyBySection.set(s.key, currentChapter);
  }
  const sectionByAsset = new Map<string, string>();
  for (const e of indexEntries) {
    if (e.asset_ref) sectionByAsset.set(e.asset_ref, e.section_key);
  }
  const changedChapters = new Set<string>();
  for (const patch of patches) {
    const ref = patch.bound_item_ref ?? patch.reflow_item_ref;
    if (!ref) continue;
    const sectionKey = sectionByAsset.get(ref);
    if (!sectionKey) continue;
    const chapterKey = chapterKeyBySection.get(sectionKey);
    if (chapterKey) changedChapters.add(chapterKey);
  }

  const chapters: OutlineChapterVM[] = [];
  let sectionCount = 0;
  for (const section of sections) {
    if (section.level === 1) {
      chapters.push({
        key: section.key,
        number: section.number,
        title: section.title,
        bindingText: '结构',
        bindingTone: 'neutral',
        childCount: 0,
        rows: [],
      });
    } else {
      sectionCount += 1;
      const chapter = chapters[chapters.length - 1];
      if (!chapter) continue;
      const vm = vmByKey.get(section.key);
      chapter.rows.push({
        key: section.key,
        number: section.number,
        title: section.title,
        statusText: vm?.statusText ?? '',
        statusTone: vm?.statusTone ?? 'neutral',
        showStatus: !!vm && vm.statusText !== '结构章节',
      });
    }
  }

  for (const chapter of chapters) {
    chapter.childCount = chapter.rows.length;
    const memberKeys = [chapter.key, ...chapter.rows.map((r) => r.key)];
    const vms = memberKeys
      .map((k) => vmByKey.get(k))
      .filter((v): v is SlotSectionVM => Boolean(v));
    const hasDanger = vms.some((v) => v.statusTone === 'danger');
    const hasSuccess = vms.some((v) => v.statusTone === 'success');
    if (hasDanger) {
      chapter.bindingText = '缺失';
      chapter.bindingTone = 'danger';
    } else if (changedChapters.has(chapter.key)) {
      chapter.bindingText = '有改动';
      chapter.bindingTone = 'warning';
    } else if (hasSuccess) {
      chapter.bindingText = '已绑定';
      chapter.bindingTone = 'success';
    } else {
      chapter.bindingText = '结构';
      chapter.bindingTone = 'neutral';
    }
  }

  return { chapters, chapterCount: chapters.length, sectionCount };
}

// 编辑影响类型的处置文案（D4 分组卡「章节结构」组明细复用 index_structure 一句）。
const EDIT_IMPACT_DESC: Record<EditImpact, string> = {
  doc_expression: '仅涉及文字、结构、排版等表达，不影响已绑定需求条目。',
  confirmed_item: '内容改动可能影响部分条目，定稿前需要复核确认。',
  no_source_fact: '存在未绑定来源的事实陈述，建议绑定来源或补充依据。',
  index_structure: '触及章节结构，需回到索引编排调整后重新生成。',
  other_asset: '触及其它正式资产（如图表源码），需回相应工作台修订。',
};

// ---- D2 行级 diff（生成稿 baseline ↔ 当前编辑内容）----
// CodeMirror 6 编辑器的 diff 高亮/gutter 与修订摘要条同用此结果。卡面限依赖只能
// @codemirror/*，故 diff 不引第三方库、在此自写行级 LCS（公共前后缀裁剪 + 中段 LCS，
// 超阈值退化为整段改动近似），纯函数、可单测；改动分类仍是后端职责，此处只做视觉 diff。

export type LineChangeStatus = 'same' | 'add' | 'chg';

export interface DiffLineVM {
  /** 本条「当前稿」行的状态 */
  status: LineChangeStatus;
  /** 紧接本行之前、从 baseline 删除的行数（gutter 画 del 标记） */
  delBefore: number;
}

export interface MarkdownDiffVM {
  /** 逐当前稿行 */
  lines: DiffLineVM[];
  /** 末行之后从 baseline 删除的行数 */
  trailingDel: number;
  /** 新增行数（摘要条 ＋a 增） */
  add: number;
  /** 修改行数（摘要条 ~b 改） */
  chg: number;
  /** 删除的 baseline 行数（不进摘要计数，见详设 §7.2；仅 gutter 呈现） */
  del: number;
  /** 每个连续改动段（add/chg 相邻run）的起始行号（0 基），供修订条 位置 k/N 导航 */
  hunks: number[];
}

const DIFF_LCS_CELL_LIMIT = 2_000_000; // 中段 n*m 超此值退化，护住大文档性能（失败策略②）

/** 行级 LCS diff：baseline→current。空串按零行处理。 */
export function diffMarkdownLines(baseline: string, current: string): MarkdownDiffVM {
  const a = baseline.length ? baseline.split('\n') : [];
  const b = current.length ? current.split('\n') : [];

  // 公共前缀 / 后缀裁剪：典型编辑是局部的，裁掉相同头尾后 LCS 规模骤降
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre += 1;
  let suf = 0;
  while (
    suf < a.length - pre &&
    suf < b.length - pre &&
    a[a.length - 1 - suf] === b[b.length - 1 - suf]
  ) {
    suf += 1;
  }
  const am = a.slice(pre, a.length - suf);
  const bm = b.slice(pre, b.length - suf);

  // 中段编辑脚本：op 序列 'keep'|'add'|'del'，对齐 baseline/current 中段
  type Op = { kind: 'keep' | 'add' | 'del' };
  let ops: Op[];
  if (am.length === 0) {
    ops = bm.map(() => ({ kind: 'add' as const }));
  } else if (bm.length === 0) {
    ops = am.map(() => ({ kind: 'del' as const }));
  } else if (am.length * bm.length > DIFF_LCS_CELL_LIMIT) {
    // 退化：中段整体视为改动（前 min 对 keep 语义交给下方 del+add 配对为 chg）
    ops = [...am.map(() => ({ kind: 'del' as const })), ...bm.map(() => ({ kind: 'add' as const }))];
  } else {
    // LCS DP（Int32 一维滚动降内存），再回溯
    const n = am.length;
    const m = bm.length;
    const dp = new Int32Array((n + 1) * (m + 1));
    const w = m + 1;
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        dp[i * w + j] =
          am[i] === bm[j]
            ? dp[(i + 1) * w + (j + 1)] + 1
            : Math.max(dp[(i + 1) * w + j], dp[i * w + (j + 1)]);
      }
    }
    ops = [];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (am[i] === bm[j]) {
        ops.push({ kind: 'keep' });
        i += 1;
        j += 1;
      } else if (dp[(i + 1) * w + j] >= dp[i * w + (j + 1)]) {
        ops.push({ kind: 'del' });
        i += 1;
      } else {
        ops.push({ kind: 'add' });
        j += 1;
      }
    }
    while (i < n) {
      ops.push({ kind: 'del' });
      i += 1;
    }
    while (j < m) {
      ops.push({ kind: 'add' });
      j += 1;
    }
  }

  // 逐当前稿行状态：先铺公共前缀（same），再走中段 ops，再铺公共后缀（same）。
  // 相邻 del 段紧跟 add 段 → 前 min 对配为 chg（修改），多余归 add/del（§7.2 口径）。
  const lines: DiffLineVM[] = [];
  for (let k = 0; k < pre; k += 1) lines.push({ status: 'same', delBefore: 0 });

  let add = 0;
  let chg = 0;
  let pendingDel = 0; // 尚未配对的删除行数，供下一段 add 配为 chg
  const flushDelBefore = (): number => {
    const d = pendingDel;
    pendingDel = 0;
    return d;
  };
  let t = 0;
  while (t < ops.length) {
    const kind = ops[t].kind;
    if (kind === 'keep') {
      lines.push({ status: 'same', delBefore: flushDelBefore() });
      t += 1;
    } else if (kind === 'del') {
      pendingDel += 1;
      t += 1;
    } else {
      // 收集这一整段连续 add
      let addRun = 0;
      while (t < ops.length && ops[t].kind === 'add') {
        addRun += 1;
        t += 1;
      }
      // 前 min(删,增) 对配为 chg（修改行，删除被吸收、不再单独作 del gutter）；
      // 多余的 add 归纯新增，多余的删留待后续行的 delBefore。
      const paired = Math.min(pendingDel, addRun);
      pendingDel -= paired;
      for (let k = 0; k < addRun; k += 1) {
        if (k < paired) {
          lines.push({ status: 'chg', delBefore: 0 });
          chg += 1;
        } else {
          lines.push({ status: 'add', delBefore: 0 });
          add += 1;
        }
      }
    }
  }
  // 中段剩余的未配对删除：有公共后缀时归第一行后缀之前（删除发生在它上方），
  // 无后缀时才是末尾删除。
  let trailingDel = 0;
  if (suf > 0) {
    lines.push({ status: 'same', delBefore: pendingDel });
    for (let k = 1; k < suf; k += 1) lines.push({ status: 'same', delBefore: 0 });
  } else {
    trailingDel = pendingDel;
  }

  // del = 未配对删除（即 gutter 上真正呈现的删除标记）＝各行 delBefore 之和 + 末尾删除
  const del = lines.reduce((s, l) => s + l.delBefore, 0) + trailingDel;

  // hunks：连续 add/chg run 的起始行
  const hunks: number[] = [];
  for (let k = 0; k < lines.length; k += 1) {
    const changed = lines[k].status !== 'same';
    const prevChanged = k > 0 && lines[k - 1].status !== 'same';
    if (changed && !prevChanged) hunks.push(k);
  }

  return { lines, trailingDel, add, chg, del, hunks };
}

// ---- D4 编辑影响按对象分组（章节 / 确认态条目 / 图表 / 提示）----
// 详设 §7.3：概览句 + 判定 pill + 四组卡。分类仍是后端 patch.impact 职责，此处只
// 换呈现层级（把扁平瓦片重排为按影响对象分组），复用 buildOutlineTree 同款 join。

export interface EditImpactObjectItemVM {
  key: string;
  label: string;
  tone: BadgeTone;
  /** 前后差值摘要 / 处数说明 */
  detail: string;
}

export interface EditImpactGroupVM {
  key: 'section' | 'item' | 'chart' | 'hint' | 'structure';
  title: string;
  tone: BadgeTone;
  count: number;
  items: EditImpactObjectItemVM[];
  emptyText: string;
}

export interface EditImpactOverviewVM {
  totalChanges: number;
  sectionCount: number;
  itemCount: number;
  chartCount: number;
  needsReview: number;
  overviewText: string;
  verdict: { label: string; tone: BadgeTone };
  groups: EditImpactGroupVM[];
}

const DIFF_SNIPPET_MAX = 48;
function diffSnippet(before: string, after: string): string {
  const trim = (s: string) => {
    const one = (s ?? '').replace(/\s+/g, ' ').trim();
    return one.length > DIFF_SNIPPET_MAX ? `${one.slice(0, DIFF_SNIPPET_MAX)}…` : one;
  };
  const b = trim(before);
  const a = trim(after);
  if (b && a) return `${b} → ${a}`;
  if (a) return `＋ ${a}`;
  if (b) return `－ ${b}`;
  return '（无文本差异）';
}

export function buildEditImpactGroups(
  patches: MarkdownPatchRead[],
  indexEntries: DocIndexEntryRead[],
  sections: TemplateSectionRead[],
): EditImpactOverviewVM {
  const sectionByAsset = new Map<string, string>();
  for (const e of indexEntries) {
    if (e.asset_ref) sectionByAsset.set(e.asset_ref, e.section_key);
  }
  const sectionMeta = new Map<string, { number: string; title: string }>();
  for (const s of sections) sectionMeta.set(s.key, { number: s.number, title: s.title });

  // 章节组：patch → 绑定条目 → 索引 → 章节，聚合处数。
  // index_structure 补丁专有分支优先归「章节结构」组（见下），即便其 ref 可 join 到章节
  // 也不并入本组，故此处先行剔除，避免章节结构调整被抹平为普通「章节有改动」。
  const sectionCounts = new Map<string, number>();
  for (const patch of patches) {
    if (patch.impact === 'index_structure') continue;
    const ref = patch.bound_item_ref ?? patch.reflow_item_ref;
    if (!ref) continue;
    const sectionKey = sectionByAsset.get(ref);
    if (!sectionKey) continue;
    sectionCounts.set(sectionKey, (sectionCounts.get(sectionKey) ?? 0) + 1);
  }
  const sectionItems: EditImpactObjectItemVM[] = [...sectionCounts.entries()].map(([key, count]) => {
    const meta = sectionMeta.get(key);
    return {
      key,
      label: meta ? `${meta.number} ${meta.title}`.trim() : key,
      tone: 'warning' as BadgeTone,
      detail: `${count} 处改动`,
    };
  });

  // 确认态条目组：绑定条目文本被改（有 bound_item_ref 且 impact 属条目类），按条目聚合。
  // 图表（other_asset）、无来源事实（no_source_fact）、章节结构（index_structure）各归本组，
  // 不重复计入条目组——即便图表补丁也带 bound_item_ref（图表资产 ref）。
  const ITEM_IMPACTS: EditImpact[] = ['confirmed_item', 'doc_expression'];
  const itemGroups = new Map<string, { impact: EditImpact; patches: MarkdownPatchRead[] }>();
  for (const patch of patches) {
    if (!patch.bound_item_ref) continue;
    if (!ITEM_IMPACTS.includes(patch.impact)) continue;
    const g = itemGroups.get(patch.bound_item_ref) ?? { impact: patch.impact, patches: [] };
    g.patches.push(patch);
    // 影响态取更重者：confirmed_item 优先于 doc_expression
    if (patch.impact === 'confirmed_item') g.impact = 'confirmed_item';
    itemGroups.set(patch.bound_item_ref, g);
  }
  const itemItems: EditImpactObjectItemVM[] = [...itemGroups.entries()].map(([ref, g]) => {
    const meta = editImpactMeta(g.impact);
    const first = g.patches[0];
    return {
      key: ref,
      label: ref,
      tone: meta.tone,
      detail: `${meta.label}｜${diffSnippet(first?.before_text ?? '', first?.after_text ?? '')}`,
    };
  });

  // 受控图表组：impact = other_asset（触及图表源码围栏 → 阻断）
  const chartPatches = patches.filter((p) => p.impact === 'other_asset');
  const chartItems: EditImpactObjectItemVM[] = chartPatches.map((p) => ({
    key: p.patch_ref,
    label: p.bound_item_ref ?? '受控图表',
    tone: 'danger' as BadgeTone,
    detail: `触及图表源码围栏（阻断）｜${diffSnippet(p.before_text, p.after_text)}`,
  }));

  // 待处理提示组：impact = no_source_fact（无来源新事实）
  const hintPatches = patches.filter((p) => p.impact === 'no_source_fact');
  const hintItems: EditImpactObjectItemVM[] = hintPatches.map((p) => ({
    key: p.patch_ref,
    label: '无来源新事实',
    tone: 'danger' as BadgeTone,
    detail: diffSnippet(p.before_text, p.after_text),
  }));

  // 章节结构组：impact = index_structure（触及章节结构 → 需回索引编排重新生成）。
  // 专有分支优先：即便补丁带可 join 的 bound_item_ref/reflow_item_ref，也一律归本组、
  // 不并入章节组，以保留「回索引编排」这一处置动作不被抹平（对齐 F-1 修复方向 a）。
  const structurePatches = patches.filter((p) => p.impact === 'index_structure');
  const structureItems: EditImpactObjectItemVM[] = structurePatches.map((p) => ({
    key: p.patch_ref,
    label: p.bound_item_ref ?? p.reflow_item_ref ?? '章节结构',
    tone: 'danger' as BadgeTone,
    detail: `${EDIT_IMPACT_DESC.index_structure}｜${diffSnippet(p.before_text, p.after_text)}`,
  }));

  const groups: EditImpactGroupVM[] = [
    { key: 'section', title: '影响的章节', tone: 'warning', count: sectionItems.length, items: sectionItems, emptyText: '未触及任何章节内容' },
    { key: 'item', title: '涉及的确认态条目', tone: 'warning', count: itemItems.length, items: itemItems, emptyText: '未触及已绑定条目' },
    { key: 'chart', title: '受控图表', tone: 'danger', count: chartItems.length, items: chartItems, emptyText: '未触及图表源码' },
    { key: 'hint', title: '待处理提示', tone: 'danger', count: hintItems.length, items: hintItems, emptyText: '无待处理提示' },
    { key: 'structure', title: '章节结构（回索引编排）', tone: 'danger', count: structureItems.length, items: structureItems, emptyText: '未触及章节结构' },
  ];

  const totalChanges = patches.length;
  const needsReview = patches.filter(
    (p) => p.impact === 'confirmed_item' || p.impact === 'no_source_fact' || p.impact === 'other_asset' || p.impact === 'index_structure',
  ).length;
  const overviewText =
    `${totalChanges} 处改动 · ${sectionItems.length} 章节 · ${itemItems.length} 条目 · ${chartItems.length} 图表 · 待复核 ${needsReview}`;
  const verdict = needsReview > 0
    ? { label: '需复核', tone: 'warning' as BadgeTone }
    : { label: '可定稿', tone: 'success' as BadgeTone };

  return {
    totalChanges,
    sectionCount: sectionItems.length,
    itemCount: itemItems.length,
    chartCount: chartItems.length,
    needsReview,
    overviewText,
    verdict,
    groups,
  };
}

// ---- 简易 Markdown 渲染（预览用；标题/加粗/列表/段落）----

export function renderMarkdownHtml(markdown: string): string {
  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const inline = (s: string) => escape(s).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  const html: string[] = [];
  let fenceLines: string[] | null = null;
  let fenceStart = 0;
  let tableRows: string[][] | null = null;
  let tableStart = 0;

  // 注：图形围栏（mermaid/plantuml）的真图渲染走 React 组件（见 MarkdownPreview）；
  // 本纯字符串渲染器只服务无围栏文本片段（追溯片段/测试），围栏统一按等宽源码块输出。
  // 每个块级元素带 data-line=源码行号（0 基），供源码/预览滚动联动做行映射
  const flushTable = () => {
    if (!tableRows || tableRows.length === 0) {
      tableRows = null;
      return;
    }
    const [head, ...body] = tableRows;
    const cells = (row: string[], tag: string) =>
      row.map((cell) => `<${tag}>${inline(cell)}</${tag}>`).join('');
    html.push(
      `<table data-line="${tableStart}"><thead><tr>${cells(head, 'th')}</tr></thead><tbody>${body
        .map((row) => `<tr>${cells(row, 'td')}</tr>`)
        .join('')}</tbody></table>`,
    );
    tableRows = null;
  };

  const lines = markdown.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trim();
    if (line.startsWith('```')) {
      flushTable();
      if (fenceLines === null) {
        fenceLines = [];
        fenceStart = i;
      } else {
        html.push(`<pre data-line="${fenceStart}"><code>${escape(fenceLines.join('\n'))}</code></pre>`);
        fenceLines = null;
      }
      continue;
    }
    if (fenceLines !== null) {
      fenceLines.push(raw);
      continue;
    }
    if (line.startsWith('|')) {
      const cells = line.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
      if (cells.every((c) => /^[-: ]*$/.test(c))) continue; // 分隔行
      if (!tableRows) {
        tableRows = [];
        tableStart = i;
      }
      tableRows.push(cells);
      continue;
    }
    flushTable();
    if (!line || line.startsWith('<!--')) continue;
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 6);
      html.push(`<h${level} data-line="${i}">${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      html.push(`<li data-line="${i}">${inline(line.replace(/^[-*]\s+/, ''))}</li>`);
      continue;
    }
    html.push(`<p data-line="${i}">${inline(line)}</p>`);
  }
  flushTable();
  if (fenceLines !== null) {
    html.push(`<pre data-line="${fenceStart}"><code>${escape(fenceLines.join('\n'))}</code></pre>`);
  }
  return html.join('\n');
}


// ---- 参考资料章节撰稿：引用行插入与起草依据提示（T20260721）----

/** 一条可插入撰稿的引用条目（只取插入需要的两个字段，避免 VM 依赖设置域的完整 DTO）。 */
export interface CitableStandard {
  code: string;
  title: string;
}

/**
 * 撰稿框现有文本里已用到的最大引用序号 +1，即下一条该从几号排起；一条都没有则从 1 起。
 *
 * 这是「接着已有编号往下排」这条规则的唯一实现处（2026-07-22 用户拍板）：分两次选取也不会
 * 出现两个 [1]。只认行首（允许前置空白）的 `[数字]`，正文里顺带提到的方括号数字不算。
 */
export function nextCitationNumber(text: string): number {
  let max = 0;
  for (const match of (text || '').matchAll(/^[ \t]*\[(\d+)\]/gm)) {
    max = Math.max(max, Number(match[1]));
  }
  return max + 1;
}

/** 引用行的统一格式：`[序号] 标准号 名称`。格式只在这里定义一处。 */
export function formatCitationLines(entries: CitableStandard[], startNo: number): string {
  return entries
    .map((entry, at) => `[${startNo + at}] ${entry.code} ${entry.title}`.trim())
    .join('\n');
}

/**
 * 把引用行插进撰稿框光标处，返回新文本与插入后的光标位置。
 *
 * 前后按需补换行：引用行必须自成一行，否则会粘到用户正在写的那句话尾巴上。光标落在插入内容
 * 之后，接着输入不会顶开刚插入的引用。
 */
export function insertCitations(
  text: string,
  lines: string,
  cursor: number | null,
): { text: string; caret: number } {
  const source = text || '';
  const at = Math.min(Math.max(cursor ?? source.length, 0), source.length);
  const before = source.slice(0, at);
  const after = source.slice(at);
  const block = `${before && !before.endsWith('\n') ? '\n' : ''}${lines}${
    after && !after.startsWith('\n') ? '\n' : ''
  }`;
  return { text: before + block + after, caret: (before + block).length };
}

/**
 * 零依据章节的点击前提示：两项依据都为 0 时给一句话，否则不提示。
 *
 * 依据计数来自后端（口径同起草服务实际喂给模型的输入）。计数缺失（老版本响应）时不提示——
 * 宁可不提示，也不能凭前端猜测报一个可能不准的数。
 */
export function draftBasisHint(basis: SectionDraftBasisRead | undefined): string | null {
  if (!basis || basis.asset_count > 0 || basis.example_count > 0) {
    return null;
  }
  return '本章节没有可作依据的内容：关联需求资产 0 条、章节样例 0 条。AI 起草通常会拒绝。';
}
