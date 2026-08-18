import { StrictMode } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RelativeTime, relativeCadenceMs, subscribeRelativeTimeClock } from '../src/ui/RelativeTime';
import { formatAbsoluteMinute, formatAbsoluteTime, formatRelativeTime } from '../src/view-models/time';

const NOW = new Date('2026-07-04T12:00:00Z');

describe('formatRelativeTime', () => {
  it('分档:刚刚/分钟/小时/天/非法值', () => {
    expect(formatRelativeTime('2026-07-04T11:59:40Z', NOW)).toBe('刚刚');
    expect(formatRelativeTime('2026-07-04T11:45:00Z', NOW)).toBe('15 分钟前');
    expect(formatRelativeTime('2026-07-04T06:00:00Z', NOW)).toBe('6 小时前');
    expect(formatRelativeTime('2026-07-01T12:00:00Z', NOW)).toBe('3 天前');
    expect(formatRelativeTime('not-a-date', NOW)).toBe('—');
  });

  it('未来时刻按「刚刚」计,不出现负数文案', () => {
    expect(formatRelativeTime('2026-07-04T12:05:00Z', NOW)).toBe('刚刚');
  });
});

describe('formatAbsoluteTime', () => {
  it('渲染本地时区完整时刻（TZ 由 vite.config 钉 Asia/Shanghai，字面量断言防同算法重言式）', () => {
    // 合并裁定 F4：此前用与实现同一套 getFullYear/pad 回算 expected，在 UTC 机上
    // 与 slice(0,19) 手法不可区分（断言空转）。字面量 + 非 UTC 钉死时区后，
    // UTC 原串冒充本地时刻（+8h 偏移、跨日）在此必挂。
    expect(formatAbsoluteTime('2026-07-04T12:00:00Z')).toBe('2026-07-04 20:00:00');
    expect(formatAbsoluteTime('2026-07-04T17:30:00+00:00')).toBe('2026-07-05 01:30:00'); // 跨日
  });

  it('非法值与相对文案同口径占位', () => {
    expect(formatAbsoluteTime('not-a-date')).toBe('—');
  });

  it('空值占位:null/undefined/空串均不得渲染纪元（new Date(null) 落 1970 而非 NaN）', () => {
    expect(formatAbsoluteTime(null)).toBe('—');
    expect(formatAbsoluteTime(undefined)).toBe('—');
    expect(formatAbsoluteTime('')).toBe('—');
  });
});

describe('formatAbsoluteMinute', () => {
  it('分钟精度落本地时区（issue #21 实证样本:UTC 原串冒充本地时刻在此必挂）', () => {
    // wire 06:48:19Z ⇒ 所钉 +8 下为 14:48（slice 手法会给出 UTC 原串 06:48）。
    expect(formatAbsoluteMinute('2026-07-10T06:48:19.687564+00:00')).toBe('2026-07-10 14:48');
    expect(formatAbsoluteMinute('2026-07-04T17:30:00+00:00')).toBe('2026-07-05 01:30'); // 跨日
  });

  it('与秒精度同一内核:非法值/空值同口径占位', () => {
    expect(formatAbsoluteMinute('not-a-date')).toBe('—');
    expect(formatAbsoluteMinute(null)).toBe('—');
  });
});

describe('RelativeTime 组件', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: NOW });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('默认显相对文案,悬停(title)见绝对时刻', () => {
    render(<RelativeTime iso="2026-07-04T11:45:00Z" />);

    const node = screen.getByText('15 分钟前');
    expect(node).toHaveAttribute('title', formatAbsoluteTime('2026-07-04T11:45:00Z'));
    expect(node).toHaveAttribute('datetime', '2026-07-04T11:45:00Z');
  });

  it('suffix 与相对文案同处一个节点', () => {
    render(<RelativeTime iso="2026-07-04T11:59:40Z" suffix="更新" />);

    expect(screen.getByText('刚刚更新')).toBeInTheDocument();
  });

  it('单钟到点后文案自动刷新(不必重挂组件)', async () => {
    render(<RelativeTime iso="2026-07-04T11:59:40Z" />);
    expect(screen.getByText('刚刚')).toBeInTheDocument();

    // 推进到该时刻满 1 分钟之后,单钟节拍应把文案带过分档线
    // (节拍在 act 外触发 setState,React 19 只排队不冲刷,故须包裹)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(65_000);
    });

    expect(screen.getByText('1 分钟前')).toBeInTheDocument();
  });

  it('全站单钟:多实例共用一个 interval', () => {
    expect(vi.getTimerCount()).toBe(0);

    render(
      <>
        <RelativeTime iso="2026-07-04T11:45:00Z" />
        <RelativeTime iso="2026-07-04T10:00:00Z" />
        <RelativeTime iso="2026-07-01T12:00:00Z" />
      </>,
    );

    expect(vi.getTimerCount()).toBe(1);
  });

  it('卸载清理:末位实例走后停表,不留定时器', () => {
    const view = render(<RelativeTime iso="2026-07-04T11:45:00Z" />);
    expect(vi.getTimerCount()).toBe(1);

    view.unmount();

    expect(vi.getTimerCount()).toBe(0);
  });

  it('非末位退订对称(F1):卸载其一,幸存实例仍随钟走(clear 式全清在此必挂)', async () => {
    const first = render(<RelativeTime iso="2026-07-04T11:59:40Z" />);
    render(<RelativeTime iso="2026-07-04T11:59:41Z" />);

    first.unmount(); // 退订其一——只允许 delete 本订阅者,不得清空全表

    await act(async () => {
      await vi.advanceTimersByTimeAsync(65_000);
    });
    expect(screen.getByText('1 分钟前')).toBeInTheDocument(); // 幸存实例过档
    expect(vi.getTimerCount()).toBe(1); // 仍有订阅者,钟不得停
  });

  it('相位偏移订阅者首拍即刷(F2):不再最长拖满两倍档期', async () => {
    render(<RelativeTime iso="2026-07-04T12:00:00Z" />); // t=0 起钟,共享拍在 30s 栅格
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    render(<RelativeTime iso="2026-07-04T11:59:20Z" />); // t=10s 带相位加入,时龄 50s → 刚刚
    expect(screen.getAllByText('刚刚')).toHaveLength(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000); // t=30s:加入后首个共享拍
    });
    // 时龄 70s,首拍即应过档;墙钟播种的旧实现在此拍被跳过(20s<30s),文案要拖到 t=60s
    expect(screen.getByText('1 分钟前')).toBeInTheDocument();
  });

  it('StrictMode 双调用不泄漏定时器', () => {
    const view = render(
      <StrictMode>
        <RelativeTime iso="2026-07-04T11:45:00Z" />
      </StrictMode>,
    );

    // effect 被挂载→卸载→挂载三段执行,订阅/退订对称则表只剩一只
    expect(vi.getTimerCount()).toBe(1);

    view.unmount();

    expect(vi.getTimerCount()).toBe(0);
  });

  it('iso 变化立即对齐文案,不等下一次节拍', () => {
    const view = render(<RelativeTime iso="2026-07-04T11:45:00Z" />);
    expect(screen.getByText('15 分钟前')).toBeInTheDocument();

    view.rerender(<RelativeTime iso="2026-07-04T06:00:00Z" />);

    expect(screen.getByText('6 小时前')).toBeInTheDocument();
  });

  it('不可解析时省略 datetime/title(F6):不渲染 datetime="" 与「—」tooltip', () => {
    render(<RelativeTime iso="" suffix="更新" />);

    const node = screen.getByText('—更新');
    expect(node).not.toHaveAttribute('datetime');
    expect(node).not.toHaveAttribute('title');
  });
});

describe('单钟分级节流(F3:直测 subscribeRelativeTimeClock,不经文案)', () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: NOW });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('30s 档:90s 内恰 3 拍(钉死基础节拍=30s)', async () => {
    const run = vi.fn();
    const off = subscribeRelativeTimeClock(() => 30_000, run);

    await vi.advanceTimersByTimeAsync(90_000);
    expect(run).toHaveBeenCalledTimes(3); // 基础节拍若被放宽到 60s,此处只剩 1

    off();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('60s 档:120s 内恰 2 拍(钉死节流真在工作,非每拍必刷)', async () => {
    const run = vi.fn();
    const off = subscribeRelativeTimeClock(() => 60_000, run);

    await vi.advanceTimersByTimeAsync(120_000);
    // 播种 0 → 首拍(30s)即刷并 snap,此后 90s 处再刷一次;判据若被删成每拍必刷则为 4
    expect(run).toHaveBeenCalledTimes(2);

    off();
  });

  it('档位选择字面量(钉死常量与三元方向):分钟内 30s、小时级/不可解析 60s', () => {
    expect(relativeCadenceMs('2026-07-04T11:59:40Z')).toBe(30_000); // 时龄 20s
    expect(relativeCadenceMs('2026-07-04T10:00:00Z')).toBe(60_000); // 时龄 2h
    expect(relativeCadenceMs('not-a-date')).toBe(60_000);
  });
});
