import type { NotificationListRead } from '../api/notifications';

// 通知铃铛 + 通知抽屉的展示模型(04A §2.1)。纯映射,不发请求。

export type NotificationsFetchPhase = 'loading' | 'error' | 'ready';

export interface NotificationItemVM {
  id: string;
  title: string;
  summary: string;
  kindLabel: string;
  read: boolean;
  occurrencesText: string | null;
  /** ISO 时刻;相对文案与悬停原值交由 RelativeTime 呈现(相对时间会过期,须由单钟刷新) */
  updatedAt: string;
}

export interface NotificationsVM {
  unreadCount: number;
  items: NotificationItemVM[];
  emptyText: string;
}

const KIND_LABELS: Record<string, string> = {
  'agent_run.failed': 'AI 任务失败',
  'export.failed': '导出失败',
};

export function buildNotificationsVM(
  data: NotificationListRead | null,
  phase: NotificationsFetchPhase,
): NotificationsVM {
  if (data === null) {
    return {
      unreadCount: 0,
      items: [],
      emptyText: phase === 'error' ? '后端不可达,无法获取通知' : '正在加载通知…',
    };
  }

  return {
    unreadCount: data.unread_count,
    items: data.notifications.map((item) => ({
      id: item.id,
      title: item.title,
      summary: item.summary,
      kindLabel: KIND_LABELS[item.kind] ?? item.kind,
      read: item.read,
      occurrencesText: item.occurrences > 1 ? `已发生 ${item.occurrences} 次` : null,
      updatedAt: item.updated_at,
    })),
    emptyText: '暂无需要处理的通知',
  };
}
