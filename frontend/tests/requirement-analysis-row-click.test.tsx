/**
 * 区1 整行可点 ＋ 区3「当前选区」按钮（T20260724-analysis-row-click-and-selection-append）。
 *
 * 缺陷背景：区1 的点击目标此前只有行内正文按钮 .az1-row__txt。该按钮是行里唯一可压缩的
 * flex 项（徽标全是 flex:none），徽标齐全的行——前提假设行最典型——把它压到 0 宽，
 * 于是"看得见一行、点不着一行"。口径：命中面扩到整行，role=option 与键盘随迁到行上。
 *
 * 说明：vitest 配置 css: false 且 jsdom 不做排版计算，所以宽度归零本身在这里测不出来
 * （由浏览器走查证据兜底）；本文件守的是命中面与语义这一层——谁是点击目标、勾选框是否
 * 被整行点击吞掉、键盘路径与 aria-selected 是否还在。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getMaterialCanvasMock = vi.fn();
const getMaterialParseContextMock = vi.fn();
const getWorkspaceMock = vi.fn();
const submitRecognitionMock = vi.fn();
const sendDialogueStreamMock = vi.fn();
const triageElementsMock = vi.fn();

vi.mock('../src/api/analysis', () => ({
  analysisApi: {
    getMaterialCanvas: (...args: unknown[]) => getMaterialCanvasMock(...args),
    getMaterialParseContext: (...args: unknown[]) => getMaterialParseContextMock(...args),
    getWorkspace: (...args: unknown[]) => getWorkspaceMock(...args),
    submitRecognition: (...args: unknown[]) => submitRecognitionMock(...args),
    decideElements: vi.fn(),
    sendDialogueStream: (...args: unknown[]) => sendDialogueStreamMock(...args),
    triageElements: (...args: unknown[]) => triageElementsMock(...args),
    getElementHistory: vi.fn(),
  },
}));
vi.mock('../src/api/transcript', () => ({ fetchChatTranscript: vi.fn(() => Promise.resolve({ rows: [] })) }));
vi.mock('../src/api/runtime-status', () => ({ runtimeStatusApi: { probe: vi.fn() } }));

import { RequirementAnalysisFlow } from '../src/workbenches/RequirementAnalysisFlow';

const RAW = '系统应支持批量导入。导入失败需给出原因。假设上游数据准确。';
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

function anchorFor(text: string): string {
  const start = RAW.indexOf(text);
  return JSON.stringify({ material_ref: MATERIAL_REF, ranges: [{ start, end: start + text.length, exact: text }] });
}

/** 前提假设行＝徽标最齐的那一行：来源段数＋模型裁定＋状态＋置信度同现（缺陷首发现场） */
const assumption = {
  id: 'E-ASM',
  element_type: 'assumption',
  content: '假设上游数据准确',
  source_anchor: anchorFor('假设上游数据准确'),
  confidence: 0.74,
  source_drift_tokens: [],
  process_status: 'pending_confirmation',
  model_verdict: 'processable',
  version: 1,
  superseded: false,
  origin_refs: [],
};

const functional = {
  id: 'E-FR',
  element_type: 'functional_requirement',
  content: '系统应支持批量导入',
  source_anchor: anchorFor('系统应支持批量导入'),
  confidence: 0.9,
  source_drift_tokens: [],
  process_status: 'pending_confirmation',
  model_verdict: 'processable',
  version: 1,
  superseded: false,
  origin_refs: [],
};

const workspace = {
  parse_context_ref: CONTEXT_REF,
  parse_result_ref: 'PR-1',
  workspace_version: '3',
  parse_status: 'parsed',
  material_canvas: canvas,
  elements: [functional, assumption],
  merged_existing_elements: [],
  selected_element_ref: 'E-FR',
  available_actions: [],
  available_operations: [],
};

/** 建议剔除候选＝模型判为不承载需求信息且未撤回的那一条：区1 底部独立分组，与普通行并列渲染 */
const noise = {
  id: 'E-NOISE',
  element_type: 'functional_requirement',
  content: '下期再讨论导出模板',
  source_anchor: anchorFor('导入失败需给出原因'),
  confidence: 0.41,
  source_drift_tokens: [],
  process_status: 'pending_confirmation',
  model_verdict: 'suspected_noise',
  noise_triage: 'suggested',
  version: 1,
  superseded: false,
  origin_refs: [],
};

const triageWorkspace = { ...workspace, elements: [functional, assumption, noise] };

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

async function rows(container: HTMLElement): Promise<HTMLElement[]> {
  return waitFor(() => {
    const found = [...container.querySelectorAll('.az1-row')] as HTMLElement[];
    if (found.length < 2) throw new Error('区1 行未渲染');
    return found;
  });
}

function rowFor(list: HTMLElement[], elementId: string): HTMLElement {
  const row = list.find((r) => r.dataset.elementId === elementId);
  if (!row) throw new Error(`未找到行 ${elementId}`);
  return row;
}

/** 同上，但每次从容器现查：重渲染后的行是新节点，断言不能拿旧引用 */
function row(container: HTMLElement, elementId: string): HTMLElement {
  return rowFor([...container.querySelectorAll('.az1-row')] as HTMLElement[], elementId);
}

/** 区4 详情区的当前目标＝选中态的可观察出口（区1 的 aria-selected 之外的第二信源） */
function detailTitle(container: HTMLElement): string {
  return (container.querySelector('.analysis-zone--detail') as HTMLElement)?.textContent ?? '';
}

beforeEach(() => {
  getMaterialCanvasMock.mockReset().mockResolvedValue(canvas);
  getMaterialParseContextMock.mockReset();
  getWorkspaceMock.mockReset().mockResolvedValue(workspace);
  submitRecognitionMock.mockReset();
  // 受理但不改工作区的回执：本文件断言的是发出去的载荷与命中面，不是命令结算
  sendDialogueStreamMock.mockReset().mockResolvedValue({ outcome: 'clarify', message: '收到' });
  triageElementsMock.mockReset().mockResolvedValue(triageWorkspace);
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('A1 区1 整行可点', () => {
  it('行本身即点击目标：行承担 option 语义，正文不再是自己的按钮', async () => {
    const { container } = renderFlow();
    const list = await rows(container);

    expect(container.querySelector('button.az1-row__txt')).toBeNull();
    list.forEach((el) => {
      expect(el.getAttribute('role')).toBe('option');
      expect(el.getAttribute('tabindex')).toBe('0');
      expect(el.hasAttribute('aria-selected')).toBe(true);
    });
    // option 仍住在 listbox 里（把语义从正文按钮搬到行上没有搬出容器）
    expect(list[0].closest('[role="listbox"]')).toBeTruthy();
  });

  it('点前提假设行的行本身（非正文）即选中该项，aria-selected 随之翻转', async () => {
    const { container } = renderFlow();
    const list = await rows(container);
    const asmRow = rowFor(list, 'E-ASM');

    expect(asmRow.getAttribute('aria-selected')).toBe('false');
    fireEvent.click(asmRow);

    await waitFor(() => expect(row(container, 'E-ASM').getAttribute('aria-selected')).toBe('true'));
    expect(row(container, 'E-FR').getAttribute('aria-selected')).toBe('false');
    expect(detailTitle(container)).toContain('假设上游数据准确');
  });

  it('点行内徽标区域（状态标签）同样选中——命中面是整行，不只是正文那一段', async () => {
    const { container } = renderFlow();
    const list = await rows(container);
    const pill = rowFor(list, 'E-ASM').querySelector('.status-pill') as HTMLElement;
    expect(pill).toBeTruthy();

    fireEvent.click(pill);

    await waitFor(() => expect(row(container, 'E-ASM').getAttribute('aria-selected')).toBe('true'));
  });

  it('点勾选框只改勾选集，不把选中目标换成这一条（两件事的边界）', async () => {
    const { container } = renderFlow();
    const list = await rows(container);
    const box = rowFor(list, 'E-ASM').querySelector('input[type="checkbox"]') as HTMLInputElement;
    expect(box).toBeTruthy();

    fireEvent.click(box);

    await waitFor(() => expect(box.checked).toBe(true));
    // 选中目标仍是初始那条：勾选没有顺带换目标
    expect(row(container, 'E-ASM').getAttribute('aria-selected')).toBe('false');
    expect(row(container, 'E-FR').getAttribute('aria-selected')).toBe('true');
  });
});

describe('A2 键盘路径与读屏语义不丢', () => {
  it('回车与空格落在行上都能选中，空格不放行默认滚动', async () => {
    const { container } = renderFlow();
    let list = await rows(container);

    fireEvent.keyDown(rowFor(list, 'E-ASM'), { key: 'Enter' });
    await waitFor(() => expect(row(container, 'E-ASM').getAttribute('aria-selected')).toBe('true'));

    list = await rows(container);
    fireEvent.keyDown(rowFor(list, 'E-FR'), { key: ' ' });
    await waitFor(() => expect(row(container, 'E-FR').getAttribute('aria-selected')).toBe('true'));

    const spaceOnRow = fireEvent.keyDown(row(container, 'E-ASM'), { key: ' ' });
    expect(spaceOnRow).toBe(false); // preventDefault 生效
  });

  it('行内控件的按键不被行截走：勾选框上的空格不触发选中', async () => {
    const { container } = renderFlow();
    const list = await rows(container);
    const box = rowFor(list, 'E-ASM').querySelector('input[type="checkbox"]') as HTMLInputElement;

    fireEvent.keyDown(box, { key: ' ' });

    expect(row(container, 'E-ASM').getAttribute('aria-selected')).toBe('false');
  });
});

/** 在区3 造一份选区：jsdom 无真实 Selection，按组件读取的字段构造替身后触发 mouseUp */
async function dragSelect(container: HTMLElement, from: number, to: number) {
  const article = container.querySelector('.analysis-canvas') as HTMLElement;
  const segment = article.querySelector('[data-seg-start]') as HTMLElement;
  vi.spyOn(window, 'getSelection').mockReturnValue({
    isCollapsed: false,
    getRangeAt: () => ({
      startContainer: segment,
      startOffset: from,
      endContainer: segment,
      endOffset: to,
      commonAncestorContainer: segment,
    }),
  } as unknown as Selection);
  fireEvent.mouseUp(article);
  return waitFor(() => {
    const bar = container.querySelector('.analysis-selection-bar');
    if (!bar) throw new Error('选区操作条未出现');
    return bar as HTMLElement;
  });
}

function selectionButton(container: HTMLElement): HTMLButtonElement | null {
  const bar = container.querySelector('.analysis-selection-bar');
  if (!bar) return null;
  return [...bar.querySelectorAll('button')].find((b) => b.textContent?.trim() === '当前选区') ?? null;
}

function composer(container: HTMLElement): HTMLTextAreaElement {
  return container.querySelector('.analysis-zone--operations textarea') as HTMLTextAreaElement;
}

/** 区5 快捷命令药丸：按显示文字取，文字带后缀的（如「改范围 · 用当前选区」）按前缀匹配 */
function commandPill(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('.az5-qp')].find((b) => b.textContent?.startsWith(label));
  if (!found) throw new Error(`未找到药丸 ${label}`);
  return found as HTMLButtonElement;
}

describe('A5 区3「当前选区」按钮态与联动', () => {
  it('无选区时按钮不存在（随操作条一起出现与消失）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    expect(container.querySelector('.analysis-selection-bar')).toBeNull();
    expect(selectionButton(container)).toBeNull();

    await dragSelect(container, 0, 4);
    expect(selectionButton(container)).toBeTruthy();

    // 清除选区 → 操作条与按钮一并消失
    const clear = [...container.querySelectorAll('.analysis-selection-bar button')].find(
      (b) => b.textContent?.trim() === '清除选区',
    ) as HTMLElement;
    fireEvent.click(clear);
    await waitFor(() => expect(container.querySelector('.analysis-selection-bar')).toBeNull());
    expect(selectionButton(container)).toBeNull();
  });

  it('点按钮把选区描述写进区5 输入框；同一选区再点只更新那一段（幂等）', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());
    await dragSelect(container, 0, 4);

    fireEvent.click(selectionButton(container) as HTMLElement);
    await waitFor(() => expect(composer(container).value).toContain('当前选区（0–4）'));
    const once = composer(container).value;

    fireEvent.click(selectionButton(container) as HTMLElement);
    await waitFor(() => expect(composer(container).value).toBe(once));
    expect(composer(container).value.match(/当前选区/g)).toHaveLength(1);
  });

  it('先点区5「改范围」（无选区版引导语）再点按钮：引导语被选区描述替换，不并存', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());

    fireEvent.click(commandPill(container, '改范围'));
    await waitFor(() => expect(composer(container).value).toContain('先在区3 拖选'));

    await dragSelect(container, 0, 4);
    fireEvent.click(selectionButton(container) as HTMLElement);

    await waitFor(() => expect(composer(container).value).toContain('当前选区（0–4）'));
    expect(composer(container).value).not.toContain('先在区3 拖选');
    expect(composer(container).value.startsWith('/改范围 ')).toBe(true);
  });

  it('识别锁定期间按钮禁用（R1 口径，与「清除选区」同条件）', async () => {
    // 送检挂起不回执 → phase 停在 recognizing、本页持有识别（recognitionLocked 成立）
    submitRecognitionMock.mockImplementation(() => new Promise(() => {}));
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());
    // 先造选区：R1 禁的是识别期间新生成选区，已有选区仍在条上
    await dragSelect(container, 0, 4);
    expect((selectionButton(container) as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: '识别知识项' }));
    fireEvent.click(await screen.findByText('仍要重新识别'));

    await waitFor(() => expect((selectionButton(container) as HTMLButtonElement).disabled).toBe(true));
  });
});

describe('A7 「当前选区」按钮的正文形态门（冷审查裁定 C1／C2／C4／C5 的共用入口）', () => {
  /**
   * 选区说明是给人读的自然语言，而后端命令解释器按参数语法切正文。正文以别的命令词开头时，
   * 说明会被整段读进该命令的参数——`/改表达` 与 `/改类型` 更是就地修订、直写入库无确认步骤。
   * 用户拍板的口径：按钮只对「改范围」与自由正文生效，其余命令下禁用并说明原因。
   */
  it('正文以别的命令词开头时按钮禁用，且悬浮说明讲清为什么', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());
    await dragSelect(container, 0, 4);
    expect(selectionButton(container)?.disabled).toBe(false);

    for (const label of ['改表达', '拆分', '新增遗漏', '勘误', '补入']) {
      fireEvent.click(commandPill(container, label));
      await waitFor(() => expect(composer(container).value.startsWith('/')).toBe(true));
      expect(selectionButton(container)?.disabled).toBe(true);
    }

    // 改类型与合并的正文经参数弹层组稿，这里直接置入其预填形态，判据同上
    for (const text of ['/改类型 外部系统', '/合并 与「系统要发通知」合并，合并后表达由 AI 起草。']) {
      fireEvent.change(composer(container), { target: { value: text } });
      await waitFor(() => expect(selectionButton(container)?.disabled).toBe(true));
    }

    const hint = selectionButton(container)?.getAttribute('title') ?? '';
    expect(hint).toContain('改范围');
    expect(hint).toContain('当成命令的内容');
  });

  it('「改范围」预填下可用，且拼装出的正文只有一段选区说明', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());
    // 先造选区再点药丸 → 预填即带选区说明；换一段选区后点按钮应就地更新那一段
    await dragSelect(container, 0, 4);
    fireEvent.click(commandPill(container, '改范围'));
    await waitFor(() => expect(composer(container).value).toContain('当前选区（0–4）'));
    expect(selectionButton(container)?.disabled).toBe(false);

    await dragSelect(container, 6, 9);
    fireEvent.click(selectionButton(container) as HTMLElement);
    await waitFor(() => expect(composer(container).value).toContain('当前选区（6–9）'));
    expect(composer(container).value.startsWith('/改范围 把')).toBe(true);
    expect(composer(container).value.match(/当前选区/g)).toHaveLength(1);
  });

  it('自由正文（不以斜杠命令开头）下可用，说明以逗号衔接追加到末尾', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());
    await dragSelect(container, 0, 4);

    fireEvent.change(composer(container), { target: { value: '这条位置标错了' } });
    await waitFor(() => expect(selectionButton(container)?.disabled).toBe(false));

    fireEvent.click(selectionButton(container) as HTMLElement);
    await waitFor(() =>
      expect(composer(container).value).toBe(
        `这条位置标错了，来源改到区3 当前选区（0–4）：「${RAW.slice(0, 4)}」`,
      ),
    );
  });
});

describe('A9 区3 两个写输入框的按钮与输入框共用同一把锁', () => {
  it('批量分诊模式下三者一并禁用：不出现「按钮可用、要写入的输入框已禁用」', async () => {
    const { container } = renderFlow();
    const list = await rows(container);
    // 勾选 ≥2 条 → 批量分诊模式，区5 只剩确认/拒绝有效
    for (const id of ['E-FR', 'E-ASM']) {
      fireEvent.click(rowFor(list, id).querySelector('input[type="checkbox"]') as HTMLInputElement);
    }
    await waitFor(() => expect(composer(container).disabled).toBe(true));

    await dragSelect(container, 0, 4);
    expect(selectionButton(container)?.disabled).toBe(true);
    const addMissing = [...container.querySelectorAll('.analysis-selection-bar button')].find((b) =>
      b.textContent?.includes('新增为知识项'),
    ) as HTMLButtonElement;
    expect(addMissing.disabled).toBe(true);
  });
});

describe('A3 发送载荷：区3 选区随请求走既有通道', () => {
  it('拖选→点「当前选区」→发送：后端收到的 selected_text_ranges 与该选区相符', async () => {
    const { container } = renderFlow();
    await waitFor(() => expect(container.querySelector('.analysis-canvas')).toBeTruthy());
    await dragSelect(container, 0, 4);

    fireEvent.click(selectionButton(container) as HTMLElement);
    await waitFor(() => expect(composer(container).value).toContain('当前选区（0–4）'));

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(sendDialogueStreamMock).toHaveBeenCalledTimes(1));
    const payload = sendDialogueStreamMock.mock.calls[0][2] as Record<string, unknown>;
    // 写进输入框的只是给人读的文字；选区数据本身仍走 selected_text_ranges
    expect(payload.selected_text_ranges).toEqual([
      { start: 0, end: 4, exact: RAW.slice(0, 4), prefix: '', suffix: '' },
    ]);
    expect(payload.message).toContain('当前选区（0–4）');
  });
});

describe('A8 建议剔除候选行：整行命中面与撤回按钮的边界', () => {
  async function renderWithTriage() {
    getWorkspaceMock.mockResolvedValue(triageWorkspace);
    const view = renderFlow();
    await waitFor(() => expect(view.container.querySelector('.az1-triage__head')).toBeTruthy());
    // 候选区默认折叠，先展开
    fireEvent.click(view.container.querySelector('.az1-triage__head') as HTMLElement);
    await waitFor(() => expect(view.container.querySelector('.az1-row--triage')).toBeTruthy());
    return view;
  }

  it('候选行整行可点：点行上的非交互区域即选中该项', async () => {
    const { container } = await renderWithTriage();
    const triageRow = row(container, 'E-NOISE');
    expect(triageRow.classList.contains('az1-row--triage')).toBe(true);
    expect(triageRow.getAttribute('role')).toBe('option');
    expect(triageRow.getAttribute('aria-selected')).toBe('false');

    fireEvent.click(triageRow);

    await waitFor(() => expect(row(container, 'E-NOISE').getAttribute('aria-selected')).toBe('true'));
    expect(detailTitle(container)).toContain('下期再讨论导出模板');
  });

  it('点「撤回到正常列表」只做处置，不把选中目标换到这一条', async () => {
    const { container } = await renderWithTriage();
    expect(row(container, 'E-FR').getAttribute('aria-selected')).toBe('true');
    const restore = [...container.querySelectorAll('.az1-triage__act button')].find((b) =>
      b.textContent?.includes('撤回到正常列表'),
    ) as HTMLElement;

    fireEvent.click(restore);

    await waitFor(() => expect(triageElementsMock).toHaveBeenCalledTimes(1));
    expect(triageElementsMock.mock.calls[0][2]).toMatchObject({ element_refs: ['E-NOISE'], action: 'restore' });
    // 选中目标仍是初始那条：处置动作没有顺带换目标
    await waitFor(() => expect(row(container, 'E-FR').getAttribute('aria-selected')).toBe('true'));
    expect(row(container, 'E-NOISE').getAttribute('aria-selected')).toBe('false');
  });
});
