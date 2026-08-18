/**
 * 统一 AI 对话控件 · 动作分发器与动作状态机（工作包 01 篇 §4）。
 *
 * 动作四型（§4.1）：
 *  - submit：继续下一轮对话——收集卡内 input.* 值＋预埋 data，经 transport 发回当前会话（走注入的 onSubmit）。
 *  - host  ：向页面发消息——查 adapter.actions 注册表按 name 调用；未注册→按钮渲染禁用态＋提示，不报错。
 *  - url   ：导航/外链——控件直接处理（走注入的 onUrl）。
 *  - component：拉起复杂交互——转 component 分部逃生舱（走注入的 onComponent）。
 *
 * 状态机（§4.2，单个动作实例）：
 *   idle ─点击→ dispatching ─成功→ settled-ok ─声明 followup→ awaiting-followup ─后续轮到达→ linked
 *                    └─失败→ settled-error（按钮恢复可点，错误行入线程）
 *  - dispatching 期间对同实例的再次触发被单飞守卫忽略（防双击的动作级在途）。
 *  - pending-followup：成功后停在 awaiting-followup（呈现「已受理·后续中」类标记），后续轮由 markLinked 推进。
 *  - 收束禁本地乐观（README §3 条 3）：本机只管理 dispatching 瞬时相位，已采纳/已收束视觉态由投影数据派生。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { logActionDispatch, logActionSettled } from './log';
import type {
  ActionPhase,
  ChatAction,
  ChatActionApi,
  ChatHostAdapter,
  SubmitAction,
  UrlAction,
} from './types';

export interface ActionDispatchConfig {
  adapter: ChatHostAdapter;
  /** submit 出口：拼命令并经 transport 发回当前会话，解析为 ok 布尔（会话级在途/回执条由 send 侧管理）。 */
  onSubmit: (action: SubmitAction) => Promise<boolean>;
  /** url 出口：应用内路由跳转或新开页。 */
  onUrl: (action: UrlAction) => void;
  /** component 出口：拉起逃生舱交互（不改动作相位）。 */
  onComponent: (name: string, props: Record<string, unknown>) => void;
  /** 错误行入线程（host/submit 失败时）：交由控件追加一条可见错误提示。 */
  notifyError: (text: string) => void;
  /** 敏感动作确认（默认 window.confirm）；测试可注入。 */
  confirmAction?: (message: string) => boolean;
}

export interface ActionDispatchController extends ChatActionApi {
  /** 后续轮到达：把某 awaiting-followup 实例推进到 linked（线程内衔接呈现新一轮）。 */
  markLinked: (instanceId: string) => void;
  /** 重置某实例相位（换会话/线程重投影后清理）。 */
  reset: (instanceId: string) => void;
}

export function useActionDispatch(config: ActionDispatchConfig): ActionDispatchController {
  const { adapter, onSubmit, onUrl, onComponent, notifyError } = config;
  const confirmAction = config.confirmAction ?? ((message: string) => window.confirm(message));

  const [phases, setPhases] = useState<Record<string, ActionPhase>>({});
  const phasesRef = useRef(phases);
  phasesRef.current = phases;
  // 同步在途集：单飞守卫不能只读 React 相位（同一事件内多次点击时相位尚未提交），故用 ref 同步记账。
  const inFlightRef = useRef<Set<string>>(new Set());
  const disposedRef = useRef(false);
  useEffect(() => {
    disposedRef.current = false;
    return () => {
      disposedRef.current = true;
    };
  }, []);

  const setPhase = useCallback((instanceId: string, phase: ActionPhase) => {
    if (disposedRef.current) return;
    setPhases((prev) => (prev[instanceId] === phase ? prev : { ...prev, [instanceId]: phase }));
  }, []);

  const dispatch = useCallback(
    (action: ChatAction, instanceId: string) => {
      // 单飞守卫：dispatching 期间忽略同实例再次触发（防双击）；读同步在途集，不读尚未提交的相位。
      if (inFlightRef.current.has(instanceId)) return;

      const sessionKey = adapter.sessionKey();
      const followup = 'followup' in action && action.followup === 'pending-followup' ? 'pending-followup' : 'done';

      if (action.kind === 'url') {
        logActionDispatch({ kind: 'url', name: null, sessionKey });
        onUrl(action);
        setPhase(instanceId, 'settled-ok');
        logActionSettled({ ok: true, durationMs: 0, followup: 'done' });
        return;
      }

      if (action.kind === 'component') {
        // 逃生舱只负责拉起交互，不进入 dispatch 状态机（不占用动作级在途）。
        logActionDispatch({ kind: 'component', name: action.name, sessionKey });
        onComponent(action.name, action.props ?? {});
        return;
      }

      if (action.kind === 'host' && !adapter.actions?.[action.name]) {
        // 防御：未注册动作名本应渲染为禁用按钮（isActionRegistered）；万一被触发，降级不报错。
        logActionSettled({ ok: false, durationMs: 0, followup: 'done' });
        notifyError(`动作「${action.label}」在当前页面不可用。`);
        return;
      }

      if (action.confirm && !confirmAction(action.confirm)) return;

      inFlightRef.current.add(instanceId);
      setPhase(instanceId, 'dispatching');
      logActionDispatch({
        kind: action.kind,
        name: action.kind === 'host' ? action.name : null,
        sessionKey,
      });
      const startedAt = performance.now();

      const settle = (ok: boolean, failText?: string) => {
        inFlightRef.current.delete(instanceId);
        const durationMs = performance.now() - startedAt;
        logActionSettled({ ok, durationMs, followup });
        if (!ok) {
          setPhase(instanceId, 'settled-error');
          if (failText) notifyError(failText);
          return;
        }
        setPhase(instanceId, followup === 'pending-followup' ? 'awaiting-followup' : 'settled-ok');
      };

      const run =
        action.kind === 'host'
          ? adapter
              .actions![action.name](action.payload ?? {})
              .then((result) => settle(result.ok, result.ok ? undefined : result.message ?? `动作「${action.label}」未成功。`))
          : onSubmit(action).then((ok) => settle(ok, ok ? undefined : '发送未成功，请重试。'));

      run.catch((error: unknown) => {
        settle(false, error instanceof Error && error.message ? error.message : '动作执行出错，请重试。');
      });
    },
    [adapter, confirmAction, notifyError, onComponent, onSubmit, onUrl, setPhase],
  );

  const phaseOf = useCallback((instanceId: string): ActionPhase => phasesRef.current[instanceId] ?? 'idle', []);
  const isActionRegistered = useCallback((name: string) => !!adapter.actions?.[name], [adapter]);
  const markLinked = useCallback(
    (instanceId: string) => {
      if (phasesRef.current[instanceId] === 'awaiting-followup') setPhase(instanceId, 'linked');
    },
    [setPhase],
  );
  const reset = useCallback((instanceId: string) => setPhase(instanceId, 'idle'), [setPhase]);

  // phaseOf 读 ref 恒稳，但按钮需随 phases 变化重渲——把 phases 纳入依赖使返回对象换新。
  return useMemo<ActionDispatchController>(
    () => ({ dispatch, phaseOf, isActionRegistered, markLinked, reset }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dispatch, isActionRegistered, markLinked, reset, phases],
  );
}
