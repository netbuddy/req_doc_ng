import { apiDelete, apiGet, apiPost, apiPut } from './client';
import type { TemplateDescriptorRead } from './publication';

// ---- 模板注册表 DTO（与 backend/app/api/schemas.py 同名对齐）----

export type TemplateSource = 'builtin' | 'registered';
export type TemplateStatus = 'active' | 'disabled';

export interface TemplateRegistryRead {
  registry_ref: string;
  template_key: string;
  version_no: number;
  name: string;
  schema_version: string;
  doc_type: string;
  content_hash: string;
  source: TemplateSource;
  status: TemplateStatus;
  registered_by: string;
  registered_at: string;
}

export interface TemplateRegistryDetailRead extends TemplateRegistryRead {
  descriptor: TemplateDescriptorRead;
}

export interface TemplateRegisterCommand {
  content: string;
  name?: string | null;
  operator_ref: string;
  idempotency_key: string;
}

export interface TemplateValidationRead {
  ok: boolean;
  error?: string | null;
  descriptor?: TemplateDescriptorRead | null;
}

// ---- 模板定制草稿（工作态暂存：可变可删，未送检不占版本号）----

export type TemplateDraftOrigin = 'blank' | 'copy' | 'edit';

export interface TemplateDraftRead {
  draft_ref: string;
  name: string;
  payload: string; // 定制器状态 JSON 信封
  origin: TemplateDraftOrigin;
  source_registry_ref?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface TemplateDraftSaveCommand {
  name: string;
  payload: string;
  origin?: TemplateDraftOrigin;
  source_registry_ref?: string | null;
  operator_ref: string;
}

export const templatesApi = {
  validate(content: string): Promise<TemplateValidationRead> {
    return apiPost<TemplateValidationRead>('/templates/validate', { content });
  },

  list(): Promise<TemplateRegistryRead[]> {
    return apiGet<TemplateRegistryRead[]>('/templates');
  },

  register(command: TemplateRegisterCommand): Promise<TemplateRegistryRead> {
    return apiPost<TemplateRegistryRead>('/templates', command);
  },

  getDetail(registryRef: string): Promise<TemplateRegistryDetailRead> {
    return apiGet<TemplateRegistryDetailRead>(`/templates/${encodeURIComponent(registryRef)}`);
  },

  setStatus(registryRef: string, status: TemplateStatus, operatorRef: string): Promise<TemplateRegistryRead> {
    return apiPost<TemplateRegistryRead>(`/templates/${encodeURIComponent(registryRef)}/status`, {
      status,
      operator_ref: operatorRef,
    });
  },

  previewDocxUrl(registryRef: string): string {
    return `/api/templates/${encodeURIComponent(registryRef)}/preview-docx`;
  },

  listDrafts(): Promise<TemplateDraftRead[]> {
    return apiGet<TemplateDraftRead[]>('/template-drafts');
  },

  createDraft(command: TemplateDraftSaveCommand): Promise<TemplateDraftRead> {
    return apiPost<TemplateDraftRead>('/template-drafts', command);
  },

  updateDraft(draftRef: string, command: TemplateDraftSaveCommand): Promise<TemplateDraftRead> {
    return apiPut<TemplateDraftRead>(`/template-drafts/${encodeURIComponent(draftRef)}`, command);
  },

  deleteDraft(draftRef: string, operatorRef: string): Promise<{ ok: boolean }> {
    return apiDelete<{ ok: boolean }>(
      `/template-drafts/${encodeURIComponent(draftRef)}?operator_ref=${encodeURIComponent(operatorRef)}`,
    );
  },
};
