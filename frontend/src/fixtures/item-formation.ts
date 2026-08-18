import type { ItemFormationWorkspaceRead } from '../api/item-formation';

function anchor(materialRef: string, start: number, end: number, exact: string): string {
  return JSON.stringify({ material_ref: materialRef, ranges: [{ start, end, exact, prefix: '', suffix: '' }] });
}

const rawText = [
  '客户要求在异常链路出现时提供诊断提示，并保留来源依据。',
  '诊断提示需要在 5 秒内展示，且给出可执行的处理建议。',
  '系统还应记录处理过程，便于后续追溯与复盘。',
].join('\n');

export const itemFormationWorkspaceFixture: ItemFormationWorkspaceRead = {
  formation_context_ref: '',
  parse_result_ref: null,
  workspace_version: '1',
  material_canvas: {
    material_ref: 'MAT-DEMO',
    title: '客户访谈纪要节选',
    source_note: '前端本地示例工作区，用于条目形成阶段开发验证。',
    source_version: 1,
    raw_text: rawText,
    blocks: [
      { block_id: 'b0', index: 0, start_offset: 0, end_offset: rawText.length, text: rawText },
    ],
    supplements: [],
  },
  eligible_elements: [
    {
      id: 'EL-001',
      element_type: 'functional_requirement',
      knowledge_category: 'requirement',
      content: '异常链路出现时提供诊断提示',
      source_anchor: anchor('MAT-DEMO', 4, 22, rawText.slice(4, 22)),
      confidence: 0.92,
      process_status: 'confirmed',
      version: 1,
      superseded: false,
    },
    {
      id: 'EL-002',
      element_type: 'quality_attribute',
      knowledge_category: 'requirement',
      content: '诊断提示需要在 5 秒内展示',
      source_anchor: anchor('MAT-DEMO', 31, 46, rawText.slice(31, 46)),
      confidence: 0.88,
      process_status: 'confirmed',
      version: 1,
      superseded: false,
    },
    {
      id: 'EL-003',
      element_type: 'functional_requirement',
      knowledge_category: 'requirement',
      content: '记录处理过程以便追溯与复盘',
      source_anchor: anchor('MAT-DEMO', 66, 83, rawText.slice(66, 83)),
      confidence: 0.84,
      process_status: 'confirmed',
      version: 1,
      superseded: false,
    },
  ],
  blocked_elements: [
    {
      id: 'EL-004',
      element_type: 'scenario',
      knowledge_category: 'requirement',
      content: '异常链路出现',
      source_anchor: anchor('MAT-DEMO', 4, 10, rawText.slice(4, 10)),
      confidence: 0.76,
      process_status: 'confirmed',
      version: 1,
      superseded: false,
      formation_role: 'supporting',
      blocked_reason: '支撑或上下文类要素仅作为依据',
    },
  ],
  intent_context: [],
  pending_items: [],
  selected_item_ref: null,
  batch_results: [],
  revision_suggestions: [],
  available_actions: [
    { key: 'start_review', enabled: false, disabled_reason: '尚未形成待确认条目' },
    { key: 'return_to_elements', enabled: true, disabled_reason: null },
  ],
  available_operations: [
    { key: 'start_itemization', enabled: true, disabled_reason: null },
    { key: 'apply_revision', enabled: false, disabled_reason: '尚未形成待确认条目' },
    { key: 'accept_revision_suggestion', enabled: false, disabled_reason: '没有候选修订建议' },
  ],
  next_action: '选择区1要素后点击“生成待确认条目”。',
};
