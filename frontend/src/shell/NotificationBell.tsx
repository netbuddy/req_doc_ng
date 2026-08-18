import { Badge, Button, Drawer, Tag, Tooltip } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { notificationsApi, type NotificationListRead } from '../api/notifications';
import { buildNotificationsVM, type NotificationsFetchPhase } from '../view-models/notifications';
import { BellOutlined } from '../ui/icons';
import { RelativeTime } from '../ui/RelativeTime';

// 状态栏通知徽标 + 通知抽屉(04A §2.1)。
// 计数=未读待处理事项数(按事项去重);只承接需人处理的事件,不承接运行日志。
const POLL_INTERVAL_MS = 30_000;

export function NotificationBell() {
  const [data, setData] = useState<NotificationListRead | null>(null);
  const [phase, setPhase] = useState<NotificationsFetchPhase>('loading');
  const [open, setOpen] = useState(false);

  // 请求序号:轮询刻度与"标记已读"后的重拉可能并发在途,先发后到的响应会覆盖后发先到的,
  // 失败分支还会清空整个列表。只有当前最新一次请求的响应允许写 state,成功与失败两个分支
  // 同守——与 RuntimeStatusBadge 逐行同形(冷审查裁定 C2 点名两处同批修)。
  const requestSeq = useRef(0);

  const refresh = useCallback(() => {
    let disposed = false;
    const seq = (requestSeq.current += 1);
    const isCurrent = (): boolean => !disposed && seq === requestSeq.current;

    notificationsApi
      .list()
      .then((next) => {
        if (isCurrent()) {
          setData(next);
          setPhase('ready');
        }
      })
      .catch(() => {
        if (isCurrent()) {
          setData(null);
          setPhase('error');
        }
      });

    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    const dispose = refresh();
    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);

    return () => {
      dispose();
      window.clearInterval(timer);
    };
  }, [refresh]);

  const vm = useMemo(() => buildNotificationsVM(data, phase), [data, phase]);

  const handleMarkRead = useCallback(
    (notificationId: string) => {
      notificationsApi
        .markRead(notificationId)
        .then(() => refresh())
        .catch(() => refresh());
    },
    [refresh],
  );

  const handleReadAll = useCallback(() => {
    notificationsApi
      .markAllRead()
      .then(() => refresh())
      .catch(() => refresh());
  }, [refresh]);

  return (
    <>
      <Tooltip title="通知">
        <Badge count={vm.unreadCount} size="small">
          <Button aria-label="通知" icon={<BellOutlined />} shape="circle" onClick={() => setOpen(true)} />
        </Badge>
      </Tooltip>

      <Drawer
        className="notification-panel"
        extra={
          <Button disabled={vm.unreadCount === 0} size="small" onClick={handleReadAll}>
            全部已读
          </Button>
        }
        open={open}
        title={`通知${vm.unreadCount > 0 ? `（${vm.unreadCount} 未读）` : ''}`}
        width={420}
        onClose={() => setOpen(false)}
      >
        {vm.items.length === 0 ? (
          <span className="notification-panel__muted">{vm.emptyText}</span>
        ) : (
          <ul className="notification-panel__list">
            {vm.items.map((item) => (
              <li
                className={
                  item.read ? 'notification-item notification-item--read' : 'notification-item'
                }
                key={item.id}
              >
                <div className="notification-item__head">
                  <span className="notification-item__title">
                    {!item.read && <span aria-label="未读" className="notification-item__dot" />}
                    {item.title}
                  </span>
                  <Tag>{item.kindLabel}</Tag>
                </div>
                <div className="notification-item__summary">{item.summary}</div>
                <div className="notification-item__meta">
                  <span className="notification-panel__muted">
                    <RelativeTime iso={item.updatedAt} />
                    {item.occurrencesText ? ` · ${item.occurrencesText}` : ''}
                  </span>
                  {!item.read && (
                    <Button size="small" type="link" onClick={() => handleMarkRead(item.id)}>
                      标记已读
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Drawer>
    </>
  );
}
