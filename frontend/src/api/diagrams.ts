import { apiPostBlob } from './client';

// 图形栅格化：mermaid 浏览器端渲染；plantuml 无 JS 渲染器，走后端本机 plantuml.jar 出图。
export type DiagramFormat = 'mermaid' | 'plantuml';

export const diagramsApi = {
  /** 源码 → PNG Blob（预览用）。渲染失败/工具缺失时后端返 4xx/5xx，调用方降级为源码块。 */
  renderPng(format: DiagramFormat, source: string): Promise<Blob> {
    return apiPostBlob('/diagrams/render', { format, source });
  },
};
