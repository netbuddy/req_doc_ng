import type { AppShellVM } from '../view-models/app-shell';

export const appShellFixture: AppShellVM = {
  activeWorkbench: 'overview',
  navigationItems: [
    { key: 'overview', label: '总览', iconKey: 'overview' },
    { key: 'management', label: '管理', iconKey: 'management' },
    { key: 'traceability', label: '追溯', iconKey: 'traceability' },
    { key: 'diagram', label: '图表', iconKey: 'diagram' },
    { key: 'release', label: '发布', iconKey: 'release' },
    { key: 'settings', label: '设置', iconKey: 'settings' },
  ],
  projectStatus: {
    projectName: '需求工程示例项目',
    userName: '李想',
    avatarText: 'Y',
    globalStatus: 'normal',
    statusText: '运行中',
  },
  projectSelectorText: '运营效率系统',
  projectSelectorStatus: 'empty',
  projectOptions: [],
  searchPlaceholder: '搜索需求、资产、文档...',
};
