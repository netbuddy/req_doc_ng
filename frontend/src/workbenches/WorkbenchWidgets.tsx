import { Button } from 'antd';
import type { ReactNode } from 'react';
import { renderActionIcon } from '../ui/icons';
import type {
  ActionVM,
  ActivityItemVM,
  BadgeTone,
  MetricCardVM,
  SidePanelSectionVM,
  StatusSummaryVM,
} from '../view-models/common';

export function StatusPill({ tone = 'neutral', children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`status-pill status-pill--${tone}`}>{children}</span>;
}

export function MetricCards({ items, compact = false }: { items: MetricCardVM[]; compact?: boolean }) {
  return (
    <div className={compact ? 'metric-grid metric-grid--compact' : 'metric-grid'}>
      {items.map((item) => (
        <div className={`metric-card metric-card--${item.tone ?? 'neutral'}`} key={item.key}>
          <div className="metric-card__label">{item.title}</div>
          <div className="metric-card__value-row">
            <strong>{item.value}</strong>
            {item.deltaText ? <span>{item.deltaText}</span> : null}
          </div>
          {item.helperText ? <p>{item.helperText}</p> : null}
        </div>
      ))}
    </div>
  );
}

export function StatusList({ items }: { items: StatusSummaryVM[] }) {
  return (
    <div className="status-list">
      {items.map((item) => (
        <div className="status-list__row" key={item.key}>
          <span>{item.label}</span>
          <strong className={item.tone ? `text-tone text-tone--${item.tone}` : undefined}>{item.value}</strong>
        </div>
      ))}
    </div>
  );
}

export function ActionButton({
  action,
  block = false,
  onClick,
  primary = false,
}: {
  action: ActionVM;
  block?: boolean;
  onClick?: () => void;
  primary?: boolean;
}) {
  return (
    <Button
      block={block}
      className="icon-action-button"
      danger={action.variant === 'danger'}
      disabled={action.disabled}
      icon={renderActionIcon(action.iconKey)}
      onClick={onClick}
      title={action.disabledReason}
      type={primary || action.variant === 'primary' ? 'primary' : 'default'}
    >
      {action.label}
    </Button>
  );
}

export function SidePanel({
  sections,
  activities,
  childrenBefore,
}: {
  sections: SidePanelSectionVM[];
  activities?: ActivityItemVM[];
  childrenBefore?: ReactNode;
}) {
  return (
    <aside className="right-stack">
      {childrenBefore}

      {sections.map((section) => (
        <section className="panel panel--side" key={section.key}>
          <div className="panel__header panel__header--tight">
            <h2 className="panel__title">{section.title}</h2>
            {section.actionLabel ? (
              <button className="text-command" type="button">
                {section.actionLabel}
              </button>
            ) : null}
          </div>
          <div className="panel__body">
            <StatusList items={section.items} />
          </div>
        </section>
      ))}

      {activities?.length ? (
        <section className="panel panel--side">
          <div className="panel__header panel__header--tight">
            <h2 className="panel__title">最近活动</h2>
            <button className="text-command" type="button">全部</button>
          </div>
          <div className="panel__body">
            <div className="activity-list">
              {activities.map((item) => (
                <div className="activity-list__item" key={item.key}>
                  <span className={`activity-dot activity-dot--${item.tone ?? 'neutral'}`} />
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.description}</p>
                    <time>{item.timeText}</time>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      ) : null}
    </aside>
  );
}
