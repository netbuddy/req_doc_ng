/**
 * 统一 AI 对话控件 · 契约类型单一来源（工作包 01 篇 §2–§4）。
 *
 * 本文件是消息信封、内容分部、页面接入契约 HostAdapter、传输绑定、线程源、动作模型的
 * 唯一类型定义（README §3 条 2：控件核心零页面感知——任何页面名/页面接口/页面特判都不得
 * 出现在本目录）。页面差异一律经 HostAdapter 注入表达。
 *
 * 完整 CardV1 卡片 schema 属 P5（03 篇 §1 权威 JSON Schema）；本卡（P1）后端不出 card 分部，
 * 控件对 card 分部只做 fallback 文本兜底渲染，故此处 CardV1 只声明兜底所需最小面。
 */
import type { ComponentType } from 'react';

// ======================= §2.1 消息信封 =======================

export type MessageRole = 'user' | 'assistant' | 'system';

/** pending=已发送未受理；streaming=SSE 增量中；settled=终态；failed=终态失败（可见错误行，不静默消失）。 */
export type MessageStatus = 'pending' | 'streaming' | 'settled' | 'failed';

export interface ChatMessage {
  /** 服务端行＝服务端消息 id；本地乐观行＝`local-<自增>` 前缀，settle 后整行替换（不原地改 id）。 */
  id: string;
  role: MessageRole;
  /** ISO 时间戳：服务端行＝落库时间；本地行＝发送瞬间。 */
  at: string;
  status: MessageStatus;
  /** 渲染顺序＝数组顺序；空数组合法（骨架行）。 */
  parts: MessagePart[];
  /** 关联 AgentRun／stage 帧序列，回执条取数键。 */
  traceRef?: string;
}

// ======================= §2.2 内容分部（本期五型，type 开放命名空间） =======================

export interface TextPart {
  type: 'text';
  text: string;
}

export interface MarkdownPart {
  type: 'markdown';
  text: string;
}

export interface ImagePart {
  type: 'image';
  src: string;
  alt?: string;
}

export interface CardPart {
  type: 'card';
  card: CardV1;
}

/** 逃生舱：查页面 capabilities.customCards 注册表渲染对应 React 组件。 */
export interface ComponentPart {
  type: 'component';
  name: string;
  props?: Record<string, unknown>;
}

/**
 * 未知分部：type 落在已知五型之外（语音/视频等未来类型）。运行时可能到达，
 * 控件按 §2.2 统一降级（折叠计数），绝不抛错、绝不阻断整条消息（00 篇裁定 4）。
 */
export interface UnknownPart {
  type: string;
  [key: string]: unknown;
}

export type KnownMessagePart = TextPart | MarkdownPart | ImagePart | CardPart | ComponentPart;

export type MessagePart = KnownMessagePart | UnknownPart;

/**
 * 卡片分部载荷。完整字段集属 P5（03 篇）；P1 期后端不出 card 分部，控件只用 fallback 文本兜底，
 * 故此处只固定 `card` 版本判别与 `fallback`，其余元素/动作字段留给 P5 的权威 schema 定义。
 */
export interface CardV1 {
  card: 'v1';
  /** 全卡文本兜底（未知版本/渲染失败/P1 尚无渲染器时展示）。 */
  fallback: string;
  [key: string]: unknown;
}

// ----- 分部类型守卫（渲染边界防御：数据来自后端/页面，运行时可能不合型） -----

export function isTextPart(p: MessagePart): p is TextPart {
  return p.type === 'text' && typeof (p as TextPart).text === 'string';
}
export function isMarkdownPart(p: MessagePart): p is MarkdownPart {
  return p.type === 'markdown' && typeof (p as MarkdownPart).text === 'string';
}
export function isImagePart(p: MessagePart): p is ImagePart {
  return p.type === 'image' && typeof (p as ImagePart).src === 'string';
}
export function isCardPart(p: MessagePart): p is CardPart {
  const card = (p as CardPart).card;
  return p.type === 'card' && !!card && typeof card === 'object';
}
export function isComponentPart(p: MessagePart): p is ComponentPart {
  return p.type === 'component' && typeof (p as ComponentPart).name === 'string';
}

// ======================= §3.2 传输绑定 DialogueTransport =======================

/** 显式引用块（§3.1 quote / B 交互）。 */
export interface QuoteBlock {
  id: string;
  label: string;
  text: string;
  /** 业务引用锚（如要素/选区 ref），随命令体透传，控件不解读。 */
  ref?: string;
}

/** 控件→传输的一次流式发送回调。stage 帧驱动回执条；result/error 为终态。 */
export interface StreamHandlers<TResult = unknown> {
  /** SSE stage 帧（回执条数据源）；帧损坏只影响点灯，不影响结果。 */
  onStage?: (stage: string) => void;
  onResult: (result: TResult) => void;
  onError: (error: unknown) => void;
}

/** 发送句柄：控件在卸载/换会话/新发送时可请求中止（P0 传输层若不支持中止则为尽力而为的空实现）。 */
export interface AbortHandle {
  abort: () => void;
}

export interface DialogueTransport<TCommand = unknown, TResult = unknown> {
  /** P0 的 sendDialogueStream 薄包装：页面绑定对话端点。控件所有 submit 类发送走它。 */
  send(command: TCommand, handlers: StreamHandlers<TResult>): AbortHandle;
  /** 页面契约的命令体拼装（数据上下文快照 + 可选引用块）。 */
  buildCommand(text: string, ctx: Record<string, unknown>, quotes?: QuoteBlock[]): TCommand;
}

// ======================= §3.3 线程源 ThreadSource =======================

export type ThreadSource =
  /** 页面持有权威数据，适配器投影为消息数组；控件每次 sessionKey 变化或页面通知时重投影。 */
  | { kind: 'projected'; project: () => ChatMessage[] }
  /**
   * 控件内存队列（乐观行＋结果帧回填），刷新即丢，明示过渡态（抽取/形成页 P2–P4 窗口期）。
   * appendResult 是把页面专属结果投影为追加消息的扩展点（P2 供给，本卡 P1 无页面消费、可缺省）。
   */
  | { kind: 'local'; appendResult?: (result: unknown) => ChatMessage[] }
  /** 服务端线程：控件走游标拉取＋两级快路径（02 篇），P4 起接入；本卡仅类型就位。 */
  | { kind: 'server'; conversationRef: string };

// ======================= §3.1 页面接入契约 HostAdapter =======================

export interface HostCapabilities {
  /** 命令药丸容量：超出按 priority 裁剪、同优先级保持原序。 */
  quickCommandSlots?: number;
  /** 逃生舱组件注册表：component 分部按 name 查此表渲染。 */
  customCards?: Record<string, ComponentType<ComponentPartRenderProps>>;
  /** 结果帧是否内联工作区快照（抽取/形成 true、评审 false），决定回写通路。 */
  inlineWorkspace?: boolean;
}

export interface QuickCommand {
  command: string;
  label: string;
  /** 只生成可编辑文本进输入框；命令词解析恒归后端（README §3 条 8）。 */
  prefill: (ctx: Record<string, unknown>) => string;
  priority: number;
  /** 参数组稿弹层扩展点（输出仍是文本）；P1 不实现。 */
  paramsForm?: unknown;
}

export interface HostActionResult {
  ok: boolean;
  message?: string;
}

/** 语义动作处理函数：页面处理函数内部才知道自己的接口。 */
export type HostActionHandler = (payload: unknown) => Promise<HostActionResult>;

export interface QuoteProvider {
  available: () => boolean;
  capture: () => QuoteBlock[];
}

/** 控件→页面回写通知（取代现状回调 prop 层层上传的阶段推进）。 */
export interface ThreadEvent {
  kind: 'workspace-updated' | 'item-created' | 'stage-advanced';
  payload?: Record<string, unknown>;
}

export interface ChatHostAdapter {
  /** 页面标识（由各页面适配器自定），会话路由键成分与日志维度。控件不枚举、不特判任何具体值。 */
  hostId: string;
  /** 会话路由键 `${hostId}:${scope}:${objectRef}`；切换业务对象即切线程（返回值随选中变化）。 */
  sessionKey: () => string;
  /**
   * 会话头人读标签（页面注入的场景名，原型主视图帧的场景徽标）。可选：缺省时控件回退用 hostId。
   * 只作展示，不参与路由（路由恒以 sessionKey() 为准），故不属 §3.1 必选字段。
   */
  sessionLabel?: () => string;
  transport: DialogueTransport;
  /** 数据上下文快照，发送瞬间由控件拉取拼进命令体（拉不是塞）。 */
  getContext: () => Record<string, unknown>;
  /**
   * 「发送时携带」徽标行的人读标签（原型主视图帧注 6：携带内容对用户可见、可核对）。
   * 仅影响展示，命令体恒取 getContext()；缺省时控件回退显示 getContext() 的键名。
   * 返回空数组=本页无需携带徽标行（整行隐藏）。
   */
  contextChips?: () => string[];
  threadSource: ThreadSource;
  capabilities?: HostCapabilities;
  quickCommands?: QuickCommand[];
  /** 语义动作注册表：动作名→页面处理函数（host 型动作的目的地）。 */
  actions?: Record<string, HostActionHandler>;
  quote?: QuoteProvider;
  onThreadEvent?: (e: ThreadEvent) => void;
}

// ======================= §4.1 动作四型（判别式联合） =======================

/** 后续态声明（§4.2）：done=本轮即终；pending-followup=已受理但有后续异步产出。 */
export type FollowupMode = 'done' | 'pending-followup';

export interface SubmitAction {
  kind: 'submit';
  label: string;
  /** 动作预埋载荷（后端写入、前端透传），与卡内 input.* 值合并后经 transport 发回当前会话。 */
  data?: Record<string, unknown>;
  confirm?: string;
  followup?: FollowupMode;
}

export interface HostAction {
  kind: 'host';
  label: string;
  /** 语义动作名，控件查 adapter.actions 转发。 */
  name: string;
  payload?: Record<string, unknown>;
  confirm?: string;
  followup?: FollowupMode;
}

export interface UrlAction {
  kind: 'url';
  label: string;
  href: string;
  /** 新开页（否则应用内路由跳转）。 */
  external?: boolean;
}

export interface ComponentAction {
  kind: 'component';
  label: string;
  name: string;
  props?: Record<string, unknown>;
}

export type ChatAction = SubmitAction | HostAction | UrlAction | ComponentAction;

// ======================= §4.2 动作状态机（单个动作实例） =======================

export type ActionPhase =
  | 'idle'
  | 'dispatching'
  | 'settled-ok'
  | 'settled-error'
  | 'awaiting-followup'
  | 'linked';

/** 传给逃生舱组件（component 分部）与 P5 卡片渲染器的动作能力面。 */
export interface ChatActionApi {
  /** 触发一个动作实例；dispatching 期间对同实例的再次触发被单飞守卫忽略。 */
  dispatch: (action: ChatAction, instanceId: string) => void;
  /** 该动作实例当前相位（渲染按钮态：dispatching→处理中、settled-error→可重试、awaiting-followup→已受理·后续中）。 */
  phaseOf: (instanceId: string) => ActionPhase;
  /** host 动作名是否在本页注册（未注册→按钮渲染禁用态＋提示，不报错，§4.1）。 */
  isActionRegistered: (name: string) => boolean;
}

/** 逃生舱组件（capabilities.customCards[name]）拿到的 props：页面 props 透传 + 动作能力面。 */
export interface ComponentPartRenderProps {
  props: Record<string, unknown>;
  actions: ChatActionApi;
  /** 该分部所在消息 id，供组件生成稳定的动作实例键。 */
  messageId: string;
}
