import {
  BranchesOutlined,
  BulbOutlined,
  DatabaseOutlined,
  ProjectOutlined,
  ReadOutlined,
} from '@ant-design/icons';
import type { ComponentType, CSSProperties } from 'react';
import type { WorkbenchKey } from './app-shell';

type IconComponent = ComponentType<{ style?: CSSProperties; className?: string }>;

// 全局检索 entity_type → { 工作台 / 组标签 / 图标 / 徽标色 } 单一来源映射（工作包 01 §6.3 / 05 §2.4）。
// 服务端已给每条 hit 的 workbench（落点权威）；此表补前端展示元数据 + "码→选中动作"落点校验。
// 术语用「知识项」（两翼新词，非「要素」）；徽标色走 antd 预设色（主题令牌，不硬编码 hex）。

export interface SearchEntityMeta {
  workbench: WorkbenchKey;
  groupLabel: string; // 兜底组头；实际组头优先取 API 的 group.label（labels.py 单一来源）
  icon: IconComponent;
  tagColor: string; // antd 预设色名（主题感知）
}

// 跨项目深链目标（P4，05 §3）：命令面板选中 → App 携此对象切项目 + 切工作台 → 各工作台一次性消费。
// token（Date.now()）+ projectId 双守卫：解决"异步切项目竞态"与"StrictMode 双调用"（范式同 resumeConsumedRef）。
export interface SearchTarget {
  projectId: string;
  workbench: WorkbenchKey;
  entityType: string;
  ref: string;
  title: string;
  token: number;
}

export const SEARCH_ENTITY_META: Record<string, SearchEntityMeta> = {
  requirement_item: { workbench: 'management', groupLabel: '需求条目', icon: ProjectOutlined, tagColor: 'blue' },
  element: { workbench: 'management', groupLabel: '知识项', icon: BulbOutlined, tagColor: 'gold' },
  chart: { workbench: 'diagram', groupLabel: '图表', icon: BranchesOutlined, tagColor: 'geekblue' },
  document: { workbench: 'release', groupLabel: '文档', icon: ReadOutlined, tagColor: 'green' },
  material: { workbench: 'management', groupLabel: '材料', icon: DatabaseOutlined, tagColor: 'volcano' },
};

export function searchEntityMeta(entityType: string): SearchEntityMeta {
  return (
    SEARCH_ENTITY_META[entityType] ?? {
      workbench: 'management',
      groupLabel: entityType,
      icon: ProjectOutlined,
      tagColor: 'default',
    }
  );
}

// snippet 高亮：把 q 命中处切分为 { text, hit } 段，组件渲染时把 hit 段包 <mark>（口径与服务端一致）。
export function highlightSnippet(snippet: string, q: string): Array<{ text: string; hit: boolean }> {
  const query = q.trim();
  if (!query || !snippet) return [{ text: snippet, hit: false }];
  const lower = snippet.toLowerCase();
  const needle = query.toLowerCase();
  const parts: Array<{ text: string; hit: boolean }> = [];
  let cursor = 0;
  while (cursor < snippet.length) {
    const idx = lower.indexOf(needle, cursor);
    if (idx === -1) {
      parts.push({ text: snippet.slice(cursor), hit: false });
      break;
    }
    if (idx > cursor) parts.push({ text: snippet.slice(cursor, idx), hit: false });
    parts.push({ text: snippet.slice(idx, idx + needle.length), hit: true });
    cursor = idx + needle.length;
  }
  return parts;
}
