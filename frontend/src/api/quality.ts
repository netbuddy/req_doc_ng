/**
 * 需求质量诊断器 API（v2 签名件）：条目质量投影读（AEP-105）。
 * 触发诊断复用 itemReviewApi.startDiagnosis（单条目批次）+ agentRunApi.subscribe。
 */
import { apiGet } from './client';
import type { components } from './generated/schema';

export type ItemQualityRead = components['schemas']['ItemQualityRead'];
export type SourceAlignmentRead = components['schemas']['SourceAlignmentRead'];
export type ReviewFindingRead = components['schemas']['ReviewFindingRead'];

export const qualityApi = {
  getItemQuality(projectId: string, itemRef: string): Promise<ItemQualityRead> {
    return apiGet<ItemQualityRead>(
      `/projects/${encodeURIComponent(projectId)}/requirement-items/${encodeURIComponent(itemRef)}/quality`,
    );
  },
};
