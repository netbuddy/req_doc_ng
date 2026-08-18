import { ApiError } from './errors';
import { observeServerDate } from './server-clock';

export const API_BASE = '/api';

/**
 * 每次响应顺带校一次服务器时钟（走查反馈第⑤组）。
 * 放在这里而不是挑一个专门的探测接口：所有业务请求都经过本文件，白拿的观测值不必另发请求。
 * 测试里的 mock 响应可能没有 headers，故不假定它存在。
 */
function noteServerDate(response: Response): void {
  observeServerDate(response.headers?.get?.('Date') ?? null);
}

interface Envelope<T> {
  success: boolean;
  data?: T;
  error?: string | null;
}

function isEnvelope<T>(body: unknown): body is Envelope<T> {
  return (
    typeof body === 'object' &&
    body !== null &&
    'success' in body &&
    ('data' in body || 'error' in body)
  );
}

function snippet(text: string): string {
  const trimmed = text.trim().replace(/\s+/g, ' ');
  return trimmed.length > 200 ? `${trimmed.slice(0, 200)}…` : trimmed;
}

function pickErrorDetail(body: unknown): string {
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>;
    if (typeof record.error === 'string') return record.error;
    if (typeof record.detail === 'string') return record.detail;
    if (record.detail != null) return String(record.detail);
  }
  return '';
}

interface ParsedBody {
  text: string | null; // 原始文本（仅 response.text() 可用时）
  body: unknown;
  jsonOk: boolean;
}

// 优先用 text()（真实浏览器：可在非 JSON 时暴露原始内容）；仅提供 json() 的环境（测试 mock）回退。
async function readBody(response: Response): Promise<ParsedBody> {
  if (typeof response.text === 'function') {
    const text = await response.text();
    if (!text) return { text: '', body: null, jsonOk: false };
    try {
      return { text, body: JSON.parse(text), jsonOk: true };
    } catch {
      return { text, body: null, jsonOk: false };
    }
  }
  try {
    return { text: null, body: await response.json(), jsonOk: true };
  } catch {
    return { text: null, body: null, jsonOk: false };
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  let response: Response;

  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
  } catch (error) {
    throw new ApiError(`请求 ${url} 失败：无法连接后端（网络或代理错误）`, { kind: 'network' });
  }
  noteServerDate(response);

  const { text, body, jsonOk } = await readBody(response);

  if (!response.ok) {
    // 暴露真实错误：后端 {error}/{detail}，否则非 JSON 响应体片段（代理错误页 / 500 纯文本）。
    const detail = pickErrorDetail(body) || (text ? snippet(text) : '');
    throw new ApiError(
      `请求 ${url} 失败：HTTP ${response.status}${detail ? ` — ${detail}` : ''}`,
      { kind: 'http', status: response.status },
    );
  }

  if (!jsonOk) {
    // 2xx 但响应体不是合法 JSON（多为代理/网关插入的非 JSON 内容或空体）。
    throw new ApiError(
      text
        ? `请求 ${url} 返回非 JSON 响应（HTTP ${response.status}）：${snippet(text)}`
        : `请求 ${url} 返回空响应（HTTP ${response.status}）`,
      { kind: 'invalid-response' },
    );
  }

  if (isEnvelope<T>(body)) {
    if (body.success === false) {
      throw new ApiError(body.error ?? `请求 ${url} 失败`, { kind: 'http' });
    }
    return body.data as T;
  }

  return body as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'GET' });
}

/** HEAD 探活（不取正文）：2xx resolve，否则抛 ApiError。用于 iframe 直连前先判可用（如 PDF 精确预览）。 */
export async function apiHead(path: string): Promise<void> {
  const url = `${API_BASE}${path}`;
  let response: Response;
  try {
    response = await fetch(url, { method: 'HEAD' });
  } catch {
    throw new ApiError(`请求 ${url} 失败：无法连接后端（网络或代理错误）`, { kind: 'network' });
  }
  noteServerDate(response);
  if (!response.ok) {
    throw new ApiError(`请求 ${url} 失败：HTTP ${response.status}`, { kind: 'http', status: response.status });
  }
}

/** 取二进制资源（如生成好的 docx 字节流）为 Blob；错误口径同 request（抛 ApiError）。 */
export async function apiGetBlob(path: string): Promise<Blob> {
  const url = `${API_BASE}${path}`;
  let response: Response;
  try {
    response = await fetch(url, { headers: { Accept: 'application/octet-stream' } });
  } catch {
    throw new ApiError(`请求 ${url} 失败：无法连接后端（网络或代理错误）`, { kind: 'network' });
  }
  noteServerDate(response);
  if (!response.ok) {
    throw new ApiError(`请求 ${url} 失败：HTTP ${response.status}`, { kind: 'http', status: response.status });
  }
  return response.blob();
}

// POST JSON → 二进制 Blob（如图形栅格化：源码入参、PNG 出参）。取字节仍走 api 层守 MVVM 边界。
export async function apiPostBlob(path: string, body: unknown): Promise<Blob> {
  const url = `${API_BASE}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { Accept: 'application/octet-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(`请求 ${url} 失败：无法连接后端（网络或代理错误）`, { kind: 'network' });
  }
  noteServerDate(response);
  if (!response.ok) {
    throw new ApiError(`请求 ${url} 失败：HTTP ${response.status}`, { kind: 'http', status: response.status });
  }
  return response.blob();
}

export interface SseFrame {
  event: string;
  data: string;
}

/**
 * SSE POST（对话端点流式变体，链路回执条数据源）：逐帧回调 `event:`/`data:` 对。
 * 非 2xx 与网络错误抛 ApiError（口径同 request）；流正常结束即 resolve。
 * 环境不支持流式读取（无 response.body，如测试 mock）时抛 invalid-response，调用方回退非流式。
 */
export async function apiPostSse(
  path: string,
  body: unknown,
  onFrame: (frame: SseFrame) => void,
): Promise<void> {
  const url = `${API_BASE}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(`请求 ${url} 失败：无法连接后端（网络或代理错误）`, { kind: 'network' });
  }
  noteServerDate(response);
  if (!response.ok) {
    const { text, body: parsed } = await readBody(response);
    const detail = pickErrorDetail(parsed) || (text ? snippet(text) : '');
    throw new ApiError(
      `请求 ${url} 失败：HTTP ${response.status}${detail ? ` — ${detail}` : ''}`,
      { kind: 'http', status: response.status },
    );
  }
  if (!response.body) {
    throw new ApiError(`请求 ${url} 返回不可流式读取的响应`, { kind: 'invalid-response' });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let event = 'message';
  const dataLines: string[] = [];
  const flush = () => {
    if (dataLines.length) {
      onFrame({ event, data: dataLines.join('\n') });
    }
    event = 'message';
    dataLines.length = 0;
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf('\n');
    while (newline >= 0) {
      const line = buffer.slice(0, newline).replace(/\r$/, '');
      buffer = buffer.slice(newline + 1);
      if (line === '') {
        flush();
      } else if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).replace(/^ /, ''));
      }
      newline = buffer.indexOf('\n');
    }
  }
  flush();
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}
