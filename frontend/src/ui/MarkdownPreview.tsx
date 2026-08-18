import { createElement, Fragment, type ReactNode } from 'react';
import { MermaidPreview } from './mermaid';
import { PlantumlPreview } from './PlantumlPreview';

// 发布预览的 Markdown 渲染（标题/加粗/列表/表格/代码/图形围栏）。
// 关键：图形围栏渲染为**真 React 组件**（MermaidPreview/PlantumlPreview），各自管理异步与生命周期，
// 不再往 React 托管的 innerHTML 里命令式塞 SVG（那会被重渲/StrictMode 冲掉，只剩图标题）。
// 每个块级元素带 data-line=源码行号（0 基），供源码/预览滚动联动做行映射（见 MarkdownSplit）。

const EMPTY_DIAGRAM = '（图形源码为空）';

function inlineNodes(text: string): ReactNode[] {
  // 仅处理 **加粗**；其余文本由 React 自动转义（受控输入安全）
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .filter(Boolean)
    .map((part, i) =>
      part.startsWith('**') && part.endsWith('**') ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : (
        <Fragment key={i}>{part}</Fragment>
      ),
    );
}

export function MarkdownPreview({ markdown }: { markdown: string }): ReactNode {
  const blocks: ReactNode[] = [];
  let fenceLines: string[] | null = null;
  let fenceStart = 0;
  let fenceLang = '';
  let tableRows: string[][] | null = null;
  let tableStart = 0;
  let key = 0;

  const flushFence = () => {
    if (fenceLines === null) return;
    const src = fenceLines.join('\n');
    const dl = fenceStart;
    if (fenceLang === 'mermaid') {
      blocks.push(
        <figure key={key++} data-line={dl} className="md-diagram">
          <MermaidPreview chartRef={`mdp-${dl}`} version={0} code={src} emptyText={EMPTY_DIAGRAM} />
        </figure>,
      );
    } else if (fenceLang === 'plantuml') {
      blocks.push(
        <figure key={key++} data-line={dl} className="md-diagram">
          <PlantumlPreview code={src} emptyText={EMPTY_DIAGRAM} />
        </figure>,
      );
    } else {
      blocks.push(
        <pre key={key++} data-line={dl}>
          <code>{src}</code>
        </pre>,
      );
    }
    fenceLines = null;
  };

  const flushTable = () => {
    if (!tableRows || tableRows.length === 0) {
      tableRows = null;
      return;
    }
    const [head, ...body] = tableRows;
    blocks.push(
      <table key={key++} data-line={tableStart}>
        <thead>
          <tr>{head.map((cell, ci) => <th key={ci}>{inlineNodes(cell)}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri}>{row.map((cell, ci) => <td key={ci}>{inlineNodes(cell)}</td>)}</tr>
          ))}
        </tbody>
      </table>,
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
        fenceLang = line.slice(3).trim().toLowerCase();
      } else {
        flushFence();
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
      blocks.push(
        createElement(`h${level}`, { key: key++, 'data-line': i }, inlineNodes(heading[2])),
      );
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      blocks.push(
        <li key={key++} data-line={i}>{inlineNodes(line.replace(/^[-*]\s+/, ''))}</li>,
      );
      continue;
    }
    blocks.push(<p key={key++} data-line={i}>{inlineNodes(line)}</p>);
  }
  flushTable();
  flushFence();
  return <>{blocks}</>;
}
