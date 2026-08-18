/**
 * 服务器时间基准（走查反馈第⑤组）。
 *
 * 要解决的问题：全站「几分钟前」都是拿本机时钟当「现在」算的，用户设备快 8 分钟时，
 * 服务端刚写下的时间戳在他的设备上一出生就显示成「8 分钟前」。
 *
 * 做法：记住「服务器现在 − 本机现在」这个差值，凡是要用「现在」的地方都用修正过的。
 * 本模块自己不发任何请求——差值由 api 层（client.ts）在每次收到响应时喂进来，
 * 组件与 ViewModel 只读不写，MVVM 边界不破。
 */

/** 服务器时刻减本机时刻，毫秒。正数＝本机慢，负数＝本机快。 */
let offsetMs = 0;

/**
 * 小于这个幅度的偏差按零处理。
 * `Date` 响应头只有秒精度，还叠着一次网络往返的耗时；而相对文案最细的一档是「分钟」。
 * 去修正几秒的抖动没有任何可见收益，只会让文案在边界上来回跳。
 */
export const SKEW_THRESHOLD_MS = 30_000;

type OffsetListener = (offsetMs: number) => void;
const listeners = new Set<OffsetListener>();

/**
 * 偏移量变化时通知（相对时间标签据此立刻重算，不必等下一个刷新节拍）。
 * 返回退订函数；订阅/退订严格对称。
 */
export function subscribeServerClockOffset(listener: OffsetListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 由 `Date` 响应头计算并记录偏移量。头缺失或不可解析时保持原值——没有观测就不改判。 */
export function observeServerDate(header: string | null | undefined): void {
  if (!header) {
    return;
  }
  const serverMs = Date.parse(header);
  if (Number.isNaN(serverMs)) {
    return;
  }
  // 收到响应的这一刻取本机时钟，与响应头声明的服务器时刻比。
  const raw = serverMs - Date.now();
  const next = Math.abs(raw) < SKEW_THRESHOLD_MS ? 0 : raw;
  if (next === offsetMs) {
    return;
  }
  offsetMs = next;
  listeners.forEach((listener) => listener(next));
}

/** 当前偏移量（毫秒）。供测试与诊断读取。 */
export function serverClockOffsetMs(): number {
  return offsetMs;
}

/** 服务器视角的「现在」。全站算相对时间、给本地乐观消息打时间戳都用它。 */
export function serverNow(): Date {
  return new Date(Date.now() + offsetMs);
}

/** 服务器视角「现在」的 ISO 串（本地发消息打时间戳用）。 */
export function serverNowIso(): string {
  return serverNow().toISOString();
}

/** 仅供测试：清空偏移量与订阅者，避免用例间互相串味。 */
export function resetServerClockForTest(): void {
  offsetMs = 0;
  listeners.clear();
}
