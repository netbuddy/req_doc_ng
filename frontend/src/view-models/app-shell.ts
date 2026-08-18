export type WorkbenchKey =
  | 'overview'
  | 'management'
  | 'traceability'
  | 'diagram'
  | 'release'
  | 'settings';

export type NavigationIconKey = WorkbenchKey;

export interface NavigationItemVM {
  key: WorkbenchKey;
  label: string;
  iconKey: NavigationIconKey;
}

export interface ProjectStatusVM {
  projectName: string;
  userName: string;
  avatarText?: string;
  globalStatus: 'normal' | 'degraded' | 'blocked';
  statusText: string;
}

export interface ProjectOptionVM {
  id: string;
  name: string;
  statusText?: string;
}

export type ProjectSelectorStatus = 'loading' | 'ready' | 'empty' | 'error';

export interface AppShellVM {
  activeWorkbench: WorkbenchKey;
  navigationItems: NavigationItemVM[];
  projectStatus: ProjectStatusVM;
  projectSelectorText: string;
  projectSelectorStatus: ProjectSelectorStatus;
  projectOptions: ProjectOptionVM[];
  selectedProjectId?: string;
  searchPlaceholder: string;
}
