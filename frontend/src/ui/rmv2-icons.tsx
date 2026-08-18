import type { ReactNode } from 'react';

/**
 * rmv2 线性图标集（复刻 v2 基准件图标精灵，替代 emoji）：
 * stroke=currentColor / fill=none / 1.7 线宽圆角端点，尺寸由 .i/.i.sm 类控制。
 */
const ICON_PATHS: Record<string, ReactNode> = {
  manage: <path d="M12 3 3.5 7.2 12 11.4l8.5-4.2zM3.5 12 12 16.2 20.5 12M3.5 16.8 12 21l8.5-4.2" />,
  trace: (
    <>
      <circle cx="6" cy="6" r="2.3" />
      <circle cx="18" cy="18" r="2.3" />
      <circle cx="18" cy="6" r="2.3" />
      <path d="M8 7.4 16 16.6M8 6h6.5M18 8.3v7.4" />
    </>
  ),
  chart: <path d="M4 20V4M4 20h16M8 16v-4.5M12 16V8.5M16 16v-6" />,
  doc: <path d="M6.5 3h7l4 4v13a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 5.5 20V4a.5.5 0 0 1 .5-.5zM13.5 3v4.5H18" />,
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.6-3.6" />
    </>
  ),
  'chev-r': <path d="M9.5 6l6 6-6 6" />,
  'chev-d': <path d="M6 9.5l6 6 6-6" />,
  check: <path d="M4 12.5 9 17.5 20 6.5" />,
  warn: <path d="M12 3 2.5 20h19zM12 9.5v5M12 17.6v.3" />,
  alert: <path d="M8.2 3h7.6l5.2 5.2v7.6l-5.2 5.2H8.2L3 15.8V8.2zM12 8v5M12 16.6v.3" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.6v.3" />
    </>
  ),
  link: <path d="M9 15l6-6M8.5 12l-2 2a3 3 0 1 0 4 4l2-2M15.5 12l2-2a3 3 0 1 0-4-4l-2 2" />,
  edit: <path d="M4 20h4L18.4 9.6a2 2 0 0 0-3-3L5 17zM13.5 6.6l3 3" />,
  shield: <path d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6zM9 12l2 2 4-4" />,
  spark: <path d="M11 3.5l1.7 4.6 4.6 1.7-4.6 1.7L11 16.1 9.3 11.5 4.7 9.8l4.6-1.7zM18.5 14l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5.2l3.4 2" />
    </>
  ),
  flag: <path d="M6 21V4M6 4.5h11l-2 3.2 2 3.3H6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  list: <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />,
  grid: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.4" />
      <rect x="13" y="4" width="7" height="7" rx="1.4" />
      <rect x="4" y="13" width="7" height="7" rx="1.4" />
      <rect x="13" y="13" width="7" height="7" rx="1.4" />
    </>
  ),
  branch: (
    <>
      <circle cx="6" cy="6" r="2.3" />
      <circle cx="6" cy="18" r="2.3" />
      <circle cx="18" cy="8" r="2.3" />
      <path d="M6 8.3v7.4M8.3 6H14a2 2 0 0 1 2 2v0" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  zap: <path d="M13 3 4.5 13.5H11l-1 7.5L18.5 10.5H12z" />,
  scan: <path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16M4 12h16" />,
  wand: <path d="M6 21 17 10M14.5 6.5l3 3M15 3l.8 1.7L17.5 5.5 15.8 6.3 15 8l-.8-1.7L12.5 5.5 14.2 4.7zM5 8l.6 1.3L7 10l-1.4.7L5 12l-.6-1.3L3 10l1.4-.7z" />,
};

export type RmIconName = keyof typeof ICON_PATHS;

export function RmIcon({ name, className }: { name: string; className?: string }) {
  const paths = ICON_PATHS[name];
  if (!paths) return null;
  return (
    <svg aria-hidden="true" className={className ? `i ${className}` : 'i'} viewBox="0 0 24 24">
      {paths}
    </svg>
  );
}

/** 左树彩色文件夹（复刻基准件 .fdr）：填色按资产组，装饰性图形。 */
export function RmFolderIcon({ tone }: { tone: 'amber' | 'purple' | 'blue' }) {
  const fills: Record<string, string> = {
    amber: 'var(--amber-weak, #ffd77e)',
    purple: 'var(--purple-weak, #c9b8ff)',
    blue: 'var(--blue-weak, #a8d4ff)',
  };
  const strokes: Record<string, string> = {
    amber: 'var(--amber, #d99a1b)',
    purple: 'var(--purple, #8b5cf6)',
    blue: 'var(--blue, #1677ff)',
  };
  return (
    <svg aria-hidden="true" className="i sm fdr" viewBox="0 0 24 24" style={{ fill: fills[tone], stroke: strokes[tone] }}>
      <path d="M3.5 6.2A1.2 1.2 0 0 1 4.7 5h4.4l2 2.4h8.2a1.2 1.2 0 0 1 1.2 1.2v9.2a1.2 1.2 0 0 1-1.2 1.2H4.7a1.2 1.2 0 0 1-1.2-1.2z" strokeWidth="1.4" />
    </svg>
  );
}
