import type { WorkbenchKey } from './app-shell';

export type WorkbenchHandoffIntent =
  | 'create_chart_from_item'
  | 'compose_document_from_assets'
  | 'inspect_document_trace';

export type WorkbenchHandoffEntityType = 'requirement_item' | 'chart' | 'document';

export interface WorkbenchHandoffAsset {
  entityType: WorkbenchHandoffEntityType;
  ref: string;
  title: string;
}

/** 一次性对象交接：只预选/聚焦，不执行目标工作台的写操作。 */
export interface WorkbenchHandoff {
  token: number;
  projectId: string;
  targetWorkbench: WorkbenchKey;
  intent: WorkbenchHandoffIntent;
  anchor: WorkbenchHandoffAsset;
  relatedAssets: WorkbenchHandoffAsset[];
}

export function createWorkbenchHandoff(
  input: Omit<WorkbenchHandoff, 'token'>,
): WorkbenchHandoff {
  return { ...input, token: Date.now() };
}
