import { Alert, Empty, Spin } from 'antd';
import { useEffect, useState } from 'react';
import { diagramsApi } from '../api/diagrams';

// PlantUML 无浏览器端渲染器：走后端本机 plantuml.jar 出 PNG（不出网）。
// 渲染失败只影响预览，不影响已保存的事实源。
export function PlantumlPreview({
  code,
  emptyText = '尚无受控源码；编辑源码或请求 AI 建议后展示预览',
}: {
  code: string;
  emptyText?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    if (!code.trim()) {
      setUrl(null);
      setError(null);
      return;
    }
    setLoading(true);
    diagramsApi
      .renderPng('plantuml', code)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [code]);

  if (!code.trim()) return <Empty description={emptyText} />;
  if (loading && !url) return <Spin />;
  if (error) {
    return (
      <Alert
        title="PlantUML 预览渲染失败（不影响已保存的事实）"
        description={error}
        showIcon
        type="warning"
      />
    );
  }
  if (url) return <img className="md-diagram__img" src={url} alt="PlantUML 图" />;
  return null;
}
