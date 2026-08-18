import { apiGet } from './client';
import type { components } from './generated/schema';

// ---- 全局检索·跨项目命令面板（GET /api/search；工作包 04 篇）----

export type SearchResultsRead = components['schemas']['SearchResultsRead'];
export type SearchGroupRead = components['schemas']['SearchGroupRead'];
export type SearchHitRead = components['schemas']['SearchHitRead'];

export const searchApi = {
  search(params: { q: string; types?: string[]; limit?: number }): Promise<SearchResultsRead> {
    const query = new URLSearchParams();
    query.set('q', params.q);
    if (params.types && params.types.length > 0) query.set('types', params.types.join(','));
    if (params.limit) query.set('limit', String(params.limit));
    return apiGet<SearchResultsRead>(`/search?${query.toString()}`);
  },
};
