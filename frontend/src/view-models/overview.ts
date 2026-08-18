import type { WorkbenchKey } from './app-shell';

export type OverviewTone = 'blue' | 'green' | 'orange' | 'purple' | 'red' | 'gray';

export interface OverviewProjectRowVM {
  id: string;
  name: string;
  dateText?: string;
}

export interface OverviewSelectedProjectVM {
  id: string;
  name: string;
  scope: string;
  /** 治理目标/背景（LDM-001.background；缺省"未填写"）。 */
  goal: string;
  /** P6b 业务领域档案中文名（缺省"通用"，只读）。 */
  domainProfileLabel: string;
  createdText: string;
  /** 无后端数据源的字段（成员/团队/创建人）统一以此标注，不显示假值。 */
  deferredFacts: string;
}

export interface OverviewAssetMetricVM {
  key: string;
  label: string;
  value: string;
  tone: OverviewTone;
  targetWorkbench: WorkbenchKey;
}

export interface OverviewStatMetricVM {
  key: string;
  label: string;
  value: string;
  tone: OverviewTone;
  targetWorkbench: WorkbenchKey;
}

export interface OverviewCoverageMetricVM extends OverviewStatMetricVM {
  percent: number;
}

export interface OverviewAiStageMetricVM {
  key: string;
  stage: string;
  accepted: string;
  revised: string;
  rejected: string;
  issue: string;
  targetWorkbench: WorkbenchKey;
  /** 样本不足（收口明细 <5）：显示但灰化标注（口径设计 §5.1）。 */
  insufficient?: boolean;
}

export interface OverviewCalibrationPointVM {
  x: number; // 平均置信度（0-100）
  y: number; // 实际采纳率（0-100）
  count: number;
}

export interface OverviewCalibrationVM {
  eceText: string;
  ratingText: string;
  sampleText: string;
  points: OverviewCalibrationPointVM[];
}

export interface OverviewAiCoverageLegendVM {
  touched: string;
  untouched: string;
  notApplicable: string;
  total: string;
}

export interface OverviewRiskSignalVM {
  key: string;
  label: string;
  level: string;
  levelTone: OverviewTone;
  value: string;
  targetWorkbench: WorkbenchKey;
}

/** 交付失败矩阵单格：某 lane 在某失败关卡的计数（0 由展示层渲染为「·」；口径 §5.5）。 */
export interface OverviewDeliveryFailureCellVM {
  failureStage: string;
  count: number;
}

/** 交付失败块行：一个 lane 的失败率 + 分数 + 按失败关卡分桶（口径 §5.5）。 */
export interface OverviewDeliveryFailureRowVM {
  key: string;        // lane 稳定码
  laneLabel: string;
  rateText: string;   // 失败率百分比（分数=failed/total）
  ratePercent: number;
  failed: number;     // 失败数（排序用数值，勿从 scoreText 反解）
  scoreText: string;  // "失败数 / 判定数"
  tone: OverviewTone; // 失败率严重度色（复用语义令牌，阈值暂定）
  cells: OverviewDeliveryFailureCellVM[]; // 按 DELIVERY_FAILURE_STAGE_ORDER 对齐
}

export interface OverviewBoundaryItemVM {
  key: string;
  title: string;
  description: string;
  tone: OverviewTone;
}

// ---- 需求转化链 / 数字桥 / 状态对账（任务卡 T20260724-overview-conversion-chain）----

/**
 * 转化链与数字桥的取色。与 OverviewTone 分开定义，是因为原型给「条目形成」这一段用了
 * 靛蓝（indigo），而 OverviewTone 六色里没有它；另起一个联合类型可避免加宽 OverviewTone
 * 影响既有消费方。对应的 .ovc-tone-* 文本色在 styles-overview-chain.css。
 */
export type OverviewChainTone = 'blue' | 'green' | 'orange' | 'indigo' | 'gray';

/** 主数字旁的对照数字（阶段二的「待确认」、阶段三的「尚未形成」）。 */
export interface OverviewChainCounterVM {
  label: string;
  value: string;
  tone: OverviewChainTone;
}

/** 节点内的拆分小字（阶段一的需求类/非需求类、产出节点的三个状态）。 */
export interface OverviewChainPartVM {
  label: string;
  value: string;
  tone?: OverviewChainTone;
}

export interface OverviewChainNodeVM {
  key: 'recognition' | 'confirmation' | 'formation' | 'output';
  stageLabel: string;      // 阶段一 / 阶段二 / 阶段三 / 产出
  title: string;
  value: string;
  valueTone: OverviewChainTone;
  valueName: string;
  counter: OverviewChainCounterVM | null;
  parts: OverviewChainPartVM[];
  /** 进度条百分比；null＝该节点不画进度条。 */
  percent: number | null;
  progressText: string | null;
  /** 门禁注脚；null＝该节点当前没有可说的注脚（见产出节点的直建口径）。 */
  gateHint: string | null;
  targetWorkbench: WorkbenchKey;
}

/** 数字桥一行里的一个去向分量（整句已在装配时拼好，展示层只负责取色）。 */
export interface OverviewBridgePartVM {
  text: string;
  tone?: OverviewChainTone;
}

/** 数字桥一行：左侧粗体小结 + 运算符 + 若干去向分量。 */
export interface OverviewBridgeRowVM {
  key: string;
  head: string;
  /** ＝ 表示行内加总闭合；→ 表示跨对象的形成映射（左边数知识项、右边数条目），不是等式。 */
  operator: '＝' | '→';
  parts: OverviewBridgePartVM[];
}

export interface OverviewTypeBridgeVM {
  key: string;
  label: string;
  rows: OverviewBridgeRowVM[];
  /** 该类型无知识项时给白话空态，不摆 0÷0 式空账。 */
  emptyText: string | null;
  /** 结论句；null＝该类型知识项与条目都为 0，没有去向可对照，整句不渲染。 */
  conclusion: string | null;
}

/** 类型瓦片的确认进度小字（统计对象是知识项，含未确认）。 */
export interface OverviewTypeConfirmVM {
  key: string;
  confirmedText: string;
  pendingText: string;
  percent: number;
}

/** 状态三瓦片与资产盘点的对账行。 */
export interface OverviewStatusReconciliationVM {
  /** 「81＋25＋3＝109」。 */
  equationText: string;
  /** 「＝资产盘点「需求条目」✓」；不等时改为白话提示。 */
  resultText: string;
  balanced: boolean;
}

export interface OverviewFlowStageChipVM {
  stage: string;
  label: string;
  statusText: string;
  tone: OverviewTone;
  detail?: string | null;
}

export interface OverviewFlowRowVM {
  flowId: string;
  title: string;
  summary: string;
  updatedText: string;
  resumable: boolean;
  /** 终结态（需补充/已排除）：可继续编辑（预填重提）/可放弃（软删）；死路行仍仅可查看。 */
  dismissable: boolean;
  stages: OverviewFlowStageChipVM[];
}

export interface OverviewWorkbenchVM {
  projectList: OverviewProjectRowVM[];
  selectedProject: OverviewSelectedProjectVM;
  assetMetrics: OverviewAssetMetricVM[];
  requirementTypeMetrics: OverviewStatMetricVM[];
  requirementStatusMetrics: OverviewStatMetricVM[];
  coverageMetrics: OverviewCoverageMetricVM[];
  traceabilityMetrics: OverviewStatMetricVM[];
  aiStageMetrics: OverviewAiStageMetricVM[];
  aiCoverage: OverviewCoverageMetricVM;
  aiCoverageLegend: OverviewAiCoverageLegendVM;
  aiCalibration: OverviewCalibrationVM | null;
  aiRiskSignals: OverviewRiskSignalVM[];
  /** 交付失败块（lane × 失败关卡；口径 §5.5）；空数组=窗口内无判定行/未接入。 */
  deliveryFailures: OverviewDeliveryFailureRowVM[];
  boundaryItems: OverviewBoundaryItemVM[];
  /** 新增需求流程阶段面板（AEP-072 只读投影）；null=加载中/不可用。 */
  flows: OverviewFlowRowVM[] | null;
  /** 需求转化链四节点；null＝加载中/不可用（不显示空链）。 */
  conversionChain: OverviewChainNodeVM[] | null;
  /** 五类型的数字桥，一次下发；点类型瓦片只切换展示，不再请求。 */
  typeBridges: OverviewTypeBridgeVM[];
  /** 类型瓦片的确认进度小字。 */
  typeConfirmations: OverviewTypeConfirmVM[];
  /** 状态三瓦片的对账行；null＝无数据。 */
  statusReconciliation: OverviewStatusReconciliationVM | null;
  /** 覆盖度/追溯与风险已接真实数据（false=保持待接入占位与标注）。 */
  coverageReady: boolean;
  traceReady: boolean;
  aiReady: boolean;
  /** 延期指标组（AI 效能）的"待接入"标注文案。 */
  deferredNote: string;
}

// ---- 后端只读投影 → VM 映射（AEP-052/072；tone/label 归展示层，此处定义）----

import type {
  AiDeliveryFailureRead,
  AiEffectivenessRead,
  AiStageEffectRead,
} from '../api/ai-effectiveness';
import type { OverviewRead, RequirementFlowRead } from '../api/overview';
import type { ProjectDetailRead, ProjectRead } from '../api/projects';

const FLOW_STAGE_LABELS: Record<string, string> = {
  intake: '材料接入',
  analysis: '知识抽取',
  itemFormation: '条目形成',
  itemReview: '条目评审',
};

const FLOW_STATUS_META: Record<string, { text: string; tone: OverviewTone }> = {
  done: { text: '完成', tone: 'green' },
  in_progress: { text: '进行中', tone: 'blue' },
  stopped: { text: '停靠', tone: 'orange' },
  not_started: { text: '未开始', tone: 'gray' },
};

function pad(n: number): string {
  return `${n}`.padStart(2, '0');
}

function formatFlowUpdatedText(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatDateText(iso: string | null | undefined): string {
  if (!iso) {
    return '—';
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function toOverviewFlowRowVM(flow: RequirementFlowRead): OverviewFlowRowVM {
  return {
    flowId: flow.flow_id,
    title: flow.title,
    summary: flow.summary ?? '',
    updatedText: formatFlowUpdatedText(flow.updated_at),
    resumable: flow.resumable,
    dismissable: flow.dismissable ?? false,
    stages: flow.stages.map((stage) => ({
      stage: stage.stage,
      label: FLOW_STAGE_LABELS[stage.stage] ?? stage.stage,
      // 条目评审未上线：恒显示「待接入」而非「未开始」（不显示虚假进度）
      statusText:
        stage.stage === 'itemReview' && stage.status === 'not_started'
          ? '待接入'
          : FLOW_STATUS_META[stage.status]?.text ?? stage.status,
      tone: FLOW_STATUS_META[stage.status]?.tone ?? 'gray',
      detail: stage.detail,
    })),
  };
}

/** 真实项目 → 选中项目卡（无来源字段一律标注待接入，不显示假值）。 */
export function toSelectedProjectVM(project: ProjectDetailRead): OverviewSelectedProjectVM {
  return {
    id: project.id,
    name: project.name,
    scope: project.scope ?? '未填写',
    goal: project.background ?? '未填写',
    domainProfileLabel: project.domain_profile_label || '通用',
    createdText: formatDateText(project.created_at),
    deferredFacts: '成员/团队/创建人：待接入（项目上下文服务）',
  };
}

/** 资产盘点卡 key → 后端计数 key（AEP-052）。 */
const ASSET_METRIC_KEYS: Record<string, string> = {
  materials: 'materials',
  requirements: 'items',
  diagrams: 'charts',
  documents: 'documents',
  issues: 'issues',
};

/** 覆盖度卡 key → 追溯覆盖方向 key（AEP-062 口径，经总览转投影）。 */
const COVERAGE_METRIC_KEYS: Record<string, string> = {
  'source-coverage': 'item_source',
  'diagram-coverage': 'item_chart',
  'document-coverage': 'item_document',
};

const TRACE_RISK_TONES: Record<string, OverviewTone> = {
  'trace-gap': 'red',
  'suspicious-links': 'orange',
  'issue-items': 'red',
};

/** 原型四行 ← stage 聚合映射（口径设计 §4 末段；图表两环节归"查看更多分析"）。 */
const AI_STAGE_ROWS: { key: string; stages: string[] }[] = [
  { key: 'material-intake', stages: ['source_intake'] },
  { key: 'analysis', stages: ['element_recognition', 'element_review', 'element_execution'] },
  { key: 'item-formation', stages: ['item_formation'] },
  { key: 'item-review', stages: ['item_diagnosis'] },
];

const AI_RISK_KEYS: Record<string, string> = {
  'low-confidence': 'low_confidence',
  'rejection-rising': 'rejection_rising',
  'issue-conversion': 'issue_conversion',
  'source-conflict': 'source_conflict',
};

const AI_LEVEL_META: Record<string, { label: string; tone: OverviewTone }> = {
  high: { label: '高', tone: 'red' },
  medium: { label: '中', tone: 'orange' },
  low: { label: '低', tone: 'green' },
  deferred: { label: '待接入', tone: 'gray' },
};

const AI_RATING_LABELS: Record<string, string> = {
  excellent: '优',
  good: '良好',
  fair: '一般',
  poor: '较差',
  insufficient: '样本不足',
};

// ---- 交付失败块（lane × 失败关卡；口径设计 §5.5）----

/** 失败关卡列的规范顺序（含未分关桶）；矩阵表头与单格按此对齐。 */
export const DELIVERY_FAILURE_STAGE_ORDER = [
  'parse',
  'llm_error',
  'structure',
  'aggregation',
  'synthesis',
  'unclassified',
] as const;

/** 失败关卡白话标签（禁自造行话；口径 §5.5）。 */
export const DELIVERY_FAILURE_STAGE_LABELS: Record<string, string> = {
  parse: '解析',
  llm_error: '模型错误',
  structure: '结构',
  aggregation: '聚合',
  synthesis: '综合',
  unclassified: '未分关',
};

/** lane 稳定码 → 白话环节名（细到 lane，便于定位待优化提示词）。 */
const DELIVERY_LANE_LABELS: Record<string, string> = {
  source_intake: '材料接入',
  element_recognition: '要素识别',
  element_review: '要素复核',
  element_execution: '要素执行',
  item_formation: '条目形成',
  item_diagnosis: '条目诊断',
  item_structure_recheck: '条目复核',
  chart_source_suggestion: '图表建议',
  chart_verification: '图表核对',
};

/** 失败率严重度色（复用语义令牌，阈值暂定，运行数据积累后复核）。 */
function deliveryFailureTone(percent: number): OverviewTone {
  if (percent > 20) return 'red';
  if (percent > 5) return 'orange';
  return 'green';
}

function toDeliveryFailureRowVM(row: AiDeliveryFailureRead): OverviewDeliveryFailureRowVM {
  const total = row.total ?? 0;
  const failed = row.failed ?? 0;
  const percent = total > 0 ? Math.round((failed / total) * 100) : 0;
  const counts = new Map(
    (row.by_failure_stage ?? []).map((c) => [c.failure_stage, c.count ?? 0]),
  );
  return {
    key: row.stage,
    laneLabel: DELIVERY_LANE_LABELS[row.stage] ?? row.stage,
    rateText: `${percent}%`,
    ratePercent: percent,
    failed,
    scoreText: `${failed} / ${total}`,
    tone: deliveryFailureTone(percent),
    cells: DELIVERY_FAILURE_STAGE_ORDER.map((failureStage) => ({
      failureStage,
      count: counts.get(failureStage) ?? 0,
    })),
  };
}

/** 交付失败行装配：失败率降序（高失败 lane 前置），并列按失败数、环节名稳定排序。 */
export function buildDeliveryFailures(ai: AiEffectivenessRead): OverviewDeliveryFailureRowVM[] {
  return (ai.delivery_failures ?? [])
    .map(toDeliveryFailureRowVM)
    .sort(
      (a, b) =>
        b.ratePercent - a.ratePercent ||
        b.failed - a.failed ||
        a.laneLabel.localeCompare(b.laneLabel),
    );
}

function aiStageRow(base: OverviewAiStageMetricVM, stages: AiStageEffectRead[]): OverviewAiStageMetricVM {
  const total = stages.reduce((s, x) => s + x.total, 0);
  if (total === 0) {
    return { ...base, accepted: '—', revised: '—', rejected: '—', issue: '—', insufficient: true };
  }
  const pct = (n: number) => `${Math.round((n / total) * 100)}%`;
  return {
    ...base,
    accepted: pct(stages.reduce((s, x) => s + x.adopted, 0)),
    revised: pct(stages.reduce((s, x) => s + x.adopted_with_revision, 0)),
    rejected: pct(stages.reduce((s, x) => s + x.rejected, 0)),
    issue: pct(stages.reduce((s, x) => s + x.transferred_to_issue, 0)),
    insufficient: total < 5,
  };
}

// ---- 转化链 / 数字桥 / 对账行装配（文案依据原型 v2；数字全部来自同一次后端响应）----

type ConversionChainRead = NonNullable<OverviewRead['conversion_chain']>;
type TypeBridgeRead = NonNullable<OverviewRead['type_bridge']>[number];

/**
 * 进度条与进度文案共用的百分比取整。
 *
 * 两个边界不做四舍五入，因为取整后的数字会与它旁边照实打印的计数器互相矛盾：
 * 289/290 若显示 100%，进度条画满，而紧邻的「待确认 1」还在；1/300 若显示 0%，
 * 进度条空着，而已经有 1 个完成。所以只有真的全部完成才给 100，真的一个都没有才给 0。
 */
function percentOf(part: number, whole: number): number {
  if (whole <= 0) return 0;
  const rounded = Math.round((part / whole) * 100);
  if (rounded >= 100 && part < whole) return 99;
  if (rounded <= 0 && part > 0) return 1;
  return rounded;
}

/** 转化链四节点。门禁注脚与阶段用语按原型 v2 逐字落，「阶段」不写「工序」、「已有」不写「现役」。 */
export function buildConversionChain(chain: ConversionChainRead): OverviewChainNodeVM[] {
  const confirmPercent = percentOf(chain.elements_confirmed, chain.elements_requirement);
  const formPercent = percentOf(chain.materials_formed, chain.materials_with_requirement);
  return [
    {
      key: 'recognition',
      stageLabel: '阶段一',
      title: '识别产出 · 知识项',
      value: `${chain.elements_total}`,
      valueTone: 'blue',
      valueName: '已有知识项',
      counter: null,
      parts: [
        { label: '需求类', value: `${chain.elements_requirement}` },
        { label: '非需求类（角色/术语/场景等）', value: `${chain.elements_other}` },
      ],
      percent: null,
      progressText: null,
      gateHint: '仅需求类五种进入右侧转化；非需求类作分析上下文，不形成条目。',
      targetWorkbench: 'management',
    },
    {
      key: 'confirmation',
      stageLabel: '阶段二',
      title: '人工确认 · 需求类知识项',
      value: `${chain.elements_confirmed}`,
      valueTone: 'green',
      valueName: '已确认',
      counter: { label: '待确认', value: `${chain.elements_pending}`, tone: 'orange' },
      parts: [],
      percent: confirmPercent,
      progressText: `确认进度 ${chain.elements_confirmed}/${chain.elements_requirement}（${confirmPercent}%）`,
      gateHint: '确认是条目形成的门禁：未确认的知识项不会进入条目形成。',
      targetWorkbench: 'management',
    },
    {
      key: 'formation',
      stageLabel: '阶段三',
      title: '条目形成 · 按材料执行',
      value: `${chain.materials_formed}`,
      valueTone: 'indigo',
      valueName: '份材料已形成条目',
      counter: { label: '尚未形成', value: `${chain.materials_unformed}`, tone: 'orange' },
      parts: [],
      percent: formPercent,
      progressText: `识别出需求类知识项的材料共 ${chain.materials_with_requirement} 份`,
      gateHint: '形成时可拆分、归并知识项，条目数与知识项数不一一对应。',
      targetWorkbench: 'management',
    },
    {
      key: 'output',
      stageLabel: '产出',
      title: '需求条目（对账＝资产盘点）',
      value: `${chain.items_total}`,
      valueTone: 'green',
      valueName: '需求条目',
      counter: null,
      parts: [
        { label: '待确认', value: `${chain.items_pending}`, tone: 'orange' },
        { label: '已确认', value: `${chain.items_confirmed}`, tone: 'green' },
        { label: '已了结', value: `${chain.items_closed}` },
      ],
      percent: null,
      progressText: null,
      // 措辞裁定（2026-07-25）：判据只看条目有无来源引用，不校验来源知识项当前是否仍为已确认态，
      // 故不写「可回溯到已确认知识项」；产品文案也不出现演示库字样。
      //
      // 口径裁定（2026-07-25，冷审查 C3）：产品代码目前没有直建通道——条目形成、拆分、归并、
      // 重排四条写入路径都要求至少一个来源知识项，只有演示种子脚本能写出空来源。因此真实项目里
      // 这条脚注恒为「含 0 条」，是一处无信息量的界面元素；直建数为 0 时整条脚注隐去，
      // 大于 0（演示库）时照常显示。
      gateHint:
        chain.items_direct > 0
          ? `含 ${chain.items_direct} 条无知识项来源的直建条目；其余 ${chain.items_sourced} 条可回溯到知识项来源。`
          : null,
      targetWorkbench: 'management',
    },
  ];
}

/** 数字桥四行账 + 结论句（结论句措辞按原型，数字随类型替换）。 */
export function buildTypeBridges(
  chain: ConversionChainRead,
  bridges: TypeBridgeRead[],
  labels: Map<string, string>,
): OverviewTypeBridgeVM[] {
  return bridges.map((b) => {
    const label = labels.get(b.key) ?? b.key;
    // 残差措辞随事实选择：只要出现「材料已执行形成但该知识项未被采用」，
    // 就不能沿用原型的「所在材料尚未执行形成」，否则文案与事实不符。
    const notFormedText =
      b.not_formed_not_adopted > 0
        ? `${b.not_formed} 尚未形成条目（所在材料尚未执行 ${b.not_formed_material_pending} · 形成时未采用 ${b.not_formed_not_adopted}）`
        : `${b.not_formed} 所在材料尚未执行形成（停在阶段三）`;
    const rows: OverviewBridgeRowVM[] = [];
    if (b.elements_total > 0) {
      rows.push({
        key: 'existing',
        head: `${b.elements_total} 个已有${label}知识项`,
        operator: '＝',
        parts: [
          { text: `${b.elements_confirmed} 已确认`, tone: 'green' },
          { text: `${b.elements_pending} 待确认（停在阶段二，不能形成条目）`, tone: 'orange' },
        ],
      });
      rows.push({
        key: 'confirmed',
        head: `${b.elements_confirmed} 个已确认`,
        operator: '＝',
        parts: [
          { text: `${b.entered_formation} 已进入条目形成`, tone: 'indigo' },
          { text: notFormedText, tone: 'orange' },
        ],
      });
      rows.push({
        key: 'entered',
        head: `${b.entered_formation} 个进入形成`,
        operator: '→',
        parts: [
          { text: `${b.items_from_elements_same_type} 条${label}条目` },
          { text: `${b.items_from_elements_other_type} 条形成时被定为其它类型的条目` },
        ],
      });
    }
    if (b.items_total > 0 || b.elements_total > 0) {
      // 直建那一段只在真有直建条目时出现：产品代码尚无直建通道，恒印「＋0 直建」是噪声（C3 口径）。
      const itemParts: OverviewBridgePartVM[] = [{ text: `${b.items_sourced} 来自知识项` }];
      if (b.items_direct > 0) {
        itemParts.push({ text: `${b.items_direct} 直建（无知识项来源）` });
      }
      rows.push({
        key: 'items',
        head: `${b.items_total} 条${label}条目`,
        operator: '＝',
        parts: itemParts,
      });
    }
    // 空态判据只管「该类型没有知识项」这一件事。「下列条目均为直建」是对条目来源构成的断言，
    // 必须由 items_direct 与 items_total 相等来支撑：知识项数为 0 推不出条目全是直建——
    // 条目类型可人工改判，来源知识项也可能事后被撤销或因拆分归并而被排除，
    // 这些情况下条目仍带着来源引用，说「均为直建」就与它下方那行照实打印的拆分数字直接矛盾。
    let emptyText: string | null = null;
    if (b.elements_total === 0) {
      if (b.items_total === 0) {
        emptyText = `本项目暂无${label}知识项与${label}条目，尚无去向可算。`;
      } else if (b.items_direct === b.items_total) {
        emptyText = `本项目暂无${label}知识项，下列条目均为直建。`;
      } else {
        emptyText = `本项目暂无${label}知识项。`;
      }
    }
    // 结论句的落点是「X知识项 → X条目（去向如上）」，两边都是 0 时上方一行去向都没有，
    // 「去向如上」无所指，故此时整句不渲染。这是本装配对原型逐字文案唯一的一处有意偏离
    // （2026-07-25 用户拍板，冷审查 P1）；其余情形逐字保留原型措辞。
    const hasNothing = b.elements_total === 0 && b.items_total === 0;
    return {
      key: b.key,
      label,
      rows,
      emptyText,
      conclusion: hasNothing
        ? null
        : `${b.elements_total} 与 ${chain.items_total} 之间没有直接的算术关系：` +
          `${b.elements_total} 只统计「${label}」这一类知识项，${chain.items_total} 统计「全部五类」条目；` +
          '两个数字隔着「人工确认、条目形成」两个阶段，还隔着类型口径的差异。' +
          `真正应当对照的是：${label}知识项 ${b.elements_total} → ${label}条目 ${b.items_total}（去向如上），` +
          `需求类知识项 ${chain.elements_requirement} → 全部条目 ${chain.items_total}。`,
    };
  });
}

function buildTypeConfirmations(bridges: TypeBridgeRead[]): OverviewTypeConfirmVM[] {
  return bridges.map((b) => ({
    key: b.key,
    confirmedText: `${b.elements_confirmed}`,
    pendingText: `${b.elements_pending}`,
    percent: percentOf(b.elements_confirmed, b.elements_total),
  }));
}

/** 对账行：三块之和与资产盘点条目数比对；不等即报，不粉饰。 */
export function buildStatusReconciliation(
  chain: ConversionChainRead,
  assetItems: number | undefined,
): OverviewStatusReconciliationVM {
  const sum = chain.items_pending + chain.items_confirmed + chain.items_closed;
  const balanced = assetItems !== undefined && sum === assetItems;
  return {
    equationText: `${chain.items_pending}＋${chain.items_confirmed}＋${chain.items_closed}＝${sum}`,
    resultText: balanced
      ? '＝资产盘点「需求条目」✓'
      : `与资产盘点「需求条目」${assetItems ?? '—'} 不一致，请核对`,
    balanced,
  };
}

/**
 * 总览台 VM 装配：真实项目列表 + 真实投影覆盖可支撑指标组；
 * AI 效能来自 AEP-094（`ai=null` 时保持待接入占位，不显示假数）。
 * `read=null`（加载中/失败）时指标保持 base 占位，不显示假数。
 */
export function buildOverviewVM(
  base: OverviewWorkbenchVM,
  projects: ProjectRead[],
  read: OverviewRead | null,
  ai: AiEffectivenessRead | null = null,
): OverviewWorkbenchVM {
  const withProjects: OverviewWorkbenchVM = {
    ...base,
    projectList: projects.map((project) => ({
      id: project.id,
      name: project.name,
      dateText: formatDateText(project.created_at),
    })),
  };

  if (!read) {
    return withProjects;
  }

  const assetValues = new Map(read.asset_metrics.map((m) => [m.key, m.value]));
  const typeValues = new Map(read.requirement_type_metrics.map((m) => [m.key, m.value]));
  const statusValues = new Map(read.requirement_status_metrics.map((m) => [m.key, m.value]));
  const coverageValues = new Map((read.coverage ?? []).map((c) => [c.key, c]));
  const traceRisk = read.trace_risk ?? null;

  return {
    ...withProjects,
    assetMetrics: withProjects.assetMetrics.map((metric) => {
      const backendKey = ASSET_METRIC_KEYS[metric.key];
      const value = backendKey ? assetValues.get(backendKey) : undefined;
      return { ...metric, value: value === undefined ? '—' : `${value}` };
    }),
    requirementTypeMetrics: withProjects.requirementTypeMetrics.map((metric) => ({
      ...metric,
      value: `${typeValues.get(metric.key) ?? 0}`,
    })),
    requirementStatusMetrics: withProjects.requirementStatusMetrics.map((metric) => ({
      ...metric,
      value: `${statusValues.get(metric.key) ?? 0}`,
    })),
    ...buildConversionSection(withProjects, read, assetValues),
    coverageReady: coverageValues.size > 0,
    coverageMetrics: withProjects.coverageMetrics.map((metric) => {
      const direction = coverageValues.get(COVERAGE_METRIC_KEYS[metric.key] ?? '');
      if (!direction) {
        return metric;
      }
      const percent = Math.round(direction.ratio * 100);
      return { ...metric, value: `${percent}%`, percent };
    }),
    traceReady: traceRisk !== null,
    traceabilityMetrics: withProjects.traceabilityMetrics.map((metric) => {
      if (!traceRisk) {
        return metric;
      }
      const value =
        metric.key === 'trace-gap'
          ? traceRisk.gaps
          : metric.key === 'suspicious-links'
            ? traceRisk.suspects
            : traceRisk.issues;
      return { ...metric, value: `${value}`, tone: TRACE_RISK_TONES[metric.key] ?? metric.tone };
    }),
    flows: read.flows.map(toOverviewFlowRowVM),
    ...buildAiSection(withProjects, ai),
  };
}

/**
 * 转化链、数字桥、类型确认进度、对账行四者同源装配：数字全部取自同一次 overview 响应，
 * 展示层不再自行加总。后端未下发 conversion_chain（旧版本/不可用）时保持 base 占位。
 */
function buildConversionSection(
  base: OverviewWorkbenchVM,
  read: OverviewRead,
  assetValues: Map<string, number>,
): Pick<
  OverviewWorkbenchVM,
  'conversionChain' | 'typeBridges' | 'typeConfirmations' | 'statusReconciliation'
> {
  const chain = read.conversion_chain;
  if (!chain) {
    return {
      conversionChain: base.conversionChain,
      typeBridges: base.typeBridges,
      typeConfirmations: base.typeConfirmations,
      statusReconciliation: base.statusReconciliation,
    };
  }
  const bridges = read.type_bridge ?? [];
  const labels = new Map(base.requirementTypeMetrics.map((m) => [m.key, m.label]));
  return {
    conversionChain: buildConversionChain(chain),
    typeBridges: buildTypeBridges(chain, bridges, labels),
    typeConfirmations: buildTypeConfirmations(bridges),
    statusReconciliation: buildStatusReconciliation(chain, assetValues.get('items')),
  };
}

function buildAiSection(
  base: OverviewWorkbenchVM,
  ai: AiEffectivenessRead | null,
): Pick<
  OverviewWorkbenchVM,
  | 'aiReady'
  | 'aiStageMetrics'
  | 'aiCoverage'
  | 'aiCoverageLegend'
  | 'aiCalibration'
  | 'aiRiskSignals'
  | 'deliveryFailures'
> {
  if (!ai) {
    return {
      aiReady: false,
      aiStageMetrics: base.aiStageMetrics,
      aiCoverage: base.aiCoverage,
      aiCoverageLegend: base.aiCoverageLegend,
      aiCalibration: null,
      aiRiskSignals: base.aiRiskSignals,
      deliveryFailures: base.deliveryFailures,
    };
  }
  const byStage = new Map((ai.stages ?? []).map((s) => [s.stage, s]));
  const coverage = ai.coverage;
  const denominator = coverage.touched + coverage.untouched;
  const percent = denominator > 0 ? Math.round((coverage.touched / denominator) * 100) : 0;
  const calibration = ai.calibration;
  return {
    aiReady: true,
    aiStageMetrics: base.aiStageMetrics.map((row) => {
      const spec = AI_STAGE_ROWS.find((r) => r.key === row.key);
      if (!spec) return row;
      const stages = spec.stages
        .map((s) => byStage.get(s))
        .filter((s): s is AiStageEffectRead => s !== undefined);
      return aiStageRow(row, stages);
    }),
    aiCoverage: {
      ...base.aiCoverage,
      value: denominator > 0 ? `${percent}%` : '—',
      percent,
    },
    aiCoverageLegend: {
      touched: `${coverage.touched}`,
      untouched: `${coverage.untouched}`,
      notApplicable: `${coverage.not_applicable}`,
      total: `${coverage.total_items}`,
    },
    aiCalibration: {
      eceText: calibration.ece == null ? '—' : calibration.ece.toFixed(2),
      ratingText: AI_RATING_LABELS[calibration.rating] ?? calibration.rating,
      sampleText: `样本 ${calibration.sample_size}`,
      points: (calibration.buckets ?? []).map((b) => ({
        x: Math.round(b.avg_confidence * 100),
        y: Math.round(b.accuracy * 100),
        count: b.count,
      })),
    },
    aiRiskSignals: base.aiRiskSignals.map((row) => {
      const signal = (ai.risk_signals ?? []).find((s) => s.key === AI_RISK_KEYS[row.key]);
      if (!signal) return row;
      const meta = AI_LEVEL_META[signal.level] ?? { label: signal.level, tone: 'gray' as OverviewTone };
      return {
        ...row,
        level: meta.label,
        levelTone: meta.tone,
        value: signal.level === 'deferred' ? '—' : `${signal.value}`,
      };
    }),
    deliveryFailures: buildDeliveryFailures(ai),
  };
}
