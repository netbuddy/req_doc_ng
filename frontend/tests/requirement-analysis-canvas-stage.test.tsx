/**
 * 区3 画布台结构用例（T20260720-analysis-scroll-siblings-fix）。
 *
 * 缺陷：区3 的滚动收敛到外层容器 .analysis-canvas-wrap 之后，容器里还住着三个按
 * 「容器不滚」写成的兄弟元素——识别中遮罩、共用标注气泡、选区操作条。它们随正文一起滚，
 * 于是遮罩滚出视野后不再拦截点击、气泡飞出可视区、操作条恒落在可视区下沿之外。
 * 口径：三者提升到不滚动的画布台 .analysis-canvas-stage，滚动容器只留画布一个子元素。
 *
 * 说明：vitest 配置 css: false 且 jsdom 不做排版计算，因此本文件只能守住 DOM 归属这一层
 * （谁挂在滚动容器里、谁挂在台面上），滚动后的实际可见性由浏览器走查兜底。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const getMaterialCanvasMock = vi.fn();
const getMaterialParseContextMock = vi.fn();
const getWorkspaceMock = vi.fn();
const submitRecognitionMock = vi.fn();
const decideElementsMock = vi.fn();

vi.mock('../src/api/analysis', () => ({
  analysisApi: {
    getMaterialCanvas: (...args: unknown[]) => getMaterialCanvasMock(...args),
    getMaterialParseContext: (...args: unknown[]) => getMaterialParseContextMock(...args),
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    submitRecognition: (...args: unknown[]) => submitRecognitionMock(...args),
    decideElements: (...args: unknown[]) => decideElementsMock(...args),
    sendDialogueStream: vi.fn(),
    getElementHistory: vi.fn(),
  },
}));
// C-9 修：组件读 res.rows，桩须回对象而非数组，否则每条用例都在一条被吞的 TypeError 上跑
vi.mock('../src/api/transcript', () => ({ fetchChatTranscript: vi.fn(() => Promise.resolve({ rows: [] })) }));
vi.mock('../src/api/runtime-status', () => ({ runtimeStatusApi: { probe: vi.fn() } }));

import { RequirementAnalysisFlow, clampOverlapPosition } from '../src/workbenches/RequirementAnalysisFlow';

const RAW = '系统应支持批量导入。导入失败需给出原因。';
const SHARED = '系统应支持批量导入';
const MATERIAL_REF = 'M-1';
const CONTEXT_REF = 'PCTX-9';

const canvas = {
  material_ref: MATERIAL_REF,
  title: '导入需求说明',
  source_note: '走查材料',
  raw_text: RAW,
  source_version: 1,
  blocks: [{ block_id: 'B1', start_offset: 0, end_offset: RAW.length, text: RAW }],
  supplements: [],
};

/** 两条知识项共用同一段原文 → 该段的 refs 长度为 2，点击才会弹共用标注气泡 */
const sharedAnchor = JSON.stringify({
  material_ref: MATERIAL_REF,
  ranges: [{ start: 0, end: SHARED.length, exact: SHARED }],
});

function element(id: string, content: string) {
  return {
    id,
    element_type: 'functional_requirement',
    content,
    source_anchor: sharedAnchor,
    confidence: 0.9,
    source_drift_tokens: [],
    process_status: 'pending_confirmation',
    model_verdict: 'processable',
    version: 1,
    superseded: false,
    origin_refs: [],
  };
}

const workspace = {
  parse_context_ref: CONTEXT_REF,
  parse_result_ref: 'PR-1',
  workspace_version: '3',
  parse_status: 'parsed',
  material_canvas: canvas,
  elements: [element('E-1', '系统应支持批量导入'), element('E-2', '批量导入为核心能力')],
  merged_existing_elements: [],
  selected_element_ref: 'E-1',
  available_actions: [],
  available_operations: [],
};

function renderFlow() {
  return render(
    <RequirementAnalysisFlow
      initialParseContextRef={CONTEXT_REF}
      materialRef={MATERIAL_REF}
      onBackToIntake={vi.fn()}
      operatorRef="U-1"
      projectId="P-1"
    />,
  );
}

function stage(container: HTMLElement): HTMLElement {
  const el = container.querySelector('.analysis-canvas-stage');
  if (!el) throw new Error('未找到区3 画布台 .analysis-canvas-stage');
  return el as HTMLElement;
}

function scrollWrap(container: HTMLElement): HTMLElement {
  const el = container.querySelector('.analysis-canvas-wrap');
  if (!el) throw new Error('未找到区3 滚动容器 .analysis-canvas-wrap');
  return el as HTMLElement;
}

beforeEach(() => {
  getMaterialCanvasMock.mockReset().mockResolvedValue(canvas);
  getMaterialParseContextMock.mockReset();
  getWorkspaceMock.mockReset().mockResolvedValue(workspace);
  submitRecognitionMock.mockReset();
  decideElementsMock.mockReset();
  // jsdom 未实现 scrollIntoView：selectElement/handleListSelect 的 rAF 回调会调它，桩成 noop
  Element.prototype.scrollIntoView = vi.fn();
});

// C-10 修：test 里 vi.spyOn(window,'getSelection') 不恢复会泄漏到后续用例（新增的拖选守卫
// 会读 getSelection，泄漏的 isCollapsed:false 会让本该开气泡的用例静默早退）。逐例恢复所有 spy。
afterEach(() => {
  vi.restoreAllMocks();
});

/** 点区2「识别知识项」并确认重识别弹窗（工作区有 2 条 → buildReidentifyGuard 要求前置确认） */
async function startReidentify() {
  fireEvent.click(screen.getByRole('button', { name: '识别知识项' }));
  fireEvent.click(await screen.findByText('仍要重新识别'));
}

describe('区3 画布台：不滚动的定位承载层', () => {
  it('滚动容器只装画布一个子元素，且自身挂在画布台下', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    const wrap = scrollWrap(container);
    expect(wrap.parentElement).toBe(stage(container));
    expect(wrap.children.length).toBe(1);
    expect(wrap.children[0].classList.contains('analysis-canvas')).toBe(true);
  });

  it('选区操作条挂在画布台上而非滚动容器内（拖选后恒在画布下沿可见）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    const article = container.querySelector('.analysis-canvas') as HTMLElement;
    const segment = article.querySelector('[data-seg-start]') as HTMLElement;
    // jsdom 不实现真实选区，按组件读取的字段构造一份等价的 Selection 替身
    vi.spyOn(window, 'getSelection').mockReturnValue({
      isCollapsed: false,
      getRangeAt: () => ({
        startContainer: segment,
        startOffset: 0,
        endContainer: segment,
        endOffset: 4,
        commonAncestorContainer: segment,
      }),
    } as unknown as Selection);

    fireEvent.mouseUp(article);

    const bar = await waitFor(() => {
      const el = container.querySelector('.analysis-selection-bar');
      if (!el) throw new Error('选区操作条未出现');
      return el as HTMLElement;
    });
    expect(bar.parentElement).toBe(stage(container));
    expect(scrollWrap(container).contains(bar)).toBe(false);
  });

  it('共用标注气泡挂在画布台上，落点按台面坐标算（无需补滚动量）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    const shared = container.querySelector('.canvas-highlight--overlap') as HTMLElement;
    expect(shared).toBeTruthy();
    // 台面矩形左上角固定为 (40, 100)，点击点 (140, 300) → 落点应为 (100, 216)：
    // 216 = 300 − 100 + 16（16 是气泡相对点击点的下移量），不含任何滚动量
    vi.spyOn(stage(container), 'getBoundingClientRect').mockReturnValue({
      left: 40,
      top: 100,
      right: 640,
      bottom: 500,
      width: 600,
      height: 400,
      x: 40,
      y: 100,
      toJSON: () => ({}),
    } as DOMRect);

    fireEvent.click(shared, { clientX: 140, clientY: 300 });

    const popover = await waitFor(() => {
      const el = container.querySelector('.analysis-overlap-popover');
      if (!el) throw new Error('共用标注气泡未出现');
      return el as HTMLElement;
    });
    expect(popover.parentElement).toBe(stage(container));
    expect(scrollWrap(container).contains(popover)).toBe(false);
    expect(popover.style.left).toBe('100px');
    expect(popover.style.top).toBe('216px');
  });

  it('图例给出「迁移点」条目，且样例字不被 aria-hidden 隐去（R7 读屏口径）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    expect(screen.getByText('迁移点')).toBeTruthy();
    const relocated = container.querySelector('.canvas-legend-relocated');
    expect(relocated).toBeTruthy();
    // R7：与「原文外补充」对齐，样例字须留在无障碍树里，否则读屏说明句丢主语
    expect(relocated?.getAttribute('aria-hidden')).toBeNull();

    // K11：同排同构的「共用」样例字是这条图例的主语，也不得被 aria-hidden 隐去（与「迁移点」对称）
    const overlapSwatch = container.querySelector('.canvas-legend-overlap-swatch');
    expect(overlapSwatch?.textContent).toBe('共用');
    expect(overlapSwatch?.getAttribute('aria-hidden')).toBeNull();
  });
});

describe('区3 画布交互修复（T20260721-analysis-canvas-interaction-fix）', () => {
  it('R4：选区归属判定基准＝画布台——跨越操作条/气泡的选区（cac 落在台面）仍成选区', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    const article = container.querySelector('.analysis-canvas') as HTMLElement;
    const stageEl = stage(container);
    const segment = article.querySelector('[data-seg-start]') as HTMLElement;
    // 起止都在正文段内、但拖动跨过了操作条/气泡，浏览器把 commonAncestorContainer 归一到画布台。
    // 基准＝canvasStageRef 时 stage.contains(stage)=true → 正常成选区；若退回 canvasRef(wrap) 基准，
    // wrap.contains(stage)=false → 逃逸清空，选区起不来（本用例即钉住基准是 stage 不是 wrap）。
    vi.spyOn(window, 'getSelection').mockReturnValue({
      isCollapsed: false,
      getRangeAt: () => ({
        startContainer: segment,
        startOffset: 0,
        endContainer: segment,
        endOffset: 4,
        commonAncestorContainer: stageEl,
      }),
    } as unknown as Selection);

    fireEvent.mouseUp(article);

    await waitFor(() => expect(container.querySelector('.analysis-selection-bar')).toBeTruthy());
  });

  it('R1/P3：识别进行中点共用标注段不弹气泡（守卫早退，含键盘路径入口）', async () => {
    // 让识别请求悬着不结算，phase 停在 recognizing、recognitionOwned 恒 true（遮罩在、守卫生效）
    let release: () => void = () => {};
    submitRecognitionMock.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ status: 'accepted', parse_context_ref: CONTEXT_REF, agent_run_ref: null });
      }),
    );

    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.canvas-highlight--overlap')).toBeTruthy());

    await startReidentify();
    await waitFor(() => expect(container.querySelector('.analysis-canvas-mask')).toBeTruthy());

    const shared = container.querySelector('.canvas-highlight--overlap') as HTMLElement;
    fireEvent.click(shared, { clientX: 200, clientY: 200 });
    // 守卫早退：识别中不开共用标注气泡（去掉 handleSegmentClick 的 recognitionLocked 早退即转红）
    expect(container.querySelector('.analysis-overlap-popover')).toBeNull();

    release();
  });

  it('R1：识别进行中 mouseUp 不生成选区（守卫早退）', async () => {
    let release: () => void = () => {};
    submitRecognitionMock.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ status: 'accepted', parse_context_ref: CONTEXT_REF, agent_run_ref: null });
      }),
    );

    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    await startReidentify();
    await waitFor(() => expect(container.querySelector('.analysis-canvas-mask')).toBeTruthy());

    const article = container.querySelector('.analysis-canvas') as HTMLElement;
    const segment = article.querySelector('[data-seg-start]') as HTMLElement;
    vi.spyOn(window, 'getSelection').mockReturnValue({
      isCollapsed: false,
      getRangeAt: () => ({
        startContainer: segment,
        startOffset: 0,
        endContainer: segment,
        endOffset: 4,
        commonAncestorContainer: segment,
      }),
    } as unknown as Selection);
    fireEvent.mouseUp(article);

    expect(container.querySelector('.analysis-selection-bar')).toBeNull();
    release();
  });

  it('A5/K8：重新识别真替换工作区（新识别上下文＋全新要素 id）后气泡被清（旧引用一条都解析不到）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.canvas-highlight--overlap')).toBeTruthy());

    const shared = container.querySelector('.canvas-highlight--overlap') as HTMLElement;
    fireEvent.click(shared, { clientX: 140, clientY: 300 });
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeTruthy());

    // 真替换：重新识别产出全新 parse context 与全新要素 id → 气泡 refs（E-1/E-2）在新工作区解析不到。
    // K8 把无条件清除收窄为「上下文换了 || refs 全解析不到」后，此场景仍清（A5 口径不变）。
    submitRecognitionMock.mockResolvedValue({ status: 'accepted', parse_context_ref: 'PCTX-NEW', agent_run_ref: null });
    getWorkspaceMock.mockResolvedValue({
      ...workspace,
      parse_context_ref: 'PCTX-NEW',
      elements: [element('E-9', '系统应支持批量导入'), element('E-10', '批量导入为核心能力')],
      selected_element_ref: 'E-9',
    });
    await startReidentify();

    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeNull());
  });

  it('K8：增量刷新（同上下文、要素 id 未换）不清掉正在读的气泡——后台结算不打断', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.canvas-highlight--overlap')).toBeTruthy());

    // decide 需要一个裁决目标：先点气泡里的 E-1 按钮选中它（selectElement 会关掉气泡）
    const shared = container.querySelector('.canvas-highlight--overlap') as HTMLElement;
    fireEvent.click(shared, { clientX: 140, clientY: 300 });
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeTruthy());
    fireEvent.click(container.querySelector('.analysis-overlap-popover button') as HTMLElement);
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeNull());

    // 再点共用段把气泡开回来（E-1 此刻已选中）
    fireEvent.click(shared, { clientX: 140, clientY: 300 });
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeTruthy());

    // 增量刷新：decide 回填同上下文、E-1 仍在的工作区 → 气泡 refs 里 E-1 仍解析得到 → 不清
    decideElementsMock.mockResolvedValue({
      ...workspace,
      elements: [element('E-1', '系统应支持批量导入')],
      selected_element_ref: 'E-1',
    });
    fireEvent.click(container.querySelector('.az5-qp--ok') as HTMLElement);
    await screen.findByText(/已确认 1 条/); // 结算落定（applyWorkspace 已跑）

    // K8：把 applyWorkspace 的清除改回无条件 setOverlap(null) 即转红
    expect(container.querySelector('.analysis-overlap-popover')).toBeTruthy();
  });

  it('K12：结算换掉工作区后 mergeChecked 剔除已不存在的要素 id（组稿按钮由可用转禁用）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.canvas-highlight--overlap')).toBeTruthy());

    // 选中 E-1
    const shared = container.querySelector('.canvas-highlight--overlap') as HTMLElement;
    fireEvent.click(shared, { clientX: 140, clientY: 300 });
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeTruthy());
    fireEvent.click(container.querySelector('.analysis-overlap-popover button') as HTMLElement);
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeNull());

    // 打开合并浮层、勾选候选 E-2 → mergeChecked=[E-2]，「组稿命令文本」转可用
    fireEvent.click(screen.getByRole('button', { name: '合并' }));
    const dialog = await screen.findByRole('dialog', { name: '选择参与合并的要素' });
    fireEvent.click(dialog.querySelector('input[type="checkbox"]') as HTMLElement);
    const composeBtn = () => within(dialog).getByRole('button', { name: '组稿命令文本' });
    await waitFor(() => expect(composeBtn()).toBeEnabled());

    // 增量结算：E-1 仍在（目标不变、浮层不关）、E-2 已不在 → mergeChecked 剔除 E-2
    decideElementsMock.mockResolvedValue({
      ...workspace,
      elements: [element('E-1', '系统应支持批量导入')],
      selected_element_ref: 'E-1',
    });
    fireEvent.click(container.querySelector('.az5-qp--ok') as HTMLElement);
    await screen.findByText(/已确认 1 条/); // 结算落定（applyWorkspace 已跑）

    // 注掉 applyWorkspace 里的 setMergeChecked 裁剪即转红（残留 E-2 → 按钮仍可用）
    expect(composeBtn()).toBeDisabled();
  });

  it('K1：鼠标位移超阈值＝段内拖选，不弹共用标注气泡；位移在阈内则正常开气泡', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.canvas-highlight--overlap')).toBeTruthy());
    const article = container.querySelector('.analysis-canvas') as HTMLElement;
    const shared = container.querySelector('.canvas-highlight--overlap') as HTMLElement;

    // 按下(100,100)→点击(100,140)：位移 40px > 4px 阈值 → 判为拖选，onClick 早退不开气泡
    fireEvent.mouseDown(article, { clientX: 100, clientY: 100 });
    fireEvent.click(shared, { clientX: 100, clientY: 140 });
    expect(container.querySelector('.analysis-overlap-popover')).toBeNull();

    // 对照：按下(100,100)→点击(100,102) 位移 2px ≤ 阈值 → 判为点选，正常开气泡（防误吞）
    fireEvent.mouseDown(article, { clientX: 100, clientY: 100 });
    fireEvent.click(shared, { clientX: 100, clientY: 102 });
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeTruthy());
  });

  it('K1：键盘 Enter 激活共用标注段（文档存在未折叠选区也不失效——键盘入口不设选区门）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.canvas-highlight--overlap')).toBeTruthy());
    const shared = container.querySelector('.canvas-highlight--overlap') as HTMLElement;

    // D2 触发条件：文档任意位置存在未折叠选区（旧实现下键盘激活被同一守卫吞掉且不自愈）
    vi.spyOn(window, 'getSelection').mockReturnValue({ isCollapsed: false } as unknown as Selection);
    fireEvent.keyDown(shared, { key: 'Enter' });

    // 键盘不读选区、直接开共用标注气泡（给键盘路径加选区门即转红）
    await waitFor(() => expect(container.querySelector('.analysis-overlap-popover')).toBeTruthy());
  });
});

describe('clampOverlapPosition：气泡越界钳制算式（R2/R3）', () => {
  // 一份「有充裕空间」的基准几何：气泡 280×160，台面 900×600，无操作条，页面下沿很远
  const roomy = {
    popW: 280,
    popH: 160,
    stageW: 900,
    stageH: 600,
    barH: 0,
    pageBottomInStage: 1000,
    cursorY: 284,
  };

  it('空间充裕时落点原样透传（不钳）', () => {
    // maxBottom=1000（barH=0 取 pageBottom），气泡放得下 → 落点不动；maxHeight=从 top 到 maxBottom 的余量
    expect(clampOverlapPosition({ x: 120, y: 300 }, roomy)).toEqual({ left: 120, top: 300, maxHeight: 700 });
  });

  it('横向：落点靠右时钳进台面（left ≤ stageW − popW），不越进区5', () => {
    // stageW−popW=620；raw.x=800 越界 → 钳到 620
    expect(clampOverlapPosition({ x: 800, y: 300 }, roomy).left).toBe(620);
  });

  it('横向：台面比气泡还窄时贴左（left=0，不出负数）', () => {
    expect(clampOverlapPosition({ x: 50, y: 100 }, { ...roomy, stageW: 200 }).left).toBe(0);
  });

  it('纵向：操作条存在且下方放不下时翻到光标上方（气泡下沿贴光标上方 8px）', () => {
    // 操作条占底部 40 → 可用下界＝stageH−barH=560；raw.y=520，520+160=680>560 放不下
    // → 翻到光标上方：cursorY(504) − 8 − popH(160) = 336
    const geom = { ...roomy, barH: 40, cursorY: 504 };
    expect(clampOverlapPosition({ x: 100, y: 520 }, geom).top).toBe(336);
  });

  it('纵向：无操作条时下界＝页面下沿，气泡可越出区3 浮到区4（不强行关回）', () => {
    // pageBottom=650，raw.y=520，520+160=680>650 → 翻到光标上方 cursorY(504)−8−160=336
    const geom = { ...roomy, pageBottomInStage: 650, cursorY: 504 };
    expect(clampOverlapPosition({ x: 100, y: 520 }, geom).top).toBe(336);
    // 但若页面够高（放得下），落点原样保留、允许盖到区4
    expect(clampOverlapPosition({ x: 100, y: 520 }, { ...roomy, pageBottomInStage: 900 }).top).toBe(520);
  });

  it('翻到上方后仍越顶时钳到 0（不跑出台面顶部）', () => {
    // 气泡很高：下方放不下触发翻转，翻到上方又算出负数（184−8−400=−224）→ 钳到 0
    const geom = { ...roomy, popH: 400, barH: 40, cursorY: 184 };
    expect(clampOverlapPosition({ x: 100, y: 200 }, geom).top).toBe(0);
  });

  // K2 变异对①（钳制下界）：气泡放得下、但翻转把它顶到了操作条下界之外时，须复检下界钳回来。
  // 注掉 clampOverlapPosition 里的下界钳制（top=Math.min(top, maxBottom−popH)），本例即转红。
  it('K2：翻转后仍越过下界时钳回下界（气泡下沿 ≤ maxBottom，不压操作条）', () => {
    // maxBottom=stageH−barH=560；popH=200 放得下。光标落在下界之外（cursorY=800）→ 翻转算出
    // top=800−8−200=592，未复检下界会让气泡下沿 592+200=792 越过 560、压住操作条。
    const geom = { ...roomy, popH: 200, barH: 40, cursorY: 800 };
    const { top, maxHeight } = clampOverlapPosition({ x: 100, y: 500 }, geom);
    const maxBottom = 560;
    expect(top).toBe(maxBottom - 200); // 360：钳回下界，而非 592
    expect(top + 200).toBeLessThanOrEqual(maxBottom); // 气泡下沿 ≤ maxBottom（不钉具体 top）
    expect(maxHeight).toBe(200);
  });

  it('K2：气泡比可用高度还高（popH>maxBottom）时钳顶＋限高内滚（top=0、maxHeight=maxBottom）', () => {
    // maxBottom=stageH−barH=160；popH=300 放哪都超界（数学无解）。此时应钳到顶（top=0）并把可用高度
    // 作为 maxHeight 交给 CSS overflow-y 内滚，而非退化成常量恒返回某个把气泡推出界的 top。
    const geom = { ...roomy, stageH: 200, popH: 300, barH: 40, cursorY: 400 };
    const { top, maxHeight } = clampOverlapPosition({ x: 100, y: 50 }, geom);
    expect(top).toBe(0);
    expect(maxHeight).toBe(160); // = maxBottom，气泡限高内滚不越界
  });
});
