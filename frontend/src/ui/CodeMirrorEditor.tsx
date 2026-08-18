import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import {
  Compartment,
  EditorState,
  RangeSet,
  RangeSetBuilder,
  StateEffect,
  StateField,
} from '@codemirror/state';
import {
  Decoration,
  EditorView,
  GutterMarker,
  gutter,
  keymap,
  lineNumbers,
} from '@codemirror/view';
import type { DecorationSet } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, redo, undo } from '@codemirror/commands';
import { HighlightStyle, Language, defineLanguageFacet, syntaxHighlighting } from '@codemirror/language';
import { GFM, parser as markdownBaseParser } from '@lezer/markdown';
import { tags } from '@lezer/highlight';

// 直接用 @lezer/markdown 解析器（GFM：表格/删除线/任务列表/autolink），不经 @codemirror/lang-markdown。
// 后者顶层 `import … from '@codemirror/lang-html'` 会连带拉入 lezer html/css/javascript 语法
// （占构建增量近半），而本页 Markdown 编辑不需要内嵌 HTML 高亮，故绕开。语法 highlight 标记由
// @lezer/markdown 解析器内建 styleTags 提供，配下方 HighlightStyle 即生效。
const markdownLanguage = new Language(
  defineLanguageFacet(),
  markdownBaseParser.configure([GFM]),
  [],
  'markdown',
);
import { diffMarkdownLines } from '../view-models/publication';
import type { MarkdownDiffVM } from '../view-models/publication';

// CodeMirror 6 源码编辑器（发布台 Markdown 编辑页 D2/D3）。
// 薄 React 包装：管 EditorView 生命周期，把 value/baseline/disabled/wordWrap 同步进
// 编辑器；diff 行装饰＋gutter 标记由自写行级 LCS（diffMarkdownLines）驱动；语法配色走
// --tok-* 令牌（HighlightStyle 映射到 CSS class）。改动分类仍是后端职责，此处只做视觉。

export interface CodeMirrorEditorHandle {
  scrollToLine: (line: number) => void;
  focusHunk: (line: number) => void;
  undo: () => void;
  redo: () => void;
}

interface CodeMirrorEditorProps {
  value: string;
  /** diff 基线＝生成稿（载入/上次保存的服务端内容快照） */
  baseline: string;
  disabled: boolean;
  wordWrap: boolean;
  onChange: (value: string) => void;
  /** 每次重算 diff 后回传，供修订摘要条读计数/hunk */
  onDiff?: (diff: MarkdownDiffVM) => void;
  /** 滚动时回传顶部可见源码行（0 基），供预览联动 */
  onScrollTopLine?: (line: number) => void;
}

// ---- diff 装饰：由 setDiff effect 承载 diffMarkdownLines 结果 ----
const setDiff = StateEffect.define<MarkdownDiffVM>();
const setActiveHunk = StateEffect.define<number>();

const addLineDeco = Decoration.line({ class: 'cm-diff-add' });
const chgLineDeco = Decoration.line({ class: 'cm-diff-chg' });
const delLineDeco = Decoration.line({ class: 'cm-diff-del' });
const activeLineDeco = Decoration.line({ class: 'cm-diff-active' });

function buildDiffDeco(state: EditorState, diff: MarkdownDiffVM, activeLine: number): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const doc = state.doc;
  const n = Math.min(diff.lines.length, doc.lines);
  for (let i = 0; i < n; i += 1) {
    const info = diff.lines[i];
    const from = doc.line(i + 1).from;
    if (info.status === 'add') builder.add(from, from, addLineDeco);
    else if (info.status === 'chg') builder.add(from, from, chgLineDeco);
    else if (info.delBefore > 0) builder.add(from, from, delLineDeco);
    if (i === activeLine) builder.add(from, from, activeLineDeco);
  }
  return builder.finish();
}

// diff 结果字段（供 gutter 与装饰共享）
const diffField = StateField.define<{ diff: MarkdownDiffVM; active: number }>({
  create: () => ({ diff: { lines: [], trailingDel: 0, add: 0, chg: 0, del: 0, hunks: [] }, active: -1 }),
  update(value, tr) {
    let next = value;
    for (const e of tr.effects) {
      if (e.is(setDiff)) next = { diff: e.value, active: -1 };
      else if (e.is(setActiveHunk)) next = { ...next, active: e.value };
    }
    return next;
  },
});

const diffDecoField = StateField.define<DecorationSet>({
  create: (state) => buildDiffDeco(state, state.field(diffField).diff, state.field(diffField).active),
  update(deco, tr) {
    const hasEffect = tr.effects.some((e) => e.is(setDiff) || e.is(setActiveHunk));
    if (hasEffect) {
      const f = tr.state.field(diffField);
      return buildDiffDeco(tr.state, f.diff, f.active);
    }
    return deco.map(tr.changes);
  },
  provide: (f) => EditorView.decorations.from(f),
});

class DiffGutterMarker extends GutterMarker {
  constructor(readonly kind: 'add' | 'chg' | 'del') {
    super();
  }
  override eq(other: DiffGutterMarker) {
    return other.kind === this.kind;
  }
  override toDOM() {
    const span = document.createElement('span');
    span.className = `cm-diffmark cm-diffmark--${this.kind}`;
    span.textContent = this.kind === 'add' ? '＋' : this.kind === 'chg' ? '~' : '－';
    return span;
  }
}
const addMark = new DiffGutterMarker('add');
const chgMark = new DiffGutterMarker('chg');
const delMark = new DiffGutterMarker('del');

function buildDiffGutter(state: EditorState): RangeSet<GutterMarker> {
  const { diff } = state.field(diffField);
  const builder = new RangeSetBuilder<GutterMarker>();
  const doc = state.doc;
  const n = Math.min(diff.lines.length, doc.lines);
  for (let i = 0; i < n; i += 1) {
    const info = diff.lines[i];
    const from = doc.line(i + 1).from;
    if (info.status === 'add') builder.add(from, from, addMark);
    else if (info.status === 'chg') builder.add(from, from, chgMark);
    else if (info.delBefore > 0) builder.add(from, from, delMark);
  }
  return builder.finish();
}

// ---- D3 语法配色：Lezer 标记 → --tok-* CSS class（颜色令牌在 styles.css，亮暗双主题）----
const mdHighlight = HighlightStyle.define([
  { tag: tags.heading, class: 'cm-tok-h' },
  { tag: tags.heading1, class: 'cm-tok-h' },
  { tag: tags.heading2, class: 'cm-tok-h' },
  { tag: tags.heading3, class: 'cm-tok-h' },
  { tag: tags.strong, class: 'cm-tok-strong' },
  { tag: tags.emphasis, class: 'cm-tok-em' },
  { tag: tags.monospace, class: 'cm-tok-code' },
  { tag: [tags.link, tags.url], class: 'cm-tok-link' },
  { tag: tags.quote, class: 'cm-tok-quote' },
  { tag: [tags.list, tags.processingInstruction], class: 'cm-tok-punct' },
]);

// 图表围栏（```mermaid / ```plantuml）行专门着色 + 「受控图表」标签片（--tok-fence）：
// 语法层无此语义标记，用轻量行装饰按围栏范围标注。触及围栏＝OTHER_ASSET 阻断的视觉呼应。
const fenceLineDeco = Decoration.line({ class: 'cm-tok-fence-line' });
const fenceOpenDeco = Decoration.line({ class: 'cm-tok-fence-line cm-tok-fence-open' });
function buildFenceDeco(state: EditorState): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const doc = state.doc;
  let inFence = false;
  for (let i = 1; i <= doc.lines; i += 1) {
    const line = doc.line(i);
    const opener = /^\s*```(mermaid|plantuml|puml)\b/i.test(line.text);
    const closer = /^\s*```\s*$/.test(line.text);
    if (!inFence && opener) {
      inFence = true;
      builder.add(line.from, line.from, fenceOpenDeco);
    } else if (inFence) {
      builder.add(line.from, line.from, fenceLineDeco);
      if (closer) inFence = false;
    }
  }
  return builder.finish();
}
const fenceField = StateField.define<DecorationSet>({
  create: (state) => buildFenceDeco(state),
  update(deco, tr) {
    if (tr.docChanged) return buildFenceDeco(tr.state);
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

export const CodeMirrorEditor = forwardRef<CodeMirrorEditorHandle, CodeMirrorEditorProps>(
  function CodeMirrorEditor(
    { value, baseline, disabled, wordWrap, onChange, onDiff, onScrollTopLine },
    ref,
  ) {
    const hostRef = useRef<HTMLDivElement>(null);
    const viewRef = useRef<EditorView | null>(null);
    const syncingRef = useRef(false);
    const baselineRef = useRef(baseline);
    const diffTimerRef = useRef<number | undefined>(undefined);
    // 惰性初始化：Compartment 实例只需构造一次并跨渲染稳定，用 useMemo 避免每次渲染都
    // 无谓 new 一个随即被丢弃的实例（旧 useRef(new Compartment()) 会每渲染构造后弃用）。
    const editableComp = useMemo(() => new Compartment(), []);
    const wrapComp = useMemo(() => new Compartment(), []);
    const onChangeRef = useRef(onChange);
    const onDiffRef = useRef(onDiff);
    const onScrollRef = useRef(onScrollTopLine);
    onChangeRef.current = onChange;
    onDiffRef.current = onDiff;
    onScrollRef.current = onScrollTopLine;

    // 顶部可见源码行（0 基）
    const topLine = (view: EditorView): number => {
      const block = view.lineBlockAtHeight(view.scrollDOM.scrollTop);
      return view.state.doc.lineAt(block.from).number - 1;
    };

    // 防抖重算 diff（读实时 doc 与 baselineRef），派发 setDiff，并回传给修订条
    const recomputeDiff = (immediate = false) => {
      const view = viewRef.current;
      if (!view) return;
      window.clearTimeout(diffTimerRef.current);
      const run = () => {
        const v = viewRef.current;
        if (!v) return;
        const diff = diffMarkdownLines(baselineRef.current, v.state.doc.toString());
        v.dispatch({ effects: setDiff.of(diff) });
        onDiffRef.current?.(diff);
      };
      if (immediate) run();
      else diffTimerRef.current = window.setTimeout(run, 200);
    };

    // 初始化 EditorView（一次）
    useEffect(() => {
      if (!hostRef.current) return;
      const view = new EditorView({
        parent: hostRef.current,
        state: EditorState.create({
          doc: value,
          extensions: [
            lineNumbers(),
            gutter({ class: 'cm-diffgutter', markers: (v) => buildDiffGutter(v.state) }),
            history(),
            keymap.of([...defaultKeymap, ...historyKeymap]),
            markdownLanguage,
            syntaxHighlighting(mdHighlight),
            diffField,
            diffDecoField,
            fenceField,
            editableComp.of(EditorView.editable.of(!disabled)),
            wrapComp.of(wordWrap ? EditorView.lineWrapping : []),
            EditorView.updateListener.of((update) => {
              if (update.docChanged && !syncingRef.current) {
                onChangeRef.current(update.state.doc.toString());
                recomputeDiff();
              }
              if (update.geometryChanged || update.docChanged) {
                // 滚动或几何变化：回传顶部行
                onScrollRef.current?.(topLine(update.view));
              }
            }),
            EditorView.domEventHandlers({
              scroll: (_event, view) => {
                onScrollRef.current?.(topLine(view));
              },
            }),
            EditorView.theme({ '&': { height: '100%' }, '.cm-scroller': { overflow: 'auto' } }),
          ],
        }),
      });
      viewRef.current = view;
      recomputeDiff(true);
      return () => {
        window.clearTimeout(diffTimerRef.current);
        view.destroy();
        viewRef.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // value 外部变化（刷新/放弃）→ 同步进编辑器（用户输入引起的相等则跳过，护住光标）
    useEffect(() => {
      const view = viewRef.current;
      if (!view) return;
      const current = view.state.doc.toString();
      if (current === value) return;
      syncingRef.current = true;
      view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
      syncingRef.current = false;
      recomputeDiff(true);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value]);

    // baseline 变化（保存后重置）→ 重算 diff
    useEffect(() => {
      baselineRef.current = baseline;
      recomputeDiff(true);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [baseline]);

    // disabled / wordWrap 变化 → 重配 compartment（不重建 view）
    useEffect(() => {
      viewRef.current?.dispatch({
        effects: editableComp.reconfigure(EditorView.editable.of(!disabled)),
      });
    }, [disabled]);
    useEffect(() => {
      viewRef.current?.dispatch({
        effects: wrapComp.reconfigure(wordWrap ? EditorView.lineWrapping : []),
      });
    }, [wordWrap]);

    useImperativeHandle(ref, () => ({
      scrollToLine: (line: number) => {
        const view = viewRef.current;
        if (!view) return;
        const target = Math.max(0, Math.min(line, view.state.doc.lines - 1));
        const pos = view.state.doc.line(target + 1).from;
        view.dispatch({ effects: EditorView.scrollIntoView(pos, { y: 'start' }) });
      },
      focusHunk: (line: number) => {
        const view = viewRef.current;
        if (!view) return;
        const target = Math.max(0, Math.min(line, view.state.doc.lines - 1));
        const pos = view.state.doc.line(target + 1).from;
        view.dispatch({
          effects: [EditorView.scrollIntoView(pos, { y: 'center' }), setActiveHunk.of(target)],
        });
      },
      undo: () => {
        const view = viewRef.current;
        if (view) {
          undo(view);
          view.focus();
        }
      },
      redo: () => {
        const view = viewRef.current;
        if (view) {
          redo(view);
          view.focus();
        }
      },
    }));

    return <div ref={hostRef} className="cm-host" data-testid="markdown-editor" />;
  },
);
