/**
 * 需求质量诊断器 ViewModel（v2 签名件）：从 AEP-105 ItemQualityRead 建 QualityPanelVM。
 *
 * MVVM 纪律：只做展示投影，不复制领域规则。span 偏移用 evidence_span 在 base_expression 内
 * indexOf（与后端 _anchor_once 同口径：唯一定位）；autofix = 该 finding 绑定了修订点。
 * 契约事实源：docs/proposals/requirement-management-redesign/02_质量诊断引擎与契约设计.md §7。
 */
import type { ItemQualityRead, ReviewFindingRead, SourceAlignmentRead } from '../api/quality';
import type { BadgeTone } from './common';
import { verdictKindText } from './requirement-item-review';

// ---- 文案映射（单一来源在后端 labels.py；前端只做展示名） ----

export function qualityDimensionText(key: string): string {
  const map: Record<string, string> = {
    unambiguous: '无歧义', verifiable: '可验证', singular: '原子性',
    complete: '完整性', consistent: '一致性', traceable: '可追溯',
  };
  return map[key] ?? key;
}

export function qualityRuleText(code?: string | null): string {
  if (!code) return '';
  const map: Record<string, string> = {
    'INCOSE-R21': '可选逃逸子句', 'INCOSE-R7': '模糊量词', 'SMELL-UNDEF': '未定义阈值',
    'MODAL-WEAK': '弱化情态', 'SMELL-COMPOUND': '复合动作', 'SMELL-PASSIVE': '被动语态',
    'SMELL-UNIVERSAL': '全称量词', 'EARS-INCOMPLETE': 'EARS 要件缺失', 'SRC-DRIFT': '来源偏离',
  };
  return map[code] ?? code;
}

export function earsPatternText(pattern?: string | null): string {
  if (!pattern) return '';
  const map: Record<string, string> = {
    ubiquitous: '泛在', event_driven: '事件驱动', state_driven: '状态驱动',
    unwanted: '非期望行为', optional: '可选特性', complex: '复合',
  };
  return map[pattern] ?? pattern;
}

export function severityMeta(sev: string): { label: string; tone: BadgeTone } {
  const map: Record<string, { label: string; tone: BadgeTone }> = {
    high: { label: '高', tone: 'danger' },
    medium: { label: '中', tone: 'warning' },
    low: { label: '低', tone: 'neutral' },
  };
  return map[sev] ?? { label: sev, tone: 'neutral' };
}

function scoreTone(score: number): BadgeTone {
  if (score >= 80) return 'success';
  if (score >= 65) return 'processing';
  if (score >= 50) return 'warning';
  return 'danger';
}

// ---- VM 形状 ----

export interface QualityDimVM {
  key: string;
  label: string;
  score: number;
  tone: BadgeTone;
}

export interface QualityFindingVM {
  n: number; // 问题序号（1 起；no_blocker 不入列表故不占号）
  findingRef: string;
  ruleCode: string | null;
  ruleLabel: string;
  severity: string;
  severityText: string;
  severityTone: BadgeTone;
  dimensionLabel: string;
  summary: string;
  basis: string;
  span: { start: number; end: number } | null;
  hasSpan: boolean; // 是否标注到原文一段 → 决定给不给「定位原文」入口（无标注给了也只闪自己）
  pointRef: string | null; // 绑定修订点 → 一键修复
  autofix: boolean;
}

export interface QualitySpanVM {
  n: number;
  start: number;
  end: number;
  severity: string;
}

export interface QualityAlignmentVM {
  elementRef: string;
  wing: string | null;
  anchor: string | null;
  alignment: number | null;
  alignmentPct: number | null;
  drift: boolean;
  driftTokens: string[];
  note: string | null;
  tone: BadgeTone;
}

export interface QualityPanelVM {
  hasDiagnosis: boolean;
  baseExpression: string;
  roundRef: string | null;
  status: string;
  overall: number | null;
  verdictText: string;
  verdictSummary: string;
  dims: QualityDimVM[];
  findings: QualityFindingVM[];
  /** 通过性说明（no_blocker 发现项的白话结论）：不编号、不计入问题数、不给定位入口。 */
  passNotes: string[];
  spans: QualitySpanVM[];
  ears: { patternLabel: string; lines: string[]; note: string } | null;
  sourceAlignments: QualityAlignmentVM[];
  tally: { high: number; medium: number; low: number };
}

export const EMPTY_QUALITY_PANEL: QualityPanelVM = {
  hasDiagnosis: false, baseExpression: '', roundRef: null, status: '',
  overall: null, verdictText: '—', verdictSummary: '', dims: [], findings: [],
  passNotes: [], spans: [], ears: null, sourceAlignments: [], tally: { high: 0, medium: 0, low: 0 },
};

/** 从 ItemQualityRead 建面板 VM；span 用 evidence_span 在 base 内唯一定位，找不到则无 span。 */
export function buildQualityPanelVM(q: ItemQualityRead): QualityPanelVM {
  if (!q.has_diagnosis) {
    return { ...EMPTY_QUALITY_PANEL, baseExpression: q.base_expression };
  }
  const base = q.base_expression;
  // 修订点挂到哪条发现项：优先按发现项引用配对。
  // finding_index 是模型输出的序号，与 findings 数组的读出序不是一回事（读出序由后端排序
  // 决定），照它当下标用会把「一键修复」按钮挂到错误的行上。存量轮次拿不到引用时才回退下标，
  // 与改前行为一致。
  const pointByFindingRef = new Map<string, string>();
  const pointByFindingIndex = new Map<number, string>();
  (q.revision_points ?? []).forEach((p) => {
    if (p.finding_ref) {
      pointByFindingRef.set(p.finding_ref, p.point_ref);
    } else if (typeof p.finding_index === 'number') {
      pointByFindingIndex.set(p.finding_index, p.point_ref);
    }
  });

  // 先算 span（唯一定位），再按 start 排序去重叠、编号
  // 类型直接用生成的 ReviewFindingRead：schema.ts 已再生，vetoed / veto_reason /
  // source_attested 都在其中，此前的局部加宽交叉类型（FindingWithVeto）随之删除（issue #97）。
  type Raw = { f: ReviewFindingRead; idx: number; span: { start: number; end: number } | null };
  // idx 保留在 q.findings 中的原始下标，供存量轮次按 finding_index 回退配对；no_blocker 不入
  // 问题列表（不编号、不计问题数、不给定位入口），只单列出一行白话通过说明。
  const allRaws: Raw[] = (q.findings ?? []).map((f, idx) => {
    let span: { start: number; end: number } | null = null;
    if (f.evidence_span) {
      const s = base.indexOf(f.evidence_span);
      if (s >= 0 && base.indexOf(f.evidence_span, s + 1) < 0) {
        span = { start: s, end: s + f.evidence_span.length };
      }
    }
    return { f, idx, span };
  });
  // C41/C10：用户在区5 把某条判成「不是问题」后，区4 不能还把它当问题——此前它照常带序号、
  // 带严重度色块、还给「一键修复」，点下去必被后端拒绝（那条改法已不可采纳），是一条死路。
  // 已标记的问题移出问题列表（不编号、不计数、不标注原文、不给一键修复），改为与「未发现
  // 阻断问题」同列一行说明，用户仍看得见它的存在，撤销入口在区5。
  //
  // 人工确认降格（source_attested）与它同构，2026-07-25 冷审查 K2 消费时并入同一条通路：
  // 后端把这条来源对齐类发现降为非阻断提示，区5 据此对用户写着「这条不用改，采纳时不会应用
  // 它」，而区4 此前照旧给它序号、严重度色块和会写库的〔一键修复〕——同一屏两处对同一条发现
  // 项给相反指令，其中一处点下去会改写条目表达。降格项与被否决项同样移出问题列表。
  const isVetoed = (f: ReviewFindingRead) => f.vetoed === true;
  const isAttested = (f: ReviewFindingRead) => f.source_attested === true;
  const isHandled = (f: ReviewFindingRead) => isVetoed(f) || isAttested(f);
  const raws: Raw[] = allRaws.filter((r) => r.f.finding_type !== 'no_blocker' && !isHandled(r.f));
  const passNotes: string[] = [
    ...allRaws
      .filter((r) => r.f.finding_type === 'no_blocker')
      .map((r) => r.f.diagnosis_summary?.trim() || '未发现阻断问题'),
    ...allRaws
      .filter((r) => r.f.finding_type !== 'no_blocker' && isVetoed(r.f))
      .map((r) => `你已标为不是问题：${r.f.diagnosis_summary?.trim() || r.f.rule_code || '这一条'}`),
    ...allRaws
      .filter((r) => r.f.finding_type !== 'no_blocker' && isAttested(r.f) && !isVetoed(r.f))
      .map((r) => `不用你处理（来源＝人工确认）：${r.f.diagnosis_summary?.trim() || r.f.rule_code || '这一条'}`),
  ];

  // 原文标注分配：每条问题都要在原文里指到一处，包住别人的那条也不能把里面的挤掉
  // （用户走查第 1 轮意见：「超时后」被「暂缓发货；超时后自动」整个包住，标注被丢掉，
  // 原文里就缺了 ⁴ 这个上标）。
  // 按证据长度升序占位——最具体的证据先拿到它那一段；范围大的后来者取剩下最长的一段
  // 连续空档，于是每条问题的上标各出现一次，既不重复也不缺号。
  // 一处空档都没有的（证据被完全占满）只在列表里编号，不上标注、也不给「定位原文」。
  const byLength = raws
    .filter((r) => r.span)
    .sort((a, b) => (a.span!.end - a.span!.start) - (b.span!.end - b.span!.start));
  const marks = new Map<number, { start: number; end: number }>();
  const taken: Array<{ start: number; end: number }> = [];
  for (const r of byLength) {
    // 该证据范围内尚未被占用的连续空档，取最长的一段
    let best: { start: number; end: number } | null = null;
    let cursor = r.span!.start;
    const blocking = taken
      .filter((t) => t.start < r.span!.end && t.end > r.span!.start)
      .sort((a, b) => a.start - b.start);
    for (const t of [...blocking, { start: r.span!.end, end: r.span!.end }]) {
      const gapEnd = Math.min(t.start, r.span!.end);
      if (gapEnd > cursor && (!best || gapEnd - cursor > best.end - best.start)) {
        best = { start: cursor, end: gapEnd };
      }
      cursor = Math.max(cursor, t.end);
    }
    if (best) {
      marks.set(r.idx, best);
      taken.push(best);
    }
  }

  // 编号是「问题序号」，不是「原文标注序号」——每条问题都要有号（用户走查第 1 轮意见：
  // 面板右上写「检出 4 项」而列表只编到 3、还有一行显示「·」，读的人无法判断到底几个问题）。
  // 有标注的按标注在原文里出现的先后编号（上标从左到右升序），其余按诊断读出顺序接在后面。
  const kept: Raw[] = raws
    .filter((r) => marks.has(r.idx))
    .sort((a, b) => marks.get(a.idx)!.start - marks.get(b.idx)!.start);
  const nByIdx = new Map<number, number>();
  kept.forEach((r, i) => nByIdx.set(r.idx, i + 1));
  let nextNo = kept.length;
  raws.forEach((r) => {
    if (!nByIdx.has(r.idx)) {
      nextNo += 1;
      nByIdx.set(r.idx, nextNo);
    }
  });

  const findings: QualityFindingVM[] = raws.map((r) => {
    const pointRef = pointByFindingRef.get(r.f.finding_ref) ?? pointByFindingIndex.get(r.idx) ?? null;
    const sm = severityMeta(r.f.severity);
    return {
      n: nByIdx.get(r.idx) ?? 0,
      findingRef: r.f.finding_ref,
      ruleCode: r.f.rule_code ?? null,
      ruleLabel: qualityRuleText(r.f.rule_code),
      severity: r.f.severity,
      severityText: sm.label,
      severityTone: sm.tone,
      dimensionLabel: qualityDimensionText(r.f.dimension ?? ''),
      summary: r.f.diagnosis_summary,
      basis: r.f.basis_summary,
      // 只有参与原文标注的那几条带 span；取实际标注到的那一段，与原文里高亮的范围一致
      span: marks.get(r.idx) ?? null,
      // 未标注者定位不了原文，不给「定位原文」入口（给了点下去也只会把它自己闪一下）
      hasSpan: marks.has(r.idx),
      pointRef,
      // 第二道锁：降格项与被否决项已被上面的过滤挡在问题列表之外，理论上到不了这里。
      // 仍显式关掉〔一键修复〕——它按下去会直接调裁决接口写库改写条目表达，过滤条件将来
      // 若被改动，这里能保证写入口不会跟着一起松开（K2）。
      autofix: pointRef != null && !isHandled(r.f),
    };
  });
  // 行按问题序号排：原来行按诊断读出序、编号按原文位置排，两套顺序混在一起，
  // 列表读下来是 1、3、·、2（用户走查第 1 轮意见）。
  findings.sort((a, b) => a.n - b.n);

  const spans: QualitySpanVM[] = kept.map((r) => ({
    n: nByIdx.get(r.idx)!, start: marks.get(r.idx)!.start, end: marks.get(r.idx)!.end,
    severity: r.f.severity,
  }));

  const qp = q.quality_profile as { overall?: number; dimensions?: Array<{ key: string; score: number }> } | null;
  const dims: QualityDimVM[] = (qp?.dimensions ?? []).map((d) => ({
    key: d.key, label: qualityDimensionText(d.key), score: d.score, tone: scoreTone(d.score),
  }));

  const er = q.ears_rewrite as { pattern_type?: string | null; lines?: string[]; note?: string } | null;
  const ears = er && (er.lines?.length ?? 0) > 0
    ? { patternLabel: earsPatternText(er.pattern_type), lines: er.lines ?? [], note: er.note ?? '' }
    : null;

  const tally = { high: 0, medium: 0, low: 0 };
  findings.forEach((f) => {
    if (f.severity in tally) tally[f.severity as 'high' | 'medium' | 'low'] += 1;
  });

  return {
    hasDiagnosis: true,
    baseExpression: base,
    roundRef: q.round_ref ?? null,
    status: q.status,
    overall: typeof qp?.overall === 'number' ? qp.overall : null,
    verdictText: verdictKindText(q.verdict_kind),
    verdictSummary: q.verdict_summary ?? '',
    dims,
    findings,
    passNotes,
    spans,
    ears,
    sourceAlignments: (q.source_alignments ?? []).map(mapAlignment),
    tally,
  };
}

function mapAlignment(a: SourceAlignmentRead): QualityAlignmentVM {
  const pct = typeof a.alignment === 'number' ? Math.round(a.alignment * 100) : null;
  return {
    elementRef: a.element_ref,
    wing: a.wing ?? null,
    anchor: a.anchor ?? null,
    alignment: a.alignment ?? null,
    alignmentPct: pct,
    drift: !!a.drift,
    driftTokens: a.drift_tokens ?? [],
    note: a.note ?? null,
    tone: a.drift ? 'warning' : pct != null && pct >= 85 ? 'success' : 'neutral',
  };
}

/** 标注视图切段：把 base 按 spans 切成 [{text, span?}]，供组件渲染波浪线 + 编号。 */
export function segmentAnnotations(
  base: string,
  spans: QualitySpanVM[],
): Array<{ text: string; span: QualitySpanVM | null }> {
  const out: Array<{ text: string; span: QualitySpanVM | null }> = [];
  let cur = 0;
  for (const s of [...spans].sort((a, b) => a.start - b.start)) {
    if (s.start > cur) out.push({ text: base.slice(cur, s.start), span: null });
    out.push({ text: base.slice(s.start, s.end), span: s });
    cur = s.end;
  }
  if (cur < base.length) out.push({ text: base.slice(cur), span: null });
  return out;
}
