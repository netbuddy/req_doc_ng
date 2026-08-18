/**
 * 条目评审 API（SCN-003 v5 结论裁决）。
 *
 * 事实源：docs/40 domains/DS-001/interfaces/条目评审服务.md、
 * slices/SCN-003-P01/页面详细设计.md（v5）。
 * - AEP-032 诊断（批量异步，结论逐条目实时入流）；
 * - AEP-033 工作区读（线程/会话条/动态流三投影的唯一素材）；
 * - AEP-034 结论裁决（采纳副作用链后端原子执行 / 拒绝理由必填）；
 * - AEP-095 对话（解释/草案/轻量重评三出口，领域零写入）；
 * - 覆盖确认 / 人工撤回（人工单边命令，理由必填）。
 */
import { apiGet, apiPost } from './client';
import { sendDialogueStream as streamDialogueSse } from './dialogue-stream';
import type { ActionFact, MaterialCanvasRead } from './analysis';
import type {
  FormationElementRead,
  RequirementItemStatus,
  RequirementItemType,
} from './item-formation';

export type DiagnosisMode = 'quick' | 'standard' | 'comprehensive' | 'incremental';

export type DiagnosisTrigger = 'user_submit' | 'revision_chained' | 'dialogue_reeval';

/** 后端派生显示态（AEP-033 三分＋终态）；用户可见细分见 ReviewDisplayCode（后端读视图单点，issue #10 B2a 下沉）。 */
export type ReviewItemStatus =
  | 'no_verdict'
  | 'diagnosing'
  | 'awaiting_adjudication'
  | 'confirmed'
  | 'terminated';

/**
 * 用户可见显示态封闭集（issue #10 B2a：AEP-033 读视图 display_code 单点输出）。
 * 把粗粒 no_verdict 按最近轮次事实二次细分为四格（待诊断/诊断失败/结论已拒绝/待补充来源），
 * 与状态机文档 §3 一致；四码 diagnosing/awaiting_adjudication/confirmed/terminated 与 ReviewItemStatus 同值同义。
 * 前端只做 code→{label,tone} 纯呈现映射，语义分桶全在后端（deriveReviewDisplay 已退役）。
 */
export type ReviewDisplayCode =
  | 'diagnosing'
  | 'awaiting_adjudication'
  | 'confirmed'
  | 'terminated'
  | 'pending_diagnosis'
  | 'diagnosis_failed'
  | 'verdict_rejected'
  | 'supplement_pending';

export type VerdictKind = 'pass' | 'revise' | 'withdraw' | 'supplement';

export type VerdictDecision = 'adopted' | 'rejected';

export type DialogueOutcomeType = 'explanation' | 'draft' | 'reeval' | 'command';

export type ReviewFindingType =
  | 'source_inconsistency'
  | 'ambiguous_expression'
  | 'untestable'
  | 'missing_field'
  | 'no_blocker';

export interface ReviewFindingRead {
  finding_ref: string;
  finding_type: ReviewFindingType;
  diagnosis_summary: string;
  basis_summary: string;
  rule_code?: string | null;
  dimension?: string | null;
  severity?: string;
  evidence_span?: string | null;
  /** 用户已裁定这条不是问题（AEP-116；后端读投影时按指纹现算，随撤销自动恢复） */
  vetoed?: boolean;
  /** 命中的那条否决留痕，撤销时回传 */
  veto_ref?: string | null;
  veto_reason?: string | null;
  /** 这条能否被标记：无规则码+证据片段则跨轮认不出同一个问题，界面不给入口 */
  can_veto?: boolean;
  /**
   * 条目已有人工确认来源，这条来源对齐类发现因此降为非阻断提示（后端读投影现算）。
   * 只降来源对齐一类：与业务规则矛盾、表达歧义、可测试性等判据照常阻断。
   */
  source_attested?: boolean;
}

/**
 * 人工确认背书：材料里没写这条，由人确认它是真实需求并负责登记。
 *
 * 与「来源要素」并列的**独立证据类别**，不是来源的一种——背书没有对应的材料位置，
 * 所以界面只显示标记与理由，绝不显示引文，也不在区3 高亮任何段落。
 */
export interface SourceAttestationRead {
  record_ref: string;
  reason: string;
  operator_ref: string;
  at: string;
}

/** 问题否决留痕（AEP-116）：用户裁定「这条不是问题」的账目，含已撤销者 */
export interface FindingVetoRead {
  veto_ref: string;
  item_ref: string;
  finding_type: ReviewFindingType;
  rule_code?: string | null;
  evidence_span?: string | null;
  finding_summary: string;
  reason?: string | null;
  operator_ref: string;
  at: string;
  revoked: boolean;
  revoked_at?: string | null;
}

export interface RevisionPointRead {
  point_ref: string;
  label: string;
  /** 模型输出的发现项序号；与 findings 数组的读出序不是一回事，配对请优先用 finding_ref */
  finding_index: number;
  /** 本修订点所针对的发现项引用；存量轮次为空，此时才回退按 finding_index 配对 */
  finding_ref?: string | null;
  find: string;
  replace: string;
  basis: string;
  group?: string | null;
  /** 本点针对的问题已被裁定为不是问题 → 不应再采纳（界面标灰不可勾选） */
  vetoed?: boolean;
}

export interface VerdictAdjudicationRead {
  decision: VerdictDecision;
  selected_point_refs: string[];
  excluded_point_refs: string[];
  /** 采纳时用户对所选点替换文本的改稿（{point_ref: 终稿}）；空=采纳的就是 AI 原案 */
  point_edits?: Record<string, string>;
  reason?: string | null;
  operator_ref: string;
  at: string;
}

export interface VerdictRead {
  round_ref: string;
  round_no: number;
  // run/batch 归属（issue #10 B2a 后端契约，只增不改）：暂无前端消费者——失败归因实由
  // DiagnosisRunProgressRead.failed_count 独力驱动，此二字段仅镜像后端 schema 备查。
  batch_ref?: string;
  item_ref?: string;
  diagnosis_mode: DiagnosisMode;
  trigger: DiagnosisTrigger;
  status: 'running' | 'completed' | 'failed';
  verdict_kind?: VerdictKind | null;
  verdict_summary?: string | null;
  findings: ReviewFindingRead[];
  revision_points: RevisionPointRead[];
  supplement_gaps: string[];
  context_coverage: string;
  model_result_refs: string[];
  invalidated: boolean;
  invalidated_reason?: string | null;
  superseded_by?: string | null;
  adjudication?: VerdictAdjudicationRead | null;
  overridden: boolean;
  confirm_result?: string | null;
  effective: boolean;
  reason?: string | null;
  created_at: string;
  /** 本轮仍然成立的阻断问题条数（被裁定为不是问题的不计入） */
  blocking_finding_count?: number;
  /** 本轮报的阻断问题已被逐条裁定为不是问题、一条不剩 → 可直接确认 */
  all_blocking_findings_vetoed?: boolean;
  /**
   * 本轮曾报出阻断问题，且此刻一条待处理的都不剩（被裁定或因人工确认降格都算）。
   * 直接确认通道按它开门；界面读 available_actions 的 affordance，不自算门禁。
   */
  blocking_findings_cleared?: boolean;
}

export interface DialogueMessageRead {
  message_ref: string;
  kind: DialogueOutcomeType;
  user_message: string;
  text: string;
  draft_value?: string | null;
  draft_note?: string | null;
  draft_seq?: number | null;
  suggestion_ref?: string | null;
  in_flight: boolean;
  /** 这条交换发生在哪一页（review/formation）；存量数据为空，来源不明按原样显示 */
  origin?: string | null;
  created_at: string;
}

export interface ItemRevisionRecordRead {
  record_ref: string;
  field_key: string;
  before_value: string;
  after_value: string;
  revision_mode: string;
  selected_point_refs: string[];
  operator_ref: string;
  reason?: string | null;
  created_at: string;
}

export interface ReviewRequirementItemRead {
  item_ref: string;
  req_no: string;
  expression: string;
  req_type: RequirementItemType;
  status: RequirementItemStatus;
  version_no: string;
  source_element_refs: string[];
  formation_basis_ref?: string | null;
  verification_method?: string[]; // 29148 属性补齐（验证方式，多选）
  verification_note?: string | null; // 验收准则（缺失=评审补充前置信号）
  priority?: string | null; // 优先级（仅人工设定）
  revision_records: ItemRevisionRecordRead[];
  review_status: ReviewItemStatus;
  status_note: string;
  // 用户可见显示态封闭集单点（issue #10 B2a 契约，只增不改）：把 no_verdict 细分为
  // 待诊断/诊断失败/结论已拒绝/待补充来源。display_note=对应说明句单点（含待裁决说明句、
  // 诊断失败连击次数、到达路径副语）。B2b 区1/区5 消费同一字段，退役前端 deriveReviewDisplay。
  display_code: ReviewDisplayCode;
  display_note: string;
  current_verdict?: VerdictRead | null;
  verdict_history: VerdictRead[];
  dialogue_messages: DialogueMessageRead[];
  supplement_gaps_open: string[];
  /** 已按 AI 建议采纳过多少次「建议修订」仍未通过。纯事实、不是门禁，界面只据此给非阻断提示 */
  adopted_revise_rounds?: number;
  /** 本条目的否决账目（含已撤销者，新→旧） */
  finding_vetoes?: FindingVetoRead[];
  /** 人工确认背书（材料未记载该需求，由人确认它成立）。空＝没有背书过。 */
  source_attestation?: SourceAttestationRead | null;
  /**
   * 当前这次「旧结论失效」正是人工确认造成的——即来源缺口刚刚被确认闭合。
   * 与 source_attestation 的区别：后者是粘性事实（背过书就一直在），本字段只在那一刻为真，
   * 背书之后的任何一次普通修订都会让它转假。界面据此把说明句升格为醒目横幅。
   */
  attestation_closed_gap?: boolean;
  available_actions: ActionFact[];
}

export interface DiagnosisRunProgressRead {
  run_ref: string;
  item_refs: string[];
  diagnosis_mode: DiagnosisMode;
  status: 'running' | 'completed';
  completed_count: number;
  total_count: number;
  // 本批次内失败轮次数（issue #10 B2a 契约，只增不改）：按 run 直接归因（分子=该批
  // processing_status ∈{failed,not_diagnosable} 轮次数）。前端弃用以条目全局最新态猜测本 run 失败
  // （结算窗口内被新批重诊漏报、跨批遗留失败误计）。批次收束后仍稳定可查。
  // 必填（与兄弟字段 completed_count/total_count 同口径）：后端 schema 缺省 0 且唯一生产者恒显式传值；
  // 若建模为可选，缺字段的失败形态是 toast 静默不弹而非类型报错（合并裁定 F8）。
  failed_count: number;
  next_action?: string | null;
}

export interface ItemReviewWorkspaceRead {
  review_context_ref: string;
  formation_context_ref: string;
  workspace_version: string;
  material_canvas: MaterialCanvasRead;
  source_elements: FormationElementRead[];
  review_items: ReviewRequirementItemRead[];
  diagnosis_options: DiagnosisMode[];
  diagnosis_runs: DiagnosisRunProgressRead[];
  available_operations: ActionFact[];
  confirmed_count: number;
  total_count: number;
  next_action?: string | null;
}

export interface ItemReviewDiagnosisCommand {
  project_ref: string;
  item_refs: string[];
  diagnosis_mode: DiagnosisMode;
  workspace_version: string;
  operator_ref: string;
  idempotency_key: string;
}

export interface ItemReviewDiagnosisRequestResult {
  status: 'submitted' | 'rejected_precheck';
  review_context_ref?: string | null;
  agent_run_ref?: string | null;
  next_action?: string | null;
}

export interface VerdictAdjudicationCommand {
  project_ref: string;
  item_ref: string;
  round_ref: string;
  decision: VerdictDecision;
  selected_point_refs?: string[] | null;
  /** 采纳修订时用户对所选点替换文本的改稿；不传=按 AI 原案采纳 */
  point_edits?: Record<string, string> | null;
  reason?: string | null;
  workspace_version: string;
  operator_ref: string;
  idempotency_key: string;
}

/** AEP-116 否决/恢复一条诊断问题 */
export interface FindingVetoCommand {
  project_ref: string;
  item_ref: string;
  action: 'veto' | 'restore';
  finding_ref?: string | null;  // action=veto 时给
  veto_ref?: string | null;     // action=restore 时给
  reason?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

/**
 * 人工确认背书入参：材料里漏写了这条，人工确认它是真实需求。
 * 理由必填；服务端只登记背书事实，不写任何材料锚点、不生成引文、不改来源要素。
 */
export interface SourceAttestationCommand {
  project_ref: string;
  item_ref: string;
  reason: string;
  operator_ref: string;
  idempotency_key: string;
}

export interface ReviewDialogueCommand {
  project_ref: string;
  item_ref: string;
  message: string;  // 整段原文，可含 /命令词（前端不解析）
  draft_ref?: string | null;
  selected_item_refs?: string[];  // 上下文：区1 勾选的诊断范围
  workspace_version: string;
  operator_ref: string;
  idempotency_key: string;
  /**
   * 这条命令是不是用户亲手输入的。页面自行发起的命令传 false（后端据此不写演示留痕，
   * 否则每次进页面都会多出一对用户从未输入过的气泡）。不传＝true，手敲命令行为不变。
   */
  user_initiated?: boolean;
}

/**
 * 为条目找候选来源读视图（issue #30；候选=同批次已确认、未链接到本条的要素投影）。
 * element_ref 取自服务算出的差集，content/source_quote 为要素事实（原文引文=登记依据），
 * reason/rank 为「找来源」lane 的推荐理由与相关度排序（1 最相关）。
 */
export interface SourceCandidateRead {
  element_ref: string;
  element_type: string;
  content: string;
  source_quote?: string | null;
  reason: string;
  rank: number;
}

export interface ReviewDialogueResult {
  outcome_type: DialogueOutcomeType;
  explanation?: string | null;
  draft?: DialogueMessageRead | null;
  agent_run_ref?: string | null;
  next_action?: string | null;
  // outcome_type=command 的解释回执（时间线审计）
  command_word?: string | null;
  operation?: string | null;
  operation_label?: string | null;
  params_echo?: Record<string, unknown> | null;
  message?: string | null;
  // /找来源 命令的候选来源载荷（issue #30；非空即候选卡素材，只读）
  source_candidates?: SourceCandidateRead[] | null;
}

export interface ItemConfirmationCommand {
  project_ref: string;
  item_ref: string;
  workspace_version: string;
  override: boolean;
  reason?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface ItemConfirmationResult {
  status: 'confirmed' | 'rejected_precheck';
  item_ref: string;
  item_status: RequirementItemStatus;
  next_action?: string | null;
}

export interface ItemWithdrawCommand {
  project_ref: string;
  item_ref: string;
  workspace_version: string;
  reason: string;
  operator_ref: string;
  idempotency_key: string;
}

export interface ItemWithdrawResult {
  status: 'terminated' | 'rejected_precheck';
  item_ref: string;
  item_status: RequirementItemStatus;
  next_action?: string | null;
}

function itemReviewPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/item-reviews`;
}

function requirementPath(projectId: string, itemRef: string): string {
  return `/projects/${encodeURIComponent(projectId)}/requirements/${encodeURIComponent(itemRef)}`;
}

// 与上面 requirementPath 不是同一段路径：确认/撤回挂在 /requirements/，
// 质量投影与否决挂在 /requirement-items/（后端既有分段，此处如实镜像，不合并）。
function requirementItemPath(projectId: string, itemRef: string): string {
  return `/projects/${encodeURIComponent(projectId)}/requirement-items/${encodeURIComponent(itemRef)}`;
}

export const itemReviewApi = {
  startDiagnosis(projectId: string, command: ItemReviewDiagnosisCommand): Promise<ItemReviewDiagnosisRequestResult> {
    return apiPost<ItemReviewDiagnosisRequestResult>(`${itemReviewPath(projectId)}/diagnosis`, command);
  },

  getWorkspace(projectId: string, reviewContextRef: string): Promise<ItemReviewWorkspaceRead> {
    return apiGet<ItemReviewWorkspaceRead>(
      `${itemReviewPath(projectId)}/${encodeURIComponent(reviewContextRef)}`,
    );
  },

  adjudicateVerdict(projectId: string, command: VerdictAdjudicationCommand): Promise<ItemReviewWorkspaceRead> {
    return apiPost<ItemReviewWorkspaceRead>(
      `${itemReviewPath(projectId)}/rounds/${encodeURIComponent(command.round_ref)}/adjudication`,
      command,
    );
  },

  reviewDialogue(projectId: string, command: ReviewDialogueCommand): Promise<ReviewDialogueResult> {
    return apiPost<ReviewDialogueResult>(`${itemReviewPath(projectId)}/dialogue`, command);
  },

  // AEP-095 流式变体（链路回执条数据源）：改薄包装，SSE 实现体收敛至 api/dialogue-stream.ts（P0）
  reviewDialogueStream(
    projectId: string,
    command: ReviewDialogueCommand,
    handlers: { onStage?: (stage: string) => void },
  ): Promise<ReviewDialogueResult> {
    return streamDialogueSse<ReviewDialogueResult>(
      `${itemReviewPath(projectId)}/dialogue`, command, handlers,
    );
  },

  recordFindingVeto(projectId: string, command: FindingVetoCommand): Promise<ItemReviewWorkspaceRead> {
    return apiPost<ItemReviewWorkspaceRead>(
      `${requirementItemPath(projectId, command.item_ref)}/finding-vetoes`, command,
    );
  },

  attestSource(projectId: string, command: SourceAttestationCommand): Promise<ItemReviewWorkspaceRead> {
    return apiPost<ItemReviewWorkspaceRead>(
      `${requirementItemPath(projectId, command.item_ref)}/source-attestation`, command,
    );
  },

  confirmItem(projectId: string, command: ItemConfirmationCommand): Promise<ItemConfirmationResult> {
    return apiPost<ItemConfirmationResult>(
      `${requirementPath(projectId, command.item_ref)}/confirm`, command,
    );
  },

  withdrawItem(projectId: string, command: ItemWithdrawCommand): Promise<ItemWithdrawResult> {
    return apiPost<ItemWithdrawResult>(
      `${requirementPath(projectId, command.item_ref)}/withdraw`, command,
    );
  },
};
