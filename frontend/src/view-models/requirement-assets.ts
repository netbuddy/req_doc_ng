import type {
  AssetCatalogRead,
  AssetDetailRead,
  ItemMaintenanceItemRead,
} from '../api/assets';
import type { WorkbenchKey } from './app-shell';
import type { ActionVM, BadgeTone, SidePanelSectionVM } from './common';
import { nodeStatusMeta, subLabelText } from './trace-graph';
import { formatAbsoluteTime } from './time';
import { requirementItemStatusMeta, requirementItemTypeText } from './requirement-item-formation';
import type { RequirementItemType } from '../api/item-formation';
import {
  ELEMENT_TYPE_META,
  KNOWLEDGE_CATEGORY_META,
  KNOWLEDGE_CATEGORY_ORDER,
  elementTypeMeta,
  type KnowledgeCategory,
} from './requirement-analysis';

// ---- 稳定码 → 展示标签（中文是展示层，稳定码是跨线契约）----

/**
 * 资产口径（04A §5，2026-07-07 整合修订）：树只组织五类资产；
 * 追溯关系（资产间的边）与问题项（过程治理对象）不入树。
 * meta 保留 trace_link/issue 标签仅供详情兜底展示，不参与树构建。
 */
export const ASSET_TYPE_META: Record<string, { label: string; order: number }> = {
  material: { label: '材料', order: 1 },
  element: { label: '知识项', order: 2 },
  requirement_item: { label: '需求条目', order: 3 },
  chart: { label: '图表', order: 4 },
  document: { label: '文档', order: 5 },
  trace_link: { label: '追溯关系', order: 98 },
  issue: { label: '问题项', order: 99 },
};

/** 五类入树资产（04A §5 资产口径）。 */
export const NAV_ASSET_TYPES = ['material', 'element', 'requirement_item', 'chart', 'document'] as const;

const EXTRA_STATUS_META: Record<string, { label: string; tone: BadgeTone }> = {
  // 材料接入结论（LDM-003）
  accepted: { label: '已接入', tone: 'success' },
  returned_for_supplement: { label: '需补充', tone: 'warning' },
  excluded: { label: '已排除', tone: 'neutral' },
  // 追溯关系（LDM-013）
  effective: { label: '有效', tone: 'success' },
  pre_established: { label: '预建立', tone: 'processing' },
  suspect_pending_review: { label: '可疑待复核', tone: 'warning' },
  invalid: { label: '失效', tone: 'neutral' },
  // 问题项（LDM-011）
  pending: { label: '待处理', tone: 'warning' },
  resolved: { label: '已处理', tone: 'success' },
};

export function assetStatusMeta(
  status: string | null | undefined,
): { label: string; tone: BadgeTone } | null {
  if (!status) return null;
  return EXTRA_STATUS_META[status] ?? nodeStatusMeta('material', status);
}

const ATTRIBUTE_LABELS: Record<string, string> = {
  source_note: '来源备注',
  created_at: '创建时间',
  derived_elements: '派生要素数',
  element_type: '要素类型',
  referenced_by: '被条目引用',
  updated_at: '最近更新',
  req_no: '条目编号',
  req_type: '语义类型',
  source_elements: '来源要素数',
  chart_coverage: '图表覆盖数',
  in_document_index: '已入文档索引',
  revisions: '修订记录数',
  trace_effective: '有效追溯数',
  trace_suspect: '可疑追溯数',
  chart_type: '图表类型',
  covered_items: '覆盖条目数',
  upstream: '上游',
  downstream: '下游',
  status_reason: '状态原因',
  index_version: '索引版本',
  index_entries: '索引条目数',
  issue_type: '问题类型',
  origin_kind: '来源环节',
};

const RELATION_KIND_LABELS: Record<string, string> = {
  derived_element: '派生要素',
  source_material: '来源材料',
  referenced_by_item: '被条目引用',
  covered_by_chart: '图表覆盖',
  covers_item: '覆盖条目',
  upstream: '上游',
  downstream: '下游',
};

function attributeValueText(key: string, value: string): string {
  if (key === 'in_document_index') return value === 'yes' ? '是' : '否';
  if (key === 'element_type' || key === 'req_type' || key === 'issue_type') {
    return subLabelText(value) ?? value;
  }
  // 值域恒 ∈ {ISO 串, 字面量 "—"}(后端 _iso(x) or "—"):后者不可解析,与占位同文,故 key 白名单已足。
  if (key === 'updated_at' || key === 'created_at') return formatAbsoluteTime(value);
  return value;
}

// ---- 资产导航树（04A §3.1 文件夹树形态）/ 详情 VM ----

export interface AssetNavLeafVM {
  /** `assetType:ref`，选中与详情读取的稳定键。 */
  key: string;
  assetType: string;
  ref: string;
  /** 条目叶子的编号（REQ-xx）；非条目叶子为 null。 */
  idText: string | null;
  title: string;
  statusText: string | null;
  statusTone: BadgeTone;
  warnings: string[];
  /** 最新诊断轮质量分（无诊断/无画像为 null，不伪造） */
  qualityScore?: number | null;
  /** 最新诊断轮最重严重度 high/medium/low（无发现项为 null） */
  qualityAlert?: string | null;
}

export interface AssetNavSubgroupVM {
  key: string;
  label: string;
  count: number;
  leaves: AssetNavLeafVM[];
}

export interface AssetNavGroupVM {
  key: string;
  assetType: string;
  label: string;
  count: number;
  /** 非条目分组的直接叶子。 */
  leaves: AssetNavLeafVM[];
  /** 需求条目分组按语义类型分层的子分组。 */
  subgroups: AssetNavSubgroupVM[];
}

const ITEM_SUBGROUP_ORDER: string[] = ['functional', 'quality', 'constraint', 'data', 'interface'];

/**
 * 资产导航树：五类分组过滤 + 需求条目按语义类型子分组。
 * 条目叶子来自维护列表投影（承接筛选/搜索/完备警示），非条目叶子来自资产目录。
 */
export function buildAssetNavVM(
  catalog: AssetCatalogRead,
  items: ItemMaintenanceItemRead[],
): AssetNavGroupVM[] {
  const groups = new Map((catalog.groups ?? []).map((g) => [g.asset_type, g]));
  return NAV_ASSET_TYPES.map((assetType) => {
    const group = groups.get(assetType);
    if (assetType === 'requirement_item') {
      const byType = new Map<string, ItemMaintenanceItemRead[]>();
      for (const item of items) {
        const list = byType.get(item.req_type) ?? [];
        list.push(item);
        byType.set(item.req_type, list);
      }
      const typeKeys = [
        ...ITEM_SUBGROUP_ORDER.filter((t) => byType.has(t)),
        ...[...byType.keys()].filter((t) => !ITEM_SUBGROUP_ORDER.includes(t)),
      ];
      return {
        key: `group:${assetType}`,
        assetType,
        label: ASSET_TYPE_META[assetType].label,
        count: group?.count ?? items.length,
        leaves: [],
        subgroups: typeKeys.map((reqType) => {
          const children = byType.get(reqType) ?? [];
          return {
            key: `subgroup:${reqType}`,
            label: requirementItemTypeText(reqType as RequirementItemType),
            count: children.length,
            leaves: children.map((item) => {
              const status = requirementItemStatusMeta(item.status);
              const warnings: string[] = [];
              if (item.verification_missing) warnings.push('缺验收准则');
              if (item.priority_missing) warnings.push('缺优先级');
              return {
                key: nodeKeyOf(assetType, item.ref),
                assetType,
                ref: item.ref,
                idText: item.req_no,
                qualityScore: item.quality_score ?? null,
                qualityAlert: item.quality_alert ?? null,
                title: item.expression,
                statusText: status.label,
                statusTone: status.tone,
                warnings,
              };
            }),
          };
        }),
      };
    }
    if (assetType === 'element') {
      // 知识项组按两翼分层（05 §1）：恰好两子组「需求知识/业务知识」，翼内按
      // element_type 声明序二级排序；翼归属派生自 ELEMENT_TYPE_META（单一来源），
      // 两子组叶子数之和守恒 = 扁平叶子总数（AC-P2-01）。
      const nodes = group?.nodes ?? [];
      const typeOrder = Object.keys(ELEMENT_TYPE_META);
      const byCat = new Map<KnowledgeCategory, typeof nodes>();
      for (const node of nodes) {
        const cat = elementTypeMeta(node.sub_label ?? '').category;
        const list = byCat.get(cat) ?? [];
        list.push(node);
        byCat.set(cat, list);
      }
      return {
        key: `group:${assetType}`,
        assetType,
        label: ASSET_TYPE_META[assetType].label,
        count: group?.count ?? nodes.length,
        leaves: [],
        subgroups: KNOWLEDGE_CATEGORY_ORDER.map((cat) => {
          const children = [...(byCat.get(cat) ?? [])].sort(
            (a, b) => typeOrder.indexOf(a.sub_label ?? '') - typeOrder.indexOf(b.sub_label ?? ''),
          );
          return {
            key: `subgroup:${cat}`,
            label: KNOWLEDGE_CATEGORY_META[cat].shortLabel,
            count: children.length,
            leaves: children.map((node) => {
              const status = assetStatusMeta(node.status);
              return {
                key: nodeKeyOf(assetType, node.ref),
                assetType,
                ref: node.ref,
                idText: elementTypeMeta(node.sub_label ?? '').label,
                title: node.label,
                statusText: status?.label ?? null,
                statusTone: status?.tone ?? 'neutral',
                warnings: [],
              };
            }),
          };
        }),
      };
    }
    return {
      key: `group:${assetType}`,
      assetType,
      label: ASSET_TYPE_META[assetType].label,
      count: group?.count ?? 0,
      leaves: (group?.nodes ?? []).map((node) => {
        const status = assetStatusMeta(node.status);
        return {
          key: nodeKeyOf(assetType, node.ref),
          assetType,
          ref: node.ref,
          idText: null,
          title: node.label,
          statusText: status?.label ?? null,
          statusTone: status?.tone ?? 'neutral',
          warnings: [],
        };
      }),
      subgroups: [],
    };
  });
}

export interface AssetFactRowVM {
  label: string;
  value: string;
}

export interface AssetRelationRowVM {
  kindText: string;
  label: string;
  assetType: string;
  ref: string;
}

export interface AssetDetailVM {
  ref: string;
  title: string;
  typeText: string;
  summaryText: string;
  statusText: string | null;
  statusTone: BadgeTone;
  facts: AssetFactRowVM[];
  relations: AssetRelationRowVM[];
}

/** 上下文动作：无 targetWorkbench 的动作必须 disabled（目标工作面未落地）。 */
export interface AssetContextActionVM extends ActionVM {
  targetWorkbench?: WorkbenchKey;
}

/**
 * 上下文动作是稳定视图配置（04A §3.1：折叠在非条目资产详情卡内，只导航或提交候选意图）。
 * 整合修订：原「去需求维护」撤销（条目维护即本视图）；「转问题项」随资产口径收敛移除（04A §5）。
 */
export const ASSET_CONTEXT_ACTIONS: AssetContextActionVM[] = [
  { key: 'go-traceability', label: '去追溯分析', iconKey: 'traceability', targetWorkbench: 'traceability' },
  { key: 'go-diagram', label: '去图表核对', iconKey: 'diagram', targetWorkbench: 'diagram' },
  { key: 'go-release', label: '去发布编排', iconKey: 'release', targetWorkbench: 'release' },
];

export function resolveAssetActionTarget(action: AssetContextActionVM): WorkbenchKey | null {
  if (action.disabled || !action.targetWorkbench) {
    return null;
  }
  return action.targetWorkbench;
}

export function nodeKeyOf(assetType: string, ref: string): string {
  return `${assetType}:${ref}`;
}

export function parseNodeKey(key: string): { assetType: string; ref: string } | null {
  const idx = key.indexOf(':');
  if (idx <= 0) return null;
  const assetType = key.slice(0, idx);
  if (!(assetType in ASSET_TYPE_META)) return null;
  return { assetType, ref: key.slice(idx + 1) };
}

export function buildAssetDetailVM(detail: AssetDetailRead): AssetDetailVM {
  const status = assetStatusMeta(detail.status);
  return {
    ref: detail.ref,
    title: detail.label,
    typeText: [
      ASSET_TYPE_META[detail.asset_type]?.label ?? detail.asset_type,
      detail.sub_label ? (subLabelText(detail.sub_label) ?? detail.sub_label) : null,
    ]
      .filter(Boolean)
      .join(' · '),
    summaryText: detail.summary ?? '',
    statusText: status?.label ?? null,
    statusTone: status?.tone ?? 'neutral',
    facts: (detail.attributes ?? []).map((a) => ({
      label: ATTRIBUTE_LABELS[a.key] ?? a.key,
      value: attributeValueText(a.key, a.value),
    })),
    relations: (detail.relations ?? []).map((r) => ({
      kindText: RELATION_KIND_LABELS[r.kind] ?? r.kind,
      label: r.label,
      assetType: r.asset_type,
      ref: r.ref,
    })),
  };
}

/** 树底追溯摘要条（04A §5：追溯关系是资产间的边，本视图只保留计数与跳转入口）。 */
export function buildTraceSummarySection(catalog: AssetCatalogRead): SidePanelSectionVM {
  const ts = catalog.trace_summary;
  return {
    key: 'trace',
    title: '追溯摘要',
    items: [
      { key: 'effective', label: '有效', value: String(ts.effective), tone: 'success' },
      { key: 'pre', label: '预建立', value: String(ts.pre_established), tone: 'processing' },
      { key: 'suspect', label: '可疑', value: String(ts.suspect), tone: 'warning' },
      { key: 'invalid', label: '失效', value: String(ts.invalid), tone: 'neutral' },
    ],
    actionLabel: '去追溯分析',
  };
}
