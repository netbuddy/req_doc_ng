import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../src/api/errors';
import { apiGet, apiPost } from '../src/api/client';
import { agentRunApi } from '../src/api/agent-runs';
import { intakeApi } from '../src/api/intake';
import { projectsApi } from '../src/api/projects';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('api client', () => {
  it('所有请求经 /api 前缀', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await apiGet('/health');

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/health');
  });

  it('兼容通用响应包裹并解包 data', async () => {
    const payload = { status: 'ok', checks: { app: 'ok', db: 'ok' } };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, data: payload, error: null }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiGet('/health')).resolves.toEqual(payload);
  });

  it('POST 序列化请求体但不泄露 API 层外的 fetch 细节', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, data: { accepted: true }, error: null }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await apiPost('/commands', { command: 'noop' });

    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
    expect(fetchMock.mock.calls[0][1]?.body).toBe('{"command":"noop"}');
  });

  it('非 2xx 响应转换为 ApiError', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: 'service unavailable' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiGet('/health')).rejects.toBeInstanceOf(ApiError);
  });
});

describe('domain api wrappers', () => {
  it('projectsApi.listProjects 使用 /api/projects 并拆 V2 信封', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        result: '成功',
        data: [{ project_id: 'p-1', name: '示例项目', created_at: '2026-08-07T10:00:00Z' }],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const rows = await projectsApi.listProjects();

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/projects');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('GET');
    expect(rows).toEqual([{ id: 'p-1', name: '示例项目', created_at: '2026-08-07T10:00:00Z' }]);
  });

  it('intakeApi.submit/getResult 使用项目级路径并序列化正文', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          result: '成功',
          data: { context_ref: 'ctx-1', agent_run_ref: 'run-1' },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          result: '成功',
          data: { context_ref: 'ctx-1', intake_conclusion: 'accepted' },
        }),
      });
    vi.stubGlobal('fetch', fetchMock);

    const outcome = await intakeApi.submit('project-1', {
      text: '来源材料',
      source_note: '会议纪要',
      operator_ref: 'Yun',
      idempotency_key: 'key-1',
    });
    await intakeApi.getResult('project-1', 'ctx-1');

    expect(outcome).toEqual({ status: 'submitted', context_ref: 'ctx-1', agent_run_ref: 'run-1' });
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/projects/project-1/intake');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
    expect(fetchMock.mock.calls[0][1]?.body).toBe(
      '{"text":"来源材料","source_note":"会议纪要","operator_ref":"Yun","idempotency_key":"key-1"}',
    );
    expect(String(fetchMock.mock.calls[1][0])).toBe('/api/projects/project-1/intake/ctx-1');
    expect(fetchMock.mock.calls[1][1]?.method).toBe('GET');
  });

  it('agentRunApi.get/subscribe 使用 AgentRun 路径和事件流', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        data: { id: 'run-1', kind: 'source_intake', status: 'succeeded', events: [] },
        error: null,
      }),
    });
    const closeMock = vi.fn();
    const eventSourceMock = vi.fn();
    class EventSourceMock {
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(url: string) {
        eventSourceMock(url);
      }

      addEventListener() {}

      close() {
        closeMock();
      }
    }
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('EventSource', EventSourceMock);

    await agentRunApi.get('run-1');
    const subscription = agentRunApi.subscribe('run-1', vi.fn());
    subscription.close();

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/agent-runs/run-1');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('GET');
    expect(eventSourceMock).toHaveBeenCalledWith('/api/agent-runs/run-1/events');
    expect(closeMock).toHaveBeenCalledTimes(1);
  });
});
