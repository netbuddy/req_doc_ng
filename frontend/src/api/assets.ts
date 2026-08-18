import { apiGet } from './client';
import type { components } from './generated/schema';

// ---- 需求资产目录·资产读侧（04A §5 资产树/详情 + §3.1 维护列表；只读投影）----

export type AssetCatalogRead = components['schemas']['AssetCatalogRead'];
export type AssetGroupRead = components['schemas']['AssetGroupRead'];
export type AssetNodeRead = components['schemas']['AssetNodeRead'];
export type AssetDetailRead = components['schemas']['AssetDetailRead'];
export type AssetTraceSummaryRead = components['schemas']['AssetTraceSummaryRead'];
export type ItemMaintenanceListRead = components['schemas']['ItemMaintenanceListRead'];
export type ItemMaintenanceItemRead = components['schemas']['ItemMaintenanceItemRead'];
export type ItemMaintenanceCardRead = components['schemas']['ItemMaintenanceCardRead'];
export type ItemRevisionRead = components['schemas']['ItemRevisionRead'];
export type ItemSourceEvidenceRead = components['schemas']['ItemSourceEvidenceRead'];
export type BusinessKnowledgeListRead = components['schemas']['BusinessKnowledgeListRead'];
export type BusinessKnowledgeRowRead = components['schemas']['BusinessKnowledgeRowRead'];

export type AssetType =
  | 'material'
  | 'element'
  | 'requirement_item'
  | 'chart'
  | 'trace_link'
  | 'document'
  | 'issue';

function projectPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export const assetsApi = {
  catalog(projectId: string): Promise<AssetCatalogRead> {
    return apiGet<AssetCatalogRead>(`${projectPath(projectId)}/assets/catalog`);
  },

  detail(projectId: string, assetType: string, ref: string): Promise<AssetDetailRead> {
    return apiGet<AssetDetailRead>(
      `${projectPath(projectId)}/assets/${encodeURIComponent(assetType)}/${encodeURIComponent(ref)}`,
    );
  },

  listItems(
    projectId: string,
    params: { status?: string; reqType?: string; search?: string; gap?: string } = {},
  ): Promise<ItemMaintenanceListRead> {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.reqType) query.set('req_type', params.reqType);
    if (params.search) query.set('search', params.search);
    if (params.gap) query.set('gap', params.gap); // 缺验收准则/缺优先级警示筛选（29148 属性补齐）
    const suffix = query.size > 0 ? `?${query.toString()}` : '';
    return apiGet<ItemMaintenanceListRead>(`${projectPath(projectId)}/requirement-items${suffix}`);
  },

  itemCard(projectId: string, itemRef: string): Promise<ItemMaintenanceCardRead> {
    return apiGet<ItemMaintenanceCardRead>(
      `${projectPath(projectId)}/requirement-items/${encodeURIComponent(itemRef)}`,
    );
  },

  // AEP-104 业务知识清单（05 §2）：业务领域知识翼要素只读治理面。
  listBusinessKnowledge(
    projectId: string,
    params: { elementType?: string; status?: string; search?: string } = {},
  ): Promise<BusinessKnowledgeListRead> {
    const query = new URLSearchParams();
    if (params.elementType) query.set('element_type', params.elementType);
    if (params.status) query.set('status', params.status);
    if (params.search) query.set('search', params.search);
    const suffix = query.size > 0 ? `?${query.toString()}` : '';
    return apiGet<BusinessKnowledgeListRead>(
      `${projectPath(projectId)}/assets/business-knowledge${suffix}`,
    );
  },
};
