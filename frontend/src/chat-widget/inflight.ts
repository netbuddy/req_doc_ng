/**
 * 统一 AI 对话控件 · 两级在途模型（工作包 01 篇 §5，取代页面级 busy）。
 *
 * - 动作级：键 `(sessionKey, actionInstanceId)`，单个卡片动作在途 → 只禁该按钮，不牵连其他按钮与输入区。
 * - 会话级：键 `sessionKey`，该会话有对话请求在途 → 当前会话显示「AI 正在回复本会话…」；
 *   切走后不显示（在途归属发送会话，不属当前显示会话——摸底 V3 语义），会话条以徽标呈现。
 *
 * 回执归属：响应落回**发送时**的会话线程，与当前显示会话解耦（send.settled 日志记 landed_on_current）。
 */
import { useCallback, useMemo, useState } from 'react';

/** 动作级在途键（sessionKey 与实例 id 用不会在两者内部出现的分隔符拼接）。 */
export function actionInflightKey(sessionKey: string, actionInstanceId: string): string {
  return `${sessionKey}\x00${actionInstanceId}`;
}

export interface InflightModel {
  /** 该会话是否有对话请求在途。 */
  hasSession: (sessionKey: string) => boolean;
  /** 有在途请求的全部会话键（会话条徽标用）。 */
  sessionsInflight: () => string[];
  /** 该动作实例是否在途。 */
  hasAction: (sessionKey: string, actionInstanceId: string) => boolean;
  markSession: (sessionKey: string) => void;
  clearSession: (sessionKey: string) => void;
  markAction: (sessionKey: string, actionInstanceId: string) => void;
  clearAction: (sessionKey: string, actionInstanceId: string) => void;
}

export function useInflight(): InflightModel {
  const [sessions, setSessions] = useState<ReadonlySet<string>>(() => new Set());
  const [actions, setActions] = useState<ReadonlySet<string>>(() => new Set());

  const markSession = useCallback((sessionKey: string) => {
    setSessions((prev) => {
      if (prev.has(sessionKey)) return prev;
      const next = new Set(prev);
      next.add(sessionKey);
      return next;
    });
  }, []);
  const clearSession = useCallback((sessionKey: string) => {
    setSessions((prev) => {
      if (!prev.has(sessionKey)) return prev;
      const next = new Set(prev);
      next.delete(sessionKey);
      return next;
    });
  }, []);
  const markAction = useCallback((sessionKey: string, actionInstanceId: string) => {
    const key = actionInflightKey(sessionKey, actionInstanceId);
    setActions((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  }, []);
  const clearAction = useCallback((sessionKey: string, actionInstanceId: string) => {
    const key = actionInflightKey(sessionKey, actionInstanceId);
    setActions((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
  }, []);

  return useMemo<InflightModel>(
    () => ({
      hasSession: (sessionKey) => sessions.has(sessionKey),
      sessionsInflight: () => [...sessions],
      hasAction: (sessionKey, actionInstanceId) => actions.has(actionInflightKey(sessionKey, actionInstanceId)),
      markSession,
      clearSession,
      markAction,
      clearAction,
    }),
    [sessions, actions, markSession, clearSession, markAction, clearAction],
  );
}
