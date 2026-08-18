import { Drawer, Input, Modal, Select } from 'antd';
import {
  BarChartOutlined,
  CarryOutOutlined,
  CheckCircleFilled,
  ClockCircleFilled,
  ClockCircleOutlined,
  DatabaseOutlined,
  DotChartOutlined,
  ExclamationCircleFilled,
  FileTextOutlined,
  FolderOpenOutlined,
  InfoCircleOutlined,
  LockOutlined,
  PlusOutlined,
  ProfileOutlined,
  QuestionCircleOutlined,
  RightOutlined,
  SafetyCertificateOutlined,
  ScanOutlined,
  SearchOutlined,
  SettingOutlined,
  ShareAltOutlined,
  UnorderedListOutlined,
  WarningFilled,
  WarningOutlined,
} from '@ant-design/icons';
import { Fragment, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { aiEffectivenessApi } from '../api/ai-effectiveness';
import type { AiDeliveryFailureInstanceRead } from '../api/ai-effectiveness';
import { projectsApi } from '../api/projects';
import type {
  DomainProfileRead,
  ProjectCreateCommand,
  ProjectDetailRead,
  ProjectRead,
} from '../api/projects';
import type { WorkbenchKey } from '../view-models/app-shell';
import {
  DELIVERY_FAILURE_STAGE_LABELS,
  DELIVERY_FAILURE_STAGE_ORDER,
  toSelectedProjectVM,
} from '../view-models/overview';
import type {
  OverviewAiCoverageLegendVM,
  OverviewAiStageMetricVM,
  OverviewAssetMetricVM,
  OverviewBoundaryItemVM,
  OverviewCalibrationVM,
  OverviewChainNodeVM,
  OverviewCoverageMetricVM,
  OverviewDeliveryFailureRowVM,
  OverviewFlowRowVM,
  OverviewRiskSignalVM,
  OverviewSelectedProjectVM,
  OverviewStatMetricVM,
  OverviewTone,
  OverviewTypeBridgeVM,
  OverviewTypeConfirmVM,
  OverviewWorkbenchVM,
} from '../view-models/overview';
import '../styles-overview-chain.css';

const WORKBENCH_LABELS: Record<WorkbenchKey, string> = {
  overview: '总览',
  management: '管理',
  traceability: '追溯',
  diagram: '图表',
  release: '发布',
  settings: '设置',
};

interface OverviewWorkbenchProps {
  vm: OverviewWorkbenchVM;
  selectedProject: ProjectDetailRead | null;
  onNavigate: (key: WorkbenchKey) => void;
  onCreateProject: (command: ProjectCreateCommand) => Promise<ProjectRead>;
  /** 项目列表行点击切换：与顶栏项目选择器同一入口（项目上下文流），不另起状态。 */
  onProjectChange?: (projectId: string) => void;
  /** 恢复深链（AEP-072）：仅导航——携流程上下文进需求管理工作台回放，本页不执行阶段动作。 */
  onResumeFlow?: (flowId: string) => void;
  /** 终结态行「恢复」（AEP-112，位置修正 2026-07-10）：预填旧上下文进材料接入表单，重提为新流程。 */
  onContinueEditFlow?: (flowId: string) => void;
}

export function OverviewWorkbench({
  vm,
  selectedProject,
  onNavigate,
  onCreateProject,
  onProjectChange,
  onResumeFlow,
  onContinueEditFlow,
}: OverviewWorkbenchProps) {
  const selectedProjectView = selectedProject ? toSelectedProjectVM(selectedProject) : vm.selectedProject;
  const activeProjectId = selectedProject?.id ?? vm.selectedProject.id;

  return (
    <section aria-label="项目治理总览台" className="overview-page page-fill">
      <div className="overview-prototype-grid">
        <ProjectManagementPanel
          activeProjectId={activeProjectId}
          project={selectedProjectView}
          vm={vm}
          onNavigate={onNavigate}
          onCreateProject={onCreateProject}
          onProjectChange={onProjectChange}
        />
        <main className="overview-prototype-main">
          <RequirementStatisticsPanel vm={vm} onNavigate={onNavigate} />
          <RequirementFlowsPanel
            flows={vm.flows}
            onContinueEditFlow={onContinueEditFlow}
            onNavigate={onNavigate}
            onResumeFlow={onResumeFlow}
          />
          <AiAnalysisPanel vm={vm} activeProjectId={activeProjectId} onNavigate={onNavigate} />
        </main>
      </div>

      <BoundaryStrip items={vm.boundaryItems} />
    </section>
  );
}

function ProjectManagementPanel({
  activeProjectId,
  project,
  vm,
  onNavigate,
  onCreateProject,
  onProjectChange,
}: {
  activeProjectId: string;
  project: OverviewSelectedProjectVM;
  vm: OverviewWorkbenchVM;
  onNavigate: (key: WorkbenchKey) => void;
  onCreateProject: (command: ProjectCreateCommand) => Promise<ProjectRead>;
  onProjectChange?: (projectId: string) => void;
}) {
  const [searchText, setSearchText] = useState('');
  const filteredProjects = searchText.trim()
    ? vm.projectList.filter((item) => item.name.toLowerCase().includes(searchText.trim().toLowerCase()))
    : vm.projectList;

  return (
    <aside className="overview-prototype-panel overview-project-management-panel">
      <div className="overview-panel-heading">
        <h2>
          左区：项目管理
          <InfoCircleOutlined aria-hidden="true" />
        </h2>
      </div>

      <div className="overview-project-toolbar">
        <strong>项目列表</strong>
        <div className="overview-project-actions" aria-label="项目动作">
          <NewProjectAction onCreateProject={onCreateProject} />
          <button aria-disabled="true" type="button">
            <FolderOpenOutlined aria-hidden="true" />
            归档
          </button>
        </div>
      </div>

      <label className="overview-project-search">
        <SearchOutlined aria-hidden="true" />
        <span className="visually-hidden">搜索项目名称</span>
        <input
          placeholder="搜索项目名称"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
        />
      </label>

      <div className="overview-project-list-card" aria-label="项目列表">
        {filteredProjects.length === 0 ? (
          <p className="overview-project-list-empty">{searchText.trim() ? '无匹配项目' : '暂无项目'}</p>
        ) : (
          filteredProjects.map((item) => {
            const active = item.id === activeProjectId;

            return (
              <button
                aria-current={active ? 'true' : undefined}
                aria-label={active ? `${item.name}（当前项目）` : `切换到项目 ${item.name}`}
                className={active ? 'overview-project-row overview-project-row--active' : 'overview-project-row'}
                key={item.id}
                type="button"
                onClick={() => {
                  if (!active) {
                    onProjectChange?.(item.id);
                  }
                }}
              >
                <span className="overview-project-dot" aria-hidden="true" />
                <strong>{item.name}</strong>
                <span>{active ? '当前项目' : item.dateText}</span>
              </button>
            );
          })
        )}
      </div>

      <section className="overview-selected-project-card">
        <div className="overview-selected-project-card__title">
          <h3>选中项目：{project.name}</h3>
          <button aria-disabled="true" type="button">
            <SettingOutlined aria-hidden="true" />
            设置
          </button>
        </div>
        <dl className="overview-project-facts">
          <div>
            <dt>范围</dt>
            <dd>{project.scope}</dd>
          </div>
          <div>
            <dt>治理目标</dt>
            <dd>{project.goal}</dd>
          </div>
          <div>
            <dt>业务领域</dt>
            <dd>{project.domainProfileLabel}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{project.createdText}</dd>
          </div>
        </dl>
        <p className="overview-project-facts-deferred">
          <DeferredBadge note={project.deferredFacts} />
        </p>
      </section>

      <section className="overview-asset-section">
        <h3>
          资产盘点（项目级）
          <InfoCircleOutlined aria-hidden="true" />
          <span>实时派生</span>
        </h3>
        <div className="overview-asset-grid" data-testid="overview-project-assets">
          {vm.assetMetrics.map((metric) => (
            <AssetMetricButton key={metric.key} metric={metric} onNavigate={onNavigate} />
          ))}
        </div>
      </section>

      <div className="overview-project-note">
        <ProfileOutlined aria-hidden="true" />
        <p>
          <strong>说明：</strong>
          此区管理项目实体，是其它两块的上下文。
          <br />
          项目动作交由 <span className="overview-note-chip">项目上下文服务</span> 写入{' '}
          <span className="overview-note-code">LDM-001</span>。
        </p>
      </div>
    </aside>
  );
}

// 新建项目：总览台仅承接“项目动作”这一写入口（边界例外），提交交由项目上下文服务写 LDM-001。
function NewProjectAction({
  onCreateProject,
}: {
  onCreateProject: (command: ProjectCreateCommand) => Promise<ProjectRead>;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [scope, setScope] = useState('');
  const [background, setBackground] = useState('');
  const [domainProfileKey, setDomainProfileKey] = useState<string>('generic'); // P6b 领域档案
  const [domainProfiles, setDomainProfiles] = useState<DomainProfileRead[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  // P6b：打开表单时拉取领域档案封闭集（AEP-103），失败静默降级为仅「通用」
  useEffect(() => {
    if (!open) return;
    projectsApi.listDomainProfiles().then(setDomainProfiles).catch(() => setDomainProfiles([]));
  }, [open]);

  const reset = () => {
    setName('');
    setScope('');
    setBackground('');
    setDomainProfileKey('generic');
    setErrorText(null);
    setSubmitting(false);
  };

  const close = () => {
    if (submitting) {
      return;
    }

    setOpen(false);
    reset();
  };

  const handleOk = async () => {
    const trimmedName = name.trim();

    if (!trimmedName) {
      setErrorText('请填写项目名称');
      return;
    }

    setSubmitting(true);
    setErrorText(null);

    try {
      await onCreateProject({
        name: trimmedName,
        scope: scope.trim() || null,
        background: background.trim() || null,
        // P6b：generic 视同不指定（后端 None=generic）
        domain_profile_key: domainProfileKey === 'generic' ? null : domainProfileKey,
      });
      setOpen(false);
      reset();
    } catch (error) {
      setSubmitting(false);
      setErrorText(error instanceof Error ? error.message : '创建项目失败，请稍后重试');
    }
  };

  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        <PlusOutlined aria-hidden="true" />
        新建
      </button>
      <Modal
        cancelText="取消"
        confirmLoading={submitting}
        okText="创建"
        open={open}
        title="新建项目"
        onCancel={close}
        onOk={handleOk}
      >
        <div className="overview-create-project-form">
          <label className="overview-create-project-field">
            <span>
              项目名称<em aria-hidden="true">*</em>
            </span>
            <Input
              aria-label="项目名称"
              maxLength={200}
              placeholder="例如：运营效率系统"
              value={name}
              onChange={(event) => {
                setErrorText(null);
                setName(event.target.value);
              }}
              onPressEnter={handleOk}
            />
          </label>
          <label className="overview-create-project-field">
            <span>范围（可选）</span>
            <Input
              aria-label="项目范围"
              placeholder="例如：release-v0.1"
              value={scope}
              onChange={(event) => setScope(event.target.value)}
            />
          </label>
          <label className="overview-create-project-field">
            <span>项目背景（可选）</span>
            <Input.TextArea
              aria-label="项目背景"
              autoSize={{ minRows: 2, maxRows: 4 }}
              placeholder="补充项目背景，可留空"
              value={background}
              onChange={(event) => setBackground(event.target.value)}
            />
          </label>
          <label className="overview-create-project-field">
            <span>业务领域（可选）</span>
            <Select
              aria-label="业务领域"
              value={domainProfileKey}
              onChange={setDomainProfileKey}
              options={(domainProfiles.length
                ? domainProfiles
                : [{ key: 'generic', label: '通用', description: '', version: 1 }]
              ).map((p) => ({ value: p.key, label: p.label }))}
            />
          </label>
          {errorText ? (
            <p className="overview-create-project-error" role="alert">
              {errorText}
            </p>
          ) : null}
        </div>
      </Modal>
    </>
  );
}

function RequirementStatisticsPanel({
  vm,
  onNavigate,
}: {
  vm: OverviewWorkbenchVM;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  // 数字桥默认展示功能类；点类型瓦片切换。五类数据随 overview 一次下发，切换不再请求。
  const [bridgeKey, setBridgeKey] = useState('functional');
  const bridge =
    vm.typeBridges.find((b) => b.key === bridgeKey) ?? vm.typeBridges[0] ?? null;
  const confirmations = new Map(vm.typeConfirmations.map((c) => [c.key, c]));

  return (
    <section className="overview-prototype-panel overview-requirement-prototype-panel">
      <PanelTitle title="主区 · 需求统计" actionText="全部指标说明" />

      {vm.conversionChain ? (
        <section className="ovc-chain-section" data-testid="overview-conversion-chain">
          <span className="overview-group-label">
            需求转化链（识别 → 确认 → 形成 → 条目）·「知识项多、条目少」的原因在此
          </span>
          <ConversionChainStrip nodes={vm.conversionChain} onNavigate={onNavigate} />
        </section>
      ) : null}

      {bridge ? <TypeBridgeBox bridge={bridge} /> : null}

      <section className="overview-type-section">
        <span className="overview-group-label">
          按类型 · 需求类知识项（统计对象是知识项，不是条目；含未确认）
        </span>
        <div className="overview-type-grid" data-testid="overview-type-metrics">
          {vm.requirementTypeMetrics.map((metric) => (
            <TypeMetricButton
              key={metric.key}
              metric={metric}
              confirmation={confirmations.get(metric.key) ?? null}
              selected={bridge !== null && bridge.key === metric.key}
              onSelect={vm.typeBridges.length > 0 ? () => setBridgeKey(metric.key) : undefined}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      </section>

      <div className="overview-stat-groups ovc-stat-groups">
        <section className="overview-group-box" data-testid="overview-status-metrics">
          <span className="overview-group-label">按状态（需求条目）</span>
          <div className="overview-group-tiles overview-group-tiles--g3">
            {vm.requirementStatusMetrics.map((metric) => (
              <CompactMetricButton key={metric.key} metric={metric} onNavigate={onNavigate} />
            ))}
          </div>
          {vm.statusReconciliation ? (
            <p
              className={`ovc-recon${vm.statusReconciliation.balanced ? '' : ' ovc-recon--off'}`}
              data-testid="overview-status-reconciliation"
            >
              <span className="ovc-num">{vm.statusReconciliation.equationText}</span>
              <span className="ovc-recon-result">{vm.statusReconciliation.resultText}</span>
            </p>
          ) : null}
        </section>

        <section className="overview-group-box" data-testid="overview-coverage-metrics">
          <span className="overview-group-label">
            覆盖度（条目 → 来源 / 图表 / 文档）
            {vm.coverageReady ? null : <DeferredBadge note={vm.deferredNote} />}
          </span>
          <div className="overview-group-tiles overview-group-tiles--g3">
            {vm.coverageMetrics.map((metric) => (
              <CoverageDonutButton key={metric.key} metric={metric} onNavigate={onNavigate} />
            ))}
          </div>
        </section>

        <section className="overview-group-box" data-testid="overview-risk-metrics">
          <span className="overview-group-label">
            追溯与风险
            {vm.traceReady ? null : <DeferredBadge note={vm.deferredNote} />}
          </span>
          <div className="overview-group-tiles overview-group-tiles--g3">
            {vm.traceabilityMetrics.map((metric) => (
              <CompactMetricButton key={metric.key} metric={metric} onNavigate={onNavigate} />
            ))}
          </div>
        </section>
      </div>

      <div className="overview-hint-bar">
        <span aria-hidden="true">☝</span>
        提示：以上数字均可点击，跳转到对应工作面进行查看与处理（需求 / 管理 / 追溯）。
      </div>
    </section>
  );
}

// 新增需求流程阶段面板（AEP-072）：只读投影 + 恢复导航（页面设计 §4.4/§6.4）；
// 行动作全量统一为「恢复」（OVW-001 修订 2026-07-10）：终结态行点击＝预填重提（AEP-112），
// 「放弃本次接入」下沉到材料接入页预填模式；死路行（resumable=false 且 dismissable=false）仅可查看。
function RequirementFlowsPanel({
  flows,
  onNavigate,
  onResumeFlow,
  onContinueEditFlow,
}: {
  flows: OverviewFlowRowVM[] | null;
  onNavigate: (key: WorkbenchKey) => void;
  onResumeFlow?: (flowId: string) => void;
  onContinueEditFlow?: (flowId: string) => void;
}) {
  return (
    <section className="overview-prototype-panel overview-flows-panel" data-testid="overview-flows-panel">
      <div className="overview-panel-heading overview-panel-heading--row">
        <h2>
          主区 · 新增需求流程
          <InfoCircleOutlined aria-hidden="true" />
        </h2>
        <span className="overview-flows-note">阶段状态由既有事实源实时派生</span>
      </div>

      {flows === null ? (
        <p className="overview-flows-empty">流程状态加载中…</p>
      ) : flows.length === 0 ? (
        <div className="overview-flows-empty">
          <p>暂无新增需求流程</p>
          <button className="overview-text-link" type="button" onClick={() => onNavigate('management')}>
            去需求管理工作台新增
            <RightOutlined aria-hidden="true" />
          </button>
        </div>
      ) : (
        <div className="overview-flows-list" role="list" aria-label="新增需求流程列表">
          {flows.map((flow) => (
            <div className="overview-flow-row" key={flow.flowId} role="listitem">
              <div className="overview-flow-row__head">
                <strong title={flow.title}>{flow.title}</strong>
                <span className="overview-flow-row__meta">
                  {flow.summary} · 更新 {flow.updatedText}
                </span>
              </div>
              <div className="overview-flow-row__stages" aria-label={`${flow.title} 阶段状态`}>
                {flow.stages.map((stage) => (
                  <span
                    className={`overview-flow-chip overview-tone-${stage.tone}`}
                    key={stage.stage}
                    title={stage.detail ?? undefined}
                  >
                    {stage.label}
                    <em>{stage.statusText}</em>
                  </span>
                ))}
              </div>
              {flow.resumable && onResumeFlow ? (
                <button
                  aria-label={`恢复 ${flow.title}，跳转到需求管理工作台`}
                  className="overview-flow-resume"
                  type="button"
                  onClick={() => onResumeFlow(flow.flowId)}
                >
                  恢复
                  <RightOutlined aria-hidden="true" />
                </button>
              ) : flow.dismissable && onContinueEditFlow ? (
                <button
                  aria-label={`恢复 ${flow.title}，预填后重新提交为新流程`}
                  className="overview-flow-resume"
                  title="预填后重新提交为新流程"
                  type="button"
                  onClick={() => onContinueEditFlow(flow.flowId)}
                >
                  恢复
                  <RightOutlined aria-hidden="true" />
                </button>
              ) : (
                <span className="overview-flow-resume overview-flow-resume--disabled">仅可查看</span>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function DeferredBadge({ note }: { note: string }) {
  return <span className="overview-deferred-badge">{note}</span>;
}

function AiAnalysisPanel({
  vm,
  activeProjectId,
  onNavigate,
}: {
  vm: OverviewWorkbenchVM;
  activeProjectId: string;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <section className="overview-prototype-panel overview-ai-prototype-panel">
      <div className="overview-panel-heading overview-panel-heading--row">
        <h2>
          主区 · AI 效能分析
          <InfoCircleOutlined aria-hidden="true" />
        </h2>
        {vm.aiReady ? null : <DeferredBadge note={vm.deferredNote} />}
        <button className="overview-panel-action" type="button">
          查看更多分析
          <RightOutlined aria-hidden="true" />
        </button>
      </div>

      <div className="overview-ai-analysis-grid">
        <AiStageTable items={vm.aiStageMetrics} onNavigate={onNavigate} />
        <CalibrationChart calibration={vm.aiCalibration} deferredNote={vm.deferredNote} />
        <AiCoverageCard legend={vm.aiCoverageLegend} metric={vm.aiCoverage} onNavigate={onNavigate} />
        <AiRiskList items={vm.aiRiskSignals} onNavigate={onNavigate} />
      </div>

      <DeliveryFailurePanel
        rows={vm.deliveryFailures}
        aiReady={vm.aiReady}
        deferredNote={vm.deferredNote}
        activeProjectId={activeProjectId}
        onNavigate={onNavigate}
      />
    </section>
  );
}

interface DeliveryDrill {
  stage: string;
  laneLabel: string;
  failureStage?: string;
}

// 交付失败率：交付失败＝AI 未能交出合法结论（LDM-015 judgement=*_failed），到不了裁决环节；
// 与「拒绝率」（人工看过合法结论后不买账）是正交维度，独立成带、不排入「按环节效果」（口径 §5.5，A4）。
// 行/命中格可点开个案钻取 Drawer（读侧），接运行态·诊断中心重试/降级跟进（不转问题项）。
function DeliveryFailurePanel({
  rows,
  aiReady,
  deferredNote,
  activeProjectId,
  onNavigate,
}: {
  rows: OverviewDeliveryFailureRowVM[];
  aiReady: boolean;
  deferredNote: string;
  activeProjectId: string;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  const [drill, setDrill] = useState<DeliveryDrill | null>(null);
  return (
    <section className="overview-delivery-failure" data-testid="overview-delivery-failure">
      <div className="overview-delivery-failure-head">
        <h3>
          交付失败率 <span className="overview-muted">（lane × 失败关卡 · 近 30 天）</span>
        </h3>
        <p className="overview-delivery-failure-note">
          交付失败＝AI 未能交出合法结论（到不了裁决环节），与「拒绝率」（人工不采纳）是不同维度。点环节或关卡数可查具体个案。
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="overview-delivery-failure-empty">
          {aiReady ? '窗口内暂无判定记录。' : deferredNote}
        </p>
      ) : (
        <div className="overview-delivery-failure-scroll">
          <div
            className="overview-delivery-failure-table"
            role="table"
            aria-label="交付失败率（lane × 失败关卡）"
          >
            <div className="overview-delivery-failure-row overview-delivery-failure-row--head" role="row">
              <span role="columnheader">环节</span>
              <span role="columnheader">失败率</span>
              <span role="columnheader">失败/判定</span>
              {DELIVERY_FAILURE_STAGE_ORDER.map((stage) => (
                <span key={stage} role="columnheader">
                  {DELIVERY_FAILURE_STAGE_LABELS[stage] ?? stage}
                </span>
              ))}
            </div>
            {rows.map((row) => {
              const canDrill = row.scoreText.split(' / ')[0] !== '0';
              return (
                <div className="overview-delivery-failure-row" key={row.key} role="row">
                  {canDrill ? (
                    <button
                      role="rowheader"
                      className="overview-delivery-lane"
                      type="button"
                      aria-label={`${row.laneLabel} 交付失败个案`}
                      onClick={() => setDrill({ stage: row.key, laneLabel: row.laneLabel })}
                    >
                      {row.laneLabel}
                    </button>
                  ) : (
                    <span role="rowheader">{row.laneLabel}</span>
                  )}
                  <span role="cell">
                    <span className={`overview-level overview-tone-${row.tone}`}>{row.rateText}</span>
                  </span>
                  <span role="cell" className="overview-delivery-score">
                    {row.scoreText}
                  </span>
                  {row.cells.map((cell) =>
                    cell.count > 0 ? (
                      <button
                        role="cell"
                        key={cell.failureStage}
                        className="overview-delivery-cell overview-delivery-cell--hit"
                        type="button"
                        aria-label={`${row.laneLabel} · ${DELIVERY_FAILURE_STAGE_LABELS[cell.failureStage] ?? cell.failureStage} ${cell.count} 例个案`}
                        onClick={() =>
                          setDrill({ stage: row.key, laneLabel: row.laneLabel, failureStage: cell.failureStage })
                        }
                      >
                        {cell.count}
                      </button>
                    ) : (
                      <span role="cell" key={cell.failureStage} className="overview-delivery-cell">
                        ·
                      </span>
                    ),
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      <DeliveryFailureDrawer projectId={activeProjectId} drill={drill} onClose={() => setDrill(null)} />
    </section>
  );
}

const DELIVERY_RUN_STATUS_META: Record<string, { label: string; tone: OverviewTone }> = {
  failed: { label: '运行失败', tone: 'red' },
  queued: { label: '排队中', tone: 'orange' },
  started: { label: '运行中', tone: 'blue' },
  succeeded: { label: '运行完成', tone: 'gray' }, // 运行成功但产出未过守卫＝提示词/产出质量问题
};

// ISO 时间 → "MM-DD HH:mm"（本地时区）；不可解析原样返回。
function formatInstantText(iso: string): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const p = (n: number) => `${n}`.padStart(2, '0');
  return `${p(date.getMonth() + 1)}-${p(date.getDate())} ${p(date.getHours())}:${p(date.getMinutes())}`;
}

// 交付失败个案钻取：读 AEP-094 delivery-failures，接运行态·诊断中心重试/降级（不转问题项）。
function DeliveryFailureDrawer({
  projectId,
  drill,
  onClose,
}: {
  projectId: string;
  drill: DeliveryDrill | null;
  onClose: () => void;
}) {
  const [instances, setInstances] = useState<AiDeliveryFailureInstanceRead[] | null>(null);
  const [totalFailed, setTotalFailed] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!drill) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setInstances(null);
    aiEffectivenessApi
      .deliveryFailureInstances(projectId, drill.stage, {
        failureStage: drill.failureStage,
        limit: 50,
      })
      .then((res) => {
        if (cancelled) return;
        setInstances(res.instances ?? []);
        setTotalFailed(res.total_failed ?? 0);
      })
      .catch(() => {
        if (!cancelled) setError('个案加载失败，请稍后重试。');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, drill]);

  const title = drill
    ? `交付失败个案 · ${drill.laneLabel}${drill.failureStage ? ` · ${DELIVERY_FAILURE_STAGE_LABELS[drill.failureStage] ?? drill.failureStage}` : ''}`
    : '交付失败个案';

  return (
    <Drawer
      title={title}
      open={drill !== null}
      onClose={onClose}
      width={520}
      className="overview-delivery-drawer"
    >
      <p className="overview-delivery-drawer-hint">
        交付失败＝AI 未能交出合法结论（未进裁决环节）。需重试/降级请从右上角
        <strong>运行态徽标 → 诊断中心</strong>跟进；「运行完成」却失败＝产出未过守卫，通常是提示词/产出质量问题。
      </p>
      {loading ? <p className="overview-delivery-drawer-empty">加载中…</p> : null}
      {error ? <p className="overview-delivery-drawer-empty">{error}</p> : null}
      {instances && !loading && !error ? (
        instances.length === 0 ? (
          <p className="overview-delivery-drawer-empty">窗口内暂无该关个案。</p>
        ) : (
          <>
            {totalFailed > instances.length ? (
              <p className="overview-delivery-drawer-count">
                共 {totalFailed} 例，显示最近 {instances.length} 例。
              </p>
            ) : (
              <p className="overview-delivery-drawer-count">共 {instances.length} 例。</p>
            )}
            <ul className="overview-delivery-instance-list">
              {instances.map((inst, idx) => {
                const runMeta = inst.run_status ? DELIVERY_RUN_STATUS_META[inst.run_status] : null;
                return (
                  <li className="overview-delivery-instance" key={idx}>
                    <div className="overview-delivery-instance-top">
                      <span className="overview-delivery-instance-subject">
                        {inst.subject_req_no ? `条目 ${inst.subject_req_no}` : '（未关联条目）'}
                      </span>
                      <span className="overview-delivery-instance-meta">
                        <span className="overview-level overview-tone-gray">
                          {DELIVERY_FAILURE_STAGE_LABELS[inst.failure_stage] ?? inst.failure_stage}
                        </span>
                        {runMeta ? (
                          <span className={`overview-level overview-tone-${runMeta.tone}`}>{runMeta.label}</span>
                        ) : null}
                      </span>
                    </div>
                    <p className="overview-delivery-instance-detail">{inst.detail || '（无详情）'}</p>
                    <time className="overview-delivery-instance-time">{formatInstantText(inst.occurred_at)}</time>
                  </li>
                );
              })}
            </ul>
          </>
        )
      ) : null}
    </Drawer>
  );
}

function PanelTitle({ title, actionText }: { title: string; actionText: string }) {
  return (
    <div className="overview-panel-heading overview-panel-heading--row">
      <h2>
        {title}
        <InfoCircleOutlined aria-hidden="true" />
      </h2>
      <button className="overview-panel-action" type="button">
        {actionText}
        <RightOutlined aria-hidden="true" />
      </button>
    </div>
  );
}

function AssetMetricButton({
  metric,
  onNavigate,
}: {
  metric: OverviewAssetMetricVM;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <button
      aria-label={`${metric.label} ${metric.value}，跳转到${WORKBENCH_LABELS[metric.targetWorkbench]}`}
      className={`overview-asset-card overview-tone-${metric.tone}`}
      onClick={() => onNavigate(metric.targetWorkbench)}
      type="button"
    >
      <span>
        <MetricIcon metricKey={metric.key} tone={metric.tone} />
        {metric.label}
      </span>
      <strong>{metric.value}</strong>
    </button>
  );
}

// 需求转化链条（阶段一 识别 → 阶段二 人工确认 → 阶段三 条目形成 → 产出 需求条目）。
// 1280 窄宽下四节点不换行，由 .ovc-chain-strip 横向内滚承载（节点不砍、页面不横滚）。
function ConversionChainStrip({
  nodes,
  onNavigate,
}: {
  nodes: OverviewChainNodeVM[];
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <div className="ovc-chain-strip">
      {nodes.map((node, index) => (
        <Fragment key={node.key}>
          {index > 0 ? (
            <span className="ovc-chain-arrow" aria-hidden="true">
              <RightOutlined />
            </span>
          ) : null}
          <button
            aria-label={`${node.stageLabel} ${node.title}：${node.value} ${node.valueName}，跳转到${WORKBENCH_LABELS[node.targetWorkbench]}`}
            className="ovc-chain-node"
            data-testid={`overview-chain-node-${node.key}`}
            onClick={() => onNavigate(node.targetWorkbench)}
            type="button"
          >
            <span className="ovc-chain-stage">
              <span className="ovc-chain-step">{node.stageLabel}</span>
              {node.title}
            </span>
            <span className="ovc-chain-main">
              <strong className={`ovc-chain-num ovc-num ovc-tone-${node.valueTone}`}>
                {node.value}
              </strong>
              <span className="ovc-chain-name">{node.valueName}</span>
              {node.counter ? (
                <span className="ovc-chain-counter">
                  {node.counter.label}{' '}
                  <b className={`ovc-num ovc-tone-${node.counter.tone}`}>{node.counter.value}</b>
                </span>
              ) : null}
            </span>
            {node.percent === null ? null : (
              <span className="ovc-bar">
                <i
                  className={`ovc-bar-fill ovc-bar-fill--${node.valueTone}`}
                  style={{ width: `${node.percent}%` }}
                />
              </span>
            )}
            {node.parts.length > 0 || node.progressText ? (
              <span className="ovc-chain-sub">
                {node.progressText ? <span>{node.progressText}</span> : null}
                {node.parts.map((part) => (
                  <span key={part.label}>
                    {part.label}{' '}
                    <b className={`ovc-num${part.tone ? ` ovc-tone-${part.tone}` : ''}`}>
                      {part.value}
                    </b>
                  </span>
                ))}
              </span>
            ) : null}
            {node.gateHint ? <span className="ovc-chain-gate">{node.gateHint}</span> : null}
          </button>
        </Fragment>
      ))}
    </div>
  );
}

// 数字桥：把某一类型「知识项 → 条目」的每一步去向摆成一行账，消除「知识项数 ≠ 条目数」的误读。
function TypeBridgeBox({ bridge }: { bridge: OverviewTypeBridgeVM }) {
  return (
    <section className="ovc-bridge" data-testid="overview-type-bridge">
      <span className="overview-group-label">
        数字桥 · {bridge.label}类：知识项到条目的每一步去向
      </span>
      {bridge.emptyText ? <p className="ovc-bridge-empty">{bridge.emptyText}</p> : null}
      {bridge.rows.length > 0 ? (
        <div className="ovc-bridge-rows ovc-num">
          {bridge.rows.map((row) => (
            <p className="ovc-bridge-row" key={row.key}>
              <span className="ovc-bridge-head">{row.head}</span>
              {row.parts.map((part, index) => (
                <Fragment key={part.text}>
                  <span className="ovc-bridge-op" aria-hidden="true">
                    {index === 0 ? row.operator : '＋'}
                  </span>
                  <span className={part.tone ? `ovc-tone-${part.tone}` : undefined}>
                    {part.text}
                  </span>
                </Fragment>
              ))}
            </p>
          ))}
        </div>
      ) : null}
      {bridge.conclusion ? (
        <p className="ovc-bridge-conclusion">{bridge.conclusion}</p>
      ) : null}
    </section>
  );
}

// 类型瓦片：主体点击＝切换数字桥（选中态高亮），右侧箭头是独立按钮＝跳转工作台。
// 两个动作分开成两个按钮而非嵌套，嵌套会破坏键盘操作与读屏。
function TypeMetricButton({
  metric,
  confirmation,
  selected,
  onSelect,
  onNavigate,
}: {
  metric: OverviewStatMetricVM;
  confirmation: OverviewTypeConfirmVM | null;
  selected: boolean;
  onSelect?: () => void;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  // 没有桥数据（首屏响应未到达、或请求失败）时，瓦片主体退回改动前的导航行为，而不是禁用：
  // 禁用会让整块瓦片既不可点也不可聚焦，只剩右上角 1.5rem 的箭头可用，比改动前退化。
  const bodyLabel = onSelect
    ? `${metric.label}知识项 ${metric.value}，查看该类型的数字桥`
    : `${metric.label} ${metric.value}，跳转到${WORKBENCH_LABELS[metric.targetWorkbench]}`;
  const onBodyClick = onSelect ?? (() => onNavigate(metric.targetWorkbench));
  return (
    <div
      className={`overview-stat-tile ovc-type-tile${selected ? ' ovc-type-tile--selected' : ''}`}
      data-testid={`overview-type-tile-${metric.key}`}
    >
      <button
        aria-label={bodyLabel}
        aria-pressed={onSelect ? selected : undefined}
        className="ovc-type-tile-body"
        onClick={onBodyClick}
        type="button"
      >
        <span className="overview-stat-top">
          <MetricIcon metricKey={metric.key} tone={metric.tone} />
          {metric.label}
        </span>
        <span className="overview-stat-bottom">
          <strong>{metric.value}</strong>
        </span>
        {confirmation ? (
          <>
            <span className="ovc-type-confirm">
              已确认 <b className="ovc-num">{confirmation.confirmedText}</b> · 待确认{' '}
              <b className="ovc-num">{confirmation.pendingText}</b>
            </span>
            <span className="ovc-bar">
              <i
                className={`ovc-bar-fill ovc-bar-fill--tone-${metric.tone}`}
                style={{ width: `${confirmation.percent}%` }}
              />
            </span>
          </>
        ) : null}
      </button>
      <button
        aria-label={`跳转到${WORKBENCH_LABELS[metric.targetWorkbench]}查看${metric.label}知识项`}
        className="ovc-type-tile-go"
        onClick={() => onNavigate(metric.targetWorkbench)}
        type="button"
      >
        <RightOutlined className="overview-arrow" aria-hidden="true" />
      </button>
    </div>
  );
}

function CompactMetricButton({
  metric,
  onNavigate,
}: {
  metric: OverviewStatMetricVM;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <button
      aria-label={`${metric.label} ${metric.value}，跳转到${WORKBENCH_LABELS[metric.targetWorkbench]}`}
      className="overview-mini-tile"
      onClick={() => onNavigate(metric.targetWorkbench)}
      type="button"
    >
      <span className="overview-mini-label">{metric.label}</span>
      <span className="overview-mini-row">
        <span className="overview-mini-left">
          <MetricIcon metricKey={metric.key} tone={metric.tone} compact />
          <strong>{metric.value}</strong>
        </span>
        <RightOutlined className="overview-arrow" aria-hidden="true" />
      </span>
    </button>
  );
}

function CoverageDonutButton({
  metric,
  onNavigate,
}: {
  metric: OverviewCoverageMetricVM;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <button
      aria-label={`${metric.label} ${metric.value}，跳转到${WORKBENCH_LABELS[metric.targetWorkbench]}`}
      className="overview-mini-tile"
      onClick={() => onNavigate(metric.targetWorkbench)}
      type="button"
    >
      <span className="overview-mini-label">{metric.label}</span>
      <span className="overview-mini-row">
        <span
          className={`overview-ring overview-ring--${metric.tone}`}
          style={{ '--value': `${metric.percent}%` } as CSSProperties & Record<'--value', string>}
        >
          <strong>{metric.value}</strong>
        </span>
        <RightOutlined className="overview-arrow" aria-hidden="true" />
      </span>
    </button>
  );
}

function AiStageTable({
  items,
  onNavigate,
}: {
  items: OverviewAiStageMetricVM[];
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <section className="overview-ai-card overview-ai-stage-card" data-testid="overview-ai-stage-table">
      <h3>
        按环节效果 <span className="overview-muted">（近 30 天）</span>
      </h3>
      <div className="overview-ai-stage-table" role="table" aria-label="AI 环节统计">
        <div className="overview-ai-stage-row overview-ai-stage-row--head" role="row">
          <span role="columnheader">环节</span>
          <span role="columnheader">采纳</span>
          <span role="columnheader">修订采纳</span>
          <span role="columnheader">拒绝</span>
          <span role="columnheader">转问题项</span>
          <span aria-hidden="true" />
        </div>
        {items.map((item) => (
          <div
            className={item.insufficient ? 'overview-ai-stage-row overview-ai-stage-row--insufficient' : 'overview-ai-stage-row'}
            key={item.key}
            role="row"
            title={item.insufficient ? '样本不足（收口明细 <5）' : undefined}
          >
            <button
              aria-label={`${item.stage} AI 效能，跳转到${WORKBENCH_LABELS[item.targetWorkbench]}`}
              onClick={() => onNavigate(item.targetWorkbench)}
              role="cell"
              type="button"
            >
              {item.stage}
            </button>
            <span role="cell">{item.accepted}</span>
            <span role="cell">{item.revised}</span>
            <span role="cell">{item.rejected}</span>
            <span role="cell">{item.issue}</span>
            <span aria-hidden="true">
              <RightOutlined className="overview-arrow" />
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

// 置信度校准：样本=识别明细×要素置信度（AEP-094，口径设计 §5.2）；无数据只画坐标轴，不绘虚构曲线。
function CalibrationChart({
  calibration,
  deferredNote,
}: {
  calibration: OverviewCalibrationVM | null;
  deferredNote: string;
}) {
  const points = calibration?.points ?? [];
  const toX = (pct: number) => 46 + (pct / 100) * 172;
  const toY = (pct: number) => 122 - (pct / 100) * 104;
  return (
    <section className="overview-ai-card overview-calibration-card">
      <h3>置信度校准</h3>
      <p className="overview-ece-line">
        ECE：<strong>{calibration?.eceText ?? '—'}</strong>
        {calibration ? (
          <span className="overview-muted">（{calibration.ratingText} · {calibration.sampleText}）</span>
        ) : null}
      </p>
      <svg aria-label="置信度校准" className="overview-calibration-chart" viewBox="0 0 230 150">
        <g className="overview-chart-grid-lines">
          <line className="overview-chart-grid" x1="46" x2="218" y1="18" y2="18" />
          <line className="overview-chart-grid" x1="46" x2="218" y1="70" y2="70" />
        </g>
        <line x1="46" x2="46" y1="18" y2="122" />
        <line x1="46" x2="218" y1="122" y2="122" />
        <line className="overview-chart-ideal" x1="46" x2="218" y1="122" y2="18" />
        <text textAnchor="end" x="40" y="21.5">100%</text>
        <text textAnchor="end" x="40" y="73.5">50%</text>
        <text textAnchor="end" x="40" y="125.5">0%</text>
        <text textAnchor="middle" x="46" y="136">0%</text>
        <text textAnchor="middle" x="132" y="136">50%</text>
        <text textAnchor="middle" x="218" y="136">100%</text>
        <text textAnchor="middle" x="132" y="148">模型置信度</text>
        {points.length > 1 ? (
          <polyline
            className="overview-chart-model-line"
            fill="none"
            points={points.map((pt) => `${toX(pt.x)},${toY(pt.y)}`).join(' ')}
          />
        ) : null}
        {points.map((pt) => (
          <circle
            className="overview-chart-model-dot"
            cx={toX(pt.x)}
            cy={toY(pt.y)}
            key={`${pt.x}-${pt.y}`}
            r={3}
          >
            <title>{`置信度 ${pt.x}% → 采纳率 ${pt.y}%（${pt.count} 样本）`}</title>
          </circle>
        ))}
        {points.length === 0 ? (
          <text className="overview-chart-deferred" textAnchor="middle" x="120" y="62">
            {calibration ? '样本不足' : deferredNote}
          </text>
        ) : null}
      </svg>
      <div className="overview-chart-legend">
        <span>
          <span className="overview-legend-swatch" aria-hidden="true" />
          模型输出
        </span>
        <span>
          <span className="overview-legend-swatch overview-legend-swatch--ideal" aria-hidden="true" />
          理想校准线
        </span>
      </div>
    </section>
  );
}

function AiCoverageCard({
  metric,
  legend,
  onNavigate,
}: {
  metric: OverviewCoverageMetricVM;
  legend: OverviewAiCoverageLegendVM;
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <section className="overview-ai-card overview-ai-coverage-card">
      <h3>
        AI 覆盖 <span className="overview-muted">（近 30 天）</span>
      </h3>
      <div className="overview-cover-wrap">
        <button
          aria-label={`${metric.label} ${metric.value}，跳转到${WORKBENCH_LABELS[metric.targetWorkbench]}`}
          className="overview-ai-coverage-button"
          onClick={() => onNavigate(metric.targetWorkbench)}
          type="button"
        >
          <span
            className="overview-donut"
            style={{ '--value': `${metric.percent}%` } as CSSProperties & Record<'--value', string>}
          >
            <strong>{metric.value}</strong>
          </span>
        </button>
        <ul className="overview-cover-legend">
          <li>
            <span className="overview-legend-dot overview-legend-dot--blue" />
            AI 触达条目 <strong>{legend.touched}</strong>
          </li>
          <li>
            <span className="overview-legend-dot overview-legend-dot--gray" />
            未触达条目 <strong>{legend.untouched}</strong>
          </li>
          <li>
            <span className="overview-legend-dot overview-legend-dot--light" />
            暂不适用 <strong>{legend.notApplicable}</strong>
          </li>
        </ul>
      </div>
      <p className="overview-cover-total">
        总条目：<strong>{legend.total}</strong>
      </p>
    </section>
  );
}

function AiRiskList({
  items,
  onNavigate,
}: {
  items: OverviewRiskSignalVM[];
  onNavigate: (key: WorkbenchKey) => void;
}) {
  return (
    <section className="overview-ai-card overview-ai-risk-card">
      <h3>风险信号</h3>
      <div className="overview-ai-risk-list" data-testid="overview-ai-risk-metrics">
        {items.map((item) => (
          <button
            aria-label={`${item.label} ${item.value}，跳转到${WORKBENCH_LABELS[item.targetWorkbench]}`}
            className="overview-ai-risk-row"
            key={item.key}
            onClick={() => onNavigate(item.targetWorkbench)}
            type="button"
          >
            <span>{item.label}</span>
            <em className={`overview-level overview-tone-${item.levelTone}`}>{item.level}</em>
            <strong>{item.value}</strong>
          </button>
        ))}
      </div>
      <button className="overview-text-link overview-text-link--center" type="button">
        查看全部风险
        <RightOutlined aria-hidden="true" />
      </button>
    </section>
  );
}

function BoundaryStrip({ items }: { items: OverviewBoundaryItemVM[] }) {
  return (
    <section className="overview-boundary-prototype-strip" aria-label="总览台边界">
      <div className="overview-boundary-primary">
        <span>
          <SafetyCertificateOutlined aria-hidden="true" />
        </span>
        <div>
          <strong>边界：总览台只读聚合 + 导航（项目动作除外）</strong>
          <p>总览台不持有第二份事实源 · 不就地处理待办 · 不碰门禁</p>
        </div>
      </div>
      {items.map((item) => (
        <div className="overview-boundary-item" key={item.key}>
          <MetricIcon metricKey={item.key} tone={item.tone} compact />
          <div>
            <strong>{item.title}</strong>
            <p>{item.description}</p>
          </div>
        </div>
      ))}
    </section>
  );
}

function MetricIcon({
  metricKey,
  tone,
  compact = false,
}: {
  metricKey: string;
  tone: OverviewTone;
  compact?: boolean;
}) {
  const icon = metricIcon(metricKey);
  const className = compact
    ? `overview-metric-icon overview-metric-icon--${tone} overview-metric-icon--compact`
    : `overview-metric-icon overview-metric-icon--${tone}`;

  return <span className={className}>{icon}</span>;
}

const METRIC_ICONS: Record<string, ReactNode> = {
  // 资产盘点（项目级）——浅色底 + 同色图标（原型 a-ico）
  materials: <ProfileOutlined aria-hidden="true" />,
  requirements: <UnorderedListOutlined aria-hidden="true" />,
  diagrams: <BarChartOutlined aria-hidden="true" />,
  documents: <FileTextOutlined aria-hidden="true" />,
  issues: <WarningOutlined aria-hidden="true" />,
  // 按类型（知识项）——浅色底 + 同色描边图标
  functional: <CarryOutOutlined aria-hidden="true" />,
  quality: <SafetyCertificateOutlined aria-hidden="true" />,
  constraint: <LockOutlined aria-hidden="true" />,
  data: <DatabaseOutlined aria-hidden="true" />,
  interface: <ShareAltOutlined aria-hidden="true" />,
  // 按状态（需求条目）
  confirmed: <CheckCircleFilled aria-hidden="true" />,
  pending: <ClockCircleFilled aria-hidden="true" />,
  // 追溯与风险
  'trace-gap': <WarningFilled aria-hidden="true" />,
  'suspicious-links': <QuestionCircleOutlined aria-hidden="true" />,
  'issue-items': <ExclamationCircleFilled aria-hidden="true" />,
  // 边界条
  'requirement-status': <ScanOutlined aria-hidden="true" />,
  'coverage-gap': <ShareAltOutlined aria-hidden="true" />,
  'ai-analysis': <DotChartOutlined aria-hidden="true" />,
};

function metricIcon(metricKey: string): ReactNode {
  return METRIC_ICONS[metricKey] ?? <ClockCircleOutlined aria-hidden="true" />;
}
