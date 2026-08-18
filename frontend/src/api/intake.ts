/**
 * 材料接入接口封装——V2 应答信封（2026-08-08 路线 A：三拍制保留，应答信封化）。
 *
 * 信封由本模块拆解：提交的「前检不过」（正文为空／项目未选定）在线上是业务拒绝
 * 信封（200），本模块把它映射回工作台既有的 rejected_precheck 状态键——界面的
 * 预检状态机不感知信封存在。项目标识只走路径，请求体不再携带 project_ref。
 */
import { apiGet, apiPost } from './client';
import type { components } from './generated/schema';

export type IntakeSubmitCommand = components['schemas']['IntakeSubmitCommand'];
export type IntakeResultRead = components['schemas']['IntakeResultRead'];
type SuccessOfIntakeReceipt = components['schemas']['SuccessOfIntakeReceipt'];
type SuccessOfIntakeConclusion = components['schemas']['SuccessOfIntakeConclusion'];
type BusinessRejectionEnvelope = components['schemas']['BusinessRejectionEnvelope'];

/** 提交结果（模块内已拆信封）：受理登记成功，或前检业务拒绝。 */
export type IntakeSubmitOutcome =
  | { status: 'submitted'; context_ref: string; agent_run_ref: string | null }
  | { status: 'rejected_precheck'; reason_code: string; message: string };

function projectPath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/intake`;
}

export const intakeApi = {
  async submit(projectId: string, command: IntakeSubmitCommand): Promise<IntakeSubmitOutcome> {
    const body = await apiPost<SuccessOfIntakeReceipt | BusinessRejectionEnvelope>(
      projectPath(projectId),
      command,
    );
    if (body.result === '业务拒绝') {
      return {
        status: 'rejected_precheck',
        reason_code: body.rejection.reason_code,
        message: body.rejection.message,
      };
    }
    return {
      status: 'submitted',
      context_ref: body.data.context_ref,
      agent_run_ref: body.data.agent_run_ref ?? null,
    };
  },

  async getResult(projectId: string, contextRef: string): Promise<IntakeResultRead> {
    const body = await apiGet<SuccessOfIntakeConclusion>(
      `${projectPath(projectId)}/${encodeURIComponent(contextRef)}`,
    );
    return body.data;
  },
};
