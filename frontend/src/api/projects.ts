/**
 * 项目管理接口封装——继材料模块之后第二个走 V2 应答信封的前端模块
 * （2026-08-07 项目管理组重构：整组存量接口切换到信封应答）。
 *
 * 信封约定（api/openapi.yaml 项目四操作）：成功与业务拒绝同走 200，以 result
 * 字段区分；本模块自行拆封，对外只交出数据，调用方不感知信封存在。
 *
 * 字段映射：线上契约的项目标识叫 project_id，本模块把它映射成前端内部的 id
 * ——既有页面代码（App 与各工作台共 20 余处）都以 selectedProject.id 取用，
 * 映射收在本层可让页面零改动（MVVM 边界：接口层拥有线上形态到视图形态的转换）。
 */
import { apiDelete, apiGet, apiPost } from './client';
import { createIdempotencyKey } from './idempotency';
import type { components } from './generated/schema';

type SuccessOfProjectList = components['schemas']['SuccessOfProjectList'];
type SuccessOfProjectDetail = components['schemas']['SuccessOfProjectDetail'];
type SuccessOfProjectDeletion = components['schemas']['SuccessOfProjectDeletion'];
type BusinessRejectionEnvelope = components['schemas']['BusinessRejectionEnvelope'];
export type DomainProfileRead = components['schemas']['DomainProfileRead'];
export type ProjectDeletionReport = components['schemas']['ProjectDeletionReport'];

/** 项目摘要视图（后端 ProjectSummary 的 id 映射版）——列表与当前项目选择用。 */
export interface ProjectRead {
  id: string;
  name: string;
  created_at: string;
}

/** 项目详情视图（后端 ProjectDetail 的 id 映射版）——设置页展示用。 */
export interface ProjectDetailRead {
  id: string;
  name: string;
  scope: string | null;
  background: string | null;
  domain_profile_key: string | null;
  domain_profile_label: string;
  created_at: string;
}

/** 创建项目的用户可填部分；操作者与幂等键由 createProject 补齐。 */
export interface ProjectCreateCommand {
  name: string;
  scope?: string | null;
  background?: string | null;
  domain_profile_key?: string | null;
}

export const projectsApi = {
  /** 列出全部项目（摘要：标识、名称、创建时刻；详情走 getProject）。 */
  async listProjects(): Promise<ProjectRead[]> {
    const body = await apiGet<SuccessOfProjectList>('/projects');
    return body.data.map((p) => ({ id: p.project_id, name: p.name, created_at: p.created_at }));
  },

  /** 读单个项目详情（范围、背景、领域档案显示名）。 */
  async getProject(projectId: string): Promise<ProjectDetailRead> {
    const body = await apiGet<SuccessOfProjectDetail>(
      `/projects/${encodeURIComponent(projectId)}`,
    );
    const d = body.data;
    return {
      id: d.project_id,
      name: d.name,
      scope: d.scope ?? null,
      background: d.background ?? null,
      domain_profile_key: d.domain_profile_key ?? null,
      domain_profile_label: d.domain_profile_label,
      created_at: d.created_at,
    };
  },

  /** 创建项目：补上操作者与幂等键（同键重放后端返回同一项目）。 */
  async createProject(command: ProjectCreateCommand, operatorRef: string): Promise<ProjectRead> {
    const body = await apiPost<SuccessOfProjectDetail>('/projects', {
      ...command,
      operator_ref: operatorRef,
      idempotency_key: createIdempotencyKey(),
    });
    const d = body.data;
    return { id: d.project_id, name: d.name, created_at: d.created_at };
  },

  // AEP-103：领域档案只读目录（建项目下拉 + 设置页展示）
  listDomainProfiles(): Promise<DomainProfileRead[]> {
    return apiGet<DomainProfileRead[]>('/config/domain-profiles');
  },

  /**
   * 删除项目（级联删净）。404=不存在；项目内有执行中 AI 任务时后端回业务拒绝
   * 信封（200），本层把它转成异常抛出，调用方沿用既有 try/catch 展示文案。
   */
  async deleteProject(projectId: string, operatorRef: string): Promise<ProjectDeletionReport> {
    const body = await apiDelete<SuccessOfProjectDeletion | BusinessRejectionEnvelope>(
      `/projects/${encodeURIComponent(projectId)}?operator_ref=${encodeURIComponent(operatorRef)}`,
    );
    if (body.result === '业务拒绝') {
      throw new Error(body.rejection.message);
    }
    return body.data;
  },
};
