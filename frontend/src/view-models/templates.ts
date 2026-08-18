import type { TemplateDraftRead, TemplateRegistryRead } from '../api/templates';
import type { TemplateDescriptorRead, TemplateSectionRead } from '../api/publication';
import type { BadgeTone } from './common';
import { requirementItemTypeText } from './requirement-item-formation';
import { formatAbsoluteTime } from './time';

// ---- 模板注册表 ViewModel（配置域：登记快照列表 + 结构预览）----

export interface TemplateRowVM {
  registryRef: string;
  templateKey: string;
  versionText: string;
  name: string;
  schemaVersion: string;
  hashShort: string;
  sourceText: string;
  sourceTone: BadgeTone;
  statusText: string;
  statusTone: BadgeTone;
  registeredBy: string;
  registeredAtText: string;
  canDisable: boolean; // 内置模板不可停用
  canEnable: boolean;
}

export function buildTemplateRows(rows: TemplateRegistryRead[]): TemplateRowVM[] {
  return rows.map((row) => ({
    registryRef: row.registry_ref,
    templateKey: row.template_key,
    versionText: `v${row.version_no}`,
    name: row.name,
    schemaVersion: row.schema_version,
    hashShort: row.content_hash.slice(0, 12),
    sourceText: row.source === 'builtin' ? '内置' : '登记',
    sourceTone: row.source === 'builtin' ? 'processing' : 'neutral',
    statusText: row.status === 'active' ? '可用' : '已停用',
    statusTone: row.status === 'active' ? 'success' : 'warning',
    registeredBy: row.registered_by,
    registeredAtText: formatAbsoluteTime(row.registered_at),
    canDisable: row.status === 'active' && row.source !== 'builtin',
    canEnable: row.status === 'disabled',
  }));
}

function latestActiveByTemplateKey(rows: TemplateRegistryRead[]): TemplateRegistryRead[] {
  const latest = new Map<string, TemplateRegistryRead>();
  for (const row of rows) {
    if (row.status !== 'active') continue;
    const existing = latest.get(row.template_key);
    if (!existing || row.version_no > existing.version_no) {
      latest.set(row.template_key, row);
    }
  }
  return Array.from(latest.values());
}

/** 发布页模板选择器选项：每个 template_key 取最新 active 版本。 */
export function buildTemplateOptions(rows: TemplateRegistryRead[]): { value: string; label: string }[] {
  return latestActiveByTemplateKey(rows).map((row) => ({
    value: row.template_key,
    label: `${row.name}（${row.template_key} v${row.version_no}）`,
  }));
}

export interface TemplateChoiceVM {
  templateId: string; // 发布 API 入参仍叫 template_ref，值为 template_key
  registryRef: string; // 最新 active 版本注册行（预览/选用锚点）
  name: string;
  versionText: string;
  schemaVersion: string;
  sourceText: string;
  sourceTone: BadgeTone;
  hashShort: string;
}

/** 模板选择步卡片：每个 template_key 取最新 active 版本。 */
export function buildTemplateChoices(rows: TemplateRegistryRead[]): TemplateChoiceVM[] {
  return latestActiveByTemplateKey(rows).map((row) => ({
    templateId: row.template_key,
    registryRef: row.registry_ref,
    name: row.name,
    versionText: `v${row.version_no}`,
    schemaVersion: row.schema_version,
    sourceText: row.source === 'builtin' ? '内置' : '登记',
    sourceTone: row.source === 'builtin' ? 'processing' : 'neutral',
    hashShort: row.content_hash.slice(0, 12),
  }));
}

// ---- 模板定制草稿 ViewModel（暂存工作态列表）----

export interface TemplateDraftRowVM {
  draftRef: string;
  name: string;
  originText: string;
  updatedAtText: string;
  createdBy: string;
}

const DRAFT_ORIGIN_TEXT: Record<string, string> = {
  blank: '空白起草',
  copy: '复制起草',
  edit: '编辑模板',
};

export function buildDraftRows(rows: TemplateDraftRead[]): TemplateDraftRowVM[] {
  return rows.map((row) => ({
    draftRef: row.draft_ref,
    name: row.name || '（未命名模板草稿）',
    originText: DRAFT_ORIGIN_TEXT[row.origin] ?? row.origin,
    updatedAtText: formatAbsoluteTime(row.updated_at),
    createdBy: row.created_by,
  }));
}

export interface TemplateSectionPreviewVM {
  key: string;
  indent: number;
  headingText: string;
  purpose: string;
  slotText: string | null; // 槽位说明（非槽位章节为 null）
  requiredText: string | null;
  missingPolicyText: string | null;
  boilerplate: string | null;
}

function slotText(section: TemplateSectionRead): string | null {
  const itemTypes = section.content_types
    .filter((c) => c.startsWith('requirement_item:'))
    .map((c) => requirementItemTypeText(c.split(':')[1] as never));
  if (itemTypes.length > 0) return `${itemTypes.join('/')}条目槽位`;
  if (section.content_types.includes('chart')) return '图表槽位';
  if (section.content_types.includes('material')) return '支撑材料槽位';
  if (section.content_types.includes('boilerplate')) return '模板文本';
  return null;
}

export function buildTemplatePreview(descriptor: TemplateDescriptorRead): TemplateSectionPreviewVM[] {
  return descriptor.sections.map((section) => ({
    key: section.key,
    indent: section.level - 1,
    headingText: `${section.number} ${section.title}`,
    purpose: section.purpose,
    slotText: slotText(section),
    requiredText: section.content_types.length > 0 ? (section.required ? '必填' : '可选') : null,
    missingPolicyText: section.content_types.length > 0
      ? (section.missing_policy === 'block' ? '缺失阻塞' : '缺失跳过')
      : null,
    boilerplate: section.boilerplate ?? null,
  }));
}
