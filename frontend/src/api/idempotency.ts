/**
 * 幂等键（idempotency key，同一键重复提交不重复生效的防重标识）生成器。
 * 2026-08-08 随刀清理：原先五个文件各自复制同一函数，收拢为唯一实现
 * （热点档案第 5 节销账项；坏味道＝重复代码）。
 */
export function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `op-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
