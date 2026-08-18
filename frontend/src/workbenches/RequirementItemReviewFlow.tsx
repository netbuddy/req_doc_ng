/**
 * 条目评审阶段五区页面（SCN-003 v5 · 对话式结论裁决 · 线程收件箱）。
 *
 * 事实源：docs/40-detailed-design/slices/SCN-003-P01-需求条目评审/页面详细设计.md（v5）。
 * - 一个条目 = 一条线程；会话条芯片承载并行（徽标=结论状态字缩写，后台事件只亮徽标）；
 * - 结论卡 = 唯一裁决对象（大状态字 + 证据行 + 逐条问题块〔可改法／可标为不是问题〕 + 采纳/拒绝两键）；
 * - 草案卡零副作用（采纳走 AEP-036 → 链式自动增量）；解释消息不改结论；
 * - 快捷命令药丸仅预填 `/命令词`（前端不解析）：整段原文发 AEP-095，由后端注册表解析命令词、
 *   LLM 解释正文后派发 AEP-032/034/036/037；结论卡/草案卡一键裁决保持直发；无斜杠自由文本三出口不变；
 * - 按钮可用性来自后端 available_actions/available_operations；前端不自算门禁。
 */
import { Button, Input, Modal, notification } from 'antd';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  itemReviewApi,
  type DiagnosisMode,
  type ItemReviewWorkspaceRead,
  type ReviewDialogueCommand,
  type ReviewDialogueResult,
  type ReviewFindingRead,
  type ReviewRequirementItemRead,
  type RevisionPointRead,
  type VerdictRead,
} from '../api/item-review';
import type { ItemFormationWorkspaceRead } from '../api/item-formation';
import { requirementsApi } from '../api/requirements';
import { qualityApi } from '../api/quality';
import type { ItemQualityRead } from '../api/quality';
import { RequirementQualityPanel } from './RequirementQualityPanel';
import { buildQualityPanelVM, EMPTY_QUALITY_PANEL } from '../view-models/requirement-quality';
import { useAgentRunWatcher, type RunPollTick } from '../hooks/useAgentRunWatcher';
import { itemReviewWorkspaceFixture } from '../fixtures/item-review';
import { renderActionIcon } from '../ui/icons';
import { RelativeTime } from '../ui/RelativeTime';
import { ChatWidget } from '../chat-widget';
import type {
  ChatHostAdapter,
  ChatMessage,
  ComponentPartRenderProps,
  DialogueTransport,
  QuickCommand,
  StreamHandlers,
} from '../chat-widget';
import {
  projectItemThread,
  REVIEW_CARD,
  type ReviewDraftCardProps,
  type ReviewReceiptCardProps,
  type ReviewSupplementExitProps,
  type ReviewVerdictCardProps,
} from '../view-models/item-review-thread';
import { mergeReviewThread } from '../view-models/demo-chat-transcript';
import { fetchChatTranscript, type ChatTranscriptRow } from '../api/transcript';
import { elementTypeMeta } from '../view-models/requirement-analysis';
import {
  adoptVerbText,
  buildActivityFeed,
  buildInitialReviewWorkspace,
  buildReviewSourceCanvas,
  buildSourceCandidateCards,
  buildSourceRegistrationValue,
  findSelectionHits,
  revisionRecordText,
  type CanvasTextSelection,
  type SelectionHit,
  buildThreadStrip,
  buildVerdictProblems,
  collectEditedPointTrail,
  collectRunFailureToasts,
  composeSelectedPoints,
  deriveDiagnosisRunProgress,
  DIAGNOSIS_MODE_OPTIONS,
  diagnosisLaunchCommand,
  diagnosisModeText,
  diagnosisScopeHint,
  findingTypeText,
  groupReviewItems,
  isSourceAttestation,
  attestAffordance,
  isSupplementPending,
  mapReviewItems,
  mapReviewSourceElementsById,
  nextAwaitingItem,
  QUICK_COMMAND_PREFILLS,
  receiptText,
  resolveReviewAnchors,
  reviewDisplayMeta,
  reviewItemStatusNote,
  reviewRunHint,
  type SourceCandidateCardVM,
  triggerText,
  verdictKindText,
} from '../view-models/requirement-item-review';
import {
  priorityText,
  requirementItemTypeText,
  verificationMethodText,
} from '../view-models/requirement-item-formation';
import { StatusPill } from './WorkbenchWidgets';
import '../styles-review-attest.css';
import { createIdempotencyKey } from '../api/idempotency';

interface RequirementItemReviewFlowProps {
  projectId: string;
  operatorRef: string;
  sourceWorkspace: ItemFormationWorkspaceRead | null;
  onBackToFormation: () => void;
  onBackToMaintenance: () => void;
}

function makeInitialWorkspace(sourceWorkspace: ItemFormationWorkspaceRead | null): ItemReviewWorkspaceRead {
  return sourceWorkspace?.pending_items.length
    ? buildInitialReviewWorkspace(sourceWorkspace)
    : itemReviewWorkspaceFixture;
}

function defaultDiagnosisSelection(workspace: ItemReviewWorkspaceRead): string[] {
  return workspace.review_items
    .filter((item) => item.available_actions.some((a) => a.key === 'request_diagnosis' && a.enabled)
      || (!item.available_actions.length && item.review_status === 'no_verdict'))
    .map((item) => item.item_ref);
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求处理失败';
}

function hasRunning(workspace: ItemReviewWorkspaceRead): boolean {
  return (
    workspace.review_items.some((item) => item.review_status === 'diagnosing') ||
    workspace.diagnosis_runs.some((run) => run.status === 'running')
  );
}

/** 在途诊断批次已处理数之和：作停滞判定的进展信号（推进即复位停滞时钟）。 */
function runningProgressTotal(workspace: ItemReviewWorkspaceRead): number {
  return workspace.diagnosis_runs
    .filter((run) => run.status === 'running')
    .reduce((sum, run) => sum + run.completed_count, 0);
}

/** C44：写入成功、随后的工作区刷新失败时的提示（与「写入失败」分开说，见 refreshAfterWrite）。 */
const REFRESH_FAILED_NOTE = '页面数据没取回来，请手动刷新看最新状态';

const REVIEW_POLL_INTERVAL_MS = 1500;
// 停滞阈值：诊断在途但「无任何条目完成推进」持续超此时长 → 前端派生停滞态（B1 审查 O1：inline
// 后台线程被杀致 run 滞留 started）。进展信号只有条目粒度（单条目批次整个 LLM 调用期间零推进），
// 故阈值必须 ≥ 后端单条目预算＋余量，否则健康 run 被诬告「可能已中断」：后端预算见
// backend/app/workers/queue.py `single = max(2*LLM_TIMEOUT, 360s)`（缺省 360s），此处取 +60s 余量
// （合并裁定 F2：原 60s 比预算早 5.7 倍误报）。停滞≠终止：仅提示，不停表、不改数据。
const REVIEW_STALL_THRESHOLD_MS = 420_000;

const VERDICT_TONE: Record<string, string> = {
  pass: 'rv5-verdict--pass', revise: 'rv5-verdict--revise',
  withdraw: 'rv5-verdict--withdraw', supplement: 'rv5-verdict--supplement',
};

/** 诊断模式弹层：区2 主按钮与区5 药丸双入口共用一件（禁第二套）；范围说明随区1 勾选联动 */
function DiagnosisModePop({
  below,
  onPick,
  selectedCount,
}: {
  below?: boolean;
  onPick: (mode: DiagnosisMode) => void;
  selectedCount: number;
}) {
  return (
    <div
      aria-label="选择诊断模式"
      className={below ? 'az5-pop rv5-pop az5-pop--below' : 'az5-pop rv5-pop'}
      role="menu"
    >
      <span className="az5-pop__cap">
        诊断模式？范围 = 区1 已勾选 {selectedCount} 条（未勾选则为当前条目）
      </span>
      {DIAGNOSIS_MODE_OPTIONS.map((mode) => (
        <button className="az5-qp" key={mode} onClick={() => onPick(mode)} type="button">
          {diagnosisModeText(mode)}
        </button>
      ))}
    </div>
  );
}

/** 补充来源出口态（issue #30）：候选按 item_ref 键存组件态；candidates=null 表示未查/查询中，
 * []=已查但空（空池/服务不可用/AI 无贴切候选）。find_sources 命令不持久化，候选是一次性返回值。 */
interface SourceExitState {
  loading: boolean;
  candidates: SourceCandidateCardVM[] | null;
  message: string | null;
  nextAction: string | null;
}

/**
 * 补充来源出口卡（issue #30 出口三部曲之三；ADR-0002 P1 无死胡同 / P3 说缺必说补）。
 * 采纳 supplement 结论后条目进入「待补充来源」派生态，此时该轮已被裁决、不再是站立结论，
 * 故本卡以 display_code==='supplement_pending' 为条件在区5 线程重新托出：缺口清单 → 候选来源
 * （每条带原文引文、推荐理由与〔登记为本条来源〕）→ 兜底出口〔撤回该条〕〔我自己指定来源〕。
 * 空/拒绝态不给死按钮：无候选时只呈现指引文案，兜底出口恒在。
 */
function SupplementSourceExit({
  gaps,
  state,
  busy,
  onRegister,
  onRefetch,
  onWithdraw,
  onSpecify,
  onAttest,
  attestable,
}: {
  gaps: string[];
  state?: SourceExitState;
  busy: boolean;
  onRegister: (elementRef: string) => void;
  onRefetch: () => void;
  onWithdraw: () => void;
  onSpecify: () => void;
  onAttest: () => void;
  /** 能否人工确认；不能时给出后端的理由（读 affordance，不前端自算门禁） */
  attestable: { enabled: boolean; reason: string | null };
}) {
  // 未查（state 缺席）按加载中呈现，避免首帧闪现「暂未找到」（冷审查 F2）
  const loading = state ? state.loading : true;
  const candidates = state?.candidates ?? null;
  const hasCandidates = !!candidates && candidates.length > 0;
  return (
    <div className="az5-card rv5-vcard rv5-supplement" aria-label="补充来源出口">
      <div className="rv5-verdict rv5-verdict--supplement">
        待补充来源
        <span className="rv5-verdict__sub">登记正确来源后可继续评审</span>
      </div>
      <div className="az5-card__bd">
        {gaps.length ? (
          <div className="rv5-compose">
            <span className="rv5-compose__cap">来源缺口清单</span>
            {gaps.join('；')}
          </div>
        ) : null}
        {loading ? (
          <p className="rv5-evi rv5-supplement__loading">
            <span className="rv5-spin" aria-hidden="true" />
            正在为本条在材料里查找候选来源…
          </p>
        ) : hasCandidates ? (
          <div className="rv5-cand-list">
            <span className="rv5-compose__cap">材料里的候选来源（按相关度排序）</span>
            {candidates!.map((candidate) => (
              <div className="rv5-cand" key={candidate.elementRef}>
                <div className="rv5-cand__hd">
                  <span className={`element-type-chip element-type-chip--${candidate.typeColorKey}`}>
                    {candidate.typeLabel}
                  </span>
                  <span className="rv5-cand__content">{candidate.content}</span>
                </div>
                {candidate.sourceQuote ? (
                  <p className="rv5-cand__quote">原文：「{candidate.sourceQuote}」</p>
                ) : null}
                {candidate.reason ? <p className="rv5-evi">{candidate.reason}</p> : null}
                <button
                  className="az5-btn az5-btn--primary rv5-cand__reg"
                  disabled={busy}
                  onClick={() => onRegister(candidate.elementRef)}
                  type="button"
                >
                  登记为本条来源
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="rv5-evi">{state?.message ?? '暂未找到候选来源。'}</p>
        )}
        {/* 常驻指路：用户明确知道出处时，最短的路是直接去正文里拖选，不必等 AI 找 */}
        <p className="rv5-supplement__tip">
          知道这条需求出自材料哪句话？直接在左边正文里拖选那句话。
        </p>
      </div>
      <div className="az5-card__ft">
        {/* 材料里确实没写这条时的出口：不是「找不到就算了」，而是由人担下来并留痕。
            与其余按钮分开摆——它是授权例外，不该看着像又一个普通选项。
            已经确认过一次就不再给这个按钮：出处缺口那时就闭合了，此后再判「建议补充来源」
            缺的必定是格式/字段/阈值这类具体值，而人工确认一个值都提供不了——再点一次只会
            在「确认→重诊→又说缺」里绕圈。此时改摆一句说明，指向真正能解决的两条路。 */}
        {attestable.enabled ? (
          <button
            className="az5-btn rv5-btn--attest"
            disabled={busy}
            onClick={onAttest}
            title="材料里没写这条；我确认它是真实需求，由我负责登记"
            type="button"
          >
            人工确认
          </button>
        ) : null}
        <button className="az5-btn rv5-btn--danger" disabled={busy} onClick={onWithdraw} type="button">
          撤回该条
        </button>
        {/* AI 重查保留为并存动作，降到次级：它解决的是「不知道出处、让 AI 再找找」，
            与拖选（知道出处）、人工确认（材料里根本没有）各管一段，不互相替代。 */}
        <button className="az5-btn" disabled={busy} onClick={onSpecify} type="button">
          按说明查找
        </button>
        {!loading ? (
          <button className="az5-btn" disabled={busy} onClick={onRefetch} type="button">
            重新查找
          </button>
        ) : null}
        <span className="az5-card__note">
          {!attestable.enabled && attestable.reason
            // 按钮撤走时必须说清为什么，并给出真正能解决这一轮缺口的两条路——只把按钮
            // 藏掉，用户只会觉得功能没了。
            ? `${attestable.reason}。把缺的口径直接写进条目表达（下方〔人工修订〕），或在左边正文里拖选出处登记为来源。`
            : hasCandidates
              ? '登记后旧结论随之失效、离开「待补充来源」；随后点上方「发起诊断」复核本条。都不对可撤回该条。'
              : loading
                ? '也可以直接在左边正文里拖选出处，或撤回该条。'
                : state?.nextAction ?? '没有贴切候选？在左边正文里拖选出处，或撤回该条。'}
        </span>
      </div>
    </div>
  );
}

/**
 * 区3 拖选确认条：显示选中的原文，并按选区落点给出下一步。
 *
 * 三种落点各有各的说法，不含糊成一句「无法登记」：
 * - 落在一条已确认的知识项上 → 直接登记为本条来源（走与 AI 候选同一条登记通道）。
 * - 落在多条上（选区跨了几个知识项）→ 列出来让用户点一条，不替用户猜。
 * - 一条都没落上 → 这段话还没被识别成知识项。此时不自动补建：补建要先在需求分析页
 *   生成知识项并**由人确认**，那一步有人工含义，不该由评审页替用户静默做掉。所以这里
 *   说清楚下一步该去哪、做什么（ADR-0002 P1 无死胡同＝必须说清下一步，而不是给个死按钮）。
 *
 * **确认条只在当前条目处于「待补充来源」时出现**（2026-07-20 用户走查第 1 轮拍板）。其余状态下
 * 拖选照样能选中、能复制，但不弹任何东西——那时没有来源要登记，弹一条只为说「不需要指定来源」
 * 是句废话，白占地方。拖选后下方出现什么，取决于当前条目处在哪个状态。
 */
function CanvasSelectionBar({
  selection,
  hits,
  busy,
  isSupplementGap,
  attestOffered,
  onRegister,
  onClear,
}: {
  selection: CanvasTextSelection;
  hits: SelectionHit[];
  busy: boolean;
  isSupplementGap: boolean;
  /** 出口卡此刻是否还摆着〔人工确认〕；已确认过的条目不再摆，指路就不能指它（K4） */
  attestOffered: boolean;
  onRegister: (elementRef: string) => void;
  onClear: () => void;
}) {
  const preview = selection.text.length > 40 ? `${selection.text.slice(0, 40)}…` : selection.text;
  const usable = hits.filter((h) => h.registrable);
  return (
    <div className="rv5-sel" role="status">
      <p className="rv5-sel__quote">已选原文：「{preview}」</p>
      {hits.length === 0 ? (
        // 零命中的引导按缺口态分叉：〔人工确认〕按钮只在「待补充来源」缺口态存在，非缺口态提它会指向
        // 一个不存在的按钮（K4）。缺口态保留原指引；非缺口态只指向知识抽取页，不提〔人工确认〕。
        isSupplementGap ? (
          <p className="rv5-sel__hint">
            这段话还没有被识别成一条知识项，所以现在还不能登记为来源。
            请到知识抽取页把它补成知识项并确认，再回到这里登记；
            {attestOffered
              ? '材料里本来就没写这条需求的话，用右侧的〔人工确认〕。'
              : '这条已经人工确认过来源了，本轮缺的是具体口径——把口径直接写进条目表达即可。'}
          </p>
        ) : (
          <p className="rv5-sel__hint">
            这段话还没有被识别成一条知识项，所以现在还不能登记为来源。
            材料里没有这句时，先到知识抽取页把它补成知识项并确认，再回来拖选登记。
          </p>
        )
      ) : usable.length === 0 ? (
        <p className="rv5-sel__hint">{hits[0].blockedReason}</p>
      ) : (
        <div className="rv5-sel__hits">
          {usable.length > 1 ? (
            <span className="rv5-sel__cap">这段话跨了几条知识项，选一条登记为本条来源：</span>
          ) : null}
          {usable.map((hit) => (
            <button
              className="az5-btn az5-btn--primary rv5-sel__reg"
              disabled={busy}
              key={hit.elementRef}
              onClick={() => onRegister(hit.elementRef)}
              type="button"
            >
              <span className={`element-type-chip element-type-chip--${hit.typeColorKey}`}>{hit.typeLabel}</span>
              登记为本条来源：{hit.content.length > 24 ? `${hit.content.slice(0, 24)}…` : hit.content}
            </button>
          ))}
        </div>
      )}
      <button className="rv5-sel__clear" onClick={onClear} type="button">取消选择</button>
    </div>
  );
}

// ======================= 控件宿主：逃生舱组件与页面协同上下文 =======================
// 结论卡/回执折叠/草案卡/补充来源出口塌不进控件通用分部，按 04 篇 §3.1 以 component 逃生舱进线程，
// 沿用现有 rv5- 样式（不硬拉平）。逃生舱组件在模块层定义（避免随渲染重挂载），静态数据经投影
// props 拿、活状态与回调经 ReviewHostContext 拿；「采纳」「采纳草案」走 host 动作（actions.dispatch）。

/** 「先补理由再发送」类操作的理由输入弹层配置（拒绝结论/撤回该条/指定来源共用一件）。 */
interface ReasonModalConfig {
  title: string;
  label: string;
  placeholder?: string;
  confirmText?: string;
  danger?: boolean;
  /** 理由必填时提交按钮置灰（如人工确认背书：授权例外要赖不掉，空白理由等于没留痕） */
  requireReason?: boolean;
  submit: (reason: string) => void | Promise<void>;
}

/** 逃生舱组件与页面主体的协同面：活状态与回调经此传递（静态数据仍走投影 props）。 */
interface ReviewHostApi {
  currentItem: ReviewRequirementItemRead | null;
  busy: boolean;
  openReasonModal: (cfg: ReasonModalConfig) => void;
  sourceExitState: (itemRef: string) => SourceExitState | undefined;
  fetchSourceCandidates: (itemRef: string, message?: string) => void;
  registerSource: (itemRef: string, candidateRef: string) => void;
  /** 人工确认背书：材料未记载该需求，用户确认它成立（理由必填） */
  attestSource: (itemRef: string, reason: string) => void;
  rejectVerdict: (itemRef: string, verdict: VerdictRead, reason: string) => Promise<void>;
  runReviewCommand: (itemRef: string, message: string) => Promise<void>;
  /** AEP-116：标记「这条不是问题」（给 findingRef）或撤销标记（给 vetoRef） */
  setFindingVeto: (
    itemRef: string,
    args: { findingRef?: string; vetoRef?: string; reason?: string | null },
  ) => Promise<void>;
  /** 本轮建议已被逐条标为不是问题时的直接确认（非覆盖确认） */
  confirmVetoCleared: (itemRef: string) => Promise<void>;
  /** 整条重写条目表达（AI 未给改法时的出口，走人工修订通道） */
  rewriteExpression: (itemRef: string, expression: string) => Promise<void>;
}

const ReviewHostContext = createContext<ReviewHostApi | null>(null);

function useReviewHost(): ReviewHostApi {
  const ctx = useContext(ReviewHostContext);
  if (!ctx) throw new Error('ReviewHostContext 未提供：逃生舱组件必须在评审页宿主内渲染');
  return ctx;
}

/**
 * 站立结论卡逃生舱：唯一裁决对象。
 *
 * 卡面以**问题**为唯一列表单元（2026-07-20 用户走查后重设计）：AI 给的改法挂在它所针对的
 * 问题下面，不再与问题并排成第二个列表——此前那样排，用户读到的是「三个问题」，而后端其实
 * 只报了两个问题一个改法。每个问题二选一：在文本框里写改后的文字，或标「这不是问题」。
 * 没有勾选框，也就没有「问题成立但先不改」这个出口；两样都不想选就整轮「拒绝…」。
 */
function ReviewVerdictCard({ props, actions }: ComponentPartRenderProps) {
  const host = useReviewHost();
  const { verdict: v, itemRef, itemVersionNo, at } = props as unknown as ReviewVerdictCardProps;
  const item = host.currentItem;
  const baseExpression = item?.expression ?? '';
  const { problems, orphanFixes, attestedNotices } = buildVerdictProblems(v);
  // 局部改法的改稿（point_ref → 用户终稿）；只存改过的，没改的不进来，提交时也不带上。
  const [edits, setEdits] = useState<Record<string, string>>({});
  // 整条重写（finding_ref → 用户写的整条表达）：AI 只报了问题没给改法时，用户只能自己重写整条。
  const [rewrites, setRewrites] = useState<Record<string, string>>({});
  const kindClass = v.verdict_kind ? VERDICT_TONE[v.verdict_kind] : '';
  const instanceId = `adopt:${itemRef}:${v.round_ref}`;
  const phase = actions.phaseOf(instanceId);
  const dispatching = phase === 'dispatching';
  const awaiting = phase === 'awaiting-followup';

  // 生效的整条重写：内容与预填（条目当前表达）不同才算数。整条重写与局部改法互斥，
  // 一张卡至多一处生效——两处同时改整条，合成结果就无从谈起。
  // C39：候选集只取「仍然存在且未被标为不是问题」的问题（与下面 dirtyEdits 同法）。否则用户在
  // 整条重写生效期间把同一个问题标为不是问题后，那段已看不见的草稿仍会让整卡锁死、取消入口消失，
  // 主按钮还会用它整条替换条目。
  const liveFindingRefs = new Set(problems.filter((p) => !p.vetoed).map((p) => p.findingRef));
  const activeRewrite = Object.entries(rewrites).find(
    ([ref, text]) => liveFindingRefs.has(ref) && text !== baseExpression,
  );
  const rewriteRef = activeRewrite?.[0] ?? null;
  const rewriteText = activeRewrite?.[1] ?? null;
  const locked = rewriteRef !== null;  // 整条重写生效时，其余输入框只读

  // 未被标为「不是问题」的点全部参与采纳（无勾选框＝无部分选择），改稿覆盖其替换文本。
  // C15：候选集由**界面上展示出来的问题块**派生，而不是由 v.revision_points 全量派生——
  // 归不到任何问题的改法（orphanFixes）只在脚注里以一行灰字出现，用户既编辑不了也标记不了，
  // 却曾照样进合成预览与提交载荷、真的改了他的条目。提交什么与展示什么必须同源。
  const livePoints = problems
    .filter((p) => !p.vetoed)
    .flatMap((p) => p.fixes)
    .filter((p) => !p.vetoed);
  const effectivePoints = livePoints.map((p) =>
    edits[p.point_ref] !== undefined ? { ...p, replace: edits[p.point_ref] } : p,
  );
  const liveRefs = new Set(livePoints.map((p) => p.point_ref));
  const composePreview = rewriteText !== null
    ? rewriteText
    : v.verdict_kind === 'revise'
      ? composeSelectedPoints(baseExpression, effectivePoints, liveRefs)
      : null;
  const dirtyEdits = Object.fromEntries(
    Object.entries(edits).filter(([ref, text]) => {
      const point = livePoints.find((p) => p.point_ref === ref);
      return point !== undefined && text !== point.replace;
    }),
  );
  // C24：整条重写生效时，局部改法一概不应用，它们留下的空值就不该再禁用提交——否则提示
  // 会叫用户去填一个已经被置灰的框，而那个框此刻填不了字。
  const emptyEdit = rewriteText !== null
    ? !rewriteText.trim()
    : Object.entries(dirtyEdits).some(([, text]) => !text.trim());
  // C14(b)：确认入口的可用性读后端 affordance，不自算门禁（本文件开头的自述即此约定）。
  // 前端此前只看 all_blocking_findings_vetoed，缺「诊断进行中」这一支，诊断中按钮仍亮着。
  const vetoClearable = (item?.available_actions ?? []).some(
    (a) => a.key === 'confirm_without_override' && a.enabled,
  );
  // 「照建议改了几次仍没通过」——只用来提示，不禁用任何入口：什么时候不值得再改由用户判断，
  // 往复本身没有上限（2026-07-20 废除采纳链空转熔断）。
  const repeatedRounds = item?.adopted_revise_rounds ?? 0;

  const openVeto = (findingRef: string) =>
    host.openReasonModal({
      title: '标记为不是问题',
      label: '为什么不是问题（可不填）',
      placeholder: '例如：这是业务上已确认的说法，不需要改',
      confirmText: '标记为不是问题',
      submit: (reason) => host.setFindingVeto(itemRef, { findingRef, reason }),
    });

  const adopt = () => {
    // 整条重写走人工修订通道（整条替换、旧结论失效）；否则走结论采纳通道（局部合成）。
    // 两条都是既有通道，后端未为本次重设计改动一行。
    if (rewriteText !== null) {
      void host.rewriteExpression(itemRef, rewriteText);
      return;
    }
    actions.dispatch(
      {
        kind: 'host',
        name: 'adopt_verdict',
        label: adoptVerbText(v.verdict_kind),
        payload: {
          item_ref: itemRef,
          round_ref: v.round_ref,
          verdict_kind: v.verdict_kind,
          selected_point_refs: v.verdict_kind === 'revise' ? [...liveRefs] : null,
          point_edits: v.verdict_kind === 'revise' && Object.keys(dirtyEdits).length ? dirtyEdits : null,
        },
        confirm:
          v.verdict_kind === 'pass'
            ? '采纳「建议通过」将确认该条目并进入下游发布口径（没有第二步）。确认？'
            : undefined,
        followup: v.verdict_kind === 'revise' ? 'pending-followup' : 'done',
      },
      instanceId,
    );
  };

  const openReject = () =>
    host.openReasonModal({
      title: `拒绝结论 · 第${v.round_no}轮`,
      label: '拒绝理由（必填）',
      placeholder: '说明为何拒绝该结论（将作废本轮结论并留痕）',
      confirmText: '拒绝该结论',
      danger: true,
      requireReason: true,  // 后端本就要求理由必填；不在此拦住，用户拿到的是一句报错
      submit: (reason) => host.rejectVerdict(itemRef, v, reason),
    });

  const adoptLabel = dispatching
    ? '处理中…'
    : awaiting
      ? '已受理 · 重诊中'
      : rewriteText !== null
        ? '按你写的整条替换'
        : v.verdict_kind === 'revise'
          ? '按上面的内容修改并自动重诊'
          : adoptVerbText(v.verdict_kind);

  return (
    <div className="rv5-hatch">
      <span className="az5-who">
        结论 · 第 {v.round_no} 轮 · {diagnosisModeText(v.diagnosis_mode)} · {triggerText(v.trigger)}
        <RelativeTime className="az5-time" iso={at} />
      </span>
      <div className="az5-card rv5-vcard">
        <div className={`rv5-verdict ${kindClass}`}>
          {verdictKindText(v.verdict_kind)}
          <span className="rv5-verdict__sub">当前有效 · v{itemVersionNo}</span>
        </div>
        <div className="az5-card__bd">
          <p>{v.verdict_summary}</p>
          {problems.length ? (
            <div className="rv5-problems">
              <span className="rv5-problems__cap">
                发现 {problems.length} 个问题 · 每个问题要么改、要么标为不是问题
              </span>
              {repeatedRounds >= 3 ? (
                <span className="rv5-problems__cap">
                  这是你第 {repeatedRounds} 次照建议改后仍没通过——可能不是表达的问题，
                  值得看看来源要素讲的是不是同一件事；改不好也可以撤回这条。
                </span>
              ) : null}
              {problems.map((problem, index) => {
                const rewrite = rewrites[problem.findingRef];
                const rewriting = rewriteRef === problem.findingRef;
                const otherLocked = locked && !rewriting;
                return (
                  <div
                    className={`rv5-problem${problem.vetoed ? ' rv5-problem--vetoed' : ''}${otherLocked ? ' rv5-problem--locked' : ''}`}
                    key={problem.findingRef}
                  >
                    <div className="rv5-problem__hd">
                      <span className="rv5-problem__no">问题 {index + 1}</span>
                      <span className="rv5-tag">{findingTypeText(problem.findingType)}</span>
                      {problem.ruleCode ? <span className="rv5-tag">{problem.ruleCode}</span> : null}
                    </div>
                    <p className="rv5-problem__sum">{problem.summary}</p>
                    {problem.basis ? <p className="rv5-problem__basis">依据：{problem.basis}</p> : null}

                    {problem.vetoed ? (
                      <div className="rv5-veto">
                        <span className="rv5-veto__mark">
                          你已标为不是问题{problem.vetoReason ? `：${problem.vetoReason}` : ''}
                        </span>
                        <button
                          className="rv5-linkbtn"
                          disabled={host.busy}
                          onClick={() => void host.setFindingVeto(itemRef, { vetoRef: problem.vetoRef ?? undefined })}
                          type="button"
                        >
                          重新计入
                        </button>
                      </div>
                    ) : (
                      <>
                        {problem.fixes.map((p) => (
                          <div className="rv5-edit" key={p.point_ref}>
                            <span className="rv5-edit__anchor">
                              AI 建议把「{p.find}」改成：
                            </span>
                            <textarea
                              className="rv5-edit__box"
                              onChange={(event) => setEdits((cur) => ({ ...cur, [p.point_ref]: event.target.value }))}
                              readOnly={otherLocked}
                              rows={2}
                              value={edits[p.point_ref] ?? p.replace}
                            />
                            {p.basis ? <span className="rv5-edit__basis">依据：{p.basis}</span> : null}
                            {(edits[p.point_ref] ?? p.replace) !== p.replace ? (
                              <button
                                className="rv5-linkbtn"
                                onClick={() => setEdits((cur) => {
                                  const next = { ...cur };
                                  delete next[p.point_ref];
                                  return next;
                                })}
                                type="button"
                              >
                                还原成 AI 写的
                              </button>
                            ) : null}
                          </div>
                        ))}
                        {problem.fixes.length ? null : (
                          <div className="rv5-edit">
                            <span className="rv5-edit__anchor">
                              AI 没有给出改法（多半是来源材料里没有那个值，得先补来源）。你可以自己把整条改掉：
                            </span>
                            <textarea
                              className="rv5-edit__box"
                              onChange={(event) => setRewrites((cur) => ({ ...cur, [problem.findingRef]: event.target.value }))}
                              readOnly={otherLocked}
                              rows={3}
                              value={rewrite ?? baseExpression}
                            />
                            {rewriting ? (
                              <button
                                className="rv5-linkbtn"
                                onClick={() => setRewrites((cur) => {
                                  const next = { ...cur };
                                  delete next[problem.findingRef];
                                  return next;
                                })}
                                type="button"
                              >
                                取消整条重写
                              </button>
                            ) : (
                              <span className="rv5-edit__basis">
                                {otherLocked ? '已有一处在整条重写，这里改不了' : '不动它就是这一处你还没处理'}
                              </span>
                            )}
                          </div>
                        )}
                        {problem.canVeto ? (
                          <div className="rv5-problem__ft">
                            <button
                              className="rv5-linkbtn"
                              disabled={host.busy}
                              onClick={() => openVeto(problem.findingRef)}
                              type="button"
                            >
                              这不是问题
                            </button>
                          </div>
                        ) : null}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ) : null}
          {/* 来源已由人工确认的条目：AI 仍报出的来源对齐类发现降为提示，与上面的问题分开列。
              后端已把它排除在阻断计数外（source_attested），这里只是把同一个事实呈现出来，
              不自己判断哪条该降格。 */}
          {attestedNotices.length ? (
            <div className="rv5-attested">
              <span className="rv5-attested__cap">
                以下 {attestedNotices.length} 条与来源有关，不用你处理——这条需求的来源已由人工确认
              </span>
              {attestedNotices.map((notice) => (
                <div className="rv5-attested__item" key={notice.findingRef}>
                  <div className="rv5-problem__hd">
                    <span className="rv5-attested__mark">提示</span>
                    <span className="rv5-tag">{findingTypeText(notice.findingType)}</span>
                    {notice.ruleCode ? <span className="rv5-tag">{notice.ruleCode}</span> : null}
                    <span className="rv5-attested__src">来源＝人工确认</span>
                  </div>
                  <p className="rv5-problem__sum">{notice.summary}</p>
                  {notice.fixes.length ? (
                    <p className="rv5-problem__basis">
                      AI 就此给过改法，但这条不用改，采纳时不会应用它：
                      {notice.fixes.map((p) => `「${p.find}」→「${p.replace}」`).join('；')}
                    </p>
                  ) : null}
                  {/* 这条以前被你标成过「不是问题」：状态与撤销入口要留在看得见的地方。
                      降格把它移出了问题列表，而「重新计入」按钮长在问题卡片里，两者一起消失
                      的话，用户既看不到自己曾经的裁定、也无法撤销它——而那条否决按指纹对此后
                      所有轮次持续生效。 */}
                  {notice.vetoed ? (
                    <div className="rv5-attested__veto">
                      <span>
                        你还把它标过「不是问题」{notice.vetoReason ? `：${notice.vetoReason}` : ''}
                      </span>
                      <button
                        className="rv5-linkbtn"
                        disabled={host.busy}
                        onClick={() => void host.setFindingVeto(itemRef, { vetoRef: notice.vetoRef ?? undefined })}
                        type="button"
                      >
                        撤销这个标记
                      </button>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
          {orphanFixes.length ? (
            <p className="rv5-problem__basis">
              另有 {orphanFixes.length} 条改法对应不到上面任何一个问题，不会被应用：
              {orphanFixes.map((p) => `「${p.find}」→「${p.replace}」`).join('；')}
            </p>
          ) : null}
          {locked ? (
            <p className="rv5-problem__basis">
              你正在整条重写，上面那些局部改法就不应用了。
            </p>
          ) : null}
          {v.verdict_kind === 'revise' ? (
            <div className="rv5-compose">
              <span className="rv5-compose__cap">改完的表达（随你的改动实时更新）</span>
              {composePreview}
            </div>
          ) : null}
          {v.verdict_kind === 'supplement' ? (
            <div className="rv5-compose">
              <span className="rv5-compose__cap">来源缺口清单</span>
              {v.supplement_gaps.join('；')}
            </div>
          ) : null}
        </div>
        <div className="az5-card__ft">
          {vetoClearable ? (
            <button
              className="az5-btn rv5-btn--success"
              disabled={host.busy || dispatching || awaiting}
              onClick={() => void host.confirmVetoCleared(itemRef)}
              type="button"
            >
              确认这个条目
            </button>
          ) : null}
          <button
            className={`az5-btn ${v.verdict_kind === 'pass' ? 'rv5-btn--success' : vetoClearable ? '' : 'az5-btn--primary'}`}
            disabled={
              dispatching || awaiting || emptyEdit || host.busy
              || (v.verdict_kind === 'revise' && rewriteText === null && liveRefs.size === 0)
            }
            onClick={adopt}
            type="button"
          >
            {adoptLabel}
          </button>
          <button className="az5-btn rv5-btn--danger" disabled={dispatching || awaiting} onClick={openReject} type="button">
            拒绝…
          </button>
          <span className="az5-card__note">
            {emptyEdit
              ? '有一处你改成了空的：填上内容，或者把这个问题标成不是问题。'
              // 全部发现项都因人工确认降格时，界面上一个待处理的问题都没有，改法也全在不会被
              // 应用的那一栏里。这一支必须排在 vetoClearable 之前：后端放宽直接确认通道之后
              // （K5：降格清空同样开门），这种条目的 vetoClearable 也为真，而「你都标成了不是
              // 问题」讲的是用户逐条裁定，用户在这里一次都没裁定过，说出来就是假话。
              : problems.length === 0 && attestedNotices.length > 0
                ? '本轮只剩来源类提示，而这条的来源已由人工确认，没有需要你处理的问题：可以直接确认这个条目，也可以拒绝这一轮结论。'
                : vetoClearable
                  ? '这一轮提的问题你都标成了不是问题，可以直接确认这个条目。'
                  : rewriteText !== null
                    ? '整条替换后旧结论失效，需要你再手动发起一次诊断。'
                    : v.verdict_kind === 'pass'
                      ? '采纳即确认，进入下游发布口径——没有第二步。不同意？直接回复，我会解释或改判。'
                      : v.verdict_kind === 'revise'
                        ? '每个问题要么改、要么标为不是问题；两样都不想选就整轮拒绝。改完自动重诊。'
                        : '不同意但说不清？直接回复；要人工路线用下方命令。'}
          </span>
        </div>
      </div>
    </div>
  );
}

/** 回执折叠块逃生舱：已裁决/已替代/已失效轮次收折为一行（展开回看证据）。 */
function ReviewReceiptCard({ props }: ComponentPartRenderProps) {
  const { verdict: v, at } = props as unknown as ReviewReceiptCardProps;
  const receipt = receiptText(v);
  return (
    <details className="rv5-fold rv5-hatch">
      <summary>
        <span className={`rv5-fold__mark rv5-fold__mark--${receipt.tone}`}>{receipt.mark}</span>
        <span>{receipt.text}</span>
        <RelativeTime className="az5-time" iso={at} />
        <span className="rv5-fold__exp">展开回看</span>
      </summary>
      <div className="rv5-fold__bd">
        <p>{v.verdict_summary}</p>
        {v.findings.map((f) => (
          <p className="rv5-evi" key={f.finding_ref}>
            <span className="rv5-tag">{findingTypeText(f.finding_type)}</span>
            {f.diagnosis_summary}
          </p>
        ))}
      </div>
    </details>
  );
}

/** 修订草案卡逃生舱：在途未采纳无副作用；采纳走 adopt_draft host 动作（修订并自动重诊）。 */
function ReviewDraftCard({ props, actions }: ComponentPartRenderProps) {
  const { message: m, itemRef, at } = props as unknown as ReviewDraftCardProps;
  const actionable = m.in_flight && !!m.suggestion_ref;
  const instanceId = `adopt-draft:${itemRef}:${m.message_ref}`;
  const phase = actions.phaseOf(instanceId);
  const dispatching = phase === 'dispatching';
  const awaiting = phase === 'awaiting-followup';
  const adopt = () =>
    actions.dispatch(
      {
        kind: 'host',
        name: 'adopt_draft',
        label: '采纳草案',
        payload: { item_ref: itemRef, suggestion_ref: m.suggestion_ref ?? '' },
        followup: 'pending-followup',
      },
      instanceId,
    );
  // 这份草案是在条目形成页起草的：跨页续稿刻意保留，所以它在本页仍可采纳，
  // 但要说清它从哪来，否则读着像本页说过的话（走查反馈第⑧组）。
  const fromFormation = m.origin === 'formation';
  return (
    <div className="rv5-hatch">
      <span className="az5-who">
        修订草案 D{m.draft_seq ?? 1} · {fromFormation ? '来自条目形成页' : '由你的意见起草'}
        <RelativeTime className="az5-time" iso={at} />
      </span>
      <div className="az5-card">
        <div className="az5-card__hd">
          <b>修订草案 D{m.draft_seq ?? 1}</b>
          <span className="rv5-tag">{m.in_flight ? '在途 · 未采纳无副作用' : '已收束'}</span>
        </div>
        <div className="az5-card__bd">
          {fromFormation ? (
            <p className="rv5-evi">这份修订建议来自条目形成页，可在本页采纳。</p>
          ) : null}
          <p className="az5-diff az5-diff--after">{m.draft_value}</p>
          {m.draft_note ? <p className="rv5-evi">{m.draft_note}</p> : null}
        </div>
        {actionable ? (
          <div className="az5-card__ft">
            <button className="az5-btn az5-btn--primary" disabled={dispatching || awaiting} onClick={adopt} type="button">
              {dispatching ? '处理中…' : awaiting ? '已受理 · 重诊中' : '采纳草案 · 修订这个条目'}
            </button>
            <span className="az5-card__note">继续说可原位迭代；不想要不用管它。</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** 补充来源出口逃生舱：把 issue #30 出口卡接入控件线程（活状态经 Context，撤回/指定走理由弹层）。 */
function ReviewSupplementExitCard({ props }: ComponentPartRenderProps) {
  const host = useReviewHost();
  const { itemRef, gaps } = props as unknown as ReviewSupplementExitProps;
  return (
    <div className="rv5-hatch">
      <SupplementSourceExit
        busy={host.busy}
        gaps={gaps}
        onRefetch={() => host.fetchSourceCandidates(itemRef)}
        onRegister={(elementRef) => host.registerSource(itemRef, elementRef)}
        onSpecify={() =>
          host.openReasonModal({
            title: '按说明查找来源',
            label: '补充说明（可留空直接重查）',
            placeholder: '例如：来源在材料第 3 节某段落…',
            confirmText: '开始查找',
            submit: (hint) => host.fetchSourceCandidates(itemRef, `${QUICK_COMMAND_PREFILLS.specifySource()}${hint}`),
          })
        }
        // 取活状态而非投影 props：affordance 随每次工作区刷新变化，冻进 props 会过期
        attestable={host.currentItem ? attestAffordance(host.currentItem) : { enabled: true, reason: null }}
        onAttest={() =>
          host.openReasonModal({
            title: '人工确认这是真实需求',
            label: '为什么它是真实需求（必填）',
            placeholder: '例如：客户在 3 月 12 日评审会上口头确认，会议纪要漏记了这一条',
            confirmText: '确认并登记',
            requireReason: true,
            submit: (reason) => host.attestSource(itemRef, reason),
          })
        }
        onWithdraw={() =>
          host.openReasonModal({
            title: '撤回该条',
            label: '撤回理由（必填）',
            placeholder: '说明为何撤回该条目（留痕）',
            confirmText: '撤回该条',
            danger: true,
            // 理由留空就是一次空点击：命令带着空理由发出去，后端要求补理由，那句追问
            // 又只落在区2 的一行灰字里——用户在区5 点的，看不到任何反应（走查实测）。
            requireReason: true,
            submit: (reason) => host.runReviewCommand(itemRef, `/撤回 理由：${reason}`),
          })
        }
        state={host.sourceExitState(itemRef)}
      />
    </div>
  );
}

/** 逃生舱组件注册表（capabilities.customCards 单一来源，组件名同投影 REVIEW_CARD 常量）。 */
const REVIEW_CUSTOM_CARDS = {
  [REVIEW_CARD.verdict]: ReviewVerdictCard,
  [REVIEW_CARD.receipt]: ReviewReceiptCard,
  [REVIEW_CARD.draft]: ReviewDraftCard,
  [REVIEW_CARD.supplementExit]: ReviewSupplementExitCard,
} as const;

/** 快捷命令药丸：预填 /命令词进控件输入框，命令解析恒归后端（发起诊断的模式弹层留区2）。 */
const REVIEW_QUICK_COMMANDS: QuickCommand[] = [
  {
    command: 'diagnose',
    label: '发起诊断',
    priority: 50,
    prefill: (ctx) =>
      diagnosisLaunchCommand('standard', ((ctx.selected_item_refs as string[] | undefined)?.length) ?? 0),
  },
  { command: 'manual-revision', label: '人工修订', priority: 40, prefill: () => QUICK_COMMAND_PREFILLS.manualRevision() },
  { command: 'override', label: '覆盖确认', priority: 30, prefill: () => QUICK_COMMAND_PREFILLS.overrideConfirm() },
  { command: 'withdraw', label: '撤回条目', priority: 20, prefill: () => QUICK_COMMAND_PREFILLS.withdraw() },
  { command: 'ask-basis', label: '问依据', priority: 10, prefill: () => '解释当前结论的判定依据，并给出来源原文对照。' },
];

export function RequirementItemReviewFlow({
  projectId,
  operatorRef,
  sourceWorkspace,
  onBackToFormation,
  onBackToMaintenance,
}: RequirementItemReviewFlowProps) {
  const [workspace, setWorkspace] = useState<ItemReviewWorkspaceRead>(() => makeInitialWorkspace(sourceWorkspace));
  const [selectedForDiagnosis, setSelectedForDiagnosis] = useState<string[]>(() =>
    defaultDiagnosisSelection(makeInitialWorkspace(sourceWorkspace)),
  );
  const [currentItemRef, setCurrentItemRef] = useState<string | null>(() =>
    makeInitialWorkspace(sourceWorkspace).review_items[0]?.item_ref ?? null,
  );
  const [feedMode, setFeedMode] = useState(false);
  const [diagnosisMode, setDiagnosisMode] = useState<DiagnosisMode>('standard');
  const [modePopAnchor, setModePopAnchor] = useState<'zone2' | 'zone5' | null>(null);
  const [quality, setQuality] = useState<ItemQualityRead | null>(null); // v2 质量投影（AEP-105）
  const [busy, setBusy] = useState(false);
  const [noticeText, setNoticeText] = useState<string | null>(null);
  // 「先补理由再发送」类操作（拒绝结论/撤回该条/指定来源）的理由输入弹层（结论卡按用户拍板走弹层，非输入框预填）。
  const [reasonModal, setReasonModal] = useState<ReasonModalConfig | null>(null);
  const [reasonText, setReasonText] = useState('');
  const [reasonBusy, setReasonBusy] = useState(false);
  // 补充来源出口态（issue #30）：候选按 item_ref 键存组件态（find_sources 不持久化）；
  // fetchedSourcesRef 守卫「每条自动查一次」，防轮询/重渲染重复触发（重新查找显式绕过）。
  const [sourceExit, setSourceExit] = useState<Record<string, SourceExitState>>({});
  const fetchedSourcesRef = useRef<Set<string>>(new Set());
  const runStatusRef = useRef<Map<string, string>>(new Map());
  // 控件宿主动作（host 动作处理函数、getContext 快照）在发送瞬间读最新工作区/勾选，用 ref 兜稳定闭包。
  const workspaceRef = useRef(workspace);
  workspaceRef.current = workspace;
  const selectedForDiagnosisRef = useRef(selectedForDiagnosis);
  selectedForDiagnosisRef.current = selectedForDiagnosis;
  // 当前会话对象（发送/会话键取此 ref，随选中实时更新，供传输与适配器的延迟闭包读最新值）。
  const currentItemRefForSend = useRef<string>('');
  // 停滞判定进展快照：在途批次已处理数推进即复位停滞时钟（见 pollWorkspace）。
  const lastProgressRef = useRef<number | null>(null);
  // 诊断中轮询交由通用生命周期 hook（卸载清理/cancelled 终止/按 run 隔离/停滞派生，issue #10 B2b ④）。
  // 只解构消费：整容器随 watching/stalled 换新，入 deps 会引发每渲染 churn（合并裁定 F1 同病点）。
  const {
    watching: diagnosisWatching,
    stalled: diagnosisStalled,
    start: startRunWatch,
  } = useAgentRunWatcher({
    intervalMs: REVIEW_POLL_INTERVAL_MS,
    stallThresholdMs: REVIEW_STALL_THRESHOLD_MS,
  });

  const reviewContextRef = sourceWorkspace?.formation_context_ref ?? null;
  const operator = operatorRef || 'current-user';

  const applyWorkspace = useCallback((next: ItemReviewWorkspaceRead) => {
    setWorkspace(next);
    setCurrentItemRef((current) =>
      current && next.review_items.some((item) => item.item_ref === current)
        ? current
        : next.review_items[0]?.item_ref ?? null,
    );
    setSelectedForDiagnosis((current) => {
      const selectable = new Set(defaultDiagnosisSelection(next));
      const kept = current.filter((ref) => selectable.has(ref));
      return kept.length ? kept : [...selectable];
    });
  }, []);

  const refreshWorkspace = useCallback(async () => {
    if (!reviewContextRef) {
      return null;
    }
    const next = await itemReviewApi.getWorkspace(projectId, reviewContextRef);
    applyWorkspace(next);
    return next;
  }, [applyWorkspace, projectId, reviewContextRef]);

  // C44：写入成功之后的这次刷新如果失败，错误属于「没取回最新数据」，不是「没写成功」。
  // 两者共用一个 try/catch 时用户会以为写入失败而重试——重试带的是新幂等键，后端认不出
  // 是重放，条目已进新状态，状态机自环再弹一条错。故写入与刷新分开，各说各的话。
  const refreshAfterWrite = useCallback(async (): Promise<
    { ok: true; workspace: Awaited<ReturnType<typeof refreshWorkspace>> } | { ok: false }
  > => {
    try {
      return { ok: true, workspace: await refreshWorkspace() };
    } catch {
      return { ok: false };
    }
  }, [refreshWorkspace]);

  // 诊断中轮询体（单条目结论实时入流；后台线程只亮会话条徽标，不打扰当前线程）。
  // 返回 RunPollTick 交 hook：在途且有进展→复位停滞时钟；在途无进展→累积停滞判定；收束→停表。
  const pollWorkspace = useCallback(async (): Promise<RunPollTick> => {
    if (!reviewContextRef) {
      return { done: true };
    }
    try {
      const next = await itemReviewApi.getWorkspace(projectId, reviewContextRef);
      applyWorkspace(next);
      if (hasRunning(next)) {
        const progress = runningProgressTotal(next);
        const advanced = lastProgressRef.current === null || progress !== lastProgressRef.current;
        lastProgressRef.current = progress;
        return { done: false, stallCandidate: !advanced };
      }
      lastProgressRef.current = null;
      setNoticeText(next.next_action ?? '本次诊断已收束。');
      return { done: true };
    } catch (error) {
      // 瞬时轮询失败不停表：停表会让「诊断中」徽标与进度条冻结在旧快照且无恢复路径，批完成/失败
      // toast 也永不触发。继续按节奏重试，错误如实提示；非停滞候选=复位停滞时钟（取数失败非"卡死"）。
      setNoticeText(getErrorMessage(error));
      return { done: false, stallCandidate: false };
    }
  }, [applyWorkspace, projectId, reviewContextRef]);

  // 诊断在途时启动轮询（幂等：hook.start 抢占旧 watch，重复调用不叠加）。
  const startPolling = useCallback(() => {
    lastProgressRef.current = null;
    startRunWatch(pollWorkspace);
  }, [pollWorkspace, startRunWatch]);

  // 初始进入：以后端评审工作区（AEP-033）为准；不可达时回落本地待诊断投影。
  useEffect(() => {
    const initial = makeInitialWorkspace(sourceWorkspace);
    setWorkspace(initial);
    setSelectedForDiagnosis(defaultDiagnosisSelection(initial));
    setCurrentItemRef(initial.review_items[0]?.item_ref ?? null);
    if (!reviewContextRef) {
      return;
    }
    void (async () => {
      try {
        const next = await itemReviewApi.getWorkspace(projectId, reviewContextRef);
        applyWorkspace(next);
        if (hasRunning(next)) {
          startPolling();
        }
      } catch (error) {
        setNoticeText(`评审工作区读取失败：${getErrorMessage(error)}`);
      }
    })();
  }, [applyWorkspace, projectId, reviewContextRef, sourceWorkspace, startPolling]);

  // run 级聚合失败反馈：running→completed 迁移时结算一次（迁移即去重；失败条数直取后端 per-run
  // failed_count，右上角一条不逐条弹）。toast=在页即时；离页可发现走通知中心（notify_agent_run_failed），
  // 二者分工不双重轰炸——toast 只在本页在途结算时弹，通知中心承接离页/历史可发现（口径见页面详设 §9）。
  const [notifyApi, notifyContextHolder] = notification.useNotification();
  useEffect(() => {
    const { toasts, nextStatus } = collectRunFailureToasts(
      runStatusRef.current,
      workspace.diagnosis_runs,
    );
    runStatusRef.current = nextStatus;
    for (const toast of toasts) {
      notifyApi.error({
        key: `diagnosis-run-${toast.runRef}`,
        title: '诊断批次未全部完成',
        description: `${toast.failedCount} 个条目诊断失败，失败原因见对应条目线程与链路回执条。`,
        placement: 'topRight',
      });
    }
  }, [notifyApi, workspace.diagnosis_runs]);

  // 在途批次确定型进度（已出结论 n/N；分母=发起时捕获）；批次收束返回 null，进度不残留
  const runProgress = useMemo(
    () => deriveDiagnosisRunProgress(workspace.diagnosis_runs),
    [workspace.diagnosis_runs],
  );
  const awaitingCount = useMemo(
    () => workspace.review_items.filter((item) => item.review_status === 'awaiting_adjudication').length,
    [workspace.review_items],
  );

  const currentItem = useMemo(
    () => workspace.review_items.find((item) => item.item_ref === currentItemRef) ?? workspace.review_items[0] ?? null,
    [currentItemRef, workspace.review_items],
  );
  const itemVMs = useMemo(
    () => mapReviewItems(workspace.review_items, selectedForDiagnosis, currentItem?.item_ref ?? null),
    [currentItem, selectedForDiagnosis, workspace.review_items],
  );
  // 区4 留痕卡：用户改过稿再采纳的建议（AI 原案与实际应用稿并排）
  const editedPointTrail = useMemo(
    () => (currentItem ? collectEditedPointTrail(currentItem) : []),
    [currentItem],
  );
  const groups = useMemo(() => groupReviewItems(itemVMs), [itemVMs]);
  const strip = useMemo(
    () => buildThreadStrip(workspace.review_items, feedMode ? null : currentItem?.item_ref ?? null),
    [currentItem, feedMode, workspace.review_items],
  );
  const feed = useMemo(() => buildActivityFeed(workspace), [workspace]);
  const sourceElementsById = useMemo(() => mapReviewSourceElementsById(workspace), [workspace]);
  const anchors = useMemo(() => resolveReviewAnchors(workspace), [workspace]);
  const canvasBlocks = useMemo(
    () => buildReviewSourceCanvas(workspace, currentItem, anchors),
    [anchors, currentItem, workspace],
  );

  const standing = currentItem?.current_verdict ?? null;

  // 演示留痕（COMMAND 交换与失败回执，投影不含）：按当前条目拉取，与投影按时间序合并。
  // 绑定 itemRef 防切条目串味；随 workspace_version 变化重拉（命令落定后 sendReviewCommand 必刷工作区，
  // 与「工作区重拉取时机」同步——方案 §2.3）。刷新时挂载即拉，与命令交换等价复现。
  const [transcript, setTranscript] = useState<{ itemRef: string; rows: ChatTranscriptRow[] }>({ itemRef: '', rows: [] });
  // 专用重拉信号（F1）：/诊断、/找来源、拒绝类回复等命令会写留痕却不改 workspace_version，
  // 单靠版本号触发的重拉拉不回这些新行（当场消失，须刷新才现）。sendReviewCommand 每轮对话往返
  // 完成（成功与失败路径皆然）后自增此计数，把新写的留痕行当场拉回显示。
  const [transcriptReloadSignal, setTranscriptReloadSignal] = useState(0);
  useEffect(() => {
    if (!projectId || !currentItemRef || feedMode) return;
    let cancelled = false;
    void fetchChatTranscript(projectId, 'review', currentItemRef)
      .then((res) => { if (!cancelled) setTranscript({ itemRef: currentItemRef, rows: res.rows }); })
      .catch(() => { /* 水合失败不打断页面 */ });
    return () => { cancelled = true; };
  }, [projectId, currentItemRef, feedMode, workspace.workspace_version, transcriptReloadSignal]);

  // 线程投影（结论有效性规则复用 buildThread，落页面侧映射器）⊕ 留痕行；控件按此重投影，自带滚动到底。
  const threadMessages = useMemo<ChatMessage[]>(() => {
    if (!currentItem || feedMode) return [];
    const rows = transcript.itemRef === currentItem.item_ref ? transcript.rows : [];
    return mergeReviewThread(projectItemThread(currentItem), rows);
  }, [currentItem, feedMode, transcript]);

  const switchThread = useCallback((itemRef: string) => {
    setFeedMode(false);
    setCurrentItemRef(itemRef);
  }, []);

  const toggleDiagnosisRange = useCallback((itemRef: string) => {
    const item = workspace.review_items.find((candidate) => candidate.item_ref === itemRef);
    if (!item || !item.available_actions.some((a) => a.key === 'request_diagnosis' && a.enabled)) {
      return;
    }
    setSelectedForDiagnosis((current) =>
      current.includes(itemRef) ? current.filter((ref) => ref !== itemRef) : [...current, itemRef],
    );
  }, [workspace.review_items]);

  // ---- 结论裁决（直发，不经模型解析）：采纳/拒绝均走控件 host 动作或理由弹层，动作态由 phaseOf 承载 ----
  // 载荷（item_ref/round_ref/verdict_kind/selected_point_refs）由结论卡预埋透传；工作区版本发送瞬间读 ref。
  const doAdjudicate = useCallback(
    async (args: {
      itemRef: string;
      roundRef: string;
      verdictKind: VerdictRead['verdict_kind'];
      decision: 'adopted' | 'rejected';
      selectedPointRefs: string[] | null;
      /** 采纳修订时的逐点改稿；null/空=按 AI 原案采纳 */
      pointEdits?: Record<string, string> | null;
      reason: string | null;
    }): Promise<{ ok: boolean; message?: string }> => {
      setNoticeText(null);
      try {
        const next = await itemReviewApi.adjudicateVerdict(projectId, {
          project_ref: projectId,
          item_ref: args.itemRef,
          round_ref: args.roundRef,
          decision: args.decision,
          selected_point_refs:
            args.decision === 'adopted' && args.verdictKind === 'revise' ? args.selectedPointRefs ?? [] : null,
          point_edits:
            args.decision === 'adopted' && args.verdictKind === 'revise' ? args.pointEdits ?? null : null,
          reason: args.reason,
          workspace_version: workspaceRef.current.workspace_version,
          operator_ref: operator,
          idempotency_key: createIdempotencyKey(),
        });
        applyWorkspace(next);
        if (hasRunning(next)) startPolling();
        const after = next.review_items.find((i) => i.item_ref === args.itemRef);
        const reqNo = after?.req_no ?? args.itemRef;
        let note: string;
        if (args.decision === 'adopted' && args.verdictKind === 'pass') {
          note = `已采纳「建议通过」：${reqNo} 已确认（无第二步）。`;
          const nxt = nextAwaitingItem(next.review_items, args.itemRef);
          if (nxt && after?.review_status === 'confirmed') setCurrentItemRef(nxt);
        } else if (args.decision === 'adopted') {
          // 「改完会不会自动重诊」不是前端能断言的事：续接与否由服务端按阶段策略定。
          // 此处只陈述已发生的（内容已应用、旧结论失效），后半句回读服务端刚算出的显示态说明，
          // 由它说清接下来是「正在重诊」还是要用户手动发起。
          note =
            args.verdictKind === 'revise'
              ? `${args.pointEdits && Object.keys(args.pointEdits).length
                  ? '已按你改后的内容应用'
                  : '修订已应用'}，旧结论随版本失效。${after?.display_note ?? ''}`
              : args.verdictKind === 'withdraw'
                ? `已采纳「建议撤回」：${reqNo} 已终止。`
                : '来源缺口已登记：补充来源或修订表达后可再诊断。';
        } else {
          note = '结论已拒绝作废：可重新诊断、人工修订、覆盖确认或撤回。';
        }
        setNoticeText(note);
        return { ok: true, message: note };
      } catch (error) {
        const message = getErrorMessage(error);
        setNoticeText(message);
        return { ok: false, message };
      }
    },
    [applyWorkspace, operator, projectId, startPolling],
  );

  // 拒绝结论（理由来自弹层）：拒绝是裁决，走直发 adjudicate（rejected），与采纳对称、不经 LLM 命令解析。
  const rejectVerdict = useCallback(
    async (itemRef: string, verdict: VerdictRead, reason: string): Promise<void> => {
      await doAdjudicate({
        itemRef,
        roundRef: verdict.round_ref,
        verdictKind: verdict.verdict_kind,
        decision: 'rejected',
        selectedPointRefs: null,
        reason: reason || null,
      });
    },
    [doAdjudicate],
  );

  // AEP-116 标记「这条不是问题」/ 撤销标记：直发端点，不经 LLM 命令解析（与拒绝结论同类）。
  // 标记登记的是问题指纹，后端此后所有轮次都不再把它计入阻断；撤销则恢复计入。
  const setFindingVeto = useCallback(
    async (itemRef: string, args: { findingRef?: string; vetoRef?: string; reason?: string | null }) => {
      setBusy(true);
      setNoticeText(null);
      try {
        const next = await itemReviewApi.recordFindingVeto(projectId, {
          project_ref: projectId,
          item_ref: itemRef,
          action: args.vetoRef ? 'restore' : 'veto',
          finding_ref: args.findingRef ?? null,
          veto_ref: args.vetoRef ?? null,
          reason: args.reason ?? null,
          operator_ref: operator,
          idempotency_key: createIdempotencyKey(),
        });
        applyWorkspace(next);
        setNoticeText(args.vetoRef ? '已恢复这条，重新计入。' : '已标记为不是问题，以后不再提示这一条。');
      } catch (error) {
        setNoticeText(getErrorMessage(error));
      } finally {
        setBusy(false);
      }
    },
    [applyWorkspace, operator, projectId],
  );

  // ---- 区3 拖选指定来源 ----
  //
  // 用户明确知道这条需求出自材料哪句话时，不必再绕 AI 找候选：直接在区3 正文里拖选那句话。
  // 坐标换算与需求分析页区3 同一套（那里已用于「补入遗漏」）：取选区端点所在 span 的
  // data-seg-start（该片段在语料中的起始位置），加上端点在文本节点内的偏移。片段内只有
  // 一个文本节点，所以这是查表加法，得到的是精确的语料坐标——不是文本匹配，也不做近似。
  const canvasRef = useRef<HTMLElement | null>(null);
  const [canvasSelection, setCanvasSelection] = useState<CanvasTextSelection | null>(null);

  // 待确认态的条目都可拖选指定来源（C3：门禁由「待补充来源」放宽为「待确认态」）：背书（人工确认）
  // 后又在材料里发现真实出处时，仍能拖选登记真实来源，不至于堵死补救路径。门禁语义不变（待确认态放宽
  // 仍成立），但可用性读后端动作事实 apply_manual_revision.enabled（即 can_manual＝待确认且非诊断中，
  // 见 C14(b) :494-495），不自算门禁——诊断中后端禁写这四个动作，确认条随之不出。available_actions 为
  // 空时（本地兜底投影）回落状态位判断，仿 :115-116 既有习惯。拖选确认条只在用户主动划选时才出，属低
  // 打扰形态；AI 自动查候选与补充来源出口卡仍只在「待补充来源」缺口态出（见 supplementItemRef，不在此
  // 放宽）。其余状态下正文照样能选中能复制，只是不弹确认条。动态流下无「当前条目」概念，拖选不得再写入
  // 来源（C20：与 supplementItemRef 的 !feedMode 守卫对齐）。
  const canSpecifySource =
    !feedMode && !!currentItem && (
      currentItem.available_actions.length
        ? currentItem.available_actions.some((a) => a.key === 'apply_manual_revision' && a.enabled)
        : currentItem.status === 'pending_confirmation'
    );

  const handleCanvasMouseUp = useCallback(() => {
    const domSelection = window.getSelection();
    if (!canvasRef.current || !canSpecifySource) {
      return;
    }
    if (!domSelection || domSelection.isCollapsed) {
      setCanvasSelection(null);  // 空选＝点了一下取消选择，确认条随之收起
      return;
    }
    const range = domSelection.getRangeAt(0);
    if (!canvasRef.current.contains(range.commonAncestorContainer)) {
      return;  // 选区不在区3 正文里
    }
    const toGlobal = (node: Node, offset: number): number | null => {
      const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement);
      const segStart = el?.getAttribute('data-seg-start');
      if (segStart === null || segStart === undefined) {
        return null;  // 端点落在没有坐标的节点上（如标题）：宁可不给，也不猜一个位置
      }
      return Number(segStart) + offset;
    };
    const start = toGlobal(range.startContainer, range.startOffset);
    const end = toGlobal(range.endContainer, range.endOffset);
    if (start === null || end === null || end <= start) {
      setCanvasSelection(null);
      return;
    }
    const text = (workspace.material_canvas.raw_text ?? '').slice(start, end);
    if (!text.trim()) {
      setCanvasSelection(null);  // 纯空白选区没有指认价值
      return;
    }
    setCanvasSelection({ start, end, text });
  }, [canSpecifySource, workspace]);

  // 选区落在哪些知识项上（纯区间相交，前端算得出，不必问后端）
  const selectionHits = useMemo(
    () => (canvasSelection ? findSelectionHits(canvasBlocks, canvasSelection, sourceElementsById) : []),
    [canvasBlocks, canvasSelection, sourceElementsById],
  );

  /** 收起确认条，并把浏览器里那段蓝底也一并清掉（只留一个的话看着像还没取消）。 */
  const clearCanvasSelection = useCallback(() => {
    setCanvasSelection(null);
    window.getSelection()?.removeAllRanges();
  }, []);

  // 切换条目时清掉旧选区：那段选中的话是针对上一条说的，留着会张冠李戴
  useEffect(() => {
    setCanvasSelection(null);
  }, [currentItemRef]);

  // 人工确认背书：材料里漏写了这条，用户确认它是真实需求并负责登记，条目据此离开
  // 「待补充来源」。这是对「条目的依据必须能在材料里指出来」的授权例外，所以理由必填、
  // 全程留痕；后端只登记背书事实，不写任何材料锚点、不生成引文、不动来源要素。
  const attestSource = useCallback(
    async (itemRef: string, reason: string) => {
      setBusy(true);
      setNoticeText(null);
      try {
        const next = await itemReviewApi.attestSource(projectId, {
          project_ref: projectId,
          item_ref: itemRef,
          reason,
          operator_ref: operator,
          idempotency_key: createIdempotencyKey(),
        });
        applyWorkspace(next);
        fetchedSourcesRef.current.delete(itemRef);
        setSourceExit((cur) => {
          const nextState = { ...cur };
          delete nextState[itemRef];
          return nextState;
        });
        // 背书后条目仍停在待确认（C3 放宽门禁为「待确认态」后不再靠「离开待补充来源」自动收起），
        // 显式清拖选选区，否则确认条会带着旧选区滞留、指向已消失的〔人工确认〕（照 registerSource 写法）。
        clearCanvasSelection();
        setNoticeText('已记下你的确认；这条已离开「待补充来源」，点上方「发起诊断」可继续评审。');
      } catch (error) {
        setNoticeText(getErrorMessage(error));
      } finally {
        setBusy(false);
      }
    },
    [applyWorkspace, clearCanvasSelection, operator, projectId],
  );

  // 整条重写：AI 只报了问题没给改法时，用户自己把整条改掉。走既有的人工修订通道
  // （AEP-036 MANUAL，与快捷命令「人工修订」同一条），不经结论裁决——用户没有采纳 AI 的结论，
  // 而是自己改了条目，旧结论随修订失效。该通道不自动复诊（阶段策略解耦 P1 的既有口径），
  // 所以提示里如实说明要再手动发起一次。
  const rewriteExpression = useCallback(
    async (itemRef: string, expression: string) => {
      setBusy(true);
      setNoticeText(null);
      try {
        const result = await requirementsApi.applyItemRevision(projectId, itemRef, {
          project_ref: projectId,
          item_ref: itemRef,
          workspace_version: workspaceRef.current.workspace_version,
          revision_mode: 'manual',
          field_key: 'expression',
          revised_value: expression,
          suggestion_ref: null,
          accept_mode: null,
          reason: '结论卡整条重写（AI 未给出改法）',
          operator_ref: operator,
          idempotency_key: createIdempotencyKey(),
        });
        // C40：后端前置检查（如版本不一致）返回 HTTP 200 加 status != 'applied'，不抛异常。
        // 此时条目一个字没改，不能谎报成功——如实告知失败并保留用户草稿（本地 rewrites 不动）。
        if (result.status !== 'applied') {
          setNoticeText(result.next_action ?? '整条替换没有生效，请刷新后重试。');
          return;
        }
        const done = '已按你写的整条替换；旧结论已失效，可以重新发起诊断。';
        const refreshed = await refreshAfterWrite();
        setNoticeText(refreshed.ok ? done : `${done}（${REFRESH_FAILED_NOTE}）`);
      } catch (error) {
        setNoticeText(getErrorMessage(error));
      } finally {
        setBusy(false);
      }
    },
    [operator, projectId, refreshAfterWrite],
  );

  // 否决消解后的确认：本轮建议已被逐条标为不是问题，条目可直接确认。这不是覆盖确认——
  // 后端会重新核算「阻断问题一条不剩」这个谓词，核算不过就照常拒绝。
  const confirmVetoCleared = useCallback(
    async (itemRef: string) => {
      setBusy(true);
      setNoticeText(null);
      try {
        const result = await itemReviewApi.confirmItem(projectId, {
          project_ref: projectId,
          item_ref: itemRef,
          workspace_version: workspaceRef.current.workspace_version,
          override: false,
          reason: null,
          operator_ref: operator,
          idempotency_key: createIdempotencyKey(),
        });
        const refreshed = await refreshAfterWrite();
        if (result.status !== 'confirmed') {
          setNoticeText(result.next_action ?? '这个条目暂时还不能确认。');
          return;
        }
        if (!refreshed.ok) {
          setNoticeText(`条目已确认（${REFRESH_FAILED_NOTE}）。`);
          return;
        }
        const next = refreshed.workspace;
        const after = next?.review_items.find((i) => i.item_ref === itemRef);
        setNoticeText(`${after?.req_no ?? itemRef} 已确认。`);
        if (next && after?.review_status === 'confirmed') {
          const nxt = nextAwaitingItem(next.review_items, itemRef);
          if (nxt) setCurrentItemRef(nxt);
        }
      } catch (error) {
        setNoticeText(getErrorMessage(error));
      } finally {
        setBusy(false);
      }
    },
    [operator, projectId, refreshAfterWrite],
  );

  // 标记/撤销一条问题**不**改工作区版本（改了会作废用户手里的确认版本，「标完直接确认」这条
  // 通道会自己把自己堵死），所以质量投影不能只依赖版本号，否则区4 停在标记前的画面：那条已被
  // 标记的问题还带着序号、色块与「一键修复」。用本条目的否决账目签名当第二个触发。
  const vetoSignature = (currentItem?.finding_vetoes ?? [])
    .map((v) => `${v.veto_ref}:${v.revoked ? 'x' : 'o'}`)
    .join('|');

  // v2 质量诊断器：当前条目最新一轮质量投影（span 标注/6 维/EARS/对齐分）；随工作区版本与否决账目刷新
  useEffect(() => {
    let cancelled = false;
    setQuality(null);
    if (!projectId || !currentItemRef) return undefined;
    qualityApi
      .getItemQuality(projectId, currentItemRef)
      .then((q) => { if (!cancelled) setQuality(q); })
      .catch(() => { if (!cancelled) setQuality(null); });
    return () => { cancelled = true; };
  }, [projectId, currentItemRef, workspace.workspace_version, vetoSignature]);

  // 一键修复：采纳站立结论的指定修订点（复用 adjudicateVerdict revise 采纳链）
  const adoptQualityPoint = useCallback(async (pointRef: string) => {
    if (!currentItem?.current_verdict || currentItem.current_verdict.verdict_kind !== 'revise') return;
    setBusy(true);
    setNoticeText(null);
    try {
      const next = await itemReviewApi.adjudicateVerdict(projectId, {
        project_ref: projectId,
        item_ref: currentItem.item_ref,
        round_ref: currentItem.current_verdict.round_ref,
        decision: 'adopted',
        selected_point_refs: [pointRef],
        reason: null,
        workspace_version: workspace.workspace_version,
        operator_ref: operator,
        idempotency_key: createIdempotencyKey(),
      });
      applyWorkspace(next);
      if (hasRunning(next)) startPolling();
      setNoticeText('已采纳修订点，旧结论随版本失效，已自动发起增量诊断。');
    } catch (error) {
      setNoticeText(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [applyWorkspace, currentItem, operator, projectId, workspace.workspace_version]);

  // 采纳对话草案（adopt_draft host 动作）：走 AEP-036 修订通道（accept_suggestion），随后刷新并复诊。
  const doApplyRevisionDraft = useCallback(
    async (itemRef: string, suggestionRef: string): Promise<{ ok: boolean; message?: string }> => {
      setNoticeText(null);
      try {
        const result = await requirementsApi.applyItemRevision(projectId, itemRef, {
          project_ref: projectId,
          item_ref: itemRef,
          workspace_version: workspaceRef.current.workspace_version,
          revision_mode: 'accept_suggestion',
          field_key: 'expression',
          revised_value: null,
          suggestion_ref: suggestionRef,
          accept_mode: null,
          reason: '采纳对话草案',
          operator_ref: operator,
          idempotency_key: createIdempotencyKey(),
        });
        // C8：后端前置检查失败时返回 HTTP 200 加 status != 'applied'，条目一个字没改——不能谎报成功
        // （照 :1349 现成写法）。如实回失败，调用方据 ok=false 保留草案、提示用户刷新重试。
        if (result.status !== 'applied') {
          const failNote = result.next_action ?? '草案没有应用成功（可能版本不一致），请刷新后重试。';
          setNoticeText(failNote);
          return { ok: false, message: failNote };
        }
        const note = result.next_action ?? '修订已应用。';
        // C44：修订已经写进去了。这之后的刷新失败不改变「已应用」这个事实，也不能把返回值
        // 翻成 ok:false——调用方会据此当成写入失败处理。
        const refreshed = await refreshAfterWrite();
        const message = refreshed.ok ? note : `${note}（${REFRESH_FAILED_NOTE}）`;
        setNoticeText(message);
        startPolling();
        return { ok: true, message };
      } catch (error) {
        const message = getErrorMessage(error);
        setNoticeText(message);
        return { ok: false, message };
      }
    },
    [operator, projectId, refreshAfterWrite, startPolling],
  );

  // ---- 补充来源出口（issue #30）：找候选 / 登记来源 / 自动接续复诊 ----

  // /找来源：在候选差集里检索候选来源。命令不持久化，结果存组件态供出口卡渲染；
  // 与主输入框对话分开——不占用 busy/trace/notice，只翻转本条 sourceExit。
  const fetchSourceCandidates = useCallback(async (
    itemRef: string,
    message?: string,
    /** 谁发起的：用户点〔重新查找〕〔按说明查找〕='user'；页面进入该态自动查一次='auto'。
     *  后端据此决定写不写留痕——自动查不是用户说的话，写进去就成了刷新一次多一对的幻影气泡。 */
    origin: 'user' | 'auto' = 'user',
  ) => {
    fetchedSourcesRef.current.add(itemRef);
    setSourceExit((cur) => ({
      ...cur,
      [itemRef]: { loading: true, candidates: null, message: null, nextAction: null },
    }));
    try {
      const result = await itemReviewApi.reviewDialogue(projectId, {
        project_ref: projectId,
        item_ref: itemRef,
        message: message ?? QUICK_COMMAND_PREFILLS.findSources(),
        draft_ref: null,
        selected_item_refs: [],
        workspace_version: workspaceRef.current.workspace_version,
        operator_ref: operator,
        idempotency_key: createIdempotencyKey(),
        user_initiated: origin === 'user',
      });
      const cards = result.source_candidates ? buildSourceCandidateCards(result.source_candidates) : [];
      setSourceExit((cur) => ({
        ...cur,
        [itemRef]: {
          loading: false,
          candidates: cards,
          message: result.message ?? null,
          nextAction: result.next_action ?? null,
        },
      }));
    } catch (error) {
      setSourceExit((cur) => ({
        ...cur,
        [itemRef]: { loading: false, candidates: [], message: getErrorMessage(error), nextAction: null },
      }));
    }
  }, [operator, projectId]);

  // 登记候选为本条来源：整集替换（当前来源 ∪ 候选）走 AEP-036 修订通道。登记只**关闭缺口**、
  // 不自动复诊（阶段策略解耦 stage-policy-p1：直发修订不自动链式复诊；自动复诊在本机 LLM 上
  // 又慢又易失败、还会再判 supplement 导致循环，2026-07-16 用户改回一键发起）。登记成功后条目
  // 离开「待补充来源」进入待诊断，回执明确指引用户点区2/区5 的〔发起诊断〕一键复核，不替用户打长调用。
  const registerSource = useCallback(async (itemRef: string, candidateRef: string) => {
    const item = workspaceRef.current.review_items.find((i) => i.item_ref === itemRef);
    if (!item) return;
    setBusy(true);
    setNoticeText(null);
    try {
      const result = await requirementsApi.applyItemRevision(projectId, item.item_ref, {
        project_ref: projectId,
        item_ref: item.item_ref,
        workspace_version: workspaceRef.current.workspace_version,
        revision_mode: 'manual',
        field_key: 'source_element_refs',
        revised_value: buildSourceRegistrationValue(item.source_element_refs, candidateRef),
        suggestion_ref: null,
        accept_mode: null,
        reason: '登记候选为本条来源',
        operator_ref: operator,
        idempotency_key: createIdempotencyKey(),
      });
      // C8：后端前置检查（如版本不一致）返回 HTTP 200 加 status != 'applied'，条目一个字没改。
      // 不能谎报成功——如实告知失败，且**不清**候选态与 fetchedSourcesRef 守卫、不清选区：清了会让
      // 出口卡因依赖表未变不再自动重查而永远停在「正在查找候选来源…」，把用户彻底卡死（照 :1349 现成写法）。
      if (result.status !== 'applied') {
        setNoticeText(result.next_action ?? '来源没有登记成功（可能版本不一致），请刷新后重试。');
        return;
      }
      // 先刷新（此刻缺口已闭合、条目离开「待补充来源」）再清本条候选态与守卫——放在刷新之后，
      // 避免清守卫瞬间条目仍是 supplement_pending 而触发一次多余的 /找来源。日后若重回该态会自动重查。
      const refreshed = await refreshAfterWrite();
      fetchedSourcesRef.current.delete(item.item_ref);
      setSourceExit((cur) => {
        const next = { ...cur };
        delete next[item.item_ref];
        return next;
      });
      // 拖选确认条的收起由渲染点 onRegister 回调里的同步 clearCanvasSelection() 完成
      // （void registerSource(...) 跑到首个 await 即交回控制权，随即清选区），与条目登记来源后
      // 状态是否变化无关，此处不必再清。
      setNoticeText(
        refreshed.ok
          ? `${result.next_action ?? '来源已登记。'}；点上方「发起诊断」即可复核本条。`
          : `${result.next_action ?? '来源已登记。'}（${REFRESH_FAILED_NOTE}）`,
      );
    } catch (error) {
      setNoticeText(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [operator, projectId, refreshAfterWrite]);

  // 进入「待补充来源」态自动查一次候选（ADR-0002 P3 说缺必说补：缺口与候选一起出现）；
  // 守卫集防轮询/重渲染重复触发（同一 item_ref 只自动查一次），〔重新查找〕显式重取。
  // origin='auto'：这一次是页面自己发的，不写留痕（守卫集每次挂载新建，否则刷新一次多一对气泡）。
  const supplementItemRef =
    currentItem && !feedMode && isSupplementPending(currentItem) ? currentItem.item_ref : null;
  useEffect(() => {
    if (!supplementItemRef) {
      return;
    }
    if (fetchedSourcesRef.current.has(supplementItemRef)) {
      return;
    }
    void fetchSourceCandidates(supplementItemRef, undefined, 'auto');
  }, [fetchSourceCandidates, supplementItemRef]);

  // ---- 发送入口：整段原文交 AEP-095（SSE 流式，stage 帧驱动控件内建链路回执条；前端不解析）----
  // 链路回执条改由控件内建（trace-rail），页面不再手写 trace；发送瞬间的命令体拼装与结果后处理留页面侧。

  /** 命令体拼装：发送瞬间读最新工作区版本与区1 勾选范围（拉不是塞）。 */
  const buildReviewCommand = useCallback(
    (itemRef: string, message: string): ReviewDialogueCommand => ({
      project_ref: projectId,
      item_ref: itemRef,
      message,
      draft_ref: null,
      selected_item_refs: selectedForDiagnosisRef.current,
      workspace_version: workspaceRef.current.workspace_version,
      operator_ref: operator,
      idempotency_key: createIdempotencyKey(),
    }),
    [operator, projectId],
  );

  /** 发送 + 结果后处理（source_candidates 回流出口卡、命令回执 notice、start_diagnosis 清勾选、刷新与条件轮询）。
   *  控件路径与区2 发起诊断共用；控件自管乐观行/在途/回执条，此处只做页面侧副作用与工作区重投影。 */
  const sendReviewCommand = useCallback(
    async (command: ReviewDialogueCommand, onStage?: (stage: string) => void): Promise<ReviewDialogueResult> => {
      try {
        const result = await itemReviewApi.reviewDialogueStream(projectId, command, { onStage });
        if (result.outcome_type === 'command') {
          const echo = result.operation_label ? `［${result.operation_label}］` : '';
          setNoticeText(`${echo}${(result.message ?? result.next_action) ?? '命令已执行。'}`);
          if (result.source_candidates) {
            // /找来源 的命令结果回流出口卡：重跑候选与自动查同一渲染路径，出口卡即时可登记（ADR-0002 无死胡同）
            fetchedSourcesRef.current.add(command.item_ref);
            setSourceExit((cur) => ({
              ...cur,
              [command.item_ref]: {
                loading: false,
                candidates: buildSourceCandidateCards(result.source_candidates ?? []),
                message: result.message ?? null,
                nextAction: result.next_action ?? null,
              },
            }));
          }
          if (result.operation === 'start_diagnosis' && result.agent_run_ref) {
            setSelectedForDiagnosis([]);
          }
        } else {
          setNoticeText(result.message ?? result.next_action ?? null);
        }
        await refreshWorkspace();
        if (result.outcome_type === 'reeval' || result.agent_run_ref) {
          startPolling();
        }
        return result;
      } finally {
        // 每轮对话往返完成即触发留痕专用重拉（F1）：成功与失败路径都要——版本号未变的命令
        // （诊断/找来源/拒绝类）新写的留痕行靠此当场拉回，失败路径的失败留痕行亦然。
        setTranscriptReloadSignal((n) => n + 1);
      }
    },
    [projectId, refreshWorkspace, startPolling],
  );

  /** 「先补理由再发送」的对话命令（撤回该条走此路）：与控件发送同链，走同一后处理。 */
  const runReviewCommand = useCallback(
    async (itemRef: string, message: string): Promise<void> => {
      try {
        await sendReviewCommand(buildReviewCommand(itemRef, message));
      } catch (error) {
        setNoticeText(getErrorMessage(error));
      }
    },
    [buildReviewCommand, sendReviewCommand],
  );

  // 传输绑定：控件所有 submit/自由输入发送走此（buildCommand 发送瞬间快照、send 内建回执条 onStage）。
  const transport = useMemo<DialogueTransport<ReviewDialogueCommand, ReviewDialogueResult>>(
    () => ({
      buildCommand: (text) => buildReviewCommand(currentItemRefForSend.current, text),
      send: (command, handlers: StreamHandlers<ReviewDialogueResult>) => {
        let aborted = false;
        void (async () => {
          try {
            const result = await sendReviewCommand(command, (stage) => handlers.onStage?.(stage));
            if (!aborted) handlers.onResult(result);
          } catch (error) {
            if (!aborted) handlers.onError(error);
          }
        })();
        // 传输层不支持真正中止（AEP-095 无 abort 句柄）：尽力而为，忽略回执落线程。
        return { abort: () => { aborted = true; } };
      },
    }),
    [buildReviewCommand, sendReviewCommand],
  );

  // 控件适配器：会话键随当前条目切换（切对象即切线程），线程源为页面投影（projected）。
  currentItemRefForSend.current = currentItemRef ?? '';
  const adapter = useMemo<ChatHostAdapter>(
    () => ({
      hostId: 'item-review',
      sessionKey: () => `item-review:item:${currentItemRefForSend.current}`,
      sessionLabel: () => {
        const it = workspaceRef.current.review_items.find((i) => i.item_ref === currentItemRefForSend.current);
        return it ? `条目评审 · ${it.req_no}` : '条目评审';
      },
      transport,
      getContext: () => {
        const it = workspaceRef.current.review_items.find((i) => i.item_ref === currentItemRefForSend.current);
        return {
          item_ref: currentItemRefForSend.current,
          req_no: it?.req_no ?? '',
          selected_item_refs: selectedForDiagnosisRef.current,
          workspace_version: workspaceRef.current.workspace_version,
        };
      },
      // 「发送时携带」人读标签（仅展示；命令体恒取 getContext）：条目号／勾选数／工作区版本。
      contextChips: () => {
        const it = workspaceRef.current.review_items.find((i) => i.item_ref === currentItemRefForSend.current);
        const selected = selectedForDiagnosisRef.current.length;
        return [
          ...(it ? [`条目 ${it.req_no}`] : []),
          ...(selected > 0 ? [`已勾选 ×${selected}`] : []),
          `工作区 v${workspaceRef.current.workspace_version}`,
        ];
      },
      threadSource: { kind: 'projected', project: () => threadMessages },
      capabilities: { customCards: REVIEW_CUSTOM_CARDS, inlineWorkspace: false },
      quickCommands: REVIEW_QUICK_COMMANDS,
      actions: {
        adopt_verdict: async (payload) => {
          const p = payload as {
            item_ref: string;
            round_ref: string;
            verdict_kind: VerdictRead['verdict_kind'];
            selected_point_refs: string[] | null;
            point_edits?: Record<string, string> | null;
          };
          return doAdjudicate({
            itemRef: p.item_ref,
            roundRef: p.round_ref,
            verdictKind: p.verdict_kind,
            decision: 'adopted',
            selectedPointRefs: p.selected_point_refs,
            pointEdits: p.point_edits ?? null,
            reason: null,
          });
        },
        adopt_draft: async (payload) => {
          const p = payload as { item_ref: string; suggestion_ref: string };
          if (!p.suggestion_ref) return { ok: false, message: '草案标识缺失，无法采纳。' };
          return doApplyRevisionDraft(p.item_ref, p.suggestion_ref);
        },
      },
    }),
    [transport, threadMessages, doAdjudicate, doApplyRevisionDraft],
  );

  const canStartDiagnosis = workspace.available_operations.some(
    (op) => op.key === 'start_diagnosis' && op.enabled,
  );
  const startDiagnosisFact = workspace.available_operations.find((op) => op.key === 'start_diagnosis') ?? null;
  const selectableForDiagnosisCount = useMemo(() => defaultDiagnosisSelection(workspace).length, [workspace]);
  // 进行态优先本地 watcher（派发后首次刷新前服务端事实还没翻转）；禁用原因以服务端 ActionFact 为准
  const startDiagnosisTitle = diagnosisWatching
    ? '诊断进行中'
    : !canStartDiagnosis
      ? startDiagnosisFact?.disabled_reason ?? '没有可诊断的条目'
      : diagnosisScopeHint(selectedForDiagnosis.length, selectableForDiagnosisCount);

  // 区2 主入口：选模式后直接发起——与控件发送同链（同命令文本、同后处理与轮询），此路走页面级 busy。
  const startDiagnosisFromToolbar = useCallback(async (mode: DiagnosisMode) => {
    setModePopAnchor(null);
    if (!currentItem || busy) {
      return;
    }
    setBusy(true);
    try {
      await sendReviewCommand(
        buildReviewCommand(currentItem.item_ref, diagnosisLaunchCommand(mode, selectedForDiagnosis.length)),
      );
    } catch (error) {
      setNoticeText(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [buildReviewCommand, busy, currentItem, selectedForDiagnosis.length, sendReviewCommand]);

  const gateText = useMemo(() => {
    if (feedMode) return '动态流不承载裁决，点击任意行进入对应线程';
    if (!currentItem) return '—';
    // C8：状态条读后端说明句，不自己按显示态造句。否决消解态下后端把说明句换成了「可以直接
    // 确认这个条目」而显示态仍是 awaiting_adjudication（封闭集不新增成员是后端的取向），
    // 前端再自造一句「待你裁决」就与同屏的后端说明句正好相反。
    return reviewItemStatusNote(currentItem);
  }, [currentItem, feedMode]);

  // 人工确认刚闭合来源缺口的那一刻，状态说明句要看得见。此前它只以一行小字出现在
  // 「当前结论」条里，用户走到这一步读不到，于是不知道自己已经可以重新诊断了。
  // 判据取后端的 attestation_closed_gap，不看「背书过没有」，也不去匹配说明句的文本：
  // 背书是粘性事实，之后的普通修订同样让旧结论失效，只看「背书过」会把那次修订也说成
  // 来源缺口刚闭合；匹配文本则改一个字就失灵。文案本身仍逐字取后端说明句，不造第二套。
  // S2：只当真值用（三处消费全是三元判断），显示文字恒等于 gateText，故是布尔量不是字符串
  const attestedBanner = useMemo(
    () => !feedMode && !!currentItem?.attestation_closed_gap,
    [currentItem, feedMode],
  );

  const nextAwaiting = useMemo(
    () => nextAwaitingItem(workspace.review_items, currentItem?.item_ref ?? null),
    [currentItem, workspace.review_items],
  );

  // 理由弹层提交（拒绝结论/撤回该条/指定来源共用）：提交后关闭并清空。
  const handleReasonSubmit = useCallback(async () => {
    if (!reasonModal) return;
    setReasonBusy(true);
    try {
      await reasonModal.submit(reasonText.trim());
      setReasonModal(null);
      setReasonText('');
    } finally {
      setReasonBusy(false);
    }
  }, [reasonModal, reasonText]);

  // 逃生舱组件协同面：活状态与回调经 Context 下传（静态数据仍走投影 props）。
  const hostApi = useMemo<ReviewHostApi>(
    () => ({
      currentItem,
      busy,
      openReasonModal: (cfg) => {
        setReasonText('');
        setReasonModal(cfg);
      },
      sourceExitState: (itemRef) => sourceExit[itemRef],
      fetchSourceCandidates: (itemRef, message) => void fetchSourceCandidates(itemRef, message),
      registerSource: (itemRef, candidateRef) => void registerSource(itemRef, candidateRef),
      attestSource: (itemRef, reason) => void attestSource(itemRef, reason),
      rejectVerdict,
      runReviewCommand,
      setFindingVeto,
      confirmVetoCleared,
      rewriteExpression,
    }),
    [currentItem, busy, sourceExit, fetchSourceCandidates, registerSource, attestSource,
     rejectVerdict, runReviewCommand, setFindingVeto, confirmVetoCleared, rewriteExpression],
  );

  return (
    <div className="item-review-grid" aria-label="条目评审页面">
      {notifyContextHolder}
      <section className="item-review-zone item-review-zone--list" aria-label="区1 待评审条目与进度">
        <div className="intake-zone__header">
          <span>区1</span>
          <h3>待评审条目与进度</h3>
        </div>
        <div className="item-review-context">
          <strong>{workspace.material_canvas.title}</strong>
          <span>工作区版本：v{workspace.workspace_version}</span>
          <span>{selectedForDiagnosis.length} 条纳入本次诊断</span>
        </div>

        <div className="item-review-group-list">
          {groups.map((group) => (
            <section className="item-review-group" key={group.key} aria-label={group.label}>
              <div className="item-review-group__head">
                <strong>{group.label}</strong>
                <span>{group.items.length}</span>
              </div>
              {group.items.map((item) => (
                <div
                  aria-selected={item.current && !feedMode}
                  className={item.current && !feedMode ? 'review-list-item review-list-item--current' : 'review-list-item'}
                  key={item.itemRef}
                  onClick={() => switchThread(item.itemRef)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      switchThread(item.itemRef);
                    }
                  }}
                  role="option"
                  tabIndex={0}
                >
                  <label className="review-list-item__check" title={item.checkboxDisabledReason ?? undefined}>
                    <input
                      checked={item.selectedForDiagnosis}
                      disabled={!item.selectableForDiagnosis}
                      onChange={() => toggleDiagnosisRange(item.itemRef)}
                      onClick={(event) => event.stopPropagation()}
                      type="checkbox"
                    />
                  </label>
                  <div className="review-list-item__body">
                    <span className="review-list-item__head">
                      <strong>{item.reqNo}</strong>
                      <StatusPill tone={item.statusTone}>
                        {item.verdictGlyph ? `${item.statusText} · ${item.verdictGlyph}` : item.statusText}
                      </StatusPill>
                    </span>
                    <span>{item.expression}</span>
                    <em>{item.typeText} · {item.sourceCountText}</em>
                  </div>
                </div>
              ))}
            </section>
          ))}
        </div>
      </section>

      <div className="item-review-middle">
        <section className="item-review-zone item-review-zone--toolbar" aria-label="区2 导航与进度">
          <div>
            <div className="intake-zone__header">
              <span>区2</span>
              <h3>阶段进度</h3>
            </div>
          </div>
          <div className="item-review-toolbar">
            <Button icon={renderActionIcon('more')} onClick={() => void refreshWorkspace().then((n) => setNoticeText(n?.next_action ?? '工作区已刷新。')).catch((e) => setNoticeText(getErrorMessage(e)))}>
              读取工作区
            </Button>
            <Button icon={renderActionIcon('back')} onClick={onBackToFormation}>
              条目形成
            </Button>
            <Button onClick={onBackToMaintenance}>
              返回维护视图
            </Button>
            <div className="item-review-launch">
              <Button
                disabled={!canStartDiagnosis || busy || diagnosisWatching}
                onClick={() => setModePopAnchor((v) => (v === 'zone2' ? null : 'zone2'))}
                title={startDiagnosisTitle}
                type="primary"
              >
                ✨ 发起诊断
              </Button>
              {modePopAnchor === 'zone2' ? (
                <DiagnosisModePop
                  below
                  onPick={startDiagnosisFromToolbar}
                  selectedCount={selectedForDiagnosis.length}
                />
              ) : null}
            </div>
          </div>
          <div className="item-review-run">
            {diagnosisWatching || runProgress ? (
              <>
                <StatusPill tone="processing">诊断中</StatusPill>
                {runProgress ? (
                  <span aria-label="诊断批次进度" className="if-batch" role="status">
                    <span className="if-batch__bar" aria-hidden>
                      <span
                        className="if-batch__seg if-batch__seg--formed"
                        style={{ width: `${runProgress.pct}%` }}
                      />
                    </span>
                    <span className="if-batch__counts">{runProgress.countsText}</span>
                  </span>
                ) : (
                  <span>单条目结论实时入流</span>
                )}
              </>
            ) : (
              <>
                <StatusPill tone={workspace.confirmed_count === workspace.total_count && workspace.total_count > 0 ? 'success' : 'neutral'}>
                  已确认 {workspace.confirmed_count}/{workspace.total_count}
                </StatusPill>
                <span>
                  {reviewRunHint(
                    workspace.next_action,
                    selectedForDiagnosis.length,
                    selectableForDiagnosisCount,
                    awaitingCount,
                    hasRunning(workspace),
                  )}
                </span>
              </>
            )}
          </div>
          {diagnosisStalled ? (
            // 停滞=前端派生（复用 AI 请求链路回执条范式）：诊断长时间无进展，疑似执行器中断（B1 O1）。
            <p className="item-formation-notice item-formation-notice--warn" role="status">
              诊断任务长时间无进展，执行器可能已中断（任务仍标记进行中但未落地）。可在右上角运行态徽标进入诊断中心排查，或稍后重新发起。
            </p>
          ) : null}
          {noticeText ? (
            <p className="item-formation-notice" role="status">{noticeText}</p>
          ) : null}
        </section>

        <section className="item-review-zone item-review-zone--canvas" aria-label="区3 材料正文评审依据">
          <div className="intake-zone__header">
            <span>区3</span>
            <h3>材料正文（评审依据）</h3>
          </div>
          <article className="item-formation-canvas" onMouseUp={handleCanvasMouseUp} ref={canvasRef}>
            <h4>{workspace.material_canvas.title}</h4>
            {workspace.material_canvas.source_note ? <p className="analysis-canvas__note">{workspace.material_canvas.source_note}</p> : null}
            {canvasBlocks.map((block) => (
              <p key={block.blockId}>
                {block.segments.map((seg) =>
                  seg.refs.length ? (
                    <span
                      className={[
                        'canvas-highlight',
                        `canvas-highlight--${seg.primaryColorKey ?? 'term'}`,
                        currentItem?.source_element_refs.some((ref) => seg.refs.includes(ref)) ? 'canvas-highlight--selected' : '',
                      ].join(' ')}
                      data-first-ref={seg.refs[0]}
                      data-seg-start={seg.start}
                      key={seg.key}
                    >
                      {seg.text}
                    </span>
                  ) : (
                    <span data-seg-start={seg.start} key={seg.key}>{seg.text}</span>
                  ),
                )}
              </p>
            ))}
          </article>
          {/* 再判一次 canSpecifySource：动态流/条目切换让 canSpecifySource 转假时确认条随之收起；
              登记成功的收起由下方 onRegister 回调的同步 clearCanvasSelection() 完成，与条目状态是否变化无关。 */}
          {canvasSelection && canSpecifySource ? (
            <CanvasSelectionBar
              busy={busy}
              hits={selectionHits}
              attestOffered={!currentItem || attestAffordance(currentItem).enabled}
              isSupplementGap={!!currentItem && isSupplementPending(currentItem)}
              onClear={clearCanvasSelection}
              onRegister={(elementRef) => {
                if (currentItem) {
                  void registerSource(currentItem.item_ref, elementRef);
                }
                clearCanvasSelection();
              }}
              selection={canvasSelection}
            />
          ) : null}
        </section>

        <section className="item-review-zone item-review-zone--evidence" aria-label="区4 当前条目证据链">
          <div className="intake-zone__header">
            <span>区4</span>
            <h3>详情 + 证据区</h3>
          </div>
          {currentItem ? (
            <div className="review-evidence-grid">
              <section className="review-evidence-card review-evidence-card--item">
                <div className="item-detail-card__title">
                  <strong>{currentItem.req_no}</strong>
                  <StatusPill tone={reviewDisplayMeta(currentItem.display_code).tone}>
                    {reviewDisplayMeta(currentItem.display_code).label}
                  </StatusPill>
                </div>
                {/* 走查反馈第⑥组：与条目形成页、需求管理页同一口径的两组分区。
                    来源要素清单归「登记信息」，故整块移到第二组下面。 */}
                <p className="item-detail-expression">{currentItem.expression}</p>
                <p className="item-detail-group__cap">条目内容</p>
                <dl>
                  <dt>类型</dt>
                  <dd>{requirementItemTypeText(currentItem.req_type)}</dd>
                  <dt>验证方式</dt>
                  <dd>{verificationMethodText(currentItem.verification_method) ?? '（未建议）'}</dd>
                  <dt>验收准则</dt>
                  <dd className={currentItem.verification_note ? undefined : 'item-attr-missing'}>
                    {currentItem.verification_note ?? '缺失：建议经"补充来源"闭合后回条目形成补写（仅警示）'}
                  </dd>
                  <dt>优先级</dt>
                  <dd className={currentItem.priority ? undefined : 'item-attr-missing'}>
                    {priorityText(currentItem.priority) ?? '未设定：确认前应人工补齐（仅警示）'}
                  </dd>
                </dl>
                <p className="item-detail-group__cap">登记信息</p>
                <dl>
                  <dt>版本</dt>
                  <dd>v{currentItem.version_no}</dd>
                  <dt>形成依据</dt>
                  <dd>{currentItem.formation_basis_ref ?? '人工形成'}</dd>
                  <dt>来源要素</dt>
                  <dd>{currentItem.source_element_refs.length} 个</dd>
                </dl>
                {/* 人工确认背书：与来源要素并列的独立证据类别，不混进上面的清单。
                    材料里没有对应位置，所以这里只写标记、理由与经手人，一个字的原文都不编。 */}
                {currentItem.source_attestation ? (
                  <div className="review-attest">
                    <p className="review-attest__hd">
                      <span className="review-attest__mark">人工确认</span>
                      材料里没有写到这条，由人确认它是真实需求
                    </p>
                    <p className="review-attest__reason">理由：{currentItem.source_attestation.reason}</p>
                    <p className="review-attest__meta">
                      {currentItem.source_attestation.operator_ref} 确认于
                      <RelativeTime className="az5-time" iso={currentItem.source_attestation.at} />
                    </p>
                    <p className="review-attest__note">
                      这条没有材料出处，但缺口已经闭合：再次诊断时 AI 会知道有过这次确认，
                      不会再因为「材料没写」判它需要补充来源。万一它仍提到表达与来源对不上，
                      那条会显示成提示、不用你处理；表达是否清楚、能不能验证这些照常评。
                    </p>
                  </div>
                ) : null}
                <div className="review-source-list">
                  {currentItem.source_element_refs.map((ref) => {
                    const source = sourceElementsById.get(ref);
                    if (!source) return null;
                    const meta = elementTypeMeta(source.element_type);
                    return (
                      <p key={ref}>
                        <span className={`element-type-chip element-type-chip--${meta.colorKey}`}>{meta.label}</span>
                        {source.content}
                      </p>
                    );
                  })}
                </div>
              </section>

              <section className="review-evidence-card">
                <strong>当前结论摘要</strong>
                {standing ? (
                  <dl>
                    <dt>结论</dt>
                    <dd>{verdictKindText(standing.verdict_kind)}（第 {standing.round_no} 轮 · {diagnosisModeText(standing.diagnosis_mode)} · {triggerText(standing.trigger)}）</dd>
                    <dt>总结</dt>
                    <dd>{standing.verdict_summary}</dd>
                    <dt>证据</dt>
                    <dd>{standing.findings.length} 条 · 详见区5 线程结论卡</dd>
                    <dt>模型推理结果记录</dt>
                    <dd>{standing.model_result_refs.join('、') || '—'}</dd>
                  </dl>
                ) : (
                  <p className="empty-state">{reviewItemStatusNote(currentItem)}</p>
                )}
                {currentItem.supplement_gaps_open.length ? (
                  <div className="review-source-list">
                    <strong>未闭合来源缺口</strong>
                    {currentItem.supplement_gaps_open.map((gap) => <p key={gap}>{gap}</p>)}
                  </div>
                ) : null}
              </section>

              {/* v2 质量诊断器（签名件，与详情卡共用同组件）：span 标注/6 维评分/EARS/对齐分 +
                  一键修复（采纳修订点，走既有 adjudicate 链）；诊断仍由区5 发起诊断驱动 */}
              <section className="review-evidence-card review-evidence-card--quality">
                <strong>质量诊断</strong>
                <RequirementQualityPanel
                  vm={quality ? buildQualityPanelVM(quality) : EMPTY_QUALITY_PANEL}
                  onAdoptPoint={adoptQualityPoint}
                />
              </section>

              <section className="review-evidence-card">
                <strong>裁决与修订留痕</strong>
                <div className="review-source-list">
                  <strong>修订记录</strong>
                  {/* C22：人工确认背书借修订记录表落库，但它一个字段都没改条目——把它列在「修订记录」
                      标题下与「背书不改任何字段」的红线相悖。区4 上方已有独立背书块承载同一事实，
                      这里过滤掉该行不丢信息。 */}
                  {(() => {
                    const records = currentItem.revision_records.filter(
                      (record) => !isSourceAttestation(record),
                    );
                    return records.length ? (
                      records.map((record) => (
                        <p key={record.record_ref}>
                          {revisionRecordText(record)}
                          {record.selected_point_refs.length ? `（点：${record.selected_point_refs.join('、')}）` : ''}
                        </p>
                      ))
                    ) : (
                      <p>暂无修订记录。</p>
                    );
                  })()}
                </div>
                {/* 采纳时用户改过稿的点：AI 原案与实际应用稿并排，两者都不丢 */}
                {editedPointTrail.length ? (
                  <div className="review-source-list">
                    <strong>你改过再采纳的建议</strong>
                    {editedPointTrail.map((row) => (
                      <p key={row.key}>
                        第{row.roundNo}轮 · {row.label}
                        <br />
                        AI 原案：{row.aiText}
                        <br />
                        你实际用的：{row.userText}
                      </p>
                    ))}
                  </div>
                ) : null}
                <div className="review-source-list">
                  <strong>你标为不是问题的</strong>
                  {currentItem.finding_vetoes?.length ? (
                    currentItem.finding_vetoes.map((veto) => (
                      <p key={veto.veto_ref}>
                        {veto.finding_summary}
                        {veto.reason ? `（理由：${veto.reason}）` : ''}
                        {veto.revoked ? '　—— 已恢复计入' : ''}
                      </p>
                    ))
                  ) : (
                    <p>还没有标记过。</p>
                  )}
                </div>
              </section>
            </div>
          ) : (
            <p className="empty-state">尚无待评审条目。</p>
          )}
        </section>
      </div>

      <section className="item-review-zone item-review-zone--operations rv5-rail" aria-label="区5 条目评审操作与确认">
        <div className="az5-top rv5-top">
          <div className="az5-row1">
            <span className="az5-zone">区5</span>
            <h4 className="az5-title">条目评审 · AI 协同</h4>
            <span className="az5-prog">已确认 {workspace.confirmed_count}/{workspace.total_count}</span>
          </div>
          <div className="rv5-strip" role="tablist" aria-label="会话线程切换">
            {strip.map((chip) => (
              <button
                className={chip.active ? 'rv5-chip rv5-chip--active' : 'rv5-chip'}
                key={chip.itemRef}
                onClick={() => switchThread(chip.itemRef)}
                role="tab"
                type="button"
              >
                {chip.spinning ? <span className="rv5-spin" aria-hidden="true" /> : null}
                {chip.reqNo}
                {chip.glyph ? <span className="rv5-chip__bd">{chip.glyph}</span> : null}
                {chip.done ? <span className="rv5-chip__bd rv5-chip__bd--ok">✓</span> : null}
              </button>
            ))}
            <span className="rv5-strip__sep" />
            <button
              className={feedMode ? 'rv5-chip rv5-chip--active' : 'rv5-chip'}
              onClick={() => setFeedMode(true)}
              role="tab"
              type="button"
            >
              动态流
            </button>
          </div>
          {currentItem && !feedMode ? (
            <div className="az5-target">
              <span className="az5-target__cap">当前条目</span>
              <span className="az5-target__body">{currentItem.req_no} {currentItem.expression}</span>
              <span className="az5-target__st">
                {reviewDisplayMeta(currentItem.display_code).label} · v{currentItem.version_no}
              </span>
            </div>
          ) : null}
          {/* 状态条就地升格，而不是在它上面再加一条横幅：说明句只有一句，两处都显示就是
              同一句话连说两遍。升格后标签也换成「人工确认」——此刻这句话讲的不是结论。 */}
          <div
            className={attestedBanner ? 'rv5-gate rv5-gate--attested' : 'rv5-gate'}
            role={attestedBanner ? 'status' : undefined}
          >
            <span className={attestedBanner ? 'rv5-gate__mark' : 'rv5-gate__cap'}>
              {attestedBanner ? '人工确认' : '当前结论'}
            </span>
            <span>{gateText}</span>
            {nextAwaiting && !feedMode ? (
              <button className="rv5-gate__next" onClick={() => switchThread(nextAwaiting)} type="button">
                下一待裁决 →
              </button>
            ) : null}
          </div>
        </div>

        {/* 控件常驻挂载（动态流开启时以 CSS 隐藏，保住草稿/在途、不中断在途发送）；线程与输入区归控件。 */}
        <div className={feedMode ? 'rv5-widget-host rv5-widget-host--hidden' : 'rv5-widget-host'}>
          {currentItem ? (
            <ReviewHostContext.Provider value={hostApi}>
              <ChatWidget adapter={adapter} />
            </ReviewHostContext.Provider>
          ) : (
            <p className="az5-hint">尚无待评审条目。</p>
          )}
        </div>

        {feedMode ? (
          <div className="az5-thread rv5-thread rv5-feed">
            <p className="az5-hint">全局动态 · 只读；点击任意行进入对应线程。</p>
            {feed.map((line) => (
              <button className="rv5-feedline" key={line.key} onClick={() => switchThread(line.itemRef)} type="button">
                <span className="rv5-feedline__it">{line.reqNo}</span>
                <span>{line.text}</span>
                <RelativeTime className="az5-time" iso={line.at} />
              </button>
            ))}
            {!feed.length ? <p className="az5-hint">暂无事件。</p> : null}
          </div>
        ) : null}

        {reasonModal ? (
          <Modal
            cancelText="取消"
            confirmLoading={reasonBusy}
            okButtonProps={{
              danger: reasonModal.danger,
              disabled: !!reasonModal.requireReason && !reasonText.trim(),
            }}
            okText={reasonModal.confirmText ?? '提交'}
            onCancel={() => { if (!reasonBusy) setReasonModal(null); }}
            onOk={handleReasonSubmit}
            open
            title={reasonModal.title}
          >
            <p className="rv5-reason__label">{reasonModal.label}</p>
            <Input.TextArea
              autoFocus
              onChange={(event) => setReasonText(event.target.value)}
              placeholder={reasonModal.placeholder}
              rows={3}
              value={reasonText}
            />
          </Modal>
        ) : null}
      </section>
    </div>
  );
}

// 迁移核销（04 篇 §3.1 删除清单）：区5 内联线程/输入区实现（rv5 线程消息渲染、结论卡/回执/草案/
// 补充来源出口、预填药丸与输入框、页面手写链路回执条 AiTraceRail）已删除，改由 <ChatWidget/> ＋
// 页面侧逃生舱组件（ReviewVerdictCard/ReviewReceiptCard/ReviewDraftCard/ReviewSupplementExitCard）承载；
// spike chat-widget/project-thread.ts 收编为 view-models/item-review-thread.ts 页面侧投影映射器。
