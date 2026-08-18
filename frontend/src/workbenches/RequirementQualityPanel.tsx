/**
 * 需求质量诊断器 · 共享面板（v2 签名件，高保真复刻 需求管理工作台高密度重设计-v2.html 的 lint 区）。
 * 两处共用（详情卡 + 评审流程）；纯展示 + 诊断↔原文双向定位；诊断/一键修复经回调注入。
 * 自带 .rmv2-root 作用域，样式来自 styles-rmv2.css，可脱离外壳单独使用（评审流程）。
 */
import { useState } from 'react';
import '../styles-rmv2.css';
import type { QualityPanelVM } from '../view-models/requirement-quality';
import { segmentAnnotations } from '../view-models/requirement-quality';
import { RmIcon } from '../ui/rmv2-icons';

interface Props {
  vm: QualityPanelVM;
  diagnosing?: boolean;
  readOnly?: boolean;
  onDiagnose?: () => void;
  onAdoptPoint?: (pointRef: string) => void;
  /** true 时自带 .rmv2-root 作用域（评审流程用）；详情卡已在 .rmv2-root 内则传 false */
  scoped?: boolean;
}

const RADAR_ORDER = ['unambiguous', 'verifiable', 'singular', 'complete', 'consistent', 'traceable'];
const sevClass: Record<string, string> = { high: 'h', medium: 'm', low: 'l' };

function radarVertices(dims: { key: string; score: number }[]): [number, number][] {
  const by = new Map(dims.map((d) => [d.key, d.score]));
  const cx = 60, cy = 60, r = 44;
  return RADAR_ORDER.map((k, i) => {
    const score = (by.get(k) ?? 0) / 100;
    const ang = -Math.PI / 2 + (i * Math.PI) / 3;
    return [cx + Math.cos(ang) * r * score, cy + Math.sin(ang) * r * score] as [number, number];
  });
}
function radarRingVertices(scale: number): [number, number][] {
  const cx = 60, cy = 60, r = 44 * scale;
  return Array.from({ length: 6 }, (_, i) => {
    const ang = -Math.PI / 2 + (i * Math.PI) / 3;
    return [cx + Math.cos(ang) * r, cy + Math.sin(ang) * r] as [number, number];
  });
}
const toPoints = (pts: [number, number][]) => pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');

function ringDash(score: number): number {
  return 94.2 - (94.2 * score) / 100; // r=15 圆周 ≈ 94.2
}

export function RequirementQualityPanel({
  vm, diagnosing, readOnly, onDiagnose, onAdoptPoint, scoped = true,
}: Props) {
  const [flash, setFlash] = useState<number | null>(null);

  const locate = (n: number) => {
    if (!n) return;
    setFlash(null);
    requestAnimationFrame(() => requestAnimationFrame(() => setFlash(n)));
    document.querySelector(`[data-rq="${n}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const diagnoseBtn = onDiagnose ? (
    <button className="btn primary" disabled={diagnosing || readOnly} onClick={onDiagnose} type="button">
      {diagnosing ? '诊断中…' : vm.hasDiagnosis ? '重新诊断' : '发起诊断'}
    </button>
  ) : null;

  const inner = !vm.hasDiagnosis ? (
    <section className="sec">
      <div className="rq-empty-bar">
        <RmIcon name="scan" className="sm" />
        <span>该条目尚无质量诊断结论；诊断在条目评审阶段发起，结论回流本卡。</span>
        {readOnly ? null : diagnoseBtn}
      </div>
    </section>
  ) : (
    <>
      <section className="sec">
        <div className="sh">
          <h4><RmIcon name="scan" className="sm" />需求陈述 · 质量诊断</h4>
          <span className="aux">
            <span className="tag gray mono">ISO 29148</span>
            <span className="tag gray mono">INCOSE</span>
            {vm.overall != null ? <span className="tag blue mono">质量分 {vm.overall}</span> : null}
            {diagnoseBtn}
          </span>
        </div>
        <div className="lint">
          <div>
            <div className="stmt-wrap">
              <div className="stmt-gutter">
                <i style={{ background: 'var(--red)' }} />
                <i style={{ background: 'var(--amber)' }} />
                <i style={{ background: 'var(--indigo)' }} />
              </div>
              <p className="stmt">
                {segmentAnnotations(vm.baseExpression, vm.spans).map((seg, i) =>
                  seg.span ? (
                    <span
                      key={i}
                      data-rq={seg.span.n}
                      className={`sm sm-${sevClass[seg.span.severity] ?? 'l'} ${flash === seg.span.n ? 'flash' : ''}`}
                      onClick={() => locate(seg.span!.n)}
                      title="点击定位诊断项"
                    >
                      {seg.text}<sup>{seg.span.n}</sup>
                    </span>
                  ) : (
                    <span key={i}>{seg.text}</span>
                  ),
                )}
              </p>
            </div>
            {vm.ears ? (
              <div className="ears">
                <div className="ears-h">
                  <span className="eyebrow">EARS 规范化改写建议</span>
                  {vm.ears.patternLabel ? (
                    <span className="tag indigo" style={{ marginLeft: 'auto' }}>{vm.ears.patternLabel}</span>
                  ) : null}
                </div>
                <div className="ears-body">
                  {vm.ears.lines.map((line, i) => (
                    <div key={i}>{renderEarsLine(line)}</div>
                  ))}
                </div>
                <div className="ears-meta">{vm.ears.note || 'EARS 脚手架，供人工润色确认；不自动写入需求事实。'}</div>
              </div>
            ) : null}
          </div>
          <div className="diag">
            {vm.findings.map((f) => (
              <div
                key={f.findingRef}
                data-rq={f.n || undefined}
                className={`diag-item ${flash === f.n ? 'flash' : ''}`}
              >
                <span className={`num-b nb-${f.n ? sevClass[f.severity] ?? 'l' : 'n'}`}>{f.n || '·'}</span>
                <div className="d-body">
                  <div className="d-top">
                    {f.ruleCode ? <span className="rule">{f.ruleCode}</span> : null}
                    <span className={`sev ${sevClass[f.severity] ?? 'l'}`}>
                      {f.severityText}{f.dimensionLabel ? ` · ${f.dimensionLabel}` : ''}
                    </span>
                  </div>
                  <p>{f.summary}</p>
                  {f.autofix && onAdoptPoint && !readOnly ? (
                    <button className="fix" onClick={() => onAdoptPoint(f.pointRef!)} disabled={diagnosing} type="button">
                      ✦ 一键修复
                    </button>
                  ) : f.hasSpan ? (
                    <button className="fix" onClick={() => locate(f.n)} type="button">↳ 定位原文</button>
                  ) : null}
                </div>
              </div>
            ))}
            {vm.passNotes.map((note, i) => (
              <div className="diag-pass" key={`pass-${i}`}>
                <RmIcon name="check" className="sm" />
                <span>{note}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {vm.dims.length > 0 || vm.sourceAlignments.length > 0 ? (
        <div className="g2">
          {vm.dims.length > 0 ? (
            <section className="sec">
              <div className="sh"><h4><RmIcon name="target" className="sm" />质量画像</h4><span className="aux">6 维判据 · 非权威载荷</span></div>
              <div className="radar-wrap">
                <svg width="120" height="120" viewBox="0 0 120 120">
                  <polygon points={toPoints(radarRingVertices(1))} fill="none" stroke="var(--line)" strokeWidth="1" />
                  <polygon points={toPoints(radarRingVertices(0.5))} fill="none" stroke="var(--line-soft)" strokeWidth="1" />
                  {radarRingVertices(1).map(([x, y], i) => (
                    <line key={i} x1="60" y1="60" x2={x.toFixed(1)} y2={y.toFixed(1)} stroke="var(--line-soft)" strokeWidth="1" />
                  ))}
                  <polygon points={toPoints(radarVertices(vm.dims))} fill="color-mix(in srgb, var(--indigo) 16%, transparent)" stroke="var(--indigo)" strokeWidth="1.6" />
                  {radarVertices(vm.dims).map(([x, y], i) => (
                    <circle key={i} cx={x.toFixed(1)} cy={y.toFixed(1)} r="2" fill="var(--indigo)" />
                  ))}
                </svg>
                <ul className="radar-list">
                  {RADAR_ORDER.map((k) => {
                    const d = vm.dims.find((x) => x.key === k);
                    if (!d) return null;
                    const cls = d.score >= 80 ? 'ok' : d.score >= 65 ? '' : d.score >= 50 ? 'warn' : 'bad';
                    return <li key={k}>{d.label} <b className={cls}>{d.score}</b></li>;
                  })}
                </ul>
              </div>
            </section>
          ) : null}
          {vm.sourceAlignments.length > 0 ? (
            <section className="sec">
              <div className="sh"><h4><RmIcon name="link" className="sm" />来源依据 · 语义对齐</h4><span className="aux">原文即证据，不随修订改写</span></div>
              {vm.sourceAlignments.map((a) => (
                <div key={a.elementRef} className={`src ${a.drift ? 'drift' : ''}`}>
                  <span className="anc">{a.anchor ?? a.elementRef.slice(0, 8)}</span>
                  <div className="sb">
                    {a.note ?? '来源要素'}
                    {a.drift && a.driftTokens.length > 0 ? (
                      <div className="sm-meta">⚠ 偏离：{a.driftTokens.join('、')}</div>
                    ) : null}
                    {a.alignmentPct != null ? (
                      <div className="align">
                        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-4)' }}>对齐度</span>
                        <span className="albar"><i style={{ width: `${a.alignmentPct}%` }} /></span>
                        <span className="alv">{(a.alignment ?? 0).toFixed(2)}</span>
                      </div>
                    ) : (
                      <div className="sm-meta">对齐度待诊断</div>
                    )}
                  </div>
                </div>
              ))}
            </section>
          ) : null}
        </div>
      ) : null}
    </>
  );

  return scoped ? <div className="rmv2-root">{inner}</div> : inner;
}

/** EARS 行：把 WHEN/SHALL/IF/THE… 关键词高亮（复刻 mockup 的 .kw）。 */
function renderEarsLine(line: string) {
  const parts = line.split(/(WHEN|WHILE|WHERE|IF|THEN|THE [^，,。]+?(?=\s)|SHALL NOT|SHALL)/g);
  return parts.map((p, i) => {
    if (/^(WHEN|WHILE|WHERE|IF|THEN|SHALL NOT|SHALL)$/.test(p)) return <span key={i} className="kw">{p}</span>;
    if (/^THE /.test(p)) return <span key={i} className="kw sys">{p}</span>;
    return <span key={i}>{p}</span>;
  });
}
