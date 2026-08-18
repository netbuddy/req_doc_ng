import { useEffect, useState } from 'react';
import { subscribeServerClockOffset } from '../api/server-clock';
import { formatAbsoluteTime, formatRelativeTime, HOUR_SECONDS, relativeAgeSeconds } from '../view-models/time';

// 相对时间标签(全站唯一呈现形态):默认显相对文案,悬停见绝对时刻。
// 相对文案会过期,故须定时重算——但全站只挂一个 interval(单钟),
// 由订阅集分发;禁每实例各挂 interval(实例可达数百,会轰击渲染)。

/** 单钟基础节拍=最细档节奏;各订阅者按自身档位在此之上分级节流。
 * 隐含契约:每个 cadence 都必须是 BASE_TICK_MS 的整倍数,否则节流判据静默失准(合并裁定 F2 附带约束)。 */
export const BASE_TICK_MS = 30_000;
/** 分钟内(文案每分钟会变)的刷新节奏=基础节拍(有意等式:基础节拍就是最细档节奏) */
export const MINUTE_CADENCE_MS = BASE_TICK_MS;
/** 小时级及以上(文案至少一小时才变)的刷新节奏 */
export const HOUR_CADENCE_MS = 60_000;

/** 按时刻老化选刷新档位:翻「小时前」档 ⟺ 降为小时节奏(同一 HOUR_SECONDS 边界);不可解析按小时档。 */
export function relativeCadenceMs(iso: string): number {
  const age = relativeAgeSeconds(iso);
  return Number.isNaN(age) || age >= HOUR_SECONDS ? HOUR_CADENCE_MS : MINUTE_CADENCE_MS;
}

interface ClockSubscriber {
  /** 本实例期望的刷新间隔(随时刻老化分级,故为函数而非定值) */
  cadenceMs: () => number;
  run: () => void;
  lastRun: number;
}

const subscribers = new Set<ClockSubscriber>();
let timer: ReturnType<typeof setInterval> | null = null;

function tick(): void {
  const now = Date.now();
  subscribers.forEach((subscriber) => {
    // 判据留半拍容差:tick 由单调钟(setInterval)驱动而此处用墙钟度量,零余量时
    // NTP 缓变/回拨会让每拍差之毫厘被跳过,节奏静默翻倍(合并裁定 F8);
    // cadence 皆为整倍拍,容差半拍不会多刷。
    if (now - subscriber.lastRun >= subscriber.cadenceMs() - BASE_TICK_MS / 2) {
      subscriber.lastRun = now;
      subscriber.run();
    }
  });
}

/**
 * 订阅单钟,返回退订函数。订阅/退订严格对称:末位退订即停表,
 * 故 StrictMode 的 effect 双调用(挂载→卸载→挂载)不会遗留定时器。
 */
export function subscribeRelativeTimeClock(cadenceMs: () => number, run: () => void): () => void {
  // lastRun 播种 0 而非 Date.now():墙钟播种会让带相位加入的订阅者首拍被跳过、
  // 首刷最长拖满两倍档期(合并裁定 F2);播种 0 令订阅后首拍即刷并 snap 上共享栅格,
  // 多出的一次是同值 setText,React 自行短路。
  const subscriber: ClockSubscriber = { cadenceMs, run, lastRun: 0 };
  subscribers.add(subscriber);
  if (timer === null) {
    timer = setInterval(tick, BASE_TICK_MS);
  }
  return () => {
    subscribers.delete(subscriber);
    if (subscribers.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export interface RelativeTimeProps {
  /** ISO 时刻;不可解析时渲染占位 */
  iso: string;
  className?: string;
  /** 紧随相对文案的后缀(如「更新」),同处一个节点内以免拼接处断行 */
  suffix?: string;
}

export function RelativeTime({ iso, className, suffix }: RelativeTimeProps) {
  const [text, setText] = useState(() => formatRelativeTime(iso));

  useEffect(() => {
    // iso 变化时立刻对齐,否则会沿用上一时刻的文案直到下一次节拍。
    setText(formatRelativeTime(iso));
    // 文案未变时 setState 同值,React 自会短路,不产生实际重渲染。
    const unsubscribeClock = subscribeRelativeTimeClock(
      () => relativeCadenceMs(iso),
      () => setText(formatRelativeTime(iso)),
    );
    // 服务器时钟偏移量首次测出或变化时立刻重算:偏移量一变,已经渲染出来的文案就全错了,
    // 不能等下一个节拍(最长 30 秒)才纠正。
    const unsubscribeOffset = subscribeServerClockOffset(() => setText(formatRelativeTime(iso)));
    return () => {
      unsubscribeClock();
      unsubscribeOffset();
    };
  }, [iso]);

  // 不可解析(含 loading/error 下的空串)时省略两属性:datetime="" 不合规,
  // title="—" 是无意义 tooltip(合并裁定 F6);可见文案仍与占位口径一致。
  const valid = !Number.isNaN(relativeAgeSeconds(iso));
  return (
    <time className={className} dateTime={valid ? iso : undefined} title={valid ? formatAbsoluteTime(iso) : undefined}>
      {text}
      {suffix}
    </time>
  );
}
