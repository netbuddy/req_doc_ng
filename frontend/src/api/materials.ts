/**
 * 材料读侧接口封装——第一个走 V2 应答信封的前端模块。
 *
 * 信封约定（api/openapi.yaml）：成功与业务拒绝同走 200，以 result 字段区分；
 * 与 client.ts 处理的 V1 旧信封（success/data/error）不同，故本模块自行拆封：
 * 对外只交出 data，调用方不感知信封存在。类型全部取自生成文件（契约同源）。
 */
import { apiGet } from './client';
import type { components } from './generated/schema';

export type MaterialSummary = components['schemas']['MaterialSummary'];
export type SuccessOfMaterialList = components['schemas']['SuccessOfMaterialList'];

export const materialsApi = {
  /** 列出项目内全部材料（按导入时刻倒序）。 */
  async list(projectId: string): Promise<MaterialSummary[]> {
    const body = await apiGet<SuccessOfMaterialList>(
      `/projects/${encodeURIComponent(projectId)}/materials`,
    );
    return body.data;
  },
};
