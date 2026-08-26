import { Alert, Empty } from 'antd';
import mermaid from 'mermaid';
import { useEffect, useState } from 'react';
import { readChartPalette, useTheme, type ThemeKey } from './theme';
import { iterFences } from '../lib/markdown-fences';

// mermaid 全局配置随主题切换重建；按主题键幂等（StrictMode 双调用安全），渲染由组件显式驱动。
let lastMermaidThemeKey: ThemeKey | null = null;

export function configureMermaidTheme(themeKey: ThemeKey): void {
  if (lastMermaidThemeKey === themeKey) {
    return;
  }
  lastMermaidThemeKey = themeKey;
  const style = getComputedStyle(document.documentElement);
  const token = (name: string) => style.getPropertyValue(name).trim() || undefined;
  const palette = readChartPalette();
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    // 文字用纯 SVG <text> 而不是 foreignObject 里的 HTML：后者只有浏览器认得，
    // 图片查看器、Word 与 resvg（docx 备用 PNG）都不渲染，导出的图会缺字（2026-08-26 走查发现）。
    htmlLabels: false,
    // B 用 mermaid 内置暗色基底；其余浅色主题在 default 基底上注入令牌（暗色不复用浅色板）
    theme: themeKey === 'b-xuanye' ? 'dark' : 'default',
    themeVariables: {
      primaryColor: token('--color-primary-soft'),
      primaryTextColor: token('--color-title'),
      primaryBorderColor: token('--color-primary'),
      lineColor: token('--color-text'),
      textColor: token('--color-text'),
      ...Object.fromEntries(palette.map((color, index) => [`pie${index + 1}`, color])),
    },
  });
}

// ---- 发布前预渲染（AppImage 单机模式方案 §3.2 第 3 项）----
// 服务器不装浏览器，mermaid 的 SVG 只能在这里（用户浏览器）出：发起导出时把正文里的每个 mermaid
// 围栏渲染成 SVG 随请求提交；单个围栏失败只记原因，后端保留该块源码并在导出结果里报告。
export interface PrerenderedDiagram {
  fence_index: number;
  format: 'mermaid';
  svg?: string;
  error?: string;
}

export async function prerenderMermaidFences(markdown: string, themeKey: ThemeKey): Promise<PrerenderedDiagram[]> {
  const fences = iterFences(markdown).filter((f) => f.lang === 'mermaid');
  if (fences.length === 0) return [];
  configureMermaidTheme(themeKey);
  const out: PrerenderedDiagram[] = [];
  for (const fence of fences) {
    const id = `mmd-export-${fence.index}-${Date.now()}`;
    try {
      const result = await mermaid.render(id, fence.source);
      out.push({ fence_index: fence.index, format: 'mermaid', svg: result.svg });
    } catch (err: unknown) {
      document.getElementById(`d${id}`)?.remove();
      out.push({ fence_index: fence.index, format: 'mermaid', error: err instanceof Error ? err.message : String(err) });
    }
  }
  return out;
}

// ---- Mermaid 实时预览（渲染失败只影响预览，不影响事实源）----

export function MermaidPreview({
  chartRef,
  version,
  code,
  emptyText = '尚无受控源码；编辑源码或请求 AI 建议后展示预览',
}: {
  chartRef: string;
  version: number;
  code: string;
  emptyText?: string;
}) {
  const [svg, setSvg] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const { themeKey } = useTheme();

  useEffect(() => {
    let cancelled = false;
    if (!code.trim()) {
      setSvg('');
      setError(null);
      return;
    }
    configureMermaidTheme(themeKey);
    const id = `mmd-${chartRef.slice(0, 8)}-${version}-${Date.now()}`;
    mermaid
      .render(id, code)
      .then((result) => {
        if (!cancelled) {
          setSvg(result.svg);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSvg('');
          setError(err instanceof Error ? err.message : String(err));
        }
        // mermaid 渲染失败会残留孤儿节点，清理避免污染 DOM
        document.getElementById(`d${id}`)?.remove();
      });
    return () => {
      cancelled = true;
    };
  }, [chartRef, version, code, themeKey]);

  if (!code.trim()) {
    return <Empty description={emptyText} />;
  }
  if (error) {
    return (
      <Alert
        title="预览渲染失败（不影响已保存的事实）"
        description={error}
        showIcon
        type="warning"
      />
    );
  }
  // svg 由 mermaid strict 模式生成
  return <div className="mermaid-preview" dangerouslySetInnerHTML={{ __html: svg }} />;
}
