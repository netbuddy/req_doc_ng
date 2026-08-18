// 设置页「逐项探测能力」面板的交互纯测（T20260724-capability-probe-panel 冷审查消费）。
// 这里钉的是两条只有在界面上才成立的事实：探测被放弃后按钮要能继续用；一轮没探出结论的探测
// 不许被「应用」。两者都会把用户拖进死胡同：前者要重开页面（未保存的编辑全丢），后者会把上次
// 探明的能力档案覆盖成一份空结论。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SettingsWorkbench } from '../src/workbenches/SettingsWorkbench';
import type { ModelCapabilityProbeResult } from '../src/api/settings';

function okJson(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => ({ success: true, data, error: null }),
  } as unknown as Response;
}

function providerListStub() {
  return {
    active_provider_id: 'p1',
    providers: [{
      id: 'p1',
      name: '本地 llama.cpp',
      provider_type: 'llama_cpp',
      base_url: 'http://127.0.0.1:8084/v1',
      model: 'qwen3',
      timeout_seconds: 180,
      max_retries: 3,
      concurrency_limit: 5,
      api_key_set: false,
      active: true,
      thinking_enabled: false,
      capability_profile: {},
    }],
    provider_types: [{ key: 'llama_cpp', label: 'llama.cpp', description: '本地 llama.cpp 服务' }],
    source: 'saved',
    updated_at: null,
    updated_by: null,
  };
}

/** 一轮什么都没探出来的探测结果：第一项判不可用，其余五项「没探明」（后端 ok=false）。 */
function blankProbeResult(): ModelCapabilityProbeResult {
  const keys = ['reachable', 'generate', 'thinking', 'structured', 'context', 'unknown_fields'] as const;
  return {
    items: keys.map((key, index) => ({
      key,
      state: index === 0 ? 'unsupported' : 'unknown',
      mode: null, available: null, tier: null, tokens: null, source: null,
      note_code: null, outcome: index === 0 ? 'unreachable' : null, latency_ms: null, detail: {},
    })),
    profile: { thinking: { off_state: 'unknown' } },
    probed_at: '2026-07-25T10:00:00+08:00',
    ok: false,
  };
}

/** 探完了并且问出了结论的一轮（后端 ok=true）。 */
function goodProbeResult(): ModelCapabilityProbeResult {
  const result = blankProbeResult();
  return {
    ...result,
    items: result.items.map((item) => ({ ...item, state: 'supported', outcome: null })),
    ok: true,
  };
}

let probeResponder: () => Promise<Response>;

beforeEach(() => {
  probeResponder = async () => okJson(goodProbeResult());
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/config/model-service/probe-capabilities')) {
      return probeResponder();
    }
    if (url.includes('/config/model-service/providers')) {
      return okJson(providerListStub());
    }
    if (url.includes('/config/domains')) {
      return okJson([]);
    }
    if (url.includes('/templates')) {
      return okJson([]);
    }
    return okJson(null);
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function renderPanel() {
  render(<SettingsWorkbench operatorRef="tester" />);
  await screen.findByTestId('provider-probe');
  await waitFor(() => expect(screen.getByTestId('provider-model')).toHaveValue('qwen3'));
}

describe('探测在途被放弃', () => {
  it('改了模型标识就放弃这一轮探测，三个按钮必须立刻恢复可用', async () => {
    // 探测一轮要跑到一分钟，期间顺手改个字段是极自然的动作。转圈标志若不复位，探测按钮永远转圈、
    // 两个连通测试按钮永远置灰，只能切走配置域再切回来重挂面板，代价是当前未保存的编辑全丢。
    let releaseProbe: (() => void) | null = null;
    probeResponder = () => new Promise<Response>((resolve) => {
      releaseProbe = () => resolve(okJson(goodProbeResult()));
    });

    await renderPanel();
    fireEvent.click(screen.getByTestId('provider-probe'));
    await waitFor(() => expect(screen.getByTestId('provider-test-reachability')).toBeDisabled());

    fireEvent.change(screen.getByTestId('provider-model'), { target: { value: 'qwen3-32b' } });

    await waitFor(() => expect(screen.getByTestId('provider-test-reachability')).not.toBeDisabled());
    expect(screen.getByTestId('provider-test-generation')).not.toBeDisabled();
    expect(screen.getByTestId('provider-probe').className).not.toContain('ant-btn-loading');

    // 放走那条被丢弃的请求，确认它回来时不会把结论显示到已经改过的表单上
    await act(async () => {
      releaseProbe?.();
      await Promise.resolve();
    });
    expect(screen.queryByTestId('provider-capability-list')).toBeNull();
  });
});

describe('探测没探出结论时不许应用', () => {
  it('后端说这一轮没结论（ok=false）：应用按钮置灰并说清原因', async () => {
    probeResponder = async () => okJson(blankProbeResult());
    await renderPanel();
    fireEvent.click(screen.getByTestId('provider-probe'));

    await screen.findByTestId('provider-capability-list');
    expect(screen.getByTestId('provider-apply-profile')).toBeDisabled();
    expect(screen.getByText(/这轮没探出结论/)).toBeTruthy();
  });

  it('探出了结论就照常可以应用', async () => {
    await renderPanel();
    fireEvent.click(screen.getByTestId('provider-probe'));

    await screen.findByTestId('provider-capability-list');
    expect(screen.getByTestId('provider-apply-profile')).not.toBeDisabled();
  });
});
