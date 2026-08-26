// Markdown 围栏块解析（纯函数，与后端 app/adapters/diagram_assets.iter_fences 同规则）：
// 以 ``` 开栏/闭栏，语言标签小写；序号对所有语言的围栏统一计数（从 0 起），
// 后端按序号把浏览器预渲染的 SVG 对号入座，不比对源码。
export interface MarkdownFence {
  index: number;
  lang: string;
  source: string;
}

export function iterFences(markdown: string): MarkdownFence[] {
  const fences: MarkdownFence[] = [];
  let inFence = false;
  let lang = '';
  let buf: string[] = [];
  for (const raw of markdown.split('\n')) {
    const stripped = raw.trim();
    if (stripped.startsWith('```')) {
      if (!inFence) {
        inFence = true;
        lang = stripped.slice(3).trim().toLowerCase();
        buf = [];
      } else {
        fences.push({ index: fences.length, lang, source: buf.join('\n') });
        inFence = false;
      }
      continue;
    }
    if (inFence) buf.push(raw.replace(/\s+$/, ''));
  }
  if (inFence) fences.push({ index: fences.length, lang, source: buf.join('\n') });
  return fences;
}
