import { Alert, Button, Modal, Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { renderAsync } from 'docx-preview';
import { publicationApi } from '../api/publication';

interface DocxPreviewModalProps {
  open: boolean;
  title: string;
  projectId: string | undefined;
  exportRef: string | null;
  onClose: () => void;
}

/**
 * 候选 / 基线 docx 在线预览：docx-preview 在浏览器 HTML 近似渲染，看内容用；版式/页数以下载后的
 * Word 文件为准。字节走 publicationApi（守 MVVM 边界：视图不直连 fetch）。
 * 原「精确预览（PDF）」（后端 LibreOffice 转 PDF）已退役（AppImage 单机模式方案裁定 D2）。
 */
export function DocxPreviewModal({ open, title, projectId, exportRef, onClose }: DocxPreviewModalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const renderedDocxRef = useRef<string | null>(null);  // 已渲染内容预览的 exportRef

  // 可拖拽调整的弹窗尺寸（w=弹窗宽，h=正文区高）；默认适当加大，用户可拖右下角把手放大/缩小。
  // 默认值与拖拽夹紧同界（减 24/132 给边距与页眉页脚），避免小视口下首次拖拽发生跳变。
  const [size, setSize] = useState(() => ({
    w: Math.min(1160, window.innerWidth - 24),
    h: Math.min(Math.round(window.innerHeight * 0.84), window.innerHeight - 132),
  }));
  const [resizing, setResizing] = useState(false);

  // 右下角把手拖拽：弹窗居中，从中心对称缩放 → 宽/高各按 2× 光标位移，把手即跟随光标。
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startY = e.clientY;
    const startW = size.w;
    const startH = size.h;
    setResizing(true);
    const onMove = (ev: MouseEvent) => {
      const w = Math.max(720, Math.min(window.innerWidth - 24, startW + (ev.clientX - startX) * 2));
      const h = Math.max(360, Math.min(window.innerHeight - 132, startH + (ev.clientY - startY) * 2));
      setSize({ w, h });
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const fileUrl = projectId && exportRef ? publicationApi.exportFileUrl(projectId, exportRef) : '';

  // 打开或切换预览目标时重置：清渲染标记。
  useEffect(() => {
    if (!open) return;
    setError(null);
    renderedDocxRef.current = null;
    if (containerRef.current) containerRef.current.innerHTML = '';
  }, [open, exportRef]);

  // 内容预览：docx-preview 渲染（每个目标只渲染一次）。
  useEffect(() => {
    if (!open || !projectId || !exportRef) return;
    if (renderedDocxRef.current === exportRef) return;
    let disposed = false;
    setError(null);
    setContentLoading(true);
    if (containerRef.current) containerRef.current.innerHTML = '';
    (async () => {
      try {
        const blob = await publicationApi.fetchExportBlob(projectId, exportRef);
        if (disposed || !containerRef.current) return;
        await renderAsync(blob, containerRef.current, undefined, {
          className: 'docx',
          inWrapper: true,
        });
        renderedDocxRef.current = exportRef;
      } catch (e) {
        if (!disposed) setError(e instanceof Error ? e.message : String(e));
      } finally {
        setContentLoading(false);
      }
    })();
    return () => {
      disposed = true;
    };
  }, [open, projectId, exportRef]);

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onClose}
      width={size.w}
      centered
      styles={{ body: { padding: 0, height: size.h, overflow: 'hidden', display: 'flex', flexDirection: 'column' } }}
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 12, opacity: 0.65 }}>
            内容预览：仅供看内容，版式/页数以下载后的 Word 文件为准
          </span>
          <Button href={fileUrl} target="_blank" disabled={!fileUrl}>
            下载查看
          </Button>
        </div>
      }
    >
      <div style={{ position: 'relative', flex: 1, minHeight: 0, userSelect: resizing ? 'none' : undefined }}>
        {contentLoading ? (
          <div className="docx-preview-loading">
            <Spin description="正在加载内容预览…" />
          </div>
        ) : null}
        {error ? (
          <Alert type="error" showIcon message="预览加载失败" description={error} style={{ margin: 16 }} />
        ) : null}
        <div className="docx-preview-modal" ref={containerRef} />
        {/* 右下角缩放把手：拖动放大/缩小预览窗口 */}
        <div
          className="docx-preview-resize"
          onMouseDown={startResize}
          role="separator"
          aria-label="拖动调整预览窗口大小"
          title="拖动调整大小"
        />
      </div>
    </Modal>
  );
}
