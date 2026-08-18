/**
 * 统一 AI 对话控件 · 对外导出面（工作包 01 篇）。
 *
 * 消费方（各页面适配器）只从本入口引入控件与契约类型；控件核心零页面感知，
 * 页面差异一律经 ChatHostAdapter 注入。
 */
export { ChatWidget } from './ChatWidget';
export type { ChatWidgetProps } from './ChatWidget';
export { ThreadView } from './thread-view';
export { useActionDispatch } from './action-dispatch';
export type { ActionDispatchConfig, ActionDispatchController } from './action-dispatch';
export { useInflight, actionInflightKey } from './inflight';
export type { InflightModel } from './inflight';
export { DraftStore } from './drafts';
export { useDialogueTrace, ChatTraceRail } from './trace-rail';
export type { DialogueTraceController } from './trace-rail';
export {
  cwLog,
  hashSessionKey,
  logSessionSwitchTiming,
  logActionDispatch,
  logActionSettled,
  logSendSettled,
} from './log';
export {
  isTextPart,
  isMarkdownPart,
  isImagePart,
  isCardPart,
  isComponentPart,
} from './types';
export type {
  ChatMessage,
  MessageRole,
  MessageStatus,
  MessagePart,
  TextPart,
  MarkdownPart,
  ImagePart,
  CardPart,
  ComponentPart,
  UnknownPart,
  CardV1,
  ChatHostAdapter,
  HostCapabilities,
  QuickCommand,
  HostActionResult,
  HostActionHandler,
  QuoteProvider,
  QuoteBlock,
  ThreadEvent,
  ThreadSource,
  DialogueTransport,
  StreamHandlers,
  AbortHandle,
  ChatAction,
  SubmitAction,
  HostAction,
  UrlAction,
  ComponentAction,
  FollowupMode,
  ActionPhase,
  ChatActionApi,
  ComponentPartRenderProps,
} from './types';
