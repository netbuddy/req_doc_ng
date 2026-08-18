/**
 * 条目评审页 · 线程投影映射器（统一 AI 对话控件工作包 04 篇 §3.1、01 篇 §3.3）。
 *
 * 把评审工作区里「当前条目」的服务端权威数据（结论轮次 + 对话消息）投影成控件消息信封
 * ChatMessage[]。复用既有领域投影 buildThread（结论有效性规则留在页面侧，控件核心零业务
 * 感知——01 篇 §3.3 边界），本文件只负责「领域线程条目 → 控件分部」的形态转换。
 *
 * 分部选型（P1 期后端不出 card 分部、渲染器属 P5）：
 *  - 系统提示（诊断失败等）→ text 分部（system 角色）；
 *  - AI 解释 → markdown 分部（既定改进项：LLM 回复渲染为富文本，AC-P1-03）；
 *  - 结论卡 / 回执折叠块 / 修订草案卡 / 补充来源出口 → component 逃生舱分部
 *    （04 篇 §3.1：塌不进通用分部的评审专属卡以 component 保留、不硬拉平；逃生舱组件在
 *    评审页侧注册，沿用现有 rv5- 样式）。
 *
 * 逃生舱组件名单一来源在本文件（REVIEW_CARD 常量）：投影方与页面 capabilities.customCards
 * 注册表共用同一组名，避免字符串两处漂移。
 */
import type {
  DialogueMessageRead,
  ReviewRequirementItemRead,
  VerdictRead,
} from '../api/item-review';
import type { ChatMessage } from '../chat-widget';
import { buildThread } from './requirement-item-review';

/** 评审页逃生舱组件名（component 分部 name 与页面 customCards 注册表共用单一来源）。 */
export const REVIEW_CARD = {
  verdict: 'review-verdict',
  receipt: 'review-receipt',
  draft: 'review-draft',
  supplementExit: 'review-supplement-exit',
} as const;

/** 站立结论卡逃生舱 props（页面 ReviewVerdictCard 消费；裁决动作载荷用 itemRef/round_ref）。 */
export interface ReviewVerdictCardProps {
  verdict: VerdictRead;
  itemRef: string;
  itemVersionNo: string;
  at: string;
}

/** 已裁决/已替代/已失效回执折叠块逃生舱 props。 */
export interface ReviewReceiptCardProps {
  verdict: VerdictRead;
  at: string;
}

/** 修订草案卡逃生舱 props（在途未采纳无副作用；采纳走 adopt_draft host 动作）。 */
export interface ReviewDraftCardProps {
  message: DialogueMessageRead;
  itemRef: string;
  at: string;
}

/** 补充来源出口逃生舱 props（issue #30 出口卡；候选/登记等活状态经页面 Context 拿）。 */
export interface ReviewSupplementExitProps {
  itemRef: string;
  gaps: string[];
}

function settled(id: string, role: ChatMessage['role'], at: string, parts: ChatMessage['parts']): ChatMessage {
  return { id, role, at, status: 'settled', parts };
}

function componentMessage(
  id: string,
  at: string,
  name: string,
  props: Record<string, unknown>,
): ChatMessage {
  return settled(id, 'assistant', at, [{ type: 'component', name, props }]);
}

/** 「待补充来源」派生态：站立结论已被裁决、不再是站立结论，出口卡以补充来源出口逃生舱重新托出。 */
function isSupplementPending(item: ReviewRequirementItemRead): boolean {
  return item.display_code === 'supplement_pending';
}

/**
 * 当前条目 → 控件消息线程。空线程返回 []（控件渲染自带空态）。
 * 顺序：buildThread 已按时间序合并结论轮次与对话消息；补充来源出口卡（若在该态）恒置末尾。
 */
export function projectItemThread(item: ReviewRequirementItemRead): ChatMessage[] {
  const entries = buildThread(item);
  const messages: ChatMessage[] = [];

  for (const entry of entries) {
    if (entry.kind === 'system') {
      messages.push(
        settled(entry.key, 'system', entry.at, [
          { type: 'text', text: `${entry.tone === 'warn' ? '⚠ ' : ''}${entry.text}` },
        ]),
      );
    } else if (entry.kind === 'receipt') {
      const props: ReviewReceiptCardProps = { verdict: entry.verdict, at: entry.at };
      messages.push(componentMessage(entry.key, entry.at, REVIEW_CARD.receipt, { ...props }));
    } else if (entry.kind === 'verdict') {
      const props: ReviewVerdictCardProps = {
        verdict: entry.verdict,
        itemRef: item.item_ref,
        itemVersionNo: item.version_no,
        at: entry.at,
      };
      messages.push(componentMessage(entry.key, entry.at, REVIEW_CARD.verdict, { ...props }));
    } else {
      // dialogue：用户意见（text）＋ AI 解释（markdown）/ 修订草案（component 逃生舱）。
      const m = entry.message;
      if (m.user_message) {
        messages.push(settled(`${entry.key}-u`, 'user', entry.at, [{ type: 'text', text: m.user_message }]));
      }
      if (m.kind === 'draft') {
        const props: ReviewDraftCardProps = { message: m, itemRef: item.item_ref, at: entry.at };
        messages.push(componentMessage(`${entry.key}-a`, entry.at, REVIEW_CARD.draft, { ...props }));
      } else {
        messages.push(
          settled(`${entry.key}-a`, 'assistant', entry.at, [{ type: 'markdown', text: m.text }]),
        );
      }
    }
  }

  if (isSupplementPending(item)) {
    const props: ReviewSupplementExitProps = {
      itemRef: item.item_ref,
      gaps: item.supplement_gaps_open,
    };
    messages.push(
      componentMessage(`supplement-exit-${item.item_ref}`, item.current_verdict?.created_at ?? item.req_no, REVIEW_CARD.supplementExit, {
        ...props,
      }),
    );
  }

  return messages;
}
