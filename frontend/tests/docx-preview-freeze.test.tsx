/**
 * docx 在线预览窗「转圈永不熄灭 + 遮罩挡操作」回归用例（试点卡 issue #86）。
 *
 * 对应验收场景（《试点卡-86-docx预览冻结-需求文档.md》§3）：
 * - V-86-S1：内容预览已渲染完成 → 切到精确预览（探活未返回）→ 切回内容预览，转圈须消失。
 * - V-86-S2：精确预览已就绪 → 内容预览转圈中切到精确预览，不得出现永久转圈。
 * - V-86-S3：遮罩样式带点击穿透（滚轮与点击落到下层内容）。
 *
 * 设计依据：加载开关按页签各自独立（内容一个、精确一个），遮罩只看当前页签自己的开关。
 * 用受控 Promise 模拟「渲染挂起」「探活挂起」，避免真实时钟带来的抖动。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** 永不 settle 的 Promise：模拟「一直转圈」的加载。 */
function pending<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

const fetchExportBlobMock = vi.fn();
const probeExportPdfMock = vi.fn();
vi.mock('../src/api/publication', () => ({
  publicationApi: {
    fetchExportBlob: (...args: unknown[]) => fetchExportBlobMock(...args),
    probeExportPdf: (...args: unknown[]) => probeExportPdfMock(...args),
    exportFileUrl: (projectId: string, exportRef: string) =>
      `/api/projects/${projectId}/publication/exports/${exportRef}/file`,
    exportPdfUrl: (projectId: string, exportRef: string) =>
      `/api/projects/${projectId}/publication/exports/${exportRef}/pdf`,
  },
}));

const renderAsyncMock = vi.fn();
vi.mock('docx-preview', () => ({
  renderAsync: (...args: unknown[]) => renderAsyncMock(...args),
}));

import { DocxPreviewModal } from '../src/workbenches/DocxPreviewModal';

const mask = () => document.querySelector('.docx-preview-loading');
const contentPane = () => document.querySelector<HTMLElement>('.docx-preview-modal');
const pdfFrame = () => document.querySelector('iframe[title="docx 精确预览"]');

/** 页签切换：点 Segmented 的选项文字。 */
function switchTo(label: '内容预览（快速）' | '精确预览（PDF）') {
  fireEvent.click(screen.getByText(label));
}

function renderModal() {
  return render(
    <DocxPreviewModal open title="需求规格说明 V1.13" projectId="P1" exportRef="E1" onClose={() => {}} />,
  );
}

describe('DocxPreviewModal 预览冻结（issue #86）', () => {
  beforeEach(() => {
    fetchExportBlobMock.mockReset();
    probeExportPdfMock.mockReset();
    renderAsyncMock.mockReset();
    // 渲染 mock 往容器里写点内容，便于断言「文档已渲染好」。
    renderAsyncMock.mockImplementation(async (_blob: unknown, container: HTMLElement) => {
      container.innerHTML = '<div class="docx-wrapper">已渲染的文档正文</div>';
    });
  });

  it('V-86-S1 精确预览探活未返回时切回内容预览，转圈消失、内容可见', async () => {
    const blob = deferred<Blob>();
    fetchExportBlobMock.mockReturnValue(blob.promise);
    probeExportPdfMock.mockReturnValue(pending<void>()); // 探活挂起，模拟首次 PDF 转换的数秒窗口

    renderModal();

    // 内容预览渲染完成 → 转圈熄灭
    blob.resolve(new Blob(['docx']));
    await waitFor(() => expect(screen.getByText('已渲染的文档正文')).toBeInTheDocument());
    await waitFor(() => expect(mask()).toBeNull());

    // 切到精确预览：探活挂起，转圈亮起
    switchTo('精确预览（PDF）');
    await waitFor(() => expect(mask()).not.toBeNull());
    expect(screen.getByText('正在生成精确预览…')).toBeInTheDocument();

    // 转圈结束前切回内容预览：内容早已渲染好，转圈必须随即消失
    switchTo('内容预览（快速）');
    await waitFor(() => expect(mask()).toBeNull());
    expect(contentPane()).not.toBeNull();
    expect(contentPane()!.style.display).toBe('block');
    expect(screen.getByText('已渲染的文档正文')).toBeInTheDocument();
  });

  it('V-86-S2 内容预览渲染挂起时切到已就绪的精确预览，转圈消失、PDF 可见', async () => {
    // 每次调用返回一只新的受控 Promise：内容预览可以「两次都挂起」。
    const contentCalls: Deferred<Blob>[] = [];
    fetchExportBlobMock.mockImplementation(() => {
      const d = deferred<Blob>();
      contentCalls.push(d);
      return d.promise;
    });
    probeExportPdfMock.mockResolvedValue(undefined); // 探活立即成功

    renderModal();

    // 内容预览取字节挂起 → 转圈亮着
    await waitFor(() => expect(mask()).not.toBeNull());
    expect(screen.getByText('正在加载内容预览…')).toBeInTheDocument();

    // 切到精确预览：探活立即成功 → 转圈熄灭、PDF 出现（此后「精确预览已就绪」）
    switchTo('精确预览（PDF）');
    await waitFor(() => expect(pdfFrame()).not.toBeNull());
    expect(mask()).toBeNull();

    // 切回内容预览：内容仍未渲染成功，重新取字节且再次挂起 → 转圈亮着（本页签确实在加载）
    switchTo('内容预览（快速）');
    await waitFor(() => expect(mask()).not.toBeNull());
    expect(contentCalls.length).toBe(2);

    // 内容预览转圈中再切到已就绪的精确预览：探活跳过，转圈必须消失、PDF 仍在
    switchTo('精确预览（PDF）');
    await waitFor(() => expect(mask()).toBeNull());
    expect(pdfFrame()).not.toBeNull();
    expect(probeExportPdfMock).toHaveBeenCalledTimes(1); // 已探活过就不再重探
  });

  it('V-86-S3 加载遮罩样式带点击穿透，不拦滚轮与点击', () => {
    // 读文件路径按仓内既有惯例走 process.cwd()（见 tests/mvvm-boundary.test.ts）。
    const css = readFileSync(join(process.cwd(), 'src', 'styles.css'), 'utf-8');
    const start = css.indexOf('.docx-preview-loading {');
    expect(start).toBeGreaterThan(-1);
    const block = css.slice(start, css.indexOf('}', start));
    expect(block).toMatch(/pointer-events:\s*none\s*;/);
  });
});
