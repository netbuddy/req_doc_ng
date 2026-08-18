/**
 * 维护列表/需求卡片 VM：29148 属性补齐展示投影（优先级/验证方式标签、缺失警示、gap 筛选配置）。
 * 权威校验与筛选逻辑在后端（tests/backend test_asset_catalog.py）；本文件只测展示投影。
 * 事实源：docs/20 §5.7（LDM-007 属性补齐）、docs/40 条目完备性档案与结构投影.md。
 */
import { describe, expect, it } from 'vitest';
import type { ItemMaintenanceCardRead } from '../src/api/assets';
import type { ItemFormationWorkspaceRead } from '../src/api/item-formation';
import type { RequirementFlowRead } from '../src/api/overview';
import {
  priorityText,
  verificationMethodText,
} from '../src/view-models/requirement-item-formation';
import {
  buildRequirementCardVM,
  focusFormationWorkspaceOnItem,
  formationWorkspaceContainsItem,
  MAINTENANCE_GAP_FILTERS,
  reviewBatchCandidates,
} from '../src/view-models/requirement-management';

describe('29148 属性标签口径', () => {
  it('验证方式多选 → 中文顿号连接；空值为 null 由调用侧给缺失文案', () => {
    expect(verificationMethodText(['demonstration', 'analysis'])).toBe('演示、分析');
    expect(verificationMethodText([])).toBeNull();
    expect(verificationMethodText(undefined)).toBeNull();
  });

  it('优先级三级中文标签；未知码原样透出', () => {
    expect(priorityText('high')).toBe('高');
    expect(priorityText(null)).toBeNull();
    expect(priorityText('urgent')).toBe('urgent');
  });
});

describe('时刻落本地时区（issue #21：原 timeText 走 slice(0,16) 直示 UTC 原串）', () => {
  // TZ 由 vite.config 钉 Asia/Shanghai；17:30Z ⇒ 次日 01:30。
  // 旧 slice 手法会给出 UTC 原串 17:30（时辰错、日期也错），在此必挂。
  const CROSS_DAY = '2026-07-04T17:30:00+00:00';

  const timeCard: ItemMaintenanceCardRead = {
    ref: 'item-1',
    req_no: 'FR-001',
    expression: '系统应支持导出 docx',
    req_type: 'functional',
    status: 'pending_confirmation',
    updated_at: CROSS_DAY,
    verification_method: ['test'],
    verification_note: null,
    priority: null,
    source_evidence: [],
    revisions: [
      {
        field_key: 'priority',
        before_value: '',
        after_value: 'high',
        revision_mode: 'manual',
        reason: null,
        operator_ref: 'U1',
        created_at: CROSS_DAY,
      },
    ],
    related: { charts: 0, documents: 0, trace_effective: 0, trace_suspect: 0 },
  };

  it('卡片「最近更新」与修订留痕时刻：同口径落本地', () => {
    const vm = buildRequirementCardVM(timeCard);
    expect(vm.facts).toContainEqual({
      key: 'updated', label: '最近更新', group: 'registry', value: '2026-07-05 01:30',
    });
    expect(vm.revisions[0].timeText).toBe('2026-07-05 01:30');
  });
});

describe('维护列表警示投影', () => {
  it('gap 筛选配置：全部/缺验收准则/缺优先级（key=后端契约）', () => {
    expect(MAINTENANCE_GAP_FILTERS.map((f) => f.key)).toEqual([
      'all',
      'verification_note',
      'priority',
    ]);
  });
});

describe('需求卡片属性投影', () => {
  const card: ItemMaintenanceCardRead = {
    ref: 'item-1',
    req_no: 'FR-001',
    expression: '系统应支持导出 docx',
    req_type: 'functional',
    status: 'pending_confirmation',
    updated_at: '2026-07-06T10:00:00',
    verification_method: ['test', 'analysis'],
    verification_note: null,
    priority: null,
    source_evidence: [],
    revisions: [
      {
        field_key: 'priority',
        before_value: '',
        after_value: 'high',
        revision_mode: 'manual',
        reason: null,
        operator_ref: 'U1',
        created_at: '2026-07-06T10:00:00',
      },
    ],
    related: { charts: 0, documents: 0, trace_effective: 0, trace_suspect: 0 },
  };

  it('facts 含优先级/验证方式；缺失给"未设定/未建议"文案', () => {
    const vm = buildRequirementCardVM(card);
    const facts = Object.fromEntries(vm.facts.map((f) => [f.key, f.value]));
    expect(facts.priority).toBe('未设定');
    expect(facts.verification).toBe('测试、分析');
  });

  it('门禁就绪区：验收准则缺失 → warning（仅警示，不阻断）；修订留痕字段中文标签', () => {
    const vm = buildRequirementCardVM(card);
    const readiness = Object.fromEntries(vm.gate.readinessItems.map((i) => [i.key, i]));
    expect(readiness.verification.tone).toBe('warning');
    expect(readiness.priority.tone).toBe('warning');
    expect(vm.revisions[0].fieldText).toBe('优先级');
  });
});

describe('修订留痕：人工确认背书白话渲染（C10：管理台不再露内部键名、不误标「人工修订」）', () => {
  const cardWithAttestation: ItemMaintenanceCardRead = {
    ref: 'item-2',
    req_no: 'FR-002',
    expression: '系统应支持批量导入',
    req_type: 'functional',
    status: 'pending_confirmation',
    updated_at: '2026-07-06T10:00:00',
    verification_method: [],
    verification_note: null,
    priority: null,
    source_evidence: [],
    revisions: [
      {
        field_key: 'source_attestation',
        before_value: '旧值',
        after_value: '已人工确认为真实需求（材料未记载）',
        revision_mode: 'manual',
        reason: '客户口头确认，纪要漏记',
        operator_ref: 'U1',
        created_at: '2026-07-06T10:00:00',
      },
      {
        field_key: 'expression',
        before_value: '旧表达',
        after_value: '系统应支持批量导入',
        revision_mode: 'manual',
        reason: null,
        operator_ref: 'U1',
        created_at: '2026-07-06T11:00:00',
      },
    ],
    related: { charts: 0, documents: 0, trace_effective: 0, trace_suspect: 0 },
  };

  it('背书行：isAttestation=true、字段列显「人工确认」、不显示改前值、方式列不标「人工修订」', () => {
    const att = buildRequirementCardVM(cardWithAttestation).revisions[0];
    expect(att.isAttestation).toBe(true);
    expect(att.fieldText).toBe('人工确认');
    expect(att.fieldText).not.toContain('source_attestation');
    expect(att.beforeText).toBe('');
    expect(att.afterText).toBe('已人工确认为真实需求（材料未记载）');
    expect(att.modeText).toBe('人工确认');
    expect(att.modeText).not.toContain('人工修订');
  });

  it('普通字段行不受影响：isAttestation=false，字段/改前/方式照常', () => {
    const normal = buildRequirementCardVM(cardWithAttestation).revisions[1];
    expect(normal.isAttestation).toBe(false);
    expect(normal.fieldText).toBe('需求表达');
    expect(normal.beforeText).toBe('旧表达');
    expect(normal.modeText).toBe('人工修订');
  });
});

// ---- 台内评审入口（issue #5）：批次候选与条目归属判定 ----

describe('台内评审入口（issue #5）', () => {
  const flow = (over: Partial<RequirementFlowRead>): RequirementFlowRead => ({
    flow_id: 'flow-1',
    title: '流程',
    current_stage: 'itemReview',
    resume_stage: 'itemFormation',
    resumable: true,
    dismissable: false,
    stages: [],
    intake_context_ref: 'intake-1',
    updated_at: '2026-07-12T10:00:00',
    ...over,
  });

  it('批次候选：过滤无形成上下文的流程，保持近更优先顺序并去重', () => {
    const flows = [
      flow({ flow_id: 'f1', formation_context_ref: 'ctx-a' }),
      flow({ flow_id: 'f2', formation_context_ref: null }),
      flow({ flow_id: 'f3', formation_context_ref: 'ctx-b' }),
      flow({ flow_id: 'f4', formation_context_ref: 'ctx-a' }),
      flow({ flow_id: 'f5' }),
    ];
    expect(reviewBatchCandidates(flows)).toEqual(['ctx-a', 'ctx-b']);
    expect(reviewBatchCandidates([])).toEqual([]);
  });

  it('条目归属：按 pending_items.item_ref 判定批次是否含目标条目', () => {
    const workspace = {
      pending_items: [{ item_ref: 'item-1' }, { item_ref: 'item-2' }],
    } as unknown as ItemFormationWorkspaceRead;
    expect(formationWorkspaceContainsItem(workspace, 'item-2')).toBe(true);
    expect(formationWorkspaceContainsItem(workspace, 'item-9')).toBe(false);
    expect(
      formationWorkspaceContainsItem(
        { pending_items: [] } as unknown as ItemFormationWorkspaceRead,
        'item-1',
      ),
    ).toBe(false);
  });
});

describe('focusFormationWorkspaceOnItem（激活线程=所选条目）', () => {
  const workspace = {
    selected_item_ref: 'item-1',
    pending_items: [{ item_ref: 'item-1' }, { item_ref: 'item-2' }, { item_ref: 'item-3' }],
  } as unknown as ItemFormationWorkspaceRead;

  it('目标条目提到 pending_items 首位并回写 selected_item_ref；不改原对象', () => {
    const focused = focusFormationWorkspaceOnItem(workspace, 'item-3');
    expect(focused.pending_items.map((i) => i.item_ref)).toEqual(['item-3', 'item-1', 'item-2']);
    expect(focused.selected_item_ref).toBe('item-3');
    expect(workspace.pending_items.map((i) => i.item_ref)).toEqual(['item-1', 'item-2', 'item-3']);
  });

  it('目标不在批次内 → 原样返回', () => {
    expect(focusFormationWorkspaceOnItem(workspace, 'item-9')).toBe(workspace);
  });
});
