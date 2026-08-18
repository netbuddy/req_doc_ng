// 全站时间文案内核(唯一实现)。相对时间原为 runtime-status.ts 私产,
// 因 RelativeTime 组件与多处落点共用而迁入此处;勿在别处另写分档逻辑。
//
// 「现在」一律取服务器视角(serverNow),不取本机时钟:用户设备时钟快 8 分钟时,
// 服务端刚写下的时间戳按本机时钟算会一出生就显示「8 分钟前」(走查反馈第⑤组)。
// 绝对时刻的渲染不受影响——它渲染的是时间戳自身,与「现在」无关。
import { serverNow } from '../api/server-clock';

const MINUTE_SECONDS = 60;
/** 导出供单钟分级节流对齐同一语义边界（文案翻「小时前」档 ⟺ 刷新降为小时节奏），禁两处各写 3600。 */
export const HOUR_SECONDS = 3600;
const DAY_SECONDS = 86400;

/** 时刻不可解析时的占位(相对与绝对同口径) */
const INVALID_TEXT = '—';

/** iso 距 now 的秒数;不可解析返回 NaN。未来时刻按 0 计(不出现「-1 分钟前」)。 */
export function relativeAgeSeconds(iso: string, now: Date = serverNow()): number {
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) {
    return Number.NaN;
  }
  return Math.max(0, Math.floor((now.getTime() - time.getTime()) / 1000));
}

export function formatRelativeTime(iso: string, now: Date = serverNow()): string {
  const diffSeconds = relativeAgeSeconds(iso, now);
  if (Number.isNaN(diffSeconds)) {
    return INVALID_TEXT;
  }
  if (diffSeconds < MINUTE_SECONDS) {
    return '刚刚';
  }
  if (diffSeconds < HOUR_SECONDS) {
    return `${Math.floor(diffSeconds / MINUTE_SECONDS)} 分钟前`;
  }
  if (diffSeconds < DAY_SECONDS) {
    return `${Math.floor(diffSeconds / HOUR_SECONDS)} 小时前`;
  }
  return `${Math.floor(diffSeconds / DAY_SECONDS)} 天前`;
}

/** 绝对时刻内核(本地时区),秒精度/分钟精度/纯时分秒共用;勿在别处另写。 */
function formatAbsolute(
  iso: string | null | undefined,
  withSeconds: boolean,
  withDate = true,
): string {
  // 显式判空:new Date(null) 返回纪元而非 NaN,漏判会把空值渲染成 1970-01-01 08:00:00。
  if (!iso) {
    return INVALID_TEXT;
  }
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) {
    return INVALID_TEXT;
  }
  const pad = (value: number): string => String(value).padStart(2, '0');
  const date = `${time.getFullYear()}-${pad(time.getMonth() + 1)}-${pad(time.getDate())}`;
  let clock = `${pad(time.getHours())}:${pad(time.getMinutes())}`;
  if (withSeconds) {
    clock = `${clock}:${pad(time.getSeconds())}`;
  }
  return withDate ? `${date} ${clock}` : clock;
}

/** 绝对时刻文案(本地时区完整时刻),全站唯一定义:相对时间标签的悬停原值与各页时刻落点走此处。 */
export function formatAbsoluteTime(iso: string | null | undefined): string {
  return formatAbsolute(iso, true);
}

/** 分钟精度变体(本地时区):落款、列表元信息等不需秒的场合。 */
export function formatAbsoluteMinute(iso: string | null | undefined): string {
  return formatAbsolute(iso, false);
}

/** 时分秒变体(本地时区,不带日期):今天之内的紧凑落点,如运行态面板的「数据截至」。
 * 调用方勿改回切分 formatAbsoluteTime 的输出——那把「输出恰好两段、单空格分隔」变成隐含契约。 */
export function formatClockTime(iso: string | null | undefined): string {
  return formatAbsolute(iso, true, false);
}
