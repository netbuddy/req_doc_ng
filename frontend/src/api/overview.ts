import { apiGet, apiPost } from './client';
import type { components } from './generated/schema';

export type OverviewRead = components['schemas']['OverviewRead'];
export type OverviewStatMetricRead = components['schemas']['OverviewStatMetricRead'];
export type RequirementFlowRead = components['schemas']['RequirementFlowRead'];
export type FlowStageStatusRead = components['schemas']['FlowStageStatusRead'];
export type IntakePrefillRead = components['schemas']['IntakePrefillRead'];
export type FlowDismissCommand = components['schemas']['FlowDismissCommand'];
export type FlowDismissRead = components['schemas']['FlowDismissRead'];

function projectPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}`;
}

// 需求资产目录服务只读投影（AEP-052 资产盘点 / AEP-072 跨任务状态聚合）。
// 总览台边界（UINV-21/22）：只读 + 导航；「恢复」由需求管理工作台经既有读端点回放。
// 终结态处置（OVW-001 修订 2026-07-10）：AEP-112 继续编辑预填读 + AEP-111 放弃本次接入（软删）。
export const overviewApi = {
  getOverview(projectId: string): Promise<OverviewRead> {
    return apiGet<OverviewRead>(`${projectPath(projectId)}/overview`);
  },

  getRequirementFlows(projectId: string): Promise<RequirementFlowRead[]> {
    return apiGet<RequirementFlowRead[]>(`${projectPath(projectId)}/requirement-flows`);
  },

  getIntakePrefill(projectId: string, contextRef: string): Promise<IntakePrefillRead> {
    return apiGet<IntakePrefillRead>(
      `${projectPath(projectId)}/requirement-flows/${encodeURIComponent(contextRef)}/intake-prefill`,
    );
  },

  dismissFlow(
    projectId: string,
    contextRef: string,
    command: FlowDismissCommand,
  ): Promise<FlowDismissRead> {
    return apiPost<FlowDismissRead>(
      `${projectPath(projectId)}/requirement-flows/${encodeURIComponent(contextRef)}/dismiss`,
      command,
    );
  },
};
