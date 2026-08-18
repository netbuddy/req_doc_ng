/**
 * 总览流程行 VM：终结态处置投影（OVW-001 修订 2026-07-10，同日位置修正）。
 * 行动作全量统一为「恢复」：dismissable=true 的终结态行点「恢复」＝预填重提（AEP-112）；
 * dismissable 是后端事实（行3/4），前端只映射不推断；死路行（行8）保持仅可查看。
 * 折叠 source_note 还原（预填模式）在 parseFoldedSourceNote 测试组。
 */
import { describe, expect, it } from 'vitest';
import type { RequirementFlowRead } from '../src/api/overview';
import { toOverviewFlowRowVM } from '../src/view-models/overview';
import { parseFoldedSourceNote } from '../src/view-models/requirement-management';

function flowRead(overrides: Partial<RequirementFlowRead> = {}): RequirementFlowRead {
  return {
    flow_id: 'ctx-1',
    title: '访谈纪要',
    summary: '材料接入 · 停靠',
    current_stage: 'intake',
    resume_stage: 'intake',
    resumable: false,
    dismissable: false,
    stages: [
      { stage: 'intake', status: 'stopped', detail: '需补充：补充后重新提交为新流程' },
      { stage: 'analysis', status: 'not_started', detail: null },
      { stage: 'itemFormation', status: 'not_started', detail: null },
      { stage: 'itemReview', status: 'not_started', detail: '待接入（SCN-003）' },
    ],
    intake_context_ref: 'ctx-1',
    material_ref: null,
    parse_context_ref: null,
    formation_context_ref: null,
    updated_at: '2026-07-10T09:00:00+00:00',
    ...overrides,
  };
}

describe('toOverviewFlowRowVM 终结态处置映射（总览行统一「恢复」）', () => {
  it('终结态行（后端 dismissable=true）映射为可处置（行内呈现为统一「恢复」＝预填重提）', () => {
    const vm = toOverviewFlowRowVM(flowRead({ dismissable: true }));
    expect(vm.dismissable).toBe(true);
    expect(vm.resumable).toBe(false);
  });

  it('死路行（resumable=false 且 dismissable=false）保持不可处置（仅可查看）', () => {
    const vm = toOverviewFlowRowVM(flowRead());
    expect(vm.dismissable).toBe(false);
    expect(vm.resumable).toBe(false);
  });

  it('dismissable 缺省（旧后端投影）按 false 兜底', () => {
    const read = flowRead();
    delete (read as Record<string, unknown>).dismissable;
    expect(toOverviewFlowRowVM(read).dismissable).toBe(false);
  });
});

describe('parseFoldedSourceNote 折叠字段还原', () => {
  it('标准折叠串还原为表单字段，提交人不回填', () => {
    const parsed = parseFoldedSourceNote(
      '来源类型:客户访谈；来源对象:6 月客户访谈纪要；来源时间:2026-06-30；提交人:张三；来源说明:补充第二批',
    );
    expect(parsed).toEqual({
      sourceType: '客户访谈',
      sourceName: '6 月客户访谈纪要',
      sourceTime: '2026-06-30',
      sourceNote: '补充第二批',
    });
  });

  it('缺省占位值（未命名材料/未填写/无）还原为空串', () => {
    const parsed = parseFoldedSourceNote(
      '来源类型:会议纪要；来源对象:未命名材料；来源时间:未填写；提交人:张三；来源说明:无',
    );
    expect(parsed).toEqual({
      sourceType: '会议纪要',
      sourceName: '',
      sourceTime: '',
      sourceNote: '',
    });
  });

  it('来源说明含分号时整段保留（末字段截取）', () => {
    const parsed = parseFoldedSourceNote(
      '来源类型:会议纪要；来源对象:纪要；来源时间:未填写；提交人:张三；来源说明:一句；另一句',
    );
    expect(parsed.sourceNote).toBe('一句；另一句');
  });

  it('非折叠格式不猜测：整串落来源说明', () => {
    expect(parseFoldedSourceNote('手工录入的历史备注')).toEqual({
      sourceNote: '手工录入的历史备注',
    });
  });

  it('空串返回空对象', () => {
    expect(parseFoldedSourceNote('  ')).toEqual({});
  });
});
