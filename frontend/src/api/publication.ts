import { apiGet, apiGetBlob, apiHead, apiPost } from './client';
import type { RequirementItemStatus, RequirementItemType } from './item-formation';

/**
 * 模型拒绝为本章节起草初稿（AEP-110 的 status='declined'）。
 *
 * 这不是请求出错：拒绝是模型对「依据不足，照写就是编造」的正当判断，界面要把 `reason` 当
 * 一等回执原样呈现，而不是套用通用报错气泡（通用气泡会在理由前拼上请求地址与 HTTP 状态码，
 * 把真正的理由挤出视野——T20260721 的源头报障正是这个形态）。
 */
export class ManuscriptDeclinedError extends Error {
  readonly reason: string;

  constructor(reason: string) {
    super(reason || 'AI 无法起草该章节初稿');
    this.name = 'ManuscriptDeclinedError';
    this.reason = reason;
  }
}

// ---- SCN-005 发布管理 DTO（与 backend/app/api/schemas.py 同名对齐）----

export type DocumentStatus =
  | 'index_draft'
  | 'index_blocked'
  | 'index_ready'
  | 'markdown_draft'
  | 'markdown_finalized'
  | 'baseline_published';

export type SlotAssetType = 'requirement_item' | 'material' | 'chart' | 'boilerplate';

export type EditImpact =
  | 'doc_expression'
  | 'confirmed_item'
  | 'index_structure'
  | 'no_source_fact'
  | 'other_asset';

export type MarkdownDraftStatus = 'draft' | 'finalized' | 'superseded' | 'awaiting_item_revision';

export type DocxExportStatus =
  | 'converting'
  | 'succeeded'
  | 'failed'
  | 'check_rejected'
  | 'baseline_confirmed'
  | 'manual_fallback';

export interface TemplateSectionRead {
  key: string;
  number: string;
  title: string;
  level: number;
  purpose: string;
  content_types: string[];
  required: boolean;
  repeatable: boolean;
  missing_policy: string;
  boilerplate?: string | null;
  examples?: string[]; // 章节样例（P1 加法字段；复制起草反填/AI 起草少样本）
  /**
   * 撰稿时是否提供「从目录选取」引用标准的入口（T20260721）。
   * 判定在后端单点完成（标题像参考资料类 ∧ 支持人工撰稿），前端只读这个标志——
   * 不要在前端按章节 key 或标题另判一次。
   */
  standards_pickable?: boolean;
}

export interface TemplateDescriptorRead {
  template_ref: string;
  schema_version?: string | null;
  title?: string | null;
  description?: string | null;
  export_binding?: Record<string, unknown> | null;
  sections: TemplateSectionRead[];
  error?: string | null;
}

export interface CandidateItemRead {
  item_ref: string;
  req_no: string;
  expression: string;
  req_type: RequirementItemType;
  status: RequirementItemStatus;
  version_no: string;
}

export interface CandidateMaterialRead {
  material_ref: string;
  source_note: string;
  excerpt: string;
  source_version: number;
}

export interface CandidateChartRead {
  chart_ref: string;
  title: string;
  chart_type: string;
  format: string;
  status: string;
  draft_version: number;
  source_count: number;
  confirmed_at?: string | null;
}

export interface TraceBindingSummaryRead {
  effective: number;
  pre_established: number;
  suspect: number;
}

export interface CandidateAssetsRead {
  items: CandidateItemRead[];
  materials: CandidateMaterialRead[];
  charts: CandidateChartRead[];
  traces: string[];
  trace_summary?: TraceBindingSummaryRead | null;
  pending_item_count: number;
}

export interface DocIndexEntryRead {
  section_key: string;
  asset_type: SlotAssetType;
  asset_ref?: string | null;
  asset_version?: string;
  order_no?: number;
}

export interface SlotStatusRead {
  section_key: string;
  required: boolean;
  satisfied: boolean;
  filled_count: number;
  missing_reason?: string | null;
  rebuild_entry?: string | null;
}

export interface MissingItemRead {
  section_key: string;
  section_title: string;
  reason: string;
  rebuild_entry: string;
}

export interface MarkdownPatchRead {
  patch_ref: string;
  impact: EditImpact;
  before_text: string;
  after_text: string;
  bound_item_ref?: string | null;
  reflow_item_ref?: string | null;
  status: string;
  note?: string | null;
}

export interface SourceBindingRead {
  start_line: number;
  end_line: number;
  kind: 'heading' | 'boilerplate' | 'item' | 'material' | 'chart';
  section_key: string;
  asset_ref?: string | null;
}

export interface DocumentFragmentRead {
  section_key: string;
  section_number: string;
  section_title: string;
  start_line: number;
  end_line: number;
  markdown: string;
}

export interface AssetFragmentRead {
  project_ref: string;
  asset_type: 'requirement_item' | 'chart';
  asset_ref: string;
  document_ref?: string | null;
  document_title?: string | null;
  document_status?: string | null;
  draft_ref?: string | null;
  draft_version?: number | null;
  draft_status?: MarkdownDraftStatus | null;
  index_version?: number | null;
  in_current_index: boolean;
  baseline_ref?: string | null;
  fragments: DocumentFragmentRead[];
  next_action?: string | null;
}

export interface MarkdownDraftRead {
  draft_ref: string;
  version_no: number;
  index_version: number;
  status: MarkdownDraftStatus;
  can_export: boolean;
  content: string;
  source_bindings: SourceBindingRead[];
  block_reasons: string[];
  patches: MarkdownPatchRead[];
  finalized_by?: string | null;
  finalized_at?: string | null;
}

export interface DocxExportRead {
  export_ref: string;
  draft_ref: string;
  status: DocxExportStatus;
  failure_reason?: string | null;
  manual_fallback: boolean;
  check_note?: string | null;
  file_available: boolean;
  created_at: string;
}

export interface ReleaseBaselineRead {
  baseline_ref: string;
  document_ref: string;
  index_version: number;
  draft_ref: string;
  template_ref: string;
  template_schema_version: string;
  export_ref: string;
  manual_fallback: boolean;
  asset_refs: string[];
  confirmed_by: string;
  confirmed_at: string;
  note?: string | null;
}

export interface RequirementDocumentRead {
  document_ref: string;
  doc_type: string;
  title: string;
  template_ref: string;
  template_schema_version: string;
  coverage_scope?: string | null;
  status: DocumentStatus;
  blocked_reason?: string | null;
  index_version: number;
}

export interface SectionManuscriptRead {
  section_key: string;
  content: string;
  revision_no: number;
  updated_by: string;
  updated_at: string;
}

export interface SaveManuscriptCommand {
  project_ref: string;
  template_ref?: string | null;
  section_key: string;
  content: string;
  operator_ref: string;
}

export interface CandidatePreviewRead {
  asset_type: string;
  asset_ref: string;
  title: string;
  markdown: string;
}

/** 某个可撰稿章节的 AI 起草依据计数（口径同后端起草服务实际喂给模型的输入）。 */
export interface SectionDraftBasisRead {
  section_key: string;
  asset_count: number;
  example_count: number;
}

/** AEP-110 起草结果信封：起草成功与模型拒绝都是 HTTP 200。 */
export interface SectionDraftResultRead {
  status: 'drafted' | 'declined';
  manuscript?: SectionManuscriptRead | null;
  reason?: string | null;
}

export interface PublicationWorkspaceRead {
  project_ref: string;
  document?: RequirementDocumentRead | null;
  template: TemplateDescriptorRead;
  candidates: CandidateAssetsRead;
  manuscripts: SectionManuscriptRead[];
  /** 每个可 AI 起草章节的起草依据计数（零依据章节据此在点击前提示）。 */
  draft_basis?: SectionDraftBasisRead[];
  index_entries: DocIndexEntryRead[];
  slot_status: SlotStatusRead[];
  missing_list: MissingItemRead[];
  markdown?: MarkdownDraftRead | null;
  exports: DocxExportRead[];
  baseline?: ReleaseBaselineRead | null;
  next_action?: string | null;
}

export interface SaveIndexCommand {
  project_ref: string;
  template_ref?: string | null;
  coverage_scope?: string | null;
  entries: DocIndexEntryRead[];
  operator_ref: string;
  idempotency_key: string;
}

export interface SaveIndexResult {
  status: 'index_ready' | 'index_blocked' | 'rejected_precheck';
  document_ref?: string | null;
  index_version?: number | null;
  missing_list: MissingItemRead[];
  blocked_reason?: string | null;
  next_action?: string | null;
}

export interface GenerateMarkdownCommand {
  project_ref: string;
  operator_ref: string;
  idempotency_key: string;
}

export interface MarkdownEditCommand {
  project_ref: string;
  draft_ref: string;
  content: string;
  operator_ref: string;
}

export interface MarkdownEditResult {
  status: string;
  draft_ref: string;
  patches: MarkdownPatchRead[];
  can_finalize: boolean;
  block_reasons: string[];
  pending_item_refs: string[];
  next_action?: string | null;
}

export interface FinalizeMarkdownCommand {
  project_ref: string;
  draft_ref: string;
  confirm_reflow?: boolean;
  operator_ref: string;
  idempotency_key: string;
}

export interface FinalizeMarkdownResult {
  status: 'finalized' | 'pending_item_confirmation' | 'item_revision_reflowed' | 'blocked';
  draft_ref: string;
  pending_items: MarkdownPatchRead[];
  reflowed_item_refs: string[];
  block_reasons: string[];
  next_action?: string | null;
}

export interface StartDocxExportResult {
  status: 'submitted' | 'rejected_precheck';
  export_ref?: string | null;
  agent_run_ref?: string | null;
  next_action?: string | null;
}

export interface ConfirmBaselineResult {
  status: 'confirmed' | 'rejected_precheck';
  baseline_ref?: string | null;
  next_action?: string | null;
}

export interface ItemConfirmResult {
  status: string;
  item_ref: string;
  item_status: RequirementItemStatus;
  next_action?: string | null;
}

function base(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/publication`;
}

export const publicationApi = {
  getWorkspace(projectId: string, templateRef?: string): Promise<PublicationWorkspaceRead> {
    const query = templateRef ? `?template_ref=${encodeURIComponent(templateRef)}` : '';
    return apiGet<PublicationWorkspaceRead>(`${base(projectId)}/workspace${query}`);
  },

  saveIndex(projectId: string, command: SaveIndexCommand): Promise<SaveIndexResult> {
    return apiPost<SaveIndexResult>(`${base(projectId)}/index`, command);
  },

  saveManuscript(projectId: string, command: SaveManuscriptCommand): Promise<SectionManuscriptRead> {
    return apiPost<SectionManuscriptRead>(`${base(projectId)}/manuscripts`, command);
  },

  /**
   * AEP-110：为 authored_text 章节 AI 起草初稿（写撰稿阶段，人工可改可确认）。
   *
   * 后端回的是信封，这里在 api 层拆开：起草成功 resolve 撰稿；模型拒绝 reject 一个
   * `ManuscriptDeclinedError`。之所以拆在这层而不是把信封透传给界面——调用链上游只取
   * `content`，透传信封会逼上游每一层都判一次 status；抛错则原样穿过上游，由撰稿抽屉一处
   * 捕获并渲染一等回执。
   */
  async draftManuscript(
    projectId: string,
    sectionKey: string,
    command: { project_ref: string; template_ref?: string | null; operator_ref: string },
  ): Promise<SectionManuscriptRead> {
    const result = await apiPost<SectionDraftResultRead>(
      `${base(projectId)}/manuscripts/${encodeURIComponent(sectionKey)}/draft`,
      command,
    );
    if (result.status === 'declined' || !result.manuscript) {
      throw new ManuscriptDeclinedError(result.reason ?? '');
    }
    return result.manuscript;
  },

  candidatePreview(
    projectId: string,
    assetType: 'requirement_item' | 'chart' | 'material',
    assetRef: string,
  ): Promise<CandidatePreviewRead> {
    const query = `?asset_type=${encodeURIComponent(assetType)}&asset_ref=${encodeURIComponent(assetRef)}`;
    return apiGet<CandidatePreviewRead>(`${base(projectId)}/candidate-preview${query}`);
  },

  getAssetFragment(
    projectId: string,
    assetType: 'requirement_item' | 'chart',
    assetRef: string,
  ): Promise<AssetFragmentRead> {
    const query = `?asset_type=${encodeURIComponent(assetType)}&asset_ref=${encodeURIComponent(assetRef)}`;
    return apiGet<AssetFragmentRead>(`${base(projectId)}/asset-fragment${query}`);
  },

  generateMarkdown(projectId: string, command: GenerateMarkdownCommand): Promise<MarkdownDraftRead> {
    return apiPost<MarkdownDraftRead>(`${base(projectId)}/markdown/generate`, command);
  },

  recordEdit(projectId: string, command: MarkdownEditCommand): Promise<MarkdownEditResult> {
    return apiPost<MarkdownEditResult>(`${base(projectId)}/markdown/edit`, command);
  },

  finalizeMarkdown(projectId: string, command: FinalizeMarkdownCommand): Promise<FinalizeMarkdownResult> {
    return apiPost<FinalizeMarkdownResult>(`${base(projectId)}/markdown/finalize`, command);
  },

  reopenIndex(projectId: string, operatorRef: string): Promise<RequirementDocumentRead> {
    return apiPost<RequirementDocumentRead>(`${base(projectId)}/markdown/reopen-index`, {
      project_ref: projectId,
      operator_ref: operatorRef,
    });
  },

  confirmItem(projectId: string, itemRef: string, operatorRef: string): Promise<ItemConfirmResult> {
    return apiPost<ItemConfirmResult>(`${base(projectId)}/items/${encodeURIComponent(itemRef)}/confirm`, {
      project_ref: projectId,
      item_ref: itemRef,
      operator_ref: operatorRef,
      idempotency_key: `item-confirm-${itemRef}`,
    });
  },

  startExport(projectId: string, draftRef: string, operatorRef: string, idempotencyKey: string): Promise<StartDocxExportResult> {
    return apiPost<StartDocxExportResult>(`${base(projectId)}/exports`, {
      project_ref: projectId,
      draft_ref: draftRef,
      operator_ref: operatorRef,
      idempotency_key: idempotencyKey,
    });
  },

  reportCheck(projectId: string, exportRef: string, passed: boolean, note: string, operatorRef: string): Promise<DocxExportRead> {
    return apiPost<DocxExportRead>(`${base(projectId)}/exports/${encodeURIComponent(exportRef)}/check`, {
      project_ref: projectId,
      export_ref: exportRef,
      passed,
      note,
      operator_ref: operatorRef,
    });
  },

  registerManualFallback(projectId: string, draftRef: string, reason: string, operatorRef: string, idempotencyKey: string): Promise<DocxExportRead> {
    return apiPost<DocxExportRead>(`${base(projectId)}/exports/manual-fallback`, {
      project_ref: projectId,
      draft_ref: draftRef,
      reason,
      operator_ref: operatorRef,
      idempotency_key: idempotencyKey,
    });
  },

  confirmBaseline(projectId: string, exportRef: string, note: string, operatorRef: string, idempotencyKey: string): Promise<ConfirmBaselineResult> {
    return apiPost<ConfirmBaselineResult>(`${base(projectId)}/exports/${encodeURIComponent(exportRef)}/confirm-baseline`, {
      project_ref: projectId,
      export_ref: exportRef,
      note,
      operator_ref: operatorRef,
      idempotency_key: idempotencyKey,
    });
  },

  exportFileUrl(projectId: string, exportRef: string): string {
    return `/api${base(projectId)}/exports/${encodeURIComponent(exportRef)}/file`;
  },

  /** 取生成好的候选/基线 docx 字节流（供在线预览渲染）；与 exportFileUrl 同源。 */
  fetchExportBlob(projectId: string, exportRef: string): Promise<Blob> {
    return apiGetBlob(`${base(projectId)}/exports/${encodeURIComponent(exportRef)}/file`);
  },

  exportPdfUrl(projectId: string, exportRef: string): string {
    return `/api${base(projectId)}/exports/${encodeURIComponent(exportRef)}/pdf`;
  },

  /** 精确预览探活：HEAD 触发/命中转换缓存，2xx 表示可用（iframe 可直连）；未装 LibreOffice 抛 503。 */
  probeExportPdf(projectId: string, exportRef: string): Promise<void> {
    return apiHead(`${base(projectId)}/exports/${encodeURIComponent(exportRef)}/pdf`);
  },
};
