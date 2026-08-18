import type { ReactNode } from 'react';

interface WorkbenchFrameProps {
  /* 不再渲染可见标题，仅作 aria-label 锚点（可访问性 + 测试定位） */
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}

export function WorkbenchFrame({ title, extra, children }: WorkbenchFrameProps) {
  return (
    <section className="workbench-frame page-fill" aria-label={title}>
      {extra ? <div className="workbench-actions-row">{extra}</div> : null}
      {children}
    </section>
  );
}
