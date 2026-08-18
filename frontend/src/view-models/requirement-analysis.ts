/**
 * 知识抽取阶段 ViewModel（UI 投影，不复制 LDM 领域对象）。
 *
 * 事实源：docs/40 slices/SCN-001-P02-需求要素识别/页面详细设计.md
 * - §4.2 锚点解析：offset 优先、exact+prefix/suffix fallback、失效锚点不隐藏要素
 * - §4.3 高亮渲染管线：DOM 分段（segment 携带 highlight_refs[]）
 * - §4.4 类型颜色（element_type）与状态层（process_status）分离
 */
import type {
  ElementFacetReviewRead,
  ElementWorkspaceRead,
  MaterialTextBlockRead,
  RequirementElementRead,
  SourceAnchorRange,
} from '../api/analysis';
import type { BadgeTone } from './common';

// ---- 类型颜色与文案（§4.4；颜色落 CSS class，VM 只给 colorKey）----

// KnowledgeCategory 稳定码（两翼）。与后端 ELEMENT_KNOWLEDGE_CATEGORY 单一来源对齐，
// API 已带 knowledge_category 时优先消费 API 值，META 作展示映射与兜底（01 篇 §3）。
export type KnowledgeCategory = 'requirement' | 'business';

/** 族内装饰性色阶（v4 原型 .mk-ico r-a/b/c、b-a/b/c）：仅视觉节奏，语义在图标+文字，不承载类型。 */
export type WingShade = 'a' | 'b' | 'c';

/** 类型释义（悬停一句话）：只给「光看名字猜不出是什么」的类型配，其余留空不强凑。 */
export const ELEMENT_TYPE_META: Record<
  string,
  { label: string; colorKey: string; category: KnowledgeCategory; shade: WingShade; hint?: string }
> = {
  functional_requirement: { label: '功能需求', colorKey: 'func', category: 'requirement', shade: 'a' },
  quality_attribute: { label: '质量属性', colorKey: 'quality', category: 'requirement', shade: 'c' },
  constraint: { label: '约束', colorKey: 'constraint', category: 'requirement', shade: 'b' },
  data_requirement: { label: '数据需求', colorKey: 'data', category: 'requirement', shade: 'a' },
  interface_requirement: { label: '接口需求', colorKey: 'interface', category: 'requirement', shade: 'b' },
  goal: { label: '目标', colorKey: 'goal', category: 'requirement', shade: 'c' },
  scenario: { label: '场景', colorKey: 'scenario', category: 'requirement', shade: 'a' },
  term: { label: '术语', colorKey: 'term', category: 'business', shade: 'a' },
  assumption: {
    label: '前提假设',
    colorKey: 'assumption',
    category: 'business',
    shade: 'c',
    hint: '材料默认成立、但还没人确认的前提；不成立时相关需求要重审',
  },
  business_rule: { label: '业务规则', colorKey: 'business_rule', category: 'business', shade: 'a' },
  role: { label: '角色', colorKey: 'role', category: 'business', shade: 'b' },
  external_system: { label: '外部系统', colorKey: 'external', category: 'business', shade: 'b' },
};

// 两翼展示映射（跨主题只改配色令牌；结构/图标一致）。需求翼在前。
export const KNOWLEDGE_CATEGORY_ORDER: KnowledgeCategory[] = ['requirement', 'business'];
export const KNOWLEDGE_CATEGORY_META: Record<
  KnowledgeCategory,
  { label: string; shortLabel: string; tone: BadgeTone; colorKey: string }
> = {
  requirement: { label: '需求领域知识', shortLabel: '需求知识', tone: 'processing', colorKey: 'wing-requirement' },
  business: { label: '业务领域知识', shortLabel: '业务知识', tone: 'success', colorKey: 'wing-business' },
};

export function knowledgeCategoryMeta(code: string | null | undefined) {
  return (code && KNOWLEDGE_CATEGORY_META[code as KnowledgeCategory]) || KNOWLEDGE_CATEGORY_META.business;
}

/** 某翼全部类型选项（区1 类型子筛选 chips 数据源；派生自 ELEMENT_TYPE_META，禁手写清单）。 */
export function elementTypeOptionsForWing(
  wing: KnowledgeCategory,
): { code: string; label: string; hint?: string }[] {
  return Object.entries(ELEMENT_TYPE_META)
    .filter(([, meta]) => meta.category === wing)
    .map(([code, meta]) => ({ code, label: meta.label, hint: meta.hint }));
}

/** 人工确认生命周期（state-machines/需求要素.md）：待确认/分析中/修订中/已确认/已撤销。 */
// 状态机 2026-07-05 收敛为 3 态：AI 复核/修订迭代是会话事实（见「有修订稿」派生筛选），不入状态。
export const PROCESS_STATUS_META: Record<string, { label: string; tone: BadgeTone }> = {
  pending_confirmation: { label: '待确认', tone: 'processing' },
  confirmed: { label: '已确认', tone: 'success' },
  revoked: { label: '已撤销', tone: 'neutral' },
};

/**
 * 模型裁定（证据预标记，不是状态）。
 *
 * hint＝该裁定的通用判据，只在模型没给出这一条的具体理由时兜底显示（区4 详情）。
 * 判据的权威文本在后端 `backend/app/domain/labels.py` 的 MODEL_VERDICT_GUIDE，但那份是写给模型看的
 * 提示词材料、也没有接口交给前端；此处按本表既有的中文标签复制惯例，另写一份面向用户的说法。
 *
 * 改判据须两处同步（后端那份也标了指回这里的注释）。两份文本有意不逐字一致：后端每条 hint 的
 * 后半句是给模型的指令（如「无法确定时也用它」「仍要登记，交由人工裁定」），只属提示词，不进本表。
 */
export const MODEL_VERDICT_META: Record<string, { label: string; tone: BadgeTone; hint: string }> = {
  processable: { label: '可处理', tone: 'success', hint: '有原文依据、表达可用' },
  suspected_needs_supplement: {
    label: '疑似需补充',
    tone: 'warning',
    hint: '有来源依据，但信息不完整，可能还需要补充来源材料',
  },
  suspected_noise: {
    label: '建议剔除',
    tone: 'danger',
    hint: '寒暄、下期范围一类不承载需求信息的内容',
  },
};

/** AI 复核结论（分析中裁定矩阵的列）。 */
export const REVIEW_CONCLUSION_META: Record<string, { label: string; tone: BadgeTone }> = {
  pass: { label: '可通过', tone: 'success' },
  needs_revision: { label: '须修订', tone: 'warning' },
  fail: { label: '不可通过', tone: 'danger' },
};

export function elementTypeMeta(code: string): {
  label: string;
  colorKey: string;
  category: KnowledgeCategory;
  shade: WingShade;
  hint?: string;
} {
  return (
    ELEMENT_TYPE_META[code] ?? { label: code, colorKey: 'term', category: 'business' as KnowledgeCategory, shade: 'a' }
  );
}

// ---- 状态前导标记（区3：与「类型」正交的状态通道，走形态=图标而非色相）----
// 待确认（无修订稿）不做标记，保持默认清爽态；其余状态各取一枚前导图标。

export type ElementStatusMarkKey = 'confirmed' | 'revoked' | 'has_draft';

/** label 供区3 图例、colorKey 落 CSS class（confirmed|revoked|has_draft），图例与标注共用单一来源。 */
export const STATUS_MARK_META: Record<ElementStatusMarkKey, { label: string; colorKey: ElementStatusMarkKey }> = {
  confirmed: { label: '已确认', colorKey: 'confirmed' },
  revoked: { label: '已撤销', colorKey: 'revoked' },
  has_draft: { label: '有修订稿', colorKey: 'has_draft' },
};

// 图例展示顺序（已确认 → 已撤销 → 有修订稿）。
export const STATUS_MARK_ORDER: ElementStatusMarkKey[] = ['confirmed', 'revoked', 'has_draft'];

/**
 * 单枚状态前导标记（优先级：已撤销 > 已确认 > 有修订稿 > 纯待确认无标记）。
 * confirmed/revoked 为终态；has_draft 是待确认下「存在未采纳修订稿」的会话事实。
 */
export function elementStatusMarkKey(
  processStatus: string,
  revisionDraft: string | null | undefined,
): ElementStatusMarkKey | null {
  if (processStatus === 'revoked') {
    return 'revoked';
  }
  if (processStatus === 'confirmed') {
    return 'confirmed';
  }
  if ((revisionDraft ?? '').trim()) {
    return 'has_draft';
  }
  return null;
}

export function processStatusMeta(code: string): { label: string; tone: BadgeTone } {
  return PROCESS_STATUS_META[code] ?? { label: code, tone: 'neutral' };
}

export function modelVerdictMeta(
  code: string | null | undefined,
): { label: string; tone: BadgeTone; hint?: string } | null {
  if (!code) {
    return null;
  }
  return MODEL_VERDICT_META[code] ?? { label: code, tone: 'neutral' };
}

/**
 * 区4 展示用的裁定理由：模型给了就用模型的，没给就回落到该裁定的通用判据。
 *
 * 为什么不留空：存量知识项识别时还没有这个字段，模型也可能漏给。留空会让详情区出现一条
 * 有标题没内容的空行，读者无从判断是"模型没说"还是"页面没取到"；回落到判据并明说模型没给，
 * 两种情况都交代清楚。
 */
export function verdictReasonText(
  verdictCode: string | null | undefined,
  reason: string | null | undefined,
): string | null {
  const text = (reason ?? '').trim();
  if (text) {
    return text;
  }
  const hint = verdictCode ? MODEL_VERDICT_META[verdictCode]?.hint : undefined;
  return hint ? `模型没有给出这一条的具体理由。这类裁定的通用判据是：${hint}` : null;
}

export function reviewConclusionMeta(code: string | null | undefined): { label: string; tone: BadgeTone } | null {
  if (!code) {
    return null;
  }
  return REVIEW_CONCLUSION_META[code] ?? { label: code, tone: 'neutral' };
}

// ---- 完备度投影（facet_review；LDM-015 派生、非权威，只作提示不作门禁）----

// hint＝界面悬停的一句话白话解释（四个成分状态各一句，读者不需要预先懂判定口径）。
const FACET_STATUS_META: Record<string, { mark: string; label: string; tone: BadgeTone; hint: string }> = {
  present: { mark: '✓', label: '存在', tone: 'success', hint: '这条话里已经写清了这项内容' },
  missing: { mark: '✗', label: '缺失', tone: 'danger', hint: '这项内容没写，建议补上' },
  ambiguous: {
    mark: '?',
    label: '含糊',
    tone: 'warning',
    hint: '写了但说得不明确，读的人可能理解成不同意思',
  },
  // 判据驱动 N/A（成分不适配该陈述形态）：中性态，不计缺口，判定理由见 note。
  not_applicable: { mark: '—', label: '不适用', tone: 'neutral', hint: '这类陈述本来就不需要这项，不算缺' },
};

const CORRECTNESS_META: Record<string, { label: string; tone: BadgeTone }> = {
  consistent_with_source: { label: '与原文一致', tone: 'success' },
  deviates: { label: '偏离原文', tone: 'danger' },
  unverifiable: { label: '原文无法核验', tone: 'warning' },
};

const COMPLETENESS_META: Record<string, { label: string; tone: BadgeTone }> = {
  complete: { label: '完备', tone: 'success' },
  incomplete: { label: '不完备（可带缺陷确认）', tone: 'warning' },
};

export interface FacetBadgeVM {
  key: string;
  label: string;
  required: boolean;
  status: string;
  statusMark: string;
  statusLabel: string;
  /** 该状态的白话解释，供徽标悬停显示 */
  statusHint: string;
  tone: BadgeTone;
  evidence: string | null;
  note: string | null;
  revisionHint: string | null;
}

export interface FacetReviewVM {
  rubricVersion: number;
  correctness: { label: string; tone: BadgeTone } | null;
  completeness: { label: string; tone: BadgeTone } | null;
  badges: FacetBadgeVM[];
  /** 缺失/含糊面向（详情区提示 + 修订指引用） */
  gaps: FacetBadgeVM[];
  /** 要素已修订出新版本，投影过期（修订后未复核；TC-08 版本锚。与条目侧同口径，
   * T20260711-item-structure-recheck 裁定 1：动词统一「复核」——要素侧本就有
   * 「重新发起 AI 复核」机制，仅改词与释义不改机制） */
  stale: boolean;
}

/** facet_review → 徽章 VM；无判据类型（facet_review 为空）返回 null，不渲染徽章区。 */
export function mapFacetReview(review: ElementFacetReviewRead | null | undefined): FacetReviewVM | null {
  if (!review || !(review.facets ?? []).length) {
    return null;
  }
  const badges: FacetBadgeVM[] = (review.facets ?? []).map((f) => {
    const meta = FACET_STATUS_META[f.status] ?? {
      mark: '?',
      label: f.status,
      tone: 'neutral' as BadgeTone,
      hint: '',
    };
    return {
      key: f.facet_key,
      label: f.label,
      required: Boolean(f.required),
      status: f.status,
      statusMark: meta.mark,
      statusLabel: meta.label,
      statusHint: meta.hint,
      tone: meta.tone,
      evidence: f.evidence ?? null,
      note: f.note ?? null,
      revisionHint: f.revision_hint ?? null,
    };
  });
  return {
    rubricVersion: review.rubric_version,
    correctness: review.correctness ? (CORRECTNESS_META[review.correctness] ?? null) : null,
    completeness: review.completeness ? (COMPLETENESS_META[review.completeness] ?? null) : null,
    badges,
    // 缺口＝真正缺失/含糊；not_applicable 是判据不适配（不计缺口），present 已具备——二者均排除。
    gaps: badges.filter((b) => b.status !== 'present' && b.status !== 'not_applicable'),
    stale: Boolean(review.stale),
  };
}

// ---- TC-08：完备度筛选（纯 UI 态，归 ViewModel）与修订稿预填 ----

export const COMPLETENESS_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'incomplete', label: '不完备' },
  { key: 'stale', label: '修订后未复核' },
] as const;

export type CompletenessFilterKey = (typeof COMPLETENESS_FILTERS)[number]['key'];

/** 列表项是否命中完备度筛选（读投影派生字段；不复制领域门禁）。 */
export function matchesCompletenessFilter(
  item: { completenessKey: string | null; facetStale: boolean },
  filter: CompletenessFilterKey,
): boolean {
  if (filter === 'all') {
    return true;
  }
  if (filter === 'stale') {
    return item.facetStale;
  }
  return !item.facetStale && item.completenessKey === 'incomplete';
}

/** 无选区时「改范围」预填的引导语：拿到选区后由选区描述整段替换，不与描述并存堆叠。 */
export const SELECTION_PROMPT_GUIDANCE = '（请先在区3 拖选新的原文范围，选区会随本命令一起发送）';

/**
 * 选区描述（给人读的 affordance 文字，不是结构化参数）：区3 选区随请求上下文另走
 * selected_text_ranges 通道，这段文字只让用户在发送前看清「命令将作用在哪一段原文」。
 *
 * 摘要里的直角引号「」被剥掉：这对引号是描述自身的定界符，选区原文里若也带一对，
 * 下方 SELECTION_AFFORDANCE_PATTERN 就认不出描述的边界，更新时会再拼一段而不是替换。
 */
export function buildSelectionAffordance(selection: CanvasSelection): string {
  const raw = selection.text.replace(/[「」]/g, '');
  const preview = raw.length > 20 ? `${raw.slice(0, 20)}…` : raw;
  return `来源改到区3 当前选区（${selection.start}–${selection.end}）：「${preview}」`;
}

/**
 * 已在正文里的选区描述：认出它才能「更新」而不是「再堆一段」。前缀「把」可选——
 * 「改范围」预填写作「把来源改到…」，而追加到自由正文末尾时不带这个字。
 */
const SELECTION_AFFORDANCE_PATTERN = /(把)?来源改到区3 当前选区（\d+–\d+）：「[^「」]*」/;

/**
 * 追加位置的收尾标点分三类，衔接方式各不相同。三类共用的目的是：追加进去的这段选区说明，
 * 读起来必须是独立的一句，不能被读成前一句的组成部分。
 *
 * 终止型：句子已经说完（含 ASCII 句点与省略号），直接接上即可，再补逗号就成了「。，」连排。
 */
const TRAILING_TERMINATOR = /[。．.!！?？…]$/;
/** 分隔型：这个标点本身就是分隔，追加内容读作并列的下一段，同样不再补逗号。 */
const TRAILING_SEPARATOR = /[，,、;；]$/;
/**
 * 引导型（冒号）：冒号后面的文字会被读成冒号前那个名目的取值——命令的参数槽正是这么断句的
 * （`/改表达 修订为：` 之后的整段都是新表达）。此处若直接接上，这段给人读的选区说明就变成了
 * 参数正文，故换行另起一段，不与冒号粘连；补逗号会得到「：，」，既难读也不解决断句。
 */
const TRAILING_LEAD_IN = /[:：]$/;

/**
 * 「当前选区」按钮的文本改写规则（纯函数：当前输入框正文 ＋ 选区 → 新正文）。四种形态：
 * ① 空正文 → 只有选区描述本身（补「把」成句）；
 * ② 含无选区版引导语 → 用选区描述替换引导语（不在引导语后面再堆一段）；
 * ③ 已含一段选区描述 → 就地更新那一段（同一选区连点两次正文不变，幂等）；
 * ④ 用户自由编辑过的正文 → 按收尾标点的类别自然衔接到末尾。
 */
export function withSelectionAffordance(currentText: string, selection: CanvasSelection): string {
  const affordance = buildSelectionAffordance(selection);
  const text = currentText.replace(/\s+$/, '');
  if (!text.trim()) {
    return `把${affordance}`;
  }
  if (SELECTION_AFFORDANCE_PATTERN.test(text)) {
    // 保留原有的「把」：命令预填带、末尾追加的不带，更新不该改写这个字。
    return text.replace(SELECTION_AFFORDANCE_PATTERN, (_match, prefix: string | undefined) =>
      `${prefix ?? ''}${affordance}`,
    );
  }
  if (text.includes(SELECTION_PROMPT_GUIDANCE)) {
    // 替换值必须走函数：选区原文里的 $&、$`、$'、$$ 在替换字符串里恒有特殊含义，
    // 会被当成替换模式展开，输出乱句并让「同一选区连点两次正文不变」的幂等承诺落空。
    return text.replace(SELECTION_PROMPT_GUIDANCE, () => `把${affordance}`);
  }
  if (TRAILING_LEAD_IN.test(text)) {
    return `${text}\n${affordance}`;
  }
  return TRAILING_TERMINATOR.test(text) || TRAILING_SEPARATOR.test(text)
    ? `${text}${affordance}`
    : `${text}，${affordance}`;
}

/** 「当前选区」按钮唯一适用的那条命令：选区说明的语义与它的参数一致（作用在哪一段原文）。 */
const SELECTION_AFFORDANCE_COMMAND = '/改范围';

/**
 * 正文形态是否接受追加选区说明——「当前选区」按钮的可用判据。
 *
 * 这段说明是给人读的自然语言，不是结构化参数；而后端命令解释器按参数语法切正文（取第一个冒号
 * 之后的全部文本、按「」提取名字、按行数判定分支）。于是正文以别的命令开头时，说明会被整段读进
 * 该命令的参数：`/改表达` 把它当成知识项的新表达直写入库，`/改类型` 从中扫出类型词覆盖用户的选择，
 * `/合并` 把说明里的引号当成又一条知识项的名字，`/新增遗漏` 丢掉用户正文只留说明，`/拆分` 因正文
 * 塌成一行而从确定性分支掉进 AI 分支。
 *
 * 因此只放行两种形态：`/改范围`（说明的语义正是它的参数，含无选区版引导语那一支），
 * 以及不以斜杠命令开头的自由正文（后端不按参数语法切，整段交 AI 解读）。
 */
export function acceptsSelectionAffordance(currentText: string): boolean {
  const text = currentText.trimStart();
  if (!text.startsWith('/') && !text.startsWith('／')) {
    return true;
  }
  // 全角斜杠与半角斜杠后端同等对待，判据统一按半角比对
  const normalized = `/${text.slice(1)}`;
  if (!normalized.startsWith(SELECTION_AFFORDANCE_COMMAND)) {
    return false;
  }
  // 命令词要整词命中：`/改范围x` 不是「改范围」
  const rest = normalized.slice(SELECTION_AFFORDANCE_COMMAND.length);
  return rest === '' || /^\s/.test(rest);
}

/**
 * 区5 快捷命令预填构造器（AEP-096）：药丸只向输入框预填 `/命令词` 前缀文本，
 * 参数弹层（类型浮层 / 合并复选）只是文本组稿助手；前端不解析命令词、不暗挂结构化参数，
 * 发送时整段原文交后端命令解释端点。
 */
export const QUICK_COMMAND_PREFILLS = {
  adjustType: (typeLabel: string) => `/改类型 ${typeLabel}`,
  reviseExpression: () => '/改表达 修订为：',
  // 改范围：后端从请求上下文取区3 选区（无需参数），正文仅作用户可读的 affordance。
  // 有选区→明示将使用当前选区并附摘要预览（与「当前选区」按钮共用同一段描述构造，
  // 于是预填出来的那段能被按钮认出并就地更新）；无选区→引导先选区（与后端 clarify 口径一致）。
  adjustAnchor: (selection?: CanvasSelection | null) => {
    if (selection) {
      return `/改范围 把${buildSelectionAffordance(selection)}`;
    }
    return `/改范围 ${SELECTION_PROMPT_GUIDANCE}`;
  },
  split: () => '/拆分 1. \n2. ',
  merge: (names: string[]) =>
    `/合并 与${names.map((n) => `「${n}」`).join('')}合并，合并后表达由 AI 起草。`,
  addMissing: (selectionText?: string | null) => `/新增遗漏 ${(selectionText ?? '').trim()}`,
  // 勘误：有区3 选区→选区文本入「原文」空位；无选区→空脚手架＋占位提示（不再出现空「」脏文本）。
  erratum: (selectionText?: string | null) => {
    const src = (selectionText ?? '').trim();
    return src ? `/勘误 把「${src}」改正为「」` : '/勘误 把「原文里写错的片段」改正为「更正后的文本」';
  },
  supplement: () => '/补入 （依据：）',
} as const;

export interface CanvasSelection {
  start: number;
  end: number;
  text: string;
}

export interface SelectionRange {
  start: number;
  end: number;
  exact: string;
  prefix: string;
  suffix: string;
}

/**
 * ① 区3 拖选 → 命令来源锚点载荷：选区文本入 exact、范围入 start/end，随命令送后端；
 * 后端「新增遗漏」据此把选区范围建成新知识项的 source_anchor（区3 高亮、区1 计数自洽）。
 * 无选区 → 空数组（命令不挂结构化锚点）。
 */
export function buildSelectionRanges(selection: CanvasSelection | null): SelectionRange[] {
  return selection
    ? [{ start: selection.start, end: selection.end, exact: selection.text, prefix: '', suffix: '' }]
    : [];
}

export interface ReidentifyGuard {
  needsConfirm: boolean;
  message: string;
}

/**
 * ② 识别重跑拦截判定：每次识别都新建识别上下文、生成一份全新清单并切走视图——
 * 工作区已有知识项时属破坏性操作（已确认/修订/拆分归并的成果从工作区移除、不再显示），需前置确认。
 * 计数取区5 可见口径（live=当前工作区条数、confirmed=已确认条数），文案白话、与用户可见输入自洽。
 */
export function buildReidentifyGuard(liveCount: number, confirmedCount: number): ReidentifyGuard {
  if (liveCount <= 0) {
    return { needsConfirm: false, message: '' };
  }
  const confirmedNote = confirmedCount > 0 ? `（其中已确认 ${confirmedCount} 条）` : '';
  return {
    needsConfirm: true,
    message: `当前工作区已有 ${liveCount} 条${confirmedNote}，重新识别后将不再显示在工作区，新结果需重新逐条确认。`,
  };
}

/**
 * 一键预填修订稿（§4：revision_hint + 诊断 note 组装模板）。
 * 预填≠生效：产物只进入前端修订输入框，仍走 修订中 → 定稿 状态机。
 */
export function buildRevisionPrefill(content: string, gaps: FacetBadgeVM[]): string {
  const lines = [content];
  for (const g of gaps) {
    const note = g.note ? `${g.note}；` : '';
    const hint = g.revisionHint ?? '';
    lines.push(`【${g.label}·待补充】${note}${hint}`.trim());
  }
  return lines.join('\n');
}

// ---- 锚点解析（§4.2）----

export type AnchorStatus = 'ok' | 'relocated' | 'invalid' | 'none';

export interface ResolvedRange {
  start: number;
  end: number;
  relocated: boolean;
}

export interface ResolvedAnchor {
  status: AnchorStatus;
  ranges: ResolvedRange[];
}

interface AnchorJson {
  material_ref?: string;
  ranges?: Array<Partial<SourceAnchorRange>>;
}

/** 解析单个 source_anchor JSON：offset 命中 → ok；quote fallback 唯一命中 → relocated；否则 invalid。 */
export function resolveAnchor(
  sourceAnchor: string | null | undefined,
  canvasMaterialRef: string,
  rawText: string,
): ResolvedAnchor {
  if (!sourceAnchor) {
    return { status: 'none', ranges: [] };
  }

  let parsed: AnchorJson;
  try {
    parsed = JSON.parse(sourceAnchor) as AnchorJson;
  } catch {
    return { status: 'invalid', ranges: [] };
  }

  if (!parsed || parsed.material_ref !== canvasMaterialRef || !Array.isArray(parsed.ranges)) {
    return { status: 'invalid', ranges: [] };
  }

  const resolved: ResolvedRange[] = [];
  let anyRelocated = false;

  for (const range of parsed.ranges) {
    const start = typeof range.start === 'number' ? range.start : -1;
    const end = typeof range.end === 'number' ? range.end : -1;
    const exact = range.exact ?? '';

    if (start >= 0 && end > start && end <= rawText.length && rawText.slice(start, end) === exact) {
      resolved.push({ start, end, relocated: false });
      continue;
    }

    if (!exact) {
      continue;
    }

    // fallback：exact（必要时 prefix/suffix 消歧）唯一命中 → 锚点已重定位
    const occurrences: number[] = [];
    let cursor = rawText.indexOf(exact);
    while (cursor !== -1) {
      occurrences.push(cursor);
      cursor = rawText.indexOf(exact, cursor + 1);
    }

    let hit: number | null = null;
    if (occurrences.length === 1) {
      hit = occurrences[0];
    } else if (occurrences.length > 1) {
      const withContext = occurrences.filter((pos) => {
        const prefixOk = !range.prefix || rawText.slice(Math.max(0, pos - range.prefix.length), pos) === range.prefix;
        const suffixOk =
          !range.suffix || rawText.slice(pos + exact.length, pos + exact.length + range.suffix.length) === range.suffix;
        return prefixOk && suffixOk;
      });
      if (withContext.length === 1) {
        hit = withContext[0];
      }
    }

    if (hit !== null) {
      resolved.push({ start: hit, end: hit + exact.length, relocated: true });
      anyRelocated = true;
    }
  }

  if (!resolved.length) {
    return { status: 'invalid', ranges: [] };
  }
  return { status: anyRelocated ? 'relocated' : 'ok', ranges: resolved };
}

// ---- 高亮与分段（§4.3 / §4.5）----

export interface ElementHighlight {
  elementId: string;
  typeColorKey: string;
  processStatus: string;
  ranges: ResolvedRange[];
}

export interface CanvasSegmentVM {
  key: string;
  text: string;
  start: number;
  end: number;
  /** 覆盖本片段的要素 id（>1 = 重叠高亮，点击开重叠选择浮层） */
  refs: string[];
  primaryColorKey: string | null;
  primaryStatus: string | null;
  relocated: boolean;
}

export interface CanvasBlockVM {
  blockId: string;
  segments: CanvasSegmentVM[];
}

/** 按 block 切分正文为携带 highlight_refs 的 segment（边界=所有相交 range 的 start/end）。 */
export function buildCanvasBlocks(
  blocks: MaterialTextBlockRead[],
  highlights: ElementHighlight[],
): CanvasBlockVM[] {
  return blocks.map((block) => {
    const boundaries = new Set<number>([block.start_offset, block.end_offset]);
    const inBlock = highlights
      .map((h) => ({
        ...h,
        ranges: h.ranges.filter((r) => r.start < block.end_offset && r.end > block.start_offset),
      }))
      .filter((h) => h.ranges.length > 0);

    for (const h of inBlock) {
      for (const r of h.ranges) {
        boundaries.add(Math.max(r.start, block.start_offset));
        boundaries.add(Math.min(r.end, block.end_offset));
      }
    }

    const sorted = [...boundaries].sort((a, b) => a - b);
    const segments: CanvasSegmentVM[] = [];

    for (let i = 0; i < sorted.length - 1; i += 1) {
      const start = sorted[i];
      const end = sorted[i + 1];
      if (end <= start) {
        continue;
      }
      const covering = inBlock.filter((h) => h.ranges.some((r) => r.start <= start && r.end >= end));
      const primary = covering[0] ?? null;
      segments.push({
        key: `${block.block_id}-${start}`,
        text: '',
        start,
        end,
        refs: covering.map((h) => h.elementId),
        primaryColorKey: primary ? primary.typeColorKey : null,
        primaryStatus: primary ? primary.processStatus : null,
        relocated: covering.some((h) => h.ranges.some((r) => r.relocated && r.start <= start && r.end >= end)),
      });
    }

    return { blockId: block.block_id, segments };
  });
}

/** 用 block 文本填充 segment.text（分离便于测试边界计算）。 */
export function fillSegmentText(blocks: MaterialTextBlockRead[], canvasBlocks: CanvasBlockVM[]): CanvasBlockVM[] {
  const byId = new Map(blocks.map((b) => [b.block_id, b]));
  return canvasBlocks.map((cb) => {
    const block = byId.get(cb.blockId);
    if (!block) {
      return cb;
    }
    return {
      ...cb,
      segments: cb.segments.map((seg) => ({
        ...seg,
        text: block.text.slice(seg.start - block.start_offset, seg.end - block.start_offset),
      })),
    };
  });
}

// ---- 区1 列表项投影 ----

export interface ElementListItemVM {
  id: string;
  seq: number;
  typeCode: string;
  typeLabel: string;
  /** 类型释义（悬停一句话）；只有配了释义的类型才有值 */
  typeHint?: string;
  typeColorKey: string;
  content: string;
  confidenceText: string;
  statusCode: string;
  statusLabel: string;
  statusTone: BadgeTone;
  verdictLabel: string | null;
  verdictTone: BadgeTone;
  /** 模型裁定稳定码（候选区归组依据；展示文案一律走 verdictLabel） */
  verdictCode: string | null;
  /** 模型给这一条裁定的具体理由；模型漏给/存量数据为 null（读侧回落见 verdictReasonText） */
  verdictReason: string | null;
  /** 建议剔除候选：模型判为建议剔除、且人工尚未撤回，且是本次识别的在册项 */
  triageCandidate: boolean;
  version: number;
  superseded: boolean;
  anchorStatus: AnchorStatus;
  anchorHint: string | null;
  /** 解析成功的来源锚点段数（区1 行内 ⚓×N，0 不显示） */
  anchorCount: number;
  /** 完备度投影派生（TC-08 列表筛选用）：complete/incomplete/null（无判定） */
  completenessKey: string | null;
  facetStale: boolean;
  /** 会话事实：存在未采纳修订稿（区1「有修订稿」派生筛选用） */
  hasDraft: boolean;
  /** 既有知识项：本次识别按同名归并到既往材料的项，只读展示，不参与勾选/裁决/批量 */
  mergedExisting: boolean;
}

/**
 * 这一条是不是「AI 建议剔除的候选」——即它该待在列表底部的候选区，而不是正常列表里。
 *
 * 候选区的语义是「待人工处置的队列」（2026-07-25 用户拍板）：进箱的条件是模型判它建议剔除，
 * 出箱的条件是人工处置过它——撤回到正常列表，或者撤销。故判据四条同时成立：
 * - 模型裁定为建议剔除；
 * - 人工还没把它撤回到正常列表（noise_triage 不是 restored）；
 * - 它还没被撤销——用户照页面提示把「确是多余的」撤销掉之后，这一条就算处置完毕，随即离箱、
 *   组名计数相应减少；留在箱里会让计数一个不减，用户看不出自己处置过什么；
 * - 它是本次识别产生的在册知识项。后两类被排除在候选区之外是有意的——
 *   已被替代（superseded）的是历史留痕，撤回它没有意义，仍按既有方式留在正常列表；
 *   「已有」的知识项来自此前的材料、本页只读，撤回按钮点下去后端也会拒。
 *
 * 撤回与撤销是两回事：撤回改的是「显示在哪个列表里」，撤销改的是确认生命周期。撤销让它离箱，
 * 但它仍不是已确认的知识项——两条判据各管各的，不要据此推断撤销等于确认。
 *
 * 后端 `backend/app/services/analysis_workspace.py` 的 `_in_triage_group` 是同一套判据（用于挑
 * 默认选中目标，那里的 superseded 由调用侧先滤掉）。改判据须两处同步。
 */
export function isTriageCandidate(
  element: Pick<RequirementElementRead, 'model_verdict' | 'noise_triage' | 'superseded' | 'process_status'>,
  mergedExisting = false,
): boolean {
  return (
    element.model_verdict === 'suspected_noise' &&
    element.noise_triage !== 'restored' &&
    element.process_status !== 'revoked' &&
    !element.superseded &&
    !mergedExisting
  );
}

/**
 * 从一组知识项 id 里滤掉不可裁决的：工作区里已不存在的、已被替代的、以及建议剔除候选。
 *
 * 候选条目不参与任何批量入口（2026-07-25 用户拍板的口径），批量确认、批量拒绝、复核送检、
 * 区5 组稿命令、「已选 n 条」计数共用本函数，判据只有这一处。候选条目本来就没有复选框，混进
 * 勾选集合的路径只有一条：先撤回到正常列表、勾选它、再把它移回候选区。
 *
 * 与 `isTriageCandidate` 的分工：那个回答「这一条该显示在哪个列表里」，这个回答「这一批里哪些
 * 可以提交裁决」。撤销不走本函数——撤销是候选区的正当出口，单条选中一条候选仍可撤销它。
 */
export function withoutTriageCandidates(
  ids: readonly string[],
  elementsById: ReadonlyMap<string, RequirementElementRead>,
  mergedExistingIds?: ReadonlySet<string>,
): string[] {
  return ids.filter((id) => {
    const element = elementsById.get(id);
    if (!element || element.superseded) {
      return false;
    }
    return !isTriageCandidate(element, Boolean(mergedExistingIds?.has(id)));
  });
}

/**
 * 区1 列表拆两处：正常列表 / 建议剔除候选区（乙案＝垃圾邮件文件夹形态）。
 *
 * 被撤回的知识项 triageCandidate 为假，于是自动回到正常列表、按原类型进两翼分组，
 * 这里不需要为"撤回过"单独开一条支路。
 */
export function splitTriageCandidates(items: ElementListItemVM[]): {
  normal: ElementListItemVM[];
  candidates: ElementListItemVM[];
} {
  return {
    normal: items.filter((item) => !item.triageCandidate),
    candidates: items.filter((item) => item.triageCandidate),
  };
}

export function mapElementList(
  elements: RequirementElementRead[],
  anchors: Map<string, ResolvedAnchor>,
  mergedExistingIds?: ReadonlySet<string>,
): ElementListItemVM[] {
  return elements.map((element, index) => {
    const type = elementTypeMeta(element.element_type);
    const status = processStatusMeta(element.process_status);
    const verdict = modelVerdictMeta(element.model_verdict);
    const restoredFromTriage =
      element.model_verdict === 'suspected_noise' && element.noise_triage === 'restored';
    const triageCandidate = isTriageCandidate(element, Boolean(mergedExistingIds?.has(element.id)));
    const anchor = anchors.get(element.id) ?? { status: 'none' as const, ranges: [] };
    return {
      id: element.id,
      seq: index + 1,
      typeCode: element.element_type,
      typeLabel: type.label,
      typeHint: type.hint,
      typeColorKey: type.colorKey,
      content: element.content,
      confidenceText:
        element.confidence !== null && element.confidence !== undefined
          ? `${Math.round(element.confidence * 100)}%`
          : '—',
      statusCode: element.process_status,
      statusLabel: status.label,
      statusTone: status.tone,
      // 「建议剔除」这个红徽标只出现在候选区的条目上，正常列表里一个都不出现——那正是本卡要
      // 消掉的自相矛盾（好列表里的东西标着该被剔除）。判据与 isTriageCandidate 是同一个，故：
      // - 被人工撤回的那条改显示人工裁定「已撤回」，模型的原判定与理由仍原样陈列在区4 详情；
      // - 已撤销、已替代、「已有」这三类带 suspected_noise 却留在正常列表的条目不出裁定徽标——
      //   它们的区4 都不给撤回按钮，挂个红徽标只会让用户对着一行无计可施（冷审查裁定 C5）。
      // 「疑似需补充」不受此限：它本就该留在正常列表并带自己的徽标。
      verdictLabel: restoredFromTriage
        ? '已撤回'
        : element.model_verdict === 'suspected_noise'
          ? (triageCandidate ? verdict?.label ?? null : null)
          : verdict && element.model_verdict !== 'processable'
            ? verdict.label
            : null,
      verdictTone: restoredFromTriage ? 'neutral' : verdict?.tone ?? 'neutral',
      verdictCode: element.model_verdict ?? null,
      verdictReason: element.verdict_reason ?? null,
      triageCandidate,
      version: element.version ?? 1,
      superseded: Boolean(element.superseded),
      hasDraft: Boolean((element.revision_draft ?? '').trim()),
      anchorStatus: anchor.status,
      anchorCount: anchor.ranges.length,
      anchorHint:
        anchor.status === 'invalid'
          ? '来源定位待修正'
          : anchor.status === 'relocated'
            ? '锚点已重定位'
            : null,
      completenessKey: element.facet_review?.completeness ?? null,
      facetStale: Boolean(element.facet_review?.stale),
      mergedExisting: Boolean(mergedExistingIds?.has(element.id)),
    };
  });
}

/** 区1 两翼分组投影（v4 原型 .wing-group）：按翼归组、翼序需求在前、空翼组整组隐藏。 */
export function groupElementListByWing(
  items: ElementListItemVM[],
): { wing: KnowledgeCategory; items: ElementListItemVM[] }[] {
  return KNOWLEDGE_CATEGORY_ORDER.map((wing) => ({
    wing,
    items: items.filter((item) => elementTypeMeta(item.typeCode).category === wing),
  })).filter((group) => group.items.length > 0);
}

// ---- 识别相位（区2 按钮、区3 遮罩、区5 输入的共同依据）----

/** 就绪＝识别已出结果；识别中＝还没有结果；失败＝识别停靠且后端给了重试出口。 */
export type RecognitionPhase = 'ready' | 'recognizing' | 'failed';

/**
 * 工作区 → 识别相位（冷审查裁定 C1）。
 *
 * 为什么必须是三态：识别失败停靠时，后端返回的工作区同样没有 parse_status，
 * 与「识别还在跑」长得一模一样。旧代码把这两种情况压成一个「识别中」，于是
 * 一次失败的识别上下文被进页回放读回来之后，页面永远停在识别中——区5 输入与
 * 命令全禁用，区2 的识别按钮因为在转圈而吞掉点击，刷新也不自愈。
 * 后端其实一直在失败停靠时给出 retry 动作（`available_actions`），这里把它读出来，
 * 让页面认出失败态并放出重试出口。
 *
 * 第三种情况（parse_status 与停靠原因双空、动作清单为空）是执行器中断或识别真的
 * 还在跑，两者从工作区数据上无法分辨，故如实报「识别中」、不谎报失败；页面另按
 * 「这次识别是不是本页发起的」决定要不要锁住识别按钮。
 */
export function deriveRecognitionPhase(workspace: ElementWorkspaceRead): RecognitionPhase {
  if (workspace.parse_status) {
    return 'ready';
  }
  const retry = (workspace.available_actions ?? []).find((a) => a.key === 'retry');
  return retry && retry.enabled !== false ? 'failed' : 'recognizing';
}

/** 工作区 → 全部要素锚点解析结果。 */
export function resolveWorkspaceAnchors(workspace: ElementWorkspaceRead): Map<string, ResolvedAnchor> {
  const map = new Map<string, ResolvedAnchor>();
  const canvas = workspace.material_canvas;
  if (!canvas) {
    return map;
  }
  // 既有知识项一并解析：其锚点已由后端换算为本材料锚点，区1 来源段数与区3 标注共用同一入口
  for (const element of [...(workspace.elements ?? []), ...(workspace.merged_existing_elements ?? [])]) {
    map.set(element.id, resolveAnchor(element.source_anchor, canvas.material_ref, canvas.raw_text));
  }
  return map;
}

/** 工作区 → 区3 高亮集合（被替代 superseded 要素不再上色，保留列表历史态）。 */
export function buildHighlights(
  workspace: ElementWorkspaceRead,
  anchors: Map<string, ResolvedAnchor>,
): ElementHighlight[] {
  const highlights: ElementHighlight[] = [];
  for (const e of [...(workspace.elements ?? []), ...(workspace.merged_existing_elements ?? [])]) {
    if (e.superseded) {
      continue;
    }
    const anchor = anchors.get(e.id);
    if (!anchor || !anchor.ranges.length) {
      continue;
    }
    highlights.push({
      elementId: e.id,
      typeColorKey: elementTypeMeta(e.element_type).colorKey,
      processStatus: e.process_status,
      ranges: anchor.ranges,
    });
  }
  return highlights;
}

// ---- 区5 时间线（知识抽取页与条目形成页共用；条目评审页走 ChatWidget 自有投影）----

/** 时间线上的一条消息：两页各自的 ChatMsg 都满足这个形状。 */
export interface TimelineMessageLike {
  id: number;
  kind: string;
  text: string;
  /** 消息时刻（ISO） */
  at: string;
  /** 来源留痕行 id（水合而来的消息才有；本地推的消息没有） */
  sourceId?: string | null;
}

/** 时间线上的一张卡（复核·修订稿卡 / 变更草案卡 / 修订建议卡）。 */
export interface TimelineCardLike {
  key: string;
  /** 卡片对应事实的最近写入时刻（ISO）；后端未给出时为 null＝按最新处理，排在末尾 */
  at: string | null;
  /**
   * 卡片正文所依据的字段拼成的内容指纹。给了就用它判断「这张卡是不是换了内容」，
   * 不给则退回按 at 判断。
   *
   * 复核卡必须给：它的 at 取的是知识项整行的最后写入时刻，而「✓ 确认 / ✗ 拒绝」
   * 也会 UPDATE 该行、刷新这个时刻——确认既不是复核也不是修订，卡片不该因此
   * 重新落位、更不该把时间标签跳成「刚刚」（冷审查裁定 C7）。
   */
  fingerprint?: string | null;
}

export type Zone5TimelineItem<M extends TimelineMessageLike> =
  | { kind: 'message'; key: string; at: string | null; message: M }
  | { kind: 'card'; key: string; at: string | null };

/** 已落位的卡片：sortMs 决定它排在哪，at 是给用户看的事实时刻。 */
export interface PositionedCard {
  key: string;
  at: string | null;
  sortMs: number;
  /** 落位当时的内容标识（fingerprint ?? at）：与新一轮比对，变了才重新落位 */
  identity: string | null;
}

function timeValue(at: string | null | undefined): number | null {
  if (!at) {
    return null;
  }
  const ms = Date.parse(at);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * 卡片落位：按「进入这段对话的时刻」排，显示的仍是事实时刻。
 *
 * 两种情形要区分开：
 *  - 刚由你的命令产生的卡片，事实时刻就是刚刚，按事实时刻落在命令之后——对话读起来是连贯的。
 *  - 只是切到了一条早就有修订稿的知识项，卡片的事实时刻可能是一小时前；若按事实时刻排，
 *    它会浮到整段历史的最顶部，读者看到的是「AI 凭空在开头答了一句」。这种一律落在当前
 *    最新一条消息之后。
 *
 * 落位一次即记住（memory），此后新发的消息排在卡片之下，卡片不会被消息推着走。
 * 卡片内容更新（内容指纹变化，未给指纹时退回事实时刻）时重新落位。
 * 显示时刻始终取事实时刻，不伪造。
 *
 * 一条边界要单说：**一条消息都还没有时不把落位记死**（裁定 C3）。留痕历史是在页面
 * 提交这一帧之后才拉回来的，所以首帧落位时消息必为空；此刻若记死，历史随后水合进来，
 * 卡片就排在这些历史消息之上——刷新页面即可复现。这一帧只给暂定落位，等出现第一条
 * 消息再正式落位。
 */
export function resolveCardPositions(
  cards: TimelineCardLike[],
  latestMessageAt: string | null,
  memory: Map<string, PositionedCard>,
): PositionedCard[] {
  const latestMs = timeValue(latestMessageAt);
  const alive = new Set(cards.map((c) => c.key));
  for (const key of [...memory.keys()]) {
    if (!alive.has(key)) {
      memory.delete(key); // 卡片消失（草案确认/清除）即忘掉落位
    }
  }
  return cards.map((card) => {
    const identity = card.fingerprint ?? card.at;
    const remembered = memory.get(card.key);
    if (remembered && remembered.identity === identity) {
      return remembered;
    }
    const factMs = timeValue(card.at);
    const afterLatest = latestMs === null ? null : latestMs + 1;
    // 后端没给时刻的卡片视为最新，排在末尾——正无穷是「排最后」的排序值，
    // 不是伪造出来的时刻（显示侧读的是 at，仍为 null，不显示时间）。
    const sortMs =
      factMs === null
        ? Number.POSITIVE_INFINITY
        : afterLatest === null || factMs >= afterLatest
          ? factMs
          : afterLatest;
    const positioned: PositionedCard = { key: card.key, at: card.at, sortMs, identity };
    if (latestMs !== null) {
      memory.set(card.key, positioned);
    }
    return positioned;
  });
}

/**
 * 消息与卡片合成一条按时刻升序的时间线。
 *
 * 缺陷背景：卡片此前固定渲染在消息列表之后，新发的消息追加在消息数组末尾却渲染在卡片之上，
 * 看着像插进了历史中间。现在卡片按 resolveCardPositions 给出的落位参与排序；
 * 同刻时消息在前、卡片在后（卡片是命令的后果）。
 */
export function buildZone5Timeline<M extends TimelineMessageLike>(
  messages: M[],
  cards: PositionedCard[],
): Zone5TimelineItem<M>[] {
  const items: { item: Zone5TimelineItem<M>; time: number | null; weight: number; seq: number }[] = [];
  messages.forEach((message, index) => {
    items.push({
      item: { kind: 'message', key: `msg-${message.id}`, at: message.at, message },
      time: timeValue(message.at),
      weight: 0,
      seq: index,
    });
  });
  cards.forEach((card, index) => {
    items.push({
      item: { kind: 'card', key: card.key, at: card.at },
      time: card.sortMs,
      weight: 1,
      seq: messages.length + index,
    });
  });
  return items
    .sort((a, b) => {
      if (a.time === null || b.time === null) {
        // 无时刻者恒排在有时刻者之后；两者皆无则保持给定顺序
        if (a.time === b.time) return a.seq - b.seq;
        return a.time === null ? 1 : -1;
      }
      if (a.time !== b.time) return a.time - b.time;
      if (a.weight !== b.weight) return a.weight - b.weight;
      return a.seq - b.seq;
    })
    .map((entry) => entry.item);
}

/**
 * 留痕水合与实时消息的真合并（冷审查裁定 F8）。
 *
 * 旧写法 `current.length ? current : rows` 是全有全无：用户在留痕响应到达前抢先发一条，
 * 整段服务端历史就被丢弃、此后不再重试。这里改为按标识去重的合并：
 * 服务端行带 sourceId，据此去重；本地推的消息没有 sourceId，用「同语气＋同文本＋时刻相近」
 * 判为同一条，避免刚发出去、后端已记录的消息被水合成两条（原守卫防的正是这个）。
 */
export function mergeHydratedMessages<M extends TimelineMessageLike>(
  current: M[],
  hydrated: M[],
  nearMs = 120_000,
): M[] {
  if (!hydrated.length) {
    return current;
  }
  const knownSourceIds = new Set(
    current.map((m) => m.sourceId).filter((id): id is string => Boolean(id)),
  );
  const additions = hydrated.filter((row) => {
    if (row.sourceId && knownSourceIds.has(row.sourceId)) {
      return false;
    }
    const rowTime = timeValue(row.at);
    return !current.some((local) => {
      if (local.sourceId || local.kind !== row.kind || local.text !== row.text) {
        return false;
      }
      const localTime = timeValue(local.at);
      if (rowTime === null || localTime === null) {
        return true; // 时刻不可比时按同一条处理，宁可少一条也不重复
      }
      return Math.abs(localTime - rowTime) <= nearMs;
    });
  });
  if (!additions.length) {
    return current;
  }
  return [...current, ...additions].sort((a, b) => {
    const ta = timeValue(a.at);
    const tb = timeValue(b.at);
    if (ta === null || tb === null) {
      return ta === tb ? 0 : ta === null ? 1 : -1;
    }
    return ta - tb;
  });
}
