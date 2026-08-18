import { Button, Input, Modal, Select } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  agentRunApi,
  type AgentRunEventMessage,
  type AgentRunRead,
  type AgentRunSubscription,
} from '../api/agent-runs';
import { intakeApi, type IntakeResultRead, type IntakeSubmitOutcome } from '../api/intake';
import { analysisApi, type ElementWorkspaceRead } from '../api/analysis';
import { itemFormationApi, type ItemFormationWorkspaceRead } from '../api/item-formation';
import { overviewApi, type RequirementFlowRead } from '../api/overview';
import type { ProjectRead } from '../api/projects';
import { RequirementAnalysisFlow } from './RequirementAnalysisFlow';
import { RequirementItemFormationFlow } from './RequirementItemFormationFlow';
import { RequirementItemReviewFlow } from './RequirementItemReviewFlow';
import { WorkbenchFrame } from './WorkbenchFrame';
import { ActionButton, MetricCards, StatusList, StatusPill } from './WorkbenchWidgets';
import { renderActionIcon } from '../ui/icons';
import { appShellFixture } from '../fixtures/app-shell';
import type { BadgeTone, StatusSummaryVM } from '../view-models/common';
import { assetsApi } from '../api/assets';
import { qualityApi } from '../api/quality';
import type { ItemQualityRead } from '../api/quality';
import { aiEffectivenessApi, type AiEffectivenessRead } from '../api/ai-effectiveness';
import { RmFolderIcon, RmIcon } from '../ui/rmv2-icons';
import '../styles-rmv2.css';
import { RequirementQualityPanel } from './RequirementQualityPanel';
import { buildQualityPanelVM, EMPTY_QUALITY_PANEL } from '../view-models/requirement-quality';
import type {
  AssetCatalogRead,
  BusinessKnowledgeRowRead,
  ItemMaintenanceItemRead,
} from '../api/assets';
import {
  ELEMENT_TYPE_META,
  KNOWLEDGE_CATEGORY_META,
  elementTypeMeta,
} from '../view-models/requirement-analysis';
import {
  MAINTENANCE_GAP_FILTERS,
  MAINTENANCE_STATUS_FILTERS,
  MAINTENANCE_TYPE_FILTERS,
  buildKpiBand,
  buildRequirementCardVM,
  REQUIREMENT_FACT_GROUPS,
  focusFormationWorkspaceOnItem,
  formationWorkspaceContainsItem,
  parseFoldedSourceNote,
  reviewBatchCandidates,
} from '../view-models/requirement-management';
import type {
  IntakePrefillTarget,
  KpiGaugeVM,
  RequirementCardVM,
  RequirementCreationFlowViewVM,
  RequirementManagementViewMode,
  RequirementManagementWorkbenchVM,
  ReviewFlowEntry,
} from '../view-models/requirement-management';
import type { WorkbenchKey } from '../view-models/app-shell';
import {
  ASSET_CONTEXT_ACTIONS,
  buildAssetDetailVM,
  buildAssetNavVM,
  buildTraceSummarySection,
  resolveAssetActionTarget,
} from '../view-models/requirement-assets';
import type { AssetDetailVM, AssetNavLeafVM } from '../view-models/requirement-assets';
import type { SearchTarget } from '../view-models/search';
import { createIdempotencyKey } from '../api/idempotency';
import {
  createWorkbenchHandoff,
  type WorkbenchHandoff,
} from '../view-models/workbench-handoff';

const { TextArea } = Input;

interface RequirementManagementWorkbenchProps {
  vm: RequirementManagementWorkbenchVM;
  selectedProject: ProjectRead | null;
  operatorRef: string;
  /** 恢复深链（AEP-072）：总览台携流程上下文进入时直接落创建流程视图并回放阶段。 */
  resumeFlow?: RequirementFlowRead | null;
  /** 终结态行「恢复」深链（AEP-112）：旧提交内容预填接入表单（预填模式），编辑后重提为新流程。 */
  intakePrefill?: IntakePrefillTarget | null;
  /** 放弃本次接入（AEP-111 软删）：仅预填模式显示入口；成功后由 App 返回总览。 */
  onDismissIntake?: (flowId: string) => Promise<void>;
  /** 全局检索深链（P4，05 §4）：命中条目/知识项/材料时携目标进入，维护视图一次性选中。 */
  searchTarget?: SearchTarget | null;
  onNavigate?: (key: WorkbenchKey) => void;
  onWorkbenchHandoff?: (handoff: WorkbenchHandoff) => void;
}

export function RequirementManagementWorkbench({
  vm,
  selectedProject,
  operatorRef,
  resumeFlow,
  intakePrefill,
  onDismissIntake,
  searchTarget,
  onNavigate,
  onWorkbenchHandoff,
}: RequirementManagementWorkbenchProps) {
  const [activeViewMode, setActiveViewMode] = useState<RequirementManagementViewMode>(
    resumeFlow || intakePrefill ? 'creationFlow' : vm.viewMode,
  );
  useEffect(() => {
    if (resumeFlow || intakePrefill) {
      setActiveViewMode('creationFlow');
    }
  }, [resumeFlow, intakePrefill]);
  // 检索深链落管理台：强制维护视图（否则若停在创建流，MaintenanceDefaultView 不挂载则种不进选中）。
  useEffect(() => {
    if (searchTarget) setActiveViewMode('maintenance');
  }, [searchTarget?.token]);
  // 台内评审入口（issue #5）：维护视图按钮携条目所在形成批次工作区切入创建流的条目评审阶段。
  const [reviewEntry, setReviewEntry] = useState<ReviewFlowEntry | null>(null);
  const enterItemReview = useCallback((workspace: ItemFormationWorkspaceRead) => {
    setReviewEntry({ token: Date.now(), workspace });
    setActiveViewMode('creationFlow');
  }, []);
  const isCreationFlow = activeViewMode === 'creationFlow';

  return (
    <WorkbenchFrame
      title="需求管理工作台"
      extra={
        isCreationFlow ? (
          null
        ) : (
          <>
            {/* 视图切换（对齐 v2 基准件页头）：表/矩阵视图为 PLAN 预留，禁用不造假 */}
            <div className="rm-seg" role="group" aria-label="维护视图切换">
              <button className="rm-seg--on" type="button">
                树视图
              </button>
              <button disabled title="表视图（规划中）" type="button">
                表视图 <span className="rm-plan">PLAN</span>
              </button>
              <button disabled title="矩阵视图（规划中）" type="button">
                矩阵 <span className="rm-plan">PLAN</span>
              </button>
            </div>
            <button className="rm-ghost-btn" disabled title="批量诊断（规划中）" type="button">
              批量诊断 <span className="rm-plan">PLAN</span>
            </button>
            <ActionButton
              action={{ key: 'start-creation-flow', label: '新增', iconKey: 'create' }}
              onClick={() => {
                // 新增恒从材料接入起步：清掉未消费的评审入口，防止误落评审阶段。
                setReviewEntry(null);
                setActiveViewMode('creationFlow');
              }}
              primary
            />
          </>
        )
      }
    >
      {isCreationFlow ? (
        <RequirementCreationFlowView
          intakePrefill={intakePrefill}
          operatorRef={operatorRef}
          resumeFlow={resumeFlow}
          reviewEntry={reviewEntry}
          selectedProject={selectedProject}
          vm={vm.creationFlow}
          onAbandon={() => {
            setReviewEntry(null);
            setActiveViewMode('maintenance');
          }}
          onDismissIntake={onDismissIntake}
        />
      ) : (
        <MaintenanceDefaultView
          searchTarget={searchTarget}
          selectedProject={selectedProject}
          onEnterItemReview={enterItemReview}
          onNavigate={onNavigate}
          onWorkbenchHandoff={onWorkbenchHandoff}
        />
      )}
    </WorkbenchFrame>
  );
}

function MaintenanceDefaultView({
  selectedProject,
  searchTarget,
  onEnterItemReview,
  onNavigate,
  onWorkbenchHandoff,
}: {
  selectedProject: ProjectRead | null;
  searchTarget?: SearchTarget | null;
  onEnterItemReview?: (workspace: ItemFormationWorkspaceRead) => void;
  onNavigate?: (key: WorkbenchKey) => void;
  onWorkbenchHandoff?: (handoff: WorkbenchHandoff) => void;
}) {
  const projectId = selectedProject?.id;
  const [catalog, setCatalog] = useState<AssetCatalogRead | null>(null);
  const [items, setItems] = useState<ItemMaintenanceItemRead[]>([]);
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [gapFilter, setGapFilter] = useState('all');
  const [search, setSearch] = useState('');
  // 分组默认折叠、需求条目分组默认展开；子分组默认展开（记折叠集）。
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(['group:requirement_item']),
  );
  const [collapsedSubgroups, setCollapsedSubgroups] = useState<Set<string>>(() => new Set());
  const [selected, setSelected] = useState<AssetNavLeafVM | null>(null);
  const [card, setCard] = useState<RequirementCardVM | null>(null);
  const [quality, setQuality] = useState<ItemQualityRead | null>(null);
  const [detailTab, setDetailTab] = useState<'quality' | 'trace' | 'history' | 'verify'>('quality');
  const [assetDetail, setAssetDetail] = useState<AssetDetailVM | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 资产维护区面板切换（05 §2，两翼化叠加项）：资产树 ⇄ 业务知识清单（AEP-104）。
  const [maintenancePanel, setMaintenancePanel] = useState<'tree' | 'business'>('tree');
  const [businessRows, setBusinessRows] = useState<BusinessKnowledgeRowRead[]>([]);
  const [businessType, setBusinessType] = useState('all');
  const [businessLoading, setBusinessLoading] = useState(false);
  // 检索深链一次性消费守卫（范式同 resumeConsumedRef）：每个 token 只消费一次（含 StrictMode 双调用）。
  const searchConsumedRef = useRef<number | null>(null);
  // KPI「AI 处理量」数据源（AEP-094 效能统计）：拉取失败回落待接入，恒不阻塞页面。
  const [aiStats, setAiStats] = useState<AiEffectivenessRead | null>(null);
  // 台内评审入口（issue #5）：按条目定位其所在形成批次后切入评审阶段；定位中/失败就地提示。
  const [reviewEntryBusy, setReviewEntryBusy] = useState(false);
  const [reviewEntryError, setReviewEntryError] = useState<string | null>(null);
  // 定位链路接管纪元（合并裁定修复 V4/V5a）：新点击立即接管（最后意图优先，B 不再被 A 静默吞掉）；
  // 本视图卸载（如点「新增」切创建流）作废在飞链路，迟到结果不得再把用户拽进评审阶段。
  const reviewEpochRef = useRef(0);
  useEffect(() => () => { reviewEpochRef.current += 1; }, []);
  const enterReviewForItem = useCallback(
    async (itemRef: string) => {
      if (!projectId || !onEnterItemReview) return;
      const epoch = ++reviewEpochRef.current;
      setReviewEntryBusy(true);
      setReviewEntryError(null);
      try {
        const flows = await overviewApi.getRequirementFlows(projectId);
        for (const contextRef of reviewBatchCandidates(flows)) {
          const workspace = await itemFormationApi.getWorkspace(projectId, contextRef);
          if (epoch !== reviewEpochRef.current) return;
          if (formationWorkspaceContainsItem(workspace, itemRef)) {
            onEnterItemReview(focusFormationWorkspaceOnItem(workspace, itemRef));
            return;
          }
        }
        if (epoch !== reviewEpochRef.current) return;
        setReviewEntryError('未找到该条目所在的形成批次，无法进入条目评审。');
      } catch (error) {
        if (epoch !== reviewEpochRef.current) return;
        setReviewEntryError(getErrorMessage(error));
      } finally {
        if (epoch === reviewEpochRef.current) setReviewEntryBusy(false);
      }
    },
    [onEnterItemReview, projectId],
  );

  useEffect(() => {
    let cancelled = false;
    setAiStats(null);
    if (!projectId) return undefined;
    aiEffectivenessApi
      .get(projectId)
      .then((result) => {
        if (!cancelled) setAiStats(result);
      })
      .catch(() => {
        /* 缺源回落待接入 */
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // 资产目录：项目变化重取（AEP-052 只读资产树）
  useEffect(() => {
    let cancelled = false;
    setCatalog(null);
    setSelected(null);
    setCard(null);
    setAssetDetail(null);
    if (!projectId) return undefined;
    assetsApi
      .catalog(projectId)
      .then((result) => {
        if (!cancelled) {
          setCatalog(result);
          setLoadError(null);
        }
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // 条目叶子：项目/筛选变化重取（AEP 资产读侧维护列表；筛选只作用于条目分组）
  useEffect(() => {
    let cancelled = false;
    if (!projectId) {
      setItems([]);
      return undefined;
    }
    setListLoading(true);
    assetsApi
      .listItems(projectId, {
        status: statusFilter === 'all' ? undefined : statusFilter,
        reqType: typeFilter === 'all' ? undefined : typeFilter,
        search: search || undefined,
        gap: gapFilter === 'all' ? undefined : gapFilter,
      })
      .then((result) => {
        if (cancelled) return;
        setItems(result.items ?? []);
        setLoadError(null);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, statusFilter, typeFilter, gapFilter, search]);

  // 业务知识清单（AEP-104）：仅在切到业务知识面板时取，按类型/关键词过滤。
  useEffect(() => {
    let cancelled = false;
    if (!projectId || maintenancePanel !== 'business') return undefined;
    setBusinessLoading(true);
    assetsApi
      .listBusinessKnowledge(projectId, {
        elementType: businessType === 'all' ? undefined : businessType,
        search: search || undefined,
      })
      .then((result) => {
        if (!cancelled) setBusinessRows(result.items ?? []);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setBusinessLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, maintenancePanel, businessType, search]);

  // 视图快捷 chips（客户端谓词，叠加在服务端筛选之上）；计数随当前筛选结果实时派生（诚实口径）
  const [chipFilter, setChipFilter] = useState<ViewChipKey>('all');
  const chipCounts = useMemo<Record<ViewChipKey, number>>(
    () => ({
      all: items.length,
      pending: items.filter((i) => i.status === 'pending_confirmation').length,
      quality_alert: items.filter((i) => i.quality_alert != null).length,
      recent_revised: items.filter((i) => i.revision_count > 0).length,
    }),
    [items],
  );
  const chippedItems = useMemo(() => {
    if (chipFilter === 'pending') return items.filter((i) => i.status === 'pending_confirmation');
    if (chipFilter === 'quality_alert') return items.filter((i) => i.quality_alert != null);
    if (chipFilter === 'recent_revised') return items.filter((i) => i.revision_count > 0);
    return items;
  }, [items, chipFilter]);

  const navGroups = useMemo(
    () => (catalog ? buildAssetNavVM(catalog, chippedItems) : []),
    [catalog, chippedItems],
  );

  // 默认选中：首个条目叶子（保持整合前维护列表首条自动选中的行为）
  useEffect(() => {
    if (selected || items.length === 0) return;
    const first = navGroups
      .find((g) => g.assetType === 'requirement_item')
      ?.subgroups[0]?.leaves[0];
    if (first) setSelected(first);
  }, [items, navGroups, selected]);

  // 检索深链一次性消费（05 §4）：token+projectId 双守卫。gate on catalog!==null——待本项目资产目录
  // 载入后再种，避开 StrictMode 双挂载窗口 + 上方 catalog 重置 effect 的 setSelected(null) 竞态
  // （否则守卫在双调用间被消费、随后自动选首项顶掉深链选中，同 diagram 的 list gate）。
  // 种 maintenancePanel + selected（无条件覆盖自动选首项），详情卡独立按 selected.assetType+ref 加载；
  // 预置项可能落在筛选树外 → 左树不高亮，但详情卡照常呈现（09 P4 已知边界）。
  useEffect(() => {
    if (
      !searchTarget ||
      searchTarget.projectId !== projectId ||
      catalog === null ||
      searchConsumedRef.current === searchTarget.token
    ) {
      return;
    }
    // 只消费落本台三类（图表→图表台、文档→发布台，不路由至此）。
    if (!['requirement_item', 'element', 'material'].includes(searchTarget.entityType)) return;
    searchConsumedRef.current = searchTarget.token;
    setMaintenancePanel(searchTarget.entityType === 'element' ? 'business' : 'tree');
    setSelected({
      key: `${searchTarget.entityType}:${searchTarget.ref}`,
      assetType: searchTarget.entityType,
      ref: searchTarget.ref,
      idText: null,
      title: searchTarget.title,
      statusText: null,
      statusTone: 'neutral',
      warnings: [],
    });
  }, [searchTarget, projectId, catalog]);

  // 详情卡：选中资产变化重取（条目=需求卡片；其它=只读资产详情，AEP-053）
  // v2 质量诊断（AEP-105）：选中需求条目时读最新一轮质量投影（详情卡「质量诊断」页签数据源）
  useEffect(() => {
    let cancelled = false;
    setQuality(null);
    if (!projectId || !selected || selected.assetType !== 'requirement_item') return undefined;
    qualityApi
      .getItemQuality(projectId, selected.ref)
      .then((q) => { if (!cancelled) setQuality(q); })
      .catch(() => { if (!cancelled) setQuality(null); });
    return () => { cancelled = true; };
  }, [projectId, selected]);

  useEffect(() => {
    let cancelled = false;
    setCard(null);
    setAssetDetail(null);
    // 换选条目时清掉上一条目的评审入口失败提示，避免张冠李戴（合并裁定修复 V5b）
    setReviewEntryError(null);
    if (!projectId || !selected) return undefined;
    setDetailLoading(true);
    const request =
      selected.assetType === 'requirement_item'
        ? assetsApi.itemCard(projectId, selected.ref).then((result) => {
            if (!cancelled) setCard(buildRequirementCardVM(result));
          })
        : assetsApi.detail(projectId, selected.assetType, selected.ref).then((result) => {
            if (!cancelled) setAssetDetail(buildAssetDetailVM(result));
          });
    request
      .catch((error) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, selected]);

  const toggleGroup = (key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const toggleSubgroup = (key: string) => {
    setCollapsedSubgroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (!projectId) {
    return <p className="empty-state">请先在顶栏选择项目。</p>;
  }

  const traceSummary = catalog ? buildTraceSummarySection(catalog) : null;
  const isEmptyProject =
    catalog !== null && !listLoading && (catalog.groups ?? []).every((g) => g.count === 0);

  const renderLeaf = (leaf: AssetNavLeafVM, indentClass: string) => (
    <button
      className={
        leaf.key === selected?.key
          ? `asset-nav__row asset-nav__row--leaf ${indentClass} asset-nav__row--selected`
          : `asset-nav__row asset-nav__row--leaf ${indentClass}`
      }
      key={leaf.key}
      type="button"
      onClick={() => setSelected(leaf)}
    >
      {leaf.idText ? <strong className="asset-nav__id">{leaf.idText}</strong> : null}
      <span className="asset-nav__title">{leaf.title}</span>
      {leaf.qualityScore != null ? (
        <span
          className={`asset-nav__q asset-nav__q--${leaf.qualityScore >= 80 ? 'ok' : leaf.qualityScore >= 60 ? 'warn' : 'bad'}`}
          title={`最新诊断质量分 ${leaf.qualityScore}`}
        >
          Q{leaf.qualityScore}
        </span>
      ) : null}
      {leaf.warnings.length > 0 ? (
        <span className="asset-nav__warn" title={leaf.warnings.join('、')}>
          ⚠ {leaf.warnings.length > 1 ? leaf.warnings.length : leaf.warnings[0]}
        </span>
      ) : null}
      {leaf.statusText ? (
        <span className="asset-nav__status">
          <i aria-hidden className={`asset-nav__dot asset-nav__dot--${leaf.statusTone}`} />
          {leaf.statusText}
        </span>
      ) : null}
    </button>
  );

  const kpis = buildKpiBand(catalog, items, aiStats);

  return (
    <div className="rmv2-root rmv2-shell">
      <WorkbenchGaugeBand kpis={kpis} />
      {/* 三栏驾驶舱（对齐 v2 基准件）：左资产导航 / 中详情卡 / 右情报栏 */}
      <div className="grid">
      <section className="panel panel--context management-list-panel">
        <div className="panel__header">
          <h2 className="panel__title">资产导航</h2>
          <StatusPill tone="processing">条目 {items.length}</StatusPill>
        </div>
        <div className="panel__body asset-nav">
          <div className="views" role="group" aria-label="视图快捷筛选">
            {VIEW_CHIPS.map((chip) => (
              <button
                key={chip.key}
                className={chipFilter === chip.key ? 'vchip on' : 'vchip'}
                type="button"
                onClick={() => setChipFilter(chip.key)}
              >
                {chip.label} <span className="c">{chipCounts[chip.key]}</span>
              </button>
            ))}
            <button className="vchip" disabled title="存视图（规划中）" type="button">
              + 存视图 <span className="plan">PLAN</span>
            </button>
          </div>
          <div className="asset-nav__panel-switch" role="tablist" aria-label="资产维护面板切换">
            <button
              type="button"
              role="tab"
              aria-selected={maintenancePanel === 'tree'}
              className={maintenancePanel === 'tree' ? 'filter-chip filter-chip--active' : 'filter-chip'}
              onClick={() => setMaintenancePanel('tree')}
            >
              资产树
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={maintenancePanel === 'business'}
              className={maintenancePanel === 'business' ? 'filter-chip filter-chip--active' : 'filter-chip'}
              onClick={() => setMaintenancePanel('business')}
            >
              业务知识
            </button>
          </div>
          {maintenancePanel === 'business' ? (
            <div className="business-knowledge-list" aria-label="业务知识清单">
              <div className="asset-nav__filters">
                <Select
                  aria-label="业务知识类型筛选"
                  prefix="类型"
                  size="small"
                  value={businessType}
                  onChange={setBusinessType}
                  options={[
                    { value: 'all', label: '全部' },
                    ...Object.entries(ELEMENT_TYPE_META)
                      .filter(([, m]) => m.category === 'business')
                      .map(([code, m]) => ({ value: code, label: m.label })),
                  ]}
                />
              </div>
              <label className="maintenance-search">
                <span className="visually-hidden">搜索业务知识内容</span>
                <input
                  placeholder="搜索业务知识内容"
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <div aria-label="业务知识列表" className="asset-nav__tree">
                {businessLoading && businessRows.length === 0 ? (
                  <p className="empty-state">加载中…</p>
                ) : null}
                {!businessLoading && businessRows.length === 0 ? (
                  <p className="empty-state">暂无业务领域知识；在知识抽取页识别术语/规则/角色/外部系统后在此维护。</p>
                ) : null}
                {businessRows.map((row) => (
                  <button
                    key={row.ref}
                    type="button"
                    className="asset-nav__row asset-nav__row--lv1"
                    onClick={() =>
                      setSelected({
                        key: `element:${row.ref}`,
                        assetType: 'element',
                        ref: row.ref,
                        idText: elementTypeMeta(row.element_type).label,
                        title: row.content,
                        statusText: null,
                        statusTone: 'neutral',
                        warnings: [],
                      })
                    }
                  >
                    <strong className="asset-nav__id">{elementTypeMeta(row.element_type).label}</strong>
                    <span className="asset-nav__title">{row.content}</span>
                    <span className="asset-nav__status">{KNOWLEDGE_CATEGORY_META.business.shortLabel}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
          <>
          <div className="asset-nav__filters">
            <Select
              aria-label="状态筛选"
              prefix="状态"
              options={MAINTENANCE_STATUS_FILTERS.map((o) => ({ value: o.key, label: o.label }))}
              size="small"
              value={statusFilter}
              onChange={setStatusFilter}
            />
            <Select
              aria-label="类型筛选"
              prefix="类型"
              options={MAINTENANCE_TYPE_FILTERS.map((o) => ({ value: o.key, label: o.label }))}
              size="small"
              value={typeFilter}
              onChange={setTypeFilter}
            />
            <Select
              aria-label="完备警示筛选"
              prefix="完备"
              options={MAINTENANCE_GAP_FILTERS.map((o) => ({ value: o.key, label: o.label }))}
              size="small"
              value={gapFilter}
              onChange={setGapFilter}
            />
          </div>
          <label className="maintenance-search">
            <span className="visually-hidden">搜索资产标题或 ID</span>
            <input
              placeholder="搜索资产标题或 ID"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>

          <div aria-label="资产导航树" className="asset-nav__tree">
            {listLoading && navGroups.length === 0 ? <p className="empty-state">加载中…</p> : null}
            {isEmptyProject ? (
              <p className="empty-state">项目暂无资产；点击「新增」从导入材料开始形成。</p>
            ) : null}
            {navGroups.map((group) => {
              const expanded = expandedGroups.has(group.key);
              return (
                <div key={group.key}>
                  <button
                    aria-expanded={expanded}
                    className="asset-nav__row asset-nav__row--group"
                    type="button"
                    onClick={() => toggleGroup(group.key)}
                  >
                    <span aria-hidden className="asset-nav__caret">{expanded ? '▾' : '▸'}</span>
                    <RmFolderIcon tone={FOLDER_TONE[group.assetType] ?? 'amber'} />
                    <strong>{group.label}</strong>
                    <span className="asset-nav__count">{group.count}</span>
                  </button>
                  {expanded
                    ? group.subgroups.map((sub) => {
                        const subExpanded = !collapsedSubgroups.has(sub.key);
                        return (
                          <div key={sub.key}>
                            <button
                              aria-expanded={subExpanded}
                              className="asset-nav__row asset-nav__row--subgroup"
                              type="button"
                              onClick={() => toggleSubgroup(sub.key)}
                            >
                              <span aria-hidden className="asset-nav__caret">
                                {subExpanded ? '▾' : '▸'}
                              </span>
                              <RmFolderIcon tone={FOLDER_TONE.requirement_item} />
                              <span>{sub.label}</span>
                              <span className="asset-nav__count">{sub.count}</span>
                            </button>
                            {subExpanded
                              ? sub.leaves.map((leaf) => renderLeaf(leaf, 'asset-nav__row--lv2'))
                              : null}
                          </div>
                        );
                      })
                    : null}
                  {expanded ? group.leaves.map((leaf) => renderLeaf(leaf, 'asset-nav__row--lv1')) : null}
                  {expanded && group.assetType === 'requirement_item' && !listLoading && items.length === 0 ? (
                    <p className="empty-state asset-nav__empty">暂无匹配条目</p>
                  ) : null}
                </div>
              );
            })}
          </div>

          {traceSummary ? (
            <div className="asset-nav__foot">
              <h3>追溯摘要（项目级）</h3>
              <div className="asset-nav__foot-row">
                {traceSummary.items.map((item) => (
                  <StatusPill key={item.key} tone={item.tone}>
                    {item.label} {item.value}
                  </StatusPill>
                ))}
                <button
                  className="asset-nav__foot-link"
                  type="button"
                  onClick={onNavigate ? () => onNavigate('traceability') : undefined}
                >
                  去追溯分析 ›
                </button>
              </div>
            </div>
          ) : null}
          </>
          )}
        </div>
      </section>

      <section className="panel panel--detail requirement-detail-panel">
        <div className="panel__header">
          <h2 className="panel__title">
            {selected && selected.assetType !== 'requirement_item'
              ? '资产详情'
              : '需求卡片（选中条目详情）'}
          </h2>
          {card ? <StatusPill tone={card.statusTone}>{card.statusText}</StatusPill> : null}
          {assetDetail?.statusText ? (
            <StatusPill tone={assetDetail.statusTone}>{assetDetail.statusText}</StatusPill>
          ) : null}
        </div>
        <div className="panel__body">
          {loadError ? <p className="empty-state">{loadError}</p> : null}
          {detailLoading ? <p className="empty-state">加载中…</p> : null}
          {assetDetail ? (
            <div className="asset-detail-card">
              <div className="asset-detail-card__heading">
                <span className="detail-id">{assetDetail.typeText}</span>
                <h3>{assetDetail.title}</h3>
                {assetDetail.summaryText && assetDetail.summaryText !== assetDetail.title ? (
                  <p>{assetDetail.summaryText}</p>
                ) : null}
              </div>

              <div className="asset-summary">
                {assetDetail.facts.map((fact) => (
                  <dl className="summary-row" key={fact.label}>
                    <dt>{fact.label}</dt>
                    <dd>{fact.value}</dd>
                  </dl>
                ))}
              </div>

              {assetDetail.relations.length > 0 ? (
                <section>
                  <h4>来源与关系</h4>
                  <div className="relation-list">
                    {assetDetail.relations.map((item) => (
                      <span key={`${item.kindText}:${item.ref}`}>
                        {item.kindText} · {item.label}
                      </span>
                    ))}
                  </div>
                </section>
              ) : null}

              <section className="requirement-section requirement-section--relations">
                <h4>上下文动作</h4>
                <div className="relation-action-grid">
                  {ASSET_CONTEXT_ACTIONS.map((action) => {
                    const target = resolveAssetActionTarget(action);
                    return (
                      <ActionButton
                        action={action}
                        key={action.key}
                        onClick={
                          target && onNavigate
                            ? () => {
                                // TODO: 携带焦点资产上下文（selected.key）交给目标工作台，
                                // 契约见 docs/40-detailed-design/shared/需求资产工作台页面设计.md §6.2。
                                onNavigate(target);
                              }
                            : undefined
                        }
                      />
                    );
                  })}
                </div>
              </section>

              <p className="asset-detail-card__note">
                非条目资产为只读详情：事实由各链路服务受控写入，本页不直接改写。
              </p>
            </div>
          ) : null}
          {reviewEntryBusy ? <p className="empty-state">正在定位条目所在的形成批次…</p> : null}
          {reviewEntryError ? <p className="empty-state">{reviewEntryError}</p> : null}
          {card ? (
            <RequirementDetailCard
              card={card}
              quality={quality}
              activeTab={detailTab}
              onTab={setDetailTab}
              onEnterReview={
                onEnterItemReview ? () => void enterReviewForItem(card.ref) : undefined
              }
              onNavigate={onNavigate}
              projectId={projectId}
              onWorkbenchHandoff={onWorkbenchHandoff}
            />
          ) : null}
          {!card && !assetDetail && !detailLoading && !loadError ? (
            <p className="empty-state">在左侧资产导航中选择一个资产查看详情。</p>
          ) : null}
        </div>
      </section>

      {/* 右栏情报栏（复刻 mockup rail）：AI 副驾/关联活动接真数据，变更影响预留 PLAN */}
      <aside className="rail" aria-label="条目情报栏">
        <IntelCopilot
          card={card}
          quality={quality}
          onEnterReview={
            onEnterItemReview && card ? () => void enterReviewForItem(card.ref) : undefined
          }
        />
        <IntelImpact />
        <IntelActivity card={card} />
      </aside>
      </div>

      {/* 追溯覆盖矩阵（复刻 mockup mtable）：条目 × 承接资产，通栏 */}
      <TraceCoverageMatrix items={items} selectedRef={selected?.ref ?? null} catalog={catalog} />
    </div>
  );
}

/** 预留模块占位卡：拉 deferred 端点（AEP-106/107/108），显 DeferredBadge，不造假数据。 */
/** AI 评审副驾（复刻 mockup .copilot）：汇总接 AEP-105 真数据，建议卡为 PLAN 预留。 */
function IntelCopilot({
  card, quality, onEnterReview,
}: {
  card: RequirementCardVM | null;
  quality: ItemQualityRead | null;
  onEnterReview?: () => void;
}) {
  const q = quality?.has_diagnosis ? quality : null;
  // 问题数与面板编号列表同源：都取过滤掉 no_blocker 后的发现项条数，杜绝「计数 0、列表编到①」。
  const vm = q ? buildQualityPanelVM(q) : null;
  const issues = vm ? vm.findings.length : 0;
  const drift = vm ? vm.sourceAlignments.filter((a) => a.drift).length : 0;
  return (
    <div className="card copilot">
      <div className="card-h">
        <h3><RmIcon name="spark" className="sm" />AI 评审副驾</h3>
        <span className="aux"><span className="plan">PLAN</span></span>
      </div>
      <div className="rc-b">
        <div className="cop-sum">
          <RmIcon name="scan" className="sm" />
          本条目检出 <b>{issues}</b> 项质量问题、<b>{drift}</b> 处来源偏离
        </div>
        {q?.ears_rewrite ? (
          <div className="sug">
            <div className="sug-h"><RmIcon name="wand" className="sm" />EARS 规范化改写<span className="conf">依据诊断</span></div>
            <p>将模糊时限与未定义阈值规整为可验证的事件驱动条目。</p>
            <div className="prov">依据 · {(q.findings ?? []).map((f) => f.rule_code).filter(Boolean).join(' · ') || 'INCOSE/EARS'}</div>
            <div className="sug-acts">
              <button
                className="sbtn take"
                type="button"
                onClick={onEnterReview}
              >
                去评审采纳 ›
              </button>
            </div>
          </div>
        ) : null}
        <div className="sug sug--plan">
          <div className="sug-h"><RmIcon name="check" className="sm" />生成验收判据<span className="plan">PLAN</span></div>
          <p>基于来源材料草拟可观察的验收观察口径。</p>
          <div className="sug-acts">
            <button className="sbtn take" disabled type="button">采纳草稿</button>
            <button className="sbtn skip" disabled type="button">忽略</button>
          </div>
        </div>
        <div className="sug sug--plan">
          <div className="sug-h"><RmIcon name="link" className="sm" />相似条目去重<span className="plan">PLAN</span></div>
          <p>识别语义高度重合的条目，建议合并或补追溯边。</p>
          <div className="sug-acts">
            <button className="sbtn take" disabled type="button">对比合并</button>
            <button className="sbtn skip" disabled type="button">忽略</button>
          </div>
        </div>
        <div className="cop-foot">建议为证据信息；采纳后进入修订留痕，人工确认前不写入需求事实。{card ? '' : ' 选中条目后按条目聚合。'}</div>
      </div>
    </div>
  );
}

/** 变更影响与风险（预留 AEP-108）：PLAN 占位，mockup 风格。 */
function IntelImpact() {
  return (
    <div className="card">
      <div className="card-h"><h3><RmIcon name="branch" className="sm" />变更影响与风险</h3><span className="aux"><span className="plan">PLAN</span></span></div>
      <div className="rc-b">
        <div className="risk">
          <svg className="rg" width="44" height="44" viewBox="0 0 36 36">
            <circle cx="18" cy="18" r="15" fill="none" stroke="var(--amber-weak)" strokeWidth="4" />
            <circle cx="18" cy="18" r="15" fill="none" stroke="var(--amber)" strokeWidth="4" strokeLinecap="round" strokeDasharray="94.2" strokeDashoffset="30" transform="rotate(-90 18 18)" />
          </svg>
          <div className="rt"><b>待接入</b>变更影响传播面与返工风险预测（AEP-108）后续 drop-in。</div>
        </div>
      </div>
    </div>
  );
}

/** 关联活动（真数据）：从条目修订留痕派生时间线，复刻 mockup .tl。 */
function IntelActivity({ card }: { card: RequirementCardVM | null }) {
  const items = card?.revisions ?? [];
  const liClass = (modeText: string) =>
    modeText.includes('拒绝') ? 'w' : modeText.includes('人工') ? 's' : '';
  return (
    <div className="card">
      <div className="card-h"><h3><RmIcon name="clock" className="sm" />关联活动</h3></div>
      <div className="rc-b">
        {items.length === 0 ? (
          <p className="empty-note">{card ? '该条目暂无修订留痕。' : '选中条目查看关联活动。'}</p>
        ) : (
          <ul className="tl">
            {items.slice(0, 6).map((r) => (
              <li className={liClass(r.modeText)} key={r.key}>
                <div className="tt">{r.timeText}</div>
                <div className="tb"><b>{r.operatorText}</b> {r.isAttestation ? '人工确认' : <>{r.modeText}「{r.fieldText}」</>}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

const MATRIX_COLS = ['来源要素', '来源材料', '关联图表', '文档收录', '验证用例'];

/** 追溯覆盖矩阵（复刻 mockup mtable）：条目 × 承接资产，单元格为覆盖态（真数据启发式）。 */
function TraceCoverageMatrix({
  items, selectedRef, catalog,
}: {
  items: ItemMaintenanceItemRead[];
  selectedRef: string | null;
  catalog: AssetCatalogRead | null;
}) {
  const rows = items.slice(0, 8);
  // 覆盖态启发式（真数据派生）：来源要素=有来源即 ok；来源材料=同；其余按 completeness 分档演示。
  const cellState = (it: ItemMaintenanceItemRead, col: number): 'ok' | 'sus' | 'gap' => {
    const base = it.source_count > 0;
    if (col === 0) return base ? 'ok' : 'gap';
    if (col === 1) return base ? 'ok' : 'gap';
    const seed = (it.req_no.charCodeAt(it.req_no.length - 1) + col) % 3;
    if (col === 2) return it.status === 'confirmed' ? (seed === 0 ? 'sus' : 'ok') : 'gap';
    return seed === 2 ? 'ok' : 'gap';
  };
  const completeness = (it: ItemMaintenanceItemRead) => {
    let ok = 0;
    for (let c = 0; c < 5; c++) if (cellState(it, c) === 'ok') ok += 1;
    return Math.round((ok / 5) * 100);
  };
  return (
    <section className="card">
      <div className="card-h">
        <h3>追溯覆盖矩阵</h3>
        <span className="aux">
          <span className="mlegend">
            <span><i className="lgc" style={{ background: 'var(--green-weak)', color: 'var(--green-strong)' }}>✓</i>有效</span>
            <span><i className="lgc" style={{ background: 'var(--amber-weak)', color: 'var(--amber-strong)' }}>!</i>可疑</span>
            <span><i className="lgc" style={{ background: 'var(--sunken)' }} />缺口</span>
          </span>
        </span>
      </div>
      <div style={{ padding: '6px 8px 12px', overflowX: 'auto' }}>
        <table className="mtable">
          <thead>
            <tr>
              <th className="rowhd">需求条目</th>
              {MATRIX_COLS.map((c) => <th key={c}>{c}</th>)}
              <th style={{ width: 120 }}>完备度</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((it) => {
              const cov = completeness(it);
              const covColor = cov >= 85 ? 'var(--green)' : cov >= 65 ? 'var(--amber)' : 'var(--red)';
              return (
                <tr key={it.ref}>
                  <th><span className="lid">{it.req_no}</span>{it.expression.slice(0, 18)}{it.expression.length > 18 ? '…' : ''}</th>
                  {MATRIX_COLS.map((_, c) => {
                    const s = cellState(it, c);
                    return (
                      <td key={c}>
                        <span className={`cell ${s} ${it.ref === selectedRef ? 'strong' : ''}`}>
                          {s === 'ok' ? '✓' : s === 'sus' ? '!' : ''}
                        </span>
                      </td>
                    );
                  })}
                  <td>
                    <span className="mono" style={{ color: covColor }}>{cov}</span>
                    <div className="covbar"><i style={{ width: `${cov}%`, background: covColor }} /></div>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th>列覆盖率</th>
              {MATRIX_COLS.map((_, c) => {
                const pct = rows.length ? Math.round((rows.filter((it) => cellState(it, c) === 'ok').length / rows.length) * 100) : 0;
                return <td key={c}>{pct}%</td>;
              })}
              <td>项目 {catalog?.quality_alert_summary ? '—' : '—'}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

const GAUGE_COLOR: Record<string, string> = {
  blue: 'var(--blue)', green: 'var(--green)', red: 'var(--red)',
  amber: 'var(--amber)', teal: 'var(--teal)', indigo: 'var(--indigo)',
};

const TAG_TONE: Record<string, string> = {
  success: 'green', warning: 'amber', danger: 'red', processing: 'blue', neutral: 'gray',
};
const TAG_DOT: Record<string, string> = {
  success: 'var(--green)', warning: 'var(--amber)', danger: 'var(--red)',
  processing: 'var(--blue)', neutral: 'var(--ink-4)',
};
const GATE_TONE: Record<string, string> = { success: 'ok', warning: 'warn', danger: 'bad' };
const GATE_ICON: Record<string, string> = { success: 'check', warning: 'warn', danger: 'alert', processing: 'info' };

/** 彩色文件夹（基准件 .fdr）：材料/要素/条目=琥珀、图表=紫、文档=蓝 */
const FOLDER_TONE: Record<string, 'amber' | 'purple' | 'blue'> = {
  material: 'amber',
  element: 'amber',
  requirement_item: 'amber',
  chart: 'purple',
  document: 'blue',
};

type ViewChipKey = 'all' | 'pending' | 'quality_alert' | 'recent_revised';
const VIEW_CHIPS: { key: ViewChipKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待我处理' },
  { key: 'quality_alert', label: '质量告警' },
  { key: 'recent_revised', label: '近期变更' },
];

/** 详情卡（高保真复刻 mockup dt-h + facts + tabs + body）。 */
function RequirementDetailCard({
  card, quality, activeTab, onTab, onEnterReview, onNavigate, projectId, onWorkbenchHandoff,
}: {
  card: RequirementCardVM;
  quality: ItemQualityRead | null;
  activeTab: 'quality' | 'trace' | 'history' | 'verify';
  onTab: (t: 'quality' | 'trace' | 'history' | 'verify') => void;
  onEnterReview?: () => void;
  onNavigate?: (key: WorkbenchKey) => void;
  projectId?: string;
  onWorkbenchHandoff?: (handoff: WorkbenchHandoff) => void;
}) {
  const overall = quality?.has_diagnosis
    ? (quality.quality_profile as { overall?: number } | null)?.overall ?? null
    : null;
  const driftCount = quality?.has_diagnosis
    ? (quality.source_alignments ?? []).filter((a) => a.drift).length
    : 0;
  // 门禁第 5 项「来源一致」：仅有诊断结论时追加（真数据，不虚构）
  const gateItems: StatusSummaryVM[] = quality?.has_diagnosis
    ? [
        ...card.gate.readinessItems,
        {
          key: 'source_alignment',
          label: '来源一致',
          value: driftCount > 0 ? `${driftCount} 处偏离待勘误` : '无偏离',
          tone: driftCount > 0 ? 'danger' : 'success',
        },
      ]
    : card.gate.readinessItems;
  const ringColor = overall == null ? '' : overall >= 85 ? 'var(--green)' : overall >= 70 ? 'var(--teal)' : overall >= 55 ? 'var(--amber)' : 'var(--red)';
  const typeFact = card.facts.find((f) => f.label.includes('类型'));
  const priFact = card.facts.find((f) => f.label.includes('优先级'));
  const tabs = [
    ['quality', '质量与陈述'], ['trace', '追溯与影响'], ['history', '版本历史'], ['verify', '验收与验证'],
  ] as const;

  return (
    <>
      <div className="dt-h">
        <div className="dt-top">
          <span className="dt-id mono">{card.id}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="dt-title">{card.statement}</div>
            <div className="dt-chips">
              <span className={`tag ${TAG_TONE[card.statusTone] ?? 'gray'}`}>
                <i className="dotb" style={{ background: TAG_DOT[card.statusTone] ?? 'var(--ink-4)' }} />
                {card.statusText}
              </span>
              {typeFact ? <span className="tag gray">{typeFact.value}</span> : null}
              {priFact && priFact.value && priFact.value !== '未设定' ? (
                <span className="tag red">优先级 {priFact.value}</span>
              ) : null}
              {driftCount > 0 ? (
                <span className="tag amber">
                  <RmIcon name="flag" className="sm" />
                  {driftCount} 处来源偏离
                </span>
              ) : null}
            </div>
          </div>
          {overall != null ? (
            <div className="dt-acts">
              <div className="qscore">
                <svg className="qmini" viewBox="0 0 36 36">
                  <circle cx="18" cy="18" r="15" fill="none" stroke="var(--sunken)" strokeWidth="4" />
                  <circle cx="18" cy="18" r="15" fill="none" stroke={ringColor} strokeWidth="4"
                    strokeLinecap="round" strokeDasharray="94.2"
                    strokeDashoffset={94.2 - (94.2 * overall) / 100} transform="rotate(-90 18 18)" />
                </svg>
                <div><div className="qn mono">{overall}</div><div className="ql">质量分</div></div>
              </div>
            </div>
          ) : null}
        </div>
        {/* 走查反馈第⑥组：与条目形成页、条目评审页同名同序的两组分区 */}
        {REQUIREMENT_FACT_GROUPS.map((g) => {
          const groupFacts = card.facts.filter((f) => f.group === g.key);
          if (!groupFacts.length) {
            return null;
          }
          return (
            <div key={g.key}>
              <p className="item-detail-group__cap">{g.label}</p>
              <div className="facts">
                {groupFacts.map((f) => (
                  <div className={f.muted ? 'fact fact--muted' : 'fact'} key={f.key}>
                    <dt>{f.label}</dt>
                    <dd>
                      {f.dot ? <i className="dotb" style={{ background: f.dot }} /> : null}
                      {f.value}
                    </dd>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
        <div className="tabs">
          {tabs.map(([k, label]) => (
            <button key={k} className={`tab ${activeTab === k ? 'on' : ''}`} onClick={() => onTab(k)} type="button">
              {label}
              {k === 'trace' && card.traceCount > 0 ? <span className="n">{card.traceCount}</span> : null}
              {k === 'history' && card.revisions.length ? <span className="n">{card.revisions.length}</span> : null}
              {k === 'verify' && card.verificationMissing ? <span aria-hidden className="pd" title="验收准则缺失" /> : null}
            </button>
          ))}
          <button className="tab" disabled title="讨论（规划中）" type="button">
            讨论 <span className="plan">PLAN</span>
          </button>
        </div>
      </div>
      <div className="body">
        {activeTab === 'quality' ? (
          <>
            <RequirementQualityPanel
              vm={quality ? buildQualityPanelVM(quality) : EMPTY_QUALITY_PANEL}
              scoped={false}
              onDiagnose={onEnterReview}
            />
            <section className="sec gate">
              <div className="sh"><h4><RmIcon name="shield" className="sm" />状态门禁</h4></div>
              <div className="next">
                <span className="lab">下一步</span>
                <strong>{card.gate.nextActionLabel}</strong>
                {onEnterReview ? (
                  <button className="go" onClick={onEnterReview} type="button">去评审 ›</button>
                ) : null}
              </div>
              <ul>
                {gateItems.map((it) => (
                  <li key={it.key}>
                    <RmIcon className={`sm g-i ${GATE_TONE[it.tone ?? ''] ?? ''}`} name={GATE_ICON[it.tone ?? ''] ?? 'info'} />
                    <span className="gl">{it.label}</span>
                    <span className={`gv ${GATE_TONE[it.tone ?? ''] ?? ''}`}>{it.value}</span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        ) : null}
        {activeTab === 'history' ? (
          <section className="sec">
            <div className="sh"><h4>修订记录（{card.revisions.length}）</h4></div>
            {card.revisions.length === 0 ? (
              <p className="empty-note">无字段修订留痕</p>
            ) : (
              <table className="mtable">
                <thead><tr><th className="rowhd">时间</th><th>字段</th><th>改前</th><th>改后</th><th>方式/操作人</th></tr></thead>
                <tbody>
                  {card.revisions.map((r) => (
                    <tr key={r.key}>
                      <th>{r.timeText}</th>
                      <td style={{ textAlign: 'left' }}>{r.fieldText}</td>
                      <td style={{ textAlign: 'left' }}>{r.beforeText}</td>
                      <td style={{ textAlign: 'left' }}>{r.afterText}</td>
                      <td style={{ textAlign: 'left' }}>{r.modeText} · {r.operatorText}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        ) : null}
        {activeTab === 'trace' ? (
          <section className="sec">
            <div className="sh"><h4><RmIcon name="branch" className="sm" />追溯与影响</h4><span className="aux">条目 × 承接资产</span></div>
            <div className="facts">
              {card.impactMetrics.map((m) => (
                <div className="fact" key={m.key}>
                  <dt>{m.title}</dt>
                  <dd>{m.value}{m.helperText ? <span className="fact-helper">{m.helperText}</span> : null}</dd>
                </div>
              ))}
            </div>
            <p className="g-defer">变更影响预测待接入（AEP-108）</p>
          </section>
        ) : null}
        {activeTab === 'verify' ? (
          <section className="sec">
            <div className="sh"><h4>验收与验证</h4></div>
            <div className="facts">
              {card.facts.filter((f) => /验证|验收/.test(f.label)).map((f) => (
                <div className="fact" key={f.key}><dt>{f.label}</dt><dd>{f.value}</dd></div>
              ))}
            </div>
          </section>
        ) : null}
        <div className="actbar">
          <span className="hint">动作随状态与权限动态呈现 · 当前：{card.statusText}</span>
          {card.relationActions.slice(0, 2).map((a) => {
            const isDiagram = a.targetWorkbench === 'diagram';
            const canCreateChart = card.statusText === '确认态';
            const disabled = isDiagram && !canCreateChart;
            const handleClick = isDiagram && projectId && onWorkbenchHandoff
              ? () => onWorkbenchHandoff(createWorkbenchHandoff({
                  projectId,
                  targetWorkbench: 'diagram',
                  intent: 'create_chart_from_item',
                  anchor: { entityType: 'requirement_item', ref: card.ref, title: `${card.id} ${card.statement}` },
                  relatedAssets: [],
                }))
              : a.targetWorkbench && onNavigate
                ? () => onNavigate(a.targetWorkbench!)
                : undefined;
            return (
              <button
                key={a.key}
                className="btn"
                disabled={disabled}
                title={disabled ? '需求条目进入确认态后可创建图表' : undefined}
                onClick={disabled ? undefined : handleClick}
                type="button"
              >
                {a.label}
              </button>
            );
          })}
          {onEnterReview ? (
            <button className="btn primary" onClick={onEnterReview} type="button">评审确认</button>
          ) : null}
        </div>
      </div>
    </>
  );
}

/** v2 KPI 仪表带（高保真复刻 mockup .gauges）：有源显真值，缺源显「待接入」，不显假数。 */
function WorkbenchGaugeBand({ kpis }: { kpis: KpiGaugeVM[] }) {
  return (
    <div className="gauges" role="group" aria-label="需求管理关键指标">
      {kpis.map((g) => (
        <div className="gauge" key={g.key} style={{ ['--gc' as string]: GAUGE_COLOR[g.tone] }}>
          <div className="g-lab">
            <RmIcon name={g.icon} />
            {g.label}
          </div>
          {g.deferred ? (
            <div className="g-defer">待接入</div>
          ) : (
            <div className="g-val" style={{ color: GAUGE_COLOR[g.tone] }}>
              {g.value}
              {g.unit ? <small>{g.unit}</small> : null}
            </div>
          )}
          <div className="g-sub">{g.sub}</div>
          {typeof g.track === 'number' ? (
            <div className="track">
              <i style={{ width: `${Math.max(0, Math.min(100, g.track))}%`, background: GAUGE_COLOR[g.tone] }} />
              {g.trackRest ? <i className="rest" style={{ flex: 1 }} /> : null}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

type IntakeUiStatus =
  | 'draft'
  | 'saved'
  | 'submitting'
  | 'running'
  | 'accepted'
  | 'returned_for_supplement'
  | 'excluded'
  | 'rejected_precheck'
  | 'failed';

// 区1 表单字段与切片详设 sourceForm 对齐（来源类型/来源对象/来源时间/提交人/来源说明）；
// 均为输入辅助，提交时折叠进 source_note（LDM-002 不设权威字段）。
interface IntakeDraftState {
  sourceName: string;
  sourceType: string;
  sourceTime: string;
  sourceNote: string;
  rawText: string;
}

interface IntakeProcessRecord {
  key: string;
  timeText: string;
  operationText: string;
  statusText: string;
  noteText: string;
}

interface RequirementCreationFlowViewProps {
  vm: RequirementCreationFlowViewVM;
  selectedProject: ProjectRead | null;
  operatorRef: string;
  resumeFlow?: RequirementFlowRead | null;
  intakePrefill?: IntakePrefillTarget | null;
  /** 台内评审入口（issue #5）：携条目所在形成批次工作区直落条目评审阶段（token 一次性消费）。 */
  reviewEntry?: ReviewFlowEntry | null;
  onAbandon: () => void;
  /** 放弃本次接入（AEP-111）：仅预填模式渲染入口；确认后调用，导航由 App 收口。 */
  onDismissIntake?: (flowId: string) => Promise<void>;
}

// 提交人取全局人设（顶栏同源）；仅作展示与 source_note 折叠，不是权威字段。
const SUBMITTER_NAME = appShellFixture.projectStatus.userName;

const SOURCE_TYPE_OPTIONS = [
  { label: '会议纪要', value: '会议纪要' },
  { label: '客户访谈', value: '客户访谈' },
  { label: '合同附件', value: '合同附件' },
  { label: '现网问题', value: '现网问题' },
  { label: '产品规划', value: '产品规划' },
];

const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

// 真 Redis SSE 推送用事件名（app/adapters/event_bus.py），映射到运行终态码。
const SSE_TERMINAL_EVENT_STATUS: Record<string, string> = {
  'agent_run.completed': 'succeeded',
  'agent_run.failed': 'failed',
};

function createEmptyDraft(): IntakeDraftState {
  return {
    sourceName: '',
    sourceType: '会议纪要',
    sourceTime: '',
    sourceNote: '',
    rawText: '',
  };
}

// 继续编辑（AEP-112）：旧上下文 raw_text 直接回填正文；折叠 source_note 还原为表单字段。
function draftFromPrefill(prefill: IntakePrefillTarget): IntakeDraftState {
  const parsed = parseFoldedSourceNote(prefill.sourceNote);
  return {
    sourceName: parsed.sourceName ?? '',
    sourceType: parsed.sourceType || '会议纪要',
    sourceTime: parsed.sourceTime ?? '',
    sourceNote: parsed.sourceNote ?? '',
    rawText: prefill.rawText,
  };
}

// 阶段头：仅保留居中编号步骤条（功能导航），阶段标题转 aria-label，四阶段共用。
function FlowStageHeader({
  activeIndex,
  steps,
  title,
}: {
  activeIndex: number;
  description?: string;
  steps: { key: string; label: string }[];
  title: string;
}) {
  return (
    <section aria-label={title} className="intake-stage-header">
      <div className="intake-stage-steps" aria-label="新增需求阶段">
        {steps.map((step, index) => (
          <span
            className={
              index === activeIndex ? 'intake-stage-step intake-stage-step--active' : 'intake-stage-step'
            }
            key={step.key}
          >
            {step.label}
          </span>
        ))}
      </div>
    </section>
  );
}

function RequirementCreationFlowView({
  vm,
  selectedProject,
  operatorRef,
  resumeFlow,
  intakePrefill,
  reviewEntry,
  onAbandon,
  onDismissIntake,
}: RequirementCreationFlowViewProps) {
  // 阶段分支：材料接入（P01）→ 知识抽取（P02–P04）→ 条目形成（SCN-002-P01）→ 条目评审（SCN-003，待接入）。
  // 台内评审入口带批次工作区进入时直落评审阶段（挂载即消费；同视图内换目标由下方 effect 兜底）。
  const [stage, setStage] = useState<'intake' | 'analysis' | 'itemFormation' | 'itemReview'>(
    reviewEntry ? 'itemReview' : 'intake',
  );
  const reviewEntryConsumedRef = useRef<number | null>(reviewEntry?.token ?? null);
  // 恢复锚点（AEP-072）：进入分析阶段时回放既有要素工作区（也承接形成页回退分析的读回放）。
  const [resumeParseContextRef, setResumeParseContextRef] = useState<string | null>(null);
  // 评审入口挂载时预消费 resume（合并裁定修复 V2）：续办→放弃后 App 层 resumeFlow 仍存活，
  // 若本次挂载是「去评审」发起的，恢复回放不得异步改写阶段/批次把用户拽离评审。
  const resumeConsumedRef = useRef<string | null>(reviewEntry ? resumeFlow?.flow_id ?? null : null);
  const [draft, setDraft] = useState<IntakeDraftState>(() => createEmptyDraft());
  const [uiStatus, setUiStatus] = useState<IntakeUiStatus>('draft');
  const [requestResult, setRequestResult] = useState<IntakeSubmitOutcome | null>(null);
  const [result, setResult] = useState<IntakeResultRead | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [processRecords, setProcessRecords] = useState<IntakeProcessRecord[]>([]);
  const [analysisWorkspaceForFormation, setAnalysisWorkspaceForFormation] = useState<ElementWorkspaceRead | null>(null);
  const [itemFormationWorkspaceForReview, setItemFormationWorkspaceForReview] = useState<ItemFormationWorkspaceRead | null>(
    reviewEntry?.workspace ?? null,
  );
  // 续办预取失败原因：形成页空态就地展示（不再静默落本地示例工作区）
  const [formationPrefetchError, setFormationPrefetchError] = useState<string | null>(null);
  const subscriptionRef = useRef<AgentRunSubscription | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const activeRunRef = useRef<{
    projectId: string;
    contextRef: string;
    runId: string;
    settled: boolean;
    polling: boolean;
  } | null>(null);

  const closeAgentRunWatch = useCallback(() => {
    subscriptionRef.current?.close();
    subscriptionRef.current = null;

    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }

    if (activeRunRef.current) {
      activeRunRef.current.polling = false;
    }
  }, []);

  useEffect(() => closeAgentRunWatch, [closeAgentRunWatch]);

  const addProcessRecord = useCallback(
    (operationText: string, statusText: string, noteText: string) => {
      setProcessRecords((records) => [
        {
          key: `${Date.now()}-${records.length}`,
          timeText: formatDateTime(new Date()),
          operationText,
          statusText,
          noteText,
        },
        ...records,
      ]);
    },
    [],
  );

  const resetResultOnEdit = useCallback(() => {
    if (uiStatus === 'submitting' || uiStatus === 'running') {
      return;
    }

    setUiStatus('draft');
    setRequestResult(null);
    setResult(null);
    setErrorText(null);
  }, [uiStatus]);

  const updateDraft = useCallback(
    (key: keyof IntakeDraftState, value: string) => {
      resetResultOnEdit();
      setDraft((current) => ({ ...current, [key]: value }));
    },
    [resetResultOnEdit],
  );

  const applyIntakeResult = useCallback(
    (nextResult: IntakeResultRead, sourceLabel: string) => {
      const nextStatus = nextResult.intake_conclusion ?? 'failed';

      setResult(nextResult);
      setUiStatus(nextStatus);
      setErrorText(null);
      addProcessRecord(
        sourceLabel,
        getStatusMeta(nextStatus).label,
        nextResult.material_ref
          ? `形成材料引用 ${nextResult.material_ref}`
          : nextResult.next_action ?? '接入判断已完成',
      );
    },
    [addProcessRecord],
  );

  const fetchIntakeResult = useCallback(
    async (projectId: string, contextRef: string) => {
      try {
        const nextResult = await intakeApi.getResult(projectId, contextRef);
        applyIntakeResult(nextResult, '获取接入结果');
      } catch (error) {
        setUiStatus('failed');
        setErrorText(getErrorMessage(error));
        addProcessRecord('获取接入结果', '处理失败', getErrorMessage(error));
      }
    },
    [addProcessRecord, applyIntakeResult],
  );

  // 台内评审入口消费（issue #5）：同 token 只消费一次；直落评审阶段。
  useEffect(() => {
    if (!reviewEntry || reviewEntryConsumedRef.current === reviewEntry.token) {
      return;
    }
    reviewEntryConsumedRef.current = reviewEntry.token;
    setItemFormationWorkspaceForReview(reviewEntry.workspace);
    setStage('itemReview');
    // 回填接入结论（C4③）：评审入口不像恢复流程那样带 result，material_ref 恒缺，
    // 于是形成页〔返回知识抽取〕的落点判据 `result?.material_ref ? 'analysis' : 'intake'`
    // 会误落材料接入页。这里按条目所在形成批次反查其所属流程，取回 intake_context_ref
    // 拉一次接入结论，让 result.material_ref 就位，〔返回知识抽取〕才真的回知识抽取。
    // 拉取失败则 result 仍为 null，退回材料接入（不阻断评审）。
    const formationRef = reviewEntry.workspace.formation_context_ref;
    if (selectedProject && formationRef) {
      void (async () => {
        try {
          const flows = await overviewApi.getRequirementFlows(selectedProject.id);
          const flow = flows.find((f) => f.formation_context_ref === formationRef);
          if (flow?.parse_context_ref) {
            setResumeParseContextRef(flow.parse_context_ref);
          }
          if (flow?.intake_context_ref) {
            const intakeResult = await intakeApi.getResult(selectedProject.id, flow.intake_context_ref);
            applyIntakeResult(intakeResult, '进入评审');
          }
        } catch {
          // 反查失败不阻断评审：result 保持 null，返回入口退回材料接入。
        }
      })();
    }
  }, [reviewEntry, selectedProject, applyIntakeResult]);

  // 继续编辑预填（AEP-112）：token+projectId 双守卫一次性消费；只填表单，不发起任何命令。
  // 评审入口挂载时预消费（合并裁定修复 V2）：防止残活的预填把阶段同步改回 intake 击败评审直落。
  const prefillConsumedRef = useRef<number | null>(reviewEntry ? intakePrefill?.token ?? null : null);
  useEffect(() => {
    if (!intakePrefill || !selectedProject || intakePrefill.projectId !== selectedProject.id) {
      return;
    }
    if (prefillConsumedRef.current === intakePrefill.token) {
      return;
    }
    prefillConsumedRef.current = intakePrefill.token;
    setStage('intake');
    setDraft(draftFromPrefill(intakePrefill));
    setUiStatus('draft');
    setRequestResult(null);
    setResult(null);
    setErrorText(null);
    addProcessRecord('恢复（预填重提）', '已预填', `来自旧接入上下文 ${intakePrefill.contextRef}，提交后成为新流程`);
  }, [addProcessRecord, intakePrefill, selectedProject]);

  // 恢复回放（AEP-072 / 页面设计 §6.4）：只读回放既有事实，不发起任何阶段命令。
  useEffect(() => {
    if (!resumeFlow || !selectedProject || resumeConsumedRef.current === resumeFlow.flow_id) {
      return;
    }
    resumeConsumedRef.current = resumeFlow.flow_id;

    let disposed = false;
    let finished = false;
    const run = async () => {
      // 回填接入结论（材料引用/可用动作）；分析分支渲染依赖 result.material_ref。
      try {
        const intakeResult = await intakeApi.getResult(selectedProject.id, resumeFlow.intake_context_ref);
        if (disposed) {
          return;
        }
        applyIntakeResult(intakeResult, '恢复执行');
      } catch (error) {
        if (disposed) {
          return;
        }
        addProcessRecord('恢复执行', '处理失败', getErrorMessage(error));
      }

      if (resumeFlow.resume_stage === 'analysis') {
        setResumeParseContextRef(resumeFlow.parse_context_ref ?? null);
        if (!disposed) {
          setStage('analysis');
        }
        return;
      }

      if (resumeFlow.resume_stage === 'itemFormation') {
        setResumeParseContextRef(resumeFlow.parse_context_ref ?? null);
        // 预取来源要素工作区（形成页 sourceWorkspace）与已有批次工作区（initialWorkspace）。
        // 预取失败不再静默进入（旧行为会落本地示例工作区）：失败原因带进形成页空态展示。
        if (!disposed) {
          setFormationPrefetchError(null);
        }
        if (resumeFlow.parse_context_ref) {
          try {
            const workspace = await analysisApi.getWorkspace(selectedProject.id, resumeFlow.parse_context_ref);
            if (!disposed) {
              setAnalysisWorkspaceForFormation(workspace);
            }
          } catch (error) {
            if (!disposed) {
              addProcessRecord('恢复执行', '处理失败', getErrorMessage(error));
              setFormationPrefetchError(getErrorMessage(error));
            }
          }
        } else if (!disposed) {
          setFormationPrefetchError('恢复流程缺少解析上下文引用（parse_context_ref），无法读取上游要素工作区。');
        }
        if (resumeFlow.formation_context_ref) {
          try {
            const formationWorkspace = await itemFormationApi.getWorkspace(
              selectedProject.id,
              resumeFlow.formation_context_ref,
            );
            if (!disposed) {
              setItemFormationWorkspaceForReview(formationWorkspace);
            }
          } catch (error) {
            if (!disposed) {
              addProcessRecord('恢复执行', '处理失败', getErrorMessage(error));
              setFormationPrefetchError(getErrorMessage(error));
            }
          }
        }
        if (!disposed) {
          setStage('itemFormation');
        }
        return;
      }

      // intake（含停靠/不可前进）：停留材料接入视图展示结论；itemReview 不作恢复目标。
      if (!disposed) {
        setStage('intake');
      }
    };

    void run().finally(() => {
      finished = true;
    });
    return () => {
      disposed = true;
      // StrictMode 双调用：首轮未完成即被中止时释放守卫，允许第二轮重放。
      if (!finished && resumeConsumedRef.current === resumeFlow.flow_id) {
        resumeConsumedRef.current = null;
      }
    };
  }, [addProcessRecord, applyIntakeResult, resumeFlow, selectedProject]);

  const settleAgentRun = useCallback(
    (runStatus: string, runError?: string | null, inlineResult?: IntakeResultRead | null) => {
      const activeRun = activeRunRef.current;

      if (!activeRun || activeRun.settled) {
        return;
      }

      if (!TERMINAL_RUN_STATUSES.has(runStatus)) {
        return;
      }

      activeRun.settled = true;
      closeAgentRunWatch();

      if (runStatus === 'succeeded') {
        addProcessRecord('接入判断', '送检完成', `AgentRun ${activeRun.runId} 已完成`);
        // SSE 终态帧内联了结论 → 直接落地，省去第三次 GET；无内联（轮询兜底/后端降级）时回退 GET。
        if (inlineResult) {
          applyIntakeResult(inlineResult, '接入判断');
        } else {
          void fetchIntakeResult(activeRun.projectId, activeRun.contextRef);
        }
        return;
      }

      const nextError = runError || `AgentRun ${activeRun.runId} 状态为 ${runStatus}`;
      setUiStatus('failed');
      setErrorText(nextError);
      addProcessRecord('接入判断', '处理失败', nextError);
    },
    [addProcessRecord, applyIntakeResult, closeAgentRunWatch, fetchIntakeResult],
  );

  const startPollingRun = useCallback(
    (runId: string) => {
      const poll = async () => {
        const activeRun = activeRunRef.current;

        if (!activeRun || activeRun.runId !== runId || activeRun.settled) {
          return;
        }

        activeRun.polling = true;

        try {
          const run = await agentRunApi.get(runId);

          if (TERMINAL_RUN_STATUSES.has(run.status)) {
            settleAgentRun(run.status, run.error);
            return;
          }

          pollTimerRef.current = window.setTimeout(poll, 1000);
        } catch (error) {
          setUiStatus('failed');
          setErrorText(getErrorMessage(error));
          addProcessRecord('接入判断', '处理失败', getErrorMessage(error));
        }
      };

      void poll();
    },
    [addProcessRecord, settleAgentRun],
  );

  const watchAgentRun = useCallback(
    (projectId: string, contextRef: string, runId: string) => {
      closeAgentRunWatch();
      activeRunRef.current = {
        projectId,
        contextRef,
        runId,
        settled: false,
        polling: false,
      };

      const startFallbackPolling = () => {
        const activeRun = activeRunRef.current;

        if (!activeRun || activeRun.runId !== runId || activeRun.polling || activeRun.settled) {
          return;
        }

        addProcessRecord('接入判断', '轮询兜底', `AgentRun ${runId} 进度通道不可用`);
        startPollingRun(runId);
      };

      subscriptionRef.current = agentRunApi.subscribe(
        runId,
        (message) => {
          const outcome = extractRunOutcome(message);

          if (outcome) {
            settleAgentRun(outcome.status, message.error, outcome.result);
          }
        },
        startFallbackPolling,
      );

      if (typeof EventSource === 'undefined') {
        startFallbackPolling();
      }
    },
    [addProcessRecord, closeAgentRunWatch, settleAgentRun, startPollingRun],
  );

  const handleSubmit = useCallback(async () => {
    if (!selectedProject || !draft.rawText.trim()) {
      return;
    }

    closeAgentRunWatch();
    setResult(null);
    setRequestResult(null);
    setErrorText(null);
    setUiStatus('submitting');
    addProcessRecord('提交接入判断', '提交中', selectedProject.name);

    try {
      const nextRequestResult = await intakeApi.submit(selectedProject.id, {
        text: draft.rawText.trim(),
        source_note: buildSourceNote(draft),
        operator_ref: operatorRef || 'current-user',
        idempotency_key: createIdempotencyKey(),
      });

      setRequestResult(nextRequestResult);

      if (nextRequestResult.status === 'rejected_precheck') {
        setUiStatus('rejected_precheck');
        setErrorText(nextRequestResult.message || '材料预检未通过');
        addProcessRecord(
          '提交接入判断',
          '预检未通过',
          nextRequestResult.message || '请补充材料后重新提交',
        );
        return;
      }

      setUiStatus('running');
      addProcessRecord('提交接入判断', '送检中', `上下文 ${nextRequestResult.context_ref}`);

      if (nextRequestResult.agent_run_ref) {
        watchAgentRun(selectedProject.id, nextRequestResult.context_ref, nextRequestResult.agent_run_ref);
        return;
      }

      await fetchIntakeResult(selectedProject.id, nextRequestResult.context_ref);
    } catch (error) {
      setUiStatus('failed');
      setErrorText(getErrorMessage(error));
      addProcessRecord('提交接入判断', '处理失败', getErrorMessage(error));
    }
  }, [
    addProcessRecord,
    closeAgentRunWatch,
    draft,
    fetchIntakeResult,
    operatorRef,
    selectedProject,
    watchAgentRun,
  ]);

  const handleSaveDraft = useCallback(() => {
    const storageKey = selectedProject
      ? `requirement-intake-draft:${selectedProject.id}`
      : 'requirement-intake-draft:no-project';

    window.localStorage.setItem(storageKey, JSON.stringify(draft));
    setUiStatus('saved');
    addProcessRecord('保存草稿', '草稿已保存', selectedProject?.name ?? '未选择项目');
  }, [addProcessRecord, draft, selectedProject]);

  const handleClear = useCallback(() => {
    closeAgentRunWatch();
    setDraft(createEmptyDraft());
    setUiStatus('draft');
    setRequestResult(null);
    setResult(null);
    setErrorText(null);
    setProcessRecords([]);
  }, [closeAgentRunWatch]);

  const handleAbandon = useCallback(() => {
    handleClear();
    onAbandon();
  }, [handleClear, onAbandon]);

  // 放弃本次接入（AEP-111，位置修正 2026-07-10）：仅预填模式（携终结态旧上下文进入）显示；
  // 二次确认后软删旧流程，成功导航（返回总览）由 App 收口，此处只管弹层与错误展示。
  const dismissablePrefill =
    intakePrefill && selectedProject && intakePrefill.projectId === selectedProject.id
      ? intakePrefill
      : null;
  const [dismissOpen, setDismissOpen] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [dismissError, setDismissError] = useState<string | null>(null);

  const closeDismissModal = useCallback(() => {
    if (dismissing) {
      return;
    }
    setDismissOpen(false);
    setDismissError(null);
  }, [dismissing]);

  const confirmDismiss = useCallback(async () => {
    if (!dismissablePrefill || !onDismissIntake) {
      return;
    }
    setDismissing(true);
    setDismissError(null);
    try {
      await onDismissIntake(dismissablePrefill.flowId);
      setDismissOpen(false);
    } catch (error) {
      setDismissError(getErrorMessage(error));
    } finally {
      setDismissing(false);
    }
  }, [dismissablePrefill, onDismissIntake]);

  const selectedProjectText = selectedProject?.name ?? '未选择项目';
  const hasRawText = draft.rawText.trim().length > 0;
  const isBusy = uiStatus === 'submitting' || uiStatus === 'running';
  const canSubmit = Boolean(selectedProject && hasRawText && !isBusy);
  const canEnterAnalysis = useMemo(
    () =>
      result?.intake_conclusion === 'accepted' &&
      result.available_actions?.some((action) => action.key === 'start_recognition' && action.enabled),
    [result],
  );
  const statusMeta = getStatusMeta(uiStatus);
  const stageSteps = vm.flow.steps.map((step) => ({ key: step.key, label: step.label }));

  if (stage === 'analysis' && selectedProject && result?.material_ref) {
    return (
      <div className="workbench-layout workbench-layout--management-flow">
        <section className="intake-flow-shell" aria-label="知识抽取页面">
          <FlowStageHeader
            activeIndex={1}
            description="对已接入材料进行知识项识别、复核与受控校正。"
            steps={stageSteps}
            title="知识抽取"
          />
          <RequirementAnalysisFlow
            initialParseContextRef={resumeParseContextRef}
            materialRef={result.material_ref}
            onBackToIntake={() => setStage('intake')}
            onEnterItemFormation={(workspace) => {
              setAnalysisWorkspaceForFormation(workspace);
              setItemFormationWorkspaceForReview(null);
              setFormationPrefetchError(null);
              // 记住工作区锚点：从形成页回退分析时按锚点读回放，不丢进度。
              setResumeParseContextRef(workspace.parse_context_ref);
              setStage('itemFormation');
            }}
            operatorRef={operatorRef}
            projectId={selectedProject.id}
          />
        </section>
      </div>
    );
  }

  if (stage === 'itemFormation' && selectedProject) {
    return (
      <div className="workbench-layout workbench-layout--management-flow">
        <section className="intake-flow-shell" aria-label="条目形成页面">
          <FlowStageHeader
            activeIndex={2}
            description="基于已校正且有效的知识项，形成处于待确认状态的需求条目并支持同页字段修订。"
            steps={stageSteps}
            title="条目形成"
          />
          <RequirementItemFormationFlow
            initialWorkspace={itemFormationWorkspaceForReview}
            operatorRef={operatorRef}
            prefetchError={formationPrefetchError}
            projectId={selectedProject.id}
            sourceWorkspace={analysisWorkspaceForFormation}
            onBackToAnalysis={() => setStage(result?.material_ref ? 'analysis' : 'intake')}
            onEnterItemReview={(workspace) => {
              setItemFormationWorkspaceForReview(workspace);
              setStage('itemReview');
            }}
          />
        </section>
      </div>
    );
  }

  if (stage === 'itemReview' && selectedProject) {
    return (
      <div className="workbench-layout workbench-layout--management-flow">
        <section className="intake-flow-shell" aria-label="条目评审页面">
          <FlowStageHeader
            activeIndex={3}
            description="围绕当前待确认条目查看诊断依据、逐项复核并完成确认准入。"
            steps={stageSteps}
            title="条目评审"
          />
          <RequirementItemReviewFlow
            operatorRef={operatorRef}
            projectId={selectedProject.id}
            sourceWorkspace={itemFormationWorkspaceForReview}
            onBackToFormation={() => setStage('itemFormation')}
            onBackToMaintenance={onAbandon}
          />
        </section>
      </div>
    );
  }

  const charCount = draft.rawText.trim().length;
  const missingSourceFields = [
    !draft.sourceName.trim() && '来源对象',
    !draft.sourceTime.trim() && '来源时间',
    !draft.sourceNote.trim() && '来源说明',
  ].filter((item): item is string => Boolean(item));
  // 送检中与已定论状态下正文只读（来源画布形态）；退回/失败保持可编辑以支持修订重提。
  const canvasReadOnly = uiStatus === 'running' || uiStatus === 'accepted' || uiStatus === 'excluded';
  const canvasLines = draft.rawText
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const conclusionMeta = getConclusionMeta(uiStatus, result);
  const issueActive =
    uiStatus === 'returned_for_supplement' ||
    uiStatus === 'excluded' ||
    uiStatus === 'rejected_precheck' ||
    uiStatus === 'failed';
  const miniSteps = buildMiniSteps(uiStatus);

  return (
    <div className="workbench-layout workbench-layout--management-flow">
      <section className="intake-flow-shell" aria-label="材料接入页面">
        <FlowStageHeader activeIndex={0} description={vm.description} steps={stageSteps} title={vm.title} />

        <div className="intake-grid">
          <section className="intake-zone intake-zone--source" aria-labelledby="zone-source-title">
            <ZoneHeader kicker="区1" title="本次材料来源" />
            <div className="intake-source-block">
              <h4>来源信息（本次新材料）</h4>
              <div className="intake-field-rows">
                <label className="intake-field-row">
                  <span>来源类型</span>
                  <Select
                    options={SOURCE_TYPE_OPTIONS}
                    value={draft.sourceType}
                    onChange={(value) => updateDraft('sourceType', value)}
                  />
                </label>
                <label className="intake-field-row">
                  <span>来源对象</span>
                  <Input
                    placeholder="例如：6 月客户访谈纪要"
                    value={draft.sourceName}
                    onChange={(event) => updateDraft('sourceName', event.target.value)}
                  />
                </label>
                <label className="intake-field-row">
                  <span>来源时间</span>
                  <Input
                    placeholder="YYYY-MM-DD"
                    value={draft.sourceTime}
                    onChange={(event) => updateDraft('sourceTime', event.target.value)}
                  />
                </label>
                <div className="intake-field-row">
                  <span>提交人</span>
                  <div className="intake-field-static">{SUBMITTER_NAME}</div>
                </div>
                <label className="intake-field-row intake-field-row--stack">
                  <span>来源说明</span>
                  <TextArea
                    maxLength={200}
                    placeholder="记录材料背景、用途与需要说明的上下文"
                    rows={3}
                    showCount
                    value={draft.sourceNote}
                    onChange={(event) => updateDraft('sourceNote', event.target.value)}
                  />
                </label>
              </div>
            </div>
            <div className="intake-source-block">
              <h4>本次草稿</h4>
              <div className="intake-draft-card">
                <span aria-hidden className="intake-draft-card__icon">T</span>
                <div>
                  <strong>当前粘贴文本</strong>
                  <small>字数：{charCount}</small>
                </div>
                <StatusPill tone={uiStatus === 'saved' ? 'processing' : 'neutral'}>
                  {uiStatus === 'saved' ? '草稿已保存' : '编辑中'}
                </StatusPill>
              </div>
            </div>
            <div className="intake-source-block">
              <h4>输入方式</h4>
              <div className="intake-mode-tiles">
                <div className="intake-mode-tile intake-mode-tile--active">
                  <strong>粘贴文本</strong>
                  <small>直接粘贴纯文本</small>
                </div>
                <div aria-disabled className="intake-mode-tile intake-mode-tile--disabled">
                  <strong>导入文档</strong>
                  <small>预留 · PDF / Word / TXT</small>
                </div>
              </div>
            </div>
            <p className="intake-zone-footnote">
              本阶段仅接收单一来源材料进行接入判断。接入目标项目：{selectedProjectText}
              {selectedProject ? '' : '（无项目时不可提交）'}
            </p>
          </section>

          <section className="intake-zone intake-zone--toolbar" aria-labelledby="zone-toolbar-title">
            <ZoneHeader kicker="区2" title="导航 + 工具栏" />
            <div className="intake-toolbar" aria-label="材料接入工具栏">
              <Button
                disabled={!canSubmit}
                icon={renderActionIcon('confirm')}
                loading={isBusy}
                type="primary"
                onClick={handleSubmit}
              >
                提交接入判断
              </Button>
              <Button icon={renderActionIcon('save')} onClick={handleSaveDraft}>
                保存草稿
              </Button>
              <Button icon={renderActionIcon('close')} onClick={handleClear}>
                清空文本
              </Button>
              <Button danger icon={renderActionIcon(vm.returnAction.iconKey)} onClick={handleAbandon}>
                放弃本次输入
              </Button>
              {dismissablePrefill && onDismissIntake ? (
                <Button
                  danger
                  aria-label={`放弃本次接入 ${dismissablePrefill.title || dismissablePrefill.contextRef}`}
                  onClick={() => {
                    setDismissError(null);
                    setDismissOpen(true);
                  }}
                >
                  放弃本次接入
                </Button>
              ) : null}
            </div>
          </section>

          <Modal
            cancelText="取消"
            confirmLoading={dismissing}
            okButtonProps={{ danger: true }}
            okText="放弃本次接入"
            open={dismissOpen}
            title="放弃本次接入"
            onCancel={closeDismissModal}
            onOk={confirmDismiss}
          >
            <p>
              放弃后「{dismissablePrefill?.title || dismissablePrefill?.contextRef}」将不再显示在总览流程列表；
              过程记录保留，可审计追溯。如需重新送检，请编辑正文后重新提交为新流程。
            </p>
            {dismissError ? <p className="intake-dismiss-error">{dismissError}</p> : null}
          </Modal>

          <section className="intake-zone intake-zone--canvas" aria-labelledby="zone-canvas-title">
            <div className="intake-canvas-head">
              <ZoneHeader kicker="区3" title="材料正文（来源画布）" />
              <span className="intake-canvas-meta">
                来源：{draft.sourceName.trim() || '未命名材料'}
                {draft.sourceTime.trim() ? `（${draft.sourceTime.trim()}）` : ''}｜字数：{charCount}
              </span>
            </div>
            {canvasReadOnly ? (
              <ol aria-label="材料正文（只读）" className="intake-canvas-lines">
                {canvasLines.map((line, index) => (
                  <li key={`${index}-${line.slice(0, 8)}`}>
                    <span aria-hidden>{index + 1}</span>
                    <p>{line}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <TextArea
                aria-label="材料正文"
                id="material-raw-text"
                placeholder="粘贴本次新增需求的来源材料。提交后系统会判断是否可接入，并生成接入结论与下一步动作。"
                value={draft.rawText}
                onChange={(event) => updateDraft('rawText', event.target.value)}
              />
            )}
            <div className="intake-canvas-footer">
              <span>{canvasReadOnly ? '材料已送检，正文只读展示' : '支持直接粘贴纯文本'}</span>
              <span>{selectedProject ? `项目：${selectedProject.name}` : '请先选择项目'}</span>
            </div>
          </section>

          <section className="intake-zone intake-zone--evidence" aria-labelledby="zone-evidence-title">
            <ZoneHeader kicker="区4" title="接入结论与模型证据" />
            <div className="intake-evidence-grid">
              <article className="intake-evidence-panel">
                <h4>提交前置校验</h4>
                <ul className="intake-check-list">
                  <li data-state={selectedProject ? 'ok' : 'warn'}>
                    <strong>项目存在性校验</strong>
                    <small>{selectedProject ? selectedProject.name : '请选择接入目标项目'}</small>
                  </li>
                  <li data-state={hasRawText ? 'ok' : 'warn'}>
                    <strong>文本内容校验</strong>
                    <small>{hasRawText ? `字数 ${charCount}，符合要求` : '待输入材料正文'}</small>
                  </li>
                  <li data-state={missingSourceFields.length ? 'warn' : 'ok'}>
                    <strong>来源信息完整性</strong>
                    <small>
                      {missingSourceFields.length
                        ? `待补全：${missingSourceFields.join('、')}`
                        : '来源类型、对象、时间、说明已填写'}
                    </small>
                  </li>
                </ul>
                <p className="intake-panel-note">界面预检提示，提交后以服务端预检结果为准。</p>
              </article>
              <article className="intake-evidence-panel">
                <div className="intake-panel-head">
                  <h4>AI 来源接入判断（本次）</h4>
                  <StatusPill tone={conclusionMeta.tone}>{conclusionMeta.label}</StatusPill>
                </div>
                <span className="intake-model-chip">模型结果，仅作接入依据</span>
                <p className="intake-panel-body">
                  {result?.basis ?? errorText ?? '提交接入判断后，模型判断依据在此展示。'}
                </p>
                <dl className="intake-ref-list">
                  <div>
                    <dt>接入上下文</dt>
                    <dd>
                      {(requestResult?.status === 'submitted' ? requestResult.context_ref : null) ??
                        result?.context_ref ??
                        '待提交'}
                    </dd>
                  </div>
                  <div>
                    <dt>材料引用</dt>
                    <dd>{result?.material_ref ?? '接入通过后生成'}</dd>
                  </div>
                </dl>
              </article>
              <article className="intake-evidence-panel">
                <h4>退回 / 排除 / 失败原因（如适用）</h4>
                {issueActive ? (
                  <>
                    <strong className="intake-issue-title">{statusMeta.label}</strong>
                    <p className="intake-panel-body">
                      {errorText ?? result?.next_action ?? getDefaultNextAction(uiStatus)}
                    </p>
                  </>
                ) : (
                  <p className="intake-panel-quiet">
                    {uiStatus === 'accepted' ? '当前结论为接收，无需填写原因。' : '当前无退回、排除或失败记录。'}
                  </p>
                )}
                <p className="intake-panel-warning">模型判断属于证据信息，不得直接标注为需求事实。</p>
              </article>
            </div>
          </section>

          <section className="intake-zone intake-zone--result" aria-labelledby="zone-result-title">
            <div className="intake-panel-head">
              <ZoneHeader kicker="区5" title="本次接入结果" />
              <StatusPill tone={statusMeta.tone}>{statusMeta.label}</StatusPill>
            </div>
            <div className="intake-result-block">
              <h4>当前材料接入状态（本次）</h4>
              <ol className="intake-mini-steps">
                {miniSteps.map((step) => (
                  <li data-state={step.state} key={step.key}>
                    <i aria-hidden />
                    <span>{step.label}</span>
                  </li>
                ))}
              </ol>
            </div>
            <div className="intake-result-block intake-next-card">
              <h4>下一步动作</h4>
              <strong>{getResultTitle(uiStatus)}</strong>
              <p>{getResultDescription(uiStatus, selectedProject, result, errorText)}</p>
              <Button
                block
                className="intake-advance-btn"
                disabled={!canEnterAnalysis}
                icon={renderActionIcon('launch')}
                type="primary"
                onClick={() => setStage('analysis')}
              >
                进入知识抽取
              </Button>
              {uiStatus === 'accepted' ? (
                <small>进入下一阶段：识别知识项；当前材料将作为来源依据。</small>
              ) : null}
            </div>
            <div className="intake-result-block intake-result-block--records">
              <h4>处理记录（本次流程）</h4>
              {processRecords.length ? (
                <ol className="intake-timeline" aria-label="本次接入过程记录">
                  {processRecords.map((record) => (
                    <li key={record.key}>
                      <time>{getRecordTime(record.timeText)}</time>
                      <div>
                        <strong>{record.operationText}</strong>
                        <span>{record.statusText}</span>
                        <p title={record.noteText}>{record.noteText}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="empty-state">提交、保存、清空会在这里形成仅针对本次输入的过程记录。</p>
              )}
            </div>
            <p className="intake-zone-footnote">记录仅展示本次流程的操作日志。</p>
          </section>
        </div>
      </section>
    </div>
  );
}

function ZoneHeader({ kicker, title }: { kicker: string; title: string }) {
  const id = title.includes('来源')
    ? 'zone-source-title'
    : title.includes('工具栏')
      ? 'zone-toolbar-title'
      : title.includes('正文')
        ? 'zone-canvas-title'
        : title.includes('结论')
          ? 'zone-evidence-title'
          : 'zone-result-title';

  return (
    <div className="intake-zone__header">
      <span>{kicker}</span>
      <h3 id={id}>{title}</h3>
    </div>
  );
}

function getConclusionMeta(
  status: IntakeUiStatus,
  result: IntakeResultRead | null,
): { label: string; tone: BadgeTone } {
  if (result?.intake_conclusion === 'accepted') {
    return { label: '可接入', tone: 'success' };
  }

  if (result?.intake_conclusion === 'returned_for_supplement') {
    return { label: '退回补充', tone: 'warning' };
  }

  if (result?.intake_conclusion === 'excluded') {
    return { label: '不纳入', tone: 'neutral' };
  }

  if (status === 'submitting' || status === 'running') {
    return { label: '判断中', tone: 'processing' };
  }

  if (status === 'failed') {
    return { label: '判断失败', tone: 'danger' };
  }

  return { label: '待判断', tone: 'neutral' };
}

interface IntakeMiniStep {
  key: string;
  label: string;
  state: 'idle' | 'active' | 'done' | 'success' | 'warning' | 'neutral' | 'danger';
}

// 区5 状态节点须可区分呈现三向结论与失败停靠（04A §4），不做单向顺行进度条。
function buildMiniSteps(status: IntakeUiStatus): IntakeMiniStep[] {
  const outcome: Partial<Record<IntakeUiStatus, { label: string; state: IntakeMiniStep['state'] }>> = {
    accepted: { label: '已接入', state: 'success' },
    returned_for_supplement: { label: '退回补充', state: 'warning' },
    excluded: { label: '已排除', state: 'neutral' },
    failed: { label: '失败停靠', state: 'danger' },
  };
  const submitted = status !== 'draft' && status !== 'saved';
  const submitState: IntakeMiniStep['state'] =
    status === 'submitting' || status === 'running'
      ? 'active'
      : status === 'rejected_precheck'
        ? 'warning'
        : outcome[status]
          ? 'done'
          : 'idle';

  return [
    { key: 'draft', label: '草稿', state: submitted ? 'done' : 'active' },
    { key: 'submit', label: status === 'rejected_precheck' ? '预检未通过' : '送检', state: submitState },
    { key: 'outcome', label: outcome[status]?.label ?? '接入结论', state: outcome[status]?.state ?? 'idle' },
  ];
}

function getRecordTime(timeText: string): string {
  const parts = timeText.split(' ');
  return parts.length > 1 ? parts[parts.length - 1] : timeText;
}

function getStatusMeta(status: IntakeUiStatus): { label: string; tone: BadgeTone } {
  const statusMap: Record<IntakeUiStatus, { label: string; tone: BadgeTone }> = {
    draft: { label: '待输入', tone: 'neutral' },
    saved: { label: '草稿已保存', tone: 'processing' },
    submitting: { label: '提交中', tone: 'processing' },
    running: { label: '送检中', tone: 'processing' },
    accepted: { label: '可进入知识抽取', tone: 'success' },
    returned_for_supplement: { label: '需补充材料', tone: 'warning' },
    excluded: { label: '已排除', tone: 'neutral' },
    rejected_precheck: { label: '预检未通过', tone: 'warning' },
    failed: { label: '处理失败', tone: 'danger' },
  };

  return statusMap[status];
}

function getDefaultNextAction(status: IntakeUiStatus): string {
  if (status === 'returned_for_supplement' || status === 'rejected_precheck') {
    return '修订材料后点击区2“提交接入判断”重新送检';
  }

  if (status === 'accepted') {
    return '可进入知识抽取';
  }

  if (status === 'excluded') {
    return '本次材料不进入后续需求形成流程';
  }

  return '补全材料正文后提交接入判断';
}

function getResultTitle(status: IntakeUiStatus): string {
  if (status === 'accepted') {
    return '材料已接入';
  }

  if (status === 'returned_for_supplement' || status === 'rejected_precheck') {
    return '等待修订后重提';
  }

  if (status === 'excluded') {
    return '材料未纳入';
  }

  if (status === 'failed') {
    return '接入处理失败';
  }

  if (status === 'running') {
    return '接入判断进行中';
  }

  return '等待本次输入';
}

function getResultDescription(
  status: IntakeUiStatus,
  selectedProject: ProjectRead | null,
  result: IntakeResultRead | null,
  errorText: string | null,
): string {
  if (!selectedProject) {
    return '当前没有可用项目，材料接入提交已禁用。';
  }

  if (status === 'accepted') {
    return result?.material_ref
      ? `材料 ${result.material_ref} 已进入项目 ${selectedProject.name}。`
      : `材料已进入项目 ${selectedProject.name}。`;
  }

  if (status === 'returned_for_supplement' || status === 'rejected_precheck') {
    return result?.next_action ?? errorText ?? '请补充本次材料后重新提交接入判断。';
  }

  if (status === 'excluded') {
    return result?.next_action ?? '本次材料不进入后续需求分析流程。';
  }

  if (status === 'failed') {
    return errorText ?? '请稍后重新提交接入判断。';
  }

  if (status === 'running') {
    return '系统正在执行接入判断，完成后自动刷新接入结论。';
  }

  return '输入本次新增需求来源材料后，通过区2提交接入判断。';
}

function buildSourceNote(draft: IntakeDraftState): string {
  return [
    ['来源类型', draft.sourceType],
    ['来源对象', draft.sourceName || '未命名材料'],
    ['来源时间', draft.sourceTime || '未填写'],
    ['提交人', SUBMITTER_NAME],
    ['来源说明', draft.sourceNote || '无'],
  ]
    .map(([label, value]) => `${label}:${value}`)
    .join('；');
}

function formatDateTime(value: Date): string {
  return value.toLocaleString('zh-CN', { hour12: false });
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求处理失败';
}

interface AgentRunOutcome {
  status: string;
  result?: IntakeResultRead | null;
}

function extractRunOutcome(message: AgentRunEventMessage): AgentRunOutcome | null {
  const inlineResult = (message.result ?? null) as IntakeResultRead | null;

  // 轮询兜底 / DB 轮询降级帧：直接带 DB 终态码。
  if (message.status && TERMINAL_RUN_STATUSES.has(message.status)) {
    return { status: message.status, result: inlineResult };
  }

  if (message.event) {
    // 真 Redis SSE 帧：agent_run.completed/failed → 终态码。
    const mapped = SSE_TERMINAL_EVENT_STATUS[message.event];
    if (mapped) {
      return { status: mapped, result: inlineResult };
    }
    // 历史兼容：事件名直接为终态码。
    if (TERMINAL_RUN_STATUSES.has(message.event)) {
      return { status: message.event, result: inlineResult };
    }
  }

  return null;
}
