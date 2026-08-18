import { describe, expect, it } from 'vitest';
import {
  diagramChartListFixture,
  diagramTraceLinksFixture,
  diagramWorkspaceFixture,
} from '../src/fixtures/diagram';
import {
  buildChartRows,
  buildChartWorkspaceVM,
  buildTraceLinkRows,
  chartStatusMeta,
  parseMarkdownTable,
  traceStatusMeta,
  typeFormatOptions,
} from '../src/view-models/diagram';

describe('diagram view-models', () => {
  it('图表列表行投影：状态中文标签与语气', () => {
    const rows = buildChartRows(diagramChartListFixture);
    expect(rows[0].statusLabel).toBe('草稿中');
    expect(rows[0].statusTone).toBe('processing');
    expect(rows[0].typeLabel).toBe('流程图');
    expect(rows[1].statusLabel).toBe('已确认');
    expect(rows[1].formatLabel).toBe('Markdown 表格');
  });

  it('工作区 VM 只读投影后端门禁：不在前端复算准入', () => {
    const vm = buildChartWorkspaceVM(diagramWorkspaceFixture);
    expect(vm.isPending).toBe(true);
    expect(vm.canSubmitConfirmation).toBe(false);
    expect(vm.blockedReasons).toEqual(['存在未复核的核对发现项']);
    // 待确认冻结编辑：动作可用性透传自 available_actions
    expect(vm.actionEnabled.apply_source_change).toBe(false);
    expect(vm.actionDisabledReason.apply_source_change).toContain('冻结');
    expect(vm.actionEnabled.start_verification).toBe(true);
  });

  it('preview_capability 由后端派生透传（mermaid 可预览）', () => {
    const vm = buildChartWorkspaceVM(diagramWorkspaceFixture);
    expect(vm.previewable).toBe(true);
    const plantuml = buildChartWorkspaceVM({
      ...diagramWorkspaceFixture,
      format: 'plantuml',
      preview_capability: 'not_previewable',
    });
    expect(plantuml.previewable).toBe(false);
  });

  it('追溯行投影：四态标签齐全且区分', () => {
    const rows = buildTraceLinkRows(diagramTraceLinksFixture);
    expect(rows[0].statusLabel).toBe('预建立');
    expect(rows[1].statusLabel).toBe('有效');
    expect(rows[1].establishedAt).not.toBeNull();
    expect(Object.keys(traceStatusMeta)).toEqual([
      'pre_established',
      'effective',
      'suspect_pending_review',
      'invalid',
    ]);
  });

  it('图表状态元数据覆盖五态（含作废）', () => {
    expect(Object.keys(chartStatusMeta)).toEqual([
      'draft',
      'pending_confirmation',
      'confirmed',
      'returned_for_revision',
      'voided',
    ]);
  });

  it('类型 × 表达方式候选矩阵：表格类只开放 Markdown 表格', () => {
    expect(typeFormatOptions.decision_table).toEqual(['markdown_table']);
    expect(typeFormatOptions.flowchart).toContain('mermaid');
  });

  it('Markdown 表格解析：受控表格转单元格矩阵；非表格返回 null', () => {
    const cells = parseMarkdownTable('| 条件 | 动作 |\n|---|---|\n| A | B |');
    expect(cells).toEqual([
      ['条件', '动作'],
      ['A', 'B'],
    ]);
    expect(parseMarkdownTable('flowchart TD')).toBeNull();
  });
});
