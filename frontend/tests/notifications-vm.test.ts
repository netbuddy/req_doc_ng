import { describe, expect, it } from 'vitest';
import type { NotificationListRead } from '../src/api/notifications';
import { buildNotificationsVM } from '../src/view-models/notifications';

function payload(overrides: Partial<NotificationListRead> = {}): NotificationListRead {
  return {
    unread_count: 1,
    notifications: [
      {
        id: 'n1',
        kind: 'agent_run.failed',
        title: 'AI 任务失败：需求条目形成',
        summary: '任务已标记失败，可在对应工作台重试、补充材料或转人工处理。',
        occurrences: 1,
        read: false,
        created_at: '2026-07-04T11:55:00Z',
        updated_at: '2026-07-04T11:55:00Z',
      },
      {
        id: 'n2',
        kind: 'export.failed',
        title: 'docx 导出失败：需求规格说明',
        summary: '可重试导出或改用人工降级导出。',
        occurrences: 3,
        read: true,
        created_at: '2026-07-04T10:00:00Z',
        updated_at: '2026-07-04T11:00:00Z',
      },
    ],
    ...overrides,
  };
}

describe('notifications view model', () => {
  it('未读计数 + 条目映射(kind 标签/时刻透传/复发次数)', () => {
    const vm = buildNotificationsVM(payload(), 'ready');

    expect(vm.unreadCount).toBe(1);
    expect(vm.items).toHaveLength(2);

    const [unreadItem, readItem] = vm.items;
    expect(unreadItem.kindLabel).toBe('AI 任务失败');
    expect(unreadItem.read).toBe(false);
    expect(unreadItem.occurrencesText).toBeNull();
    // VM 只透传 ISO,相对文案由 RelativeTime 呈现(见 relative-time.test.tsx)
    expect(unreadItem.updatedAt).toBe('2026-07-04T11:55:00Z');

    expect(readItem.kindLabel).toBe('导出失败');
    expect(readItem.read).toBe(true);
    expect(readItem.occurrencesText).toBe('已发生 3 次');
    expect(readItem.updatedAt).toBe('2026-07-04T11:00:00Z');
  });

  it('未知 kind 回退为稳定码本身', () => {
    const vm = buildNotificationsVM(
      payload({
        notifications: [
          {
            id: 'n3',
            kind: 'gate.blocked',
            title: 't',
            summary: 's',
            occurrences: 1,
            read: false,
            created_at: '2026-07-04T11:59:50Z',
            updated_at: '2026-07-04T11:59:50Z',
          },
        ],
      }),
      'ready',
    );

    expect(vm.items[0].kindLabel).toBe('gate.blocked');
  });

  it('空列表与加载/错误占位', () => {
    const empty = buildNotificationsVM(payload({ notifications: [], unread_count: 0 }), 'ready');
    expect(empty.unreadCount).toBe(0);
    expect(empty.emptyText).toBe('暂无需要处理的通知');

    expect(buildNotificationsVM(null, 'loading').emptyText).toBe('正在加载通知…');
    expect(buildNotificationsVM(null, 'error').emptyText).toBe('后端不可达,无法获取通知');
    expect(buildNotificationsVM(null, 'error').unreadCount).toBe(0);
  });
});
