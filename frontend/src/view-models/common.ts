export type BadgeTone = 'neutral' | 'processing' | 'success' | 'warning' | 'danger';

export interface ActionVM {
  key: string;
  label: string;
  iconKey?: string;
  variant?: 'default' | 'primary' | 'danger';
  disabled?: boolean;
  disabledReason?: string;
}

export interface MetricCardVM {
  key: string;
  title: string;
  value: string;
  helperText?: string;
  deltaText?: string;
  tone?: BadgeTone;
}

export interface ActivityItemVM {
  key: string;
  title: string;
  description: string;
  timeText: string;
  tone?: BadgeTone;
}

export interface StatusSummaryVM {
  key: string;
  label: string;
  value: string;
  tone?: BadgeTone;
}

export interface SidePanelSectionVM {
  key: string;
  title: string;
  items: StatusSummaryVM[];
  actionLabel?: string;
}

export function tagColorForTone(tone: BadgeTone): string {
  const colors: Record<BadgeTone, string> = {
    neutral: 'default',
    processing: 'processing',
    success: 'success',
    warning: 'warning',
    danger: 'error',
  };

  return colors[tone];
}
