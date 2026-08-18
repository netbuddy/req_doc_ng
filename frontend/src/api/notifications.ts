import { apiGet, apiPost } from './client';

// 通知徽标(04A §2.1):需人处理的未读事项,按事项去重。
// infra 风格端点,手写类型(同 runtime-status.ts)。

export interface NotificationRead {
  id: string;
  kind: string; // agent_run.failed | export.failed | ...
  title: string;
  summary: string;
  project_ref?: string | null;
  ref?: string | null;
  occurrences: number;
  read: boolean;
  created_at: string;
  updated_at: string;
}

export interface NotificationListRead {
  notifications: NotificationRead[];
  unread_count: number;
}

export interface NotificationActionResult {
  status: 'marked_read' | 'already_read' | 'all_read' | string;
  unread_count: number;
}

export const notificationsApi = {
  list(): Promise<NotificationListRead> {
    return apiGet<NotificationListRead>('/notifications');
  },

  markRead(notificationId: string): Promise<NotificationActionResult> {
    return apiPost<NotificationActionResult>(
      `/notifications/${encodeURIComponent(notificationId)}/read`,
    );
  },

  markAllRead(): Promise<NotificationActionResult> {
    return apiPost<NotificationActionResult>('/notifications/read-all');
  },
};
