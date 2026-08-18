/**
 * 统一 AI 对话控件 · markdown 分部受限渲染（工作包 01 篇 §2.2）。
 *
 * 复用发布预览既有的 `MarkdownPreview` 渲染管线（受限子集：标题/列表/强调/行内代码/表格/代码块），
 * 不 fork、不改共享件。渲染异常时退回纯文本（§2.2 降级「渲染异常退 text」）——LLM 输出可能不合法，
 * 任何渲染问题都不得阻断整条消息，故用错误边界兜底。
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { MarkdownPreview } from '../ui/MarkdownPreview';
import { cwLog } from './log';

class MarkdownErrorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 只记异常存在与位置摘要，不落 markdown 原文（内容纪律）。
    cwLog('WARN', 'chat.markdown.render_failed', {
      error_name: error.name,
      has_component_stack: !!info.componentStack,
    });
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export function MarkdownPart({ text }: { text: string }): ReactNode {
  return (
    <div className="cw-markdown">
      <MarkdownErrorBoundary fallback={<span className="cw-text">{text}</span>}>
        <MarkdownPreview markdown={text} />
      </MarkdownErrorBoundary>
    </div>
  );
}
