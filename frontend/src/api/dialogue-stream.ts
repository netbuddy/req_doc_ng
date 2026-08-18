import { apiPostSse } from './client';
import { ApiError } from './errors';

/**
 * 区5 对话流式变体的唯一实现（P0 传输层收敛）。
 *
 * 三页对话端点（AEP-096 知识抽取 / AEP-097 条目形成 / AEP-095 条目评审）的 SSE 帧解析此前逐字
 * 复制三份（analysis/item-formation/item-review），语义完全一致——本函数吸收为一份泛型，三处
 * API 模块改薄包装（只留 URL 拼装与结果类型绑定）。语义逐字保持：stage/result/error 三分支处理
 * 顺序、错误文案、连接中断（无 result 帧）兜底与原三份实现一致——P0 是换底不是改进。
 *
 * - stage 帧：逐帧回调 `handlers.onStage`（链路回执条数据源）；帧损坏只影响点灯，不影响结果。
 * - result 帧：终帧作为返回值（按调用方绑定的 `TResult` 收窄）。
 * - error 帧：抛 ApiError（kind='http'），文案取帧内 message，帧损坏则取原始 data。
 * - 无 result 帧即返回（流被中断）：抛 ApiError（kind='invalid-response'）。
 *
 * 检查点日志（README §3 条 7）：只记 stage 名与结果长度/错误存在性，不落消息原文、不记 token。
 */
export async function sendDialogueStream<TResult>(
  path: string,
  command: unknown,
  handlers: { onStage?: (stage: string) => void },
): Promise<TResult> {
  let result: TResult | null = null;
  let errorMessage: string | null = null;
  await apiPostSse(path, command, (frame) => {
    if (frame.event === 'stage') {
      try {
        const stage = String((JSON.parse(frame.data) as { stage: string }).stage);
        console.info('dialogue_stream.stage', { path, stage });
        handlers.onStage?.(stage);
      } catch {
        // 帧损坏只影响点灯，不影响结果
      }
    } else if (frame.event === 'result') {
      result = JSON.parse(frame.data) as TResult;
    } else if (frame.event === 'error') {
      try {
        errorMessage = String((JSON.parse(frame.data) as { message: string }).message);
      } catch {
        errorMessage = frame.data;
      }
    }
  });
  if (errorMessage) {
    // errorMessage 经闭包内赋值：TS 控制流在真值分支把它收窄为 never，取长度需断言回 string（不落原文，仅记长度）
    console.info('dialogue_stream.error', { path, message_length: (errorMessage as string).length });
    throw new ApiError(errorMessage, { kind: 'http' });
  }
  if (!result) {
    console.info('dialogue_stream.interrupted', { path });
    throw new ApiError('流式响应未返回结果帧（连接被中断）', { kind: 'invalid-response' });
  }
  console.info('dialogue_stream.result', { path });
  return result;
}
