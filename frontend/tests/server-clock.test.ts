import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  observeServerDate,
  resetServerClockForTest,
  serverClockOffsetMs,
  serverNow,
  SKEW_THRESHOLD_MS,
  subscribeServerClockOffset,
} from '../src/api/server-clock';
import { formatRelativeTime } from '../src/view-models/time';

// 走查反馈第⑤组：设备时钟与服务器差得远时，相对文案必须按服务器基准算。
// 本机时钟在用例里钉死，服务器时刻由 Date 响应头模拟。

const LOCAL_NOW = new Date('2026-07-20T12:00:00.000Z').getTime();

/** 造一个 Date 响应头：服务器比本机快 skewMs（负数＝服务器慢，即本机快）。 */
const dateHeader = (skewMs: number): string => new Date(LOCAL_NOW + skewMs).toUTCString();

describe('服务器时钟偏移量', () => {
  beforeEach(() => {
    resetServerClockForTest();
    vi.useFakeTimers();
    vi.setSystemTime(LOCAL_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    resetServerClockForTest();
  });

  it('零偏移：服务器与本机一致时不做任何修正', () => {
    observeServerDate(dateHeader(0));
    expect(serverClockOffsetMs()).toBe(0);
    expect(serverNow().getTime()).toBe(LOCAL_NOW);
  });

  it('本机快 8 分钟（实证场景）：偏移为负，服务器视角的现在被拨回 8 分钟', () => {
    const eightMinutes = 8 * 60_000;
    observeServerDate(dateHeader(-eightMinutes));
    expect(serverClockOffsetMs()).toBe(-eightMinutes);
    expect(serverNow().getTime()).toBe(LOCAL_NOW - eightMinutes);
  });

  it('本机慢 5 分钟：偏移为正，服务器视角的现在被拨快 5 分钟', () => {
    const fiveMinutes = 5 * 60_000;
    observeServerDate(dateHeader(fiveMinutes));
    expect(serverClockOffsetMs()).toBe(fiveMinutes);
    expect(serverNow().getTime()).toBe(LOCAL_NOW + fiveMinutes);
  });

  it('门槛以内的抖动按零处理：Date 头只有秒精度且叠网络往返，修它只会让文案跳动', () => {
    observeServerDate(dateHeader(SKEW_THRESHOLD_MS - 1_000));
    expect(serverClockOffsetMs()).toBe(0);
    observeServerDate(dateHeader(-(SKEW_THRESHOLD_MS - 1_000)));
    expect(serverClockOffsetMs()).toBe(0);
  });

  it('门槛边界：恰好等于门槛即采纳，不再按零处理', () => {
    observeServerDate(dateHeader(SKEW_THRESHOLD_MS));
    expect(serverClockOffsetMs()).toBe(SKEW_THRESHOLD_MS);
  });

  it('每次响应都重算：用户中途对时后偏移量随即归零', () => {
    observeServerDate(dateHeader(-8 * 60_000));
    expect(serverClockOffsetMs()).toBe(-8 * 60_000);
    observeServerDate(dateHeader(0));
    expect(serverClockOffsetMs()).toBe(0);
  });

  it('头缺失或不可解析时保持原值：没有观测就不改判', () => {
    observeServerDate(dateHeader(-8 * 60_000));
    observeServerDate(null);
    observeServerDate(undefined);
    observeServerDate('');
    observeServerDate('not a date');
    expect(serverClockOffsetMs()).toBe(-8 * 60_000);
  });

  it('偏移量变化才通知订阅者；同值不重复通知，退订后不再收到', () => {
    const seen: number[] = [];
    const unsubscribe = subscribeServerClockOffset((offset) => seen.push(offset));
    observeServerDate(dateHeader(-8 * 60_000));
    observeServerDate(dateHeader(-8 * 60_000)); // 同值：不应再通知
    expect(seen).toEqual([-8 * 60_000]);
    unsubscribe();
    observeServerDate(dateHeader(0));
    expect(seen).toEqual([-8 * 60_000]);
  });
});

describe('相对文案按服务器基准计算（第⑤组的用户可见效果）', () => {
  beforeEach(() => {
    resetServerClockForTest();
    vi.useFakeTimers();
    vi.setSystemTime(LOCAL_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    resetServerClockForTest();
  });

  it('本机快 8 分钟时，服务端刚写下的时间戳修正前显示「8 分钟前」、修正后显示「刚刚」', () => {
    // 服务端此刻的真实时刻＝本机时钟往回拨 8 分钟。
    const serverIso = new Date(LOCAL_NOW - 8 * 60_000).toISOString();
    // 修正前（偏移量尚未测出）：拿本机时钟当现在，一出生就是 8 分钟前——这正是用户报障的现象。
    expect(formatRelativeTime(serverIso)).toBe('8 分钟前');
    // 收到一次响应，偏移量测出后：同一条时间戳显示「刚刚」。
    observeServerDate(dateHeader(-8 * 60_000));
    expect(formatRelativeTime(serverIso)).toBe('刚刚');
  });

  it('本机慢 8 分钟时，本地按服务器视角打的时间戳仍显示「刚刚」，不会一出生就是 8 分钟前', () => {
    observeServerDate(dateHeader(8 * 60_000));
    // 乐观消息用 serverNow 打戳，与渲染时的「现在」同基准。
    const optimisticIso = serverNow().toISOString();
    expect(formatRelativeTime(optimisticIso)).toBe('刚刚');
  });
});
