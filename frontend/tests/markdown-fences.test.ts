import { describe, expect, it } from 'vitest';
import { iterFences } from '../src/lib/markdown-fences';

describe('iterFences（与后端 diagram_assets.iter_fences 同规则）', () => {
  it('按出现顺序对所有语言的围栏统一计序', () => {
    const md = '# t\n\n```python\nprint(1)\n```\n\n```mermaid\nflowchart LR\n A --> B\n```\n\n```PlantUML\n@startuml\n@enduml\n```\n';
    expect(iterFences(md).map((f) => [f.index, f.lang])).toEqual([[0, 'python'], [1, 'mermaid'], [2, 'plantuml']]);
    expect(iterFences(md)[1].source).toBe('flowchart LR\n A --> B');
  });
  it('未闭合围栏一直到文末', () => {
    expect(iterFences('```mermaid\ngraph TD\n A')).toEqual([{ index: 0, lang: 'mermaid', source: 'graph TD\n A' }]);
  });
  it('没有围栏返回空表', () => {
    expect(iterFences('正文而已')).toEqual([]);
  });
});
