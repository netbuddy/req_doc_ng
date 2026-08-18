// 配置管理入口 API（04A §9 设置工作台）。DTO 与 backend/app/api/schemas.py 同名对齐。
// 外观域为浏览器本地偏好（04A §9.1），无任何后端调用。
import { apiGet, apiPost, apiPut } from './client';

export interface ConfigDomainStatusRead {
  domain: string;
  label: string;
  group: string;
  downstream: string;
  configured: boolean;
  source: string; // saved / env
  updated_at: string | null;
  updated_by: string | null;
}

export interface ConfigFieldRead {
  key: string;
  value: string | number | null;
  source: string; // saved / env
}

/** 密钥字段读投影：只报告是否已设置，绝不含明文。 */
export interface ConfigSecretRead {
  key: string;
  set: boolean;
  placeholder: string;
}

export interface ConfigDomainRead {
  domain: string;
  label: string;
  group: string;
  downstream: string;
  source: string;
  updated_at: string | null;
  updated_by: string | null;
  fields: ConfigFieldRead[];
  secrets: ConfigSecretRead[];
}

export interface ConfigSaveCommand {
  values: Record<string, string | number | null>;
  secrets: Record<string, string>; // 空串 = 保留原值（脱敏占位未重输）
  operator_ref: string;
}

export interface ConfigSaveResult {
  domain: string;
  saved: boolean;
  changed_keys: string[];
  audit_ref: string;
}

/** 两级连通测试：reachability=带鉴权探模型列表；generation=发一次最小生成请求。 */
export type ConnectionTestLevel = 'reachability' | 'generation';

export interface ModelConnectionTestCommand {
  base_url: string;
  model?: string | null;
  timeout_seconds?: number;
  api_key?: string | null;
  use_saved_key?: boolean;
  level?: ConnectionTestLevel;
  provider_type?: string;
  provider_id?: string | null;
}

/** 后端只回封闭集结果码，白话文案由 view-models/settings.ts 映射（走查改措辞不必动后端）。 */
export type ConnectionOutcome =
  | 'ok'
  | 'unreachable'
  | 'timeout'
  | 'auth_failed'
  | 'model_missing'
  | 'bad_response';

export interface ModelConnectionTestResult {
  ok: boolean;
  latency_ms: number | null;
  model_count: number | null;
  error_code: string | null;
  level: ConnectionTestLevel;
  outcome: ConnectionOutcome;
  model_listed: boolean | null;
  reply_length: number | null;
  models: string[];
}

// ---- 逐能力探测（C1 可达 / C2 能生成 / C3 可关思考 / C4 结构化输出 / C5 有效上下文 /
// C6 未识别字段是否静默接受）。键与取值的封闭集由后端定，前端只做白话映射，不另写一份清单。----

export type CapabilityKey =
  | 'reachable'
  | 'generate'
  | 'thinking'
  | 'structured'
  | 'context'
  | 'unknown_fields';

/** supported=可用 / degraded=有条件 / unsupported=不可用 / unknown=没探明。 */
export type CapabilityState = 'supported' | 'degraded' | 'unsupported' | 'unknown';

export interface CapabilityItemRead {
  key: CapabilityKey;
  state: CapabilityState;
  /** C3 探明的关思考方式：reasoning_effort / enable_thinking / none。 */
  mode: string | null;
  /** C3 这个模型具不具备思考能力（null=判断不了）——与「能不能关掉」是两个结论：
   *  具备能力却被服务端全局关掉是常见形态，此时 available=true 而探测看不到思考段。 */
  available: boolean | null;
  /** C4 实测强制生效的最高档：json_schema / json_object / prompt_only。 */
  tier: string | null;
  /** C5 有效上下文（token）与它的出处。 */
  tokens: number | null;
  source: string | null;
  /** 结论之外还要告诉用户的那一句话的代码（如 vllm_needs_reasoning_parser）。 */
  note_code: string | null;
  outcome: string | null;
  latency_ms: number | null;
  /** 判定依据的数值事实（基线/候选各自的延迟与输出 token 数）。不含响应正文。 */
  detail: Record<string, unknown>;
}

export interface ModelCapabilityProbeResult {
  items: CapabilityItemRead[];
  /** 可直接写回 provider 的能力档案（形状由后端定，前端只透传，不解读内部结构）。 */
  profile: Record<string, unknown>;
  probed_at: string;
  ok: boolean;
}

// ---- 模型服务多 provider（列表管理 + 启用指针；类型封闭集由后端给）----

// 导出能力就绪清单：后端只给稳定结果码与探到的事实，白话文案由 view-model 映射。
export type ExportCapabilityKey = 'pdf_preview' | 'mermaid_diagram' | 'plantuml_diagram';

export type ExportReadinessOutcome =
  | 'ready'
  | 'soffice_missing'
  | 'mmdc_missing'
  | 'java_missing'
  | 'plantuml_jar_missing';

export interface ExportReadinessItemRead {
  key: ExportCapabilityKey;
  ready: boolean;
  outcome: ExportReadinessOutcome;
  path: string | null;
  version: string | null;
}

export interface ExportReadinessRead {
  checked_at: string;
  all_ready: boolean;
  items: ExportReadinessItemRead[];
}

export interface LlmProviderTypeRead {
  key: string;
  label: string;
  description: string;
}

export interface LlmProviderRead {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  model: string;
  timeout_seconds: number;
  max_retries: number;
  concurrency_limit: number;
  /** 密钥只报是否已设置，绝不回显明文。 */
  api_key_set: boolean;
  active: boolean;
  /** 思考模式：是否让这个模型服务带思考跑。默认关。 */
  thinking_enabled: boolean;
  /** 能力探测档案（空对象=从未探测）。前端只负责原样存取，不解读内部结构。 */
  capability_profile: Record<string, unknown>;
}

export interface LlmProviderListRead {
  active_provider_id: string;
  providers: LlmProviderRead[];
  provider_types: LlmProviderTypeRead[];
  source: string; // saved / env
  updated_at: string | null;
  updated_by: string | null;
}

export interface LlmProviderWrite {
  id?: string | null;
  name: string;
  provider_type: string;
  base_url: string;
  model: string;
  timeout_seconds: number;
  max_retries: number;
  concurrency_limit: number;
  /** 留空=保留原值（脱敏占位下没重输）。 */
  api_key?: string | null;
  clear_api_key?: boolean;
  /** 缺席=保留库里原值；给了才覆盖（点「应用探测结果」时带上）。 */
  capability_profile?: Record<string, unknown> | null;
  /** 缺席=保留库里原值。 */
  thinking_enabled?: boolean | null;
}

export interface LlmProviderSaveCommand {
  providers: LlmProviderWrite[];
  active_provider_id: string | null;
  operator_ref: string;
}

// ---- AEP-118 引用标准目录（配置域 reference_standards）----
// 清单定义的单一来源在后端 app/domain/reference_standards.py：前端不得内置任何一条标准，
// 也不得硬编码类别中文标签（categories 由后端给）。

export interface ReferenceStandardCategoryRead {
  key: string;
  label: string;
}

export interface ReferenceStandardRead {
  key: string;
  code: string;
  title: string;
  year: string;
  issuer: string;
  note: string;
  category: string;
  category_label: string;
  url: string;
  /** 内置条目：随代码版本化，只可停用不可编辑。 */
  builtin: boolean;
  /** 停用的内置条目仍在列表里（供恢复），只是 enabled=false；自有条目恒 true。 */
  enabled: boolean;
}

export interface ReferenceStandardCatalogRead {
  entries: ReferenceStandardRead[];
  categories: ReferenceStandardCategoryRead[];
  builtin_count: number;
  custom_count: number;
  disabled_count: number;
  source: string; // saved / builtin
  updated_at: string | null;
  updated_by: string | null;
}

export interface ReferenceStandardWrite {
  /** 留空＝后端按标准号自动生成标识。 */
  key?: string | null;
  code: string;
  title: string;
  year: string;
  issuer: string;
  note: string;
  category: string;
  url: string;
}

export interface ReferenceStandardSaveCommand {
  /** 整表替换：即保存后的完整自有条目列表，缺席者视为删除。 */
  custom_entries: ReferenceStandardWrite[];
  disabled_builtin_keys: string[];
  operator_ref: string;
}

// ---- AEP-102 需求规约方案目录（只读；文案单一来源，前端禁硬编码规约说明）----

export interface ConventionPatternRead {
  label: string;
  pattern: string;
}

export interface ConventionExampleRead {
  req_type: string;
  statement: string;
}

export interface RequirementConventionRead {
  convention_key: string;
  display_name: string;
  blueprint: string;
  positioning: string;
  pattern_overview: ConventionPatternRead[];
  examples: ConventionExampleRead[];
}

export interface RequirementConventionCatalogRead {
  active_convention: string;
  conventions: RequirementConventionRead[];
}

export const settingsApi = {
  listDomains: () => apiGet<ConfigDomainStatusRead[]>('/config/domains'),
  getDomain: (domain: string) => apiGet<ConfigDomainRead>(`/config/${domain}`),
  saveDomain: (domain: string, command: ConfigSaveCommand) =>
    apiPut<ConfigSaveResult>(`/config/${domain}`, command),
  testModelConnection: (command: ModelConnectionTestCommand) =>
    apiPost<ModelConnectionTestResult>('/config/model-service/test-connection', command),
  // 导出能力就绪：逐项探测本地工具链（只定位＋取版本，无副作用），用户点动作才调。
  getExportReadiness: () => apiGet<ExportReadinessRead>('/config/export/readiness'),
  probeModelCapabilities: (command: ModelConnectionTestCommand) =>
    apiPost<ModelCapabilityProbeResult>('/config/model-service/probe-capabilities', command),
  listProviders: () => apiGet<LlmProviderListRead>('/config/model-service/providers'),
  saveProviders: (command: LlmProviderSaveCommand) =>
    apiPut<LlmProviderListRead>('/config/model-service/providers', command),
  // AEP-102：全局只读方案目录（含当前生效方案 key）。
  listRequirementConventions: () =>
    apiGet<RequirementConventionCatalogRead>('/requirement-conventions'),
  // AEP-118：引用标准目录（内置＋自有全集，含被停用的内置条目）。
  listReferenceStandards: () =>
    apiGet<ReferenceStandardCatalogRead>('/config/reference-standards'),
  saveReferenceStandards: (command: ReferenceStandardSaveCommand) =>
    apiPut<ReferenceStandardCatalogRead>('/config/reference-standards', command),
};
