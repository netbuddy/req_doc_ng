import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import type { TemplateDraftRead, TemplateRegistryRead } from '../src/api/templates';

// 模板端点 mock：面板复用既有 GET /templates（list）+ GET /template-drafts；不打真实后端。
const listMock = vi.fn();
const listDraftsMock = vi.fn();
vi.mock('../src/api/templates', () => ({
  templatesApi: {
    list: (...args: unknown[]) => listMock(...args),
    listDrafts: (...args: unknown[]) => listDraftsMock(...args),
    createDraft: vi.fn(),
    updateDraft: vi.fn(),
    deleteDraft: vi.fn(),
    getDetail: vi.fn(),
    validate: vi.fn(),
    register: vi.fn(),
    setStatus: vi.fn(),
    previewDocxUrl: (ref: string) => `/api/templates/${ref}/preview-docx`,
  },
}));

import { DocumentTemplatePanel } from '../src/workbenches/DocumentTemplatePanel';

function row(overrides: Partial<TemplateRegistryRead> = {}): TemplateRegistryRead {
  return {
    registry_ref: 'r-1',
    template_key: 'srs-iso29148-v1',
    version_no: 1,
    name: 'SRS 内置模板',
    schema_version: '1.0',
    doc_type: 'srs',
    content_hash: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
    source: 'builtin',
    status: 'active',
    registered_by: 'system',
    registered_at: '2026-07-01T10:00:00Z',
    ...overrides,
  };
}

function draft(overrides: Partial<TemplateDraftRead> = {}): TemplateDraftRead {
  return {
    draft_ref: 'd-1',
    name: '内部 SRS 模板（草稿）',
    payload: '{"designer_state_version":1}',
    origin: 'blank',
    source_registry_ref: null,
    created_by: 'U1',
    created_at: '2026-07-09T10:00:00Z',
    updated_at: '2026-07-09T11:30:00Z',
    ...overrides,
  };
}

describe('DocumentTemplatePanel（设置 › 文档模板 列表页冒烟）', () => {
  beforeEach(() => {
    listMock.mockReset();
    listDraftsMock.mockReset();
    listDraftsMock.mockResolvedValue([]);
  });

  it('渲染定制/登记入口与模板卡片（复用既有列表端点）', async () => {
    listMock.mockResolvedValue([row()]);
    render(<DocumentTemplatePanel operatorRef="U1" />);

    // 定制新模板 / 登记新模板 入口（迁自发布工作台）都在此处。
    expect(await screen.findByTestId('dt-open-designer')).toBeInTheDocument();
    expect(screen.getByTestId('dt-open-register')).toBeInTheDocument();

    // 列表卡片按 list 端点渲染；卡片带「编辑」入口（登记为同 key 新版本）。
    await waitFor(() => expect(screen.getByTestId('dt-card-grid')).toBeInTheDocument());
    expect(screen.getByText('SRS 内置模板')).toBeInTheDocument();
    expect(screen.getByTestId('dt-edit-template')).toBeInTheDocument();
    expect(listMock).toHaveBeenCalled();
    expect(listDraftsMock).toHaveBeenCalled();
  });

  it('空注册表显示引导空态', async () => {
    listMock.mockResolvedValue([]);
    render(<DocumentTemplatePanel operatorRef="U1" />);
    await waitFor(() => expect(screen.getByText(/暂无可用模板/)).toBeInTheDocument());
  });

  it('有暂存草稿时渲染草稿列表（继续编辑/删除入口）', async () => {
    listMock.mockResolvedValue([row()]);
    listDraftsMock.mockResolvedValue([draft()]);
    render(<DocumentTemplatePanel operatorRef="U1" />);

    await waitFor(() => expect(screen.getByTestId('dt-draft-list')).toBeInTheDocument());
    expect(screen.getByText('内部 SRS 模板（草稿）')).toBeInTheDocument();
    expect(screen.getByText('空白起草')).toBeInTheDocument();
    expect(screen.getByTestId('dt-draft-resume')).toBeInTheDocument();
    expect(screen.getByTestId('dt-draft-delete')).toBeInTheDocument();
  });
});
