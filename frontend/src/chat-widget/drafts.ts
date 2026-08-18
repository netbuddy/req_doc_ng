/**
 * 统一 AI 对话控件 · 按会话草稿存取（工作包 01 篇 §6）。
 *
 * 草稿按 sessionKey 保存/恢复，内存级即可（不持久化——刷新即丢，属过渡态口径）。
 * 输入框有内容时发生会话切换：控件据本存储判断上一会话是否留有草稿，出提醒行。
 * 纯数据结构，无 React 依赖，便于单测（AC 草稿存取与切换提醒）。
 */
export class DraftStore {
  private readonly map = new Map<string, string>();

  /** 取草稿；无则空串。 */
  get(sessionKey: string): string {
    return this.map.get(sessionKey) ?? '';
  }

  /** 存草稿：空白（trim 后为空）等价删除，避免留下空草稿误报「有未发送内容」。 */
  set(sessionKey: string, value: string): void {
    if (value.trim().length > 0) {
      this.map.set(sessionKey, value);
    } else {
      this.map.delete(sessionKey);
    }
  }

  /** 清除某会话草稿（如发送后）。 */
  clear(sessionKey: string): void {
    this.map.delete(sessionKey);
  }

  /** 该会话是否留有非空草稿。 */
  has(sessionKey: string): boolean {
    return this.get(sessionKey).trim().length > 0;
  }

  /** 非空草稿去除首尾空白后的字数（提醒行「未发送的草稿（N 字）」用）。 */
  length(sessionKey: string): number {
    return this.get(sessionKey).trim().length;
  }
}
