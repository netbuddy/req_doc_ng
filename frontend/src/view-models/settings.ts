// 设置工作台 VM（04A §9 两区布局：左配置域菜单 / 右配置面板）。
// 纯函数：DTO → VM，无 React / 无 fetch（MVVM 边界）。
import type {
  CapabilityItemRead,
  CapabilityKey,
  CapabilityState,
  ConfigDomainRead,
  ConfigDomainStatusRead,
  ConnectionTestLevel,
  ExportCapabilityKey,
  ExportReadinessOutcome,
  ExportReadinessRead,
  LlmProviderListRead,
  LlmProviderRead,
  LlmProviderWrite,
  ModelConnectionTestResult,
  ReferenceStandardCatalogRead,
  ReferenceStandardRead,
  ReferenceStandardWrite,
  RequirementConventionCatalogRead,
  RequirementConventionRead,
} from '../api/settings';
import { requirementItemTypeText } from './requirement-item-formation';
import { formatAbsoluteMinute } from './time';

/** 配置域菜单键：支撑能力域 + 生成治理域（需求规约）走后端；users/appearance/document_template 不经配置存储；project=项目危险区（AEP-113）。 */
export type SettingsDomainKey =
  | 'users'
  | 'model_service'
  | 'export'
  | 'chart_rendering'
  | 'document_template'
  | 'requirement_convention'
  | 'reference_standards'
  | 'project'
  | 'appearance';

export type SettingsStatusTone = 'configured' | 'default' | 'pending' | 'local';

export interface SettingsMenuItemVM {
  key: SettingsDomainKey;
  label: string;
  /** 状态签：已配置 / 默认值 / 待接入 / 本地偏好 */
  statusText: string;
  statusTone: SettingsStatusTone;
}

export interface SettingsMenuGroupVM {
  title: string;
  items: SettingsMenuItemVM[];
}

/**
 * 配置域菜单三组（04A §9）：身份与权限 / 外部能力 / 个性化。结构恒定，状态签随后端数据。
 * `extras.documentTemplateCount`：文档模板域不走 config_registry，状态签由已启用模板数派生（tone=default）。
 */
export function buildSettingsMenu(
  statuses: ConfigDomainStatusRead[] | null,
  extras?: { documentTemplateCount?: number },
): SettingsMenuGroupVM[] {
  const byDomain = new Map((statuses ?? []).map((row) => [row.domain, row]));
  const capability = (domain: string, label: string): SettingsMenuItemVM => {
    const status = byDomain.get(domain);
    if (!status) {
      // 后端不可达/未加载：不显示假状态
      return { key: domain as SettingsDomainKey, label, statusText: '—', statusTone: 'default' };
    }
    return {
      key: domain as SettingsDomainKey,
      label,
      statusText: status.configured ? '已配置' : '默认值',
      statusTone: status.configured ? 'configured' : 'default',
    };
  };
  return [
    {
      title: '身份与权限',
      items: [{ key: 'users', label: '用户与权限', statusText: '待接入', statusTone: 'pending' }],
    },
    {
      title: '外部能力',
      items: [
        capability('model_service', '模型服务'),
        capability('export', '导出能力'),
        // 文档模板：管的是模板注册表（非 key-value 配置），状态签由已启用模板数派生。
        {
          key: 'document_template',
          label: '文档模板',
          statusText:
            extras?.documentTemplateCount != null ? `${extras.documentTemplateCount} 个可用` : '—',
          statusTone: 'default',
        },
        capability('chart_rendering', '图表渲染'),
      ],
    },
    {
      // 生成治理：影响 LLM 生成行为的治理类配置，与「外部能力」（连接外部服务）语义区分。
      title: '生成治理',
      items: [capability('requirement_convention', '需求规约')],
    },
    {
      // 文档资源：撰写文档时可取用的素材目录。既不连接外部服务（非「外部能力」），也不影响
      // 模型的生成行为（非「生成治理」），单列一组。
      title: '文档资源',
      items: [capability('reference_standards', '引用标准目录')],
    },
    {
      // 项目危险区（AEP-113）：删除当前项目（级联删净）；非配置存储，无状态签数据。
      title: '项目',
      items: [{ key: 'project', label: '项目管理', statusText: '危险区', statusTone: 'pending' }],
    },
    {
      title: '个性化',
      items: [{ key: 'appearance', label: '外观', statusText: '本地偏好', statusTone: 'local' }],
    },
  ];
}

// ---- 能力域表单 ----

export interface SettingsFieldVM {
  key: string;
  label: string;
  value: string;
  unit?: string;
  /** 填写要求（只有取值形态受约束的字段才有），跟在来源标之后显示 */
  hint?: string;
  /** 生效值来源：已保存 / env 默认 */
  sourceText: string;
}

export interface SettingsSecretVM {
  key: string;
  label: string;
  set: boolean;
  placeholder: string;
}

export interface SettingsDomainFormVM {
  domain: string;
  label: string;
  downstream: string;
  sourceText: string;
  updatedText: string;
  connectionFields: SettingsFieldVM[];
  paramFields: SettingsFieldVM[];
  secrets: SettingsSecretVM[];
}

/** 路径字段填错时的一句白话（与后端 PATH_FIELD_HINT 同措辞）；字段名旁只挂缩略版，位置窄。 */
const PATH_FIELD_HINT = '需填绝对路径（以 / 开头），不支持 ~ 与相对路径';
const PATH_FIELD_HINT_SHORT = '需填绝对路径';

/** 取值必须是绝对路径的字段：填相对路径或 ~ 开头，文件会落到后端进程的当前目录且不报错。 */
const PATH_FIELDS = new Set(['export_dir']);

const FIELD_LABELS: Record<
  string,
  { label: string; unit?: string; hint?: string; section: 'connection' | 'param' }
> = {
  service_name: { label: '服务名称', section: 'connection' },
  base_url: { label: '服务地址', section: 'connection' },
  model: { label: '模型标识', section: 'connection' },
  timeout_seconds: { label: '超时时间', unit: '秒', section: 'param' },
  max_retries: { label: '最大重试', unit: '次', section: 'param' },
  concurrency_limit: { label: '并发上限', unit: '个', section: 'param' },
  export_dir: { label: '导出目录', hint: PATH_FIELD_HINT_SHORT, section: 'connection' },
  renderer: { label: '渲染引擎', section: 'connection' },
  security_level: { label: '安全级别', section: 'connection' },
};

const SECRET_LABELS: Record<string, string> = {
  api_key: 'API Key',
};

/** 「时刻 · 操作人」落款(分钟精度),格式唯一实现;未保存过时的回退文案由调用方给(各页措辞不同)。 */
export function formatUpdatedStamp(
  updatedAt: string | null | undefined,
  updatedBy: string | null | undefined,
  fallback: string,
): string {
  return updatedAt && updatedBy ? `${formatAbsoluteMinute(updatedAt)} · ${updatedBy}` : fallback;
}

export function buildDomainForm(read: ConfigDomainRead): SettingsDomainFormVM {
  const connectionFields: SettingsFieldVM[] = [];
  const paramFields: SettingsFieldVM[] = [];
  for (const field of read.fields) {
    const meta = FIELD_LABELS[field.key] ?? { label: field.key, section: 'connection' as const };
    const vm: SettingsFieldVM = {
      key: field.key,
      label: meta.label,
      value: field.value == null ? '' : String(field.value),
      unit: meta.unit,
      hint: meta.hint,
      sourceText: field.source === 'saved' ? '已保存' : 'env 默认',
    };
    (meta.section === 'param' ? paramFields : connectionFields).push(vm);
  }
  return {
    domain: read.domain,
    label: read.label,
    downstream: read.downstream,
    sourceText: read.source === 'saved' ? '已保存配置' : 'env 默认值',
    updatedText: formatUpdatedStamp(read.updated_at, read.updated_by, '尚未保存过'),
    connectionFields,
    paramFields,
    secrets: read.secrets.map((secret) => ({
      key: secret.key,
      label: SECRET_LABELS[secret.key] ?? secret.key,
      set: secret.set,
      placeholder: secret.placeholder,
    })),
  };
}

// ---- 模型服务多 provider：编辑草稿 VM 与两级测试结果文案 ----

/** 界面上正在编辑的一条模型服务记录。id 为空串=尚未保存过的新增项。 */
export interface ProviderDraftVM {
  id: string;
  name: string;
  providerType: string;
  baseUrl: string;
  model: string;
  timeoutSeconds: string;
  maxRetries: string;
  concurrencyLimit: string;
  /** 已保存过密钥（只读标志，明文永不下发）。 */
  apiKeySet: boolean;
  /** 本次新输入的密钥；空串=不改。 */
  apiKeyInput: string;
  /** 勾选后保存时清除已存密钥。 */
  clearApiKey: boolean;
  /** 思考模式开关（默认关）。 */
  thinkingEnabled: boolean;
  /** 能力探测档案：从服务端读回或探测后「应用」进来，前端只原样存取。 */
  capabilityProfile: Record<string, unknown>;
  /**
   * 这份档案是不是本次编辑新应用进来的。
   *
   * 只有为真时保存请求才带上档案字段。后端对「请求体没带档案」的语义是「保留库里原有那份」，
   * 界面若每次保存都把读回来的档案原样发回去，这条保护就永远走不到：拿着一个开了很久的页面
   * 点一次普通保存，就会把别处刚探明的档案覆盖成页面上那份旧的。
   */
  capabilityProfileChanged: boolean;
}

export function providerDraftFrom(read: LlmProviderRead): ProviderDraftVM {
  return {
    id: read.id,
    name: read.name,
    providerType: read.provider_type,
    baseUrl: read.base_url,
    model: read.model,
    timeoutSeconds: String(read.timeout_seconds),
    maxRetries: String(read.max_retries),
    concurrencyLimit: String(read.concurrency_limit),
    apiKeySet: read.api_key_set,
    apiKeyInput: '',
    clearApiKey: false,
    thinkingEnabled: read.thinking_enabled,
    capabilityProfile: read.capability_profile ?? {},
    capabilityProfileChanged: false,
  };
}

/**
 * 新增一条模型服务。id 在这里就地派号（不等保存后由服务端派），这样刚加的一条也能立刻
 * 「设为使用中」——否则用户得先保存一次、再设使用中、再保存一次，白走两趟。
 * 字符集与后端 provider 标识校验一致（字母数字连字符下划线）。
 */
export function emptyProviderDraft(providerType = 'llama_cpp'): ProviderDraftVM {
  return {
    id: `p${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`,
    name: '',
    providerType,
    baseUrl: '',
    model: '',
    timeoutSeconds: '180',
    maxRetries: '3',
    concurrencyLimit: '5',
    apiKeySet: false,
    apiKeyInput: '',
    clearApiKey: false,
    thinkingEnabled: false,
    capabilityProfile: {},
    capabilityProfileChanged: false,
  };
}

function numberOr(raw: string, fallback: number): number {
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function providerDraftToWrite(draft: ProviderDraftVM): LlmProviderWrite {
  return {
    id: draft.id || null,
    name: draft.name.trim(),
    provider_type: draft.providerType,
    base_url: draft.baseUrl.trim(),
    model: draft.model.trim(),
    timeout_seconds: numberOr(draft.timeoutSeconds, 180),
    max_retries: numberOr(draft.maxRetries, 3),
    concurrency_limit: numberOr(draft.concurrencyLimit, 5),
    api_key: draft.apiKeyInput.trim() || null,
    clear_api_key: draft.clearApiKey,
    thinking_enabled: draft.thinkingEnabled,
    // 没应用过新档案就不发这个字段（null=缺席），让后端「缺席即保留库里原有那份」的保护生效。
    capability_profile: draft.capabilityProfileChanged ? draft.capabilityProfile : null,
  };
}

/** 保存前的就地校验：返回第一条问题的白话说明，全部合规则返回 null。 */
export function validateProviderDrafts(drafts: ProviderDraftVM[]): string | null {
  if (drafts.length === 0) {
    return '至少要保留一个模型服务';
  }
  for (const draft of drafts) {
    const label = draft.name.trim() || '未命名的模型服务';
    if (!draft.name.trim()) {
      return '每个模型服务都要填名称';
    }
    if (!draft.baseUrl.trim()) {
      return `「${label}」还没填服务地址`;
    }
    if (!draft.model.trim()) {
      return `「${label}」还没填模型标识`;
    }
    if (!/^https?:\/\//i.test(draft.baseUrl.trim())) {
      return `「${label}」的服务地址要以 http:// 或 https:// 开头`;
    }
  }
  const names = drafts.map((d) => d.name.trim());
  const duplicate = names.find((name, index) => names.indexOf(name) !== index);
  return duplicate ? `名称重复了：${duplicate}` : null;
}

/**
 * 测试连接时是否使用已存密钥，以及不能用时给用户的一句提示。
 *
 * 后端把「已存密钥」与「保存时的地址」绑定：拿已存密钥去测一个被改过的地址会被 400 拒
 * （外泄面守卫）。所以前端在草稿地址与已存地址不一致时，就不置 use_saved_key，避免用户「改
 * 地址后随手测一下」撞 400；此时若密钥框也是空的，本次测试就不带密钥（本地不需要密钥的服务
 * 照样能测通），并给一句白话提示说明已存密钥为何没被用上。归一化只忽略结尾多余斜杠，与后端一致。
 */
export interface TestKeyUsageVM {
  useSavedKey: boolean;
  /** 已存密钥因地址改动没被用上时的提示；可用或无关时为 null。 */
  savedKeyBlockedHint: string | null;
}

export function resolveTestKeyUsage(params: {
  typedKey: string;
  apiKeySet: boolean;
  draftBaseUrl: string;
  savedBaseUrl: string | null | undefined;
}): TestKeyUsageVM {
  // 现输的密钥优先——有就用它，与已存密钥无关。
  if (params.typedKey.trim()) {
    return { useSavedKey: false, savedKeyBlockedHint: null };
  }
  // 这条没存过密钥：本就没有已存密钥可用。
  if (!params.apiKeySet) {
    return { useSavedKey: false, savedKeyBlockedHint: null };
  }
  const norm = (u: string | null | undefined) => (u ?? '').trim().replace(/\/+$/, '');
  const saved = norm(params.savedBaseUrl);
  if (saved !== '' && saved === norm(params.draftBaseUrl)) {
    return { useSavedKey: true, savedKeyBlockedHint: null };
  }
  return {
    useSavedKey: false,
    savedKeyBlockedHint:
      '服务地址和保存时不一样，已存密钥不会用于本次测试；如需带密钥测试，请在上方重新输入密钥。',
  };
}

/** 地址常见写法提醒：本仓只对接各引擎的 OpenAI 兼容接口，地址通常以 /v1 结尾。 */
export function baseUrlHint(baseUrl: string): string | null {
  const trimmed = baseUrl.trim().replace(/\/+$/, '');
  if (!trimmed || !/^https?:\/\//i.test(trimmed)) {
    return null;
  }
  return trimmed.endsWith('/v1') ? null : '这个地址不是以 /v1 结尾，多数服务的兼容接口地址需要带 /v1';
}

export interface ConnectionResultVM {
  tone: 'success' | 'error';
  /** 一行结论。 */
  title: string;
  /** 一句可照做的说明；无补充时为空串。 */
  detail: string;
}

const LEVEL_LABEL: Record<ConnectionTestLevel, string> = {
  reachability: '连得上',
  generation: '能正常回答',
};

/**
 * 两级测试结果 → 白话文案。后端只给封闭集结果码，文案在这里定，走查改措辞不必动后端。
 * 说明一律给出「下一步该看哪儿」，不留只报错码的死胡同。
 */
export function connectionResultText(
  result: ModelConnectionTestResult,
  model: string,
): ConnectionResultVM {
  const level = LEVEL_LABEL[result.level] ?? '连得上';
  const latency = result.latency_ms == null ? '' : `${result.latency_ms} 毫秒`;
  if (result.ok) {
    if (result.level === 'generation') {
      return {
        tone: 'success',
        title: `${level}：模型已正常回复（${latency}）`,
        detail: result.reply_length ? `本次回复 ${result.reply_length} 个字。` : '',
      };
    }
    const listed = result.model_listed === true ? `，其中有「${model}」` : '';
    return {
      tone: 'success',
      title: `${level}：服务响应正常（${latency}）`,
      detail:
        result.model_count == null
          ? ''
          : `服务上共有 ${result.model_count} 个模型${listed}。接着可以再测「能正常回答」。`,
    };
  }
  switch (result.outcome) {
    case 'unreachable':
      return {
        tone: 'error',
        title: '连不上这个服务',
        detail: '请检查服务地址是否写对、模型服务是否已启动、本机到它的网络是否通。',
      };
    case 'timeout':
      // 超时按秒说：等了八秒钟，写成「8040 毫秒」反而要读者自己换算
      return {
        tone: 'error',
        title:
          result.latency_ms == null
            ? '等不到响应'
            : `等不到响应（等了约 ${Math.max(1, Math.round(result.latency_ms / 1000))} 秒）`,
        detail: '服务可能正忙或正在加载模型；可以稍后重试，或把超时时间调大一些。',
      };
    case 'auth_failed':
      return {
        tone: 'error',
        title: '服务拒绝了这个 API Key',
        detail: '请确认密钥是否正确、是否已过期；本地服务通常不需要填密钥。',
      };
    case 'model_missing':
      return {
        tone: 'error',
        title: `服务上没有「${model}」这个模型`,
        detail:
          result.models.length > 0
            ? `服务上现有：${result.models.slice(0, 5).join('、')}${result.models.length > 5 ? ' 等' : ''}。请照着改写模型标识。`
            : '请确认模型标识写法；Ollama 的模型标识要带标签，例如 qwen2.5:7b。',
      };
    case 'bad_response':
      return {
        tone: 'error',
        title: '服务回的内容不是预期格式',
        detail: '这个地址多半不是模型服务的兼容接口；请确认地址是否写全（通常以 /v1 结尾）。',
      };
    default:
      return { tone: 'error', title: '测试没有通过', detail: '' };
  }
}

// ---- 能力清单：封闭集结果码 → 白话文案 ----
// 后端只回代码与实测数值（supported/degraded/…、探到的字段名与数字），措辞全在这里定：
// 走查阶段改文案不必动后端，且每条文案都可单测。前端不得自行判定能力，只做翻译。

export interface CapabilityRowVM {
  key: CapabilityKey;
  /** 能力名（清单左列）。 */
  label: string;
  /** 三态：ok=✅ 可用 / warn=⚠ 有条件或没探明 / bad=❌ 不可用。 */
  tone: 'ok' | 'warn' | 'bad';
  /** 一行结论。 */
  summary: string;
  /** 一句白话解释，含探到的参数与下一步该做什么；无补充时为空串。 */
  detail: string;
}

const CAPABILITY_LABEL: Record<CapabilityKey, string> = {
  reachable: '连得上',
  generate: '能回答',
  thinking: '思考能力',
  structured: 'JSON 格式输出',
  context: '上下文窗口',
  unknown_fields: '未知参数',
};

const TONE_BY_STATE: Record<CapabilityState, CapabilityRowVM['tone']> = {
  supported: 'ok',
  degraded: 'warn',
  unsupported: 'bad',
  // 没探明也用 ⚠：它不是「不支持」，而是「这次没问出来」，需要用户知道并可重试。
  unknown: 'warn',
};

/** 关思考方式 → 界面上怎么称呼它。参数名照实给出（排查时要用），后面跟一句人话。 */
const THINKING_MODE_TEXT: Record<string, string> = {
  reasoning_effort: '用 reasoning_effort=none 关',
  enable_thinking: '用 enable_thinking=false 关',
  none: '这个模型本来就不输出思考过程',
};

const CONTEXT_SOURCE_TEXT: Record<string, string> = {
  'models.max_model_len': '取自服务的模型列表（max_model_len）',
  'props.n_ctx': '取自服务的运行参数（n_ctx）',
  'api_show.context_length': '取自模型信息（context_length）',
};

function millisText(ms: unknown): string {
  const value = typeof ms === 'number' && Number.isFinite(ms) ? ms : null;
  if (value == null) {
    return '';
  }
  // 超过一秒改用秒：读者要的是「快还是慢」，24000 毫秒得自己换算一次才知道
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} 毫秒`;
}

/** 与思考相关的说明码：从档案的 notes 里择出来给开关与清单共用。 */
const THINKING_NOTE_CODES = [
  'vllm_needs_reasoning_parser',
  'thinking_disabled_on_server',
  'thinking_declared_not_observed',
  'thinking_segment_hidden',
];

/**
 * 这类服务在没有探明结论时，本产品会不会下发关思考参数。
 *
 * vLLM 与通用兼容端点的先验是「一个字段都不发」——它们会把参数收下回 200 却不一定生效，
 * 盲发只会掩盖真实情况。所以对这两类不能说「仍按默认方式下发关思考参数」，那是假话。
 */
const THINKING_FIELD_FREE_TYPES = ['vllm', 'openai_compatible'];

function sendsThinkingOffField(providerType: string): boolean {
  return !THINKING_FIELD_FREE_TYPES.includes(providerType);
}

/** 「没探明时产品怎么办」这句话，按服务类型分叉。 */
function thinkingPriorText(providerType: string): string {
  return sendsThinkingOffField(providerType)
    ? '本产品仍按这类服务的默认方式下发关思考参数。'
    : '本产品不向这类服务下发关思考参数——它们会把参数收下却不一定生效，发了反而看不出真实情况。';
}

function thinkingRow(item: CapabilityItemRead, providerType: string): CapabilityRowVM {
  const label = CAPABILITY_LABEL.thinking;
  const tried = Array.isArray(item.detail?.tried) ? (item.detail.tried as Record<string, unknown>[]) : [];
  const effective = tried.find((t) => t.has_thinking === false);
  const baseline = millisText(item.detail?.baseline_latency_ms);
  const after = millisText(effective?.latency_ms);

  // 探到了思考段并找到了关它的参数：这是最完整的一种结论。
  if (item.state === 'supported' && item.mode && item.mode !== 'none') {
    const compare = baseline && after ? `探测时不关思考用了 ${baseline}，关掉后 ${after}。` : '';
    return {
      key: 'thinking', label, tone: 'ok',
      summary: `具备思考能力，可以关闭——${THINKING_MODE_TEXT[item.mode] ?? '已探明有效的参数'}`,
      detail: `${compare}应用后，这个服务的所有 AI 调用都按这个方式关思考。`.trim(),
    };
  }
  // 没探到思考段。是「模型不会思考」还是「会思考但被服务端关了」，看端点自己的能力声明。
  if (item.state === 'supported') {
    if (item.note_code === 'thinking_disabled_on_server') {
      return {
        key: 'thinking', label, tone: 'ok',
        summary: '具备思考能力，当前由服务端关着',
        detail: '探测没看到思考过程，这个端点声明模型支持思考，而且它自己报了「服务端已把思考输出'
          + '全局关闭」（llama.cpp 的 -rea off / --reasoning-format none）。想让它思考要改服务端'
          + `启动参数，不是换模型。${thinkingPriorText(providerType)}`,
      };
    }
    if (item.note_code === 'thinking_declared_not_observed') {
      return {
        key: 'thinking', label, tone: 'ok',
        summary: '具备思考能力，这次探测没看到思考过程',
        detail: '这个端点声明模型支持思考，但探测的那道题它没有展开思考——可能题目太简单，也可能'
          + `服务端把思考输出关掉了，从这一轮问不出是哪种。当前不影响使用；${thinkingPriorText(providerType)}`,
      };
    }
    if (item.available === false) {
      return {
        key: 'thinking', label, tone: 'ok',
        summary: '不具备思考能力：这个模型不输出思考过程',
        detail: `端点声明它不支持思考，探测也没看到思考过程。${thinkingPriorText(providerType)}`
          + (sendsThinkingOffField(providerType) ? '对不思考的模型没有副作用。' : ''),
      };
    }
    return {
      key: 'thinking', label, tone: 'ok',
      summary: '当前没有思考过程；具不具备思考能力判断不了',
      detail: '这个端点不声明模型能力，探测也没看到思考过程——可能本来就不会思考，也可能被服务端'
        + `关掉了。当前不影响使用；${thinkingPriorText(providerType)}`,
    };
  }
  if (item.note_code === 'vllm_needs_reasoning_parser') {
    return {
      key: 'thinking', label, tone: 'warn',
      summary: '具备思考能力，但关不掉：需要在服务端开启思考解析',
      detail:
        'vLLM 要在启动参数里加上 --reasoning-parser 才支持关思考。加上并重启后再探一次；'
        + '在那之前本产品不会下发关思考参数——这个端点会把它收下却不生效，发了反而看不出真实情况。',
    };
  }
  if (item.note_code === 'thinking_segment_hidden') {
    return {
      key: 'thinking', label, tone: 'warn',
      summary: '没探明：这个端点不单独回出思考过程',
      detail: '它把回复的长度用满了却看不到思考段，因此判断不了思考有没有在跑。可以换个模型标识再探一次。',
    };
  }
  if (item.state === 'unsupported') {
    return {
      key: 'thinking', label, tone: 'bad',
      summary: '具备思考能力，且没能关掉',
      detail: '试过的关思考参数这个端点都不认。带思考跑会明显变慢，长流程可能直接超时。',
    };
  }
  return {
    key: 'thinking', label, tone: 'warn',
    summary: '没探明',
    detail: '探测请求超时或没回出内容，可以稍后再探一次。这一项没结论时，本产品按服务类型的默认方式处理。',
  };
}

function structuredRow(item: CapabilityItemRead): CapabilityRowVM {
  const label = CAPABILITY_LABEL.structured;
  const tried = Array.isArray(item.detail?.tried) ? (item.detail.tried as Record<string, unknown>[]) : [];
  // 「收下了参数、回了 200、产物却不符合格式」——这就是只看状态码看不出的假成功
  const falseSuccess = tried.some((t) => t.ok === true && t.conforms === false);
  if (item.state === 'supported') {
    return {
      key: 'structured', label, tone: 'ok',
      summary: '可以按给定的结构强制输出',
      detail: '需要机器读的结果（要素识别、条目形成、质量诊断）会直接用这一档，可靠性最高。',
    };
  }
  if (item.state === 'degraded') {
    return {
      key: 'structured', label, tone: 'warn',
      summary: '只支持「必须回 JSON」这一档',
      detail: '端点没有按给定结构强制约束，已降一档：结构靠提示词说明来保证，解析失败时产品会重试。',
    };
  }
  if (item.state === 'unsupported') {
    return {
      key: 'structured', label, tone: 'bad',
      summary: '端点不强制输出格式',
      detail: falseSuccess
        ? '它收下了格式参数、也回了成功，但产物并不符合要求的结构——这种「假成功」只看返回码是看不出来的。'
          + '已降为提示词模式：在提示词里要求返回 JSON，解析失败时产品会重试。'
        : '已降为提示词模式：在提示词里要求返回 JSON，解析失败时产品会重试。',
    };
  }
  return { key: 'structured', label, tone: 'warn', summary: '没探明', detail: '可以稍后再探一次。' };
}

function contextRow(item: CapabilityItemRead): CapabilityRowVM {
  const label = CAPABILITY_LABEL.context;
  const tokens = typeof item.tokens === 'number' && item.tokens > 0 ? item.tokens : null;
  const source = CONTEXT_SOURCE_TEXT[item.source ?? ''] ?? '';
  if (item.state === 'supported' && tokens) {
    return {
      key: 'context', label, tone: 'ok',
      summary: `${tokens.toLocaleString('en-US')} tokens`,
      detail: `${source}。一次调用里提示词加回复合计不能超过它，已据此把单次回复的长度上限卡在窗口内，避免请求被服务端直接拒绝。`,
    };
  }
  if (item.state === 'degraded' && tokens) {
    return {
      key: 'context', label, tone: 'warn',
      summary: `${tokens.toLocaleString('en-US')} tokens（模型上限，不是实际生效值）`,
      detail: `${source}。Ollama 实际生效的窗口通常比模型上限小，超出的提示词会被悄悄截断而不报错。`
        + '为了不拿一个不准的数字去截断你的请求，本产品不据此卡长度上限。',
    };
  }
  return {
    key: 'context', label, tone: 'warn',
    summary: '没探到',
    detail: '这个端点没提供窗口大小。本产品不会用猜的数字去截断请求，喂长文档时请自己留意是否被截断。',
  };
}

function unknownFieldsRow(item: CapabilityItemRead): CapabilityRowVM {
  const label = CAPABILITY_LABEL.unknown_fields;
  if (item.state === 'degraded') {
    return {
      key: 'unknown_fields', label, tone: 'warn',
      summary: '这个端点会收下不认识的参数并照常返回成功',
      detail: '所以「请求成功」不等于「参数生效」。上面几项结论都是按实际产物判定的，不是看返回码——这正是要探测的原因。',
    };
  }
  if (item.state === 'supported') {
    return {
      key: 'unknown_fields', label, tone: 'ok',
      summary: '这个端点会拒绝不认识的参数',
      detail: '参数写错时它会直接报错，不会悄悄失效。',
    };
  }
  return { key: 'unknown_fields', label, tone: 'warn', summary: '没探明', detail: '' };
}

function baselineRow(item: CapabilityItemRead, model: string): CapabilityRowVM {
  const label = CAPABILITY_LABEL[item.key];
  const latency = millisText(item.latency_ms);
  if (item.state === 'supported') {
    if (item.key === 'generate') {
      return { key: item.key, label, tone: 'ok', summary: `已正常回复${latency ? `（${latency}）` : ''}`, detail: '' };
    }
    const count = item.detail?.model_count;
    const listed = item.detail?.model_listed === true ? `，其中有「${model}」` : '';
    return {
      key: item.key, label, tone: 'ok',
      summary: `服务响应正常${latency ? `（${latency}）` : ''}`,
      detail: typeof count === 'number' ? `服务上共有 ${count} 个模型${listed}。` : '',
    };
  }
  if (item.state === 'unknown') {
    return { key: item.key, label, tone: 'warn', summary: '没探——前一项没过', detail: '' };
  }
  const reason = connectionResultText(
    {
      ok: false, latency_ms: item.latency_ms, model_count: null, error_code: item.outcome ?? null,
      level: item.key === 'generate' ? 'generation' : 'reachability',
      outcome: (item.outcome ?? 'bad_response') as ModelConnectionTestResult['outcome'],
      model_listed: null, reply_length: null, models: [],
    },
    model,
  );
  return { key: item.key, label, tone: 'bad', summary: reason.title, detail: reason.detail };
}

/** 能力清单的落款：这份结论是什么时候探的。档案会过期，时间戳要让用户看得见。 */
export function capabilityProbeStamp(probedAt: string | null | undefined): string {
  return probedAt ? `探测于 ${formatAbsoluteMinute(probedAt)}` : '';
}

/**
 * 探测结果 → 能力清单的逐行文案。行的顺序由后端给定，前端不重排。
 *
 * 要 providerType 是因为思考那一行得说清「没探明时产品会怎么做」，而这件事按服务类型分叉：
 * vLLM 与通用兼容端点先验就是不发任何关思考参数。
 */
export function buildCapabilityRows(
  items: CapabilityItemRead[], model: string, providerType: string,
): CapabilityRowVM[] {
  return items.map((item) => {
    switch (item.key) {
      case 'thinking':
        return thinkingRow(item, providerType);
      case 'structured':
        return structuredRow(item);
      case 'context':
        return contextRow(item);
      case 'unknown_fields':
        return unknownFieldsRow(item);
      default:
        return baselineRow(item, model);
    }
  });
}

// ---- 思考模式开关 ----
// 「会不会思考」与「能不能关掉思考」是两个结论，开关的说明必须同时用上：
// 关不掉的端点，开关关着也照样带思考跑——不说清楚，用户会以为自己已经关了。

export interface ThinkingFacts {
  state: CapabilityState;
  mode: string;
  available: boolean | null;
  noteCode: string | null;
}

const EMPTY_THINKING_FACTS: ThinkingFacts = {
  state: 'unknown', mode: '', available: null, noteCode: null,
};

/** 从已保存的能力档案里读思考相关的结论（没探测过就是「没探明」）。 */
export function thinkingFactsFromProfile(profile: Record<string, unknown> | null | undefined): ThinkingFacts {
  const section = (profile ?? {}).thinking;
  if (!section || typeof section !== 'object') {
    return EMPTY_THINKING_FACTS;
  }
  const row = section as Record<string, unknown>;
  const notes = (profile ?? {}).notes;
  // 说明码存在档案的 notes 里（各项共用一份），按已知的思考类码择出来。
  const noteCode = Array.isArray(notes)
    ? (notes.find((n) => THINKING_NOTE_CODES.includes(String(n))) as string | undefined) ?? null
    : null;
  return {
    state: (typeof row.off_state === 'string' ? row.off_state : 'unknown') as CapabilityState,
    mode: typeof row.off_mode === 'string' ? row.off_mode : '',
    available: typeof row.available === 'boolean' ? row.available : null,
    noteCode,
  };
}

/** 从这一轮探测结果里读思考相关的结论（比档案新，优先用它）。 */
export function thinkingFactsFromItems(items: CapabilityItemRead[]): ThinkingFacts {
  const item = items.find((i) => i.key === 'thinking');
  if (!item) {
    return EMPTY_THINKING_FACTS;
  }
  return { state: item.state, mode: item.mode ?? '', available: item.available, noteCode: item.note_code };
}

export interface ThinkingModeVM {
  /** 开关下方常驻的一行状态说明。 */
  statusText: string;
  /** 提示的标题：一句话说清这条提示要讲什么；无提示时为空串。 */
  warningTitle: string;
  /** 需要提醒时的整段提示；无需提醒时为空串。 */
  warning: string;
  /** 提示的语气：warn=要当心，info=只是说明。 */
  warningTone: 'warn' | 'info';
}

/**
 * 思考模式开关的说明与提醒。
 *
 * 默认关闭的依据是实测（2026-07-24，本地 27B 模型，见能力探测与参数适配提案第一部分）：
 * 带思考跑慢 20–50 倍，条目形成与端到端两条流程直接跑到 240 秒超时；且思考过程会占用输出
 * 预算，占满时正文为空、任务判为失败。用户执意打开时，把这两条后果原样告诉他。
 */
export function buildThinkingMode(
  facts: ThinkingFacts, enabled: boolean, providerType: string,
): ThinkingModeVM {
  const serverDisabled = facts.noteCode === 'thinking_disabled_on_server';
  const declaredNotObserved = facts.noteCode === 'thinking_declared_not_observed';
  const availability =
    serverDisabled ? '这个模型具备思考能力，但服务端把思考关掉了'
      : declaredNotObserved ? '这个模型具备思考能力，探测时没看到它展开思考'
        : facts.available === true ? '这个模型具备思考能力'
          : facts.available === false ? '这个模型不具备思考能力'
            : '还没探明这个模型具不具备思考能力';
  const cannotTurnOff = facts.state === 'degraded' || facts.state === 'unsupported';

  if (enabled) {
    if (serverDisabled) {
      // 最容易踩空的一种：开关打开了，思考却仍然不会出现——因为闸门在服务端。
      return {
        statusText: `${availability}。开关已开启，但要服务端先放开才会真的思考。`,
        warningTitle: '开关打不开思考——闸门在服务端',
        warning: '这个端点声明模型支持思考，而服务端把思考输出全局关掉了（llama.cpp 的 -rea off '
          + '/ --reasoning-format none）。开关打开也不会有思考——要改的是服务端启动参数，不是换模型。',
        warningTone: 'info',
      };
    }
    if (facts.available === false) {
      return {
        statusText: `${availability}。已开启，但对这个模型不会有变化。`,
        warningTitle: '这个开关对当前模型没有作用',
        warning: '端点声明这个模型不支持思考，探测也没看到思考过程，开着不会有思考。'
          + '要用思考模型，得先在模型服务上换一个。',
        warningTone: 'info',
      };
    }
    return {
      statusText: `${availability}。已开启：这个服务的所有 AI 功能都会带着思考过程跑。`,
      warningTitle: '开着思考跑会有两个后果',
      warning:
        '开启前请知道两件事，都是本地 27B 模型上的实测结果：'
        + '一是慢 20–50 倍，条目形成与端到端识别两条流程会直接跑到 240 秒超时；'
        + '二是思考过程会占用回复的长度预算，占满时正文为空，任务会判为失败、结果不落库。'
        + '除非你就是要看模型的思考过程，否则建议关闭。',
      warningTone: 'warn',
    };
  }
  if (cannotTurnOff) {
    return {
      statusText: `${availability}。开关已关闭，但这个端点关不掉思考。`,
      warningTitle: '开关关着，但这个端点仍会带思考跑',
      warning:
        facts.noteCode === 'vllm_needs_reasoning_parser'
          ? '探测结论：这个端点要在服务端加 --reasoning-parser 才支持关思考。所以开关虽然关着，它仍会带思考跑，'
            + '慢和「正文为空」的风险照旧存在。请到服务端加上该参数并重启，再探一次。'
          : '探测结论：试过的关思考参数这个端点都不认。所以开关虽然关着，它仍会带思考跑，请留意速度与超时。',
      warningTone: 'warn',
    };
  }
  // 关思考参数到底发不发：探明了有效方式就按那个方式发（与服务类型无关），没探明才看类型先验。
  // 与适配层的 chat_extension_fields 同一套判断，两边说的必须是同一件事。
  const probedMode = facts.state === 'supported' && !!facts.mode && facts.mode !== 'none';
  return {
    statusText: probedMode || sendsThinkingOffField(providerType)
      ? `${availability}。开关已关闭，调用时会要求模型跳过思考过程。`
      // vLLM 与通用兼容端点在没有探明有效方式前一个字段都不发，说「会要求模型跳过思考」是假话。
      : `${availability}。开关已关闭；这类服务不靠请求参数关思考，会不会思考取决于服务端设置。`,
    warningTitle: '', warning: '', warningTone: 'info',
  };
}

/** provider 列表页头的落款文案。 */
export function providerListStamp(read: LlmProviderListRead | null): string {
  if (!read) {
    return '—';
  }
  const source = read.source === 'saved' ? '已保存配置' : '尚未保存过（沿用原有配置）';
  return `${source} · ${formatUpdatedStamp(read.updated_at, read.updated_by, '无保存记录')}`;
}

// ---- 文档资源：引用标准目录 VM（AEP-118 → 展示模型；条目内容全部来自后端）----

/** 自有条目的编辑草稿（内置条目不可编辑，不进这里）。 */
export interface StandardDraftVM {
  key: string;
  code: string;
  title: string;
  year: string;
  issuer: string;
  note: string;
  category: string;
  url: string;
}

export function standardDraftFrom(read: ReferenceStandardRead): StandardDraftVM {
  return {
    key: read.key,
    code: read.code,
    title: read.title,
    year: read.year,
    issuer: read.issuer,
    note: read.note,
    category: read.category,
    url: read.url,
  };
}

/**
 * 新增一条自有条目。标识在这里就地派号（不等保存后由后端按标准号生成），这样刚加的一条在
 * 保存前就有稳定的行标识，编辑中途不会因为改标准号而让表格行错位。
 * 字符集与后端条目标识校验一致（字母、数字、连字符、下划线）。
 */
export function emptyStandardDraft(category = 'national'): StandardDraftVM {
  return {
    key: `s${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`,
    code: '',
    title: '',
    year: '',
    issuer: '',
    note: '',
    category,
    url: '',
  };
}

export function standardDraftToWrite(draft: StandardDraftVM): ReferenceStandardWrite {
  return {
    key: draft.key || null,
    code: draft.code.trim(),
    title: draft.title.trim(),
    year: draft.year.trim(),
    issuer: draft.issuer.trim(),
    note: draft.note.trim(),
    category: draft.category,
    url: draft.url.trim(),
  };
}

/**
 * 保存前的就地校验：返回第一条问题的白话说明，全部合规则返回 null。
 * 规则与后端 validate_custom_entries 对齐——前端不额外加后端没有的限制，免得出现「界面拦下
 * 了但接口本来允许」的两套口径。
 */
export function validateStandardDrafts(drafts: StandardDraftVM[]): string | null {
  for (const draft of drafts) {
    const label = draft.code.trim() || draft.title.trim() || '新增的条目';
    if (!draft.code.trim()) {
      return '每条自有条目都要填标准号';
    }
    if (!draft.title.trim()) {
      return `「${label}」还没填名称`;
    }
    const url = draft.url.trim();
    if (url && !/^https?:\/\//i.test(url)) {
      return `「${label}」的链接要以 http:// 或 https:// 开头`;
    }
  }
  return null;
}

/** 目录表格的一行（内置条目与自有条目同表呈现，靠 builtin 区分能做什么）。 */
export interface StandardRowVM {
  key: string;
  code: string;
  title: string;
  year: string;
  issuer: string;
  note: string;
  categoryKey: string;
  categoryLabel: string;
  url: string;
  builtin: boolean;
  enabled: boolean;
  /** 来源与状态的白话说明：内置 / 内置·已停用 / 自有 */
  sourceText: string;
  /** 自有条目在草稿数组里的下标；内置条目为 -1（不可编辑）。 */
  draftIndex: number;
}

/**
 * 目录行投影：内置条目（来自后端目录，停用状态取本地未保存的选择）＋ 自有条目草稿。
 * 排序与后端一致（类别次序 → 标准号），编辑过程中表格顺序才不会跳。
 */
export function buildStandardRows(
  catalog: ReferenceStandardCatalogRead | null,
  drafts: StandardDraftVM[],
  disabledKeys: Set<string>,
): StandardRowVM[] {
  if (!catalog) {
    return [];
  }
  const categoryOrder = catalog.categories.map((c) => c.key);
  const labelOf = (key: string) =>
    catalog.categories.find((c) => c.key === key)?.label ?? key;
  const rows: StandardRowVM[] = catalog.entries
    .filter((e) => e.builtin)
    .map((e) => ({
      key: e.key,
      code: e.code,
      title: e.title,
      year: e.year,
      issuer: e.issuer,
      note: e.note,
      categoryKey: e.category,
      categoryLabel: e.category_label,
      url: e.url,
      builtin: true,
      enabled: !disabledKeys.has(e.key),
      sourceText: disabledKeys.has(e.key) ? '内置 · 已停用' : '内置',
      draftIndex: -1,
    }));
  drafts.forEach((draft, index) => {
    rows.push({
      key: draft.key,
      code: draft.code,
      title: draft.title,
      year: draft.year,
      issuer: draft.issuer,
      note: draft.note,
      categoryKey: draft.category,
      categoryLabel: labelOf(draft.category),
      url: draft.url,
      builtin: false,
      enabled: true,
      sourceText: '自有',
      draftIndex: index,
    });
  });
  const rank = (categoryKey: string) => {
    const at = categoryOrder.indexOf(categoryKey);
    return at < 0 ? categoryOrder.length : at;
  };
  return rows.sort(
    (a, b) => rank(a.categoryKey) - rank(b.categoryKey)
      || a.code.localeCompare(b.code)
      || a.key.localeCompare(b.key),
  );
}

export function catalogStamp(read: ReferenceStandardCatalogRead | null): string {
  if (!read) {
    return '—';
  }
  const source = read.source === 'saved' ? '已保存配置' : '尚未保存过（目录全部来自内置清单）';
  return `${source} · ${formatUpdatedStamp(read.updated_at, read.updated_by, '无保存记录')}`;
}

// ---- 生成治理：需求规约方案目录 VM（AEP-102 → 展示模型；文案全部来自后端）----

export interface ConventionPatternVM {
  label: string;
  pattern: string;
}

export interface ConventionExampleVM {
  /** 条目类型中文名（来自既有 labels 单一来源） */
  typeLabel: string;
  statement: string;
}

export interface ConventionCardVM {
  key: string;
  displayName: string;
  /** 定位首句（卡片副标题） */
  tagline: string;
  /** 当前生效（生效徽标） */
  active: boolean;
}

export interface ConventionDetailVM {
  key: string;
  displayName: string;
  positioning: string;
  blueprint: string;
  patterns: ConventionPatternVM[];
  examples: ConventionExampleVM[];
}

export interface ConventionCatalogVM {
  activeKey: string;
  cards: ConventionCardVM[];
  detailByKey: Record<string, ConventionDetailVM>;
}

function firstSentence(text: string): string {
  const idx = text.search(/[。；;]/);
  return idx >= 0 ? text.slice(0, idx + 1) : text;
}

function typeLabelOf(reqType: string): string {
  // 条目类型中文名走既有单一来源；未知码回落原码（不硬编码方案文案）。
  return (requirementItemTypeText(reqType as Parameters<typeof requirementItemTypeText>[0]) as string) ?? reqType;
}

/** AEP-102 目录 DTO → 展示 VM（单选卡 + 详情卡）。纯函数，不含任何硬编码规约说明文案。 */
export function buildConventionCatalog(catalog: RequirementConventionCatalogRead | null): ConventionCatalogVM {
  const conventions: RequirementConventionRead[] = catalog?.conventions ?? [];
  const activeKey = catalog?.active_convention ?? '';
  const cards: ConventionCardVM[] = conventions.map((c) => ({
    key: c.convention_key,
    displayName: c.display_name,
    tagline: firstSentence(c.positioning),
    active: c.convention_key === activeKey,
  }));
  const detailByKey: Record<string, ConventionDetailVM> = {};
  for (const c of conventions) {
    detailByKey[c.convention_key] = {
      key: c.convention_key,
      displayName: c.display_name,
      positioning: c.positioning,
      blueprint: c.blueprint,
      patterns: c.pattern_overview.map((p) => ({ label: p.label, pattern: p.pattern })),
      examples: c.examples.map((e) => ({ typeLabel: typeLabelOf(e.req_type), statement: e.statement })),
    };
  }
  return { activeKey, cards, detailByKey };
}

/** 表单编辑值 → 保存命令 values（数字字段回转数字；空串跳过=不改）。 */
export function toSaveValues(
  form: SettingsDomainFormVM,
  edited: Record<string, string>,
): Record<string, string | number | null> {
  const numeric = new Set(['timeout_seconds', 'max_retries', 'concurrency_limit']);
  const values: Record<string, string | number | null> = {};
  for (const field of [...form.connectionFields, ...form.paramFields]) {
    const raw = edited[field.key];
    if (raw == null || raw === field.value) {
      continue; // 未编辑
    }
    if (numeric.has(field.key)) {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed)) {
        continue; // 非法数字不提交（表单侧已有约束，双保险）
      }
      values[field.key] = parsed;
    } else {
      values[field.key] = raw;
    }
  }
  return values;
}

/**
 * 保存前的就地校验：返回第一条问题的白话说明，全部合规则返回 null。
 * 规则与后端 save_domain 的路径字段校验对齐——界面先拦一道，用户不用把错值提交一遍才看到原因。
 * 空串是例外：它表示清掉保存值、回落 env 默认，不是一个要落盘的路径。
 */
export function validateDomainValues(values: Record<string, string | number | null>): string | null {
  for (const [key, raw] of Object.entries(values)) {
    if (!PATH_FIELDS.has(key) || typeof raw !== 'string' || raw.trim() === '') {
      continue;
    }
    if (!raw.trim().startsWith('/')) {
      return `「${FIELD_LABELS[key]?.label ?? key}」${PATH_FIELD_HINT}`;
    }
  }
  return null;
}

// ---- 导出能力就绪清单（04A §9「按域提供专属操作」在导出域的落点）----
// 后端只下发稳定结果码与探到的事实（就绪与否/路径/版本串），下面这层负责把它翻成用户看得懂的话：
// 能力名用用户视角（「文档转 PDF 预览」而不是 soffice），缺失后果一句白话，二进制名只作括注。
// 走查改措辞在这里改，不必动后端——与模型服务连通测试同一套口径。

export interface ExportReadinessRowVM {
  key: ExportCapabilityKey;
  /** 用户视角的能力名 */
  capability: string;
  ready: boolean;
  /** 状态列文字：就绪 / 缺失 */
  statusText: string;
  /** 说明列：就绪时给依赖名＋版本＋定位到的路径；缺失时给缺了什么＋一句后果 */
  detail: string;
}

export interface ExportReadinessVM {
  rows: ExportReadinessRowVM[];
  allReady: boolean;
  /** 「检测于 2026-07-25 09:41」 */
  checkedText: string;
  /** 清单上方一句结论 */
  summary: string;
}

/** 各能力的用户视角名与它依赖的工具（工具名只作括注，不单独成行）。 */
const EXPORT_CAPABILITY_LABELS: Record<ExportCapabilityKey, { capability: string; tool: string }> = {
  pdf_preview: { capability: '文档转 PDF 预览', tool: 'LibreOffice' },
  mermaid_diagram: { capability: '流程图渲染', tool: 'mermaid-cli' },
  plantuml_diagram: { capability: '结构图渲染', tool: 'PlantUML' },
};

/** 缺失时的白话说明：缺的是什么 + 对用户意味着什么。 */
const EXPORT_MISSING_TEXT: Record<ExportReadinessOutcome, string> = {
  ready: '',
  soffice_missing:
    '本机没找到 LibreOffice（soffice）。发布页的「精确预览」不可用；导出的 Word 文件本身不受影响。',
  mmdc_missing:
    '本机没找到 mermaid-cli（mmdc）。导出文档里的流程图会以源码文本呈现，不会渲染成图片。',
  // 结构图两条要连屏幕预览一起讲：PlantUML 由后端渲染，缺 Java 或缺 jar 时发布页/追溯页的预览
  // 只会弹一条渲染失败提示，连源码都不显示——比导出文档那一面更难受。（mermaid 那条不同：
  // 它在浏览器里渲染，不经后端，所以 mmdc 缺失确实只影响导出文件。）
  java_missing:
    '本机没找到 Java 运行环境。导出文档里的结构图会以源码文本呈现，不会渲染成图片；'
    + '发布页、追溯页的屏幕预览会显示一条渲染失败提示。',
  plantuml_jar_missing:
    '本机没找到 plantuml.jar。导出文档里的结构图会以源码文本呈现，不会渲染成图片；'
    + '发布页、追溯页的屏幕预览会显示一条渲染失败提示。',
};

/** 从工具自报的版本串里取出版本号本身（各工具格式不一，只认第一串点分数字）。 */
function versionNumber(version: string | null): string {
  return version?.match(/\d+(?:\.\d+)+/)?.[0] ?? '';
}

export function buildExportReadiness(read: ExportReadinessRead): ExportReadinessVM {
  const rows = read.items.map((item) => {
    // 后端把 key 声明为开放的 str：将来多一种能力（或浏览器拿着旧包遇上新后端）时，这里没有兜底
    // 就会取属性抛错，整块清单变成一条「检测失败」，已探到的几项一条都看不见。逐行降级即可。
    const meta = EXPORT_CAPABILITY_LABELS[item.key] ?? { capability: item.key, tool: item.key };
    if (!item.ready) {
      return {
        key: item.key,
        capability: meta.capability,
        ready: false,
        statusText: '缺失',
        detail: EXPORT_MISSING_TEXT[item.outcome] || '本机缺少这项能力依赖的工具。',
      };
    }
    const number = versionNumber(item.version);
    const tool = number ? `${meta.tool} ${number}` : meta.tool;
    return {
      key: item.key,
      capability: meta.capability,
      ready: true,
      statusText: '就绪',
      detail: item.path ? `${tool} · ${item.path}` : tool,
    };
  });
  const missing = rows.filter((row) => !row.ready).length;
  return {
    rows,
    allReady: read.all_ready,
    checkedText: `检测于 ${formatAbsoluteMinute(read.checked_at)}`,
    summary:
      missing === 0
        ? '导出所需的本地工具已全部就绪。'
        : `有 ${missing} 项能力缺少本地工具，导出仍可进行，但下面这些效果会打折扣。`,
  };
}
