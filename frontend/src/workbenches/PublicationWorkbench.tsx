import {
  Alert,
  Button,
  Collapse,
  Empty,
  Input,
  Modal,
  Space,
  Splitter,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { WorkbenchFrame } from './WorkbenchFrame';
import { TemplatePreviewDrawer } from './TemplatePreviewDrawer';
import { DocxPreviewModal } from './DocxPreviewModal';
import { PublicationIndexView } from './PublicationIndexView';
import { publicationApi } from '../api/publication';
import { templatesApi } from '../api/templates';
import type { TemplateRegistryRead } from '../api/templates';
import {
  buildTemplateChoices,
  buildTemplateRows,
  type TemplateChoiceVM,
  type TemplateRowVM,
} from '../view-models/templates';
import type {
  DocIndexEntryRead,
  MarkdownPatchRead,
  PublicationWorkspaceRead,
  TemplateSectionRead,
} from '../api/publication';
import type { ProjectRead } from '../api/projects';
import { MarkdownPreview } from '../ui/MarkdownPreview';
import { CodeMirrorEditor } from '../ui/CodeMirrorEditor';
import type { CodeMirrorEditorHandle } from '../ui/CodeMirrorEditor';
import {
  buildDraftMissingList,
  buildEditImpactGroups,
  buildFooterSummary,
  buildIndexHeader,
  buildMarkdownState,
  buildOutlineTree,
  documentStatusMeta,
  exportStatusMeta,
  nextCoverageAnnouncement,
  type MarkdownDiffVM,
  type PublicationViewMode,
} from '../view-models/publication';
import type { BadgeTone } from '../view-models/common';
import '../styles-pub-rail.css';
import { formatAbsoluteTime } from '../view-models/time';
import type { SettingsDomainKey } from '../view-models/settings';
import {
  createWorkbenchHandoff,
  type WorkbenchHandoff,
} from '../view-models/workbench-handoff';

const { Text, Paragraph } = Typography;

const TONE_COLOR: Record<BadgeTone, string> = {
  success: 'green',
  processing: 'blue',
  warning: 'orange',
  danger: 'red',
  neutral: 'default',
} as Record<BadgeTone, string>;

/** 竖向进度条的节点形态（原型 v1 定稿的五态）。 */
export type PublishStepNode = 'done' | 'active' | 'busy' | 'failed' | 'pending';
/** 节点下方轨道段：绿实线=已完成段；蓝→灰渐变=当前推进段；灰=未达段。 */
export type PublishStepLine = 'done' | 'flow' | 'plain';
/** 内容卡的强调形态：仅当前步用浅蓝渐变底，其余降为素卡。 */
export type PublishStepCard = 'active' | 'failed' | 'dim' | 'plain';

export interface PublishStepState {
  node: PublishStepNode;
  line: PublishStepLine;
  card: PublishStepCard;
}

/**
 * 右栏三阶段的进度条形态推导（定稿 → 候选 docx → 发布基线）。
 *
 * 纯函数，只把已有的业务状态翻译成视觉形态，不参与任何状态迁移判定——阶段徽标、按钮可用性、
 * 轮询仍由既有逻辑各自决定。放在本文件而非 view-models/publication.ts，是因为本卡把
 * view-models/ 列为禁区，且既有的 finalizeBadge/exportBadge/baselineBadge 同样在组件内就地推导，
 * 此处与之同址一致（2026-07-20 用户确认的架构取舍，见任务卡「方案确认」）。
 */
export function derivePublishSteps(input: {
  /** Markdown 中间稿是否已定稿；无中间稿时传 false。 */
  finalized: boolean;
  /** 是否有在途的 docx 转换任务。 */
  converting: boolean;
  /** 是否有可操作的候选件（转换成功待检查 / 人工降级登记）。 */
  hasActionableCandidate: boolean;
  /** 是否有失败记录（转换失败 / 检查不通过）。 */
  hasFailure: boolean;
  /**
   * 本轮是否已形成发布基线，取文档状态 `baseline_published`（只读复核态）。
   *
   * 刻意不取「基线记录是否存在」：v0.1 单文档单基线，上一轮的基线记录在开启新一轮后依然留存，
   * 用它判完成会让刚定稿的新一轮凭空显示第 2、3 步已完成。文档状态则随
   * `BASELINE_PUBLISHED --REOPEN_INDEX--> INDEX_READY` 迁移解除，正是「本轮」的口径。
   */
  published: boolean;
}): [PublishStepState, PublishStepState, PublishStepState] {
  const { finalized, converting, hasActionableCandidate, hasFailure, published } = input;

  // 第 1 步：定稿。中间稿被索引调整或条目修订回流置为 superseded/awaiting_item_revision 时
  // finalized 转假，当前步自动退回第 1 步——这正是用户此刻要处理的事。
  const step1Node: PublishStepNode = finalized ? 'done' : 'active';

  // 第 2 步：候选 docx。优先级＝已成基线 > 转换中 > 有候选件待处理 > 有失败记录 > 未达。
  // 失败记录排在候选件之后：两者并存时（失败后重试成功）用户该做的是检查新候选件，不是看旧失败。
  const step2Node: PublishStepNode = !finalized
    ? 'pending'
    : published
      ? 'done'
      : converting
        ? 'busy'
        : hasActionableCandidate
          ? 'active'
          : hasFailure
            ? 'failed'
            : 'pending';

  // 第 3 步：发布基线，只有完成与未达两态——「确认发布基线」按钮长在第 2 步卡内，
  // 第 3 步没有自己的待办动作，故不设「当前」态（与原型六张状态图一致）。
  const step3Node: PublishStepNode = published ? 'done' : 'pending';

  const lineOf = (node: PublishStepNode): PublishStepLine =>
    node === 'done' ? 'done' : node === 'active' || node === 'busy' ? 'flow' : 'plain';

  // 卡片强调：当前步与转换中步高亮，失败步红底；已完成与未达步降为素卡。
  // 例外＝已完成的第 3 步，它承载基线详情（基线号/索引版本/模板/确认人/追溯入口），
  // 降为素卡会让这些内容读起来像已作废，故保持普通白卡。
  const cardOf = (node: PublishStepNode): PublishStepCard =>
    node === 'active' || node === 'busy' ? 'active' : node === 'failed' ? 'failed' : 'dim';

  return [
    { node: step1Node, line: lineOf(step1Node), card: cardOf(step1Node) },
    { node: step2Node, line: lineOf(step2Node), card: cardOf(step2Node) },
    { node: step3Node, line: 'plain', card: step3Node === 'done' ? 'plain' : 'dim' },
  ];
}

/** 阶段徽标在进度条内改用轻量胶囊呈现（与 antd Tag 同一套语气分档，只换外观）。 */
const CHIP_CLASS: Record<BadgeTone, string> = {
  success: 'pub-vstep__chip--success',
  processing: 'pub-vstep__chip--processing',
  warning: 'pub-vstep__chip--warning',
  danger: 'pub-vstep__chip--danger',
  neutral: 'pub-vstep__chip--neutral',
} as Record<BadgeTone, string>;

function StepChip(props: { tone: BadgeTone; text: string }) {
  return <span className={`pub-vstep__chip ${CHIP_CLASS[props.tone]}`}>{props.text}</span>;
}

/** 节点列：圆节点＋下延轨道段；末步不画轨道。 */
function StepRail(props: { index: number; state: PublishStepState; last?: boolean }) {
  const { index, state, last } = props;
  const label = state.node === 'done' ? '✓' : state.node === 'failed' ? '✕' : String(index);
  return (
    <div className="pub-vstep__rail">
      <div
        className={`pub-vstep__node pub-vstep__node--${state.node}`}
        data-testid={`pub-step-node-${index}`}
        data-state={state.node}
      >
        {state.node === 'busy' ? <span className="pub-vstep__spin" aria-hidden /> : null}
        {label}
      </div>
      {last ? null : <div className={`pub-vstep__line pub-vstep__line--${state.line}`} />}
    </div>
  );
}

interface PublicationWorkbenchProps {
  selectedProject: ProjectRead | null;
  operatorRef: string;
  /** 空态引导跳设置对应域（定制/登记已迁入设置 › 文档模板）。 */
  onOpenSettingsDomain: (domain: SettingsDomainKey) => void;
  workbenchHandoff?: WorkbenchHandoff | null;
  onWorkbenchHandoff?: (handoff: WorkbenchHandoff) => void;
  onConsumeWorkbenchHandoff?: (token: number) => void;
}

function newKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

/** 资产类型 → 模板目标槽位（同类型唯一槽位自动映射）。 */
function targetSection(
  sections: TemplateSectionRead[],
  assetType: 'requirement_item' | 'material' | 'chart',
  reqType?: string,
): TemplateSectionRead | undefined {
  if (assetType === 'requirement_item') {
    return sections.find((s) => s.content_types.includes(`requirement_item:${reqType}`));
  }
  if (assetType === 'chart') {
    return sections.find((s) => s.content_types.includes('chart'));
  }
  return (
    sections.find((s) => s.key === 'appendix.materials' && s.content_types.includes('material')) ??
    sections.find((s) => s.content_types.includes('material'))
  );
}

function resolveHandoffEntries(
  workspace: PublicationWorkspaceRead,
  handoff: WorkbenchHandoff,
): DocIndexEntryRead[] {
  const resolved: DocIndexEntryRead[] = [];
  for (const asset of [handoff.anchor, ...handoff.relatedAssets]) {
    if (asset.entityType === 'chart') {
      const candidate = workspace.candidates.charts.find((row) => row.chart_ref === asset.ref);
      const section = candidate ? targetSection(workspace.template.sections, 'chart') : undefined;
      if (candidate && section) {
        resolved.push({ section_key: section.key, asset_type: 'chart', asset_ref: candidate.chart_ref });
      }
    }
    if (asset.entityType === 'requirement_item') {
      const candidate = workspace.candidates.items.find((row) => row.item_ref === asset.ref);
      const section = candidate
        ? targetSection(workspace.template.sections, 'requirement_item', candidate.req_type)
        : undefined;
      if (candidate && section) {
        resolved.push({ section_key: section.key, asset_type: 'requirement_item', asset_ref: candidate.item_ref });
      }
    }
  }
  return resolved;
}

function mergeHandoffEntries(
  current: DocIndexEntryRead[],
  resolved: DocIndexEntryRead[],
): DocIndexEntryRead[] {
  const existing = new Set(current.map((entry) => entry.asset_ref).filter(Boolean));
  const next = [...current];
  for (const entry of resolved) {
    if (!entry.asset_ref || existing.has(entry.asset_ref)) continue;
    const order = next.filter((row) => row.section_key === entry.section_key).length;
    next.push({ ...entry, order_no: order });
    existing.add(entry.asset_ref);
  }
  return next;
}

export function PublicationWorkbench({
  selectedProject,
  operatorRef,
  onOpenSettingsDomain,
  workbenchHandoff,
  onWorkbenchHandoff,
  onConsumeWorkbenchHandoff,
}: PublicationWorkbenchProps) {
  const projectId = selectedProject?.id;
  const [workspace, setWorkspace] = useState<PublicationWorkspaceRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<PublicationViewMode>('main');
  const [draftEntries, setDraftEntries] = useState<DocIndexEntryRead[]>([]);
  const [coverageScope, setCoverageScope] = useState('');
  const [markdownText, setMarkdownText] = useState('');
  // diff 基线（生成稿）：载入/上次保存时的服务端内容快照；编辑器实时 diff 相对它（详设 §7.6，
  // 口径「距上次保存」——保存后 refresh 会把它重置为新内容，diff 归空）。
  const [baselineText, setBaselineText] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingReflow, setPendingReflow] = useState<MarkdownPatchRead[] | null>(null);
  const [checkTarget, setCheckTarget] = useState<string | null>(null);
  const [checkNote, setCheckNote] = useState('');
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const [fallbackReason, setFallbackReason] = useState('');
  const [templateRows, setTemplateRows] = useState<TemplateRegistryRead[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>();
  const [templatePreviewRef, setTemplatePreviewRef] = useState<string | null>(null);
  const [docxPreview, setDocxPreview] = useState<{ ref: string; title: string } | null>(null);
  const handoffConsumedRef = useRef<number | null>(null);
  // 初始化请求可能晚于交接 effect 返回；保留本次入口意图，避免刷新把索引编排页复位为主工作台。
  const incomingHandoffRef = useRef<WorkbenchHandoff | null>(workbenchHandoff ?? null);
  if (workbenchHandoff) incomingHandoffRef.current = workbenchHandoff;
  // 全覆盖成功提示基线（missingCount 上次读数）；初值 null=尚无基线，首帧只记录不提示。
  // 声明置于 refresh 之前，使服务端来源的草稿写入能在同批次将其重置（C1）。
  const missingCountRef = useRef<number | null>(null);

  const refresh = useCallback(async (switchView = false, templateRef?: string) => {
    if (!projectId) return null;
    try {
      const ws = await publicationApi.getWorkspace(projectId, templateRef);
      setWorkspace(ws);
      setLoadError(null);
      setCoverageScope(ws.document?.coverage_scope ?? '');
      setSelectedTemplateId((current) => templateRef ?? current ?? ws.template.template_ref);
      if (ws.markdown) {
        setMarkdownText(ws.markdown.content);
        setBaselineText(ws.markdown.content);
      }
      if (switchView) {
        // 发布主工作台恒为发布入口默认落点：有文档一律落主工作台，索引未形成时
        // 由主工作台内的分级引导空态承接（去索引编排/模板选择）；完全无文档才先选模板。
        const incoming = incomingHandoffRef.current;
        const shouldCompose =
          incoming?.intent === 'compose_document_from_assets' &&
          incoming.targetWorkbench === 'release' &&
          incoming.projectId === projectId;
        setDraftEntries(
          shouldCompose ? mergeHandoffEntries(ws.index_entries, resolveHandoffEntries(ws, incoming)) : ws.index_entries,
        );
        // 服务端来源的草稿写入不冒充用户动作：重置提示基线，避免刷新覆盖未保存编排后误报「全覆盖」。
        missingCountRef.current = null;
        setViewMode(shouldCompose ? 'index' : ws.document ? 'main' : 'template');
      } else {
        setDraftEntries(ws.index_entries);
        missingCountRef.current = null;
      }
      return ws;
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
      return null;
    }
  }, [projectId]);

  useEffect(() => {
    void refresh(true);
  }, [refresh]);

  // 转换态自动完成检测：只要本轮存在 converting 导出就轮询工作区，直到其落终态（成功/失败）。
  // 事实源=导出行状态（后端 A3 超时对账兜底保证终态必达）；attempts 上限防御性收尾避免无限轮询。
  // 口径必须与右栏进度条的 converting 一致（同一「本轮」判据，见下方 roundExports）：只认当前未
  // 作废中间稿上的在途转换。否则跨轮在途转换（在已作废旧稿上）会驱动轮询、两分钟后弹出转换超时
  // 警告，而进度条上没有任何对应指示——两处取数分叉（C5）。
  const pollDraft = workspace?.markdown;
  const pollDraftRef =
    pollDraft && pollDraft.status !== 'superseded' && pollDraft.status !== 'awaiting_item_revision'
      ? pollDraft.draft_ref
      : null;
  const hasConverting =
    pollDraftRef != null &&
    (workspace?.exports.some((e) => e.status === 'converting' && e.draft_ref === pollDraftRef) ?? false);
  useEffect(() => {
    if (!hasConverting) return;
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (attempts > 40) {
        clearInterval(timer);
        void message.warning('候选 docx 转换较慢或可能失败，请稍后刷新或重试');
        return;
      }
      void refresh();
    }, 3000);
    return () => clearInterval(timer);
  }, [hasConverting, refresh]);

  const refreshTemplates = useCallback(() => {
    templatesApi.list().then(setTemplateRows).catch(() => setTemplateRows([]));
  }, []);

  useEffect(() => {
    refreshTemplates();
  }, [refreshTemplates]);

  const templateChoices = useMemo(() => buildTemplateChoices(templateRows), [templateRows]);
  const currentChoice = useMemo(
    () => templateChoices.find((c) => c.templateId === selectedTemplateId),
    [templateChoices, selectedTemplateId],
  );

  const previewSelectedTemplate = useCallback(() => {
    if (currentChoice) setTemplatePreviewRef(currentChoice.registryRef);
  }, [currentChoice]);

  const chooseTemplate = useCallback((templateId: string) => {
    setSelectedTemplateId(templateId);
    // 选用模板 → 按该模板槽位评估当前编排，进入索引编排（保存时正式生效并冻结注册行）
    void refresh(false, templateId);
    setViewMode('index');
  }, [refresh]);

  // 章节撰稿（AEP-098）：保存后刷新工作区（撰稿/覆盖状态/缺失清单同步）
  const saveManuscript = useCallback(async (sectionKey: string, content: string) => {
    if (!projectId) return;
    try {
      await publicationApi.saveManuscript(projectId, {
        project_ref: projectId,
        template_ref: selectedTemplateId ?? null,
        section_key: sectionKey,
        content,
        operator_ref: operatorRef,
      });
      await refresh(false, selectedTemplateId);
      void message.success(content.trim() ? '撰稿已保存（重新生成后进入下一稿）' : '已恢复模板默认文本');
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
      throw error;
    }
  }, [projectId, selectedTemplateId, operatorRef, refresh]);

  // AEP-110：为 authored_text 章节 AI 起草初稿，写入撰稿并回填草稿供人工完善（非最终稿）。
  const draftManuscript = useCallback(async (sectionKey: string): Promise<string> => {
    if (!projectId) return '';
    const read = await publicationApi.draftManuscript(projectId, sectionKey, {
      project_ref: projectId,
      template_ref: selectedTemplateId ?? null,
      operator_ref: operatorRef,
    });
    await refresh(false, selectedTemplateId);
    return read.content;
  }, [projectId, selectedTemplateId, operatorRef, refresh]);

  // 候选预览（AEP-099）：只读，最终渲染
  const loadCandidatePreview = useCallback(
    (kind: 'requirement_item' | 'chart' | 'material', ref: string) => {
      if (!projectId) return Promise.reject(new Error('未选择项目'));
      return publicationApi.candidatePreview(projectId, kind, ref);
    },
    [projectId],
  );

  const footer = useMemo(
    () => (workspace ? buildFooterSummary(workspace, draftEntries) : null),
    [workspace, draftEntries],
  );
  const mdState = useMemo(() => buildMarkdownState(workspace?.markdown), [workspace]);
  const selectedRefs = useMemo(() => new Set(draftEntries.map((e) => e.asset_ref ?? '')), [draftEntries]);
  // 缺失清单读侧：草稿派生（勾选即闭合，无需保存往返），保存后草稿等于服务端条目即回落其口径（issue #14）
  const missingList = useMemo(
    () => (workspace ? buildDraftMissingList(workspace, draftEntries) : []),
    [workspace, draftEntries],
  );

  // 全部必填槽位闭合的成功提示：只在「有缺失 → 无缺失」跃迁且可进入 Markdown 时弹一次。
  // missingCountRef 初值 null（尚无基线）——首帧（含 StrictMode 双调用、初始即全覆盖、服务端刷新落 0）
  // 只记录不提示；模板校验失败时 canEnterMarkdown=false，跃迁到 0 亦不提示（P1）。
  useEffect(() => {
    if (viewMode !== 'index' || !footer) {
      missingCountRef.current = null;
      return;
    }
    const { announce, nextBaseline } = nextCoverageAnnouncement(
      missingCountRef.current,
      footer.missingCount,
      footer.canEnterMarkdown,
    );
    missingCountRef.current = nextBaseline;
    if (announce) {
      void message.success('全部必填槽位已覆盖，可进入 Markdown 生成');
    }
  }, [footer, viewMode]);

  // 图表工作台交接：进入索引编排后预选图表及其来源条目，不自动保存索引。
  useEffect(() => {
    if (
      !workbenchHandoff ||
      workbenchHandoff.intent !== 'compose_document_from_assets' ||
      workbenchHandoff.targetWorkbench !== 'release' ||
      workbenchHandoff.projectId !== projectId ||
      !workspace ||
      handoffConsumedRef.current === workbenchHandoff.token
    ) {
      return;
    }
    if (viewMode === 'template') return;
    if (viewMode === 'main') {
      setViewMode('index');
    }

    const resolved = resolveHandoffEntries(workspace, workbenchHandoff);
    setDraftEntries((current) => mergeHandoffEntries(current, resolved));
    handoffConsumedRef.current = workbenchHandoff.token;
    onConsumeWorkbenchHandoff?.(workbenchHandoff.token);
    if (resolved.length > 0) {
      void message.success(`已带入 ${resolved.length} 项发布候选，请复核章节后保存索引`);
    } else {
      void message.warning('交接资产当前不在发布候选池，请检查条目或图表状态');
    }
  }, [onConsumeWorkbenchHandoff, projectId, viewMode, workbenchHandoff, workspace]);

  if (!selectedProject) {
    return (
      <WorkbenchFrame title="发布管理工作台">
        <Empty description="请先在顶部选择项目" />
      </WorkbenchFrame>
    );
  }

  if (loadError) {
    return (
      <WorkbenchFrame title="发布管理工作台">
        <Alert type="error" showIcon title="发布工作台加载失败" description={loadError} />
      </WorkbenchFrame>
    );
  }

  if (!workspace) {
    return (
      <WorkbenchFrame title="发布管理工作台">
        <Empty description="正在加载发布工作台…" />
      </WorkbenchFrame>
    );
  }

  const docMeta = documentStatusMeta(workspace.document?.status);

  const toggleAsset = (
    assetType: 'requirement_item' | 'material' | 'chart',
    ref: string,
    reqType?: string,
    sectionKey?: string,
  ) => {
    if (selectedRefs.has(ref)) {
      setDraftEntries((current) => current.filter((e) => e.asset_ref !== ref));
      return;
    }
    // 指定槽位（「+添加到此槽位」）须兼容该资产类型，否则回落自动映射
    const wanted = assetType === 'requirement_item' ? `requirement_item:${reqType}` : assetType;
    const explicit = sectionKey
      ? workspace.template.sections.find(
          (s) => s.key === sectionKey && s.content_types.includes(wanted),
        )
      : undefined;
    const section = explicit ?? targetSection(workspace.template.sections, assetType, reqType);
    if (!section) {
      void message.warning('当前模板没有可承接该资产类型的章节槽位');
      return;
    }
    setDraftEntries((current) => [
      ...current,
      {
        section_key: section.key,
        asset_type: assetType,
        asset_ref: ref,
        order_no: current.filter((e) => e.section_key === section.key).length,
      },
    ]);
  };

  const moveEntry = (sectionKey: string, ref: string, delta: number) => {
    setDraftEntries((current) => {
      const inSection = current
        .filter((e) => e.section_key === sectionKey)
        .sort((a, b) => (a.order_no ?? 0) - (b.order_no ?? 0));
      const idx = inSection.findIndex((e) => e.asset_ref === ref);
      const swap = idx + delta;
      if (idx < 0 || swap < 0 || swap >= inSection.length) return current;
      [inSection[idx], inSection[swap]] = [inSection[swap], inSection[idx]];
      const reordered = inSection.map((e, i) => ({ ...e, order_no: i }));
      return [...current.filter((e) => e.section_key !== sectionKey), ...reordered];
    });
  };

  const removeEntry = (ref: string) => {
    setDraftEntries((current) => current.filter((e) => e.asset_ref !== ref));
  };

  const changeSlot = (ref: string, sectionKey: string) => {
    setDraftEntries((current) => {
      const entry = current.find((e) => e.asset_ref === ref);
      if (!entry || entry.section_key === sectionKey) return current;
      const rest = current.filter((e) => e.asset_ref !== ref);
      return [
        ...rest,
        {
          ...entry,
          section_key: sectionKey,
          order_no: rest.filter((e) => e.section_key === sectionKey).length,
        },
      ];
    });
  };

  const reorderTo = (sectionKey: string, ref: string, targetIndex: number) => {
    setDraftEntries((current) => {
      const inSection = current
        .filter((e) => e.section_key === sectionKey)
        .sort((a, b) => (a.order_no ?? 0) - (b.order_no ?? 0));
      const idx = inSection.findIndex((e) => e.asset_ref === ref);
      if (idx < 0) return current;
      const [moved] = inSection.splice(idx, 1);
      inSection.splice(Math.max(0, Math.min(targetIndex, inSection.length)), 0, moved);
      const reordered = inSection.map((e, i) => ({ ...e, order_no: i }));
      return [...current.filter((e) => e.section_key !== sectionKey), ...reordered];
    });
  };

  const clearEntries = () => setDraftEntries([]);

  const cancelDraft = () => {
    setDraftEntries(workspace.index_entries);
    setCoverageScope(workspace.document?.coverage_scope ?? '');
    const status = workspace.document?.status;
    if (status && status !== 'index_draft' && status !== 'index_blocked' && status !== 'index_ready') {
      setViewMode('main');
    }
  };

  const saveIndex = async (silent = false) => {
    setBusy(true);
    try {
      const result = await publicationApi.saveIndex(projectId!, {
        project_ref: projectId!,
        template_ref: selectedTemplateId ?? null,
        coverage_scope: coverageScope || null,
        entries: draftEntries,
        operator_ref: operatorRef,
        idempotency_key: newKey('idx'),
      });
      if (result.status === 'index_ready') {
        if (!silent) void message.success('文档内容索引已形成，可进入 Markdown 生成');
      } else {
        void message.warning(result.blocked_reason ?? '索引受阻');
      }
      await refresh();
      return result;
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const saveAndEnterMarkdown = async () => {
    const result = await saveIndex(true);
    if (result?.status === 'index_ready') {
      await generateMarkdown();
    }
  };

  const generateMarkdown = async () => {
    setBusy(true);
    try {
      await publicationApi.generateMarkdown(projectId!, {
        project_ref: projectId!,
        operator_ref: operatorRef,
        idempotency_key: newKey('gen'),
      });
      await refresh();
      setViewMode('main');
      void message.success('Markdown 中间稿已生成');
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const submitEdit = async () => {
    if (!workspace.markdown) return;
    setBusy(true);
    try {
      const result = await publicationApi.recordEdit(projectId!, {
        project_ref: projectId!,
        draft_ref: workspace.markdown.draft_ref,
        content: markdownText,
        operator_ref: operatorRef,
      });
      await refresh();
      if (result.block_reasons.length > 0) {
        void message.warning(`编辑已记录，存在不可定稿项 ${result.block_reasons.length} 项`);
      } else if (result.pending_item_refs.length > 0) {
        void message.info(`编辑已记录：触及确认态条目 ${result.pending_item_refs.length} 条，定稿时需确认修订清单`);
      } else {
        void message.success('编辑已记录：纯文档表达微调');
      }
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const finalize = async (confirmReflow: boolean) => {
    if (!workspace.markdown) return;
    setBusy(true);
    try {
      const result = await publicationApi.finalizeMarkdown(projectId!, {
        project_ref: projectId!,
        draft_ref: workspace.markdown.draft_ref,
        confirm_reflow: confirmReflow,
        operator_ref: operatorRef,
        idempotency_key: newKey('fin'),
      });
      if (result.status === 'pending_item_confirmation') {
        setPendingReflow(result.pending_items);
      } else if (result.status === 'item_revision_reflowed') {
        setPendingReflow(null);
        void message.warning('确认态条目修订已回流为新的待确认条目；当前稿等待修订收束，需重新生成');
      } else if (result.status === 'finalized') {
        setPendingReflow(null);
        void message.success('Markdown 已定稿，可导出候选 docx');
      } else {
        void message.error(result.block_reasons.join('；') || '定稿被阻断');
      }
      await refresh();
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const reopenIndex = async () => {
    setBusy(true);
    try {
      await publicationApi.reopenIndex(projectId!, operatorRef);
      await refresh();
      setViewMode('index');
      void message.info('已返回索引编排页；原 Markdown 稿标记需重新生成');
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const startExport = async () => {
    if (!workspace.markdown) return;
    setBusy(true);
    try {
      const result = await publicationApi.startExport(
        projectId!, workspace.markdown.draft_ref, operatorRef, newKey('exp'),
      );
      if (result.status === 'submitted') {
        void message.success('候选 docx 导出已受理');
      } else {
        void message.warning(result.next_action ?? '导出被拒绝');
      }
      await refresh();
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const submitCheck = async (passed: boolean) => {
    if (!checkTarget) return;
    setBusy(true);
    try {
      await publicationApi.reportCheck(projectId!, checkTarget, passed, checkNote, operatorRef);
      setCheckTarget(null);
      setCheckNote('');
      await refresh();
      void message.success(passed ? '检查结论已记录：通过' : '检查结论已记录：不通过（可重试导出/回 P02/回 P01）');
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const confirmBaseline = async (exportRef: string) => {
    Modal.confirm({
      title: '确认发布基线',
      content: '确认后将冻结本次交付快照（索引版本、Markdown 定稿版本、模板与候选件引用）。导出成功不等于发布，此操作代表您确认该候选件可交付。',
      okText: '确认发布基线',
      cancelText: '取消',
      onOk: async () => {
        const result = await publicationApi.confirmBaseline(
          projectId!, exportRef, '', operatorRef, newKey('bl'),
        );
        if (result.status === 'confirmed') {
          void message.success('发布基线已形成，可只读复核与下载');
        } else {
          void message.warning(result.next_action ?? '基线确认被拒绝');
        }
        await refresh();
      },
    });
  };

  const registerFallback = async () => {
    if (!workspace.markdown) return;
    setBusy(true);
    try {
      await publicationApi.registerManualFallback(
        projectId!, workspace.markdown.draft_ref, fallbackReason, operatorRef, newKey('mf'),
      );
      setFallbackOpen(false);
      setFallbackReason('');
      await refresh();
      void message.warning('人工降级导出件已登记（明确标记，不算系统转换成功）');
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const headerExtra = (
    <Space>
      <Tag color={TONE_COLOR[docMeta.tone]}>{workspace.document?.title ?? '需求规格说明'} · {docMeta.label}</Tag>
      {viewMode === 'main' ? (
        <Button onClick={() => setViewMode('index')}>调整索引编排</Button>
      ) : viewMode === 'index' && workspace.document && workspace.document.status !== 'index_blocked' ? (
        <Button onClick={() => setViewMode('main')}>进入发布主工作台</Button>
      ) : null}
    </Space>
  );

  const viewPublishedTrace = (documentRef: string, title: string) => {
    if (!projectId || !onWorkbenchHandoff) return;
    onWorkbenchHandoff(createWorkbenchHandoff({
      projectId,
      targetWorkbench: 'traceability',
      intent: 'inspect_document_trace',
      anchor: { entityType: 'document', ref: documentRef, title },
      relatedAssets: [],
    }));
  };

  return (
    <WorkbenchFrame title="发布管理工作台" extra={headerExtra}>
      {workspace.template.error ? (
        <Alert
          type="error"
          showIcon
          title="模板文件不可用"
          description={`${workspace.template.error}。请更换模板或修复模板文件（模板修复不在文档编排流程内）。`}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      {workspace.next_action ? (
        <Alert type="info" showIcon title={workspace.next_action} style={{ marginBottom: 12 }} />
      ) : null}

      {viewMode === 'template' ? (
        <TemplateSelectionView
          choices={templateChoices}
          versionRows={buildTemplateRows(templateRows)}
          selectedTemplateId={selectedTemplateId}
          busy={busy}
          operatorRef={operatorRef}
          onPreview={setTemplatePreviewRef}
          onChoose={chooseTemplate}
          onRegistryChanged={refreshTemplates}
          onOpenSettingsDomain={onOpenSettingsDomain}
        />
      ) : viewMode === 'index' ? (
        <PublicationIndexView
          workspace={workspace}
          draftEntries={draftEntries}
          currentTemplateText={currentChoice ? `${currentChoice.name}（${currentChoice.templateId} ${currentChoice.versionText}）` : selectedTemplateId ?? '—'}
          missingList={missingList}
          footer={footer}
          coverageScope={coverageScope}
          busy={busy}
          canCancel
          onCoverageScopeChange={setCoverageScope}
          onTemplatePreview={previewSelectedTemplate}
          onChangeTemplate={() => setViewMode('template')}
          onToggleAsset={toggleAsset}
          onMove={moveEntry}
          onRemove={removeEntry}
          onChangeSlot={changeSlot}
          onReorderTo={reorderTo}
          onClear={clearEntries}
          onSave={() => void saveIndex()}
          onSaveAndEnterMarkdown={() => void saveAndEnterMarkdown()}
          onCancel={cancelDraft}
          onSaveManuscript={saveManuscript}
          onDraftManuscript={draftManuscript}
          onLoadPreview={loadCandidatePreview}
        />
      ) : (
        <MainPublicationView
          workspace={workspace}
          mdState={mdState}
          markdownText={markdownText}
          baselineText={baselineText}
          busy={busy}
          onMarkdownChange={setMarkdownText}
          onSubmitEdit={submitEdit}
          onRegenerate={generateMarkdown}
          onFinalize={() => finalize(false)}
          onReopenIndex={reopenIndex}
          onGoIndex={() => setViewMode('index')}
          onGoTemplate={() => setViewMode('template')}
          onRefresh={() => void refresh()}
          onStartExport={startExport}
          onOpenCheck={(ref) => setCheckTarget(ref)}
          onConfirmBaseline={confirmBaseline}
          onOpenFallback={() => setFallbackOpen(true)}
          exportFileUrl={(ref) => publicationApi.exportFileUrl(projectId!, ref)}
          onPreviewDocx={(ref, title) => setDocxPreview({ ref, title })}
          onViewTrace={viewPublishedTrace}
        />
      )}

      <TemplatePreviewDrawer registryRef={templatePreviewRef} onClose={() => setTemplatePreviewRef(null)} />

      <DocxPreviewModal
        open={docxPreview !== null}
        title={docxPreview?.title ?? '候选 docx 预览'}
        projectId={projectId}
        exportRef={docxPreview?.ref ?? null}
        onClose={() => setDocxPreview(null)}
      />

      <Modal
        title="待修订确认态条目清单"
        open={pendingReflow !== null}
        okText="确认修订清单并回流"
        cancelText="退回窗口继续编辑"
        onOk={() => finalize(true)}
        onCancel={() => setPendingReflow(null)}
        confirmLoading={busy}
      >
        <Paragraph type="warning">
          以下编辑触及确认态需求条目。确认后系统将生成新的待确认条目（旧确认态不被覆盖），当前 Markdown 等待条目修订收束后需重新生成；拒绝则退回窗口继续编辑。
        </Paragraph>
        {(pendingReflow ?? []).map((patch) => (
          <div key={patch.patch_ref} style={{ marginBottom: 12, padding: 8, background: 'var(--color-note-bg)', borderRadius: 4 }}>
            <Text type="secondary">修改前：</Text>
            <Paragraph style={{ marginBottom: 4 }}>{patch.before_text}</Paragraph>
            <Text type="secondary">修改后：</Text>
            <Paragraph style={{ marginBottom: 0 }}>{patch.after_text}</Paragraph>
          </div>
        ))}
      </Modal>

      <Modal
        title="候选 docx 检查结论"
        open={checkTarget !== null}
        onCancel={() => setCheckTarget(null)}
        footer={
          <Space>
            <Button onClick={() => setCheckTarget(null)}>取消</Button>
            <Button danger loading={busy} onClick={() => void submitCheck(false)}>检查不通过</Button>
            <Button type="primary" loading={busy} onClick={() => void submitCheck(true)}>检查通过</Button>
          </Space>
        }
      >
        <Paragraph>请先下载候选 docx 检查格式、样式与目录编号，再记录检查结论。</Paragraph>
        <Input.TextArea
          rows={3}
          placeholder="检查说明（不通过时请填写原因）"
          value={checkNote}
          onChange={(e) => setCheckNote(e.target.value)}
        />
      </Modal>

      <Modal
        title="登记人工降级导出件"
        open={fallbackOpen}
        okText="登记人工降级"
        cancelText="取消"
        onOk={() => void registerFallback()}
        onCancel={() => setFallbackOpen(false)}
        confirmLoading={busy}
      >
        <Paragraph type="warning">
          仅在系统转换失败后可登记。人工降级导出件将被明确标记，不计为系统转换成功；确认发布后基线带人工降级标记。
        </Paragraph>
        <Input.TextArea
          rows={3}
          placeholder="降级原因（必填）"
          value={fallbackReason}
          onChange={(e) => setFallbackReason(e.target.value)}
        />
      </Modal>
    </WorkbenchFrame>
  );
}

// ---- P02/P03 发布主工作台（04A §8 原型高保真复刻）：信息条 + 左大纲 / 中 Markdown 窗口 / 右定稿·导出·发布 ----

export interface MarkdownSplitHandle {
  scrollToLine: (line: number) => void;
}

/** 在 Markdown 源码中定位某章节标题所在行（先按章节号，回退按标题包含）。 */
function findHeadingLine(markdown: string, number: string, title: string): number {
  const lines = markdown.split('\n');
  const numEsc = number.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const byNumber = new RegExp(`^#{1,6}\\s+${numEsc}(?:\\s|$)`);
  for (let i = 0; i < lines.length; i++) {
    if (byNumber.test(lines[i].trim())) return i;
  }
  if (title) {
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i].trim();
      if (/^#{1,6}\s/.test(l) && l.includes(title)) return i;
    }
  }
  return -1;
}

/**
 * Markdown 源码（CodeMirror 6）+ 渲染预览：可左右拖拽双栏；两栏滚动按源码行号**双向联动**。
 * 源码区换 CodeMirror 6（详设 §7.6 方案 B）：行/块 diff 高亮、gutter 改动标记、md 语法配色、
 * 撤销/恢复/放弃与修订摘要条。预览块元素带 `data-line`（见 MarkdownPreview）用于行映射联动。
 * diff 基线＝生成稿（父层传入的载入/上次保存内容快照）；`scrollToLine` 供大纲点击跳转。
 */
const MarkdownSplit = forwardRef<
  MarkdownSplitHandle,
  { value: string; baseline: string; disabled: boolean; onChange: (value: string) => void }
>(function MarkdownSplit({ value, baseline, disabled, onChange }, ref) {
  const editorRef = useRef<CodeMirrorEditorHandle>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const driverRef = useRef<'src' | 'prev' | null>(null);
  const releaseRef = useRef<number | undefined>(undefined);
  const [wordWrap, setWordWrap] = useState(false);
  const [diff, setDiff] = useState<MarkdownDiffVM>({
    lines: [], trailingDel: 0, add: 0, chg: 0, del: 0, hunks: [],
  });
  const [navIndex, setNavIndex] = useState(0);

  const dirty = value !== baseline; // F1：放弃门禁按「当前内容 ≠ baseline」判定，纯删除态亦可放弃

  // 程序化滚动会触发对侧 onScroll；用驱动权 + 120ms 释放避免互相回弹。
  const holdDriver = (who: 'src' | 'prev') => {
    driverRef.current = who;
    window.clearTimeout(releaseRef.current);
    releaseRef.current = window.setTimeout(() => {
      driverRef.current = null;
    }, 120);
  };

  const marks = () =>
    previewRef.current
      ? Array.from(previewRef.current.querySelectorAll<HTMLElement>('[data-line]'))
      : [];

  // 源码顶部可见行 → 预览 scrollTop（按 data-line 线性插值）
  const previewScrollForLine = (line: number): number => {
    let prev: { line: number; top: number } | null = null;
    let next: { line: number; top: number } | null = null;
    for (const el of marks()) {
      const ml = Number(el.dataset.line);
      if (ml <= line) prev = { line: ml, top: el.offsetTop };
      else {
        next = { line: ml, top: el.offsetTop };
        break;
      }
    }
    if (!prev) return 0;
    if (!next) return prev.top;
    const f = (line - prev.line) / (next.line - prev.line || 1);
    return prev.top + f * (next.top - prev.top);
  };

  // 预览 scrollTop → 源码顶部行
  const lineForPreviewScroll = (): number => {
    const st = previewRef.current ? previewRef.current.scrollTop : 0;
    let prev: { line: number; top: number } | null = null;
    let next: { line: number; top: number } | null = null;
    for (const el of marks()) {
      const top = el.offsetTop;
      if (top <= st + 1) prev = { line: Number(el.dataset.line), top };
      else {
        next = { line: Number(el.dataset.line), top };
        break;
      }
    }
    if (!prev) return 0;
    if (!next) return prev.line;
    const f = (st - prev.top) / (next.top - prev.top || 1);
    return prev.line + f * (next.line - prev.line);
  };

  // CodeMirror 顶部可见行变化 → 预览按 data-line 联动
  const onEditorScroll = (topLine: number) => {
    if (driverRef.current === 'prev') return;
    holdDriver('src');
    const pv = previewRef.current;
    if (pv) pv.scrollTop = previewScrollForLine(topLine);
  };

  const onPreviewScroll = () => {
    if (driverRef.current === 'src') return;
    holdDriver('prev');
    editorRef.current?.scrollToLine(Math.round(lineForPreviewScroll()));
  };

  // 大纲点击跳转：把源码与预览一起滚到指定源码行。
  const scrollToLine = (line: number) => {
    holdDriver('src');
    editorRef.current?.scrollToLine(line);
    if (previewRef.current) previewRef.current.scrollTop = previewScrollForLine(line);
  };

  // 修订摘要条读 diff（编辑器每次防抖重算回传）；hunk 导航 + 放弃全部修改
  const onDiff = (d: MarkdownDiffVM) => {
    setDiff(d);
    setNavIndex((i) => (d.hunks.length ? Math.min(i, d.hunks.length - 1) : 0));
  };
  const gotoHunk = (dir: 1 | -1) => {
    if (diff.hunks.length === 0) return;
    const next = (navIndex + dir + diff.hunks.length) % diff.hunks.length;
    setNavIndex(next);
    holdDriver('src');
    editorRef.current?.focusHunk(diff.hunks[next]);
  };
  const discardAll = () => {
    if (dirty) onChange(baseline);
  };

  const copySource = () => {
    if (!navigator.clipboard) {
      void message.warning('当前环境不支持剪贴板');
      return;
    }
    navigator.clipboard
      .writeText(value)
      .then(() => message.success('已复制 Markdown 源码'))
      .catch(() => message.error('复制失败'));
  };

  useImperativeHandle(ref, () => ({ scrollToLine }));
  useEffect(() => () => window.clearTimeout(releaseRef.current), []);

  return (
    <Splitter className="pub-md__splitter" style={{ flex: 1, minHeight: 0 }}>
      <Splitter.Panel defaultSize="50%" min="20%">
        <div className="pub-md__pane">
          <div className="pub-md__pane-head">
            <span>Markdown 源码</span>
            <div className="pub-md__tools">
              <button
                type="button"
                className="pub-md__tool"
                disabled={disabled}
                onClick={() => editorRef.current?.undo()}
                title="撤销（回退最近一处）"
              >
                撤销
              </button>
              <button
                type="button"
                className="pub-md__tool"
                disabled={disabled}
                onClick={() => editorRef.current?.redo()}
                title="恢复（重放）"
              >
                恢复
              </button>
              <button
                type="button"
                className={wordWrap ? 'pub-md__tool is-on' : 'pub-md__tool'}
                aria-pressed={wordWrap}
                onClick={() => setWordWrap((v) => !v)}
                title="自动换行"
              >
                自动换行
              </button>
              <button type="button" className="pub-md__tool" onClick={copySource} title="复制源码">
                复制
              </button>
            </div>
          </div>
          <div className="pub-rev" role="status" data-testid="revision-bar">
            <span className="pub-rev__stat">
              本次改动 <b>{diff.hunks.length}</b> 处
              <span className="pub-rev__add"> · ＋{diff.add} 增</span>
              <span className="pub-rev__chg"> · ~{diff.chg} 改</span>
              {diff.del > 0 ? <span className="pub-rev__del"> · －{diff.del} 删</span> : null}
            </span>
            <span className="pub-rev__nav">
              {diff.hunks.length > 0 ? (
                <span className="pub-rev__pos">位置 {navIndex + 1}/{diff.hunks.length}</span>
              ) : (
                <span className="pub-rev__muted">无改动</span>
              )}
              <button
                type="button"
                className="pub-md__tool"
                disabled={diff.hunks.length === 0}
                onClick={() => gotoHunk(-1)}
                title="上一处改动"
                aria-label="上一处改动"
              >
                ‹
              </button>
              <button
                type="button"
                className="pub-md__tool"
                disabled={diff.hunks.length === 0}
                onClick={() => gotoHunk(1)}
                title="下一处改动"
                aria-label="下一处改动"
              >
                ›
              </button>
              <button
                type="button"
                className="pub-md__tool pub-rev__discard"
                disabled={!dirty || disabled}
                onClick={discardAll}
                title="放弃全部修改，回落生成稿"
              >
                放弃全部修改
              </button>
            </span>
          </div>
          <div className="pub-src">
            <CodeMirrorEditor
              ref={editorRef}
              value={value}
              baseline={baseline}
              disabled={disabled}
              wordWrap={wordWrap}
              onChange={onChange}
              onDiff={onDiff}
              onScrollTopLine={onEditorScroll}
            />
          </div>
        </div>
      </Splitter.Panel>
      <Splitter.Panel min="20%">
        <div className="pub-md__pane">
          <div className="pub-md__pane-head">渲染预览</div>
          <div
            ref={previewRef}
            className="md-render pub-md__preview"
            onScroll={onPreviewScroll}
          >
            <MarkdownPreview markdown={value} />
          </div>
        </div>
      </Splitter.Panel>
    </Splitter>
  );
});

function MainPublicationView(props: {
  workspace: PublicationWorkspaceRead;
  mdState: ReturnType<typeof buildMarkdownState>;
  markdownText: string;
  baselineText: string;
  busy: boolean;
  onMarkdownChange: (value: string) => void;
  onSubmitEdit: () => void;
  onRegenerate: () => void;
  onFinalize: () => void;
  onReopenIndex: () => void;
  onGoIndex: () => void;
  onGoTemplate: () => void;
  onRefresh: () => void;
  onStartExport: () => void;
  onOpenCheck: (exportRef: string) => void;
  onConfirmBaseline: (exportRef: string) => void;
  onOpenFallback: () => void;
  exportFileUrl: (exportRef: string) => string;
  onPreviewDocx: (exportRef: string, title: string) => void;
  onViewTrace: (documentRef: string, title: string) => void;
}) {
  const {
    workspace, mdState, markdownText, baselineText, busy,
    onMarkdownChange, onSubmitEdit, onRegenerate, onFinalize, onReopenIndex,
    onGoIndex, onGoTemplate, onRefresh,
    onStartExport, onOpenCheck, onConfirmBaseline, onOpenFallback, exportFileUrl,
    onPreviewDocx, onViewTrace,
  } = props;

  const [showImpactDetail, setShowImpactDetail] = useState(false);

  const doc = workspace.document;
  const status = doc?.status;
  const indexFormed = !!status && status !== 'index_draft' && status !== 'index_blocked';
  const markdown = workspace.markdown;

  // 大纲点击 → 编辑器（源码+预览）跳到该章节：按章节号在源码里定位标题行。
  const mdSplitRef = useRef<MarkdownSplitHandle>(null);
  const jumpToSection = (number: string, title: string) => {
    if (!markdown) return;
    const line = findHeadingLine(markdownText, number, title);
    if (line >= 0) mdSplitRef.current?.scrollToLine(line);
  };
  const baseline = workspace.baseline;

  const header = buildIndexHeader(workspace, workspace.index_entries);
  const outline = buildOutlineTree(
    workspace.template.sections,
    workspace.slot_status,
    markdown?.patches ?? [],
    workspace.index_entries,
  );
  const impact = buildEditImpactGroups(
    markdown?.patches ?? [],
    workspace.index_entries,
    workspace.template.sections,
  );
  // 导出记录按「本轮」过滤。判据＝中间稿存在**且未作废**。
  // 注意 reopen_index（调整索引编排 · 开始新一轮）只把旧稿状态迁为 superseded/awaiting_item_revision，
  // 并不新建中间稿——新稿只在「重新生成 Markdown」时产生。故 reopen 之后若只按 draft_ref 匹配，上一轮
  // 的导出记录（draft_ref 未变）会被当作本轮渲染：灰色未达节点下并列红色失败卡，其「重试转换」点下去
  // 命中后端「Markdown 未定稿或不可导出」被拒（C2）。中间稿一旦作废即视作本轮无导出记录，上一轮的产物
  // 仍可从下方发布基线历史区查看。
  const roundDraft =
    markdown && markdown.status !== 'superseded' && markdown.status !== 'awaiting_item_revision'
      ? markdown
      : null;
  const roundExports = roundDraft
    ? workspace.exports.filter((e) => e.draft_ref === roundDraft.draft_ref)
    : [];
  const failedExports = roundExports.filter(
    (e) => e.status === 'failed' || e.status === 'check_rejected',
  );
  // 候选件只保留可操作态（转换中/待检查/人工降级）；baseline_confirmed 归发布基线卡，
  // failed/check_rejected 归失败就地处理。exports 已按 created_at desc → [0] 即最新。
  const candidateExports = roundExports.filter(
    (e) => e.status === 'converting' || e.status === 'succeeded' || e.status === 'manual_fallback',
  );
  const latestCandidate = candidateExports[0];  // 只展示最新一个候选件，历史不再堆叠
  const latestExport = roundExports[0];
  // 是否有在途转换：用于禁用生成/重试按钮，杜绝转换期间重复点击、重复提交。
  const converting = candidateExports.some((e) => e.status === 'converting');
  // 基线已发布 = 只读复核态：状态机不接受 generate-md（默认拒绝），须走「调整索引编排」开新一轮。
  const baselinePublished = status === 'baseline_published';

  // 分阶段状态徽标
  const finalizeBadge = markdown?.status === 'finalized'
    ? { text: '已定稿', tone: 'success' as BadgeTone }
    : mdState.canFinalize
      ? { text: '可定稿', tone: 'processing' as BadgeTone }
      : { text: '待定稿', tone: 'neutral' as BadgeTone };
  const canExport = markdown?.status === 'finalized' && markdown.can_export;
  const exportBadge = candidateExports.some((e) => e.status === 'succeeded' || e.status === 'manual_fallback')
    ? { text: '候选件待检查', tone: 'success' as BadgeTone }
    : canExport
      ? { text: '可生成', tone: 'processing' as BadgeTone }
      : { text: '待生成', tone: 'neutral' as BadgeTone };
  // 第 3 步徽标按「本轮」判定：上一轮的基线不让本步显示为已形成（那条基线的详情与入口
  // 在新一轮里挂到下方发布基线历史区，事实不丢）。
  const baselineBadge = baselinePublished
    ? { text: '已形成', tone: 'success' as BadgeTone }
    : { text: '待确认', tone: 'neutral' as BadgeTone };

  // 竖向进度条形态（原型 v1 方案 A）：只翻译上面已算好的业务状态，不新增任何判定。
  const steps = derivePublishSteps({
    finalized: markdown?.status === 'finalized',
    converting,
    hasActionableCandidate: latestCandidate?.status === 'succeeded' || latestCandidate?.status === 'manual_fallback',
    hasFailure: failedExports.length > 0,
    published: baselinePublished,
  });
  // 已成基线时第 2 步收为完成态：徽标与检查结论取那条被确认为基线的导出记录
  // （它已从 candidateExports 中被排除，故 exportBadge 在此状态下不适用）。
  const baselineExport = baseline
    ? workspace.exports.find((e) => e.export_ref === baseline.export_ref)
    : undefined;
  // 已成基线态第 2 步单行摘要的文案：区分三种来源，避免把登记说明冠以「检查结论：」，
  // 或在无结论时退化成没有标签的裸时间戳（C3）。
  //   · 人工降级：check_note 本身即自带「人工降级登记：…」标签，直接显示，不再加前缀；
  //   · 真检查结论：加「检查结论：」前缀；
  //   · 未记录检查结论：给带标签兜底。
  const baselineSummary = !baselineExport
    ? null
    : baselineExport.manual_fallback
      ? baselineExport.check_note || '人工降级交付'
      : baselineExport.check_note
        ? `检查结论：${baselineExport.check_note}`
        : '未记录检查结论';
  // 「生成候选 docx」按钮的禁用条件；已发布只读复核态不再渲染该按钮（C1），此处仅用于第 2 步
  // 未收束时的生成/重试。
  const exportDisabled = !canExport || busy || converting;

  // 基线详情块（基线号/索引版本/模板/确认人与时间/降级标记/预览/下载/追溯）。
  // 两处复用同一块：本轮已发布时挂在第 3 步内，新一轮进行中时挂在下方发布基线历史区。
  const renderBaselineDetail = () => {
    if (!baseline) return null;
    return (
      <div className="pub-vstep__sub" data-testid="baseline-card">
        <div className="pub-vstep__note pub-vstep__num">
          基线 {baseline.baseline_ref.slice(0, 8)}… · 索引版本 v{baseline.index_version}
        </div>
        <div className="pub-vstep__note">模板 {baseline.template_ref}</div>
        <div className="pub-vstep__note pub-vstep__num">
          确认人 {baseline.confirmed_by} · {formatAbsoluteTime(baseline.confirmed_at)}
        </div>
        {baseline.manual_fallback ? (
          <div className="pub-vstep__subrow">
            <StepChip tone="warning" text="人工降级交付" />
          </div>
        ) : null}
        <div className="pub-vstep__actions">
          <Button size="small" type="primary" ghost onClick={() => onPreviewDocx(baseline.export_ref, '发布基线 docx 预览')}>预览</Button>
          <Button size="small" href={exportFileUrl(baseline.export_ref)} target="_blank">下载基线 docx</Button>
          <Button size="small" onClick={() => onViewTrace(baseline.document_ref, doc?.title ?? '需求文档')}>查看追溯关系</Button>
        </div>
      </div>
    );
  };

  return (
    <div className="pub-main">
      <div className="pub-infobar" data-testid="pub-main-infobar">
        <span className="pub-infobar__field">
          <Text type="secondary">当前文档</Text>
          <Text strong>{header.docTitle}</Text>
        </span>
        <span className="pub-infobar__field">
          <Text type="secondary">文档状态</Text>
          <Tag color={TONE_COLOR[header.statusTone]}>{header.statusText}</Tag>
        </span>
        <div className="pub-infobar__stats">
          <span className="pub-infobar__stat">需求总数 <b>{header.stats.total}</b></span>
          <span className="pub-infobar__stat pub-infobar__stat--ok">已确认 <b>{header.stats.confirmed}</b></span>
          <span className="pub-infobar__stat pub-infobar__stat--warn">待确认 <b>{header.stats.pending}</b></span>
          <span className="pub-infobar__stat pub-infobar__stat--danger">缺失槽位 <b>{header.stats.missingSlots}</b></span>
        </div>
      </div>

      {!doc ? (
        <div className="panel pub-main__guard" data-testid="main-guard-template">
          <Empty description="尚未选择文档模板：发布须先选定并预览模板，再进入索引编排">
            <Button type="primary" onClick={onGoTemplate}>选择文档模板</Button>
          </Empty>
        </div>
      ) : !indexFormed ? (
        <div className="panel pub-main__guard" data-testid="main-guard-index">
          <Empty
            description={
              status === 'index_blocked'
                ? '文档内容索引受阻：请回到索引编排补齐必填槽位'
                : '文档内容索引尚未形成：请先在索引编排页完成编排'
            }
          >
            <Button type="primary" onClick={onGoIndex}>去索引编排</Button>
          </Empty>
        </div>
      ) : (
        <Splitter className="pub-main__splitter" style={{ height: '100%' }}>
          {/* 左区：文档大纲 */}
          <Splitter.Panel defaultSize="22%" min="240px" max="42%" collapsible>
          <section className="panel pub-main__outline" data-testid="doc-outline">
            <div className="panel__header">
              <h2 className="panel__title">文档大纲</h2>
              <Button size="small" type="text" onClick={onReopenIndex}>调整索引编排</Button>
            </div>
            <div className="panel__body pub-outline__body">
              {outline.chapters.map((chapter) => (
                <div key={chapter.key} className="pub-outline__chapter">
                  <div
                    className="pub-outline__chapter-head pub-outline__jump"
                    role="button"
                    tabIndex={0}
                    onClick={() => jumpToSection(chapter.number, chapter.title)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        jumpToSection(chapter.number, chapter.title);
                      }
                    }}
                  >
                    <span className="pub-outline__chapter-title">{chapter.number} {chapter.title}</span>
                    <span className="pub-outline__badges">
                      <Tag color={TONE_COLOR[chapter.bindingTone]} className="pub-outline__binding">{chapter.bindingText}</Tag>
                      <span className="pub-outline__count">{chapter.childCount}</span>
                    </span>
                  </div>
                  {chapter.rows.map((row) => (
                    <div
                      key={row.key}
                      className="pub-outline__row pub-outline__jump"
                      role="button"
                      tabIndex={0}
                      onClick={() => jumpToSection(row.number, row.title)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          jumpToSection(row.number, row.title);
                        }
                      }}
                    >
                      <span className="pub-outline__row-title">{row.number} {row.title}</span>
                      {row.showStatus ? (
                        <Tag color={TONE_COLOR[row.statusTone]} className="pub-outline__row-status">{row.statusText}</Tag>
                      ) : null}
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div className="pub-outline__foot">
              <span>共 {outline.chapterCount} 章 / {outline.sectionCount} 节</span>
              <Button size="small" type="text" onClick={onRefresh}>刷新</Button>
            </div>
          </section>
          </Splitter.Panel>

          {/* 中区：Markdown 工作窗口 */}
          <Splitter.Panel min="360px">
          <section className="panel pub-main__editor" data-testid="markdown-window">
            <div className="panel__header">
              <h2 className="panel__title">Markdown 工作窗口</h2>
              <Space size={6}>
                <Tag color={TONE_COLOR[mdState.statusTone]}>{mdState.statusText}</Tag>
                <Button size="small" loading={busy} disabled={!mdState.canEdit} onClick={onSubmitEdit}>保存编辑并识别影响</Button>
                {baselinePublished ? (
                  <>
                    <Tooltip title="发布基线为只读复核态，不能重新生成；如需修改请『调整索引编排』开始新一轮">
                      <span><Button size="small" disabled>重新生成</Button></span>
                    </Tooltip>
                    <Button size="small" type="primary" ghost onClick={onReopenIndex}>调整索引编排</Button>
                  </>
                ) : (
                  <Button size="small" loading={busy} onClick={onRegenerate}>重新生成</Button>
                )}
              </Space>
            </div>
            <div className="panel__body">
              {!markdown ? (
                <Empty description="索引已就绪，尚未生成 Markdown 中间稿">
                  <Button type="primary" loading={busy} onClick={onRegenerate}>生成 Markdown</Button>
                </Empty>
              ) : (
                <>
                  {mdState.needsRegenerate ? (
                    <Alert
                      type="warning"
                      showIcon
                      title={mdState.statusText}
                      description="索引已调整或条目修订回流中：请重新生成 Markdown。"
                      action={<Button size="small" loading={busy} onClick={onRegenerate}>重新生成</Button>}
                      style={{ marginBottom: 8 }}
                    />
                  ) : null}
                  {mdState.blockReasons.map((reason) => (
                    <Alert key={reason} type="error" showIcon title={reason} style={{ marginBottom: 8 }} />
                  ))}
                  <MarkdownSplit ref={mdSplitRef} value={markdownText} baseline={baselineText} disabled={!mdState.canEdit} onChange={onMarkdownChange} />
                  <div className="pub-md__foot">
                    <span>字数 {markdownText.length.toLocaleString()}</span>
                    <span>行数 {Math.max(markdownText.split('\n').length, 1)}</span>
                    <span>上次定稿 {formatAbsoluteTime(markdown.finalized_at)}</span>
                  </div>

                  <div className="pub-main__impact" data-testid="edit-impact">
                    <div className="pub-main__impact-head">
                      <div className="pub-impact-overview">
                        <Text strong>编辑影响</Text>
                        <span className="pub-impact-overview__sentence">{impact.overviewText}</span>
                        <Tag color={TONE_COLOR[impact.verdict.tone]} className="pub-impact-overview__verdict">
                          {impact.verdict.label}
                        </Tag>
                      </div>
                      <a className="pub-link" onClick={() => setShowImpactDetail((v) => !v)}>
                        {showImpactDetail ? '收起明细' : '查看明细'}
                      </a>
                    </div>
                    <div className="pub-impact-groups">
                      {impact.groups.map((group) => (
                        <div
                          key={group.key}
                          className={`pub-impact-group pub-impact-group--${group.tone}${group.count === 0 ? ' is-empty' : ''}`}
                        >
                          <div className="pub-impact-group__head">
                            <span className="pub-impact-group__title">{group.title}</span>
                            <span className="pub-impact-group__count">{group.count}</span>
                          </div>
                          {group.count === 0 ? (
                            <div className="pub-impact-group__empty">{group.emptyText}</div>
                          ) : showImpactDetail ? (
                            <ul className="pub-impact-group__list">
                              {group.items.map((it) => (
                                <li key={it.key} className="pub-impact-group__item">
                                  <span className={`pub-impact-group__dot pub-impact-group__dot--${it.tone}`} aria-hidden />
                                  <span className="pub-impact-group__label">{it.label}</span>
                                  <span className="pub-impact-group__detail">{it.detail}</span>
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          </section>
          </Splitter.Panel>

          {/* 右区：定稿 · 导出 · 发布 */}
          <Splitter.Panel defaultSize="26%" min="280px" collapsible>
          <section className="panel pub-main__rail" data-testid="finalize-export-panel">
            <div className="panel__header"><h2 className="panel__title">定稿 · 导出 · 发布</h2></div>
            <div className="panel__body pub-rail__body">
              {/* 竖向进度条：定稿 → 候选 docx → 发布基线（原型 v1 方案 A · 细轨圆节点）。
                  三阶段业务逻辑、按钮回调、轮询、状态判定全部沿用既有实现，此处只重排结构。 */}
              <div className="pub-vsteps" data-testid="pub-vsteps">
                {/* 已发布基线＝只读复核态：顶部完成横幅承载复核说明与开启新一轮的入口 */}
                {baselinePublished ? (
                  <div className="pub-vsteps__doneband" data-testid="pub-doneband">
                    <span className="pub-vsteps__doneband-mark" aria-hidden>✓</span>
                    <span className="pub-vsteps__doneband-text">
                      <b>发布基线已形成</b><br />
                      <span>本轮交付快照已冻结，页面转入只读复核态。</span>
                    </span>
                    <Button size="small" onClick={onReopenIndex}>调整索引编排 · 开始新一轮</Button>
                  </div>
                ) : null}

                {/* 第 1 步：Markdown 定稿 */}
                <div className="pub-vstep">
                  <StepRail index={1} state={steps[0]} />
                  <div className={`pub-vstep__card pub-vstep__card--${steps[0].card}`}>
                    <div className="pub-vstep__titlerow">
                      <span className="pub-vstep__title">Markdown 定稿</span>
                      <StepChip tone={finalizeBadge.tone} text={finalizeBadge.text} />
                    </div>
                    {steps[0].node === 'done' ? (
                      // 已定稿：收为单行摘要（谁、何时）。此状态下「确认 Markdown 定稿」按钮的
                      // 禁用条件（status !== 'draft'）必然成立，隐藏不损失任何可操作能力。
                      <div className="pub-vstep__meta pub-vstep__meta--last pub-vstep__num">
                        已锁定内容作为导出依据
                        {markdown?.finalized_by ? ` · ${markdown.finalized_by}` : ''}
                        {markdown?.finalized_at ? ` · ${formatAbsoluteTime(markdown.finalized_at)}` : ''}
                      </div>
                    ) : (
                      <>
                        <div className="pub-vstep__meta">
                          当前状态：{mdState.statusText}
                          {markdown?.finalized_by ? ` · 上次定稿 ${markdown.finalized_by}` : ''}
                        </div>
                        <Button
                          type="primary"
                          block
                          loading={busy}
                          disabled={!markdown || markdown.status !== 'draft'}
                          onClick={onFinalize}
                        >
                          确认 Markdown 定稿
                        </Button>
                        <div className="pub-vstep__hint">定稿后将锁定内容，作为可导出依据。</div>
                      </>
                    )}
                  </div>
                </div>

                {/* 第 2 步：候选 docx（失败就地处理收在本步卡内） */}
                <div className="pub-vstep">
                  <StepRail index={2} state={steps[1]} />
                  <div className={`pub-vstep__card pub-vstep__card--${steps[1].card}`}>
                    <div className="pub-vstep__titlerow">
                      <span className="pub-vstep__title">候选 docx</span>
                      {steps[1].node === 'done' ? (
                        <StepChip tone="success" text={exportStatusMeta('baseline_confirmed').label} />
                      ) : steps[1].node === 'busy' ? (
                        // 转换中：徽标报「转换中」，不落回 exportBadge 的「可生成」——旋转环、流光条与
                        // 按钮上的「转换中…」都在说转换在跑，徽标若还写「可生成」便与三者矛盾（C4）。
                        <StepChip tone="processing" text={exportStatusMeta('converting').label} />
                      ) : steps[1].node === 'failed' && failedExports[0] ? (
                        // 失败步的徽标报失败（原型 ③），而不是报「可生成」——能否再生成由下方按钮表达，
                        // 徽标该说的是这一步当前的处境。
                        <StepChip tone="danger" text={exportStatusMeta(failedExports[0].status).label} />
                      ) : (
                        <StepChip tone={exportBadge.tone} text={exportBadge.text} />
                      )}
                    </div>

                    {steps[1].node === 'done' ? (
                      // 已成基线＝只读复核态：第 2 步收为单行摘要，不再渲染任何写入按钮（原型 ⑥）。
                      // 此态下「生成候选 docx」虽仍被后端受理并建 converting 行，但其产物在只读态永不
                      // 可见、永不可确认，是通向死路的写入动作，故一并折叠——要再转换须走顶部
                      // 「调整索引编排 · 开始新一轮」（C1）。摘要文案区分登记说明与真检查结论（C3）。
                      <div className="pub-vstep__meta pub-vstep__num pub-vstep__meta--last">
                        {baselineExport
                          ? `${baselineSummary} · 转换于 ${formatAbsoluteTime(baselineExport.created_at)}`
                          : '已确认为发布基线'}
                      </div>
                    ) : (
                      <>
                        {steps[1].node !== 'failed' ? (
                          // 失败态不渲染「定稿后可生成」说明与整宽生成按钮：该按钮与失败卡内的「重试转换」
                          // 是同一个回调（onStartExport），且「定稿后可生成」在失败情境下是无关文案；原型 ③
                          // 卡内只有失败原因与两个按钮（P4）。转换中态的说明文案改为转换语义（C4）。
                          <>
                            <div className="pub-vstep__meta">
                              {steps[1].node === 'busy'
                                ? '异步转换进行中 · 预计 15~45 秒'
                                : '定稿后可生成 · 预计 15~45 秒（异步）'}
                            </div>
                            {steps[1].node === 'busy' ? (
                              <div className="pub-vstep__progress" aria-hidden><i /></div>
                            ) : null}
                            <Button
                              block
                              loading={busy || converting}
                              disabled={exportDisabled}
                              onClick={onStartExport}
                            >
                              {converting ? '转换中…' : '生成候选 docx'}
                            </Button>
                          </>
                        ) : null}

                        {/* 候选件子块只在有可操作候选件（转换成功待检查 / 人工降级）时渲染。转换中的记录也会
                            进 latestCandidate，但那时它只有徽标与时间、没有任何可操作入口，原型 ② 卡内也没有
                            子块，故转换中不渲染这个空子块（P3）。 */}
                        {latestCandidate && latestCandidate.status !== 'converting' ? (() => {
                          const meta = exportStatusMeta(latestCandidate.status);
                          const actionable = latestCandidate.status === 'succeeded' || latestCandidate.status === 'manual_fallback';
                          return (
                            <div className={`pub-vstep__sub${meta.tone === 'success' ? ' pub-vstep__sub--ok' : ''}`} data-testid="pub-candidate">
                              <div className="pub-vstep__subrow">
                                <StepChip tone={meta.tone} text={meta.label} />
                                {latestCandidate.manual_fallback ? <StepChip tone="warning" text="人工降级" /> : null}
                                <span className="pub-vstep__time">{formatAbsoluteTime(latestCandidate.created_at)}</span>
                              </div>
                              {latestCandidate.check_note ? (
                                <div className="pub-vstep__note">{latestCandidate.check_note}</div>
                              ) : null}
                              <div className="pub-vstep__actions">
                                {latestCandidate.file_available ? (
                                  <>
                                    <Button size="small" type="primary" ghost onClick={() => onPreviewDocx(latestCandidate.export_ref, '候选 docx 预览')}>预览</Button>
                                    <Button size="small" href={exportFileUrl(latestCandidate.export_ref)} target="_blank">下载检查</Button>
                                  </>
                                ) : null}
                                {actionable ? (
                                  <>
                                    <Button size="small" onClick={() => onOpenCheck(latestCandidate.export_ref)}>记录检查结论</Button>
                                    <Button size="small" type="primary" onClick={() => onConfirmBaseline(latestCandidate.export_ref)}>确认发布基线</Button>
                                  </>
                                ) : null}
                              </div>
                            </div>
                          );
                        })() : null}

                        {/* 失败就地处理：失败本就属于本步，从独立区块迁入卡内；多条逐条渲染。
                            置于本（未收束）分支内——已发布只读复核态第 2 步已收束（node=done），不应再在绿色
                            完成卡里并列红色失败卡（C1 加重情形一）；跨轮的旧失败记录则已被上方「本轮」过滤按
                            未作废中间稿排除，不会落到这里（C2）。 */}
                        {failedExports.length > 0 ? (
                          <div data-testid="failure-handling">
                            {/* 旧的独立区块用标题「失败就地处理 N」承载条数；迁入步内后逐条渲染，
                                条数只在多于一条时才是额外信息，故此时补一行，单条时不重复告知。 */}
                            {failedExports.length > 1 ? (
                              <div className="pub-vstep__hint">失败就地处理 · 共 {failedExports.length} 条</div>
                            ) : null}
                            {failedExports.map((exportItem) => (
                              <div key={exportItem.export_ref} className="pub-vstep__sub pub-vstep__sub--fail">
                                <div className="pub-vstep__reason">{exportStatusMeta(exportItem.status).label}</div>
                                {exportItem.failure_reason ? (
                                  <div className="pub-vstep__note">{exportItem.failure_reason}</div>
                                ) : null}
                                {exportItem.check_note ? (
                                  <div className="pub-vstep__note">{exportItem.check_note}</div>
                                ) : null}
                                <div className="pub-vstep__note pub-vstep__num">{formatAbsoluteTime(exportItem.created_at)}</div>
                                <div className="pub-vstep__actions">
                                  <Button size="small" danger loading={converting} disabled={converting} onClick={onStartExport}>重试转换</Button>
                                  <Button size="small" onClick={onOpenFallback}>登记人工降级</Button>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}

                        {steps[1].node === 'busy' ? (
                          // 转换中提示「可离开本页」（原型 ②）。原提示行条件为「无任何导出记录」，而转换中
                          // 必有记录，故那句在转换中永不出现（P3）——此处按转换态直接给出。
                          <div className="pub-vstep__hint">可离开本页，转换完成后回到此处查看结果。</div>
                        ) : !latestExport ? (
                          <div className="pub-vstep__hint">生成后将进行检查，形成检查结论（导出成功不等于发布）。</div>
                        ) : null}
                      </>
                    )}
                  </div>
                </div>

                {/* 第 3 步：发布基线 */}
                <div className="pub-vstep">
                  <StepRail index={3} state={steps[2]} last />
                  <div className={`pub-vstep__card pub-vstep__card--${steps[2].card}`}>
                    <div className="pub-vstep__titlerow">
                      <span className="pub-vstep__title">发布基线</span>
                      <StepChip tone={baselineBadge.tone} text={baselineBadge.text} />
                    </div>
                    {/* 只有本轮确认的基线才留在步内。新一轮进行中时，上一轮的基线连同预览/下载/追溯
                        三个入口整块移到下方发布基线历史区——进度条只反映本轮进度，能力一个不少。 */}
                    {baseline && baselinePublished ? (
                      renderBaselineDetail()
                    ) : (
                      <div className="pub-vstep__meta pub-vstep__meta--last">
                        需基于通过检查的候选 docx 进行确认，形成发布基线快照。
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* 发布基线历史（只读；v0.1 单文档单基线） */}
              <div className="pub-history" data-testid="baseline-history">
                <div className="pub-history__head">发布基线历史（只读）</div>
                {baseline ? (
                  <table className="pub-history__table">
                    <thead>
                      <tr><th>版本</th><th>发布时间</th><th>发布人</th><th>状态</th></tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>v{baseline.index_version}</td>
                        <td>{formatAbsoluteTime(baseline.confirmed_at)}</td>
                        <td>{baseline.confirmed_by}</td>
                        <td>
                          {/* 新一轮进行中时这条基线已不是「当前」，而是上一轮的基线；下方说明块也称其
                              「上一轮」，两处文案须同源，避免一条基线被相邻标签同时称作「当前」和「上一轮」（C7）。 */}
                          <Tag color={baselinePublished ? 'green' : 'default'}>{baselinePublished ? '当前' : '上一轮'}</Tag>
                          {baseline.manual_fallback ? <Tag color="orange">降级</Tag> : null}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                ) : (
                  <Text type="secondary" style={{ fontSize: 12 }}>尚无发布基线（v0.1 单文档单基线）</Text>
                )}
                {/* 新一轮进行中：上一轮基线的详情与三个入口落在这里（历史区本就是过往轮次的位置）。
                    已发布只读复核态下它归第 3 步，此处不重复。 */}
                {baseline && !baselinePublished ? (
                  <div className="pub-vsteps pub-history__baseline">
                    <div className="pub-vstep__hint">上一轮的发布基线（本轮尚未确认新基线）</div>
                    {renderBaselineDetail()}
                  </div>
                ) : null}
              </div>
            </div>
          </section>
          </Splitter.Panel>
        </Splitter>
      )}
    </div>
  );
}

// ---- 模板选择步（P01 前置）：选定/预览模板 → 基于该模板进入索引编排 ----
// 登记与版本停用也在此承接（登记仍只登记不管理：内容送检、快照不可变、无编辑删除）。

function TemplateSelectionView(props: {
  choices: TemplateChoiceVM[];
  versionRows: TemplateRowVM[];
  selectedTemplateId: string | undefined;
  busy: boolean;
  operatorRef: string;
  onPreview: (registryRef: string) => void;
  onChoose: (templateId: string) => void;
  onRegistryChanged: () => void;
  onOpenSettingsDomain: (domain: SettingsDomainKey) => void;
}) {
  const { choices, versionRows, selectedTemplateId, busy, operatorRef, onPreview, onChoose, onRegistryChanged, onOpenSettingsDomain } = props;
  const [statusBusy, setStatusBusy] = useState(false);

  const setStatus = async (row: TemplateRowVM, status: 'active' | 'disabled') => {
    setStatusBusy(true);
    try {
      await templatesApi.setStatus(row.registryRef, status, operatorRef);
      onRegistryChanged();
      void message.success(status === 'disabled' ? '模板已停用（历史基线引用不受影响）' : '模板已启用');
    } catch (error) {
      void message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setStatusBusy(false);
    }
  };

  const versionColumns = [
    { title: '模板', render: (_: unknown, r: TemplateRowVM) => (
      <Space orientation="vertical" size={0}>
        <Text>{r.name}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>{r.templateKey} · {r.versionText} · schema {r.schemaVersion}</Text>
      </Space>
    ) },
    { title: '内容哈希', dataIndex: 'hashShort', render: (v: string) => <Text code style={{ fontSize: 12 }}>{v}…</Text> },
    { title: '来源', render: (_: unknown, r: TemplateRowVM) => <Tag color={TONE_COLOR[r.sourceTone]}>{r.sourceText}</Tag> },
    { title: '状态', render: (_: unknown, r: TemplateRowVM) => <Tag color={TONE_COLOR[r.statusTone]}>{r.statusText}</Tag> },
    { title: '登记', render: (_: unknown, r: TemplateRowVM) => (
      <Text type="secondary" style={{ fontSize: 12 }}>{r.registeredBy} · {r.registeredAtText}</Text>
    ) },
    { title: '操作', render: (_: unknown, r: TemplateRowVM) => (
      <Space size={4}>
        <Button size="small" onClick={() => onPreview(r.registryRef)}>预览</Button>
        {r.canDisable ? (
          <Button size="small" danger loading={statusBusy} onClick={() => void setStatus(r, 'disabled')}>停用</Button>
        ) : null}
        {r.canEnable ? (
          <Button size="small" loading={statusBusy} onClick={() => void setStatus(r, 'active')}>启用</Button>
        ) : null}
      </Space>
    ) },
  ];

  return (
    <section className="panel pub-template-select" data-testid="template-selection">
      <div className="panel__header" style={{ display: 'flex', alignItems: 'center' }}>
        <h2 className="panel__title" style={{ flex: 1 }}>选择文档模板</h2>
        <Button data-testid="publication-goto-template-settings" onClick={() => onOpenSettingsDomain('document_template')}>
          管理模板（设置 › 文档模板）
        </Button>
      </div>
      <div className="panel__body">
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          先选定并预览模板，再基于该模板进行索引编排；保存索引时冻结所选模板的登记快照（模板后续升级不影响本文档）。
          模板的定制与登记已迁至<b>设置 › 文档模板</b>；发布环节只选用与预览。
        </Paragraph>
        {choices.length === 0 ? (
          <Empty
            description="暂无可用模板：请到「设置 › 文档模板」定制或登记"
            data-testid="publication-template-empty"
          >
            <Button type="primary" onClick={() => onOpenSettingsDomain('document_template')}>
              前往设置 › 文档模板
            </Button>
          </Empty>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
            {choices.map((choice) => (
              <div
                key={choice.templateId}
                data-testid="template-card"
                style={{
                  border: choice.templateId === selectedTemplateId ? '2px solid var(--color-primary)' : '1px solid var(--color-border)',
                  borderRadius: 8,
                  padding: 12,
                }}
              >
                <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                  <Space size={6} wrap>
                    <Text strong>{choice.name}</Text>
                    <Tag color={TONE_COLOR[choice.sourceTone]}>{choice.sourceText}</Tag>
                    {choice.templateId === selectedTemplateId ? <Tag color="blue">当前选用</Tag> : null}
                  </Space>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {choice.templateId} · {choice.versionText} · schema {choice.schemaVersion} · {choice.hashShort}…
                  </Text>
                  <Space>
                    <Button size="small" onClick={() => onPreview(choice.registryRef)}>预览结构与版式</Button>
                    <Button size="small" type="primary" loading={busy} onClick={() => onChoose(choice.templateId)}>
                      选用此模板编排
                    </Button>
                  </Space>
                </Space>
              </div>
            ))}
          </div>
        )}
        <Collapse
          ghost
          style={{ marginTop: 12 }}
          items={[{
            key: 'versions',
            label: `全部登记版本（${versionRows.length}）· 停用/启用`,
            children: (
              <Table<TemplateRowVM>
                rowKey="registryRef"
                size="small"
                columns={versionColumns}
                dataSource={versionRows}
                pagination={false}
              />
            ),
          }]}
        />
      </div>
    </section>
  );
}
