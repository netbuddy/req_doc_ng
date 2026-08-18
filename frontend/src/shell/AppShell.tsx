import { Avatar, Button, Input, Select, Tooltip } from 'antd';
import { useEffect, useState } from 'react';
import { CommandPalette } from './CommandPalette';
import type { SearchHitRead } from '../api/search';
import type { SearchTarget } from '../view-models/search';
import { NotificationBell } from './NotificationBell';
import { RuntimeStatusBadge } from './RuntimeStatusBadge';
import { OverviewWorkbench } from '../workbenches/OverviewWorkbench';
import { RequirementManagementWorkbench } from '../workbenches/RequirementManagementWorkbench';
import { PublicationWorkbench } from '../workbenches/PublicationWorkbench';
import { DiagramWorkbench } from '../workbenches/DiagramWorkbench';
import { SettingsWorkbench } from '../workbenches/SettingsWorkbench';
import { TraceabilityWorkbench } from '../workbenches/TraceabilityWorkbench';
import { requirementManagementWorkbenchFixture } from '../fixtures/requirement-management';
import {
  QuestionCircleOutlined,
  SearchOutlined,
  renderNavigationIcon,
  DownOutlined,
  ArrowLeftOutlined,
} from '../ui/icons';
import type { ProjectCreateCommand, ProjectDetailRead, ProjectRead } from '../api/projects';
import type { RequirementFlowRead } from '../api/overview';
import type { AppShellVM, WorkbenchKey } from '../view-models/app-shell';
import type { OverviewWorkbenchVM } from '../view-models/overview';
import type { IntakePrefillTarget } from '../view-models/requirement-management';
import type { SettingsDomainKey } from '../view-models/settings';
import type { WorkbenchHandoff } from '../view-models/workbench-handoff';

interface AppShellProps {
  vm: AppShellVM;
  overviewVM: OverviewWorkbenchVM;
  resumeFlow: RequirementFlowRead | null;
  /** 终结态行「恢复」深链（AEP-112）：预填旧提交内容进材料接入表单（预填模式）。 */
  intakePrefill: IntakePrefillTarget | null;
  searchTarget: SearchTarget | null;
  workbenchHandoff: WorkbenchHandoff | null;
  selectedProject: ProjectDetailRead | null;
  settingsInitialDomain?: SettingsDomainKey;
  onNavigate: (key: WorkbenchKey) => void;
  onWorkbenchHandoff: (handoff: WorkbenchHandoff) => void;
  onConsumeWorkbenchHandoff: (token: number) => void;
  onOpenSettingsDomain: (domain: SettingsDomainKey) => void;
  onProjectChange: (projectId: string) => void;
  onCreateProject: (command: ProjectCreateCommand) => Promise<ProjectRead>;
  onResumeFlow: (flowId: string) => void;
  onContinueEditFlow: (flowId: string) => void;
  /** 放弃本次接入（AEP-111）：入口在材料接入页预填模式，成功后由 App 返回总览。 */
  onDismissIntake: (flowId: string) => Promise<void>;
  onSearchNavigate: (hit: SearchHitRead) => void;
  /** 项目删除成功（AEP-113）：App 刷新项目列表并切换到剩余项目/空态。 */
  onProjectDeleted: (deletedProjectId: string) => Promise<void>;
}

export function AppShell({
  vm,
  overviewVM,
  resumeFlow,
  intakePrefill,
  searchTarget,
  workbenchHandoff,
  selectedProject,
  settingsInitialDomain,
  onNavigate,
  onWorkbenchHandoff,
  onConsumeWorkbenchHandoff,
  onOpenSettingsDomain,
  onProjectChange,
  onCreateProject,
  onResumeFlow,
  onContinueEditFlow,
  onDismissIntake,
  onSearchNavigate,
  onProjectDeleted,
}: AppShellProps) {
  const [paletteOpen, setPaletteOpen] = useState(false);

  // ⌘K / Ctrl+K 全局唤起命令面板（要求修饰键，故他处输入框内单键不误触发，05 §2.1）。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="app-shell">
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={(hit) => {
          setPaletteOpen(false);
          onSearchNavigate(hit);
        }}
      />
      <header className="top-bar">
        <div className="top-bar__brand">
          <svg aria-hidden="true" className="brand-mark" viewBox="0 0 40 40">
            <path d="M20 3 L34.5 11.5 V28.5 L20 37 L5.5 28.5 V11.5 Z" fill="#1677ff" />
            <path
              d="M20 11 L27 15 V23 L20 27 L13 23 V15 Z"
              fill="none"
              stroke="#fff"
              strokeLinejoin="round"
              strokeWidth="2.2"
            />
            <path
              d="M13 15 L20 19 L27 15 M20 19 V27"
              fill="none"
              stroke="#fff"
              strokeLinejoin="round"
              strokeWidth="2.2"
            />
          </svg>
          <div className="brand-copy">
            <span className="brand-title">睿析</span>
            <span className="brand-subtitle">智能化需求治理平台</span>
          </div>
        </div>

        <Select
          aria-label="当前项目"
          className="project-switcher"
          disabled={vm.projectSelectorStatus !== 'ready'}
          loading={vm.projectSelectorStatus === 'loading'}
          notFoundContent="暂无项目"
          options={vm.projectOptions.map((project) => ({
            label: project.name,
            value: project.id,
          }))}
          placeholder={vm.projectSelectorText}
          value={vm.selectedProjectId}
          onChange={onProjectChange}
        />

        {/* 只读触发器：点击或 ⌘K 唤起命令面板（不再是无绑定装饰框，05 §2.1）。 */}
        <Input
          readOnly
          aria-label="全局搜索"
          className="global-search"
          placeholder={vm.searchPlaceholder}
          prefix={<SearchOutlined aria-hidden="true" />}
          suffix={<span style={{ fontSize: '0.75rem', opacity: 0.55 }}>⌘K</span>}
          style={{ cursor: 'pointer' }}
          onClick={() => setPaletteOpen(true)}
        />

        <div className="top-bar__status">
          {/* 04A §2.1:运行态徽标 + 通知徽标(互不混用的全局入口),不新增导航项 */}
          <RuntimeStatusBadge />
          <NotificationBell />
          <Tooltip title="帮助">
            <Button aria-label="帮助" icon={<QuestionCircleOutlined />} shape="circle" />
          </Tooltip>
          <div className="user-chip">
            <Avatar size={32}>{vm.projectStatus.avatarText ?? vm.projectStatus.userName.slice(0, 1).toUpperCase()}</Avatar>
            <span>{vm.projectStatus.userName}</span>
            <DownOutlined aria-hidden="true" />
          </div>
        </div>
      </header>

      <div className="shell-layout">
        <nav aria-label="主导航" className="nav-rail">
          {vm.navigationItems.map((item) => {
            const isActive = item.key === vm.activeWorkbench;

            return (
              <button
                aria-current={isActive ? 'page' : undefined}
                aria-label={item.label}
                className={isActive ? 'nav-rail__item nav-rail__item--active' : 'nav-rail__item'}
                key={item.key}
                onClick={() => onNavigate(item.key)}
                type="button"
              >
                <span className="nav-rail__icon">{renderNavigationIcon(item.iconKey)}</span>
                <span className="nav-rail__label">{item.label}</span>
              </button>
            );
          })}
          <button aria-label="收起导航" className="nav-rail__collapse" type="button">
            <ArrowLeftOutlined aria-hidden="true" />
            <span>收起</span>
          </button>
        </nav>

        <main className="workbench-main">
          {renderWorkbench(
            vm.activeWorkbench,
            selectedProject,
            vm.projectStatus.userName,
            onNavigate,
            onCreateProject,
            overviewVM,
            resumeFlow,
            onResumeFlow,
            onProjectChange,
            searchTarget,
            workbenchHandoff,
            onOpenSettingsDomain,
            settingsInitialDomain,
            intakePrefill,
            onContinueEditFlow,
            onDismissIntake,
            onWorkbenchHandoff,
            onConsumeWorkbenchHandoff,
            onProjectDeleted,
          )}
        </main>
      </div>
    </div>
  );
}

function renderWorkbench(
  activeWorkbench: WorkbenchKey,
  selectedProject: ProjectDetailRead | null,
  operatorRef: string,
  onNavigate: (key: WorkbenchKey) => void,
  onCreateProject: (command: ProjectCreateCommand) => Promise<ProjectRead>,
  overviewVM: OverviewWorkbenchVM,
  resumeFlow: RequirementFlowRead | null,
  onResumeFlow: (flowId: string) => void,
  onProjectChange: (projectId: string) => void,
  searchTarget: SearchTarget | null,
  workbenchHandoff: WorkbenchHandoff | null,
  onOpenSettingsDomain: (domain: SettingsDomainKey) => void,
  settingsInitialDomain?: SettingsDomainKey,
  intakePrefill?: IntakePrefillTarget | null,
  onContinueEditFlow?: (flowId: string) => void,
  onDismissIntake?: (flowId: string) => Promise<void>,
  onWorkbenchHandoff?: (handoff: WorkbenchHandoff) => void,
  onConsumeWorkbenchHandoff?: (token: number) => void,
  onProjectDeleted?: (deletedProjectId: string) => Promise<void>,
) {
  if (activeWorkbench === 'management') {
    return (
      <RequirementManagementWorkbench
        intakePrefill={intakePrefill}
        operatorRef={operatorRef}
        resumeFlow={resumeFlow}
        searchTarget={searchTarget}
        selectedProject={selectedProject}
        vm={requirementManagementWorkbenchFixture}
        onDismissIntake={onDismissIntake}
        onNavigate={(key) => onNavigate(key as WorkbenchKey)}
        onWorkbenchHandoff={onWorkbenchHandoff}
      />
    );
  }

  if (activeWorkbench === 'release') {
    // 发布只消费已登记模板（选用/预览）；定制与登记迁入设置 › 文档模板（空态深链跳转）。
    return (
      <PublicationWorkbench
        operatorRef={operatorRef}
        selectedProject={selectedProject}
        onOpenSettingsDomain={onOpenSettingsDomain}
        workbenchHandoff={workbenchHandoff}
        onWorkbenchHandoff={onWorkbenchHandoff}
        onConsumeWorkbenchHandoff={onConsumeWorkbenchHandoff}
      />
    );
  }

  if (activeWorkbench === 'diagram') {
    // 图表深链：searchTarget（entityType=chart）经此传入，list 载入后 openChart（05 §4）。
    return (
      <DiagramWorkbench
        operatorRef={operatorRef}
        searchTarget={searchTarget}
        selectedProject={selectedProject}
        workbenchHandoff={workbenchHandoff}
        onWorkbenchHandoff={onWorkbenchHandoff}
        onConsumeWorkbenchHandoff={onConsumeWorkbenchHandoff}
      />
    );
  }

  if (activeWorkbench === 'traceability') {
    return (
      <TraceabilityWorkbench
        operatorRef={operatorRef}
        searchTarget={searchTarget}
        selectedProject={selectedProject}
        onNavigate={(key) => onNavigate(key as WorkbenchKey)}
        workbenchHandoff={workbenchHandoff}
        onConsumeWorkbenchHandoff={onConsumeWorkbenchHandoff}
      />
    );
  }

  if (activeWorkbench === 'overview') {
    return (
      <OverviewWorkbench
        selectedProject={selectedProject}
        vm={overviewVM}
        onContinueEditFlow={onContinueEditFlow}
        onCreateProject={onCreateProject}
        onNavigate={onNavigate}
        onProjectChange={onProjectChange}
        onResumeFlow={onResumeFlow}
      />
    );
  }

  // 04A §9 设置工作台（配置管理入口）：文档模板域承接定制/登记（深链可预选域）；项目危险区（AEP-113）。
  return (
    <SettingsWorkbench
      initialDomain={settingsInitialDomain}
      operatorRef={operatorRef}
      selectedProject={selectedProject}
      onProjectDeleted={onProjectDeleted}
    />
  );
}
