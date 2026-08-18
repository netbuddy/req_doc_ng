/**
 * 条目评审页线程投影映射器单测（统一 AI 对话控件 P1-02 · 04 篇 §3.1 / 01 篇 §3.3）。
 *
 * 断言重点：结论有效性规则仍在页面侧（复用 buildThread）——站立结论/回执/草案/补充来源出口
 * 一律走 component 逃生舱分部；AI 解释走 markdown 分部（A1 富文本改进）；控件核心零业务感知。
 */
import { describe, expect, it } from 'vitest';
import type {
  ItemReviewWorkspaceRead,
  ReviewRequirementItemRead,
  VerdictRead,
} from '../src/api/item-review';
import type { ChatMessage, ComponentPart } from '../src/chat-widget';
import { itemFormationWorkspaceFixture } from '../src/fixtures/item-formation';
import { createPendingItemsFromElements } from '../src/view-models/requirement-item-formation';
import { buildInitialReviewWorkspace } from '../src/view-models/requirement-item-review';
import { projectItemThread, REVIEW_CARD } from '../src/view-models/item-review-thread';

function initialReviewWorkspace(): ItemReviewWorkspaceRead {
  const formed = createPendingItemsFromElements(
    itemFormationWorkspaceFixture,
    itemFormationWorkspaceFixture.eligible_elements.map((element) => element.id),
  );
  return buildInitialReviewWorkspace(formed);
}

function baseItem(): ReviewRequirementItemRead {
  return initialReviewWorkspace().review_items[0];
}

function verdictOf(partial: Partial<VerdictRead>): VerdictRead {
  return {
    round_ref: 'R-1', round_no: 1, diagnosis_mode: 'standard', trigger: 'user_submit',
    status: 'completed', verdict_kind: 'revise', verdict_summary: '建议修订。',
    findings: [{ finding_ref: 'F-1', finding_type: 'untestable', diagnosis_summary: '缺口径', basis_summary: '' }],
    revision_points: [{ point_ref: 'P1', label: '补口径', finding_index: 0, find: 'A', replace: 'A+', basis: '', group: null }],
    supplement_gaps: [], context_coverage: '', model_result_refs: [],
    invalidated: false, invalidated_reason: null, superseded_by: null,
    adjudication: null, overridden: false, confirm_result: null,
    effective: true, reason: null, created_at: '2026-07-06T00:00:00Z',
    ...partial,
  };
}

/** 取一条消息的首个 component 分部（逃生舱断言辅助）。 */
function componentOf(message: ChatMessage): ComponentPart {
  const part = message.parts[0];
  expect(part.type).toBe('component');
  return part as ComponentPart;
}

describe('projectItemThread', () => {
  it('空线程（无结论/无对话/非补充态）投影为空数组', () => {
    const item: ReviewRequirementItemRead = { ...baseItem(), review_status: 'no_verdict' };
    expect(projectItemThread(item)).toEqual([]);
  });

  it('站立结论卡走 review-verdict 逃生舱，载荷带 itemRef/版本/时间', () => {
    const item: ReviewRequirementItemRead = {
      ...baseItem(),
      review_status: 'awaiting_adjudication',
      current_verdict: verdictOf({ round_ref: 'R-2', round_no: 2, created_at: '2026-07-06T02:00:00Z' }),
    };
    const messages = projectItemThread(item);
    expect(messages).toHaveLength(1);
    const comp = componentOf(messages[0]);
    expect(comp.name).toBe(REVIEW_CARD.verdict);
    expect(comp.props?.itemRef).toBe(item.item_ref);
    expect(comp.props?.itemVersionNo).toBe(item.version_no);
    expect((comp.props?.verdict as VerdictRead).round_ref).toBe('R-2');
    expect(comp.props?.at).toBe('2026-07-06T02:00:00Z');
  });

  it('已裁决历史轮次走 review-receipt 逃生舱；AI 解释走 markdown 分部；时间序合并', () => {
    const item: ReviewRequirementItemRead = {
      ...baseItem(),
      review_status: 'awaiting_adjudication',
      current_verdict: verdictOf({ round_ref: 'R-2', round_no: 2, created_at: '2026-07-06T02:00:00Z' }),
      verdict_history: [
        verdictOf({
          round_ref: 'R-1', effective: false, created_at: '2026-07-06T01:00:00Z',
          adjudication: {
            decision: 'rejected', selected_point_refs: [], excluded_point_refs: [],
            reason: '判定依据不足', operator_ref: 'U1', at: '2026-07-06T01:10:00Z',
          },
        }),
      ],
      dialogue_messages: [{
        message_ref: 'M-1', kind: 'explanation', user_message: '为什么？', text: '**依据**是…',
        in_flight: false, created_at: '2026-07-06T03:00:00Z',
      }],
    };
    const messages = projectItemThread(item);
    // 顺序：receipt（R-1，01:00）→ verdict（R-2，02:00）→ 用户提问 + AI 解释（03:00）
    expect(componentOf(messages[0]).name).toBe(REVIEW_CARD.receipt);
    expect(componentOf(messages[1]).name).toBe(REVIEW_CARD.verdict);
    expect(messages[2].role).toBe('user');
    expect(messages[2].parts[0]).toEqual({ type: 'text', text: '为什么？' });
    expect(messages[3].role).toBe('assistant');
    expect(messages[3].parts[0]).toEqual({ type: 'markdown', text: '**依据**是…' });
  });

  it('修订草案走 review-draft 逃生舱', () => {
    const item: ReviewRequirementItemRead = {
      ...baseItem(),
      review_status: 'awaiting_adjudication',
      dialogue_messages: [{
        message_ref: 'M-9', kind: 'draft', user_message: '改成这样', text: '',
        draft_value: '系统应在 2 秒内返回结果', draft_note: '补时限', draft_seq: 1,
        suggestion_ref: 'S-1', in_flight: true, created_at: '2026-07-06T04:00:00Z',
      }],
    };
    const messages = projectItemThread(item);
    const draft = messages.find((m) => m.parts[0]?.type === 'component');
    expect(draft).toBeDefined();
    const comp = componentOf(draft!);
    expect(comp.name).toBe(REVIEW_CARD.draft);
    expect(comp.props?.itemRef).toBe(item.item_ref);
  });

  it('失败轮次走 system 文本；「待补充来源」态末尾追加 review-supplement-exit 逃生舱', () => {
    const item: ReviewRequirementItemRead = {
      ...baseItem(),
      review_status: 'no_verdict',
      display_code: 'supplement_pending',
      supplement_gaps_open: ['来源缺口X'],
      current_verdict: null,
      verdict_history: [
        verdictOf({ round_ref: 'R-0', status: 'failed', reason: '诊断未完成', created_at: '2026-07-06T00:30:00Z' }),
      ],
    };
    const messages = projectItemThread(item);
    expect(messages[0].role).toBe('system');
    expect(messages[0].parts[0]).toEqual({ type: 'text', text: '⚠ 诊断未完成' });
    const last = messages[messages.length - 1];
    const comp = componentOf(last);
    expect(comp.name).toBe(REVIEW_CARD.supplementExit);
    expect(comp.props?.gaps).toEqual(['来源缺口X']);
    expect(comp.props?.itemRef).toBe(item.item_ref);
  });
});
