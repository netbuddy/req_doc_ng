import { message } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ThemedConfigProvider, ThemeProvider } from './ui/theme';
import { AppShell } from './shell/AppShell';
import { appShellFixture } from './fixtures/app-shell';
import { overviewWorkbenchFixture } from './fixtures/overview';
import {
  projectsApi,
  type ProjectCreateCommand,
  type ProjectDetailRead,
  type ProjectRead,
} from './api/projects';
import { overviewApi, type OverviewRead, type RequirementFlowRead } from './api/overview';
import type { SearchHitRead } from './api/search';
import type { SearchTarget } from './view-models/search';
import { aiEffectivenessApi, type AiEffectivenessRead } from './api/ai-effectiveness';
import { buildOverviewVM } from './view-models/overview';
import type { ProjectSelectorStatus, WorkbenchKey } from './view-models/app-shell';
import type { IntakePrefillTarget } from './view-models/requirement-management';
import type { SettingsDomainKey } from './view-models/settings';
import type { WorkbenchHandoff } from './view-models/workbench-handoff';

export default function App() {
  const [activeWorkbench, setActiveWorkbench] = useState<WorkbenchKey>('overview');
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>();
  // 项目详情（范围/背景/领域档案）：列表只回摘要（2026-08-07 项目管理组重构），
  // 选中项目的详情按标识单独拉取，总览选中卡与设置页消费。
  const [selectedProjectDetail, setSelectedProjectDetail] = useState<ProjectDetailRead | null>(null);
  const [projectSelectorStatus, setProjectSelectorStatus] = useState<ProjectSelectorStatus>('loading');
  // 总览台只读投影（AEP-052/072）：随项目/进入总览刷新；失败保持 null（fixture 兜底）。
  const [overviewData, setOverviewData] = useState<OverviewRead | null>(null);
  // AI 效能统计（AEP-094）：失败保持 null（AI 效能区显示待接入，不显示假数）。
  const [aiData, setAiData] = useState<AiEffectivenessRead | null>(null);
  // 恢复深链（AEP-072）：总览台点「恢复」→ 携流程上下文进需求管理工作台回放。
  const [resumeFlow, setResumeFlow] = useState<RequirementFlowRead | null>(null);
  // 终结态行「恢复」深链（AEP-112，位置修正 2026-07-10）：预填旧内容进接入表单（预填模式），重提成新流程。
  const [intakePrefill, setIntakePrefill] = useState<IntakePrefillTarget | null>(null);
  // 全局检索深链（P4，05 §3）：命令面板选中 → 携目标切项目 + 切工作台，各工作台 token+projectId 双守卫一次性消费。
  const [searchTarget, setSearchTarget] = useState<SearchTarget | null>(null);
  const [workbenchHandoff, setWorkbenchHandoff] = useState<WorkbenchHandoff | null>(null);
  // 设置深链初始域（发布空态「前往设置 › 文档模板」）：切到设置台并预选该域；侧栏手动进设置则清空。
  const [settingsInitialDomain, setSettingsInitialDomain] = useState<SettingsDomainKey | undefined>();

  useEffect(() => {
    let disposed = false;

    projectsApi
      .listProjects()
      .then((nextProjects) => {
        if (disposed) {
          return;
        }

        setProjects(nextProjects);
        setProjectSelectorStatus(nextProjects.length > 0 ? 'ready' : 'empty');
        setSelectedProjectId((currentId) => {
          if (currentId && nextProjects.some((project) => project.id === currentId)) {
            return currentId;
          }

          return nextProjects[0]?.id;
        });
      })
      .catch(() => {
        if (disposed) {
          return;
        }

        setProjects([]);
        setSelectedProjectId(undefined);
        setProjectSelectorStatus('error');
      });

    return () => {
      disposed = true;
    };
  }, []);

  // 项目切换或回到总览台时刷新只读投影（统计口径=当前选中项目）。
  useEffect(() => {
    if (!selectedProjectId) {
      setOverviewData(null);
      return;
    }

    let disposed = false;
    setOverviewData(null);
    setAiData(null);
    overviewApi
      .getOverview(selectedProjectId)
      .then((data) => {
        if (!disposed) {
          setOverviewData(data);
        }
      })
      .catch(() => {
        if (!disposed) {
          setOverviewData(null);
        }
      });
    aiEffectivenessApi
      .get(selectedProjectId)
      .then((data) => {
        if (!disposed) {
          setAiData(data);
        }
      })
      .catch(() => {
        if (!disposed) {
          setAiData(null);
        }
      });

    return () => {
      disposed = true;
    };
  }, [selectedProjectId, activeWorkbench]);

  // 选中项目详情拉取：切项目即重拉；失败保持 null（卡片以「未填写」样式兜底）。
  useEffect(() => {
    let disposed = false;
    if (!selectedProjectId) {
      setSelectedProjectDetail(null);
      return;
    }
    projectsApi
      .getProject(selectedProjectId)
      .then((detail) => {
        if (!disposed) {
          setSelectedProjectDetail(detail);
        }
      })
      .catch(() => {
        if (!disposed) {
          setSelectedProjectDetail(null);
        }
      });
    return () => {
      disposed = true;
    };
  }, [selectedProjectId]);

  // 选中项目=摘要立即可用＋详情到位后补全（避免切项目时下游短暂拿到 null）。
  const selectedProject = useMemo<ProjectDetailRead | null>(() => {
    const summary = projects.find((project) => project.id === selectedProjectId) ?? null;
    if (!summary) {
      return null;
    }
    if (selectedProjectDetail && selectedProjectDetail.id === summary.id) {
      return selectedProjectDetail;
    }
    return {
      ...summary,
      scope: null,
      background: null,
      domain_profile_key: null,
      domain_profile_label: '',
    };
  }, [projects, selectedProjectId, selectedProjectDetail]);

  // 创建项目由项目上下文服务写入 LDM-001；成功后并入列表并选中新项目（新项目即当前上下文）。
  // 操作者标识取顶栏人设（与其余写接口同源）；幂等键由接口层生成。
  const handleCreateProject = useCallback(async (command: ProjectCreateCommand): Promise<ProjectRead> => {
    const created = await projectsApi.createProject(command, appShellFixture.projectStatus.userName);

    setProjects((current) =>
      current.some((project) => project.id === created.id) ? current : [...current, created],
    );
    setSelectedProjectId(created.id);
    setProjectSelectorStatus('ready');

    return created;
  }, []);

  // 导航：离开需求管理工作台即清空恢复上下文（每次恢复由总览台显式发起）。
  // 手动导航同时清空检索深链目标：防陈引用在新上下文残留幽灵选中（05 §3.1）。
  const handleNavigate = useCallback((key: WorkbenchKey) => {
    setActiveWorkbench(key);
    setSearchTarget(null);
    setWorkbenchHandoff(null);
    setSettingsInitialDomain(undefined); // 侧栏手动进设置：不强制预选域，落默认域
    if (key !== 'management') {
      setResumeFlow(null);
      setIntakePrefill(null);
    }
  }, []);

  const handleWorkbenchHandoff = useCallback((handoff: WorkbenchHandoff) => {
    console.info('workbench_handoff.created', {
      intent: handoff.intent,
      targetWorkbench: handoff.targetWorkbench,
    });
    if (handoff.projectId !== selectedProjectId) setSelectedProjectId(handoff.projectId);
    setSearchTarget(null);
    setResumeFlow(null);
    setIntakePrefill(null);
    setWorkbenchHandoff(handoff);
    setActiveWorkbench(handoff.targetWorkbench);
  }, [selectedProjectId]);

  const handleConsumeWorkbenchHandoff = useCallback((token: number) => {
    setWorkbenchHandoff((current) => (current?.token === token ? null : current));
  }, []);

  // 发布空态深链：切到设置工作台并预选文档模板域（定制/登记已迁入设置）。
  const handleOpenSettingsDomain = useCallback((domain: SettingsDomainKey) => {
    setSearchTarget(null);
    setResumeFlow(null);
    setIntakePrefill(null);
    setSettingsInitialDomain(domain);
    setActiveWorkbench('settings');
  }, []);

  // 手动切项目：清空检索深链目标（陈目标属于旧项目，切后不得幽灵选中）。
  const handleProjectChange = useCallback((projectId: string) => {
    setSearchTarget(null);
    setWorkbenchHandoff(null);
    setSelectedProjectId(projectId);
  }, []);

  // 项目删除成功（AEP-113）：重取项目列表为准（后端过滤口径），切换到剩余首个项目；
  // 无剩余则回空态引导新建。深链上下文全部清空（属于已删项目）。
  const handleProjectDeleted = useCallback(async (deletedProjectId: string) => {
    setSearchTarget(null);
    setResumeFlow(null);
    setIntakePrefill(null);
    setWorkbenchHandoff(null);
    let remaining: ProjectRead[] = [];
    try {
      remaining = await projectsApi.listProjects();
    } catch {
      remaining = [];
    }
    setProjects(remaining);
    setProjectSelectorStatus(remaining.length > 0 ? 'ready' : 'empty');
    setSelectedProjectId((currentId) => {
      if (currentId && currentId !== deletedProjectId && remaining.some((p) => p.id === currentId)) {
        return currentId;
      }
      return remaining[0]?.id;
    });
  }, []);

  // 恢复深链：总览台只导航，阶段回放由需求管理工作台经既有读端点完成（UINV-21/22）。
  const handleResumeFlow = useCallback(
    (flowId: string) => {
      const flow = overviewData?.flows.find((item) => item.flow_id === flowId) ?? null;
      if (!flow) {
        return;
      }

      setIntakePrefill(null);
      setResumeFlow(flow);
      setActiveWorkbench('management');
    },
    [overviewData],
  );

  // 终结态行「恢复」（AEP-112，位置修正 2026-07-10）：读旧上下文提交内容 → 携预填目标进管理台
  // 接入表单（预填模式，接入页开放「放弃本次接入」）；读取失败留在总览不导航。
  const handleContinueEditFlow = useCallback(
    async (flowId: string) => {
      if (!selectedProjectId) {
        return;
      }
      const prefill = await overviewApi.getIntakePrefill(selectedProjectId, flowId).catch(() => null);
      if (!prefill) {
        return;
      }
      const flowTitle = overviewData?.flows.find((flow) => flow.flow_id === flowId)?.title ?? '';
      setResumeFlow(null);
      setSearchTarget(null);
      setIntakePrefill({
        token: Date.now(),
        projectId: selectedProjectId,
        flowId,
        title: flowTitle,
        contextRef: prefill.context_ref,
        rawText: prefill.raw_text,
        sourceNote: prefill.source_note,
      });
      setActiveWorkbench('management');
    },
    [overviewData, selectedProjectId],
  );

  // 放弃本次接入（AEP-111 软删，入口在材料接入页预填模式）：成功后提示并返回总览；
  // 该行即时移除（后端过滤为准，回总览后的重取口径一致）。
  const handleDismissIntake = useCallback(
    async (flowId: string) => {
      if (!selectedProjectId) {
        return;
      }
      await overviewApi.dismissFlow(selectedProjectId, flowId, {
        operator_ref: appShellFixture.projectStatus.userName,
      });
      setOverviewData((current) =>
        current
          ? { ...current, flows: current.flows.filter((flow) => flow.flow_id !== flowId) }
          : current,
      );
      setIntakePrefill(null);
      setResumeFlow(null);
      setActiveWorkbench('overview');
      message.success('已放弃本次接入，过程记录保留可审计');
    },
    [selectedProjectId],
  );

  // 命令面板选中 → 跨项目深链（05 §3.1）：① 跨项目先切项目 ② 携目标（token 唯一）③ 切工作台。
  // 清 resumeFlow 保证管理台落维护视图而非创建流；各工作台按 token+projectId 双守卫一次性消费。
  const handleSearchNavigate = useCallback(
    (hit: SearchHitRead) => {
      const workbench = hit.workbench as WorkbenchKey;
      if (hit.project_id !== selectedProjectId) setSelectedProjectId(hit.project_id);
      setResumeFlow(null);
      setIntakePrefill(null);
      setSearchTarget({
        projectId: hit.project_id,
        workbench,
        entityType: hit.entity_type,
        ref: hit.ref,
        title: hit.title,
        token: Date.now(),
      });
      setActiveWorkbench(workbench);
    },
    [selectedProjectId],
  );

  // 真实项目列表 + 只读投影装配 VM；加载中/失败时指标保持 `—` 占位（不显示假数）。
  const overviewVM = useMemo(
    () => buildOverviewVM(overviewWorkbenchFixture, projects, overviewData, aiData),
    [aiData, overviewData, projects],
  );

  const vm = useMemo(
    () => ({
      ...appShellFixture,
      activeWorkbench,
      projectSelectorStatus,
      projectOptions: projects.map((project) => ({
        id: project.id,
        name: project.name,
      })),
      selectedProjectId,
      projectSelectorText:
        selectedProject?.name ??
        (projectSelectorStatus === 'loading'
          ? '正在加载项目'
          : projectSelectorStatus === 'error'
            ? '项目加载失败'
            : '请先创建项目'),
    }),
    [activeWorkbench, projectSelectorStatus, projects, selectedProject, selectedProjectId],
  );

  return (
    <ThemeProvider>
      <ThemedConfigProvider locale={zhCN}>
        <AppShell
          intakePrefill={intakePrefill}
          overviewVM={overviewVM}
          resumeFlow={resumeFlow}
          searchTarget={searchTarget}
          workbenchHandoff={workbenchHandoff}
          selectedProject={selectedProject}
          settingsInitialDomain={settingsInitialDomain}
          vm={vm}
          onContinueEditFlow={handleContinueEditFlow}
          onCreateProject={handleCreateProject}
          onDismissIntake={handleDismissIntake}
          onNavigate={handleNavigate}
          onWorkbenchHandoff={handleWorkbenchHandoff}
          onConsumeWorkbenchHandoff={handleConsumeWorkbenchHandoff}
          onOpenSettingsDomain={handleOpenSettingsDomain}
          onProjectChange={handleProjectChange}
          onProjectDeleted={handleProjectDeleted}
          onResumeFlow={handleResumeFlow}
          onSearchNavigate={handleSearchNavigate}
        />
      </ThemedConfigProvider>
    </ThemeProvider>
  );
}
