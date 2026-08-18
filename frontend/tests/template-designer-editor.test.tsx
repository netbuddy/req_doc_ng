import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const createDraftMock = vi.fn();
const updateDraftMock = vi.fn();
vi.mock('../src/api/templates', () => ({
  templatesApi: {
    validate: vi.fn(),
    register: vi.fn(),
    getDetail: vi.fn(),
    list: vi.fn(),
    setStatus: vi.fn(),
    listDrafts: vi.fn(),
    createDraft: (...args: unknown[]) => createDraftMock(...args),
    updateDraft: (...args: unknown[]) => updateDraftMock(...args),
    deleteDraft: vi.fn(),
    previewDocxUrl: (r: string) => `/api/templates/${r}/preview-docx`,
  },
}));

import { TemplateDesignerEditor } from '../src/workbenches/TemplateDesignerEditor';

function renderEditor() {
  const onBack = vi.fn();
  const onRegistered = vi.fn();
  render(<TemplateDesignerEditor operatorRef="U1" onBack={onBack} onRegistered={onRegistered} />);
  return { onBack, onRegistered };
}

// 选中树中第一个章节节点（antd Tree 点击 content-wrapper 触发 onSelect）。
function selectFirstTreeNode() {
  const wrapper = document.querySelector('.ant-tree-node-content-wrapper');
  if (!wrapper) throw new Error('tree node not found');
  fireEvent.click(wrapper);
}

describe('TemplateDesignerEditor 交互冒烟（03 §2/§3）', () => {
  it('整页三栏 + 动作条 + 初始根节点态', () => {
    renderEditor();
    expect(screen.getByTestId('designer-back')).toBeInTheDocument();
    expect(screen.getByTestId('designer-validate')).toBeInTheDocument();
    expect(screen.getByTestId('designer-register')).toBeDisabled(); // 未送检不可登记
    expect(screen.getByTestId('designer-root-form')).toBeInTheDocument(); // 默认选根节点
    expect(screen.getByTestId('designer-preview')).toBeInTheDocument();
  });

  it('添加顶层章节 → 预览增长', () => {
    renderEditor();
    const before = within(screen.getByTestId('designer-preview')).getAllByText(/^\d/).length;
    fireEvent.click(screen.getByTestId('designer-add-top'));
    const after = within(screen.getByTestId('designer-preview')).getAllByText(/^\d/).length;
    expect(after).toBe(before + 1);
  });

  it('选中章节 → 两个文本勾选按组合切三态说明；「逐条目成节」按装配条件灰置', async () => {
    renderEditor();
    // 选中树中的「引言」节点（初始树含一节，已勾「模板默认文本」）
    selectFirstTreeNode();
    await waitFor(() => expect(screen.getByTestId('designer-section-form')).toBeInTheDocument());

    // 初始无需求条目装配 → 逐条目成节灰置
    expect(screen.getByTestId('section-repeatable')).toBeDisabled();

    // 仅勾「模板默认文本」→ 说明「本章出固定内容」（T20260720 三态说明）
    expect(screen.getByTestId('section-authored-note')).toHaveAttribute('data-note-kind', 'boilerplate_only');

    // 再勾「人工撰稿（AI 起草初稿）」→ 同选态：默认文本作预填底稿、改写后覆盖
    fireEvent.click(screen.getByText('人工撰稿（AI 起草初稿）'));
    await waitFor(() =>
      expect(screen.getByTestId('section-authored-note')).toHaveAttribute('data-note-kind', 'both'),
    );

    // 取消「模板默认文本」→ 仅撰稿态
    fireEvent.click(screen.getByText('模板默认文本（固定预填）'));
    await waitFor(() =>
      expect(screen.getByTestId('section-authored-note')).toHaveAttribute('data-note-kind', 'authored_only'),
    );

    // 两个都取消 → 不显示说明
    fireEvent.click(screen.getByText('人工撰稿（AI 起草初稿）'));
    await waitFor(() => expect(screen.queryByTestId('section-authored-note')).toBeNull());

    // 勾一个需求条目类型 → 逐条目成节解除灰置
    fireEvent.click(screen.getByText('功能需求'));
    await waitFor(() => expect(screen.getByTestId('section-repeatable')).not.toBeDisabled());
  });

  it('暂存草稿：改动出现「未暂存」标记，暂存成功后消失（新建走 createDraft）', async () => {
    createDraftMock.mockResolvedValue({
      draft_ref: 'd-1', name: '草稿', payload: '{}', origin: 'blank',
      source_registry_ref: null, created_by: 'U1',
      created_at: '2026-07-09T10:00:00Z', updated_at: '2026-07-09T10:00:00Z',
    });
    renderEditor();
    expect(screen.getByTestId('designer-save-draft')).toBeInTheDocument();
    expect(screen.queryByTestId('designer-dirty-tag')).toBeNull();

    fireEvent.click(screen.getByTestId('designer-add-top'));
    await waitFor(() => expect(screen.getByTestId('designer-dirty-tag')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('designer-save-draft'));
    await waitFor(() => expect(createDraftMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId('designer-dirty-tag')).toBeNull());
    // 暂存信封带定制器状态版本号
    const payload = JSON.parse((createDraftMock.mock.calls[0][0] as { payload: string }).payload);
    expect(payload.designer_state_version).toBe(1);
  });

  it('未暂存修改点返回 → 退出守卫弹窗（继续编辑/直接退出/暂存并退出）', async () => {
    const { onBack } = renderEditor();
    fireEvent.click(screen.getByTestId('designer-add-top'));
    fireEvent.click(screen.getByTestId('designer-back'));
    await waitFor(() => expect(screen.getByTestId('exit-save')).toBeInTheDocument());
    expect(onBack).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('exit-discard'));
    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
  });

  it('无修改点返回 → 直接退出不弹窗', () => {
    const { onBack } = renderEditor();
    fireEvent.click(screen.getByTestId('designer-back'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('章节样例可增删', async () => {
    renderEditor();
    selectFirstTreeNode();
    await waitFor(() => expect(screen.getByTestId('designer-section-form')).toBeInTheDocument());
    expect(screen.queryAllByTestId('section-example-row')).toHaveLength(0);
    fireEvent.click(screen.getByTestId('section-example-add'));
    await waitFor(() => expect(screen.getAllByTestId('section-example-row')).toHaveLength(1));
    fireEvent.click(screen.getByTestId('section-example-remove'));
    await waitFor(() => expect(screen.queryAllByTestId('section-example-row')).toHaveLength(0));
  });
});
