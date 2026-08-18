import { apiPost } from './client';
import type { components } from './generated/schema';

// AEP-036 待确认字段修订（需求条目服务）—— 与 AEP-038 命令归属分开。
export type ItemRevisionCommand = components['schemas']['ItemRevisionCommand'];
export type ItemRevisionResult = components['schemas']['ItemRevisionResult'];
export type ItemRevisionMode = components['schemas']['ItemRevisionMode'];

function requirementRevisionPath(projectId: string, itemRef: string): string {
  return `/projects/${encodeURIComponent(projectId)}/requirements/${encodeURIComponent(itemRef)}/revision`;
}

export const requirementsApi = {
  applyItemRevision(
    projectId: string,
    itemRef: string,
    command: ItemRevisionCommand,
  ): Promise<ItemRevisionResult> {
    return apiPost<ItemRevisionResult>(requirementRevisionPath(projectId, itemRef), command);
  },
};
