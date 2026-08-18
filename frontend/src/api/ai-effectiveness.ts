import { apiGet } from './client';
import type { components } from './generated/schema';

// ---- AEP-094 AI 效能按环节统计（模型推理结果仓储·统计读面；只读，UINV-23）----

export type AiEffectivenessRead = components['schemas']['AiEffectivenessRead'];
export type AiStageEffectRead = components['schemas']['AiStageEffectRead'];
export type AiCalibrationRead = components['schemas']['AiCalibrationRead'];
export type AiCoverageRead = components['schemas']['AiCoverageRead'];
export type AiRiskSignalRead = components['schemas']['AiRiskSignalRead'];
export type AiDeliveryFailureRead = components['schemas']['AiDeliveryFailureRead'];
export type AiFailureStageCountRead = components['schemas']['AiFailureStageCountRead'];
export type AiDeliveryFailureInstancesRead =
  components['schemas']['AiDeliveryFailureInstancesRead'];
export type AiDeliveryFailureInstanceRead =
  components['schemas']['AiDeliveryFailureInstanceRead'];

export const aiEffectivenessApi = {
  get(projectId: string, windowDays = 30): Promise<AiEffectivenessRead> {
    return apiGet<AiEffectivenessRead>(
      `/projects/${encodeURIComponent(projectId)}/ai-effectiveness?window_days=${windowDays}`,
    );
  },
  /** 交付失败个案钻取（口径 §5.5）：某 lane[×失败关卡] 的失败行明细。 */
  deliveryFailureInstances(
    projectId: string,
    stage: string,
    opts: { failureStage?: string; windowDays?: number; limit?: number } = {},
  ): Promise<AiDeliveryFailureInstancesRead> {
    const params = new URLSearchParams({ stage, window_days: `${opts.windowDays ?? 30}` });
    if (opts.failureStage) params.set('failure_stage', opts.failureStage);
    if (opts.limit) params.set('limit', `${opts.limit}`);
    return apiGet<AiDeliveryFailureInstancesRead>(
      `/projects/${encodeURIComponent(projectId)}/ai-effectiveness/delivery-failures?${params}`,
    );
  },
};
