/**
 * 链路回执条（04A §2.1 增补）：AI 请求全链路的消息锚定投影。
 * 只渲染 VM（ai-request-trace.ts），不自算阶段；在途展开、完成由调用方收敛为耗时摘要。
 */
import {
  formatDuration,
  projectTrace,
  type AiRequestTrace,
} from '../view-models/ai-request-trace';

export function AiTraceRail({ trace, now }: { trace: AiRequestTrace; now: number }) {
  const vm = projectTrace(trace, now);
  const activeHint = vm.nodes.find((n) => n.stallHint)?.stallHint ?? null;
  return (
    <div aria-label="AI 请求链路" className={`ai-trace${vm.finished ? ' ai-trace--finished' : ''}`} role="status">
      <span className="ai-trace__rail">
        {vm.nodes.map((node, index) => (
          <span className={`ai-trace__node ai-trace__node--${node.state}`} key={node.stage}>
            {index > 0 ? <span aria-hidden className="ai-trace__link" /> : null}
            <span aria-hidden className="ai-trace__dot" />
            <span className="ai-trace__label">
              {node.label}
              {(node.state === 'active' || node.state === 'stalled') && node.elapsedMs !== null
                ? ` ${formatDuration(node.elapsedMs)}`
                : ''}
            </span>
          </span>
        ))}
      </span>
      <span className="ai-trace__total">⏱ {formatDuration(vm.totalMs)}</span>
      {activeHint ? <span className="ai-trace__hint">⚠ {activeHint}</span> : null}
      {trace.stallAlert ? <span className="ai-trace__alert">✕ {trace.stallAlert}</span> : null}
    </div>
  );
}

/** 完成回执内的可展开链路详情（各阶段观测时刻 + 运行引用，供与 dialogue.* 日志对账）。 */
export function AiTraceDetail({ lines }: { lines: string[] }) {
  if (!lines.length) {
    return null;
  }
  return (
    <details className="ai-trace-detail">
      <summary>链路详情</summary>
      {lines.map((line) => (
        <code key={line}>{line}</code>
      ))}
    </details>
  );
}
