import { apiPostBlob } from './client';

// 图形渲染：mermaid 浏览器端渲染（SVG 由前端 mermaid 库直接产出）；
// plantuml 无 JS 渲染器，走后端本机 plantuml.jar 出图。服务器侧不依赖浏览器。
export type DiagramFormat = 'mermaid' | 'plantuml';

export const diagramsApi = {
  /** 源码 → PNG Blob。渲染失败/工具缺失时后端返 4xx/5xx，调用方降级为源码块。 */
  renderPng(format: DiagramFormat, source: string): Promise<Blob> {
    return apiPostBlob('/diagrams/render', { format, source, output: 'png' });
  },
  /** plantuml 源码 → SVG Blob（image/svg+xml）。mermaid 不走本接口（后端返 422）。 */
  renderSvg(source: string): Promise<Blob> {
    return apiPostBlob('/diagrams/render', { format: 'plantuml', source, output: 'svg' });
  },
};
