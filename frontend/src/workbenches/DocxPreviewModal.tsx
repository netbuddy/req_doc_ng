import { Alert, Button, Modal, Segmented, Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { renderAsync } from 'docx-preview';
import { publicationApi } from '../api/publication';

type PreviewMode = 'content' | 'pdf';

interface DocxPreviewModalProps {
  open: boolean;
  title: string;
  projectId: string | undefined;
  exportRef: string | null;
  onClose: () => void;
}

/**
 * 候选 / 基线 docx 在线预览（双模）。
 * - 内容预览（快速）：docx-preview 在浏览器 HTML 近似渲染，看内容用；版式/页数不保证与真实 docx 一致。
 * - 精确预览（PDF）：后端 LibreOffice 把 docx 真转 PDF，浏览器原生查看器呈现，真实分页/版式/页数正确。
 * 字节均走 publicationApi（守 MVVM 边界：视图不直连 fetch）。LibreOffice 缺失时精确预览回 503，
 * 提示降级到内容预览/下载查看。
 */
export function DocxPreviewModal({ open, title, projectId, exportRef, onClose }: DocxPreviewModalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<PreviewMode>('content');
  // 加载开关按页签各自独立：遮罩只看当前页签自己的开关，切页签无须任何代码去碰开关，
  // 「切走再切回 → 转圈永不熄灭」（issue #86 缺陷 1）在结构上不可发生。
  const [contentLoading, setContentLoading] = useState(false);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pdfReady, setPdfReady] = useState(false);
  const renderedDocxRef = useRef<string | null>(null);  // 已渲染内容预览的 exportRef
  const probedPdfRef = useRef<string | null>(null);      // 已探活 PDF 的 exportRef

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
  const pdfUrl = projectId && exportRef ? publicationApi.exportPdfUrl(projectId, exportRef) : '';

  // 打开或切换预览目标时重置：回内容模式、清渲染/探活标记。
  useEffect(() => {
    if (!open) return;
    setMode('content');
    setError(null);
    setPdfReady(false);
    renderedDocxRef.current = null;
    probedPdfRef.current = null;
    if (containerRef.current) containerRef.current.innerHTML = '';
  }, [open, exportRef]);

  // 内容预览：docx-preview 渲染（每个目标只渲染一次）。
  useEffect(() => {
    if (!open || mode !== 'content' || !projectId || !exportRef) return;
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
        // 无条件关：本开关只管内容预览页签，被中止的这一轮关掉自己的开关伤不到别的页签。
        setContentLoading(false);
      }
    })();
    return () => {
      disposed = true;
    };
  }, [open, mode, projectId, exportRef]);

  // 精确预览：先探活（HEAD 触发/命中 LibreOffice 转换缓存），可用则 iframe 直连真实 PDF 地址渲染
  // （浏览器原生查看器对真实 URL 渲染可靠；blob: 在部分环境不渲染，故不用 objectURL）。
  useEffect(() => {
    if (!open || mode !== 'pdf' || !projectId || !exportRef) return;
    if (probedPdfRef.current === exportRef) return;
    let disposed = false;
    setError(null);
    setPdfReady(false);
    setPdfLoading(true);
    publicationApi
      .probeExportPdf(projectId, exportRef)
      .then(() => {
        if (disposed) return;
        probedPdfRef.current = exportRef;
        setPdfReady(true);
      })
      .catch(() => {
        if (!disposed) {
          setError('精确预览暂不可用（服务端可能未安装 LibreOffice）。可切换到内容预览，或下载查看。');
        }
      })
      .finally(() => {
        // 同上：无条件关本页签的开关（探活被中止时其结果作废，下次进精确预览会重新探活）。
        setPdfLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [open, mode, projectId, exportRef]);

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
            {mode === 'pdf'
              ? '精确预览：真实分页与版式（LibreOffice 渲染）'
              : '内容预览：仅供看内容，版式/页数以精确预览为准'}
          </span>
          <Button href={fileUrl} target="_blank" disabled={!fileUrl}>
            下载查看
          </Button>
        </div>
      }
    >
      <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--color-border-soft)' }}>
        <Segmented
          value={mode}
          onChange={(v) => setMode(v as PreviewMode)}
          options={[
            { label: '内容预览（快速）', value: 'content' },
            { label: '精确预览（PDF）', value: 'pdf' },
          ]}
        />
      </div>
      <div style={{ position: 'relative', flex: 1, minHeight: 0, userSelect: resizing ? 'none' : undefined }}>
        {(mode === 'content' ? contentLoading : pdfLoading) ? (
          <div className="docx-preview-loading">
            <Spin description={mode === 'pdf' ? '正在生成精确预览…' : '正在加载内容预览…'} />
          </div>
        ) : null}
        {error ? (
          <Alert
            type={mode === 'pdf' ? 'warning' : 'error'}
            showIcon
            message={mode === 'pdf' ? '精确预览不可用' : '预览加载失败'}
            description={error}
            style={{ margin: 16 }}
          />
        ) : null}
        {/* 内容预览容器：始终挂载，非内容模式隐藏（保留已渲染 DOM，切回不重渲染） */}
        <div
          className="docx-preview-modal"
          ref={containerRef}
          style={{ display: mode === 'content' ? 'block' : 'none' }}
        />
        {/* 精确预览：浏览器原生 PDF 查看器（自带分页/页数/缩放）；直连真实 PDF 地址。
            拖拽时给 iframe 关掉指针事件，否则鼠标移到 iframe 上父窗口收不到 mousemove。 */}
        {mode === 'pdf' && pdfReady && !error ? (
          <iframe
            title="docx 精确预览"
            src={pdfUrl}
            style={{ width: '100%', height: '100%', border: 'none', pointerEvents: resizing ? 'none' : 'auto' }}
          />
        ) : null}
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
